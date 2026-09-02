"""Crash-safe confined storage for MO2 containment scenario evidence."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from contextlib import contextmanager
from dataclasses import dataclass, replace
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
    ):
        self._effect_recorder = _effect_recorder
        self._open_root(validation_root, create=True)

    @classmethod
    def open_readonly(cls, validation_root: Path) -> "ContainmentStore":
        """Open one existing direct validation root without preparing any path."""
        store = cls.__new__(cls)
        store._effect_recorder = None
        store._open_root(validation_root, create=False)
        return store

    def _open_root(self, validation_root: Path, *, create: bool) -> None:
        root = Path(validation_root).expanduser().absolute()
        self.root = root
        if create:
            self._ensure_direct_directory(root)
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
    ) -> ImmutableWrite[dict[str, object]]:
        checked = _intent_document(document, run_id)
        data = _canonical(checked)
        target = self.intent_path(run_id)
        with self._run_lock(run_id):
            self._prepare_run(run_id)
            existed = self._write_immutable(target, data, "run intent")
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
        if label not in {"before", "after"}:
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
                self.run_path(run_id),
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
        try:
            entries = tuple(os.scandir(self.root))
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

    def request_path(self, run_id: str) -> Path:
        return self.run_path(run_id) / "request.json"

    def intent_path(self, run_id: str) -> Path:
        return self.run_path(run_id) / "intent.json"

    def scenario_path(self, run_id: str, scenario: ContainmentScenario) -> Path:
        if not isinstance(scenario, ContainmentScenario):
            raise ContainmentStoreError("scenario must be ContainmentScenario")
        return self.run_path(run_id) / "scenarios" / scenario.value

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
        return self.run_path(run_id) / "decision.json"

    def authority_path(self) -> Path:
        return self.root / "authority"

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
        self._ensure_direct_directory(self.run_path(run_id))
        self._ensure_direct_directory(self.run_path(run_id) / "scenarios")
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
        if label not in {"before", "after"}:
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
        return value

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
            self.run_path(decision.run_id) / "scenarios",
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

    def _write_immutable(self, target: Path, data: bytes, label: str) -> bool:
        if target.exists():
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
        part = target.parent / f".{target.name}.{uuid.uuid4().hex}.part"
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

    def _atomic_replace(
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
        part = target.parent / f".{target.name}.{uuid.uuid4().hex}.part"
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

    def _read(self, target: Path, label: str) -> bytes:
        try:
            metadata = target.lstat()
        except FileNotFoundError as error:
            raise ContainmentStoreNotFound(f"{label} is missing: {target}") from error
        except OSError as error:
            raise ContainmentStoreError(f"cannot inspect {label}: {error}") from error
        if not stat.S_ISREG(metadata.st_mode):
            raise ContainmentStoreError(f"{label} must be a direct regular file")
        self._reject_redirect(target)
        try:
            return target.read_bytes()
        except OSError as error:
            raise ContainmentStoreError(f"cannot read {label}: {error}") from error

    def _ensure_direct_directory(self, path: Path) -> None:
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
