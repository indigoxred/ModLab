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
    ScenarioOutcome,
    ScenarioJournal,
    ScenarioState,
    ScenarioResult,
    TreeIdentity,
    WatcherEvent,
)
from modlab.validation.mo2_containment_serialization import (
    ContainmentFormatError,
    capability_decision_from_bytes,
    capability_decision_id_for,
    capability_decision_to_bytes,
    scenario_journal_from_bytes,
    scenario_journal_to_bytes,
    scenario_result_from_bytes,
    scenario_result_id_for,
    scenario_result_to_bytes,
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


def valid_scenario_result(scenario: ContainmentScenario) -> ScenarioResult:
    before = protected("a")
    common = dict(
        schema_version=1,
        run_id="containment-run:0123456789abcdef0123456789abcdef",
        scenario=scenario,
        outcome=ScenarioOutcome.PASSED,
        protected_before=before,
        protected_after=before,
        mo2_process=ProcessEvidence(
            pid=42,
            executable=r"C:\Lab\MO2\ModOrganizer.exe",
            executable_version="2.5.2.0",
            arguments=("--instance", "Lab"),
            working_directory=r"C:\Lab\MO2",
            integrity=IntegrityObservation.LOW,
        ),
        source_integrity=IntegrityObservation.MEDIUM,
        stage_integrity=IntegrityObservation.LOW,
        watcher_complete=True,
        watcher_events=(),
        projection_count=2,
        projection_targets_verified=True,
        projection_payload_bytes_copied=0,
        production_backup_names=(),
        source_restored_after_quarantine=True,
        reasons=(),
    )
    if scenario is ContainmentScenario.NEW_FOLDER:
        return ScenarioResult(
            **common,
            staging_new_names=("ModLab Spike New",),
            staging_output_names=("meshes/new-folder.bin",),
            adopted_name="ModLab Spike New",
            adopted_tree=tree("b"),
            adopted_integrity=IntegrityObservation.MEDIUM,
        )
    if scenario is ContainmentScenario.FOMOD_DEPENDENCY:
        return ScenarioResult(
            **common,
            staging_new_names=("ModLab Spike FOMOD",),
            staging_output_names=("always.txt", "dependency-seen.txt"),
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


def invalid_containment_documents():
    value = valid_scenario_result(ContainmentScenario.MERGE_EXISTING)
    document = json.loads(scenario_result_to_bytes(value))
    valid = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    duplicate = valid.replace(b'"schemaVersion":1,', b'"schemaVersion":1,"schemaVersion":1,')

    extra = dict(document, unexpected=True)
    absolute_path = json.loads(valid)
    absolute_path["watcherEvents"] = [{
        "sequence": 1,
        "rootKind": "stage",
        "action": "Created",
        "relativePath": r"C:\\unsafe.txt",
    }]
    mixed_hash = json.loads(valid)
    mixed_hash["protectedBefore"]["sourceMods"]["sha256"] = "A" * 64
    duplicate_sequence = json.loads(valid)
    duplicate_sequence["watcherEvents"] = [
        {"sequence": 1, "rootKind": "stage", "action": "Created", "relativePath": "one.txt"},
        {"sequence": 1, "rootKind": "stage", "action": "Deleted", "relativePath": "two.txt"},
    ]
    passed_mutation = json.loads(valid)
    passed_mutation["watcherEvents"] = [
        {"sequence": 1, "rootKind": "sourceMods", "action": "Changed", "relativePath": "mod.txt"}
    ]
    changed_hash = json.loads(valid)
    changed_hash["protectedAfter"]["downloads"]["sha256"] = "b" * 64
    missing_after = json.loads(valid)
    del missing_after["protectedAfter"]
    malformed = [
        (duplicate, "duplicate JSON key"),
        (json.dumps(extra, sort_keys=True, separators=(",", ":")).encode(), "fields differ"),
        (json.dumps(absolute_path, sort_keys=True, separators=(",", ":")).encode(), "relativePath"),
        (json.dumps(mixed_hash, sort_keys=True, separators=(",", ":")).encode(), "lowercase SHA-256"),
        (json.dumps(duplicate_sequence, sort_keys=True, separators=(",", ":")).encode(), "duplicate watcher sequence"),
        (json.dumps(passed_mutation, sort_keys=True, separators=(",", ":")).encode(), "Passed result cannot record watcher events"),
        (json.dumps(changed_hash, sort_keys=True, separators=(",", ":")).encode(), "protectedAfter"),
        (json.dumps(missing_after, sort_keys=True, separators=(",", ":")).encode(), "protectedAfter"),
    ]

    all_results = tuple(valid_scenario_result(scenario) for scenario in ContainmentScenario)
    identifiers = tuple(scenario_result_id_for(item) for item in all_results)
    supported = CapabilityDecision(
        schema_version=1,
        run_id=value.run_id,
        mechanism="isolated-low-integrity-junction-projection-v1",
        verdict=CapabilityVerdict.SUPPORTED,
        scenario_result_ids=identifiers,
        reasons=(),
    )
    incomplete_document = json.loads(capability_decision_to_bytes(supported, all_results))
    incomplete_document["scenarioResultIds"] = list(identifiers[:-1])
    wrong_document = json.loads(capability_decision_to_bytes(supported, all_results))
    wrong_document["runId"] = "containment-run:fedcba9876543210fedcba9876543210Z"
    malformed.extend((
        (json.dumps(incomplete_document, sort_keys=True, separators=(",", ":")).encode(), "Supported decision requires four passing scenarios"),
        (json.dumps(wrong_document, sort_keys=True, separators=(",", ":")).encode(), "runId"),
    ))
    return malformed


class Mo2ContainmentSerializationTests(unittest.TestCase):
    def test_scenario_result_round_trips_canonically(self):
        value = valid_scenario_result(ContainmentScenario.MERGE_EXISTING)
        encoded = scenario_result_to_bytes(value)
        self.assertEqual(value, scenario_result_from_bytes(encoded))
        self.assertEqual(encoded, scenario_result_to_bytes(value))
        self.assertEqual(
            f"containment-result-sha256:{hashlib.sha256(encoded).hexdigest()}",
            scenario_result_id_for(value),
        )

    def test_unknown_duplicate_unsafe_and_impossible_values_are_rejected(self):
        for data, message in invalid_containment_documents():
            with self.subTest(message=message):
                loader = (
                    capability_decision_from_bytes
                    if b'"verdict"' in data
                    else scenario_result_from_bytes
                )
                with self.assertRaisesRegex(ContainmentFormatError, message):
                    loader(data)

    def test_supported_decision_round_trips_with_ids_in_scenario_order(self):
        results = tuple(valid_scenario_result(scenario) for scenario in ContainmentScenario)
        decision = CapabilityDecision(
            schema_version=1,
            run_id=results[0].run_id,
            mechanism="isolated-low-integrity-junction-projection-v1",
            verdict=CapabilityVerdict.SUPPORTED,
            scenario_result_ids=tuple(scenario_result_id_for(item) for item in results),
            reasons=(),
        )

        encoded = capability_decision_to_bytes(decision, results)
        self.assertEqual(decision, capability_decision_from_bytes(encoded, results))
        self.assertEqual(
            f"containment-decision-sha256:{hashlib.sha256(encoded).hexdigest()}",
            capability_decision_id_for(decision, results),
        )

    def test_supported_decision_rejects_cross_record_mismatch(self):
        results = tuple(valid_scenario_result(scenario) for scenario in ContainmentScenario)
        decision = CapabilityDecision(
            schema_version=1,
            run_id=results[0].run_id,
            mechanism="isolated-low-integrity-junction-projection-v1",
            verdict=CapabilityVerdict.SUPPORTED,
            scenario_result_ids=tuple(scenario_result_id_for(item) for item in results),
            reasons=(),
        )

        for invalid in (
            results[::-1],
            results[:-1] + (replace(results[-1], outcome=ScenarioOutcome.FAILED, reasons=("failed",)),),
            (replace(results[0], run_id="containment-run:fedcba9876543210fedcba9876543210"),) + results[1:],
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ContainmentFormatError):
                    capability_decision_to_bytes(decision, invalid)

    def test_failed_or_unknown_evidence_is_not_allowed_to_be_supported(self):
        value = valid_scenario_result(ContainmentScenario.MERGE_EXISTING)
        invalid = (
            replace(value, outcome=ScenarioOutcome.FAILED, watcher_complete=False, reasons=("failure",)),
            replace(value, outcome=ScenarioOutcome.FAILED, mo2_process=None, reasons=("failure",)),
            replace(value, outcome=ScenarioOutcome.FAILED, source_integrity=IntegrityObservation.UNKNOWN, reasons=("failure",)),
            replace(value, outcome=ScenarioOutcome.FAILED, reasons=()),
        )
        for result in invalid:
            with self.subTest(result=result):
                with self.assertRaises(ContainmentFormatError):
                    scenario_result_to_bytes(result)

    def test_failed_adoption_with_unknown_integrity_is_incomplete(self):
        result = replace(
            valid_scenario_result(ContainmentScenario.NEW_FOLDER),
            outcome=ScenarioOutcome.FAILED,
            adopted_integrity=IntegrityObservation.UNKNOWN,
            reasons=("adopted integrity was unavailable",),
        )

        with self.assertRaises(ContainmentFormatError):
            scenario_result_to_bytes(result)

    def test_journal_round_trips_and_rejects_captured_without_mo2_pid(self):
        value = ScenarioJournal(
            schema_version=1,
            run_id="containment-run:0123456789abcdef0123456789abcdef",
            scenario=ContainmentScenario.MERGE_EXISTING,
            state=ScenarioState.CAPTURED,
            source_root=r"C:\Lab\source",
            stage_root=r"C:\Lab\stage",
            archive_path=r"C:\Lab\archive.zip",
            protected_mod_name="Protected Mod",
            expected_new_mod_name="Expected Mod",
            protected_before=protected("a"),
            monitor_pid=11,
            mo2_pid=12,
            error=None,
        )

        self.assertEqual(value, scenario_journal_from_bytes(scenario_journal_to_bytes(value)))
        with self.assertRaises(ContainmentFormatError):
            scenario_journal_to_bytes(replace(value, mo2_pid=None))

    def test_passing_special_scenarios_require_exact_adopted_outputs(self):
        for scenario in (
            ContainmentScenario.NEW_FOLDER,
            ContainmentScenario.FOMOD_DEPENDENCY,
        ):
            with self.subTest(scenario=scenario):
                result = valid_scenario_result(scenario)
                self.assertEqual(result, scenario_result_from_bytes(scenario_result_to_bytes(result)))
                bad = replace(result, staging_output_names=("wrong.txt",))
                with self.assertRaises(ContainmentFormatError):
                    scenario_result_to_bytes(bad)


if __name__ == "__main__":
    unittest.main()
