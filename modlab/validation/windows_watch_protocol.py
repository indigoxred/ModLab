"""Pure immutable protocol records for the Windows containment watcher."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import sys
from typing import Any

from modlab.platform.windows_exact_fs import (
    ExactObjectError,
    ExactObjectOwnershipError,
    publish_new_pinned,
)
from modlab.validation.mo2_containment_model import (
    ContainmentScenario,
    WatchEvidenceCompletion,
    WatcherEvent,
)


SCHEMA_VERSION = 1
REQUEST_NAME = "request.json"
CLAIM_NAME = "controller-claim.json"
LAUNCH_NAME = "worker-launch.json"
READY_NAME = "ready.json"
EVENTS_NAME = "events.ndjson"
TERMINAL_NAME = "terminal.json"
CONTROLLER_LOSS_NAME = "controller-loss.json"
OUTCOME_NAME = "outcome.json"
STOP_NAME = "stop.token"


ROOT_KINDS = (
    "SourceMods",
    "LabProfile",
    "PlayProfile",
    "Downloads",
    "Overwrite",
    "BoundedGame",
    "ExternalLocalLow",
    "ExternalTempLow",
)
_ROOT_KIND_SET = frozenset(ROOT_KINDS)

_REQUEST_ID = re.compile(r"^watch-request:[0-9a-f]{64}$")
_SESSION_ID = re.compile(r"^watch-session:[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^containment-run:[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_WATCH_OUTCOME_ID = re.compile(r"^watch-outcome-sha256:[0-9a-f]{64}$")

_REQUEST_FIELDS = {
    "schemaVersion",
    "requestId",
    "sessionId",
    "runId",
    "scenario",
    "evidenceRoot",
    "stopTokenPath",
    "roots",
}
_ROOT_FIELDS = {"rootKind", "path", "volumeSerial", "fileId"}
_CLAIM_FIELDS = {
    "schemaVersion",
    "requestSha256",
    "sessionId",
    "runId",
    "scenario",
    "requestPath",
    "workerCommand",
    "controllerPid",
    "controllerCreationTime",
}
_LAUNCH_FIELDS = {
    "schemaVersion",
    "requestSha256",
    "sessionId",
    "runId",
    "scenario",
    "workerPid",
    "workerCreationTime",
}
_LOSS_FIELDS = {
    "schemaVersion",
    "requestSha256",
    "sessionId",
    "runId",
    "scenario",
    "controllerPid",
    "controllerCreationTime",
    "workerPid",
    "workerCreationTime",
    "reasonCode",
}


class WatchProtocolError(RuntimeError):
    """A watcher record is unsafe, malformed, noncanonical, or unbound."""


class WatchProtocolOwnershipError(WatchProtocolError):
    """A protocol publication failed with explicit retained-handle ownership."""

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


@dataclass(frozen=True)
class WatchRoot:
    root_kind: str
    path: Path
    volume_serial: int
    file_id: int


@dataclass(frozen=True)
class WatchRequest:
    request_id: str
    session_id: str
    run_id: str
    scenario: ContainmentScenario
    evidence_root: Path
    stop_token_path: Path
    roots: tuple[WatchRoot, ...]


@dataclass(frozen=True)
class ControllerClaim:
    schema_version: int
    request_sha256: str
    session_id: str
    run_id: str
    scenario: ContainmentScenario
    request_path: Path
    worker_command: tuple[str, ...]
    controller_pid: int
    controller_creation_time: int


@dataclass(frozen=True)
class WorkerLaunch:
    schema_version: int
    request_sha256: str
    session_id: str
    run_id: str
    scenario: ContainmentScenario
    worker_pid: int
    worker_creation_time: int


@dataclass(frozen=True)
class ControllerLoss:
    schema_version: int
    request_sha256: str
    session_id: str
    run_id: str
    scenario: ContainmentScenario
    controller_pid: int
    controller_creation_time: int
    worker_pid: int
    worker_creation_time: int
    reason_code: str


@dataclass(frozen=True)
class WatchReceipt:
    request_id: str
    session_id: str
    run_id: str
    scenario: ContainmentScenario
    worker_pid: int
    evidence_completion: WatchEvidenceCompletion
    ready: bool
    opened_root_kinds: tuple[str, ...]
    events: tuple[WatcherEvent, ...]
    event_bytes_sha256: str
    worker_exit_code: int | None
    watch_outcome_id: str | None
    request_bytes_sha256: str
    error: str | None

    def __post_init__(self) -> None:
        if (
            self.evidence_completion is WatchEvidenceCompletion.COMPLETED
            and (
                type(self.watch_outcome_id) is not str
                or _WATCH_OUTCOME_ID.fullmatch(self.watch_outcome_id) is None
            )
        ):
            raise WatchProtocolError(
                "Completed receipt requires a valid watch outcome ID"
            )

    @property
    def complete(self) -> bool:
        return self.evidence_completion is WatchEvidenceCompletion.COMPLETED


def watch_request_to_bytes(value: WatchRequest) -> bytes:
    return _canonical_bytes(_request_dict(_validate_request(value)))


def watch_request_from_bytes(data: bytes) -> WatchRequest:
    value = _object_from_bytes(data, "request")
    _exact_fields(value, _REQUEST_FIELDS, "request")
    _schema(value["schemaVersion"], "request schemaVersion")
    roots_value = value["roots"]
    if type(roots_value) is not list:
        raise WatchProtocolError("request roots must be an array")
    roots = []
    for index, item in enumerate(roots_value):
        if type(item) is not dict:
            raise WatchProtocolError(f"request roots[{index}] must be an object")
        _exact_fields(item, _ROOT_FIELDS, f"request roots[{index}]")
        roots.append(
            WatchRoot(
                _text(item["rootKind"], f"request roots[{index}].rootKind"),
                _path(item["path"], f"request roots[{index}].path"),
                _integer(
                    item["volumeSerial"],
                    f"request roots[{index}].volumeSerial",
                    maximum=0xFFFFFFFF,
                ),
                _integer(
                    item["fileId"],
                    f"request roots[{index}].fileId",
                    maximum=0xFFFFFFFFFFFFFFFF,
                ),
            )
        )
    request = WatchRequest(
        request_id=_pattern(value["requestId"], _REQUEST_ID, "requestId"),
        session_id=_pattern(value["sessionId"], _SESSION_ID, "sessionId"),
        run_id=_pattern(value["runId"], _RUN_ID, "runId"),
        scenario=_scenario(value["scenario"]),
        evidence_root=_path(value["evidenceRoot"], "evidenceRoot"),
        stop_token_path=_path(value["stopTokenPath"], "stopTokenPath"),
        roots=tuple(roots),
    )
    return _validate_request(request)


def watch_request_sha256(value: WatchRequest) -> str:
    return hashlib.sha256(watch_request_to_bytes(value)).hexdigest()


def watch_worker_command(request_path: Path) -> tuple[str, ...]:
    canonical_request_path = _path_value(request_path, "worker request path")
    executable = _path_value(Path(sys.executable), "worker executable")
    return (
        str(executable),
        "-B",
        "-m",
        "modlab.validation.windows_watch",
        "--worker",
        str(canonical_request_path),
    )


def controller_claim_to_bytes(
    value: ControllerClaim,
    request: WatchRequest,
) -> bytes:
    return _canonical_bytes(
        _claim_dict(_validate_claim(value, _validate_request(request)))
    )


def controller_claim_from_bytes(
    data: bytes,
    request: WatchRequest,
) -> ControllerClaim:
    bound_request = _validate_request(request)
    value = _object_from_bytes(data, "controller claim")
    _exact_fields(value, _CLAIM_FIELDS, "controller claim")
    claim = ControllerClaim(
        schema_version=_schema(value["schemaVersion"], "claim schemaVersion"),
        request_sha256=_pattern(
            value["requestSha256"],
            _SHA256,
            "claim requestSha256",
        ),
        session_id=_pattern(value["sessionId"], _SESSION_ID, "claim sessionId"),
        run_id=_pattern(value["runId"], _RUN_ID, "claim runId"),
        scenario=_scenario(value["scenario"]),
        request_path=_path(value["requestPath"], "claim requestPath"),
        worker_command=_command_array(
            value["workerCommand"],
            "claim workerCommand",
        ),
        controller_pid=_positive(value["controllerPid"], "claim controllerPid"),
        controller_creation_time=_positive(
            value["controllerCreationTime"],
            "claim controllerCreationTime",
        ),
    )
    return _validate_claim(claim, bound_request)


def worker_launch_to_bytes(
    value: WorkerLaunch,
    request: WatchRequest,
) -> bytes:
    return _canonical_bytes(
        _launch_dict(_validate_launch(value, _validate_request(request)))
    )


def worker_launch_from_bytes(
    data: bytes,
    request: WatchRequest,
) -> WorkerLaunch:
    bound_request = _validate_request(request)
    value = _object_from_bytes(data, "worker launch")
    _exact_fields(value, _LAUNCH_FIELDS, "worker launch")
    launch = WorkerLaunch(
        schema_version=_schema(value["schemaVersion"], "launch schemaVersion"),
        request_sha256=_pattern(
            value["requestSha256"],
            _SHA256,
            "launch requestSha256",
        ),
        session_id=_pattern(value["sessionId"], _SESSION_ID, "launch sessionId"),
        run_id=_pattern(value["runId"], _RUN_ID, "launch runId"),
        scenario=_scenario(value["scenario"]),
        worker_pid=_positive(value["workerPid"], "launch workerPid"),
        worker_creation_time=_positive(
            value["workerCreationTime"],
            "launch workerCreationTime",
        ),
    )
    return _validate_launch(launch, bound_request)


def controller_loss_to_bytes(
    value: ControllerLoss,
    request: WatchRequest,
    claim: ControllerClaim,
    launch: WorkerLaunch,
) -> bytes:
    return _canonical_bytes(
        _loss_dict(_validate_loss(value, request, claim, launch))
    )


def controller_loss_from_bytes(
    data: bytes,
    request: WatchRequest,
    claim: ControllerClaim,
    launch: WorkerLaunch,
) -> ControllerLoss:
    value = _object_from_bytes(data, "controller loss")
    _exact_fields(value, _LOSS_FIELDS, "controller loss")
    loss = ControllerLoss(
        schema_version=_schema(value["schemaVersion"], "loss schemaVersion"),
        request_sha256=_pattern(
            value["requestSha256"],
            _SHA256,
            "loss requestSha256",
        ),
        session_id=_pattern(value["sessionId"], _SESSION_ID, "loss sessionId"),
        run_id=_pattern(value["runId"], _RUN_ID, "loss runId"),
        scenario=_scenario(value["scenario"]),
        controller_pid=_positive(value["controllerPid"], "loss controllerPid"),
        controller_creation_time=_positive(
            value["controllerCreationTime"],
            "loss controllerCreationTime",
        ),
        worker_pid=_positive(value["workerPid"], "loss workerPid"),
        worker_creation_time=_positive(
            value["workerCreationTime"],
            "loss workerCreationTime",
        ),
        reason_code=_text(value["reasonCode"], "loss reasonCode"),
    )
    return _validate_loss(loss, request, claim, launch)


def publish_new_verified(
    path: Path,
    data: bytes,
    parse: Callable[[bytes], object],
) -> bytes:
    """Durably publish one immutable record without replacing a destination."""
    if type(path) not in {Path, type(Path())} and not isinstance(path, Path):
        raise WatchProtocolError("publication path must be a Path")
    if type(data) is not bytes:
        raise WatchProtocolError("publication data must be bytes")
    if not callable(parse):
        raise WatchProtocolError("publication parser must be callable")

    def validate(candidate: bytes) -> bytes:
        parse(candidate)
        if candidate != data:
            raise WatchProtocolError(f"durable readback mismatch for {path.name}")
        return candidate

    try:
        published = publish_new_pinned(path, data, validate)
    except ExactObjectOwnershipError as error:
        raise WatchProtocolOwnershipError(
            f"exact immutable publication retained ownership for {path.name}: {error}",
            error,
        ) from error
    except ExactObjectError as error:
        raise WatchProtocolError(
            f"exact immutable publication failed for {path.name}: {error}"
        ) from error
    if type(published) is not bytes:
        raise WatchProtocolError("immutable publication validator returned invalid bytes")
    return published


def _validate_request(value: WatchRequest) -> WatchRequest:
    if not isinstance(value, WatchRequest):
        raise WatchProtocolError("request must be WatchRequest")
    request_id = _pattern(value.request_id, _REQUEST_ID, "requestId")
    session_id = _pattern(value.session_id, _SESSION_ID, "sessionId")
    run_id = _pattern(value.run_id, _RUN_ID, "runId")
    if not isinstance(value.scenario, ContainmentScenario):
        raise WatchProtocolError("scenario must be ContainmentScenario")
    evidence_root = _path_value(value.evidence_root, "evidenceRoot")
    stop_token = _path_value(value.stop_token_path, "stopTokenPath")
    if stop_token != evidence_root / STOP_NAME:
        raise WatchProtocolError(
            "stop token path must be the exact confined evidence stop.token"
        )
    if type(value.roots) is not tuple:
        raise WatchProtocolError("roots must be a tuple")
    roots = tuple(_validate_root(root, index) for index, root in enumerate(value.roots))
    if tuple(root.root_kind for root in roots) != ROOT_KINDS:
        raise WatchProtocolError(
            "request must contain all eight logical root kinds in canonical order"
        )
    return WatchRequest(
        request_id,
        session_id,
        run_id,
        value.scenario,
        evidence_root,
        stop_token,
        roots,
    )


def _validate_root(value: WatchRoot, index: int) -> WatchRoot:
    if not isinstance(value, WatchRoot):
        raise WatchProtocolError(f"roots[{index}] must be WatchRoot")
    if type(value.root_kind) is not str or value.root_kind not in _ROOT_KIND_SET:
        raise WatchProtocolError(f"roots[{index}] has invalid root kind")
    path = _path_value(value.path, f"roots[{index}].path")
    volume_serial = _integer(
        value.volume_serial,
        f"roots[{index}].volumeSerial",
        maximum=0xFFFFFFFF,
    )
    file_id = _integer(
        value.file_id,
        f"roots[{index}].fileId",
        maximum=0xFFFFFFFFFFFFFFFF,
    )
    return WatchRoot(value.root_kind, path, volume_serial, file_id)


def _validate_claim(
    value: ControllerClaim,
    request: WatchRequest,
) -> ControllerClaim:
    if not isinstance(value, ControllerClaim):
        raise WatchProtocolError("controller claim must be ControllerClaim")
    _schema(value.schema_version, "claim schemaVersion")
    _bind_request_record(
        value.request_sha256,
        value.session_id,
        value.run_id,
        value.scenario,
        request,
        "controller claim",
    )
    request_path = _path_value(value.request_path, "claim requestPath")
    expected_request_path = request.evidence_root / REQUEST_NAME
    if request_path != expected_request_path:
        raise WatchProtocolError("controller claim request path mismatch")
    if type(value.worker_command) is not tuple:
        raise WatchProtocolError("claim workerCommand must be a tuple")
    worker_command = tuple(
        _text(item, f"claim workerCommand[{index}]")
        for index, item in enumerate(value.worker_command)
    )
    if worker_command != watch_worker_command(request_path):
        raise WatchProtocolError("controller claim worker command mismatch")
    controller_pid = _positive(value.controller_pid, "claim controllerPid")
    controller_creation_time = _positive(
        value.controller_creation_time,
        "claim controllerCreationTime",
    )
    return ControllerClaim(
        value.schema_version,
        value.request_sha256,
        value.session_id,
        value.run_id,
        value.scenario,
        request_path,
        worker_command,
        controller_pid,
        controller_creation_time,
    )


def _validate_launch(value: WorkerLaunch, request: WatchRequest) -> WorkerLaunch:
    if not isinstance(value, WorkerLaunch):
        raise WatchProtocolError("worker launch must be WorkerLaunch")
    _schema(value.schema_version, "launch schemaVersion")
    _bind_request_record(
        value.request_sha256,
        value.session_id,
        value.run_id,
        value.scenario,
        request,
        "worker launch",
    )
    _positive(value.worker_pid, "launch workerPid")
    _positive(value.worker_creation_time, "launch workerCreationTime")
    return value


def _validate_loss(
    value: ControllerLoss,
    request: WatchRequest,
    claim: ControllerClaim,
    launch: WorkerLaunch,
) -> ControllerLoss:
    bound_request = _validate_request(request)
    bound_claim = _validate_claim(claim, bound_request)
    bound_launch = _validate_launch(launch, bound_request)
    if not isinstance(value, ControllerLoss):
        raise WatchProtocolError("controller loss must be ControllerLoss")
    _schema(value.schema_version, "loss schemaVersion")
    _bind_request_record(
        value.request_sha256,
        value.session_id,
        value.run_id,
        value.scenario,
        bound_request,
        "controller loss",
    )
    if (
        value.controller_pid,
        value.controller_creation_time,
    ) != (
        bound_claim.controller_pid,
        bound_claim.controller_creation_time,
    ):
        raise WatchProtocolError("controller loss claim identity mismatch")
    if (value.worker_pid, value.worker_creation_time) != (
        bound_launch.worker_pid,
        bound_launch.worker_creation_time,
    ):
        raise WatchProtocolError("controller loss launch identity mismatch")
    if value.reason_code != "controller-session-lost":
        raise WatchProtocolError(
            "controller loss reason must be controller-session-lost"
        )
    return value


def _bind_request_record(
    request_sha256: Any,
    session_id: Any,
    run_id: Any,
    scenario: Any,
    request: WatchRequest,
    label: str,
) -> None:
    if _pattern(request_sha256, _SHA256, f"{label} requestSha256") != watch_request_sha256(request):
        raise WatchProtocolError(f"{label} request SHA-256 mismatch")
    if _pattern(session_id, _SESSION_ID, f"{label} sessionId") != request.session_id:
        raise WatchProtocolError(f"{label} session mismatch")
    if _pattern(run_id, _RUN_ID, f"{label} runId") != request.run_id:
        raise WatchProtocolError(f"{label} run mismatch")
    if scenario is not request.scenario:
        raise WatchProtocolError(f"{label} scenario mismatch")


def _request_dict(value: WatchRequest) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "requestId": value.request_id,
        "sessionId": value.session_id,
        "runId": value.run_id,
        "scenario": value.scenario.value,
        "evidenceRoot": str(value.evidence_root),
        "stopTokenPath": str(value.stop_token_path),
        "roots": [
            {
                "rootKind": root.root_kind,
                "path": str(root.path),
                "volumeSerial": root.volume_serial,
                "fileId": root.file_id,
            }
            for root in value.roots
        ],
    }


def _claim_dict(value: ControllerClaim) -> dict[str, Any]:
    return {
        "schemaVersion": value.schema_version,
        "requestSha256": value.request_sha256,
        "sessionId": value.session_id,
        "runId": value.run_id,
        "scenario": value.scenario.value,
        "requestPath": str(value.request_path),
        "workerCommand": list(value.worker_command),
        "controllerPid": value.controller_pid,
        "controllerCreationTime": value.controller_creation_time,
    }


def _launch_dict(value: WorkerLaunch) -> dict[str, Any]:
    return {
        "schemaVersion": value.schema_version,
        "requestSha256": value.request_sha256,
        "sessionId": value.session_id,
        "runId": value.run_id,
        "scenario": value.scenario.value,
        "workerPid": value.worker_pid,
        "workerCreationTime": value.worker_creation_time,
    }


def _loss_dict(value: ControllerLoss) -> dict[str, Any]:
    return {
        "schemaVersion": value.schema_version,
        "requestSha256": value.request_sha256,
        "sessionId": value.session_id,
        "runId": value.run_id,
        "scenario": value.scenario.value,
        "controllerPid": value.controller_pid,
        "controllerCreationTime": value.controller_creation_time,
        "workerPid": value.worker_pid,
        "workerCreationTime": value.worker_creation_time,
        "reasonCode": value.reason_code,
    }


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _object_from_bytes(data: bytes, label: str) -> dict[str, object]:
    if type(data) is not bytes:
        raise WatchProtocolError(f"{label} must be bytes")
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                WatchProtocolError(f"invalid JSON constant: {constant}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, WatchProtocolError) as error:
        if isinstance(error, WatchProtocolError) and str(error).startswith("duplicate JSON key"):
            raise
        raise WatchProtocolError(f"malformed {label}: {error}") from error
    if type(value) is not dict:
        raise WatchProtocolError(f"{label} must be a JSON object")
    if _canonical_bytes(value) != data:
        raise WatchProtocolError(f"{label} must use canonical JSON")
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise WatchProtocolError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _exact_fields(value: Mapping[str, object], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise WatchProtocolError(f"{label} fields must be exact")


def _schema(value: Any, label: str) -> int:
    if type(value) is not int or value != SCHEMA_VERSION:
        raise WatchProtocolError(f"{label} must be integer {SCHEMA_VERSION}")
    return value


def _integer(
    value: Any,
    label: str,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    if type(value) is not int or value < minimum:
        raise WatchProtocolError(f"{label} must be an integer >= {minimum}")
    if maximum is not None and value > maximum:
        raise WatchProtocolError(f"{label} exceeds {maximum}")
    return value


def _positive(value: Any, label: str) -> int:
    return _integer(value, label, minimum=1)


def _text(value: Any, label: str) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or any(ord(character) < 32 for character in value)
    ):
        raise WatchProtocolError(f"{label} must be nonempty canonical text")
    return value


def _command_array(value: Any, label: str) -> tuple[str, ...]:
    if type(value) is not list:
        raise WatchProtocolError(f"{label} must be an array")
    return tuple(
        _text(item, f"{label}[{index}]")
        for index, item in enumerate(value)
    )


def _pattern(value: Any, pattern: re.Pattern[str], label: str) -> str:
    text = _text(value, label)
    if pattern.fullmatch(text) is None:
        raise WatchProtocolError(f"{label} is malformed")
    return text


def _scenario(value: Any) -> ContainmentScenario:
    if type(value) is not str:
        raise WatchProtocolError("scenario must be text")
    try:
        return ContainmentScenario(value)
    except ValueError as error:
        raise WatchProtocolError("scenario is invalid") from error


def _path(value: Any, label: str) -> Path:
    text = _text(value, label)
    pure = PureWindowsPath(text)
    if (
        "/" in text
        or not pure.is_absolute()
        or any(part in {".", ".."} for part in pure.parts)
        or str(pure) != text
    ):
        raise WatchProtocolError(
            f"{label} must use one canonical absolute Windows path spelling"
        )
    return Path(text)


def _path_value(value: Any, label: str) -> Path:
    if not isinstance(value, Path):
        raise WatchProtocolError(f"{label} must use Path values")
    text = str(value)
    pure = PureWindowsPath(text)
    if (
        "/" in text
        or not pure.is_absolute()
        or any(part in {".", ".."} for part in pure.parts)
        or str(pure) != text
    ):
        raise WatchProtocolError(
            f"{label} must use one canonical absolute Windows path spelling"
        )
    return Path(text)
