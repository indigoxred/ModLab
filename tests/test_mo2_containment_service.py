import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from modlab.validation.mo2_containment_model import (
    CapabilityVerdict,
    ContainmentScenario,
    IntegrityObservation,
    ProcessEvidence,
    ProtectedState,
    ScenarioCleanupStatus,
    ScenarioJournal,
    ScenarioRecovery,
    ScenarioOutcome,
    ScenarioState,
    TreeIdentity,
    WatchEvidenceCompletion,
    WatchOutcome,
    WatcherEvent,
)
from modlab.validation.mo2_containment_serialization import watch_outcome_id_for
from modlab.validation.mo2_containment_service import (
    RecoveryCleanupEvidence,
    RecoveryProofEvidence,
    ScenarioEvidence,
    adjudicate_results,
    evaluate_scenario,
    recover_scenario,
)
from modlab.validation.mo2_containment_store import ContainmentStore
from modlab.validation import mo2_containment_service as service
from modlab.workspace import initialize_workspace


WATCH_ROOT_KINDS = (
    "SourceMods",
    "LabProfile",
    "PlayProfile",
    "Downloads",
    "Overwrite",
    "BoundedGame",
    "ExternalLocalLow",
    "ExternalTempLow",
)
RUN_ID = "containment-run:0123456789abcdef0123456789abcdef"


def tree(letter: str) -> TreeIdentity:
    return TreeIdentity(letter * 64, 1, 1, 7)


def protected(letter: str = "a", *, play: str | None = None) -> ProtectedState:
    value = tree(letter)
    return ProtectedState(
        value,
        letter * 64,
        (play or letter) * 64,
        value,
        value,
        value,
    )


def event(root_kind: str = "SourceMods") -> WatcherEvent:
    return WatcherEvent(1, root_kind, "Modified", "marker.txt")


