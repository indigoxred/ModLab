"""Crash-safe, content-addressed retention for user-owned mod archives."""

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from modlab.workspace import initialize_workspace

from .model import ArchiveArtifact
from .serialization import artifact_from_dict, artifact_to_dict


class ArchiveImportError(RuntimeError):
    """Raised when an archive cannot be retained without risking user data."""


_ARCHIVE_EXTENSIONS = {".zip", ".7z", ".rar"}
_COPY_CHUNK_SIZE = 1024 * 1024


class ArchiveVault:
    def __init__(self, workspace_root: Path):
        layout = initialize_workspace(workspace_root)
        self.workspace_root = layout.root
        self.archives_path = layout.archives
        self.jobs_path = layout.jobs
        self.metadata_path = layout.metadata / "artifacts"
        self.metadata_path.mkdir(parents=True, exist_ok=True)

    def import_archive(
        self,
        source: Path,
        source_note: str,
        source_url: str | None = None,
        imported_at: str | None = None,
    ) -> ArchiveArtifact:
        source_path = Path(source).expanduser().resolve()
        self._validate_source(source_path)

        operation_id = uuid.uuid4().hex
        staging_path = self.jobs_path / f"import-{operation_id}.part"
        metadata_temp_path = self.metadata_path / f"metadata.{operation_id}.part"
        promoted_path: Path | None = None
        metadata_target: Path | None = None

        try:
            sha256, size = _copy_and_hash(source_path, staging_path)
            artifact_id = f"archive-sha256:{sha256}"
            metadata_target = self.metadata_path / f"{sha256}.json"

            if metadata_target.exists():
                record = artifact_from_dict(
                    json.loads(metadata_target.read_text(encoding="utf-8"))
                )
                if record.sha256 != sha256:
                    raise ValueError("existing metadata identity does not match imported bytes")
                stored_path = record.stored_path(self.workspace_root)
                if stored_path.exists():
                    actual_sha256, actual_size = _hash_file(stored_path)
                    if actual_sha256 != sha256 or actual_size != record.size:
                        raise ValueError("existing retained payload has been modified")
                    staging_path.unlink(missing_ok=True)
                else:
                    stored_path.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(staging_path, stored_path)
                    promoted_path = stored_path
                return record

            extension = source_path.suffix.lower()
            stored_relative_path = PurePosixPath(
                "library",
                "archives",
                sha256[:2],
                sha256,
                f"payload{extension}",
            ).as_posix()
            record = artifact_from_dict(
                artifact_to_dict(
                    ArchiveArtifact(
                        schema_version=1,
                        artifact_id=artifact_id,
                        sha256=sha256,
                        size=size,
                        original_name=source_path.name,
                        stored_relative_path=stored_relative_path,
                        imported_at=imported_at or _utc_now(),
                        source_note=source_note,
                        source_url=source_url,
                    )
                )
            )

            stored_path = record.stored_path(self.workspace_root)
            stored_path.parent.mkdir(parents=True, exist_ok=True)
            if stored_path.exists():
                actual_sha256, actual_size = _hash_file(stored_path)
                if actual_sha256 != sha256 or actual_size != size:
                    raise ValueError("retained payload path already contains different bytes")
                staging_path.unlink(missing_ok=True)
            else:
                os.replace(staging_path, stored_path)
                promoted_path = stored_path

            serialized = json.dumps(
                artifact_to_dict(record), indent=2, sort_keys=True, ensure_ascii=False
            )
            metadata_temp_path.write_text(serialized + "\n", encoding="utf-8")
            os.replace(metadata_temp_path, metadata_target)
            return record
        except Exception as error:
            if promoted_path is not None and (
                metadata_target is None or not metadata_target.exists()
            ):
                promoted_path.unlink(missing_ok=True)
            raise ArchiveImportError(f"Could not import archive {source_path}: {error}") from error
        finally:
            staging_path.unlink(missing_ok=True)
            metadata_temp_path.unlink(missing_ok=True)

    @staticmethod
    def _validate_source(source_path: Path) -> None:
        if not source_path.is_file():
            raise ArchiveImportError(f"Archive source is not a file: {source_path}")
        if source_path.suffix.lower() not in _ARCHIVE_EXTENSIONS:
            raise ArchiveImportError(
                f"Unsupported archive extension for {source_path}; expected ZIP, 7z, or RAR"
            )
        if source_path.stat().st_size <= 0:
            raise ArchiveImportError(f"Archive source is empty: {source_path}")


def _copy_and_hash(source: Path, destination: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as input_stream, destination.open("xb") as output_stream:
        while chunk := input_stream.read(_COPY_CHUNK_SIZE):
            output_stream.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(_COPY_CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
