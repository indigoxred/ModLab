import hashlib
import json
import os
from dataclasses import fields, replace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from modlab.platform import windows_exact_fs
from modlab.validation import windows_watch_protocol
from modlab.platform.windows_exact_fs import (
    ExactObjectOwnershipError,
    PinnedIdentity,
    PinnedObject,
)
from modlab.validation.mo2_containment_model import (
    ContainmentScenario,
    WatchEvidenceCompletion,
    WatcherEvent,
)
from modlab.validation.windows_watch_protocol import (
    CLAIM_NAME,
    CONTROLLER_LOSS_NAME,
    EVENTS_NAME,
    LAUNCH_NAME,
    OUTCOME_NAME,
    READY_NAME,
    REQUEST_NAME,
    ROOT_KINDS,
    STOP_NAME,
    TERMINAL_NAME,
    ControllerClaim,
    ControllerLoss,
    WatchProtocolError,
    WatchProtocolOwnershipError,
    WatchReceipt,
    WatchRequest,
    WatchRoot,
    WorkerLaunch,
    controller_claim_from_bytes,
    controller_claim_to_bytes,
    controller_loss_from_bytes,
    controller_loss_to_bytes,
    publish_new_verified,
    watch_request_from_bytes,
    watch_request_sha256,
    watch_request_to_bytes,
    watch_worker_command,
    worker_launch_from_bytes,
    worker_launch_to_bytes,
)


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


class WatchProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="modlab-watch-protocol-")
        self.root = Path(self.temporary.name)
        self.evidence = self.root / "evidence"
        self.watched = self.root / "watched"
        self.evidence.mkdir()
        self.watched.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_publication_ownership_error_preserves_each_explicit_role(self) -> None:
        for candidate_live, parent_live in ((True, False), (False, True), (True, True)):
            candidate = PinnedObject(self.evidence / "record.json", 11 if candidate_live else 0, PinnedIdentity(1, 1, 0))
            parent = PinnedObject(self.evidence, 12 if parent_live else 0, PinnedIdentity(1, 2, 0x10))
            ownership = ExactObjectOwnershipError(
                "injected ownership", candidate=candidate if candidate_live else None,
                destination_parent=parent if parent_live else None,
            )
            with mock.patch.object(
                windows_watch_protocol, "publish_new_pinned", side_effect=ownership
            ):
                with self.assertRaises(WatchProtocolOwnershipError) as raised:
                    publish_new_verified(self.evidence / "record.json", b"{}\n", lambda value: value)
            self.assertIs(ownership, raised.exception.ownership)
            self.assertIs(candidate if candidate_live else None, raised.exception.candidate)
            self.assertIs(parent if parent_live else None, raised.exception.destination_parent)

    def test_publication_ownership_error_preserves_the_full_owner_collection(self) -> None:
        candidates = (
            PinnedObject(self.evidence / "one.json", 21, PinnedIdentity(1, 1, 0)),
            PinnedObject(self.evidence / "two.json", 22, PinnedIdentity(1, 2, 0)),
        )
        owner_type = windows_exact_fs.RetainedObjectOwner
        role = windows_exact_fs.RetainedObjectRole
        ownership = ExactObjectOwnershipError(
            "injected ownership",
            owners=tuple(owner_type(role.CANDIDATE, candidate) for candidate in candidates),
        )
        with mock.patch.object(
            windows_watch_protocol,
            "publish_new_pinned",
            side_effect=ownership,
        ):
            with self.assertRaises(WatchProtocolOwnershipError) as raised:
                publish_new_verified(
                    self.evidence / "record.json",
                    b"{}\n",
                    lambda value: value,
                )

        self.assertEqual(ownership.owners, raised.exception.owners)
        self.assertEqual(candidates, raised.exception.candidates)

    def request(self) -> WatchRequest:
        roots = tuple(
            WatchRoot(kind, self.watched, 7, 8)
            for kind in ROOT_KINDS
        )
        return WatchRequest(
            request_id="watch-request:" + "1" * 64,
            session_id="watch-session:" + "2" * 64,
            run_id="containment-run:0123456789abcdef0123456789abcdef",
            scenario=ContainmentScenario.MERGE_EXISTING,
            evidence_root=self.evidence,
            stop_token_path=self.evidence / STOP_NAME,
            roots=roots,
        )

    def claim(self, request: WatchRequest) -> ControllerClaim:
        request_path = request.evidence_root / REQUEST_NAME
        return ControllerClaim(
            schema_version=1,
            request_sha256=watch_request_sha256(request),
            session_id=request.session_id,
            run_id=request.run_id,
            scenario=request.scenario,
            request_path=request_path,
            worker_command=watch_worker_command(request_path),
            controller_pid=41,
            controller_creation_time=1001,
        )

    def launch(self, request: WatchRequest) -> WorkerLaunch:
        return WorkerLaunch(
            schema_version=1,
            request_sha256=watch_request_sha256(request),
            session_id=request.session_id,
            run_id=request.run_id,
            scenario=request.scenario,
            worker_pid=42,
            worker_creation_time=1002,
        )

    def loss(self, request: WatchRequest) -> ControllerLoss:
        claim = self.claim(request)
        launch = self.launch(request)
        return ControllerLoss(
            schema_version=1,
            request_sha256=watch_request_sha256(request),
            session_id=request.session_id,
            run_id=request.run_id,
            scenario=request.scenario,
            controller_pid=claim.controller_pid,
            controller_creation_time=claim.controller_creation_time,
            worker_pid=launch.worker_pid,
            worker_creation_time=launch.worker_creation_time,
            reason_code="controller-session-lost",
        )

    def test_fixed_protocol_names_are_unambiguous(self):
        self.assertEqual(
            (
                "request.json",
                "controller-claim.json",
                "worker-launch.json",
                "ready.json",
                "events.ndjson",
                "terminal.json",
                "controller-loss.json",
                "outcome.json",
                "stop.token",
            ),
            (
                REQUEST_NAME,
                CLAIM_NAME,
                LAUNCH_NAME,
                READY_NAME,
                EVENTS_NAME,
                TERMINAL_NAME,
                CONTROLLER_LOSS_NAME,
                OUTCOME_NAME,
                STOP_NAME,
            ),
        )

    def test_request_round_trips_with_all_bindings_and_aliases(self):
        value = self.request()
        encoded = watch_request_to_bytes(value)
        document = json.loads(encoded)

        self.assertEqual(value, watch_request_from_bytes(encoded))
        self.assertEqual(hashlib.sha256(encoded).hexdigest(), watch_request_sha256(value))
        self.assertEqual(value.session_id, document["sessionId"])
        self.assertEqual(value.run_id, document["runId"])
        self.assertEqual(value.scenario.value, document["scenario"])
        self.assertEqual(list(ROOT_KINDS), [item["rootKind"] for item in document["roots"]])
        self.assertEqual(1, len({(item["volumeSerial"], item["fileId"]) for item in document["roots"]}))

    def test_request_rejects_wrong_types_paths_coverage_and_noncanonical_json(self):
        value = self.request()
        invalid = (
            replace(value, session_id="watch-session:bad"),
            replace(value, run_id="containment-run:bad"),
            replace(value, stop_token_path=self.root / STOP_NAME),
            replace(value, roots=value.roots[:-1]),
            replace(value, roots=value.roots[::-1]),
            replace(value, roots=(replace(value.roots[0], volume_serial=True), *value.roots[1:])),
            replace(value, evidence_root=str(self.evidence)),
        )
        for item in invalid:
            with self.subTest(item=item):
                with self.assertRaises(WatchProtocolError):
                    watch_request_to_bytes(item)

        document = json.loads(watch_request_to_bytes(value))
        document["unexpected"] = True
        with self.assertRaisesRegex(WatchProtocolError, "fields"):
            watch_request_from_bytes(_canonical(document))
        pretty = json.dumps(json.loads(watch_request_to_bytes(value)), indent=2).encode()
        with self.assertRaisesRegex(WatchProtocolError, "canonical JSON"):
            watch_request_from_bytes(pretty)
        duplicate = watch_request_to_bytes(value).replace(
            b'"schemaVersion":1,',
            b'"schemaVersion":1,"schemaVersion":1,',
        )
        with self.assertRaisesRegex(WatchProtocolError, "duplicate JSON key"):
            watch_request_from_bytes(duplicate)

    def test_claim_launch_and_loss_round_trip_only_with_exact_request_binding(self):
        request = self.request()
        claim = self.claim(request)
        launch = self.launch(request)
        loss = self.loss(request)

        self.assertEqual(
            claim,
            controller_claim_from_bytes(controller_claim_to_bytes(claim, request), request),
        )
        self.assertEqual(
            launch,
            worker_launch_from_bytes(worker_launch_to_bytes(launch, request), request),
        )
        self.assertEqual(
            loss,
            controller_loss_from_bytes(
                controller_loss_to_bytes(loss, request, claim, launch),
                request,
                claim,
                launch,
            ),
        )

        other_session = replace(request, session_id="watch-session:" + "9" * 64)
        with self.assertRaises(WatchProtocolError):
            controller_claim_to_bytes(claim, other_session)
        with self.assertRaises(WatchProtocolError):
            worker_launch_to_bytes(launch, other_session)
        with self.assertRaises(WatchProtocolError):
            controller_loss_to_bytes(loss, other_session, claim, launch)
        with self.assertRaisesRegex(WatchProtocolError, "reason"):
            controller_loss_to_bytes(
                replace(loss, reason_code="controller-timeout"),
                request,
                claim,
                launch,
            )

    def test_claim_binds_canonical_request_path_and_exact_worker_command(self):
        request = self.request()
        claim = self.claim(request)
        document = json.loads(controller_claim_to_bytes(claim, request))
        request_path = request.evidence_root / REQUEST_NAME
        expected_command = (
            sys.executable,
            "-B",
            "-m",
            "modlab.validation.windows_watch",
            "--worker",
            str(request_path),
        )

        self.assertEqual(str(request_path), document.get("requestPath"))
        self.assertEqual(list(expected_command), document.get("workerCommand"))

        path_replay = dict(document)
        path_replay["requestPath"] = str(self.root / "replayed" / REQUEST_NAME)
        with self.assertRaisesRegex(WatchProtocolError, "request path"):
            controller_claim_from_bytes(_canonical(path_replay), request)

        command_replay = dict(document)
        command_replay["workerCommand"] = [
            *expected_command[:-2],
            "--replayed-worker",
            expected_command[-1],
        ]
        with self.assertRaisesRegex(WatchProtocolError, "worker command"):
            controller_claim_from_bytes(_canonical(command_replay), request)

    def test_record_schemas_reject_boolean_integer_and_extra_field(self):
        request = self.request()
        claim = self.claim(request)
        with self.assertRaises(WatchProtocolError):
            controller_claim_to_bytes(replace(claim, controller_pid=True), request)

        document = json.loads(controller_claim_to_bytes(claim, request))
        document["unexpected"] = True
        with self.assertRaisesRegex(WatchProtocolError, "fields"):
            controller_claim_from_bytes(_canonical(document), request)

    def test_watch_receipt_has_no_serialized_completion_boolean(self):
        expected_fields = (
            "request_id",
            "session_id",
            "run_id",
            "scenario",
            "worker_pid",
            "evidence_completion",
            "ready",
            "opened_root_kinds",
            "events",
            "event_bytes_sha256",
            "worker_exit_code",
            "watch_outcome_id",
            "request_bytes_sha256",
            "error",
        )
        self.assertEqual(expected_fields, tuple(item.name for item in fields(WatchReceipt)))
        receipt = WatchReceipt(
            request_id="watch-request:" + "1" * 64,
            session_id="watch-session:" + "2" * 64,
            run_id="containment-run:0123456789abcdef0123456789abcdef",
            scenario=ContainmentScenario.MERGE_EXISTING,
            worker_pid=42,
            evidence_completion=WatchEvidenceCompletion.COMPLETED,
            ready=True,
            opened_root_kinds=ROOT_KINDS,
            events=(WatcherEvent(1, "PlayProfile", "Modified", "modlist.txt"),),
            event_bytes_sha256="3" * 64,
            worker_exit_code=0,
            watch_outcome_id="watch-outcome-sha256:" + "4" * 64,
            request_bytes_sha256="5" * 64,
            error=None,
        )
        self.assertTrue(receipt.complete)
        self.assertFalse(
            replace(
                receipt,
                evidence_completion=WatchEvidenceCompletion.INCOMPLETE,
            ).complete
        )

    def test_publish_new_verified_uses_retained_publication_and_refuses_collision(self):
        path = self.evidence / CLAIM_NAME
        data = _canonical({"schemaVersion": 1})
        self.assertEqual(
            data,
            publish_new_verified(path, data, lambda value: json.loads(value)),
        )
        self.assertEqual(data, path.read_bytes())

        with self.assertRaises(FileExistsError):
            publish_new_verified(path, b"different", lambda value: value)
        self.assertEqual(data, path.read_bytes())

    def test_publication_failure_never_parses_or_leaves_unpublished_temporary(self):
        path = self.evidence / LAUNCH_NAME
        parsed = False

        def parse(_: bytes):
            nonlocal parsed
            parsed = True

        with mock.patch.object(
            windows_exact_fs,
            "write_pinned_file",
            side_effect=OSError("injected write failure"),
        ):
            with self.assertRaises(OSError):
                publish_new_verified(path, b"payload", parse)

        self.assertFalse(parsed)
        self.assertFalse(path.exists())
        self.assertEqual([], list(self.evidence.glob(".*.tmp")))

    def test_publication_write_rename_and_readback_fail_closed(self):
        cases = (
            ("write_pinned_file", "write"),
            ("rename_pinned_no_replace", "rename"),
        )
        for attribute, operation in cases:
            with self.subTest(operation=operation):
                path = self.evidence / f"{operation}.json"
                with mock.patch.object(
                    windows_exact_fs,
                    attribute,
                    side_effect=OSError(f"injected {operation}"),
                ):
                    with self.assertRaisesRegex(OSError, f"injected {operation}"):
                        publish_new_verified(path, b"payload", lambda value: value)
                self.assertFalse(path.exists())
                self.assertEqual([], list(self.evidence.glob(".*.tmp")))

        path = self.evidence / OUTCOME_NAME
        with mock.patch.object(
            windows_exact_fs,
            "read_pinned_file",
            side_effect=(b"payload", b"wrong"),
        ):
            with self.assertRaisesRegex(WatchProtocolError, "readback mismatch"):
                publish_new_verified(path, b"payload", lambda value: value)
        self.assertFalse(path.exists())

    def test_publication_close_failure_preserves_the_primary_error(self):
        path = self.evidence / "close.json"
        real_close = windows_exact_fs.PinnedObject.close

        def close_parent_then_fail(pinned):
            real_close(pinned)
            if pinned.path == self.evidence:
                raise OSError("injected close failure")

        with mock.patch.object(
            windows_exact_fs.PinnedObject,
            "close",
            new=close_parent_then_fail,
        ):
            with self.assertRaisesRegex(OSError, "injected close failure"):
                publish_new_verified(path, b"payload", lambda value: value)

        self.assertFalse(path.exists())

    def test_windows_watch_facade_reexports_protocol_values(self):
        from modlab.validation.windows_watch import (
            ROOT_KINDS as facade_root_kinds,
            WatchReceipt as FacadeReceipt,
            WatchRequest as FacadeRequest,
            WatchRoot as FacadeRoot,
        )

        self.assertIs(WatchRequest, FacadeRequest)
        self.assertIs(WatchRoot, FacadeRoot)
        self.assertIs(WatchReceipt, FacadeReceipt)
        self.assertEqual(ROOT_KINDS, facade_root_kinds)


if __name__ == "__main__":
    unittest.main()
