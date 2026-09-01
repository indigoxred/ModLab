"""Loss-detecting recursive Windows directory mutation monitor."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
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

from modlab.validation.mo2_containment_model import (
    ContainmentScenario,
    ProtectedState,
    TreeIdentity,
    WatchEvidenceCompletion,
    WatcherEvent,
)
from modlab.validation.windows_watch_protocol import (
    CLAIM_NAME as _CLAIM_NAME,
    CONTROLLER_LOSS_NAME as _CONTROLLER_LOSS_NAME,
    EVENTS_NAME as _EVENTS_NAME,
    READY_NAME as _READY_NAME,
    REQUEST_NAME as _REQUEST_NAME,
    ROOT_KINDS,
    STOP_NAME as _STOP_NAME,
    TERMINAL_NAME as _TERMINAL_NAME,
    ControllerClaim,
    ControllerLoss,
    WorkerLaunch,
    WatchProtocolError,
    WatchReceipt,
    WatchRequest,
    WatchRoot,
    controller_claim_from_bytes,
    controller_claim_to_bytes,
    controller_loss_from_bytes,
    controller_loss_to_bytes,
    publish_new_verified,
    watch_request_from_bytes,
    watch_request_sha256,
    watch_request_to_bytes,
)


_SCHEMA_VERSION = 1
_OWNER_NAME = "owner.json"

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
_ERROR_IO_PENDING = 997
_ERROR_OPERATION_ABORTED = 995
_ERROR_NOT_FOUND = 1168
_ERROR_NOTIFY_ENUM_DIR = 1022
_WAIT_OBJECT_0 = 0
_WAIT_ABANDONED = 0x00000080
_WAIT_TIMEOUT = 258
_WAIT_FAILED = 0xFFFFFFFF
_INFINITE = 0xFFFFFFFF
_FILE_BEGIN = 0
_SYNCHRONIZE = 0x00100000
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_BUFFER_SIZE = 64 * 1024
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _HandleOwnershipError(WatchProtocolError):
    """A native handle could not be closed and remains owned by the caller."""

    def __init__(self, message: str, handle: int, label: str = "retained native handle") -> None:
        super().__init__(message)
        self.handle = handle
        self.label = label


@dataclass
class _LocalWorker:
    process: subprocess.Popen[bytes] | None
    worker_pid: int
    request: WatchRequest
    request_bytes_sha256: str
    process_handle: int
    process_creation_time: int
    owner_token: str
    popen_handle: int = 0
    cleanup_complete: bool = False


_LOCAL_WORKERS: dict[Path, _LocalWorker] = {}
_LOCAL_WORKERS_LOCK = threading.Lock()
_RETAINED_AUXILIARY_HANDLES: dict[Path, list[int]] = {}
_RETAINED_AUXILIARY_HANDLES_LOCK = threading.Lock()
_RETAINED_SYNCHRONIZATION_HANDLES: list[int] = []
_RETAINED_OWNED_MUTEX_HANDLES: list[tuple[int, int]] = []
_RETAINED_SYNCHRONIZATION_HANDLES_LOCK = threading.Lock()
_SYNCHRONIZATION_CLEANUP_WARNINGS: list[str] = []


def _make_watch_receipt(
    request: WatchRequest | object,
    worker_pid: int,
    *,
    complete: bool,
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
        evidence_completion=(
            WatchEvidenceCompletion.COMPLETED
            if complete
            else WatchEvidenceCompletion.INCOMPLETE
        ),
        ready=ready,
        opened_root_kinds=opened_root_kinds,
        events=events,
        event_bytes_sha256=event_bytes_sha256,
        worker_exit_code=0 if complete else None,
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
    _kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    _kernel32.CreateMutexW.restype = wintypes.HANDLE
    _kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    _kernel32.ReleaseMutex.restype = wintypes.BOOL
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
        close_error = _close_handle(handle, "event journal readback")
        if close_error is not None:
            raise _HandleOwnershipError(close_error, handle, "event journal readback")


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
    return _normalize_request(watch_request_from_bytes(data), inspect_roots=False)


def _write_new(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{secrets.token_hex(8)}.tmp"
    )
    descriptor = os.open(temporary, flags, 0o600)
    promoted = False
    try:
        written = os.write(descriptor, data)
        if written != len(data):
            raise OSError(f"incomplete write for {path}")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.rename(temporary, path)
        promoted = True
    finally:
        if not promoted:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _replace_record(path: Path, data: bytes) -> None:
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{secrets.token_hex(8)}.tmp"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = os.open(temporary, flags, 0o600)
    promoted = False
    try:
        written = os.write(descriptor, data)
        if written != len(data):
            raise OSError(f"incomplete write for {path}")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, path)
        promoted = True
    finally:
        if not promoted:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


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
    except BaseException as error:
        close_error = _close_handle(handle, f"worker process {pid}")
        if close_error is not None:
            raise _HandleOwnershipError(
                f"{error}; {close_error}",
                handle,
                f"worker process {pid}",
            ) from error
        raise


def _completed_receipt_document(receipt: WatchReceipt) -> dict[str, object]:
    return {
        "complete": receipt.complete,
        "error": receipt.error,
        "eventBytesSha256": receipt.event_bytes_sha256,
        "events": [
            {
                "action": event.action,
                "relativePath": event.relative_path,
                "rootKind": event.root_kind,
                "sequence": event.sequence,
            }
            for event in receipt.events
        ],
        "openedRootKinds": list(receipt.opened_root_kinds),
        "ready": receipt.ready,
        "requestBytesSha256": receipt.request_bytes_sha256,
        "requestId": receipt.request_id,
        "workerPid": receipt.worker_pid,
    }


def _completed_receipt_from_document(
    value: object,
    request: WatchRequest,
    worker_pid: int,
    request_sha256: str,
) -> WatchReceipt:
    if type(value) is not dict:
        raise WatchProtocolError("completed owner receipt must be an object")
    _exact_fields(
        value,
        {
            "complete",
            "error",
            "eventBytesSha256",
            "events",
            "openedRootKinds",
            "ready",
            "requestBytesSha256",
            "requestId",
            "workerPid",
        },
        "completed owner receipt",
    )
    if value["complete"] is not True or value["ready"] is not True or value["error"] is not None:
        raise WatchProtocolError("completed owner receipt is not complete")
    if (
        value["requestId"] != request.request_id
        or value["requestBytesSha256"] != request_sha256
        or _required_int(value["workerPid"], "completed receipt worker PID", minimum=1)
        != worker_pid
    ):
        raise WatchProtocolError("completed owner receipt identity mismatch")
    opened_value = value["openedRootKinds"]
    if type(opened_value) is not list or not all(type(kind) is str for kind in opened_value):
        raise WatchProtocolError("completed owner root kinds must be strings")
    opened = tuple(opened_value)
    if opened != tuple(root.root_kind for root in request.roots):
        raise WatchProtocolError("completed owner root coverage mismatch")
    event_values = value["events"]
    if type(event_values) is not list:
        raise WatchProtocolError("completed owner events must be an array")
    event_bytes = b""
    for event_value in event_values:
        event_bytes += _canonical_bytes(event_value)
    events = _parse_events(event_bytes, request)
    digest = _required_text(value["eventBytesSha256"], "completed event SHA-256")
    if digest != hashlib.sha256(event_bytes).hexdigest():
        raise WatchProtocolError("completed owner event SHA-256 mismatch")
    return _make_watch_receipt(
        request,
        worker_pid,
        complete=True,
        ready=True,
        opened_root_kinds=opened,
        events=events,
        event_bytes_sha256=digest,
        error=None,
        request_bytes_sha256=request_sha256,
    )


def _owner_document(
    request: WatchRequest,
    request_sha256: str,
    owner_token: str,
    *,
    state: str,
    worker_pid: int,
    worker_creation_time: int,
    error: str | None,
    retaining_controller_pid: int = 0,
    retaining_controller_creation_time: int = 0,
    observer_controller_pid: int = 0,
    observer_controller_creation_time: int = 0,
    completed_receipt: WatchReceipt | None = None,
) -> dict[str, object]:
    if state in {"Starting", "RecoveryRequired"} and retaining_controller_pid == 0:
        retaining_controller_pid, retaining_controller_creation_time = (
            _current_controller_identity()
        )
    if state in {"ExitObserved", "CleanupComplete", "Completed"} and observer_controller_pid == 0:
        observer_controller_pid, observer_controller_creation_time = (
            _current_controller_identity()
        )
    return {
        "completedReceipt": (
            _completed_receipt_document(completed_receipt)
            if completed_receipt is not None
            else None
        ),
        "error": error,
        "observerControllerCreationTime": observer_controller_creation_time,
        "observerControllerPid": observer_controller_pid,
        "ownerToken": owner_token,
        "request": _request_document(request),
        "requestBytesSha256": request_sha256,
        "requestId": request.request_id,
        "retainingControllerCreationTime": retaining_controller_creation_time,
        "retainingControllerPid": retaining_controller_pid,
        "schemaVersion": _SCHEMA_VERSION,
        "state": state,
        "workerCreationTime": worker_creation_time,
        "workerPid": worker_pid,
    }


def _replace_owner_state(
    request: WatchRequest,
    request_sha256: str,
    owner_token: str,
    *,
    state: str,
    worker_pid: int,
    worker_creation_time: int,
    error: str | None,
    retaining_controller_pid: int = 0,
    retaining_controller_creation_time: int = 0,
    observer_controller_pid: int = 0,
    observer_controller_creation_time: int = 0,
    completed_receipt: WatchReceipt | None = None,
) -> None:
    _replace_record(
        request.evidence_root / _OWNER_NAME,
        _canonical_bytes(
            _owner_document(
                request,
                request_sha256,
                owner_token,
                state=state,
                worker_pid=worker_pid,
                worker_creation_time=worker_creation_time,
                error=error,
                retaining_controller_pid=retaining_controller_pid,
                retaining_controller_creation_time=retaining_controller_creation_time,
                observer_controller_pid=observer_controller_pid,
                observer_controller_creation_time=observer_controller_creation_time,
                completed_receipt=completed_receipt,
            )
        ),
    )


def _parse_owner(
    data: bytes,
    request: WatchRequest,
    request_sha256: str,
) -> dict[str, object]:
    value = _object_from_bytes(data, "owner record")
    _exact_fields(
        value,
        {
            "completedReceipt",
            "error",
            "observerControllerCreationTime",
            "observerControllerPid",
            "ownerToken",
            "request",
            "requestBytesSha256",
            "requestId",
            "schemaVersion",
            "state",
            "retainingControllerCreationTime",
            "retainingControllerPid",
            "workerCreationTime",
            "workerPid",
        },
        "owner record",
    )
    if _required_int(value["schemaVersion"], "owner schema version") != _SCHEMA_VERSION:
        raise WatchProtocolError("unsupported owner schema version")
    if value["requestId"] != request.request_id:
        raise WatchProtocolError("owner record request ID mismatch")
    if value["request"] != _request_document(request):
        raise WatchProtocolError(
            "owner record request SHA-256 mismatch: embedded request document differs"
        )
    digest = _required_text(value["requestBytesSha256"], "owner request SHA-256")
    if digest != request_sha256:
        raise WatchProtocolError("owner record request SHA-256 mismatch")
    token = _required_text(value["ownerToken"], "owner token")
    if re.fullmatch(r"[0-9a-f]{64}", token) is None:
        raise WatchProtocolError("owner token must be lowercase 256-bit hex")
    state = _required_text(value["state"], "owner state")
    if state not in {
        "Launching",
        "Starting",
        "Running",
        "ExitObserved",
        "CleanupComplete",
        "Completed",
        "LaunchFailed",
        "RecoveryRequired",
    }:
        raise WatchProtocolError("owner state is invalid")
    pid = _required_int(value["workerPid"], "owner worker PID")
    creation_time = _required_int(
        value["workerCreationTime"],
        "owner worker creation time",
    )
    error = value["error"]
    if error is not None:
        _required_text(error, "owner error")
    retaining_pid = _required_int(
        value["retainingControllerPid"], "retaining controller PID"
    )
    retaining_creation = _required_int(
        value["retainingControllerCreationTime"],
        "retaining controller creation time",
    )
    observer_pid = _required_int(value["observerControllerPid"], "observer controller PID")
    observer_creation = _required_int(
        value["observerControllerCreationTime"],
        "observer controller creation time",
    )
    if (retaining_pid == 0) != (retaining_creation == 0):
        raise WatchProtocolError("retaining controller identity is incomplete")
    if (observer_pid == 0) != (observer_creation == 0):
        raise WatchProtocolError("observer controller identity is incomplete")
    completed_receipt = value["completedReceipt"]
    if state in {"Starting", "Running"}:
        if pid == 0 or creation_time == 0 or error is not None:
            raise WatchProtocolError(f"{state.lower()} owner record is incomplete")
        if observer_pid or (state == "Starting") != bool(retaining_pid):
            raise WatchProtocolError(f"{state.lower()} owner controller identity is inconsistent")
        if completed_receipt is not None:
            raise WatchProtocolError(f"{state.lower()} owner completed receipt is inconsistent")
    elif state in {"ExitObserved", "CleanupComplete", "Completed"}:
        if pid == 0 or creation_time == 0 or error is not None or not observer_pid:
            raise WatchProtocolError(f"{state.lower()} owner record is inconsistent")
        if retaining_pid:
            raise WatchProtocolError(f"{state.lower()} owner retention is inconsistent")
        if state == "Completed":
            _completed_receipt_from_document(
                completed_receipt, request, pid, request_sha256
            )
        elif completed_receipt is not None:
            raise WatchProtocolError(f"{state.lower()} owner completed receipt is inconsistent")
    elif state == "RecoveryRequired":
        if pid == 0 or error is None or not retaining_pid:
            raise WatchProtocolError("recovery-required owner record is inconsistent")
        if completed_receipt is not None:
            raise WatchProtocolError("recovery-required owner completed receipt is inconsistent")
    elif state == "LaunchFailed":
        if pid != 0 or creation_time != 0 or error is None or retaining_pid or observer_pid:
            raise WatchProtocolError("launch-failed owner record is inconsistent")
        if completed_receipt is not None:
            raise WatchProtocolError("launch-failed owner completed receipt is inconsistent")
    elif pid != 0 or creation_time != 0 or error is not None or retaining_pid or observer_pid or completed_receipt is not None:
        raise WatchProtocolError("launching owner record is inconsistent")
    return value


def _request_from_owner_unbound(data: bytes, request_path: Path) -> WatchRequest:
    """Recover the immutable request copy when request.json is unavailable."""
    value = _object_from_bytes(data, "owner record")
    request_value = value.get("request")
    if type(request_value) is not dict:
        raise WatchProtocolError("owner record immutable request is unavailable")
    request_bytes = _canonical_bytes(request_value)
    request = _request_from_bytes(request_bytes)
    expected_path = request.evidence_root / _REQUEST_NAME
    if expected_path.absolute() != Path(request_path).absolute():
        raise WatchProtocolError("owner request path identity mismatch")
    request_sha256 = hashlib.sha256(request_bytes).hexdigest()
    _parse_owner(data, request, request_sha256)
    return request


def _worker_identity_from_ready(
    data: bytes,
    request: WatchRequest,
    request_sha256: str,
) -> tuple[int, int]:
    value = _object_from_bytes(data, "ready record")
    pid = _required_int(value.get("workerPid"), "ready worker PID", minimum=1)
    creation_time = _required_int(
        value.get("workerCreationTime"), "ready worker creation time", minimum=1
    )
    _parse_ready(data, request, pid, request_sha256, creation_time)
    return pid, creation_time


def _load_request_path(request_path: Path) -> WatchRequest:
    supplied = _reject_reparse_components(Path(request_path), "request path")
    resolved = supplied.resolve(strict=True)
    request = _request_from_bytes(resolved.read_bytes())
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


def _publish_launch_failure(
    request: WatchRequest,
    request_sha256: str,
    owner_token: str,
    events_path: Path,
    terminal_path: Path,
    owner_path: Path,
    message: str,
) -> str:
    journal_evidence = _JournalEvidence(0, 0, b"", 0, 0)
    retained_journal_handles: list[int] = []
    try:
        journal = _EventJournal(events_path)
        try:
            journal_evidence = journal.evidence()
        except (OSError, WatchProtocolError) as journal_evidence_error:
            message = (
                f"{message}; launch-failure journal evidence failed: "
                f"{journal_evidence_error}"
            )
        journal_close_error = journal.close()
        if journal_close_error is not None:
            message = f"{message}; {journal_close_error}"
            retained_journal_handles.append(journal._handle)
    except _HandleOwnershipError as journal_error:
        retained_journal_handles.append(journal_error.handle)
        message = f"{message}; launch-failure journal publication failed: {journal_error}"
    except (OSError, WatchProtocolError) as journal_error:
        message = f"{message}; launch-failure journal publication failed: {journal_error}"
    if retained_journal_handles:
        with _RETAINED_AUXILIARY_HANDLES_LOCK:
            _RETAINED_AUXILIARY_HANDLES.setdefault(
                request.evidence_root.absolute(), []
            ).extend(retained_journal_handles)
    _replace_record(
        owner_path,
        _canonical_bytes(
            _owner_document(
                request,
                request_sha256,
                owner_token,
                state="LaunchFailed",
                worker_pid=0,
                worker_creation_time=0,
                error=message,
            )
        ),
    )
    terminal = _terminal_document(
        request,
        worker_pid=0,
        worker_creation_time=0,
        request_sha256=request_sha256,
        complete=False,
        ready=False,
        event_digest=journal_evidence.sha256,
        errors=[message],
        open_handle_count=len(retained_journal_handles),
        root_identities_unchanged=False,
        opened_root_kinds=(),
        journal_evidence=journal_evidence,
    )
    _write_new(terminal_path, _canonical_bytes(terminal))
    return message


def _cleanup_spawned_worker(
    process: subprocess.Popen[bytes],
    process_handle: int,
) -> tuple[bool, tuple[str, ...], int, int]:
    errors: list[str] = []
    try:
        process.terminate()
    except OSError as error:
        errors.append(f"worker termination failed: {error}")
    exited = False
    try:
        process.wait(timeout=15)
        exited = True
    except subprocess.TimeoutExpired:
        errors.append("worker exit wait timed out after termination")
        try:
            process.kill()
        except OSError as error:
            errors.append(f"worker kill failed: {error}")
        try:
            process.wait(timeout=15)
            exited = True
        except subprocess.TimeoutExpired:
            errors.append("worker exit wait timed out after kill")
        except OSError as error:
            errors.append(f"worker exit wait failed after kill: {error}")
    except OSError as error:
        errors.append(f"worker exit wait failed after termination: {error}")
    if not exited:
        return False, tuple(errors), process_handle, 0
    retained_process_handle = process_handle
    retained_popen_handle = _detach_local_popen_handle(process)
    if process_handle:
        close_error = _close_handle(process_handle, f"worker process {process.pid}")
        if close_error is not None:
            errors.append(close_error)
        else:
            retained_process_handle = 0
    if retained_popen_handle:
        close_error = _close_handle(
            retained_popen_handle,
            f"local Popen process {process.pid}",
        )
        if close_error is not None:
            errors.append(close_error)
        else:
            retained_popen_handle = 0
    complete = retained_process_handle == 0 and retained_popen_handle == 0
    return (
        complete,
        tuple(errors),
        retained_process_handle,
        retained_popen_handle,
    )


def _detach_local_popen_handle(process: subprocess.Popen[bytes]) -> int:
    """Transfer Popen's native process handle into explicit controller ownership."""
    handle = getattr(process, "_handle", None)
    if not isinstance(handle, int) or not hasattr(handle, "Detach"):
        return 0
    if getattr(handle, "closed", False):
        return 0
    detached = int(handle.Detach())
    process._child_created = False
    return detached


