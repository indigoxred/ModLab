"""Exact Windows junction projection and fail-closed staged-mod adoption."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import stat
import struct

from modlab.validation.mo2_containment_model import TreeIdentity
from modlab.validation.windows_integrity import (
    IntegrityLevel,
    inspect_path_integrity,
    set_medium_integrity_entries,
)


IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003

_FSCTL_SET_REPARSE_POINT = 0x000900A4
_FSCTL_GET_REPARSE_POINT = 0x000900A8
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_DELETE = 0x00010000
_FILE_READ_ATTRIBUTES = 0x00000080
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_ALL = 0x00000001 | 0x00000002 | 0x00000004
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000
_FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
_FILE_RENAME_INFO_CLASS = 3
_FILE_DISPOSITION_INFO_CLASS = 4
_ERROR_FILE_EXISTS = 80
_ERROR_ALREADY_EXISTS = 183
_MAXIMUM_REPARSE_DATA_BUFFER_SIZE = 16 * 1024
_READ_CHUNK_SIZE = 1024 * 1024
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class ContainmentSafetyError(RuntimeError):
    """A filesystem observation is insufficiently exact for safe containment."""


@dataclass(frozen=True)
class JunctionEvidence:
    link_path: Path
    target_path: Path
    substitute_name: str
    print_name: str
    reparse_tag: int
    reparse_payload_sha256: str


@dataclass(frozen=True)
class AdoptionEvidence:
    adopted_name: str
    destination_path: Path
    before_tree: TreeIdentity
    after_tree: TreeIdentity
    final_integrity: IntegrityLevel
    final_is_reparse: bool


@dataclass
class _PinnedObject:
    path: Path
    handle: int
    identity: tuple[int, int]

    def close(self) -> None:
        if self.handle:
            _close_handle(self.handle)
            self.handle = 0


@dataclass
class _PinnedEntry:
    relative_path: str
    is_directory: bool
    pinned: _PinnedObject


@dataclass
class _PinnedTree:
    root: _PinnedObject
    entries: list[_PinnedEntry]
    current_path: Path

    def close_descendants(self) -> None:
        for entry in reversed(self.entries):
            entry.pinned.close()
        self.entries.clear()

    def close(self) -> None:
        self.close_descendants()
        self.root.close()


class _PinnedTreeRejected(ContainmentSafetyError):
    def __init__(self, message: str, tree: _PinnedTree) -> None:
        super().__init__(message)
        self.tree = tree


if os.name == "nt":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
        _fields_ = [
            ("FileAttributes", wintypes.DWORD),
            ("ReparseTag", wintypes.DWORD),
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

    class _FILE_RENAME_INFO(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD),
            ("FileName", wintypes.WCHAR * 1),
        ]

    class _FILE_DISPOSITION_INFO(ctypes.Structure):
        _fields_ = [("DeleteFile", wintypes.BOOL)]

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
    _kernel32.DeviceIoControl.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    _kernel32.DeviceIoControl.restype = wintypes.BOOL
    _kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    _kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION),
    ]
    _kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    _kernel32.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
    _kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    _kernel32.ReadFile.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL


def _require_windows() -> None:
    if os.name != "nt":
        raise OSError("Windows junction APIs are unavailable")


def _winerror(message: str, code: int | None = None) -> OSError:
    if code is None:
        code = ctypes.get_last_error()
    return OSError(code, f"{message}: {ctypes.FormatError(code)}")


def _close_handle(handle: int | None) -> None:
    if handle and handle != _INVALID_HANDLE_VALUE:
        _kernel32.CloseHandle(handle)


def _absolute(path: Path) -> Path:
    target = Path(path)
    if not target.is_absolute():
        raise ContainmentSafetyError(f"path must be absolute: {target}")
    text = str(target)
    if text.startswith(("\\\\", "\\?\\", "\\.\\")):
        raise ContainmentSafetyError(f"device and UNC paths are not allowed: {target}")
    return Path(os.path.abspath(target))


def _open_no_follow(
    path: Path,
    desired_access: int = 0,
    *,
    share_mode: int = _FILE_SHARE_ALL,
) -> int:
    _require_windows()
    handle = _kernel32.CreateFileW(
        str(path),
        desired_access,
        share_mode,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_OPEN_REPARSE_POINT | _FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        raise _winerror(f"CreateFileW failed for {path}")
    return handle


def _attribute_tag_for_handle(handle: int, path: Path) -> tuple[int, int]:
    info = _FILE_ATTRIBUTE_TAG_INFO()
    if not _kernel32.GetFileInformationByHandleEx(
        handle,
        _FILE_ATTRIBUTE_TAG_INFO_CLASS,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        raise _winerror(f"GetFileInformationByHandleEx failed for {path}")
    return info.FileAttributes, info.ReparseTag


def _attribute_tag(path: Path) -> tuple[int, int]:
    handle = _open_no_follow(path)
    try:
        return _attribute_tag_for_handle(handle, path)
    finally:
        _close_handle(handle)


def _is_reparse(attributes: int, tag: int) -> bool:
    return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT) or tag != 0


def _require_direct_components(path: Path, *, allow_final_reparse: bool = False) -> Path:
    target = _absolute(path)
    current = Path(target.anchor)
    parts = target.parts[1:]
    for index, part in enumerate(parts):
        current = current / part
        try:
            metadata = current.lstat()
        except OSError as error:
            raise ContainmentSafetyError(f"cannot inspect direct path component {current}: {error}") from error
        attributes = int(getattr(metadata, "st_file_attributes", 0))
        tag = int(getattr(metadata, "st_reparse_tag", 0))
        is_final = index == len(parts) - 1
        if _is_reparse(attributes, tag) and not (allow_final_reparse and is_final):
            raise ContainmentSafetyError(f"reparse component is not allowed: {current}")
    return target


def _require_direct_directory(path: Path) -> Path:
    target = _require_direct_components(path)
    attributes, tag = _attribute_tag(target)
    if _is_reparse(attributes, tag) or not attributes & _FILE_ATTRIBUTE_DIRECTORY:
        raise ContainmentSafetyError(f"direct regular directory required: {target}")
    return target


def _safe_name(name: str, label: str) -> str:
    if not isinstance(name, str) or not name or name in {".", ".."}:
        raise ContainmentSafetyError(f"{label} must be a nonempty direct name")
    if any(character in name for character in "\\/:\0") or name[-1] in {" ", "."}:
        raise ContainmentSafetyError(f"{label} is not a safe direct name: {name!r}")
    if any(ord(character) < 0x20 for character in name):
        raise ContainmentSafetyError(f"{label} contains a control character")
    stem = name.split(".", 1)[0].casefold()
    if stem in {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}:
        raise ContainmentSafetyError(f"{label} is a reserved Windows name: {name!r}")
    return name


def _same_path(left: Path | str, right: Path | str) -> bool:
    return str(PureWindowsPath(left)).casefold() == str(PureWindowsPath(right)).casefold()


def _mount_point_payload(substitute_name: str, print_name: str) -> bytes:
    substitute = substitute_name.encode("utf-16-le")
    display = print_name.encode("utf-16-le")
    path_buffer = substitute + b"\0\0" + display + b"\0\0"
    return struct.pack(
        "<IHHHHHH",
        IO_REPARSE_TAG_MOUNT_POINT,
        8 + len(path_buffer),
        0,
        0,
        len(substitute),
        len(substitute) + 2,
        len(display),
    ) + path_buffer


def _read_reparse_payload_handle(handle: int, path: Path) -> bytes:
    output = ctypes.create_string_buffer(_MAXIMUM_REPARSE_DATA_BUFFER_SIZE)
    returned = wintypes.DWORD()
    if not _kernel32.DeviceIoControl(
        handle,
        _FSCTL_GET_REPARSE_POINT,
        None,
        0,
        output,
        len(output),
        ctypes.byref(returned),
        None,
    ):
        raise _winerror(f"FSCTL_GET_REPARSE_POINT failed for {path}")
    return output.raw[: returned.value]


def _read_reparse_payload(path: Path) -> bytes:
    handle = _open_no_follow(path)
    try:
        return _read_reparse_payload_handle(handle, path)
    finally:
        _close_handle(handle)


def _parse_mount_point_payload(payload: bytes) -> tuple[str, str]:
    if len(payload) < 16:
        raise ContainmentSafetyError("truncated mount-point reparse payload")
    tag, data_length, reserved, substitute_offset, substitute_length, print_offset, print_length = struct.unpack_from(
        "<IHHHHHH", payload
    )
    if tag != IO_REPARSE_TAG_MOUNT_POINT:
        raise ContainmentSafetyError(f"unexpected reparse tag: 0x{tag:08x}")
    if reserved != 0 or data_length + 8 != len(payload):
        raise ContainmentSafetyError("invalid mount-point reparse header")
    path_buffer = payload[16:]
    values: list[str] = []
    for offset, length, label in (
        (substitute_offset, substitute_length, "substitute"),
        (print_offset, print_length, "print"),
    ):
        if offset % 2 or length % 2 or offset + length + 2 > len(path_buffer):
            raise ContainmentSafetyError(f"invalid {label} name bounds in reparse payload")
        if path_buffer[offset + length : offset + length + 2] != b"\0\0":
            raise ContainmentSafetyError(f"unterminated {label} name in reparse payload")
        try:
            values.append(path_buffer[offset : offset + length].decode("utf-16-le"))
        except UnicodeDecodeError as error:
            raise ContainmentSafetyError(f"invalid UTF-16 {label} name in reparse payload") from error
    return values[0], values[1]


def inspect_junction(path: Path) -> JunctionEvidence:
    link = _require_direct_components(path, allow_final_reparse=True)
    attributes, tag = _attribute_tag(link)
    if tag != IO_REPARSE_TAG_MOUNT_POINT or not attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        raise ContainmentSafetyError(f"path is not a mount-point junction: {link}")
    payload = _read_reparse_payload(link)
    substitute_name, print_name = _parse_mount_point_payload(payload)
    if not substitute_name.startswith("\\??\\") or not print_name:
        raise ContainmentSafetyError("mount-point target names are not canonical")
    target = _require_direct_directory(Path(print_name)).resolve(strict=True)
    expected_substitute = "\\??\\" + str(target)
    if not _same_path(substitute_name, expected_substitute) or not _same_path(print_name, target):
        raise ContainmentSafetyError("mount-point target names disagree")
    if payload != _mount_point_payload(substitute_name, print_name):
        raise ContainmentSafetyError("noncanonical reparse payload")
    return JunctionEvidence(
        link_path=link,
        target_path=target,
        substitute_name=substitute_name,
        print_name=print_name,
        reparse_tag=tag,
        reparse_payload_sha256=hashlib.sha256(payload).hexdigest(),
    )


def create_mod_projection(source_mod: Path, staging_mod: Path) -> JunctionEvidence:
    source = _require_direct_directory(source_mod).resolve(strict=True)
    link = _absolute(staging_mod)
    parent = _require_direct_directory(link.parent)
    if link.parent != parent:
        raise ContainmentSafetyError("staging junction parent changed during validation")
    try:
        link.lstat()
    except FileNotFoundError:
        pass
    else:
        raise ContainmentSafetyError(f"staging projection already exists: {link}")

    substitute_name = "\\??\\" + str(source)
    print_name = str(source)
    payload = _mount_point_payload(substitute_name, print_name)
    created = False
    try:
        link.mkdir()
        created = True
        handle = _open_no_follow(link, _GENERIC_WRITE)
        try:
            native_buffer = ctypes.create_string_buffer(payload)
            returned = wintypes.DWORD()
            if not _kernel32.DeviceIoControl(
                handle,
                _FSCTL_SET_REPARSE_POINT,
                native_buffer,
                len(payload),
                None,
                0,
                ctypes.byref(returned),
                None,
            ):
                raise _winerror(f"FSCTL_SET_REPARSE_POINT failed for {link}")
        finally:
            _close_handle(handle)
        evidence = inspect_junction(link)
        if not _same_path(evidence.target_path, source):
            raise ContainmentSafetyError("created junction target does not match requested source")
        if evidence.reparse_payload_sha256 != hashlib.sha256(payload).hexdigest():
            raise ContainmentSafetyError("created junction payload does not match requested payload")
        return evidence
    except BaseException as error:
        if created and _exists_no_follow(link):
            try:
                link.rmdir()
            except OSError as cleanup_error:
                raise ContainmentSafetyError(
                    f"junction creation failed and exact link cleanup also failed: {cleanup_error}"
                ) from error
        raise


def _top_level_entries(root: Path) -> tuple[Path, ...]:
    entries = tuple(Path(entry.path) for entry in os.scandir(root))
    folded = [entry.name.casefold() for entry in entries]
    if len(folded) != len(set(folded)):
        raise ContainmentSafetyError(f"case-insensitive top-level collision beneath {root}")
    return tuple(sorted(entries, key=lambda entry: (entry.name.casefold(), entry.name)))


def _delete_exact_projection(expected: JunctionEvidence) -> None:
    handle = _open_no_follow(
        expected.link_path,
        _DELETE | _GENERIC_READ,
        share_mode=_FILE_SHARE_READ,
    )
    try:
        attributes, tag = _attribute_tag_for_handle(handle, expected.link_path)
        if tag != expected.reparse_tag or not attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise ContainmentSafetyError(
                f"projection changed before exact rollback: {expected.link_path}"
            )
        payload = _read_reparse_payload_handle(handle, expected.link_path)
        substitute_name, print_name = _parse_mount_point_payload(payload)
        if (
            substitute_name != expected.substitute_name
            or print_name != expected.print_name
            or hashlib.sha256(payload).hexdigest()
            != expected.reparse_payload_sha256
        ):
            raise ContainmentSafetyError(
                f"projection changed before exact rollback: {expected.link_path}"
            )
        disposition = _FILE_DISPOSITION_INFO(True)
        if not _kernel32.SetFileInformationByHandle(
            handle,
            _FILE_DISPOSITION_INFO_CLASS,
            ctypes.byref(disposition),
            ctypes.sizeof(disposition),
        ):
            raise _winerror(
                f"handle-based exact junction rollback failed for {expected.link_path}"
            )
    finally:
        _close_handle(handle)
    if _exists_no_follow(expected.link_path):
        raise ContainmentSafetyError(
            f"projection remained after rollback: {expected.link_path}"
        )


def build_projection(source_mods: Path, stage_mods: Path) -> tuple[JunctionEvidence, ...]:
    source_root = _require_direct_directory(source_mods)
    stage_root = _require_direct_directory(stage_mods)
    if _top_level_entries(stage_root):
        raise ContainmentSafetyError("staging mods root must be empty before projection")

    source_entries = _top_level_entries(source_root)
    for entry in source_entries:
        attributes, tag = _attribute_tag(entry)
        if _is_reparse(attributes, tag):
            raise ContainmentSafetyError(f"source mod is reparse: {entry.name}")
        if not attributes & _FILE_ATTRIBUTE_DIRECTORY:
            raise ContainmentSafetyError(f"source mod is not a direct directory: {entry.name}")
    created: list[JunctionEvidence] = []
    try:
        for entry in source_entries:
            created.append(create_mod_projection(entry, stage_root / entry.name))
    except BaseException as creation_error:
        cleanup_failures: list[str] = []
        for expected in reversed(created):
            try:
                observed = inspect_junction(expected.link_path)
                if observed != expected:
                    raise ContainmentSafetyError(
                        f"projection changed before rollback: {expected.link_path}"
                    )
                _delete_exact_projection(expected)
            except BaseException as cleanup_error:
                cleanup_failures.append(f"{expected.link_path}: {cleanup_error}")
        if cleanup_failures:
            details = "; ".join(cleanup_failures)
            raise ContainmentSafetyError(
                f"projection creation failed and exact rollback failed: {details}"
            ) from creation_error
        raise
    return tuple(created)


def _walk_direct_tree(root: Path) -> tuple[tuple[str, Path, bool], ...]:
    direct_root = _require_direct_directory(root)
    collected: list[tuple[str, Path, bool]] = []

    def walk(directory: Path, parts: tuple[str, ...]) -> None:
        entries = tuple(
            sorted(os.scandir(directory), key=lambda entry: (entry.name.casefold(), entry.name))
        )
        folded = [entry.name.casefold() for entry in entries]
        if len(folded) != len(set(folded)):
            raise ContainmentSafetyError(f"case-insensitive tree collision beneath {directory}")
        for entry in entries:
            path = Path(entry.path)
            relative = "/".join((*parts, entry.name))
            attributes, tag = _attribute_tag(path)
            if _is_reparse(attributes, tag):
                raise ContainmentSafetyError(f"reparse descendant is not allowed: {relative}")
            is_directory = bool(attributes & _FILE_ATTRIBUTE_DIRECTORY)
            if not is_directory:
                metadata = path.lstat()
                if not stat.S_ISREG(metadata.st_mode):
                    raise ContainmentSafetyError(f"non-regular tree entry is not allowed: {relative}")
            collected.append((relative, path, is_directory))
            if is_directory:
                walk(path, (*parts, entry.name))

    walk(direct_root, ())
    return tuple(collected)


def reject_reparse_tree(root: Path) -> None:
    try:
        tree = _pin_tree(root)
    except _PinnedTreeRejected as error:
        error.tree.close()
        raise ContainmentSafetyError(str(error)) from error
    else:
        tree.close()


def _handle_information(handle: int, path: Path) -> _BY_HANDLE_FILE_INFORMATION:
    information = _BY_HANDLE_FILE_INFORMATION()
    if not _kernel32.GetFileInformationByHandle(handle, ctypes.byref(information)):
        raise _winerror(f"GetFileInformationByHandle failed for {path}")
    return information


def _file_identity(information: _BY_HANDLE_FILE_INFORMATION) -> tuple[int, int]:
    file_index = (information.nFileIndexHigh << 32) | information.nFileIndexLow
    return information.dwVolumeSerialNumber, file_index


def _identity_at_path(path: Path) -> tuple[int, int]:
    handle = _open_no_follow(path)
    try:
        return _file_identity(_handle_information(handle, path))
    finally:
        _close_handle(handle)


def _pin_object(
    path: Path,
    *,
    desired_access: int,
    expected_identity: tuple[int, int] | None = None,
    allow_reparse: bool,
    share_mode: int = _FILE_SHARE_READ,
) -> _PinnedObject:
    target = _absolute(path)
    try:
        handle = _open_no_follow(
            target,
            desired_access,
            share_mode=share_mode,
        )
    except OSError as error:
        raise ContainmentSafetyError(
            f"cannot acquire identity pin before mutation for {target}: {error}"
        ) from error
    try:
        attributes, tag = _attribute_tag_for_handle(handle, target)
        if _is_reparse(attributes, tag) and not allow_reparse:
            raise ContainmentSafetyError(f"reparse object cannot be pinned: {target}")
        identity = _file_identity(_handle_information(handle, target))
        if expected_identity is not None and identity != expected_identity:
            raise ContainmentSafetyError(f"object changed before identity pin: {target}")
        return _PinnedObject(target, handle, identity)
    except BaseException:
        _close_handle(handle)
        raise


def _pin_parent_directory(path: Path) -> _PinnedObject:
    parent = _require_direct_directory(path)
    expected = _identity_at_path(parent)
    return _pin_object(
        parent,
        desired_access=_DELETE | _FILE_READ_ATTRIBUTES,
        expected_identity=expected,
        allow_reparse=False,
        share_mode=_FILE_SHARE_READ | _FILE_SHARE_WRITE,
    )


def _pin_descendants(tree: _PinnedTree) -> None:
    if tree.entries:
        raise ContainmentSafetyError("descendant pins must be empty before reacquisition")

    def walk(directory: Path, parts: tuple[str, ...]) -> None:
        try:
            entries = tuple(
                sorted(
                    os.scandir(directory),
                    key=lambda entry: (entry.name.casefold(), entry.name),
                )
            )
        except OSError as error:
            raise ContainmentSafetyError(
                f"cannot enumerate pinned tree directory {directory}: {error}"
            ) from error
        folded = [entry.name.casefold() for entry in entries]
        if len(folded) != len(set(folded)):
            raise ContainmentSafetyError(
                f"case-insensitive tree collision beneath {directory}"
            )
        for entry in entries:
            path = Path(entry.path)
            relative = "/".join((*parts, entry.name))
            observed_identity = _identity_at_path(path)
            metadata = entry.stat(follow_symlinks=False)
            is_directory = stat.S_ISDIR(metadata.st_mode)
            access = _DELETE | (
                _FILE_READ_ATTRIBUTES if is_directory else _GENERIC_READ
            )
            pinned = _pin_object(
                path,
                desired_access=access,
                expected_identity=observed_identity,
                allow_reparse=True,
            )
            attributes, tag = _attribute_tag_for_handle(pinned.handle, path)
            if _is_reparse(attributes, tag):
                pinned.close()
                raise _PinnedTreeRejected(
                    f"reparse descendant is not allowed: {relative}", tree
                )
            pinned_is_directory = bool(attributes & _FILE_ATTRIBUTE_DIRECTORY)
            if pinned_is_directory != is_directory:
                pinned.close()
                raise ContainmentSafetyError(
                    f"tree entry type changed before identity pin: {relative}"
                )
            if not pinned_is_directory and not stat.S_ISREG(metadata.st_mode):
                pinned.close()
                raise _PinnedTreeRejected(
                    f"non-regular tree entry is not allowed: {relative}", tree
                )
            tree.entries.append(_PinnedEntry(relative, pinned_is_directory, pinned))
            if pinned_is_directory:
                walk(path, (*parts, entry.name))

    try:
        if _identity_at_path(tree.current_path) != tree.root.identity:
            raise ContainmentSafetyError("pinned tree root identity changed")
        walk(tree.current_path, ())
        _assert_pinned_tree(tree)
    except BaseException:
        tree.close_descendants()
        raise


def _pin_tree(root: Path) -> _PinnedTree:
    root_path = _require_direct_directory(root)
    root_identity = _identity_at_path(root_path)
    root_pin = _pin_object(
        root_path,
        desired_access=_DELETE | _FILE_READ_ATTRIBUTES,
        expected_identity=root_identity,
        allow_reparse=False,
    )
    tree = _PinnedTree(root=root_pin, entries=[], current_path=root_path)
    try:
        _pin_descendants(tree)
        return tree
    except _PinnedTreeRejected:
        raise
    except BaseException:
        tree.close()
        raise


def _assert_pinned_tree(tree: _PinnedTree) -> None:
    _assert_pinned_root_path(tree)
    observed = _walk_direct_tree(tree.current_path)
    expected_by_relative = {entry.relative_path: entry for entry in tree.entries}
    if {relative for relative, _, _ in observed} != set(expected_by_relative):
        raise ContainmentSafetyError("pinned tree membership changed")
    for relative, path, is_directory in observed:
        expected = expected_by_relative[relative]
        if expected.is_directory != is_directory:
            raise ContainmentSafetyError(f"pinned tree entry type changed: {relative}")
        if _identity_at_path(path) != expected.pinned.identity:
            raise ContainmentSafetyError(f"pinned tree entry identity changed: {relative}")


def _assert_pinned_root_path(tree: _PinnedTree) -> None:
    if _identity_at_path(tree.current_path) != tree.root.identity:
        raise ContainmentSafetyError("pinned tree root identity changed")


def _rename_pinned_object(
    pinned: _PinnedObject,
    destination: Path,
    destination_parent: _PinnedObject,
) -> None:
    destination_path = _absolute(destination)
    if destination_path.parent != destination_parent.path:
        raise ContainmentSafetyError("pinned rename destination parent mismatch")
    if _identity_at_path(destination_parent.path) != destination_parent.identity:
        raise ContainmentSafetyError("pinned rename destination parent changed")
    source_information = _handle_information(pinned.handle, pinned.path)
    parent_information = _handle_information(
        destination_parent.handle, destination_parent.path
    )
    if source_information.dwVolumeSerialNumber != parent_information.dwVolumeSerialNumber:
        raise ContainmentSafetyError("rename requires source and destination on the same volume")

    encoded_name = str(destination_path).encode("utf-16-le")
    name_offset = _FILE_RENAME_INFO.FileName.offset
    native_buffer = ctypes.create_string_buffer(name_offset + len(encoded_name) + 2)
    rename = _FILE_RENAME_INFO.from_buffer(native_buffer)
    rename.Flags = 0
    rename.RootDirectory = None
    rename.FileNameLength = len(encoded_name)
    ctypes.memmove(
        ctypes.addressof(native_buffer) + name_offset,
        encoded_name,
        len(encoded_name),
    )
    ctypes.set_last_error(0)
    if not _kernel32.SetFileInformationByHandle(
        pinned.handle,
        _FILE_RENAME_INFO_CLASS,
        native_buffer,
        len(native_buffer),
    ):
        error = ctypes.get_last_error()
        if error in {_ERROR_FILE_EXISTS, _ERROR_ALREADY_EXISTS}:
            raise FileExistsError(error, "destination already exists", str(destination_path))
        raise _winerror(
            f"handle-based no-replace rename failed for {destination_path}", error
        )
    pinned.path = destination_path
    if _identity_at_path(destination_path) != pinned.identity:
        raise ContainmentSafetyError("renamed destination is not the pinned source object")


def _hash_direct_file(path: Path) -> tuple[str, int]:
    handle = _open_no_follow(path, _GENERIC_READ)
    try:
        attributes, tag = _attribute_tag_for_handle(handle, path)
        if _is_reparse(attributes, tag) or attributes & _FILE_ATTRIBUTE_DIRECTORY:
            raise ContainmentSafetyError(f"direct regular file required while hashing: {path}")
        before = _handle_information(handle, path)
        digest = hashlib.sha256()
        total = 0
        buffer = ctypes.create_string_buffer(_READ_CHUNK_SIZE)
        while True:
            returned = wintypes.DWORD()
            if not _kernel32.ReadFile(handle, buffer, len(buffer), ctypes.byref(returned), None):
                raise _winerror(f"ReadFile failed for {path}")
            if returned.value == 0:
                break
            digest.update(buffer.raw[: returned.value])
            total += returned.value
        after = _handle_information(handle, path)
        expected_size = (after.nFileSizeHigh << 32) | after.nFileSizeLow
        if _file_identity(before) != _file_identity(after) or total != expected_size:
            raise ContainmentSafetyError(f"file changed while hashing: {path}")
        return digest.hexdigest(), total
    finally:
        _close_handle(handle)


def _tree_identity(root: Path) -> TreeIdentity:
    rows: list[list[str | int]] = []
    regular_file_count = 0
    directory_count = 0
    total_size = 0
    for relative, path, is_directory in _walk_direct_tree(root):
        if is_directory:
            directory_count += 1
            rows.append(["D", relative])
        else:
            digest, size = _hash_direct_file(path)
            regular_file_count += 1
            total_size += size
            rows.append(["F", relative, digest, size])
    canonical = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return TreeIdentity(
        sha256=hashlib.sha256(canonical).hexdigest(),
        regular_file_count=regular_file_count,
        directory_count=directory_count,
        total_size=total_size,
    )


def stable_tree_identity(root: Path, *, required_equal_passes: int) -> TreeIdentity:
    if not isinstance(required_equal_passes, int) or isinstance(required_equal_passes, bool) or required_equal_passes < 2:
        raise ValueError("stable tree identity requires at least two equal passes")
    observed = _tree_identity(root)
    for _ in range(required_equal_passes - 1):
        repeated = _tree_identity(root)
        if repeated != observed:
            raise ContainmentSafetyError(f"tree identity changed between passes: {root}")
    return observed


def require_tree_integrity(root: Path, expected: IntegrityLevel) -> None:
    paths = (Path(root), *(path for _, path, _ in _walk_direct_tree(root)))
    for path in paths:
        observed = inspect_path_integrity(path)
        if observed is not expected:
            raise ContainmentSafetyError(
                f"integrity mismatch for {path}: expected {expected.name}, observed {observed.name}"
            )


def _exists_no_follow(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _quarantine_entries(entries: tuple[Path, ...], quarantine_root: Path) -> None:
    quarantine = _require_direct_directory(quarantine_root)
    pinned_entries: list[_PinnedObject] = []
    parent_pin: _PinnedObject | None = None
    try:
        for entry in entries:
            if not _exists_no_follow(entry):
                continue
            expected_identity = _identity_at_path(entry)
            pinned_entries.append(
                _pin_object(
                    entry,
                    desired_access=_DELETE | _FILE_READ_ATTRIBUTES,
                    expected_identity=expected_identity,
                    allow_reparse=True,
                )
            )
        parent_pin = _pin_parent_directory(quarantine)
        for pinned in pinned_entries:
            destination = quarantine / pinned.path.name
            try:
                _rename_pinned_object(pinned, destination, parent_pin)
            except FileExistsError as error:
                raise ContainmentSafetyError(
                    f"quarantine collision for {pinned.path.name}"
                ) from error
            except OSError as error:
                raise ContainmentSafetyError(
                    f"cannot quarantine {pinned.path.name}: {error}"
                ) from error
    finally:
        if parent_pin is not None:
            parent_pin.close()
        for pinned in reversed(pinned_entries):
            pinned.close()


def _quarantine_pinned_tree(tree: _PinnedTree, quarantine_root: Path) -> None:
    quarantine = _require_direct_directory(quarantine_root)
    parent_pin = _pin_parent_directory(quarantine)
    try:
        destination = quarantine / tree.current_path.name
        try:
            _rename_pinned_object(tree.root, destination, parent_pin)
        except FileExistsError as error:
            raise ContainmentSafetyError(
                f"quarantine collision for {tree.current_path.name}"
            ) from error
        tree.current_path = destination
        _assert_pinned_root_path(tree)
    finally:
        parent_pin.close()


def _select_unique_new_directory(
    stage_mods: Path,
    source_mods: Path,
    expected_name: str,
    before_names: tuple[str, ...],
    quarantine_root: Path,
) -> Path:
    stage = _require_direct_directory(stage_mods)
    source = _require_direct_directory(source_mods)
    _require_direct_directory(quarantine_root)
    expected = _safe_name(expected_name, "expected name")
    if not isinstance(before_names, tuple):
        raise ContainmentSafetyError("before names must be a tuple")
    validated_before = tuple(_safe_name(name, "before name") for name in before_names)
    before_folded = [name.casefold() for name in validated_before]
    if len(before_folded) != len(set(before_folded)):
        raise ContainmentSafetyError("before names contain a case-insensitive collision")

    entries = _top_level_entries(stage)
    entry_by_fold = {entry.name.casefold(): entry for entry in entries}
    changed: list[Path] = []
    baseline_problem = False
    for before_name in validated_before:
        entry = entry_by_fold.get(before_name.casefold())
        if entry is None:
            baseline_problem = True
            continue
        if entry.name != before_name:
            baseline_problem = True
            changed.append(entry)
            continue
        try:
            expected_target = _require_direct_directory(source / before_name).resolve(
                strict=True
            )
            evidence = inspect_junction(entry)
            if not _same_path(evidence.target_path, expected_target):
                raise ContainmentSafetyError(
                    f"projection target does not match {expected_target}"
                )
        except (ContainmentSafetyError, OSError):
            baseline_problem = True
            changed.append(entry)
    new_entries = tuple(entry for entry in entries if entry.name.casefold() not in set(before_folded))
    if baseline_problem:
        _quarantine_entries(tuple(dict.fromkeys((*changed, *new_entries))), quarantine_root)
        raise ContainmentSafetyError("changed staging projection prevents adoption")
    if len(new_entries) != 1:
        _quarantine_entries(new_entries, quarantine_root)
        raise ContainmentSafetyError(
            f"exactly one new staging entry is required, observed {len(new_entries)}"
        )

    candidate = new_entries[0]
    if candidate.name != expected:
        _quarantine_entries((candidate,), quarantine_root)
        raise ContainmentSafetyError(
            f"new staging entry must have exact expected name {expected!r}"
        )
    if any(entry.name.casefold() == expected.casefold() for entry in _top_level_entries(source)):
        _quarantine_entries((candidate,), quarantine_root)
        raise ContainmentSafetyError(f"case-insensitive source collision for {expected!r}")
    attributes, tag = _attribute_tag(candidate)
    if _is_reparse(attributes, tag) or not attributes & _FILE_ATTRIBUTE_DIRECTORY:
        _quarantine_entries((candidate,), quarantine_root)
        raise ContainmentSafetyError("new staging entry is not a direct regular directory")
    return candidate


def _collision_free_destination(source_mods: Path, expected_name: str) -> Path:
    source = _require_direct_directory(source_mods)
    expected = _safe_name(expected_name, "expected name")
    if any(entry.name.casefold() == expected.casefold() for entry in _top_level_entries(source)):
        raise ContainmentSafetyError(f"case-insensitive source collision for {expected!r}")
    return source / expected


def adopt_unique_staged_mod(
    *,
    stage_mods: Path,
    source_mods: Path,
    expected_name: str,
    before_names: tuple[str, ...],
    quarantine_root: Path,
) -> AdoptionEvidence:
    candidate = _select_unique_new_directory(
        stage_mods,
        source_mods,
        expected_name,
        before_names,
        quarantine_root,
    )
    tree: _PinnedTree | None = None
    destination: Path | None = None
    quarantined = False
    source_parent: _PinnedObject | None = None
    try:
        try:
            tree = _pin_tree(candidate)
        except _PinnedTreeRejected as rejection:
            tree = rejection.tree
            tree.close_descendants()
            _quarantine_pinned_tree(tree, quarantine_root)
            quarantined = True
            raise ContainmentSafetyError(str(rejection)) from rejection
        _assert_pinned_tree(tree)
        before_tree = stable_tree_identity(candidate, required_equal_passes=2)
        _assert_pinned_tree(tree)
        pinned_paths = (tree.root.path,) + tuple(
            entry.pinned.path for entry in tree.entries
        )
        set_medium_integrity_entries(pinned_paths)
        _assert_pinned_tree(tree)
        require_tree_integrity(candidate, IntegrityLevel.MEDIUM)
        _assert_pinned_tree(tree)
        destination = _collision_free_destination(source_mods, expected_name)
        source_parent = _pin_parent_directory(destination.parent)
        tree.close_descendants()
        try:
            _rename_pinned_object(tree.root, destination, source_parent)
        except FileExistsError as error:
            raise ContainmentSafetyError("destination already exists at adoption rename boundary") from error
        tree.current_path = destination
        _pin_descendants(tree)
        _assert_pinned_tree(tree)
        after_tree = stable_tree_identity(destination, required_equal_passes=2)
        _assert_pinned_tree(tree)
        if after_tree != before_tree:
            raise ContainmentSafetyError("adopted tree identity changed")
        require_tree_integrity(destination, IntegrityLevel.MEDIUM)
        _assert_pinned_tree(tree)
        final_attributes, final_tag = _attribute_tag(destination)
        final_is_reparse = _is_reparse(final_attributes, final_tag)
        if final_is_reparse:
            raise ContainmentSafetyError("adopted destination became reparse")
        final_integrity = inspect_path_integrity(destination)
        if final_integrity is not IntegrityLevel.MEDIUM:
            raise ContainmentSafetyError("adopted destination integrity is not Medium")
        return AdoptionEvidence(
            adopted_name=expected_name,
            destination_path=destination,
            before_tree=before_tree,
            after_tree=after_tree,
            final_integrity=final_integrity,
            final_is_reparse=final_is_reparse,
        )
    except BaseException as error:
        if tree is not None and not quarantined:
            try:
                tree.close_descendants()
                tree.current_path = tree.root.path
                _quarantine_pinned_tree(tree, quarantine_root)
                quarantined = True
            except BaseException as quarantine_error:
                raise ContainmentSafetyError(
                    f"adoption failed and quarantine also failed: {quarantine_error}"
                ) from error
        if isinstance(error, ContainmentSafetyError):
            raise
        raise ContainmentSafetyError(f"adoption failed safely: {error}") from error
    finally:
        if source_parent is not None:
            source_parent.close()
        if tree is not None:
            tree.close()


__all__ = [
    "AdoptionEvidence",
    "ContainmentSafetyError",
    "IO_REPARSE_TAG_MOUNT_POINT",
    "JunctionEvidence",
    "adopt_unique_staged_mod",
    "build_projection",
    "create_mod_projection",
    "inspect_junction",
    "reject_reparse_tree",
    "require_tree_integrity",
    "stable_tree_identity",
]
