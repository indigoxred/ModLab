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

from modlab.validation.mo2_containment_model import ProtectedState, TreeIdentity, WatcherEvent


_SCHEMA_VERSION = 1
_REQUEST_NAME = "request.json"
_READY_NAME = "ready.json"
_EVENTS_NAME = "events.ndjson"
_TERMINAL_NAME = "terminal.json"
_STOP_NAME = "stop.token"
_REQUEST_ID = re.compile(r"watch-request:[0-9a-f]{64}\Z")

ROOT_KINDS = (
    "SourceMods",
    "LabProfile",
    "PlayProfile",
    "Downloads",
    "Overwrite",
    "BoundedGame",
    "ExternalLocalLow",
    "ExternalTempLow",
)
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
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_DELETE = 0x00000004
_OPEN_EXISTING = 3
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
_WAIT_TIMEOUT = 258
_INFINITE = 0xFFFFFFFF
_SYNCHRONIZE = 0x00100000
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_BUFFER_SIZE = 64 * 1024
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class WatchProtocolError(RuntimeError):
    """The watch request or retained evidence is unsafe or noncanonical."""


@dataclass(frozen=True)
class WatchRoot:
    root_kind: str
    path: Path
    volume_serial: int
    file_id: int


@dataclass(frozen=True)
class WatchRequest:
    request_id: str
    evidence_root: Path
    stop_token_path: Path
    roots: tuple[WatchRoot, ...]


@dataclass(frozen=True)
class WatchReceipt:
    request_id: str
    worker_pid: int
    complete: bool
    ready: bool
    opened_root_kinds: tuple[str, ...]
    events: tuple[WatcherEvent, ...]
    event_bytes_sha256: str
    error: str | None


@dataclass
class _LocalWorker:
    process: subprocess.Popen[bytes]
    request: WatchRequest


_LOCAL_WORKERS: dict[Path, _LocalWorker] = {}
_LOCAL_WORKERS_LOCK = threading.Lock()


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
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL


@dataclass
class _WatchState:
    root: WatchRoot
    directory_path: Path
    label: str
    directory_handle: int
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


class _EventJournal:
    def __init__(self, path: Path) -> None:
        flags = os.O_WRONLY | os.O_APPEND
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        self._descriptor = os.open(path, flags)
        self._lock = threading.Lock()
        self._sequence = 0

    def append(self, root_kind: str, records: tuple[tuple[str, str], ...]) -> None:
        with self._lock:
            for action, relative_path in records:
                self._sequence += 1
                data = _canonical_bytes(
                    {
                        "action": action,
                        "relativePath": relative_path,
                        "rootKind": root_kind,
                        "sequence": self._sequence,
                    }
                )
                written = os.write(self._descriptor, data)
                if written != len(data):
                    raise OSError("atomic event append was incomplete")
            os.fsync(self._descriptor)

    def close(self) -> str | None:
        if self._descriptor < 0:
            return None
        descriptor = self._descriptor
        self._descriptor = -1
        try:
            os.close(descriptor)
        except OSError as error:
            return f"unclosed handle: event journal ({error})"
        return None


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
        raise WatchProtocolError(close_error)
    return WatchRoot(root_kind, supplied, volume_serial, file_id)


def _normalize_request(request: WatchRequest, *, inspect_roots: bool) -> WatchRequest:
    if not isinstance(request, WatchRequest):
        raise WatchProtocolError("request must be WatchRequest")
    if not isinstance(request.request_id, str) or not _REQUEST_ID.fullmatch(request.request_id):
        raise WatchProtocolError("request ID must be watch-request plus lowercase SHA-256")
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
    identities: set[tuple[int, int]] = set()
    root_kinds: set[str] = set()
    for root in request.roots:
        if not isinstance(root, WatchRoot):
            raise WatchProtocolError("roots must contain WatchRoot values")
        if root.root_kind not in _ROOT_KIND_SET:
            raise WatchProtocolError(f"invalid root kind: {root.root_kind!r}")
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
        identity = (root.volume_serial, root.file_id)
        if identity in identities:
            if inspect_roots:
                continue
            raise WatchProtocolError("serialized roots must have unique identities")
        if root.root_kind in root_kinds:
            raise WatchProtocolError(f"duplicate root kind: {root.root_kind}")
        root_kinds.add(root.root_kind)
        identities.add(identity)
        normalized.append(WatchRoot(root.root_kind, path, root.volume_serial, root.file_id))
    return WatchRequest(request.request_id, evidence_root, expected_stop, tuple(normalized))


