import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as Obj

from modlab.resources.mo2_hub.assessment import Asset, Plugin
from modlab.resources.mo2_hub.archives import check_archive_index


class ArchiveChecks(unittest.TestCase):
    def test_registered_skyrim_archives_keep_distinct_ini_positions(self):
        from modlab.resources.mo2_hub.archives import archive_rank
        plugins = [Plugin('Skyrim.esm', 0)]
        registered = ['Skyrim - Animations.bsa', 'Skyrim - Meshes0.bsa']
        self.assertLess(archive_rank('Skyrim - Animations.bsa', plugins, registered),
                        archive_rank('Skyrim - Meshes0.bsa', plugins, registered))

    def test_game_archive_overlap_uses_plugin_order_and_preserves_loose_winner(self):
        from modlab.resources.mo2_hub.archives import merge_game_archives
        archives = [Asset('Base.bsa', ('data',)), Asset('Quest.bsa', ('Quest',)), Asset('Fix.bsa', ('Fix',))]
        plugins = [Plugin('Quest.esp', 8), Plugin('Fix.esp', 6)]
        indexed = {'meshes/head.nif': ('Loose face',), 'scripts/q.pex': ('Fix',)}
        packed = {'scripts/q.pex': 'Fix.bsa'}
        assets = [Asset('meshes/head.nif', ('Loose face',)), Asset('scripts/q.pex', ('Fix',), 'Fix.bsa')]
        reader = Obj(entries=lambda p: ('meshes/head.nif', 'scripts/q.pex'))
        result, findings = merge_game_archives(assets, indexed, packed, archives, {'Base.bsa','Quest.bsa'},
            plugins, ['Base.bsa'], lambda p:p, reader)
        by_path = {a.path:a for a in result}
        self.assertEqual('Loose face', by_path['meshes/head.nif'].origins[0])
        self.assertEqual({'Quest', 'data'}, set(by_path['meshes/head.nif'].origins[1:]))
        self.assertEqual('Quest.bsa', by_path['scripts/q.pex'].archive)
        self.assertEqual(('Quest', 'Fix', 'data'), by_path['scripts/q.pex'].origins)
        self.assertEqual((), findings)

    def test_unknown_archive_loader_does_not_get_arbitrary_priority(self):
        from modlab.resources.mo2_hub.archives import merge_game_archives
        result, findings = merge_game_archives([], {}, {}, [Asset('Odd.bsa', ('Odd',))], {'Odd.bsa'},
            [], [], lambda p:p, Obj(entries=lambda p: ('meshes/head.nif',)))
        self.assertEqual([], result)
        self.assertTrue(any(f.code == 'archive-order-unknown' for f in findings))

    def check(self, indexed, selected=('Appearance.bsa',), plugins=()):
        archives = [Asset('Appearance.bsa', ('Appearance',))]
        reader = Obj(entries=lambda path: ('meshes/actors/head.nif', 'textures/actors/face.dds'))
        return check_archive_index(archives, selected, indexed, plugins, lambda p: p, reader)

    def test_loose_winner_still_retains_packed_provider_coverage(self):
        covered, findings = self.check({
            'meshes/actors/head.nif': ('Chosen head', 'Appearance'),
            'textures/actors/face.dds': ('Appearance',),
        })
        self.assertTrue(covered)
        self.assertTrue(any(f.code == 'archive-index-checked' for f in findings))

    def test_existing_path_from_wrong_provider_does_not_hide_skipped_archive(self):
        covered, findings = self.check({
            'meshes/actors/head.nif': ('Other mod',),
            'textures/actors/face.dds': ('Appearance',),
        })
        self.assertFalse(covered)
        self.assertIn('meshes/actors/head.nif', '\n'.join(f.detail for f in findings))

    def test_disabled_loader_reports_mod_and_enable_action(self):
        covered, findings = self.check({}, selected=(), plugins=(Plugin('Appearance.esp', -1),))
        self.assertFalse(covered)
        unloaded = next(f for f in findings if f.code == 'archive-not-loaded')
        self.assertIn('Appearance.esp', unloaded.action)
        self.assertIn('Appearance', unloaded.title)

    def test_broken_archive_is_not_marked_checked(self):
        def broken(path):
            raise ValueError('Invalid BSA header')
        covered, findings = check_archive_index([Asset('Broken.bsa', ('Broken mod',))], ['Broken.bsa'], {}, (),
                                                lambda p: p, Obj(entries=broken))
        self.assertFalse(covered)
        self.assertIn('Invalid BSA header', '\n'.join(f.detail for f in findings))

    def test_physical_helper_search_excludes_packed_pseudo_paths(self):
        from modlab.resources.mo2_hub.vfs import find_files
        with TemporaryDirectory() as temp:
            loose = Path(temp) / 'tool.exe'; loose.write_bytes(b'file')
            host = Obj(listDirectories=lambda p: [], findFiles=lambda p, patterns: [str(loose), str(Path(temp)/'packed.exe')])
            self.assertEqual([str(loose)], list(find_files(host, '', ['*.exe'])))
