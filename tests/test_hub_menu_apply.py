from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as Obj
from unittest.mock import Mock, patch
import unittest
from modlab.resources.mo2_hub import dialog_workflow as flow, body_workflow as body, pandora_workflow as animation
from modlab.resources.mo2_hub.bodyslide import Catalog


class ApplyAndPreviewTests(unittest.TestCase):
    def test_pending_publication_keeps_operation_running_until_callback(self):
        organizer = Obj(profilePath=lambda:'profile')
        dialog = Obj(organizer=organizer, profile_path='profile', choice_signature='same',
                     setEnabled=Mock(), awaiting_publication=False)
        def publish_later(): dialog.awaiting_publication = True
        with patch.object(flow, 'context_signature', return_value='same'), patch.object(flow, 'require_game_closed'), \
             patch.object(flow.graphics, 'withdraw_for_upstream', return_value=False):
            flow.begin_apply(dialog, publish_later)
        self.assertTrue(dialog.operation_running)
        self.assertEqual(False, dialog.setEnabled.call_args.args[0])
        flow.end_apply(dialog)
        self.assertFalse(dialog.operation_running)
        self.assertEqual(True, dialog.setEnabled.call_args.args[0])

    def test_failed_apply_restores_current_graphics_without_retrying_source(self):
        with TemporaryDirectory() as temp:
            marker = Path(temp) / 'withdrawn'; marker.write_text('retained')
            organizer = Obj(profilePath=lambda:'profile')
            hub = Obj(organizer=organizer, refresh=Mock(), finish_setup=Mock(), summary=Mock())
            dialog = Obj(profile_path='profile', operation_started=True, applied=False)
            with patch.object(flow.graphics, 'withdrawn_path', return_value=marker), \
                 patch.object(flow.graphics, 'load_choices', return_value={'saved':True}), \
                 patch.object(flow.graphics, 'reapply_saved') as restore:
                schedule=Mock(); flow.finish_dialog(hub, dialog, schedule)
                restore.call_args.args[2](None, None)
            restore.assert_called_once()
            hub.refresh.assert_called_once()
            schedule.assert_not_called()
            hub.finish_setup.assert_not_called()

    def test_body_preview_does_not_copy_helper_create_job_or_withdraw_output(self):
        with TemporaryDirectory() as temp:
            root=Path(temp); exe=root/'BodySlide x64.exe'; exe.write_bytes(b'helper')
            host=Obj(managedGame=lambda:Obj(gameName=lambda:'Skyrim Special Edition'), modsPath=lambda:str(root),
                     profile=lambda:Obj(name=lambda:'Test'),profilePath=lambda:str(root/'profile'))
            with patch.object(body,'effective_files',return_value={exe.name:exe}), \
                 patch.object(body,'context_signature',return_value=()), \
                 patch.object(body,'read_catalog_files',return_value=Catalog({}, {}, {})), \
                 patch.object(body.shutil,'copy2') as copy, \
                 patch.object(flow.graphics,'require_upstream_view') as require:
                preview=body.prepare_body_job(host,lambda _: '5.8.2',preview=True)
            self.assertIsNone(preview.directory)
            copy.assert_not_called(); require.assert_not_called()
            self.assertEqual([exe],list(root.iterdir()))

    def test_animation_preview_does_not_download_runtime_or_copy_helper(self):
        host=Obj(managedGame=lambda:Obj(gameName=lambda:'Skyrim Special Edition'),
                 profile=lambda:Obj(name=lambda:'Test'),profilePath=lambda:'profile')
        with patch.object(animation,'locate_engine',return_value=Path('Pandora.exe')), \
             patch.object(animation,'available_patches',return_value=()), \
             patch.object(animation,'context_signature',return_value=()), \
             patch.object(animation,'ensure_net10') as runtime, patch.object(animation.shutil,'copytree') as copy:
            preview=animation.prepare_job(host,preview=True)
        self.assertIsNone(preview.directory)
        runtime.assert_not_called(); copy.assert_not_called()
