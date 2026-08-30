"""Pure projection of inspected MO2 evidence into canonical logical state."""

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from pathlib import PurePosixPath

from modlab.recipes.model import CheckState

from .model import (
    Mo2ExecutableEvidence,
    Mo2Finding,
    Mo2InspectionReport,
    Mo2PathEvidence,
    Mo2ProfileEvidence,
    Mo2StateFileEvidence,
)


class Mo2ProjectionError(ValueError):
    """Raised when captured evidence cannot form one coherent MO2 state."""


class Mo2Readiness(StrEnum):
    READY = "Ready"
    BLOCKED = "Blocked"


@dataclass(frozen=True)
class Mo2Capability:
    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class Mo2ObservationContext:
    mo2_root: str
    game_root: str
    active_profile: str | None
    executable: Mo2ExecutableEvidence | None
    configured_paths: tuple[Mo2PathEvidence, ...]
    read_set_sha256: str | None
    read_set_stable: bool


@dataclass(frozen=True)
class Mo2ProjectedMod:
    name: str
    kind: str
    marker: str
    enabled: bool
    list_index: int
    runtime_priority: int | None


@dataclass(frozen=True)
class Mo2ProjectedPlugin:
    name: str
    primary: bool
    enabled: bool
    load_order_index: int


@dataclass(frozen=True)
class Mo2ProfileState:
    name: str
    profile_local_saves: bool
    profile_local_settings: bool
    mods: tuple[Mo2ProjectedMod, ...]
    plugins: tuple[Mo2ProjectedPlugin, ...]
    effective_load_order: tuple[str, ...]
    state_files: tuple[Mo2StateFileEvidence, ...]


@dataclass(frozen=True)
class Mo2InstalledModState:
    name: str
    meta_ini: Mo2StateFileEvidence | None


@dataclass(frozen=True)
class Mo2AdapterState:
    schema_version: int
    adapter_id: str
    lab: Mo2ProfileState
    play: Mo2ProfileState
    installed_mods: tuple[Mo2InstalledModState, ...]
    overwrite_entries: tuple[str, ...]


@dataclass(frozen=True)
class Mo2Coverage:
    profile_state: str
    installed_payload_content: str = "not-inspected"
    asset_conflicts: str = "not-inspected"
    plugin_record_conflicts: str = "not-inspected"
    runtime_validation: str = "not-performed"


@dataclass(frozen=True)
class Mo2Projection:
    readiness: Mo2Readiness
    observation_context: Mo2ObservationContext
    capabilities: tuple[Mo2Capability, ...]
    adapter_state: Mo2AdapterState | None
    adapter_state_sha256: str | None
    coverage: Mo2Coverage
    findings: tuple[Mo2Finding, ...]


_CORE_PRIMARY_PLUGINS = (
    "Skyrim.esm",
    "Update.esm",
    "Dawnguard.esm",
    "HearthFires.esm",
    "Dragonborn.esm",
)
_REQUIRED_PROFILE_FILES = {
    "modlist.txt",
    "plugins.txt",
    "loadorder.txt",
    "settings.ini",
}
_REQUIRED_PATH_KINDS = {"base", "downloads", "mods", "profiles", "overwrite"}
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
_CAPABILITY_DETAILS = {
    "scanner-safety": (
        "Scanner evidence has no blocking finding.",
        "Scanner evidence is blocked.",
    ),
    "configuration": (
        "Portable MO2 configuration and executable identity were observed.",
        "Portable MO2 configuration or executable identity is incomplete.",
    ),
    "game-match": (
        "The requested Skyrim installation was observed and matched.",
        "The requested Skyrim installation was not proven to match.",
    ),
    "contained-paths": (
        "All five configured MO2 paths are contained.",
        "Configured MO2 path evidence is incomplete or unsafe.",
    ),
    "exact-profiles": (
        "The exact Lab and Play profiles were observed once each.",
        "The exact Lab and Play profile pair is incomplete.",
    ),
    "required-profile-files": (
        "Both profiles have every required file and isolation setting.",
        "Required profile files or isolation settings are incomplete.",
    ),
    "primary-plugin-policy": (
        "Skyrim primary plug-in policy is complete and unique.",
        "Skyrim primary plug-in policy is missing or ambiguous.",
    ),
    "plugin-order-coherence": (
        "Plug-in state and load order reconcile for both profiles.",
        "Plug-in state and load-order evidence are inconsistent.",
    ),
    "installed-inventory": (
        "Installed mod folders and available metadata were observed.",
        "Installed mod inventory evidence is incomplete.",
    ),
    "overwrite-inventory": (
        "The overwrite top-level inventory was completely observed.",
        "Overwrite inventory evidence is incomplete.",
    ),
    "stable-read-set": (
        "The authoritative read set remained stable.",
        "The authoritative read set is missing or changed.",
    ),
    "read-only-side-effects": (
        "All side-effect collections are explicitly empty.",
        "Read-only inspection reported an unexpected side effect.",
    ),
}


