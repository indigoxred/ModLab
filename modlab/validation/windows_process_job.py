"""Own the original suspended process and a private non-breakaway job.

Owners cannot be copied or serialized. Failed launch exceptions expose ``owner``
so callers can keep observing and retry ``close`` once the entire tree exits.
``close`` never terminates processes, including a child still suspended after a
failed termination attempt. Such a child requires explicit controller recovery.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
import threading


@dataclass(frozen=True)
class ProcessTreeObservation:
    pid: int
    creation_time: int
    root_exit_code: int | None
    active_processes: int
    total_processes: int


if os.name == 'nt':
    _kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

    class _BASIC_LIMIT(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit', ctypes.c_longlong), ('PerJobUserTimeLimit', ctypes.c_longlong),
                    ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                    ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                    ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ('ReadOperationCount', 'WriteOperationCount',
                    'OtherOperationCount', 'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]

    class _EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [('BasicLimitInformation', _BASIC_LIMIT), ('IoInfo', _IO_COUNTERS),
                    ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                    ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]

    class _ACCOUNTING(ctypes.Structure):
        _fields_ = [(name, ctypes.c_longlong) for name in ('TotalUserTime', 'TotalKernelTime',
                    'ThisPeriodTotalUserTime', 'ThisPeriodTotalKernelTime')] + [
                    (name, wintypes.DWORD) for name in ('TotalPageFaultCount', 'TotalProcesses',
                    'ActiveProcesses', 'TotalTerminatedProcesses')]

    for name, args, result in (
        ('CreateJobObjectW', [ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        ('AssignProcessToJobObject', [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        ('IsProcessInJob', [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL),
        ('QueryInformationJobObject', [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        ('GetExitCodeProcess', [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        ('GetProcessId', [wintypes.HANDLE], wintypes.DWORD),
        ('WaitForSingleObject', [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
        ('CloseHandle', [wintypes.HANDLE], wintypes.BOOL),
    ):
        function = getattr(_kernel32, name)
        function.argtypes = args
        function.restype = result


def _error(message: str) -> OSError:
    return OSError(ctypes.get_last_error(), message)


def _close_native_handle(handle: int) -> None:
    if not _kernel32.CloseHandle(handle):
        raise _error('CloseHandle failed; ownership retained')


def _query(job, info_class, structure):
    result = structure()
    returned = wintypes.DWORD()
    if not _kernel32.QueryInformationJobObject(job, info_class, ctypes.byref(result), ctypes.sizeof(result), ctypes.byref(returned)):
        raise _error('QueryInformationJobObject failed')
    if returned.value != ctypes.sizeof(result):
        raise OSError('job information has unexpected size')
    return result


def _assign_and_verify(job: int, process: int) -> None:
    # A newly created unnamed job has zero limits. Explicitly verify both
    # BREAKAWAY_OK (0x800), SILENT_BREAKAWAY_OK (0x1000), and KILL_ON_JOB_CLOSE
    # (0x2000) are absent, before and after assignment.
    for assign in (True, False):
        limits = _query(job, 9, _EXTENDED_LIMIT)
        if limits.BasicLimitInformation.LimitFlags != 0:
            raise OSError('private job has unexpected limits or breakaway policy')
        if assign and not _kernel32.AssignProcessToJobObject(job, process):
            raise _error('AssignProcessToJobObject failed')
    in_job = wintypes.BOOL()
    if not _kernel32.IsProcessInJob(process, job, ctypes.byref(in_job)) or not in_job.value:
        raise OSError('private job membership verification failed')
    accounting = _query(job, 1, _ACCOUNTING)
    if accounting.ActiveProcesses != 1 or accounting.TotalProcesses != 1:
        raise OSError('suspended job accounting verification failed')


class ProcessJobOwner:
    """Controller-local ownership; constructed only by the suspended launcher."""

    def __init__(self):
        raise TypeError('process ownership is transferred only by the suspended launcher')

    @classmethod
    def _take_created(cls, information):
        # Destructively consume PROCESS_INFORMATION so it cannot be transferred
        # twice and launcher cleanup cannot close handles now owned here.
        if not information.hProcess or not information.hThread:
            raise RuntimeError('process handles already transferred or invalid')
        owner = object.__new__(cls)
        owner._controller = os.getpid()
        owner._lock = threading.RLock()
        owner._process = information.hProcess
        owner._thread = information.hThread
        owner._token = None
        owner._pid = int(information.dwProcessId)
        owner._creation_time = 0
        owner._job = None
        owner._assigned = False
        owner._resumed = False
        owner._closed = False
        owner._quiescent = False
        information.hProcess = None
        information.hThread = None
        return owner

    def _check(self) -> None:
        if os.getpid() != self._controller:
            raise RuntimeError('process owner belongs to another controller')
        if self._closed:
            raise RuntimeError('process owner is closed')

    @property
    def process_handle(self) -> int:
        with self._lock:
            self._check()
            return self._process

    @property
    def resumed(self) -> bool:
        with self._lock:
            self._check()
            return self._resumed

    def _admit(self, creation_time: int) -> None:
        with self._lock:
            self._check()
            if self._job or self._creation_time:
                raise RuntimeError('process ownership already admitted')
            if _kernel32.GetProcessId(self._process) != self._pid:
                raise RuntimeError('original process handle PID does not match owner')
            self._creation_time = creation_time
            self._job = _kernel32.CreateJobObjectW(None, None)
            if not self._job:
                raise _error('CreateJobObjectW failed')
            _assign_and_verify(self._job, self._process)
            self._assigned = True

    def _mark_resumed(self) -> None:
        with self._lock:
            self._check()
            if self._resumed:
                raise RuntimeError('process owner already resumed')
            if not self._assigned:
                raise RuntimeError('process owner not admitted')
            self._resumed = True

    def _close_token(self) -> None:
        with self._lock:
            self._check()
            if self._token:
                _close_native_handle(self._token)
                self._token = None

    def observe(self) -> ProcessTreeObservation:
        with self._lock:
            self._check()
            if not self._job or not self._assigned:
                raise RuntimeError('process job admission incomplete')
            exit_code = self._root_exit()
            accounting = _query(self._job, 1, _ACCOUNTING)
            if accounting.TotalProcesses < 1 or accounting.ActiveProcesses > accounting.TotalProcesses:
                raise OSError('invalid job accounting')
            if accounting.ActiveProcesses == 0 and exit_code is None:
                # An exit may race the initial root wait; query once more.
                exit_code = self._root_exit()
                if exit_code is None:
                    raise OSError('empty job has an unconfirmed root exit')
            return ProcessTreeObservation(self._pid, self._creation_time, exit_code,
                                          accounting.ActiveProcesses, accounting.TotalProcesses)

    def _root_exit(self) -> int | None:
        wait = _kernel32.WaitForSingleObject(self._process, 0)
        if wait == 0x102:
            return None
        if wait != 0:
            raise _error(f'original process wait failed: {wait:#x}')
        code = wintypes.DWORD()
        if not _kernel32.GetExitCodeProcess(self._process, ctypes.byref(code)):
            raise _error('GetExitCodeProcess failed')
        return int(code.value)

    def close(self) -> None:
        with self._lock:
            if os.getpid() != self._controller:
                raise RuntimeError('process owner belongs to another controller')
            if self._closed:
                return
            if not self._quiescent:
                # Even an admission failure may have assigned the job before
                # verification failed. Query it whenever a job handle exists.
                root_exit = self._root_exit()
                active = _query(self._job, 1, _ACCOUNTING).ActiveProcesses if self._job else 0
                if root_exit is None or active:
                    raise RuntimeError('process tree is still active; ownership retained')
                self._quiescent = True
            for attribute in ('_token', '_thread', '_job', '_process'):
                handle = getattr(self, attribute)
                if handle:
                    _close_native_handle(handle)
                    setattr(self, attribute, None)
            self._closed = True

    def __copy__(self):
        raise TypeError('process ownership cannot be copied')

    def __deepcopy__(self, memo):
        raise TypeError('process ownership cannot be copied or serialized')

    def __reduce__(self):
        raise TypeError('process ownership cannot be serialized')
