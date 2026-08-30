"""Pure, placement-aware comparison of projected MO2 Lab and Play state."""

from dataclasses import dataclass
from pathlib import PurePosixPath

from modlab.recipes.model import CheckState

from .model import Mo2Finding, Mo2StateFileEvidence
from .projection import (
    Mo2AdapterState,
    Mo2Capability,
    Mo2Coverage,
    Mo2ObservationContext,
    Mo2ProjectedMod,
    Mo2ProjectedPlugin,
    Mo2Projection,
    Mo2Readiness,
)


@dataclass(frozen=True)
class Mo2EntryPosition:
    sequence_index: int
    runtime_priority: int | None
    previous_shared_anchor: str | None
    next_shared_anchor: str | None


@dataclass(frozen=True)
class Mo2EntryChange:
    name: str
    entry_kind: str
    change: str
    play_position: Mo2EntryPosition | None
    lab_position: Mo2EntryPosition | None


@dataclass(frozen=True)
class Mo2OrderDifference:
    changed: bool
    differing_position_count: int
    first_divergence: int | None
    play_sequence: tuple[str, ...]
    lab_sequence: tuple[str, ...]


@dataclass(frozen=True)
class Mo2ConfigurationDifferences:
    lab_only: tuple[str, ...]
    play_only: tuple[str, ...]
    content_changed: tuple[str, ...]


@dataclass(frozen=True)
class Mo2Differences:
    mod_changes: tuple[Mo2EntryChange, ...]
    mod_order: Mo2OrderDifference
    plugin_changes: tuple[Mo2EntryChange, ...]
    plugin_order: Mo2OrderDifference
    configuration: Mo2ConfigurationDifferences


@dataclass(frozen=True)
class Mo2SharedStateObservations:
    overwrite_entries: tuple[str, ...]
    installed_mod_folders: tuple[str, ...]
    installed_payload_content: str


@dataclass(frozen=True)
class Mo2ComparisonReport:
    schema_version: int
    adapter_id: str
    readiness: Mo2Readiness
    direction: str
    observation_context: Mo2ObservationContext
    capabilities: tuple[Mo2Capability, ...]
    adapter_state: Mo2AdapterState | None
    adapter_state_sha256: str | None
    differences: Mo2Differences | None
    observations: Mo2SharedStateObservations
    coverage: Mo2Coverage
    findings: tuple[Mo2Finding, ...]


_SEMANTIC_LIST_FILES = {"modlist.txt", "plugins.txt", "loadorder.txt"}


def compare_mo2_profiles(projection: Mo2Projection) -> Mo2ComparisonReport:
    state = projection.adapter_state
    observations = Mo2SharedStateObservations(
        overwrite_entries=() if state is None else state.overwrite_entries,
        installed_mod_folders=(
            () if state is None else tuple(item.name for item in state.installed_mods)
        ),
        installed_payload_content="not-inspected",
    )
    if projection.readiness is Mo2Readiness.BLOCKED:
        return Mo2ComparisonReport(
            schema_version=1,
            adapter_id="portable-mo2-skyrim",
            readiness=projection.readiness,
            direction="Play-to-Lab",
            observation_context=projection.observation_context,
            capabilities=projection.capabilities,
            adapter_state=None,
            adapter_state_sha256=None,
            differences=None,
            observations=observations,
            coverage=projection.coverage,
            findings=projection.findings,
        )
    if state is None or projection.adapter_state_sha256 is None:
        raise ValueError("Ready MO2 projection must contain adapter state and hash")

    mod_changes, mod_order = _compare_mods(state)
    plugin_changes, plugin_order = _compare_plugins(state)
    configuration = _configuration_differences(
        state.play.state_files, state.lab.state_files
    )
    differences = Mo2Differences(
        mod_changes=mod_changes,
        mod_order=mod_order,
        plugin_changes=plugin_changes,
        plugin_order=plugin_order,
        configuration=configuration,
    )
    findings = list(projection.findings)
    codes = {item.code for item in findings}
    if state.installed_mods and "mo2-shared-installed-content-not-fingerprinted" not in codes:
        findings.append(
            Mo2Finding(
                CheckState.WARNING,
                "mo2-shared-installed-content-not-fingerprinted",
                "Installed mod folders are shared by both profiles; their payload contents were not fingerprinted.",
            )
        )
        codes.add("mo2-shared-installed-content-not-fingerprinted")
    if _has_no_in_scope_differences(differences) and "mo2-profile-state-no-differences" not in codes:
        findings.append(
            Mo2Finding(
                CheckState.PASSED,
                "mo2-profile-state-no-differences",
                "No differences were found in the inspected profile-state scope; installed payload content was not inspected.",
            )
        )

    return Mo2ComparisonReport(
        schema_version=1,
        adapter_id="portable-mo2-skyrim",
        readiness=projection.readiness,
        direction="Play-to-Lab",
        observation_context=projection.observation_context,
        capabilities=projection.capabilities,
        adapter_state=state,
        adapter_state_sha256=projection.adapter_state_sha256,
        differences=differences,
        observations=observations,
        coverage=projection.coverage,
        findings=tuple(findings),
    )


