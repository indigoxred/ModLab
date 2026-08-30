"""Strict canonical JSON and bounded text for MO2 comparison results."""

import json
from pathlib import PurePosixPath, PureWindowsPath
import re
from typing import Mapping

from modlab.recipes.model import CheckState

from .comparison import (
    Mo2ComparisonReport,
    Mo2ConfigurationDifferences,
    Mo2Differences,
    Mo2EntryChange,
    Mo2EntryPosition,
    Mo2OrderDifference,
    Mo2SharedStateObservations,
    _compare_mods,
    _compare_plugins,
    _configuration_differences,
)
from .model import (
    Mo2ExecutableEvidence,
    Mo2Finding,
    Mo2PathEvidence,
    Mo2StateFileEvidence,
)
from .projection import (
    Mo2AdapterState,
    Mo2Capability,
    Mo2Coverage,
    Mo2InstalledModState,
    Mo2ObservationContext,
    Mo2ProfileState,
    Mo2ProjectedMod,
    Mo2ProjectedPlugin,
    Mo2Readiness,
    adapter_state_sha256,
    adapter_state_to_dict,
)


class Mo2ComparisonFormatError(ValueError):
    """Raised when a comparison payload is ambiguous or internally inconsistent."""


_TOP_FIELDS = {
    "schemaVersion",
    "managerComparison",
    "actionsPerformed",
    "downloadsPerformed",
    "installationActionsPerformed",
    "programsLaunched",
}
_MANAGER_FIELDS = {
    "schemaVersion",
    "adapterId",
    "readiness",
    "direction",
    "observationContext",
    "capabilities",
    "adapterState",
    "adapterStateSha256",
    "differences",
    "observations",
    "coverage",
    "findings",
}
_CONTEXT_FIELDS = {
    "mo2Root",
    "gameRoot",
    "activeProfile",
    "executable",
    "configuredPaths",
    "readSetSha256",
    "readSetStable",
}
_EXECUTABLE_FIELDS = {"relativePath", "fileVersion", "sha256", "size"}
_PATH_FIELDS = {"kind", "configuredPath", "resolvedPath", "contained"}
_CAPABILITY_FIELDS = {"name", "status", "detail"}
_STATE_FIELDS = {
    "schemaVersion",
    "adapterId",
    "lab",
    "play",
    "installedMods",
    "overwriteEntries",
}
_PROFILE_FIELDS = {
    "name",
    "profileLocalSaves",
    "profileLocalSettings",
    "mods",
    "plugins",
    "effectiveLoadOrder",
    "stateFiles",
}
_MOD_FIELDS = {
    "name",
    "kind",
    "marker",
    "enabled",
    "listIndex",
    "runtimePriority",
}
_PLUGIN_FIELDS = {
    "name",
    "primary",
    "enabled",
    "loadOrderIndex",
}
_STATE_FILE_FIELDS = {"relativePath", "sha256", "size"}
_INSTALLED_MOD_FIELDS = {"name", "metaIni"}
_DIFFERENCES_FIELDS = {
    "modChanges",
    "modOrder",
    "pluginChanges",
    "pluginOrder",
    "configuration",
}
_CHANGE_FIELDS = {"name", "entryKind", "change", "playPosition", "labPosition"}
_POSITION_FIELDS = {
    "sequenceIndex",
    "runtimePriority",
    "previousSharedAnchor",
    "nextSharedAnchor",
}
_ORDER_FIELDS = {
    "changed",
    "differingPositionCount",
    "firstDivergence",
    "playSequence",
    "labSequence",
}
_CONFIGURATION_FIELDS = {"labOnly", "playOnly", "contentChanged"}
_OBSERVATION_FIELDS = {
    "overwriteEntries",
    "installedModFolders",
    "installedPayloadContent",
}
_COVERAGE_FIELDS = {
    "profileState",
    "installedPayloadContent",
    "assetConflicts",
    "pluginRecordConflicts",
    "runtimeValidation",
}
_FINDING_FIELDS = {"state", "code", "message"}
_CAPABILITY_NAMES = (
    "scanner-safety",
    "configuration",
    "game-match",
    "contained-paths",
    "exact-profiles",
    "required-profile-files",
    "primary-plugin-policy",
    "plugin-order-coherence",
    "installed-inventory",
    "overwrite-inventory",
    "stable-read-set",
    "read-only-side-effects",
)
_PATH_KINDS = {"base", "downloads", "mods", "profiles", "overwrite"}
_MOD_KINDS = {"managed", "foreign", "separator"}
_ENTRY_KINDS = _MOD_KINDS | {"plugin"}
_CHANGE_VALUES = {
    "added-in-lab",
    "removed-from-lab",
    "enabled-in-lab",
    "disabled-in-lab",
}
_PLUGIN_EXTENSIONS = {".esm", ".esl", ".esp"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CODE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def comparison_result_to_dict(report: Mo2ComparisonReport) -> dict[str, object]:
    mapping = {
        "schemaVersion": 1,
        "managerComparison": _manager_to_dict(report),
        "actionsPerformed": [],
        "downloadsPerformed": [],
        "installationActionsPerformed": [],
        "programsLaunched": [],
    }
    comparison_result_from_dict(mapping)
    return mapping


def comparison_result_from_dict(data: object) -> Mo2ComparisonReport:
    root = _exact_mapping(data, _TOP_FIELDS, "comparison result")
    _schema(root["schemaVersion"], "comparison result schemaVersion")
    for field in (
        "actionsPerformed",
        "downloadsPerformed",
        "installationActionsPerformed",
        "programsLaunched",
    ):
        if root[field] != []:
            raise Mo2ComparisonFormatError(f"{field} must be an explicit empty array")

    manager = _exact_mapping(
        root["managerComparison"], _MANAGER_FIELDS, "managerComparison"
    )
    _schema(manager["schemaVersion"], "managerComparison.schemaVersion")
    if manager["adapterId"] != "portable-mo2-skyrim":
        raise Mo2ComparisonFormatError("adapterId must be portable-mo2-skyrim")
    try:
        readiness = Mo2Readiness(manager["readiness"])
    except (TypeError, ValueError) as error:
        raise Mo2ComparisonFormatError("readiness must be Ready or Blocked") from error
    if manager["direction"] != "Play-to-Lab":
        raise Mo2ComparisonFormatError("direction must be Play-to-Lab")

    context = _context(manager["observationContext"])
    capabilities = _capabilities(manager["capabilities"])
    adapter_state = (
        None if manager["adapterState"] is None else _adapter_state(manager["adapterState"])
    )
    adapter_hash = _optional_sha256(
        manager["adapterStateSha256"], "adapterStateSha256"
    )
    differences = (
        None if manager["differences"] is None else _differences(manager["differences"])
    )
    observations = _observations(manager["observations"])
    coverage = _coverage(manager["coverage"])
    findings = _findings(manager["findings"])

    if readiness is Mo2Readiness.READY:
        if adapter_state is None or differences is None or adapter_hash is None:
            raise Mo2ComparisonFormatError(
                "Ready comparison requires adapterState, adapterStateSha256, and differences"
            )
        if any(item.status != "complete" for item in capabilities):
            raise Mo2ComparisonFormatError("Ready comparison requires complete capabilities")
        if adapter_state_sha256(adapter_state) != adapter_hash:
            raise Mo2ComparisonFormatError(
                "adapterStateSha256 does not match adapterState"
            )
        if coverage.profile_state != "complete":
            raise Mo2ComparisonFormatError(
                "Ready comparison requires complete profileState"
            )
        expected_mod_changes, expected_mod_order = _compare_mods(adapter_state)
        expected_plugin_changes, expected_plugin_order = _compare_plugins(adapter_state)
        expected = Mo2Differences(
            expected_mod_changes,
            expected_mod_order,
            expected_plugin_changes,
            expected_plugin_order,
            _configuration_differences(
                adapter_state.play.state_files,
                adapter_state.lab.state_files,
            ),
        )
        if differences != expected:
            raise Mo2ComparisonFormatError(
                "differences do not match the referenced adapterState"
            )
        if observations.overwrite_entries != adapter_state.overwrite_entries:
            raise Mo2ComparisonFormatError(
                "observed overwrite entries do not match adapterState"
            )
        if observations.installed_mod_folders != tuple(
            item.name for item in adapter_state.installed_mods
        ):
            raise Mo2ComparisonFormatError(
                "observed installed folders do not match adapterState"
            )
    else:
        if any(
            value is not None
            for value in (adapter_state, adapter_hash, differences)
        ):
            raise Mo2ComparisonFormatError(
                "Blocked comparison requires null state, hash, and differences"
            )
        if coverage.profile_state != "incomplete":
            raise Mo2ComparisonFormatError(
                "Blocked comparison requires incomplete profileState"
            )
        if all(item.status == "complete" for item in capabilities):
            raise Mo2ComparisonFormatError(
                "Blocked comparison requires an incomplete capability"
            )
        if observations.overwrite_entries or observations.installed_mod_folders:
            raise Mo2ComparisonFormatError(
                "Blocked comparison cannot expose unavailable shared state"
            )

    return Mo2ComparisonReport(
        schema_version=1,
        adapter_id="portable-mo2-skyrim",
        readiness=readiness,
        direction="Play-to-Lab",
        observation_context=context,
        capabilities=capabilities,
        adapter_state=adapter_state,
        adapter_state_sha256=adapter_hash,
        differences=differences,
        observations=observations,
        coverage=coverage,
        findings=findings,
    )


def comparison_result_to_json(report: Mo2ComparisonReport) -> str:
    return json.dumps(
        comparison_result_to_dict(report),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def comparison_result_from_json(text: str) -> Mo2ComparisonReport:
    try:
        data = json.loads(text, object_pairs_hook=_unique_object)
    except Mo2ComparisonFormatError:
        raise
    except (TypeError, json.JSONDecodeError) as error:
        raise Mo2ComparisonFormatError("comparison result must be valid JSON") from error
    return comparison_result_from_dict(data)


def comparison_result_to_text(report: Mo2ComparisonReport) -> str:
    validated = comparison_result_from_dict(comparison_result_to_dict(report))
    lines = [
        (
            "Ready: all required MO2 profile-state evidence was compared."
            if validated.readiness is Mo2Readiness.READY
            else "Blocked: required MO2 profile-state evidence is incomplete or incoherent."
        ),
        "Direction: Play-to-Lab",
    ]
    differences = validated.differences
    if differences is not None:
        lines.append(f"Mod changes: {len(differences.mod_changes)}")
        for item in differences.mod_changes:
            lines.append(f"  {item.change}: {item.name}")
        lines.append(f"Plug-in changes: {len(differences.plugin_changes)}")
        for item in differences.plugin_changes:
            lines.append(f"  {item.change}: {item.name}")
        _append_order_text(lines, "Mod", differences.mod_order)
        _append_order_text(lines, "Plug-in", differences.plugin_order)
        configuration_count = (
            len(differences.configuration.lab_only)
            + len(differences.configuration.play_only)
            + len(differences.configuration.content_changed)
        )
        lines.append(f"Profile configuration changes: {configuration_count}")
        no_difference = _no_in_scope_difference(differences)
        if no_difference:
            lines.append("No in-scope profile-state differences were found.")
            if validated.observations.installed_mod_folders:
                lines.append(
                    "Installed mod payload contents were not fingerprinted by this command."
                )
                lines.append(
                    "Lab and Play may still be affected by changes to their shared mod directories."
                )

    if validated.observations.installed_mod_folders:
        lines.append(
            "Shared installed mod folders: "
            + ", ".join(validated.observations.installed_mod_folders)
        )
    else:
        lines.append("Shared installed mod folders: none observed")
    if validated.observations.overwrite_entries:
        lines.append(
            "Overwrite entries: "
            + ", ".join(validated.observations.overwrite_entries)
        )
    else:
        lines.append("Overwrite entries: none observed")
    lines.extend(
        (
            f"Coverage: profile state is {validated.coverage.profile_state}.",
            "Coverage limit: installed payload content, asset conflicts, and plug-in record conflicts were not inspected.",
            "Coverage limit: runtime validation was not performed.",
        )
    )
    if validated.readiness is Mo2Readiness.BLOCKED:
        lines.append("Blocking findings:")
        for item in validated.findings:
            if item.state is CheckState.BLOCKED:
                lines.append(f"  {item.code}: {item.message}")
    lines.append(
        "Nothing was launched, changed, installed, repaired, promoted, or downloaded."
    )
    return "\n".join(lines) + "\n"


def _manager_to_dict(report: Mo2ComparisonReport) -> dict[str, object]:
    context = report.observation_context
    return {
        "schemaVersion": report.schema_version,
        "adapterId": report.adapter_id,
        "readiness": report.readiness.value,
        "direction": report.direction,
        "observationContext": {
            "mo2Root": context.mo2_root,
            "gameRoot": context.game_root,
            "activeProfile": context.active_profile,
            "executable": (
                None
                if context.executable is None
                else {
                    "relativePath": context.executable.relative_path,
                    "fileVersion": context.executable.file_version,
                    "sha256": context.executable.sha256,
                    "size": context.executable.size,
                }
            ),
            "configuredPaths": [
                {
                    "kind": item.kind,
                    "configuredPath": item.configured_path,
                    "resolvedPath": item.resolved_path,
                    "contained": item.contained,
                }
                for item in context.configured_paths
            ],
            "readSetSha256": context.read_set_sha256,
            "readSetStable": context.read_set_stable,
        },
        "capabilities": [
            {"name": item.name, "status": item.status, "detail": item.detail}
            for item in report.capabilities
        ],
        "adapterState": (
            None
            if report.adapter_state is None
            else adapter_state_to_dict(report.adapter_state)
        ),
        "adapterStateSha256": report.adapter_state_sha256,
        "differences": (
            None if report.differences is None else _differences_to_dict(report.differences)
        ),
        "observations": {
            "overwriteEntries": list(report.observations.overwrite_entries),
            "installedModFolders": list(report.observations.installed_mod_folders),
            "installedPayloadContent": report.observations.installed_payload_content,
        },
        "coverage": {
            "profileState": report.coverage.profile_state,
            "installedPayloadContent": report.coverage.installed_payload_content,
            "assetConflicts": report.coverage.asset_conflicts,
            "pluginRecordConflicts": report.coverage.plugin_record_conflicts,
            "runtimeValidation": report.coverage.runtime_validation,
        },
        "findings": [
            {
                "state": item.state.value,
                "code": item.code,
                "message": item.message,
            }
            for item in report.findings
        ],
    }


def _differences_to_dict(value: Mo2Differences) -> dict[str, object]:
    def position(item: Mo2EntryPosition | None):
        if item is None:
            return None
        return {
            "sequenceIndex": item.sequence_index,
            "runtimePriority": item.runtime_priority,
            "previousSharedAnchor": item.previous_shared_anchor,
            "nextSharedAnchor": item.next_shared_anchor,
        }

    def change(item: Mo2EntryChange):
        return {
            "name": item.name,
            "entryKind": item.entry_kind,
            "change": item.change,
            "playPosition": position(item.play_position),
            "labPosition": position(item.lab_position),
        }

    def order(item: Mo2OrderDifference):
        return {
            "changed": item.changed,
            "differingPositionCount": item.differing_position_count,
            "firstDivergence": item.first_divergence,
            "playSequence": list(item.play_sequence),
            "labSequence": list(item.lab_sequence),
        }

    return {
        "modChanges": [change(item) for item in value.mod_changes],
        "modOrder": order(value.mod_order),
        "pluginChanges": [change(item) for item in value.plugin_changes],
        "pluginOrder": order(value.plugin_order),
        "configuration": {
            "labOnly": list(value.configuration.lab_only),
            "playOnly": list(value.configuration.play_only),
            "contentChanged": list(value.configuration.content_changed),
        },
    }


def _context(value: object) -> Mo2ObservationContext:
    mapping = _exact_mapping(value, _CONTEXT_FIELDS, "observationContext")
    executable = None if mapping["executable"] is None else _executable(mapping["executable"])
    paths = _array(mapping["configuredPaths"], _path, "configuredPaths")
    _unique((item.kind for item in paths), "configuredPaths contain duplicate kinds")
    active = mapping["activeProfile"]
    if active is not None:
        active = _name(active, "activeProfile")
    return Mo2ObservationContext(
        mo2_root=_absolute_path(mapping["mo2Root"], "mo2Root"),
        game_root=_absolute_path(mapping["gameRoot"], "gameRoot"),
        active_profile=active,
        executable=executable,
        configured_paths=paths,
        read_set_sha256=_optional_sha256(mapping["readSetSha256"], "readSetSha256"),
        read_set_stable=_bool(mapping["readSetStable"], "readSetStable"),
    )


def _executable(value: object) -> Mo2ExecutableEvidence:
    mapping = _exact_mapping(value, _EXECUTABLE_FIELDS, "executable")
    if mapping["relativePath"] != "ModOrganizer.exe":
        raise Mo2ComparisonFormatError("executable must identify ModOrganizer.exe")
    version = mapping["fileVersion"]
    if version is not None:
        version = _text(version, "fileVersion")
    return Mo2ExecutableEvidence(
        "ModOrganizer.exe",
        version,
        _sha256(mapping["sha256"], "executable.sha256"),
        _non_negative_int(mapping["size"], "executable.size"),
    )


def _path(value: object) -> Mo2PathEvidence:
    mapping = _exact_mapping(value, _PATH_FIELDS, "configured path")
    kind = mapping["kind"]
    if kind not in _PATH_KINDS:
        raise Mo2ComparisonFormatError("configured path kind is invalid")
    return Mo2PathEvidence(
        kind,
        _text(mapping["configuredPath"], "configuredPath"),
        _absolute_path(mapping["resolvedPath"], "resolvedPath"),
        _bool(mapping["contained"], "contained"),
    )


def _capabilities(value: object) -> tuple[Mo2Capability, ...]:
    items = _array(value, _capability, "capabilities")
    if tuple(item.name for item in items) != _CAPABILITY_NAMES:
        raise Mo2ComparisonFormatError(
            "capabilities must contain all twelve names exactly once in canonical order"
        )
    return items


def _capability(value: object) -> Mo2Capability:
    mapping = _exact_mapping(value, _CAPABILITY_FIELDS, "capability")
    name = _text(mapping["name"], "capability.name")
    status = mapping["status"]
    if status not in {"complete", "incomplete"}:
        raise Mo2ComparisonFormatError("capability.status is invalid")
    return Mo2Capability(name, status, _text(mapping["detail"], "capability.detail"))


def _adapter_state(value: object) -> Mo2AdapterState:
    mapping = _exact_mapping(value, _STATE_FIELDS, "adapterState")
    _schema(mapping["schemaVersion"], "adapterState.schemaVersion")
    if mapping["adapterId"] != "portable-mo2-skyrim":
        raise Mo2ComparisonFormatError("adapterState.adapterId is invalid")
    lab = _profile(mapping["lab"], "ModLab - Lab")
    play = _profile(mapping["play"], "ModLab - Play")
    installed = _array(mapping["installedMods"], _installed_mod, "installedMods")
    _unique(
        (item.name.casefold() for item in installed),
        "installedMods contain duplicate names",
    )
    overwrite = _name_array(mapping["overwriteEntries"], "overwriteEntries")
    return Mo2AdapterState(1, "portable-mo2-skyrim", lab, play, installed, overwrite)


def _profile(value: object, expected_name: str) -> Mo2ProfileState:
    mapping = _exact_mapping(value, _PROFILE_FIELDS, "profile state")
    if mapping["name"] != expected_name:
        raise Mo2ComparisonFormatError(f"profile name must be {expected_name}")
    mods = _array(mapping["mods"], _mod, "profile.mods")
    _unique((item.name.casefold() for item in mods), "mods contain duplicate names")
    runtime = 0
    for index, item in enumerate(mods):
        if item.list_index != index:
            raise Mo2ComparisonFormatError("mod listIndex must be contiguous")
        if item.kind == "separator":
            if item.runtime_priority is not None:
                raise Mo2ComparisonFormatError("separator runtimePriority must be null")
        else:
            if item.runtime_priority != runtime:
                raise Mo2ComparisonFormatError("mod runtimePriority must be contiguous")
            runtime += 1
    plugins = _array(mapping["plugins"], _plugin, "profile.plugins")
    _unique(
        (item.name.casefold() for item in plugins),
        "plugins contain duplicate names",
    )
    if any(item.load_order_index != index for index, item in enumerate(plugins)):
        raise Mo2ComparisonFormatError("plugin loadOrderIndex must be contiguous")
    effective = _name_array(
        mapping["effectiveLoadOrder"], "effectiveLoadOrder", plugin=True
    )
    if effective != tuple(item.name for item in plugins if item.enabled):
        raise Mo2ComparisonFormatError(
            "effectiveLoadOrder must contain exactly the enabled plug-ins"
        )
    state_files = _array(mapping["stateFiles"], _state_file, "stateFiles")
    _unique(
        (item.relative_path.casefold() for item in state_files),
        "stateFiles contain duplicate paths",
    )
    prefix = f"profiles/{expected_name}/".casefold()
    if any(not item.relative_path.casefold().startswith(prefix) for item in state_files):
        raise Mo2ComparisonFormatError("stateFiles escape their profile")
    return Mo2ProfileState(
        expected_name,
        _bool(mapping["profileLocalSaves"], "profileLocalSaves"),
        _bool(mapping["profileLocalSettings"], "profileLocalSettings"),
        mods,
        plugins,
        effective,
        state_files,
    )


def _mod(value: object) -> Mo2ProjectedMod:
    mapping = _exact_mapping(value, _MOD_FIELDS, "projected mod")
    name = _name(mapping["name"], "mod.name")
    kind = mapping["kind"]
    if kind not in _MOD_KINDS:
        raise Mo2ComparisonFormatError("mod.kind is invalid")
    marker = mapping["marker"]
    enabled = _bool(mapping["enabled"], "mod.enabled")
    if marker not in {"+", "-", "*"} or enabled != (marker in {"+", "*"}):
        raise Mo2ComparisonFormatError("mod marker and enabled state disagree")
    if kind == "foreign" and marker != "*":
        raise Mo2ComparisonFormatError("foreign mod must use the * marker")
    if kind == "separator" and not name.casefold().endswith("_separator"):
        raise Mo2ComparisonFormatError("separator name must end in _separator")
    if kind == "managed" and (marker == "*" or name.casefold().endswith("_separator")):
        raise Mo2ComparisonFormatError("managed mod classification is inconsistent")
    runtime = mapping["runtimePriority"]
    if runtime is not None:
        runtime = _non_negative_int(runtime, "runtimePriority")
    return Mo2ProjectedMod(
        name,
        kind,
        marker,
        enabled,
        _non_negative_int(mapping["listIndex"], "listIndex"),
        runtime,
    )


def _plugin(value: object) -> Mo2ProjectedPlugin:
    mapping = _exact_mapping(value, _PLUGIN_FIELDS, "projected plugin")
    return Mo2ProjectedPlugin(
        _name(mapping["name"], "plugin.name", plugin=True),
        _bool(mapping["primary"], "plugin.primary"),
        _bool(mapping["enabled"], "plugin.enabled"),
        _non_negative_int(mapping["loadOrderIndex"], "loadOrderIndex"),
    )


def _state_file(value: object) -> Mo2StateFileEvidence:
    mapping = _exact_mapping(value, _STATE_FILE_FIELDS, "state file")
    return Mo2StateFileEvidence(
        _relative_path(mapping["relativePath"], "relativePath"),
        _sha256(mapping["sha256"], "stateFile.sha256"),
        _non_negative_int(mapping["size"], "stateFile.size"),
    )


def _installed_mod(value: object) -> Mo2InstalledModState:
    mapping = _exact_mapping(value, _INSTALLED_MOD_FIELDS, "installed mod")
    name = _name(mapping["name"], "installedMod.name")
    metadata = None if mapping["metaIni"] is None else _state_file(mapping["metaIni"])
    if metadata is not None:
        expected = PurePosixPath("mods", name, "meta.ini").as_posix()
        if metadata.relative_path.casefold() != expected.casefold():
            raise Mo2ComparisonFormatError("metaIni path does not match installed mod")
    return Mo2InstalledModState(name, metadata)


def _differences(value: object) -> Mo2Differences:
    mapping = _exact_mapping(value, _DIFFERENCES_FIELDS, "differences")
    mods = _array(mapping["modChanges"], _entry_change, "modChanges")
    plugins = _array(mapping["pluginChanges"], _entry_change, "pluginChanges")
    config_mapping = _exact_mapping(
        mapping["configuration"], _CONFIGURATION_FIELDS, "configuration"
    )
    return Mo2Differences(
        mods,
        _order(mapping["modOrder"], "modOrder"),
        plugins,
        _order(mapping["pluginOrder"], "pluginOrder"),
        Mo2ConfigurationDifferences(
            _name_array(config_mapping["labOnly"], "configuration.labOnly"),
            _name_array(config_mapping["playOnly"], "configuration.playOnly"),
            _name_array(
                config_mapping["contentChanged"], "configuration.contentChanged"
            ),
        ),
    )


def _entry_change(value: object) -> Mo2EntryChange:
    mapping = _exact_mapping(value, _CHANGE_FIELDS, "entry change")
    kind = mapping["entryKind"]
    change = mapping["change"]
    if kind not in _ENTRY_KINDS:
        raise Mo2ComparisonFormatError("entryKind is invalid")
    if change not in _CHANGE_VALUES:
        raise Mo2ComparisonFormatError("change is invalid")
    return Mo2EntryChange(
        _name(mapping["name"], "change.name"),
        kind,
        change,
        _optional_position(mapping["playPosition"], "playPosition"),
        _optional_position(mapping["labPosition"], "labPosition"),
    )


def _optional_position(value: object, label: str) -> Mo2EntryPosition | None:
    if value is None:
        return None
    mapping = _exact_mapping(value, _POSITION_FIELDS, label)
    runtime = mapping["runtimePriority"]
    if runtime is not None:
        runtime = _non_negative_int(runtime, f"{label}.runtimePriority")
    return Mo2EntryPosition(
        _non_negative_int(mapping["sequenceIndex"], f"{label}.sequenceIndex"),
        runtime,
        _optional_name(mapping["previousSharedAnchor"], f"{label}.previousSharedAnchor"),
        _optional_name(mapping["nextSharedAnchor"], f"{label}.nextSharedAnchor"),
    )


def _order(value: object, label: str) -> Mo2OrderDifference:
    mapping = _exact_mapping(value, _ORDER_FIELDS, label)
    first = mapping["firstDivergence"]
    if first is not None:
        first = _non_negative_int(first, f"{label}.firstDivergence")
    return Mo2OrderDifference(
        _bool(mapping["changed"], f"{label}.changed"),
        _non_negative_int(
            mapping["differingPositionCount"], f"{label}.differingPositionCount"
        ),
        first,
        _name_array(mapping["playSequence"], f"{label}.playSequence"),
        _name_array(mapping["labSequence"], f"{label}.labSequence"),
    )


def _observations(value: object) -> Mo2SharedStateObservations:
    mapping = _exact_mapping(value, _OBSERVATION_FIELDS, "observations")
    if mapping["installedPayloadContent"] != "not-inspected":
        raise Mo2ComparisonFormatError(
            "observations.installedPayloadContent must be not-inspected"
        )
    return Mo2SharedStateObservations(
        _name_array(mapping["overwriteEntries"], "observations.overwriteEntries"),
        _name_array(
            mapping["installedModFolders"], "observations.installedModFolders"
        ),
        "not-inspected",
    )


def _coverage(value: object) -> Mo2Coverage:
    mapping = _exact_mapping(value, _COVERAGE_FIELDS, "coverage")
    if mapping["profileState"] not in {"complete", "incomplete"}:
        raise Mo2ComparisonFormatError("coverage.profileState is invalid")
    expected = {
        "installedPayloadContent": "not-inspected",
        "assetConflicts": "not-inspected",
        "pluginRecordConflicts": "not-inspected",
        "runtimeValidation": "not-performed",
    }
    if any(mapping[field] != expected[field] for field in expected):
        raise Mo2ComparisonFormatError("coverage contains an invalid claim")
    return Mo2Coverage(mapping["profileState"])


def _findings(value: object) -> tuple[Mo2Finding, ...]:
    items = _array(value, _finding, "findings", non_empty=True)
    _unique((item.code.casefold() for item in items), "findings contain duplicate codes")
    return items


def _finding(value: object) -> Mo2Finding:
    mapping = _exact_mapping(value, _FINDING_FIELDS, "finding")
    try:
        state = CheckState(mapping["state"])
    except (TypeError, ValueError) as error:
        raise Mo2ComparisonFormatError("finding.state is invalid") from error
    code = mapping["code"]
    if not isinstance(code, str) or _CODE.fullmatch(code) is None:
        raise Mo2ComparisonFormatError("finding.code is invalid")
    return Mo2Finding(state, code, _text(mapping["message"], "finding.message"))


def _append_order_text(
    lines: list[str], label: str, order: Mo2OrderDifference
) -> None:
    if not order.changed:
        return
    lines.append(
        f"{label} order: {order.differing_position_count} differing positions."
    )
    lines.append(f"First divergence: {order.first_divergence}")
    start = max(0, order.first_divergence - 3)
    end = min(len(order.play_sequence), order.first_divergence + 4)
    for index in range(start, end):
        lines.append(f"Play[{index}]: {order.play_sequence[index]}")
        lines.append(f"Lab[{index}]: {order.lab_sequence[index]}")
    lines.append("Use --format json for complete sequences.")


def _no_in_scope_difference(value: Mo2Differences) -> bool:
    return (
        not value.mod_changes
        and not value.mod_order.changed
        and not value.plugin_changes
        and not value.plugin_order.changed
        and not value.configuration.lab_only
        and not value.configuration.play_only
        and not value.configuration.content_changed
    )


def _exact_mapping(value: object, fields: set[str], label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise Mo2ComparisonFormatError(f"{label} must contain exactly {sorted(fields)}")
    return value


def _array(value: object, parser, label: str, *, non_empty: bool = False):
    if type(value) is not list or (non_empty and not value):
        raise Mo2ComparisonFormatError(f"{label} must be a{' non-empty' if non_empty else ''} array")
    return tuple(parser(item) for item in value)


def _name_array(value: object, label: str, *, plugin: bool = False) -> tuple[str, ...]:
    values = _array(value, lambda item: _name(item, label, plugin=plugin), label)
    _unique((item.casefold() for item in values), f"{label} contains duplicate names")
    return values


def _name(value: object, label: str, *, plugin: bool = False) -> str:
    text = _text(value, label)
    if (
        text in {".", ".."}
        or any(character in text for character in "/\\")
        or any(ord(character) < 32 for character in text)
    ):
        raise Mo2ComparisonFormatError(f"{label} must be one safe name")
    if plugin and PureWindowsPath(text).suffix.casefold() not in _PLUGIN_EXTENSIONS:
        raise Mo2ComparisonFormatError(f"{label} must name an ESM, ESL, or ESP")
    return text


def _optional_name(value: object, label: str) -> str | None:
    return None if value is None else _name(value, label)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise Mo2ComparisonFormatError(f"{label} must be non-empty text")
    return value


def _absolute_path(value: object, label: str) -> str:
    text = _text(value, label)
    if not (PureWindowsPath(text).is_absolute() or PurePosixPath(text).is_absolute()):
        raise Mo2ComparisonFormatError(f"{label} must be absolute")
    return text


def _relative_path(value: object, label: str) -> str:
    text = _text(value, label)
    path = PurePosixPath(text)
    if path.is_absolute() or "\\" in text or any(part in {"", ".", ".."} for part in path.parts):
        raise Mo2ComparisonFormatError(f"{label} must be a safe relative path")
    return path.as_posix()


def _bool(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise Mo2ComparisonFormatError(f"{label} must be boolean")
    return value


def _non_negative_int(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise Mo2ComparisonFormatError(f"{label} must be a non-negative integer")
    return value


def _schema(value: object, label: str) -> None:
    if type(value) is not int or value != 1:
        raise Mo2ComparisonFormatError(f"{label} must be integer 1")


def _sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise Mo2ComparisonFormatError(f"{label} must be a lowercase SHA-256")
    return value


def _optional_sha256(value: object, label: str) -> str | None:
    return None if value is None else _sha256(value, label)


def _unique(values, message: str) -> None:
    items = tuple(values)
    if len(items) != len(set(items)):
        raise Mo2ComparisonFormatError(message)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Mo2ComparisonFormatError(f"duplicate JSON key: {key}")
        result[key] = value
    return result
