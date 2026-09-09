from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as Obj
import unittest
from unittest.mock import patch
import json


class SceneWorkflowTests(unittest.TestCase):
    def test_new_loose_variant_of_a_selected_packed_asset_requires_reassessment(self):
        import zipfile
        from modlab.resources.mo2_hub import scene_workflow as scene, scene_build
        from modlab.resources.mo2_hub.scene_assets import Catalog
        from test_hub_scene_assets import ArchiveFixture
        with TemporaryDirectory() as tmp:
            root = Path(tmp); source = root/'mods/Trees A'; source.mkdir(parents=True)
            name = 'meshes/landscape/trees/pine.nif'
            archive = source/'Trees.bsa'
            with zipfile.ZipFile(archive, 'w') as package:
                package.writestr(name, b'packed pine')
            context = dict(roots=[('Trees A', str(source))], library='fixture-boundary',
                archives=[dict(provider='Trees A', path=str(archive), rank=[1, 1])])
            assets = Catalog(context['roots'], context['archives'], root/'retained-cache', ArchiveFixture())
            job = scene_build.prepare(root, 'A', assets.providers, {'trees': 'Trees A'},
                lambda meshes, directory: {path: [] for path in meshes}, lambda path: None)
            job['context'] = context
            target = root/'mods/Scene output'; target.mkdir()
            with patch('modlab.resources.mo2_hub.skse.require_game_closed'):
                scene_build.publish(target, job, 'A')
            effective = {path: str(target/path) for path in job['hashes']}
            with patch('modlab.resources.mo2_hub.archive_text.ArchiveReader', return_value=ArchiveFixture()):
                scene.verify_saved(target, job, context, effective)
                loose = source/name; loose.parent.mkdir(parents=True); loose.write_bytes(b'new installed patch')
                with self.assertRaisesRegex(ValueError, 'changed|variant'):
                    scene.verify_saved(target, job, context, effective)
            self.assertEqual(b'packed pine', (target/name).read_bytes())
            self.assertEqual(b'new installed patch', loose.read_bytes())

    def test_selected_component_update_is_reassessed_without_losing_preference(self):
        from modlab.resources.mo2_hub import scene_workflow as scene, scene_build
        from modlab.resources.mo2_hub.scene_choices import scan_provider
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'mods/Trees A'
            pine = source/'meshes/landscape/trees/pine.nif'
            pine.parent.mkdir(parents=True); pine.write_bytes(b'pine')
            providers = {'Trees A': scan_provider('Trees A', source)}
            job = scene_build.prepare(root, 'A', providers, {'trees': 'Trees A'},
                lambda meshes, directory: {name: [] for name in meshes}, lambda name: None)
            context = dict(roots=[('Trees A', str(source))])
            job['context'] = context
            target = root/'mods/Scene output'; target.mkdir()
            with patch('modlab.resources.mo2_hub.skse.require_game_closed'):
                scene_build.publish(target, job, 'A')
            effective = {name: str(target/name) for name in job['hashes']}
            scene.verify_saved(target, job, context, effective)
            # Updating another part of the same package must not invalidate trees.
            unrelated = source/'meshes/clutter/chair.nif'
            unrelated.parent.mkdir(parents=True); unrelated.write_bytes(b'chair')
            (source/'readme.txt').write_text('updated documentation')
            scene.verify_saved(target, job, context, effective)
            oak = pine.with_name('oak.nif'); oak.write_bytes(b'new oak')
            with self.assertRaisesRegex(ValueError, 'Trees.*changed|Trees.*added'):
                scene.verify_saved(target, job, context, effective)
            self.assertEqual({'trees': 'Trees A'}, job['choices'])
            self.assertFalse((target/'meshes/landscape/trees/oak.nif').exists())
            self.assertEqual(b'pine', (target/'meshes/landscape/trees/pine.nif').read_bytes())

    def test_custom_archive_loading_is_not_silently_treated_as_standard_order(self):
        from modlab.resources.mo2_hub.scene_workflow import check_archive_inis
        with TemporaryDirectory() as tmp:
            path = Path(tmp)/'SkyrimCustom.ini'
            path.write_text('[Display]\niSize W=1280\n')
            check_archive_inis([path])
            path.write_text('[Archive]\nsResourceArchiveList=Alternate.bsa\n')
            with self.assertRaisesRegex(ValueError, 'archive'):
                check_archive_inis([path])

    def test_saved_request_is_separate_from_applied_choices_and_checks_concurrent_edit(self):
        from modlab.resources.mo2_hub import scene_workflow as scene
        with TemporaryDirectory() as tmp:
            host = Obj(profilePath=lambda: tmp)
            request = scene.begin_pending(host, {'roads': 'Blended Roads'}, expected=None)
            self.assertEqual({'roads': 'Blended Roads'}, scene.load(host, 'pending')['choices'])
            self.assertIsNone(scene.load(host))
            with self.assertRaisesRegex(ValueError, 'changed'):
                scene.begin_pending(host, {'trees': 'Trees'}, expected=None)
            scene.begin_pending(host, {}, expected=request)
            self.assertEqual({}, scene.load(host, 'pending')['choices'])
            self.assertIsNone(scene.load(host))

    def test_request_checks_profile_and_rejects_unknown_component_without_writing(self):
        from modlab.resources.mo2_hub import scene_workflow as scene
        with TemporaryDirectory() as tmp:
            host = Obj(profilePath=lambda: tmp)
            with self.assertRaisesRegex(ValueError, 'component'):
                scene.begin_pending(host, {'unknown': 'Mod'}, expected=None)
            self.assertFalse(scene.path_for(host, 'pending').exists())
            request = scene.begin_pending(host, {'roads': 'Roads'}, expected=None)
            with self.assertRaisesRegex(ValueError, 'profile'):
                scene.check_request(Obj(profilePath=lambda: str(Path(tmp)/'other')), request)

    def test_changed_provider_or_archive_order_invalidates_prepared_context(self):
        from modlab.resources.mo2_hub.scene_workflow import check_snapshot
        original = {'profile_path': 'A', 'roots': [('Roads', 'mods/Roads')],
                    'archives': [{'path': 'data/a.bsa', 'rank': [1, 3]}]}
        check_snapshot(original, original)
        for changed in (dict(original, profile_path='B'), dict(original, roots=[]),
                        dict(original, archives=[{'path': 'data/a.bsa', 'rank': [1, 4]}])):
            with self.assertRaisesRegex(ValueError, 'changed'):
                check_snapshot(original, changed)

    def test_applied_result_requires_current_output_and_active_source_mod(self):
        from modlab.resources.mo2_hub.scene_workflow import verify_saved
        from modlab.resources.mo2_hub import scene_build
        import test_hub_scene_choices as fixtures
        with TemporaryDirectory() as tmp:
            root = Path(tmp); providers = fixtures.SceneChoiceTests().sources(root/'mods')
            target = root/'mods/Scene output'; target.mkdir()
            job = scene_build.prepare(root, 'A', providers, {'roads': 'Roads'},
                lambda meshes, directory: {name: [] for name in meshes}, lambda name: None)
            context = dict(roots=[('Roads', providers['Roads']['root'])])
            job['context'] = context
            with patch('modlab.resources.mo2_hub.skse.require_game_closed'):
                scene_build.publish(target, job, 'A')
            effective = {name: str(target/name) for name in job['hashes']}
            verify_saved(target, job, context, effective)
            with self.assertRaisesRegex(ValueError, 'source'):
                verify_saved(target, job, dict(roots=[]), effective)
            effective[next(iter(effective))] = str(root/'other-file.nif')
            with self.assertRaisesRegex(ValueError, 'overridden'):
                verify_saved(target, job, context, effective)

    def test_disabled_fallback_texture_provider_is_not_reported_as_working(self):
        from modlab.resources.mo2_hub import scene_workflow as scene, scene_build
        from modlab.resources.mo2_hub.scene_assets import Catalog
        from test_hub_scene_assets import ArchiveFixture
        import test_hub_scene_choices as fixtures
        with TemporaryDirectory() as tmp:
            root = Path(tmp); providers = fixtures.SceneChoiceTests().sources(root/'mods')
            texture = root/'mods/Textures/textures/extra.dds'
            texture.parent.mkdir(parents=True); texture.write_bytes(b'existing texture')
            roots = [('Roads', providers['Roads']['root']), ('Textures', str(root/'mods/Textures'))]
            assets = Catalog(roots, [], root/'cache', ArchiveFixture())
            job = scene_build.prepare(root, 'A', providers, {'roads': 'Roads'},
                lambda meshes, directory: {name: ['textures/extra.dds'] for name in meshes}, assets.resolve)
            job['context'] = dict(roots=roots)
            target = root/'mods/Scene output'; target.mkdir()
            with patch('modlab.resources.mo2_hub.skse.require_game_closed'):
                scene_build.publish(target, job, 'A')
            effective = {name: str(target/name) for name in job['hashes']}
            current = dict(roots=roots[:1])
            disabled = Catalog(current['roots'], [], root/'cache2', ArchiveFixture())
            with patch.object(scene, 'catalog', return_value=disabled):
                with self.assertRaisesRegex(ValueError, 'texture'):
                    scene.verify_saved(target, job, current, effective)
