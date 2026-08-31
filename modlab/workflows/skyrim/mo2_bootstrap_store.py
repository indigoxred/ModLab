"""Confined, crash-safe storage for verified MO2 bootstrap records."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import threading
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

from modlab.adapters.mo2.bootstrap_model import (
    BootstrapDisposition,
    BootstrapJobState,
    BootstrapJournal,
    BootstrapPlan,
    BootstrapReceipt,
    BootstrapReceiptMode,
    FileIdentity,
)
from modlab.adapters.mo2.bootstrap_serialization import (
    BootstrapFormatError,
    journal_from_bytes,
    journal_to_bytes,
    plan_from_bytes,
    plan_to_bytes,
    receipt_from_bytes,
    receipt_to_bytes,
)
from modlab.workspace import WorkspaceLayout, workspace_layout


class Mo2BootstrapStoreError(RuntimeError):
    """Bootstrap state could not be retained without weakening its evidence."""


class Mo2BootstrapNotFoundError(LookupError):
    """A requested bootstrap record does not exist in this workspace."""


@dataclass(frozen=True)
class StoredBootstrapPlan:
    plan: BootstrapPlan
    path: Path
    data: bytes
    sha256: str
    changed: bool


@dataclass(frozen=True)
class StoredBootstrapJournal:
    journal: BootstrapJournal
    path: Path
    data: bytes
    sha256: str
    changed: bool


@dataclass(frozen=True)
class StoredBootstrapReceipt:
    receipt: BootstrapReceipt
    path: Path
    data: bytes
    sha256: str
    changed: bool


@dataclass(frozen=True)
class BootstrapReceiptMatch:
    """Immutable package and game evidence required before deeper live matching."""

    release_id: str
    release_descriptor_sha256: str
    archive_artifact_id: str
    archive_metadata_sha256: str
    archive_sha256: str
    archive_size: int
    skyrim_executable: FileIdentity
    final_root: str

    @classmethod
    def from_plan(cls, plan: BootstrapPlan) -> BootstrapReceiptMatch:
        try:
            plan_to_bytes(plan)
        except BootstrapFormatError as error:
            raise Mo2BootstrapStoreError(f"invalid bootstrap plan: {error}") from error
        return cls(
            release_id=plan.release_id,
            release_descriptor_sha256=plan.release_descriptor_sha256,
            archive_artifact_id=plan.archive.artifact_id,
            archive_metadata_sha256=plan.archive.metadata_sha256,
            archive_sha256=plan.archive.sha256,
            archive_size=plan.archive.size,
            skyrim_executable=plan.skyrim_executable,
            final_root=plan.final_root,
        )


_PLAN_ID = re.compile(r"^bootstrap-plan-sha256:([0-9a-f]{64})$")
_JOB_ID = re.compile(r"^bootstrap-job:([0-9a-f]{32})$")
_RECEIPT_ID = re.compile(r"^bootstrap-receipt-sha256:([0-9a-f]{64})$")
_RECEIPT_FILENAME = re.compile(r"^([0-9a-f]{64})\.json$")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_CREATE_TRANSITIONS = {
    BootstrapJobState.PLANNED: {BootstrapJobState.STAGING},
    BootstrapJobState.STAGING: {BootstrapJobState.STAGED},
    BootstrapJobState.STAGED: {BootstrapJobState.APPLYING},
    BootstrapJobState.APPLYING: {BootstrapJobState.ACTIVATED},
    BootstrapJobState.ACTIVATED: {BootstrapJobState.VERIFIED},
    BootstrapJobState.RECOVERY_REQUIRED: {
        BootstrapJobState.ACTIVATED,
        BootstrapJobState.RECOVERED,
    },
}
_ADOPT_TRANSITIONS = {
    BootstrapJobState.PLANNED: {BootstrapJobState.STAGING},
    BootstrapJobState.STAGING: {BootstrapJobState.STAGED},
    BootstrapJobState.STAGED: {BootstrapJobState.VERIFIED},
    BootstrapJobState.RECOVERY_REQUIRED: {BootstrapJobState.RECOVERED},
}
_CREATE_FAILURE_SOURCES = {
    BootstrapJobState.PLANNED,
    BootstrapJobState.STAGING,
    BootstrapJobState.STAGED,
    BootstrapJobState.APPLYING,
    BootstrapJobState.ACTIVATED,
}
_ADOPT_FAILURE_SOURCES = {
    BootstrapJobState.PLANNED,
    BootstrapJobState.STAGING,
    BootstrapJobState.STAGED,
}
_IMMUTABLE_JOURNAL_FIELDS = tuple(
    item.name
    for item in fields(BootstrapJournal)
    if item.name
    not in {
        "state",
        "stage_inventory_sha256",
        "stage_entry_count",
        "activated_inventory_sha256",
        "activated_entry_count",
        "updated_at",
        "receipt_id",
        "error",
    }
)
_PROCESS_LOCKS: dict[str, threading.Lock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_bootstrap_job_id() -> str:
    return f"bootstrap-job:{uuid.uuid4().hex}"


@contextmanager
def _windows_named_mutex(identity: str):
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_wchar_p,
    )
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.ReleaseMutex.argtypes = (ctypes.c_void_p,)
    kernel32.ReleaseMutex.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.restype = ctypes.c_int

    name = f"Global\\ModLab-MO2-Bootstrap-{identity}"
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        error = ctypes.get_last_error()
        raise Mo2BootstrapStoreError(
            f"cannot create bootstrap document mutex: Windows error {error}"
        )
    acquired = False
    try:
        result = kernel32.WaitForSingleObject(handle, 0)
        if result == 0x00000102:
            raise Mo2BootstrapStoreError(
                "bootstrap document is already being changed by another process"
            )
        if result not in {0x00000000, 0x00000080}:
            error = ctypes.get_last_error()
            raise Mo2BootstrapStoreError(
                "cannot acquire bootstrap document mutex: "
                f"Windows result {result:#x}, error {error}"
            )
        acquired = True
        yield
    finally:
        if acquired:
            kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)


class Mo2BootstrapStore:
    def __init__(
        self,
        workspace_root: Path,
        *,
        clock: Callable[[], datetime] = utc_now,
        job_id_factory: Callable[[], str] = new_bootstrap_job_id,
    ):
        self.requested_root = Path(workspace_root).expanduser().absolute()
        self.layout: WorkspaceLayout = workspace_layout(workspace_root)
        self._clock = clock
        self._job_id_factory = job_id_factory

    def plan_path(self, plan_id: str) -> Path:
        digest = self._identity_digest(plan_id, _PLAN_ID, "plan ID")
        return self.layout.mo2_bootstrap_plans / f"{digest}.json"

    def job_directory(self, job_id: str) -> Path:
        job_hex = self._identity_digest(job_id, _JOB_ID, "job ID")
        return self.layout.mo2_bootstrap_jobs / job_hex

    def journal_path(self, job_id: str) -> Path:
        return self.job_directory(job_id) / "journal.json"

    def stage_root(self, job_id: str) -> Path:
        job_hex = self._identity_digest(job_id, _JOB_ID, "job ID")
        return self.layout.skyrim_mo2.parent / (
            f".skyrim-se-ae.modlab-stage-{job_hex}"
        )

    def prior_root(self, job_id: str) -> Path:
        return self.job_directory(job_id) / "prior"

    def receipt_path(self, receipt_id: str) -> Path:
        digest = self._identity_digest(receipt_id, _RECEIPT_ID, "receipt ID")
        return self.layout.mo2_bootstrap_receipts / f"{digest}.json"

    def write_plan(self, plan: BootstrapPlan) -> StoredBootstrapPlan:
        data = self._serialize(plan_to_bytes, plan, "plan")
        self._validate_plan_workspace(plan)
        target = self.plan_path(plan.plan_id)
        changed = self._write_immutable(target, data, "plan")
        loaded = self.load_plan(plan.plan_id)
        if loaded.plan != plan or loaded.data != data:
            raise Mo2BootstrapStoreError("stored plan differs after write")
        return StoredBootstrapPlan(
            plan=loaded.plan,
            path=loaded.path,
            data=loaded.data,
            sha256=loaded.sha256,
            changed=changed,
        )

    def load_plan(self, plan_id: str) -> StoredBootstrapPlan:
        path = self.plan_path(plan_id)
        data = self._read_document(path, "plan", plan_id)
        plan = self._parse(plan_from_bytes, data, "plan", path)
        if plan.plan_id != plan_id or plan_to_bytes(plan) != data:
            raise Mo2BootstrapStoreError(
                f"stored plan bytes do not match their content address: {path}"
            )
        self._validate_plan_workspace(plan)
        return StoredBootstrapPlan(
            plan=plan,
            path=path,
            data=data,
            sha256=hashlib.sha256(data).hexdigest(),
            changed=False,
        )

    def create_job(self, plan: BootstrapPlan) -> StoredBootstrapJournal:
        data = self._serialize(plan_to_bytes, plan, "plan")
        stored_plan = self.load_plan(plan.plan_id)
        if stored_plan.data != data or stored_plan.plan != plan:
            raise Mo2BootstrapStoreError("stored plan changed before job creation")
        self._validate_plan_workspace(plan)
        if plan.disposition not in {
            BootstrapDisposition.CREATE,
            BootstrapDisposition.ADOPT,
        }:
            raise Mo2BootstrapStoreError(
                "a bootstrap job requires a Create or Adopt plan"
            )
        expected_kind = (
            "Empty"
            if plan.disposition is BootstrapDisposition.CREATE
            else "Existing"
        )
        if plan.target.kind != expected_kind:
            raise Mo2BootstrapStoreError(
                f"{plan.disposition.value} job requires a {expected_kind} target"
            )

        try:
            job_id = self._job_id_factory()
        except Exception as error:
            raise Mo2BootstrapStoreError(
                f"could not derive bootstrap job ID: {error}"
            ) from error
        self._identity_digest(job_id, _JOB_ID, "job ID")
        job_directory = self.job_directory(job_id)
        stage_root = self.stage_root(job_id)
        prior_root = self.prior_root(job_id)
        self._validate_target(job_directory)
        self._validate_target(stage_root)
        if self._lstat_if_exists(job_directory) is not None:
            raise Mo2BootstrapStoreError(
                f"bootstrap job already exists: {job_directory}"
            )
        if self._lstat_if_exists(stage_root) is not None:
            raise Mo2BootstrapStoreError(
                f"bootstrap staging path already exists: {stage_root}"
            )

        timestamp = self._timestamp(self._clock())
        journal = BootstrapJournal(
            schema_version=1,
            job_id=job_id,
            plan_id=plan.plan_id,
            disposition=plan.disposition,
            state=BootstrapJobState.PLANNED,
            stage_root=str(stage_root),
            prior_root=str(prior_root),
            final_root=str(self.layout.skyrim_mo2),
            prior_target_kind=plan.target.kind,
            prior_inventory_sha256=plan.target.inventory_sha256,
            prior_entry_count=plan.target.entry_count,
            stage_inventory_sha256=None,
            stage_entry_count=None,
            activated_inventory_sha256=None,
            activated_entry_count=None,
            created_at=timestamp,
            updated_at=timestamp,
            receipt_id=None,
            error=None,
        )
        journal_data = self._serialize(journal_to_bytes, journal, "journal")

        self._prepare_directory(job_directory.parent)
        created_job_directory = False
        try:
            job_directory.mkdir()
            created_job_directory = True
            self._validate_existing_directory(job_directory, "bootstrap job directory")
            changed = self._atomic_create(
                self.journal_path(job_id),
                journal_data,
                f"journal-{job_id.removeprefix('bootstrap-job:')}",
            )
            if not changed:
                raise Mo2BootstrapStoreError(
                    "bootstrap journal appeared during job creation"
                )
            return self.load_job(job_id, changed=True)
        except Mo2BootstrapStoreError:
            if created_job_directory:
                self._remove_empty_directory(job_directory)
            raise
        except Exception as error:
            if created_job_directory:
                self._remove_empty_directory(job_directory)
            raise Mo2BootstrapStoreError(
                f"could not create bootstrap job {job_id}: {error}"
            ) from error

    def load_job(
        self, job_id: str, *, changed: bool = False
    ) -> StoredBootstrapJournal:
        path = self.journal_path(job_id)
        data = self._read_document(path, "journal", job_id)
        journal = self._parse(journal_from_bytes, data, "journal", path)
        if journal.job_id != job_id or journal_to_bytes(journal) != data:
            raise Mo2BootstrapStoreError(
                f"stored journal bytes do not match their job identity: {path}"
            )
        if not self._journal_paths_match(journal):
            raise Mo2BootstrapStoreError(
                f"stored journal paths do not match job-derived workspace paths: {path}"
            )
        return StoredBootstrapJournal(
            journal=journal,
            path=path,
            data=data,
            sha256=hashlib.sha256(data).hexdigest(),
            changed=changed,
        )

    def transition_job(
        self,
        observed: StoredBootstrapJournal,
        expected_state: BootstrapJobState,
        replacement: BootstrapJournal,
    ) -> StoredBootstrapJournal:
        if not isinstance(observed, StoredBootstrapJournal):
            raise Mo2BootstrapStoreError(
                "journal transition requires an exact stored journal observation"
            )
        expected_path = self.journal_path(observed.journal.job_id)
        if observed.path != expected_path:
            raise Mo2BootstrapStoreError("stored journal observation has the wrong path")
        if observed.sha256 != hashlib.sha256(observed.data).hexdigest():
            raise Mo2BootstrapStoreError("stored journal observation hash is inconsistent")
        if observed.journal.state is not expected_state:
            raise Mo2BootstrapStoreError(
                "journal is not in the caller's expected state: "
                f"expected {expected_state.value}, observed {observed.journal.state.value}"
            )

        replacement_data = self._serialize(
            journal_to_bytes,
            replacement,
            "journal",
        )
        with self._document_lock(expected_path):
            current_data = self._read_document(
                expected_path,
                "journal",
                observed.journal.job_id,
            )
            if current_data != observed.data:
                raise Mo2BootstrapStoreError(
                    "bootstrap journal changed after observation"
                )
            current = self._parse(
                journal_from_bytes,
                current_data,
                "journal",
                expected_path,
            )
            if current != observed.journal:
                raise Mo2BootstrapStoreError(
                    "bootstrap journal changed after observation"
                )
            self._validate_transition(current, replacement)
            self._atomic_replace(
                expected_path,
                replacement_data,
                f"journal-{current.job_id.removeprefix('bootstrap-job:')}",
                expected_current=current_data,
            )
            loaded = self.load_job(current.job_id, changed=True)
            if loaded.journal != replacement or loaded.data != replacement_data:
                raise Mo2BootstrapStoreError("stored journal differs after transition")
            return loaded

    def write_receipt(self, receipt: BootstrapReceipt) -> StoredBootstrapReceipt:
        data = self._serialize(receipt_to_bytes, receipt, "receipt")
        if not self._same_windows_path(receipt.final_root, self.layout.skyrim_mo2):
            raise Mo2BootstrapStoreError(
                "receipt final root does not match this workspace's Skyrim MO2 target"
            )
        target = self.receipt_path(receipt.receipt_id)
        changed = self._write_immutable(target, data, "receipt")
        loaded = self.load_receipt(receipt.receipt_id)
        if loaded.receipt != receipt or loaded.data != data:
            raise Mo2BootstrapStoreError("stored receipt differs after write")
        return StoredBootstrapReceipt(
            receipt=loaded.receipt,
            path=loaded.path,
            data=loaded.data,
            sha256=loaded.sha256,
            changed=changed,
        )

    def load_receipt(self, receipt_id: str) -> StoredBootstrapReceipt:
        path = self.receipt_path(receipt_id)
        data = self._read_document(path, "receipt", receipt_id)
        receipt = self._parse(receipt_from_bytes, data, "receipt", path)
        if receipt.receipt_id != receipt_id or receipt_to_bytes(receipt) != data:
            raise Mo2BootstrapStoreError(
                f"stored receipt bytes do not match their content address: {path}"
            )
        if not self._same_windows_path(receipt.final_root, self.layout.skyrim_mo2):
            raise Mo2BootstrapStoreError(
                f"stored receipt targets another workspace: {path}"
            )
        return StoredBootstrapReceipt(
            receipt=receipt,
            path=path,
            data=data,
            sha256=hashlib.sha256(data).hexdigest(),
            changed=False,
        )

    def find_compatible_receipt(
        self, match: BootstrapReceiptMatch
    ) -> StoredBootstrapReceipt | None:
        if not isinstance(match, BootstrapReceiptMatch):
            raise Mo2BootstrapStoreError(
                "receipt search requires exact compatibility evidence"
            )
        directory = self.layout.mo2_bootstrap_receipts
        metadata = self._lstat_if_exists(directory)
        if metadata is None:
            return None
        self._validate_existing_directory(directory, "bootstrap receipt directory")
        try:
            entries = sorted(
                directory.iterdir(),
                key=lambda path: (path.name.casefold(), path.name),
            )
        except OSError as error:
            raise Mo2BootstrapStoreError(
                f"cannot enumerate bootstrap receipts {directory}: {error}"
            ) from error

        compatible: list[StoredBootstrapReceipt] = []
        for path in entries:
            filename_match = _RECEIPT_FILENAME.fullmatch(path.name)
            if filename_match is None:
                raise Mo2BootstrapStoreError(
                    f"unexpected entry in bootstrap receipt store: {path}"
                )
            receipt_id = f"bootstrap-receipt-sha256:{filename_match.group(1)}"
            loaded = self.load_receipt(receipt_id)
            if self._receipt_matches(loaded.receipt, match):
                self._require_verified_receipt(loaded.receipt)
                compatible.append(loaded)
        if not compatible:
            return None
        return max(
            compatible,
            key=lambda item: (item.receipt.verified_at, item.receipt.receipt_id),
        )

    def _write_immutable(self, target: Path, data: bytes, label: str) -> bool:
        metadata = self._lstat_if_exists(target)
        if metadata is not None:
            self._validate_existing_file(target, f"stored {label}")
            existing = self._read_existing_bytes(target, f"stored {label}")
            if existing != data:
                raise Mo2BootstrapStoreError(
                    f"stored {label} path contains different bytes: {target}"
                )
            return False
        return self._atomic_create(target, data, label)

    def _atomic_create(self, target: Path, data: bytes, label: str) -> bool:
        part = self._stage_document(target, data, label)
        promoted = False
        try:
            self._validate_target(target)
            try:
                _promote_no_replace(part, target)
            except FileExistsError:
                self._validate_existing_file(target, f"stored {label}")
                existing = self._read_existing_bytes(target, f"stored {label}")
                if existing == data:
                    return False
                raise Mo2BootstrapStoreError(
                    f"stored {label} path contains different bytes: {target}"
                )
            promoted = True
            self._validate_existing_file(target, f"stored {label}")
            if self._read_existing_bytes(target, f"stored {label}") != data:
                raise Mo2BootstrapStoreError(
                    f"stored {label} differs after atomic promotion: {target}"
                )
            return True
        except Mo2BootstrapStoreError:
            raise
        except Exception as error:
            phase = "after promotion" if promoted else "before promotion"
            raise Mo2BootstrapStoreError(
                f"could not write bootstrap {label} {phase} at {target}: {error}"
            ) from error
        finally:
            self._cleanup_part(part)

    def _atomic_replace(
        self,
        target: Path,
        data: bytes,
        label: str,
        *,
        expected_current: bytes,
    ) -> None:
        part = self._stage_document(target, data, label)
        promoted = False
        try:
            self._assert_expected_current(target, expected_current)
            os.replace(part, target)
            promoted = True
            self._validate_existing_file(target, f"stored {label}")
            if self._read_existing_bytes(target, f"stored {label}") != data:
                raise Mo2BootstrapStoreError(
                    f"stored {label} differs after atomic promotion: {target}"
                )
        except Mo2BootstrapStoreError:
            raise
        except Exception as error:
            phase = "after promotion" if promoted else "before promotion"
            raise Mo2BootstrapStoreError(
                f"could not write bootstrap {label} {phase} at {target}: {error}"
            ) from error
        finally:
            self._cleanup_part(part)

    def _stage_document(self, target: Path, data: bytes, label: str) -> Path:
        self._validate_target(target)
        self._prepare_directory(self.layout.mo2_bootstrap_jobs)
        self._prepare_directory(target.parent)
        part = self.layout.mo2_bootstrap_jobs / (
            f"{label}-{uuid.uuid4().hex}.part"
        )
        try:
            with part.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            self._validate_existing_file(part, "bootstrap staging document")
            return part
        except Mo2BootstrapStoreError:
            self._cleanup_part(part)
            raise
        except Exception as error:
            self._cleanup_part(part)
            raise Mo2BootstrapStoreError(
                f"could not stage bootstrap {label} before promotion to {target}: {error}"
            ) from error

    @staticmethod
    def _cleanup_part(part: Path) -> None:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass

    def _assert_expected_current(self, target: Path, expected: bytes) -> None:
        metadata = self._lstat_if_exists(target)
        if metadata is None:
            raise Mo2BootstrapStoreError(
                f"bootstrap journal changed after observation: {target} is missing"
            )
        self._validate_existing_file(target, "bootstrap write target")
        if self._read_existing_bytes(target, "bootstrap write target") != expected:
            raise Mo2BootstrapStoreError(
                "bootstrap journal changed after observation"
            )

    def _validate_transition(
        self,
        current: BootstrapJournal,
        replacement: BootstrapJournal,
    ) -> None:
        for name in _IMMUTABLE_JOURNAL_FIELDS:
            if getattr(current, name) != getattr(replacement, name):
                raise Mo2BootstrapStoreError(
                    f"immutable journal field changed during transition: {name}"
                )
        if replacement.updated_at < current.updated_at:
            raise Mo2BootstrapStoreError(
                "journal updated timestamp moved backwards"
            )
        transitions = (
            _CREATE_TRANSITIONS
            if current.disposition is BootstrapDisposition.CREATE
            else _ADOPT_TRANSITIONS
        )
        allowed = set(transitions.get(current.state, set()))
        failure_sources = (
            _CREATE_FAILURE_SOURCES
            if current.disposition is BootstrapDisposition.CREATE
            else _ADOPT_FAILURE_SOURCES
        )
        if current.state in failure_sources:
            allowed.add(BootstrapJobState.RECOVERY_REQUIRED)
        if replacement.state not in allowed:
            raise Mo2BootstrapStoreError(
                "bootstrap journal transition is not allowed: "
                f"{current.state.value} -> {replacement.state.value}"
            )
        self._require_inventory_not_changed(
            current.stage_inventory_sha256,
            current.stage_entry_count,
            replacement.stage_inventory_sha256,
            replacement.stage_entry_count,
            "stage",
        )
        self._require_inventory_not_changed(
            current.activated_inventory_sha256,
            current.activated_entry_count,
            replacement.activated_inventory_sha256,
            replacement.activated_entry_count,
            "activated",
        )
        if replacement.state is BootstrapJobState.VERIFIED:
            if replacement.receipt_id is None:
                raise Mo2BootstrapStoreError(
                    "Verified journal transition requires a receipt ID"
                )
            receipt = self.load_receipt(replacement.receipt_id).receipt
            plan = self.load_plan(replacement.plan_id).plan
            if (
                receipt.job_id != replacement.job_id
                or receipt.plan_id != replacement.plan_id
            ):
                raise Mo2BootstrapStoreError(
                    "verified receipt does not belong to this bootstrap job and plan"
                )
            if not self._receipt_matches(
                receipt,
                BootstrapReceiptMatch.from_plan(plan),
            ):
                raise Mo2BootstrapStoreError(
                    "verified receipt conflicts with its retained plan evidence"
                )
            expected_mode = (
                BootstrapReceiptMode.CREATED
                if replacement.disposition is BootstrapDisposition.CREATE
                else BootstrapReceiptMode.ADOPTED
            )
            if receipt.mode is not expected_mode:
                raise Mo2BootstrapStoreError(
                    "verified receipt mode does not match the bootstrap disposition"
                )

    @contextmanager
    def _document_lock(self, target: Path):
        identity = hashlib.sha256(
            (str(self.layout.root).casefold() + "\0" + str(target).casefold()).encode(
                "utf-8"
            )
        ).hexdigest()
        if os.name == "nt":
            with _windows_named_mutex(identity):
                yield
            return

        # The MO2 bootstrap is Windows-only. This fallback keeps unit tests and
        # read-only tooling deterministic on other hosts without pretending to
        # provide cross-process guarantees there.
        with _PROCESS_LOCKS_GUARD:
            lock = _PROCESS_LOCKS.setdefault(identity, threading.Lock())
        if not lock.acquire(blocking=False):
            raise Mo2BootstrapStoreError(
                f"bootstrap document is already being changed: {target}"
            )
        try:
            yield
        finally:
            lock.release()

    def _require_verified_receipt(self, receipt: BootstrapReceipt) -> None:
        try:
            job = self.load_job(receipt.job_id).journal
            plan = self.load_plan(receipt.plan_id).plan
        except Mo2BootstrapNotFoundError as error:
            raise Mo2BootstrapStoreError(
                "matching bootstrap receipt is not backed by retained plan and job evidence"
            ) from error
        if (
            job.state is not BootstrapJobState.VERIFIED
            or job.receipt_id != receipt.receipt_id
            or job.plan_id != receipt.plan_id
        ):
            raise Mo2BootstrapStoreError(
                "matching bootstrap receipt is not backed by a Verified job journal"
            )
        expected_mode = (
            BootstrapReceiptMode.CREATED
            if job.disposition is BootstrapDisposition.CREATE
            else BootstrapReceiptMode.ADOPTED
        )
        if (
            receipt.mode is not expected_mode
            or not self._receipt_matches(
                receipt,
                BootstrapReceiptMatch.from_plan(plan),
            )
        ):
            raise Mo2BootstrapStoreError(
                "matching bootstrap receipt conflicts with its retained plan or job"
            )

    @staticmethod
    def _require_inventory_not_changed(
        old_sha: str | None,
        old_count: int | None,
        new_sha: str | None,
        new_count: int | None,
        label: str,
    ) -> None:
        if old_sha is not None and (new_sha != old_sha or new_count != old_count):
            raise Mo2BootstrapStoreError(
                f"recorded {label} inventory changed during journal transition"
            )

    def _validate_plan_workspace(self, plan: BootstrapPlan) -> None:
        expected = (
            (plan.workspace_root, self.layout.root, "workspace root"),
            (plan.final_root, self.layout.skyrim_mo2, "final root"),
            (plan.staging_parent, self.layout.skyrim_mo2.parent, "staging parent"),
            (plan.target.root, self.layout.skyrim_mo2, "target root"),
        )
        for observed, required, label in expected:
            if not self._same_windows_path(observed, required):
                raise Mo2BootstrapStoreError(
                    f"plan {label} does not match this workspace"
                )

    def _journal_paths_match(self, journal: BootstrapJournal) -> bool:
        return (
            self._same_windows_path(journal.stage_root, self.stage_root(journal.job_id))
            and self._same_windows_path(journal.prior_root, self.prior_root(journal.job_id))
            and self._same_windows_path(journal.final_root, self.layout.skyrim_mo2)
        )

    @staticmethod
    def _receipt_matches(
        receipt: BootstrapReceipt,
        match: BootstrapReceiptMatch,
    ) -> bool:
        return (
            receipt.qualification == "VerifiedBootstrap"
            and receipt.release_id == match.release_id
            and receipt.release_descriptor_sha256
            == match.release_descriptor_sha256
            and receipt.archive_artifact_id == match.archive_artifact_id
            and receipt.archive_metadata_sha256 == match.archive_metadata_sha256
            and receipt.archive_sha256 == match.archive_sha256
            and receipt.archive_size == match.archive_size
            and Mo2BootstrapStore._same_file_identity(
                receipt.skyrim_executable,
                match.skyrim_executable,
            )
            and Mo2BootstrapStore._same_windows_path(
                receipt.final_root,
                match.final_root,
            )
        )

    @staticmethod
    def _same_file_identity(left: FileIdentity, right: FileIdentity) -> bool:
        return (
            Mo2BootstrapStore._same_windows_path(left.path, right.path)
            and left.sha256 == right.sha256
            and left.size == right.size
        )

    @staticmethod
    def _same_windows_path(left: str | Path, right: str | Path) -> bool:
        return str(PureWindowsPath(left)).casefold() == str(
            PureWindowsPath(right)
        ).casefold()

    @staticmethod
    def _identity_digest(value: str, pattern: re.Pattern[str], label: str) -> str:
        if not isinstance(value, str):
            raise Mo2BootstrapStoreError(f"{label} has an invalid format")
        match = pattern.fullmatch(value)
        if match is None:
            raise Mo2BootstrapStoreError(f"{label} has an invalid format")
        return match.group(1)

    @staticmethod
    def _serialize(serializer, value, label: str) -> bytes:
        try:
            return serializer(value)
        except BootstrapFormatError as error:
            raise Mo2BootstrapStoreError(
                f"invalid bootstrap {label}: {error}"
            ) from error

    @staticmethod
    def _parse(parser, data: bytes, label: str, path: Path):
        try:
            return parser(data)
        except (BootstrapFormatError, UnicodeError, ValueError) as error:
            raise Mo2BootstrapStoreError(
                f"cannot parse bootstrap {label} {path}: {error}"
            ) from error

    def _read_document(self, path: Path, label: str, identity: str) -> bytes:
        self._validate_target(path)
        if self._lstat_if_exists(path) is None:
            raise Mo2BootstrapNotFoundError(
                f"bootstrap {label} is not present for {identity}: {path}"
            )
        self._validate_existing_file(path, f"bootstrap {label}")
        return self._read_existing_bytes(path, f"bootstrap {label}")

    @staticmethod
    def _read_existing_bytes(path: Path, label: str) -> bytes:
        try:
            return path.read_bytes()
        except OSError as error:
            raise Mo2BootstrapStoreError(
                f"cannot read {label} {path}: {error}"
            ) from error

    def _prepare_directory(self, directory: Path) -> None:
        self._validate_workspace_identity()
        try:
            relative = directory.relative_to(self.layout.root)
        except ValueError as error:
            raise Mo2BootstrapStoreError(
                f"bootstrap directory is outside the workspace: {directory}"
            ) from error
        candidates = [self.layout.root]
        current = self.layout.root
        for part in relative.parts:
            current = current / part
            candidates.append(current)
        for candidate in candidates:
            metadata = self._lstat_if_exists(candidate)
            if metadata is None:
                try:
                    candidate.mkdir()
                except OSError as error:
                    raise Mo2BootstrapStoreError(
                        f"cannot create bootstrap directory {candidate}: {error}"
                    ) from error
            self._validate_existing_directory(candidate, "bootstrap directory")

    def _validate_target(self, target: Path) -> None:
        self._validate_workspace_identity()
        try:
            relative = target.relative_to(self.layout.root)
        except ValueError as error:
            raise Mo2BootstrapStoreError(
                f"bootstrap target is outside the workspace: {target}"
            ) from error
        if not relative.parts:
            raise Mo2BootstrapStoreError(
                "workspace root is not a bootstrap document target"
            )
        current = self.layout.root
        candidates = [current]
        for part in relative.parts:
            current = current / part
            candidates.append(current)
        for candidate in candidates:
            metadata = self._lstat_if_exists(candidate)
            if metadata is None:
                continue
            self._reject_redirect(candidate, metadata)
            if candidate != target and not stat.S_ISDIR(metadata.st_mode):
                raise Mo2BootstrapStoreError(
                    f"bootstrap ancestor is not a directory: {candidate}"
                )

    def _validate_workspace_identity(self) -> None:
        try:
            resolved = self.requested_root.resolve(strict=False)
        except OSError as error:
            raise Mo2BootstrapStoreError(
                f"cannot resolve workspace root {self.requested_root}: {error}"
            ) from error
        if resolved != self.layout.root:
            raise Mo2BootstrapStoreError(
                f"redirected workspace root is not allowed: {self.requested_root}"
            )
        candidates = [self.requested_root, *self.requested_root.parents]
        for candidate in candidates:
            metadata = self._lstat_if_exists(candidate)
            if metadata is None:
                continue
            self._reject_redirect(candidate, metadata)
            if not stat.S_ISDIR(metadata.st_mode):
                raise Mo2BootstrapStoreError(
                    f"workspace ancestor is not a directory: {candidate}"
                )

    def _validate_existing_file(self, path: Path, label: str) -> None:
        self._validate_target(path)
        metadata = self._lstat_if_exists(path)
        if metadata is None:
            raise Mo2BootstrapStoreError(f"{label} is missing: {path}")
        self._reject_redirect(path, metadata)
        if not stat.S_ISREG(metadata.st_mode):
            raise Mo2BootstrapStoreError(
                f"{label} must be a direct regular file: {path}"
            )

    def _validate_existing_directory(self, path: Path, label: str) -> None:
        if path != self.layout.root:
            self._validate_target(path / ".modlab-directory-check")
        else:
            self._validate_workspace_identity()
        metadata = self._lstat_if_exists(path)
        if metadata is None:
            raise Mo2BootstrapStoreError(f"{label} is missing: {path}")
        self._reject_redirect(path, metadata)
        if not stat.S_ISDIR(metadata.st_mode):
            raise Mo2BootstrapStoreError(
                f"{label} must be a direct directory: {path}"
            )

    @staticmethod
    def _lstat_if_exists(path: Path):
        try:
            return path.lstat()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise Mo2BootstrapStoreError(
                f"cannot inspect bootstrap path {path}: {error}"
            ) from error

    @staticmethod
    def _reject_redirect(path: Path, metadata) -> None:
        if path.is_symlink() or bool(
            getattr(metadata, "st_file_attributes", 0)
            & _FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise Mo2BootstrapStoreError(
                f"redirected bootstrap path is not allowed: {path}"
            )

    @staticmethod
    def _timestamp(value: datetime) -> str:
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise Mo2BootstrapStoreError(
                "bootstrap clock must return a timezone-aware datetime"
            )
        utc = value.astimezone(timezone.utc)
        return utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _remove_empty_directory(path: Path) -> None:
        try:
            path.rmdir()
        except OSError:
            pass


def _promote_no_replace(source: Path, target: Path) -> None:
    """Atomically move a staged file only when the destination is absent."""
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.MoveFileExW.argtypes = (
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
        )
        kernel32.MoveFileExW.restype = ctypes.c_int
        if kernel32.MoveFileExW(str(source), str(target), 0x00000008):
            return
        error = ctypes.get_last_error()
        if error in {80, 183}:
            raise FileExistsError(error, "destination already exists", str(target))
        raise OSError(error, "atomic no-replace promotion failed", str(target))

    os.link(source, target, follow_symlinks=False)
    try:
        source.unlink()
    except OSError as error:
        try:
            target.unlink()
        except OSError as rollback_error:
            raise OSError(
                "could not remove no-replace promotion alias or roll back target"
            ) from rollback_error
        raise OSError("could not finish no-replace promotion") from error


__all__ = [
    "BootstrapReceiptMatch",
    "Mo2BootstrapNotFoundError",
    "Mo2BootstrapStore",
    "Mo2BootstrapStoreError",
    "StoredBootstrapJournal",
    "StoredBootstrapPlan",
    "StoredBootstrapReceipt",
    "new_bootstrap_job_id",
    "utc_now",
]
