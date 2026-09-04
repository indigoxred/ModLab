import hashlib
import json
import os
import subprocess
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from modlab.adapters.mo2.bootstrap_model import ProcessObservation
from modlab.platform import windows_exact_fs
from modlab.platform.windows_exact_fs import (
    ExactObjectOwnershipError,
    PinnedIdentity,
    PinnedObject,
)
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
from modlab.validation.mo2_containment_store import (
    ContainmentStore,
    ContainmentStoreError,
    ContainmentStoreOwnershipError,
)
from modlab.validation import mo2_containment_service as service
from modlab.validation import windows_junction as junction
from modlab.validation.windows_watch_protocol import (
    CLAIM_NAME,
    LAUNCH_NAME,
    ControllerClaim,
    WatchRequest,
    WatchRoot,
    WorkerLaunch,
    controller_claim_to_bytes,
    watch_request_sha256,
    watch_request_to_bytes,
    watch_worker_command,
    worker_launch_to_bytes,
)
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


@unittest.skipUnless(os.name == "nt", "retained projection tests require Windows")
class PreparationProjectionRegressionTests(unittest.TestCase):
    def test_delegate_and_projection_close_failures_retain_the_union_of_live_owners(self):
        with tempfile.TemporaryDirectory(prefix="modlab-owner-union-", dir=Path(__file__).resolve().parents[1]) as directory:
            base = Path(directory)
            root = base / "observed"
            target = root / "source/Protected"
            target.mkdir(parents=True)
            (root / "stage").mkdir()
            unrelated = base / "delegate-owned"
            unrelated.mkdir()
            pins = []
            real_close = junction._close_handle
            def close(handle):
                if any(pin.handle == handle for pin in pins):
                    raise OSError("injected exact-owner close failure")
                real_close(handle)
            def operation():
                owner = junction.create_owned_projection(target, root / "stage/Protected")
                pins.extend(owner.pins)
                extra = junction._pin_object(unrelated, desired_access=junction._FILE_READ_ATTRIBUTES, allow_reparse=False)
                pins.append(extra)
                failure = junction.JunctionOwnershipError("delegate retains a separate exact owner", (extra,))
                failure.projection_owners = (owner,)
                raise failure
            token = service._ACTIVE_EFFECTS.set(service._EffectLedger())
            try:
                with patch.object(junction, "_close_handle", side_effect=close):
                    with self.assertRaises(junction.JunctionOwnershipError) as raised:
                        service._delegated_mutations((root,), operation)
                self.assertEqual(5, len(raised.exception.pins))
            finally:
                service._ACTIVE_EFFECTS.reset(token)
                junction._close_pinned_objects(tuple(pins), "test owner union")

    def test_real_delegate_exception_observes_partial_projection_then_releases_owners(self):
        with tempfile.TemporaryDirectory(prefix="modlab-partial-owner-", dir=Path(__file__).resolve().parents[1]) as directory:
            root = Path(directory)
            target = root / "source/Protected"
            target.mkdir(parents=True)
            stage = root / "stage"
            stage.mkdir()
            owners = []
            failure = RuntimeError("genuine delegate failure after partial mutation")
            def operation():
                owner = junction.create_owned_projection(target, stage / "Protected")
                owners.append(owner)
                (stage / "partial.txt").write_bytes(b"actual partial output")
                failure.projection_owners = (owner,)
                raise failure
            ledger = service._EffectLedger()
            token = service._ACTIVE_EFFECTS.set(ledger)
            try:
                with self.assertRaises(RuntimeError) as raised:
                    service._delegated_mutations((root,), operation)
                self.assertIs(failure, raised.exception)
                self.assertEqual((root,), ledger.freeze().child_mutation_roots)
                self.assertTrue(all(not pinned.handle for pinned in owners[0].pins))
                self.assertEqual(b"actual partial output", (stage / "partial.txt").read_bytes())
            finally:
                service._ACTIVE_EFFECTS.reset(token)
                for owner in owners:
                    owner.close()

    def test_real_fixture_reaches_guarded_post_observation(self):
        # Catches loss of the real fixture's projection owner at the effect boundary.
        from tests.support.mo2_containment import prepare_fixture_with_fake_bootstrap
        with tempfile.TemporaryDirectory(prefix="modlab-preparation-red-", dir=Path(__file__).resolve().parents[1]) as directory:
            root = Path(directory)
            ledger = service._EffectLedger()
            token = service._ACTIVE_EFFECTS.set(ledger)
            try:
                try:
                    fixture = service._delegated_mutations(
                        (root,), lambda: prepare_fixture_with_fake_bootstrap(root, retain_projection_owners=True)
                    )
                except service.ContainmentServiceError as error:
                    self.fail(f"real guarded fixture rejected its own projection: {error}")
                self.assertTrue(fixture.stage_mods.joinpath("Protected Existing").is_junction())
                self.assertEqual((root,), ledger.freeze().child_mutation_roots)
            finally:
                service._ACTIVE_EFFECTS.reset(token)

    def test_job_created_projection_is_observed_without_following_target(self):
        # Catches blanket reparse rejection and traversal through an allowed link.
        self.assertTrue(callable(getattr(junction, "create_owned_projection", None)), "creation-bound projection API is missing")
        with tempfile.TemporaryDirectory(prefix="modlab-owned-link-", dir=Path(__file__).resolve().parents[1]) as directory:
            root = Path(directory)
            source = root / "source" / "Protected Existing"
            stage = root / "stage"
            source.mkdir(parents=True)
            stage.mkdir()
            (source / "marker.txt").write_bytes(b"unchanged")
            owner = junction.create_owned_projection(source, stage / "Protected Existing")
            try:
                rows = service._mutation_root_observation(root, expected_projections=(owner,))
                self.assertIsNotNone(rows)
                names = {row[0] for row in rows}
                self.assertIn("stage/Protected Existing", names)
                self.assertNotIn("stage/Protected Existing/marker.txt", names)
                self.assertEqual(b"unchanged", (source / "marker.txt").read_bytes())
            finally:
                owner.close()

    def test_recover_preparation_refuses_absent_storage_without_creating_it(self):
        # Catches physically mutating an absent root while handling recovery refusal.
        self.assertTrue(callable(getattr(service, "recover_preparation", None)), "preparation recovery API is missing")
        with tempfile.TemporaryDirectory(prefix="modlab-preparation-recovery-", dir=Path(__file__).resolve().parents[1]) as directory:
            absent = Path(directory) / "absent-validation"
            with self.assertRaises((service.ContainmentServiceError, ContainmentStoreError)):
                service.recover_preparation(absent, "containment-run:" + "a" * 32)
            self.assertFalse(absent.exists())


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


def fixture_record(root: Path, scenario: ContainmentScenario) -> service.FixtureRecord:
    fixture = root / "fixture"
    return service.FixtureRecord(
        scenario,
        fixture,
        fixture / "source",
        fixture / "stage",
        fixture / "archive.zip",
        fixture / "source" / "mods",
        fixture / "stage" / "mods",
        fixture / "source" / "profiles" / "lab.txt",
        fixture / "source" / "profiles" / "play.txt",
        fixture / "source" / "downloads",
        fixture / "source" / "overwrite",
        fixture / "game",
        fixture / "stage" / "app",
        fixture / "stage" / "downloads",
        fixture / "stage" / "profiles",
        fixture / "stage" / "overwrite",
        fixture / "stage" / "cache",
        fixture / "stage" / "logs",
        {},
        (
            ("ExternalLocalLow", fixture / "external-local-low"),
            ("ExternalTempLow", fixture / "external-temp-low"),
        ),
        ("Protected Existing",),
    )


def prepared_record(
    parent: Path,
    scenario: ContainmentScenario,
    steam_root: Path,
) -> service.FixtureRecord:
    return service.FixtureRecord(
        scenario,
        parent,
        parent / "source",
        parent / "stage",
        parent / "archive.zip",
        parent / "source" / "mods",
        parent / "stage" / "mods",
        parent / "source" / "lab.txt",
        parent / "source" / "play.txt",
        parent / "source" / "downloads",
        parent / "source" / "overwrite",
        steam_root / "game",
        parent / "stage" / "app",
        parent / "stage" / "downloads",
        parent / "stage" / "profiles",
        parent / "stage" / "overwrite",
        parent / "stage" / "cache",
        parent / "stage" / "logs",
        {
            name: str(parent / "env")
            for name in (
                "TEMP", "TMP", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME"
            )
        },
        (
            ("ExternalLocalLow", parent / "local-low"),
            ("ExternalTempLow", parent / "temp-low"),
        ),
        ("Protected Existing",),
    )


