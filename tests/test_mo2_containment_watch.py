import ctypes
from ctypes import wintypes
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

from modlab.validation import windows_watch
from modlab.validation.mo2_containment_model import ProtectedState, TreeIdentity, WatcherEvent
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


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _protected_state(tag: str) -> ProtectedState:
    tree = TreeIdentity(tag * 64, 1, 1, 4)
    return ProtectedState(tree, tag * 64, tag * 64, tree, tree, tree)


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
        local_process = windows_watch._LOCAL_WORKERS[request_path.absolute()].process
        self.assertIsNotNone(local_process)
        self.assertFalse(local_process._handle.closed)

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
        self.assertTrue(local_process._handle.closed)

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

    def test_launch_failure_is_durable_and_does_not_leave_registry_ownership(self):
        request = self._request()
        request_path = self.evidence / "request.json"

        with mock.patch.object(
            windows_watch.subprocess,
            "Popen",
            side_effect=OSError("injected launch failure"),
        ) as launch:
            with self.assertRaisesRegex(WatchProtocolError, "injected launch failure"):
                start_watch(request)

        request_path = request_path.resolve(strict=True)
        self.assertEqual(
            (
                windows_watch.sys.executable,
                "-B",
                "-m",
                "modlab.validation.windows_watch",
                "--worker",
                str(request_path),
            ),
            launch.call_args.args[0],
        )
        self.assertIs(launch.call_args.kwargs["shell"], False)
        self.assertTrue((self.evidence / "owner.json").is_file())
        self.assertTrue(request_path.is_file())
        self.assertTrue((self.evidence / "events.ndjson").is_file())
        terminal = json.loads((self.evidence / "terminal.json").read_text(encoding="utf-8"))
        request_sha256 = hashlib.sha256(request_path.read_bytes()).hexdigest()
        self.assertEqual(request_sha256, terminal["requestBytesSha256"])
        self.assertIn("injected launch failure", terminal["error"])
        self.assertNotIn(request_path.absolute(), windows_watch._LOCAL_WORKERS)

    def test_request_publication_failure_is_durable_before_spawn(self):
        request = self._request()
        original_write = windows_watch._write_new

        def fail_request_publication(path: Path, data: bytes) -> None:
            if Path(path).name == "request.json":
                raise OSError("injected request publication failure")
            original_write(path, data)

        with mock.patch.object(
            windows_watch,
            "_write_new",
            side_effect=fail_request_publication,
        ), mock.patch.object(windows_watch.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(WatchProtocolError, "request publication failure"):
                start_watch(request)

        launch.assert_not_called()
        owner = json.loads((self.evidence / "owner.json").read_text(encoding="utf-8"))
        terminal = json.loads((self.evidence / "terminal.json").read_text(encoding="utf-8"))
        self.assertEqual("LaunchFailed", owner["state"])
        self.assertEqual(0, owner["workerPid"])
        self.assertIn("injected request publication failure", terminal["error"])
        self.assertNotIn((self.evidence / "request.json").absolute(), windows_watch._LOCAL_WORKERS)

    def test_request_resolution_failure_is_durable_before_spawn(self):
        request = self._request()
        original_write = windows_watch._write_new

        def remove_published_request(path: Path, data: bytes) -> None:
            original_write(path, data)
            if Path(path).name == "request.json":
                Path(path).unlink()

        with mock.patch.object(
            windows_watch,
            "_write_new",
            side_effect=remove_published_request,
        ), mock.patch.object(windows_watch.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(WatchProtocolError, "request publication failed"):
                start_watch(request)

        launch.assert_not_called()
        owner = json.loads((self.evidence / "owner.json").read_text(encoding="utf-8"))
        terminal = json.loads((self.evidence / "terminal.json").read_text(encoding="utf-8"))
        self.assertEqual("LaunchFailed", owner["state"])
        self.assertEqual(0, owner["workerPid"])
        self.assertIn("cannot find the file", terminal["error"].lower())

    def test_post_create_identity_failure_terminates_worker_and_is_durable(self):
        request = self._request()
        process = mock.Mock(pid=4242)
        process.wait.return_value = 17

        with mock.patch.object(windows_watch.subprocess, "Popen", return_value=process), mock.patch.object(
            windows_watch,
            "_open_process_identity",
            side_effect=OSError("injected identity failure"),
        ):
            with self.assertRaisesRegex(WatchProtocolError, "identity failure"):
                start_watch(request)

        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=15)
        owner = json.loads((self.evidence / "owner.json").read_text(encoding="utf-8"))
        terminal = json.loads((self.evidence / "terminal.json").read_text(encoding="utf-8"))
        self.assertEqual("LaunchFailed", owner["state"])
        self.assertIn("injected identity failure", terminal["error"])
        self.assertNotIn((self.evidence / "request.json").absolute(), windows_watch._LOCAL_WORKERS)

    def test_identity_unwind_close_failure_retains_exact_spawned_handle(self):
        request = self._request()
        request_path = self.evidence / "request.json"
        process = mock.Mock(pid=4242)
        process.wait.return_value = 17
        original_close = windows_watch._close_handle

        def fail_fake_identity_close(handle: int, label: str) -> str | None:
            if handle == 9876:
                return "unclosed handle: injected identity unwind"
            return original_close(handle, label)

        try:
            with mock.patch.object(
                windows_watch.subprocess,
                "Popen",
                return_value=process,
            ), mock.patch.object(
                windows_watch,
                "_open_process_identity",
                side_effect=windows_watch._HandleOwnershipError(
                    "injected identity unwind",
                    9876,
                ),
            ), mock.patch.object(
                windows_watch,
                "_close_handle",
                side_effect=fail_fake_identity_close,
            ):
                with self.assertRaisesRegex(WatchProtocolError, "identity unwind"):
                    start_watch(request)

            owner = json.loads((self.evidence / "owner.json").read_text(encoding="utf-8"))
            self.assertEqual("RecoveryRequired", owner["state"])
            self.assertEqual(4242, owner["workerPid"])
            self.assertEqual(0, owner["workerCreationTime"])
            retained = windows_watch._LOCAL_WORKERS[request_path.absolute()]
            self.assertIs(process, retained.process)
            self.assertEqual(9876, retained.process_handle)
        finally:
            with windows_watch._LOCAL_WORKERS_LOCK:
                windows_watch._LOCAL_WORKERS.pop(request_path.absolute(), None)

    def test_spawn_cleanup_explicitly_closes_identity_and_popen_handles(self):
        process = subprocess.Popen(
            [windows_watch.sys.executable, "-B", "-c", "pass"],
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        identity_handle, _ = windows_watch._open_process_identity(process.pid)

        cleanup_complete, errors, retained_identity, retained_popen = (
            windows_watch._cleanup_spawned_worker(process, identity_handle)
        )

        self.assertTrue(cleanup_complete, errors)
        self.assertEqual((), errors)
        self.assertEqual(0, retained_identity)
        self.assertEqual(0, retained_popen)
        self.assertTrue(process._handle.closed)

    def test_terminate_failure_with_proved_exit_is_durable_launch_failure(self):
        request = self._request()
        process = mock.Mock(pid=4242)
        process.terminate.side_effect = OSError("injected terminate failure")
        process.wait.return_value = 17

        with mock.patch.object(windows_watch.subprocess, "Popen", return_value=process), mock.patch.object(
            windows_watch,
            "_open_process_identity",
            side_effect=OSError("injected identity failure"),
        ):
            with self.assertRaisesRegex(WatchProtocolError, "terminate failure"):
                start_watch(request)

        owner = json.loads((self.evidence / "owner.json").read_text(encoding="utf-8"))
        terminal = json.loads((self.evidence / "terminal.json").read_text(encoding="utf-8"))
        self.assertEqual("LaunchFailed", owner["state"])
        self.assertEqual(0, owner["workerPid"])
        self.assertIn("injected terminate failure", terminal["error"])
        self.assertNotIn((self.evidence / "request.json").absolute(), windows_watch._LOCAL_WORKERS)

    def test_running_owner_promotion_failure_terminates_worker_and_is_durable(self):
        request = self._request()
        process = mock.Mock(pid=4242)
        process.wait.return_value = 17
        original_replace = windows_watch._replace_record
        original_close = windows_watch._close_handle
        rejected_running = False

        def fail_running_owner(path: Path, data: bytes) -> None:
            nonlocal rejected_running
            document = json.loads(data)
            if document.get("state") == "Running" and not rejected_running:
                rejected_running = True
                raise OSError("injected owner promotion failure")
            original_replace(path, data)

        def close_except_fake_process(handle: int, label: str) -> str | None:
            if handle == 9876:
                return None
            return original_close(handle, label)

        with mock.patch.object(windows_watch.subprocess, "Popen", return_value=process), mock.patch.object(
            windows_watch,
            "_open_process_identity",
            return_value=(9876, 123456789),
        ), mock.patch.object(
            windows_watch,
            "_replace_record",
            side_effect=fail_running_owner,
        ), mock.patch.object(
            windows_watch,
            "_close_handle",
            side_effect=close_except_fake_process,
        ) as close_handle:
            with self.assertRaisesRegex(WatchProtocolError, "owner promotion failure"):
                start_watch(request)

        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=15)
        self.assertIn(mock.call(9876, "worker process 4242"), close_handle.call_args_list)
        owner = json.loads((self.evidence / "owner.json").read_text(encoding="utf-8"))
        terminal = json.loads((self.evidence / "terminal.json").read_text(encoding="utf-8"))
        self.assertEqual("LaunchFailed", owner["state"])
        self.assertIn("injected owner promotion failure", terminal["error"])
        self.assertNotIn((self.evidence / "request.json").absolute(), windows_watch._LOCAL_WORKERS)

    def test_wait_timeout_and_kill_failure_preserve_exact_recovery_ownership(self):
        request = self._request()
        request_path = self.evidence / "request.json"
        process = mock.Mock(pid=4242)
        process.wait.side_effect = subprocess.TimeoutExpired("watch-worker", 15)
        process.kill.side_effect = OSError("injected kill failure")
        original_replace = windows_watch._replace_record
        original_close = windows_watch._close_handle
        rejected_running = False

        def fail_running_owner(path: Path, data: bytes) -> None:
            nonlocal rejected_running
            document = json.loads(data)
            if document.get("state") == "Running" and not rejected_running:
                rejected_running = True
                raise OSError("injected owner promotion failure")
            original_replace(path, data)

        def protect_fake_process_handle(handle: int, label: str) -> str | None:
            if handle == 9876:
                self.fail("uncertain cleanup must retain the exact process handle")
            return original_close(handle, label)

        try:
            with mock.patch.object(windows_watch.subprocess, "Popen", return_value=process), mock.patch.object(
                windows_watch,
                "_open_process_identity",
                return_value=(9876, 123456789),
            ), mock.patch.object(
                windows_watch,
                "_replace_record",
                side_effect=fail_running_owner,
            ), mock.patch.object(
                windows_watch,
                "_close_handle",
                side_effect=protect_fake_process_handle,
            ):
                with self.assertRaisesRegex(WatchProtocolError, "kill failure"):
                    start_watch(request)

            owner = json.loads((self.evidence / "owner.json").read_text(encoding="utf-8"))
            self.assertEqual("RecoveryRequired", owner["state"])
            self.assertEqual(4242, owner["workerPid"])
            self.assertEqual(123456789, owner["workerCreationTime"])
            self.assertIn("injected kill failure", owner["error"])
            retained = windows_watch._LOCAL_WORKERS[request_path.absolute()]
            self.assertIs(process, retained.process)
            self.assertEqual(9876, retained.process_handle)
        finally:
            with windows_watch._LOCAL_WORKERS_LOCK:
                windows_watch._LOCAL_WORKERS.pop(request_path.absolute(), None)

    def test_early_worker_exit_close_failure_preserves_running_ownership(self):
        request = self._request()
        request_path = self.evidence / "request.json"
        process = mock.Mock(pid=4242)
        process.poll.return_value = 19
        process.wait.return_value = 19
        original_close = windows_watch._close_handle

        def fail_fake_process_close(handle: int, label: str) -> str | None:
            if handle == 9876:
                return "unclosed handle: injected startup process handle"
            return original_close(handle, label)

        try:
            with mock.patch.object(windows_watch.subprocess, "Popen", return_value=process), mock.patch.object(
                windows_watch,
                "_open_process_identity",
                return_value=(9876, 123456789),
            ), mock.patch.object(
                windows_watch,
                "_close_handle",
                side_effect=fail_fake_process_close,
            ):
                with self.assertRaisesRegex(WatchProtocolError, "startup process handle"):
                    start_watch(request)

            owner = json.loads((self.evidence / "owner.json").read_text(encoding="utf-8"))
            self.assertEqual("RecoveryRequired", owner["state"])
            self.assertEqual(4242, owner["workerPid"])
            retained = windows_watch._LOCAL_WORKERS[request_path.absolute()]
            self.assertIs(process, retained.process)
            self.assertEqual(9876, retained.process_handle)
        finally:
            with windows_watch._LOCAL_WORKERS_LOCK:
                windows_watch._LOCAL_WORKERS.pop(request_path.absolute(), None)

    def test_ready_timeout_preserves_recovery_owner_when_exit_is_uncertain(self):
        request = self._request()
        request_path = self.evidence / "request.json"
        process = mock.Mock(pid=4242)
        process.wait.side_effect = subprocess.TimeoutExpired("watch-worker", 15)
        original_close = windows_watch._close_handle

        def protect_fake_process_handle(handle: int, label: str) -> str | None:
            if handle == 9876:
                self.fail("readiness timeout must retain the exact process handle")
            return original_close(handle, label)

        try:
            with mock.patch.object(windows_watch.subprocess, "Popen", return_value=process), mock.patch.object(
                windows_watch,
                "_open_process_identity",
                return_value=(9876, 123456789),
            ), mock.patch.object(
                windows_watch.time,
                "monotonic",
                side_effect=(0.0, 16.0),
            ), mock.patch.object(
                windows_watch,
                "_close_handle",
                side_effect=protect_fake_process_handle,
            ):
                with self.assertRaisesRegex(WatchProtocolError, "did not become ready or stop"):
                    start_watch(request)

            owner = json.loads((self.evidence / "owner.json").read_text(encoding="utf-8"))
            self.assertEqual("RecoveryRequired", owner["state"])
            self.assertEqual(4242, owner["workerPid"])
            self.assertEqual(123456789, owner["workerCreationTime"])
            self.assertIn(request_path.absolute(), windows_watch._LOCAL_WORKERS)
        finally:
            with windows_watch._LOCAL_WORKERS_LOCK:
                windows_watch._LOCAL_WORKERS.pop(request_path.absolute(), None)

    def test_concurrent_starts_have_one_atomic_owner_and_one_launch_attempt(self):
        request = self._request()
        errors: list[BaseException] = []
        entered = threading.Event()
        release = threading.Event()

        def failing_launch(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(5.0))
            raise OSError("injected launch failure")

        def invoke_start() -> None:
            try:
                start_watch(request)
            except BaseException as error:
                errors.append(error)

        with mock.patch.object(windows_watch.subprocess, "Popen", side_effect=failing_launch) as launch:
            first = threading.Thread(target=invoke_start)
            first.start()
            self.assertTrue(entered.wait(5.0))
            second = threading.Thread(target=invoke_start)
            second.start()
            second.join(5.0)
            release.set()
            first.join(5.0)

        self.assertEqual(1, launch.call_count)
        self.assertEqual(2, len(errors))
        self.assertTrue(any("owner claim" in str(error) for error in errors), errors)
        self.assertTrue(any("injected launch failure" in str(error) for error in errors), errors)

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
        self.assertRegex(receipt.error, "root membership changed|root identity changed")

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
        self.assertIn("root membership changed during watch", receipt.error)

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
        self.assertIn("root membership changed during watch", receipt.error)

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
        self.assertIn("root membership changed during watch", receipt.error)

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

    def test_missing_request_record_still_stops_exact_registered_worker(self):
        request_path, worker_pid = self._start()
        request_bytes = request_path.read_bytes()
        request_path.unlink()
        try:
            receipt = stop_watch(request_path)
        finally:
            request_path.write_bytes(request_bytes)

        self.assertEqual(worker_pid, receipt.worker_pid)
        self.assertFalse(receipt.complete)
        self.assertTrue((self.evidence / "stop.token").is_file())
        self.assertIn("request record unavailable during stop", receipt.error)

        recovered = stop_watch(request_path)
        self.assertTrue(recovered.complete, recovered.error)
        owner = json.loads((self.evidence / "owner.json").read_text(encoding="utf-8"))
        self.assertEqual("Completed", owner["state"])

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
                self.assertIn(message, receipt.error)

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
                self.assertIn(message, receipt.error)

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
                self.assertIn("schema version", receipt.error)

    def test_root_a_receipt_replayed_for_root_b_is_incomplete(self):
        root_a = self.root / "receipt-a"
        root_b = self.root / "receipt-b"
        root_a.mkdir()
        root_b.mkdir()
        self._receipt_fixture(
            root_a,
            event_bytes=b"",
            complete=True,
            terminal_error=None,
            open_handle_count=0,
        )
        for name in ("owner.json", "ready.json", "events.ndjson", "terminal.json"):
            (root_b / name).write_bytes((root_a / name).read_bytes())
        request_b = replace(
            self._request(),
            evidence_root=root_b,
            stop_token_path=root_b / "stop.token",
        )

        receipt = watch_receipt_from_files(
            request_b,
            os.getpid(),
            root_b / "ready.json",
            root_b / "events.ndjson",
            root_b / "terminal.json",
        )

        self.assertFalse(receipt.complete)
        self.assertIn("request SHA-256 mismatch", receipt.error)

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
                self.assertRegex(receipt.error, "journal identity|byte count|SHA-256|malformed")

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
                self.assertRegex(receipt.error, message)

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
                self.assertIn("rename pair", receipt.error)

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
        self.assertIn("complete terminal requires ready", receipt.error)

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
        self.assertIn("malformed ready", receipt.error)

    def test_malformed_owner_still_reaps_exact_registered_worker(self):
        request_path, worker_pid = self._start()
        (self.evidence / "owner.json").write_bytes(b"not-json\n")

        receipt = stop_watch(request_path)

        self.assertEqual(worker_pid, receipt.worker_pid)
        self.assertFalse(receipt.complete)
        self.assertIn("owner record unavailable", receipt.error)
        self.assertNotIn(request_path.absolute(), windows_watch._LOCAL_WORKERS)

    def test_pid_creation_time_mismatch_is_incomplete(self):
        case_root = self.root / "pid-mismatch"
        case_root.mkdir()
        self._receipt_fixture(
            case_root,
            event_bytes=b"",
            complete=True,
            terminal_error=None,
            open_handle_count=0,
        )
        owner_path = case_root / "owner.json"
        owner = json.loads(owner_path.read_text(encoding="utf-8"))
        owner["state"] = "Running"
        owner["workerCreationTime"] += 1
        owner_path.write_bytes(_canonical(owner))

        receipt = stop_watch(case_root / "request.json")

        self.assertFalse(receipt.complete)
        self.assertIn("creation-time identity mismatch", receipt.error)

    def test_recovery_required_owner_cannot_reconstruct_complete_receipt(self):
        case_root = self.root / "recovery-required-owner"
        case_root.mkdir()
        self._receipt_fixture(
            case_root,
            event_bytes=b"",
            complete=True,
            terminal_error=None,
            open_handle_count=0,
        )
        owner_path = case_root / "owner.json"
        owner = json.loads(owner_path.read_text(encoding="utf-8"))
        owner["state"] = "RecoveryRequired"
        owner["error"] = "injected retained controller ownership"
        owner_path.write_bytes(_canonical(owner))
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
        self.assertIn("injected retained controller ownership", receipt.error)

    def test_running_owner_cannot_reconstruct_complete_receipt(self):
        case_root = self.root / "running-owner"
        case_root.mkdir()
        self._receipt_fixture(
            case_root,
            event_bytes=b"",
            complete=True,
            terminal_error=None,
            open_handle_count=0,
        )
        owner_path = case_root / "owner.json"
        owner = json.loads(owner_path.read_text(encoding="utf-8"))
        owner["state"] = "Running"
        owner_path.write_bytes(_canonical(owner))
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
        self.assertIn("owner state is not completed: Running", receipt.error)

    def test_terminal_record_does_not_substitute_for_process_exit(self):
        case_root = self.root / "terminal-before-exit"
        case_root.mkdir()
        self._receipt_fixture(
            case_root,
            event_bytes=b"",
            complete=True,
            terminal_error=None,
            open_handle_count=0,
        )
        owner_path = case_root / "owner.json"
        owner = json.loads(owner_path.read_text(encoding="utf-8"))
        owner["state"] = "Running"
        owner_path.write_bytes(_canonical(owner))

        with mock.patch.object(windows_watch, "_wait_process_handle", return_value=False):
            receipt = stop_watch(case_root / "request.json")

        self.assertFalse(receipt.complete)
        self.assertIn("did not stop", receipt.error)

    def test_controller_process_handle_close_failure_retains_ownership_and_is_incomplete(self):
        request_path, worker_pid = self._start()
        original_close = windows_watch._close_handle
        injected = False

        def fail_controller_process_close(handle: int, label: str) -> str | None:
            nonlocal injected
            if label == f"worker process {worker_pid}" and not injected:
                injected = True
                return "unclosed handle: injected controller process handle"
            return original_close(handle, label)

        with mock.patch.object(
            windows_watch,
            "_close_handle",
            side_effect=fail_controller_process_close,
        ):
            receipt = stop_watch(request_path)

        self.assertTrue(injected)
        self.assertFalse(receipt.complete)
        self.assertIn("injected controller process handle", receipt.error)
        self.assertIn(request_path.absolute(), windows_watch._LOCAL_WORKERS)
        owner = json.loads((self.evidence / "owner.json").read_text(encoding="utf-8"))
        self.assertEqual("RecoveryRequired", owner["state"])
        self.assertEqual(worker_pid, owner["workerPid"])

        request = windows_watch._load_request_path(request_path)
        reconstructed = watch_receipt_from_files(
            request,
            worker_pid,
            self.evidence / "ready.json",
            self.evidence / "events.ndjson",
            self.evidence / "terminal.json",
        )
        self.assertFalse(reconstructed.complete)
        self.assertIn("injected controller process handle", reconstructed.error)

        recovered = stop_watch(request_path)
        self.assertTrue(recovered.complete, recovered.error)
        self.assertNotIn(request_path.absolute(), windows_watch._LOCAL_WORKERS)
        owner = json.loads((self.evidence / "owner.json").read_text(encoding="utf-8"))
        self.assertEqual("Completed", owner["state"])

    def test_local_popen_handle_close_failure_is_durable_and_retryable(self):
        request_path, worker_pid = self._start()
        original_close = windows_watch._close_handle
        retained_popen_handle = 0

        def fail_popen_close_once(handle: int, label: str) -> str | None:
            nonlocal retained_popen_handle
            if label == f"local Popen process {worker_pid}" and not retained_popen_handle:
                retained_popen_handle = handle
                return "unclosed handle: injected local Popen process"
            return original_close(handle, label)

        with mock.patch.object(
            windows_watch,
            "_close_handle",
            side_effect=fail_popen_close_once,
        ):
            receipt = stop_watch(request_path)

        self.assertFalse(receipt.complete)
        self.assertIn("injected local Popen process", receipt.error)
        owner = json.loads((self.evidence / "owner.json").read_text(encoding="utf-8"))
        self.assertEqual("RecoveryRequired", owner["state"])
        retained = windows_watch._LOCAL_WORKERS[request_path.absolute()]
        self.assertEqual(0, retained.process_handle)
        self.assertEqual(retained_popen_handle, retained.popen_handle)

        recovered = stop_watch(request_path)
        self.assertTrue(recovered.complete, recovered.error)
        owner = json.loads((self.evidence / "owner.json").read_text(encoding="utf-8"))
        self.assertEqual("Completed", owner["state"])
        self.assertNotIn(request_path.absolute(), windows_watch._LOCAL_WORKERS)

    def test_concurrent_stop_calls_never_close_one_native_handle_twice(self):
        request_path, _ = self._start()
        original_close = windows_watch._close_handle
        original_create_mutex = windows_watch._kernel32.CreateMutexW
        observed_process_handles: list[int] = []
        observed_mutex_names: list[str] = []
        observed_lock = threading.Lock()
        barrier = threading.Barrier(3)
        receipts: list[WatchReceipt] = []

        def track_process_close(handle: int, label: str) -> str | None:
            if label.startswith(("worker process ", "local Popen process ")):
                with observed_lock:
                    self.assertNotIn(handle, observed_process_handles)
                    observed_process_handles.append(handle)
            return original_close(handle, label)

        def stop_concurrently() -> None:
            barrier.wait()
            receipts.append(stop_watch(request_path))

        def track_mutex(security, initial_owner, name):
            with observed_lock:
                observed_mutex_names.append(name)
            return original_create_mutex(security, initial_owner, name)

        threads = [threading.Thread(target=stop_concurrently) for _ in range(2)]
        with mock.patch.object(
            windows_watch,
            "_close_handle",
            side_effect=track_process_close,
        ), mock.patch.object(
            windows_watch._kernel32,
            "CreateMutexW",
            side_effect=track_mutex,
        ):
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join()

        self.assertEqual(2, len(receipts))
        self.assertTrue(all(receipt.complete for receipt in receipts), receipts)
        self.assertEqual(2, len(observed_process_handles))
        request_sha256 = hashlib.sha256(request_path.read_bytes()).hexdigest()
        self.assertEqual(
            [f"Local\\ModLab-Watch-Stop-{request_sha256}"] * 2,
            observed_mutex_names,
        )

    def test_completed_publication_failure_retries_from_cleanup_complete(self):
        request_path, _ = self._start()
        original_replace = windows_watch._replace_owner_state
        injected = False

        def fail_completed_once(*args, **kwargs):
            nonlocal injected
            if kwargs.get("state") == "Completed" and not injected:
                injected = True
                raise OSError("injected completed publication failure")
            return original_replace(*args, **kwargs)

        with mock.patch.object(
            windows_watch,
            "_replace_owner_state",
            side_effect=fail_completed_once,
        ):
            failed = stop_watch(request_path)

        self.assertFalse(failed.complete)
        self.assertIn("completed publication failure", failed.error)
        owner = json.loads((self.evidence / "owner.json").read_text(encoding="utf-8"))
        self.assertEqual("CleanupComplete", owner["state"])
        self.assertNotIn(request_path.absolute(), windows_watch._LOCAL_WORKERS)

        recovered = stop_watch(request_path)
        self.assertTrue(recovered.complete, recovered.error)

    def test_stop_retains_failed_journal_readback_close_for_retry(self):
        request_path, _ = self._start()
        original_close = windows_watch._close_handle
        injected = False

        def fail_readback_once(handle: int, label: str) -> str | None:
            nonlocal injected
            if label == "event journal readback" and not injected:
                injected = True
                return "unclosed handle: injected stop journal readback"
            return original_close(handle, label)

        with mock.patch.object(
            windows_watch,
            "_close_handle",
            side_effect=fail_readback_once,
        ):
            failed = stop_watch(request_path)

        self.assertFalse(failed.complete)
        self.assertIn("retained an unclosed handle", failed.error)
        self.assertIn(self.evidence.absolute(), windows_watch._RETAINED_AUXILIARY_HANDLES)
        owner = json.loads((self.evidence / "owner.json").read_text(encoding="utf-8"))
        self.assertEqual("RecoveryRequired", owner["state"])
        retained = windows_watch._LOCAL_WORKERS[request_path.absolute()]
        self.assertTrue(retained.cleanup_complete)

        recovered = stop_watch(request_path)
        self.assertTrue(recovered.complete, recovered.error)
        self.assertNotIn(self.evidence.absolute(), windows_watch._RETAINED_AUXILIARY_HANDLES)

    def test_retained_handle_establishes_previously_unknown_creation_time(self):
        request_path, worker_pid = self._start()
        retained = windows_watch._LOCAL_WORKERS[request_path.absolute()]
        retained.process_creation_time = 0
        owner_path = self.evidence / "owner.json"
        owner = json.loads(owner_path.read_text(encoding="utf-8"))
        owner["state"] = "RecoveryRequired"
        owner["workerCreationTime"] = 0
        owner["error"] = "injected unknown creation time"
        owner_path.write_bytes(_canonical(owner))

        receipt = stop_watch(request_path)

        self.assertTrue(receipt.complete, receipt.error)
        owner = json.loads(owner_path.read_text(encoding="utf-8"))
        self.assertEqual("Completed", owner["state"])
        self.assertEqual(worker_pid, owner["workerPid"])
        self.assertGreater(owner["workerCreationTime"], 0)

    def test_reopened_process_handle_close_failure_is_retained_and_incomplete(self):
        case_root = self.root / "reopened-handle-close"
        case_root.mkdir()
        self._receipt_fixture(
            case_root,
            event_bytes=b"",
            complete=True,
            terminal_error=None,
            open_handle_count=0,
        )
        owner_path = case_root / "owner.json"
        owner = json.loads(owner_path.read_text(encoding="utf-8"))
        owner["state"] = "Running"
        owner_path.write_bytes(_canonical(owner))
        request_path = case_root / "request.json"
        worker_pid = os.getpid()
        original_close = windows_watch._close_handle
        retained_handle = 0

        def fail_reopened_process_close(handle: int, label: str) -> str | None:
            nonlocal retained_handle
            if label == f"worker process {worker_pid}":
                retained_handle = handle
                return "unclosed handle: injected reopened process handle"
            return original_close(handle, label)

        try:
            with mock.patch.object(
                windows_watch,
                "_wait_process_handle",
                return_value=True,
            ), mock.patch.object(
                windows_watch,
                "_close_handle",
                side_effect=fail_reopened_process_close,
            ):
                receipt = stop_watch(request_path)

            self.assertFalse(receipt.complete)
            self.assertIn("injected reopened process handle", receipt.error)
            retained = windows_watch._LOCAL_WORKERS[request_path.absolute()]
            self.assertIsNone(retained.process)
            self.assertEqual(retained_handle, retained.process_handle)

            owner = json.loads((case_root / "owner.json").read_text(encoding="utf-8"))
            self.assertEqual("RecoveryRequired", owner["state"])
            reconstructed = watch_receipt_from_files(
                retained.request,
                worker_pid,
                case_root / "ready.json",
                case_root / "events.ndjson",
                case_root / "terminal.json",
            )
            self.assertFalse(reconstructed.complete)

            with mock.patch.object(
                windows_watch,
                "_wait_process_handle",
                return_value=True,
            ):
                recovered = stop_watch(request_path)
            self.assertTrue(recovered.complete, recovered.error)
            owner = json.loads((case_root / "owner.json").read_text(encoding="utf-8"))
            self.assertEqual("Completed", owner["state"])
            retained_handle = 0
        finally:
            if retained_handle:
                original_close(retained_handle, "test reopened process cleanup")
            with windows_watch._LOCAL_WORKERS_LOCK:
                windows_watch._LOCAL_WORKERS.pop(request_path.absolute(), None)

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

    def test_receipt_retries_a_retained_journal_readback_handle(self):
        case_root = self.root / "receipt-readback-retry"
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
        worker_pid = os.getpid()
        original_close = windows_watch._close_handle
        injected = False

        def fail_readback_close_once(handle: int, label: str) -> str | None:
            nonlocal injected
            if label == "event journal readback" and not injected:
                injected = True
                return "unclosed handle: injected receipt readback"
            return original_close(handle, label)

        with mock.patch.object(
            windows_watch,
            "_close_handle",
            side_effect=fail_readback_close_once,
        ):
            failed = watch_receipt_from_files(
                request,
                worker_pid,
                case_root / "ready.json",
                case_root / "events.ndjson",
                case_root / "terminal.json",
            )

        self.assertFalse(failed.complete)
        self.assertIn("retained an unclosed handle", failed.error)
        self.assertIn(case_root.absolute(), windows_watch._RETAINED_AUXILIARY_HANDLES)

        recovered = watch_receipt_from_files(
            request,
            worker_pid,
            case_root / "ready.json",
            case_root / "events.ndjson",
            case_root / "terminal.json",
        )
        self.assertTrue(recovered.complete, recovered.error)
        self.assertNotIn(case_root.absolute(), windows_watch._RETAINED_AUXILIARY_HANDLES)

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
        retained_handles: list[int] = []

        def fail_identity_close(handle: int, label: str) -> str | None:
            if label == f"worker process {os.getpid()}":
                retained_handles.append(handle)
                return "unclosed handle: injected worker identity unwind"
            return original_close(handle, label)

        try:
            with mock.patch.object(
                windows_watch,
                "_process_handle_creation_time",
                side_effect=WatchProtocolError("injected worker identity inspection"),
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
        self.assertIn("worker death", receipt.error)

    def test_thread_start_failure_closes_opened_handles_and_writes_terminal(self):
        case_root = self.root / "thread-start-failure"
        case_root.mkdir()
        root = watch_root("SourceMods", self.watched)
        request = WatchRequest(
            request_id="watch-request:" + "c" * 64,
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
        (case_root / "owner.json").write_bytes(
            _canonical(
                {
                    "error": None,
                    "ownerToken": "2" * 64,
                    "requestBytesSha256": request_sha256,
                    "requestId": request.request_id,
                    "schemaVersion": 1,
                    "state": "Running",
                    "workerCreationTime": worker_creation_time,
                    "workerPid": os.getpid(),
                }
            )
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
        self.assertIn("thread start failure", receipt.error)
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

    def test_receipt_normalization_access_failure_returns_incomplete(self):
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
        self.assertIn("injected evidence access denied", receipt.error)

    def test_stop_returns_incomplete_when_evidence_root_vanishes_after_process_cleanup(self):
        request_path, worker_pid = self._start()
        original_close = windows_watch._close_handle
        removed = False

        def close_then_remove_evidence(handle: int, label: str) -> str | None:
            nonlocal removed
            result = original_close(handle, label)
            if label == f"worker process {worker_pid}" and result is None and not removed:
                shutil.rmtree(self.evidence)
                removed = True
            return result

        with mock.patch.object(
            windows_watch,
            "_close_handle",
            side_effect=close_then_remove_evidence,
        ):
            receipt = stop_watch(request_path)

        self.assertTrue(removed)
        self.assertFalse(receipt.complete)
        self.assertIn("evidence", receipt.error)
        self.assertNotIn(request_path.absolute(), windows_watch._LOCAL_WORKERS)

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

    def test_events_and_manifests_are_independent_required_proofs(self):
        clean = WatchReceipt(
            request_id="watch-request:" + "b" * 64,
            worker_pid=os.getpid(),
            complete=True,
            ready=True,
            opened_root_kinds=windows_watch.ROOT_KINDS,
            events=(),
            event_bytes_sha256=hashlib.sha256(b"").hexdigest(),
            error=None,
            request_bytes_sha256="4" * 64,
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
        self.assertFalse(watch_proves_unchanged(clean, "partial", "partial"))
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
                replace(clean, request_bytes_sha256=""),
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
            evidence_root=case_root,
            stop_token_path=case_root / "stop.token",
            roots=roots,
        )
        request_bytes, request_sha256 = windows_watch._request_bytes_and_sha256(request)
        request_path = case_root / "request.json"
        request_path.write_bytes(request_bytes)
        process_handle, creation_time = windows_watch._open_process_identity(os.getpid())
        self.assertIsNone(windows_watch._close_handle(process_handle, "direct worker process"))
        (case_root / "owner.json").write_bytes(
            _canonical(
                {
                    "error": None,
                    "ownerToken": "3" * 64,
                    "requestBytesSha256": request_sha256,
                    "requestId": request.request_id,
                    "schemaVersion": 1,
                    "state": "Running",
                    "workerCreationTime": creation_time,
                    "workerPid": os.getpid(),
                }
            )
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
        request_bytes = _canonical(
            {
                "evidenceRoot": str(request.evidence_root),
                "requestId": request.request_id,
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
        )
        request_sha256 = hashlib.sha256(request_bytes).hexdigest()
        (case_root / "request.json").write_bytes(request_bytes)
        (case_root / "owner.json").write_bytes(
            _canonical(
                {
                    "error": None,
                    "ownerToken": "1" * 64,
                    "requestBytesSha256": request_sha256,
                    "requestId": request.request_id,
                    "schemaVersion": 1,
                    "state": "Completed",
                    "workerCreationTime": worker_creation_time,
                    "workerPid": worker_pid,
                }
            )
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
