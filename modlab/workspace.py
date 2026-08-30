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
    "tools",
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
    exports: Path
    cache: Path
    jobs: Path
    transactions: Path


def default_workspace_root() -> Path:
    return Path(__file__).resolve().parents[1] / "workspace"


def initialize_workspace(root: Path) -> WorkspaceLayout:
    resolved = Path(root).expanduser().resolve()
    for relative in _DIRECTORIES:
        (resolved / relative).mkdir(parents=True, exist_ok=True)

    skyrim = resolved / "games" / "skyrim-se-ae"
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
        exports=resolved / "exports",
        cache=resolved / "runtime" / "cache",
        jobs=resolved / "runtime" / "jobs",
        transactions=resolved / "runtime" / "transactions",
    )
