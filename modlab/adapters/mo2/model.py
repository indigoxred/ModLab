"""Immutable evidence returned by portable MO2 inspection."""

from dataclasses import dataclass

from modlab.recipes.model import CheckState


@dataclass(frozen=True)
class Mo2ExecutableEvidence:
    relative_path: str
    file_version: str | None
    sha256: str
    size: int


@dataclass(frozen=True)
class Mo2PathEvidence:
    kind: str
    configured_path: str
    resolved_path: str
    contained: bool


@dataclass(frozen=True)
class Mo2StateFileEvidence:
    relative_path: str
    sha256: str
    size: int


@dataclass(frozen=True)
class Mo2ModEntry:
    name: str
    marker: str
    enabled: bool


@dataclass(frozen=True)
class Mo2PluginEntry:
    name: str
    enabled: bool


@dataclass(frozen=True)
class Mo2ProfileEvidence:
    name: str
    relative_path: str
    profile_local_saves: bool | None
    state_files: tuple[Mo2StateFileEvidence, ...]
    mods: tuple[Mo2ModEntry, ...]
    plugins: tuple[Mo2PluginEntry, ...]
    load_order: tuple[str, ...]


@dataclass(frozen=True)
class Mo2Finding:
    state: CheckState
    code: str
    message: str


@dataclass(frozen=True)
class Mo2InspectionReport:
    schema_version: int
    adapter_id: str
    requested_root: str
    resolved_root: str
    game_root: str
    executable: Mo2ExecutableEvidence | None
    portable_config_present: bool
    configured_game_path: str | None
    paths: tuple[Mo2PathEvidence, ...]
    active_profile: str | None
    profiles: tuple[Mo2ProfileEvidence, ...]
    top_level_mods: tuple[str, ...]
    overwrite_entries: tuple[str, ...]
    findings: tuple[Mo2Finding, ...]
    actions: tuple[()]
    downloads: tuple[()]
    installations: tuple[()]
    program_launches: tuple[()]

