"""Strict evidence contract for an honest observed Skyrim baseline."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PureWindowsPath

from modlab.adapters.mo2.comparison_serialization import (
    Mo2ComparisonFormatError,
    adapter_state_from_dict,
    capabilities_from_dict,
    capabilities_to_dict,
    findings_from_dict,
    findings_to_dict,
    observation_context_from_dict,
    observation_context_to_dict,
)
from modlab.adapters.mo2.model import Mo2Finding
from modlab.adapters.mo2.projection import (
    Mo2AdapterState,
    Mo2Capability,
    Mo2ObservationContext,
    adapter_state_sha256,
    adapter_state_to_dict,
)
from modlab.adapters.skyrim.model import SkyrimDiscoveryReport
from modlab.adapters.skyrim.serialization import (
    SkyrimDiscoveryFormatError,
    discovery_from_dict,
    discovery_to_dict,
)
from modlab.recipes.model import CheckState, RecipeIdentity

from .observation import LiveDimensionFinding


class BaselineEvidenceFormatError(ValueError):
    """Observed-baseline evidence is malformed or makes an unsafe claim."""


@dataclass(frozen=True)
class BaselineRecipeEvidence:
    recipe_source_sha256: str
    selected: tuple[str, ...]
    omitted: tuple[str, ...]
    identity: RecipeIdentity
    ready_for_approval: bool


@dataclass(frozen=True)
class BaselineTargetEnvironmentEvidence:
    environment_source_sha256: str
    environment_id: str
    dimensions: tuple[tuple[str, str], ...]
    live_dimension_findings: tuple[LiveDimensionFinding, ...]


@dataclass(frozen=True)
class ObservedBaselineEvidence:
    game_discovery: SkyrimDiscoveryReport
    manager_observation_context: Mo2ObservationContext
    manager_capabilities: tuple[Mo2Capability, ...]
    manager_findings: tuple[Mo2Finding, ...]
    adapter_state_sha256: str
    recipe_review: BaselineRecipeEvidence
    target_environment: BaselineTargetEnvironmentEvidence

    @property
    def promotion_ready(self) -> bool:
        return False

    @property
    def restorable(self) -> bool:
        return False


_TOP_FIELDS = {
    "schemaVersion",
    "captureKind",
    "qualification",
    "promotionReady",
    "restorable",
    "gameDiscovery",
    "managerObservation",
    "recipeReview",
    "targetEnvironment",
    "foundationAssembly",
    "validation",
    "coverage",
    "actionsPerformed",
    "downloadsPerformed",
    "installationActionsPerformed",
    "programsLaunched",
}
_MANAGER_FIELDS = {
    "observationContext",
    "capabilities",
    "findings",
    "adapterStateSha256",
}
_RECIPE_FIELDS = {
    "recipeSourceSha256",
    "selected",
    "omitted",
    "identity",
    "readyForApproval",
}
_TARGET_FIELDS = {
    "environmentSourceSha256",
    "environmentId",
    "dimensions",
    "liveDimensionFindings",
}
_DIMENSION_FINDING_FIELDS = {
    "dimension",
    "targetValue",
    "actualValue",
    "state",
    "source",
    "message",
}
_VALIDATION_FIELDS = {"runtimeValidation", "smokeTest"}
_COVERAGE_FIELDS = {
    "profileState",
    "installedPayloadContent",
    "artifactCoverage",
    "assetConflicts",
    "pluginRecordConflicts",
}
_CAPTURE_KIND = "ObservedBaseline"
_QUALIFICATION = "Observed"
_FOUNDATION_ASSEMBLY = "NotVerified"
_NOT_PERFORMED = "NotPerformed"
_NOT_INSPECTED = "NotInspected"
_NOT_LINKED = "NotLinked"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_DIMENSION = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,127}$")
_REQUIRED_PATH_KINDS = {"base", "downloads", "mods", "profiles", "overwrite"}


def baseline_evidence_to_dict(
    evidence: ObservedBaselineEvidence,
    *,
    adapter_state: Mo2AdapterState,
) -> dict[str, object]:
    expected_adapter_hash = adapter_state_sha256(adapter_state)
    if evidence.adapter_state_sha256 != expected_adapter_hash:
        raise BaselineEvidenceFormatError(
            "adapterStateSha256 does not match the supplied adapter state"
        )
    value = {
        "schemaVersion": 1,
        "captureKind": _CAPTURE_KIND,
        "qualification": _QUALIFICATION,
        "promotionReady": False,
        "restorable": False,
        "gameDiscovery": discovery_to_dict(evidence.game_discovery),
        "managerObservation": {
            "observationContext": observation_context_to_dict(
                evidence.manager_observation_context
            ),
            "capabilities": capabilities_to_dict(
                evidence.manager_capabilities
            ),
            "findings": findings_to_dict(evidence.manager_findings),
            "adapterStateSha256": expected_adapter_hash,
        },
        "recipeReview": {
            "recipeSourceSha256": evidence.recipe_review.recipe_source_sha256,
            "selected": list(evidence.recipe_review.selected),
            "omitted": list(evidence.recipe_review.omitted),
            "identity": evidence.recipe_review.identity.value,
            "readyForApproval": evidence.recipe_review.ready_for_approval,
        },
        "targetEnvironment": {
            "environmentSourceSha256": (
                evidence.target_environment.environment_source_sha256
            ),
            "environmentId": evidence.target_environment.environment_id,
            "dimensions": [
                [dimension, item]
                for dimension, item in evidence.target_environment.dimensions
            ],
            "liveDimensionFindings": [
                {
                    "dimension": item.dimension,
                    "targetValue": item.target_value,
                    "actualValue": item.actual_value,
                    "state": item.state.value,
                    "source": item.source,
                    "message": item.message,
                }
                for item in evidence.target_environment.live_dimension_findings
            ],
        },
        "foundationAssembly": _FOUNDATION_ASSEMBLY,
        "validation": {
            "runtimeValidation": _NOT_PERFORMED,
            "smokeTest": _NOT_PERFORMED,
        },
        "coverage": {
            "profileState": "Complete",
            "installedPayloadContent": _NOT_INSPECTED,
            "artifactCoverage": _NOT_LINKED,
            "assetConflicts": _NOT_INSPECTED,
            "pluginRecordConflicts": _NOT_INSPECTED,
        },
        "actionsPerformed": [],
        "downloadsPerformed": [],
        "installationActionsPerformed": [],
        "programsLaunched": [],
    }
    baseline_evidence_from_dict(value, adapter_state=adapter_state)
    return value


def baseline_evidence_from_dict(
    value: object,
    *,
    adapter_state: Mo2AdapterState,
) -> ObservedBaselineEvidence:
    try:
        adapter_state = adapter_state_from_dict(
            adapter_state_to_dict(adapter_state)
        )
        return _baseline_evidence_from_dict(value, adapter_state)
    except BaselineEvidenceFormatError:
        raise
    except (Mo2ComparisonFormatError, SkyrimDiscoveryFormatError) as error:
        raise BaselineEvidenceFormatError(str(error)) from error


def _baseline_evidence_from_dict(
    value: object,
    adapter_state: Mo2AdapterState,
) -> ObservedBaselineEvidence:
    root = _exact_mapping(value, _TOP_FIELDS, "baseline evidence")
    _reject_save_like_values(root)
    if type(root["schemaVersion"]) is not int or root["schemaVersion"] != 1:
        raise BaselineEvidenceFormatError("schemaVersion must be integer 1")
    _literal(root["captureKind"], _CAPTURE_KIND, "captureKind")
    _literal(root["qualification"], _QUALIFICATION, "qualification")
    if root["promotionReady"] is not False:
        raise BaselineEvidenceFormatError("promotionReady must be false")
    if root["restorable"] is not False:
        raise BaselineEvidenceFormatError("restorable must be false")
    _literal(
        root["foundationAssembly"],
        _FOUNDATION_ASSEMBLY,
        "foundationAssembly",
    )
    for field in (
        "actionsPerformed",
        "downloadsPerformed",
        "installationActionsPerformed",
        "programsLaunched",
    ):
        if root[field] != []:
            raise BaselineEvidenceFormatError(
                f"{field} must be an explicit empty array"
            )

    validation = _exact_mapping(
        root["validation"], _VALIDATION_FIELDS, "validation"
    )
    _literal(
        validation["runtimeValidation"],
        _NOT_PERFORMED,
        "validation.runtimeValidation",
    )
    _literal(
        validation["smokeTest"],
        _NOT_PERFORMED,
        "validation.smokeTest",
    )
    coverage = _exact_mapping(root["coverage"], _COVERAGE_FIELDS, "coverage")
    expected_coverage = {
        "profileState": "Complete",
        "installedPayloadContent": _NOT_INSPECTED,
        "artifactCoverage": _NOT_LINKED,
        "assetConflicts": _NOT_INSPECTED,
        "pluginRecordConflicts": _NOT_INSPECTED,
    }
    if any(coverage[field] != expected for field, expected in expected_coverage.items()):
        raise BaselineEvidenceFormatError("coverage contains an invalid claim")

    discovery = discovery_from_dict(root["gameDiscovery"])
    if (
        discovery.app_id != "489830"
        or discovery.game_root is None
        or discovery.executable is None
        or any(item.state is CheckState.BLOCKED for item in discovery.findings)
    ):
        raise BaselineEvidenceFormatError(
            "gameDiscovery must contain complete non-blocking Skyrim identity"
        )

    manager = _exact_mapping(
        root["managerObservation"], _MANAGER_FIELDS, "managerObservation"
    )
    context = observation_context_from_dict(manager["observationContext"])
    capabilities = capabilities_from_dict(manager["capabilities"])
    findings = findings_from_dict(manager["findings"])
    expected_adapter_hash = adapter_state_sha256(adapter_state)
    if manager["adapterStateSha256"] != expected_adapter_hash:
        raise BaselineEvidenceFormatError(
            "managerObservation.adapterStateSha256 does not match adapter state"
        )
    if (
        context.executable is None
        or not context.read_set_stable
        or context.read_set_sha256 is None
        or set(item.kind for item in context.configured_paths)
        != _REQUIRED_PATH_KINDS
        or not all(item.contained for item in context.configured_paths)
        or any(item.status != "complete" for item in capabilities)
        or any(item.state is CheckState.BLOCKED for item in findings)
    ):
        raise BaselineEvidenceFormatError(
            "managerObservation must contain complete non-blocking stable evidence"
        )
    if PureWindowsPath(context.game_root) != PureWindowsPath(discovery.game_root):
        raise BaselineEvidenceFormatError(
            "managerObservation gameRoot does not match gameDiscovery"
        )
    if discovery.mo2_path is not None and (
        PureWindowsPath(discovery.mo2_path).parent
        != PureWindowsPath(context.mo2_root)
    ):
        raise BaselineEvidenceFormatError(
            "managerObservation mo2Root does not match gameDiscovery mo2Path"
        )

    recipe_data = _exact_mapping(
        root["recipeReview"], _RECIPE_FIELDS, "recipeReview"
    )
    selected = _ordered_ids(recipe_data["selected"], "recipeReview.selected")
    omitted = _ordered_ids(recipe_data["omitted"], "recipeReview.omitted")
    overlap = sorted(set(selected) & set(omitted))
    if overlap:
        raise BaselineEvidenceFormatError(
            "recipeReview selected and omitted overlap: " + ", ".join(overlap)
        )
    try:
        identity = RecipeIdentity(recipe_data["identity"])
    except (TypeError, ValueError) as error:
        raise BaselineEvidenceFormatError(
            "recipeReview.identity must be Original or Custom"
        ) from error
    if identity is RecipeIdentity.INCOMPLETE:
        raise BaselineEvidenceFormatError(
            "recipeReview.identity must not be Incomplete"
        )
    if recipe_data["readyForApproval"] is not True:
        raise BaselineEvidenceFormatError(
            "recipeReview.readyForApproval must be true"
        )
    recipe = BaselineRecipeEvidence(
        recipe_source_sha256=_sha256(
            recipe_data["recipeSourceSha256"],
            "recipeReview.recipeSourceSha256",
        ),
        selected=selected,
        omitted=omitted,
        identity=identity,
        ready_for_approval=True,
    )

    target_data = _exact_mapping(
        root["targetEnvironment"], _TARGET_FIELDS, "targetEnvironment"
    )
    dimensions = _dimensions(target_data["dimensions"])
    live_findings = _live_dimension_findings(
        target_data["liveDimensionFindings"], dimensions
    )
    target = BaselineTargetEnvironmentEvidence(
        environment_source_sha256=_sha256(
            target_data["environmentSourceSha256"],
            "targetEnvironment.environmentSourceSha256",
        ),
        environment_id=_safe_id(
            target_data["environmentId"], "targetEnvironment.environmentId"
        ),
        dimensions=dimensions,
        live_dimension_findings=live_findings,
    )
    return ObservedBaselineEvidence(
        game_discovery=discovery,
        manager_observation_context=context,
        manager_capabilities=capabilities,
        manager_findings=findings,
        adapter_state_sha256=expected_adapter_hash,
        recipe_review=recipe,
        target_environment=target,
    )


def _dimensions(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        raise BaselineEvidenceFormatError(
            "targetEnvironment.dimensions must be an array"
        )
    result: list[tuple[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, list) or len(item) != 2:
            raise BaselineEvidenceFormatError(
                f"targetEnvironment.dimensions[{index}] must be a two-item array"
            )
        dimension = _dimension(item[0], f"dimensions[{index}][0]")
        result.append((dimension, _text(item[1], f"dimensions[{index}][1]")))
    ordered = tuple(result)
    if ordered != tuple(sorted(ordered)) or len({key for key, _ in ordered}) != len(
        ordered
    ):
        raise BaselineEvidenceFormatError(
            "targetEnvironment.dimensions must be sorted with unique names"
        )
    return ordered


def _live_dimension_findings(
    value: object,
    dimensions: tuple[tuple[str, str], ...],
) -> tuple[LiveDimensionFinding, ...]:
    if not isinstance(value, list):
        raise BaselineEvidenceFormatError(
            "targetEnvironment.liveDimensionFindings must be an array"
        )
    targets = dict(dimensions)
    findings: list[LiveDimensionFinding] = []
    for index, item in enumerate(value):
        mapping = _exact_mapping(
            item,
            _DIMENSION_FINDING_FIELDS,
            f"liveDimensionFindings[{index}]",
        )
        dimension = _dimension(mapping["dimension"], "finding.dimension")
        target_value = _text(mapping["targetValue"], "finding.targetValue")
        if targets.get(dimension) != target_value:
            raise BaselineEvidenceFormatError(
                "live dimension finding target does not match target dimensions"
            )
        actual_value = mapping["actualValue"]
        if actual_value is not None:
            actual_value = _text(actual_value, "finding.actualValue")
        try:
            state = CheckState(mapping["state"])
        except (TypeError, ValueError) as error:
            raise BaselineEvidenceFormatError(
                "live dimension finding state is invalid"
            ) from error
        if state not in {CheckState.PASSED, CheckState.BLOCKED, CheckState.UNKNOWN}:
            raise BaselineEvidenceFormatError(
                "live dimension finding state must be Passed, Blocked, or Unknown"
            )
        if (
            (state is CheckState.PASSED and actual_value != target_value)
            or (state is CheckState.BLOCKED and (actual_value is None or actual_value == target_value))
            or (state is CheckState.UNKNOWN and actual_value is not None)
        ):
            raise BaselineEvidenceFormatError(
                "live dimension finding state disagrees with actual and target values"
            )
        if state is CheckState.BLOCKED:
            raise BaselineEvidenceFormatError(
                "observed baseline cannot contain a blocked live dimension"
            )
        findings.append(
            LiveDimensionFinding(
                dimension=dimension,
                target_value=target_value,
                actual_value=actual_value,
                state=state,
                source=_text(mapping["source"], "finding.source"),
                message=_text(mapping["message"], "finding.message"),
            )
        )
    result = tuple(findings)
    if (
        tuple(item.dimension for item in result) != tuple(sorted(targets))
        or len({item.dimension for item in result}) != len(result)
    ):
        raise BaselineEvidenceFormatError(
            "liveDimensionFindings must cover dimensions once in sorted order"
        )
    return result


def _exact_mapping(
    value: object, expected: set[str], label: str
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise BaselineEvidenceFormatError(f"{label} must be an object")
    actual = set(value)
    if actual != expected:
        raise BaselineEvidenceFormatError(
            f"{label} fields differ: missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )
    return value


def _ordered_ids(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise BaselineEvidenceFormatError(f"{label} must be an array")
    result = tuple(
        _safe_id(item, f"{label}[{index}]")
        for index, item in enumerate(value)
    )
    if result != tuple(sorted(result)) or len(set(result)) != len(result):
        raise BaselineEvidenceFormatError(
            f"{label} must be sorted without duplicates"
        )
    return result


def _sha256(value: object, label: str) -> str:
    text = _text(value, label)
    if _SHA256.fullmatch(text) is None:
        raise BaselineEvidenceFormatError(
            f"{label} must be a lowercase SHA-256"
        )
    return text


def _safe_id(value: object, label: str) -> str:
    text = _text(value, label)
    if _SAFE_ID.fullmatch(text) is None:
        raise BaselineEvidenceFormatError(f"{label} is not a safe identifier")
    return text


def _dimension(value: object, label: str) -> str:
    text = _text(value, label)
    if _DIMENSION.fullmatch(text) is None:
        raise BaselineEvidenceFormatError(f"{label} is not a safe dimension")
    return text


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise BaselineEvidenceFormatError(
            f"{label} must be non-empty text without surrounding whitespace"
        )
    return value


def _literal(value: object, expected: str, label: str) -> None:
    if value != expected:
        raise BaselineEvidenceFormatError(f"{label} must be {expected}")


def _reject_save_like_values(value: object, location: str = "evidence") -> None:
    if isinstance(value, str):
        normalized = value.replace("\\", "/")
        parts = [part.casefold() for part in normalized.split("/") if part]
        if "saves" in parts or normalized.casefold().endswith((".ess", ".skse")):
            raise BaselineEvidenceFormatError(
                f"{location} contains a save or co-save reference"
            )
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_save_like_values(item, f"{location}[{index}]")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_save_like_values(item, f"{location}.{key}")
