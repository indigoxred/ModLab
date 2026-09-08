import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from modlab.resources.mo2_hub import shape_preparation
from modlab.resources.mo2_hub.outputs import publish_output, MANIFEST, digest
from unittest.mock import patch


class ShapePreparationTests(unittest.TestCase):
    def test_dialog_failure_recovers_both_outputs_and_retains_a_clear_pending_state(self):
        from types import SimpleNamespace
        from tests.test_hub_body_selection_events import method
        from modlab.resources.mo2_hub import character_shapes
        from modlab.resources.mo2_hub.outputs import output_name
        failed=method('guided_body_dialog.py','GuidedBodyDialog','shapes_failed',
            {'__name__':'modlab.resources.mo2_hub.guided_body_dialog','__package__':'modlab.resources.mo2_hub'})
        with TemporaryDirectory() as folder,patch('modlab.resources.mo2_hub.skse.require_game_closed'):
            root=Path(folder);profile=root/'profiles/Test';profile.mkdir(parents=True)
            identity=str(profile);before=character_shapes.load_choices(identity)
            body=root/'mods/body';config=root/'mods'/output_name('Test',identity,character_shapes.TOOL)
            jobs={}
            for target,tool,relative in ((body,'BodySlide','body.nif'),(config,character_shapes.TOOL,character_shapes.OBODY_CONFIG)):
                target.mkdir(parents=True)
                for version in ('old','new'):
                    job=root/'builds'/tool/version;file=job/'output'/relative;file.parent.mkdir(parents=True)
                    file.write_text(version)
                    publish_output(target,job,identity,{'Item':{'outputs':[relative],'preset':version}},{relative:digest(file)},tool=tool)
                jobs[tool]=job
            record=jobs[character_shapes.TOOL]/'operation.json'
            character_shapes._write(identity,dict(before,applied={'record_path':str(record)}),before)
            messages=[]
            host=SimpleNamespace(profilePath=lambda:identity,modsPath=lambda:str(root/'mods'),
                profile=lambda:SimpleNamespace(name=lambda:'Test'),refresh=lambda:messages.append('refresh'))
            dialog=SimpleNamespace(organizer=host,profile_path=identity,installed=body,
                job=SimpleNamespace(directory=jobs['BodySlide']),shape_record=record,
                shape_recipe={'choices':before},shape_defaults_published=False,applied=True,
                setEnabled=lambda value:None,guided_status=SimpleNamespace(setText=messages.append))
            failed(dialog,'Assignment activation failed')
            self.assertEqual('old',(body/'body.nif').read_text())
            self.assertEqual('old',(config/character_shapes.OBODY_CONFIG).read_text())
            self.assertEqual(before,character_shapes.load_choices(identity))
            self.assertFalse(dialog.applied)
            self.assertEqual('Blocked',shape_preparation.pending_finding(identity).level)
            self.assertIn('previous files restored',' '.join(messages))

    def test_rollback_only_restores_the_output_published_by_this_attempt(self):
        with TemporaryDirectory() as folder,patch('modlab.resources.mo2_hub.skse.require_game_closed'):
            root=Path(folder);target=root/'mods/body';target.mkdir(parents=True)
            profile=str(root/'profiles/test');prior=root/'builds/body/old';job=root/'builds/body/new'
            for directory,text in ((prior,'old'),(job,'new')):
                (directory/'output').mkdir(parents=True);(directory/'output/body.nif').write_text(text)
                publish_output(target,directory,profile,{'Body':{'outputs':['body.nif'],'preset':text}}, {'body.nif':digest(directory/'output/body.nif')})
            shape_preparation.restore_attempt(target,job,profile,'BodySlide')
            self.assertEqual('old',(target/'body.nif').read_text())
            self.assertFalse(shape_preparation.restore_attempt(target,job,profile,'BodySlide'))

    def test_rollback_does_not_discard_a_manual_edit(self):
        with TemporaryDirectory() as folder,patch('modlab.resources.mo2_hub.skse.require_game_closed'):
            root=Path(folder);target=root/'mods/body';target.mkdir(parents=True)
            job=root/'builds/body/new';(job/'output').mkdir(parents=True)
            (job/'output/body.nif').write_text('new');profile=str(root/'profiles/test')
            publish_output(target,job,profile,{'Body':{'outputs':['body.nif'],'preset':'new'}},{'body.nif':digest(job/'output/body.nif')})
            (target/'body.nif').write_text('manual')
            with self.assertRaisesRegex(ValueError,'changed outside'):
                shape_preparation.restore_attempt(target,job,profile,'BodySlide')
            self.assertEqual('manual',(target/'body.nif').read_text())

    def test_incomplete_preparation_is_a_launch_blocker_with_a_recovery_path(self):
        with TemporaryDirectory() as folder:
            shape_preparation.mark_pending(folder,{'status':'Config application failed','body_job':'retained/job'})
            finding=shape_preparation.pending_finding(folder)
            self.assertEqual('Blocked',finding.level)
            self.assertIn('retained/job',finding.detail)
