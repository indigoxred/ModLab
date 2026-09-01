import hashlib
import json
import tempfile
import unittest
from unittest import mock
from dataclasses import replace
from pathlib import Path

from modlab.validation.mo2_containment_model import (
    CapabilityDecision,
    CapabilityVerdict,
    ContainmentScenario,
    IntegrityObservation,
    ProcessEvidence,
    ProtectedState,
    ScenarioJournal,
    ScenarioCleanupStatus,
    ScenarioRecovery,
    ScenarioOutcome,
    ScenarioResult,
    ScenarioState,
    TreeIdentity,
    WatchEvidenceCompletion,
    WatchOutcome,
)
from modlab.validation.mo2_containment_serialization import (
    scenario_result_id_for,
    watch_outcome_id_for,
)
from modlab.validation.mo2_containment_store import (
    ContainmentStore,
    ContainmentStoreError,
)
from modlab.validation import mo2_containment_store as containment_store


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


def protected(letter: str = "a") -> ProtectedState:
    value = tree(letter)
    return ProtectedState(value, letter * 64, letter * 64, value, value, value)


def valid_watch_outcome(
    scenario: ContainmentScenario = ContainmentScenario.MERGE_EXISTING,
) -> WatchOutcome:
    return WatchOutcome(
        schema_version=1,
        request_id="watch-request:" + "1" * 64,
        request_sha256="2" * 64,
        session_id="watch-session:" + "3" * 64,
        run_id=RUN_ID,
        scenario=scenario,
        controller_pid=41,
        controller_creation_time=1001,
        worker_pid=42,
        worker_creation_time=1002,
        evidence_completion=WatchEvidenceCompletion.COMPLETED,
        worker_exit_code=0,
        ready=True,
        opened_root_kinds=WATCH_ROOT_KINDS,
        events=(),
        event_bytes_sha256=hashlib.sha256(b"").hexdigest(),
        journal_volume_serial=7,
        journal_file_id=8,
        journal_byte_count=0,
        journal_event_count=0,
        journal_final_sequence=0,
        terminal_bytes_sha256="4" * 64,
        root_identities_unchanged=True,
        reason_codes=(),
    )


def valid_scenario_result(
    scenario: ContainmentScenario = ContainmentScenario.MERGE_EXISTING,
) -> tuple[ScenarioResult, WatchOutcome]:
    outcome = valid_watch_outcome(scenario)
    before = protected()
    common = dict(
        schema_version=1,
        run_id=RUN_ID,
        scenario=scenario,
        outcome=ScenarioOutcome.PASSED,
        protected_before=before,
        protected_after=before,
        mo2_process=ProcessEvidence(
            51,
            str(Path(tempfile.gettempdir()) / "stage" / "ModOrganizer.exe"),
            "2.5.2.0",
            ("--profile", "ModLab - Lab"),
            str(Path(tempfile.gettempdir()) / "stage"),
            IntegrityObservation.LOW,
        ),
        source_integrity=IntegrityObservation.MEDIUM,
        stage_integrity=IntegrityObservation.LOW,
        watch_outcome_id=watch_outcome_id_for(outcome),
        watch_evidence_completion=WatchEvidenceCompletion.COMPLETED,
        scenario_started=True,
        fresh_retry_eligible=False,
        watcher_events=(),
        projection_count=1,
        projection_targets_verified=True,
        projection_payload_bytes_copied=0,
        production_backup_names=(),
        source_restored_after_quarantine=True,
        reasons=(),
    )
    if scenario is ContainmentScenario.NEW_FOLDER:
        result = ScenarioResult(
            **common,
            staging_new_names=("ModLab Spike New",),
            staging_output_names=("meshes/new-folder.bin",),
            adopted_name="ModLab Spike New",
            adopted_tree=tree("b"),
            adopted_integrity=IntegrityObservation.MEDIUM,
        )
    elif scenario is ContainmentScenario.FOMOD_DEPENDENCY:
        result = ScenarioResult(
            **common,
            staging_new_names=("ModLab Spike FOMOD",),
            staging_output_names=("always.txt", "dependency-seen.txt"),
            adopted_name="ModLab Spike FOMOD",
            adopted_tree=tree("b"),
            adopted_integrity=IntegrityObservation.MEDIUM,
        )
    else:
        result = ScenarioResult(
            **common,
            staging_new_names=(),
            staging_output_names=(),
            adopted_name=None,
            adopted_tree=None,
            adopted_integrity=None,
        )
    return result, outcome


