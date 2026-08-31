"""Read-only Windows process and Documents evidence for MO2 bootstrap."""

from __future__ import annotations

import ctypes
import os
import stat
import uuid
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from .bootstrap_model import ProcessIdentity, ProcessObservation


class ProcessInspectionError(RuntimeError):
    """Raised when required host evidence cannot be observed completely."""


@dataclass(frozen=True)
class RawProcess:
    pid: int
    image_name: str
    executable_path: str


_TH32CS_SNAPPROCESS = 0x00000002
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_ERROR_NO_MORE_FILES = 18
_MAX_PATH = 260
_QUERY_PATH_CAPACITY = 32768
_CANDIDATE_IMAGES = {"modorganizer.exe", "nxmhandler.exe"}
_FOLDERID_DOCUMENTS = uuid.UUID("fdd39ad0-238f-46af-adb4-6c85480369c7")


class _ProcessEntry32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * _MAX_PATH),
    ]


class _Guid(ctypes.Structure):
    _fields_ = [
        ("data1", wintypes.DWORD),
        ("data2", wintypes.WORD),
        ("data3", wintypes.WORD),
        ("data4", ctypes.c_ubyte * 8),
    ]


def _inspect_mo2_processes(
    instance_root: Path,
    *,
    enumerator: Callable[[], tuple[RawProcess, ...]],
) -> ProcessObservation:
    root = Path(instance_root)
    expected = {
        _normalize_absolute(root / "app" / "ModOrganizer.exe"),
        _normalize_absolute(root / "app" / "nxmhandler.exe"),
    }
    try:
        records = enumerator()
        relevant: list[ProcessIdentity] = []
        seen_pids: set[int] = set()
        for item in sorted(records, key=lambda value: value.pid):
            if item.image_name.casefold() not in _CANDIDATE_IMAGES:
                continue
            if type(item.pid) is not int or item.pid <= 0:
                raise ProcessInspectionError("candidate process PID is invalid")
            if item.pid in seen_pids:
                raise ProcessInspectionError("candidate process PID is duplicated")
            seen_pids.add(item.pid)
            actual = _normalize_absolute(Path(item.executable_path))
            if actual in expected:
                relevant.append(
                    ProcessIdentity(
                        pid=item.pid,
                        image_name=item.image_name,
                        executable_path=item.executable_path,
                    )
                )
    except (OSError, ProcessInspectionError) as error:
        return ProcessObservation(complete=False, relevant=(), error=str(error))
    return ProcessObservation(complete=True, relevant=tuple(relevant), error=None)


