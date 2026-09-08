import unittest

from modlab.resources.mo2_hub.assessment import Plugin
from modlab.resources.mo2_hub.loot import validate_order, report_findings


class LootTests(unittest.TestCase):
    def test_old_engine_fixes_advice_requires_matching_installed_files_to_explain(self):
        from modlab.resources.mo2_hub.loot import reconcile_requirements
        from modlab.resources.mo2_hub.assessment import Finding
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        warning = Finding('Review', 'loot-message', 'LOOT advice: this setup',
            'It appears you have installed **SSE Engine Fixes - Part 1**, but some of its requirements seem to be missing. '
            'Please ensure you have correctly installed **[SSE Engine Fixes - Part 2](https://www.nexusmods.com/skyrimspecialedition/mods/17230/)**.', 'Review')
        with TemporaryDirectory() as tmp:
            root = Path(tmp); dll = root / 'EngineFixes.dll'; dll.write_bytes(b'test')
            loader = root / 'd3dx9_42.dll'; loader.write_bytes(b'test')
            known = {dll: '5d1384acfb523abd1333f5af71af0b7d131b6ebb1a0ee6b3edff86fb4c93adf3',
                     loader: 'cf366987da6237559eb6e113ea717ec21762c9eec3a87d9dc4fa9ddfe7789c26'}
            resolve = lambda name: str(dll)
            with patch('modlab.resources.mo2_hub.outputs.digest', side_effect=lambda p: known[p]):
                result = reconcile_requirements((warning,), root, resolve, '1.6.1170.0')
                self.assertEqual('Info', result[0].level)
                self.assertIn(warning.detail, result[0].detail)
                self.assertIn('startup', result[0].action)
                self.assertEqual((warning,), reconcile_requirements((warning,), root, resolve, '1.7.104.0'))
                known[dll] = 'unrecognized version'
                self.assertEqual((warning,), reconcile_requirements((warning,), root, resolve, '1.6.1170'))
                known[dll] = '5d1384acfb523abd1333f5af71af0b7d131b6ebb1a0ee6b3edff86fb4c93adf3'
                loader.unlink()
                self.assertEqual((warning,), reconcile_requirements((warning,), root, resolve, '1.6.1170'))

    def setUp(self):
        self.plugins = (Plugin('Skyrim.esm', 0),
                        Plugin('Follower.esp', 1, ('Skyrim.esm',)),
                        Plugin('Unused.esp', -1))

    def test_complete_proposal_preserves_original_spelling(self):
        self.assertEqual(('Skyrim.esm', 'Unused.esp', 'Follower.esp'),
                         validate_order('# generated\nskyrim.esm\nUnused.esp\nFollower.esp\n', self.plugins))

    def test_truncated_extra_or_duplicate_output_cannot_be_applied(self):
        for text in ('Skyrim.esm\nFollower.esp',
                     'Skyrim.esm\nFollower.esp\nUnused.esp\nUnknown.esp',
                     'Skyrim.esm\nFollower.esp\nUnused.esp\nfollower.ESP'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_order(text, self.plugins)

    def test_dependency_must_precede_enabled_dependent(self):
        with self.assertRaisesRegex(ValueError, 'before'):
            validate_order('Follower.esp\nSkyrim.esm\nUnused.esp', self.plugins)

    def test_inactive_plugin_advice_is_not_an_active_blocker(self):
        report = {'stats': {'lootVersion': '0.22'}, 'plugins': [
            {'name': 'Unused.esp', 'missingMasters': ['Absent.esm']},
            {'name': 'Follower.esp', 'messages': [{'type': 'warn', 'text': 'Install the optional patch.'}]}]}
        findings = report_findings(report, self.plugins)
        self.assertEqual(1, len(findings))
        self.assertEqual('Review', findings[0].level)
        self.assertIn('optional patch', findings[0].detail)

    def test_dirty_advice_does_not_authorize_automatic_cleaning(self):
        findings = report_findings({'stats': {}, 'plugins': [
            {'name': 'Follower.esp', 'dirty': [{'itm': 3, 'cleaningUtility': 'xEdit',
                                            'info': 'Do not clean this version.'}]}]}, self.plugins)
        self.assertEqual('Review', findings[0].level)
        self.assertIn('Do not clean', findings[0].detail)
        self.assertIn('author', findings[0].action)

    def test_corrupt_or_unrecognized_report_is_not_silent_success(self):
        for report in (None, {}, {'stats': {}, 'plugins': 'invalid'},
                       {'stats': {}, 'messages': [{'type': 'warn'}]}):
            with self.subTest(report=report), self.assertRaises(ValueError):
                report_findings(report, self.plugins)

    def test_incompatibility_retains_loot_attribution(self):
        findings = report_findings({'stats': {}, 'plugins': [
            {'name': 'Follower.esp', 'incompatibilities': [{'name': 'Other.esp'}]}]}, self.plugins)
        self.assertIn('LOOT', findings[0].title)
        self.assertIn('Other.esp', findings[0].detail)
