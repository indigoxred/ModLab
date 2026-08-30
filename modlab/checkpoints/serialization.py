"""Canonical, versioned serialization for immutable checkpoints."""

import hashlib
import json
import re
from datetime import datetime
from typing import Mapping

from modlab.recipes.model import RecipeIdentity, RecipeMaturity

from .model import CheckpointDraft, CheckpointRecord


class CheckpointFormatError(ValueError):
    """Raised when checkpoint data is incomplete, unsafe, or inconsistent."""


_BODY_FIELDS = {
    "schemaVersion",
    "game",
    "environmentId",
    "adapterId",
    "createdAt",
    "parentCheckpointId",
    "lineageId",
    "recipe",
    "artifactIds",
    "adapterState",
    "evidence",
}
_RECORD_FIELDS = _BODY_FIELDS | {"checkpointId"}
_RECIPE_FIELDS = {"recipeId", "revision", "maturity", "identity"}
_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_CHECKPOINT_ID = re.compile(r"^checkpoint-sha256:[0-9a-f]{64}$")
_ARTIFACT_ID = re.compile(
    r"^(?:archive|installed|generated|configuration|root)-sha256:[0-9a-f]{64}$"
)


def draft_from_dict(data: object) -> CheckpointDraft:
    mapping = _exact_mapping(data, _BODY_FIELDS, "checkpoint draft")

    schema_version = mapping["schemaVersion"]
    if type(schema_version) is not int or schema_version != 1:
        raise CheckpointFormatError("schemaVersion must be integer 1")

    game = _safe_id(mapping["game"], "game")
    environment_id = _safe_id(mapping["environmentId"], "environmentId")
    adapter_id = _safe_id(mapping["adapterId"], "adapterId")
    lineage_id = _safe_id(mapping["lineageId"], "lineageId")
    created_at = _utc_timestamp(mapping["createdAt"])

    parent_checkpoint_id = mapping["parentCheckpointId"]
    if parent_checkpoint_id is not None:
        parent_checkpoint_id = _checkpoint_id(
            parent_checkpoint_id, "parentCheckpointId"
        )

    recipe = _exact_mapping(mapping["recipe"], _RECIPE_FIELDS, "recipe")
    recipe_id = _safe_id(recipe["recipeId"], "recipe.recipeId")
    recipe_revision = _safe_id(recipe["revision"], "recipe.revision")
    recipe_maturity = _enum_value(
        RecipeMaturity, recipe["maturity"], "recipe.maturity"
    )
    recipe_identity = _enum_value(
        RecipeIdentity, recipe["identity"], "recipe.identity"
    )

    artifact_values = mapping["artifactIds"]
    if not isinstance(artifact_values, list):
        raise CheckpointFormatError("artifactIds must be an array")
    artifact_ids = tuple(
        _artifact_id(value, f"artifactIds[{index}]")
        for index, value in enumerate(artifact_values)
    )
    if len(set(artifact_ids)) != len(artifact_ids):
        raise CheckpointFormatError("artifactIds must not contain duplicates")

    adapter_state_json = _canonical_object(mapping["adapterState"], "adapterState")
    evidence_json = _canonical_object(mapping["evidence"], "evidence")

    return CheckpointDraft(
        schema_version=schema_version,
        game=game,
        environment_id=environment_id,
        adapter_id=adapter_id,
        created_at=created_at,
        parent_checkpoint_id=parent_checkpoint_id,
        lineage_id=lineage_id,
        recipe_id=recipe_id,
        recipe_revision=recipe_revision,
        recipe_maturity=recipe_maturity,
        recipe_identity=recipe_identity,
        artifact_ids=tuple(sorted(artifact_ids)),
        adapter_state_json=adapter_state_json,
        evidence_json=evidence_json,
    )


def checkpoint_record_from_draft(draft: CheckpointDraft) -> CheckpointRecord:
    normalized = draft_from_dict(_draft_to_dict(draft))
    return CheckpointRecord(
        **normalized.__dict__,
        checkpoint_id=checkpoint_id_for_draft(normalized),
    )


def checkpoint_id_for_draft(draft: CheckpointDraft) -> str:
    normalized = draft_from_dict(_draft_to_dict(draft))
    digest = hashlib.sha256(_canonical_bytes(_draft_to_dict(normalized))).hexdigest()
    return f"checkpoint-sha256:{digest}"


