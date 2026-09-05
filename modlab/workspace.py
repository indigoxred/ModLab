"""Predictable, non-destructive storage layout for user-owned ModLab data."""

import stat
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
    "games/skyrim-se-ae/tool-installations/mo2",
    "tools/mo2/skyrim-se-ae/app",
    "tools/mo2/skyrim-se-ae/downloads",
    "tools/mo2/skyrim-se-ae/mods",
    "tools/mo2/skyrim-se-ae/profiles",
    "tools/mo2/skyrim-se-ae/overwrite",
    "exports",
    "runtime/cache",
    "runtime/jobs",
    "runtime/jobs/mo2-bootstrap/plans",
    "runtime/transactions",
    "runtime/validation/mo2-containment",
)

_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class WorkspaceError(RuntimeError):
    """The workspace tree cannot be initialized without following a redirect."""


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
    mo2_bootstrap_jobs: Path
    mo2_bootstrap_plans: Path
    mo2_bootstrap_receipts: Path
    mo2_containment_validation: Path
    mo2_containment_authority: Path


def default_workspace_root() -> Path:
    return Path(__file__).resolve().parents[1] / "workspace"


def workspace_layout(root: Path) -> WorkspaceLayout:
    resolved = Path(root).expanduser().resolve()
    skyrim = resolved / "games" / "skyrim-se-ae"
    skyrim_mo2 = resolved / "tools" / "mo2" / "skyrim-se-ae"
    mo2_bootstrap_jobs = resolved / "runtime" / "jobs" / "mo2-bootstrap"
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
        mo2_bootstrap_jobs=mo2_bootstrap_jobs,
        mo2_bootstrap_plans=mo2_bootstrap_jobs / "plans",
        mo2_bootstrap_receipts=skyrim / "tool-installations" / "mo2",
        mo2_containment_validation=resolved / "runtime" / "validation" / "mo2-containment",
        mo2_containment_authority=resolved / "runtime" / "validation-authority" / "mo2-containment",
    )


def initialize_workspace(root: Path) -> WorkspaceLayout:
    requested = Path(root).expanduser().absolute()
    _prepare_workspace_root(requested)
    _validate_workspace_root(requested)
    layout = workspace_layout(requested)
    if layout.root != requested:
        raise WorkspaceError(f"workspace root changed during initialization: {requested}")

    # Validate all existing destination chains before the first child write. This
    # prevents a late redirected branch from leaving a partially initialized tree.
    for relative in _DIRECTORIES:
        _validate_existing_chain(layout.root, layout.root / relative)
    for relative in _DIRECTORIES:
        _ensure_direct_directory(layout.root, layout.root / relative)
    _validate_workspace_root(requested)
    return layout


def _prepare_workspace_root(root: Path) -> None:
    candidates = list(reversed((root, *root.parents)))
    for candidate in candidates:
        metadata = _lstat_if_exists(candidate)
        if metadata is not None:
            _require_direct_directory(candidate, metadata)

    for candidate in candidates:
        metadata = _lstat_if_exists(candidate)
        if metadata is None:
            _require_direct_directory(
                candidate.parent,
                _required_lstat(candidate.parent),
            )
            try:
                candidate.mkdir()
            except FileExistsError:
                pass
            except OSError as error:
                raise WorkspaceError(
                    f"cannot create workspace path {candidate}: {error}"
                ) from error
            metadata = _lstat_if_exists(candidate)
        if metadata is None:
            raise WorkspaceError(f"workspace path was not created: {candidate}")
        _require_direct_directory(candidate, metadata)


def _validate_existing_chain(root: Path, target: Path) -> None:
    _validate_workspace_root(root)
    try:
        relative = target.relative_to(root)
    except ValueError as error:
        raise WorkspaceError(f"workspace target escapes its root: {target}") from error
    current = root
    candidates = [root]
    for part in relative.parts:
        current = current / part
        candidates.append(current)
    for candidate in candidates:
        metadata = _lstat_if_exists(candidate)
        if metadata is not None:
            _require_direct_directory(candidate, metadata)


def _ensure_direct_directory(root: Path, target: Path) -> None:
    _validate_workspace_root(root)
    relative = target.relative_to(root)
    current = root
    _require_direct_directory(root, _required_lstat(root))
    for part in relative.parts:
        current = current / part
        metadata = _lstat_if_exists(current)
        if metadata is None:
            _require_direct_directory(current.parent, _required_lstat(current.parent))
            try:
                current.mkdir()
            except FileExistsError:
                pass
            except OSError as error:
                raise WorkspaceError(
                    f"cannot create workspace directory {current}: {error}"
                ) from error
            metadata = _lstat_if_exists(current)
        if metadata is None:
            raise WorkspaceError(f"workspace directory was not created: {current}")
        _require_direct_directory(current, metadata)
        _validate_workspace_root(root)


def _validate_workspace_root(root: Path) -> None:
    for candidate in (root, *root.parents):
        _require_direct_directory(candidate, _required_lstat(candidate))
    try:
        resolved = root.resolve(strict=True)
    except OSError as error:
        raise WorkspaceError(f"cannot resolve workspace root {root}: {error}") from error
    if resolved != root:
        raise WorkspaceError(f"redirected workspace root is not allowed: {root}")


def _required_lstat(path: Path):
    metadata = _lstat_if_exists(path)
    if metadata is None:
        raise WorkspaceError(f"workspace path disappeared during initialization: {path}")
    return metadata


def _lstat_if_exists(path: Path):
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise WorkspaceError(f"cannot inspect workspace path {path}: {error}") from error


def _require_direct_directory(path: Path, metadata) -> None:
    if path.is_symlink() or bool(
        getattr(metadata, "st_file_attributes", 0)
        & _FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise WorkspaceError(f"redirected workspace path is not allowed: {path}")
    if not stat.S_ISDIR(metadata.st_mode):
        raise WorkspaceError(f"workspace path is not a directory: {path}")
