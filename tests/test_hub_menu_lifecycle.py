import ast
from pathlib import Path
from types import SimpleNamespace as Obj
from unittest.mock import Mock, patch
import unittest
from modlab.resources.mo2_hub import assessment


def method(name, namespace):
    source = Path(assessment.__file__).with_name('plugin.py')
    cls = next(n for n in ast.parse(source.read_text(encoding='utf-8')).body
               if isinstance(n, ast.ClassDef) and n.name == 'HubWindow')
    node = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace[name]


class MenuLifecycleTests(unittest.TestCase):
    def test_browsing_each_menu_neither_withdraws_output_nor_schedules_preparation(self):
        for name in ('bodyslide', 'npc_appearances', 'pandora', 'synthesis'):
            with self.subTest(menu=name):
                dialog = Mock()
                host = Obj(organizer=Mock(), processing=False, installing=False, summary=Mock(),
                           refresh=Mock(), finish_setup=Mock(), dialog_closed=Mock())
                graphics = Mock(); graphics.withdraw_for_upstream.return_value = False
                ns = dict(graphics=graphics, QTimer=Mock(), QApplication=Mock(),
                          QMessageBox=Mock(), mobase=Mock(), prepare_body_job=Mock(),
                          animations=Mock(), BodyDialog=Mock(return_value=dialog),
                          NpcDialog=Mock(return_value=dialog), PandoraDialog=Mock(return_value=dialog),
                          SynthesisDialog=Mock(return_value=dialog))
                for action in ('bodyslide','npc_appearances','pandora','synthesis'):
                    setattr(host, action, Mock())
                method(name, ns)(host)
                graphics.withdraw_for_upstream.assert_not_called()
                ns['QMessageBox'].warning.assert_not_called()
                ns['QTimer'].singleShot.assert_not_called()
                if name == 'bodyslide':
                    self.assertTrue(ns['prepare_body_job'].call_args.kwargs.get('preview'))
                if name == 'pandora':
                    self.assertTrue(ns['animations'].prepare_job.call_args.kwargs.get('preview'))

    def test_closing_unchanged_dialog_does_not_refresh_or_prepare(self):
        from modlab.resources.mo2_hub.dialog_workflow import finish_dialog
        host = Obj(organizer=Mock(), refresh=Mock(), finish_setup=Mock())
        dialog = Obj(operation_started=False, applied=False, preferences_changed=False)
        finish_dialog(host, dialog, Mock())
        host.refresh.assert_not_called()
        host.finish_setup.assert_not_called()

    def test_success_schedules_one_preparation_but_saved_preferences_only_refresh(self):
        from modlab.resources.mo2_hub.dialog_workflow import finish_dialog
        host = Obj(organizer=Mock(), refresh=Mock(), finish_setup=Mock())
        schedule = Mock()
        finish_dialog(host, Obj(operation_started=True, applied=True), schedule)
        schedule.assert_called_once_with(host.finish_setup)
        schedule.reset_mock()
        finish_dialog(host, Obj(operation_started=False, applied=False, preferences_changed=True), schedule)
        host.refresh.assert_called_once()
        schedule.assert_not_called()

    def test_changed_profile_prevents_apply_before_any_output_is_withdrawn(self):
        from modlab.resources.mo2_hub.dialog_workflow import begin_apply
        dialog = Obj(organizer=Obj(profilePath=lambda:'new'), profile_path='old', setEnabled=Mock())
        with patch('modlab.resources.mo2_hub.dialog_workflow.graphics.withdraw_for_upstream') as withdraw:
            with self.assertRaisesRegex(ValueError, 'profile'):
                begin_apply(dialog, Mock())
        withdraw.assert_not_called()


if __name__ == '__main__':
    unittest.main()
