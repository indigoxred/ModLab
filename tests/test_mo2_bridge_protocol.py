"""Strict wire-format coverage for the passive MO2 Guard handshake."""

from __future__ import annotations

import hashlib
import json
import unittest
from dataclasses import replace

from modlab.resources.mo2_guard.protocol import (
    BridgeEvent,
    BridgeEventKind,
    BridgeProtocolError,
    BridgeRequest,
    BridgeRequestKind,
    SafeToClose,
    event_to_bytes,
    parse_bridge_document,
    request_from_bytes,
    request_sha256,
    request_to_bytes,
    safe_to_close_to_bytes,
    validate_handshake_evidence,
)


def valid_request() -> BridgeRequest:
    workspace = r"C:\ModLab\workspace"
    job = "bridge-job:" + "1" * 32
    request = "bridge-request:" + "2" * 32
    job_root = workspace + "\\runtime\\jobs\\mo2-bridge\\" + "1" * 32
    return BridgeRequest(
        schema_version=1,
        request_id=request,
        kind=BridgeRequestKind.HANDSHAKE,
        nonce="3" * 64,
        job_id=job,
        workspace_root=workspace,
        request_path=job_root + r"\request.json",
        claimed_path=job_root + r"\claimed.json",
        event_root=job_root + r"\events",
        safe_to_close_path=job_root + r"\safe-to-close.json",
        mo2_version="2.5.2.0",
        bootstrap_receipt_id="bootstrap-receipt-sha256:" + "4" * 64,
        bridge_receipt_id="bridge-receipt-sha256:" + "5" * 64,
        bridge_receipt_path=(
            workspace
            + "\\games\\skyrim-se-ae\\tool-installations\\mo2-guard\\"
            + "5" * 64
            + ".json"
        ),
        containment_run_id="containment-run:" + "6" * 32,
        containment_decision_id="containment-decision-sha256:" + "7" * 64,
        profile_name="ModLab - Lab",
        profile_path=r"C:\ModLab\workspace\tools\mo2\skyrim-se-ae\app\profiles\ModLab - Lab",
        base_path=r"C:\ModLab\workspace\tools\mo2\skyrim-se-ae\app",
        downloads_path=r"C:\ModLab\workspace\tools\mo2\skyrim-se-ae\downloads",
        mods_path=r"C:\ModLab\workspace\tools\mo2\skyrim-se-ae\mods",
        overwrite_path=r"C:\ModLab\workspace\tools\mo2\skyrim-se-ae\overwrite",
    )


def valid_event(request: BridgeRequest, sequence: int, kind: BridgeEventKind) -> BridgeEvent:
    return BridgeEvent(
        schema_version=1,
        job_id=request.job_id,
        request_id=request.request_id,
        request_sha256=request_sha256(request),
        sequence=sequence,
        kind=kind,
        detail_code="ok",
        observed_profile_name=request.profile_name,
        observed_profile_path=request.profile_path,
        observed_base_path=request.base_path,
        observed_downloads_path=request.downloads_path,
        observed_mods_path=request.mods_path,
        observed_overwrite_path=request.overwrite_path,
        observed_mo2_version=request.mo2_version,
        rejected_executable=None,
    )


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


