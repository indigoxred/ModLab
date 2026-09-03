"""Operation-path regressions exercise actual mutation entry points."""
import hashlib
import tempfile
import unittest
import os
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
        with patch.object(
            service,
            "preflight_containment_fixture",
            return_value=fixture_budget,
        ):
            budget = service._preflight_preparation(
                Path(r"C:\source"),
                "archive-sha256:" + "a" * 64,
                validation,
                run_id,
            )
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


@unittest.skipUnless(os.name == "nt", "native bounded preparation consumers require Windows")
class NativePathBudgetTests(unittest.TestCase):
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
