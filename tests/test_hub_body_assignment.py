import unittest
from tests import test_hub_character_body as fixtures
from tests.test_hub_character_body import npc, addon, ref
from tests.test_hub_npc import record
from modlab.resources.mo2_hub import body_assignment

class BodyAssignmentTests(unittest.TestCase):
    setUp=fixtures.CharacterBodyTests.setUp
    plugin=fixtures.CharacterBodyTests.plugin
    index=fixtures.CharacterBodyTests.index
    base=fixtures.CharacterBodyTests.base
    def test_shared_assignment_changes_only_requested_actor_resolution(self):
        base=self.base([npc(0x801,skin=0x910),record(b'ARMO',ref(b'MODL',0x911),0x910),
            addon(0x911,'custom/body_1.nif',0x903)])
        index=self.index(base)
        before=index.character('000801:base.esm')
        planned=body_assignment.plan(index,{'000801:base.esm':'shared'})
        self.assertEqual('000901:base.esm',planned['000801:base.esm']['skin'])
        self.assertEqual('shared',planned['000801:base.esm']['body']['scope'])
        self.assertEqual(before,index.character('000801:base.esm'))
        self.assertEqual('private',before['scope'])

    def test_shared_mesh_plan_retains_private_skin_and_texture_references(self):
        base=self.base([npc(0x801,skin=0x910),record(b'ARMO',ref(b'MODL',0x911),0x910),
            addon(0x911,'custom/body_1.nif',0x903)])
        index=self.index(base)
        planned=body_assignment.plan_preserving_skin(index,{'000801:base.esm':'shared'})['000801:base.esm']
        self.assertEqual('000910:base.esm',planned['skin'])
        self.assertEqual({'000911:base.esm':'000902:base.esm'},planned['model_sources'])
        self.assertEqual(index.character('000801:base.esm')['textures'],planned['retained_textures'])
        self.assertEqual('female',planned['sex'])

    def test_traits_template_requires_explicit_handling(self):
        index=self.index(self.base([npc(0x801,template=0x800,traits=True)]))
        with self.assertRaisesRegex(ValueError,'template'):
            body_assignment.plan(index,{'000801:base.esm':'shared'})

    def test_unknown_choice_is_not_treated_as_shared(self):
        index=self.index(self.base())
        with self.assertRaisesRegex(ValueError,'choice'):
            body_assignment.plan(index,{'000800:base.esm':'anything'})

    def test_export_verification_checks_hand_and_foot_models_too(self):
        from copy import deepcopy
        from unittest.mock import patch
        index=self.index(self.base([npc(0x801,skin=0x910),
            record(b'ARMO',ref(b'MODL',0x911),0x910),addon(0x911,'custom/body_1.nif',0x903)]))
        plans=body_assignment.plan_preserving_skin(index,{'000801:base.esm':'shared'})
        plan=plans['000801:base.esm']
        # A second non-torso part deliberately retains an old mesh in the export.
        original=deepcopy(plan['original']['parts'][0]);original['slots']=[33]
        shared=deepcopy(plan['shared']['parts'][0]);shared['slots']=[33]
        shared['models']=['meshes/shared/hands_1.nif']
        shared['world_models']=shared['models'];shared['first_person_models']=[]
        plan['original']['parts'].append(original);plan['shared']['parts'].append(shared)
        actual=deepcopy(plan['original']);actual['skin']='000999:patch.esp'
        actual['body_models']=plan['shared']['body_models']
        actual['parts'][0].update({k:plan['shared']['parts'][0][k]
            for k in ('models','world_models','first_person_models')})
        with patch.object(type(index),'add_plugin'),patch.object(type(index),'character',return_value=actual):
            with self.assertRaisesRegex(ValueError,'meshes'):
                body_assignment.verify_preserved(self.root/'Patch.esp',index,plans)
            actual['parts'][1].update({k:shared[k] for k in ('models','world_models','first_person_models')})
            body_assignment.verify_preserved(self.root/'Patch.esp',index,plans)

    def test_generated_record_must_have_requested_assignment(self):
        original=self.base()
        with self.assertRaisesRegex(ValueError,'assignment'):
            body_assignment.verify(original,{'000800:base.esm':{'skin':'000901:base.esm'}})
        output=self.plugin('Patch.esp',[npc(0x800,skin=0x901)],masters=('Base.esm',))
        body_assignment.verify(output,{'000800:base.esm':{'skin':'000901:base.esm'}})

class HelperAdapterTests(unittest.TestCase):
    def test_patch_is_bounded_and_keeps_asset_pipeline(self):
        from modlab.resources.mo2_hub.npc_body_helper import adapt_source
        program='if (mergeJSONlist.Any())\ncopyAssets(NPCoverride, currentModContext.ModKey, settings, currentDataDir, PPS, isTemplated, state);\npublic static Npc addNPCtoPatch('
        settings='public class PatcherSettings\n    {'
        result,config=adapt_source(program,settings)
        self.assertIn('ModLabBodyAssignments',config)
        self.assertIn('copyAssets(NPCoverride',result)
        self.assertIn('WornArmor.SetTo',result)
        with self.assertRaisesRegex(ValueError,'source'):
            adapt_source('unrecognized',settings)
