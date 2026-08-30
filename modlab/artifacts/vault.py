"""Crash-safe, content-addressed retention for user-owned mod archives."""

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from modlab.workspace import initialize_workspace

from .model import ArchiveArtifact, ArtifactFinding, ArtifactHealth
from .serialization import ArtifactFormatError, artifact_from_dict, artifact_to_dict


class ArchiveImportError(RuntimeError):
    """Raised when an archive cannot be retained without risking user data."""


class ArtifactNotFoundError(LookupError):
    """Raised when a valid artifact identity is not present in this vault."""


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

    def list(self) -> tuple[ArchiveArtifact, ...]:
        records = tuple(
            self._load_metadata(path) for path in self.metadata_path.glob("*.json")
        )
        return tuple(sorted(records, key=lambda record: (record.imported_at, record.artifact_id)))

    def get(self, artifact_id: str) -> ArchiveArtifact:
        prefix = "archive-sha256:"
        if not isinstance(artifact_id, str) or not artifact_id.startswith(prefix):
            raise ArtifactFormatError("artifact ID must use archive-sha256:<hash>")
        sha256 = artifact_id.removeprefix(prefix)
        if len(sha256) != 64 or any(
            character not in "0123456789abcdef" for character in sha256
        ):
            raise ArtifactFormatError(
                "artifact ID hash must be 64 lowercase hexadecimal characters"
            )

        metadata_path = self.metadata_path / f"{sha256}.json"
        if not metadata_path.is_file():
            raise ArtifactNotFoundError(f"Unknown artifact ID: {artifact_id}")
        record = self._load_metadata(metadata_path)
        if record.artifact_id != artifact_id:
            raise ArtifactFormatError("artifact ID does not match its metadata filename")
        return record

    def verify(self, artifact_id: str) -> ArtifactFinding:
        record = self.get(artifact_id)
        stored_path = record.stored_path(self.workspace_root)
        if not stored_path.is_file():
            return ArtifactFinding(
                health=ArtifactHealth.MISSING,
                artifact_id=record.artifact_id,
                expected_sha256=record.sha256,
                actual_sha256=None,
                path=stored_path,
                message="Retained archive payload is missing.",
            )

        actual_sha256, actual_size = _hash_file(stored_path)
        if actual_sha256 == record.sha256 and actual_size == record.size:
            return ArtifactFinding(
                health=ArtifactHealth.AVAILABLE,
                artifact_id=record.artifact_id,
                expected_sha256=record.sha256,
                actual_sha256=actual_sha256,
                path=stored_path,
                message="Retained archive bytes match their metadata.",
            )
        return ArtifactFinding(
            health=ArtifactHealth.MODIFIED,
            artifact_id=record.artifact_id,
            expected_sha256=record.sha256,
            actual_sha256=actual_sha256,
            path=stored_path,
            message="Retained archive bytes differ from their recorded identity.",
        )

    def import_archive(
        self,
        source: Path,
        source_note: str,
        source_url: str | None = None,
        imported_at: str | None = None,
    ) -> ArchiveArtifact:
        requested_source = Path(source).expanduser()
        try:
            source_path = requested_source.resolve()
            self._validate_source(source_path)
        except ArchiveImportError:
            raise
        except OSError as error:
            raise ArchiveImportError(
                f"Could not inspect archive source {requested_source}: {error}"
            ) from error

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

    @staticmethod
    def _load_metadata(metadata_path: Path) -> ArchiveArtifact:
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ArtifactFormatError(
                f"Could not read artifact metadata {metadata_path.name}: {error}"
            ) from error
        return artifact_from_dict(data)


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
