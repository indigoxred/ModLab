import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from modlab.resources.mo2_hub import body_inputs


class PresetInvalidationTests(unittest.TestCase):
    def test_mesh_helper_ignores_bodyslide_sources_but_keeps_model_and_plugin_inputs(self):
        original=dict(order=['Skyrim.esm'],sources=[['Skyrim.esm','game/Skyrim.esm','hash']],assets=[
            ['Bodies','mods/Bodies',[['meshes/body.nif',5,10],['CalienteTools/BodySlide/body.osp',12,30]]],
            ['Presets','mods/Presets',[['CalienteTools/BodySlide/SliderPresets/shared.xml',10,20]]]])
        changed=copy.deepcopy(original)
        changed['assets'][1][2].append(['CalienteTools/BodySlide/SliderPresets/Lydia.xml',50,50])
        self.assertEqual(body_inputs.mesh_helper_state(original),body_inputs.mesh_helper_state(changed))
        changed['assets'][0][2][0][2]=99
        self.assertNotEqual(body_inputs.mesh_helper_state(original),body_inputs.mesh_helper_state(changed))
        self.assertEqual(2,len(original['assets']))

    def test_unused_preset_does_not_invalidate_build_but_used_or_duplicate_preset_does(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);runner=root/'runner/SliderPresets';runner.mkdir(parents=True)
            used=root/'used.xml';used.write_text('<SliderPresets><Preset name="Athletic"/></SliderPresets>')
            (runner/'used.xml').write_bytes(used.read_bytes())
            old=[['SliderPresets/used.xml',str(used),10,20],['SliderSets/body.osp','body.osp',30,40]]
            record=dict(preset='Athletic',sources=old)
            extra=root/'custom.xml';extra.write_text('<SliderPresets><Preset name="Lydia"/></SliderPresets>')
            current=old+[['SliderPresets/custom.xml',str(extra),20,50]]
            self.assertTrue(body_inputs.build_sources_match(record,current,root/'operation.json'))
            modified=copy.deepcopy(current);modified[0][3]=99
            self.assertFalse(body_inputs.build_sources_match(record,modified,root/'operation.json'))
            extra.write_text('<SliderPresets><Preset name="Athletic"/></SliderPresets>')
            self.assertFalse(body_inputs.build_sources_match(record,current,root/'operation.json'))

    def test_receipts_without_preset_evidence_keep_the_original_strict_check(self):
        self.assertTrue(body_inputs.build_sources_match({'sources':[['file',1]]},[['file',1]],Path('missing')))
        self.assertFalse(body_inputs.build_sources_match({'sources':[['file',1]]},[],Path('missing')))
