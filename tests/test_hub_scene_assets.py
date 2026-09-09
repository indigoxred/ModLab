from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

from modlab.resources.mo2_hub.scene_choices import plan_choices, check_sources


class ArchiveFixture:
    """Filesystem fixture at the existing ArchiveReader entries/extract boundary."""
    def entries(self, path):
        with zipfile.ZipFile(path) as archive:
            return archive.namelist()

    def extract_asset(self, path, name, destination):
        destination = Path(destination); destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path) as archive:
            destination.write_bytes(archive.read(name))


class SceneAssetTests(unittest.TestCase):
    def archive(self, path, content):
        with zipfile.ZipFile(path, 'w') as archive:
            for name, value in content.items(): archive.writestr(name, value)

    def test_packed_provider_selects_component_and_loose_patch_wins_within_provider(self):
        from modlab.resources.mo2_hub.scene_assets import Catalog
        with TemporaryDirectory() as tmp:
            root = Path(tmp); mod = root/'Roads'; mod.mkdir()
            archive = mod/'Roads.bsa'
            self.archive(archive, {'meshes/landscape/roads/a.nif': b'packed road',
                                  'meshes/clutter/b.nif': b'unrelated object'})
            loose = mod/'meshes/landscape/roads/a.nif'; loose.parent.mkdir(parents=True); loose.write_bytes(b'patch')
            catalog = Catalog([('Roads', str(mod))], [{'path': str(archive), 'provider': 'Roads', 'rank': [1, 2]}], root/'cache', ArchiveFixture())
            plan = plan_choices(catalog.providers, {'roads': 'Roads'})
            self.assertEqual({'meshes/landscape/roads/a.nif'}, set(plan['files']))
            self.assertEqual(b'patch', Path(plan['files']['meshes/landscape/roads/a.nif']['source']).read_bytes())

    def test_packed_dependency_uses_archive_loading_order_and_tracks_original_archive(self):
        from modlab.resources.mo2_hub.scene_assets import Catalog
        with TemporaryDirectory() as tmp:
            root = Path(tmp); low = root/'Low'; high = root/'High'; low.mkdir(); high.mkdir()
            path = 'textures/shared/a.dds'
            self.archive(low/'Low.bsa', {path: b'low'})
            self.archive(high/'High.bsa', {path: b'high'})
            # Loose mod priority deliberately disagrees with plugin archive order.
            catalog = Catalog([('High', str(high)), ('Low', str(low))], [
                {'path': str(low/'Low.bsa'), 'provider': 'Low', 'rank': [1, 1]},
                {'path': str(high/'High.bsa'), 'provider': 'High', 'rank': [1, 2]}], root/'cache', ArchiveFixture())
            source = catalog.resolve(path)
            self.assertEqual(b'high', Path(source['source']).read_bytes())
            plan = dict(files={}, dependencies={path: source})
            check_sources(plan)
            self.archive(high/'High.bsa', {path: b'changed'})
            with self.assertRaisesRegex(ValueError, 'changed'):
                check_sources(plan)

    def test_equal_rank_archive_collision_is_not_resolved_alphabetically(self):
        from modlab.resources.mo2_hub.scene_assets import Catalog
        with TemporaryDirectory() as tmp:
            root = Path(tmp); mod = root/'Roads'; mod.mkdir()
            for name in ('Roads.bsa', 'Roads - Textures.bsa'):
                self.archive(mod/name, {'textures/shared/a.dds': b'collision'})
            catalog = Catalog([('Roads', str(mod))], [
                {'path': str(mod/name), 'provider': 'Roads', 'rank': [1, 1]}
                for name in ('Roads.bsa', 'Roads - Textures.bsa')], root/'cache', ArchiveFixture())
            with self.assertRaisesRegex(ValueError, 'archive'):
                catalog.resolve('textures/shared/a.dds')
