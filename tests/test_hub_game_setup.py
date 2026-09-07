from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import hashlib
import unittest

from modlab.resources.mo2_hub import game_setup


class GameSetupTests(unittest.TestCase):
    def test_recorded_baseline_detects_steam_replacement_and_is_scoped_to_game_folder(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); game = root / 'game'; store = root / 'records'
            (game / 'Data').mkdir(parents=True)
            contents = {'SkyrimSE.exe': b'1170 exe', 'SkyrimSELauncher.exe': b'1170 launcher',
                        'Data/Skyrim - Shaders.bsa': b'1170 shaders'}
            for name, content in contents.items():
                (game / name).write_bytes(content)
            (game / 'steam_api64.dll').write_bytes(b'steam')
            expected = {name: hashlib.sha256(content).hexdigest() for name, content in contents.items()}
            with patch.object(game_setup, 'TARGET_FILES', expected):
                game_setup.remember_baseline(game, store)
                self.assertFalse(any(f.level == 'Blocked' for f in game_setup.inspect_saved_baseline(game, store)))
                (game / 'SkyrimSE.exe').write_bytes(b'new Steam executable')
                findings = game_setup.inspect_saved_baseline(game, store)
                self.assertEqual('Blocked', findings[0].level)
                self.assertIn('SkyrimSE.exe', findings[0].detail)
                self.assertEqual((), game_setup.inspect_saved_baseline(root / 'other game', store))
                with self.assertRaises(ValueError):
                    game_setup.remember_baseline(game, store)
                self.assertEqual('Blocked', game_setup.inspect_saved_baseline(game, store)[0].level)

    def test_recorded_baseline_detects_missing_shader_without_changing_game_files(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); game = root / 'game'; game.mkdir()
            (game / 'steam_api64.dll').write_bytes(b'steam')
            shader = game / 'shader'; shader.write_bytes(b'original shader')
            with patch.object(game_setup, 'TARGET_FILES', {'shader': hashlib.sha256(b'original shader').hexdigest()}):
                game_setup.remember_baseline(game, root / 'records')
                shader.unlink()
                self.assertEqual('Blocked', game_setup.inspect_saved_baseline(game, root / 'records')[0].level)
                self.assertFalse(shader.exists())

    def test_missing_or_changed_shader_is_not_accepted_as_a_complete_baseline(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'Data').mkdir()
            self.assertIn('Data/Skyrim - Shaders.bsa', game_setup.mismatched_files(root, game_setup.TARGET_FILES))
            (root / 'Data/Skyrim - Shaders.bsa').write_bytes(b'wrong version')
            self.assertIn('Data/Skyrim - Shaders.bsa', game_setup.mismatched_files(root, game_setup.TARGET_FILES))

    def test_patcher_preflight_rejects_wrong_store_runtime_and_binary(self):
        for runtime, steam, identity in [('1.6.1170.0', True, game_setup.SOURCE_SHA1),
                                         ('1.7.104.0', False, game_setup.SOURCE_SHA1),
                                         ('1.7.104.0', True, 'modified')]:
            with self.assertRaises(ValueError):
                game_setup.check_source(runtime, steam, identity)
        game_setup.check_source('1.7.104.0', True, game_setup.SOURCE_SHA1)

    def test_version_label_alone_does_not_pass_target_check(self):
        self.assertFalse(game_setup.target_matches('1.6.1170.0', True, 'modified'))
        self.assertFalse(game_setup.target_matches('1.6.1170.0', False, game_setup.TARGET_SHA1))
        self.assertTrue(game_setup.target_matches('1.6.1170.0', True, game_setup.TARGET_SHA1))

    def test_backup_preserves_catalog_and_game_and_refuses_reuse(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            game = root / 'game'
            (game / 'Data').mkdir(parents=True)
            (game / 'SkyrimSE.exe').write_bytes(b'original executable')
            (game / 'Data/Skyrim - Shaders.bsa').write_bytes(b'original shaders')
            (game / 'skse64_loader.exe').write_bytes(b'original loader')
            catalog = root / 'ContentCatalog.txt'
            catalog.write_bytes(b'original catalog')
            record = game_setup.backup_files(game, catalog, root / 'backup')
            self.assertEqual((root / 'backup/game/SkyrimSE.exe').read_bytes(), b'original executable')
            self.assertEqual((root / 'backup/ContentCatalog.txt').read_bytes(), b'original catalog')
            self.assertEqual(catalog.read_bytes(), b'original catalog')
            self.assertEqual((game / 'skse64_loader.exe').read_bytes(), b'original loader')
            self.assertIn('Data/Skyrim - Shaders.bsa', record['game_files'])
            with self.assertRaises(FileExistsError):
                game_setup.backup_files(game, catalog, root / 'backup')
