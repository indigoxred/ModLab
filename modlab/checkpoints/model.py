"""Immutable values for complete, adapter-supplied state snapshots."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from modlab.recipes.model import RecipeIdentity, RecipeMaturity


class CheckpointHealth(StrEnum):
    AVAILABLE = "Available"
    MISSING = "Missing"
    MODIFIED = "Modified"


@dataclass(frozen=True)
class CheckpointDraft:
    schema_version: int
    game: str
    environment_id: str
    adapter_id: str
    created_at: str
    parent_checkpoint_id: str | None
    lineage_id: str
    recipe_id: str
    recipe_revision: str
    recipe_maturity: RecipeMaturity
    recipe_identity: RecipeIdentity
    artifact_ids: tuple[str, ...]
    adapter_state_json: str
    evidence_json: str


@dataclass(frozen=True)
class CheckpointRecord(CheckpointDraft):
    checkpoint_id: str


@dataclass(frozen=True)
class CheckpointFinding:
    health: CheckpointHealth
    checkpoint_id: str
    actual_checkpoint_id: str | None
    path: Path
    message: str
