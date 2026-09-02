import hashlib
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from modlab.adapters.mo2.ini import decode_qsettings_path, parse_ini_bytes
from modlab.adapters.skyrim.scanner import discover_skyrim_steam
from modlab.artifacts.vault import ArchiveVault
from modlab.validation import mo2_containment_fixtures as fixtures
from modlab.validation.mo2_containment_fixtures import write_scenario_archives
from modlab.validation.windows_integrity import IntegrityLevel
from modlab.validation.windows_junction import inspect_junction
from modlab.workspace import initialize_workspace
from tests.support.mo2_containment import (
    ProductionPathSnapshot,
    capture_production_path_snapshots,
    capture_skyrim_production_path_snapshots,
    import_curated_mo2_archive,
    measure_real_fixture_evidence,
    prepare_fixture_with_fake_bootstrap,
    prepare_real_containment_fixture,
)
from tests.support.skyrim_workflow import create_skyrim_workflow_fixture


def zip_names(path: Path) -> tuple[str, ...]:
    with zipfile.ZipFile(path) as archive:
        return tuple(archive.namelist())


def read_zip(path: Path, name: str) -> bytes:
    with zipfile.ZipFile(path) as archive:
        return archive.read(name)


class ContainmentFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_archives_have_exact_safe_contents_and_fomod_dependency(self):
        # Catches archive payload drift or a missing FOMOD dependency rule.
        archives = write_scenario_archives(self.root)

        self.assertEqual(("meshes/new-folder.bin",), zip_names(archives.new_folder))
        self.assertIn("meshes/canary.bin", zip_names(archives.overwrite_probe))
        module = read_zip(archives.fomod_dependency, "fomod/ModuleConfig.xml")
        self.assertIn(b'fileDependency file="marker.txt" state="Active"', module)
        self.assertIn(b'destination="dependency-seen.txt"', module)

    def test_curated_archive_import_replaces_payload_name_without_hardlinking(self):
        # Catches an opt-in import retaining the vault payload filename as original_name.
        source = self.root / "payload.7z"
        data = b"exact-curated-release-bytes"
        source.write_bytes(data)
        workspace = self.root / "workspace"
        curated_copy_root = self.root / "curated-copy"
        descriptor = SimpleNamespace(
            archive_name="Mod.Organizer-2.5.2.7z",
            archive_sha256=hashlib.sha256(data).hexdigest(),
            archive_size=len(data),
        )

        with patch(
            "tests.support.mo2_containment.load_mo2_release",
            return_value=SimpleNamespace(descriptor=descriptor),
        ):
            artifact = import_curated_mo2_archive(source, workspace, curated_copy_root)

        curated_copy = curated_copy_root / descriptor.archive_name
        self.assertEqual(descriptor.archive_name, artifact.original_name)
        self.assertEqual(descriptor.archive_sha256, artifact.sha256)
        self.assertFalse(source.samefile(curated_copy))

    def test_curated_archive_import_refuses_mismatched_bytes(self):
        # Catches a named temporary copy that is imported without release-byte verification.
        source = self.root / "payload.7z"
        source.write_bytes(b"wrong-release-bytes")
        descriptor = SimpleNamespace(
            archive_name="Mod.Organizer-2.5.2.7z",
            archive_sha256="0" * 64,
            archive_size=len(b"wrong-release-bytes"),
        )

        with (
            patch(
                "tests.support.mo2_containment.load_mo2_release",
                return_value=SimpleNamespace(descriptor=descriptor),
            ),
            self.assertRaisesRegex(RuntimeError, "curated release"),
        ):
            import_curated_mo2_archive(
                source, self.root / "workspace", self.root / "curated-copy"
            )

    def test_fixture_uses_two_confined_instances_and_payload_junctions(self):
        # Catches a stage instance escaping its disposable run or copying source mods.
        fixture = prepare_fixture_with_fake_bootstrap(self.root)

        self.assertTrue(fixture.source_workspace.is_relative_to(fixture.run_root))
        self.assertTrue(fixture.stage_workspace.is_relative_to(fixture.run_root))
        self.assertEqual(
            fixture.source_mods / "Protected Existing",
            inspect_junction(fixture.stage_mods / "Protected Existing").target_path,
        )
        self.assertEqual(b"+Protected Existing\r\n", fixture.stage_lab_modlist.read_bytes())

    def test_fixture_inner_vaults_retain_the_source_artifact_official_name(self):
        # Catches inner vault imports using the content-addressed payload filename.
        fixture = prepare_fixture_with_fake_bootstrap(self.root)

        source_record, = ArchiveVault(fixture.source_workspace).list()
        stage_record, = ArchiveVault(fixture.stage_workspace).list()
        outer_record, = ArchiveVault(self.root / "source-vault").list()
        bootstrap_copy = (
            fixture.run_root / "bootstrap-archive" / "Mod.Organizer-2.5.2.7z"
        )
        self.assertEqual("Mod.Organizer-2.5.2.7z", source_record.original_name)
        self.assertEqual("Mod.Organizer-2.5.2.7z", stage_record.original_name)
        self.assertFalse(
            bootstrap_copy.samefile(outer_record.stored_path(self.root / "source-vault"))
        )

    def test_materialized_bootstrap_archive_refuses_unsafe_original_name(self):
        # Catches an original name escaping the run-scoped bootstrap-archive directory.
        payload = self.root / "payload.7z"
        payload.write_bytes(b"trusted payload")
        record = SimpleNamespace(
            original_name="../escape.7z",
            sha256=hashlib.sha256(b"trusted payload").hexdigest(),
            size=len(b"trusted payload"),
        )

        with self.assertRaisesRegex(
            fixtures.ContainmentFixtureError, "curated original name"
        ):
            fixtures._materialize_verified_bootstrap_archive(
                record, payload, self.root / "run"
            )

    def test_materialized_bootstrap_archive_refuses_changed_payload(self):
        # Catches payload bytes changing after the source-vault verification.
        payload = self.root / "payload.7z"
        payload.write_bytes(b"changed payload")
        record = SimpleNamespace(
            original_name="Mod.Organizer-2.5.2.7z",
            sha256=hashlib.sha256(b"original payload").hexdigest(),
            size=len(b"original payload"),
        )

        with self.assertRaisesRegex(
            fixtures.ContainmentFixtureError, "changed during materialization"
        ):
            fixtures._materialize_verified_bootstrap_archive(
                record, payload, self.root / "run"
            )
        self.assertFalse(
            (self.root / "run" / "bootstrap-archive" / record.original_name).exists()
        )

    def test_materialized_bootstrap_archive_refuses_noncurated_windows_names_before_creation(
        self,
    ):
        # Catches treating a generic .7z filename as the exact MO2 release identity.
        payload = self.root / "payload.7z"
        data = b"trusted payload"
        payload.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        unsafe_names = (
            "CON.7z",
            "file:stream.7z",
            "trailing.7z.",
            "trailing.7z ",
            "control\x01.7z",
        )

        for original_name in unsafe_names:
            with self.subTest(original_name=repr(original_name)):
                run_root = self.root / (
                    f"run-{len(original_name)}-{ord(original_name[0])}"
                )
                record = SimpleNamespace(
                    original_name=original_name,
                    sha256=digest,
                    size=len(data),
                )

                with self.assertRaisesRegex(
                    fixtures.ContainmentFixtureError, "curated original name"
                ):
                    fixtures._materialize_verified_bootstrap_archive(
                        record, payload, run_root
                    )
                self.assertFalse((run_root / "bootstrap-archive").exists())

    def test_materialized_bootstrap_archive_removes_owned_partial_after_write_error(
        self,
    ):
        # Catches an exclusive destination left behind when copying to it fails.
        payload = self.root / "payload.7z"
        data = b"trusted payload"
        payload.write_bytes(data)
        record = SimpleNamespace(
            original_name="Mod.Organizer-2.5.2.7z",
            sha256=hashlib.sha256(data).hexdigest(),
            size=len(data),
        )
        run_root = self.root / "run"
        destination = run_root / "bootstrap-archive" / record.original_name
        original_open = Path.open

        class FailingWriter:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.stream.close()
                return False

            def fileno(self):
                return self.stream.fileno()

            def write(self, _data):
                raise OSError("injected write failure")

        def open_with_write_failure(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            mode = args[0] if args else kwargs.get("mode", "r")
            if Path(path) == destination and mode == "xb":
                return FailingWriter(stream)
            return stream

        with (
            patch.object(Path, "open", open_with_write_failure),
            self.assertRaisesRegex(fixtures.ContainmentFixtureError, "materialize"),
        ):
            fixtures._materialize_verified_bootstrap_archive(record, payload, run_root)
        self.assertFalse(destination.exists())

    def test_materialized_bootstrap_archive_preserves_preexisting_destination_after_xb_failure(
        self,
    ):
        # Catches cleanup deleting a destination this invocation did not create.
        payload = self.root / "payload.7z"
        data = b"trusted payload"
        payload.write_bytes(data)
        record = SimpleNamespace(
            original_name="Mod.Organizer-2.5.2.7z",
            sha256=hashlib.sha256(data).hexdigest(),
            size=len(data),
        )
        destination = self.root / "run" / "bootstrap-archive" / record.original_name
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"preexisting bytes")

        with self.assertRaisesRegex(fixtures.ContainmentFixtureError, "materialize"):
            fixtures._materialize_verified_bootstrap_archive(
                record, payload, self.root / "run"
            )
        self.assertEqual(b"preexisting bytes", destination.read_bytes())

    def test_real_fixture_evidence_derives_version_projection_and_steam_mutation(self):
        # Catches evidence that is fabricated instead of read from the prepared fixture.
        fixture = prepare_fixture_with_fake_bootstrap(self.root / "fixture")
        skyrim = create_skyrim_workflow_fixture(self.root / "production")
        before = capture_skyrim_production_path_snapshots(skyrim.steam_root)
        manifest = skyrim.steam_root / "steamapps" / "appmanifest_489830.acf"
        manifest.write_text(
            manifest.read_text(encoding="utf-8") + "\n", encoding="utf-8"
        )

        def read_staged_version(path: Path) -> str:
            self.assertEqual(
                fixture.stage_layout.skyrim_mo2_app / "ModOrganizer.exe", path
            )
            return "9.8.7.6"

        with patch(
            "tests.support.mo2_containment.read_windows_file_version",
            side_effect=read_staged_version,
        ):
            after = capture_skyrim_production_path_snapshots(skyrim.steam_root)
            evidence = measure_real_fixture_evidence(fixture, before, after)

        self.assertEqual("9.8.7.6", evidence.executable_version)
        self.assertEqual(0, evidence.payload_bytes_copied_for_projection)
        self.assertEqual((manifest,), evidence.production_paths_written)

    def test_real_fixture_evidence_refuses_empty_direct_projection_replacement(self):
        # Catches a replaced projection that could otherwise be misreported as zero bytes.
        fixture = prepare_fixture_with_fake_bootstrap(self.root)
        projection = fixture.stage_mods / "Protected Existing"
        projection.rmdir()
        projection.mkdir()
        before = capture_production_path_snapshots((self.root / "Steam",))
        after = capture_production_path_snapshots((self.root / "Steam",))

        with (
            patch(
                "tests.support.mo2_containment.read_windows_file_version",
                return_value="2.5.2.0",
            ),
            self.assertRaisesRegex(RuntimeError, "junction"),
        ):
            measure_real_fixture_evidence(fixture, before, after)

    def test_production_snapshot_records_reparse_without_following_it(self):
        # Catches snapshot traversal into a Steam-root reparse target.
        steam_root = self.root / "Steam"
        steam_root.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "must-not-be-observed.txt").write_bytes(b"outside")
        link = steam_root / "library-link"
        try:
            os.symlink(outside, link, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"directory symlink unavailable: {error}")

        with self.assertRaisesRegex(RuntimeError, "reparse"):
            capture_production_path_snapshots((steam_root,))

    def test_production_snapshot_records_file_metadata_without_payload_hashes(self):
        # Catches a production snapshot that uses content hashes instead of metadata.
        steam_root = self.root / "Steam"
        steam_root.mkdir()
        payload = steam_root / "library.vdf"
        payload.write_bytes(b"metadata-only-observation")

        snapshot = capture_production_path_snapshots((steam_root,))[0]
        entries = {entry.relative_path: entry for entry in snapshot.entries}

        self.assertEqual("file", entries["library.vdf"].kind)
        self.assertEqual(len(b"metadata-only-observation"), entries["library.vdf"].size)

    def test_skyrim_production_snapshot_ignores_unrelated_steam_cache_mutation(
        self,
    ):
        # Catches broad Steam-root observation treating client cache churn as a game write.
        skyrim = create_skyrim_workflow_fixture(self.root)
        before = capture_skyrim_production_path_snapshots(skyrim.steam_root)
        cache = skyrim.steam_root / "appcache" / "assetcache.vdf"
        cache.parent.mkdir()
        cache.write_bytes(b"ambient Steam cache churn")

        after = capture_skyrim_production_path_snapshots(skyrim.steam_root)

        self.assertEqual(before, after)
        self.assertEqual(
            (
                skyrim.steam_root / "steamapps" / "appmanifest_489830.acf",
                skyrim.game_root,
            ),
            tuple(snapshot.path for snapshot in before),
        )

    def test_skyrim_production_snapshot_reports_manifest_mutation(self):
        # Catches a changed appmanifest_489830.acf disappearing from production evidence.
        skyrim = create_skyrim_workflow_fixture(self.root)
        before = capture_skyrim_production_path_snapshots(skyrim.steam_root)
        manifest = skyrim.steam_root / "steamapps" / "appmanifest_489830.acf"
        manifest.write_text(
            manifest.read_text(encoding="utf-8") + "\n", encoding="utf-8"
        )

        after = capture_skyrim_production_path_snapshots(skyrim.steam_root)

        changed = tuple(
            old.path
            for old, new in zip(before, after, strict=True)
            if old.entries != new.entries
        )
        self.assertEqual((manifest,), changed)

    def test_skyrim_production_snapshot_reports_game_root_mutation(self):
        # Catches a changed Skyrim root tree disappearing from production evidence.
        skyrim = create_skyrim_workflow_fixture(self.root)
        before = capture_skyrim_production_path_snapshots(skyrim.steam_root)
        (skyrim.game_root / "Data" / "injected.esp").write_bytes(b"must be reported")

        after = capture_skyrim_production_path_snapshots(skyrim.steam_root)

        changed = tuple(
            old.path
            for old, new in zip(before, after, strict=True)
            if old.entries != new.entries
        )
        self.assertEqual((skyrim.game_root,), changed)

    def test_skyrim_production_snapshot_refuses_missing_or_blocked_discovery(self):
        # Catches preflight observing paths when Skyrim discovery cannot trust them.
        missing = self.root / "missing-Steam"
        blocked = self.root / "blocked-Steam"
        manifest = blocked / "steamapps" / "appmanifest_489830.acf"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("not a Steam manifest", encoding="utf-8")

        for steam_root in (missing, blocked):
            with self.subTest(steam_root=steam_root.name):
                with self.assertRaisesRegex(RuntimeError, "Skyrim discovery"):
                    capture_skyrim_production_path_snapshots(steam_root)

    def test_skyrim_production_snapshot_refuses_discovery_path_change_during_capture(
        self,
    ):
        # Catches snapshots accepted after discovery resolves a different Skyrim root.
        skyrim = create_skyrim_workflow_fixture(self.root)
        alternate = skyrim.steam_root / "steamapps" / "common" / "Skyrim Alternate"
        (alternate / "Data").mkdir(parents=True)
        (alternate / "SkyrimSE.exe").write_bytes(b"alternate Skyrim executable")
        manifest = skyrim.steam_root / "steamapps" / "appmanifest_489830.acf"
        original_discover = discover_skyrim_steam
        calls = 0

        def discovery_that_changes_root(steam_root):
            nonlocal calls
            calls += 1
            if calls == 2:
                manifest.write_text(
                    '"AppState"\n{\n'
                    '    "appid" "489830"\n'
                    '    "name" "The Elder Scrolls V: Skyrim Special Edition"\n'
                    '    "StateFlags" "4"\n'
                    '    "installdir" "Skyrim Alternate"\n'
                    "}\n",
                    encoding="utf-8",
                )
            return original_discover(steam_root)

        with (
            patch(
                "tests.support.mo2_containment.discover_skyrim_steam",
                side_effect=discovery_that_changes_root,
            ),
            self.assertRaisesRegex(
                RuntimeError, "changed during production observation"
            ),
        ):
            capture_skyrim_production_path_snapshots(skyrim.steam_root)

    def test_skyrim_production_snapshot_refuses_discovery_report_change_during_capture(
        self,
    ):
        # Catches accepting a snapshot after discovery changed without moving its paths.
        skyrim = create_skyrim_workflow_fixture(self.root)
        manifest = skyrim.steam_root / "steamapps" / "appmanifest_489830.acf"
        original_discover = discover_skyrim_steam
        calls = 0

        def discovery_that_changes_report(steam_root):
            nonlocal calls
            calls += 1
            if calls == 2:
                manifest.write_text(
                    manifest.read_text(encoding="utf-8").replace(
                        '"StateFlags" "4"', '"StateFlags" "3"'
                    ),
                    encoding="utf-8",
                )
            return original_discover(steam_root)

        with (
            patch(
                "tests.support.mo2_containment.discover_skyrim_steam",
                side_effect=discovery_that_changes_report,
            ),
            self.assertRaisesRegex(
                RuntimeError, "changed during production observation"
            ),
        ):
            capture_skyrim_production_path_snapshots(skyrim.steam_root)

    def test_real_fixture_refuses_bound_snapshot_path_change_after_preparation(
        self,
    ):
        # Catches final evidence comparing a different discovery path tuple than preflight.
        manifest = self.root / "Steam" / "steamapps" / "appmanifest_489830.acf"
        first_game_root = self.root / "Steam" / "steamapps" / "common" / "Skyrim"
        second_game_root = self.root / "Steam" / "steamapps" / "common" / "Skyrim Alt"
        manifest.parent.mkdir(parents=True)
        manifest.write_bytes(b"manifest")
        first_game_root.mkdir(parents=True)
        second_game_root.mkdir(parents=True)
        fixture = prepare_fixture_with_fake_bootstrap(self.root / "fixture")
        before = (
            ProductionPathSnapshot(manifest, ()),
            ProductionPathSnapshot(first_game_root, ()),
        )
        after = (
            ProductionPathSnapshot(manifest, ()),
            ProductionPathSnapshot(second_game_root, ()),
        )

        with (
            patch(
                "tests.support.mo2_containment.capture_skyrim_production_path_snapshots",
                side_effect=(before, after),
            ),
            patch(
                "tests.support.mo2_containment.import_curated_mo2_archive",
                return_value=SimpleNamespace(
                    artifact_id="archive-sha256:" + "a" * 64
                ),
            ),
            patch(
                "modlab.validation.mo2_containment_fixtures.prepare_containment_fixture",
                return_value=fixture,
            ),
            self.assertRaisesRegex(RuntimeError, "production observation paths changed"),
        ):
            with prepare_real_containment_fixture(
                self.root / "payload.7z", self.root / "Steam"
            ):
                self.fail("mismatched bound observation must refuse before yielding")

    def test_fixture_parent_override_is_exact_and_confined_below_validation_root(self):
        from tests.support.mo2_containment import prepare_fixture_with_fake_bootstrap

        parent = self.root / "source-vault" / "runtime" / "validation" / "mo2-containment" / ("a" * 32) / "fixtures" / "NewFolder"
        fixture = prepare_fixture_with_fake_bootstrap(self.root, fixture_parent=parent)
        self.assertEqual(parent.resolve(), fixture.run_root)

        with self.assertRaisesRegex(fixtures.ContainmentFixtureError, "fixture parent"):
            escaped_root = self.root / "escaped-case"
            prepare_fixture_with_fake_bootstrap(
                escaped_root,
                fixture_parent=self.root / "escape",
            )

    def test_stage_root_is_labeled_low_before_any_projection_can_exist(self):
        # Catches low labels being applied only to children, leaving the MO2 root Medium.
        layout = initialize_workspace(self.root / "stage-workspace")
        labels: list[Path] = []

        with patch.object(
            fixtures,
            "set_low_integrity_tree",
            side_effect=lambda path: labels.append(Path(path)),
        ):
            fixtures._prepare_stage_environment(self.root, layout)

        self.assertIn(layout.skyrim_mo2, labels)

    def test_fixture_exposes_portable_cache_and_logs_under_the_stage_base_directory(self):
        # Catches fixture cache/log paths which MO2 portable mode cannot consume.
        fixture = prepare_fixture_with_fake_bootstrap(self.root)
        configuration = parse_ini_bytes(
            (fixture.stage_layout.skyrim_mo2_app / "ModOrganizer.ini").read_bytes()
        )

        self.assertEqual(
            fixture.stage_layout.skyrim_mo2 / "webcache", fixture.stage_cache
        )
        self.assertEqual(fixture.stage_layout.skyrim_mo2 / "logs", fixture.stage_logs)
        self.assertTrue(fixture.stage_cache.is_dir())
        self.assertTrue(fixture.stage_logs.is_dir())
        self.assertEqual(
            str(fixture.stage_layout.skyrim_mo2),
            decode_qsettings_path(configuration.get("Settings", "base_directory")),
        )
        self.assertNotIn("MODLAB_MO2_CACHE_DIRECTORY", fixture.stage_environment)
        self.assertNotIn("MODLAB_MO2_LOG_DIRECTORY", fixture.stage_environment)

    def test_external_low_temp_uses_the_exact_low_child_of_the_current_temp_base(self):
        # Catches replacing the actual current-temp Low child with a derived LocalLow path.
        local_low = self.root / "LocalLow"
        current_temp = self.root / "nondefault-temp-base"
        low_temp = current_temp / "Low"
        local_low.mkdir()
        low_temp.mkdir(parents=True)

        roots = fixtures._external_low_watch_roots(
            local_low_resolver=lambda: local_low,
            current_temp_base_resolver=lambda: current_temp,
            integrity_reader=lambda _: IntegrityLevel.LOW,
        )

        self.assertEqual(
            (
                ("ExternalLocalLow", local_low.resolve(strict=False)),
                ("ExternalTempLow", low_temp.resolve(strict=False)),
            ),
            roots,
        )

    def test_low_temp_refuses_missing_non_low_and_redirected_candidates(self):
        # Catches an external Low temp root that cannot be proven direct and Low.
        base = self.root / "temp-base"
        base.mkdir()
        with self.assertRaisesRegex(fixtures.ContainmentFixtureError, "unavailable"):
            fixtures._current_low_temp_directory(
                temp_base_resolver=lambda: base,
                integrity_reader=lambda _: IntegrityLevel.LOW,
            )

        low = base / "Low"
        low.mkdir()
        with self.assertRaisesRegex(fixtures.ContainmentFixtureError, "not Low"):
            fixtures._current_low_temp_directory(
                temp_base_resolver=lambda: base,
                integrity_reader=lambda _: IntegrityLevel.MEDIUM,
            )

        real_is_symlink = Path.is_symlink
        with (
            patch.object(
                Path,
                "is_symlink",
                autospec=True,
                side_effect=lambda path: Path(path) == low or real_is_symlink(path),
            ),
            self.assertRaisesRegex(fixtures.ContainmentFixtureError, "must be direct"),
        ):
            fixtures._current_low_temp_directory(
                temp_base_resolver=lambda: base,
                integrity_reader=lambda _: IntegrityLevel.LOW,
            )


if __name__ == "__main__":
    unittest.main()
