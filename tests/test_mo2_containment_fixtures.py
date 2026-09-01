import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from modlab.adapters.mo2.ini import decode_qsettings_path, parse_ini_bytes
from modlab.validation import mo2_containment_fixtures as fixtures
from modlab.validation.mo2_containment_fixtures import write_scenario_archives
from modlab.validation.windows_integrity import IntegrityLevel
from modlab.validation.windows_junction import inspect_junction
from modlab.workspace import initialize_workspace
from tests.support.mo2_containment import (
    capture_production_path_snapshots,
    measure_real_fixture_evidence,
    prepare_fixture_with_fake_bootstrap,
)


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

    def test_real_fixture_evidence_derives_version_projection_and_steam_mutation(self):
        # Catches evidence that is fabricated instead of read from the prepared fixture.
        fixture = prepare_fixture_with_fake_bootstrap(self.root)
        steam_root = self.root / "Steam"
        before = capture_production_path_snapshots((steam_root,))
        (steam_root / "unexpected-write.txt").write_bytes(b"must be reported")

        def read_staged_version(path: Path) -> str:
            self.assertEqual(
                fixture.stage_layout.skyrim_mo2_app / "ModOrganizer.exe", path
            )
            return "9.8.7.6"

        with patch(
            "tests.support.mo2_containment.read_windows_file_version",
            side_effect=read_staged_version,
        ):
            evidence = measure_real_fixture_evidence(fixture, before)

        self.assertEqual("9.8.7.6", evidence.executable_version)
        self.assertEqual(0, evidence.payload_bytes_copied_for_projection)
        self.assertEqual((steam_root.resolve(),), evidence.production_paths_written)

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

        snapshot = capture_production_path_snapshots((steam_root,))[0]
        entries = {entry.relative_path: entry for entry in snapshot.entries}

        self.assertEqual("reparse", entries["library-link"].kind)
        self.assertNotIn("library-link/must-not-be-observed.txt", entries)

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
