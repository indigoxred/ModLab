"""Prepare and inspect allowlisted file transactions without silent scope expansion."""

import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from modlab.workspace import initialize_workspace

from .model import (
    ChangeOperation,
    FileSnapshot,
    RequestedChange,
    TransactionEntry,
    TransactionJournal,
    TransactionState,
)
from .serialization import (
    TransactionFormatError,
    journal_from_dict,
    journal_to_dict,
    validate_target_relative_path,
)


class TransactionManagerError(RuntimeError):
    """Raised when a transaction cannot proceed without violating its journal."""


_TRANSACTION_ID = re.compile(r"^transaction:([0-9a-f]{32})$")
_CHECKPOINT_ID = re.compile(r"^checkpoint-sha256:[0-9a-f]{64}$")
_CHUNK_SIZE = 1024 * 1024


class TransactionManager:
    def __init__(self, workspace_root: Path):
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.transactions_path = self.workspace_root / "runtime" / "transactions"

    def prepare(
        self,
        play_root: Path,
        staged_root: Path,
        changes: tuple[RequestedChange, ...],
        from_checkpoint_id: str | None,
        to_checkpoint_id: str,
        *,
        transaction_id: str | None = None,
        created_at: str | None = None,
    ) -> TransactionJournal:
        play = Path(play_root).expanduser().resolve()
        staged = Path(staged_root).expanduser().resolve()
        self._validate_roots(play, staged)
        normalized_changes = self._validate_changes(play, staged, changes)
        self._validate_checkpoint_id(to_checkpoint_id, "to checkpoint")
        if from_checkpoint_id is not None:
            self._validate_checkpoint_id(from_checkpoint_id, "from checkpoint")

        selected_id = transaction_id or f"transaction:{uuid.uuid4().hex}"
        tx_dir = self.transaction_directory(selected_id)
        if tx_dir.exists():
            raise TransactionManagerError(
                f"transaction already exists: {selected_id}"
            )
        timestamp = created_at or _utc_now()

        layout = initialize_workspace(self.workspace_root)
        self.transactions_path = layout.transactions
        created_directory = False
        try:
            tx_dir.mkdir(parents=False, exist_ok=False)
            created_directory = True
            entries: list[TransactionEntry] = []
            for change in normalized_changes:
                target = _confined_path(play, change.relative_path)
                staged_source = _confined_path(staged, change.relative_path)
                prior = self._snapshot_if_present(
                    target,
                    tx_dir / "prior" / _relative_path(change.relative_path),
                    f"prior/{change.relative_path}",
                )
                if change.operation is ChangeOperation.REPLACE:
                    desired = self._snapshot_if_present(
                        staged_source,
                        tx_dir / "desired" / _relative_path(change.relative_path),
                        f"desired/{change.relative_path}",
                    )
                    if not desired.present:
                        raise TransactionManagerError(
                            f"staged replacement is missing: {change.relative_path}"
                        )
                else:
                    desired = FileSnapshot(False, None, None, None)
                entries.append(
                    TransactionEntry(
                        relative_path=change.relative_path,
                        operation=change.operation,
                        prior=prior,
                        desired=desired,
                    )
                )

            journal = TransactionJournal(
                schema_version=1,
                transaction_id=selected_id,
                state=TransactionState.PREPARED,
                play_root=str(play),
                staged_root=str(staged),
                from_checkpoint_id=from_checkpoint_id,
                to_checkpoint_id=to_checkpoint_id,
                created_at=timestamp,
                updated_at=timestamp,
                entries=tuple(entries),
                error=None,
            )
            normalized_journal = journal_from_dict(journal_to_dict(journal))
            self._write_journal(normalized_journal)
            return normalized_journal
        except Exception as error:
            if created_directory:
                shutil.rmtree(tx_dir, ignore_errors=False)
            if isinstance(error, TransactionManagerError):
                raise
            raise TransactionManagerError(
                f"Could not prepare transaction {selected_id}: {error}"
            ) from error

    def preflight(self, transaction_id: str) -> tuple[str, ...]:
        journal = self.load(transaction_id)
        if journal.state is not TransactionState.PREPARED:
            raise TransactionManagerError(
                f"preflight requires Prepared, found {journal.state.value}"
            )
        play = Path(journal.play_root)
        drifted = []
        for entry in journal.entries:
            target = _confined_path(play, entry.relative_path)
            if not _matches_snapshot(target, entry.prior):
                drifted.append(entry.relative_path)
        return tuple(drifted)

    def list(self) -> tuple[TransactionJournal, ...]:
        journals = tuple(
            self._load_path(path)
            for path in self.transactions_path.glob("*/journal.json")
        )
        return tuple(
            sorted(journals, key=lambda item: (item.created_at, item.transaction_id))
        )

    def load(self, transaction_id: str) -> TransactionJournal:
        path = self.path_for(transaction_id)
        if not path.is_file():
            raise TransactionManagerError(f"unknown transaction: {transaction_id}")
        journal = self._load_path(path)
        if journal.transaction_id != transaction_id:
            raise TransactionFormatError(
                "transactionId does not match its transaction directory"
            )
        return journal

    def transaction_directory(self, transaction_id: str) -> Path:
        if not isinstance(transaction_id, str):
            raise TransactionFormatError(
                "transaction ID must be transaction:<32 lowercase hex>"
            )
        match = _TRANSACTION_ID.fullmatch(transaction_id)
        if match is None:
            raise TransactionFormatError(
                "transaction ID must be transaction:<32 lowercase hex>"
            )
        return self.transactions_path / match.group(1)

    def path_for(self, transaction_id: str) -> Path:
        return self.transaction_directory(transaction_id) / "journal.json"

    def _validate_roots(self, play: Path, staged: Path) -> None:
        if not play.is_dir():
            raise TransactionManagerError(f"Play root is not a directory: {play}")
        if not staged.is_dir():
            raise TransactionManagerError(f"staged root is not a directory: {staged}")
        if play == staged or play in staged.parents or staged in play.parents:
            raise TransactionManagerError(
                "Play and staged roots must be separate, non-nested directories"
            )

    def _validate_changes(
        self,
        play: Path,
        staged: Path,
        changes: tuple[RequestedChange, ...],
    ) -> tuple[RequestedChange, ...]:
        if not isinstance(changes, tuple) or not changes:
            raise TransactionManagerError("changes must be a non-empty tuple")
        normalized: list[RequestedChange] = []
        for change in changes:
            if not isinstance(change, RequestedChange):
                raise TransactionManagerError("every change must be a RequestedChange")
            try:
                relative_path = validate_target_relative_path(change.relative_path)
            except TransactionFormatError as error:
                raise TransactionManagerError(str(error)) from error
            if not isinstance(change.operation, ChangeOperation):
                raise TransactionManagerError(
                    f"unsupported operation for {relative_path}"
                )
            target = _confined_path(play, relative_path)
            if target.is_symlink() or (target.exists() and not target.is_file()):
                raise TransactionManagerError(
                    f"Play target is not a regular file or absent: {relative_path}"
                )
            if change.operation is ChangeOperation.REPLACE:
                source = _confined_path(staged, relative_path)
                if source.is_symlink() or not source.is_file():
                    raise TransactionManagerError(
                        f"staged replacement is missing or not a regular file: {relative_path}"
                    )
            normalized.append(RequestedChange(relative_path, change.operation))
        paths = [change.relative_path for change in normalized]
        if len(paths) != len(set(paths)):
            raise TransactionManagerError("changes contain a duplicate relative path")
        return tuple(sorted(normalized, key=lambda item: item.relative_path))

    @staticmethod
    def _validate_checkpoint_id(value: str, label: str) -> None:
        if not isinstance(value, str) or _CHECKPOINT_ID.fullmatch(value) is None:
            raise TransactionManagerError(
                f"{label} must be checkpoint-sha256:<64 lowercase hex>"
            )

    @staticmethod
    def _snapshot_if_present(
        source: Path,
        snapshot_path: Path,
        snapshot_relative_path: str,
    ) -> FileSnapshot:
        if not source.exists():
            return FileSnapshot(False, None, None, None)
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        sha256, size = _copy_snapshot(source, snapshot_path)
        return FileSnapshot(True, sha256, size, snapshot_relative_path)

    def _write_journal(self, journal: TransactionJournal) -> None:
        path = self.path_for(journal.transaction_id)
        temporary = path.parent / f"journal.{uuid.uuid4().hex}.part"
        try:
            serialized = json.dumps(
                journal_to_dict(journal),
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
            temporary.write_text(serialized + "\n", encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _load_path(path: Path) -> TransactionJournal:
        try:
            data = json.loads(
                path.read_text(encoding="utf-8"),
                object_pairs_hook=_object_without_duplicate_keys,
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise TransactionFormatError(
                f"Could not read transaction journal {path}: {error}"
            ) from error
        return journal_from_dict(data)


def _relative_path(relative_path: str) -> Path:
    return Path(*PurePosixPath(relative_path).parts)


def _confined_path(root: Path, relative_path: str) -> Path:
    candidate = root.joinpath(*PurePosixPath(relative_path).parts)
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve(strict=False)
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as error:
        raise TransactionManagerError(
            f"path escapes its declared root: {relative_path}"
        ) from error
    return candidate


def _copy_snapshot(source: Path, destination: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as input_stream, destination.open("xb") as output_stream:
        while chunk := input_stream.read(_CHUNK_SIZE):
            output_stream.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _matches_snapshot(path: Path, snapshot: FileSnapshot) -> bool:
    if not snapshot.present:
        return not path.exists() and not path.is_symlink()
    if path.is_symlink() or not path.is_file():
        return False
    try:
        sha256, size = _hash_file(path)
    except OSError:
        return False
    return sha256 == snapshot.sha256 and size == snapshot.size


def _object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise TransactionFormatError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