def _request_document(request: WatchRequest) -> dict[str, object]:
    return {
        "evidenceRoot": str(request.evidence_root),
        "requestId": request.request_id,
        "roots": [
            {
                "fileId": root.file_id,
                "path": str(root.path),
                "rootKind": root.root_kind,
                "volumeSerial": root.volume_serial,
            }
            for root in request.roots
        ],
        "schemaVersion": _SCHEMA_VERSION,
        "stopTokenPath": str(request.stop_token_path),
    }


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
    value = _object_from_bytes(data, "request")
    _exact_fields(
        value,
        {"evidenceRoot", "requestId", "roots", "schemaVersion", "stopTokenPath"},
        "request",
    )
    if value["schemaVersion"] != _SCHEMA_VERSION:
        raise WatchProtocolError("unsupported request schema version")
    roots_value = value["roots"]
    if type(roots_value) is not list:
        raise WatchProtocolError("request roots must be an array")
    roots: list[WatchRoot] = []
    for item in roots_value:
        if type(item) is not dict:
            raise WatchProtocolError("request root must be an object")
        _exact_fields(item, {"fileId", "path", "rootKind", "volumeSerial"}, "request root")
        roots.append(
            WatchRoot(
                _required_text(item["rootKind"], "root kind"),
                Path(_required_text(item["path"], "root path")),
                _required_int(item["volumeSerial"], "volume serial"),
                _required_int(item["fileId"], "file ID"),
            )
        )
    request = WatchRequest(
        _required_text(value["requestId"], "request ID"),
        Path(_required_text(value["evidenceRoot"], "evidence root")),
        Path(_required_text(value["stopTokenPath"], "stop token")),
        tuple(roots),
    )
    return _normalize_request(request, inspect_roots=False)


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


def _load_request_path(request_path: Path) -> WatchRequest:
    supplied = _reject_reparse_components(Path(request_path), "request path")
    resolved = supplied.resolve(strict=True)
    request = _request_from_bytes(resolved.read_bytes())
    if resolved != request.evidence_root / _REQUEST_NAME:
        raise WatchProtocolError("request path is not confined to its evidence root")
    return request


def _ready_document(request: WatchRequest, worker_pid: int) -> dict[str, object]:
    return {
        "openedRootKinds": [root.root_kind for root in request.roots],
        "requestId": request.request_id,
        "schemaVersion": _SCHEMA_VERSION,
        "workerPid": worker_pid,
    }


def _parse_ready(data: bytes, request: WatchRequest, worker_pid: int) -> tuple[str, ...]:
    value = _object_from_bytes(data, "ready record")
    _exact_fields(value, {"openedRootKinds", "requestId", "schemaVersion", "workerPid"}, "ready record")
    if value["schemaVersion"] != _SCHEMA_VERSION:
        raise WatchProtocolError("unsupported ready schema version")
    if value["requestId"] != request.request_id or value["workerPid"] != worker_pid:
        raise WatchProtocolError("ready record does not match request and worker")
    kinds = value["openedRootKinds"]
    if type(kinds) is not list or not all(type(kind) is str for kind in kinds):
        raise WatchProtocolError("ready root kinds must be an array of strings")
    opened = tuple(kinds)
    expected = tuple(root.root_kind for root in request.roots)
    if opened != expected:
        raise WatchProtocolError("ready root kinds do not match the request")
    return opened


