import unittest
from modlab.resources.mo2_hub.assessment import Finding, Asset, SetupSnapshot, assess
from modlab.resources.mo2_hub.guidance import setup_guidance, display_name, finding_action, retain_run_findings


def finding(level, code):
    return Finding(level, code, code, 'Evidence', 'Next action')


class GuidanceTests(unittest.TestCase):
    def test_stale_outputs_are_executable_work_not_completed_preparation(self):
        plan = setup_guidance([finding('Review', 'npc-stale'), finding('Review', 'graphics-stale')], checked=True)
        self.assertFalse(plan.complete)
        self.assertEqual(plan.action, 'finish_setup')
        self.assertEqual(len(plan.groups['work']), 2)

    def test_failed_inspection_cannot_be_hidden_by_informational_overlaps(self):
        plan = setup_guidance([finding('Review', 'asset-overlap'), finding('Unknown', 'record-check-incomplete')], checked=True)
        self.assertFalse(plan.complete)
        self.assertEqual(len(plan.groups['attention']), 1)
        self.assertEqual(len(plan.groups['observations']), 1)

    def test_new_choices_route_to_the_relevant_operation(self):
        plan = setup_guidance([finding('Review', 'body-choices')], checked=True)
        self.assertFalse(plan.complete)
        self.assertEqual(plan.action, 'bodyslide')
        self.assertEqual(finding_action(finding('Blocked', 'animation-choices'))[0], 'pandora')

    def test_no_completion_without_current_preparation_evidence(self):
        self.assertFalse(setup_guidance([], checked=False).complete)
        self.assertTrue(setup_guidance([finding('Info', 'npc-current')], checked=True).complete)
        self.assertFalse(setup_guidance([finding('Review', 'record-errors')], checked=True).complete)

    def test_unresolved_record_warning_does_not_hide_available_preparation(self):
        plan = setup_guidance([finding('Review', 'record-errors'), finding('Review', 'graphics-stale')], checked=True)
        self.assertEqual(plan.action, 'finish_setup')
        self.assertFalse(plan.complete)
        self.assertEqual(len(plan.groups['attention']), 1)

    def test_failed_pending_npc_choice_routes_to_selection_not_a_recheck_loop(self):
        plan = setup_guidance([finding('Blocked', 'npc-pending')], checked=True)
        self.assertEqual(plan.action, 'npc_appearances')
        self.assertFalse(plan.complete)
        self.assertEqual(len(plan.groups['work']), 0)

    def test_reopening_keeps_failed_preparation_and_pending_choices_visible(self):
        for code in ('animation-failed', 'animation-choices', 'body-build-failed', 'body-choices'):
            with self.subTest(code=code):
                failed = finding('Blocked', code)
                current = retain_run_findings([finding('Info', 'npc-current')], [failed])
                self.assertIn(failed, current)
                self.assertFalse(setup_guidance(current, checked=True).complete)

    def test_fresh_inspection_replaces_old_staleness_and_does_not_duplicate_warnings(self):
        current = finding('Info', 'npc-current')
        failed = finding('Blocked', 'animation-failed')
        result = retain_run_findings([current, failed], [finding('Review', 'npc-stale'), failed])
        self.assertEqual(result, (current, failed))

    def test_short_names_keep_version_and_do_not_modify_identity(self):
        original = 'Summermyst 4.2.0 6285 4.2.0 2026-08-27T17-52Z bqv3tSsQQ'
        self.assertEqual(display_name(original), 'Summermyst 4.2.0')
        self.assertEqual(display_name('CBBE 3BA (3BBB)-30174-2-48-1740765899'), 'CBBE 3BA (3BBB)')
        self.assertEqual(display_name('Custom skin 2026'), 'Custom skin 2026')

    def test_spell_textures_do_not_receive_body_advice(self):
        snapshot = SetupSnapshot('Skyrim Special Edition', '1.6.1170.0', 'Test', 'C:/Game', assets=(
            Asset('textures/effects/example.dds', ('Apocalypse', 'Summermyst')),))
        overlap = next(f for f in assess(snapshot).findings if f.code == 'asset-overlap')
        self.assertNotIn('body', overlap.action.casefold())
        self.assertEqual(overlap.level, 'Review')

if __name__ == '__main__':
    unittest.main()
