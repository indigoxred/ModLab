"""Disposable Task 7B full-stack gate operator support.

This module deliberately separates preparation and pure evidence validation from
live operation.  Importing it has no filesystem or process effects.  The first
review gate covers this file and its focused offline tests; no live archive,
watcher, MO2 process, capability selection, or bridge authority is created by
the offline test entry point.
"""

from __future__ import annotations

import base64
import argparse
import binascii
import ctypes
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import struct
import sys
from types import SimpleNamespace
from typing import Any, Callable, Mapping
from ctypes import wintypes
import zlib

_SCRIPT_REPO_ROOT = Path(__file__).absolute().parents[1]
if str(_SCRIPT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_REPO_ROOT))

from modlab.adapters.mo2.archive import (
    ArchiveListing,
    observe_bsdtar,
    preflight_archive,
)
from modlab.adapters.mo2.bootstrap_model import (
    BootstrapDisposition,
    BootstrapFailureResult,
    BootstrapJobState,
    BootstrapReceiptMode,
)
from modlab.adapters.mo2.bootstrap_config import render_modorganizer_ini
from modlab.adapters.mo2.bootstrap_serialization import (
    journal_from_dict,
    journal_to_dict,
    plan_from_dict,
    plan_to_dict,
    receipt_from_dict,
    receipt_to_dict,
)
from modlab.adapters.mo2.bridge_runtime_capability import (
    ARCHIVE as EXPECTED_ARCHIVE,
    LAYOUTS,
    PHASES,
    RUNTIME_PATHS,
    candidate_sources,
    capability_to_bytes,
    launch_environment,
    load_capability,
    loaded_from_logs,
    own_inventory,
    parse_json,
    publish_capability,
    publish_document,
    read_exact,
    run_root as capability_run_root,
    select_capability,
)
from modlab.adapters.mo2.processes import enumerate_windows_processes
from modlab.adapters.mo2 import bridge_runtime_capability as runtime_capability
from modlab.adapters.mo2.path_budget import (
    COMPONENT_LIMIT,
    LIMITS,
    MAX_PATHS,
    PathBudget,
    PathBudgetError,
    PlannedPath,
    admit_paths,
    bootstrap_paths,
    budget_from_dict,
    budget_to_dict,
    publication_paths,
    stage_name,
)
from modlab.adapters.mo2.release import bundled_mo2_252_path, load_mo2_release, load_mo2_release_bytes
from modlab.artifacts.serialization import artifact_from_dict, artifact_to_dict
from modlab.artifacts.vault import ArchiveVault
from modlab.adapters.skyrim.windows_version import read_windows_file_version
from modlab.platform import windows_exact_fs
from modlab.validation.mo2_containment_model import (
    ContainmentScenario,
    ProtectedState,
    WatchEvidenceCompletion,
)
from modlab.validation.mo2_containment_serialization import (
    source_artifact_id_for,
    watch_outcome_from_bytes,
    watch_outcome_id_for,
    watch_outcome_to_bytes,
)
from modlab.validation.mo2_containment_service import (
    ContainmentEffects,
    ContainmentServiceResult,
)
from modlab.validation.mo2_containment_store import ContainmentStore, evidence_root_for
from modlab.validation.windows_vault_security import create_vault, open_vault
from modlab.validation import mo2_containment_service as containment_service
from modlab.validation.mo2_containment_fixtures import _external_low_watch_roots
from modlab.validation.windows_integrity import (
    IntegrityLevel,
    inspect_path_integrity,
    launch_low_integrity_process,
    set_low_integrity_tree,
)
from modlab.validation.windows_junction import stable_tree_identity
from modlab.validation import windows_watch
from modlab.validation.windows_watch import start_watch, stop_watch, watch_proves_unchanged
from modlab.validation.windows_watch_protocol import (
    CAUSAL_NAMES,
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
    WatchReceipt,
    WatchRequest,
    WatchRoot,
    controller_claim_from_bytes,
    controller_claim_to_bytes,
    watch_request_from_bytes,
    watch_request_sha256,
    watch_request_to_bytes,
    worker_launch_from_bytes,
    worker_launch_to_bytes,
)
from modlab.workflows.skyrim.mo2_bootstrap import (
    Mo2BootstrapRefusal,
    _move_exact_directory,
    apply_mo2_setup,
    prepare_mo2_setup,
)
from modlab.workspace import workspace_layout


SCHEMA_VERSION = 1
HARNESS_POLICY_VERSION = 1
EFFECT_POLICY_VERSION = 1
PROTOCOL_VERSION = 2
PUBLICATION_POLICY_VERSION = "handle-pinned-no-replace-v2"
RUNTIME_POLICY_VERSION = 1
FIXTURE_VERSION = 2
TAR_CWD_LIMIT = LIMITS["tar-cwd"]
COMPLETE_PATH_LIMIT = LIMITS["preparation-wide"]
INVENTORY_LIMIT = MAX_PATHS
MAX_PREPARATION_INPUT_BYTES = 2 * 1024 * 1024
MAX_GATE_RECORD_BYTES = 16 * 1024 * 1024
# The complete two-layout bootstrap/path evidence exceeds an ordinary record.
MAX_PREPARATION_RECORD_BYTES = 64 * 1024 * 1024
MAX_RETAINED_LOGS = 128
MAX_GATE_INVENTORY_ENTRIES = 16384
MAX_SCREENSHOT_BYTES = 16 * 1024 * 1024
MAX_PNG_DECOMPRESSED_BYTES = 256 * 1024 * 1024
_RUN = re.compile(r"^[0-9a-f]{32}$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_JOB = re.compile(r"^bootstrap-job:[0-9a-f]{32}$")
_ID64 = re.compile(r"^[a-z][a-z0-9-]*:[0-9a-f]{64}$")
_NOREGISTER = b"[General]\r\nnoregister=true\r\n"
_ALLOWED_RUNTIME_PREFIXES = (
    "logs/",
    "webcache/",
    "cache/",
)
REPO_ROOT = _SCRIPT_REPO_ROOT
MO2_FILE_VERSION = "2.5.2.0"


class GateError(RuntimeError):
    """The disposable attempt cannot advance without weakening its contract."""


@dataclass(frozen=True)
class GateConfig:
    run_root: Path
    archive_path: Path
    steam_root: Path
    source: Mapping[str, str]
    bootstrap_job_ids: Mapping[str, str]
    authority_root: Path = field(kw_only=True)

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_root", Path(self.run_root).absolute())
        object.__setattr__(self, "authority_root", Path(self.authority_root).absolute())
        if (self.authority_root != authority_run_root(self.run_root)
                or self.run_root.is_relative_to(self.authority_root)
                or self.authority_root.is_relative_to(self.run_root)):
            raise GateError("authority root must be the disjoint evidence vault for this fresh run")
        object.__setattr__(self, "archive_path", Path(self.archive_path).absolute())
        object.__setattr__(self, "steam_root", Path(self.steam_root).absolute())
        if _RUN.fullmatch(self.run_root.name) is None:
            raise GateError("run root must end in one fresh 32-lowercase-hex ID")
        if set(self.bootstrap_job_ids) != set(LAYOUTS):
            raise GateError("both and only the two layout bootstrap job IDs are required")
        if any(_JOB.fullmatch(value) is None for value in self.bootstrap_job_ids.values()):
            raise GateError("bootstrap job identities must be complete and fresh")
        if len(set(self.bootstrap_job_ids.values())) != len(LAYOUTS):
            raise GateError("bootstrap jobs must be distinct")
        required_source = {"commit", "tree"}
        if set(self.source) != required_source or any(
            _HEX40.fullmatch(self.source[name]) is None for name in required_source
        ):
            raise GateError("source commit and tree identities are required")


def authority_run_root(run_root: Path) -> Path:
    """Derive evidence from trusted path configuration, never a disposable record."""
    root = Path(run_root).absolute()
    if _RUN.fullmatch(root.name) is None or ".." in root.parts:
        raise GateError("invalid disposable run identity")
    return evidence_root_for(root.parent).with_name("mo2-containment") / root.name


def _evidence_store(run_root: Path) -> ContainmentStore:
    root = Path(run_root).absolute()
    return ContainmentStore.open_readonly(root.parent, authority_root=authority_run_root(root).parent)


def _read_gate_evidence(run_root: Path, target: Path, *, maximum_bytes: int = MAX_GATE_RECORD_BYTES) -> bytes:
    return _evidence_store(run_root).read_evidence_file(
        "containment-run:" + Path(run_root).name, target, maximum_bytes=maximum_bytes,
    )


def _load_gate_json(run_root: Path, target: Path) -> object:
    maximum_bytes = (MAX_PREPARATION_RECORD_BYTES
                     if Path(target).absolute() == authority_run_root(run_root) / "preparation.json"
                     else MAX_GATE_RECORD_BYTES)
    data = _read_gate_evidence(run_root, target, maximum_bytes=maximum_bytes)
    value = parse_json(data)
    if _canonical(value) != data:
        raise GateError("protected gate record is not canonical")
    return value


def _publish_gate_json(run_root: Path, target: Path, value: object) -> object:
    with open_vault(authority_run_root(run_root)) as vault:
        vault.verify_descendant(Path(target).parent)
        result = publish_exact_json(target, value)
        vault.verify_descendant(target)
        if _load_gate_json(run_root, target) != result:
            raise GateError("protected gate publication readback differs")
        return result


def _preparation_capture_paths(authority: Path) -> dict[str, Path]:
    captures = {"harness": authority / "originals" / "harness.py",
                "nativeHelper": authority / "originals" / "windows_exact_fs.py",
                "release": authority / "originals" / "release.json"}
    for layout in LAYOUTS:
        for key, name in (("metadata", "artifact.json"), ("ini", "ModOrganizer.ini"), ("marker", "nxmhandler.ini")):
            captures[layout + ":" + key] = authority / layout / "originals" / name
    return captures


def _capture_preparation_originals(config: GateConfig, record: Mapping[str, object], vault: object, release_path: Path) -> dict[str, object]:
    """Retain bounded source bytes and the original native observations, before use."""
    store = _evidence_store(config.run_root)
    destinations = _preparation_capture_paths(config.authority_root)
    sources = {"harness": Path(__file__).absolute(), "nativeHelper": Path(windows_exact_fs.__file__).absolute(),
               "release": release_path}
    observations = {}
    for row in record["layouts"]:
        layout = row["layout"]
        workspace = Path(row["workspace"])
        artifact = artifact_from_dict(row["artifact"])
        sources[layout + ":metadata"] = workspace / "library" / "metadata" / "artifacts" / (artifact.sha256 + ".json")
        sources[layout + ":ini"] = Path(row["configuration"]["modOrganizerIni"]["path"])
        sources[layout + ":marker"] = Path(row["configuration"]["noregister"]["path"])
        observations[layout] = {
            "payload": _stable_file(workspace / Path(*artifact.stored_relative_path.split("/"))),
            "relocation": row["relocation"], "configuration": row["configuration"],
            "runtime": row["runtime"], "writableBaseline": row["writableBaseline"],
        }
    captures = {}
    ledger = containment_service._current_effects()
    for key, target in destinations.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        vault.verify_descendant(target.parent)
        # Capture can publish an original/provenance and then fail verification or
        # close. Admit both attempted writes before delegating, as for other gate publications.
        ledger.write(target)
        ledger.write(target.with_name(target.name + ".capture.json"))
        captures[key] = store.capture_evidence_file("containment-run:" + config.run_root.name,
            sources[key], target, maximum_bytes=MAX_PREPARATION_INPUT_BYTES, stage="Preparation")
    original = {"schemaVersion": 1, "runId": config.run_root.name, "authorityRoot": str(config.authority_root),
        "source": record["source"], "archive": record["archive"], "extractor": record["extractor"],
        "game": record["game"], "layouts": observations, "captures": captures}
    target = config.authority_root / "preparation-originals.json"
    ledger.write(target)
    _publish_gate_json(config.run_root, target, original)
    return original


def _load_preparation_originals(root: Path, preparation: Mapping[str, object]) -> tuple[dict, dict[str, bytes]]:
    authority = authority_run_root(root)
    original = _load_gate_json(root, authority / "preparation-originals.json")
    _exact_object(original, {"schemaVersion", "runId", "authorityRoot", "source", "archive", "extractor", "game", "layouts", "captures"}, "preparation originals")
    if (original["schemaVersion"] != 1 or original["runId"] != root.name
            or original["authorityRoot"] != str(authority)
            or preparation["originalsSha256"] != _sha256(_canonical(original))
            or any(original[key] != preparation[key] for key in ("source", "archive", "extractor", "game"))):
        raise GateError("preparation original observations differ")
    paths = _preparation_capture_paths(authority)
    if type(original["captures"]) is not dict or set(original["captures"]) != set(paths):
        raise GateError("preparation captures are incomplete")
    if type(original["layouts"]) is not dict or set(original["layouts"]) != set(LAYOUTS):
        raise GateError("preparation layout observations are incomplete")
    data = {}
    for key, target in paths.items():
        receipt = original["captures"][key]
        _exact_object(receipt, {"schemaVersion", "sourcePath", "capturePath", "volumeSerial", "fileId", "byteCount", "sha256", "stage"}, "preparation capture")
        if (receipt["schemaVersion"] != 1 or receipt["capturePath"] != str(target)
                or receipt["stage"] != "Preparation"
                or type(receipt["byteCount"]) is not int or not 0 < receipt["byteCount"] <= MAX_PREPARATION_INPUT_BYTES
                or _load_gate_json(root, target.with_name(target.name + ".capture.json")) != receipt):
            raise GateError("preparation capture provenance differs")
        captured = _read_gate_evidence(root, target, maximum_bytes=MAX_PREPARATION_INPUT_BYTES)
        if len(captured) != receipt["byteCount"] or _sha256(captured) != receipt["sha256"]:
            raise GateError("preparation captured bytes differ")
        data[key] = captured
    for row in preparation["layouts"]:
        observed = original["layouts"][row["layout"]]
        _exact_object(observed, {"payload", "relocation", "configuration", "runtime", "writableBaseline"}, "prepared layout original observations")
        if any(observed[key] != row[key] for key in ("relocation", "configuration", "runtime", "writableBaseline")):
            raise GateError("preparation layout original observations differ")
        artifact = artifact_from_dict(row["artifact"])
        expected_sources = {"metadata": Path(row["workspace"]) / "library" / "metadata" / "artifacts" / (artifact.sha256 + ".json"),
            "ini": Path(row["appRoot"]) / "ModOrganizer.ini", "marker": Path(row["appRoot"]) / "nxmhandler.ini"}
        for kind, source in expected_sources.items():
            receipt = original["captures"][row["layout"] + ":" + kind]
            if receipt["sourcePath"] != str(source):
                raise GateError("preparation capture source binding differs")
            if kind != "metadata":
                file = row["configuration"]["modOrganizerIni" if kind == "ini" else "noregister"]
                if (receipt["sha256"] != file["sha256"] or receipt["byteCount"] != file["size"]
                        or receipt["volumeSerial"] != file["identity"]["volumeSerial"] or receipt["fileId"] != file["identity"]["fileId"]):
                    raise GateError("preparation capture original identity differs")
    if (original["captures"]["harness"]["sourcePath"] != str(Path(__file__).absolute())
            or original["captures"]["nativeHelper"]["sourcePath"] != str(Path(windows_exact_fs.__file__).absolute())
            or preparation["harnessSha256"] != _sha256(data["harness"])
            or preparation["nativeHelperSha256"] != _sha256(data["nativeHelper"])):
        raise GateError("preparation captured source binding differs")
    return original, data


@dataclass(frozen=True)
class GatePathAdmission:
    bootstrap_budget: PathBudget
    tar_cwd_limit: int = TAR_CWD_LIMIT
    complete_path_limit: int = COMPLETE_PATH_LIMIT
    component_limit: int = COMPONENT_LIMIT
    inventory_limit: int = INVENTORY_LIMIT


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git_source() -> dict[str, str]:
    try:
        commit = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        tree = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD^{tree}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "-C", str(REPO_ROOT), "diff", "--exit-code", "HEAD", "--", "modlab", "tests"],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise GateError("current tracked source identity is unavailable or dirty") from error
    if _HEX40.fullmatch(commit) is None or _HEX40.fullmatch(tree) is None:
        raise GateError("current Git commit/tree identity is malformed")
    return {"commit": commit, "tree": tree}


def _json_value(value: object) -> object:
    if isinstance(value, Path):
        return str(value.absolute())
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, SimpleNamespace):
        return _json_value(vars(value))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if value is None or type(value) in {str, int, bool, float}:
        return value
    raise GateError(f"unsupported evidence value: {type(value).__name__}")


def _identity(value: object) -> dict[str, int]:
    if value is None:
        raise GateError("filesystem identity is unavailable")
    result = {
        "volumeSerial": getattr(value, "volume_serial", None),
        "fileId": getattr(value, "file_id", None),
        "attributes": getattr(value, "attributes", None),
    }
    if any(type(item) is not int or item < 0 for item in result.values()):
        raise GateError("filesystem identity is malformed")
    return result


def _stable_file(path: Path) -> dict[str, object]:
    source = Path(path).absolute()
    before = windows_exact_fs.identity_at_path(source)
    data = read_exact(source)
    after = windows_exact_fs.identity_at_path(source)
    if before != after:
        raise GateError(f"file identity changed while reading: {source}")
    return {
        "path": str(source),
        "sha256": _sha256(data),
        "size": len(data),
        "identity": _identity(before),
    }


def _set_low_integrity_receipted(path: Path) -> object:
    """Apply Low integrity and bind the exact icacls receipt to the service ledger."""
    receipt = set_low_integrity_tree(Path(path).absolute())
    value = _json_value(receipt)
    canonical = _canonical(value)
    containment_service._current_effects().preparation_process(
        "icacls-low:" + _sha256(canonical) + ":" + canonical.decode("utf-8").rstrip("\n")
    )
    return receipt


def _vault_paths(workspace: Path, archive_path: Path) -> tuple[PlannedPath, ...]:
    digest = EXPECTED_ARCHIVE["sha256"]
    extension = archive_path.suffix.lower()
    jobs = workspace / "runtime" / "jobs"
    metadata = workspace / "library" / "metadata" / "artifacts"
    payload = workspace / "library" / "archives" / digest[:2] / digest / ("payload" + extension)
    token = "f" * 32
    return tuple(
        PlannedPath("archive-retention", "preparation-wide", str(path.absolute()))
        for path in (
            jobs / f"import-{token}.part",
            metadata / f"metadata.{token}.part",
            metadata / f"{digest}.json",
            payload,
        )
    )


def _runtime_paths(run_root: Path, layout: str, sources: Mapping[str, bytes]) -> tuple[PlannedPath, ...]:
    layout_root = run_root / layout
    bootstrap_root = layout_root / "bootstrap"
    manager_root = workspace_layout(bootstrap_root).skyrim_mo2
    rows = [
        run_root,
        layout_root,
        layout_root / "app",
        manager_root,
        manager_root / "downloads",
        manager_root / "mods",
        manager_root / "profiles",
        manager_root / "overwrite",
        manager_root / "webcache",
        manager_root / "logs",
        layout_root / "environment",
        layout_root / "environment" / "TEMP",
        layout_root / "environment" / "TMP",
        layout_root / "environment" / "APPDATA",
        layout_root / "environment" / "LOCALAPPDATA",
        layout_root / "environment" / "USERPROFILE",
        layout_root / "environment" / "HOME",
    ]
    publication_targets = [
        layout_root / "app" / "nxmhandler.ini",
        layout_root / "installation.json",
        layout_root / "installation-failure.json",
        run_root / "preparation.json",
        run_root / "preparation-effect.json",
        run_root / "failure.json",
        run_root / "restart.json",
        run_root / "selection.json",
        run_root / "full-stack-envelope.json",
    ]
    rows.extend(publication_targets)
    for relative in sources:
        target = layout_root / "app" / "plugins" / Path(*relative.split("/"))
        rows.append(target)
        publication_targets.append(target)
    for phase in PHASES:
        rows.append(layout_root / "jobs" / phase)
        job = authority_run_root(run_root) / layout / "jobs" / phase
        watch = job / "watch"
        phase_targets = (
            job / "begin.json",
            job / "process.json",
            job / "operator-evidence.json",
            job / "runtime-delta.json",
            job / "observation.json",
            job / "effect.json",
            job / "phase-final.json",
            job / "failure.json",
            job / "cleanup.json",
            job / "window.png",
            job / "guarded.json",
            job / "mo2.log",
            watch / REQUEST_NAME,
            watch / CLAIM_NAME,
            watch / LAUNCH_NAME,
            watch / READY_NAME,
            watch / STOP_NAME,
            watch / EVENTS_NAME,
            watch / TERMINAL_NAME,
            watch / CONTROLLER_LOSS_NAME,
            watch / OUTCOME_NAME,
            *(watch / name for name in sorted(set(CAUSAL_NAMES.values()))),
            job / "window.png.capture.json",
        )
        rows.extend((job, watch, *phase_targets))
        publication_targets.extend(phase_targets)
        retained_logs = tuple(job / f"observed-{index:03}.log" for index in range(MAX_RETAINED_LOGS))
        retained_logs += tuple(path.with_name(path.name + ".capture.json") for path in retained_logs)
        rows.extend(retained_logs)
        publication_targets.extend(retained_logs)
    planned = [
        PlannedPath(f"gate-runtime:{layout}", "preparation-wide", str(path.absolute()))
        for path in rows
    ]
    for path in publication_targets:
        planned.extend(publication_paths(path.absolute(), f"gate-runtime:{layout}:publication"))
    return tuple(planned)


def admit_gate_paths(
    run_root: Path,
    listing: ArchiveListing,
    archive_path: Path,
    game_root: Path,
    bootstrap_job_ids: Mapping[str, str],
) -> GatePathAdmission:
    """Prove the complete deterministic physical layout without creating it."""
    root = Path(run_root).absolute()
    archive = Path(archive_path).absolute()
    game = Path(game_root).absolute()
    if _RUN.fullmatch(root.name) is None:
        raise GateError("invalid disposable run root")
    if set(bootstrap_job_ids) != set(LAYOUTS):
        raise GateError("path admission requires both layout jobs")
    rows: list[PlannedPath] = [
        PlannedPath("gate-input:archive", "preparation-wide", str(archive)),
        PlannedPath("gate-input:game", "preparation-wide", str(game)),
    ]
    try:
        for layout in LAYOUTS:
            job_id = bootstrap_job_ids[layout]
            if _JOB.fullmatch(job_id) is None:
                raise GateError("path admission received an invalid bootstrap job")
            workspace = root / layout / "bootstrap"
            rows.extend(_vault_paths(workspace, archive))
            rows.extend(
                bootstrap_paths(
                    workspace,
                    listing,
                    job_id=job_id,
                    layout_version=2,
                    disposition=BootstrapDisposition.CREATE.value,
                    archive_path=archive,
                )
            )
            rows.extend(_runtime_paths(root, layout, candidate_sources(root, layout)))
        authority = authority_run_root(root)
        targets = [authority / name for name in ("preparation.json", "preparation-effect.json", "preparation-originals.json", "failure.json", "restart.json", "selection.json", "full-stack-envelope.json")]
        for layout in LAYOUTS:
            targets.extend(authority / layout / name for name in ("installation.json", "installation-failure.json"))
        for target in _preparation_capture_paths(authority).values():
            targets.extend((target, target.with_name(target.name + ".capture.json")))
        for target in targets:
            rows.extend(publication_paths(target, "gate-authority:publication"))
            rows.append(PlannedPath("gate-authority", "preparation-wide", str(target)))
        return GatePathAdmission(admit_paths(rows))
    except (OSError, PathBudgetError, ValueError) as error:
        raise GateError(f"full-stack path budget refused before mutation: {error}") from error


def relocate_created_app(source: Path, destination: Path) -> dict[str, object]:
    """Move exactly one Created app through the shared pinned rename boundary."""
    source = Path(source).absolute()
    destination = Path(destination).absolute()
    if not source.is_dir() or source.is_symlink():
        raise GateError("relocation source must be a direct existing directory")
    if not destination.parent.is_dir() or destination.parent.is_symlink():
        raise GateError("relocation destination parent must be a direct existing directory")
    if destination.exists():
        raise GateError("relocation destination already exists")
    source_identity = windows_exact_fs.identity_at_path(source)
    source_parent_identity = windows_exact_fs.identity_at_path(source.parent)
    destination_parent_identity = windows_exact_fs.identity_at_path(destination.parent)
    if source_identity.volume_serial != destination_parent_identity.volume_serial:
        raise GateError("relocation must remain on one volume")

    def validate_before() -> None:
        if (
            windows_exact_fs.identity_at_path(source) != source_identity
            or windows_exact_fs.identity_at_path(source.parent) != source_parent_identity
            or windows_exact_fs.identity_at_path(destination.parent) != destination_parent_identity
            or destination.exists()
        ):
            raise GateError("relocation identity changed before the pinned rename")

    def validate_after() -> None:
        if source.exists() or windows_exact_fs.identity_at_path(destination) != source_identity:
            raise GateError("relocation destination is not the exact source object")
        if (
            windows_exact_fs.identity_at_path(source.parent) != source_parent_identity
            or windows_exact_fs.identity_at_path(destination.parent) != destination_parent_identity
        ):
            raise GateError("relocation parent identity changed")

    _move_exact_directory(
        source,
        destination,
        validate_before=validate_before,
        validate_after=validate_after,
    )
    return {
        "mechanism": "windows-exact-fs.rename_pinned_no_replace",
        "sameVolume": True,
        "noReplace": True,
        "source": str(source),
        "destination": str(destination),
        "sourceIdentity": _identity(source_identity),
        "destinationIdentity": _identity(windows_exact_fs.identity_at_path(destination)),
        "sourceParentIdentity": _identity(source_parent_identity),
        "destinationParentIdentity": _identity(destination_parent_identity),
    }


def publish_exact_json(path: Path, value: object) -> object:
    expected = _json_value(value)
    published = publish_document(Path(path).absolute(), expected)
    if published != expected or load_exact_json(path) != expected:
        raise GateError("immutable JSON publication exact reload failed")
    return published


def load_exact_json(path: Path) -> object:
    data = read_exact(Path(path).absolute())
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise GateError("published evidence is not valid UTF-8 JSON") from error
    if _canonical(value) != data:
        raise GateError("published evidence is not canonical JSON")
    return value


def _bootstrap_effects(
    value: object,
    workspace: Path,
    label: str,
    *,
    record: bool = True,
) -> dict[str, object]:
    workspace = Path(workspace).absolute()

    def paths(name: str) -> list[str]:
        raw = getattr(value, name, ())
        if type(raw) is not tuple:
            raise GateError(f"{label} {name} is not an exact tuple")
        result: list[str] = []
        seen: set[str] = set()
        for item in raw:
            if isinstance(item, Path):
                if not item.is_absolute() or item != item.absolute():
                    raise GateError(f"{label} {name} contains a non-canonical Path")
                target = item.absolute()
            elif type(item) is str:
                if (
                    not item
                    or "\\" in item
                    or Path(item).is_absolute()
                    or any(part in {"", ".", ".."} for part in item.split("/"))
                ):
                    raise GateError(f"{label} {name} contains an unsafe relative path")
                target = workspace.joinpath(*item.split("/")).absolute()
            else:
                raise GateError(f"{label} {name} contains a non-path value")
            try:
                admit_paths((
                    PlannedPath(
                        f"{label}:{name}",
                        "preparation-wide",
                        str(target),
                    ),
                ))
            except (OSError, PathBudgetError, ValueError) as error:
                raise GateError(
                    f"{label} {name} contains an unsupported Windows path: {error}"
                ) from error
            if not _inside(target, workspace):
                raise GateError(f"{label} {name} escapes the exact bootstrap workspace")
            key = str(target).casefold()
            if key in seen:
                raise GateError(f"{label} {name} contains a path collision")
            seen.add(key)
            result.append(str(target))
        return result

    result = {
        "paths_written": paths("paths_written"),
        "manager_changes": paths("manager_changes"),
    }
    for name in ("downloads", "installations", "game_changes", "programs_launched"):
        raw = getattr(value, name, ())
        if type(raw) is not tuple or any(type(item) is not str or not item for item in raw):
            raise GateError(f"{label} {name} is malformed")
        result[name] = list(raw)
    if result["game_changes"]:
        raise GateError(f"{label} unexpectedly reports a real-game mutation")
    if record:
        for item in result["programs_launched"]:
            containment_service._current_effects().preparation_process(item)
        for item in (*result["paths_written"], *result["manager_changes"]):
            containment_service._current_effects().write(Path(item))
    return result


def _bootstrap_failure_evidence(
    failure: BootstrapFailureResult,
    workspace: Path,
    layout: str,
) -> dict[str, object]:
    if layout not in LAYOUTS:
        raise GateError("bootstrap failure layout is invalid")
    normalized = _bootstrap_effects(failure, workspace, "bootstrap failure")
    result = {
        "outcome": failure.outcome,
        "code": failure.code,
        "message": failure.message,
        "planId": failure.plan_id,
        "jobId": failure.job_id,
        "journal": None if failure.journal is None else journal_to_dict(failure.journal),
        "receipt": None if failure.receipt is None else receipt_to_dict(failure.receipt),
        "actionsComplete": failure.actions_complete,
        "pathsWritten": list(failure.paths_written),
        "downloads": list(failure.downloads),
        "installations": list(failure.installations),
        "managerChanges": list(failure.manager_changes),
        "gameChanges": list(failure.game_changes),
        "programsLaunched": list(failure.programs_launched),
    }
    evidence = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "task-7B-bootstrap-failure-evidence",
        "layout": layout,
        "workspace": str(Path(workspace).absolute()),
        "result": result,
        "normalizedEffects": normalized,
        "authority": False,
    }
    evidence["bootstrapFailureId"] = "bootstrap-failure-sha256:" + _sha256(_canonical(evidence))
    return _validate_bootstrap_failure_evidence(evidence, Path(workspace).absolute().parent.parent)


