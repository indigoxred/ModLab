"""Crash-safe confined storage for MO2 containment scenario evidence."""

from __future__ import annotations

from .mo2_preparation_recovery import (
    PreparationAttempt, PreparationFailure, PreparationProjection,
    PreparationReplacementV2, PreparationStartupInitialized, PreparationStartupFailure,
    PreparationRecovery, PreparationReplacement, PreparationCleanup, record_from_bytes,
    record_to_bytes, record_id,
)

import ctypes
from ctypes import wintypes
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass, replace, fields
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
from typing import Callable, Generic, TypeVar
import uuid

from modlab.platform.windows_exact_fs import (
    ExactObjectError,
    ExactObjectOwnershipError,
    publish_new_pinned,
    resolve_retained_ownership,
    pin_stable_direct_object, pin_direct_object, read_pinned_file,
    create_pinned_directory_child, RetainedObjectOwner, RetainedObjectRole,
    union_retained_ownership,
)
from .mo2_containment_authority import (
    CapabilityEligibility,
    CapabilityRetirement,
    CapabilityReview,
    CapabilitySupersession,
)
from .mo2_containment_model import (
    CapabilityDecision,
    CapabilityVerdict,
    ContainmentScenario,
    IntegrityObservation,
    ProtectedState,
    ProcessEvidence,
    ScenarioJournal,
    ScenarioRecovery,
    ScenarioResult,
    ScenarioState,
    TreeIdentity,
    WatchOutcome,
)
from .mo2_containment_serialization import (
    _capability_decision_probe_from_bytes,
    ContainmentFormatError,
    capability_eligibility_from_bytes,
    capability_eligibility_id_for,
    capability_eligibility_to_bytes,
    capability_decision_from_bytes,
    capability_decision_to_bytes,
    capability_retirement_from_bytes,
    capability_retirement_id_for,
    capability_retirement_to_bytes,
    capability_review_from_bytes,
    capability_review_id_for,
    capability_review_to_bytes,
    capability_supersession_from_bytes,
    capability_supersession_id_for,
    capability_supersession_to_bytes,
    scenario_journal_from_bytes,
    scenario_journal_to_bytes,
    scenario_recovery_from_bytes,
    scenario_recovery_to_bytes,
    scenario_result_from_bytes,
    scenario_result_id_for,
    scenario_result_to_bytes,
    watch_outcome_from_bytes,
    watch_outcome_id_for,
    watch_outcome_to_bytes,
)


from .mo2_containment_evaluation import ScenarioEvidence, evaluate_scenario
from .windows_vault_security import create_vault, open_vault, pin_trusted_paths


def evidence_root_for(validation_root: Path) -> Path:
    """Deterministic authority namespace, never taken from disposable records."""
    root = Path(validation_root).expanduser().absolute()
    if root.parent.name == "validation":
        return root.parent.parent / "validation-authority" / root.name
    return root.with_name(root.name + "-authority")


PROTECTED_STATE_LABELS = ("before", "after")


_RUN = re.compile(r"^containment-run:([0-9a-f]{32})$")
_DECISION_ID = re.compile(r"^containment-decision-sha256:[0-9a-f]{64}$")
_AUTHORITY_KINDS = {
    "retirements": "containment-retirement-sha256:",
    "supersessions": "containment-supersession-sha256:",
    "reviews": "containment-review-sha256:",
    "eligibilities": "containment-eligibility-sha256:",
}
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_PROCESS_LOCKS: dict[str, threading.Lock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()
_UNSET = object()
_T = TypeVar("_T")


class ContainmentStoreError(RuntimeError):
    """A containment document could not be stored or proved exact."""


class ContainmentStoreOwnershipError(ContainmentStoreError):
    """An immutable store write leaves explicitly-owned exact handles live."""

    def __init__(self, message: str, ownership: ExactObjectOwnershipError) -> None:
        super().__init__(message)
        self.ownership = ownership

    @property
    def candidate(self):
        return self.ownership.candidate

    @property
    def candidates(self):
        return self.ownership.candidates

    @property
    def destination_parent(self):
        return self.ownership.destination_parent

    @property
    def destination_parents(self):
        return self.ownership.destination_parents

    @property
    def verification(self):
        return self.ownership.verification

    @property
    def owners(self):
        return self.ownership.owners

    @property
    def retained_objects(self):
        return self.ownership.retained_objects

    def resolve(self) -> None:
        try:
            self.ownership.resolve()
        except ExactObjectOwnershipError as unresolved:
            self.ownership = unresolved
            raise self from unresolved


class ContainmentStoreMalformedEvidence(ContainmentStoreError):
    """Stored containment evidence exists but cannot be parsed exactly."""


class ContainmentStoreNotFound(ContainmentStoreError):
    """A required containment document is absent."""


@dataclass(frozen=True)
class ImmutableWrite(Generic[_T]):
    value: _T
    content_id: str
    path: Path
    existed: bool


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def mutable_replacement_part_path(target: Path, token: str) -> Path:
    """Construct the exact temporary pathname used by mutable journal replace."""
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}", token):
        raise ContainmentStoreError("mutable replacement token must be 32 lowercase hex characters")
    return target.with_name(f".{target.name}.{token}.part")


