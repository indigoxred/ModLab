import ctypes
from ctypes import wintypes
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import warnings
from unittest import mock

from modlab.validation import windows_watch
from modlab.validation.mo2_containment_model import (
    ContainmentScenario,
    ProtectedState,
    TreeIdentity,
    WatchEvidenceCompletion,
    WatcherEvent,
)
from modlab.validation.mo2_containment_serialization import (
    watch_outcome_from_bytes,
    watch_outcome_id_for,
)
from modlab.validation.windows_junction import create_mod_projection
from modlab.validation.windows_watch import (
    WatchProtocolError,
    WatchReceipt,
    WatchRequest,
    WatchRoot,
    start_watch,
    stop_watch,
    run_watch_worker,
    watch_proves_unchanged,
    watch_receipt_from_files,
    watch_root,
)


@dataclass(frozen=True)
class CancelDrainProof:
    every_completion_observed: bool
    storage_reused_before_completion: bool


def run_cancel_completion_fixture(completion: str) -> tuple[CancelDrainProof, tuple[str, ...]]:
    root = mock.Mock(root_kind="SourceMods")
    journal = mock.Mock()
    errors: list[str] = []
    state = windows_watch._WatchState(
        root=root,
        logical_root_kinds=("SourceMods",),
        directory_path=Path(r"C:\watched"),
        label="cancel-fixture",
        directory_handle=101,
        expected_volume_serial=1,
        expected_file_id=2,
        event_handle=102,
        recursive=True,
        notify_filter=windows_watch._NOTIFY_FILTER,
        membership_name=None,
        journal=journal,
        stopping=threading.Event(),
        errors=errors,
        errors_lock=threading.Lock(),
        buffer=ctypes.create_string_buffer(64),
        overlapped=windows_watch._OVERLAPPED(),
        armed=threading.Event(),
        pending_lock=threading.Lock(),
        pending=True,
    )
    completion_observed = False
    storage_reused_before_completion = False

    def cancel_io(*_args: object) -> bool:
        if completion == "cancel-not-found-after-completion":
            ctypes.set_last_error(windows_watch._ERROR_NOT_FOUND)
            return False
        return True

    def get_result(
        _handle: object,
        _overlapped: object,
        transferred_pointer: object,
        _wait: object,
    ) -> bool:
        nonlocal completion_observed
        completion_observed = True
        if completion in {"operation-aborted", "wait-failed-then-aborted"}:
            ctypes.set_last_error(windows_watch._ERROR_OPERATION_ABORTED)
            return False
        if completion == "unexpected-error":
            ctypes.set_last_error(123)
            return False
        ctypes.cast(
            transferred_pointer,
            ctypes.POINTER(wintypes.DWORD),
        ).contents.value = 12
        return True

    def parse_final_data(_buffer: object, _byte_count: int):
        nonlocal storage_reused_before_completion
        if not completion_observed:
            storage_reused_before_completion = True
        return ((1, "final.txt"),)

    with mock.patch.object(
        windows_watch._kernel32,
        "CancelIoEx",
        side_effect=cancel_io,
    ), mock.patch.object(
        windows_watch._kernel32,
        "WaitForSingleObject",
        return_value=(
            windows_watch._WAIT_FAILED
            if completion == "wait-failed-then-aborted"
            else windows_watch._WAIT_OBJECT_0
        ),
    ), mock.patch.object(
        windows_watch._kernel32,
        "GetOverlappedResult",
        side_effect=get_result,
    ), mock.patch.object(
        windows_watch,
        "_parse_notification_buffer",
        side_effect=parse_final_data,
    ):
        windows_watch._cancel_and_drain(state)

    if state.pending or not completion_observed:
        storage_reused_before_completion = True
    return (
        CancelDrainProof(
            every_completion_observed=completion_observed and not state.pending,
            storage_reused_before_completion=storage_reused_before_completion,
        ),
        tuple(errors),
    )


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _protected_state(tag: str) -> ProtectedState:
    tree = TreeIdentity(tag * 64, 1, 1, 4)
    return ProtectedState(tree, tag * 64, tag * 64, tree, tree, tree)


def _publish_controller_pid_barrier(path: Path, worker_pid: int) -> None:
    payload = str(worker_pid).encode("ascii")
    windows_watch.publish_new_verified(path, payload, lambda data: int(data))


