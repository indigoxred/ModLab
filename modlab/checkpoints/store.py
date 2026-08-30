"""Crash-safe local storage for immutable checkpoint lockfiles."""

import json
import os
import re
import uuid
from pathlib import Path

from modlab.workspace import initialize_workspace

from .model import CheckpointDraft, CheckpointRecord
from .serialization import (
    CheckpointFormatError,
    checkpoint_from_dict,
    checkpoint_record_from_draft,
    checkpoint_to_dict,
)


class CheckpointStoreError(RuntimeError):
    """Raised when a checkpoint cannot be stored without risking prior state."""


class CheckpointNotFoundError(LookupError):
    """Raised when a valid checkpoint ID is absent from the selected game store."""


_GAME_KEY = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_CHECKPOINT_ID = re.compile(r"^checkpoint-sha256:([0-9a-f]{64})$")


class CheckpointStore:
    def __init__(self, workspace_root: Path, game_key: str):
        if not isinstance(game_key, str) or _GAME_KEY.fullmatch(game_key) is None:
            raise CheckpointFormatError(
                "game key must use lowercase letters, digits, dots, underscores, or hyphens"
            )
        layout = initialize_workspace(workspace_root)
        self.workspace_root = layout.root
        self.game_key = game_key
        self.checkpoints_path = layout.games / game_key / "checkpoints"
        self.jobs_path = layout.jobs
        self.checkpoints_path.mkdir(parents=True, exist_ok=True)

    def create(self, draft: CheckpointDraft) -> CheckpointRecord:
        record = checkpoint_record_from_draft(draft)
        if record.game != self.game_key:
            raise CheckpointStoreError(
                f"checkpoint game {record.game} does not match store {self.game_key}"
            )
        if record.parent_checkpoint_id is not None:
            try:
                self.get(record.parent_checkpoint_id)
            except CheckpointNotFoundError as error:
                raise CheckpointStoreError(
                    f"parent checkpoint is not present: {record.parent_checkpoint_id}"
                ) from error

        target = self.path_for(record.checkpoint_id)
        if target.exists():
            existing = self.get(record.checkpoint_id)
            if existing != record:
                raise CheckpointStoreError(
                    f"checkpoint path contains different content: {target}"
                )
            return existing

        staging = self.jobs_path / f"checkpoint-{uuid.uuid4().hex}.part"
        try:
            serialized = json.dumps(
                checkpoint_to_dict(record),
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
            staging.write_text(serialized + "\n", encoding="utf-8")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                existing = self.get(record.checkpoint_id)
                if existing != record:
                    raise CheckpointStoreError(
                        f"checkpoint path contains different content: {target}"
                    )
                return existing
            os.replace(staging, target)
            return record
        except CheckpointStoreError:
            raise
        except Exception as error:
            raise CheckpointStoreError(
                f"Could not store checkpoint {record.checkpoint_id}: {error}"
            ) from error
        finally:
            staging.unlink(missing_ok=True)

    def list(self) -> tuple[CheckpointRecord, ...]:
        records = tuple(
            self._load(path)
            for path in self.checkpoints_path.glob("*/modlab.lock.json")
        )
        return tuple(
            sorted(records, key=lambda record: (record.created_at, record.checkpoint_id))
        )

    def get(self, checkpoint_id: str) -> CheckpointRecord:
        path = self.path_for(checkpoint_id)
        if not path.is_file():
            raise CheckpointNotFoundError(f"Unknown checkpoint ID: {checkpoint_id}")
        record = self._load(path)
        if record.checkpoint_id != checkpoint_id:
            raise CheckpointFormatError(
                "checkpointId does not match its content-addressed directory"
            )
        if record.game != self.game_key:
            raise CheckpointFormatError("checkpoint game does not match its game store")
        return record

    def path_for(self, checkpoint_id: str) -> Path:
        if not isinstance(checkpoint_id, str):
            raise CheckpointFormatError(
                "checkpoint ID must be checkpoint-sha256:<64 lowercase hex>"
            )
        match = _CHECKPOINT_ID.fullmatch(checkpoint_id)
        if match is None:
            raise CheckpointFormatError(
                "checkpoint ID must be checkpoint-sha256:<64 lowercase hex>"
            )
        return self.checkpoints_path / match.group(1) / "modlab.lock.json"

    @staticmethod
    def _load(path: Path) -> CheckpointRecord:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise CheckpointFormatError(
                f"Could not read checkpoint lockfile {path}: {error}"
            ) from error
        return checkpoint_from_dict(data)
