import copy
import json
import unittest

from modlab.resources.mo2_hub import obody_config as config


LYDIA = '0A2C8E:skyrim.esm'


def source():
    return dict(npcFormID={}, npc={}, factionFemale={}, factionMale={},
        npcPluginFemale={}, npcPluginMale={}, raceFemale={}, raceMale={},
        blacklistedNpcs=[], blacklistedNpcsFormID={},
        blacklistedNpcsPluginFemale=[], blacklistedNpcsPluginMale=[],
        blacklistedRacesFemale=['ElderRace'], blacklistedRacesMale=['ElderRace'],
        blacklistedOutfitsFromORefitFormID={}, blacklistedOutfitsFromORefit=['OBody Nude 32'],
        blacklistedOutfitsFromORefitPlugin=[], outfitsForceRefitFormID={}, outfitsForceRefit=[],
        blacklistedPresetsFromRandomDistribution=['Zeroed Sliders'],
        blacklistedPresetsShowInOBodyMenu=True)


class OBodyConfigTests(unittest.TestCase):
    def plan(self, data, choices=None, **kwargs):
        return config.plan_config(json.dumps(data), choices if choices is not None else {LYDIA:'Athletic'},
            {LYDIA:{'name':'Lydia'}}, {LYDIA:{'Athletic', 'Slim'}}, **kwargs)

    def test_only_selected_actor_changes_and_existing_rule_spelling_is_retained(self):
        data = source()
        data['npcFormID'] = {'Skyrim.esm':{'000A2C8E':['Slim'], '00013BA3':['Other']}}
        data['npc'] = {'Lydia':['Named fallback'], 'Hulda':['Keep']}
        data['raceFemale'] = {'NordRace':['Shared shape']}
        data['futureSetting'] = {'leave':['intact']}
        original = copy.deepcopy(data)
        plan = self.plan(data)
        actual = json.loads(plan.text)
        expected = copy.deepcopy(original)
        expected['npcFormID']['Skyrim.esm']['000A2C8E'] = ['Athletic']
        self.assertEqual(expected, actual)
        self.assertEqual(original, data)
        self.assertEqual({LYDIA:['Slim']}, plan.replaced)

    def test_owner_identity_does_not_depend_on_current_load_order(self):
        key = '000812:new follower.esp'
        data = source()
        data['npcFormID'] = {'New Follower.esp':{'FE123812':['Old']}}
        plan = config.plan_config(json.dumps(data), {key:'Slim'}, {key:{'name':'Follower'}},
            {key:{'Slim'}}, light_plugins={'New Follower.esp'})
        self.assertEqual({'New Follower.esp':{'FE123812':['Slim']}}, json.loads(plan.text)['npcFormID'])

    def test_new_rule_uses_local_base_identity_and_empty_choices_restore_source(self):
        data = source()
        plan = self.plan(data)
        self.assertEqual({'skyrim.esm':{'0A2C8E':['Athletic']}}, json.loads(plan.text)['npcFormID'])
        self.assertEqual(data, json.loads(self.plan(data, {}).text))

    def test_duplicate_json_and_semantic_aliases_are_not_silently_overwritten(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            config.plan_config('{"npc":{},"npc":{}}', {}, {}, {})
        for rules in ({'Skyrim.esm':{'A2C8E':['A'], '000A2C8E':['B']}},
                      {'Skyrim.esm':{}, 'skyrim.esm':{}}):
            with self.subTest(rules=rules):
                data = source(); data['npcFormID'] = rules
                with self.assertRaisesRegex(ValueError, 'ambiguous'):
                    self.plan(data)

    def test_specific_blacklist_requires_a_decision_but_general_blacklists_are_preserved(self):
        for field, value in [('blacklistedNpcs', ['Lydia']),
                             ('blacklistedNpcsFormID', {'Skyrim.esm':['000A2C8E']})]:
            data = source(); data[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'Lydia.*excluded'):
                self.plan(data)
        data = source(); data['blacklistedNpcsPluginFemale'] = ['Skyrim.esm']
        plan = self.plan(data)
        self.assertEqual(['Skyrim.esm'], json.loads(plan.text)['blacklistedNpcsPluginFemale'])
        self.assertEqual(['Athletic'], json.loads(plan.text)['npcFormID']['skyrim.esm']['0A2C8E'])

    def test_unavailable_actor_or_exact_preset_is_rejected_before_preparation(self):
        for choices in ({LYDIA:'athletic'}, {'123456:missing.esp':'Athletic'}):
            with self.subTest(choices=choices), self.assertRaisesRegex(ValueError, 'available'):
                self.plan(source(), choices)
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            self.plan(source(), {LYDIA:'Athletic', LYDIA.lower():'Slim'})

    def test_source_change_requires_reconciliation_and_missing_helper_does_not_create_defaults(self):
        data = source(); old = self.plan(data)
        self.plan(data, expected_source=old.source_sha256)
        data['raceFemale']['NordRace'] = ['Manual choice']
        with self.assertRaisesRegex(ValueError, 'changed outside'):
            self.plan(data, expected_source=old.source_sha256)
        with self.assertRaisesRegex(ValueError, 'Set up'):
            config.plan_config(None, {LYDIA:'Athletic'}, {}, {})
        data = source(); del data['npc']
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            self.plan(data)

    def test_esl_blacklist_is_not_bypassed_when_caller_omits_light_metadata(self):
        key = '000812:follower.esl'
        data = source(); data['blacklistedNpcsFormID'] = {'Follower.esl':['FE123812']}
        with self.assertRaisesRegex(ValueError, 'excluded'):
            config.plan_config(json.dumps(data), {key:'Slim'}, {key:{'name':'Follower'}}, {key:{'Slim'}})
        key = '000812:follower.esp'
        data['blacklistedNpcsFormID'] = {'Follower.esp':['FE123812']}
        with self.assertRaisesRegex(ValueError, 'light.plugin'):
            config.plan_config(json.dumps(data), {key:'Slim'}, {key:{'name':'Follower'}}, {key:{'Slim'}})

    def test_directory_or_preset_aliases_cannot_override_inspected_character(self):
        data = source(); data['blacklistedNpcs'] = ['Lydia']
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            config.plan_config(json.dumps(data), {LYDIA:'Slim'},
                {LYDIA:{'name':'Lydia'}, LYDIA.lower():{'name':'Other'}}, {LYDIA:{'Slim'}})
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            config.plan_config(json.dumps(source()), {LYDIA:'Slim'}, {LYDIA:{'name':'Lydia'}},
                {LYDIA:{'Athletic'}, LYDIA.lower():{'Slim'}})


if __name__ == '__main__': unittest.main()
