"""Crash-safe orchestration and fail-closed adjudication for MO2 containment."""

from __future__ import annotations
from modlab.adapters.mo2.path_budget import PathBudget, PlannedPath, admit_paths, publication_paths

from contextvars import ContextVar
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, replace
from functools import partial, wraps
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
from typing import Callable, Generic, Mapping, TypeVar
import uuid

from modlab.adapters.mo2.processes import inspect_mo2_processes
from modlab.adapters.mo2.release import bundled_mo2_252_path, load_mo2_release
from modlab.adapters.skyrim.windows_version import read_windows_file_version
from modlab.platform.windows_exact_fs import (
    ExactDirectoryCreationError,
    ExactDirectoryCreationOutcome,
    ExactDirectoryCreationOwnershipError,
    ExactObjectError,
    ExactObjectOwnershipError,
    PinnedIdentity,
    PinnedObject,
    RetainedObjectOwner,
    RetainedObjectRole,
    create_pinned_directory_child,
    identity_at_path,
    pin_stable_direct_object,
    read_pinned_file,
    union_retained_ownership,
)
from modlab.workspace import workspace_layout

from .mo2_containment_fixtures import (
    ContainmentFixture,
    FixtureAdmission,
    PROTECTED_MOD_FILES,
    REPLACEMENT_OUTPUT_FILES,
    SCENARIO_ADOPTION_NAMES,
    SCENARIO_OUTPUT_FILES,
    preflight_containment_fixture,
    prepare_containment_fixture,
)
from .mo2_containment_model import (
    CapabilityDecision,
    DecisionBindings,
    CapabilityVerdict,
    ContainmentScenario,
    IntegrityObservation,
    ProcessEvidence,
    ProtectedState,
    ScenarioCleanupStatus,
    ScenarioJournal,
    ScenarioOutcome,
    ScenarioRecovery,
    ScenarioResult,
    ScenarioState,
    TreeIdentity,
    WatchEvidenceCompletion,
    WatchOutcome,
)
from .mo2_containment_serialization import (
    ContainmentFormatError,
    capability_decision_to_bytes,
    deterministic_policy_violations,
    scenario_result_id_for,
    scenario_result_to_bytes,
    source_artifact_id_for,
    watch_outcome_id_for,
)
from .mo2_containment_store import (
    PROTECTED_STATE_LABELS,
    ContainmentStore,
    ContainmentStoreError,
    ContainmentStoreNotFound,
    ContainmentStoreOwnershipError,
    mutable_replacement_part_path,
)
from .windows_integrity import (
    IntegrityLabelError,
    IntegrityLevel,
    inspect_path_integrity,
    inspect_process_integrity,
    launch_low_integrity_process,
    set_low_integrity_tree,
    source_integrity_allowed,
    stage_integrity_allowed,
    to_integrity_observation,
)
from .windows_junction import (
    ContainmentSafetyError,
    JunctionOwnershipError,
    OwnedProjection,
    RetainedRelocationAuthority,
    adopt_retained_relocation,
    create_mod_projection,
    ensure_direct_subdirectory,
    inspect_junction,
    quarantine_retained_relocation,
    quarantine_retained_replacement,
    retain_relocation_authority,
    stable_tree_identity,
)
from . import windows_watch as _windows_watch
from .mo2_preparation_recovery import (
    PreparationAttempt, PreparationFailure, PreparationProjection,
    PreparationRecovery, PreparationProcess, ProjectionIdentity, PreparationCleanup,
    PreparationReplacementV2, PreparationStartupInitialized, PreparationStartupFailure,
    native_preparation_process_inventory, native_process_absent, record_id, record_to_bytes,
)
from .windows_watch import start_watch, stop_watch, watch_root
from .windows_watch_protocol import (
    CLAIM_NAME,
    LAUNCH_NAME,
    ROOT_KINDS,
    WatchRequest,
    controller_claim_from_bytes,
    watch_request_from_bytes,
    watch_request_sha256,
    worker_launch_from_bytes,
)


_SCHEMA_VERSION = 1
_MECHANISM = "isolated-low-integrity-junction-projection-v1"
_CLASSIFICATION_POLICY = "scenario-classification-v4"
_PREVIOUS_CLASSIFICATION_POLICY = "scenario-classification-v3"
_OLDER_CLASSIFICATION_POLICY = "scenario-classification-v2"
_FIXTURE_POLICY = "disposable-shell-environment-v3"
_PREVIOUS_FIXTURE_POLICY = "disposable-shell-environment-v2"
_OPERATOR_POLICY = "foreground-interactive-confirmation-v1"
_PROTECTED_NAME = "Protected Existing"
_EXPECTED_NEW = SCENARIO_ADOPTION_NAMES
_EXPECTED_OUTPUTS = SCENARIO_OUTPUT_FILES
_EXPECTED_REPLACEMENT_OUTPUTS = REPLACEMENT_OUTPUT_FILES


_P = TypeVar("_P")
_V = TypeVar("_V")