def project_mo2_state(report: Mo2InspectionReport) -> Mo2Projection:
    evidence = report.comparison_evidence
    context = Mo2ObservationContext(
        mo2_root=report.resolved_root,
        game_root=report.game_root,
        active_profile=report.active_profile,
        executable=report.executable,
        configured_paths=tuple(sorted(report.paths, key=lambda item: item.kind)),
        read_set_sha256=None if evidence is None else evidence.read_set_sha256,
        read_set_stable=False if evidence is None else evidence.read_set_stable,
    )
    finding_by_code = {item.code: item for item in report.findings}
    profile_by_name = {item.name: item for item in report.profiles}
    exact_profiles = (
        len(report.profiles) == 2
        and set(profile_by_name) == {"ModLab - Lab", "ModLab - Play"}
        and _passed(finding_by_code, "lab-play-ready")
    )
    path_by_kind = {item.kind: item for item in report.paths}
    contained_paths = (
        len(report.paths) == len(_REQUIRED_PATH_KINDS)
        and set(path_by_kind) == _REQUIRED_PATH_KINDS
        and all(item.contained for item in path_by_kind.values())
        and _passed(finding_by_code, "paths-contained")
    )

    local_settings: dict[str, bool] = {}
    if evidence is not None:
        for item in evidence.profile_settings:
            if (
                item.name in local_settings
                or item.name not in {"ModLab - Lab", "ModLab - Play"}
                or type(item.profile_local_settings) is not bool
            ):
                local_settings = {}
                break
            local_settings[item.name] = item.profile_local_settings
    required_profile_files = exact_profiles and set(local_settings) == {
        "ModLab - Lab",
        "ModLab - Play",
    }
    if required_profile_files:
        for profile in report.profiles:
            basenames = {
                PurePosixPath(item.relative_path).name.casefold()
                for item in profile.state_files
            }
            if (
                not _REQUIRED_PROFILE_FILES.issubset(basenames)
                or type(profile.profile_local_saves) is not bool
                or (
                    profile.profile_local_settings is not None
                    and profile.profile_local_settings
                    != local_settings[profile.name]
                )
            ):
                required_profile_files = False
                break

    primary_plugins = () if evidence is None else evidence.primary_plugins
    primary_keys = tuple(item.casefold() for item in primary_plugins)
    primary_policy = (
        evidence is not None
        and len(primary_keys) >= len(_CORE_PRIMARY_PLUGINS)
        and primary_keys[: len(_CORE_PRIMARY_PLUGINS)]
        == tuple(item.casefold() for item in _CORE_PRIMARY_PLUGINS)
        and len(primary_keys) == len(set(primary_keys))
    )

    projected_plugins: dict[str, tuple[Mo2ProjectedPlugin, ...]] = {}
    plugin_error: str | None = None
    plugin_coherence = exact_profiles and primary_policy
    if plugin_coherence:
        try:
            for name in ("ModLab - Lab", "ModLab - Play"):
                projected_plugins[name] = _project_plugins(
                    profile_by_name[name], primary_plugins
                )
        except Mo2ProjectionError as error:
            plugin_error = str(error)
            plugin_coherence = False

    metadata_by_name, installed_inventory = _metadata_by_installed_mod(report)
    installed_inventory = (
        installed_inventory
        and contained_paths
        and not any(
            code in finding_by_code
            for code in ("mods-unreadable", "mods-entries-skipped", "mod-metadata-skipped")
        )
    )
    overwrite_status = tuple(
        item
        for item in report.findings
        if item.code in {"overwrite-empty", "overwrite-not-empty"}
        and item.state in {CheckState.PASSED, CheckState.WARNING}
    )
    overwrite_inventory = (
        contained_paths
        and len(overwrite_status) == 1
        and len({item.casefold() for item in report.overwrite_entries})
        == len(report.overwrite_entries)
        and not any(
            code in finding_by_code
            for code in ("overwrite-entries-skipped", "overwrite-status-unknown")
        )
    )
    stable_read_set = (
        evidence is not None
        and evidence.read_set_stable
        and not evidence.changed_paths
        and _valid_sha256(evidence.read_set_sha256)
    )
    read_only = not any(
        (
            report.actions,
            report.downloads,
            report.installations,
            report.program_launches,
        )
    )
    checks = {
        "scanner-safety": not any(
            item.state is CheckState.BLOCKED for item in report.findings
        ),
        "configuration": (
            report.portable_config_present
            and report.configured_game_path is not None
            and report.executable is not None
            and _passed(finding_by_code, "portable-config-observed")
        ),
        "game-match": (
            _passed(finding_by_code, "game-root-observed")
            and _passed(finding_by_code, "game-path-matched")
        ),
        "contained-paths": contained_paths,
        "exact-profiles": exact_profiles,
        "required-profile-files": required_profile_files,
        "primary-plugin-policy": primary_policy,
        "plugin-order-coherence": plugin_coherence,
        "installed-inventory": installed_inventory,
        "overwrite-inventory": overwrite_inventory,
        "stable-read-set": stable_read_set,
        "read-only-side-effects": read_only,
    }
    capabilities = tuple(
        Mo2Capability(
            name,
            "complete" if checks[name] else "incomplete",
            _CAPABILITY_DETAILS[name][0 if checks[name] else 1],
        )
        for name in _CAPABILITY_NAMES
    )

    projection_findings: list[Mo2Finding] = []
    existing_codes = {item.code for item in report.findings}
    for capability in capabilities:
        if capability.status == "complete":
            continue
        code = (
            "mo2-plugin-order-evidence-inconsistent"
            if capability.name == "plugin-order-coherence" and plugin_error
            else f"mo2-{capability.name}-incomplete"
        )
        if code in existing_codes:
            continue
        message = capability.detail
        if plugin_error and capability.name == "plugin-order-coherence":
            message = f"{message} {plugin_error}"
        projection_findings.append(Mo2Finding(CheckState.BLOCKED, code, message))
        existing_codes.add(code)

    ready = all(item.status == "complete" for item in capabilities)
    coverage = Mo2Coverage("complete" if ready else "incomplete")
    if not ready:
        return Mo2Projection(
            readiness=Mo2Readiness.BLOCKED,
            observation_context=context,
            capabilities=capabilities,
            adapter_state=None,
            adapter_state_sha256=None,
            coverage=coverage,
            findings=report.findings + tuple(projection_findings),
        )

    lab = _project_profile(
        profile_by_name["ModLab - Lab"],
        local_settings["ModLab - Lab"],
        projected_plugins["ModLab - Lab"],
    )
    play = _project_profile(
        profile_by_name["ModLab - Play"],
        local_settings["ModLab - Play"],
        projected_plugins["ModLab - Play"],
    )
    installed_mods = tuple(
        Mo2InstalledModState(name, metadata_by_name.get(name.casefold()))
        for name in sorted(report.top_level_mods, key=str.casefold)
    )
    state = Mo2AdapterState(
        schema_version=1,
        adapter_id="portable-mo2-skyrim",
        lab=lab,
        play=play,
        installed_mods=installed_mods,
        overwrite_entries=tuple(sorted(report.overwrite_entries, key=str.casefold)),
    )
    return Mo2Projection(
        readiness=Mo2Readiness.READY,
        observation_context=context,
        capabilities=capabilities,
        adapter_state=state,
        adapter_state_sha256=adapter_state_sha256(state),
        coverage=coverage,
        findings=report.findings,
    )


