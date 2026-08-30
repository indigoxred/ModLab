"""Immutable evidence returned by Skyrim Steam discovery."""

from dataclasses import dataclass

from modlab.recipes.model import CheckState


@dataclass(frozen=True)
class ExecutableEvidence:
    relative_path: str
    file_version: str | None
    compatibility_runtime: str | None
    sha256: str
    size: int


@dataclass(frozen=True)
class DataFileEvidence:
    relative_path: str
    extension: str
    size: int


@dataclass(frozen=True)
class DiscoveryFinding:
    state: CheckState
    code: str
    message: str


@dataclass(frozen=True)
class SkyrimDiscoveryReport:
    schema_version: int
    adapter_id: str
    steam_root: str
    manifest_path: str
    app_id: str | None
    app_name: str | None
    install_directory: str | None
    install_state_flags: int | None
    game_root: str | None
    executable: ExecutableEvidence | None
    data_files: tuple[DataFileEvidence, ...]
    creation_club_plugin_count: int
    creation_club_archive_count: int
    mo2_path: str | None
    findings: tuple[DiscoveryFinding, ...]