def _unique_paths(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    rows: list[Path] = []
    seen: set[str] = set()
    for supplied in paths:
        path = Path(supplied)
        key = os.path.normcase(os.path.normpath(str(path)))
        if key not in seen:
            seen.add(key)
            rows.append(path)
    return tuple(rows)


def _unique_text(rows: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(rows))


@dataclass(frozen=True)
class ContainmentEffects:
    """Exact paths, delegated roots, and processes observed for one operation."""

    written_paths: tuple[Path, ...] = ()
    child_mutation_roots: tuple[Path, ...] = ()
    watcher_pid: int | None = None
    mo2_pid: int | None = None
    source_changes: tuple[str, ...] = ()
    game_changes: tuple[str, ...] = ()
    production_mo2_changes: tuple[str, ...] = ()
    preparation_processes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        written = _unique_paths(tuple(self.written_paths))
        roots = _unique_paths(tuple(self.child_mutation_roots))
        if any(not path.is_absolute() for path in (*written, *roots)):
            raise ValueError("containment effect paths must be absolute")
        if any(root not in written for root in roots):
            raise ValueError("child mutation roots must be included in written paths")
        for label, pid in (("watcher", self.watcher_pid), ("MO2", self.mo2_pid)):
            if pid is not None and (type(pid) is not int or pid <= 0):
                raise ValueError(f"{label} PID must be a positive integer")
        changes = (
            ("source", tuple(self.source_changes)),
            ("game", tuple(self.game_changes)),
            ("production MO2", tuple(self.production_mo2_changes)),
        )
        for label, values in changes:
            if any(type(value) is not str or not value for value in values):
                raise ValueError(f"{label} effects must be nonempty text")
        if any(type(value) is not str or not value for value in self.preparation_processes):
            raise ValueError("preparation processes must be nonempty text")
        object.__setattr__(self, "written_paths", written)
        object.__setattr__(self, "child_mutation_roots", roots)
        object.__setattr__(self, "source_changes", _unique_text(changes[0][1]))
        object.__setattr__(self, "game_changes", _unique_text(changes[1][1]))
        object.__setattr__(
            self,
            "production_mo2_changes",
            _unique_text(changes[2][1]),
        )

    @property
    def launched_processes(self) -> tuple[str, ...]:
        rows: list[str] = []
        if self.watcher_pid is not None:
            rows.append(f"Watcher PID: {self.watcher_pid}")
        if self.mo2_pid is not None:
            rows.append(f"MO2 PID: {self.mo2_pid}")
        return (*rows, *self.preparation_processes)

    @classmethod
    def merged(cls, *receipts: "ContainmentEffects") -> "ContainmentEffects":
        values = tuple(receipts)
        if any(not isinstance(value, cls) for value in values):
            raise TypeError("containment effects can merge only exact receipts")
        watcher_pids = {
            value.watcher_pid for value in values if value.watcher_pid is not None
        }
        mo2_pids = {value.mo2_pid for value in values if value.mo2_pid is not None}
        if len(watcher_pids) > 1 or len(mo2_pids) > 1:
            raise ValueError("containment effect receipts disagree on a launched PID")
        return cls(
            written_paths=tuple(
                path for value in values for path in value.written_paths
            ),
            child_mutation_roots=tuple(
                path for value in values for path in value.child_mutation_roots
            ),
            watcher_pid=next(iter(watcher_pids), None),
            mo2_pid=next(iter(mo2_pids), None),
            source_changes=tuple(
                item for value in values for item in value.source_changes
            ),
            game_changes=tuple(
                item for value in values for item in value.game_changes
            ),
            production_mo2_changes=tuple(
                item for value in values for item in value.production_mo2_changes
            ),
            preparation_processes=tuple(
                item for value in values for item in value.preparation_processes
            ),
        )


@dataclass(frozen=True)
class ContainmentServiceResult(Generic[_V]):
    """Immutable service value paired with its exact operation effects."""

    value: _V
    effects: ContainmentEffects

    def __getattr__(self, name: str):
        return getattr(self.value, name)


class ContainmentServiceError(RuntimeError):
    """The scenario cannot advance without weakening its evidence boundary."""

    def __init__(
        self,
        message: str,
        *,
        effects: ContainmentEffects | None = None,
    ) -> None:
        super().__init__(message)
        self.effects = ContainmentEffects() if effects is None else effects


class ContainmentOperationError(ContainmentServiceError):
    """A mutating service operation failed with an exact partial receipt."""

    def __init__(
        self,
        message: str,
        *,
        effects: ContainmentEffects,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message, effects=effects)
        self.cause = cause


class ContainmentDecisionNotReady(ContainmentServiceError):
    """Current-run evidence is not complete enough to freeze a decision."""


class _EffectLedger:
    def __init__(self) -> None:
        self._written_paths: list[Path] = []
        self._child_mutation_roots: list[Path] = []
        self._watcher_pid: int | None = None
        self._mo2_pid: int | None = None
        self._source_changes: list[str] = []
        self._game_changes: list[str] = []
        self._production_mo2_changes: list[str] = []
        self._preparation_processes: list[str] = []

    def write(self, path: Path) -> None:
        self._written_paths.append(Path(path))

    def child_mutation_root(self, path: Path) -> None:
        root = Path(path)
        self._written_paths.append(root)
        self._child_mutation_roots.append(root)

    def watcher(self, pid: int) -> None:
        self._watcher_pid = pid

    def mo2(self, pid: int) -> None:
        self._mo2_pid = pid

    def preparation_process(self, label: str) -> None:
        self._preparation_processes.append(label)

    def changes(
        self,
        *,
        source: tuple[str, ...] = (),
        game: tuple[str, ...] = (),
        production_mo2: tuple[str, ...] = (),
    ) -> None:
        self._source_changes.extend(source)
        self._game_changes.extend(game)
        self._production_mo2_changes.extend(production_mo2)

    def freeze(self) -> ContainmentEffects:
        return ContainmentEffects(
            tuple(self._written_paths),
            tuple(self._child_mutation_roots),
            self._watcher_pid,
            self._mo2_pid,
            tuple(self._source_changes),
            tuple(self._game_changes),
            tuple(self._production_mo2_changes),
            tuple(self._preparation_processes),
        )


_ACTIVE_EFFECTS: ContextVar[_EffectLedger | None] = ContextVar(
    "mo2_containment_effects",
    default=None,
)
_ACTIVE_GUARDED_MUTATION_ROOTS: ContextVar[tuple[Path, ...]] = ContextVar(
    "mo2_containment_guarded_mutation_roots",
    default=(),
)
_ACTIVE_RELOCATION_AUTHORITIES: ContextVar[
    tuple[RetainedRelocationAuthority, ...]
] = ContextVar("mo2_containment_relocation_authorities", default=())


def _current_effects() -> _EffectLedger:
    ledger = _ACTIVE_EFFECTS.get()
    if ledger is None:
        raise RuntimeError("containment effect ledger is unavailable")
    return ledger


def _effect_store(validation_root: Path) -> ContainmentStore:
    return ContainmentStore(
        validation_root,
        _effect_recorder=_current_effects().write,
    )


class _MutationObservationError(ContainmentServiceError):
    """A delegated root is proven unsafe rather than merely unavailable."""


class _MutationObservationOwnershipError(ContainmentStoreOwnershipError):
    """A complete inventory whose final handle close retained ownership."""

    def __init__(
        self,
        message: str,
        ownership: ExactObjectOwnershipError,
        completed_observation: tuple[tuple[object, ...], ...],
    ) -> None:
        super().__init__(message, ownership)
        self.completed_observation = completed_observation


@dataclass(frozen=True)
class _RelocationAdmission:
    source_rows: tuple[tuple[object, ...], ...]
    stage_rows: tuple[tuple[object, ...], ...]
    source_names: tuple[str, ...]
    stage_names: tuple[str, ...]
    changed_names: tuple[str, ...]
    mutation_names: tuple[str, ...]
    destination_map: tuple[tuple[str, tuple[str, ...]], ...]
    quarantine_names: tuple[str, ...] | None
    source_volume: int
    stage_volume: int
    destination_volume: int
    projection_rows: tuple[tuple[object, ...], ...]
    budget: PathBudget

    @property
    def sha256(self) -> str:
        canonical = json.dumps(
            {
                "sourceRows": self.source_rows,
                "stageRows": self.stage_rows,
                "sourceNames": self.source_names,
                "stageNames": self.stage_names,
                "changedNames": self.changed_names,
                "mutationNames": self.mutation_names,
                "destinationMap": self.destination_map,
                "quarantineNames": self.quarantine_names,
                "sourceVolume": self.source_volume,
                "stageVolume": self.stage_volume,
                "destinationVolume": self.destination_volume,
                "projectionRows": self.projection_rows,
                "budget": self.budget.sha256,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


@dataclass
class _RetainedRelocation:
    admission: _RelocationAdmission
    authority: RetainedRelocationAuthority
    expected_projections: tuple = ()

    def verify(self, *, require_quarantine_parent: bool = False) -> None:
        if (
            _relocation_projection_rows(self.expected_projections)
            != self.admission.projection_rows
        ):
            raise ContainmentServiceError(
                "relocation expected projection ownership changed"
            )
        self.authority.verify(
            require_quarantine_parent=require_quarantine_parent,
        )

    def bind_quarantine_parent(self, owner: object) -> None:
        self.authority.bind_quarantine_parent(owner)


def _canonical_relocation_rows(
    root: Path,
    rows: tuple[tuple[object, ...], ...] | None,
) -> tuple[tuple[object, ...], ...]:
    if not rows or len(rows[0]) != 10 or rows[0][0] != ".":
        raise ContainmentServiceError(
            f"relocation tree observation is unavailable: {root}"
        )
    identities: set[str] = set()
    prior_key: tuple[str, str] | None = None
    for index, row in enumerate(rows):
        if not row or not isinstance(row[0], str):
            raise ContainmentServiceError("relocation tree contains a malformed row")
        relative = row[0]
        if index:
            path = PurePosixPath(relative)
            if (
                path.is_absolute()
                or path.as_posix() != relative
                or any(part in {"", ".", ".."} for part in path.parts)
            ):
                raise ContainmentServiceError(
                    f"relocation tree contains an unsafe relative path: {relative!r}"
                )
            identity = relative.casefold()
            if identity in identities:
                raise ContainmentServiceError(
                    "relocation tree contains a case-insensitive collision"
                )
            identities.add(identity)
            key = (identity, relative)
            if prior_key is not None and key < prior_key:
                raise ContainmentServiceError(
                    "relocation tree observation is not canonically ordered"
                )
            prior_key = key
        if len(row) == 10 and row[1] in {"directory", "file"}:
            if not all(isinstance(value, int) for value in row[2:9]):
                raise ContainmentServiceError(
                    "relocation tree contains malformed direct identity"
                )
            digest = row[-1]
            if row[1] == "file":
                if (
                    not isinstance(digest, str)
                    or len(digest) != 64
                    or any(character not in "0123456789abcdef" for character in digest)
                ):
                    raise ContainmentServiceError(
                        "relocation tree contains malformed file identity"
                    )
            elif digest is not None:
                raise ContainmentServiceError(
                    "relocation tree contains malformed directory identity"
                )
        elif not (
            index
            and len(row) == 10
            and row[1] == "junction"
            and all(isinstance(value, int) for value in row[2:4])
            and all(value is None for value in row[4:9])
            and isinstance(row[9], str)
            and len(row[9]) == 64
            and all(character in "0123456789abcdef" for character in row[9])
        ):
            raise ContainmentServiceError("relocation tree contains a malformed row")
    return rows


def _top_level_relocation_names(
    rows: tuple[tuple[object, ...], ...],
) -> tuple[str, ...]:
    names: dict[str, str] = {}
    for row in rows[1:]:
        name = PurePosixPath(row[0]).parts[0]
        prior = names.setdefault(name.casefold(), name)
        if prior != name:
            raise ContainmentServiceError(
                "relocation tree contains a case-insensitive collision"
            )
    return tuple(sorted(names.values(), key=lambda item: (item.casefold(), item)))


def _relocation_destination_volume(destination: Path) -> int:
    ancestor = _nearest_existing_direct_directory(Path(destination))
    identity = identity_at_path(ancestor)
    if hasattr(identity, "volume_serial"):
        return int(identity.volume_serial)
    return int(identity[0])


def _changed_relocation_names(
    rows: tuple[tuple[object, ...], ...],
    before_names: tuple[str, ...],
) -> tuple[str, ...]:
    top_level = {
        row[0]: row
        for row in rows[1:]
        if len(PurePosixPath(row[0]).parts) == 1
    }
    return tuple(
        name
        for name in before_names
        if name in top_level and top_level[name][1] != "junction"
    )


def _relocation_projection_rows(
    expected_projections: tuple,
) -> tuple[tuple[object, ...], ...]:
    rows = []
    for owner in expected_projections:
        try:
            owner.verify()
            rows.append(
                (
                    str(owner.evidence.link_path),
                    str(owner.evidence.target_path),
                    hashlib.sha256(owner.payload).hexdigest(),
                    tuple(
                        (str(pin.path), int(pin.identity[0]), int(pin.identity[1]))
                        for pin in owner.pins
                    ),
                )
            )
        except (AttributeError, IndexError, TypeError, ValueError) as error:
            raise ContainmentServiceError(
                "relocation expected projection evidence is malformed"
            ) from error
    return tuple(rows)


def _reject_relocation_destination_collisions(
    record,
    quarantine: Path,
    mutation_names: tuple[str, ...],
    source_names: tuple[str, ...],
) -> tuple[str, ...] | None:
    """Reject known destination collisions before any quarantine root creation."""
    mutation_folds = {name.casefold() for name in mutation_names}
    if record.scenario in _EXPECTED_OUTPUTS:
        expected = _EXPECTED_NEW[record.scenario]
        if (
            expected.casefold() in mutation_folds
            and any(name.casefold() == expected.casefold() for name in source_names)
        ):
            raise ContainmentServiceError(
                f"relocation destination collision in source mods for {expected!r}"
            )

    destination = Path(quarantine)
    try:
        metadata = destination.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ContainmentServiceError(
            "relocation quarantine destination is unavailable"
        ) from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
        or not stat.S_ISDIR(metadata.st_mode)
    ):
        raise ContainmentServiceError(
            "relocation quarantine destination is not a direct directory"
        )
    try:
        names = _direct_names(destination)
    except OSError as error:
        raise ContainmentServiceError(
            "relocation quarantine destination is unavailable"
        ) from error
    folded = tuple(name.casefold() for name in names)
    if len(folded) != len(set(folded)):
        raise ContainmentServiceError(
            "relocation quarantine destination contains a case-insensitive collision"
        )
    collision = next(
        (name for name in names if name.casefold() in mutation_folds),
        None,
    )
    if collision is not None:
        raise ContainmentServiceError(
            f"relocation destination collision in quarantine for {collision!r}"
        )
    return names


def _preflight_projection_relocation(
    record,
    quarantine: Path,
    *,
    expected_projections: tuple = (),
) -> _RelocationAdmission:
    source_rows = _canonical_relocation_rows(
        record.source_mods,
        _mutation_root_observation(
            record.source_mods,
            expected_projections=expected_projections,
        ) if expected_projections else _mutation_root_observation(record.source_mods),
    )
    stage_rows = _canonical_relocation_rows(
        record.stage_mods,
        _mutation_root_observation(
            record.stage_mods,
            expected_projections=expected_projections,
        ) if expected_projections else _mutation_root_observation(record.stage_mods),
    )
    source_names = _top_level_relocation_names(source_rows)
    stage_names = _top_level_relocation_names(stage_rows)
    changed_names = _changed_relocation_names(stage_rows, record.before_names)
    if any(name not in stage_names for name in changed_names):
        raise ContainmentServiceError(
            "relocation changed-name evidence is outside the observed staging tree"
        )
    source_volume = int(source_rows[0][2])
    stage_volume = int(stage_rows[0][2])
    if (
        any(int(row[2]) != source_volume for row in source_rows)
        or any(int(row[2]) != stage_volume for row in stage_rows)
    ):
        raise ContainmentServiceError(
            "relocation tree crosses a native volume boundary"
        )
    destination_volume = _relocation_destination_volume(quarantine)
    if source_volume != stage_volume or stage_volume != destination_volume:
        raise ContainmentServiceError(
            "relocation source and destination must remain on one volume"
        )
    projection_rows = _relocation_projection_rows(expected_projections)

    rows: list[PlannedPath] = []
    for label, root, observed in (
        ("relocation-source", record.source_mods, source_rows),
        ("relocation-stage", record.stage_mods, stage_rows),
    ):
        for row in observed:
            relative = row[0]
            path = root if relative == "." else root.joinpath(*relative.split("/"))
            rows.append(PlannedPath(label, "preparation-wide", str(path)))

    unexpected = tuple(name for name in stage_names if name not in record.before_names)
    mutation_names = tuple(dict.fromkeys((*changed_names, *unexpected)))
    quarantine_names = _reject_relocation_destination_collisions(
        record,
        Path(quarantine),
        mutation_names,
        source_names,
    )
    expected = _EXPECTED_NEW[record.scenario]
    destination_map = []
    for name in mutation_names:
        members = tuple(
            row[0].removeprefix(name + "/")
            for row in stage_rows[1:]
            if row[0].startswith(name + "/")
        )
        destinations = []
        if (
            record.scenario in _EXPECTED_OUTPUTS
            and name == expected
            and name not in record.before_names
        ):
            destinations.append(record.source_mods / name)
        destinations.append(Path(quarantine) / name)
        destination_map.append((name, tuple(str(path) for path in destinations)))
        for destination in destinations:
            rows.append(
                PlannedPath("relocation-destination", "preparation-wide", str(destination))
            )
            rows.extend(
                PlannedPath(
                    "relocation-destination:member",
                    "preparation-wide",
                    str(destination.joinpath(*member.split("/"))),
                )
                for member in members
            )
    return _RelocationAdmission(
        source_rows,
        stage_rows,
        source_names,
        stage_names,
        changed_names,
        mutation_names,
        tuple(destination_map),
        quarantine_names,
        source_volume,
        stage_volume,
        destination_volume,
        projection_rows,
        admit_paths(rows),
    )


def _require_relocation_admission(
    record,
    quarantine: Path,
    expected: _RelocationAdmission,
    *,
    expected_projections: tuple = (),
) -> _RelocationAdmission:
    current = _preflight_projection_relocation(
        record,
        quarantine,
        expected_projections=expected_projections,
    )
    if current != expected:
        raise ContainmentServiceError("relocation immutable admission changed")
    return current


@contextmanager
def _retained_projection_relocation(
    record,
    quarantine: Path,
    guards_by_path: Mapping[Path, object],
    expected: _RelocationAdmission,
    *,
    expected_projections: tuple = (),
):
    """Bind one immutable relocation admission to live exact-object owners."""
    admission = _require_relocation_admission(
        record,
        quarantine,
        expected,
        expected_projections=expected_projections,
    )
    source_parent = guards_by_path.get(Path(record.source_mods))
    stage_parent = guards_by_path.get(Path(record.stage_mods))
    quarantine_parent = guards_by_path.get(Path(quarantine))
    if source_parent is None or stage_parent is None:
        raise ContainmentServiceError(
            "relocation mutation-root owners are unavailable"
        )
    authority = None
    primary = None
    try:
        authority = retain_relocation_authority(
            source_mods=record.source_mods,
            stage_mods=record.stage_mods,
            quarantine_root=Path(quarantine),
            source_rows=admission.source_rows,
            stage_rows=admission.stage_rows,
            source_names=admission.source_names,
            stage_names=admission.stage_names,
            mutation_names=admission.mutation_names,
            destination_map=admission.destination_map,
            quarantine_names=admission.quarantine_names,
            source_parent=source_parent,
            stage_parent=stage_parent,
            quarantine_parent=quarantine_parent,
        )
        yield _RetainedRelocation(admission, authority, expected_projections)
    except BaseException as error:
        primary = error
        raise
    finally:
        if authority is not None:
            try:
                authority.close()
            except JunctionOwnershipError as error:
                if primary is not None:
                    raise error from primary
                raise


def _verify_retained_relocation_before_creation(
    relocation: _RetainedRelocation,
) -> None:
    relocation.verify()


def _bind_retained_relocation_root(
    relocation: _RetainedRelocation,
    owner: object,
) -> None:
    relocation.bind_quarantine_parent(owner)


def _verify_retained_relocation_before_operation(
    relocation: _RetainedRelocation,
) -> None:
    relocation.verify(require_quarantine_parent=True)


@contextmanager
def _retained_relocation_projections(store, run_id: str, record):
    """Retain the one admitted baseline projection when it is still present."""
    link = record.stage_mods / _PROTECTED_NAME
    try:
        metadata = link.lstat()
    except FileNotFoundError:
        raise ContainmentServiceError(
            "relocation baseline projection is missing"
        )
    except OSError as error:
        raise ContainmentServiceError(
            "relocation baseline projection identity is unavailable"
        ) from error
    redirected = link.is_symlink() or bool(
        getattr(metadata, "st_file_attributes", 0) & 0x400
    )
    if not redirected:
        if (
            record.scenario is ContainmentScenario.REPLACE_EXISTING
            and stat.S_ISDIR(metadata.st_mode)
        ):
            yield ()
            return
        raise ContainmentServiceError(
            "relocation baseline projection is not the exact prepared object"
        )
    try:
        projection = store.load_preparation_projection(
            run_id,
            record.scenario.value,
        )
        owner = _pin_preparation_projection(store, projection)
    except BaseException as error:
        raise ContainmentServiceError(
            "relocation baseline projection is not the exact prepared object"
        ) from error
    primary = None
    try:
        if owner.evidence.link_path != link:
            raise ContainmentServiceError(
                "relocation baseline projection path differs from the fixture"
            )
        yield (owner,)
    except BaseException as error:
        primary = error
        raise
    finally:
        _close_preparation_owners(
            (owner,),
            "relocation baseline projection",
            primary,
        )


def _path_component_identity(metadata: os.stat_result) -> tuple[int, ...]:
    """Stable pathname-component identity, separate from subtree contents."""
    return (
        stat.S_IFMT(metadata.st_mode),
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(getattr(metadata, "st_file_attributes", 0)) & 0x410,
    )


def _mutation_root_guard(
    supplied_root: Path,
    existing_guards: tuple[object, ...] = (),
    *,
    require_target_existing: bool = False,
) -> object | None:
    """Pin the nearest existing object and prove its entire pathname chain.

    The retained descendant denies rename/delete for itself and, on Windows,
    consequently denies replacing any ancestor through which that open handle
    was resolved.  Comparing every component on both sides of acquisition
    closes the only gap before that denial is in force without requiring
    DELETE access to protected ancestors such as ``C:\\Users``.
    """
    root = Path(supplied_root)
    if not root.is_absolute():
        raise _MutationObservationError(
            f"delegated mutation root must be absolute: {root}"
        )
    if require_target_existing:
        try:
            root.lstat()
        except FileNotFoundError as error:
            raise _MutationObservationError(
                "delegated mutation root is absent; use the service-controlled "
                f"creation lifecycle: {root}"
            ) from error
    if os.name != "nt":
        return None

    current = Path(root.anchor)
    path_chain = [current]
    for part in root.parts[1:]:
        current /= part
        path_chain.append(current)
    observed_chain: list[tuple[Path, tuple[int, ...], os.stat_result]] = []
    for current in path_chain:
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            break
        if stat.S_ISLNK(metadata.st_mode) or bool(
            getattr(metadata, "st_file_attributes", 0) & 0x400
        ):
            raise _MutationObservationError(
                f"delegated mutation path is redirected or reparse: {current}"
            )
        if not stat.S_ISDIR(metadata.st_mode):
            raise _MutationObservationError(
                f"delegated mutation path is not a direct directory: {current}"
            )
        observed_chain.append((current, _path_component_identity(metadata), metadata))
    if not observed_chain:
        raise _MutationObservationError(
            f"delegated mutation path has no observable direct ancestor: {root}"
        )
    if require_target_existing and observed_chain[-1][0] != root:
        raise _MutationObservationError(
            "delegated mutation root is absent; use the service-controlled "
            f"creation lifecycle: {root}"
        )
    existing, _existing_identity, _existing_metadata = observed_chain[-1]
    pinned = next(
        (
            held
            for held in existing_guards
            if os.path.normcase(os.path.normpath(str(held.path)))
            == os.path.normcase(os.path.normpath(str(existing)))
        ),
        None,
    )
    acquired = pinned is None
    if acquired:
        try:
            expected_pinned_identity = identity_at_path(existing)
            pinned = pin_stable_direct_object(
                existing,
                "directory",
                allow_writes=True,
            )
        except ExactObjectOwnershipError as error:
            raise ContainmentStoreOwnershipError(
                "delegated mutation guard retained exact ownership",
                error,
            ) from error
        except (ExactObjectError, OSError) as error:
            raise _MutationObservationError(
                f"delegated mutation guard rejected {existing}: {error}"
            ) from error
    else:
        expected_pinned_identity = pinned.identity
    assert pinned is not None
    try:
        def identity_pair(value: object) -> tuple[int, int]:
            if isinstance(value, PinnedIdentity):
                return (value.volume_serial, value.file_id)
            if (
                type(value) is tuple
                and len(value) == 2
                and all(type(part) is int for part in value)
            ):
                return value
            raise _MutationObservationError(
                f"delegated mutation guard identity is malformed at {existing}"
            )

        pinned_identity_matches = (
            expected_pinned_identity == pinned.identity
            if isinstance(expected_pinned_identity, PinnedIdentity)
            else identity_pair(expected_pinned_identity)
            == identity_pair(pinned.identity)
        )
        if (
            pinned.identity is None
            or expected_pinned_identity is None
            or not pinned_identity_matches
            or identity_pair(identity_at_path(existing))
            != identity_pair(pinned.identity)
        ):
            raise _MutationObservationError(
                f"delegated mutation guard pinned a changed identity at {existing}"
            )
        for component, expected_identity, _metadata in observed_chain:
            metadata = component.lstat()
            if (
                stat.S_ISLNK(metadata.st_mode)
                or bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
                or not stat.S_ISDIR(metadata.st_mode)
                or _path_component_identity(metadata) != expected_identity
            ):
                raise _MutationObservationError(
                    "delegated mutation guard path changed during acquisition at "
                    f"{component}"
                )
    except BaseException as error:
        if acquired:
            try:
                pinned.close()
            except BaseException as close_error:
                if pinned.handle:
                    ownership = ExactObjectOwnershipError(
                        "delegated mutation guard validation retained a live handle: "
                        f"{close_error}",
                        owners=(
                            *(
                                error.owners
                                if isinstance(error, ExactObjectOwnershipError)
                                else ()
                            ),
                            RetainedObjectOwner(
                                RetainedObjectRole.VERIFICATION,
                                pinned,
                            ),
                        ),
                    )
                    raise ContainmentStoreOwnershipError(
                        "delegated mutation guard requires retained-handle resolution",
                        ownership,
                    ) from error
        if isinstance(error, ExactObjectOwnershipError):
            raise ContainmentStoreOwnershipError(
                "delegated mutation guard identity lookup retained exact ownership",
                error,
            ) from error
        raise
    return pinned if acquired else None


def _close_mutation_root_guards(
    guards: tuple[object, ...],
    primary: BaseException | None,
) -> None:
    unresolved = []
    close_errors: list[BaseException] = []
    for pinned in reversed(guards):
        try:
            pinned.close()
        except BaseException as error:
            close_errors.append(error)
        if getattr(pinned, "handle", 0):
            unresolved.append(pinned)
    close_owners = tuple(
        owner for error in close_errors
        if isinstance(error, ExactObjectOwnershipError)
        for owner in error.owners if owner.pinned.handle
    )
    if not unresolved and not close_owners:
        if close_errors and primary is not None and hasattr(primary, "add_note"):
            primary.add_note(
                "delegated mutation guard cleanup completed with errors: "
                + "; ".join(str(error) for error in close_errors)
            )
        elif close_errors:
            raise ContainmentServiceError(
                "delegated mutation guard cleanup failed: "
                + "; ".join(str(error) for error in close_errors)
            ) from close_errors[0]
        return
    prior_ownership = (
        primary.ownership
        if isinstance(primary, ContainmentStoreOwnershipError)
        else primary
    )
    ownership = union_retained_ownership(
        "delegated mutation guard retained live ownership after cleanup failure",
        prior=prior_ownership,
        owners=(
            *close_owners,
            *(
                RetainedObjectOwner(RetainedObjectRole.VERIFICATION, pinned)
                for pinned in unresolved
            ),
        ),
    )
    for error in close_errors:
        ownership.add_note(f"delegated mutation guard close failed: {error!r}")
    if isinstance(primary, ContainmentStoreOwnershipError):
        primary.ownership = ownership
        if hasattr(primary, "add_note"):
            primary.add_note("delegated mutation guard cleanup retained live ownership")
        raise primary
    raise ContainmentStoreOwnershipError(
        "delegated mutation guard requires retained-handle resolution",
        ownership,
    ) from primary


def _mutation_root_observation(
    supplied_root: Path,
    *,
    expected_projections: tuple = (),
) -> tuple[tuple[object, ...], ...] | None:
    """Take one stable, non-following inventory used to prove delegated mutation."""
    root = Path(supplied_root)
    if not root.is_absolute():
        raise _MutationObservationError(
            f"delegated mutation root must be absolute: {root}"
        )

    rows: list[tuple[object, ...]] = []
    retained: list[object] = []
    from .windows_junction import OwnedProjection
    if type(expected_projections) is not tuple or any(type(owner) is not OwnedProjection for owner in expected_projections):
        raise _MutationObservationError("expected projections must be exact retained owners")
    projections = {}
    owned_direct = {}
    for owner in expected_projections:
        try:
            owner.verify()
            link = owner.evidence.link_path
            link_beneath = _beneath(root, link) and link != root
            relevant_pins = tuple(
                pin
                for pin in owner.pins[1:]
                if pin.path == root or _beneath(root, pin.path)
            )
            if not link_beneath and not relevant_pins:
                raise ContainmentSafetyError("expected projection is outside observation or duplicated")
            if link_beneath:
                if link in projections:
                    raise ContainmentSafetyError(
                        "expected projection is outside observation or duplicated"
                    )
                projections[link] = owner
            owned_direct.update((pin.path, pin) for pin in relevant_pins)
        except (ContainmentSafetyError, OSError) as error:
            raise _MutationObservationError(str(error)) from error

    def verify_projections() -> None:
        for owner in expected_projections:
            try:
                owner.verify()
            except (ContainmentSafetyError, OSError) as error:
                raise _MutationObservationError(str(error)) from error

    def redirected(metadata: os.stat_result) -> bool:
        return stat.S_ISLNK(metadata.st_mode) or bool(
            getattr(metadata, "st_file_attributes", 0) & 0x400
        )

    def identity(metadata: os.stat_result) -> tuple[int, ...]:
        return (
            int(metadata.st_mode),
            int(metadata.st_dev),
            int(metadata.st_ino),
            int(metadata.st_size),
            int(metadata.st_mtime_ns),
            int(metadata.st_ctime_ns),
            int(getattr(metadata, "st_file_attributes", 0)),
        )

    def path_identity(path: Path, metadata: os.stat_result) -> tuple[int, ...]:
        # Ancestor siblings are outside this receipt boundary.  The observed
        # root and every descendant still retain the complete metadata/digest
        # drift checks below, while ancestors prove only stable direct identity.
        return identity(metadata) if path == root else _path_component_identity(metadata)

    def close_retained(
        primary: BaseException | None = None,
        *,
        completed_observation: tuple[tuple[object, ...], ...] | None = None,
    ) -> None:
        unresolved = []
        close_errors: list[BaseException] = []
        for pinned in reversed(retained):
            try:
                pinned.close()
            except BaseException as error:
                close_errors.append(error)
            if getattr(pinned, "handle", 0):
                unresolved.append(pinned)
        close_owners = tuple(
            owner for error in close_errors
            if isinstance(error, ExactObjectOwnershipError)
            for owner in error.owners if owner.pinned.handle
        )
        if unresolved or close_owners:
            prior_ownership = (
                primary.ownership
                if isinstance(primary, ContainmentStoreOwnershipError)
                else primary
            )
            ownership = union_retained_ownership(
                "stable delegated-effect observation retained live handles: "
                + "; ".join(str(error) for error in close_errors),
                prior=prior_ownership,
                owners=(
                    *close_owners,
                    *(
                        RetainedObjectOwner(
                            RetainedObjectRole.VERIFICATION,
                            pinned,
                        )
                        for pinned in unresolved
                    ),
                ),
            )
            for error in close_errors:
                ownership.add_note(f"observation handle close failed: {error!r}")
            message = (
                "stable delegated-effect observation requires retained-handle resolution"
            )
            if completed_observation is not None and primary is None:
                raise _MutationObservationOwnershipError(
                    message,
                    ownership,
                    completed_observation,
                )
            raise ContainmentStoreOwnershipError(message, ownership) from primary
        if close_errors and primary is None:
            raise ContainmentServiceError(
                "stable delegated-effect observation handle cleanup failed: "
                + "; ".join(str(error) for error in close_errors)
            ) from close_errors[0]

    pinned_direct = dict(owned_direct)

    def stable_pin(path: Path, kind: str) -> object | None:
        if os.name != "nt":
            return None
        if path in pinned_direct:
            verify_projections()
            return pinned_direct[path]
        for authority in _ACTIVE_RELOCATION_AUTHORITIES.get():
            pinned = authority.pin_for_path(path)
            if pinned is not None:
                return pinned
        if path in _ACTIVE_GUARDED_MUTATION_ROOTS.get():
            return None
        pinned = pin_stable_direct_object(path, kind)
        retained.append(pinned)
        pinned_direct[path] = pinned
        return pinned

    def require_direct_directory(
        path: Path,
        metadata: os.stat_result,
        *,
        retain: bool = False,
    ) -> object | None:
        if redirected(metadata):
            raise _MutationObservationError(
                f"delegated mutation path is redirected or reparse: {path}"
            )
        if not stat.S_ISDIR(metadata.st_mode):
            raise _MutationObservationError(
                f"delegated mutation path is not a direct directory: {path}"
            )
        return stable_pin(path, "directory") if retain else None

    current = Path(root.anchor)
    path_identities: list[tuple[Path, tuple[int, ...]]] = []
    try:
        path_chain: list[Path] = [current]
        for part in root.parts[1:]:
            current /= part
            path_chain.append(current)
        for index, component in enumerate(path_chain):
            try:
                metadata = component.lstat()
            except FileNotFoundError:
                close_retained()
                return (("absent",),)
            require_direct_directory(
                component,
                metadata,
                retain=(
                    index == len(path_chain) - 1
                    and root not in _ACTIVE_GUARDED_MUTATION_ROOTS.get()
                ),
            )
            path_identities.append((component, path_identity(component, metadata)))
        root_metadata = root.lstat()
    except _MutationObservationError as error:
        close_retained(error)
        raise
    except ExactObjectOwnershipError as error:
        close_retained(error)
        raise ContainmentStoreOwnershipError(
            "stable delegated-effect observation retained exact ownership",
            error,
        ) from error
    except (ExactObjectError, OSError):
        close_retained()
        return None
    except BaseException as error:
        close_retained(error)
        raise

    def file_digest(path: Path, metadata: os.stat_result) -> str | None:
        if not stat.S_ISREG(metadata.st_mode):
            return None
        if redirected(metadata):
            raise _MutationObservationError(
                f"delegated mutation entry is redirected or reparse: {path}"
            )
        if os.name == "nt":
            pinned = stable_pin(path, "file")
            assert pinned is not None
            data = read_pinned_file(pinned)
            after = path.lstat()
            if identity(after) != identity(metadata):
                raise OSError(
                    f"delegated mutation entry changed while hashing: {path}"
                )
            return hashlib.sha256(data).hexdigest()
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if (
                opened.st_mode,
                opened.st_dev,
                opened.st_ino,
                opened.st_size,
                opened.st_mtime_ns,
            ) != (
                metadata.st_mode,
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_size,
                metadata.st_mtime_ns,
            ):
                raise OSError(f"delegated mutation entry changed while opening: {path}")
            digest = hashlib.sha256()
            while block := os.read(descriptor, 1024 * 1024):
                digest.update(block)
        finally:
            os.close(descriptor)
        after = path.lstat()
        if (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) != (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        ):
            raise OSError(f"delegated mutation entry changed while hashing: {path}")
        return digest.hexdigest()

    def metadata_row(
        relative: str,
        path: Path,
        metadata: os.stat_result,
    ) -> tuple[object, ...]:
        kind = "directory" if stat.S_ISDIR(metadata.st_mode) else "file"
        digest = file_digest(path, metadata)
        pinned = stable_pin(path, kind)
        if os.name == "nt":
            native = identity_at_path(path) if pinned is None else pinned.identity
            if hasattr(native, "volume_serial"):
                volume_serial = int(native.volume_serial)
                file_id = int(native.file_id)
            else:
                volume_serial, file_id = (int(value) for value in native)
        else:
            volume_serial = int(metadata.st_dev)
            file_id = int(metadata.st_ino)
        return (
            relative,
            kind,
            volume_serial,
            file_id,
            int(metadata.st_mode),
            int(metadata.st_size),
            int(metadata.st_mtime_ns),
            int(metadata.st_ctime_ns),
            int(getattr(metadata, "st_file_attributes", 0)),
            digest,
        )

    try:
        rows.append(metadata_row(".", root, root_metadata))
    except _MutationObservationError as error:
        close_retained(error)
        raise
    except ExactObjectOwnershipError as error:
        close_retained(error)
        raise ContainmentStoreOwnershipError(
            "stable delegated-effect observation retained exact ownership",
            error,
        ) from error
    except (ExactObjectError, OSError):
        close_retained()
        return None
    except BaseException as error:
        close_retained(error)
        raise

    def scan(current: Path) -> tuple[tuple[str, Path, os.stat_result], ...]:
        with os.scandir(current) as found:
            ordered = sorted(
                found,
                key=lambda entry: (entry.name.casefold(), entry.name),
            )
            return tuple(
                (
                    entry.name,
                    Path(entry.path),
                    Path(entry.path).lstat(),
                )
                for entry in ordered
            )

    def visit(
        current: Path,
        relative: Path,
        expected: os.stat_result,
    ) -> None:
        entered = current.lstat()
        if identity(entered) != identity(expected):
            raise OSError(f"delegated mutation directory changed before scan: {current}")
        require_direct_directory(current, entered) if os.name != "nt" else None
        entries = scan(current)
        for name, child_path, metadata in entries:
            child_relative = relative / name
            if child_path in projections:
                verify_projections()
                owner = projections[child_path]
                link_identity = owner.pins[0].identity
                if hasattr(link_identity, "volume_serial"):
                    volume_serial = int(link_identity.volume_serial)
                    file_id = int(link_identity.file_id)
                else:
                    volume_serial, file_id = (
                        int(value) for value in link_identity
                    )
                rows.append(
                    (
                        child_relative.as_posix(),
                        "junction",
                        volume_serial,
                        file_id,
                        None,
                        None,
                        None,
                        None,
                        None,
                        owner.evidence.reparse_payload_sha256,
                    )
                )
                continue
            if redirected(metadata):
                raise _MutationObservationError(
                    f"delegated mutation entry is redirected or reparse: {child_path}"
                )
            if not stat.S_ISDIR(metadata.st_mode) and not stat.S_ISREG(metadata.st_mode):
                raise _MutationObservationError(
                    f"delegated mutation entry is not direct: {child_path}"
                )
            if stat.S_ISDIR(metadata.st_mode) and os.name == "nt":
                stable_pin(child_path, "directory")
            rows.append(metadata_row(child_relative.as_posix(), child_path, metadata))
            if stat.S_ISDIR(metadata.st_mode):
                visit(child_path, child_relative, metadata)
        rescanned = scan(current)
        before_rows = tuple((name, identity(metadata)) for name, _path, metadata in entries)
        after_rows = tuple((name, identity(metadata)) for name, _path, metadata in rescanned)
        verify_projections()
        if any(redirected(metadata) and path not in projections for _name, path, metadata in rescanned):
            raise _MutationObservationError(
                f"delegated mutation directory gained a reparse entry: {current}"
            )
        if before_rows != after_rows or identity(current.lstat()) != identity(expected):
            raise OSError(f"delegated mutation directory changed during scan: {current}")

    try:
        visit(root, Path(), root_metadata)
        verify_projections()
        for component, expected_identity in path_identities:
            metadata = component.lstat()
            if redirected(metadata):
                raise _MutationObservationError(
                    f"delegated mutation path became redirected or reparse: {component}"
                )
            if path_identity(component, metadata) != expected_identity:
                raise OSError(
                    f"delegated mutation path changed during observation: {component}"
                )
    except _MutationObservationError as error:
        close_retained(error)
        raise
    except ExactObjectOwnershipError as error:
        close_retained(error)
        raise ContainmentStoreOwnershipError(
            "stable delegated-effect observation retained exact ownership",
            error,
        ) from error
    except (ExactObjectError, OSError):
        close_retained()
        return None
    except BaseException as error:
        close_retained(error)
        raise
    completed = tuple(rows)
    close_retained(completed_observation=completed)
    return completed


def _projection_relevant_to_root(root: Path, owner) -> bool:
    try:
        paths = (
            owner.evidence.link_path,
            *(pin.path for pin in owner.pins[1:]),
        )
    except (AttributeError, TypeError) as error:
        raise _MutationObservationError(
            "expected projection evidence is malformed"
        ) from error
    return any(path == root or _beneath(root, path) for path in paths)


def _borrowed_projection_root_guards(
    expected_projections: tuple,
    roots: tuple[Path, ...],
) -> dict[Path, object]:
    """Borrow only exact persisted-projection parent pins for matching roots."""
    if type(expected_projections) is not tuple or any(
        type(owner) is not OwnedProjection for owner in expected_projections
    ):
        raise _MutationObservationError(
            "expected projections must be exact retained owners"
        )
    exact_by_spelling = {str(Path(root)): Path(root) for root in roots}
    borrowed: dict[Path, object] = {}
    for owner in expected_projections:
        try:
            owner.verify()
        except (ContainmentSafetyError, OSError) as error:
            raise _MutationObservationError(str(error)) from error
        target_parent = owner.pins[2]
        link_parent = owner.pins[3]
        expected = (
            (target_parent, Path(owner.evidence.target_path).parent),
            (link_parent, Path(owner.evidence.link_path).parent),
        )
        for pinned, evidence_path in expected:
            pinned_path = Path(pinned.path)
            if str(pinned_path) != str(evidence_path):
                raise _MutationObservationError(
                    "persisted projection parent path binding differs"
                )
            root = exact_by_spelling.get(str(pinned_path))
            if root is None:
                continue
            prior = borrowed.get(root)
            if prior is not None and prior is not pinned:
                raise _MutationObservationError(
                    f"multiple retained projection parents claim {root}"
                )
            borrowed[root] = pinned
    return borrowed


def _delegated_mutations_guarded(
    roots: tuple[Path, ...],
    operation: Callable[[], _V],
    *,
    expected_projections: tuple = (),
) -> _V:
    """Record delegated roots iff pre/post inventories prove they changed."""
    exact_roots = _unique_paths(roots)
    before_rows: list[tuple[tuple[object, ...], ...] | None] = []
    try:
        for root in exact_roots:
            owners = tuple(
                owner
                for owner in expected_projections
                if _projection_relevant_to_root(root, owner)
            )
            before_rows.append(
                _mutation_root_observation(root, expected_projections=owners)
                if owners
                else _mutation_root_observation(root)
            )
    except _MutationObservationError as error:
        raise ContainmentServiceError(
            f"delegated mutation effect observation rejected before operation: {error}"
        ) from error
    before = tuple(before_rows)
    unavailable_before = tuple(
        root for root, observation in zip(exact_roots, before, strict=True)
        if observation is None
    )
    if unavailable_before:
        joined = ", ".join(str(root) for root in unavailable_before)
        raise ContainmentServiceError(
            f"delegated mutation effect observation is unavailable before operation: {joined}"
        )

    def record_observed_changes() -> None:
        unavailable_after: list[Path] = []
        rejected_after: list[tuple[Path, BaseException]] = []
        ownership_after: list[tuple[Path, ContainmentStoreOwnershipError]] = []
        for root, prior in zip(exact_roots, before, strict=True):
            try:
                owners = tuple(
                    owner
                    for owner in expected_projections
                    if _projection_relevant_to_root(root, owner)
                )
                current = (_mutation_root_observation(root, expected_projections=owners)
                           if owners else _mutation_root_observation(root))
            except ContainmentStoreOwnershipError as error:
                completed = (
                    error.completed_observation
                    if isinstance(error, _MutationObservationOwnershipError)
                    else None
                )
                if completed is not None and prior != completed:
                    _current_effects().child_mutation_root(root)
                ownership_after.append((root, error))
                continue
            except _MutationObservationError as error:
                # A safe pre-observation cannot contain a reparse/non-direct
                # entry.  Seeing one afterwards proves the delegate changed
                # this mutation root even though the resulting tree is unsafe.
                _current_effects().child_mutation_root(root)
                rejected_after.append((root, error))
                continue
            if current is None:
                unavailable_after.append(root)
            elif prior != current:
                _current_effects().child_mutation_root(root)
        if ownership_after:
            primary_root, primary = ownership_after[0]
            primary.ownership = ExactObjectOwnershipError(
                "delegated mutation observations retained live ownership",
                owners=tuple(
                    owner
                    for _root, error in ownership_after
                    for owner in error.ownership.owners
                ),
            )
            if hasattr(primary, "add_note"):
                for root, error in ownership_after[1:]:
                    primary.add_note(
                        f"additional post-observation ownership at {root}: {error}"
                    )
                if rejected_after:
                    primary.add_note(
                        "additional delegated mutation observation rejection: "
                        + ", ".join(
                            f"{root} ({error})" for root, error in rejected_after
                        )
                    )
                if unavailable_after:
                    primary.add_note(
                        "additional delegated mutation observation unavailable: "
                        + ", ".join(str(root) for root in unavailable_after)
                    )
                primary.add_note(
                    f"primary post-observation ownership occurred at {primary_root}"
                )
            raise primary
        if rejected_after:
            joined = ", ".join(
                f"{root} ({error})" for root, error in rejected_after
            )
            raise ContainmentServiceError(
                "delegated mutation effect observation rejected after operation: "
                + joined
            ) from rejected_after[0][1]
        if unavailable_after:
            joined = ", ".join(str(root) for root in unavailable_after)
            raise ContainmentServiceError(
                f"delegated mutation effect observation is unavailable after operation: {joined}"
            )

    try:
        value = operation()
    except BaseException as operation_error:
        expected_projections = (*expected_projections, *getattr(operation_error, "projection_owners", ()))
        try:
            observation_error = None
            try:
                record_observed_changes()
            except BaseException as error:
                observation_error = error
                raise
            finally:
                if expected_projections:
                    _close_preparation_owners(expected_projections, "failed delegated projection observation", operation_error, observation_error)
        except ContainmentStoreOwnershipError as observation_error:
            detail = (
                "delegated mutation effect observation retained ownership after "
                f"failure: {observation_error}"
            )
            if isinstance(operation_error, ContainmentStoreOwnershipError):
                operation_error.ownership = ExactObjectOwnershipError(
                    "delegated mutation operation and observation retained live ownership",
                    owners=(
                        *operation_error.ownership.owners,
                        *observation_error.ownership.owners,
                    ),
                )
                if hasattr(operation_error, "add_note"):
                    operation_error.add_note(detail)
                    for note in getattr(observation_error, "__notes__", ()):
                        operation_error.add_note(note)
                raise operation_error
            if not isinstance(operation_error, Exception):
                raise
            if hasattr(observation_error, "add_note"):
                observation_error.add_note(
                    "delegated mutation operation failed before ownership was "
                    f"observed: {operation_error}"
                )
            raise observation_error from operation_error
        except ContainmentServiceError as observation_error:
            detail = str(observation_error).replace("after operation", "after failure")
            if hasattr(operation_error, "add_note"):
                operation_error.add_note(detail)
            if isinstance(operation_error, ContainmentStoreOwnershipError):
                raise operation_error
            if not isinstance(operation_error, Exception):
                raise
            raise ContainmentServiceError(
                f"delegated mutation failed ({operation_error}); {detail}"
            ) from operation_error
        raise
    if isinstance(value, ContainmentFixture):
        expected_projections = (*expected_projections, *value.projection_owners)
    observation_error = None
    try:
        record_observed_changes()
    except BaseException as error:
        observation_error = error
        raise
    finally:
        if expected_projections:
            _close_preparation_owners(expected_projections, "delegated projection observation", observation_error)
    return value


def _delegated_mutations(
    roots: tuple[Path, ...],
    operation: Callable[[], _V],
    *,
    expected_projections: tuple = (),
    guarded_operation: Callable[[Mapping[Path, object]], _V] | None = None,
) -> _V:
    exact_roots = _unique_paths(roots)
    borrowed_guards = _borrowed_projection_root_guards(
        expected_projections,
        exact_roots,
    )
    guards: list[object] = []
    primary: BaseException | None = None
    try:
        for root in exact_roots:
            guard = _mutation_root_guard(
                root,
                (*borrowed_guards.values(), *guards),
                require_target_existing=True,
            )
            if guard is not None:
                guards.append(guard)
        token = _ACTIVE_GUARDED_MUTATION_ROOTS.set(exact_roots)
        try:
            return _delegated_mutations_guarded(
                exact_roots,
                (
                    operation
                    if guarded_operation is None
                    else lambda: guarded_operation(
                        {
                            **borrowed_guards,
                            **{Path(guard.path): guard for guard in guards},
                        }
                    )
                ),
                expected_projections=expected_projections,
            )
        finally:
            _ACTIVE_GUARDED_MUTATION_ROOTS.reset(token)
    except BaseException as error:
        primary = error
        raise
    finally:
        _close_mutation_root_guards(tuple(guards), primary)


def _delegated_mutation(
    root: Path,
    operation: Callable[[], _V],
) -> _V:
    return _delegated_mutations((root,), operation)


def _direct_directory_present(path: Path) -> bool:
    """Return whether *path* is a present direct directory without following it."""
    candidate = Path(path)
    try:
        metadata = candidate.lstat()
    except FileNotFoundError:
        return False
    except OSError as error:
        raise _MutationObservationError(
            f"service-controlled mutation root cannot be inspected: {candidate}"
        ) from error
    if stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & 0x400
    ):
        raise _MutationObservationError(
            f"service-controlled mutation root is redirected or reparse: {candidate}"
        )
    if not stat.S_ISDIR(metadata.st_mode):
        raise _MutationObservationError(
            f"service-controlled mutation root is not a direct directory: {candidate}"
        )
    return True


def _nearest_existing_direct_directory(path: Path) -> Path:
    candidate = Path(path)
    while not _direct_directory_present(candidate):
        parent = candidate.parent
        if parent == candidate:
            raise _MutationObservationError(
                f"service-controlled mutation root has no direct ancestor: {path}"
            )
        candidate = parent
    return candidate


def _receipt_exact_directory_creation_failure(
    error: ExactDirectoryCreationError | ExactDirectoryCreationOwnershipError,
    target: Path,
    parent: object,
) -> None:
    """Retain only creation effects justified by native and exact-object evidence."""
    outcome = error.outcome
    if not (
        isinstance(outcome, ExactDirectoryCreationOutcome)
        and isinstance(parent, PinnedObject)
        and parent.handle
        and isinstance(parent.identity, PinnedIdentity)
        and outcome.path == target
        and target.parent == parent.path
        and outcome.parent_path == parent.path
        and outcome.parent_identity == parent.identity
    ):
        error.add_note(
            "directory-creation effect observation is unavailable: retained "
            "parent/target evidence does not bind the invoked direct child"
        )
        return
    if not outcome.proven_created:
        return
    if not outcome.has_valid_handle:
        # STATUS_SUCCESS + FILE_CREATED is evidence of a write even when the
        # native result failed to return an owner.  Do not reopen or clean up
        # the unowned pathname, and never enter the delegated mutation phase.
        if isinstance(error, ExactDirectoryCreationError):
            _current_effects().child_mutation_root(target)
        return
    if not isinstance(error, ExactDirectoryCreationOwnershipError):
        # A created candidate whose exact cleanup completed is ephemeral.
        return
    candidate = error.created_candidate
    if candidate is None:
        return
    try:
        if not (
            isinstance(candidate, PinnedObject)
            and candidate.handle
            and any(candidate is owned for owned in error.candidates)
            and candidate.path == target
            and isinstance(candidate.identity, PinnedIdentity)
            and candidate.identity.volume_serial == parent.identity.volume_serial
            and candidate.identity.attributes & 0x10
            and not candidate.identity.attributes & 0x400
            and identity_at_path(parent.path) == parent.identity
            and identity_at_path(target) == candidate.identity
        ):
            raise ExactObjectError(
                "surviving created candidate is not bound to its retained direct child"
            )
    except ExactObjectOwnershipError as observation_error:
        error.owners = union_retained_ownership(
            "directory-creation and effect observation retained exact ownership",
            prior=error,
            owners=observation_error.owners,
        ).owners
        error.add_note(
            "directory-creation effect observation retained additional ownership: "
            f"{observation_error}"
        )
        return
    except BaseException as observation_error:
        error.add_note(
            "directory-creation effect observation is unavailable: "
            f"{observation_error!r}"
        )
        return
    _current_effects().child_mutation_root(target)


def _delegated_mutations_with_created_root(
    created_root: Path,
    other_roots: tuple[Path, ...],
    preflight: Callable[[], _P],
    operation: Callable[[_P], _V],
    *,
    require_absent: bool = False,
    expected_projections: tuple = (),
    retained_preflight: Callable[
        [Mapping[Path, object], _P], object
    ] | None = None,
    verify_before_create: Callable[[_P], None] | None = None,
    bind_created_root: Callable[[_P, object], None] | None = None,
    verify_before_operation: Callable[[_P], None] | None = None,
) -> _V:
    """Create and retain each missing direct root component before delegating."""
    target = Path(created_root)
    exact_roots = _unique_paths((*other_roots, target))
    if not target.is_absolute():
        raise _MutationObservationError(
            f"service-controlled mutation root must be absolute: {target}"
        )
    if _direct_directory_present(target):
        if require_absent:
            raise _MutationObservationError(
                f"fresh service-controlled mutation root already exists: {target}"
            )
        def run_existing(guards_by_path: Mapping[Path, object]) -> _V:
            expected = preflight()
            manager = (
                retained_preflight(guards_by_path, expected)
                if retained_preflight is not None
                else nullcontext(expected)
            )
            with manager as prepared:
                authority_token = None
                if isinstance(prepared, _RetainedRelocation):
                    authority_token = _ACTIVE_RELOCATION_AUTHORITIES.set(
                        (*_ACTIVE_RELOCATION_AUTHORITIES.get(), prepared.authority)
                    )
                try:
                    if bind_created_root is not None:
                        bind_created_root(prepared, guards_by_path[target])
                    if verify_before_create is not None:
                        verify_before_create(prepared)
                    if verify_before_operation is not None:
                        verify_before_operation(prepared)
                    return operation(prepared)
                finally:
                    if authority_token is not None:
                        _ACTIVE_RELOCATION_AUTHORITIES.reset(authority_token)

        return _delegated_mutations(
            exact_roots,
            lambda: operation(preflight()),
            expected_projections=expected_projections,
            guarded_operation=run_existing,
        )

    borrowed_guards = _borrowed_projection_root_guards(
        expected_projections,
        exact_roots,
    )
    guards: list[object] = []
    primary: BaseException | None = None
    try:
        ancestor_guard = _mutation_root_guard(
            target,
            tuple(borrowed_guards.values()),
        )
        if ancestor_guard is not None:
            guards.append(ancestor_guard)
            ancestor = Path(ancestor_guard.path)
        else:
            ancestor = _nearest_existing_direct_directory(target)

        if _direct_directory_present(target):
            raise _MutationObservationError(
                "service-controlled mutation root appeared during guard "
                f"acquisition: {target}"
            )
        for root in exact_roots:
            if root == target:
                continue
            guard = _mutation_root_guard(
                root,
                (*borrowed_guards.values(), *guards),
                require_target_existing=True,
            )
            if guard is not None:
                guards.append(guard)

        guards_by_path = {
            **borrowed_guards,
            **{Path(guard.path): guard for guard in guards},
        }
        preflight_token = _ACTIVE_GUARDED_MUTATION_ROOTS.set(exact_roots)
        try:
            expected = preflight()
            manager = (
                retained_preflight(guards_by_path, expected)
                if retained_preflight is not None
                else nullcontext(expected)
            )
            with manager as prepared:
                authority_token = None
                if isinstance(prepared, _RetainedRelocation):
                    authority_token = _ACTIVE_RELOCATION_AUTHORITIES.set(
                        (*_ACTIVE_RELOCATION_AUTHORITIES.get(), prepared.authority)
                    )
                try:
                    if _direct_directory_present(target):
                        _current_effects().child_mutation_root(target)
                        raise _MutationObservationError(
                            "service-controlled preflight mutated its delegated root: "
                            f"{target}"
                        )
                    if verify_before_create is not None:
                        verify_before_create(prepared)

                    try:
                        relative = target.relative_to(ancestor)
                    except ValueError as error:
                        raise _MutationObservationError(
                            "service-controlled mutation root escapes its retained "
                            f"ancestor: {target}"
                        ) from error
                    if os.name != "nt" or not guards:
                        raise _MutationObservationError(
                            "exact service-controlled directory creation is unavailable "
                            "outside the Windows retained-handle authority path"
                        )
                    current = ancestor
                    creation_parent = guards[0]
                    for part in relative.parts:
                        current /= part
                        try:
                            guard = create_pinned_directory_child(current, creation_parent)
                        except FileExistsError as error:
                            raise _MutationObservationError(
                                "service-controlled mutation root appeared before exact "
                                f"creation: {current}"
                            ) from error
                        except ExactDirectoryCreationOwnershipError as error:
                            _receipt_exact_directory_creation_failure(
                                error,
                                current,
                                creation_parent,
                            )
                            raise ContainmentStoreOwnershipError(
                                "service-controlled directory creation retained exact ownership",
                                error,
                            ) from error
                        except ExactObjectOwnershipError as error:
                            raise ContainmentStoreOwnershipError(
                                "service-controlled directory creation retained exact ownership",
                                error,
                            ) from error
                        except ExactDirectoryCreationError as error:
                            _receipt_exact_directory_creation_failure(
                                error,
                                current,
                                creation_parent,
                            )
                            raise _MutationObservationError(
                                "service-controlled mutation root creation returned an "
                                f"unowned or rejected native outcome: {current}"
                            ) from error
                        except ExactObjectError as error:
                            raise _MutationObservationError(
                                "service-controlled mutation root creation was rejected: "
                                f"{current}"
                            ) from error
                        except OSError as error:
                            raise _MutationObservationError(
                                "service-controlled mutation root creation failed: "
                                f"{current}"
                            ) from error
                        guards.append(guard)
                        guards_by_path[Path(guard.path)] = guard
                        _current_effects().child_mutation_root(current)
                        creation_parent = guard

                    if bind_created_root is not None:
                        bind_created_root(prepared, creation_parent)
                    if verify_before_operation is not None:
                        verify_before_operation(prepared)
                    return _delegated_mutations_guarded(
                        exact_roots,
                        lambda: operation(prepared),
                        expected_projections=expected_projections,
                    )
                finally:
                    if authority_token is not None:
                        _ACTIVE_RELOCATION_AUTHORITIES.reset(authority_token)
        finally:
            _ACTIVE_GUARDED_MUTATION_ROOTS.reset(preflight_token)
    except BaseException as error:
        primary = error
        raise
    finally:
        _close_mutation_root_guards(tuple(guards), primary)


def _receipted(
    operation: Callable[..., _V],
) -> Callable[..., ContainmentServiceResult[_V]]:
    @wraps(operation)
    def invoke(*args, **kwargs):
        ledger = _EffectLedger()
        token = _ACTIVE_EFFECTS.set(ledger)
        try:
            value = operation(*args, **kwargs)
        except Exception as error:
            prior = getattr(error, "effects", ContainmentEffects())
            effects = ContainmentEffects.merged(ledger.freeze(), prior)
            if isinstance(error, ContainmentDecisionNotReady):
                raise ContainmentDecisionNotReady(
                    str(error),
                    effects=effects,
                ) from error
            if isinstance(error, ContainmentStoreOwnershipError):
                error.effects = effects
                raise
            cause = error.cause if isinstance(error, ContainmentOperationError) else error
            raise ContainmentOperationError(
                str(error),
                effects=effects,
                cause=cause,
            ) from error
        finally:
            _ACTIVE_EFFECTS.reset(token)
        return ContainmentServiceResult(value, ledger.freeze())

    return invoke


@dataclass(frozen=True)
class FixtureRecord:
    scenario: ContainmentScenario
    run_root: Path
    source_root: Path
    stage_root: Path
    archive_path: Path
    source_mods: Path
    stage_mods: Path
    source_lab_modlist: Path
    source_play_modlist: Path
    source_downloads: Path
    source_overwrite: Path
    bounded_game: Path
    stage_app: Path
    stage_downloads: Path
    stage_profiles: Path
    stage_overwrite: Path
    stage_cache: Path
    stage_logs: Path
    stage_environment: Mapping[str, str]
    external_watch_roots: tuple[tuple[str, Path], ...]
    before_names: tuple[str, ...]

    @property
    def executable(self) -> Path:
        return self.stage_app / "ModOrganizer.exe"


@dataclass(frozen=True)
class ScenarioEvidence:
    run_id: str
    scenario: ContainmentScenario
    protected_before: ProtectedState
    protected_after: ProtectedState
    watch_outcome: WatchOutcome
    mo2_process: ProcessEvidence | None
    source_integrity: IntegrityObservation
    stage_integrity: IntegrityObservation
    scenario_started: bool
    fresh_retry_eligible: bool
    projection_count: int
    projection_targets_verified: bool
    projection_observation_complete: bool
    projection_payload_bytes_copied: int
    production_backup_names: tuple[str, ...]
    production_observation_complete: bool
    staging_new_names: tuple[str, ...]
    staging_observation_complete: bool
    staging_output_names: tuple[str, ...]
    output_observation_complete: bool
    adopted_name: str | None
    adopted_tree: TreeIdentity | None
    adopted_integrity: IntegrityObservation | None
    source_restored_after_quarantine: bool
    safety_reasons: tuple[str, ...]
    incomplete_reasons: tuple[str, ...]


@dataclass(frozen=True)
class RecoveryCleanupEvidence:
    watch_outcome: WatchOutcome | None
    protected_after: ProtectedState
    source_integrity: IntegrityObservation
    stage_integrity: IntegrityObservation
    projection_count: int
    projection_targets_verified: bool
    projection_observation_complete: bool
    projection_payload_bytes_copied: int
    blockers: tuple[str, ...]


@dataclass(frozen=True)
class RecoveryProofEvidence:
    watch_outcome: WatchOutcome | None
    protected_after: ProtectedState
    blockers: tuple[str, ...]


@dataclass(frozen=True)
class _ProjectionEvidence:
    protected_after: ProtectedState
    projection_count: int
    projection_targets_verified: bool
    projection_observation_complete: bool
    production_backup_names: tuple[str, ...]
    production_observation_complete: bool
    staging_new_names: tuple[str, ...]
    staging_observation_complete: bool
    staging_output_names: tuple[str, ...]
    output_observation_complete: bool
    adopted_name: str | None
    adopted_tree: TreeIdentity | None
    adopted_integrity: IntegrityObservation | None
    source_restored_after_quarantine: bool
    safety_reasons: tuple[str, ...]
    incomplete_reasons: tuple[str, ...]


@dataclass(frozen=True)
class _RetryReplayEvidence:
    recovery: ScenarioRecovery
    journal: ScenarioJournal
    record: FixtureRecord
    result: ScenarioResult
    outcome: WatchOutcome


def evaluate_scenario(value: ScenarioEvidence) -> ScenarioResult:
    """Apply exact scenario policy with Failed > Incomplete > Passed precedence."""
    if not isinstance(value, ScenarioEvidence):
        raise ContainmentServiceError("scenario evaluation requires ScenarioEvidence")
    outcome = value.watch_outcome
    if outcome.run_id != value.run_id or outcome.scenario is not value.scenario:
        raise ContainmentServiceError("watch outcome is not bound to the scenario")

    violations = set(value.safety_reasons)
    incomplete = set(value.incomplete_reasons)
    if outcome.events:
        violations.add("forbidden-watcher-event")
    if value.protected_before != value.protected_after:
        violations.add("protected-state-changed")
    if outcome.evidence_completion is not WatchEvidenceCompletion.COMPLETED:
        incomplete.add("watch-evidence-incomplete")
    if value.mo2_process is None:
        incomplete.add("mo2-process-evidence-missing")
    else:
        expected_executable = Path(value.mo2_process.working_directory) / "ModOrganizer.exe"
        if (
            value.mo2_process.integrity is not IntegrityObservation.LOW
            or value.mo2_process.executable_version != "2.5.2.0"
            or tuple(value.mo2_process.arguments) != ("--profile", "ModLab - Lab")
            or not _same_path(value.mo2_process.executable, expected_executable)
        ):
            violations.add("mo2-process-evidence-invalid")
    if value.source_integrity not in {
        IntegrityObservation.MEDIUM,
        IntegrityObservation.HIGH,
        IntegrityObservation.SYSTEM,
    }:
        violations.add("source-integrity-invalid")
    if value.stage_integrity is not IntegrityObservation.LOW:
        violations.add("stage-integrity-invalid")
    if not value.projection_observation_complete:
        incomplete.add("projection-observation-incomplete")
    else:
        if value.projection_count <= 0:
            violations.add("projection-count-invalid")
        if not value.projection_targets_verified:
            violations.add("projection-target-changed")
    if value.projection_payload_bytes_copied != 0:
        violations.add("projection-payload-copied")
    if not value.production_observation_complete:
        incomplete.add("production-observation-incomplete")
    elif value.production_backup_names:
        violations.add("production-backup-created")
    if not value.source_restored_after_quarantine:
        violations.add("source-not-restored-after-quarantine")

    expected_name = _EXPECTED_NEW[value.scenario]
    expected_outputs = _EXPECTED_OUTPUTS.get(value.scenario, ())
    adoption = value.scenario in _EXPECTED_OUTPUTS
    if adoption:
        if not value.staging_observation_complete:
            incomplete.add("staging-observation-incomplete")
        elif value.staging_new_names != (expected_name,) and (
            value.staging_new_names or value.output_observation_complete
        ):
            violations.add("staging-new-folder-set-invalid")
        if not value.output_observation_complete:
            incomplete.add("output-observation-incomplete")
        elif value.staging_output_names != expected_outputs:
            violations.add("staging-output-set-invalid")
        if value.output_observation_complete and (
            value.adopted_name != expected_name
            or value.adopted_tree is None
            or value.adopted_integrity is not IntegrityObservation.MEDIUM
        ):
            violations.add("adoption-proof-invalid")
    elif value.scenario is ContainmentScenario.REPLACE_EXISTING:
        if not value.staging_observation_complete:
            incomplete.add("staging-observation-incomplete")
        if not value.output_observation_complete:
            incomplete.add("output-observation-incomplete")
        elif value.staging_output_names != _EXPECTED_REPLACEMENT_OUTPUTS:
            violations.add("staging-output-set-invalid")
        if (
            value.staging_new_names
            or value.adopted_name is not None
            or value.adopted_tree is not None
            or value.adopted_integrity is not None
        ):
            violations.add("unexpected-staging-output")
    elif not value.staging_observation_complete or not value.output_observation_complete:
        incomplete.add("staging-observation-incomplete")
    elif (
        value.staging_new_names
        or value.staging_output_names
        or value.adopted_name is not None
        or value.adopted_tree is not None
        or value.adopted_integrity is not None
    ):
        violations.add("unexpected-staging-output")

    probe = ScenarioResult(
        _SCHEMA_VERSION, value.run_id, value.scenario, ScenarioOutcome.INCOMPLETE,
        value.protected_before, value.protected_after, value.mo2_process,
        value.source_integrity, value.stage_integrity, watch_outcome_id_for(outcome),
        outcome.evidence_completion, value.scenario_started, False, outcome.events,
        value.projection_count, value.projection_targets_verified,
        value.projection_observation_complete,
        value.projection_payload_bytes_copied,
        tuple(sorted(set(value.production_backup_names))),
        value.production_observation_complete,
        tuple(sorted(set(value.staging_new_names))),
        value.staging_observation_complete,
        tuple(sorted(set(value.staging_output_names))),
        value.output_observation_complete,
        value.adopted_name,
        value.adopted_tree, value.adopted_integrity,
        value.source_restored_after_quarantine, ("classification-probe",),
    )
    typed_violations = set(deterministic_policy_violations(probe))
    violations.update(typed_violations)
    positive_breach = bool(outcome.events) or value.protected_before != value.protected_after
    positive_failure = positive_breach or (
        outcome.evidence_completion is WatchEvidenceCompletion.COMPLETED
        and bool(typed_violations)
    )
    if violations and positive_failure:
        scenario_outcome = ScenarioOutcome.FAILED
        reasons = tuple(sorted(violations | incomplete))
        retry = False
    elif violations or incomplete:
        scenario_outcome = ScenarioOutcome.INCOMPLETE
        reasons = tuple(sorted(violations | incomplete))
        retry = bool(
            value.fresh_retry_eligible
            and not value.scenario_started
            and outcome.evidence_completion is WatchEvidenceCompletion.INCOMPLETE
            and "controller-session-lost" in outcome.reason_codes
        )
    else:
        scenario_outcome = ScenarioOutcome.PASSED
        reasons = ()
        retry = False

    result = ScenarioResult(
        schema_version=_SCHEMA_VERSION,
        run_id=value.run_id,
        scenario=value.scenario,
        outcome=scenario_outcome,
        protected_before=value.protected_before,
        protected_after=value.protected_after,
        mo2_process=value.mo2_process,
        source_integrity=value.source_integrity,
        stage_integrity=value.stage_integrity,
        watch_outcome_id=watch_outcome_id_for(outcome),
        watch_evidence_completion=outcome.evidence_completion,
        scenario_started=value.scenario_started,
        fresh_retry_eligible=retry,
        watcher_events=outcome.events,
        projection_count=value.projection_count,
        projection_targets_verified=value.projection_targets_verified,
        projection_observation_complete=value.projection_observation_complete,
        projection_payload_bytes_copied=value.projection_payload_bytes_copied,
        production_backup_names=tuple(sorted(set(value.production_backup_names))),
        production_observation_complete=value.production_observation_complete,
        staging_new_names=tuple(sorted(set(value.staging_new_names))),
        staging_observation_complete=value.staging_observation_complete,
        staging_output_names=tuple(sorted(set(value.staging_output_names))),
        output_observation_complete=value.output_observation_complete,
        adopted_name=value.adopted_name,
        adopted_tree=value.adopted_tree,
        adopted_integrity=value.adopted_integrity,
        source_restored_after_quarantine=value.source_restored_after_quarantine,
        reasons=reasons,
    )
    try:
        scenario_result_to_bytes(result, outcome)
    except ContainmentFormatError as error:
        raise ContainmentServiceError(f"scenario evaluation is not serializable: {error}") from error
    return result


def adjudicate_results(
    scenario_results: tuple[ScenarioResult, ...],
    watch_outcomes: tuple[WatchOutcome, ...],
) -> CapabilityDecision:
    """Deep-bind results and retain the worst immutable result per scenario."""
    results = tuple(scenario_results)
    outcomes = tuple(watch_outcomes)
    if len(results) != len(outcomes):
        raise ContainmentServiceError("result/outcome evidence counts differ")
    if not results:
        raise ContainmentServiceError("adjudication requires a run ID")
    run_id = results[0].run_id
    grouped: dict[ContainmentScenario, list[tuple[ScenarioResult, WatchOutcome]]] = {}
    for result, outcome in zip(results, outcomes, strict=True):
        if result.run_id != run_id or outcome.run_id != run_id:
            raise ContainmentServiceError("adjudication evidence spans multiple runs")
        if result.scenario is not outcome.scenario:
            raise ContainmentServiceError("result/outcome scenario binding differs")
        try:
            scenario_result_to_bytes(result, outcome)
        except ContainmentFormatError as error:
            raise ContainmentServiceError(f"invalid adjudication evidence: {error}") from error
        grouped.setdefault(result.scenario, []).append((result, outcome))

    rank = {
        ScenarioOutcome.PASSED: 0,
        ScenarioOutcome.INCOMPLETE: 1,
        ScenarioOutcome.FAILED: 2,
    }
    selected = tuple(
        max(grouped[scenario], key=lambda pair: rank[pair[0].outcome])
        for scenario in ContainmentScenario
        if scenario in grouped
    )
    selected_results = tuple(item[0] for item in selected)
    selected_outcomes = tuple(item[1] for item in selected)
    identifiers = tuple(
        scenario_result_id_for(result, outcome)
        for result, outcome in selected
    )
    if any(result.outcome is ScenarioOutcome.FAILED for result in selected_results):
        verdict = CapabilityVerdict.REJECTED
        reasons = ("containment-breach",)
    elif (
        len(selected_results) != len(ContainmentScenario)
        or any(result.outcome is not ScenarioOutcome.PASSED for result in selected_results)
    ):
        verdict = CapabilityVerdict.INCOMPLETE
        reasons = ("scenario-evidence-incomplete",)
    else:
        verdict = CapabilityVerdict.SUPPORTED
        reasons = ()
    decision = CapabilityDecision(
        _SCHEMA_VERSION,
        run_id,
        _MECHANISM,
        verdict,
        identifiers,
        reasons,
    )
    try:
        capability_decision_to_bytes(decision, selected_results, selected_outcomes)
    except ContainmentFormatError as error:
        raise ContainmentServiceError(f"capability decision is invalid: {error}") from error
    return decision


def _effects_for_result(result: ScenarioResult) -> ContainmentEffects:
    """Describe protected changes retained in one exact scenario result."""
    source_changes: list[str] = []
    game_changes: list[str] = []
    source_fields = (
        (
            "SourceMods",
            result.protected_before.source_mods,
            result.protected_after.source_mods,
        ),
        (
            "LabProfile",
            result.protected_before.lab_profile_sha256,
            result.protected_after.lab_profile_sha256,
        ),
        (
            "PlayProfile",
            result.protected_before.play_profile_sha256,
            result.protected_after.play_profile_sha256,
        ),
        (
            "Downloads",
            result.protected_before.downloads,
            result.protected_after.downloads,
        ),
        (
            "Overwrite",
            result.protected_before.overwrite,
            result.protected_after.overwrite,
        ),
    )
    for label, before, after in source_fields:
        if before != after:
            source_changes.append(f"Protected {label} evidence changed")
    if result.protected_before.bounded_game != result.protected_after.bounded_game:
        game_changes.append("Protected BoundedGame evidence changed")
    for event in result.watcher_events:
        change = f"{event.root_kind}: {event.action} {event.relative_path}"
        if event.root_kind == "BoundedGame":
            game_changes.append(change)
        elif event.root_kind in {
            "SourceMods",
            "LabProfile",
            "PlayProfile",
            "Downloads",
            "Overwrite",
        }:
            source_changes.append(change)
    return ContainmentEffects(
        source_changes=tuple(source_changes),
        game_changes=tuple(game_changes),
        production_mo2_changes=tuple(
            f"Observed production backup: {name}"
            for name in result.production_backup_names
        ),
    )


def _record_result_effects(result: ScenarioResult) -> None:
    changes = _effects_for_result(result)
    _current_effects().changes(
        source=changes.source_changes,
        game=changes.game_changes,
        production_mo2=changes.production_mo2_changes,
    )


def load_result(
    validation_root: Path,
    run_id: str,
    scenario: ContainmentScenario,
) -> ContainmentServiceResult[ScenarioResult]:
    """Read one result without preparing storage and expose its retained changes."""
    result = ContainmentStore.open_readonly(validation_root).load_result(
        run_id,
        scenario,
    )
    return ContainmentServiceResult(result, _effects_for_result(result))


def load_decision(
    validation_root: Path,
    run_id: str,
) -> ContainmentServiceResult[CapabilityDecision]:
    """Read one decision without preparing storage or reporting mutations."""
    decision = ContainmentStore.open_readonly(validation_root).load_decision(run_id)
    return ContainmentServiceResult(decision, ContainmentEffects())


class _PreparationCommandRunner:
    """Receipt a real process as soon as its native creation has succeeded."""

    def run(self, args, *, on_created=None):
        with subprocess.Popen(list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as process:
            _current_effects().preparation_process(f"{args[0]} [{args[1]}] PID: {process.pid}")
            try:
                if on_created is not None:
                    on_created()
                stdout, stderr = process.communicate()
            except BaseException:
                process.kill()
                process.wait()
                raise
            return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)


@dataclass(frozen=True)
class _PreparationAdmission:
    budget: PathBudget
    fixtures: tuple[tuple[ContainmentScenario, FixtureAdmission], ...]

    @property
    def paths(self):
        return self.budget.paths

    def fixture(self, scenario: ContainmentScenario) -> FixtureAdmission:
        try:
            return dict(self.fixtures)[scenario]
        except KeyError as error:
            raise ContainmentServiceError(
                f"path budget: missing fixture admission for {scenario.value}"
            ) from error


def _preflight_preparation(source, artifact_id, steam, validation, run_id):
    """Read-only admission for all four fixtures and their service-owned paths.

    This does not qualify native MO2 runtime outputs. The returned policy's
    machine-readable scope is preparation-only and runtimeQualified is false.
    """
    if not isinstance(run_id, str) or not re.fullmatch(r"containment-run:[0-9a-f]{32}", run_id):
        raise ContainmentServiceError("path budget: invalid future run identity")
    root = Path(validation) / run_id.split(":")[1]
    rows = []
    fixtures = []
    for scenario in ContainmentScenario:
        fixture = root / "fixtures/v2" / scenario.value
        admission = preflight_containment_fixture(
            source,
            artifact_id,
            steam,
            validation,
            scenario,
            fixture_parent=fixture,
            command_runner=_PreparationCommandRunner(),
        )
        fixtures.append((scenario, admission))
        rows.extend(admission.paths)
        scenario_root = root / "scenarios" / scenario.value
        record_names = (
            "journal.json", "result.json", "retry.json", "retry-consumption.json",
            "launch.json", *(label + ".json" for label in PROTECTED_STATE_LABELS),
            "recovery.json",
        )
        for name in record_names:
            rows.extend(publication_paths(scenario_root / name, "containment-record"))
        rows.append(PlannedPath(
            "containment-record:replacement-candidate",
            "preparation-wide",
            str(mutable_replacement_part_path(
                scenario_root / "journal.json",
                "f" * 32,
            )),
        ))
        for name in ("request.json", "controller-claim.json", "worker-launch.json", "ready.json",
                     "events.ndjson", "terminal.json", "controller-loss.json", "outcome.json", "stop.token"):
            rows.extend(publication_paths(scenario_root / "watch" / name, "watcher-record"))
        for suffix in (".json", "-cleanup.json"):
            rows.extend(publication_paths(root / "preparation-projections" / (scenario.value + suffix), "preparation-projection"))
        # Preparation cleanup relocates the exact projection itself, not its target.
        rows.append(PlannedPath("preparation-recovery", "preparation-wide",
            str(root / ("preparation-quarantine-" + scenario.value) / "Protected Existing")))
        # Declared fixture payloads at all later quarantine destinations; no
        # arbitrary user-selected mod names or unknown native outputs admitted.
        members = {
            ContainmentScenario.NEW_FOLDER: _EXPECTED_OUTPUTS[ContainmentScenario.NEW_FOLDER],
            ContainmentScenario.MERGE_EXISTING: tuple(PROTECTED_MOD_FILES),
            ContainmentScenario.REPLACE_EXISTING: _EXPECTED_REPLACEMENT_OUTPUTS,
            ContainmentScenario.FOMOD_DEPENDENCY: _EXPECTED_OUTPUTS[ContainmentScenario.FOMOD_DEPENDENCY],
        }[scenario]
        for quarantine in (root / "quarantine" / scenario.value,
                           root / "quarantine" / ("Recovery-" + scenario.value)):
            destination = quarantine / _EXPECTED_NEW[scenario]
            rows.append(PlannedPath(
                "fixture-quarantine",
                "preparation-wide",
                str(destination),
            ))
            for member in members:
                rows.append(PlannedPath(
                    "fixture-quarantine:member",
                    "preparation-wide",
                    str(destination / member),
                ))
    for name in ("intent.json", "request.json", "decision.json", "preparation-attempt.json",
                 "preparation-failure.json", "preparation-recovery.json", "preparation-replacement.json",
                 "preparation-startup-initialized.json", "preparation-startup-failure.json"):
        rows.extend(publication_paths(root / name, "containment-run-record"))
    return _PreparationAdmission(admit_paths(rows), tuple(fixtures))


def _repeat_fixture_admission(
    expected: FixtureAdmission,
    source: Path,
    artifact_id: str,
    steam: Path,
    validation: Path,
    scenario: ContainmentScenario,
    fixture_parent: Path,
) -> FixtureAdmission:
    observed = preflight_containment_fixture(
        source,
        artifact_id,
        steam,
        validation,
        scenario,
        fixture_parent=fixture_parent,
        command_runner=_PreparationCommandRunner(),
    )
    if observed != expected:
        raise ContainmentServiceError("fixture immutable input admission changed")
    return observed


@_receipted
def prepare_run(
    source_workspace: Path,
    mo2_artifact_id: str,
    steam_root: Path,
    validation_root: Path,
    *,
    retry_of: ScenarioRecovery | None = None,
    preparation_recovery: PreparationRecovery | None = None,
    _admitted_run_id: str | None = None,
    _admitted_preparation: _PreparationAdmission | None = None,
) -> str:
    """Create four independent disposable fixtures and one immutable request."""
    source = Path(source_workspace).expanduser().absolute()
    validation = Path(validation_root).expanduser().absolute()
    expected_validation = workspace_layout(source).mo2_containment_validation
    if not _same_path(validation, expected_validation):
        raise ContainmentServiceError("validation root must be the workspace containment root")
    steam = Path(steam_root).expanduser().absolute()
    run_id = _admitted_run_id or ("containment-run:" + uuid.uuid4().hex)
    # Read-only complete future operation admission precedes store creation,
    # cleanup, scenario retry consumption, or one-use preparation replacement.
    observed_admission = _preflight_preparation(
        source, mo2_artifact_id, steam, validation, run_id
    )
    if (
        _admitted_preparation is not None
        and observed_admission != _admitted_preparation
    ):
        raise ContainmentServiceError("preparation immutable input admission changed")
    admission = observed_admission
    store = _effect_store(validation)
    command_fingerprint = _command_fingerprint(source, mo2_artifact_id, steam)
    with store.command_lock(command_fingerprint):
        repeated_admission = _preflight_preparation(
            source, mo2_artifact_id, steam, validation, run_id
        )
        if repeated_admission != admission:
            raise ContainmentServiceError("preparation immutable input admission changed")
        predecessor_run_ids = _prepare_predecessor_run_ids(
            store,
            command_fingerprint,
            retry_of,
            preparation_recovery=preparation_recovery,
        )
        if preparation_recovery is not None and retry_of is not None:
            raise ContainmentServiceError("scenario and preparation replacements are distinct")
        retry_binding = None
        if retry_of is not None:
            if not isinstance(retry_of, ScenarioRecovery):
                raise ContainmentServiceError("retry_of must be an exact ScenarioRecovery")
            try:
                retry_binding = store.consume_retry_authority(
                    retry_of,
                    store.recovery_id_for(retry_of),
                    command_fingerprint,
                    run_id,
                )
            except ContainmentStoreError as error:
                raise ContainmentServiceError(
                    f"retry authority cannot be consumed: {error}"
                ) from error
        intent = {
            "schemaVersion": _SCHEMA_VERSION,
            "runId": run_id,
            "mechanism": _MECHANISM,
            "sourceWorkspace": str(source),
            "mo2ArtifactId": mo2_artifact_id,
            "steamRoot": str(steam),
            "commandFingerprint": command_fingerprint,
            "predecessorRunIds": list(predecessor_run_ids),
            "retryOf": retry_binding,
        }
        # Source/native identity acquisition has no fresh-run effects and must
        # precede consuming the old one-use preparation authority.
        attempt = _new_preparation_attempt(run_id, _intent_id_for(intent), command_fingerprint, preparation_recovery)
        if preparation_recovery is not None:
            with store._run_lock(preparation_recovery.run_id), _retained_preparation_recovery(store, preparation_recovery):
                try:
                    old_attempt = store.load_preparation_attempt(preparation_recovery.run_id)
                except ContainmentStoreNotFound:
                    old_attempt = None
                proof = _preparation_absence_proof(old_attempt)
                intent_write = _initialize_preparation_successor(store, preparation_recovery, intent, attempt, proof)
        else:
            intent_write = store.write_intent(run_id, intent)
            store.write_preparation_attempt(attempt)
        fixture_root = store.run_path(run_id) / "fixtures" / "v2"
        fixtures: list[ContainmentFixture] = []
        projection_ids = []
        def retain_projection(scenario, owner):
            owner.verify()
            projection = PreparationProjection(run_id, intent_write.content_id,
                command_fingerprint, record_id(attempt), scenario.value,
                tuple(ProjectionIdentity(str(pin.path), *pin.identity) for pin in owner.pins),
                owner.payload.hex())
            store.write_preparation_projection(projection)
            projection_ids.append(record_id(projection))
        try:
            # Receipts use a separate direct parent: the fixture operation holds
            # DELETE-denying ancestor guards through post-observation.
            _delegated_mutations_with_created_root(
                store.run_path(run_id) / "preparation-projections", (),
                lambda: None, lambda _prepared: None, require_absent=True)
            for scenario in ContainmentScenario:
                fixture_parent = fixture_root / scenario.value
                expected_fixture_admission = admission.fixture(scenario)
                fixtures.append(
                    _delegated_mutations_with_created_root(
                        fixture_parent,
                        (),
                        partial(
                            _repeat_fixture_admission,
                            expected_fixture_admission,
                            source,
                            mo2_artifact_id,
                            steam,
                            validation,
                            scenario,
                            fixture_parent=fixture_parent,
                        ),
                        lambda prepared, scenario=scenario, fixture_parent=fixture_parent: (
                            prepare_containment_fixture(
                                source,
                                mo2_artifact_id,
                                steam,
                                validation,
                                scenario,
                                fixture_parent=fixture_parent,
                                command_runner=_PreparationCommandRunner(),
                                retain_projection_owners=True,
                                on_projection_created=partial(retain_projection, scenario),
                                expected_admission=prepared,
                            )
                        ),
                        require_absent=True,
                    )
                )
            records = tuple(_fixture_record(fixture, steam) for fixture in fixtures)
            document = {
                **intent,
                "intentId": intent_write.content_id,
                "scenarios": [_record_document(record) for record in records],
            }
            store.write_request(run_id, document)
        except BaseException as error:
            # Never rewrite intent/request or reinterpret this as a scenario result.
            if not _path_exists_no_follow(store.request_path(run_id)):
                failure = PreparationFailure(run_id, intent_write.content_id,
                    command_fingerprint, record_id(attempt),
                    tuple(str(path) for path in _current_effects().freeze().written_paths),
                    tuple(projection_ids), f"{type(error).__name__}: {error}", False)
                try:
                    store.write_preparation_failure(failure)
                except BaseException as publication_error:
                    error.add_note(f"preparation failure evidence publication refused: {publication_error}")
                    if isinstance(publication_error, ContainmentStoreOwnershipError):
                        raise publication_error from error
            raise
    return run_id


def _startup_record_fields(replacement):
    return (replacement.run_id, replacement.intent_id, replacement.command_fingerprint,
            record_id(replacement), replacement.consumed_by_run_id)


def _initialize_preparation_successor(store, recovery, intent, attempt, proof):
    """Reserve exact lineage atomically before fallible successor publication.

    Startup evidence lives with the reservation, so an uncertain successor
    namespace is never adopted or rewritten to record its initialization error.
    """
    reservation = PreparationReplacementV2(
        recovery.run_id, recovery.intent_id, recovery.command_fingerprint,
        record_id(recovery), attempt.run_id, proof,
        successor_intent=json.dumps(intent, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
        successor_attempt=attempt)
    phase = "consume"
    try:
        store.consume_preparation_replacement(recovery, attempt.run_id,
            process_proof=proof, reservation=reservation)
        phase = "intent"
        with store._run_lock(attempt.run_id):
            if _path_exists_no_follow(store.run_path(attempt.run_id)):
                raise ContainmentServiceError("reserved successor namespace is no longer fresh")
            written = store.write_intent(attempt.run_id, intent, require_new=True)
            phase = "attempt"
            store.write_preparation_attempt(attempt, require_new=True)
            phase = "startup-initialized"
            store.write_preparation_startup_initialized(PreparationStartupInitialized(*_startup_record_fields(reservation)))
        return written
    except BaseException as error:
        try:
            # Exact observed reservation is evidence, not a claim that an
            # uncertain publication succeeded or that its objects are ours.
            observed = store.load_preparation_replacement(recovery.run_id)
            if observed != reservation:
                raise ContainmentServiceError("startup reservation is absent, substituted or ambiguous")
            store.write_preparation_startup_failure(PreparationStartupFailure(
                *_startup_record_fields(reservation), "CaughtFailure", phase,
                f"observed {type(error).__name__} during {phase}: {error}",
                tuple(str(path) for path in _current_effects().freeze().written_paths), ()))
        except BaseException as publication_error:
            error.add_note(f"startup failure disposition publication refused: {publication_error}")
            if isinstance(publication_error, ContainmentStoreOwnershipError):
                if isinstance(error, ContainmentStoreOwnershipError):
                    error.ownership = union_retained_ownership(
                        "startup and failure publication retain live ownership",
                        prior=error.ownership, owners=publication_error.ownership.owners)
                    raise error from publication_error
                raise publication_error from error
        raise


def _new_preparation_attempt(run_id, intent_id, fingerprint, recovery):
    source = Path(__file__).resolve().parents[2]
    commit = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    tree = subprocess.run(["git", "-C", str(source), "rev-parse", commit + "^{tree}"], check=True, capture_output=True, text=True).stdout.strip()
    digest = hashlib.sha256()
    for path in sorted((source / "modlab").rglob("*.py")):
        name, data = path.relative_to(source).as_posix().encode(), path.read_bytes()
        digest.update(len(name).to_bytes(8, "big") + name + len(data).to_bytes(8, "big") + data)
    handle, created = _windows_watch._open_process_identity(os.getpid())
    if _windows_watch._close_controller_handle(handle, "preparation controller", source):
        raise ContainmentServiceError("preparation controller identity handle could not be closed")
    return PreparationAttempt(run_id, intent_id, fingerprint, commit, tree, digest.hexdigest(),
        "preparation-session:" + uuid.uuid4().hex, os.getpid(), created,
        None if recovery is None else record_id(recovery))


def _require_early_preparation(store, run_id):
    # lstat: a substituted link, malformed file or any started evidence also blocks.
    for path in (store.request_path(run_id), store.decision_path(run_id)):
        if _path_exists_no_follow(path):
            raise ContainmentServiceError("prepared or scenario-bearing run cannot use preparation recovery")
    scenarios = store.run_path(run_id) / "scenarios"
    if _path_exists_no_follow(scenarios):
        store._require_existing_direct_directory(scenarios, "preparation scenarios")
        if tuple(scenarios.iterdir()):
            raise ContainmentServiceError("scenario-bearing run cannot use preparation recovery")


def _preparation_absence_proof(attempt):
    proof = ()
    if attempt is not None:
        if not native_process_absent(attempt.controller_pid, attempt.controller_creation_time):
            raise ContainmentServiceError("old preparing controller is live, reused or uncertain")
        proof = (PreparationProcess(attempt.controller_pid, attempt.controller_creation_time, "preparing-controller", "Absent"),)
    first = native_preparation_process_inventory()
    second = native_preparation_process_inventory()
    if first != second:
        raise ContainmentServiceError("native preparation process inventory changed")
    return (*proof, *second)


def _require_supported_legacy_preparation(store, run_id, intent):
    """Recognize only the retained v1 intent-only/NewFolder failure shape.

    This policy proves current abandonment, not the old exit code or Git source.
    No legacy pathname observation confers object creation or deletion ownership.
    """
    if intent["retryOf"] is not None or intent["predecessorRunIds"]:
        raise ContainmentServiceError("unsupported legacy preparation lineage")
    root = store.run_path(run_id)
    allowed = {"intent.json", "fixtures", "scenarios", "quarantine", "preparation-failure.json", "preparation-recovery.json", "preparation-replacement.json"}
    if set(_direct_names(root)) - allowed:
        raise ContainmentServiceError("unknown legacy preparation run shape")
    for name in ("scenarios", "quarantine"):
        path = root / name
        if _path_exists_no_follow(path):
            store._require_existing_direct_directory(path, "legacy " + name)
            if tuple(path.iterdir()):
                raise ContainmentServiceError("legacy preparation has scenario or cleanup state")
    fixtures = root / "fixtures"
    store._require_existing_direct_directory(fixtures, "legacy fixtures")
    if _direct_names(fixtures) != ("NewFolder",):
        raise ContainmentServiceError("unsupported legacy fixture shape")
    store._require_existing_direct_directory(fixtures / "NewFolder", "legacy NewFolder fixture")


@_receipted
def recover_preparation(validation_root: Path, run_id: str) -> PreparationRecovery:
    """Cleanup-only preparation recovery; no request, result, decision or launch."""
    store = ContainmentStore.open_readonly(validation_root)
    store._effect_recorder = _current_effects().write
    intent = store.load_intent(run_id)
    fingerprint = _intent_command_fingerprint(intent, run_id)
    with store.command_lock(fingerprint), store._run_lock(run_id):
        _require_early_preparation(store, run_id)
        try:
            existing = store.load_preparation_recovery(run_id)
        except ContainmentStoreNotFound:
            existing = None
        if existing is not None:
            with _retained_preparation_recovery(store, existing):
                try:
                    prior_attempt = store.load_preparation_attempt(run_id)
                except ContainmentStoreNotFound:
                    prior_attempt = None
                _preparation_absence_proof(prior_attempt)
                _reconcile_preparation_startup(store, run_id)
            return existing
        try:
            attempt = store.load_preparation_attempt(run_id)
        except ContainmentStoreNotFound:
            attempt = None
        if attempt is None:
            _require_supported_legacy_preparation(store, run_id, intent)
        try:
            failure = store.load_preparation_failure(run_id)
        except ContainmentStoreNotFound:
            if attempt is not None:
                raise ContainmentServiceError("preparation was interrupted without final effect receipts; cleanup ownership is incomplete")
            _preparation_absence_proof(None)
            failure = PreparationFailure(run_id, _intent_id_for(intent), fingerprint,
                None, (), (), "legacy abandonment under supported-preparation-python-git-tar-mo2-absence-v1; no historic source/exit or creation ownership attested", True)
            store.write_preparation_failure(failure)
        proof = _preparation_absence_proof(attempt)
        if failure.legacy:
            recovery = PreparationRecovery(run_id, failure.intent_id, fingerprint, record_id(failure),
                proof, (), (str(store.run_path(run_id) / "fixtures"),),
                (), True)
            store.write_preparation_recovery(recovery)
            return recovery
        projections = []
        for scenario in ContainmentScenario:
            try:
                projections.append(store.load_preparation_projection(run_id, scenario.value))
            except ContainmentStoreNotFound:
                pass
        if tuple(record_id(item) for item in projections) != failure.projection_ids:
            raise ContainmentServiceError("preparation failure projection receipts are incomplete or inconsistent")
        cleaned = []
        cleanup_ids = []
        from . import windows_junction as junction
        owners = []
        primary = None
        try:
            for projection in projections:
                owners.append(_pin_preparation_projection(store, projection))
            fixtures = store.run_path(run_id) / "fixtures"
            if _path_exists_no_follow(fixtures):
                observation = _mutation_root_observation(fixtures, expected_projections=tuple(owners))
                if observation is None:
                    raise ContainmentServiceError("preparation fixture state could not be observed exactly")
            for projection, owner in zip(projections, owners, strict=True):
                cleaned.extend(_recover_preparation_projection(store, projection, owner))
                cleanup_ids.append(record_id(store.load_preparation_cleanup(run_id, projection.scenario)))
        except BaseException as error:
            primary = error
            raise
        finally:
            _close_preparation_owners(tuple(owners), "preparation recovery owners", primary)
        # Reprove after cleanup, before issuing the single immutable authority.
        proof = _preparation_absence_proof(attempt)
        recovery = PreparationRecovery(run_id, failure.intent_id, fingerprint, record_id(failure),
            proof, tuple(cleaned), (str(store.run_path(run_id) / "fixtures"),), (), True,
            cleanup_ids=tuple(cleanup_ids))
        store.write_preparation_recovery(recovery)
        return recovery


def _close_preparation_owners(owners, label, *errors):
    from . import windows_junction as junction
    retained = tuple(error if isinstance(error, (junction.JunctionOwnershipError, ExactObjectOwnershipError))
        else getattr(error, "ownership", None) for error in errors)
    junction._close_retained_owners((*retained, *owners), label)


def _pin_preparation_projection(store, projection):
    from . import windows_junction as junction
    fixture = store.run_path(projection.run_id) / "fixtures" / projection.scenario
    expected_link = fixture / "stage-workspace/tools/mo2/skyrim-se-ae/mods/Protected Existing"
    expected_target = fixture / "source-workspace/tools/mo2/skyrim-se-ae/mods/Protected Existing"
    expected = (expected_link, expected_target, expected_target.parent, expected_link.parent)
    actual = tuple(Path(item.path) for item in projection.identities)
    if actual != expected:
        fixture = store.run_path(projection.run_id) / "fixtures" / "v2" / projection.scenario
        expected_link = fixture / "t/tools/mo2/skyrim-se-ae/mods/Protected Existing"
        expected_target = fixture / "s/tools/mo2/skyrim-se-ae/mods/Protected Existing"
        expected = (expected_link, expected_target, expected_target.parent, expected_link.parent)
    if tuple(Path(item.path) for item in projection.identities) != expected:
        raise ContainmentServiceError("preparation projection is not the exact confined fixture link")
    pins = []
    try:
        for index, item in enumerate(projection.identities):
            pins.append(junction._pin_object(Path(item.path), desired_access=junction._DELETE | junction._FILE_READ_ATTRIBUTES,
                expected_identity=(item.volume, item.file_id), allow_reparse=index == 0,
                share_mode=junction._FILE_SHARE_READ | junction._FILE_SHARE_WRITE))
        owner = junction.OwnedProjection(junction._inspect_junction_handle(expected_link, pins[0].handle), tuple(pins), bytes.fromhex(projection.payload_hex))
        owner.verify()
        return owner
    except BaseException as error:
        _close_preparation_owners(tuple(pins), "preparation cleanup", error)
        raise


def _recover_preparation_projection(store, projection, owner):
    from . import windows_junction as junction
    owner.verify()
    quarantine = store.run_path(projection.run_id) / ("preparation-quarantine-" + projection.scenario)
    def record_created_parent(_prepared):
        volume, file_id = junction._identity_at_path(quarantine)
        cleanup = PreparationCleanup(projection.run_id, projection.intent_id, projection.command_fingerprint,
            record_id(projection), projection.scenario, str(quarantine / owner.evidence.link_path.name),
            ProjectionIdentity(str(quarantine), volume, file_id))
        store.write_preparation_cleanup(cleanup)
        return cleanup
    cleanup = _delegated_mutations_with_created_root(quarantine, (), lambda: None,
        record_created_parent, require_absent=True)
    parent = junction._pin_parent_directory(quarantine)
    primary = None
    try:
        if parent.identity != (cleanup.destination_parent.volume, cleanup.destination_parent.file_id):
            raise ContainmentServiceError("created cleanup parent was substituted")
        junction._rename_pinned_object(owner.pins[0], Path(cleanup.destination), parent)
        if _path_exists_no_follow(owner.evidence.link_path):
            raise ContainmentServiceError("cleaned projection source path reappeared")
    except BaseException as error:
        primary = error
        raise
    finally:
        # An error after the native move still reports the observed real effect.
        if owner.pins[0].path != owner.evidence.link_path:
            _current_effects().child_mutation_root(owner.evidence.link_path.parent)
            _current_effects().write(owner.pins[0].path)
        _close_preparation_owners((parent,), "preparation cleanup parent", primary)
    return (str(owner.pins[0].path),)


@contextmanager
def _retained_preparation_recovery(store, recovery):
    """Hold every cleaned object/parent through absence proof and consumption."""
    from . import windows_junction as junction
    if store.load_preparation_recovery(recovery.run_id) != recovery:
        raise ContainmentServiceError("preparation recovery is not the exact stored value")
    failure = store.load_preparation_failure(recovery.run_id)
    pins = []
    guards = []
    primary = None
    try:
        _require_early_preparation(store, recovery.run_id)
        if failure.legacy:
            _require_supported_legacy_preparation(store, recovery.run_id, store.load_intent(recovery.run_id))
            guard = _mutation_root_guard(store.run_path(recovery.run_id) / "fixtures/NewFolder", require_target_existing=True)
            if guard is not None:
                guards.append(guard)
            if recovery.cleaned or recovery.cleanup_ids:
                raise ContainmentServiceError("legacy recovery cannot claim cleanup ownership")
        else:
            projections = []
            cleanups = []
            destinations = []
            for scenario in ContainmentScenario:
                try:
                    projection = store.load_preparation_projection(recovery.run_id, scenario.value)
                except ContainmentStoreNotFound:
                    continue
                cleanup = store.load_preparation_cleanup(recovery.run_id, scenario.value)
                projections.append(record_id(projection))
                cleanups.append(record_id(cleanup))
                destinations.append(cleanup.destination)
                expected_parent = store.run_path(recovery.run_id) / ("preparation-quarantine-" + scenario.value)
                if Path(cleanup.destination_parent.path) != expected_parent or Path(cleanup.destination) != expected_parent / "Protected Existing":
                    raise ContainmentServiceError("cleanup destination escaped its exact run")
                for index, identity in enumerate(projection.identities):
                    path = Path(cleanup.destination) if index == 0 else Path(identity.path)
                    junction._require_direct_components(path, allow_final_reparse=index == 0)
                    pinned = junction._pin_object(path, desired_access=junction._DELETE | junction._FILE_READ_ATTRIBUTES,
                        expected_identity=(identity.volume, identity.file_id), allow_reparse=index == 0,
                        share_mode=junction._FILE_SHARE_READ | junction._FILE_SHARE_WRITE)
                    pins.append(pinned)
                    information = junction._handle_information(pinned.handle, path)
                    if (not information.dwFileAttributes & junction._FILE_ATTRIBUTE_DIRECTORY
                            or junction._identity_at_path(path) != pinned.identity):
                        raise ContainmentServiceError("cleanup verification identity or directory type differs")
                    if index == 0:
                        if junction._read_reparse_payload_handle(pinned.handle, path) != bytes.fromhex(projection.payload_hex):
                            raise ContainmentServiceError("cleaned projection payload was substituted")
                        if _path_exists_no_follow(Path(identity.path)):
                            raise ContainmentServiceError("old projection path reappeared after cleanup")
                parent = cleanup.destination_parent
                pins.append(junction._pin_object(Path(parent.path), desired_access=junction._DELETE | junction._FILE_READ_ATTRIBUTES,
                    expected_identity=(parent.volume, parent.file_id), allow_reparse=False,
                    share_mode=junction._FILE_SHARE_READ | junction._FILE_SHARE_WRITE))
            if (tuple(projections) != failure.projection_ids or tuple(cleanups) != recovery.cleanup_ids
                    or tuple(destinations) != recovery.cleaned):
                raise ContainmentServiceError("cleanup record set does not match the recovered failure")
            for pinned in pins:
                if junction._identity_at_path(pinned.path) != pinned.identity:
                    raise ContainmentServiceError("cleanup identity changed before consumption")
        yield
    except BaseException as error:
        primary = error
        raise
    finally:
        _close_mutation_root_guards(tuple(guards), primary)
        _close_preparation_owners(tuple(pins), "preparation replacement verification", primary)


@_receipted
def restart_preparation(validation_root: Path, run_id: str) -> str:
    store = ContainmentStore.open_readonly(validation_root)
    intent = store.load_intent(run_id)
    future_run_id = "containment-run:" + uuid.uuid4().hex
    admission = _preflight_preparation(
        Path(intent["sourceWorkspace"]),
        str(intent["mo2ArtifactId"]),
        Path(intent["steamRoot"]),
        Path(validation_root),
        future_run_id,
    )
    receipt = recover_preparation(validation_root, run_id)
    _record_preparation_effects(receipt.effects)
    recovery = receipt.value
    if not recovery.fresh_run_permitted:
        raise ContainmentServiceError("preparation replacement refused: " + "; ".join(recovery.blockers))
    store = ContainmentStore.open_readonly(validation_root)
    intent = store.load_intent(run_id)
    fresh = prepare_run(Path(intent["sourceWorkspace"]), str(intent["mo2ArtifactId"]),
        Path(intent["steamRoot"]), validation_root, preparation_recovery=recovery,
        _admitted_run_id=future_run_id, _admitted_preparation=admission)
    _record_preparation_effects(fresh.effects)
    return fresh.value


def _record_preparation_effects(effects):
    ledger = _current_effects()
    for path in effects.written_paths:
        ledger.write(path)
    for root in effects.child_mutation_roots:
        ledger.child_mutation_root(root)
    for process in effects.preparation_processes:
        ledger.preparation_process(process)
    if effects.watcher_pid is not None:
        ledger.watcher(effects.watcher_pid)
    if effects.mo2_pid is not None:
        ledger.mo2(effects.mo2_pid)
    ledger.changes(source=effects.source_changes, game=effects.game_changes,
        production_mo2=effects.production_mo2_changes)


@_receipted
def arm_scenario(
    validation_root: Path,
    run_id: str,
    scenario: ContainmentScenario,
) -> ScenarioJournal:
    """Capture stable pre-state and stop only after the watcher is ready."""
    store = _effect_store(validation_root)
    _require_current_execution_policy(store, run_id)
    record = _load_fixture_record(store, run_id, scenario)
    before = _capture_protected(record)
    source_level = inspect_path_integrity(record.source_root)
    stage_level = inspect_path_integrity(record.stage_root)
    if not source_integrity_allowed(source_level):
        raise ContainmentServiceError("source integrity is not Medium or higher")
    if not stage_integrity_allowed(stage_level):
        raise ContainmentServiceError("stage integrity is not Low")
    projection_count, targets_verified, projection_complete = _projection_state(record)
    if not projection_complete:
        raise ContainmentServiceError("stage projection observation is unavailable")
    if not targets_verified or projection_count <= 0:
        raise ContainmentServiceError("stage projection is not exact before arming")
    store.write_protected_state(run_id, scenario, "before", before)
    journal = store.create(
        ScenarioJournal(
            _SCHEMA_VERSION,
            run_id,
            scenario,
            ScenarioState.PREPARED,
            str(record.source_root),
            str(record.stage_root),
            str(record.archive_path),
            _PROTECTED_NAME,
            _EXPECTED_NEW[scenario],
            before,
            None,
            None,
            None,
        )
    )
    evidence_root = store.watch_path(run_id, scenario)
    roots = _watch_roots(record)
    request = WatchRequest(
        request_id="watch-request:" + secrets.token_hex(32),
        session_id="watch-session:" + secrets.token_hex(32),
        run_id=run_id,
        scenario=scenario,
        evidence_root=evidence_root,
        stop_token_path=evidence_root / "stop.token",
        roots=roots,
    )
    try:
        worker_pid = _delegated_mutation(
            evidence_root,
            lambda: start_watch(
                request,
                on_created=_current_effects().watcher,
            ),
        )
        if type(worker_pid) is not int or worker_pid <= 0:
            raise ContainmentServiceError("watch startup returned an invalid worker PID")
    except ContainmentStoreOwnershipError:
        raise
    except (OSError, RuntimeError) as error:
        store.transition(
            journal,
            ScenarioState.RECOVERY_REQUIRED,
            error=f"watch startup failed: {error}",
        )
        raise ContainmentServiceError(f"watch startup failed: {error}") from error
    return store.transition(journal, ScenarioState.ARMED, monitor_pid=worker_pid)


@_receipted
def launch_scenario(
    validation_root: Path,
    run_id: str,
    scenario: ContainmentScenario,
) -> ScenarioJournal:
    """Persist ScenarioStarted before launching the exact Low-integrity child."""
    store = _effect_store(validation_root)
    _require_current_execution_policy(store, run_id)
    record = _load_fixture_record(store, run_id, scenario)
    journal = store.load_journal(run_id, scenario)
    if journal.state is not ScenarioState.ARMED:
        raise ContainmentServiceError(
            f"launch requires Armed journal, observed {journal.state.value}"
        )
    if not _watcher_live(
        store.watch_path(run_id, scenario) / "request.json",
        journal.monitor_pid,
    ):
        store.transition(
            journal,
            ScenarioState.RECOVERY_REQUIRED,
            error="watcher liveness could not be verified before launch",
        )
        raise ContainmentServiceError("watcher liveness could not be verified before launch")
    if _capture_protected(record) != journal.protected_before:
        store.transition(
            journal,
            ScenarioState.RECOVERY_REQUIRED,
            error="protected state changed before ScenarioStarted",
        )
        raise ContainmentServiceError("protected state changed before ScenarioStarted")
    retry_binding = _retry_binding_for_run(store, run_id)
    if retry_binding is not None:
        _reprove_retry_absence(store, run_id, retry_binding)
    started = store.transition(journal, ScenarioState.SCENARIO_STARTED)
    version = read_windows_file_version(record.executable)
    if version != "2.5.2.0":
        store.transition(
            started,
            ScenarioState.RECOVERY_REQUIRED,
            error="post-ScenarioStarted: stage MO2 executable version is not 2.5.2.0",
        )
        raise ContainmentServiceError("stage MO2 executable version is not 2.5.2.0")
    created_pid: int | None = None

    def record_created_pid(pid: int) -> None:
        nonlocal created_pid
        created_pid = pid
        _current_effects().mo2(pid)

    try:
        launch = launch_low_integrity_process(
            record.executable,
            ("--profile", "ModLab - Lab"),
            record.stage_app,
            record.stage_environment,
            on_created=record_created_pid,
        )
        observed_integrity = inspect_process_integrity(launch.pid)
        observation = inspect_mo2_processes(record.stage_root)
        exact = tuple(item for item in observation.relevant if item.pid == launch.pid)
        if (
            created_pid != launch.pid
            or launch.integrity is not IntegrityLevel.LOW
            or observed_integrity is not IntegrityLevel.LOW
            or not observation.complete
            or len(exact) != 1
            or not _same_path(exact[0].executable_path, record.executable)
            or not _same_path(launch.executable, record.executable)
            or launch.arguments != ("--profile", "ModLab - Lab")
            or not _same_path(launch.working_directory, record.stage_app)
        ):
            raise ContainmentServiceError("launched MO2 identity/integrity proof failed")
    except BaseException as error:
        mo2_pid = created_pid
        store.transition(
            started,
            ScenarioState.RECOVERY_REQUIRED,
            mo2_pid=mo2_pid,
            error=f"post-ScenarioStarted launch failed: {error}",
        )
        if isinstance(error, ContainmentServiceError):
            raise
        raise ContainmentServiceError(f"post-ScenarioStarted launch failed: {error}") from error
    process = ProcessEvidence(
        launch.pid,
        str(record.executable),
        version,
        launch.arguments,
        str(record.stage_app),
        to_integrity_observation(observed_integrity),
    )
    launch_document = {
        "schemaVersion": _SCHEMA_VERSION,
        "runId": run_id,
        "scenario": scenario.value,
        "purpose": "stage-mo2-scenario",
        "pid": process.pid,
        "creationTime": launch.creation_time,
        "executable": process.executable,
        "executableVersion": process.executable_version,
        "arguments": list(process.arguments),
        "workingDirectory": process.working_directory,
        "integrity": process.integrity.value,
    }
    try:
        store.write_launch_evidence(run_id, scenario, launch_document)
        return store.transition(started, ScenarioState.LAUNCHED, mo2_pid=launch.pid)
    except ContainmentStoreOwnershipError:
        raise
    except (ContainmentStoreError, OSError) as error:
        store.transition(
            started,
            ScenarioState.RECOVERY_REQUIRED,
            mo2_pid=launch.pid,
            error=f"post-ScenarioStarted launch evidence publication failed: {error}",
        )
        raise ContainmentServiceError(
            f"post-ScenarioStarted launch evidence publication failed: {error}"
        ) from error


@_receipted
def capture_scenario(
    validation_root: Path,
    run_id: str,
    scenario: ContainmentScenario,
) -> ScenarioResult:
    """Stop the same-controller watcher, bind its outcome, and capture final policy."""
    store = _effect_store(validation_root)
    _require_current_execution_policy(store, run_id)
    record = _load_fixture_record(store, run_id, scenario)
    journal = store.load_journal(run_id, scenario)
    if journal.state is not ScenarioState.LAUNCHED:
        raise ContainmentServiceError(
            f"capture requires Launched journal, observed {journal.state.value}"
        )
    observation = inspect_mo2_processes(record.stage_root)
    if not observation.complete:
        raise ContainmentServiceError("MO2 process liveness is uncertain")
    if observation.relevant:
        raise ContainmentServiceError("MO2 must be closed before capture")
    try:
        launch_document = store.load_launch_evidence(run_id, scenario)
        process = _load_launch_process(store, run_id, scenario, record)
    except (ContainmentStoreError, ContainmentServiceError) as error:
        store.transition(
            journal,
            ScenarioState.RECOVERY_REQUIRED,
            error=f"durable launch evidence is unavailable: {error}",
        )
        raise ContainmentServiceError("durable launch evidence is unavailable") from error
    if process.pid != journal.mo2_pid:
        store.transition(
            journal,
            ScenarioState.RECOVERY_REQUIRED,
            error="durable launch evidence PID differs from journal",
        )
        raise ContainmentServiceError("durable launch evidence PID differs from journal")
    if not _exact_process_absent(
        int(launch_document["pid"]),
        int(launch_document["creationTime"]),
        "captured-stage-mo2",
    ):
        store.transition(
            journal,
            ScenarioState.RECOVERY_REQUIRED,
            error="exact launched MO2 absence is live or uncertain",
        )
        raise ContainmentServiceError("exact launched MO2 absence is live or uncertain")

    watch_evidence_root = store.watch_path(run_id, scenario)
    request_path = watch_evidence_root / "request.json"
    receipt = _delegated_mutation(
        watch_evidence_root,
        lambda: stop_watch(request_path),
    )
    if receipt.watch_outcome_id is None:
        store.transition(
            journal,
            ScenarioState.RECOVERY_REQUIRED,
            error="watch outcome was not durably published",
        )
        raise ContainmentServiceError("watch outcome was not durably published")
    outcome = store.load_watch_outcome(run_id, scenario, receipt.watch_outcome_id)
    if (
        receipt.run_id != run_id
        or receipt.scenario is not scenario
        or receipt.request_id != outcome.request_id
        or receipt.session_id != outcome.session_id
        or receipt.worker_pid != outcome.worker_pid
        or receipt.request_bytes_sha256 != outcome.request_sha256
    ):
        store.transition(
            journal,
            ScenarioState.RECOVERY_REQUIRED,
            error="watch receipt/outcome binding mismatch",
        )
        raise ContainmentServiceError("watch receipt/outcome binding mismatch")

    post_mo2 = _capture_protected(record)
    projection_quarantine = store.quarantine_path(run_id) / scenario.value
    with _retained_relocation_projections(store, run_id, record) as projections:
        projection = _delegated_mutations_with_created_root(
            projection_quarantine,
            (
                record.source_mods,
                record.stage_mods,
            ),
            partial(
                _preflight_projection_relocation,
                record,
                projection_quarantine,
                expected_projections=projections,
            ),
            lambda prepared: _finalize_projection(
                store,
                run_id,
                record,
                journal.protected_before,
                post_mo2,
                quarantine_root=projection_quarantine,
                relocation=prepared,
                expected_projections=projections,
            ),
            expected_projections=projections,
            retained_preflight=partial(
                _retained_projection_relocation,
                record,
                projection_quarantine,
                expected_projections=projections,
            ),
            verify_before_create=_verify_retained_relocation_before_creation,
            bind_created_root=_bind_retained_relocation_root,
            verify_before_operation=_verify_retained_relocation_before_operation,
        )
    store.write_protected_state(
        run_id,
        scenario,
        "after",
        projection.protected_after,
    )
    source_observation = _integrity_observation(record.source_root)
    stage_observation = _integrity_observation(record.stage_root)
    result = evaluate_scenario(
        ScenarioEvidence(
            run_id,
            scenario,
            journal.protected_before,
            projection.protected_after,
            outcome,
            process,
            source_observation,
            stage_observation,
            True,
            False,
            projection.projection_count,
            projection.projection_targets_verified,
            projection.projection_observation_complete,
            0,
            projection.production_backup_names,
            projection.production_observation_complete,
            projection.staging_new_names,
            projection.staging_observation_complete,
            projection.staging_output_names,
            projection.output_observation_complete,
            projection.adopted_name,
            projection.adopted_tree,
            projection.adopted_integrity,
            projection.source_restored_after_quarantine,
            projection.safety_reasons,
            projection.incomplete_reasons,
        )
    )
    _record_result_effects(result)
    store.write_result(result)
    store.transition(journal, ScenarioState.CAPTURED)
    return result


@_receipted
def recover_scenario(
    validation_root: Path,
    run_id: str,
    scenario: ContainmentScenario,
) -> ScenarioRecovery:
    """Perform cleanup only; never return a journal or resume a success path."""
    store = _effect_store(validation_root)
    journal = store.load_journal(run_id, scenario)
    if journal.state is ScenarioState.CAPTURED:
        raise ContainmentServiceError("Captured scenario does not require recovery")
    record = _load_fixture_record(store, run_id, scenario)
    scenario_started = _scenario_was_started(store, journal)
    proof = _prove_recovery(store, record, journal)
    proof_blockers = tuple(sorted(set(proof.blockers)))
    if proof_blockers:
        if journal.state is not ScenarioState.RECOVERY_REQUIRED:
            journal = store.transition(
                journal,
                ScenarioState.RECOVERY_REQUIRED,
                error=f"recovery refused from {journal.state.value}",
            )
        result_id = _persist_proven_recovery_breach(
            store,
            journal,
            proof,
            scenario_started=scenario_started,
        )
        blockers = proof_blockers
        if proof.watch_outcome is None:
            blockers = tuple(sorted(set((*blockers, "durable-watch-outcome-unavailable"))))
        recovery = ScenarioRecovery(
            _SCHEMA_VERSION, run_id, scenario, store.journal_id_for(journal),
            result_id, ScenarioCleanupStatus.REFUSED, False, blockers,
        )
        store.write_recovery(recovery)
        return recovery

    if journal.state is not ScenarioState.RECOVERY_REQUIRED:
        journal = store.transition(
            journal,
            ScenarioState.RECOVERY_REQUIRED,
            error=f"interrupted in {journal.state.value}",
        )
    cleanup = _perform_recovery_cleanup(store, record, journal, proof=proof)
    blockers = tuple(sorted(set(cleanup.blockers)))
    result_id: str | None = None
    pre_scenario = not scenario_started
    if blockers or cleanup.watch_outcome is None:
        if cleanup.watch_outcome is None:
            blockers = tuple(sorted(set((*blockers, "durable-watch-outcome-unavailable"))))
        refused_proof = RecoveryProofEvidence(
            cleanup.watch_outcome,
            cleanup.protected_after,
            blockers,
        )
        result_id = _persist_proven_recovery_breach(
            store,
            journal,
            refused_proof,
            scenario_started=scenario_started,
        )
        recovery = ScenarioRecovery(
            _SCHEMA_VERSION,
            run_id,
            scenario,
            store.journal_id_for(journal),
            result_id,
            ScenarioCleanupStatus.REFUSED,
            False,
            blockers,
        )
        store.write_recovery(recovery)
        return recovery

    outcome = cleanup.watch_outcome
    if watch_outcome_id_for(outcome) != watch_outcome_id_for(
        store.load_watch_outcome(run_id, scenario, watch_outcome_id_for(outcome))
    ):
        raise ContainmentServiceError("recovery watch outcome changed after cleanup")
    try:
        existing_result = store.load_result(run_id, scenario)
    except ContainmentStoreNotFound:
        existing_result = None
    if existing_result is not None:
        if existing_result.watch_outcome_id != watch_outcome_id_for(outcome):
            raise ContainmentServiceError(
                "existing scenario result is bound to another watch outcome"
            )
        existing_id = scenario_result_id_for(existing_result, outcome)
        retry_existing = bool(
            existing_result.fresh_retry_eligible
            and not existing_result.scenario_started
        )
        recovery = ScenarioRecovery(
            _SCHEMA_VERSION,
            run_id,
            scenario,
            store.journal_id_for(journal),
            existing_id,
            ScenarioCleanupStatus.SUCCEEDED,
            retry_existing,
            (),
        )
        recovery_write = store.write_recovery(recovery)
        if retry_existing:
            _ensure_retry_authority(store, recovery, recovery_write)
        return recovery
    retry = bool(
        pre_scenario
        and outcome.evidence_completion is WatchEvidenceCompletion.INCOMPLETE
        and "controller-session-lost" in outcome.reason_codes
        and cleanup.protected_after == journal.protected_before
    )
    try:
        prior_process = _load_launch_process(store, run_id, scenario, _load_fixture_record(store, run_id, scenario))
    except (ContainmentStoreError, ContainmentServiceError):
        prior_process = None
    result = evaluate_scenario(
        ScenarioEvidence(
            run_id,
            scenario,
            journal.protected_before,
            cleanup.protected_after,
            outcome,
            prior_process,
            cleanup.source_integrity,
            cleanup.stage_integrity,
            not pre_scenario,
            retry,
            cleanup.projection_count,
            cleanup.projection_targets_verified,
            cleanup.projection_observation_complete,
            cleanup.projection_payload_bytes_copied,
            (),
            False,
            (),
            False,
            (),
            False,
            None,
            None,
            None,
            cleanup.protected_after == journal.protected_before,
            (),
            ("interrupted-scenario-cleaned",),
        )
    )
    _record_result_effects(result)
    written = store.write_result(result)
    result_id = written.content_id
    recovery = ScenarioRecovery(
        _SCHEMA_VERSION,
        run_id,
        scenario,
        store.journal_id_for(journal),
        result_id,
        ScenarioCleanupStatus.SUCCEEDED,
        retry,
        (),
    )
    recovery_write = store.write_recovery(recovery)
    if retry:
        _ensure_retry_authority(store, recovery, recovery_write)
    return recovery


@_receipted
def adjudicate_run(validation_root: Path, run_id: str) -> CapabilityDecision:
    """Resolve every retained result and write one immutable capability decision."""
    store = _effect_store(validation_root)
    results: list[ScenarioResult] = []
    outcomes: list[WatchOutcome] = []
    for scenario in ContainmentScenario:
        try:
            result = store.load_result(run_id, scenario)
            outcome = store.load_watch_outcome(run_id, scenario, result.watch_outcome_id)
            scenario_result_id_for(result, outcome)
            journal_path = store.journal_path(run_id, scenario)
            if journal_path.exists():
                journal = store.load_journal(run_id, scenario)
                if journal.state not in {
                    ScenarioState.CAPTURED,
                    ScenarioState.RECOVERY_REQUIRED,
                }:
                    raise ContainmentDecisionNotReady(
                        f"current evidence is nonterminal: {scenario.value}"
                    )
        except ContainmentDecisionNotReady:
            raise
        except (ContainmentStoreError, ContainmentFormatError) as error:
            raise ContainmentDecisionNotReady(
                f"current evidence is unresolvable: {scenario.value}"
            ) from error
        results.append(result)
        outcomes.append(outcome)
    decision = adjudicate_results(tuple(results), tuple(outcomes))
    evidence_reasons: list[str] = []
    cohort_reasons: list[str] = []
    historical_ids: list[str] = []
    historical_failed = False
    current_intent: dict[str, object] | None = None
    retry_replay: _RetryReplayEvidence | None = None
    try:
        current_intent = store.load_intent(run_id)
        current_fingerprint = _intent_command_fingerprint(current_intent, run_id)
        predecessors = current_intent["predecessorRunIds"]
        if not _valid_predecessor_run_ids(predecessors, run_id):
            raise ContainmentServiceError("current predecessor snapshot is malformed")
        _load_bound_request(store, run_id, current_intent)
        retry_binding = current_intent["retryOf"]
        if retry_binding is not None:
            try:
                retry_replay = _load_consumed_retry_evidence(
                    store,
                    run_id,
                    dict(retry_binding),
                )
            except ContainmentServiceError as error:
                evidence_reasons.append(
                    f"current-retry-unresolvable:{type(error).__name__}"
                )
            else:
                if retry_replay.recovery.run_id not in predecessors:
                    evidence_reasons.append(
                        "current-retry-predecessor-binding-mismatch"
                    )
                    retry_replay = None
    except (ContainmentStoreError, ContainmentServiceError) as error:
        raise ContainmentDecisionNotReady(
            f"current source binding is unresolvable: {type(error).__name__}"
        ) from error

    if _valid_predecessor_run_ids(predecessors, run_id):
        for historical_run_id in predecessors:
            metadata_valid = True
            try:
                historical_intent = store.load_intent(historical_run_id)
                historical_fingerprint = _intent_command_fingerprint(
                    historical_intent, historical_run_id
                )
                if historical_fingerprint != current_fingerprint:
                    raise ContainmentServiceError(
                        "listed predecessor command fingerprint differs"
                    )
                try:
                    replacement = store.load_preparation_replacement(historical_run_id)
                except ContainmentStoreNotFound:
                    pass
                else:
                    _load_preparation_successor(store, replacement, current_fingerprint)
                    if replacement.consumed_by_run_id not in (*predecessors, run_id):
                        raise ContainmentServiceError("preparation replacement escaped current predecessor cohort")
                    # This is only an abandoned preparation disposition. It has
                    # no scenario result and contributes no successful evidence.
                    continue
                _load_bound_request(store, historical_run_id, historical_intent)
            except (ContainmentStoreError, ContainmentServiceError) as error:
                metadata_valid = False
                cohort_reasons.append(
                    f"predecessor-cohort-unresolvable:{historical_run_id}:"
                    f"{type(error).__name__}"
                )
            for scenario in ContainmentScenario:
                try:
                    result = store.load_result(historical_run_id, scenario)
                except ContainmentStoreNotFound:
                    if (
                        retry_replay is not None
                        and retry_replay.recovery.run_id == historical_run_id
                        and not _path_exists_no_follow(
                            store.scenario_path(historical_run_id, scenario)
                        )
                    ):
                        continue
                    cohort_reasons.append(
                        f"predecessor-evidence-unresolvable:"
                        f"{historical_run_id}:{scenario.value}"
                    )
                    continue
                except ContainmentStoreError:
                    cohort_reasons.append(
                        f"predecessor-evidence-unresolvable:"
                        f"{historical_run_id}:{scenario.value}"
                    )
                    continue
                try:
                    outcome = store.load_watch_outcome(
                        historical_run_id,
                        scenario,
                        result.watch_outcome_id,
                    )
                    identifier = scenario_result_id_for(result, outcome)
                except (ContainmentStoreError, ContainmentFormatError):
                    cohort_reasons.append(
                        f"predecessor-evidence-unresolvable:"
                        f"{historical_run_id}:{scenario.value}"
                    )
                    continue
                historical_ids.append(
                    f"historical-result:{result.outcome.value}:"
                    f"{historical_run_id}:{identifier}"
                )
                if result.outcome is ScenarioOutcome.FAILED:
                    historical_failed = True
                elif result.outcome is ScenarioOutcome.INCOMPLETE:
                    if not (
                        retry_replay is not None
                        and retry_replay.recovery.run_id == historical_run_id
                        and retry_replay.result.scenario is scenario
                        and retry_replay.result == result
                        and retry_replay.outcome == outcome
                    ):
                        cohort_reasons.append(
                            f"predecessor-result-incomplete:"
                            f"{historical_run_id}:{identifier}"
                        )
            if not metadata_valid:
                continue

    if historical_failed or decision.verdict is CapabilityVerdict.REJECTED:
        decision = CapabilityDecision(
            _SCHEMA_VERSION,
            run_id,
            _MECHANISM,
            CapabilityVerdict.REJECTED,
            decision.scenario_result_ids,
            tuple(
                sorted(
                    set(
                        (
                            *decision.reasons,
                            *evidence_reasons,
                            *cohort_reasons,
                            *historical_ids,
                            "historical-or-current-containment-failure",
                        )
                    )
                )
            ),
        )
    elif evidence_reasons or cohort_reasons:
        decision = CapabilityDecision(
            _SCHEMA_VERSION,
            run_id,
            _MECHANISM,
            CapabilityVerdict.INCOMPLETE,
            decision.scenario_result_ids,
            tuple(
                sorted(
                    set(
                        (
                            *decision.reasons,
                            *evidence_reasons,
                            *cohort_reasons,
                            *historical_ids,
                        )
                    )
                )
            ),
        )
    if current_intent is None:
        raise ContainmentDecisionNotReady("current run identity is unavailable")
    decision = replace(
        decision,
        schema_version=2,
        bindings=_decision_bindings(current_intent),
    )
    written = store.write_decision(decision)
    if written.value != decision or store.load_decision(run_id) != decision:
        raise ContainmentServiceError("stored capability decision differs after exact reload")
    return decision


def _decision_bindings(intent: Mapping[str, object]) -> DecisionBindings:
    artifact = intent.get("mo2ArtifactId")
    if type(artifact) is not str or not artifact:
        raise ContainmentDecisionNotReady("current source artifact is unavailable")
    release = load_mo2_release(bundled_mo2_252_path())
    source_root = Path(__file__).resolve().parents[2]
    try:
        commit = subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        tree = subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", f"{commit}^{{tree}}"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as error:
        raise ContainmentDecisionNotReady("source Git identity is unavailable") from error
    manifest = {
        "schemaVersion": 1,
        "sourceCommitId": commit,
        "sourceTreeId": tree,
        "mo2ArtifactId": artifact,
        "releaseDescriptorSha256": release.sha256,
        "archiveSha256": release.descriptor.archive_sha256,
    }
    return DecisionBindings(
        binding_version=2,
        source_commit_id=commit,
        source_tree_id=tree,
        source_artifact_id=source_artifact_id_for(manifest),
        protocol_version=2,
        publication_policy_version="handle-pinned-no-replace-v2",
        fixture_version=2,
        effect_receipt_version=1,
        authority_policy_version=1,
        mo2_version=release.descriptor.executable.file_version or "",
        mo2_executable_sha256=release.descriptor.executable.sha256,
    )


def _load_bound_request(
    store: ContainmentStore,
    run_id: str,
    intent: dict[str, object],
) -> dict[str, object]:
    for scenario in ContainmentScenario:
        _load_fixture_record(store, run_id, scenario)
    request = store.load_request(run_id)
    if (
        request.get("intentId") != _intent_id_for(intent)
        or any(request.get(name) != value for name, value in intent.items())
    ):
        raise ContainmentServiceError("request and run intent bindings differ")
    return request


def _fixture_record(fixture: ContainmentFixture, steam_root: Path) -> FixtureRecord:
    scenario = fixture.scenario
    archive = {
        ContainmentScenario.NEW_FOLDER: fixture.archives.new_folder,
        ContainmentScenario.MERGE_EXISTING: fixture.archives.overwrite_probe,
        ContainmentScenario.REPLACE_EXISTING: fixture.archives.overwrite_probe,
        ContainmentScenario.FOMOD_DEPENDENCY: fixture.archives.fomod_dependency,
    }[scenario]
    game = Path(steam_root).expanduser().absolute() / "steamapps" / "common" / "Skyrim Special Edition"
    before_names = _direct_names(fixture.source_mods)
    return FixtureRecord(
        scenario,
        fixture.run_root,
        fixture.source_layout.skyrim_mo2,
        fixture.stage_layout.skyrim_mo2,
        archive,
        fixture.source_mods,
        fixture.stage_mods,
        fixture.source_lab_modlist,
        fixture.source_play_modlist,
        fixture.source_layout.skyrim_mo2_downloads,
        fixture.source_layout.skyrim_mo2_overwrite,
        game,
        fixture.stage_layout.skyrim_mo2_app,
        fixture.stage_layout.skyrim_mo2_downloads,
        fixture.stage_layout.skyrim_mo2_profiles,
        fixture.stage_layout.skyrim_mo2_overwrite,
        fixture.stage_cache,
        fixture.stage_logs,
        dict(fixture.stage_environment),
        fixture.external_watch_roots,
        before_names,
    )


def _record_document(record: FixtureRecord) -> dict[str, object]:
    return {
        "scenario": record.scenario.value,
        "runRoot": str(record.run_root),
        "sourceRoot": str(record.source_root),
        "stageRoot": str(record.stage_root),
        "archivePath": str(record.archive_path),
        "sourceMods": str(record.source_mods),
        "stageMods": str(record.stage_mods),
        "sourceLabModlist": str(record.source_lab_modlist),
        "sourcePlayModlist": str(record.source_play_modlist),
        "sourceDownloads": str(record.source_downloads),
        "sourceOverwrite": str(record.source_overwrite),
        "boundedGame": str(record.bounded_game),
        "stageApp": str(record.stage_app),
        "stageDownloads": str(record.stage_downloads),
        "stageProfiles": str(record.stage_profiles),
        "stageOverwrite": str(record.stage_overwrite),
        "stageCache": str(record.stage_cache),
        "stageLogs": str(record.stage_logs),
        "stageEnvironment": dict(sorted(record.stage_environment.items())),
        "externalWatchRoots": [
            {"rootKind": kind, "path": str(path)}
            for kind, path in record.external_watch_roots
        ],
        "beforeNames": list(record.before_names),
    }


def _load_fixture_record(
    store: ContainmentStore,
    run_id: str,
    scenario: ContainmentScenario,
) -> FixtureRecord:
    document = store.load_request(run_id)
    request_fields = {
        "schemaVersion",
        "runId",
        "mechanism",
        "sourceWorkspace",
        "mo2ArtifactId",
        "steamRoot",
        "commandFingerprint",
        "predecessorRunIds",
        "retryOf",
        "intentId",
        "scenarios",
    }
    if set(document) != request_fields:
        raise ContainmentServiceError("request fields are not exact")
    try:
        intent = store.load_intent(run_id)
    except ContainmentStoreError as error:
        raise ContainmentServiceError(f"run intent is unavailable: {error}") from error
    intent_fields = set(intent)
    if (
        document["intentId"] != _intent_id_for(intent)
        or any(document.get(name) != intent[name] for name in intent_fields)
    ):
        raise ContainmentServiceError("request and run intent bindings differ")
    if (
        document["schemaVersion"] != _SCHEMA_VERSION
        or document["runId"] != run_id
        or document["mechanism"] != _MECHANISM
        or type(document["sourceWorkspace"]) is not str
        or type(document["mo2ArtifactId"]) is not str
        or not document["mo2ArtifactId"]
        or type(document["steamRoot"]) is not str
        or type(document["commandFingerprint"]) is not str
        or re.fullmatch(
            r"containment-command-sha256:[0-9a-f]{64}",
            document["commandFingerprint"],
        )
        is None
        or document["commandFingerprint"]
        not in _known_command_fingerprints(
            Path(document["sourceWorkspace"]),
            document["mo2ArtifactId"],
            Path(document["steamRoot"]),
        )
        or not _valid_predecessor_run_ids(document["predecessorRunIds"], run_id)
        or not _valid_retry_binding(document["retryOf"], document["commandFingerprint"])
    ):
        raise ContainmentServiceError("request identity values are malformed")
    rows = document.get("scenarios")
    if (
        type(rows) is not list
        or len(rows) != len(ContainmentScenario)
        or tuple(
            row.get("scenario") if type(row) is dict else None
            for row in rows
        )
        != tuple(item.value for item in ContainmentScenario)
    ):
        raise ContainmentServiceError("request scenarios are unavailable")
    matches = [row for row in rows if type(row) is dict and row.get("scenario") == scenario.value]
    if len(matches) != 1:
        raise ContainmentServiceError("request does not contain one exact scenario record")
    row = matches[0]
    expected = {
        "scenario", "runRoot", "sourceRoot", "stageRoot", "archivePath",
        "sourceMods", "stageMods", "sourceLabModlist", "sourcePlayModlist",
        "sourceDownloads", "sourceOverwrite", "boundedGame", "stageApp",
        "stageDownloads", "stageProfiles", "stageOverwrite", "stageCache",
        "stageLogs", "stageEnvironment", "externalWatchRoots", "beforeNames",
    }
    if set(row) != expected:
        raise ContainmentServiceError("scenario request fields are not exact")
    environment = row["stageEnvironment"]
    roots = row["externalWatchRoots"]
    names = row["beforeNames"]
    if (
        type(environment) is not dict
        or any(type(key) is not str or type(value) is not str for key, value in environment.items())
        or type(roots) is not list
        or type(names) is not list
        or any(type(name) is not str for name in names)
    ):
        raise ContainmentServiceError("scenario request values are malformed")
    external: list[tuple[str, Path]] = []
    for item in roots:
        if type(item) is not dict or set(item) != {"rootKind", "path"}:
            raise ContainmentServiceError("external watch root is malformed")
        external.append((str(item["rootKind"]), Path(str(item["path"]))))
    path_names = (
        "runRoot", "sourceRoot", "stageRoot", "archivePath", "sourceMods",
        "stageMods", "sourceLabModlist", "sourcePlayModlist", "sourceDownloads",
        "sourceOverwrite", "boundedGame", "stageApp", "stageDownloads",
        "stageProfiles", "stageOverwrite", "stageCache", "stageLogs",
    )
    paths = {name: Path(str(row[name])) for name in path_names}
    if any(not path.is_absolute() for path in paths.values()):
        raise ContainmentServiceError("scenario request paths must be absolute")
    if not _beneath(store.run_path(run_id), paths["runRoot"]):
        raise ContainmentServiceError("scenario fixture root escapes run root")
    confined_paths = tuple(
        paths[name]
        for name in path_names
        if name not in {"runRoot", "boundedGame"}
    )
    if any(not _beneath(paths["runRoot"], path) for path in confined_paths):
        raise ContainmentServiceError("scenario fixture path escapes its run root")
    if tuple(kind for kind, _path in external) != ROOT_KINDS[-2:]:
        raise ContainmentServiceError("external watch roots are not exact")
    for name in ("TEMP", "TMP", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME"):
        value = environment.get(name)
        if type(value) is not str:
            raise ContainmentServiceError("contained stage environment is incomplete")
        environment_path = Path(value)
        if not environment_path.is_absolute() or not _beneath(paths["runRoot"], environment_path):
            raise ContainmentServiceError("contained stage environment escapes its run root")
    supplied_names = tuple(names)
    if (
        supplied_names != (_PROTECTED_NAME,)
        or
        supplied_names != tuple(sorted(supplied_names, key=lambda item: (item.casefold(), item)))
        or len({name.casefold() for name in supplied_names}) != len(supplied_names)
    ):
        raise ContainmentServiceError("beforeNames must be canonical and unique")
    return FixtureRecord(
        scenario,
        paths["runRoot"], paths["sourceRoot"], paths["stageRoot"],
        paths["archivePath"], paths["sourceMods"], paths["stageMods"],
        paths["sourceLabModlist"], paths["sourcePlayModlist"],
        paths["sourceDownloads"], paths["sourceOverwrite"], paths["boundedGame"],
        paths["stageApp"], paths["stageDownloads"], paths["stageProfiles"],
        paths["stageOverwrite"], paths["stageCache"], paths["stageLogs"],
        dict(environment), tuple(external), supplied_names,
    )


def _capture_protected(record: FixtureRecord) -> ProtectedState:
    return ProtectedState(
        stable_tree_identity(record.source_mods, required_equal_passes=2),
        _stable_file_sha256(record.source_lab_modlist),
        _stable_file_sha256(record.source_play_modlist),
        stable_tree_identity(record.source_downloads, required_equal_passes=2),
        stable_tree_identity(record.source_overwrite, required_equal_passes=2),
        stable_tree_identity(record.bounded_game, required_equal_passes=2),
    )


def _stable_file_sha256(path: Path) -> str:
    def observe() -> tuple[str, int, int]:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or path.is_symlink() or bool(
            getattr(metadata, "st_file_attributes", 0) & 0x400
        ):
            raise ContainmentServiceError(f"protected file is not direct: {path}")
        data = path.read_bytes()
        after = path.lstat()
        if (metadata.st_ino, metadata.st_size, metadata.st_mtime_ns) != (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ContainmentServiceError(f"protected file changed while hashing: {path}")
        return hashlib.sha256(data).hexdigest(), len(data), after.st_mtime_ns
    first = observe()
    second = observe()
    if first != second:
        raise ContainmentServiceError(f"protected file was not stable: {path}")
    return first[0]


def _watch_roots(record: FixtureRecord):
    physical = (
        ("SourceMods", record.source_mods),
        ("LabProfile", record.source_lab_modlist.parent),
        ("PlayProfile", record.source_play_modlist.parent),
        ("Downloads", record.source_downloads),
        ("Overwrite", record.source_overwrite),
        ("BoundedGame", record.bounded_game),
        *record.external_watch_roots,
    )
    if tuple(kind for kind, _ in physical) != ROOT_KINDS:
        raise ContainmentServiceError("fixture does not provide all eight exact watch roots")
    return tuple(watch_root(kind, path) for kind, path in physical)


def _projection_state(
    record: FixtureRecord,
    *,
    allow_new: bool = False,
) -> tuple[int, bool, bool]:
    try:
        stage_names = _direct_names(record.stage_mods)
    except OSError:
        return 0, False, False
    if allow_new:
        if any(name not in stage_names for name in record.before_names):
            return len(record.before_names), False, True
    elif stage_names != record.before_names:
        return len(stage_names), False, True
    for name in record.before_names:
        try:
            evidence = inspect_junction(record.stage_mods / name)
        except (OSError, ContainmentSafetyError):
            return len(record.before_names), False, False
        if not _same_path(evidence.target_path, record.source_mods / name):
            return len(record.before_names), False, True
    return len(record.before_names), True, True


def _finalize_projection(
    store: ContainmentStore,
    run_id: str,
    record: FixtureRecord,
    before: ProtectedState,
    post_mo2: ProtectedState,
    *,
    quarantine_root: Path | None = None,
    relocation: _RetainedRelocation | None = None,
    expected_projections: tuple = (),
) -> _ProjectionEvidence:
    incomplete: list[str] = []
    prospective_quarantine = (
        store.quarantine_path(run_id) / record.scenario.value
        if quarantine_root is None
        else Path(quarantine_root)
    )
    if relocation is None:
        raise ContainmentServiceError(
            "retained relocation authority is required before projection mutation"
        )
    relocation.verify(require_quarantine_parent=True)
    admitted = relocation.admission
    stage_names = admitted.stage_names
    source_names = admitted.source_names
    changed_snapshot = admitted.changed_names
    staging_complete = True
    production_complete = True
    production_backups = tuple(name for name in source_names if name not in record.before_names)
    new_names = tuple(name for name in stage_names if name not in record.before_names)
    projection_count, targets_verified, projection_complete = _projection_state(
        record, allow_new=True
    )
    safety: list[str] = []
    if production_complete and production_backups:
        safety.append("production-backup-created")
    if record.scenario is not ContainmentScenario.REPLACE_EXISTING:
        if not projection_complete:
            incomplete.append("projection-observation-unavailable")
        elif not targets_verified:
            safety.append("projection-target-changed")
    adopted_name: str | None = None
    adopted_tree: TreeIdentity | None = None
    adopted_integrity: IntegrityObservation | None = None
    outputs: tuple[str, ...] = ()
    output_complete = (
        record.scenario not in _EXPECTED_OUTPUTS
        and record.scenario is not ContainmentScenario.REPLACE_EXISTING
    )
    final = post_mo2
    restored = post_mo2 == before
    quarantine = (
        ensure_direct_subdirectory(
            store.quarantine_path(run_id),
            record.scenario.value,
        )
        if quarantine_root is None
        else Path(quarantine_root)
    )

    if record.scenario in _EXPECTED_OUTPUTS:
        expected = _EXPECTED_NEW[record.scenario]
        adoption = None
        if post_mo2 != before:
            safety.append("protected-state-changed-before-adoption")
            quarantine_names = admitted.mutation_names if staging_complete else ()
            try:
                quarantine_retained_relocation(
                    relocation.authority,
                    quarantine_names,
                )
            except (OSError, ContainmentSafetyError):
                incomplete.append("staging-quarantine-failed")
        else:
            try:
                adoption = adopt_retained_relocation(
                    relocation.authority,
                    expected_name=expected,
                    before_names=record.before_names,
                )
                candidate_integrity = to_integrity_observation(
                    adoption.final_integrity
                )
                candidate_outputs = _relative_files(adoption.destination_path)
                adopted_name = adoption.adopted_name
                adopted_tree = adoption.after_tree
                adopted_integrity = candidate_integrity
                outputs = candidate_outputs
                output_complete = True
            except (OSError, ContainmentSafetyError, ValueError) as error:
                output_complete = False
                incomplete.append(f"adoption-proof-unavailable:{type(error).__name__}")
            finally:
                if adoption is not None and _exists_no_follow(adoption.destination_path):
                    try:
                        quarantine_retained_relocation(
                            relocation.authority,
                            (expected,),
                            verify=False,
                        )
                    except (OSError, ContainmentSafetyError):
                        incomplete.append("adopted-source-quarantine-failed")
        final = _capture_protected(record)
        restored = final == before
    else:
        unexpected = tuple(name for name in stage_names if name not in record.before_names)
        changed = changed_snapshot
        replacement_candidate = (
            record.scenario is ContainmentScenario.REPLACE_EXISTING
            and not unexpected
            and changed == (_PROTECTED_NAME,)
        )
        if replacement_candidate:
            replacement = record.stage_mods / _PROTECTED_NAME
            try:
                replacement_evidence = quarantine_retained_replacement(
                    relocation.authority,
                    expected_name=_PROTECTED_NAME,
                )
                outputs = replacement_evidence.output_names
                output_complete = True
            except (OSError, ContainmentSafetyError):
                output_complete = False
                incomplete.append("replacement-quarantine-proof-unavailable")
            else:
                try:
                    create_mod_projection(
                        record.source_mods / _PROTECTED_NAME,
                        replacement,
                    )
                except (OSError, ContainmentSafetyError):
                    incomplete.append("projection-restoration-failed")
                else:
                    projection_count, targets_verified, projection_complete = (
                        _projection_state(record)
                    )
        else:
            if unexpected or changed:
                safety.append("unexpected-staging-backup")
            quarantine_names = admitted.mutation_names if staging_complete else ()
            try:
                quarantine_retained_relocation(
                    relocation.authority,
                    quarantine_names,
                )
            except (OSError, ContainmentSafetyError):
                incomplete.append("staging-quarantine-failed")
        if record.scenario is ContainmentScenario.REPLACE_EXISTING:
            if not projection_complete:
                incomplete.append("projection-observation-unavailable")
            elif not targets_verified:
                safety.append("projection-target-changed")
        final = _capture_protected(record)
        restored = final == before

    return _ProjectionEvidence(
        final,
        projection_count,
        targets_verified,
        projection_complete,
        production_backups,
        production_complete,
        new_names,
        staging_complete,
        outputs,
        output_complete,
        adopted_name,
        adopted_tree,
        adopted_integrity,
        restored,
        tuple(sorted(set(safety))),
        tuple(sorted(set(incomplete))),
    )


def _perform_recovery_cleanup(
    store: ContainmentStore,
    record: FixtureRecord,
    journal: ScenarioJournal,
    *,
    proof: RecoveryProofEvidence,
) -> RecoveryCleanupEvidence:
    blockers: list[str] = []
    watch: WatchOutcome | None = proof.watch_outcome
    request_path = store.watch_path(journal.run_id, journal.scenario) / "request.json"
    if request_path.exists():
        recovery_watch_root = store.watch_path(journal.run_id, journal.scenario)
        receipt = _delegated_mutation(
            recovery_watch_root,
            lambda: stop_watch(request_path),
        )
        if receipt.watch_outcome_id is None:
            blockers.append("durable-watch-outcome-unavailable")
        else:
            try:
                watch = store.load_watch_outcome(
                    journal.run_id,
                    journal.scenario,
                    receipt.watch_outcome_id,
                )
            except ContainmentStoreError:
                blockers.append("durable-watch-outcome-invalid")
            if (
                watch is not None
                and (
                    receipt.run_id != journal.run_id
                    or receipt.scenario is not journal.scenario
                    or receipt.request_id != watch.request_id
                    or receipt.session_id != watch.session_id
                    or receipt.worker_pid != watch.worker_pid
                    or receipt.request_bytes_sha256 != watch.request_sha256
                )
            ):
                blockers.append("watch-receipt-outcome-binding-mismatch")
    else:
        blockers.append("watch-request-unavailable")

    if blockers:
        return RecoveryCleanupEvidence(
            watch,
            proof.protected_after,
            IntegrityObservation.UNKNOWN,
            IntegrityObservation.UNKNOWN,
            0,
            False,
            False,
            0,
            tuple(sorted(set(blockers))),
        )

    recovery_quarantine = store.quarantine_path(journal.run_id) / (
        "Recovery-" + journal.scenario.value
    )

    def quarantine_recovery(
        prepared: _RetainedRelocation,
        projections: tuple,
    ) -> None:
        prepared.verify(require_quarantine_parent=True)
        quarantine_retained_relocation(
            prepared.authority,
            prepared.admission.mutation_names,
        )

    try:
        with _retained_relocation_projections(
            store,
            journal.run_id,
            record,
        ) as projections:
            _delegated_mutations_with_created_root(
                recovery_quarantine,
                (record.source_mods, record.stage_mods),
                partial(
                    _preflight_projection_relocation,
                    record,
                    recovery_quarantine,
                    expected_projections=projections,
                ),
                partial(quarantine_recovery, projections=projections),
                expected_projections=projections,
                retained_preflight=partial(
                    _retained_projection_relocation,
                    record,
                    recovery_quarantine,
                    expected_projections=projections,
                ),
                verify_before_create=_verify_retained_relocation_before_creation,
                bind_created_root=_bind_retained_relocation_root,
                verify_before_operation=_verify_retained_relocation_before_operation,
            )
    except (OSError, ValueError, ContainmentSafetyError, ContainmentServiceError):
        blockers.append("staging-quarantine-failed")
        return RecoveryCleanupEvidence(
            watch,
            proof.protected_after,
            IntegrityObservation.UNKNOWN,
            IntegrityObservation.UNKNOWN,
            0,
            False,
            False,
            0,
            tuple(sorted(set(blockers))),
        )

    try:
        before_normalization = _capture_protected(record)
    except (OSError, ContainmentSafetyError, ContainmentServiceError):
        before_normalization = journal.protected_before
        blockers.append("source-evidence-unavailable")
    for path in (
        record.stage_app,
        record.stage_downloads,
        record.stage_profiles,
        record.stage_overwrite,
        record.stage_cache,
        record.stage_logs,
    ):
        try:
            set_low_integrity_tree(path)
        except IntegrityLabelError:
            _current_effects().child_mutation_root(path)
            blockers.append("stage-integrity-normalization-failed")
            break
        except (OSError, RuntimeError):
            blockers.append("stage-integrity-normalization-failed")
            break
        else:
            _current_effects().child_mutation_root(path)
    source_integrity = _integrity_observation(record.source_root)
    stage_integrity = _integrity_observation(record.stage_root)
    if source_integrity not in {
        IntegrityObservation.MEDIUM,
        IntegrityObservation.HIGH,
        IntegrityObservation.SYSTEM,
    }:
        blockers.append("source-integrity-unverified")
    if stage_integrity is not IntegrityObservation.LOW:
        blockers.append("stage-integrity-unverified")
    projection_count, targets_verified, projection_complete = _projection_state(record)
    if not projection_complete or not targets_verified:
        blockers.append("projection-integrity-unverified")
    try:
        after = _capture_protected(record)
    except (OSError, ContainmentSafetyError, ContainmentServiceError):
        after = before_normalization
        blockers.append("source-reverification-failed")
    if after != before_normalization:
        blockers.append("source-changed-during-recovery")
    return RecoveryCleanupEvidence(
        watch,
        after,
        source_integrity,
        stage_integrity,
        projection_count,
        targets_verified,
        projection_complete,
        0,
        tuple(sorted(set(blockers))),
    )


def _prove_recovery(
    store: ContainmentStore,
    record: FixtureRecord,
    journal: ScenarioJournal,
) -> RecoveryProofEvidence:
    """Read-only ownership and absence proof; no cleanup mutation is permitted here."""
    blockers: list[str] = []
    watch: WatchOutcome | None = None
    after = journal.protected_before
    if (
        not _same_path(journal.source_root, record.source_root)
        or not _same_path(journal.stage_root, record.stage_root)
        or not _same_path(journal.archive_path, record.archive_path)
        or journal.protected_mod_name != _PROTECTED_NAME
        or journal.expected_new_mod_name != _EXPECTED_NEW[journal.scenario]
    ):
        blockers.append("journal-request-fixture-binding-mismatch")
    request_path = store.watch_path(journal.run_id, journal.scenario) / "request.json"
    try:
        request_bytes = request_path.read_bytes()
        request = watch_request_from_bytes(request_bytes)
        if request_bytes != _windows_watch.watch_request_to_bytes(request):
            raise ContainmentServiceError("watch request is not canonical")
        claim = controller_claim_from_bytes(
            (request.evidence_root / CLAIM_NAME).read_bytes(), request
        )
        launch = worker_launch_from_bytes(
            (request.evidence_root / LAUNCH_NAME).read_bytes(), request
        )
        if (
            request.run_id != journal.run_id
            or request.scenario is not journal.scenario
            or launch.worker_pid != journal.monitor_pid
            or claim.request_sha256 != watch_request_sha256(request)
            or launch.request_sha256 != claim.request_sha256
            or launch.session_id != claim.session_id
            or launch.run_id != claim.run_id
            or launch.scenario is not claim.scenario
        ):
            raise ContainmentServiceError("watch request/claim/launch binding mismatch")
        if not _exact_process_absent(
            claim.controller_pid,
            claim.controller_creation_time,
            "prior-controller",
        ):
            blockers.append("prior-controller-live-or-uncertain")
        if not _exact_process_absent(
            launch.worker_pid,
            launch.worker_creation_time,
            "prior-watcher",
        ):
            blockers.append("prior-watcher-live-or-uncertain")
        try:
            watch = store.load_watch_outcome(journal.run_id, journal.scenario)
        except ContainmentStoreNotFound:
            watch = None
        if watch is not None and (
            watch.request_id != request.request_id
            or watch.request_sha256 != claim.request_sha256
            or watch.session_id != request.session_id
            or watch.run_id != journal.run_id
            or watch.scenario is not journal.scenario
            or watch.controller_pid != claim.controller_pid
            or watch.controller_creation_time != claim.controller_creation_time
            or watch.worker_pid != launch.worker_pid
            or watch.worker_creation_time != launch.worker_creation_time
        ):
            blockers.append("watch-outcome-binding-mismatch")
            watch = None
    except (OSError, RuntimeError, ValueError, ContainmentStoreError) as error:
        blockers.append(f"watch-proof-unavailable:{type(error).__name__}")

    try:
        launch_document = store.load_launch_evidence(journal.run_id, journal.scenario)
        launch_process = _load_launch_process(
            store, journal.run_id, journal.scenario, record
        )
        if journal.mo2_pid is not None and launch_process.pid != journal.mo2_pid:
            blockers.append("mo2-launch-journal-binding-mismatch")
    except ContainmentStoreNotFound:
        launch_document = None
        if _journal_requires_launch(journal):
            blockers.append("mo2-launch-proof-unavailable:missing")
    except (ContainmentStoreError, ContainmentServiceError, ValueError) as error:
        launch_document = None
        blockers.append(f"mo2-launch-proof-unavailable:{type(error).__name__}")
    if launch_document is not None:
        try:
            launch_absent = _exact_process_absent(
                int(launch_document["pid"]),
                int(launch_document["creationTime"]),
                "prior-stage-mo2",
            )
        except (OSError, RuntimeError, ValueError) as error:
            blockers.append(
                f"mo2-exact-process-proof-unavailable:{type(error).__name__}"
            )
        else:
            if not launch_absent:
                blockers.append("prior-mo2-live-or-uncertain")
    try:
        observation = inspect_mo2_processes(record.stage_root)
    except (OSError, RuntimeError, ValueError) as error:
        blockers.append(f"mo2-process-proof-unavailable:{type(error).__name__}")
    else:
        if not observation.complete:
            blockers.append("mo2-process-identity-uncertain")
        elif observation.relevant:
            blockers.append("prior-mo2-process-still-live")
    try:
        after = _capture_protected(record)
    except (OSError, RuntimeError, ValueError, ContainmentSafetyError):
        blockers.append("protected-state-proof-unavailable")
    return RecoveryProofEvidence(watch, after, tuple(sorted(set(blockers))))


def _exact_process_absent(pid: int, creation_time: int, label: str) -> bool:
    status, handle, _detail = _windows_watch._exact_process_status(pid, creation_time)
    close_error = None
    if handle:
        close_error = _windows_watch._close_handle(handle, label)
    return status == "dead" and close_error is None


def _scenario_was_started(store: ContainmentStore, journal: ScenarioJournal) -> bool:
    if _journal_requires_launch(journal):
        return True
    try:
        store.load_launch_evidence(journal.run_id, journal.scenario)
    except ContainmentStoreNotFound:
        return False
    except ContainmentStoreError:
        return True
    return True


def _journal_requires_launch(journal: ScenarioJournal) -> bool:
    return journal.state in {
        ScenarioState.SCENARIO_STARTED,
        ScenarioState.LAUNCHED,
        ScenarioState.CAPTURED,
    } or journal.mo2_pid is not None or "ScenarioStarted" in (journal.error or "")


def _persist_proven_recovery_breach(
    store: ContainmentStore,
    journal: ScenarioJournal,
    proof: RecoveryProofEvidence,
    *,
    scenario_started: bool,
) -> str | None:
    outcome = proof.watch_outcome
    if (
        outcome is None
        or not scenario_started
        or (not outcome.events and proof.protected_after == journal.protected_before)
    ):
        return None
    try:
        existing = store.load_result(journal.run_id, journal.scenario)
    except ContainmentStoreNotFound:
        existing = None
    if existing is not None:
        _record_result_effects(existing)
        return scenario_result_id_for(existing, outcome)
    result = evaluate_scenario(
        ScenarioEvidence(
            run_id=journal.run_id,
            scenario=journal.scenario,
            protected_before=journal.protected_before,
            protected_after=proof.protected_after,
            watch_outcome=outcome,
            mo2_process=None,
            source_integrity=IntegrityObservation.UNKNOWN,
            stage_integrity=IntegrityObservation.UNKNOWN,
            scenario_started=True,
            fresh_retry_eligible=False,
            projection_count=0,
            projection_targets_verified=False,
            projection_observation_complete=False,
            projection_payload_bytes_copied=0,
            production_backup_names=(),
            production_observation_complete=False,
            staging_new_names=(),
            staging_observation_complete=False,
            staging_output_names=(),
            output_observation_complete=False,
            adopted_name=None,
            adopted_tree=None,
            adopted_integrity=None,
            source_restored_after_quarantine=(
                proof.protected_after == journal.protected_before
            ),
            safety_reasons=(),
            incomplete_reasons=proof.blockers,
        )
    )
    if result.outcome is not ScenarioOutcome.FAILED:
        return None
    _record_result_effects(result)
    return store.write_result(result).content_id


def _request_command_fingerprint(store: ContainmentStore, run_id: str) -> str:
    document = store.load_request(run_id)
    value = document.get("commandFingerprint")
    if (
        type(value) is not str
        or re.fullmatch(r"containment-command-sha256:[0-9a-f]{64}", value) is None
    ):
        raise ContainmentServiceError("request command fingerprint is unavailable")
    source = document.get("sourceWorkspace")
    artifact = document.get("mo2ArtifactId")
    steam = document.get("steamRoot")
    if (
        type(source) is not str
        or type(artifact) is not str
        or not artifact
        or type(steam) is not str
        or value not in _known_command_fingerprints(
            Path(source), artifact, Path(steam)
        )
    ):
        raise ContainmentServiceError("request command fingerprint does not recompute")
    return value


def _require_current_execution_policy(
    store: ContainmentStore,
    run_id: str,
) -> None:
    observed = _request_command_fingerprint(store, run_id)
    document = store.load_request(run_id)
    source = document.get("sourceWorkspace")
    artifact = document.get("mo2ArtifactId")
    steam = document.get("steamRoot")
    if type(source) is not str or type(artifact) is not str or type(steam) is not str:
        raise ContainmentServiceError("request command fingerprint does not recompute")
    expected = _command_fingerprint(Path(source), artifact, Path(steam))
    if observed != expected:
        raise ContainmentServiceError(
            "scenario execution requires the current command policy; "
            "historical runs are read-only"
        )


def _ensure_retry_authority(
    store: ContainmentStore,
    recovery: ScenarioRecovery,
    recovery_write,
) -> None:
    fingerprint = _request_command_fingerprint(store, recovery.run_id)
    try:
        authority = store.load_retry_authority(recovery.run_id, recovery.scenario)
    except ContainmentStoreNotFound:
        store.write_retry_authority(
            recovery, recovery_write.content_id, fingerprint
        )
        return
    if (
        authority["recoveryId"] != recovery_write.content_id
        or authority["commandFingerprint"] != fingerprint
    ):
        raise ContainmentServiceError("existing retry authority binding differs")


def _retry_binding_for_run(
    store: ContainmentStore,
    run_id: str,
) -> dict[str, object] | None:
    try:
        document = store.load_request(run_id)
    except ContainmentStoreNotFound:
        return None
    value = document.get("retryOf")
    fingerprint = document.get("commandFingerprint")
    if not _valid_retry_binding(value, fingerprint):
        raise ContainmentServiceError("retry-bound request is malformed")
    return None if value is None else dict(value)


def _reprove_retry_absence(
    store: ContainmentStore,
    new_run_id: str,
    binding: dict[str, object],
) -> None:
    replay = _load_consumed_retry_evidence(store, new_run_id, binding)
    proof = _prove_recovery(store, replay.record, replay.journal)
    if proof.blockers:
        raise ContainmentServiceError(
            "retry prior controller/watcher/MO2 absence is not exact: "
            + ",".join(proof.blockers)
        )


def _load_consumed_retry_evidence(
    store: ContainmentStore,
    new_run_id: str,
    binding: dict[str, object],
) -> _RetryReplayEvidence:
    old_run_id = str(binding["runId"])
    try:
        old_scenario = ContainmentScenario(str(binding["scenario"]))
        recovery = store.load_recovery(old_run_id, old_scenario)
        authority = store.load_retry_authority(old_run_id, old_scenario)
        old_request = store.load_request(old_run_id)
        journal = store.load_journal(old_run_id, old_scenario)
        record = _load_fixture_record(store, old_run_id, old_scenario)
        result = store.load_result(old_run_id, old_scenario)
        outcome = store.load_watch_outcome(
            old_run_id, old_scenario, result.watch_outcome_id
        )
    except (ContainmentStoreError, ContainmentServiceError, ValueError) as error:
        raise ContainmentServiceError(
            f"retry prior evidence cannot be resolved: {error}"
        ) from error
    if (
        store.recovery_id_for(recovery) != binding["recoveryId"]
        or recovery.journal_id != store.journal_id_for(journal)
        or not recovery.fresh_run_permitted
        or recovery.cleanup_status is not ScenarioCleanupStatus.SUCCEEDED
        or authority["authorityId"] != binding["authorityId"]
        or authority["recoveryId"] != binding["recoveryId"]
        or authority["commandFingerprint"] != binding["commandFingerprint"]
        or authority["state"] != "Consumed"
        or authority["consumedByRunId"] != new_run_id
        or old_request.get("commandFingerprint") != binding["commandFingerprint"]
        or recovery.result_id != scenario_result_id_for(result, outcome)
        or result.outcome is not ScenarioOutcome.INCOMPLETE
        or not result.fresh_retry_eligible
        or result.scenario_started
        or _journal_requires_launch(journal)
    ):
        raise ContainmentServiceError("retry authority binding/replay proof differs")
    return _RetryReplayEvidence(recovery, journal, record, result, outcome)


def _path_exists_no_follow(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _direct_names(root: Path) -> tuple[str, ...]:
    return tuple(sorted((entry.name for entry in os.scandir(root)), key=lambda item: (item.casefold(), item)))


def _relative_files(root: Path) -> tuple[str, ...]:
    rows: list[str] = []
    for current, directories, files in os.walk(root, followlinks=False):
        base = Path(current)
        for name in directories:
            path = base / name
            if path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400):
                raise ContainmentSafetyError("adopted output contains a reparse directory")
        for name in files:
            path = base / name
            if path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400):
                raise ContainmentSafetyError("adopted output contains a reparse file")
            rows.append(path.relative_to(root).as_posix())
    return tuple(sorted(rows))


def _exists_no_follow(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _integrity_observation(path: Path) -> IntegrityObservation:
    try:
        return to_integrity_observation(inspect_path_integrity(path))
    except (OSError, ValueError, RuntimeError):
        return IntegrityObservation.UNKNOWN


def _watcher_live(request_path: Path, pid: int | None) -> bool:
    if type(pid) is not int or pid <= 0:
        return False
    try:
        request_bytes = request_path.read_bytes()
        request = watch_request_from_bytes(request_bytes)
        if request_bytes != _windows_watch.watch_request_to_bytes(request):
            return False
        launch_path = request.evidence_root / LAUNCH_NAME
        launch_bytes = launch_path.read_bytes()
        launch = worker_launch_from_bytes(launch_bytes, request)
        if launch.worker_pid != pid:
            return False
        status, handle, _detail = _windows_watch._exact_process_status(
            launch.worker_pid,
            launch.worker_creation_time,
        )
    except (OSError, RuntimeError, ValueError):
        return False
    close_error = None
    if handle:
        close_error = _windows_watch._close_handle(
            handle,
            f"scenario launch watcher process {pid}",
        )
    return status == "live" and close_error is None


def _load_launch_process(
    store: ContainmentStore,
    run_id: str,
    scenario: ContainmentScenario,
    record: FixtureRecord,
) -> ProcessEvidence:
    document = store.load_launch_evidence(run_id, scenario)
    process = ProcessEvidence(
        int(document["pid"]),
        str(document["executable"]),
        str(document["executableVersion"]),
        tuple(document["arguments"]),
        str(document["workingDirectory"]),
        IntegrityObservation(str(document["integrity"])),
    )
    if (
        not _same_path(process.executable, record.executable)
        or not _same_path(process.working_directory, record.stage_app)
        or process.arguments != ("--profile", "ModLab - Lab")
    ):
        raise ContainmentServiceError("launch evidence does not match exact fixture command")
    return process


def _same_path(left: Path | str, right: Path | str) -> bool:
    return os.path.normcase(os.path.normpath(str(left))) == os.path.normcase(
        os.path.normpath(str(right))
    )


def _beneath(root: Path, candidate: Path) -> bool:
    try:
        common = os.path.commonpath((str(root), str(candidate)))
    except ValueError:
        return False
    return os.path.normcase(os.path.normpath(common)) == os.path.normcase(
        os.path.normpath(str(root))
    )


def _command_fingerprint(
    source_workspace: Path,
    mo2_artifact_id: str,
    steam_root: Path,
) -> str:
    document = {
        "classificationPolicy": _CLASSIFICATION_POLICY,
        "fixturePolicy": _FIXTURE_POLICY,
        "mechanism": _MECHANISM,
        "mo2ArtifactId": mo2_artifact_id,
        "operatorPolicy": _OPERATOR_POLICY,
        "scenarios": [item.value for item in ContainmentScenario],
        "sourceWorkspace": os.path.normcase(
            os.path.normpath(str(Path(source_workspace).expanduser().absolute()))
        ),
        "steamRoot": os.path.normcase(
            os.path.normpath(str(Path(steam_root).expanduser().absolute()))
        ),
    }
    return _command_fingerprint_for(document)


def _pre_operator_policy_command_fingerprint(
    source_workspace: Path,
    mo2_artifact_id: str,
    steam_root: Path,
) -> str:
    document = {
        "classificationPolicy": _CLASSIFICATION_POLICY,
        "fixturePolicy": _FIXTURE_POLICY,
        "mechanism": _MECHANISM,
        "mo2ArtifactId": mo2_artifact_id,
        "scenarios": [item.value for item in ContainmentScenario],
        "sourceWorkspace": os.path.normcase(
            os.path.normpath(str(Path(source_workspace).expanduser().absolute()))
        ),
        "steamRoot": os.path.normcase(
            os.path.normpath(str(Path(steam_root).expanduser().absolute()))
        ),
    }
    return _command_fingerprint_for(document)


def _pre_fixture_policy_command_fingerprint(
    source_workspace: Path,
    mo2_artifact_id: str,
    steam_root: Path,
) -> str:
    document = {
        "classificationPolicy": _OLDER_CLASSIFICATION_POLICY,
        "mechanism": _MECHANISM,
        "mo2ArtifactId": mo2_artifact_id,
        "scenarios": [item.value for item in ContainmentScenario],
        "sourceWorkspace": os.path.normcase(
            os.path.normpath(str(Path(source_workspace).expanduser().absolute()))
        ),
        "steamRoot": os.path.normcase(
            os.path.normpath(str(Path(steam_root).expanduser().absolute()))
        ),
    }
    return _command_fingerprint_for(document)


def _previous_classification_command_fingerprint(
    source_workspace: Path,
    mo2_artifact_id: str,
    steam_root: Path,
) -> str:
    document = {
        "classificationPolicy": _PREVIOUS_CLASSIFICATION_POLICY,
        "fixturePolicy": _PREVIOUS_FIXTURE_POLICY,
        "mechanism": _MECHANISM,
        "mo2ArtifactId": mo2_artifact_id,
        "scenarios": [item.value for item in ContainmentScenario],
        "sourceWorkspace": os.path.normcase(
            os.path.normpath(str(Path(source_workspace).expanduser().absolute()))
        ),
        "steamRoot": os.path.normcase(
            os.path.normpath(str(Path(steam_root).expanduser().absolute()))
        ),
    }
    return _command_fingerprint_for(document)


def _older_classification_command_fingerprint(
    source_workspace: Path,
    mo2_artifact_id: str,
    steam_root: Path,
) -> str:
    document = {
        "classificationPolicy": _OLDER_CLASSIFICATION_POLICY,
        "fixturePolicy": _PREVIOUS_FIXTURE_POLICY,
        "mechanism": _MECHANISM,
        "mo2ArtifactId": mo2_artifact_id,
        "scenarios": [item.value for item in ContainmentScenario],
        "sourceWorkspace": os.path.normcase(
            os.path.normpath(str(Path(source_workspace).expanduser().absolute()))
        ),
        "steamRoot": os.path.normcase(
            os.path.normpath(str(Path(steam_root).expanduser().absolute()))
        ),
    }
    return _command_fingerprint_for(document)


def _previous_fixture_policy_command_fingerprint(
    source_workspace: Path,
    mo2_artifact_id: str,
    steam_root: Path,
) -> str:
    document = {
        "classificationPolicy": _CLASSIFICATION_POLICY,
        "fixturePolicy": _PREVIOUS_FIXTURE_POLICY,
        "mechanism": _MECHANISM,
        "mo2ArtifactId": mo2_artifact_id,
        "scenarios": [item.value for item in ContainmentScenario],
        "sourceWorkspace": os.path.normcase(
            os.path.normpath(str(Path(source_workspace).expanduser().absolute()))
        ),
        "steamRoot": os.path.normcase(
            os.path.normpath(str(Path(steam_root).expanduser().absolute()))
        ),
    }
    return _command_fingerprint_for(document)


def _legacy_command_fingerprint(
    source_workspace: Path,
    mo2_artifact_id: str,
    steam_root: Path,
) -> str:
    document = {
        "mechanism": _MECHANISM,
        "mo2ArtifactId": mo2_artifact_id,
        "scenarios": [item.value for item in ContainmentScenario],
        "sourceWorkspace": os.path.normcase(
            os.path.normpath(str(Path(source_workspace).expanduser().absolute()))
        ),
        "steamRoot": os.path.normcase(
            os.path.normpath(str(Path(steam_root).expanduser().absolute()))
        ),
    }
    return _command_fingerprint_for(document)


def _known_command_fingerprints(
    source_workspace: Path,
    mo2_artifact_id: str,
    steam_root: Path,
) -> tuple[str, str, str, str, str, str, str]:
    return (
        _command_fingerprint(source_workspace, mo2_artifact_id, steam_root),
        _pre_operator_policy_command_fingerprint(
            source_workspace, mo2_artifact_id, steam_root
        ),
        _previous_fixture_policy_command_fingerprint(
            source_workspace, mo2_artifact_id, steam_root
        ),
        _previous_classification_command_fingerprint(
            source_workspace, mo2_artifact_id, steam_root
        ),
        _older_classification_command_fingerprint(
            source_workspace, mo2_artifact_id, steam_root
        ),
        _pre_fixture_policy_command_fingerprint(
            source_workspace, mo2_artifact_id, steam_root
        ),
        _legacy_command_fingerprint(source_workspace, mo2_artifact_id, steam_root),
    )


def _command_fingerprint_for(document: dict[str, object]) -> str:
    data = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    return "containment-command-sha256:" + hashlib.sha256(data).hexdigest()


def _prepare_predecessor_run_ids(
    store: ContainmentStore,
    command_fingerprint: str,
    retry_of: ScenarioRecovery | None,
    *,
    preparation_recovery: PreparationRecovery | None = None,
) -> tuple[str, ...]:
    run_ids: list[str] = []
    unresolved: list[str] = []
    available_retries: list[tuple[str, ContainmentScenario, str]] = []
    outstanding_retries: list[str] = []
    try:
        listed_run_ids = store.list_run_ids()
    except ContainmentStoreError as error:
        raise ContainmentServiceError(
            f"cannot enumerate containment runs: {error}"
        ) from error
    for run_id in listed_run_ids:
        try:
            intent = store.load_intent(run_id)
            fingerprint = _intent_command_fingerprint(intent, run_id)
        except (ContainmentStoreError, ContainmentServiceError) as error:
            raise ContainmentServiceError(
                f"unresolved run intent blocks prepare: {run_id}: {error}"
            ) from error
        if fingerprint != command_fingerprint:
            continue
        run_ids.append(run_id)
        try:
            store.load_decision(run_id)
        except ContainmentStoreError:
            try:
                replacement = store.load_preparation_replacement(run_id)
            except ContainmentStoreNotFound:
                unresolved.append(run_id)
            else:
                _load_preparation_successor(store, replacement, command_fingerprint)
                if isinstance(replacement, PreparationReplacementV2):
                    try:
                        store.load_preparation_startup_failure(run_id)
                    except ContainmentStoreNotFound:
                        pass
                    else:
                        # A failed reserved identity remains unresolved even if
                        # death preceded creation of its physical run directory.
                        # Its disposition is lineage, never another retry grant.
                        unresolved.append(replacement.consumed_by_run_id)
        for scenario in ContainmentScenario:
            try:
                authority = store.load_retry_authority(run_id, scenario)
            except ContainmentStoreNotFound:
                continue
            except ContainmentStoreError as error:
                raise ContainmentServiceError(
                    f"unresolved retry authority blocks prepare: "
                    f"{run_id}:{scenario.value}: {error}"
                ) from error
            if authority["state"] == "Available":
                available_retries.append(
                    (run_id, scenario, str(authority["recoveryId"]))
                )
                outstanding_retries.append(
                    f"available:{run_id}:{scenario.value}"
                )
                continue
            consumed_by = str(authority["consumedByRunId"])
            try:
                consumed_intent = store.load_intent(consumed_by)
                if (
                    _intent_command_fingerprint(consumed_intent, consumed_by)
                    != command_fingerprint
                ):
                    raise ContainmentServiceError(
                        "consumed retry target command fingerprint differs"
                    )
                store.load_decision(consumed_by)
            except (ContainmentStoreError, ContainmentServiceError):
                outstanding_retries.append(
                    f"consumed-unresolved:{run_id}:{scenario.value}:{consumed_by}"
                )
    predecessors = tuple(sorted(set(run_ids)))
    unresolved_runs = tuple(sorted(set(unresolved)))
    if preparation_recovery is not None:
        if type(preparation_recovery) is not PreparationRecovery or retry_of is not None:
            raise ContainmentServiceError("preparation replacement requires an exact distinct recovery")
        stored = store.load_preparation_recovery(preparation_recovery.run_id)
        if (stored != preparation_recovery or not stored.fresh_run_permitted
                or stored.command_fingerprint != command_fingerprint
                or unresolved_runs != (stored.run_id,) or outstanding_retries):
            raise ContainmentServiceError("preparation authority must identify the sole unresolved same-command predecessor")
        return predecessors
    if retry_of is None:
        if unresolved_runs or outstanding_retries:
            raise ContainmentServiceError(
                "nonterminal same-command run requires its exact retry authority: "
                + ",".join((*unresolved_runs, *outstanding_retries))
            )
        return predecessors
    if not isinstance(retry_of, ScenarioRecovery):
        raise ContainmentServiceError("retry_of must be an exact ScenarioRecovery")
    expected_available = (
        retry_of.run_id,
        retry_of.scenario,
        store.recovery_id_for(retry_of),
    )
    if expected_available not in available_retries:
        raise ContainmentServiceError(
            "retry authority is consumed or not the exact Available same-command authority"
        )
    other_unresolved = tuple(
        item for item in unresolved_runs if item != retry_of.run_id
    )
    expected_label = (
        f"available:{retry_of.run_id}:{retry_of.scenario.value}"
    )
    other_authorities = tuple(
        item for item in outstanding_retries if item != expected_label
    )
    if other_unresolved or other_authorities:
        raise ContainmentServiceError(
            "retry authority must identify the sole unresolved same-command run: "
            + ",".join((*other_unresolved, *other_authorities))
        )
    return predecessors


def _require_reserved_startup_only(store, replacement):
    """Observe known startup bytes only; this never grants object ownership."""
    if _path_exists_no_follow(store.preparation_path(replacement.run_id, "startup-initialized")):
        raise ContainmentServiceError("startup initialization barrier is present or uncertain")
    root = store.run_path(replacement.consumed_by_run_id)
    if not _path_exists_no_follow(root):
        return
    store._require_existing_direct_directory(root, "reserved startup root")
    expected = {
        store.intent_path(replacement.consumed_by_run_id): replacement.successor_intent.encode("utf-8"),
        store.preparation_path(replacement.consumed_by_run_id, "attempt"): record_to_bytes(replacement.successor_attempt),
    }
    for path in root.iterdir():
        if path in expected:
            if store._read(path, "reserved startup evidence") != expected[path]:
                raise ContainmentServiceError("reserved startup evidence was substituted")
        elif path.name in {"scenarios", "quarantine"}:
            store._require_existing_direct_directory(path, "reserved startup scaffold")
            if tuple(path.iterdir()):
                raise ContainmentServiceError("reserved startup has started or unknown child evidence")
        else:
            raise ContainmentServiceError("reserved startup has fixture or unknown evidence")
    if (_path_exists_no_follow(store.preparation_path(replacement.consumed_by_run_id, "attempt"))
            and not _path_exists_no_follow(store.intent_path(replacement.consumed_by_run_id))):
        raise ContainmentServiceError("reserved startup attempt lacks its intent")


def _reconcile_preparation_startup(store, old_run_id):
    try:
        replacement = store.load_preparation_replacement(old_run_id)
    except ContainmentStoreNotFound:
        return
    if not isinstance(replacement, PreparationReplacementV2):
        return  # Historical v1 has no reserved source/native data to invent.
    try:
        store.load_preparation_startup_failure(old_run_id)
    except ContainmentStoreNotFound:
        pass
    else:
        return
    if _path_exists_no_follow(store.preparation_path(old_run_id, "startup-initialized")):
        # Not a startup crash. Normal preparation failure/recovery is separate.
        store.load_preparation_startup_initialized(old_run_id)
        return
    with store._run_lock(replacement.consumed_by_run_id):
        _require_reserved_startup_only(store, replacement)
        proof = _preparation_absence_proof(replacement.successor_attempt)
        _require_reserved_startup_only(store, replacement)
        if store.load_preparation_replacement(old_run_id) != replacement:
            raise ContainmentServiceError("startup reservation changed during absence proof")
        store.write_preparation_startup_failure(PreparationStartupFailure(
            *_startup_record_fields(replacement), "AbandonedStartup", "interrupted-startup",
            "current reserved-controller and supported-candidate absence; no initialization barrier; no historic exit or object ownership attested",
            (), proof))


def _load_preparation_successor(store, replacement, fingerprint):
    _require_early_preparation(store, replacement.run_id)
    if isinstance(replacement, PreparationReplacementV2):
        try:
            store.load_preparation_startup_failure(replacement.run_id)
        except ContainmentStoreNotFound:
            pass
        else:
            if replacement.command_fingerprint != fingerprint:
                raise ContainmentServiceError("failed startup successor command differs")
            # Exact lineage comes from the old-root reservation, not adoption of
            # possibly missing/ambiguous physical files under the new namespace.
            return replacement.successor_attempt
    successor = store.load_preparation_attempt(replacement.consumed_by_run_id)
    intent = store.load_intent(successor.run_id)
    if (successor.replaces_recovery_id != replacement.recovery_id
            or successor.command_fingerprint != fingerprint
            or replacement.command_fingerprint != fingerprint
            or intent["retryOf"] is not None
            or replacement.run_id not in intent["predecessorRunIds"]):
        raise ContainmentServiceError("preparation replacement successor lineage differs")
    if isinstance(replacement, PreparationReplacementV2):
        store.load_preparation_startup_initialized(replacement.run_id)
        if successor != replacement.successor_attempt:
            raise ContainmentServiceError("successor attempt differs from reservation")
    return successor


def _intent_command_fingerprint(
    intent: dict[str, object],
    run_id: str,
) -> str:
    if (
        intent.get("runId") != run_id
        or intent.get("mechanism") != _MECHANISM
        or type(intent.get("sourceWorkspace")) is not str
        or type(intent.get("mo2ArtifactId")) is not str
        or not intent.get("mo2ArtifactId")
        or type(intent.get("steamRoot")) is not str
        or type(intent.get("commandFingerprint")) is not str
    ):
        raise ContainmentServiceError("run intent command binding is malformed")
    computed = _known_command_fingerprints(
        Path(intent["sourceWorkspace"]),
        intent["mo2ArtifactId"],
        Path(intent["steamRoot"]),
    )
    if intent["commandFingerprint"] not in computed:
        raise ContainmentServiceError("run intent command fingerprint does not recompute")
    return intent["commandFingerprint"]


def _intent_id_for(intent: dict[str, object]) -> str:
    data = (
        json.dumps(
            intent,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    return "containment-intent-sha256:" + hashlib.sha256(data).hexdigest()


def _valid_predecessor_run_ids(
    value: object,
    current_run_id: str,
) -> bool:
    if type(value) is not list or any(type(item) is not str for item in value):
        return False
    run_ids = tuple(value)
    if run_ids != tuple(sorted(set(run_ids))) or current_run_id in run_ids:
        return False
    try:
        for run_id in run_ids:
            ContainmentStore._run_hex(run_id)
    except ContainmentStoreError:
        return False
    return True


def _valid_retry_binding(value: object, command_fingerprint: object) -> bool:
    if value is None:
        return True
    fields = {"runId", "scenario", "recoveryId", "authorityId", "commandFingerprint"}
    if type(value) is not dict or set(value) != fields:
        return False
    try:
        ContainmentStore._run_hex(value["runId"])
        ContainmentScenario(value["scenario"])
    except (ContainmentStoreError, TypeError, ValueError):
        return False
    return (
        value["commandFingerprint"] == command_fingerprint
        and type(value["recoveryId"]) is str
        and re.fullmatch(r"containment-recovery-sha256:[0-9a-f]{64}", value["recoveryId"])
        is not None
        and type(value["authorityId"]) is str
        and re.fullmatch(r"containment-retry-sha256:[0-9a-f]{64}", value["authorityId"])
        is not None
    )


__all__ = [
    "ContainmentEffects",
    "ContainmentDecisionNotReady",
    "ContainmentOperationError",
    "ContainmentServiceResult",
    "ContainmentServiceError",
    "FixtureRecord",
    "RecoveryCleanupEvidence",
    "RecoveryProofEvidence",
    "ScenarioEvidence",
    "adjudicate_results",
    "adjudicate_run",
    "arm_scenario",
    "capture_scenario",
    "evaluate_scenario",
    "launch_scenario",
    "load_decision",
    "load_result",
    "prepare_run",
    "recover_scenario",
]
