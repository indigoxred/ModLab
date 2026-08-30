"""Strict, canonical machine-readable portable MO2 evidence."""

import json
import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import Mapping

from modlab.recipes.model import CheckState

from .model import (
    Mo2ExecutableEvidence,
    Mo2Finding,
    Mo2InspectionReport,
    Mo2ModEntry,
    Mo2PathEvidence,
    Mo2PluginEntry,
    Mo2ProfileEvidence,
    Mo2StateFileEvidence,
)


class Mo2EvidenceFormatError(ValueError):
    """Raised when portable MO2 evidence is ambiguous or unsafe."""


_REPORT_FIELDS = {
    "schemaVersion",
    "adapterId",
    "requestedRoot",
    "resolvedRoot",
    "gameRoot",
    "executable",
    "portableConfigPresent",
    "configuredGamePath",
    "paths",
    "activeProfile",
    "profiles",
    "topLevelMods",
    "overwriteEntries",
    "findings",
    "actions",
    "downloads",
    "installations",
    "programLaunches",
}
_EXECUTABLE_FIELDS = {"relativePath", "fileVersion", "sha256", "size"}
_PATH_FIELDS = {"kind", "configuredPath", "resolvedPath", "contained"}
_PROFILE_FIELDS = {
    "name",
    "relativePath",
    "profileLocalSaves",
    "stateFiles",
    "mods",
    "plugins",
    "loadOrder",
}
_STATE_FILE_FIELDS = {"relativePath", "sha256", "size"}
_MOD_FIELDS = {"name", "marker", "enabled"}
_PLUGIN_FIELDS = {"name", "enabled"}
_FINDING_FIELDS = {"state", "code", "message"}
_PATH_KINDS = {"base", "downloads", "mods", "profiles", "overwrite"}
_PLUGIN_EXTENSIONS = {".esm", ".esl", ".esp"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}$")
_CODE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def report_from_json(text: str) -> Mo2InspectionReport:
    try:
        data = json.loads(text, object_pairs_hook=_unique_object)
    except Mo2EvidenceFormatError:
        raise
    except (TypeError, json.JSONDecodeError) as error:
        raise Mo2EvidenceFormatError("MO2 evidence must be valid JSON") from error
    return report_from_dict(data)


