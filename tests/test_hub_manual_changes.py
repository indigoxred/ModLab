"""Exercise real change callbacks without requiring MO2's embedded Qt runtime."""
import ast
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as Obj
import sys
import unittest
import zlib
from unittest.mock import Mock, patch

from modlab.resources.mo2_hub import npc_workflow as npc, pgpatcher_workflow as graphics
from modlab.resources.mo2_hub.outputs import MANIFEST, digest, output_name


def hub_method(name):
    source = Path(npc.__file__).with_name('plugin.py')
    cls = next(n for n in ast.parse(source.read_text(encoding='utf-8')).body
               if isinstance(n, ast.ClassDef) and n.name == 'HubWindow')
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
    namespace = {'QApplication': Obj(activeModalWidget=lambda: None)}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace[name]


class ManualChangeTests(unittest.TestCase):
    def test_external_change_rechecks_without_running_helpers(self):
        host = Obj(recheck_pending=True, isVisible=lambda: True, processing=False,
                   installing=False, refresh=Mock(), finish_setup=Mock(),
                   automatic_signature='old result', change_timer=Mock())
        hub_method('recheck_changes')(host)
        host.refresh.assert_called_once()
        host.finish_setup.assert_not_called()
        self.assertFalse(host.recheck_pending)
        self.assertIsNone(host.automatic_signature)

    def test_external_change_waits_for_current_installation(self):
        host = Obj(recheck_pending=True, isVisible=lambda: True, processing=False,
                   installing=True, refresh=Mock(), finish_setup=Mock(), change_timer=Mock())
        hub_method('recheck_changes')(host)
        host.change_timer.start.assert_called_once()
        host.refresh.assert_not_called()
        host.finish_setup.assert_not_called()
        self.assertTrue(host.recheck_pending)

    def fixture(self, root, tool):
        profile = root / 'profile'; profile.mkdir()
        mods = root / 'mods'; mods.mkdir()
        target = mods / output_name('Test', str(profile), tool); target.mkdir()
        (target / 'mesh.nif').write_bytes(b'generated mesh')
        hashes = {'mesh.nif': digest(target / 'mesh.nif')}
        (target / MANIFEST).write_text(json.dumps(dict(owner='ModLab ' + tool,
            profile_path=str(profile), projects={}, hashes=hashes)))
        winner = root / 'other-mesh.nif'; winner.write_bytes(b'user selected mesh')
        modlist = Obj(state=lambda n: 1, setActive=Mock(return_value=True))
        host = Obj(profilePath=lambda: str(profile), profile=lambda: Obj(name=lambda: 'Test'),
                   modsPath=lambda: str(mods), modList=lambda: modlist,
                   resolvePath=lambda n: str(winner),
                   pluginList=lambda: Obj(loadOrder=lambda n: 1, priority=lambda n: 1),
                   onNextRefresh=Mock(), refresh=Mock())
        return host, target, hashes

    def test_npc_override_is_a_choice_not_an_automatic_rebuild(self):
        with TemporaryDirectory() as tmp:
            host, target, hashes = self.fixture(Path(tmp), 'NPC Appearance')
            saved = dict(hashes=hashes, input_state={'order': []})
            with patch.object(npc, 'state', return_value={'order': []}):
                with self.assertRaisesRegex(ValueError, 'overrid|provider'):
                    npc.saved_build_current(host, saved)
            self.assertEqual(b'generated mesh', (target / 'mesh.nif').read_bytes())
            host.modList().setActive.assert_not_called()

    def test_graphics_override_is_preserved_before_source_helpers_start(self):
        with TemporaryDirectory() as tmp:
            host, target, hashes = self.fixture(Path(tmp), 'Graphics')
            graphics.choices_path(host).write_text(json.dumps(dict(status='applied', hashes=hashes)))
            modules = {'mobase': Obj(ModState=Obj(ACTIVE=1)),
                       'PyQt6.QtCore': Obj(QTimer=Obj(singleShot=lambda d, f: f()))}
            with patch.dict(sys.modules, modules), patch.object(graphics, 'require_game_closed'):
                with self.assertRaisesRegex(ValueError, 'overrid|provider'):
                    graphics.withdraw_for_upstream(host, Mock())
            host.modList().setActive.assert_not_called()
            self.assertFalse(graphics.withdrawn_path(host).exists())

    def test_recorded_graphics_transformation_is_not_a_manual_npc_override(self):
        with TemporaryDirectory() as tmp:
            host, target, hashes = self.fixture(Path(tmp), 'NPC Appearance')
            downstream = Path(host.modsPath()) / graphics.own_name(host); downstream.mkdir()
            (downstream / 'mesh.nif').write_bytes(b'patched mesh')
            (downstream / 'ParallaxGen_Diff.json').write_text(json.dumps({'mesh.nif': {
                'crc32original': zlib.crc32(b'generated mesh'), 'crc32patched': zlib.crc32(b'patched mesh')}}))
            checked = {p.name: digest(p) for p in downstream.iterdir()}
            (downstream / MANIFEST).write_text(json.dumps(dict(owner='ModLab Graphics',
                profile_path=host.profilePath(), projects={}, hashes=checked)))
            graphics.choices_path(host).write_text(json.dumps(dict(status='applied', hashes=checked)))
            host.resolvePath = lambda n: str(downstream / n)
            saved = dict(hashes=hashes, input_state={'order': []})
            with patch.object(npc, 'state', return_value={'order': []}):
                self.assertTrue(npc.saved_build_current(host, saved))
                (downstream / 'mesh.nif').write_bytes(b'manual change')
                with self.assertRaises(ValueError):
                    npc.saved_build_current(host, saved)

    def test_graphics_explicit_new_choice_can_replace_previous_override(self):
        with TemporaryDirectory() as tmp:
            host, target, hashes = self.fixture(Path(tmp), 'Graphics')
            graphics.choices_path(host).write_text(json.dumps(dict(status='applied', hashes=hashes)))
            graphics.begin_pending(host, {'renderer': 'Community Shaders'})
            modules = {'mobase': Obj(ModState=Obj(ACTIVE=1)),
                       'PyQt6.QtCore': Obj(QTimer=Obj(singleShot=lambda d, f: f()))}
            with patch.dict(sys.modules, modules), patch.object(graphics, 'require_game_closed'):
                self.assertTrue(graphics.withdraw_for_upstream(host, Mock()))
            host.modList().setActive.assert_called_once_with(target.name, False)
            self.assertTrue(graphics.withdrawn_path(host).exists())

    def test_disabled_graphics_plugin_is_not_reactivated_by_preparation(self):
        with TemporaryDirectory() as tmp:
            host, target, hashes = self.fixture(Path(tmp), 'Graphics')
            (target / 'Generated.esp').write_bytes(b'plugin')
            hashes['Generated.esp'] = digest(target / 'Generated.esp')
            (target / MANIFEST).write_text(json.dumps(dict(owner='ModLab Graphics',
                profile_path=host.profilePath(), projects={}, hashes=hashes)))
            graphics.choices_path(host).write_text(json.dumps(dict(status='applied', hashes=hashes)))
            host.resolvePath = lambda n: str(target / n)
            host.pluginList = lambda: Obj(loadOrder=lambda n: -1)
            modules = {'mobase': Obj(ModState=Obj(ACTIVE=1)),
                       'PyQt6.QtCore': Obj(QTimer=Obj(singleShot=lambda d, f: f()))}
            with patch.dict(sys.modules, modules), patch.object(graphics, 'require_game_closed'):
                with self.assertRaisesRegex(ValueError, 'disabled'):
                    graphics.withdraw_for_upstream(host, Mock())
            host.modList().setActive.assert_not_called()
            self.assertFalse(graphics.withdrawn_path(host).exists())


if __name__ == '__main__':
    unittest.main()
