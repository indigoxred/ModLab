"""Prepare and inspect allowlisted file transactions without silent scope expansion."""

import hashlib
import json
import os
import re
import shutil
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

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

        self.transactions_path.mkdir(parents=True, exist_ok=True)
        self._validated_storage_root()
        tx_dir = self.transaction_directory(selected_id)
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
                plan_sha256="0" * 64,
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
            journal = replace(
                journal,
                plan_sha256=calculate_plan_sha256(journal),
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
        play = self._trusted_play_root(journal)
        drifted = []
        for entry in journal.entries:
            target = _confined_path(play, entry.relative_path)
            if not _matches_snapshot(target, entry.prior):
                drifted.append(entry.relative_path)
        return tuple(drifted)

    def apply(
        self, transaction_id: str, *, updated_at: str | None = None
    ) -> TransactionJournal:
        journal = self.load(transaction_id)
        if journal.state is not TransactionState.PREPARED:
            raise TransactionManagerError(
                f"apply requires Prepared, found {journal.state.value}"
            )
        target_drift = self._target_drift(journal, desired=False)
        if target_drift:
            raise TransactionManagerError(
                "Play target drift blocks apply: " + ", ".join(target_drift)
            )
        snapshot_drift = self._snapshot_drift(journal, include_desired=True)
        if snapshot_drift:
            raise TransactionManagerError(
                "transaction snapshot drift blocks apply: "
                + ", ".join(snapshot_drift)
            )

        timestamp = updated_at or _utc_now()
        applying = self._transition(
            journal, TransactionState.APPLYING, timestamp, error=None
        )
        self._write_journal(applying)
        try:
            play = self._trusted_play_root(applying)
            tx_dir = self.transaction_directory(applying.transaction_id)
            for entry in applying.entries:
                play = self._trusted_play_root(applying)
                target = _confined_path(play, entry.relative_path)
                if entry.desired.present:
                    desired_source = _confined_path(
                        tx_dir, entry.desired.snapshot_relative_path
                    )
                    _promote_file(
                        desired_source, target, applying.transaction_id
                    )
                else:
                    _remove_target(target)

            desired_drift = self._target_drift(applying, desired=True)
            if desired_drift:
                raise TransactionManagerError(
                    "applied files do not match desired state: "
                    + ", ".join(desired_drift)
                )
            applied = self._transition(
                applying, TransactionState.APPLIED, timestamp, error=None
            )
            self._write_journal(applied)
            return applied
        except Exception as apply_error:
            try:
                self._restore_prior(
                    applying,
                    error_message=str(apply_error),
                    updated_at=timestamp,
                )
            except TransactionManagerError as recovery_error:
                raise TransactionManagerError(
                    f"transaction apply failed and recovery is required: {recovery_error}"
                ) from apply_error
            raise TransactionManagerError(
                f"transaction apply failed; prior state restored: {apply_error}"
            ) from apply_error

    def commit(
        self, transaction_id: str, *, updated_at: str | None = None
    ) -> TransactionJournal:
        journal = self.load(transaction_id)
        if journal.state is not TransactionState.APPLIED:
            raise TransactionManagerError(
                f"commit requires Applied, found {journal.state.value}"
            )
        desired_drift = self._target_drift(journal, desired=True)
        if desired_drift:
            raise TransactionManagerError(
                "Play no longer matches desired state: "
                + ", ".join(desired_drift)
            )
        snapshot_drift = self._snapshot_drift(journal, include_desired=True)
        if snapshot_drift:
            raise TransactionManagerError(
                "transaction snapshots changed before commit: "
                + ", ".join(snapshot_drift)
            )
        committed = self._transition(
            journal,
            TransactionState.COMMITTED,
            updated_at or _utc_now(),
            error=None,
        )
        self._write_journal(committed)
        return committed

    def recover(
        self, transaction_id: str, *, updated_at: str | None = None
    ) -> TransactionJournal:
        journal = self.load(transaction_id)
        if journal.state is TransactionState.ROLLED_BACK:
            return journal
        if journal.state not in {
            TransactionState.APPLYING,
            TransactionState.APPLIED,
            TransactionState.ROLLING_BACK,
            TransactionState.RECOVERY_REQUIRED,
        }:
            raise TransactionManagerError(
                f"{journal.state.value} transaction does not require recovery"
            )
        return self._restore_prior(
            journal,
            error_message=(
                journal.error
                or f"Recovered interrupted transaction from {journal.state.value}"
            ),
            updated_at=updated_at or _utc_now(),
        )

    def list(self) -> tuple[TransactionJournal, ...]:
        self._validated_storage_root()
        journals: list[TransactionJournal] = []
        if not self.transactions_path.exists():
            return ()
        for directory in self.transactions_path.iterdir():
            if directory.is_symlink():
                raise TransactionManagerError(
                    f"transaction directory must not be a symlink: {directory}"
                )
            if not directory.is_dir():
                continue
            path = directory / "journal.json"
            if not path.is_file():
                continue
            directory_name = directory.name
            if re.fullmatch(r"[0-9a-f]{32}", directory_name) is None:
                raise TransactionFormatError(
                    f"transaction journal has an invalid directory: {directory}"
                )
            journals.append(self.load(f"transaction:{directory_name}"))
        return tuple(
            sorted(journals, key=lambda item: (item.created_at, item.transaction_id))
        )

    def load(self, transaction_id: str) -> TransactionJournal:
        path = self.path_for(transaction_id)
        if path.is_symlink():
            raise TransactionManagerError(
                f"transaction journal must not be a symlink: {path}"
            )
        if not path.is_file():
            raise TransactionManagerError(f"unknown transaction: {transaction_id}")
        journal = self._load_path(path)
        if journal.transaction_id != transaction_id:
            raise TransactionFormatError(
                "transactionId does not match its transaction directory"
            )
        return journal

    def verify(self, transaction_id: str) -> TransactionFinding:
        path = self.path_for(transaction_id)
        if not path.is_file():
            return TransactionFinding(
                TransactionHealth.MISSING,
                transaction_id,
                None,
                path,
                ("journal:missing",),
                "The transaction journal is missing.",
            )
        try:
            journal = self.load(transaction_id)
        except (TransactionFormatError, TransactionManagerError) as error:
            return TransactionFinding(
                TransactionHealth.MODIFIED,
                transaction_id,
                None,
                path,
                ("journal:invalid",),
                f"The transaction journal cannot be trusted: {error}",
            )

        try:
            issues = self._snapshot_drift(journal, include_desired=True)
        except (OSError, TransactionFormatError, TransactionManagerError) as error:
            return TransactionFinding(
                TransactionHealth.MODIFIED,
                transaction_id,
                journal.state,
                path,
                ("snapshot:invalid",),
                f"Retained transaction snapshots cannot be trusted: {error}",
            )
        if issues:
            return TransactionFinding(
                TransactionHealth.MODIFIED,
                transaction_id,
                journal.state,
                path,
                issues,
                "One or more retained transaction snapshots are missing or modified.",
            )
        return TransactionFinding(
            TransactionHealth.AVAILABLE,
            transaction_id,
            journal.state,
            path,
            (),
            "The journal plan and all retained snapshots match their recorded identities.",
        )

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
        transaction_directory = self.transactions_path / match.group(1)
        if transaction_directory.is_symlink():
            raise TransactionManagerError(
                "transaction directory must not be a symlink: "
                f"{transaction_directory}"
            )
        resolved_storage = self._validated_storage_root()
        resolved_transaction = transaction_directory.resolve(strict=False)
        try:
            resolved_transaction.relative_to(resolved_storage)
        except ValueError as error:
            raise TransactionManagerError(
                "transaction storage escapes the workspace"
            ) from error
        return transaction_directory

    def _validated_storage_root(self) -> Path:
        runtime_path = self.workspace_root / "runtime"
        for storage_path in (runtime_path, self.transactions_path):
            if storage_path.is_symlink():
                raise TransactionManagerError(
                    f"transaction storage must not be a symlink: {storage_path}"
                )
            if storage_path.exists() and not storage_path.is_dir():
                raise TransactionManagerError(
                    f"transaction storage is not a directory: {storage_path}"
                )
        resolved_storage = self.transactions_path.resolve(strict=False)
        try:
            resolved_storage.relative_to(self.workspace_root)
        except ValueError as error:
            raise TransactionManagerError(
                "transaction storage escapes the workspace"
            ) from error
        return resolved_storage

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
            if not target.parent.is_dir():
                raise TransactionManagerError(
                    f"Play target parent does not exist: {relative_path}"
                )
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

    def _target_drift(
        self, journal: TransactionJournal, *, desired: bool
    ) -> tuple[str, ...]:
        play = self._trusted_play_root(journal)
        drifted: list[str] = []
        for entry in journal.entries:
            target = _confined_path(play, entry.relative_path)
            expected = entry.desired if desired else entry.prior
            if not _matches_snapshot(target, expected):
                drifted.append(entry.relative_path)
        return tuple(drifted)

    def _snapshot_drift(
        self, journal: TransactionJournal, *, include_desired: bool
    ) -> tuple[str, ...]:
        tx_dir = self.transaction_directory(journal.transaction_id)
        drifted: list[str] = []
        for entry in journal.entries:
            snapshots = [("prior", entry.prior)]
            if include_desired:
                snapshots.append(("desired", entry.desired))
            for label, snapshot in snapshots:
                if not snapshot.present:
                    continue
                path = _confined_path(tx_dir, snapshot.snapshot_relative_path)
                if not _matches_snapshot(path, snapshot):
                    drifted.append(f"{label}:{entry.relative_path}")
        return tuple(drifted)

    def _restore_prior(
        self,
        journal: TransactionJournal,
        *,
        error_message: str,
        updated_at: str,
    ) -> TransactionJournal:
        prior_drift = self._snapshot_drift(journal, include_desired=False)
        if prior_drift:
            recovery_error = (
                "prior recovery snapshots are unavailable: "
                + ", ".join(prior_drift)
            )
            required = self._transition(
                journal,
                TransactionState.RECOVERY_REQUIRED,
                updated_at,
                error=recovery_error,
            )
            self._write_journal(required)
            raise TransactionManagerError(recovery_error)

        rolling_back = self._transition(
            journal, TransactionState.ROLLING_BACK, updated_at, error=None
        )
        self._write_journal(rolling_back)
        try:
            play = self._trusted_play_root(rolling_back)
            tx_dir = self.transaction_directory(rolling_back.transaction_id)
            for entry in rolling_back.entries:
                play = self._trusted_play_root(rolling_back)
                target = _confined_path(play, entry.relative_path)
                if entry.prior.present:
                    prior_source = _confined_path(
                        tx_dir, entry.prior.snapshot_relative_path
                    )
                    _promote_file(
                        prior_source, target, rolling_back.transaction_id
                    )
                else:
                    _remove_target(target)

            restored_drift = self._target_drift(
                rolling_back, desired=False
            )
            if restored_drift:
                raise TransactionManagerError(
                    "restored files do not match prior state: "
                    + ", ".join(restored_drift)
                )
            rolled_back = self._transition(
                rolling_back,
                TransactionState.ROLLED_BACK,
                updated_at,
                error=error_message,
            )
            self._write_journal(rolled_back)
            return rolled_back
        except Exception as restore_error:
            recovery_error = f"rollback could not restore prior state: {restore_error}"
            required = self._transition(
                rolling_back,
                TransactionState.RECOVERY_REQUIRED,
                updated_at,
                error=recovery_error,
            )
            self._write_journal(required)
            raise TransactionManagerError(recovery_error) from restore_error

    @staticmethod
    def _transition(
        journal: TransactionJournal,
        state: TransactionState,
        updated_at: str,
        *,
        error: str | None,
    ) -> TransactionJournal:
        candidate = replace(
            journal,
            state=state,
            updated_at=updated_at,
            error=error,
        )
        return journal_from_dict(journal_to_dict(candidate))

    @staticmethod
    def _trusted_play_root(journal: TransactionJournal) -> Path:
        declared = Path(journal.play_root)
        if declared.is_symlink() or not declared.is_dir():
            raise TransactionManagerError(
                f"Play root is missing or redirected: {declared}"
            )
        try:
            resolved = declared.resolve(strict=True)
        except OSError as error:
            raise TransactionManagerError(
                f"Play root cannot be resolved safely: {declared}"
            ) from error
        if os.path.normcase(str(resolved)) != os.path.normcase(str(declared)):
            raise TransactionManagerError(
                f"Play root is redirected: {declared} -> {resolved}"
            )
        return declared

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
    relative_parts = PurePosixPath(relative_path).parts
    candidate = root.joinpath(*relative_parts)
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve(strict=False)
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as error:
        raise TransactionManagerError(
            f"path escapes its declared root: {relative_path}"
        ) from error
    parent = root
    for part in relative_parts[:-1]:
        parent = parent / part
        if parent.is_symlink():
            raise TransactionManagerError(
                f"path parent is redirected: {relative_path}"
            )
        resolved_parent = parent.resolve(strict=False)
        if os.path.normcase(str(resolved_parent)) != os.path.normcase(str(parent)):
            raise TransactionManagerError(
                f"path parent is redirected: {relative_path}"
            )
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


def _promote_file(source: Path, target: Path, transaction_id: str) -> None:
    if not target.parent.is_dir():
        raise TransactionManagerError(
            f"target parent disappeared during transaction: {target.parent}"
        )
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise TransactionManagerError(
            f"target is no longer a regular file or absent: {target}"
        )
    tx_suffix = transaction_id.removeprefix("transaction:")
    temporary = target.parent / f".modlab-{tx_suffix}-{uuid.uuid4().hex}.part"
    try:
        _copy_snapshot(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _remove_target(target: Path) -> None:
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise TransactionManagerError(
            f"target is no longer a regular file or absent: {target}"
        )
    target.unlink(missing_ok=True)


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