@unittest.skipUnless(os.name == "nt", "ReadDirectoryChangesW requires Windows")
class MutationWatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="modlab-containment-watch-")
        self.root = Path(self.temporary.name)
        self.watched = self.root / "watched"
        self.evidence = self.root / "evidence"
        self.watched.mkdir()
        self.evidence.mkdir()
        self.active_requests: list[Path] = []

    def tearDown(self) -> None:
        for request_path in self.active_requests:
            try:
                stop_watch(request_path)
            except (OSError, WatchProtocolError):
                pass
        self.temporary.cleanup()

    def _request(self, *roots: WatchRoot) -> WatchRequest:
        if not roots:
            physical = watch_root("SourceMods", self.watched)
            roots = tuple(replace(physical, root_kind=kind) for kind in windows_watch.ROOT_KINDS)
        return WatchRequest(
            request_id="watch-request:" + "a" * 64,
            session_id="watch-session:" + "b" * 64,
            run_id="containment-run:0123456789abcdef0123456789abcdef",
            scenario=ContainmentScenario.MERGE_EXISTING,
            evidence_root=self.evidence,
            stop_token_path=self.evidence / "stop.token",
            roots=tuple(roots),
        )

    def _start(self, *roots: WatchRoot) -> tuple[Path, int]:
        request = self._request(*roots)
        worker_pid = start_watch(request)
        request_path = self.evidence / "request.json"
        self.active_requests.append(request_path)
        return request_path, worker_pid

    def _start_case(self, case_name: str) -> tuple[Path, int]:
        case_root = self.root / case_name
        watched = case_root / "watched"
        evidence = case_root / "evidence"
        watched.mkdir(parents=True)
        evidence.mkdir()
        physical = watch_root("SourceMods", watched)
        request = WatchRequest(
            request_id="watch-request:" + hashlib.sha256(case_name.encode()).hexdigest(),
            session_id="watch-session:" + hashlib.sha256((case_name + "-session").encode()).hexdigest(),
            run_id=(
                "containment-run:"
                + hashlib.sha256((case_name + "-run").encode()).hexdigest()[:32]
            ),
            scenario=ContainmentScenario.MERGE_EXISTING,
            evidence_root=evidence,
            stop_token_path=evidence / "stop.token",
            roots=tuple(
                replace(physical, root_kind=kind)
                for kind in windows_watch.ROOT_KINDS
            ),
        )
        worker_pid = start_watch(request)
        request_path = evidence / "request.json"
        self.active_requests.append(request_path)
        return request_path, worker_pid

    def _external_controller_fixture(
        self,
        case_name: str,
    ) -> tuple[subprocess.Popen[bytes], Path, Path, int, int]:
        case_root = self.root / case_name
        evidence = case_root / "evidence"
        watched = case_root / "watched"
        barrier = case_root / "controller-ready.txt"
        evidence.mkdir(parents=True)
        watched.mkdir()
        controller_script = "\n".join(
            (
                "from dataclasses import replace",
                "from pathlib import Path",
                "import sys",
                "import time",
                "from modlab.validation.mo2_containment_model import ContainmentScenario",
                "from modlab.validation.windows_watch import ROOT_KINDS, WatchRequest, start_watch, watch_root",
                "from tests.test_mo2_containment_watch import _publish_controller_pid_barrier",
                "evidence, watched, barrier = map(Path, sys.argv[1:4])",
                "root = watch_root('SourceMods', watched)",
                "request = WatchRequest(",
                "    request_id='watch-request:' + '8' * 64,",
                "    session_id='watch-session:' + '9' * 64,",
                "    run_id='containment-run:0123456789abcdef0123456789abcdef',",
                "    scenario=ContainmentScenario.MERGE_EXISTING,",
                "    evidence_root=evidence,",
                "    stop_token_path=evidence / 'stop.token',",
                "    roots=tuple(replace(root, root_kind=kind) for kind in ROOT_KINDS),",
                ")",
                "worker_pid = start_watch(request)",
                "_publish_controller_pid_barrier(barrier, worker_pid)",
                "while True: time.sleep(1)",
            )
        )
        controller = subprocess.Popen(
            (
                sys.executable,
                "-B",
                "-c",
                controller_script,
                str(evidence),
                str(watched),
                str(barrier),
            ),
            cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 20.0
        while not barrier.exists() and time.monotonic() < deadline:
            if controller.poll() is not None:
                self.fail(f"external controller exited before ready: {controller.returncode}")
            time.sleep(0.02)
        self.assertTrue(barrier.is_file(), "external controller did not become ready")
        worker_pid = int(barrier.read_text(encoding="ascii"))
        worker_handle, _ = windows_watch._open_process_identity(worker_pid)
        return controller, evidence, evidence / "request.json", worker_pid, worker_handle

    def _finish_external_fixture(
        self,
        controller: subprocess.Popen[bytes],
        worker_pid: int,
        worker_handle: int,
    ) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateProcess.restype = wintypes.BOOL
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        if controller.poll() is None:
            controller.terminate()
            controller.wait(timeout=10)
        if kernel32.WaitForSingleObject(worker_handle, 10_000) != windows_watch._WAIT_OBJECT_0:
            terminate_handle = kernel32.OpenProcess(
                0x0001 | windows_watch._SYNCHRONIZE,
                False,
                worker_pid,
            )
            if terminate_handle:
                try:
                    kernel32.TerminateProcess(terminate_handle, 98)
                    kernel32.WaitForSingleObject(terminate_handle, 10_000)
                finally:
                    kernel32.CloseHandle(terminate_handle)
        self.assertIsNone(
            windows_watch._close_handle(worker_handle, "external fixture worker")
        )

    @staticmethod
    def _protocol_bytes(evidence: Path) -> dict[str, bytes]:
        return {
            path.name: path.read_bytes()
            for path in sorted(evidence.iterdir(), key=lambda item: item.name)
            if path.is_file()
        }

    def test_recursive_create_write_rename_and_delete_are_ordered(self):
        nested = self.watched / "nested"
        nested.mkdir()
        request_path, _ = self._start()
        original = nested / "probe.txt"
        renamed = nested / "renamed.txt"

        with original.open("wb") as stream:
            stream.write(b"one")
            stream.flush()
            os.fsync(stream.fileno())
        original.rename(renamed)
        renamed.unlink()
        time.sleep(0.2)
        receipt = stop_watch(request_path)

        self.assertTrue(receipt.complete, receipt.error)
        selected = tuple(
            event.action
            for event in receipt.events
            if event.root_kind == "SourceMods"
            and event.relative_path in {"nested/probe.txt", "nested/renamed.txt"}
        )
        self.assertEqual(
            ("Added", "Modified", "RenamedOld", "RenamedNew", "Removed"),
            selected,
        )
        self.assertEqual(tuple(range(1, len(receipt.events) + 1)), tuple(e.sequence for e in receipt.events))
        self.assertTrue(all(type(event) is WatcherEvent for event in receipt.events))

    def test_intentional_cancel_completes_without_an_overflow(self):
        request_path, worker_pid = self._start()
        self.assertIn(request_path.absolute(), windows_watch._LOCAL_SESSIONS)

        receipt = stop_watch(request_path)

        self.assertEqual(worker_pid, receipt.worker_pid)
        self.assertTrue(receipt.ready)
        self.assertTrue(receipt.complete, receipt.error)
        self.assertEqual((), receipt.events)
        self.assertIsNone(receipt.error)
        self.assertEqual(
            hashlib.sha256(request_path.read_bytes()).hexdigest(),
            receipt.request_bytes_sha256,
        )
        outcome = watch_outcome_from_bytes(
            (self.evidence / "outcome.json").read_bytes()
        )
        self.assertEqual(WatchEvidenceCompletion.COMPLETED, outcome.evidence_completion)
        self.assertEqual(0, outcome.worker_exit_code)
        self.assertEqual((), outcome.reason_codes)
        self.assertEqual(receipt.watch_outcome_id, watch_outcome_id_for(outcome))
        protected = _protected_state("1")
        self.assertTrue(watch_proves_unchanged(receipt, protected, protected))
        self.assertFalse(watch_proves_unchanged(receipt, "partial", "partial"))
        self.assertFalse(
            watch_proves_unchanged(
                replace(receipt, request_bytes_sha256="not-a-request-hash"),
                protected,
                protected,
            )
        )
        self.assertTrue((self.evidence / "controller-claim.json").is_file())
        self.assertTrue((self.evidence / "worker-launch.json").is_file())
        self.assertNotIn(request_path.absolute(), windows_watch._LOCAL_SESSIONS)

    def test_mutable_evidence_completion_helper_is_removed(self):
        self.assertFalse(hasattr(windows_watch, "_watch_receipt_from_files_impl"))

    def test_completed_outcome_reconstructs_without_evidence_reopen(self):
        request_path, worker_pid = self._start()
        expected = stop_watch(request_path)
        request = windows_watch._load_request_path(request_path)
        outcome_path = self.evidence / "outcome.json"
        real_read = windows_watch._read_exact_regular_file
        real_path_read_bytes = Path.read_bytes

        def read_outcome_only(path: Path, label: str, **kwargs: object) -> bytes:
            if Path(path) != outcome_path:
                self.fail(f"reconstruction reopened mutable authority: {label}")
            return real_read(path, label, **kwargs)

        def reject_mutable_path_read(path: Path) -> bytes:
            if path.name in {"ready.json", "terminal.json"}:
                self.fail(f"reconstruction reopened mutable authority: {path.name}")
            return real_path_read_bytes(path)

        with (
            mock.patch.object(
                windows_watch,
                "_read_exact_regular_file",
                side_effect=read_outcome_only,
            ),
            mock.patch.object(
                windows_watch,
                "_load_worker_launch",
                side_effect=AssertionError("reconstruction reopened worker launch"),
            ),
            mock.patch.object(
                windows_watch,
                "_read_exact_journal",
                side_effect=AssertionError("reconstruction reopened event journal"),
            ),
            mock.patch.object(
                windows_watch,
                "_process_alive",
                side_effect=AssertionError("reconstruction reopened worker process"),
            ),
            mock.patch.object(Path, "read_bytes", new=reject_mutable_path_read),
        ):
            receipt = watch_receipt_from_files(
                request,
                worker_pid,
                self.evidence / "ready.json",
                self.evidence / "events.ndjson",
                self.evidence / "terminal.json",
            )

        self.assertEqual(expected, receipt)

    def test_complete_mutation_evidence_is_not_unchanged(self):
        request_path, worker_pid = self._start()
        (self.watched / "mutation.txt").write_text("changed", encoding="utf-8")
        time.sleep(0.2)
        expected = stop_watch(request_path)
        request = windows_watch._load_request_path(request_path)
        (self.evidence / "events.ndjson").write_bytes(b"")
        (self.evidence / "terminal.json").write_bytes(b"not a terminal record\n")

        receipt = watch_receipt_from_files(
            request,
            worker_pid,
            self.evidence / "ready.json",
            self.evidence / "events.ndjson",
            self.evidence / "terminal.json",
        )

        self.assertEqual(expected, receipt)
        self.assertTrue(receipt.complete, receipt.error)
        self.assertTrue(receipt.events)
        state = _protected_state("1")
        self.assertFalse(watch_proves_unchanged(receipt, state, state))

    def test_incomplete_outcome_cannot_be_promoted_by_terminal_files(self):
        request_path, worker_pid = self._start_case("incomplete-outcome-reconstruction")
        with mock.patch.object(windows_watch, "_get_process_exit_code", return_value=73):
            expected = stop_watch(request_path)
        request = windows_watch._load_request_path(request_path)

        receipt = watch_receipt_from_files(
            request,
            worker_pid,
            request.evidence_root / "ready.json",
            request.evidence_root / "events.ndjson",
            request.evidence_root / "terminal.json",
        )

        self.assertEqual(expected, receipt)
        self.assertFalse(receipt.complete)
        self.assertEqual(73, receipt.worker_exit_code)

    def test_noncanonical_or_unbound_outcome_returns_incomplete(self):
        request_path, worker_pid = self._start_case("invalid-outcome-reconstruction")
        self.assertTrue(stop_watch(request_path).complete)
        request = windows_watch._load_request_path(request_path)
        outcome_path = request.evidence_root / "outcome.json"
        canonical = outcome_path.read_bytes()

        for label, outcome_bytes, supplied_worker_pid, expected_error in (
            (
                "malformed",
                b"not an outcome\n",
                worker_pid,
                "watch outcome reconstruction failed",
            ),
            (
                "noncanonical",
                json.dumps(json.loads(canonical), indent=2).encode("utf-8"),
                worker_pid,
                "watch outcome canonical bytes mismatch",
            ),
            (
                "worker-mismatch",
                canonical,
                worker_pid + 1,
                "watch outcome binding mismatch",
            ),
        ):
            with self.subTest(label=label):
                outcome_path.write_bytes(outcome_bytes)
                receipt = watch_receipt_from_files(
                    request,
                    supplied_worker_pid,
                    request.evidence_root / "ready.json",
                    request.evidence_root / "events.ndjson",
                    request.evidence_root / "terminal.json",
                )

                self.assertFalse(receipt.complete)
                self.assertIn(expected_error, receipt.error)

        outcome_path.write_bytes(canonical)

    def test_outcome_reporting_close_warning_does_not_reverse_completion(self):
        request_path, worker_pid = self._start_case("outcome-reporting-close-warning")
        expected = stop_watch(request_path)
        request = windows_watch._load_request_path(request_path)
        real_close = windows_watch._close_handle

        def reporting_close_warning(handle: int, label: str) -> str | None:
            if label == "watch outcome readback":
                self.assertIsNone(real_close(handle, label))
                return "injected outcome reporting close warning"
            return real_close(handle, label)

        with self.assertWarnsRegex(RuntimeWarning, "outcome reporting close warning"):
            with mock.patch.object(
                windows_watch,
                "_close_handle",
                side_effect=reporting_close_warning,
            ):
                receipt = watch_receipt_from_files(
                    request,
                    worker_pid,
                    request.evidence_root / "ready.json",
                    request.evidence_root / "events.ndjson",
                    request.evidence_root / "terminal.json",
                )

        self.assertEqual(expected, receipt)

    def test_outcome_reporting_warning_filter_preserves_captured_outcomes(self):
        completed_path, completed_pid = self._start_case("reporting-filter-completed")
        completed = stop_watch(completed_path)
        incomplete_path, incomplete_pid = self._start_case("reporting-filter-incomplete")
        with mock.patch.object(windows_watch, "_get_process_exit_code", return_value=73):
            incomplete = stop_watch(incomplete_path)

        for request_path, worker_pid, expected in (
            (completed_path, completed_pid, completed),
            (incomplete_path, incomplete_pid, incomplete),
        ):
            with self.subTest(completion=expected.evidence_completion.value):
                request = windows_watch._load_request_path(request_path)
                real_close = windows_watch._close_handle

                def reporting_close_warning(handle: int, label: str) -> str | None:
                    if label == "watch outcome readback":
                        self.assertIsNone(real_close(handle, label))
                        return "injected outcome reporting close warning"
                    return real_close(handle, label)

                with warnings.catch_warnings():
                    warnings.simplefilter("error", RuntimeWarning)
                    with mock.patch.object(
                        windows_watch,
                        "_close_handle",
                        side_effect=reporting_close_warning,
                    ):
                        receipt = watch_receipt_from_files(
                            request,
                            worker_pid,
                            request.evidence_root / "ready.json",
                            request.evidence_root / "events.ndjson",
                            request.evidence_root / "terminal.json",
                        )

                self.assertEqual(expected, receipt)

    def test_start_publishes_command_bound_claim_before_exact_spawn(self):
        request = self._request()
        request_path = self.evidence / "request.json"
        claim_path = self.evidence / "controller-claim.json"
        self.active_requests.append(request_path)
        real_popen = subprocess.Popen
        observed_spawn = False

        def inspect_spawn(command, **kwargs):
            nonlocal observed_spawn
            observed_spawn = True
            self.assertTrue(claim_path.is_file(), "claim was not published before spawn")
            claim = windows_watch.controller_claim_from_bytes(
                claim_path.read_bytes(),
                windows_watch._load_request_path(request_path),
            )
            self.assertEqual(
                request_path.resolve(strict=True),
                getattr(claim, "request_path", None),
            )
            self.assertEqual(tuple(command), getattr(claim, "worker_command", ()))
            self.assertIs(kwargs.get("shell"), False)
            return real_popen(command, **kwargs)

        with mock.patch.object(
            windows_watch.subprocess,
            "Popen",
            side_effect=inspect_spawn,
        ):
            worker_pid = start_watch(request)

        self.assertTrue(observed_spawn)
        self.assertEqual(
            worker_pid,
            windows_watch._LOCAL_SESSIONS[request_path.absolute()].worker_pid,
        )

    def test_launch_publication_failure_uses_owned_incomplete_cleanup(self):
        request = self._request()
        request_path = self.evidence / "request.json"
        real_publish = windows_watch.publish_new_verified
        real_popen = subprocess.Popen
        launched: list[subprocess.Popen[bytes]] = []

        def capture_launch(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            launched.append(process)
            return process

        def fail_launch_record(path: Path, data: bytes, parse):
            if path.name == "worker-launch.json":
                raise OSError("injected launch publication failure")
            return real_publish(path, data, parse)

        try:
            with (
                mock.patch.object(
                    windows_watch.subprocess,
                    "Popen",
                    side_effect=capture_launch,
                ),
                mock.patch.object(
                    windows_watch,
                    "publish_new_verified",
                    side_effect=fail_launch_record,
                ),
            ):
                with self.assertRaisesRegex(
                    WatchProtocolError,
                    "identity/launch publication failed",
                ):
                    start_watch(request)

            self.assertEqual(1, len(launched))
            self.assertTrue((self.evidence / "outcome.json").is_file())
            outcome = watch_outcome_from_bytes(
                (self.evidence / "outcome.json").read_bytes()
            )
            self.assertEqual(
                WatchEvidenceCompletion.INCOMPLETE,
                outcome.evidence_completion,
            )
            self.assertIn("worker-launch-publication-failed", outcome.reason_codes)
            self.assertTrue((self.evidence / "stop.token").is_file())
            self.assertNotIn(request_path.absolute(), windows_watch._LOCAL_SESSIONS)
            with self.assertRaisesRegex(WatchProtocolError, "unavailable"):
                windows_watch._popen_process_handle(launched[0])
        finally:
            for process in launched:
                try:
                    windows_watch._popen_process_handle(process)
                except WatchProtocolError:
                    continue
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
                windows_watch._close_popen_process_handle(process)

    def test_startup_ready_exact_read_failure_aborts_incomplete(self):
        request = self._request()
        request_path = self.evidence / "request.json"
        self.active_requests.append(request_path)
        real_read = windows_watch._read_exact_regular_file

        def fail_ready(path: Path, label: str) -> bytes:
            if path.name == "ready.json":
                raise OSError("injected startup ready exact-read failure")
            return real_read(path, label)

        try:
            with mock.patch.object(
                windows_watch,
                "_read_exact_regular_file",
                side_effect=fail_ready,
            ):
                with self.assertRaisesRegex(
                    WatchProtocolError,
                    "startup ready exact-read failure",
                ):
                    start_watch(request)

            outcome = watch_outcome_from_bytes(
                (self.evidence / "outcome.json").read_bytes()
            )
            self.assertEqual(
                WatchEvidenceCompletion.INCOMPLETE,
                outcome.evidence_completion,
            )
            self.assertIn("watch-startup-failed", outcome.reason_codes)
            self.assertNotIn(request_path.absolute(), windows_watch._LOCAL_SESSIONS)
        finally:
            if request_path.absolute() in windows_watch._LOCAL_SESSIONS:
                stop_watch(request_path)

    def test_startup_retries_transient_ready_sharing_error_for_exact_live_worker(self):
        request = self._request()
        request_path = self.evidence / "request.json"
        self.active_requests.append(request_path)
        real_read = windows_watch._read_exact_regular_file
        ready_reads = 0

        def share_locked_once(path: Path, label: str) -> bytes:
            nonlocal ready_reads
            if path.name == "ready.json":
                ready_reads += 1
                if ready_reads == 1:
                    raise OSError(
                        32,
                        "injected transient ready sharing violation",
                        str(path),
                    )
            return real_read(path, label)

        with mock.patch.object(
            windows_watch,
            "_read_exact_regular_file",
            side_effect=share_locked_once,
        ):
            worker_pid = start_watch(request)

        session = windows_watch._LOCAL_SESSIONS[request_path.absolute()]
        self.assertEqual(worker_pid, session.worker_pid)
        process_handle, creation_time = windows_watch._open_process_identity(worker_pid)
        try:
            self.assertEqual(session.worker_creation_time, creation_time)
            self.assertEqual(
                windows_watch._WAIT_TIMEOUT,
                windows_watch._kernel32.WaitForSingleObject(process_handle, 0),
            )
        finally:
            self.assertIsNone(
                windows_watch._close_handle(process_handle, "transient-ready test worker")
            )
        self.assertGreaterEqual(ready_reads, 2)

    def test_startup_retries_exact_ready_not_found_for_exact_live_worker(self):
        request = self._request()
        request_path = self.evidence / "request.json"
        self.active_requests.append(request_path)
        real_read = windows_watch._read_exact_regular_file
        ready_reads = 0

        def missing_once(path: Path, label: str, **kwargs: object) -> bytes:
            nonlocal ready_reads
            if Path(path).name == "ready.json":
                ready_reads += 1
                if ready_reads == 1:
                    raise FileNotFoundError(
                        2,
                        "injected exact ready not found",
                        str(path),
                    )
            return real_read(path, label, **kwargs)

        with mock.patch.object(
            windows_watch,
            "_read_exact_regular_file",
            side_effect=missing_once,
        ):
            worker_pid = start_watch(request)

        self.assertEqual(
            worker_pid,
            windows_watch._LOCAL_SESSIONS[request_path.absolute()].worker_pid,
        )
        self.assertGreaterEqual(ready_reads, 2)

    def test_startup_not_found_aborts_when_worker_wait_is_uncertain(self):
        for index, wait_result in enumerate(
            (windows_watch._WAIT_FAILED, 7),
        ):
            with self.subTest(wait_result=wait_result):
                case_root = self.root / f"ready-not-found-wait-{index}"
                watched = case_root / "watched"
                evidence = case_root / "evidence"
                watched.mkdir(parents=True)
                evidence.mkdir()
                physical = watch_root("SourceMods", watched)
                request = WatchRequest(
                    request_id="watch-request:" + f"{index + 1:x}" * 64,
                    session_id="watch-session:" + f"{index + 3:x}" * 64,
                    run_id="containment-run:" + f"{index + 5:x}" * 32,
                    scenario=ContainmentScenario.MERGE_EXISTING,
                    evidence_root=evidence,
                    stop_token_path=evidence / "stop.token",
                    roots=tuple(
                        replace(physical, root_kind=kind)
                        for kind in windows_watch.ROOT_KINDS
                    ),
                )
                request_path = evidence / "request.json"
                self.active_requests.append(request_path)
                real_read = windows_watch._read_exact_regular_file
                real_wait = windows_watch._kernel32.WaitForSingleObject
                missing_injected = False
                wait_injected = False

                def missing_once(
                    path: Path,
                    label: str,
                    **kwargs: object,
                ) -> bytes:
                    nonlocal missing_injected
                    if Path(path).name == "ready.json" and not missing_injected:
                        missing_injected = True
                        raise FileNotFoundError(
                            2,
                            "injected exact ready not found",
                            str(path),
                        )
                    return real_read(path, label, **kwargs)

                def uncertain_once(handle: int, timeout: int) -> int:
                    nonlocal wait_injected
                    if timeout == 0 and not wait_injected:
                        wait_injected = True
                        return wait_result
                    return real_wait(handle, timeout)

                with (
                    mock.patch.object(
                        windows_watch,
                        "_read_exact_regular_file",
                        side_effect=missing_once,
                    ),
                    mock.patch.object(
                        windows_watch._kernel32,
                        "WaitForSingleObject",
                        side_effect=uncertain_once,
                    ),
                ):
                    with self.assertRaisesRegex(
                        WatchProtocolError,
                        "ready.*liveness wait failed",
                    ):
                        start_watch(request)

                self.assertTrue(missing_injected)
                self.assertTrue(wait_injected)
                outcome = watch_outcome_from_bytes(
                    (evidence / "outcome.json").read_bytes()
                )
                self.assertEqual(
                    WatchEvidenceCompletion.INCOMPLETE,
                    outcome.evidence_completion,
                )

    def test_startup_lock_retry_aborts_when_worker_wait_is_uncertain(self):
        for index, (error_code, wait_result) in enumerate(
            (
                (windows_watch._ERROR_SHARING_VIOLATION, windows_watch._WAIT_FAILED),
                (windows_watch._ERROR_LOCK_VIOLATION, 7),
            )
        ):
            with self.subTest(error_code=error_code, wait_result=wait_result):
                case_root = self.root / f"ready-lock-wait-{index}"
                watched = case_root / "watched"
                evidence = case_root / "evidence"
                watched.mkdir(parents=True)
                evidence.mkdir()
                physical = watch_root("SourceMods", watched)
                request = WatchRequest(
                    request_id="watch-request:" + f"{index + 7:x}" * 64,
                    session_id="watch-session:" + f"{index + 9:x}" * 64,
                    run_id="containment-run:" + f"{index + 11:x}" * 32,
                    scenario=ContainmentScenario.MERGE_EXISTING,
                    evidence_root=evidence,
                    stop_token_path=evidence / "stop.token",
                    roots=tuple(
                        replace(physical, root_kind=kind)
                        for kind in windows_watch.ROOT_KINDS
                    ),
                )
                request_path = evidence / "request.json"
                self.active_requests.append(request_path)
                real_read = windows_watch._read_exact_regular_file
                real_wait = windows_watch._kernel32.WaitForSingleObject
                lock_injected = False
                wait_injected = False

                def locked_once(
                    path: Path,
                    label: str,
                    **kwargs: object,
                ) -> bytes:
                    nonlocal lock_injected
                    if Path(path).name == "ready.json" and not lock_injected:
                        lock_injected = True
                        raise OSError(
                            error_code,
                            "injected exact ready lock",
                            str(path),
                        )
                    return real_read(path, label, **kwargs)

                def uncertain_once(handle: int, timeout: int) -> int:
                    nonlocal wait_injected
                    if timeout == 0 and not wait_injected:
                        wait_injected = True
                        return wait_result
                    return real_wait(handle, timeout)

                with (
                    mock.patch.object(
                        windows_watch,
                        "_read_exact_regular_file",
                        side_effect=locked_once,
                    ),
                    mock.patch.object(
                        windows_watch._kernel32,
                        "WaitForSingleObject",
                        side_effect=uncertain_once,
                    ),
                ):
                    with self.assertRaisesRegex(
                        WatchProtocolError,
                        "ready.*liveness wait failed",
                    ):
                        start_watch(request)

                self.assertTrue(lock_injected)
                self.assertTrue(wait_injected)
                outcome = watch_outcome_from_bytes(
                    (evidence / "outcome.json").read_bytes()
                )
                self.assertEqual(
                    WatchEvidenceCompletion.INCOMPLETE,
                    outcome.evidence_completion,
                )

    def test_same_controller_requires_exact_zero_exit_before_completion(self):
        request_path, _ = self._start()

        with mock.patch.object(
            windows_watch,
            "_get_process_exit_code",
            return_value=73,
            create=True,
        ):
            receipt = stop_watch(request_path)

        self.assertFalse(receipt.complete)
        outcome = watch_outcome_from_bytes(
            (self.evidence / "outcome.json").read_bytes()
        )
        self.assertEqual(WatchEvidenceCompletion.INCOMPLETE, outcome.evidence_completion)
        self.assertEqual(73, outcome.worker_exit_code)

    def test_same_controller_failure_matrix_is_permanently_incomplete(self):
        for fault in (
            "wait-timeout",
            "wait-failure",
            "exit-read-failure",
            "still-active",
            "post-signal-identity-mismatch",
            "evidence-capture-failure",
            "process-handle-close-failure",
        ):
            with self.subTest(fault=fault):
                request_path, _ = self._start_case(fault)
                session = windows_watch._LOCAL_SESSIONS[request_path.absolute()]
                patcher = None
                if fault in {"wait-timeout", "wait-failure"}:
                    real_wait = windows_watch._kernel32.WaitForSingleObject

                    def fail_wait(handle: int, timeout: int) -> int:
                        if handle == int(session.process._handle) and timeout == 15_000:
                            return (
                                windows_watch._WAIT_TIMEOUT
                                if fault == "wait-timeout"
                                else windows_watch._WAIT_FAILED
                            )
                        return real_wait(handle, timeout)

                    patcher = mock.patch.object(
                        windows_watch._kernel32,
                        "WaitForSingleObject",
                        side_effect=fail_wait,
                    )
                elif fault == "exit-read-failure":
                    patcher = mock.patch.object(
                        windows_watch,
                        "_get_process_exit_code",
                        side_effect=OSError("injected exit-code read failure"),
                    )
                elif fault == "still-active":
                    patcher = mock.patch.object(
                        windows_watch,
                        "_get_process_exit_code",
                        return_value=windows_watch._STILL_ACTIVE,
                    )
                elif fault == "post-signal-identity-mismatch":
                    real_verify = windows_watch._verify_retained_process_handle
                    inspections = 0

                    def mismatch_after_signal(handle: int, pid: int, creation: int) -> None:
                        nonlocal inspections
                        inspections += 1
                        if inspections == 2:
                            raise WatchProtocolError("injected post-signal mismatch")
                        real_verify(handle, pid, creation)

                    patcher = mock.patch.object(
                        windows_watch,
                        "_verify_retained_process_handle",
                        side_effect=mismatch_after_signal,
                    )
                elif fault == "evidence-capture-failure":
                    patcher = mock.patch.object(
                        windows_watch,
                        "_capture_worker_evidence",
                        side_effect=WatchProtocolError("injected evidence capture failure"),
                    )
                else:
                    patcher = mock.patch.object(
                        windows_watch,
                        "_close_popen_process_handle",
                        return_value="unclosed handle: injected controller process handle",
                    )

                with patcher:
                    receipt = stop_watch(request_path)

                self.assertFalse(receipt.complete)
                outcome = watch_outcome_from_bytes(
                    (request_path.parent / "outcome.json").read_bytes()
                )
                self.assertEqual(
                    WatchEvidenceCompletion.INCOMPLETE,
                    outcome.evidence_completion,
                )
                if request_path.absolute() in windows_watch._LOCAL_SESSIONS:
                    stop_watch(request_path)

    def test_same_controller_protocol_uncertainty_permanently_poisons_session(self):
        request_path, _ = self._start_case("same-controller-protocol-poison")

        with mock.patch.object(
            windows_watch,
            "_load_controller_claim",
            side_effect=OSError("injected transient claim read failure"),
        ):
            first = stop_watch(request_path)

        second = stop_watch(request_path)
        outcome = watch_outcome_from_bytes(
            (request_path.parent / "outcome.json").read_bytes()
        )
        self.assertFalse(first.complete)
        self.assertFalse(second.complete)
        self.assertEqual(first.watch_outcome_id, second.watch_outcome_id)
        self.assertEqual(WatchEvidenceCompletion.INCOMPLETE, outcome.evidence_completion)
        self.assertIn("same-controller-protocol-uncertain", outcome.reason_codes)

    def test_authority_file_exact_read_failure_prevents_completion(self):
        for authority_name, expected_reason in (
            ("ready.json", "worker-ready-invalid"),
            ("terminal.json", "worker-evidence-invalid"),
        ):
            with self.subTest(authority_name=authority_name):
                request_path, _ = self._start_case(
                    f"exact-read-failure-{authority_name.removesuffix('.json')}"
                )
                real_read = windows_watch._read_exact_regular_file

                def fail_authority(path: Path, label: str) -> bytes:
                    if path.name == authority_name:
                        raise WatchProtocolError(
                            f"injected exact {authority_name} read failure"
                        )
                    return real_read(path, label)

                with mock.patch.object(
                    windows_watch,
                    "_read_exact_regular_file",
                    side_effect=fail_authority,
                ):
                    receipt = stop_watch(request_path)

                outcome = watch_outcome_from_bytes(
                    (request_path.parent / "outcome.json").read_bytes()
                )
                self.assertFalse(receipt.complete)
                self.assertEqual(
                    WatchEvidenceCompletion.INCOMPLETE,
                    outcome.evidence_completion,
                )
                self.assertIn(expected_reason, outcome.reason_codes)

    def test_authority_file_handle_close_failure_prevents_completion(self):
        for authority_name, close_label in (
            ("ready.json", "ready record readback"),
            ("terminal.json", "terminal record readback"),
        ):
            with self.subTest(authority_name=authority_name):
                request_path, _ = self._start_case(
                    f"exact-close-failure-{authority_name.removesuffix('.json')}"
                )
                real_close = windows_watch._close_handle
                injected = False

                def fail_first_close(handle: int, label: str) -> str | None:
                    nonlocal injected
                    if label == close_label:
                        if not injected:
                            self.assertIsNone(real_close(handle, label))
                            injected = True
                            return f"unclosed handle: injected {close_label}"
                        return None
                    return real_close(handle, label)

                with mock.patch.object(
                    windows_watch,
                    "_close_handle",
                    side_effect=fail_first_close,
                ):
                    receipt = stop_watch(request_path)

                outcome = watch_outcome_from_bytes(
                    (request_path.parent / "outcome.json").read_bytes()
                )
                self.assertTrue(injected)
                self.assertFalse(receipt.complete)
                self.assertEqual(
                    WatchEvidenceCompletion.INCOMPLETE,
                    outcome.evidence_completion,
                )
                self.assertIn("evidence-handle-close-failed", outcome.reason_codes)

    def test_completed_outcome_publication_failure_falls_back_to_incomplete(self):
        request_path, _ = self._start_case("outcome-publication-failure")
        outcome_attempts = 0

        def fail_first_outcome(source: Path, destination: Path) -> None:
            nonlocal outcome_attempts
            outcome_attempts += 1
            if outcome_attempts == 1:
                raise OSError("injected outcome publication failure")
            os.rename(source, destination)

        with mock.patch.object(
            windows_watch,
            "_promote_outcome_candidate",
            side_effect=fail_first_outcome,
            create=True,
        ):
            receipt = stop_watch(request_path)

        self.assertFalse(receipt.complete)
        outcome = watch_outcome_from_bytes(
            (request_path.parent / "outcome.json").read_bytes()
        )
        self.assertEqual(2, outcome_attempts)
        self.assertEqual(WatchEvidenceCompletion.INCOMPLETE, outcome.evidence_completion)
        self.assertIn("outcome-publication-failed", outcome.reason_codes)

    def test_outcome_candidate_readback_failure_is_permanently_incomplete(self):
        request_path, _ = self._start_case("outcome-candidate-readback-failure")
        candidate_reads = 0

        def fail_first_outcome_candidate(path: Path, label: str) -> bytes:
            nonlocal candidate_reads
            if path.name.startswith(".outcome.json."):
                candidate_reads += 1
                if candidate_reads == 1:
                    raise OSError("injected private outcome readback failure")
            return path.read_bytes()

        with mock.patch.object(
            windows_watch,
            "_read_exact_regular_file",
            side_effect=fail_first_outcome_candidate,
            create=True,
        ):
            first = stop_watch(request_path)

        second = stop_watch(request_path)
        outcome = watch_outcome_from_bytes(
            (request_path.parent / "outcome.json").read_bytes()
        )
        self.assertEqual(2, candidate_reads)
        self.assertFalse(first.complete)
        self.assertFalse(second.complete)
        self.assertEqual(first.watch_outcome_id, second.watch_outcome_id)
        self.assertEqual(WatchEvidenceCompletion.INCOMPLETE, outcome.evidence_completion)
        self.assertIn("outcome-publication-failed", outcome.reason_codes)

    def test_outcome_candidate_write_and_flush_failures_are_incomplete(self):
        for fault in ("write", "flush"):
            with self.subTest(fault=fault):
                request_path, _ = self._start_case(f"outcome-candidate-{fault}")
                real_open = windows_watch.os.open
                real_write = windows_watch.os.write
                real_fsync = windows_watch.os.fsync
                candidate_descriptors: set[int] = set()
                injected = False

                def track_candidate(path, flags, mode=0o777):
                    descriptor = real_open(path, flags, mode)
                    if Path(path).name.startswith(".outcome.json."):
                        candidate_descriptors.add(descriptor)
                    return descriptor

                def fail_candidate_write(descriptor: int, data) -> int:
                    nonlocal injected
                    if (
                        fault == "write"
                        and descriptor in candidate_descriptors
                        and not injected
                    ):
                        injected = True
                        raise OSError("injected outcome candidate write failure")
                    return real_write(descriptor, data)

                def fail_candidate_flush(descriptor: int) -> None:
                    nonlocal injected
                    if (
                        fault == "flush"
                        and descriptor in candidate_descriptors
                        and not injected
                    ):
                        injected = True
                        raise OSError("injected outcome candidate flush failure")
                    real_fsync(descriptor)

                with (
                    mock.patch.object(
                        windows_watch.os,
                        "open",
                        side_effect=track_candidate,
                    ),
                    mock.patch.object(
                        windows_watch.os,
                        "write",
                        side_effect=fail_candidate_write,
                    ),
                    mock.patch.object(
                        windows_watch.os,
                        "fsync",
                        side_effect=fail_candidate_flush,
                    ),
                ):
                    first = stop_watch(request_path)

                second = stop_watch(request_path)
                outcome = watch_outcome_from_bytes(
                    (request_path.parent / "outcome.json").read_bytes()
                )
                self.assertTrue(injected)
                self.assertFalse(first.complete)
                self.assertFalse(second.complete)
                self.assertEqual(first.watch_outcome_id, second.watch_outcome_id)
                self.assertEqual(
                    WatchEvidenceCompletion.INCOMPLETE,
                    outcome.evidence_completion,
                )
                self.assertIn("outcome-publication-failed", outcome.reason_codes)

    def test_non_owner_refuses_without_mutation_while_controller_is_live(self):
        controller, evidence, request_path, worker_pid, worker_handle = (
            self._external_controller_fixture("live-controller-refusal")
        )
        try:
            before = self._protocol_bytes(evidence)
            receipt = stop_watch(request_path)

            self.assertFalse(receipt.complete)
            self.assertIn("claimed controller is live", receipt.error)
            self.assertEqual(before, self._protocol_bytes(evidence))
            self.assertFalse((evidence / "stop.token").exists())
        finally:
            self._finish_external_fixture(controller, worker_pid, worker_handle)

    def test_non_owner_after_exact_controller_death_is_cleanup_only(self):
        controller, evidence, request_path, worker_pid, worker_handle = (
            self._external_controller_fixture("dead-controller-cleanup")
        )
        try:
            controller.terminate()
            controller.wait(timeout=10)
            receipt = stop_watch(request_path)

            self.assertFalse(receipt.complete)
            outcome = watch_outcome_from_bytes((evidence / "outcome.json").read_bytes())
            self.assertEqual(
                WatchEvidenceCompletion.INCOMPLETE,
                outcome.evidence_completion,
            )
            self.assertIn(
                True,
                (
                    windows_watch._kernel32.WaitForSingleObject(worker_handle, 0)
                    == windows_watch._WAIT_OBJECT_0,
                    "worker-identity-uncertain" in outcome.reason_codes,
                    "worker-cleanup-refused" in outcome.reason_codes,
                ),
            )
        finally:
            self._finish_external_fixture(controller, worker_pid, worker_handle)

    def test_bound_event_survives_exact_controller_death_as_incomplete(self):
        controller, evidence, request_path, worker_pid, worker_handle = (
            self._external_controller_fixture("dead-controller-bound-event")
        )
        watched = evidence.parent / "watched"
        relative_path = "positive-before-controller-death.txt"
        try:
            (watched / relative_path).write_text("breach", encoding="utf-8")
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                try:
                    if relative_path.encode("utf-8") in (
                        evidence / "events.ndjson"
                    ).read_bytes():
                        break
                except FileNotFoundError:
                    pass
                self.assertIsNone(
                    controller.poll(),
                    "controller exited before the positive event was journalled",
                )
                time.sleep(0.02)
            else:
                self.fail("positive event was not journalled before controller death")

            controller.terminate()
            controller.wait(timeout=10)
            receipt = stop_watch(request_path)
            outcome = watch_outcome_from_bytes((evidence / "outcome.json").read_bytes())

            self.assertFalse(receipt.complete)
            self.assertEqual(
                WatchEvidenceCompletion.INCOMPLETE,
                outcome.evidence_completion,
            )
            self.assertIn("controller-session-lost", outcome.reason_codes)
            self.assertTrue(
                any(
                    event.root_kind == "SourceMods"
                    and event.relative_path == relative_path
                    for event in outcome.events
                ),
                "a fully bound positive event was discarded",
            )
            self.assertEqual(outcome.events, receipt.events)
        finally:
            self._finish_external_fixture(controller, worker_pid, worker_handle)

    def test_bound_event_survives_unrelated_terminal_incompleteness(self):
        request_path, _ = self._start_case("bound-event-unrelated-incomplete")
        watched = request_path.parent.parent / "watched"
        relative_path = "positive-before-unrelated-error.txt"
        (watched / relative_path).write_text("breach", encoding="utf-8")
        time.sleep(0.2)
        real_read = windows_watch._read_exact_regular_file

        def incomplete_terminal(
            path: Path,
            label: str,
            **kwargs: object,
        ) -> bytes:
            data = real_read(path, label, **kwargs)
            if Path(path).name == "terminal.json" and label == "terminal record":
                terminal = json.loads(data)
                terminal["complete"] = False
                terminal["error"] = "injected unrelated completeness error"
                return _canonical(terminal)
            return data

        with mock.patch.object(
            windows_watch,
            "_read_exact_regular_file",
            side_effect=incomplete_terminal,
        ):
            receipt = stop_watch(request_path)

        outcome = watch_outcome_from_bytes(
            (request_path.parent / "outcome.json").read_bytes()
        )
        self.assertFalse(receipt.complete)
        self.assertEqual(
            WatchEvidenceCompletion.INCOMPLETE,
            outcome.evidence_completion,
        )
        self.assertTrue(
            any(
                event.root_kind == "SourceMods"
                and event.relative_path == relative_path
                for event in outcome.events
            ),
            "an unrelated completion error discarded trustworthy event evidence",
        )
        self.assertEqual(outcome.events, receipt.events)

    def test_untrusted_event_evidence_cannot_survive_incomplete_capture(self):
        for fault in ("malformed", "hash", "sequence", "identity"):
            with self.subTest(fault=fault):
                request_path, _ = self._start_case(f"untrusted-event-{fault}")
                watched = request_path.parent.parent / "watched"
                (watched / f"{fault}.txt").write_text("breach", encoding="utf-8")
                time.sleep(0.2)
                real_read = windows_watch._read_exact_regular_file
                real_journal = windows_watch._read_exact_journal

                def corrupt_terminal(
                    path: Path,
                    label: str,
                    **kwargs: object,
                ) -> bytes:
                    data = real_read(path, label, **kwargs)
                    if Path(path).name != "terminal.json" or label != "terminal record":
                        return data
                    terminal = json.loads(data)
                    if fault == "hash":
                        terminal["eventBytesSha256"] = "0" * 64
                    elif fault == "identity":
                        terminal["journalFileId"] += 1
                    return _canonical(terminal)

                def corrupt_journal(path: Path):
                    journal = real_journal(path)
                    if fault == "malformed":
                        return replace(journal, data=b'{"sequence":1\n')
                    if fault == "sequence":
                        return replace(
                            journal,
                            data=_canonical(
                                {
                                    "action": "Added",
                                    "relativePath": "sequence.txt",
                                    "rootKind": "SourceMods",
                                    "sequence": 2,
                                }
                            ),
                        )
                    return journal

                with (
                    mock.patch.object(
                        windows_watch,
                        "_read_exact_regular_file",
                        side_effect=corrupt_terminal,
                    ),
                    mock.patch.object(
                        windows_watch,
                        "_read_exact_journal",
                        side_effect=corrupt_journal,
                    ),
                ):
                    receipt = stop_watch(request_path)

                outcome = watch_outcome_from_bytes(
                    (request_path.parent / "outcome.json").read_bytes()
                )
                self.assertFalse(receipt.complete)
                self.assertEqual((), outcome.events)
                self.assertEqual((), receipt.events)

    def test_controller_death_after_terminal_before_outcome_cannot_promote(self):
        case_root = self.root / "controller-death-after-terminal"
        evidence = case_root / "evidence"
        watched = case_root / "watched"
        ready = case_root / "controller-ready.txt"
        finish = case_root / "finish-watch.txt"
        terminal_ready = case_root / "terminal-ready.txt"
        evidence.mkdir(parents=True)
        watched.mkdir()
        controller_script = "\n".join(
            (
                "from dataclasses import replace",
                "from pathlib import Path",
                "import sys",
                "import time",
                "from modlab.validation.mo2_containment_model import ContainmentScenario",
                "from modlab.validation.windows_watch import ROOT_KINDS, WatchRequest, start_watch, watch_root",
                "from tests.test_mo2_containment_watch import _publish_controller_pid_barrier",
                "evidence, watched, ready, finish, terminal_ready = map(Path, sys.argv[1:6])",
                "root = watch_root('SourceMods', watched)",
                "request = WatchRequest(",
                "    request_id='watch-request:' + '4' * 64,",
                "    session_id='watch-session:' + '5' * 64,",
                "    run_id='containment-run:0123456789abcdef0123456789abcdef',",
                "    scenario=ContainmentScenario.MERGE_EXISTING,",
                "    evidence_root=evidence,",
                "    stop_token_path=evidence / 'stop.token',",
                "    roots=tuple(replace(root, root_kind=kind) for kind in ROOT_KINDS),",
                ")",
                "worker_pid = start_watch(request)",
                "_publish_controller_pid_barrier(ready, worker_pid)",
                "while not finish.exists(): time.sleep(0.01)",
                "request.stop_token_path.write_bytes(b'stop\\n')",
                "while not (evidence / 'terminal.json').exists(): time.sleep(0.01)",
                "terminal_ready.write_text('ready', encoding='ascii')",
                "while True: time.sleep(1)",
            )
        )
        controller = subprocess.Popen(
            (
                sys.executable,
                "-B",
                "-c",
                controller_script,
                str(evidence),
                str(watched),
                str(ready),
                str(finish),
                str(terminal_ready),
            ),
            cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        worker_pid = 0
        worker_handle = 0
        try:
            deadline = time.monotonic() + 20.0
            while not ready.exists() and time.monotonic() < deadline:
                self.assertIsNone(controller.poll(), "controller exited before ready")
                time.sleep(0.02)
            self.assertTrue(ready.is_file())
            worker_pid = int(ready.read_text(encoding="ascii"))
            worker_handle, _ = windows_watch._open_process_identity(worker_pid)
            finish.write_text("finish", encoding="ascii")
            deadline = time.monotonic() + 20.0
            while not terminal_ready.exists() and time.monotonic() < deadline:
                self.assertIsNone(
                    controller.poll(),
                    "controller exited before terminal publication",
                )
                time.sleep(0.02)
            self.assertTrue(terminal_ready.is_file())
            terminal = json.loads(
                (evidence / "terminal.json").read_text(encoding="utf-8")
            )
            self.assertTrue(terminal["complete"], terminal["error"])
            self.assertFalse((evidence / "outcome.json").exists())

            controller.terminate()
            controller.wait(timeout=10)
            first = stop_watch(evidence / "request.json")
            second = stop_watch(evidence / "request.json")
            outcome = watch_outcome_from_bytes((evidence / "outcome.json").read_bytes())
            self.assertFalse(first.complete)
            self.assertFalse(second.complete)
            self.assertEqual(first.watch_outcome_id, second.watch_outcome_id)
            self.assertEqual(
                WatchEvidenceCompletion.INCOMPLETE,
                outcome.evidence_completion,
            )
            self.assertIn("controller-session-lost", outcome.reason_codes)
        finally:
            if worker_handle:
                self._finish_external_fixture(controller, worker_pid, worker_handle)
            elif controller.poll() is None:
                controller.terminate()
                controller.wait(timeout=10)

    def test_controller_death_before_launch_publication_is_mechanically_incomplete(self):
        case_root = self.root / "controller-death-before-launch"
        evidence = case_root / "evidence"
        watched = case_root / "watched"
        barrier = case_root / "worker-spawned.txt"
        evidence.mkdir(parents=True)
        watched.mkdir()
        controller_script = "\n".join(
            (
                "from dataclasses import replace",
                "import json",
                "from pathlib import Path",
                "import sys",
                "import time",
                "from modlab.validation.mo2_containment_model import ContainmentScenario",
                "import modlab.validation.windows_watch as watch",
                "from tests.test_mo2_containment_watch import _publish_controller_pid_barrier",
                "evidence, watched, barrier = map(Path, sys.argv[1:4])",
                "real_publish = watch.publish_new_verified",
                "def stall_launch(path, data, parse):",
                "    if path.name == 'worker-launch.json':",
                "        _publish_controller_pid_barrier(barrier, json.loads(data)['workerPid'])",
                "        while True: time.sleep(1)",
                "    return real_publish(path, data, parse)",
                "watch.publish_new_verified = stall_launch",
                "root = watch.watch_root('SourceMods', watched)",
                "request = watch.WatchRequest(",
                "    request_id='watch-request:' + '2' * 64,",
                "    session_id='watch-session:' + '3' * 64,",
                "    run_id='containment-run:0123456789abcdef0123456789abcdef',",
                "    scenario=ContainmentScenario.MERGE_EXISTING,",
                "    evidence_root=evidence,",
                "    stop_token_path=evidence / 'stop.token',",
                "    roots=tuple(replace(root, root_kind=kind) for kind in watch.ROOT_KINDS),",
                ")",
                "watch.start_watch(request)",
            )
        )
        controller = subprocess.Popen(
            (
                sys.executable,
                "-B",
                "-c",
                controller_script,
                str(evidence),
                str(watched),
                str(barrier),
            ),
            cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        worker_pid = 0
        worker_handle = 0
        try:
            deadline = time.monotonic() + 20.0
            while not barrier.exists() and time.monotonic() < deadline:
                self.assertIsNone(
                    controller.poll(),
                    "controller exited before spawning the worker",
                )
                time.sleep(0.02)
            self.assertTrue(barrier.is_file())
            worker_pid = int(barrier.read_text(encoding="ascii"))
            worker_handle, _ = windows_watch._open_process_identity(worker_pid)
            self.assertFalse((evidence / "worker-launch.json").exists())
            self.assertFalse((evidence / "ready.json").exists())

            controller.terminate()
            controller.wait(timeout=10)
            self.assertEqual(
                windows_watch._WAIT_OBJECT_0,
                windows_watch._kernel32.WaitForSingleObject(worker_handle, 15_000),
            )
            receipt = stop_watch(evidence / "request.json")
            self.assertFalse(receipt.complete)
            self.assertIn("watch protocol is unavailable", receipt.error)
            self.assertFalse((evidence / "outcome.json").exists())
            self.assertFalse((evidence / "stop.token").exists())
        finally:
            if worker_handle:
                self._finish_external_fixture(controller, worker_pid, worker_handle)
            elif controller.poll() is None:
                controller.terminate()
                controller.wait(timeout=10)

    def test_controller_death_after_launch_before_ready_acceptance_is_incomplete(self):
        case_root = self.root / "controller-death-before-ready-acceptance"
        evidence = case_root / "evidence"
        watched = case_root / "watched"
        barrier = case_root / "ready-awaiting-controller.txt"
        evidence.mkdir(parents=True)
        watched.mkdir()
        controller_script = "\n".join(
            (
                "from dataclasses import replace",
                "from pathlib import Path",
                "import sys",
                "import time",
                "from modlab.validation.mo2_containment_model import ContainmentScenario",
                "import modlab.validation.windows_watch as watch",
                "from tests.test_mo2_containment_watch import _publish_controller_pid_barrier",
                "evidence, watched, barrier = map(Path, sys.argv[1:4])",
                "def stall_ready(data, request, worker_pid, request_sha256, worker_creation_time):",
                "    _publish_controller_pid_barrier(barrier, worker_pid)",
                "    while True: time.sleep(1)",
                "watch._parse_ready = stall_ready",
                "root = watch.watch_root('SourceMods', watched)",
                "request = watch.WatchRequest(",
                "    request_id='watch-request:' + 'a' * 64,",
                "    session_id='watch-session:' + 'b' * 64,",
                "    run_id='containment-run:0123456789abcdef0123456789abcdef',",
                "    scenario=ContainmentScenario.MERGE_EXISTING,",
                "    evidence_root=evidence,",
                "    stop_token_path=evidence / 'stop.token',",
                "    roots=tuple(replace(root, root_kind=kind) for kind in watch.ROOT_KINDS),",
                ")",
                "watch.start_watch(request)",
            )
        )
        controller = subprocess.Popen(
            (
                sys.executable,
                "-B",
                "-c",
                controller_script,
                str(evidence),
                str(watched),
                str(barrier),
            ),
            cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        worker_pid = 0
        worker_handle = 0
        try:
            deadline = time.monotonic() + 20.0
            while not barrier.exists() and time.monotonic() < deadline:
                self.assertIsNone(
                    controller.poll(),
                    "controller exited before the ready acceptance barrier",
                )
                time.sleep(0.02)
            self.assertTrue(barrier.is_file())
            worker_pid = int(barrier.read_text(encoding="ascii"))
            worker_handle, _ = windows_watch._open_process_identity(worker_pid)
            self.assertTrue((evidence / "worker-launch.json").is_file())
            self.assertTrue((evidence / "ready.json").is_file())
            self.assertFalse((evidence / "outcome.json").exists())

            controller.terminate()
            controller.wait(timeout=10)
            self.assertEqual(
                windows_watch._WAIT_OBJECT_0,
                windows_watch._kernel32.WaitForSingleObject(worker_handle, 15_000),
            )
            receipt = stop_watch(evidence / "request.json")
            outcome = watch_outcome_from_bytes((evidence / "outcome.json").read_bytes())
            self.assertFalse(receipt.complete)
            self.assertEqual(
                WatchEvidenceCompletion.INCOMPLETE,
                outcome.evidence_completion,
            )
            self.assertIn("controller-session-lost", outcome.reason_codes)
        finally:
            if worker_handle:
                self._finish_external_fixture(controller, worker_pid, worker_handle)
            elif controller.poll() is None:
                controller.terminate()
                controller.wait(timeout=10)

    def test_controller_death_after_token_before_terminal_is_incomplete(self):
        case_root = self.root / "controller-death-after-token"
        evidence = case_root / "evidence"
        watched = case_root / "watched"
        ready = case_root / "controller-ready.txt"
        release = case_root / "release-token.txt"
        token_ready = case_root / "token-ready.txt"
        evidence.mkdir(parents=True)
        watched.mkdir()
        sentinel = b'{"sentinel":"no-worker-terminal"}\n'
        controller_script = "\n".join(
            (
                "from dataclasses import replace",
                "from pathlib import Path",
                "import sys",
                "import time",
                "from modlab.validation.mo2_containment_model import ContainmentScenario",
                "from modlab.validation.windows_watch import ROOT_KINDS, WatchRequest, start_watch, watch_root",
                "from tests.test_mo2_containment_watch import _publish_controller_pid_barrier",
                "evidence, watched, ready, release, token_ready = map(Path, sys.argv[1:6])",
                "root = watch_root('SourceMods', watched)",
                "request = WatchRequest(",
                "    request_id='watch-request:' + 'c' * 64,",
                "    session_id='watch-session:' + 'd' * 64,",
                "    run_id='containment-run:0123456789abcdef0123456789abcdef',",
                "    scenario=ContainmentScenario.MERGE_EXISTING,",
                "    evidence_root=evidence,",
                "    stop_token_path=evidence / 'stop.token',",
                "    roots=tuple(replace(root, root_kind=kind) for kind in ROOT_KINDS),",
                ")",
                "worker_pid = start_watch(request)",
                "_publish_controller_pid_barrier(ready, worker_pid)",
                "while not release.exists(): time.sleep(0.01)",
                "request.stop_token_path.write_bytes(b'stop\\n')",
                "token_ready.write_text('ready', encoding='ascii')",
                "while True: time.sleep(1)",
            )
        )
        controller = subprocess.Popen(
            (
                sys.executable,
                "-B",
                "-c",
                controller_script,
                str(evidence),
                str(watched),
                str(ready),
                str(release),
                str(token_ready),
            ),
            cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        worker_pid = 0
        worker_handle = 0
        try:
            deadline = time.monotonic() + 20.0
            while not ready.exists() and time.monotonic() < deadline:
                self.assertIsNone(controller.poll(), "controller exited before ready")
                time.sleep(0.02)
            self.assertTrue(ready.is_file())
            worker_pid = int(ready.read_text(encoding="ascii"))
            worker_handle, _ = windows_watch._open_process_identity(worker_pid)
            (evidence / "terminal.json").write_bytes(sentinel)
            release.write_text("release", encoding="ascii")
            deadline = time.monotonic() + 20.0
            while not token_ready.exists() and time.monotonic() < deadline:
                self.assertIsNone(
                    controller.poll(),
                    "controller exited before publishing the stop token",
                )
                time.sleep(0.02)
            self.assertTrue(token_ready.is_file())
            self.assertTrue((evidence / "stop.token").is_file())

            controller.terminate()
            controller.wait(timeout=10)
            receipt = stop_watch(evidence / "request.json")
            outcome = watch_outcome_from_bytes((evidence / "outcome.json").read_bytes())
            self.assertFalse(receipt.complete)
            self.assertEqual(sentinel, (evidence / "terminal.json").read_bytes())
            self.assertEqual(
                WatchEvidenceCompletion.INCOMPLETE,
                outcome.evidence_completion,
            )
            self.assertIn("controller-session-lost", outcome.reason_codes)
        finally:
            if worker_handle:
                self._finish_external_fixture(controller, worker_pid, worker_handle)
            elif controller.poll() is None:
                controller.terminate()
                controller.wait(timeout=10)

    def test_dead_owner_worker_identity_uncertainty_is_explicit_and_read_only(self):
        controller, evidence, request_path, worker_pid, worker_handle = (
            self._external_controller_fixture("dead-controller-worker-uncertain")
        )
        real_open = windows_watch._open_process_identity

        def deny_worker_identity(pid: int):
            if pid == worker_pid:
                raise OSError(5, "injected worker identity access denial")
            return real_open(pid)

        try:
            controller.terminate()
            controller.wait(timeout=10)
            with mock.patch.object(
                windows_watch,
                "_open_process_identity",
                side_effect=deny_worker_identity,
            ):
                receipt = stop_watch(request_path)

            outcome = watch_outcome_from_bytes((evidence / "outcome.json").read_bytes())
            self.assertFalse(receipt.complete)
            self.assertIn("worker-identity-uncertain", receipt.error)
            self.assertIn("worker-identity-uncertain", outcome.reason_codes)
            self.assertFalse((evidence / "stop.token").exists())
            self.assertEqual(
                windows_watch._WAIT_TIMEOUT,
                windows_watch._kernel32.WaitForSingleObject(worker_handle, 0),
            )
        finally:
            self._finish_external_fixture(controller, worker_pid, worker_handle)

    def test_dead_owner_cleanup_faults_are_explicitly_incomplete(self):
        for fault, expected_reason in (
            ("worker-creation-mismatch", "worker-identity-uncertain"),
            ("worker-wait-timeout", "worker-cleanup-refused"),
            ("worker-handle-close-failure", "worker-cleanup-refused"),
        ):
            with self.subTest(fault=fault):
                controller, evidence, request_path, worker_pid, worker_handle = (
                    self._external_controller_fixture(f"dead-owner-{fault}")
                )
                real_open = windows_watch._open_process_identity
                real_wait = windows_watch._kernel32.WaitForSingleObject
                real_close = windows_watch._close_handle

                def inspect_process(pid: int):
                    handle, creation_time = real_open(pid)
                    if fault == "worker-creation-mismatch" and pid == worker_pid:
                        return handle, creation_time + 1
                    return handle, creation_time

                def wait_for_process(handle: int, timeout: int) -> int:
                    if fault == "worker-wait-timeout" and timeout == 15_000:
                        return windows_watch._WAIT_TIMEOUT
                    return real_wait(handle, timeout)

                def close_process(handle: int, label: str) -> str | None:
                    if (
                        fault == "worker-handle-close-failure"
                        and label.startswith("cleanup worker process")
                    ):
                        self.assertIsNone(real_close(handle, label))
                        return "unclosed handle: injected cleanup worker process"
                    return real_close(handle, label)

                try:
                    controller.terminate()
                    controller.wait(timeout=10)
                    with (
                        mock.patch.object(
                            windows_watch,
                            "_open_process_identity",
                            side_effect=inspect_process,
                        ),
                        mock.patch.object(
                            windows_watch._kernel32,
                            "WaitForSingleObject",
                            side_effect=wait_for_process,
                        ),
                        mock.patch.object(
                            windows_watch,
                            "_close_handle",
                            side_effect=close_process,
                        ),
                    ):
                        receipt = stop_watch(request_path)

                    outcome = watch_outcome_from_bytes(
                        (evidence / "outcome.json").read_bytes()
                    )
                    self.assertFalse(receipt.complete)
                    self.assertIn(expected_reason, receipt.error)
                    self.assertIn(expected_reason, outcome.reason_codes)
                    if fault == "worker-creation-mismatch":
                        self.assertFalse((evidence / "stop.token").exists())
                finally:
                    self._finish_external_fixture(
                        controller,
                        worker_pid,
                        worker_handle,
                    )

    def test_existing_incomplete_outcome_reports_new_cleanup_blocker(self):
        controller, evidence, request_path, worker_pid, worker_handle = (
            self._external_controller_fixture("existing-outcome-cleanup-blocker")
        )
        cleanup_handle = 0
        try:
            request = windows_watch._load_request_path(request_path)
            claim = windows_watch._load_controller_claim(request)
            launch = windows_watch._load_worker_launch(request)
            seeded = windows_watch._publish_or_load_outcome(
                windows_watch._watch_outcome(
                    request,
                    claim,
                    launch,
                    completion=WatchEvidenceCompletion.INCOMPLETE,
                    worker_exit_code=None,
                    reasons=("controller-session-lost",),
                    captured=None,
                ),
                request,
                claim,
                launch,
            )
            seeded_bytes = (evidence / "outcome.json").read_bytes()
            controller.terminate()
            controller.wait(timeout=10)
            cleanup_handle, _ = windows_watch._open_process_identity(worker_pid)
            real_status = windows_watch._exact_process_status
            real_wait = windows_watch._kernel32.WaitForSingleObject

            def live_worker_status(pid: int, creation_time: int):
                if pid == worker_pid:
                    return "live", cleanup_handle, None
                return real_status(pid, creation_time)

            def timeout_cleanup(handle: int, timeout: int) -> int:
                if handle == cleanup_handle and timeout == 15_000:
                    return windows_watch._WAIT_TIMEOUT
                return real_wait(handle, timeout)

            with (
                mock.patch.object(
                    windows_watch,
                    "_exact_process_status",
                    side_effect=live_worker_status,
                ),
                mock.patch.object(
                    windows_watch._kernel32,
                    "WaitForSingleObject",
                    side_effect=timeout_cleanup,
                ),
            ):
                receipt = stop_watch(request_path)

            self.assertFalse(receipt.complete)
            self.assertEqual(watch_outcome_id_for(seeded), receipt.watch_outcome_id)
            self.assertIn("worker-cleanup-refused", receipt.error)
            self.assertEqual(seeded_bytes, (evidence / "outcome.json").read_bytes())
        finally:
            self._finish_external_fixture(controller, worker_pid, worker_handle)

    def test_non_owner_identity_uncertainty_refuses_all_mutation(self):
        for fault in ("access-denied", "creation-time-mismatch"):
            with self.subTest(fault=fault):
                controller, evidence, request_path, worker_pid, worker_handle = (
                    self._external_controller_fixture(f"non-owner-{fault}")
                )
                real_open = windows_watch._open_process_identity

                def uncertain_controller(pid: int):
                    if pid != controller.pid:
                        return real_open(pid)
                    if fault == "access-denied":
                        raise OSError(5, "injected controller access denial")
                    handle, creation_time = real_open(pid)
                    return handle, creation_time + 1

                try:
                    before = self._protocol_bytes(evidence)
                    with mock.patch.object(
                        windows_watch,
                        "_open_process_identity",
                        side_effect=uncertain_controller,
                    ):
                        receipt = stop_watch(request_path)

                    self.assertFalse(receipt.complete)
                    self.assertIn("uncertain", receipt.error)
                    self.assertEqual(before, self._protocol_bytes(evidence))
                    self.assertFalse((evidence / "stop.token").exists())
                    self.assertFalse((evidence / "outcome.json").exists())
                finally:
                    self._finish_external_fixture(controller, worker_pid, worker_handle)

    def test_concurrent_local_stops_share_one_completion_session(self):
        request_path, _ = self._start_case("concurrent-local-stop")
        barrier = threading.Barrier(3)
        receipts: list[WatchReceipt] = []
        failures: list[BaseException] = []

        def stop_once() -> None:
            try:
                barrier.wait()
                receipts.append(stop_watch(request_path))
            except BaseException as error:
                failures.append(error)

        threads = [threading.Thread(target=stop_once) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        self.assertEqual([], failures)
        self.assertEqual(2, len(receipts))
        self.assertTrue(all(receipt.complete for receipt in receipts), receipts)
        self.assertEqual(
            1,
            len({receipt.watch_outcome_id for receipt in receipts}),
        )

    def test_concurrent_non_owners_both_refuse_a_live_controller(self):
        controller, evidence, request_path, worker_pid, worker_handle = (
            self._external_controller_fixture("concurrent-non-owner-refusal")
        )
        try:
            before = self._protocol_bytes(evidence)
            barrier = threading.Barrier(3)
            receipts: list[WatchReceipt] = []

            def refuse_once() -> None:
                barrier.wait()
                receipts.append(stop_watch(request_path))

            threads = [threading.Thread(target=refuse_once) for _ in range(2)]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join()

            self.assertEqual(2, len(receipts))
            self.assertTrue(all(not receipt.complete for receipt in receipts))
            self.assertTrue(
                all("claimed controller is live" in receipt.error for receipt in receipts)
            )
            self.assertEqual(before, self._protocol_bytes(evidence))
            self.assertFalse((evidence / "stop.token").exists())
        finally:
            self._finish_external_fixture(controller, worker_pid, worker_handle)

    def test_two_cleanup_processes_converge_on_one_incomplete_outcome(self):
        controller, evidence, request_path, worker_pid, worker_handle = (
            self._external_controller_fixture("two-process-dead-owner-cleanup")
        )
        cleaners: list[subprocess.Popen[bytes]] = []
        cleanup_script = "\n".join(
            (
                "import json",
                "from pathlib import Path",
                "import sys",
                "import time",
                "from modlab.validation.windows_watch import stop_watch",
                "request, ready, go, result = map(Path, sys.argv[1:5])",
                "ready.write_text('ready', encoding='ascii')",
                "while not go.exists(): time.sleep(0.01)",
                "receipt = stop_watch(request)",
                "result.write_text(json.dumps({",
                "    'complete': receipt.complete,",
                "    'error': receipt.error,",
                "    'watchOutcomeId': receipt.watch_outcome_id,",
                "}, sort_keys=True), encoding='utf-8')",
            )
        )
        go = evidence.parent / "cleanup-go.txt"
        ready_paths = [evidence.parent / f"cleanup-{index}-ready.txt" for index in range(2)]
        result_paths = [evidence.parent / f"cleanup-{index}-result.json" for index in range(2)]
        try:
            controller.terminate()
            controller.wait(timeout=10)
            for ready, result in zip(ready_paths, result_paths, strict=True):
                cleaners.append(
                    subprocess.Popen(
                        (
                            sys.executable,
                            "-B",
                            "-c",
                            cleanup_script,
                            str(request_path),
                            str(ready),
                            str(go),
                            str(result),
                        ),
                        cwd=Path(__file__).resolve().parents[1],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                )
            deadline = time.monotonic() + 20.0
            while (
                not all(path.exists() for path in ready_paths)
                and time.monotonic() < deadline
            ):
                self.assertTrue(
                    all(process.poll() is None for process in cleaners),
                    "cleanup process exited before the race barrier",
                )
                time.sleep(0.02)
            self.assertTrue(all(path.is_file() for path in ready_paths))
            go.write_text("go", encoding="ascii")
            diagnostics: list[str] = []
            for process in cleaners:
                stdout, stderr = process.communicate(timeout=20)
                diagnostics.append(
                    f"exit={process.returncode} stdout={stdout!r} stderr={stderr!r}"
                )
            self.assertTrue(
                all(process.returncode == 0 for process in cleaners),
                diagnostics,
            )
            results = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in result_paths
            ]
            self.assertTrue(all(not result["complete"] for result in results))
            self.assertEqual(
                1,
                len({result["watchOutcomeId"] for result in results}),
            )
            self.assertIsNotNone(results[0]["watchOutcomeId"])
            outcome_bytes = (evidence / "outcome.json").read_bytes()
            outcome = watch_outcome_from_bytes(outcome_bytes)
            self.assertEqual(
                WatchEvidenceCompletion.INCOMPLETE,
                outcome.evidence_completion,
            )
            parent_receipt = stop_watch(request_path)
            self.assertFalse(parent_receipt.complete)
            self.assertEqual(results[0]["watchOutcomeId"], parent_receipt.watch_outcome_id)
            self.assertEqual(outcome_bytes, (evidence / "outcome.json").read_bytes())
        finally:
            for process in cleaners:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
            self._finish_external_fixture(controller, worker_pid, worker_handle)




    def test_live_event_journal_cannot_be_reopened_for_write_or_delete(self):
        request_path, _ = self._start()
        events_path = self.evidence / "events.ndjson"

        with self.assertRaises(PermissionError):
            events_path.write_bytes(b"truncate")
        with self.assertRaises(PermissionError):
            events_path.unlink()
        receipt = stop_watch(request_path)

        self.assertTrue(receipt.complete, receipt.error)

    def test_every_exact_root_kind_is_armed_cancelled_and_attributed(self):
        kinds = (
            "SourceMods",
            "LabProfile",
            "PlayProfile",
            "Downloads",
            "Overwrite",
            "BoundedGame",
            "ExternalLocalLow",
            "ExternalTempLow",
        )
        roots = []
        for index, kind in enumerate(kinds):
            path = self.root / f"watched-{index}"
            path.mkdir()
            roots.append(watch_root(kind, path))
        request_path, _ = self._start(*roots)
        (roots[-1].path / "outside-state.txt").write_bytes(b"outside")
        time.sleep(0.1)

        receipt = stop_watch(request_path)

        self.assertTrue(receipt.complete, receipt.error)
        self.assertEqual(kinds, receipt.opened_root_kinds)
        self.assertTrue(
            any(event.root_kind == "ExternalTempLow" for event in receipt.events),
            receipt.events,
        )
        state = _protected_state("3")
        self.assertFalse(watch_proves_unchanged(receipt, state, state))

    def test_equal_root_identities_preserve_all_logical_kinds_and_fan_out_events(self):
        first = watch_root("SourceMods", self.watched)
        aliases = tuple(replace(first, root_kind=kind) for kind in windows_watch.ROOT_KINDS)
        request_path, _ = self._start(*aliases)
        (self.watched / "aliased.txt").write_bytes(b"alias fan-out")
        time.sleep(0.1)

        receipt = stop_watch(request_path)
        request_document = json.loads(request_path.read_text(encoding="utf-8"))

        self.assertTrue(receipt.complete, receipt.error)
        self.assertEqual(windows_watch.ROOT_KINDS, receipt.opened_root_kinds)
        self.assertEqual(8, len(request_document["roots"]))
        observed = {
            event.root_kind
            for event in receipt.events
            if event.relative_path == "aliased.txt" and event.action == "Added"
        }
        self.assertEqual(set(windows_watch.ROOT_KINDS), observed)

    def test_interleaved_alias_groups_keep_canonical_logical_coverage(self):
        second_path = self.root / "watched-second"
        second_path.mkdir()
        first = watch_root("SourceMods", self.watched)
        second = watch_root("SourceMods", second_path)
        roots = tuple(
            replace(first if index % 2 == 0 else second, root_kind=kind)
            for index, kind in enumerate(windows_watch.ROOT_KINDS)
        )
        request_path, _ = self._start(*roots)
        (self.watched / "mixed-alias.txt").write_bytes(b"mixed")
        time.sleep(0.1)

        receipt = stop_watch(request_path)

        self.assertTrue(receipt.complete, receipt.error)
        self.assertEqual(windows_watch.ROOT_KINDS, receipt.opened_root_kinds)
        observed = {
            event.root_kind
            for event in receipt.events
            if event.relative_path == "mixed-alias.txt" and event.action == "Added"
        }
        self.assertEqual(set(windows_watch.ROOT_KINDS[::2]), observed)

    def test_serialized_request_preserves_duplicate_physical_identities(self):
        root = watch_root("SourceMods", self.watched)
        document = {
            "evidenceRoot": str(self.evidence),
            "requestId": "watch-request:" + "d" * 64,
            "runId": "containment-run:0123456789abcdef0123456789abcdef",
            "scenario": ContainmentScenario.MERGE_EXISTING.value,
            "sessionId": "watch-session:" + "e" * 64,
            "roots": [
                {
                    "fileId": root.file_id,
                    "path": str(root.path),
                    "rootKind": kind,
                    "volumeSerial": root.volume_serial,
                }
                for kind in windows_watch.ROOT_KINDS
            ],
            "schemaVersion": 1,
            "stopTokenPath": str(self.evidence / "stop.token"),
        }

        parsed = windows_watch._request_from_bytes(_canonical(document))

        self.assertEqual(windows_watch.ROOT_KINDS, tuple(root.root_kind for root in parsed.roots))
        self.assertEqual(1, len({(root.volume_serial, root.file_id) for root in parsed.roots}))

    def test_request_schema_rejects_boolean_and_float_integer_values(self):
        request = self._request()
        base = {
            "evidenceRoot": str(request.evidence_root),
            "requestId": request.request_id,
            "runId": request.run_id,
            "scenario": request.scenario.value,
            "sessionId": request.session_id,
            "roots": [
                {
                    "fileId": root.file_id,
                    "path": str(root.path),
                    "rootKind": root.root_kind,
                    "volumeSerial": root.volume_serial,
                }
                for root in request.roots
            ],
            "schemaVersion": 1,
            "stopTokenPath": str(request.stop_token_path),
        }
        mutations = (
            ("boolean schema version", {**base, "schemaVersion": True}),
            ("float schema version", {**base, "schemaVersion": 1.0}),
            (
                "boolean file ID",
                {
                    **base,
                    "roots": [{**base["roots"][0], "fileId": True}, *base["roots"][1:]],
                },
            ),
        )

        for label, document in mutations:
            with self.subTest(label=label):
                with self.assertRaisesRegex(WatchProtocolError, "integer|schema version"):
                    windows_watch._request_from_bytes(_canonical(document))

    def test_request_rejects_noncanonical_persisted_path_spelling(self):
        request = self._request()
        document = json.loads(windows_watch._request_bytes_and_sha256(request)[0])
        document["evidenceRoot"] = str(request.evidence_root) + "\\."

        with self.assertRaisesRegex(WatchProtocolError, "canonical"):
            windows_watch._request_from_bytes(_canonical(document))

















    def test_partial_logical_root_coverage_is_rejected_before_launch(self):
        partial = watch_root("SourceMods", self.watched)

        with self.assertRaisesRegex(WatchProtocolError, "all eight logical root kinds"):
            start_watch(self._request(partial))

    def test_root_path_replacement_after_ready_is_incomplete(self):
        request_path, _ = self._start()
        moved = self.root / "watched-original"

        self.watched.rename(moved)
        self.watched.mkdir()
        receipt = stop_watch(request_path)

        self.assertFalse(receipt.complete)
        self.assertRegex(receipt.error, "root-membership-changed|root-identity-changed")

    def test_transient_root_replacement_and_restoration_is_incomplete(self):
        request_path, _ = self._start()
        moved = self.root / "watched-original"
        replacement = self.watched

        self.watched.rename(moved)
        replacement.mkdir()
        transient = replacement / "transient.txt"
        transient.write_bytes(b"must-not-be-lost")
        transient.unlink()
        replacement.rmdir()
        moved.rename(self.watched)
        time.sleep(0.1)
        receipt = stop_watch(request_path)

        self.assertFalse(receipt.complete)
        self.assertIn("root-membership-changed", receipt.error)

    def test_retained_chain_blocks_parent_rename_after_ready(self):
        outer = self.root / "ancestor-outer"
        inner = outer / "inner"
        watched = inner / "watched"
        watched.mkdir(parents=True)
        physical = watch_root("SourceMods", watched)
        aliases = tuple(replace(physical, root_kind=kind) for kind in windows_watch.ROOT_KINDS)
        request_path, _ = self._start(*aliases)
        moved = self.root / "ancestor-outer-original"

        with self.assertRaises(PermissionError):
            outer.rename(moved)
        receipt = stop_watch(request_path)

        self.assertTrue(receipt.complete, receipt.error)

    def test_retained_chain_blocks_ancestor_reparse_swap_after_ready(self):
        outer = self.root / "reparse-outer"
        watched = outer / "inner" / "watched"
        watched.mkdir(parents=True)
        physical = watch_root("SourceMods", watched)
        aliases = tuple(replace(physical, root_kind=kind) for kind in windows_watch.ROOT_KINDS)
        request_path, _ = self._start(*aliases)
        moved = self.root / "reparse-outer-original"
        outside = self.root / "reparse-outside"
        (outside / "inner" / "watched").mkdir(parents=True)

        with self.assertRaises(PermissionError):
            outer.rename(moved)
        receipt = stop_watch(request_path)

        self.assertTrue(receipt.complete, receipt.error)

    def test_worker_rejects_controller_claim_path_and_command_replay(self):
        physical = watch_root("SourceMods", self.watched)
        roots = tuple(
            replace(physical, root_kind=kind)
            for kind in windows_watch.ROOT_KINDS
        )
        for field in ("requestPath", "workerCommand"):
            with self.subTest(field=field):
                case_root = self.root / f"claim-replay-{field}"
                case_root.mkdir()
                _, request_path = self._direct_worker_request(case_root, roots)
                claim_path = case_root / "controller-claim.json"
                claim = json.loads(claim_path.read_bytes())
                canonical_request_path = request_path.resolve(strict=True)
                command = [
                    sys.executable,
                    "-B",
                    "-m",
                    "modlab.validation.windows_watch",
                    "--worker",
                    str(canonical_request_path),
                ]
                claim["requestPath"] = str(canonical_request_path)
                claim["workerCommand"] = command
                if field == "requestPath":
                    claim[field] = str(case_root / "replayed-request.json")
                else:
                    claim[field] = [*command[:-2], "--replayed-worker", command[-1]]
                claim_path.write_bytes(_canonical(claim))

                self.assertEqual(2, run_watch_worker(request_path))
                self.assertFalse((case_root / "ready.json").exists())
                self.assertFalse((case_root / "events.ndjson").exists())
                self.assertFalse((case_root / "terminal.json").exists())

    def test_guard_chain_detects_parent_replace_restore_before_descendant_open(self):
        outer = self.root / "arming-parent"
        watched = outer / "inner" / "watched"
        watched.mkdir(parents=True)
        physical = watch_root("SourceMods", watched)
        roots = tuple(replace(physical, root_kind=kind) for kind in windows_watch.ROOT_KINDS)
        case_root = self.root / "arming-evidence"
        case_root.mkdir()
        request, request_path = self._direct_worker_request(case_root, roots)
        moved = self.root / "arming-parent-original"
        real_open = windows_watch._open_directory
        raced = False

        def replace_during_open(path: Path, *, overlapped: bool):
            nonlocal raced
            if Path(path) == outer and not raced:
                raced = True
                outer.rename(moved)
                outer.mkdir()
                outer.rmdir()
                moved.rename(outer)
            return real_open(path, overlapped=overlapped)

        with mock.patch.object(windows_watch, "_open_directory", side_effect=replace_during_open):
            result = run_watch_worker(request_path)

        self.assertTrue(raced)
        self.assertEqual(1, result)
        receipt = watch_receipt_from_files(
            request,
            os.getpid(),
            case_root / "ready.json",
            case_root / "events.ndjson",
            case_root / "terminal.json",
        )
        self.assertFalse(receipt.complete)
        self.assertEqual("watch outcome missing", receipt.error)

    def test_guard_chain_detects_ancestor_reparse_swap_before_descendant_open(self):
        outer = self.root / "arming-reparse-parent"
        watched = outer / "inner" / "watched"
        watched.mkdir(parents=True)
        outside = self.root / "arming-reparse-outside"
        outside.mkdir()
        physical = watch_root("SourceMods", watched)
        roots = tuple(replace(physical, root_kind=kind) for kind in windows_watch.ROOT_KINDS)
        case_root = self.root / "arming-reparse-evidence"
        case_root.mkdir()
        request, request_path = self._direct_worker_request(case_root, roots)
        moved = self.root / "arming-reparse-parent-original"
        real_open = windows_watch._open_directory
        raced = False

        def swap_during_open(path: Path, *, overlapped: bool):
            nonlocal raced
            if Path(path) == outer and not raced:
                raced = True
                outer.rename(moved)
                create_mod_projection(outside, outer)
                outer.rmdir()
                moved.rename(outer)
            return real_open(path, overlapped=overlapped)

        with mock.patch.object(windows_watch, "_open_directory", side_effect=swap_during_open):
            result = run_watch_worker(request_path)

        self.assertTrue(raced)
        self.assertEqual(1, result)
        receipt = watch_receipt_from_files(
            request,
            os.getpid(),
            case_root / "ready.json",
            case_root / "events.ndjson",
            case_root / "terminal.json",
        )
        self.assertFalse(receipt.complete)
        self.assertEqual("watch outcome missing", receipt.error)

    def test_guard_chain_closes_new_child_on_every_pretransfer_validation_failure(self):
        physical = watch_root("SourceMods", self.watched)
        roots = tuple(replace(physical, root_kind=kind) for kind in windows_watch.ROOT_KINDS)
        child_path = Path(self.watched.anchor) / self.watched.parts[1]
        original_open = windows_watch._open_directory
        original_identity = windows_watch._handle_identity
        original_close = windows_watch._close_handle

        for mode, expected_error, expected_open_count in (
            ("identity", "injected child identity failure", 0),
            ("type", "path component is not a directory", 0),
            ("reparse", "path component became reparse", 0),
            ("close", "injected child handle close failure", 1),
        ):
            with self.subTest(mode=mode):
                case_root = self.root / f"child-validation-{mode}"
                case_root.mkdir()
                request, request_path = self._direct_worker_request(case_root, roots)
                opened_child = 0
                child_closes: list[tuple[int, str, str | None]] = []

                def track_open(path: Path, *, overlapped: bool) -> int:
                    nonlocal opened_child
                    handle = original_open(path, overlapped=overlapped)
                    if Path(path) == child_path:
                        opened_child = handle
                    return handle

                def fail_child_validation(handle: int, path: Path):
                    volume, file_id, attributes = original_identity(handle, path)
                    if Path(path) != child_path:
                        return volume, file_id, attributes
                    if mode in {"identity", "close"}:
                        raise OSError("injected child identity failure")
                    if mode == "type":
                        attributes &= ~windows_watch._FILE_ATTRIBUTE_DIRECTORY
                    else:
                        attributes |= windows_watch._FILE_ATTRIBUTE_REPARSE_POINT
                    return volume, file_id, attributes

                def track_close(handle: int, label: str) -> str | None:
                    if handle == opened_child and mode == "close":
                        result = "unclosed handle: injected child handle close failure"
                    else:
                        result = original_close(handle, label)
                    if handle == opened_child:
                        child_closes.append((handle, label, result))
                    return result

                try:
                    with mock.patch.object(
                        windows_watch,
                        "_open_directory",
                        side_effect=track_open,
                    ), mock.patch.object(
                        windows_watch,
                        "_handle_identity",
                        side_effect=fail_child_validation,
                    ), mock.patch.object(
                        windows_watch,
                        "_close_handle",
                        side_effect=track_close,
                    ):
                        result = run_watch_worker(request_path)
                finally:
                    observed_child_closes = tuple(child_closes)
                    if opened_child and (
                        not child_closes or child_closes[-1][2] is not None
                    ):
                        original_close(opened_child, "test leaked child cleanup")

                self.assertEqual(1, result)
                self.assertNotEqual(0, opened_child)
                self.assertEqual(
                    2 if expected_open_count == 1 else 1,
                    len(observed_child_closes),
                    observed_child_closes,
                )
                self.assertEqual(
                    expected_open_count == 1,
                    observed_child_closes[0][2] is not None,
                )
                self.assertEqual(
                    expected_open_count == 1,
                    observed_child_closes[-1][2] is not None,
                )
                terminal = json.loads((case_root / "terminal.json").read_text(encoding="utf-8"))
                self.assertEqual(expected_open_count, terminal["openHandleCount"])
                self.assertIn(expected_error, terminal["error"])


    def test_malformed_event_gap_overflow_and_unclosed_handle_are_incomplete(self):
        cases = (
            (b'{"sequence":1\n', True, None, 0, "malformed events"),
            (
                _canonical(
                    {"action": "Added", "relativePath": "one.txt", "rootKind": "SourceMods", "sequence": 1}
                )
                + _canonical(
                    {"action": "Removed", "relativePath": "one.txt", "rootKind": "SourceMods", "sequence": 3}
                ),
                True,
                None,
                0,
                "sequence gap",
            ),
            (b"", False, "watch overflow: ERROR_NOTIFY_ENUM_DIR", 0, "overflow"),
            (b"", True, None, 1, "unclosed handle"),
            (
                _canonical(
                    {
                        "action": "Added",
                        "relativePath": "nested\\unsafe.txt",
                        "rootKind": "SourceMods",
                        "sequence": 1,
                    }
                ),
                True,
                None,
                0,
                "event relative path is unsafe",
            ),
            (
                _canonical({"action": "Added", "relativePath": "CON.txt", "rootKind": "SourceMods", "sequence": 1}),
                True,
                None,
                0,
                "event relative path is unsafe",
            ),
        )
        for index, (event_bytes, complete, terminal_error, open_handles, message) in enumerate(cases):
            with self.subTest(message=message):
                case_root = self.root / (f"case-{index}-" + message.replace(" ", "-"))
                case_root.mkdir()
                receipt = self._receipt_fixture(
                    case_root,
                    event_bytes=event_bytes,
                    complete=complete,
                    terminal_error=terminal_error,
                    open_handle_count=open_handles,
                )
                self.assertFalse(receipt.complete)
                self.assertEqual("watch outcome missing", receipt.error)

    def test_ready_and_terminal_records_reject_extra_fields(self):
        for record_name, message in (
            ("ready.json", "ready record fields must be exact"),
            ("terminal.json", "terminal record fields must be exact"),
        ):
            with self.subTest(record=record_name):
                case_root = self.root / ("strict-" + record_name.removesuffix(".json"))
                case_root.mkdir()
                self._receipt_fixture(
                    case_root,
                    event_bytes=b"",
                    complete=True,
                    terminal_error=None,
                    open_handle_count=0,
                )
                record_path = case_root / record_name
                value = json.loads(record_path.read_text(encoding="utf-8"))
                value["unexpected"] = True
                record_path.write_bytes(_canonical(value))
                request = replace(
                    self._request(),
                    evidence_root=case_root,
                    stop_token_path=case_root / "stop.token",
                )

                receipt = watch_receipt_from_files(
                    request,
                    os.getpid(),
                    case_root / "ready.json",
                    case_root / "events.ndjson",
                    case_root / "terminal.json",
                )

                self.assertFalse(receipt.complete)
                self.assertEqual("watch outcome missing", receipt.error)

    def test_ready_and_terminal_reject_boolean_or_float_schema_versions(self):
        for record_name, schema_value in (("ready.json", True), ("terminal.json", 1.0)):
            with self.subTest(record=record_name):
                case_root = self.root / ("typed-" + record_name.removesuffix(".json"))
                case_root.mkdir()
                self._receipt_fixture(
                    case_root,
                    event_bytes=b"",
                    complete=True,
                    terminal_error=None,
                    open_handle_count=0,
                )
                record_path = case_root / record_name
                value = json.loads(record_path.read_text(encoding="utf-8"))
                value["schemaVersion"] = schema_value
                record_path.write_bytes(_canonical(value))
                request = replace(
                    self._request(),
                    evidence_root=case_root,
                    stop_token_path=case_root / "stop.token",
                )

                receipt = watch_receipt_from_files(
                    request,
                    os.getpid(),
                    case_root / "ready.json",
                    case_root / "events.ndjson",
                    case_root / "terminal.json",
                )

                self.assertFalse(receipt.complete)
                self.assertEqual("watch outcome missing", receipt.error)

    def test_root_a_receipt_replayed_for_root_b_is_incomplete(self):
        root_b = self.root / "receipt-b"
        root_b.mkdir()
        request_path, worker_pid = self._start()
        self.assertTrue(stop_watch(request_path).complete)
        (root_b / "outcome.json").write_bytes(
            (self.evidence / "outcome.json").read_bytes()
        )
        request_b = replace(
            windows_watch._load_request_path(request_path),
            evidence_root=root_b,
            stop_token_path=root_b / "stop.token",
        )

        receipt = watch_receipt_from_files(
            request_b,
            worker_pid,
            root_b / "ready.json",
            root_b / "events.ndjson",
            root_b / "terminal.json",
        )

        self.assertFalse(receipt.complete)
        self.assertEqual("watch outcome binding mismatch", receipt.error)

    def test_replaced_or_truncated_event_journal_is_incomplete(self):
        for mutation in ("replace", "truncate"):
            with self.subTest(mutation=mutation):
                case_root = self.root / f"journal-{mutation}"
                case_root.mkdir()
                self._receipt_fixture(
                    case_root,
                    event_bytes=b"",
                    complete=True,
                    terminal_error=None,
                    open_handle_count=0,
                )
                events_path = case_root / "events.ndjson"
                if mutation == "replace":
                    retained = case_root / "retained-events.ndjson"
                    events_path.rename(retained)
                    events_path.write_bytes(retained.read_bytes())
                else:
                    events_path.write_bytes(b"tampered\n")
                request = replace(
                    self._request(),
                    evidence_root=case_root,
                    stop_token_path=case_root / "stop.token",
                )

                receipt = watch_receipt_from_files(
                    request,
                    os.getpid(),
                    case_root / "ready.json",
                    events_path,
                    case_root / "terminal.json",
                )

                self.assertFalse(receipt.complete)
                self.assertEqual("watch outcome missing", receipt.error)

    def test_terminal_event_hash_count_and_sequence_must_all_match(self):
        mutations = (
            ("eventBytesSha256", "0" * 64, "SHA-256 mismatch"),
            ("eventByteCount", 1, "byte count mismatch"),
            ("eventCount", 1, "event count|final sequence"),
            ("finalSequence", 1, "final sequence|event count"),
        )
        for index, (field, value, message) in enumerate(mutations):
            with self.subTest(field=field):
                case_root = self.root / f"terminal-event-binding-{index}"
                case_root.mkdir()
                self._receipt_fixture(
                    case_root,
                    event_bytes=b"",
                    complete=True,
                    terminal_error=None,
                    open_handle_count=0,
                )
                terminal_path = case_root / "terminal.json"
                terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
                terminal[field] = value
                terminal_path.write_bytes(_canonical(terminal))
                request = replace(
                    self._request(),
                    evidence_root=case_root,
                    stop_token_path=case_root / "stop.token",
                )

                receipt = watch_receipt_from_files(
                    request,
                    os.getpid(),
                    case_root / "ready.json",
                    case_root / "events.ndjson",
                    terminal_path,
                )

                self.assertFalse(receipt.complete)
                self.assertEqual("watch outcome missing", receipt.error)

    def test_orphaned_and_reversed_rename_pairs_are_incomplete(self):
        cases = (
            (
                b"".join(
                    _canonical(
                        {
                            "action": "RenamedOld",
                            "relativePath": "old.txt",
                            "rootKind": kind,
                            "sequence": sequence,
                        }
                    )
                    for sequence, kind in enumerate(windows_watch.ROOT_KINDS, start=1)
                ),
                "orphaned",
            ),
            (
                b"".join(
                    _canonical(
                        {
                            "action": "RenamedNew",
                            "relativePath": "new.txt",
                            "rootKind": kind,
                            "sequence": sequence,
                        }
                    )
                    for sequence, kind in enumerate(windows_watch.ROOT_KINDS, start=1)
                ),
                "reversed",
            ),
        )
        for index, (event_bytes, label) in enumerate(cases):
            with self.subTest(label=label):
                case_root = self.root / f"rename-{index}"
                case_root.mkdir()
                receipt = self._receipt_fixture(
                    case_root,
                    event_bytes=event_bytes,
                    complete=True,
                    terminal_error=None,
                    open_handle_count=0,
                )
                self.assertFalse(receipt.complete)
                self.assertEqual("watch outcome missing", receipt.error)

    def test_terminal_cannot_claim_complete_without_ready(self):
        case_root = self.root / "complete-without-ready"
        case_root.mkdir()
        self._receipt_fixture(
            case_root,
            event_bytes=b"",
            complete=True,
            terminal_error=None,
            open_handle_count=0,
        )
        terminal_path = case_root / "terminal.json"
        terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
        terminal["ready"] = False
        terminal_path.write_bytes(_canonical(terminal))
        request = replace(
            self._request(),
            evidence_root=case_root,
            stop_token_path=case_root / "stop.token",
        )

        receipt = watch_receipt_from_files(
            request,
            os.getpid(),
            case_root / "ready.json",
            case_root / "events.ndjson",
            terminal_path,
        )

        self.assertFalse(receipt.complete)
        self.assertEqual("watch outcome missing", receipt.error)

    def test_stop_token_is_written_and_worker_reaped_when_ready_is_malformed(self):
        request_path, _ = self._start()
        ready_path = self.evidence / "ready.json"
        ready_bytes = ready_path.read_bytes()
        ready_path.write_bytes(b"not-json\n")
        try:
            receipt = stop_watch(request_path)
        finally:
            ready_path.write_bytes(ready_bytes)

        self.assertFalse(receipt.complete)
        self.assertTrue((self.evidence / "stop.token").is_file())
        self.assertIn("worker-ready-invalid", receipt.error)

























    def test_process_identity_error_retains_handle_when_unwind_close_fails(self):
        original_close = windows_watch._close_handle
        retained_handle = 0
        try:
            with mock.patch.object(
                windows_watch,
                "_process_handle_creation_time",
                side_effect=WatchProtocolError("injected identity inspection failure"),
            ), mock.patch.object(
                windows_watch,
                "_close_handle",
                return_value="unclosed handle: injected identity handle",
            ):
                with self.assertRaises(windows_watch._HandleOwnershipError) as raised:
                    windows_watch._open_process_identity(os.getpid())
            retained_handle = raised.exception.handle
            self.assertNotEqual(0, retained_handle)
            self.assertIn("injected identity handle", str(raised.exception))
        finally:
            if retained_handle:
                original_close(retained_handle, "test identity ownership cleanup")

    def test_verified_process_mismatch_retains_handle_when_close_fails(self):
        original_close = windows_watch._close_handle
        retained_handle, creation_time = windows_watch._open_process_identity(os.getpid())
        try:
            with mock.patch.object(
                windows_watch,
                "_open_process_identity",
                return_value=(retained_handle, creation_time + 1),
            ), mock.patch.object(
                windows_watch,
                "_close_handle",
                return_value="unclosed handle: injected mismatch handle",
            ):
                with self.assertRaises(windows_watch._HandleOwnershipError) as raised:
                    windows_watch._verified_process_handle(os.getpid(), creation_time)
            self.assertEqual(retained_handle, raised.exception.handle)
        finally:
            original_close(retained_handle, "test mismatch ownership cleanup")

    def test_process_liveness_probe_retains_handle_when_close_fails(self):
        original_close = windows_watch._close_handle
        retained_handle = 0

        def fail_close(handle: int, label: str) -> str:
            nonlocal retained_handle
            retained_handle = handle
            return "unclosed handle: injected liveness handle"

        try:
            with mock.patch.object(windows_watch, "_close_handle", side_effect=fail_close):
                with self.assertRaises(windows_watch._HandleOwnershipError) as raised:
                    windows_watch._process_alive(os.getpid())
            self.assertEqual(retained_handle, raised.exception.handle)
        finally:
            if retained_handle:
                original_close(retained_handle, "test liveness ownership cleanup")




    def test_event_journal_init_retains_handle_when_unwind_close_fails(self):
        original_close = windows_watch._close_handle
        journal_path = self.root / "journal-init-close-failure.ndjson"
        retained_handle = 0
        try:
            with mock.patch.object(
                windows_watch,
                "_handle_identity",
                side_effect=WatchProtocolError("injected journal identity failure"),
            ), mock.patch.object(
                windows_watch,
                "_close_handle",
                return_value="unclosed handle: injected journal init handle",
            ):
                with self.assertRaises(windows_watch._HandleOwnershipError) as raised:
                    windows_watch._EventJournal(journal_path)
            retained_handle = raised.exception.handle
            self.assertNotEqual(0, retained_handle)
        finally:
            if retained_handle:
                original_close(retained_handle, "test journal init ownership cleanup")

    def test_event_journal_close_failure_keeps_handle_for_retry(self):
        journal = windows_watch._EventJournal(self.root / "journal-close-retry.ndjson")
        retained_handle = journal._handle
        original_close = windows_watch._close_handle

        with mock.patch.object(
            windows_watch,
            "_close_handle",
            return_value="unclosed handle: injected journal close",
        ):
            close_error = journal.close()

        self.assertIn("injected journal close", close_error)
        self.assertEqual(retained_handle, journal._handle)
        self.assertIsNone(journal.close())
        self.assertEqual(0, journal._handle)

    def test_event_journal_readback_close_failure_preserves_handle_reference(self):
        journal_path = self.root / "journal-readback-close.ndjson"
        journal_path.write_bytes(b"")
        original_close = windows_watch._close_handle
        retained_handle = 0
        try:
            with mock.patch.object(
                windows_watch,
                "_close_handle",
                return_value="unclosed handle: injected journal readback",
            ):
                with self.assertRaises(windows_watch._HandleOwnershipError) as raised:
                    windows_watch._read_exact_journal(journal_path)
            retained_handle = raised.exception.handle
            self.assertNotEqual(0, retained_handle)
        finally:
            if retained_handle:
                original_close(retained_handle, "test journal readback cleanup")


    def test_watch_root_close_failure_preserves_identity_handle_reference(self):
        watched_path = self._request().roots[0].path
        original_close = windows_watch._close_handle
        retained_handle = 0
        try:
            with mock.patch.object(
                windows_watch,
                "_close_handle",
                return_value="unclosed handle: injected root identity",
            ):
                with self.assertRaises(windows_watch._HandleOwnershipError) as raised:
                    watch_root("SourceMods", watched_path)
            retained_handle = raised.exception.handle
            self.assertNotEqual(0, retained_handle)
        finally:
            if retained_handle:
                original_close(retained_handle, "test root identity cleanup")

    def test_completion_event_setup_close_failure_preserves_handle_reference(self):
        root = self._request().roots[0]
        directory_handle = windows_watch._open_directory(root.path, overlapped=True)
        original_close = windows_watch._close_handle
        retained_event = 0
        try:
            with mock.patch.object(
                windows_watch,
                "_WatchState",
                side_effect=MemoryError("injected state allocation failure"),
            ), mock.patch.object(
                windows_watch,
                "_close_handle",
                return_value="unclosed handle: injected completion event",
            ):
                with self.assertRaises(windows_watch._HandleOwnershipError) as raised:
                    windows_watch._arm_directory_state(
                        root=root,
                        logical_root_kinds=(root.root_kind,),
                        directory_path=root.path,
                        directory_handle=directory_handle,
                        expected_volume_serial=root.volume_serial,
                        expected_file_id=root.file_id,
                        label="injected-state",
                        recursive=True,
                        notify_filter=windows_watch._NOTIFY_FILTER,
                        membership_name=None,
                        journal=mock.Mock(),
                        stopping=threading.Event(),
                        errors=[],
                        errors_lock=threading.Lock(),
                        states=[],
                    )
            retained_event = raised.exception.handle
            self.assertNotEqual(0, retained_event)
        finally:
            if retained_event:
                original_close(retained_event, "test completion event cleanup")
            original_close(directory_handle, "test completion directory cleanup")

    def test_worker_self_handle_close_uncertainty_is_counted_in_terminal(self):
        case_root = self.root / "worker-self-close"
        case_root.mkdir()
        _, request_path = self._direct_worker_request(case_root, self._request().roots)
        original_close = windows_watch._close_handle
        retained_handles: list[int] = []

        def fail_worker_self_close(handle: int, label: str) -> str | None:
            if label == f"worker self process {os.getpid()}":
                retained_handles.append(handle)
                return "unclosed handle: injected worker self process"
            return original_close(handle, label)

        try:
            with mock.patch.object(
                windows_watch,
                "_close_handle",
                side_effect=fail_worker_self_close,
            ):
                result = run_watch_worker(request_path)

            self.assertEqual(1, result)
            terminal = json.loads(
                (case_root / "terminal.json").read_text(encoding="utf-8")
            )
            self.assertFalse(terminal["complete"])
            self.assertEqual(1, terminal["openHandleCount"])
            self.assertIn("injected worker self process", terminal["error"])
        finally:
            for handle in set(retained_handles):
                original_close(handle, "test worker self ownership cleanup")

    def test_worker_identity_unwind_close_uncertainty_writes_terminal(self):
        case_root = self.root / "worker-identity-unwind"
        case_root.mkdir()
        _, request_path = self._direct_worker_request(case_root, self._request().roots)
        original_close = windows_watch._close_handle
        original_process_creation_time = windows_watch._process_handle_creation_time
        retained_handles: list[int] = []
        identity_inspections = 0

        def fail_worker_identity_inspection(handle: int, pid: int) -> int:
            nonlocal identity_inspections
            identity_inspections += 1
            if identity_inspections == 2:
                raise WatchProtocolError("injected worker identity inspection")
            return original_process_creation_time(handle, pid)

        def fail_identity_close(handle: int, label: str) -> str | None:
            if label == f"worker process {os.getpid()}":
                retained_handles.append(handle)
                return "unclosed handle: injected worker identity unwind"
            return original_close(handle, label)

        try:
            with mock.patch.object(
                windows_watch,
                "_process_handle_creation_time",
                side_effect=fail_worker_identity_inspection,
            ), mock.patch.object(
                windows_watch,
                "_close_handle",
                side_effect=fail_identity_close,
            ):
                result = run_watch_worker(request_path)

            self.assertEqual(1, result)
            terminal = json.loads(
                (case_root / "terminal.json").read_text(encoding="utf-8")
            )
            self.assertFalse(terminal["complete"])
            self.assertEqual(1, terminal["openHandleCount"])
            self.assertIn("injected worker identity unwind", terminal["error"])
        finally:
            for handle in set(retained_handles):
                original_close(handle, "test worker identity unwind cleanup")

    def test_worker_self_cancels_after_exact_controller_death(self):
        controller_script = "\n".join(
            (
                "from dataclasses import replace",
                "import os",
                "from pathlib import Path",
                "import sys",
                "import time",
                "from modlab.validation.mo2_containment_model import ContainmentScenario",
                "from modlab.validation.windows_watch import ROOT_KINDS, WatchRequest, start_watch, watch_root",
                "from tests.test_mo2_containment_watch import _publish_controller_pid_barrier",
                "evidence, watched, barrier = map(Path, sys.argv[1:4])",
                "root = watch_root('SourceMods', watched)",
                "request = WatchRequest(",
                "    request_id='watch-request:' + '6' * 64,",
                "    session_id='watch-session:' + '7' * 64,",
                "    run_id='containment-run:0123456789abcdef0123456789abcdef',",
                "    scenario=ContainmentScenario.MERGE_EXISTING,",
                "    evidence_root=evidence,",
                "    stop_token_path=evidence / 'stop.token',",
                "    roots=tuple(replace(root, root_kind=kind) for kind in ROOT_KINDS),",
                ")",
                "worker_pid = start_watch(request)",
                "_publish_controller_pid_barrier(barrier, worker_pid)",
                "while True: time.sleep(1)",
            )
        )
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateProcess.restype = wintypes.BOOL
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL

        def run_case(case_name: str, *, terminal_collision: bool):
            case_root = self.root / case_name
            evidence = case_root / "evidence"
            watched = case_root / "watched"
            barrier = case_root / "controller-ready.txt"
            evidence.mkdir(parents=True)
            watched.mkdir()
            controller = subprocess.Popen(
                (
                    sys.executable,
                    "-B",
                    "-c",
                    controller_script,
                    str(evidence),
                    str(watched),
                    str(barrier),
                ),
                cwd=Path(__file__).resolve().parents[1],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            worker_handle = 0
            worker_pid = 0
            sentinel = b'{"sentinel":"immutable"}\n'
            try:
                deadline = time.monotonic() + 20.0
                while not barrier.exists() and time.monotonic() < deadline:
                    if controller.poll() is not None:
                        self.fail(f"controller exited before ready: {controller.returncode}")
                    time.sleep(0.02)
                self.assertTrue(barrier.is_file(), "controller did not publish ready barrier")
                worker_pid = int(barrier.read_text(encoding="ascii"))
                worker_handle, _ = windows_watch._open_process_identity(worker_pid)
                if terminal_collision:
                    (evidence / "terminal.json").write_bytes(sentinel)

                controller.terminate()
                controller.wait(timeout=10)
                self.assertEqual(
                    windows_watch._WAIT_OBJECT_0,
                    kernel32.WaitForSingleObject(worker_handle, 15_000),
                    "worker remained alive after its exact controller died",
                )
                exit_code = wintypes.DWORD()
                self.assertTrue(kernel32.GetExitCodeProcess(worker_handle, ctypes.byref(exit_code)))
                return evidence, sentinel, exit_code.value
            finally:
                if controller.poll() is None:
                    controller.terminate()
                    controller.wait(timeout=10)
                if worker_handle:
                    if kernel32.WaitForSingleObject(worker_handle, 0) != windows_watch._WAIT_OBJECT_0:
                        terminate_handle = kernel32.OpenProcess(
                            0x0001 | windows_watch._SYNCHRONIZE,
                            False,
                            worker_pid,
                        )
                        if terminate_handle:
                            try:
                                kernel32.TerminateProcess(terminate_handle, 97)
                                kernel32.WaitForSingleObject(terminate_handle, 10_000)
                            finally:
                                kernel32.CloseHandle(terminate_handle)
                    self.assertIsNone(
                        windows_watch._close_handle(worker_handle, "controller-loss test worker")
                    )

        evidence, _, exit_code = run_case(
            "controller-loss-terminal",
            terminal_collision=False,
        )
        terminal = json.loads((evidence / "terminal.json").read_text(encoding="utf-8"))
        self.assertEqual(1, exit_code)
        self.assertFalse(terminal["complete"])
        self.assertIn("controller-session-lost", terminal["error"])

        collision_evidence, sentinel, collision_exit = run_case(
            "controller-loss-collision",
            terminal_collision=True,
        )
        self.assertEqual(2, collision_exit)
        self.assertEqual(sentinel, (collision_evidence / "terminal.json").read_bytes())
        loss = json.loads(
            (collision_evidence / "controller-loss.json").read_text(encoding="utf-8")
        )
        self.assertEqual("controller-session-lost", loss["reasonCode"])

    def test_cancel_io_completion_races_are_drained(self):
        for completion in (
            "operation-aborted",
            "normal-final-event",
            "cancel-not-found-after-completion",
        ):
            with self.subTest(completion=completion):
                proof, errors = run_cancel_completion_fixture(completion)
                self.assertTrue(proof.every_completion_observed)
                self.assertFalse(proof.storage_reused_before_completion)
                self.assertEqual((), errors)

        proof, errors = run_cancel_completion_fixture("unexpected-error")
        self.assertTrue(proof.every_completion_observed)
        self.assertFalse(proof.storage_reused_before_completion)
        self.assertTrue(errors)
        self.assertIn("WinError 123", errors[0])

        proof, errors = run_cancel_completion_fixture("wait-failed-then-aborted")
        self.assertTrue(proof.every_completion_observed)
        self.assertFalse(proof.storage_reused_before_completion)
        self.assertTrue(errors)
        self.assertIn("watch completion wait failed", errors[0])

    def test_worker_death_without_terminal_record_is_incomplete(self):
        request_path, worker_pid = self._start()
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        process = kernel32.OpenProcess(
            0x0001 | 0x00100000,
            False,
            worker_pid,
        )
        self.assertTrue(process)
        try:
            kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
            kernel32.TerminateProcess.restype = wintypes.BOOL
            kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel32.WaitForSingleObject.restype = wintypes.DWORD
            self.assertTrue(kernel32.TerminateProcess(process, 73))
            self.assertEqual(0, kernel32.WaitForSingleObject(process, 10_000))
        finally:
            self.assertTrue(kernel32.CloseHandle(process))

        receipt = stop_watch(request_path)

        self.assertFalse(receipt.complete)
        self.assertIn("worker-terminal-missing", receipt.error)

    def test_thread_start_failure_closes_opened_handles_and_writes_terminal(self):
        case_root = self.root / "thread-start-failure"
        case_root.mkdir()
        root = watch_root("SourceMods", self.watched)
        request = WatchRequest(
            request_id="watch-request:" + "c" * 64,
            session_id="watch-session:" + "d" * 64,
            run_id="containment-run:0123456789abcdef0123456789abcdef",
            scenario=ContainmentScenario.MERGE_EXISTING,
            evidence_root=case_root,
            stop_token_path=case_root / "stop.token",
            roots=tuple(replace(root, root_kind=kind) for kind in windows_watch.ROOT_KINDS),
        )
        request_path = case_root / "request.json"
        request_path.write_bytes(
            _canonical(
                {
                    "evidenceRoot": str(case_root),
                    "requestId": request.request_id,
                    "runId": request.run_id,
                    "scenario": request.scenario.value,
                    "sessionId": request.session_id,
                    "roots": [
                        {
                            "fileId": root.file_id,
                            "path": str(root.path),
                            "rootKind": kind,
                            "volumeSerial": root.volume_serial,
                        }
                        for kind in windows_watch.ROOT_KINDS
                    ],
                    "schemaVersion": 1,
                    "stopTokenPath": str(case_root / "stop.token"),
                }
            )
        )
        process_handle, worker_creation_time = windows_watch._open_process_identity(os.getpid())
        self.assertIsNone(windows_watch._close_handle(process_handle, "test worker process"))
        request_sha256 = hashlib.sha256(request_path.read_bytes()).hexdigest()
        controller_pid, controller_creation_time = (
            windows_watch._current_controller_identity()
        )
        claim = windows_watch.ControllerClaim(
            schema_version=1,
            request_sha256=request_sha256,
            session_id=request.session_id,
            run_id=request.run_id,
            scenario=request.scenario,
            request_path=request_path,
            worker_command=windows_watch.watch_worker_command(request_path),
            controller_pid=controller_pid,
            controller_creation_time=controller_creation_time,
        )
        (case_root / "controller-claim.json").write_bytes(
            windows_watch.controller_claim_to_bytes(claim, request)
        )
        launch = windows_watch.WorkerLaunch(
            schema_version=1,
            request_sha256=request_sha256,
            session_id=request.session_id,
            run_id=request.run_id,
            scenario=request.scenario,
            worker_pid=os.getpid(),
            worker_creation_time=worker_creation_time,
        )
        (case_root / "worker-launch.json").write_bytes(
            windows_watch.worker_launch_to_bytes(launch, request)
        )
        with mock.patch.object(
            windows_watch.threading.Thread,
            "start",
            side_effect=RuntimeError("injected thread start failure"),
        ):
            result = run_watch_worker(request_path)

        self.assertEqual(1, result)
        receipt = watch_receipt_from_files(
            request,
            os.getpid(),
            case_root / "ready.json",
            case_root / "events.ndjson",
            case_root / "terminal.json",
        )
        self.assertFalse(receipt.complete)
        self.assertEqual("watch outcome missing", receipt.error)
        terminal = json.loads((case_root / "terminal.json").read_text(encoding="utf-8"))
        self.assertEqual(0, terminal["openHandleCount"])

    def test_malformed_native_notification_record_is_rejected(self):
        malformed = ctypes.create_string_buffer(b"\0" * 12)

        with self.assertRaisesRegex(WatchProtocolError, "malformed notification record name"):
            windows_watch._parse_notification_buffer(malformed, 12)

    def test_exact_root_kinds_and_confined_protocol_paths_are_required(self):
        valid = watch_root("SourceMods", self.watched)
        invalid_kind = replace(valid, root_kind="sourceMods")
        with self.assertRaisesRegex(WatchProtocolError, "root kind"):
            start_watch(self._request(invalid_kind))

        outside_stop = self.root / "outside.stop"
        with self.assertRaisesRegex(WatchProtocolError, "stop token"):
            start_watch(replace(self._request(valid), stop_token_path=outside_stop))

        with self.assertRaisesRegex(WatchProtocolError, "Path values"):
            start_watch(replace(self._request(), evidence_root=str(self.evidence)))
        string_root = replace(self._request().roots[0], path=str(self.watched))
        with self.assertRaisesRegex(WatchProtocolError, "Path values"):
            start_watch(replace(self._request(), roots=(string_root, *self._request().roots[1:])))

    def test_watch_root_rejects_a_junction_before_resolving_its_target(self):
        outside = self.root / "outside"
        junction = self.root / "watched-junction"
        outside.mkdir()
        create_mod_projection(outside, junction)

        with self.assertRaisesRegex(WatchProtocolError, "reparse component"):
            watch_root("SourceMods", junction)

    def test_receipt_files_must_be_the_exact_confined_protocol_paths(self):
        case_root = self.root / "confined-receipt"
        copied_root = self.root / "copied-receipt"
        case_root.mkdir()
        copied_root.mkdir()
        self._receipt_fixture(
            case_root,
            event_bytes=b"",
            complete=True,
            terminal_error=None,
            open_handle_count=0,
        )
        for name in ("ready.json", "events.ndjson", "terminal.json"):
            (copied_root / name).write_bytes((case_root / name).read_bytes())
        request = replace(
            self._request(),
            evidence_root=case_root,
            stop_token_path=case_root / "stop.token",
        )

        receipt = watch_receipt_from_files(
            request,
            os.getpid(),
            copied_root / "ready.json",
            copied_root / "events.ndjson",
            copied_root / "terminal.json",
        )

        self.assertFalse(receipt.complete)
        self.assertIn("evidence paths must be confined", receipt.error)

    def test_receipt_ignores_mutable_evidence_normalization_after_publication(self):
        case_root = self.root / "inaccessible-receipt"
        case_root.mkdir()
        self._receipt_fixture(
            case_root,
            event_bytes=b"",
            complete=True,
            terminal_error=None,
            open_handle_count=0,
        )
        request = replace(
            self._request(),
            evidence_root=case_root,
            stop_token_path=case_root / "stop.token",
        )

        with mock.patch.object(
            windows_watch,
            "_normalize_request",
            side_effect=PermissionError("injected evidence access denied"),
        ):
            receipt = watch_receipt_from_files(
                request,
                os.getpid(),
                case_root / "ready.json",
                case_root / "events.ndjson",
                case_root / "terminal.json",
            )
        self.assertFalse(receipt.complete)
        self.assertEqual("watch outcome missing", receipt.error)


    def test_protocol_record_is_not_visible_until_its_bytes_are_complete(self):
        target = self.root / "atomic-ready.json"
        entered_write = threading.Event()
        release_write = threading.Event()
        real_write = windows_watch.os.write

        def delayed_write(descriptor, data):
            entered_write.set()
            self.assertTrue(release_write.wait(5.0))
            return real_write(descriptor, data)

        with mock.patch.object(windows_watch.os, "write", side_effect=delayed_write):
            writer = threading.Thread(target=windows_watch._write_new, args=(target, b"ready\n"))
            writer.start()
            self.assertTrue(entered_write.wait(5.0))
            try:
                self.assertFalse(target.exists())
            finally:
                release_write.set()
                writer.join()

        self.assertEqual(b"ready\n", target.read_bytes())

    def test_controller_pid_barrier_is_nonempty_before_visibility(self):
        target = self.root / "controller-pid.txt"
        entered_write = threading.Event()
        release_write = threading.Event()
        real_os_write = os.write

        def delayed_path_write_text(path: Path, data: str, **kwargs: object) -> int:
            with path.open("w", **kwargs) as stream:
                entered_write.set()
                self.assertTrue(release_write.wait(5.0))
                return stream.write(data)

        def delayed_os_write(descriptor: int, data: object) -> int:
            entered_write.set()
            self.assertTrue(release_write.wait(5.0))
            return real_os_write(descriptor, data)

        with mock.patch.object(
            Path,
            "write_text",
            new=delayed_path_write_text,
        ), mock.patch.object(
            windows_watch.os,
            "write",
            side_effect=delayed_os_write,
        ):
            writer = threading.Thread(
                target=_publish_controller_pid_barrier,
                args=(target, 4242),
            )
            writer.start()
            self.assertTrue(entered_write.wait(5.0))
            try:
                self.assertFalse(target.exists())
            finally:
                release_write.set()
                writer.join()

        self.assertEqual("4242", target.read_text(encoding="ascii"))

    def test_external_fixture_cleanup_waits_exact_worker_after_fixture_failure(self):
        controller, _, _, worker_pid, cleanup_handle = (
            self._external_controller_fixture("fixture-failure-cleanup")
        )
        observation_handle, creation_time = windows_watch._open_process_identity(worker_pid)
        session_creation_time = windows_watch.worker_launch_from_bytes(
            (self.root / "fixture-failure-cleanup" / "evidence" / "worker-launch.json").read_bytes(),
            windows_watch._load_request_path(
                self.root / "fixture-failure-cleanup" / "evidence" / "request.json"
            ),
        ).worker_creation_time
        try:
            self.assertEqual(session_creation_time, creation_time)
            with self.assertRaisesRegex(AssertionError, "injected fixture failure"):
                try:
                    self.fail("injected fixture failure")
                finally:
                    self._finish_external_fixture(
                        controller,
                        worker_pid,
                        cleanup_handle,
                    )
            cleanup_handle = 0
            self.assertEqual(
                windows_watch._WAIT_OBJECT_0,
                windows_watch._kernel32.WaitForSingleObject(
                    observation_handle,
                    0,
                ),
            )
        finally:
            if cleanup_handle:
                self._finish_external_fixture(controller, worker_pid, cleanup_handle)
            self.assertIsNone(
                windows_watch._close_handle(
                    observation_handle,
                    "fixture-failure observation worker",
                )
            )

    def test_events_and_manifests_are_independent_required_proofs(self):
        receipt_arguments = dict(
            request_id="watch-request:" + "b" * 64,
            session_id="watch-session:" + "c" * 64,
            run_id="containment-run:0123456789abcdef0123456789abcdef",
            scenario=ContainmentScenario.MERGE_EXISTING,
            worker_pid=os.getpid(),
            evidence_completion=WatchEvidenceCompletion.COMPLETED,
            ready=True,
            opened_root_kinds=windows_watch.ROOT_KINDS,
            events=(),
            event_bytes_sha256=hashlib.sha256(b"").hexdigest(),
            worker_exit_code=0,
            error=None,
            request_bytes_sha256="4" * 64,
        )
        for invalid_outcome_id in (None, "watch-outcome-sha256:invalid"):
            with self.subTest(watch_outcome_id=invalid_outcome_id):
                with self.assertRaisesRegex(
                    WatchProtocolError,
                    "Completed receipt requires a valid watch outcome ID",
                ):
                    WatchReceipt(
                        **receipt_arguments,
                        watch_outcome_id=invalid_outcome_id,
                    )
        clean = WatchReceipt(
            **receipt_arguments,
            watch_outcome_id="watch-outcome-sha256:" + "5" * 64,
        )
        transient = replace(
            clean,
            events=(WatcherEvent(1, "SourceMods", "Added", "transient.txt"),),
        )

        before = _protected_state("1")
        changed = _protected_state("2")
        self.assertTrue(watch_proves_unchanged(clean, before, before))
        self.assertFalse(watch_proves_unchanged(clean, before, changed))
        self.assertFalse(watch_proves_unchanged(transient, before, before))
        self.assertFalse(
            watch_proves_unchanged(
                replace(clean, opened_root_kinds=("SourceMods",)),
                before,
                before,
            )
        )
        self.assertFalse(
            watch_proves_unchanged(
                replace(clean, event_bytes_sha256="0" * 64),
                before,
                before,
            )
        )
        self.assertFalse(
            watch_proves_unchanged(
                replace(clean, worker_exit_code=1),
                before,
                before,
            )
        )

    def _direct_worker_request(
        self,
        case_root: Path,
        roots: tuple[WatchRoot, ...],
    ) -> tuple[WatchRequest, Path]:
        request = WatchRequest(
            request_id="watch-request:" + "e" * 64,
            session_id="watch-session:" + "f" * 64,
            run_id="containment-run:0123456789abcdef0123456789abcdef",
            scenario=ContainmentScenario.MERGE_EXISTING,
            evidence_root=case_root,
            stop_token_path=case_root / "stop.token",
            roots=roots,
        )
        request_bytes, request_sha256 = windows_watch._request_bytes_and_sha256(request)
        request_path = case_root / "request.json"
        request_path.write_bytes(request_bytes)
        controller_pid, controller_creation_time = (
            windows_watch._current_controller_identity()
        )
        claim = windows_watch.ControllerClaim(
            schema_version=1,
            request_sha256=request_sha256,
            session_id=request.session_id,
            run_id=request.run_id,
            scenario=request.scenario,
            request_path=request_path,
            worker_command=windows_watch.watch_worker_command(request_path),
            controller_pid=controller_pid,
            controller_creation_time=controller_creation_time,
        )
        claim_bytes = windows_watch.controller_claim_to_bytes(claim, request)
        windows_watch.publish_new_verified(
            case_root / "controller-claim.json",
            claim_bytes,
            lambda data: windows_watch.controller_claim_from_bytes(data, request),
        )
        process_handle, creation_time = windows_watch._open_process_identity(os.getpid())
        self.assertIsNone(windows_watch._close_handle(process_handle, "direct worker process"))
        launch = windows_watch.WorkerLaunch(
            schema_version=1,
            request_sha256=request_sha256,
            session_id=request.session_id,
            run_id=request.run_id,
            scenario=request.scenario,
            worker_pid=os.getpid(),
            worker_creation_time=creation_time,
        )
        (case_root / "worker-launch.json").write_bytes(
            windows_watch.worker_launch_to_bytes(launch, request)
        )
        return request, request_path

    def _receipt_fixture(
        self,
        case_root: Path,
        *,
        event_bytes: bytes,
        complete: bool,
        terminal_error: str | None,
        open_handle_count: int,
    ) -> WatchReceipt:
        request = replace(
            self._request(),
            evidence_root=case_root,
            stop_token_path=case_root / "stop.token",
        )
        ready_path = case_root / "ready.json"
        events_path = case_root / "events.ndjson"
        terminal_path = case_root / "terminal.json"
        worker_pid = os.getpid()
        process_handle, worker_creation_time = windows_watch._open_process_identity(worker_pid)
        close_error = windows_watch._close_handle(process_handle, "test process identity")
        self.assertIsNone(close_error)
        request_bytes, request_sha256 = windows_watch._request_bytes_and_sha256(request)
        (case_root / "request.json").write_bytes(request_bytes)
        launch = windows_watch.WorkerLaunch(
            schema_version=1,
            request_sha256=request_sha256,
            session_id=request.session_id,
            run_id=request.run_id,
            scenario=request.scenario,
            worker_pid=worker_pid,
            worker_creation_time=worker_creation_time,
        )
        (case_root / "worker-launch.json").write_bytes(
            windows_watch.worker_launch_to_bytes(launch, request)
        )
        ready_path.write_bytes(
            _canonical(
                {
                    "openedRootKinds": list(windows_watch.ROOT_KINDS),
                    "requestBytesSha256": request_sha256,
                    "requestId": request.request_id,
                    "schemaVersion": 1,
                    "workerCreationTime": worker_creation_time,
                    "workerPid": worker_pid,
                }
            )
        )
        events_path.write_bytes(event_bytes)
        journal_evidence = windows_watch._read_exact_journal(events_path)
        event_count = len(event_bytes.splitlines())
        terminal_path.write_bytes(
            _canonical(
                {
                    "complete": complete,
                    "error": terminal_error,
                    "eventByteCount": len(event_bytes),
                    "eventBytesSha256": hashlib.sha256(event_bytes).hexdigest(),
                    "eventCount": event_count,
                    "finalSequence": event_count,
                    "journalFileId": journal_evidence.file_id,
                    "journalVolumeSerial": journal_evidence.volume_serial,
                    "openHandleCount": open_handle_count,
                    "openedRootKinds": list(windows_watch.ROOT_KINDS),
                    "ready": True,
                    "requestBytesSha256": request_sha256,
                    "requestId": request.request_id,
                    "rootIdentitiesUnchanged": True,
                    "schemaVersion": 1,
                    "workerCreationTime": worker_creation_time,
                    "workerPid": worker_pid,
                }
            )
        )
        return watch_receipt_from_files(
            request,
            worker_pid,
            ready_path,
            events_path,
            terminal_path,
        )


if __name__ == "__main__":
    unittest.main()
