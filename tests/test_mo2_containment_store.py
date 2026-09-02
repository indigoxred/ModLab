import hashlib
import json
import stat
import tempfile
import threading
import unittest
from unittest import mock
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from modlab.platform import windows_exact_fs
from modlab.platform.windows_exact_fs import (
    ExactObjectOwnershipError,
    PinnedIdentity,
    PinnedObject,
)
from modlab.validation.mo2_containment_model import (
    CapabilityDecision,
    DecisionBindings,
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
    source_artifact_id_for,
    watch_outcome_id_for,
)
from modlab.validation.mo2_containment_store import (
    ContainmentStore,
    ContainmentStoreError,
    ContainmentStoreMalformedEvidence,
    ContainmentStoreOwnershipError,
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
        result = ScenarioResult(
            **common,
            staging_new_names=("ModLab Spike New",),
            staging_output_names=("meshes/new-folder.bin", "meta.ini"),
            adopted_name="ModLab Spike New",
            adopted_tree=tree("b"),
            adopted_integrity=IntegrityObservation.MEDIUM,
        )
    elif scenario is ContainmentScenario.FOMOD_DEPENDENCY:
        result = ScenarioResult(
            **common,
            staging_new_names=("ModLab Spike FOMOD",),
            staging_output_names=("always.txt", "dependency-seen.txt", "meta.ini"),
            adopted_name="ModLab Spike FOMOD",
            adopted_tree=tree("b"),
            adopted_integrity=IntegrityObservation.MEDIUM,
        )
    elif scenario is ContainmentScenario.REPLACE_EXISTING:
        result = ScenarioResult(
            **common,
            staging_new_names=(),
            staging_output_names=("meshes/canary.bin", "meshes/new.bin", "meta.ini"),
            adopted_name=None,
            adopted_tree=None,
            adopted_integrity=None,
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


def valid_bound_decision(store: ContainmentStore) -> CapabilityDecision:
    pairs = tuple(valid_scenario_result(scenario) for scenario in ContainmentScenario)
    for result, outcome in pairs:
        store.write_watch_outcome(outcome)
        store.write_result(result)
    return CapabilityDecision(
        2,
        RUN_ID,
        "isolated-low-integrity-junction-projection-v1",
        CapabilityVerdict.SUPPORTED,
        tuple(scenario_result_id_for(result, outcome) for result, outcome in pairs),
        (),
        DecisionBindings(
            2, "a" * 40, "b" * 40,
            source_artifact_id_for({"schemaVersion": 1, "artifact": "store"}),
            2, "handle-pinned-no-replace-v2", 2, 1, 1, "2.5.2.0", "c" * 64,
        ),
    )


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

    def test_open_readonly_requires_existing_root_without_creating_it(self):
        absent = self.root / "absent-validation"

        with self.assertRaises(containment_store.ContainmentStoreNotFound):
            ContainmentStore.open_readonly(absent)

        self.assertFalse(absent.exists())

    def test_open_readonly_never_prepares_existing_root_or_children(self):
        existing = self.root / "existing-validation"
        existing.mkdir()

        with mock.patch.object(
            ContainmentStore,
            "_ensure_direct_directory",
            side_effect=AssertionError("read-only open attempted preparation"),
        ) as prepare:
            store = ContainmentStore.open_readonly(existing)
            self.assertEqual(existing.absolute(), store.root)
            self.assertEqual((), store.list_run_ids())

        prepare.assert_not_called()
        self.assertEqual((), tuple(existing.iterdir()))

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

    def test_bound_decision_probe_rejects_wrong_run_and_noncanonical_bytes(self):
        store = ContainmentStore(self.root)
        decision = valid_bound_decision(store)
        store.write_decision(decision)
        data = store.decision_path(RUN_ID).read_bytes()
        with self.assertRaises(containment_store.ContainmentStoreMalformedEvidence):
            ContainmentStore._decision_probe(data, "containment-run:" + "f" * 32)
        with self.assertRaises(containment_store.ContainmentStoreMalformedEvidence):
            ContainmentStore._decision_probe(data.replace(b"{", b"{ ", 1), RUN_ID)

    def test_bound_decision_is_immutable_for_identical_and_changed_bytes(self):
        store = ContainmentStore(self.root)
        decision = valid_bound_decision(store)
        first = store.write_decision(decision)
        second = store.write_decision(decision)
        self.assertFalse(first.existed)
        self.assertTrue(second.existed)
        changed_bindings = (
            replace(decision.bindings, source_commit_id="d" * 40),
            replace(decision.bindings, source_tree_id="e" * 40),
            replace(
                decision.bindings,
                source_artifact_id="containment-source-artifact-sha256:" + "f" * 64,
            ),
        )
        for bindings in changed_bindings:
            with self.subTest(bindings=bindings):
                with self.assertRaisesRegex(ContainmentStoreError, "different bytes"):
                    store.write_decision(replace(decision, bindings=bindings))

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

    def test_malformed_scenario_result_has_a_typed_read_error(self):
        store = ContainmentStore(self.root)
        result, outcome = valid_scenario_result()
        store.write_watch_outcome(outcome)
        store.result_path(RUN_ID, result.scenario).write_bytes(b"{}\n")

        with self.assertRaises(
            containment_store.ContainmentStoreMalformedEvidence,
        ):
            store.load_result(RUN_ID, result.scenario)

    def test_noncanonical_scenario_result_has_a_typed_read_error(self):
        store = ContainmentStore(self.root)
        result, outcome = valid_scenario_result()
        store.write_watch_outcome(outcome)
        store.write_result(result)
        path = store.result_path(RUN_ID, result.scenario)
        path.write_bytes(path.read_bytes().replace(b"{", b"{ ", 1))

        with self.assertRaises(
            containment_store.ContainmentStoreMalformedEvidence,
        ):
            store.load_result(RUN_ID, result.scenario)

    def test_malformed_capability_decision_has_a_typed_read_error(self):
        with self.assertRaises(
            containment_store.ContainmentStoreMalformedEvidence,
        ):
            ContainmentStore._decision_probe(b"{}\n", RUN_ID)

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

    def test_effect_recorder_keeps_write_when_replace_fails_after_mutation(self):
        recorded = []
        store = ContainmentStore(self.root, _effect_recorder=recorded.append)
        journal = store.create(valid_prepared_journal(self.root))
        recorded.clear()

        def replace_then_fail(source, target):
            target.write_bytes(source.read_bytes())
            source.unlink()
            raise OSError("injected durability failure after replacement")

        with (
            mock.patch.object(
                containment_store,
                "_replace_durable",
                side_effect=replace_then_fail,
            ),
            self.assertRaisesRegex(ContainmentStoreError, "atomically replace"),
        ):
            store.transition(journal, ScenarioState.ARMED, monitor_pid=17)

        self.assertEqual([store.journal_path(RUN_ID, journal.scenario)], recorded)

    def test_effect_recorder_distinguishes_post_publication_from_prepublication_failure(self):
        recorded = []
        store = ContainmentStore(self.root, _effect_recorder=recorded.append)
        written_target = self.root / "written.json"
        refused_target = self.root / "refused.json"
        data = b'{"exact":true}\n'

        def publish_then_fail(path, payload, _validator):
            path.write_bytes(payload)
            raise OSError("injected failure after publication")

        with (
            mock.patch.object(
                containment_store,
                "publish_new_pinned",
                side_effect=publish_then_fail,
            ),
            self.assertRaisesRegex(ContainmentStoreError, "atomically create"),
        ):
            store._write_immutable(written_target, data, "written record")

        with (
            mock.patch.object(
                containment_store,
                "publish_new_pinned",
                side_effect=OSError("injected refusal before publication"),
            ),
            self.assertRaisesRegex(ContainmentStoreError, "atomically create"),
        ):
            store._write_immutable(refused_target, data, "refused record")

        self.assertEqual(data, written_target.read_bytes())
        self.assertFalse(refused_target.exists())
        self.assertEqual([written_target], recorded)

    def test_effect_recorder_keeps_posix_promotion_before_late_failure(self):
        recorded = []
        store = ContainmentStore(self.root, _effect_recorder=recorded.append)
        target = self.root / "promoted.json"
        data = b'{"promoted":true}\n'

        def promote_then_fail(source, destination):
            destination.write_bytes(source.read_bytes())
            raise OSError("injected failure after promotion")

        with (
            mock.patch.object(containment_store.os, "name", "posix"),
            mock.patch.object(
                containment_store,
                "_promote_no_replace_posix",
                side_effect=promote_then_fail,
            ),
            self.assertRaisesRegex(ContainmentStoreError, "after promotion"),
        ):
            store._write_immutable(target, data, "promoted record")

        self.assertEqual(data, target.read_bytes())
        self.assertEqual([target], recorded)

    def test_windows_immutable_writes_route_only_to_retained_publication(self):
        store = ContainmentStore(self.root)
        calls = []

        def publish(path, data, validator):
            calls.append((path, data))
            self.assertEqual(data, validator(data))
            return data

        records = (
            (self.root / "request.json", b'{"record":"request"}\n', "request"),
            (self.root / "result.json", b'{"record":"result"}\n', "scenario result"),
            (self.root / "decision.json", b'{"record":"decision"}\n', "capability decision"),
        )
        with (
            mock.patch.object(
                containment_store,
                "publish_new_pinned",
                side_effect=publish,
            ),
            mock.patch.object(
                containment_store,
                "_promote_no_replace_posix",
                side_effect=AssertionError("Windows reached POSIX promotion"),
            ) as posix_promotion,
        ):
            for target, data, label in records:
                self.assertFalse(store._write_immutable(target, data, label))

        self.assertEqual(
            [(target, data) for target, data, _label in records],
            calls,
        )
        posix_promotion.assert_not_called()
        self.assertTrue(all(not target.exists() for target, _data, _label in records))

    def test_failed_windows_immutable_publication_records_unexpected_new_target(self):
        recorded = []
        store = ContainmentStore(self.root, _effect_recorder=recorded.append)
        target = self.root / "unexpected-windows.json"

        def publish_then_fail(path, _data, _validator):
            path.write_bytes(b"unexpected\n")
            raise OSError("injected failure after unexpected publication")

        with (
            mock.patch.object(containment_store.os, "name", "nt"),
            mock.patch.object(
                containment_store,
                "publish_new_pinned",
                side_effect=publish_then_fail,
            ),
            self.assertRaises(ContainmentStoreError),
        ):
            store._write_immutable(target, b"expected\n", "Windows record")

        self.assertEqual(b"unexpected\n", target.read_bytes())
        self.assertEqual([target], recorded)

    def test_failed_posix_immutable_promotion_records_unexpected_new_target(self):
        recorded = []
        store = ContainmentStore(self.root, _effect_recorder=recorded.append)
        target = self.root / "unexpected-posix.json"

        def promote_then_fail(_source, destination):
            destination.write_bytes(b"unexpected\n")
            raise OSError("injected failure after unexpected promotion")

        with (
            mock.patch.object(containment_store.os, "name", "posix"),
            mock.patch.object(
                containment_store,
                "_promote_no_replace_posix",
                side_effect=promote_then_fail,
            ),
            self.assertRaises(ContainmentStoreError),
        ):
            store._write_immutable(target, b"expected\n", "POSIX record")

        self.assertEqual(b"unexpected\n", target.read_bytes())
        self.assertEqual([target], recorded)

    def test_failed_replacement_records_deleted_target(self):
        recorded = []
        store = ContainmentStore(self.root, _effect_recorder=recorded.append)
        target = self.root / "deleted-replacement.json"
        target.write_bytes(b"before\n")

        def delete_then_fail(_source, destination):
            destination.unlink()
            raise OSError("injected failure after target deletion")

        with (
            mock.patch.object(
                containment_store,
                "_replace_durable",
                side_effect=delete_then_fail,
            ),
            self.assertRaises(ContainmentStoreError),
        ):
            store._atomic_replace(
                target,
                b"after\n",
                b"before\n",
                "replacement record",
            )

        self.assertFalse(target.exists())
        self.assertEqual([target], recorded)

    def test_failed_immutable_cleanup_records_surviving_part(self):
        recorded = []
        store = ContainmentStore(self.root, _effect_recorder=recorded.append)
        target = self.root / "part-survival.json"
        token = "1" * 32
        part = self.root / f".{target.name}.{token}.part"
        real_unlink = Path.unlink

        def refuse_part_cleanup(path, *args, **kwargs):
            if path == part:
                raise OSError("injected part cleanup failure")
            return real_unlink(path, *args, **kwargs)

        with (
            mock.patch.object(containment_store.os, "name", "posix"),
            mock.patch.object(
                containment_store.uuid,
                "uuid4",
                return_value=SimpleNamespace(hex=token),
            ),
            mock.patch.object(
                containment_store,
                "_promote_no_replace_posix",
                side_effect=OSError("injected pre-promotion failure"),
            ),
            mock.patch.object(type(part), "unlink", new=refuse_part_cleanup),
            self.assertRaises(ContainmentStoreError),
        ):
            store._write_immutable(target, b"payload\n", "part record")

        self.assertTrue(part.is_file())
        self.assertEqual([part], recorded)

    def test_failed_replacement_cleanup_records_surviving_part_only(self):
        recorded = []
        store = ContainmentStore(self.root, _effect_recorder=recorded.append)
        target = self.root / "replace-part-survival.json"
        target.write_bytes(b"before\n")
        token = "2" * 32
        part = self.root / f".{target.name}.{token}.part"
        real_unlink = Path.unlink

        def refuse_part_cleanup(path, *args, **kwargs):
            if path == part:
                raise OSError("injected replacement part cleanup failure")
            return real_unlink(path, *args, **kwargs)

        with (
            mock.patch.object(
                containment_store.uuid,
                "uuid4",
                return_value=SimpleNamespace(hex=token),
            ),
            mock.patch.object(
                containment_store,
                "_replace_durable",
                side_effect=OSError("injected pre-replacement failure"),
            ),
            mock.patch.object(type(part), "unlink", new=refuse_part_cleanup),
            self.assertRaises(ContainmentStoreError),
        ):
            store._atomic_replace(
                target,
                b"after\n",
                b"before\n",
                "replacement part record",
            )

        self.assertEqual(b"before\n", target.read_bytes())
        self.assertTrue(part.is_file())
        self.assertEqual([part], recorded)

    def test_posix_immutable_promotion_keeps_hard_link_no_replace_behavior(self):
        source = self.root / "first.part"
        target = self.root / "immutable.json"
        source.write_bytes(b"first")
        with mock.patch.object(containment_store.os, "name", "posix"):
            containment_store._promote_no_replace_posix(source, target)
        self.assertFalse(source.exists())
        self.assertEqual(b"first", target.read_bytes())

        colliding_source = self.root / "second.part"
        colliding_source.write_bytes(b"second")
        with (
            mock.patch.object(containment_store.os, "name", "posix"),
            self.assertRaises(FileExistsError),
        ):
            containment_store._promote_no_replace_posix(colliding_source, target)
        self.assertEqual(b"second", colliding_source.read_bytes())
        self.assertEqual(b"first", target.read_bytes())

    def test_posix_immutable_promotion_fails_closed_on_windows(self):
        source = self.root / "immutable.part"
        source.write_bytes(b"payload")
        with self.assertRaisesRegex(ContainmentStoreError, "POSIX-only"):
            containment_store._promote_no_replace_posix(
                source,
                self.root / "immutable.json",
            )
        self.assertEqual(b"payload", source.read_bytes())

    def test_windows_immutable_write_preserves_structured_ownership_for_retry(self):
        store = ContainmentStore(self.root)
        candidates = (
            PinnedObject(self.root / "immutable-one.json", 81, PinnedIdentity(1, 1, 0)),
            PinnedObject(self.root / "immutable-two.json", 82, PinnedIdentity(1, 2, 0)),
        )
        owner_type = windows_exact_fs.RetainedObjectOwner
        role = windows_exact_fs.RetainedObjectRole
        ownership = ExactObjectOwnershipError(
            "injected",
            owners=tuple(owner_type(role.CANDIDATE, candidate) for candidate in candidates),
        )
        with (
            mock.patch.object(containment_store, "publish_new_pinned", side_effect=ownership),
            mock.patch.object(containment_store, "resolve_retained_ownership", side_effect=ownership),
        ):
            with self.assertRaises(ContainmentStoreOwnershipError) as raised:
                store._write_immutable(self.root / "immutable.json", b"{}\n", "immutable record")
        self.assertEqual(ownership.owners, raised.exception.owners)
        self.assertIs(candidates[0], raised.exception.candidate)
        self.assertEqual(candidates, raised.exception.candidates)

    def test_windows_ownership_failure_records_surviving_candidate_path(self):
        recorded = []
        store = ContainmentStore(self.root, _effect_recorder=recorded.append)
        candidate_path = self.root / ".immutable.json.retained.tmp"
        candidate_path.write_bytes(b"partial\n")
        candidate = PinnedObject(
            candidate_path,
            91,
            PinnedIdentity(1, 91, 0),
        )
        ownership = ExactObjectOwnershipError(
            "injected retained candidate",
            candidate=candidate,
        )
        with (
            mock.patch.object(
                containment_store,
                "publish_new_pinned",
                side_effect=ownership,
            ),
            mock.patch.object(
                containment_store,
                "resolve_retained_ownership",
                side_effect=ownership,
            ),
            self.assertRaises(ContainmentStoreOwnershipError),
        ):
            store._write_immutable(
                self.root / "immutable.json",
                b"expected\n",
                "immutable record",
            )

        self.assertEqual([candidate_path], recorded)

    def test_windows_immutable_write_never_publishes_a_substitute_at_candidate_path(self):
        store = ContainmentStore(self.root)
        target = self.root / "immutable.json"
        data = b'{"ok":true}\n'
        real_read = windows_exact_fs.read_pinned_file
        swapped = False

        def read_then_replace_old_candidate(pinned):
            nonlocal swapped
            observed = real_read(pinned)
            if not swapped and pinned.path.name.startswith(".immutable.json."):
                swapped = True
                stolen = pinned.path.with_name("stolen-immutable.json")
                pinned.path.rename(stolen)
                pinned.path.write_bytes(b'{"replacement":true}\n')
            return observed

        with mock.patch.object(
            windows_exact_fs,
            "read_pinned_file",
            side_effect=read_then_replace_old_candidate,
        ):
            self.assertFalse(store._write_immutable(target, data, "immutable record"))

        self.assertTrue(swapped)
        self.assertEqual(data, target.read_bytes())
        replacements = list(self.root.glob(".immutable.json.*.tmp"))
        self.assertEqual(1, len(replacements))
        self.assertEqual(b'{"replacement":true}\n', replacements[0].read_bytes())

    def test_retry_authority_consumption_never_uses_durable_replacement(self):
        store = ContainmentStore(self.root)
        recovery = ScenarioRecovery(
            1, RUN_ID, ContainmentScenario.MERGE_EXISTING,
            "containment-journal-sha256:" + "a" * 64,
            "containment-result-sha256:" + "b" * 64,
            ScenarioCleanupStatus.SUCCEEDED, True, (),
        )
        written = store.write_recovery(recovery)
        fingerprint = "containment-command-sha256:" + "c" * 64
        store.write_retry_authority(recovery, written.content_id, fingerprint)
        with mock.patch.object(containment_store, "_replace_durable") as replace_call:
            store.consume_retry_authority(
                recovery,
                written.content_id,
                fingerprint,
                "containment-run:" + "d" * 32,
            )
        replace_call.assert_not_called()

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
        persisted = json.loads(store.retry_path(RUN_ID, recovery.scenario).read_text("utf-8"))
        self.assertEqual("Available", persisted["state"])
        self.assertIsNone(persisted["consumedByRunId"])
        with self.assertRaisesRegex(ContainmentStoreError, "already consumed"):
            store.consume_retry_authority(
                recovery,
                written.content_id,
                fingerprint,
                "containment-run:" + "d" * 32,
            )
        with self.assertRaisesRegex(ContainmentStoreError, "already consumed"):
            store.consume_retry_authority(
                recovery,
                written.content_id,
                fingerprint,
                "containment-run:" + "e" * 32,
            )

    def test_retry_authority_rejects_legacy_embedded_consumption_without_marker(self):
        store = ContainmentStore(self.root)
        recovery = ScenarioRecovery(1, RUN_ID, ContainmentScenario.MERGE_EXISTING,
            "containment-journal-sha256:" + "a" * 64,
            "containment-result-sha256:" + "b" * 64,
            ScenarioCleanupStatus.SUCCEEDED, True, ())
        written = store.write_recovery(recovery)
        fingerprint = "containment-command-sha256:" + "c" * 64
        authority = store.write_retry_authority(recovery, written.content_id, fingerprint).value
        authority["state"] = "Consumed"
        authority["consumedByRunId"] = "containment-run:" + "d" * 32
        store.retry_path(RUN_ID, recovery.scenario).write_bytes(containment_store._canonical(authority))
        with self.assertRaisesRegex(ContainmentStoreError, "retry authority values"):
            store.load_retry_authority(RUN_ID, recovery.scenario)

    def test_retry_authority_rejects_foreign_consumption_marker(self):
        store = ContainmentStore(self.root)
        recovery = ScenarioRecovery(1, RUN_ID, ContainmentScenario.MERGE_EXISTING,
            "containment-journal-sha256:" + "a" * 64,
            "containment-result-sha256:" + "b" * 64,
            ScenarioCleanupStatus.SUCCEEDED, True, ())
        written = store.write_recovery(recovery)
        fingerprint = "containment-command-sha256:" + "c" * 64
        authority = store.write_retry_authority(recovery, written.content_id, fingerprint).value
        marker = containment_store._retry_consumption_document(authority, "containment-run:" + "d" * 32)
        marker["authorityId"] = "containment-retry-sha256:" + "f" * 64
        store.retry_consumption_path(RUN_ID, recovery.scenario).write_bytes(containment_store._canonical(marker))
        with self.assertRaisesRegex(ContainmentStoreError, "consumption binding"):
            store.load_retry_authority(RUN_ID, recovery.scenario)

    def test_retry_authority_rejects_malformed_consumption_marker(self):
        store = ContainmentStore(self.root)
        recovery = ScenarioRecovery(
            1, RUN_ID, ContainmentScenario.MERGE_EXISTING,
            "containment-journal-sha256:" + "a" * 64,
            "containment-result-sha256:" + "b" * 64,
            ScenarioCleanupStatus.SUCCEEDED, True, (),
        )
        written = store.write_recovery(recovery)
        store.write_retry_authority(
            recovery,
            written.content_id,
            "containment-command-sha256:" + "c" * 64,
        )
        store.retry_consumption_path(RUN_ID, recovery.scenario).write_bytes(b"{broken")
        with self.assertRaisesRegex(ContainmentStoreError, "consumption is malformed"):
            store.load_retry_authority(RUN_ID, recovery.scenario)

    def test_retry_authority_rejects_embedded_consumer_marker_conflict(self):
        store = ContainmentStore(self.root)
        recovery = ScenarioRecovery(
            1, RUN_ID, ContainmentScenario.MERGE_EXISTING,
            "containment-journal-sha256:" + "a" * 64,
            "containment-result-sha256:" + "b" * 64,
            ScenarioCleanupStatus.SUCCEEDED, True, (),
        )
        written = store.write_recovery(recovery)
        fingerprint = "containment-command-sha256:" + "c" * 64
        authority = store.write_retry_authority(
            recovery,
            written.content_id,
            fingerprint,
        ).value
        marker = containment_store._retry_consumption_document(
            authority,
            "containment-run:" + "d" * 32,
        )
        store.retry_consumption_path(RUN_ID, recovery.scenario).write_bytes(
            containment_store._canonical(marker)
        )
        authority["state"] = "Consumed"
        authority["consumedByRunId"] = "containment-run:" + "e" * 32
        store.retry_path(RUN_ID, recovery.scenario).write_bytes(
            containment_store._canonical(authority)
        )

        with self.assertRaisesRegex(ContainmentStoreError, "retry authority values"):
            store.load_retry_authority(RUN_ID, recovery.scenario)

    def test_retry_consumption_refuses_success_when_marker_readback_is_missing(self):
        store = ContainmentStore(self.root)
        recovery = ScenarioRecovery(
            1,
            RUN_ID,
            ContainmentScenario.MERGE_EXISTING,
            "containment-journal-sha256:" + "a" * 64,
            "containment-result-sha256:" + "b" * 64,
            ScenarioCleanupStatus.SUCCEEDED,
            True,
            (),
        )
        written = store.write_recovery(recovery)
        fingerprint = "containment-command-sha256:" + "c" * 64
        store.write_retry_authority(recovery, written.content_id, fingerprint)

        with mock.patch.object(store, "_write_immutable", return_value=False):
            with self.assertRaisesRegex(
                ContainmentStoreError,
                "consumption marker readback",
            ):
                store.consume_retry_authority(
                    recovery,
                    written.content_id,
                    fingerprint,
                    "containment-run:" + "d" * 32,
                )

    def test_retry_consumption_publication_failure_leaves_authority_available(self):
        store = ContainmentStore(self.root)
        recovery = ScenarioRecovery(
            1,
            RUN_ID,
            ContainmentScenario.MERGE_EXISTING,
            "containment-journal-sha256:" + "a" * 64,
            "containment-result-sha256:" + "b" * 64,
            ScenarioCleanupStatus.SUCCEEDED,
            True,
            (),
        )
        written = store.write_recovery(recovery)
        fingerprint = "containment-command-sha256:" + "c" * 64
        store.write_retry_authority(recovery, written.content_id, fingerprint)
        marker_path = store.retry_consumption_path(RUN_ID, recovery.scenario)
        real_write = store._write_immutable

        def fail_marker_publication(target, data, label):
            if target == marker_path:
                raise ContainmentStoreError("injected marker publication failure")
            return real_write(target, data, label)

        with mock.patch.object(
            store,
            "_write_immutable",
            side_effect=fail_marker_publication,
        ):
            with self.assertRaisesRegex(
                ContainmentStoreError,
                "marker publication failure",
            ):
                store.consume_retry_authority(
                    recovery,
                    written.content_id,
                    fingerprint,
                    "containment-run:" + "d" * 32,
                )

        self.assertFalse(marker_path.exists())
        loaded = store.load_retry_authority(RUN_ID, recovery.scenario)
        self.assertEqual("Available", loaded["state"])
        self.assertIsNone(loaded["consumedByRunId"])

    def test_run_intent_is_immutable_canonical_and_precedes_request(self):
        store = ContainmentStore(self.root)
        fingerprint = "containment-command-sha256:" + "c" * 64
        document = {
            "schemaVersion": 1,
            "runId": RUN_ID,
            "mechanism": "isolated-low-integrity-junction-projection-v1",
            "sourceWorkspace": str((self.root / "source").absolute()),
            "mo2ArtifactId": "artifact:abc",
            "steamRoot": str((self.root / "steam").absolute()),
            "commandFingerprint": fingerprint,
            "predecessorRunIds": [],
            "retryOf": None,
        }

        first = store.write_intent(RUN_ID, document)
        second = store.write_intent(RUN_ID, document)

        self.assertFalse(first.existed)
        self.assertTrue(second.existed)
        self.assertEqual(document, store.load_intent(RUN_ID))
        self.assertTrue(first.path.is_relative_to(store.run_path(RUN_ID)))
        self.assertFalse(store.request_path(RUN_ID).exists())
        with self.assertRaisesRegex(ContainmentStoreError, "different bytes"):
            store.write_intent(
                RUN_ID,
                {**document, "mo2ArtifactId": "artifact:different"},
            )

    def test_command_lock_serializes_same_root_and_fingerprint(self):
        first = ContainmentStore(self.root)
        second = ContainmentStore(self.root)
        fingerprint = "containment-command-sha256:" + "d" * 64
        acquired = threading.Event()
        errors = []

        def contend() -> None:
            try:
                with second.command_lock(fingerprint):
                    acquired.set()
            except BaseException as error:
                errors.append(error)

        with first.command_lock(fingerprint):
            thread = threading.Thread(target=contend)
            thread.start()
            self.assertFalse(acquired.wait(0.1))
        thread.join(2)

        self.assertFalse(thread.is_alive())
        self.assertEqual([], errors)
        self.assertTrue(acquired.is_set())

    def test_list_run_ids_accepts_valid_direct_directory_and_ignores_unrelated(self):
        store = ContainmentStore(self.root)
        valid_hex = RUN_ID.removeprefix("containment-run:")
        (self.root / valid_hex).mkdir()
        (self.root / "notes").mkdir()
        (self.root / "not-a-containment-run.txt").write_text(
            "unrelated\n", encoding="utf-8"
        )

        self.assertEqual((RUN_ID,), store.list_run_ids())

    def test_list_run_ids_rejects_regular_file_impostor(self):
        store = ContainmentStore(self.root)
        (self.root / ("a" * 32)).write_bytes(b"not a run directory\n")

        with self.assertRaisesRegex(ContainmentStoreError, "direct.*directory"):
            store.list_run_ids()

    def test_list_run_ids_rejects_case_variant_run_name(self):
        store = ContainmentStore(self.root)
        (self.root / ("A" * 32)).mkdir()

        with self.assertRaisesRegex(ContainmentStoreError, "noncanonical"):
            store.list_run_ids()

    def test_list_run_ids_rejects_no_follow_stat_uncertainty(self):
        for error in (PermissionError("access denied"), FileNotFoundError("gone")):
            with self.subTest(error=type(error).__name__):
                store = ContainmentStore(self.root)
                entry = mock.Mock()
                entry.name = "b" * 32
                entry.stat.side_effect = error
                with (
                    mock.patch.object(
                        containment_store.os,
                        "scandir",
                        return_value=(entry,),
                    ),
                    self.assertRaisesRegex(
                        ContainmentStoreError,
                        "cannot prove containment run entry",
                    ),
                ):
                    store.list_run_ids()
                entry.stat.assert_called_once_with(follow_symlinks=False)

    def test_list_run_ids_rejects_reparse_directory_metadata(self):
        store = ContainmentStore(self.root)
        entry = mock.Mock()
        entry.name = "c" * 32
        entry.stat.return_value = SimpleNamespace(
            st_mode=stat.S_IFDIR,
            st_file_attributes=containment_store._FILE_ATTRIBUTE_REPARSE_POINT,
        )
        with (
            mock.patch.object(
                containment_store.os,
                "scandir",
                return_value=(entry,),
            ),
            self.assertRaisesRegex(ContainmentStoreError, "reparse"),
        ):
            store.list_run_ids()

    def test_list_run_ids_rejects_symlink_when_supported(self):
        store = ContainmentStore(self.root)
        target = self.root / "target"
        target.mkdir()
        link = self.root / ("d" * 32)
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"directory symlink unavailable: {error}")

        with self.assertRaisesRegex(
            ContainmentStoreError,
            "direct.*directory|reparse",
        ):
            store.list_run_ids()

    def test_decision_resolution_does_not_hide_malformed_nested_result(self):
        store = ContainmentStore(self.root)
        decision = valid_bound_decision(store)
        damaged = store.result_path(
            RUN_ID,
            ContainmentScenario.MERGE_EXISTING,
        )
        damaged.write_bytes(b"{malformed\n")

        with self.assertRaises(ContainmentStoreMalformedEvidence):
            store.write_decision(decision)


if __name__ == "__main__":
    unittest.main()