def _validate_bootstrap_failure_evidence(
    value: object,
    run_root: Path,
) -> dict[str, object]:
    root = Path(run_root).absolute()
    evidence = _record_id(
        value,
        "bootstrapFailureId",
        "bootstrap-failure-sha256:",
        "bootstrap failure evidence",
    )
    fields = {
        "schemaVersion", "kind", "layout", "workspace", "result",
        "normalizedEffects", "authority", "bootstrapFailureId",
    }
    layout = evidence.get("layout")
    workspace = root / str(layout) / "bootstrap"
    if (
        set(evidence) != fields
        or evidence.get("schemaVersion") != SCHEMA_VERSION
        or evidence.get("kind") != "task-7B-bootstrap-failure-evidence"
        or layout not in LAYOUTS
        or evidence.get("workspace") != str(workspace)
        or evidence.get("authority") is not False
    ):
        raise GateError("bootstrap failure evidence binding differs")
    result = _exact_object(
        evidence.get("result"),
        {
            "outcome", "code", "message", "planId", "jobId", "journal",
            "receipt", "actionsComplete", "pathsWritten", "downloads",
            "installations", "managerChanges", "gameChanges", "programsLaunched",
        },
        "bootstrap failure result",
    )
    if (
        any(type(result.get(name)) is not str or not result[name] for name in ("outcome", "code", "message"))
        or (result.get("planId") is not None and type(result["planId"]) is not str)
        or (result.get("jobId") is not None and type(result["jobId"]) is not str)
        or type(result.get("actionsComplete")) is not bool
    ):
        raise GateError("bootstrap failure result scalar fields are malformed")
    raw_names = {
        "pathsWritten": "paths_written",
        "downloads": "downloads",
        "installations": "installations",
        "managerChanges": "manager_changes",
        "gameChanges": "game_changes",
        "programsLaunched": "programs_launched",
    }
    for name in raw_names:
        if type(result.get(name)) is not list or any(type(item) is not str or not item for item in result[name]):
            raise GateError(f"bootstrap failure result {name} is malformed")
    raw = SimpleNamespace(**{
        target: tuple(result[source]) for source, target in raw_names.items()
    })
    expected_effects = _bootstrap_effects(
        raw,
        workspace,
        "bootstrap failure",
        record=False,
    )
    if evidence.get("normalizedEffects") != expected_effects:
        raise GateError("bootstrap failure normalized effects differ")
    try:
        journal = None if result["journal"] is None else journal_from_dict(result["journal"])
        receipt = None if result["receipt"] is None else receipt_from_dict(result["receipt"])
        if journal is not None and journal_to_dict(journal) != result["journal"]:
            raise GateError("bootstrap failure journal is not canonical")
        if receipt is not None and receipt_to_dict(receipt) != result["receipt"]:
            raise GateError("bootstrap failure receipt is not canonical")
    except GateError:
        raise
    except Exception as error:
        raise GateError(f"bootstrap failure product evidence is invalid: {error}") from error
    if journal is not None and (
        result["planId"] != journal.plan_id or result["jobId"] != journal.job_id
    ):
        raise GateError("bootstrap failure journal identity differs")
    if receipt is not None and (
        result["planId"] != receipt.plan_id
        or result["jobId"] != receipt.job_id
        or (journal is not None and journal.receipt_id != receipt.receipt_id)
    ):
        raise GateError("bootstrap failure receipt identity differs")
    return evidence


def _record_bootstrap_refusal(
    error: BaseException,
    workspace: Path,
    layout: str,
) -> dict[str, object] | None:
    if not isinstance(error, Mo2BootstrapRefusal):
        return None
    failure = error.failure
    if failure is None:
        return None
    if not isinstance(failure, BootstrapFailureResult):
        raise GateError("bootstrap refusal failure evidence has the wrong type")
    evidence = _bootstrap_failure_evidence(failure, workspace, layout)
    setattr(error, "_modlab_bootstrap_failure", evidence)
    return evidence


def _bootstrap_summary(planned: object, applied: object, workspace: Path) -> dict[str, object]:
    workspace = Path(workspace).absolute()
    if not workspace.is_dir() or workspace.is_symlink():
        raise GateError("bootstrap workspace is missing or redirected")
    plan = getattr(planned, "plan", None)
    receipt = getattr(applied, "receipt", None)
    journal = getattr(applied, "journal", None)
    if plan is None or receipt is None or journal is None:
        raise GateError("bootstrap did not return complete plan, receipt, and journal evidence")
    mode = getattr(receipt, "mode", None)
    state = getattr(journal, "state", None)
    planning_effects = _bootstrap_effects(planned, workspace, "bootstrap planning")
    application_effects = _bootstrap_effects(applied, workspace, "bootstrap application")
    if mode is not BootstrapReceiptMode.CREATED or state is not BootstrapJobState.VERIFIED:
        raise GateError("only a newly Created and Verified bootstrap may enter the gate")

    return {
        "mode": mode.value,
        "state": state.value,
        "outcome": getattr(applied, "outcome", None),
        "plan": plan_to_dict(plan) if not isinstance(plan, SimpleNamespace) else _json_value(plan),
        "receipt": receipt_to_dict(receipt) if not isinstance(receipt, SimpleNamespace) else _json_value(receipt),
        "journal": journal_to_dict(journal) if not isinstance(journal, SimpleNamespace) else _json_value(journal),
        "planningEffects": planning_effects,
        "applicationEffects": application_effects,
    }


def _candidate_record(run_root: Path, layout: str) -> dict[str, object]:
    sources = candidate_sources(run_root, layout)
    return {
        "layout": layout,
        "files": [
            {
                "path": relative,
                "sha256": _sha256(data),
                "size": len(data),
                "bytesBase64": base64.b64encode(data).decode("ascii"),
            }
            for relative, data in sources.items()
        ],
    }


def _load_preparation_failure(run_root: Path) -> Mapping[str, object]:
    root = Path(run_root).absolute()
    value = _record_id(
        _load_gate_json(root, authority_run_root(root) / "failure.json"),
        "failureId",
        "preparation-failure-sha256:",
        "failed preparation record",
    )
    fields = {
        "schemaVersion", "kind", "runId", "terminal", "retryPermitted",
        "errorType", "error", "bootstrapFailure", "effects", "authority",
        "failureId",
    }
    if (
        set(value) != fields
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != "task-7B-live-gate-failed-attempt"
        or value.get("runId") != root.name
        or value.get("terminal") is not True
        or value.get("retryPermitted") is not False
        or value.get("authority") is not False
        or type(value.get("errorType")) is not str
        or not value["errorType"]
        or type(value.get("error")) is not str
        or not value["error"]
    ):
        raise GateError("failed preparation record binding differs")
    bootstrap = value.get("bootstrapFailure")
    if bootstrap is not None:
        _validate_bootstrap_failure_evidence(bootstrap, root)
    effect = validate_effect_record(value["effects"])
    if (
        effect.get("scope") != "Preparation:failed"
        or str(authority_run_root(root) / "failure.json") not in effect.get("writtenPaths", ())
        or effect.get("childMutationRoots") != [str(root)]
        or effect.get("sourceChanges") != []
        or effect.get("gameChanges") != []
        or effect.get("productionMo2Changes") != []
    ):
        raise GateError("failed preparation effect binding differs")
    return value


def _write_failure(run_root: Path, error: BaseException) -> None:
    target = authority_run_root(run_root) / "failure.json"
    if not target.parent.is_dir() or target.exists():
        return
    try:
        ledger = containment_service._current_effects()
        ledger.write(target)
        effect = build_effect_record("Preparation:failed", ledger.freeze())
        record = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "task-7B-live-gate-failed-attempt",
            "runId": run_root.name,
            "terminal": True,
            "retryPermitted": False,
            "errorType": type(error).__name__,
            "error": str(error) or type(error).__name__,
            "bootstrapFailure": getattr(error, "_modlab_bootstrap_failure", None),
            "effects": effect,
            "authority": False,
        }
        record["failureId"] = "preparation-failure-sha256:" + _sha256(_canonical(record))
        _publish_gate_json(run_root, target, record)
        if _load_preparation_failure(run_root) != record:
            raise GateError("failed preparation evidence exact reload differs")
    except BaseException as publication_error:
        publication_ownership = getattr(publication_error, "ownership", publication_error)
        if isinstance(publication_ownership, windows_exact_fs.ExactObjectOwnershipError):
            raise windows_exact_fs.union_retained_ownership(
                "preparation and failure publication retain original handles",
                prior=getattr(error, "ownership", error), owners=publication_ownership.owners,
            ) from error
        if hasattr(error, "add_note"):
            error.add_note(f"failure evidence publication also failed: {publication_error}")


def prepare_gate(config: GateConfig, *, backend: object | None = None) -> ContainmentServiceResult:
    """Prepare both layouts only after complete read-only admission succeeds."""
    if not isinstance(config, GateConfig):
        raise GateError("prepare_gate requires GateConfig")
    if config.run_root.exists() or config.authority_root.exists():
        raise GateError("disposable and authority run roots must both be completely fresh")
    selected = ProductionBackend() if backend is None else backend
    inspected = selected.inspect_inputs(config)
    admission = admit_gate_paths(
        config.run_root,
        inspected.listing,
        config.archive_path,
        inspected.game_root,
        config.bootstrap_job_ids,
    )
    return containment_service._receipted(_prepare_gate_mutation)(
        config,
        selected,
        inspected,
        admission,
    )


def _prepare_gate_mutation(
    config: GateConfig,
    selected: object,
    inspected: object,
    admission: GatePathAdmission,
) -> Mapping[str, object]:
    """Keep verified fresh authority ownership across every delegated mutation."""
    if config.run_root.exists() or config.authority_root.exists():
        raise GateError("disposable and authority run roots must both be completely fresh")
    store = ContainmentStore(config.run_root.parent, authority_root=config.authority_root.parent)
    store._create_trusted_directories(config.authority_root.parent)
    with create_vault(config.authority_root) as vault:
        containment_service._current_effects().write(config.authority_root)
        result = _prepare_gate_in_vault(config, selected, inspected, admission, vault)
        vault.verify()
        return result


def _prepare_gate_in_vault(config, selected, inspected, admission, vault):
    try:
        containment_service._current_effects().child_mutation_root(config.run_root)
        config.run_root.mkdir(parents=True, exist_ok=False)
        layouts: list[dict[str, object]] = []
        for layout in LAYOUTS:
            layout_root = config.run_root / layout
            layout_root.mkdir()
            workspace = layout_root / "bootstrap"
            artifact = selected.import_archive(layout, workspace, config.archive_path)
            artifact_record = artifact_to_dict(artifact) if not isinstance(artifact, SimpleNamespace) else _json_value(artifact)
            retained = artifact_from_dict(artifact_record)
            ledger = containment_service._current_effects()
            ledger.write(workspace / "library" / "metadata" / "artifacts" / f"{retained.sha256}.json")
            ledger.write(workspace / Path(*retained.stored_relative_path.split("/")))
            try:
                planned = selected.prepare_setup(
                    layout,
                    artifact.artifact_id,
                    workspace,
                    config.steam_root,
                )
                if getattr(planned.plan, "disposition", None) is not BootstrapDisposition.CREATE:
                    raise GateError("bootstrap plan was not a fresh Create plan")
                applied = selected.apply_setup(
                    layout,
                    planned.plan.plan_id,
                    workspace,
                    config.bootstrap_job_ids[layout],
                )
            except BaseException as bootstrap_error:
                try:
                    _record_bootstrap_refusal(bootstrap_error, workspace, layout)
                except BaseException as evidence_error:
                    if hasattr(bootstrap_error, "add_note"):
                        bootstrap_error.add_note(
                            f"bootstrap failure evidence was rejected: {evidence_error}"
                        )
                raise
            bootstrap = _bootstrap_summary(planned, applied, workspace)
            if getattr(applied.receipt, "job_id", None) != config.bootstrap_job_ids[layout]:
                raise GateError("bootstrap receipt job identity differs")
            manager_root = workspace_layout(workspace).skyrim_mo2
            app_root = layout_root / "app"
            relocation = selected.relocate(layout, manager_root / "app", app_root)
            ledger.write(app_root)
            configuration = selected.configure(layout, layout_root, manager_root)
            for configured in configuration.get("roots", ()):
                ledger.write(Path(str(configured)).absolute())
            ledger.write(layout_root / "app" / "ModOrganizer.ini")
            ledger.write(layout_root / "app" / "nxmhandler.ini")
            ledger.write(manager_root / "profiles" / "ModLab - Lab" / "modlist.txt")
            ledger.write(manager_root / "profiles" / "ModLab - Play" / "modlist.txt")
            runtime = selected.runtime_identities(layout, app_root)
            for runtime_file in runtime.get("files", ()):
                ledger.write(Path(str(runtime_file["path"])).absolute())
            writable_baseline = _snapshot_runtime_writable(config.run_root, layout)
            layouts.append(
                {
                    "layout": layout,
                    "workspace": str(workspace),
                    "managerRoot": str(manager_root),
                    "appRoot": str(app_root),
                    "artifact": artifact_record,
                    "bootstrap": bootstrap,
                    "relocation": _json_value(relocation),
                    "configuration": _json_value(configuration),
                    "runtime": _json_value(runtime),
                    "writableBaseline": writable_baseline,
                    "candidate": _candidate_record(config.run_root, layout),
                }
            )
        harness = _stable_file(Path(__file__).absolute())
        native_helper = _stable_file(Path(windows_exact_fs.__file__).absolute())
        artifact_ids = {
            str(row["artifact"].get("artifactId", row["artifact"].get("artifact_id", "")))
            for row in layouts
        }
        if len(artifact_ids) != 1 or not next(iter(artifact_ids)):
            raise GateError("both layouts must bind the same retained archive artifact")
        source_manifest = {
            "schemaVersion": 1,
            "sourceCommitId": config.source["commit"],
            "sourceTreeId": config.source["tree"],
            "mo2ArtifactId": next(iter(artifact_ids)),
            "releaseDescriptorSha256": str(inspected.release_descriptor_sha256),
            "archiveSha256": str(inspected.archive["sha256"]),
        }
        game_root = Path(inspected.game_root).absolute()
        record = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "task-7B-live-gate-preparation",
            "runId": config.run_root.name,
            "runRoot": str(config.run_root),
            "authorityRoot": str(config.authority_root),
            "steamRoot": str(config.steam_root),
            "bootstrapJobIds": dict(config.bootstrap_job_ids),
            "source": dict(config.source),
            "sourceArtifactManifest": source_manifest,
            "sourceArtifactId": source_artifact_id_for(source_manifest),
            "archive": _json_value(inspected.archive),
            "extractor": _json_value(inspected.extractor),
            "mo2": _json_value(inspected.mo2),
            "gameRoot": str(game_root),
            "game": {
                "root": str(game_root),
                "rootIdentity": _identity(windows_exact_fs.identity_at_path(game_root)),
                "skyrimExecutable": _stable_file(game_root / "SkyrimSE.exe"),
            },
            "pathAdmission": {
                "sha256": admission.bootstrap_budget.sha256,
                "budget": budget_to_dict(admission.bootstrap_budget),
                "runtimeQualified": False,
                "tarCwdLimit": admission.tar_cwd_limit,
                "completePathLimit": admission.complete_path_limit,
                "componentLimit": admission.component_limit,
                "inventoryLimit": admission.inventory_limit,
            },
            "harnessSha256": harness["sha256"],
            "nativeHelperSha256": native_helper["sha256"],
            "policies": {
                "harness": HARNESS_POLICY_VERSION,
                "effect": EFFECT_POLICY_VERSION,
                "publication": PUBLICATION_POLICY_VERSION,
                "protocol": PROTOCOL_VERSION,
                "runtime": RUNTIME_POLICY_VERSION,
                "fixture": FIXTURE_VERSION,
            },
            "layouts": layouts,
            "authority": False,
        }
        ledger = containment_service._current_effects()
        original = _capture_preparation_originals(config, record, vault, Path(getattr(selected, "release_path", bundled_mo2_252_path())).absolute())
        record["originalsSha256"] = _sha256(_canonical(original))
        preparation_path = config.authority_root / "preparation.json"
        effect_path = config.authority_root / "preparation-effect.json"
        ledger.write(preparation_path)
        ledger.write(effect_path)
        preparation_effect = build_effect_record("Preparation", ledger.freeze())
        record["preparationEffectId"] = preparation_effect["effectId"]
        record["preparationId"] = "preparation-sha256:" + _sha256(_canonical(record))
        effect_record = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "task-7B-preparation-effect",
            "preparationId": record["preparationId"],
            "effect": preparation_effect,
            "authority": False,
        }
        _publish_gate_json(config.run_root, preparation_path, record)
        _publish_gate_json(config.run_root, effect_path, effect_record)
        if load_exact_json(preparation_path) != record or load_exact_json(effect_path) != effect_record:
            raise GateError("preparation and effect exact reload differs")
        if isinstance(selected, ProductionBackend):
            strict = _fresh_preparation_preflight(config.run_root)
            if strict != record:
                raise GateError("production preparation strict reload differs")
        return record
    except BaseException as error:
        _write_failure(config.run_root, error)
        raise