def _record_spawned_failure(
    request: WatchRequest,
    request_path: Path,
    request_sha256: str,
    owner_token: str,
    events_path: Path,
    terminal_path: Path,
    owner_path: Path,
    process: subprocess.Popen[bytes],
    process_handle: int,
    process_creation_time: int,
    message: str,
) -> str:
    (
        cleanup_complete,
        cleanup_errors,
        retained_process_handle,
        retained_popen_handle,
    ) = _cleanup_spawned_worker(process, process_handle)
    if cleanup_errors:
        message = f"{message}; {'; '.join(cleanup_errors)}"
    if cleanup_complete:
        return _publish_launch_failure(
            request,
            request_sha256,
            owner_token,
            events_path,
            terminal_path,
            owner_path,
            message,
        )
    request_key = request_path.absolute()
    with _LOCAL_WORKERS_LOCK:
        _LOCAL_WORKERS[request_key] = _LocalWorker(
            process if getattr(process, "_child_created", True) else None,
            process.pid,
            request,
            request_sha256,
            retained_process_handle,
            process_creation_time,
            owner_token,
            retained_popen_handle,
        )
    _replace_record(
        owner_path,
        _canonical_bytes(
            _owner_document(
                request,
                request_sha256,
                owner_token,
                state="RecoveryRequired",
                worker_pid=process.pid,
                worker_creation_time=process_creation_time,
                error=message,
            )
        ),
    )
    return message


