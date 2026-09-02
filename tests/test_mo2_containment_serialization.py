import hashlib
import json
import unittest
from dataclasses import replace

from modlab.validation.mo2_containment_model import (
    CapabilityDecision,
    CapabilityVerdict,
    ContainmentScenario,
    IntegrityObservation,
    ProcessEvidence,
    ProtectedState,
    ScenarioCleanupStatus,
    ScenarioJournal,
    ScenarioOutcome,
    ScenarioRecovery,
    ScenarioResult,
    ScenarioState,
    TreeIdentity,
    WatchEvidenceCompletion,
    WatchOutcome,
    WatcherEvent,
)
from modlab.validation.mo2_containment_serialization import (
    ContainmentFormatError,
    capability_decision_from_bytes,
    capability_decision_id_for,
    capability_decision_to_bytes,
    scenario_journal_from_bytes,
    scenario_journal_to_bytes,
    scenario_recovery_from_bytes,
    scenario_recovery_to_bytes,
    scenario_result_from_bytes,
    scenario_result_id_for,
    scenario_result_to_bytes,
    watch_outcome_from_bytes,
    watch_outcome_id_for,
    watch_outcome_to_bytes,
)


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


def tree(letter: str) -> TreeIdentity:
    return TreeIdentity(letter * 64, 1, 1, 7)


def protected(letter: str) -> ProtectedState:
    return ProtectedState(
        source_mods=tree(letter),
        lab_profile_sha256=letter * 64,
        play_profile_sha256=letter * 64,
        downloads=tree(letter),
        overwrite=tree(letter),
        bounded_game=tree(letter),
    )


def forbidden_play_event() -> WatcherEvent:
    return WatcherEvent(1, "PlayProfile", "Modified", "modlist.txt")


