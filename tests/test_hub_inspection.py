"""Host observation must retain partial failures and active profile identity."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as Obj
import unittest

from modlab.resources.mo2_hub.inspection import collect_setup


class HubInspectionTests(unittest.TestCase):
    def test_normal_inspection_compares_dll_minimum_against_actual_skse_loader_version(self):
        import struct
        from tests.test_hub_native import dll_fixture, packed
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('skse64_loader.exe', 'skse64_1_6_1170.dll', 'skse.pex'):
                (root / name).write_bytes(b'fixture')
            host = self.host(root)
            host.listDirectories = lambda path: {'': ['SKSE'], 'SKSE': ['Plugins']}.get(path, [])
            host.findFileInfos = lambda path, predicate: [Obj(filePath='example.dll', origins=['Example'], archive='')] if path == 'SKSE/Plugins' else []
            host.resolvePath = lambda path: str(root / 'example.dll') if path.lower().endswith('.dll') else str(root / 'skse.pex') if path.lower() == 'scripts/skse.pex' else ''
            for minimum, loader_version, expected in (
                    (packed(2, 2, 5), '2.2.8.0', None),
                    (packed(2, 2, 5), '0.2.2.8', None),
                    (packed(2, 2, 5), '0, 2, 2, 8', None),
                    (packed(2, 3, 0), '2.2.8.0', 'native-runtime-incompatible'),
                    (packed(2, 2, 5), '', 'native-runtime-unknown')):
                data = bytearray(dll_fixture(flags=6))
                struct.pack_into('<I', data, 1536 + 844, minimum)
                (root / 'example.dll').write_bytes(data)
                result = collect_setup(host, version_reader=lambda path: loader_version if path.endswith('skse64_loader.exe') else '1.6.1170.0')
                native = [f.code for f in result.generated_findings if f.code.startswith('native-runtime-')]
                self.assertEqual([expected] if expected else [], native)
                if expected == 'native-runtime-incompatible':
                    finding = next(f for f in result.generated_findings if f.code == expected)
                    self.assertIn('Required SKSE: 2.3.0.0', finding.detail)
                    self.assertIn('Installed SKSE loader: 2.2.8.0', finding.detail)

    def test_normal_inspection_and_launch_detect_changed_recorded_game_files(self):
        import hashlib
        from unittest.mock import Mock, patch
        from modlab.resources.mo2_hub import game_setup
        from modlab.resources.mo2_hub.launch import launch_game
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'SkyrimSE.exe').write_bytes(b'original')
            (root / 'steam_api64.dll').write_bytes(b'steam')
            host = self.host(root)
            host.getPluginDataPath = lambda: str(root / 'plugin-data')
            host.startApplication = Mock()
            expected = {'SkyrimSE.exe': hashlib.sha256(b'original').hexdigest()}
            with patch.object(game_setup, 'TARGET_FILES', expected):
                game_setup.remember_baseline(root, root / 'plugin-data/modlab')
                result = collect_setup(host, version_reader=lambda _: '1.6.1170.0')
                self.assertTrue(any(f.code == 'game-baseline-checked' for f in result.generated_findings))
                (root / 'SkyrimSE.exe').write_bytes(b'updated by Steam')
                result = collect_setup(host, version_reader=lambda _: '1.7.104.0')
                self.assertTrue(any(f.code == 'game-baseline-changed' for f in result.generated_findings))
                # Exercise the real collector/assessment/launch guard; no game process is started.
                with patch('modlab.resources.mo2_hub.launch.context_signature', return_value='same'), \
                     patch('modlab.resources.mo2_hub.skse.require_game_closed'):
                    with self.assertRaisesRegex(ValueError, 'recorded Skyrim 1.6.1170 setup has changed'):
                        launch_game(host, lambda _: '1.7.104.0')
                host.startApplication.assert_not_called()

    def test_active_order_uses_current_priorities_after_sorting(self):
        from modlab.resources.mo2_hub.inspection import plugin_load_orders
        names = ['Skyrim.esm', 'A.esp', 'Disabled.esp', 'B.esp']
        cached = {'Skyrim.esm': 0, 'A.esp': 1, 'Disabled.esp': -1, 'B.esp': 2}
        current = ['Skyrim.esm', 'B.esp', 'Disabled.esp', 'A.esp']
        plugins = Obj(pluginNames=lambda: names, priority=current.index, loadOrder=cached.__getitem__)
        self.assertEqual({'Skyrim.esm': 0, 'B.esp': 1, 'Disabled.esp': -1, 'A.esp': 2},
                         plugin_load_orders(plugins))

    def host(self, root, master_reader=lambda name: ("Skyrim.esm",)):
        plugins = Obj(pluginNames=lambda: ["Example.esp"], loadOrder=lambda name: 2, priority=lambda name: 2,
                      masters=master_reader, origin=lambda name: "Example")
        files = [Obj(filePath="SKSE/Plugins/example.dll", origins=["Example"], archive="")]
        return Obj(
            managedGame=lambda: Obj(gameName=lambda: "Skyrim Special Edition",
                                    gameDirectory=lambda: Obj(absolutePath=lambda: str(root))),
            profile=lambda: Obj(name=lambda: "Adventure"), pluginList=lambda: plugins,
            findFileInfos=lambda path, predicate: [f for f in files if predicate(f)],
            listDirectories=lambda path: [],
        )

    def test_collects_real_paths_and_host_metadata_without_inventing_version(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "skse64_loader.exe").write_bytes(b"fixture")
            result = collect_setup(self.host(root), version_reader=lambda path: "")
        self.assertEqual("Adventure", result.profile)
        self.assertEqual("", result.runtime)
        self.assertTrue(result.skse_loader_present)
        self.assertEqual(("Skyrim.esm",), result.plugins[0].masters)
        self.assertFalse(result.archives_inspected)
        self.assertTrue(any("runtime" in error.lower() for error in result.errors))

    def test_master_api_error_is_retained_as_incomplete_observation(self):
        def failed(name):
            raise RuntimeError("unreadable header")
        with TemporaryDirectory() as directory:
            result = collect_setup(self.host(Path(directory), failed), version_reader=lambda p: "1.7.104.0")
        self.assertTrue(any("Example.esp" in error and "unreadable header" in error for error in result.errors))

    def test_profile_switch_invalidates_collected_context(self):
        with TemporaryDirectory() as directory:
            host = self.host(Path(directory))
            names = iter(["Adventure", "Other"])
            host.profile = lambda: Obj(name=lambda: next(names))
            result = collect_setup(host, version_reader=lambda p: "1.7.104.0")
        self.assertTrue(any("profile changed" in error.lower() for error in result.errors))

    def test_nested_native_plugins_are_found_and_paths_are_relative(self):
        with TemporaryDirectory() as directory:
            host = self.host(Path(directory))
            host.listDirectories = lambda path: {'': ['SKSE'], 'SKSE': ['Plugins']}.get(path, [])
            host.findFileInfos = lambda path, predicate: [Obj(
                filePath=r'C:\mods\Native\SKSE\Plugins\example.dll', origins=['Native'], archive='')
            ] if path == 'SKSE/Plugins' else []
            result = collect_setup(host, version_reader=lambda path: '1.7.104.0')
        self.assertEqual(['SKSE/Plugins/example.dll'], [a.path for a in result.assets])

    def test_single_provider_address_database_is_included_in_foundation_check(self):
        with TemporaryDirectory() as directory:
            host = self.host(Path(directory))
            host.resolvePath = lambda path: ''
            host.listDirectories = lambda path: {'': ['SKSE'], 'SKSE': ['Plugins']}.get(path, [])
            database = Obj(filePath=r'C:\mods\Library\SKSE\Plugins\versionlib-1-6-1170-0.bin',
                           origins=['Old library'], archive='')
            host.findFileInfos = lambda path, predicate: [database] if path == 'SKSE/Plugins' and predicate(database) else []
            result = collect_setup(host, version_reader=lambda path: '1.7.104.0')
        self.assertEqual(['SKSE/Plugins/versionlib-1-6-1170-0.bin'], [a.path for a in result.assets])
        self.assertTrue(any(f.code == 'address-library-runtime' for f in result.generated_findings))

    def test_interrupted_patch_request_remains_a_blocker_in_normal_inspection(self):
        from modlab.resources.mo2_hub.synthesis import begin_pending, update_pending, clear_pending
        from tests.test_hub_synthesis import pipeline
        with TemporaryDirectory() as directory:
            root = Path(directory)
            host = self.host(root)
            host.profilePath = lambda: str(root)
            host.modsPath = lambda: str(root / 'mods')
            attempt = begin_pending(root, pipeline())
            update_pending(root, attempt, error='Helper is missing')
            result = collect_setup(host, version_reader=lambda p: '1.7.104.0')
            finding = next(f for f in result.generated_findings if f.code == 'patching-pending')
            self.assertEqual('Blocked', finding.level)
            self.assertIn('Helper is missing', finding.detail)
            clear_pending(root, attempt)
            result = collect_setup(host, version_reader=lambda p: '1.7.104.0')
            self.assertFalse(any(f.code.startswith('patching-pending') for f in result.generated_findings))

    def test_unfinished_archive_queue_is_not_hidden_by_active_setup_checks(self):
        from modlab.resources.mo2_hub.install_queue import create_queue, dismiss_queue
        with TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'Unattempted outfit.zip'; archive.write_bytes(b'archive')
            host = self.host(root)
            host.profilePath = lambda: str(root)
            host.modsPath = lambda: str(root / 'mods')
            queue = create_queue(root, str(root), [archive])
            result = collect_setup(host, version_reader=lambda p: '1.7.104.0')
            finding = next(f for f in result.generated_findings if f.code == 'installation-queue-pending')
            self.assertIn('Unattempted outfit.zip', finding.detail)
            dismiss_queue(queue)
            self.assertTrue(archive.exists())
            result = collect_setup(host, version_reader=lambda p: '1.7.104.0')
            self.assertFalse(any(f.code == 'installation-queue-pending' for f in result.generated_findings))
