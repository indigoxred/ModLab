from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as Obj
import unittest

from modlab.resources.mo2_hub.installation import inspect_install_result
from modlab.resources.mo2_hub import installation
import hashlib


class HubInstallationTests(unittest.TestCase):
    def capture(self, root, archive, target, original=None):
        self.assertTrue(hasattr(installation, 'capture_files'), 'Installer must record exact installed bytes')
        return installation.capture_files(archive, target, root, original)

    def finish(self, steps):
        while True:
            try:
                next(steps)
            except StopIteration as done:
                return done.value

    def test_inventory_records_archive_and_selected_files_and_yields_for_ui(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); target = root/'Example'; target.mkdir()
            archive = root/'source.7z'; archive.write_bytes(b'original archive')
            (target/'meta.ini').write_text('[General]')
            (target/'Example.esp').write_bytes(b'installed plugin')
            steps = self.capture(root, archive, target)
            self.assertIsInstance(next(steps), str)
            receipt = self.finish(steps)
            self.assertEqual(receipt['archive_sha256'], hashlib.sha256(b'original archive').hexdigest())
            self.assertEqual(receipt['files'], {'Example.esp': {
                'size': 16, 'sha256': hashlib.sha256(b'installed plugin').hexdigest()}})
            self.assertEqual(receipt['version'], 1)

    def test_modified_file_during_inventory_cannot_be_certified(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp); target=root/'Example'; target.mkdir()
            archive=root/'source.zip'; archive.write_bytes(b'archive')
            asset=target/'example.esp'; asset.write_bytes(b'one')
            steps=self.capture(root,archive,target)
            while next(steps) != 'Checked example.esp':
                pass
            asset.write_bytes(b'two')
            with self.assertRaisesRegex(ValueError, 'changed'):
                self.finish(steps)

    def test_final_metadata_pass_does_not_yield_before_receipt_completion(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp); target=root/'Example'; target.mkdir()
            archive=root/'source.zip'; archive.write_bytes(b'archive')
            (target/'example.esp').write_bytes(b'one')
            steps=self.capture(root,archive,target)
            while next(steps) != 'Checked example.esp':
                pass
            with self.assertRaises(StopIteration) as finished:
                next(steps)
            self.assertIn('example.esp',finished.exception.value['files'])

    def test_added_file_during_inventory_cannot_be_certified(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp); target=root/'Example'; target.mkdir()
            archive=root/'source.zip'; archive.write_bytes(b'archive')
            (target/'example.esp').write_bytes(b'one')
            steps=self.capture(root,archive,target)
            while next(steps) != 'Checked example.esp':
                pass
            (target/'extra.dll').write_bytes(b'extra')
            with self.assertRaisesRegex(ValueError, 'changed'):
                self.finish(steps)

    def test_archive_changed_after_installer_started_is_not_certified(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp); target=root/'Example'; target.mkdir()
            archive=root/'source.zip'; archive.write_bytes(b'archive')
            (target/'example.esp').write_bytes(b'one')
            self.assertTrue(hasattr(installation, 'file_stamp'))
            original=installation.file_stamp(archive)
            archive.write_bytes(b'changed archive')
            with self.assertRaisesRegex(ValueError, 'archive changed'):
                self.finish(self.capture(root,archive,target,original))

    def test_empty_and_outside_inventory_are_not_accepted(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp); mods=root/'mods'; mods.mkdir(); target=mods/'Empty'; target.mkdir()
            archive=root/'source.zip'; archive.write_bytes(b'archive')
            (target/'meta.ini').write_text('[General]')
            with self.assertRaisesRegex(ValueError, 'without content'):
                self.finish(self.capture(mods,archive,target))
            with self.assertRaisesRegex(ValueError, 'selected mods'):
                self.finish(self.capture(mods,archive,root))

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
