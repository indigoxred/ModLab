"""Loss-detecting recursive Windows directory mutation monitor."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, replace
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import time
from typing import Callable, TYPE_CHECKING

from modlab.platform.windows_exact_fs import (
    ExactObjectError,
    ExactObjectOwnershipError,
    PinnedObject,
    PinnedIdentity,
    read_pinned_file,
    publish_new_pinned,
    resolve_retained_ownership,
    union_retained_ownership,
)
from modlab.validation.windows_vault_security import EvidenceVault, open_vault, pin_trusted_paths
from modlab.validation.windows_process_job import ProcessJobOwner
from modlab.validation.mo2_containment_model import (
    ContainmentScenario,
    ProtectedState,
    TreeIdentity,
    WatchEvidenceCompletion,
    WatchOutcome,
    WatcherEvent,
)
from modlab.validation.mo2_containment_serialization import (
    ContainmentFormatError,
    watch_outcome_from_bytes,
    watch_outcome_id_for,
    watch_outcome_to_bytes,
)
from modlab.validation.windows_watch_protocol import (
    CAUSAL_NAMES,
    causal_record,
    causal_record_from_bytes,
    causal_record_to_bytes,
    CLAIM_NAME as _CLAIM_NAME,
    CONTROLLER_LOSS_NAME as _CONTROLLER_LOSS_NAME,
    EVENTS_NAME as _EVENTS_NAME,
    LAUNCH_NAME as _LAUNCH_NAME,
    OUTCOME_NAME as _OUTCOME_NAME,
    READY_NAME as _READY_NAME,
    REQUEST_NAME as _REQUEST_NAME,
    ROOT_KINDS,
    STOP_NAME as _STOP_NAME,
    TERMINAL_NAME as _TERMINAL_NAME,
    ControllerClaim,
    ControllerLoss,
    WorkerLaunch,
    WatchProtocolError,
    WatchProtocolOwnershipError,
    WatchReceipt,
    WatchRequest,
    WatchRoot,
    controller_claim_from_bytes,
    controller_claim_to_bytes,
    controller_loss_from_bytes,
    controller_loss_to_bytes,
    publish_new_verified,
    worker_launch_from_bytes,
    worker_launch_to_bytes,
    watch_request_from_bytes,
    watch_request_sha256,
    watch_request_to_bytes,
    watch_worker_command,
)


if TYPE_CHECKING:
    from modlab.validation.windows_integrity import ProcessLaunch


_SCHEMA_VERSION = 2
_ROOT_KIND_SET = frozenset(ROOT_KINDS)
_ACTIONS = {
    1: "Added",
    2: "Removed",
    3: "Modified",
    4: "RenamedOld",
    5: "RenamedNew",
}
_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)

_FILE_LIST_DIRECTORY = 0x0001
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_DELETE = 0x00000004
_OPEN_EXISTING = 3
_CREATE_NEW = 1
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_FLAG_OVERLAPPED = 0x40000000
_FILE_NOTIFY_CHANGE_FILE_NAME = 0x00000001
_FILE_NOTIFY_CHANGE_DIR_NAME = 0x00000002
_FILE_NOTIFY_CHANGE_ATTRIBUTES = 0x00000004
_FILE_NOTIFY_CHANGE_SIZE = 0x00000008
_FILE_NOTIFY_CHANGE_LAST_WRITE = 0x00000010
_FILE_NOTIFY_CHANGE_CREATION = 0x00000040
_FILE_NOTIFY_CHANGE_SECURITY = 0x00000100
_NOTIFY_FILTER = (
    _FILE_NOTIFY_CHANGE_FILE_NAME
    | _FILE_NOTIFY_CHANGE_DIR_NAME
    | _FILE_NOTIFY_CHANGE_ATTRIBUTES
    | _FILE_NOTIFY_CHANGE_SIZE
    | _FILE_NOTIFY_CHANGE_LAST_WRITE
    | _FILE_NOTIFY_CHANGE_CREATION
    | _FILE_NOTIFY_CHANGE_SECURITY
)
_ERROR_INVALID_PARAMETER = 87
_ERROR_SHARING_VIOLATION = 32
_ERROR_LOCK_VIOLATION = 33
_ERROR_FILE_EXISTS = 80
_ERROR_ALREADY_EXISTS = 183
_ERROR_IO_PENDING = 997
_ERROR_OPERATION_ABORTED = 995
_ERROR_NOT_FOUND = 1168
_ERROR_NOTIFY_ENUM_DIR = 1022
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258
_WAIT_FAILED = 0xFFFFFFFF
_INFINITE = 0xFFFFFFFF
_FILE_BEGIN = 0
_SYNCHRONIZE = 0x00100000
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_BUFFER_SIZE = 64 * 1024
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _HandleOwnershipError(WatchProtocolError):
    """A native handle could not be closed and remains owned by the caller."""

    def __init__(self, message: str, handle: int, label: str = "retained native handle") -> None:
        super().__init__(message)
        self.handle = handle
        self.label = label


def _close_or_retain_raw(handle: int, label: str) -> None:
    try:
        close_error = _close_handle(handle, label)
    except BaseException as error:
        raise _HandleOwnershipError(str(error), handle, label) from error
    if close_error is not None:
        raise _HandleOwnershipError(close_error, handle, label)


def _protocol_owner(
    ownership: ExactObjectOwnershipError, cause: BaseException,
) -> WatchProtocolOwnershipError | ExactObjectOwnershipError:
    try:
        error = WatchProtocolOwnershipError(str(cause), ownership)
    except BaseException as wrapping_error:
        ownership.__cause__ = wrapping_error
        return ownership
    error.__cause__ = cause
    return error


def _controller_owner(error: BaseException, path: Path) -> BaseException:
    """Adapt only a controller's raw owner; never add deletion authority."""
    if not isinstance(error, _HandleOwnershipError):
        return error
    retained = PinnedObject(path, error.handle, None)
    ownership = ExactObjectOwnershipError(str(error), verification=(retained,))
    error.handle = 0
    return _protocol_owner(ownership, error)


def _merge_controller_errors(primary: BaseException, cleanup: BaseException) -> BaseException:
    first = primary.ownership if isinstance(primary, WatchProtocolOwnershipError) else primary
    second = cleanup.ownership if isinstance(cleanup, WatchProtocolOwnershipError) else cleanup
    if isinstance(first, ExactObjectOwnershipError):
        if isinstance(second, ExactObjectOwnershipError):
            combined = union_retained_ownership(str(cleanup), prior=first, owners=second.owners)
            return _protocol_owner(combined, primary)
        primary.add_note(f"additional controller cleanup failure: {cleanup!r}")
        return primary
    if cleanup is not primary:
        cleanup.__cause__ = primary
    return cleanup


def _close_controller_handle(handle: int, label: str, path: Path) -> str | None:
    try:
        _close_or_retain_raw(handle, label)
    except _HandleOwnershipError as error:
        owner = _controller_owner(error, path)
        owner.resolve()
        return str(error)
    return None


class _PopenVerificationOwner(PinnedObject):
    """Close-only adapter for Popen's one handle, including interrupted Detach."""

    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self.process = process
        self.handle_object = process._handle
        self.detached = False
        super().__init__(Path(f"Popen process {process.pid}"), int(self.handle_object), None)

    def close(self) -> None:
        if not self.handle:
            return
        if not self.detached:
            try:
                self.handle_object.Detach()
            finally:
                # Detach can change this flag before an interruption. It is
                # transfer evidence only, never proof of native close success.
                self.detached = self.handle_object.closed
            if not self.detached:
                raise WatchProtocolError("Popen handle detachment did not complete")
        super().close()
        self.process._child_created = False


@dataclass
class _LocalWatchSession:
    request: WatchRequest
    request_sha256: str
    claim: ControllerClaim
    launch: WorkerLaunch
    controller_pid: int
    controller_creation_time: int
    worker_pid: int
    worker_creation_time: int
    process: subprocess.Popen[bytes]
    lock: threading.Lock
    poison_reasons: list[str]
    finalized_outcome: WatchOutcome | None = None
    vault: EvidenceVault | None = None
    runtime_guard: object | None = None
    process_owner: ProcessJobOwner | None = None
    admission: dict[str, object] | None = None
    quiescence: dict[str, object] | None = None
    record_pins: list[PinnedObject] | None = None
    observed_worker_exit_code: int | None = None


_LOCAL_SESSIONS: dict[Path, _LocalWatchSession] = {}
_LOCAL_SESSIONS_LOCK = threading.Lock()


def _make_incomplete_receipt(
    request: WatchRequest | object,
    worker_pid: int,
    *,
    ready: bool,
    opened_root_kinds: tuple[str, ...],
    events: tuple[WatcherEvent, ...],
    event_bytes_sha256: str,
    error: str | None,
    request_bytes_sha256: str = "",
) -> WatchReceipt:
    if isinstance(request, WatchRequest):
        request_id = request.request_id
        session_id = request.session_id
        run_id = request.run_id
        scenario = request.scenario
    else:
        request_id = "watch-request:" + "0" * 64
        session_id = "watch-session:" + "0" * 64
        run_id = "containment-run:" + "0" * 32
        scenario = ContainmentScenario.MERGE_EXISTING
    return WatchReceipt(
        request_id=request_id,
        session_id=session_id,
        run_id=run_id,
        scenario=scenario,
        worker_pid=worker_pid,
        evidence_completion=WatchEvidenceCompletion.INCOMPLETE,
        ready=ready,
        opened_root_kinds=opened_root_kinds,
        events=events,
        event_bytes_sha256=event_bytes_sha256,
        worker_exit_code=None,
        watch_outcome_id=None,
        request_bytes_sha256=request_bytes_sha256,
        error=error,
    )


if os.name == "nt":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _OVERLAPPED(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetFileAttributesW.restype = wintypes.DWORD
    _kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION),
    ]
    _kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    _kernel32.GetFileSizeEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_longlong)]
    _kernel32.GetFileSizeEx.restype = wintypes.BOOL
    _kernel32.SetFilePointerEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_longlong,
        ctypes.POINTER(ctypes.c_longlong),
        wintypes.DWORD,
    ]
    _kernel32.SetFilePointerEx.restype = wintypes.BOOL
    _kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    _kernel32.ReadFile.restype = wintypes.BOOL
    _kernel32.WriteFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    _kernel32.WriteFile.restype = wintypes.BOOL
    _kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    _kernel32.FlushFileBuffers.restype = wintypes.BOOL
    _kernel32.CreateEventW.argtypes = [
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    _kernel32.CreateEventW.restype = wintypes.HANDLE
    _kernel32.ResetEvent.argtypes = [wintypes.HANDLE]
    _kernel32.ResetEvent.restype = wintypes.BOOL
    _kernel32.ReadDirectoryChangesW.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(_OVERLAPPED),
        ctypes.c_void_p,
    ]
    _kernel32.ReadDirectoryChangesW.restype = wintypes.BOOL
    _kernel32.GetOverlappedResult.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_OVERLAPPED),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.BOOL,
    ]
    _kernel32.GetOverlappedResult.restype = wintypes.BOOL
    _kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(_OVERLAPPED)]
    _kernel32.CancelIoEx.restype = wintypes.BOOL
    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.GetProcessId.argtypes = [wintypes.HANDLE]
    _kernel32.GetProcessId.restype = wintypes.DWORD
    _kernel32.GetCurrentProcess.argtypes = []
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    _kernel32.GetProcessTimes.restype = wintypes.BOOL
    _kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL


@dataclass
class _WatchState:
    root: WatchRoot
    logical_root_kinds: tuple[str, ...]
    directory_path: Path
    label: str
    directory_handle: int
    expected_volume_serial: int
    expected_file_id: int
    event_handle: int
    recursive: bool
    notify_filter: int
    membership_name: str | None
    journal: "_EventJournal"
    stopping: threading.Event
    errors: list[str]
    errors_lock: threading.Lock
    buffer: ctypes.Array[ctypes.c_char]
    overlapped: object
    armed: threading.Event
    pending_lock: threading.Lock
    pending: bool = False
    thread: threading.Thread | None = None
    started: bool = False

    def fail(self, message: str) -> None:
        with self.errors_lock:
            self.errors.append(message)
        self.stopping.set()

    def consume_completion_after_stop(self) -> None:
        _consume_watch_completion(self, after_stop=True)


@dataclass(frozen=True)
class _JournalEvidence:
    volume_serial: int
    file_id: int
    data: bytes
    event_count: int
    final_sequence: int

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


