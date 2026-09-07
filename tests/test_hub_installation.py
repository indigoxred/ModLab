from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as Obj
import unittest

from modlab.resources.mo2_hub.installation import inspect_install_result


class HubInstallationTests(unittest.TestCase):
    def test_cancelled_installer_is_not_success(self):
        result = inspect_install_result(None, Path("."), False)
        self.assertEqual("Not installed", result.status)

    def test_metadata_only_folder_is_not_success(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "Empty").mkdir()
            (root / "Empty/meta.ini").write_text("[General]")
            mod = Obj(name=lambda: "Empty", absolutePath=lambda: str(root / "Empty"))
            result = inspect_install_result(mod, root, True)
        self.assertEqual("Incomplete", result.status)

    def test_real_files_but_disabled_is_reported_accurately(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "Example"
            target.mkdir()
            (target / "Example.esp").write_bytes(b"test")
            mod = Obj(name=lambda: "Example", absolutePath=lambda: str(target))
            result = inspect_install_result(mod, root, False)
        self.assertEqual("Installed, disabled", result.status)
        self.assertEqual(1, result.file_count)

    def test_returned_mod_outside_selected_store_is_rejected(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "mods").mkdir()
            (root / "unexpected").mkdir()
            mod = Obj(name=lambda: "Bad", absolutePath=lambda: str(root / "unexpected"))
            with self.assertRaisesRegex(ValueError, "selected mods"):
                inspect_install_result(mod, root / "mods", True)

    def test_active_content_is_installed_not_gameplay_verified(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "Example"
            target.mkdir()
            (target / "example.txt").write_text("hello")
            mod = Obj(name=lambda: "Example", absolutePath=lambda: str(target))
            result = inspect_install_result(mod, root, True)
        self.assertEqual("Installed, enabled", result.status)
        self.assertIn("not yet", result.detail)
