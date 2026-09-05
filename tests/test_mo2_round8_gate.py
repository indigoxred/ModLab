"""Focused offline tests for the Task 7B disposable full-stack gate harness.

These tests allocate only explicitly supplied scratch roots.  They never inspect the
real archive, start the watcher, launch MO2, or interact with a native window.
"""

from __future__ import annotations

from dataclasses import asdict, replace
from contextlib import ExitStack
import base64
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import call, patch
import zlib


HERE = Path(__file__).absolute().parent
REPO = HERE.parents[2]
HARNESS_PATH = HERE / "task-7B-live-gate-harness.py"
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from modlab.adapters.mo2.archive import ArchiveEntry, ArchiveListing
from modlab.adapters.mo2.bootstrap_model import (
    ArchiveEvidence,
    BootstrapFailureResult,
    BootstrapDisposition,
    BootstrapJobState,
    BootstrapReceiptMode,
    ExtractorIdentity,
    FileIdentity,
    TargetSnapshot,
)
from modlab.workflows.skyrim.mo2_bootstrap import Mo2BootstrapRefusal
from modlab.adapters.mo2.bootstrap_serialization import (
    journal_from_dict,
    journal_to_dict,
    plan_from_dict,
    plan_id_for,
    plan_to_dict,
    receipt_from_dict,
    receipt_id_for,
    receipt_to_dict,
)
from modlab.adapters.mo2.bootstrap_config import render_modorganizer_ini
from modlab.adapters.mo2.path_budget import PathBudget, PlannedPath, admit_paths, stage_name
from modlab.artifacts.vault import ArchiveVault
from modlab.validation.mo2_containment_model import (
    ContainmentScenario,
    ProtectedState,
    TreeIdentity,
    WatchEvidenceCompletion,
    WatchOutcome,
    WatcherEvent,
)
from modlab.validation.mo2_containment_service import ContainmentEffects, ContainmentOperationError
from modlab.validation.windows_integrity import IntegrityLevel
from modlab.validation.windows_watch_protocol import (
    ROOT_KINDS,
    ControllerClaim,
    WatchReceipt,
    WatchRequest,
    WatchRoot,
    WorkerLaunch,
    controller_claim_to_bytes,
    watch_worker_command,
    worker_launch_to_bytes,
)
from tests.support.mo2_bootstrap import (
    BootstrapPlanningFixture,
    FakeRunner,
    make_journal_fixture,
    make_plan_fixture,
    make_receipt_fixture,
)


def _load_harness():
    if not HARNESS_PATH.is_file():
        return None
    spec = importlib.util.spec_from_file_location("task_7b_live_gate_harness", HARNESS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


h = _load_harness()


def _listing(*names: str) -> ArchiveListing:
    entries = tuple(ArchiveEntry(name, "file") for name in names)
    canonical = ("\n".join(names) + "\n").encode("utf-8")
    return ArchiveListing(entries, hashlib.sha256(canonical).hexdigest())


def _tree() -> TreeIdentity:
    return TreeIdentity("1" * 64, 1, 1, 1)


def _protected() -> ProtectedState:
    tree = _tree()
    return ProtectedState(tree, "2" * 64, "3" * 64, tree, tree, tree)


def _png_bytes() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\xff"))
        + chunk(b"IEND", b"")
    )


def _receipt(
    *,
    request: str,
    session: str,
    worker: int,
    changed: bool = False,
    run: str = "a" * 32,
    outcome_id: str = "watch-outcome-sha256:" + "4" * 64,
) -> WatchReceipt:
    return WatchReceipt(
        request_id=request,
        session_id=session,
        run_id="containment-run:" + run,
        scenario=ContainmentScenario.NEW_FOLDER,
        worker_pid=worker,
        evidence_completion=WatchEvidenceCompletion.COMPLETED,
        ready=True,
        opened_root_kinds=ROOT_KINDS,
        events=() if not changed else (),
        event_bytes_sha256=hashlib.sha256(b"").hexdigest(),
        worker_exit_code=0,
        watch_outcome_id=outcome_id,
        request_bytes_sha256="5" * 64,
        error=None,
    )


def _cleanup_outcome(
    request: WatchRequest,
    worker: int,
    *,
    completion: WatchEvidenceCompletion = WatchEvidenceCompletion.COMPLETED,
    reasons: tuple[str, ...] = (),
) -> WatchOutcome:
    return WatchOutcome(
        schema_version=1,
        request_id=request.request_id,
        request_sha256=h.watch_request_sha256(request),
        session_id=request.session_id,
        run_id=request.run_id,
        scenario=request.scenario,
        controller_pid=29,
        controller_creation_time=99,
        worker_pid=worker,
        worker_creation_time=100,
        evidence_completion=completion,
        worker_exit_code=0,
        ready=True,
        opened_root_kinds=ROOT_KINDS,
        events=(),
        event_bytes_sha256=hashlib.sha256(b"").hexdigest(),
        journal_volume_serial=1,
        journal_file_id=2,
        journal_byte_count=0,
        journal_event_count=0,
        journal_final_sequence=0,
        terminal_bytes_sha256="6" * 64,
        root_identities_unchanged=True,
        reason_codes=reasons,
    )


