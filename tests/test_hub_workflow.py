import tempfile
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from modlab.resources.mo2_hub.assessment import Finding, Plugin, SetupSnapshot
from modlab.resources.mo2_hub import workflow


class WorkflowTests(unittest.TestCase):
    def setup_data(self, plugins=()):
        return SetupSnapshot('Skyrim Special Edition', '1.7.104.0', 'Test', 'C:/Game', tuple(plugins))

    def test_install_automatically_runs_loot_and_applies_checked_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            organizer = SimpleNamespace(modsPath=lambda: str(root / 'mods'))
            run = SimpleNamespace(directory=root, previous=('B.esp', 'A.esm'), proposal=('A.esm', 'B.esp'),
                                  findings=(), signature=('new',))
            with patch.object(workflow, 'collect_setup', return_value=self.setup_data()), \
                 patch.object(workflow, 'context_signature', return_value=('new',)), \
                 patch.object(workflow, 'run_loot', return_value=run) as loot, \
                 patch.object(workflow, 'apply_order', return_value=run.previous) as apply:
                result = workflow.finish_setup(organizer, root, lambda _: '', previous_signature=('old',))
            loot.assert_called_once()
            apply.assert_called_once_with(organizer, run.proposal, run.signature)
            self.assertTrue(any('applied' in s.lower() for s in result.steps))
            self.assertTrue(result.record.is_file())

    def test_no_plugin_change_reuses_current_loot_advice_without_rerunning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            organizer = SimpleNamespace(modsPath=lambda: str(root / 'mods'))
            advice = (Finding('Review', 'loot-cleaning', 'Cleaning advice: A.esp', 'Specific advice', 'Review'),)
            with patch.object(workflow, 'collect_setup', return_value=self.setup_data()), \
                 patch.object(workflow, 'context_signature', return_value=('same',)), \
                 patch.object(workflow, 'run_loot') as loot:
                result = workflow.finish_setup(organizer, root, lambda _: '', previous_signature=('same',), previous_findings=advice)
            loot.assert_not_called()
            self.assertIn(advice[0], result.findings)

    def test_missing_master_prevents_sort_and_reports_specific_blocker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            organizer = SimpleNamespace(modsPath=lambda: str(root / 'mods'))
            with patch.object(workflow, 'collect_setup', return_value=self.setup_data([Plugin('A.esp', 1, ('Missing.esm',))])), \
                 patch.object(workflow, 'context_signature', return_value=('new',)), \
                 patch.object(workflow, 'run_loot') as loot:
                result = workflow.finish_setup(organizer, root, lambda _: '')
            loot.assert_not_called()
            self.assertTrue(any(f.code == 'missing-master' for f in result.findings))

    def test_failed_tool_is_recorded_and_never_becomes_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            organizer = SimpleNamespace(modsPath=lambda: str(root / 'mods'))
            with patch.object(workflow, 'collect_setup', return_value=self.setup_data()), \
                 patch.object(workflow, 'context_signature', return_value=('new',)), \
                 patch.object(workflow, 'run_loot', side_effect=RuntimeError('Missing Qt6Core.dll')):
                result = workflow.finish_setup(organizer, root, lambda _: '')
            self.assertTrue(any('Qt6Core.dll' in f.detail for f in result.findings))
            self.assertTrue(result.record.is_file())

    def test_normal_flow_prepares_supported_cleaning_without_manual_helper_button(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            organizer = SimpleNamespace(modsPath=lambda: str(root / 'mods'))
            advice = Finding('Review', 'loot-cleaning', 'A.esp', '', '')
            run = SimpleNamespace(directory=root, previous=('A.esp',), proposal=('A.esp',), findings=(advice,), signature=('new',))
            (root / 'report.json').write_text(json.dumps({'plugins': []}))
            target = root / 'mods/Cleaned'
            with patch.object(workflow, 'collect_setup', return_value=self.setup_data()), \
                 patch.object(workflow, 'context_signature', return_value=('new',)), \
                 patch.object(workflow, 'run_loot', return_value=run), \
                 patch.object(workflow, 'prepare_cleaning', return_value=(target, (), ('Checked cleaned copy',))) as cleaning:
                result = workflow.finish_setup(organizer, root, lambda _: '')
            cleaning.assert_called_once()
            self.assertEqual(target, result.cleaned_output)
            self.assertNotIn(advice, result.findings)
