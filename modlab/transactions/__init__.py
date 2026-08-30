"""Crash-recoverable, allowlisted state transaction primitives."""

from .model import (
    ChangeOperation,
    FileSnapshot,
    RequestedChange,
    TransactionEntry,
    TransactionJournal,
    TransactionState,
)
from .serialization import TransactionFormatError, journal_from_dict, journal_to_dict
from .manager import TransactionManager, TransactionManagerError

__all__ = [
    "ChangeOperation",
    "FileSnapshot",
    "RequestedChange",
    "TransactionEntry",
    "TransactionFormatError",
    "TransactionJournal",
    "TransactionManager",
    "TransactionManagerError",
    "TransactionState",
    "journal_from_dict",
    "journal_to_dict",
]