def _release_started_worker(
    request: WatchRequest,
    request_path: Path,
    request_sha256: str,
    owner_token: str,
    owner_path: Path,
    process: subprocess.Popen[bytes],
    process_handle: int,
    process_creation_time: int,
    *,
    timeout: int,
    failure_message: str,
) -> str | None:
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        message = f"{failure_message}: {error}"
    except OSError as error:
        message = f"{failure_message}: worker exit wait failed: {error}"
    else:
        observer_pid, observer_creation_time = _current_controller_identity()
        try:
            _replace_owner_state(
                request,
                request_sha256,
                owner_token,
                state="ExitObserved",
                worker_pid=process.pid,
                worker_creation_time=process_creation_time,
                error=None,
                observer_controller_pid=observer_pid,
                observer_controller_creation_time=observer_creation_time,
            )
        except (OSError, WatchProtocolError) as error:
            return f"{failure_message}: exit-observed publication failed: {error}"
        retained_process_handle = process_handle
        retained_popen_handle = _detach_local_popen_handle(process)
        close_errors: list[str] = []
        if retained_process_handle:
            close_error = _close_handle(
                retained_process_handle,
                f"worker process {process.pid}",
            )
            if close_error is None:
                retained_process_handle = 0
            else:
                close_errors.append(close_error)
        if retained_popen_handle:
            close_error = _close_handle(
                retained_popen_handle,
                f"local Popen process {process.pid}",
            )
            if close_error is None:
                retained_popen_handle = 0
            else:
                close_errors.append(close_error)
        with _LOCAL_WORKERS_LOCK:
            local_worker = _LOCAL_WORKERS.get(request_path.absolute())
            if local_worker is not None:
                if not getattr(process, "_child_created", True):
                    local_worker.process = None
                local_worker.process_handle = retained_process_handle
                local_worker.popen_handle = retained_popen_handle
                local_worker.cleanup_complete = not close_errors
        if not close_errors:
            try:
                _replace_owner_state(
                    request,
                    request_sha256,
                    owner_token,
                    state="CleanupComplete",
                    worker_pid=process.pid,
                    worker_creation_time=process_creation_time,
                    error=None,
                    observer_controller_pid=observer_pid,
                    observer_controller_creation_time=observer_creation_time,
                )
            except (OSError, WatchProtocolError) as error:
                return (
                    f"{failure_message}: cleanup-complete publication failed: {error}"
                )
            with _LOCAL_WORKERS_LOCK:
                _LOCAL_WORKERS.pop(request_path.absolute(), None)
            return None
        message = f"{failure_message}: {'; '.join(close_errors)}"
        _replace_owner_state(
            request,
            request_sha256,
            owner_token,
            state="RecoveryRequired",
            worker_pid=process.pid,
            worker_creation_time=process_creation_time,
            error=message,
            observer_controller_pid=observer_pid,
            observer_controller_creation_time=observer_creation_time,
        )
        return message
    _replace_owner_state(
        request,
        request_sha256,
        owner_token,
        state="RecoveryRequired",
        worker_pid=process.pid,
        worker_creation_time=process_creation_time,
        error=message,
    )
    return message