@unittest.skipIf(h is None, "harness not implemented yet")
class OfflineGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = SCRATCH / self._testMethodName
        self.scratch.mkdir(parents=True)
        self.run_root = self.scratch / ("a" * 32)
        self.archive = self.scratch / "Mod.Organizer-2.5.2.7z"
        self.steam = self.scratch / "Steam"
        self.archive.write_bytes(b"synthetic-not-a-live-archive")
        self.game = self.steam / "steamapps" / "common" / "Skyrim Special Edition"
        self.game.mkdir(parents=True)
        (self.game / "SkyrimSE.exe").write_bytes(b"fixture-skyrim")

    def _complete_phase_evidence(self, case: str):
        root = self.scratch / case / ("a" * 32)
        layout = "SingleFile"
        phase = "Control"
        layout_root = root / layout
        app = layout_root / "app"
        workspace = layout_root / "bootstrap"
        manager = h.workspace_layout(workspace).skyrim_mo2
        job = layout_root / "jobs" / phase
        watch = job / "watch"
        watch.mkdir(parents=True)
        app.mkdir(parents=True)
        manager.mkdir(parents=True)
        (app / "ModOrganizer.exe").write_bytes(b"fixture MO2")
        roots = {}
        for kind in ROOT_KINDS:
            path = root / "protected" / kind
            path.mkdir(parents=True)
            roots[kind] = path
        watch_roots = tuple(h.windows_watch.watch_root(kind, roots[kind]) for kind in ROOT_KINDS)
        request = WatchRequest(
            "watch-request:" + hashlib.sha256((case + ":request").encode()).hexdigest(),
            "watch-session:" + hashlib.sha256((case + ":session").encode()).hexdigest(),
            "containment-run:" + root.name,
            ContainmentScenario.NEW_FOLDER,
            watch.absolute(),
            (watch / "stop.token").absolute(),
            watch_roots,
        )
        request_data = h.watch_request_to_bytes(request)
        (watch / "request.json").write_bytes(request_data)
        request_sha = h.watch_request_sha256(request)
        claim = h.windows_watch.ControllerClaim(
            schema_version=1,
            request_sha256=request_sha,
            session_id=request.session_id,
            run_id=request.run_id,
            scenario=request.scenario,
            request_path=(watch / "request.json").absolute(),
            worker_command=h.windows_watch.watch_worker_command(watch / "request.json"),
            controller_pid=7001,
            controller_creation_time=8001,
        )
        (watch / "controller-claim.json").write_bytes(
            h.controller_claim_to_bytes(claim, request)
        )
        launch = h.windows_watch.WorkerLaunch(
            schema_version=1,
            request_sha256=request_sha,
            session_id=request.session_id,
            run_id=request.run_id,
            scenario=request.scenario,
            worker_pid=7002,
            worker_creation_time=8002,
        )
        (watch / "worker-launch.json").write_bytes(
            h.worker_launch_to_bytes(launch, request)
        )
        (watch / "ready.json").write_bytes(h._canonical(
            h.windows_watch._ready_document(
                request,
                launch.worker_pid,
                request_sha,
                launch.worker_creation_time,
            )
        ))
        (watch / "events.ndjson").write_bytes(b"")
        journal = h.windows_watch._read_exact_journal(watch / "events.ndjson")
        (watch / "terminal.json").write_bytes(h._canonical({
            "complete": True,
            "error": None,
            "eventByteCount": 0,
            "eventBytesSha256": hashlib.sha256(b"").hexdigest(),
            "eventCount": 0,
            "finalSequence": 0,
            "journalFileId": journal.file_id,
            "journalVolumeSerial": journal.volume_serial,
            "openHandleCount": 0,
            "openedRootKinds": list(ROOT_KINDS),
            "ready": True,
            "requestBytesSha256": request_sha,
            "requestId": request.request_id,
            "rootIdentitiesUnchanged": True,
            "schemaVersion": 1,
            "workerCreationTime": launch.worker_creation_time,
            "workerPid": launch.worker_pid,
        }))
        captured = h.windows_watch._capture_worker_evidence(request, launch)
        outcome = h.windows_watch._watch_outcome(
            request,
            claim,
            launch,
            completion=WatchEvidenceCompletion.COMPLETED,
            worker_exit_code=0,
            reasons=(),
            captured=captured,
        )
        (watch / "outcome.json").write_bytes(h.watch_outcome_to_bytes(outcome))
        (watch / "stop.token").write_bytes(b"stop\n")

        protected = h._json_value(_protected())
        writable = {
            name: {
                "rootIdentity": {"volumeSerial": 1, "fileId": index, "attributes": 16},
                "inventory": [],
            }
            for index, name in enumerate(("App", "Manager", "Environment"), 1)
        }
        nonce = hashlib.sha256((case + ":nonce").encode()).hexdigest()[:32]
        begin = {
            "schemaVersion": 1,
            "kind": "task-7B-phase-begin",
            "runId": root.name,
            "layout": layout,
            "phase": phase,
            "nonce": nonce,
            "preparationId": "preparation-sha256:" + "1" * 64,
            "startedAt": "2026-09-04T00:00:00+00:00",
            "pluginsBefore": [],
            "appBefore": [],
            "runtimeBefore": [],
            "writableBefore": writable,
            "protectedBefore": protected,
            "requestId": request.request_id,
            "sessionId": request.session_id,
            "authority": False,
        }
        begin["beginId"] = "phase-begin-sha256:" + h._sha256(h._canonical(begin))
        h.publish_exact_json(job / "begin.json", begin)
        mo2_pid = 7003
        process = {
            "pid": mo2_pid,
            "creationTime": 8003,
            "executable": str(app / "ModOrganizer.exe"),
            "integrity": "LOW",
            "arguments": ["--profile", "ModLab - Lab"],
            "workingDirectory": str(app),
        }
        process["processId"] = "phase-process-sha256:" + h._sha256(h._canonical(process))
        h.publish_exact_json(job / "process.json", process)
        (job / "window.png").write_bytes(_png_bytes())
        image = {
            "path": str(job / "window.png"),
            "sha256": hashlib.sha256(_png_bytes()).hexdigest(),
            "size": len(_png_bytes()),
        }
        native = {
            "hwnd": 9001,
            "pid": mo2_pid,
            "title": "Mod Organizer v2.5.2",
            "className": "Qt5152QWindowIcon",
            "visible": True,
        }
        process_identity = {
            "pid": mo2_pid,
            "creationTime": 8003,
            "executable": str(app / "ModOrganizer.exe"),
        }
        window = {
            "windowId": 51,
            "app": "process:" + str(app / "ModOrganizer.exe"),
            "title": native["title"],
            "screenshotId": "fixture-shot",
            "image": image,
            "nativeBefore": native,
            "nativeAfter": native,
        }
        close = {
            "action": "Alt+F4",
            "windowId": 51,
            "returned": True,
            "remainingMatches": [],
            "exitCode": 0,
        }
        operator = {
            "schemaVersion": 1,
            "kind": "task-7B-operator-evidence",
            "runId": root.name,
            "layout": layout,
            "phase": phase,
            "process": process_identity,
            "window": window,
            "tool": None,
            "close": close,
            "windowObservedAt": "2026-09-04T00:00:01+00:00",
            "closeObservedAt": "2026-09-04T00:00:02+00:00",
            "computerControlUsed": True,
            "authority": False,
        }
        operator["operatorEvidenceId"] = "operator-evidence-sha256:" + h._sha256(h._canonical(operator))
        h.publish_exact_json(job / "operator-evidence.json", operator)
        observation = {
            "pid": mo2_pid,
            "before": [],
            "runtimeBefore": [],
            "launchProcess": process_identity,
            "ui": {
                "windowId": 51,
                "app": window["app"],
                "title": window["title"],
                "loadedTool": None,
                "closeAction": "Alt+F4",
                "screenshotIds": ["fixture-shot"],
            },
        }
        h.publish_exact_json(job / "observation.json", observation)
        evidence_paths = (
            layout_root,
            job / "begin.json",
            job / "process.json",
            job / "window.png",
            job / "operator-evidence.json",
            job / "runtime-delta.json",
            job / "observation.json",
            job / "effect.json",
            job / "phase-final.json",
            *(watch / name for name in (
                "request.json", "controller-claim.json", "worker-launch.json",
                "ready.json", "events.ndjson", "terminal.json", "outcome.json",
                "stop.token",
            )),
        )
        effect = h.build_effect_record(
            f"{layout}:{phase}",
            ContainmentEffects(
                written_paths=evidence_paths,
                child_mutation_roots=(layout_root,),
                watcher_pid=launch.worker_pid,
                mo2_pid=mo2_pid,
            ),
        )
        h.publish_exact_json(job / "effect.json", effect)
        runtime_delta = h.build_runtime_delta(root, layout, phase, writable, writable)
        h.publish_exact_json(job / "runtime-delta.json", runtime_delta)
        raw_names = {
            "requestSha256": "request.json",
            "claimSha256": "controller-claim.json",
            "launchSha256": "worker-launch.json",
            "readySha256": "ready.json",
            "eventsSha256": "events.ndjson",
            "terminalSha256": "terminal.json",
            "outcomeSha256": "outcome.json",
            "stopSha256": "stop.token",
        }
        final = {
            "schemaVersion": 1,
            "kind": "task-7B-phase-final",
            "runId": root.name,
            "layout": layout,
            "phase": phase,
            "preparationId": begin["preparationId"],
            "observationId": "observation-sha256:" + h._sha256(h._canonical(observation)),
            "watchRequestId": request.request_id,
            "watchOutcomeId": h.watch_outcome_id_for(outcome),
            "effectId": effect["effectId"],
            "operatorEvidenceId": operator["operatorEvidenceId"],
            "beginId": begin["beginId"],
            "processId": process["processId"],
            "runtimeDeltaId": runtime_delta["runtimeDeltaId"],
            "watchEvidence": {
                field: hashlib.sha256((watch / name).read_bytes()).hexdigest()
                for field, name in raw_names.items()
            },
            "protectedBefore": protected,
            "protectedAfter": protected,
            "windowObservedAt": operator["windowObservedAt"],
            "closeObservedAt": operator["closeObservedAt"],
            "completedAt": "2026-09-04T00:00:03+00:00",
            "authority": False,
        }
        final["phaseFinalId"] = "phase-final-sha256:" + h._sha256(h._canonical(final))
        h.publish_exact_json(job / "phase-final.json", final)
        preparation = {
            "preparationId": begin["preparationId"],
            "runRoot": str(root),
            "gameRoot": str(roots["BoundedGame"]),
            "layouts": [{
                "layout": layout,
                "workspace": str(workspace),
                "appRoot": str(app),
                "managerRoot": str(manager),
            }],
        }
        return SimpleNamespace(
            root=root,
            job=job,
            watch=watch,
            preparation=preparation,
            roots=roots,
            protected=_protected(),
        )

    def _reload_complete_phase(self, fixture):
        with patch.object(h, "_load_preparation", return_value=fixture.preparation), \
                patch.object(h, "_derived_watch_roots", return_value=fixture.roots), \
                patch.object(h, "_capture_protected", return_value=fixture.protected), \
                patch.object(h.runtime_capability, "_observation"), \
                patch.object(h.runtime_capability, "_verify_raw_evidence"):
            return h._load_validated_phase_bundle(
                fixture.root,
                "SingleFile",
                "Control",
                fixture.preparation["preparationId"],
            )

    def test_gate_path_admission_covers_both_bootstraps_and_runtime_without_mutation(self):
        listing = _listing("ModOrganizer.exe", "plugins/plugin_python/libs/mobase.cp312-win_amd64.pyd")
        admission = h.admit_gate_paths(
            self.run_root,
            listing,
            self.archive,
            self.steam / "steamapps/common/Skyrim Special Edition",
            {"SingleFile": "bootstrap-job:" + "1" * 32, "Package": "bootstrap-job:" + "2" * 32},
        )
        self.assertIsInstance(admission.bootstrap_budget, PathBudget)
        self.assertFalse(admission.bootstrap_budget.runtime_qualified)
        self.assertEqual((247, 512, 255, 100000), (
            admission.tar_cwd_limit,
            admission.complete_path_limit,
            admission.component_limit,
            admission.inventory_limit,
        ))
        stages = {row.stage for row in admission.bootstrap_budget.paths}
        self.assertIn("archive-extraction", stages)
        self.assertIn("gate-runtime:SingleFile", stages)
        self.assertIn("gate-runtime:Package", stages)
        admitted_names = {Path(row.path).name for row in admission.bootstrap_budget.paths}
        self.assertTrue({
            "controller-claim.json", "worker-launch.json", "ready.json",
            "terminal.json", "controller-loss.json", "installation.json", "installation-failure.json",
            "effect.json", "phase-final.json", "process.json", "selection.json",
            "full-stack-envelope.json",
        }.issubset(admitted_names))
        self.assertIn("observed-000.log", admitted_names)
        self.assertIn(f"observed-{h.MAX_RETAINED_LOGS - 1:03}.log", admitted_names)
        self.assertNotIn(f"observed-{h.MAX_RETAINED_LOGS:03}.log", admitted_names)
        self.assertTrue(any("publication-candidate" in row.stage for row in admission.bootstrap_budget.paths))
        self.assertFalse(self.run_root.exists())

    def test_path_admission_includes_private_candidates_for_non_json_publications(self):
        """Removing non-JSON publication admission must expose this test failure."""
        admission = h.admit_gate_paths(
            self.run_root,
            _listing("ModOrganizer.exe", "plugins/plugin_python/libs/mobase.cp312-win_amd64.pyd"),
            self.archive,
            self.steam / "steamapps/common/Skyrim Special Edition",
            {"SingleFile": "bootstrap-job:" + "1" * 32, "Package": "bootstrap-job:" + "2" * 32},
        )
        paths = admission.bootstrap_budget.paths
        for target_name in (
            "nxmhandler.ini",
            "modlab_capability_probe.py",
            "observed-000.log",
            "window.png",
            "events.ndjson",
            "stop.token",
        ):
            targets = [row for row in paths if Path(row.path).name == target_name]
            self.assertTrue(targets, target_name)
            self.assertTrue(
                any(
                    "publication-candidate" in row.stage
                    and Path(row.path).name.startswith(f".{target_name}.")
                    and Path(row.path).suffix == ".tmp"
                    for row in paths
                ),
                target_name,
            )
        self.assertFalse(self.run_root.exists())

    def test_overbudget_refuses_before_backend_mutation(self):
        overlong = self.scratch / ("x" * 240) / ("b" * 32)
        backend = _FakeBackend(self.scratch, _listing("ModOrganizer.exe"))
        config = h.GateConfig(
            overlong,
            self.archive,
            self.steam,
            {"commit": "1" * 40, "tree": "2" * 40},
            {"SingleFile": "bootstrap-job:" + "1" * 32, "Package": "bootstrap-job:" + "2" * 32},
        )
        with self.assertRaises(h.GateError):
            h.prepare_gate(config, backend=backend)
        self.assertEqual([], backend.mutations)
        self.assertFalse(overlong.exists())

    def test_gate_config_rejects_extra_source_assertions(self):
        with self.assertRaises(h.GateError):
            h.GateConfig(
                self.run_root,
                self.archive,
                self.steam,
                {"commit": "1" * 40, "tree": "2" * 40, "claimed": "forged"},
                {"SingleFile": "bootstrap-job:" + "1" * 32, "Package": "bootstrap-job:" + "2" * 32},
            )

    def test_preparation_calls_two_fresh_bootstraps_and_requires_created_receipts(self):
        backend = _FakeBackend(self.scratch, _listing("ModOrganizer.exe", "plugins/base.py"))
        config = h.GateConfig(
            self.run_root,
            self.archive,
            self.steam,
            {"commit": "1" * 40, "tree": "2" * 40},
            {"SingleFile": "bootstrap-job:" + "1" * 32, "Package": "bootstrap-job:" + "2" * 32},
        )
        received = h.prepare_gate(config, backend=backend)
        self.assertEqual(["SingleFile", "Package"], backend.imported)
        self.assertEqual(["SingleFile", "Package"], backend.prepared)
        self.assertEqual(["SingleFile", "Package"], backend.applied)
        self.assertEqual(["SingleFile", "Package"], backend.relocated)
        self.assertEqual((self.run_root,), received.effects.child_mutation_roots)
        processes = set(received.effects.preparation_processes)
        self.assertTrue({
            "tar-plan:SingleFile", "tar-apply:SingleFile",
            "tar-plan:Package", "tar-apply:Package",
        }.issubset(processes))
        self.assertEqual(6, len(processes))
        self.assertEqual(2, sum(item.startswith("icacls-low:") for item in processes))
        record = received.value
        self.assertEqual(["SingleFile", "Package"], [row["layout"] for row in record["layouts"]])
        for row in record["layouts"]:
            self.assertEqual("Created", row["bootstrap"]["mode"])
            self.assertEqual("Verified", row["bootstrap"]["state"])
            self.assertTrue(Path(row["appRoot"]).is_dir())
        self.assertEqual(record, h.load_exact_json(self.run_root / "preparation.json"))
        self.assertRegex(record["preparationId"], r"^preparation-sha256:[0-9a-f]{64}$")
        self.assertRegex(record["sourceArtifactId"], r"^containment-source-artifact-sha256:[0-9a-f]{64}$")
        self.assertEqual("handle-pinned-no-replace-v2", record["policies"]["publication"])
        self.assertEqual(2, record["policies"]["protocol"])
        self.assertRegex(record["harnessSha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(record["nativeHelperSha256"], r"^[0-9a-f]{64}$")

    def test_preparation_publishes_a_bilaterally_bound_service_effect_receipt(self):
        """Dropping durable preparation effects must make this test fail."""
        backend = _FakeBackend(self.scratch, _listing("ModOrganizer.exe", "plugins/base.py"))
        received = h.prepare_gate(
            h.GateConfig(
                self.run_root,
                self.archive,
                self.steam,
                {"commit": "1" * 40, "tree": "2" * 40},
                {"SingleFile": "bootstrap-job:" + "1" * 32, "Package": "bootstrap-job:" + "2" * 32},
            ),
            backend=backend,
        )
        preparation = received.value
        effect = h.load_exact_json(self.run_root / "preparation-effect.json")
        self.assertEqual(preparation["preparationId"], effect["preparationId"])
        self.assertEqual(preparation["preparationEffectId"], effect["effect"]["effectId"])
        self.assertEqual(effect["effect"], h.validate_effect_record(effect["effect"]))
        self.assertEqual(h.build_effect_record("Preparation", received.effects), effect["effect"])
        self.assertIn(str(self.run_root / "preparation.json"), effect["effect"]["writtenPaths"])
        self.assertIn(str(self.run_root / "preparation-effect.json"), effect["effect"]["writtenPaths"])

    def test_real_preparation_path_requires_the_full_strict_reload_before_success(self):
        backend = _FakeBackend(self.scratch, _listing("ModOrganizer.exe", "plugins/base.py"))
        config = h.GateConfig(
            self.run_root,
            self.archive,
            self.steam,
            {"commit": "1" * 40, "tree": "2" * 40},
            {"SingleFile": "bootstrap-job:" + "1" * 32, "Package": "bootstrap-job:" + "2" * 32},
        )
        strict_loads = []

        def strict_load(path):
            strict_loads.append(Path(path).absolute())
            return h.load_exact_json(Path(path) / "preparation.json")

        with patch.object(h, "ProductionBackend", _FakeBackend), \
                patch.object(h, "_load_preparation", side_effect=strict_load):
            received = h.prepare_gate(config, backend=backend)
        self.assertEqual([self.run_root], strict_loads)
        self.assertEqual(received.value, h.load_exact_json(self.run_root / "preparation.json"))

    def test_preparation_binds_current_game_executable_and_root_identity(self):
        """A pathname alone is not durable evidence of the protected game tree."""
        record = h.prepare_gate(
            h.GateConfig(
                self.run_root,
                self.archive,
                self.steam,
                {"commit": "1" * 40, "tree": "2" * 40},
                {"SingleFile": "bootstrap-job:" + "1" * 32, "Package": "bootstrap-job:" + "2" * 32},
            ),
            backend=_FakeBackend(self.scratch, _listing("ModOrganizer.exe", "plugins/base.py")),
        ).value
        game = record["game"]
        self.assertEqual(str(self.game), game["root"])
        self.assertEqual(h._identity(h.windows_exact_fs.identity_at_path(self.game)), game["rootIdentity"])
        self.assertEqual(h._stable_file(self.game / "SkyrimSE.exe"), game["skyrimExecutable"])

    def test_non_created_bootstrap_is_retained_as_failed_attempt_not_upgraded(self):
        backend = _FakeBackend(self.scratch, _listing("ModOrganizer.exe"))
        backend.mode = BootstrapReceiptMode.ADOPTED
        config = h.GateConfig(
            self.run_root,
            self.archive,
            self.steam,
            {"commit": "1" * 40, "tree": "2" * 40},
            {"SingleFile": "bootstrap-job:" + "1" * 32, "Package": "bootstrap-job:" + "2" * 32},
        )
        with self.assertRaises(Exception) as raised:
            h.prepare_gate(config, backend=backend)
        self.assertEqual((self.run_root,), raised.exception.effects.child_mutation_roots)
        self.assertTrue(self.run_root.is_dir())
        self.assertFalse((self.run_root / "preparation.json").exists())
        failure = h.load_exact_json(self.run_root / "failure.json")
        self.assertTrue(failure["terminal"])
        self.assertFalse(failure["retryPermitted"])
        effect = h.validate_effect_record(failure["effects"])
        self.assertEqual("Preparation:failed", effect["scope"])
        self.assertEqual([str(self.run_root)], effect["childMutationRoots"])
        self.assertIn(str(self.run_root / "failure.json"), effect["writtenPaths"])
        self.assertTrue(any(path.endswith("fixture-plan.json") for path in effect["writtenPaths"]))
        self.assertIn("tar-plan:SingleFile", effect["preparationProcesses"])
        self.assertIn("tar-apply:SingleFile", effect["preparationProcesses"])
        self.assertEqual([], effect["sourceChanges"])
        self.assertEqual([], effect["gameChanges"])
        self.assertEqual([], effect["productionMo2Changes"])
        self.assertEqual(failure, h.load_exact_json(self.run_root / "failure.json"))

    def test_raised_bootstrap_refusal_retains_real_partial_failure_effects(self):
        backend = _FakeBackend(self.scratch, _listing("ModOrganizer.exe"))
        original_apply = backend.apply_setup
        config = h.GateConfig(
            self.run_root,
            self.archive,
            self.steam,
            {"commit": "1" * 40, "tree": "2" * 40},
            {"SingleFile": "bootstrap-job:" + "1" * 32, "Package": "bootstrap-job:" + "2" * 32},
        )
        relative = "tools/mo2/skyrim-se-ae/partial.txt"

        expected_result = None

        def refuse(layout, plan_id, workspace, job_id):
            nonlocal expected_result
            applied = original_apply(layout, plan_id, workspace, job_id)
            expected_result = BootstrapFailureResult(
                outcome="RecoveryRequired",
                code="fixture-partial",
                message="fixture bootstrap failed after a partial write",
                plan_id=plan_id,
                job_id=job_id,
                journal=applied.journal,
                receipt=applied.receipt,
                actions_complete=True,
                paths_written=(relative, *applied.paths_written),
                downloads=("fixture-download",),
                installations=applied.installations,
                manager_changes=(relative, *applied.manager_changes),
                game_changes=(),
                programs_launched=("tar-partial:SingleFile", *applied.programs_launched),
            )
            raise Mo2BootstrapRefusal(
                "fixture-partial",
                "fixture bootstrap failed after a partial write",
                failure=expected_result,
            )

        backend.apply_setup = refuse
        with self.assertRaises(ContainmentOperationError):
            h.prepare_gate(config, backend=backend)
        failure = h.load_exact_json(self.run_root / "failure.json")
        effect = h.validate_effect_record(failure["effects"])
        expected = str(self.run_root / "SingleFile" / "bootstrap" / Path(*relative.split("/")))
        self.assertIn(expected, effect["writtenPaths"])
        self.assertIn("tar-partial:SingleFile", effect["preparationProcesses"])
        self.assertEqual([], effect["gameChanges"])
        self.assertFalse((self.run_root / "preparation.json").exists())
        self.assertIsNotNone(expected_result)
        evidence = failure["bootstrapFailure"]
        self.assertEqual("SingleFile", evidence["layout"])
        self.assertEqual(str(self.run_root / "SingleFile" / "bootstrap"), evidence["workspace"])
        result = evidence["result"]
        self.assertEqual(
            {
                "outcome", "code", "message", "planId", "jobId", "journal",
                "receipt", "actionsComplete", "pathsWritten", "downloads",
                "installations", "managerChanges", "gameChanges", "programsLaunched",
            },
            set(result),
        )
        self.assertEqual(expected_result.outcome, result["outcome"])
        self.assertEqual(expected_result.plan_id, result["planId"])
        self.assertEqual(expected_result.job_id, result["jobId"])
        self.assertEqual(journal_to_dict(expected_result.journal), result["journal"])
        self.assertEqual(receipt_to_dict(expected_result.receipt), result["receipt"])
        self.assertTrue(result["actionsComplete"])
        self.assertEqual(list(expected_result.paths_written), result["pathsWritten"])
        self.assertEqual(["fixture-download"], result["downloads"])
        self.assertEqual(["portable-mo2-create"], result["installations"])
        self.assertEqual(list(expected_result.manager_changes), result["managerChanges"])
        self.assertEqual(list(expected_result.programs_launched), result["programsLaunched"])
        self.assertRegex(evidence["bootstrapFailureId"], r"^bootstrap-failure-sha256:[0-9a-f]{64}$")
        self.assertRegex(failure["failureId"], r"^preparation-failure-sha256:[0-9a-f]{64}$")
        self.assertEqual(failure, h._load_preparation_failure(self.run_root))

    def test_real_shared_relocation_preserves_identity_and_refuses_collision(self):
        source_parent = self.scratch / "source"
        destination_parent = self.scratch / "destination"
        source = source_parent / "app"
        destination = destination_parent / "app"
        (source / "plugins").mkdir(parents=True)
        destination_parent.mkdir()
        (source / "ModOrganizer.exe").write_bytes(b"fixture")
        evidence = h.relocate_created_app(source, destination)
        self.assertFalse(source.exists())
        self.assertTrue(destination.is_dir())
        self.assertEqual(evidence["sourceIdentity"], evidence["destinationIdentity"])

        other = self.scratch / "other"
        target = self.scratch / "occupied" / "app"
        other.mkdir()
        target.mkdir(parents=True)
        (other / "keep.bin").write_bytes(b"keep")
        with self.assertRaises(Exception):
            h.relocate_created_app(other, target)
        self.assertEqual(b"keep", (other / "keep.bin").read_bytes())

    def test_relocation_refuses_source_or_parent_substitution_cross_volume_and_reparse(self):
        source_parent = self.scratch / "relocation-source"
        destination_parent = self.scratch / "relocation-destination"
        source = source_parent / "app"
        destination = destination_parent / "app"
        source.mkdir(parents=True)
        destination_parent.mkdir()
        one = SimpleNamespace(volume_serial=1, file_id=1, attributes=16)
        source_parent_id = SimpleNamespace(volume_serial=1, file_id=2, attributes=16)
        destination_parent_id = SimpleNamespace(volume_serial=1, file_id=3, attributes=16)

        for changed_call in (4, 6):
            calls = 0
            expected = (one, source_parent_id, destination_parent_id, one, source_parent_id, destination_parent_id)

            def identity(_path):
                nonlocal calls
                calls += 1
                if calls == changed_call:
                    return SimpleNamespace(volume_serial=1, file_id=99, attributes=16)
                return expected[calls - 1]

            def invoke(_source, _destination, *, validate_before, validate_after):
                validate_before()
                validate_after()

            with self.subTest(changed_call=changed_call), \
                    patch.object(h.windows_exact_fs, "identity_at_path", side_effect=identity), \
                    patch.object(h, "_move_exact_directory", side_effect=invoke), \
                    self.assertRaises(h.GateError):
                h.relocate_created_app(source, destination)
            self.assertTrue(source.is_dir())
            self.assertFalse(destination.exists())

        with patch.object(
            h.windows_exact_fs,
            "identity_at_path",
            side_effect=(one, source_parent_id, SimpleNamespace(volume_serial=2, file_id=3, attributes=16)),
        ), patch.object(h, "_move_exact_directory") as move, self.assertRaises(h.GateError):
            h.relocate_created_app(source, destination)
        move.assert_not_called()

        with patch.object(Path, "is_symlink", lambda value: value == source), \
                patch.object(h, "_move_exact_directory") as move, self.assertRaises(h.GateError):
            h.relocate_created_app(source, destination)
        move.assert_not_called()

    def test_relocation_propagates_retained_close_failure_without_fallback(self):
        source = self.scratch / "owned-source"
        destination_parent = self.scratch / "owned-destination"
        destination = destination_parent / "app"
        source.mkdir()
        destination_parent.mkdir()
        retained = RuntimeError("retained exact owner")
        with patch.object(h, "_move_exact_directory", side_effect=retained), \
                self.assertRaisesRegex(RuntimeError, "retained exact owner"):
            h.relocate_created_app(source, destination)
        self.assertTrue(source.is_dir())
        self.assertFalse(destination.exists())

    def test_phase_order_requires_control_install_and_exact_prefix(self):
        self.assertEqual("Control", h.next_action(()))
        self.assertEqual("Install", h.next_action(("Control",), installed=False))
        self.assertEqual("First", h.next_action(("Control",), installed=True))
        self.assertEqual("Second", h.next_action(("Control", "First"), installed=True))
        self.assertEqual("Passive", h.next_action(("Control", "First", "Second"), installed=True))
        self.assertEqual("Guarded", h.next_action(("Control", "First", "Second", "Passive"), installed=True))
        self.assertEqual("Complete", h.next_action(("Control", "First", "Second", "Passive", "Guarded"), installed=True))
        for invalid in (("First",), ("Control", "Second"), ("Control", "First", "First")):
            with self.subTest(invalid=invalid), self.assertRaises(h.GateError):
                h.next_action(invalid)

    def test_durable_state_rejects_phase_filename_without_validated_bundle(self):
        job = self.run_root / "SingleFile" / "jobs" / "Control"
        job.mkdir(parents=True)
        (job / "phase-final.json").write_bytes(b"{}\n")
        preparation = {
            "preparationId": "preparation-sha256:" + "1" * 64,
            "runRoot": str(self.run_root),
        }
        with patch.object(h, "_load_preparation", return_value=preparation), self.assertRaises(Exception):
            h.ProductionLiveBackend().load_state(self.run_root, "SingleFile")

    def test_child_environment_is_exact_and_excludes_secrets_and_bytecode_controls(self):
        environment = h.child_environment(
            self.run_root,
            "SingleFile",
            "Guarded",
            "b" * 32,
            system_root=r"C:\Windows",
            username="fixture-user",
        )
        expected = {
            "SystemRoot", "WINDIR", "PATH", "USERNAME", "TEMP", "TMP", "APPDATA",
            "LOCALAPPDATA", "USERPROFILE", "HOME", "MODLAB_CAPABILITY_PHASE",
            "MODLAB_CAPABILITY_NONCE", "MODLAB_CAPABILITY_GUARD",
        }
        self.assertEqual(expected, set(environment))
        self.assertFalse(any(name.upper().startswith("PYTHON") for name in environment))
        self.assertFalse(any(name in environment for name in ("TOKEN", "PASSWORD", "SECRET", "QT_DEBUG_PLUGINS")))

    def test_output_policy_refuses_escape_cache_and_runtime_identity_drift(self):
        base = [{"path": "app/plugins/base.py", "kind": "file", "sha256": "1" * 64, "size": 1, "volume": 1, "fileId": 1}]
        allowed_log = {**base[0], "path": "logs/mo_interface.log", "fileId": 2}
        self.assertEqual([], h.validate_runtime_outputs(self.run_root, "SingleFile", "First", base, [*base, allowed_log], base, base))
        for after, runtime_after in (
            ([*base, {**allowed_log, "path": "escape.bin"}], base),
            ([*base, {**allowed_log, "path": "app/plugins/modlab_capability_probe/__pycache__/plugin.pyc"}], base),
            ([*base, {**allowed_log, "path": "app/logs/" + "x" * 256}], base),
            ([{**base[0], "sha256": "2" * 64}], base),
            ([*base, allowed_log], [{**base[0], "fileId": 9}]),
        ):
            with self.assertRaises(h.GateError):
                h.validate_runtime_outputs(self.run_root, "SingleFile", "First", base, after, base, runtime_after)

    def test_runtime_delta_confines_app_manager_and_environment_writes(self):
        roots = h._runtime_writable_roots(self.run_root, "SingleFile")
        before = {
            name: {
                "rootIdentity": {"volumeSerial": 1, "fileId": index, "attributes": 16},
                "inventory": [],
            }
            for index, name in enumerate(roots, 1)
        }
        allowed = copy.deepcopy(before)
        allowed["Manager"]["inventory"] = [{"path": "webcache/cache.bin", "kind": "file", "sha256": "2" * 64, "size": 1, "volume": 1, "fileId": 2}]
        allowed["Environment"]["inventory"] = [{"path": "TEMP/session.tmp", "kind": "file", "sha256": "3" * 64, "size": 1, "volume": 1, "fileId": 3}]
        delta = h.build_runtime_delta(self.run_root, "SingleFile", "First", before, allowed)
        self.assertRegex(delta["runtimeDeltaId"], r"^runtime-delta-sha256:[0-9a-f]{64}$")
        for root_name, relative in (
            ("App", "logs/mo_interface.log"),
            ("App", "plugins/foreign.py"),
            ("Manager", "profiles/ModLab - Lab/plugins.txt"),
            ("Environment", "TEMP/__pycache__/probe.pyc"),
        ):
            changed = copy.deepcopy(allowed)
            changed[root_name]["inventory"].append({
                "path": relative,
                "kind": "file",
                "sha256": "4" * 64,
                "size": 1,
                "volume": 1,
                "fileId": 4,
            })
            with self.subTest(root=root_name, relative=relative), self.assertRaises(h.GateError):
                h.build_runtime_delta(self.run_root, "SingleFile", "First", before, changed)

        preexisting = copy.deepcopy(before)
        preexisting["App"]["inventory"] = [{"path": "ModOrganizer.exe", "kind": "file", "sha256": "5" * 64, "size": 1, "volume": 1, "fileId": 5}]
        changed = copy.deepcopy(preexisting)
        changed["App"]["inventory"][0]["sha256"] = "6" * 64
        with self.assertRaises(h.GateError):
            h.build_runtime_delta(self.run_root, "SingleFile", "First", preexisting, changed)
        changed = copy.deepcopy(before)
        changed["Manager"]["rootIdentity"]["fileId"] = 99
        with self.assertRaisesRegex(h.GateError, "root identity changed"):
            h.build_runtime_delta(self.run_root, "SingleFile", "First", before, changed)

        forbidden = copy.deepcopy(before)
        forbidden["App"]["inventory"] = [{
            "path": "plugins/modlab_capability_probe/__pycache__/probe.pyc",
            "kind": "file",
            "sha256": "7" * 64,
            "size": 1,
            "volume": 1,
            "fileId": 7,
        }]
        with self.assertRaisesRegex(h.GateError, "forbidden"):
            h.build_runtime_delta(self.run_root, "SingleFile", "First", forbidden, copy.deepcopy(forbidden))

    def test_writable_chain_binds_preparation_installation_phases_and_current_state(self):
        def snapshots() -> dict[str, object]:
            return {
                name: {
                    "rootIdentity": {"volumeSerial": 1, "fileId": index, "attributes": 16},
                    "inventory": [],
                }
                for index, name in enumerate(("App", "Manager", "Environment"), 1)
            }

        baseline = snapshots()
        control_after = copy.deepcopy(baseline)
        control_after["Manager"]["inventory"] = [{
            "path": "logs/control.log", "kind": "file", "sha256": "1" * 64,
            "size": 1, "volume": 1, "fileId": 11,
        }]
        candidate = {
            "path": "modlab_capability_probe.py", "kind": "file", "sha256": "2" * 64,
            "size": 1, "volume": 1, "fileId": 12,
        }
        installed_after = copy.deepcopy(control_after)
        installed_after["App"]["inventory"] = [{**candidate, "path": "plugins/" + candidate["path"]}]
        first_after = copy.deepcopy(installed_after)
        first_after["Manager"]["inventory"].append({
            "path": "logs/first.log", "kind": "file", "sha256": "3" * 64,
            "size": 1, "volume": 1, "fileId": 13,
        })
        deltas = {
            "Control": h.build_runtime_delta(self.run_root, "SingleFile", "Control", baseline, control_after),
            "First": h.build_runtime_delta(self.run_root, "SingleFile", "First", installed_after, first_after),
        }
        installation = {
            "declared": [candidate],
            "writableBefore": control_after,
            "writableAfter": installed_after,
        }
        h._validate_writable_chain(
            self.run_root,
            "SingleFile",
            baseline,
            ("Control", "First"),
            installation,
            deltas,
            first_after,
        )

        drifted = copy.deepcopy(first_after)
        drifted["Manager"]["inventory"].append({
            "path": "logs/unobserved.log", "kind": "file", "sha256": "4" * 64,
            "size": 1, "volume": 1, "fileId": 14,
        })
        with self.assertRaises(h.GateError):
            h._validate_writable_chain(
                self.run_root, "SingleFile", baseline, ("Control", "First"),
                installation, deltas, drifted,
            )

        substituted = copy.deepcopy(installation)
        substituted["writableBefore"] = baseline
        with self.assertRaises(h.GateError):
            h._validate_writable_chain(
                self.run_root, "SingleFile", baseline, ("Control", "First"),
                substituted, deltas, first_after,
            )

    def test_watch_request_uses_canonical_order_and_fresh_evidence_root(self):
        roots = {}
        for index, kind in enumerate(ROOT_KINDS, 1):
            path = self.scratch / "protected" / kind
            path.mkdir(parents=True)
            roots[kind] = path
        evidence = self.scratch / "watch"
        evidence.mkdir()
        request = h.build_watch_request(
            self.run_root,
            "SingleFile",
            "Control",
            roots,
            evidence,
            request_token="6" * 64,
            session_token="7" * 64,
            root_factory=lambda kind, path: WatchRoot(kind, path.absolute(), 1, len(kind)),
        )
        self.assertEqual(ROOT_KINDS, tuple(row.root_kind for row in request.roots))
        self.assertEqual(evidence / "stop.token", request.stop_token_path)
        (evidence / "request.json").write_bytes(b"occupied")
        with self.assertRaises(h.GateError):
            h.build_watch_request(
                self.run_root, "SingleFile", "Control", roots, evidence,
                request_token="8" * 64, session_token="9" * 64,
                root_factory=lambda kind, path: WatchRoot(kind, path.absolute(), 1, len(kind)),
            )

    def test_default_watch_roots_use_one_stable_identity_observation_each(self):
        roots = {}
        for kind in ROOT_KINDS:
            path = self.scratch / "stable" / kind
            path.mkdir(parents=True)
            roots[kind] = path
        evidence = self.scratch / "stable-watch"
        evidence.mkdir()
        calls = []

        def identity(path):
            calls.append(Path(path).absolute())
            return SimpleNamespace(volume_serial=7, file_id=len(calls), attributes=16)

        with patch.object(h.windows_exact_fs, "identity_at_path", side_effect=identity):
            request = h.build_watch_request(
                self.run_root, "SingleFile", "Control", roots, evidence,
                request_token="a" * 64, session_token="b" * 64,
            )
        self.assertEqual(8, len(calls))
        self.assertEqual(list(ROOT_KINDS), [row.root_kind for row in request.roots])
        self.assertEqual(list(range(1, 9)), [row.file_id for row in request.roots])

    def test_watch_and_effect_validation_rejects_changed_or_reused_pid_receipts(self):
        before = _protected()
        one = _receipt(request="watch-request:" + "1" * 64, session="watch-session:" + "2" * 64, worker=31)
        effects = ContainmentEffects(
            written_paths=(self.scratch.absolute(),),
            child_mutation_roots=(self.scratch.absolute(),),
            watcher_pid=31,
            mo2_pid=41,
        )
        h.validate_watch_effect(one, before, before, effects)
        with self.assertRaises(h.GateError):
            h.validate_watch_effect(one, before, ProtectedState(_tree(), "8" * 64, "3" * 64, _tree(), _tree(), _tree()), effects)
        with self.assertRaises(h.GateError):
            h.validate_watch_effect(
                replace(one, evidence_completion=WatchEvidenceCompletion.INCOMPLETE),
                before,
                before,
                effects,
            )
        two = _receipt(request="watch-request:" + "3" * 64, session="watch-session:" + "4" * 64, worker=32)
        h.require_distinct_phase_receipts(((one, effects), (two, ContainmentEffects(watcher_pid=32, mo2_pid=42))))
        with self.assertRaises(h.GateError):
            h.require_distinct_phase_receipts(((one, effects), (two, ContainmentEffects(watcher_pid=32, mo2_pid=41))))

    def test_operator_evidence_requires_reviewable_image_exact_process_and_normal_close(self):
        image = self.scratch / "window.png"
        image.write_bytes(_png_bytes())
        process = {"pid": 41, "creationTime": 101, "executable": str(self.run_root / "SingleFile/app/ModOrganizer.exe")}
        native = {"hwnd": 9001, "pid": 41, "title": "Mod Organizer", "className": "Qt5152QWindowIcon", "visible": True}
        window = {
            "windowId": 51,
            "app": "process:" + process["executable"],
            "title": "Mod Organizer",
            "screenshotId": "shot-1",
            "image": {"path": str(image.absolute()), "sha256": hashlib.sha256(image.read_bytes()).hexdigest(), "size": image.stat().st_size},
            "nativeBefore": native,
            "nativeAfter": native,
        }
        tool = {"loadedTool": "ModLab Capability Probe", "windowId": 51, "screenshotId": "shot-1", "image": window["image"]}
        close = {"action": "Alt+F4", "windowId": 51, "returned": True, "remainingMatches": [], "exitCode": 0}
        stable_image = {**window["image"], "identity": {"volumeSerial": 1, "fileId": 2, "attributes": 0}}
        with patch.object(h, "_stable_file", return_value=stable_image) as stable:
            h.validate_operator_evidence("First", process, window, tool, close)
        stable.assert_called_once_with(image.absolute())
        for changed in (
            {**window, "image": {**window["image"], "sha256": "0" * 64}},
            {**window, "nativeAfter": {**native, "pid": 99}},
            {**window, "title": "Unrelated window"},
        ):
            with self.assertRaises(h.GateError):
                h.validate_operator_evidence("First", process, changed, tool, close)
        with self.assertRaises(h.GateError):
            h.validate_operator_evidence("First", process, window, tool, {**close, "returned": False})
        with self.assertRaises(h.GateError):
            h.validate_operator_evidence("First", process, window, None, close)

        image.write_bytes(b"not a PNG")
        changed_image = {**window, "image": {**window["image"], "sha256": hashlib.sha256(image.read_bytes()).hexdigest(), "size": image.stat().st_size}}
        with patch.object(h, "_stable_file", return_value={**changed_image["image"], "identity": {"volumeSerial": 1, "fileId": 2, "attributes": 0}}), \
                self.assertRaises(h.GateError):
            h.validate_operator_evidence("First", process, changed_image, {**tool, "image": changed_image["image"]}, close)

    def test_screenshot_data_url_requires_bounded_structurally_valid_png(self):
        data = _png_bytes()
        encoded = "data:image/png;base64," + base64.b64encode(data).decode("ascii")
        self.assertEqual(data, h._decode_png_data_url(encoded))
        for invalid in (
            "data:image/png;base64," + base64.b64encode(b"arbitrary bytes").decode("ascii"),
            "data:text/plain;base64," + base64.b64encode(data).decode("ascii"),
            "data:image/png;base64,***",
        ):
            with self.subTest(invalid=invalid[:32]), self.assertRaises(h.GateError):
                h._decode_png_data_url(invalid)

    def test_screenshot_png_requires_one_bounded_decodable_scanline_stream(self):
        def chunk(kind: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

        def png(
            idat: bytes,
            *,
            colour: int = 6,
            interlace: int = 0,
            before_idat: tuple[tuple[bytes, bytes], ...] = (),
            after_idat: tuple[tuple[bytes, bytes], ...] = (),
        ) -> bytes:
            return (
                b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, colour, 0, 0, interlace))
                + b"".join(chunk(kind, payload) for kind, payload in before_idat)
                + chunk(b"IDAT", idat)
                + b"".join(chunk(kind, payload) for kind, payload in after_idat)
                + chunk(b"IEND", b"")
            )

        rgba_scanline = b"\x00\x00\x00\x00\xff"
        for label, invalid in (
            ("invalid-zlib", png(b"not a zlib stream")),
            ("short-scanline", png(zlib.compress(b"\x00\x00"))),
            ("invalid-filter", png(zlib.compress(b"\x05\x00\x00\x00\xff"))),
            ("trailing-stream", png(zlib.compress(rgba_scanline) + zlib.compress(rgba_scanline))),
            ("interlaced", png(zlib.compress(rgba_scanline), interlace=1)),
            ("palette", png(zlib.compress(b"\x00\x00"), colour=3)),
        ):
            with self.subTest(label=label), self.assertRaises(h.GateError):
                h._validate_png_bytes(invalid)

        allowed = (
            (b"gAMA", struct.pack(">I", 45455)),
            (b"sRGB", b"\x00"),
            (b"pHYs", struct.pack(">IIB", 3780, 3780, 1)),
        )
        metadata_stream = zlib.compress(rgba_scanline)
        valid_with_metadata = png(metadata_stream, before_idat=allowed)
        self.assertEqual(valid_with_metadata, h._validate_png_bytes(valid_with_metadata))
        for label, invalid in (
            ("unknown-ancillary", png(metadata_stream, before_idat=((b"aaAA", b"opaque"),))),
            ("malformed-gamma-length", png(metadata_stream, before_idat=((b"gAMA", b"bad"),))),
            ("zero-gamma", png(metadata_stream, before_idat=((b"gAMA", struct.pack(">I", 0)),))),
            ("duplicate-gamma", png(metadata_stream, before_idat=(allowed[0], allowed[0]))),
            ("gamma-after-idat", png(metadata_stream, after_idat=(allowed[0],))),
            ("invalid-srgb-intent", png(metadata_stream, before_idat=((b"sRGB", b"\x04"),))),
            ("invalid-phys-unit", png(metadata_stream, before_idat=((b"pHYs", struct.pack(">IIB", 3780, 3780, 2)),))),
            ("zero-phys-axis", png(metadata_stream, before_idat=((b"pHYs", struct.pack(">IIB", 0, 3780, 1)),))),
            (
                "srgb-gamma-mismatch",
                png(metadata_stream, before_idat=((b"gAMA", struct.pack(">I", 100000)), allowed[1])),
            ),
        ):
            with self.subTest(label=label), self.assertRaises(h.GateError):
                h._validate_png_bytes(invalid)

        header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
        stream = zlib.compress(rgba_scanline)
        for label, invalid in (
            ("unknown-critical", b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"ABCD", b"") + chunk(b"IDAT", stream) + chunk(b"IEND", b"")),
            ("invalid-reserved-bit", b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"abcd", b"") + chunk(b"IDAT", stream) + chunk(b"IEND", b"")),
            ("unsupported-plte", b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"PLTE", b"\x00\x00\x00") + chunk(b"IDAT", stream) + chunk(b"IEND", b"")),
            ("duplicate-ihdr", b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IHDR", header) + chunk(b"IDAT", stream) + chunk(b"IEND", b"")),
            ("nonempty-iend", b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", stream) + chunk(b"IEND", b"x")),
        ):
            with self.subTest(label=label), self.assertRaises(h.GateError):
                h._validate_png_bytes(invalid)

    def test_immutable_publication_exact_reload_and_no_replace(self):
        target = self.scratch / "record.json"
        value = {"schemaVersion": 1, "kind": "offline-fixture"}
        h.publish_exact_json(target, value)
        self.assertEqual(value, h.load_exact_json(target))
        original = target.read_bytes()
        with self.assertRaises(Exception):
            h.publish_exact_json(target, value)
        self.assertEqual(original, target.read_bytes())

    def test_effect_record_id_is_derived_from_exact_service_receipt(self):
        effects = ContainmentEffects(
            written_paths=(self.run_root,), child_mutation_roots=(self.run_root,),
            watcher_pid=31, mo2_pid=41,
        )
        record = h.build_effect_record("SingleFile:Control", effects)
        self.assertRegex(record["effectId"], r"^effect-sha256:[0-9a-f]{64}$")
        self.assertEqual(record, h.validate_effect_record(record))
        with self.assertRaises(h.GateError):
            h.validate_effect_record({**record, "mo2Pid": 99})

    def test_selection_refuses_until_both_complete_candidates_exist(self):
        for candidates in ([], [{"layout": "SingleFile"}], [{"layout": "SingleFile"}, {"layout": "Package"}]):
            matrix = {"candidates": candidates}
            with self.subTest(candidates=len(candidates)), self.assertRaises(h.GateError):
                h.require_complete_matrix(matrix)
        complete = {
            "candidates": [
                {"layout": "SingleFile", "control": {}, "observations": [{}, {}, {}, {}]},
                {"layout": "Package", "control": {}, "observations": [{}, {}, {}, {}]},
            ]
        }
        self.assertIs(complete, h.require_complete_matrix(complete))

    def test_selection_is_delegated_to_the_strict_product_selector(self):
        complete = {
            "candidates": [
                {
                    "layout": "SingleFile", "control": {"loaded": None},
                    "observations": [{"loaded": {"bytecodeDisabled": False}} for _ in range(4)],
                },
                {
                    "layout": "Package", "control": {"loaded": None},
                    "observations": [{"loaded": {"bytecodeDisabled": False}} for _ in range(4)],
                },
            ]
        }
        selected = {"selection": "SingleFile", "capabilityId": "mo2-runtime-capability-sha256:" + "7" * 64}
        calls = []

        def selector(value):
            calls.append(value)
            return selected

        self.assertIs(selected, h.derive_selection(complete, selector=selector))
        self.assertEqual([complete], calls)
        complete["candidates"][1]["observations"][2]["loaded"]["bytecodeDisabled"] = True
        with self.assertRaisesRegex(h.GateError, "bytecodeDisabled"):
            h.derive_selection(complete, selector=selector)

    def test_long_lived_controller_keeps_one_owner_through_watch_stop_and_publication(self):
        backend = _FakeLiveBackend(self.run_root)
        exchange = _FakeExchange()
        result = h.run_operator_phase(
            self.run_root,
            "SingleFile",
            "Control",
            backend=backend,
            exchange=exchange,
        )
        self.assertEqual(
            [
                "source-check", "load-state", "begin:Control", "native-before",
                "exchange-window", "native-after", "exchange-close",
                "finalize:Control", "publish:Control",
            ],
            backend.events,
        )
        self.assertEqual(backend.owner, backend.stop_owner)
        self.assertEqual(31, result.effects.watcher_pid)
        self.assertEqual(41, result.effects.mo2_pid)
        self.assertEqual("observation-sha256:" + "8" * 64, result.value["observationId"])

    def test_candidate_install_controller_requires_receipt_and_publishes_once(self):
        backend = _FakeInstallBackend(self.run_root)
        received = h.install_operator_candidate(
            self.run_root,
            "SingleFile",
            "observation-sha256:" + "8" * 64,
            backend=backend,
        )
        self.assertEqual(["source-check", "load-state", "install", "publish"], backend.events)
        self.assertEqual("installation-sha256:" + "9" * 64, received.value["installationId"])
        self.assertEqual((self.run_root,), received.effects.child_mutation_roots)
        self.assertIn(
            self.run_root / "SingleFile" / "installation.json",
            received.effects.written_paths,
        )

    def test_production_install_publication_receipts_its_own_record(self):
        layout_root = self.run_root / "SingleFile"
        layout_root.mkdir(parents=True)
        base = ContainmentEffects(
            written_paths=(layout_root, layout_root / "app/plugins/probe.py"),
            child_mutation_roots=(layout_root,),
        )
        draft = {
            "controlId": "observation-sha256:" + "1" * 64,
            "declared": [],
            "before": [],
            "after": [],
            "writableBefore": {
                name: {"rootIdentity": {"volumeSerial": 1, "fileId": index, "attributes": 16}, "inventory": []}
                for index, name in enumerate(("App", "Manager", "Environment"), 1)
            },
            "writableAfter": {
                name: {"rootIdentity": {"volumeSerial": 1, "fileId": index, "attributes": 16}, "inventory": []}
                for index, name in enumerate(("App", "Manager", "Environment"), 1)
            },
            "observedAt": "fixture",
        }
        target = layout_root / "installation.json"
        backend = h.ProductionLiveBackend()
        with patch.object(
            h,
            "_load_installation",
            side_effect=lambda *_args: h.load_exact_json(target),
        ) as strict_load, patch.object(
            backend,
            "load_state",
            return_value=h.DurableLayoutState(("Control",), True),
        ) as durable_load:
            received = backend.publish_install(
                self.run_root,
                "SingleFile",
                draft,
                base,
            )
        strict_load.assert_called_once_with(self.run_root.absolute(), "SingleFile")
        durable_load.assert_called_once_with(self.run_root.absolute(), "SingleFile")
        self.assertIsInstance(received, h.ContainmentServiceResult)
        self.assertIn(target, received.effects.written_paths)
        combined = ContainmentEffects.merged(base, received.effects)
        self.assertEqual(
            h.build_effect_record("SingleFile:Install", combined),
            received.value["effect"],
        )
        self.assertEqual(received.value, h.load_exact_json(target))

    def test_production_failure_publications_receipt_their_own_records(self):
        layout_root = self.run_root / "SingleFile"
        job = layout_root / "jobs" / "Control"
        job.mkdir(parents=True)
        backend = h.ProductionLiveBackend()
        backend.fail_install(
            self.run_root,
            "SingleFile",
            "observation-sha256:" + "1" * 64,
            h.GateError("fixture install failure"),
            ContainmentEffects(
                written_paths=(layout_root,),
                child_mutation_roots=(layout_root,),
            ),
        )
        target = layout_root / "installation-failure.json"
        self.assertIn(str(target), h.load_exact_json(target)["effects"]["writtenPaths"])

    def test_installation_reload_requires_exact_control_and_effect_binding(self):
        layout_root = self.run_root / "SingleFile"
        control_job = layout_root / "jobs" / "Control"
        control_job.mkdir(parents=True)
        observation = {"after": [], "runtimeAfter": []}
        control_id = "observation-sha256:" + h._sha256(h._canonical(observation))
        h.publish_exact_json(control_job / "observation.json", observation)
        h.publish_exact_json(control_job / "phase-final.json", {"observationId": control_id})
        effect = h.build_effect_record(
            "SingleFile:Install",
            ContainmentEffects(
                written_paths=(layout_root, layout_root / "installation.json"),
                child_mutation_roots=(layout_root,),
            ),
        )
        record = {
            "schemaVersion": 1,
            "kind": "task-7B-candidate-installation",
            "runId": self.run_root.name,
            "layout": "SingleFile",
            "controlId": control_id,
            "declared": [],
            "before": [],
            "after": [],
            "writableBefore": {
                name: {"rootIdentity": {"volumeSerial": 1, "fileId": index, "attributes": 16}, "inventory": []}
                for index, name in enumerate(("App", "Manager", "Environment"), 1)
            },
            "writableAfter": {
                name: {"rootIdentity": {"volumeSerial": 1, "fileId": index, "attributes": 16}, "inventory": []}
                for index, name in enumerate(("App", "Manager", "Environment"), 1)
            },
            "observedAt": "fixture",
            "effect": effect,
            "authority": False,
        }
        record["installationId"] = "installation-sha256:" + h._sha256(h._canonical(record))
        h.publish_exact_json(layout_root / "installation.json", record)
        self.assertEqual(record, h._load_installation(self.run_root, "SingleFile"))
        forged = {**record, "controlId": "observation-sha256:" + "0" * 64}
        body = {key: item for key, item in forged.items() if key != "installationId"}
        forged["installationId"] = "installation-sha256:" + h._sha256(h._canonical(body))

        original = h.load_exact_json
        def forged_load(path):
            return forged if Path(path).name == "installation.json" else original(path)

        with patch.object(h, "load_exact_json", side_effect=forged_load), self.assertRaises(h.GateError):
            h._load_installation(self.run_root, "SingleFile")

        forged_schema = {**record, "schemaVersion": h.SCHEMA_VERSION + 1}
        body = {key: item for key, item in forged_schema.items() if key != "installationId"}
        forged_schema["installationId"] = "installation-sha256:" + h._sha256(h._canonical(body))

        def forged_schema_load(path):
            return forged_schema if Path(path).name == "installation.json" else original(path)

        with patch.object(h, "load_exact_json", side_effect=forged_schema_load), self.assertRaises(h.GateError):
            h._load_installation(self.run_root, "SingleFile")

    def test_candidate_install_failure_is_terminal_and_cannot_be_reused(self):
        backend = _FakeInstallBackend(self.run_root)
        backend.fail_install_requested = True
        with self.assertRaises(ContainmentOperationError):
            h.install_operator_candidate(
                self.run_root,
                "SingleFile",
                "observation-sha256:" + "8" * 64,
                backend=backend,
            )
        self.assertTrue(backend.terminal)
        self.assertIn("fail-install", backend.events)
        with self.assertRaises(h.GateError):
            h.install_operator_candidate(
                self.run_root,
                "SingleFile",
                "observation-sha256:" + "8" * 64,
                backend=backend,
            )

    def test_json_line_exchange_supports_binary_controller_streams(self):
        supplied = {
            "windowId": 51,
            "app": "process:fixture",
            "title": "Mod Organizer",
            "screenshotId": "shot-1",
            "screenshotDataUrl": "data:image/png;base64," + base64.b64encode(_png_bytes()).decode("ascii"),
            "loadedTool": None,
            "observedAt": "2026-09-04T00:00:00Z",
        }
        input_stream = io.BytesIO((json.dumps(supplied) + "\n").encode("utf-8"))
        output_stream = io.BytesIO()
        session = SimpleNamespace(
            run_root=self.run_root,
            layout="SingleFile",
            phase="Control",
            launch=SimpleNamespace(pid=41, creation_time=101),
            executable="fixture",
        )
        received = h.JsonLineExchange(input_stream, output_stream).window(session)
        self.assertEqual(supplied, received)
        prompt = json.loads(output_stream.getvalue().decode("utf-8"))
        self.assertEqual("capture-visible-window", prompt["operatorAction"])

    def test_cli_parser_exposes_only_bounded_operator_actions(self):
        status = h._parse_cli(["status", "--run-id", "a" * 32])
        self.assertEqual("status", status.command)
        phase = h._parse_cli([
            "phase", "--run-id", "a" * 32, "SingleFile", "Control",
        ])
        self.assertEqual(("SingleFile", "Control"), (phase.layout, phase.phase))
        cleanup = h._parse_cli([
            "cleanup", "--run-id", "a" * 32, "SingleFile", "Control",
        ])
        self.assertEqual(("SingleFile", "Control"), (cleanup.layout, cleanup.phase))
        restart = h._parse_cli([
            "restart", "--run-id", "a" * 32, "--new-run-id", "b" * 32,
            "--archive", str(self.archive), "--steam-root", str(self.steam),
        ])
        self.assertEqual("b" * 32, restart.new_run_id)
        with patch.object(sys, "stderr", io.StringIO()), self.assertRaises(SystemExit):
            h._parse_cli(["phase", "--run-id", "not-fresh", "SingleFile", "Control"])

    def test_cli_script_bootstraps_repository_imports_without_mutation(self):
        completed = subprocess.run(
            [sys.executable, "-B", str(HARNESS_PATH), "--help"],
            cwd=self.scratch,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("Disposable non-authorizing MO2 full-stack capability gate", completed.stdout)
        self.assertFalse(self.run_root.exists())

    def test_retained_log_targets_are_bounded_before_publication(self):
        targets = h._retained_log_targets(self.run_root, h.MAX_RETAINED_LOGS)
        self.assertEqual(h.MAX_RETAINED_LOGS, len(targets))
        self.assertEqual("observed-000.log", targets[0].name)
        with self.assertRaises(h.GateError):
            h._retained_log_targets(self.run_root, h.MAX_RETAINED_LOGS + 1)

    def test_production_input_binding_refuses_before_archive_read_or_mutation(self):
        config = h.GateConfig(
            self.run_root,
            self.archive,
            self.steam,
            {"commit": "1" * 40, "tree": "2" * 40},
            {"SingleFile": "bootstrap-job:" + "1" * 32, "Package": "bootstrap-job:" + "2" * 32},
        )
        with patch.object(h, "load_mo2_release") as release, self.assertRaises(h.GateError):
            h.ProductionBackend().inspect_inputs(config)
        release.assert_not_called()
        self.assertFalse(self.run_root.exists())

    def test_production_backend_runs_real_import_prepare_and_apply_fixture_stack(self):
        """Cover the production backend wiring while substituting only external command seams."""
        from modlab.workflows.skyrim import mo2_bootstrap as product_bootstrap

        fixture = BootstrapPlanningFixture(self.scratch / "production-fixture")
        source_archive = self.scratch / "Mod.Organizer-2.5.2.7z"
        retained = fixture.workspace.joinpath(
            *next((fixture.workspace / "library" / "archives").rglob("payload.7z")).relative_to(fixture.workspace).parts
        )
        source_archive.write_bytes(retained.read_bytes())
        workspace = self.scratch / "production-backend-workspace"
        backend = h.ProductionBackend(
            release_path=fixture.release_path,
            extractor_path=Path(r"C:\Windows\System32\tar.exe"),
        )

        def prepare_real(**kwargs):
            with patch.object(product_bootstrap, "load_mo2_release", return_value=fixture.release):
                return product_bootstrap.prepare_mo2_setup(
                    **kwargs,
                    documents_root=fixture.documents_root,
                    version_reader=fixture.version_reader,
                    process_inspector=lambda _root: fixture.processes,
                    command_runner=fixture.runner,
                    free_space_reader=lambda _root: fixture.free_bytes,
                )

        extraction_runner = FakeRunner.for_extraction(fixture.package_files)
        extraction_runner.responses["--version"] = subprocess.CompletedProcess(
            (), 0, b"bsdtar 3.8.8 - libarchive fixture\n", b""
        )

        def apply_real(plan_id, workspace_root, **kwargs):
            with patch.object(product_bootstrap, "load_mo2_release", return_value=fixture.release):
                return product_bootstrap.apply_mo2_setup(
                    plan_id,
                    workspace_root,
                    **kwargs,
                    documents_root=fixture.documents_root,
                    version_reader=fixture.version_reader,
                    process_inspector=lambda _root: fixture.processes,
                    command_runner=extraction_runner,
                    free_space_reader=lambda _root: fixture.free_bytes,
                )

        artifact = backend.import_archive("SingleFile", workspace, source_archive)
        with patch.object(h, "prepare_mo2_setup", side_effect=prepare_real), \
                patch.object(h, "apply_mo2_setup", side_effect=apply_real):
            planned = backend.prepare_setup("SingleFile", artifact.artifact_id, workspace, fixture.steam_root)
            applied = backend.apply_setup(
                "SingleFile",
                planned.plan.plan_id,
                workspace,
                "bootstrap-job:" + "d" * 32,
            )

        self.assertEqual(BootstrapDisposition.CREATE, planned.plan.disposition)
        self.assertEqual(BootstrapReceiptMode.CREATED, applied.receipt.mode)
        self.assertEqual(BootstrapJobState.VERIFIED, applied.journal.state)
        self.assertEqual("bootstrap-job:" + "d" * 32, applied.receipt.job_id)
        self.assertEqual(applied.receipt, receipt_from_dict(receipt_to_dict(applied.receipt)))
        self.assertEqual(applied.journal, journal_from_dict(journal_to_dict(applied.journal)))
        self.assertTrue((workspace / "tools" / "mo2" / "skyrim-se-ae" / "app" / "ModOrganizer.exe").is_file())

        summarized = h.containment_service._receipted(h._bootstrap_summary)(
            planned,
            applied,
            workspace,
        )
        for field in ("paths_written", "manager_changes"):
            for item in summarized.value["applicationEffects"][field]:
                self.assertTrue(h._inside(Path(item), workspace))
                self.assertTrue(Path(item).is_absolute())
        self.assertEqual(
            summarized.value["applicationEffects"],
            h._bootstrap_effect(
                summarized.value["applicationEffects"],
                "real fixture application effect",
                workspace,
            ),
        )

    def test_bootstrap_summary_rejects_unsafe_or_colliding_product_paths(self):
        workspace = self.scratch / "bootstrap-summary"
        workspace.mkdir()
        safe = workspace / "plan.json"
        safe.write_bytes(b"fixture")
        planned = SimpleNamespace(
            plan=SimpleNamespace(disposition=BootstrapDisposition.CREATE),
            paths_written=(safe,),
            downloads=(),
            installations=(),
            manager_changes=(),
            game_changes=(),
            programs_launched=(),
        )

        def applied(paths, changes=()):
            return SimpleNamespace(
                outcome=BootstrapReceiptMode.CREATED.value,
                receipt=SimpleNamespace(mode=BootstrapReceiptMode.CREATED),
                journal=SimpleNamespace(state=BootstrapJobState.VERIFIED),
                paths_written=paths,
                downloads=(),
                installations=("portable-mo2-create",),
                manager_changes=changes,
                game_changes=(),
                programs_launched=(),
            )

        for paths, changes in (
            (("../escape.json",), ()),
            ((r"runtime\escape.json",), ()),
            ((r"C:\outside.json",), ()),
            (("runtime/./escape.json",), ()),
            (("runtime/a.json", "RUNTIME/A.JSON"), ()),
            (("runtime/a.json",), ("tools/mo2/../escape.bin",)),
            (("runtime/file:stream",), ()),
            (("runtime/NUL",), ()),
            (("runtime/name.",), ()),
            (("runtime/name ",), ()),
            (("runtime/*.json",), ()),
            (("runtime/NAME~1/file.json",), ()),
            (((workspace / "runtime" / "file:stream").absolute(),), ()),
            (((workspace / "runtime" / "NUL").absolute(),), ()),
            (((workspace / "runtime" / "name.").absolute(),), ()),
            (((workspace / "runtime" / "NAME~1" / "file.json").absolute(),), ()),
        ):
            with self.subTest(paths=paths, changes=changes), self.assertRaises(ContainmentOperationError):
                h.containment_service._receipted(h._bootstrap_summary)(
                    planned,
                    applied(paths, changes),
                    workspace,
                )

    def test_layout_record_rejects_manager_root_substitution(self):
        preparation = {
            "runRoot": str(self.run_root),
            "layouts": [{
                "layout": "SingleFile",
                "workspace": str(self.run_root / "SingleFile" / "bootstrap"),
                "managerRoot": str(self.run_root / "substituted-manager"),
                "appRoot": str(self.run_root / "SingleFile" / "app"),
            }],
        }
        with self.assertRaises(h.GateError):
            h._layout_record(preparation, "SingleFile")

    def test_preparation_reload_rejects_recomputed_cross_binding_forgery(self):
        backend = _FakeBackend(self.scratch, _listing("ModOrganizer.exe", "plugins/base.py"))
        source = {"commit": "1" * 40, "tree": "2" * 40}
        config = h.GateConfig(
            self.run_root,
            self.archive,
            self.steam,
            source,
            {"SingleFile": "bootstrap-job:" + "1" * 32, "Package": "bootstrap-job:" + "2" * 32},
        )
        record = h.prepare_gate(config, backend=backend).value
        release = SimpleNamespace(
            sha256="6" * 64,
            descriptor=SimpleNamespace(
                archive_sha256=record["archive"]["sha256"],
                archive_size=record["archive"]["size"],
                archive_name=record["archive"]["originalName"],
                executable=SimpleNamespace(
                    file_version="2.5.2.0",
                    sha256=record["mo2"]["sha256"],
                    size=record["mo2"]["size"],
                ),
            ),
        )
        original_load = h.load_exact_json
        effect = original_load(self.run_root / "preparation-effect.json")
        original_stable = h._stable_file

        def stable(path):
            if Path(path).absolute() == backend.extractor_path:
                return {
                    "path": str(backend.extractor_path),
                    "sha256": backend.extractor_sha256,
                    "size": backend.extractor_size,
                    "identity": {"volumeSerial": 1, "fileId": 1, "attributes": 32},
                }
            return original_stable(path)

        def forged_load(value):
            return lambda path: value if Path(path).name == "preparation.json" else (
                effect if Path(path).name == "preparation-effect.json" else original_load(path)
            )

        with patch.object(h, "capability_run_root", return_value=self.run_root), \
                patch.object(h, "_git_source", return_value=source), \
                patch.object(h, "load_mo2_release", return_value=release), \
                patch.object(h, "inspect_path_integrity", return_value=IntegrityLevel.LOW), \
                patch.object(h, "_stable_file", side_effect=stable):
            self.assertEqual(record, h._load_preparation(self.run_root))
            forged = json.loads(json.dumps(record))
            forged["sourceArtifactManifest"]["archiveSha256"] = "0" * 64
            forged["sourceArtifactId"] = h.source_artifact_id_for(forged["sourceArtifactManifest"])
            body = {key: item for key, item in forged.items() if key != "preparationId"}
            forged["preparationId"] = "preparation-sha256:" + h._sha256(h._canonical(body))
            with patch.object(h, "load_exact_json", side_effect=forged_load(forged)), self.assertRaises(h.GateError):
                h._load_preparation(self.run_root)
            for label, mutate in (
                ("game root", lambda item: item.__setitem__("gameRoot", str(self.scratch / "other-game"))),
                (
                    "relocation destination",
                    lambda item: item["layouts"][0]["relocation"].__setitem__(
                        "destination", str(self.scratch / "substituted-app")
                    ),
                ),
                (
                    "bootstrap receipt job",
                    lambda item: item["layouts"][0]["bootstrap"]["receipt"].__setitem__(
                        "jobId", "bootstrap-job:" + "f" * 32
                    ),
                ),
            ):
                with self.subTest(label=label):
                    forged = json.loads(json.dumps(record))
                    mutate(forged)
                    body = {key: item for key, item in forged.items() if key != "preparationId"}
                    forged["preparationId"] = "preparation-sha256:" + h._sha256(h._canonical(body))
                    with patch.object(h, "load_exact_json", side_effect=forged_load(forged)), self.assertRaises(h.GateError):
                        h._load_preparation(self.run_root)

    def test_preparation_reload_rejects_product_valid_nested_and_effect_substitutions(self):
        backend = _FakeBackend(self.scratch, _listing("ModOrganizer.exe", "plugins/base.py"))
        source = {"commit": "1" * 40, "tree": "2" * 40}
        record = h.prepare_gate(
            h.GateConfig(
                self.run_root,
                self.archive,
                self.steam,
                source,
                {"SingleFile": "bootstrap-job:" + "1" * 32, "Package": "bootstrap-job:" + "2" * 32},
            ),
            backend=backend,
        ).value
        effect = h.load_exact_json(self.run_root / "preparation-effect.json")
        release = SimpleNamespace(
            sha256="6" * 64,
            descriptor=SimpleNamespace(
                archive_sha256=record["archive"]["sha256"],
                archive_size=record["archive"]["size"],
                archive_name=record["archive"]["originalName"],
                executable=SimpleNamespace(
                    file_version="2.5.2.0",
                    sha256=record["mo2"]["sha256"],
                    size=record["mo2"]["size"],
                ),
            ),
        )
        original_load = h.load_exact_json
        original_stable = h._stable_file

        def stable(path):
            if Path(path).absolute() == backend.extractor_path:
                return {
                    "path": str(backend.extractor_path),
                    "sha256": backend.extractor_sha256,
                    "size": backend.extractor_size,
                    "identity": {"volumeSerial": 1, "fileId": 1, "attributes": 32},
                }
            return original_stable(path)

        def reidentify(value):
            body = {key: item for key, item in value.items() if key != "preparationId"}
            value["preparationId"] = "preparation-sha256:" + h._sha256(h._canonical(body))

        def load_with(preparation, preparation_effect):
            def load(path):
                name = Path(path).name
                if name == "preparation.json":
                    return preparation
                if name == "preparation-effect.json":
                    return preparation_effect
                return original_load(path)
            return load

        attacks = []

        def artifact_metadata(item):
            item["layouts"][0]["artifact"]["sourceNote"] = "substituted but product-valid"

        attacks.append(("artifact metadata", artifact_metadata))

        def plan_game(item):
            plan = plan_from_dict(item["layouts"][0]["bootstrap"]["plan"])
            changed = replace(plan, game_root=str(self.scratch / "other-game"))
            changed = replace(changed, plan_id=plan_id_for(changed))
            item["layouts"][0]["bootstrap"]["plan"] = plan_to_dict(changed)

        attacks.append(("recomputed bootstrap plan", plan_game))

        def receipt_job(item):
            row = item["layouts"][0]
            bootstrap = row["bootstrap"]
            new_job = "bootstrap-job:" + "e" * 32
            receipt = replace(receipt_from_dict(bootstrap["receipt"]), job_id=new_job)
            receipt = replace(receipt, receipt_id=receipt_id_for(receipt))
            journal = replace(
                journal_from_dict(bootstrap["journal"]),
                job_id=new_job,
                stage_root=str(Path(row["managerRoot"]).parent / stage_name(new_job, 2)),
                prior_root=str(Path(row["workspace"]) / "runtime" / "jobs" / "mo2-bootstrap" / ("e" * 32) / "prior"),
                receipt_id=receipt.receipt_id,
            )
            item["bootstrapJobIds"]["SingleFile"] = new_job
            bootstrap["receipt"] = receipt_to_dict(receipt)
            bootstrap["journal"] = journal_to_dict(journal)

        attacks.append(("recomputed bootstrap job receipt and journal", receipt_job))
        attacks.extend((
            ("archive source identity", lambda item: item["archive"]["identity"].__setitem__("fileId", 999)),
            ("game root identity", lambda item: item["game"]["rootIdentity"].__setitem__("fileId", 999)),
            ("relocation identity", lambda item: item["layouts"][0]["relocation"]["destinationIdentity"].__setitem__("fileId", 999)),
            ("configured root", lambda item: item["layouts"][0]["configuration"]["roots"].__setitem__(0, str(self.scratch / "other-downloads"))),
            ("manager config identity", lambda item: item["layouts"][0]["configuration"]["modOrganizerIni"].__setitem__("sha256", "0" * 64)),
            ("noregister identity", lambda item: item["layouts"][0]["configuration"]["noregister"].__setitem__("sha256", "0" * 64)),
            ("runtime identity", lambda item: item["layouts"][0]["runtime"]["files"][0].__setitem__("sha256", "0" * 64)),
            ("extra nested field", lambda item: item["layouts"][0].__setitem__("unreviewed", True)),
        ))

        common = (
            patch.object(h, "capability_run_root", return_value=self.run_root),
            patch.object(h, "_git_source", return_value=source),
            patch.object(h, "load_mo2_release", return_value=release),
            patch.object(h, "inspect_path_integrity", return_value=IntegrityLevel.LOW),
            patch.object(h, "_stable_file", side_effect=stable),
        )
        with ExitStack() as stack:
            for context in common:
                stack.enter_context(context)
            self.assertEqual(record, h._load_preparation(self.run_root))
            for label, mutate in attacks:
                with self.subTest(label=label):
                    forged = json.loads(json.dumps(record))
                    mutate(forged)
                    reidentify(forged)
                    with patch.object(h, "load_exact_json", side_effect=load_with(forged, effect)), self.assertRaises(h.GateError):
                        h._load_preparation(self.run_root)

            forged_effect = json.loads(json.dumps(effect))
            forged_effect["effect"]["scope"] = "SubstitutedPreparation"
            effect_body = {key: item for key, item in forged_effect["effect"].items() if key != "effectId"}
            forged_effect["effect"]["effectId"] = "effect-sha256:" + h._sha256(h._canonical(effect_body))
            forged = json.loads(json.dumps(record))
            forged["preparationEffectId"] = forged_effect["effect"]["effectId"]
            reidentify(forged)
            forged_effect["preparationId"] = forged["preparationId"]
            with patch.object(h, "load_exact_json", side_effect=load_with(forged, forged_effect)), self.assertRaises(h.GateError):
                h._load_preparation(self.run_root)

            manager_ini = Path(record["layouts"][0]["appRoot"]) / "ModOrganizer.ini"
            original_manager_ini = manager_ini.read_bytes()
            manager_ini.write_bytes(original_manager_ini.replace(b"base_directory=", b"base_directory=redirected-"))
            with self.assertRaises(h.GateError):
                h._load_preparation(self.run_root)
            manager_ini.write_bytes(original_manager_ini)

            artifact = record["layouts"][0]["artifact"]
            payload = Path(record["layouts"][0]["workspace"]).joinpath(*artifact["storedRelativePath"].split("/"))
            payload.write_bytes(b"substituted retained payload")
            with self.assertRaises(h.GateError):
                h._load_preparation(self.run_root)

    def test_production_phase_begin_receipts_processes_and_integrity_commands(self):
        layout_root = self.run_root / "SingleFile"
        app = layout_root / "app"
        manager = layout_root / "bootstrap" / "tools" / "mo2" / "skyrim-se-ae"
        (app / "plugins").mkdir(parents=True)
        manager.mkdir(parents=True)
        (layout_root / "environment").mkdir(parents=True)
        preparation = {
            "preparationId": "preparation-sha256:" + "1" * 64,
            "runRoot": str(self.run_root),
            "gameRoot": str(self.steam / "steamapps/common/Skyrim Special Edition"),
            "layouts": [{
                "layout": "SingleFile",
                "workspace": str(layout_root / "bootstrap"),
                "appRoot": str(app),
                "managerRoot": str(manager),
            }],
        }
        job = layout_root / "jobs" / "Control"
        request = WatchRequest(
            "watch-request:" + "2" * 64,
            "watch-session:" + "3" * 64,
            "containment-run:" + "a" * 32,
            ContainmentScenario.NEW_FOLDER,
            job / "watch",
            job / "watch" / "stop.token",
            (),
        )
        launch = SimpleNamespace(
            pid=41,
            creation_time=101,
            executable=str(app / "ModOrganizer.exe"),
            integrity=IntegrityLevel.LOW,
            arguments=("--profile", "ModLab - Lab"),
            working_directory=str(app),
        )
        integrity_receipt = SimpleNamespace(
            executable=r"C:\Windows\System32\icacls.exe",
            arguments=("fixture",),
            exit_code=0,
            stdout_sha256="4" * 64,
            stderr_sha256="5" * 64,
            integrity=IntegrityLevel.LOW,
        )

        def start(_request, *, on_created):
            on_created(31)

        def launch_process(*_args, on_created, **_kwargs):
            on_created(41)
            return launch

        backend = h.ProductionLiveBackend()
        with patch.object(h, "_load_preparation", return_value=preparation), \
                patch.object(backend, "load_state", return_value=h.DurableLayoutState((), False)), \
                patch.object(h, "snapshot_tree", return_value=[]), \
                patch.object(h, "_runtime_inventory", return_value=[]), \
                patch.object(h, "_capture_protected", return_value=_protected()), \
                patch.object(h, "_derived_watch_roots", return_value={}), \
                patch.object(h, "build_watch_request", return_value=request), \
                patch.object(h, "set_low_integrity_tree", return_value=integrity_receipt), \
                patch.object(h, "inspect_path_integrity", return_value=IntegrityLevel.LOW), \
                patch.object(h, "start_watch", side_effect=start), \
                patch.object(h, "launch_low_integrity_process", side_effect=launch_process), \
                patch.object(h.windows_watch, "_verified_process_handle", return_value=777):
            received = backend.begin_phase(self.run_root, "SingleFile", "Control")
        self.assertEqual((31, 41), (received.effects.watcher_pid, received.effects.mo2_pid))
        self.assertEqual(2, len(received.effects.preparation_processes))
        self.assertTrue(all(item.startswith("icacls-low:") for item in received.effects.preparation_processes))
        self.assertEqual(777, received.value.process_handle)
        self.assertTrue((job / "process.json").is_file())

    def test_production_failure_uses_pending_session_and_stops_dead_process_watch(self):
        job = self.run_root / "SingleFile" / "jobs" / "Control"
        watch = job / "watch"
        watch.mkdir(parents=True)
        request = SimpleNamespace(
            evidence_root=watch,
            request_id="watch-request:" + "2" * 64,
            session_id="watch-session:" + "3" * 64,
            run_id="containment-run:" + self.run_root.name,
            scenario=ContainmentScenario.NEW_FOLDER,
        )
        session = SimpleNamespace(
            run_root=self.run_root,
            layout="SingleFile",
            phase="Control",
            job_root=job,
            request=request,
            process_handle=777,
            launch=SimpleNamespace(pid=41, creation_time=101),
        )
        backend = h.ProductionLiveBackend()
        backend._pending_session = session
        with patch.object(h.windows_watch._kernel32, "WaitForSingleObject", return_value=h.windows_watch._WAIT_OBJECT_0), \
                patch.object(h.windows_watch, "_close_controller_handle", return_value=None), \
                patch.object(
                    h,
                    "stop_watch",
                    return_value=_receipt(
                        request=request.request_id,
                        session=request.session_id,
                        worker=31,
                    ),
                ) as stop:
            backend.fail_phase(
                self.run_root,
                "SingleFile",
                "Control",
                h.GateError("fixture failure"),
                ContainmentEffects(
                    written_paths=(self.run_root,),
                    child_mutation_roots=(self.run_root,),
                    watcher_pid=31,
                    mo2_pid=41,
                ),
            )
        stop.assert_called_once_with(watch / "request.json")
        failure = h.load_exact_json(job / "failure.json")
        self.assertFalse(failure["processMayStillBeLive"])
        self.assertTrue(failure["watchCleanupPending"])
        self.assertIn(str(job / "failure.json"), failure["effects"]["writtenPaths"])
        self.assertIs(session, backend._pending_session)

    def test_failed_phase_retains_live_handle_and_incomplete_watcher_owner(self):
        for case in ("live-process", "incomplete-watch", "mismatched-watch", "close-failure"):
            with self.subTest(case=case):
                root = self.scratch / case / ("a" * 32)
                job = root / "SingleFile" / "jobs" / "Control"
                watch = job / "watch"
                watch.mkdir(parents=True)
                request = SimpleNamespace(
                    evidence_root=watch,
                    request_id="watch-request:" + "2" * 64,
                    session_id="watch-session:" + "3" * 64,
                    run_id="containment-run:" + root.name,
                    scenario=ContainmentScenario.NEW_FOLDER,
                )
                session = SimpleNamespace(
                    run_root=root,
                    layout="SingleFile",
                    phase="Control",
                    job_root=job,
                    request=request,
                    process_handle=777,
                    launch=SimpleNamespace(pid=41, creation_time=101),
                    watcher_pid=31,
                    watcher_created=True,
                )
                backend = h.ProductionLiveBackend()
                backend._pending_session = session
                receipt = _receipt(
                    request=request.request_id,
                    session=("watch-session:" + "9" * 64 if case == "mismatched-watch" else request.session_id),
                    worker=31,
                )
                if case == "incomplete-watch":
                    receipt = replace(
                        receipt,
                        evidence_completion=WatchEvidenceCompletion.INCOMPLETE,
                        watch_outcome_id=None,
                        worker_exit_code=None,
                        error="fixture cleanup incomplete",
                    )
                wait = h.windows_watch._WAIT_TIMEOUT if case == "live-process" else h.windows_watch._WAIT_OBJECT_0
                close_error = "fixture handle close failure" if case == "close-failure" else None
                with patch.object(h.windows_watch._kernel32, "WaitForSingleObject", return_value=wait), \
                        patch.object(h.windows_watch, "_close_controller_handle", return_value=close_error) as close, \
                        patch.object(h, "stop_watch", return_value=receipt) as stop:
                    backend.fail_phase(
                        root,
                        "SingleFile",
                        "Control",
                        h.GateError("fixture phase failure"),
                        ContainmentEffects(watcher_pid=31, mo2_pid=41),
                    )
                failure = h.load_exact_json(job / "failure.json")
                self.assertIs(session, backend._pending_session)
                self.assertEqual(case == "live-process", failure["processMayStillBeLive"])
                if case == "live-process":
                    self.assertEqual(777, session.process_handle)
                    close.assert_not_called()
                    stop.assert_not_called()
                else:
                    close.assert_called_once()
                    stop.assert_called_once()
                if case in {"incomplete-watch", "mismatched-watch"}:
                    self.assertEqual(0, session.process_handle)
                    self.assertTrue(failure["watchCleanupPending"])
                elif case == "close-failure":
                    self.assertEqual(777, session.process_handle)
                    self.assertTrue(failure["watchCleanupPending"])
                    self.assertTrue(failure["cleanup"]["processHandleCleanupPending"])
                    self.assertTrue(any("handle close failure" in item for item in failure["cleanupErrors"]))
                else:
                    self.assertTrue(failure["watchCleanupPending"])
                if case == "incomplete-watch":
                    self.assertTrue(any("fixture cleanup incomplete" in item for item in failure["cleanupErrors"]))
                if case == "mismatched-watch":
                    self.assertTrue(any("exact completed receipt" in item for item in failure["cleanupErrors"]))

    def test_cleanup_only_restart_preserves_failure_and_creates_fresh_attempt(self):
        old_root = self.scratch / ("a" * 32)
        new_root = self.scratch / ("b" * 32)
        old_jobs = {
            "SingleFile": "bootstrap-job:" + "1" * 32,
            "Package": "bootstrap-job:" + "2" * 32,
        }
        h.prepare_gate(
            h.GateConfig(
                old_root,
                self.archive,
                self.steam,
                {"commit": "1" * 40, "tree": "2" * 40},
                old_jobs,
            ),
            backend=_FakeBackend(self.scratch, _listing("ModOrganizer.exe")),
        )
        job = old_root / "SingleFile" / "jobs" / "Control"
        watch = job / "watch"
        watch.mkdir(parents=True)
        watched_fixture = old_root / "fixture-roots" / "shared"
        watched_fixture.mkdir(parents=True)
        physical_root = h.windows_watch.watch_root("SourceMods", watched_fixture)
        watch_roots = tuple(
            replace(physical_root, root_kind=kind) for kind in ROOT_KINDS
        )
        request = WatchRequest(
            "watch-request:" + "3" * 64,
            "watch-session:" + "4" * 64,
            "containment-run:" + old_root.name,
            ContainmentScenario.NEW_FOLDER,
            watch,
            watch / "stop.token",
            watch_roots,
        )
        (watch / "request.json").write_bytes(h.watch_request_to_bytes(request))
        request_sha256 = h.watch_request_sha256(request)
        claim = ControllerClaim(
            schema_version=1,
            request_sha256=request_sha256,
            session_id=request.session_id,
            run_id=request.run_id,
            scenario=request.scenario,
            request_path=watch / "request.json",
            worker_command=watch_worker_command(watch / "request.json"),
            controller_pid=29,
            controller_creation_time=99,
        )
        launch = WorkerLaunch(
            schema_version=1,
            request_sha256=request_sha256,
            session_id=request.session_id,
            run_id=request.run_id,
            scenario=request.scenario,
            worker_pid=31,
            worker_creation_time=100,
        )
        (watch / "controller-claim.json").write_bytes(controller_claim_to_bytes(claim, request))
        (watch / "worker-launch.json").write_bytes(worker_launch_to_bytes(launch, request))
        captured = h.windows_watch._CapturedWatchEvidence(
            ready=True,
            opened_root_kinds=ROOT_KINDS,
            events=(),
            journal=h.windows_watch._JournalEvidence(1, 2, b"", 0, 0),
            terminal_bytes_sha256="6" * 64,
            root_identities_unchanged=True,
            completion_reasons=("controller-session-lost",),
        )
        self.assertFalse((watch / "outcome.json").exists())
        owner = SimpleNamespace(
            run_root=old_root,
            layout="SingleFile",
            phase="Control",
            job_root=job,
            request=request,
            process_handle=777,
            launch=SimpleNamespace(
                pid=41,
                creation_time=101,
                executable=str(old_root / "SingleFile" / "app" / "ModOrganizer.exe"),
            ),
            watcher_pid=31,
            watcher_created=True,
        )
        failed_backend = h.ProductionLiveBackend()
        failed_backend._pending_session = owner
        with patch.object(
            h.windows_watch._kernel32,
            "WaitForSingleObject",
            return_value=h.windows_watch._WAIT_TIMEOUT,
        ):
            failed_backend.fail_phase(
                old_root,
                "SingleFile",
                "Control",
                h.GateError("fixture live failure"),
                ContainmentEffects(watcher_pid=31, mo2_pid=41),
            )
        failure_path = job / "failure.json"
        failure_before = failure_path.read_bytes()
        failure = h.load_exact_json(failure_path)
        self.assertTrue(failure["processMayStillBeLive"])
        self.assertIn("failureId", failure)
        forged_failure = json.loads(json.dumps(failure))
        forged_failure["cleanup"]["watcherCreated"] = False
        body = {key: value for key, value in forged_failure.items() if key != "failureId"}
        forged_failure["failureId"] = "phase-failure-sha256:" + h._sha256(h._canonical(body))
        original_load = h.load_exact_json

        def forged_failure_load(path):
            return forged_failure if Path(path).absolute() == failure_path.absolute() else original_load(path)

        with patch.object(h, "load_exact_json", side_effect=forged_failure_load), \
                self.assertRaises(h.GateError):
            h._load_phase_failure(old_root, "SingleFile", "Control")

        safe_outcome = _cleanup_outcome(
            request,
            31,
            completion=WatchEvidenceCompletion.INCOMPLETE,
            reasons=("controller-session-lost",),
        )
        event = WatcherEvent(1, "SourceMods", "Added", "breach.txt")
        event_bytes = h._canonical({
            "action": event.action,
            "relativePath": event.relative_path,
            "rootKind": event.root_kind,
            "sequence": event.sequence,
        })
        unsafe_outcomes = (
            replace(
                safe_outcome,
                root_identities_unchanged=False,
                reason_codes=("controller-session-lost", "root-identity-changed"),
            ),
            replace(
                safe_outcome,
                events=(event,),
                event_bytes_sha256=hashlib.sha256(event_bytes).hexdigest(),
                journal_byte_count=len(event_bytes),
                journal_event_count=1,
                journal_final_sequence=1,
            ),
            replace(safe_outcome, reason_codes=("worker-cleanup-refused",)),
            replace(
                safe_outcome,
                reason_codes=("controller-session-lost", "worker-cleanup-refused"),
            ),
            replace(
                safe_outcome,
                ready=False,
                opened_root_kinds=(),
                root_identities_unchanged=False,
                reason_codes=("controller-session-lost", "worker-ready-invalid"),
            ),
        )
        original_read = h.read_exact
        for unsafe_outcome in unsafe_outcomes:
            unsafe_bytes = h.watch_outcome_to_bytes(unsafe_outcome)
            unsafe_id = h.watch_outcome_id_for(unsafe_outcome)

            def read_unsafe(path, *, data=unsafe_bytes):
                return data if Path(path).absolute() == (watch / "outcome.json").absolute() else original_read(path)

            with self.subTest(reasons=unsafe_outcome.reason_codes), \
                    patch.object(h, "read_exact", side_effect=read_unsafe), \
                    self.assertRaises(h.GateError):
                h._load_cleanup_watch_evidence(
                    job,
                    old_root,
                    failure["cleanup"],
                    unsafe_id,
                )

        safe_bytes = h.watch_outcome_to_bytes(safe_outcome)
        safe_id = h.watch_outcome_id_for(safe_outcome)
        unsafe_captures = (
            replace(
                captured,
                events=(event,),
                journal=h.windows_watch._JournalEvidence(1, 2, event_bytes, 1, 1),
            ),
            replace(captured, terminal_bytes_sha256="7" * 64),
            replace(
                captured,
                ready=False,
                opened_root_kinds=(),
                completion_reasons=("controller-session-lost", "worker-ready-invalid"),
            ),
            replace(
                captured,
                root_identities_unchanged=False,
                completion_reasons=("controller-session-lost", "root-identity-changed"),
            ),
        )

        def read_safe(path):
            return safe_bytes if Path(path).absolute() == (watch / "outcome.json").absolute() else original_read(path)

        for unsafe_capture in unsafe_captures:
            with self.subTest(raw_reasons=unsafe_capture.completion_reasons), \
                    patch.object(h, "read_exact", side_effect=read_safe), \
                    patch.object(h.windows_watch, "_capture_worker_evidence", return_value=unsafe_capture), \
                    self.assertRaises(h.GateError):
                h._load_cleanup_watch_evidence(
                    job,
                    old_root,
                    failure["cleanup"],
                    safe_id,
                )

        restarted_backend = h.ProductionLiveBackend()
        with patch.object(h.windows_watch, "_exact_process_status", return_value=("live", 888, None)), \
                patch.object(h.windows_watch, "_close_controller_handle", return_value=None), \
                self.assertRaisesRegex(ContainmentOperationError, "close it normally"):
            restarted_backend.cleanup_failed_phase(old_root, "SingleFile", "Control")
        self.assertFalse((job / "cleanup.json").exists())
        with patch.object(h.windows_watch, "_exact_process_status", return_value=("dead", 0, None)) as exact_status, \
                patch.object(h.windows_watch, "_capture_worker_evidence", return_value=captured) as capture, \
                patch.object(h, "_require_process_absence"):
            cleaned = restarted_backend.cleanup_failed_phase(old_root, "SingleFile", "Control")
        self.assertGreaterEqual(exact_status.call_count, 4)
        self.assertEqual(3, capture.call_count)
        capture.assert_has_calls([
            call(request, launch),
            call(request, launch),
            call(request, launch),
        ])
        outcome = h.watch_outcome_from_bytes((watch / "outcome.json").read_bytes())
        self.assertEqual(failure_before, failure_path.read_bytes())
        self.assertFalse(cleaned.value["authority"])
        self.assertFalse(cleaned.value["retryPermitted"])
        self.assertTrue(cleaned.value["freshAttemptRequired"])
        self.assertTrue(cleaned.value["mo2Absent"])
        self.assertTrue(cleaned.value["watcherQuiescent"])
        self.assertEqual("Incomplete", cleaned.value["watchEvidenceCompletion"])
        self.assertEqual(["controller-session-lost"], cleaned.value["watchReasonCodes"])
        self.assertEqual(29, cleaned.value["controllerPid"])
        self.assertEqual(99, cleaned.value["controllerCreationTime"])
        self.assertEqual(100, cleaned.value["watcherCreationTime"])
        forged_no_watch_failure = json.loads(json.dumps(failure))
        forged_no_watch_failure["watchCleanupPending"] = False
        for name in (
            "watcherPid", "requestId", "sessionId", "evidenceRoot", "requestSha256",
            "controllerPid", "controllerCreationTime", "controllerClaimSha256",
            "watcherCreationTime", "workerLaunchSha256",
        ):
            forged_no_watch_failure["cleanup"][name] = None
        forged_no_watch_failure["cleanup"]["watcherCreated"] = False
        forged_no_watch_failure["cleanup"]["watchIdentityAvailable"] = False
        body = {key: value for key, value in forged_no_watch_failure.items() if key != "failureId"}
        forged_no_watch_failure["failureId"] = "phase-failure-sha256:" + h._sha256(h._canonical(body))
        forged_no_watch_cleanup = json.loads(json.dumps(cleaned.value))
        forged_no_watch_cleanup["failureId"] = forged_no_watch_failure["failureId"]
        for name in (
            "requestId", "sessionId", "watcherPid", "watchOutcomeId", "requestSha256",
            "controllerPid", "controllerCreationTime", "controllerClaimSha256",
            "watcherCreationTime", "workerLaunchSha256",
        ):
            forged_no_watch_cleanup[name] = None
        body = {key: value for key, value in forged_no_watch_cleanup.items() if key != "cleanupId"}
        forged_no_watch_cleanup["cleanupId"] = "phase-cleanup-sha256:" + h._sha256(h._canonical(body))
        cleanup_path = job / "cleanup.json"

        def forged_no_watch_load(path):
            absolute = Path(path).absolute()
            if absolute == failure_path.absolute():
                return forged_no_watch_failure
            if absolute == cleanup_path.absolute():
                return forged_no_watch_cleanup
            return original_load(path)

        with patch.object(h, "load_exact_json", side_effect=forged_no_watch_load), \
                self.assertRaises(h.GateError):
            h._load_phase_cleanup(old_root, "SingleFile", "Control")
        (watch / "outcome.json").write_bytes(b"{}\n")
        with patch.object(h, "_require_process_absence"), \
                self.assertRaisesRegex(ContainmentOperationError, "watch outcome"):
            restarted_backend.cleanup_failed_phase(old_root, "SingleFile", "Control")
        (watch / "outcome.json").write_bytes(h.watch_outcome_to_bytes(outcome))
        with self.assertRaises(h.GateError):
            restarted_backend.load_state(old_root, "SingleFile")

        new_jobs = {
            "SingleFile": "bootstrap-job:" + "5" * 32,
            "Package": "bootstrap-job:" + "6" * 32,
        }
        with patch.object(h, "_require_process_absence"), \
                patch.object(h.windows_watch, "_exact_process_status", return_value=("dead", 0, None)), \
                patch.object(h.windows_watch, "_capture_worker_evidence", return_value=captured), \
                patch.object(
                    h,
                    "_load_preparation",
                    side_effect=lambda root: h.load_exact_json(Path(root) / "preparation.json"),
                ):
            restarted = h.restart_failed_attempt(
                old_root,
                h.GateConfig(
                    new_root,
                    self.archive,
                    self.steam,
                    {"commit": "1" * 40, "tree": "2" * 40},
                    new_jobs,
                ),
                live_backend=restarted_backend,
                preparation_backend=_FakeBackend(self.scratch, _listing("ModOrganizer.exe")),
            )
        self.assertEqual(failure_before, failure_path.read_bytes())
        self.assertEqual(new_root.name, restarted.value["newRunId"])
        self.assertEqual(old_root.name, restarted.value["failedRunId"])
        self.assertEqual(str(old_root.absolute()), restarted.value["failedRunRoot"])
        self.assertEqual(set(), set(old_jobs.values()) & set(new_jobs.values()))
        self.assertEqual(
            h.load_exact_json(new_root / "preparation.json")["preparationId"],
            restarted.value["preparationId"],
        )
        self.assertEqual(restarted.value, h.load_exact_json(new_root / "restart.json"))

        restart_path = new_root / "restart.json"
        restart_record = h.load_exact_json(restart_path)
        original_load = h.load_exact_json

        def reidentify(item):
            body = {key: value for key, value in item.items() if key != "restartId"}
            item["restartId"] = "failed-attempt-restart-sha256:" + h._sha256(h._canonical(body))

        attacks = (
            ("failed run root", lambda item: item.__setitem__("failedRunRoot", str(self.scratch / "not-a-run"))),
            ("failed preparation", lambda item: item.__setitem__("failedPreparationId", "preparation-sha256:" + "0" * 64)),
            ("failure IDs", lambda item: item.__setitem__("failureIds", ["phase-failure-sha256:" + "0" * 64])),
            ("cleanup IDs", lambda item: item.__setitem__("cleanupIds", ["phase-cleanup-sha256:" + "0" * 64])),
            ("fresh preparation", lambda item: item.__setitem__("preparationId", "preparation-sha256:" + "0" * 64)),
            (
                "reused old bootstrap job",
                lambda item: item["bootstrapJobIds"].__setitem__("SingleFile", old_jobs["SingleFile"]),
            ),
        )
        for label, mutate in attacks:
            forged = json.loads(json.dumps(restart_record))
            mutate(forged)
            reidentify(forged)

            def forged_restart_load(path, *, value=forged):
                return value if Path(path).absolute() == restart_path.absolute() else original_load(path)

            with self.subTest(label=label), \
                    patch.object(h, "load_exact_json", side_effect=forged_restart_load), \
                    patch.object(
                        h,
                        "_load_preparation",
                        side_effect=lambda root: original_load(Path(root) / "preparation.json"),
                    ), \
                    self.assertRaises(h.GateError):
                h._load_restart_record(new_root)

    def test_partial_begin_failures_retain_cleanup_ownership_and_truthful_evidence(self):
        for case in ("launch-identity", "handle-acquisition", "launch-callback-error", "watch-stop-error"):
            with self.subTest(case=case):
                root = self.scratch / case / ("b" * 32)
                layout_root = root / "SingleFile"
                app = layout_root / "app"
                manager = layout_root / "bootstrap" / "tools" / "mo2" / "skyrim-se-ae"
                (app / "plugins").mkdir(parents=True)
                manager.mkdir(parents=True)
                (layout_root / "environment").mkdir(parents=True)
                (app / "ModOrganizer.exe").write_bytes(b"fixture")
                job = layout_root / "jobs" / "Control"
                request = WatchRequest(
                    "watch-request:" + "2" * 64,
                    "watch-session:" + "3" * 64,
                    "containment-run:" + "b" * 32,
                    ContainmentScenario.NEW_FOLDER,
                    job / "watch",
                    job / "watch" / "stop.token",
                    (),
                )
                preparation = {
                    "preparationId": "preparation-sha256:" + "1" * 64,
                    "runRoot": str(root),
                    "gameRoot": str(self.game),
                    "layouts": [{
                        "layout": "SingleFile",
                        "workspace": str(layout_root / "bootstrap"),
                        "appRoot": str(app),
                        "managerRoot": str(manager),
                    }],
                }
                launch = SimpleNamespace(
                    pid=41,
                    creation_time=101,
                    executable=(str(app / "other.exe") if case == "launch-identity" else str(app / "ModOrganizer.exe")),
                    integrity=IntegrityLevel.LOW,
                    arguments=("--profile", "ModLab - Lab"),
                    working_directory=str(app),
                )
                integrity_receipt = SimpleNamespace(
                    executable=r"C:\Windows\System32\icacls.exe",
                    arguments=("fixture",),
                    exit_code=0,
                    stdout_sha256="4" * 64,
                    stderr_sha256="5" * 64,
                    integrity=IntegrityLevel.LOW,
                )

                def start(_request, *, on_created):
                    on_created(31)

                def launch_process(*_args, on_created, **_kwargs):
                    on_created(41)
                    if case == "launch-callback-error":
                        raise OSError("fixture failure after CreateProcess callback")
                    return launch

                stop_error = h.GateError("fixture watcher stop failure") if case == "watch-stop-error" else None
                backend = h.ProductionLiveBackend()
                with patch.object(h, "_load_preparation", return_value=preparation), \
                        patch.object(backend, "load_state", return_value=h.DurableLayoutState((), False)), \
                        patch.object(h, "snapshot_tree", return_value=[]), \
                        patch.object(h, "_runtime_inventory", return_value=[]), \
                        patch.object(h, "_capture_protected", return_value=_protected()), \
                        patch.object(h, "_derived_watch_roots", return_value={}), \
                        patch.object(h, "build_watch_request", return_value=request), \
                        patch.object(h, "set_low_integrity_tree", return_value=integrity_receipt), \
                        patch.object(h, "inspect_path_integrity", return_value=IntegrityLevel.LOW), \
                        patch.object(h, "start_watch", side_effect=start), \
                        patch.object(h, "launch_low_integrity_process", side_effect=launch_process), \
                        patch.object(h.windows_watch, "_verified_process_handle", side_effect=OSError("fixture handle failure")), \
                        patch.object(h.windows_watch, "_exact_process_status", return_value=("dead", 0, None)), \
                        patch.object(
                            h,
                            "stop_watch",
                            side_effect=stop_error,
                            return_value=_receipt(
                                request=request.request_id,
                                session=request.session_id,
                                worker=31,
                                run=root.name,
                            ),
                        ) as stop:
                    with self.assertRaises(ContainmentOperationError) as raised:
                        backend.begin_phase(root, "SingleFile", "Control")
                    backend.fail_phase(
                        root,
                        "SingleFile",
                        "Control",
                        raised.exception,
                        raised.exception.effects,
                    )
                failure = h.load_exact_json(job / "failure.json")
                cleanup = failure["cleanup"]
                self.assertEqual(31, cleanup["watcherPid"])
                self.assertEqual(41, cleanup["mo2Pid"])
                self.assertEqual(request.request_id, cleanup["requestId"])
                self.assertEqual(request.session_id, cleanup["sessionId"])
                self.assertEqual(str(request.evidence_root), cleanup["evidenceRoot"])
                self.assertEqual(case != "launch-callback-error", cleanup["mo2CreationTimeAvailable"])
                self.assertEqual(case == "launch-callback-error", failure["processMayStillBeLive"])
                self.assertTrue(failure["watchCleanupPending"])
                self.assertEqual(0 if case == "launch-callback-error" else 1, stop.call_count)
                if case == "watch-stop-error":
                    self.assertTrue(any("watcher stop failure" in item for item in failure["cleanupErrors"]))
                self.assertIsNotNone(backend._pending_session)

    def test_production_controller_keeps_one_watch_through_strict_control_finalization(self):
        layout_root = self.run_root / "SingleFile"
        app = layout_root / "app"
        plugins = app / "plugins"
        manager = layout_root / "bootstrap" / "tools" / "mo2" / "skyrim-se-ae"
        manager_logs = manager / "logs"
        plugins.mkdir(parents=True)
        manager_logs.mkdir(parents=True)
        (app / "ModOrganizer.exe").write_bytes(b"fixture executable")
        source_log = manager_logs / "mo_interface.log"
        source_log.write_bytes(b"ordinary MO2 control log\n")
        job = layout_root / "jobs" / "Control"
        request = WatchRequest(
            "watch-request:" + "2" * 64,
            "watch-session:" + "3" * 64,
            "containment-run:" + "a" * 32,
            ContainmentScenario.NEW_FOLDER,
            job / "watch",
            job / "watch" / "stop.token",
            (),
        )
        preparation = {
            "preparationId": "preparation-sha256:" + "1" * 64,
            "runRoot": str(self.run_root),
            "gameRoot": str(self.steam / "steamapps/common/Skyrim Special Edition"),
            "layouts": [{
                "layout": "SingleFile",
                "workspace": str(layout_root / "bootstrap"),
                "appRoot": str(app),
                "managerRoot": str(manager),
            }],
        }
        runtime = []
        for index, (relative, content) in enumerate(
            zip(h.RUNTIME_PATHS, h.runtime_capability.RUNTIME_CONTENT, strict=True), 1
        ):
            runtime.append({
                "path": relative,
                "kind": "file",
                "sha256": content[0],
                "size": content[1],
                "volume": 7,
                "fileId": index,
            })
        baseline = [{**item, "path": item["path"][len("plugins/"):]} for item in runtime[1:]]
        app_tree = [dict(item) for item in runtime]
        launch = SimpleNamespace(
            pid=41,
            creation_time=101,
            executable=str(app / "ModOrganizer.exe"),
            integrity=IntegrityLevel.LOW,
            arguments=("--profile", "ModLab - Lab"),
            working_directory=str(app),
        )
        integrity_receipt = SimpleNamespace(
            executable=r"C:\Windows\System32\icacls.exe",
            arguments=("fixture",),
            exit_code=0,
            stdout_sha256="4" * 64,
            stderr_sha256="5" * 64,
            integrity=IntegrityLevel.LOW,
        )
        process = SimpleNamespace(pid=41, executable_path=str(app / "ModOrganizer.exe"))
        receipt = _receipt(request=request.request_id, session=request.session_id, worker=31)
        events = []

        def snapshot(path):
            absolute = Path(path).absolute()
            if absolute == plugins.absolute():
                return [dict(item) for item in baseline]
            if absolute == app.absolute():
                return [dict(item) for item in app_tree]
            if absolute == manager_logs.absolute():
                return [{"path": source_log.name, "kind": "file"}]
            raise AssertionError(f"unexpected snapshot root: {absolute}")

        def start(_request, *, on_created):
            events.append(("start", _request))
            for name in ("request.json", "controller-claim.json", "ready.json", "events.ndjson", "terminal.json"):
                (_request.evidence_root / name).write_bytes(b"fixture\n")
            h.publish_exact_json(_request.evidence_root / "worker-launch.json", {"workerPid": 31})
            on_created(31)

        def launch_process(*_args, on_created, **_kwargs):
            events.append(("launch", launch))
            on_created(41)
            return launch

        def stop(request_path):
            events.append(("stop", request_path))
            (request_path.parent / "outcome.json").write_bytes(b"fixture outcome\n")
            (request_path.parent / "stop.token").write_bytes(b"stop\n")
            return receipt

        class Exchange:
            def window(self, session):
                return {
                    "windowId": 51,
                    "app": "process:" + session.executable,
                    "title": "Mod Organizer v2.5.2",
                    "screenshotId": "fixture-shot",
                    "screenshotDataUrl": "data:image/png;base64," + base64.b64encode(_png_bytes()).decode("ascii"),
                    "loadedTool": None,
                    "observedAt": "2026-09-04T00:00:00+00:00",
                }

            def close(self, session, window):
                return {
                    "action": "Alt+F4",
                    "windowId": window["windowId"],
                    "returned": True,
                    "observedAt": "2026-09-04T00:00:01+00:00",
                }

        backend = h.ProductionLiveBackend()
        with ExitStack() as stack:
            stack.enter_context(patch.object(backend, "source_check"))
            durable = stack.enter_context(patch.object(
                backend,
                "load_state",
                side_effect=(
                    h.DurableLayoutState((), False),
                    h.DurableLayoutState((), False),
                    h.DurableLayoutState(("Control",), False),
                ),
            ))
            stack.enter_context(patch.object(h, "_load_preparation", return_value=preparation))
            stack.enter_context(patch.object(h, "snapshot_tree", side_effect=snapshot))
            stack.enter_context(patch.object(h, "_runtime_inventory", return_value=runtime))
            stack.enter_context(patch.object(h, "_snapshot_runtime_writable", return_value={
                "App": {"rootIdentity": {"volumeSerial": 1, "fileId": 1, "attributes": 16}, "inventory": app_tree},
                "Manager": {"rootIdentity": {"volumeSerial": 1, "fileId": 2, "attributes": 16}, "inventory": []},
                "Environment": {"rootIdentity": {"volumeSerial": 1, "fileId": 3, "attributes": 16}, "inventory": []},
            }))
            outputs = stack.enter_context(patch.object(h, "validate_runtime_outputs", wraps=h.validate_runtime_outputs))
            stack.enter_context(patch.object(h, "_capture_protected", return_value=_protected()))
            stack.enter_context(patch.object(h, "_derived_watch_roots", return_value={}))
            stack.enter_context(patch.object(h, "build_watch_request", return_value=request))
            stack.enter_context(patch.object(h, "set_low_integrity_tree", return_value=integrity_receipt))
            stack.enter_context(patch.object(h, "inspect_path_integrity", return_value=IntegrityLevel.LOW))
            stack.enter_context(patch.object(h, "start_watch", side_effect=start))
            stack.enter_context(patch.object(h, "launch_low_integrity_process", side_effect=launch_process))
            stack.enter_context(patch.object(h, "stop_watch", side_effect=stop))
            stack.enter_context(patch.object(h, "watch_proves_unchanged", return_value=True))
            stack.enter_context(patch.object(h, "_candidate_processes", side_effect=((process,), (process,), ())))
            stack.enter_context(patch.object(h, "_native_windows_for_pid", return_value=[{
                "hwnd": 9001,
                "pid": 41,
                "title": "Mod Organizer v2.5.2",
                "className": "Qt5152QWindowIcon",
                "visible": True,
            }]))
            stack.enter_context(patch.object(h.windows_watch, "_verified_process_handle", return_value=777))
            stack.enter_context(patch.object(h.windows_watch, "_verify_retained_process_handle"))
            stack.enter_context(patch.object(h.windows_watch._kernel32, "WaitForSingleObject", side_effect=(h.windows_watch._WAIT_TIMEOUT, h.windows_watch._WAIT_TIMEOUT, h.windows_watch._WAIT_OBJECT_0)))
            stack.enter_context(patch.object(h.windows_watch, "_get_process_exit_code", return_value=0))
            stack.enter_context(patch.object(h.windows_watch, "_close_controller_handle", return_value=None))
            stack.enter_context(patch.object(h.windows_watch, "_exact_process_status", return_value=("dead", 0, None)))
            strict_bundle = stack.enter_context(patch.object(h, "_load_validated_phase_bundle"))
            received = h.run_operator_phase(
                self.run_root,
                "SingleFile",
                "Control",
                backend=backend,
                exchange=Exchange(),
            )
        strict_bundle.assert_called_once_with(
            self.run_root.absolute(),
            "SingleFile",
            "Control",
            preparation["preparationId"],
        )
        self.assertEqual(3, durable.call_count)
        self.assertEqual(["start", "launch", "stop"], [item[0] for item in events])
        outputs.assert_called_once_with(
            self.run_root,
            "SingleFile",
            "Control",
            app_tree,
            app_tree,
            runtime,
            runtime,
        )
        self.assertIs(request, events[0][1])
        self.assertEqual(request.evidence_root / "request.json", events[2][1])
        self.assertEqual("Control", h.load_exact_json(job / "observation.json")["phase"])
        self.assertEqual(received.value, h.load_exact_json(job / "phase-final.json"))
        operator = h.load_exact_json(job / "operator-evidence.json")
        self.assertEqual(operator["operatorEvidenceId"], received.value["operatorEvidenceId"])
        self.assertTrue((job / "window.png").is_file())
        self.assertEqual(hashlib.sha256(_png_bytes()).hexdigest(), operator["window"]["image"]["sha256"])
        self.assertNotEqual(operator["window"]["windowId"], operator["window"]["nativeBefore"]["hwnd"])
        written = set(received.effects.written_paths)
        for path in (
            job / "begin.json",
            job / "process.json",
            job / "window.png",
            job / "operator-evidence.json",
            job / "observed-000.log",
            job / "runtime-delta.json",
            job / "observation.json",
            job / "effect.json",
            job / "phase-final.json",
            *(request.evidence_root / name for name in (
                "request.json", "controller-claim.json", "worker-launch.json",
                "ready.json", "events.ndjson", "terminal.json", "outcome.json", "stop.token",
            )),
        ):
            self.assertIn(path, written)
        self.assertEqual(
            h.build_effect_record("SingleFile:Control", received.effects),
            h.load_exact_json(job / "effect.json"),
        )
        self.assertIsNone(backend._pending_session)

    def test_real_product_selector_accepts_complete_bytecode_safe_fixture(self):
        product_test = REPO / "tests" / "test_mo2_bridge_runtime_capability.py"
        spec = importlib.util.spec_from_file_location("task_7b_product_capability_fixture", product_test)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(module)
        selected = h.derive_selection(module.fixture(self.scratch))
        self.assertEqual("SingleFile", selected["selection"])
        self.assertRegex(selected["capabilityId"], r"^mo2-runtime-capability-sha256:[0-9a-f]{64}$")

    def test_guarded_marker_is_controller_published_through_shared_no_replace_boundary(self):
        job = self.run_root / "SingleFile" / "jobs" / "Guarded"
        job.mkdir(parents=True)
        session = SimpleNamespace(
            run_root=self.run_root,
            layout="SingleFile",
            phase="Guarded",
            nonce="c" * 32,
            job_root=job,
            launch=SimpleNamespace(pid=41),
        )
        loaded = {
            "schemaVersion": 2,
            "runId": self.run_root.name,
            "layout": "SingleFile",
            "phase": "Guarded",
            "nonce": "c" * 32,
            "pid": 41,
            "pythonVersion": "3.12.8",
            "implementation": "cpython",
            "cacheTag": "cpython-312",
            "bytecodeDisabled": True,
            "executableRelative": h.RUNTIME_PATHS[0],
            "mobaseRelative": h.RUNTIME_PATHS[2],
        }
        with self.assertRaises(h.GateError):
            h._publish_guarded_marker(
                session,
                {**loaded, "pid": 42},
            )
        self.assertFalse((job / "guarded.json").exists())
        with patch.object(h, "publish_exact_json", wraps=h.publish_exact_json) as publish:
            received = h.containment_service._receipted(h._publish_guarded_marker)(session, loaded)
            self.assertEqual(loaded, received.value)
        publish.assert_called_once_with(job / "guarded.json", loaded)
        self.assertEqual(loaded, h.load_exact_json(job / "guarded.json"))
        with self.assertRaises(Exception):
            h.containment_service._receipted(h._publish_guarded_marker)(session, loaded)

    def test_guarded_marker_is_not_published_before_full_observation_validation(self):
        job = self.run_root / "SingleFile" / "jobs" / "Guarded"
        job.mkdir(parents=True)
        session = SimpleNamespace(
            run_root=self.run_root,
            layout="SingleFile",
            phase="Guarded",
            nonce="c" * 32,
            job_root=job,
            app_root=self.run_root / "SingleFile" / "app",
            launch=SimpleNamespace(pid=41),
        )
        loaded = {
            "schemaVersion": 2,
            "runId": self.run_root.name,
            "layout": "SingleFile",
            "phase": "Guarded",
            "nonce": "c" * 32,
            "pid": 41,
            "pythonVersion": "3.12.8",
            "implementation": "cpython",
            "cacheTag": "cpython-312",
            "bytecodeDisabled": True,
            "executableRelative": h.RUNTIME_PATHS[0],
            "mobaseRelative": h.RUNTIME_PATHS[2],
        }
        observation = {"loaded": loaded, "guarded": None}
        with patch.object(h.runtime_capability, "_observation", side_effect=h.GateError("invalid observation")), \
                patch.object(h, "_publish_guarded_marker") as publish, \
                self.assertRaises(h.GateError):
            h._validate_observation_and_publish_guarded(session, observation)
        publish.assert_not_called()
        self.assertIsNone(observation["guarded"])
        self.assertFalse((job / "guarded.json").exists())

        observation = {"loaded": loaded, "guarded": None}
        with patch.object(h.runtime_capability, "_observation"), \
                patch.object(h.runtime_capability, "_verify_raw_evidence", side_effect=h.GateError("invalid raw evidence")), \
                patch.object(h, "_publish_guarded_marker") as publish, \
                self.assertRaises(h.GateError):
            h._validate_observation_and_publish_guarded(session, observation)
        publish.assert_not_called()
        self.assertIsNone(observation["guarded"])
        self.assertFalse((job / "guarded.json").exists())

        observation = {"loaded": loaded, "guarded": None}
        with patch.object(h.runtime_capability, "_observation") as validate, \
                patch.object(h.runtime_capability, "_verify_raw_evidence") as raw, \
                patch.object(h, "_publish_guarded_marker", return_value=loaded) as publish:
            self.assertEqual(loaded, h._validate_observation_and_publish_guarded(session, observation))
        self.assertEqual(loaded, observation["guarded"])
        self.assertEqual(2, validate.call_count)
        self.assertEqual(2, raw.call_count)
        publish.assert_called_once_with(session, loaded)

    def test_final_phase_bundle_binds_effect_and_watcher_pids_to_observation(self):
        observation = {"pid": 41}
        observation_id = "observation-sha256:" + h._sha256(h._canonical(observation))
        effect = h.build_effect_record(
            "SingleFile:Control",
            ContainmentEffects(
                written_paths=(self.run_root,),
                child_mutation_roots=(self.run_root,),
                watcher_pid=31,
                mo2_pid=41,
            ),
        )
        outcome = SimpleNamespace(
            request_id="watch-request:" + "2" * 64,
            session_id="watch-session:" + "3" * 64,
            run_id="containment-run:" + "a" * 32,
            scenario=ContainmentScenario.NEW_FOLDER,
            opened_root_kinds=ROOT_KINDS,
            worker_pid=31,
        )
        watch_id = "watch-outcome-sha256:" + "4" * 64
        final = {
            "schemaVersion": 1,
            "kind": "task-7B-phase-final",
            "runId": "a" * 32,
            "layout": "SingleFile",
            "phase": "Control",
            "preparationId": "preparation-sha256:" + "1" * 64,
            "observationId": observation_id,
            "watchRequestId": outcome.request_id,
            "watchOutcomeId": watch_id,
            "effectId": effect["effectId"],
            "operatorEvidenceId": "operator-evidence-sha256:" + "5" * 64,
            "beginId": "phase-begin-sha256:" + "6" * 64,
            "processId": "phase-process-sha256:" + "7" * 64,
            "runtimeDeltaId": "runtime-delta-sha256:" + "9" * 64,
            "watchEvidence": {name: "8" * 64 for name in (
                "requestSha256", "claimSha256", "launchSha256", "readySha256",
                "eventsSha256", "terminalSha256", "outcomeSha256", "stopSha256",
            )},
            "protectedBefore": {},
            "protectedAfter": {},
            "windowObservedAt": "before",
            "closeObservedAt": "after",
            "completedAt": "complete",
            "authority": False,
        }
        final["phaseFinalId"] = "phase-final-sha256:" + h._sha256(h._canonical(final))
        with patch.object(h, "watch_outcome_from_bytes", return_value=outcome), \
                patch.object(h, "watch_outcome_to_bytes", return_value=b"outcome"), \
                patch.object(h, "watch_outcome_id_for", return_value=watch_id):
            bundle = h._validate_phase_bundle(
                self.run_root,
                "SingleFile",
                "Control",
                "preparation-sha256:" + "1" * 64,
                observation,
                final,
                effect,
                b"outcome",
            )
            self.assertEqual((31, 41), (bundle["watcherPid"], bundle["mo2Pid"]))
            wrong = h.build_effect_record(
                "SingleFile:Control",
                ContainmentEffects(
                    written_paths=(self.run_root,),
                    child_mutation_roots=(self.run_root,),
                    watcher_pid=31,
                    mo2_pid=42,
                ),
            )
            wrong_final = {**final, "effectId": wrong["effectId"]}
            body = {key: item for key, item in wrong_final.items() if key != "phaseFinalId"}
            wrong_final["phaseFinalId"] = "phase-final-sha256:" + h._sha256(h._canonical(body))
            with self.assertRaises(h.GateError):
                h._validate_phase_bundle(
                    self.run_root,
                    "SingleFile",
                    "Control",
                    "preparation-sha256:" + "1" * 64,
                    observation,
                    wrong_final,
                    wrong,
                    b"outcome",
                )

    def test_phase_reload_rejects_missing_complete_raw_evidence_chain(self):
        """A final/outcome summary alone is not a restart-safe phase proof."""
        job = self.run_root / "SingleFile" / "jobs" / "Control"
        watch = job / "watch"
        watch.mkdir(parents=True)
        observation = {"pid": 41}
        observation_id = "observation-sha256:" + h._sha256(h._canonical(observation))
        effect = h.build_effect_record(
            "SingleFile:Control",
            ContainmentEffects(
                written_paths=(self.run_root,),
                child_mutation_roots=(self.run_root,),
                watcher_pid=31,
                mo2_pid=41,
            ),
        )
        outcome = SimpleNamespace(
            request_id="watch-request:" + "2" * 64,
            session_id="watch-session:" + "3" * 64,
            run_id="containment-run:" + self.run_root.name,
            scenario=ContainmentScenario.NEW_FOLDER,
            opened_root_kinds=ROOT_KINDS,
            worker_pid=31,
        )
        watch_id = "watch-outcome-sha256:" + "4" * 64
        final = {
            "schemaVersion": 1,
            "kind": "task-7B-phase-final",
            "runId": self.run_root.name,
            "layout": "SingleFile",
            "phase": "Control",
            "preparationId": "preparation-sha256:" + "1" * 64,
            "observationId": observation_id,
            "watchRequestId": outcome.request_id,
            "watchOutcomeId": watch_id,
            "effectId": effect["effectId"],
            "operatorEvidenceId": "operator-evidence-sha256:" + "5" * 64,
            "beginId": "phase-begin-sha256:" + "6" * 64,
            "processId": "phase-process-sha256:" + "7" * 64,
            "runtimeDeltaId": "runtime-delta-sha256:" + "9" * 64,
            "watchEvidence": {name: "8" * 64 for name in (
                "requestSha256", "claimSha256", "launchSha256", "readySha256",
                "eventsSha256", "terminalSha256", "outcomeSha256", "stopSha256",
            )},
            "protectedBefore": {},
            "protectedAfter": {},
            "windowObservedAt": "before",
            "closeObservedAt": "after",
            "completedAt": "complete",
            "authority": False,
        }
        final["phaseFinalId"] = "phase-final-sha256:" + h._sha256(h._canonical(final))
        h.publish_exact_json(job / "observation.json", observation)
        h.publish_exact_json(job / "effect.json", effect)
        h.publish_exact_json(job / "phase-final.json", final)
        (watch / "outcome.json").write_bytes(b"outcome")
        with patch.object(h, "watch_outcome_from_bytes", return_value=outcome), \
                patch.object(h, "watch_outcome_to_bytes", return_value=b"outcome"), \
                patch.object(h, "watch_outcome_id_for", return_value=watch_id), \
                patch.object(h.runtime_capability, "_observation"), \
                patch.object(h.runtime_capability, "_verify_raw_evidence"), \
                self.assertRaises(h.GateError):
            h._load_validated_phase_bundle(
                self.run_root,
                "SingleFile",
                "Control",
                "preparation-sha256:" + "1" * 64,
            )

    def test_complete_phase_reload_reconstructs_every_raw_record(self):
        fixture = self._complete_phase_evidence("strict-valid")
        observation, bundle = self._reload_complete_phase(fixture)
        self.assertEqual(7003, observation["pid"])
        self.assertEqual(7002, bundle["watcherPid"])
        self.assertEqual(7003, bundle["mo2Pid"])

    def test_complete_phase_reload_rejects_record_substitution_and_controller_loss(self):
        raw_names = (
            "request.json",
            "controller-claim.json",
            "worker-launch.json",
            "ready.json",
            "events.ndjson",
            "terminal.json",
            "outcome.json",
            "stop.token",
        )
        for name in raw_names:
            with self.subTest(record=name):
                fixture = self._complete_phase_evidence("raw-" + name.replace(".", "-"))
                (fixture.watch / name).write_bytes(b"{}\n")
                with self.assertRaises(h.GateError):
                    self._reload_complete_phase(fixture)
        for name, mutate in (
            ("begin.json", lambda value: value.update(nonce="f" * 32)),
            ("process.json", lambda value: value.update(pid=9991)),
            ("operator-evidence.json", lambda value: value.update(computerControlUsed=False)),
            ("runtime-delta.json", lambda value: value.update(authority=True)),
        ):
            with self.subTest(record=name):
                fixture = self._complete_phase_evidence("phase-" + name.replace(".", "-"))
                path = fixture.job / name
                value = json.loads(path.read_bytes())
                mutate(value)
                path.write_bytes(h._canonical(value))
                with self.assertRaises(h.GateError):
                    self._reload_complete_phase(fixture)
        fixture = self._complete_phase_evidence("controller-loss")
        (fixture.watch / "controller-loss.json").write_bytes(b"{}\n")
        with self.assertRaisesRegex(h.GateError, "controller-loss"):
            self._reload_complete_phase(fixture)

        fixture = self._complete_phase_evidence("missing-stop-token")
        (fixture.watch / "stop.token").unlink()
        with self.assertRaises(h.GateError):
            self._reload_complete_phase(fixture)

    def test_complete_phase_reload_rejects_recomputed_root_and_protected_state_substitution(self):
        fixture = self._complete_phase_evidence("root-identity")
        request_path = fixture.watch / "request.json"
        request = h.watch_request_from_bytes(request_path.read_bytes())
        changed_request = replace(
            request,
            roots=(replace(request.roots[0], file_id=request.roots[0].file_id + 1), *request.roots[1:]),
        )
        request_path.write_bytes(h.watch_request_to_bytes(changed_request))
        request_sha = h.watch_request_sha256(changed_request)
        old_request = request
        claim_path = fixture.watch / "controller-claim.json"
        claim = h.controller_claim_from_bytes(claim_path.read_bytes(), old_request)
        changed_claim = replace(claim, request_sha256=request_sha)
        claim_path.write_bytes(h.controller_claim_to_bytes(changed_claim, changed_request))
        launch_path = fixture.watch / "worker-launch.json"
        launch = h.worker_launch_from_bytes(launch_path.read_bytes(), old_request)
        changed_launch = replace(launch, request_sha256=request_sha)
        launch_path.write_bytes(h.worker_launch_to_bytes(changed_launch, changed_request))
        ready = json.loads((fixture.watch / "ready.json").read_bytes())
        ready["requestBytesSha256"] = request_sha
        (fixture.watch / "ready.json").write_bytes(h._canonical(ready))
        terminal = json.loads((fixture.watch / "terminal.json").read_bytes())
        terminal["requestBytesSha256"] = request_sha
        (fixture.watch / "terminal.json").write_bytes(h._canonical(terminal))
        captured = h.windows_watch._capture_worker_evidence(changed_request, changed_launch)
        changed_outcome = h.windows_watch._watch_outcome(
            changed_request,
            changed_claim,
            changed_launch,
            completion=WatchEvidenceCompletion.COMPLETED,
            worker_exit_code=0,
            reasons=(),
            captured=captured,
        )
        (fixture.watch / "outcome.json").write_bytes(h.watch_outcome_to_bytes(changed_outcome))
        final_path = fixture.job / "phase-final.json"
        final = json.loads(final_path.read_bytes())
        del final["phaseFinalId"]
        final["watchOutcomeId"] = h.watch_outcome_id_for(changed_outcome)
        for field, name in (
            ("requestSha256", "request.json"),
            ("claimSha256", "controller-claim.json"),
            ("launchSha256", "worker-launch.json"),
            ("readySha256", "ready.json"),
            ("eventsSha256", "events.ndjson"),
            ("terminalSha256", "terminal.json"),
            ("outcomeSha256", "outcome.json"),
        ):
            final["watchEvidence"][field] = hashlib.sha256((fixture.watch / name).read_bytes()).hexdigest()
        final["phaseFinalId"] = "phase-final-sha256:" + h._sha256(h._canonical(final))
        final_path.write_bytes(h._canonical(final))
        with self.assertRaisesRegex(h.GateError, "root path or identity"):
            self._reload_complete_phase(fixture)

        fixture = self._complete_phase_evidence("protected-after")
        final_path = fixture.job / "phase-final.json"
        final = json.loads(final_path.read_bytes())
        del final["phaseFinalId"]
        final["protectedAfter"]["source_mods"]["sha256"] = "9" * 64
        final["phaseFinalId"] = "phase-final-sha256:" + h._sha256(h._canonical(final))
        final_path.write_bytes(h._canonical(final))
        with self.assertRaisesRegex(h.GateError, "current protected state"):
            self._reload_complete_phase(fixture)

        fixture = self._complete_phase_evidence("protected-before")
        begin_path = fixture.job / "begin.json"
        begin = json.loads(begin_path.read_bytes())
        del begin["beginId"]
        begin["protectedBefore"]["source_mods"]["sha256"] = "8" * 64
        begin["beginId"] = "phase-begin-sha256:" + h._sha256(h._canonical(begin))
        begin_path.write_bytes(h._canonical(begin))
        final_path = fixture.job / "phase-final.json"
        final = json.loads(final_path.read_bytes())
        del final["phaseFinalId"]
        final["beginId"] = begin["beginId"]
        final["protectedBefore"] = begin["protectedBefore"]
        final["phaseFinalId"] = "phase-final-sha256:" + h._sha256(h._canonical(final))
        final_path.write_bytes(h._canonical(final))
        with self.assertRaisesRegex(h.GateError, "does not prove unchanged"):
            self._reload_complete_phase(fixture)

    def test_complete_phase_reload_rejects_worker_and_mo2_pid_cross_binding(self):
        for field, watcher_pid, mo2_pid in (
            ("watcher", 9991, 7003),
            ("MO2", 7002, 9992),
        ):
            with self.subTest(field=field):
                fixture = self._complete_phase_evidence("pid-" + field.lower())
                effect = h.build_effect_record(
                    "SingleFile:Control",
                    ContainmentEffects(
                        written_paths=(fixture.root,),
                        child_mutation_roots=(fixture.root,),
                        watcher_pid=watcher_pid,
                        mo2_pid=mo2_pid,
                    ),
                )
                effect_path = fixture.job / "effect.json"
                effect_path.write_bytes(h._canonical(effect))
                final_path = fixture.job / "phase-final.json"
                final = json.loads(final_path.read_bytes())
                del final["phaseFinalId"]
                final["effectId"] = effect["effectId"]
                final["phaseFinalId"] = "phase-final-sha256:" + h._sha256(h._canonical(final))
                final_path.write_bytes(h._canonical(final))
                with self.assertRaises(h.GateError):
                    self._reload_complete_phase(fixture)

    def test_complete_phase_reload_rejects_recomputed_delta_and_missing_publication_receipt(self):
        fixture = self._complete_phase_evidence("delta-forgery")
        delta_path = fixture.job / "runtime-delta.json"
        delta = json.loads(delta_path.read_bytes())
        del delta["runtimeDeltaId"]
        delta["roots"][0]["after"]["inventory"].append({
            "path": "plugins/foreign.py",
            "kind": "file",
            "sha256": "7" * 64,
            "size": 1,
            "volume": 1,
            "fileId": 77,
        })
        delta["roots"][0]["changes"].append({
            "path": "plugins/foreign.py",
            "change": "Added",
            "before": None,
            "after": delta["roots"][0]["after"]["inventory"][-1],
        })
        delta["runtimeDeltaId"] = "runtime-delta-sha256:" + h._sha256(h._canonical(delta))
        delta_path.write_bytes(h._canonical(delta))
        final_path = fixture.job / "phase-final.json"
        final = json.loads(final_path.read_bytes())
        del final["phaseFinalId"]
        final["runtimeDeltaId"] = delta["runtimeDeltaId"]
        final["phaseFinalId"] = "phase-final-sha256:" + h._sha256(h._canonical(final))
        final_path.write_bytes(h._canonical(final))
        with self.assertRaisesRegex(h.GateError, "runtime write escaped"):
            self._reload_complete_phase(fixture)

        fixture = self._complete_phase_evidence("missing-publication-receipt")
        effect_path = fixture.job / "effect.json"
        effect = json.loads(effect_path.read_bytes())
        receipt_path = str(fixture.job / "operator-evidence.json")
        effect["writtenPaths"].remove(receipt_path)
        del effect["effectId"]
        effect["effectId"] = "effect-sha256:" + h._sha256(h._canonical(effect))
        effect_path.write_bytes(h._canonical(effect))
        final_path = fixture.job / "phase-final.json"
        final = json.loads(final_path.read_bytes())
        del final["phaseFinalId"]
        final["effectId"] = effect["effectId"]
        final["phaseFinalId"] = "phase-final-sha256:" + h._sha256(h._canonical(final))
        final_path.write_bytes(h._canonical(final))
        with self.assertRaisesRegex(h.GateError, "every confined publication"):
            self._reload_complete_phase(fixture)

    def test_production_finalizer_consumes_all_ten_validated_bundles_and_publishes_no_authority(self):
        product_test = REPO / "tests" / "test_mo2_bridge_runtime_capability.py"
        spec = importlib.util.spec_from_file_location("task_7b_finalize_capability_fixture", product_test)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(module)
        matrix = module.fixture(self.scratch)
        preparation_id = "preparation-sha256:" + "1" * 64
        preparation = {
            "preparationId": preparation_id,
            "source": matrix["source"],
            "archive": matrix["archive"],
            "sourceArtifactId": "containment-source-artifact-sha256:" + "2" * 64,
        }
        outcomes = {}
        counter = 0
        for candidate in matrix["candidates"]:
            layout = candidate["layout"]
            layout_root = self.run_root / layout
            observations = [candidate["control"], *candidate["observations"]]
            for phase, observation in zip(h.PHASES, observations, strict=True):
                counter += 1
                pid = 1000 + counter
                creation = 2000 + counter
                observation["pid"] = pid
                observation["launchProcess"].update(pid=pid, creationTime=creation)
                for event in (observation["ui"]["processCorrelation"]["events"][0], observation["ui"]["processCorrelation"]["events"][2]):
                    event["processes"][0].update(pid=pid, creationTime=creation)
                if observation["loaded"] is not None:
                    observation["loaded"]["pid"] = pid
                if phase == "Guarded":
                    observation["guarded"] = copy.deepcopy(observation["loaded"])
                job = layout_root / "jobs" / phase
                watch = job / "watch"
                watch.mkdir(parents=True)
                if observation["loaded"] is None:
                    log_data = b"ordinary MO2 control log\n"
                else:
                    payload = json.dumps(observation["loaded"], sort_keys=True, separators=(",", ":")).encode("utf-8")
                    log_data = b"INFO MODLAB_CAPABILITY_V2 " + payload + b"\n"
                log_path = job / "observed-000.log"
                log_path.write_bytes(log_data)
                observation["logs"] = [{
                    "path": str(log_path),
                    "sha256": hashlib.sha256(log_data).hexdigest(),
                    "size": len(log_data),
                }]
                if phase == "Guarded":
                    h.publish_exact_json(job / "guarded.json", observation["guarded"])
                observation_id = "observation-sha256:" + h._sha256(h._canonical(observation))
                effect = h.build_effect_record(
                    f"{layout}:{phase}",
                    ContainmentEffects(
                        written_paths=(layout_root,),
                        child_mutation_roots=(layout_root,),
                        watcher_pid=3000 + counter,
                        mo2_pid=pid,
                    ),
                )
                request_id = "watch-request:" + f"{counter:064x}"
                session_id = "watch-session:" + f"{counter:064x}"
                watch_id = "watch-outcome-sha256:" + f"{counter:064x}"
                raw_outcome = f"fixture-outcome-{counter}".encode("ascii")
                outcome = SimpleNamespace(
                    raw=raw_outcome,
                    watch_id=watch_id,
                    request_id=request_id,
                    session_id=session_id,
                    run_id="containment-run:" + self.run_root.name,
                    scenario=ContainmentScenario.NEW_FOLDER,
                    opened_root_kinds=ROOT_KINDS,
                    worker_pid=3000 + counter,
                )
                outcomes[raw_outcome] = outcome
                (watch / "outcome.json").write_bytes(raw_outcome)
                final = {
                    "schemaVersion": 1,
                    "kind": "task-7B-phase-final",
                    "runId": self.run_root.name,
                    "layout": layout,
                    "phase": phase,
                    "preparationId": preparation_id,
                    "observationId": observation_id,
                    "watchRequestId": request_id,
                    "watchOutcomeId": watch_id,
                    "effectId": effect["effectId"],
                    "operatorEvidenceId": "operator-evidence-sha256:" + f"{counter:064x}",
                    "beginId": "phase-begin-sha256:" + f"{counter:064x}",
                    "processId": "phase-process-sha256:" + f"{counter:064x}",
                    "runtimeDeltaId": "runtime-delta-sha256:" + f"{counter:064x}",
                    "watchEvidence": {name: f"{counter:064x}" for name in (
                        "requestSha256", "claimSha256", "launchSha256", "readySha256",
                        "eventsSha256", "terminalSha256", "outcomeSha256",
                    )},
                    "protectedBefore": {},
                    "protectedAfter": {},
                    "windowObservedAt": "before",
                    "closeObservedAt": "after",
                    "completedAt": "complete",
                    "authority": False,
                }
                final["phaseFinalId"] = "phase-final-sha256:" + h._sha256(h._canonical(final))
                h.publish_exact_json(job / "observation.json", observation)
                h.publish_exact_json(job / "effect.json", effect)
                h.publish_exact_json(job / "phase-final.json", final)
            control = candidate["control"]
            control_id = "observation-sha256:" + h._sha256(h._canonical(control))
            install_effect = h.build_effect_record(
                f"{layout}:Install",
                ContainmentEffects(
                    written_paths=(layout_root, layout_root / "installation.json"),
                    child_mutation_roots=(layout_root,),
                ),
            )
            writable_before = {
                name: {
                    "rootIdentity": {
                        "volumeSerial": 1,
                        "fileId": 100 + index,
                        "attributes": 16,
                    },
                    "inventory": [],
                }
                for index, name in enumerate(("App", "Manager", "Environment"), 1)
            }
            writable_after = copy.deepcopy(writable_before)
            writable_after["App"]["inventory"] = [
                {**item, "path": "plugins/" + item["path"]}
                for item in candidate["declared"]
            ]
            installation = {
                "schemaVersion": 1,
                "kind": "task-7B-candidate-installation",
                "runId": self.run_root.name,
                "layout": layout,
                "controlId": control_id,
                "declared": candidate["declared"],
                "before": control["after"],
                "after": [*control["after"], *candidate["declared"]],
                "writableBefore": writable_before,
                "writableAfter": writable_after,
                "observedAt": "fixture",
                "effect": install_effect,
                "authority": False,
            }
            installation["installationId"] = "installation-sha256:" + h._sha256(h._canonical(installation))
            h.publish_exact_json(layout_root / "installation.json", installation)

        def load_validated(root, layout, phase, expected_preparation_id):
            job = Path(root) / layout / "jobs" / phase
            observation = h.load_exact_json(job / "observation.json")
            return observation, h._validate_phase_bundle(
                Path(root),
                layout,
                phase,
                expected_preparation_id,
                observation,
                h.load_exact_json(job / "phase-final.json"),
                h.load_exact_json(job / "effect.json"),
                h.read_exact(job / "watch" / "outcome.json"),
            )

        backend = h.ProductionLiveBackend()
        with patch.object(h, "_load_preparation", return_value=preparation), \
                patch.object(backend, "load_state", return_value=h.DurableLayoutState(h.PHASES, True)), \
                patch.object(h, "_require_process_absence"), \
                patch.object(h, "watch_outcome_from_bytes", side_effect=lambda data: outcomes[data]), \
                patch.object(h, "watch_outcome_to_bytes", side_effect=lambda outcome: outcome.raw), \
                patch.object(h, "watch_outcome_id_for", side_effect=lambda outcome: outcome.watch_id), \
                patch.object(h, "_load_validated_phase_bundle", side_effect=load_validated) as loader:
            received = backend.finalize_gate(self.run_root)
            envelope_path = self.run_root / "full-stack-envelope.json"
            selection_path = self.run_root / "selection.json"
            self.assertEqual(received.value["envelope"], h.load_envelope(envelope_path))
            # Finalization, selection reload, and envelope reload each consume all
            # ten phase bundles; durable-state validation is isolated above.
            self.assertEqual(30, loader.call_count)

            original_selection = h.read_exact(selection_path)
            alternate_matrix = {
                key: copy.deepcopy(received.value["selection"][key])
                for key in h.runtime_capability.MATRIX_FIELDS
            }
            alternate_matrix["candidates"][0]["observations"][0]["after"].append(module.entry("surprise.py"))
            alternate_selection = h.derive_selection(alternate_matrix)
            self.assertEqual("Package", alternate_selection["selection"])
            selection_path.write_bytes(h.capability_to_bytes(alternate_selection))
            with self.assertRaises(h.GateError):
                h.load_envelope(envelope_path)
            selection_path.write_bytes(original_selection)

            original_envelope = h.load_exact_json(envelope_path)

            def recompute_envelope(value):
                value.pop("envelopeId", None)
                value["envelopeId"] = "full-stack-envelope-sha256:" + h._sha256(h._canonical(value))

            for label, mutate in (
                ("source", lambda value: value["source"].__setitem__("commit", "f" * 40)),
                ("preparation", lambda value: value.__setitem__("preparationId", "preparation-sha256:" + "f" * 64)),
                ("phase", lambda value: value["phaseIds"].__setitem__(0, "observation-sha256:" + "f" * 64)),
                ("watch", lambda value: value["watchOutcomeIds"].__setitem__(0, "watch-outcome-sha256:" + "f" * 64)),
                ("effect", lambda value: value["effectIds"].__setitem__(0, "effect-sha256:" + "f" * 64)),
                ("policy", lambda value: value["policies"].__setitem__("protocol", 999)),
            ):
                with self.subTest(envelope_binding=label):
                    changed = copy.deepcopy(original_envelope)
                    mutate(changed)
                    recompute_envelope(changed)
                    envelope_path.write_bytes(h._canonical(changed))
                    with self.assertRaises(h.GateError):
                        h.load_envelope(envelope_path)
            envelope_path.write_bytes(h._canonical(original_envelope))

        self.assertEqual("SingleFile", received.value["selection"]["selection"])
        self.assertFalse(received.value["envelope"]["authority"])
        self.assertFalse(received.value["envelope"]["bridgeConsumptionPermitted"])
        self.assertEqual(received.value["selection"], h.load_capability(self.run_root / "selection.json"))
        self.assertEqual(received.value["envelope"], h.load_exact_json(self.run_root / "full-stack-envelope.json"))

    def test_controller_terminalizes_failure_and_never_reuses_incomplete_phase(self):
        backend = _FakeLiveBackend(self.run_root)
        backend.fail_after_launch = True
        with self.assertRaisesRegex(h.GateError, "native-after failed"):
            h.run_operator_phase(
                self.run_root,
                "SingleFile",
                "Control",
                backend=backend,
                exchange=_FakeExchange(),
            )
        self.assertEqual(1, backend.failures)
        self.assertTrue(backend.terminal)
        with self.assertRaises(h.GateError):
            h.run_operator_phase(
                self.run_root,
                "SingleFile",
                "Control",
                backend=backend,
                exchange=_FakeExchange(),
            )

    def test_controller_preserves_partial_begin_receipt_in_terminal_failure(self):
        backend = _FakeLiveBackend(self.run_root)
        backend.fail_begin = True
        with self.assertRaisesRegex(h.GateError, "begin failed"):
            h.run_operator_phase(
                self.run_root,
                "SingleFile",
                "Control",
                backend=backend,
                exchange=_FakeExchange(),
            )
        self.assertEqual((31, 41), (
            backend.failure_effects.watcher_pid,
            backend.failure_effects.mo2_pid,
        ))

    def test_controller_terminalizes_post_publication_reconstruction_failure(self):
        backend = _FakeLiveBackend(self.run_root)
        backend.fail_publish_reconstruction = True
        with self.assertRaisesRegex(h.GateError, "durable reconstruction failed"):
            h.run_operator_phase(
                self.run_root,
                "SingleFile",
                "Control",
                backend=backend,
                exchange=_FakeExchange(),
            )
        self.assertEqual(1, backend.failures)
        self.assertTrue(backend.terminal)
        self.assertEqual((31, 41), (
            backend.failure_effects.watcher_pid,
            backend.failure_effects.mo2_pid,
        ))

    def test_envelope_binds_inputs_and_cannot_claim_bridge_authority(self):
        fabricated = {"selection": "SingleFile", "capabilityId": "mo2-runtime-capability-sha256:" + "4" * 64}
        source = {"commit": "1" * 40, "tree": "2" * 40, "artifactId": "containment-source-artifact-sha256:" + "3" * 64}
        with self.assertRaises(h.GateError):
            h.build_envelope(
                run_id="a" * 32,
                source=source,
                preparation_id="preparation-sha256:" + "5" * 64,
                phase_ids=tuple("observation-sha256:" + f"{index:x}" * 64 for index in range(10)),
                watch_outcome_ids=tuple("watch-outcome-sha256:" + f"{index:x}" * 64 for index in range(10)),
                effect_ids=tuple("effect-sha256:" + f"{index:x}" * 64 for index in range(10)),
                selection=fabricated,
            )

        product_test = REPO / "tests" / "test_mo2_bridge_runtime_capability.py"
        spec = importlib.util.spec_from_file_location("task_7b_envelope_capability_fixture", product_test)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(module)
        selection = h.derive_selection(module.fixture(self.scratch / "envelope"))
        source = {
            **selection["source"],
            "artifactId": "containment-source-artifact-sha256:" + "3" * 64,
        }
        envelope = h.build_envelope(
            run_id=selection["runId"],
            source=source,
            preparation_id="preparation-sha256:" + "5" * 64,
            phase_ids=tuple("observation-sha256:" + f"{index:x}" * 64 for index in range(10)),
            watch_outcome_ids=tuple("watch-outcome-sha256:" + f"{index:x}" * 64 for index in range(10)),
            effect_ids=tuple("effect-sha256:" + f"{index:x}" * 64 for index in range(10)),
            selection=selection,
        )
        self.assertFalse(envelope["authority"])
        self.assertNotIn("bridgeReceiptId", envelope)
        self.assertEqual(10, len(envelope["phaseIds"]))
        self.assertEqual(selection["capabilityId"], envelope["selectionId"])
        self.assertEqual(
            hashlib.sha256(h.capability_to_bytes(selection)).hexdigest(),
            envelope["selectionSha256"],
        )
        with self.assertRaises(h.GateError):
            h.build_envelope(
                run_id="a" * 32, source=source,
                preparation_id="preparation-sha256:" + "5" * 64,
                phase_ids=(), watch_outcome_ids=(), effect_ids=(), selection=selection,
            )
        with self.assertRaises(h.GateError):
            h.build_envelope(
                run_id="a" * 32, source=source,
                preparation_id="preparation-sha256:" + "5" * 64,
                phase_ids=tuple("observation-sha256:" + f"{index:x}" * 64 for index in range(10)),
                watch_outcome_ids=tuple("watch-outcome-sha256:" + f"{index:x}" * 64 for index in range(10)),
                effect_ids=tuple("effect-sha256:" + f"{index:x}" * 64 for index in range(10)),
                selection={**selection, "nested": {"bridgeReceiptId": "forged"}},
            )


class _FakeBackend:
    def __init__(self, scratch: Path, listing: ArchiveListing) -> None:
        self.scratch = scratch
        self.listing = listing
        self.mutations: list[str] = []
        self.imported: list[str] = []
        self.prepared: list[str] = []
        self.applied: list[str] = []
        self.relocated: list[str] = []
        self.mode = BootstrapReceiptMode.CREATED
        self._planned: dict[Path, object] = {}
        self.extractor_path = Path(r"C:\Windows\System32\tar.exe")
        self.extractor_sha256 = "7" * 64
        self.extractor_size = 1

    def inspect_inputs(self, config):
        archive = h._stable_file(config.archive_path)
        archive["originalName"] = config.archive_path.name
        extractor_path = self.extractor_path
        mo2_bytes = b"fixture-mo2"
        return SimpleNamespace(
            listing=self.listing,
            archive=archive,
            extractor={
                "path": str(extractor_path),
                "sha256": self.extractor_sha256,
                "size": self.extractor_size,
                "version": "bsdtar fixture",
            },
            release_descriptor_sha256="6" * 64,
            mo2={
                "version": "2.5.2.0",
                "sha256": hashlib.sha256(mo2_bytes).hexdigest(),
                "size": len(mo2_bytes),
            },
            game_root=config.steam_root / "steamapps/common/Skyrim Special Edition",
        )

    def import_archive(self, layout: str, workspace: Path, archive: Path):
        self.mutations.append("import:" + layout)
        self.imported.append(layout)
        return ArchiveVault(workspace).import_archive(
            archive,
            source_note=f"Task 7B disposable {layout} full-stack gate",
            imported_at="2026-09-04T00:00:00Z",
        )

    def prepare_setup(self, layout: str, artifact_id: str, workspace: Path, steam_root: Path):
        self.mutations.append("prepare:" + layout)
        self.prepared.append(layout)
        budget = admit_paths((PlannedPath("fixture", "preparation-wide", str(workspace.absolute())),))
        final = workspace / "tools/mo2/skyrim-se-ae"
        artifact = ArchiveVault(workspace).get(artifact_id)
        metadata_path = workspace / "library" / "metadata" / "artifacts" / f"{artifact.sha256}.json"
        extractor_path = self.extractor_path
        game_executable = h._stable_file(steam_root / "steamapps/common/Skyrim Special Edition/SkyrimSE.exe")
        archive_evidence = ArchiveEvidence(
            artifact_id=artifact.artifact_id,
            metadata_sha256=h._stable_file(metadata_path)["sha256"],
            original_name=artifact.original_name,
            stored_path=artifact.stored_relative_path,
            sha256=artifact.sha256,
            size=artifact.size,
            entry_count=self.listing.entry_count,
            listing_sha256=self.listing.canonical_sha256,
        )
        extractor = ExtractorIdentity(
            FileIdentity(str(extractor_path), self.extractor_sha256, self.extractor_size),
            "bsdtar fixture",
        )
        plan = make_plan_fixture(
            schema_version=2,
            workspace_root=str(workspace),
            steam_root=str(steam_root),
            game_root=str(steam_root / "steamapps/common/Skyrim Special Edition"),
            final_root=str(final),
            staging_parent=str(final.parent),
            staging_name_template=".s<job-base32>",
            skyrim_executable=FileIdentity(
                str(steam_root / "steamapps/common/Skyrim Special Edition/SkyrimSE.exe"),
                str(game_executable["sha256"]),
                int(game_executable["size"]),
            ),
            release_descriptor_sha256="6" * 64,
            archive=archive_evidence,
            extractor=extractor,
            target=TargetSnapshot("Empty", str(final), "0" * 64, 0),
            path_budget=budget,
            programs_launched=(f"tar-plan:{layout}",),
        )
        self._planned[workspace] = plan
        fixture_plan_path = workspace / "runtime" / "fixture-plan.json"
        fixture_plan_path.parent.mkdir(parents=True, exist_ok=True)
        fixture_plan_path.write_bytes(b"fixture plan evidence")
        return SimpleNamespace(
            plan=plan,
            paths_written=(fixture_plan_path,),
            downloads=(),
            installations=(),
            manager_changes=(),
            game_changes=(),
            programs_launched=(f"tar-plan:{layout}",),
        )

    def apply_setup(self, layout: str, plan_id: str, workspace: Path, job_id: str):
        self.mutations.append("apply:" + layout)
        self.applied.append(layout)
        planned = self._latest_plan(workspace)
        app = workspace / "tools/mo2/skyrim-se-ae/app"
        (app / "plugins").mkdir(parents=True)
        runtime_bytes = {
            "ModOrganizer.exe": b"fixture-mo2",
            "plugins/plugin_python/dlls/python312.dll": b"fixture-python",
            "plugins/plugin_python/libs/mobase.cp312-win_amd64.pyd": b"fixture-mobase",
        }
        for relative, data in runtime_bytes.items():
            target = app.joinpath(*relative.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (app / "ModOrganizer.ini").write_bytes(
            render_modorganizer_ini(
                h.workspace_layout(workspace),
                Path(planned.game_root),
                SimpleNamespace(product_version="2.5.2.0"),
            )
        )
        mo2 = h._stable_file(app / "ModOrganizer.exe")
        receipt = make_receipt_fixture(
            mode=self.mode,
            plan_id=plan_id,
            job_id=job_id,
            release_descriptor_sha256=planned.release_descriptor_sha256,
            archive_artifact_id=planned.archive.artifact_id,
            archive_metadata_sha256=planned.archive.metadata_sha256,
            archive_sha256=planned.archive.sha256,
            archive_size=planned.archive.size,
            skyrim_executable=planned.skyrim_executable,
            mo2_executable=FileIdentity(str(app / "ModOrganizer.exe"), str(mo2["sha256"]), int(mo2["size"])),
            final_root=planned.final_root,
            downloads_root=str(Path(planned.final_root) / "downloads"),
            mods_root=str(Path(planned.final_root) / "mods"),
            profiles_root=str(Path(planned.final_root) / "profiles"),
            overwrite_root=str(Path(planned.final_root) / "overwrite"),
            webcache_root=str(Path(planned.final_root) / "webcache"),
            extractor=planned.extractor,
        )
        journal = make_journal_fixture(
            schema_version=2,
            state=BootstrapJobState.VERIFIED,
            job_id=job_id,
            plan_id=plan_id,
            stage_root=str(Path(planned.final_root).parent / stage_name(job_id, 2)),
            prior_root=str(workspace / "runtime" / "jobs" / "mo2-bootstrap" / job_id.split(":", 1)[1] / "prior"),
            final_root=planned.final_root,
            receipt_id=receipt.receipt_id,
            stage_inventory_sha256=receipt.package_inventory_sha256,
            stage_entry_count=receipt.package_file_count,
            activated_inventory_sha256=receipt.package_inventory_sha256,
            activated_entry_count=receipt.package_file_count,
            path_budget=planned.path_budget,
        )
        fixture_receipt = workspace / "runtime" / "fixture-receipt.json"
        fixture_receipt.write_bytes(b"fixture receipt evidence")
        return SimpleNamespace(
            outcome=self.mode.value,
            receipt=receipt,
            journal=journal,
            paths_written=("runtime/fixture-receipt.json",),
            downloads=(),
            installations=("portable-mo2-create",),
            manager_changes=tuple(
                f"tools/mo2/skyrim-se-ae/app/{relative}"
                for relative in (*runtime_bytes, "ModOrganizer.ini")
            ),
            game_changes=(),
            programs_launched=(f"tar-apply:{layout}",),
        )

    def _latest_plan(self, workspace: Path):
        from modlab.adapters.mo2.bootstrap_serialization import plan_from_bytes

        plans = sorted((workspace / "runtime" / "jobs" / "mo2-bootstrap" / "plans").glob("*.json"))
        if plans:
            return plan_from_bytes(plans[-1].read_bytes())
        return self._planned[workspace]

    def relocate(self, layout: str, source: Path, destination: Path):
        self.mutations.append("relocate:" + layout)
        self.relocated.append(layout)
        return h.relocate_created_app(source, destination)

    def configure(self, layout: str, layout_root: Path, manager_root: Path):
        self.mutations.append("configure:" + layout)
        environment_root = layout_root / "environment"
        roots = [
            manager_root / "downloads", manager_root / "mods", manager_root / "profiles",
            manager_root / "overwrite", manager_root / "webcache", manager_root / "logs",
            *(environment_root / name for name in ("TEMP", "TMP", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME")),
        ]
        for root in roots:
            root.mkdir(parents=True, exist_ok=True)
        for profile in ("ModLab - Lab", "ModLab - Play"):
            target = manager_root / "profiles" / profile / "modlist.txt"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"# fixture\n")
        marker = layout_root / "app" / "nxmhandler.ini"
        marker.write_bytes(h._NOREGISTER)
        integrity_receipt = {
            "executable": r"C:\Windows\System32\icacls.exe",
            "arguments": [str(layout_root), "/setintegritylevel", "(OI)(CI)L", "/T", "/C", "/Q"],
            "exit_code": 0,
            "stdout_sha256": "a" * 64,
            "stderr_sha256": "b" * 64,
            "integrity": int(IntegrityLevel.LOW),
        }
        canonical = h._canonical(integrity_receipt)
        h.containment_service._current_effects().preparation_process(
            "icacls-low:" + h._sha256(canonical) + ":" + canonical.decode("utf-8").rstrip("\n")
        )
        return {
            "managerRoot": str(manager_root),
            "environmentRoot": str(environment_root),
            "roots": [str(path) for path in roots],
            "modOrganizerIni": h._stable_file(layout_root / "app" / "ModOrganizer.ini"),
            "noregister": h._stable_file(marker),
            "lowIntegrity": [integrity_receipt],
        }

    def runtime_identities(self, layout: str, app_root: Path):
        return {
            "layout": layout,
            "files": [
                {"relativePath": relative, **h._stable_file(app_root.joinpath(*relative.split("/")))}
                for relative in h.RUNTIME_PATHS
            ],
        }


class _FakeExchange:
    def window(self, launch):
        launch.events.append("exchange-window")
        return {"windowId": 51, "app": "process:" + launch.executable, "title": "Mod Organizer", "screenshotIds": ["shot-1"]}

    def close(self, launch, window):
        launch.events.append("exchange-close")
        return {"action": "Alt+F4", "windowId": window["windowId"], "returned": True}


class _FakeLiveBackend:
    def __init__(self, run_root: Path) -> None:
        self.run_root = run_root
        self.events: list[str] = []
        self.owner = object()
        self.stop_owner = None
        self.fail_after_launch = False
        self.fail_begin = False
        self.fail_publish_reconstruction = False
        self.failures = 0
        self.terminal = False
        self.failure_effects = ContainmentEffects()

    def source_check(self, run_root):
        self.events.append("source-check")

    def load_state(self, run_root, layout):
        self.events.append("load-state")
        if self.terminal:
            raise h.GateError("phase attempt is already terminal")
        return SimpleNamespace(completed=(), installed=False)

    def begin_phase(self, run_root, layout, phase):
        self.events.append("begin:" + phase)
        effects = ContainmentEffects(
            written_paths=(run_root,), child_mutation_roots=(run_root,),
            watcher_pid=31, mo2_pid=41,
        )
        if self.fail_begin:
            raise ContainmentOperationError("begin failed", effects=effects)
        session = SimpleNamespace(
            owner=self.owner,
            events=self.events,
            executable=str(run_root / layout / "app" / "ModOrganizer.exe"),
        )
        return h.ContainmentServiceResult(
            session,
            effects,
        )

    def native(self, session, kind):
        self.events.append(kind)
        if kind == "native-after" and self.fail_after_launch:
            raise h.GateError("native-after failed")
        return {"kind": kind, "process": {"pid": 41}}

    def finalize_phase(self, session, native_before, window, native_after, close):
        self.events.append("finalize:Control")
        self.stop_owner = session.owner
        return h.ContainmentServiceResult(
            {"nativeBefore": native_before, "window": window, "nativeAfter": native_after, "close": close},
            ContainmentEffects(
                written_paths=(self.run_root,), child_mutation_roots=(self.run_root,),
                watcher_pid=31, mo2_pid=41,
            ),
        )

    def publish_phase(self, session, draft, effects):
        self.events.append("publish:Control")
        if self.fail_publish_reconstruction:
            raise h.GateError("durable reconstruction failed")
        return {"observationId": "observation-sha256:" + "8" * 64}

    def fail_phase(self, run_root, layout, phase, error, effects, session=None):
        self.failures += 1
        self.terminal = True
        self.failure_effects = effects


class _FakeInstallBackend:
    def __init__(self, run_root: Path) -> None:
        self.run_root = run_root
        self.events: list[str] = []
        self.fail_install_requested = False
        self.terminal = False

    def source_check(self, run_root):
        self.events.append("source-check")

    def load_state(self, run_root, layout):
        self.events.append("load-state")
        if self.terminal:
            raise h.GateError("installation attempt is terminal")
        return h.DurableLayoutState(("Control",), False)

    def install_candidate(self, run_root, layout, control_id):
        self.events.append("install")
        effects = ContainmentEffects(
            written_paths=(self.run_root,),
            child_mutation_roots=(self.run_root,),
        )
        if self.fail_install_requested:
            raise ContainmentOperationError("install failed", effects=effects)
        return h.ContainmentServiceResult({"controlId": control_id}, effects)

    def publish_install(self, run_root, layout, draft, effects):
        self.events.append("publish")
        return h.ContainmentServiceResult(
            {"installationId": "installation-sha256:" + "9" * 64},
            ContainmentEffects(written_paths=(Path(run_root) / layout / "installation.json",)),
        )

    def fail_install(self, run_root, layout, control_id, error, effects):
        self.events.append("fail-install")
        self.terminal = True


class HarnessPresenceTest(unittest.TestCase):
    def test_reviewed_disposable_harness_exists(self):
        self.assertIsNotNone(h, "Task 7B live-gate harness has not been implemented")


def main() -> int:
    stream = io.StringIO()
    suite = unittest.TestSuite()
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(HarnessPresenceTest))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(OfflineGateTests))
    live_calls = {"archive": 0, "watcher": 0, "application": 0}

    def refuse_live(name):
        def invoke(*_args, **_kwargs):
            live_calls[name] += 1
            raise AssertionError(f"offline suite attempted real {name} operation")
        return invoke

    with ExitStack() as stack:
        stack.enter_context(patch.object(h, "preflight_archive", side_effect=refuse_live("archive")))
        stack.enter_context(patch.object(h, "start_watch", side_effect=refuse_live("watcher")))
        stack.enter_context(patch.object(h, "launch_low_integrity_process", side_effect=refuse_live("application")))
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    try:
        matches = h.enumerate_windows_processes()
        process_check = {
            "complete": True,
            "matchingProcesses": len(matches),
            "pids": [item.pid for item in matches],
        }
    except Exception as error:
        process_check = {
            "complete": False,
            "errorType": type(error).__name__,
            "error": str(error) or type(error).__name__,
        }
    output = stream.getvalue()
    print(output, end="")
    if LOG_PATH is not None:
        with LOG_PATH.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(output)
            handle.write(json.dumps({
                "tests": result.testsRun,
                "failures": len(result.failures),
                "errors": len(result.errors),
                "skips": len(result.skipped),
                "successful": result.wasSuccessful(),
                "realArchiveRead": live_calls["archive"] != 0,
                "realArchiveReadCalls": live_calls["archive"],
                "appLaunched": live_calls["application"] != 0,
                "appLaunchCalls": live_calls["application"],
                "watcherStarted": live_calls["watcher"] != 0,
                "watcherStartCalls": live_calls["watcher"],
                "computerControlUsed": False,
                "processCheckAfter": process_check,
            }, sort_keys=True, separators=(",", ":")) + "\n")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    args = list(sys.argv[1:])
    if len(args) != 4 or args[:2] != ["--scratch", args[1]] or args[2] != "--log":
        raise SystemExit("usage: task-7B-live-gate-tests.py --scratch <fresh-dir> --log <fresh-log>")
    SCRATCH = Path(args[1]).absolute()
    LOG_PATH = Path(args[3]).absolute()
    if SCRATCH.exists() or LOG_PATH.exists():
        raise SystemExit("scratch and log paths must both be fresh")
    SCRATCH.mkdir(parents=True)
    raise SystemExit(main())
else:
    SCRATCH = HERE / "offline-test-import-scratch"
    LOG_PATH = None
