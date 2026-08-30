"""Crash-recoverable, allowlisted state transaction primitives."""

from .model import (
    ChangeOperation,
    FileSnapshot,
    RequestedChange,
    TransactionEntry,
    TransactionFinding,
    TransactionHealth,
    TransactionJournal,
    TransactionState,
)
from .serialization import (
    TransactionFormatError,
    calculate_plan_sha256,
    journal_from_dict,
    journal_to_dict,
)
from .manager import TransactionManager, TransactionManagerError

__all__ = [
    "ChangeOperation",
    "FileSnapshot",
    "RequestedChange",
    "TransactionEntry",
    "TransactionFormatError",
    "TransactionFinding",
    "TransactionHealth",
    "TransactionJournal",
    "TransactionManager",
    "TransactionManagerError",
    "TransactionState",
    "calculate_plan_sha256",
    "journal_from_dict",
    "journal_to_dict",
]
