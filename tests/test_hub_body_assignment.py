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

    def test_traits_template_requires_explicit_handling(self):
        index=self.index(self.base([npc(0x801,template=0x800,traits=True)]))
        with self.assertRaisesRegex(ValueError,'template'):
            body_assignment.plan(index,{'000801:base.esm':'shared'})

    def test_unknown_choice_is_not_treated_as_shared(self):
        index=self.index(self.base())
        with self.assertRaisesRegex(ValueError,'choice'):
            body_assignment.plan(index,{'000800:base.esm':'anything'})

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
