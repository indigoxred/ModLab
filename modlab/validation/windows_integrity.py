"""Windows Mandatory Integrity Control helpers for containment validation."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from enum import IntEnum
import hashlib
import os
from pathlib import Path
import stat
import subprocess
from typing import Mapping

from modlab.validation.mo2_containment_model import IntegrityObservation


class IntegrityLevel(IntEnum):
    UNTRUSTED = 0x0000
    LOW = 0x1000
    MEDIUM = 0x2000
    HIGH = 0x3000
    SYSTEM = 0x4000


@dataclass(frozen=True)
class ProcessLaunch:
    pid: int
    executable: str
    arguments: tuple[str, ...]
    working_directory: str
    integrity: IntegrityLevel


@dataclass(frozen=True)
class IntegrityLabelApplication:
    executable: str
    arguments: tuple[str, ...]
    exit_code: int
    stdout_sha256: str
    stderr_sha256: str
    integrity: IntegrityLevel


class IntegrityLabelError(RuntimeError):
    def __init__(self, message: str, receipt: IntegrityLabelApplication) -> None:
        super().__init__(message)
        self.receipt = receipt


def source_integrity_allowed(level: IntegrityLevel) -> bool:
    return isinstance(level, IntegrityLevel) and level in {
        IntegrityLevel.MEDIUM,
        IntegrityLevel.HIGH,
        IntegrityLevel.SYSTEM,
    }


def stage_integrity_allowed(level: IntegrityLevel) -> bool:
    return level is IntegrityLevel.LOW


def to_integrity_observation(level: IntegrityLevel | None) -> IntegrityObservation:
    if level is None:
        return IntegrityObservation.UNKNOWN
    if not isinstance(level, IntegrityLevel):
        raise ValueError(f"unknown native integrity level: {level!r}")
    conversions = {
        IntegrityLevel.UNTRUSTED: IntegrityObservation.UNTRUSTED,
        IntegrityLevel.LOW: IntegrityObservation.LOW,
        IntegrityLevel.MEDIUM: IntegrityObservation.MEDIUM,
        IntegrityLevel.HIGH: IntegrityObservation.HIGH,
        IntegrityLevel.SYSTEM: IntegrityObservation.SYSTEM,
    }
    return conversions[level]


integrity_observation_for = to_integrity_observation


if os.name == "nt":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    _SE_FILE_OBJECT = 1
    _LABEL_SECURITY_INFORMATION = 0x00000010
    _SYSTEM_MANDATORY_LABEL_ACE_TYPE = 0x11
    _SYSTEM_MANDATORY_LABEL_NO_WRITE_UP = 0x00000001
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _TOKEN_QUERY = 0x0008
    _TOKEN_ADJUST_DEFAULT = 0x0080
    _TOKEN_INTEGRITY_LEVEL = 25
    _TOKEN_MANDATORY_POLICY = 27
    _TOKEN_MANDATORY_POLICY_NO_WRITE_UP = 0x00000001
    _SE_GROUP_INTEGRITY = 0x00000020
    _CREATE_SUSPENDED = 0x00000004
    _CREATE_UNICODE_ENVIRONMENT = 0x00000400
    _ERROR_INSUFFICIENT_BUFFER = 122
    _WAIT_OBJECT_0 = 0x00000000
    _WAIT_TIMEOUT_MS = 5000

    class _ACL(ctypes.Structure):
        _fields_ = [
            ("AclRevision", wintypes.BYTE),
            ("Sbz1", wintypes.BYTE),
            ("AclSize", wintypes.WORD),
            ("AceCount", wintypes.WORD),
            ("Sbz2", wintypes.WORD),
        ]

    class _ACE_HEADER(ctypes.Structure):
        _fields_ = [
            ("AceType", wintypes.BYTE),
            ("AceFlags", wintypes.BYTE),
            ("AceSize", wintypes.WORD),
        ]

    class _SYSTEM_MANDATORY_LABEL_ACE(ctypes.Structure):
        _fields_ = [
            ("Header", _ACE_HEADER),
            ("Mask", wintypes.DWORD),
            ("SidStart", wintypes.DWORD),
        ]

    class _SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class _TOKEN_MANDATORY_LABEL(ctypes.Structure):
        _fields_ = [("Label", _SID_AND_ATTRIBUTES)]

    class _TOKEN_MANDATORY_POLICY_INFO(ctypes.Structure):
        _fields_ = [("Policy", wintypes.DWORD)]

    class _SID_IDENTIFIER_AUTHORITY(ctypes.Structure):
        _fields_ = [("Value", wintypes.BYTE * 6)]

    class _STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class _PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(_STARTUPINFOW),
        ctypes.POINTER(_PROCESS_INFORMATION),
    ]
    _kernel32.CreateProcessW.restype = wintypes.BOOL
    _kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    _kernel32.ResumeThread.restype = wintypes.DWORD
    _kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateProcess.restype = wintypes.BOOL
    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD

    _advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    _advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    _advapi32.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    _advapi32.GetAce.restype = wintypes.BOOL
    _advapi32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
    _advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(wintypes.BYTE)
    _advapi32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    _advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
    _advapi32.GetLengthSid.argtypes = [ctypes.c_void_p]
    _advapi32.GetLengthSid.restype = wintypes.DWORD
    _advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    _advapi32.OpenProcessToken.restype = wintypes.BOOL
    _advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _advapi32.GetTokenInformation.restype = wintypes.BOOL
    _advapi32.AllocateAndInitializeSid.argtypes = [
        ctypes.POINTER(_SID_IDENTIFIER_AUTHORITY),
        wintypes.BYTE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    _advapi32.AllocateAndInitializeSid.restype = wintypes.BOOL
    _advapi32.FreeSid.argtypes = [ctypes.c_void_p]
    _advapi32.FreeSid.restype = ctypes.c_void_p
    _advapi32.SetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _advapi32.SetTokenInformation.restype = wintypes.BOOL


def _require_windows() -> None:
    if os.name != "nt":
        raise OSError("Windows integrity APIs are unavailable")


def _winerror(message: str, code: int | None = None) -> OSError:
    if code is None:
        code = ctypes.get_last_error()
    return OSError(code, f"{message}: {ctypes.FormatError(code)}")


def _close_handle(handle: int | None) -> None:
    if handle:
        _kernel32.CloseHandle(handle)


def _integrity_from_rid(rid: int) -> IntegrityLevel:
    if rid < IntegrityLevel.LOW:
        return IntegrityLevel.UNTRUSTED
    if rid < IntegrityLevel.MEDIUM:
        return IntegrityLevel.LOW
    if rid < IntegrityLevel.HIGH:
        return IntegrityLevel.MEDIUM
    if rid < IntegrityLevel.SYSTEM:
        return IntegrityLevel.HIGH
    if rid == IntegrityLevel.SYSTEM:
        return IntegrityLevel.SYSTEM
    raise OSError(f"unrecognized mandatory integrity RID: 0x{rid:04x}")


def _integrity_from_sid(sid: int) -> IntegrityLevel:
    count_pointer = _advapi32.GetSidSubAuthorityCount(sid)
    if not count_pointer or count_pointer[0] == 0:
        raise _winerror("GetSidSubAuthorityCount failed")
    rid_pointer = _advapi32.GetSidSubAuthority(sid, count_pointer[0] - 1)
    if not rid_pointer:
        raise _winerror("GetSidSubAuthority failed")
    return _integrity_from_rid(rid_pointer[0])


def _integrity_from_label_ace(ace: int) -> IntegrityLevel:
    label = ctypes.cast(ace, ctypes.POINTER(_SYSTEM_MANDATORY_LABEL_ACE)).contents
    if label.Header.AceSize < ctypes.sizeof(_SYSTEM_MANDATORY_LABEL_ACE):
        raise OSError("mandatory label ACE is truncated")
    if not label.Mask & _SYSTEM_MANDATORY_LABEL_NO_WRITE_UP:
        raise OSError("explicit mandatory label does not enforce no-write-up")
    sid_address = ace + _SYSTEM_MANDATORY_LABEL_ACE.SidStart.offset
    return _integrity_from_sid(sid_address)


def inspect_path_integrity(path: Path) -> IntegrityLevel:
    _require_windows()
    target = Path(path)
    if not target.is_absolute():
        raise ValueError("integrity inspection requires an absolute path")

    sacl = ctypes.c_void_p()
    security_descriptor = ctypes.c_void_p()
    result = _advapi32.GetNamedSecurityInfoW(
        str(target),
        _SE_FILE_OBJECT,
        _LABEL_SECURITY_INFORMATION,
        None,
        None,
        None,
        ctypes.byref(sacl),
        ctypes.byref(security_descriptor),
    )
    if result:
        raise _winerror(f"GetNamedSecurityInfoW failed for {target}", result)
    try:
        if not sacl:
            return IntegrityLevel.MEDIUM
        acl = ctypes.cast(sacl, ctypes.POINTER(_ACL)).contents
        observed: list[IntegrityLevel] = []
        for index in range(acl.AceCount):
            ace = ctypes.c_void_p()
            if not _advapi32.GetAce(sacl, index, ctypes.byref(ace)):
                raise _winerror(f"GetAce failed for {target}")
            header = ctypes.cast(ace, ctypes.POINTER(_ACE_HEADER)).contents
            if header.AceType == _SYSTEM_MANDATORY_LABEL_ACE_TYPE:
                observed.append(_integrity_from_label_ace(ace.value))
        if not observed:
            return IntegrityLevel.MEDIUM
        if len(observed) != 1:
            raise OSError(f"multiple mandatory integrity labels observed for {target}")
        return observed[0]
    finally:
        if security_descriptor:
            _kernel32.LocalFree(security_descriptor)


def inspect_process_integrity(pid: int) -> IntegrityLevel:
    _require_windows()
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ValueError("pid must be a positive integer")

    process = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not process:
        raise _winerror(f"OpenProcess failed for pid {pid}")
    token = wintypes.HANDLE()
    try:
        if not _advapi32.OpenProcessToken(process, _TOKEN_QUERY, ctypes.byref(token)):
            raise _winerror(f"OpenProcessToken failed for pid {pid}")
        needed = wintypes.DWORD()
        ctypes.set_last_error(0)
        if _advapi32.GetTokenInformation(token, _TOKEN_INTEGRITY_LEVEL, None, 0, ctypes.byref(needed)):
            raise OSError("GetTokenInformation unexpectedly accepted an empty buffer")
        if ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER or needed.value == 0:
            raise _winerror(f"GetTokenInformation sizing failed for pid {pid}")
        buffer = ctypes.create_string_buffer(needed.value)
        if not _advapi32.GetTokenInformation(
            token, _TOKEN_INTEGRITY_LEVEL, buffer, needed, ctypes.byref(needed)
        ):
            raise _winerror(f"GetTokenInformation failed for pid {pid}")
        label = ctypes.cast(buffer, ctypes.POINTER(_TOKEN_MANDATORY_LABEL)).contents
        if not label.Label.Sid:
            raise OSError(f"process {pid} returned an empty integrity SID")
        mandatory_policy = _token_mandatory_policy(token)
        if not mandatory_policy & _TOKEN_MANDATORY_POLICY_NO_WRITE_UP:
            raise OSError(
                f"process {pid} mandatory policy does not enforce no-write-up"
            )
        return _integrity_from_sid(label.Label.Sid)
    finally:
        _close_handle(token.value)
        _close_handle(process)


def _token_mandatory_policy(token: int) -> int:
    policy = _TOKEN_MANDATORY_POLICY_INFO()
    returned = wintypes.DWORD()
    if not _advapi32.GetTokenInformation(
        token,
        _TOKEN_MANDATORY_POLICY,
        ctypes.byref(policy),
        ctypes.sizeof(policy),
        ctypes.byref(returned),
    ):
        raise _winerror("GetTokenInformation(TokenMandatoryPolicy) failed")
    if returned.value != ctypes.sizeof(policy):
        raise OSError(
            "GetTokenInformation(TokenMandatoryPolicy) returned an unexpected size: "
            f"{returned.value}"
        )
    return policy.Policy


def _is_reparse(path: Path) -> bool:
    metadata = path.lstat()
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_attribute) or stat.S_ISLNK(metadata.st_mode)


def _direct_tree_entries(path: Path) -> tuple[Path, ...]:
    target = Path(path)
    if not target.is_absolute():
        raise ValueError("integrity labeling requires an absolute path")
    if _is_reparse(target):
        raise ValueError(f"integrity labeling rejects reparse root: {target}")
    if not target.is_dir():
        raise ValueError(f"integrity labeling requires a direct directory: {target}")

    entries = [target]
    for current, directories, files in os.walk(target, topdown=True, followlinks=False):
        for name in (*directories, *files):
            entry = Path(current, name)
            if _is_reparse(entry):
                raise ValueError(f"integrity labeling rejects reparse descendant: {entry}")
            entries.append(entry)
    return tuple(entries)


def _set_integrity_tree(path: Path, level: IntegrityLevel) -> IntegrityLabelApplication:
    _require_windows()
    target = Path(path)
    _direct_tree_entries(target)
    level_name = {IntegrityLevel.LOW: "L", IntegrityLevel.MEDIUM: "M"}[level]
    executable = r"C:\Windows\System32\icacls.exe"
    arguments = (
        str(target),
        "/setintegritylevel",
        f"(OI)(CI){level_name}",
        "/T",
        "/C",
        "/Q",
    )
    completed = subprocess.run(
        (executable, *arguments),
        shell=False,
        check=False,
        capture_output=True,
    )
    receipt = IntegrityLabelApplication(
        executable=executable,
        arguments=arguments,
        exit_code=completed.returncode,
        stdout_sha256=hashlib.sha256(completed.stdout).hexdigest(),
        stderr_sha256=hashlib.sha256(completed.stderr).hexdigest(),
        integrity=level,
    )
    if completed.returncode != 0:
        raise IntegrityLabelError(
            f"icacls failed with exit code {completed.returncode} for {target}", receipt
        )

    try:
        entries = _direct_tree_entries(target)
        mismatches = [
            entry for entry in entries if inspect_path_integrity(entry) is not level
        ]
        if mismatches:
            raise OSError(
                f"icacls did not apply {level.name} integrity to {mismatches[0]}"
            )
    except Exception as exc:
        raise IntegrityLabelError(
            f"post-icacls verification failed for {target}: {exc}", receipt
        ) from exc
    return receipt


def set_low_integrity_tree(path: Path) -> IntegrityLabelApplication:
    return _set_integrity_tree(path, IntegrityLevel.LOW)


def set_medium_integrity_tree(path: Path) -> IntegrityLabelApplication:
    return _set_integrity_tree(path, IntegrityLevel.MEDIUM)


def _allocate_integrity_sid(level: IntegrityLevel) -> int:
    authority = _SID_IDENTIFIER_AUTHORITY((0, 0, 0, 0, 0, 16))
    sid = ctypes.c_void_p()
    if not _advapi32.AllocateAndInitializeSid(
        ctypes.byref(authority),
        1,
        int(level),
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        ctypes.byref(sid),
    ):
        raise _winerror("AllocateAndInitializeSid failed")
    return sid.value


def _set_token_integrity(token: int, level: IntegrityLevel) -> None:
    sid = _allocate_integrity_sid(level)
    try:
        label = _TOKEN_MANDATORY_LABEL(_SID_AND_ATTRIBUTES(sid, _SE_GROUP_INTEGRITY))
        length = ctypes.sizeof(label) + _advapi32.GetLengthSid(sid)
        if not _advapi32.SetTokenInformation(
            token, _TOKEN_INTEGRITY_LEVEL, ctypes.byref(label), length
        ):
            raise _winerror("SetTokenInformation(TokenIntegrityLevel) failed")
    finally:
        _advapi32.FreeSid(sid)


def _environment_block(environment: Mapping[str, str]) -> ctypes.Array[ctypes.c_wchar]:
    entries: dict[str, tuple[str, str]] = {}
    for key, value in environment.items():
        if not isinstance(key, str) or not key or "=" in key or "\0" in key:
            raise ValueError(f"invalid environment variable name: {key!r}")
        if not isinstance(value, str) or "\0" in value:
            raise ValueError(f"invalid environment value for {key!r}")
        folded = key.casefold()
        if folded in entries:
            raise ValueError(f"duplicate case-insensitive environment variable: {key!r}")
        entries[folded] = (key, value)
    serialized = "\0".join(
        f"{key}={value}" for _, (key, value) in sorted(entries.items())
    ) + "\0\0"
    return ctypes.create_unicode_buffer(serialized)


def _terminate_created_process(process: int) -> None:
    if not _kernel32.TerminateProcess(process, 1):
        raise _winerror("TerminateProcess failed for suspended child")
    wait_result = _kernel32.WaitForSingleObject(process, _WAIT_TIMEOUT_MS)
    if wait_result != _WAIT_OBJECT_0:
        raise OSError(
            f"suspended child termination was not observed (wait result 0x{wait_result:08x})"
        )


def _resume_verified_child(process: int, thread: int, pid: int) -> None:
    del process, pid
    previous_suspend_count = _kernel32.ResumeThread(thread)
    if previous_suspend_count == 0xFFFFFFFF:
        raise _winerror("ResumeThread failed for verified Low-integrity child")
    if previous_suspend_count != 1:
        raise OSError(
            "verified Low-integrity child had an unexpected prior suspend count: "
            f"{previous_suspend_count}"
        )


def launch_low_integrity_process(
    executable: Path,
    args: tuple[str, ...],
    cwd: Path,
    environment: Mapping[str, str],
) -> ProcessLaunch:
    _require_windows()
    executable_path = Path(executable)
    working_directory = Path(cwd)
    if not executable_path.is_absolute() or not executable_path.is_file():
        raise ValueError("executable must be an existing absolute regular file")
    if not working_directory.is_absolute() or not working_directory.is_dir():
        raise ValueError("working directory must be an existing absolute directory")
    if not isinstance(args, tuple) or any(
        not isinstance(argument, str) or "\0" in argument for argument in args
    ):
        raise ValueError("arguments must be a tuple of NUL-free strings")

    environment_buffer = _environment_block(environment)
    command_line_text = subprocess.list2cmdline((str(executable_path), *args))
    command_line = ctypes.create_unicode_buffer(command_line_text)
    process_information = _PROCESS_INFORMATION()
    startup = _STARTUPINFOW()
    startup.cb = ctypes.sizeof(startup)
    if not _kernel32.CreateProcessW(
        str(executable_path),
        command_line,
        None,
        None,
        False,
        _CREATE_SUSPENDED | _CREATE_UNICODE_ENVIRONMENT,
        environment_buffer,
        str(working_directory),
        ctypes.byref(startup),
        ctypes.byref(process_information),
    ):
        raise _winerror("CreateProcessW(CREATE_SUSPENDED) failed")

    child_token = wintypes.HANDLE()
    resumed = False
    try:
        try:
            if not _advapi32.OpenProcessToken(
                process_information.hProcess,
                _TOKEN_QUERY | _TOKEN_ADJUST_DEFAULT,
                ctypes.byref(child_token),
            ):
                raise _winerror("OpenProcessToken failed for suspended child")
            _set_token_integrity(child_token, IntegrityLevel.LOW)
        finally:
            _close_handle(child_token.value)

        observed = inspect_process_integrity(process_information.dwProcessId)
        if observed is not IntegrityLevel.LOW:
            raise OSError(
                f"suspended process {process_information.dwProcessId} was {observed.name}, not LOW"
            )
        _resume_verified_child(
            process_information.hProcess,
            process_information.hThread,
            process_information.dwProcessId,
        )
        resumed = True
        return ProcessLaunch(
            pid=process_information.dwProcessId,
            executable=str(executable_path),
            arguments=args,
            working_directory=str(working_directory),
            integrity=observed,
        )
    except BaseException:
        if not resumed:
            _terminate_created_process(process_information.hProcess)
        raise
    finally:
        _close_handle(process_information.hThread)
        _close_handle(process_information.hProcess)
