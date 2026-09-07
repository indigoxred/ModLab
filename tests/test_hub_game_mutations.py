"""Gameplay must not overlap mutations of the profile's effective files."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as Obj
import unittest
from unittest.mock import Mock, patch

from modlab.resources.mo2_hub.outputs import publish_output, digest, MANIFEST
from modlab.resources.mo2_hub.loot_workflow import apply_order
from modlab.resources.mo2_hub.recovery import restore_previous_output
from modlab.resources.mo2_hub.skse import require_game_closed


class GameMutationTests(unittest.TestCase):
    def test_running_game_preserves_previous_output_and_pending_build(self):
        with TemporaryDirectory() as directory:
            root=Path(directory); target=root/'mods/Generated'; target.mkdir(parents=True)
            (target/'meta.ini').write_text('[General]')
            job=root/'builds/body/check'; (job/'output/meshes').mkdir(parents=True)
            source=job/'output/meshes/body.nif'; source.write_bytes(b'generated')
            projects={'Body':dict(outputs=['meshes/body.nif'],preset='Test',source_mod='Test')}
            with patch('modlab.resources.mo2_hub.skse.subprocess.run',
                       return_value=Obj(returncode=0,stdout=b'"SkyrimSE.exe","123"')):
                with self.assertRaisesRegex(ValueError,'Close Skyrim'):
                    publish_output(target,job,'Test',projects,{'meshes/body.nif':digest(source)})
            self.assertEqual(['meta.ini'],[p.name for p in target.iterdir()])
            self.assertFalse((job/'previous-output').exists())
            self.assertFalse((job/'pending-output').exists())
            self.assertEqual(b'generated',source.read_bytes())

    def test_running_game_stops_order_changes_before_host_mutation(self):
        host=Mock()
        with patch('modlab.resources.mo2_hub.skse.subprocess.run',
                   return_value=Obj(returncode=0,stdout=b'"SkyrimSE.exe","123"')):
            with self.assertRaisesRegex(ValueError,'Close Skyrim'):
                apply_order(host,(),())
        host.pluginList.assert_not_called()

    def test_running_game_stops_restore_before_reading_or_replacing_output(self):
        with TemporaryDirectory() as directory:
            target=Path(directory)/'mods/Generated'; target.mkdir(parents=True)
            (target/MANIFEST).write_text('intentionally not parsed while game is running')
            with patch('modlab.resources.mo2_hub.skse.subprocess.run',
                       return_value=Obj(returncode=0,stdout=b'"SkyrimSE.exe","123"')):
                with self.assertRaisesRegex(ValueError,'Close Skyrim'):
                    restore_previous_output(target,'Test','BodySlide','unused')
            self.assertEqual('intentionally not parsed while game is running',(target/MANIFEST).read_text())

    def test_failed_process_inspection_is_not_treated_as_game_closed(self):
        with patch('modlab.resources.mo2_hub.skse.subprocess.run',
                   return_value=Obj(returncode=1,stdout=b'')):
            with self.assertRaisesRegex(ValueError,'Could not check'):
                require_game_closed()

    def test_closed_game_can_continue(self):
        with patch('modlab.resources.mo2_hub.skse.subprocess.run',
                   return_value=Obj(returncode=0,stdout=b'INFO: No tasks are running which match the specified criteria.')):
            require_game_closed()
