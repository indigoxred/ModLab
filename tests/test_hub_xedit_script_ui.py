import unittest
from modlab.resources.mo2_hub.xedit_script_ui import dialog_action, selection_step, report_finished

class ScriptUITests(unittest.TestCase):
    def test_terminal_marker_allows_close_but_is_not_a_verification_verdict(self):
        self.assertTrue(report_finished('MODLAB-WINNERS-1\tjob\ninvalid rows\nEND\tjob\n','job'))
        self.assertFalse(report_finished('MODLAB-WINNERS-1\tjob\nEND\tother','job'))
        self.assertFalse(report_finished('MODLAB-WINNERS-1\tjob\n','job'))
    def test_queued_click_needs_observed_departure_and_retries_are_bounded(self):
        state={}
        self.assertIsNone(selection_step(state,False))
        for i in range(20):
            self.assertEqual('click',selection_step(state,True))
            self.assertFalse(state.get('selected',False))
        self.assertEqual('cancel',selection_step(state,True))
        self.assertIsNone(selection_step(state,False))
        self.assertTrue(state['selected'])

    def test_only_exact_selection_dialog_and_its_ok_button_can_start(self):
        self.assertEqual('select',dialog_action('TfrmModuleSelect','Module Selection',False,False, [('TButton','OK')]))
        self.assertIsNone(dialog_action('TfrmModuleSelect','Module Selection',True,False,[('TButton','OK')]))
        self.assertIsNone(dialog_action('OtherDialog','Module Selection',False,False,[('TButton','OK')]))
        self.assertIsNone(dialog_action('TfrmModuleSelect','Module Selection',False,False,[('TButton','Save')]))
    def test_only_completed_script_main_window_can_close(self):
        self.assertEqual('close',dialog_action('TfrmMain','SSEScript 4.1.5f x64',True,True,[]))
        self.assertIsNone(dialog_action('TfrmMain','SSEScript 4.1.5f x64',True,False,[]))
        self.assertIsNone(dialog_action('TfrmMain','SSEEdit 4.1.5f x64',True,True,[]))
        self.assertIsNone(dialog_action('TfrmModuleSelect','Save modified plugins',True,True,[]))
