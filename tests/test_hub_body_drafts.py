import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from modlab.resources.mo2_hub.body_choices import save_default, load_defaults
from modlab.resources.mo2_hub.body_drafts import load_drafts, save_draft, choice_fields, missing_decisions


class BodyDraftTests(unittest.TestCase):
    def choice(self, body='Body', preset='Customized shape'):
        return dict(body=body,preset=preset,decisions={'hands':'Hands'},outfits=True,shape_support=True)

    def test_pending_shape_survives_reopening_without_becoming_applied(self):
        with TemporaryDirectory() as root:
            save_default(root,'female',self.choice(preset='Applied shape'))
            before=load_defaults(root)
            save_draft(root,'female',self.choice(),expected=None)
            self.assertEqual(self.choice(),load_drafts(root)['female'])
            self.assertEqual(before,load_defaults(root))

    def test_both_groups_survive_and_only_matching_pending_choice_is_cleared(self):
        with TemporaryDirectory() as root:
            female=self.choice(); male=self.choice('Male','Male shape')
            save_draft(root,'female',female,expected=None)
            save_draft(root,'male',male,expected=None)
            save_draft(root,'female',None,expected=female)
            self.assertEqual({'male':male},load_drafts(root))

    def test_external_pending_change_is_not_overwritten(self):
        with TemporaryDirectory() as root:
            old=self.choice(); other=self.choice(preset='Changed elsewhere')
            save_draft(root,'female',old,expected=None)
            save_draft(root,'female',other,expected=old)
            with self.assertRaisesRegex(ValueError,'changed'):
                save_draft(root,'female',None,expected=old)
            self.assertEqual(other,load_drafts(root)['female'])

    def test_saved_draft_cannot_be_reused_by_a_different_profile(self):
        with TemporaryDirectory() as root:
            a=Path(root)/'A'; b=Path(root)/'B'; a.mkdir(); b.mkdir()
            save_draft(a,'female',self.choice(),expected=None)
            (b/'modlab-body-drafts.json').write_bytes((a/'modlab-body-drafts.json').read_bytes())
            with self.assertRaisesRegex(ValueError,'profile'): load_drafts(b)

    def test_pending_fields_do_not_inherit_old_build_success(self):
        choice=self.choice(); choice.update(build_record='old/operation.json',selected=['Old'])
        self.assertEqual(self.choice(),choice_fields(choice))

    def test_removed_body_part_choice_is_retained_for_review_not_silently_skipped(self):
        self.assertEqual({'lost':'Removed outfit'},missing_decisions({'lost':'Removed outfit','kept':''},set()))
        self.assertEqual({},missing_decisions({'kept':'Hands'},{'kept'}))