def checkpoint_from_dict(data: object) -> CheckpointRecord:
    mapping = _exact_mapping(data, _RECORD_FIELDS, "checkpoint")
    checkpoint_id = _checkpoint_id(mapping["checkpointId"], "checkpointId")
    body = {key: mapping[key] for key in _BODY_FIELDS}
    draft = draft_from_dict(body)
    expected = checkpoint_id_for_draft(draft)
    if checkpoint_id != expected:
        raise CheckpointFormatError(
            f"checkpointId does not match canonical content; expected {expected}"
        )
    return CheckpointRecord(**draft.__dict__, checkpoint_id=checkpoint_id)


def checkpoint_to_dict(record: CheckpointRecord) -> dict[str, object]:
    result = _draft_to_dict(record)
    result["checkpointId"] = record.checkpoint_id
    return result


def _draft_to_dict(draft: CheckpointDraft) -> dict[str, object]:
    try:
        adapter_state = json.loads(draft.adapter_state_json)
        evidence = json.loads(draft.evidence_json)
    except (TypeError, json.JSONDecodeError) as error:
        raise CheckpointFormatError(f"checkpoint contains invalid canonical JSON: {error}") from error
    return {
        "schemaVersion": draft.schema_version,
        "game": draft.game,
        "environmentId": draft.environment_id,
        "adapterId": draft.adapter_id,
        "createdAt": draft.created_at,
        "parentCheckpointId": draft.parent_checkpoint_id,
        "lineageId": draft.lineage_id,
        "recipe": {
            "recipeId": draft.recipe_id,
            "revision": draft.recipe_revision,
            "maturity": draft.recipe_maturity.value,
            "identity": draft.recipe_identity.value,
        },
        "artifactIds": list(draft.artifact_ids),
        "adapterState": adapter_state,
        "evidence": evidence,
    }


def _exact_mapping(
    value: object, expected_fields: set[str], label: str
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise CheckpointFormatError(f"{label} must be an object")
    actual_fields = set(value)
    if actual_fields != expected_fields:
        missing = sorted(expected_fields - actual_fields)
        extra = sorted(actual_fields - expected_fields)
        raise CheckpointFormatError(
            f"{label} fields differ: missing={missing}, extra={extra}"
        )
    return value


def _safe_id(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise CheckpointFormatError(
            f"{field_name} must use lowercase letters, digits, dots, underscores, or hyphens"
        )
    return value


def _checkpoint_id(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _CHECKPOINT_ID.fullmatch(value) is None:
        raise CheckpointFormatError(
            f"{field_name} must be checkpoint-sha256:<64 lowercase hex>"
        )
    return value


def _artifact_id(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _ARTIFACT_ID.fullmatch(value) is None:
        raise CheckpointFormatError(
            f"{field_name} must be a supported typed SHA-256 artifact ID"
        )
    return value


def _utc_timestamp(value: object) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise CheckpointFormatError("createdAt must be an ISO-8601 UTC timestamp ending in Z")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise CheckpointFormatError(
            "createdAt must use YYYY-MM-DDTHH:MM:SSZ"
        ) from error
    return value


def _enum_value(enum_type, value: object, field_name: str):
    if not isinstance(value, str):
        raise CheckpointFormatError(f"{field_name} must be text")
    try:
        return enum_type(value)
    except ValueError as error:
        allowed = ", ".join(item.value for item in enum_type)
        raise CheckpointFormatError(f"{field_name} must be one of: {allowed}") from error


def _canonical_object(value: object, field_name: str) -> str:
    if not isinstance(value, Mapping):
        raise CheckpointFormatError(f"{field_name} must be an object")
    normalized = _validate_json_value(value, field_name)
    return _canonical_bytes(normalized).decode("utf-8")


def _validate_json_value(value: object, location: str):
    if value is None or isinstance(value, (bool, str)) or type(value) is int:
        return value
    if isinstance(value, list):
        return [
            _validate_json_value(item, f"{location}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CheckpointFormatError(f"{location} object keys must be strings")
            result[key] = _validate_json_value(item, f"{location}.{key}")
        return result
    raise CheckpointFormatError(
        f"{location} contains unsupported JSON value type {type(value).__name__}"
    )


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
