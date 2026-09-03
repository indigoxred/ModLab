"""Strict, separate preparation lifecycle records; never scenario authority."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import hashlib
import json
from pathlib import Path
import re
import types
from typing import get_args, get_origin, get_type_hints


@dataclass(frozen=True)
class PreparationAttempt:
    run_id: str
    intent_id: str
    command_fingerprint: str
    source_commit: str
    source_tree: str
    source_bytes_sha256: str
    session_id: str
    controller_pid: int
    controller_creation_time: int
    replaces_recovery_id: str | None


@dataclass(frozen=True)
class ProjectionIdentity:
    path: str
    volume: int
    file_id: int


@dataclass(frozen=True)
class PreparationProjection:
    run_id: str
    intent_id: str
    command_fingerprint: str
    attempt_id: str
    scenario: str
    identities: tuple[ProjectionIdentity, ...]
    payload_hex: str


@dataclass(frozen=True)
class PreparationFailure:
    run_id: str
    intent_id: str
    command_fingerprint: str
    attempt_id: str | None
    effects: tuple[str, ...]
    projection_ids: tuple[str, ...]
    reason: str
    legacy: bool


@dataclass(frozen=True)
class PreparationCleanup:
    run_id: str
    intent_id: str
    command_fingerprint: str
    projection_id: str
    scenario: str
    destination: str
    destination_parent: ProjectionIdentity


@dataclass(frozen=True)
class PreparationProcess:
    pid: int
    creation_time: int
    image: str
    disposition: str


@dataclass(frozen=True)
class PreparationRecovery:
    run_id: str
    intent_id: str
    command_fingerprint: str
    failure_id: str
    process_proof: tuple[PreparationProcess, ...]
    cleaned: tuple[str, ...]
    preserved: tuple[str, ...]
    blockers: tuple[str, ...]
    fresh_run_permitted: bool
    process_policy: str = "supported-preparation-python-git-tar-mo2-absence-v1"
    cleanup_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PreparationReplacement:
    run_id: str
    intent_id: str
    command_fingerprint: str
    recovery_id: str
    consumed_by_run_id: str
    process_proof: tuple[PreparationProcess, ...] = ()


_KINDS = {
    PreparationAttempt: "attempt", PreparationProjection: "projection",
    PreparationFailure: "failure", PreparationRecovery: "recovery",
    PreparationReplacement: "replacement",
    PreparationCleanup: "cleanup",
}


def _require_id(value: str, prefix: str, length: int = 64) -> None:
    if type(value) is not str or re.fullmatch(re.escape(prefix) + "[0-9a-f]{" + str(length) + "}", value) is None:
        raise ValueError(f"invalid {prefix} identity")


def _typed(value, annotation, *, decoding: bool):
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is types.UnionType:
        if value is None and type(None) in args:
            return None
        return _typed(value, next(arg for arg in args if arg is not type(None)), decoding=decoding)
    if origin is tuple:
        if type(value) is not (list if decoding else tuple):
            raise ValueError("preparation arrays must have an exact type")
        return tuple(_typed(item, args[0], decoding=decoding) for item in value)
    if hasattr(annotation, "__dataclass_fields__"):
        if not decoding:
            if type(value) is not annotation:
                raise ValueError("incorrect preparation record type")
            source = {field.name: getattr(value, field.name) for field in fields(annotation)}
        else:
            if type(value) is not dict or set(value) != {field.name for field in fields(annotation)}:
                raise ValueError("preparation record fields are not exact")
            source = value
        hints = get_type_hints(annotation)
        return annotation(**{name: _typed(item, hints[name], decoding=decoding) for name, item in source.items()})
    if type(value) is not annotation:
        raise ValueError("preparation value type is not exact")
    return value


def _validate(value):
    kind = _KINDS.get(type(value))
    if kind is None:
        raise ValueError("unknown preparation record kind")
    _typed(value, type(value), decoding=False)
    _require_id(value.run_id, "containment-run:", 32)
    _require_id(value.intent_id, "containment-intent-sha256:")
    _require_id(value.command_fingerprint, "containment-command-sha256:")
    if isinstance(value, PreparationAttempt):
        _require_id(value.source_commit, "", 40)
        _require_id(value.source_tree, "", 40)
        _require_id(value.source_bytes_sha256, "")
        _require_id(value.session_id, "preparation-session:", 32)
        if value.controller_pid <= 0 or value.controller_creation_time <= 0:
            raise ValueError("preparing controller identity is missing")
        if value.replaces_recovery_id is not None:
            _require_id(value.replaces_recovery_id, "preparation-recovery-sha256:")
    elif isinstance(value, PreparationProjection):
        _require_id(value.attempt_id, "preparation-attempt-sha256:")
        if value.scenario not in {"NewFolder", "MergeExisting", "ReplaceExisting", "FomodDependency"}:
            raise ValueError("invalid projection scenario")
        if len(value.identities) != 4 or len({item.volume for item in value.identities}) != 1:
            raise ValueError("projection must bind link, target, parents and one volume")
        for item in value.identities:
            if not Path(item.path).is_absolute() or item.volume < 0 or item.file_id <= 0:
                raise ValueError("invalid projection native identity")
        if not value.payload_hex or bytes.fromhex(value.payload_hex).hex() != value.payload_hex:
            raise ValueError("projection payload is not canonical")
    elif isinstance(value, PreparationFailure):
        if value.legacy != (value.attempt_id is None) or not value.reason:
            raise ValueError("failure lifecycle is inconsistent")
        if value.attempt_id is not None:
            _require_id(value.attempt_id, "preparation-attempt-sha256:")
        for item in value.projection_ids:
            _require_id(item, "preparation-projection-sha256:")
        if value.legacy and value.projection_ids:
            raise ValueError("legacy failure cannot acquire creation receipts")
        if any(not Path(path).is_absolute() for path in value.effects):
            raise ValueError("failure effects must be absolute paths")
    elif isinstance(value, PreparationCleanup):
        _require_id(value.projection_id, "preparation-projection-sha256:")
        if value.scenario not in {"NewFolder", "MergeExisting", "ReplaceExisting", "FomodDependency"}:
            raise ValueError("invalid cleanup scenario")
        parent = value.destination_parent
        if not Path(value.destination).is_absolute() or Path(value.destination).parent != Path(parent.path) or parent.volume < 0 or parent.file_id <= 0:
            raise ValueError("invalid cleanup destination identity")
    elif isinstance(value, PreparationRecovery):
        if value.process_policy != "supported-preparation-python-git-tar-mo2-absence-v1":
            raise ValueError("unsupported preparation process proof policy")
        _require_id(value.failure_id, "preparation-failure-sha256:")
        for identifier in value.cleanup_ids:
            _require_id(identifier, "preparation-cleanup-sha256:")
        if len(value.cleaned) != len(value.cleanup_ids):
            raise ValueError("cleanup dispositions lack their exact receipts")
        if value.fresh_run_permitted and (value.blockers or not value.process_proof):
            raise ValueError("replacement requires complete process proof without blockers")
        for process in value.process_proof:
            if (process.pid <= 0 or process.creation_time <= 0 or not process.image
                    or process.disposition not in {"Verifier", "Absent"}):
                raise ValueError("invalid native process proof")
        if value.fresh_run_permitted and sum(item.disposition == "Verifier" for item in value.process_proof) != 1:
            raise ValueError("process proof lacks the exact verifier")
        if any(not Path(path).is_absolute() for path in (*value.cleaned, *value.preserved)):
            raise ValueError("recovery paths must be absolute")
    else:
        _require_id(value.recovery_id, "preparation-recovery-sha256:")
        _require_id(value.consumed_by_run_id, "containment-run:", 32)
        if value.consumed_by_run_id == value.run_id:
            raise ValueError("replacement cannot reuse the failed run")
        if not value.process_proof:
            raise ValueError("replacement requires current process proof")
        if (sum(item.disposition == "Verifier" for item in value.process_proof) != 1
                or any(item.pid <= 0 or item.creation_time <= 0 or not item.image
                    or item.disposition not in {"Verifier", "Absent"} for item in value.process_proof)):
            raise ValueError("invalid replacement native process proof")
    return kind


def record_to_bytes(value) -> bytes:
    kind = _validate(value)
    return (json.dumps({"preparationSchemaVersion": 1, "kind": kind, "record": asdict(value)},
                       sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate preparation JSON field")
        result[key] = value
    return result


def record_from_bytes(data: bytes, record_type):
    document = json.loads(data.decode("utf-8"), object_pairs_hook=_unique)
    if (type(document) is not dict or set(document) != {"preparationSchemaVersion", "kind", "record"}
            or type(document["preparationSchemaVersion"]) is not int or document["preparationSchemaVersion"] != 1
            or document["kind"] != _KINDS.get(record_type)):
        raise ValueError("invalid preparation schema or kind")
    result = _typed(document["record"], record_type, decoding=True)
    if record_to_bytes(result) != data:
        raise ValueError("preparation bytes are not canonical")
    return result


def record_id(value) -> str:
    return "preparation-" + _validate(value) + "-sha256:" + hashlib.sha256(record_to_bytes(value)).hexdigest()


def native_process_absent(pid: int, creation_time: int) -> bool:
    """Recognize only raw native absence or the exited exact process identity."""
    import ctypes
    import os
    from . import windows_watch as watch
    if os.name != "nt" or type(pid) is not int or pid <= 0 or type(creation_time) is not int or creation_time <= 0:
        return False
    ctypes.set_last_error(0)
    handle = watch._kernel32.OpenProcess(watch._SYNCHRONIZE | watch._PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # Read the native code immediately. The older watch wrapper stores it in
        # errno, whereas its status path inspects winerror; do not reinterpret it.
        return ctypes.get_last_error() == 87
    absent = False
    try:
        observed = watch._process_handle_creation_time(handle, pid)
        absent = observed == creation_time and watch._kernel32.WaitForSingleObject(handle, 0) == watch._WAIT_OBJECT_0
    finally:
        if watch._close_controller_handle(handle, "preparation exact process", Path(__file__).absolute()):
            absent = False
    return absent


def native_preparation_process_inventory() -> tuple[PreparationProcess, ...]:
    """Complete Toolhelp enumeration, conservative candidate images, exact verifier.

    The pre-request call graph only creates synchronous system tar children;
    Python controllers and later MO2 images are also conservatively excluded.
    Every candidate other than this exact verifier refuses, regardless of path.
    """
    import ctypes
    from ctypes import wintypes
    import os
    import sys
    from modlab.adapters.mo2.processes import _ProcessEntry32W
    from . import windows_watch as watch

    if os.name != "nt":
        raise RuntimeError("native preparation process proof requires Windows")
    candidates = {"python.exe", "pythonw.exe", "git.exe", "tar.exe", "modorganizer.exe", "nxmhandler.exe", Path(sys.executable).name.casefold()}
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ("Process32FirstW", "Process32NextW"):
        function = getattr(kernel, name)
        function.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W))
        function.restype = wintypes.BOOL
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        raise RuntimeError("native process snapshot is unavailable")
    rows = []
    primary = None
    try:
        entry = _ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(entry)
        ctypes.set_last_error(0)
        if not kernel.Process32FirstW(snapshot, ctypes.byref(entry)):
            raise RuntimeError("native process snapshot is incomplete or empty")
        seen = set()
        while True:
            pid, image = int(entry.th32ProcessID), entry.szExeFile.casefold()
            if pid in seen:
                raise RuntimeError("native process snapshot contains duplicate PID")
            seen.add(pid)
            if image in candidates:
                handle, created = watch._open_process_identity(pid)
                try:
                    if pid != os.getpid():
                        raise RuntimeError(f"preparation process candidate is live or uncertain: {image} PID {pid}")
                    # Opening our actual current PID plus its native creation time
                    # positively identifies the sole excluded verifier.
                    rows.append(PreparationProcess(pid, created, image, "Verifier"))
                finally:
                    if watch._close_controller_handle(handle, "preparation process proof", Path(__file__).absolute()):
                        raise RuntimeError("preparation process proof handle close failed")
            entry.dwSize = ctypes.sizeof(entry)
            ctypes.set_last_error(0)
            if kernel.Process32NextW(snapshot, ctypes.byref(entry)):
                continue
            if ctypes.get_last_error() != 18:
                raise RuntimeError("native process enumeration ended incompletely")
            break
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            if watch._close_controller_handle(snapshot, "preparation process snapshot", Path(__file__).absolute()):
                raise RuntimeError("preparation snapshot handle close failed")
        except BaseException as close_error:
            if primary is not None:
                raise watch._merge_controller_errors(primary, close_error) from primary
            raise
    if len(rows) != 1:
        raise RuntimeError("native inventory did not identify the exact verifier")
    return tuple(rows)
