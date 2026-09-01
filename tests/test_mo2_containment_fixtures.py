import tempfile
import unittest
import zipfile
from pathlib import Path

from modlab.validation.mo2_containment_fixtures import write_scenario_archives
from modlab.validation.windows_junction import inspect_junction
from tests.support.mo2_containment import prepare_fixture_with_fake_bootstrap


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


if __name__ == "__main__":
    unittest.main()
