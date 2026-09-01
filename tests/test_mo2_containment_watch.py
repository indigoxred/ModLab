import ctypes
from ctypes import wintypes
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
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
            roots = (watch_root("SourceMods", self.watched),)
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
            if event.relative_path in {"nested/probe.txt", "nested/renamed.txt"}
        )
        self.assertEqual(
            ("Added", "Modified", "RenamedOld", "RenamedNew", "Removed"),
            selected,
        )
        self.assertEqual(tuple(range(1, len(receipt.events) + 1)), tuple(e.sequence for e in receipt.events))
        self.assertTrue(all(type(event) is WatcherEvent for event in receipt.events))

    def test_intentional_cancel_completes_without_an_overflow(self):
        request_path, worker_pid = self._start()

        receipt = stop_watch(request_path)

        self.assertEqual(worker_pid, receipt.worker_pid)
        self.assertTrue(receipt.ready)
        self.assertTrue(receipt.complete, receipt.error)
        self.assertEqual((), receipt.events)
        self.assertIsNone(receipt.error)

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

    def test_equal_root_identities_are_serialized_once_and_keep_first_kind(self):
        first = watch_root("SourceMods", self.watched)
        duplicate = replace(first, root_kind="ExternalTempLow")
        request_path, _ = self._start(first, duplicate)

        receipt = stop_watch(request_path)
        request_document = json.loads(request_path.read_text(encoding="utf-8"))

        self.assertTrue(receipt.complete, receipt.error)
        self.assertEqual(("SourceMods",), receipt.opened_root_kinds)
        self.assertEqual(1, len(request_document["roots"]))
        self.assertEqual("SourceMods", request_document["roots"][0]["rootKind"])

    def test_serialized_request_rejects_duplicate_root_identities(self):
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
                for kind in ("SourceMods", "ExternalTempLow")
            ],
            "schemaVersion": 1,
            "stopTokenPath": str(self.evidence / "stop.token"),
        }

        with self.assertRaisesRegex(WatchProtocolError, "serialized roots.*unique identities"):
            windows_watch._request_from_bytes(_canonical(document))

    def test_root_path_replacement_after_ready_is_incomplete(self):
        request_path, _ = self._start()
        moved = self.root / "watched-original"

        self.watched.rename(moved)
        self.watched.mkdir()
        receipt = stop_watch(request_path)

        self.assertFalse(receipt.complete)
        self.assertIn("root identity changed", receipt.error)

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
            roots=(root,),
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
                            "rootKind": root.root_kind,
                            "volumeSerial": root.volume_serial,
                        }
                    ],
                    "schemaVersion": 1,
                    "stopTokenPath": str(case_root / "stop.token"),
                }
            )
        )
        (case_root / "events.ndjson").write_bytes(b"")

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
            opened_root_kinds=("SourceMods",),
            events=(),
            event_bytes_sha256=hashlib.sha256(b"").hexdigest(),
            error=None,
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
        ready_path.write_bytes(
            _canonical(
                {
                    "openedRootKinds": ["SourceMods"],
                    "requestId": request.request_id,
                    "schemaVersion": 1,
                    "workerPid": worker_pid,
                }
            )
        )
        events_path.write_bytes(event_bytes)
        terminal_path.write_bytes(
            _canonical(
                {
                    "complete": complete,
                    "error": terminal_error,
                    "eventBytesSha256": hashlib.sha256(event_bytes).hexdigest(),
                    "openHandleCount": open_handle_count,
                    "openedRootKinds": ["SourceMods"],
                    "ready": True,
                    "requestId": request.request_id,
                    "rootIdentitiesUnchanged": True,
                    "schemaVersion": 1,
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
