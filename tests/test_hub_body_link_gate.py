from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
from modlab.resources.mo2_hub.bodyslide import Catalog, Project
from modlab.resources.mo2_hub.body_workflow import BodyJob, run_body_job
from tests.test_hub_body_morphs import tri


class BodyLinkGateTests(unittest.TestCase):
    def test_successful_helper_cannot_publish_morph_build_without_attached_link(self):
        with TemporaryDirectory() as temp:
            root=Path(temp); runner=root/'runner'; runner.mkdir()
            (runner/'Config.xml').write_text('<Config/>')
            catalog=Catalog({'Body':Project('Body',('meshes/actors/body.nif',),morph_output='meshes/actors/body.tri')},
                            {'Slim':('Body',set())},{})
            job=BodyJob(root,catalog,runner/'BodySlide x64.exe',(),(),{})
            class Organizer:
                def managedGame(self): return self
                def gameDirectory(self): return self
                def absolutePath(self): return str(root/'game')
                def modsPath(self): return str(root/'mods')
                def profile(self): return self
                def name(self): return 'Test'
                def startApplication(self,*args):
                    target=root/'output/meshes/actors'; target.mkdir(parents=True)
                    (target/'body.nif').write_bytes(b'Gamebryo File Format, Version '+bytes(150))
                    (target/'body.tri').write_bytes(tri())
                    return 1
                def waitForApplication(self,*args): return True,0
            def inspect(sdk,tools,meshes,directory):
                return {str(path):{'shapes':{'Body':{'vertices':1,'links':[]}}} for path in meshes}
            with patch('modlab.resources.mo2_hub.body_workflow.check_body_context'), \
                 patch('modlab.resources.mo2_hub.runtime.ensure_sdk10',return_value=root/'sdk'), \
                 patch('modlab.resources.mo2_hub.body_meshes.inspect_meshes',side_effect=inspect):
                with self.assertRaisesRegex(ValueError,'BODYTRI'):
                    run_body_job(Organizer(),job,['Body'],'Slim',morphs=True)
            self.assertIsNone(job.archive)
            self.assertFalse((root/'ModLab BodySlide Output.zip').exists())
            self.assertEqual('Needs attention',job.record['status'])


if __name__=='__main__': unittest.main()