def _project_plugins(
    profile: Mo2ProfileEvidence,
    primary_plugins: tuple[str, ...],
) -> tuple[Mo2ProjectedPlugin, ...]:
    load_keys = tuple(item.casefold() for item in profile.load_order)
    primary_keys = tuple(item.casefold() for item in primary_plugins)
    state_keys = tuple(item.name.casefold() for item in profile.plugins)
    if len(load_keys) != len(set(load_keys)):
        raise Mo2ProjectionError("loadorder.txt contains duplicate plug-ins")
    if len(state_keys) != len(set(state_keys)):
        raise Mo2ProjectionError("plugins.txt contains duplicate plug-ins")
    core_count = len(_CORE_PRIMARY_PLUGINS)
    if load_keys[:core_count] != primary_keys[:core_count]:
        raise Mo2ProjectionError(
            "expected core primary plug-ins are not the load-order prefix"
        )
    if any(key in set(primary_keys) for key in state_keys):
        raise Mo2ProjectionError("plugins.txt contains a primary plug-in state row")
    primary_count = len(load_keys) - len(state_keys)
    if primary_count < core_count:
        raise Mo2ProjectionError("loadorder.txt is missing a core primary plug-in")
    observed_creation_keys = load_keys[core_count:primary_count]
    creation_policy_positions = {
        key: index for index, key in enumerate(primary_keys[core_count:])
    }
    if any(key not in creation_policy_positions for key in observed_creation_keys):
        raise Mo2ProjectionError(
            "loadorder.txt has an unexplained load-order-only plug-in"
        )
    observed_positions = tuple(
        creation_policy_positions[key] for key in observed_creation_keys
    )
    if observed_positions != tuple(sorted(observed_positions)):
        raise Mo2ProjectionError(
            "installed Creation plug-ins do not follow Skyrim.ccc order"
        )
    if state_keys != load_keys[primary_count:]:
        raise Mo2ProjectionError(
            "plugins.txt does not match non-primary load order"
        )
    states = {item.name.casefold(): item.enabled for item in profile.plugins}
    return tuple(
        Mo2ProjectedPlugin(
            name=name,
            primary=index < primary_count,
            enabled=(
                True
                if index < primary_count
                else states[name.casefold()]
            ),
            load_order_index=index,
        )
        for index, name in enumerate(profile.load_order)
    )


