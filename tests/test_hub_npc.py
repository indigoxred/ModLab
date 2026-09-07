import struct
import unittest
import zlib
from unittest.mock import patch
from types import SimpleNamespace as Obj
from pathlib import Path
from tempfile import TemporaryDirectory


def sub(kind, data):
    return kind + struct.pack('<H', len(data)) + data


def record(kind, data, form=0, flags=0):
    return struct.pack('<4sIIIIHH', kind, len(data), flags, form, 0, 44, 0) + data


class NpcChecks(unittest.TestCase):
    def test_selected_face_texture_is_retained_and_missing_dependency_stops_build(self):
        from modlab.resources.mo2_hub.npc_assets import retain_textures
        with TemporaryDirectory() as temp:
            root = Path(temp); source = root/'source'; output = root/'output'
            texture = source/'textures/custom/hair.dds'; texture.parent.mkdir(parents=True); texture.write_bytes(b'chosen texture')
            provider = {'ForcedAssetDirectory': str(source), 'Plugin': 'Faces.esp'}
            organizer = Obj(resolvePath=lambda name: '')
            with patch('modlab.resources.mo2_hub.archives.archive_reader', return_value=Obj(entries=lambda p:())), \
                 patch('modlab.resources.mo2_hub.archives.active_archive_paths', return_value={}):
                evidence = retain_textures(organizer, provider, ['textures/custom/hair.dds'], output)
                self.assertEqual(b'chosen texture', (output/'textures/custom/hair.dds').read_bytes())
                self.assertEqual('textures/custom/hair.dds', evidence[0]['path'])
                organizer.resolvePath = lambda name: str(texture)
                self.assertTrue(retain_textures(organizer, provider, ['textures/shared.dds'], output)[0]['sha256'])
                organizer.resolvePath = lambda name: ''
                with self.assertRaisesRegex(ValueError, 'missing texture'):
                    retain_textures(organizer, provider, ['textures/custom/missing.dds'], output)

    def test_archive_selection_uses_ini_and_enabled_plugins_not_optional_cache(self):
        from modlab.resources.mo2_hub.archives import selected_archives
        from modlab.resources.mo2_hub.assessment import Plugin
        names = ['Skyrim - Textures0.bsa', 'Faces.bsa', 'Faces - Textures.bsa', 'Inactive.bsa', 'Unloaded.bsa']
        plugins = [Plugin('Faces.esp', 3, (), 'Faces'), Plugin('Inactive.esp', -1, (), 'Inactive')]
        self.assertEqual(names[:3], selected_archives(names, [names[0]], plugins))

    def test_texture_paths_handle_real_facegen_prefixes_and_reject_escape(self):
        from modlab.resources.mo2_hub.npc_assets import texture_path
        for value in ('Data\\Textures\\Actors\\face.dds', 'Textures/Actors/face.dds', 'Actors/face.dds'):
            self.assertEqual('textures/actors/face.dds', texture_path(value))
        for value in ('C:/outside.dds', '../outside.dds', 'textures//face.dds', 'textures/face.exe'):
            with self.assertRaises(ValueError): texture_path(value)

    def test_appearance_order_retains_sources_then_npc_then_downstream_helpers(self):
        from modlab.resources.mo2_hub.npc import constrain_order
        original = ('Skyrim.esm', 'NPC.esp', 'Synthesis.esp', 'Face.esp', 'PG.esp')
        self.assertEqual(('Skyrim.esm', 'Face.esp', 'NPC.esp', 'Synthesis.esp', 'PG.esp'),
            constrain_order(original, 'NPC.esp', {'Synthesis.esp', 'PG.esp'}))
        self.assertEqual(original, constrain_order(original, 'Absent.esp', set()))

    def fixture(self, root, *, compressed=False):
        header = record(b'TES4', sub(b'HEDR', struct.pack('<fII', 1.7, 1, 0x800)) + sub(b'MAST', b'Skyrim.esm\0'))
        data = sub(b'EDID', b'TestNpc\0') + sub(b'FULL', b'Test Citizen\0')
        if compressed:
            data = struct.pack('<I', len(data)) + zlib.compress(data)
        npc = record(b'NPC_', data, 0x1234, 0x40000 if compressed else 0)
        group = struct.pack('<4sI4sIII', b'GRUP', 24 + len(npc), b'NPC_', 0, 0, 0) + npc
        path = root / 'Appearance.esp'; path.write_bytes(header + group)
        return path

    def test_npc_identity_uses_master_not_provider_load_index(self):
        from modlab.resources.mo2_hub.npc import npc_records
        with TemporaryDirectory() as temp:
            self.assertEqual({'001234:skyrim.esm': 'Test Citizen (TestNpc)'}, npc_records(self.fixture(Path(temp))))

    def test_compressed_npc_and_truncated_group(self):
        from modlab.resources.mo2_hub.npc import npc_records
        with TemporaryDirectory() as temp:
            path = self.fixture(Path(temp), compressed=True)
            self.assertIn('001234:skyrim.esm', npc_records(path))
            path.write_bytes(path.read_bytes()[:-3])
            with self.assertRaises(ValueError): npc_records(path)

    def test_facegen_requires_both_paired_assets_from_selected_provider(self):
        from modlab.resources.mo2_hub.npc import facegen_paths, validate_selections
        paths = facegen_paths('001234:Skyrim.esm')
        self.assertTrue(paths[0].endswith('Skyrim.esm/00001234.nif'))
        catalog = {'001234:Skyrim.esm': {'label': 'Test', 'options': {'A.esp': {'ready': False, 'problem': 'Face tint missing'}}}}
        with self.assertRaisesRegex(ValueError, 'Face tint missing'):
            validate_selections(catalog, {'001234:Skyrim.esm': 'A.esp'})

    def test_different_bytes_at_shared_asset_path_are_not_silently_overwritten(self):
        from modlab.resources.mo2_hub.npc import merge_assets
        with TemporaryDirectory() as temp:
            root = Path(temp); a = root/'a'; b=root/'b'; output=root/'out'
            a.mkdir(); b.mkdir(); output.mkdir()
            (a/'hair.dds').write_bytes(b'a'); (b/'hair.dds').write_bytes(b'b')
            merge_assets(a, output)
            with self.assertRaisesRegex(ValueError, 'different'):
                merge_assets(b, output)
            self.assertEqual(b'a', (output/'hair.dds').read_bytes())
