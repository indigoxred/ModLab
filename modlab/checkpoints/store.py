"""Crash-safe local storage for immutable checkpoint lockfiles."""

import json
import os
import re
import uuid
from pathlib import Path
from typing import Mapping

from modlab.workspace import initialize_workspace

from .model import (
    CheckpointDraft,
    CheckpointFinding,
    CheckpointHealth,
    CheckpointRecord,
)
from .serialization import (
    CheckpointFormatError,
    checkpoint_from_dict,
    checkpoint_id_for_draft,
    checkpoint_record_from_draft,
    checkpoint_to_dict,
    draft_from_dict,
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
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.game_key = game_key
        self.checkpoints_path = self.workspace_root / "games" / game_key / "checkpoints"
        self.jobs_path = self.workspace_root / "runtime" / "jobs"

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

        layout = initialize_workspace(self.workspace_root)
        self.checkpoints_path.mkdir(parents=True, exist_ok=True)
        self.jobs_path = layout.jobs

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

    def verify(self, checkpoint_id: str) -> CheckpointFinding:
        path = self.path_for(checkpoint_id)
        if not path.is_file():
            return CheckpointFinding(
                health=CheckpointHealth.MISSING,
                checkpoint_id=checkpoint_id,
                actual_checkpoint_id=None,
                path=path,
                message="Checkpoint lockfile is missing.",
            )

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            return CheckpointFinding(
                health=CheckpointHealth.MODIFIED,
                checkpoint_id=checkpoint_id,
                actual_checkpoint_id=None,
                path=path,
                message=f"Checkpoint lockfile cannot be parsed: {error}",
            )

        actual_checkpoint_id: str | None = None
        try:
            if not isinstance(data, Mapping):
                raise CheckpointFormatError("checkpoint must be an object")
            body = {key: value for key, value in data.items() if key != "checkpointId"}
            draft = draft_from_dict(body)
            actual_checkpoint_id = checkpoint_id_for_draft(draft)
            stored_checkpoint_id = data.get("checkpointId")
            if (
                actual_checkpoint_id == checkpoint_id
                and stored_checkpoint_id == checkpoint_id
                and draft.game == self.game_key
            ):
                return CheckpointFinding(
                    health=CheckpointHealth.AVAILABLE,
                    checkpoint_id=checkpoint_id,
                    actual_checkpoint_id=actual_checkpoint_id,
                    path=path,
                    message="Checkpoint canonical content matches its identity.",
                )
        except CheckpointFormatError:
            pass

        return CheckpointFinding(
            health=CheckpointHealth.MODIFIED,
            checkpoint_id=checkpoint_id,
            actual_checkpoint_id=actual_checkpoint_id,
            path=path,
            message="Checkpoint content differs from its recorded identity.",
        )

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
