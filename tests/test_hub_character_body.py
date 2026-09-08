import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from tests.test_hub_npc import record, sub
from modlab.resources.mo2_hub import body_ownership


def ref(tag, value):
    return sub(tag, struct.pack('<I', value))


def npc(form, skin=0, template=0, traits=False, female=True):
    config = struct.pack('<I', int(female)) + bytes(14) + struct.pack('<H', int(traits)) + bytes(4)
    return record(b'NPC_', sub(b'ACBS', config) + ref(b'RNAM', 0x900) +
                  ref(b'WNAM', skin) + ref(b'TPLT', template), form)


def addon(form, model, texture=0, race=0x900):
    return record(b'ARMA', ref(b'RNAM', race) + sub(b'BOD2', struct.pack('<II', 4, 0)) +
                  sub(b'DNAM', bytes([0, 0, 2, 2]) + bytes(8)) +
                  sub(b'MOD3', model.encode() + b'\0') + ref(b'NAM1', texture), form)


class CharacterBodyTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def plugin(self, name, rows, masters=()):
        path = self.root / name
        path.write_bytes(record(b'TES4', sub(b'HEDR', struct.pack('<fII', 1.7, len(rows), 0x800)) +
            b''.join(sub(b'MAST', m.encode() + b'\0') for m in masters)) + b''.join(rows))
        return path

    def index(self, *paths):
        self.assertTrue(hasattr(body_ownership, 'BodyIndex'), 'Character body ownership must be resolved before offering a shape')
        index = body_ownership.BodyIndex()
        for path in paths: index.add_plugin(path)
        return index

    def base(self, extra=()):
        return self.plugin('Base.esm', [
            record(b'RACE', sub(b'EDID', b'NordRace\0') + ref(b'WNAM', 0x901), 0x900),
            record(b'ARMO', ref(b'MODL', 0x902), 0x901),
            addon(0x902, 'actors/character/female/body_1.nif', 0x903),
            record(b'TXST', sub(b'TX00', b'actors/character/female/body.dds\0'), 0x903),
            npc(0x800), *extra])

    def test_race_body_and_skin_paths_are_resolved_for_character_without_replacer(self):
        body = self.index(self.base()).character('000800:base.esm')
        self.assertEqual('shared', body['scope'])
        self.assertEqual('female', body['sex'])
        self.assertEqual({'meshes/actors/character/female/body_0.nif', 'meshes/actors/character/female/body_1.nif'}, set(body['body_models']))
        self.assertEqual(['textures/actors/character/female/body.dds'], body['textures'])
        self.assertEqual('Base.esm', body['skin_plugin'])

    def test_race_templates_cover_races_without_an_existing_actor(self):
        path=self.base([record(b'RACE',sub(b'EDID',b'NordRaceVampire\0')+ref(b'WNAM',0x901)+ref(b'RNAM',0x900),0x910)])
        index=self.index(path)
        result=index.race_templates()
        self.assertTrue(any(row['body']['race_editor']=='NordRaceVampire' and row['body']['sex']=='female' for row in result.values()))


    def test_private_body_uses_winning_addon_override_and_its_master_references(self):
        base = self.base()
        face = self.plugin('Face.esp', [npc(0x800, 0x01000910),
            record(b'ARMO', ref(b'MODL', 0x01000911), 0x01000910),
            addon(0x01000911, 'private/old_1.nif')], ['Base.esm'])
        patch = self.plugin('Patch.esp', [addon(0x01000911, 'private/new_1.nif', 0x903)], ['Base.esm', 'Face.esp'])
        body = self.index(base, face, patch).character('000800:base.esm')
        self.assertEqual('private', body['scope'])
        self.assertEqual('NordRace', body['race_editor'])
        self.assertEqual(['meshes/actors/character/female/body_0.nif', 'meshes/actors/character/female/body_1.nif'], body['race_body_models'])
        self.assertEqual(['meshes/private/new_0.nif', 'meshes/private/new_1.nif'], body['body_models'])
        self.assertEqual('Patch.esp', body['parts'][0]['plugin'])

    def test_template_only_supplies_traits_when_the_actor_requests_it(self):
        base = self.base([npc(0x801, template=0x800, traits=True, female=False),
                          npc(0x802, template=0xdead, traits=False)])
        index = self.index(base)
        self.assertEqual('female', index.character('000801:base.esm')['sex'])
        self.assertEqual('000800:base.esm', index.character('000801:base.esm')['traits_actor'])
        self.assertEqual('shared', index.character('000802:base.esm')['scope'])

    def test_missing_and_cyclic_trait_templates_do_not_fall_back_to_shared_body(self):
        index = self.index(self.base([npc(0x801, template=0xdead, traits=True),
            npc(0x802, template=0x803, traits=True), npc(0x803, template=0x802, traits=True)]))
        for key in ('000801:base.esm', '000802:base.esm'):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'template'):
                index.character(key)

    def test_private_skin_assignment_can_still_use_shared_body_meshes(self):
        base = self.base([npc(0x801, 0x910), record(b'ARMO', ref(b'MODL', 0x902), 0x910)])
        body = self.index(base).character('000801:base.esm')
        self.assertEqual('shared', body['scope'])
        self.assertEqual('000910:base.esm', body['skin'])

    def test_wrong_race_addon_and_disabled_weight_variant_are_not_offered(self):
        base = self.base()
        patch = self.plugin('Patch.esp', [record(b'ARMO', ref(b'MODL', 0x902) + ref(b'MODL', 0x01000904), 0x901),
            record(b'ARMA', ref(b'RNAM', 0x900) + sub(b'BOD2', struct.pack('<II', 4, 0)) +
                   sub(b'DNAM', bytes(12)) + sub(b'MOD3', b'body/fixed_1.nif\0'), 0x902),
            addon(0x01000904, 'other/body_1.nif', race=0xdead)], ['Base.esm'])
        body = self.index(base, patch).character('000800:base.esm')
        self.assertEqual(['meshes/body/fixed_1.nif'], body['body_models'])

    def test_deleted_skin_addon_is_not_considered_a_working_body(self):
        base = self.base()
        patch = self.plugin('Patch.esp', [record(b'ARMA', b'', 0x902, 0x20)], ['Base.esm'])
        with self.assertRaisesRegex(ValueError, 'armature'):
            self.index(base, patch).character('000800:base.esm')

    def test_shared_first_person_arms_do_not_make_a_private_npc_body_shared(self):
        base = self.base([npc(0x801, 0x910), record(b'ARMO', ref(b'MODL', 0x911), 0x910),
            record(b'ARMA', ref(b'RNAM', 0x900) + sub(b'BOD2', struct.pack('<II', 4, 0)) +
                sub(b'DNAM', bytes([0, 0, 2, 2]) + bytes(8)) +
                sub(b'MOD3', b'private/body_1.nif\0') +
                sub(b'MOD5', b'actors/character/female/body_1.nif\0'), 0x911)])
        body = self.index(base).character('000801:base.esm')
        self.assertEqual('private', body['scope'])
        self.assertEqual(['meshes/private/body_0.nif', 'meshes/private/body_1.nif'], body['body_models'])
        self.assertEqual(['meshes/actors/character/female/body_0.nif', 'meshes/actors/character/female/body_1.nif'], body['first_person_models'])

    def test_competing_torso_priorities_require_resolution_not_a_union_of_bodies(self):
        base = self.base([npc(0x801, 0x910), record(b'ARMO', ref(b'MODL', 0x902) + ref(b'MODL', 0x911), 0x910),
            record(b'ARMA', ref(b'RNAM', 0x900) + sub(b'BOD2', struct.pack('<II', 4, 0)) +
                sub(b'DNAM', bytes([0, 5, 2, 2]) + bytes(8)) + sub(b'MOD3', b'private/body_1.nif\0'), 0x911)])
        with self.assertRaisesRegex(ValueError, 'overlapping'):
            self.index(base).character('000801:base.esm')


if __name__ == '__main__': unittest.main()