def enumerate_windows_processes() -> tuple[RawProcess, ...]:
    if os.name != "nt":
        raise ProcessInspectionError("Windows process inspection is unavailable")

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_snapshot = kernel32.CreateToolhelp32Snapshot
    create_snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    create_snapshot.restype = wintypes.HANDLE
    first_process = kernel32.Process32FirstW
    first_process.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_ProcessEntry32W),
    )
    first_process.restype = wintypes.BOOL
    next_process = kernel32.Process32NextW
    next_process.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_ProcessEntry32W),
    )
    next_process.restype = wintypes.BOOL
    open_process = kernel32.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    query_path = kernel32.QueryFullProcessImageNameW
    query_path.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    query_path.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    snapshot = create_snapshot(_TH32CS_SNAPPROCESS, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ProcessInspectionError(
            f"cannot enumerate Windows processes: {_last_windows_error()}"
        )

    result: list[RawProcess] = []
    try:
        entry = _ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(_ProcessEntry32W)
        ctypes.set_last_error(0)
        if not first_process(snapshot, ctypes.byref(entry)):
            error_code = ctypes.get_last_error()
            if error_code == _ERROR_NO_MORE_FILES:
                return ()
            raise ProcessInspectionError(
                f"cannot read first Windows process: {_windows_error_text(error_code)}"
            )

        while True:
            image_name = entry.szExeFile
            if image_name.casefold() in _CANDIDATE_IMAGES:
                executable_path = _query_candidate_path(
                    kernel32,
                    open_process,
                    query_path,
                    close_handle,
                    int(entry.th32ProcessID),
                    image_name,
                )
                result.append(
                    RawProcess(
                        pid=int(entry.th32ProcessID),
                        image_name=image_name,
                        executable_path=executable_path,
                    )
                )

            entry.dwSize = ctypes.sizeof(_ProcessEntry32W)
            ctypes.set_last_error(0)
            if next_process(snapshot, ctypes.byref(entry)):
                continue
            error_code = ctypes.get_last_error()
            if error_code not in {0, _ERROR_NO_MORE_FILES}:
                raise ProcessInspectionError(
                    f"Windows process enumeration failed: {_windows_error_text(error_code)}"
                )
            break
    finally:
        close_handle(snapshot)

    return tuple(sorted(result, key=lambda item: item.pid))


def inspect_mo2_processes(
    instance_root: Path,
    *,
    enumerator: Callable[
        [], tuple[RawProcess, ...]
    ] = enumerate_windows_processes,
) -> ProcessObservation:
    return _inspect_mo2_processes(instance_root, enumerator=enumerator)


def windows_documents_root() -> Path:
    try:
        raw_path = _resolve_documents_path()
    except OSError as error:
        raise ProcessInspectionError(f"cannot resolve Windows Documents: {error}") from error
    path = Path(raw_path)
    if not path.is_absolute():
        raise ProcessInspectionError("Windows Documents path must be absolute")
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ProcessInspectionError(
            f"Windows Documents path must be an existing directory: {error}"
        ) from error
    if not stat.S_ISDIR(metadata.st_mode):
        raise ProcessInspectionError("Windows Documents path must be an existing directory")
    return path.resolve(strict=True)


def _query_candidate_path(
    kernel32,
    open_process,
    query_path,
    close_handle,
    pid: int,
    image_name: str,
) -> str:
    ctypes.set_last_error(0)
    handle = open_process(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        raise ProcessInspectionError(
            f"cannot inspect candidate {image_name} PID {pid}: {_last_windows_error()}"
        )
    try:
        buffer = ctypes.create_unicode_buffer(_QUERY_PATH_CAPACITY)
        size = wintypes.DWORD(len(buffer))
        ctypes.set_last_error(0)
        if not query_path(handle, 0, buffer, ctypes.byref(size)):
            raise ProcessInspectionError(
                f"cannot resolve candidate {image_name} PID {pid}: {_last_windows_error()}"
            )
        value = buffer.value
        if not value or not PureWindowsPath(value).is_absolute():
            raise ProcessInspectionError(
                f"candidate {image_name} PID {pid} path is not absolute"
            )
        return value
    finally:
        close_handle(handle)


def _resolve_documents_path() -> str:
    if os.name != "nt":
        raise ProcessInspectionError("Windows Known Folder API is unavailable")
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    known_folder = shell32.SHGetKnownFolderPath
    known_folder.argtypes = (
        ctypes.POINTER(_Guid),
        wintypes.DWORD,
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_wchar_p),
    )
    known_folder.restype = ctypes.c_long
    free_memory = ole32.CoTaskMemFree
    free_memory.argtypes = (ctypes.c_void_p,)
    free_memory.restype = None

    folder_id = _Guid.from_buffer_copy(_FOLDERID_DOCUMENTS.bytes_le)
    output = ctypes.c_wchar_p()
    result = known_folder(ctypes.byref(folder_id), 0, None, ctypes.byref(output))
    if result != 0:
        raise ProcessInspectionError(
            f"SHGetKnownFolderPath(Documents) failed with HRESULT 0x{result & 0xFFFFFFFF:08x}"
        )
    try:
        if not output.value:
            raise ProcessInspectionError("SHGetKnownFolderPath returned an empty path")
        return output.value
    finally:
        free_memory(output)


def _normalize_absolute(path: Path) -> str:
    windows = PureWindowsPath(str(path))
    if not windows.is_absolute():
        raise ProcessInspectionError(f"candidate executable path must be absolute: {path}")
    try:
        resolved = path.resolve(strict=False)
    except OSError as error:
        raise ProcessInspectionError(f"cannot resolve candidate executable path: {error}") from error
    return os.path.normcase(os.path.normpath(str(resolved)))


def _last_windows_error() -> str:
    return _windows_error_text(ctypes.get_last_error())


def _windows_error_text(error_code: int) -> str:
    if error_code == 0:
        return "unknown Windows error"
    return str(ctypes.WinError(error_code))
