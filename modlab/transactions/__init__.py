"""Crash-recoverable, allowlisted state transaction primitives."""

from .model import (
    ChangeOperation,
    FileSnapshot,
    TransactionEntry,
    TransactionJournal,
    TransactionState,
)
from .serialization import TransactionFormatError, journal_from_dict, journal_to_dict

__all__ = [
    "ChangeOperation",
    "FileSnapshot",
    "TransactionEntry",
    "TransactionFormatError",
    "TransactionJournal",
    "TransactionState",
    "journal_from_dict",
    "journal_to_dict",
]
