import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from tests.test_hub_npc import record, sub
from modlab.resources.mo2_hub.body_ownership import outfit_models


class BodyOwnershipTests(unittest.TestCase):
    def plugin(self, path, rows, masters=()):
        path.write_bytes(record(b'TES4', sub(b'HEDR', struct.pack('<fII', 1.7, len(rows), 0x800)) +
            b''.join(sub(b'MAST', m.encode() + b'\0') for m in masters)) + b''.join(rows))
        return path

    def test_npc_skin_in_armor_folder_is_not_a_shared_outfit(self):
        with TemporaryDirectory() as root:
            p = self.plugin(Path(root)/'Follower.esp', [
                record(b'NPC_', sub(b'WNAM', struct.pack('<I', 0x800)), 0x810),
                record(b'ARMO', sub(b'MODL', struct.pack('<I', 0x801)), 0x800),
                record(b'ARMA', sub(b'MOD3', b'armor/private/anything_1.nif\0'), 0x801),
                record(b'ARMO', sub(b'MODL', struct.pack('<I', 0x803)), 0x802),
                record(b'ARMA', sub(b'MOD3', b'armor/iron/armor_1.nif\0'), 0x803)])
            paths = outfit_models([p])
            self.assertEqual({'meshes/armor/iron/armor_0.nif', 'meshes/armor/iron/armor_1.nif'}, paths)

    def test_skin_reference_in_override_resolves_master_identity(self):
        with TemporaryDirectory() as root:
            root = Path(root)
            base = self.plugin(root/'Base.esm', [
                record(b'ARMO', sub(b'MODL', struct.pack('<I', 0x801)), 0x800),
                record(b'ARMA', sub(b'MOD3', b'armor/base/body_1.nif\0'), 0x801)])
            patch = self.plugin(root/'Patch.esp', [record(b'RACE', sub(b'WNAM', struct.pack('<I', 0x800)), 0x01000810)], ['Base.esm'])
            self.assertTrue(outfit_models([base]))
            self.assertFalse(outfit_models([base, patch]))

    def test_missing_skin_armature_is_not_silently_declared_safe(self):
        with TemporaryDirectory() as root:
            p = self.plugin(Path(root)/'Missing.esp', [
                record(b'NPC_', sub(b'WNAM', struct.pack('<I', 0x800)), 0x810),
                record(b'ARMO', sub(b'MODL', struct.pack('<I', 0x801)), 0x800)])
            with self.assertRaisesRegex(ValueError, 'skin'):
                outfit_models([p])

    def test_outfits_use_selected_sex_but_protect_skin_paths_for_both(self):
        with TemporaryDirectory() as root:
            p = self.plugin(Path(root)/'Both.esp', [
                record(b'NPC_', sub(b'WNAM', struct.pack('<I', 0x800)), 0x810),
                record(b'ARMO', sub(b'MODL', struct.pack('<I', 0x801)), 0x800),
                record(b'ARMA', sub(b'MOD2', b'private/shared.nif\0'), 0x801),
                record(b'ARMO', sub(b'MODL', struct.pack('<I', 0x803)), 0x802),
                record(b'ARMA', sub(b'MOD2', b'armor/male.nif\0') +
                    sub(b'MOD3', b'armor/female.nif\0') +
                    sub(b'MOD5', b'private/shared.nif\0'), 0x803)])
            self.assertEqual({'meshes/armor/female.nif'}, outfit_models([p], 'female'))
            self.assertEqual({'meshes/armor/male.nif'}, outfit_models([p], 'male'))

    def test_winning_armor_addon_paths_are_used(self):
        with TemporaryDirectory() as root:
            root = Path(root)
            base = self.plugin(root/'Base.esm', [
                record(b'ARMO', sub(b'MODL', struct.pack('<I', 0x801)), 0x800),
                record(b'ARMA', sub(b'MOD3', b'armor/old.nif\0'), 0x801)])
            patch = self.plugin(root/'Patch.esp', [record(b'ARMA', sub(b'MOD3', b'armor/new.nif\0'), 0x801)], ['Base.esm'])
            self.assertEqual({'meshes/armor/new.nif'}, outfit_models([base, patch]))


if __name__ == '__main__': unittest.main()
