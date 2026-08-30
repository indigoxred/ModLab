"""Strict serialization for crash-recovery transaction journals."""

import re
from datetime import datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Mapping

from .model import (
    ChangeOperation,
    FileSnapshot,
    TransactionEntry,
    TransactionJournal,
    TransactionState,
)


class TransactionFormatError(ValueError):
    """Raised when a transaction journal cannot be trusted."""


_JOURNAL_FIELDS = {
    "schemaVersion",
    "transactionId",
    "state",
    "playRoot",
    "stagedRoot",
    "fromCheckpointId",
    "toCheckpointId",
    "createdAt",
    "updatedAt",
    "entries",
    "error",
}
_ENTRY_FIELDS = {"relativePath", "operation", "prior", "desired"}
_SNAPSHOT_FIELDS = {"present", "sha256", "size", "snapshotRelativePath"}
_TRANSACTION_ID = re.compile(r"^transaction:[0-9a-f]{32}$")
_CHECKPOINT_ID = re.compile(r"^checkpoint-sha256:[0-9a-f]{64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAVE_EXTENSIONS = {".ess", ".skse"}


def journal_from_dict(data: object) -> TransactionJournal:
    mapping = _exact_mapping(data, _JOURNAL_FIELDS, "transaction journal")

    schema_version = mapping["schemaVersion"]
    if type(schema_version) is not int or schema_version != 1:
        raise TransactionFormatError("schemaVersion must be integer 1")

    transaction_id = _transaction_id(mapping["transactionId"])
    state = _enum_value(TransactionState, mapping["state"], "state")
    play_root = _absolute_root(mapping["playRoot"], "playRoot")
    staged_root = _absolute_root(mapping["stagedRoot"], "stagedRoot")
    if play_root.casefold() == staged_root.casefold():
        raise TransactionFormatError("playRoot and stagedRoot must be different")

    from_checkpoint_id = mapping["fromCheckpointId"]
    if from_checkpoint_id is not None:
        from_checkpoint_id = _checkpoint_id(
            from_checkpoint_id, "fromCheckpointId"
        )
    to_checkpoint_id = _checkpoint_id(mapping["toCheckpointId"], "toCheckpointId")
    created_at = _utc_timestamp(mapping["createdAt"], "createdAt")
    updated_at = _utc_timestamp(mapping["updatedAt"], "updatedAt")
    if updated_at < created_at:
        raise TransactionFormatError("updatedAt must not precede createdAt")

    entry_values = mapping["entries"]
    if not isinstance(entry_values, list) or not entry_values:
        raise TransactionFormatError("entries must be a non-empty array")
    entries = tuple(_entry_from_dict(value) for value in entry_values)
    paths = [entry.relative_path for entry in entries]
    if len(paths) != len(set(paths)):
        raise TransactionFormatError("entries contain a duplicate relativePath")

    error_value = mapping["error"]
    if error_value is not None and (
        not isinstance(error_value, str) or not error_value.strip()
    ):
        raise TransactionFormatError("error must be null or non-blank text")
    if state is TransactionState.RECOVERY_REQUIRED and error_value is None:
        raise TransactionFormatError("Recovery Required journals must record an error")
    if state in {
        TransactionState.PREPARED,
        TransactionState.APPLYING,
        TransactionState.APPLIED,
        TransactionState.COMMITTED,
        TransactionState.ROLLING_BACK,
    } and error_value is not None:
        raise TransactionFormatError(f"{state.value} journals cannot record an error")

    return TransactionJournal(
        schema_version=schema_version,
        transaction_id=transaction_id,
        state=state,
        play_root=play_root,
        staged_root=staged_root,
        from_checkpoint_id=from_checkpoint_id,
        to_checkpoint_id=to_checkpoint_id,
        created_at=created_at,
        updated_at=updated_at,
        entries=tuple(sorted(entries, key=lambda entry: entry.relative_path)),
        error=error_value,
    )


def journal_to_dict(journal: TransactionJournal) -> dict[str, object]:
    return {
        "schemaVersion": journal.schema_version,
        "transactionId": journal.transaction_id,
        "state": journal.state.value,
        "playRoot": journal.play_root,
        "stagedRoot": journal.staged_root,
        "fromCheckpointId": journal.from_checkpoint_id,
        "toCheckpointId": journal.to_checkpoint_id,
        "createdAt": journal.created_at,
        "updatedAt": journal.updated_at,
        "entries": [_entry_to_dict(entry) for entry in journal.entries],
        "error": journal.error,
    }


def validate_target_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise TransactionFormatError("relativePath must be non-blank text")
    if "\\" in value:
        raise TransactionFormatError("relativePath must use forward slashes")
    raw_parts = value.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise TransactionFormatError("relativePath contains an unsafe path segment")
    posix_path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if posix_path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
        raise TransactionFormatError("relativePath must not be absolute or drive-qualified")
    if any(part.casefold() == "saves" for part in raw_parts):
        raise TransactionFormatError("save directories are excluded from transactions")
    if posix_path.suffix.casefold() in _SAVE_EXTENSIONS:
        raise TransactionFormatError("save and co-save files are excluded from transactions")
    return posix_path.as_posix()


def _entry_from_dict(value: object) -> TransactionEntry:
    mapping = _exact_mapping(value, _ENTRY_FIELDS, "transaction entry")
    relative_path = validate_target_relative_path(mapping["relativePath"])
    operation = _enum_value(
        ChangeOperation, mapping["operation"], "entry.operation"
    )
    prior = _snapshot_from_dict(mapping["prior"], "prior", relative_path)
    desired = _snapshot_from_dict(mapping["desired"], "desired", relative_path)
    if operation is ChangeOperation.REPLACE and not desired.present:
        raise TransactionFormatError("Replace entries require a desired file")
    if operation is ChangeOperation.DELETE and desired.present:
        raise TransactionFormatError("Delete entries cannot contain a desired file")
    return TransactionEntry(relative_path, operation, prior, desired)


def _entry_to_dict(entry: TransactionEntry) -> dict[str, object]:
    return {
        "relativePath": entry.relative_path,
        "operation": entry.operation.value,
        "prior": _snapshot_to_dict(entry.prior),
        "desired": _snapshot_to_dict(entry.desired),
    }


def _snapshot_from_dict(
    value: object, snapshot_kind: str, relative_path: str
) -> FileSnapshot:
    mapping = _exact_mapping(value, _SNAPSHOT_FIELDS, f"{snapshot_kind} snapshot")
    present = mapping["present"]
    if type(present) is not bool:
        raise TransactionFormatError(f"{snapshot_kind}.present must be boolean")
    sha256 = mapping["sha256"]
    size = mapping["size"]
    snapshot_path = mapping["snapshotRelativePath"]
    if not present:
        if sha256 is not None or size is not None or snapshot_path is not None:
            raise TransactionFormatError(
                f"absent {snapshot_kind} snapshot must have null hash, size, and path"
            )
        return FileSnapshot(False, None, None, None)

    if not isinstance(sha256, str) or _SHA256.fullmatch(sha256) is None:
        raise TransactionFormatError(
            f"present {snapshot_kind} snapshot requires a lowercase SHA-256"
        )
    if type(size) is not int or size < 0:
        raise TransactionFormatError(
            f"present {snapshot_kind} snapshot requires a non-negative size"
        )
    expected_path = f"{snapshot_kind}/{relative_path}"
    if snapshot_path != expected_path:
        raise TransactionFormatError(
            f"{snapshot_kind}.snapshotRelativePath must equal {expected_path}"
        )
    return FileSnapshot(True, sha256, size, expected_path)


def _snapshot_to_dict(snapshot: FileSnapshot) -> dict[str, object]:
    return {
        "present": snapshot.present,
        "sha256": snapshot.sha256,
        "size": snapshot.size,
        "snapshotRelativePath": snapshot.snapshot_relative_path,
    }


def _exact_mapping(
    value: object, expected_fields: set[str], label: str
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TransactionFormatError(f"{label} must be an object")
    actual_fields = set(value)
    if actual_fields != expected_fields:
        missing = sorted(str(field) for field in expected_fields - actual_fields)
        extra = sorted(str(field) for field in actual_fields - expected_fields)
        raise TransactionFormatError(
            f"{label} fields differ: missing={missing}, extra={extra}"
        )
    return value


def _absolute_root(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TransactionFormatError(f"{field_name} must be an absolute path")
    path = PureWindowsPath(value)
    if not path.is_absolute() or not path.drive:
        raise TransactionFormatError(f"{field_name} must be an absolute path")
    if ".." in path.parts:
        raise TransactionFormatError(f"{field_name} must not traverse parents")
    return str(path)


def _transaction_id(value: object) -> str:
    if not isinstance(value, str) or _TRANSACTION_ID.fullmatch(value) is None:
        raise TransactionFormatError(
            "transactionId must be transaction:<32 lowercase hex>"
        )
    return value


def _checkpoint_id(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _CHECKPOINT_ID.fullmatch(value) is None:
        raise TransactionFormatError(
            f"{field_name} must be checkpoint-sha256:<64 lowercase hex>"
        )
    return value


def _utc_timestamp(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TransactionFormatError(f"{field_name} must be a UTC timestamp")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise TransactionFormatError(
            f"{field_name} must use YYYY-MM-DDTHH:MM:SSZ"
        ) from error
    return value


def _enum_value(enum_type, value: object, field_name: str):
    if not isinstance(value, str):
        raise TransactionFormatError(f"{field_name} must be text")
    try:
        return enum_type(value)
    except ValueError as error:
        allowed = ", ".join(item.value for item in enum_type)
        raise TransactionFormatError(f"{field_name} must be one of: {allowed}") from error