class Mo2BridgeProtocolTests(unittest.TestCase):
    def test_handshake_request_round_trips_canonically(self) -> None:
        request = valid_request()
        encoded = request_to_bytes(request)
        self.assertEqual(request, request_from_bytes(encoded))
        self.assertEqual(encoded, request_to_bytes(request))
        self.assertEqual(hashlib.sha256(encoded).hexdigest(), request_sha256(request))

    def test_protocol_rejects_duplicate_extra_unsafe_and_impossible_values(self) -> None:
        encoded = request_to_bytes(valid_request())
        document = json.loads(encoded)
        cases = (
            (b'{"schemaVersion":1,"schemaVersion":1}', "duplicate JSON key"),
            (canonical({**document, "unexpected": True}), "fields differ"),
            (canonical({**document, "schemaVersion": True}), "schemaVersion"),
            (canonical({**document, "nonce": "A" * 64}), "lowercase SHA-256"),
            (canonical({**document, "kind": "Install"}), "Handshake"),
            (canonical({**document, "requestPath": r"C:\elsewhere\request.json"}), "requestPath"),
            (canonical({**document, "eventRoot": r"\\server\events"}), "eventRoot"),
        )
        for data, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(BridgeProtocolError, message):
                    parse_bridge_document(data)

    def test_evidence_requires_contiguous_final_safe_event_after_verified_identity(self) -> None:
        request = valid_request()
        events = (
            valid_event(request, 1, BridgeEventKind.REQUEST_CLAIMED),
            valid_event(request, 2, BridgeEventKind.VETO_REGISTERED),
            valid_event(request, 3, BridgeEventKind.UI_READY),
            valid_event(request, 4, BridgeEventKind.REFRESH_COMPLETED),
            valid_event(request, 5, BridgeEventKind.SAFE_TO_CLOSE),
        )
        safe = SafeToClose(
            schema_version=1,
            job_id=request.job_id,
            request_id=request.request_id,
            request_sha256=request_sha256(request),
            safe_event_sha256=hashlib.sha256(event_to_bytes(events[-1])).hexdigest(),
            safe_sequence=5,
            outcome="Passed",
        )
        validate_handshake_evidence(request, events, safe)
        with self.assertRaisesRegex(BridgeProtocolError, "contiguous"):
            validate_handshake_evidence(request, (events[0], replace(events[1], sequence=3)), safe)
        with self.assertRaisesRegex(BridgeProtocolError, "SafeToClose event"):
            validate_handshake_evidence(request, events[:-1], safe)
        failed = (*events[:-1], valid_event(request, 5, BridgeEventKind.FAILED), events[-1])
        with self.assertRaisesRegex(BridgeProtocolError, "failed identity"):
            validate_handshake_evidence(request, failed, safe)
        self.assertEqual(safe, parse_bridge_document(safe_to_close_to_bytes(safe)))

    def test_passing_evidence_requires_the_complete_exact_identity_lifecycle(self) -> None:
        request = valid_request()
        only_safe = valid_event(request, 1, BridgeEventKind.SAFE_TO_CLOSE)
        safe = SafeToClose(
            schema_version=1,
            job_id=request.job_id,
            request_id=request.request_id,
            request_sha256=request_sha256(request),
            safe_event_sha256=hashlib.sha256(event_to_bytes(only_safe)).hexdigest(),
            safe_sequence=1,
            outcome="Passed",
        )
        with self.assertRaisesRegex(BridgeProtocolError, "lifecycle"):
            validate_handshake_evidence(request, (only_safe,), safe)

        events = (
            valid_event(request, 1, BridgeEventKind.REQUEST_CLAIMED),
            valid_event(request, 2, BridgeEventKind.VETO_REGISTERED),
            replace(valid_event(request, 3, BridgeEventKind.UI_READY), observed_profile_name=None),
            valid_event(request, 4, BridgeEventKind.REFRESH_COMPLETED),
            valid_event(request, 5, BridgeEventKind.SAFE_TO_CLOSE),
        )
        safe = replace(
            safe,
            safe_event_sha256=hashlib.sha256(event_to_bytes(events[-1])).hexdigest(),
            safe_sequence=5,
        )
        with self.assertRaisesRegex(BridgeProtocolError, "observed identity"):
            validate_handshake_evidence(request, events, safe)
        changed = (*events[:2], replace(events[2], observed_profile_name="Other"), *events[3:])
        with self.assertRaisesRegex(BridgeProtocolError, "observed identity"):
            validate_handshake_evidence(request, changed, safe)

    def test_request_roots_must_be_exact_and_windows_safe(self) -> None:
        request = valid_request()
        with self.assertRaisesRegex(BridgeProtocolError, "basePath"):
            request_to_bytes(replace(request, base_path=r"C:\Elsewhere\app"))
        with self.assertRaisesRegex(BridgeProtocolError, "unsafe Windows"):
            request_to_bytes(replace(request, downloads_path=r"C:\NUL\downloads"))

    def test_evidence_rejects_mismatched_bindings_and_safe_hashes(self) -> None:
        request = valid_request()
        events = tuple(
            valid_event(request, index, kind)
            for index, kind in enumerate(
                (
                    BridgeEventKind.REQUEST_CLAIMED,
                    BridgeEventKind.VETO_REGISTERED,
                    BridgeEventKind.UI_READY,
                    BridgeEventKind.REFRESH_COMPLETED,
                    BridgeEventKind.SAFE_TO_CLOSE,
                ),
                start=1,
            )
        )
        safe = SafeToClose(
            schema_version=1,
            job_id=request.job_id,
            request_id=request.request_id,
            request_sha256=request_sha256(request),
            safe_event_sha256=hashlib.sha256(event_to_bytes(events[-1])).hexdigest(),
            safe_sequence=5,
            outcome="Passed",
        )
        for altered, message in (
            ((replace(events[0], job_id="bridge-job:" + "8" * 32), *events[1:]), "not bound"),
            ((events[0], replace(events[1], sequence=1), *events[2:]), "contiguous"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(BridgeProtocolError, message):
                validate_handshake_evidence(request, altered, safe)
        with self.assertRaisesRegex(BridgeProtocolError, "exact SafeToClose"):
            validate_handshake_evidence(request, events, replace(safe, safe_event_sha256="0" * 64))
