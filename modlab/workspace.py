"""Predictable, non-destructive storage layout for user-owned ModLab data."""

from dataclasses import dataclass
from pathlib import Path


_DIRECTORIES = (
    "inbox",
    "library/archives",
    "library/metadata",
    "library/quarantine",
    "games/skyrim-se-ae/environments/stable",
    "games/skyrim-se-ae/environments/candidate",
    "games/skyrim-se-ae/checkpoints",
    "games/skyrim-se-ae/generated",
    "games/skyrim-se-ae/logs",
    "games/skyrim-se-ae/recipes",
    "games/skyrim-se-ae/target-environments",
    "tools/mo2/skyrim-se-ae/app",
    "tools/mo2/skyrim-se-ae/downloads",
    "tools/mo2/skyrim-se-ae/mods",
    "tools/mo2/skyrim-se-ae/profiles",
    "tools/mo2/skyrim-se-ae/overwrite",
    "exports",
    "runtime/cache",
    "runtime/jobs",
    "runtime/transactions",
)


@dataclass(frozen=True)
class WorkspaceLayout:
    root: Path
    inbox: Path
    archives: Path
    metadata: Path
    quarantine: Path
    games: Path
    skyrim: Path
    stable_environment: Path
    candidate_environment: Path
    checkpoints: Path
    generated: Path
    logs: Path
    tools: Path
    skyrim_mo2: Path
    skyrim_mo2_app: Path
    skyrim_mo2_downloads: Path
    skyrim_mo2_mods: Path
    skyrim_mo2_profiles: Path
    skyrim_mo2_overwrite: Path
    exports: Path
    cache: Path
    jobs: Path
    transactions: Path
    skyrim_environment_configuration: Path
    skyrim_recipes: Path
    skyrim_target_environments: Path


def default_workspace_root() -> Path:
    return Path(__file__).resolve().parents[1] / "workspace"


def workspace_layout(root: Path) -> WorkspaceLayout:
    resolved = Path(root).expanduser().resolve()
    skyrim = resolved / "games" / "skyrim-se-ae"
    skyrim_mo2 = resolved / "tools" / "mo2" / "skyrim-se-ae"
    return WorkspaceLayout(
        root=resolved,
        inbox=resolved / "inbox",
        archives=resolved / "library" / "archives",
        metadata=resolved / "library" / "metadata",
        quarantine=resolved / "library" / "quarantine",
        games=resolved / "games",
        skyrim=skyrim,
        stable_environment=skyrim / "environments" / "stable",
        candidate_environment=skyrim / "environments" / "candidate",
        checkpoints=skyrim / "checkpoints",
        generated=skyrim / "generated",
        logs=skyrim / "logs",
        tools=resolved / "tools",
        skyrim_mo2=skyrim_mo2,
        skyrim_mo2_app=skyrim_mo2 / "app",
        skyrim_mo2_downloads=skyrim_mo2 / "downloads",
        skyrim_mo2_mods=skyrim_mo2 / "mods",
        skyrim_mo2_profiles=skyrim_mo2 / "profiles",
        skyrim_mo2_overwrite=skyrim_mo2 / "overwrite",
        exports=resolved / "exports",
        cache=resolved / "runtime" / "cache",
        jobs=resolved / "runtime" / "jobs",
        transactions=resolved / "runtime" / "transactions",
        skyrim_environment_configuration=skyrim / "environment.json",
        skyrim_recipes=skyrim / "recipes",
        skyrim_target_environments=skyrim / "target-environments",
    )


def initialize_workspace(root: Path) -> WorkspaceLayout:
    layout = workspace_layout(root)
    for relative in _DIRECTORIES:
        (layout.root / relative).mkdir(parents=True, exist_ok=True)
    return layout
