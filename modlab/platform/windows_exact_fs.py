"""Retained-handle Windows operations for authority-bearing filesystem objects.

The functions in this module deliberately never fall back to a pathname mutation.
Objects are validated while retained, then renamed through their own handles or
created directly relative to an already-retained parent handle.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
from collections.abc import Callable
import re
import secrets
import threading


_DELETE = 0x00010000
_FILE_READ_ATTRIBUTES = 0x00000080
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_DELETE = 0x00000004
_FILE_SHARE_ALL = _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE
_OPEN_EXISTING = 3
_CREATE_NEW = 1
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_LIST_DIRECTORY = 0x0001
_FILE_ADD_FILE = 0x0002
_FILE_ADD_SUBDIRECTORY = 0x0004
_FILE_RENAME_INFO_CLASS = 3
_FILE_DISPOSITION_INFO_CLASS = 4
_FILE_BEGIN = 0
_FILE_TYPE_DISK = 0x0001
_ERROR_FILE_EXISTS = 80
_ERROR_ALREADY_EXISTS = 183
_STATUS_OBJECT_NAME_COLLISION = 0xC0000035
_STATUS_OBJECT_NAME_EXISTS = 0x40000000
_FILE_RENAME_INFORMATION_CLASS = 10
_FILE_CREATE_DISPOSITION = 2
_FILE_DIRECTORY_FILE = 0x00000001
_FILE_CREATED_INFORMATION = 2
_OBJ_CASE_INSENSITIVE = 0x00000040
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


def normalize_identity_attributes(attributes: int, *, is_directory: bool) -> int:
    """Discard non-safety directory flags while preserving file attributes."""
    value = int(attributes)
    if is_directory:
        return value & (_FILE_ATTRIBUTE_DIRECTORY | _FILE_ATTRIBUTE_REPARSE_POINT)
    return value


class ExactObjectError(RuntimeError):
    """A path cannot be proved to name the retained exact object."""


class RetainedObjectRole(Enum):
    """Cleanup authority carried by one retained exact-object owner."""

    CANDIDATE = "candidate"
    DESTINATION_PARENT = "destination_parent"
    VERIFICATION = "verification"


class ExactPublicationPhase(Enum):
    """Furthest authority-bearing phase reached by immutable publication."""

    PRIVATE_CANDIDATE = "private-candidate"
    RENAME_VISIBLE = "rename-visible"


class ExactPublicationEffect(Enum):
    """Destination effect remaining after an incomplete publication unwinds."""

    NO_DESTINATION_CHANGE = "no-destination-change"
    ROLLED_BACK = "rolled-back"
    ROLLBACK_INCOMPLETE = "rollback-incomplete"


@dataclass(frozen=True)
class ExactPublicationFailure:
    """Truthful phase/effect evidence for an incomplete immutable publication."""

    phase: ExactPublicationPhase
    effect: ExactPublicationEffect

    def __post_init__(self) -> None:
        if not isinstance(self.phase, ExactPublicationPhase):
            raise TypeError("publication phase must be ExactPublicationPhase")
        if not isinstance(self.effect, ExactPublicationEffect):
            raise TypeError("publication effect must be ExactPublicationEffect")
        if (
            self.phase is ExactPublicationPhase.PRIVATE_CANDIDATE
            and self.effect is not ExactPublicationEffect.NO_DESTINATION_CHANGE
        ):
            raise ValueError("private candidate cannot report a destination effect")
        if (
            self.phase is ExactPublicationPhase.RENAME_VISIBLE
            and self.effect is ExactPublicationEffect.NO_DESTINATION_CHANGE
        ):
            raise ValueError("rename-visible publication must report rollback state")

    @property
    def completed(self) -> bool:
        return False

    @property
    def changed(self) -> bool:
        return self.phase is ExactPublicationPhase.RENAME_VISIBLE


@dataclass(frozen=True)
class RetainedObjectOwner:
    """One typed cleanup role paired with its exact retained object."""

    role: RetainedObjectRole
    pinned: "PinnedObject"

    def __post_init__(self) -> None:
        if not isinstance(self.role, RetainedObjectRole):
            raise TypeError("retained owner role must be RetainedObjectRole")
        if not isinstance(self.pinned, PinnedObject):
            raise TypeError("retained owner must reference a PinnedObject")


@dataclass(frozen=True)
class ExactDirectoryCreationOutcome:
    """Native evidence returned for one rooted direct-directory create."""

    path: Path
    parent_path: Path
    parent_identity: "PinnedIdentity"
    status: int | None
    information: int
    has_valid_handle: bool
    delete_access: bool = True

    @property
    def proven_created(self) -> bool:
        """Whether the native result itself proves FILE_CREATED success."""
        return self.status == 0 and self.information == _FILE_CREATED_INFORMATION

    @property
    def owns_created_candidate(self) -> bool:
        """Whether a retained handle carries exact candidate cleanup authority."""
        return self.proven_created and self.has_valid_handle and self.delete_access


class ExactDirectoryCreationError(ExactObjectError):
    """A rooted directory create failed with explicit native outcome evidence."""

    def __init__(
        self,
        message: str,
        outcome: ExactDirectoryCreationOutcome,
    ) -> None:
        super().__init__(message)
        self.outcome = outcome


class ExactObjectOwnershipError(ExactObjectError):
    """A failed publication still has explicitly retained live ownership."""

    def __init__(
        self,
        message: str,
        *,
        owners: tuple[RetainedObjectOwner, ...] = (),
        candidate: "PinnedObject | None" = None,
        destination_parent: "PinnedObject | None" = None,
        verification: tuple["PinnedObject", ...] = (),
        publication: ExactPublicationFailure | None = None,
    ) -> None:
        super().__init__(message)
        if not isinstance(owners, tuple):
            raise TypeError("retained ownership must be a tuple")
        if not isinstance(verification, tuple):
            raise TypeError("verification ownership must be a tuple")
        compatibility_owners = (
            *((RetainedObjectOwner(RetainedObjectRole.CANDIDATE, candidate),) if candidate is not None else ()),
            *((RetainedObjectOwner(RetainedObjectRole.DESTINATION_PARENT, destination_parent),) if destination_parent is not None else ()),
            *(RetainedObjectOwner(RetainedObjectRole.VERIFICATION, pinned) for pinned in verification),
        )
        self.owners = _normalize_retained_owners((*owners, *compatibility_owners))
        if not self.owners:
            raise ValueError("exact ownership error requires an explicit live-owner role")
        if publication is not None and not isinstance(
            publication, ExactPublicationFailure
        ):
            raise TypeError("publication evidence must be ExactPublicationFailure")
        self.publication = publication

    @property
    def candidates(self) -> tuple["PinnedObject", ...]:
        return tuple(
            owner.pinned
            for owner in self.owners
            if owner.role is RetainedObjectRole.CANDIDATE
        )

    @property
    def candidate(self) -> "PinnedObject | None":
        return self.candidates[0] if self.candidates else None

    @property
    def destination_parents(self) -> tuple["PinnedObject", ...]:
        return tuple(
            owner.pinned
            for owner in self.owners
            if owner.role is RetainedObjectRole.DESTINATION_PARENT
        )

    @property
    def destination_parent(self) -> "PinnedObject | None":
        return self.destination_parents[0] if self.destination_parents else None

    @property
    def verification(self) -> tuple["PinnedObject", ...]:
        return tuple(
            owner.pinned
            for owner in self.owners
            if owner.role is RetainedObjectRole.VERIFICATION
        )

    @property
    def retained_objects(self) -> tuple["PinnedObject", ...]:
        """Distinct live owners in deterministic union order."""
        return tuple(owner.pinned for owner in self.owners if owner.pinned.handle)

    @property
    def pinned_object(self) -> "PinnedObject":
        return self.retained_objects[0]

    def resolve(self) -> None:
        resolve_retained_ownership(self)


class ExactDirectoryCreationOwnershipError(ExactObjectOwnershipError):
    """A rooted create outcome retains explicitly-role-bound live ownership."""

    def __init__(
        self,
        message: str,
        outcome: ExactDirectoryCreationOutcome,
        *,
        owners: tuple[RetainedObjectOwner, ...] = (),
        candidate: "PinnedObject | None" = None,
        destination_parent: "PinnedObject | None" = None,
        verification: tuple["PinnedObject", ...] = (),
        publication: ExactPublicationFailure | None = None,
    ) -> None:
        super().__init__(
            message,
            owners=owners,
            candidate=candidate,
            destination_parent=destination_parent,
            verification=verification,
            publication=publication,
        )
        self.outcome = outcome
        self.created_candidate = candidate


@dataclass(frozen=True)
class PinnedIdentity:
    volume_serial: int
    file_id: int
    attributes: int


@dataclass
class PinnedObject:
    """One explicitly owned Windows handle and the identity it established."""

    path: Path
    handle: int
    identity: PinnedIdentity | None

    def close(self) -> None:
        if not self.handle:
            return
        handle = self.handle
        _close_handle(handle)
        self.handle = 0


def _normalize_retained_owners(
    owners: tuple[RetainedObjectOwner, ...],
) -> tuple[RetainedObjectOwner, ...]:
    """Deduplicate identical owners while retaining every distinct live owner."""
    normalized: list[RetainedObjectOwner] = []
    positions: dict[int, int] = {}
    for owner in owners:
        if not isinstance(owner, RetainedObjectOwner):
            raise TypeError("retained ownership entries must be RetainedObjectOwner")
        if not owner.pinned.handle:
            continue
        marker = id(owner.pinned)
        position = positions.get(marker)
        if position is None:
            positions[marker] = len(normalized)
            normalized.append(owner)
            continue
        current = normalized[position]
        if (
            current.role is not RetainedObjectRole.CANDIDATE
            and owner.role is RetainedObjectRole.CANDIDATE
        ):
            normalized[position] = owner
    return tuple(normalized)


def union_retained_ownership(
    message: str,
    *,
    prior: BaseException | None = None,
    owners: tuple[RetainedObjectOwner, ...] = (),
    publication: ExactPublicationFailure | None = None,
) -> ExactObjectOwnershipError:
    """Extend ownership without discarding native evidence or failure context."""
    prior_owners = prior.owners if isinstance(prior, ExactObjectOwnershipError) else ()
    prior_publication = (
        prior.publication if isinstance(prior, ExactObjectOwnershipError) else None
    )
    if (
        publication is not None
        and prior_publication is not None
        and publication != prior_publication
    ):
        raise ValueError("cannot replace retained publication evidence during union")
    publication = publication or prior_publication
    if isinstance(prior, ExactDirectoryCreationOwnershipError):
        result = ExactDirectoryCreationOwnershipError(
            message,
            prior.outcome,
            owners=(*prior_owners, *owners),
            candidate=prior.created_candidate,
            publication=publication,
        )
    else:
        result = ExactObjectOwnershipError(
            message,
            owners=(*prior_owners, *owners),
            publication=publication,
        )
    if prior is not None:
        result.__cause__ = prior
        for note in getattr(prior, "__notes__", ()):
            result.add_note(note)
    return result


def _union_ownership(
    message: str,
    *,
    prior: BaseException | None = None,
    owners: tuple[RetainedObjectOwner, ...] = (),
    candidate: PinnedObject | None = None,
    destination_parent: PinnedObject | None = None,
    verification: tuple[PinnedObject, ...] = (),
    publication: ExactPublicationFailure | None = None,
) -> ExactObjectOwnershipError:
    """Build one role-preserving union of every still-live exact owner."""
    additional = (
        *owners,
        *((RetainedObjectOwner(RetainedObjectRole.CANDIDATE, candidate),) if candidate is not None else ()),
        *((RetainedObjectOwner(RetainedObjectRole.DESTINATION_PARENT, destination_parent),) if destination_parent is not None else ()),
        *(RetainedObjectOwner(RetainedObjectRole.VERIFICATION, pinned) for pinned in verification),
    )
    return union_retained_ownership(
        message,
        prior=prior,
        owners=additional,
        publication=publication,
    )


def _directory_creation_ownership(
    message: str,
    outcome: ExactDirectoryCreationOutcome,
    *,
    prior: BaseException | None = None,
    owners: tuple[RetainedObjectOwner, ...] = (),
    candidate: PinnedObject | None = None,
    verification: tuple[PinnedObject, ...] = (),
) -> ExactDirectoryCreationOwnershipError:
    prior_owners = prior.owners if isinstance(prior, ExactObjectOwnershipError) else ()
    return ExactDirectoryCreationOwnershipError(
        message,
        outcome,
        owners=(*prior_owners, *owners),
        candidate=candidate,
        verification=verification,
    )


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
    _fields_ = [("DeleteFile", wintypes.BOOLEAN)]


class _IO_STATUS_BLOCK(ctypes.Structure):
    _fields_ = [("Status", ctypes.c_long), ("Information", ctypes.c_size_t)]


class _UNICODE_STRING(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", wintypes.LPWSTR),
    ]


class _OBJECT_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.ULONG),
        ("RootDirectory", wintypes.HANDLE),
        ("ObjectName", ctypes.POINTER(_UNICODE_STRING)),
        ("Attributes", wintypes.ULONG),
        ("SecurityDescriptor", ctypes.c_void_p),
        ("SecurityQualityOfService", ctypes.c_void_p),
    ]


if os.name == "nt":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetFileAttributesW.restype = wintypes.DWORD
    _kernel32.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION)]
    _kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    _kernel32.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
    _kernel32.GetFileSizeEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_longlong)]
    _kernel32.GetFileSizeEx.restype = wintypes.BOOL
    _kernel32.SetFilePointerEx.argtypes = [wintypes.HANDLE, ctypes.c_longlong, ctypes.POINTER(ctypes.c_longlong), wintypes.DWORD]
    _kernel32.SetFilePointerEx.restype = wintypes.BOOL
    _kernel32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    _kernel32.ReadFile.restype = wintypes.BOOL
    _kernel32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    _kernel32.WriteFile.restype = wintypes.BOOL
    _kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    _kernel32.FlushFileBuffers.restype = wintypes.BOOL
    _kernel32.GetFileType.argtypes = [wintypes.HANDLE]
    _kernel32.GetFileType.restype = wintypes.DWORD
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    _ntdll.NtSetInformationFile.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_IO_STATUS_BLOCK),
        ctypes.c_void_p,
        wintypes.ULONG,
        ctypes.c_int,
    ]
    _ntdll.NtSetInformationFile.restype = ctypes.c_long
    _ntdll.NtCreateFile.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        ctypes.POINTER(_OBJECT_ATTRIBUTES),
        ctypes.POINTER(_IO_STATUS_BLOCK),
        ctypes.c_void_p,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        ctypes.c_void_p,
        wintypes.ULONG,
    ]
    _ntdll.NtCreateFile.restype = ctypes.c_long
    _ntdll.RtlNtStatusToDosError.argtypes = [ctypes.c_long]
    _ntdll.RtlNtStatusToDosError.restype = wintypes.ULONG
else:
    _kernel32 = None
    _ntdll = None


def _require_windows() -> None:
    if os.name != "nt":
        raise OSError("Windows exact-object APIs are unavailable")


def _winerror(message: str, code: int | None = None) -> OSError:
    if code is None:
        code = ctypes.get_last_error()
    return OSError(code, f"{message}: {ctypes.FormatError(code)}")


def _close_handle(handle: int) -> None:
    if handle and handle != _INVALID_HANDLE_VALUE and not _kernel32.CloseHandle(handle):
        raise _winerror("CloseHandle failed")


def _absolute(path: Path) -> Path:
    target = Path(path)
    if not target.is_absolute():
        raise ExactObjectError(f"path must be absolute: {target}")
    text = str(target)
    if text.startswith(("\\\\", "\\?\\", "\\.\\")):
        raise ExactObjectError(f"device and UNC paths are not allowed: {target}")
    return Path(os.path.abspath(target))


def _is_reparse(attributes: int) -> bool:
    return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def _require_direct_components(path: Path) -> Path:
    target = _absolute(path)
    current = Path(target.anchor)
    for part in target.parts[1:]:
        current /= part
        attributes = _kernel32.GetFileAttributesW(str(current))
        if attributes == 0xFFFFFFFF:
            raise _winerror(f"could not inspect direct path component {current}")
        if _is_reparse(int(attributes)):
            raise ExactObjectError(f"reparse component is not allowed: {current}")
    return target


def _open_no_follow(path: Path, desired_access: int) -> int:
    _require_windows()
    handle = _kernel32.CreateFileW(str(path), desired_access, _FILE_SHARE_ALL, None, _OPEN_EXISTING,
                                   _FILE_FLAG_OPEN_REPARSE_POINT | _FILE_FLAG_BACKUP_SEMANTICS, None)
    if handle == _INVALID_HANDLE_VALUE:
        raise _winerror(f"CreateFileW failed for {path}")
    return handle


def _identity_from_information(information: _BY_HANDLE_FILE_INFORMATION) -> PinnedIdentity:
    return PinnedIdentity(int(information.dwVolumeSerialNumber),
                          (int(information.nFileIndexHigh) << 32) | int(information.nFileIndexLow),
                          int(information.dwFileAttributes))


def _handle_identity(handle: int, path: Path) -> PinnedIdentity:
    information = _BY_HANDLE_FILE_INFORMATION()
    if not _kernel32.GetFileInformationByHandle(handle, ctypes.byref(information)):
        raise _winerror(f"GetFileInformationByHandle failed for {path}")
    return _identity_from_information(information)


def _identity_key(identity: object) -> tuple[int, int]:
    if isinstance(identity, PinnedIdentity):
        return identity.volume_serial, identity.file_id
    volume_serial, file_id = identity  # compatibility for retained junction pins during migration
    return int(volume_serial), int(file_id)


def identity_at_path(path: Path) -> PinnedIdentity:
    target = _require_direct_components(path)
    handle = _open_no_follow(target, _FILE_READ_ATTRIBUTES)
    retained = PinnedObject(target, handle, None)
    try:
        identity = _handle_identity(handle, target)
        retained.identity = identity
    except BaseException as error:
        try:
            retained.close()
        except BaseException as close_error:
            if retained.handle:
                raise ExactObjectOwnershipError(
                    "identity lookup retained a live verification handle after "
                    f"validation and close failed: {close_error}",
                    verification=(retained,),
                ) from error
        raise
    try:
        retained.close()
    except BaseException as close_error:
        if retained.handle:
            raise ExactObjectOwnershipError(
                "identity lookup retained a live verification handle after close "
                f"failed: {close_error}",
                verification=(retained,),
            ) from close_error
        raise
    return identity


def pin_direct_object(
    path: Path, kind: str, *, delete_access: bool = True,
) -> PinnedObject:
    """Retain the direct regular file or directory currently named by *path*.

    Publication parents may omit DELETE access to coexist with a retained
    rename/delete guard. Mutation-source callers retain DELETE by default.
    """
    if kind not in {"file", "directory"}:
        raise ExactObjectError(f"unsupported pinned object kind: {kind!r}")
    target = _require_direct_components(path)
    desired_access = (_DELETE if delete_access else 0) | _FILE_READ_ATTRIBUTES | _GENERIC_READ
    if kind == "directory":
        desired_access |= _FILE_LIST_DIRECTORY | _FILE_ADD_FILE | _FILE_ADD_SUBDIRECTORY
    handle = _open_no_follow(target, desired_access)
    retained = PinnedObject(target, handle, None)
    try:
        identity = _handle_identity(handle, target)
        retained.identity = identity
        if _is_reparse(identity.attributes):
            raise ExactObjectError(f"reparse object cannot be pinned: {target}")
        is_directory = bool(identity.attributes & _FILE_ATTRIBUTE_DIRECTORY)
        if kind == "directory" and not is_directory:
            raise ExactObjectError(f"direct regular directory required: {target}")
        if kind == "file" and (is_directory or _kernel32.GetFileType(handle) != _FILE_TYPE_DISK):
            raise ExactObjectError(f"direct regular file required: {target}")
        return retained
    except BaseException as error:
        try:
            retained.close()
        except BaseException as close_error:
            if retained.handle:
                raise ExactObjectOwnershipError(
                    "direct-object validation retained a live acquisition handle "
                    f"after close failed: {close_error}",
                    verification=(retained,),
                ) from error
        raise


def pin_stable_direct_object(
    path: Path,
    kind: str,
    *,
    allow_writes: bool = False,
) -> PinnedObject:
    """Retain one direct object while denying rename/delete.

    Snapshot pins also deny concurrent writers.  A mutation-root guard may set
    ``allow_writes`` so its authorized delegate can mutate children while the
    root itself remains non-replaceable.
    """
    if kind not in {"file", "directory"}:
        raise ExactObjectError(f"unsupported pinned object kind: {kind!r}")
    target = _absolute(path)
    desired_access = (
        _DELETE | _FILE_READ_ATTRIBUTES
        if kind == "directory"
        else _DELETE | _FILE_READ_ATTRIBUTES | _GENERIC_READ
    )
    handle = _kernel32.CreateFileW(
        str(target),
        desired_access,
        _FILE_SHARE_READ | (_FILE_SHARE_WRITE if allow_writes else 0),
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_OPEN_REPARSE_POINT | _FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        raise _winerror(f"stable CreateFileW failed for {target}")
    retained = PinnedObject(target, handle, None)
    try:
        identity = _handle_identity(handle, target)
        retained.identity = identity
        if _is_reparse(identity.attributes):
            raise ExactObjectError(f"reparse object cannot be observed: {target}")
        is_directory = bool(identity.attributes & _FILE_ATTRIBUTE_DIRECTORY)
        if kind == "directory" and not is_directory:
            raise ExactObjectError(f"direct regular directory required: {target}")
        if kind == "file" and (
            is_directory or _kernel32.GetFileType(handle) != _FILE_TYPE_DISK
        ):
            raise ExactObjectError(f"direct regular file required: {target}")
        return retained
    except BaseException as error:
        try:
            retained.close()
        except BaseException as close_error:
            if retained.handle:
                raise ExactObjectOwnershipError(
                    "stable direct-object validation retained a live handle after "
                    f"close failed: {close_error}",
                    verification=(retained,),
                ) from error
        raise


def create_pinned_new(path: Path, destination_parent: PinnedObject) -> PinnedObject:
    """Create a direct regular file and retain its first handle without reopening it."""
    _require_windows()
    target = _absolute(path)
    _safe_destination_name(target, destination_parent)
    if not _parent_path_is_retained(destination_parent, identity_at_path):
        raise ExactObjectError("pinned create destination parent changed")
    handle = _kernel32.CreateFileW(
        str(target),
        _DELETE | _FILE_READ_ATTRIBUTES | _GENERIC_READ | _GENERIC_WRITE,
        _FILE_SHARE_ALL,
        None,
        _CREATE_NEW,
        _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        error = ctypes.get_last_error()
        if error in {_ERROR_FILE_EXISTS, _ERROR_ALREADY_EXISTS}:
            raise FileExistsError(error, "destination already exists", str(target))
        raise _winerror(f"CreateFileW create-new failed for {target}", error)
    candidate = PinnedObject(target, handle, None)
    try:
        identity = _handle_identity(handle, target)
        candidate.identity = identity
        if _is_reparse(identity.attributes) or identity.attributes & _FILE_ATTRIBUTE_DIRECTORY:
            raise ExactObjectError(f"new candidate is not a direct regular file: {target}")
        if not _parent_path_is_retained(destination_parent, identity_at_path):
            raise ExactObjectError("pinned create destination parent changed during creation")
        return candidate
    except BaseException as error:
        cleanup_error: BaseException | None = None
        try:
            delete_pinned_object(candidate)
        except BaseException as candidate_cleanup_error:
            cleanup_error = candidate_cleanup_error
        if candidate.handle:
            raise _union_ownership(
                "new-candidate validation retained live ownership after exact "
                f"cleanup failed: {cleanup_error}",
                prior=error,
                candidate=candidate,
            ) from error
        raise


def create_pinned_directory_child(
    path: Path,
    destination_parent: PinnedObject,
    *,
    identity_at_path_fn: Callable[[Path], PinnedIdentity] | None = None,
    initial_security_descriptor: int | None = None,
    read_control: bool = False,
    delete_access: bool = True,
) -> PinnedObject:
    """Create one direct directory relative to a retained parent handle.

    The native create returns the first handle to the new object.  That handle
    denies rename/delete from the instant the directory exists, so validation
    never crosses a pathname-only ownership gap.
    ``initial_security_descriptor`` is a caller-owned native descriptor address
    kept alive through this call; it applies at creation, never after opening.
    ``read_control`` grants security inspection on the first retained handle.
    ``delete_access=False`` allows concurrent readers that also deny deletion.
    Such handles have close-only cleanup ownership: validation failure leaves
    the created directory in place, without acquiring deletion authority later.
    """
    _require_windows()
    if identity_at_path_fn is None:
        identity_at_path_fn = identity_at_path
    if not isinstance(destination_parent, PinnedObject):
        raise ExactObjectError(
            "pinned directory creation requires a PinnedObject parent"
        )
    if not destination_parent.handle or destination_parent.identity is None:
        raise ExactObjectError(
            "pinned directory creation requires a live parent handle"
        )
    target = _absolute(path)
    name = _safe_destination_name(target, destination_parent)

    parent_identity = _handle_identity(
        destination_parent.handle,
        destination_parent.path,
    )
    if parent_identity != destination_parent.identity:
        raise ExactObjectError(
            "pinned directory creation parent handle identity changed"
        )
    if (
        _is_reparse(parent_identity.attributes)
        or not parent_identity.attributes & _FILE_ATTRIBUTE_DIRECTORY
    ):
        raise ExactObjectError("pinned directory creation requires a direct parent")
    if identity_at_path_fn(destination_parent.path) != destination_parent.identity:
        raise ExactObjectError("pinned directory creation parent path changed")

    name_buffer = ctypes.create_unicode_buffer(name)
    encoded_length = len(name.encode("utf-16-le"))
    object_name = _UNICODE_STRING(
        encoded_length,
        encoded_length + ctypes.sizeof(ctypes.c_wchar),
        ctypes.cast(name_buffer, wintypes.LPWSTR),
    )
    object_attributes = _OBJECT_ATTRIBUTES(
        ctypes.sizeof(_OBJECT_ATTRIBUTES),
        destination_parent.handle,
        ctypes.pointer(object_name),
        _OBJ_CASE_INSENSITIVE,
        initial_security_descriptor,
        None,
    )
    io_status = _IO_STATUS_BLOCK()
    output_handle = wintypes.HANDLE()
    native_error: BaseException | None = None
    status = None
    try:
        status = _ntdll.NtCreateFile(
            ctypes.byref(output_handle),
            (_DELETE if delete_access else 0)
            | (0x00020000 if read_control else 0)
            | _FILE_READ_ATTRIBUTES
            | _FILE_LIST_DIRECTORY
            | _FILE_ADD_FILE
            | _FILE_ADD_SUBDIRECTORY,
            ctypes.byref(object_attributes),
            ctypes.byref(io_status),
            None,
            _FILE_ATTRIBUTE_DIRECTORY,
            _FILE_SHARE_READ | _FILE_SHARE_WRITE,
            _FILE_CREATE_DISPOSITION,
            _FILE_DIRECTORY_FILE | _FILE_FLAG_OPEN_REPARSE_POINT,
            None,
            0,
        )
    except BaseException as error:
        native_error = error
    handle = output_handle.value
    has_valid_handle = bool(handle and handle != _INVALID_HANDLE_VALUE)
    retained = (
        PinnedObject(target, int(handle), None)
        if has_valid_handle
        else None
    )
    outcome = ExactDirectoryCreationOutcome(
        path=target,
        parent_path=destination_parent.path,
        parent_identity=parent_identity,
        status=status,
        information=int(io_status.Information),
        has_valid_handle=has_valid_handle,
        delete_access=delete_access,
    )
    if native_error is not None:
        # Information alone is not a reliable native result.  An interrupted
        # output handle can only be closed, never treated as a created candidate.
        native_error.add_note(f"interrupted rooted directory creation: {outcome!r}")
        cleanup_error: BaseException | None = None
        if retained is not None:
            try:
                retained.close()
            except BaseException as error:
                cleanup_error = error
                native_error.add_note(f"interrupted-create handle close failed: {error!r}")
        owners = _normalize_retained_owners((
            *(native_error.owners if isinstance(native_error, ExactObjectOwnershipError) else ()),
            *(cleanup_error.owners if isinstance(cleanup_error, ExactObjectOwnershipError) else ()),
            *((RetainedObjectOwner(RetainedObjectRole.VERIFICATION, retained),) if retained is not None else ()),
        ))
        if owners:
            raise _directory_creation_ownership(
                "interrupted rooted directory creation retained close-only ownership",
                outcome,
                owners=owners,
            ) from native_error
        raise native_error
    if status != 0:
        unsigned_status = status & 0xFFFFFFFF
        if retained is not None:
            raise _directory_creation_ownership(
                "rooted directory creation returned contradictory live ownership "
                f"with status 0x{unsigned_status:08x}",
                outcome,
                verification=(retained,),
            )
        if unsigned_status in {_STATUS_OBJECT_NAME_COLLISION, _STATUS_OBJECT_NAME_EXISTS}:
            raise FileExistsError(
                _ERROR_FILE_EXISTS,
                "destination already exists",
                str(target),
            )
        error = int(_ntdll.RtlNtStatusToDosError(status))
        raise _winerror(f"rooted directory creation failed for {target}", error)
    if retained is None:
        raise ExactDirectoryCreationError(
            "rooted directory creation returned no ownership handle",
            outcome,
        )
    if not outcome.proven_created:
        raise _directory_creation_ownership(
            "rooted directory creation did not prove a newly created object",
            outcome,
            verification=(retained,),
        )
    candidate = retained
    try:
        child_identity = _handle_identity(candidate.handle, target)
        candidate.identity = child_identity
        if (
            _is_reparse(child_identity.attributes)
            or not child_identity.attributes & _FILE_ATTRIBUTE_DIRECTORY
        ):
            raise ExactObjectError(
                f"new child is not a direct regular directory: {target}"
            )
        if child_identity.volume_serial != parent_identity.volume_serial:
            raise ExactObjectError(
                "pinned directory creation requires parent and child on the same volume"
            )
        if (
            _handle_identity(
                destination_parent.handle,
                destination_parent.path,
            )
            != destination_parent.identity
            or identity_at_path_fn(destination_parent.path)
            != destination_parent.identity
        ):
            raise ExactObjectError(
                "pinned directory creation parent changed during creation"
            )
        if identity_at_path_fn(target) != child_identity:
            raise ExactObjectError(
                "created directory pathname is not the retained child object"
            )
        return candidate
    except BaseException as error:
        cleanup_error: BaseException | None = None
        try:
            if delete_access:
                delete_pinned_object(candidate)
            else:
                candidate.close()
        except BaseException as candidate_cleanup_error:
            cleanup_error = candidate_cleanup_error
        if candidate.handle:
            raise _directory_creation_ownership(
                "new-directory validation retained live ownership after exact "
                f"cleanup failed: {cleanup_error}",
                outcome,
                prior=error,
                candidate=candidate if delete_access else None,
                verification=() if delete_access else (candidate,),
            ) from error
        if not isinstance(error, Exception):
            raise
        creation_error = (
            _directory_creation_ownership(
                "new-directory validation retained verification ownership after "
                f"candidate cleanup: {error}",
                outcome,
                prior=error,
            )
            if isinstance(error, ExactObjectOwnershipError)
            else ExactDirectoryCreationError(
                f"new-directory validation failed after exact cleanup: {error}",
                outcome,
            )
        )
        if cleanup_error is not None and hasattr(creation_error, "add_note"):
            creation_error.add_note(
                "new-directory exact cleanup completed with an error: "
                f"{cleanup_error}"
            )
        raise creation_error from error


def write_pinned_file(source: PinnedObject, data: bytes) -> None:
    """Write and flush bytes through the retained candidate handle."""
    if type(data) is not bytes:
        raise ExactObjectError("pinned file data must be bytes")
    if not source.handle:
        raise ExactObjectError("cannot write a closed pinned object")
    if not _kernel32.SetFilePointerEx(source.handle, ctypes.c_longlong(0), None, _FILE_BEGIN):
        raise _winerror(f"SetFilePointerEx failed for {source.path}")
    remaining = memoryview(data)
    while remaining:
        written = wintypes.DWORD()
        buffer = ctypes.create_string_buffer(remaining.tobytes())
        if not _kernel32.WriteFile(source.handle, buffer, len(remaining), ctypes.byref(written), None):
            raise _winerror(f"WriteFile failed for {source.path}")
        if written.value == 0:
            raise ExactObjectError(f"pinned file write was short: {source.path}")
        remaining = remaining[written.value:]
    if not _kernel32.FlushFileBuffers(source.handle):
        raise _winerror(f"FlushFileBuffers failed for {source.path}")


def _safe_destination_name(destination: Path, destination_parent: object) -> str:
    parent_path = Path(destination_parent.path)
    destination_path = _absolute(destination)
    if destination_path.parent != parent_path:
        raise ExactObjectError("pinned rename destination parent mismatch")
    name = destination_path.name
    if not name or name in {".", ".."} or any(character in name for character in "\\/:\0"):
        raise ExactObjectError(f"destination must be a direct child name: {destination_path}")
    if name[-1] in {" ", "."}:
        raise ExactObjectError(f"destination name is not canonical: {name!r}")
    return name


def _parent_path_is_retained(
    destination_parent: object,
    identity_at_path_fn: Callable[[Path], object],
) -> bool:
    observed = identity_at_path_fn(Path(destination_parent.path))
    return _identity_key(observed) == _identity_key(destination_parent.identity)


def _case_insensitive_destination_exists(destination_parent: object, name: str) -> bool:
    with os.scandir(destination_parent.path) as entries:
        return any(entry.name.casefold() == name.casefold() for entry in entries)


def rename_pinned_no_replace(
    source: object,
    destination: Path,
    destination_parent: object,
    *,
    identity_at_path_fn: Callable[[Path], object] = identity_at_path,
) -> None:
    """Rename a retained object through a retained parent, without replacement."""
    _require_windows()
    if not getattr(source, "handle", 0) or not getattr(destination_parent, "handle", 0):
        raise ExactObjectError("pinned rename requires live source and parent handles")
    name = _safe_destination_name(destination, destination_parent)
    destination_path = _absolute(destination)
    if not _parent_path_is_retained(destination_parent, identity_at_path_fn):
        raise ExactObjectError("pinned rename destination parent changed")
    if _case_insensitive_destination_exists(destination_parent, name):
        raise FileExistsError(_ERROR_FILE_EXISTS, "destination already exists", str(destination_path))
    source_identity = _handle_identity(source.handle, Path(source.path))
    parent_identity = _handle_identity(destination_parent.handle, Path(destination_parent.path))
    if _identity_key(source_identity) != _identity_key(source.identity):
        raise ExactObjectError("pinned rename source identity changed")
    if _identity_key(parent_identity) != _identity_key(destination_parent.identity):
        raise ExactObjectError("pinned rename destination parent handle changed")
    if source_identity.volume_serial != parent_identity.volume_serial:
        raise ExactObjectError("rename requires source and destination on the same volume")
    encoded_name = name.encode("utf-16-le")
    name_offset = _FILE_RENAME_INFO.FileName.offset
    native_buffer = ctypes.create_string_buffer(name_offset + len(encoded_name) + 2)
    rename = _FILE_RENAME_INFO.from_buffer(native_buffer)
    rename.Flags = 0
    rename.RootDirectory = destination_parent.handle
    rename.FileNameLength = len(encoded_name)
    ctypes.memmove(ctypes.addressof(native_buffer) + name_offset, encoded_name, len(encoded_name))
    io_status = _IO_STATUS_BLOCK()
    status = int(_ntdll.NtSetInformationFile(
        source.handle,
        ctypes.byref(io_status),
        native_buffer,
        len(native_buffer),
        _FILE_RENAME_INFORMATION_CLASS,
    ))
    if status != 0:
        unsigned_status = status & 0xFFFFFFFF
        if unsigned_status in {_STATUS_OBJECT_NAME_COLLISION, _STATUS_OBJECT_NAME_EXISTS}:
            raise FileExistsError(_ERROR_FILE_EXISTS, "destination already exists", str(destination_path))
        error = int(_ntdll.RtlNtStatusToDosError(status))
        raise _winerror(f"rooted handle-based no-replace rename failed for {destination_path}", error)
    source.path = destination_path
    if not _parent_path_is_retained(destination_parent, identity_at_path_fn):
        raise ExactObjectError("pinned rename destination parent changed during rename")
    if _identity_key(identity_at_path_fn(destination_path)) != _identity_key(source.identity):
        raise ExactObjectError("renamed destination is not the pinned source object")


def read_pinned_file(source: PinnedObject) -> bytes:
    """Read a retained direct regular file without reopening its mutable pathname."""
    if not source.handle:
        raise ExactObjectError("cannot read a closed pinned object")
    identity = _handle_identity(source.handle, source.path)
    if _identity_key(identity) != _identity_key(source.identity):
        raise ExactObjectError("pinned file identity changed before read")
    if _is_reparse(identity.attributes) or identity.attributes & _FILE_ATTRIBUTE_DIRECTORY:
        raise ExactObjectError(f"direct regular file required: {source.path}")
    size = ctypes.c_longlong()
    if not _kernel32.GetFileSizeEx(source.handle, ctypes.byref(size)):
        raise _winerror(f"GetFileSizeEx failed for {source.path}")
    if size.value < 0:
        raise ExactObjectError(f"pinned file size is negative: {source.path}")
    if not _kernel32.SetFilePointerEx(source.handle, ctypes.c_longlong(0), None, _FILE_BEGIN):
        raise _winerror(f"SetFilePointerEx failed for {source.path}")
    parts: list[bytes] = []
    remaining = size.value
    while remaining:
        amount = min(remaining, 64 * 1024)
        buffer = ctypes.create_string_buffer(amount)
        transferred = wintypes.DWORD()
        if not _kernel32.ReadFile(source.handle, buffer, amount, ctypes.byref(transferred), None):
            raise _winerror(f"ReadFile failed for {source.path}")
        if transferred.value == 0:
            raise ExactObjectError(f"pinned file read was short: {source.path}")
        parts.append(bytes(buffer[: transferred.value]))
        remaining -= transferred.value
    if _identity_key(_handle_identity(source.handle, source.path)) != _identity_key(source.identity):
        raise ExactObjectError("pinned file identity changed during read")
    return b"".join(parts)


def delete_pinned_object(source: PinnedObject) -> None:
    """Delete only the retained object, then close its ownership handle."""
    if not source.handle:
        raise ExactObjectError("cannot delete a closed pinned object")
    disposition = _FILE_DISPOSITION_INFO(True)
    if not _kernel32.SetFileInformationByHandle(source.handle, _FILE_DISPOSITION_INFO_CLASS, ctypes.byref(disposition), ctypes.sizeof(disposition)):
        raise _winerror(f"handle-based exact cleanup failed for {source.path}")
    source.close()


def resolve_retained_ownership(error: ExactObjectOwnershipError) -> None:
    """Retry exact cleanup without ever reopening a mutable candidate pathname."""
    cleanup_errors: list[BaseException] = []
    unresolved: list[RetainedObjectOwner] = []
    for owner in error.owners:
        pinned = owner.pinned
        if not pinned.handle:
            continue
        try:
            if owner.role is RetainedObjectRole.CANDIDATE:
                delete_pinned_object(pinned)
            else:
                pinned.close()
        except BaseException as cleanup_error:
            cleanup_errors.append(cleanup_error)
            if isinstance(cleanup_error, ExactObjectOwnershipError):
                unresolved.extend(cleanup_error.owners)
        if pinned.handle:
            unresolved.append(owner)
    remaining = _normalize_retained_owners(tuple(unresolved))
    if remaining:
        message = "retained exact-object retry cleanup failed: " + "; ".join(
            repr(cleanup_error) for cleanup_error in cleanup_errors
        )
        raise union_retained_ownership(
            message,
            prior=error,
            owners=remaining,
        ) from error


def publication_candidate_path(
    destination: Path,
    pid: int,
    thread_id: int,
    token: str,
) -> Path:
    """Build the bounded private name used by immutable publication."""
    if (type(pid) is not int or not 1 <= pid <= 0xFFFFFFFF
            or type(thread_id) is not int or not 1 <= thread_id <= 0xFFFFFFFF):
        raise ExactObjectError("publication PID and thread identity must fit unsigned DWORD")
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{16}", token):
        raise ExactObjectError("publication token must be 16 lowercase hex characters")
    target = Path(destination)
    return target.with_name(f".{target.name}.{pid}.{thread_id}.{token}.tmp")


def publish_new_pinned(
    path: Path,
    data: bytes,
    validator: Callable[[bytes], object],
    *,
    destination_parent: PinnedObject | None = None,
) -> object:
    """Create, validate, and publish immutable bytes through one retained owner.

    The validator runs before and after the rooted rename while the same source
    handle remains open.  Any failure before both owners close deletes only the
    retained candidate, including a candidate already moved to its destination.
    """
    destination = _absolute(path)
    temporary = publication_candidate_path(
        destination,
        os.getpid(),
        threading.get_ident(),
        secrets.token_hex(8),
    )
    candidate: PinnedObject | None = None
    primary_error: BaseException | None = None
    completed = False
    renamed = False
    borrowed_parent = destination_parent is not None
    parent = (
        destination_parent
        if borrowed_parent
        else pin_direct_object(destination.parent, kind="directory", delete_access=False)
    )
    if (
        not isinstance(parent, PinnedObject)
        or not parent.handle
        or parent.identity is None
        or parent.path != destination.parent
    ):
        raise ExactObjectError(
            "immutable publication requires its exact destination-parent owner"
        )
    try:
        candidate = create_pinned_new(temporary, parent)
        write_pinned_file(candidate, data)
        candidate_bytes = read_pinned_file(candidate)
        if candidate_bytes != data:
            raise ExactObjectError("private candidate retained-handle readback mismatch")
        validator(candidate_bytes)
        rename_pinned_no_replace(candidate, destination, parent)
        renamed = True
        published_bytes = read_pinned_file(candidate)
        if published_bytes != data:
            raise ExactObjectError("published candidate retained-handle readback mismatch")
        result = validator(published_bytes)
        if not borrowed_parent:
            parent.close()
            parent = None
        candidate.close()
        candidate = None
        completed = True
        return result
    except BaseException as error:
        primary_error = error
        raise
    finally:
        cleanup_errors: list[BaseException] = []
        retained_objects: list[PinnedObject] = []
        rename_visible = renamed or (
            candidate is not None and candidate.path == destination
        )
        rolled_back = False
        if candidate is not None:
            try:
                delete_pinned_object(candidate)
                candidate = None
                rolled_back = rename_visible
            except BaseException as cleanup_error:
                cleanup_errors.append(cleanup_error)
                if candidate.handle:
                    retained_objects.append(candidate)
        if parent is not None and not borrowed_parent:
            try:
                parent.close()
            except BaseException as close_error:
                cleanup_errors.append(close_error)
                if parent.handle:
                    retained_objects.append(parent)
        prior_ownership = (
            primary_error
            if isinstance(primary_error, ExactObjectOwnershipError)
            else None
        )
        if retained_objects or (
            prior_ownership is not None and prior_ownership.retained_objects
        ):
            ownership_error = _union_ownership(
                "retained exact-object cleanup failed; caller owns live handle(s): "
                + "; ".join(str(error) for error in cleanup_errors),
                prior=prior_ownership,
                candidate=candidate if candidate is not None and candidate.handle else None,
                destination_parent=(
                    parent
                    if not borrowed_parent and parent is not None and parent.handle
                    else None
                ),
                publication=ExactPublicationFailure(
                    phase=(
                        ExactPublicationPhase.RENAME_VISIBLE
                        if rename_visible
                        else ExactPublicationPhase.PRIVATE_CANDIDATE
                    ),
                    effect=(
                        ExactPublicationEffect.ROLLED_BACK
                        if rolled_back
                        else (
                            ExactPublicationEffect.ROLLBACK_INCOMPLETE
                            if rename_visible
                            else ExactPublicationEffect.NO_DESTINATION_CHANGE
                        )
                    ),
                ),
            )
            if primary_error is not None:
                raise ownership_error from primary_error
            raise ownership_error from cleanup_errors[0]
        if primary_error is not None and cleanup_errors:
            primary_error.add_note(
                "retained exact-object cleanup completed with errors: "
                + "; ".join(str(error) for error in cleanup_errors)
            )
        elif cleanup_errors and not completed:
            raise cleanup_errors[0]
