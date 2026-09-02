"""Strict, dependency-free wire protocol shared by the host and MO2 Guard."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from pathlib import PureWindowsPath
from typing import Any


class BridgeProtocolError(ValueError):
    """Raised when bridge evidence is malformed, ambiguous, or unsafe."""


class BridgeRequestKind(StrEnum):
    HANDSHAKE = "Handshake"


class BridgeEventKind(StrEnum):
    REQUEST_CLAIMED = "RequestClaimed"
    VETO_REGISTERED = "VetoRegistered"
    UI_READY = "UiReady"
    REFRESH_COMPLETED = "RefreshCompleted"
    LAUNCH_REJECTED = "LaunchRejected"
    FAILED = "Failed"
    SAFE_TO_CLOSE = "SafeToClose"


@dataclass(frozen=True)
class BridgeBundleFile:
    relative_path: str
    sha256: str
    size: int


@dataclass(frozen=True)
class BridgeReceipt:
    schema_version: int
    receipt_id: str
    qualification: str
    job_id: str
    bootstrap_receipt_id: str
    target_root: str
    bundle_version: str
    files: tuple[BridgeBundleFile, ...]
    verified_at: str


@dataclass(frozen=True)
class BridgeRequest:
    schema_version: int
    request_id: str
    kind: BridgeRequestKind
    nonce: str
    job_id: str
    workspace_root: str
    request_path: str
    claimed_path: str
    event_root: str
    safe_to_close_path: str
    mo2_version: str
    bootstrap_receipt_id: str
    bridge_receipt_id: str
    bridge_receipt_path: str
    containment_run_id: str
    containment_decision_id: str
    profile_name: str
    profile_path: str
    base_path: str
    downloads_path: str
    mods_path: str
    overwrite_path: str


@dataclass(frozen=True)
class BridgeEvent:
    schema_version: int
    job_id: str
    request_id: str
    request_sha256: str
    sequence: int
    kind: BridgeEventKind
    detail_code: str
    observed_profile_name: str | None
    observed_profile_path: str | None
    observed_base_path: str | None
    observed_downloads_path: str | None
    observed_mods_path: str | None
    observed_overwrite_path: str | None
    observed_mo2_version: str | None
    rejected_executable: str | None


@dataclass(frozen=True)
class SafeToClose:
    schema_version: int
    job_id: str
    request_id: str
    request_sha256: str
    safe_event_sha256: str
    safe_sequence: int
    outcome: str


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_JOB_ID = re.compile(r"^bridge-job:[0-9a-f]{32}$")
_REQUEST_ID = re.compile(r"^bridge-request:[0-9a-f]{32}$")
_BOOTSTRAP_RECEIPT_ID = re.compile(r"^bootstrap-receipt-sha256:[0-9a-f]{64}$")
_BRIDGE_RECEIPT_ID = re.compile(r"^bridge-receipt-sha256:[0-9a-f]{64}$")
_CONTAINMENT_RUN_ID = re.compile(r"^containment-run:[0-9a-f]{32}$")
_CONTAINMENT_DECISION_ID = re.compile(
    r"^containment-decision-sha256:[0-9a-f]{64}$"
)
_DETAIL_CODE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_BUNDLE_FILES = ("__init__.py", "plugin.py", "protocol.py")

_REQUEST_FIELDS = {
    "schemaVersion", "requestId", "kind", "nonce", "jobId", "workspaceRoot",
    "requestPath", "claimedPath", "eventRoot", "safeToClosePath", "mo2Version",
    "bootstrapReceiptId", "bridgeReceiptId", "bridgeReceiptPath", "containmentRunId",
    "containmentDecisionId", "profileName", "profilePath", "basePath", "downloadsPath",
    "modsPath", "overwritePath",
}
_EVENT_FIELDS = {
    "schemaVersion", "jobId", "requestId", "requestSha256", "sequence", "kind",
    "detailCode", "observedProfileName", "observedProfilePath", "observedBasePath",
    "observedDownloadsPath", "observedModsPath", "observedOverwritePath",
    "observedMo2Version", "rejectedExecutable",
}
_SAFE_FIELDS = {
    "schemaVersion", "jobId", "requestId", "requestSha256", "safeEventSha256",
    "safeSequence", "outcome",
}
_RECEIPT_BODY_FIELDS = {
    "schemaVersion", "qualification", "jobId", "bootstrapReceiptId", "targetRoot",
    "bundleVersion", "files", "verifiedAt",
}
_RECEIPT_FIELDS = _RECEIPT_BODY_FIELDS | {"receiptId"}


def request_to_dict(value: BridgeRequest) -> dict[str, object]:
    return _request_dict(_validate_request(value))


def request_to_bytes(value: BridgeRequest) -> bytes:
    return _canonical_bytes(request_to_dict(value))


def request_from_dict(value: object) -> BridgeRequest:
    data = _exact_mapping(value, _REQUEST_FIELDS, "bridge request")
    result = BridgeRequest(
        schema_version=_schema(data["schemaVersion"]),
        request_id=_typed_id(data["requestId"], _REQUEST_ID, "requestId"),
        kind=_enum(BridgeRequestKind, data["kind"], "kind"),
        nonce=_sha256(data["nonce"], "nonce"),
        job_id=_typed_id(data["jobId"], _JOB_ID, "jobId"),
        workspace_root=_absolute_path(data["workspaceRoot"], "workspaceRoot"),
        request_path=_absolute_path(data["requestPath"], "requestPath"),
        claimed_path=_absolute_path(data["claimedPath"], "claimedPath"),
        event_root=_absolute_path(data["eventRoot"], "eventRoot"),
        safe_to_close_path=_absolute_path(data["safeToClosePath"], "safeToClosePath"),
        mo2_version=_fixed_text(data["mo2Version"], "2.5.2.0", "mo2Version"),
        bootstrap_receipt_id=_typed_id(data["bootstrapReceiptId"], _BOOTSTRAP_RECEIPT_ID, "bootstrapReceiptId"),
        bridge_receipt_id=_typed_id(data["bridgeReceiptId"], _BRIDGE_RECEIPT_ID, "bridgeReceiptId"),
        bridge_receipt_path=_absolute_path(data["bridgeReceiptPath"], "bridgeReceiptPath"),
        containment_run_id=_typed_id(data["containmentRunId"], _CONTAINMENT_RUN_ID, "containmentRunId"),
        containment_decision_id=_typed_id(data["containmentDecisionId"], _CONTAINMENT_DECISION_ID, "containmentDecisionId"),
        profile_name=_fixed_text(data["profileName"], "ModLab - Lab", "profileName"),
        profile_path=_absolute_path(data["profilePath"], "profilePath"),
        base_path=_absolute_path(data["basePath"], "basePath"),
        downloads_path=_absolute_path(data["downloadsPath"], "downloadsPath"),
        mods_path=_absolute_path(data["modsPath"], "modsPath"),
        overwrite_path=_absolute_path(data["overwritePath"], "overwritePath"),
    )
    return _validate_request(result)


def request_from_bytes(data: bytes) -> BridgeRequest:
    return request_from_dict(_decode_document(data, "bridge request"))


def request_sha256(value: BridgeRequest) -> str:
    return hashlib.sha256(request_to_bytes(value)).hexdigest()


def event_to_dict(value: BridgeEvent) -> dict[str, object]:
    return _event_dict(_validate_event(value))


def event_to_bytes(value: BridgeEvent) -> bytes:
    return _canonical_bytes(event_to_dict(value))


def event_from_dict(value: object) -> BridgeEvent:
    data = _exact_mapping(value, _EVENT_FIELDS, "bridge event")
    result = BridgeEvent(
        schema_version=_schema(data["schemaVersion"]),
        job_id=_typed_id(data["jobId"], _JOB_ID, "jobId"),
        request_id=_typed_id(data["requestId"], _REQUEST_ID, "requestId"),
        request_sha256=_sha256(data["requestSha256"], "requestSha256"),
        sequence=_positive_int(data["sequence"], "sequence"),
        kind=_enum(BridgeEventKind, data["kind"], "kind"),
        detail_code=_detail_code(data["detailCode"]),
        observed_profile_name=_optional_text(data["observedProfileName"], "observedProfileName"),
        observed_profile_path=_optional_path(data["observedProfilePath"], "observedProfilePath"),
        observed_base_path=_optional_path(data["observedBasePath"], "observedBasePath"),
        observed_downloads_path=_optional_path(data["observedDownloadsPath"], "observedDownloadsPath"),
        observed_mods_path=_optional_path(data["observedModsPath"], "observedModsPath"),
        observed_overwrite_path=_optional_path(data["observedOverwritePath"], "observedOverwritePath"),
        observed_mo2_version=_optional_text(data["observedMo2Version"], "observedMo2Version"),
        rejected_executable=_optional_path(data["rejectedExecutable"], "rejectedExecutable"),
    )
    return _validate_event(result)


def event_from_bytes(data: bytes) -> BridgeEvent:
    return event_from_dict(_decode_document(data, "bridge event"))


def safe_to_close_to_dict(value: SafeToClose) -> dict[str, object]:
    return _safe_dict(_validate_safe(value))


def safe_to_close_to_bytes(value: SafeToClose) -> bytes:
    return _canonical_bytes(safe_to_close_to_dict(value))


def safe_to_close_from_dict(value: object) -> SafeToClose:
    data = _exact_mapping(value, _SAFE_FIELDS, "safe-to-close")
    return _validate_safe(SafeToClose(
        schema_version=_schema(data["schemaVersion"]),
        job_id=_typed_id(data["jobId"], _JOB_ID, "jobId"),
        request_id=_typed_id(data["requestId"], _REQUEST_ID, "requestId"),
        request_sha256=_sha256(data["requestSha256"], "requestSha256"),
        safe_event_sha256=_sha256(data["safeEventSha256"], "safeEventSha256"),
        safe_sequence=_positive_int(data["safeSequence"], "safeSequence"),
        outcome=_fixed_text(data["outcome"], "Passed", "outcome"),
    ))


def safe_to_close_from_bytes(data: bytes) -> SafeToClose:
    return safe_to_close_from_dict(_decode_document(data, "safe-to-close"))


def bridge_receipt_id_for(value: BridgeReceipt) -> str:
    normalized = _validate_receipt_body(value)
    return "bridge-receipt-sha256:" + hashlib.sha256(
        _canonical_bytes(_receipt_body_dict(normalized))
    ).hexdigest()


def bridge_receipt_to_dict(value: BridgeReceipt) -> dict[str, object]:
    normalized = _validate_receipt_body(value)
    receipt_id = _typed_id(value.receipt_id, _BRIDGE_RECEIPT_ID, "receiptId")
    expected = bridge_receipt_id_for(normalized)
    if receipt_id != expected:
        raise BridgeProtocolError("receiptId does not match canonical receipt body")
    result = _receipt_body_dict(normalized)
    result["receiptId"] = receipt_id
    return result


def bridge_receipt_to_bytes(value: BridgeReceipt) -> bytes:
    return _canonical_bytes(bridge_receipt_to_dict(value))


def bridge_receipt_from_dict(value: object) -> BridgeReceipt:
    data = _exact_mapping(value, _RECEIPT_FIELDS, "bridge receipt")
    result = _receipt_from_body({key: data[key] for key in _RECEIPT_BODY_FIELDS})
    receipt_id = _typed_id(data["receiptId"], _BRIDGE_RECEIPT_ID, "receiptId")
    expected = bridge_receipt_id_for(result)
    if receipt_id != expected:
        raise BridgeProtocolError("receiptId does not match canonical receipt body")
    return replace(result, receipt_id=receipt_id)


def bridge_receipt_from_bytes(data: bytes) -> BridgeReceipt:
    return bridge_receipt_from_dict(_decode_document(data, "bridge receipt"))


def parse_bridge_document(data: bytes) -> BridgeRequest | BridgeEvent | SafeToClose | BridgeReceipt:
    document = _decode_document(data, "bridge document")
    if not isinstance(document, Mapping):
        raise BridgeProtocolError("bridge document must be an object")
    fields = set(document)
    if fields == _REQUEST_FIELDS:
        return request_from_dict(document)
    if fields == _EVENT_FIELDS:
        return event_from_dict(document)
    if fields == _SAFE_FIELDS:
        return safe_to_close_from_dict(document)
    if fields == _RECEIPT_FIELDS:
        return bridge_receipt_from_dict(document)
    raise BridgeProtocolError("bridge document fields differ from every supported schema")


def validate_handshake_evidence(
    request: BridgeRequest, events: Sequence[BridgeEvent], safe: SafeToClose
) -> None:
    request = _validate_request(request)
    if not events:
        raise BridgeProtocolError("handshake evidence must contain events")
    expected_sha = request_sha256(request)
    expected_sequence = 1
    for event in events:
        event = _validate_event(event)
        if event.sequence != expected_sequence:
            raise BridgeProtocolError("event sequences must be contiguous from one")
        expected_sequence += 1
        if (event.job_id, event.request_id, event.request_sha256) != (
            request.job_id, request.request_id, expected_sha,
        ):
            raise BridgeProtocolError("event is not bound to the request identity")
        if event.kind is BridgeEventKind.FAILED:
            raise BridgeProtocolError("SafeToClose cannot follow a failed identity check")
    safe = _validate_safe(safe)
    if (safe.job_id, safe.request_id, safe.request_sha256) != (
        request.job_id, request.request_id, expected_sha,
    ):
        raise BridgeProtocolError("SafeToClose is not bound to the request identity")
    final = events[-1]
    if final.kind is not BridgeEventKind.SAFE_TO_CLOSE:
        raise BridgeProtocolError("SafeToClose must bind the final SafeToClose event")
    if safe.safe_sequence != final.sequence or safe.safe_event_sha256 != hashlib.sha256(event_to_bytes(final)).hexdigest():
        raise BridgeProtocolError("SafeToClose is not bound to the final event")


def _request_dict(value: BridgeRequest) -> dict[str, object]:
    return {
        "schemaVersion": value.schema_version, "requestId": value.request_id,
        "kind": value.kind.value, "nonce": value.nonce, "jobId": value.job_id,
        "workspaceRoot": value.workspace_root, "requestPath": value.request_path,
        "claimedPath": value.claimed_path, "eventRoot": value.event_root,
        "safeToClosePath": value.safe_to_close_path, "mo2Version": value.mo2_version,
        "bootstrapReceiptId": value.bootstrap_receipt_id, "bridgeReceiptId": value.bridge_receipt_id,
        "bridgeReceiptPath": value.bridge_receipt_path, "containmentRunId": value.containment_run_id,
        "containmentDecisionId": value.containment_decision_id, "profileName": value.profile_name,
        "profilePath": value.profile_path, "basePath": value.base_path,
        "downloadsPath": value.downloads_path, "modsPath": value.mods_path,
        "overwritePath": value.overwrite_path,
    }


def _event_dict(value: BridgeEvent) -> dict[str, object]:
    return {
        "schemaVersion": value.schema_version, "jobId": value.job_id,
        "requestId": value.request_id, "requestSha256": value.request_sha256,
        "sequence": value.sequence, "kind": value.kind.value, "detailCode": value.detail_code,
        "observedProfileName": value.observed_profile_name,
        "observedProfilePath": value.observed_profile_path,
        "observedBasePath": value.observed_base_path,
        "observedDownloadsPath": value.observed_downloads_path,
        "observedModsPath": value.observed_mods_path,
        "observedOverwritePath": value.observed_overwrite_path,
        "observedMo2Version": value.observed_mo2_version,
        "rejectedExecutable": value.rejected_executable,
    }


def _safe_dict(value: SafeToClose) -> dict[str, object]:
    return {
        "schemaVersion": value.schema_version, "jobId": value.job_id,
        "requestId": value.request_id, "requestSha256": value.request_sha256,
        "safeEventSha256": value.safe_event_sha256, "safeSequence": value.safe_sequence,
        "outcome": value.outcome,
    }


def _receipt_body_dict(value: BridgeReceipt) -> dict[str, object]:
    return {
        "schemaVersion": value.schema_version, "qualification": value.qualification,
        "jobId": value.job_id, "bootstrapReceiptId": value.bootstrap_receipt_id,
        "targetRoot": value.target_root, "bundleVersion": value.bundle_version,
        "files": [_bundle_file_dict(item) for item in value.files], "verifiedAt": value.verified_at,
    }


def _receipt_from_body(value: object) -> BridgeReceipt:
    data = _exact_mapping(value, _RECEIPT_BODY_FIELDS, "bridge receipt body")
    return _receipt_from_values(data)


def _receipt_from_values(data: Mapping[str, object]) -> BridgeReceipt:
    return BridgeReceipt(
        schema_version=_schema(data["schemaVersion"]), receipt_id="",
        qualification=_fixed_text(data["qualification"], "Verified", "qualification"),
        job_id=_typed_id(data["jobId"], _JOB_ID, "jobId"),
        bootstrap_receipt_id=_typed_id(data["bootstrapReceiptId"], _BOOTSTRAP_RECEIPT_ID, "bootstrapReceiptId"),
        target_root=_absolute_path(data["targetRoot"], "targetRoot"),
        bundle_version=_fixed_text(data["bundleVersion"], "mo2-guard-v1", "bundleVersion"),
        files=_bundle_files(data["files"]), verified_at=_utc_timestamp(data["verifiedAt"], "verifiedAt"),
    )


def _validate_request(value: object) -> BridgeRequest:
    if not isinstance(value, BridgeRequest):
        raise BridgeProtocolError("bridge request has the wrong type")
    return request_from_dict(_request_dict(value)) if type(value.schema_version) is not int else _validate_request_values(value)


def _validate_request_values(value: BridgeRequest) -> BridgeRequest:
    # Reparse through the exact schema so constructors are as strict as bytes.
    return _request_from_values(_request_dict(value))


def _request_from_values(data: dict[str, object]) -> BridgeRequest:
    # The direct path avoids recursive request_from_dict -> _validate_request calls.
    result = BridgeRequest(
        schema_version=_schema(data["schemaVersion"]), request_id=_typed_id(data["requestId"], _REQUEST_ID, "requestId"),
        kind=_enum(BridgeRequestKind, data["kind"], "kind"), nonce=_sha256(data["nonce"], "nonce"),
        job_id=_typed_id(data["jobId"], _JOB_ID, "jobId"), workspace_root=_absolute_path(data["workspaceRoot"], "workspaceRoot"),
        request_path=_absolute_path(data["requestPath"], "requestPath"), claimed_path=_absolute_path(data["claimedPath"], "claimedPath"),
        event_root=_absolute_path(data["eventRoot"], "eventRoot"), safe_to_close_path=_absolute_path(data["safeToClosePath"], "safeToClosePath"),
        mo2_version=_fixed_text(data["mo2Version"], "2.5.2.0", "mo2Version"), bootstrap_receipt_id=_typed_id(data["bootstrapReceiptId"], _BOOTSTRAP_RECEIPT_ID, "bootstrapReceiptId"),
        bridge_receipt_id=_typed_id(data["bridgeReceiptId"], _BRIDGE_RECEIPT_ID, "bridgeReceiptId"), bridge_receipt_path=_absolute_path(data["bridgeReceiptPath"], "bridgeReceiptPath"),
        containment_run_id=_typed_id(data["containmentRunId"], _CONTAINMENT_RUN_ID, "containmentRunId"), containment_decision_id=_typed_id(data["containmentDecisionId"], _CONTAINMENT_DECISION_ID, "containmentDecisionId"),
        profile_name=_fixed_text(data["profileName"], "ModLab - Lab", "profileName"), profile_path=_absolute_path(data["profilePath"], "profilePath"),
        base_path=_absolute_path(data["basePath"], "basePath"), downloads_path=_absolute_path(data["downloadsPath"], "downloadsPath"),
        mods_path=_absolute_path(data["modsPath"], "modsPath"), overwrite_path=_absolute_path(data["overwritePath"], "overwritePath"),
    )
    _require_request_relationships(result)
    return result


def _validate_event(value: object) -> BridgeEvent:
    if not isinstance(value, BridgeEvent):
        raise BridgeProtocolError("bridge event has the wrong type")
    return _event_from_values(_event_dict(value))


def _event_from_values(data: dict[str, object]) -> BridgeEvent:
    result = BridgeEvent(
        schema_version=_schema(data["schemaVersion"]), job_id=_typed_id(data["jobId"], _JOB_ID, "jobId"), request_id=_typed_id(data["requestId"], _REQUEST_ID, "requestId"),
        request_sha256=_sha256(data["requestSha256"], "requestSha256"), sequence=_positive_int(data["sequence"], "sequence"),
        kind=_enum(BridgeEventKind, data["kind"], "kind"), detail_code=_detail_code(data["detailCode"]),
        observed_profile_name=_optional_text(data["observedProfileName"], "observedProfileName"), observed_profile_path=_optional_path(data["observedProfilePath"], "observedProfilePath"),
        observed_base_path=_optional_path(data["observedBasePath"], "observedBasePath"), observed_downloads_path=_optional_path(data["observedDownloadsPath"], "observedDownloadsPath"),
        observed_mods_path=_optional_path(data["observedModsPath"], "observedModsPath"), observed_overwrite_path=_optional_path(data["observedOverwritePath"], "observedOverwritePath"),
        observed_mo2_version=_optional_text(data["observedMo2Version"], "observedMo2Version"), rejected_executable=_optional_path(data["rejectedExecutable"], "rejectedExecutable"),
    )
    if result.kind is BridgeEventKind.LAUNCH_REJECTED and result.rejected_executable is None:
        raise BridgeProtocolError("LaunchRejected event requires rejectedExecutable")
    if result.kind is not BridgeEventKind.LAUNCH_REJECTED and result.rejected_executable is not None:
        raise BridgeProtocolError("only LaunchRejected may include rejectedExecutable")
    return result


def _validate_safe(value: object) -> SafeToClose:
    if not isinstance(value, SafeToClose):
        raise BridgeProtocolError("safe-to-close has the wrong type")
    return SafeToClose(
        schema_version=_schema(value.schema_version), job_id=_typed_id(value.job_id, _JOB_ID, "jobId"),
        request_id=_typed_id(value.request_id, _REQUEST_ID, "requestId"), request_sha256=_sha256(value.request_sha256, "requestSha256"),
        safe_event_sha256=_sha256(value.safe_event_sha256, "safeEventSha256"), safe_sequence=_positive_int(value.safe_sequence, "safeSequence"),
        outcome=_fixed_text(value.outcome, "Passed", "outcome"),
    )


def _validate_receipt_body(value: object) -> BridgeReceipt:
    if not isinstance(value, BridgeReceipt):
        raise BridgeProtocolError("bridge receipt has the wrong type")
    return _receipt_from_values(_receipt_body_dict(value))


def _require_request_relationships(value: BridgeRequest) -> None:
    workspace = PureWindowsPath(value.workspace_root)
    job_root = workspace / "runtime" / "jobs" / "mo2-bridge" / value.job_id.removeprefix("bridge-job:")
    exact = {
        "requestPath": (value.request_path, job_root / "request.json"),
        "claimedPath": (value.claimed_path, job_root / "claimed.json"),
        "eventRoot": (value.event_root, job_root / "events"),
        "safeToClosePath": (value.safe_to_close_path, job_root / "safe-to-close.json"),
        "bridgeReceiptPath": (
            value.bridge_receipt_path,
            workspace / "games" / "skyrim-se-ae" / "tool-installations" / "mo2-guard"
            / (value.bridge_receipt_id.removeprefix("bridge-receipt-sha256:") + ".json"),
        ),
    }
    for label, (actual, expected) in exact.items():
        if actual.casefold() != str(expected).casefold():
            raise BridgeProtocolError(f"{label} is outside its exact job containment path")


def _bundle_files(value: object) -> tuple[BridgeBundleFile, ...]:
    if not isinstance(value, list):
        raise BridgeProtocolError("files must be an array")
    files: list[BridgeBundleFile] = []
    for index, item in enumerate(value):
        data = _exact_mapping(item, {"relativePath", "sha256", "size"}, f"files[{index}]")
        files.append(BridgeBundleFile(
            relative_path=_fixed_text(data["relativePath"], _BUNDLE_FILES[index] if index < len(_BUNDLE_FILES) else "", f"files[{index}].relativePath"),
            sha256=_sha256(data["sha256"], f"files[{index}].sha256"), size=_nonnegative_int(data["size"], f"files[{index}].size"),
        ))
    result = tuple(files)
    if tuple(item.relative_path for item in result) != _BUNDLE_FILES:
        raise BridgeProtocolError("files must contain the exact ordered three-file bundle")
    return result


def _bundle_file_dict(value: BridgeBundleFile) -> dict[str, object]:
    if not isinstance(value, BridgeBundleFile):
        raise BridgeProtocolError("bundle file has the wrong type")
    return {"relativePath": value.relative_path, "sha256": value.sha256, "size": value.size}


def _decode_document(data: bytes, label: str) -> object:
    if type(data) is not bytes:
        raise BridgeProtocolError(f"{label} data must be bytes")
    if data.startswith(b"\xef\xbb\xbf"):
        raise BridgeProtocolError(f"{label} must be UTF-8 without BOM")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BridgeProtocolError(f"{label} is not valid UTF-8") from error
    try:
        document = json.loads(text, object_pairs_hook=_without_duplicates, parse_constant=_reject_nonfinite)
    except json.JSONDecodeError as error:
        raise BridgeProtocolError(f"{label} is not valid JSON: line {error.lineno}, column {error.colno}") from error
    if _canonical_bytes(document) != data:
        raise BridgeProtocolError(f"{label} must use canonical JSON bytes")
    return document


def _without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise BridgeProtocolError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> object:
    raise BridgeProtocolError(f"non-finite JSON value is not allowed: {value}")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _exact_mapping(value: object, expected: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BridgeProtocolError(f"{label} must be an object")
    actual = set(value)
    if actual != expected:
        raise BridgeProtocolError(f"{label} fields differ: missing={sorted(expected - actual)}, extra={sorted(actual - expected)}")
    return value


def _schema(value: object) -> int:
    if type(value) is not int or value != 1:
        raise BridgeProtocolError("schemaVersion must be integer 1")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or any(ord(char) < 32 for char in value):
        raise BridgeProtocolError(f"{label} must be non-blank unpadded text")
    return value


def _optional_text(value: object, label: str) -> str | None:
    return None if value is None else _text(value, label)


def _fixed_text(value: object, expected: str, label: str) -> str:
    text = _text(value, label)
    if text != expected:
        raise BridgeProtocolError(f"{label} must equal {expected}")
    return text


def _sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise BridgeProtocolError(f"{label} must be a lowercase SHA-256")
    return value


def _typed_id(value: object, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise BridgeProtocolError(f"{label} has an invalid typed identity")
    return value


def _enum(enum_type: type[StrEnum], value: object, label: str) -> StrEnum:
    if not isinstance(value, str):
        raise BridgeProtocolError(f"{label} must be text")
    try:
        return enum_type(value)
    except ValueError as error:
        raise BridgeProtocolError(f"{label} must be Handshake or a supported event kind") from error


def _positive_int(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise BridgeProtocolError(f"{label} must be a positive integer")
    return value


def _nonnegative_int(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise BridgeProtocolError(f"{label} must be a non-negative integer")
    return value


def _detail_code(value: object) -> str:
    text = _text(value, "detailCode")
    if _DETAIL_CODE.fullmatch(text) is None:
        raise BridgeProtocolError("detailCode must be lowercase kebab-case")
    return text


def _utc_timestamp(value: object, label: str) -> str:
    text = _text(value, label)
    try:
        datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise BridgeProtocolError(f"{label} must use YYYY-MM-DDTHH:MM:SSZ") from error
    return text


def _absolute_path(value: object, label: str) -> str:
    text = _text(value, label)
    if "/" in text or text.startswith("\\\\"):
        raise BridgeProtocolError(f"{label} must be a direct absolute Windows path")
    path = PureWindowsPath(text)
    if not path.is_absolute() or not path.drive or str(path) != text or any(part in {".", ".."} for part in path.parts):
        raise BridgeProtocolError(f"{label} must be a direct absolute Windows path")
    for part in path.parts[1:]:
        if part.endswith((" ", ".")) or any(char in '<>:"|?*' or ord(char) < 32 for char in part):
            raise BridgeProtocolError(f"{label} contains an unsafe Windows path component")
    return text


def _optional_path(value: object, label: str) -> str | None:
    return None if value is None else _absolute_path(value, label)


__all__ = [
    "BridgeBundleFile", "BridgeEvent", "BridgeEventKind", "BridgeProtocolError",
    "BridgeReceipt", "BridgeRequest", "BridgeRequestKind", "SafeToClose",
    "bridge_receipt_from_bytes", "bridge_receipt_id_for", "bridge_receipt_to_bytes",
    "event_from_bytes", "event_to_bytes", "parse_bridge_document", "request_from_bytes",
    "request_sha256", "request_to_bytes", "safe_to_close_from_bytes",
    "safe_to_close_to_bytes", "validate_handshake_evidence",
]