def _event_bytes(events: tuple[WatcherEvent, ...]) -> bytes:
    return b"".join(
        (
            json.dumps(
                {
                    "action": event.action,
                    "relativePath": event.relative_path,
                    "rootKind": event.root_kind,
                    "sequence": event.sequence,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        for event in events
    )


def valid_watch_outcome(
    *,
    scenario: ContainmentScenario = ContainmentScenario.MERGE_EXISTING,
    events: tuple[WatcherEvent, ...] = (),
) -> WatchOutcome:
    event_bytes = _event_bytes(events)
    return WatchOutcome(
        schema_version=1,
        request_id="watch-request:" + "1" * 64,
        request_sha256="2" * 64,
        session_id="watch-session:" + "3" * 64,
        run_id="containment-run:0123456789abcdef0123456789abcdef",
        scenario=scenario,
        controller_pid=41,
        controller_creation_time=1001,
        worker_pid=42,
        worker_creation_time=1002,
        evidence_completion=WatchEvidenceCompletion.COMPLETED,
        worker_exit_code=0,
        ready=True,
        opened_root_kinds=WATCH_ROOT_KINDS,
        events=events,
        event_bytes_sha256=hashlib.sha256(event_bytes).hexdigest(),
        journal_volume_serial=7,
        journal_file_id=8,
        journal_byte_count=len(event_bytes),
        journal_event_count=len(events),
        journal_final_sequence=events[-1].sequence if events else 0,
        terminal_bytes_sha256="4" * 64,
        root_identities_unchanged=True,
        reason_codes=(),
    )


def incomplete_watch_outcome(
    *,
    scenario: ContainmentScenario = ContainmentScenario.MERGE_EXISTING,
    events: tuple[WatcherEvent, ...] = (),
) -> WatchOutcome:
    return replace(
        valid_watch_outcome(scenario=scenario, events=events),
        evidence_completion=WatchEvidenceCompletion.INCOMPLETE,
        worker_exit_code=None,
        reason_codes=("controller-session-lost",),
    )


def valid_scenario_result(
    scenario: ContainmentScenario,
    watch_outcome: WatchOutcome,
) -> ScenarioResult:
    before = protected("a")
    common = dict(
        schema_version=1,
        run_id=watch_outcome.run_id,
        scenario=scenario,
        outcome=ScenarioOutcome.PASSED,
        protected_before=before,
        protected_after=before,
        mo2_process=ProcessEvidence(
            pid=42,
            executable=r"C:\Lab\MO2\ModOrganizer.exe",
            executable_version="2.5.2.0",
            arguments=("--profile", "ModLab - Lab"),
            working_directory=r"C:\Lab\MO2",
            integrity=IntegrityObservation.LOW,
        ),
        source_integrity=IntegrityObservation.MEDIUM,
        stage_integrity=IntegrityObservation.LOW,
        watch_outcome_id=watch_outcome_id_for(watch_outcome),
        watch_evidence_completion=watch_outcome.evidence_completion,
        scenario_started=True,
        fresh_retry_eligible=False,
        watcher_events=watch_outcome.events,
        projection_count=1,
        projection_targets_verified=True,
        projection_observation_complete=True,
        projection_payload_bytes_copied=0,
        production_backup_names=(),
        production_observation_complete=True,
        staging_observation_complete=True,
        output_observation_complete=True,
        source_restored_after_quarantine=True,
        reasons=(),
    )
    if scenario is ContainmentScenario.NEW_FOLDER:
        return ScenarioResult(
            **common,
            staging_new_names=("ModLab Spike New",),
            staging_output_names=("meshes/new-folder.bin", "meta.ini"),
            adopted_name="ModLab Spike New",
            adopted_tree=tree("b"),
            adopted_integrity=IntegrityObservation.MEDIUM,
        )
    if scenario is ContainmentScenario.FOMOD_DEPENDENCY:
        return ScenarioResult(
            **common,
            staging_new_names=("ModLab Spike FOMOD",),
            staging_output_names=("always.txt", "dependency-seen.txt", "meta.ini"),
            adopted_name="ModLab Spike FOMOD",
            adopted_tree=tree("b"),
            adopted_integrity=IntegrityObservation.MEDIUM,
        )
    return ScenarioResult(
        **common,
        staging_new_names=(),
        staging_output_names=(),
        adopted_name=None,
        adopted_tree=None,
        adopted_integrity=None,
    )


def failed_result_for(
    outcome: WatchOutcome,
    *,
    reasons: tuple[str, ...],
) -> ScenarioResult:
    completed = valid_watch_outcome(
        scenario=outcome.scenario,
        events=outcome.events,
    )
    return replace(
        valid_scenario_result(outcome.scenario, completed),
        outcome=ScenarioOutcome.FAILED,
        watch_outcome_id=watch_outcome_id_for(outcome),
        watch_evidence_completion=outcome.evidence_completion,
        watcher_events=outcome.events,
        scenario_started=True,
        fresh_retry_eligible=False,
        reasons=reasons,
    )


def incomplete_result_for(outcome: WatchOutcome) -> ScenarioResult:
    completed = valid_watch_outcome(scenario=outcome.scenario)
    return replace(
        valid_scenario_result(outcome.scenario, completed),
        outcome=ScenarioOutcome.INCOMPLETE,
        watch_outcome_id=watch_outcome_id_for(outcome),
        watch_evidence_completion=outcome.evidence_completion,
        watcher_events=outcome.events,
        scenario_started=True,
        fresh_retry_eligible=False,
        reasons=("watch-evidence-incomplete",),
    )


def all_passing_evidence():
    outcomes = tuple(
        valid_watch_outcome(scenario=scenario)
        for scenario in ContainmentScenario
    )
    results = tuple(
        valid_scenario_result(scenario, outcome)
        for scenario, outcome in zip(ContainmentScenario, outcomes, strict=True)
    )
    return results, outcomes


class Mo2ContainmentSerializationTests(unittest.TestCase):
    def test_watch_outcome_round_trips_canonically(self):
        value = valid_watch_outcome(events=(forbidden_play_event(),))
        encoded = watch_outcome_to_bytes(value)

        self.assertEqual(value, watch_outcome_from_bytes(encoded))
        self.assertEqual(encoded, watch_outcome_to_bytes(value))
        self.assertEqual(
            "watch-outcome-sha256:" + hashlib.sha256(encoded).hexdigest(),
            watch_outcome_id_for(value),
        )

    def test_scenario_result_round_trips_only_with_bound_watch_outcome(self):
        outcome = valid_watch_outcome()
        value = valid_scenario_result(outcome.scenario, outcome)
        encoded = scenario_result_to_bytes(value, outcome)

        self.assertEqual(value, scenario_result_from_bytes(encoded, outcome))
        self.assertEqual(encoded, scenario_result_to_bytes(value, outcome))
        self.assertEqual(
            "containment-result-sha256:" + hashlib.sha256(encoded).hexdigest(),
            scenario_result_id_for(value, outcome),
        )

        replay = replace(outcome, session_id="watch-session:" + "9" * 64)
        with self.assertRaisesRegex(ContainmentFormatError, "watch outcome"):
            scenario_result_from_bytes(encoded, replay)

    def test_scenario_recovery_round_trips_and_enforces_cleanup_authority(self):
        value = ScenarioRecovery(
            schema_version=1,
            run_id="containment-run:0123456789abcdef0123456789abcdef",
            scenario=ContainmentScenario.MERGE_EXISTING,
            journal_id="containment-journal-sha256:" + "5" * 64,
            result_id="containment-result-sha256:" + "6" * 64,
            cleanup_status=ScenarioCleanupStatus.SUCCEEDED,
            fresh_run_permitted=True,
            blockers=(),
        )
        self.assertEqual(
            value,
            scenario_recovery_from_bytes(scenario_recovery_to_bytes(value)),
        )

        with self.assertRaises(ContainmentFormatError):
            scenario_recovery_to_bytes(replace(value, result_id=None))
        with self.assertRaises(ContainmentFormatError):
            scenario_recovery_to_bytes(
                replace(
                    value,
                    cleanup_status=ScenarioCleanupStatus.REFUSED,
                    blockers=("worker-identity-uncertain",),
                )
            )
        refused = replace(
            value,
            cleanup_status=ScenarioCleanupStatus.REFUSED,
            fresh_run_permitted=False,
            blockers=("worker-identity-uncertain",),
        )
        self.assertEqual(
            refused,
            scenario_recovery_from_bytes(scenario_recovery_to_bytes(refused)),
        )

    def test_journal_scenario_started_requires_monitor_and_precedes_mo2_pid(self):
        value = ScenarioJournal(
            schema_version=1,
            run_id="containment-run:0123456789abcdef0123456789abcdef",
            scenario=ContainmentScenario.MERGE_EXISTING,
            state=ScenarioState.SCENARIO_STARTED,
            source_root=r"C:\Lab\source",
            stage_root=r"C:\Lab\stage",
            archive_path=r"C:\Lab\archive.zip",
            protected_mod_name="Protected Mod",
            expected_new_mod_name="Expected Mod",
            protected_before=protected("a"),
            monitor_pid=11,
            mo2_pid=None,
            error=None,
        )
        self.assertEqual(value, scenario_journal_from_bytes(scenario_journal_to_bytes(value)))
        with self.assertRaises(ContainmentFormatError):
            scenario_journal_to_bytes(replace(value, monitor_pid=None))
        with self.assertRaises(ContainmentFormatError):
            scenario_journal_to_bytes(replace(value, mo2_pid=12))

    def test_incomplete_watch_with_bound_positive_event_may_be_failed(self):
        outcome = incomplete_watch_outcome(events=(forbidden_play_event(),))
        result = failed_result_for(
            outcome,
            reasons=("forbidden-play-profile-mutation",),
        )
        encoded = scenario_result_to_bytes(result, outcome)
        self.assertEqual(result, scenario_result_from_bytes(encoded, outcome))

    def test_incomplete_watch_without_positive_breach_cannot_be_failed(self):
        outcome = incomplete_watch_outcome()
        result = failed_result_for(outcome, reasons=("terminal-missing",))
        with self.assertRaisesRegex(ContainmentFormatError, "positive breach"):
            scenario_result_to_bytes(result, outcome)

    def test_completed_typed_policy_violation_is_positive_failure_proof(self):
        outcome = valid_watch_outcome()
        result = replace(
            failed_result_for(outcome, reasons=("production-backup-created",)),
            production_backup_names=("Protected Existing_backup",),
        )
        encoded = scenario_result_to_bytes(result, outcome)
        self.assertEqual(result, scenario_result_from_bytes(encoded, outcome))

    def test_completed_arbitrary_reason_is_not_positive_failure_proof(self):
        outcome = valid_watch_outcome()
        result = failed_result_for(outcome, reasons=("operator-disliked-result",))
        with self.assertRaisesRegex(ContainmentFormatError, "positive breach"):
            scenario_result_to_bytes(result, outcome)

    def test_legacy_empty_adoption_failure_remains_readable(self):
        # Catches a corrected classifier making an immutable v1 diagnostic unreadable.
        outcome = valid_watch_outcome(scenario=ContainmentScenario.NEW_FOLDER)
        result = replace(
            valid_scenario_result(ContainmentScenario.NEW_FOLDER, outcome),
            outcome=ScenarioOutcome.FAILED,
            staging_new_names=(),
            staging_output_names=(),
            output_observation_complete=False,
            adopted_name=None,
            adopted_tree=None,
            adopted_integrity=None,
            reasons=(
                "adoption-proof-unavailable:ContainmentSafetyError",
                "output-observation-incomplete",
                "staging-new-folder-set-invalid",
            ),
        )

        self.assertEqual(
            result,
            scenario_result_from_bytes(
                scenario_result_to_bytes(result, outcome), outcome
            ),
        )

    def test_v2_metadata_false_failure_remains_readable(self):
        # Catches the v3 metadata correction hiding its immutable v2 diagnostic.
        outcome = valid_watch_outcome(scenario=ContainmentScenario.NEW_FOLDER)
        result = replace(
            valid_scenario_result(ContainmentScenario.NEW_FOLDER, outcome),
            outcome=ScenarioOutcome.FAILED,
            reasons=("staging-output-set-invalid",),
        )

        self.assertEqual(
            result,
            scenario_result_from_bytes(
                scenario_result_to_bytes(result, outcome), outcome
            ),
        )

    def test_protected_delta_is_positive_failure_proof_without_complete_watch(self):
        outcome = incomplete_watch_outcome()
        result = replace(
            failed_result_for(outcome, reasons=("protected-state-changed",)),
            protected_after=protected("b"),
        )
        self.assertEqual(
            result,
            scenario_result_from_bytes(scenario_result_to_bytes(result, outcome), outcome),
        )

    def test_retry_eligibility_requires_pre_scenario_incomplete(self):
        outcome = incomplete_watch_outcome()
        value = replace(
            valid_scenario_result(
                outcome.scenario,
                valid_watch_outcome(scenario=outcome.scenario),
            ),
            outcome=ScenarioOutcome.INCOMPLETE,
            watch_outcome_id=watch_outcome_id_for(outcome),
            watch_evidence_completion=WatchEvidenceCompletion.INCOMPLETE,
            scenario_started=False,
            fresh_retry_eligible=True,
            reasons=("controller-exited-before-scenario",),
        )
        self.assertEqual(
            value,
            scenario_result_from_bytes(scenario_result_to_bytes(value, outcome), outcome),
        )
        with self.assertRaises(ContainmentFormatError):
            scenario_result_to_bytes(replace(value, scenario_started=True), outcome)
        with self.assertRaises(ContainmentFormatError):
            scenario_result_to_bytes(
                replace(value, watch_evidence_completion=WatchEvidenceCompletion.COMPLETED),
                outcome,
            )

    def test_supported_decision_deeply_binds_each_result_and_watch_outcome(self):
        results, outcomes = all_passing_evidence()
        decision = CapabilityDecision(
            schema_version=1,
            run_id=results[0].run_id,
            mechanism="isolated-low-integrity-junction-projection-v1",
            verdict=CapabilityVerdict.SUPPORTED,
            scenario_result_ids=tuple(
                scenario_result_id_for(result, outcome)
                for result, outcome in zip(results, outcomes, strict=True)
            ),
            reasons=(),
        )

        encoded = capability_decision_to_bytes(decision, results, outcomes)
        self.assertEqual(
            decision,
            capability_decision_from_bytes(encoded, results, outcomes),
        )
        self.assertEqual(
            "containment-decision-sha256:" + hashlib.sha256(encoded).hexdigest(),
            capability_decision_id_for(decision, results, outcomes),
        )

        invalid_pairs = (
            (results[::-1], outcomes),
            (results, outcomes[::-1]),
            (results[:-1], outcomes),
            (results, outcomes[:-1]),
            (
                (replace(results[0], run_id="containment-run:" + "f" * 32),)
                + results[1:],
                outcomes,
            ),
        )
        for invalid_results, invalid_outcomes in invalid_pairs:
            with self.subTest(
                invalid_results=len(invalid_results),
                invalid_outcomes=len(invalid_outcomes),
            ):
                with self.assertRaises(ContainmentFormatError):
                    capability_decision_to_bytes(
                        decision,
                        invalid_results,
                        invalid_outcomes,
                    )

    def test_rejected_and_incomplete_decisions_deep_bind_every_supplied_pair(self):
        failed_outcome = incomplete_watch_outcome(
            scenario=ContainmentScenario.NEW_FOLDER,
            events=(forbidden_play_event(),),
        )
        failed_result = failed_result_for(
            failed_outcome,
            reasons=("forbidden-play-profile-mutation",),
        )
        incomplete_outcome = incomplete_watch_outcome(
            scenario=ContainmentScenario.MERGE_EXISTING,
        )
        incomplete_result = incomplete_result_for(incomplete_outcome)
        results = (failed_result, incomplete_result)
        outcomes = (failed_outcome, incomplete_outcome)
        identifiers = tuple(
            scenario_result_id_for(result, outcome)
            for result, outcome in zip(results, outcomes, strict=True)
        )
        rejected = CapabilityDecision(
            schema_version=1,
            run_id=failed_result.run_id,
            mechanism="isolated-low-integrity-junction-projection-v1",
            verdict=CapabilityVerdict.REJECTED,
            scenario_result_ids=identifiers,
            reasons=("containment-breach",),
        )

        encoded = capability_decision_to_bytes(rejected, results, outcomes)
        self.assertEqual(
            rejected,
            capability_decision_from_bytes(encoded, results, outcomes),
        )

        invalid_evidence = (
            (replace(rejected, scenario_result_ids=identifiers[::-1]), results, outcomes),
            (rejected, results[::-1], outcomes[::-1]),
            (
                rejected,
                results,
                (
                    failed_outcome,
                    replace(
                        incomplete_outcome,
                        run_id="containment-run:" + "f" * 32,
                    ),
                ),
            ),
            (rejected, results[:-1], outcomes),
        )
        for decision, supplied_results, supplied_outcomes in invalid_evidence:
            with self.subTest(
                ids=decision.scenario_result_ids,
                results=tuple(result.scenario for result in supplied_results),
            ):
                with self.assertRaises(ContainmentFormatError):
                    capability_decision_to_bytes(
                        decision,
                        supplied_results,
                        supplied_outcomes,
                    )

        incomplete = replace(
            rejected,
            verdict=CapabilityVerdict.INCOMPLETE,
            scenario_result_ids=(identifiers[1],),
            reasons=("remaining-scenarios-unavailable",),
        )
        self.assertEqual(
            incomplete,
            capability_decision_from_bytes(
                capability_decision_to_bytes(
                    incomplete,
                    (incomplete_result,),
                    (incomplete_outcome,),
                ),
                (incomplete_result,),
                (incomplete_outcome,),
            ),
        )
        with self.assertRaisesRegex(ContainmentFormatError, "Failed.*Rejected"):
            capability_decision_to_bytes(
                replace(
                    incomplete,
                    scenario_result_ids=(identifiers[0],),
                ),
                (failed_result,),
                (failed_outcome,),
            )

    def test_non_supported_decision_evidence_must_be_all_present_or_all_empty(self):
        outcome = incomplete_watch_outcome()
        result = incomplete_result_for(outcome)
        identifier = scenario_result_id_for(result, outcome)
        decision = CapabilityDecision(
            schema_version=1,
            run_id=result.run_id,
            mechanism="isolated-low-integrity-junction-projection-v1",
            verdict=CapabilityVerdict.INCOMPLETE,
            scenario_result_ids=(identifier,),
            reasons=("scenario-unavailable",),
        )

        with self.assertRaises(ContainmentFormatError):
            capability_decision_to_bytes(decision)
        with self.assertRaises(ContainmentFormatError):
            capability_decision_to_bytes(
                replace(decision, scenario_result_ids=()),
                (result,),
                (outcome,),
            )

        for verdict in (CapabilityVerdict.REJECTED, CapabilityVerdict.INCOMPLETE):
            with self.subTest(verdict=verdict):
                early = replace(
                    decision,
                    verdict=verdict,
                    scenario_result_ids=(),
                )
                self.assertEqual(
                    early,
                    capability_decision_from_bytes(
                        capability_decision_to_bytes(early),
                    ),
                )

    def test_watch_outcome_rejects_impossible_or_noncanonical_values(self):
        value = valid_watch_outcome(events=(forbidden_play_event(),))
        encoded = watch_outcome_to_bytes(value)
        duplicate = encoded.replace(
            b'"schemaVersion":1,',
            b'"schemaVersion":1,"schemaVersion":1,',
        )
        extra = json.loads(encoded)
        extra["unexpected"] = True

        invalid_values = (
            replace(value, controller_pid=True),
            replace(value, request_id="watch-request:bad"),
            replace(value, worker_exit_code=7),
            replace(value, opened_root_kinds=WATCH_ROOT_KINDS[:-1]),
            replace(value, event_bytes_sha256="0" * 64),
            replace(value, journal_event_count=0),
            replace(value, journal_final_sequence=0),
            replace(value, root_identities_unchanged=False),
            replace(value, reason_codes=("unexpected",)),
            replace(incomplete_watch_outcome(), reason_codes=()),
        )
        for invalid in invalid_values:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ContainmentFormatError):
                    watch_outcome_to_bytes(invalid)

        for data in (
            duplicate,
            json.dumps(extra, sort_keys=True, separators=(",", ":")).encode(),
        ):
            with self.assertRaises(ContainmentFormatError):
                watch_outcome_from_bytes(data)

    def test_watch_outcome_rejects_event_attributed_to_unknown_root(self):
        event = WatcherEvent(1, "UnknownRoot", "Modified", "modlist.txt")
        event_bytes = _event_bytes((event,))
        value = replace(
            valid_watch_outcome(),
            events=(event,),
            event_bytes_sha256=hashlib.sha256(event_bytes).hexdigest(),
            journal_byte_count=len(event_bytes),
            journal_event_count=1,
            journal_final_sequence=1,
        )

        with self.assertRaisesRegex(ContainmentFormatError, "root kind"):
            watch_outcome_to_bytes(value)

    def test_result_rejects_replay_copied_completion_and_extra_fields(self):
        outcome = valid_watch_outcome()
        result = valid_scenario_result(outcome.scenario, outcome)
        encoded = scenario_result_to_bytes(result, outcome)

        replay = replace(outcome, run_id="containment-run:" + "f" * 32)
        with self.assertRaises(ContainmentFormatError):
            scenario_result_from_bytes(encoded, replay)
        with self.assertRaises(ContainmentFormatError):
            scenario_result_to_bytes(
                replace(
                    result,
                    watch_evidence_completion=WatchEvidenceCompletion.INCOMPLETE,
                ),
                outcome,
            )

        document = json.loads(encoded)
        document["unexpected"] = True
        with self.assertRaisesRegex(ContainmentFormatError, "fields differ"):
            scenario_result_from_bytes(
                json.dumps(document, sort_keys=True, separators=(",", ":")).encode(),
                outcome,
            )

    def test_passed_result_requires_complete_clean_started_evidence(self):
        outcome = valid_watch_outcome()
        value = valid_scenario_result(outcome.scenario, outcome)
        incomplete = incomplete_watch_outcome()

        invalid = (
            (replace(value, scenario_started=False), outcome),
            (replace(value, fresh_retry_eligible=True), outcome),
            (replace(value, protected_after=protected("b")), outcome),
            (replace(value, watcher_events=(forbidden_play_event(),)), outcome),
            (
                replace(
                    value,
                    watch_outcome_id=watch_outcome_id_for(incomplete),
                    watch_evidence_completion=WatchEvidenceCompletion.INCOMPLETE,
                ),
                incomplete,
            ),
        )
        for result, bound in invalid:
            with self.subTest(result=result):
                with self.assertRaises(ContainmentFormatError):
                    scenario_result_to_bytes(result, bound)

    def test_passing_special_scenarios_require_exact_adopted_outputs(self):
        for scenario in (
            ContainmentScenario.NEW_FOLDER,
            ContainmentScenario.FOMOD_DEPENDENCY,
        ):
            with self.subTest(scenario=scenario):
                outcome = valid_watch_outcome(scenario=scenario)
                result = valid_scenario_result(scenario, outcome)
                self.assertEqual(
                    result,
                    scenario_result_from_bytes(
                        scenario_result_to_bytes(result, outcome),
                        outcome,
                    ),
                )
                with self.assertRaises(ContainmentFormatError):
                    scenario_result_to_bytes(
                        replace(result, staging_output_names=("wrong.txt",)),
                        outcome,
                    )


if __name__ == "__main__":
    unittest.main()
