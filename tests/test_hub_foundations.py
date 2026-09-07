from pathlib import Path
from tempfile import TemporaryDirectory
import struct
import unittest
from unittest.mock import patch

from modlab.resources.mo2_hub.assessment import Asset, Plugin, SetupSnapshot, assess


class FoundationTests(unittest.TestCase):
    def test_engine_fixes_7020_requires_preloader_in_game_root(self):
        from dataclasses import replace
        from modlab.resources.mo2_hub.foundations import inspect_foundations
        from tests.test_hub_native import dll_fixture
        with TemporaryDirectory() as directory:
            root = Path(directory); dll = root / 'EngineFixes.dll'
            dll.write_bytes(dll_fixture(flags=6, extended=2))
            setup = replace(self.setup(root, assets=(Asset('SKSE/Plugins/EngineFixes.dll', ('Engine Fixes',)),)), runtime='1.6.1170.0')
            with patch('modlab.resources.mo2_hub.outputs.digest', return_value=
                       '5d1384acfb523abd1333f5af71af0b7d131b6ebb1a0ee6b3edff86fb4c93adf3'):
                findings = inspect_foundations(setup, lambda p: str(dll) if p.endswith('.dll') else '')
                missing = [f for f in findings if f.code == 'engine-fixes-preloader-missing']
                self.assertEqual(1, len(missing))
                self.assertEqual('Blocked', missing[0].level)
                self.assertIn('725261', missing[0].action)
                (root / 'd3dx9_42.dll').write_bytes(b'present')
                findings = inspect_foundations(setup, lambda p: str(dll) if p.endswith('.dll') else '')
                self.assertFalse(any(f.code == 'engine-fixes-preloader-missing' for f in findings))

    def test_engine_fixes_observed_loader_failure_is_scoped_to_binary_and_runtime(self):
        from modlab.resources.mo2_hub.foundations import inspect_foundations
        from tests.test_hub_native import dll_fixture
        from dataclasses import replace
        checksum = '26af56098f739821558ac07e1b5730a223bcd8dd7af87ef1edb0288c22cae179'
        with TemporaryDirectory() as directory:
            root = Path(directory); dll = root / 'EngineFixes.dll'
            dll.write_bytes(dll_fixture(flags=6, extended=2))
            setup = self.setup(root, assets=(Asset('SKSE/Plugins/EngineFixes.dll', ('Renamed provider',)),))
            for identity, runtime, blocked in ((checksum, '1.6.1170.0', True),
                                               (checksum, '1.6.1170', True),
                                               (checksum, '1.7.104.0', False),
                                               ('other build', '1.6.1170.0', False)):
                with self.subTest(identity=identity, runtime=runtime):
                    with patch('modlab.resources.mo2_hub.outputs.digest', return_value=identity):
                        findings = inspect_foundations(replace(setup, runtime=runtime),
                            lambda p: str(dll) if p.lower().endswith('.dll') else '')
                    failures = [f for f in findings if f.code == 'native-functional-incompatible']
                    self.assertEqual(blocked, bool(failures))
                    if blocked:
                        self.assertEqual('Blocked', failures[0].level)
                        self.assertIn('Renamed provider', failures[0].detail)
                        self.assertIn('17230', failures[0].action)
                        self.assertIn('complete', failures[0].action)

    def test_ostim_hash_read_failure_keeps_other_native_findings(self):
        from modlab.resources.mo2_hub.foundations import inspect_foundations
        from tests.test_hub_native import dll_fixture
        with TemporaryDirectory() as directory:
            root = Path(directory); dll = root / 'fixture.dll'
            dll.write_bytes(dll_fixture(version=False))
            setup = self.setup(root, assets=(Asset('SKSE/Plugins/old.dll', ('Wrong runtime',)),
                                            Asset('SKSE/Plugins/OStim.dll', ('Unreadable framework',))))
            with patch('modlab.resources.mo2_hub.outputs.digest', side_effect=PermissionError('File is locked')):
                findings = inspect_foundations(setup, lambda p: str(dll) if p.endswith('.dll') else '')
            self.assertTrue(any(f.code == 'native-runtime-incompatible' for f in findings))
            self.assertTrue(any(f.code == 'native-inspection-incomplete' and 'Unreadable framework' in f.detail
                                for f in findings))

    def test_other_ostim_binary_can_be_hashed_without_becoming_a_known_exception(self):
        from modlab.resources.mo2_hub.foundations import inspect_foundations
        from tests.test_hub_native import dll_fixture
        with TemporaryDirectory() as directory:
            root = Path(directory); dll = root / 'OStim.dll'
            dll.write_bytes(dll_fixture(flags=6, extended=2))
            setup = self.setup(root, assets=(Asset('SKSE/Plugins/OStim.dll', ('Other release',)),))
            findings = inspect_foundations(setup, lambda p: str(dll) if p.endswith('.dll') else '')
            self.assertFalse(any(f.code == 'native-functional-incompatible' for f in findings))

    def test_bundled_papyrusutil_mismatch_names_a_candidate_without_recommending_a_dll_only_swap(self):
        from modlab.resources.mo2_hub.foundations import inspect_foundations
        from tests.test_hub_native import dll_fixture, packed
        from dataclasses import replace
        with TemporaryDirectory() as directory:
            root = Path(directory); dll = root / 'PapyrusUtil.dll'
            data = bytearray(dll_fixture(flags=0, versions=(packed(),)))
            data[1544:1556] = b'PapyrusUtil\0'; dll.write_bytes(data)
            setup = replace(self.setup(root, assets=(Asset('SKSE/Plugins/PapyrusUtil.dll', ('Bundled framework',)),)),
                            runtime='1.6.1170.0')
            finding = next(f for f in inspect_foundations(setup, lambda p: str(dll) if p.endswith('.dll') else '')
                           if f.code == 'native-runtime-incompatible')
            self.assertIn('4.6', finding.action)
            self.assertIn('13048', finding.action)
            self.assertIn('bundled', finding.action.lower())
            self.assertIn('scripts', finding.action.lower())

    def test_author_functional_exception_overrides_a_passing_declaration_only_for_exact_release(self):
        from modlab.resources.mo2_hub.foundations import inspect_foundations
        from tests.test_hub_native import dll_fixture
        from dataclasses import replace
        with TemporaryDirectory() as directory:
            root = Path(directory); dll = root / 'OStim.dll'; dll.write_bytes(dll_fixture(flags=6, extended=2))
            setup = self.setup(root, assets=(Asset('SKSE/Plugins/OStim.dll', ('Renamed framework',)),))
            for checksum, runtime, blocked in (
                ('62d531c5dd138fcf9bb909487e7a4b61bdfecd0b584b5a8d570bc0d8cce1f078', '1.7.104.0', True),
                ('62d531c5dd138fcf9bb909487e7a4b61bdfecd0b584b5a8d570bc0d8cce1f078', '1.6.1170.0', False),
                ('different release', '1.7.104.0', False)):
                # Only replace file identity; parsing and assessment use real code.
                with patch('modlab.resources.mo2_hub.outputs.digest', return_value=checksum):
                    findings = inspect_foundations(replace(setup, runtime=runtime), lambda p: str(dll) if p.endswith('.dll') else '')
                self.assertEqual(blocked, any(f.code == 'native-functional-incompatible' for f in findings))

    def test_incompatible_dll_finding_links_the_winning_mod_metadata(self):
        from modlab.resources.mo2_hub.foundations import inspect_foundations
        from modlab.resources.mo2_hub.mod_documents import download_page
        from tests.test_hub_native import dll_fixture
        with TemporaryDirectory() as directory:
            root = Path(directory); mod = root / 'Renamed source'; mod.mkdir()
            (mod / 'meta.ini').write_text('[General]\ngameName=SkyrimSE\nmodid=17230\n')
            dll = mod / 'enginefixes.dll'; dll.write_bytes(dll_fixture(version=False))
            options = {'source_page': lambda name: download_page(root, name)}
            findings = inspect_foundations(self.setup(root, assets=(Asset('SKSE/Plugins/EngineFixes.dll', (mod.name, 'Losing mod')),)),
                                          lambda p: str(dll) if p.lower().endswith('.dll') else '', **options)
            finding = next(f for f in findings if f.code == 'native-runtime-incompatible')
            self.assertIn('https://www.nexusmods.com/skyrimspecialedition/mods/17230?tab=files', finding.action)
            self.assertIn('Renamed source', finding.detail)

    def test_missing_address_database_requests_dependency_not_replacement_dll(self):
        from modlab.resources.mo2_hub.foundations import inspect_foundations
        from tests.test_hub_native import dll_fixture
        from dataclasses import replace
        with TemporaryDirectory() as directory:
            root = Path(directory)
            dll = root / 'example.dll'; dll.write_bytes(dll_fixture())
            setup = replace(self.setup(root, assets=(Asset('SKSE/Plugins/example.dll', ('My mod',)),)),
                            runtime='1.6.1170.0')
            findings = inspect_foundations(setup, lambda p: str(dll) if p.lower().endswith('.dll') else '')
            self.assertEqual(['native-dependency-missing'], [f.code for f in findings])
            self.assertEqual('Blocked', findings[0].level)
            self.assertIn('32444', findings[0].action)
            self.assertNotIn('disable it and dependent mods', findings[0].action)
            database = root / 'versionlib-1-6-1170-0.bin'; database.write_bytes(b'database')
            findings = inspect_foundations(setup, lambda p: str(dll) if p.lower().endswith('.dll') else str(database))
            self.assertFalse(any(f.level == 'Blocked' for f in findings))

    def test_missing_database_does_not_hide_incompatible_structure_layout(self):
        from modlab.resources.mo2_hub.foundations import inspect_foundations
        from tests.test_hub_native import dll_fixture
        with TemporaryDirectory() as directory:
            root = Path(directory)
            dll = root / 'old.dll'; dll.write_bytes(dll_fixture(flags=1, extended=2))
            findings = inspect_foundations(self.setup(root, assets=(Asset('SKSE/Plugins/old.dll', ('Old mod',)),)),
                                          lambda p: str(dll) if p.lower().endswith('.dll') else '')
            self.assertTrue(any(f.code == 'native-runtime-incompatible' and '1.6.629' in f.detail for f in findings))

    def test_fsmp4_menu_framework_is_optional_and_does_not_block_physics(self):
        from modlab.resources.mo2_hub.foundations import inspect_foundations
        from modlab.resources.mo2_hub.native import NativeInfo, packed_version
        with TemporaryDirectory() as directory:
            root = Path(directory); dependency = root/'SKSEMenuFramework.dll'
            setup = self.setup(root, assets=(Asset('SKSE/Plugins/hdtSMP64.dll', ('My physics',)),))
            resolve = lambda p: str(dependency) if p.casefold().endswith('sksemenuframework.dll') else ''
            def native(version):
                return NativeInfo(0x8664,1787774121,('SKSEPlugin_Load','SKSEPlugin_Version'),1,
                                  'hdtsmp64',2,1,plugin_version=packed_version(version))
            with patch('modlab.resources.mo2_hub.foundations.inspect_binary', return_value=native('4.1.1.0')):
                # Supply a concrete engine path; the dependency remains absent.
                findings = inspect_foundations(setup, lambda p: resolve(p) or str(root/'hdtSMP64.dll'))
                problem = next(f for f in findings if 'Menu Framework' in f.title)
                self.assertEqual('Info', problem.level)
                self.assertFalse(any(f.level == 'Blocked' for f in findings))
                self.assertIn('optional', problem.explanation.lower())
                self.assertIn('configs.json', problem.action)
                self.assertIn('120352', problem.action)
                self.assertIn('My physics', problem.detail)
                dependency.write_bytes(b'present; its runtime is checked separately')
                findings = inspect_foundations(setup, lambda p: resolve(p) or str(root/'hdtSMP64.dll'))
                self.assertFalse(any(f.code == 'fsmp-optional-menu' for f in findings))
            dependency.unlink()
            with patch('modlab.resources.mo2_hub.foundations.inspect_binary', return_value=native('3.5.0.0')):
                findings = inspect_foundations(setup, lambda p: resolve(p) or str(root/'hdtSMP64.dll'))
                self.assertFalse(any(f.code == 'fsmp-optional-menu' for f in findings))

    def setup(self, root, **kwargs):
        return SetupSnapshot('Skyrim Special Edition', '1.7.104.0', 'Test', str(root), **kwargs)

    def test_script_only_skyui_requires_skse_but_disabled_copy_does_not(self):
        for order, blocked in ((1, True), (-1, False)):
            findings = assess(self.setup('.', plugins=(Plugin('SkyUI_SE.esp', order, origin='My interface'),))).findings
            self.assertEqual(blocked, any(f.code == 'skse-loader-missing' for f in findings))

    def test_racemenu_requires_skse_without_waiting_for_native_dll_detection(self):
        findings = assess(self.setup('.', plugins=(Plugin('RaceMenu.esp', 1),))).findings
        self.assertTrue(any(f.code == 'skse-loader-missing' for f in findings))

    def test_plain_skyrim_does_not_require_character_or_native_foundations(self):
        from modlab.resources.mo2_hub.foundations import inspect_foundations
        with TemporaryDirectory() as directory:
            self.assertEqual((), inspect_foundations(self.setup(directory), lambda p: ''))

    def test_incomplete_loader_is_reported_before_launch(self):
        from modlab.resources.mo2_hub.foundations import inspect_foundations
        with TemporaryDirectory() as directory:
            root = Path(directory)
            setup = self.setup(root, skse_loader_present=True)
            findings = inspect_foundations(setup, lambda p: '')
            self.assertTrue(any(f.code == 'skse-files-incomplete' and '1_7_104' in f.detail for f in findings))
            (root / 'skse64_1_7_104.dll').write_bytes(b'dll')
            findings = inspect_foundations(setup, lambda p: '')
            self.assertTrue(any('scripts' in f.detail.lower() for f in findings))

    def test_other_runtime_address_library_is_review_not_universal_requirement(self):
        from modlab.resources.mo2_hub.foundations import inspect_foundations
        old = Asset('SKSE/Plugins/versionlib-1-6-1170-0.bin', ('Old address library',))
        current = Asset('SKSE/Plugins/versionlib-1-7-104-0.bin', ('Address library',))
        with TemporaryDirectory() as directory:
            findings = inspect_foundations(self.setup(directory, assets=(old,)), lambda p: '')
            finding = next(f for f in findings if f.code == 'address-library-runtime')
            self.assertEqual('Review', finding.level)
            self.assertIn('1-7-104-0', finding.action)
            self.assertEqual((), inspect_foundations(self.setup(directory, assets=(old, current)), lambda p: ''))

    def test_native_x86_dll_is_blocked_and_x64_is_not_claimed_runtime_compatible(self):
        from modlab.resources.mo2_hub.foundations import inspect_foundations
        with TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'example.dll'
            data = bytearray(256)
            data[:2] = b'MZ'
            struct.pack_into('<I', data, 0x3c, 128)
            data[128:132] = b'PE\0\0'
            struct.pack_into('<H', data, 132, 0x14c)
            binary.write_bytes(data)
            setup = self.setup(root, assets=(Asset('SKSE/Plugins/example.dll', ('Legacy mod',)),))
            findings = inspect_foundations(setup, lambda p: str(binary))
            finding = next(f for f in findings if f.code == 'native-wrong-architecture')
            self.assertEqual('Blocked', finding.level)
            self.assertIn('Legacy mod', finding.detail)
            struct.pack_into('<H', data, 132, 0x8664)
            binary.write_bytes(data)
            self.assertEqual(['native-inspection-incomplete'],
                [f.code for f in inspect_foundations(setup, lambda p: str(binary))])
            self.assertTrue(any(f.code == 'native-compatibility-unverified' for f in assess(setup).findings))

    def test_unreadable_native_file_is_unknown_not_a_false_incompatibility(self):
        from modlab.resources.mo2_hub.foundations import inspect_foundations
        with TemporaryDirectory() as directory:
            setup = self.setup(directory, assets=(Asset('SKSE/Plugins/example.dll', ('Mod',)),))
            findings = inspect_foundations(setup, lambda p: str(Path(directory) / 'missing.dll'))
            self.assertEqual(['native-inspection-incomplete'], [f.code for f in findings])
            self.assertEqual('Unknown', findings[0].level)