def event_bytes(events: tuple[WatcherEvent, ...]) -> bytes:
    return b"".join(
        (
            json.dumps(
                {
                    "action": item.action,
                    "relativePath": item.relative_path,
                    "rootKind": item.root_kind,
                    "sequence": item.sequence,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        for item in events
    )


def watch_outcome(
    scenario: ContainmentScenario,
    *,
    events: tuple[WatcherEvent, ...] = (),
    completion: WatchEvidenceCompletion = WatchEvidenceCompletion.COMPLETED,
    reason: str = "controller-session-lost",
) -> WatchOutcome:
    data = event_bytes(events)
    complete = completion is WatchEvidenceCompletion.COMPLETED
    return WatchOutcome(
        1,
        "watch-request:" + "1" * 64,
        "2" * 64,
        "watch-session:" + "3" * 64,
        RUN_ID,
        scenario,
        41,
        1001,
        42,
        1002,
        completion,
        0 if complete else None,
        True,
        WATCH_ROOT_KINDS,
        events,
        hashlib.sha256(data).hexdigest(),
        7,
        8,
        len(data),
        len(events),
        events[-1].sequence if events else 0,
        "4" * 64,
        True,
        () if complete else (reason,),
    )


def process(*, integrity: IntegrityObservation = IntegrityObservation.LOW) -> ProcessEvidence:
    return ProcessEvidence(
        51,
        str(Path(tempfile.gettempdir()) / "stage" / "ModOrganizer.exe"),
        "2.5.2.0",
        ("--profile", "ModLab - Lab"),
        str(Path(tempfile.gettempdir()) / "stage"),
        integrity,
    )


def evidence(
    scenario: ContainmentScenario,
    **changes,
) -> ScenarioEvidence:
    adopted = scenario in {
        ContainmentScenario.NEW_FOLDER,
        ContainmentScenario.FOMOD_DEPENDENCY,
    }
    expected_name = (
        "ModLab Spike New"
        if scenario is ContainmentScenario.NEW_FOLDER
        else "ModLab Spike FOMOD"
    )
    expected_outputs = (
        ("meshes/new-folder.bin",)
        if scenario is ContainmentScenario.NEW_FOLDER
        else ("always.txt", "dependency-seen.txt")
    )
    values = dict(
        run_id=RUN_ID,
        scenario=scenario,
        protected_before=protected(),
        protected_after=protected(),
        watch_outcome=watch_outcome(scenario),
        mo2_process=process(),
        source_integrity=IntegrityObservation.MEDIUM,
        stage_integrity=IntegrityObservation.LOW,
        scenario_started=True,
        fresh_retry_eligible=False,
        projection_count=1,
        projection_targets_verified=True,
        projection_payload_bytes_copied=0,
        production_backup_names=(),
        staging_new_names=(expected_name,) if adopted else (),
        staging_output_names=expected_outputs if adopted else (),
        adopted_name=expected_name if adopted else None,
        adopted_tree=tree("b") if adopted else None,
        adopted_integrity=IntegrityObservation.MEDIUM if adopted else None,
        source_restored_after_quarantine=True,
        safety_reasons=(),
        incomplete_reasons=(),
    )
    values.update(changes)
    return ScenarioEvidence(**values)


class ContainmentServiceTests(unittest.TestCase):
    def test_replaced_baseline_projection_is_classified_for_quarantine(self):
        with tempfile.TemporaryDirectory(prefix="modlab-replaced-projection-") as directory:
            root = Path(directory)
            source = root / "source"
            stage = root / "stage"
            source.mkdir()
            stage.mkdir()
            (source / "Protected Existing").mkdir()
            (stage / "Protected Existing").mkdir()
            record = service.FixtureRecord(
                ContainmentScenario.REPLACE_EXISTING,
                root, root, root, root / "archive.zip", source, stage,
                root / "lab.txt", root / "play.txt", root, root, root,
                root, root, root, root, root, root, {}, (),
                ("Protected Existing",),
            )
            with patch.object(
                service,
                "inspect_junction",
                side_effect=service.ContainmentSafetyError("replaced"),
            ):
                self.assertEqual(
                    ("Protected Existing",),
                    service._changed_projection_names(record),
                )

    def test_adopted_folder_is_quarantined_even_if_output_inspection_fails(self):
        with tempfile.TemporaryDirectory(prefix="modlab-adoption-quarantine-") as directory:
            root = Path(directory)
            source = root / "source"
            stage = root / "stage"
            source.mkdir()
            stage.mkdir()
            (source / "Protected Existing").mkdir()
            (stage / "Protected Existing").mkdir()
            (stage / "ModLab Spike New").mkdir()
            destination = source / "ModLab Spike New"
            destination.mkdir()
            record = service.FixtureRecord(
                ContainmentScenario.NEW_FOLDER,
                root, root, root, root / "archive.zip", source, stage,
                root / "lab.txt", root / "play.txt", root, root, root,
                root, root, root, root, root, root, {}, (),
                ("Protected Existing",),
            )
            store = ContainmentStore(root / "validation")
            store.write_request(RUN_ID, {"runId": RUN_ID})
            adoption = SimpleNamespace(
                adopted_name="ModLab Spike New",
                destination_path=destination,
                after_tree=tree("b"),
                final_integrity=service.IntegrityLevel.MEDIUM,
            )
            with (
                patch.object(service, "_projection_state", return_value=(1, True)),
                patch.object(service, "adopt_unique_staged_mod", return_value=adoption),
                patch.object(service, "_relative_files", side_effect=OSError("inspect failed")),
                patch.object(service, "_capture_protected", return_value=protected()),
            ):
                result = service._finalize_projection(
                    store,
                    RUN_ID,
                    record,
                    protected(),
                    protected(),
                )
            quarantined = store.quarantine_path(RUN_ID) / record.scenario.value / destination.name
            self.assertFalse(destination.exists())
            self.assertTrue(quarantined.is_dir())
            self.assertIn("adoption-proof-unavailable:OSError", result.incomplete_reasons)

    def test_protected_delta_before_adoption_never_moves_staging_into_source(self):
        with tempfile.TemporaryDirectory(prefix="modlab-pre-adoption-delta-") as directory:
            root = Path(directory)
            source = root / "source"
            stage = root / "stage"
            source.mkdir()
            stage.mkdir()
            (source / "Protected Existing").mkdir()
            (stage / "Protected Existing").mkdir()
            (stage / "ModLab Spike New").mkdir()
            record = service.FixtureRecord(
                ContainmentScenario.NEW_FOLDER,
                root, root, root, root / "archive.zip", source, stage,
                root / "lab.txt", root / "play.txt", root, root, root,
                root, root, root, root, root, root, {}, (),
                ("Protected Existing",),
            )
            store = ContainmentStore(root / "validation")
            store.write_request(RUN_ID, {"runId": RUN_ID})
            with (
                patch.object(service, "_projection_state", return_value=(1, True)),
                patch.object(
                    service,
                    "adopt_unique_staged_mod",
                    side_effect=AssertionError("must not adopt after protected delta"),
                ) as adopt,
                patch.object(service, "_capture_protected", return_value=protected("b")),
            ):
                result = service._finalize_projection(
                    store,
                    RUN_ID,
                    record,
                    protected(),
                    protected("b"),
                )
            adopt.assert_not_called()
            self.assertEqual(protected("b"), result.protected_after)
            self.assertFalse((source / "ModLab Spike New").exists())

    def test_expected_new_folder_does_not_invalidate_retained_projection(self):
        with tempfile.TemporaryDirectory(prefix="modlab-projection-policy-") as directory:
            root = Path(directory)
            source = root / "source"
            stage = root / "stage"
            source.mkdir()
            stage.mkdir()
            (source / "Protected Existing").mkdir()
            (stage / "Protected Existing").mkdir()
            (stage / "ModLab Spike New").mkdir()
            record = service.FixtureRecord(
                ContainmentScenario.NEW_FOLDER,
                root,
                root,
                root,
                root / "archive.zip",
                source,
                stage,
                root / "lab.txt",
                root / "play.txt",
                root,
                root,
                root,
                root,
                root,
                root,
                root,
                root,
                root,
                {},
                (),
                ("Protected Existing",),
            )
            with patch.object(
                service,
                "inspect_junction",
                return_value=SimpleNamespace(
                    target_path=source / "Protected Existing"
                ),
            ):
                self.assertEqual((1, True), service._projection_state(record, allow_new=True))

    def test_merge_and_replace_pass_only_with_zero_source_events_and_hash_match(self):
        for scenario in (
            ContainmentScenario.MERGE_EXISTING,
            ContainmentScenario.REPLACE_EXISTING,
        ):
            with self.subTest(scenario=scenario):
                result = evaluate_scenario(evidence(scenario))
                self.assertEqual(ScenarioOutcome.PASSED, result.outcome)
                self.assertEqual(result.protected_before, result.protected_after)
                self.assertEqual((), result.watcher_events)
                self.assertEqual((), result.production_backup_names)
                self.assertIsNone(result.adopted_name)

    def test_supported_requires_all_four_exact_passes(self):
        pairs = tuple(
            (evaluate_scenario(evidence(scenario)), watch_outcome(scenario))
            for scenario in ContainmentScenario
        )
        decision = adjudicate_results(
            tuple(item[0] for item in pairs), tuple(item[1] for item in pairs)
        )
        self.assertEqual(CapabilityVerdict.SUPPORTED, decision.verdict)

        incomplete = adjudicate_results(
            tuple(item[0] for item in pairs[:-1]),
            tuple(item[1] for item in pairs[:-1]),
        )
        self.assertEqual(CapabilityVerdict.INCOMPLETE, incomplete.verdict)

    def test_valid_forbidden_event_then_controller_death_is_failed_without_retry(self):
        outcome = watch_outcome(
            ContainmentScenario.MERGE_EXISTING,
            events=(event("PlayProfile"),),
            completion=WatchEvidenceCompletion.INCOMPLETE,
        )
        result = evaluate_scenario(
            evidence(
                ContainmentScenario.MERGE_EXISTING,
                watch_outcome=outcome,
            )
        )
        self.assertEqual(ScenarioOutcome.FAILED, result.outcome)
        self.assertEqual(
            WatchEvidenceCompletion.INCOMPLETE,
            result.watch_evidence_completion,
        )
        self.assertFalse(result.fresh_retry_eligible)

    def test_completed_typed_policy_failures_are_failed_without_root_breach(self):
        scenario = ContainmentScenario.MERGE_EXISTING
        cases = (
            ("source event", dict(watch_outcome=watch_outcome(scenario, events=(event(),))), ScenarioOutcome.FAILED),
            ("final hash mismatch", dict(protected_after=protected("b")), ScenarioOutcome.FAILED),
            ("Play byte change", dict(protected_after=protected(play="b")), ScenarioOutcome.FAILED),
            ("watcher overflow", dict(watch_outcome=watch_outcome(scenario, completion=WatchEvidenceCompletion.INCOMPLETE, reason="notification-overflow")), ScenarioOutcome.INCOMPLETE),
            ("MO2 still running", dict(incomplete_reasons=("mo2-still-running",)), ScenarioOutcome.INCOMPLETE),
            ("wrong process integrity", dict(mo2_process=process(integrity=IntegrityObservation.MEDIUM)), ScenarioOutcome.FAILED),
            ("unexpected stage backup", dict(production_backup_names=("Protected Existing_backup",)), ScenarioOutcome.FAILED),
            ("source junction replaced", dict(projection_targets_verified=False), ScenarioOutcome.FAILED),
        )
        for label, changes, expected in cases:
            with self.subTest(label=label):
                result = evaluate_scenario(evidence(scenario, **changes))
                self.assertEqual(expected, result.outcome)
                self.assertFalse(result.fresh_retry_eligible)

        incomplete_watch = watch_outcome(
            scenario,
            completion=WatchEvidenceCompletion.INCOMPLETE,
        )
        self.assertEqual(
            ScenarioOutcome.INCOMPLETE,
            evaluate_scenario(
                evidence(
                    scenario,
                    watch_outcome=incomplete_watch,
                    production_backup_names=("Protected Existing_backup",),
                )
            ).outcome,
        )

    def test_prepare_run_generates_identity_before_confined_fixture_creation(self):
        with tempfile.TemporaryDirectory(prefix="modlab-prepare-run-") as directory:
            source = Path(directory) / "source"
            layout = initialize_workspace(source)
            validation = layout.mo2_containment_validation
            steam = Path(directory) / "steam"
            calls = []

            def fake_prepare(_source, _artifact, _steam, _validation, scenario, *, fixture_parent=None):
                calls.append((scenario, Path(fixture_parent)))
                return SimpleNamespace(scenario=scenario, fixture_parent=Path(fixture_parent))

            def fake_record(fixture, _steam):
                parent = fixture.fixture_parent
                return service.FixtureRecord(
                    fixture.scenario, parent, parent / "source", parent / "stage",
                    parent / "archive.zip", parent / "source" / "mods",
                    parent / "stage" / "mods", parent / "source" / "lab.txt",
                    parent / "source" / "play.txt", parent / "source" / "downloads",
                    parent / "source" / "overwrite", steam / "game", parent / "stage" / "app",
                    parent / "stage" / "downloads", parent / "stage" / "profiles",
                    parent / "stage" / "overwrite", parent / "stage" / "cache",
                    parent / "stage" / "logs",
                    {name: str(parent / "env") for name in ("TEMP", "TMP", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME")},
                    (("ExternalLocalLow", parent / "local-low"), ("ExternalTempLow", parent / "temp-low")),
                    ("Protected Existing",),
                )

            with (
                patch.object(service, "prepare_containment_fixture", side_effect=fake_prepare),
                patch.object(service, "_fixture_record", side_effect=fake_record),
            ):
                run_id = service.prepare_run(source, "artifact:abc", steam, validation)

            run_root = validation / run_id.removeprefix("containment-run:")
            self.assertEqual(tuple(ContainmentScenario), tuple(item[0] for item in calls))
            self.assertTrue(all(parent.is_relative_to(run_root) for _, parent in calls))
            self.assertEqual(
                tuple(run_root / "fixtures" / item.value for item in ContainmentScenario),
                tuple(parent for _, parent in calls),
            )
            request = ContainmentStore(validation).load_request(run_id)
            self.assertRegex(request["commandFingerprint"], r"^containment-command-sha256:[0-9a-f]{64}$")
            self.assertEqual([], request["predecessorRunIds"])
            self.assertIsNone(request["retryOf"])

    def test_arm_scenario_public_workflow_persists_before_watcher_ready(self):
        with tempfile.TemporaryDirectory(prefix="modlab-arm-workflow-") as directory:
            root = Path(directory)
            store = ContainmentStore(root / "validation")
            record = service.FixtureRecord(
                ContainmentScenario.MERGE_EXISTING, root / "fixture", root / "source",
                root / "stage", root / "archive.zip", root / "source-mods",
                root / "stage-mods", root / "lab.txt", root / "play.txt",
                root / "downloads", root / "overwrite", root / "game", root / "app",
                root / "stage-downloads", root / "profiles", root / "stage-overwrite",
                root / "cache", root / "logs", {}, (), ("Protected Existing",),
            )
            observed = []

            def fake_start(_request):
                observed.append(store.load_protected_state(RUN_ID, record.scenario, "before"))
                return 42

            with (
                patch.object(service, "_load_fixture_record", return_value=record),
                patch.object(service, "_capture_protected", return_value=protected()),
                patch.object(service, "inspect_path_integrity", side_effect=(service.IntegrityLevel.MEDIUM, service.IntegrityLevel.LOW)),
                patch.object(service, "_projection_state", return_value=(1, True)),
                patch.object(service, "_watch_roots", return_value=()),
                patch.object(service, "start_watch", side_effect=fake_start),
            ):
                armed = service.arm_scenario(store.root, RUN_ID, record.scenario)
            self.assertEqual(ScenarioState.ARMED, armed.state)
            self.assertEqual([protected()], observed)

    def test_launch_and_capture_reload_exact_durable_launch_after_restart(self):
        with tempfile.TemporaryDirectory(prefix="modlab-launch-workflow-") as directory:
            root = Path(directory)
            store = ContainmentStore(root / "validation")
            retry_binding = {
                "runId": "containment-run:" + "f" * 32,
                "scenario": ContainmentScenario.MERGE_EXISTING.value,
                "recoveryId": "containment-recovery-sha256:" + "a" * 64,
                "authorityId": "containment-retry-sha256:" + "b" * 64,
                "commandFingerprint": "containment-command-sha256:" + "c" * 64,
            }
            store.write_request(
                RUN_ID,
                {
                    "runId": RUN_ID,
                    "commandFingerprint": retry_binding["commandFingerprint"],
                    "retryOf": retry_binding,
                },
            )
            fixture = root / "fixture"
            fixture.mkdir()
            record = service.FixtureRecord(
                ContainmentScenario.MERGE_EXISTING, fixture, fixture / "source", fixture / "stage",
                fixture / "archive.zip", fixture / "source-mods", fixture / "stage-mods",
                fixture / "lab.txt", fixture / "play.txt", fixture / "downloads",
                fixture / "overwrite", fixture / "game", fixture / "app", fixture / "stage-downloads",
                fixture / "profiles", fixture / "stage-overwrite", fixture / "cache", fixture / "logs",
                {}, (), ("Protected Existing",),
            )
            armed = store.create(ScenarioJournal(
                1, RUN_ID, record.scenario, ScenarioState.ARMED,
                str(record.source_root), str(record.stage_root), str(record.archive_path),
                "Protected Existing", "Protected Existing", protected(), 42, None, None,
            ))
            native_launch = SimpleNamespace(
                pid=51, executable=str(record.executable),
                arguments=("--profile", "ModLab - Lab"),
                working_directory=str(record.stage_app), integrity=service.IntegrityLevel.LOW,
                creation_time=987654321,
            )
            observation = SimpleNamespace(
                complete=True,
                relevant=(SimpleNamespace(pid=51, executable_path=record.executable),),
            )
            with (
                patch.object(service, "_load_fixture_record", return_value=record),
                patch.object(service, "_watcher_live", return_value=True),
                patch.object(service, "_capture_protected", return_value=protected()),
                patch.object(service, "read_windows_file_version", return_value="2.5.2.0"),
                patch.object(service, "launch_low_integrity_process", return_value=native_launch),
                patch.object(service, "inspect_process_integrity", return_value=service.IntegrityLevel.LOW),
                patch.object(service, "inspect_mo2_processes", return_value=observation),
                patch.object(service, "_reprove_retry_absence") as reprove,
            ):
                launched = service.launch_scenario(store.root, RUN_ID, record.scenario)
            reprove.assert_called_once()
            self.assertEqual(store.root, reprove.call_args.args[0].root)
            self.assertEqual((RUN_ID, retry_binding), reprove.call_args.args[1:])
            self.assertEqual(ScenarioState.LAUNCHED, launched.state)
            durable = store.load_launch_evidence(RUN_ID, record.scenario)
            self.assertEqual(987654321, durable["creationTime"])
            self.assertFalse(hasattr(service, "_LAUNCH_EVIDENCE"))

            outcome = watch_outcome(record.scenario)
            store.write_watch_outcome(outcome)
            receipt = SimpleNamespace(
                watch_outcome_id=watch_outcome_id_for(outcome), run_id=RUN_ID,
                scenario=record.scenario, request_id=outcome.request_id,
                session_id=outcome.session_id, worker_pid=outcome.worker_pid,
                request_bytes_sha256=outcome.request_sha256,
            )
            projection = service._ProjectionEvidence(
                protected(), 1, True, (), (), (), None, None, None, True, (), (),
            )
            with (
                patch.object(service, "_load_fixture_record", return_value=record),
                patch.object(service, "inspect_mo2_processes", return_value=SimpleNamespace(complete=True, relevant=())),
                patch.object(service, "stop_watch", return_value=receipt),
                patch.object(service, "_capture_protected", return_value=protected()),
                patch.object(service, "_finalize_projection", return_value=projection),
                patch.object(service, "_integrity_observation", side_effect=(IntegrityObservation.MEDIUM, IntegrityObservation.LOW)),
                patch.object(service, "_exact_process_absent", return_value=True),
            ):
                result = service.capture_scenario(store.root, RUN_ID, record.scenario)
            self.assertEqual(51, result.mo2_process.pid)

    def test_prepare_run_consumes_retry_before_fixture_work_and_never_refunds_it(self):
        with tempfile.TemporaryDirectory(prefix="modlab-retry-consume-") as directory:
            source = Path(directory) / "source"
            layout = initialize_workspace(source)
            steam = Path(directory) / "steam"
            store = ContainmentStore(layout.mo2_containment_validation)
            recovery = ScenarioRecovery(
                1, RUN_ID, ContainmentScenario.MERGE_EXISTING,
                "containment-journal-sha256:" + "a" * 64,
                "containment-result-sha256:" + "b" * 64,
                ScenarioCleanupStatus.SUCCEEDED, True, (),
            )
            written = store.write_recovery(recovery)
            fingerprint = service._command_fingerprint(source, "artifact:abc", steam)
            store.write_retry_authority(recovery, written.content_id, fingerprint)

            with patch.object(
                service,
                "prepare_containment_fixture",
                side_effect=RuntimeError("injected fixture failure"),
            ) as fixture:
                with self.assertRaisesRegex(RuntimeError, "injected fixture failure"):
                    service.prepare_run(
                        source, "artifact:abc", steam, store.root, retry_of=recovery
                    )
                self.assertEqual(1, fixture.call_count)
                consumed = store.load_retry_authority(RUN_ID, recovery.scenario)
                self.assertTrue(
                    store.run_path(consumed["consumedByRunId"]).is_dir()
                )
                with self.assertRaisesRegex(service.ContainmentServiceError, "consumed"):
                    service.prepare_run(
                        source, "artifact:abc", steam, store.root, retry_of=recovery
                    )
                self.assertEqual(1, fixture.call_count)

    def test_prepare_run_binds_the_single_consumed_retry_to_its_new_request(self):
        with tempfile.TemporaryDirectory(prefix="modlab-retry-request-") as directory:
            source = Path(directory) / "source"
            layout = initialize_workspace(source)
            steam = Path(directory) / "steam"
            store = ContainmentStore(layout.mo2_containment_validation)
            recovery = ScenarioRecovery(
                1, RUN_ID, ContainmentScenario.MERGE_EXISTING,
                "containment-journal-sha256:" + "a" * 64,
                "containment-result-sha256:" + "b" * 64,
                ScenarioCleanupStatus.SUCCEEDED, True, (),
            )
            recovery_write = store.write_recovery(recovery)
            fingerprint = service._command_fingerprint(source, "artifact:abc", steam)
            authority = store.write_retry_authority(
                recovery, recovery_write.content_id, fingerprint
            )
            with (
                patch.object(
                    service,
                    "prepare_containment_fixture",
                    side_effect=lambda *_args, **kwargs: kwargs["fixture_parent"],
                ),
                patch.object(
                    service,
                    "_fixture_record",
                    side_effect=lambda fixture, _steam: fixture,
                ),
                patch.object(
                    service,
                    "_record_document",
                    side_effect=lambda record: {"fixtureParent": str(record)},
                ),
            ):
                new_run = service.prepare_run(
                    source, "artifact:abc", steam, store.root, retry_of=recovery
                )
            request = store.load_request(new_run)
            self.assertEqual(authority.content_id, request["retryOf"]["authorityId"])
            self.assertEqual(RUN_ID, request["retryOf"]["runId"])
            consumed = store.load_retry_authority(RUN_ID, recovery.scenario)
            self.assertEqual(new_run, consumed["consumedByRunId"])

    def test_adjudicate_run_retains_same_command_failed_history_only(self):
        with tempfile.TemporaryDirectory(prefix="modlab-history-") as directory:
            store = ContainmentStore(Path(directory))
            failed_run = "containment-run:" + "1" * 32
            current_run = "containment-run:" + "2" * 32
            unrelated_run = "containment-run:" + "3" * 32
            source = store.root / "source"
            steam = store.root / "steam"

            def write_run(run_id, artifact, failed, predecessors=()):
                fingerprint = service._command_fingerprint(source, artifact, steam)
                store.write_request(
                    run_id,
                    {
                        "runId": run_id,
                        "sourceWorkspace": str(source.absolute()),
                        "mo2ArtifactId": artifact,
                        "steamRoot": str(steam.absolute()),
                        "commandFingerprint": fingerprint,
                        "predecessorRunIds": list(predecessors),
                        "retryOf": None,
                    },
                )
                identifiers = []
                for scenario in ContainmentScenario:
                    observed = replace(
                        watch_outcome(
                            scenario,
                            events=(event(),) if failed and scenario is ContainmentScenario.MERGE_EXISTING else (),
                        ),
                        run_id=run_id,
                    )
                    result = evaluate_scenario(
                        replace(evidence(scenario), run_id=run_id, watch_outcome=observed)
                    )
                    store.write_watch_outcome(observed)
                    identifiers.append(store.write_result(result).content_id)
                return tuple(identifiers)

            historical_ids = write_run(failed_run, "artifact:same", True)
            current_ids = write_run(
                current_run, "artifact:same", False, (failed_run,)
            )
            write_run(unrelated_run, "artifact:other", True)
            decision = service.adjudicate_run(store.root, current_run)
            self.assertEqual(CapabilityVerdict.REJECTED, decision.verdict)
            self.assertEqual(current_ids, decision.scenario_result_ids)
            failed_id = historical_ids[list(ContainmentScenario).index(ContainmentScenario.MERGE_EXISTING)]
            self.assertTrue(any(failed_id in reason for reason in decision.reasons))
            self.assertTrue(
                all(any(identifier in reason for reason in decision.reasons) for identifier in historical_ids)
            )
            self.assertFalse(any(unrelated_run in reason for reason in decision.reasons))

    def test_adjudicate_run_turns_damaged_current_evidence_into_incomplete(self):
        with tempfile.TemporaryDirectory(prefix="modlab-damaged-adjudication-") as directory:
            store = ContainmentStore(Path(directory))
            run_id = "containment-run:" + "4" * 32
            source = store.root / "source"
            steam = store.root / "steam"
            artifact = "artifact:damaged"
            fingerprint = service._command_fingerprint(source, artifact, steam)
            store.write_request(
                run_id,
                {
                    "runId": run_id,
                    "sourceWorkspace": str(source.absolute()),
                    "mo2ArtifactId": artifact,
                    "steamRoot": str(steam.absolute()),
                    "commandFingerprint": fingerprint,
                    "predecessorRunIds": [],
                    "retryOf": None,
                },
            )
            for scenario in ContainmentScenario:
                observed = replace(watch_outcome(scenario), run_id=run_id)
                result = evaluate_scenario(
                    replace(evidence(scenario), run_id=run_id, watch_outcome=observed)
                )
                store.write_watch_outcome(observed)
                store.write_result(result)
            store.result_path(run_id, ContainmentScenario.REPLACE_EXISTING).write_bytes(b"damaged\n")
            decision = service.adjudicate_run(store.root, run_id)
            self.assertEqual(CapabilityVerdict.INCOMPLETE, decision.verdict)
            self.assertTrue(any("ReplaceExisting" in reason for reason in decision.reasons))

    def test_adjudicate_run_is_not_poisoned_by_later_same_command_failure(self):
        with tempfile.TemporaryDirectory(prefix="modlab-history-order-") as directory:
            store = ContainmentStore(Path(directory))
            current_run = "containment-run:" + "4" * 32
            later_run = "containment-run:" + "5" * 32
            source = store.root / "source"
            steam = store.root / "steam"
            artifact = "artifact:ordered-history"
            fingerprint = service._command_fingerprint(source, artifact, steam)

            for run_id, failed in ((current_run, False), (later_run, True)):
                store.write_request(
                    run_id,
                    {
                        "runId": run_id,
                        "sourceWorkspace": str(source.absolute()),
                        "mo2ArtifactId": artifact,
                        "steamRoot": str(steam.absolute()),
                        "commandFingerprint": fingerprint,
                        "predecessorRunIds": [],
                        "retryOf": None,
                    },
                )
                for scenario in ContainmentScenario:
                    observed = replace(
                        watch_outcome(
                            scenario,
                            events=(event(),)
                            if failed
                            and scenario is ContainmentScenario.MERGE_EXISTING
                            else (),
                        ),
                        run_id=run_id,
                    )
                    result = evaluate_scenario(
                        replace(
                            evidence(scenario),
                            run_id=run_id,
                            watch_outcome=observed,
                        )
                    )
                    store.write_watch_outcome(observed)
                    store.write_result(result)

            decision = service.adjudicate_run(store.root, current_run)
            self.assertEqual(CapabilityVerdict.SUPPORTED, decision.verdict)
            self.assertFalse(any(later_run in reason for reason in decision.reasons))

    def test_adjudicate_run_preserves_failed_precedence_over_damaged_evidence(self):
        with tempfile.TemporaryDirectory(prefix="modlab-failed-damaged-") as directory:
            store = ContainmentStore(Path(directory))
            run_id = "containment-run:" + "5" * 32
            source = store.root / "source"
            steam = store.root / "steam"
            artifact = "artifact:failed-damaged"
            store.write_request(
                run_id,
                {
                    "runId": run_id,
                    "sourceWorkspace": str(source.absolute()),
                    "mo2ArtifactId": artifact,
                    "steamRoot": str(steam.absolute()),
                    "commandFingerprint": service._command_fingerprint(
                        source, artifact, steam
                    ),
                    "predecessorRunIds": [],
                    "retryOf": None,
                },
            )
            for scenario in ContainmentScenario:
                observed = replace(
                    watch_outcome(
                        scenario,
                        events=(event(),)
                        if scenario is ContainmentScenario.MERGE_EXISTING
                        else (),
                    ),
                    run_id=run_id,
                )
                result = evaluate_scenario(
                    replace(evidence(scenario), run_id=run_id, watch_outcome=observed)
                )
                store.write_watch_outcome(observed)
                store.write_result(result)
            store.result_path(
                run_id, ContainmentScenario.REPLACE_EXISTING
            ).write_bytes(b"damaged\n")
            self.assertEqual(
                CapabilityVerdict.REJECTED,
                service.adjudicate_run(store.root, run_id).verdict,
            )
    def test_adoption_failures_and_exact_outputs_fail_closed(self):
        scenario = ContainmentScenario.FOMOD_DEPENDENCY
        cases = (
            ("FOMOD marker absent", dict(staging_output_names=("always.txt",)), ScenarioOutcome.FAILED),
            ("adoption collision", dict(adopted_name=None, adopted_tree=None, adopted_integrity=None, safety_reasons=("adoption-collision",)), ScenarioOutcome.INCOMPLETE),
            ("more than one new folder", dict(staging_new_names=("ModLab Spike FOMOD", "Other")), ScenarioOutcome.FAILED),
            ("failed integrity normalization", dict(adopted_integrity=IntegrityObservation.UNKNOWN), ScenarioOutcome.INCOMPLETE),
        )
        for label, changes, expected in cases:
            with self.subTest(label=label):
                result = evaluate_scenario(evidence(scenario, **changes))
                self.assertEqual(expected, result.outcome)
                self.assertTrue(result.reasons)

    def test_protected_delta_plus_damaged_terminal_is_failed_but_malformed_only_is_incomplete(self):
        scenario = ContainmentScenario.MERGE_EXISTING
        damaged = watch_outcome(
            scenario,
            completion=WatchEvidenceCompletion.INCOMPLETE,
            reason="terminal-damaged",
        )
        breached = evaluate_scenario(
            evidence(scenario, protected_after=protected("b"), watch_outcome=damaged)
        )
        malformed = evaluate_scenario(
            evidence(scenario, watch_outcome=replace(damaged, reason_codes=("malformed-event",)))
        )
        self.assertEqual(ScenarioOutcome.FAILED, breached.outcome)
        self.assertFalse(breached.fresh_retry_eligible)
        self.assertEqual(ScenarioOutcome.INCOMPLETE, malformed.outcome)
        self.assertFalse(malformed.fresh_retry_eligible)

    def test_failed_result_cannot_be_hidden_by_later_passed_result(self):
        scenario = ContainmentScenario.NEW_FOLDER
        failed_outcome = watch_outcome(scenario, events=(event("PlayProfile"),))
        failed = evaluate_scenario(evidence(scenario, watch_outcome=failed_outcome))
        passed_outcome = watch_outcome(scenario)
        passed = evaluate_scenario(evidence(scenario, watch_outcome=passed_outcome))
        other_pairs = tuple(
            (evaluate_scenario(evidence(item)), watch_outcome(item))
            for item in ContainmentScenario
            if item is not scenario
        )
        decision = adjudicate_results(
            (failed, passed, *(item[0] for item in other_pairs)),
            (failed_outcome, passed_outcome, *(item[1] for item in other_pairs)),
        )
        self.assertEqual(CapabilityVerdict.REJECTED, decision.verdict)
        self.assertIn("containment-breach", decision.reasons)


class ContainmentRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="modlab-containment-recovery-")
        self.root = Path(self.temporary.name)
        self.store = ContainmentStore(self.root)
        scenario_root = self.root / "fixture"
        scenario_root.mkdir()
        self.journal = ScenarioJournal(
            1,
            RUN_ID,
            ContainmentScenario.MERGE_EXISTING,
            ScenarioState.ARMED,
            str(scenario_root / "source"),
            str(scenario_root / "stage"),
            str(scenario_root / "archive.zip"),
            "Protected Existing",
            "ModLab Spike New",
            protected(),
            17,
            None,
            None,
        )
        self.store.create(self.journal)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_scenario_loading_rejects_nonexact_request_schema(self):
        self.store.write_request(
            RUN_ID,
            {"runId": RUN_ID, "scenarios": [], "unexpected": True},
        )
        with self.assertRaisesRegex(
            service.ContainmentServiceError,
            "request fields are not exact",
        ):
            service._load_fixture_record(
                self.store,
                RUN_ID,
                ContainmentScenario.MERGE_EXISTING,
            )

    def test_pre_scenario_interruption_may_retry_once_after_cleanup(self):
        outcome = watch_outcome(
            self.journal.scenario,
            completion=WatchEvidenceCompletion.INCOMPLETE,
        )
        self.store.write_watch_outcome(outcome)
        source = self.root / "command-source"
        steam = self.root / "command-steam"
        artifact = "artifact:recovery"
        fingerprint = service._command_fingerprint(source, artifact, steam)
        self.store.write_request(
            RUN_ID,
            {
                "runId": RUN_ID,
                "sourceWorkspace": str(source.absolute()),
                "mo2ArtifactId": artifact,
                "steamRoot": str(steam.absolute()),
                "commandFingerprint": fingerprint,
                "predecessorRunIds": [],
            },
        )
        cleanup = RecoveryCleanupEvidence(
            watch_outcome=outcome,
            protected_after=protected(),
            source_integrity=IntegrityObservation.MEDIUM,
            stage_integrity=IntegrityObservation.LOW,
            projection_count=1,
            projection_targets_verified=True,
            projection_payload_bytes_copied=0,
            blockers=(),
        )
        with patch(
            "modlab.validation.mo2_containment_service._perform_recovery_cleanup",
            return_value=cleanup,
        ), patch(
            "modlab.validation.mo2_containment_service._prove_recovery",
            return_value=RecoveryProofEvidence(outcome, protected(), ()),
        ), patch(
            "modlab.validation.mo2_containment_service._load_fixture_record",
            return_value=object(),
        ):
            recovery = recover_scenario(self.root, RUN_ID, self.journal.scenario)
        self.assertEqual(ScenarioCleanupStatus.SUCCEEDED, recovery.cleanup_status)
        self.assertTrue(recovery.fresh_run_permitted)
        self.assertIsNotNone(recovery.result_id)
        authority = self.store.load_retry_authority(RUN_ID, self.journal.scenario)
        self.assertEqual("Available", authority["state"])
        self.assertEqual(
            fingerprint,
            authority["commandFingerprint"],
        )

    def test_cleanup_refusal_never_starts_fresh_run(self):
        cleanup = RecoveryCleanupEvidence(
            watch_outcome=None,
            protected_after=protected(),
            source_integrity=IntegrityObservation.MEDIUM,
            stage_integrity=IntegrityObservation.UNKNOWN,
            projection_count=0,
            projection_targets_verified=False,
            projection_payload_bytes_copied=0,
            blockers=("worker-identity-uncertain",),
        )
        with (
            patch(
                "modlab.validation.mo2_containment_service._perform_recovery_cleanup",
                return_value=cleanup,
            ),
            patch(
                "modlab.validation.mo2_containment_service._prove_recovery",
                return_value=RecoveryProofEvidence(None, protected(), ()),
            ),
            patch(
                "modlab.validation.mo2_containment_service._load_fixture_record",
                return_value=object(),
            ),
            patch("modlab.validation.mo2_containment_service.launch_low_integrity_process") as launch,
        ):
            recovery = recover_scenario(self.root, RUN_ID, self.journal.scenario)
        self.assertEqual(ScenarioCleanupStatus.REFUSED, recovery.cleanup_status)
        self.assertFalse(recovery.fresh_run_permitted)
        launch.assert_not_called()

    def test_read_only_recovery_refusal_performs_zero_cleanup_mutation(self):
        proof = RecoveryProofEvidence(
            watch_outcome=None,
            protected_after=protected(),
            blockers=("prior-watcher-still-live",),
        )
        quarantine_before = tuple(self.store.quarantine_path(RUN_ID).iterdir())
        with (
            patch.object(service, "_load_fixture_record", return_value=object()),
            patch.object(service, "_prove_recovery", return_value=proof),
            patch.object(
                service,
                "_perform_recovery_cleanup",
                side_effect=AssertionError("cleanup mutation must not begin"),
            ) as cleanup,
        ):
            recovery = recover_scenario(self.root, RUN_ID, self.journal.scenario)
        self.assertEqual(ScenarioCleanupStatus.REFUSED, recovery.cleanup_status)
        cleanup.assert_not_called()
        self.assertEqual(quarantine_before, tuple(self.store.quarantine_path(RUN_ID).iterdir()))

    def test_refused_cleanup_retains_trusted_positive_breach_as_failed_result(self):
        started = self.store.transition(self.journal, ScenarioState.SCENARIO_STARTED)
        outcome = watch_outcome(started.scenario, events=(event(),))
        self.store.write_watch_outcome(outcome)
        proof = RecoveryProofEvidence(
            watch_outcome=outcome,
            protected_after=protected(),
            blockers=("prior-mo2-process-still-live",),
        )
        with (
            patch.object(service, "_load_fixture_record", return_value=object()),
            patch.object(service, "_prove_recovery", return_value=proof),
            patch.object(service, "_perform_recovery_cleanup") as cleanup,
        ):
            recovery = recover_scenario(self.root, RUN_ID, started.scenario)
        cleanup.assert_not_called()
        self.assertEqual(ScenarioCleanupStatus.REFUSED, recovery.cleanup_status)
        self.assertIsNotNone(recovery.result_id)
        self.assertEqual(
            ScenarioOutcome.FAILED,
            self.store.load_result(RUN_ID, started.scenario).outcome,
        )

    def test_cleanup_after_result_publication_reuses_immutable_result(self):
        outcome = watch_outcome(
            self.journal.scenario,
            completion=WatchEvidenceCompletion.INCOMPLETE,
            reason="terminal-damaged",
        )
        self.store.write_watch_outcome(outcome)
        launched = self.store.transition(
            self.store.transition(
                self.journal,
                ScenarioState.SCENARIO_STARTED,
            ),
            ScenarioState.LAUNCHED,
            mo2_pid=51,
        )
        damaged = evaluate_scenario(
            evidence(
                self.journal.scenario,
                protected_after=protected("b"),
                watch_outcome=outcome,
            )
        )
        written = self.store.write_result(damaged)
        cleanup = RecoveryCleanupEvidence(
            watch_outcome=outcome,
            protected_after=protected("b"),
            source_integrity=IntegrityObservation.MEDIUM,
            stage_integrity=IntegrityObservation.LOW,
            projection_count=1,
            projection_targets_verified=True,
            projection_payload_bytes_copied=0,
            blockers=(),
        )
        with (
            patch(
                "modlab.validation.mo2_containment_service._perform_recovery_cleanup",
                return_value=cleanup,
            ),
            patch(
                "modlab.validation.mo2_containment_service._prove_recovery",
                return_value=RecoveryProofEvidence(outcome, protected("b"), ()),
            ),
            patch(
                "modlab.validation.mo2_containment_service._load_fixture_record",
                return_value=object(),
            ),
        ):
            recovery = recover_scenario(self.root, RUN_ID, launched.scenario)
        self.assertEqual(ScenarioCleanupStatus.SUCCEEDED, recovery.cleanup_status)
        self.assertFalse(recovery.fresh_run_permitted)
        self.assertEqual(written.content_id, recovery.result_id)
        self.assertEqual(damaged, self.store.load_result(RUN_ID, launched.scenario))


if __name__ == "__main__":
    unittest.main()
