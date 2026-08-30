"""Immutable values describing retained mod archives."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath


class ArtifactHealth(StrEnum):
    AVAILABLE = "Available"
    MISSING = "Missing"
    MODIFIED = "Modified"


@dataclass(frozen=True)
class ArchiveArtifact:
    schema_version: int
    artifact_id: str
    sha256: str
    size: int
    original_name: str
    stored_relative_path: str
    imported_at: str
    source_note: str
    source_url: str | None

    def stored_path(self, workspace_root: Path) -> Path:
        return workspace_root.joinpath(*PurePosixPath(self.stored_relative_path).parts)
