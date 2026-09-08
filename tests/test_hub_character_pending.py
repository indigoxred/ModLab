import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from modlab.resources.mo2_hub import npc_workflow as workflow


class CharacterPendingTests(unittest.TestCase):
    def test_newer_choice_is_not_cleared_by_completed_build(self):
        with TemporaryDirectory() as folder:
            organizer=SimpleNamespace(profilePath=lambda:folder)
            original={'selected':{},'body_choices':{'adrianne':'shared'}}
            newer={'selected':{},'body_choices':{'lydia':'shared'}}
            path=workflow.path_for(organizer,'pending')
            path.write_text(json.dumps(newer),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'choices changed'):
                workflow.check_pending(organizer,original)
            self.assertEqual(newer,json.loads(path.read_text()))
            workflow.check_pending(organizer,newer)

    def test_cancelled_pending_is_also_a_change(self):
        with TemporaryDirectory() as folder:
            organizer=SimpleNamespace(profilePath=lambda:folder)
            with self.assertRaisesRegex(ValueError,'choices changed'):
                workflow.check_pending(organizer,{'selected':{}})
            workflow.check_pending(organizer,None)