class ProductionBackend:
    """Thin production seams used only by an explicitly invoked live preparation."""

    def __init__(self, release_path: Path | None = None, extractor_path: Path | None = None) -> None:
        self.release_path = Path(release_path or bundled_mo2_252_path()).absolute()
        self.extractor_path = Path(extractor_path or Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe").absolute()

    def inspect_inputs(self, config: GateConfig) -> SimpleNamespace:
        if config.run_root != capability_run_root(config.run_root.name):
            raise GateError("live run root is outside the exact capability namespace")
        if dict(config.source) != _git_source():
            raise GateError("supplied source identity differs from current clean Git source")
        release = load_mo2_release(self.release_path)
        archive = _stable_file(config.archive_path)
        expected = release.descriptor
        if (
            archive["sha256"] != expected.archive_sha256
            or archive["size"] != expected.archive_size
            or config.archive_path.name != expected.archive_name
        ):
            raise GateError("live archive identity does not match the curated MO2 2.5.2 release")
        archive.update(originalName=config.archive_path.name)
        extractor = observe_bsdtar(self.extractor_path)
        listing = preflight_archive(
            config.archive_path,
            expected,
            Path(extractor.executable.path),
        )
        game_root = config.steam_root / "steamapps" / "common" / "Skyrim Special Edition"
        if not game_root.is_dir() or not (game_root / "SkyrimSE.exe").is_file():
            raise GateError("the exact Steam Skyrim Special Edition root is unavailable")
        return SimpleNamespace(
            listing=listing,
            archive=archive,
            extractor={
                "path": extractor.executable.path,
                "sha256": extractor.executable.sha256,
                "size": extractor.executable.size,
                "version": extractor.version,
            },
            release_descriptor_sha256=release.sha256,
            mo2={
                "version": expected.executable.file_version,
                "sha256": expected.executable.sha256,
                "size": expected.executable.size,
            },
            game_root=game_root.absolute(),
        )

    def import_archive(self, layout: str, workspace: Path, archive: Path):
        return ArchiveVault(workspace).import_archive(
            archive,
            source_note=f"Task 7B disposable {layout} full-stack gate",
        )

    def prepare_setup(self, layout: str, artifact_id: str, workspace: Path, steam_root: Path):
        return prepare_mo2_setup(
            artifact_id=artifact_id,
            workspace_root=workspace,
            steam_root=steam_root,
            release_path=self.release_path,
            extractor_path=self.extractor_path,
        )

    def apply_setup(self, layout: str, plan_id: str, workspace: Path, job_id: str):
        return apply_mo2_setup(
            plan_id,
            workspace,
            release_path=self.release_path,
            extractor_path=self.extractor_path,
            job_id_factory=lambda: job_id,
        )

    def relocate(self, layout: str, source: Path, destination: Path):
        return relocate_created_app(source, destination)

    def configure(self, layout: str, layout_root: Path, manager_root: Path) -> dict[str, object]:
        environment_root = layout_root / "environment"
        directories = [
            manager_root / "downloads",
            manager_root / "mods",
            manager_root / "profiles",
            manager_root / "overwrite",
            manager_root / "webcache",
            manager_root / "logs",
            *(environment_root / name for name in ("TEMP", "TMP", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME")),
        ]
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
            if directory.is_symlink():
                raise GateError(f"configured root is redirected: {directory}")
        marker = layout_root / "app" / "nxmhandler.ini"
        if marker.exists():
            raise GateError("fresh app unexpectedly already contains nxmhandler.ini")
        windows_exact_fs.publish_new_pinned(
            marker,
            _NOREGISTER,
            lambda data: data if data == _NOREGISTER else (_ for _ in ()).throw(GateError("noregister bytes differ")),
        )
        integrity = [_json_value(_set_low_integrity_receipted(layout_root))]
        for directory in (layout_root, layout_root / "app", layout_root / "app" / "plugins", *directories):
            if inspect_path_integrity(directory) is not IntegrityLevel.LOW:
                raise GateError(f"configured disposable path is not Low integrity: {directory}")
        return {
            "managerRoot": str(manager_root),
            "environmentRoot": str(environment_root),
            "roots": [str(path) for path in directories],
            "modOrganizerIni": _stable_file(layout_root / "app" / "ModOrganizer.ini"),
            "noregister": _stable_file(marker),
            "lowIntegrity": integrity,
        }

    def runtime_identities(self, layout: str, app_root: Path) -> dict[str, object]:
        files = []
        for relative in RUNTIME_PATHS:
            path = app_root.joinpath(*relative.split("/"))
            files.append({"relativePath": relative, **_stable_file(path)})
        return {"layout": layout, "files": files}


@dataclass(frozen=True)
class DurableLayoutState:
    completed: tuple[str, ...]
    installed: bool


@dataclass
class PendingPhaseCleanup:
    """Exact partial ownership retained from the first native child callback."""

    run_root: Path
    layout: str
    phase: str
    job_root: Path
    request: WatchRequest
    executable: str
    watcher_created: bool = False
    watcher_pid: int | None = None
    mo2_created: bool = False
    mo2_pid: int | None = None
    mo2_creation_time: int | None = None
    launch: object | None = None
    process_handle: int = 0
    process_owner: object | None = None


@dataclass
class LivePhaseSession:
    run_root: Path
    layout: str
    phase: str
    preparation: Mapping[str, object]
    layout_record: Mapping[str, object]
    job_root: Path
    app_root: Path
    manager_root: Path
    executable: str
    nonce: str
    request: WatchRequest
    launch: object
    process_handle: int
    plugins_before: list[dict[str, object]]
    app_before: list[dict[str, object]]
    runtime_before: list[dict[str, object]]
    protected_before: ProtectedState
    writable_before: Mapping[str, Mapping[str, object]]


def _exact_object(value: object, fields: set[str], label: str) -> dict[str, object]:
    if type(value) is not dict or set(value) != fields:
        raise GateError(f"{label} fields are not exact")
    return value


def _inside(path: Path, root: Path) -> bool:
    candidate = Path(path).absolute()
    boundary = Path(root).absolute()
    return candidate == boundary or boundary in candidate.parents


def _file_identity_dict(value: object, label: str) -> dict[str, object]:
    record = _exact_object(value, {"path", "sha256", "size", "identity"}, label)
    _exact_object(record["identity"], {"volumeSerial", "fileId", "attributes"}, f"{label} identity")
    if (
        type(record["path"]) is not str
        or _HEX64.fullmatch(str(record["sha256"])) is None
        or type(record["size"]) is not int
        or record["size"] <= 0
    ):
        raise GateError(f"{label} is malformed")
    return record


def _require_current_file(value: object, path: Path, label: str) -> dict[str, object]:
    record = _file_identity_dict(value, label)
    expected = Path(path).absolute()
    if record["path"] != str(expected) or _stable_file(expected) != record:
        raise GateError(f"current {label} identity differs")
    return record


def _bootstrap_effect(value: object, label: str, root: Path) -> dict[str, object]:
    fields = {
        "paths_written", "downloads", "installations", "manager_changes",
        "game_changes", "programs_launched",
    }
    record = _exact_object(value, fields, label)
    for name in fields:
        if type(record[name]) is not list or any(type(item) is not str or not item for item in record[name]):
            raise GateError(f"{label} {name} is malformed")
    if record["game_changes"]:
        raise GateError(f"{label} reports a game mutation")
    for item in (*record["paths_written"], *record["manager_changes"]):
        if not _inside(Path(item), root):
            raise GateError(f"{label} path escapes the disposable run")
    return record


def _validate_prepared_layout(
    root: Path,
    preparation: Mapping[str, object],
    layout: str,
    record: object,
    jobs: Mapping[str, str],
    originals: Mapping[str, object],
    captures: Mapping[str, bytes],
) -> tuple[str, tuple[str, ...], set[Path]]:
    row = _exact_object(
        record,
        {
            "layout", "workspace", "managerRoot", "appRoot", "artifact",
            "bootstrap", "relocation", "configuration", "runtime", "writableBaseline", "candidate",
        },
        f"{layout} preparation layout",
    )
    if row["layout"] != layout:
        raise GateError("preparation layouts are incomplete or out of order")
    workspace = root / layout / "bootstrap"
    manager = workspace_layout(workspace).skyrim_mo2
    app = root / layout / "app"
    if (
        row["workspace"] != str(workspace)
        or row["managerRoot"] != str(manager)
        or row["appRoot"] != str(app)
    ):
        raise GateError("prepared workspace, manager, or app root differs from the exact layout")

    try:
        artifact = artifact_from_dict(row["artifact"])
        if artifact_to_dict(artifact) != row["artifact"]:
            raise GateError("archive artifact record is not canonical")
        metadata_path = workspace / "library" / "metadata" / "artifacts" / f"{artifact.sha256}.json"
        metadata = artifact_from_dict(parse_json(captures[layout + ":metadata"]))
        if artifact_to_dict(metadata) != row["artifact"]:
            raise GateError("archive artifact differs from retained metadata")
        payload_path = workspace / Path(*artifact.stored_relative_path.split("/"))
        payload = _file_identity_dict(originals["layouts"][layout]["payload"], "retained payload observation")
        if payload["path"] != str(payload_path):
            raise GateError("retained payload path differs")
    except GateError:
        raise
    except Exception as error:
        raise GateError(f"{layout} retained archive evidence is invalid: {error}") from error
    archive = preparation["archive"]
    if (
        artifact.sha256 != archive["sha256"]
        or artifact.size != archive["size"]
        or artifact.original_name != archive["originalName"]
        or payload["sha256"] != artifact.sha256
        or payload["size"] != artifact.size
    ):
        raise GateError("retained archive payload/top-level binding differs")

    bootstrap = _exact_object(
        row["bootstrap"],
        {"mode", "state", "outcome", "plan", "receipt", "journal", "planningEffects", "applicationEffects"},
        f"{layout} bootstrap",
    )
    try:
        plan = plan_from_dict(bootstrap["plan"])
        receipt = receipt_from_dict(bootstrap["receipt"])
        journal = journal_from_dict(bootstrap["journal"])
        if (
            plan_to_dict(plan) != bootstrap["plan"]
            or receipt_to_dict(receipt) != bootstrap["receipt"]
            or journal_to_dict(journal) != bootstrap["journal"]
        ):
            raise GateError("bootstrap product record is not canonical")
    except GateError:
        raise
    except Exception as error:
        raise GateError(f"{layout} bootstrap product record is invalid: {error}") from error
    planning = _bootstrap_effect(bootstrap["planningEffects"], f"{layout} bootstrap planning effect", root)
    application = _bootstrap_effect(bootstrap["applicationEffects"], f"{layout} bootstrap application effect", root)
    job_id = jobs[layout]
    game_file = preparation["game"]["skyrimExecutable"]
    top_extractor = preparation["extractor"]
    extractor_identity = {
        "path": top_extractor["path"],
        "sha256": top_extractor["sha256"],
        "size": top_extractor["size"],
    }
    game_identity = {
        "path": game_file["path"],
        "sha256": game_file["sha256"],
        "size": game_file["size"],
    }
    if (
        bootstrap["mode"] != BootstrapReceiptMode.CREATED.value
        or bootstrap["state"] != BootstrapJobState.VERIFIED.value
        or bootstrap["outcome"] != BootstrapReceiptMode.CREATED.value
        or plan.schema_version != 2
        or plan.disposition is not BootstrapDisposition.CREATE
        or plan.workspace_root != str(workspace)
        or plan.steam_root != preparation["steamRoot"]
        or plan.game_root != preparation["gameRoot"]
        or plan.final_root != str(manager)
        or plan.staging_parent != str(manager.parent)
        or plan.staging_name_template != ".s<job-base32>"
        or plan.release_descriptor_sha256 != preparation["sourceArtifactManifest"]["releaseDescriptorSha256"]
        or asdict(plan.skyrim_executable) != game_identity
        or asdict(plan.extractor.executable) != extractor_identity
        or plan.extractor.version != top_extractor["version"]
        or plan.archive.artifact_id != artifact.artifact_id
        or plan.archive.metadata_sha256 != _sha256(captures[layout + ":metadata"])
        or plan.archive.original_name != artifact.original_name
        or plan.archive.stored_path != artifact.stored_relative_path
        or plan.archive.sha256 != artifact.sha256
        or plan.archive.size != artifact.size
        or plan.target.kind != "Empty"
        or plan.target.root != str(manager)
    ):
        raise GateError("bootstrap plan cross-binding differs")
    plan_record = bootstrap["plan"]
    for effect_name, plan_name in (
        ("downloads", "downloads"),
        ("installations", "installations"),
        ("manager_changes", "managerChanges"),
        ("game_changes", "gameChanges"),
        ("programs_launched", "programsLaunched"),
    ):
        if planning[effect_name] != plan_record[plan_name]:
            raise GateError("bootstrap planning effects differ from the exact plan")
    if (
        receipt.schema_version != 1
        or receipt.mode is not BootstrapReceiptMode.CREATED
        or receipt.qualification != "VerifiedBootstrap"
        or receipt.plan_id != plan.plan_id
        or receipt.job_id != job_id
        or receipt.release_id != plan.release_id
        or receipt.release_descriptor_sha256 != plan.release_descriptor_sha256
        or receipt.archive_artifact_id != artifact.artifact_id
        or receipt.archive_metadata_sha256 != plan.archive.metadata_sha256
        or receipt.archive_sha256 != artifact.sha256
        or receipt.archive_size != artifact.size
        or asdict(receipt.skyrim_executable) != game_identity
        or asdict(receipt.extractor.executable) != extractor_identity
        or receipt.extractor.version != top_extractor["version"]
        or receipt.final_root != str(manager)
        or receipt.downloads_root != str(manager / "downloads")
        or receipt.mods_root != str(manager / "mods")
        or receipt.profiles_root != str(manager / "profiles")
        or receipt.overwrite_root != str(manager / "overwrite")
        or receipt.webcache_root != str(manager / "webcache")
        or receipt.mo2_executable.path != str(manager / "app" / "ModOrganizer.exe")
        or receipt.mo2_executable.sha256 != preparation["mo2"]["sha256"]
        or receipt.mo2_executable.size != preparation["mo2"]["size"]
        or receipt.repairable is not False
        or receipt.upgrade_supported is not False
    ):
        raise GateError("bootstrap receipt cross-binding differs")
    expected_stage = manager.parent / stage_name(job_id, 2)
    expected_prior = workspace / "runtime" / "jobs" / "mo2-bootstrap" / job_id.split(":", 1)[1] / "prior"
    if (
        journal.schema_version != 2
        or journal.disposition is not BootstrapDisposition.CREATE
        or journal.state is not BootstrapJobState.VERIFIED
        or journal.job_id != job_id
        or journal.plan_id != plan.plan_id
        or journal.receipt_id != receipt.receipt_id
        or journal.final_root != str(manager)
        or journal.stage_root != str(expected_stage)
        or journal.prior_root != str(expected_prior)
        or journal.prior_target_kind != "Empty"
        or journal.path_budget != plan.path_budget
        or journal.activated_inventory_sha256 != receipt.package_inventory_sha256
        or journal.activated_entry_count != receipt.package_file_count
    ):
        raise GateError("bootstrap journal cross-binding differs")

    relocation = _exact_object(
        row["relocation"],
        {
            "mechanism", "sameVolume", "noReplace", "source", "destination",
            "sourceIdentity", "destinationIdentity", "sourceParentIdentity", "destinationParentIdentity",
        },
        f"{layout} relocation",
    )
    source = manager / "app"
    if (
        relocation["mechanism"] != "windows-exact-fs.rename_pinned_no_replace"
        or relocation["sameVolume"] is not True
        or relocation["noReplace"] is not True
        or relocation["source"] != str(source)
        or relocation["destination"] != str(app)
        or relocation["sourceIdentity"] != relocation["destinationIdentity"]
    ):
        raise GateError("relocation identity/path binding differs")

    configuration = _exact_object(
        row["configuration"],
        {
            "managerRoot", "environmentRoot", "roots", "modOrganizerIni",
            "noregister", "lowIntegrity",
        },
        f"{layout} configuration",
    )
    environment = root / layout / "environment"
    configured_roots = [
        manager / "downloads", manager / "mods", manager / "profiles", manager / "overwrite", manager / "webcache", manager / "logs",
        *(environment / name for name in ("TEMP", "TMP", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME")),
    ]
    if (
        configuration["managerRoot"] != str(manager)
        or configuration["environmentRoot"] != str(environment)
        or configuration["roots"] != [str(path) for path in configured_roots]
    ):
        raise GateError("configured writable roots differ")
    manager_ini_path = app / "ModOrganizer.ini"
    manager_ini = _file_identity_dict(configuration["modOrganizerIni"], "ModOrganizer.ini")
    if manager_ini["path"] != str(manager_ini_path):
        raise GateError("prepared ModOrganizer.ini path differs")
    expected_manager_ini = render_modorganizer_ini(
        workspace_layout(workspace),
        Path(str(preparation["gameRoot"])),
        SimpleNamespace(product_version=str(preparation["mo2"]["version"])),
    )
    if captures[layout + ":ini"] != expected_manager_ini:
        raise GateError("ModOrganizer.ini no longer binds the exact contained manager and game roots")
    marker_path = app / "nxmhandler.ini"
    marker = _file_identity_dict(configuration["noregister"], "noregister marker")
    if marker["path"] != str(marker_path) or captures[layout + ":marker"] != _NOREGISTER:
        raise GateError("noregister marker bytes differ")
    low = configuration["lowIntegrity"]
    if type(low) is not list or len(low) != 1:
        raise GateError("Low-integrity application evidence is incomplete")
    low_receipt = _exact_object(
        low[0],
        {"executable", "arguments", "exit_code", "stdout_sha256", "stderr_sha256", "integrity"},
        f"{layout} Low-integrity receipt",
    )
    if (
        low_receipt["executable"] != r"C:\Windows\System32\icacls.exe"
        or low_receipt["arguments"] != [str(root / layout), "/setintegritylevel", "(OI)(CI)L", "/T", "/C", "/Q"]
        or low_receipt["exit_code"] != 0
        or low_receipt["integrity"] != int(IntegrityLevel.LOW)
        or _HEX64.fullmatch(str(low_receipt["stdout_sha256"])) is None
        or _HEX64.fullmatch(str(low_receipt["stderr_sha256"])) is None
    ):
        raise GateError("Low-integrity application receipt differs")
    runtime = _exact_object(row["runtime"], {"layout", "files"}, f"{layout} runtime")
    files = runtime["files"]
    if type(files) is not list or runtime["layout"] != layout or len(files) != len(RUNTIME_PATHS):
        raise GateError("prepared runtime record is incomplete")
    runtime_paths: list[Path] = []
    for relative, runtime_file in zip(RUNTIME_PATHS, files, strict=True):
        current_path = app.joinpath(*relative.split("/"))
        current = _exact_object(
            runtime_file,
            {"relativePath", "path", "sha256", "size", "identity"},
            f"{layout} runtime file",
        )
        if current["relativePath"] != relative:
            raise GateError("prepared runtime relative path differs")
        _file_identity_dict({key: current[key] for key in ("path", "sha256", "size", "identity")}, f"{layout} runtime {relative}")
        if current["path"] != str(current_path):
            raise GateError("prepared runtime absolute path differs")
        runtime_paths.append(current_path)
    if files[0]["sha256"] != preparation["mo2"]["sha256"] or files[0]["size"] != preparation["mo2"]["size"]:
        raise GateError("current MO2 runtime differs from the curated executable")
    if row["candidate"] != _candidate_record(root, layout):
        raise GateError("candidate source binding differs")
    _validated_writable_snapshots(
        root,
        layout,
        row["writableBaseline"],
        f"{layout} preparation writable baseline",
        require_current_roots=False,
    )

    canonical_low = _canonical(low_receipt)
    processes = (
        *planning["programs_launched"],
        *application["programs_launched"],
        "icacls-low:" + _sha256(canonical_low) + ":" + canonical_low.decode("utf-8").rstrip("\n"),
    )
    required_paths = {
        metadata_path,
        payload_path,
        app,
        manager_ini_path,
        marker_path,
        *configured_roots,
        manager / "profiles" / "ModLab - Lab" / "modlist.txt",
        manager / "profiles" / "ModLab - Play" / "modlist.txt",
        *runtime_paths,
        *(Path(item).absolute() for item in planning["paths_written"]),
        *(Path(item).absolute() for item in planning["manager_changes"]),
        *(Path(item).absolute() for item in application["paths_written"]),
        *(Path(item).absolute() for item in application["manager_changes"]),
    }
    return artifact.artifact_id, processes, required_paths


def _load_preparation(run_root: Path) -> Mapping[str, object]:
    root = Path(run_root).absolute()
    if root != capability_run_root(root.name):
        raise GateError("run root is outside the exact capability namespace")
    value = _load_gate_json(root, authority_run_root(root) / "preparation.json")
    if type(value) is not dict:
        raise GateError("preparation record is not an exact object")
    _exact_object(
        value,
        {
            "schemaVersion", "kind", "runId", "runRoot", "authorityRoot", "originalsSha256", "steamRoot",
            "bootstrapJobIds", "source", "sourceArtifactManifest", "sourceArtifactId",
            "archive", "extractor", "mo2", "gameRoot", "game", "pathAdmission",
            "harnessSha256", "nativeHelperSha256", "policies", "layouts",
            "preparationEffectId", "preparationId", "authority",
        },
        "preparation",
    )
    preparation_id = value.get("preparationId")
    body = {key: item for key, item in value.items() if key != "preparationId"}
    if preparation_id != "preparation-sha256:" + _sha256(_canonical(body)):
        raise GateError("preparation ID differs from the exact record body")
    if (
        value.get("schemaVersion") != SCHEMA_VERSION
        or
        value.get("kind") != "task-7B-live-gate-preparation"
        or value.get("runId") != root.name
        or value.get("runRoot") != str(root)
        or value.get("authority") is not False
        or value.get("authorityRoot") != str(authority_run_root(root))
    ):
        raise GateError("preparation source/run/harness binding differs")
    _exact_object(value.get("source"), {"commit", "tree"}, "preparation source")
    if any(_HEX40.fullmatch(str(value["source"][key])) is None for key in ("commit", "tree")):
        raise GateError("preparation source identity is malformed")
    originals, captures = _load_preparation_originals(root, value)
    steam_root = Path(str(value.get("steamRoot", ""))).absolute()
    expected_game_root = steam_root / "steamapps" / "common" / "Skyrim Special Edition"
    jobs = value.get("bootstrapJobIds")
    if (
        value.get("steamRoot") != str(steam_root)
        or value.get("gameRoot") != str(expected_game_root)
        or type(jobs) is not dict
        or set(jobs) != set(LAYOUTS)
        or any(_JOB.fullmatch(jobs.get(layout, "")) is None for layout in LAYOUTS)
        or len(set(jobs.values())) != len(LAYOUTS)
    ):
        raise GateError("preparation Steam/game/bootstrap-job binding differs")
    archive = value.get("archive")
    release_record = load_mo2_release_bytes(captures["release"], authority_run_root(root) / "originals" / "release.json")
    release = release_record.descriptor
    if (
        type(archive) is not dict
        or set(archive) != {"path", "sha256", "size", "identity", "originalName"}
        or archive.get("sha256") != release.archive_sha256
        or archive.get("size") != release.archive_size
        or archive.get("originalName") != release.archive_name
    ):
        raise GateError("preparation archive/release binding differs")
    if (
        archive["path"] != str(Path(archive["path"]).absolute())
        or archive["originalName"] != Path(archive["path"]).name
    ):
        raise GateError("current preparation archive identity differs")
    extractor = value.get("extractor")
    if (
        type(extractor) is not dict
        or set(extractor) != {"path", "sha256", "size", "version"}
        or not isinstance(extractor.get("path"), str)
        or _HEX64.fullmatch(str(extractor.get("sha256"))) is None
        or type(extractor.get("size")) is not int
        or extractor["size"] <= 0
        or not isinstance(extractor.get("version"), str)
        or not extractor["version"]
    ):
        raise GateError("preparation extractor binding is malformed")
    game = _exact_object(value.get("game"), {"root", "rootIdentity", "skyrimExecutable"}, "preparation game")
    _exact_object(game["rootIdentity"], {"volumeSerial", "fileId", "attributes"}, "preparation game root identity")
    if (
        game["root"] != str(expected_game_root)
    ):
        raise GateError("current game-root identity differs")
    if _file_identity_dict(game["skyrimExecutable"], "Skyrim executable")["path"] != str(expected_game_root / "SkyrimSE.exe"):
        raise GateError("prepared Skyrim executable path differs")
    policies = value.get("policies")
    if policies != {
        "harness": HARNESS_POLICY_VERSION,
        "effect": EFFECT_POLICY_VERSION,
        "publication": PUBLICATION_POLICY_VERSION,
        "protocol": PROTOCOL_VERSION,
        "runtime": RUNTIME_POLICY_VERSION,
        "fixture": FIXTURE_VERSION,
    }:
        raise GateError("preparation policy binding differs")
    _exact_object(
        value.get("sourceArtifactManifest"),
        {
            "schemaVersion", "sourceCommitId", "sourceTreeId", "mo2ArtifactId",
            "releaseDescriptorSha256", "archiveSha256",
        },
        "source-artifact manifest",
    )
    mo2 = _exact_object(value.get("mo2"), {"version", "sha256", "size"}, "prepared MO2 identity")
    if (
        type(mo2["version"]) is not str
        or _HEX64.fullmatch(str(mo2["sha256"])) is None
        or type(mo2["size"]) is not int
        or mo2["size"] <= 0
    ):
        raise GateError("prepared MO2 identity is malformed")
    layouts = value.get("layouts")
    if type(layouts) is not list or [item.get("layout") for item in layouts if isinstance(item, Mapping)] != list(LAYOUTS):
        raise GateError("preparation layouts are incomplete or out of order")
    artifact_ids: set[str] = set()
    expected_processes: list[str] = []
    expected_written_paths: set[Path] = {
        root,
        authority_run_root(root),
        authority_run_root(root) / "preparation.json",
        authority_run_root(root) / "preparation-effect.json",
        authority_run_root(root) / "preparation-originals.json",
        *_preparation_capture_paths(authority_run_root(root)).values(),
        *(path.with_name(path.name + ".capture.json") for path in _preparation_capture_paths(authority_run_root(root)).values()),
    }
    for layout, record in zip(LAYOUTS, layouts, strict=True):
        if not isinstance(record, Mapping):
            raise GateError("preparation layout record is malformed")
        strict_artifact, strict_process_rows, strict_written = _validate_prepared_layout(
            root,
            value,
            layout,
            record,
            jobs,
            originals,
            captures,
        )
        artifact_ids.add(strict_artifact)
        expected_processes.extend(strict_process_rows)
        expected_written_paths.update(strict_written)
        _layout_record(value, layout)
        artifact = record.get("artifact")
        if not isinstance(artifact, Mapping):
            raise GateError("preparation archive vault record is missing")
        artifact_id = artifact.get("artifactId", artifact.get("artifact_id"))
        if not isinstance(artifact_id, str) or not artifact_id:
            raise GateError("preparation archive artifact identity is missing")
        artifact_ids.add(artifact_id)
        bootstrap = record.get("bootstrap")
        relocation = record.get("relocation")
        receipt = bootstrap.get("receipt") if isinstance(bootstrap, Mapping) else None
        receipt_job = (
            receipt.get("jobId", receipt.get("job_id"))
            if isinstance(receipt, Mapping)
            else None
        )
        manager_root = Path(str(record.get("managerRoot", ""))).absolute()
        app_root = Path(str(record.get("appRoot", ""))).absolute()
        if (
            not isinstance(bootstrap, Mapping)
            or bootstrap.get("mode") != BootstrapReceiptMode.CREATED.value
            or bootstrap.get("state") != BootstrapJobState.VERIFIED.value
            or receipt_job != jobs[layout]
            or not isinstance(relocation, Mapping)
            or relocation.get("mechanism") != "windows-exact-fs.rename_pinned_no_replace"
            or relocation.get("sameVolume") is not True
            or relocation.get("noReplace") is not True
            or relocation.get("source") != str(manager_root / "app")
            or relocation.get("destination") != str(app_root)
            or record.get("candidate") != _candidate_record(root, layout)
        ):
            raise GateError("preparation bootstrap, relocation, or candidate binding differs")
    if len(artifact_ids) != 1:
        raise GateError("preparation layouts do not share one retained archive artifact")
    manifest = value.get("sourceArtifactManifest")
    expected_manifest = {
        "schemaVersion": 1,
        "sourceCommitId": value["source"]["commit"],
        "sourceTreeId": value["source"]["tree"],
        "mo2ArtifactId": next(iter(artifact_ids)),
        "releaseDescriptorSha256": release_record.sha256,
        "archiveSha256": release.archive_sha256,
    }
    if (
        manifest != expected_manifest
        or value.get("sourceArtifactId") != source_artifact_id_for(expected_manifest)
    ):
        raise GateError("preparation source-artifact binding differs")
    admission = value.get("pathAdmission")
    try:
        budget = budget_from_dict(admission["budget"] if isinstance(admission, Mapping) else None)
    except (KeyError, PathBudgetError, TypeError) as error:
        raise GateError("preparation path admission is malformed") from error
    if admission != {
        "sha256": budget.sha256,
        "budget": budget_to_dict(budget),
        "runtimeQualified": False,
        "tarCwdLimit": TAR_CWD_LIMIT,
        "completePathLimit": COMPLETE_PATH_LIMIT,
        "componentLimit": COMPONENT_LIMIT,
        "inventoryLimit": INVENTORY_LIMIT,
    }:
        raise GateError("preparation path admission binding differs")
    admitted_paths = {Path(row.path).absolute() for row in budget.paths}
    for layout in LAYOUTS:
        manager = workspace_layout(root / layout / "bootstrap").skyrim_mo2
        if manager.parent / stage_name(jobs[layout], 2) not in admitted_paths:
            raise GateError("bootstrap job identity is absent from the admitted physical layout")
    if value.get("mo2") != {
        "version": release.executable.file_version,
        "sha256": release.executable.sha256,
        "size": release.executable.size,
    }:
        raise GateError("preparation MO2 release binding differs")
    effect_record = _exact_object(
        _load_gate_json(root, authority_run_root(root) / "preparation-effect.json"),
        {"schemaVersion", "kind", "preparationId", "effect", "authority"},
        "preparation effect publication",
    )
    effect = validate_effect_record(effect_record["effect"])
    written = {Path(item).absolute() for item in effect["writtenPaths"]}
    if (
        effect_record["schemaVersion"] != SCHEMA_VERSION
        or effect_record["kind"] != "task-7B-preparation-effect"
        or effect_record["preparationId"] != preparation_id
        or effect_record["authority"] is not False
        or value.get("preparationEffectId") != effect["effectId"]
        or effect["scope"] != "Preparation"
        or effect["childMutationRoots"] != [str(root)]
        or effect["watcherPid"] is not None
        or effect["mo2Pid"] is not None
        or effect["sourceChanges"] != []
        or effect["gameChanges"] != []
        or effect["productionMo2Changes"] != []
        or effect["preparationProcesses"] != expected_processes
        or written != expected_written_paths
        or any(not (_inside(path, root) or _inside(path, authority_run_root(root))) for path in written)
    ):
        raise GateError("preparation service-effect binding differs")
    return value


def _fresh_preparation_preflight(run_root: Path) -> Mapping[str, object]:
    """Current-use admission; historical reconstruction alone never authorizes actions."""
    root = Path(run_root).absolute()
    value = _load_preparation(root)
    if (value["source"] != _git_source()
            or value["harnessSha256"] != _stable_file(Path(__file__))["sha256"]
            or value["nativeHelperSha256"] != _stable_file(Path(windows_exact_fs.__file__))["sha256"]):
        raise GateError("preparation source/run/harness binding differs")
    archive = value["archive"]
    if _stable_file(Path(archive["path"])) != {key: archive[key] for key in ("path", "sha256", "size", "identity")}:
        raise GateError("current preparation archive identity differs")
    release = load_mo2_release(bundled_mo2_252_path())
    if release.sha256 != value["sourceArtifactManifest"]["releaseDescriptorSha256"]:
        raise GateError("current release descriptor differs")
    extractor = value["extractor"]
    current_extractor = _stable_file(Path(extractor["path"]))
    if any(current_extractor[name] != extractor[name] for name in ("path", "sha256", "size")):
        raise GateError("current extractor identity differs")
    game = value["game"]
    if game["rootIdentity"] != _identity(windows_exact_fs.identity_at_path(Path(game["root"]))):
        raise GateError("current game-root identity differs")
    _require_current_file(game["skyrimExecutable"], Path(game["root"]) / "SkyrimSE.exe", "Skyrim executable")
    for layout in LAYOUTS:
        row = _layout_record(value, layout)
        workspace, app = Path(row["workspace"]), Path(row["appRoot"])
        artifact = artifact_from_dict(row["artifact"])
        metadata = workspace / "library" / "metadata" / "artifacts" / (artifact.sha256 + ".json")
        if artifact_to_dict(artifact_from_dict(parse_json(read_exact(metadata)))) != row["artifact"]:
            raise GateError("current retained archive metadata differs")
        if _stable_file(metadata)["sha256"] != row["bootstrap"]["plan"]["archive"]["metadataSha256"]:
            raise GateError("current retained archive metadata hash differs")
        payload = _stable_file(workspace / Path(*artifact.stored_relative_path.split("/")))
        if payload["sha256"] != artifact.sha256 or payload["size"] != artifact.size:
            raise GateError("current retained archive payload differs")
        relocation = row["relocation"]
        source = Path(relocation["source"])
        if (source.exists() or not app.is_dir() or app.is_symlink()
                or relocation["destinationIdentity"] != _identity(windows_exact_fs.identity_at_path(app))
                or relocation["sourceParentIdentity"] != _identity(windows_exact_fs.identity_at_path(source.parent))
                or relocation["destinationParentIdentity"] != _identity(windows_exact_fs.identity_at_path(app.parent))):
            raise GateError("current relocation identity/path binding differs")
        configuration = row["configuration"]
        roots = [Path(path) for path in configuration["roots"]]
        if any(not path.is_dir() or path.is_symlink() for path in roots):
            raise GateError("current configured writable roots differ")
        for key, name in (("modOrganizerIni", "ModOrganizer.ini"), ("noregister", "nxmhandler.ini")):
            _require_current_file(configuration[key], app / name, name)
        for current in (root / layout, app, app / "plugins", *roots):
            if inspect_path_integrity(current) is not IntegrityLevel.LOW:
                raise GateError(f"current disposable path is not Low integrity: {current}")
        for current in row["runtime"]["files"]:
            _require_current_file({key: current[key] for key in ("path", "sha256", "size", "identity")},
                app.joinpath(*current["relativePath"].split("/")), f"{layout} runtime {current['relativePath']}")
        _runtime_inventory(value, layout)
        _validated_writable_snapshots(root, layout, row["writableBaseline"],
            f"{layout} preparation writable baseline", require_current_roots=True)
    return value


def _layout_record(preparation: Mapping[str, object], layout: str) -> Mapping[str, object]:
    records = preparation.get("layouts")
    if type(records) is not list:
        raise GateError("preparation layout records are unavailable")
    selected = [item for item in records if isinstance(item, Mapping) and item.get("layout") == layout]
    if len(selected) != 1:
        raise GateError("preparation does not bind one exact layout")
    record = selected[0]
    root = Path(str(preparation["runRoot"]))
    workspace = root / layout / "bootstrap"
    manager = workspace_layout(workspace).skyrim_mo2
    if (
        record.get("workspace") != str(workspace)
        or record.get("managerRoot") != str(manager)
        or record.get("appRoot") != str(root / layout / "app")
    ):
        raise GateError("prepared workspace, manager, or app root differs from the exact layout")
    return record


def _load_installation(run_root: Path, layout: str) -> Mapping[str, object]:
    root = Path(run_root).absolute()
    if layout not in LAYOUTS or _RUN.fullmatch(root.name) is None:
        raise GateError("installation run/layout binding is invalid")
    authority_layout = authority_run_root(root) / layout
    value = _load_gate_json(root, authority_layout / "installation.json")
    fields = {
        "schemaVersion", "kind", "runId", "layout", "controlId", "declared",
        "before", "after", "writableBefore", "writableAfter", "observedAt",
        "effect", "authority", "installationId",
    }
    body = {
        key: item for key, item in value.items() if key != "installationId"
    } if isinstance(value, Mapping) else {}
    if (
        not isinstance(value, Mapping)
        or set(value) != fields
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("installationId") != "installation-sha256:" + _sha256(_canonical(body))
        or value.get("kind") != "task-7B-candidate-installation"
        or value.get("runId") != root.name
        or value.get("layout") != layout
        or value.get("authority") is not False
        or type(value.get("declared")) is not list
        or type(value.get("before")) is not list
        or type(value.get("after")) is not list
    ):
        raise GateError("candidate installation record binding differs")
    effect = validate_effect_record(value["effect"])
    layout_root = root / layout
    if (
        effect.get("scope") != f"{layout}:Install"
        or effect.get("watcherPid") is not None
        or effect.get("mo2Pid") is not None
        or effect.get("childMutationRoots") != [str(layout_root)]
        or str(authority_layout / "installation.json") not in effect.get("writtenPaths", ())
        or any(not (_inside(Path(path), layout_root) or _inside(Path(path), authority_layout)) for path in effect.get("writtenPaths", ()))
        or effect.get("sourceChanges") != []
        or effect.get("gameChanges") != []
        or effect.get("productionMo2Changes") != []
    ):
        raise GateError("candidate installation effect binding differs")
    control = _load_gate_json(root, authority_layout / "jobs" / "Control" / "observation.json")
    control_id = "observation-sha256:" + _sha256(_canonical(control))
    control_final = _load_gate_json(root, authority_layout / "jobs" / "Control" / "phase-final.json")
    if (
        value.get("controlId") != control_id
        or not isinstance(control_final, Mapping)
        or control_final.get("observationId") != control_id
        or value.get("before") != control.get("after")
        or own_inventory(value["after"]) != value["declared"]
    ):
        raise GateError("candidate installation Control or inventory binding differs")
    expected_after = list(value["before"]) + list(value["declared"])
    try:
        if sorted(value["after"], key=lambda item: item["path"]) != sorted(
            expected_after,
            key=lambda item: item["path"],
        ):
            raise GateError("candidate installation includes undeclared inventory")
    except (KeyError, TypeError) as error:
        raise GateError("candidate installation inventory is malformed") from error
    _validate_installation_writable_transition(
        root,
        layout,
        value["declared"],
        value["writableBefore"],
        value["writableAfter"],
    )
    return value


def snapshot_tree(root):
    """Gate Low-input inventories refuse excessive discovery before reading bytes."""
    return runtime_capability.snapshot_tree(
        root, maximum_entries=MAX_GATE_INVENTORY_ENTRIES,
        maximum_log_files=MAX_RETAINED_LOGS, maximum_log_bytes=MAX_GATE_RECORD_BYTES,
        stream_files=True,
    )


def _runtime_inventory(preparation: Mapping[str, object], layout: str) -> list[dict[str, object]]:
    record = _layout_record(preparation, layout)
    app = Path(str(record["appRoot"]))
    if read_windows_file_version(app / "ModOrganizer.exe") != MO2_FILE_VERSION:
        raise GateError("MO2 file version differs from 2.5.2.0")
    tree = {item["path"]: item for item in snapshot_tree(app)}
    actual = [tree.get(relative) for relative in RUNTIME_PATHS]
    if any(item is None for item in actual):
        raise GateError("MO2/Python/mobase runtime file is missing")
    expected_record = record.get("runtime")
    files = expected_record.get("files") if isinstance(expected_record, Mapping) else None
    if type(files) is not list or [item.get("relativePath") for item in files if isinstance(item, Mapping)] != list(RUNTIME_PATHS):
        raise GateError("prepared runtime identity record is malformed")
    for current, expected in zip(actual, files, strict=True):
        identity = expected.get("identity")
        if not isinstance(identity, Mapping) or current != {
            "path": expected["relativePath"],
            "kind": "file",
            "sha256": expected["sha256"],
            "size": expected["size"],
            "volume": identity["volumeSerial"],
            "fileId": identity["fileId"],
        }:
            raise GateError("current runtime identity differs from preparation")
    return [dict(item) for item in actual if item is not None]


def _capture_protected(preparation: Mapping[str, object], layout: str) -> ProtectedState:
    record = _layout_record(preparation, layout)
    manager = Path(str(record["managerRoot"]))
    game = Path(str(preparation["gameRoot"]))
    return ProtectedState(
        stable_tree_identity(manager / "mods", required_equal_passes=2),
        str(_stable_file(manager / "profiles" / "ModLab - Lab" / "modlist.txt")["sha256"]),
        str(_stable_file(manager / "profiles" / "ModLab - Play" / "modlist.txt")["sha256"]),
        stable_tree_identity(manager / "downloads", required_equal_passes=2),
        stable_tree_identity(manager / "overwrite", required_equal_passes=2),
        stable_tree_identity(game, required_equal_passes=2),
    )


def _derived_watch_roots(preparation: Mapping[str, object], layout: str) -> dict[str, Path]:
    record = _layout_record(preparation, layout)
    manager = Path(str(record["managerRoot"]))
    rows = [
        ("SourceMods", manager / "mods"),
        ("LabProfile", manager / "profiles" / "ModLab - Lab"),
        ("PlayProfile", manager / "profiles" / "ModLab - Play"),
        ("Downloads", manager / "downloads"),
        ("Overwrite", manager / "overwrite"),
        ("BoundedGame", Path(str(preparation["gameRoot"]))),
        *_external_low_watch_roots(),
    ]
    if tuple(name for name, _path in rows) != ROOT_KINDS:
        raise GateError("machine does not expose all eight canonical watch roots")
    return {name: Path(path).absolute() for name, path in rows}


def _validate_png_bytes(data: bytes) -> bytes:
    if type(data) is not bytes or not 45 <= len(data) <= MAX_SCREENSHOT_BYTES:
        raise GateError("screenshot PNG size is invalid or unbounded")
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise GateError("screenshot bytes do not have the PNG signature")
    offset = 8
    chunks: list[bytes] = []
    idat_parts: list[bytes] = []
    width = height = 0
    channels = 0
    saw_idat = False
    idat_closed = False
    ancillary_seen: set[bytes] = set()
    gamma_value: int | None = None
    while offset < len(data):
        if offset + 12 > len(data):
            raise GateError("screenshot PNG has a truncated chunk")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        kind = data[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(data) or any(byte not in range(65, 91) and byte not in range(97, 123) for byte in kind):
            raise GateError("screenshot PNG chunk is malformed")
        if kind[2] in range(97, 123):
            raise GateError("screenshot PNG chunk uses the reserved lowercase bit")
        if kind[0] in range(65, 91) and kind not in {b"IHDR", b"IDAT", b"IEND"}:
            raise GateError("screenshot PNG contains an unsupported critical chunk")
        payload = data[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length : end])[0]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != expected_crc:
            raise GateError("screenshot PNG chunk checksum differs")
        if kind[0] in range(97, 123):
            if kind not in {b"gAMA", b"sRGB", b"pHYs"}:
                raise GateError("screenshot PNG contains unsupported ancillary data")
            if saw_idat:
                raise GateError("screenshot PNG ancillary data must precede IDAT")
            if kind in ancillary_seen:
                raise GateError("screenshot PNG contains duplicate ancillary data")
            ancillary_seen.add(kind)
            if kind == b"gAMA":
                if length != 4:
                    raise GateError("screenshot PNG gAMA payload is malformed")
                gamma_value = struct.unpack(">I", payload)[0]
                if gamma_value == 0:
                    raise GateError("screenshot PNG gAMA value is invalid")
                if b"sRGB" in ancillary_seen and gamma_value != 45455:
                    raise GateError("screenshot PNG sRGB/gAMA values disagree")
            elif kind == b"sRGB":
                if length != 1 or payload[0] not in range(4):
                    raise GateError("screenshot PNG sRGB payload is malformed")
                if gamma_value is not None and gamma_value != 45455:
                    raise GateError("screenshot PNG sRGB/gAMA values disagree")
            else:
                if length != 9:
                    raise GateError("screenshot PNG pHYs payload is malformed")
                pixels_x, pixels_y, unit = struct.unpack(">IIB", payload)
                if pixels_x == 0 or pixels_y == 0 or unit not in {0, 1}:
                    raise GateError("screenshot PNG pHYs values are invalid")
        chunks.append(kind)
        if len(chunks) == 1:
            if kind != b"IHDR" or length != 13:
                raise GateError("screenshot PNG does not begin with one IHDR")
            width, height, depth, colour, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
            channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(colour, 0)
            if (
                width <= 0
                or height <= 0
                or depth != 8
                or channels == 0
                or compression != 0
                or filtering != 0
                or interlace != 0
                or height * (1 + width * channels) > MAX_PNG_DECOMPRESSED_BYTES
            ):
                raise GateError("screenshot PNG header is unsupported")
        elif kind == b"IHDR":
            raise GateError("screenshot PNG contains multiple IHDR chunks")
        if kind == b"IDAT":
            if idat_closed:
                raise GateError("screenshot PNG IDAT chunks are not consecutive")
            saw_idat = True
            idat_parts.append(payload)
        elif saw_idat and kind != b"IEND":
            idat_closed = True
        if kind == b"IEND":
            if length != 0 or end != len(data):
                raise GateError("screenshot PNG has an invalid terminal IEND")
            break
        offset = end
    if not chunks or chunks[-1] != b"IEND" or not saw_idat or width <= 0 or height <= 0:
        raise GateError("screenshot PNG is incomplete")
    expected_size = height * (1 + width * channels)
    try:
        decompressor = zlib.decompressobj()
        decoded = decompressor.decompress(b"".join(idat_parts), expected_size + 1)
    except zlib.error as error:
        raise GateError("screenshot PNG IDAT stream is not decodable") from error
    if (
        len(decoded) != expected_size
        or not decompressor.eof
        or decompressor.unconsumed_tail
        or decompressor.unused_data
    ):
        raise GateError("screenshot PNG decoded scanline size/stream differs")
    row_size = 1 + width * channels
    if any(decoded[index * row_size] not in range(5) for index in range(height)):
        raise GateError("screenshot PNG contains an invalid scanline filter")
    return data


def _decode_png_data_url(value: object) -> bytes:
    prefix = "data:image/png;base64,"
    if type(value) is not str or not value.startswith(prefix):
        raise GateError("screenshot must be a PNG data URL")
    encoded = value[len(prefix) :]
    if not encoded or len(encoded) > ((MAX_SCREENSHOT_BYTES + 2) // 3) * 4 + 4:
        raise GateError("screenshot data URL is empty or unbounded")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise GateError("screenshot data URL is not valid base64") from error
    return _validate_png_bytes(data)


def _native_windows_for_pid(pid: int) -> list[dict[str, object]]:
    """Enumerate real HWNDs independently; Computer Use window IDs stay opaque."""
    if os.name != "nt" or type(pid) is not int or pid <= 0:
        raise GateError("native HWND enumeration requires one Windows process PID")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    rows: list[dict[str, object]] = []

    @callback_type
    def observe(hwnd, _parameter):
        if not user32.IsWindowVisible(hwnd):
            return True
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if int(owner.value) != pid:
            return True
        title_length = int(user32.GetWindowTextLengthW(hwnd))
        title_buffer = ctypes.create_unicode_buffer(title_length + 1)
        if title_length and user32.GetWindowTextW(hwnd, title_buffer, len(title_buffer)) <= 0:
            return True
        class_buffer = ctypes.create_unicode_buffer(256)
        if user32.GetClassNameW(hwnd, class_buffer, len(class_buffer)) <= 0:
            return True
        rows.append(
            {
                "hwnd": int(hwnd),
                "pid": pid,
                "title": title_buffer.value,
                "className": class_buffer.value,
                "visible": True,
            }
        )
        return True

    ctypes.set_last_error(0)
    if not user32.EnumWindows(observe, 0):
        code = ctypes.get_last_error()
        raise OSError(code, "EnumWindows failed")
    return sorted(rows, key=lambda item: int(item["hwnd"]))


def _candidate_processes() -> tuple[object, ...]:
    rows = enumerate_windows_processes()
    if any(type(item.pid) is not int or item.pid <= 0 for item in rows):
        raise GateError("native candidate process inventory is malformed")
    return rows


def _require_process_absence() -> None:
    rows = _candidate_processes()
    if rows:
        raise GateError("ModOrganizer/nxmhandler process already exists")


def _retained_log_targets(job_root: Path, count: int) -> tuple[Path, ...]:
    if type(count) is not int or count < 0 or count > MAX_RETAINED_LOGS:
        raise GateError(f"retained MO2 log count exceeds the fixed {MAX_RETAINED_LOGS}-file budget")
    root = Path(job_root).absolute()
    return tuple(root / f"observed-{index:03}.log" for index in range(count))


def _screenshot_capture(window, image):
    return {"schemaVersion": 1, "kind": "task-7B-screenshot-capture",
            "stage": "NativeWindow", "sourceKind": "ComputerUseDataUrl",
            "screenshotId": window["screenshotId"], "capturePath": image["path"],
            "byteCount": image["size"], "sha256": image["sha256"]}


def _validate_phase_bundle(
    run_root: Path,
    layout: str,
    phase: str,
    preparation_id: str,
    observation: object,
    final: object,
    effect_value: object,
    outcome_data: bytes,
) -> dict[str, object]:
    root = Path(run_root).absolute()
    if (
        layout not in LAYOUTS
        or phase not in PHASES
        or _RUN.fullmatch(root.name) is None
        or not isinstance(observation, Mapping)
        or type(observation.get("pid")) is not int
        or observation["pid"] <= 0
    ):
        raise GateError("phase bundle run/layout/phase observation binding is invalid")
    observation_id = "observation-sha256:" + _sha256(_canonical(observation))
    final_fields = {
        "schemaVersion", "kind", "runId", "layout", "phase",
        "preparationId", "observationId", "watchRequestId",
        "watchOutcomeId", "effectId", "operatorEvidenceId", "beginId",
        "processId", "runtimeDeltaId", "watchEvidence", "protectedBefore",
        "protectedAfter", "windowObservedAt", "closeObservedAt",
        "completedAt", "authority", "phaseFinalId",
    }
    final_body = {
        key: item for key, item in final.items() if key != "phaseFinalId"
    } if isinstance(final, Mapping) else {}
    if (
        not isinstance(final, Mapping)
        or set(final) != final_fields
        or final.get("phaseFinalId") != "phase-final-sha256:" + _sha256(_canonical(final_body))
        or final.get("schemaVersion") != SCHEMA_VERSION
        or final.get("kind") != "task-7B-phase-final"
        or final.get("authority") is not False
        or final.get("runId") != root.name
        or final.get("layout") != layout
        or final.get("phase") != phase
        or final.get("preparationId") != preparation_id
        or final.get("observationId") != observation_id
        or not isinstance(final.get("operatorEvidenceId"), str)
        or not str(final.get("operatorEvidenceId")).startswith("operator-evidence-sha256:")
        or not isinstance(final.get("beginId"), str)
        or not str(final.get("beginId")).startswith("phase-begin-sha256:")
        or not isinstance(final.get("processId"), str)
        or not str(final.get("processId")).startswith("phase-process-sha256:")
        or not isinstance(final.get("runtimeDeltaId"), str)
        or not str(final.get("runtimeDeltaId")).startswith("runtime-delta-sha256:")
    ):
        raise GateError("phase final record binding differs")
    effect = validate_effect_record(effect_value)
    if (
        final.get("effectId") != effect["effectId"]
        or effect.get("scope") != f"{layout}:{phase}"
    ):
        raise GateError("phase final effect binding differs")
    outcome = watch_outcome_from_bytes(outcome_data)
    if outcome_data != watch_outcome_to_bytes(outcome):
        raise GateError("watch outcome bytes are not canonical")
    watch_id = watch_outcome_id_for(outcome)
    if (
        final.get("watchOutcomeId") != watch_id
        or final.get("watchRequestId") != outcome.request_id
        or outcome.run_id != "containment-run:" + root.name
        or outcome.scenario is not ContainmentScenario.NEW_FOLDER
        or outcome.opened_root_kinds != ROOT_KINDS
        or effect.get("watcherPid") != outcome.worker_pid
        or effect.get("mo2Pid") != observation.get("pid")
        or not isinstance(outcome.session_id, str)
        or not outcome.session_id
    ):
        raise GateError("phase final watch outcome binding differs")
    return {
        "observationId": observation_id,
        "watchOutcomeId": watch_id,
        "effectId": effect["effectId"],
        "watcherPid": effect["watcherPid"],
        "mo2Pid": effect["mo2Pid"],
        "requestId": final["watchRequestId"],
        "sessionId": outcome.session_id,
    }


def _protected_state(value: object, label: str) -> ProtectedState:
    fields = {
        "source_mods", "lab_profile_sha256", "play_profile_sha256",
        "downloads", "overwrite", "bounded_game",
    }
    record = _exact_object(value, fields, label)

    def tree(item: object, item_label: str):
        from modlab.validation.mo2_containment_model import TreeIdentity

        row = _exact_object(
            item,
            {"sha256", "regular_file_count", "directory_count", "total_size"},
            item_label,
        )
        if (
            type(row["sha256"]) is not str
            or _HEX64.fullmatch(row["sha256"]) is None
            or any(type(row[name]) is not int or row[name] < 0 for name in (
                "regular_file_count", "directory_count", "total_size",
            ))
        ):
            raise GateError(f"{item_label} is malformed")
        return TreeIdentity(
            str(row["sha256"]),
            int(row["regular_file_count"]),
            int(row["directory_count"]),
            int(row["total_size"]),
        )

    for name in ("lab_profile_sha256", "play_profile_sha256"):
        if type(record[name]) is not str or _HEX64.fullmatch(record[name]) is None:
            raise GateError(f"{label}.{name} is malformed")
    return ProtectedState(
        tree(record["source_mods"], f"{label}.source_mods"),
        str(record["lab_profile_sha256"]),
        str(record["play_profile_sha256"]),
        tree(record["downloads"], f"{label}.downloads"),
        tree(record["overwrite"], f"{label}.overwrite"),
        tree(record["bounded_game"], f"{label}.bounded_game"),
    )


def _record_id(value: object, field: str, prefix: str, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise GateError(f"{label} is not an object")
    record = dict(value)
    identifier = record.pop(field, None)
    if identifier != prefix + _sha256(_canonical(record)):
        raise GateError(f"{label} content identity differs")
    return dict(value)


def _validate_phase_evidence_chain(
    root: Path,
    layout: str,
    phase: str,
    preparation: Mapping[str, object],
    observation: Mapping[str, object],
    final: Mapping[str, object],
    effect: Mapping[str, object],
    begin: object,
    process: object,
    operator: object,
    runtime_delta: object,
    raw: Mapping[str, bytes],
) -> None:
    """Reconstruct every immutable watcher/phase record after restart."""
    job = authority_run_root(root) / layout / "jobs" / phase
    watch = job / "watch"
    expected_raw = {
        "requestSha256": REQUEST_NAME,
        "claimSha256": CLAIM_NAME,
        "launchSha256": LAUNCH_NAME,
        "readySha256": READY_NAME,
        "eventsSha256": EVENTS_NAME,
        "terminalSha256": TERMINAL_NAME,
        "outcomeSha256": OUTCOME_NAME,
        "stopSha256": STOP_NAME,
        "admissionSha256": CAUSAL_NAMES["LaunchAdmission"],
        "quiescenceSha256": CAUSAL_NAMES["ProcessTreeQuiescence"],
        "workerExitSha256": CAUSAL_NAMES["WorkerExitObservation"],
    }
    hashes = {field: _sha256(raw[filename]) for field, filename in expected_raw.items()}
    if final.get("watchEvidence") != hashes:
        raise GateError("phase final does not bind every exact watcher evidence file")
    if os.path.lexists(watch / CONTROLLER_LOSS_NAME):
        raise GateError("completed phase contains a controller-loss record")

    try:
        request = watch_request_from_bytes(raw[REQUEST_NAME])
        if raw[REQUEST_NAME] != watch_request_to_bytes(request):
            raise GateError("watch request bytes are not canonical")
        if (
            request.request_id != final.get("watchRequestId")
            or request.run_id != "containment-run:" + root.name
            or request.scenario is not ContainmentScenario.NEW_FOLDER
            or request.evidence_root != watch.absolute()
            or request.stop_token_path != (watch / STOP_NAME).absolute()
            or tuple(item.root_kind for item in request.roots) != ROOT_KINDS
        ):
            raise GateError("watch request phase/root binding differs")
        derived = _derived_watch_roots(preparation, layout)
        if tuple(derived) != ROOT_KINDS:
            raise GateError("derived watch root order differs")
        if tuple((item.root_kind, item.path) for item in request.roots) != tuple((kind, derived[kind].absolute()) for kind in ROOT_KINDS):
            raise GateError("watch request root path differs from protected preparation")
        if request.authority_root != authority_run_root(root):
            raise GateError("watch request authority binding differs")

        claim = controller_claim_from_bytes(raw[CLAIM_NAME], request)
        if raw[CLAIM_NAME] != controller_claim_to_bytes(claim, request):
            raise GateError("controller claim bytes are not canonical")
        launch = worker_launch_from_bytes(raw[LAUNCH_NAME], request)
        if raw[LAUNCH_NAME] != worker_launch_to_bytes(launch, request):
            raise GateError("worker launch bytes are not canonical")
        outcome = watch_outcome_from_bytes(raw[OUTCOME_NAME])
        receipt = windows_watch.watch_receipt_from_files(request, launch.worker_pid,
            watch / READY_NAME, watch / EVENTS_NAME, watch / TERMINAL_NAME)
        _require_cleanup_receipt(receipt, request, launch.worker_pid)
        if receipt.watch_outcome_id != watch_outcome_id_for(outcome):
            raise GateError("watch outcome differs from shared causal reconstruction")
    except GateError:
        raise
    except Exception as error:
        raise GateError(f"complete watcher evidence is invalid: {error}") from error

    begin_record = _record_id(begin, "beginId", "phase-begin-sha256:", "phase begin")
    begin_fields = {
        "schemaVersion", "kind", "runId", "layout", "phase", "nonce",
        "preparationId", "startedAt", "pluginsBefore", "appBefore",
        "runtimeBefore", "writableBefore", "protectedBefore", "requestId", "sessionId",
        "authority", "beginId",
    }
    if (
        set(begin_record) != begin_fields
        or begin_record.get("schemaVersion") != SCHEMA_VERSION
        or begin_record.get("kind") != "task-7B-phase-begin"
        or begin_record.get("runId") != root.name
        or begin_record.get("layout") != layout
        or begin_record.get("phase") != phase
        or begin_record.get("preparationId") != preparation.get("preparationId")
        or begin_record.get("requestId") != request.request_id
        or begin_record.get("sessionId") != request.session_id
        or begin_record.get("authority") is not False
        or type(begin_record.get("nonce")) is not str
        or re.fullmatch(r"[0-9a-f]{32}", str(begin_record.get("nonce"))) is None
        or type(begin_record.get("startedAt")) is not str
        or not begin_record.get("startedAt")
        or begin_record.get("pluginsBefore") != observation.get("before")
        or begin_record.get("runtimeBefore") != observation.get("runtimeBefore")
        or not isinstance(begin_record.get("writableBefore"), Mapping)
        or final.get("beginId") != begin_record.get("beginId")
    ):
        raise GateError("phase begin binding differs")

    process_record = _record_id(process, "processId", "phase-process-sha256:", "phase process")
    process_fields = {
        "pid", "creationTime", "executable", "integrity", "arguments",
        "workingDirectory", "processId",
    }
    layout_record = _layout_record(preparation, layout)
    expected_executable = str(Path(str(layout_record["appRoot"])) / "ModOrganizer.exe")
    if (
        set(process_record) != process_fields
        or final.get("processId") != process_record.get("processId")
    ):
        raise GateError("phase process content identity differs")
    if (
        type(process_record.get("pid")) is not int
        or process_record["pid"] <= 0
        or type(process_record.get("creationTime")) is not int
        or process_record["creationTime"] <= 0
        or process_record.get("executable") != expected_executable
        or process_record.get("integrity") != IntegrityLevel.LOW.name
        or process_record.get("arguments") != ["--profile", "ModLab - Lab"]
        or process_record.get("workingDirectory") != str(layout_record["appRoot"])
        or observation.get("pid") != process_record["pid"]
        or observation.get("launchProcess") != {
            "pid": process_record["pid"],
            "creationTime": process_record["creationTime"],
            "executable": process_record["executable"],
        }
    ):
        raise GateError("phase process/observation binding differs")

    admission = windows_watch.causal_record_from_bytes(raw[CAUSAL_NAMES["LaunchAdmission"]], request, claim, launch)
    if (admission["processPid"], admission["processCreationTime"]) != (process_record["pid"], process_record["creationTime"]):
        raise GateError("phase process differs from original watch admission")

    operator_record = _record_id(
        operator,
        "operatorEvidenceId",
        "operator-evidence-sha256:",
        "operator evidence",
    )
    operator_fields = {
        "schemaVersion", "kind", "runId", "layout", "phase", "process",
        "window", "tool", "close", "windowObservedAt", "closeObservedAt",
        "computerControlUsed", "authority", "operatorEvidenceId",
    }
    if (
        set(operator_record) != operator_fields
        or operator_record.get("schemaVersion") != SCHEMA_VERSION
        or operator_record.get("kind") != "task-7B-operator-evidence"
        or operator_record.get("runId") != root.name
        or operator_record.get("layout") != layout
        or operator_record.get("phase") != phase
        or operator_record.get("process") != observation.get("launchProcess")
        or operator_record.get("computerControlUsed") is not True
        or operator_record.get("authority") is not False
        or operator_record.get("operatorEvidenceId") != final.get("operatorEvidenceId")
        or operator_record.get("windowObservedAt") != final.get("windowObservedAt")
        or operator_record.get("closeObservedAt") != final.get("closeObservedAt")
    ):
        raise GateError("operator evidence phase/process binding differs")
    checked_delta = _validate_runtime_delta_record(
        root,
        layout,
        phase,
        begin_record,
        runtime_delta,
    )
    if checked_delta.get("runtimeDeltaId") != final.get("runtimeDeltaId"):
        raise GateError("phase final runtime-delta binding differs")
    window = operator_record.get("window")
    ui = observation.get("ui")
    if not isinstance(window, Mapping) or not isinstance(ui, Mapping):
        raise GateError("operator/UI evidence is malformed")
    image = window.get("image")
    close = operator_record.get("close")
    if (
        not isinstance(image, Mapping)
        or not isinstance(close, Mapping)
        or image.get("path") != str(job / "window.png")
        or ui.get("windowId") != window.get("windowId")
        or ui.get("app") != window.get("app")
        or ui.get("title") != window.get("title")
        or ui.get("screenshotIds") != [window.get("screenshotId")]
        or ui.get("loadedTool") != (
            None if phase == "Control" else "ModLab Capability Probe"
        )
        or ui.get("closeAction") != close.get("action")
    ):
        raise GateError("operator evidence does not match the phase UI observation")
    validate_operator_evidence(
        phase,
        operator_record["process"],
        operator_record["window"],
        operator_record["tool"],
        operator_record["close"],
        run_root=root,
    )

    if _load_gate_json(root, job / "window.png.capture.json") != _screenshot_capture(window, image):
        raise GateError("screenshot original capture provenance differs")
    for reference in observation.get("logs", ()):
        target = Path(reference["path"])
        capture = _load_gate_json(root, target.with_name(target.name + ".capture.json"))
        if (set(capture) != {"schemaVersion", "sourcePath", "capturePath", "volumeSerial", "fileId", "byteCount", "sha256", "stage"}
                or capture["schemaVersion"] != 1 or capture["capturePath"] != str(target)
                or capture["byteCount"] != reference["size"] or capture["sha256"] != reference["sha256"]
                or capture["stage"] != f"{layout}:{phase}:Log"
                or type(capture["volumeSerial"]) is not int or type(capture["fileId"]) is not int
                or not any(_inside(Path(capture["sourcePath"]), Path(str(layout_record[name])) / "logs")
                           for name in ("appRoot", "managerRoot"))):
            raise GateError("retained log capture provenance differs")

    before = _protected_state(final.get("protectedBefore"), "protectedBefore")
    after = _protected_state(final.get("protectedAfter"), "protectedAfter")
    if begin_record.get("protectedBefore") != final.get("protectedBefore"):
        raise GateError("phase begin/final protected-before binding differs")
    if not watch_proves_unchanged(receipt, before, after):
        raise GateError("durable watcher/protected-state evidence does not prove unchanged")
    required_writes = {
        root / layout,
        job / "begin.json",
        job / "process.json",
        job / "window.png",
        job / "window.png.capture.json",
        job / "operator-evidence.json",
        job / "runtime-delta.json",
        job / "observation.json",
        job / "effect.json",
        job / "phase-final.json",
        *(watch / name for name in (
            REQUEST_NAME,
            CLAIM_NAME,
            LAUNCH_NAME,
            READY_NAME,
            EVENTS_NAME,
            TERMINAL_NAME,
            OUTCOME_NAME,
            STOP_NAME,
            CAUSAL_NAMES["LaunchAdmission"],
            CAUSAL_NAMES["ProcessTreeQuiescence"],
            CAUSAL_NAMES["WorkerExitObservation"],
        )),
    }
    for reference in observation.get("logs", ()):
        if not isinstance(reference, Mapping) or type(reference.get("path")) is not str:
            raise GateError("phase log evidence reference is malformed")
        required_writes.add(Path(reference["path"]).absolute())
        required_writes.add(Path(reference["path"] + ".capture.json").absolute())
    if observation.get("guarded") is not None:
        required_writes.add(job / "guarded.json")
    written = {Path(path).absolute() for path in effect.get("writtenPaths", ())}
    layout_root = root / layout
    if (
        not required_writes.issubset(written)
        or any(not (_inside(path, layout_root) or _inside(path, authority_run_root(root) / layout)) for path in written)
        or effect.get("childMutationRoots") != [str(layout_root)]
        or effect.get("sourceChanges") != []
        or effect.get("gameChanges") != []
        or effect.get("productionMo2Changes") != []
    ):
        raise GateError("phase service effect does not contain every confined publication")
    if (
        effect.get("watcherPid") != launch.worker_pid
        or effect.get("mo2Pid") != process_record["pid"]
        or final.get("watchOutcomeId") != watch_outcome_id_for(outcome)
        or final.get("watchRequestId") != request.request_id
    ):
        raise GateError("phase watcher/process/effect binding differs")


def _load_validated_phase_bundle(
    run_root: Path,
    layout: str,
    phase: str,
    preparation_id: str,
) -> tuple[Mapping[str, object], Mapping[str, object]]:
    root = Path(run_root).absolute()
    job = authority_run_root(root) / layout / "jobs" / phase
    try:
        observation = _load_gate_json(root, job / "observation.json")
        final = _load_gate_json(root, job / "phase-final.json")
        effect = _load_gate_json(root, job / "effect.json")
        begin = _load_gate_json(root, job / "begin.json")
        process = _load_gate_json(root, job / "process.json")
        operator = _load_gate_json(root, job / "operator-evidence.json")
        runtime_delta = _load_gate_json(root, job / "runtime-delta.json")
        watch = job / "watch"
        raw = {
            name: _read_gate_evidence(root, watch / name)
            for name in (
                REQUEST_NAME,
                CLAIM_NAME,
                LAUNCH_NAME,
                READY_NAME,
                EVENTS_NAME,
                TERMINAL_NAME,
                OUTCOME_NAME,
                STOP_NAME,
                CAUSAL_NAMES["LaunchAdmission"],
                CAUSAL_NAMES["ProcessTreeQuiescence"],
                CAUSAL_NAMES["WorkerExitObservation"],
            )
        }
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise GateError(f"complete durable phase evidence is missing or unreadable: {error}") from error
    outcome_data = raw[OUTCOME_NAME]
    try:
        bundle = _validate_phase_bundle(
            root,
            layout,
            phase,
            preparation_id,
            observation,
            final,
            effect,
            outcome_data,
        )
    except GateError:
        raise
    except Exception as error:
        raise GateError(f"phase summary evidence is invalid: {error}") from error
    preparation = _load_preparation(root)
    if preparation.get("preparationId") != preparation_id:
        raise GateError("phase preparation identity differs on reload")
    _validate_phase_evidence_chain(
        root,
        layout,
        phase,
        preparation,
        observation,
        final,
        effect,
        begin,
        process,
        operator,
        runtime_delta,
        raw,
    )
    try:
        runtime_capability._observation(
            observation,
            {"layout": layout, "appRoot": str(root / layout / "app")},
            {"schemaVersion": 3, "root": str(root), "authorityRoot": str(authority_run_root(root)), "runId": root.name},
            PHASES.index(phase),
        )
        runtime_capability._verify_raw_evidence(
            {"schemaVersion": 3, "authorityRoot": str(authority_run_root(root)), "candidates": [{"control": observation, "observations": []}]}
        )
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise GateError(f"durable phase observation/raw evidence differs: {error}") from error
    return observation, bundle


def _require_cleanup_receipt(
    receipt: object,
    request: object,
    watcher_pid: int | None,
) -> WatchReceipt:
    if (
        not isinstance(receipt, WatchReceipt)
        or receipt.evidence_completion is not WatchEvidenceCompletion.COMPLETED
        or receipt.error is not None
        or receipt.ready is not True
        or receipt.opened_root_kinds != ROOT_KINDS
        or receipt.worker_exit_code != 0
        or type(receipt.watch_outcome_id) is not str
        or not receipt.watch_outcome_id.startswith("watch-outcome-sha256:")
        or _HEX64.fullmatch(receipt.watch_outcome_id.removeprefix("watch-outcome-sha256:")) is None
        or receipt.request_id != getattr(request, "request_id", None)
        or receipt.session_id != getattr(request, "session_id", None)
        or receipt.run_id != getattr(request, "run_id", None)
        or receipt.scenario is not getattr(request, "scenario", None)
        or (watcher_pid is not None and receipt.worker_pid != watcher_pid)
    ):
        detail = getattr(receipt, "error", None)
        raise GateError(
            "watch cleanup did not return one exact completed receipt"
            + (f": {detail}" if detail else "")
        )
    return receipt


def _load_bound_watch_identity(job: Path, run_root: Path) -> SimpleNamespace:
    watch = Path(job).absolute() / "watch"
    root = Path(run_root).absolute()
    try:
        request_bytes = _read_gate_evidence(root, watch / REQUEST_NAME)
        request = watch_request_from_bytes(request_bytes)
        if request_bytes != watch_request_to_bytes(request):
            raise GateError("failed phase watch request bytes are not canonical")
        if (
            request.evidence_root != watch
            or request.stop_token_path != watch / STOP_NAME
            or request.run_id != "containment-run:" + root.name
            or request.scenario is not ContainmentScenario.NEW_FOLDER
            or tuple(item.root_kind for item in request.roots) != ROOT_KINDS
        ):
            raise GateError("failed phase watch request binding differs")
        claim_bytes = _read_gate_evidence(root, watch / CLAIM_NAME)
        claim = controller_claim_from_bytes(claim_bytes, request)
        if claim_bytes != controller_claim_to_bytes(claim, request):
            raise GateError("failed phase controller claim bytes are not canonical")
        launch_bytes = _read_gate_evidence(root, watch / LAUNCH_NAME)
        launch = worker_launch_from_bytes(launch_bytes, request)
        if launch_bytes != worker_launch_to_bytes(launch, request):
            raise GateError("failed phase worker launch bytes are not canonical")
    except GateError:
        raise
    except Exception as error:
        raise GateError(f"failed phase watcher identity cannot be reconstructed: {error}") from error
    return SimpleNamespace(
        request=request,
        claim=claim,
        launch=launch,
        request_sha256=watch_request_sha256(request),
        claim_sha256=_sha256(claim_bytes),
        launch_sha256=_sha256(launch_bytes),
    )


def _require_exact_process_dead(pid: object, creation_time: object, label: str, root: Path) -> None:
    if type(pid) is not int or pid <= 0 or type(creation_time) is not int or creation_time <= 0:
        raise GateError(f"{label} exact process identity is unavailable")
    status, handle, detail = windows_watch._exact_process_status(pid, creation_time)
    close_error = None
    if handle:
        close_error = windows_watch._close_controller_handle(handle, label, Path(root).absolute())
    if close_error is not None:
        raise GateError(close_error)
    if status == "live":
        raise GateError(f"{label} is still running")
    if status != "dead" or detail is not None:
        raise GateError(f"{label} absence is uncertain: {detail or status}")


def _load_phase_failure(run_root: Path, layout: str, phase: str) -> Mapping[str, object]:
    root = Path(run_root).absolute()
    job = authority_run_root(root) / layout / "jobs" / phase
    value = _record_id(
        _load_gate_json(root, job / "failure.json"),
        "failureId",
        "phase-failure-sha256:",
        "failed phase record",
    )
    fields = {
        "schemaVersion", "kind", "runId", "layout", "phase", "terminal",
        "retryPermitted", "processMayStillBeLive", "watchCleanupPending",
        "cleanupErrors", "cleanup", "errorType", "error", "effects",
        "authority", "failureId",
    }
    cleanup_fields = {
        "watcherCreated", "watcherPid", "requestId", "sessionId", "evidenceRoot",
        "watchIdentityAvailable", "requestSha256", "controllerPid",
        "controllerCreationTime", "controllerClaimSha256", "watcherCreationTime",
        "workerLaunchSha256",
        "mo2Created", "mo2Pid", "mo2CreationTime", "mo2CreationTimeAvailable",
        "executable", "processHandleAcquired", "processHandleCleanupPending",
    }
    cleanup = value.get("cleanup")
    if (
        set(value) != fields
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != "task-7B-phase-failed-attempt"
        or value.get("runId") != root.name
        or value.get("layout") != layout
        or value.get("phase") != phase
        or value.get("terminal") is not True
        or value.get("retryPermitted") is not False
        or value.get("authority") is not False
        or type(value.get("processMayStillBeLive")) is not bool
        or type(value.get("watchCleanupPending")) is not bool
        or type(value.get("cleanupErrors")) is not list
        or any(type(item) is not str or not item for item in value["cleanupErrors"])
        or not isinstance(cleanup, Mapping)
        or set(cleanup) != cleanup_fields
    ):
        raise GateError("failed phase record binding differs")
    effect = validate_effect_record(value["effects"])
    if (
        effect.get("scope") != f"{layout}:{phase}:failed"
        or str(job / "failure.json") not in effect.get("writtenPaths", ())
    ):
        raise GateError("failed phase effect binding differs")
    watcher_created = cleanup.get("watcherCreated")
    mo2_created = cleanup.get("mo2Created")
    if type(watcher_created) is not bool or type(mo2_created) is not bool:
        raise GateError("failed phase cleanup creation flags are malformed")
    if watcher_created and (
        type(cleanup.get("watcherPid")) is not int
        or cleanup["watcherPid"] <= 0
        or cleanup.get("evidenceRoot") != str(job / "watch")
        or type(cleanup.get("requestId")) is not str
        or type(cleanup.get("sessionId")) is not str
    ):
        raise GateError("failed phase watcher binding differs")
    identity_available = cleanup.get("watchIdentityAvailable")
    if type(identity_available) is not bool:
        raise GateError("failed phase watcher identity availability is malformed")
    if identity_available and not watcher_created:
        raise GateError("failed phase invents an identity for an uncreated watcher")
    identity_fields = (
        "requestSha256", "controllerPid", "controllerCreationTime",
        "controllerClaimSha256", "watcherCreationTime", "workerLaunchSha256",
    )
    if identity_available:
        identity = _load_bound_watch_identity(job, root)
        expected = {
            "requestId": identity.request.request_id,
            "sessionId": identity.request.session_id,
            "requestSha256": identity.request_sha256,
            "controllerPid": identity.claim.controller_pid,
            "controllerCreationTime": identity.claim.controller_creation_time,
            "controllerClaimSha256": identity.claim_sha256,
            "watcherPid": identity.launch.worker_pid,
            "watcherCreationTime": identity.launch.worker_creation_time,
            "workerLaunchSha256": identity.launch_sha256,
        }
        if any(cleanup.get(name) != expected_value for name, expected_value in expected.items()):
            raise GateError("failed phase exact watcher identity differs")
    elif any(cleanup.get(name) is not None for name in identity_fields):
        raise GateError("failed phase invents an unavailable watcher identity")
    if mo2_created and (type(cleanup.get("mo2Pid")) is not int or cleanup["mo2Pid"] <= 0):
        raise GateError("failed phase MO2 binding differs")
    available = cleanup.get("mo2CreationTimeAvailable")
    if type(available) is not bool or available != (
        type(cleanup.get("mo2CreationTime")) is int and cleanup["mo2CreationTime"] > 0
    ):
        raise GateError("failed phase MO2 creation-time binding differs")
    return value


def _load_cleanup_watch_evidence(
    job: Path,
    run_root: Path,
    prior: Mapping[str, object],
    outcome_id: str,
) -> SimpleNamespace:
    root = Path(run_root).absolute()
    watch = Path(job).absolute() / "watch"
    try:
        identity = _load_bound_watch_identity(job, run_root)
        request = identity.request
        if (
            prior.get("watchIdentityAvailable") is not True
            or request.request_id != prior["requestId"]
            or request.session_id != prior["sessionId"]
            or identity.request_sha256 != prior["requestSha256"]
            or identity.claim.controller_pid != prior["controllerPid"]
            or identity.claim.controller_creation_time != prior["controllerCreationTime"]
            or identity.claim_sha256 != prior["controllerClaimSha256"]
            or identity.launch.worker_pid != prior["watcherPid"]
            or identity.launch.worker_creation_time != prior["watcherCreationTime"]
            or identity.launch_sha256 != prior["workerLaunchSha256"]
        ):
            raise GateError("failed phase cleanup exact watcher identity differs")
        outcome_bytes = _read_gate_evidence(root, watch / OUTCOME_NAME)
        outcome = watch_outcome_from_bytes(outcome_bytes)
        if outcome_bytes != watch_outcome_to_bytes(outcome):
            raise GateError("failed phase cleanup watch outcome bytes are not canonical")
        receipt = windows_watch.watch_receipt_from_files(request, identity.launch.worker_pid,
            watch / READY_NAME, watch / EVENTS_NAME, watch / TERMINAL_NAME)
        _require_cleanup_receipt(receipt, request, identity.launch.worker_pid)
        if (
            watch_outcome_id_for(outcome) != outcome_id
            or outcome.request_sha256 != watch_request_sha256(request)
        ):
            raise GateError("failed phase cleanup watch outcome binding differs")
        if (
            receipt.watch_outcome_id != outcome_id
            or receipt.request_id != request.request_id
            or receipt.session_id != request.session_id
            or receipt.run_id != request.run_id
            or receipt.scenario is not request.scenario
            or receipt.worker_pid != identity.launch.worker_pid
            or receipt.opened_root_kinds != ROOT_KINDS
            or receipt.ready is not True
            or outcome.root_identities_unchanged is not True
            or outcome.events != ()
            or outcome.evidence_completion not in {
                WatchEvidenceCompletion.COMPLETED,
                WatchEvidenceCompletion.INCOMPLETE,
            }
            or (
                outcome.evidence_completion is WatchEvidenceCompletion.COMPLETED
                and (receipt.error is not None or receipt.worker_exit_code != 0 or outcome.reason_codes != ())
            )
            or (
                outcome.evidence_completion is WatchEvidenceCompletion.INCOMPLETE
                and (
                    outcome.reason_codes != ("controller-session-lost",)
                    or receipt.error != "controller-session-lost"
                )
            )
        ):
            raise GateError("failed phase cleanup watch receipt binding differs")
        return SimpleNamespace(identity=identity, outcome=outcome, receipt=receipt)
    except GateError:
        raise
    except Exception as error:
        raise GateError(f"failed phase cleanup watch outcome cannot be reconstructed: {error}") from error


def _load_phase_cleanup(run_root: Path, layout: str, phase: str) -> Mapping[str, object]:
    root = Path(run_root).absolute()
    job = authority_run_root(root) / layout / "jobs" / phase
    failure = _load_phase_failure(root, layout, phase)
    value = _record_id(
        _load_gate_json(root, job / "cleanup.json"),
        "cleanupId",
        "phase-cleanup-sha256:",
        "failed phase cleanup record",
    )
    fields = {
        "schemaVersion", "kind", "runId", "layout", "phase", "failureId",
        "requestId", "sessionId", "watcherPid", "watchOutcomeId", "mo2Pid",
        "requestSha256", "controllerPid", "controllerCreationTime",
        "controllerClaimSha256", "watcherCreationTime", "workerLaunchSha256",
        "watchEvidenceCompletion", "watchReasonCodes", "watchRootIdentitiesUnchanged",
        "mo2CreationTime", "mo2Absent", "watcherQuiescent", "terminal",
        "retryPermitted", "freshAttemptRequired", "cleanedAt", "effect",
        "authority", "cleanupId",
    }
    if (
        set(value) != fields
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != "task-7B-failed-phase-cleanup"
        or value.get("runId") != root.name
        or value.get("layout") != layout
        or value.get("phase") != phase
        or value.get("failureId") != failure["failureId"]
        or value.get("mo2Absent") is not True
        or value.get("watcherQuiescent") is not True
        or value.get("terminal") is not True
        or value.get("retryPermitted") is not False
        or value.get("freshAttemptRequired") is not True
        or value.get("authority") is not False
    ):
        raise GateError("failed phase cleanup record binding differs")
    prior = failure["cleanup"]
    if prior["mo2Created"] and not prior["watcherCreated"]:
        raise GateError("cleanup lacks original process admission and closed-tree evidence")
    for current, expected in (
        ("requestId", prior["requestId"]),
        ("sessionId", prior["sessionId"]),
        ("watcherPid", prior["watcherPid"]),
        ("requestSha256", prior["requestSha256"]),
        ("controllerPid", prior["controllerPid"]),
        ("controllerCreationTime", prior["controllerCreationTime"]),
        ("controllerClaimSha256", prior["controllerClaimSha256"]),
        ("watcherCreationTime", prior["watcherCreationTime"]),
        ("workerLaunchSha256", prior["workerLaunchSha256"]),
        ("mo2Pid", prior["mo2Pid"]),
        ("mo2CreationTime", prior["mo2CreationTime"]),
    ):
        if value.get(current) != expected:
            raise GateError(f"failed phase cleanup {current} differs")
    if prior["watcherCreated"] and (
        type(value.get("watchOutcomeId")) is not str
        or not value["watchOutcomeId"].startswith("watch-outcome-sha256:")
        or _HEX64.fullmatch(value["watchOutcomeId"].removeprefix("watch-outcome-sha256:")) is None
    ):
        raise GateError("failed phase cleanup watcher outcome is malformed")
    if prior["watcherCreated"]:
        evidence = _load_cleanup_watch_evidence(job, root, prior, value["watchOutcomeId"])
        if (
            value.get("watchEvidenceCompletion") != evidence.outcome.evidence_completion.value
            or value.get("watchReasonCodes") != list(evidence.outcome.reason_codes)
            or value.get("watchRootIdentitiesUnchanged") is not evidence.outcome.root_identities_unchanged
        ):
            raise GateError("failed phase cleanup outcome summary differs")
    if not prior["watcherCreated"] and (
        value.get("watchOutcomeId") is not None
        or value.get("watchEvidenceCompletion") is not None
        or value.get("watchReasonCodes") != []
        or value.get("watchRootIdentitiesUnchanged") is not None
    ):
        raise GateError("failed phase cleanup invents watcher evidence")
    effect = validate_effect_record(value["effect"])
    if (
        effect.get("scope") != f"{layout}:{phase}:cleanup"
        or str(job / "cleanup.json") not in effect.get("writtenPaths", ())
        or effect.get("sourceChanges") != []
        or effect.get("gameChanges") != []
        or effect.get("productionMo2Changes") != []
    ):
        raise GateError("failed phase cleanup effect binding differs")
    return value


class ProductionLiveBackend:
    """Real phase backend retained by one long-lived controller process."""

    def __init__(self) -> None:
        self._pending_session: LivePhaseSession | PendingPhaseCleanup | None = None

    def source_check(self, run_root: Path) -> None:
        _fresh_preparation_preflight(run_root)
        _require_process_absence()

    def load_state(self, run_root: Path, layout: str) -> DurableLayoutState:
        preparation = _fresh_preparation_preflight(run_root)
        layout_root = authority_run_root(run_root) / layout
        terminal = list(layout_root.glob("jobs/*/failure.json"))
        installation_failure = authority_run_root(run_root) / layout / "installation-failure.json"
        incomplete = [
            path for path in layout_root.glob("jobs/*")
            if path.is_dir() and not (path / "phase-final.json").is_file()
        ]
        if terminal or incomplete or installation_failure.exists():
            raise GateError("run contains a terminal or incomplete phase and cannot resume")
        completed = tuple(phase for phase in PHASES if (layout_root / "jobs" / phase / "phase-final.json").is_file())
        if completed != PHASES[: len(completed)]:
            raise GateError("durable phase records are not one exact prefix")
        deltas: dict[str, Mapping[str, object]] = {}
        for phase in completed:
            _load_validated_phase_bundle(
                Path(run_root).absolute(),
                layout,
                phase,
                str(preparation["preparationId"]),
            )
            deltas[phase] = _load_gate_json(run_root, layout_root / "jobs" / phase / "runtime-delta.json")
        installed = (authority_run_root(run_root) / layout / "installation.json").is_file()
        installation = None
        if installed:
            installation = _load_installation(run_root, layout)
        if installed and completed[:1] != ("Control",):
            raise GateError("candidate installation is not bound to completed Control")
        if len(completed) > 1 and not installed:
            raise GateError("candidate phases exist without the installation transition")
        if completed:
            latest = layout_root / "jobs" / completed[-1]
            final = _load_gate_json(run_root, latest / "phase-final.json")
            if _capture_protected(preparation, layout) != _protected_state(final["protectedAfter"], "protectedAfter"):
                raise GateError("current protected state differs from the completed phase")
            request = _load_bound_watch_identity(latest, run_root).request
            roots = _derived_watch_roots(preparation, layout)
            if request.roots != tuple(windows_watch.watch_root(kind, roots[kind]) for kind in ROOT_KINDS):
                raise GateError("current watch root path or identity differs from the completed phase")
        preparation_layout = _layout_record(preparation, layout)
        _validate_writable_chain(
            Path(run_root).absolute(),
            layout,
            preparation_layout["writableBaseline"],
            completed,
            installation,
            deltas,
            _snapshot_runtime_writable(run_root, layout),
        )
        return DurableLayoutState(completed, installed)

    def install_candidate(self, run_root: Path, layout: str, control_id: str) -> ContainmentServiceResult:
        return containment_service._receipted(self._install_candidate)(run_root, layout, control_id)

    def _install_candidate(self, run_root: Path, layout: str, control_id: str) -> Mapping[str, object]:
        self.source_check(run_root)
        state = self.load_state(run_root, layout)
        if state != DurableLayoutState(("Control",), False):
            raise GateError("candidate installation requires exactly one completed Control")
        root = Path(run_root).absolute()
        layout_root = root / layout
        control_final = _load_gate_json(root, authority_run_root(root) / layout / "jobs" / "Control" / "phase-final.json")
        if control_final.get("observationId") != control_id:
            raise GateError("candidate installation control identity differs")
        control = _load_gate_json(root, authority_run_root(root) / layout / "jobs" / "Control" / "observation.json")
        if "observation-sha256:" + _sha256(_canonical(control)) != control_id:
            raise GateError("candidate installation control bytes differ")
        preparation = _load_preparation(root)
        record = _layout_record(preparation, layout)
        app = Path(str(record["appRoot"]))
        plugins = app / "plugins"
        if snapshot_tree(plugins) != control.get("after"):
            raise GateError("current plugin tree differs from the frozen Control baseline")
        if _runtime_inventory(preparation, layout) != control.get("runtimeAfter"):
            raise GateError("current runtime differs from the frozen Control baseline")
        if own_inventory(control.get("after")):
            raise GateError("Control baseline unexpectedly contains candidate/cache entries")
        writable_before = _snapshot_runtime_writable(root, layout)
        containment_service._current_effects().child_mutation_root(layout_root)
        generated = candidate_sources(root, layout)
        namespace = plugins / runtime_capability.NAMESPACE
        if layout == "Package":
            namespace.mkdir(exist_ok=False)
            containment_service._current_effects().write(namespace)
            _set_low_integrity_receipted(namespace)
        for relative, data in generated.items():
            target = plugins.joinpath(*relative.split("/"))
            containment_service._current_effects().write(target)
            windows_exact_fs.publish_new_pinned(
                target,
                data,
                lambda actual, expected=data: actual if actual == expected else (_ for _ in ()).throw(GateError("candidate bytes differ")),
            )
        _set_low_integrity_receipted(plugins)
        if inspect_path_integrity(plugins) is not IntegrityLevel.LOW:
            raise GateError("candidate plugin root is not Low integrity")
        after = snapshot_tree(plugins)
        declared = own_inventory(after)
        names = ([runtime_capability.NAMESPACE] if layout == "Package" else []) + list(generated)
        if [item["path"] for item in declared] != names:
            raise GateError("installed candidate inventory differs")
        for item in declared:
            if item["kind"] == "file":
                data = generated[item["path"]]
                if item["sha256"] != _sha256(data) or item["size"] != len(data):
                    raise GateError("installed candidate content differs")
        expected = list(control["after"]) + declared
        if sorted(after, key=lambda item: item["path"]) != sorted(expected, key=lambda item: item["path"]):
            raise GateError("candidate installation changed an undeclared plugin entry")
        writable_after = _snapshot_runtime_writable(root, layout)
        _validate_installation_writable_transition(
            root,
            layout,
            declared,
            writable_before,
            writable_after,
        )
        return {
            "layout": layout,
            "controlId": control_id,
            "declared": declared,
            "before": control["after"],
            "after": after,
            "writableBefore": writable_before,
            "writableAfter": writable_after,
            "observedAt": _now(),
        }

    def publish_install(
        self,
        run_root: Path,
        layout: str,
        draft: Mapping[str, object],
        effects: ContainmentEffects,
    ) -> ContainmentServiceResult:
        return containment_service._receipted(self._publish_install)(
            run_root,
            layout,
            draft,
            effects,
        )

    def _publish_install(
        self,
        run_root: Path,
        layout: str,
        draft: Mapping[str, object],
        effects: ContainmentEffects,
    ) -> Mapping[str, object]:
        root = Path(run_root).absolute()
        target = authority_run_root(root) / layout / "installation.json"
        containment_service._current_effects().write(target)
        combined = ContainmentEffects.merged(
            effects,
            containment_service._current_effects().freeze(),
        )
        effect = build_effect_record(f"{layout}:Install", combined)
        record = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "task-7B-candidate-installation",
            "runId": root.name,
            **_json_value(draft),
            "effect": effect,
            "authority": False,
        }
        record["installationId"] = "installation-sha256:" + _sha256(_canonical(record))
        _publish_gate_json(root, target, record)
        exact = _load_installation(root, layout)
        if exact != record:
            raise GateError("candidate installation exact reload differs")
        if self.load_state(root, layout) != DurableLayoutState(("Control",), True):
            raise GateError("candidate installation did not become the exact durable writable-state head")
        return exact

    def fail_install(
        self,
        run_root: Path,
        layout: str,
        control_id: str,
        error: BaseException,
        effects: ContainmentEffects,
    ) -> ContainmentEffects:
        root = Path(run_root).absolute()
        target = authority_run_root(root) / layout / "installation-failure.json"
        if not target.parent.is_dir() or target.exists():
            return effects
        failure_effects = ContainmentEffects.merged(
            effects,
            ContainmentEffects(written_paths=(target,)),
        )
        try:
            _publish_gate_json(
                root, target,
                {
                    "schemaVersion": SCHEMA_VERSION,
                    "kind": "task-7B-candidate-installation-failed-attempt",
                    "runId": root.name,
                    "layout": layout,
                    "controlId": control_id,
                    "terminal": True,
                    "retryPermitted": False,
                    "errorType": type(error).__name__,
                    "error": str(error) or type(error).__name__,
                    "effects": build_effect_record(f"{layout}:Install:failed", failure_effects),
                    "authority": False,
                },
            )
        except BaseException as publication_error:
            partial = getattr(publication_error, "effects", None)
            publication_error.effects = (
                ContainmentEffects.merged(failure_effects, partial)
                if isinstance(partial, ContainmentEffects) else failure_effects
            )
            raise
        return failure_effects

    def begin_phase(self, run_root: Path, layout: str, phase: str) -> ContainmentServiceResult:
        return containment_service._receipted(self._begin_phase)(run_root, layout, phase)

    def _begin_phase(self, run_root: Path, layout: str, phase: str) -> LivePhaseSession:
        with open_vault(authority_run_root(run_root)):
            return self._begin_phase_in_vault(run_root, layout, phase)

    def _begin_phase_in_vault(self, run_root: Path, layout: str, phase: str) -> LivePhaseSession:
        self.source_check(run_root)
        preparation = _load_preparation(run_root)
        durable = self.load_state(run_root, layout)
        if next_action(durable.completed, installed=durable.installed) != phase:
            raise GateError("phase begin does not match the exact durable writable-state head")
        record = _layout_record(preparation, layout)
        root = Path(run_root).absolute()
        layout_root = root / layout
        app = Path(str(record["appRoot"]))
        manager = Path(str(record["managerRoot"]))
        job = authority_run_root(root) / layout / "jobs" / phase
        child_job = layout_root / "jobs" / phase
        if job.exists():
            raise GateError("phase job path already exists")
        containment_service._current_effects().child_mutation_root(layout_root)
        job.mkdir(parents=True, exist_ok=False)
        child_job.mkdir(parents=True, exist_ok=False)
        _set_low_integrity_receipted(child_job)
        if inspect_path_integrity(child_job) is not IntegrityLevel.LOW:
            raise GateError("phase job root is not Low integrity")
        plugins_before = snapshot_tree(app / "plugins")
        app_before = snapshot_tree(app)
        runtime_before = _runtime_inventory(preparation, layout)
        protected_before = _capture_protected(preparation, layout)
        writable_before = _snapshot_runtime_writable(root, layout)
        if phase == "Control":
            if own_inventory(plugins_before):
                raise GateError("Control candidate/cache namespace is not absent")
        else:
            installation = _load_installation(root, layout)
            declared = installation.get("declared") if isinstance(installation, Mapping) else None
            if type(declared) is not list or any(
                {item["path"]: item for item in plugins_before}.get(item.get("path")) != item
                for item in declared if isinstance(item, Mapping)
            ):
                raise GateError("installed candidate differs before launch")
        nonce = secrets.token_hex(16)
        watch_root = job / "watch"
        watch_root.mkdir()
        request = build_watch_request(
            root,
            layout,
            phase,
            _derived_watch_roots(preparation, layout),
            watch_root,
            request_token=secrets.token_hex(32),
            session_token=secrets.token_hex(32),
        )
        begin = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "task-7B-phase-begin",
            "runId": root.name,
            "layout": layout,
            "phase": phase,
            "nonce": nonce,
            "preparationId": preparation["preparationId"],
            "startedAt": _now(),
            "pluginsBefore": plugins_before,
            "appBefore": app_before,
            "runtimeBefore": runtime_before,
            "writableBefore": writable_before,
            "protectedBefore": _json_value(protected_before),
            "requestId": request.request_id,
            "sessionId": request.session_id,
            "authority": False,
        }
        begin["beginId"] = "phase-begin-sha256:" + _sha256(_canonical(begin))
        containment_service._current_effects().write(job / "begin.json")
        _publish_gate_json(root, job / "begin.json", begin)
        executable = app / "ModOrganizer.exe"
        pending_cleanup = PendingPhaseCleanup(
            root,
            layout,
            phase,
            job,
            request,
            str(executable),
        )
        self._pending_session = pending_cleanup

        def watcher_created(pid: int) -> None:
            pending_cleanup.watcher_created = True
            pending_cleanup.watcher_pid = pid
            containment_service._current_effects().watcher(pid)

        def mo2_created(pid: int) -> None:
            pending_cleanup.mo2_created = True
            pending_cleanup.mo2_pid = pid
            containment_service._current_effects().mo2(pid)

        # Admit attempted immutable publications before delegation: a shared
        # writer may publish and then raise. Never snapshot its live journal.
        for name in (REQUEST_NAME, CLAIM_NAME, LAUNCH_NAME, READY_NAME, EVENTS_NAME):
            containment_service._current_effects().write(watch_root / name)
        start_watch(request, on_created=watcher_created)
        if not pending_cleanup.watcher_created:
            raise GateError("watch startup returned without a worker creation receipt")
        environment = child_environment(
            root,
            layout,
            phase,
            nonce,
            system_root=os.environ.get("SystemRoot", r"C:\Windows"),
            username=os.environ.get("USERNAME", ""),
        )
        def before_resume(value):
            pending_cleanup.launch = value
            pending_cleanup.mo2_creation_time = value.creation_time
            pending_cleanup.process_handle = value.owner.process_handle
            containment_service._current_effects().write(watch_root / CAUSAL_NAMES["LaunchAdmission"])
            windows_watch.admit_watch_launch(watch_root / REQUEST_NAME, value)

        try:
            launch = launch_low_integrity_process(
                executable,
                ("--profile", "ModLab - Lab"),
                app,
                environment,
                on_created=mo2_created,
                retain_owner=True,
                before_resume=before_resume,
            )
        except BaseException as error:
            pending_cleanup.process_owner = containment_service._process_owner_from_error(error)
            raise
        pending_cleanup.launch = launch
        pending_cleanup.mo2_created = True
        pending_cleanup.mo2_pid = getattr(launch, "pid", pending_cleanup.mo2_pid)
        pending_cleanup.mo2_creation_time = getattr(launch, "creation_time", None)
        if launch.integrity is not IntegrityLevel.LOW or launch.executable != str(executable):
            raise GateError("launched process identity or integrity differs")
        pending = LivePhaseSession(
            root,
            layout,
            phase,
            preparation,
            record,
            job,
            app,
            manager,
            str(executable),
            nonce,
            request,
            launch,
            0,
            plugins_before,
            app_before,
            runtime_before,
            protected_before,
            writable_before,
        )
        self._pending_session = pending
        process_handle = launch.owner.process_handle
        pending.process_handle = process_handle
        process = {
            "pid": launch.pid,
            "creationTime": launch.creation_time,
            "executable": launch.executable,
            "integrity": launch.integrity.name,
            "arguments": list(launch.arguments),
            "workingDirectory": launch.working_directory,
        }
        process["processId"] = "phase-process-sha256:" + _sha256(_canonical(process))
        containment_service._current_effects().write(job / "process.json")
        _publish_gate_json(root, job / "process.json", process)
        return pending

    def native(self, session: LivePhaseSession, kind: str) -> Mapping[str, object]:
        if kind not in {"native-before", "native-after"}:
            raise GateError("native observation kind is invalid")
        windows_watch._verify_retained_process_handle(
            session.process_handle,
            session.launch.pid,
            session.launch.creation_time,
        )
        wait = windows_watch._kernel32.WaitForSingleObject(session.process_handle, 0)
        if wait != windows_watch._WAIT_TIMEOUT:
            raise GateError("launched MO2 process is no longer live during UI correlation")
        matches = [
            item for item in _candidate_processes()
            if item.executable_path.casefold() == session.executable.casefold()
        ]
        if len(matches) != 1 or matches[0].pid != session.launch.pid:
            raise GateError("native inventory does not contain one exact launched process")
        windows = _native_windows_for_pid(session.launch.pid)
        if not windows:
            raise GateError("launched MO2 process has no visible native HWND")
        if kind == "native-before":
            session.native_windows_before = windows
        else:
            session.native_windows_after = windows
        return {
            "kind": kind,
            "complete": True,
            "processes": [
                {
                    "pid": session.launch.pid,
                    "creationTime": session.launch.creation_time,
                    "executable": session.executable,
                    "running": True,
                }
            ],
        }

    def finalize_phase(
        self,
        session: LivePhaseSession,
        native_before: Mapping[str, object],
        window: Mapping[str, object],
        native_after: Mapping[str, object],
        close: Mapping[str, object],
    ) -> ContainmentServiceResult:
        return containment_service._receipted(self._finalize_phase)(
            session,
            native_before,
            window,
            native_after,
            close,
        )

    def _finalize_phase(
        self,
        session: LivePhaseSession,
        native_before: Mapping[str, object],
        window: Mapping[str, object],
        native_after: Mapping[str, object],
        close: Mapping[str, object],
    ) -> Mapping[str, object]:
        containment_service._current_effects().child_mutation_root(session.run_root / session.layout)
        containment_service._current_effects().watcher(self._watcher_pid(session))
        containment_service._current_effects().mo2(session.launch.pid)
        expected_window = {
            "windowId", "app", "title", "screenshotId", "screenshotDataUrl",
            "loadedTool", "observedAt",
        }
        if type(window) is not dict or set(window) != expected_window:
            raise GateError("operator window fields are not exact")
        if (
            (type(window["windowId"]) is not int or window["windowId"] <= 0)
            or window["app"] != "process:" + session.executable
            or type(window["title"]) is not str
            or not window["title"]
            or type(window["screenshotId"]) is not str
            or not window["screenshotId"]
            or (session.phase == "Control" and window["loadedTool"] is not None)
            or (session.phase != "Control" and window["loadedTool"] != "ModLab Capability Probe")
        ):
            raise GateError("operator window is not bound to the exact disposable MO2 phase")
        screenshot = _decode_png_data_url(window["screenshotDataUrl"])
        if type(close) is not dict or set(close) != {"action", "windowId", "returned", "observedAt"}:
            raise GateError("normal-close fields are not exact")
        if (
            close["action"] not in {"Alt+F4", "Close button", "File/Exit"}
            or close["windowId"] != window["windowId"]
            or close["returned"] is not True
        ):
            raise GateError("Computer Use did not report the requested normal-close action")
        wait = windows_watch._kernel32.WaitForSingleObject(session.process_handle, 30_000)
        if wait != windows_watch._WAIT_OBJECT_0:
            raise GateError("MO2 did not exit normally within the bounded close interval")
        owner = session.launch.owner
        observation = owner.observe()
        exit_code = observation.root_exit_code
        if exit_code != 0 or observation.active_processes != 0:
            raise GateError("MO2 original process tree did not exit normally and completely")
        containment_service._current_effects().write(session.request.evidence_root / CAUSAL_NAMES["ProcessTreeQuiescence"])
        windows_watch.complete_watch_launch(session.request.evidence_root / REQUEST_NAME, owner)
        if any(item.executable_path.casefold() == session.executable.casefold() for item in _candidate_processes()):
            raise GateError("MO2 candidate process remains after original tree completion")
        before_windows = getattr(session, "native_windows_before", None)
        after_windows = getattr(session, "native_windows_after", None)
        if type(before_windows) is not list or type(after_windows) is not list:
            raise GateError("native HWND observations do not bracket Computer Use")
        correlated = [
            before
            for before in before_windows
            if before in after_windows
            and type(before) is dict
            and set(before) == {"hwnd", "pid", "title", "className", "visible"}
            and before["pid"] == session.launch.pid
            and before["title"] == window["title"]
            and before["visible"] is True
            and type(before["hwnd"]) is int
            and before["hwnd"] > 0
            and type(before["className"]) is str
            and bool(before["className"])
        ]
        if len(correlated) != 1:
            raise GateError("Computer Use title does not bind one stable native HWND for the launched PID")
        native_window = dict(correlated[0])
        image_path = session.job_root / "window.png"
        operator_path = session.job_root / "operator-evidence.json"
        ledger = containment_service._current_effects()
        ledger.write(image_path)
        ledger.write(image_path.with_name("window.png.capture.json"))
        ledger.write(operator_path)

        def validate_image(data: bytes) -> bytes:
            _validate_png_bytes(data)
            if data != screenshot:
                raise GateError("published screenshot bytes differ from Computer Use")
            return data

        windows_exact_fs.publish_new_pinned(image_path, screenshot, validate_image)
        image_data = _read_gate_evidence(session.run_root, image_path, maximum_bytes=MAX_SCREENSHOT_BYTES)
        observed_image = {"sha256": _sha256(image_data), "size": len(image_data)}
        image = {
            "path": str(image_path),
            "sha256": observed_image["sha256"],
            "size": observed_image["size"],
        }
        _publish_gate_json(session.run_root, image_path.with_name("window.png.capture.json"),
                           _screenshot_capture(window, image))
        process = {
            "pid": session.launch.pid,
            "creationTime": session.launch.creation_time,
            "executable": session.executable,
        }
        strict_window = {
            "windowId": window["windowId"],
            "app": window["app"],
            "title": window["title"],
            "screenshotId": window["screenshotId"],
            "image": image,
            "nativeBefore": native_window,
            "nativeAfter": native_window,
        }
        tool = None if session.phase == "Control" else {
            "loadedTool": window["loadedTool"],
            "windowId": window["windowId"],
            "screenshotId": window["screenshotId"],
            "image": image,
        }
        strict_close = {
            "action": close["action"],
            "windowId": window["windowId"],
            "returned": close["returned"],
            "remainingMatches": [],
            "exitCode": exit_code,
        }
        validate_operator_evidence(session.phase, process, strict_window, tool, strict_close, run_root=session.run_root)
        operator = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "task-7B-operator-evidence",
            "runId": session.run_root.name,
            "layout": session.layout,
            "phase": session.phase,
            "process": process,
            "window": strict_window,
            "tool": tool,
            "close": strict_close,
            "windowObservedAt": window["observedAt"],
            "closeObservedAt": close["observedAt"],
            "computerControlUsed": True,
            "authority": False,
        }
        operator["operatorEvidenceId"] = "operator-evidence-sha256:" + _sha256(_canonical(operator))
        _publish_gate_json(session.run_root, operator_path, operator)
        if _load_gate_json(session.run_root, operator_path) != operator:
            raise GateError("operator evidence exact reload differs")
        for name in (
            REQUEST_NAME,
            CLAIM_NAME,
            LAUNCH_NAME,
            READY_NAME,
            EVENTS_NAME,
            TERMINAL_NAME,
            OUTCOME_NAME,
            STOP_NAME,
            *sorted(set(CAUSAL_NAMES.values())),
        ):
            containment_service._current_effects().write(session.request.evidence_root / name)
        receipt = stop_watch(session.request.evidence_root / REQUEST_NAME)
        session.process_handle = 0
        if (
            receipt.request_id != session.request.request_id
            or receipt.session_id != session.request.session_id
            or receipt.run_id != "containment-run:" + session.run_root.name
            or receipt.scenario is not ContainmentScenario.NEW_FOLDER
            or receipt.opened_root_kinds != ROOT_KINDS
        ):
            raise GateError("watch receipt binding differs from the exact phase request")
        protected_after = _capture_protected(session.preparation, session.layout)
        if not watch_proves_unchanged(receipt, session.protected_before, protected_after):
            raise GateError("watcher/protected-state evidence does not prove unchanged")
        plugins_after = snapshot_tree(session.app_root / "plugins")
        app_after = snapshot_tree(session.app_root)
        runtime_after = _runtime_inventory(session.preparation, session.layout)
        writable_after = _snapshot_runtime_writable(session.run_root, session.layout)
        runtime_delta = build_runtime_delta(
            session.run_root,
            session.layout,
            session.phase,
            session.writable_before,
            writable_after,
        )
        validate_runtime_outputs(
            session.run_root,
            session.layout,
            session.phase,
            session.app_before,
            app_after,
            session.runtime_before,
            runtime_after,
        )
        logs: list[dict[str, object]] = []
        raw_logs: list[bytes] = []
        candidates: list[Path] = []
        for root in (session.manager_root / "logs", session.app_root / "logs"):
            if root.is_dir():
                for item in snapshot_tree(root):
                    if item["kind"] == "file" and str(item["path"]).casefold().endswith(".log"):
                        candidates.append(root.joinpath(*str(item["path"]).split("/")))
        targets = _retained_log_targets(session.job_root, len(candidates))
        for source, target in zip(
            sorted(candidates, key=lambda path: str(path).casefold()),
            targets,
            strict=True,
        ):
            ledger.write(target)
            ledger.write(target.with_name(target.name + ".capture.json"))
            capture = _evidence_store(session.run_root).capture_evidence_file(
                "containment-run:" + session.run_root.name, source, target,
                maximum_bytes=MAX_GATE_RECORD_BYTES, stage=f"{session.layout}:{session.phase}:Log")
            data = _read_gate_evidence(session.run_root, target, maximum_bytes=capture["byteCount"])
            logs.append({"path": str(target), "sha256": _sha256(data), "size": len(data)})
            raw_logs.append(data)
        if not logs:
            raise GateError("no exact MO2 log evidence was retained")
        loaded = loaded_from_logs(raw_logs, session.phase, session.nonce)
        ui = {
            "windowId": window["windowId"],
            "app": window["app"],
            "title": window["title"],
            "loadedTool": window["loadedTool"],
            "closeAction": close["action"],
            "screenshotIds": [window["screenshotId"]],
            "processCorrelation": {
                "kind": "unique-exact-executable-correlation",
                "events": [
                    dict(native_before),
                    {
                        "kind": "window-state",
                        "windowId": window["windowId"],
                        "app": window["app"],
                        "screenshotIds": [window["screenshotId"]],
                    },
                    dict(native_after),
                ],
            },
        }
        launch_process = {
            "pid": session.launch.pid,
            "creationTime": session.launch.creation_time,
            "executable": session.executable,
        }
        observation = {
            "phase": session.phase,
            "sequence": PHASES.index(session.phase),
            "job": str(session.job_root),
            "nonce": session.nonce,
            "pid": session.launch.pid,
            "before": session.plugins_before,
            "after": plugins_after,
            "ownBefore": own_inventory(session.plugins_before),
            "ownAfter": own_inventory(plugins_after),
            "runtimeBefore": session.runtime_before,
            "runtimeAfter": runtime_after,
            "exitCode": exit_code,
            "normalClose": True,
            "loaded": loaded,
            "guarded": None,
            "ui": ui,
            "logs": logs,
            "launchProcess": launch_process,
        }
        if session.phase == "Guarded":
            _validate_observation_and_publish_guarded(session, observation)
        else:
            runtime_capability._observation(
                observation,
                {"layout": session.layout, "appRoot": str(session.app_root)},
                {"schemaVersion": 3, "root": str(session.run_root), "authorityRoot": str(authority_run_root(session.run_root)), "runId": session.run_root.name},
                PHASES.index(session.phase),
            )
            runtime_capability._verify_raw_evidence(
                {"schemaVersion": 3, "authorityRoot": str(authority_run_root(session.run_root)), "candidates": [{"control": observation, "observations": []}]}
            )
        return {
            "observation": observation,
            "watchReceipt": receipt,
            "protectedAfter": protected_after,
            "appAfter": app_after,
            "windowObservedAt": window["observedAt"],
            "closeObservedAt": close["observedAt"],
            "operatorEvidenceId": operator["operatorEvidenceId"],
            "runtimeDelta": runtime_delta,
        }

    @staticmethod
    def _watcher_pid(session: LivePhaseSession) -> int:
        path = session.request.evidence_root / LAUNCH_NAME
        value = parse_json(_read_gate_evidence(session.run_root, path))
        pid = value.get("workerPid") if isinstance(value, Mapping) else None
        if type(pid) is not int or pid <= 0:
            raise GateError("watch worker launch PID is unavailable")
        return pid

    def publish_phase(
        self,
        session: LivePhaseSession,
        draft: Mapping[str, object],
        effects: ContainmentEffects,
    ) -> ContainmentServiceResult:
        return containment_service._receipted(self._publish_phase)(session, draft, effects)

    def _publish_phase(
        self,
        session: LivePhaseSession,
        draft: Mapping[str, object],
        effects: ContainmentEffects,
    ) -> Mapping[str, object]:
        receipt = draft.get("watchReceipt")
        protected_after = draft.get("protectedAfter")
        runtime_delta = draft.get("runtimeDelta")
        if not isinstance(receipt, WatchReceipt) or not isinstance(protected_after, ProtectedState):
            raise GateError("phase draft lacks exact watcher/protected evidence")
        validate_watch_effect(receipt, session.protected_before, protected_after, effects)
        if effects.mo2_pid != session.launch.pid:
            raise GateError("service effect MO2 PID differs from the exact phase launch")
        if not isinstance(runtime_delta, Mapping):
            raise GateError("phase draft lacks an exact runtime delta")
        runtime_delta = _validate_runtime_delta_record(
            session.run_root,
            session.layout,
            session.phase,
            _load_gate_json(session.run_root, session.job_root / "begin.json"),
            runtime_delta,
        )
        ledger = containment_service._current_effects()
        publication_paths = [
            session.job_root / "begin.json",
            session.job_root / "process.json",
            session.job_root / "window.png",
            session.job_root / "window.png.capture.json",
            session.job_root / "operator-evidence.json",
            session.job_root / "runtime-delta.json",
            session.job_root / "observation.json",
            session.job_root / "effect.json",
            session.job_root / "phase-final.json",
            *(session.request.evidence_root / name for name in (
                REQUEST_NAME,
                CLAIM_NAME,
                LAUNCH_NAME,
                READY_NAME,
                EVENTS_NAME,
                TERMINAL_NAME,
                OUTCOME_NAME,
                STOP_NAME,
                *sorted(set(CAUSAL_NAMES.values())),
            )),
        ]
        observation = draft["observation"]
        for reference in observation.get("logs", ()) if isinstance(observation, Mapping) else ():
            if isinstance(reference, Mapping) and type(reference.get("path")) is str:
                publication_paths.append(Path(reference["path"]).absolute())
                publication_paths.append(Path(reference["path"] + ".capture.json").absolute())
        if isinstance(observation, Mapping) and observation.get("guarded") is not None:
            publication_paths.append(session.job_root / "guarded.json")
        for path in publication_paths:
            ledger.write(path)
        combined = ContainmentEffects.merged(effects, ledger.freeze())
        effect = build_effect_record(f"{session.layout}:{session.phase}", combined)
        _publish_gate_json(session.run_root, session.job_root / "runtime-delta.json", runtime_delta)
        _publish_gate_json(session.run_root, session.job_root / "effect.json", effect)
        observation_id = "observation-sha256:" + _sha256(_canonical(observation))
        _publish_gate_json(session.run_root, session.job_root / "observation.json", observation)
        final = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "task-7B-phase-final",
            "runId": session.run_root.name,
            "layout": session.layout,
            "phase": session.phase,
            "preparationId": session.preparation["preparationId"],
            "observationId": observation_id,
            "watchRequestId": receipt.request_id,
            "watchOutcomeId": receipt.watch_outcome_id,
            "effectId": effect["effectId"],
            "operatorEvidenceId": draft["operatorEvidenceId"],
            "beginId": _load_gate_json(session.run_root, session.job_root / "begin.json")["beginId"],
            "processId": _load_gate_json(session.run_root, session.job_root / "process.json")["processId"],
            "runtimeDeltaId": runtime_delta["runtimeDeltaId"],
            "watchEvidence": {
                name: _sha256(_read_gate_evidence(session.run_root, session.request.evidence_root / filename))
                for name, filename in (
                    ("requestSha256", REQUEST_NAME),
                    ("claimSha256", CLAIM_NAME),
                    ("launchSha256", LAUNCH_NAME),
                    ("readySha256", READY_NAME),
                    ("eventsSha256", EVENTS_NAME),
                    ("terminalSha256", TERMINAL_NAME),
                    ("outcomeSha256", OUTCOME_NAME),
                    ("stopSha256", STOP_NAME),
                    ("admissionSha256", CAUSAL_NAMES["LaunchAdmission"]),
                    ("quiescenceSha256", CAUSAL_NAMES["ProcessTreeQuiescence"]),
                    ("workerExitSha256", CAUSAL_NAMES["WorkerExitObservation"]),
                )
            },
            "protectedBefore": _json_value(session.protected_before),
            "protectedAfter": _json_value(protected_after),
            "windowObservedAt": draft["windowObservedAt"],
            "closeObservedAt": draft["closeObservedAt"],
            "completedAt": _now(),
            "authority": False,
        }
        final["phaseFinalId"] = "phase-final-sha256:" + _sha256(_canonical(final))
        _publish_gate_json(session.run_root, session.job_root / "phase-final.json", final)
        _load_validated_phase_bundle(
            session.run_root,
            session.layout,
            session.phase,
            str(session.preparation["preparationId"]),
        )
        expected_state = DurableLayoutState(
            PHASES[: PHASES.index(session.phase) + 1],
            session.phase != "Control",
        )
        if self.load_state(session.run_root, session.layout) != expected_state:
            raise GateError("phase publication did not become the exact durable writable-state head")
        self._pending_session = None
        return final

    def finalize_gate(self, run_root: Path) -> ContainmentServiceResult:
        return containment_service._receipted(self._finalize_gate)(run_root)

    def _finalize_gate(self, run_root: Path) -> Mapping[str, object]:
        root = Path(run_root).absolute()
        preparation = _fresh_preparation_preflight(root)
        _require_process_absence()
        containment_service._current_effects().child_mutation_root(root)
        candidates: list[dict[str, object]] = []
        phase_ids: list[str] = []
        watch_ids: list[str] = []
        effect_ids: list[str] = []
        receipts: list[tuple[int, int, str, str]] = []
        for layout in LAYOUTS:
            if self.load_state(root, layout) != DurableLayoutState(PHASES, True):
                raise GateError("selection requires both complete durable layout matrices")
            installation = _load_installation(root, layout)
            declared = installation.get("declared") if isinstance(installation, Mapping) else None
            if type(declared) is not list:
                raise GateError("candidate installation record is malformed")
            observations = []
            for phase in PHASES:
                observation, bundle = _load_validated_phase_bundle(
                    root,
                    layout,
                    phase,
                    str(preparation["preparationId"]),
                )
                phase_ids.append(str(bundle["observationId"]))
                watch_ids.append(str(bundle["watchOutcomeId"]))
                effect_ids.append(str(bundle["effectId"]))
                receipts.append(
                    (
                        int(bundle["watcherPid"]),
                        int(bundle["mo2Pid"]),
                        str(bundle["requestId"]),
                        str(bundle["sessionId"]),
                    )
                )
                observations.append(observation)
            candidates.append(
                {
                    "layout": layout,
                    "appRoot": str(root / layout / "app"),
                    "declared": declared,
                    "control": observations[0],
                    "observations": observations[1:],
                }
            )
        if len(receipts) != 10 or any(len({item[index] for item in receipts}) != 10 for index in range(4)):
            raise GateError("all ten watcher/MO2/request/session identities must be distinct")
        matrix = {
            "schemaVersion": 3,
            "runId": root.name,
            "root": str(root),
            "authorityRoot": str(authority_run_root(root)),
            "source": dict(preparation["source"]),
            "archive": {
                "sha256": preparation["archive"]["sha256"],
                "size": preparation["archive"]["size"],
            },
            "candidates": candidates,
        }
        selection = derive_selection(matrix)
        containment_service._current_effects().write(authority_run_root(root) / "selection.json")
        publish_capability(authority_run_root(root) / "selection.json", selection)
        exact_selection = load_capability(authority_run_root(root) / "selection.json", authority_root=authority_run_root(root))
        if exact_selection != selection or capability_to_bytes(exact_selection) != _read_gate_evidence(root, authority_run_root(root) / "selection.json"):
            raise GateError("selection exact publication/reload differs")
        source = {
            **dict(preparation["source"]),
            "artifactId": preparation["sourceArtifactId"],
        }
        envelope = build_envelope(
            run_id=root.name,
            source=source,
            preparation_id=str(preparation["preparationId"]),
            phase_ids=tuple(phase_ids),
            watch_outcome_ids=tuple(watch_ids),
            effect_ids=tuple(effect_ids),
            selection=exact_selection,
        )
        containment_service._current_effects().write(authority_run_root(root) / "full-stack-envelope.json")
        _publish_gate_json(root, authority_run_root(root) / "full-stack-envelope.json", envelope)
        exact_envelope = load_envelope(authority_run_root(root) / "full-stack-envelope.json")
        if exact_envelope != envelope:
            raise GateError("full-stack envelope exact publication/reload differs")
        return {"selection": exact_selection, "envelope": exact_envelope}

    def fail_phase(
        self,
        run_root: Path,
        layout: str,
        phase: str,
        error: BaseException,
        effects: ContainmentEffects,
        session: LivePhaseSession | None = None,
    ) -> ContainmentEffects:
        job = authority_run_root(run_root) / layout / "jobs" / phase
        pending = self._pending_session
        if session is None and pending is not None and (
            pending.run_root == Path(run_root).absolute()
            and pending.layout == layout
            and pending.phase == phase
        ):
            session = pending
        if not job.is_dir() or (job / "failure.json").exists():
            return effects
        owner = session
        request = getattr(owner, "request", None) if owner is not None else None
        launch = getattr(owner, "launch", None) if owner is not None else None
        process_handle = int(getattr(owner, "process_handle", 0) or 0) if owner is not None else 0
        process_handle_acquired = bool(process_handle)
        watcher_pid = getattr(owner, "watcher_pid", None) if owner is not None else None
        if watcher_pid is None:
            watcher_pid = effects.watcher_pid
        watcher_created = bool(getattr(owner, "watcher_created", False) or watcher_pid is not None)
        mo2_pid = getattr(owner, "mo2_pid", None) if owner is not None else None
        if mo2_pid is None and launch is not None:
            mo2_pid = getattr(launch, "pid", None)
        if mo2_pid is None:
            mo2_pid = effects.mo2_pid
        mo2_creation_time = getattr(owner, "mo2_creation_time", None) if owner is not None else None
        if mo2_creation_time is None and launch is not None:
            mo2_creation_time = getattr(launch, "creation_time", None)
        executable = getattr(owner, "executable", None) if owner is not None else None
        if executable is None and launch is not None:
            executable = getattr(launch, "executable", None)
        cleanup_errors: list[str] = []
        cleanup_failures: list[BaseException] = []
        cleanup_writes = []
        process_live = False
        process_handle_cleanup_pending = False
        process_owner = (getattr(launch, "owner", None) or getattr(owner, "process_owner", None)
                         or containment_service._process_owner_from_error(error))
        if owner is not None and process_owner is not None:
            owner.process_owner = process_owner
        if type(mo2_pid) is int and mo2_pid > 0:
            process_live = True
            process_handle_cleanup_pending = True
            if process_owner is None:
                cleanup_errors.append("original process tree owner unavailable")
            else:
                try:
                    observed = process_owner.observe()
                    process_live = observed.root_exit_code is None or observed.active_processes != 0
                    if not process_live and request is not None:
                        cleanup_writes.append(request.evidence_root / CAUSAL_NAMES["ProcessTreeQuiescence"])
                        windows_watch.complete_watch_launch(request.evidence_root / REQUEST_NAME, process_owner)
                except BaseException as cleanup_error:
                    cleanup_errors.append(str(cleanup_error) or type(cleanup_error).__name__)
                    cleanup_failures.append(cleanup_error)
                    process_live = True

        evidence_root = getattr(request, "evidence_root", None) if request is not None else None
        request_id = getattr(request, "request_id", None) if request is not None else None
        session_id = getattr(request, "session_id", None) if request is not None else None
        request_run_id = getattr(request, "run_id", None) if request is not None else None
        request_scenario = getattr(request, "scenario", None) if request is not None else None
        watch_identity = None
        if watcher_created:
            try:
                watch_identity = _load_bound_watch_identity(job, Path(run_root).absolute())
                if (
                    request != watch_identity.request
                    or watcher_pid != watch_identity.launch.worker_pid
                    or request_run_id != watch_identity.request.run_id
                    or request_scenario is not watch_identity.request.scenario
                ):
                    raise GateError("failed phase watcher callback identity differs")
            except BaseException as identity_error:
                cleanup_errors.append(str(identity_error) or type(identity_error).__name__)
                cleanup_failures.append(identity_error)
                watch_identity = None
        watch_cleanup_pending = watcher_created and (process_live or watch_identity is None)
        if watcher_created and not process_live:
            try:
                if evidence_root is None:
                    raise GateError("watch evidence root is unavailable for failure cleanup")
                cleanup_writes.extend(Path(evidence_root) / name for name in (STOP_NAME, EVENTS_NAME, TERMINAL_NAME, OUTCOME_NAME, CAUSAL_NAMES["WorkerExitObservation"]))
                receipt = stop_watch(Path(evidence_root) / REQUEST_NAME)
                _require_cleanup_receipt(receipt, request, watcher_pid)
                watch_cleanup_pending = watch_identity is None
                process_handle_cleanup_pending = False
                if owner is not None:
                    owner.process_handle = 0
            except BaseException as watch_error:
                watch_cleanup_pending = True
                cleanup_errors.append(str(watch_error) or type(watch_error).__name__)
                cleanup_failures.append(watch_error)
        cleanup = {
            "watcherCreated": watcher_created,
            "watcherPid": watcher_pid,
            "requestId": request_id,
            "sessionId": session_id,
            "evidenceRoot": str(evidence_root) if evidence_root is not None else None,
            "watchIdentityAvailable": watch_identity is not None,
            "requestSha256": None if watch_identity is None else watch_identity.request_sha256,
            "controllerPid": None if watch_identity is None else watch_identity.claim.controller_pid,
            "controllerCreationTime": None if watch_identity is None else watch_identity.claim.controller_creation_time,
            "controllerClaimSha256": None if watch_identity is None else watch_identity.claim_sha256,
            "watcherCreationTime": None if watch_identity is None else watch_identity.launch.worker_creation_time,
            "workerLaunchSha256": None if watch_identity is None else watch_identity.launch_sha256,
            "mo2Created": type(mo2_pid) is int and mo2_pid > 0,
            "mo2Pid": mo2_pid,
            "mo2CreationTime": mo2_creation_time,
            "mo2CreationTimeAvailable": type(mo2_creation_time) is int and mo2_creation_time > 0,
            "executable": executable,
            "processHandleAcquired": process_handle_acquired,
            "processHandleCleanupPending": process_handle_cleanup_pending,
        }
        cleanup_ownership = None
        for cleanup_failure in cleanup_failures:
            partial = getattr(cleanup_failure, "effects", None)
            if isinstance(partial, ContainmentEffects):
                effects = ContainmentEffects.merged(effects, partial)
            retained = _phase_native_ownership(cleanup_failure)
            if retained is not None:
                cleanup_ownership = windows_exact_fs.union_retained_ownership(
                    "phase failure cleanup retains original handles",
                    prior=cleanup_ownership or _phase_native_ownership(error), owners=retained.owners)
        effects = ContainmentEffects.merged(effects, ContainmentEffects(written_paths=tuple(cleanup_writes)))
        failure = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "task-7B-phase-failed-attempt",
            "runId": Path(run_root).name,
            "layout": layout,
            "phase": phase,
            "terminal": True,
            "retryPermitted": False,
            "processMayStillBeLive": process_live,
            "watchCleanupPending": watch_cleanup_pending,
            "cleanupErrors": cleanup_errors,
            "cleanup": cleanup,
            "errorType": type(error).__name__,
            "error": str(error) or type(error).__name__,
            "effects": build_effect_record(
                f"{layout}:{phase}:failed",
                ContainmentEffects.merged(
                    effects,
                    ContainmentEffects(written_paths=(job / "failure.json",)),
                ),
            ),
            "authority": False,
        }
        failure["failureId"] = "phase-failure-sha256:" + _sha256(_canonical(failure))
        failure_effects = ContainmentEffects.merged(effects, ContainmentEffects(written_paths=(job / "failure.json",)))
        pending_cleanup = process_live or process_handle_cleanup_pending or watch_cleanup_pending
        self._pending_session = owner if pending_cleanup and owner is not None else None
        try:
            _publish_gate_json(run_root, job / "failure.json", failure)
        except BaseException as publication_error:
            partial = getattr(publication_error, "effects", None)
            if isinstance(partial, ContainmentEffects):
                failure_effects = ContainmentEffects.merged(failure_effects, partial)
            publication_error.effects = failure_effects
            if process_owner is not None:
                publication_error.owner = process_owner
            if cleanup_ownership is not None:
                publication_ownership = _phase_native_ownership(publication_error)
                combined = windows_exact_fs.union_retained_ownership(
                    f"phase cleanup and failure publication retain original handles: {publication_error}",
                    prior=cleanup_ownership,
                    owners=() if publication_ownership is None else publication_ownership.owners)
                combined.owner = process_owner
                combined.effects = failure_effects
                raise combined from publication_error
            raise
        if cleanup_ownership is not None:
            # Publish immutable failure facts before returning retryable native pins.
            cleanup_ownership.owner = process_owner
            cleanup_ownership.effects = failure_effects
            raise cleanup_ownership from error
        return failure_effects

    def cleanup_failed_phase(
        self,
        run_root: Path,
        layout: str,
        phase: str,
    ) -> ContainmentServiceResult:
        return containment_service._receipted(self._cleanup_failed_phase)(run_root, layout, phase)

    def _cleanup_failed_phase(
        self,
        run_root: Path,
        layout: str,
        phase: str,
    ) -> Mapping[str, object]:
        root = Path(run_root).absolute()
        if layout not in LAYOUTS or phase not in PHASES or _RUN.fullmatch(root.name) is None:
            raise GateError("failed phase cleanup binding is invalid")
        job = authority_run_root(root) / layout / "jobs" / phase
        existing = job / "cleanup.json"
        if existing.is_file():
            return _load_phase_cleanup(root, layout, phase)
        failure = _load_phase_failure(root, layout, phase)
        cleanup = failure["cleanup"]
        _require_process_absence()
        ledger = containment_service._current_effects()
        ledger.child_mutation_root(root)
        outcome_id = None
        watch_evidence = None
        if cleanup["watcherCreated"]:
            if cleanup["watchIdentityAvailable"] is not True:
                raise GateError("failed-attempt watcher exact identity is unavailable")
            identity = _load_bound_watch_identity(job, root)
            request = identity.request
            watch = request.evidence_root
            receipt = windows_watch.watch_receipt_from_files(request, identity.launch.worker_pid,
                watch / READY_NAME, watch / EVENTS_NAME, watch / TERMINAL_NAME)
            if not receipt.complete:
                # Only the original shared session can prove this tree closed.
                # No numeric PID inventory or saved outcome substitutes for it.
                ledger.write(watch / CAUSAL_NAMES["ProcessTreeQuiescence"])
                windows_watch.complete_watch_launch(watch / REQUEST_NAME)
                for name in (STOP_NAME, EVENTS_NAME, TERMINAL_NAME, OUTCOME_NAME, CAUSAL_NAMES["WorkerExitObservation"]):
                    ledger.write(watch / name)
                receipt = stop_watch(watch / REQUEST_NAME)
            _require_cleanup_receipt(receipt, request, identity.launch.worker_pid)
            outcome_id = receipt.watch_outcome_id
            watch_evidence = _load_cleanup_watch_evidence(job, root, cleanup, outcome_id)
            if watch_evidence.receipt != receipt:
                raise GateError("cleanup receipt differs from shared raw reconstruction")
        elif cleanup["mo2Created"]:
            raise GateError("original process tree ownership is unavailable for incomplete attempt")

        ledger.write(existing)
        effect = build_effect_record(f"{layout}:{phase}:cleanup", ledger.freeze())
        record = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "task-7B-failed-phase-cleanup",
            "runId": root.name,
            "layout": layout,
            "phase": phase,
            "failureId": failure["failureId"],
            "requestId": cleanup["requestId"],
            "sessionId": cleanup["sessionId"],
            "watcherPid": cleanup["watcherPid"],
            "watchOutcomeId": outcome_id,
            "requestSha256": cleanup["requestSha256"],
            "controllerPid": cleanup["controllerPid"],
            "controllerCreationTime": cleanup["controllerCreationTime"],
            "controllerClaimSha256": cleanup["controllerClaimSha256"],
            "watcherCreationTime": cleanup["watcherCreationTime"],
            "workerLaunchSha256": cleanup["workerLaunchSha256"],
            "watchEvidenceCompletion": (
                None if watch_evidence is None else watch_evidence.outcome.evidence_completion.value
            ),
            "watchReasonCodes": (
                [] if watch_evidence is None else list(watch_evidence.outcome.reason_codes)
            ),
            "watchRootIdentitiesUnchanged": (
                None if watch_evidence is None else watch_evidence.outcome.root_identities_unchanged
            ),
            "mo2Pid": cleanup["mo2Pid"],
            "mo2CreationTime": cleanup["mo2CreationTime"],
            "mo2Absent": True,
            "watcherQuiescent": True,
            "terminal": True,
            "retryPermitted": False,
            "freshAttemptRequired": True,
            "cleanedAt": _now(),
            "effect": effect,
            "authority": False,
        }
        record["cleanupId"] = "phase-cleanup-sha256:" + _sha256(_canonical(record))
        _publish_gate_json(root, existing, record)
        exact = _load_phase_cleanup(root, layout, phase)
        if exact != record:
            raise GateError("failed phase cleanup exact reload differs")
        self._pending_session = None
        return exact


class JsonLineExchange:
    """Two-message root/operator exchange while the controller stays alive."""

    def __init__(self, input_stream=None, output_stream=None) -> None:
        self.input = sys.stdin.buffer if input_stream is None else input_stream
        self.output = sys.stdout if output_stream is None else output_stream

    def _receive(self, prompt: Mapping[str, object]) -> Mapping[str, object]:
        payload = (json.dumps(prompt, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            self.output.write(payload)
        except TypeError:
            self.output.write(payload.decode("utf-8"))
        self.output.flush()
        data = self.input.readline()
        if type(data) is str:
            data = data.encode("utf-8")
        if not data:
            raise GateError("operator exchange ended before required evidence")
        value = parse_json(data)
        if type(value) is not dict:
            raise GateError("operator exchange payload is not an object")
        return value

    def window(self, session: LivePhaseSession) -> Mapping[str, object]:
        return self._receive(
            {
                "operatorAction": "capture-visible-window",
                "runId": session.run_root.name,
                "layout": session.layout,
                "phase": session.phase,
                "pid": session.launch.pid,
                "creationTime": session.launch.creation_time,
                "executable": session.executable,
            }
        )

    def close(self, session: LivePhaseSession, window: Mapping[str, object]) -> Mapping[str, object]:
        return self._receive(
            {
                "operatorAction": "close-window-normally-and-confirm-absence",
                "runId": session.run_root.name,
                "layout": session.layout,
                "phase": session.phase,
                "windowId": window.get("windowId"),
                "executable": session.executable,
            }
        )


def next_action(completed: tuple[str, ...], *, installed: bool = False) -> str:
    if type(completed) is not tuple or any(type(item) is not str for item in completed):
        raise GateError("completed phases must be an exact tuple")
    order = PHASES
    if len(completed) > len(order) or completed != order[: len(completed)]:
        raise GateError("phase history is not an exact ordered prefix")
    if not completed:
        if installed:
            raise GateError("candidate cannot exist before Control")
        return "Control"
    if completed == ("Control",) and not installed:
        return "Install"
    if len(completed) < len(order):
        if not installed:
            raise GateError("candidate must be installed exactly after Control")
        return order[len(completed)]
    if not installed:
        raise GateError("completed matrix lost its installed candidate")
    return "Complete"


def child_environment(
    run_root: Path,
    layout: str,
    phase: str,
    nonce: str,
    *,
    system_root: str,
    username: str,
) -> dict[str, str]:
    return launch_environment(
        Path(run_root).absolute(),
        layout,
        phase,
        nonce,
        inherited={"SystemRoot": system_root, "USERNAME": username},
    )


def _inventory_map(value: object, label: str) -> dict[str, dict[str, object]]:
    if type(value) is not list or len(value) > INVENTORY_LIMIT:
        raise GateError(f"{label} inventory is malformed or unbounded")
    result: dict[str, dict[str, object]] = {}
    for item in value:
        if type(item) is not dict or type(item.get("path")) is not str:
            raise GateError(f"{label} inventory entry is malformed")
        path = item["path"].replace("\\", "/")
        if path != item["path"] or path.startswith("/") or any(part in {"", ".", ".."} for part in path.split("/")):
            raise GateError(f"{label} inventory path is unsafe")
        key = path.casefold()
        if key in result:
            raise GateError(f"{label} inventory contains a collision")
        result[key] = item
    return result


def _runtime_writable_roots(run_root: Path, layout: str) -> dict[str, Path]:
    root = Path(run_root).absolute()
    if layout not in LAYOUTS or _RUN.fullmatch(root.name) is None:
        raise GateError("runtime writable-root binding is invalid")
    layout_root = root / layout
    return {
        "App": layout_root / "app",
        "Manager": workspace_layout(layout_root / "bootstrap").skyrim_mo2,
        "Environment": layout_root / "environment",
    }


def _snapshot_runtime_writable(run_root: Path, layout: str) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for name, root in _runtime_writable_roots(run_root, layout).items():
        if not root.is_dir() or root.is_symlink():
            raise GateError(f"runtime writable root is missing or redirected: {name}")
        before = windows_exact_fs.identity_at_path(root)
        inventory = snapshot_tree(root)
        after = windows_exact_fs.identity_at_path(root)
        if before != after:
            raise GateError(f"runtime writable root identity changed during snapshot: {name}")
        result[name] = {"rootIdentity": _identity(before), "inventory": inventory}
    return result


def _strict_inventory(value: object, label: str) -> dict[str, dict[str, object]]:
    rows = _inventory_map(value, label)
    for item in rows.values():
        common = {"path", "kind", "volume", "fileId"}
        if item.get("kind") == "file":
            if (
                set(item) != common | {"sha256", "size"}
                or type(item.get("sha256")) is not str
                or _HEX64.fullmatch(item["sha256"]) is None
                or type(item.get("size")) is not int
                or item["size"] < 0
            ):
                raise GateError(f"{label} file inventory entry is malformed")
        elif item.get("kind") == "directory":
            if set(item) != common | {"sha256", "size"} or item.get("sha256") is not None or item.get("size") != 0:
                raise GateError(f"{label} directory inventory entry is malformed")
        else:
            raise GateError(f"{label} inventory kind is invalid")
        if (
            type(item.get("volume")) is not int
            or item["volume"] <= 0
            or type(item.get("fileId")) is not int
            or item["fileId"] <= 0
        ):
            raise GateError(f"{label} inventory identity is malformed")
    return rows


def _runtime_snapshot(value: object, label: str) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    snapshot = _exact_object(value, {"rootIdentity", "inventory"}, label)
    identity = _exact_object(
        snapshot["rootIdentity"],
        {"volumeSerial", "fileId", "attributes"},
        f"{label} root identity",
    )
    if any(type(identity[name]) is not int or identity[name] <= 0 for name in ("volumeSerial", "fileId")) \
            or type(identity["attributes"]) is not int or identity["attributes"] < 0:
        raise GateError(f"{label} root identity is malformed")
    return snapshot, _strict_inventory(snapshot["inventory"], f"{label} inventory")


def _validated_writable_snapshots(
    run_root: Path,
    layout: str,
    value: object,
    label: str,
    *,
    require_current_roots: bool = False,
) -> dict[str, object]:
    roots = _runtime_writable_roots(run_root, layout)
    record = _exact_object(value, set(roots), label)
    result: dict[str, object] = {}
    for name, path in roots.items():
        snapshot, inventory = _runtime_snapshot(record[name], f"{label} {name}")
        for item in inventory.values():
            if _runtime_entry_forbidden(str(item["path"])):
                raise GateError(f"{label} contains a forbidden entry: {item['path']}")
        if require_current_roots:
            try:
                current_identity = _identity(windows_exact_fs.identity_at_path(path))
            except OSError as error:
                raise GateError(f"{label} root identity is unavailable: {name}") from error
            if snapshot["rootIdentity"] != current_identity:
                raise GateError(f"{label} root identity differs: {name}")
        result[name] = snapshot
    return result


def _validate_installation_writable_transition(
    run_root: Path,
    layout: str,
    declared: object,
    before: object,
    after: object,
) -> tuple[dict[str, object], dict[str, object]]:
    previous = _validated_writable_snapshots(run_root, layout, before, "installation writable-before")
    current = _validated_writable_snapshots(run_root, layout, after, "installation writable-after")
    declared_rows = _strict_inventory(declared, "installation declared inventory")
    for name in previous:
        if previous[name]["rootIdentity"] != current[name]["rootIdentity"]:
            raise GateError(f"installation writable root identity changed: {name}")
    if previous["Manager"] != current["Manager"] or previous["Environment"] != current["Environment"]:
        raise GateError("candidate installation changed Manager or Environment state")
    additions: list[dict[str, object]] = []
    for item in declared_rows.values():
        if _runtime_entry_forbidden(str(item["path"])):
            raise GateError("candidate installation declared forbidden bytecode/cache output")
        additions.append({**item, "path": "plugins/" + str(item["path"])})
    expected = sorted(
        [*previous["App"]["inventory"], *additions],
        key=lambda item: str(item["path"]).casefold(),
    )
    actual = sorted(current["App"]["inventory"], key=lambda item: str(item["path"]).casefold())
    if actual != expected:
        raise GateError("candidate installation writable transition contains undeclared changes")
    return previous, current


def _runtime_delta_snapshots(
    run_root: Path,
    layout: str,
    phase: str,
    value: object,
) -> tuple[dict[str, object], dict[str, object]]:
    record = _record_id(value, "runtimeDeltaId", "runtime-delta-sha256:", "runtime delta chain record")
    roots = _runtime_writable_roots(run_root, layout)
    if (
        record.get("runId") != Path(run_root).absolute().name
        or record.get("layout") != layout
        or record.get("phase") != phase
        or type(record.get("roots")) is not list
        or len(record["roots"]) != len(roots)
    ):
        raise GateError("runtime delta chain binding differs")
    before: dict[str, object] = {}
    after: dict[str, object] = {}
    for name, path, row in zip(roots, roots.values(), record["roots"], strict=True):
        item = _exact_object(row, {"rootKind", "path", "before", "after", "changes"}, "runtime delta chain root")
        if item["rootKind"] != name or item["path"] != str(path):
            raise GateError("runtime delta chain root differs")
        before[name] = item["before"]
        after[name] = item["after"]
    return (
        _validated_writable_snapshots(run_root, layout, before, f"{phase} writable-before"),
        _validated_writable_snapshots(run_root, layout, after, f"{phase} writable-after"),
    )


def _validate_writable_chain(
    run_root: Path,
    layout: str,
    preparation_baseline: object,
    completed: tuple[str, ...],
    installation: Mapping[str, object] | None,
    deltas: Mapping[str, Mapping[str, object]],
    current: object,
) -> None:
    if completed != PHASES[: len(completed)] or set(deltas) != set(completed):
        raise GateError("writable chain phases are not one exact prefix")
    expected = _validated_writable_snapshots(
        run_root,
        layout,
        preparation_baseline,
        "preparation writable baseline",
    )
    installation_before = installation_after = None
    if installation is not None:
        installation_before, installation_after = _validate_installation_writable_transition(
            run_root,
            layout,
            installation.get("declared"),
            installation.get("writableBefore"),
            installation.get("writableAfter"),
        )
        if not completed or completed[0] != "Control":
            raise GateError("installation writable transition lacks completed Control")
    for phase in completed:
        phase_before, phase_after = _runtime_delta_snapshots(run_root, layout, phase, deltas[phase])
        if phase_before != expected:
            raise GateError(f"unobserved writable-state drift precedes {phase}")
        expected = phase_after
        if phase == "Control" and installation is not None:
            if installation_before != expected:
                raise GateError("installation writable-before differs from Control after")
            expected = installation_after
    actual = _validated_writable_snapshots(run_root, layout, current, "current writable state")
    if actual != expected:
        raise GateError("current writable state differs from the latest durable transition")


def _runtime_entry_forbidden(relative: str) -> bool:
    lowered = relative.casefold()
    return "__pycache__" in lowered.split("/") or lowered.endswith((".pyc", ".pyo"))


def _runtime_change_allowed(phase: str, root_name: str, relative: str) -> bool:
    if phase not in PHASES:
        return False
    prefixes = {
        current: {
            "App": (),
            "Manager": ("logs", "webcache", "cache"),
            "Environment": ("temp", "tmp", "appdata", "localappdata", "userprofile", "home"),
        }
        for current in PHASES
    }
    lowered = relative.casefold()
    return not _runtime_entry_forbidden(relative) and any(
        lowered == prefix or lowered.startswith(prefix + "/")
        for prefix in prefixes[phase][root_name]
    )


def build_runtime_delta(
    run_root: Path,
    layout: str,
    phase: str,
    before: Mapping[str, object],
    after: Mapping[str, object],
) -> dict[str, object]:
    roots = _runtime_writable_roots(run_root, layout)
    if phase not in PHASES or set(before) != set(roots) or set(after) != set(roots):
        raise GateError("runtime delta root/phase fields are not exact")
    records: list[dict[str, object]] = []
    for name, root in roots.items():
        before_snapshot, previous = _runtime_snapshot(before[name], f"{name} before")
        after_snapshot, current = _runtime_snapshot(after[name], f"{name} after")
        if before_snapshot["rootIdentity"] != after_snapshot["rootIdentity"]:
            raise GateError(f"runtime writable root identity changed: {name}")
        for item in (*previous.values(), *current.values()):
            if _runtime_entry_forbidden(str(item["path"])):
                raise GateError(f"runtime snapshot contains a forbidden entry: {item['path']}")
        try:
            admitted = [
                PlannedPath(
                    f"gate-runtime-delta:{layout}:{phase}:{name}",
                    "preparation-wide",
                    str(root),
                ),
                *(
                PlannedPath(
                    f"gate-runtime-delta:{layout}:{phase}:{name}",
                    "preparation-wide",
                    str(root.joinpath(*item["path"].split("/"))),
                )
                for item in current.values()
                ),
            ]
            admit_paths(admitted)
        except (OSError, PathBudgetError, ValueError) as error:
            raise GateError(f"runtime delta path budget refused: {error}") from error
        changes: list[dict[str, object]] = []
        keys = sorted(set(previous) | set(current))
        for key in keys:
            old = previous.get(key)
            new = current.get(key)
            if old == new:
                continue
            relative = str((old or new)["path"])
            if not _runtime_change_allowed(phase, name, relative):
                raise GateError(f"runtime write escaped the declared {name} roots: {relative}")
            changes.append({
                "path": relative,
                "change": "Added" if old is None else "Removed" if new is None else "Modified",
                "before": old,
                "after": new,
            })
        records.append({
            "rootKind": name,
            "path": str(root),
            "before": before_snapshot,
            "after": after_snapshot,
            "changes": changes,
        })
    record = {
        "schemaVersion": RUNTIME_POLICY_VERSION,
        "kind": "task-7B-runtime-delta",
        "runId": Path(run_root).absolute().name,
        "layout": layout,
        "phase": phase,
        "roots": records,
        "authority": False,
    }
    record["runtimeDeltaId"] = "runtime-delta-sha256:" + _sha256(_canonical(record))
    return record


def _validate_runtime_delta_record(
    run_root: Path,
    layout: str,
    phase: str,
    begin: Mapping[str, object],
    value: object,
) -> dict[str, object]:
    record = _record_id(
        value,
        "runtimeDeltaId",
        "runtime-delta-sha256:",
        "runtime delta",
    )
    if set(record) != {
        "schemaVersion", "kind", "runId", "layout", "phase", "roots",
        "authority", "runtimeDeltaId",
    } or (
        record.get("schemaVersion") != RUNTIME_POLICY_VERSION
        or record.get("kind") != "task-7B-runtime-delta"
        or record.get("runId") != Path(run_root).absolute().name
        or record.get("layout") != layout
        or record.get("phase") != phase
        or record.get("authority") is not False
        or type(record.get("roots")) is not list
    ):
        raise GateError("runtime delta record binding differs")
    expected_roots = _runtime_writable_roots(run_root, layout)
    if len(record["roots"]) != len(expected_roots):
        raise GateError("runtime delta root count differs")
    before: dict[str, object] = {}
    after: dict[str, object] = {}
    for expected_name, expected_path, row in zip(
        expected_roots,
        expected_roots.values(),
        record["roots"],
        strict=True,
    ):
        item = _exact_object(
            row,
            {"rootKind", "path", "before", "after", "changes"},
            f"runtime delta {expected_name}",
        )
        if item["rootKind"] != expected_name or item["path"] != str(expected_path):
            raise GateError("runtime delta root path/order differs")
        before[expected_name] = item["before"]
        after[expected_name] = item["after"]
    rebuilt = build_runtime_delta(run_root, layout, phase, before, after)
    if rebuilt != record or begin.get("writableBefore") != before:
        raise GateError("runtime delta does not bind the exact phase-begin snapshots")
    app_snapshot, _app_inventory = _runtime_snapshot(before["App"], "phase begin App")
    if begin.get("appBefore") != app_snapshot["inventory"]:
        raise GateError("runtime delta app baseline differs from phase begin")
    return record


def validate_runtime_outputs(
    run_root: Path,
    layout: str,
    phase: str,
    before: list[dict[str, object]],
    after: list[dict[str, object]],
    runtime_before: list[dict[str, object]],
    runtime_after: list[dict[str, object]],
) -> list[str]:
    if layout not in LAYOUTS or phase not in PHASES or _RUN.fullmatch(Path(run_root).name) is None:
        raise GateError("runtime output validation binding is invalid")
    previous = _inventory_map(before, "before")
    current = _inventory_map(after, "after")
    try:
        admit_paths(
            PlannedPath(
                f"gate-runtime-output:{layout}:{phase}",
                "preparation-wide",
                str((Path(run_root).absolute() / layout).joinpath(*item["path"].split("/"))),
            )
            for item in current.values()
        )
    except (OSError, PathBudgetError, ValueError) as error:
        raise GateError(f"runtime output path budget refused: {error}") from error
    if _inventory_map(runtime_before, "runtime before") != _inventory_map(runtime_after, "runtime after"):
        raise GateError("MO2/Python/mobase runtime identity drifted")
    for key, item in previous.items():
        if current.get(key) != item:
            raise GateError(f"pre-existing runtime entry changed: {item['path']}")
    additions = [item["path"] for key, item in current.items() if key not in previous]
    for relative in additions:
        lowered = relative.casefold()
        if "__pycache__" in lowered.split("/") or lowered.endswith((".pyc", ".pyo")):
            raise GateError("Python bytecode/cache output is forbidden")
        if not any(lowered.startswith(prefix.casefold()) for prefix in _ALLOWED_RUNTIME_PREFIXES):
            raise GateError(f"runtime output escaped declared disposable roots: {relative}")
    return []


def build_watch_request(
    run_root: Path,
    layout: str,
    phase: str,
    roots: Mapping[str, Path],
    evidence_root: Path,
    *,
    request_token: str,
    session_token: str,
    root_factory: Callable[[str, Path], WatchRoot] | None = None,
) -> WatchRequest:
    run_root = Path(run_root).absolute()
    evidence_root = Path(evidence_root).absolute()
    if layout not in LAYOUTS or phase not in PHASES or _RUN.fullmatch(run_root.name) is None:
        raise GateError("watch request run/layout/phase binding is invalid")
    if _HEX64.fullmatch(request_token) is None or _HEX64.fullmatch(session_token) is None:
        raise GateError("watch request and session tokens must be fresh SHA-256 values")
    if evidence_root != authority_run_root(run_root) / layout / "jobs" / phase / "watch":
        raise GateError("watch evidence path differs from the exact protected phase")
    if set(roots) != set(ROOT_KINDS):
        raise GateError("watch roots must contain all eight canonical kinds")
    if not evidence_root.is_dir() or any(evidence_root.iterdir()):
        raise GateError("watch evidence root must be fresh and empty")
    def stable_root(kind: str, path: Path) -> WatchRoot:
        identity = windows_exact_fs.identity_at_path(path)
        return WatchRoot(kind, path.absolute(), identity.volume_serial, identity.file_id)

    factory = root_factory or stable_root
    watch_roots = tuple(factory(kind, Path(roots[kind]).absolute()) for kind in ROOT_KINDS)
    with open_vault(authority_run_root(run_root)) as vault:
        vault.verify_descendant(evidence_root)
        authority_identity = vault.identity
        creator_sid = vault.creator_sid
    return WatchRequest(
        request_id="watch-request:" + request_token,
        session_id="watch-session:" + session_token,
        run_id="containment-run:" + run_root.name,
        scenario=ContainmentScenario.NEW_FOLDER,
        evidence_root=evidence_root,
        stop_token_path=evidence_root / STOP_NAME,
        roots=watch_roots,
        authority_root=authority_run_root(run_root),
        authority_volume_serial=authority_identity.volume_serial,
        authority_file_id=authority_identity.file_id,
        authority_creator_sid=creator_sid,
    )


def validate_watch_effect(
    receipt: WatchReceipt,
    before: ProtectedState,
    after: ProtectedState,
    effects: ContainmentEffects,
) -> None:
    if not watch_proves_unchanged(receipt, before, after):
        raise GateError("watcher or protected-state evidence does not prove unchanged")
    if not isinstance(effects, ContainmentEffects):
        raise GateError("service-authored effect receipt is required")
    if effects.watcher_pid != receipt.worker_pid or effects.mo2_pid is None:
        raise GateError("watcher/MO2 PID receipt does not bind the launch")


def require_distinct_phase_receipts(
    values: tuple[tuple[WatchReceipt, ContainmentEffects], ...],
) -> None:
    if type(values) is not tuple or not values:
        raise GateError("phase receipts are required")
    request_ids: set[str] = set()
    session_ids: set[str] = set()
    watcher_pids: set[int] = set()
    mo2_pids: set[int] = set()
    for receipt, effects in values:
        if not isinstance(receipt, WatchReceipt) or not isinstance(effects, ContainmentEffects):
            raise GateError("phase receipt entries are malformed")
        if effects.watcher_pid != receipt.worker_pid or effects.mo2_pid is None:
            raise GateError("phase receipt PID binding differs")
        for value, seen, label in (
            (receipt.request_id, request_ids, "request"),
            (receipt.session_id, session_ids, "session"),
            (receipt.worker_pid, watcher_pids, "watcher PID"),
            (effects.mo2_pid, mo2_pids, "MO2 PID"),
        ):
            if value in seen:
                raise GateError(f"a {label} was reused across launches")
            seen.add(value)


def validate_operator_evidence(
    phase: str,
    process: Mapping[str, object],
    window: Mapping[str, object],
    tool: Mapping[str, object] | None,
    close: Mapping[str, object],
    *,
    run_root: Path | None = None,
) -> None:
    if phase not in PHASES or phase == "Control" and tool is not None:
        raise GateError("operator phase/tool evidence is inconsistent")
    required_process = {"pid", "creationTime", "executable"}
    if set(process) != required_process or type(process["pid"]) is not int or process["pid"] <= 0:
        raise GateError("exact process identity is malformed")
    if set(window) != {"windowId", "app", "title", "screenshotId", "image", "nativeBefore", "nativeAfter"}:
        raise GateError("window observation fields are not exact")
    if (
        type(window["windowId"]) is not int
        or window["windowId"] <= 0
        or type(window["screenshotId"]) is not str
        or not window["screenshotId"]
        or type(window["title"]) is not str
        or not window["title"]
        or window["app"] != "process:" + str(process["executable"])
    ):
        raise GateError("visible window is not bound to the exact executable")
    native_fields = {"hwnd", "pid", "title", "className", "visible"}
    before = window["nativeBefore"]
    after = window["nativeAfter"]
    if (
        type(before) is not dict
        or type(after) is not dict
        or set(before) != native_fields
        or set(after) != native_fields
        or before != after
        or type(before["hwnd"]) is not int
        or before["hwnd"] <= 0
        or before["pid"] != process["pid"]
        or before["title"] != window["title"]
        or type(before["className"]) is not str
        or not before["className"]
        or before["visible"] is not True
    ):
        raise GateError("native HWND observations do not bracket the same visible process window")
    image = window["image"]
    if type(image) is not dict or set(image) != {"path", "sha256", "size"}:
        raise GateError("reviewable image reference is malformed")
    image_path = Path(str(image["path"])).absolute()
    if not image_path.is_file():
        raise GateError("reviewable image is missing")
    image_data = (read_exact(image_path, maximum_bytes=MAX_SCREENSHOT_BYTES) if run_root is None
                  else _read_gate_evidence(run_root, image_path, maximum_bytes=MAX_SCREENSHOT_BYTES))
    if image.get("size") != len(image_data) or image.get("sha256") != _sha256(image_data):
        raise GateError("reviewable image bytes differ from the observation")
    _validate_png_bytes(image_data)
    if phase != "Control":
        if (
            type(tool) is not dict
            or set(tool) != {"loadedTool", "windowId", "screenshotId", "image"}
            or tool.get("loadedTool") != "ModLab Capability Probe"
        ):
            raise GateError("visible ModLab probe evidence is required")
        if (
            tool.get("windowId") != window["windowId"]
            or tool.get("screenshotId") != window["screenshotId"]
            or tool.get("image") != image
        ):
            raise GateError("tool observation is not bound to the visible window")
    if set(close) != {"action", "windowId", "returned", "remainingMatches", "exitCode"}:
        raise GateError("normal-close evidence fields are not exact")
    if (
        close["windowId"] != window["windowId"]
        or close["returned"] is not True
        or close["remainingMatches"] != []
        or close["exitCode"] != 0
        or close["action"] not in {"Alt+F4", "Close button", "File/Exit"}
    ):
        raise GateError("MO2 was not proven to close normally and completely")


def _publish_guarded_marker(session: object, loaded: object) -> Mapping[str, object] | None:
    job = Path(getattr(session, "job_root", "")).absolute()
    target = job / "guarded.json"
    phase = getattr(session, "phase", None)
    if phase != "Guarded":
        if target.exists():
            raise GateError("guarded evidence exists outside the Guarded phase")
        return None
    try:
        runtime_capability._loaded_format(loaded)
    except runtime_capability.CapabilityError as error:
        raise GateError("Guarded marker loaded observation is malformed") from error
    launch = getattr(session, "launch", None)
    if (
        not isinstance(loaded, Mapping)
        or launch is None
        or type(getattr(launch, "pid", None)) is not int
        or loaded.get("pid") != launch.pid
        or loaded.get("runId") != Path(getattr(session, "run_root", "")).name
        or loaded.get("layout") != getattr(session, "layout", None)
        or loaded.get("phase") != "Guarded"
        or loaded.get("nonce") != getattr(session, "nonce", None)
    ):
        raise GateError("Guarded marker is not bound to the exact loaded observation")
    containment_service._current_effects().write(target)
    _publish_gate_json(session.run_root, target, loaded)
    exact = _load_gate_json(session.run_root, target)
    if exact != loaded:
        raise GateError("Guarded marker exact reload differs")
    return exact


def _validate_observation_and_publish_guarded(
    session: object,
    observation: dict[str, object],
) -> Mapping[str, object]:
    if getattr(session, "phase", None) != "Guarded" or type(observation) is not dict:
        raise GateError("Guarded publication requires the exact mutable observation")
    if observation.get("guarded") is not None or observation.get("loaded") is None:
        raise GateError("Guarded publication requires one uncommitted loaded observation")
    candidate = {
        "layout": getattr(session, "layout", None),
        "appRoot": str(Path(getattr(session, "app_root", ""))),
    }
    matrix = {
        "schemaVersion": 3,
        "authorityRoot": str(authority_run_root(session.run_root)),
        "root": str(Path(getattr(session, "run_root", ""))),
        "runId": Path(getattr(session, "run_root", "")).name,
    }

    def validate() -> None:
        runtime_capability._observation(
            observation,
            candidate,
            matrix,
            PHASES.index("Guarded"),
        )
        runtime_capability._verify_raw_evidence(
            {"schemaVersion": 3, "authorityRoot": str(authority_run_root(session.run_root)), "candidates": [{"control": observation, "observations": []}]}
        )

    # First prove the complete launch/process/runtime observation and the exact
    # retained log marker while the authority-bearing marker is still absent.
    validate()
    guarded = _publish_guarded_marker(session, observation["loaded"])
    if guarded != observation["loaded"]:
        raise GateError("published Guarded marker differs from the validated loaded observation")
    observation["guarded"] = guarded
    # Reconstruct the same full observation with the newly published immutable
    # marker so its bytes are also covered by the final raw-evidence proof.
    validate()
    return guarded


def build_effect_record(scope: str, effects: ContainmentEffects) -> dict[str, object]:
    if type(scope) is not str or not scope or not isinstance(effects, ContainmentEffects):
        raise GateError("effect record requires an exact scope and service receipt")
    body = {
        "schemaVersion": EFFECT_POLICY_VERSION,
        "kind": "task-7B-service-effect-receipt",
        "scope": scope,
        "writtenPaths": [str(path) for path in effects.written_paths],
        "childMutationRoots": [str(path) for path in effects.child_mutation_roots],
        "watcherPid": effects.watcher_pid,
        "mo2Pid": effects.mo2_pid,
        "sourceChanges": list(effects.source_changes),
        "gameChanges": list(effects.game_changes),
        "productionMo2Changes": list(effects.production_mo2_changes),
        "preparationProcesses": list(effects.preparation_processes),
        "authority": False,
    }
    body["effectId"] = "effect-sha256:" + _sha256(_canonical(body))
    return body


def validate_effect_record(value: object) -> Mapping[str, object]:
    if type(value) is not dict:
        raise GateError("effect record is not an exact object")
    expected = {
        "schemaVersion", "kind", "scope", "writtenPaths", "childMutationRoots",
        "watcherPid", "mo2Pid", "sourceChanges", "gameChanges",
        "productionMo2Changes", "preparationProcesses", "authority", "effectId",
    }
    if set(value) != expected or value.get("authority") is not False:
        raise GateError("effect record fields are not exact")
    effect_id = value.get("effectId")
    body = {key: item for key, item in value.items() if key != "effectId"}
    if effect_id != "effect-sha256:" + _sha256(_canonical(body)):
        raise GateError("effect record ID differs from its exact bytes")
    try:
        reconstructed = ContainmentEffects(
            written_paths=tuple(Path(item) for item in value["writtenPaths"]),
            child_mutation_roots=tuple(Path(item) for item in value["childMutationRoots"]),
            watcher_pid=value["watcherPid"],
            mo2_pid=value["mo2Pid"],
            source_changes=tuple(value["sourceChanges"]),
            game_changes=tuple(value["gameChanges"]),
            production_mo2_changes=tuple(value["productionMo2Changes"]),
            preparation_processes=tuple(value["preparationProcesses"]),
        )
    except (TypeError, ValueError) as error:
        raise GateError(f"effect record body is malformed: {error}") from error
    if build_effect_record(str(value["scope"]), reconstructed) != value:
        raise GateError("effect record is not canonical")
    return value


def require_complete_matrix(matrix: Mapping[str, object]) -> Mapping[str, object]:
    candidates = matrix.get("candidates") if isinstance(matrix, Mapping) else None
    if type(candidates) is not list or len(candidates) != 2:
        raise GateError("both complete candidate matrices are required")
    for expected, candidate in zip(LAYOUTS, candidates, strict=True):
        if (
            type(candidate) is not dict
            or candidate.get("layout") != expected
            or type(candidate.get("control")) is not dict
            or type(candidate.get("observations")) is not list
            or len(candidate["observations"]) != 4
            or any(type(item) is not dict for item in candidate["observations"])
        ):
            raise GateError("candidate matrix is incomplete or out of order")
    return matrix


def derive_selection(
    matrix: Mapping[str, object],
    *,
    selector: Callable[[Mapping[str, object]], Mapping[str, object]] = select_capability,
) -> Mapping[str, object]:
    """Delegate verdict derivation to the strict product V2 selector."""
    complete = require_complete_matrix(matrix)
    if (set(complete) != runtime_capability.PROTECTED_MATRIX_FIELDS or complete.get("schemaVersion") != 3
            or type(complete.get("root")) is not str or type(complete.get("authorityRoot")) is not str
            or complete.get("authorityRoot") != str(authority_run_root(Path(complete["root"])))
            or complete.get("runId") != Path(complete["root"]).name):
        raise GateError("new gate selection requires exact schema-3 paired root bindings")
    for candidate in complete["candidates"]:
        control = candidate["control"]
        if isinstance(control, Mapping) and control.get("loaded") is not None:
            raise GateError("Control cannot carry loaded runtime evidence")
        for observation in candidate["observations"]:
            loaded = observation.get("loaded") if isinstance(observation, Mapping) else None
            if not isinstance(loaded, Mapping) or loaded.get("bytecodeDisabled") is not False:
                raise GateError("every candidate launch must prove bytecodeDisabled=false")
    try:
        selected = selector(complete)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise GateError(f"strict capability selection refused: {error}") from error
    capability_id = selected.get("capabilityId") if isinstance(selected, Mapping) else None
    if (
        selected.get("selection") not in (*LAYOUTS, "NotSupported")
        or not isinstance(capability_id, str)
        or not capability_id.startswith("mo2-runtime-capability-sha256:")
        or _HEX64.fullmatch(capability_id.removeprefix("mo2-runtime-capability-sha256:")) is None
        or any(key in selected for key in ("authority", "bridgeReceiptId", "bridgeConsumptionPermitted"))
    ):
        raise GateError("strict selector returned malformed or authority-bearing output")
    return selected


def _phase_native_ownership(error):
    """Recover retained native ownership through the gate/service cause wrappers."""
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        ownership = getattr(error, "ownership", error)
        if isinstance(ownership, windows_exact_fs.ExactObjectOwnershipError):
            return ownership
        error = getattr(error, "cause", None) or error.__cause__
    return None


def run_operator_phase(
    run_root: Path,
    layout: str,
    phase: str,
    *,
    backend: object,
    exchange: object,
) -> ContainmentServiceResult:
    """Own one watcher/MO2 phase from launch through immutable finalization.

    The production backend keeps the watcher session in this controller process.
    ``exchange`` is the sole seam through which root supplies real native-window
    and normal-close observations while this call remains alive.
    """
    root = Path(run_root).absolute()
    if layout not in LAYOUTS or phase not in PHASES or _RUN.fullmatch(root.name) is None:
        raise GateError("operator phase binding is invalid")
    effects = ContainmentEffects()
    session = None
    try:
        backend.source_check(root)
        state = backend.load_state(root, layout)
        expected = next_action(tuple(state.completed), installed=bool(state.installed))
        if expected != phase:
            raise GateError(f"durable phase state requires {expected}, not {phase}")
        begun = backend.begin_phase(root, layout, phase)
        if not isinstance(begun, ContainmentServiceResult):
            raise GateError("phase begin did not return a service effect receipt")
        session = begun.value
        effects = ContainmentEffects.merged(effects, begun.effects)
        native_before = backend.native(session, "native-before")
        window = exchange.window(session)
        native_after = backend.native(session, "native-after")
        close = exchange.close(session, window)
        finalized = backend.finalize_phase(
            session,
            native_before,
            window,
            native_after,
            close,
        )
        if not isinstance(finalized, ContainmentServiceResult):
            raise GateError("phase finalization did not return a service effect receipt")
        effects = ContainmentEffects.merged(effects, finalized.effects)
        published = backend.publish_phase(session, finalized.value, effects)
        if isinstance(published, ContainmentServiceResult):
            effects = ContainmentEffects.merged(effects, published.effects)
            record = published.value
        else:
            record = published
        if not isinstance(record, Mapping):
            raise GateError("phase publication is malformed")
        return ContainmentServiceResult(record, effects)
    except BaseException as error:
        partial = getattr(error, "effects", None)
        if isinstance(partial, ContainmentEffects):
            effects = ContainmentEffects.merged(effects, partial)
        try:
            failure_effects = backend.fail_phase(root, layout, phase, error, effects, session)
            if isinstance(failure_effects, ContainmentEffects):
                effects = ContainmentEffects.merged(effects, failure_effects)
        except BaseException as failure_error:
            partial = getattr(failure_error, "effects", None)
            if isinstance(partial, ContainmentEffects):
                effects = ContainmentEffects.merged(effects, partial)
            primary = error.cause if isinstance(error, containment_service.ContainmentOperationError) and error.cause is not None else error
            secondary_ownership = _phase_native_ownership(failure_error)
            if isinstance(secondary_ownership, windows_exact_fs.ExactObjectOwnershipError):
                combined = windows_exact_fs.union_retained_ownership(
                    f"phase and failure publication retain original handles: {failure_error}",
                    prior=_phase_native_ownership(error) or primary, owners=secondary_ownership.owners)
                combined.owner = containment_service._process_owner_from_error(error) or containment_service._process_owner_from_error(failure_error)
                combined.effects = effects
                raise combined from error
            error.add_note(f"terminal phase failure publication also failed: {failure_error}")
        if isinstance(error, GateError):
            error.effects = effects
            error.owner = containment_service._process_owner_from_error(error)
            error.ownership = _phase_native_ownership(error)
            raise
        wrapped = GateError(str(error) or type(error).__name__)
        wrapped.effects = effects
        wrapped.ownership = _phase_native_ownership(error)
        wrapped.owner = containment_service._process_owner_from_error(error)
        raise wrapped from error


def install_operator_candidate(
    run_root: Path,
    layout: str,
    control_id: str,
    *,
    backend: object | None = None,
) -> ContainmentServiceResult:
    """Install one candidate only after the exact durable Control observation."""
    root = Path(run_root).absolute()
    if (
        layout not in LAYOUTS
        or _RUN.fullmatch(root.name) is None
        or type(control_id) is not str
        or not control_id.startswith("observation-sha256:")
        or _HEX64.fullmatch(control_id.removeprefix("observation-sha256:")) is None
    ):
        raise GateError("candidate installation binding is invalid")
    selected = ProductionLiveBackend() if backend is None else backend
    effects = ContainmentEffects()
    try:
        selected.source_check(root)
        if selected.load_state(root, layout) != DurableLayoutState(("Control",), False):
            raise GateError("candidate installation requires exactly completed Control")
        received = selected.install_candidate(root, layout, control_id)
        if not isinstance(received, ContainmentServiceResult):
            raise GateError("candidate installation did not return a service effect receipt")
        effects = ContainmentEffects.merged(effects, received.effects)
        published = selected.publish_install(root, layout, received.value, effects)
        if isinstance(published, ContainmentServiceResult):
            effects = ContainmentEffects.merged(effects, published.effects)
            record = published.value
        else:
            record = published
        if not isinstance(record, Mapping):
            raise GateError("candidate installation publication is malformed")
        return ContainmentServiceResult(record, effects)
    except BaseException as error:
        partial = getattr(error, "effects", None)
        if isinstance(partial, ContainmentEffects):
            effects = ContainmentEffects.merged(effects, partial)
        try:
            failure_effects = selected.fail_install(root, layout, control_id, error, effects)
            if isinstance(failure_effects, ContainmentEffects):
                effects = ContainmentEffects.merged(effects, failure_effects)
        except BaseException as failure_error:
            partial = getattr(failure_error, "effects", None)
            if isinstance(partial, ContainmentEffects):
                effects = ContainmentEffects.merged(effects, partial)
            failure_ownership = getattr(failure_error, "ownership", failure_error)
            if isinstance(failure_ownership, windows_exact_fs.ExactObjectOwnershipError):
                primary = error.cause if isinstance(error, containment_service.ContainmentOperationError) and error.cause is not None else error
                ownership = windows_exact_fs.union_retained_ownership(
                    f"installation and failure publication retain original handles: {failure_error}",
                    prior=getattr(primary, "ownership", primary), owners=failure_ownership.owners,
                )
                ownership.effects = effects
                raise ownership from error
            if hasattr(error, "add_note"):
                error.add_note(f"terminal installation failure publication also failed: {failure_error}")
        error.effects = effects
        raise


def _load_restart_record(run_root: Path) -> Mapping[str, object]:
    root = Path(run_root).absolute()
    value = _record_id(
        _load_gate_json(root, authority_run_root(root) / "restart.json"),
        "restartId",
        "failed-attempt-restart-sha256:",
        "failed attempt restart record",
    )
    fields = {
        "schemaVersion", "kind", "failedRunId", "failedRunRoot", "newRunId",
        "failedPreparationId", "preparationId", "failureIds", "cleanupIds",
        "bootstrapJobIds", "createdAt", "terminal", "priorRunReusable",
        "effect", "authority", "restartId",
    }
    failed_root_text = value.get("failedRunRoot")
    failed_root = Path(failed_root_text).absolute() if type(failed_root_text) is str else None
    jobs = value.get("bootstrapJobIds")
    if (
        set(value) != fields
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != "task-7B-fresh-attempt-after-cleanup"
        or value.get("newRunId") != root.name
        or value.get("failedRunId") == root.name
        or _RUN.fullmatch(str(value.get("failedRunId"))) is None
        or failed_root is None
        or str(failed_root) != failed_root_text
        or failed_root.name != value.get("failedRunId")
        or failed_root == root
        or value.get("terminal") is not False
        or value.get("priorRunReusable") is not False
        or value.get("authority") is not False
        or type(value.get("failureIds")) is not list
        or not value["failureIds"]
        or type(value.get("cleanupIds")) is not list
        or len(value["cleanupIds"]) != len(value["failureIds"])
        or type(jobs) is not dict
        or set(jobs) != set(LAYOUTS)
        or any(_JOB.fullmatch(str(jobs.get(layout))) is None for layout in LAYOUTS)
        or len(set(jobs.values())) != len(LAYOUTS)
        or type(value.get("createdAt")) is not str
        or not value["createdAt"]
    ):
        raise GateError("failed attempt restart record binding differs")
    old_preparation = _load_preparation(failed_root)
    if value.get("failedPreparationId") != old_preparation.get("preparationId"):
        raise GateError("failed attempt restart old preparation differs")
    failures = [
        _load_phase_failure(failed_root, layout, phase)
        for layout in LAYOUTS
        for phase in PHASES
        if (authority_run_root(failed_root) / layout / "jobs" / phase / "failure.json").is_file()
    ]
    if not failures or value["failureIds"] != [item["failureId"] for item in failures]:
        raise GateError("failed attempt restart failure sequence differs")
    cleanups = [
        _load_phase_cleanup(failed_root, str(failure["layout"]), str(failure["phase"]))
        for failure in failures
    ]
    if value["cleanupIds"] != [item["cleanupId"] for item in cleanups]:
        raise GateError("failed attempt restart cleanup sequence differs")
    preparation = _load_preparation(root)
    if (
        value.get("preparationId") != preparation["preparationId"]
        or jobs != preparation.get("bootstrapJobIds")
    ):
        raise GateError("fresh attempt restart preparation differs")
    old_jobs = old_preparation.get("bootstrapJobIds")
    if (
        type(old_jobs) is not dict
        or set(old_jobs) != set(LAYOUTS)
        or any(_JOB.fullmatch(str(old_jobs.get(layout))) is None for layout in LAYOUTS)
        or set(old_jobs.values()) & set(jobs.values())
    ):
        raise GateError("fresh attempt reuses an old bootstrap job identity")
    effect = validate_effect_record(value["effect"])
    if (
        effect.get("scope") != "Restart"
        or effect.get("writtenPaths") != [str(root), str(authority_run_root(root) / "restart.json")]
        or effect.get("childMutationRoots") != [str(root)]
        or effect.get("watcherPid") is not None
        or effect.get("mo2Pid") is not None
        or effect.get("sourceChanges") != []
        or effect.get("gameChanges") != []
        or effect.get("productionMo2Changes") != []
        or effect.get("preparationProcesses") != []
    ):
        raise GateError("fresh attempt restart effect differs")
    return value


def _publish_restart_record(
    failed_root: Path,
    new_config: GateConfig,
    failed_preparation_id: str,
    preparation_id: str,
    failures: list[Mapping[str, object]],
    cleanups: list[Mapping[str, object]],
) -> Mapping[str, object]:
    target = new_config.authority_root / "restart.json"
    ledger = containment_service._current_effects()
    ledger.child_mutation_root(new_config.run_root)
    ledger.write(target)
    effect = build_effect_record("Restart", ledger.freeze())
    record = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "task-7B-fresh-attempt-after-cleanup",
        "failedRunId": failed_root.name,
        "failedRunRoot": str(failed_root),
        "newRunId": new_config.run_root.name,
        "failedPreparationId": failed_preparation_id,
        "preparationId": preparation_id,
        "failureIds": [item["failureId"] for item in failures],
        "cleanupIds": [item["cleanupId"] for item in cleanups],
        "bootstrapJobIds": dict(new_config.bootstrap_job_ids),
        "createdAt": _now(),
        "terminal": False,
        "priorRunReusable": False,
        "effect": effect,
        "authority": False,
    }
    record["restartId"] = "failed-attempt-restart-sha256:" + _sha256(_canonical(record))
    _publish_gate_json(new_config.run_root, target, record)
    exact = _load_restart_record(new_config.run_root)
    if exact != record:
        raise GateError("fresh attempt restart exact reload differs")
    return exact


def restart_failed_attempt(
    failed_run_root: Path,
    new_config: GateConfig,
    *,
    live_backend: ProductionLiveBackend | None = None,
    preparation_backend: object | None = None,
) -> ContainmentServiceResult:
    failed_root = Path(failed_run_root).absolute()
    if (
        _RUN.fullmatch(failed_root.name) is None
        or not isinstance(new_config, GateConfig)
        or new_config.run_root == failed_root
        or new_config.run_root.exists()
    ):
        raise GateError("restart requires one distinct completely fresh run root")
    old_preparation = _fresh_preparation_preflight(failed_root)
    failures = [
        _load_phase_failure(failed_root, layout, phase)
        for layout in LAYOUTS
        for phase in PHASES
        if (authority_run_root(failed_root) / layout / "jobs" / phase / "failure.json").is_file()
    ]
    if not failures:
        raise GateError("restart requires at least one immutable failed phase")
    selected = ProductionLiveBackend() if live_backend is None else live_backend
    cleanups: list[Mapping[str, object]] = []
    effects = ContainmentEffects()
    for failure in failures:
        received = selected.cleanup_failed_phase(
            failed_root,
            str(failure["layout"]),
            str(failure["phase"]),
        )
        if not isinstance(received, ContainmentServiceResult):
            raise GateError("failed phase cleanup lacks a service receipt")
        cleanups.append(received.value)
        effects = ContainmentEffects.merged(effects, received.effects)
    _require_process_absence()
    old_job_ids = {
        str(record["bootstrap"]["receipt"]["jobId"])
        for record in old_preparation["layouts"]
    }
    if old_job_ids & set(new_config.bootstrap_job_ids.values()):
        raise GateError("fresh attempt reuses an old bootstrap job identity")
    prepared = prepare_gate(new_config, backend=preparation_backend)
    effects = ContainmentEffects.merged(effects, prepared.effects)
    published = containment_service._receipted(_publish_restart_record)(
        failed_root,
        new_config,
        str(old_preparation["preparationId"]),
        str(prepared.value["preparationId"]),
        failures,
        cleanups,
    )
    effects = ContainmentEffects.merged(effects, published.effects)
    return ContainmentServiceResult(published.value, effects)


def _ids(values: tuple[str, ...], prefix: str, label: str) -> list[str]:
    if type(values) is not tuple or len(values) != 10:
        raise GateError(f"exactly ten {label} IDs are required")
    if any(type(value) is not str or not value.startswith(prefix) or _HEX64.fullmatch(value[len(prefix):]) is None for value in values):
        raise GateError(f"{label} IDs are malformed")
    if len(set(values)) != 10:
        raise GateError(f"{label} IDs must all be fresh")
    return list(values)


def build_envelope(
    *,
    run_id: str,
    source: Mapping[str, str],
    preparation_id: str,
    phase_ids: tuple[str, ...],
    watch_outcome_ids: tuple[str, ...],
    effect_ids: tuple[str, ...],
    selection: Mapping[str, object],
) -> dict[str, object]:
    if _RUN.fullmatch(run_id) is None:
        raise GateError("envelope run identity is invalid")
    if set(source) != {"commit", "tree", "artifactId"} or any(
        _HEX40.fullmatch(source[name]) is None for name in ("commit", "tree")
    ) or not str(source["artifactId"]).startswith("containment-source-artifact-sha256:"):
        raise GateError("envelope source identity is incomplete")
    if not preparation_id.startswith("preparation-sha256:") or _HEX64.fullmatch(preparation_id.removeprefix("preparation-sha256:")) is None:
        raise GateError("preparation identity is malformed")
    def contains_authority(value: object) -> bool:
        if isinstance(value, Mapping):
            if any(key in value for key in ("authority", "bridgeReceiptId", "bridgeConsumptionPermitted")):
                return True
            return any(contains_authority(item) for item in value.values())
        if isinstance(value, (list, tuple)):
            return any(contains_authority(item) for item in value)
        return False

    try:
        selection_bytes = capability_to_bytes(selection)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise GateError(f"product-valid derived selection is required: {error}") from error
    if (
        contains_authority(selection)
        or selection.get("schemaVersion") != 3
        or selection.get("runId") != run_id
        or selection.get("source") != {key: source[key] for key in ("commit", "tree")}
    ):
        raise GateError("derived selection run/source binding differs")
    selection_id = selection.get("capabilityId")
    if (
        type(selection_id) is not str
        or not selection_id.startswith("mo2-runtime-capability-sha256:")
        or _HEX64.fullmatch(selection_id.removeprefix("mo2-runtime-capability-sha256:")) is None
    ):
        raise GateError("derived selection identity is malformed")
    body = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "task-7B-full-stack-envelope",
        "runId": run_id,
        "source": dict(source),
        "preparationId": preparation_id,
        "phaseIds": _ids(phase_ids, "observation-sha256:", "phase"),
        "watchOutcomeIds": _ids(watch_outcome_ids, "watch-outcome-sha256:", "watch outcome"),
        "effectIds": _ids(effect_ids, "effect-sha256:", "effect"),
        "selectionId": selection_id,
        "selectionSha256": _sha256(selection_bytes),
        "selection": _json_value(selection),
        "policies": {
            "harness": HARNESS_POLICY_VERSION,
            "effect": EFFECT_POLICY_VERSION,
            "publication": PUBLICATION_POLICY_VERSION,
            "protocol": PROTOCOL_VERSION,
            "runtime": RUNTIME_POLICY_VERSION,
            "fixture": FIXTURE_VERSION,
        },
        "authority": False,
        "bridgeConsumptionPermitted": False,
    }
    body["envelopeId"] = "full-stack-envelope-sha256:" + _sha256(_canonical(body))
    return body


def load_envelope(path: Path) -> Mapping[str, object]:
    """Rebuild every final binding from durable product and phase evidence."""
    target = Path(path).absolute()
    root = capability_run_root(target.parent.name)
    if target != authority_run_root(root) / "full-stack-envelope.json" or _RUN.fullmatch(root.name) is None:
        raise GateError("full-stack envelope path/run binding is invalid")
    value = _load_gate_json(root, target)
    fields = {
        "schemaVersion", "kind", "runId", "source", "preparationId",
        "phaseIds", "watchOutcomeIds", "effectIds", "selectionId",
        "selectionSha256", "selection", "policies", "authority",
        "bridgeConsumptionPermitted", "envelopeId",
    }
    if type(value) is not dict or set(value) != fields:
        raise GateError("full-stack envelope fields are not exact")
    body = {key: item for key, item in value.items() if key != "envelopeId"}
    if (
        value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != "task-7B-full-stack-envelope"
        or value.get("runId") != root.name
        or value.get("authority") is not False
        or value.get("bridgeConsumptionPermitted") is not False
        or value.get("envelopeId")
        != "full-stack-envelope-sha256:" + _sha256(_canonical(body))
    ):
        raise GateError("full-stack envelope identity or non-authority binding differs")
    expected_policies = {
        "harness": HARNESS_POLICY_VERSION,
        "effect": EFFECT_POLICY_VERSION,
        "publication": PUBLICATION_POLICY_VERSION,
        "protocol": PROTOCOL_VERSION,
        "runtime": RUNTIME_POLICY_VERSION,
        "fixture": FIXTURE_VERSION,
    }
    if value.get("policies") != expected_policies:
        raise GateError("full-stack envelope policy binding differs")

    preparation = _load_preparation(root)
    expected_source = {
        **dict(preparation["source"]),
        "artifactId": preparation["sourceArtifactId"],
    }
    if (
        value.get("source") != expected_source
        or value.get("preparationId") != preparation["preparationId"]
    ):
        raise GateError("full-stack envelope source/preparation binding differs")

    selection_path = authority_run_root(root) / "selection.json"
    selection_bytes = _read_gate_evidence(root, selection_path)
    try:
        selection = load_capability(selection_path, authority_root=authority_run_root(root))
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise GateError(f"durable capability selection is invalid: {error}") from error
    if (
        selection_bytes != capability_to_bytes(selection)
        or value.get("selection") != selection
        or value.get("selectionId") != selection.get("capabilityId")
        or value.get("selectionSha256") != _sha256(selection_bytes)
        or selection.get("runId") != root.name
        or selection.get("root") != str(root)
        or selection.get("source") != preparation["source"]
        or selection.get("archive") != {
            "sha256": preparation["archive"]["sha256"],
            "size": preparation["archive"]["size"],
        }
    ):
        raise GateError("full-stack envelope selection bytes/content binding differs")

    candidates: list[dict[str, object]] = []
    phase_ids: list[str] = []
    watch_ids: list[str] = []
    effect_ids: list[str] = []
    receipts: list[tuple[int, int, str, str]] = []
    for layout in LAYOUTS:
        installation = _load_installation(root, layout)
        declared = installation.get("declared")
        if type(declared) is not list:
            raise GateError("durable candidate installation is malformed")
        observations: list[Mapping[str, object]] = []
        for phase in PHASES:
            observation, bundle = _load_validated_phase_bundle(
                root,
                layout,
                phase,
                str(preparation["preparationId"]),
            )
            observations.append(observation)
            phase_ids.append(str(bundle["observationId"]))
            watch_ids.append(str(bundle["watchOutcomeId"]))
            effect_ids.append(str(bundle["effectId"]))
            receipts.append((
                int(bundle["watcherPid"]),
                int(bundle["mo2Pid"]),
                str(bundle["requestId"]),
                str(bundle["sessionId"]),
            ))
        candidates.append({
            "layout": layout,
            "appRoot": str(root / layout / "app"),
            "declared": declared,
            "control": observations[0],
            "observations": observations[1:],
        })
    if len(receipts) != 10 or any(
        len({receipt[index] for receipt in receipts}) != 10 for index in range(4)
    ):
        raise GateError("durable watcher/MO2/request/session identities are not all fresh")
    expected_selection = derive_selection({
        "schemaVersion": 3,
        "runId": root.name,
        "root": str(root),
        "authorityRoot": str(authority_run_root(root)),
        "source": dict(preparation["source"]),
        "archive": {
            "sha256": preparation["archive"]["sha256"],
            "size": preparation["archive"]["size"],
        },
        "candidates": candidates,
    })
    if selection != expected_selection:
        raise GateError("durable selection differs from the reconstructed ten-phase matrix")
    expected_phase = _ids(tuple(phase_ids), "observation-sha256:", "phase")
    expected_watch = _ids(tuple(watch_ids), "watch-outcome-sha256:", "watch outcome")
    expected_effect = _ids(tuple(effect_ids), "effect-sha256:", "effect")
    if (
        value.get("phaseIds") != expected_phase
        or value.get("watchOutcomeIds") != expected_watch
        or value.get("effectIds") != expected_effect
    ):
        raise GateError("full-stack envelope phase/watch/effect binding differs")
    return value


def _run_argument(value: str) -> str:
    if _RUN.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("run ID must be exactly 32 lowercase hexadecimal characters")
    return value


def _observation_argument(value: str) -> str:
    prefix = "observation-sha256:"
    if not value.startswith(prefix) or _HEX64.fullmatch(value[len(prefix):]) is None:
        raise argparse.ArgumentTypeError("Control ID must be one observation-sha256 identity")
    return value


def _parse_cli(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="task-7B-live-gate-harness.py",
        description="Disposable non-authorizing MO2 full-stack capability gate",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare", help="prepare both fresh disposable layouts")
    prepare.add_argument("--run-id", required=True, type=_run_argument)
    prepare.add_argument("--archive", required=True, type=Path)
    prepare.add_argument("--steam-root", required=True, type=Path)

    install = commands.add_parser("install", help="install a candidate after exact Control")
    install.add_argument("--run-id", required=True, type=_run_argument)
    install.add_argument("layout", choices=LAYOUTS)
    install.add_argument("--control-id", required=True, type=_observation_argument)

    phase = commands.add_parser("phase", help="run one long-lived watcher/MO2 phase")
    phase.add_argument("--run-id", required=True, type=_run_argument)
    phase.add_argument("layout", choices=LAYOUTS)
    phase.add_argument("phase", choices=PHASES)

    cleanup = commands.add_parser("cleanup", help="quiesce one immutable failed phase only")
    cleanup.add_argument("--run-id", required=True, type=_run_argument)
    cleanup.add_argument("layout", choices=LAYOUTS)
    cleanup.add_argument("phase", choices=PHASES)

    restart = commands.add_parser("restart", help="clean a failed run and prepare one fresh run")
    restart.add_argument("--run-id", required=True, type=_run_argument)
    restart.add_argument("--new-run-id", required=True, type=_run_argument)
    restart.add_argument("--archive", required=True, type=Path)
    restart.add_argument("--steam-root", required=True, type=Path)

    finalize = commands.add_parser("finalize", help="derive the non-authorizing capability result")
    finalize.add_argument("--run-id", required=True, type=_run_argument)

    status = commands.add_parser("status", help="read the immutable progress state")
    status.add_argument("--run-id", required=True, type=_run_argument)
    return parser.parse_args(arguments)


def _write_cli_result(value: object, *, stream=None) -> None:
    output = sys.stdout if stream is None else stream
    output.write(json.dumps(_json_value(value), sort_keys=True, separators=(",", ":")) + "\n")
    output.flush()


def _cli_main(arguments: list[str] | None = None) -> int:
    options = _parse_cli(arguments)
    root = capability_run_root(options.run_id)
    if options.command == "prepare":
        jobs = {layout: "bootstrap-job:" + secrets.token_hex(16) for layout in LAYOUTS}
        received = prepare_gate(
            GateConfig(root, options.archive, options.steam_root, _git_source(), jobs,
                       authority_root=authority_run_root(root))
        )
        _write_cli_result(
            {
                "command": "prepare",
                "runRoot": str(root),
                "preparationId": received.value["preparationId"],
                "effect": build_effect_record("Preparation", received.effects),
            }
        )
        return 0
    if options.command == "install":
        received = install_operator_candidate(root, options.layout, options.control_id)
        _write_cli_result(
            {
                "command": "install",
                "installationId": received.value["installationId"],
                "effect": build_effect_record(f"{options.layout}:Install", received.effects),
            }
        )
        return 0
    if options.command == "phase":
        received = run_operator_phase(
            root,
            options.layout,
            options.phase,
            backend=ProductionLiveBackend(),
            exchange=JsonLineExchange(),
        )
        _write_cli_result(
            {
                "command": "phase",
                "phaseFinal": received.value,
                "effect": build_effect_record(f"{options.layout}:{options.phase}", received.effects),
            }
        )
        return 0
    if options.command == "cleanup":
        received = ProductionLiveBackend().cleanup_failed_phase(
            root,
            options.layout,
            options.phase,
        )
        _write_cli_result({"command": "cleanup", **received.value})
        return 0
    if options.command == "restart":
        jobs = {layout: "bootstrap-job:" + secrets.token_hex(16) for layout in LAYOUTS}
        new_root = capability_run_root(options.new_run_id)
        received = restart_failed_attempt(
            root,
            GateConfig(new_root, options.archive, options.steam_root, _git_source(), jobs,
                       authority_root=authority_run_root(new_root)),
        )
        _write_cli_result({"command": "restart", **received.value})
        return 0
    if options.command == "finalize":
        received = ProductionLiveBackend().finalize_gate(root)
        _write_cli_result(
            {
                "command": "finalize",
                **received.value,
                "effect": build_effect_record("Finalize", received.effects),
            }
        )
        return 0
    preparation = _load_preparation(root)
    backend = ProductionLiveBackend()
    layouts = []
    for layout in LAYOUTS:
        state = backend.load_state(root, layout)
        layouts.append(
            {
                "layout": layout,
                "completed": list(state.completed),
                "installed": state.installed,
                "next": next_action(state.completed, installed=state.installed),
            }
        )
    _write_cli_result(
        {
            "command": "status",
            "runId": root.name,
            "preparationId": preparation["preparationId"],
            "layouts": layouts,
            "authority": False,
        }
    )
    return 0


__all__ = [
    "GateConfig",
    "GateError",
    "GatePathAdmission",
    "JsonLineExchange",
    "MAX_RETAINED_LOGS",
    "ProductionBackend",
    "ProductionLiveBackend",
    "admit_gate_paths",
    "build_envelope",
    "build_effect_record",
    "build_watch_request",
    "child_environment",
    "derive_selection",
    "install_operator_candidate",
    "load_envelope",
    "load_exact_json",
    "next_action",
    "prepare_gate",
    "publish_exact_json",
    "relocate_created_app",
    "run_operator_phase",
    "require_complete_matrix",
    "require_distinct_phase_receipts",
    "validate_operator_evidence",
    "validate_effect_record",
    "validate_runtime_outputs",
    "validate_watch_effect",
]


if __name__ == "__main__":
    try:
        raise SystemExit(_cli_main())
    except Exception as error:
        failure = {
            "command": "failed",
            "errorType": type(error).__name__,
            "error": str(error) or type(error).__name__,
            "authority": False,
        }
        effects = getattr(error, "effects", None)
        if isinstance(effects, ContainmentEffects):
            failure["effect"] = build_effect_record("Failed", effects)
        print(json.dumps(failure, sort_keys=True, separators=(",", ":")), file=sys.stderr)
        raise SystemExit(3)
