"""Read-only combined observation of Skyrim, portable MO2, and target intent."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from modlab.adapters.mo2.projection import (
    Mo2Projection,
    Mo2Readiness,
    project_mo2_state,
)
from modlab.adapters.mo2.scanner import inspect_skyrim_mo2
from modlab.adapters.skyrim.model import SkyrimDiscoveryReport
from modlab.adapters.skyrim.scanner import discover_skyrim_steam
from modlab.adapters.skyrim.windows_version import read_windows_file_version
from modlab.recipes.model import CheckState, EnvironmentEvidence
from modlab.workspace import workspace_layout


@dataclass(frozen=True)
class LiveDimensionFinding:
    dimension: str
    target_value: str
    actual_value: str | None
    state: CheckState
    source: str
    message: str


@dataclass(frozen=True)
class SkyrimLiveObservation:
    discovery: SkyrimDiscoveryReport
    projection: Mo2Projection | None
    live_dimensions: tuple[tuple[str, str], ...]
    dimension_findings: tuple[LiveDimensionFinding, ...]
    ready: bool


def normalize_mo2_compatibility_version(value: str | None) -> str | None:
    if value is None:
        return None
    parts = value.split(".")
    if (
        len(parts) == 4
        and all(part.isdecimal() for part in parts)
        and parts[-1] == "0"
    ):
        return ".".join(parts[:3])
    if len(parts) == 3 and all(part.isdecimal() for part in parts):
        return value
    return None


def observe_skyrim_environment(
    steam_root: Path,
    workspace_root: Path,
    target_environment: EnvironmentEvidence,
    *,
    skyrim_version_reader: Callable[[Path], str | None] = read_windows_file_version,
    mo2_version_reader: Callable[[Path], str | None] = read_windows_file_version,
) -> SkyrimLiveObservation:
    layout = workspace_layout(workspace_root)
    discovery = discover_skyrim_steam(
        steam_root,
        mo2_path=layout.skyrim_mo2_app / "ModOrganizer.exe",
        version_reader=skyrim_version_reader,
    )
    if discovery.game_root is None:
        return _blocked_observation(discovery, target_environment)

    inspection = inspect_skyrim_mo2(
        layout.skyrim_mo2_app,
        Path(discovery.game_root),
        workspace_root=workspace_root,
        version_reader=mo2_version_reader,
    )
    projection = project_mo2_state(inspection)

    live_dimensions: dict[str, str] = {}
    if (
        discovery.executable is not None
        and discovery.executable.compatibility_runtime is not None
    ):
        live_dimensions["executableRuntime"] = (
            discovery.executable.compatibility_runtime
        )
    manager_version = normalize_mo2_compatibility_version(
        None
        if projection.observation_context.executable is None
        else projection.observation_context.executable.file_version
    )
    if manager_version is not None:
        live_dimensions["adapterVersion"] = manager_version

    dimension_findings = _dimension_findings(
        target_environment, live_dimensions
    )
    game_identity_complete = (
        discovery.executable is not None
        and _valid_sha256(discovery.executable.sha256)
        and discovery.executable.size >= 0
        and not any(
            finding.state is CheckState.BLOCKED
            for finding in discovery.findings
        )
    )
    manager = projection.observation_context.executable
    manager_identity_complete = (
        manager is not None
        and _valid_sha256(manager.sha256)
        and manager.size >= 0
    )
    required_observable_dimensions = {
        "executableRuntime",
        "adapterVersion",
    } & set(target_environment.dimensions)
    observable_targets_complete = required_observable_dimensions.issubset(
        live_dimensions
    )
    ready = (
        game_identity_complete
        and manager_identity_complete
        and projection.readiness is Mo2Readiness.READY
        and projection.observation_context.read_set_stable
        and observable_targets_complete
        and not any(
            finding.state is CheckState.BLOCKED
            for finding in dimension_findings
        )
    )
    return SkyrimLiveObservation(
        discovery=discovery,
        projection=projection,
        live_dimensions=tuple(sorted(live_dimensions.items())),
        dimension_findings=dimension_findings,
        ready=ready,
    )


def _blocked_observation(
    discovery: SkyrimDiscoveryReport,
    target_environment: EnvironmentEvidence,
) -> SkyrimLiveObservation:
    findings = tuple(
        LiveDimensionFinding(
            dimension=dimension,
            target_value=target_value,
            actual_value=None,
            state=CheckState.UNKNOWN,
            source="not observed",
            message=(
                f"{dimension} target {target_value} was not compared because "
                "the Skyrim installation could not be observed safely."
            ),
        )
        for dimension, target_value in sorted(
            target_environment.dimensions.items()
        )
    )
    return SkyrimLiveObservation(
        discovery=discovery,
        projection=None,
        live_dimensions=(),
        dimension_findings=findings,
        ready=False,
    )


def _dimension_findings(
    target_environment: EnvironmentEvidence,
    live_dimensions: dict[str, str],
) -> tuple[LiveDimensionFinding, ...]:
    findings: list[LiveDimensionFinding] = []
    for dimension, target_value in sorted(target_environment.dimensions.items()):
        actual_value = live_dimensions.get(dimension)
        if actual_value is None:
            findings.append(
                LiveDimensionFinding(
                    dimension=dimension,
                    target_value=target_value,
                    actual_value=None,
                    state=CheckState.UNKNOWN,
                    source="target intent only",
                    message=(
                        f"{dimension} target {target_value} is retained intent; "
                        "no live adapter observed this dimension."
                    ),
                )
            )
        elif actual_value == target_value:
            findings.append(
                LiveDimensionFinding(
                    dimension=dimension,
                    target_value=target_value,
                    actual_value=actual_value,
                    state=CheckState.PASSED,
                    source=_dimension_source(dimension),
                    message=(
                        f"Observed {dimension} {actual_value}, matching target intent."
                    ),
                )
            )
        else:
            findings.append(
                LiveDimensionFinding(
                    dimension=dimension,
                    target_value=target_value,
                    actual_value=actual_value,
                    state=CheckState.BLOCKED,
                    source=_dimension_source(dimension),
                    message=(
                        f"Observed {dimension} {actual_value}, but target intent "
                        f"requires {target_value}."
                    ),
                )
            )
    return tuple(findings)


def _dimension_source(dimension: str) -> str:
    if dimension == "executableRuntime":
        return "Skyrim executable"
    if dimension == "adapterVersion":
        return "ModOrganizer executable"
    return "live adapter"


def _valid_sha256(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )
