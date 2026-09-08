import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from modlab.resources.mo2_hub.pandora_workflow import source_signature


class AnimationFaceInputsTests(unittest.TestCase):
    def test_facegen_rebuild_does_not_invalidate_behaviors_but_animation_does(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            face=root/'meshes/actors/character/FaceGenData/FaceGeom/Skyrim.esm/000123.nif'
            animation=root/'meshes/actors/character/animations/example.hkx'
            face.parent.mkdir(parents=True);animation.parent.mkdir(parents=True)
            face.write_bytes(b'old face');animation.write_bytes(b'behavior input')
            before=source_signature([('NPC output',root)])
            face.write_bytes(b'new generated face with different bytes')
            self.assertEqual(before,source_signature([('NPC output',root)]))
            animation.write_bytes(b'new animation input')
            self.assertNotEqual(before,source_signature([('NPC output',root)]))
