"""Explicit, fail-closed source and target inventory for the MO2 Guard bundle."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from modlab.resources.mo2_guard.protocol import BridgeBundleFile


class BridgeBundleError(RuntimeError):
    """Raised when a Guard source or deployed target is not exact."""


@dataclass(frozen=True)
class GuardBundleFile(BridgeBundleFile):
    data: bytes

    @property
    def metadata(self) -> BridgeBundleFile:
        return BridgeBundleFile(self.relative_path, self.sha256, self.size)


@dataclass(frozen=True)
class GuardBundle:
    files: tuple[GuardBundleFile, ...]


_DECLARED_NAMES = ("__init__.py", "plugin.py", "protocol.py")
_REPARSE_POINT = 0x400
_HASH_CHUNK = 1024 * 1024


def declared_guard_bundle() -> GuardBundle:
    """Read only the three explicitly named repository resources, stably."""
    package = resources.files("modlab.resources.mo2_guard")
    files: list[GuardBundleFile] = []
    for name in _DECLARED_NAMES:
        entry = package.joinpath(name)
        try:
            data = entry.read_bytes()
            reread = entry.read_bytes()
        except (FileNotFoundError, OSError) as error:
            raise BridgeBundleError(f"declared bridge source is missing: {name}") from error
        if data != reread:
            raise BridgeBundleError(f"declared bridge source changed while reading: {name}")
        if data.startswith(b"\xef\xbb\xbf"):
            raise BridgeBundleError(f"declared bridge source has a UTF-8 BOM: {name}")
        files.append(GuardBundleFile(name, hashlib.sha256(data).hexdigest(), len(data), data))
    return GuardBundle(tuple(files))


def verify_guard_bundle(root: Path, expected_files: tuple[BridgeBundleFile, ...]) -> tuple[BridgeBundleFile, ...]:
    """Reject every non-file, redirect, bytecode cache, or undeclared target entry."""
    target = _direct_directory(Path(root), "bridge target")
    expected = _validate_expected(expected_files)
    observed: dict[str, BridgeBundleFile] = {}
    try:
        entries = list(os.scandir(target))
    except OSError as error:
        raise BridgeBundleError(f"cannot enumerate bridge target: {error}") from error
    for entry in sorted(entries, key=lambda item: (item.name.casefold(), item.name)):
        if entry.name.casefold() in {"__pycache__"} or entry.name.casefold().endswith(".pyc"):
            raise BridgeBundleError(f"bridge target contains forbidden bytecode entry: {entry.name}")
        if entry.name not in _DECLARED_NAMES:
            raise BridgeBundleError(f"bridge target contains undeclared entry: {entry.name}")
        path = Path(entry.path)
        try:
            metadata = path.lstat()
        except OSError as error:
            raise BridgeBundleError(f"cannot inspect bridge entry {entry.name}: {error}") from error
        if entry.is_symlink() or _is_reparse(metadata) or not stat.S_ISREG(metadata.st_mode):
            raise BridgeBundleError(f"bridge target entry is redirected or not a direct regular file: {entry.name}")
        observed[entry.name] = BridgeBundleFile(entry.name, *_hash_stable_file(path))
    if tuple(observed) != _DECLARED_NAMES:
        raise BridgeBundleError("bridge target does not contain the exact declared files")
    for expected_file in expected:
        actual = observed[expected_file.relative_path]
        if actual != expected_file:
            raise BridgeBundleError(f"bridge target file identity differs: {expected_file.relative_path}")
    return expected_files


def _validate_expected(value: tuple[BridgeBundleFile, ...]) -> tuple[BridgeBundleFile, ...]:
    if not isinstance(value, tuple) or len(value) != len(_DECLARED_NAMES):
        raise BridgeBundleError("expected bridge inventory must contain exactly three files")
    normalized: list[BridgeBundleFile] = []
    for index, item in enumerate(value):
        if not isinstance(item, BridgeBundleFile) or item.relative_path != _DECLARED_NAMES[index]:
            raise BridgeBundleError("expected bridge inventory must use the declared file order")
        if not isinstance(item.sha256, str) or len(item.sha256) != 64 or any(char not in "0123456789abcdef" for char in item.sha256):
            raise BridgeBundleError("expected bridge inventory contains an invalid SHA-256")
        if type(item.size) is not int or item.size < 0:
            raise BridgeBundleError("expected bridge inventory contains an invalid size")
        normalized.append(BridgeBundleFile(item.relative_path, item.sha256, item.size))
    return tuple(normalized)


def _direct_directory(path: Path, label: str) -> Path:
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise BridgeBundleError(f"{label} must be an existing direct directory: {error}") from error
    if path.is_symlink() or _is_reparse(metadata) or not stat.S_ISDIR(metadata.st_mode):
        raise BridgeBundleError(f"{label} must be an existing direct directory")
    return resolved


def _hash_stable_file(path: Path) -> tuple[str, int]:
    try:
        before = path.lstat()
        if path.is_symlink() or _is_reparse(before) or not stat.S_ISREG(before.st_mode):
            raise BridgeBundleError(f"bridge target file is not direct: {path.name}")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(_HASH_CHUNK):
                digest.update(chunk)
        after = path.lstat()
    except BridgeBundleError:
        raise
    except OSError as error:
        raise BridgeBundleError(f"cannot hash bridge target file {path.name}: {error}") from error
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns or _is_reparse(after):
        raise BridgeBundleError(f"bridge target file changed while reading: {path.name}")
    return digest.hexdigest(), before.st_size


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT)


__all__ = ["BridgeBundleError", "GuardBundle", "GuardBundleFile", "declared_guard_bundle", "verify_guard_bundle"]