def _project_profile(
    profile: Mo2ProfileEvidence,
    local_settings: bool,
    plugins: tuple[Mo2ProjectedPlugin, ...],
) -> Mo2ProfileState:
    runtime_priority = 0
    mods: list[Mo2ProjectedMod] = []
    for list_index, item in enumerate(reversed(profile.mods)):
        if item.marker == "*":
            kind = "foreign"
        elif item.name.casefold().endswith("_separator"):
            kind = "separator"
        else:
            kind = "managed"
        priority = None if kind == "separator" else runtime_priority
        if priority is not None:
            runtime_priority += 1
        mods.append(
            Mo2ProjectedMod(
                name=item.name,
                kind=kind,
                marker=item.marker,
                enabled=item.enabled,
                list_index=list_index,
                runtime_priority=priority,
            )
        )
    return Mo2ProfileState(
        name=profile.name,
        profile_local_saves=profile.profile_local_saves,
        profile_local_settings=local_settings,
        mods=tuple(mods),
        plugins=plugins,
        effective_load_order=tuple(item.name for item in plugins if item.enabled),
        state_files=tuple(
            sorted(profile.state_files, key=lambda item: item.relative_path.casefold())
        ),
    )


def _metadata_by_installed_mod(
    report: Mo2InspectionReport,
) -> tuple[dict[str, Mo2StateFileEvidence], bool]:
    names = tuple(item.casefold() for item in report.top_level_mods)
    if len(names) != len(set(names)):
        return {}, False
    installed = set(names)
    metadata: dict[str, Mo2StateFileEvidence] = {}
    for item in report.mod_metadata_files:
        parts = PurePosixPath(item.relative_path).parts
        if (
            len(parts) != 3
            or parts[0].casefold() != "mods"
            or parts[2].casefold() != "meta.ini"
            or parts[1].casefold() not in installed
            or parts[1].casefold() in metadata
        ):
            return {}, False
        metadata[parts[1].casefold()] = item
    return metadata, True


def adapter_state_to_dict(state: Mo2AdapterState) -> dict[str, object]:
    def state_file(item: Mo2StateFileEvidence) -> dict[str, object]:
        return {
            "relativePath": item.relative_path,
            "sha256": item.sha256,
            "size": item.size,
        }

    def profile(item: Mo2ProfileState) -> dict[str, object]:
        return {
            "name": item.name,
            "profileLocalSaves": item.profile_local_saves,
            "profileLocalSettings": item.profile_local_settings,
            "mods": [
                {
                    "name": mod.name,
                    "kind": mod.kind,
                    "marker": mod.marker,
                    "enabled": mod.enabled,
                    "listIndex": mod.list_index,
                    "runtimePriority": mod.runtime_priority,
                }
                for mod in item.mods
            ],
            "plugins": [
                {
                    "name": plugin.name,
                    "primary": plugin.primary,
                    "enabled": plugin.enabled,
                    "loadOrderIndex": plugin.load_order_index,
                }
                for plugin in item.plugins
            ],
            "effectiveLoadOrder": list(item.effective_load_order),
            "stateFiles": [state_file(value) for value in item.state_files],
        }

    return {
        "schemaVersion": state.schema_version,
        "adapterId": state.adapter_id,
        "lab": profile(state.lab),
        "play": profile(state.play),
        "installedMods": [
            {
                "name": item.name,
                "metaIni": None if item.meta_ini is None else state_file(item.meta_ini),
            }
            for item in state.installed_mods
        ],
        "overwriteEntries": list(state.overwrite_entries),
    }


def adapter_state_sha256(state: Mo2AdapterState) -> str:
    encoded = json.dumps(
        adapter_state_to_dict(state),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _passed(findings: dict[str, Mo2Finding], code: str) -> bool:
    item = findings.get(code)
    return item is not None and item.state is CheckState.PASSED


def _valid_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
