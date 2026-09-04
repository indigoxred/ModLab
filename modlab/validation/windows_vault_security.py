"""Small native Medium evidence boundary for trusted controller/watcher lifetimes.

Keep the vault open throughout authoritative work. Its root and every ancestor
remain pinned against rename/delete; verify immediately before authoritative
reads/writes and after delegated work. Descendant verification is a policy check,
not a retained snapshot: use the exact-file primitives for byte/identity reads.
The creating account and SYSTEM are trusted; same-account Medium code is not
authenticated here. No existing object's security is repaired or changed.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
from pathlib import Path

from modlab.platform import windows_exact_fs as exact

__all__ = ['VaultPrincipal', 'EvidenceVault', 'current_vault_principal', 'create_vault', 'open_vault']

_READ_CONTROL = 0x20000
_FULL = 0x1F01FF
_MEDIUM = 0x2000
_SYSTEM = 'S-1-5-18'


@dataclass(frozen=True)
class VaultPrincipal:
    sid: str
    integrity_rid: int
    mandatory_policy: int


@dataclass(frozen=True)
class _Policy:
    owner: str
    protected: bool
    dacl: tuple[tuple[int, int, int, str], ...] | None
    labels: tuple[tuple[int, int, int, str], ...]


class _ACL(ctypes.Structure):
    _fields_ = [('revision', wintypes.BYTE), ('reserved', wintypes.BYTE), ('size', wintypes.WORD), ('count', wintypes.WORD), ('reserved2', wintypes.WORD)]


class _SID_ATTRIBUTES(ctypes.Structure):
    _fields_ = [('sid', ctypes.c_void_p), ('attributes', wintypes.DWORD)]


class _GENERIC_MAPPING(ctypes.Structure):
    _fields_ = [('read', wintypes.DWORD), ('write', wintypes.DWORD), ('execute', wintypes.DWORD), ('all', wintypes.DWORD)]


if os.name == 'nt':
    _advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    _kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    _P = ctypes.c_void_p
    _PP = ctypes.POINTER(_P)
    _PD = ctypes.POINTER(wintypes.DWORD)
    for name, args, result in (
        ('OpenThreadToken', [wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, ctypes.POINTER(wintypes.HANDLE)], wintypes.BOOL),
        ('OpenProcessToken', [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)], wintypes.BOOL),
        ('GetTokenInformation', [wintypes.HANDLE, ctypes.c_int, _P, wintypes.DWORD, _PD], wintypes.BOOL),
        ('DuplicateToken', [wintypes.HANDLE, ctypes.c_int, ctypes.POINTER(wintypes.HANDLE)], wintypes.BOOL),
        ('ConvertSidToStringSidW', [_P, _PP], wintypes.BOOL),
        ('IsValidSid', [_P], wintypes.BOOL),
        ('ConvertStringSecurityDescriptorToSecurityDescriptorW', [wintypes.LPCWSTR, wintypes.DWORD, _PP, _PD], wintypes.BOOL),
        ('GetSecurityInfo', [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD, _PP, _PP, _PP, _PP, _PP], wintypes.DWORD),
        ('GetSecurityDescriptorControl', [_P, ctypes.POINTER(wintypes.WORD), _PD], wintypes.BOOL),
        ('AccessCheck', [_P, wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(_GENERIC_MAPPING), _P, _PD, _PD, ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL),
    ):
        function = getattr(_advapi, name)
        function.argtypes, function.restype = args, result
    _kernel.LocalFree.argtypes, _kernel.LocalFree.restype = [_P], _P


def _error(message):
    return exact._winerror(message)


def _close_pins(pins, original=None):
    """Close all owners even after failure; union nested acquisition owners."""
    errors = []
    owners = list(original.owners if isinstance(original, exact.ExactObjectOwnershipError) else ())
    for pinned in reversed(pins):
        try:
            pinned.close()
        except BaseException as error:
            errors.append(error)
            if isinstance(error, exact.ExactObjectOwnershipError):
                owners.extend(error.owners)
        if pinned.handle:
            owners.append(exact.RetainedObjectOwner(exact.RetainedObjectRole.VERIFICATION, pinned))
    if owners:
        cause = original if original is not None else errors[0]
        error = exact.union_retained_ownership('vault cleanup retained live handles', prior=cause, owners=tuple(owners))
        for failure in errors:
            error.add_note(f'close failed: {failure!r}')
        raise error from cause
    if errors:
        if original is not None:
            for error in errors:
                original.add_note(f'close failed after release: {error!r}')
        else:
            raise errors[0]


@contextmanager
def _effective_token():
    exact._require_windows()
    handle = wintypes.HANDLE()
    # Only ERROR_NO_TOKEN permits process-token fallback. Never ignore an
    # inaccessible impersonation token (including identification-only tokens).
    if not _advapi.OpenThreadToken(wintypes.HANDLE(-2), 0xA, True, ctypes.byref(handle)):
        if ctypes.get_last_error() != 1008:
            raise _error('OpenThreadToken failed')
        if not _advapi.OpenProcessToken(wintypes.HANDLE(-1), 0xA, ctypes.byref(handle)):
            raise _error('OpenProcessToken failed')
    token = exact.PinnedObject(Path('<effective-token>'), handle.value, None)
    try:
        yield token
    except BaseException as error:
        _close_pins([token], error)
        raise
    else:
        _close_pins([token])


def _token_information(token, kind):
    size = wintypes.DWORD()
    if _advapi.GetTokenInformation(token.handle, kind, None, 0, ctypes.byref(size)) or ctypes.get_last_error() != 122 or not size.value:
        raise _error('GetTokenInformation sizing failed')
    buffer = ctypes.create_string_buffer(size.value)
    if not _advapi.GetTokenInformation(token.handle, kind, buffer, size.value, ctypes.byref(size)):
        raise _error('GetTokenInformation failed')
    return buffer


def _sid_text(sid):
    if not sid or not _advapi.IsValidSid(sid):
        raise exact.ExactObjectError('invalid security SID')
    value = ctypes.c_void_p()
    if not _advapi.ConvertSidToStringSidW(sid, ctypes.byref(value)):
        raise _error('ConvertSidToStringSidW failed')
    try:
        return ctypes.wstring_at(value)
    finally:
        _kernel.LocalFree(value)


def _principal(token):
    user = _token_information(token, 1)
    label = _token_information(token, 25)
    policy = _token_information(token, 27)
    sid = _sid_text(ctypes.cast(user, ctypes.POINTER(_SID_ATTRIBUTES)).contents.sid)
    integrity_sid = _sid_text(ctypes.cast(label, ctypes.POINTER(_SID_ATTRIBUTES)).contents.sid)
    if not integrity_sid.startswith('S-1-16-') or integrity_sid.count('-') != 3:
        raise exact.ExactObjectError('unexpected token integrity SID')
    return VaultPrincipal(sid, int(integrity_sid.rsplit('-', 1)[1]), ctypes.cast(policy, ctypes.POINTER(wintypes.DWORD)).contents.value)


def current_vault_principal() -> VaultPrincipal:
    with _effective_token() as token:
        return _principal(token)


def _require_principal(principal, creator=None):
    if principal.integrity_rid != _MEDIUM or not principal.mandatory_policy & 1:
        raise exact.ExactObjectError('vault requires exact Medium token with NO_WRITE_UP')
    if creator is not None and principal.sid != creator:
        raise exact.ExactObjectError('unexpected vault creator SID')


@contextmanager
def _descriptor(pinned):
    owner, group, dacl, sacl, descriptor = (ctypes.c_void_p() for _ in range(5))
    result = _advapi.GetSecurityInfo(pinned.handle, 1, 0x17, ctypes.byref(owner), ctypes.byref(group), ctypes.byref(dacl), ctypes.byref(sacl), ctypes.byref(descriptor))
    if result:
        raise exact._winerror('GetSecurityInfo failed', result)
    try:
        yield descriptor, owner, dacl, sacl
    finally:
        _kernel.LocalFree(descriptor)


def _aces(acl_pointer):
    if not acl_pointer:
        return ()
    address = acl_pointer.value
    acl = _ACL.from_address(address)
    offset = ctypes.sizeof(_ACL)
    entries = []
    for _ in range(acl.count):
        if offset + 8 > acl.size:
            raise exact.ExactObjectError('truncated ACL')
        kind, flags, size = ctypes.c_ubyte.from_address(address + offset).value, ctypes.c_ubyte.from_address(address + offset + 1).value, ctypes.c_ushort.from_address(address + offset + 2).value
        if size < 16 or offset + size > acl.size:
            raise exact.ExactObjectError('truncated ACE')
        if kind not in (0, 1, 17):
            raise exact.ExactObjectError('unknown security ACE policy')
        sid = address + offset + 8
        # Bound the SID before passing it to native SID parsing.
        count = ctypes.c_ubyte.from_address(sid + 1).value
        if 8 + 8 + count * 4 > size:
            raise exact.ExactObjectError('truncated ACE SID')
        mask = wintypes.DWORD.from_address(address + offset + 4).value
        entries.append((kind, flags, mask, _sid_text(sid)))
        offset += size
    return tuple(entries)


def _security_policy(pinned):
    with _descriptor(pinned) as (descriptor, owner, dacl, sacl):
        control, revision = wintypes.WORD(), wintypes.DWORD()
        if not _advapi.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision)):
            raise _error('GetSecurityDescriptorControl failed')
        return _Policy(_sid_text(owner), bool(control.value & 0x1000), _aces(dacl) if dacl else None, _aces(sacl))


def _check_access(pinned, creator):
    with _effective_token() as token:
        _require_principal(_principal(token), creator)
        duplicate = wintypes.HANDLE()
        if not _advapi.DuplicateToken(token.handle, 2, ctypes.byref(duplicate)):
            raise _error('DuplicateToken failed')
        owner = exact.PinnedObject(Path('<access-check-token>'), duplicate.value, None)
        try:
            with _descriptor(pinned) as (descriptor, *_):
                mapping = _GENERIC_MAPPING(0x120089, 0x120116, 0x1200A0, _FULL)
                privilege = ctypes.create_string_buffer(1024)
                size, granted, allowed = wintypes.DWORD(1024), wintypes.DWORD(), wintypes.BOOL()
                # Concrete read/write/delete access. AccessCheck evaluates deny-
                # only/restricting SIDs; an allow ACE alone is insufficient.
                required = 0x3019F
                if not _advapi.AccessCheck(descriptor, owner.handle, required, ctypes.byref(mapping), privilege, ctypes.byref(size), ctypes.byref(granted), ctypes.byref(allowed)):
                    raise _error('AccessCheck failed')
                if not allowed.value or granted.value & required != required:
                    raise exact.ExactObjectError('effective token lacks vault access')
        except BaseException as error:
            _close_pins([owner], error)
            raise
        else:
            _close_pins([owner])


def _validate_policy(pinned, creator=None, *, root=False):
    policy = _security_policy(pinned)
    labels = policy.labels
    if creator is None:
        # INHERIT_ONLY labels (e.g. the stock C:\\ root) do not label
        # this directory. Its effective policy remains implicit Medium.
        labels = tuple(ace for ace in labels if not ace[1] & 8)
    if not labels:
        if creator is not None:
            raise exact.ExactObjectError('vault requires explicit Medium label')
        return  # Unlabeled Windows objects have effective Medium NO_WRITE_UP.
    if len(labels) != 1:
        raise exact.ExactObjectError('ambiguous mandatory label')
    kind, flags, mask, sid = labels[0]
    if kind != 17 or not sid.startswith('S-1-16-') or sid.count('-') != 3 or not mask & 1 or flags & 8:
        raise exact.ExactObjectError('unsafe mandatory label policy')
    rid = int(sid.rsplit('-', 1)[1])
    known_trusted_rids = {_MEDIUM, 0x2100, 0x3000, 0x4000, 0x5000}
    if rid not in known_trusted_rids or (creator is not None and rid != _MEDIUM):
        raise exact.ExactObjectError('unsafe mandatory integrity RID')
    if creator is None:
        return
    if pinned.identity.attributes & 0x10 and (flags & 3 != 3 or flags & 4):
        raise exact.ExactObjectError('directory label must inherit to every descendant')
    if policy.owner != creator or (root and not policy.protected) or not policy.dacl:
        raise exact.ExactObjectError('invalid vault owner or protected DACL')
    allowed = set()
    for kind, flags, mask, sid in policy.dacl:
        if kind != 0 or sid not in (creator, _SYSTEM) or mask != _FULL or flags & (4 | 8):
            raise exact.ExactObjectError('unexpected vault ACE policy or writer')
        if pinned.identity.attributes & 0x10 and flags & 3 != 3:
            raise exact.ExactObjectError('vault DACL must inherit')
        allowed.add(sid)
    if allowed != {creator, _SYSTEM}:
        raise exact.ExactObjectError('missing required vault principal')
    _check_access(pinned, creator)


def _pin(path):
    # Metadata-only opens do not participate in Windows share-access checks.
    # LIST_DIRECTORY / READ_DATA makes the no-SHARE_DELETE guard effective.
    # No DELETE desired access permits independent controller/watcher guards.
    handle = exact._kernel32.CreateFileW(str(path), _READ_CONTROL | 0x80 | 0x1, 3, None, 3, 0x02200000, None)
    if handle == exact._INVALID_HANDLE_VALUE:
        raise _error(f'vault direct open failed for {path}')
    pinned = exact.PinnedObject(path, handle, None)
    try:
        pinned.identity = exact._handle_identity(handle, path)
        if pinned.identity.attributes & 0x400:
            raise exact.ExactObjectError('vault rejects reparse objects')
        return pinned
    except BaseException as error:
        _close_pins([pinned], error)
        raise


class EvidenceVault:
    """Owns root/ancestor guards until close; failed closes remain retryable."""
    path: Path
    creator_sid: str
    identity: exact.PinnedIdentity

    def __init__(self, path, creator_sid, pins):
        self.path, self.creator_sid = path, creator_sid
        self._pins = pins
        self._root = pins[-1]
        self.identity = self._root.identity

    def verify(self) -> None:
        _require_principal(current_vault_principal(), self.creator_sid)
        for pinned in self._pins:
            if not pinned.handle or exact._handle_identity(pinned.handle, pinned.path) != pinned.identity or exact.identity_at_path(pinned.path) != pinned.identity:
                raise exact.ExactObjectError('vault retained identity changed or closed')
            if pinned.identity.volume_serial != self.identity.volume_serial:
                raise exact.ExactObjectError('vault requires one volume')
            _validate_policy(pinned, self.creator_sid if pinned is self._root else None, root=pinned is self._root)

    def verify_descendant(self, path: Path) -> None:
        self.verify()
        target = exact._absolute(path)
        try:
            relative = target.relative_to(self.path)
        except ValueError as error:
            raise exact.ExactObjectError('path is outside vault') from error
        if not relative.parts:
            return
        pins = []
        try:
            current = self.path
            for index, part in enumerate(relative.parts):
                current /= part
                pinned = _pin(current)
                pins.append(pinned)
                if pinned.identity.volume_serial != self.identity.volume_serial:
                    raise exact.ExactObjectError('vault descendant changed volume')
                if index < len(relative.parts) - 1 and not pinned.identity.attributes & 0x10:
                    raise exact.ExactObjectError('vault ancestor is not a directory')
                _validate_policy(pinned, self.creator_sid)
                if exact.identity_at_path(current) != pinned.identity:
                    raise exact.ExactObjectError('vault descendant identity changed')
        except BaseException as error:
            _close_pins(pins, error)
            raise
        else:
            _close_pins(pins)

    def close(self) -> None:
        _close_pins(self._pins)

    def __enter__(self):
        return self

    def __exit__(self, kind, error, traceback):
        _close_pins(self._pins, error)


def _acquire(path, *, create, expected_creator_sid=None):
    principal = current_vault_principal()
    _require_principal(principal, expected_creator_sid)
    target = exact._absolute(path)
    if target == Path(target.anchor):
        raise exact.ExactObjectError('volume root cannot be a vault')
    pins = []
    try:
        for ancestor in reversed(target.parents):
            pinned = _pin(ancestor)
            pins.append(pinned)
            if not pinned.identity.attributes & 0x10:
                raise exact.ExactObjectError('vault ancestor is not a directory')
            if pinned.identity.volume_serial != pins[0].identity.volume_serial:
                raise exact.ExactObjectError('vault requires one volume')
            _validate_policy(pinned)
        if create:
            descriptor = ctypes.c_void_p()
            sddl = f'O:{principal.sid}G:{principal.sid}D:P(A;OICI;FA;;;{principal.sid})(A;OICI;FA;;;SY)S:(ML;OICI;NW;;;ME)'
            if not _advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
                raise _error('vault initial security descriptor failed')
            try:
                root = exact.create_pinned_directory_child(target, pins[-1], initial_security_descriptor=descriptor.value, read_control=True, delete_access=False)
            finally:
                _kernel.LocalFree(descriptor)
        else:
            root = _pin(target)
        pins.append(root)
        if not root.identity.attributes & 0x10:
            raise exact.ExactObjectError('vault root must be a directory')
        vault = EvidenceVault(target, principal.sid, pins)
        vault.verify()
        return vault
    except BaseException as error:
        # Security failure never deletes by path, including existing collisions.
        # A created, secure but unverified directory remains for explicit cleanup.
        _close_pins(pins, error)
        raise


def create_vault(path: Path) -> EvidenceVault:
    """Create a fresh direct vault under an existing trusted parent."""
    return _acquire(path, create=True)


def open_vault(path: Path, *, expected_creator_sid: str | None = None) -> EvidenceVault:
    """Verify a vault for the actual effective creator token, without repair."""
    return _acquire(path, create=False, expected_creator_sid=expected_creator_sid)
