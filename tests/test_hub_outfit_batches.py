import unittest
from modlab.resources.mo2_hub import body_choices as choices
from modlab.resources.mo2_hub.bodyslide import Catalog


class OutfitBatchTests(unittest.TestCase):
    def fixture(self):
        groups=[choices.ChoiceGroup('dress','Dress','outfit',('Dress','Dress Physics'),None),
            choices.ChoiceGroup('armor','Armor','outfit',('Armor','Armor Physics'),None),
            choices.ChoiceGroup('hands','Hands','body part',('Hands','Hands Alt'),None),
            choices.ChoiceGroup('cape','Cape','outfit',('Cape','Cape Other'),None)]
        catalog=Catalog({}, {}, {'Regular':{'Dress','Armor','Hands'},
            'Physics':{'Dress Physics','Armor Physics'},'All':{'Dress','Dress Physics','Armor','Armor Physics'},
            'Uncertain':{'Dress','Dress Physics','Armor'}})
        return catalog,groups

    def test_group_batch_changes_only_unambiguous_matching_outfits(self):
        catalog,groups=self.fixture()
        self.assertEqual({'dress':'Dress','armor':'Armor'},choices.outfit_batch(catalog,groups,'Regular'))
        self.assertEqual({'armor':'Armor'},choices.outfit_batch(catalog,groups,'Uncertain'))
        self.assertEqual({},choices.outfit_batch(catalog,groups,'All'))
        self.assertEqual({},choices.outfit_batch(catalog,groups,'Missing'))

    def test_batches_are_declared_groups_that_reduce_multiple_choices(self):
        catalog,groups=self.fixture()
        self.assertEqual([('Physics',2),('Regular',2)],choices.outfit_batches(catalog,groups))
