from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
import json
from unittest.mock import patch

from modlab.resources.mo2_hub.pandora_workflow import source_signature, effective_issues, input_roots, activate_generated_plugins
from modlab.resources.mo2_hub.outputs import digest


class PandoraWorkflowTests(unittest.TestCase):
    def test_previous_receipt_with_empty_mod_entries_can_be_reused(self):
        from modlab.resources.mo2_hub.pandora_workflow import saved_build_current
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            organizer = SimpleNamespace(modsPath=lambda: str(root),
                profile=lambda: SimpleNamespace(name=lambda: 'Test'), profilePath=lambda: str(root / 'profile'))
            with patch('pathlib.Path.is_dir', return_value=True), \
                    patch('modlab.resources.mo2_hub.pandora_workflow.read_manifest', return_value={'hashes': {}}), \
                    patch('modlab.resources.mo2_hub.pandora_workflow.input_roots', return_value=[]), \
                    patch('modlab.resources.mo2_hub.pandora_workflow.locate_engine', return_value=root / 'engine.exe'):
                self.assertTrue(saved_build_current(organizer, {'sources': [['Scripts only', str(root), []]]}))
                self.assertFalse(saved_build_current(organizer, {'sources': [['Removed animation', str(root), [['meshes/a.hkx', 1, 2]]]]}))

    def test_script_only_mod_does_not_invalidate_animation_output(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'Animation/meshes/actors/character/behaviors/test.hkx'
            source.parent.mkdir(parents=True)
            source.write_bytes(b'behavior')
            script = root / 'SKSE/Scripts/skse.pex'
            script.parent.mkdir(parents=True)
            script.write_bytes(b'script')
            inputs = [('Animation', root / 'Animation')]
            before = source_signature(inputs)
            self.assertEqual(before, source_signature(inputs + [('SKSE', root / 'SKSE')]))
            script.write_bytes(b'updated script')
            self.assertEqual(before, source_signature([('SKSE', root / 'SKSE')] + inputs))
            patch_file = root / 'SKSE/Nemesis_Engine/mod/new/info.ini'
            patch_file.parent.mkdir(parents=True)
            patch_file.write_text('new patch')
            self.assertNotEqual(before, source_signature(inputs + [('SKSE', root / 'SKSE')]))

    def test_private_output_is_configured_before_starting_engine(self):
        from modlab.resources.mo2_hub.pandora_workflow import PandoraJob, run_job
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = root / 'runner'
            runner.mkdir()
            game = root / 'game'
            (game / 'Data').mkdir(parents=True)
            def start(*args):
                config = json.loads((runner / 'Settings.json').read_text())['games']['SkyrimSE']
                self.assertEqual(Path(config['outputPath']), root / 'output')
                self.assertTrue(Path(config['outputPath']).is_dir())
                self.assertEqual(Path(config['gameDataPath']), game / 'Data')
                return 1
            organizer = SimpleNamespace(managedGame=lambda: SimpleNamespace(gameDirectory=lambda: SimpleNamespace(
                absolutePath=lambda: str(game))), profile=lambda: SimpleNamespace(name=lambda: 'Test'),
                startApplication=start, waitForApplication=lambda *args: (True, 7))
            job = PandoraJob(root, runner / 'engine.exe', (), (), (), {'runtime': str(root), 'fnis_codes': []})
            with patch('modlab.resources.mo2_hub.pandora_workflow.check_context'):
                with self.assertRaisesRegex(ValueError, 'code 7'):
                    run_job(organizer, job, [])

    def test_generated_plugin_activation_is_verified(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = root / 'FNIS.esp'
            plugin.write_bytes(b'TES4')
            states = {'FNIS.esp': 1}
            plugins = SimpleNamespace(state=lambda name: states[name], setState=lambda name, value: states.update({name: value}))
            organizer = SimpleNamespace(pluginList=lambda: plugins, resolvePath=lambda _: str(plugin))
            self.assertEqual(activate_generated_plugins(organizer, root, {'FNIS.esp': digest(plugin)}, 2), ['FNIS.esp'])
            self.assertEqual(activate_generated_plugins(organizer, root, {'FNIS.esp': digest(plugin)}, 2), [])
            states['FNIS.esp'] = 1
            plugins.setState = lambda *args: None
            with self.assertRaisesRegex(ValueError, 'enable'):
                activate_generated_plugins(organizer, root, {'FNIS.esp': digest(plugin)}, 2)

    def test_active_mod_paths_are_read_from_mod_list(self):
        mods = SimpleNamespace(allMods=lambda: ['Enabled', 'Disabled'], priority=lambda _: 1,
            state=lambda name: 2 if name == 'Enabled' else 1,
            getMod=lambda _: SimpleNamespace(absolutePath=lambda: 'C:/mods/Enabled'))
        organizer = SimpleNamespace(modList=lambda: mods, profile=lambda: SimpleNamespace(name=lambda: 'Test'),
            profilePath=lambda: 'C:/profiles/Test', managedGame=lambda: SimpleNamespace(
                gameDirectory=lambda: SimpleNamespace(absolutePath=lambda: 'C:/game')))
        with patch.dict('sys.modules', {'mobase': SimpleNamespace(ModState=SimpleNamespace(ACTIVE=2))}):
            self.assertEqual([name for name, _ in input_roots(organizer)], ['Game Data', 'Enabled'])

    def test_hidden_source_changes_invalidate_build_and_own_output_does_not(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('Source', 'Other winner', 'Generated'):
                path = root / name / 'meshes/actors/character/behaviors/test.hkx'
                path.parent.mkdir(parents=True)
                path.write_bytes(b'old')
            inputs = [('Source', root / 'Source'), ('Other winner', root / 'Other winner')]
            before = source_signature(inputs)
            (root / 'Generated/meshes/actors/character/behaviors/test.hkx').write_bytes(b'output update')
            self.assertEqual(before, source_signature(inputs))
            (root / 'Source/meshes/actors/character/behaviors/test.hkx').write_bytes(b'changed underlying input')
            self.assertNotEqual(before, source_signature(inputs))
            self.assertNotEqual(source_signature(inputs), source_signature(list(reversed(inputs))))

    def test_effective_check_requires_our_output_to_win_not_just_same_bytes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / 'output'
            target.mkdir()
            ours = target / 'a.hkx'
            ours.write_bytes(b'generated')
            other = root / 'a.hkx'
            other.write_bytes(ours.read_bytes())
            organizer = SimpleNamespace(resolvePath=lambda _: str(other))
            self.assertTrue(effective_issues(organizer, target, {'a.hkx': digest(ours)}))
            organizer.resolvePath = lambda _: str(ours)
            self.assertEqual(effective_issues(organizer, target, {'a.hkx': digest(ours)}), [])