def _unique_json(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ContainmentStoreError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


@contextmanager
def _windows_run_mutex(identity: str, *, blocking: bool = False):
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
    kernel32.ReleaseMutex.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.CreateMutexW(
        None,
        False,
        f"Local\\ModLab-MO2-Containment-{identity}",
    )
    if not handle:
        raise ContainmentStoreError(
            f"cannot create containment run mutex: Windows error {ctypes.get_last_error()}"
        )
    acquired = False
    primary_error: BaseException | None = None
    try:
        result = kernel32.WaitForSingleObject(handle, 0xFFFFFFFF if blocking else 0)
        if result == 0x00000102:
            raise ContainmentStoreError("containment run is already being changed")
        if result not in {0x00000000, 0x00000080}:
            raise ContainmentStoreError(
                f"cannot acquire containment run mutex: Windows result {result:#x}"
            )
        acquired = True
        yield
    except BaseException as error:
        primary_error = error
        raise
    finally:
        cleanup_errors: list[str] = []
        if acquired and not kernel32.ReleaseMutex(handle):
            cleanup_errors.append(
                f"ReleaseMutex failed with Windows error {ctypes.get_last_error()}"
            )
        if not kernel32.CloseHandle(handle):
            cleanup_errors.append(
                f"CloseHandle failed with Windows error {ctypes.get_last_error()}"
            )
        if cleanup_errors:
            detail = "; ".join(cleanup_errors)
            if primary_error is None:
                raise ContainmentStoreError(detail)
            primary_error.add_note(detail)


class ContainmentStore:
    """Store one immutable evidence graph below one validation root."""

    _TRANSITIONS = {
        ScenarioState.PREPARED: {ScenarioState.ARMED, ScenarioState.RECOVERY_REQUIRED},
        ScenarioState.ARMED: {
            ScenarioState.SCENARIO_STARTED,
            ScenarioState.RECOVERY_REQUIRED,
        },
        ScenarioState.SCENARIO_STARTED: {
            ScenarioState.LAUNCHED,
            ScenarioState.RECOVERY_REQUIRED,
        },
        ScenarioState.LAUNCHED: {
            ScenarioState.CAPTURED,
            ScenarioState.RECOVERY_REQUIRED,
        },
        ScenarioState.CAPTURED: set(),
        ScenarioState.RECOVERY_REQUIRED: set(),
    }

    def __init__(
        self,
        validation_root: Path,
        *,
        _effect_recorder: Callable[[Path], None] | None = None,
        authority_root: Path | None = None,
    ):
        self._effect_recorder = _effect_recorder
        self.evidence_root = Path(authority_root).absolute() if authority_root is not None else evidence_root_for(validation_root)
        self._open_root(validation_root, create=True)

    @classmethod
    def open_readonly(cls, validation_root: Path, *, authority_root: Path | None = None) -> "ContainmentStore":
        """Open one existing direct validation root without preparing any path."""
        store = cls.__new__(cls)
        store._effect_recorder = None
        store.evidence_root = Path(authority_root).absolute() if authority_root is not None else evidence_root_for(validation_root)
        store._open_root(validation_root, create=False)
        return store

    def _open_root(self, validation_root: Path, *, create: bool) -> None:
        root = Path(validation_root).expanduser().absolute()
        self.root = root
        if root == self.evidence_root or root in self.evidence_root.parents or self.evidence_root in root.parents:
            raise ContainmentStoreError("disposable and authority roots must be separate")
        if create:
            self._create_trusted_directories(root)
        else:
            self._require_existing_direct_directory(root, "validation root")
        try:
            resolved = root.resolve(strict=True)
        except OSError as error:
            raise ContainmentStoreError(f"validation root is unavailable: {error}") from error
        if os.path.normcase(str(resolved)) != os.path.normcase(str(root)):
            raise ContainmentStoreError("validation root must not be redirected")

    def create(self, journal: ScenarioJournal) -> ScenarioJournal:
        data = self._serialize(scenario_journal_to_bytes, journal, "journal")
        target = self.journal_path(journal.run_id, journal.scenario)
        with self._run_lock(journal.run_id):
            self._prepare_scenario(journal.run_id, journal.scenario)
            existed = self._write_immutable(target, data, "journal")
            if existed and self._read(target, "journal") != data:
                raise ContainmentStoreError("stored journal contains different bytes")
            loaded = self._load_journal_unlocked(journal.run_id, journal.scenario)
            if loaded != journal:
                raise ContainmentStoreError("stored journal differs after create")
            return loaded

    def prepare_run_root(self, run_id: str) -> Path:
        with self._run_lock(run_id):
            self._prepare_run(run_id)
            return self.run_path(run_id)

    def transition(
        self,
        observed: ScenarioJournal,
        new_state: ScenarioState,
        *,
        monitor_pid: int | None | object = _UNSET,
        mo2_pid: int | None | object = _UNSET,
        error: str | None | object = _UNSET,
    ) -> ScenarioJournal:
        if not isinstance(observed, ScenarioJournal):
            raise ContainmentStoreError("transition requires an exact ScenarioJournal")
        if not isinstance(new_state, ScenarioState):
            raise ContainmentStoreError("transition state must be ScenarioState")
        target = self.journal_path(observed.run_id, observed.scenario)
        observed_data = self._serialize(scenario_journal_to_bytes, observed, "journal")
        with self._run_lock(observed.run_id):
            current_data = self._read(target, "journal")
            if current_data != observed_data:
                raise ContainmentStoreError("scenario journal changed after observation")
            current = self._parse(scenario_journal_from_bytes, current_data, "journal")
            if current.state is new_state:
                if any(value is not _UNSET for value in (monitor_pid, mo2_pid, error)):
                    candidate = replace(
                        current,
                        monitor_pid=(current.monitor_pid if monitor_pid is _UNSET else monitor_pid),
                        mo2_pid=(current.mo2_pid if mo2_pid is _UNSET else mo2_pid),
                        error=(current.error if error is _UNSET else error),
                    )
                    if candidate != current:
                        raise ContainmentStoreError(
                            f"idempotent {new_state.value} transition cannot alter evidence"
                        )
                return current
            if new_state not in self._TRANSITIONS[current.state]:
                raise ContainmentStoreError(
                    f"scenario journal transition is not allowed: "
                    f"{current.state.value} -> {new_state.value}"
                )
            if new_state is ScenarioState.CAPTURED:
                try:
                    after = self._load_protected_state_unlocked(
                        current.run_id,
                        current.scenario,
                        "after",
                    )
                    result = self._load_result_unlocked(
                        current.run_id,
                        current.scenario,
                    )
                except ContainmentStoreError as evidence_error:
                    raise ContainmentStoreError(
                        "Captured requires durable after/result evidence"
                    ) from evidence_error
                if (
                    result.protected_before != current.protected_before
                    or result.protected_after != after
                    or result.mo2_process is None
                    or result.mo2_process.pid != current.mo2_pid
                ):
                    raise ContainmentStoreError(
                        "Captured requires durable after/result evidence bound to the journal"
                    )
            replacement = replace(
                current,
                state=new_state,
                monitor_pid=(current.monitor_pid if monitor_pid is _UNSET else monitor_pid),
                mo2_pid=(current.mo2_pid if mo2_pid is _UNSET else mo2_pid),
                error=(
                    f"recovery required from {current.state.value}"
                    if new_state is ScenarioState.RECOVERY_REQUIRED and error is _UNSET
                    else current.error if error is _UNSET else error
                ),
            )
            replacement_data = self._serialize(
                scenario_journal_to_bytes,
                replacement,
                "journal",
            )
            if new_state is ScenarioState.SCENARIO_STARTED:
                self.write_scenario_started(replacement)
            self._atomic_replace(target, replacement_data, current_data, "journal")
            loaded = self._load_journal_unlocked(current.run_id, current.scenario)
            if loaded != replacement:
                raise ContainmentStoreError("stored journal differs after transition")
            return loaded

    def load_journal(
        self,
        run_id: str,
        scenario: ContainmentScenario,
    ) -> ScenarioJournal:
        with self._run_lock(run_id):
            return self._load_journal_unlocked(run_id, scenario)

    def write_request(
        self,
        run_id: str,
        document: dict[str, object],
    ) -> ImmutableWrite[dict[str, object]]:
        self._run_hex(run_id)
        if type(document) is not dict or document.get("runId") != run_id:
            raise ContainmentStoreError("request document must bind the exact runId")
        data = _canonical(document)
        target = self.request_path(run_id)
        with self._run_lock(run_id):
            self._prepare_run(run_id)
            existed = self._write_immutable(target, data, "request")
            loaded = self._load_request_unlocked(run_id)
            if _canonical(loaded) != data:
                raise ContainmentStoreError("stored request differs after write")
            return ImmutableWrite(
                loaded,
                "containment-request-sha256:" + hashlib.sha256(data).hexdigest(),
                target,
                existed,
            )

    def load_request(self, run_id: str) -> dict[str, object]:
        with self._run_lock(run_id):
            return self._load_request_unlocked(run_id)

    def write_intent(
        self,
        run_id: str,
        document: dict[str, object],
        *, require_new: bool = False,
    ) -> ImmutableWrite[dict[str, object]]:
        checked = _intent_document(document, run_id)
        data = _canonical(checked)
        target = self.intent_path(run_id)
        with self._run_lock(run_id):
            self._prepare_run(run_id)
            existed = self._write_immutable(target, data, "run intent", require_new=require_new)
            loaded = self._load_intent_unlocked(run_id)
            if loaded != checked:
                raise ContainmentStoreError("stored run intent differs after write")
            return ImmutableWrite(
                loaded,
                "containment-intent-sha256:" + hashlib.sha256(data).hexdigest(),
                target,
                existed,
            )

    def load_intent(self, run_id: str) -> dict[str, object]:
        with self._run_lock(run_id):
            return self._load_intent_unlocked(run_id)

    def preparation_path(self, run_id: str, kind: str, scenario: str | None = None) -> Path:
        if kind not in {"attempt", "failure", "projection", "recovery", "replacement", "cleanup", "startup-initialized", "startup-failure"}:
            raise ContainmentStoreError("unknown preparation record kind")
        suffix = ""
        if kind in {"projection", "cleanup"}:
            try:
                return self.evidence_run_path(run_id) / "preparation-projections" / (ContainmentScenario(scenario).value + ("-cleanup" if kind == "cleanup" else "") + ".json")
            except (TypeError, ValueError) as error:
                raise ContainmentStoreError("invalid preparation projection scenario") from error
        elif scenario is not None:
            raise ContainmentStoreError("scenario cannot qualify this preparation record")
        return self.evidence_run_path(run_id) / ("preparation-" + kind + suffix + ".json")

    def _preparation_binding(self, value) -> None:
        intent = self._load_intent_unlocked(value.run_id)
        expected_id = "containment-intent-sha256:" + hashlib.sha256(_canonical(intent)).hexdigest()
        if value.intent_id != expected_id or value.command_fingerprint != intent["commandFingerprint"]:
            raise ContainmentStoreError("preparation record differs from its exact source intent")

    def _load_preparation(self, run_id: str, record_type, kind: str, scenario=None):
        try:
            value = record_from_bytes(self._read(self.preparation_path(run_id, kind, scenario), "preparation " + kind), record_type)
        except (ValueError, TypeError, UnicodeError) as error:
            raise ContainmentStoreMalformedEvidence(f"malformed preparation {kind}: {error}") from error
        if value.run_id != run_id:
            raise ContainmentStoreError("preparation record has a different run")
        self._validate_preparation_references(value, scenario)
        return value

    def _validate_preparation_references(self, value, scenario=None):
        run_id = value.run_id
        self._preparation_binding(value)
        if isinstance(value, PreparationFailure) and value.legacy:
            try:
                self.load_preparation_attempt(run_id)
            except ContainmentStoreNotFound:
                pass
            else:
                raise ContainmentStoreError("legacy failure cannot reinterpret a new-format preparation attempt")
        if isinstance(value, PreparationFailure) and value.attempt_id is not None:
            if value.attempt_id != record_id(self.load_preparation_attempt(run_id)):
                raise ContainmentStoreError("preparation failure attempt differs")
        if isinstance(value, PreparationProjection):
            if value.scenario != scenario or value.attempt_id != record_id(self.load_preparation_attempt(run_id)):
                raise ContainmentStoreError("preparation projection attempt/scenario differs")
        if isinstance(value, PreparationRecovery):
            if value.failure_id != record_id(self.load_preparation_failure(run_id)):
                raise ContainmentStoreError("preparation recovery failure differs")
        if isinstance(value, PreparationCleanup):
            projection = self.load_preparation_projection(run_id, scenario)
            if value.scenario != scenario or value.projection_id != record_id(projection):
                raise ContainmentStoreError("preparation cleanup projection differs")
        if isinstance(value, PreparationReplacement):
            recovery = self.load_preparation_recovery(run_id)
            if value.recovery_id != record_id(recovery) or not recovery.fresh_run_permitted:
                raise ContainmentStoreError("preparation replacement recovery differs or refuses replacement")

        if isinstance(value, PreparationReplacementV2):
            intent = _intent_document(json.loads(value.successor_intent), value.consumed_by_run_id)
            old_intent = self._load_intent_unlocked(run_id)
            if (intent["retryOf"] is not None or run_id not in intent["predecessorRunIds"]
                    or any(intent[key] != old_intent[key] for key in
                           ("mechanism", "sourceWorkspace", "mo2ArtifactId", "steamRoot", "commandFingerprint"))):
                raise ContainmentStoreError("reserved successor intent lineage differs")
        if isinstance(value, PreparationStartupInitialized):
            replacement = self.load_preparation_replacement(run_id)
            if (not isinstance(replacement, PreparationReplacementV2)
                    or value.replacement_id != record_id(replacement)
                    or value.successor_run_id != replacement.consumed_by_run_id):
                raise ContainmentStoreError("startup record differs from exact v2 reservation")
            if isinstance(value, PreparationStartupFailure) and value.disposition == "AbandonedStartup":
                process = value.process_proof[0]
                attempt = replacement.successor_attempt
                if (process.pid, process.creation_time) != (attempt.controller_pid, attempt.controller_creation_time):
                    raise ContainmentStoreError("startup absence differs from reserved controller")

    def _write_preparation(self, value, kind: str, scenario=None, *, require_new=False):
        try:
            data = record_to_bytes(value)
        except (ValueError, TypeError) as error:
            raise ContainmentStoreError(f"invalid preparation {kind}: {error}") from error
        with self._run_lock(value.run_id):
            self._validate_preparation_references(value, scenario)
            target = self.preparation_path(value.run_id, kind, scenario)
            existed = self._write_immutable(target, data, "preparation " + kind, require_new=require_new)
            loaded = self._load_preparation(value.run_id, type(value), kind, scenario)
            return ImmutableWrite(loaded, record_id(loaded), target, existed)

    def write_preparation_attempt(self, value: PreparationAttempt, *, require_new=False):
        return self._write_preparation(value, "attempt", require_new=require_new)

    def write_preparation_startup_initialized(self, value: PreparationStartupInitialized):
        return self._write_preparation(value, "startup-initialized", require_new=True)

    def load_preparation_startup_initialized(self, run_id):
        return self._load_preparation(run_id, PreparationStartupInitialized, "startup-initialized")

    def write_preparation_startup_failure(self, value: PreparationStartupFailure):
        return self._write_preparation(value, "startup-failure", require_new=True)

    def load_preparation_startup_failure(self, run_id):
        return self._load_preparation(run_id, PreparationStartupFailure, "startup-failure")

    def load_preparation_attempt(self, run_id: str) -> PreparationAttempt:
        return self._load_preparation(run_id, PreparationAttempt, "attempt")

    def write_preparation_projection(self, value: PreparationProjection):
        return self._write_preparation(value, "projection", value.scenario)

    def load_preparation_projection(self, run_id: str, scenario: str) -> PreparationProjection:
        return self._load_preparation(run_id, PreparationProjection, "projection", scenario)

    def write_preparation_failure(self, value: PreparationFailure):
        return self._write_preparation(value, "failure")

    def load_preparation_failure(self, run_id: str) -> PreparationFailure:
        return self._load_preparation(run_id, PreparationFailure, "failure")

    def write_preparation_recovery(self, value: PreparationRecovery):
        return self._write_preparation(value, "recovery")

    def load_preparation_recovery(self, run_id: str) -> PreparationRecovery:
        return self._load_preparation(run_id, PreparationRecovery, "recovery")

    def write_preparation_cleanup(self, value: PreparationCleanup):
        return self._write_preparation(value, "cleanup", value.scenario)

    def load_preparation_cleanup(self, run_id: str, scenario: str) -> PreparationCleanup:
        return self._load_preparation(run_id, PreparationCleanup, "cleanup", scenario)

    def load_preparation_replacement(self, run_id: str) -> PreparationReplacement:
        return self._load_preparation(run_id, PreparationReplacement, "replacement")

    def consume_preparation_replacement(self, recovery: PreparationRecovery, new_run_id: str, *, process_proof=None, reservation=None) -> PreparationReplacement:
        self._run_hex(new_run_id)
        with self._run_lock(recovery.run_id):
            stored = self.load_preparation_recovery(recovery.run_id)
            if recovery != stored or not recovery.fresh_run_permitted:
                raise ContainmentStoreError("replacement requires exact permissive preparation recovery")
            marker = self.preparation_path(recovery.run_id, "replacement")
            try:
                marker.lstat()
            except FileNotFoundError:
                pass
            else:
                raise ContainmentStoreError("preparation replacement is already consumed")
            try:
                self.run_path(new_run_id).lstat()
            except FileNotFoundError:
                pass
            else:
                raise ContainmentStoreError("replacement run must be completely fresh")
            value = PreparationReplacement(recovery.run_id, recovery.intent_id,
                recovery.command_fingerprint, record_id(recovery), new_run_id,
                recovery.process_proof if process_proof is None else process_proof)
            if reservation is not None:
                if (type(reservation) is not PreparationReplacementV2
                        or any(getattr(reservation, field) != getattr(value, field) for field in
                               ("run_id", "intent_id", "command_fingerprint", "recovery_id", "consumed_by_run_id", "process_proof"))):
                    raise ContainmentStoreError("replacement reservation differs from consumption")
                value = reservation
            return self._write_preparation(value, "replacement", require_new=True).value

    def write_launch_evidence(
        self,
        run_id: str,
        scenario: ContainmentScenario,
        document: dict[str, object],
    ) -> ImmutableWrite[dict[str, object]]:
        checked = _launch_document(document, run_id, scenario)
        data = _canonical(checked)
        target = self.launch_path(run_id, scenario)
        with self._run_lock(run_id):
            self._prepare_scenario(run_id, scenario)
            existed = self._write_immutable(target, data, "launch evidence")
            loaded = self._load_launch_evidence_unlocked(run_id, scenario)
            if loaded != checked:
                raise ContainmentStoreError("stored launch evidence differs after write")
            return ImmutableWrite(
                loaded,
                "containment-launch-sha256:" + hashlib.sha256(data).hexdigest(),
                target,
                existed,
            )

    def load_launch_evidence(
        self,
        run_id: str,
        scenario: ContainmentScenario,
    ) -> dict[str, object]:
        with self._run_lock(run_id):
            return self._load_launch_evidence_unlocked(run_id, scenario)

    def write_protected_state(
        self,
        run_id: str,
        scenario: ContainmentScenario,
        label: str,
        value: ProtectedState,
    ) -> ImmutableWrite[ProtectedState]:
        if label not in PROTECTED_STATE_LABELS:
            raise ContainmentStoreError("protected state label must be before or after")
        checked = _protected_from_document(_protected_document(value))
        data = _canonical(_protected_document(checked))
        target = self.scenario_path(run_id, scenario) / f"{label}.json"
        with self._run_lock(run_id):
            self._prepare_scenario(run_id, scenario)
            existed = self._write_immutable(target, data, f"{label} protected state")
            loaded = self._load_protected_state_unlocked(run_id, scenario, label)
            if loaded != checked:
                raise ContainmentStoreError(f"stored {label} state differs after write")
            return ImmutableWrite(
                loaded,
                f"containment-{label}-sha256:" + hashlib.sha256(data).hexdigest(),
                target,
                existed,
            )

    def load_protected_state(
        self,
        run_id: str,
        scenario: ContainmentScenario,
        label: str,
    ) -> ProtectedState:
        with self._run_lock(run_id):
            return self._load_protected_state_unlocked(run_id, scenario, label)

    def write_watch_outcome(
        self,
        value: WatchOutcome,
    ) -> ImmutableWrite[WatchOutcome]:
        data = self._serialize(watch_outcome_to_bytes, value, "watch outcome")
        identifier = watch_outcome_id_for(value)
        target = self.watch_path(value.run_id, value.scenario) / "outcome.json"
        with self._run_lock(value.run_id):
            self._prepare_scenario(value.run_id, value.scenario)
            self._ensure_direct_directory(target.parent)
            existed = self._write_immutable(target, data, "watch outcome")
            loaded = self._load_watch_outcome_unlocked(
                value.run_id,
                value.scenario,
                identifier,
            )
            return ImmutableWrite(loaded, identifier, target, existed)

    def load_watch_outcome(
        self,
        run_id: str,
        scenario: ContainmentScenario,
        expected_id: str | None = None,
    ) -> WatchOutcome:
        with self._run_lock(run_id):
            return self._load_watch_outcome_unlocked(run_id, scenario, expected_id)

    def write_result(
        self,
        value: ScenarioResult,
    ) -> ImmutableWrite[ScenarioResult]:
        with self._run_lock(value.run_id):
            watch = self._load_watch_outcome_unlocked(
                value.run_id,
                value.scenario,
                value.watch_outcome_id,
            )
            data = self._serialize(
                lambda item: scenario_result_to_bytes(item, watch),
                value,
                "scenario result",
            )
            identifier = scenario_result_id_for(value, watch)
            target = self.result_path(value.run_id, value.scenario)
            self._prepare_scenario(value.run_id, value.scenario)
            existed = self._write_immutable(target, data, "scenario result")
            loaded = self._load_result_unlocked(value.run_id, value.scenario)
            if loaded != value:
                raise ContainmentStoreError("stored scenario result differs after write")
            return ImmutableWrite(loaded, identifier, target, existed)

    def load_result(
        self,
        run_id: str,
        scenario: ContainmentScenario,
    ) -> ScenarioResult:
        with self._run_lock(run_id):
            return self._load_result_unlocked(run_id, scenario)

    def write_recovery(
        self,
        value: ScenarioRecovery,
    ) -> ImmutableWrite[ScenarioRecovery]:
        data = self._serialize(scenario_recovery_to_bytes, value, "scenario recovery")
        target = self.scenario_path(value.run_id, value.scenario) / "recovery.json"
        identifier = "containment-recovery-sha256:" + hashlib.sha256(data).hexdigest()
        with self._run_lock(value.run_id):
            self._prepare_scenario(value.run_id, value.scenario)
            existed = self._write_immutable(target, data, "scenario recovery")
            loaded = self._parse(
                scenario_recovery_from_bytes,
                self._read(target, "scenario recovery"),
                "scenario recovery",
            )
            if loaded != value:
                raise ContainmentStoreError("stored recovery differs after write")
            return ImmutableWrite(loaded, identifier, target, existed)

    def load_recovery(
        self,
        run_id: str,
        scenario: ContainmentScenario,
    ) -> ScenarioRecovery:
        with self._run_lock(run_id):
            return self._load_recovery_unlocked(run_id, scenario)

    def write_retry_authority(
        self,
        recovery: ScenarioRecovery,
        recovery_id: str,
        command_fingerprint: str,
    ) -> ImmutableWrite[dict[str, object]]:
        basis = _retry_basis(recovery, recovery_id, command_fingerprint)
        authority_id = "containment-retry-sha256:" + hashlib.sha256(
            _canonical(basis)
        ).hexdigest()
        document = {
            **basis,
            "authorityId": authority_id,
            "state": "Available",
            "consumedByRunId": None,
        }
        data = _canonical(document)
        target = self.retry_path(recovery.run_id, recovery.scenario)
        with self._run_lock(recovery.run_id):
            stored_recovery = self._load_recovery_unlocked(
                recovery.run_id, recovery.scenario
            )
            if stored_recovery != recovery or self.recovery_id_for(recovery) != recovery_id:
                raise ContainmentStoreError("retry authority recovery binding differs")
            self._prepare_scenario(recovery.run_id, recovery.scenario)
            existed = self._write_immutable(target, data, "retry authority")
            loaded = self._load_retry_authority_unlocked(
                recovery.run_id, recovery.scenario
            )
            return ImmutableWrite(loaded, authority_id, target, existed)

    def load_retry_authority(
        self,
        run_id: str,
        scenario: ContainmentScenario,
    ) -> dict[str, object]:
        with self._run_lock(run_id):
            return self._load_retry_authority_unlocked(run_id, scenario)

    def consume_retry_authority(
        self,
        recovery: ScenarioRecovery,
        recovery_id: str,
        command_fingerprint: str,
        new_run_id: str,
    ) -> dict[str, object]:
        self._run_hex(new_run_id)
        with self._run_lock(recovery.run_id):
            stored_recovery = self._load_recovery_unlocked(
                recovery.run_id, recovery.scenario
            )
            if stored_recovery != recovery or self.recovery_id_for(recovery) != recovery_id:
                raise ContainmentStoreError("retry authority recovery binding differs")
            current = self._load_retry_authority_unlocked(
                recovery.run_id, recovery.scenario
            )
            basis = _retry_basis(recovery, recovery_id, command_fingerprint)
            if any(current[name] != value for name, value in basis.items()):
                raise ContainmentStoreError("retry authority command/recovery binding differs")
            if current["state"] != "Available" or current["consumedByRunId"] is not None:
                raise ContainmentStoreError("retry authority is already consumed")
            marker = _retry_consumption_document(current, new_run_id)
            marker_target = self.retry_consumption_path(
                recovery.run_id, recovery.scenario
            )
            self._write_immutable(
                marker_target,
                _canonical(marker),
                "retry authority consumption",
            )
            loaded = self._load_retry_authority_unlocked(recovery.run_id, recovery.scenario)
            if (
                loaded["state"] != "Consumed"
                or loaded["consumedByRunId"] != new_run_id
            ):
                raise ContainmentStoreError(
                    "retry authority consumption marker readback did not prove "
                    "the requested consuming run"
                )
            return {
                "runId": recovery.run_id,
                "scenario": recovery.scenario.value,
                "recoveryId": recovery_id,
                "authorityId": str(loaded["authorityId"]),
                "commandFingerprint": command_fingerprint,
            }

    def write_decision(
        self,
        value: CapabilityDecision,
    ) -> ImmutableWrite[CapabilityDecision]:
        with self._run_lock(value.run_id):
            results, outcomes = self._resolve_decision_evidence_unlocked(value)
            data = self._decision_bytes(value, results, outcomes)
            target = self.decision_path(value.run_id)
            self._prepare_run(value.run_id)
            existed = self._write_immutable(target, data, "capability decision")
            loaded = self._decision_from_bytes(
                self._read(target, "capability decision"),
                results,
                outcomes,
            )
            if loaded != value:
                raise ContainmentStoreError("stored capability decision differs after write")
            return ImmutableWrite(
                loaded,
                "containment-decision-sha256:" + hashlib.sha256(data).hexdigest(),
                target,
                existed,
            )

    def load_decision(
        self,
        run_id: str,
        expected_id: str | None = None,
    ) -> CapabilityDecision:
        with self._run_lock(run_id):
            self._require_existing_direct_directory(
                self.evidence_run_path(run_id),
                "containment run",
            )
            raw = self._read(self.decision_path(run_id), "capability decision")
            probe = self._decision_probe(raw, run_id)
            results, outcomes = self._resolve_decision_evidence_unlocked(probe)
            value = self._decision_from_bytes(raw, results, outcomes)
            observed_id = (
                "containment-decision-sha256:" + hashlib.sha256(raw).hexdigest()
            )
            if expected_id is not None:
                if (
                    type(expected_id) is not str
                    or _DECISION_ID.fullmatch(expected_id) is None
                ):
                    raise ContainmentStoreError("expected decision ID is malformed")
                if observed_id != expected_id:
                    raise ContainmentStoreMalformedEvidence(
                        "capability decision content ID mismatch"
                    )
            return value

    def write_retirement(
        self,
        value: CapabilityRetirement,
    ) -> ImmutableWrite[CapabilityRetirement]:
        return self._write_authority_record(
            value,
            capability_retirement_to_bytes,
            capability_retirement_id_for,
            "retirements",
            "capability retirement",
            capability_retirement_from_bytes,
        )

    def load_retirement(self, identifier: str) -> CapabilityRetirement:
        return self._load_authority_record(
            identifier,
            "retirements",
            "capability retirement",
            capability_retirement_from_bytes,
            capability_retirement_id_for,
        )

    def list_retirement_ids(self) -> tuple[str, ...]:
        return self._list_authority_ids("retirements")

    def write_supersession(
        self,
        value: CapabilitySupersession,
    ) -> ImmutableWrite[CapabilitySupersession]:
        return self._write_authority_record(
            value,
            capability_supersession_to_bytes,
            capability_supersession_id_for,
            "supersessions",
            "capability supersession",
            capability_supersession_from_bytes,
        )

    def load_supersession(self, identifier: str) -> CapabilitySupersession:
        return self._load_authority_record(
            identifier,
            "supersessions",
            "capability supersession",
            capability_supersession_from_bytes,
            capability_supersession_id_for,
        )

    def list_supersession_ids(self) -> tuple[str, ...]:
        return self._list_authority_ids("supersessions")

    def write_review(
        self,
        value: CapabilityReview,
    ) -> ImmutableWrite[CapabilityReview]:
        return self._write_authority_record(
            value,
            capability_review_to_bytes,
            capability_review_id_for,
            "reviews",
            "capability review",
            capability_review_from_bytes,
        )

    def load_review(self, identifier: str) -> CapabilityReview:
        return self._load_authority_record(
            identifier,
            "reviews",
            "capability review",
            capability_review_from_bytes,
            capability_review_id_for,
        )

    def list_review_ids(self) -> tuple[str, ...]:
        return self._list_authority_ids("reviews")

    def write_eligibility(
        self,
        value: CapabilityEligibility,
    ) -> ImmutableWrite[CapabilityEligibility]:
        return self._write_authority_record(
            value,
            capability_eligibility_to_bytes,
            capability_eligibility_id_for,
            "eligibilities",
            "capability eligibility",
            capability_eligibility_from_bytes,
        )

    def load_eligibility(self, identifier: str) -> CapabilityEligibility:
        return self._load_authority_record(
            identifier,
            "eligibilities",
            "capability eligibility",
            capability_eligibility_from_bytes,
            capability_eligibility_id_for,
        )

    def list_eligibility_ids(self) -> tuple[str, ...]:
        return self._list_authority_ids("eligibilities")

    def list_run_ids(self) -> tuple[str, ...]:
        rows: list[str] = []
        if not self.evidence_root.exists():
            return ()
        with pin_trusted_paths((self.evidence_root,)):
            try:
                entries = tuple(os.scandir(self.evidence_root))
            except OSError as error:
                raise ContainmentStoreError(f"cannot enumerate containment runs: {error}") from error
            for entry in entries:
                if re.fullmatch(r"[0-9A-Fa-f]{32}", entry.name) is None:
                    continue
                if re.fullmatch(r"[0-9a-f]{32}", entry.name) is None:
                    raise ContainmentStoreError(
                        f"containment run entry name is noncanonical: {entry.name}"
                    )
                try:
                    metadata = entry.stat(follow_symlinks=False)
                except OSError as error:
                    raise ContainmentStoreError(
                        f"cannot prove containment run entry {entry.name}: {error}"
                    ) from error
                if stat.S_ISLNK(metadata.st_mode) or bool(
                    getattr(metadata, "st_file_attributes", 0)
                    & _FILE_ATTRIBUTE_REPARSE_POINT
                ):
                    raise ContainmentStoreError(
                        f"containment run entry is a reparse object: {entry.name}"
                    )
                if not stat.S_ISDIR(metadata.st_mode):
                    raise ContainmentStoreError(
                        "containment run entry is not a direct non-reparse directory: "
                        + entry.name
                    )
                with open_vault(Path(entry.path)):
                    pass
                rows.append("containment-run:" + entry.name)
            return tuple(sorted(rows))

    @contextmanager
    def command_lock(self, command_fingerprint: str):
        if (
            type(command_fingerprint) is not str
            or re.fullmatch(
                r"containment-command-sha256:[0-9a-f]{64}",
                command_fingerprint,
            )
            is None
        ):
            raise ContainmentStoreError("command fingerprint is malformed")
        identity = hashlib.sha256(
            (
                str(self.root).casefold()
                + "\0prepare\0"
                + command_fingerprint
            ).encode("utf-8")
        ).hexdigest()
        if os.name == "nt":
            with _windows_run_mutex(identity, blocking=True):
                yield
            return
        with _PROCESS_LOCKS_GUARD:
            lock = _PROCESS_LOCKS.setdefault(identity, threading.Lock())
        lock.acquire()
        try:
            yield
        finally:
            lock.release()

    def run_path(self, run_id: str) -> Path:
        return self.root / self._run_hex(run_id)

    def evidence_run_path(self, run_id: str) -> Path:
        return self.evidence_root / self._run_hex(run_id)

    def request_path(self, run_id: str) -> Path:
        return self.evidence_run_path(run_id) / "request.json"

    def intent_path(self, run_id: str) -> Path:
        return self.evidence_run_path(run_id) / "intent.json"

    def scenario_path(self, run_id: str, scenario: ContainmentScenario) -> Path:
        if not isinstance(scenario, ContainmentScenario):
            raise ContainmentStoreError("scenario must be ContainmentScenario")
        return self.evidence_run_path(run_id) / "scenarios" / scenario.value

    def journal_path(self, run_id: str, scenario: ContainmentScenario) -> Path:
        return self.scenario_path(run_id, scenario) / "journal.json"

    def watch_path(self, run_id: str, scenario: ContainmentScenario) -> Path:
        return self.scenario_path(run_id, scenario) / "watch"

    def result_path(self, run_id: str, scenario: ContainmentScenario) -> Path:
        return self.scenario_path(run_id, scenario) / "result.json"

    def retry_path(self, run_id: str, scenario: ContainmentScenario) -> Path:
        return self.scenario_path(run_id, scenario) / "retry.json"

    def retry_consumption_path(
        self,
        run_id: str,
        scenario: ContainmentScenario,
    ) -> Path:
        return self.scenario_path(run_id, scenario) / "retry-consumption.json"

    def launch_path(self, run_id: str, scenario: ContainmentScenario) -> Path:
        return self.scenario_path(run_id, scenario) / "launch.json"

    def quarantine_path(self, run_id: str) -> Path:
        return self.run_path(run_id) / "quarantine"

    def decision_path(self, run_id: str) -> Path:
        return self.evidence_run_path(run_id) / "decision.json"

    def authority_path(self) -> Path:
        return self.evidence_root / "authority"

    def retirement_path(self, identifier: str) -> Path:
        return self._authority_record_path(identifier, "retirements")

    def supersession_path(self, identifier: str) -> Path:
        return self._authority_record_path(identifier, "supersessions")

    def review_path(self, identifier: str) -> Path:
        return self._authority_record_path(identifier, "reviews")

    def eligibility_path(self, identifier: str) -> Path:
        return self._authority_record_path(identifier, "eligibilities")

    def journal_id_for(self, journal: ScenarioJournal) -> str:
        data = self._serialize(scenario_journal_to_bytes, journal, "journal")
        return "containment-journal-sha256:" + hashlib.sha256(data).hexdigest()

    def recovery_id_for(self, recovery: ScenarioRecovery) -> str:
        data = self._serialize(scenario_recovery_to_bytes, recovery, "scenario recovery")
        return "containment-recovery-sha256:" + hashlib.sha256(data).hexdigest()

    def _prepare_run(self, run_id: str) -> None:
        authority = self.evidence_run_path(run_id)
        if not os.path.lexists(authority) and os.path.lexists(self.run_path(run_id)):
            raise ContainmentStoreError("historical disposable run cannot acquire new authority")
        self._prepare_vault(authority)
        self._ensure_direct_directory(self.run_path(run_id))
        self._ensure_direct_directory(self.evidence_run_path(run_id) / "scenarios")
        self._ensure_direct_directory(self.quarantine_path(run_id))

    def _prepare_scenario(self, run_id: str, scenario: ContainmentScenario) -> None:
        self._prepare_run(run_id)
        self._ensure_direct_directory(self.scenario_path(run_id, scenario))
        self._ensure_direct_directory(self.watch_path(run_id, scenario))

    def _load_journal_unlocked(
        self,
        run_id: str,
        scenario: ContainmentScenario,
    ) -> ScenarioJournal:
        data = self._read(self.journal_path(run_id, scenario), "journal")
        value = self._parse(scenario_journal_from_bytes, data, "journal")
        if value.run_id != run_id or value.scenario is not scenario:
            raise ContainmentStoreError("stored journal has the wrong run/scenario binding")
        if scenario_journal_to_bytes(value) != data:
            raise ContainmentStoreError("stored journal bytes are not canonical")
        return value

    def _load_request_unlocked(self, run_id: str) -> dict[str, object]:
        data = self._read(self.request_path(run_id), "request")
        try:
            value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_json)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ContainmentStoreError(f"request is malformed: {error}") from error
        if type(value) is not dict or value.get("runId") != run_id:
            raise ContainmentStoreError("request has the wrong runId binding")
        if _canonical(value) != data:
            raise ContainmentStoreError("request bytes are not canonical")
        return value

    def _load_intent_unlocked(self, run_id: str) -> dict[str, object]:
        data = self._read(self.intent_path(run_id), "run intent")
        try:
            document = json.loads(
                data.decode("utf-8"), object_pairs_hook=_unique_json
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ContainmentStoreError(f"run intent is malformed: {error}") from error
        checked = _intent_document(document, run_id)
        if _canonical(checked) != data:
            raise ContainmentStoreError("stored run intent bytes are not canonical")
        return checked

    def _load_launch_evidence_unlocked(
        self,
        run_id: str,
        scenario: ContainmentScenario,
    ) -> dict[str, object]:
        data = self._read(self.launch_path(run_id, scenario), "launch evidence")
        try:
            document = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_json)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ContainmentStoreError(f"launch evidence is malformed: {error}") from error
        checked = _launch_document(document, run_id, scenario)
        if _canonical(checked) != data:
            raise ContainmentStoreError("stored launch evidence bytes are not canonical")
        return checked

    def _load_protected_state_unlocked(
        self,
        run_id: str,
        scenario: ContainmentScenario,
        label: str,
    ) -> ProtectedState:
        if label not in PROTECTED_STATE_LABELS:
            raise ContainmentStoreError("protected state label must be before or after")
        data = self._read(
            self.scenario_path(run_id, scenario) / f"{label}.json",
            f"{label} protected state",
        )
        try:
            document = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_json)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ContainmentStoreError(f"{label} protected state is malformed: {error}") from error
        value = _protected_from_document(document)
        if _canonical(_protected_document(value)) != data:
            raise ContainmentStoreError(f"stored {label} protected state bytes are not canonical")
        return value

    def _load_watch_outcome_unlocked(
        self,
        run_id: str,
        scenario: ContainmentScenario,
        expected_id: str | None,
    ) -> WatchOutcome:
        target = self.watch_path(run_id, scenario) / "outcome.json"
        data = self._read(target, "watch outcome")
        value = self._parse(watch_outcome_from_bytes, data, "watch outcome")
        observed_id = watch_outcome_id_for(value)
        if value.run_id != run_id or value.scenario is not scenario:
            raise ContainmentStoreMalformedEvidence(
                "watch outcome has the wrong run/scenario binding"
            )
        if expected_id is not None and observed_id != expected_id:
            raise ContainmentStoreError("watch outcome content ID mismatch")
        if watch_outcome_to_bytes(value) != data:
            raise ContainmentStoreMalformedEvidence(
                "watch outcome bytes are not canonical"
            )
        self._validate_watch_raw(run_id, scenario, value)
        return value

    def _validate_watch_raw(self, run_id, scenario, value):
        from .windows_watch import watch_receipt_from_files
        from .windows_watch_protocol import watch_request_from_bytes, watch_request_to_bytes, WatchProtocolError
        root = self.watch_path(run_id, scenario)
        with self._evidence_guard(root):
            raw = self._read(root / "request.json", "watch request")
            try:
                request = watch_request_from_bytes(raw)
            except WatchProtocolError as error:
                raise ContainmentStoreMalformedEvidence(f"invalid protected watch request: {error}") from error
            if raw != watch_request_to_bytes(request) or request.evidence_root != root or request.authority_root != self.evidence_run_path(run_id):
                raise ContainmentStoreMalformedEvidence("watch request authority binding mismatch")
            receipt = watch_receipt_from_files(request, value.worker_pid,
                root / "ready.json", root / "events.ndjson", root / "terminal.json")
            if receipt.watch_outcome_id != watch_outcome_id_for(value):
                raise ContainmentStoreMalformedEvidence("raw protected watcher reconstruction failed: " + str(receipt.error))

    def evaluation_path(self, run_id, scenario):
        return self.scenario_path(run_id, scenario) / "evaluation.json"

    def started_path(self, run_id, scenario):
        return self.scenario_path(run_id, scenario) / "started.json"

    def write_scenario_started(self, journal):
        if journal.state is not ScenarioState.SCENARIO_STARTED:
            raise ContainmentStoreError("immutable start requires ScenarioStarted")
        data = scenario_journal_to_bytes(journal)
        self._write_immutable(self.started_path(journal.run_id, journal.scenario), data, "ScenarioStarted")

    def write_evaluation(self, value: ScenarioEvidence):
        """Persist original independent inputs, before constructing the result."""
        if not isinstance(value, ScenarioEvidence):
            raise ContainmentStoreError("evaluation requires original ScenarioEvidence")
        run_id, scenario = value.run_id, value.scenario
        with self._run_lock(run_id):
            self._prepare_scenario(run_id, scenario)
            self.write_protected_state(run_id, scenario, "before", value.protected_before)
            self.write_protected_state(run_id, scenario, "after", value.protected_after)
            bound = {"protected_before", "protected_after", "watch_outcome", "mo2_process", "run_id", "scenario", "scenario_started"}
            observations = {field.name: getattr(value, field.name) for field in fields(value) if field.name not in bound}
            for name in ("source_integrity", "stage_integrity", "adopted_integrity"):
                if observations[name] is not None:
                    observations[name] = observations[name].value
            if value.adopted_tree is not None:
                observations["adopted_tree"] = _tree_document(value.adopted_tree)
            document = {"schemaVersion": 1, "runId": run_id, "scenario": scenario.value,
                "watchOutcomeId": watch_outcome_id_for(value.watch_outcome), "observations": observations,
                "beforeSha256": hashlib.sha256(self._read(self.scenario_path(run_id, scenario)/"before.json", "before")).hexdigest(),
                "afterSha256": hashlib.sha256(self._read(self.scenario_path(run_id, scenario)/"after.json", "after")).hexdigest(),
                "startedSha256": None, "launchSha256": None}
            for key, path in (("startedSha256", self.started_path(run_id, scenario)), ("launchSha256", self.launch_path(run_id, scenario))):
                if path.exists():
                    document[key] = hashlib.sha256(self._read(path, key)).hexdigest()
            self._write_immutable(self.evaluation_path(run_id, scenario), _canonical(document), "evaluation inputs")
            reconstructed = self._load_evaluation_unlocked(run_id, scenario, value.watch_outcome)
            if reconstructed != value:
                raise ContainmentStoreError("evaluation inputs differ from protected independent bindings")
            return reconstructed

    def _load_evaluation_unlocked(self, run_id, scenario, watch):
        raw = self._read(self.evaluation_path(run_id, scenario), "evaluation inputs")
        try:
            document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json)
            if _canonical(document) != raw or set(document) != {"schemaVersion", "runId", "scenario", "watchOutcomeId", "observations", "beforeSha256", "afterSha256", "startedSha256", "launchSha256"}:
                raise ValueError("noncanonical evaluation fields")
            if document["schemaVersion"] != 1 or document["runId"] != run_id or document["scenario"] != scenario.value or document["watchOutcomeId"] != watch_outcome_id_for(watch):
                raise ValueError("evaluation binding differs")
            for label in ("before", "after"):
                data = self._read(self.scenario_path(run_id, scenario)/(label+".json"), label)
                if hashlib.sha256(data).hexdigest() != document[label+"Sha256"]:
                    raise ValueError(label + " protected observation changed")
            journal = self._load_journal_unlocked(run_id, scenario)
            before = self._load_protected_state_unlocked(run_id, scenario, "before")
            after = self._load_protected_state_unlocked(run_id, scenario, "after")
            if journal.protected_before != before:
                raise ValueError("journal baseline binding differs")
            started = False
            process = None
            if document["startedSha256"] is not None:
                data = self._read(self.started_path(run_id, scenario), "ScenarioStarted")
                start = scenario_journal_from_bytes(data)
                if hashlib.sha256(data).hexdigest() != document["startedSha256"] or start.run_id != run_id or start.scenario is not scenario or start.state is not ScenarioState.SCENARIO_STARTED or start.protected_before != before:
                    raise ValueError("ScenarioStarted binding differs")
                started = True
            elif self.started_path(run_id, scenario).exists() or journal.mo2_pid is not None or journal.state in {ScenarioState.SCENARIO_STARTED, ScenarioState.LAUNCHED, ScenarioState.CAPTURED}:
                raise ValueError("missing immutable ScenarioStarted")
            if document["launchSha256"] is not None:
                data = self._read(self.launch_path(run_id, scenario), "launch")
                if hashlib.sha256(data).hexdigest() != document["launchSha256"]:
                    raise ValueError("launch bytes changed")
                try:
                    launch = self._load_launch_evidence_unlocked(run_id, scenario)
                except ContainmentStoreError:
                    launch = None
                # Damaged original launch evidence proves no process identity.
                # The shared classifier may still preserve a proven breach;
                # it can never derive Passed with this missing process input.
                if launch is not None and started and launch["pid"] == journal.mo2_pid:
                    process = ProcessEvidence(launch["pid"], launch["executable"], launch["executableVersion"], tuple(launch["arguments"]), launch["workingDirectory"], IntegrityObservation(launch["integrity"]))
            elif self.launch_path(run_id, scenario).exists():
                raise ValueError("evaluation omitted protected launch")
            observations = dict(document["observations"])
            for name in ("source_integrity", "stage_integrity", "adopted_integrity"):
                if observations[name] is not None:
                    observations[name] = IntegrityObservation(observations[name])
            if observations["adopted_tree"] is not None:
                observations["adopted_tree"] = _tree_from_document(observations["adopted_tree"], "adopted tree")
            for name in ("production_backup_names", "staging_new_names", "staging_output_names", "safety_reasons", "incomplete_reasons"):
                if type(observations[name]) is not list or any(type(item) is not str for item in observations[name]):
                    raise ValueError("invalid observation list")
                observations[name] = tuple(observations[name])
            for name in ("fresh_retry_eligible", "projection_targets_verified", "projection_observation_complete", "production_observation_complete", "staging_observation_complete", "output_observation_complete", "source_restored_after_quarantine"):
                if type(observations[name]) is not bool:
                    raise ValueError("invalid boolean observation")
            for name in ("projection_count", "projection_payload_bytes_copied"):
                if type(observations[name]) is not int or observations[name] < 0:
                    raise ValueError("invalid integer observation")
            return ScenarioEvidence(run_id=run_id, scenario=scenario, protected_before=before, protected_after=after,
                watch_outcome=watch, mo2_process=process, scenario_started=started, **observations)
        except (ValueError, TypeError, KeyError) as error:
            raise ContainmentStoreMalformedEvidence(f"invalid protected evaluation inputs: {error}") from error

    def _validate_result_derivation(self, run_id, scenario, watch, value):
        inputs = self._load_evaluation_unlocked(run_id, scenario, watch)
        if evaluate_scenario(inputs) != value:
            raise ContainmentStoreMalformedEvidence("scenario result differs from independent protected evaluation")

    def _load_result_unlocked(
        self,
        run_id: str,
        scenario: ContainmentScenario,
    ) -> ScenarioResult:
        watch = self._load_watch_outcome_unlocked(run_id, scenario, None)
        data = self._read(self.result_path(run_id, scenario), "scenario result")
        value = self._parse(
            lambda raw: scenario_result_from_bytes(raw, watch),
            data,
            "scenario result",
        )
        if value.run_id != run_id or value.scenario is not scenario:
            raise ContainmentStoreMalformedEvidence(
                "scenario result has the wrong run/scenario binding"
            )
        if scenario_result_to_bytes(value, watch) != data:
            raise ContainmentStoreMalformedEvidence(
                "scenario result bytes are not canonical"
            )
        self._validate_result_derivation(run_id, scenario, watch, value)
        return value

    def _load_recovery_unlocked(
        self,
        run_id: str,
        scenario: ContainmentScenario,
    ) -> ScenarioRecovery:
        data = self._read(
            self.scenario_path(run_id, scenario) / "recovery.json",
            "scenario recovery",
        )
        value = self._parse(scenario_recovery_from_bytes, data, "scenario recovery")
        if value.run_id != run_id or value.scenario is not scenario:
            raise ContainmentStoreError("stored recovery has wrong run/scenario binding")
        if scenario_recovery_to_bytes(value) != data:
            raise ContainmentStoreError("stored recovery bytes are not canonical")
        return value

    def _load_retry_authority_unlocked(
        self,
        run_id: str,
        scenario: ContainmentScenario,
    ) -> dict[str, object]:
        data = self._read(self.retry_path(run_id, scenario), "retry authority")
        try:
            document = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_json)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ContainmentStoreError(f"retry authority is malformed: {error}") from error
        checked = _retry_document(document, run_id, scenario)
        if _canonical(checked) != data:
            raise ContainmentStoreError("stored retry authority bytes are not canonical")
        try:
            consumed_data = self._read(
                self.retry_consumption_path(run_id, scenario),
                "retry authority consumption",
            )
        except ContainmentStoreNotFound:
            return checked
        try:
            consumed = json.loads(
                consumed_data.decode("utf-8"), object_pairs_hook=_unique_json
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ContainmentStoreError(
                f"retry authority consumption is malformed: {error}"
            ) from error
        consumed = _retry_consumption_value(consumed, checked)
        if _canonical(consumed) != consumed_data:
            raise ContainmentStoreError("stored retry authority consumption bytes are not canonical")
        return {
            **checked,
            "state": "Consumed",
            "consumedByRunId": consumed["consumedByRunId"],
        }

    def _write_authority_record(
        self,
        value: _T,
        encoder: Callable[[_T], bytes],
        identifier_for: Callable[[_T], str],
        kind: str,
        label: str,
        decoder: Callable[[bytes], _T],
    ) -> ImmutableWrite[_T]:
        data = self._serialize(encoder, value, label)
        try:
            identifier = identifier_for(value)
        except ContainmentFormatError as error:
            raise ContainmentStoreError(f"{label} is invalid: {error}") from error
        target = self._authority_record_path(identifier, kind)
        self._prepare_vault(self.authority_path())
        self._ensure_direct_directory(target.parent)
        self._validate_authority_layout()
        existed = self._write_immutable(target, data, label)
        loaded = self._load_authority_record(
            identifier,
            kind,
            label,
            decoder,
            identifier_for,
        )
        if loaded != value:
            raise ContainmentStoreError(f"stored {label} differs after write")
        return ImmutableWrite(loaded, identifier, target, existed)

    def _load_authority_record(
        self,
        identifier: str,
        kind: str,
        label: str,
        decoder: Callable[[bytes], _T],
        identifier_for: Callable[[_T], str],
    ) -> _T:
        target = self._authority_record_path(identifier, kind)
        self._validate_authority_layout()
        data = self._read(target, label)
        value = self._parse(decoder, data, label)
        try:
            observed_id = identifier_for(value)
        except ContainmentFormatError as error:
            raise ContainmentStoreMalformedEvidence(
                f"stored {label} is malformed: {error}"
            ) from error
        if observed_id != identifier:
            raise ContainmentStoreMalformedEvidence(
                f"stored {label} content ID mismatch"
            )
        return value

    def _list_authority_ids(self, kind: str) -> tuple[str, ...]:
        self._authority_prefix(kind)
        if not self._validate_authority_layout():
            return ()
        directory = self.authority_path() / kind
        try:
            metadata = directory.lstat()
        except FileNotFoundError:
            return ()
        except OSError as error:
            raise ContainmentStoreError(
                f"cannot inspect capability authority {kind}: {error}"
            ) from error
        self._require_direct_authority_directory(metadata, kind)
        try:
            entries = tuple(os.scandir(directory))
        except OSError as error:
            raise ContainmentStoreError(
                f"cannot enumerate capability authority {kind}: {error}"
            ) from error
        prefix = self._authority_prefix(kind)
        identifiers: list[str] = []
        for entry in entries:
            match = re.fullmatch(r"([0-9a-f]{64})\.json", entry.name)
            if match is None:
                raise ContainmentStoreError(
                    f"capability authority {kind} entry is noncanonical: {entry.name}"
                )
            try:
                entry_metadata = entry.stat(follow_symlinks=False)
            except OSError as error:
                raise ContainmentStoreError(
                    f"cannot prove capability authority {kind} entry {entry.name}: {error}"
                ) from error
            if stat.S_ISLNK(entry_metadata.st_mode) or bool(
                getattr(entry_metadata, "st_file_attributes", 0)
                & _FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise ContainmentStoreError(
                    f"capability authority {kind} entry is a reparse object: {entry.name}"
                )
            if not stat.S_ISREG(entry_metadata.st_mode):
                raise ContainmentStoreError(
                    f"capability authority {kind} entry is not a direct regular file: {entry.name}"
                )
            identifiers.append(prefix + match.group(1))
        return tuple(sorted(identifiers))

    def _validate_authority_layout(self) -> bool:
        authority = self.authority_path()
        try:
            metadata = authority.lstat()
        except FileNotFoundError:
            return False
        except OSError as error:
            raise ContainmentStoreError(
                f"cannot inspect capability authority root: {error}"
            ) from error
        self._require_direct_authority_directory(metadata, "root")
        try:
            entries = tuple(os.scandir(authority))
        except OSError as error:
            raise ContainmentStoreError(
                f"cannot enumerate capability authority root: {error}"
            ) from error
        for entry in entries:
            if entry.name not in _AUTHORITY_KINDS:
                raise ContainmentStoreError(
                    "capability authority root entry is noncanonical: " + entry.name
                )
            try:
                entry_metadata = entry.stat(follow_symlinks=False)
            except OSError as error:
                raise ContainmentStoreError(
                    f"cannot prove capability authority directory {entry.name}: {error}"
                ) from error
            self._require_direct_authority_directory(entry_metadata, entry.name)
        return True

    @staticmethod
    def _require_direct_authority_directory(metadata: os.stat_result, label: str) -> None:
        if stat.S_ISLNK(metadata.st_mode) or bool(
            getattr(metadata, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ContainmentStoreError(
                f"capability authority {label} directory is redirected or reparse"
            )
        if not stat.S_ISDIR(metadata.st_mode):
            raise ContainmentStoreError(
                f"capability authority {label} is not a direct directory"
            )

    def _authority_record_path(self, identifier: str, kind: str) -> Path:
        prefix = self._authority_prefix(kind)
        if type(identifier) is not str:
            raise ContainmentStoreError(f"capability authority {kind} ID must be text")
        match = re.fullmatch(re.escape(prefix) + r"([0-9a-f]{64})", identifier)
        if match is None:
            raise ContainmentStoreError(f"capability authority {kind} ID is malformed")
        return self.authority_path() / kind / (match.group(1) + ".json")

    @staticmethod
    def _authority_prefix(kind: str) -> str:
        try:
            return _AUTHORITY_KINDS[kind]
        except KeyError as error:
            raise ContainmentStoreError(
                f"unknown capability authority record kind: {kind}"
            ) from error

    def _resolve_decision_evidence_unlocked(
        self,
        decision: CapabilityDecision,
    ) -> tuple[tuple[ScenarioResult, ...], tuple[WatchOutcome, ...]]:
        if not decision.scenario_result_ids:
            return (), ()
        self._require_existing_direct_directory(
            self.evidence_run_path(decision.run_id) / "scenarios",
            "scenario collection",
        )
        wanted = set(decision.scenario_result_ids)
        found: dict[str, tuple[ScenarioResult, WatchOutcome]] = {}
        for scenario in ContainmentScenario:
            if not self._require_existing_direct_directory(
                self.scenario_path(decision.run_id, scenario),
                f"{scenario.value} scenario",
                required=False,
            ):
                continue
            if not self._require_existing_direct_directory(
                self.watch_path(decision.run_id, scenario),
                f"{scenario.value} watch evidence",
                required=False,
            ):
                continue
            try:
                result = self._load_result_unlocked(decision.run_id, scenario)
            except ContainmentStoreNotFound:
                continue
            watch = self._load_watch_outcome_unlocked(
                decision.run_id,
                scenario,
                result.watch_outcome_id,
            )
            identifier = scenario_result_id_for(result, watch)
            if identifier in wanted:
                found[identifier] = (result, watch)
        if set(found) != wanted:
            raise ContainmentStoreError("decision references unavailable scenario result IDs")
        ordered = tuple(
            found[identifier]
            for identifier in decision.scenario_result_ids
        )
        return tuple(item[0] for item in ordered), tuple(item[1] for item in ordered)

    def _decision_bytes(
        self,
        value: CapabilityDecision,
        results: tuple[ScenarioResult, ...],
        outcomes: tuple[WatchOutcome, ...],
    ) -> bytes:
        try:
            if value.scenario_result_ids:
                return capability_decision_to_bytes(value, results, outcomes)
            return capability_decision_to_bytes(value)
        except ContainmentFormatError as error:
            raise ContainmentStoreMalformedEvidence(
                f"capability decision is malformed: {error}"
            ) from error

    def _decision_from_bytes(
        self,
        data: bytes,
        results: tuple[ScenarioResult, ...],
        outcomes: tuple[WatchOutcome, ...],
    ) -> CapabilityDecision:
        try:
            if results or outcomes:
                return capability_decision_from_bytes(data, results, outcomes)
            return capability_decision_from_bytes(data)
        except ContainmentFormatError as error:
            raise ContainmentStoreMalformedEvidence(
                f"capability decision is malformed: {error}"
            ) from error

    @staticmethod
    def _decision_probe(data: bytes, run_id: str) -> CapabilityDecision:
        try:
            document = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_json)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ContainmentStoreMalformedEvidence(
                f"capability decision is malformed: {error}"
            ) from error
        fields = {
            "schemaVersion",
            "runId",
            "mechanism",
            "verdict",
            "scenarioResultIds",
            "reasons",
        }
        if type(document) is dict and "bindings" in document:
            try:
                decision = _capability_decision_probe_from_bytes(data)
            except ContainmentFormatError as error:
                raise ContainmentStoreMalformedEvidence(
                    f"capability decision is malformed: {error}"
                ) from error
            if _canonical(document) != data or decision.run_id != run_id:
                raise ContainmentStoreMalformedEvidence(
                    "capability decision binding is malformed"
                )
            return decision
        if type(document) is not dict or set(document) != fields or _canonical(document) != data:
            raise ContainmentStoreMalformedEvidence(
                "capability decision bytes are not canonical"
            )
        identifiers = document["scenarioResultIds"]
        reasons = document["reasons"]
        if (
            document["runId"] != run_id
            or type(identifiers) is not list
            or any(type(item) is not str for item in identifiers)
            or type(reasons) is not list
            or any(type(item) is not str for item in reasons)
        ):
            raise ContainmentStoreMalformedEvidence(
                "capability decision binding is malformed"
            )
        try:
            verdict = CapabilityVerdict(document["verdict"])
        except (TypeError, ValueError) as error:
            raise ContainmentStoreMalformedEvidence(
                "capability decision verdict is malformed"
            ) from error
        return CapabilityDecision(
            document["schemaVersion"],
            run_id,
            document["mechanism"],
            verdict,
            tuple(identifiers),
            tuple(reasons),
        )

    @contextmanager
    def _run_lock(self, run_id: str):
        run_hex = self._run_hex(run_id)
        identity = hashlib.sha256(
            (str(self.root).casefold() + "\0" + run_hex).encode("utf-8")
        ).hexdigest()
        if os.name == "nt":
            with _windows_run_mutex(identity):
                yield
            return
        with _PROCESS_LOCKS_GUARD:
            lock = _PROCESS_LOCKS.setdefault(identity, threading.Lock())
        if not lock.acquire(blocking=False):
            raise ContainmentStoreError("containment run is already being changed")
        try:
            yield
        finally:
            lock.release()

    @staticmethod
    def _run_hex(run_id: str) -> str:
        if type(run_id) is not str:
            raise ContainmentStoreError("run ID must be text")
        match = _RUN.fullmatch(run_id)
        if match is None:
            raise ContainmentStoreError("run ID is malformed")
        return match.group(1)

    def _effect_target_observation(
        self,
        target: Path,
        label: str,
    ) -> tuple[tuple[int, ...], bytes] | None:
        try:
            before = target.lstat()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise ContainmentStoreError(
                f"direct-write effect observation is unavailable for {label}: {error}"
            ) from error
        if not stat.S_ISREG(before.st_mode):
            raise ContainmentStoreError(
                f"direct-write effect observation requires a regular {label}"
            )
        self._reject_redirect(target)
        try:
            data = target.read_bytes()
            after = target.lstat()
        except OSError as error:
            raise ContainmentStoreError(
                f"direct-write effect observation is unavailable for {label}: {error}"
            ) from error
        identity_before = (
            int(before.st_mode),
            int(before.st_dev),
            int(before.st_ino),
            int(before.st_size),
            int(before.st_mtime_ns),
            int(before.st_ctime_ns),
            int(getattr(before, "st_file_attributes", 0)),
        )
        identity_after = (
            int(after.st_mode),
            int(after.st_dev),
            int(after.st_ino),
            int(after.st_size),
            int(after.st_mtime_ns),
            int(after.st_ctime_ns),
            int(getattr(after, "st_file_attributes", 0)),
        )
        if identity_before != identity_after:
            raise ContainmentStoreError(
                f"direct-write effect observation changed while reading {label}"
            )
        return identity_after, data

    def _observe_failed_immutable_write(
        self,
        target: Path,
        data: bytes,
        label: str,
    ) -> tuple[bool, str | None]:
        try:
            observed = self._effect_target_observation(target, label)
        except ContainmentStoreError as error:
            return False, str(error)
        if observed is None:
            return False, None
        self._record_written_path(target)
        if observed[1] != data:
            return True, (
                f"direct-write effect observation found unexpected bytes for {label}"
            )
        return True, None

    def _observe_failed_replacement(
        self,
        target: Path,
        before: tuple[tuple[int, ...], bytes],
        data: bytes,
        label: str,
    ) -> tuple[bool, str | None]:
        try:
            observed = self._effect_target_observation(target, label)
        except ContainmentStoreError as error:
            return False, str(error)
        if observed is None:
            self._record_written_path(target)
            return True, f"direct-write effect observation found missing {label}"
        if observed == before:
            return False, None
        self._record_written_path(target)
        if observed[1] != data:
            return True, (
                f"direct-write effect observation found unexpected bytes for {label}"
            )
        return True, None

    def _observe_retained_candidates(
        self,
        error: ExactObjectOwnershipError,
        target: Path,
        label: str,
    ) -> str | None:
        details: list[str] = []
        for candidate in error.candidates:
            path = Path(candidate.path)
            if path == target:
                continue
            try:
                observed = self._effect_target_observation(
                    path,
                    f"retained {label} candidate",
                )
            except ContainmentStoreError as observation_error:
                details.append(str(observation_error))
                continue
            if observed is not None:
                self._record_written_path(path)
        return "; ".join(details) or None

    def _cleanup_staging_part(self, part: Path, label: str) -> None:
        try:
            part.unlink()
        except FileNotFoundError:
            return
        except OSError as cleanup_error:
            try:
                observed = self._effect_target_observation(
                    part,
                    f"{label} staging part",
                )
            except ContainmentStoreError as observation_error:
                raise ContainmentStoreError(
                    f"{label} staging cleanup failed and surviving effect "
                    f"observation is unavailable: {observation_error}"
                ) from cleanup_error
            if observed is not None:
                self._record_written_path(part)

    @staticmethod
    def _effect_observation_suffix(detail: str | None) -> str:
        return "" if detail is None else f"; {detail}"

    def _write_immutable(self, target: Path, data: bytes, label: str, *, require_new=False):
        with self._evidence_guard(target.parent):
            value = self._write_immutable_guarded(target, data, label, require_new=require_new)
            with self._evidence_guard(target):
                pass
            return value

    def _write_immutable_guarded(self, target: Path, data: bytes, label: str, *, require_new=False) -> bool:
        if target.exists():
            if require_new:
                raise ContainmentStoreError(f"stored {label} already exists; fresh publication required")
            existing = self._read(target, label)
            if existing != data:
                raise ContainmentStoreError(f"stored {label} path contains different bytes")
            return True
        if os.name == "nt":
            try:
                def validate(candidate: bytes) -> bytes:
                    if candidate != data:
                        raise ContainmentStoreError(
                            f"stored {label} differs during exact immutable publication"
                        )
                    return candidate

                publish_new_pinned(target, data, validate)
                self._record_written_path(target)
                return False
            except FileExistsError:
                if require_new:
                    raise ContainmentStoreError(f"stored {label} collided during fresh publication")
                existing = self._read(target, label)
                if existing != data:
                    raise ContainmentStoreError(
                        f"stored {label} path contains different bytes"
                    )
                return True
            except ExactObjectOwnershipError as error:
                _observed, effect_detail = self._observe_failed_immutable_write(
                    target,
                    data,
                    label,
                )
                candidate_detail = self._observe_retained_candidates(
                    error,
                    target,
                    label,
                )
                effect_detail = "; ".join(
                    detail
                    for detail in (effect_detail, candidate_detail)
                    if detail is not None
                ) or None
                try:
                    resolve_retained_ownership(error)
                except ExactObjectOwnershipError as unresolved:
                    error = unresolved
                else:
                    raise ContainmentStoreError(
                        "exact immutable "
                        f"{label} cleanup completed after publication failure"
                        f"{self._effect_observation_suffix(effect_detail)}"
                    ) from error
                resolved = ContainmentStoreOwnershipError(
                    f"cannot exactly create immutable {label}; live retained ownership "
                    f"requires resolution: {error}"
                    f"{self._effect_observation_suffix(effect_detail)}",
                    error,
                )
                raise resolved from error
            except ExactObjectError as error:
                _observed, effect_detail = self._observe_failed_immutable_write(
                    target,
                    data,
                    label,
                )
                raise ContainmentStoreError(
                    f"cannot exactly create immutable {label}: {error}"
                    f"{self._effect_observation_suffix(effect_detail)}"
                ) from error
            except OSError as error:
                _observed, effect_detail = self._observe_failed_immutable_write(
                    target,
                    data,
                    label,
                )
                raise ContainmentStoreError(
                    f"cannot atomically create {label} through retained ownership: {error}"
                    f"{self._effect_observation_suffix(effect_detail)}"
                ) from error
        part = mutable_replacement_part_path(target, uuid.uuid4().hex)
        promoted = False
        try:
            with part.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            self._reject_redirect(part)
            _promote_no_replace_posix(part, target)
            promoted = True
            self._record_written_path(target)
            if self._read(target, label) != data:
                raise ContainmentStoreError(f"stored {label} differs after atomic promotion")
            return False
        except FileExistsError:
            if require_new:
                raise ContainmentStoreError(f"stored {label} collided during fresh publication")
            existing = self._read(target, label)
            if existing != data:
                raise ContainmentStoreError(f"stored {label} path contains different bytes")
            return True
        except ContainmentStoreError:
            raise
        except OSError as error:
            observed, effect_detail = self._observe_failed_immutable_write(
                target,
                data,
                label,
            )
            phase = "after promotion" if promoted or observed else "before promotion"
            raise ContainmentStoreError(
                f"cannot atomically create {label} {phase}: {error}"
                f"{self._effect_observation_suffix(effect_detail)}"
            ) from error
        finally:
            self._cleanup_staging_part(part, label)

    def _atomic_replace(self, target: Path, data: bytes, expected: bytes, label: str):
        with self._evidence_guard(target.parent):
            value = self._atomic_replace_guarded(target, data, expected, label)
            with self._evidence_guard(target):
                pass
            return value

    def _atomic_replace_guarded(
        self,
        target: Path,
        data: bytes,
        expected: bytes,
        label: str,
    ) -> None:
        if self._read(target, label) != expected:
            raise ContainmentStoreError(f"{label} changed before atomic replacement")
        before = self._effect_target_observation(target, label)
        if before is None or before[1] != expected:
            raise ContainmentStoreError(f"{label} changed before atomic replacement")
        part = mutable_replacement_part_path(target, uuid.uuid4().hex)
        try:
            with part.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            self._reject_redirect(part)
            if self._read(target, label) != expected:
                raise ContainmentStoreError(f"{label} changed before atomic replacement")
            _replace_durable(part, target)
            self._record_written_path(target)
            if self._read(target, label) != data:
                raise ContainmentStoreError(f"{label} differs after atomic replacement")
        except ContainmentStoreError:
            raise
        except OSError as error:
            _observed, effect_detail = self._observe_failed_replacement(
                target,
                before,
                data,
                label,
            )
            raise ContainmentStoreError(
                f"cannot atomically replace {label}: {error}"
                f"{self._effect_observation_suffix(effect_detail)}"
            ) from error
        finally:
            self._cleanup_staging_part(part, label)

    def _record_written_path(self, path: Path) -> None:
        if self._effect_recorder is not None:
            self._effect_recorder(path)

    @contextmanager
    def _stable_pin(self, path: Path, kind: str):
        pin = pin_stable_direct_object(path, kind, delete_access=False, allow_writes=kind == "directory")
        prior = None
        try:
            yield pin
        except BaseException as error:
            prior = error
            raise
        finally:
            try:
                pin.close()
            except BaseException as error:
                if pin.handle:
                    raise union_retained_ownership("evidence read retains exact ownership", prior=prior,
                        owners=(RetainedObjectOwner(RetainedObjectRole.VERIFICATION, pin),)) from error
                raise

    def _read(self, target: Path, label: str) -> bytes:
        try:
            with self._evidence_guard(target), self._stable_pin(target, "file") as pin:
                return read_pinned_file(pin)
        except FileNotFoundError as error:
            raise ContainmentStoreNotFound(f"{label} is missing: {target}") from error
        except ExactObjectOwnershipError:
            raise
        except (ExactObjectError, OSError) as error:
            if not target.exists():
                raise ContainmentStoreNotFound(f"{label} is missing: {target}") from error
            raise ContainmentStoreError(f"cannot read protected {label}: {error}") from error

    @contextmanager
    def _evidence_guard(self, target: Path):
        try:
            relative = Path(target).relative_to(self.evidence_root)
        except ValueError as error:
            raise ContainmentStoreError("authoritative access outside evidence root") from error
        if not relative.parts:
            raise ContainmentStoreError("authority access requires a run or catalog vault")
        root = self.evidence_root / relative.parts[0]
        primary = None
        try:
            with open_vault(root) as vault:
                vault.verify_descendant(target)
                try:
                    yield vault
                    vault.verify_descendant(target)
                except BaseException as error:
                    primary = error
                    raise
        except ExactObjectOwnershipError as error:
            if isinstance(primary, ContainmentStoreOwnershipError):
                raise union_retained_ownership(
                    "store operation and vault guard retain original handles",
                    prior=primary.ownership, owners=error.owners,
                ) from primary
            raise

    def open_evidence_vault(self, run_id: str):
        """Return an explicitly owned verified run vault; caller closes it."""
        return open_vault(self.evidence_run_path(run_id))

    def _prepare_vault(self, path: Path) -> None:
        self._create_trusted_directories(path.parent)
        if path.exists():
            with open_vault(path):
                pass
        else:
            with create_vault(path):
                self._record_written_path(path)

    def _create_trusted_directories(self, path: Path) -> None:
        missing = []
        current = path
        while not current.exists():
            missing.append(current)
            current = current.parent
        with pin_trusted_paths((current,)):
            with ExitStack() as stack:
                for child in reversed(missing):
                    parent = pin_direct_object(child.parent, kind="directory", delete_access=False)
                    pin = None
                    primary = None
                    try:
                        pin = create_pinned_directory_child(child, parent, delete_access=False)
                        self._record_written_path(child)
                        stack.enter_context(pin_trusted_paths((child,)))
                    except BaseException as error:
                        primary = error
                        raise
                    finally:
                        failures = []
                        owners = []
                        for owned, role in ((pin, RetainedObjectRole.VERIFICATION),
                                            (parent, RetainedObjectRole.DESTINATION_PARENT)):
                            if owned is None:
                                continue
                            try:
                                owned.close()
                            except BaseException as error:
                                failures.append(error)
                                if isinstance(error, ExactObjectOwnershipError):
                                    owners.extend(error.owners)
                            if owned.handle:
                                owners.append(RetainedObjectOwner(role, owned))
                        if owners:
                            cause = primary if primary is not None else failures[0]
                            raise union_retained_ownership("fresh directory creation retains original handles",
                                prior=getattr(cause, "ownership", cause), owners=tuple(owners)) from cause
                        if failures and primary is None:
                            raise failures[0]

    def read_evidence_file(self, run_id: str, target: Path, *, maximum_bytes: int) -> bytes:
        target = Path(target).absolute()
        if self.evidence_run_path(run_id) not in target.parents:
            raise ContainmentStoreError("capture read is outside the bound run vault")
        with self._evidence_guard(target), self._stable_pin(target, "file") as pin:
            return read_pinned_file(pin, maximum_bytes=maximum_bytes)

    def capture_evidence_file(self, run_id: str, source: Path, target: Path, *, maximum_bytes: int, stage: str) -> dict:
        """Copy exact bounded Low bytes into a new protected object; never move/adopt."""
        source, target = Path(source).absolute(), Path(target).absolute()
        if self.evidence_run_path(run_id) not in target.parents or not isinstance(stage, str) or not stage:
            raise ContainmentStoreError("capture requires a bound run target and origin stage")
        with ExitStack() as source_guards:
            for ancestor in reversed(source.parents):
                source_guards.enter_context(self._stable_pin(ancestor, "directory"))
            pin = source_guards.enter_context(self._stable_pin(source, "file"))
            data = read_pinned_file(pin, maximum_bytes=maximum_bytes)
            receipt = {"schemaVersion": 1, "sourcePath": str(source), "capturePath": str(target),
                "volumeSerial": pin.identity.volume_serial, "fileId": pin.identity.file_id,
                "byteCount": len(data), "sha256": hashlib.sha256(data).hexdigest(), "stage": stage}
            self._write_immutable(target, data, "captured input", require_new=True)
            self._write_immutable(target.with_name(target.name + ".capture.json"), _canonical(receipt), "capture provenance", require_new=True)
            if self.read_evidence_file(run_id, target, maximum_bytes=maximum_bytes) != data:
                raise ContainmentStoreError("captured bytes differ on protected readback")
            return receipt

    def _ensure_direct_directory(self, path: Path) -> None:
        if self.evidence_root in path.parents:
            parent = path if path.exists() else path.parent
            with self._evidence_guard(parent) as vault:
                self._ensure_direct_directory_unprotected(path)
                vault.verify_descendant(path)
        else:
            self._create_trusted_directories(path)

    def _ensure_direct_directory_unprotected(self, path: Path) -> None:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise ContainmentStoreError(f"cannot create containment directory {path}: {error}") from error
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current /= part
            try:
                metadata = current.lstat()
            except OSError as error:
                raise ContainmentStoreError(f"cannot inspect containment directory {current}: {error}") from error
            if not stat.S_ISDIR(metadata.st_mode):
                raise ContainmentStoreError(f"containment path is not a directory: {current}")
            self._reject_redirect(current)

    def _require_existing_direct_directory(
        self,
        path: Path,
        label: str,
        *,
        required: bool = True,
    ) -> bool:
        try:
            metadata = path.lstat()
        except FileNotFoundError as error:
            if not required:
                return False
            raise ContainmentStoreNotFound(
                f"{label} directory is missing: {path}"
            ) from error
        except OSError as error:
            raise ContainmentStoreError(
                f"cannot inspect {label} directory: {error}"
            ) from error
        if stat.S_ISLNK(metadata.st_mode) or bool(
            getattr(metadata, "st_file_attributes", 0)
            & _FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ContainmentStoreError(
                f"{label} directory is redirected or reparse: {path}"
            )
        if not stat.S_ISDIR(metadata.st_mode):
            raise ContainmentStoreError(
                f"{label} path is not a direct directory: {path}"
            )
        return True

    @staticmethod
    def _reject_redirect(path: Path) -> None:
        try:
            metadata = path.lstat()
        except OSError as error:
            raise ContainmentStoreError(f"cannot inspect containment path {path}: {error}") from error
        if path.is_symlink() or bool(
            getattr(metadata, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ContainmentStoreError(f"redirected containment path is not allowed: {path}")

    @staticmethod
    def _serialize(converter: Callable[[_T], bytes], value: _T, label: str) -> bytes:
        try:
            return converter(value)
        except ContainmentFormatError as error:
            raise ContainmentStoreError(f"{label} is invalid: {error}") from error

    @staticmethod
    def _parse(converter: Callable[[bytes], _T], data: bytes, label: str) -> _T:
        try:
            return converter(data)
        except ContainmentFormatError as error:
            raise ContainmentStoreMalformedEvidence(
                f"stored {label} is malformed: {error}"
            ) from error


def _intent_document(value: object, run_id: str) -> dict[str, object]:
    fields = {
        "schemaVersion",
        "runId",
        "mechanism",
        "sourceWorkspace",
        "mo2ArtifactId",
        "steamRoot",
        "commandFingerprint",
        "predecessorRunIds",
        "retryOf",
    }
    if type(value) is not dict or set(value) != fields:
        raise ContainmentStoreError("run intent fields are not exact")
    predecessors = value["predecessorRunIds"]
    if (
        value["schemaVersion"] != 1
        or value["runId"] != run_id
        or value["mechanism"] != "isolated-low-integrity-junction-projection-v1"
        or type(value["sourceWorkspace"]) is not str
        or not Path(value["sourceWorkspace"]).is_absolute()
        or type(value["mo2ArtifactId"]) is not str
        or not value["mo2ArtifactId"]
        or type(value["steamRoot"]) is not str
        or not Path(value["steamRoot"]).is_absolute()
        or type(value["commandFingerprint"]) is not str
        or re.fullmatch(
            r"containment-command-sha256:[0-9a-f]{64}",
            value["commandFingerprint"],
        )
        is None
        or type(predecessors) is not list
        or any(type(item) is not str or _RUN.fullmatch(item) is None for item in predecessors)
        or tuple(predecessors) != tuple(sorted(set(predecessors)))
        or run_id in predecessors
        or not _retry_reference(value["retryOf"], value["commandFingerprint"])
    ):
        raise ContainmentStoreError("run intent values are malformed")
    return dict(value)


def _retry_reference(value: object, command_fingerprint: object) -> bool:
    if value is None:
        return True
    fields = {
        "runId", "scenario", "recoveryId", "authorityId", "commandFingerprint"
    }
    if type(value) is not dict or set(value) != fields:
        return False
    try:
        scenario = ContainmentScenario(value["scenario"])
    except (TypeError, ValueError):
        return False
    return (
        scenario.value == value["scenario"]
        and type(value["runId"]) is str
        and _RUN.fullmatch(value["runId"]) is not None
        and value["commandFingerprint"] == command_fingerprint
        and type(value["recoveryId"]) is str
        and re.fullmatch(
            r"containment-recovery-sha256:[0-9a-f]{64}", value["recoveryId"]
        )
        is not None
        and type(value["authorityId"]) is str
        and re.fullmatch(
            r"containment-retry-sha256:[0-9a-f]{64}", value["authorityId"]
        )
        is not None
    )


def _retry_basis(
    recovery: ScenarioRecovery,
    recovery_id: str,
    command_fingerprint: str,
) -> dict[str, object]:
    if (
        not isinstance(recovery, ScenarioRecovery)
        or not recovery.fresh_run_permitted
        or recovery.cleanup_status.value != "Succeeded"
        or type(recovery_id) is not str
        or re.fullmatch(r"containment-recovery-sha256:[0-9a-f]{64}", recovery_id)
        is None
        or type(command_fingerprint) is not str
        or re.fullmatch(r"containment-command-sha256:[0-9a-f]{64}", command_fingerprint)
        is None
    ):
        raise ContainmentStoreError("retry authority basis is invalid")
    return {
        "schemaVersion": 1,
        "oldRunId": recovery.run_id,
        "scenario": recovery.scenario.value,
        "recoveryId": recovery_id,
        "commandFingerprint": command_fingerprint,
    }


def _retry_document(
    value: object,
    run_id: str,
    scenario: ContainmentScenario,
) -> dict[str, object]:
    fields = {
        "schemaVersion", "oldRunId", "scenario", "recoveryId",
        "commandFingerprint", "authorityId", "state", "consumedByRunId",
    }
    if type(value) is not dict or set(value) != fields:
        raise ContainmentStoreError("retry authority fields are not exact")
    if (
        value["schemaVersion"] != 1
        or value["oldRunId"] != run_id
        or value["scenario"] != scenario.value
        or type(value["recoveryId"]) is not str
        or re.fullmatch(r"containment-recovery-sha256:[0-9a-f]{64}", value["recoveryId"])
        is None
        or type(value["commandFingerprint"]) is not str
        or re.fullmatch(r"containment-command-sha256:[0-9a-f]{64}", value["commandFingerprint"])
        is None
        or type(value["authorityId"]) is not str
        or re.fullmatch(r"containment-retry-sha256:[0-9a-f]{64}", value["authorityId"])
        is None
        or value["state"] != "Available"
        or value["consumedByRunId"] is not None
    ):
        raise ContainmentStoreError("retry authority values are malformed")
    basis = {
        name: value[name]
        for name in (
            "schemaVersion", "oldRunId", "scenario", "recoveryId",
            "commandFingerprint",
        )
    }
    expected_id = "containment-retry-sha256:" + hashlib.sha256(
        _canonical(basis)
    ).hexdigest()
    if value["authorityId"] != expected_id:
        raise ContainmentStoreError("retry authority content ID is invalid")
    return dict(value)


def _retry_consumption_document(
    authority: dict[str, object],
    new_run_id: str,
) -> dict[str, object]:
    if type(new_run_id) is not str or _RUN.fullmatch(new_run_id) is None:
        raise ContainmentStoreError("retry consumption run ID is malformed")
    return {
        "schemaVersion": 1,
        "oldRunId": authority["oldRunId"],
        "scenario": authority["scenario"],
        "recoveryId": authority["recoveryId"],
        "authorityId": authority["authorityId"],
        "commandFingerprint": authority["commandFingerprint"],
        "consumedByRunId": new_run_id,
    }


def _retry_consumption_value(
    value: object,
    authority: dict[str, object],
) -> dict[str, object]:
    fields = {
        "schemaVersion", "oldRunId", "scenario", "recoveryId", "authorityId",
        "commandFingerprint", "consumedByRunId",
    }
    if type(value) is not dict or set(value) != fields:
        raise ContainmentStoreError("retry authority consumption fields are not exact")
    if (
        value["schemaVersion"] != 1
        or any(
            value[name] != authority[name]
            for name in (
                "oldRunId", "scenario", "recoveryId", "authorityId",
                "commandFingerprint",
            )
        )
        or type(value["consumedByRunId"]) is not str
        or _RUN.fullmatch(value["consumedByRunId"]) is None
    ):
        raise ContainmentStoreError("retry authority consumption binding is invalid")
    return dict(value)


def _launch_document(
    value: object,
    run_id: str,
    scenario: ContainmentScenario,
) -> dict[str, object]:
    fields = {
        "schemaVersion", "runId", "scenario", "purpose", "pid",
        "creationTime", "executable", "executableVersion", "arguments",
        "workingDirectory", "integrity",
    }
    if type(value) is not dict or set(value) != fields:
        raise ContainmentStoreError("launch evidence fields are not exact")
    if (
        value["schemaVersion"] != 1
        or value["runId"] != run_id
        or value["scenario"] != scenario.value
        or value["purpose"] != "stage-mo2-scenario"
        or type(value["pid"]) is not int
        or value["pid"] <= 0
        or type(value["creationTime"]) is not int
        or value["creationTime"] <= 0
        or type(value["executable"]) is not str
        or not value["executable"]
        or type(value["executableVersion"]) is not str
        or not value["executableVersion"]
        or type(value["workingDirectory"]) is not str
        or not value["workingDirectory"]
        or type(value["arguments"]) is not list
        or any(type(item) is not str for item in value["arguments"])
        or type(value["integrity"]) is not str
    ):
        raise ContainmentStoreError("launch evidence values are malformed")
    try:
        IntegrityObservation(value["integrity"])
    except ValueError as error:
        raise ContainmentStoreError("launch evidence integrity is malformed") from error
    return dict(value)


def _tree_document(value: TreeIdentity) -> dict[str, object]:
    if not isinstance(value, TreeIdentity):
        raise ContainmentStoreError("protected state contains a non-tree value")
    return {
        "sha256": value.sha256,
        "regularFileCount": value.regular_file_count,
        "directoryCount": value.directory_count,
        "totalSize": value.total_size,
    }


def _protected_document(value: ProtectedState) -> dict[str, object]:
    if not isinstance(value, ProtectedState):
        raise ContainmentStoreError("protected state must be ProtectedState")
    return {
        "sourceMods": _tree_document(value.source_mods),
        "labProfileSha256": value.lab_profile_sha256,
        "playProfileSha256": value.play_profile_sha256,
        "downloads": _tree_document(value.downloads),
        "overwrite": _tree_document(value.overwrite),
        "boundedGame": _tree_document(value.bounded_game),
    }


def _tree_from_document(value: object, label: str) -> TreeIdentity:
    fields = {"sha256", "regularFileCount", "directoryCount", "totalSize"}
    if type(value) is not dict or set(value) != fields:
        raise ContainmentStoreError(f"{label} tree fields are invalid")
    sha = value["sha256"]
    counts = (
        value["regularFileCount"],
        value["directoryCount"],
        value["totalSize"],
    )
    if type(sha) is not str or re.fullmatch(r"[0-9a-f]{64}", sha) is None:
        raise ContainmentStoreError(f"{label} tree hash is invalid")
    if any(type(item) is not int or item < 0 for item in counts):
        raise ContainmentStoreError(f"{label} tree counts are invalid")
    return TreeIdentity(sha, *counts)


def _protected_from_document(value: object) -> ProtectedState:
    fields = {
        "sourceMods",
        "labProfileSha256",
        "playProfileSha256",
        "downloads",
        "overwrite",
        "boundedGame",
    }
    if type(value) is not dict or set(value) != fields:
        raise ContainmentStoreError("protected state fields are invalid")
    hashes = (value["labProfileSha256"], value["playProfileSha256"])
    if any(type(item) is not str or re.fullmatch(r"[0-9a-f]{64}", item) is None for item in hashes):
        raise ContainmentStoreError("protected profile hash is invalid")
    return ProtectedState(
        _tree_from_document(value["sourceMods"], "sourceMods"),
        hashes[0],
        hashes[1],
        _tree_from_document(value["downloads"], "downloads"),
        _tree_from_document(value["overwrite"], "overwrite"),
        _tree_from_document(value["boundedGame"], "boundedGame"),
    )


def _promote_no_replace_posix(source: Path, target: Path) -> None:
    """Hard-link a POSIX immutable candidate into place without replacement."""
    if os.name == "nt":
        raise ContainmentStoreError(
            "POSIX-only immutable promotion cannot run on Windows"
        )
    os.link(source, target, follow_symlinks=False)
    source.unlink()


def _replace_durable(source: Path, target: Path) -> None:
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.MoveFileExW.argtypes = (
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
        )
        kernel32.MoveFileExW.restype = wintypes.BOOL
        flags = 0x00000001 | 0x00000008
        if not kernel32.MoveFileExW(str(source), str(target), flags):
            error = ctypes.get_last_error()
            raise OSError(error, "durable atomic replacement failed", str(target))
        return
    os.replace(source, target)
    directory = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


__all__ = [
    "ContainmentStore",
    "ContainmentStoreError",
    "ContainmentStoreOwnershipError",
    "ContainmentStoreMalformedEvidence",
    "ContainmentStoreNotFound",
    "ImmutableWrite",
]