def report_to_json(report: Mo2InspectionReport) -> str:
    return json.dumps(
        report_to_dict(report),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def report_from_dict(data: object) -> Mo2InspectionReport:
    mapping = _exact_mapping(data, _REPORT_FIELDS, "MO2 inspection report")
    if type(mapping["schemaVersion"]) is not int or mapping["schemaVersion"] != 1:
        raise Mo2EvidenceFormatError("schemaVersion must be integer 1")
    if mapping["adapterId"] != "portable-mo2-skyrim":
        raise Mo2EvidenceFormatError("adapterId must be portable-mo2-skyrim")

    requested_root = _absolute_path(mapping["requestedRoot"], "requestedRoot")
    resolved_root = _absolute_path(mapping["resolvedRoot"], "resolvedRoot")
    game_root = _absolute_path(mapping["gameRoot"], "gameRoot")
    executable = _executable(mapping["executable"])
    portable_config_present = _bool(
        mapping["portableConfigPresent"], "portableConfigPresent"
    )
    configured_game_path = _optional_absolute_path(
        mapping["configuredGamePath"], "configuredGamePath"
    )

    paths = _array(mapping["paths"], _path, "paths")
    _unique((item.kind for item in paths), "paths contain a duplicate kind")
    profiles = _array(mapping["profiles"], _profile, "profiles")
    _unique(
        (item.name.casefold() for item in profiles),
        "profiles contain a duplicate name",
    )
    active_profile = _optional_name(mapping["activeProfile"], "activeProfile")
    if active_profile is not None and all(
        item.name.casefold() != active_profile.casefold() for item in profiles
    ):
        raise Mo2EvidenceFormatError("activeProfile must name an observed profile")

    top_level_mods = _name_array(mapping["topLevelMods"], "topLevelMods")
    overwrite_entries = _name_array(
        mapping["overwriteEntries"], "overwriteEntries"
    )
    findings = _array(mapping["findings"], _finding, "findings", non_empty=True)
    _unique((item.code for item in findings), "findings contain a duplicate code")

    for field in ("actions", "downloads", "installations", "programLaunches"):
        if mapping[field] != []:
            raise Mo2EvidenceFormatError(f"{field} must be an explicit empty array")

    return Mo2InspectionReport(
        schema_version=1,
        adapter_id="portable-mo2-skyrim",
        requested_root=requested_root,
        resolved_root=resolved_root,
        game_root=game_root,
        executable=executable,
        portable_config_present=portable_config_present,
        configured_game_path=configured_game_path,
        paths=tuple(sorted(paths, key=lambda item: item.kind)),
        active_profile=active_profile,
        profiles=tuple(sorted(profiles, key=lambda item: item.name.casefold())),
        top_level_mods=tuple(sorted(top_level_mods, key=str.casefold)),
        overwrite_entries=tuple(sorted(overwrite_entries, key=str.casefold)),
        findings=findings,
        actions=(),
        downloads=(),
        installations=(),
        program_launches=(),
    )


def report_to_dict(report: Mo2InspectionReport) -> dict[str, object]:
    return {
        "schemaVersion": report.schema_version,
        "adapterId": report.adapter_id,
        "requestedRoot": report.requested_root,
        "resolvedRoot": report.resolved_root,
        "gameRoot": report.game_root,
        "executable": (
            None
            if report.executable is None
            else {
                "relativePath": report.executable.relative_path,
                "fileVersion": report.executable.file_version,
                "sha256": report.executable.sha256,
                "size": report.executable.size,
            }
        ),
        "portableConfigPresent": report.portable_config_present,
        "configuredGamePath": report.configured_game_path,
        "paths": [
            {
                "kind": item.kind,
                "configuredPath": item.configured_path,
                "resolvedPath": item.resolved_path,
                "contained": item.contained,
            }
            for item in report.paths
        ],
        "activeProfile": report.active_profile,
        "profiles": [
            {
                "name": profile.name,
                "relativePath": profile.relative_path,
                "profileLocalSaves": profile.profile_local_saves,
                "stateFiles": [
                    {
                        "relativePath": item.relative_path,
                        "sha256": item.sha256,
                        "size": item.size,
                    }
                    for item in profile.state_files
                ],
                "mods": [
                    {
                        "name": item.name,
                        "marker": item.marker,
                        "enabled": item.enabled,
                    }
                    for item in profile.mods
                ],
                "plugins": [
                    {"name": item.name, "enabled": item.enabled}
                    for item in profile.plugins
                ],
                "loadOrder": list(profile.load_order),
            }
            for profile in report.profiles
        ],
        "topLevelMods": list(report.top_level_mods),
        "overwriteEntries": list(report.overwrite_entries),
        "findings": [
            {
                "state": item.state.value,
                "code": item.code,
                "message": item.message,
            }
            for item in report.findings
        ],
        "actions": [],
        "downloads": [],
        "installations": [],
        "programLaunches": [],
    }


def _executable(value: object) -> Mo2ExecutableEvidence | None:
    if value is None:
        return None
    mapping = _exact_mapping(value, _EXECUTABLE_FIELDS, "executable")
    if mapping["relativePath"] != "ModOrganizer.exe":
        raise Mo2EvidenceFormatError(
            "executable.relativePath must be ModOrganizer.exe"
        )
    version = mapping["fileVersion"]
    if version is not None and (
        not isinstance(version, str) or _VERSION.fullmatch(version) is None
    ):
        raise Mo2EvidenceFormatError(
            "executable.fileVersion must be null or a dotted numeric version"
        )
    return Mo2ExecutableEvidence(
        relative_path="ModOrganizer.exe",
        file_version=version,
        sha256=_sha256(mapping["sha256"], "executable.sha256"),
        size=_non_negative_int(mapping["size"], "executable.size"),
    )


def _path(value: object) -> Mo2PathEvidence:
    mapping = _exact_mapping(value, _PATH_FIELDS, "MO2 path")
    kind = mapping["kind"]
    if kind not in _PATH_KINDS:
        raise Mo2EvidenceFormatError(f"MO2 path kind must be one of {_PATH_KINDS}")
    configured = _text(mapping["configuredPath"], "configuredPath")
    resolved = _absolute_path(mapping["resolvedPath"], "resolvedPath")
    contained = _bool(mapping["contained"], "contained")
    return Mo2PathEvidence(kind, configured, resolved, contained)


def _profile(value: object) -> Mo2ProfileEvidence:
    mapping = _exact_mapping(value, _PROFILE_FIELDS, "MO2 profile")
    name = _name(mapping["name"], "profile.name")
    relative_path = _safe_relative_path(
        mapping["relativePath"], "profile.relativePath"
    )
    expected = PurePosixPath("profiles", name).as_posix()
    if relative_path.casefold() != expected.casefold():
        raise Mo2EvidenceFormatError(
            "profile.relativePath must be profiles/<profile name>"
        )
    local_saves = mapping["profileLocalSaves"]
    if local_saves is not None and type(local_saves) is not bool:
        raise Mo2EvidenceFormatError("profileLocalSaves must be null or boolean")
    state_files = _array(mapping["stateFiles"], _state_file, "stateFiles")
    _unique(
        (item.relative_path.casefold() for item in state_files),
        "stateFiles contain a duplicate relativePath",
    )
    prefix = f"profiles/{name}/".casefold()
    if any(not item.relative_path.casefold().startswith(prefix) for item in state_files):
        raise Mo2EvidenceFormatError(
            "stateFiles must remain beneath their observed profile"
        )
    mods = _array(mapping["mods"], _mod, "mods")
    _unique((item.name.casefold() for item in mods), "mods contain a duplicate name")
    plugins = _array(mapping["plugins"], _plugin, "plugins")
    _unique(
        (item.name.casefold() for item in plugins),
        "plugins contain a duplicate name",
    )
    load_order = _name_array(mapping["loadOrder"], "loadOrder", plugin=True)
    return Mo2ProfileEvidence(
        name,
        relative_path,
        local_saves,
        tuple(state_files),
        tuple(mods),
        tuple(plugins),
        tuple(load_order),
    )


def _state_file(value: object) -> Mo2StateFileEvidence:
    mapping = _exact_mapping(value, _STATE_FILE_FIELDS, "MO2 state file")
    relative_path = _safe_relative_path(
        mapping["relativePath"], "stateFile.relativePath"
    )
    return Mo2StateFileEvidence(
        relative_path,
        _sha256(mapping["sha256"], "stateFile.sha256"),
        _non_negative_int(mapping["size"], "stateFile.size"),
    )


def _mod(value: object) -> Mo2ModEntry:
    mapping = _exact_mapping(value, _MOD_FIELDS, "mod entry")
    name = _name(mapping["name"], "mod.name")
    marker = mapping["marker"]
    enabled = _bool(mapping["enabled"], "mod.enabled")
    if marker not in {"+", "-", "*"} or enabled != (marker in {"+", "*"}):
        raise Mo2EvidenceFormatError("mod marker and enabled state disagree")
    return Mo2ModEntry(name, marker, enabled)


def _plugin(value: object) -> Mo2PluginEntry:
    mapping = _exact_mapping(value, _PLUGIN_FIELDS, "plugin entry")
    name = _name(mapping["name"], "plugin.name", plugin=True)
    return Mo2PluginEntry(name, _bool(mapping["enabled"], "plugin.enabled"))


def _finding(value: object) -> Mo2Finding:
    mapping = _exact_mapping(value, _FINDING_FIELDS, "MO2 finding")
    try:
        state = CheckState(mapping["state"])
    except (TypeError, ValueError) as error:
        raise Mo2EvidenceFormatError(
            "finding.state must be Passed, Warning, Blocked, or Unknown"
        ) from error
    code = mapping["code"]
    if not isinstance(code, str) or _CODE.fullmatch(code) is None:
        raise Mo2EvidenceFormatError("finding.code must be a lowercase slug")
    return Mo2Finding(state, code, _text(mapping["message"], "finding.message"))


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise Mo2EvidenceFormatError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _exact_mapping(
    value: object, expected: set[str], label: str
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise Mo2EvidenceFormatError(f"{label} must be an object")
    fields = set(value)
    if fields != expected:
        raise Mo2EvidenceFormatError(
            f"{label} fields differ: missing={sorted(expected - fields)}, "
            f"extra={sorted(str(item) for item in fields - expected)}"
        )
    return value


def _array(value: object, parser, label: str, non_empty: bool = False) -> tuple:
    if not isinstance(value, list) or (non_empty and not value):
        qualifier = "non-empty " if non_empty else ""
        raise Mo2EvidenceFormatError(f"{label} must be a {qualifier}array")
    return tuple(parser(item) for item in value)


def _name_array(
    value: object, label: str, plugin: bool = False
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise Mo2EvidenceFormatError(f"{label} must be an array")
    names = tuple(_name(item, label, plugin=plugin) for item in value)
    _unique((item.casefold() for item in names), f"{label} contains a duplicate")
    return names


def _name(value: object, label: str, plugin: bool = False) -> str:
    result = _text(value, label)
    if (
        result != result.strip()
        or any(character in result for character in "/\\")
        or any(ord(character) < 32 for character in result)
        or result in {".", ".."}
        or any(part.casefold() == "saves" for part in PureWindowsPath(result).parts)
    ):
        raise Mo2EvidenceFormatError(f"{label} must be one safe name")
    if plugin and PureWindowsPath(result).suffix.casefold() not in _PLUGIN_EXTENSIONS:
        raise Mo2EvidenceFormatError(f"{label} must name an ESM, ESL, or ESP")
    return result


def _optional_name(value: object, label: str) -> str | None:
    return None if value is None else _name(value, label)


def _safe_relative_path(value: object, label: str) -> str:
    if not isinstance(value, str) or "\\" in value:
        raise Mo2EvidenceFormatError(f"{label} must use safe forward slashes")
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or any(part.casefold() == "saves" for part in path.parts)
        or path.suffix.casefold() in {".ess", ".skse"}
    ):
        raise Mo2EvidenceFormatError(f"{label} must be a safe non-save relative path")
    return path.as_posix()


def _absolute_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Mo2EvidenceFormatError(f"{label} must be a safe absolute path")
    path = PureWindowsPath(value)
    if not path.is_absolute() or not path.drive or ".." in path.parts:
        raise Mo2EvidenceFormatError(f"{label} must be a safe absolute path")
    return str(path)


def _optional_absolute_path(value: object, label: str) -> str | None:
    return None if value is None else _absolute_path(value, label)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Mo2EvidenceFormatError(f"{label} must be non-blank text")
    return value


def _bool(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise Mo2EvidenceFormatError(f"{label} must be boolean")
    return value


def _sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise Mo2EvidenceFormatError(f"{label} must be a lowercase SHA-256")
    return value


def _non_negative_int(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise Mo2EvidenceFormatError(f"{label} must be a non-negative integer")
    return value


def _unique(values, message: str) -> None:
    observed = list(values)
    if len(observed) != len(set(observed)):
        raise Mo2EvidenceFormatError(message)
