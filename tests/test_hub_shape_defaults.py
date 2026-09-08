import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from modlab.resources.mo2_hub import shape_defaults
from tests.test_hub_obody_config import source


class ShapeDefaultTests(unittest.TestCase):
    def test_both_sexes_keep_their_own_default_and_actor_override_still_wins(self):
        from modlab.resources.mo2_hub.obody_config import plan_config
        rows={sex:dict(name=sex,body=dict(sex=sex,race_editor='NordRace',
            race_body_models=[sex+'.nif'],parts=[dict(models=[sex+'.nif'])])) for sex in ('female','male')}
        defaults={sex:dict(preset=sex+' shape',body_models=[sex+'.nif'],built_models=[sex+'.nif']) for sex in rows}
        result=shape_defaults.plan_many(json.dumps(source()),rows,defaults,['female shape','male shape','Lydia shape'],lambda _:None)
        data=json.loads(result['text'])
        self.assertEqual({'NordRace':['female shape']},data['raceFemale'])
        self.assertEqual({'NordRace':['male shape']},data['raceMale'])
        key='0A2C8E:skyrim.esm'
        assigned=plan_config(result['text'],{key:'Lydia shape'},{key:{'name':'Lydia'}},{key:['Lydia shape']})
        self.assertEqual(['Lydia shape'],json.loads(assigned.text)['npcFormID']['skyrim.esm']['0A2C8E'])

    def test_actor_exclusion_is_not_silently_erased(self):
        data=source();data['blacklistedNpcs']=['Lydia']
        actor=dict(name='Lydia',body=dict(sex='female',race_editor='NordRace',race_body_models=['body.nif'],parts=[dict(models=['body.nif'])]))
        with self.assertRaisesRegex(ValueError,'exclusions'):
            shape_defaults.plan(json.dumps(data),{'lydia':actor},'female','Athletic',{'body.nif'}, {'body.nif'},['Athletic'],lambda _:None)

    def test_race_default_retains_private_static_bodies_and_uses_explicit_choices(self):
        with TemporaryDirectory() as folder:
            mesh=Path(folder)/'private.nif';mesh.write_bytes(b'Gamebryo File Format, Version 20.2.0.7\n'+b'0'*140)
            character={'name':'Hulda','body':dict(sex='female',scope='private',race_editor='NordRace',
                race_body_models=['meshes/body.nif'],parts=[dict(models=['meshes/private.nif','meshes/body.nif'])])}
            result=shape_defaults.plan(json.dumps(source()),{'hulda':character},'female','Athletic',
                {'meshes/body.nif'}, {'meshes/body.nif'}, ['Athletic','Lydia custom'],lambda _:str(mesh))
            data=json.loads(result['text'])
            self.assertEqual({'NordRace':['Athletic']},data['raceFemale'])
            self.assertIn('Lydia custom',data['blacklistedPresetsFromRandomDistribution'])
            self.assertEqual(['Hulda'],result['retained_private'])
            self.assertEqual({},data['npcFormID'])
            self.assertIn('meshes/private.nif',result['inputs'])

    def test_private_morphing_body_is_not_assumed_neutral(self):
        with TemporaryDirectory() as folder:
            mesh=Path(folder)/'private.nif';mesh.write_bytes(b'Gamebryo File Format, Version 20.2.0.7\nBODYTRI'+b'0'*140)
            actor={'name':'Hulda','body':dict(sex='female',race_editor='NordRace',
                race_body_models=['meshes/body.nif'],parts=[dict(models=['meshes/private.nif'])])}
            with self.assertRaisesRegex(ValueError,'Hulda.*separate body'):
                shape_defaults.plan(json.dumps(source()),{'hulda':actor},'female','Athletic',
                    {'meshes/body.nif'}, {'meshes/body.nif'},['Athletic'],lambda _:str(mesh))

    def test_other_races_and_sexes_do_not_become_default_targets(self):
        common=dict(race_editor='NordRace',race_body_models=['meshes/body.nif'],parts=[dict(models=['meshes/body.nif'])])
        rows={'woman':dict(name='Lydia',body=dict(common,sex='female')),
              'man':dict(name='Man',body=dict(common,sex='male')),
              'creature':dict(name='Creature',body=dict(common,sex='female',race_editor='CreatureRace',race_body_models=['creature.nif']))}
        result=shape_defaults.plan(json.dumps(source()),rows,'female','Athletic',{'meshes/body.nif'},
            {'meshes/body.nif'},['Athletic'],lambda _:None)
        self.assertEqual(['NordRace'],result['races'])

    def test_existing_custom_distribution_requires_a_deliberate_choice(self):
        data=source();data['npc']={'Lydia':['Previous choice']}
        with self.assertRaisesRegex(ValueError,'existing OBody'):
            shape_defaults.plan(json.dumps(data),{},'female','Athletic',set(),set(),['Athletic'],lambda _:None)
