"""Strict, bounded output contracts for the Skyrim user workflow."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Mapping

from modlab.recipes.model import CheckState

from .drift import SkyrimDriftReport, SkyrimStatusOutcome

if TYPE_CHECKING:
    from .capture import BaselineCaptureResult
    from .service import BaselineUseResult, ConfigureResult


class SkyrimWorkflowFormatError(ValueError):
    """A serialized Skyrim workflow result violates its fixed contract."""


_TOP_LEVEL_COMMON = {
    "schemaVersion",
    "actionsPerformed",
    "downloadsPerformed",
    "installationActionsPerformed",
    "programsLaunched",
}
_STATUS_DOMAINS = {
    "game-identity",
    "manager-identity",
    "lab-profile",
    "play-profile",
    "shared-manager-state",
    "recipe-intent",
    "target-environment",
    "coverage",
}
_COVERAGE_READY = {
    "foundationAssembly": "NotVerified",
    "profileState": "Complete",
    "installedPayloadContent": "NotInspected",
    "artifactCoverage": "NotLinked",
    "assetConflicts": "NotInspected",
    "pluginRecordConflicts": "NotInspected",
    "runtimeValidation": "NotPerformed",
    "smokeTest": "NotPerformed",
}
_COVERAGE_BLOCKED = {**_COVERAGE_READY, "profileState": "NotVerified"}


def configure_result_to_dict(
    result: ConfigureResult, *, workspace_root: Path
) -> dict[str, object]:
    observation = result.observation
    projection = observation.projection
    executable = observation.discovery.executable
    manager_executable = (
        None if projection is None else projection.observation_context.executable
    )
    live_dimensions = dict(observation.live_dimensions)
    findings = [
        {
            "dimension": item.dimension,
            "targetValue": item.target_value,
            "actualValue": item.actual_value,
            "state": item.state.value,
            "source": item.source,
            "message": item.message,
        }
        for item in observation.dimension_findings
    ]
    value: dict[str, object] = {
        "schemaVersion": 1,
        "configure": {
            "outcome": "Configured" if result.changed else "Unchanged",
            "changed": result.changed,
            "game": {
                "steamRoot": observation.discovery.steam_root,
                "gameRoot": observation.discovery.game_root,
                "fileVersion": None if executable is None else executable.file_version,
                "runtime": live_dimensions.get("executableRuntime"),
                "sha256": None if executable is None else executable.sha256,
            },
            "manager": {
                "adapterId": result.configuration.manager.adapter_id,
                "root": (
                    None
                    if projection is None
                    else projection.observation_context.mo2_root
                ),
                "fileVersion": (
                    None
                    if manager_executable is None
                    else manager_executable.file_version
                ),
                "version": live_dimensions.get("adapterVersion"),
                "sha256": (
                    None
                    if manager_executable is None
                    else manager_executable.sha256
                ),
            },
            "recipe": {
                "recipeId": result.configuration.recipe.recipe_id,
                "revision": result.configuration.recipe.revision,
                "maturity": result.configuration.recipe.maturity.value,
                "identity": result.configuration.recipe.identity.value,
                "selected": list(result.configuration.recipe.selected),
                "omitted": list(result.configuration.recipe.omitted),
                "sourceSha256": result.configuration.recipe.source.source_sha256,
                "readyForApproval": result.recipe_review.ready_for_approval,
            },
            "targetEnvironment": {
                "environmentId": result.configuration.target_environment.environment_id,
                "sourceSha256": result.configuration.target_environment.source.source_sha256,
                "dimensions": dict(result.configuration.target_environment.dimensions),
                "liveDimensions": live_dimensions,
                "unverifiedDimensions": [
                    item.dimension
                    for item in observation.dimension_findings
                    if item.state is CheckState.UNKNOWN
                ],
                "findings": findings,
            },
            "baselineCheckpointId": result.configuration.baseline_checkpoint_id,
        },
        "actionsPerformed": list(result.actions_performed),
        "downloadsPerformed": [],
        "installationActionsPerformed": [],
        "programsLaunched": [],
    }
    return configure_result_from_dict(value, workspace_root=workspace_root)


def configure_result_from_dict(
    value: object, *, workspace_root: Path
) -> dict[str, object]:
    root = _command_result(value, "configure", workspace_root)
    summary = _mapping(
        root["configure"],
        {
            "outcome",
            "changed",
            "game",
            "manager",
            "recipe",
            "targetEnvironment",
            "baselineCheckpointId",
        },
        "configure",
    )
    changed = _boolean(summary["changed"], "configure.changed")
    expected_outcome = "Configured" if changed else "Unchanged"
    _literal(summary["outcome"], expected_outcome, "configure.outcome")
    game = _mapping(
        summary["game"],
        {"steamRoot", "gameRoot", "fileVersion", "runtime", "sha256"},
        "configure.game",
    )
    _path_text(game["steamRoot"], "configure.game.steamRoot")
    _path_text(game["gameRoot"], "configure.game.gameRoot")
    _optional_text(game["fileVersion"], "configure.game.fileVersion")
    _optional_text(game["runtime"], "configure.game.runtime")
    _optional_sha256(game["sha256"], "configure.game.sha256")
    manager = _mapping(
        summary["manager"],
        {"adapterId", "root", "fileVersion", "version", "sha256"},
        "configure.manager",
    )
    _literal(manager["adapterId"], "portable-mo2-skyrim", "configure.manager.adapterId")
    _path_text(manager["root"], "configure.manager.root")
    expected_manager_root = (
        Path(workspace_root).resolve()
        / "tools" / "mo2" / "skyrim-se-ae" / "app"
    )
    if Path(manager["root"]).resolve() != expected_manager_root:
        raise SkyrimWorkflowFormatError(
            "configure.manager.root is not the contained Skyrim MO2 app"
        )
    _optional_text(manager["fileVersion"], "configure.manager.fileVersion")
    _optional_text(manager["version"], "configure.manager.version")
    _optional_sha256(manager["sha256"], "configure.manager.sha256")
    recipe = _mapping(
        summary["recipe"],
        {
            "recipeId",
            "revision",
            "maturity",
            "identity",
            "selected",
            "omitted",
            "sourceSha256",
            "readyForApproval",
        },
        "configure.recipe",
    )
    for key in ("recipeId", "revision", "maturity", "identity"):
        _text(recipe[key], f"configure.recipe.{key}")
    _string_list(recipe["selected"], "configure.recipe.selected")
    _string_list(recipe["omitted"], "configure.recipe.omitted")
    _sha256(recipe["sourceSha256"], "configure.recipe.sourceSha256")
    if not _boolean(recipe["readyForApproval"], "configure.recipe.readyForApproval"):
        raise SkyrimWorkflowFormatError("configured recipe must be ready for approval")
    target = _mapping(
        summary["targetEnvironment"],
        {
            "environmentId",
            "sourceSha256",
            "dimensions",
            "liveDimensions",
            "unverifiedDimensions",
            "findings",
        },
        "configure.targetEnvironment",
    )
    _text(target["environmentId"], "configure.targetEnvironment.environmentId")
    target_sha256 = _sha256(
        target["sourceSha256"],
        "configure.targetEnvironment.sourceSha256",
    )
    dimensions = _string_mapping(
        target["dimensions"], "configure.targetEnvironment.dimensions"
    )
    live = _string_mapping(
        target["liveDimensions"], "configure.targetEnvironment.liveDimensions"
    )
    unverified = _string_list(
        target["unverifiedDimensions"],
        "configure.targetEnvironment.unverifiedDimensions",
    )
    if any(item not in dimensions or item in live for item in unverified):
        raise SkyrimWorkflowFormatError(
            "unverified dimensions must be target dimensions without live values"
        )
    finding_values = _list(target["findings"], "configure.targetEnvironment.findings")
    finding_dimensions: list[str] = []
    for index, item in enumerate(finding_values):
        finding = _mapping(
            item,
            {"dimension", "targetValue", "actualValue", "state", "source", "message"},
            f"configure.targetEnvironment.findings[{index}]",
        )
        dimension = _text(finding["dimension"], f"finding[{index}].dimension")
        if dimension in finding_dimensions:
            raise SkyrimWorkflowFormatError("duplicate target-environment finding")
        finding_dimensions.append(dimension)
        _text(finding["targetValue"], f"finding[{index}].targetValue")
        _optional_text(finding["actualValue"], f"finding[{index}].actualValue")
        if finding["state"] not in {item.value for item in CheckState}:
            raise SkyrimWorkflowFormatError("unknown target-environment finding state")
        _text(finding["source"], f"finding[{index}].source")
        _text(finding["message"], f"finding[{index}].message")
    _optional_checkpoint_id(
        summary["baselineCheckpointId"], "configure.baselineCheckpointId"
    )
    workspace = Path(workspace_root).resolve()
    allowed_actions = {
        workspace / "games" / "skyrim-se-ae" / "environment.json",
        workspace / "games" / "skyrim-se-ae" / "recipes"
        / recipe["sourceSha256"] / "recipe.json",
        workspace / "games" / "skyrim-se-ae" / "target-environments"
        / target_sha256 / "environment.json",
    }
    actions = _validated_action_paths(root["actionsPerformed"], allowed_actions)
    configuration_path = workspace / "games" / "skyrim-se-ae" / "environment.json"
    if changed != (configuration_path in actions):
        raise SkyrimWorkflowFormatError(
            "configure.changed must match the configuration-file action"
        )
    return _copy_tree(root)


def configure_result_to_text(
    result: ConfigureResult, *, workspace_root: Path
) -> str:
    value = configure_result_to_dict(result, workspace_root=workspace_root)
    summary = value["configure"]
    game = summary["game"]
    manager = summary["manager"]
    recipe = summary["recipe"]
    target = summary["targetEnvironment"]
    lines = [
        f"{summary['outcome']}: Skyrim SE/AE observed and registered.",
        f"Game: {game['gameRoot']} (runtime {game['runtime'] or 'unknown'}, file {game['fileVersion'] or 'unknown'})",
        f"Manager: {manager['root']} (MO2 {manager['version'] or 'unknown'}, file {manager['fileVersion'] or 'unknown'})",
        f"Recipe: {recipe['recipeId']} @ {recipe['revision']} ({recipe['maturity']}, {recipe['identity']})",
        "Selected: " + _joined(recipe["selected"]),
        "Omitted: " + _joined(recipe["omitted"]),
    ]
    if target["unverifiedDimensions"]:
        lines.append(
            "Unverified targets: "
            + ", ".join(
                f"{name} (target intent only)"
                for name in target["unverifiedDimensions"]
            )
        )
    else:
        lines.append("Unverified targets: (none)")
    lines.extend(_action_lines(value["actionsPerformed"]))
    lines.append("No downloads, installations, or programs were started.")
    return "\n".join(lines) + "\n"


def capture_result_to_dict(
    result: BaselineCaptureResult, *, workspace_root: Path
) -> dict[str, object]:
    checkpoint = result.checkpoint
    value: dict[str, object] = {
        "schemaVersion": 1,
        "baselineCapture": {
            "checkpointId": checkpoint.checkpoint_id,
            "parentCheckpointId": checkpoint.parent_checkpoint_id,
            "selected": result.selected,
            "selectedBaselineCheckpointId": result.configuration.baseline_checkpoint_id,
            "warning": result.warning,
            "qualification": "Observed",
            "promotionReady": False,
            "restorable": False,
            "foundationAssembly": "NotVerified",
            "runtimeValidation": "NotPerformed",
            "smokeTest": "NotPerformed",
            "artifactIds": [],
        },
        "actionsPerformed": [str(path) for path in result.actions_performed],
        "downloadsPerformed": [],
        "installationActionsPerformed": [],
        "programsLaunched": [],
    }
    return capture_result_from_dict(value, workspace_root=workspace_root)


def capture_result_from_dict(
    value: object, *, workspace_root: Path
) -> dict[str, object]:
    root = _command_result(value, "baselineCapture", workspace_root)
    summary = _mapping(
        root["baselineCapture"],
        {
            "checkpointId",
            "parentCheckpointId",
            "selected",
            "selectedBaselineCheckpointId",
            "warning",
            "qualification",
            "promotionReady",
            "restorable",
            "foundationAssembly",
            "runtimeValidation",
            "smokeTest",
            "artifactIds",
        },
        "baselineCapture",
    )
    checkpoint_id = _checkpoint_id(
        summary["checkpointId"], "baselineCapture.checkpointId"
    )
    _optional_checkpoint_id(
        summary["parentCheckpointId"], "baselineCapture.parentCheckpointId"
    )
    selected = _boolean(summary["selected"], "baselineCapture.selected")
    selected_id = _optional_checkpoint_id(
        summary["selectedBaselineCheckpointId"],
        "baselineCapture.selectedBaselineCheckpointId",
    )
    warning = _optional_text(summary["warning"], "baselineCapture.warning")
    if selected and (selected_id != checkpoint_id or warning is not None):
        raise SkyrimWorkflowFormatError(
            "selected capture must select its checkpoint without a warning"
        )
    if not selected and warning is None:
        raise SkyrimWorkflowFormatError(
            "unselected capture must explain the pointer failure"
        )
    _literal(summary["qualification"], "Observed", "baselineCapture.qualification")
    if _boolean(summary["promotionReady"], "baselineCapture.promotionReady"):
        raise SkyrimWorkflowFormatError("observed baseline cannot be promotion-ready")
    if _boolean(summary["restorable"], "baselineCapture.restorable"):
        raise SkyrimWorkflowFormatError("observed baseline cannot be restorable")
    _literal(summary["foundationAssembly"], "NotVerified", "baselineCapture.foundationAssembly")
    _literal(summary["runtimeValidation"], "NotPerformed", "baselineCapture.runtimeValidation")
    _literal(summary["smokeTest"], "NotPerformed", "baselineCapture.smokeTest")
    if summary["artifactIds"] != []:
        raise SkyrimWorkflowFormatError("observed baseline cannot link artifacts")
    workspace = Path(workspace_root).resolve()
    configuration_path = workspace / "games" / "skyrim-se-ae" / "environment.json"
    checkpoint_path = (
        workspace / "games" / "skyrim-se-ae" / "checkpoints"
        / checkpoint_id.removeprefix("checkpoint-sha256:")
        / "modlab.lock.json"
    )
    actions = _validated_action_paths(
        root["actionsPerformed"], {configuration_path, checkpoint_path}
    )
    if not selected and configuration_path in actions:
        raise SkyrimWorkflowFormatError(
            "unselected capture cannot report a configuration-pointer write"
        )
    if selected and actions and configuration_path not in actions:
        raise SkyrimWorkflowFormatError(
            "a changed selected capture must report its pointer write"
        )
    return _copy_tree(root)


def capture_result_to_text(
    result: BaselineCaptureResult, *, workspace_root: Path
) -> str:
    value = capture_result_to_dict(result, workspace_root=workspace_root)
    summary = value["baselineCapture"]
    lines = [
        f"Observed baseline captured: {summary['checkpointId']}",
        f"Parent: {summary['parentCheckpointId'] or '(none)'}",
        f"Selected: {'yes' if summary['selected'] else 'no'}",
        "Scope: observed profile state only; this is not a playable-build approval.",
        "Not verified: foundation assembly, installed payload content, asset conflicts, plugin record conflicts, runtime validation, and smoke testing.",
        "Restorable: no; promotion-ready: no; linked artifacts: none.",
    ]
    if summary["warning"] is not None:
        lines.append(f"Warning: {summary['warning']}")
    lines.extend(_action_lines(value["actionsPerformed"]))
    lines.append("No downloads, installations, programs, restoration, or promotion were started.")
    return "\n".join(lines) + "\n"


def use_result_to_dict(
    result: BaselineUseResult, *, workspace_root: Path
) -> dict[str, object]:
    value: dict[str, object] = {
        "schemaVersion": 1,
        "baselineUse": {
            "checkpointId": result.checkpoint_id,
            "selectedBaselineCheckpointId": result.configuration.baseline_checkpoint_id,
            "changed": result.changed,
        },
        "actionsPerformed": list(result.actions_performed),
        "downloadsPerformed": [],
        "installationActionsPerformed": [],
        "programsLaunched": [],
    }
    return use_result_from_dict(value, workspace_root=workspace_root)


def use_result_from_dict(
    value: object, *, workspace_root: Path
) -> dict[str, object]:
    root = _command_result(value, "baselineUse", workspace_root)
    summary = _mapping(
        root["baselineUse"],
        {"checkpointId", "selectedBaselineCheckpointId", "changed"},
        "baselineUse",
    )
    checkpoint_id = _checkpoint_id(summary["checkpointId"], "baselineUse.checkpointId")
    selected = _checkpoint_id(
        summary["selectedBaselineCheckpointId"],
        "baselineUse.selectedBaselineCheckpointId",
    )
    if checkpoint_id != selected:
        raise SkyrimWorkflowFormatError("baseline use pointer does not match checkpoint")
    changed = _boolean(summary["changed"], "baselineUse.changed")
    workspace = Path(workspace_root).resolve()
    configuration_path = workspace / "games" / "skyrim-se-ae" / "environment.json"
    actions = _validated_action_paths(
        root["actionsPerformed"], {configuration_path}
    )
    if changed != (configuration_path in actions):
        raise SkyrimWorkflowFormatError(
            "baselineUse.changed must match the configuration-pointer action"
        )
    return _copy_tree(root)


def use_result_to_text(
    result: BaselineUseResult, *, workspace_root: Path
) -> str:
    value = use_result_to_dict(result, workspace_root=workspace_root)
    summary = value["baselineUse"]
    lines = [
        f"Selected observed baseline: {summary['checkpointId']}",
        f"Configuration pointer changed: {'yes' if summary['changed'] else 'no'}",
    ]
    lines.extend(_action_lines(value["actionsPerformed"]))
    lines.append("No game or manager files were restored, repaired, installed, or launched.")
    return "\n".join(lines) + "\n"


def status_result_to_dict(result: SkyrimDriftReport) -> dict[str, object]:
    value: dict[str, object] = {
        "schemaVersion": 1,
        "status": {
            "outcome": result.outcome.value,
            "baselineCheckpointId": result.baseline_checkpoint_id,
            "domains": [
                {
                    "name": domain.name,
                    "changes": [
                        {
                            "field": change.field,
                            "checkpointValue": _json_scalar(change.checkpoint_value),
                            "currentValue": _json_scalar(change.current_value),
                        }
                        for change in domain.changes
                    ],
                }
                for domain in result.domains
            ],
            "findings": list(result.findings),
            "coverage": dict(
                _COVERAGE_BLOCKED
                if result.outcome is SkyrimStatusOutcome.BLOCKED
                else _COVERAGE_READY
            ),
        },
        "actionsPerformed": [],
        "downloadsPerformed": [],
        "installationActionsPerformed": [],
        "programsLaunched": [],
    }
    return status_result_from_dict(value)


def status_result_from_dict(value: object) -> dict[str, object]:
    root = _command_result(value, "status", None)
    if root["actionsPerformed"] != []:
        raise SkyrimWorkflowFormatError("status must be read-only")
    summary = _mapping(
        root["status"],
        {"outcome", "baselineCheckpointId", "domains", "findings", "coverage"},
        "status",
    )
    try:
        outcome = SkyrimStatusOutcome(summary["outcome"])
    except (TypeError, ValueError) as error:
        raise SkyrimWorkflowFormatError("status.outcome is unknown") from error
    baseline_id = _optional_checkpoint_id(
        summary["baselineCheckpointId"], "status.baselineCheckpointId"
    )
    if outcome in {SkyrimStatusOutcome.MATCHED, SkyrimStatusOutcome.DRIFTED} and baseline_id is None:
        raise SkyrimWorkflowFormatError("matched or drifted status requires a baseline")
    if outcome is SkyrimStatusOutcome.NO_BASELINE and baseline_id is not None:
        raise SkyrimWorkflowFormatError("NoBaseline status cannot name a baseline")

    domains = _list(summary["domains"], "status.domains")
    seen_domains: set[str] = set()
    for domain_index, item in enumerate(domains):
        domain = _mapping(item, {"name", "changes"}, f"status.domains[{domain_index}]")
        name = _text(domain["name"], f"status.domains[{domain_index}].name")
        if name not in _STATUS_DOMAINS or name in seen_domains:
            raise SkyrimWorkflowFormatError("status domains must be known and unique")
        seen_domains.add(name)
        changes = _list(domain["changes"], f"status.domains[{domain_index}].changes")
        if not changes or len(changes) > 64:
            raise SkyrimWorkflowFormatError("each drift domain needs 1 to 64 changes")
        seen_fields: set[str] = set()
        for change_index, item in enumerate(changes):
            change = _mapping(
                item,
                {"field", "checkpointValue", "currentValue"},
                f"status.domains[{domain_index}].changes[{change_index}]",
            )
            field = _text(change["field"], "status drift field")
            if field in seen_fields:
                raise SkyrimWorkflowFormatError("drift fields must be unique within a domain")
            seen_fields.add(field)
            _drift_scalar(change["checkpointValue"], "checkpointValue")
            _drift_scalar(change["currentValue"], "currentValue")
    if outcome is SkyrimStatusOutcome.DRIFTED and not domains:
        raise SkyrimWorkflowFormatError("Drifted status requires domains")
    if outcome is not SkyrimStatusOutcome.DRIFTED and domains:
        raise SkyrimWorkflowFormatError("only Drifted status may contain domains")
    findings = _string_list(summary["findings"], "status.findings")
    if outcome in {SkyrimStatusOutcome.MATCHED, SkyrimStatusOutcome.DRIFTED} and findings:
        raise SkyrimWorkflowFormatError("matched or drifted status cannot contain refusal findings")
    if outcome in {SkyrimStatusOutcome.NO_BASELINE, SkyrimStatusOutcome.BLOCKED} and not findings:
        raise SkyrimWorkflowFormatError("NoBaseline or Blocked status needs a finding")
    expected_coverage = (
        _COVERAGE_BLOCKED
        if outcome is SkyrimStatusOutcome.BLOCKED
        else _COVERAGE_READY
    )
    coverage = _mapping(
        summary["coverage"], set(expected_coverage), "status.coverage"
    )
    if dict(coverage) != expected_coverage:
        raise SkyrimWorkflowFormatError("status coverage claims exceed the supported contract")
    return _copy_tree(root)


def status_result_to_text(result: SkyrimDriftReport) -> str:
    value = status_result_to_dict(result)
    summary = value["status"]
    lines = [
        f"{summary['outcome']}: Skyrim observed-baseline status.",
        f"Baseline: {summary['baselineCheckpointId'] or '(none)'}",
    ]
    if summary["domains"]:
        lines.append("Drift domains:")
        for domain in summary["domains"]:
            lines.append(f"  - {domain['name']}")
            for change in domain["changes"]:
                lines.append(
                    f"    {change['field']}: checkpoint={_display(change['checkpointValue'])}; current={_display(change['currentValue'])}"
                )
    else:
        lines.append("Drift domains: (none)")
    if summary["findings"]:
        lines.append("Findings:")
        lines.extend(f"  - {finding}" for finding in summary["findings"])
    lines.append(
        f"Coverage: profile state {summary['coverage']['profileState']}; payload content, asset conflicts, and plugin record conflicts NotInspected; runtime and smoke tests NotPerformed."
    )
    lines.append(
        "Nothing was written, launched, installed, repaired, restored, or promoted."
    )
    return "\n".join(lines) + "\n"


def _command_result(
    value: object, command_field: str, workspace_root: Path | None
) -> Mapping[str, object]:
    root = _mapping(value, _TOP_LEVEL_COMMON | {command_field}, "workflow result")
    if type(root["schemaVersion"]) is not int or root["schemaVersion"] != 1:
        raise SkyrimWorkflowFormatError("schemaVersion must be integer 1")
    for field in (
        "downloadsPerformed",
        "installationActionsPerformed",
        "programsLaunched",
    ):
        if root[field] != []:
            raise SkyrimWorkflowFormatError(f"{field} must be empty")
    actions = _string_list(root["actionsPerformed"], "actionsPerformed")
    if workspace_root is not None:
        workspace = Path(workspace_root).resolve()
        for action in actions:
            path = Path(action)
            if not path.is_absolute():
                raise SkyrimWorkflowFormatError("action paths must be absolute")
            try:
                relative = path.resolve().relative_to(workspace)
            except (OSError, ValueError) as error:
                raise SkyrimWorkflowFormatError(
                    "action path is outside the ModLab workspace"
                ) from error
            if not relative.parts:
                raise SkyrimWorkflowFormatError("workspace root is not a file action")
    return root


def _validated_action_paths(
    values: object, allowed: set[Path]
) -> set[Path]:
    actions = _string_list(values, "actionsPerformed")
    resolved = {Path(action).resolve() for action in actions}
    expected = {path.resolve() for path in allowed}
    if not resolved.issubset(expected):
        raise SkyrimWorkflowFormatError(
            "actionsPerformed contains a path this command cannot write"
        )
    return resolved


def _mapping(value: object, expected: set[str], label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise SkyrimWorkflowFormatError(f"{label} must be an object")
    fields = set(value)
    if fields != expected:
        missing = sorted(expected - fields)
        unknown = sorted(fields - expected)
        raise SkyrimWorkflowFormatError(
            f"{label} fields differ; missing={missing}; unknown={unknown}"
        )
    return value


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise SkyrimWorkflowFormatError(f"{label} must be an array")
    return value


def _string_list(value: object, label: str) -> list[str]:
    values = _list(value, label)
    result = [_text(item, f"{label} item") for item in values]
    if len(set(result)) != len(result):
        raise SkyrimWorkflowFormatError(f"{label} cannot contain duplicates")
    return result


def _string_mapping(value: object, label: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise SkyrimWorkflowFormatError(f"{label} must be an object")
    result: dict[str, str] = {}
    for key, item in value.items():
        result[_text(key, f"{label} key")] = _text(item, f"{label}.{key}")
    return result


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\n" in value or "\r" in value:
        raise SkyrimWorkflowFormatError(f"{label} must be non-empty single-line text")
    return value


def _path_text(value: object, label: str) -> str:
    result = _text(value, label)
    if not Path(result).is_absolute():
        raise SkyrimWorkflowFormatError(f"{label} must be an absolute path")
    return result


def _optional_text(value: object, label: str) -> str | None:
    return None if value is None else _text(value, label)


def _boolean(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise SkyrimWorkflowFormatError(f"{label} must be a boolean")
    return value


def _literal(value: object, expected: str, label: str) -> None:
    if value != expected:
        raise SkyrimWorkflowFormatError(f"{label} must be {expected!r}")


def _sha256(value: object, label: str) -> str:
    result = _text(value, label)
    if len(result) != 64 or any(character not in "0123456789abcdef" for character in result):
        raise SkyrimWorkflowFormatError(f"{label} must be lowercase SHA-256")
    return result


def _optional_sha256(value: object, label: str) -> str | None:
    return None if value is None else _sha256(value, label)


def _checkpoint_id(value: object, label: str) -> str:
    result = _text(value, label)
    prefix = "checkpoint-sha256:"
    if not result.startswith(prefix):
        raise SkyrimWorkflowFormatError(f"{label} must be a checkpoint identity")
    _sha256(result.removeprefix(prefix), label)
    return result


def _optional_checkpoint_id(value: object, label: str) -> str | None:
    return None if value is None else _checkpoint_id(value, label)


def _json_scalar(value):
    return list(value) if isinstance(value, tuple) else value


def _drift_scalar(value: object, label: str) -> None:
    if value is None or type(value) in {str, bool, int}:
        if isinstance(value, str):
            _text(value, label)
        return
    if isinstance(value, list):
        if len(value) > 9:
            raise SkyrimWorkflowFormatError(
                "drift sequence evidence is limited to metadata plus a seven-item window"
            )
        _string_list(value, label)
        return
    raise SkyrimWorkflowFormatError(f"{label} has an unsupported drift value")


def _copy_tree(value):
    if isinstance(value, dict):
        return {key: _copy_tree(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_tree(item) for item in value]
    return value


def _joined(values: list[str]) -> str:
    return ", ".join(values) if values else "(none)"


def _action_lines(actions: list[str]) -> list[str]:
    if not actions:
        return ["Exact ModLab files written: (none)"]
    return ["Exact ModLab files written:", *(f"  - {path}" for path in actions)]


def _display(value: object) -> str:
    if value is None:
        return "(none)"
    if isinstance(value, list):
        return "[" + ", ".join(value) + "]"
    return str(value)