def _compare_mods(
    state: Mo2AdapterState,
) -> tuple[tuple[Mo2EntryChange, ...], Mo2OrderDifference]:
    play = state.play.mods
    lab = state.lab.mods
    play_by_name = {item.name.casefold(): item for item in play}
    lab_by_name = {item.name.casefold(): item for item in lab}
    shared_anchors = {
        key
        for key in play_by_name.keys() & lab_by_name.keys()
        if play_by_name[key].kind != "separator"
        and lab_by_name[key].kind != "separator"
        and play_by_name[key].enabled
        and lab_by_name[key].enabled
    }
    changes: list[Mo2EntryChange] = []
    for index, item in enumerate(lab):
        key = item.name.casefold()
        previous = play_by_name.get(key)
        if previous is None:
            changes.append(
                Mo2EntryChange(
                    item.name,
                    item.kind,
                    "added-in-lab",
                    None,
                    _mod_position(item, index, lab, shared_anchors),
                )
            )
        elif item.enabled and not previous.enabled:
            play_index = _index_for(play, key)
            changes.append(
                Mo2EntryChange(
                    item.name,
                    item.kind,
                    "enabled-in-lab",
                    _mod_position(previous, play_index, play, shared_anchors),
                    _mod_position(item, index, lab, shared_anchors),
                )
            )
    for index, item in enumerate(play):
        key = item.name.casefold()
        current = lab_by_name.get(key)
        if current is None:
            changes.append(
                Mo2EntryChange(
                    item.name,
                    item.kind,
                    "removed-from-lab",
                    _mod_position(item, index, play, shared_anchors),
                    None,
                )
            )
        elif item.enabled and not current.enabled:
            lab_index = _index_for(lab, key)
            changes.append(
                Mo2EntryChange(
                    item.name,
                    item.kind,
                    "disabled-in-lab",
                    _mod_position(item, index, play, shared_anchors),
                    _mod_position(current, lab_index, lab, shared_anchors),
                )
            )
    play_order = tuple(item.name for item in play if item.name.casefold() in shared_anchors)
    lab_order = tuple(item.name for item in lab if item.name.casefold() in shared_anchors)
    return tuple(changes), _order_difference(play_order, lab_order)


def _compare_plugins(
    state: Mo2AdapterState,
) -> tuple[tuple[Mo2EntryChange, ...], Mo2OrderDifference]:
    play = state.play.plugins
    lab = state.lab.plugins
    play_by_name = {item.name.casefold(): item for item in play}
    lab_by_name = {item.name.casefold(): item for item in lab}
    shared_anchors = {
        key
        for key in play_by_name.keys() & lab_by_name.keys()
        if play_by_name[key].enabled and lab_by_name[key].enabled
    }
    changes: list[Mo2EntryChange] = []
    for index, item in enumerate(lab):
        key = item.name.casefold()
        previous = play_by_name.get(key)
        if previous is None:
            changes.append(
                Mo2EntryChange(
                    item.name,
                    "plugin",
                    "added-in-lab",
                    None,
                    _plugin_position(item, index, lab, shared_anchors),
                )
            )
        elif item.enabled and not previous.enabled:
            play_index = _index_for(play, key)
            changes.append(
                Mo2EntryChange(
                    item.name,
                    "plugin",
                    "enabled-in-lab",
                    _plugin_position(previous, play_index, play, shared_anchors),
                    _plugin_position(item, index, lab, shared_anchors),
                )
            )
    for index, item in enumerate(play):
        key = item.name.casefold()
        current = lab_by_name.get(key)
        if current is None:
            changes.append(
                Mo2EntryChange(
                    item.name,
                    "plugin",
                    "removed-from-lab",
                    _plugin_position(item, index, play, shared_anchors),
                    None,
                )
            )
        elif item.enabled and not current.enabled:
            lab_index = _index_for(lab, key)
            changes.append(
                Mo2EntryChange(
                    item.name,
                    "plugin",
                    "disabled-in-lab",
                    _plugin_position(item, index, play, shared_anchors),
                    _plugin_position(current, lab_index, lab, shared_anchors),
                )
            )
    play_order = tuple(item.name for item in play if item.name.casefold() in shared_anchors)
    lab_order = tuple(item.name for item in lab if item.name.casefold() in shared_anchors)
    return tuple(changes), _order_difference(play_order, lab_order)


