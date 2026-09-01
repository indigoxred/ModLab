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

from .mo2_containment_model import (
    CapabilityDecision,
    CapabilityVerdict,
    ContainmentScenario,
    ProtectedState,
    ScenarioJournal,
    ScenarioRecovery,
    ScenarioResult,
    ScenarioState,
    TreeIdentity,
    WatchOutcome,
)
from .mo2_containment_serialization import (
    ContainmentFormatError,
    capability_decision_from_bytes,
    capability_decision_to_bytes,
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
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_PROCESS_LOCKS: dict[str, threading.Lock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()
_UNSET = object()
_T = TypeVar("_T")


class ContainmentStoreError(RuntimeError):
    """A containment document could not be stored or proved exact."""


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
def _windows_run_mutex(identity: str):
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
        result = kernel32.WaitForSingleObject(handle, 0)
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

    def __init__(self, validation_root: Path):
        root = Path(validation_root).expanduser().absolute()
        self.root = root
        self._ensure_direct_directory(root)
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

    def load_decision(self, run_id: str) -> CapabilityDecision:
        with self._run_lock(run_id):
            raw = self._read(self.decision_path(run_id), "capability decision")
            probe = self._decision_probe(raw, run_id)
            results, outcomes = self._resolve_decision_evidence_unlocked(probe)
            return self._decision_from_bytes(raw, results, outcomes)

    def run_path(self, run_id: str) -> Path:
        return self.root / self._run_hex(run_id)

    def request_path(self, run_id: str) -> Path:
        return self.run_path(run_id) / "request.json"

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

    def quarantine_path(self, run_id: str) -> Path:
        return self.run_path(run_id) / "quarantine"

    def decision_path(self, run_id: str) -> Path:
        return self.run_path(run_id) / "decision.json"

    def journal_id_for(self, journal: ScenarioJournal) -> str:
        data = self._serialize(scenario_journal_to_bytes, journal, "journal")
        return "containment-journal-sha256:" + hashlib.sha256(data).hexdigest()

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
            raise ContainmentStoreError("watch outcome has the wrong run/scenario binding")
        if expected_id is not None and observed_id != expected_id:
            raise ContainmentStoreError("watch outcome content ID mismatch")
        if watch_outcome_to_bytes(value) != data:
            raise ContainmentStoreError("watch outcome bytes are not canonical")
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
            raise ContainmentStoreError("scenario result has the wrong run/scenario binding")
        if scenario_result_to_bytes(value, watch) != data:
            raise ContainmentStoreError("scenario result bytes are not canonical")
        return value

    def _resolve_decision_evidence_unlocked(
        self,
        decision: CapabilityDecision,
    ) -> tuple[tuple[ScenarioResult, ...], tuple[WatchOutcome, ...]]:
        if not decision.scenario_result_ids:
            return (), ()
        wanted = set(decision.scenario_result_ids)
        found: dict[str, tuple[ScenarioResult, WatchOutcome]] = {}
        for scenario in ContainmentScenario:
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
            raise ContainmentStoreError(f"capability decision is invalid: {error}") from error

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
            raise ContainmentStoreError(f"capability decision is invalid: {error}") from error

    @staticmethod
    def _decision_probe(data: bytes, run_id: str) -> CapabilityDecision:
        try:
            document = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_json)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ContainmentStoreError(f"capability decision is malformed: {error}") from error
        fields = {
            "schemaVersion",
            "runId",
            "mechanism",
            "verdict",
            "scenarioResultIds",
            "reasons",
        }
        if type(document) is not dict or set(document) != fields or _canonical(document) != data:
            raise ContainmentStoreError("capability decision bytes are not canonical")
        identifiers = document["scenarioResultIds"]
        reasons = document["reasons"]
        if (
            document["runId"] != run_id
            or type(identifiers) is not list
            or any(type(item) is not str for item in identifiers)
            or type(reasons) is not list
            or any(type(item) is not str for item in reasons)
        ):
            raise ContainmentStoreError("capability decision binding is malformed")
        try:
            verdict = CapabilityVerdict(document["verdict"])
        except (TypeError, ValueError) as error:
            raise ContainmentStoreError("capability decision verdict is malformed") from error
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

    def _write_immutable(self, target: Path, data: bytes, label: str) -> bool:
        if target.exists():
            existing = self._read(target, label)
            if existing != data:
                raise ContainmentStoreError(f"stored {label} path contains different bytes")
            return True
        part = target.parent / f".{target.name}.{uuid.uuid4().hex}.part"
        promoted = False
        try:
            with part.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            self._reject_redirect(part)
            _promote_no_replace(part, target)
            promoted = True
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
            phase = "after promotion" if promoted else "before promotion"
            raise ContainmentStoreError(
                f"cannot atomically create {label} {phase}: {error}"
            ) from error
        finally:
            try:
                part.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

    def _atomic_replace(
        self,
        target: Path,
        data: bytes,
        expected: bytes,
        label: str,
    ) -> None:
        if self._read(target, label) != expected:
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
            os.replace(part, target)
            if self._read(target, label) != data:
                raise ContainmentStoreError(f"{label} differs after atomic replacement")
        except ContainmentStoreError:
            raise
        except OSError as error:
            raise ContainmentStoreError(f"cannot atomically replace {label}: {error}") from error
        finally:
            try:
                part.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

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
            raise ContainmentStoreError(f"stored {label} is invalid: {error}") from error


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


def _promote_no_replace(source: Path, target: Path) -> None:
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.MoveFileExW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
        kernel32.MoveFileExW.restype = wintypes.BOOL
        if kernel32.MoveFileExW(str(source), str(target), 0x00000008):
            return
        error = ctypes.get_last_error()
        if error in {80, 183}:
            raise FileExistsError(error, "destination already exists", str(target))
        raise OSError(error, "atomic no-replace promotion failed", str(target))
    os.link(source, target, follow_symlinks=False)
    source.unlink()


__all__ = [
    "ContainmentStore",
    "ContainmentStoreError",
    "ContainmentStoreNotFound",
    "ImmutableWrite",
]