def valid_prepared_journal(root: Path) -> ScenarioJournal:
    scenario_root = root / "fixture"
    scenario_root.mkdir()
    archive = scenario_root / "archive.zip"
    archive.write_bytes(b"zip")
    return ScenarioJournal(
        schema_version=1,
        run_id=RUN_ID,
        scenario=ContainmentScenario.MERGE_EXISTING,
        state=ScenarioState.PREPARED,
        source_root=str(scenario_root / "source"),
        stage_root=str(scenario_root / "stage"),
        archive_path=str(archive),
        protected_mod_name="Protected Existing",
        expected_new_mod_name="ModLab Spike New",
        protected_before=protected(),
        monitor_pid=None,
        mo2_pid=None,
        error=None,
    )


class ContainmentStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="modlab-containment-store-")
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_claim_and_transitions_are_atomic_and_idempotent(self):
        store = ContainmentStore(self.root)
        journal = store.create(valid_prepared_journal(self.root))
        self.assertEqual(journal, store.create(journal))

        armed = store.transition(journal, ScenarioState.ARMED, monitor_pid=17)
        self.assertEqual(armed, store.load_journal(armed.run_id, armed.scenario))
        self.assertEqual(armed, store.transition(armed, ScenarioState.ARMED))
        with self.assertRaisesRegex(ContainmentStoreError, "Armed -> Prepared"):
            store.transition(armed, ScenarioState.PREPARED)

    def test_result_and_decision_are_immutable(self):
        store = ContainmentStore(self.root)
        result, watch = valid_scenario_result()
        store.write_watch_outcome(watch)

        first = store.write_result(result)
        second = store.write_result(result)
        self.assertFalse(first.existed)
        self.assertTrue(second.existed)
        self.assertEqual(result, store.load_result(RUN_ID, result.scenario))

        changed = replace(result, outcome=ScenarioOutcome.INCOMPLETE, reasons=("manual",))
        with self.assertRaisesRegex(ContainmentStoreError, "different bytes"):
            store.write_result(changed)

        result_id = scenario_result_id_for(result, watch)
        decision = CapabilityDecision(
            1,
            RUN_ID,
            "isolated-low-integrity-junction-projection-v1",
            CapabilityVerdict.INCOMPLETE,
            (result_id,),
            ("remaining-scenarios-unavailable",),
        )
        first_decision = store.write_decision(decision)
        second_decision = store.write_decision(decision)
        self.assertFalse(first_decision.existed)
        self.assertTrue(second_decision.existed)
        self.assertEqual(decision, store.load_decision(RUN_ID))

    def test_store_uses_exact_confined_layout_and_rejects_tampered_readback(self):
        store = ContainmentStore(self.root)
        journal = store.create(valid_prepared_journal(self.root))
        path = (
            self.root
            / RUN_ID.removeprefix("containment-run:")
            / "scenarios"
            / ContainmentScenario.MERGE_EXISTING.value
            / "journal.json"
        )
        self.assertTrue(path.is_file())
        document = json.loads(path.read_bytes())
        document["protectedModName"] = "Different"
        path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(ContainmentStoreError, "canonical|bytes"):
            store.load_journal(journal.run_id, journal.scenario)

    def test_captured_transition_requires_durable_after_and_result(self):
        store = ContainmentStore(self.root)
        prepared = store.create(valid_prepared_journal(self.root))
        armed = store.transition(prepared, ScenarioState.ARMED, monitor_pid=17)
        started = store.transition(armed, ScenarioState.SCENARIO_STARTED)
        launched = store.transition(started, ScenarioState.LAUNCHED, mo2_pid=51)
        with self.assertRaisesRegex(
            ContainmentStoreError,
            "Captured requires durable after/result",
        ):
            store.transition(launched, ScenarioState.CAPTURED)

    def test_journal_replace_uses_post_rename_durability_helper(self):
        store = ContainmentStore(self.root)
        journal = store.create(valid_prepared_journal(self.root))
        calls = []
        real = containment_store._replace_durable
        with mock.patch.object(
            containment_store,
            "_replace_durable",
            side_effect=lambda source, target: (calls.append((source, target)), real(source, target))[1],
        ):
            store.transition(journal, ScenarioState.ARMED, monitor_pid=17)
        self.assertEqual(1, len(calls))
        self.assertEqual(store.journal_path(RUN_ID, journal.scenario), calls[0][1])

    def test_windows_durable_replace_uses_replace_existing_and_write_through(self):
        calls = []

        class FakeMove:
            argtypes = None
            restype = None

            def __call__(self, source, target, flags):
                calls.append((source, target, flags))
                return True

        with (
            mock.patch.object(containment_store.os, "name", "nt"),
            mock.patch.object(
                containment_store.ctypes,
                "WinDLL",
                return_value=type("Kernel", (), {"MoveFileExW": FakeMove()})(),
            ),
        ):
            containment_store._replace_durable(Path("source.part"), Path("journal.json"))
        self.assertEqual(0x00000001 | 0x00000008, calls[0][2])

    def test_posix_durable_replace_syncs_parent_directory(self):
        source = self.root / "source.part"
        target = self.root / "journal.json"
        with (
            mock.patch.object(containment_store.os, "name", "posix"),
            mock.patch.object(containment_store.os, "replace") as replace_call,
            mock.patch.object(containment_store.os, "open", return_value=71) as open_call,
            mock.patch.object(containment_store.os, "fsync") as fsync_call,
            mock.patch.object(containment_store.os, "close") as close_call,
        ):
            containment_store._replace_durable(source, target)
        replace_call.assert_called_once_with(source, target)
        self.assertEqual(self.root, open_call.call_args.args[0])
        fsync_call.assert_called_once_with(71)
        close_call.assert_called_once_with(71)

    def test_launch_evidence_is_strict_immutable_and_restart_loadable(self):
        store = ContainmentStore(self.root)
        document = {
            "schemaVersion": 1,
            "runId": RUN_ID,
            "scenario": ContainmentScenario.MERGE_EXISTING.value,
            "purpose": "stage-mo2-scenario",
            "pid": 51,
            "creationTime": 123456,
            "executable": r"C:\stage\app\ModOrganizer.exe",
            "executableVersion": "2.5.2.0",
            "arguments": ["--profile", "ModLab - Lab"],
            "workingDirectory": r"C:\stage\app",
            "integrity": IntegrityObservation.LOW.value,
        }
        first = store.write_launch_evidence(RUN_ID, ContainmentScenario.MERGE_EXISTING, document)
        second = store.write_launch_evidence(RUN_ID, ContainmentScenario.MERGE_EXISTING, document)
        self.assertFalse(first.existed)
        self.assertTrue(second.existed)
        self.assertEqual(document, store.load_launch_evidence(RUN_ID, ContainmentScenario.MERGE_EXISTING))
        with self.assertRaisesRegex(ContainmentStoreError, "different bytes"):
            store.write_launch_evidence(
                RUN_ID,
                ContainmentScenario.MERGE_EXISTING,
                {**document, "creationTime": 999},
            )

    def test_retry_authority_is_exact_and_consumed_once(self):
        store = ContainmentStore(self.root)
        recovery = ScenarioRecovery(
            1, RUN_ID, ContainmentScenario.MERGE_EXISTING,
            "containment-journal-sha256:" + "a" * 64,
            "containment-result-sha256:" + "b" * 64,
            ScenarioCleanupStatus.SUCCEEDED, True, (),
        )
        written = store.write_recovery(recovery)
        fingerprint = "containment-command-sha256:" + "c" * 64
        available = store.write_retry_authority(recovery, written.content_id, fingerprint)
        self.assertEqual("Available", available.value["state"])
        retry_of = store.consume_retry_authority(
            recovery,
            written.content_id,
            fingerprint,
            "containment-run:" + "d" * 32,
        )
        self.assertEqual(available.value["authorityId"], retry_of["authorityId"])
        self.assertEqual("Consumed", store.load_retry_authority(RUN_ID, recovery.scenario)["state"])
        with self.assertRaisesRegex(ContainmentStoreError, "already consumed"):
            store.consume_retry_authority(
                recovery,
                written.content_id,
                fingerprint,
                "containment-run:" + "e" * 32,
            )


if __name__ == "__main__":
    unittest.main()
