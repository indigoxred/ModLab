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
    ScenarioEvidence,
    adjudicate_results,
    evaluate_scenario,
    recover_scenario,
)
from modlab.validation.mo2_containment_store import ContainmentStore
from modlab.validation import mo2_containment_service as service


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

    def test_policy_failures_are_failed_only_with_positive_bound_breach(self):
        scenario = ContainmentScenario.MERGE_EXISTING
        cases = (
            ("source event", dict(watch_outcome=watch_outcome(scenario, events=(event(),))), ScenarioOutcome.FAILED),
            ("final hash mismatch", dict(protected_after=protected("b")), ScenarioOutcome.FAILED),
            ("Play byte change", dict(protected_after=protected(play="b")), ScenarioOutcome.FAILED),
            ("watcher overflow", dict(watch_outcome=watch_outcome(scenario, completion=WatchEvidenceCompletion.INCOMPLETE, reason="notification-overflow")), ScenarioOutcome.INCOMPLETE),
            ("MO2 still running", dict(incomplete_reasons=("mo2-still-running",)), ScenarioOutcome.INCOMPLETE),
            ("wrong process integrity", dict(mo2_process=process(integrity=IntegrityObservation.MEDIUM)), ScenarioOutcome.INCOMPLETE),
            ("unexpected stage backup", dict(production_backup_names=("Protected Existing_backup",)), ScenarioOutcome.INCOMPLETE),
            ("source junction replaced", dict(projection_targets_verified=False), ScenarioOutcome.INCOMPLETE),
        )
        for label, changes, expected in cases:
            with self.subTest(label=label):
                result = evaluate_scenario(evidence(scenario, **changes))
                self.assertEqual(expected, result.outcome)
                self.assertFalse(result.fresh_retry_eligible)

    def test_adoption_failures_and_exact_outputs_fail_closed(self):
        scenario = ContainmentScenario.FOMOD_DEPENDENCY
        cases = (
            ("FOMOD marker absent", dict(staging_output_names=("always.txt",))),
            ("adoption collision", dict(adopted_name=None, adopted_tree=None, adopted_integrity=None, safety_reasons=("adoption-collision",))),
            ("more than one new folder", dict(staging_new_names=("ModLab Spike FOMOD", "Other"))),
            ("failed integrity normalization", dict(adopted_integrity=IntegrityObservation.UNKNOWN)),
        )
        for label, changes in cases:
            with self.subTest(label=label):
                result = evaluate_scenario(evidence(scenario, **changes))
                self.assertEqual(ScenarioOutcome.INCOMPLETE, result.outcome)
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
            "modlab.validation.mo2_containment_service._load_fixture_record",
            return_value=object(),
        ):
            recovery = recover_scenario(self.root, RUN_ID, self.journal.scenario)
        self.assertEqual(ScenarioCleanupStatus.SUCCEEDED, recovery.cleanup_status)
        self.assertTrue(recovery.fresh_run_permitted)
        self.assertIsNotNone(recovery.result_id)

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
                "modlab.validation.mo2_containment_service._load_fixture_record",
                return_value=object(),
            ),
            patch("modlab.validation.mo2_containment_service.launch_low_integrity_process") as launch,
        ):
            recovery = recover_scenario(self.root, RUN_ID, self.journal.scenario)
        self.assertEqual(ScenarioCleanupStatus.REFUSED, recovery.cleanup_status)
        self.assertFalse(recovery.fresh_run_permitted)
        launch.assert_not_called()

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
