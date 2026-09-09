"""Current scenery must be proven from active sources and exact helper receipts."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as Obj
from unittest.mock import patch
import json
import unittest
import zlib

from modlab.resources.mo2_hub import scene_workflow as scene, scene_build
from modlab.resources.mo2_hub.assessment import Finding
from modlab.resources.mo2_hub.outputs import digest, output_name
import test_hub_scene_choices as fixtures


class SceneStatusTests(unittest.TestCase):
    def fixture(self, root):
        profile = root/'profile'; profile.mkdir()
        providers = fixtures.SceneChoiceTests().sources(root/'mods')
        host = Obj(profilePath=lambda: str(profile), profile=lambda: Obj(name=lambda: 'Test'),
                   modsPath=lambda: str(root/'mods'))
        target = root/'mods'/scene.own_name(host); target.mkdir()
        job = scene_build.prepare(root, str(profile), providers, {'roads': 'Roads', 'mountains': 'Mountains'},
            lambda meshes, directory: {name: [] for name in meshes}, lambda name: None)
        context = dict(profile_path=str(profile), roots=[(name, value['root']) for name, value in providers.items()])
        job['context'] = context
        with patch('modlab.resources.mo2_hub.skse.require_game_closed'):
            scene_build.publish(target, job, str(profile))
        job['status'] = 'Applied; file providers checked'
        scene.path_for(host).write_text(json.dumps(job))
        active = {target.name, *providers}
        host.modList = lambda: Obj(state=lambda name: 1 if name in active else 0)
        effective = {name: str(target/name) for name in job['hashes']}
        host.resolvePath = lambda name: effective.get(name, '')
        return host, target, job, context, active, effective

    def inspect(self, host, context, graphics=()):
        with patch.dict('sys.modules', {'mobase': Obj(ModState=Obj(ACTIVE=1))}), patch.object(scene, 'snapshot', return_value=context):
            return scene.inspect_scene(host, graphics_findings=graphics)

    def test_current_choices_report_each_component_and_only_the_selected_files(self):
        with TemporaryDirectory() as tmp:
            host, target, job, context, active, effective = self.fixture(Path(tmp))
            verified, findings = self.inspect(host, context)
            self.assertEqual(set(job['hashes']), set(verified))
            self.assertEqual(['Roads and bridges: Roads', 'Mountains and rocks: Mountains'], [f.title for f in findings])
            self.assertTrue(all(f.level == 'Info' for f in findings))
            self.assertNotIn('meshes/clutter/chest.nif', verified)
            self.assertIn('Roads and bridges: Roads', scene.choice_summary(findings))

    def test_disabled_or_overridden_result_is_actionable_and_does_not_restore_it(self):
        with TemporaryDirectory() as tmp:
            host, target, job, context, active, effective = self.fixture(Path(tmp))
            original = scene.path_for(host).read_bytes()
            active.remove(target.name)
            verified, findings = self.inspect(host, context)
            self.assertFalse(verified)
            self.assertEqual('graphics-scene-stale', findings[0].code)
            active.add(target.name)
            mesh = 'meshes/landscape/bridges/bridge01.nif'
            outside = Path(tmp)/'manual.nif'; outside.write_bytes(b'my replacement')
            effective[mesh] = str(outside)
            verified, findings = self.inspect(host, context)
            self.assertFalse(verified)
            self.assertEqual('graphics-scene-stale', findings[0].code)
            self.assertEqual(b'my replacement', outside.read_bytes())
            self.assertEqual(original, scene.path_for(host).read_bytes())
            self.assertNotIn('Applied', scene.choice_summary(findings))

    def test_pending_choice_does_not_inherit_the_previous_success(self):
        with TemporaryDirectory() as tmp:
            host, target, job, context, active, effective = self.fixture(Path(tmp))
            scene.begin_pending(host, {'roads': 'Objects'}, expected=None)
            verified, findings = self.inspect(host, context)
            self.assertFalse(verified)
            self.assertEqual(['graphics-scene-pending'], [f.code for f in findings])
            self.assertNotIn('graphics-scene-current', [f.code for f in findings])

    def test_only_a_current_graphics_build_with_matching_receipt_can_replace_selected_mesh(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            host, target, job, context, active, effective = self.fixture(root)
            graphics = root/'mods'/output_name('Test', host.profilePath(), 'Graphics')
            mesh = 'meshes/landscape/bridges/bridge01.nif'
            (graphics/mesh).parent.mkdir(parents=True); (graphics/mesh).write_bytes(b'patched bridge')
            receipt = {mesh: dict(crc32original=zlib.crc32((target/mesh).read_bytes()), crc32patched=zlib.crc32(b'patched bridge'))}
            diff = graphics/'ParallaxGen_Diff.json'; diff.write_text(json.dumps(receipt))
            manifest = dict(owner='ModLab Graphics', profile_path=host.profilePath(), projects={},
                            hashes={mesh: digest(graphics/mesh), 'ParallaxGen_Diff.json': digest(diff)})
            (graphics/'.modlab-output.json').write_text(json.dumps(manifest))
            effective[mesh] = str(graphics/mesh)
            current = (Finding('Info', 'graphics-current', 'Current', '', ''),)
            verified, findings = self.inspect(host, context, current)
            self.assertEqual(set(job['hashes']), set(verified))
            self.assertTrue(all(f.level == 'Info' for f in findings))
            verified, findings = self.inspect(host, context)
            self.assertFalse(verified, 'An old receipt alone must not pass when the graphics inputs changed')
            receipt[mesh]['crc32original'] = 123
            diff.write_text(json.dumps(receipt))
            manifest['hashes']['ParallaxGen_Diff.json'] = digest(diff)
            (graphics/'.modlab-output.json').write_text(json.dumps(manifest))
            verified, findings = self.inspect(host, context, current)
            self.assertFalse(verified, 'A receipt for a different input mesh must not certify this selection')

    def test_reset_requires_the_old_output_to_stay_disabled(self):
        with TemporaryDirectory() as tmp:
            host, target, job, context, active, effective = self.fixture(Path(tmp))
            scene.path_for(host).write_text(json.dumps(dict(profile_path=host.profilePath(), choices={}, status='reset')))
            active.remove(target.name)
            verified, findings = self.inspect(host, context)
            self.assertFalse(verified)
            self.assertEqual('graphics-scene-reset', findings[0].code)
            active.add(target.name)
            verified, findings = self.inspect(host, context)
            self.assertEqual('graphics-scene-stale', findings[0].code)

    def test_stale_graphics_texture_cannot_hide_behind_the_unchanged_source_dependency(self):
        from modlab.resources.mo2_hub.scene_choices import file_source
        from modlab.resources.mo2_hub.scene_assets import Catalog
        from test_hub_scene_assets import ArchiveFixture
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            host, target, job, context, active, effective = self.fixture(root)
            name = 'textures/external/bridge.dds'
            source = root/'mods/Textures'/name
            source.parent.mkdir(parents=True); source.write_bytes(b'original required texture')
            job['plan']['dependencies'][name] = file_source(source, 'Textures')
            context['roots'].append(('Textures', str(root/'mods/Textures')))
            job['context'] = context
            scene.path_for(host).write_text(json.dumps(job))
            outside = root/'mods/Old graphics'/name
            outside.parent.mkdir(parents=True); outside.write_bytes(b'changed downstream texture')
            effective[name] = str(outside)
            assets = Catalog(context['roots'], [], root/'cache', ArchiveFixture())
            with patch.object(scene, 'catalog', return_value=assets):
                verified, findings = self.inspect(host, context)
            self.assertFalse(verified)
            self.assertEqual('graphics-scene-stale', findings[0].code)
            self.assertEqual(b'changed downstream texture', outside.read_bytes())

    def test_captured_check_runs_without_calling_host_apis_from_the_worker(self):
        with TemporaryDirectory() as tmp:
            host, target, job, context, active, effective = self.fixture(Path(tmp))
            with patch.dict('sys.modules', {'mobase': Obj(ModState=Obj(ACTIVE=1))}), patch.object(scene, 'snapshot', return_value=context):
                # Gathering host state must not hash/extract files on the UI thread.
                with patch('modlab.resources.mo2_hub.outputs.read_manifest', side_effect=AssertionError('UI thread file verification')):
                    captured = scene.capture_inspection(host)
            frozen = json.loads(json.dumps(captured))
            def forbidden(*args, **kwargs):
                raise AssertionError('Worker called the host')
            host.resolvePath = host.profilePath = host.modList = host.modsPath = forbidden
            verified, findings = scene.evaluate_inspection(frozen)
            self.assertEqual(set(job['hashes']), set(verified))
            self.assertTrue(all(f.level == 'Info' for f in findings))
