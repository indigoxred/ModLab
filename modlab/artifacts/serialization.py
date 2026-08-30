"""Strict, portable JSON representation for archive artifacts."""

from pathlib import PurePosixPath, PureWindowsPath
from typing import Mapping

from .model import ArchiveArtifact


class ArtifactFormatError(ValueError):
    """Raised when artifact metadata cannot be trusted."""


_FIELDS = {
    "schemaVersion",
    "artifactId",
    "sha256",
    "size",
    "originalName",
    "storedRelativePath",
    "importedAt",
    "sourceNote",
    "sourceUrl",
}
_ARCHIVE_EXTENSIONS = {"zip", "7z", "rar"}


def artifact_to_dict(record: ArchiveArtifact) -> dict[str, object]:
    return {
        "schemaVersion": record.schema_version,
        "artifactId": record.artifact_id,
        "sha256": record.sha256,
        "size": record.size,
        "originalName": record.original_name,
        "storedRelativePath": record.stored_relative_path,
        "importedAt": record.imported_at,
        "sourceNote": record.source_note,
        "sourceUrl": record.source_url,
    }


def artifact_from_dict(data: object) -> ArchiveArtifact:
    if not isinstance(data, Mapping):
        raise ArtifactFormatError("artifact metadata must be an object")

    fields = set(data)
    if fields != _FIELDS:
        missing = sorted(_FIELDS - fields)
        extra = sorted(fields - _FIELDS)
        raise ArtifactFormatError(f"artifact fields differ: missing={missing}, extra={extra}")

    schema_version = data["schemaVersion"]
    if type(schema_version) is not int or schema_version != 1:
        raise ArtifactFormatError("schemaVersion must be integer 1")

    sha256 = _required_text(data["sha256"], "sha256")
    if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
        raise ArtifactFormatError("sha256 must be 64 lowercase hexadecimal characters")

    artifact_id = _required_text(data["artifactId"], "artifactId")
    if artifact_id != f"archive-sha256:{sha256}":
        raise ArtifactFormatError("artifactId must match sha256")

    size = data["size"]
    if type(size) is not int or size <= 0:
        raise ArtifactFormatError("size must be a positive integer")

    original_name = _required_text(data["originalName"], "originalName")
    imported_at = _required_text(data["importedAt"], "importedAt")
    source_note = _required_text(data["sourceNote"], "sourceNote")
    stored_relative_path = _validate_stored_path(data["storedRelativePath"], sha256)

    source_url = data["sourceUrl"]
    if source_url is not None and not isinstance(source_url, str):
        raise ArtifactFormatError("sourceUrl must be a string or null")
    if isinstance(source_url, str) and not source_url.strip():
        raise ArtifactFormatError("sourceUrl must not be blank")

    return ArchiveArtifact(
        schema_version=schema_version,
        artifact_id=artifact_id,
        sha256=sha256,
        size=size,
        original_name=original_name,
        stored_relative_path=stored_relative_path,
        imported_at=imported_at,
        source_note=source_note,
        source_url=source_url,
    )


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ArtifactFormatError(f"{field_name} must be non-blank text")
    return value


def _validate_stored_path(value: object, sha256: str) -> str:
    path_text = _required_text(value, "storedRelativePath")
    if "\\" in path_text:
        raise ArtifactFormatError("storedRelativePath must use forward slashes")

    path = PurePosixPath(path_text)
    windows_path = PureWindowsPath(path_text)
    if path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
        raise ArtifactFormatError("storedRelativePath must be relative")
    if ".." in path.parts:
        raise ArtifactFormatError("storedRelativePath must not traverse parents")

    if len(path.parts) != 5:
        raise ArtifactFormatError("storedRelativePath has an invalid archive layout")
    extension = path.suffix.removeprefix(".")
    if extension not in _ARCHIVE_EXTENSIONS:
        raise ArtifactFormatError("storedRelativePath must end in zip, 7z, or rar")
    expected = PurePosixPath(
        "library", "archives", sha256[:2], sha256, f"payload.{extension}"
    )
    if path != expected:
        raise ArtifactFormatError("storedRelativePath does not match sha256")
    return path.as_posix()
