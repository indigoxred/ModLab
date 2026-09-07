from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from modlab.resources.mo2_hub.assessment import SetupSnapshot
from modlab.resources.mo2_hub.launch import choose_launcher


class LaunchTests(unittest.TestCase):
    def test_unresolved_native_or_graphics_work_prevents_starting_the_game(self):
        from modlab.resources.mo2_hub.assessment import Finding
        from modlab.resources.mo2_hub.launch import launch_game
        for level, code in (('Blocked', 'native-runtime-incompatible'), ('Unknown', 'native-runtime-unknown'),
                            ('Review', 'graphics-pending'), ('Review', 'graphics-stale'),
                            ('Review', 'graphics-withdrawn'), ('Unknown', 'graphics-inspection-incomplete')):
            setup = SetupSnapshot('Skyrim Special Edition', '1.7.104.0', 'Test', '.',
                generated_findings=(Finding(level, code, 'Native DLL needs attention', 'Bad declaration', 'Install the matching release'),))
            organizer = Mock()
            with patch('modlab.resources.mo2_hub.launch.context_signature', return_value='same'), \
                 patch('modlab.resources.mo2_hub.launch.collect_setup', return_value=setup), \
                 patch('modlab.resources.mo2_hub.skse.require_game_closed'):
                with self.assertRaisesRegex(ValueError, 'Native DLL needs attention') as caught:
                    launch_game(organizer, lambda _: '1.7.104.0')
                self.assertIn('Bad declaration', str(caught.exception))
            organizer.startApplication.assert_not_called()

    def setup_files(self, root):
        (root / 'SkyrimSE.exe').write_bytes(b'game')
        return SetupSnapshot('Skyrim Special Edition', '1.7.104.0', 'Test', str(root))

    def test_plain_profile_uses_game_executable_in_selected_game_root(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            result = choose_launcher(self.setup_files(root), lambda _: '')
            self.assertEqual(root / 'SkyrimSE.exe', result.executable)

    def test_installed_skse_is_preferred_and_requires_runtime_dll_and_scripts(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            setup = self.setup_files(root)
            (root / 'skse64_loader.exe').write_bytes(b'loader')
            with self.assertRaisesRegex(ValueError, '1_7_104'):
                choose_launcher(setup, lambda _: '')
            (root / 'skse64_1_7_104.dll').write_bytes(b'runtime')
            with self.assertRaisesRegex(ValueError, 'scripts'):
                choose_launcher(setup, lambda _: '')
            script = root / 'skse.pex'
            script.write_bytes(b'script')
            result = choose_launcher(setup, lambda _: str(script))
            self.assertEqual(root / 'skse64_loader.exe', result.executable)
            self.assertEqual('SKSE through MO2', result.label)

    def test_unknown_runtime_does_not_guess_a_skse_dll(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.setup_files(root)
            (root / 'skse64_loader.exe').write_bytes(b'loader')
            setup = SetupSnapshot('Skyrim Special Edition', '', 'Test', str(root))
            with self.assertRaisesRegex(ValueError, 'runtime'):
                choose_launcher(setup, lambda _: '')
