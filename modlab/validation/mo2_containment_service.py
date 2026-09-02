"""Crash-safe orchestration and fail-closed adjudication for MO2 containment."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, replace
from functools import partial, wraps
import hashlib
import json
import os
from pathlib import Path
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
    ExactObjectError,
    ExactObjectOwnershipError,
    RetainedObjectOwner,
    RetainedObjectRole,
    pin_stable_direct_object,
    read_pinned_file,
)
from modlab.workspace import workspace_layout

from .mo2_containment_fixtures import (
    ContainmentFixture,
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
    ContainmentStore,
    ContainmentStoreError,
    ContainmentStoreNotFound,
    ContainmentStoreOwnershipError,
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
    adopt_unique_staged_mod,
    create_mod_projection,
    ensure_direct_subdirectory,
    inspect_junction,
    quarantine_exact_object,
    quarantine_replacement_tree,
    stable_tree_identity,
)
from . import windows_watch as _windows_watch
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
_EXPECTED_NEW = {
    ContainmentScenario.NEW_FOLDER: "ModLab Spike New",
    ContainmentScenario.MERGE_EXISTING: _PROTECTED_NAME,
    ContainmentScenario.REPLACE_EXISTING: _PROTECTED_NAME,
    ContainmentScenario.FOMOD_DEPENDENCY: "ModLab Spike FOMOD",
}
_EXPECTED_OUTPUTS = {
    ContainmentScenario.NEW_FOLDER: ("meshes/new-folder.bin", "meta.ini"),
    ContainmentScenario.FOMOD_DEPENDENCY: (
        "always.txt",
        "dependency-seen.txt",
        "meta.ini",
    ),
}
_EXPECTED_REPLACEMENT_OUTPUTS = (
    "meshes/canary.bin",
    "meshes/new.bin",
    "meta.ini",
)


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
        return tuple(rows)

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
        )


_ACTIVE_EFFECTS: ContextVar[_EffectLedger | None] = ContextVar(
    "mo2_containment_effects",
    default=None,
)
_ACTIVE_GUARDED_MUTATION_ROOTS: ContextVar[tuple[Path, ...]] = ContextVar(
    "mo2_containment_guarded_mutation_roots",
    default=(),
)


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


def _mutation_root_guard(
    supplied_root: Path,
    existing_guards: tuple[object, ...] = (),
) -> object | None:
    """Pin the nearest existing object and prove its entire pathname chain.

    The retained descendant denies rename/delete for itself and, on Windows,
    consequently denies replacing any ancestor through which that open handle
    was resolved.  Comparing every component on both sides of acquisition
    closes the only gap before that denial is in force without requiring
    DELETE access to protected ancestors such as ``C:\\Users``.
    """
    root = Path(supplied_root)
    if os.name != "nt":
        return None
    if not root.is_absolute():
        raise _MutationObservationError(
            f"delegated mutation root must be absolute: {root}"
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
        observed_chain.append((current, identity(metadata), metadata))
    if not observed_chain:
        raise _MutationObservationError(
            f"delegated mutation path has no observable direct ancestor: {root}"
        )
    existing, _existing_identity, existing_metadata = observed_chain[-1]
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
    assert pinned is not None
    try:
        if (
            pinned.identity is None
            or int(existing_metadata.st_ino) != pinned.identity.file_id
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
                or identity(metadata) != expected_identity
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
                        verification=(pinned,),
                    )
                    raise ContainmentStoreOwnershipError(
                        "delegated mutation guard requires retained-handle resolution",
                        ownership,
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
    if not unresolved:
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
    prior_owners = (
        primary.ownership.owners
        if isinstance(primary, ContainmentStoreOwnershipError)
        else ()
    )
    ownership = ExactObjectOwnershipError(
        "delegated mutation guard retained live ownership after cleanup failure",
        owners=(
            *prior_owners,
            *(
                RetainedObjectOwner(RetainedObjectRole.VERIFICATION, pinned)
                for pinned in unresolved
            ),
        ),
    )
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
) -> tuple[tuple[object, ...], ...] | None:
    """Take one stable, non-following inventory used to prove delegated mutation."""
    root = Path(supplied_root)
    if not root.is_absolute():
        raise _MutationObservationError(
            f"delegated mutation root must be absolute: {root}"
        )

    rows: list[tuple[object, ...]] = []
    retained: list[object] = []

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

    def close_retained(primary: BaseException | None = None) -> None:
        unresolved = []
        close_errors: list[BaseException] = []
        for pinned in reversed(retained):
            try:
                pinned.close()
            except BaseException as error:
                close_errors.append(error)
                if getattr(pinned, "handle", 0):
                    unresolved.append(pinned)
        if unresolved:
            ownership = ExactObjectOwnershipError(
                "stable delegated-effect observation retained live handles: "
                + "; ".join(str(error) for error in close_errors),
                verification=tuple(unresolved),
            )
            raise ContainmentStoreOwnershipError(
                "stable delegated-effect observation requires retained-handle resolution",
                ownership,
            ) from primary
        if close_errors and primary is None:
            raise ContainmentServiceError(
                "stable delegated-effect observation handle cleanup failed: "
                + "; ".join(str(error) for error in close_errors)
            ) from close_errors[0]

    def stable_pin(path: Path, kind: str) -> object | None:
        if os.name != "nt":
            return None
        pinned = pin_stable_direct_object(path, kind)
        retained.append(pinned)
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
            path_identities.append((component, identity(metadata)))
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
        return (
            relative,
            *identity(metadata),
            file_digest(path, metadata),
        )

    try:
        rows.append(metadata_row(".", root, root_metadata))
    except _MutationObservationError as error:
        close_retained(error)
        raise
    except (ExactObjectError, OSError):
        close_retained()
        return None

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
        if any(redirected(metadata) for _name, _path, metadata in rescanned):
            raise _MutationObservationError(
                f"delegated mutation directory gained a reparse entry: {current}"
            )
        if before_rows != after_rows or identity(current.lstat()) != identity(expected):
            raise OSError(f"delegated mutation directory changed during scan: {current}")

    try:
        visit(root, Path(), root_metadata)
        for component, expected_identity in path_identities:
            metadata = component.lstat()
            if redirected(metadata):
                raise _MutationObservationError(
                    f"delegated mutation path became redirected or reparse: {component}"
                )
            if identity(metadata) != expected_identity:
                raise OSError(
                    f"delegated mutation path changed during observation: {component}"
                )
    except _MutationObservationError as error:
        close_retained(error)
        raise
    except (ExactObjectError, OSError):
        close_retained()
        return None
    close_retained()
    return tuple(rows)


def _delegated_mutations_guarded(
    roots: tuple[Path, ...],
    operation: Callable[[], _V],
) -> _V:
    """Record delegated roots iff pre/post inventories prove they changed."""
    exact_roots = _unique_paths(roots)
    before_rows: list[tuple[tuple[object, ...], ...] | None] = []
    try:
        for root in exact_roots:
            before_rows.append(_mutation_root_observation(root))
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
        for root, prior in zip(exact_roots, before, strict=True):
            try:
                current = _mutation_root_observation(root)
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
        try:
            record_observed_changes()
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
    record_observed_changes()
    return value


def _delegated_mutations(
    roots: tuple[Path, ...],
    operation: Callable[[], _V],
) -> _V:
    exact_roots = _unique_paths(roots)
    guards: list[object] = []
    primary: BaseException | None = None
    try:
        for root in exact_roots:
            guard = _mutation_root_guard(root, tuple(guards))
            if guard is not None:
                guards.append(guard)
        token = _ACTIVE_GUARDED_MUTATION_ROOTS.set(exact_roots)
        try:
            return _delegated_mutations_guarded(exact_roots, operation)
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


@_receipted
def prepare_run(
    source_workspace: Path,
    mo2_artifact_id: str,
    steam_root: Path,
    validation_root: Path,
    *,
    retry_of: ScenarioRecovery | None = None,
) -> str:
    """Create four independent disposable fixtures and one immutable request."""
    source = Path(source_workspace).expanduser().absolute()
    validation = Path(validation_root).expanduser().absolute()
    expected_validation = workspace_layout(source).mo2_containment_validation
    if not _same_path(validation, expected_validation):
        raise ContainmentServiceError("validation root must be the workspace containment root")
    run_id = "containment-run:" + uuid.uuid4().hex
    store = _effect_store(validation)
    steam = Path(steam_root).expanduser().absolute()
    command_fingerprint = _command_fingerprint(source, mo2_artifact_id, steam)
    with store.command_lock(command_fingerprint):
        predecessor_run_ids = _prepare_predecessor_run_ids(
            store,
            command_fingerprint,
            retry_of,
        )
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
        intent_write = store.write_intent(run_id, intent)
        fixture_root = store.run_path(run_id) / "fixtures"
        fixtures: list[ContainmentFixture] = []
        for scenario in ContainmentScenario:
            fixture_parent = fixture_root / scenario.value
            fixtures.append(
                _delegated_mutation(
                    fixture_parent,
                    partial(
                        prepare_containment_fixture,
                        source,
                        mo2_artifact_id,
                        steam,
                        validation,
                        scenario,
                        fixture_parent=fixture_parent,
                    ),
                )
            )
        records = tuple(_fixture_record(fixture, steam) for fixture in fixtures)
        document = {
            **intent,
            "intentId": intent_write.content_id,
            "scenarios": [_record_document(record) for record in records],
        }
        store.write_request(run_id, document)
    return run_id


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
    projection = _delegated_mutations(
        (
            record.source_mods,
            record.stage_mods,
            store.quarantine_path(run_id) / scenario.value,
        ),
        lambda: _finalize_projection(
            store,
            run_id,
            record,
            journal.protected_before,
            post_mo2,
        ),
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
                _load_bound_request(store, historical_run_id, historical_intent)
                if historical_fingerprint != current_fingerprint:
                    raise ContainmentServiceError(
                        "listed predecessor command fingerprint differs"
                    )
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


def _changed_projection_names(record: FixtureRecord) -> tuple[str, ...]:
    stage_names = set(_direct_names(record.stage_mods))
    changed: list[str] = []
    for name in record.before_names:
        if name not in stage_names:
            continue
        try:
            evidence = inspect_junction(record.stage_mods / name)
        except (OSError, ContainmentSafetyError):
            changed.append(name)
            continue
        if not _same_path(evidence.target_path, record.source_mods / name):
            changed.append(name)
    return tuple(changed)


def _finalize_projection(
    store: ContainmentStore,
    run_id: str,
    record: FixtureRecord,
    before: ProtectedState,
    post_mo2: ProtectedState,
) -> _ProjectionEvidence:
    incomplete: list[str] = []
    try:
        stage_names = _direct_names(record.stage_mods)
        staging_complete = True
    except OSError as error:
        stage_names = ()
        staging_complete = False
        incomplete.append(f"staging-observation-unavailable:{type(error).__name__}")
    try:
        source_names = _direct_names(record.source_mods)
        production_complete = True
    except OSError as error:
        source_names = record.before_names
        production_complete = False
        incomplete.append(f"production-observation-unavailable:{type(error).__name__}")
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
    quarantine = ensure_direct_subdirectory(
        store.quarantine_path(run_id),
        record.scenario.value,
    )

    if record.scenario in _EXPECTED_OUTPUTS:
        expected = _EXPECTED_NEW[record.scenario]
        adoption = None
        if post_mo2 != before:
            safety.append("protected-state-changed-before-adoption")
            try:
                changed_names = _changed_projection_names(record)
            except OSError as error:
                changed_names = ()
                staging_complete = False
                incomplete.append(
                    f"staging-reobservation-unavailable:{type(error).__name__}"
                )
            quarantine_names = (
                tuple(dict.fromkeys((*changed_names, *new_names)))
                if staging_complete
                else ()
            )
            for name in quarantine_names:
                try:
                    _quarantine_exact(record.stage_mods / name, quarantine)
                except (OSError, ContainmentSafetyError):
                    incomplete.append("staging-quarantine-failed")
        else:
            try:
                adoption = adopt_unique_staged_mod(
                    stage_mods=record.stage_mods,
                    source_mods=record.source_mods,
                    expected_name=expected,
                    before_names=record.before_names,
                    quarantine_root=quarantine,
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
                        _quarantine_exact(adoption.destination_path, quarantine)
                    except (OSError, ContainmentSafetyError):
                        incomplete.append("adopted-source-quarantine-failed")
        final = _capture_protected(record)
        restored = final == before
    else:
        unexpected = tuple(name for name in stage_names if name not in record.before_names)
        try:
            changed = _changed_projection_names(record)
        except OSError as error:
            changed = ()
            staging_complete = False
            incomplete.append(
                f"staging-reobservation-unavailable:{type(error).__name__}"
            )
        replacement_candidate = (
            record.scenario is ContainmentScenario.REPLACE_EXISTING
            and not unexpected
            and changed == (_PROTECTED_NAME,)
        )
        if replacement_candidate:
            replacement = record.stage_mods / _PROTECTED_NAME
            try:
                replacement_evidence = quarantine_replacement_tree(
                    stage_mods=record.stage_mods,
                    expected_name=_PROTECTED_NAME,
                    quarantine_root=quarantine,
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
            quarantine_names = (
                tuple(dict.fromkeys((*changed, *unexpected)))
                if staging_complete
                else ()
            )
            for name in quarantine_names:
                try:
                    _quarantine_exact(record.stage_mods / name, quarantine)
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
    try:
        _delegated_mutation(
            recovery_quarantine,
            lambda: recovery_quarantine.mkdir(exist_ok=True),
        )
        stage_names = _direct_names(record.stage_mods)
        changed = _changed_projection_names(record)
        unexpected = tuple(name for name in stage_names if name not in record.before_names)
        for name in dict.fromkeys((*changed, *unexpected)):
            source = record.stage_mods / name
            _delegated_mutations(
                (record.stage_mods, recovery_quarantine),
                partial(
                    _quarantine_exact,
                    source,
                    recovery_quarantine,
                ),
            )
    except (OSError, ContainmentSafetyError):
        blockers.append("staging-quarantine-failed")

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


def _quarantine_exact(source: Path, quarantine: Path) -> None:
    quarantine_exact_object(source, quarantine)


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
            unresolved.append(run_id)
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
