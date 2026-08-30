"""Stable, read-only capture of the filesystem evidence used by MO2 inspection."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat


@dataclass(frozen=True)
class Mo2DirectoryEntry:
    name: str
    kind: str
    redirected: bool


@dataclass(frozen=True)
class Mo2ReadSetVerification:
    stable: bool
    sha256: str
    changed_paths: tuple[str, ...]


_UNREADABLE = object()


class Mo2ReadSet:
    """Capture each requested path once, then verify the full set a second time."""

    def __init__(self) -> None:
        self._files: dict[Path, bytes | None] = {}
        self._directories: dict[Path, tuple[Mo2DirectoryEntry, ...]] = {}

    def read_bytes(self, path: Path) -> bytes:
        observed = Path(path)
        if observed in self._files:
            value = self._files[observed]
            if value is None:
                raise FileNotFoundError(observed)
            return value
        value = self._capture_required_file(observed)
        self._files[observed] = value
        return value

    def optional_bytes(self, path: Path) -> bytes | None:
        observed = Path(path)
        if observed not in self._files:
            self._files[observed] = self._capture_optional_file(observed)
        return self._files[observed]

    def list_directory(self, path: Path) -> tuple[Mo2DirectoryEntry, ...]:
        observed = Path(path)
        if observed not in self._directories:
            self._directories[observed] = self._capture_directory(observed)
        return self._directories[observed]

    def verify(self) -> Mo2ReadSetVerification:
        changed: list[str] = []
        for path, first in self._files.items():
            try:
                second = self._capture_optional_file(path)
            except OSError:
                second = _UNREADABLE
            if second != first:
                changed.append(str(path))
        for path, first in self._directories.items():
            try:
                second = self._capture_directory(path)
            except OSError:
                second = _UNREADABLE
            if second != first:
                changed.append(str(path))
        identity = self._canonical_identity()
        return Mo2ReadSetVerification(
            stable=not changed,
            sha256=hashlib.sha256(identity).hexdigest(),
            changed_paths=tuple(sorted(set(changed), key=str.casefold)),
        )

    @staticmethod
    def _capture_required_file(path: Path) -> bytes:
        value = Mo2ReadSet._capture_optional_file(path)
        if value is None:
            raise FileNotFoundError(path)
        return value

    @staticmethod
    def _capture_optional_file(path: Path) -> bytes | None:
        try:
            status = path.lstat()
        except FileNotFoundError:
            return None
        if _is_redirected(path) or not stat.S_ISREG(status.st_mode):
            raise OSError(f"observed file is not a regular, direct file: {path}")
        return path.read_bytes()

    @staticmethod
    def _capture_directory(path: Path) -> tuple[Mo2DirectoryEntry, ...]:
        status = path.lstat()
        if _is_redirected(path) or not stat.S_ISDIR(status.st_mode):
            raise OSError(f"observed directory is not a direct directory: {path}")

        entries: list[Mo2DirectoryEntry] = []
        with os.scandir(path) as children:
            for child in children:
                kind = "other"
                if child.is_file(follow_symlinks=False):
                    kind = "file"
                elif child.is_dir(follow_symlinks=False):
                    kind = "directory"
                entries.append(
                    Mo2DirectoryEntry(
                        name=child.name,
                        kind=kind,
                        redirected=_is_redirected(Path(child.path)),
                    )
                )

        normalized = [item.name.casefold() for item in entries]
        if len(normalized) != len(set(normalized)):
            raise OSError(f"directory contains duplicate case-insensitive names: {path}")
        return tuple(sorted(entries, key=lambda item: item.name.casefold()))

    def _canonical_identity(self) -> bytes:
        files = []
        for path, data in sorted(
            self._files.items(), key=lambda item: str(item[0]).casefold()
        ):
            files.append(
                {
                    "path": str(path),
                    "present": data is not None,
                    "sha256": None if data is None else hashlib.sha256(data).hexdigest(),
                    "size": None if data is None else len(data),
                }
            )
        directories = [
            {
                "path": str(path),
                "entries": [
                    [entry.name, entry.kind, entry.redirected] for entry in entries
                ],
            }
            for path, entries in sorted(
                self._directories.items(), key=lambda item: str(item[0]).casefold()
            )
        ]
        return json.dumps(
            {"files": files, "directories": directories},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")


def _is_redirected(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return path.is_symlink() or bool(
        attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )
