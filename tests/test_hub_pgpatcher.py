import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zlib


class PGPatcherTests(unittest.TestCase):
    def test_archive_warnings_only_explain_confirmed_base_game_cases(self):
        from modlab.resources.mo2_hub.pgpatcher import review_warnings
        with TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'Data').mkdir()
            marketplace = root / 'Data/MarketplaceTextures.bsa'
            marketplace.write_bytes(b'BSA\x00')
            (root / 'Skyrim_Default.ini').write_text('[Archive]\nsResourceArchiveList2=Skyrim - Textures0.bsa, Skyrim - Patch.bsa\n')
            missing = root / 'Data/Skyrim - Patch.bsa'
            warnings = ('[now] [warning] BSA file MarketplaceTextures.bsa not loaded by any active plugin or INI.',
                        '[now] [warning] BSA is in INI but does not exist: ' + str(missing),
                        '[now] [warning] BSA file Missing Quest.bsa not loaded by any active plugin or INI.')
            resolve = lambda name: str(root / 'Data' / name) if (root / 'Data' / name).is_file() else ''
            held, explained = review_warnings(warnings, root, resolve)
            self.assertEqual(held, (warnings[2],))
            self.assertEqual(len(explained), 2)
            # A mod-provided namesake, a different missing path, or changed default
            # configuration must not inherit this exception.
            held, _ = review_warnings(warnings[:1], root, lambda name: str(root / 'mods' / name))
            self.assertEqual(held, warnings[:1])
            (root / 'Skyrim_Default.ini').write_text('[Archive]\nsResourceArchiveList2=Other.bsa\n')
            held, _ = review_warnings(warnings[1:2], root, resolve)
            self.assertEqual(held, warnings[1:2])
            altered = warnings[1].replace(str(missing), str(root / 'elsewhere/Skyrim - Patch.bsa'))
            held, _ = review_warnings((altered,), root, resolve)
            self.assertEqual(held, (altered,))

    def test_graphics_input_state_includes_synthesis_but_excludes_own_output(self):
        import sys
        from types import SimpleNamespace as Obj
        from unittest.mock import patch
        from modlab.resources.mo2_hub.synthesis_workflow import input_state
        with TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'Data').mkdir()
            names = ('Skyrim.esm', 'Chosen Patch.esp', 'PG_1.esp')
            for name in names: (root / 'Data' / name).write_bytes(name.encode())
            providers = dict(zip(names, ('Game', 'Synthesis output', 'Graphics output')))
            plugins = Obj(pluginNames=lambda: names, loadOrder=lambda name: names.index(name), priority=names.index, origin=providers.get)
            mods = Obj(allMods=lambda: (), priority=lambda name: 0)
            host = Obj(pluginList=lambda: plugins, modList=lambda: mods,
                       managedGame=lambda: Obj(gameDirectory=lambda: Obj(absolutePath=lambda: str(root))),
                       profilePath=lambda: str(root / 'profile'), resolvePath=lambda name: str(root / 'Data' / name))
            with patch.dict(sys.modules, {'mobase': Obj(ModState=Obj(ACTIVE=1))}):
                result = input_state(host, (), output_mod='Graphics output')
            self.assertEqual(result['order'], ['Skyrim.esm', 'Chosen Patch.esp'])

    def test_generated_plugin_masters_are_checked_and_ordered(self):
        from modlab.resources.mo2_hub.pgpatcher import verify_output
        from tests.test_hub_synthesis import plugin
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'PG_1.esp').write_bytes(plugin('PGPatcher.esp'))
            (root / 'PGPatcher.esp').write_bytes(plugin('Skyrim.esm'))
            log = 'PGPatcher took 2 seconds to complete'
            result = verify_output(root, log, 0, ['Skyrim.esm'])
            self.assertEqual(result.plugins, ('PGPatcher.esp', 'PG_1.esp'))
            with self.assertRaisesRegex(ValueError, 'masters'):
                verify_output(root, log, 0, [])

    def test_config_preserves_author_exclusions_and_confines_output(self):
        from modlab.resources.mo2_hub.pgpatcher import make_config
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = make_config({'renderer': 'Community Shaders', 'pbr': True}, root / 'game', root / 'mo2', root / 'job/output', 'Steam')
            params = settings['params']
            self.assertTrue(params['shaderpatcher']['truepbr'])
            self.assertFalse(params['output']['zip'])
            self.assertIn('*\\lod\\*', params['processing']['blocklist'])
            self.assertIn('Skyrim - Textures8.bsa', params['processing']['vanillabsalist'])
            self.assertEqual(params['modmanager']['type'], 2)
            self.assertEqual(settings['ui']['language'], 'en')
            with self.assertRaisesRegex(ValueError, 'Data'):
                make_config({'renderer': 'Community Shaders'}, root / 'game', root / 'mo2', root / 'game/Data/output', 'Steam')

    def test_pbr_is_not_enabled_for_enb_and_mesh_only_mode_does_not_strip_materials(self):
        from modlab.resources.mo2_hub.pgpatcher import make_config
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, 'Community Shaders'):
                make_config({'renderer': 'ENB', 'pbr': True}, root / 'game', root / 'mo2', root / 'output', 'Steam')
            settings = make_config({'renderer': 'Mesh fixes only', 'fix_lighting': True}, root / 'game', root / 'mo2', root / 'output', 'GOG')
            self.assertEqual(settings['params']['game']['type'], 1)
            self.assertFalse(any(settings['params']['shaderpatcher'].values()))
            self.assertFalse(settings['params']['postpatcher']['disableprepatchedmaterials'])

    def test_completion_does_not_accept_errors_or_partial_mesh_receipt(self):
        from modlab.resources.mo2_hub.pgpatcher import verify_output
        with TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'meshes').mkdir()
            mesh = b'Gamebryo File Format, Version 20.2.0.7\n' + bytes(40)
            (root / 'meshes/example.nif').write_bytes(mesh)
            receipt = {'meshes\\example.nif': {'crc32original': 12, 'crc32patched': zlib.crc32(mesh)}}
            (root / 'ParallaxGen_Diff.json').write_text(json.dumps(receipt))
            complete = '[2026-09-06] [info] PGPatcher took 1 seconds to complete (does not include time in user interface)'
            result = verify_output(root, complete, 0, [])
            self.assertIn('meshes/example.nif', result.hashes)
            self.assertEqual(result.checked_meshes, 1)
            with self.assertRaisesRegex(ValueError, 'error'):
                verify_output(root, '[2026-09-06] [critical] Missing input\n' + complete, 0, [])
            (root / 'meshes/example.nif').write_bytes(mesh + b'changed')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                verify_output(root, complete, 0, [])

    def test_empty_run_is_explicit_and_not_a_generated_success(self):
        from modlab.resources.mo2_hub.pgpatcher import verify_output
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = '[now] [warning] Output directory is empty. No files were generated.\nPGPatcher took 2 seconds to complete'
            result = verify_output(root, log, 0, [])
            self.assertFalse(result.hashes)
            (root / 'ParallaxGen_Diff.json').write_text('{}')
            self.assertFalse(verify_output(root, log, 0, []).hashes)
            with self.assertRaisesRegex(ValueError, 'completion'):
                verify_output(root, '', 0, [])

    def test_unexpected_files_and_traversing_receipts_are_refused(self):
        from modlab.resources.mo2_hub.pgpatcher import verify_output
        with TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'unexpected.exe').write_bytes(b'app')
            log = 'PGPatcher took 2 seconds to complete'
            with self.assertRaisesRegex(ValueError, 'Unexpected'):
                verify_output(root, log, 0, [])
            (root / 'unexpected.exe').unlink()
            (root / 'ParallaxGen_Diff.json').write_text(json.dumps({'../outside.nif': {'crc32patched': 2}}))
            with self.assertRaises(ValueError):
                verify_output(root, log, 0, [])