def _mod_position(
    entry: Mo2ProjectedMod,
    index: int,
    sequence: tuple[Mo2ProjectedMod, ...],
    shared_anchors: set[str],
) -> Mo2EntryPosition:
    previous, following = _anchors(sequence, index, shared_anchors)
    return Mo2EntryPosition(
        entry.list_index,
        entry.runtime_priority,
        previous,
        following,
    )


def _plugin_position(
    entry: Mo2ProjectedPlugin,
    index: int,
    sequence: tuple[Mo2ProjectedPlugin, ...],
    shared_anchors: set[str],
) -> Mo2EntryPosition:
    previous, following = _anchors(sequence, index, shared_anchors)
    return Mo2EntryPosition(
        entry.load_order_index,
        None,
        previous,
        following,
    )


def _anchors(sequence, index: int, shared_anchors: set[str]) -> tuple[str | None, str | None]:
    previous = next(
        (
            item.name
            for item in reversed(sequence[:index])
            if item.name.casefold() in shared_anchors
        ),
        None,
    )
    following = next(
        (
            item.name
            for item in sequence[index + 1 :]
            if item.name.casefold() in shared_anchors
        ),
        None,
    )
    return previous, following


def _index_for(sequence, key: str) -> int:
    return next(
        index for index, item in enumerate(sequence) if item.name.casefold() == key
    )


def _order_difference(
    play_sequence: tuple[str, ...],
    lab_sequence: tuple[str, ...],
) -> Mo2OrderDifference:
    changed_positions = tuple(
        index
        for index, (play, lab) in enumerate(zip(play_sequence, lab_sequence))
        if play.casefold() != lab.casefold()
    )
    return Mo2OrderDifference(
        changed=bool(changed_positions),
        differing_position_count=len(changed_positions),
        first_divergence=changed_positions[0] if changed_positions else None,
        play_sequence=play_sequence,
        lab_sequence=lab_sequence,
    )


def _configuration_differences(
    play_files: tuple[Mo2StateFileEvidence, ...],
    lab_files: tuple[Mo2StateFileEvidence, ...],
) -> Mo2ConfigurationDifferences:
    play = _configuration_map(play_files)
    lab = _configuration_map(lab_files)
    lab_only = tuple(
        sorted(
            (lab[key][0] for key in lab.keys() - play.keys()),
            key=str.casefold,
        )
    )
    play_only = tuple(
        sorted(
            (play[key][0] for key in play.keys() - lab.keys()),
            key=str.casefold,
        )
    )
    changed = tuple(
        sorted(
            (
                lab[key][0]
                for key in lab.keys() & play.keys()
                if (
                    lab[key][1].sha256 != play[key][1].sha256
                    or lab[key][1].size != play[key][1].size
                )
            ),
            key=str.casefold,
        )
    )
    return Mo2ConfigurationDifferences(lab_only, play_only, changed)


def _configuration_map(
    files: tuple[Mo2StateFileEvidence, ...],
) -> dict[str, tuple[str, Mo2StateFileEvidence]]:
    result: dict[str, tuple[str, Mo2StateFileEvidence]] = {}
    for item in files:
        name = PurePosixPath(item.relative_path).name
        key = name.casefold()
        if key in _SEMANTIC_LIST_FILES:
            continue
        if key in result:
            raise ValueError("profile contains duplicate configuration basenames")
        result[key] = (name, item)
    return result


def _has_no_in_scope_differences(differences: Mo2Differences) -> bool:
    return (
        not differences.mod_changes
        and not differences.mod_order.changed
        and not differences.plugin_changes
        and not differences.plugin_order.changed
        and not differences.configuration.lab_only
        and not differences.configuration.play_only
        and not differences.configuration.content_changed
    )
