"""Immutable transaction journal values."""

from dataclasses import dataclass
from enum import StrEnum


class TransactionState(StrEnum):
    PREPARED = "Prepared"
    APPLYING = "Applying"
    APPLIED = "Applied"
    COMMITTED = "Committed"
    ROLLING_BACK = "Rolling Back"
    ROLLED_BACK = "Rolled Back"
    RECOVERY_REQUIRED = "Recovery Required"


class ChangeOperation(StrEnum):
    REPLACE = "Replace"
    DELETE = "Delete"


@dataclass(frozen=True)
class FileSnapshot:
    present: bool
    sha256: str | None
    size: int | None
    snapshot_relative_path: str | None


@dataclass(frozen=True)
class TransactionEntry:
    relative_path: str
    operation: ChangeOperation
    prior: FileSnapshot
    desired: FileSnapshot


@dataclass(frozen=True)
class TransactionJournal:
    schema_version: int
    transaction_id: str
    state: TransactionState
    play_root: str
    staged_root: str
    from_checkpoint_id: str | None
    to_checkpoint_id: str
    created_at: str
    updated_at: str
    entries: tuple[TransactionEntry, ...]
    error: str | None
