"""Safe Windows bsdtar and package-inventory boundary for portable MO2."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from subprocess import CompletedProcess
from typing import Protocol

from .bootstrap_model import ExtractorIdentity, FileIdentity
from .release import Mo2ReleaseDescriptor, ReleaseFileIdentity
from .path_budget import PathBudgetError, PlannedPath, admit_paths, archive_paths


class Mo2ArchiveError(RuntimeError):
    """Raised when archive or extracted package evidence is unsafe."""


class CommandRunner(Protocol):
    def run(self, args: tuple[str, ...]) -> CompletedProcess[bytes]: ...


class SubprocessCommandRunner:
    def run(self, args: tuple[str, ...]) -> CompletedProcess[bytes]:
        return subprocess.run(
            list(args),
            check=False,
            capture_output=True,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


@dataclass(frozen=True)
class ArchiveEntry:
    relative_path: str
    kind: str


@dataclass(frozen=True)
class ArchiveListing:
    entries: tuple[ArchiveEntry, ...]
    canonical_sha256: str

    @property
    def entry_count(self) -> int:
        return len(self.entries)


@dataclass(frozen=True)
class BsdtarArchive:
    archive_path: str
    extractor_path: str
    listing: ArchiveListing


@dataclass(frozen=True)
class PackageFile:
    relative_path: str
    sha256: str
    size: int


@dataclass(frozen=True)
class PackageInventory:
    files: tuple[PackageFile, ...]
    total_size: int
    sha256: str

    @property
    def file_count(self) -> int:
        return len(self.files)


@dataclass(frozen=True)
class PackageComparison:
    compatible: bool
    missing: tuple[str, ...]
    modified: tuple[str, ...]
    allowed_extras: tuple[str, ...]
    unapproved_extras: tuple[str, ...]


_SYSTEM_TAR = Path(r"C:\Windows\System32\tar.exe")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_HASH_CHUNK = 1024 * 1024
_RESERVED_WINDOWS_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


def free_bytes_at(path: Path) -> int:
    return shutil.disk_usage(Path(path)).free


def observe_bsdtar(
    extractor_path: Path = _SYSTEM_TAR,
    *,
    runner: CommandRunner | None = None,
) -> ExtractorIdentity:
    source = Path(extractor_path)
    expected = _resolve_existing_regular(_SYSTEM_TAR, "system bsdtar")
    actual = _resolve_existing_regular(source, "extractor")
    if os.path.normcase(str(actual)) != os.path.normcase(str(expected)):
        raise Mo2ArchiveError("extractor must resolve to C:\\Windows\\System32\\tar.exe")
    sha256, size = _hash_stable_file(actual)
    command_runner = runner or SubprocessCommandRunner()
    result = command_runner.run((str(actual), "--version"))
    if result.returncode != 0:
        raise Mo2ArchiveError(
            f"bsdtar version command failed with exit {result.returncode}: "
            f"{_stderr_text(result.stderr)}"
        )
    text = _decode_utf8(result.stdout, "bsdtar version output")
    version = next((line.strip() for line in text.splitlines() if line.strip()), None)
    if version is None or not version.casefold().startswith("bsdtar "):
        raise Mo2ArchiveError("extractor did not report a supported bsdtar version")
    return ExtractorIdentity(
        executable=FileIdentity(path=str(actual), sha256=sha256, size=size),
        version=version,
    )


def preflight_archive(
    archive_path: Path,
    release: Mo2ReleaseDescriptor,
    extractor_path: Path,
    *,
    runner: CommandRunner | None = None,
) -> ArchiveListing:
    command_runner = runner or SubprocessCommandRunner()
    archive_text = str(Path(archive_path))
    extractor_text = str(Path(extractor_path))
    names_result = command_runner.run((extractor_text, "-tf", archive_text))
    _require_success(names_result, "archive name listing")
    types_result = command_runner.run((extractor_text, "-tvf", archive_text))
    _require_success(types_result, "archive type listing")

    name_text = _decode_utf8(names_result.stdout, "archive name listing")
    verbose_text = _decode_utf8(types_result.stdout, "archive type listing")
    names = name_text.splitlines()
    verbose_rows = verbose_text.splitlines()
    if not names or any(not name for name in names):
        raise Mo2ArchiveError("archive name listing contains an empty entry")
    if len(names) != len(verbose_rows):
        raise Mo2ArchiveError("archive name and type listings have different counts")

    entries: list[ArchiveEntry] = []
    seen: set[str] = set()
    for index, (name, verbose) in enumerate(zip(names, verbose_rows, strict=True)):
        if not verbose or verbose[0] not in {"-", "d"}:
            raise Mo2ArchiveError(
                f"archive entry {index} must contain only regular files and directories"
            )
        kind = "file" if verbose[0] == "-" else "directory"
        normalized = _safe_archive_entry(name, kind, index)
        identity = normalized.casefold()
        if identity in seen:
            raise Mo2ArchiveError("archive listing contains a case-insensitive duplicate")
        seen.add(identity)
        entries.append(ArchiveEntry(relative_path=normalized, kind=kind))

    canonical = ("\n".join(names) + "\n").encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    if len(entries) != release.archive_entry_count:
        raise Mo2ArchiveError(
            f"archive entry count mismatch: expected {release.archive_entry_count}, "
            f"observed {len(entries)}"
        )
    if digest != release.archive_listing_sha256:
        raise Mo2ArchiveError("archive canonical listing SHA-256 does not match release")
    return ArchiveListing(entries=tuple(entries), canonical_sha256=digest)


def extract_and_inventory(
    archive_path: Path,
    release: Mo2ReleaseDescriptor,
    extractor_path: Path,
    staging_root: Path,
    *,
    runner: CommandRunner | None = None,
    free_space_reader: Callable[[Path], int] = free_bytes_at,
) -> PackageInventory:
    stage = Path(staging_root)
    parent = stage.parent
    parent_resolved = _resolve_existing_directory(parent, "staging parent")
    if stage.absolute().parent.resolve(strict=True) != parent_resolved:
        raise Mo2ArchiveError("staging root must be a direct child of its resolved parent")
    try:
        stage.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise Mo2ArchiveError(f"cannot inspect staging root: {error}") from error
    else:
        raise Mo2ArchiveError("staging root must not already exist")

    try:
        free_bytes = free_space_reader(parent_resolved)
    except OSError as error:
        raise Mo2ArchiveError(f"cannot inspect staging free space: {error}") from error
    if type(free_bytes) is not int or free_bytes < release.minimum_free_bytes:
        raise Mo2ArchiveError(
            f"insufficient free space: require {release.minimum_free_bytes} bytes"
        )

    command_runner = runner or SubprocessCommandRunner()
    listing = preflight_archive(archive_path, release, extractor_path, runner=command_runner)
    try:
        admit_paths((*archive_paths(listing, stage), PlannedPath("archive-input", "preparation-wide", str(Path(archive_path).absolute()))))
    except PathBudgetError as error:
        raise Mo2ArchiveError(str(error)) from error
    try:
        stage.mkdir()
    except OSError as error:
        raise Mo2ArchiveError(f"cannot create fresh staging root: {error}") from error
    args = (
        str(Path(extractor_path)),
        "-xf",
        str(Path(archive_path)),
        "-C",
        str(stage),
    )
    result = command_runner.run(args)
    _require_success(result, "archive extraction")
    inventory = inventory_tree(stage)
    _validate_package_inventory(inventory, release)
    return inventory


def inventory_tree(root: Path) -> PackageInventory:
    source = _resolve_existing_directory(Path(root), "inventory root")
    files: list[PackageFile] = []

    def walk(directory: Path, relative_parts: tuple[str, ...]) -> None:
        try:
            entries = list(os.scandir(directory))
        except OSError as error:
            raise Mo2ArchiveError(f"cannot enumerate package directory {directory}: {error}") from error
        for entry in entries:
            path = Path(entry.path)
            relative = "/".join((*relative_parts, entry.name))
            _safe_inventory_relative_path(relative)
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as error:
                raise Mo2ArchiveError(f"cannot inspect package entry {relative}: {error}") from error
            if entry.is_symlink() or _is_reparse(metadata):
                raise Mo2ArchiveError(f"package entry {relative} is redirected")
            _require_contained(source, path, relative)
            if stat.S_ISDIR(metadata.st_mode):
                walk(path, (*relative_parts, entry.name))
            elif stat.S_ISREG(metadata.st_mode):
                sha256, size = _hash_stable_file(path)
                files.append(PackageFile(relative, sha256, size))
            else:
                raise Mo2ArchiveError(f"package entry {relative} is not a regular file or directory")

    walk(source, ())
    files.sort(key=lambda item: (item.relative_path.casefold(), item.relative_path))
    folded = [item.relative_path.casefold() for item in files]
    if len(folded) != len(set(folded)):
        raise Mo2ArchiveError("package inventory contains a case-insensitive duplicate")
    canonical = json.dumps(
        [[item.relative_path, item.sha256, item.size] for item in files],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return PackageInventory(
        files=tuple(files),
        total_size=sum(item.size for item in files),
        sha256=hashlib.sha256(canonical).hexdigest(),
    )


def compare_package_to_existing(
    package: PackageInventory,
    existing: PackageInventory,
    release: Mo2ReleaseDescriptor,
) -> PackageComparison:
    package_map = _inventory_map(package, "package")
    existing_map = _inventory_map(existing, "existing manager")
    missing: list[str] = []
    modified: list[str] = []
    for identity, packaged in package_map.items():
        observed = existing_map.get(identity)
        if observed is None:
            missing.append(packaged.relative_path)
        elif observed.sha256 != packaged.sha256 or observed.size != packaged.size:
            modified.append(packaged.relative_path)

    allowed: list[str] = []
    unapproved: list[str] = []
    for identity, observed in existing_map.items():
        if identity in package_map:
            continue
        target = allowed if _allowed_extra(PurePosixPath(observed.relative_path), release) else unapproved
        target.append(observed.relative_path)

    sort_key = lambda item: (item.casefold(), item)
    missing.sort(key=sort_key)
    modified.sort(key=sort_key)
    allowed.sort(key=sort_key)
    unapproved.sort(key=sort_key)
    return PackageComparison(
        compatible=not missing and not modified and not unapproved,
        missing=tuple(missing),
        modified=tuple(modified),
        allowed_extras=tuple(allowed),
        unapproved_extras=tuple(unapproved),
    )


def _allowed_extra(path: PurePosixPath, release: Mo2ReleaseDescriptor) -> bool:
    text = path.as_posix()
    if text.casefold() in {item.casefold() for item in release.allowed_extra_files}:
        return True
    if release.allow_root_logs and len(path.parts) == 2:
        return path.parts[0].casefold() == "logs" and path.suffix.casefold() == ".log"
    if release.allow_plugin_python_bytecode:
        return (
            len(path.parts) >= 3
            and path.parts[0].casefold() == "plugins"
            and "__pycache__" in {part.casefold() for part in path.parts[1:-1]}
            and path.suffix.casefold() == ".pyc"
        )
    return False


def _validate_package_inventory(
    inventory: PackageInventory, release: Mo2ReleaseDescriptor
) -> None:
    if inventory.file_count != release.package_file_count:
        raise Mo2ArchiveError(
            f"package file count mismatch: expected {release.package_file_count}, "
            f"observed {inventory.file_count}"
        )
    if inventory.total_size != release.extracted_size:
        raise Mo2ArchiveError(
            f"package byte size mismatch: expected {release.extracted_size}, "
            f"observed {inventory.total_size}"
        )
    identities = _inventory_map(inventory, "package")
    _require_release_file(identities, release.executable, "executable")
    for sentinel in release.sentinels:
        _require_release_file(identities, sentinel, f"sentinel {sentinel.relative_path}")


def _require_release_file(
    identities: dict[str, PackageFile], expected: ReleaseFileIdentity, label: str
) -> None:
    observed = identities.get(expected.relative_path.casefold())
    if observed is None:
        raise Mo2ArchiveError(f"package is missing required {label}")
    if observed.sha256 != expected.sha256 or observed.size != expected.size:
        raise Mo2ArchiveError(f"package {label} identity does not match release")


def _inventory_map(inventory: PackageInventory, label: str) -> dict[str, PackageFile]:
    result: dict[str, PackageFile] = {}
    prior_key: tuple[str, str] | None = None
    total = 0
    for item in inventory.files:
        _safe_inventory_relative_path(item.relative_path)
        if type(item.size) is not int or item.size < 0:
            raise Mo2ArchiveError(f"{label} inventory contains an invalid size")
        if len(item.sha256) != 64 or any(
            character not in "0123456789abcdef" for character in item.sha256
        ):
            raise Mo2ArchiveError(f"{label} inventory contains an invalid SHA-256")
        identity = item.relative_path.casefold()
        if identity in result:
            raise Mo2ArchiveError(f"{label} inventory contains a duplicate path")
        key = (identity, item.relative_path)
        if prior_key is not None and key < prior_key:
            raise Mo2ArchiveError(f"{label} inventory is not canonically sorted")
        prior_key = key
        result[identity] = item
        total += item.size
    if total != inventory.total_size:
        raise Mo2ArchiveError(f"{label} inventory total size is inconsistent")
    canonical = json.dumps(
        [[item.relative_path, item.sha256, item.size] for item in inventory.files],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if hashlib.sha256(canonical).hexdigest() != inventory.sha256:
        raise Mo2ArchiveError(f"{label} inventory identity is inconsistent")
    return result


def _safe_archive_entry(raw_name: str, kind: str, index: int) -> str:
    if any(ord(character) < 32 for character in raw_name):
        raise Mo2ArchiveError(f"archive entry {index} must be a safe relative path")
    if kind == "directory":
        if not raw_name.endswith("/"):
            raise Mo2ArchiveError(f"archive directory entry {index} must end with slash")
        name = raw_name[:-1]
    else:
        if raw_name.endswith("/"):
            raise Mo2ArchiveError(f"archive file entry {index} must not end with slash")
        name = raw_name
    _safe_inventory_relative_path(name)
    return PurePosixPath(name).as_posix()


def _safe_inventory_relative_path(text: str) -> None:
    windows = PureWindowsPath(text)
    if (
        not text
        or "\\" in text
        or text.startswith("/")
        or windows.is_absolute()
        or bool(windows.drive)
        or ":" in text
    ):
        raise Mo2ArchiveError("package path must be a safe relative path")
    parts = text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise Mo2ArchiveError("package path must be a safe relative path")
    for part in parts:
        if any(ord(character) < 32 for character in part) or part.endswith((" ", ".")):
            raise Mo2ArchiveError("package path must be a safe relative path")
        if part.split(".", 1)[0].casefold() in _RESERVED_WINDOWS_NAMES:
            raise Mo2ArchiveError("package path must be a safe relative path")
    path = PurePosixPath(text)
    if any(part.casefold() == "saves" for part in path.parts) or path.suffix.casefold() in {
        ".ess",
        ".skse",
    }:
        raise Mo2ArchiveError("package path must not reference saves or co-saves")


def _resolve_existing_regular(path: Path, label: str) -> Path:
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise Mo2ArchiveError(f"{label} must be an existing regular file: {error}") from error
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink() or _is_reparse(metadata):
        raise Mo2ArchiveError(f"{label} must be a direct regular file")
    return resolved


def _resolve_existing_directory(path: Path, label: str) -> Path:
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise Mo2ArchiveError(f"{label} must be an existing directory: {error}") from error
    if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink() or _is_reparse(metadata):
        raise Mo2ArchiveError(f"{label} must be a direct directory")
    return resolved


def _hash_stable_file(path: Path) -> tuple[str, int]:
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or path.is_symlink() or _is_reparse(before):
            raise Mo2ArchiveError(f"{path} must remain a direct regular file")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(_HASH_CHUNK):
                digest.update(chunk)
        after = path.lstat()
    except Mo2ArchiveError:
        raise
    except OSError as error:
        raise Mo2ArchiveError(f"cannot hash package file {path}: {error}") from error
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or not stat.S_ISREG(after.st_mode)
        or _is_reparse(after)
    ):
        raise Mo2ArchiveError(f"package file {path} changed while hashing")
    return digest.hexdigest(), before.st_size


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(
        getattr(metadata, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _require_contained(root: Path, path: Path, relative: str) -> None:
    try:
        resolved = path.resolve(strict=True)
        common = os.path.commonpath((str(root), str(resolved)))
    except (OSError, ValueError) as error:
        raise Mo2ArchiveError(f"package entry {relative} cannot be resolved safely: {error}") from error
    if os.path.normcase(common) != os.path.normcase(str(root)):
        raise Mo2ArchiveError(f"package entry {relative} escapes the inventory root")


def _require_success(result: CompletedProcess[bytes], label: str) -> None:
    if result.returncode != 0:
        raise Mo2ArchiveError(
            f"{label} failed with exit {result.returncode}: {_stderr_text(result.stderr)}"
        )


def _decode_utf8(value: bytes, label: str) -> str:
    if type(value) is not bytes:
        raise Mo2ArchiveError(f"{label} must be bytes")
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as error:
        raise Mo2ArchiveError(f"{label} is not valid UTF-8: {error}") from error


def _stderr_text(value: bytes) -> str:
    if not isinstance(value, bytes):
        return "unavailable error output"
    return value.decode("utf-8", errors="replace").strip()[:500]