class _EventJournal:
    def __init__(self, path: Path) -> None:
        handle = _kernel32.CreateFileW(
            str(path),
            _GENERIC_READ | _GENERIC_WRITE,
            _FILE_SHARE_READ,
            None,
            _CREATE_NEW,
            _FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        if handle == _INVALID_HANDLE_VALUE:
            raise _winerror(f"could not create exact event journal {path}")
        self._handle = handle
        try:
            volume_serial, file_id, attributes = _handle_identity(handle, path)
            if attributes & (_FILE_ATTRIBUTE_DIRECTORY | _FILE_ATTRIBUTE_REPARSE_POINT):
                raise WatchProtocolError("event journal must be a direct regular file")
            self._volume_serial = volume_serial
            self._file_id = file_id
        except BaseException as error:
            close_error = _close_handle(handle, f"event journal {path}")
            if close_error is not None:
                raise _HandleOwnershipError(
                    f"{error}; {close_error}",
                    handle,
                    f"event journal {path}",
                ) from error
            self._handle = 0
            raise
        self._lock = threading.Lock()
        self._sequence = 0
        self._event_count = 0

    def append(
        self,
        root_kinds: tuple[str, ...],
        records: tuple[tuple[str, str], ...],
    ) -> None:
        with self._lock:
            for action, relative_path in records:
                for root_kind in root_kinds:
                    self._sequence += 1
                    data = _canonical_bytes(
                        {
                            "action": action,
                            "relativePath": relative_path,
                            "rootKind": root_kind,
                            "sequence": self._sequence,
                        }
                    )
                    written = wintypes.DWORD()
                    buffer = ctypes.create_string_buffer(data)
                    if not _kernel32.WriteFile(
                        self._handle,
                        buffer,
                        len(data),
                        ctypes.byref(written),
                        None,
                    ):
                        raise _winerror("event journal append failed")
                    if written.value != len(data):
                        raise OSError("atomic event append was incomplete")
                    self._event_count += 1
            if not _kernel32.FlushFileBuffers(self._handle):
                raise _winerror("event journal flush failed")

    def evidence(self) -> _JournalEvidence:
        with self._lock:
            if not _kernel32.FlushFileBuffers(self._handle):
                raise _winerror("event journal final flush failed")
            size = ctypes.c_longlong()
            if not _kernel32.GetFileSizeEx(self._handle, ctypes.byref(size)):
                raise _winerror("event journal size inspection failed")
            if size.value < 0:
                raise WatchProtocolError("event journal size is negative")
            if not _kernel32.SetFilePointerEx(
                self._handle,
                ctypes.c_longlong(0),
                None,
                _FILE_BEGIN,
            ):
                raise _winerror("event journal rewind failed")
            chunks: list[bytes] = []
            remaining = size.value
            while remaining:
                amount = min(remaining, 64 * 1024)
                buffer = ctypes.create_string_buffer(amount)
                transferred = wintypes.DWORD()
                if not _kernel32.ReadFile(
                    self._handle,
                    buffer,
                    amount,
                    ctypes.byref(transferred),
                    None,
                ):
                    raise _winerror("event journal exact-handle read failed")
                if transferred.value == 0:
                    raise WatchProtocolError("event journal exact-handle read was short")
                chunks.append(bytes(buffer[: transferred.value]))
                remaining -= transferred.value
            data = b"".join(chunks)
            return _JournalEvidence(
                self._volume_serial,
                self._file_id,
                data,
                self._event_count,
                self._sequence,
            )

    def close(self) -> str | None:
        if not self._handle:
            return None
        handle = self._handle
        close_error = _close_handle(handle, "event journal")
        if close_error is None:
            self._handle = 0
        return close_error


def _read_exact_journal(path: Path) -> _JournalEvidence:
    handle = _kernel32.CreateFileW(
        str(path),
        _GENERIC_READ,
        _FILE_SHARE_READ,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        raise _winerror(f"could not open exact event journal {path}")
    try:
        volume_serial, file_id, attributes = _handle_identity(handle, path)
        if attributes & (_FILE_ATTRIBUTE_DIRECTORY | _FILE_ATTRIBUTE_REPARSE_POINT):
            raise WatchProtocolError("event journal must be a direct regular file")
        size = ctypes.c_longlong()
        if not _kernel32.GetFileSizeEx(handle, ctypes.byref(size)):
            raise _winerror("event journal size inspection failed")
        data_parts: list[bytes] = []
        remaining = size.value
        while remaining:
            amount = min(remaining, 64 * 1024)
            buffer = ctypes.create_string_buffer(amount)
            transferred = wintypes.DWORD()
            if not _kernel32.ReadFile(
                handle,
                buffer,
                amount,
                ctypes.byref(transferred),
                None,
            ):
                raise _winerror("event journal exact-handle read failed")
            if transferred.value == 0:
                raise WatchProtocolError("event journal exact-handle read was short")
            data_parts.append(bytes(buffer[: transferred.value]))
            remaining -= transferred.value
        data = b"".join(data_parts)
        return _JournalEvidence(volume_serial, file_id, data, 0, 0)
    finally:
        _close_or_retain_raw(handle, "event journal readback")


def _read_exact_regular_file(
    path: Path,
    label: str,
    *,
    close_failure_is_fatal: bool = True,
) -> bytes:
    handle = _kernel32.CreateFileW(
        str(path),
        _GENERIC_READ,
        _FILE_SHARE_READ,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        raise _winerror(f"could not open exact {label} {path}")
    try:
        _, _, attributes = _handle_identity(handle, path)
        if attributes & (_FILE_ATTRIBUTE_DIRECTORY | _FILE_ATTRIBUTE_REPARSE_POINT):
            raise WatchProtocolError(f"{label} must be a direct regular file")
        size = ctypes.c_longlong()
        if not _kernel32.GetFileSizeEx(handle, ctypes.byref(size)):
            raise _winerror(f"{label} size inspection failed")
        if size.value < 0:
            raise WatchProtocolError(f"{label} size is negative")
        data_parts: list[bytes] = []
        remaining = size.value
        while remaining:
            amount = min(remaining, 64 * 1024)
            buffer = ctypes.create_string_buffer(amount)
            transferred = wintypes.DWORD()
            if not _kernel32.ReadFile(
                handle,
                buffer,
                amount,
                ctypes.byref(transferred),
                None,
            ):
                raise _winerror(f"{label} exact-handle read failed")
            if transferred.value == 0:
                raise WatchProtocolError(f"{label} exact-handle read was short")
            data_parts.append(bytes(buffer[: transferred.value]))
            remaining -= transferred.value
        return b"".join(data_parts)
    finally:
        try:
            _close_or_retain_raw(handle, f"{label} readback")
        except _HandleOwnershipError as error:
            if close_failure_is_fatal:
                raise
            _controller_owner(error, path).resolve()


def _require_windows() -> None:
    if os.name != "nt":
        raise OSError("ReadDirectoryChangesW requires Windows")


def _winerror(message: str, code: int | None = None) -> OSError:
    if code is None:
        code = ctypes.get_last_error()
    return OSError(code, f"{message}: {ctypes.FormatError(code)}")


def _handle_identity(handle: int, path: Path) -> tuple[int, int, int]:
    information = _BY_HANDLE_FILE_INFORMATION()
    if not _kernel32.GetFileInformationByHandle(handle, ctypes.byref(information)):
        raise _winerror(f"could not inspect watched root {path}")
    file_id = (int(information.nFileIndexHigh) << 32) | int(information.nFileIndexLow)
    return int(information.dwVolumeSerialNumber), file_id, int(information.dwFileAttributes)


def _open_directory(path: Path, *, overlapped: bool) -> int:
    flags = _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT
    if overlapped:
        flags |= _FILE_FLAG_OVERLAPPED
    handle = _kernel32.CreateFileW(
        str(path),
        _FILE_LIST_DIRECTORY,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        None,
        _OPEN_EXISTING,
        flags,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        raise _winerror(f"could not open watched root {path}")
    return handle


def _close_handle(handle: int, label: str) -> str | None:
    if not handle or handle == _INVALID_HANDLE_VALUE:
        return None
    if _kernel32.CloseHandle(handle):
        return None
    code = ctypes.get_last_error()
    return f"unclosed handle: {label} (WinError {code})"


def _reject_reparse_components(path: Path, label: str) -> Path:
    supplied = Path(path)
    absolute = supplied if supplied.is_absolute() else supplied.absolute()
    if any(part in {".", ".."} for part in absolute.parts):
        raise WatchProtocolError(f"{label} must not contain dot traversal")
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        attributes = _kernel32.GetFileAttributesW(str(current))
        if attributes == 0xFFFFFFFF:
            raise _winerror(f"could not inspect {label} component {current}")
        if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise WatchProtocolError(f"{label} contains a reparse component: {current}")
    return absolute


def watch_root(root_kind: str, path: Path) -> WatchRoot:
    """Capture the stable native identity for one direct watched directory."""
    _require_windows()
    if root_kind not in _ROOT_KIND_SET:
        raise WatchProtocolError(f"invalid root kind: {root_kind!r}")
    supplied = _reject_reparse_components(Path(path), "watched root")
    attributes = _kernel32.GetFileAttributesW(str(supplied))
    if attributes == 0xFFFFFFFF:
        raise _winerror(f"could not inspect watched root {supplied}")
    if not attributes & _FILE_ATTRIBUTE_DIRECTORY:
        raise WatchProtocolError(f"watched root is not a directory: {supplied}")
    if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        raise WatchProtocolError(f"watched root must be direct, not reparse: {supplied}")
    handle = _open_directory(supplied, overlapped=False)
    try:
        volume_serial, file_id, handle_attributes = _handle_identity(handle, supplied)
        if not handle_attributes & _FILE_ATTRIBUTE_DIRECTORY:
            raise WatchProtocolError(f"watched root is not a directory: {supplied}")
        if handle_attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise WatchProtocolError(f"watched root must be direct, not reparse: {supplied}")
    finally:
        close_error = _close_handle(handle, f"identity root {supplied}")
    if close_error is not None:
        raise _HandleOwnershipError(close_error, handle, f"identity root {supplied}")
    return WatchRoot(root_kind, supplied, volume_serial, file_id)


def _normalize_request(request: WatchRequest, *, inspect_roots: bool) -> WatchRequest:
    request = watch_request_from_bytes(watch_request_to_bytes(request))
    supplied_evidence_root = _reject_reparse_components(
        Path(request.evidence_root),
        "evidence root",
    )
    evidence_root = supplied_evidence_root
    if Path(request.evidence_root) != evidence_root:
        raise WatchProtocolError("evidence root must be canonical and absolute")
    if not evidence_root.is_dir():
        raise WatchProtocolError("evidence root must be a directory")
    if _kernel32.GetFileAttributesW(str(evidence_root)) & _FILE_ATTRIBUTE_REPARSE_POINT:
        raise WatchProtocolError("evidence root must be direct, not reparse")
    stop_token = Path(request.stop_token_path)
    expected_stop = evidence_root / _STOP_NAME
    if stop_token.absolute() != expected_stop.absolute():
        raise WatchProtocolError("stop token must be the confined evidence stop.token")
    if type(request.roots) is not tuple or not request.roots:
        raise WatchProtocolError("roots must be a nonempty tuple")
    normalized: list[WatchRoot] = []
    root_kinds: set[str] = set()
    for root in request.roots:
        if not isinstance(root, WatchRoot):
            raise WatchProtocolError("roots must contain WatchRoot values")
        if type(root.root_kind) is not str or root.root_kind not in _ROOT_KIND_SET:
            raise WatchProtocolError(f"invalid root kind: {root.root_kind!r}")
        if not isinstance(root.path, Path):
            raise WatchProtocolError("watched root paths must be Path values")
        if type(root.volume_serial) is not int or not 0 <= root.volume_serial <= 0xFFFFFFFF:
            raise WatchProtocolError("volume serial must be an unsigned 32-bit integer")
        if type(root.file_id) is not int or not 0 <= root.file_id <= 0xFFFFFFFFFFFFFFFF:
            raise WatchProtocolError("file ID must be an unsigned 64-bit integer")
        path = Path(root.path)
        if inspect_roots:
            observed = watch_root(root.root_kind, path)
            if (root.volume_serial, root.file_id) != (observed.volume_serial, observed.file_id):
                raise WatchProtocolError(f"root identity changed before start: {root.root_kind}")
            path = observed.path
        else:
            try:
                direct_path = _reject_reparse_components(path, "watched root")
            except OSError as error:
                raise WatchProtocolError(f"watched root path is unavailable: {path}") from error
            if path != direct_path:
                raise WatchProtocolError("watched root path must be canonical and absolute")
        if root.root_kind in root_kinds:
            raise WatchProtocolError(f"duplicate root kind: {root.root_kind}")
        root_kinds.add(root.root_kind)
        normalized.append(WatchRoot(root.root_kind, path, root.volume_serial, root.file_id))
    if tuple(root.root_kind for root in normalized) != ROOT_KINDS:
        raise WatchProtocolError("request must contain all eight logical root kinds in canonical order")
    return WatchRequest(
        request.request_id,
        request.session_id,
        request.run_id,
        request.scenario,
        evidence_root,
        expected_stop,
        tuple(normalized),
        request.authority_root, request.authority_volume_serial, request.authority_file_id, request.authority_creator_sid,
    )


def _request_document(request: WatchRequest) -> dict[str, object]:
    return json.loads(watch_request_to_bytes(request))


def _request_bytes_and_sha256(request: WatchRequest) -> tuple[bytes, str]:
    request_bytes = watch_request_to_bytes(request)
    return request_bytes, watch_request_sha256(request)


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise WatchProtocolError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _object_from_bytes(data: bytes, label: str) -> dict[str, object]:
    try:
        text = data.decode("utf-8")
        value = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, WatchProtocolError) as error:
        raise WatchProtocolError(f"malformed {label}: {error}") from error
    if type(value) is not dict:
        raise WatchProtocolError(f"{label} must be a JSON object")
    if _canonical_bytes(value) != data:
        raise WatchProtocolError(f"{label} must use canonical JSON")
    return value


def _exact_fields(value: dict[str, object], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise WatchProtocolError(f"{label} fields must be exact")


def _required_text(value: object, label: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise WatchProtocolError(f"{label} must be nonempty canonical text")
    return value


def _required_int(value: object, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise WatchProtocolError(f"{label} must be an integer >= {minimum}")
    return value


def _request_from_bytes(data: bytes) -> WatchRequest:
    return watch_request_from_bytes(data)


def _write_new(path: Path, data: bytes) -> None:
    try:
        publish_new_pinned(path, data, lambda candidate: candidate)
    except ExactObjectOwnershipError as error:
        try:
            resolve_retained_ownership(error)
        except ExactObjectOwnershipError as unresolved:
            raise WatchProtocolOwnershipError(
                f"exact immutable write retained unresolved ownership for {path.name}: {unresolved}",
                unresolved,
            ) from unresolved
        raise WatchProtocolError(
            f"exact immutable write cleanup completed after failure for {path.name}"
        ) from error
    except ExactObjectError as error:
        raise WatchProtocolError(
            f"exact immutable write failed for {path.name}: {error}"
        ) from error


def _resolve_watch_protocol_ownership(
    error: WatchProtocolOwnershipError,
) -> None:
    """Resolve every retained exact object or preserve the structured error."""
    error.resolve()
    if error.ownership.retained_objects:
        raise error


def _filetime_integer(value: wintypes.FILETIME) -> int:
    return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)


def _process_handle_creation_time(handle: int, pid: int) -> int:
    if int(_kernel32.GetProcessId(handle)) != pid:
        raise WatchProtocolError("opened worker process PID does not match")
    creation = wintypes.FILETIME()
    exit_time = wintypes.FILETIME()
    kernel_time = wintypes.FILETIME()
    user_time = wintypes.FILETIME()
    if not _kernel32.GetProcessTimes(
        handle,
        ctypes.byref(creation),
        ctypes.byref(exit_time),
        ctypes.byref(kernel_time),
        ctypes.byref(user_time),
    ):
        raise _winerror(f"could not inspect watch worker process {pid}")
    return _filetime_integer(creation)


def _current_controller_identity() -> tuple[int, int]:
    """Return this controller's identity through the non-owned process pseudo-handle."""
    pid = os.getpid()
    return pid, _process_handle_creation_time(_kernel32.GetCurrentProcess(), pid)


def _open_process_identity(pid: int) -> tuple[int, int]:
    if type(pid) is not int or pid <= 0:
        raise WatchProtocolError("worker PID must be a positive integer")
    handle = _kernel32.OpenProcess(
        _SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        pid,
    )
    if not handle:
        raise _winerror(f"could not open watch worker process {pid}")
    try:
        return handle, _process_handle_creation_time(handle, pid)
    except BaseException:
        _close_or_retain_raw(handle, f"worker process {pid}")
        raise
















def _load_request_path(request_path: Path) -> WatchRequest:
    supplied = _reject_reparse_components(Path(request_path), "request path")
    resolved = supplied.resolve(strict=True)
    request = _request_from_bytes(_read_exact_regular_file(resolved, "request record"))
    if resolved != request.evidence_root / _REQUEST_NAME:
        raise WatchProtocolError("request path is not confined to its evidence root")
    return request


def _ready_document(
    request: WatchRequest,
    worker_pid: int,
    request_sha256: str,
    worker_creation_time: int,
) -> dict[str, object]:
    return {
        "openedRootKinds": [root.root_kind for root in request.roots],
        "requestBytesSha256": request_sha256,
        "requestId": request.request_id,
        "schemaVersion": _SCHEMA_VERSION,
        "workerCreationTime": worker_creation_time,
        "workerPid": worker_pid,
    }


def _parse_ready(
    data: bytes,
    request: WatchRequest,
    worker_pid: int,
    request_sha256: str,
    worker_creation_time: int,
) -> tuple[str, ...]:
    value = _object_from_bytes(data, "ready record")
    _exact_fields(
        value,
        {
            "openedRootKinds",
            "requestBytesSha256",
            "requestId",
            "schemaVersion",
            "workerCreationTime",
            "workerPid",
        },
        "ready record",
    )
    if _required_int(value["schemaVersion"], "ready schema version") != _SCHEMA_VERSION:
        raise WatchProtocolError("unsupported ready schema version")
    if (
        value["requestId"] != request.request_id
        or _required_text(value["requestBytesSha256"], "ready request SHA-256")
        != request_sha256
        or _required_int(value["workerPid"], "ready worker PID", minimum=1) != worker_pid
        or _required_int(
            value["workerCreationTime"],
            "ready worker creation time",
            minimum=1,
        )
        != worker_creation_time
    ):
        raise WatchProtocolError("ready record does not match exact request and worker")
    kinds = value["openedRootKinds"]
    if type(kinds) is not list or not all(type(kind) is str for kind in kinds):
        raise WatchProtocolError("ready root kinds must be an array of strings")
    opened = tuple(kinds)
    expected = tuple(root.root_kind for root in request.roots)
    if opened != expected:
        raise WatchProtocolError("ready root kinds do not match the request")
    return opened














def _popen_process_handle(process: subprocess.Popen[bytes]) -> int:
    retained = getattr(process, "_modlab_close_owner", None)
    if isinstance(retained, _PopenVerificationOwner) and retained.handle:
        return retained.handle
    handle = getattr(process, "_handle", None)
    if not isinstance(handle, int) or getattr(handle, "closed", False):
        raise WatchProtocolError("worker Popen process handle is unavailable")
    return int(handle)


def _get_process_exit_code(handle: int) -> int:
    exit_code = wintypes.DWORD()
    if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
        raise _winerror("could not read watch worker exit code")
    return int(exit_code.value)


def _close_popen_process_handle(process: subprocess.Popen[bytes]) -> str | None:
    retained = getattr(process, "_modlab_close_owner", None)
    if isinstance(retained, _PopenVerificationOwner):
        try:
            retained.close()
        except BaseException as error:
            return str(error)
        return None
    handle_object = getattr(process, "_handle", None)
    if not isinstance(handle_object, int) or getattr(handle_object, "closed", False):
        return None
    # Register the adapter before any transfer; a local session continues to
    # own it, while pre-registration startup failures expose this same object.
    process._modlab_close_owner = _PopenVerificationOwner(process)
    return _close_popen_process_handle(process)


def _load_controller_claim(request: WatchRequest) -> ControllerClaim:
    return controller_claim_from_bytes(
        _read_exact_regular_file(request.evidence_root / _CLAIM_NAME, "controller claim"),
        request,
    )


def _load_worker_launch(request: WatchRequest) -> WorkerLaunch:
    return worker_launch_from_bytes(
        _read_exact_regular_file(request.evidence_root / _LAUNCH_NAME, "worker launch"),
        request,
    )


def _cleanup_start_failure(
    error: BaseException,
    request_path: Path,
    process: subprocess.Popen[bytes],
    session: _LocalWatchSession | None = None,
    *,
    reason: str = "watch-startup-failed",
) -> BaseException:
    """Finish owned-worker cleanup before resolving or exposing startup owners."""
    pending = _controller_owner(error, request_path.parent)
    if session is not None:
        try:
            receipt = _stop_local_session(request_path, session, forced_reason=reason)
            if receipt.error:
                pending.add_note(receipt.error)
        except BaseException as cleanup_error:
            pending = _merge_controller_errors(
                pending, _controller_owner(cleanup_error, request_path.parent),
            )
    else:
        try:
            process.terminate()
            process.wait(timeout=15)
        except BaseException as cleanup_error:
            pending = _merge_controller_errors(pending, cleanup_error)
        close_error = _close_popen_process_handle(process)
        if close_error is not None:
            retained = process._modlab_close_owner
            owner = _protocol_owner(
                ExactObjectOwnershipError(close_error, verification=(retained,)), pending,
            )
            pending = _merge_controller_errors(pending, owner)
    if isinstance(pending, (WatchProtocolOwnershipError, ExactObjectOwnershipError)):
        pending.resolve()
        return error
    return pending


def _open_request_vault(request: WatchRequest) -> EvidenceVault:
    # Parse binding before any native lookup; never infer a vault from a child.
    watch_request_to_bytes(request)
    vault = open_vault(request.authority_root, expected_creator_sid=request.authority_creator_sid)
    try:
        if (vault.identity.volume_serial, vault.identity.file_id) != (request.authority_volume_serial, request.authority_file_id):
            raise WatchProtocolError("authority root identity mismatch")
        vault.verify_descendant(request.evidence_root)
        return vault
    except BaseException as error:
        try:
            vault.close()
        except BaseException as cleanup:
            raise _merge_controller_errors(error, cleanup)
        raise


@contextmanager
def _guard_request(request):
    vault = _open_request_vault(request)
    try:
        yield vault
    except BaseException as error:
        try:
            vault.close()
        except BaseException as cleanup:
            raise _merge_controller_errors(error, cleanup)
        raise
    else:
        vault.close()


def _verify_records(vault: EvidenceVault, request: WatchRequest, *, include_outcome=False) -> None:
    vault.verify_descendant(request.evidence_root)
    for name in {_REQUEST_NAME, _CLAIM_NAME, _LAUNCH_NAME, _READY_NAME, _EVENTS_NAME,
                 _TERMINAL_NAME, _CONTROLLER_LOSS_NAME, _OUTCOME_NAME, *CAUSAL_NAMES.values()}:
        path = request.evidence_root / name
        if (name != _OUTCOME_NAME or include_outcome) and path.exists():
            vault.verify_descendant(path)


def _runtime_paths() -> tuple[Path, ...]:
    source = Path(__file__).absolute().parents[2]
    modules = ("modlab/__init__.py", "modlab/platform/__init__.py", "modlab/validation/__init__.py",
               "modlab/platform/windows_exact_fs.py", "modlab/validation/windows_watch.py",
               "modlab/validation/windows_watch_protocol.py", "modlab/validation/windows_vault_security.py",
               "modlab/validation/windows_process_job.py", "modlab/validation/mo2_containment_model.py",
               "modlab/validation/mo2_containment_serialization.py", "modlab/validation/mo2_containment_authority.py")
    runtime = Path(sys.base_prefix)
    paths = [Path(sys.executable), source, runtime, *(source / module for module in modules)]
    # Isolated -S startup uses only the interpreter's standard library/DLL paths.
    for name in ("Lib", "DLLs", f"python{sys.version_info.major}{sys.version_info.minor}.zip",
                 f"python{sys.version_info.major}{sys.version_info.minor}.dll", "python3.dll"):
        path = runtime / name
        if path.exists():
            paths.append(path)
    return tuple(paths)


def start_watch(request: WatchRequest, *, on_created: Callable[[int], None] | None = None) -> int:
    vault = _open_request_vault(request)
    runtime = None
    try:
        runtime = pin_trusted_paths(_runtime_paths())
        return _start_watch(request, on_created=on_created, vault=vault, runtime_guard=runtime)
    except BaseException as error:
        with _LOCAL_SESSIONS_LOCK:
            retained = _LOCAL_SESSIONS.get((request.evidence_root / _REQUEST_NAME).absolute())
        if retained is None:
            for guard in (runtime, vault):
                if guard is not None:
                    try:
                        guard.close()
                    except BaseException as cleanup:
                        error = _merge_controller_errors(error, cleanup)
        raise error


def run_watch_worker(request_path: Path) -> int:
    request = _load_request_path(request_path)
    with _guard_request(request) as vault:
        _verify_records(vault, request)
        if _load_request_path(request_path) != request:
            raise WatchProtocolError("request changed during vault acquisition")
        result = _run_watch_worker(request_path)
        _verify_records(vault, request)
        return result


def _pin_record(path: Path, *, wait_for_publication=False) -> tuple[PinnedObject, bytes]:
    deadline = time.monotonic() + 1.0
    while True:
        handle = _kernel32.CreateFileW(str(path), _GENERIC_READ, _FILE_SHARE_READ, None,
                                       _OPEN_EXISTING, _FILE_FLAG_OPEN_REPARSE_POINT, None)
        if handle != _INVALID_HANDLE_VALUE:
            break
        error = _winerror(f"could not pin causal record {path}")
        if not wait_for_publication or error.errno != _ERROR_SHARING_VIOLATION or time.monotonic() >= deadline:
            raise error
        time.sleep(.01)
    pin = PinnedObject(path, handle, None)
    try:
        volume, file_id, attributes = _handle_identity(handle, path)
        if attributes & (_FILE_ATTRIBUTE_DIRECTORY | _FILE_ATTRIBUTE_REPARSE_POINT):
            raise WatchProtocolError("causal record must be direct regular file")
        pin.identity = PinnedIdentity(volume, file_id, attributes)
        return pin, read_pinned_file(pin)
    except BaseException as error:
        try:
            pin.close()
        except BaseException as cleanup:
            raise ExactObjectOwnershipError(str(cleanup), verification=(pin,)) from error
        raise


def _read_causal(request, claim, launch, kind):
    path = request.evidence_root / CAUSAL_NAMES[kind]
    try:
        data = _read_exact_regular_file(path, kind)
    except _HandleOwnershipError as error:
        raise _controller_owner(error, path)
    value = causal_record_from_bytes(data, request, claim, launch)
    if value["kind"] != kind:
        raise WatchProtocolError("causal record kind mismatch: " + kind)
    return value, hashlib.sha256(data).hexdigest()


def _publish_causal(request, claim, launch, kind, **facts):
    value = causal_record(kind, request, claim, launch, **facts)
    data = causal_record_to_bytes(value, request, claim, launch)
    _write_new(request.evidence_root / CAUSAL_NAMES[kind], data)
    return value


def _local_launch_session(request_path):
    with _LOCAL_SESSIONS_LOCK:
        session = _LOCAL_SESSIONS.get(Path(request_path).absolute())
    if session is None or _current_controller_identity() != (session.controller_pid, session.controller_creation_time):
        raise WatchProtocolError("original local watcher ownership required")
    return session


def admit_watch_launch(request_path: Path, launch: ProcessLaunch) -> None:
    """Bind the original private-job root, only from the launcher's before_resume."""
    session = _local_launch_session(request_path)
    with session.lock:
        if session.process_owner is not None or session.poison_reasons:
            raise WatchProtocolError("watch launch already admitted or poisoned")
        owner = launch.owner
        if not isinstance(owner, ProcessJobOwner):
            raise WatchProtocolError("retained process job owner required")
        # Retain before any fallible observation/publication; failed launch keeps
        # its original owner reachable through this session and launch exception.
        session.process_owner = owner
        try:
            _verify_records(session.vault, session.request)
            if owner.resumed:
                raise WatchProtocolError("launch admission must precede verified resume")
            observation = owner.observe()
            if (observation.pid, observation.creation_time) != (launch.pid, launch.creation_time) or observation.root_exit_code is not None or observation.active_processes != 1:
                raise WatchProtocolError("launch admission original process identity mismatch")
            handle = _popen_process_handle(session.process)
            _verify_retained_process_handle(handle, session.worker_pid, session.worker_creation_time)
            if _kernel32.WaitForSingleObject(handle, 0) != _WAIT_TIMEOUT or session.request.stop_token_path.exists():
                raise WatchProtocolError("watcher no longer armed before process admission")
            ready = _read_exact_regular_file(session.request.evidence_root / _READY_NAME, "ready record")
            _parse_ready(ready, session.request, session.worker_pid, session.request_sha256, session.worker_creation_time)
            session.admission = _publish_causal(session.request, session.claim, session.launch, "LaunchAdmission",
                readySha256=hashlib.sha256(ready).hexdigest(), processPid=launch.pid, processCreationTime=launch.creation_time)
        except BaseException as error:
            session.poison_reasons.append("launch-admission-failed")
            if isinstance(error, _HandleOwnershipError):
                raise _controller_owner(error, session.request.evidence_root)
            raise


def complete_watch_launch(request_path: Path, owner: ProcessJobOwner | None = None) -> None:
    """Persist original tree quiescence; close remains the stop operation's duty."""
    session = _local_launch_session(request_path)
    try:
        _complete_watch_launch(request_path, owner)
    except BaseException as error:
        if session.process_owner is not None:
            error.owner = session.process_owner
        raise


def _complete_watch_launch(request_path: Path, owner: ProcessJobOwner | None = None) -> None:
    """Query original ownership and persist quiescence; retain owner until stop."""
    session = _local_launch_session(request_path)
    with session.lock:
        retained = session.process_owner
        if retained is None or (owner is not None and owner is not retained) or session.admission is None:
            raise WatchProtocolError("original admitted process owner required")
        if session.poison_reasons or not retained.resumed:
            raise WatchProtocolError("normal completion requires unbroken verified resume")
        _verify_records(session.vault, session.request)
        observation = retained.observe()
        if (observation.pid, observation.creation_time) != (session.admission["processPid"], session.admission["processCreationTime"]):
            raise WatchProtocolError("quiescence root identity mismatch")
        if observation.root_exit_code is None or observation.active_processes != 0:
            raise WatchProtocolError("original process tree is still active")
        admission, admission_hash = _read_causal(session.request, session.claim, session.launch, "LaunchAdmission")
        if admission != session.admission:
            raise WatchProtocolError("admission differs from retained process observation")
        value = causal_record("ProcessTreeQuiescence", session.request, session.claim, session.launch,
            admissionSha256=admission_hash, processPid=observation.pid, processCreationTime=observation.creation_time,
            rootExitCode=observation.root_exit_code, activeProcesses=0, totalProcesses=observation.total_processes, resumeVerified=True)
        data = causal_record_to_bytes(value, session.request, session.claim, session.launch)
        try:
            _write_new(session.request.evidence_root / CAUSAL_NAMES["ProcessTreeQuiescence"], data)
        except FileExistsError:
            existing, _ = _read_causal(session.request, session.claim, session.launch, "ProcessTreeQuiescence")
            if existing != value:
                raise WatchProtocolError("quiescence collision differs from retained observation")
        session.quiescence = value


def _validate_stop_chain(request, claim, launch, stop):
    if stop["kind"] != "NormalControllerStop":
        raise WatchProtocolError("normal controller stop required")
    admission, admission_hash = _read_causal(request, claim, launch, "LaunchAdmission")
    quiescence, quiescence_hash = _read_causal(request, claim, launch, "ProcessTreeQuiescence")
    try:
        ready = _read_exact_regular_file(request.evidence_root / _READY_NAME, "ready record")
    except _HandleOwnershipError as error:
        owner = _controller_owner(error, request.evidence_root / _READY_NAME)
        owner.resolve()
        raise WatchProtocolError("ready evidence read close failed") from error
    _parse_ready(ready, request, launch.worker_pid, watch_request_sha256(request), launch.worker_creation_time)
    if admission["readySha256"] != hashlib.sha256(ready).hexdigest() or quiescence["admissionSha256"] != admission_hash or stop["admissionSha256"] != admission_hash or stop["quiescenceSha256"] != quiescence_hash:
        raise WatchProtocolError("causal chain hash mismatch")
    if (admission["processPid"], admission["processCreationTime"]) != (quiescence["processPid"], quiescence["processCreationTime"]):
        raise WatchProtocolError("causal root identity mismatch")


def _durable_worker_exit(request, claim, launch, captured):
    stop, stop_hash = _read_causal(request, claim, launch, "NormalControllerStop")
    _validate_stop_chain(request, claim, launch, stop)
    pin, data = _pin_record(request.stop_token_path)
    pending = None
    try:
        terminal = _parse_terminal(_read_exact_regular_file(request.evidence_root / _TERMINAL_NAME, "terminal record"),
            request, launch.worker_pid, watch_request_sha256(request), launch.worker_creation_time)
        expected = {"sha256": hashlib.sha256(data).hexdigest(), "volumeSerial": pin.identity.volume_serial, "fileId": pin.identity.file_id}
        if expected["sha256"] != stop_hash or terminal["stopBinding"] != expected:
            raise WatchProtocolError("terminal exact stop identity/hash mismatch")
    except BaseException as error:
        pending = _controller_owner(error, request.evidence_root / _TERMINAL_NAME)
        raise pending
    finally:
        try:
            pin.close()
        except BaseException as error:
            cleanup = ExactObjectOwnershipError(str(error), verification=(pin,))
            if pending is not None:
                raise _merge_controller_errors(pending, cleanup)
            raise cleanup from error
    proof, _ = _read_causal(request, claim, launch, "WorkerExitObservation")
    if proof["stopSha256"] != stop_hash or proof["terminalSha256"] != captured.terminal_bytes_sha256 or proof["eventSha256"] != captured.journal.sha256:
        raise WatchProtocolError("worker exit evidence hash mismatch")
    return proof


def _start_watch(
    request: WatchRequest,
    *,
    on_created: Callable[[int], None] | None = None,
    vault: EvidenceVault, runtime_guard: object,
) -> int:
    """Start one watcher owned exclusively by this controller process."""
    _require_windows()
    if on_created is not None and not callable(on_created):
        raise TypeError("watch creation callback must be callable")
    normalized = _normalize_request(request, inspect_roots=True)
    evidence_root = normalized.evidence_root
    request_path = evidence_root / _REQUEST_NAME
    claim_path = evidence_root / _CLAIM_NAME
    launch_path = evidence_root / _LAUNCH_NAME
    ready_path = evidence_root / _READY_NAME
    request_bytes, request_sha256 = _request_bytes_and_sha256(normalized)
    for path in (
        request_path,
        claim_path,
        launch_path,
        ready_path,
        evidence_root / _EVENTS_NAME,
        evidence_root / _TERMINAL_NAME,
        evidence_root / _CONTROLLER_LOSS_NAME,
        evidence_root / _OUTCOME_NAME,
        normalized.stop_token_path,
    ):
        if path.exists():
            raise WatchProtocolError(
                f"watch protocol path already exists: {path.name}"
            )
    controller_pid, controller_creation_time = _current_controller_identity()
    publish_new_verified(
        request_path,
        request_bytes,
        watch_request_from_bytes,
    )
    canonical_request_path = request_path.resolve(strict=True)
    command = watch_worker_command(canonical_request_path)
    claim = ControllerClaim(
        schema_version=_SCHEMA_VERSION,
        request_sha256=request_sha256,
        session_id=normalized.session_id,
        run_id=normalized.run_id,
        scenario=normalized.scenario,
        request_path=canonical_request_path,
        worker_command=command,
        controller_pid=controller_pid,
        controller_creation_time=controller_creation_time,
    )
    claim_bytes = controller_claim_to_bytes(claim, normalized)
    publish_new_verified(
        claim_path,
        claim_bytes,
        lambda data: controller_claim_from_bytes(data, normalized),
    )
    try:
        process = subprocess.Popen(
            claim.worker_command,
            shell=False,
            cwd=str(Path(__file__).absolute().parents[2]),
            env={key: value for key, value in os.environ.items() if not key.upper().startswith("PYTHON")},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        raise WatchProtocolError(f"watch worker launch failed: {error}") from error
    worker_pid = process.pid
    if type(worker_pid) is not int or worker_pid <= 0:
        error = WatchProtocolError("watch worker returned an invalid PID")
        raise _cleanup_start_failure(error, request_path, process)
    if on_created is not None:
        try:
            on_created(worker_pid)
        except BaseException as callback_error:
            raise _cleanup_start_failure(callback_error, request_path, process)
    request_key = request_path.absolute()
    session: _LocalWatchSession | None = None
    session_registered = False
    startup_error: BaseException | None = None
    try:
        process_handle = _popen_process_handle(process)
        worker_creation_time = _process_handle_creation_time(
            process_handle,
            worker_pid,
        )
        launch = WorkerLaunch(
            schema_version=_SCHEMA_VERSION,
            request_sha256=request_sha256,
            session_id=normalized.session_id,
            run_id=normalized.run_id,
            scenario=normalized.scenario,
            worker_pid=worker_pid,
            worker_creation_time=worker_creation_time,
        )
        session = _LocalWatchSession(
            request=normalized,
            request_sha256=request_sha256,
            claim=claim,
            launch=launch,
            controller_pid=controller_pid,
            controller_creation_time=controller_creation_time,
            worker_pid=worker_pid,
            worker_creation_time=worker_creation_time,
            process=process,
            lock=threading.Lock(),
            poison_reasons=[],
            vault=vault, runtime_guard=runtime_guard, record_pins=[],
        )
        with _LOCAL_SESSIONS_LOCK:
            if request_key in _LOCAL_SESSIONS:
                raise WatchProtocolError("local watch session already exists")
            _LOCAL_SESSIONS[request_key] = session
            session_registered = True
        launch_bytes = worker_launch_to_bytes(launch, normalized)
        publish_new_verified(
            launch_path,
            launch_bytes,
            lambda data: worker_launch_from_bytes(data, normalized),
        )
    except BaseException as error:
        startup_error = error
    if startup_error is not None:
        pending = _cleanup_start_failure(
            startup_error, request_path, process, session if session_registered else None,
            reason="worker-launch-publication-failed",
        )
        if not isinstance(startup_error, (OSError, WatchProtocolError)):
            raise pending
        raise WatchProtocolError(
            f"watch worker identity/launch publication failed: {startup_error}"
        ) from startup_error
    assert session is not None
    deadline = time.monotonic() + 15.0
    try:
        while time.monotonic() < deadline:
            try:
                ready_bytes = _read_exact_regular_file(
                    ready_path,
                    "ready record",
                )
            except OSError as error:
                error_code = getattr(error, "winerror", None)
                if error_code is None:
                    error_code = error.errno
                if not isinstance(error, FileNotFoundError) and error_code not in {
                    _ERROR_SHARING_VIOLATION,
                    _ERROR_LOCK_VIOLATION,
                }:
                    raise
                wait_result = _kernel32.WaitForSingleObject(process_handle, 0)
                if wait_result == _WAIT_OBJECT_0:
                    raise WatchProtocolError(
                        "watch worker exited before ready"
                    ) from error
                if wait_result != _WAIT_TIMEOUT:
                    raise WatchProtocolError(
                        f"ready worker liveness wait failed: {wait_result}"
                    ) from error
                time.sleep(0.02)
                continue
            else:
                _parse_ready(
                    ready_bytes,
                    normalized,
                    worker_pid,
                    request_sha256,
                    worker_creation_time,
                )
                for record_path, expected in ((request_path, request_bytes), (claim_path, claim_bytes),
                                              (launch_path, launch_bytes), (ready_path, ready_bytes)):
                    pin, data = _pin_record(record_path)
                    session.record_pins.append(pin)
                    if data != expected:
                        raise WatchProtocolError("startup record changed before pin")
                vault.verify()
                return worker_pid
        raise WatchProtocolError("watch worker did not become ready")
    except BaseException as error:
        pending = _cleanup_start_failure(error, request_path, process, session)
        if not isinstance(error, (OSError, WatchProtocolError)):
            raise pending
        raise WatchProtocolError(
            f"{error}; watch startup cleanup was incomplete"
        ) from error


def _parse_notification_buffer(buffer: ctypes.Array[ctypes.c_char], size: int) -> tuple[tuple[str, str], ...]:
    if size <= 0 or size > len(buffer):
        raise WatchProtocolError("malformed notification buffer length")
    raw = bytes(buffer[:size])
    records: list[tuple[str, str]] = []
    offset = 0
    while True:
        if offset + 12 > size:
            raise WatchProtocolError("malformed notification record header")
        next_offset = int.from_bytes(raw[offset : offset + 4], "little")
        action_number = int.from_bytes(raw[offset + 4 : offset + 8], "little")
        name_size = int.from_bytes(raw[offset + 8 : offset + 12], "little")
        if name_size == 0 or name_size % 2 or offset + 12 + name_size > size:
            raise WatchProtocolError("malformed notification record name")
        try:
            native_relative_path = raw[offset + 12 : offset + 12 + name_size].decode("utf-16-le")
        except UnicodeDecodeError as error:
            raise WatchProtocolError("malformed notification record UTF-16") from error
        relative_path = native_relative_path.replace("\\", "/")
        _validate_relative_path(relative_path)
        action = _ACTIONS.get(action_number)
        if action is None:
            raise WatchProtocolError(f"malformed notification action: {action_number}")
        records.append((action, relative_path))
        minimum_next = 12 + name_size
        if next_offset == 0:
            if offset + minimum_next != size:
                raise WatchProtocolError("malformed notification trailing bytes")
            break
        if next_offset % 4 or next_offset < minimum_next or offset + next_offset >= size:
            raise WatchProtocolError("malformed notification next offset")
        offset += next_offset
    return tuple(records)


def _validate_relative_path(value: str) -> None:
    if (
        not value
        or value.startswith(("\\", "/"))
        or "\\" in value
        or "\0" in value
        or re.match(r"^[A-Za-z]:", value)
    ):
        raise WatchProtocolError("event relative path is unsafe")
    if any(ord(character) < 0x20 for character in value):
        raise WatchProtocolError("event relative path is unsafe")
    for part in value.split("/"):
        if (
            not part
            or part in {".", ".."}
            or any(character in ':*?<>|"' for character in part)
            or part.endswith((" ", "."))
            or part.split(".", 1)[0].casefold() in _RESERVED_NAMES
        ):
            raise WatchProtocolError("event relative path is unsafe")


def _process_notification_completion(state: _WatchState, transferred: int) -> None:
    if transferred == 0:
        state.fail(f"watch overflow: ERROR_NOTIFY_ENUM_DIR ({state.root.root_kind})")
        return
    try:
        records = _parse_notification_buffer(state.buffer, transferred)
        if state.membership_name is not None:
            if any(
                relative_path.casefold() == state.membership_name.casefold()
                for _, relative_path in records
            ):
                state.fail(
                    f"root membership changed during watch: {state.root.root_kind}"
                )
        else:
            state.journal.append(state.logical_root_kinds, records)
    except (OSError, WatchProtocolError) as error:
        state.fail(f"malformed watch completion for {state.root.root_kind}: {error}")


def _consume_watch_completion(
    state: _WatchState,
    *,
    after_stop: bool,
    wait: bool = False,
) -> None:
    """Consume one signalled OVERLAPPED result and only then release its storage."""
    transferred = wintypes.DWORD()
    completed = _kernel32.GetOverlappedResult(
        state.directory_handle,
        ctypes.byref(state.overlapped),
        ctypes.byref(transferred),
        wait,
    )
    if not completed:
        code = ctypes.get_last_error()
        if code != _ERROR_OPERATION_ABORTED or not (
            after_stop or state.stopping.is_set()
        ):
            state.fail(_completion_error(state.root.root_kind, code))
        state.pending = False
        return
    _process_notification_completion(state, transferred.value)
    state.pending = False


def _cancel_and_drain(state: _WatchState) -> None:
    """Request cancellation and retain ownership until its completion is observed."""
    with state.pending_lock:
        if not state.pending:
            return
        cancelled = _kernel32.CancelIoEx(
            state.directory_handle,
            ctypes.byref(state.overlapped),
        )
        if not cancelled:
            code = ctypes.get_last_error()
            if code != _ERROR_NOT_FOUND:
                state.fail(
                    f"watch cancellation failed for {state.root.root_kind}: "
                    f"WinError {code} ({ctypes.FormatError(code)})"
                )
        wait_result = _kernel32.WaitForSingleObject(state.event_handle, _INFINITE)
        if wait_result != _WAIT_OBJECT_0:
            state.fail(
                f"watch completion wait failed for {state.root.root_kind}: "
                f"{wait_result}"
            )
            _consume_watch_completion(
                state,
                after_stop=True,
                wait=True,
            )
            return
        state.consume_completion_after_stop()


def _watch_thread(state: _WatchState) -> None:
    while not state.stopping.is_set():
        if not _kernel32.ResetEvent(state.event_handle):
            state.fail(str(_winerror(f"could not reset watch event for {state.root.root_kind}")))
            return
        returned = wintypes.DWORD()
        with state.pending_lock:
            if state.stopping.is_set():
                return
            state.pending = True
            started = _kernel32.ReadDirectoryChangesW(
                state.directory_handle,
                state.buffer,
                len(state.buffer),
                state.recursive,
                state.notify_filter,
                ctypes.byref(returned),
                ctypes.byref(state.overlapped),
                None,
            )
            if not started:
                code = ctypes.get_last_error()
                if code != _ERROR_IO_PENDING:
                    state.pending = False
                    state.fail(_completion_error(state.root.root_kind, code))
                    return
            state.armed.set()
        wait_result = _kernel32.WaitForSingleObject(state.event_handle, _INFINITE)
        if wait_result != _WAIT_OBJECT_0:
            state.fail(f"watch completion wait failed for {state.root.root_kind}: {wait_result}")
            return
        with state.pending_lock:
            if not state.pending:
                return
            _consume_watch_completion(state, after_stop=False)


def _completion_error(root_kind: str, code: int) -> str:
    if code == _ERROR_NOTIFY_ENUM_DIR:
        return f"watch overflow: ERROR_NOTIFY_ENUM_DIR ({root_kind})"
    return f"watch completion failed for {root_kind}: WinError {code} ({ctypes.FormatError(code)})"


def _terminal_document(
    request: WatchRequest,
    *,
    worker_pid: int,
    worker_creation_time: int,
    request_sha256: str,
    complete: bool,
    ready: bool,
    event_digest: str,
    errors: list[str],
    open_handle_count: int,
    root_identities_unchanged: bool,
    opened_root_kinds: tuple[str, ...],
    journal_evidence: _JournalEvidence,
    stop_binding: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "stopBinding": stop_binding,
        "complete": complete,
        "error": None if not errors else "; ".join(errors),
        "eventByteCount": len(journal_evidence.data),
        "eventBytesSha256": event_digest,
        "eventCount": journal_evidence.event_count,
        "finalSequence": journal_evidence.final_sequence,
        "journalFileId": journal_evidence.file_id,
        "journalVolumeSerial": journal_evidence.volume_serial,
        "openHandleCount": open_handle_count,
        "openedRootKinds": list(opened_root_kinds),
        "ready": ready,
        "requestBytesSha256": request_sha256,
        "requestId": request.request_id,
        "rootIdentitiesUnchanged": root_identities_unchanged,
        "schemaVersion": _SCHEMA_VERSION,
        "workerCreationTime": worker_creation_time,
        "workerPid": worker_pid,
    }


def _arm_directory_state(
    *,
    root: WatchRoot,
    logical_root_kinds: tuple[str, ...],
    directory_path: Path,
    directory_handle: int,
    expected_volume_serial: int,
    expected_file_id: int,
    label: str,
    recursive: bool,
    notify_filter: int,
    membership_name: str | None,
    journal: _EventJournal,
    stopping: threading.Event,
    errors: list[str],
    errors_lock: threading.Lock,
    states: list[_WatchState],
) -> _WatchState:
    if stopping.is_set():
        with errors_lock:
            detail = errors[-1] if errors else f"watch failed before arming: {label}"
        raise WatchProtocolError(detail)
    event_handle = _kernel32.CreateEventW(None, True, False, None)
    if not event_handle:
        raise _winerror(f"could not create completion event for {label}")
    try:
        overlapped = _OVERLAPPED()
        overlapped.hEvent = event_handle
        state = _WatchState(
            root=root,
            logical_root_kinds=logical_root_kinds,
            directory_path=directory_path,
            label=label,
            directory_handle=directory_handle,
            expected_volume_serial=expected_volume_serial,
            expected_file_id=expected_file_id,
            event_handle=event_handle,
            recursive=recursive,
            notify_filter=notify_filter,
            membership_name=membership_name,
            journal=journal,
            stopping=stopping,
            errors=errors,
            errors_lock=errors_lock,
            buffer=ctypes.create_string_buffer(_BUFFER_SIZE),
            overlapped=overlapped,
            armed=threading.Event(),
            pending_lock=threading.Lock(),
        )
    except BaseException as error:
        close_error = _close_handle(event_handle, f"unowned completion event {label}")
        if close_error is not None:
            raise _HandleOwnershipError(
                f"{error}; {close_error}",
                event_handle,
                f"unowned completion event {label}",
            ) from error
        raise
    states.append(state)
    state.thread = threading.Thread(
        target=_watch_thread,
        args=(state,),
        name=f"watch-{state.label}",
    )
    try:
        state.thread.start()
        state.started = True
    except RuntimeError as error:
        stopping.set()
        raise WatchProtocolError(f"thread start failure for {state.label}: {error}") from error
    if not state.armed.wait(10.0):
        stopping.set()
        raise WatchProtocolError(f"watch request did not arm: {state.label}")
    if stopping.is_set():
        with errors_lock:
            detail = errors[-1] if errors else f"watch failed while arming: {state.label}"
        raise WatchProtocolError(detail)
    return state


def _run_watch_worker(request_path: Path) -> int:
    """Run one fail-closed worker: 0 complete, 1 incomplete, 2 untrustworthy."""
    _require_windows()
    try:
        canonical_request_path = _reject_reparse_components(
            Path(request_path),
            "request path",
        ).resolve(strict=True)
        request = _load_request_path(canonical_request_path)
        _, request_sha256 = _request_bytes_and_sha256(request)
        claim = controller_claim_from_bytes(
            _read_exact_regular_file(request.evidence_root / _CLAIM_NAME, "controller claim"),
            request,
        )
        if (
            claim.request_path != canonical_request_path
            or claim.worker_command != watch_worker_command(canonical_request_path)
        ):
            raise WatchProtocolError(
                "controller claim does not bind this worker invocation"
            )
    except (OSError, WatchProtocolError):
        return 2
    ready_path = request.evidence_root / _READY_NAME
    events_path = request.evidence_root / _EVENTS_NAME
    terminal_path = request.evidence_root / _TERMINAL_NAME
    controller_loss_path = request.evidence_root / _CONTROLLER_LOSS_NAME
    worker_pid = os.getpid()
    controller_handle = 0
    controller_handle_label = f"launching controller process {claim.controller_pid}"
    controller_open_error: str | None = None
    retained_controller_handle = False
    try:
        controller_handle, observed_controller_creation = _open_process_identity(
            claim.controller_pid
        )
        if observed_controller_creation != claim.controller_creation_time:
            controller_open_error = "controller process identity mismatch"
    except _HandleOwnershipError as error:
        controller_handle = error.handle
        controller_handle_label = error.label
        retained_controller_handle = True
        controller_open_error = f"controller identity acquisition failed: {error}"
    except (OSError, WatchProtocolError) as error:
        controller_open_error = f"controller identity acquisition failed: {error}"
    if controller_open_error == "controller process identity mismatch" and controller_handle:
        close_error = _close_handle(controller_handle, controller_handle_label)
        if close_error is None:
            controller_handle = 0
        else:
            retained_controller_handle = True
            controller_open_error = f"{controller_open_error}; {close_error}"
    try:
        worker_handle, worker_creation_time = _open_process_identity(worker_pid)
    except _HandleOwnershipError as identity_error:
        worker_handle = identity_error.handle
        worker_creation_time = 0
        worker_close_error = str(identity_error)
        worker_handle_label = identity_error.label
    else:
        worker_handle_label = f"worker self process {worker_pid}"
        worker_close_error = _close_handle(
            worker_handle,
            worker_handle_label,
        )
    launch_error: str | None = None
    launch_deadline = time.monotonic() + 10.0
    while True:
        launch_path = request.evidence_root / _LAUNCH_NAME
        if launch_path.exists():
            try:
                launch = worker_launch_from_bytes(
                    _read_exact_regular_file(launch_path, "worker launch"),
                    request,
                )
                if launch.worker_pid != worker_pid or (
                    worker_creation_time != 0
                    and launch.worker_creation_time != worker_creation_time
                ):
                    raise WatchProtocolError(
                        "worker launch does not bind this exact worker"
                    )
                if worker_creation_time == 0:
                    worker_creation_time = launch.worker_creation_time
            except (OSError, WatchProtocolError) as error:
                launch_error = f"worker launch validation failed: {error}"
            break
        if controller_handle:
            controller_wait = _kernel32.WaitForSingleObject(controller_handle, 0)
            if controller_wait == _WAIT_OBJECT_0:
                launch_error = "controller-session-lost"
                break
            if controller_wait == _WAIT_FAILED:
                launch_error = "controller-liveness-wait-failed"
                break
        if time.monotonic() >= launch_deadline:
            launch_error = "worker launch record missing"
            break
        time.sleep(0.01)
    errors: list[str] = []
    retained_worker_handles: list[tuple[int, str]] = []
    if controller_open_error is not None:
        errors.append(controller_open_error)
    if launch_error is not None:
        errors.append(launch_error)
    if retained_controller_handle and controller_handle:
        retained_worker_handles.append(
            (controller_handle, controller_handle_label)
        )
        controller_handle = 0
    if worker_close_error is not None:
        errors.append(worker_close_error)
        retained_worker_handles.append(
            (worker_handle, worker_handle_label)
        )
    errors_lock = threading.Lock()
    stopping = threading.Event()
    states: list[_WatchState] = []
    opened_kinds: list[str] = []
    journal: _EventJournal | None = None
    ready = False
    root_identities_unchanged = False
    failed_closes = 0
    journal_evidence = _JournalEvidence(0, 0, b"", 0, 0)
    unresolved_protocol_ownership: WatchProtocolOwnershipError | None = None
    stop_pin = None
    stop_binding = None
    normal_stop_valid = False
    try:
        if errors:
            raise WatchProtocolError(errors[-1])
        if events_path.exists():
            raise WatchProtocolError("events journal must not exist before worker start")
        journal = _EventJournal(events_path)
        physical_roots: list[tuple[WatchRoot, tuple[str, ...]]] = []
        by_identity: dict[tuple[int, int], list[WatchRoot]] = {}
        for root in request.roots:
            by_identity.setdefault((root.volume_serial, root.file_id), []).append(root)
        for aliases in by_identity.values():
            representative = aliases[0]
            if any(alias.path != representative.path for alias in aliases[1:]):
                raise WatchProtocolError("aliased logical roots must use one canonical path")
            physical_roots.append(
                (representative, tuple(alias.root_kind for alias in aliases))
            )
        for root, logical_root_kinds in physical_roots:
            volume_path = Path(root.path.anchor)
            if root.path == volume_path:
                raise WatchProtocolError(f"watched root cannot be a volume root: {root.root_kind}")
            current_path = volume_path
            current_handle = _open_directory(current_path, overlapped=True)
            current_owned = True
            try:
                current_volume, current_file_id, current_attributes = _handle_identity(
                    current_handle,
                    current_path,
                )
                if not current_attributes & _FILE_ATTRIBUTE_DIRECTORY:
                    raise WatchProtocolError(f"volume root is not a directory: {current_path}")
                if current_attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                    raise WatchProtocolError(f"volume root became reparse: {current_path}")
                components = root.path.parts[1:]
                for component_index, component in enumerate(components):
                    _arm_directory_state(
                        root=root,
                        logical_root_kinds=logical_root_kinds,
                        directory_path=current_path,
                        directory_handle=current_handle,
                        expected_volume_serial=current_volume,
                        expected_file_id=current_file_id,
                        label=f"membership-{root.root_kind}-{component_index}",
                        recursive=False,
                        notify_filter=_FILE_NOTIFY_CHANGE_FILE_NAME
                        | _FILE_NOTIFY_CHANGE_DIR_NAME,
                        membership_name=component,
                        journal=journal,
                        stopping=stopping,
                        errors=errors,
                        errors_lock=errors_lock,
                        states=states,
                    )
                    current_owned = False
                    child_path = current_path / component
                    child_handle = _open_directory(child_path, overlapped=True)
                    try:
                        child_volume, child_file_id, child_attributes = _handle_identity(
                            child_handle,
                            child_path,
                        )
                        if not child_attributes & _FILE_ATTRIBUTE_DIRECTORY:
                            raise WatchProtocolError(
                                f"watched path component is not a directory: {child_path}"
                            )
                        if child_attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                            raise WatchProtocolError(
                                f"watched path component became reparse: {child_path}"
                            )
                    except BaseException as error:
                        child_label = f"unvalidated directory {child_path}"
                        close_error = _close_handle(
                            child_handle,
                            child_label,
                        )
                        if close_error:
                            raise _HandleOwnershipError(
                                f"{error}; {close_error}",
                                child_handle,
                                child_label,
                            ) from error
                        raise
                    current_path = child_path
                    current_handle = child_handle
                    current_owned = True
                    current_volume = child_volume
                    current_file_id = child_file_id
                if (current_volume, current_file_id) != (
                    root.volume_serial,
                    root.file_id,
                ):
                    raise WatchProtocolError(
                        f"root identity changed before ready: {root.root_kind}"
                    )
                _arm_directory_state(
                    root=root,
                    logical_root_kinds=logical_root_kinds,
                    directory_path=current_path,
                    directory_handle=current_handle,
                    expected_volume_serial=current_volume,
                    expected_file_id=current_file_id,
                    label=root.root_kind,
                    recursive=True,
                    notify_filter=_NOTIFY_FILTER,
                    membership_name=None,
                    journal=journal,
                    stopping=stopping,
                    errors=errors,
                    errors_lock=errors_lock,
                    states=states,
                )
                current_owned = False
            finally:
                if current_owned and not any(
                    state.directory_handle == current_handle for state in states
                ):
                    close_error = _close_handle(
                        current_handle,
                        f"unarmed directory {current_path}",
                    )
                    if close_error:
                        errors.append(close_error)
                        failed_closes += 1
        opened_kinds.extend(root.root_kind for root in request.roots)
        if not errors:
            _write_new(
                ready_path,
                _canonical_bytes(
                    _ready_document(
                        request,
                        worker_pid,
                        request_sha256,
                        worker_creation_time,
                    )
                ),
            )
            ready = True
        while not stopping.is_set():
            if request.stop_token_path.exists():
                stop_pin, stop_data = _pin_record(request.stop_token_path, wait_for_publication=True)
                stop = causal_record_from_bytes(stop_data, request, claim, launch)
                if stop["kind"] != "NormalControllerStop":
                    errors.append("recovery-cleanup-stop")
                else:
                    _validate_stop_chain(request, claim, launch, stop)
                    normal_stop_valid = True
                stop_binding = {"sha256": hashlib.sha256(stop_data).hexdigest(),
                                "volumeSerial": stop_pin.identity.volume_serial,
                                "fileId": stop_pin.identity.file_id}
                stopping.set()
                break
            if controller_handle:
                controller_wait = _kernel32.WaitForSingleObject(
                    controller_handle,
                    0,
                )
                if controller_wait == _WAIT_OBJECT_0:
                    errors.append("controller-session-lost")
                    stopping.set()
                    break
                if controller_wait == _WAIT_FAILED:
                    errors.append("controller-liveness-wait-failed")
                    stopping.set()
                    break
            time.sleep(0.02)
    except _HandleOwnershipError as error:
        errors.append(str(error))
        retained_worker_handles.append((error.handle, error.label))
        stopping.set()
    except WatchProtocolOwnershipError as error:
        unresolved_protocol_ownership = error
        _resolve_watch_protocol_ownership(error)
        unresolved_protocol_ownership = None
        errors.append(str(error))
        stopping.set()
    except (OSError, WatchProtocolError) as error:
        errors.append(str(error))
        stopping.set()
    finally:
        stopping.set()
        for state in states:
            _cancel_and_drain(state)
        for state in states:
            if state.thread is not None and state.started:
                state.thread.join()
        if ready:
            exact_handles_unchanged = []
            for state in states:
                try:
                    volume_serial, file_id, attributes = _handle_identity(
                        state.directory_handle,
                        state.directory_path,
                    )
                    exact_handles_unchanged.append(
                        bool(attributes & _FILE_ATTRIBUTE_DIRECTORY)
                        and not bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)
                        and (volume_serial, file_id)
                        == (state.expected_volume_serial, state.expected_file_id)
                    )
                except OSError:
                    exact_handles_unchanged.append(False)
            root_identities_unchanged = all(exact_handles_unchanged)
            if not root_identities_unchanged:
                errors.append("retained component or root identity changed after ready")
        for state in reversed(states):
            for handle, label in (
                (state.event_handle, f"completion event {state.label}"),
                (state.directory_handle, f"directory {state.label}"),
            ):
                close_error = _close_handle(handle, label)
                if close_error:
                    errors.append(close_error)
                    failed_closes += 1
        for handle, label in retained_worker_handles:
            close_error = _close_handle(handle, label)
            if close_error:
                errors.append(close_error)
                failed_closes += 1
        if journal is not None:
            try:
                journal_evidence = journal.evidence()
            except (OSError, WatchProtocolError) as error:
                errors.append(f"could not reload exact event bytes: {error}")
            close_error = journal.close()
            if close_error:
                errors.append(close_error)
                failed_closes += 1
        if controller_handle:
            controller_wait = _kernel32.WaitForSingleObject(controller_handle, 0)
            if (
                controller_wait == _WAIT_OBJECT_0
                and "controller-session-lost" not in errors
            ):
                errors.append("controller-session-lost")
            elif (
                controller_wait == _WAIT_FAILED
                and "controller-liveness-wait-failed" not in errors
            ):
                errors.append("controller-liveness-wait-failed")
            close_error = _close_handle(
                controller_handle,
                controller_handle_label,
            )
            if close_error:
                errors.append(close_error)
                failed_closes += 1
        if stop_pin is not None:
            try:
                stop_pin.close()
            except BaseException as error:
                raise ExactObjectOwnershipError(str(error), verification=(stop_pin,)) from error
        event_digest = journal_evidence.sha256
        if not normal_stop_valid and not errors:
            errors.append("normal-controller-stop-missing")
        complete = ready and normal_stop_valid and not errors and failed_closes == 0 and root_identities_unchanged
        terminal = _terminal_document(
            request,
            worker_pid=worker_pid,
            worker_creation_time=worker_creation_time,
            request_sha256=request_sha256,
            complete=complete,
            ready=ready,
            event_digest=event_digest,
            errors=errors,
            open_handle_count=failed_closes,
            root_identities_unchanged=root_identities_unchanged,
            opened_root_kinds=tuple(opened_kinds),
            journal_evidence=journal_evidence,
            stop_binding=stop_binding,
        )
        if unresolved_protocol_ownership is not None:
            raise unresolved_protocol_ownership
        terminal_publication_failed = False
        try:
            _write_new(terminal_path, _canonical_bytes(terminal))
        except WatchProtocolOwnershipError as error:
            _resolve_watch_protocol_ownership(error)
            terminal_publication_failed = True
        except (FileExistsError, OSError, WatchProtocolError):
            terminal_publication_failed = True
        if terminal_publication_failed:
            if "controller-session-lost" in errors:
                launch = WorkerLaunch(
                    schema_version=_SCHEMA_VERSION,
                    request_sha256=request_sha256,
                    session_id=request.session_id,
                    run_id=request.run_id,
                    scenario=request.scenario,
                    worker_pid=worker_pid,
                    worker_creation_time=worker_creation_time,
                )
                loss = ControllerLoss(
                    schema_version=_SCHEMA_VERSION,
                    request_sha256=request_sha256,
                    session_id=request.session_id,
                    run_id=request.run_id,
                    scenario=request.scenario,
                    controller_pid=claim.controller_pid,
                    controller_creation_time=claim.controller_creation_time,
                    worker_pid=worker_pid,
                    worker_creation_time=worker_creation_time,
                    reason_code="controller-session-lost",
                )
                loss_bytes = controller_loss_to_bytes(
                    loss,
                    request,
                    claim,
                    launch,
                )
                try:
                    publish_new_verified(
                        controller_loss_path,
                        loss_bytes,
                        lambda data: controller_loss_from_bytes(
                            data,
                            request,
                            claim,
                            launch,
                        ),
                    )
                except WatchProtocolOwnershipError as error:
                    _resolve_watch_protocol_ownership(error)
                except (FileExistsError, OSError, WatchProtocolError):
                    pass
            return 2
    return 0 if complete else 1


def _parse_events(data: bytes, request: WatchRequest) -> tuple[WatcherEvent, ...]:
    if not data:
        return ()
    events: list[WatcherEvent] = []
    expected_sequence = 1
    allowed_kinds = {root.root_kind for root in request.roots}
    for line in data.splitlines(keepends=True):
        try:
            value = _object_from_bytes(line, "event record")
        except WatchProtocolError as error:
            raise WatchProtocolError(f"malformed events: {error}") from error
        _exact_fields(value, {"action", "relativePath", "rootKind", "sequence"}, "event record")
        sequence = _required_int(value["sequence"], "event sequence", minimum=1)
        if sequence != expected_sequence:
            raise WatchProtocolError(
                f"sequence gap: expected {expected_sequence}, observed {sequence}"
            )
        root_kind = _required_text(value["rootKind"], "event root kind")
        if root_kind not in allowed_kinds:
            raise WatchProtocolError("event root kind was not opened")
        action = _required_text(value["action"], "event action")
        if action not in _ACTIONS.values():
            raise WatchProtocolError("event action is invalid")
        relative_path = _required_text(value["relativePath"], "event relative path")
        _validate_relative_path(relative_path)
        events.append(WatcherEvent(sequence, root_kind, action, relative_path))
        expected_sequence += 1
    result = tuple(events)
    _validate_physical_event_groups(result, request)
    return result


def _validate_physical_event_groups(
    events: tuple[WatcherEvent, ...],
    request: WatchRequest,
) -> None:
    aliases_by_identity: dict[tuple[int, int], tuple[str, ...]] = {}
    identity_by_kind: dict[str, tuple[int, int]] = {}
    grouped: dict[tuple[int, int], list[str]] = {}
    for root in request.roots:
        identity = (root.volume_serial, root.file_id)
        identity_by_kind[root.root_kind] = identity
        grouped.setdefault(identity, []).append(root.root_kind)
    aliases_by_identity = {
        identity: tuple(kinds) for identity, kinds in grouped.items()
    }
    physical_events: list[tuple[tuple[int, int], str, str]] = []
    index = 0
    while index < len(events):
        event = events[index]
        identity = identity_by_kind[event.root_kind]
        aliases = aliases_by_identity[identity]
        group = events[index : index + len(aliases)]
        if (
            len(group) != len(aliases)
            or tuple(item.root_kind for item in group) != aliases
            or any(
                item.action != event.action or item.relative_path != event.relative_path
                for item in group
            )
        ):
            raise WatchProtocolError("event alias fan-out is incomplete")
        physical_events.append((identity, event.action, event.relative_path))
        index += len(aliases)
    pending: dict[tuple[int, int], str] = {}
    for identity, action, relative_path in physical_events:
        if action == "RenamedOld":
            if identity in pending:
                raise WatchProtocolError("rename pair has consecutive old names")
            pending[identity] = relative_path
        elif action == "RenamedNew":
            if identity not in pending:
                raise WatchProtocolError("rename pair is reversed or orphaned")
            del pending[identity]
        elif identity in pending:
            raise WatchProtocolError("rename pair is not ordered per physical root")
    if pending:
        raise WatchProtocolError("rename pair is orphaned")


def _parse_terminal(
    data: bytes,
    request: WatchRequest,
    worker_pid: int,
    request_sha256: str,
    worker_creation_time: int,
) -> dict[str, object]:
    value = _object_from_bytes(data, "terminal record")
    _exact_fields(
        value,
        {
            "stopBinding",
            "complete",
            "error",
            "eventByteCount",
            "eventBytesSha256",
            "eventCount",
            "finalSequence",
            "journalFileId",
            "journalVolumeSerial",
            "openHandleCount",
            "openedRootKinds",
            "ready",
            "requestBytesSha256",
            "requestId",
            "rootIdentitiesUnchanged",
            "schemaVersion",
            "workerCreationTime",
            "workerPid",
        },
        "terminal record",
    )
    if _required_int(value["schemaVersion"], "terminal schema version") != _SCHEMA_VERSION:
        raise WatchProtocolError("unsupported terminal schema version")
    if (
        value["requestId"] != request.request_id
        or _required_text(value["requestBytesSha256"], "terminal request SHA-256")
        != request_sha256
        or _required_int(value["workerPid"], "terminal worker PID") != worker_pid
        or _required_int(
            value["workerCreationTime"],
            "terminal worker creation time",
        )
        != worker_creation_time
    ):
        raise WatchProtocolError("terminal record does not match exact request and worker")
    for field in ("complete", "ready", "rootIdentitiesUnchanged"):
        if type(value[field]) is not bool:
            raise WatchProtocolError(f"terminal {field} must be boolean")
    _required_int(value["openHandleCount"], "open handle count")
    for field, label in (
        ("eventByteCount", "event byte count"),
        ("eventCount", "event count"),
        ("finalSequence", "final sequence"),
        ("journalFileId", "journal file ID"),
        ("journalVolumeSerial", "journal volume serial"),
    ):
        _required_int(value[field], label)
    digest = _required_text(value["eventBytesSha256"], "event bytes SHA-256")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise WatchProtocolError("event bytes SHA-256 must be lowercase SHA-256")
    error = value["error"]
    if error is not None:
        _required_text(error, "terminal error")
    if bool(value["complete"]) == (error is not None):
        raise WatchProtocolError("terminal complete/error fields are inconsistent")
    binding = value["stopBinding"]
    if binding is not None:
        if type(binding) is not dict or set(binding) != {"sha256", "volumeSerial", "fileId"}:
            raise WatchProtocolError("terminal stop binding fields must be exact")
        if type(binding["sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", binding["sha256"]):
            raise WatchProtocolError("terminal stop hash invalid")
        _required_int(binding["volumeSerial"], "stop volume")
        _required_int(binding["fileId"], "stop file", minimum=1)
    if value["complete"] and binding is None:
        raise WatchProtocolError("complete terminal requires exact normal stop")
    if value["complete"] and not value["ready"]:
        raise WatchProtocolError("complete terminal requires ready")
    if value["complete"] and not value["rootIdentitiesUnchanged"]:
        raise WatchProtocolError("complete terminal requires unchanged root identities")
    if value["complete"] and value["openHandleCount"] != 0:
        raise WatchProtocolError("complete terminal has an unclosed handle count")
    if value["eventCount"] != value["finalSequence"]:
        raise WatchProtocolError("terminal event count and final sequence disagree")
    if value["complete"] and (
        value["journalFileId"] == 0 or value["journalVolumeSerial"] == 0
    ):
        raise WatchProtocolError("complete terminal requires exact journal identity")
    kinds = value["openedRootKinds"]
    if type(kinds) is not list or not all(type(kind) is str for kind in kinds):
        raise WatchProtocolError("terminal root kinds must be an array of strings")
    expected = tuple(root.root_kind for root in request.roots)
    opened = tuple(kinds)
    if opened != expected[: len(opened)]:
        raise WatchProtocolError("terminal opened root kinds do not match request order")
    if value["ready"] and opened != expected:
        raise WatchProtocolError("ready terminal did not open every requested root")
    return value


def _process_alive(pid: int) -> bool:
    handle = _kernel32.OpenProcess(
        _SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        pid,
    )
    if not handle:
        if ctypes.get_last_error() == _ERROR_INVALID_PARAMETER:
            return False
        return True
    result = _kernel32.WaitForSingleObject(handle, 0)
    close_error = _close_handle(handle, f"worker liveness process {pid}")
    if close_error is not None:
        raise _HandleOwnershipError(
            close_error,
            handle,
            f"worker liveness process {pid}",
        )
    return result == _WAIT_TIMEOUT


def _watch_receipt_from_files(
    request: WatchRequest,
    worker_pid: int,
    ready_path: Path,
    events_path: Path,
    terminal_path: Path,
) -> WatchReceipt:
    """Reload raw evidence; a stored outcome cannot supply its own exit proof."""
    try:
        if not isinstance(request, WatchRequest):
            return _incomplete_without_outcome(
                request,
                worker_pid if type(worker_pid) is int else 0,
                "watch request is invalid",
            )
        expected_paths = (
            Path(request.evidence_root) / _READY_NAME,
            Path(request.evidence_root) / _EVENTS_NAME,
            Path(request.evidence_root) / _TERMINAL_NAME,
        )
        supplied_paths = (Path(ready_path), Path(events_path), Path(terminal_path))
        if any(
            supplied.absolute() != expected.absolute()
            for supplied, expected in zip(supplied_paths, expected_paths, strict=True)
        ):
            return _incomplete_without_outcome(
                request,
                worker_pid if type(worker_pid) is int else 0,
                "evidence paths must be confined to the exact ready/events/terminal files",
            )

        outcome_bytes = _read_exact_regular_file(
            request.evidence_root / _OUTCOME_NAME,
            "watch outcome",
            close_failure_is_fatal=False,
        )
        outcome = watch_outcome_from_bytes(outcome_bytes)
        if outcome_bytes != watch_outcome_to_bytes(outcome):
            return _incomplete_without_outcome(
                request,
                worker_pid if type(worker_pid) is int else 0,
                "watch outcome canonical bytes mismatch",
            )
        if (
            type(worker_pid) is not int
            or outcome.request_id != request.request_id
            or outcome.request_sha256 != watch_request_sha256(request)
            or outcome.session_id != request.session_id
            or outcome.run_id != request.run_id
            or outcome.scenario is not request.scenario
            or outcome.worker_pid != worker_pid
        ):
            return _incomplete_without_outcome(
                request,
                worker_pid if type(worker_pid) is int else 0,
                "watch outcome binding mismatch",
            )
        observed_request = _load_request_path(request.evidence_root / _REQUEST_NAME)
        if observed_request != request:
            raise WatchProtocolError("watch request binding mismatch")
        claim = _load_controller_claim(request)
        launch = _load_worker_launch(request)
        return _receipt_from_outcome(
            _load_existing_outcome_after_worker_quiescence(
                request, claim, launch, candidate=outcome,
            )
        )
    except (WatchProtocolOwnershipError, ExactObjectOwnershipError):
        raise
    except FileNotFoundError:
        return _incomplete_without_outcome(
            request,
            worker_pid if type(worker_pid) is int else 0,
            "watch outcome missing",
        )
    except (OSError, ContainmentFormatError, WatchProtocolError, TypeError, ValueError, RuntimeError) as error:
        return _incomplete_without_outcome(
            request,
            worker_pid if type(worker_pid) is int else 0,
            f"watch outcome reconstruction failed: {error}",
        )


def _verified_process_handle(pid: int, creation_time: int) -> int:
    handle, observed_creation_time = _open_process_identity(pid)
    if observed_creation_time != creation_time:
        close_error = _close_handle(handle, f"mismatched worker process {pid}")
        if close_error is not None:
            raise _HandleOwnershipError(
                f"worker PID creation-time identity mismatch; {close_error}",
                handle,
                f"mismatched worker process {pid}",
            )
        raise WatchProtocolError("worker PID creation-time identity mismatch")
    return handle


def _verify_retained_process_handle(handle: int, pid: int, creation_time: int) -> None:
    observed_creation_time = _process_handle_creation_time(handle, pid)
    if observed_creation_time != creation_time:
        raise WatchProtocolError("worker PID creation-time identity mismatch")




















@dataclass(frozen=True)
class _CapturedWatchEvidence:
    ready: bool
    opened_root_kinds: tuple[str, ...]
    events: tuple[WatcherEvent, ...]
    journal: _JournalEvidence
    terminal_bytes_sha256: str
    root_identities_unchanged: bool
    completion_reasons: tuple[str, ...]


def _capture_worker_evidence(
    request: WatchRequest,
    launch: WorkerLaunch,
) -> _CapturedWatchEvidence:
    request_sha256 = watch_request_sha256(request)
    ready_path = request.evidence_root / _READY_NAME
    events_path = request.evidence_root / _EVENTS_NAME
    terminal_path = request.evidence_root / _TERMINAL_NAME
    journal = _read_exact_journal(events_path)
    events = _parse_events(journal.data, request)
    terminal_bytes = _read_exact_regular_file(terminal_path, "terminal record")
    terminal = _parse_terminal(
        terminal_bytes,
        request,
        launch.worker_pid,
        request_sha256,
        launch.worker_creation_time,
    )
    integrity_errors: list[str] = []
    if terminal["eventBytesSha256"] != journal.sha256:
        integrity_errors.append("terminal event hash mismatch")
    if terminal["eventByteCount"] != len(journal.data):
        integrity_errors.append("terminal event byte count mismatch")
    if terminal["eventCount"] != len(events):
        integrity_errors.append("terminal event count mismatch")
    final_sequence = events[-1].sequence if events else 0
    if terminal["finalSequence"] != final_sequence:
        integrity_errors.append("terminal final sequence mismatch")
    if (
        terminal["journalVolumeSerial"] != journal.volume_serial
        or terminal["journalFileId"] != journal.file_id
    ):
        integrity_errors.append("terminal journal identity mismatch")
    if integrity_errors:
        raise WatchProtocolError("; ".join(integrity_errors))

    completion_reasons: list[str] = []
    terminal_opened = tuple(terminal["openedRootKinds"])
    opened = terminal_opened
    ready_record_valid = False
    try:
        opened = _parse_ready(
            _read_exact_regular_file(ready_path, "ready record"),
            request,
            launch.worker_pid,
            request_sha256,
            launch.worker_creation_time,
        )
        ready_record_valid = True
    except _HandleOwnershipError as error:
        completion_reasons.append("evidence-handle-close-failed")
        _controller_owner(error, ready_path).resolve()
    except (OSError, WatchProtocolError):
        completion_reasons.append("worker-ready-invalid")

    if not terminal["complete"]:
        terminal_error = str(terminal["error"])
        completion_reasons.append(
            terminal_error
            if terminal_error == "controller-session-lost"
            else _worker_evidence_reason(WatchProtocolError(terminal_error))
        )
    if (
        not ready_record_valid
        or not terminal["ready"]
        or terminal_opened != opened
    ):
        completion_reasons.append("worker-ready-invalid")
    if terminal["openHandleCount"] != 0:
        completion_reasons.append("worker-handle-close-failed")
    if not terminal["rootIdentitiesUnchanged"]:
        completion_reasons.append("root-identity-changed")
    return _CapturedWatchEvidence(
        ready=(
            ready_record_valid
            and bool(terminal["ready"])
            and terminal_opened == opened
        ),
        opened_root_kinds=terminal_opened,
        events=events,
        journal=journal,
        terminal_bytes_sha256=hashlib.sha256(terminal_bytes).hexdigest(),
        root_identities_unchanged=bool(terminal["rootIdentitiesUnchanged"]),
        completion_reasons=tuple(dict.fromkeys(completion_reasons)),
    )


def _watch_outcome(
    request: WatchRequest,
    claim: ControllerClaim,
    launch: WorkerLaunch,
    *,
    completion: WatchEvidenceCompletion,
    worker_exit_code: int | None,
    reasons: tuple[str, ...],
    captured: _CapturedWatchEvidence | None,
) -> WatchOutcome:
    if captured is None:
        event_bytes = b""
        opened = ()
        events = ()
        journal = _JournalEvidence(0, 0, b"", 0, 0)
        terminal_sha256 = hashlib.sha256(b"").hexdigest()
        ready = False
        roots_unchanged = False
    else:
        event_bytes = captured.journal.data
        opened = captured.opened_root_kinds
        events = captured.events
        journal = captured.journal
        terminal_sha256 = captured.terminal_bytes_sha256
        ready = captured.ready
        roots_unchanged = captured.root_identities_unchanged
    return WatchOutcome(
        schema_version=1,
        request_id=request.request_id,
        request_sha256=watch_request_sha256(request),
        session_id=request.session_id,
        run_id=request.run_id,
        scenario=request.scenario,
        controller_pid=claim.controller_pid,
        controller_creation_time=claim.controller_creation_time,
        worker_pid=launch.worker_pid,
        worker_creation_time=launch.worker_creation_time,
        evidence_completion=completion,
        worker_exit_code=worker_exit_code,
        ready=ready,
        opened_root_kinds=opened,
        events=events,
        event_bytes_sha256=hashlib.sha256(event_bytes).hexdigest(),
        journal_volume_serial=journal.volume_serial,
        journal_file_id=journal.file_id,
        journal_byte_count=len(event_bytes),
        journal_event_count=len(events),
        journal_final_sequence=events[-1].sequence if events else 0,
        terminal_bytes_sha256=terminal_sha256,
        root_identities_unchanged=roots_unchanged,
        reason_codes=tuple(sorted(set(reasons), key=lambda item: (item.casefold(), item))),
    )


def _worker_evidence_reason(error: BaseException) -> str:
    message = str(error).casefold()
    if "root membership changed" in message:
        return "root-membership-changed"
    if "root identity" in message:
        return "root-identity-changed"
    if "ready" in message:
        return "worker-ready-invalid"
    if "terminal" in message and (
        isinstance(error, FileNotFoundError)
        or "no such file" in message
        or "missing" in message
    ):
        return "worker-terminal-missing"
    if "journal" in message or "events" in message:
        return "worker-journal-invalid"
    return "worker-evidence-invalid"


def _validate_outcome_binding(
    outcome: WatchOutcome,
    request: WatchRequest,
    claim: ControllerClaim,
    launch: WorkerLaunch,
) -> WatchOutcome:
    if (
        outcome.request_id != request.request_id
        or outcome.request_sha256 != watch_request_sha256(request)
        or outcome.session_id != request.session_id
        or outcome.run_id != request.run_id
        or outcome.scenario is not request.scenario
        or outcome.controller_pid != claim.controller_pid
        or outcome.controller_creation_time != claim.controller_creation_time
        or outcome.worker_pid != launch.worker_pid
        or outcome.worker_creation_time != launch.worker_creation_time
    ):
        raise WatchProtocolError("watch outcome binding mismatch")
    return outcome


def _validate_outcome_raw_evidence(
    outcome: WatchOutcome,
    request: WatchRequest,
    claim: ControllerClaim,
    launch: WorkerLaunch,
    captured: _CapturedWatchEvidence,
    *,
    observed_exit_code: int | None = None,
    current_reasons: tuple[str, ...] = (),
) -> WatchOutcome:
    """Check raw facts independently; only observed process state can prove success.

    Incomplete outcomes retain historical failure details, never success authority.
    A reaped process has no observable exit code: the summary cannot replace it.
    """
    validated = _validate_outcome_binding(outcome, request, claim, launch)
    reasons = list((*current_reasons, *captured.completion_reasons))
    loss_path = request.evidence_root / _CONTROLLER_LOSS_NAME
    try:
        loss_bytes = _read_exact_regular_file(loss_path, "controller loss")
    except FileNotFoundError:
        pass
    except _HandleOwnershipError as error:
        raise _controller_owner(error, loss_path)
    else:
        loss = controller_loss_from_bytes(loss_bytes, request, claim, launch)
        if loss_bytes != controller_loss_to_bytes(loss, request, claim, launch):
            raise WatchProtocolError("controller loss canonical bytes mismatch")
        reasons.append("controller-session-lost")
    try:
        proof = _durable_worker_exit(request, claim, launch, captured)
    except (WatchProtocolOwnershipError, ExactObjectOwnershipError):
        raise
    except (OSError, WatchProtocolError):
        reasons.append("causal-worker-exit-unproven")
    else:
        # Durable observation came from the original owner after native closes.
        # A later controller exit cannot retrospectively poison this observation.
        reasons = list((*captured.completion_reasons, *proof["reasonCodes"]))
        observed_exit_code = proof["workerExitCode"]
        if loss_path.exists():
            reasons.append("controller-session-lost")
    if observed_exit_code is None:
        reasons.append("worker-exit-unproven")
    elif observed_exit_code != 0:
        reasons.append("worker-exit-nonzero")
    derived = _watch_outcome(
        request,
        claim,
        launch,
        completion=(WatchEvidenceCompletion.INCOMPLETE if reasons
                    else WatchEvidenceCompletion.COMPLETED),
        worker_exit_code=observed_exit_code,
        reasons=tuple(reasons),
        captured=captured,
    )
    # Compare every raw field without using the candidate's verdict to derive one.
    raw_projection = replace(
        validated,
        evidence_completion=derived.evidence_completion,
        worker_exit_code=derived.worker_exit_code,
        reason_codes=derived.reason_codes,
    )
    if raw_projection != derived:
        raise WatchProtocolError(
            "published watch outcome differs from exact raw worker evidence"
        )
    if validated.evidence_completion is WatchEvidenceCompletion.COMPLETED:
        if validated != derived:
            raise WatchProtocolError(
                "published watch outcome cannot prove Completed: "
                + ("; ".join(derived.reason_codes) or "observed verdict differs")
            )
    elif any(reason not in validated.reason_codes for reason in captured.completion_reasons):
        raise WatchProtocolError("published watch outcome omits raw failure evidence")
    return validated


def _publish_outcome_commit(
    outcome: WatchOutcome,
    request: WatchRequest,
    claim: ControllerClaim,
    launch: WorkerLaunch,
) -> WatchOutcome:
    path = request.evidence_root / _OUTCOME_NAME
    data = watch_outcome_to_bytes(outcome)

    def parse(candidate: bytes) -> WatchOutcome:
        return _validate_outcome_binding(
            watch_outcome_from_bytes(candidate),
            request,
            claim,
            launch,
        )

    try:
        published = publish_new_pinned(path, data, parse)
    except ExactObjectOwnershipError as error:
        try:
            resolve_retained_ownership(error)
        except ExactObjectOwnershipError as unresolved:
            raise WatchProtocolOwnershipError(
                f"exact outcome publication retained unresolved ownership: {unresolved}",
                unresolved,
            ) from unresolved
        raise WatchProtocolError(
            "exact outcome publication cleanup completed after failure"
        ) from error
    except ExactObjectError as error:
        raise WatchProtocolError(f"exact outcome publication failed: {error}") from error
    if not isinstance(published, WatchOutcome):
        raise WatchProtocolError("published outcome validator returned an invalid value")
    return published


def _load_published_outcome(
    request: WatchRequest,
    claim: ControllerClaim,
    launch: WorkerLaunch,
) -> WatchOutcome:
    # No-replace publication exposes the name before its retained write/delete
    # handle closes. Never relax exact-reader sharing to consume that candidate.
    deadline = time.monotonic() + 1.0
    while True:
        try:
            data = _read_exact_regular_file(
                request.evidence_root / _OUTCOME_NAME, "watch outcome"
            )
            break
        except _HandleOwnershipError as error:
            raise _controller_owner(error, request.evidence_root / _OUTCOME_NAME)
        except OSError as error:
            error_code = getattr(error, "winerror", None)
            if error_code is None:
                error_code = error.errno
            if error_code != _ERROR_SHARING_VIOLATION or time.monotonic() >= deadline:
                raise
            time.sleep(0.02)
    outcome = watch_outcome_from_bytes(data)
    if data != watch_outcome_to_bytes(outcome):
        raise WatchProtocolError("watch outcome canonical bytes mismatch")
    return _validate_outcome_binding(outcome, request, claim, launch)


def _publish_or_load_outcome(
    outcome: WatchOutcome,
    request: WatchRequest,
    claim: ControllerClaim,
    launch: WorkerLaunch,
) -> WatchOutcome:
    try:
        return _publish_outcome_commit(outcome, request, claim, launch)
    except FileExistsError:
        existing = _load_published_outcome(request, claim, launch)
        if existing != outcome:
            raise WatchProtocolError(
                "existing watch outcome differs from controller-derived outcome"
            )
        return existing


def _receipt_from_outcome(outcome: WatchOutcome) -> WatchReceipt:
    outcome = watch_outcome_from_bytes(watch_outcome_to_bytes(outcome))
    complete = outcome.evidence_completion is WatchEvidenceCompletion.COMPLETED
    return WatchReceipt(
        request_id=outcome.request_id,
        session_id=outcome.session_id,
        run_id=outcome.run_id,
        scenario=outcome.scenario,
        worker_pid=outcome.worker_pid,
        evidence_completion=outcome.evidence_completion,
        ready=outcome.ready,
        opened_root_kinds=outcome.opened_root_kinds,
        events=outcome.events,
        event_bytes_sha256=outcome.event_bytes_sha256,
        worker_exit_code=outcome.worker_exit_code,
        watch_outcome_id=watch_outcome_id_for(outcome),
        request_bytes_sha256=outcome.request_sha256,
        error=None if complete else "; ".join(outcome.reason_codes),
    )


def _incomplete_without_outcome(
    request: WatchRequest | object,
    worker_pid: int,
    reason: str,
) -> WatchReceipt:
    request_sha256 = ""
    if isinstance(request, WatchRequest):
        try:
            request_sha256 = watch_request_sha256(request)
        except WatchProtocolError:
            pass
    return _make_incomplete_receipt(
        request,
        worker_pid,
        ready=False,
        opened_root_kinds=(),
        events=(),
        event_bytes_sha256=hashlib.sha256(b"").hexdigest(),
        error=reason,
        request_bytes_sha256=request_sha256,
    )


def _stop_local_session(
    request_path: Path,
    session: _LocalWatchSession,
    *,
    forced_reason: str | None = None,
    recovery: bool = False,
) -> WatchReceipt:
    with session.lock:
        request = session.request
        claim = session.claim
        launch = session.launch
        outcome_path = request.evidence_root / _OUTCOME_NAME
        retained_process_owner = getattr(
            session.process,
            "_modlab_close_owner",
            None,
        )
        if (isinstance(retained_process_owner, _PopenVerificationOwner)
                and not retained_process_owner.handle and session.finalized_outcome is None
                and session.poison_reasons):
            # A prior close failed after the original worker exit was observed.
            # Retry retained owners; never reopen the root/job or invent success.
            _close_session_resources(session)
            captured = None
            reasons = list(session.poison_reasons)
            try:
                captured = _capture_worker_evidence(request, launch)
                reasons.extend(captured.completion_reasons)
            except _HandleOwnershipError as error:
                raise _controller_owner(error, request.evidence_root)
            except (WatchProtocolOwnershipError, ExactObjectOwnershipError):
                raise
            except (OSError, WatchProtocolError) as error:
                reasons.append(_worker_evidence_reason(error))
            outcome = _watch_outcome(request, claim, launch,
                completion=WatchEvidenceCompletion.INCOMPLETE,
                worker_exit_code=session.observed_worker_exit_code,
                reasons=tuple(reasons), captured=captured)
            published = _publish_or_load_outcome(outcome, request, claim, launch)
            session.finalized_outcome = published
            with _LOCAL_SESSIONS_LOCK:
                _LOCAL_SESSIONS.pop(request_path.absolute(), None)
            return _receipt_from_outcome(published)
        if (
            outcome_path.exists()
            and isinstance(retained_process_owner, _PopenVerificationOwner)
            and not retained_process_owner.handle
        ):
            try:
                existing = _load_existing_outcome_after_worker_quiescence(
                    request,
                    claim,
                    launch,
                    locally_observed=session.finalized_outcome,
                )
                return _receipt_from_outcome(existing)
            except WatchProtocolOwnershipError as error:
                _resolve_watch_protocol_ownership(error)
                return _incomplete_without_outcome(request, launch.worker_pid, str(error))
            except (OSError, ContainmentFormatError, WatchProtocolError):
                return _incomplete_without_outcome(request, launch.worker_pid, "stored outcome failed raw reconstruction")
        try:
            observed_request = _load_request_path(request_path)
            observed_claim = _load_controller_claim(observed_request)
            observed_launch = _load_worker_launch(observed_request)
        except _HandleOwnershipError as error:
            if "same-controller-protocol-uncertain" not in session.poison_reasons:
                session.poison_reasons.append("same-controller-protocol-uncertain")
            raise _controller_owner(error, request.evidence_root)
        except (WatchProtocolOwnershipError, ExactObjectOwnershipError):
            if "same-controller-protocol-uncertain" not in session.poison_reasons:
                session.poison_reasons.append("same-controller-protocol-uncertain")
            raise
        except (OSError, WatchProtocolError):
            if "same-controller-protocol-uncertain" not in session.poison_reasons:
                session.poison_reasons.append("same-controller-protocol-uncertain")
        else:
            if (
                observed_request != request
                or session.request_sha256 != watch_request_sha256(observed_request)
                or observed_claim != claim
                or observed_launch != launch
            ):
                if (
                    "same-controller-session-binding-mismatch"
                    not in session.poison_reasons
                ):
                    session.poison_reasons.append(
                        "same-controller-session-binding-mismatch"
                    )
        reasons: list[str] = list(session.poison_reasons)
        if forced_reason is not None and forced_reason not in reasons:
            reasons.append(forced_reason)
        try:
            handle = _popen_process_handle(session.process)
            _verify_retained_process_handle(handle, session.worker_pid, session.worker_creation_time)
            before_stop = _kernel32.WaitForSingleObject(handle, 0)
            if before_stop != _WAIT_TIMEOUT:
                reasons.append("worker-exited-before-stop" if before_stop == _WAIT_OBJECT_0
                               else "worker-pre-stop-observation-failed")
        except (OSError, WatchProtocolError):
            reasons.append("worker-pre-stop-observation-failed")
        try:
            if recovery or forced_reason is not None:
                reasons.append("recovery-cleanup-stop")
            elif session.admission is None or session.quiescence is None or session.process_owner is None:
                reasons.append("process-admission-or-quiescence-missing")
            else:
                # Query the same retained owner again at the actual stop boundary.
                if not session.process_owner.resumed:
                    reasons.append("process-resume-unproven")
                observed = session.process_owner.observe()
                if observed.root_exit_code is None or observed.active_processes != 0:
                    reasons.append("process-tree-not-quiescent")
                if (observed.pid, observed.creation_time, observed.root_exit_code, observed.total_processes) != (
                    session.quiescence["processPid"], session.quiescence["processCreationTime"], session.quiescence["rootExitCode"], session.quiescence["totalProcesses"]):
                    reasons.append("process-quiescence-changed")
            _verify_records(session.vault, request)
            if reasons:
                cleanup_pid, cleanup_creation = _current_controller_identity()
                _publish_causal(request, claim, launch, "RecoveryCleanupStop", cleanupPid=cleanup_pid, cleanupCreationTime=cleanup_creation)
            else:
                admission, admission_hash = _read_causal(request, claim, launch, "LaunchAdmission")
                quiescence, quiescence_hash = _read_causal(request, claim, launch, "ProcessTreeQuiescence")
                if admission != session.admission or quiescence != session.quiescence:
                    raise WatchProtocolError("normal stop differs from retained process observations")
                _publish_causal(request, claim, launch, "NormalControllerStop", admissionSha256=admission_hash, quiescenceSha256=quiescence_hash)
        except FileExistsError:
            reasons.append("stop-token-already-exists")
        except WatchProtocolOwnershipError as error:
            _resolve_watch_protocol_ownership(error)
            reasons.append("stop-token-publication-failed")
        except (OSError, WatchProtocolError):
            reasons.append("stop-token-publication-failed")
        session.poison_reasons[:] = list(dict.fromkeys(reasons))
        process_handle = 0
        worker_signalled = False
        process_closed = False
        worker_exit_code: int | None = None
        captured: _CapturedWatchEvidence | None = None
        pending_ownership: BaseException | None = None
        try:
            process_handle = _popen_process_handle(session.process)
            _verify_retained_process_handle(
                process_handle,
                session.worker_pid,
                session.worker_creation_time,
            )
            wait_result = _kernel32.WaitForSingleObject(process_handle, 15_000)
            if wait_result != _WAIT_OBJECT_0:
                reasons.append(
                    "worker-wait-timeout"
                    if wait_result == _WAIT_TIMEOUT
                    else "worker-wait-failed"
                )
            else:
                worker_signalled = True
                worker_exit_code = _get_process_exit_code(process_handle)
                session.observed_worker_exit_code = worker_exit_code
                if worker_exit_code == _STILL_ACTIVE:
                    reasons.append("worker-still-active-after-signal")
                elif worker_exit_code != 0:
                    reasons.append("worker-exit-nonzero")
                _verify_retained_process_handle(
                    process_handle,
                    session.worker_pid,
                    session.worker_creation_time,
                )
                try:
                    captured = _capture_worker_evidence(request, launch)
                    reasons.extend(captured.completion_reasons)
                except _HandleOwnershipError as error:
                    reasons.append("evidence-handle-close-failed")
                    pending_ownership = _controller_owner(error, request.evidence_root)
                except (WatchProtocolOwnershipError, ExactObjectOwnershipError) as error:
                    pending_ownership = error
                except (OSError, WatchProtocolError) as error:
                    reasons.append(_worker_evidence_reason(error))
        except (WatchProtocolOwnershipError, ExactObjectOwnershipError) as error:
            pending_ownership = error
        except (OSError, WatchProtocolError):
            reasons.append("worker-process-observation-failed")
        except BaseException as error:
            pending_ownership = error
        if process_handle and worker_signalled:
            close_error = _close_popen_process_handle(session.process)
            if close_error is not None:
                reasons.append("controller-process-handle-close-failed")
            else:
                process_closed = True
        if pending_ownership is not None:
            reasons.append("evidence-handle-close-failed")
        # Close original observation ownership before publishing its durable proof.
        # Never infer suspension from resumed=False or terminate during cleanup.
        if worker_signalled and process_closed:
            try:
                _close_session_resources(session)
            except BaseException as cleanup:
                pending_ownership = cleanup if pending_ownership is None else _merge_controller_errors(pending_ownership, cleanup)
        if worker_signalled and process_closed and worker_exit_code is not None and captured is not None and pending_ownership is None:
            try:
                stop, stop_hash = _read_causal(request, claim, launch, "NormalControllerStop")
                _validate_stop_chain(request, claim, launch, stop)
                _publish_causal(request, claim, launch, "WorkerExitObservation",
                    stopSha256=stop_hash, terminalSha256=captured.terminal_bytes_sha256,
                    eventSha256=captured.journal.sha256, workerExitCode=worker_exit_code,
                    observationHandlesClosed=True, reasonCodes=list(dict.fromkeys(reasons)))
                _durable_worker_exit(request, claim, launch, captured)
            except (WatchProtocolOwnershipError, ExactObjectOwnershipError):
                raise
            except (OSError, WatchProtocolError):
                reasons.append("worker-exit-proof-unavailable")
        if worker_signalled and process_closed and session.process_owner is None and session.vault is None and session.runtime_guard is None:
            with _LOCAL_SESSIONS_LOCK:
                _LOCAL_SESSIONS.pop(request_path.absolute(), None)
        if pending_ownership is not None:
            if isinstance(pending_ownership, (WatchProtocolOwnershipError, ExactObjectOwnershipError)):
                pending_ownership.resolve()
            else:
                raise pending_ownership
        completion = (
            WatchEvidenceCompletion.COMPLETED
            if not reasons and worker_exit_code == 0 and captured is not None
            else WatchEvidenceCompletion.INCOMPLETE
        )
        if completion is WatchEvidenceCompletion.INCOMPLETE and not reasons:
            reasons.append("watch-completion-uncertain")
        outcome = _watch_outcome(
            request,
            claim,
            launch,
            completion=completion,
            worker_exit_code=worker_exit_code,
            reasons=tuple(reasons),
            captured=captured,
        )
        publication_error: BaseException | None = None
        try:
            if completion is WatchEvidenceCompletion.COMPLETED:
                _validate_outcome_raw_evidence(outcome, request, claim, launch, captured)
            published = _publish_or_load_outcome(outcome, request, claim, launch)
            session.finalized_outcome = outcome
            return _receipt_from_outcome(published)
        except WatchProtocolOwnershipError as error:
            _resolve_watch_protocol_ownership(error)
            publication_error = error
        except (OSError, ContainmentFormatError, WatchProtocolError) as error:
            publication_error = error
        if publication_error is not None:
            fallback = _watch_outcome(
                request,
                claim,
                launch,
                completion=WatchEvidenceCompletion.INCOMPLETE,
                worker_exit_code=worker_exit_code,
                reasons=tuple((*reasons, "outcome-publication-failed")),
                captured=captured,
            )
            try:
                published = _publish_or_load_outcome(fallback, request, claim, launch)
                session.finalized_outcome = fallback
                return _receipt_from_outcome(published)
            except WatchProtocolOwnershipError as fallback_error:
                _resolve_watch_protocol_ownership(fallback_error)
                return _incomplete_without_outcome(
                    request,
                    launch.worker_pid,
                    f"outcome publication failed: {publication_error}; "
                    f"incomplete fallback failed: {fallback_error}",
                )
            except (OSError, ContainmentFormatError, WatchProtocolError) as fallback_error:
                return _incomplete_without_outcome(
                    request,
                    launch.worker_pid,
                    f"outcome publication failed: {publication_error}; "
                    f"incomplete fallback failed: {fallback_error}",
                )


def _exact_process_status(
    pid: int,
    creation_time: int,
) -> tuple[str, int, str | None]:
    try:
        handle, observed_creation = _open_process_identity(pid)
    except _HandleOwnershipError as error:
        detail = str(error)
        _controller_owner(error, Path(f"process {pid}")).resolve()
        return "uncertain", 0, detail
    except OSError as error:
        error_code = getattr(error, "winerror", None)
        if error_code is None:
            error_code = error.errno
        if error_code == _ERROR_INVALID_PARAMETER:
            return "dead", 0, None
        return "uncertain", 0, str(error)
    except WatchProtocolError as error:
        return "uncertain", 0, str(error)
    if observed_creation != creation_time:
        _close_controller_handle(handle, f"mismatched process {pid}", Path(f"process {pid}"))
        return "uncertain", 0, "process creation-time mismatch"
    try:
        wait_result = _kernel32.WaitForSingleObject(handle, 0)
    except BaseException as error:
        try:
            _close_controller_handle(handle, f"uncertain process {pid}", Path(f"process {pid}"))
        except BaseException as cleanup_error:
            raise _merge_controller_errors(error, cleanup_error)
        raise
    if wait_result == _WAIT_TIMEOUT:
        return "live", handle, None
    if wait_result == _WAIT_OBJECT_0:
        return "dead", handle, None
    _close_controller_handle(handle, f"uncertain process {pid}", Path(f"process {pid}"))
    detail = f"process liveness wait failed: {wait_result}"
    return "uncertain", 0, detail


def _load_existing_outcome_after_worker_quiescence(
    request: WatchRequest,
    claim: ControllerClaim,
    launch: WorkerLaunch,
    *,
    candidate: WatchOutcome | None = None,
    locally_observed: WatchOutcome | None = None,
) -> WatchOutcome:
    try:
        proof_path = request.evidence_root / CAUSAL_NAMES["WorkerExitObservation"]
        if proof_path.exists():
            captured = _capture_worker_evidence(request, launch)
            _durable_worker_exit(request, claim, launch, captured)
            outcome = candidate if candidate is not None else _load_published_outcome(request, claim, launch)
            return _validate_outcome_raw_evidence(outcome, request, claim, launch, captured)
    except _HandleOwnershipError as error:
        raise _controller_owner(error, request.evidence_root)
    except (WatchProtocolOwnershipError, ExactObjectOwnershipError):
        raise
    # Only an already-held original session may retain its own exit observation.
    # Detached readers must observe the process themselves; disk cannot supply it.
    current_reasons: tuple[str, ...] = ()
    controller_status, controller_handle, _ = _exact_process_status(
        claim.controller_pid, claim.controller_creation_time,
    )
    if controller_handle:
        close_error = _close_controller_handle(
            controller_handle, f"existing-outcome controller {claim.controller_pid}",
            request.evidence_root,
        )
        if close_error is not None:
            raise WatchProtocolError(close_error)
    if controller_status != "live":
        current_reasons = ("controller-session-lost" if controller_status == "dead"
                           else "controller-identity-uncertain",)
    if locally_observed is not None:
        _validate_outcome_binding(locally_observed, request, claim, launch)
        current_reasons += locally_observed.reason_codes
    status, handle, detail = _exact_process_status(
        launch.worker_pid,
        launch.worker_creation_time,
    )
    if status != "dead":
        if handle:
            close_error = _close_controller_handle(
                handle,
                f"existing-outcome worker {launch.worker_pid}",
                request.evidence_root,
            )
            if close_error is not None:
                raise WatchProtocolError(close_error)
        raise WatchProtocolError(
            f"existing watch outcome worker is not exactly quiescent: {detail or status}"
        )
    observed_exit = None if locally_observed is None else locally_observed.worker_exit_code
    pending_error: BaseException | None = None
    try:
        if handle:
            _verify_retained_process_handle(
                handle,
                launch.worker_pid,
                launch.worker_creation_time,
            )
            observed_exit = _get_process_exit_code(handle)
            if observed_exit == _STILL_ACTIVE:
                raise WatchProtocolError(
                    "existing watch outcome worker is still active"
                )
        captured = _capture_worker_evidence(request, launch)
        outcome = candidate if candidate is not None else _load_published_outcome(request, claim, launch)
        if locally_observed is not None and outcome != locally_observed:
            raise WatchProtocolError("published outcome differs from original controller observation")
        _validate_outcome_raw_evidence(
            outcome,
            request,
            claim,
            launch,
            captured,
            observed_exit_code=observed_exit,
            current_reasons=current_reasons,
        )
        if (
            observed_exit is not None
            and outcome.worker_exit_code is not None
            and outcome.worker_exit_code != observed_exit
        ):
            raise WatchProtocolError(
                "published watch outcome worker exit code differs"
            )
        return outcome
    except _HandleOwnershipError as error:
        pending_error = _controller_owner(error, request.evidence_root)
        raise pending_error from error
    except BaseException as error:
        pending_error = error
        raise
    finally:
        if handle:
            try:
                close_error = _close_controller_handle(
                    handle,
                    f"existing-outcome worker {launch.worker_pid}",
                    request.evidence_root,
                )
            except BaseException as cleanup_error:
                if pending_error is None:
                    raise
                raise _merge_controller_errors(pending_error, cleanup_error)
            if close_error is not None:
                close_failure = WatchProtocolError(close_error)
                if pending_error is None:
                    raise close_failure
                raise _merge_controller_errors(pending_error, close_failure)


def _stop_non_owner(
    request: WatchRequest,
    claim: ControllerClaim,
    launch: WorkerLaunch,
) -> WatchReceipt:
    outcome_path = request.evidence_root / _OUTCOME_NAME
    if outcome_path.exists() and (request.evidence_root / CAUSAL_NAMES["WorkerExitObservation"]).exists():
        return _receipt_from_outcome(_load_existing_outcome_after_worker_quiescence(request, claim, launch))
    controller_status, controller_handle, controller_error = _exact_process_status(
        claim.controller_pid,
        claim.controller_creation_time,
    )
    if controller_handle:
        close_error = _close_controller_handle(
            controller_handle,
            f"claimed controller process {claim.controller_pid}",
            request.evidence_root,
        )
        if close_error is not None:
            return _incomplete_without_outcome(request, launch.worker_pid, close_error)
    if controller_status == "live":
        if outcome_path.exists() and claim.controller_pid == os.getpid():
            try:
                current_pid, current_creation_time = _current_controller_identity()
                if (
                    current_pid == claim.controller_pid
                    and current_creation_time == claim.controller_creation_time
                ):
                    return _receipt_from_outcome(
                        _load_existing_outcome_after_worker_quiescence(
                            request,
                            claim,
                            launch,
                        )
                    )
            except WatchProtocolOwnershipError:
                raise
            except (OSError, ContainmentFormatError, WatchProtocolError) as error:
                return _incomplete_without_outcome(
                    request,
                    launch.worker_pid,
                    f"existing outcome raw validation failed: {error}",
                )
        return _incomplete_without_outcome(
            request,
            launch.worker_pid,
            "claimed controller is live; non-owner stop refused",
        )
    if controller_status != "dead":
        return _incomplete_without_outcome(
            request,
            launch.worker_pid,
            f"claimed controller liveness is uncertain: {controller_error}",
        )
    reasons = ["controller-session-lost"]
    worker_exit_code: int | None = None
    worker_quiescent = False
    captured: _CapturedWatchEvidence | None = None
    worker_status, worker_handle, worker_error = _exact_process_status(
        launch.worker_pid,
        launch.worker_creation_time,
    )
    pending_error: BaseException | None = None
    try:
        if worker_status in {"live", "dead"}:
            try:
                cleanup_pid, cleanup_creation = _current_controller_identity()
                _publish_causal(request, claim, launch, "RecoveryCleanupStop", cleanupPid=cleanup_pid, cleanupCreationTime=cleanup_creation)
            except FileExistsError:
                pass
            except WatchProtocolOwnershipError as error:
                _resolve_watch_protocol_ownership(error)
                reasons.append("worker-cleanup-refused")
            except (OSError, WatchProtocolError):
                reasons.append("worker-cleanup-refused")
        if worker_status == "uncertain":
            reasons.append("worker-identity-uncertain")
        elif worker_status == "live" and worker_handle:
            wait_result = _kernel32.WaitForSingleObject(worker_handle, 15_000)
            if wait_result == _WAIT_OBJECT_0:
                try:
                    _verify_retained_process_handle(
                        worker_handle,
                        launch.worker_pid,
                        launch.worker_creation_time,
                    )
                    worker_quiescent = True
                    worker_exit_code = _get_process_exit_code(worker_handle)
                    if worker_exit_code == _STILL_ACTIVE:
                        reasons.append("worker-cleanup-refused")
                        worker_quiescent = False
                except (OSError, WatchProtocolError):
                    reasons.append("worker-cleanup-refused")
            else:
                reasons.append("worker-cleanup-refused")
        elif worker_status == "dead":
            worker_quiescent = True
            if worker_handle:
                try:
                    worker_exit_code = _get_process_exit_code(worker_handle)
                except OSError:
                    reasons.append("worker-cleanup-refused")
    except BaseException as error:
        pending_error = _controller_owner(error, request.evidence_root)
    if worker_handle:
        try:
            close_error = _close_controller_handle(
                worker_handle, f"cleanup worker process {launch.worker_pid}", request.evidence_root,
            )
            if close_error is not None:
                reasons.append("worker-cleanup-refused")
        except BaseException as cleanup_error:
            pending_error = (
                _merge_controller_errors(pending_error, cleanup_error)
                if pending_error is not None else cleanup_error
            )
    if pending_error is not None:
        raise pending_error
    if worker_error is not None and "worker-identity-uncertain" in reasons:
        reasons.append("worker-cleanup-refused")
    if worker_quiescent:
        try:
            captured = _capture_worker_evidence(request, launch)
            reasons.extend(captured.completion_reasons)
        except _HandleOwnershipError as error:
            reasons.append("evidence-handle-close-failed")
            _controller_owner(error, request.evidence_root).resolve()
        except (WatchProtocolOwnershipError, ExactObjectOwnershipError):
            raise
        except (OSError, WatchProtocolError) as error:
            reasons.append(_worker_evidence_reason(error))
    if outcome_path.exists():
        if not worker_quiescent or captured is None:
            return _incomplete_without_outcome(
                request,
                launch.worker_pid,
                "existing outcome cannot be trusted before exact worker quiescence and raw capture",
            )
        outcome = _load_published_outcome(request, claim, launch)
        _validate_outcome_raw_evidence(
            outcome,
            request,
            claim,
            launch,
            captured,
            observed_exit_code=worker_exit_code,
            current_reasons=tuple(reasons),
        )
        if (
            worker_exit_code is not None
            and outcome.worker_exit_code is not None
            and outcome.worker_exit_code != worker_exit_code
        ):
            raise WatchProtocolError(
                "published watch outcome worker exit code differs"
            )
    else:
        expected = _watch_outcome(
            request,
            claim,
            launch,
            completion=WatchEvidenceCompletion.INCOMPLETE,
            worker_exit_code=worker_exit_code,
            reasons=tuple(reasons),
            captured=captured,
        )
        outcome = _publish_or_load_outcome(
            expected,
            request,
            claim,
            launch,
        )
        if captured is not None:
            _validate_outcome_raw_evidence(
                outcome,
                request,
                claim,
                launch,
                captured,
                observed_exit_code=worker_exit_code,
                current_reasons=tuple(reasons),
            )
    receipt = _receipt_from_outcome(outcome)
    current_cleanup_blockers = tuple(
        reason for reason in reasons if reason not in outcome.reason_codes
    )
    if current_cleanup_blockers:
        return _force_incomplete(
            receipt,
            "current cleanup: " + "; ".join(current_cleanup_blockers),
        )
    return receipt


def _close_session_resources(session):
    pending = None
    if session.process_owner is not None:
        try:
            session.process_owner.close()
            session.process_owner = None
        except BaseException as error:
            error.owner = session.process_owner
            pending = error
    for pin in session.record_pins or []:
        try:
            pin.close()
        except BaseException as error:
            cleanup = ExactObjectOwnershipError(str(error), verification=(pin,))
            pending = cleanup if pending is None else _merge_controller_errors(pending, cleanup)
    for attribute in ("runtime_guard", "vault"):
        guard = getattr(session, attribute)
        if guard is not None:
            try:
                guard.verify()
                guard.close()
                setattr(session, attribute, None)
            except BaseException as cleanup:
                pending = cleanup if pending is None else _merge_controller_errors(pending, cleanup)
    if pending is not None:
        if session.process_owner is not None:
            pending.owner = session.process_owner
        if "session-resource-close-failed" not in session.poison_reasons:
            session.poison_reasons.append("session-resource-close-failed")
        raise pending


def stop_watch(request_path: Path, *, recovery: bool = False) -> WatchReceipt:
    request = None
    try:
        request = _load_request_path(request_path)
        with _guard_request(request) as vault:
            _verify_records(vault, request)
            result = _stop_watch(request_path, recovery=recovery)
            _verify_records(vault, request, include_outcome=True)
            return result
    except _HandleOwnershipError as error:
        raise _controller_owner(error, Path(request_path).parent if request is None else request.evidence_root)
    except (WatchProtocolOwnershipError, ExactObjectOwnershipError):
        raise
    except (OSError, WatchProtocolError) as error:
        if getattr(error, "owner", None) is not None:
            raise
        return _incomplete_without_outcome(request or object(), 0, f"protected watch stop failed: {error}")


def watch_receipt_from_files(request, worker_pid, ready_path, events_path, terminal_path):
    try:
        with _guard_request(request) as vault:
            _verify_records(vault, request)
            result = _watch_receipt_from_files(request, worker_pid, ready_path, events_path, terminal_path)
            _verify_records(vault, request, include_outcome=True)
            return result
    except _HandleOwnershipError as error:
        raise _controller_owner(error, request.evidence_root)
    except (WatchProtocolOwnershipError, ExactObjectOwnershipError):
        raise
    except (OSError, WatchProtocolError, TypeError, ValueError, RuntimeError) as error:
        return _incomplete_without_outcome(request, worker_pid, f"protected watch reconstruction failed: {error}")


def _stop_watch(request_path: Path, *, recovery: bool = False) -> WatchReceipt:
    """Complete locally owned watches; external callers are cleanup-only."""
    _require_windows()
    supplied = Path(request_path).absolute()
    with _LOCAL_SESSIONS_LOCK:
        session = _LOCAL_SESSIONS.get(supplied)
    if session is not None:
        return _stop_local_session(supplied, session, recovery=recovery)
    try:
        request = _load_request_path(supplied)
        claim = _load_controller_claim(request)
        launch = _load_worker_launch(request)
    except WatchProtocolOwnershipError as error:
        _resolve_watch_protocol_ownership(error)
        return _incomplete_without_outcome(
            object(),
            0,
            f"watch protocol is unavailable: {error}",
        )
    except (OSError, WatchProtocolError) as error:
        return _incomplete_without_outcome(
            object(),
            0,
            f"watch protocol is unavailable: {error}",
        )
    try:
        return _stop_non_owner(request, claim, launch)
    except WatchProtocolOwnershipError as error:
        _resolve_watch_protocol_ownership(error)
        return _incomplete_without_outcome(
            request,
            launch.worker_pid,
            f"non-owner cleanup failed: {error}",
        )
    except (OSError, ContainmentFormatError, WatchProtocolError) as error:
        return _incomplete_without_outcome(
            request,
            launch.worker_pid,
            f"non-owner cleanup failed: {error}",
        )


def _force_incomplete(receipt: WatchReceipt, error: str) -> WatchReceipt:
    combined = error if receipt.error is None else f"{error}; {receipt.error}"
    return WatchReceipt(
        request_id=receipt.request_id,
        session_id=receipt.session_id,
        run_id=receipt.run_id,
        scenario=receipt.scenario,
        worker_pid=receipt.worker_pid,
        evidence_completion=WatchEvidenceCompletion.INCOMPLETE,
        ready=receipt.ready,
        opened_root_kinds=receipt.opened_root_kinds,
        events=receipt.events,
        event_bytes_sha256=receipt.event_bytes_sha256,
        worker_exit_code=receipt.worker_exit_code,
        watch_outcome_id=receipt.watch_outcome_id,
        error=combined,
        request_bytes_sha256=receipt.request_bytes_sha256,
    )


def watch_proves_unchanged(
    receipt: WatchReceipt,
    before_manifest: ProtectedState,
    after_manifest: ProtectedState,
) -> bool:
    """Prove no observed mutations without deciding the scenario verdict."""
    return (
        type(receipt) is WatchReceipt
        and receipt.evidence_completion is WatchEvidenceCompletion.COMPLETED
        and receipt.ready
        and receipt.error is None
        and receipt.opened_root_kinds == ROOT_KINDS
        and receipt.worker_exit_code == 0
        and type(receipt.watch_outcome_id) is str
        and re.fullmatch(
            r"watch-outcome-sha256:[0-9a-f]{64}",
            receipt.watch_outcome_id,
        )
        is not None
        and type(receipt.request_bytes_sha256) is str
        and re.fullmatch(r"[0-9a-f]{64}", receipt.request_bytes_sha256) is not None
        and receipt.events == ()
        and receipt.event_bytes_sha256 == hashlib.sha256(b"").hexdigest()
        and _complete_protected_state(before_manifest)
        and _complete_protected_state(after_manifest)
        and before_manifest == after_manifest
    )


def _complete_protected_state(value: object) -> bool:
    if type(value) is not ProtectedState:
        return False
    trees = (
        value.source_mods,
        value.downloads,
        value.overwrite,
        value.bounded_game,
    )
    if not all(_complete_tree_identity(tree) for tree in trees):
        return False
    return all(
        type(digest) is str and re.fullmatch(r"[0-9a-f]{64}", digest) is not None
        for digest in (value.lab_profile_sha256, value.play_profile_sha256)
    )


def _complete_tree_identity(value: object) -> bool:
    return (
        type(value) is TreeIdentity
        and type(value.sha256) is str
        and re.fullmatch(r"[0-9a-f]{64}", value.sha256) is not None
        and type(value.regular_file_count) is int
        and value.regular_file_count >= 0
        and type(value.directory_count) is int
        and value.directory_count >= 0
        and type(value.total_size) is int
        and value.total_size >= 0
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m modlab.validation.windows_watch")
    parser.add_argument("--worker", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return run_watch_worker(arguments.worker)
    except WatchProtocolOwnershipError as error:
        _resolve_watch_protocol_ownership(error)
        return 2
    except (OSError, WatchProtocolError):
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
