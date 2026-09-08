import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from tests.test_hub_npc import record, sub
from modlab.resources.mo2_hub.characters import character_records, character_rows, parse_strings, winning_strings


class CharacterTests(unittest.TestCase):
    def plugin(self, root, name, rows, masters=(), localized=False):
        p=Path(root)/name
        p.write_bytes(record(b'TES4', sub(b'HEDR', struct.pack('<fII', 1.7, len(rows), 0x800)) +
            b''.join(sub(b'MAST', n.encode()+b'\0') for n in masters), flags=0x80 if localized else 0) + b''.join(rows))
        return p

    def test_localized_lydia_is_found_without_replacer_assets(self):
        with TemporaryDirectory() as root:
            p=self.plugin(root, 'Skyrim.esm', [record(b'NPC_', sub(b'EDID', b'HousecarlWhiterun\0') +
                sub(b'FULL', struct.pack('<I', 42)) + sub(b'ACBS', struct.pack('<I', 0x21)+bytes(20)), 0xa2c8e)], localized=True)
            chars=character_records(p, {42:'Lydia'})
            rows=character_rows([(p.name, chars)], {})
            row=rows['0A2C8E:skyrim.esm']
            self.assertEqual('Lydia', row['name'])
            self.assertTrue(row['unique']); self.assertEqual('female', row['sex'])
            self.assertEqual({}, row['options'])
            self.assertIn('housecarlwhiterun', row['search'])

    def test_replacements_keep_mod_names_and_only_their_own_characters(self):
        with TemporaryDirectory() as root:
            p=self.plugin(root, 'Skyrim.esm', [record(b'NPC_', sub(b'FULL', b'Lydia\0'), 0xa2c8e),
                record(b'NPC_', sub(b'FULL', b'Wizard\0'), 0x800)])
            choices={'000800:skyrim.esm': {'label':'Wizard', 'options':{'Magic.esp':{'mod':'Magic spells', 'ready':True}}}}
            rows=character_rows([(p.name, character_records(p))], choices)
            self.assertFalse(rows['0A2C8E:skyrim.esm']['options'])
            self.assertEqual('Magic spells', rows['000800:skyrim.esm']['options']['Magic.esp']['mod'])

    def test_winning_override_and_deletion_are_respected(self):
        with TemporaryDirectory() as root:
            base=self.plugin(root,'Base.esm',[record(b'NPC_',sub(b'FULL',b'Old\0'),0x800),record(b'NPC_',b'',0x801)])
            patch=self.plugin(root,'Patch.esp',[record(b'NPC_',sub(b'FULL',b'New\0'),0x800),record(b'NPC_',b'',0x801,0x20)],['Base.esm'])
            rows=character_rows([(p.name,character_records(p)) for p in (base,patch)],{})
            self.assertEqual('New',rows['000800:base.esm']['name'])
            self.assertNotIn('000801:base.esm', rows)

    def test_skin_reference_uses_master_identity(self):
        with TemporaryDirectory() as root:
            p=self.plugin(root,'Face.esp',[record(b'NPC_',sub(b'WNAM',struct.pack('<I',0x01000800)),0xa2c8e)],['Skyrim.esm'])
            self.assertEqual('000800:face.esp',character_records(p)['0A2C8E:skyrim.esm']['skin'])

    def test_name_table_uses_archive_order_and_rejects_only_ambiguous_winners(self):
        def table(name):
            body=name.encode()+b'\0'
            return struct.pack('<IIII',1,len(body),42,0)+body
        self.assertEqual({42:'New'},winning_strings([((0,0),table('Old')),((1,4),table('New'))]))
        self.assertEqual({42:'New'},winning_strings([((1,4),table('New')),((1,4),table('New'))]))
        with self.assertRaisesRegex(ValueError,'Competing'):
            winning_strings([((1,4),table('Old')),((1,4),table('New'))])

    def test_string_table_is_bounded_and_offsets_checked(self):
        data=struct.pack('<IIII',1,6,42,0)+b'Lydia\0'
        self.assertEqual({42:'Lydia'},parse_strings(data))
        with self.assertRaises(ValueError): parse_strings(data[:-1])
        with self.assertRaises(ValueError): parse_strings(struct.pack('<IIII',1,6,42,50)+b'Lydia\0')


if __name__=='__main__': unittest.main()
