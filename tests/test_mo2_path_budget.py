"""Operation-path regressions exercise actual mutation entry points."""
import hashlib
import tempfile
import unittest
import os
import shutil
import stat
from dataclasses import replace
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace

from modlab.adapters.mo2.archive import Mo2ArchiveError, extract_and_inventory
from tests.support.mo2_bootstrap import BootstrapPlanningFixture, FakeRunner, descriptor_for_package, extraction_package_files


class PathBudgetEntryPointTests(unittest.TestCase):
    def test_refused_prepare_preserves_absent_validation_and_workspace_branches(self):
        from modlab.validation import mo2_containment_service as service
        from modlab.workspace import initialize_workspace
        from modlab.artifacts.vault import ArchiveVault
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            layout = initialize_workspace(root / "workspace")
            archive = root / "Mod.Organizer-2.5.2.7z"
            archive.write_bytes(b"not the supported release")
            artifact = ArchiveVault(layout.root).import_archive(archive, source_note="unsupported")
            layout.mo2_containment_validation.rmdir()
            layout.skyrim_mo2_downloads.rmdir()
            before = {str(p.relative_to(layout.root)): p.read_bytes() for p in layout.root.rglob("*") if p.is_file()}
            with self.assertRaises(service.ContainmentServiceError):
                service.prepare_run(layout.root, artifact.artifact_id, root / "Steam", layout.mo2_containment_validation)
            self.assertFalse(layout.mo2_containment_validation.exists())
            self.assertFalse(layout.skyrim_mo2_downloads.exists())
            self.assertEqual(before, {str(p.relative_to(layout.root)): p.read_bytes() for p in layout.root.rglob("*") if p.is_file()})

    def test_archive_deep_destination_refuses_without_creating_stage(self):
        # Without complete member admission, a short -C root hides a >512-unit leaf.
        files = extraction_package_files()
        files["/".join(["nested" * 10] * 8) + "/leaf.bin"] = b"deep"
        descriptor = descriptor_for_package(files)
        runner = FakeRunner.for_listing(sorted(files), ["-"] * len(files))
        runner.responses["-xf"] = CompletedProcess((), 0, b"", b"")
        runner.extraction_files = files
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary) / "app"
            with self.assertRaisesRegex(Mo2ArchiveError, "path budget"):
                extract_and_inventory(Path("payload.7z"), descriptor, Path("tar.exe"),
                                      stage, runner=runner, free_space_reader=lambda _: 2**40)
            self.assertFalse(stage.exists())

    def test_new_bootstrap_plan_explicitly_selects_new_layout(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BootstrapPlanningFixture(Path(temporary))
            planned = fixture.prepare()
            self.assertEqual(2, planned.plan.schema_version)
            self.assertEqual(".s<job-base32>", planned.plan.staging_name_template)
            from modlab.adapters.mo2.bootstrap_serialization import plan_from_bytes, plan_to_bytes
            restored = plan_from_bytes(plan_to_bytes(planned.plan))
            self.assertFalse(restored.path_budget.runtime_qualified)
            self.assertEqual("mo2-preparation-only", restored.path_budget.scope)
            self.assertTrue(any("recovered-activated" in row.path for row in restored.path_budget.paths))
            self.assertTrue(any(row.consumer == "tar-cwd" and ".saaaaaaaaaaaaaaaaaaaaaaaaaa" in row.path
                                for row in restored.path_budget.paths))

    def test_schema_two_budget_error_is_mapped_before_job_or_stage_creation(self):
        from unittest.mock import patch
        from modlab.adapters.mo2.archive import preflight_archive
        from modlab.adapters.mo2.path_budget import PathBudgetError
        from modlab.workflows.skyrim.mo2_bootstrap_store import (
            Mo2BootstrapStore,
            Mo2BootstrapStoreError,
        )
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BootstrapPlanningFixture(Path(temporary))
            planned = fixture.prepare()
            listing = preflight_archive(
                Path("payload.7z"),
                fixture.release.descriptor,
                Path("tar.exe"),
                runner=fixture.runner,
            )
            job_id = "bootstrap-job:" + "1" * 32
            store = Mo2BootstrapStore(
                fixture.workspace,
                job_id_factory=lambda: job_id,
            )
            with patch(
                "modlab.workflows.skyrim.mo2_bootstrap_store.bootstrap_paths",
                side_effect=PathBudgetError("path budget: injected one-over"),
            ), self.assertRaisesRegex(Mo2BootstrapStoreError, "path budget"):
                store.create_job(planned.plan, listing=listing)
            self.assertFalse(store.job_directory(job_id).exists())
            self.assertFalse(store.stage_root(job_id, 2).exists())

    def test_apply_path_refusal_creates_no_job_stage_or_marker(self):
        from datetime import datetime, timezone
        from unittest.mock import patch
        from modlab.adapters.mo2.path_budget import PathBudgetError
        from modlab.workflows.skyrim import mo2_bootstrap
        from modlab.workflows.skyrim.mo2_bootstrap import (
            Mo2BootstrapRefusal,
            apply_mo2_setup,
        )
        from modlab.workflows.skyrim.mo2_bootstrap_store import Mo2BootstrapStore
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BootstrapPlanningFixture(Path(temporary))
            planned = fixture.prepare()
            fixture.runner.calls.clear()
            fixture.runner.responses["-xf"] = CompletedProcess((), 0, b"", b"")
            fixture.runner.extraction_files = dict(fixture.package_files)
            job_id = "bootstrap-job:" + "4" * 32
            store = Mo2BootstrapStore(fixture.workspace)
            before = {
                path.relative_to(fixture.workspace).as_posix(): path.read_bytes()
                for path in fixture.workspace.rglob("*")
                if path.is_file()
            }
            with (
                patch.object(mo2_bootstrap, "load_mo2_release", return_value=fixture.release),
                patch(
                    "modlab.workflows.skyrim.mo2_bootstrap_store.bootstrap_paths",
                    side_effect=PathBudgetError("path budget: injected apply one-over"),
                ),
                self.assertRaisesRegex(Mo2BootstrapRefusal, "path budget") as raised,
            ):
                apply_mo2_setup(
                    planned.plan.plan_id,
                    fixture.workspace,
                    release_path=fixture.release_path,
                    extractor_path=Path(r"C:\Windows\System32\tar.exe"),
                    documents_root=fixture.documents_root,
                    version_reader=fixture.version_reader,
                    process_inspector=lambda _: fixture.processes,
                    command_runner=fixture.runner,
                    free_space_reader=lambda _: fixture.free_bytes,
                    clock=lambda: datetime(2026, 9, 4, tzinfo=timezone.utc),
                    job_id_factory=lambda: job_id,
                )
            self.assertEqual("journal-invalid", raised.exception.code)
            self.assertFalse(store.job_directory(job_id).exists())
            self.assertFalse(store.stage_root(job_id, 2).exists())
            self.assertEqual(
                before,
                {
                    path.relative_to(fixture.workspace).as_posix(): path.read_bytes()
                    for path in fixture.workspace.rglob("*")
                    if path.is_file()
                },
            )

    def test_containment_prepare_one_over_refuses_before_root_or_record_creation(self):
        from unittest.mock import patch
        from modlab.adapters.mo2.path_budget import PlannedPath, admit_paths
        from modlab.validation import mo2_containment_service as service
        from modlab.workspace import workspace_layout
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            source = parent.joinpath(*(("w" * 100,) * 5))
            validation = workspace_layout(source).mo2_containment_validation
            fixture_budget = admit_paths(
                [PlannedPath("fixture", "preparation-wide", r"C:\fixture")]
            )
            with patch.object(
                service,
                "preflight_containment_fixture",
                return_value=fixture_budget,
            ), self.assertRaisesRegex(service.ContainmentServiceError, "path budget"):
                service.prepare_run(
                    source,
                    "archive-sha256:" + "a" * 64,
                    parent / "Steam",
                    validation,
                    _admitted_run_id="containment-run:" + "2" * 32,
                )
            self.assertFalse(source.exists())
            self.assertFalse(validation.exists())


class PathBudgetPolicyTests(unittest.TestCase):
    @staticmethod
    def _relocation_rows(*relative_paths, volume=7):
        directory_mode = stat.S_IFDIR | 0o755
        file_mode = stat.S_IFREG | 0o644
        rows = [
            (".", "directory", volume, 1, directory_mode, 0, 0, 0, 0, None)
        ]
        for index, relative in enumerate(relative_paths, start=2):
            mode = file_mode if "." in Path(relative).name else directory_mode
            kind = "file" if mode == file_mode else "directory"
            rows.append(
                (
                    relative,
                    kind,
                    volume,
                    index,
                    mode,
                    1,
                    1,
                    0,
                    0,
                    "a" * 64 if kind == "file" else None,
                )
            )
        return tuple(rows)

    def test_relocation_admission_binds_actual_tree_and_every_destination_member(self):
        from unittest.mock import patch
        from modlab.validation import mo2_containment_service as service
        record = SimpleNamespace(
            scenario=service.ContainmentScenario.NEW_FOLDER,
            source_mods=Path(r"C:\run\s\mods"),
            stage_mods=Path(r"C:\run\t\mods"),
            before_names=("Protected Existing",),
        )
        source_rows = self._relocation_rows("Protected Existing", "Protected Existing/marker.txt")
        stage_rows = (
            self._relocation_rows()[0],
            *self._relocation_rows(
                "ModLab Spike New",
                "ModLab Spike New/meshes",
                "ModLab Spike New/meshes/new-folder.bin",
                "ModLab Spike New/meta.ini",
            )[1:],
            (
                "Protected Existing",
                "junction",
                7,
                2,
                None,
                None,
                None,
                None,
                None,
                "a" * 64,
            ),
        )
        quarantine = Path(r"C:\run\quarantine\NewFolder")
        with (
            patch.object(service, "_mutation_root_observation", side_effect=(source_rows, stage_rows)),
            patch.object(service, "_relocation_destination_volume", return_value=7),
        ):
            admission = service._preflight_projection_relocation(record, quarantine)
        paths = {row.path for row in admission.budget.paths}
        for root in (
            record.stage_mods / "ModLab Spike New",
            record.source_mods / "ModLab Spike New",
            quarantine / "ModLab Spike New",
        ):
            self.assertIn(str(root), paths)
            self.assertIn(str(root / "meshes/new-folder.bin"), paths)
            self.assertIn(str(root / "meta.ini"), paths)
        self.assertEqual(("ModLab Spike New", "Protected Existing"), admission.stage_names)
        self.assertEqual(("ModLab Spike New",), admission.mutation_names)
        self.assertEqual(
            (
                (
                    "ModLab Spike New",
                    (
                        str(record.source_mods / "ModLab Spike New"),
                        str(quarantine / "ModLab Spike New"),
                    ),
                ),
            ),
            admission.destination_map,
        )
        self.assertEqual(stage_rows, admission.stage_rows)
        self.assertRegex(admission.sha256, r"^[0-9a-f]{64}$")

    def test_relocation_admission_observes_the_bound_projection_without_reenumerating(self):
        from unittest.mock import patch
        from modlab.validation import mo2_containment_service as service
        record = SimpleNamespace(
            scenario=service.ContainmentScenario.NEW_FOLDER,
            source_mods=Path(r"C:\run\s\mods"),
            stage_mods=Path(r"C:\run\t\mods"),
            before_names=("Protected Existing",),
        )
        owner = SimpleNamespace(
            verify=lambda: None,
            evidence=SimpleNamespace(
                link_path=record.stage_mods / "Protected Existing",
                target_path=record.source_mods / "Protected Existing",
            ),
            payload=b"canonical reparse payload",
            pins=(
                SimpleNamespace(
                    path=record.stage_mods / "Protected Existing",
                    identity=(7, 2),
                ),
                SimpleNamespace(
                    path=record.source_mods / "Protected Existing",
                    identity=(7, 3),
                ),
                SimpleNamespace(path=record.source_mods, identity=(7, 4)),
                SimpleNamespace(path=record.stage_mods, identity=(7, 5)),
            ),
        )
        source_rows = self._relocation_rows(
            "Protected Existing", "Protected Existing/marker.txt"
        )
        stage_rows = (
            self._relocation_rows()[0],
            *self._relocation_rows(
                "ModLab Spike New", "ModLab Spike New/meta.ini"
            )[1:],
            (
                "Protected Existing",
                "junction",
                7,
                2,
                None,
                None,
                None,
                None,
                None,
                "a" * 64,
            ),
        )
        quarantine = Path(r"C:\run\quarantine\NewFolder")
        with (
            patch.object(
                service,
                "_mutation_root_observation",
                side_effect=(source_rows, stage_rows),
            ) as observe,
            patch.object(service, "_relocation_destination_volume", return_value=7),
        ):
            admission = service._preflight_projection_relocation(
                record,
                quarantine,
                expected_projections=(owner,),
            )
        self.assertEqual((), admission.changed_names)
        self.assertEqual(("ModLab Spike New",), admission.mutation_names)
        self.assertEqual(
            (
                str(record.stage_mods / "Protected Existing"),
                str(record.source_mods / "Protected Existing"),
            ),
            admission.projection_rows[0][:2],
        )
        self.assertEqual(
            [
                ((record.source_mods,), {"expected_projections": (owner,)}),
                ((record.stage_mods,), {"expected_projections": (owner,)}),
            ],
            [(call.args, call.kwargs) for call in observe.call_args_list],
        )

    def test_created_root_guard_threads_bound_projection_into_observation(self):
        from unittest.mock import patch
        from modlab.validation import mo2_containment_service as service
        owner = object()
        prepared = object()
        with (
            patch.object(service, "_direct_directory_present", return_value=True),
            patch.object(service, "_delegated_mutations", return_value="done") as delegated,
        ):
            result = service._delegated_mutations_with_created_root(
                Path(r"C:\run\quarantine"),
                (Path(r"C:\run\stage"),),
                lambda: prepared,
                lambda value: value,
                expected_projections=(owner,),
            )
        self.assertEqual("done", result)
        self.assertEqual(
            (owner,), delegated.call_args.kwargs["expected_projections"]
        )

    def test_recovery_relocation_refusal_stops_before_later_tree_mutation(self):
        from contextlib import nullcontext
        from unittest.mock import patch
        from modlab.validation import mo2_containment_service as service
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            watch_root = root / "watch"
            watch_root.mkdir()
            (watch_root / "request.json").write_bytes(b"request")
            quarantine = root / "quarantine"
            scenario = service.ContainmentScenario.NEW_FOLDER
            watch = SimpleNamespace(
                request_id="request",
                session_id="session",
                worker_pid=41,
                request_sha256="a" * 64,
            )
            receipt = SimpleNamespace(
                watch_outcome_id="outcome",
                run_id="containment-run:" + "a" * 32,
                scenario=scenario,
                request_id=watch.request_id,
                session_id=watch.session_id,
                worker_pid=watch.worker_pid,
                request_bytes_sha256=watch.request_sha256,
            )
            store = SimpleNamespace(
                watch_path=lambda _run_id, _scenario: watch_root,
                load_watch_outcome=lambda _run_id, _scenario, _outcome: watch,
                quarantine_path=lambda _run_id: quarantine,
            )
            record = SimpleNamespace(
                scenario=scenario,
                source_mods=root / "source-mods",
                stage_mods=root / "stage-mods",
                stage_app=root / "stage-app",
                stage_downloads=root / "stage-downloads",
                stage_profiles=root / "stage-profiles",
                stage_overwrite=root / "stage-overwrite",
                stage_cache=root / "stage-cache",
                stage_logs=root / "stage-logs",
            )
            protected = object()
            journal = SimpleNamespace(
                run_id=receipt.run_id,
                scenario=scenario,
                protected_before=protected,
            )
            proof = SimpleNamespace(watch_outcome=watch, protected_after=protected)
            with (
                patch.object(service, "_delegated_mutation", return_value=receipt),
                patch.object(
                    service,
                    "_retained_relocation_projections",
                    return_value=nullcontext(()),
                ),
                patch.object(
                    service,
                    "_delegated_mutations_with_created_root",
                    side_effect=service.ContainmentServiceError("path budget refused"),
                ),
                patch.object(service, "set_low_integrity_tree") as normalize,
            ):
                result = service._perform_recovery_cleanup(
                    store,
                    record,
                    journal,
                    proof=proof,
                )
            self.assertIn("staging-quarantine-failed", result.blockers)
            normalize.assert_not_called()
            self.assertFalse(quarantine.exists())

    def test_relocation_admission_rejects_long_collision_reparse_and_cross_volume(self):
        from unittest.mock import patch
        from modlab.validation import mo2_containment_service as service
        record = SimpleNamespace(
            scenario=service.ContainmentScenario.NEW_FOLDER,
            source_mods=Path(r"C:\run\s\mods"),
            stage_mods=Path(r"C:\run\t\mods"),
            before_names=("Protected Existing",),
        )
        quarantine = Path(r"C:\run\quarantine\NewFolder")
        source_rows = self._relocation_rows("Protected Existing")
        long_member = "ModLab Spike New/" + "/".join(("deep" * 20,) * 7) + "/leaf.bin"
        cases = (
            (self._relocation_rows("ModLab Spike New", long_member), 7, None, "path budget"),
            (self._relocation_rows("ModLab Spike New", "modlab spike new"), 7, None, "collision"),
            (service._MutationObservationError("reparse entry"), 7, None, "reparse"),
            (self._relocation_rows("ModLab Spike New", volume=8), 7, None, "volume"),
        )
        for stage_value, destination_volume, _unused, message in cases:
            with self.subTest(message=message):
                side_effect = (source_rows, stage_value)
                with (
                    patch.object(service, "_mutation_root_observation", side_effect=side_effect),
                    patch.object(service, "_relocation_destination_volume", return_value=destination_volume),
                    self.assertRaisesRegex(Exception, message),
                ):
                    service._preflight_projection_relocation(record, quarantine)

    def test_relocation_snapshot_revalidation_refuses_substitution(self):
        from unittest.mock import patch
        from modlab.validation import mo2_containment_service as service
        record = SimpleNamespace(
            scenario=service.ContainmentScenario.NEW_FOLDER,
            source_mods=Path(r"C:\run\s\mods"),
            stage_mods=Path(r"C:\run\t\mods"),
            before_names=("Protected Existing",),
        )
        source_rows = self._relocation_rows("Protected Existing")
        stage_rows = self._relocation_rows("ModLab Spike New", "ModLab Spike New/meta.ini")
        changed_rows = self._relocation_rows("ModLab Spike New", "ModLab Spike New/other.ini")
        quarantine = Path(r"C:\run\quarantine\NewFolder")
        with (
            patch.object(service, "_mutation_root_observation", side_effect=(source_rows, stage_rows)),
            patch.object(service, "_relocation_destination_volume", return_value=7),
        ):
            admission = service._preflight_projection_relocation(record, quarantine)
        with (
            patch.object(service, "_mutation_root_observation", side_effect=(source_rows, changed_rows)),
            patch.object(service, "_relocation_destination_volume", return_value=7),
            self.assertRaisesRegex(service.ContainmentServiceError, "relocation.*changed"),
        ):
            service._require_relocation_admission(record, quarantine, admission)

    def test_exact_consumer_limits_and_one_unit_over(self):
        from modlab.adapters.mo2.path_budget import PlannedPath, PathBudgetError, admit_paths
        for consumer, count in (("tar-cwd", 247), ("preparation-wide", 512)):
            prefix = "C:\\" + ("p" * 100 + "\\") * ((count - 3) // 101)
            path = prefix + "f" * (count - len(prefix))
            self.assertEqual(path, admit_paths([PlannedPath("test", consumer, path)]).paths[0].path)
            with self.assertRaisesRegex(PathBudgetError, "stage=test.*limiting component"):
                admit_paths([PlannedPath("test", consumer, path + "x")])

    def test_non_bmp_uses_two_units_and_components_are_bounded(self):
        from modlab.adapters.mo2.path_budget import PlannedPath, PathBudgetError, admit_paths
        path = "C:\\" + "a" * 242 + "\U0001f600"
        admit_paths([PlannedPath("unicode", "tar-cwd", path)])
        with self.assertRaises(PathBudgetError):
            admit_paths([PlannedPath("unicode", "tar-cwd", path + "x")])
        admit_paths([PlannedPath("component", "preparation-wide", "C:\\" + "x" * 255)])
        with self.assertRaisesRegex(PathBudgetError, "component"):
            admit_paths([PlannedPath("component", "preparation-wide", "C:\\" + "x" * 256)])

    def test_aliases_unknown_consumers_unbounded_patterns_are_unsupported(self):
        from modlab.adapters.mo2.path_budget import PlannedPath, PathBudgetError, admit_paths
        for path in (r"\\?\C:\long", r"\\server\share\x", "C:\\a\\..\\b", "C:\\a.\\b", r"C:\PROGRA~1\x", r"C:\nul.txt", r"C:\a:b", r"C:\a\\b"):
            with self.subTest(path=path), self.assertRaises(PathBudgetError):
                admit_paths([PlannedPath("alias", "preparation-wide", path)])
        with self.assertRaises(PathBudgetError):
            admit_paths([PlannedPath("native-MO2", "unknown", r"C:\x")])
        with self.assertRaises(PathBudgetError):
            admit_paths([PlannedPath("native-MO2", "preparation-wide", r"C:\x")], bounded=False)

    def test_budget_roundtrip_cannot_acquire_runtime_scope_or_reinterpret_legacy(self):
        from modlab.adapters.mo2.path_budget import PlannedPath, PathBudgetError, admit_paths, budget_to_dict, budget_from_dict
        budget = admit_paths([PlannedPath("prepare", "preparation-wide", r"C:\x")])
        encoded = budget_to_dict(budget)
        self.assertEqual(budget, budget_from_dict(encoded))
        for key, value in (("runtimeQualified", True), ("scope", "runtime"), ("policyVersion", True), ("extra", 1)):
            with self.assertRaises(PathBudgetError):
                budget_from_dict(dict(encoded, **{key: value}))

    def test_new_layout_full_identity_and_strict_legacy_plan_and_journal_roundtrips(self):
        from modlab.adapters.mo2.path_budget import stage_name
        from modlab.adapters.mo2.bootstrap_serialization import plan_to_bytes, plan_from_bytes, journal_to_bytes, journal_from_bytes
        from tests.support.mo2_bootstrap import make_plan_fixture, make_journal_fixture
        self.assertEqual(".saaaaaaaaaaaaaaaaaaaaaaaaaa", stage_name("bootstrap-job:" + "0" * 32, 2))
        self.assertNotEqual(stage_name("bootstrap-job:" + "0" * 32, 2), stage_name("bootstrap-job:" + "0" * 31 + "1", 2))
        for item, write, read in ((make_plan_fixture(), plan_to_bytes, plan_from_bytes),
                                 (make_journal_fixture(), journal_to_bytes, journal_from_bytes)):
            data = write(item)
            self.assertNotIn(b"pathBudget", data)
            self.assertEqual(data, write(read(data)))
            with self.assertRaises(ValueError):
                read(data.replace(b'"schemaVersion":1', b'"schemaVersion":2'))

    def test_schema_one_plan_and_journal_have_fixed_canonical_byte_identities(self):
        from modlab.adapters.mo2.bootstrap_serialization import plan_to_bytes, journal_to_bytes
        from tests.support.mo2_bootstrap import make_plan_fixture, make_journal_fixture
        expected = (
            (plan_to_bytes(make_plan_fixture()), 3657,
             "19a06a8e47ef46345efa06779ad01bbc44ea97e8052ed0a39317dc93792a5030"),
            (journal_to_bytes(make_journal_fixture()), 840,
             "6dc6f352012c0febf42ee33de47aabe4a6cda841ba8074057a57e1a97afec550"),
        )
        for data, size, digest in expected:
            self.assertEqual(size, len(data))
            self.assertEqual(digest, hashlib.sha256(data).hexdigest())
            self.assertNotIn(b'"pathBudget"', data)
            self.assertTrue(data.endswith(b"\n"))

    def test_bootstrap_budget_uses_exact_mutator_temp_names(self):
        from pathlib import PureWindowsPath
        from modlab.adapters.mo2.archive import ArchiveEntry, ArchiveListing
        from modlab.adapters.mo2.path_budget import bootstrap_paths
        canonical = b"ModOrganizer.exe\n"
        listing = ArchiveListing(
            (ArchiveEntry("ModOrganizer.exe", "file"),),
            hashlib.sha256(canonical).hexdigest(),
        )
        rows = bootstrap_paths(r"C:\ModLab\workspace", listing)
        mutable = {
            PureWindowsPath(row.path).name
            for row in rows
            if row.stage == "bootstrap-mutable-part"
        }
        self.assertEqual(
            {
                "plan-" + "f" * 32 + ".part",
                "receipt-" + "f" * 32 + ".part",
                "journal-" + "0" * 32 + "-" + "f" * 32 + ".part",
            },
            mutable,
        )

    def test_shared_publication_candidate_bounds_pid_thread_and_token(self):
        from unittest.mock import patch
        from modlab.platform import windows_exact_fs as exact_fs
        from modlab.platform.windows_exact_fs import (
            ExactObjectError,
            publication_candidate_path,
            publish_new_pinned,
        )
        destination = Path(r"C:\records\result.json")
        expected = destination.parent / (
            ".result.json.4294967295.4294967295.ffffffffffffffff.tmp"
        )
        self.assertEqual(
            expected,
            publication_candidate_path(
                destination,
                4294967295,
                4294967295,
                "f" * 16,
            ),
        )
        for pid, thread, token in (
            (4294967296, 1, "f" * 16),
            (1, 4294967296, "f" * 16),
            (1, 1, "f" * 17),
        ):
            with self.subTest(pid=pid, thread=thread, token=token), self.assertRaises(
                ExactObjectError
            ):
                publication_candidate_path(destination, pid, thread, token)
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "result.json"
            with (
                patch.object(exact_fs.os, "getpid", return_value=0x100000000),
                patch.object(exact_fs, "pin_direct_object") as pin,
                self.assertRaisesRegex(ExactObjectError, "unsigned DWORD"),
            ):
                publish_new_pinned(target, b"never", lambda data: data)
            pin.assert_not_called()
            self.assertFalse(target.exists())

    def test_archive_budget_rejects_forged_entry_spelling_kind_and_digest(self):
        from modlab.adapters.mo2.archive import ArchiveEntry, ArchiveListing
        from modlab.adapters.mo2.path_budget import PathBudgetError, archive_paths
        canonical = b"folder/\nfolder/file.bin\n"
        valid = ArchiveListing(
            (
                ArchiveEntry("folder", "directory"),
                ArchiveEntry("folder/file.bin", "file"),
            ),
            hashlib.sha256(canonical).hexdigest(),
        )
        self.assertTrue(archive_paths(valid, r"C:\stage"))
        forged = (
            replace(valid, entries=(ArchiveEntry("other", "directory"),
                                    ArchiveEntry("folder/file.bin", "file"))),
            replace(valid, entries=(ArchiveEntry("folder", "file"),
                                    ArchiveEntry("folder/file.bin", "file"))),
            replace(valid, canonical_sha256="f" * 64),
            ArchiveListing(
                (
                    ArchiveEntry("folder", "directory"),
                    ArchiveEntry("FOLDER", "directory"),
                ),
                hashlib.sha256(b"folder/\nFOLDER/\n").hexdigest(),
            ),
            SimpleNamespace(
                entries=(SimpleNamespace(relative_path="folder"),),
                canonical_sha256=hashlib.sha256(b"folder/\n").hexdigest(),
            ),
        )
        for listing in forged:
            with self.subTest(listing=listing), self.assertRaisesRegex(
                PathBudgetError, "archive listing identity"
            ):
                archive_paths(listing, r"C:\stage")

    def test_containment_budget_names_exact_records_replacements_and_quarantine(self):
        from unittest.mock import patch
        from modlab.adapters.mo2.path_budget import PlannedPath, admit_paths
        from modlab.validation import mo2_containment_service as service
        fixture_budget = admit_paths(
            [PlannedPath("fixture", "preparation-wide", r"C:\fixture")]
        )
        run_id = "containment-run:" + "3" * 32
        validation = Path(r"C:\validation")
        observed_calls = []
        def fixture_preflight(source, artifact, steam, validation_root, scenario, **kwargs):
            observed_calls.append((source, artifact, steam, validation_root, scenario, kwargs))
            return fixture_budget
        steam = Path(r"C:\Steam")
        with patch.object(service, "preflight_containment_fixture", side_effect=fixture_preflight):
            budget = service._preflight_preparation(
                Path(r"C:\source"),
                "archive-sha256:" + "a" * 64,
                steam,
                validation,
                run_id,
            )
        self.assertEqual(4, len(observed_calls))
        self.assertTrue(all(call[2] == steam for call in observed_calls))
        paths = {row.path for row in budget.paths}
        root = validation / ("3" * 32)
        for scenario, expected_name, members in (
            ("NewFolder", "ModLab Spike New", ("meshes/new-folder.bin", "meta.ini")),
            ("MergeExisting", "Protected Existing",
             ("marker.txt", "meshes/canary.bin", "meta.ini")),
            ("ReplaceExisting", "Protected Existing",
             ("meshes/canary.bin", "meshes/new.bin", "meta.ini")),
            ("FomodDependency", "ModLab Spike FOMOD",
             ("always.txt", "dependency-seen.txt", "meta.ini")),
        ):
            scenario_root = root / "scenarios" / scenario
            self.assertIn(str(scenario_root / "before.json"), paths)
            self.assertIn(str(scenario_root / "after.json"), paths)
            self.assertNotIn(str(scenario_root / "protected-state.json"), paths)
            self.assertIn(
                str(scenario_root / (".journal.json." + "f" * 32 + ".part")),
                paths,
            )
            for quarantine_name in (scenario, "Recovery-" + scenario):
                destination = root / "quarantine" / quarantine_name / expected_name
                self.assertIn(str(destination), paths)
                for member in members:
                    self.assertIn(str(destination / member), paths)
        from pathlib import PureWindowsPath
        self.assertFalse(any(
            {"new-folder", "fomod-dependency"}.intersection(
                PureWindowsPath(path).parts
            )
            for path in paths
        ))

    def test_fixture_admission_includes_every_external_input_and_source_adoption_path(self):
        from unittest.mock import patch
        from modlab.validation import mo2_containment_fixtures as fixtures
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BootstrapPlanningFixture(Path(temporary))
            run_root = (
                fixture.layout.mo2_containment_validation
                / ("a" * 32)
                / "fixtures"
                / "v2"
                / "NewFolder"
            )
            with patch.object(fixtures, "load_mo2_release", return_value=fixture.release):
                admission = fixtures.preflight_containment_fixture(
                    fixture.workspace,
                    fixture.artifact_id,
                    fixture.steam_root,
                    fixture.layout.mo2_containment_validation,
                    fixtures.ContainmentScenario.NEW_FOLDER,
                    fixture_parent=run_root,
                    command_runner=fixture.runner,
                    version_reader=fixture.version_reader,
                )
            paths = {row.path for row in admission.paths}
            expected_external = {
                fixture.steam_root,
                fixture.steam_root / "steamapps" / "appmanifest_489830.acf",
                fixture.game_root,
                fixture.game_root / "SkyrimSE.exe",
                fixture.game_root / "Data",
                fixture.game_root / "Data" / "Skyrim.esm",
                fixture.game_root / "Skyrim.ccc",
                fixture.release_path,
                Path(r"C:\Windows\System32\tar.exe"),
                fixture.workspace
                / "library"
                / "archives"
                / fixture.artifact_id.removeprefix("archive-sha256:")[:2]
                / fixture.artifact_id.removeprefix("archive-sha256:")
                / "payload.7z",
                fixture.layout.metadata
                / "artifacts"
                / f"{fixture.artifact_id.removeprefix('archive-sha256:')}.json",
            }
            self.assertTrue({str(path) for path in expected_external}.issubset(paths))
            source_mod = run_root / "s" / "tools/mo2/skyrim-se-ae/mods/ModLab Spike New"
            self.assertIn(str(source_mod), paths)
            self.assertIn(str(source_mod / "meshes/new-folder.bin"), paths)
            self.assertIn(str(source_mod / "meta.ini"), paths)

    def test_fixture_admission_ignores_unconsumed_steam_root_child_churn(self):
        """An unrelated Steam client child cannot invalidate Skyrim inputs."""
        from unittest.mock import patch
        from modlab.validation import mo2_containment_fixtures as fixtures
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BootstrapPlanningFixture(Path(temporary))
            run_root = (
                fixture.layout.mo2_containment_validation
                / ("c" * 32)
                / "fixtures"
                / "v2"
                / "NewFolder"
            )

            def admission():
                with patch.object(
                    fixtures,
                    "load_mo2_release",
                    return_value=fixture.release,
                ):
                    return fixtures.preflight_containment_fixture(
                        fixture.workspace,
                        fixture.artifact_id,
                        fixture.steam_root,
                        fixture.layout.mo2_containment_validation,
                        fixtures.ContainmentScenario.NEW_FOLDER,
                        fixture_parent=run_root,
                        command_runner=fixture.runner,
                        version_reader=fixture.version_reader,
                    )

            first = admission()
            unrelated = fixture.steam_root / "client-cache.tmp"
            unrelated.write_bytes(b"unconsumed Steam client state")
            metadata = fixture.steam_root.stat()
            os.utime(
                fixture.steam_root,
                ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 10_000_000_000),
            )
            after_create = admission()
            unrelated.unlink()
            metadata = fixture.steam_root.stat()
            os.utime(
                fixture.steam_root,
                ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 10_000_000_000),
            )
            after_delete = admission()

            self.assertEqual(first, after_create)
            self.assertEqual(first, after_delete)
            self.assertFalse(run_root.exists())

    def test_fixture_admission_rejects_same_path_directory_substitution(self):
        """Stable spelling cannot hide replacement of an admitted directory."""
        from unittest.mock import patch
        from modlab.validation import mo2_containment_fixtures as fixtures
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BootstrapPlanningFixture(Path(temporary))
            run_root = (
                fixture.layout.mo2_containment_validation
                / ("d" * 32)
                / "fixtures"
                / "v2"
                / "NewFolder"
            )
            with patch.object(
                fixtures,
                "load_mo2_release",
                return_value=fixture.release,
            ):
                admitted = fixtures.preflight_containment_fixture(
                    fixture.workspace,
                    fixture.artifact_id,
                    fixture.steam_root,
                    fixture.layout.mo2_containment_validation,
                    fixtures.ContainmentScenario.NEW_FOLDER,
                    fixture_parent=run_root,
                    command_runner=fixture.runner,
                    version_reader=fixture.version_reader,
                )
                retained = fixture.game_root.with_name("retained-game-root")
                fixture.game_root.rename(retained)
                fixture.game_root.mkdir()
                for child in tuple(retained.iterdir()):
                    child.rename(fixture.game_root / child.name)
                retained.rmdir()

                with self.assertRaisesRegex(
                    fixtures.ContainmentFixtureError,
                    "immutable input admission changed",
                ):
                    fixtures.prepare_containment_fixture(
                        fixture.workspace,
                        fixture.artifact_id,
                        fixture.steam_root,
                        fixture.layout.mo2_containment_validation,
                        fixtures.ContainmentScenario.NEW_FOLDER,
                        fixture_parent=run_root,
                        command_runner=fixture.runner,
                        version_reader=fixture.version_reader,
                        expected_admission=admitted,
                    )
            self.assertFalse(run_root.exists())

    def test_fixture_admission_rejects_consumed_file_timestamp_and_content_drift(self):
        """Files actually consumed by discovery retain strict metadata/content evidence."""
        from unittest.mock import patch
        from modlab.validation import mo2_containment_fixtures as fixtures

        for drift in ("timestamp", "content"):
            with self.subTest(drift=drift), tempfile.TemporaryDirectory() as temporary:
                fixture = BootstrapPlanningFixture(Path(temporary))
                run_root = (
                    fixture.layout.mo2_containment_validation
                    / ("e" * 32)
                    / "fixtures"
                    / "v2"
                    / "NewFolder"
                )
                manifest = (
                    fixture.steam_root / "steamapps" / "appmanifest_489830.acf"
                )
                with patch.object(
                    fixtures,
                    "load_mo2_release",
                    return_value=fixture.release,
                ):
                    admitted = fixtures.preflight_containment_fixture(
                        fixture.workspace,
                        fixture.artifact_id,
                        fixture.steam_root,
                        fixture.layout.mo2_containment_validation,
                        fixtures.ContainmentScenario.NEW_FOLDER,
                        fixture_parent=run_root,
                        command_runner=fixture.runner,
                        version_reader=fixture.version_reader,
                    )
                    metadata = manifest.stat()
                    if drift == "timestamp":
                        os.utime(
                            manifest,
                            ns=(
                                metadata.st_atime_ns,
                                metadata.st_mtime_ns + 10_000_000_000,
                            ),
                        )
                    else:
                        original = manifest.read_bytes()
                        changed = original.replace(b"Edition", b"EditioN", 1)
                        self.assertEqual(len(original), len(changed))
                        manifest.write_bytes(changed)
                        os.utime(
                            manifest,
                            ns=(metadata.st_atime_ns, metadata.st_mtime_ns),
                        )

                    with self.assertRaisesRegex(
                        fixtures.ContainmentFixtureError,
                        "immutable input admission changed",
                    ):
                        fixtures.prepare_containment_fixture(
                            fixture.workspace,
                            fixture.artifact_id,
                            fixture.steam_root,
                            fixture.layout.mo2_containment_validation,
                            fixtures.ContainmentScenario.NEW_FOLDER,
                            fixture_parent=run_root,
                            command_runner=fixture.runner,
                            version_reader=fixture.version_reader,
                            expected_admission=admitted,
                        )
                self.assertFalse(run_root.exists())

    def test_fixture_admission_rejects_archive_observation_drift_before_creation(self):
        from unittest.mock import patch
        from modlab.validation import mo2_containment_fixtures as fixtures
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BootstrapPlanningFixture(Path(temporary))
            run_root = (
                fixture.layout.mo2_containment_validation
                / ("b" * 32)
                / "fixtures"
                / "v2"
                / "NewFolder"
            )
            first = fixtures._observe_artifact(
                fixture.layout,
                fixture.artifact_id,
            )
            changed = replace(first, metadata_sha256="f" * 64)
            with (
                patch.object(fixtures, "load_mo2_release", return_value=fixture.release),
                patch.object(
                    fixtures,
                    "_observe_artifact",
                    side_effect=(first, changed),
                ),
                self.assertRaisesRegex(
                    fixtures.ContainmentFixtureError,
                    "archive input changed",
                ),
            ):
                fixtures.preflight_containment_fixture(
                    fixture.workspace,
                    fixture.artifact_id,
                    fixture.steam_root,
                    fixture.layout.mo2_containment_validation,
                    fixtures.ContainmentScenario.NEW_FOLDER,
                    fixture_parent=run_root,
                    command_runner=fixture.runner,
                    version_reader=fixture.version_reader,
                )
            self.assertFalse(run_root.exists())

    def test_outer_admission_rejects_changed_fixture_input_snapshot(self):
        from unittest.mock import patch
        from modlab.adapters.mo2.path_budget import PlannedPath, admit_paths
        from modlab.validation import mo2_containment_service as service
        first = admit_paths([PlannedPath("fixture", "preparation-wide", r"C:\first")])
        changed = admit_paths([PlannedPath("fixture", "preparation-wide", r"C:\changed")])
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            layout = __import__("modlab.workspace", fromlist=["initialize_workspace"]).initialize_workspace(source)
            run_id = "containment-run:" + "e" * 32
            with (
                patch.object(service, "_preflight_preparation", side_effect=(first, changed)),
                patch.object(service, "_effect_store", wraps=service._effect_store),
                self.assertRaisesRegex(service.ContainmentServiceError, "input.*changed|admission.*changed"),
            ):
                service.prepare_run(
                    source,
                    "archive-sha256:" + "a" * 64,
                    Path(temporary) / "Steam",
                    layout.mo2_containment_validation,
                    _admitted_run_id=run_id,
                )
            self.assertFalse(
                (layout.mo2_containment_validation / ("e" * 32) / "intent.json").exists()
            )