def write_cohort_run(
    store: ContainmentStore,
    run_id: str,
    source: Path,
    steam: Path,
    artifact: str,
    *,
    predecessors: tuple[str, ...] = (),
    failed_scenario: ContainmentScenario | None = None,
    retry_binding: dict[str, object] | None = None,
) -> tuple[str, ...]:
    write_run_identity(
        store,
        run_id,
        source,
        steam,
        artifact,
        predecessors=predecessors,
        retry_binding=retry_binding,
    )
    identifiers = []
    for scenario in ContainmentScenario:
        observed = replace(
            watch_outcome(
                scenario,
                events=(event(),) if scenario is failed_scenario else (),
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
        identifiers.append(store.write_result(result).content_id)
    return tuple(identifiers)


def write_run_identity(
    store: ContainmentStore,
    run_id: str,
    source: Path,
    steam: Path,
    artifact: str,
    *,
    predecessors: tuple[str, ...] = (),
    retry_binding: dict[str, object] | None = None,
    command_fingerprint: str | None = None,
) -> tuple[service.FixtureRecord, ...]:
    fingerprint = command_fingerprint or service._command_fingerprint(
        source, artifact, steam
    )
    intent = {
        "schemaVersion": 1,
        "runId": run_id,
        "mechanism": "isolated-low-integrity-junction-projection-v1",
        "sourceWorkspace": str(source.absolute()),
        "mo2ArtifactId": artifact,
        "steamRoot": str(steam.absolute()),
        "commandFingerprint": fingerprint,
        "predecessorRunIds": list(predecessors),
        "retryOf": retry_binding,
    }
    intent_write = store.write_intent(run_id, intent)
    records = tuple(
        prepared_record(
            store.run_path(run_id) / "fixtures" / scenario.value,
            scenario,
            steam,
        )
        for scenario in ContainmentScenario
    )
    store.write_request(
        run_id,
        {
            **intent,
            "intentId": intent_write.content_id,
            "scenarios": [service._record_document(record) for record in records],
        },
    )
    return records


def write_bound_watch_evidence(
    store: ContainmentStore,
    journal: ScenarioJournal,
    *,
    events: tuple[WatcherEvent, ...] = (),
    completion: WatchEvidenceCompletion = WatchEvidenceCompletion.COMPLETED,
) -> WatchOutcome:
    evidence_root = store.watch_path(journal.run_id, journal.scenario)
    watched = evidence_root.parent
    request = WatchRequest(
        "watch-request:" + "6" * 64,
        "watch-session:" + "7" * 64,
        journal.run_id,
        journal.scenario,
        evidence_root,
        evidence_root / "stop.token",
        tuple(
            WatchRoot(kind, watched, 71, 72)
            for kind in WATCH_ROOT_KINDS
        ),
    )
    request_path = evidence_root / "request.json"
    request_path.write_bytes(watch_request_to_bytes(request))
    claim = ControllerClaim(
        1,
        watch_request_sha256(request),
        request.session_id,
        request.run_id,
        request.scenario,
        request_path,
        watch_worker_command(request_path),
        41,
        1001,
    )
    (evidence_root / CLAIM_NAME).write_bytes(
        controller_claim_to_bytes(claim, request)
    )
    launch = WorkerLaunch(
        1,
        watch_request_sha256(request),
        request.session_id,
        request.run_id,
        request.scenario,
        journal.monitor_pid or 42,
        1002,
    )
    (evidence_root / LAUNCH_NAME).write_bytes(
        worker_launch_to_bytes(launch, request)
    )
    observed = replace(
        watch_outcome(
            journal.scenario,
            events=events,
            completion=completion,
        ),
        request_id=request.request_id,
        request_sha256=watch_request_sha256(request),
        session_id=request.session_id,
        run_id=request.run_id,
        scenario=request.scenario,
        controller_pid=claim.controller_pid,
        controller_creation_time=claim.controller_creation_time,
        worker_pid=launch.worker_pid,
        worker_creation_time=launch.worker_creation_time,
    )
    store.write_watch_outcome(observed)
    return observed


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
        ("meshes/new-folder.bin", "meta.ini")
        if scenario is ContainmentScenario.NEW_FOLDER
        else (
            ("meshes/canary.bin", "meshes/new.bin", "meta.ini")
            if scenario is ContainmentScenario.REPLACE_EXISTING
            else ("always.txt", "dependency-seen.txt", "meta.ini")
        )
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
        projection_observation_complete=True,
        projection_payload_bytes_copied=0,
        production_backup_names=(),
        production_observation_complete=True,
        staging_new_names=(expected_name,) if adopted else (),
        staging_observation_complete=True,
        staging_output_names=(
            expected_outputs
            if adopted or scenario is ContainmentScenario.REPLACE_EXISTING
            else ()
        ),
        output_observation_complete=True,
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
    def test_historical_result_load_returns_all_changes_without_write_effects(self):
        with tempfile.TemporaryDirectory(prefix="modlab-result-effects-") as directory:
            store = ContainmentStore(Path(directory))
            scenario = ContainmentScenario.MERGE_EXISTING
            observed = watch_outcome(
                scenario,
                events=(
                    event("SourceMods"),
                    WatcherEvent(2, "BoundedGame", "Modified", "Skyrim.esm"),
                ),
            )
            evaluated = evaluate_scenario(
                evidence(
                    scenario,
                    watch_outcome=observed,
                    production_backup_names=("Protected Existing_backup",),
                )
            )
            store.write_watch_outcome(observed)
            store.write_result(evaluated)
            before = tuple(sorted(path.relative_to(store.root) for path in store.root.rglob("*")))

            loaded = service.load_result(store.root, RUN_ID, scenario)

            after = tuple(sorted(path.relative_to(store.root) for path in store.root.rglob("*")))
            self.assertEqual(evaluated, loaded.value)
            self.assertEqual(before, after)
            self.assertEqual((), loaded.effects.written_paths)
            self.assertEqual((), loaded.effects.child_mutation_roots)
            self.assertIsNone(loaded.effects.watcher_pid)
            self.assertIsNone(loaded.effects.mo2_pid)
            self.assertEqual(
                ("SourceMods: Modified marker.txt",),
                loaded.effects.source_changes,
            )
            self.assertEqual(
                ("BoundedGame: Modified Skyrim.esm",),
                loaded.effects.game_changes,
            )
            self.assertEqual(
                ("Observed production backup: Protected Existing_backup",),
                loaded.effects.production_mo2_changes,
            )

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
            source_rows = (
                (".", "directory", 7, 1, 0, 0, 0, 0, 0, None),
                ("Protected Existing", "directory", 7, 2, 0, 0, 0, 0, 0, None),
            )
            stage_rows = (
                (".", "directory", 7, 3, 0, 0, 0, 0, 0, None),
                ("Protected Existing", "directory", 7, 4, 0, 0, 0, 0, 0, None),
            )
            quarantine = root / "quarantine"
            with (
                patch.object(
                    service,
                    "_mutation_root_observation",
                    side_effect=(source_rows, stage_rows),
                ),
                patch.object(service, "_relocation_destination_volume", return_value=7),
                patch.object(
                    service,
                    "_direct_names",
                    side_effect=AssertionError("must consume canonical relocation rows"),
                ),
            ):
                admission = service._preflight_projection_relocation(
                    record,
                    quarantine,
                )
            self.assertEqual(("Protected Existing",), admission.changed_names)
            self.assertEqual(("Protected Existing",), admission.mutation_names)
            self.assertEqual(
                (
                    (
                        "Protected Existing",
                        (str(quarantine / "Protected Existing"),),
                    ),
                ),
                admission.destination_map,
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
            def adopt_for_test(**_kwargs):
                destination.mkdir()
                return adoption
            with (
                patch.object(service, "_projection_state", return_value=(1, True, True)),
                patch.object(service, "adopt_unique_staged_mod", side_effect=adopt_for_test),
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
            self.assertFalse(result.output_observation_complete)
            evaluated = evaluate_scenario(
                evidence(
                    record.scenario,
                    projection_count=result.projection_count,
                    projection_targets_verified=result.projection_targets_verified,
                    projection_observation_complete=result.projection_observation_complete,
                    production_backup_names=result.production_backup_names,
                    staging_new_names=result.staging_new_names,
                    staging_observation_complete=result.staging_observation_complete,
                    staging_output_names=result.staging_output_names,
                    output_observation_complete=result.output_observation_complete,
                    adopted_name=result.adopted_name,
                    adopted_tree=result.adopted_tree,
                    adopted_integrity=result.adopted_integrity,
                    source_restored_after_quarantine=result.source_restored_after_quarantine,
                    safety_reasons=result.safety_reasons,
                    incomplete_reasons=result.incomplete_reasons,
                )
            )
            self.assertEqual(ScenarioOutcome.INCOMPLETE, evaluated.outcome)

    def test_projection_producer_distinguishes_wrong_from_unobservable(self):
        cases = (
            ("wrong", SimpleNamespace(target_path=Path("C:/wrong")), ScenarioOutcome.FAILED),
            ("unobservable", OSError("access denied"), ScenarioOutcome.INCOMPLETE),
        )
        for label, inspection, expected in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory(
                prefix=f"modlab-projection-{label}-"
            ) as directory:
                root = Path(directory)
                source = root / "source"
                stage = root / "stage"
                source.mkdir()
                stage.mkdir()
                (source / "Protected Existing").mkdir()
                (stage / "Protected Existing").mkdir()
                record = service.FixtureRecord(
                    ContainmentScenario.MERGE_EXISTING,
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
                store = ContainmentStore(root / "validation")
                store.prepare_run_root(RUN_ID)
                effect = inspection if isinstance(inspection, BaseException) else None
                with (
                    patch.object(
                        service,
                        "inspect_junction",
                        side_effect=effect,
                        return_value=None if effect else inspection,
                    ),
                    patch.object(service, "_capture_protected", return_value=protected()),
                ):
                    projection = service._finalize_projection(
                        store,
                        RUN_ID,
                        record,
                        protected(),
                        protected(),
                    )
                evaluated = evaluate_scenario(
                    evidence(
                        record.scenario,
                        projection_count=projection.projection_count,
                        projection_targets_verified=projection.projection_targets_verified,
                        projection_observation_complete=projection.projection_observation_complete,
                        production_backup_names=projection.production_backup_names,
                        staging_new_names=projection.staging_new_names,
                        staging_observation_complete=projection.staging_observation_complete,
                        staging_output_names=projection.staging_output_names,
                        output_observation_complete=projection.output_observation_complete,
                        adopted_name=projection.adopted_name,
                        adopted_tree=projection.adopted_tree,
                        adopted_integrity=projection.adopted_integrity,
                        source_restored_after_quarantine=projection.source_restored_after_quarantine,
                        safety_reasons=projection.safety_reasons,
                        incomplete_reasons=projection.incomplete_reasons,
                    )
                )
                self.assertEqual(expected, evaluated.outcome)
                self.assertEqual(
                    label == "wrong",
                    projection.projection_observation_complete,
                )

    def test_projection_relocation_reobservation_access_failure_refuses_before_quarantine(self):
        with tempfile.TemporaryDirectory(
            prefix="modlab-projection-reobserve-"
        ) as directory:
            root = Path(directory)
            source = root / "source"
            stage = root / "stage"
            source.mkdir()
            stage.mkdir()
            (source / "Protected Existing").mkdir()
            (stage / "Protected Existing").mkdir()
            record = service.FixtureRecord(
                ContainmentScenario.MERGE_EXISTING,
                root, root, root, root / "archive.zip", source, stage,
                root / "lab.txt", root / "play.txt", root, root, root,
                root, root, root, root, root, root, {}, (),
                ("Protected Existing",),
            )
            store = ContainmentStore(root / "validation")
            store.prepare_run_root(RUN_ID)
            source_rows = (
                (".", "directory", 7, 1, 0, 0, 0, 0, 0, None),
                ("Protected Existing", "directory", 7, 2, 0, 0, 0, 0, 0, None),
            )
            stage_rows = (
                (".", "directory", 7, 3, 0, 0, 0, 0, 0, None),
                ("Protected Existing", "directory", 7, 4, 0, 0, 0, 0, 0, None),
            )
            source_before = tuple(path.name for path in source.iterdir())
            stage_before = tuple(path.name for path in stage.iterdir())
            quarantine = store.quarantine_path(RUN_ID) / record.scenario.value
            with (
                patch.object(
                    service,
                    "_mutation_root_observation",
                    side_effect=(
                        source_rows,
                        stage_rows,
                        source_rows,
                        None,
                    ),
                ),
                patch.object(service, "_relocation_destination_volume", return_value=7),
                self.assertRaisesRegex(
                    service.ContainmentServiceError,
                    "relocation tree observation is unavailable",
                ),
            ):
                service._finalize_projection(
                    store, RUN_ID, record, protected(), protected()
                )
            self.assertFalse(quarantine.exists())
            self.assertEqual(source_before, tuple(path.name for path in source.iterdir()))
            self.assertEqual(stage_before, tuple(path.name for path in stage.iterdir()))

    def test_replace_quarantines_disposable_replacement_and_restores_projection(self):
        with tempfile.TemporaryDirectory(prefix="modlab-replace-projection-") as directory:
            root = Path(directory)
            source = root / "source"
            stage = root / "stage"
            protected_mod = source / "Protected Existing"
            replacement = stage / "Protected Existing"
            protected_mod.mkdir(parents=True)
            (protected_mod / "marker.txt").write_bytes(b"protected\n")
            (replacement / "meshes").mkdir(parents=True)
            (replacement / "meshes" / "canary.bin").write_bytes(b"replacement\n")
            (replacement / "meshes" / "new.bin").write_bytes(b"new\n")
            (replacement / "meta.ini").write_bytes(b"[General]\n")
            record = service.FixtureRecord(
                ContainmentScenario.REPLACE_EXISTING,
                root, root, root, root / "archive.zip", source, stage,
                root / "lab.txt", root / "play.txt", root, root, root,
                root, root, root, root, root, root, {}, (),
                ("Protected Existing",),
            )
            store = ContainmentStore(root / "validation")
            store.prepare_run_root(RUN_ID)
            restored = False

            def inspect_for_test(_path):
                if not restored:
                    raise service.ContainmentSafetyError("direct replacement")
                return SimpleNamespace(target_path=protected_mod)

            def restore_for_test(source_mod, staging_mod):
                nonlocal restored
                self.assertEqual(protected_mod, source_mod)
                self.assertEqual(replacement, staging_mod)
                staging_mod.mkdir()
                restored = True
                return SimpleNamespace(target_path=source_mod)

            with (
                patch.object(service, "inspect_junction", side_effect=inspect_for_test),
                patch.object(
                    service,
                    "create_mod_projection",
                    side_effect=restore_for_test,
                ),
                patch.object(service, "_capture_protected", return_value=protected()),
            ):
                projection = service._finalize_projection(
                    store,
                    RUN_ID,
                    record,
                    protected(),
                    protected(),
                )

            quarantine = store.quarantine_path(RUN_ID) / record.scenario.value
            self.assertTrue(restored)
            self.assertTrue((quarantine / "Protected Existing" / "meshes" / "new.bin").is_file())
            self.assertTrue(replacement.is_dir())
            self.assertEqual(1, projection.projection_count)
            self.assertTrue(projection.projection_targets_verified)
            self.assertTrue(projection.projection_observation_complete)
            self.assertEqual(
                ("meshes/canary.bin", "meshes/new.bin", "meta.ini"),
                projection.staging_output_names,
            )
            self.assertTrue(projection.output_observation_complete)
            self.assertEqual((), projection.safety_reasons)
            self.assertEqual((), projection.incomplete_reasons)
            evaluated = evaluate_scenario(
                evidence(
                    record.scenario,
                    projection_count=projection.projection_count,
                    projection_targets_verified=projection.projection_targets_verified,
                    projection_observation_complete=projection.projection_observation_complete,
                    production_backup_names=projection.production_backup_names,
                    staging_new_names=projection.staging_new_names,
                    staging_observation_complete=projection.staging_observation_complete,
                    staging_output_names=projection.staging_output_names,
                    output_observation_complete=projection.output_observation_complete,
                    source_restored_after_quarantine=projection.source_restored_after_quarantine,
                    safety_reasons=projection.safety_reasons,
                    incomplete_reasons=projection.incomplete_reasons,
                )
            )
            self.assertEqual(
                ScenarioOutcome.PASSED,
                evaluated.outcome,
                evaluated.reasons,
            )

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
                patch.object(service, "_projection_state", return_value=(1, True, True)),
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
                self.assertEqual(
                    (1, True, True),
                    service._projection_state(record, allow_new=True),
                )

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

    def test_adoption_accepts_exact_payload_plus_mo2_generated_metadata(self):
        # Catches normal MO2 meta.ini creation being treated as a containment breach.
        for scenario in (
            ContainmentScenario.NEW_FOLDER,
            ContainmentScenario.FOMOD_DEPENDENCY,
        ):
            with self.subTest(scenario=scenario):
                result = evaluate_scenario(evidence(scenario))
                self.assertEqual(ScenarioOutcome.PASSED, result.outcome)

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

        replace_mismatch = evaluate_scenario(
            evidence(
                ContainmentScenario.REPLACE_EXISTING,
                staging_output_names=("meshes/canary.bin", "meta.ini"),
            )
        )
        self.assertEqual(ScenarioOutcome.FAILED, replace_mismatch.outcome)
        self.assertIn("staging-output-set-invalid", replace_mismatch.reasons)

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

    def test_multiple_positive_retained_projections_are_not_a_count_violation(self):
        result = evaluate_scenario(
            evidence(
                ContainmentScenario.MERGE_EXISTING,
                projection_count=2,
            )
        )
        self.assertEqual(ScenarioOutcome.PASSED, result.outcome)

    def test_prepare_run_generates_identity_before_confined_fixture_creation(self):
        with tempfile.TemporaryDirectory(prefix="modlab-prepare-run-") as directory:
            source = Path(directory) / "source"
            layout = initialize_workspace(source)
            validation = layout.mo2_containment_validation
            steam = Path(directory) / "steam"
            calls = []
            observed_intents = []

            def fake_prepare(_source, _artifact, _steam, _validation, scenario, *, fixture_parent=None, **_kwargs):
                parent = Path(fixture_parent)
                self.assertTrue(parent.is_dir())
                calls.append((scenario, parent))
                run_id = "containment-run:" + parent.parents[2].name
                observed_intents.append(
                    ContainmentStore(validation).load_intent(run_id)
                )
                return SimpleNamespace(scenario=scenario, fixture_parent=parent)

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
                patch.object(service, "preflight_containment_fixture"),
                patch.object(service, "prepare_containment_fixture", side_effect=fake_prepare),
                patch.object(service, "_fixture_record", side_effect=fake_record),
            ):
                prepared = service.prepare_run(
                    source, "artifact:abc", steam, validation
                )

            self.assertTrue(hasattr(prepared, "effects"))
            run_id = prepared.value

            run_root = validation / run_id.removeprefix("containment-run:")
            scenario_fixture_roots = tuple(
                run_root / "fixtures" / "v2" / item.value
                for item in ContainmentScenario
            )
            fixture_roots = (run_root / "preparation-projections", run_root / "fixtures", run_root / "fixtures/v2", *scenario_fixture_roots)
            self.assertEqual(fixture_roots, prepared.effects.child_mutation_roots)
            self.assertEqual(
                (
                    run_root / "intent.json",
                    run_root / "preparation-attempt.json",
                    *fixture_roots,
                    run_root / "request.json",
                ),
                prepared.effects.written_paths,
            )
            self.assertIsNone(prepared.effects.watcher_pid)
            self.assertIsNone(prepared.effects.mo2_pid)
            self.assertEqual((), prepared.effects.source_changes)
            self.assertEqual((), prepared.effects.game_changes)
            self.assertEqual((), prepared.effects.production_mo2_changes)
            self.assertEqual(tuple(ContainmentScenario), tuple(item[0] for item in calls))
            self.assertTrue(all(parent.is_relative_to(run_root) for _, parent in calls))
            self.assertEqual(
                scenario_fixture_roots,
                tuple(parent for _, parent in calls),
            )
            request = ContainmentStore(validation).load_request(run_id)
            intent_write = ContainmentStore(validation).load_intent(run_id)
            self.assertEqual(4, len(observed_intents))
            self.assertTrue(all(item == intent_write for item in observed_intents))
            self.assertRegex(request["commandFingerprint"], r"^containment-command-sha256:[0-9a-f]{64}$")
            self.assertRegex(request["intentId"], r"^containment-intent-sha256:[0-9a-f]{64}$")
            self.assertEqual([], request["predecessorRunIds"])
            self.assertIsNone(request["retryOf"])
            self.assertEqual(
                tuple(ContainmentScenario),
                tuple(
                    service._load_fixture_record(
                        ContainmentStore(validation), run_id, scenario
                    ).scenario
                    for scenario in ContainmentScenario
                ),
            )

    def test_prepare_pre_mutation_refusal_does_not_claim_fixture_root(self):
        with tempfile.TemporaryDirectory(prefix="modlab-prepare-refusal-effects-") as directory:
            source = Path(directory) / "source"
            layout = initialize_workspace(source)
            steam = Path(directory) / "steam"
            fixture_root = (
                layout.mo2_containment_validation
                / ("d" * 32)
                / "fixtures"
                / "v2"
                / ContainmentScenario.NEW_FOLDER.value
            )

            def refuse_before_mutation(*_args, fixture_parent=None, **_kwargs):
                self.assertEqual(fixture_root, Path(fixture_parent))
                self.assertFalse(fixture_root.exists())
                raise RuntimeError("fixture rejected before mutation")

            with (
                patch.object(
                    service.uuid,
                    "uuid4",
                    return_value=SimpleNamespace(hex="d" * 32),
                ),
                patch.object(
                    service,
                    "preflight_containment_fixture",
                    side_effect=refuse_before_mutation,
                ),
                patch.object(
                    service,
                    "prepare_containment_fixture",
                    side_effect=AssertionError("materialization must not begin"),
                ),
                self.assertRaises(service.ContainmentOperationError) as raised,
            ):
                service.prepare_run(
                    source,
                    "artifact:pre-mutation-refusal",
                    steam,
                    layout.mo2_containment_validation,
                )

            self.assertFalse(fixture_root.exists())
            self.assertEqual((), raised.exception.effects.child_mutation_roots)
            self.assertEqual(
                (),
                raised.exception.effects.written_paths,
            )

    def test_prepare_partial_fixture_failure_reports_only_entered_root(self):
        with tempfile.TemporaryDirectory(prefix="modlab-prepare-partial-effects-") as directory:
            source = Path(directory) / "source"
            layout = initialize_workspace(source)
            steam = Path(directory) / "steam"
            fixture_root = (
                layout.mo2_containment_validation
                / ("e" * 32)
                / "fixtures"
                / "v2"
                / ContainmentScenario.NEW_FOLDER.value
            )

            def fail_after_mutation(*_args, fixture_parent=None, **_kwargs):
                entered = Path(fixture_parent)
                self.assertTrue(entered.is_dir())
                (entered / "partial.marker").write_bytes(b"entered\n")
                raise RuntimeError("fixture failed after mutation")

            with (
                patch.object(
                    service.uuid,
                    "uuid4",
                    return_value=SimpleNamespace(hex="e" * 32),
                ),
                patch.object(service, "preflight_containment_fixture"),
                patch.object(
                    service,
                    "prepare_containment_fixture",
                    side_effect=fail_after_mutation,
                ),
                self.assertRaises(service.ContainmentOperationError) as raised,
            ):
                service.prepare_run(
                    source,
                    "artifact:partial-fixture",
                    steam,
                    layout.mo2_containment_validation,
                )

            self.assertTrue((fixture_root / "partial.marker").is_file())
            self.assertEqual(
                (fixture_root.parents[2] / "preparation-projections", fixture_root.parent.parent, fixture_root.parent, fixture_root),
                raised.exception.effects.child_mutation_roots,
            )
            self.assertEqual(
                (
                    layout.mo2_containment_validation
                    / ("e" * 32)
                    / "intent.json",
                    layout.mo2_containment_validation / ("e" * 32) / "preparation-attempt.json",
                    layout.mo2_containment_validation / ("e" * 32) / "preparation-projections",
                    fixture_root.parent.parent,
                    fixture_root.parent,
                    fixture_root,
                    layout.mo2_containment_validation / ("e" * 32) / "preparation-failure.json",
                ),
                raised.exception.effects.written_paths,
            )

    def test_prepare_run_propagates_uncertain_run_enumeration_before_fixtures(self):
        with tempfile.TemporaryDirectory(
            prefix="modlab-prepare-enumeration-"
        ) as directory:
            source = Path(directory) / "source"
            layout = initialize_workspace(source)
            steam = Path(directory) / "steam"
            (layout.mo2_containment_validation / ("f" * 32)).write_bytes(
                b"run-shaped file impostor\n"
            )

            with (
                patch.object(service, "preflight_containment_fixture"),
                patch.object(service, "prepare_containment_fixture") as fixture,
                patch.object(service, "_fixture_record", return_value=object()),
                patch.object(service, "_record_document", return_value={}),
            ):
                with self.assertRaisesRegex(
                    service.ContainmentServiceError,
                    "enumerate containment runs",
                ):
                    service.prepare_run(
                        source,
                        "artifact:enumeration",
                        steam,
                        layout.mo2_containment_validation,
                    )
            fixture.assert_not_called()

    def test_arm_scenario_public_workflow_persists_before_watcher_ready(self):
        with tempfile.TemporaryDirectory(prefix="modlab-arm-workflow-") as directory:
            root = Path(directory)
            store = ContainmentStore(root / "validation")
            records = write_run_identity(
                store,
                RUN_ID,
                root / "source-workspace",
                root / "steam",
                "artifact:arm-workflow",
            )
            record = records[1]
            observed = []

            def fake_start(request, *, on_created):
                observed.append(store.load_protected_state(RUN_ID, record.scenario, "before"))
                request.evidence_root.mkdir(parents=True, exist_ok=True)
                (request.evidence_root / "request.json").write_bytes(b"started\n")
                on_created(42)
                return 42

            with (
                patch.object(service, "_capture_protected", return_value=protected()),
                patch.object(service, "inspect_path_integrity", side_effect=(service.IntegrityLevel.MEDIUM, service.IntegrityLevel.LOW)),
                patch.object(service, "_projection_state", return_value=(1, True, True)),
                patch.object(service, "_watch_roots", return_value=()),
                patch.object(service, "start_watch", side_effect=fake_start),
            ):
                armed_receipt = service.arm_scenario(
                    store.root, RUN_ID, record.scenario
                )
            armed = armed_receipt.value
            watch_root = store.watch_path(RUN_ID, record.scenario)
            self.assertEqual(ScenarioState.ARMED, armed.state)
            self.assertEqual([protected()], observed)
            self.assertEqual(42, armed_receipt.effects.watcher_pid)
            self.assertIsNone(armed_receipt.effects.mo2_pid)
            self.assertEqual((watch_root,), armed_receipt.effects.child_mutation_roots)
            self.assertEqual(
                (
                    store.scenario_path(RUN_ID, record.scenario) / "before.json",
                    store.journal_path(RUN_ID, record.scenario),
                    watch_root,
                ),
                armed_receipt.effects.written_paths,
            )

    def test_arm_pre_mutation_watch_refusal_does_not_claim_watch_root(self):
        with tempfile.TemporaryDirectory(prefix="modlab-arm-watch-refusal-") as directory:
            root = Path(directory)
            store = ContainmentStore(root / "validation")
            record = write_run_identity(
                store,
                RUN_ID,
                root / "source-workspace",
                root / "steam",
                "artifact:watch-refusal",
            )[0]
            watch_root = store.watch_path(RUN_ID, record.scenario)

            def refuse_before_mutation(request, *, on_created):
                del on_created
                self.assertEqual(watch_root, request.evidence_root)
                self.assertTrue(watch_root.is_dir())
                self.assertEqual((), tuple(watch_root.iterdir()))
                raise RuntimeError("watch request rejected before mutation")

            with (
                patch.object(service, "_capture_protected", return_value=protected()),
                patch.object(
                    service,
                    "inspect_path_integrity",
                    side_effect=(
                        service.IntegrityLevel.MEDIUM,
                        service.IntegrityLevel.LOW,
                    ),
                ),
                patch.object(service, "_projection_state", return_value=(1, True, True)),
                patch.object(service, "_watch_roots", return_value=()),
                patch.object(service, "start_watch", side_effect=refuse_before_mutation),
                self.assertRaises(service.ContainmentOperationError) as raised,
            ):
                service.arm_scenario(store.root, RUN_ID, record.scenario)

            self.assertTrue(watch_root.is_dir())
            self.assertEqual((), tuple(watch_root.iterdir()))
            self.assertIsNone(raised.exception.effects.watcher_pid)
            self.assertEqual((), raised.exception.effects.child_mutation_roots)
            self.assertEqual(
                (
                    store.scenario_path(RUN_ID, record.scenario) / "before.json",
                    store.journal_path(RUN_ID, record.scenario),
                ),
                raised.exception.effects.written_paths,
            )

    def test_arm_fails_closed_when_successful_watch_effects_cannot_be_observed(self):
        with tempfile.TemporaryDirectory(prefix="modlab-arm-watch-observation-") as directory:
            root = Path(directory)
            store = ContainmentStore(root / "validation")
            record = write_run_identity(
                store,
                RUN_ID,
                root / "source-workspace",
                root / "steam",
                "artifact:watch-observation",
            )[0]
            watch_root = store.watch_path(RUN_ID, record.scenario)

            def start_successfully(request, *, on_created):
                (request.evidence_root / "request.json").write_bytes(b"started\n")
                on_created(73)
                return 73

            with (
                patch.object(service, "_capture_protected", return_value=protected()),
                patch.object(
                    service,
                    "inspect_path_integrity",
                    side_effect=(
                        service.IntegrityLevel.MEDIUM,
                        service.IntegrityLevel.LOW,
                    ),
                ),
                patch.object(service, "_projection_state", return_value=(1, True, True)),
                patch.object(service, "_watch_roots", return_value=()),
                patch.object(service, "start_watch", side_effect=start_successfully),
                patch.object(
                    service,
                    "_mutation_root_observation",
                    side_effect=((("before",),), None),
                ),
                self.assertRaises(service.ContainmentOperationError) as raised,
            ):
                service.arm_scenario(store.root, RUN_ID, record.scenario)

            self.assertTrue((watch_root / "request.json").is_file())
            self.assertIn("effect observation is unavailable", str(raised.exception))
            self.assertEqual(73, raised.exception.effects.watcher_pid)
            self.assertNotIn(watch_root, raised.exception.effects.written_paths)

    def test_arm_nests_watch_failure_when_post_effects_cannot_be_observed(self):
        with tempfile.TemporaryDirectory(prefix="modlab-arm-watch-failure-observation-") as directory:
            root = Path(directory)
            store = ContainmentStore(root / "validation")
            record = write_run_identity(
                store,
                RUN_ID,
                root / "source-workspace",
                root / "steam",
                "artifact:watch-failure-observation",
            )[0]
            watch_root = store.watch_path(RUN_ID, record.scenario)

            def fail_after_entering(request, *, on_created):
                del on_created
                (request.evidence_root / "partial.json").write_bytes(b"partial\n")
                raise RuntimeError("watch delegate boom")

            with (
                patch.object(service, "_capture_protected", return_value=protected()),
                patch.object(
                    service,
                    "inspect_path_integrity",
                    side_effect=(
                        service.IntegrityLevel.MEDIUM,
                        service.IntegrityLevel.LOW,
                    ),
                ),
                patch.object(service, "_projection_state", return_value=(1, True, True)),
                patch.object(service, "_watch_roots", return_value=()),
                patch.object(service, "start_watch", side_effect=fail_after_entering),
                patch.object(
                    service,
                    "_mutation_root_observation",
                    side_effect=((("before",),), None),
                ),
                self.assertRaises(service.ContainmentOperationError) as raised,
            ):
                service.arm_scenario(store.root, RUN_ID, record.scenario)

            chain = []
            current = raised.exception
            while current is not None:
                chain.append(str(current))
                current = current.__cause__
            self.assertTrue((watch_root / "partial.json").is_file())
            self.assertTrue(
                any("effect observation is unavailable" in item for item in chain)
            )
            self.assertTrue(any("watch delegate boom" in item for item in chain))
            self.assertNotIn(watch_root, raised.exception.effects.written_paths)

    def test_launch_and_capture_reload_exact_durable_launch_after_restart(self):
        with tempfile.TemporaryDirectory(prefix="modlab-launch-workflow-") as directory:
            root = Path(directory)
            store = ContainmentStore(root / "validation")
            source = root / "source-workspace"
            steam = root / "steam"
            artifact = "artifact:launch-workflow"
            fingerprint = service._command_fingerprint(source, artifact, steam)
            retry_binding = {
                "runId": "containment-run:" + "f" * 32,
                "scenario": ContainmentScenario.MERGE_EXISTING.value,
                "recoveryId": "containment-recovery-sha256:" + "a" * 64,
                "authorityId": "containment-retry-sha256:" + "b" * 64,
                "commandFingerprint": fingerprint,
            }
            records = write_run_identity(
                store,
                RUN_ID,
                source,
                steam,
                artifact,
                retry_binding=retry_binding,
            )
            record = records[1]
            record.source_mods.mkdir(parents=True)
            record.stage_mods.mkdir(parents=True)
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
            def launch_for_test(*_args, on_created, **_kwargs):
                on_created(native_launch.pid)
                return native_launch
            with (
                patch.object(service, "_load_fixture_record", return_value=record),
                patch.object(service, "_watcher_live", return_value=True),
                patch.object(service, "_capture_protected", return_value=protected()),
                patch.object(service, "read_windows_file_version", return_value="2.5.2.0"),
                patch.object(service, "launch_low_integrity_process", side_effect=launch_for_test),
                patch.object(service, "inspect_process_integrity", return_value=service.IntegrityLevel.LOW),
                patch.object(service, "inspect_mo2_processes", return_value=observation),
                patch.object(service, "_reprove_retry_absence") as reprove,
            ):
                launch_receipt = service.launch_scenario(
                    store.root, RUN_ID, record.scenario
                )
            launched = launch_receipt.value
            reprove.assert_called_once()
            self.assertEqual(store.root, reprove.call_args.args[0].root)
            self.assertEqual((RUN_ID, retry_binding), reprove.call_args.args[1:])
            self.assertEqual(ScenarioState.LAUNCHED, launched.state)
            self.assertEqual(51, launch_receipt.effects.mo2_pid)
            self.assertIsNone(launch_receipt.effects.watcher_pid)
            self.assertEqual(
                (
                    store.journal_path(RUN_ID, record.scenario),
                    store.launch_path(RUN_ID, record.scenario),
                ),
                launch_receipt.effects.written_paths,
            )
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

            def fake_stop(_request_path):
                (store.watch_path(RUN_ID, record.scenario) / "terminal.json").write_bytes(
                    b"terminal\n"
                )
                return receipt

            projection = service._ProjectionEvidence(
                protected_after=protected(),
                projection_count=1,
                projection_targets_verified=True,
                projection_observation_complete=True,
                production_backup_names=(),
                production_observation_complete=True,
                staging_new_names=(),
                staging_observation_complete=True,
                staging_output_names=(),
                output_observation_complete=True,
                adopted_name=None,
                adopted_tree=None,
                adopted_integrity=None,
                source_restored_after_quarantine=True,
                safety_reasons=(),
                incomplete_reasons=(),
            )
            with (
                patch.object(service, "_load_fixture_record", return_value=record),
                patch.object(service, "inspect_mo2_processes", return_value=SimpleNamespace(complete=True, relevant=())),
                patch.object(service, "stop_watch", side_effect=fake_stop),
                patch.object(service, "_capture_protected", return_value=protected()),
                patch.object(service, "_finalize_projection", return_value=projection),
                patch.object(service, "_integrity_observation", side_effect=(IntegrityObservation.MEDIUM, IntegrityObservation.LOW)),
                patch.object(service, "_exact_process_absent", return_value=True),
            ):
                capture_receipt = service.capture_scenario(
                    store.root, RUN_ID, record.scenario
                )
            result = capture_receipt.value
            self.assertEqual(51, result.mo2_process.pid)
            self.assertEqual(
                (
                    store.watch_path(RUN_ID, record.scenario),
                    store.quarantine_path(RUN_ID) / record.scenario.value,
                ),
                capture_receipt.effects.child_mutation_roots,
            )
            self.assertEqual(
                (
                    store.watch_path(RUN_ID, record.scenario),
                    store.quarantine_path(RUN_ID) / record.scenario.value,
                    store.scenario_path(RUN_ID, record.scenario) / "after.json",
                    store.result_path(RUN_ID, record.scenario),
                    store.journal_path(RUN_ID, record.scenario),
                ),
                capture_receipt.effects.written_paths,
            )
            self.assertEqual((), capture_receipt.effects.source_changes)
            self.assertEqual((), capture_receipt.effects.game_changes)
            self.assertEqual((), capture_receipt.effects.production_mo2_changes)

    def test_arm_failure_after_watcher_launch_preserves_exact_partial_effects(self):
        with tempfile.TemporaryDirectory(prefix="modlab-arm-effects-failure-") as directory:
            root = Path(directory)
            store = ContainmentStore(root / "validation")
            record = write_run_identity(
                store,
                RUN_ID,
                root / "source-workspace",
                root / "steam",
                "artifact:arm-effect-failure",
            )[0]
            real_transition = ContainmentStore.transition

            def fail_after_armed(self, observed, new_state, **kwargs):
                transitioned = real_transition(
                    self, observed, new_state, **kwargs
                )
                if new_state is ScenarioState.ARMED:
                    raise ContainmentStoreError("injected post-arm failure")
                return transitioned

            def fake_start(request, *, on_created):
                request.evidence_root.mkdir(parents=True, exist_ok=True)
                (request.evidence_root / "request.json").write_bytes(b"started\n")
                on_created(4242)
                return 4242

            with (
                patch.object(service, "_capture_protected", return_value=protected()),
                patch.object(
                    service,
                    "inspect_path_integrity",
                    side_effect=(
                        service.IntegrityLevel.MEDIUM,
                        service.IntegrityLevel.LOW,
                    ),
                ),
                patch.object(service, "_projection_state", return_value=(1, True, True)),
                patch.object(service, "_watch_roots", return_value=()),
                patch.object(service, "start_watch", side_effect=fake_start),
                patch.object(ContainmentStore, "transition", new=fail_after_armed),
                self.assertRaises(service.ContainmentOperationError) as raised,
            ):
                service.arm_scenario(store.root, RUN_ID, record.scenario)

            effects = raised.exception.effects
            self.assertEqual(4242, effects.watcher_pid)
            self.assertEqual(
                (
                    store.scenario_path(RUN_ID, record.scenario) / "before.json",
                    store.journal_path(RUN_ID, record.scenario),
                    store.watch_path(RUN_ID, record.scenario),
                ),
                effects.written_paths,
            )

    def test_launch_failure_after_evidence_write_preserves_mo2_and_paths(self):
        with tempfile.TemporaryDirectory(prefix="modlab-launch-effects-failure-") as directory:
            root = Path(directory)
            store = ContainmentStore(root / "validation")
            record = write_run_identity(
                store,
                RUN_ID,
                root / "source-workspace",
                root / "steam",
                "artifact:launch-effect-failure",
            )[0]
            armed = store.create(
                ScenarioJournal(
                    1,
                    RUN_ID,
                    record.scenario,
                    ScenarioState.ARMED,
                    str(record.source_root),
                    str(record.stage_root),
                    str(record.archive_path),
                    "Protected Existing",
                    "ModLab Spike New",
                    protected(),
                    42,
                    None,
                    None,
                )
            )
            native_launch = SimpleNamespace(
                pid=5151,
                executable=str(record.executable),
                arguments=("--profile", "ModLab - Lab"),
                working_directory=str(record.stage_app),
                integrity=service.IntegrityLevel.LOW,
                creation_time=987654321,
            )
            observation = SimpleNamespace(
                complete=True,
                relevant=(
                    SimpleNamespace(pid=5151, executable_path=record.executable),
                ),
            )
            real_transition = ContainmentStore.transition

            def fail_after_launched(self, observed, new_state, **kwargs):
                transitioned = real_transition(
                    self, observed, new_state, **kwargs
                )
                if new_state is ScenarioState.LAUNCHED:
                    raise ContainmentStoreError("injected post-launch failure")
                return transitioned

            def launch_for_test(*_args, on_created, **_kwargs):
                on_created(native_launch.pid)
                return native_launch

            with (
                patch.object(service, "_load_fixture_record", return_value=record),
                patch.object(service, "_watcher_live", return_value=True),
                patch.object(service, "_capture_protected", return_value=protected()),
                patch.object(service, "read_windows_file_version", return_value="2.5.2.0"),
                patch.object(service, "launch_low_integrity_process", side_effect=launch_for_test),
                patch.object(service, "inspect_process_integrity", return_value=service.IntegrityLevel.LOW),
                patch.object(service, "inspect_mo2_processes", return_value=observation),
                patch.object(ContainmentStore, "transition", new=fail_after_launched),
                self.assertRaises(service.ContainmentOperationError) as raised,
            ):
                service.launch_scenario(store.root, RUN_ID, armed.scenario)

            effects = raised.exception.effects
            self.assertEqual(5151, effects.mo2_pid)
            self.assertEqual(
                (
                    store.journal_path(RUN_ID, record.scenario),
                    store.launch_path(RUN_ID, record.scenario),
                ),
                effects.written_paths,
            )

    def test_capture_failure_after_terminal_transition_preserves_all_effects(self):
        with tempfile.TemporaryDirectory(prefix="modlab-capture-effects-failure-") as directory:
            root = Path(directory)
            store = ContainmentStore(root / "validation")
            record = write_run_identity(
                store,
                RUN_ID,
                root / "source-workspace",
                root / "steam",
                "artifact:capture-effect-failure",
            )[0]
            record.source_mods.mkdir(parents=True)
            record.stage_mods.mkdir(parents=True)
            journal = store.create(
                ScenarioJournal(
                    1,
                    RUN_ID,
                    record.scenario,
                    ScenarioState.LAUNCHED,
                    str(record.source_root),
                    str(record.stage_root),
                    str(record.archive_path),
                    "Protected Existing",
                    "ModLab Spike New",
                    protected(),
                    42,
                    51,
                    None,
                )
            )
            store.write_launch_evidence(
                RUN_ID,
                record.scenario,
                {
                    "schemaVersion": 1,
                    "runId": RUN_ID,
                    "scenario": record.scenario.value,
                    "purpose": "stage-mo2-scenario",
                    "pid": 51,
                    "creationTime": 987654321,
                    "executable": str(record.executable),
                    "executableVersion": "2.5.2.0",
                    "arguments": ["--profile", "ModLab - Lab"],
                    "workingDirectory": str(record.stage_app),
                    "integrity": IntegrityObservation.LOW.value,
                },
            )
            outcome = watch_outcome(record.scenario)
            store.write_watch_outcome(outcome)
            stop_receipt = SimpleNamespace(
                watch_outcome_id=watch_outcome_id_for(outcome),
                run_id=RUN_ID,
                scenario=record.scenario,
                request_id=outcome.request_id,
                session_id=outcome.session_id,
                worker_pid=outcome.worker_pid,
                request_bytes_sha256=outcome.request_sha256,
            )

            def fake_stop(_request_path):
                (store.watch_path(RUN_ID, record.scenario) / "terminal.json").write_bytes(
                    b"terminal\n"
                )
                return stop_receipt

            projection = service._ProjectionEvidence(
                protected(), 1, True, True, (), True, (), True, (), True,
                None, None, None, True, (), (),
            )
            real_transition = ContainmentStore.transition

            def fail_after_captured(self, observed, new_state, **kwargs):
                transitioned = real_transition(
                    self, observed, new_state, **kwargs
                )
                if new_state is ScenarioState.CAPTURED:
                    raise ContainmentStoreError("injected post-capture failure")
                return transitioned

            with (
                patch.object(service, "_load_fixture_record", return_value=record),
                patch.object(
                    service,
                    "inspect_mo2_processes",
                    return_value=SimpleNamespace(complete=True, relevant=()),
                ),
                patch.object(service, "_exact_process_absent", return_value=True),
                patch.object(service, "stop_watch", side_effect=fake_stop),
                patch.object(service, "_capture_protected", return_value=protected()),
                patch.object(service, "_finalize_projection", return_value=projection),
                patch.object(
                    service,
                    "_integrity_observation",
                    side_effect=(
                        IntegrityObservation.MEDIUM,
                        IntegrityObservation.LOW,
                    ),
                ),
                patch.object(ContainmentStore, "transition", new=fail_after_captured),
                self.assertRaises(service.ContainmentOperationError) as raised,
            ):
                service.capture_scenario(store.root, RUN_ID, journal.scenario)

            self.assertEqual(
                (
                    store.watch_path(RUN_ID, record.scenario),
                    store.quarantine_path(RUN_ID) / record.scenario.value,
                    store.scenario_path(RUN_ID, record.scenario) / "after.json",
                    store.result_path(RUN_ID, record.scenario),
                    store.journal_path(RUN_ID, record.scenario),
                ),
                raised.exception.effects.written_paths,
            )

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
            store.write_intent(
                RUN_ID,
                {
                    "schemaVersion": 1,
                    "runId": RUN_ID,
                    "mechanism": "isolated-low-integrity-junction-projection-v1",
                    "sourceWorkspace": str(source.absolute()),
                    "mo2ArtifactId": "artifact:abc",
                    "steamRoot": str(steam.absolute()),
                    "commandFingerprint": fingerprint,
                    "predecessorRunIds": [],
                    "retryOf": None,
                },
            )
            store.write_retry_authority(recovery, written.content_id, fingerprint)

            with (
                patch.object(service, "preflight_containment_fixture"),
                patch.object(
                    service,
                    "prepare_containment_fixture",
                    side_effect=RuntimeError("injected fixture failure"),
                ) as fixture,
            ):
                with self.assertRaisesRegex(RuntimeError, "injected fixture failure"):
                    service.prepare_run(
                        source, "artifact:abc", steam, store.root, retry_of=recovery
                    )
                self.assertEqual(1, fixture.call_count)
                consumed = store.load_retry_authority(RUN_ID, recovery.scenario)
                self.assertTrue(
                    store.run_path(consumed["consumedByRunId"]).is_dir()
                )
                with self.assertRaisesRegex(
                    service.ContainmentServiceError, "consumed|unresolved"
                ):
                    service.prepare_run(
                        source, "artifact:abc", steam, store.root, retry_of=recovery
                    )
                with self.assertRaisesRegex(
                    service.ContainmentServiceError, "nonterminal|unresolved"
                ):
                    service.prepare_run(
                        source, "artifact:abc", steam, store.root
                    )
                self.assertEqual(1, fixture.call_count)

    def test_prepare_run_cannot_bypass_available_retry_by_omitting_ticket(self):
        with tempfile.TemporaryDirectory(prefix="modlab-retry-bypass-") as directory:
            source = Path(directory) / "source"
            layout = initialize_workspace(source)
            steam = Path(directory) / "steam"
            store = ContainmentStore(layout.mo2_containment_validation)
            fingerprint = service._command_fingerprint(source, "artifact:abc", steam)
            intent = {
                "schemaVersion": 1,
                "runId": RUN_ID,
                "mechanism": "isolated-low-integrity-junction-projection-v1",
                "sourceWorkspace": str(source.absolute()),
                "mo2ArtifactId": "artifact:abc",
                "steamRoot": str(steam.absolute()),
                "commandFingerprint": fingerprint,
                "predecessorRunIds": [],
                "retryOf": None,
            }
            intent_write = store.write_intent(RUN_ID, intent)
            store.write_request(
                RUN_ID,
                {
                    "schemaVersion": 1,
                    "runId": RUN_ID,
                    "sourceWorkspace": str(source.absolute()),
                    "mo2ArtifactId": "artifact:abc",
                    "steamRoot": str(steam.absolute()),
                    "commandFingerprint": fingerprint,
                    "predecessorRunIds": [],
                    "retryOf": None,
                    "intentId": intent_write.content_id,
                    "scenarios": [],
                },
            )
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
            recovery_write = store.write_recovery(recovery)
            store.write_retry_authority(
                recovery,
                recovery_write.content_id,
                fingerprint,
            )
            fixture_calls = []

            with (
                patch.object(service, "preflight_containment_fixture"),
                patch.object(
                    service,
                    "prepare_containment_fixture",
                    side_effect=lambda *_args, **kwargs: fixture_calls.append(
                        kwargs["fixture_parent"]
                    )
                    or SimpleNamespace(
                        scenario=_args[-1], fixture_parent=kwargs["fixture_parent"]
                    ),
                ),
                patch.object(
                    service,
                    "_fixture_record",
                    side_effect=lambda fixture, _steam: prepared_record(
                        Path(fixture.fixture_parent), fixture.scenario, steam
                    ),
                ),
            ):
                with self.assertRaisesRegex(
                    service.ContainmentServiceError,
                    "retry|nonterminal|unresolved",
                ):
                    service.prepare_run(
                        source,
                        "artifact:abc",
                        steam,
                        store.root,
                    )

            self.assertEqual([], fixture_calls)

    def test_terminal_incomplete_run_still_requires_its_available_retry_ticket(self):
        with tempfile.TemporaryDirectory(prefix="modlab-terminal-retry-") as directory:
            source = Path(directory) / "source"
            layout = initialize_workspace(source)
            steam = Path(directory) / "steam"
            store = ContainmentStore(layout.mo2_containment_validation)
            scenario = ContainmentScenario.MERGE_EXISTING
            artifact = "artifact:terminal-retry"
            records = write_run_identity(
                store,
                RUN_ID,
                source,
                steam,
                artifact,
            )
            record = records[list(ContainmentScenario).index(scenario)]
            journal = store.create(
                ScenarioJournal(
                    1,
                    RUN_ID,
                    scenario,
                    ScenarioState.ARMED,
                    str(record.source_root),
                    str(record.stage_root),
                    str(record.archive_path),
                    "Protected Existing",
                    "Protected Existing",
                    protected(),
                    42,
                    None,
                    None,
                )
            )
            observed = write_bound_watch_evidence(
                store,
                journal,
                completion=WatchEvidenceCompletion.INCOMPLETE,
            )
            result_write = store.write_result(
                evaluate_scenario(
                    replace(
                        evidence(scenario),
                        run_id=RUN_ID,
                        watch_outcome=observed,
                        scenario_started=False,
                        fresh_retry_eligible=True,
                    )
                )
            )
            recovery = ScenarioRecovery(
                1,
                RUN_ID,
                scenario,
                store.journal_id_for(journal),
                result_write.content_id,
                ScenarioCleanupStatus.SUCCEEDED,
                True,
                (),
            )
            recovery_write = store.write_recovery(recovery)
            store.write_retry_authority(
                recovery,
                recovery_write.content_id,
                service._command_fingerprint(source, artifact, steam),
            )
            with self.assertRaises(service.ContainmentDecisionNotReady):
                service.adjudicate_run(store.root, RUN_ID)
            self.assertFalse(store.decision_path(RUN_ID).exists())
            fixture_calls = []
            with (
                patch.object(service, "preflight_containment_fixture"),
                patch.object(
                    service,
                    "prepare_containment_fixture",
                    side_effect=lambda *_args, **kwargs: fixture_calls.append(
                        kwargs["fixture_parent"]
                    )
                    or SimpleNamespace(
                        scenario=_args[-1], fixture_parent=kwargs["fixture_parent"]
                    ),
                ),
                patch.object(
                    service,
                    "_fixture_record",
                    side_effect=lambda fixture, _steam: prepared_record(
                        Path(fixture.fixture_parent), fixture.scenario, steam
                    ),
                ),
            ):
                with self.assertRaisesRegex(
                    service.ContainmentServiceError,
                    "retry|authority",
                ):
                    service.prepare_run(
                        source,
                        artifact,
                        steam,
                        store.root,
                    )
                new_run = service.prepare_run(
                    source,
                    artifact,
                    steam,
                    store.root,
                    retry_of=recovery,
                ).value

            self.assertEqual(4, len(fixture_calls))
            self.assertEqual(RUN_ID, store.load_request(new_run)["retryOf"]["runId"])

    def test_terminal_run_permits_explicit_manual_same_command_run(self):
        with tempfile.TemporaryDirectory(prefix="modlab-terminal-manual-") as directory:
            source = Path(directory) / "source"
            layout = initialize_workspace(source)
            steam = Path(directory) / "steam"
            store = ContainmentStore(layout.mo2_containment_validation)
            artifact = "artifact:terminal-manual"
            write_cohort_run(store, RUN_ID, source, steam, artifact)
            self.assertEqual(
                CapabilityVerdict.SUPPORTED,
                service.adjudicate_run(store.root, RUN_ID).verdict,
            )
            with (
                patch.object(service, "preflight_containment_fixture"),
                patch.object(
                    service,
                    "prepare_containment_fixture",
                    side_effect=lambda *_args, **kwargs: SimpleNamespace(
                        scenario=_args[-1], fixture_parent=kwargs["fixture_parent"]
                    ),
                ),
                patch.object(
                    service,
                    "_fixture_record",
                    side_effect=lambda fixture, _steam: prepared_record(
                        Path(fixture.fixture_parent), fixture.scenario, steam
                    ),
                ),
            ):
                new_run = service.prepare_run(
                    source,
                    artifact,
                    steam,
                    store.root,
                ).value

            request = store.load_request(new_run)
            self.assertEqual([RUN_ID], request["predecessorRunIds"])
            self.assertIsNone(request["retryOf"])

    def test_legacy_nonterminal_classifier_run_does_not_block_current_policy(self):
        # Catches a corrected classification policy inheriting a false v1 failure.
        with tempfile.TemporaryDirectory(prefix="modlab-policy-lineage-") as directory:
            source = Path(directory) / "source"
            layout = initialize_workspace(source)
            steam = Path(directory) / "steam"
            store = ContainmentStore(layout.mo2_containment_validation)
            artifact = "artifact:policy-lineage"
            legacy_document = {
                "mechanism": "isolated-low-integrity-junction-projection-v1",
                "mo2ArtifactId": artifact,
                "scenarios": [item.value for item in ContainmentScenario],
                "sourceWorkspace": os.path.normcase(
                    os.path.normpath(str(source.absolute()))
                ),
                "steamRoot": os.path.normcase(
                    os.path.normpath(str(steam.absolute()))
                ),
            }
            legacy_fingerprint = (
                "containment-command-sha256:"
                + hashlib.sha256(
                    (
                        json.dumps(
                            legacy_document,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n"
                    ).encode()
                ).hexdigest()
            )
            store.write_intent(
                RUN_ID,
                {
                    "schemaVersion": 1,
                    "runId": RUN_ID,
                    "mechanism": legacy_document["mechanism"],
                    "sourceWorkspace": legacy_document["sourceWorkspace"],
                    "mo2ArtifactId": artifact,
                    "steamRoot": legacy_document["steamRoot"],
                    "commandFingerprint": legacy_fingerprint,
                    "predecessorRunIds": [],
                    "retryOf": None,
                },
            )

            with (
                patch.object(service, "preflight_containment_fixture"),
                patch.object(
                    service,
                    "prepare_containment_fixture",
                    side_effect=lambda *_args, **kwargs: SimpleNamespace(
                        scenario=_args[-1], fixture_parent=kwargs["fixture_parent"]
                    ),
                ),
                patch.object(
                    service,
                    "_fixture_record",
                    side_effect=lambda fixture, _steam: prepared_record(
                        Path(fixture.fixture_parent), fixture.scenario, steam
                    ),
                ),
            ):
                new_run = service.prepare_run(
                    source, artifact, steam, store.root
                ).value

            current = store.load_intent(new_run)
            self.assertEqual([], current["predecessorRunIds"])
            self.assertNotEqual(legacy_fingerprint, current["commandFingerprint"])

    def test_pre_v4_nonterminal_runs_do_not_block_current_classification_policy(self):
        # Corrected replacement policy must not inherit v2 or v3 diagnostics.
        with tempfile.TemporaryDirectory(prefix="modlab-fixture-lineage-") as directory:
            source = Path(directory) / "source"
            layout = initialize_workspace(source)
            steam = Path(directory) / "steam"
            store = ContainmentStore(layout.mo2_containment_validation)
            artifact = "artifact:fixture-lineage"
            previous_document = {
                "classificationPolicy": "scenario-classification-v2",
                "mechanism": "isolated-low-integrity-junction-projection-v1",
                "mo2ArtifactId": artifact,
                "scenarios": [item.value for item in ContainmentScenario],
                "sourceWorkspace": os.path.normcase(
                    os.path.normpath(str(source.absolute()))
                ),
                "steamRoot": os.path.normcase(
                    os.path.normpath(str(steam.absolute()))
                ),
            }
            previous_fingerprint = (
                "containment-command-sha256:"
                + hashlib.sha256(
                    (
                        json.dumps(
                            previous_document,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n"
                    ).encode()
                ).hexdigest()
            )
            v2_fixture_document = {
                **previous_document,
                "fixturePolicy": "disposable-shell-environment-v2",
            }
            v2_fixture_fingerprint = (
                "containment-command-sha256:"
                + hashlib.sha256(
                    (
                        json.dumps(
                            v2_fixture_document,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n"
                    ).encode()
                ).hexdigest()
            )
            v3_fixture_document = {
                **v2_fixture_document,
                "classificationPolicy": "scenario-classification-v3",
            }
            v3_fixture_fingerprint = (
                "containment-command-sha256:"
                + hashlib.sha256(
                    (
                        json.dumps(
                            v3_fixture_document,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n"
                    ).encode()
                ).hexdigest()
            )
            for old_run, document, fingerprint in (
                (RUN_ID, previous_document, previous_fingerprint),
                (
                    "containment-run:" + "1" * 32,
                    v2_fixture_document,
                    v2_fixture_fingerprint,
                ),
                (
                    "containment-run:" + "2" * 32,
                    v3_fixture_document,
                    v3_fixture_fingerprint,
                ),
            ):
                store.write_intent(
                    old_run,
                    {
                        "schemaVersion": 1,
                        "runId": old_run,
                        "mechanism": document["mechanism"],
                        "sourceWorkspace": document["sourceWorkspace"],
                        "mo2ArtifactId": artifact,
                        "steamRoot": document["steamRoot"],
                        "commandFingerprint": fingerprint,
                        "predecessorRunIds": [],
                        "retryOf": None,
                    },
                )

            with (
                patch.object(service, "preflight_containment_fixture"),
                patch.object(
                    service,
                    "prepare_containment_fixture",
                    side_effect=lambda *_args, **kwargs: SimpleNamespace(
                        scenario=_args[-1], fixture_parent=kwargs["fixture_parent"]
                    ),
                ),
                patch.object(
                    service,
                    "_fixture_record",
                    side_effect=lambda fixture, _steam: prepared_record(
                        Path(fixture.fixture_parent), fixture.scenario, steam
                    ),
                ),
            ):
                new_run = service.prepare_run(
                    source, artifact, steam, store.root
                ).value

            current_document = {
                "classificationPolicy": "scenario-classification-v4",
                "fixturePolicy": "disposable-shell-environment-v3",
                "mechanism": "isolated-low-integrity-junction-projection-v1",
                "mo2ArtifactId": artifact,
                "operatorPolicy": "foreground-interactive-confirmation-v1",
                "scenarios": [item.value for item in ContainmentScenario],
                "sourceWorkspace": previous_document["sourceWorkspace"],
                "steamRoot": previous_document["steamRoot"],
            }
            expected_current = (
                "containment-command-sha256:"
                + hashlib.sha256(
                    (
                        json.dumps(
                            current_document,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n"
                    ).encode()
                ).hexdigest()
            )
            current = store.load_intent(new_run)
            self.assertEqual([], current["predecessorRunIds"])
            self.assertEqual(expected_current, current["commandFingerprint"])
            self.assertNotEqual(previous_fingerprint, current["commandFingerprint"])
            self.assertNotEqual(v2_fixture_fingerprint, current["commandFingerprint"])
            self.assertNotEqual(v3_fixture_fingerprint, current["commandFingerprint"])

    def test_previous_fixture_policy_remains_readable_without_blocking_current_cohort(self):
        # Catches a corrected fixture inheriting an unresolved run from its old protocol.
        with tempfile.TemporaryDirectory(prefix="modlab-fomod-fixture-lineage-") as directory:
            source = Path(directory) / "source"
            layout = initialize_workspace(source)
            steam = Path(directory) / "steam"
            store = ContainmentStore(layout.mo2_containment_validation)
            artifact = "artifact:fomod-fixture-lineage"
            previous_document = {
                "classificationPolicy": "scenario-classification-v4",
                "fixturePolicy": "disposable-shell-environment-v2",
                "mechanism": "isolated-low-integrity-junction-projection-v1",
                "mo2ArtifactId": artifact,
                "scenarios": [item.value for item in ContainmentScenario],
                "sourceWorkspace": os.path.normcase(
                    os.path.normpath(str(source.absolute()))
                ),
                "steamRoot": os.path.normcase(
                    os.path.normpath(str(steam.absolute()))
                ),
            }
            previous_fingerprint = (
                "containment-command-sha256:"
                + hashlib.sha256(
                    (
                        json.dumps(
                            previous_document,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n"
                    ).encode()
                ).hexdigest()
            )
            store.write_intent(
                RUN_ID,
                {
                    "schemaVersion": 1,
                    "runId": RUN_ID,
                    "mechanism": previous_document["mechanism"],
                    "sourceWorkspace": previous_document["sourceWorkspace"],
                    "mo2ArtifactId": artifact,
                    "steamRoot": previous_document["steamRoot"],
                    "commandFingerprint": previous_fingerprint,
                    "predecessorRunIds": [],
                    "retryOf": None,
                },
            )

            with (
                patch.object(service, "preflight_containment_fixture"),
                patch.object(
                    service,
                    "prepare_containment_fixture",
                    side_effect=lambda *_args, **kwargs: SimpleNamespace(
                        scenario=_args[-1], fixture_parent=kwargs["fixture_parent"]
                    ),
                ),
                patch.object(
                    service,
                    "_fixture_record",
                    side_effect=lambda fixture, _steam: prepared_record(
                        Path(fixture.fixture_parent), fixture.scenario, steam
                    ),
                ),
            ):
                new_run = service.prepare_run(
                    source, artifact, steam, store.root
                ).value

            current_document = {
                **previous_document,
                "fixturePolicy": "disposable-shell-environment-v3",
                "operatorPolicy": "foreground-interactive-confirmation-v1",
            }
            expected_current = (
                "containment-command-sha256:"
                + hashlib.sha256(
                    (
                        json.dumps(
                            current_document,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n"
                    ).encode()
                ).hexdigest()
            )
            current = store.load_intent(new_run)
            self.assertEqual([], current["predecessorRunIds"])
            self.assertEqual(expected_current, current["commandFingerprint"])
            self.assertNotEqual(previous_fingerprint, current["commandFingerprint"])

    def test_previous_operator_protocol_remains_readable_without_blocking_current_cohort(self):
        # Catches an interactive controller inheriting an incomplete EOF-only run.
        with tempfile.TemporaryDirectory(prefix="modlab-operator-lineage-") as directory:
            source = Path(directory) / "source"
            layout = initialize_workspace(source)
            steam = Path(directory) / "steam"
            store = ContainmentStore(layout.mo2_containment_validation)
            artifact = "artifact:operator-lineage"
            previous_document = {
                "classificationPolicy": "scenario-classification-v4",
                "fixturePolicy": "disposable-shell-environment-v3",
                "mechanism": "isolated-low-integrity-junction-projection-v1",
                "mo2ArtifactId": artifact,
                "scenarios": [item.value for item in ContainmentScenario],
                "sourceWorkspace": os.path.normcase(
                    os.path.normpath(str(source.absolute()))
                ),
                "steamRoot": os.path.normcase(
                    os.path.normpath(str(steam.absolute()))
                ),
            }
            previous_fingerprint = (
                "containment-command-sha256:"
                + hashlib.sha256(
                    (
                        json.dumps(
                            previous_document,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n"
                    ).encode()
                ).hexdigest()
            )
            store.write_intent(
                RUN_ID,
                {
                    "schemaVersion": 1,
                    "runId": RUN_ID,
                    "mechanism": previous_document["mechanism"],
                    "sourceWorkspace": previous_document["sourceWorkspace"],
                    "mo2ArtifactId": artifact,
                    "steamRoot": previous_document["steamRoot"],
                    "commandFingerprint": previous_fingerprint,
                    "predecessorRunIds": [],
                    "retryOf": None,
                },
            )

            with (
                patch.object(service, "preflight_containment_fixture"),
                patch.object(
                    service,
                    "prepare_containment_fixture",
                    side_effect=lambda *_args, **kwargs: SimpleNamespace(
                        scenario=_args[-1], fixture_parent=kwargs["fixture_parent"]
                    ),
                ),
                patch.object(
                    service,
                    "_fixture_record",
                    side_effect=lambda fixture, _steam: prepared_record(
                        Path(fixture.fixture_parent), fixture.scenario, steam
                    ),
                ),
            ):
                new_run = service.prepare_run(
                    source, artifact, steam, store.root
                ).value

            current_document = {
                **previous_document,
                "operatorPolicy": "foreground-interactive-confirmation-v1",
            }
            expected_current = (
                "containment-command-sha256:"
                + hashlib.sha256(
                    (
                        json.dumps(
                            current_document,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n"
                    ).encode()
                ).hexdigest()
            )
            current = store.load_intent(new_run)
            self.assertEqual([], current["predecessorRunIds"])
            self.assertEqual(expected_current, current["commandFingerprint"])
            self.assertNotEqual(previous_fingerprint, current["commandFingerprint"])

    def test_previous_operator_protocol_refuses_arm_before_any_scenario_write(self):
        # Catches current execution mutating a stage retained as read-only history.
        with tempfile.TemporaryDirectory(prefix="modlab-old-operator-arm-") as directory:
            root = Path(directory)
            source = root / "source"
            steam = root / "steam"
            store = ContainmentStore(root / "validation")
            artifact = "artifact:old-operator-arm"
            fingerprint = service._pre_operator_policy_command_fingerprint(
                source, artifact, steam
            )
            write_run_identity(
                store,
                RUN_ID,
                source,
                steam,
                artifact,
                command_fingerprint=fingerprint,
            )
            scenario = ContainmentScenario.NEW_FOLDER
            self.assertEqual(
                scenario,
                service._load_fixture_record(store, RUN_ID, scenario).scenario,
            )

            with (
                patch.object(
                    service, "_capture_protected", return_value=protected()
                ) as capture,
                patch.object(
                    service,
                    "inspect_path_integrity",
                    side_effect=(
                        service.IntegrityLevel.MEDIUM,
                        service.IntegrityLevel.LOW,
                    ),
                ),
                patch.object(
                    service, "_projection_state", return_value=(1, True, True)
                ),
                patch.object(service, "_watch_roots", return_value=()),
                patch.object(service, "start_watch", return_value=42) as start_watch,
            ):
                with self.assertRaisesRegex(
                    service.ContainmentServiceError,
                    "current command policy",
                ):
                    service.arm_scenario(store.root, RUN_ID, scenario)

            capture.assert_not_called()
            start_watch.assert_not_called()
            self.assertFalse((store.scenario_path(RUN_ID, scenario) / "before.json").exists())
            self.assertFalse(store.journal_path(RUN_ID, scenario).exists())
            self.assertFalse(store.watch_path(RUN_ID, scenario).exists())

    def test_previous_operator_protocol_refuses_launch_before_advancing_journal(self):
        with tempfile.TemporaryDirectory(prefix="modlab-old-operator-launch-") as directory:
            root = Path(directory)
            source = root / "source"
            steam = root / "steam"
            store = ContainmentStore(root / "validation")
            artifact = "artifact:old-operator-launch"
            fingerprint = service._pre_operator_policy_command_fingerprint(
                source, artifact, steam
            )
            records = write_run_identity(
                store,
                RUN_ID,
                source,
                steam,
                artifact,
                command_fingerprint=fingerprint,
            )
            record = records[0]
            journal = store.create(
                ScenarioJournal(
                    1,
                    RUN_ID,
                    record.scenario,
                    ScenarioState.ARMED,
                    str(record.source_root),
                    str(record.stage_root),
                    str(record.archive_path),
                    "Protected Existing",
                    "ModLab Spike New",
                    protected(),
                    42,
                    None,
                    None,
                )
            )

            with patch.object(service, "_watcher_live", return_value=False) as watcher:
                with self.assertRaisesRegex(
                    service.ContainmentServiceError,
                    "current command policy",
                ):
                    service.launch_scenario(store.root, RUN_ID, record.scenario)

            watcher.assert_not_called()
            self.assertEqual(
                journal,
                store.load_journal(RUN_ID, record.scenario),
            )

    def test_previous_operator_protocol_refuses_capture_before_runtime_inspection(self):
        with tempfile.TemporaryDirectory(prefix="modlab-old-operator-capture-") as directory:
            root = Path(directory)
            source = root / "source"
            steam = root / "steam"
            store = ContainmentStore(root / "validation")
            artifact = "artifact:old-operator-capture"
            fingerprint = service._pre_operator_policy_command_fingerprint(
                source, artifact, steam
            )
            records = write_run_identity(
                store,
                RUN_ID,
                source,
                steam,
                artifact,
                command_fingerprint=fingerprint,
            )
            record = records[0]
            journal = store.create(
                ScenarioJournal(
                    1,
                    RUN_ID,
                    record.scenario,
                    ScenarioState.LAUNCHED,
                    str(record.source_root),
                    str(record.stage_root),
                    str(record.archive_path),
                    "Protected Existing",
                    "ModLab Spike New",
                    protected(),
                    42,
                    51,
                    None,
                )
            )

            with patch.object(
                service,
                "inspect_mo2_processes",
                return_value=SimpleNamespace(complete=False, relevant=()),
            ) as inspect_processes:
                with self.assertRaisesRegex(
                    service.ContainmentServiceError,
                    "current command policy",
                ):
                    service.capture_scenario(store.root, RUN_ID, record.scenario)

            inspect_processes.assert_not_called()
            self.assertEqual(
                journal,
                store.load_journal(RUN_ID, record.scenario),
            )

    def test_same_command_prepare_calls_are_serialized_before_snapshot(self):
        with tempfile.TemporaryDirectory(prefix="modlab-prepare-order-") as directory:
            source = Path(directory) / "source"
            layout = initialize_workspace(source)
            steam = Path(directory) / "steam"
            entered_first = threading.Event()
            release_first = threading.Event()
            entered_second_fixture = threading.Event()
            results = []
            errors = []

            def fake_prepare(
                _source,
                _artifact,
                _steam,
                _validation,
                scenario,
                *,
                fixture_parent=None,
                **_kwargs,
            ):
                if threading.current_thread().name == "first-prepare":
                    if not entered_first.is_set():
                        entered_first.set()
                        if not release_first.wait(2):
                            raise AssertionError("first prepare was not released")
                else:
                    entered_second_fixture.set()
                return SimpleNamespace(
                    scenario=scenario,
                    fixture_parent=Path(fixture_parent),
                )

            def run_prepare() -> None:
                try:
                    results.append(
                        service.prepare_run(
                            source,
                            "artifact:abc",
                            steam,
                            layout.mo2_containment_validation,
                        )
                    )
                except BaseException as error:
                    errors.append(error)

            with (
                patch.object(service, "preflight_containment_fixture"),
                patch.object(
                    service,
                    "prepare_containment_fixture",
                    side_effect=fake_prepare,
                ),
                patch.object(
                    service,
                    "_fixture_record",
                    side_effect=lambda fixture, _steam: prepared_record(
                        Path(fixture.fixture_parent), fixture.scenario, steam
                    ),
                ),
            ):
                first = threading.Thread(target=run_prepare, name="first-prepare")
                second = threading.Thread(target=run_prepare, name="second-prepare")
                first.start()
                self.assertTrue(entered_first.wait(1))
                second.start()
                second_reached_fixture_while_first_unresolved = (
                    entered_second_fixture.wait(0.15)
                )
                release_first.set()
                first.join(3)
                second.join(3)

            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertFalse(second_reached_fixture_while_first_unresolved)
            self.assertFalse(entered_second_fixture.is_set())
            self.assertEqual(1, len(results))
            self.assertEqual(1, len(errors))
            self.assertIsInstance(errors[0], service.ContainmentServiceError)
            self.assertRegex(str(errors[0]), "nonterminal|unresolved")

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
            store.write_intent(
                RUN_ID,
                {
                    "schemaVersion": 1,
                    "runId": RUN_ID,
                    "mechanism": "isolated-low-integrity-junction-projection-v1",
                    "sourceWorkspace": str(source.absolute()),
                    "mo2ArtifactId": "artifact:abc",
                    "steamRoot": str(steam.absolute()),
                    "commandFingerprint": fingerprint,
                    "predecessorRunIds": [],
                    "retryOf": None,
                },
            )
            authority = store.write_retry_authority(
                recovery, recovery_write.content_id, fingerprint
            )
            with (
                patch.object(service, "preflight_containment_fixture"),
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
                ).value
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

            historical_ids = write_cohort_run(
                store,
                failed_run,
                source,
                steam,
                "artifact:same",
                failed_scenario=ContainmentScenario.MERGE_EXISTING,
            )
            current_ids = write_cohort_run(
                store,
                current_run,
                source,
                steam,
                "artifact:same",
                predecessors=(failed_run,),
            )
            write_cohort_run(
                store,
                unrelated_run,
                source,
                steam,
                "artifact:other",
                failed_scenario=ContainmentScenario.MERGE_EXISTING,
            )
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
            with self.assertRaises(service.ContainmentDecisionNotReady) as raised:
                service.adjudicate_run(store.root, run_id)
            self.assertFalse(store.decision_path(run_id).exists())
            self.assertEqual(
                service.ContainmentEffects(),
                raised.exception.effects,
            )

    def test_early_adjudication_refuses_without_freezing_decision(self):
        """Missing terminal evidence must not become an immutable authority record."""
        with tempfile.TemporaryDirectory(prefix="modlab-early-adjudication-") as directory:
            store = ContainmentStore(Path(directory))
            run_id = "containment-run:" + "a" * 32
            source = store.root / "source"
            steam = store.root / "steam"
            write_run_identity(store, run_id, source, steam, "artifact:partial")
            scenario = ContainmentScenario.NEW_FOLDER
            observed = replace(watch_outcome(scenario), run_id=run_id)
            result = evaluate_scenario(
                replace(evidence(scenario), run_id=run_id, watch_outcome=observed)
            )
            store.write_watch_outcome(observed)
            store.write_result(result)

            with self.assertRaises(service.ContainmentDecisionNotReady) as raised:
                service.adjudicate_run(store.root, run_id)
            self.assertFalse(store.decision_path(run_id).exists())
            self.assertEqual(service.ContainmentEffects(), raised.exception.effects)

    def test_decision_binds_complete_corrected_authority(self):
        with tempfile.TemporaryDirectory(prefix="modlab-bound-decision-") as directory:
            store = ContainmentStore(Path(directory))
            run_id = "containment-run:" + "b" * 32
            source = store.root / "source"
            steam = store.root / "steam"
            result_ids = write_cohort_run(store, run_id, source, steam, "artifact:bound")

            adjudicated = service.adjudicate_run(store.root, run_id)
            decision = adjudicated.value
            source_root = Path(service.__file__).resolve().parents[2]
            commit = subprocess.run(
                ["git", "-C", str(source_root), "rev-parse", "HEAD"],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            tree_id = subprocess.run(
                ["git", "-C", str(source_root), "rev-parse", f"{commit}^{{tree}}"],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            release = service.load_mo2_release(service.bundled_mo2_252_path())

            self.assertEqual(2, decision.binding_version)
            self.assertEqual(commit, decision.source_commit_id)
            self.assertEqual(tree_id, decision.source_tree_id)
            self.assertEqual(
                service.source_artifact_id_for(
                    {
                        "schemaVersion": 1,
                        "sourceCommitId": commit,
                        "sourceTreeId": tree_id,
                        "mo2ArtifactId": "artifact:bound",
                        "releaseDescriptorSha256": release.sha256,
                        "archiveSha256": release.descriptor.archive_sha256,
                    }
                ),
                decision.source_artifact_id,
            )
            self.assertEqual(2, decision.bindings.protocol_version)
            self.assertEqual(2, decision.bindings.binding_version)
            self.assertEqual("handle-pinned-no-replace-v2", decision.publication_policy_version)
            self.assertEqual(2, decision.bindings.fixture_version)
            self.assertEqual(1, decision.bindings.effect_receipt_version)
            self.assertEqual(1, decision.bindings.authority_policy_version)
            self.assertEqual("2.5.2.0", decision.bindings.mo2_version)
            self.assertEqual(
                release.descriptor.executable.sha256,
                decision.bindings.mo2_executable_sha256,
            )
            self.assertEqual(run_id, decision.run_id)
            self.assertEqual("isolated-low-integrity-junction-projection-v1", decision.mechanism)
            self.assertEqual(result_ids, decision.scenario_result_ids)
            self.assertEqual(
                (store.decision_path(run_id),),
                adjudicated.effects.written_paths,
            )
            self.assertEqual((), adjudicated.effects.child_mutation_roots)

    def test_decision_bindings_use_tree_of_the_observed_commit(self):
        commit = "1" * 40
        tree_for_commit = "2" * 40
        tree_after_head_moves = "3" * 40
        release = service.load_mo2_release(service.bundled_mo2_252_path())

        def git_result(command, **_kwargs):
            revision = command[-1]
            output = {
                "HEAD": commit,
                f"{commit}^{{tree}}": tree_for_commit,
                "HEAD^{tree}": tree_after_head_moves,
            }[revision]
            return subprocess.CompletedProcess(command, 0, stdout=output + "\n")

        with patch.object(service.subprocess, "run", side_effect=git_result) as run:
            bindings = service._decision_bindings({"mo2ArtifactId": "artifact:stable"})

        self.assertEqual(commit, bindings.source_commit_id)
        self.assertEqual(tree_for_commit, bindings.source_tree_id)
        self.assertEqual(
            service.source_artifact_id_for(
                {
                    "schemaVersion": 1,
                    "sourceCommitId": commit,
                    "sourceTreeId": tree_for_commit,
                    "mo2ArtifactId": "artifact:stable",
                    "releaseDescriptorSha256": release.sha256,
                    "archiveSha256": release.descriptor.archive_sha256,
                }
            ),
            bindings.source_artifact_id,
        )
        self.assertEqual(f"{commit}^{{tree}}", run.call_args_list[1].args[0][-1])

    def test_nonterminal_journal_refuses_complete_decision_without_writing(self):
        with tempfile.TemporaryDirectory(prefix="modlab-nonterminal-decision-") as directory:
            store = ContainmentStore(Path(directory))
            run_id = "containment-run:" + "c" * 32
            source = store.root / "source"
            steam = store.root / "steam"
            write_cohort_run(store, run_id, source, steam, "artifact:nonterminal")
            scenario = ContainmentScenario.NEW_FOLDER
            store.create(
                ScenarioJournal(
                    1, run_id, scenario, ScenarioState.PREPARED,
                    str((store.root / "source").absolute()),
                    str((store.root / "stage").absolute()),
                    str((store.root / "archive.zip").absolute()),
                    "Protected Existing", "ModLab Spike New", protected(), None, None, None,
                )
            )

            with self.assertRaises(service.ContainmentDecisionNotReady):
                service.adjudicate_run(store.root, run_id)

            self.assertFalse(store.decision_path(run_id).exists())

    def test_mismatched_result_outcome_refuses_without_writing_decision(self):
        with tempfile.TemporaryDirectory(prefix="modlab-mismatched-decision-") as directory:
            store = ContainmentStore(Path(directory))
            run_id = "containment-run:" + "d" * 32
            source = store.root / "source"
            steam = store.root / "steam"
            write_cohort_run(store, run_id, source, steam, "artifact:mismatched")
            source_outcome = store.watch_path(
                run_id, ContainmentScenario.NEW_FOLDER
            ) / "outcome.json"
            target_outcome = store.watch_path(
                run_id, ContainmentScenario.MERGE_EXISTING
            ) / "outcome.json"
            target_outcome.write_bytes(source_outcome.read_bytes())

            with self.assertRaises(service.ContainmentDecisionNotReady):
                service.adjudicate_run(store.root, run_id)

            self.assertFalse(store.decision_path(run_id).exists())

    def test_adjudicate_run_requires_intact_current_intent_request_binding(self):
        for damage in ("intent", "request"):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory(
                prefix=f"modlab-current-{damage}-damage-"
            ) as directory:
                store = ContainmentStore(Path(directory))
                run_id = "containment-run:" + "6" * 32
                source = store.root / "source"
                steam = store.root / "steam"
                write_cohort_run(store, run_id, source, steam, "artifact:current")
                target = (
                    store.intent_path(run_id)
                    if damage == "intent"
                    else store.request_path(run_id)
                )
                target.write_bytes(b"damaged\n")

                with self.assertRaises(service.ContainmentDecisionNotReady):
                    service.adjudicate_run(store.root, run_id)
                self.assertFalse(store.decision_path(run_id).exists())

    def test_adjudicate_run_requires_every_listed_predecessor_evidence(self):
        damage_cases = ("intent", "request", "result", "outcome")
        for damage in damage_cases:
            with self.subTest(damage=damage), tempfile.TemporaryDirectory(
                prefix=f"modlab-predecessor-{damage}-"
            ) as directory:
                store = ContainmentStore(Path(directory))
                prior = "containment-run:" + "7" * 32
                current = "containment-run:" + "8" * 32
                source = store.root / "source"
                steam = store.root / "steam"
                prior_ids = write_cohort_run(
                    store, prior, source, steam, "artifact:cohort"
                )
                write_cohort_run(
                    store,
                    current,
                    source,
                    steam,
                    "artifact:cohort",
                    predecessors=(prior,),
                )
                damaged_scenario = ContainmentScenario.REPLACE_EXISTING
                if damage == "intent":
                    store.intent_path(prior).write_bytes(b"damaged\n")
                    resolvable_ids = prior_ids
                elif damage == "request":
                    store.request_path(prior).write_bytes(b"damaged\n")
                    resolvable_ids = prior_ids
                elif damage == "result":
                    store.result_path(prior, damaged_scenario).write_bytes(b"damaged\n")
                    resolvable_ids = tuple(
                        identifier
                        for index, identifier in enumerate(prior_ids)
                        if index != list(ContainmentScenario).index(damaged_scenario)
                    )
                else:
                    (
                        store.watch_path(prior, damaged_scenario) / "outcome.json"
                    ).write_bytes(b"damaged\n")
                    resolvable_ids = tuple(
                        identifier
                        for index, identifier in enumerate(prior_ids)
                        if index != list(ContainmentScenario).index(damaged_scenario)
                    )

                decision = service.adjudicate_run(store.root, current)

                self.assertEqual(CapabilityVerdict.INCOMPLETE, decision.verdict)
                self.assertTrue(any(prior in reason for reason in decision.reasons))
                self.assertTrue(
                    all(
                        any(identifier in reason for reason in decision.reasons)
                        for identifier in resolvable_ids
                    )
                )

    def test_adjudicate_run_supersedes_only_exact_consumed_prestart_retry(self):
        with tempfile.TemporaryDirectory(prefix="modlab-retry-cohort-") as directory:
            store = ContainmentStore(Path(directory))
            prior = "containment-run:" + "b" * 32
            current = "containment-run:" + "c" * 32
            scenario = ContainmentScenario.MERGE_EXISTING
            source = store.root / "source"
            steam = store.root / "steam"
            artifact = "artifact:retry-cohort"
            records = write_run_identity(
                store,
                prior,
                source,
                steam,
                artifact,
            )
            record = records[list(ContainmentScenario).index(scenario)]
            journal = store.create(
                ScenarioJournal(
                    1,
                    prior,
                    scenario,
                    ScenarioState.ARMED,
                    str(record.source_root),
                    str(record.stage_root),
                    str(record.archive_path),
                    "Protected Existing",
                    "Protected Existing",
                    protected(),
                    42,
                    None,
                    None,
                )
            )
            observed = write_bound_watch_evidence(
                store,
                journal,
                completion=WatchEvidenceCompletion.INCOMPLETE,
            )
            result = evaluate_scenario(
                replace(
                    evidence(scenario),
                    run_id=prior,
                    watch_outcome=observed,
                    scenario_started=False,
                    fresh_retry_eligible=True,
                )
            )
            result_write = store.write_result(result)
            recovery = ScenarioRecovery(
                1,
                prior,
                scenario,
                store.journal_id_for(journal),
                result_write.content_id,
                ScenarioCleanupStatus.SUCCEEDED,
                True,
                (),
            )
            recovery_write = store.write_recovery(recovery)
            fingerprint = service._command_fingerprint(source, artifact, steam)
            store.write_retry_authority(
                recovery,
                recovery_write.content_id,
                fingerprint,
            )
            retry_binding = store.consume_retry_authority(
                recovery,
                recovery_write.content_id,
                fingerprint,
                current,
            )
            current_ids = write_cohort_run(
                store,
                current,
                source,
                steam,
                artifact,
                predecessors=(prior,),
                retry_binding=retry_binding,
            )

            decision = service.adjudicate_run(store.root, current)

            self.assertEqual(CapabilityVerdict.SUPPORTED, decision.verdict)
            self.assertEqual(current_ids, decision.scenario_result_ids)

    def test_adjudicate_run_is_not_poisoned_by_later_same_command_failure(self):
        with tempfile.TemporaryDirectory(prefix="modlab-history-order-") as directory:
            store = ContainmentStore(Path(directory))
            current_run = "containment-run:" + "4" * 32
            later_run = "containment-run:" + "5" * 32
            source = store.root / "source"
            steam = store.root / "steam"
            artifact = "artifact:ordered-history"
            write_cohort_run(
                store, current_run, source, steam, artifact
            )
            write_cohort_run(
                store,
                later_run,
                source,
                steam,
                artifact,
                predecessors=(current_run,),
                failed_scenario=ContainmentScenario.MERGE_EXISTING,
            )

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
            with self.assertRaises(service.ContainmentDecisionNotReady):
                service.adjudicate_run(store.root, run_id)
            self.assertFalse(store.decision_path(run_id).exists())

    def test_adjudicate_run_preserves_historical_failed_over_predecessor_damage(self):
        for damage in ("request", "result", "outcome"):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory(
                prefix=f"modlab-historical-failed-{damage}-"
            ) as directory:
                store = ContainmentStore(Path(directory))
                prior = "containment-run:" + "d" * 32
                current = "containment-run:" + "e" * 32
                source = store.root / "source"
                steam = store.root / "steam"
                artifact = "artifact:historical-failed-damage"
                prior_ids = write_cohort_run(
                    store,
                    prior,
                    source,
                    steam,
                    artifact,
                    failed_scenario=ContainmentScenario.MERGE_EXISTING,
                )
                write_cohort_run(
                    store,
                    current,
                    source,
                    steam,
                    artifact,
                    predecessors=(prior,),
                )
                damaged = ContainmentScenario.REPLACE_EXISTING
                if damage == "request":
                    store.request_path(prior).write_bytes(b"damaged\n")
                elif damage == "result":
                    store.result_path(prior, damaged).write_bytes(b"damaged\n")
                else:
                    (store.watch_path(prior, damaged) / "outcome.json").write_bytes(
                        b"damaged\n"
                    )

                decision = service.adjudicate_run(store.root, current)

                failed_id = prior_ids[
                    list(ContainmentScenario).index(
                        ContainmentScenario.MERGE_EXISTING
                    )
                ]
                self.assertEqual(CapabilityVerdict.REJECTED, decision.verdict)
                self.assertTrue(
                    any(failed_id in reason for reason in decision.reasons)
                )

    def test_adoption_failures_and_exact_outputs_fail_closed(self):
        scenario = ContainmentScenario.FOMOD_DEPENDENCY
        cases = (
            (
                "FOMOD marker absent",
                dict(staging_output_names=("always.txt", "meta.ini")),
                ScenarioOutcome.FAILED,
            ),
            (
                "unexpected extra output",
                dict(
                    staging_output_names=(
                        "always.txt",
                        "dependency-seen.txt",
                        "meta.ini",
                        "other.txt",
                    )
                ),
                ScenarioOutcome.FAILED,
            ),
            ("adoption collision", dict(adopted_name=None, adopted_tree=None, adopted_integrity=None, safety_reasons=("adoption-collision",)), ScenarioOutcome.INCOMPLETE),
            ("more than one new folder", dict(staging_new_names=("ModLab Spike FOMOD", "Other")), ScenarioOutcome.FAILED),
            ("failed integrity normalization", dict(adopted_integrity=IntegrityObservation.UNKNOWN), ScenarioOutcome.INCOMPLETE),
        )
        for label, changes, expected in cases:
            with self.subTest(label=label):
                result = evaluate_scenario(evidence(scenario, **changes))
                self.assertEqual(expected, result.outcome)
                self.assertTrue(result.reasons)

    def test_missing_adoption_output_after_interrupted_ui_is_incomplete(self):
        # Catches absent operator output being promoted to a proven containment breach.
        result = evaluate_scenario(
            evidence(
                ContainmentScenario.NEW_FOLDER,
                staging_new_names=(),
                output_observation_complete=False,
                adopted_name=None,
                adopted_tree=None,
                adopted_integrity=None,
                incomplete_reasons=(
                    "adoption-proof-unavailable:ContainmentSafetyError",
                ),
            )
        )

        self.assertEqual(ScenarioOutcome.INCOMPLETE, result.outcome)
        self.assertIn("output-observation-incomplete", result.reasons)
        self.assertNotIn("staging-new-folder-set-invalid", result.reasons)

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


class Task4ExactEffectsFixTests(unittest.TestCase):
    @staticmethod
    def _create_junction(link: Path, target: Path) -> None:
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise OSError(completed.stderr or completed.stdout)

    @staticmethod
    def _delegated_receipt(root: Path, operation):
        @service._receipted
        def invoke():
            return service._delegated_mutation(root, operation)

        return invoke()

    @unittest.skipUnless(os.name == "nt", "Windows stable-root guards are required")
    def test_absent_root_never_enters_ordinary_delegated_mutation_unpinned(self):
        with tempfile.TemporaryDirectory(prefix="modlab-effect-absent-root-") as directory:
            base = Path(directory)
            root = base / "root"
            displaced = base / "displaced"
            invoked = False

            def create_swap_and_restore():
                nonlocal invoked
                invoked = True
                root.mkdir()
                (root / "original.txt").write_bytes(b"original\n")
                root.rename(displaced)
                root.mkdir()
                (root / "replacement.txt").write_bytes(b"replacement\n")
                (root / "replacement.txt").unlink()
                root.rmdir()
                displaced.rename(root)

            with self.assertRaisesRegex(
                service.ContainmentOperationError,
                "absent|service-controlled",
            ):
                self._delegated_receipt(root, create_swap_and_restore)

            self.assertFalse(invoked)
            self.assertFalse(root.exists())

    @unittest.skipUnless(os.name == "nt", "Windows stable-root guards are required")
    def test_controlled_creation_retains_every_new_component_before_delegate(self):
        with tempfile.TemporaryDirectory(prefix="modlab-effect-controlled-root-") as directory:
            base = Path(directory)
            parent = base / "new-parent"
            root = parent / "root"
            displaced = base / "displaced"
            preflight_observations = []
            swap_succeeded = False

            @service._receipted
            def invoke():
                def preflight():
                    preflight_observations.append((parent.exists(), root.exists()))

                def mutate(_prepared):
                    nonlocal swap_succeeded
                    try:
                        parent.rename(displaced)
                    except OSError:
                        (root / "direct.txt").write_bytes(b"direct\n")
                    else:
                        swap_succeeded = True
                    return "created"

                return service._delegated_mutations_with_created_root(
                    root,
                    (),
                    preflight,
                    mutate,
                    require_absent=True,
                )

            receipt = invoke()

            self.assertEqual("created", receipt.value)
            self.assertEqual([(False, False)], preflight_observations)
            self.assertFalse(swap_succeeded)
            self.assertEqual(
                (parent, root),
                receipt.effects.child_mutation_roots,
            )
            self.assertEqual((parent, root), receipt.effects.written_paths)

    @unittest.skipUnless(os.name == "nt", "Windows stable-root guards are required")
    def test_controlled_creation_partial_chain_receipts_only_created_component(self):
        with tempfile.TemporaryDirectory(prefix="modlab-effect-partial-chain-") as directory:
            base = Path(directory)
            parent = base / "new-parent"
            root = parent / "root"
            real_create = service.create_pinned_directory_child

            def fail_final(candidate, retained_parent):
                if Path(candidate) == root:
                    raise OSError("injected final-directory refusal")
                return real_create(candidate, retained_parent)

            @service._receipted
            def invoke():
                return service._delegated_mutations_with_created_root(
                    root,
                    (),
                    lambda: None,
                    lambda _prepared: self.fail("delegate must not run"),
                    require_absent=True,
                )

            with (
                patch.object(
                    service,
                    "create_pinned_directory_child",
                    side_effect=fail_final,
                ),
                self.assertRaises(service.ContainmentOperationError) as raised,
            ):
                invoke()

            self.assertTrue(parent.is_dir())
            self.assertFalse(root.exists())
            self.assertEqual(
                (parent,),
                raised.exception.effects.child_mutation_roots,
            )
            self.assertEqual((parent,), raised.exception.effects.written_paths)

    @unittest.skipUnless(os.name == "nt", "Windows exact-root creation is required")
    def test_controlled_creation_cannot_substitute_new_object_before_it_is_pinned(self):
        with tempfile.TemporaryDirectory(prefix="modlab-effect-created-substitution-") as directory:
            base = Path(directory)
            root = base / "root"
            displaced = base / "displaced"
            real_mkdir = Path.mkdir
            substitution_attempted = False

            def substitute_created_object(candidate, *args, **kwargs):
                nonlocal substitution_attempted
                real_mkdir(candidate, *args, **kwargs)
                if Path(candidate) == root:
                    substitution_attempted = True
                    Path(candidate).rename(displaced)
                    real_mkdir(candidate, *args, **kwargs)

            @service._receipted
            def invoke():
                return service._delegated_mutations_with_created_root(
                    root,
                    (),
                    lambda: None,
                    lambda _prepared: (root / "direct.txt").write_bytes(b"direct\n"),
                    require_absent=True,
                )

            with patch.object(
                Path,
                "mkdir",
                autospec=True,
                side_effect=substitute_created_object,
            ):
                receipt = invoke()

            self.assertFalse(substitution_attempted)
            self.assertFalse(displaced.exists())
            self.assertEqual(b"direct\n", (root / "direct.txt").read_bytes())
            self.assertEqual((root,), receipt.effects.child_mutation_roots)
            self.assertEqual((root,), receipt.effects.written_paths)

    def test_completed_mutation_snapshot_survives_close_only_ownership(self):
        with tempfile.TemporaryDirectory(prefix="modlab-effect-completed-snapshot-") as directory:
            root = Path(directory)
            expected = service._mutation_root_observation(root)
            retained = PinnedObject(
                root,
                7201,
                PinnedIdentity(
                    int(expected[0][2]),
                    int(expected[0][3]),
                    int(expected[0][8]),
                ),
            )

            with (
                patch.object(
                    service,
                    "pin_stable_direct_object",
                    return_value=retained,
                ),
                patch.object(
                    retained,
                    "close",
                    side_effect=OSError("injected observation close failure"),
                ),
                self.assertRaises(ContainmentStoreOwnershipError) as raised,
            ):
                service._mutation_root_observation(root)

            self.assertEqual(
                expected,
                getattr(raised.exception, "completed_observation", None),
            )
            self.assertIsInstance(
                raised.exception,
                service._MutationObservationOwnershipError,
            )
            with patch(
                "modlab.platform.windows_exact_fs._close_handle"
            ) as close_handle:
                raised.exception.resolve()
            close_handle.assert_called_once_with(7201)
            self.assertEqual(0, retained.handle)

    def test_multi_root_post_observation_collects_every_position_and_change(self):
        with tempfile.TemporaryDirectory(prefix="modlab-effect-multi-root-owner-") as directory:
            roots = tuple(Path(directory) / name for name in ("first", "middle", "last"))
            for root in roots:
                root.mkdir()

            for owner_index in range(len(roots)):
                with self.subTest(owner_index=owner_index):
                    retained = PinnedObject(
                        roots[owner_index],
                        7300 + owner_index,
                        PinnedIdentity(1, 100 + owner_index, 0x10),
                    )
                    ownership = service._MutationObservationOwnershipError(
                        f"position-{owner_index} close ownership",
                        ExactObjectOwnershipError(
                            f"position-{owner_index} owner",
                            verification=(retained,),
                        ),
                        ((f"after-{owner_index}",),),
                    )
                    before = tuple(((f"before-{index}",),) for index in range(3))
                    after = [((f"after-{index}",),) for index in range(3)]
                    after[owner_index] = ownership

                    @service._receipted
                    def invoke():
                        return service._delegated_mutations_guarded(
                            roots,
                            lambda: "mutated",
                        )

                    with (
                        patch.object(
                            service,
                            "_mutation_root_observation",
                            side_effect=(*before, *after),
                        ) as observe,
                        self.assertRaises(ContainmentStoreOwnershipError) as raised,
                    ):
                        invoke()

                    self.assertIs(ownership, raised.exception)
                    self.assertEqual(6, observe.call_count)
                    self.assertEqual(roots, raised.exception.effects.child_mutation_roots)
                    with patch(
                        "modlab.platform.windows_exact_fs._close_handle"
                    ):
                        raised.exception.resolve()
                    self.assertEqual(0, retained.handle)

    def test_multi_root_unions_all_observers_and_dual_operation_owner(self):
        with tempfile.TemporaryDirectory(prefix="modlab-effect-multi-owner-union-") as directory:
            roots = tuple(Path(directory) / name for name in ("first", "middle", "last"))
            for root in roots:
                root.mkdir()
            operation_pin = PinnedObject(
                roots[0] / "operation",
                7401,
                PinnedIdentity(1, 201, 0),
            )
            first_pin = PinnedObject(
                roots[0] / "first-observer",
                7402,
                PinnedIdentity(1, 202, 0),
            )
            last_pin = PinnedObject(
                roots[2] / "last-observer",
                7403,
                PinnedIdentity(1, 203, 0),
            )
            operation = ContainmentStoreOwnershipError(
                "operation ownership",
                ExactObjectOwnershipError(
                    "operation owner",
                    verification=(operation_pin,),
                ),
            )
            operation.add_note("primary operation context")
            first = service._MutationObservationOwnershipError(
                "first observer ownership",
                ExactObjectOwnershipError(
                    "first observer owner",
                    verification=(first_pin,),
                ),
                (("after-first",),),
            )
            last = ContainmentStoreOwnershipError(
                "last observer ownership",
                ExactObjectOwnershipError(
                    "last observer owner",
                    verification=(last_pin,),
                ),
            )

            @service._receipted
            def invoke():
                return service._delegated_mutations_guarded(
                    roots,
                    lambda: (_ for _ in ()).throw(operation),
                )

            before = (
                (("before-first",),),
                (("before-middle",),),
                (("before-last",),),
            )
            after = (
                first,
                (("after-middle",),),
                last,
            )
            with (
                patch.object(
                    service,
                    "_mutation_root_observation",
                    side_effect=(*before, *after),
                ) as observe,
                self.assertRaises(ContainmentStoreOwnershipError) as raised,
            ):
                invoke()

            self.assertIs(operation, raised.exception)
            self.assertEqual(6, observe.call_count)
            self.assertEqual(
                (operation_pin, first_pin, last_pin),
                tuple(owner.pinned for owner in raised.exception.owners),
            )
            self.assertEqual(
                (roots[0], roots[1]),
                raised.exception.effects.child_mutation_roots,
            )
            self.assertIn(
                "primary operation context",
                getattr(raised.exception, "__notes__", ()),
            )
            self.assertTrue(
                any(
                    "additional post-observation ownership" in note
                    and str(roots[2]) in note
                    for note in getattr(raised.exception, "__notes__", ())
                )
            )
            with patch(
                "modlab.platform.windows_exact_fs._close_handle"
            ) as close_handle:
                raised.exception.resolve()
            self.assertEqual(3, close_handle.call_count)
            self.assertTrue(
                all(pin.handle == 0 for pin in (operation_pin, first_pin, last_pin))
            )

    @unittest.skipUnless(os.name == "nt", "Windows stable-root observation is required")
    def test_interrupted_snapshot_closes_retained_pins_without_completing_observation(self):
        for position in ("root_pin", "child_pin", "read", "rescan"):
            with self.subTest(position=position), tempfile.TemporaryDirectory(
                prefix="modlab-effect-interrupted-snapshot-"
            ) as directory:
                root = Path(directory)
                child = root / "child.bin"
                child.write_bytes(b"snapshot\n")
                root_pin = PinnedObject(root, 7801, PinnedIdentity(1, 1, 0x10))
                child_pin = PinnedObject(child, 7802, PinnedIdentity(1, 2, 0))
                interruption = KeyboardInterrupt(position)
                real_scandir = os.scandir
                scans = 0

                def pin(path, _kind):
                    if Path(path) == root:
                        if position == "root_pin":
                            raise interruption
                        return root_pin
                    if position == "child_pin":
                        raise interruption
                    return child_pin

                def scan(path):
                    nonlocal scans
                    scans += 1
                    if position == "rescan" and scans == 2:
                        raise interruption
                    return real_scandir(path)

                with (
                    patch.object(service, "pin_stable_direct_object", side_effect=pin),
                    patch.object(service, "read_pinned_file", side_effect=interruption if position == "read" else None, return_value=b"snapshot\n"),
                    patch.object(service.os, "scandir", side_effect=scan),
                    patch.object(windows_exact_fs, "_close_handle") as close,
                    self.assertRaises(KeyboardInterrupt) as raised,
                ):
                    service._mutation_root_observation(root)
                self.assertIs(interruption, raised.exception)
                expected = [] if position == "root_pin" else [7801] if position == "child_pin" else [7802, 7801]
                self.assertEqual(expected, [call.args[0] for call in close.call_args_list])
                self.assertFalse(hasattr(raised.exception, "completed_observation"))

    @unittest.skipUnless(os.name == "nt", "Windows stable-root observation is required")
    def test_interrupted_snapshot_unions_acquisition_retained_and_close_failure_owners(self):
        for acquisition_fails in (False, True):
            with self.subTest(acquisition_fails=acquisition_fails), tempfile.TemporaryDirectory(
                prefix="modlab-effect-interrupted-snapshot-owners-"
            ) as directory:
                root = Path(directory)
                child = root / "child.bin"
                child.write_bytes(b"snapshot\n")
                root_pin = PinnedObject(root, 7811, PinnedIdentity(1, 1, 0x10))
                child_pin = PinnedObject(child, 7812, PinnedIdentity(1, 2, 0))
                extra_pin = PinnedObject(root, 7813, PinnedIdentity(1, 1, 0x10))
                acquisition = ExactObjectOwnershipError("acquisition failed", verification=(child_pin,))
                interruption = KeyboardInterrupt("read interrupted")
                close_error = ExactObjectOwnershipError("close retained an additional owner", verification=(extra_pin,))

                def pin(path, _kind):
                    if Path(path) == root:
                        return root_pin
                    if acquisition_fails:
                        raise acquisition
                    return child_pin

                @service._receipted
                def invoke():
                    return service._mutation_root_observation(root)

                observed = None
                with (
                    patch.object(service, "pin_stable_direct_object", side_effect=pin),
                    patch.object(service, "read_pinned_file", side_effect=interruption),
                    patch.object(root_pin, "close", side_effect=close_error),
                    patch.object(child_pin, "close", side_effect=SystemExit("child close interrupted")),
                ):
                    try:
                        invoke()
                    except BaseException as error:
                        observed = error
                self.assertIsInstance(observed, ContainmentStoreOwnershipError)
                self.assertEqual({7811, 7812, 7813}, {owner.pinned.handle for owner in observed.owners})
                self.assertIs(acquisition if acquisition_fails else interruption, observed.__cause__)
                self.assertFalse(hasattr(observed, "completed_observation"))
                self.assertEqual((), observed.effects.child_mutation_roots)
                with patch.object(windows_exact_fs, "_close_handle") as close:
                    observed.resolve()
                self.assertEqual({7811, 7812, 7813}, {call.args[0] for call in close.call_args_list})
                self.assertTrue(all(pin.handle == 0 for pin in (root_pin, child_pin, extra_pin)))

    def _assert_creation_failure_survives_outer_cleanup(
        self, lookup_position=None, *, close_guards=True, additional_retry_owner=False,
    ):
        with tempfile.TemporaryDirectory(prefix="modlab-effect-typed-outer-union-") as directory:
            base = Path(directory)
            parent = base / "proven-parent"
            root = parent / "created-root"
            real_create = windows_exact_fs.create_pinned_directory_child
            real_identity = windows_exact_fs.identity_at_path
            real_close = windows_exact_fs._close_handle
            creation_error = None
            creation_parent = None
            retry_pin = None
            interruption = SystemExit("receipt identity interrupted")

            def create(path, retained_parent):
                nonlocal creation_error, creation_parent
                if Path(path) != root:
                    return real_create(path, retained_parent)
                creation_parent = retained_parent
                lookups = 0

                def late_mismatch(path):
                    nonlocal lookups
                    observed = real_identity(path)
                    if Path(path) == parent:
                        lookups += 1
                        if lookups == 2:
                            return replace(observed, file_id=observed.file_id + 1)
                    return observed

                try:
                    return real_create(path, retained_parent, identity_at_path_fn=late_mismatch)
                except windows_exact_fs.ExactDirectoryCreationOwnershipError as error:
                    creation_error = error
                    raise

            def receipt_identity(path):
                if creation_error is not None and Path(path) == (parent if lookup_position == "parent" else root) and lookup_position is not None:
                    raise interruption
                return real_identity(path)

            def close(handle):
                if close_guards and creation_parent is not None and handle == creation_parent.handle:
                    raise KeyboardInterrupt("parent guard close interrupted")
                real_close(handle)

            @service._receipted
            def invoke():
                return service._delegated_mutations_with_created_root(
                    root, (), lambda: None,
                    lambda _prepared: self.fail("delegate must not run"),
                    require_absent=True,
                )

            observed = None
            try:
                with (
                    patch.object(service, "create_pinned_directory_child", side_effect=create),
                    patch.object(service, "identity_at_path", side_effect=receipt_identity),
                    patch.object(windows_exact_fs, "delete_pinned_object", side_effect=OSError("candidate delete failed")),
                    patch.object(windows_exact_fs, "_close_handle", side_effect=close),
                ):
                    try:
                        invoke()
                    except BaseException as error:
                        observed = error
                self.assertIsInstance(observed, ContainmentStoreOwnershipError)
                self.assertIsInstance(observed.ownership, windows_exact_fs.ExactDirectoryCreationOwnershipError)
                self.assertIs(creation_error.outcome, observed.ownership.outcome)
                self.assertIs(creation_error.created_candidate, observed.ownership.created_candidate)
                expected_owners = {id(creation_error.created_candidate)}
                if close_guards:
                    expected_owners.add(id(creation_parent))
                self.assertEqual(expected_owners, {id(owner.pinned) for owner in observed.owners})
                self.assertEqual((parent, root) if lookup_position is None else (parent,), observed.effects.child_mutation_roots)
                self.assertEqual((creation_error.created_candidate,), observed.candidates)
                self.assertEqual((creation_parent,) if close_guards else (), observed.verification)
                if lookup_position is not None:
                    self.assertIn("SystemExit", " ".join(getattr(observed.ownership, "__notes__", ())))
                with (
                    patch.object(windows_exact_fs, "delete_pinned_object", side_effect=OSError("candidate retry failed")),
                    patch.object(windows_exact_fs, "_close_handle", side_effect=close),
                    self.assertRaises(ContainmentStoreOwnershipError) as retry,
                ):
                    observed.resolve()
                self.assertIs(observed, retry.exception)
                self.assertIsInstance(observed.ownership, windows_exact_fs.ExactDirectoryCreationOwnershipError)
                self.assertIs(creation_error.outcome, observed.ownership.outcome)
                self.assertIs(creation_error.created_candidate, observed.ownership.created_candidate)
                self.assertEqual(len(expected_owners), len(observed.owners))
                self.assertEqual((parent, root) if lookup_position is None else (parent,), observed.effects.child_mutation_roots)
                if additional_retry_owner:
                    retry_pin = windows_exact_fs.pin_direct_object(base, kind="directory")
                    retry_error = ExactObjectOwnershipError(
                        "guard retry retained another exact owner", verification=(retry_pin,),
                    )

                    def close_with_additional_owner(handle):
                        if handle == creation_parent.handle:
                            raise retry_error
                        real_close(handle)

                    with (
                        patch.object(windows_exact_fs, "_close_handle", side_effect=close_with_additional_owner),
                        self.assertRaises(ContainmentStoreOwnershipError),
                    ):
                        observed.resolve()
                    self.assertIsInstance(observed.ownership, windows_exact_fs.ExactDirectoryCreationOwnershipError)
                    self.assertIs(creation_error.outcome, observed.ownership.outcome)
                    self.assertIs(creation_error.created_candidate, observed.ownership.created_candidate)
                    self.assertEqual(0, creation_error.created_candidate.handle)
                    self.assertEqual((), observed.candidates)
                    self.assertEqual({id(creation_parent), id(retry_pin)}, {id(owner.pinned) for owner in observed.owners})
                observed.resolve()
                self.assertFalse(root.exists())
                self.assertEqual(0, creation_parent.handle)
                self.assertEqual(0, creation_error.created_candidate.handle)
                if retry_pin is not None:
                    self.assertEqual(0, retry_pin.handle)
            finally:
                # Exact retained-object cleanup also runs when a RED assertion fails.
                if creation_error is not None:
                    creation_error.resolve()
                if creation_parent is not None:
                    creation_parent.close()
                if retry_pin is not None:
                    retry_pin.close()

    @unittest.skipUnless(os.name == "nt", "Windows exact-root creation is required")
    def test_creation_outcome_survives_service_outer_union_and_failed_retry(self):
        self._assert_creation_failure_survives_outer_cleanup()

    @unittest.skipUnless(os.name == "nt", "Windows exact-root creation is required")
    def test_creation_receipt_interrupt_preserves_primary_owners_and_proven_effects(self):
        for position in ("parent", "candidate"):
            for close_guards in (False, True):
                with self.subTest(position=position, close_guards=close_guards):
                    self._assert_creation_failure_survives_outer_cleanup(position, close_guards=close_guards)

    @unittest.skipUnless(os.name == "nt", "Windows exact-root creation is required")
    def test_creation_outer_retry_preserves_new_owner_after_candidate_cleanup(self):
        self._assert_creation_failure_survives_outer_cleanup(additional_retry_owner=True)

    @unittest.skipUnless(os.name == "nt", "Windows stable-root observation is required")
    def test_child_pin_acquisition_ownership_unions_retained_close_owner(self):
        for kind in ("directory", "file"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(
                prefix=f"modlab-effect-{kind}-acquisition-owner-"
            ) as directory:
                root = Path(directory)
                child = root / ("child" if kind == "directory" else "child.bin")
                if kind == "directory":
                    child.mkdir()
                else:
                    child.write_bytes(b"child\n")
                root_pin = PinnedObject(
                    root,
                    7501,
                    PinnedIdentity(1, 301, 0x10),
                )
                acquisition_pin = PinnedObject(
                    child,
                    7502,
                    PinnedIdentity(1, 302, 0x10 if kind == "directory" else 0),
                )
                acquisition_error = ExactObjectOwnershipError(
                    f"injected {kind} acquisition ownership",
                    verification=(acquisition_pin,),
                )

                def pin(path, requested_kind, **_kwargs):
                    if Path(path) == root:
                        return root_pin
                    if Path(path) == child and requested_kind == kind:
                        raise acquisition_error
                    self.fail(f"unexpected pin request: {path} ({requested_kind})")

                with (
                    patch.object(
                        service,
                        "pin_stable_direct_object",
                        side_effect=pin,
                    ),
                    patch.object(
                        root_pin,
                        "close",
                        side_effect=OSError("injected retained-root close failure"),
                    ),
                    self.assertRaises(ContainmentStoreOwnershipError) as raised,
                ):
                    service._mutation_root_observation(root)

                self.assertNotIsInstance(
                    raised.exception,
                    service._MutationObservationOwnershipError,
                    "a failed child acquisition did not complete the snapshot",
                )
                self.assertEqual(
                    {7501, 7502},
                    {owner.pinned.handle for owner in raised.exception.owners},
                )
                with patch(
                    "modlab.platform.windows_exact_fs._close_handle"
                ) as close_handle:
                    raised.exception.resolve()
                self.assertEqual(2, close_handle.call_count)
                self.assertEqual(0, acquisition_pin.handle)
                self.assertEqual(0, root_pin.handle)

    @unittest.skipUnless(os.name == "nt", "Windows stable-root observation is required")
    def test_child_pin_acquisition_ownership_keeps_later_proven_root_effect(self):
        with tempfile.TemporaryDirectory(
            prefix="modlab-effect-acquisition-later-root-"
        ) as directory:
            roots = tuple(Path(directory) / name for name in ("first", "middle", "last"))
            for root in roots:
                root.mkdir()
            first_child = roots[0] / "child.bin"
            first_child.write_bytes(b"before\n")
            acquisition_pin = PinnedObject(
                first_child,
                7510,
                PinnedIdentity(1, 310, 0),
            )
            acquisition_error = ExactObjectOwnershipError(
                "injected post-operation child acquisition ownership",
                verification=(acquisition_pin,),
            )
            real_pin = service.pin_stable_direct_object
            after_operation = False

            def pin(path, kind, **kwargs):
                if after_operation and Path(path) == first_child:
                    raise acquisition_error
                return real_pin(path, kind, **kwargs)

            @service._receipted
            def invoke():
                def mutate():
                    nonlocal after_operation
                    (roots[2] / "changed.bin").write_bytes(b"changed\n")
                    after_operation = True
                    return "mutated"

                return service._delegated_mutations_guarded(roots, mutate)

            observed_error = None
            try:
                with patch.object(
                    service,
                    "pin_stable_direct_object",
                    side_effect=pin,
                ):
                    invoke()
            except BaseException as error:
                observed_error = error

            self.assertIsInstance(observed_error, ContainmentStoreOwnershipError)
            raised_error = observed_error
            self.assertEqual(
                (roots[2],),
                raised_error.effects.child_mutation_roots,
            )
            self.assertEqual(
                (roots[2],),
                raised_error.effects.written_paths,
            )
            self.assertEqual(
                (acquisition_pin,),
                tuple(owner.pinned for owner in raised_error.owners),
            )
            with patch(
                "modlab.platform.windows_exact_fs._close_handle"
            ) as close_handle:
                raised_error.resolve()
            close_handle.assert_called_once_with(7510)
            self.assertEqual(0, acquisition_pin.handle)

    @unittest.skipUnless(os.name == "nt", "Windows exact-root creation is required")
    def test_surviving_exact_created_candidate_is_receipted_before_ownership_refusal(self):
        with tempfile.TemporaryDirectory(
            prefix="modlab-effect-created-candidate-owner-"
        ) as directory:
            parent = Path(directory)
            root = parent / "created-root"
            real_identity = windows_exact_fs.identity_at_path
            parent_lookups = 0

            def late_parent_mismatch(path):
                nonlocal parent_lookups
                observed = real_identity(path)
                if Path(path) == parent:
                    parent_lookups += 1
                    if parent_lookups == 2:
                        return replace(observed, file_id=observed.file_id + 1)
                return observed

            def create_with_late_validation_failure(path, retained_parent):
                return windows_exact_fs.create_pinned_directory_child(
                    path,
                    retained_parent,
                    identity_at_path_fn=late_parent_mismatch,
                )

            @service._receipted
            def invoke():
                return service._delegated_mutations_with_created_root(
                    root,
                    (),
                    lambda: None,
                    lambda _prepared: self.fail("delegate must not run"),
                    require_absent=True,
                )

            with (
                patch.object(
                    service,
                    "create_pinned_directory_child",
                    side_effect=create_with_late_validation_failure,
                ),
                patch.object(
                    windows_exact_fs,
                    "delete_pinned_object",
                    side_effect=OSError("injected candidate cleanup failure"),
                ),
                self.assertRaises(ContainmentStoreOwnershipError) as raised,
            ):
                invoke()

            error = raised.exception
            candidate = error.ownership.candidate
            try:
                self.assertEqual(
                    "ExactDirectoryCreationOwnershipError",
                    type(error.ownership).__name__,
                )
                self.assertEqual((root,), error.effects.child_mutation_roots)
                self.assertEqual((root,), error.effects.written_paths)
                self.assertEqual(1, len(error.ownership.candidates))
                self.assertEqual(root, candidate.path)
                self.assertEqual(
                    candidate.identity,
                    windows_exact_fs.identity_at_path(root),
                )
            finally:
                error.resolve()
            self.assertEqual(0, candidate.handle)
            self.assertFalse(root.exists())

    @unittest.skipUnless(os.name == "nt", "Windows exact-root creation is required")
    def test_exact_created_candidate_cleanup_success_has_no_surviving_receipt(self):
        with tempfile.TemporaryDirectory(
            prefix="modlab-effect-created-candidate-cleaned-"
        ) as directory:
            parent = Path(directory)
            root = parent / "created-root"
            real_identity = windows_exact_fs.identity_at_path
            parent_lookups = 0

            def late_parent_mismatch(path):
                nonlocal parent_lookups
                observed = real_identity(path)
                if Path(path) == parent:
                    parent_lookups += 1
                    if parent_lookups == 2:
                        return replace(observed, file_id=observed.file_id + 1)
                return observed

            def create_with_late_validation_failure(path, retained_parent):
                return windows_exact_fs.create_pinned_directory_child(
                    path,
                    retained_parent,
                    identity_at_path_fn=late_parent_mismatch,
                )

            @service._receipted
            def invoke():
                return service._delegated_mutations_with_created_root(
                    root,
                    (),
                    lambda: None,
                    lambda _prepared: self.fail("delegate must not run"),
                    require_absent=True,
                )

            with (
                patch.object(
                    service,
                    "create_pinned_directory_child",
                    side_effect=create_with_late_validation_failure,
                ),
                self.assertRaises(service.ContainmentOperationError) as raised,
            ):
                invoke()

            self.assertEqual((), raised.exception.effects.child_mutation_roots)
            self.assertEqual((), raised.exception.effects.written_paths)
            self.assertFalse(root.exists())

    @unittest.skipUnless(os.name == "nt", "Windows exact-root creation is required")
    def test_created_without_handle_evidence_is_receipted_without_path_cleanup(self):
        with tempfile.TemporaryDirectory(
            prefix="modlab-effect-created-without-owner-"
        ) as directory:
            root = Path(directory) / "created-root"

            def report_created_without_handle(
                _output_handle,
                _desired_access,
                _object_attributes,
                io_status,
                _allocation_size,
                _file_attributes,
                _share_access,
                _create_disposition,
                _create_options,
                _ea_buffer,
                _ea_length,
            ):
                windows_exact_fs.ctypes.cast(
                    io_status,
                    windows_exact_fs.ctypes.POINTER(
                        windows_exact_fs._IO_STATUS_BLOCK
                    ),
                ).contents.Information = windows_exact_fs._FILE_CREATED_INFORMATION
                return 0

            @service._receipted
            def invoke():
                return service._delegated_mutations_with_created_root(
                    root,
                    (),
                    lambda: None,
                    lambda _prepared: self.fail("delegate must not run"),
                    require_absent=True,
                )

            with (
                patch.object(
                    windows_exact_fs._ntdll,
                    "NtCreateFile",
                    side_effect=report_created_without_handle,
                ),
                self.assertRaises(service.ContainmentOperationError) as raised,
            ):
                invoke()

            self.assertEqual((root,), raised.exception.effects.child_mutation_roots)
            self.assertEqual((root,), raised.exception.effects.written_paths)
            self.assertFalse(root.exists())

    @unittest.skipUnless(os.name == "nt", "Windows exact-root creation is required")
    def test_verification_only_native_creation_refusal_has_no_receipt(self):
        with tempfile.TemporaryDirectory(
            prefix="modlab-effect-uncertain-created-root-"
        ) as directory:
            root = Path(directory) / "uncertain-root"
            real_create = windows_exact_fs._ntdll.NtCreateFile

            def report_opened(*arguments):
                status = real_create(*arguments)
                windows_exact_fs.ctypes.cast(
                    arguments[3],
                    windows_exact_fs.ctypes.POINTER(
                        windows_exact_fs._IO_STATUS_BLOCK
                    ),
                ).contents.Information = 1
                return status

            @service._receipted
            def invoke():
                return service._delegated_mutations_with_created_root(
                    root,
                    (),
                    lambda: None,
                    lambda _prepared: self.fail("delegate must not run"),
                    require_absent=True,
                )

            with (
                patch.object(
                    windows_exact_fs._ntdll,
                    "NtCreateFile",
                    side_effect=report_opened,
                ),
                self.assertRaises(ContainmentStoreOwnershipError) as raised,
            ):
                invoke()

            error = raised.exception
            try:
                self.assertEqual((), error.effects.child_mutation_roots)
                self.assertEqual((), error.effects.written_paths)
                self.assertEqual((), error.candidates)
                self.assertEqual(1, len(error.verification))
                self.assertFalse(error.ownership.outcome.proven_created)
            finally:
                error.resolve()
            self.assertTrue(root.is_dir())

    def test_created_candidate_receipt_lookup_ownership_preserves_all_owners(self):
        with tempfile.TemporaryDirectory(
            prefix="modlab-effect-candidate-receipt-owner-"
        ) as directory:
            parent_path = Path(directory)
            root = parent_path / "created-root"
            parent = PinnedObject(parent_path, 7600, PinnedIdentity(9, 400, 0x10))
            candidate = PinnedObject(root, 7601, PinnedIdentity(9, 401, 0x10))
            lookup_pin = PinnedObject(root, 7602, PinnedIdentity(9, 401, 0x10))
            outcome = windows_exact_fs.ExactDirectoryCreationOutcome(
                path=root,
                parent_path=parent_path,
                parent_identity=parent.identity,
                status=0,
                information=2,
                has_valid_handle=True,
            )
            creation_error = windows_exact_fs.ExactDirectoryCreationOwnershipError(
                "created candidate cleanup failed",
                outcome,
                candidate=candidate,
            )
            lookup_error = ExactObjectOwnershipError(
                "receipt lookup retained ownership",
                verification=(lookup_pin,),
            )

            @service._receipted
            def invoke():
                service._receipt_exact_directory_creation_failure(
                    creation_error,
                    root,
                    parent,
                )
                raise ContainmentStoreOwnershipError(
                    "creation failed with live ownership",
                    creation_error,
                )

            with (
                patch.object(
                    service,
                    "identity_at_path",
                    side_effect=(parent.identity, lookup_error),
                ),
                self.assertRaises(ContainmentStoreOwnershipError) as raised,
            ):
                invoke()

            self.assertEqual((), raised.exception.effects.written_paths)
            self.assertEqual(
                (candidate, lookup_pin),
                tuple(owner.pinned for owner in raised.exception.owners),
            )
            self.assertTrue(
                any(
                    "effect observation retained additional ownership" in note
                    for note in getattr(creation_error, "__notes__", ())
                )
            )
            with (
                patch.object(
                    windows_exact_fs,
                    "delete_pinned_object",
                    side_effect=lambda pinned: setattr(pinned, "handle", 0),
                ),
                patch.object(windows_exact_fs, "_close_handle"),
            ):
                raised.exception.resolve()
            self.assertEqual(0, candidate.handle)
            self.assertEqual(0, lookup_pin.handle)

    def test_created_candidate_receipt_rejects_changed_pinned_path_binding(self):
        with tempfile.TemporaryDirectory(
            prefix="modlab-effect-candidate-receipt-mismatch-"
        ) as directory:
            parent_path = Path(directory)
            root = parent_path / "created-root"
            parent = PinnedObject(parent_path, 7610, PinnedIdentity(9, 410, 0x10))
            candidate = PinnedObject(root, 7611, PinnedIdentity(9, 411, 0x10))
            outcome = windows_exact_fs.ExactDirectoryCreationOutcome(
                path=root,
                parent_path=parent_path,
                parent_identity=parent.identity,
                status=0,
                information=2,
                has_valid_handle=True,
            )
            creation_error = windows_exact_fs.ExactDirectoryCreationOwnershipError(
                "created candidate cleanup failed",
                outcome,
                candidate=candidate,
            )

            @service._receipted
            def invoke():
                service._receipt_exact_directory_creation_failure(
                    creation_error,
                    root,
                    parent,
                )
                raise ContainmentStoreOwnershipError(
                    "creation failed with live ownership",
                    creation_error,
                )

            with (
                patch.object(
                    service,
                    "identity_at_path",
                    side_effect=(
                        parent.identity,
                        PinnedIdentity(10, 411, 0x10),
                    ),
                ),
                self.assertRaises(ContainmentStoreOwnershipError) as raised,
            ):
                invoke()

            self.assertEqual((), raised.exception.effects.written_paths)
            self.assertTrue(
                any(
                    "effect observation is unavailable" in note
                    for note in getattr(creation_error, "__notes__", ())
                )
            )
            with patch.object(
                windows_exact_fs,
                "delete_pinned_object",
                side_effect=lambda pinned: setattr(pinned, "handle", 0),
            ):
                raised.exception.resolve()
            self.assertEqual(0, candidate.handle)

    @unittest.skipUnless(os.name == "nt", "Windows stable-root guards are required")
    def test_mutation_guard_allows_unrelated_sibling_creation_during_acquisition(self):
        with tempfile.TemporaryDirectory(
            prefix="modlab-effect-sibling-acquisition-"
        ) as directory:
            parent = Path(directory)
            root = parent / "root"
            sibling = parent / "unrelated-sibling"
            root.mkdir()
            real_pin = service.pin_stable_direct_object
            created_sibling = False

            def pin_after_sibling_creation(path, kind, **kwargs):
                nonlocal created_sibling
                if Path(path) == root and not created_sibling:
                    sibling.mkdir()
                    created_sibling = True
                return real_pin(path, kind, **kwargs)

            guard = None
            observed_error = None
            try:
                try:
                    with patch.object(
                        service,
                        "pin_stable_direct_object",
                        side_effect=pin_after_sibling_creation,
                    ):
                        guard = service._mutation_root_guard(
                            root,
                            require_target_existing=True,
                        )
                except BaseException as error:
                    observed_error = error

                self.assertTrue(created_sibling)
                self.assertIsNone(observed_error)
                self.assertIsNotNone(guard)
                self.assertEqual(guard.identity, service.identity_at_path(root))
            finally:
                if guard is not None:
                    guard.close()

    @unittest.skipUnless(os.name == "nt", "Windows stable-root observation is required")
    def test_mutation_observation_allows_unrelated_ancestor_sibling_creation(self):
        with tempfile.TemporaryDirectory(
            prefix="modlab-effect-sibling-observation-"
        ) as directory:
            parent = Path(directory)
            root = parent / "root"
            sibling = parent / "unrelated-sibling"
            root.mkdir()
            (root / "unchanged.bin").write_bytes(b"unchanged\n")
            real_scandir = service.os.scandir
            created_sibling = False

            def scan_after_sibling_creation(path):
                nonlocal created_sibling
                if Path(path) == root and not created_sibling:
                    sibling.mkdir()
                    metadata = parent.lstat()
                    # NTFS may coalesce directory timestamps; force the benign
                    # ancestor metadata tick without sleeping or touching root.
                    os.utime(
                        parent,
                        ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1_000_000_000),
                    )
                    created_sibling = True
                return real_scandir(path)

            with patch.object(
                service.os,
                "scandir",
                side_effect=scan_after_sibling_creation,
            ):
                observed = service._mutation_root_observation(root)

            self.assertTrue(created_sibling)
            self.assertIsNotNone(observed)
            self.assertEqual(2, len(observed))
            self.assertEqual("unchanged.bin", observed[1][0])

    @unittest.skipUnless(os.name == "nt", "Windows stable-root guards are required")
    def test_mutation_guard_rejects_exact_root_swap_during_acquisition(self):
        with tempfile.TemporaryDirectory(
            prefix="modlab-effect-exact-root-swap-"
        ) as directory:
            parent = Path(directory)
            root = parent / "root"
            displaced = parent / "displaced-root"
            root.mkdir()
            original_identity = service.identity_at_path(root)
            real_pin = service.pin_stable_direct_object
            swapped = False

            def swap_then_pin(path, kind, **kwargs):
                nonlocal swapped
                if Path(path) == root and not swapped:
                    root.rename(displaced)
                    root.mkdir()
                    swapped = True
                return real_pin(path, kind, **kwargs)

            with (
                patch.object(
                    service,
                    "pin_stable_direct_object",
                    side_effect=swap_then_pin,
                ),
                self.assertRaisesRegex(
                    service._MutationObservationError,
                    "changed identity",
                ),
            ):
                service._mutation_root_guard(root, require_target_existing=True)

            self.assertTrue(swapped)
            self.assertEqual(original_identity, service.identity_at_path(displaced))
            self.assertNotEqual(original_identity, service.identity_at_path(root))

    @unittest.skipUnless(os.name == "nt", "Windows stable-root guards are required")
    def test_mutation_guard_rejects_same_file_id_from_foreign_volume(self):
        with tempfile.TemporaryDirectory(prefix="modlab-effect-volume-identity-") as directory:
            root = Path(directory)
            file_id = int(root.lstat().st_ino)
            path_identity = PinnedIdentity(101, file_id, 0x10)
            pinned = PinnedObject(root, 7001, PinnedIdentity(202, file_id, 0x10))

            with (
                patch.object(
                    service,
                    "identity_at_path",
                    return_value=path_identity,
                    create=True,
                ),
                patch.object(
                    service,
                    "pin_stable_direct_object",
                    return_value=pinned,
                ),
                patch.object(pinned, "close", side_effect=lambda: setattr(pinned, "handle", 0)),
                self.assertRaisesRegex(
                    service._MutationObservationError,
                    "identity|volume",
                ),
            ):
                service._mutation_root_guard(root)

            self.assertEqual(0, pinned.handle)

    @unittest.skipUnless(os.name == "nt", "Windows stable-root guards are required")
    def test_mutation_guard_retains_the_native_canonical_path_identity(self):
        with tempfile.TemporaryDirectory(prefix="modlab-effect-native-identity-") as directory:
            root = Path(directory)
            expected = service.identity_at_path(root)
            guard = service._mutation_root_guard(
                root,
                require_target_existing=True,
            )
            self.assertIsNotNone(guard)
            try:
                self.assertEqual(expected, guard.identity)
            finally:
                guard.close()

    @unittest.skipUnless(os.name == "nt", "Windows stable-root guards are required")
    def test_mutation_guard_identity_lookup_ownership_remains_resolvable(self):
        with tempfile.TemporaryDirectory(prefix="modlab-effect-identity-owner-") as directory:
            root = Path(directory)
            guard = PinnedObject(
                root,
                0,
                PinnedIdentity(1, int(root.lstat().st_ino), 0x10),
            )
            retained = PinnedObject(root, 7002, PinnedIdentity(303, 404, 0x10))
            exact = ExactObjectOwnershipError(
                "identity lookup retained ownership",
                verification=(retained,),
            )

            with (
                patch.object(
                    service,
                    "identity_at_path",
                    side_effect=exact,
                    create=True,
                ),
                patch.object(
                    service,
                    "pin_stable_direct_object",
                    return_value=guard,
                ),
                self.assertRaises(ContainmentStoreOwnershipError) as raised,
            ):
                service._mutation_root_guard(root)

            self.assertIs(exact, raised.exception.ownership)
            with patch(
                "modlab.platform.windows_exact_fs._close_handle"
            ) as close_handle:
                raised.exception.resolve()
            close_handle.assert_called_once_with(7002)
            self.assertEqual(0, retained.handle)

    @unittest.skipUnless(os.name == "nt", "Windows stable-root guards are required")
    def test_post_pin_identity_lookup_ownership_closes_guard_and_remains_resolvable(self):
        with tempfile.TemporaryDirectory(prefix="modlab-effect-post-identity-owner-") as directory:
            root = Path(directory)
            identity = PinnedIdentity(505, 606, 0x10)
            guard = PinnedObject(root, 7003, identity)
            retained = PinnedObject(root, 7004, PinnedIdentity(505, 607, 0x10))
            exact = ExactObjectOwnershipError(
                "post-pin identity lookup retained ownership",
                verification=(retained,),
            )

            with (
                patch.object(
                    service,
                    "identity_at_path",
                    side_effect=(identity, exact),
                ),
                patch.object(
                    service,
                    "pin_stable_direct_object",
                    return_value=guard,
                ),
                patch.object(
                    guard,
                    "close",
                    side_effect=lambda: setattr(guard, "handle", 0),
                ) as close_guard,
                self.assertRaises(ContainmentStoreOwnershipError) as raised,
            ):
                service._mutation_root_guard(root)

            self.assertIs(exact, raised.exception.ownership)
            close_guard.assert_called_once_with()
            self.assertEqual(0, guard.handle)
            with patch(
                "modlab.platform.windows_exact_fs._close_handle"
            ) as close_handle:
                raised.exception.resolve()
            close_handle.assert_called_once_with(7004)
            self.assertEqual(0, retained.handle)

    @unittest.skipUnless(os.name == "nt", "Windows stable-root guards are required")
    def test_dual_operation_and_observer_ownership_is_unioned_and_resolvable(self):
        with tempfile.TemporaryDirectory(prefix="modlab-effect-owner-union-") as directory:
            root = Path(directory)
            primary_pin = PinnedObject(
                root / "primary.tmp",
                7101,
                PinnedIdentity(1, 11, 0),
            )
            observer_pin = PinnedObject(
                root / "observer.tmp",
                7102,
                PinnedIdentity(1, 12, 0),
            )
            primary = ContainmentStoreOwnershipError(
                "primary operation ownership",
                ExactObjectOwnershipError(
                    "primary owner",
                    verification=(primary_pin,),
                ),
            )
            primary.add_note("primary context note")
            observer = ContainmentStoreOwnershipError(
                "post-observation ownership",
                ExactObjectOwnershipError(
                    "observer owner",
                    verification=(observer_pin,),
                ),
            )

            with (
                patch.object(
                    service,
                    "_mutation_root_observation",
                    side_effect=((('before',),), observer),
                ),
                self.assertRaises(ContainmentStoreOwnershipError) as raised,
            ):
                self._delegated_receipt(
                    root,
                    lambda: (_ for _ in ()).throw(primary),
                )

            self.assertIs(primary, raised.exception)
            self.assertEqual(
                (primary_pin, observer_pin),
                tuple(owner.pinned for owner in raised.exception.owners),
            )
            self.assertTrue(
                any(
                    "post-observation ownership" in note
                    for note in getattr(raised.exception, "__notes__", ())
                )
            )
            self.assertIn(
                "primary context note",
                getattr(raised.exception, "__notes__", ()),
            )
            with patch(
                "modlab.platform.windows_exact_fs._close_handle"
            ) as close_handle:
                raised.exception.resolve()
            self.assertEqual(2, close_handle.call_count)
            self.assertEqual(0, primary_pin.handle)
            self.assertEqual(0, observer_pin.handle)

    def test_delegated_mutation_rejects_root_junction_before_delegate(self):
        with tempfile.TemporaryDirectory(prefix="modlab-effect-root-junction-") as directory:
            base = Path(directory)
            outside = base / "outside"
            root = base / "redirected-root"
            outside.mkdir()
            try:
                self._create_junction(root, outside)
            except OSError as error:
                self.skipTest(f"junction creation unavailable: {error}")
            invoked = False

            def mutate():
                nonlocal invoked
                invoked = True
                (root / "escaped.txt").write_bytes(b"escaped\n")

            with self.assertRaisesRegex(
                service.ContainmentOperationError,
                "reparse|redirect|direct directory",
            ):
                self._delegated_receipt(root, mutate)

            self.assertFalse(invoked)
            self.assertFalse((outside / "escaped.txt").exists())

    def test_delegated_mutation_rejects_ancestor_junction_before_delegate(self):
        with tempfile.TemporaryDirectory(prefix="modlab-effect-ancestor-junction-") as directory:
            base = Path(directory)
            outside = base / "outside"
            ancestor = base / "redirected-ancestor"
            root = ancestor / "root"
            (outside / "root").mkdir(parents=True)
            try:
                self._create_junction(ancestor, outside)
            except OSError as error:
                self.skipTest(f"junction creation unavailable: {error}")
            invoked = False

            def mutate():
                nonlocal invoked
                invoked = True
                (root / "escaped.txt").write_bytes(b"escaped\n")

            with self.assertRaisesRegex(
                service.ContainmentOperationError,
                "reparse|redirect|direct directory",
            ):
                self._delegated_receipt(root, mutate)

            self.assertFalse(invoked)
            self.assertFalse((outside / "root" / "escaped.txt").exists())

    def test_delegated_mutation_rejects_nested_junction_before_delegate(self):
        with tempfile.TemporaryDirectory(prefix="modlab-effect-nested-junction-") as directory:
            base = Path(directory)
            root = base / "root"
            outside = base / "outside"
            nested = root / "redirect"
            root.mkdir()
            outside.mkdir()
            try:
                self._create_junction(nested, outside)
            except OSError as error:
                self.skipTest(f"junction creation unavailable: {error}")
            invoked = False

            def mutate():
                nonlocal invoked
                invoked = True
                (nested / "escaped.txt").write_bytes(b"escaped\n")

            with self.assertRaisesRegex(
                service.ContainmentOperationError,
                "reparse|redirect|direct directory",
            ):
                self._delegated_receipt(root, mutate)

            self.assertFalse(invoked)
            self.assertFalse((outside / "escaped.txt").exists())

    def test_mutation_observation_rejects_directory_swap_during_enumeration(self):
        with tempfile.TemporaryDirectory(prefix="modlab-effect-directory-swap-") as directory:
            base = Path(directory)
            root = base / "root"
            displaced = base / "displaced"
            root.mkdir()
            (root / "before.txt").write_bytes(b"before\n")
            real_scandir = service.os.scandir
            swapped = False
            attempted = False

            def swap_then_scan(path):
                nonlocal attempted, swapped
                if not swapped and Path(path) == root:
                    attempted = True
                    root.rename(displaced)
                    swapped = True
                    root.mkdir()
                    (root / "after.txt").write_bytes(b"after\n")
                return real_scandir(path)

            with patch.object(service.os, "scandir", side_effect=swap_then_scan):
                observation = service._mutation_root_observation(root)

            self.assertTrue(attempted)
            self.assertIsNone(observation)

    def test_delegated_mutation_reports_root_then_fails_if_delegate_creates_reparse(self):
        with tempfile.TemporaryDirectory(prefix="modlab-effect-created-junction-") as directory:
            base = Path(directory)
            root = base / "root"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()

            def mutate():
                self._create_junction(root / "redirect", outside)
                return "created"

            try:
                with self.assertRaisesRegex(
                    service.ContainmentOperationError,
                    "reparse|redirect|effect observation",
                ) as raised:
                    self._delegated_receipt(root, mutate)
            except OSError as error:
                self.skipTest(f"junction creation unavailable: {error}")

            self.assertEqual((root,), raised.exception.effects.child_mutation_roots)
            self.assertEqual((root,), raised.exception.effects.written_paths)

    def test_delegated_root_cannot_be_swapped_in_pre_delegate_gap(self):
        with tempfile.TemporaryDirectory(prefix="modlab-effect-gap-swap-") as directory:
            base = Path(directory)
            root = base / "root"
            displaced = base / "displaced"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            swap_succeeded = False

            def mutate():
                nonlocal swap_succeeded
                try:
                    root.rename(displaced)
                    self._create_junction(root, outside)
                except OSError:
                    (root / "direct.txt").write_bytes(b"direct\n")
                    return
                swap_succeeded = True
                (root / "escaped.txt").write_bytes(b"escaped\n")

            receipt = self._delegated_receipt(root, mutate)

            self.assertFalse(swap_succeeded)
            self.assertFalse((outside / "escaped.txt").exists())
            self.assertEqual((root,), receipt.effects.child_mutation_roots)

    def test_delegated_root_ancestor_cannot_be_swapped_during_delegate(self):
        with tempfile.TemporaryDirectory(prefix="modlab-effect-ancestor-swap-") as directory:
            base = Path(directory)
            parent = base / "parent"
            root = parent / "root"
            displaced = base / "displaced-parent"
            outside = base / "outside"
            root.mkdir(parents=True)
            outside.mkdir()
            swap_succeeded = False

            def mutate():
                nonlocal swap_succeeded
                try:
                    parent.rename(displaced)
                    parent.mkdir()
                    self._create_junction(parent / "root", outside)
                except OSError:
                    (root / "direct.txt").write_bytes(b"direct\n")
                    return
                swap_succeeded = True
                (parent / "root" / "escaped.txt").write_bytes(b"escaped\n")

            receipt = self._delegated_receipt(root, mutate)

            self.assertFalse(swap_succeeded)
            self.assertFalse((outside / "escaped.txt").exists())
            self.assertEqual((root,), receipt.effects.child_mutation_roots)

    def test_delegated_guard_rejects_ancestor_swap_during_root_acquisition(self):
        with tempfile.TemporaryDirectory(prefix="modlab-effect-acquire-swap-") as directory:
            base = Path(directory)
            parent = base / "parent"
            root = parent / "root"
            displaced = base / "displaced-parent"
            alternate = base / "alternate-parent"
            alternate_root = alternate / "root"
            root.mkdir(parents=True)
            alternate_root.mkdir(parents=True)
            real_pin = service.pin_stable_direct_object
            swapped = False
            invoked = False

            def swap_then_pin(path, kind, **kwargs):
                nonlocal swapped
                if not swapped and Path(path) == root:
                    parent.rename(displaced)
                    alternate.rename(parent)
                    swapped = True
                return real_pin(path, kind, **kwargs)

            def mutate():
                nonlocal invoked
                invoked = True
                (root / "escaped.txt").write_bytes(b"escaped\n")

            with (
                patch.object(
                    service,
                    "pin_stable_direct_object",
                    side_effect=swap_then_pin,
                ),
                self.assertRaisesRegex(
                    service.ContainmentOperationError,
                    "identity|changed|observation",
                ),
            ):
                self._delegated_receipt(root, mutate)

            self.assertTrue(swapped)
            self.assertFalse(invoked)
            self.assertFalse((parent / "root" / "escaped.txt").exists())

    def test_delegated_observation_failure_preserves_store_ownership_type(self):
        with tempfile.TemporaryDirectory(prefix="modlab-effect-ownership-") as directory:
            root = Path(directory)
            ownership = ContainmentStoreOwnershipError(
                "retained exact ownership",
                object(),
            )
            with (
                patch.object(
                    service,
                    "_mutation_root_observation",
                    side_effect=((('before',),), None),
                ),
                self.assertRaises(ContainmentStoreOwnershipError) as raised,
            ):
                self._delegated_receipt(root, lambda: (_ for _ in ()).throw(ownership))

            self.assertIs(ownership, raised.exception)
            self.assertTrue(
                any("effect observation" in note for note in getattr(ownership, "__notes__", ()))
            )

    def test_effect_receipt_rejects_relative_paths(self):
        cases = (
            {"written_paths": (Path("relative.json"),)},
            {
                "written_paths": (Path("relative-root"),),
                "child_mutation_roots": (Path("relative-root"),),
            },
        )
        for supplied in cases:
            with self.subTest(supplied=supplied), self.assertRaisesRegex(
                ValueError,
                "absolute",
            ):
                service.ContainmentEffects(**supplied)

    def test_effect_merge_preserves_repeated_preparation_launches_in_order(self):
        launched = r"C:\tools\bsdtar.exe [list-names] PID: 42"

        merged = service.ContainmentEffects.merged(
            service.ContainmentEffects(preparation_processes=(launched,)),
            service.ContainmentEffects(preparation_processes=(launched,)),
        )

        self.assertEqual((launched, launched), merged.preparation_processes)
        self.assertEqual((launched, launched), merged.launched_processes)

    def test_launch_helper_failure_after_creation_preserves_mo2_pid(self):
        with tempfile.TemporaryDirectory(prefix="modlab-launch-created-failure-") as directory:
            root = Path(directory)
            store = ContainmentStore(root / "validation")
            record = write_run_identity(
                store,
                RUN_ID,
                root / "source-workspace",
                root / "steam",
                "artifact:created-failure",
            )[0]
            armed = store.create(
                ScenarioJournal(
                    1,
                    RUN_ID,
                    record.scenario,
                    ScenarioState.ARMED,
                    str(record.source_root),
                    str(record.stage_root),
                    str(record.archive_path),
                    "Protected Existing",
                    "ModLab Spike New",
                    protected(),
                    42,
                    None,
                    None,
                )
            )

            def fail_after_creation(*_args, on_created, **_kwargs):
                on_created(6060)
                raise OSError("injected post-create helper failure")

            with (
                patch.object(service, "_load_fixture_record", return_value=record),
                patch.object(service, "_watcher_live", return_value=True),
                patch.object(service, "_capture_protected", return_value=protected()),
                patch.object(service, "read_windows_file_version", return_value="2.5.2.0"),
                patch.object(
                    service,
                    "launch_low_integrity_process",
                    side_effect=fail_after_creation,
                ),
                self.assertRaises(service.ContainmentOperationError) as raised,
            ):
                service.launch_scenario(store.root, RUN_ID, armed.scenario)

            self.assertEqual(6060, raised.exception.effects.mo2_pid)
            self.assertEqual(
                (store.journal_path(RUN_ID, record.scenario),),
                raised.exception.effects.written_paths,
            )
            self.assertEqual(
                6060,
                store.load_journal(RUN_ID, record.scenario).mo2_pid,
            )

    def test_launch_evidence_ownership_failure_is_not_flattened_or_transitioned(self):
        with tempfile.TemporaryDirectory(prefix="modlab-launch-evidence-ownership-") as directory:
            root = Path(directory)
            store = ContainmentStore(root / "validation")
            record = write_run_identity(
                store,
                RUN_ID,
                root / "source-workspace",
                root / "steam",
                "artifact:launch-ownership",
            )[0]
            armed = store.create(
                ScenarioJournal(
                    1,
                    RUN_ID,
                    record.scenario,
                    ScenarioState.ARMED,
                    str(record.source_root),
                    str(record.stage_root),
                    str(record.archive_path),
                    "Protected Existing",
                    "ModLab Spike New",
                    protected(),
                    42,
                    None,
                    None,
                )
            )
            native_launch = SimpleNamespace(
                pid=7070,
                executable=str(record.executable),
                arguments=("--profile", "ModLab - Lab"),
                working_directory=str(record.stage_app),
                integrity=service.IntegrityLevel.LOW,
                creation_time=987654321,
            )
            process_observation = SimpleNamespace(
                complete=True,
                relevant=(
                    SimpleNamespace(pid=7070, executable_path=record.executable),
                ),
            )
            ownership = ContainmentStoreOwnershipError(
                "injected launch evidence ownership",
                object(),
            )

            def launch_for_test(*_args, on_created, **_kwargs):
                on_created(native_launch.pid)
                return native_launch

            with (
                patch.object(service, "_load_fixture_record", return_value=record),
                patch.object(service, "_watcher_live", return_value=True),
                patch.object(service, "_capture_protected", return_value=protected()),
                patch.object(service, "read_windows_file_version", return_value="2.5.2.0"),
                patch.object(
                    service,
                    "launch_low_integrity_process",
                    side_effect=launch_for_test,
                ),
                patch.object(
                    service,
                    "inspect_process_integrity",
                    return_value=service.IntegrityLevel.LOW,
                ),
                patch.object(
                    service,
                    "inspect_mo2_processes",
                    return_value=process_observation,
                ),
                patch.object(
                    ContainmentStore,
                    "write_launch_evidence",
                    side_effect=ownership,
                ),
                self.assertRaises(ContainmentStoreOwnershipError) as raised,
            ):
                service.launch_scenario(store.root, RUN_ID, armed.scenario)

            self.assertIs(ownership, raised.exception)
            self.assertEqual(7070, ownership.effects.mo2_pid)
            self.assertEqual(
                (store.journal_path(RUN_ID, record.scenario),),
                ownership.effects.written_paths,
            )
            self.assertIs(
                ScenarioState.SCENARIO_STARTED,
                store.load_journal(RUN_ID, record.scenario).state,
            )


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
            "Protected Existing",
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
            projection_observation_complete=True,
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
            recovered = recover_scenario(self.root, RUN_ID, self.journal.scenario)
        recovery = recovered.value
        self.assertEqual(ScenarioCleanupStatus.SUCCEEDED, recovery.cleanup_status)
        self.assertTrue(recovery.fresh_run_permitted)
        self.assertIsNotNone(recovery.result_id)
        authority = self.store.load_retry_authority(RUN_ID, self.journal.scenario)
        self.assertEqual("Available", authority["state"])
        self.assertEqual(
            fingerprint,
            authority["commandFingerprint"],
        )
        self.assertEqual(
            (
                self.store.journal_path(RUN_ID, self.journal.scenario),
                self.store.result_path(RUN_ID, self.journal.scenario),
                self.store.scenario_path(RUN_ID, self.journal.scenario)
                / "recovery.json",
                self.store.retry_path(RUN_ID, self.journal.scenario),
            ),
            recovered.effects.written_paths,
        )

    def test_cleanup_refusal_never_starts_fresh_run(self):
        cleanup = RecoveryCleanupEvidence(
            watch_outcome=None,
            protected_after=protected(),
            source_integrity=IntegrityObservation.MEDIUM,
            stage_integrity=IntegrityObservation.UNKNOWN,
            projection_count=0,
            projection_targets_verified=False,
            projection_observation_complete=False,
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
            recovered = recover_scenario(self.root, RUN_ID, self.journal.scenario)
        recovery = recovered.value
        self.assertEqual(ScenarioCleanupStatus.REFUSED, recovery.cleanup_status)
        cleanup.assert_not_called()
        self.assertEqual(quarantine_before, tuple(self.store.quarantine_path(RUN_ID).iterdir()))
        self.assertEqual(
            (
                self.store.journal_path(RUN_ID, self.journal.scenario),
                self.store.scenario_path(RUN_ID, self.journal.scenario)
                / "recovery.json",
            ),
            recovered.effects.written_paths,
        )
        self.assertEqual((), recovered.effects.child_mutation_roots)

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

    def test_scenario_started_without_launch_evidence_is_permanently_refused(self):
        started = self.store.transition(
            self.journal,
            ScenarioState.SCENARIO_STARTED,
        )
        record = fixture_record(self.root, started.scenario)
        write_bound_watch_evidence(self.store, started, events=(event(),))
        quarantine = self.store.quarantine_path(RUN_ID)
        before = tuple(quarantine.iterdir())
        with (
            patch.object(service, "_load_fixture_record", return_value=record),
            patch.object(
                service._windows_watch,
                "_exact_process_status",
                return_value=("dead", None, "exact process is absent"),
            ),
            patch.object(
                service,
                "inspect_mo2_processes",
                return_value=ProcessObservation(True, (), None),
            ),
            patch.object(service, "_capture_protected", return_value=protected()),
            patch.object(
                service,
                "_perform_recovery_cleanup",
                side_effect=AssertionError("stage cleanup must never begin"),
            ) as cleanup,
        ):
            recovery = recover_scenario(self.root, RUN_ID, started.scenario)

        self.assertEqual(ScenarioCleanupStatus.REFUSED, recovery.cleanup_status)
        self.assertFalse(recovery.fresh_run_permitted)
        self.assertIsNotNone(recovery.result_id)
        self.assertIn("mo2-launch-proof-unavailable:missing", recovery.blockers)
        self.assertEqual(before, tuple(quarantine.iterdir()))
        cleanup.assert_not_called()
        self.assertEqual(
            ScenarioOutcome.FAILED,
            self.store.load_result(RUN_ID, started.scenario).outcome,
        )

    def test_started_damaged_or_unbound_launch_proof_refuses_without_mutation(self):
        for damage in ("damaged", "unbound"):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory(
                prefix=f"modlab-started-launch-{damage}-"
            ) as directory:
                root = Path(directory)
                store = ContainmentStore(root)
                record = fixture_record(root, ContainmentScenario.MERGE_EXISTING)
                prepared = store.create(
                    ScenarioJournal(
                        1,
                        RUN_ID,
                        record.scenario,
                        ScenarioState.PREPARED,
                        str(record.source_root),
                        str(record.stage_root),
                        str(record.archive_path),
                        "Protected Existing",
                        "Protected Existing",
                        protected(),
                        None,
                        None,
                        None,
                    )
                )
                armed = store.transition(
                    prepared,
                    ScenarioState.ARMED,
                    monitor_pid=42,
                )
                started = store.transition(armed, ScenarioState.SCENARIO_STARTED)
                journal = (
                    store.transition(started, ScenarioState.LAUNCHED, mo2_pid=52)
                    if damage == "unbound"
                    else started
                )
                write_bound_watch_evidence(store, journal, events=(event(),))
                if damage == "damaged":
                    store.launch_path(RUN_ID, record.scenario).write_bytes(b"damaged\n")
                else:
                    store.write_launch_evidence(
                        RUN_ID,
                        record.scenario,
                        {
                            "schemaVersion": 1,
                            "runId": RUN_ID,
                            "scenario": record.scenario.value,
                            "purpose": "stage-mo2-scenario",
                            "pid": 51,
                            "creationTime": 987654321,
                            "executable": str(record.executable),
                            "executableVersion": "2.5.2.0",
                            "arguments": ["--profile", "ModLab - Lab"],
                            "workingDirectory": str(record.stage_app),
                            "integrity": IntegrityObservation.LOW.value,
                        },
                    )
                quarantine = store.quarantine_path(RUN_ID)
                before = tuple(quarantine.iterdir())
                with (
                    patch.object(service, "_load_fixture_record", return_value=record),
                    patch.object(
                        service._windows_watch,
                        "_exact_process_status",
                        return_value=("dead", None, "exact process is absent"),
                    ),
                    patch.object(
                        service,
                        "inspect_mo2_processes",
                        return_value=ProcessObservation(True, (), None),
                    ),
                    patch.object(service, "_capture_protected", return_value=protected()),
                    patch.object(
                        service,
                        "_perform_recovery_cleanup",
                        side_effect=AssertionError("stage cleanup must never begin"),
                    ) as cleanup,
                ):
                    recovery = recover_scenario(store.root, RUN_ID, record.scenario)

                self.assertEqual(ScenarioCleanupStatus.REFUSED, recovery.cleanup_status)
                self.assertIsNotNone(recovery.result_id)
                self.assertEqual(before, tuple(quarantine.iterdir()))
                cleanup.assert_not_called()

    def test_prove_recovery_parses_exact_task4_bindings_and_rejects_mismatch(self):
        record = fixture_record(self.root, self.journal.scenario)
        observed = write_bound_watch_evidence(self.store, self.journal)
        with (
            patch.object(
                service._windows_watch,
                "_exact_process_status",
                return_value=("dead", None, "exact process is absent"),
            ),
            patch.object(
                service,
                "inspect_mo2_processes",
                return_value=ProcessObservation(True, (), None),
            ),
            patch.object(service, "_capture_protected", return_value=protected()),
        ):
            proved = service._prove_recovery(self.store, record, self.journal)
            self.assertEqual((), proved.blockers)
            self.assertEqual(observed, proved.watch_outcome)

            request_path = self.store.watch_path(
                RUN_ID, self.journal.scenario
            ) / "request.json"
            request = service.watch_request_from_bytes(request_path.read_bytes())
            mismatched = WorkerLaunch(
                1,
                watch_request_sha256(request),
                request.session_id,
                request.run_id,
                request.scenario,
                (self.journal.monitor_pid or 42) + 1,
                1002,
            )
            (request.evidence_root / LAUNCH_NAME).write_bytes(
                worker_launch_to_bytes(mismatched, request)
            )
            refused = service._prove_recovery(self.store, record, self.journal)

        self.assertIsNone(refused.watch_outcome)
        self.assertTrue(
            any("watch-proof-unavailable" in item for item in refused.blockers)
        )

    def test_recovery_process_access_uncertainty_refuses_without_cleanup(self):
        record = fixture_record(self.root, self.journal.scenario)
        write_bound_watch_evidence(self.store, self.journal)
        before = tuple(self.store.quarantine_path(RUN_ID).iterdir())
        with (
            patch.object(service, "_load_fixture_record", return_value=record),
            patch.object(
                service._windows_watch,
                "_exact_process_status",
                return_value=("dead", None, "exact process is absent"),
            ),
            patch.object(
                service,
                "inspect_mo2_processes",
                side_effect=OSError("access denied"),
            ),
            patch.object(service, "_capture_protected", return_value=protected()),
            patch.object(
                service,
                "_perform_recovery_cleanup",
                side_effect=AssertionError("cleanup mutation must not begin"),
            ) as cleanup,
        ):
            recovery = recover_scenario(
                self.store.root,
                RUN_ID,
                self.journal.scenario,
            )

        self.assertEqual(ScenarioCleanupStatus.REFUSED, recovery.cleanup_status)
        self.assertIn("mo2-process-proof-unavailable:OSError", recovery.blockers)
        self.assertEqual(before, tuple(self.store.quarantine_path(RUN_ID).iterdir()))
        cleanup.assert_not_called()

    def test_reprove_retry_absence_uses_durable_exact_bindings_after_restart(self):
        old_run = "containment-run:" + "9" * 32
        new_run = "containment-run:" + "a" * 32
        scenario = ContainmentScenario.MERGE_EXISTING
        source = self.root / "retry-source"
        steam = self.root / "retry-steam"
        artifact = "artifact:retry-proof"
        records = write_run_identity(
            self.store,
            old_run,
            source,
            steam,
            artifact,
        )
        record = records[list(ContainmentScenario).index(scenario)]
        journal = self.store.create(
            ScenarioJournal(
                1,
                old_run,
                scenario,
                ScenarioState.ARMED,
                str(record.source_root),
                str(record.stage_root),
                str(record.archive_path),
                "Protected Existing",
                "Protected Existing",
                protected(),
                42,
                None,
                None,
            )
        )
        observed = write_bound_watch_evidence(
            self.store,
            journal,
            completion=WatchEvidenceCompletion.INCOMPLETE,
        )
        result = evaluate_scenario(
            replace(
                evidence(scenario),
                run_id=old_run,
                watch_outcome=observed,
                scenario_started=False,
                fresh_retry_eligible=True,
            )
        )
        result_write = self.store.write_result(result)
        recovery = ScenarioRecovery(
            1,
            old_run,
            scenario,
            self.store.journal_id_for(journal),
            result_write.content_id,
            ScenarioCleanupStatus.SUCCEEDED,
            True,
            (),
        )
        recovery_write = self.store.write_recovery(recovery)
        fingerprint = service._command_fingerprint(source, artifact, steam)
        self.store.write_retry_authority(
            recovery,
            recovery_write.content_id,
            fingerprint,
        )
        binding = self.store.consume_retry_authority(
            recovery,
            recovery_write.content_id,
            fingerprint,
            new_run,
        )

        with (
            patch.object(
                service._windows_watch,
                "_exact_process_status",
                return_value=("dead", None, "exact process is absent"),
            ),
            patch.object(
                service,
                "inspect_mo2_processes",
                return_value=ProcessObservation(True, (), None),
            ),
            patch.object(service, "_capture_protected", return_value=protected()),
        ):
            service._reprove_retry_absence(self.store, new_run, binding)

            request_path = self.store.watch_path(old_run, scenario) / "request.json"
            request = service.watch_request_from_bytes(request_path.read_bytes())
            mismatched = WorkerLaunch(
                1,
                watch_request_sha256(request),
                request.session_id,
                request.run_id,
                request.scenario,
                99,
                1002,
            )
            (request.evidence_root / LAUNCH_NAME).write_bytes(
                worker_launch_to_bytes(mismatched, request)
            )
            with self.assertRaisesRegex(
                service.ContainmentServiceError,
                "absence is not exact",
            ):
                service._reprove_retry_absence(self.store, new_run, binding)

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
            projection_observation_complete=True,
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