def start_watch(request: WatchRequest) -> int:
    """Persist a confined request, launch its worker, and return only after ready."""
    _require_windows()
    normalized = _normalize_request(request, inspect_roots=True)
    request_path = normalized.evidence_root / _REQUEST_NAME
    ready_path = normalized.evidence_root / _READY_NAME
    events_path = normalized.evidence_root / _EVENTS_NAME
    terminal_path = normalized.evidence_root / _TERMINAL_NAME
    for path in (request_path, ready_path, events_path, terminal_path, normalized.stop_token_path):
        if path.exists():
            raise WatchProtocolError(f"watch protocol path already exists: {path.name}")
    _write_new(request_path, _canonical_bytes(_request_document(normalized)))
    _write_new(events_path, b"")
    command = (
        sys.executable,
        "-B",
        "-m",
        "modlab.validation.windows_watch",
        "--worker",
        str(request_path.resolve(strict=True)),
    )
    process = subprocess.Popen(
        command,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    request_key = request_path.absolute()
    with _LOCAL_WORKERS_LOCK:
        _LOCAL_WORKERS[request_key] = _LocalWorker(process, normalized)
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if ready_path.exists():
            try:
                _parse_ready(ready_path.read_bytes(), normalized, process.pid)
            except (OSError, WatchProtocolError) as error:
                try:
                    _write_new(normalized.stop_token_path, b"stop\n")
                except FileExistsError:
                    pass
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired as timeout_error:
                    raise WatchProtocolError(
                        "malformed ready record and worker did not stop"
                    ) from timeout_error
                with _LOCAL_WORKERS_LOCK:
                    _LOCAL_WORKERS.pop(request_key, None)
                raise
            return process.pid
        if terminal_path.exists() or process.poll() is not None:
            process.wait(timeout=5)
            with _LOCAL_WORKERS_LOCK:
                _LOCAL_WORKERS.pop(request_key, None)
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
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired as error:
        raise WatchProtocolError("watch worker did not become ready or stop") from error
    with _LOCAL_WORKERS_LOCK:
        _LOCAL_WORKERS.pop(request_key, None)
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
            with state.pending_lock:
                state.pending = False
            state.fail(f"watch completion wait failed for {state.root.root_kind}: {wait_result}")
            return
        transferred = wintypes.DWORD()
        completed = _kernel32.GetOverlappedResult(
            state.directory_handle,
            ctypes.byref(state.overlapped),
            ctypes.byref(transferred),
            False,
        )
        with state.pending_lock:
            state.pending = False
        if not completed:
            code = ctypes.get_last_error()
            if code == _ERROR_OPERATION_ABORTED and state.stopping.is_set():
                return
            state.fail(_completion_error(state.root.root_kind, code))
            return
        if transferred.value == 0:
            state.fail(f"watch overflow: ERROR_NOTIFY_ENUM_DIR ({state.root.root_kind})")
            return
        try:
            records = _parse_notification_buffer(state.buffer, transferred.value)
            if state.membership_name is not None:
                if any(
                    relative_path.casefold() == state.membership_name.casefold()
                    for _, relative_path in records
                ):
                    state.fail(
                        f"root membership changed during watch: {state.root.root_kind}"
                    )
                    return
            else:
                state.journal.append(state.root.root_kind, records)
        except (OSError, WatchProtocolError) as error:
            state.fail(f"malformed watch completion for {state.root.root_kind}: {error}")
            return


def _completion_error(root_kind: str, code: int) -> str:
    if code == _ERROR_NOTIFY_ENUM_DIR:
        return f"watch overflow: ERROR_NOTIFY_ENUM_DIR ({root_kind})"
    return f"watch completion failed for {root_kind}: WinError {code} ({ctypes.FormatError(code)})"


def _verify_root_identity(root: WatchRoot) -> tuple[bool, str | None, int]:
    handle = 0
    try:
        attributes = _kernel32.GetFileAttributesW(str(root.path))
        if attributes == 0xFFFFFFFF or attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            return False, None, 0
        handle = _open_directory(root.path, overlapped=False)
        volume_serial, file_id, handle_attributes = _handle_identity(handle, root.path)
        unchanged = (
            bool(handle_attributes & _FILE_ATTRIBUTE_DIRECTORY)
            and not bool(handle_attributes & _FILE_ATTRIBUTE_REPARSE_POINT)
            and (volume_serial, file_id) == (root.volume_serial, root.file_id)
        )
    except (OSError, WatchProtocolError):
        unchanged = False
    close_error = _close_handle(handle, f"identity verification {root.root_kind}")
    return unchanged, close_error, int(close_error is not None)


def _terminal_document(
    request: WatchRequest,
    *,
    worker_pid: int,
    complete: bool,
    ready: bool,
    event_digest: str,
    errors: list[str],
    open_handle_count: int,
    root_identities_unchanged: bool,
    opened_root_kinds: tuple[str, ...],
) -> dict[str, object]:
    return {
        "complete": complete,
        "error": None if not errors else "; ".join(errors),
        "eventBytesSha256": event_digest,
        "openHandleCount": open_handle_count,
        "openedRootKinds": list(opened_root_kinds),
        "ready": ready,
        "requestId": request.request_id,
        "rootIdentitiesUnchanged": root_identities_unchanged,
        "schemaVersion": _SCHEMA_VERSION,
        "workerPid": worker_pid,
    }


def run_watch_worker(request_path: Path) -> int:
    """Run the confined worker protocol for one persisted request."""
    _require_windows()
    request = _load_request_path(request_path)
    ready_path = request.evidence_root / _READY_NAME
    events_path = request.evidence_root / _EVENTS_NAME
    terminal_path = request.evidence_root / _TERMINAL_NAME
    worker_pid = os.getpid()
    errors: list[str] = []
    errors_lock = threading.Lock()
    stopping = threading.Event()
    states: list[_WatchState] = []
    opened_kinds: list[str] = []
    journal: _EventJournal | None = None
    ready = False
    root_identities_unchanged = False
    failed_closes = 0
    try:
        if not events_path.is_file() or events_path.stat().st_size != 0:
            raise WatchProtocolError("events journal must exist and be empty before worker start")
        journal = _EventJournal(events_path)
        for root in request.roots:
            handle = _open_directory(root.path, overlapped=True)
            try:
                volume_serial, file_id, attributes = _handle_identity(handle, root.path)
                if not attributes & _FILE_ATTRIBUTE_DIRECTORY:
                    raise WatchProtocolError(f"watched root is not a directory: {root.root_kind}")
                if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                    raise WatchProtocolError(f"watched root became reparse: {root.root_kind}")
                if (volume_serial, file_id) != (root.volume_serial, root.file_id):
                    raise WatchProtocolError(f"root identity changed before ready: {root.root_kind}")
                event_handle = _kernel32.CreateEventW(None, True, False, None)
                if not event_handle:
                    raise _winerror(f"could not create completion event for {root.root_kind}")
            except BaseException:
                close_error = _close_handle(handle, f"directory {root.root_kind}")
                if close_error:
                    errors.append(close_error)
                    failed_closes += 1
                raise
            overlapped = _OVERLAPPED()
            overlapped.hEvent = event_handle
            states.append(
                _WatchState(
                    root=root,
                    directory_path=root.path,
                    label=root.root_kind,
                    directory_handle=handle,
                    event_handle=event_handle,
                    recursive=True,
                    notify_filter=_NOTIFY_FILTER,
                    membership_name=None,
                    journal=journal,
                    stopping=stopping,
                    errors=errors,
                    errors_lock=errors_lock,
                    buffer=ctypes.create_string_buffer(_BUFFER_SIZE),
                    overlapped=overlapped,
                    armed=threading.Event(),
                    pending_lock=threading.Lock(),
                )
            )
            parent = _reject_reparse_components(root.path.parent, "watched root parent")
            if parent == root.path:
                raise WatchProtocolError(f"watched root cannot be a volume root: {root.root_kind}")
            parent_handle = _open_directory(parent, overlapped=True)
            try:
                _, _, parent_attributes = _handle_identity(parent_handle, parent)
                if not parent_attributes & _FILE_ATTRIBUTE_DIRECTORY:
                    raise WatchProtocolError(
                        f"watched root parent is not a directory: {root.root_kind}"
                    )
                if parent_attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                    raise WatchProtocolError(
                        f"watched root parent became reparse: {root.root_kind}"
                    )
                parent_event_handle = _kernel32.CreateEventW(None, True, False, None)
                if not parent_event_handle:
                    raise _winerror(
                        f"could not create membership event for {root.root_kind}"
                    )
            except BaseException:
                close_error = _close_handle(
                    parent_handle,
                    f"membership directory {root.root_kind}",
                )
                if close_error:
                    errors.append(close_error)
                    failed_closes += 1
                raise
            parent_overlapped = _OVERLAPPED()
            parent_overlapped.hEvent = parent_event_handle
            states.append(
                _WatchState(
                    root=root,
                    directory_path=parent,
                    label=f"membership-{root.root_kind}",
                    directory_handle=parent_handle,
                    event_handle=parent_event_handle,
                    recursive=False,
                    notify_filter=_FILE_NOTIFY_CHANGE_FILE_NAME
                    | _FILE_NOTIFY_CHANGE_DIR_NAME,
                    membership_name=root.path.name,
                    journal=journal,
                    stopping=stopping,
                    errors=errors,
                    errors_lock=errors_lock,
                    buffer=ctypes.create_string_buffer(_BUFFER_SIZE),
                    overlapped=parent_overlapped,
                    armed=threading.Event(),
                    pending_lock=threading.Lock(),
                )
            )
            opened_kinds.append(root.root_kind)
        for state in states:
            state.thread = threading.Thread(
                target=_watch_thread,
                args=(state,),
                name=f"watch-{state.label}",
            )
            try:
                state.thread.start()
                state.started = True
            except RuntimeError as error:
                errors.append(f"thread start failure for {state.label}: {error}")
                stopping.set()
                break
        if not errors:
            for state in states:
                if not state.armed.wait(10.0):
                    errors.append(f"watch request did not arm: {state.label}")
                    stopping.set()
                    break
        if not errors:
            _write_new(ready_path, _canonical_bytes(_ready_document(request, worker_pid)))
            ready = True
        while not stopping.is_set():
            if request.stop_token_path.exists():
                stopping.set()
                break
            time.sleep(0.02)
    except (OSError, WatchProtocolError) as error:
        errors.append(str(error))
        stopping.set()
    finally:
        stopping.set()
        for state in states:
            with state.pending_lock:
                if state.pending and not _kernel32.CancelIoEx(
                    state.directory_handle,
                    ctypes.byref(state.overlapped),
                ):
                    code = ctypes.get_last_error()
                    if code != _ERROR_NOT_FOUND:
                        errors.append(
                            f"CancelIoEx failed for {state.label}: WinError {code}"
                        )
        for state in states:
            if state.thread is not None and state.started:
                state.thread.join()
        if ready:
            root_results = tuple(_verify_root_identity(root) for root in request.roots)
            root_identities_unchanged = all(result[0] for result in root_results)
            for _, close_error, close_count in root_results:
                if close_error:
                    errors.append(close_error)
                failed_closes += close_count
            if not root_identities_unchanged:
                errors.append("root identity changed after ready")
        for state in reversed(states):
            for handle, label in (
                (state.event_handle, f"completion event {state.label}"),
                (state.directory_handle, f"directory {state.label}"),
            ):
                close_error = _close_handle(handle, label)
                if close_error:
                    errors.append(close_error)
                    failed_closes += 1
        if journal is not None:
            close_error = journal.close()
            if close_error:
                errors.append(close_error)
                failed_closes += 1
        try:
            event_bytes = events_path.read_bytes()
        except OSError as error:
            event_bytes = b""
            errors.append(f"could not reload event bytes: {error}")
        event_digest = hashlib.sha256(event_bytes).hexdigest()
        complete = ready and not errors and failed_closes == 0 and root_identities_unchanged
        terminal = _terminal_document(
            request,
            worker_pid=worker_pid,
            complete=complete,
            ready=ready,
            event_digest=event_digest,
            errors=errors,
            open_handle_count=failed_closes,
            root_identities_unchanged=root_identities_unchanged,
            opened_root_kinds=tuple(opened_kinds),
        )
        try:
            _write_new(terminal_path, _canonical_bytes(terminal))
        except (FileExistsError, OSError):
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
    return tuple(events)


def _parse_terminal(data: bytes, request: WatchRequest, worker_pid: int) -> dict[str, object]:
    value = _object_from_bytes(data, "terminal record")
    _exact_fields(
        value,
        {
            "complete",
            "error",
            "eventBytesSha256",
            "openHandleCount",
            "openedRootKinds",
            "ready",
            "requestId",
            "rootIdentitiesUnchanged",
            "schemaVersion",
            "workerPid",
        },
        "terminal record",
    )
    if value["schemaVersion"] != _SCHEMA_VERSION:
        raise WatchProtocolError("unsupported terminal schema version")
    if value["requestId"] != request.request_id or value["workerPid"] != worker_pid:
        raise WatchProtocolError("terminal record does not match request and worker")
    for field in ("complete", "ready", "rootIdentitiesUnchanged"):
        if type(value[field]) is not bool:
            raise WatchProtocolError(f"terminal {field} must be boolean")
    _required_int(value["openHandleCount"], "open handle count")
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
    try:
        result = _kernel32.WaitForSingleObject(handle, 0)
        return result == _WAIT_TIMEOUT
    finally:
        _kernel32.CloseHandle(handle)


def watch_receipt_from_files(
    request: WatchRequest,
    worker_pid: int,
    ready_path: Path,
    events_path: Path,
    terminal_path: Path,
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
            return WatchReceipt(
                request.request_id,
                worker_pid,
                False,
                False,
                (),
                (),
                hashlib.sha256(b"").hexdigest(),
                "evidence paths must be confined to the exact ready/events/terminal files",
            )
        if supplied.exists():
            attributes = _kernel32.GetFileAttributesW(str(supplied))
            if attributes == 0xFFFFFFFF or attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                return WatchReceipt(
                    request.request_id,
                    worker_pid,
                    False,
                    False,
                    (),
                    (),
                    hashlib.sha256(b"").hexdigest(),
                    "evidence paths must be confined direct files",
                )
    errors: list[str] = []
    ready = False
    opened: tuple[str, ...] = ()
    event_bytes = b""
    events: tuple[WatcherEvent, ...] = ()
    terminal: dict[str, object] | None = None
    try:
        normalized = _normalize_request(request, inspect_roots=False)
    except WatchProtocolError as error:
        normalized = request
        errors.append(str(error))
    try:
        opened = _parse_ready(Path(ready_path).read_bytes(), normalized, worker_pid)
        ready = True
    except FileNotFoundError:
        errors.append("ready record missing")
    except (OSError, WatchProtocolError) as error:
        errors.append(str(error))
    try:
        event_bytes = Path(events_path).read_bytes()
        events = _parse_events(event_bytes, normalized)
    except FileNotFoundError:
        errors.append("events journal missing")
    except (OSError, WatchProtocolError) as error:
        errors.append(str(error))
    event_digest = hashlib.sha256(event_bytes).hexdigest()
    try:
        terminal = _parse_terminal(Path(terminal_path).read_bytes(), normalized, worker_pid)
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
        if terminal["openHandleCount"] != 0:
            errors.append(f"unclosed handle count: {terminal['openHandleCount']}")
        if terminal["ready"] and not terminal["rootIdentitiesUnchanged"]:
            errors.append("root identity changed after ready")
        if not terminal["complete"]:
            errors.append(str(terminal["error"]))
    complete = terminal is not None and bool(terminal["complete"]) and not errors
    return WatchReceipt(
        request_id=request.request_id,
        worker_pid=worker_pid,
        complete=complete,
        ready=ready,
        opened_root_kinds=opened,
        events=events,
        event_bytes_sha256=event_digest,
        error=None if complete else "; ".join(dict.fromkeys(errors)),
    )


def _wait_for_process(pid: int, timeout_ms: int) -> bool:
    handle = _kernel32.OpenProcess(
        _SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        pid,
    )
    if not handle:
        return ctypes.get_last_error() == _ERROR_INVALID_PARAMETER
    try:
        return _kernel32.WaitForSingleObject(handle, timeout_ms) == _WAIT_OBJECT_0
    finally:
        _kernel32.CloseHandle(handle)


def stop_watch(request_path: Path) -> WatchReceipt:
    """Request exact-worker cancellation, wait for exit, and load its receipt."""
    _require_windows()
    request_key = Path(request_path).absolute()
    with _LOCAL_WORKERS_LOCK:
        local_worker = _LOCAL_WORKERS.get(request_key)
    request_error: str | None = None
    try:
        request = _load_request_path(request_key)
    except (OSError, WatchProtocolError) as error:
        if local_worker is None:
            raise WatchProtocolError(f"request record unavailable during stop: {error}") from error
        request = local_worker.request
        request_error = f"request record unavailable during stop: {error}"
    ready_path = request.evidence_root / _READY_NAME
    events_path = request.evidence_root / _EVENTS_NAME
    terminal_path = request.evidence_root / _TERMINAL_NAME
    try:
        _write_new(request.stop_token_path, b"stop\n")
    except FileExistsError:
        pass
    local_process = None if local_worker is None else local_worker.process
    if request_error is not None:
        worker_pid = local_process.pid
        try:
            local_process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            return WatchReceipt(
                request.request_id,
                worker_pid,
                False,
                False,
                (),
                (),
                hashlib.sha256(
                    events_path.read_bytes() if events_path.exists() else b""
                ).hexdigest(),
                f"{request_error}; worker did not stop after CancelIoEx request",
            )
        with _LOCAL_WORKERS_LOCK:
            _LOCAL_WORKERS.pop(request_key, None)
        return _force_incomplete(
            watch_receipt_from_files(
                request,
                worker_pid,
                ready_path,
                events_path,
                terminal_path,
            ),
            request_error,
        )
    try:
        ready_bytes = ready_path.read_bytes()
        ready_value = _object_from_bytes(ready_bytes, "ready record")
        worker_pid = _required_int(ready_value.get("workerPid"), "worker PID", minimum=1)
        _parse_ready(ready_bytes, request, worker_pid)
    except (FileNotFoundError, OSError, WatchProtocolError) as error:
        if local_process is not None:
            worker_pid = local_process.pid
            try:
                local_process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                return WatchReceipt(
                    request.request_id,
                    worker_pid,
                    False,
                    False,
                    (),
                    (),
                    hashlib.sha256(events_path.read_bytes() if events_path.exists() else b"").hexdigest(),
                    f"malformed ready record and worker did not stop: {error}",
                )
            with _LOCAL_WORKERS_LOCK:
                _LOCAL_WORKERS.pop(request_key, None)
            return watch_receipt_from_files(
                request,
                worker_pid,
                ready_path,
                events_path,
                terminal_path,
            )
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline and not terminal_path.exists():
            time.sleep(0.02)
        if terminal_path.exists():
            try:
                terminal_value = _object_from_bytes(terminal_path.read_bytes(), "terminal record")
                worker_pid = _required_int(
                    terminal_value.get("workerPid"),
                    "terminal worker PID",
                    minimum=1,
                )
                return watch_receipt_from_files(
                    request,
                    worker_pid,
                    ready_path,
                    events_path,
                    terminal_path,
                )
            except (OSError, WatchProtocolError):
                pass
        return WatchReceipt(
            request.request_id,
            0,
            False,
            False,
            (),
            (),
            hashlib.sha256(events_path.read_bytes() if events_path.exists() else b"").hexdigest(),
            f"ready record unavailable during stop: {error}",
        )
    if terminal_path.exists():
        exited = True
    elif local_process is not None:
        try:
            local_process.wait(timeout=30)
            exited = True
        except subprocess.TimeoutExpired:
            exited = False
    else:
        exited = _wait_for_process(worker_pid, 30_000)
    if not exited:
        return WatchReceipt(
            request.request_id,
            worker_pid,
            False,
            True,
            tuple(root.root_kind for root in request.roots),
            (),
            hashlib.sha256(events_path.read_bytes() if events_path.exists() else b"").hexdigest(),
            "watch worker did not stop after CancelIoEx request",
        )
    if local_process is not None:
        if local_process.poll() is None:
            local_process.wait(timeout=1)
        with _LOCAL_WORKERS_LOCK:
            _LOCAL_WORKERS.pop(request_key, None)
    return watch_receipt_from_files(
        request,
        worker_pid,
        ready_path,
        events_path,
        terminal_path,
    )


def _force_incomplete(receipt: WatchReceipt, error: str) -> WatchReceipt:
    combined = error if receipt.error is None else f"{error}; {receipt.error}"
    return WatchReceipt(
        request_id=receipt.request_id,
        worker_pid=receipt.worker_pid,
        complete=False,
        ready=receipt.ready,
        opened_root_kinds=receipt.opened_root_kinds,
        events=receipt.events,
        event_bytes_sha256=receipt.event_bytes_sha256,
        error=combined,
    )


def watch_proves_unchanged(
    receipt: WatchReceipt,
    before_manifest: ProtectedState,
    after_manifest: ProtectedState,
) -> bool:
    """Require both loss-free zero-event evidence and equal caller manifests."""
    return (
        receipt.complete
        and receipt.ready
        and not receipt.events
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
