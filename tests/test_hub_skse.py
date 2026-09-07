from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile
from unittest.mock import patch

from modlab.resources.mo2_hub.outputs import digest
from modlab.resources.mo2_hub import skse


class SkseTests(unittest.TestCase):
    def test_loader_resource_version_normalizes_its_leading_zero(self):
        from modlab.resources.mo2_hub.native import packed_version
        for text in ('0.2.2.8', '0, 2, 2, 8', '2.2.8', '2.2.8.0'):
            self.assertEqual(packed_version('2.2.8.0'), skse.packed_loader_version(text))
        for text in ('', 'unknown', '0.0.0.0', '2.2'):
            with self.assertRaises(ValueError):
                skse.packed_loader_version(text)

    def test_latest_skse_rejection_on_1170_links_the_matching_download(self):
        package = skse.PACKAGES['7bad616ed360823a027f8828801d91e3a4aa2eacd952023af7f3e31adb2ae250']
        with self.assertRaises(ValueError) as caught:
            skse.check_target(package, '1.6.1170.0', 'Steam')
        self.assertIn('2.2.8', str(caught.exception))
        self.assertIn('file_id=792256', str(caught.exception))

    def test_official_228_package_targets_steam_1170_only(self):
        package = skse.PACKAGES['ada6a771e18245cd0a6106b7e019b3b2bdf7a6bee390bc069c6e0a35f89544fa']
        skse.check_target(package, '1.6.1170.0', 'Steam')
        for runtime, store in [('1.7.104.0', 'Steam'), ('1.6.1179.0', 'GOG')]:
            with self.assertRaises(ValueError):
                skse.check_target(package, runtime, store)

    def test_skse_dependent_mod_is_not_classified_as_the_loader(self):
        with TemporaryDirectory() as tmp:
            archive = Path(tmp)/'SKSE Menu Framework.zip'
            with zipfile.ZipFile(archive, 'w') as out:
                out.writestr('SKSE/Plugins/SKSEMenuFramework.dll', b'plugin')
            self.assertIsNone(skse.identify(archive)[1])

    def test_unrecognized_loader_contents_still_require_official_package(self):
        with TemporaryDirectory() as tmp:
            archive = Path(tmp)/'SKSE custom package.zip'
            with zipfile.ZipFile(archive, 'w') as out:
                out.writestr('package/skse64_loader.exe', b'unknown loader')
            with self.assertRaisesRegex(ValueError, 'recognized current official'):
                skse.identify(archive)

    def fixture(self, root):
        game, source, job = root / 'game', root / 'package', root / 'job'
        for directory in (game, source, job):
            directory.mkdir()
        (game / 'SkyrimSE.exe').write_bytes(b'game')
        (source / 'skse64_loader.exe').write_bytes(b'new loader')
        (source / 'skse64_1_7_104.dll').write_bytes(b'new runtime')
        return game, source, job

    def test_wrong_runtime_or_store_rejected_before_install(self):
        package = skse.PACKAGES['7bad616ed360823a027f8828801d91e3a4aa2eacd952023af7f3e31adb2ae250']
        skse.check_target(package, '1.7.104.0', 'Steam')
        for runtime, store in [('1.6.1170', 'Steam'), ('1.7.104.0', 'GOG'), ('1.7.104.0', '')]:
            with self.assertRaisesRegex(ValueError, 'requires'):
                skse.check_target(package, runtime, store)

    def test_root_deployment_and_restore_preserve_existing_files(self):
        with TemporaryDirectory() as tmp:
            game, source, job = self.fixture(Path(tmp))
            loader = game / 'skse64_loader.exe'
            loader.write_bytes(b'previous loader')
            record = skse.deploy_root(game, source, job, digest(game / 'SkyrimSE.exe'))
            self.assertEqual(loader.read_bytes(), b'new loader')
            self.assertEqual((game / 'SkyrimSE.exe').read_bytes(), b'game')
            skse.restore_root(record, job)
            self.assertEqual(loader.read_bytes(), b'previous loader')
            self.assertFalse((game / 'skse64_1_7_104.dll').exists())
            self.assertEqual((job / 'withdrawn-root/skse64_1_7_104.dll').read_bytes(), b'new runtime')

    def test_failed_second_copy_restores_first_and_changed_game_is_rejected(self):
        with TemporaryDirectory() as tmp:
            game, source, job = self.fixture(Path(tmp))
            expected = digest(game / 'SkyrimSE.exe')
            (game / 'SkyrimSE.exe').write_bytes(b'updated game')
            with self.assertRaisesRegex(ValueError, 'changed'):
                skse.deploy_root(game, source, job, expected)
            self.assertFalse((game / 'skse64_loader.exe').exists())
            original_copy = skse.shutil.copy2
            def fail_runtime(src, dst, *args, **kwargs):
                if Path(src).name.endswith('.dll'):
                    raise OSError('copy failure')
                return original_copy(src, dst, *args, **kwargs)
            with patch.object(skse.shutil, 'copy2', side_effect=fail_runtime):
                with self.assertRaisesRegex(OSError, 'copy failure'):
                    skse.deploy_root(game, source, job, digest(game / 'SkyrimSE.exe'))
            self.assertFalse((game / 'skse64_loader.exe').exists())

    def test_restore_does_not_overwrite_later_manual_change(self):
        with TemporaryDirectory() as tmp:
            game, source, job = self.fixture(Path(tmp))
            record = skse.deploy_root(game, source, job, digest(game / 'SkyrimSE.exe'))
            (game / 'skse64_loader.exe').write_bytes(b'user replacement')
            with self.assertRaisesRegex(ValueError, 'changed'):
                skse.restore_root(record, job)
            self.assertEqual((game / 'skse64_loader.exe').read_bytes(), b'user replacement')
