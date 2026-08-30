"""Immutable transaction journal values."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


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


class TransactionHealth(StrEnum):
    AVAILABLE = "Available"
    MISSING = "Missing"
    MODIFIED = "Modified"


@dataclass(frozen=True)
class RequestedChange:
    relative_path: str
    operation: ChangeOperation


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
    plan_sha256: str
    state: TransactionState
    play_root: str
    staged_root: str
    from_checkpoint_id: str | None
    to_checkpoint_id: str
    created_at: str
    updated_at: str
    entries: tuple[TransactionEntry, ...]
    error: str | None


@dataclass(frozen=True)
class TransactionFinding:
    health: TransactionHealth
    transaction_id: str
    state: TransactionState | None
    journal_path: Path
    issues: tuple[str, ...]
    message: str