@unittest.skipUnless(os.name == "nt", "native bounded preparation consumers require Windows")
class NativePathBudgetTests(unittest.TestCase):
    @staticmethod
    def _sized_path(base: Path, units: int) -> Path:
        path = base
        while units - len(str(path)) > 101:
            path /= "p" * 100
        return path / ("q" * (units - len(str(path)) - 1))

    def test_fixture_external_game_boundary_and_one_over_are_admitted_before_creation(self):
        from unittest.mock import patch
        from modlab.adapters.mo2.path_budget import PathBudgetError
        from modlab.validation import mo2_containment_fixtures as fixtures
        with tempfile.TemporaryDirectory(
            prefix="modlab-input-boundary-", dir=Path(__file__).resolve().parents[1]
        ) as temporary:
            root = Path(temporary)
            fixture = BootstrapPlanningFixture(root / "short")
            suffixes = (
                Path("steamapps/common/Skyrim Special Edition/SkyrimSE.exe"),
                *(path.relative_to(fixture.steam_root)
                  for path in fixture.steam_root.rglob("*") if path.is_file()),
            )
            longest = max(suffixes, key=lambda value: len(str(value)))
            steam_units = 512 - 1 - len(str(longest))
            exact_steam = self._sized_path(root / "exact", steam_units)
            one_over_steam = self._sized_path(root / "one-over", steam_units + 1)
            shutil.copytree(fixture.steam_root, exact_steam)
            shutil.copytree(fixture.steam_root, one_over_steam)
            exact_parent = fixture.layout.mo2_containment_validation / "exact" / "NewFolder"
            refused_parent = fixture.layout.mo2_containment_validation / "refused" / "NewFolder"

            with patch.object(fixtures, "load_mo2_release", return_value=fixture.release):
                admitted = fixtures.preflight_containment_fixture(
                    fixture.workspace,
                    fixture.artifact_id,
                    exact_steam,
                    fixture.layout.mo2_containment_validation,
                    fixtures.ContainmentScenario.NEW_FOLDER,
                    fixture_parent=exact_parent,
                    command_runner=fixture.runner,
                    version_reader=fixture.version_reader,
                )
                self.assertEqual(512, max(len(row.path) for row in admitted.paths))
                with self.assertRaisesRegex(PathBudgetError, "fixture-game-data-entry.*513"):
                    fixtures.preflight_containment_fixture(
                        fixture.workspace,
                        fixture.artifact_id,
                        one_over_steam,
                        fixture.layout.mo2_containment_validation,
                        fixtures.ContainmentScenario.NEW_FOLDER,
                        fixture_parent=refused_parent,
                        command_runner=fixture.runner,
                        version_reader=fixture.version_reader,
                    )
            self.assertFalse(exact_parent.exists())
            self.assertFalse(refused_parent.exists())

    def test_native_relocation_admits_bound_projection_and_exact_members(self):
        from modlab.validation import mo2_containment_service as service
        from modlab.validation import windows_junction as junction
        with tempfile.TemporaryDirectory(
            prefix="modlab-relocation-owner-", dir=Path(__file__).resolve().parents[1]
        ) as temporary:
            root = Path(temporary)
            source = root / "source"
            stage = root / "stage"
            protected = source / "Protected Existing"
            protected.mkdir(parents=True)
            (protected / "marker.txt").write_bytes(b"protected")
            stage.mkdir()
            owner = junction.create_owned_projection(
                protected,
                stage / "Protected Existing",
            )
            try:
                created = stage / "ModLab Spike New"
                created.mkdir()
                (created / "meta.ini").write_bytes(b"[General]\n")
                record = SimpleNamespace(
                    scenario=service.ContainmentScenario.NEW_FOLDER,
                    source_mods=source,
                    stage_mods=stage,
                    before_names=("Protected Existing",),
                )
                quarantine = root / "quarantine" / "NewFolder"
                admission = service._preflight_projection_relocation(
                    record,
                    quarantine,
                    expected_projections=(owner,),
                )
                self.assertEqual(("ModLab Spike New",), admission.mutation_names)
                self.assertEqual("junction", dict(
                    (row[0], row[1]) for row in admission.stage_rows[1:]
                )["Protected Existing"])
                self.assertEqual(
                    admission,
                    service._require_relocation_admission(
                        record,
                        quarantine,
                        admission,
                        expected_projections=(owner,),
                    ),
                )
                self.assertFalse(quarantine.exists())
            finally:
                owner.close()

    def test_fixture_rejects_configured_steam_junction_before_creation(self):
        from unittest.mock import patch
        from modlab.validation import mo2_containment_fixtures as fixtures
        from modlab.validation import windows_junction as junction
        with tempfile.TemporaryDirectory(
            prefix="modlab-steam-reparse-", dir=Path(__file__).resolve().parents[1]
        ) as temporary:
            root = Path(temporary)
            fixture = BootstrapPlanningFixture(root / "fixture")
            alias = root / "SteamAlias"
            owner = junction.create_owned_projection(fixture.steam_root, alias)
            owner.close()
            fixture_parent = (
                fixture.layout.mo2_containment_validation
                / "reparse-refused"
                / "NewFolder"
            )
            try:
                with (
                    patch.object(fixtures, "load_mo2_release", return_value=fixture.release),
                    self.assertRaisesRegex(
                        fixtures.ContainmentFixtureError,
                        "configured Steam input.*unqualified",
                    ),
                ):
                    fixtures.preflight_containment_fixture(
                        fixture.workspace,
                        fixture.artifact_id,
                        alias,
                        fixture.layout.mo2_containment_validation,
                        fixtures.ContainmentScenario.NEW_FOLDER,
                        fixture_parent=fixture_parent,
                        command_runner=fixture.runner,
                        version_reader=fixture.version_reader,
                    )
                self.assertFalse(fixture_parent.exists())
            finally:
                alias.rmdir()

    def test_substituted_expected_junction_refuses_before_quarantine_creation(self):
        from modlab.validation import mo2_containment_service as service
        from modlab.validation import windows_junction as junction
        with tempfile.TemporaryDirectory(
            prefix="modlab-relocation-substitution-",
            dir=Path(__file__).resolve().parents[1],
        ) as temporary:
            root = Path(temporary)
            run_root = root / ("a" * 32)
            fixture = run_root / "fixtures" / "v2" / "NewFolder"
            source = fixture / "s" / "tools" / "mo2" / "skyrim-se-ae" / "mods"
            stage = fixture / "t" / "tools" / "mo2" / "skyrim-se-ae" / "mods"
            target = source / "Protected Existing"
            link = stage / "Protected Existing"
            foreign = root / "foreign" / "Protected Existing"
            target.mkdir(parents=True)
            foreign.mkdir(parents=True)
            stage.mkdir(parents=True)
            owner = junction.create_owned_projection(target, link)
            projection = SimpleNamespace(
                identities=tuple(
                    SimpleNamespace(path=str(pin.path), volume=pin.identity[0], file_id=pin.identity[1])
                    for pin in owner.pins
                ),
                payload_hex=owner.payload.hex(),
            )
            owner.close()
            link.rmdir()
            substitute = junction.create_owned_projection(foreign, link)
            substitute.close()
            store = SimpleNamespace(
                run_path=lambda _run_id: run_root,
                load_preparation_projection=lambda _run_id, _scenario: projection,
            )
            record = SimpleNamespace(
                scenario=service.ContainmentScenario.NEW_FOLDER,
                source_mods=source,
                stage_mods=stage,
                before_names=("Protected Existing",),
            )
            quarantine = root / "quarantine" / "NewFolder"
            with self.assertRaisesRegex(
                service.ContainmentServiceError,
                "not the exact prepared object",
            ):
                with service._retained_relocation_projections(
                    store,
                    "containment-run:" + "a" * 32,
                    record,
                ):
                    self.fail("substituted projection must not be admitted")
            self.assertEqual(foreign, junction.inspect_junction(link).target_path)
            self.assertFalse(quarantine.exists())

    def test_guarded_relocation_rejects_deep_foreign_tree_before_quarantine_creation(self):
        from functools import partial
        from modlab.validation import mo2_containment_service as service
        with tempfile.TemporaryDirectory(
            prefix="modlab-relocation-boundary-", dir=Path(__file__).resolve().parents[1]
        ) as temporary:
            root = Path(temporary)
            source = root / "source"
            stage = root / "stage"
            quarantine = root / "quarantine" / "NewFolder"
            (source / "Protected Existing").mkdir(parents=True)
            foreign = stage / "Foreign"
            foreign.mkdir(parents=True)
            leaf = self._sized_path(foreign, 513)
            leaf.parent.mkdir(parents=True)
            leaf.write_bytes(b"foreign")
            record = SimpleNamespace(
                scenario=service.ContainmentScenario.NEW_FOLDER,
                source_mods=source,
                stage_mods=stage,
                before_names=("Protected Existing",),
            )
            ledger = service._EffectLedger()
            token = service._ACTIVE_EFFECTS.set(ledger)
            try:
                with self.assertRaisesRegex(Exception, "path budget"):
                    service._delegated_mutations_with_created_root(
                        quarantine,
                        (source, stage),
                        partial(service._preflight_projection_relocation, record, quarantine),
                        lambda _admission: self.fail("relocation must not begin"),
                    )
            finally:
                service._ACTIVE_EFFECTS.reset(token)
            self.assertTrue(leaf.is_file())
            self.assertFalse(quarantine.exists())
            self.assertEqual((), ledger.freeze().child_mutation_roots)

    def test_guarded_relocation_rejects_reparse_before_quarantine_creation(self):
        from functools import partial
        from modlab.validation import mo2_containment_service as service
        from modlab.validation import windows_junction as junction
        with tempfile.TemporaryDirectory(
            prefix="modlab-relocation-reparse-", dir=Path(__file__).resolve().parents[1]
        ) as temporary:
            root = Path(temporary)
            source = root / "source"
            stage = root / "stage"
            quarantine = root / "quarantine" / "Recovery-NewFolder"
            target = source / "Protected Existing"
            target.mkdir(parents=True)
            stage.mkdir()
            owner = junction.create_owned_projection(target, stage / "Foreign")
            owner.close()
            record = SimpleNamespace(
                scenario=service.ContainmentScenario.NEW_FOLDER,
                source_mods=source,
                stage_mods=stage,
                before_names=("Protected Existing",),
            )
            ledger = service._EffectLedger()
            token = service._ACTIVE_EFFECTS.set(ledger)
            try:
                with self.assertRaisesRegex(service.ContainmentServiceError, "reparse"):
                    service._delegated_mutations_with_created_root(
                        quarantine,
                        (source, stage),
                        partial(service._preflight_projection_relocation, record, quarantine),
                        lambda _admission: self.fail("recovery relocation must not begin"),
                    )
            finally:
                service._ACTIVE_EFFECTS.reset(token)
            self.assertTrue((stage / "Foreign").is_junction())
            self.assertFalse(quarantine.exists())
            self.assertEqual((), ledger.freeze().child_mutation_roots)

    def test_native_tar_exact_cwd_and_member_limits_then_one_over_no_stage(self):
        import zipfile
        from modlab.adapters.mo2.archive import SubprocessCommandRunner
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stage = root / ("s" * (247 - len(str(root)) - 1))
            refused_stage = root / ("r" * (247 - len(str(root)) - 1))
            files = extraction_package_files()
            relative = "d" * 100 + "/" + "e" * 100 + "/" + "f" * 62
            self.assertEqual(512, len(str(stage / relative)))
            files[relative] = b"exact native member boundary"
            archive_parent = root / ("a" * 100) / ("b" * 100) / ("c" * 100)
            archive_parent.mkdir(parents=True)
            archive = archive_parent / ("z" * (512 - len(str(archive_parent)) - 1))
            with zipfile.ZipFile(archive, "w") as output:
                for name in sorted(files):
                    output.writestr(name, files[name])
            extract_and_inventory(archive, descriptor_for_package(files), Path(r"C:\Windows\System32\tar.exe"),
                                  stage, free_space_reader=lambda _: 2**40)
            self.assertEqual(files[relative], (stage / relative).read_bytes())
            files[relative + "x"] = files.pop(relative)
            with zipfile.ZipFile(archive, "w") as output:
                for name in sorted(files):
                    output.writestr(name, files[name])
            with self.assertRaisesRegex(Mo2ArchiveError, "path budget"):
                extract_and_inventory(archive, descriptor_for_package(files), Path(r"C:\Windows\System32\tar.exe"),
                                      refused_stage, free_space_reader=lambda _: 2**40)
            self.assertFalse(refused_stage.exists())

    def test_python_shared_handle_rename_and_recursive_watcher_at_wide_boundary(self):
        from modlab.adapters.mo2.path_budget import PlannedPath, admit_paths, publication_paths
        from modlab.platform.windows_exact_fs import pin_direct_object, rename_pinned_no_replace, publish_new_pinned
        from modlab.validation import windows_watch
        from tests.test_mo2_containment_watch import MutationWatchTests
        harness = MutationWatchTests("test_recursive_create_write_rename_and_delete_are_ordered")
        harness.setUp()
        self.addCleanup(harness.tearDown)
        def sized_path(base, units):
            path = base
            while units - len(str(path)) > 101:
                path /= "p" * 100
            return path / ("q" * (units - len(str(path)) - 1))
        watched = sized_path(harness.root / "wide", 506)
        watched.mkdir(parents=True)
        source = watched / "a.bin"
        target = watched / "b.bin"
        admit_paths([PlannedPath("native512", "preparation-wide", str(source)),
                     PlannedPath("native512", "preparation-wide", str(target))])
        self.assertEqual(512, len(str(target)))
        harness.watched = watched
        # All worker/publication candidates remain within the declared 512 bound.
        evidence = sized_path(harness.root / "protocol", 430)
        evidence.mkdir(parents=True)
        harness.evidence = evidence
        for name in ("request.json", "controller-claim.json", "worker-launch.json", "controller-loss.json",
                     "ready.json", "events.ndjson", "terminal.json", "outcome.json", "stop.token"):
            admit_paths(publication_paths(evidence / name, "native-watch-publication"))
        request, _ = harness._start()
        source.write_bytes(b"bounded native bytes")
        owner = pin_direct_object(source, "file")
        parent = pin_direct_object(watched, "directory")
        try:
            rename_pinned_no_replace(owner, target, parent)
        finally:
            parent.close()
            owner.close()
        self.assertEqual(b"bounded native bytes", target.read_bytes())
        self.assertFalse(source.exists())
        receipt = windows_watch.stop_watch(request)
        self.assertTrue(receipt.complete, receipt.error)
        self.assertTrue(any(event.relative_path == "b.bin" and event.action == "RenamedNew" for event in receipt.events))
        publication = sized_path(harness.root / "publisher", 461)
        publication.mkdir(parents=True)
        destination = publication / "p.json"
        admit_paths(publication_paths(destination, "native-shared-publication"))
        from unittest.mock import patch
        from modlab.platform import windows_exact_fs as fs
        with patch.object(fs.os, "getpid", return_value=4294967295), patch.object(fs.threading, "get_ident", return_value=4294967295):
            self.assertEqual(b"published", publish_new_pinned(destination, b"published", lambda data: data))
        self.assertEqual(b"published", destination.read_bytes())
        # Root-handle admission itself is also exercised at exactly512, with
        # no unbudgeted child writes beyond that root during this second watch.
        harness.watched = sized_path(harness.root / "root512", 512)
        harness.watched.mkdir(parents=True)
        harness.evidence = harness.root / "second-evidence"
        harness.evidence.mkdir()
        request, _ = harness._start()
        receipt = windows_watch.stop_watch(request)
        self.assertTrue(receipt.complete, receipt.error)