def start_watch(request: WatchRequest) -> int:
    """Persist a confined request, launch its worker, and return only after ready."""
    _require_windows()
    normalized = _normalize_request(request, inspect_roots=True)
    request_path = normalized.evidence_root / _REQUEST_NAME
    ready_path = normalized.evidence_root / _READY_NAME
    events_path = normalized.evidence_root / _EVENTS_NAME
    terminal_path = normalized.evidence_root / _TERMINAL_NAME
    claim_path = normalized.evidence_root / _CLAIM_NAME
    owner_path = normalized.evidence_root / _OWNER_NAME
    if owner_path.exists():
        raise WatchProtocolError("atomic owner claim already exists")
    for path in (
        request_path,
        claim_path,
        ready_path,
        events_path,
        terminal_path,
        normalized.stop_token_path,
    ):
        if path.exists():
            raise WatchProtocolError(f"watch protocol path already exists: {path.name}")
    request_bytes, request_sha256 = _request_bytes_and_sha256(normalized)
    controller_pid, controller_creation_time = _current_controller_identity()
    controller_claim = ControllerClaim(
        schema_version=_SCHEMA_VERSION,
        request_sha256=request_sha256,
        session_id=normalized.session_id,
        run_id=normalized.run_id,
        scenario=normalized.scenario,
        controller_pid=controller_pid,
        controller_creation_time=controller_creation_time,
    )
    controller_claim_bytes = controller_claim_to_bytes(
        controller_claim,
        normalized,
    )
    owner_token = secrets.token_hex(32)
    try:
        _write_new(
            owner_path,
            _canonical_bytes(
                _owner_document(
                    normalized,
                    request_sha256,
                    owner_token,
                    state="Launching",
                    worker_pid=0,
                    worker_creation_time=0,
                    error=None,
                )
            ),
        )
    except FileExistsError as error:
        raise WatchProtocolError("atomic owner claim already exists") from error
    try:
        _write_new(request_path, request_bytes)
        publish_new_verified(
            claim_path,
            controller_claim_bytes,
            lambda data: controller_claim_from_bytes(data, normalized),
        )
        resolved_request_path = request_path.resolve(strict=True)
    except (OSError, WatchProtocolError) as error:
        message = _publish_launch_failure(
            normalized,
            request_sha256,
            owner_token,
            events_path,
            terminal_path,
            owner_path,
            f"watch request publication failed: {error}",
        )
        raise WatchProtocolError(message) from error
    command = (
        sys.executable,
        "-B",
        "-m",
        "modlab.validation.windows_watch",
        "--worker",
        str(resolved_request_path),
    )
    try:
        process = subprocess.Popen(
            command,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        message = _publish_launch_failure(
            normalized,
            request_sha256,
            owner_token,
            events_path,
            terminal_path,
            owner_path,
            f"watch worker launch failed: {error}",
        )
        raise WatchProtocolError(message) from error
    process_handle = 0
    try:
        process_handle, process_creation_time = _open_process_identity(process.pid)
    except (OSError, WatchProtocolError) as error:
        retained_identity_handle = (
            error.handle if isinstance(error, _HandleOwnershipError) else 0
        )
        message = _record_spawned_failure(
            normalized,
            request_path,
            request_sha256,
            owner_token,
            events_path,
            terminal_path,
            owner_path,
            process,
            retained_identity_handle,
            0,
            f"watch worker identity acquisition failed: {error}",
        )
        raise WatchProtocolError(message) from error
    try:
        _replace_record(
            owner_path,
            _canonical_bytes(
                _owner_document(
                    normalized,
                    request_sha256,
                    owner_token,
                    state="Starting",
                    worker_pid=process.pid,
                    worker_creation_time=process_creation_time,
                    error=None,
                )
            ),
        )
    except (OSError, WatchProtocolError) as error:
        message = _record_spawned_failure(
            normalized,
            request_path,
            request_sha256,
            owner_token,
            events_path,
            terminal_path,
            owner_path,
            process,
            process_handle,
            process_creation_time,
            f"watch owner promotion failed: {error}",
        )
        raise WatchProtocolError(message) from error
    request_key = request_path.absolute()
    with _LOCAL_WORKERS_LOCK:
        _LOCAL_WORKERS[request_key] = _LocalWorker(
            process,
            process.pid,
            normalized,
            request_sha256,
            process_handle,
            process_creation_time,
            owner_token,
        )
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if ready_path.exists():
            try:
                _parse_ready(
                    ready_path.read_bytes(),
                    normalized,
                    process.pid,
                    request_sha256,
                    process_creation_time,
                )
            except (OSError, WatchProtocolError) as error:
                try:
                    _write_new(normalized.stop_token_path, b"stop\n")
                except FileExistsError:
                    pass
                cleanup_error = _release_started_worker(
                    normalized,
                    request_path,
                    request_sha256,
                    owner_token,
                    owner_path,
                    process,
                    process_handle,
                    process_creation_time,
                    timeout=15,
                    failure_message="malformed ready record and worker did not stop cleanly",
                )
                if cleanup_error is not None:
                    raise WatchProtocolError(cleanup_error) from error
                raise
            local_worker = _LOCAL_WORKERS[request_key]
            local_worker.popen_handle = _detach_local_popen_handle(process)
            local_worker.process = None
            launch_close_errors: list[str] = []
            for handle, label, field_name in (
                (
                    local_worker.process_handle,
                    f"launch identity process {process.pid}",
                    "process_handle",
                ),
                (
                    local_worker.popen_handle,
                    f"launch Popen process {process.pid}",
                    "popen_handle",
                ),
            ):
                if not handle:
                    continue
                close_error = _close_handle(handle, label)
                if close_error is None:
                    setattr(local_worker, field_name, 0)
                else:
                    launch_close_errors.append(close_error)
            if launch_close_errors:
                message = "; ".join(launch_close_errors)
                _replace_owner_state(
                    normalized,
                    request_sha256,
                    owner_token,
                    state="RecoveryRequired",
                    worker_pid=process.pid,
                    worker_creation_time=process_creation_time,
                    error=message,
                )
                raise WatchProtocolError(message)
            with _LOCAL_WORKERS_LOCK:
                _LOCAL_WORKERS.pop(request_key, None)
            try:
                _replace_owner_state(
                    normalized,
                    request_sha256,
                    owner_token,
                    state="Running",
                    worker_pid=process.pid,
                    worker_creation_time=process_creation_time,
                    error=None,
                )
            except (OSError, WatchProtocolError) as error:
                message = f"watch owner promotion failed after launcher cleanup: {error}"
                try:
                    recovery_handle = _verified_process_handle(
                        process.pid, process_creation_time
                    )
                except _HandleOwnershipError as ownership_error:
                    recovery_handle = ownership_error.handle
                    message = f"{message}; {ownership_error}"
                except (OSError, WatchProtocolError) as recovery_error:
                    raise WatchProtocolError(
                        f"{message}; exact recovery handle acquisition failed: {recovery_error}"
                    ) from error
                with _LOCAL_WORKERS_LOCK:
                    _LOCAL_WORKERS[request_key] = _LocalWorker(
                        None,
                        process.pid,
                        normalized,
                        request_sha256,
                        recovery_handle,
                        process_creation_time,
                        owner_token,
                    )
                try:
                    _replace_owner_state(
                        normalized,
                        request_sha256,
                        owner_token,
                        state="RecoveryRequired",
                        worker_pid=process.pid,
                        worker_creation_time=process_creation_time,
                        error=message,
                    )
                except (OSError, WatchProtocolError) as recovery_error:
                    message = (
                        f"{message}; recovery owner publication failed: {recovery_error}"
                    )
                raise WatchProtocolError(message) from error
            return process.pid
        if terminal_path.exists() or process.poll() is not None:
            cleanup_error = _release_started_worker(
                normalized,
                request_path,
                request_sha256,
                owner_token,
                owner_path,
                process,
                process_handle,
                process_creation_time,
                timeout=5,
                failure_message="worker exited before ready but cleanup remained incomplete",
            )
            if cleanup_error is not None:
                raise WatchProtocolError(cleanup_error)
            receipt = watch_receipt_from_files(
                normalized,
                process.pid,
                ready_path,
                events_path,
                terminal_path,
            )
            raise WatchProtocolError(receipt.error or "watch worker exited before ready")
        time.sleep(0.02)
    try:
        _write_new(normalized.stop_token_path, b"stop\n")
    except FileExistsError:
        pass
    cleanup_error = _release_started_worker(
        normalized,
        request_path,
        request_sha256,
        owner_token,
        owner_path,
        process,
        process_handle,
        process_creation_time,
        timeout=15,
        failure_message="watch worker did not become ready or stop",
    )
    if cleanup_error is not None:
        raise WatchProtocolError(cleanup_error)
    raise WatchProtocolError("watch worker did not become ready")


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
) -> dict[str, object]:
    return {
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


def run_watch_worker(request_path: Path) -> int:
    """Run one fail-closed worker: 0 complete, 1 incomplete, 2 untrustworthy."""
    _require_windows()
    try:
        request = _load_request_path(request_path)
        _, request_sha256 = _request_bytes_and_sha256(request)
        claim = controller_claim_from_bytes(
            (request.evidence_root / _CLAIM_NAME).read_bytes(),
            request,
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
    owner_deadline = time.monotonic() + 10.0
    while True:
        owner = _parse_owner(
            (request.evidence_root / _OWNER_NAME).read_bytes(),
            request,
            request_sha256,
        )
        if owner["state"] in {"Starting", "Running"}:
            if (
                owner["workerPid"] != worker_pid
                or (
                    worker_creation_time != 0
                    and owner["workerCreationTime"] != worker_creation_time
                )
            ):
                raise WatchProtocolError("owner claim does not bind this exact worker")
            if worker_creation_time == 0:
                worker_creation_time = _required_int(
                    owner["workerCreationTime"],
                    "owner worker creation time",
                )
            break
        if owner["state"] != "Launching" or time.monotonic() >= owner_deadline:
            raise WatchProtocolError("owner claim never bound the running worker")
        time.sleep(0.01)
    errors: list[str] = []
    retained_worker_handles: list[tuple[int, str]] = []
    if controller_open_error is not None:
        errors.append(controller_open_error)
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
        event_digest = journal_evidence.sha256
        complete = ready and not errors and failed_closes == 0 and root_identities_unchanged
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
        )
        try:
            _write_new(terminal_path, _canonical_bytes(terminal))
        except (FileExistsError, OSError):
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


def _watch_receipt_from_files_impl(
    request: WatchRequest,
    worker_pid: int,
    ready_path: Path,
    events_path: Path,
    terminal_path: Path,
    *,
    require_completed_owner: bool = True,
) -> WatchReceipt:
    """Load strict protocol files and convert every uncertainty to incomplete."""
    _require_windows()
    expected_paths = (
        Path(request.evidence_root) / _READY_NAME,
        Path(request.evidence_root) / _EVENTS_NAME,
        Path(request.evidence_root) / _TERMINAL_NAME,
    )
    supplied_paths = (Path(ready_path), Path(events_path), Path(terminal_path))
    for supplied, expected in zip(supplied_paths, expected_paths, strict=True):
        if supplied.absolute() != expected.absolute():
            return _make_watch_receipt(
                request,
                worker_pid,
                complete=False,
                ready=False,
                opened_root_kinds=(),
                events=(),
                event_bytes_sha256=hashlib.sha256(b"").hexdigest(),
                error="evidence paths must be confined to the exact ready/events/terminal files",
            )
        if supplied.exists():
            attributes = _kernel32.GetFileAttributesW(str(supplied))
            if attributes == 0xFFFFFFFF or attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                return _make_watch_receipt(
                    request,
                    worker_pid,
                    complete=False,
                    ready=False,
                    opened_root_kinds=(),
                    events=(),
                    event_bytes_sha256=hashlib.sha256(b"").hexdigest(),
                    error="evidence paths must be confined direct files",
                )
    errors: list[str] = []
    ready = False
    opened: tuple[str, ...] = ()
    event_bytes = b""
    journal_evidence = _JournalEvidence(0, 0, b"", 0, 0)
    events: tuple[WatcherEvent, ...] = ()
    terminal: dict[str, object] | None = None
    request_sha256 = ""
    worker_creation_time = 0
    try:
        normalized = _normalize_request(request, inspect_roots=False)
        _, request_sha256 = _request_bytes_and_sha256(normalized)
    except WatchProtocolError as error:
        normalized = request
        errors.append(str(error))
    try:
        owner = _parse_owner(
            (Path(request.evidence_root) / _OWNER_NAME).read_bytes(),
            normalized,
            request_sha256,
        )
        owner_pid = _required_int(owner["workerPid"], "owner worker PID")
        worker_creation_time = _required_int(
            owner["workerCreationTime"],
            "owner worker creation time",
        )
        if owner_pid != worker_pid:
            errors.append("owner worker PID does not match supplied worker")
        if require_completed_owner and owner["state"] != "Completed":
            errors.append(
                str(owner["error"])
                if owner["error"] is not None
                else f"owner state is not completed: {owner['state']}"
            )
    except FileNotFoundError:
        errors.append("owner record missing")
    except (OSError, WatchProtocolError) as error:
        errors.append(str(error))
    try:
        opened = _parse_ready(
            Path(ready_path).read_bytes(),
            normalized,
            worker_pid,
            request_sha256,
            worker_creation_time,
        )
        ready = True
    except FileNotFoundError:
        errors.append("ready record missing")
    except (OSError, WatchProtocolError) as error:
        errors.append(str(error))
    try:
        journal_evidence = _read_exact_journal(Path(events_path))
        event_bytes = journal_evidence.data
        events = _parse_events(event_bytes, normalized)
    except FileNotFoundError:
        errors.append("events journal missing")
    except _HandleOwnershipError:
        raise
    except (OSError, WatchProtocolError) as error:
        errors.append(str(error))
    event_digest = hashlib.sha256(event_bytes).hexdigest()
    try:
        terminal = _parse_terminal(
            Path(terminal_path).read_bytes(),
            normalized,
            worker_pid,
            request_sha256,
            worker_creation_time,
        )
    except FileNotFoundError:
        errors.append(
            "worker death before terminal record"
            if not _process_alive(worker_pid)
            else "terminal record missing"
        )
    except (OSError, WatchProtocolError) as error:
        errors.append(str(error))
    if terminal is not None:
        terminal_opened = tuple(terminal["openedRootKinds"])
        if terminal["ready"]:
            if not ready:
                errors.append("terminal claims ready without a valid ready record")
            if opened and terminal_opened != opened:
                errors.append("ready and terminal root kinds disagree")
        else:
            ready = False
            opened = terminal_opened
        if terminal["eventBytesSha256"] != event_digest:
            errors.append("event bytes SHA-256 mismatch")
        if terminal["eventByteCount"] != len(event_bytes):
            errors.append("event byte count mismatch")
        if terminal["eventCount"] != len(events):
            errors.append("event count mismatch")
        final_sequence = 0 if not events else events[-1].sequence
        if terminal["finalSequence"] != final_sequence:
            errors.append("final sequence mismatch")
        if (
            terminal["journalVolumeSerial"] != journal_evidence.volume_serial
            or terminal["journalFileId"] != journal_evidence.file_id
        ):
            errors.append("event journal identity mismatch")
        if terminal["openHandleCount"] != 0:
            errors.append(f"unclosed handle count: {terminal['openHandleCount']}")
        if terminal["ready"] and not terminal["rootIdentitiesUnchanged"]:
            errors.append("root identity changed after ready")
        if not terminal["complete"]:
            errors.append(str(terminal["error"]))
    complete = terminal is not None and bool(terminal["complete"]) and not errors
    return _make_watch_receipt(
        request,
        worker_pid,
        complete=complete,
        ready=ready,
        opened_root_kinds=opened,
        events=events,
        event_bytes_sha256=event_digest,
        error=None if complete else "; ".join(dict.fromkeys(errors)),
        request_bytes_sha256=request_sha256,
    )


def watch_receipt_from_files(
    request: WatchRequest,
    worker_pid: int,
    ready_path: Path,
    events_path: Path,
    terminal_path: Path,
) -> WatchReceipt:
    """Load evidence without allowing reconstruction uncertainty to escape."""
    evidence_key = (
        request.evidence_root.absolute()
        if isinstance(request, WatchRequest) and isinstance(request.evidence_root, Path)
        else Path.cwd() / ".invalid-watch-evidence"
    )
    with _RETAINED_AUXILIARY_HANDLES_LOCK:
        retained_handles = _RETAINED_AUXILIARY_HANDLES.pop(evidence_key, [])
    retry_errors: list[str] = []
    still_retained: list[int] = []
    for retained_handle in retained_handles:
        close_error = _close_handle(
            retained_handle,
            "retained evidence reconstruction handle",
        )
        if close_error is not None:
            retry_errors.append(close_error)
            still_retained.append(retained_handle)
    if still_retained:
        with _RETAINED_AUXILIARY_HANDLES_LOCK:
            _RETAINED_AUXILIARY_HANDLES.setdefault(evidence_key, []).extend(
                still_retained
            )
        request_sha256 = ""
        if isinstance(request, WatchRequest):
            try:
                _, request_sha256 = _request_bytes_and_sha256(request)
            except (AttributeError, TypeError, ValueError, WatchProtocolError):
                pass
        return _make_watch_receipt(
            request,
            worker_pid if type(worker_pid) is int else 0,
            complete=False,
            ready=False,
            opened_root_kinds=(),
            events=(),
            event_bytes_sha256=hashlib.sha256(b"").hexdigest(),
            error="; ".join(retry_errors),
            request_bytes_sha256=request_sha256,
        )
    try:
        return _watch_receipt_from_files_impl(
            request,
            worker_pid,
            ready_path,
            events_path,
            terminal_path,
        )
    except _HandleOwnershipError as error:
        with _RETAINED_AUXILIARY_HANDLES_LOCK:
            _RETAINED_AUXILIARY_HANDLES.setdefault(evidence_key, []).append(
                error.handle
            )
        request_id = (
            request.request_id
            if isinstance(request, WatchRequest) and type(request.request_id) is str
            else "watch-request:" + "0" * 64
        )
        request_sha256 = ""
        if isinstance(request, WatchRequest):
            try:
                _, request_sha256 = _request_bytes_and_sha256(request)
            except (AttributeError, TypeError, ValueError, WatchProtocolError):
                pass
        return _make_watch_receipt(
            request,
            worker_pid if type(worker_pid) is int else 0,
            complete=False,
            ready=False,
            opened_root_kinds=(),
            events=(),
            event_bytes_sha256=hashlib.sha256(b"").hexdigest(),
            error=f"evidence reconstruction retained an unclosed handle: {error}",
            request_bytes_sha256=request_sha256,
        )
    except (OSError, WatchProtocolError, TypeError, ValueError, RuntimeError) as error:
        request_id = (
            request.request_id
            if isinstance(request, WatchRequest) and type(request.request_id) is str
            else "watch-request:" + "0" * 64
        )
        request_sha256 = ""
        if isinstance(request, WatchRequest):
            try:
                _, request_sha256 = _request_bytes_and_sha256(request)
            except (AttributeError, TypeError, ValueError):
                pass
        return _make_watch_receipt(
            request,
            worker_pid if type(worker_pid) is int else 0,
            complete=False,
            ready=False,
            opened_root_kinds=(),
            events=(),
            event_bytes_sha256=hashlib.sha256(b"").hexdigest(),
            error=f"evidence reconstruction failed: {error}",
            request_bytes_sha256=request_sha256,
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


def _wait_process_handle(handle: int, timeout_ms: int) -> bool:
    result = _kernel32.WaitForSingleObject(handle, timeout_ms)
    if result == _WAIT_OBJECT_0:
        return True
    if result == _WAIT_TIMEOUT:
        return False
    raise WatchProtocolError(f"worker process wait failed: {result}")


def _process_identity_alive(pid: int, creation_time: int) -> bool:
    try:
        handle, observed_creation_time = _open_process_identity(pid)
    except OSError as error:
        if (
            getattr(error, "winerror", None) == _ERROR_INVALID_PARAMETER
            or getattr(error, "errno", None) == _ERROR_INVALID_PARAMETER
        ):
            return False
        raise
    try:
        alive = observed_creation_time == creation_time and not _wait_process_handle(handle, 0)
    except BaseException as error:
        close_error = _close_handle(handle, f"controller identity probe {pid}")
        if close_error is not None:
            raise _HandleOwnershipError(
                f"{error}; {close_error}",
                handle,
                f"controller identity probe {pid}",
            ) from error
        raise
    close_error = _close_handle(handle, f"controller identity probe {pid}")
    if close_error is not None:
        raise _HandleOwnershipError(close_error, handle, f"controller identity probe {pid}")
    return alive


def _incomplete_receipt(
    request: WatchRequest,
    worker_pid: int,
    error: str,
    *,
    ready: bool = False,
    opened: tuple[str, ...] = (),
) -> WatchReceipt:
    events_path = request.evidence_root / _EVENTS_NAME
    try:
        journal = _read_exact_journal(events_path)
        event_bytes = journal.data
        events = _parse_events(event_bytes, request)
    except _HandleOwnershipError as ownership_error:
        with _RETAINED_AUXILIARY_HANDLES_LOCK:
            _RETAINED_AUXILIARY_HANDLES.setdefault(
                request.evidence_root.absolute(),
                [],
            ).append(ownership_error.handle)
        event_bytes = b""
        events = ()
        error = f"{error}; {ownership_error}"
    except (OSError, WatchProtocolError):
        event_bytes = b""
        events = ()
    _, request_sha256 = _request_bytes_and_sha256(request)
    return _make_watch_receipt(
        request,
        worker_pid,
        complete=False,
        ready=ready,
        opened_root_kinds=opened,
        events=events,
        event_bytes_sha256=hashlib.sha256(event_bytes).hexdigest(),
        error=error,
        request_bytes_sha256=request_sha256,
    )


def _finalize_cleanup_complete(
    request: WatchRequest,
    worker_pid: int,
    worker_creation_time: int,
    request_sha256: str,
    owner_token: str,
    *,
    extra_errors: tuple[str, ...] = (),
) -> WatchReceipt:
    ready_path = request.evidence_root / _READY_NAME
    events_path = request.evidence_root / _EVENTS_NAME
    terminal_path = request.evidence_root / _TERMINAL_NAME
    evidence_key = request.evidence_root.absolute()
    try:
        cleanup_owner = _parse_owner(
            (request.evidence_root / _OWNER_NAME).read_bytes(), request, request_sha256
        )
        cleanup_observer_pid = _required_int(
            cleanup_owner["observerControllerPid"],
            "cleanup observer controller PID",
            minimum=1,
        )
        cleanup_observer_creation = _required_int(
            cleanup_owner["observerControllerCreationTime"],
            "cleanup observer controller creation time",
            minimum=1,
        )
    except (OSError, WatchProtocolError) as error:
        return _incomplete_receipt(
            request,
            worker_pid,
            f"cleanup owner unavailable before evidence validation: {error}",
        )

    def retain_auxiliary_recovery(message: str) -> WatchReceipt:
        with _LOCAL_WORKERS_LOCK:
            _LOCAL_WORKERS[(request.evidence_root / _REQUEST_NAME).absolute()] = (
                _LocalWorker(
                    None,
                    worker_pid,
                    request,
                    request_sha256,
                    0,
                    worker_creation_time,
                    owner_token,
                    0,
                    True,
                )
            )
        try:
            _replace_owner_state(
                request,
                request_sha256,
                owner_token,
                state="RecoveryRequired",
                worker_pid=worker_pid,
                worker_creation_time=worker_creation_time,
                error=message,
                observer_controller_pid=cleanup_observer_pid,
                observer_controller_creation_time=cleanup_observer_creation,
            )
        except (OSError, WatchProtocolError) as owner_error:
            message = f"{message}; recovery owner publication failed: {owner_error}"
        return _incomplete_receipt(request, worker_pid, message)

    with _RETAINED_AUXILIARY_HANDLES_LOCK:
        retained = _RETAINED_AUXILIARY_HANDLES.pop(evidence_key, [])
    still_retained: list[int] = []
    retry_errors: list[str] = []
    for handle in retained:
        close_error = _close_handle(handle, "retained cleanup evidence handle")
        if close_error is not None:
            still_retained.append(handle)
            retry_errors.append(close_error)
    if still_retained:
        with _RETAINED_AUXILIARY_HANDLES_LOCK:
            _RETAINED_AUXILIARY_HANDLES.setdefault(evidence_key, []).extend(
                still_retained
            )
        return retain_auxiliary_recovery("; ".join(retry_errors))
    try:
        provisional = _watch_receipt_from_files_impl(
            request,
            worker_pid,
            ready_path,
            events_path,
            terminal_path,
            require_completed_owner=False,
        )
    except _HandleOwnershipError as error:
        with _RETAINED_AUXILIARY_HANDLES_LOCK:
            _RETAINED_AUXILIARY_HANDLES.setdefault(evidence_key, []).append(
                error.handle
            )
        return retain_auxiliary_recovery(
            f"evidence reconstruction retained an unclosed handle: {error}"
        )
    except (OSError, WatchProtocolError, TypeError, ValueError, RuntimeError) as error:
        return _incomplete_receipt(
            request,
            worker_pid,
            f"evidence reconstruction failed after process cleanup: {error}",
        )
    for extra_error in extra_errors:
        provisional = _force_incomplete(provisional, extra_error)
    if not provisional.complete:
        return provisional
    try:
        _replace_owner_state(
            request,
            request_sha256,
            owner_token,
            state="Completed",
            worker_pid=worker_pid,
            worker_creation_time=worker_creation_time,
            error=None,
            observer_controller_pid=cleanup_observer_pid,
            observer_controller_creation_time=cleanup_observer_creation,
            completed_receipt=provisional,
        )
    except (OSError, WatchProtocolError) as error:
        return _force_incomplete(
            provisional,
            f"completed owner publication failed: {error}",
        )
    return provisional


def _owner_canonical_request_path(request_path: Path) -> Path:
    supplied = Path(request_path)
    owner_bytes = (supplied.parent / _OWNER_NAME).read_bytes()
    owner = _object_from_bytes(owner_bytes, "owner record")
    request_value = owner.get("request")
    if type(request_value) is not dict:
        raise WatchProtocolError("owner record immutable request is unavailable")
    request_bytes = _canonical_bytes(request_value)
    request = _request_from_bytes(request_bytes)
    request_sha256 = hashlib.sha256(request_bytes).hexdigest()
    _parse_owner(owner_bytes, request, request_sha256)
    if not os.path.samefile(supplied.parent, request.evidence_root):
        raise WatchProtocolError("owner request path identity mismatch")
    return request.evidence_root / _REQUEST_NAME


def _filesystem_canonical_request_path(request_path: Path) -> Path:
    supplied = Path(request_path)
    try:
        return supplied.resolve(strict=True)
    except FileNotFoundError:
        return supplied.parent.resolve(strict=True) / _REQUEST_NAME


def _stop_mutex_name(request_path: Path) -> str:
    supplied = Path(request_path)
    try:
        identity_path = _owner_canonical_request_path(supplied)
    except (OSError, WatchProtocolError):
        identity_path = _filesystem_canonical_request_path(supplied)
    canonical = os.path.normcase(os.path.abspath(os.fspath(identity_path)))
    identity = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"Local\\ModLab-Watch-Stop-{identity}"


def _retry_synchronization_handles() -> None:
    with _RETAINED_SYNCHRONIZATION_HANDLES_LOCK:
        retained = tuple(_RETAINED_SYNCHRONIZATION_HANDLES)
        _RETAINED_SYNCHRONIZATION_HANDLES.clear()
        retained_owned = tuple(_RETAINED_OWNED_MUTEX_HANDLES)
        _RETAINED_OWNED_MUTEX_HANDLES.clear()
    still_retained: list[int] = []
    for handle in retained:
        close_error = _close_handle(handle, "retained stop synchronization handle")
        if close_error is not None:
            still_retained.append(handle)
            _SYNCHRONIZATION_CLEANUP_WARNINGS.append(close_error)
    if still_retained:
        with _RETAINED_SYNCHRONIZATION_HANDLES_LOCK:
            _RETAINED_SYNCHRONIZATION_HANDLES.extend(still_retained)
    still_owned: list[tuple[int, int]] = []
    current_thread = threading.get_ident()
    for handle, owner_thread in retained_owned:
        acquired_for_release = owner_thread == current_thread
        if not acquired_for_release:
            wait_result = _kernel32.WaitForSingleObject(handle, 0)
            if wait_result in {_WAIT_OBJECT_0, _WAIT_ABANDONED}:
                acquired_for_release = True
            elif wait_result == _WAIT_TIMEOUT:
                still_owned.append((handle, owner_thread))
                continue
            else:
                _SYNCHRONIZATION_CLEANUP_WARNINGS.append(
                    f"retained stop mutex wait failed: {wait_result}"
                )
                still_owned.append((handle, owner_thread))
                continue
        if not _kernel32.ReleaseMutex(handle):
            _SYNCHRONIZATION_CLEANUP_WARNINGS.append(
                str(_winerror("could not release retained stop mutex"))
            )
            still_owned.append((handle, owner_thread))
            continue
        close_error = _close_handle(handle, "released retained stop mutex")
        if close_error is not None:
            still_retained.append(handle)
            _SYNCHRONIZATION_CLEANUP_WARNINGS.append(close_error)
    if still_owned or still_retained:
        with _RETAINED_SYNCHRONIZATION_HANDLES_LOCK:
            _RETAINED_OWNED_MUTEX_HANDLES.extend(still_owned)
            for handle in still_retained:
                if handle not in _RETAINED_SYNCHRONIZATION_HANDLES:
                    _RETAINED_SYNCHRONIZATION_HANDLES.append(handle)


def stop_watch(request_path: Path) -> WatchReceipt:
    """Serialize stop/recovery across threads and controller processes."""
    _retry_synchronization_handles()
    supplied_path = Path(request_path)
    try:
        request_key = _owner_canonical_request_path(supplied_path).absolute()
    except (OSError, WatchProtocolError):
        request_key = _filesystem_canonical_request_path(supplied_path).absolute()
    mutex_name = _stop_mutex_name(request_key)
    mutex_handle = _kernel32.CreateMutexW(None, False, mutex_name)
    if not mutex_handle:
        raise _winerror("could not create per-request stop mutex")
    result = _kernel32.WaitForSingleObject(mutex_handle, _INFINITE)
    if result not in {_WAIT_OBJECT_0, _WAIT_ABANDONED}:
        close_error = _close_handle(mutex_handle, "unacquired stop mutex")
        detail = f"per-request stop mutex wait failed: {result}"
        if close_error is not None:
            with _RETAINED_SYNCHRONIZATION_HANDLES_LOCK:
                _RETAINED_SYNCHRONIZATION_HANDLES.append(mutex_handle)
                _SYNCHRONIZATION_CLEANUP_WARNINGS.append(close_error)
            detail = f"{detail}; {close_error}"
        raise WatchProtocolError(detail)
    receipt: WatchReceipt | None = None
    release_error: str | None = None
    released = False
    try:
        receipt = _stop_watch_locked(request_key)
    finally:
        if not _kernel32.ReleaseMutex(mutex_handle):
            release_error = str(_winerror("could not release per-request stop mutex"))
            with _RETAINED_SYNCHRONIZATION_HANDLES_LOCK:
                _RETAINED_OWNED_MUTEX_HANDLES.append(
                    (mutex_handle, threading.get_ident())
                )
        else:
            released = True
        close_error = (
            _close_handle(mutex_handle, "per-request stop mutex")
            if released
            else None
        )
        if close_error is not None:
            if released:
                with _RETAINED_SYNCHRONIZATION_HANDLES_LOCK:
                    _RETAINED_SYNCHRONIZATION_HANDLES.append(mutex_handle)
                    _SYNCHRONIZATION_CLEANUP_WARNINGS.append(close_error)
            else:
                with _RETAINED_SYNCHRONIZATION_HANDLES_LOCK:
                    _RETAINED_SYNCHRONIZATION_HANDLES.append(mutex_handle)
                    _SYNCHRONIZATION_CLEANUP_WARNINGS.append(close_error)
                release_error = (
                    close_error
                    if release_error is None
                    else f"{release_error}; {close_error}"
                )
    if receipt is None:
        raise WatchProtocolError(release_error or "stop mutex failed before receipt")
    if release_error is not None:
        receipt = _force_incomplete(receipt, release_error)
    return receipt


def _stop_watch_locked(request_path: Path) -> WatchReceipt:
    """Request exact-worker cancellation, wait for exit, and load its receipt."""
    _require_windows()
    request_key = Path(request_path).absolute()
    with _LOCAL_WORKERS_LOCK:
        local_worker = _LOCAL_WORKERS.get(request_key)
    evidence_root = request_key.parent
    request_error: str | None = None
    try:
        request = _load_request_path(request_key)
    except (OSError, WatchProtocolError) as error:
        if local_worker is None:
            try:
                request = _request_from_owner_unbound(
                    (evidence_root / _OWNER_NAME).read_bytes(), request_key
                )
            except (OSError, WatchProtocolError) as owner_error:
                fallback = WatchRequest(
                    "watch-request:" + "0" * 64,
                    "watch-session:" + "0" * 64,
                    "containment-run:" + "0" * 32,
                    ContainmentScenario.MERGE_EXISTING,
                    evidence_root,
                    evidence_root / _STOP_NAME,
                    (),
                )
                try:
                    _write_new(fallback.stop_token_path, b"stop\n")
                except (FileExistsError, OSError):
                    pass
                return _make_watch_receipt(
                    fallback,
                    0,
                    complete=False,
                    ready=False,
                    opened_root_kinds=(),
                    events=(),
                    event_bytes_sha256=hashlib.sha256(b"").hexdigest(),
                    error=f"request and owner records unavailable during stop: {error}; {owner_error}",
                )
        else:
            request = local_worker.request
        request_error = f"request record unavailable during stop: {error}"
    ready_path = request.evidence_root / _READY_NAME
    events_path = request.evidence_root / _EVENTS_NAME
    terminal_path = request.evidence_root / _TERMINAL_NAME
    _, request_sha256 = _request_bytes_and_sha256(request)
    owner_error: str | None = None
    owner_token = local_worker.owner_token if local_worker is not None else ""
    try:
        owner = _parse_owner(
            (request.evidence_root / _OWNER_NAME).read_bytes(),
            request,
            request_sha256,
        )
        owner_state = str(owner["state"])
        owner_token = _required_text(owner["ownerToken"], "owner token")
        worker_pid = _required_int(owner["workerPid"], "owner worker PID")
        worker_creation_time = _required_int(
            owner["workerCreationTime"],
            "owner worker creation time",
        )
    except (FileNotFoundError, OSError, WatchProtocolError) as error:
        if local_worker is None:
            durable_owner_recovered = False
            try:
                durable_request = _request_from_owner_unbound(
                    (evidence_root / _OWNER_NAME).read_bytes(), request_key
                )
                if durable_request != request:
                    request_error = (
                        "request record disagrees with immutable owner request"
                    )
                    request = durable_request
                    ready_path = request.evidence_root / _READY_NAME
                    events_path = request.evidence_root / _EVENTS_NAME
                    terminal_path = request.evidence_root / _TERMINAL_NAME
                    _, request_sha256 = _request_bytes_and_sha256(request)
                    owner = _parse_owner(
                        (request.evidence_root / _OWNER_NAME).read_bytes(),
                        request,
                        request_sha256,
                    )
                    owner_state = str(owner["state"])
                    owner_token = _required_text(owner["ownerToken"], "owner token")
                    worker_pid = _required_int(owner["workerPid"], "owner worker PID")
                    worker_creation_time = _required_int(
                        owner["workerCreationTime"],
                        "owner worker creation time",
                    )
                    durable_owner_recovered = True
            except (OSError, WatchProtocolError):
                pass
            if durable_owner_recovered:
                owner_error = request_error
            else:
                try:
                    worker_pid, worker_creation_time = _worker_identity_from_ready(
                        ready_path.read_bytes(), request, request_sha256
                    )
                    owner_token = secrets.token_hex(32)
                    _replace_owner_state(
                        request,
                        request_sha256,
                        owner_token,
                        state="Running",
                        worker_pid=worker_pid,
                        worker_creation_time=worker_creation_time,
                        error=None,
                    )
                    owner = _parse_owner(
                        (request.evidence_root / _OWNER_NAME).read_bytes(),
                        request,
                        request_sha256,
                    )
                except (FileNotFoundError, OSError, WatchProtocolError) as recovery_error:
                    return _incomplete_receipt(
                        request,
                        0,
                        f"owner record unavailable during stop: {error}; "
                        f"ready identity recovery failed: {recovery_error}",
                    )
                owner_error = f"owner record unavailable during stop: {error}"
                owner_state = "Running"
        else:
            owner_error = f"owner record unavailable during stop: {error}"
            owner_state = "Running"
            worker_pid = local_worker.worker_pid
            worker_creation_time = local_worker.process_creation_time
    if owner_state == "CleanupComplete":
        with _LOCAL_WORKERS_LOCK:
            _LOCAL_WORKERS.pop(request_key, None)
        return _finalize_cleanup_complete(
            request,
            worker_pid,
            worker_creation_time,
            request_sha256,
            owner_token,
        )
    if owner_state == "ExitObserved" and not (
        local_worker is not None and not local_worker.cleanup_complete
    ):
        observer_pid = _required_int(
            owner["observerControllerPid"], "observer controller PID", minimum=1
        )
        observer_creation = _required_int(
            owner["observerControllerCreationTime"],
            "observer controller creation time",
            minimum=1,
        )
        local_exit_proof = local_worker is not None and local_worker.cleanup_complete
        if not local_exit_proof:
            try:
                observer_alive = _process_identity_alive(observer_pid, observer_creation)
            except _HandleOwnershipError as error:
                with _RETAINED_AUXILIARY_HANDLES_LOCK:
                    _RETAINED_AUXILIARY_HANDLES.setdefault(
                        request.evidence_root.absolute(), []
                    ).append(error.handle)
                return _incomplete_receipt(request, worker_pid, str(error))
            except (OSError, WatchProtocolError) as error:
                return _incomplete_receipt(
                    request,
                    worker_pid,
                    f"observer identity proof failed: {error}",
                )
            if observer_alive:
                return _incomplete_receipt(
                    request,
                    worker_pid,
                    "observer remains live without local cleanup proof",
                )
        try:
            _replace_owner_state(
                request,
                request_sha256,
                owner_token,
                state="CleanupComplete",
                worker_pid=worker_pid,
                worker_creation_time=worker_creation_time,
                error=None,
                observer_controller_pid=observer_pid,
                observer_controller_creation_time=observer_creation,
            )
        except (OSError, WatchProtocolError) as error:
            return _incomplete_receipt(
                request,
                worker_pid,
                f"cleanup-complete owner publication failed: {error}",
            )
        with _LOCAL_WORKERS_LOCK:
            _LOCAL_WORKERS.pop(request_key, None)
        return _finalize_cleanup_complete(
            request,
            worker_pid,
            worker_creation_time,
            request_sha256,
            owner_token,
        )
    if owner_state in {"Starting", "RecoveryRequired"} and local_worker is None:
        retaining_pid = _required_int(
            owner["retainingControllerPid"], "retaining controller PID", minimum=1
        )
        retaining_creation = _required_int(
            owner["retainingControllerCreationTime"],
            "retaining controller creation time",
            minimum=1,
        )
        try:
            retaining_alive = _process_identity_alive(retaining_pid, retaining_creation)
        except _HandleOwnershipError as error:
            with _RETAINED_AUXILIARY_HANDLES_LOCK:
                _RETAINED_AUXILIARY_HANDLES.setdefault(
                    request.evidence_root.absolute(), []
                ).append(error.handle)
            return _incomplete_receipt(request, worker_pid, str(error))
        except (OSError, WatchProtocolError) as error:
            return _incomplete_receipt(
                request,
                worker_pid,
                f"retaining controller identity proof failed: {error}",
            )
        current_controller_pid, current_controller_creation = (
            _current_controller_identity()
        )
        same_starting_controller = owner_state == "Starting" and (
            retaining_pid == current_controller_pid
            and retaining_creation == current_controller_creation
        )
        if retaining_alive and not same_starting_controller:
            return _incomplete_receipt(
                request,
                worker_pid,
                "retaining controller remains live with inaccessible recovery handles",
            )
        if (
            not retaining_alive
            and owner_state == "RecoveryRequired"
            and owner["observerControllerPid"]
        ):
            observer_pid = _required_int(
                owner["observerControllerPid"],
                "observer controller PID",
                minimum=1,
            )
            observer_creation = _required_int(
                owner["observerControllerCreationTime"],
                "observer controller creation time",
                minimum=1,
            )
            try:
                _replace_owner_state(
                    request,
                    request_sha256,
                    owner_token,
                    state="CleanupComplete",
                    worker_pid=worker_pid,
                    worker_creation_time=worker_creation_time,
                    error=None,
                    observer_controller_pid=observer_pid,
                    observer_controller_creation_time=observer_creation,
                )
            except (OSError, WatchProtocolError) as error:
                return _incomplete_receipt(
                    request,
                    worker_pid,
                    f"cleanup-complete owner publication failed: {error}",
                )
            return _finalize_cleanup_complete(
                request,
                worker_pid,
                worker_creation_time,
                request_sha256,
                owner_token,
            )
    if local_worker is not None:
        if owner_state in {"Starting", "RecoveryRequired"}:
            current_controller_pid, current_controller_creation = (
                _current_controller_identity()
            )
            if (
                owner["retainingControllerPid"] != current_controller_pid
                or owner["retainingControllerCreationTime"]
                != current_controller_creation
            ):
                return _incomplete_receipt(
                    request,
                    worker_pid,
                    "recovery handles belong to a different live controller identity",
                )
        if local_worker.cleanup_complete:
            try:
                _replace_owner_state(
                    request,
                    request_sha256,
                    owner_token,
                    state="CleanupComplete",
                    worker_pid=worker_pid,
                    worker_creation_time=worker_creation_time,
                    error=None,
                )
            except (OSError, WatchProtocolError) as error:
                return _incomplete_receipt(
                    request,
                    worker_pid,
                    f"cleanup-complete retry publication failed: {error}",
                )
            with _LOCAL_WORKERS_LOCK:
                _LOCAL_WORKERS.pop(request_key, None)
            return _finalize_cleanup_complete(
                request,
                worker_pid,
                worker_creation_time,
                request_sha256,
                owner_token,
            )
        try:
            _write_new(request.stop_token_path, b"stop\n")
        except FileExistsError:
            pass
        process_handle = local_worker.process_handle or local_worker.popen_handle
        if local_worker.process_creation_time == 0 and process_handle:
            try:
                discovered_creation_time = _process_handle_creation_time(
                    process_handle,
                    local_worker.worker_pid,
                )
                recovery_message = (
                    str(owner["error"])
                    if "owner" in locals() and owner.get("error") is not None
                    else "recovered previously unknown worker creation time"
                )
                _replace_owner_state(
                    request,
                    request_sha256,
                    owner_token,
                    state="RecoveryRequired",
                    worker_pid=local_worker.worker_pid,
                    worker_creation_time=discovered_creation_time,
                    error=recovery_message,
                )
            except (OSError, WatchProtocolError) as error:
                return _incomplete_receipt(
                    request,
                    local_worker.worker_pid,
                    f"retained worker creation time could not be established: {error}",
                )
            local_worker.process_creation_time = discovered_creation_time
            worker_creation_time = discovered_creation_time
        if (
            worker_pid != local_worker.worker_pid
            or worker_creation_time != local_worker.process_creation_time
        ):
            owner_error = "owner worker PID or creation time mismatches local ownership"
        if not process_handle:
            return _incomplete_receipt(
                request,
                worker_pid,
                "local worker ownership has no retained native process handle",
            )
        owns_temporary_handle = False
        try:
            _verify_retained_process_handle(
                process_handle,
                worker_pid,
                worker_creation_time,
            )
        except (OSError, WatchProtocolError) as error:
            return _incomplete_receipt(
                request,
                worker_pid,
                f"retained exact worker process could not be verified: {error}",
            )
    elif owner_state == "LaunchFailed":
        receipt = watch_receipt_from_files(
            request,
            0,
            ready_path,
            events_path,
            terminal_path,
        )
        return _force_incomplete(receipt, str(owner["error"]))
    elif owner_state == "Completed":
        return _completed_receipt_from_document(
            owner["completedReceipt"],
            request,
            worker_pid,
            request_sha256,
        )
    else:
        try:
            _write_new(request.stop_token_path, b"stop\n")
        except FileExistsError:
            pass
        try:
            process_handle = _verified_process_handle(worker_pid, worker_creation_time)
        except _HandleOwnershipError as error:
            with _LOCAL_WORKERS_LOCK:
                _LOCAL_WORKERS[request_key] = _LocalWorker(
                    None,
                    worker_pid,
                    request,
                    request_sha256,
                    error.handle,
                    worker_creation_time,
                    owner_token,
                )
            try:
                _replace_owner_state(
                    request,
                    request_sha256,
                    owner_token,
                    state="RecoveryRequired",
                    worker_pid=worker_pid,
                    worker_creation_time=worker_creation_time,
                    error=str(error),
                )
            except (OSError, WatchProtocolError) as owner_write_error:
                return _incomplete_receipt(
                    request,
                    worker_pid,
                    f"{error}; recovery owner publication failed: {owner_write_error}",
                )
            return _incomplete_receipt(request, worker_pid, str(error))
        except (OSError, WatchProtocolError) as error:
            receipt = watch_receipt_from_files(
                request,
                worker_pid,
                ready_path,
                events_path,
                terminal_path,
            )
            return _force_incomplete(
                receipt,
                f"exact worker process could not be verified: {error}",
            )
        owns_temporary_handle = True

    def release_temporary_handle() -> str | None:
        if not owns_temporary_handle:
            return None
        close_error = _close_handle(process_handle, f"worker process {worker_pid}")
        if close_error is not None:
            with _LOCAL_WORKERS_LOCK:
                _LOCAL_WORKERS[request_key] = _LocalWorker(
                    None,
                    worker_pid,
                    request,
                    request_sha256,
                    process_handle,
                    worker_creation_time,
                    owner_token,
                )
            try:
                _replace_owner_state(
                    request,
                    request_sha256,
                    owner_token,
                    state="RecoveryRequired",
                    worker_pid=worker_pid,
                    worker_creation_time=worker_creation_time,
                    error=close_error,
                )
            except (OSError, WatchProtocolError) as error:
                return f"{close_error}; recovery owner publication failed: {error}"
        return close_error

    try:
        exited = _wait_process_handle(process_handle, 30_000)
    except WatchProtocolError as error:
        close_error = release_temporary_handle()
        message = str(error)
        if close_error is not None:
            message = f"{message}; {close_error}"
        return _incomplete_receipt(request, worker_pid, message)
    if not exited:
        close_error = release_temporary_handle()
        message = "watch worker did not stop after CancelIoEx request"
        if close_error is not None:
            message = f"{message}; {close_error}"
        return _incomplete_receipt(
            request,
            worker_pid,
            message,
            ready=True,
            opened=ROOT_KINDS,
        )
    if local_worker is not None:
        if local_worker.process is not None:
            try:
                local_worker.process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired) as error:
                return _incomplete_receipt(
                    request,
                    worker_pid,
                    f"exact local worker reap failed: {error}",
                )
            if not local_worker.popen_handle:
                local_worker.popen_handle = _detach_local_popen_handle(
                    local_worker.process
                )
    observer_pid, observer_creation_time = _current_controller_identity()
    try:
        _replace_owner_state(
            request,
            request_sha256,
            owner_token,
            state="ExitObserved",
            worker_pid=worker_pid,
            worker_creation_time=worker_creation_time,
            error=None,
            observer_controller_pid=observer_pid,
            observer_controller_creation_time=observer_creation_time,
        )
    except (OSError, WatchProtocolError) as error:
        if owns_temporary_handle:
            with _LOCAL_WORKERS_LOCK:
                _LOCAL_WORKERS[request_key] = _LocalWorker(
                    None,
                    worker_pid,
                    request,
                    request_sha256,
                    process_handle,
                    worker_creation_time,
                    owner_token,
                )
        return _incomplete_receipt(
            request,
            worker_pid,
            f"completion owner publication failed: {error}",
        )
    close_errors: list[str] = []
    handles_to_close: list[tuple[int, str, str | None]] = [
        (process_handle, f"worker process {worker_pid}", None)
    ]
    if local_worker is not None and local_worker.popen_handle:
        handles_to_close.append(
            (
                local_worker.popen_handle,
                f"local Popen process {worker_pid}",
                "popen_handle",
            )
        )
    seen_handles: set[int] = set()
    for owned_handle, label, field_name in handles_to_close:
        if not owned_handle or owned_handle in seen_handles:
            continue
        seen_handles.add(owned_handle)
        owned_close_error = _close_handle(owned_handle, label)
        if owned_close_error is not None:
            close_errors.append(owned_close_error)
            continue
        if local_worker is not None:
            if field_name == "popen_handle":
                local_worker.popen_handle = 0
            elif local_worker.process_handle == owned_handle:
                local_worker.process_handle = 0
            elif local_worker.popen_handle == owned_handle:
                local_worker.popen_handle = 0
    close_error = "; ".join(close_errors) if close_errors else None
    if close_error is not None:
        if owns_temporary_handle:
            with _LOCAL_WORKERS_LOCK:
                _LOCAL_WORKERS[request_key] = _LocalWorker(
                    None,
                    worker_pid,
                    request,
                    request_sha256,
                    process_handle,
                    worker_creation_time,
                    owner_token,
                )
        try:
            _replace_owner_state(
                request,
                request_sha256,
                owner_token,
                state="RecoveryRequired",
                worker_pid=worker_pid,
                worker_creation_time=worker_creation_time,
                error=close_error,
                observer_controller_pid=observer_pid,
                observer_controller_creation_time=observer_creation_time,
            )
        except (OSError, WatchProtocolError) as error:
            close_error = f"{close_error}; recovery owner publication failed: {error}"
        receipt = watch_receipt_from_files(
            request,
            worker_pid,
            ready_path,
            events_path,
            terminal_path,
        )
        return _force_incomplete(receipt, close_error)

    with _LOCAL_WORKERS_LOCK:
        _LOCAL_WORKERS[request_key] = _LocalWorker(
            None,
            worker_pid,
            request,
            request_sha256,
            0,
            worker_creation_time,
            owner_token,
            0,
            True,
        )
    try:
        _replace_owner_state(
            request,
            request_sha256,
            owner_token,
            state="CleanupComplete",
            worker_pid=worker_pid,
            worker_creation_time=worker_creation_time,
            error=None,
            observer_controller_pid=observer_pid,
            observer_controller_creation_time=observer_creation_time,
        )
    except (OSError, WatchProtocolError) as error:
        return _incomplete_receipt(
            request,
            worker_pid,
            f"cleanup-complete owner publication failed: {error}",
        )
    with _LOCAL_WORKERS_LOCK:
        _LOCAL_WORKERS.pop(request_key, None)
    extra_errors = tuple(
        error
        for error in (request_error, owner_error)
        if error is not None
    )
    return _finalize_cleanup_complete(
        request,
        worker_pid,
        worker_creation_time,
        request_sha256,
        owner_token,
        extra_errors=extra_errors,
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
    """Require both loss-free zero-event evidence and equal caller manifests."""
    if type(receipt) is not WatchReceipt:
        return False
    if (
        not receipt.complete
        or not receipt.ready
        or receipt.error is not None
        or receipt.opened_root_kinds != ROOT_KINDS
        or re.fullmatch(r"[0-9a-f]{64}", receipt.request_bytes_sha256) is None
    ):
        return False
    expected_sequence = 1
    event_bytes_parts: list[bytes] = []
    for event in receipt.events:
        if type(event) is not WatcherEvent or event.sequence != expected_sequence:
            return False
        if event.root_kind not in _ROOT_KIND_SET or event.action not in _ACTIONS.values():
            return False
        try:
            _validate_relative_path(event.relative_path)
        except WatchProtocolError:
            return False
        event_bytes_parts.append(
            _canonical_bytes(
                {
                    "action": event.action,
                    "relativePath": event.relative_path,
                    "rootKind": event.root_kind,
                    "sequence": event.sequence,
                }
            )
        )
        expected_sequence += 1
    recomputed_event_hash = hashlib.sha256(b"".join(event_bytes_parts)).hexdigest()
    return (
        not receipt.events
        and receipt.event_bytes_sha256 == recomputed_event_hash
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
    except (OSError, WatchProtocolError):
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
