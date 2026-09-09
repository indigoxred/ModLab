"""Exercise the real Save handler; Qt widget rendering is verified in MO2."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as Obj
from unittest.mock import patch
import unittest

from modlab.resources.mo2_hub import scene_workflow as scene


class SceneDialogTests(unittest.TestCase):
    def test_explicit_same_choice_save_can_reapply_after_a_manual_change(self):
        widgets = Obj(**{name: object for name in ('QDialog', 'QVBoxLayout', 'QHBoxLayout', 'QLabel',
            'QComboBox', 'QCheckBox', 'QPushButton', 'QTabWidget', 'QWidget', 'QFormLayout')})
        path = Path(scene.__file__).with_name('graphics_dialog.py')
        spec = spec_from_file_location('modlab.resources.mo2_hub._tested_scene_dialog', path)
        module = module_from_spec(spec)
        with patch.dict('sys.modules', {'PyQt6.QtWidgets': widgets}): spec.loader.exec_module(module)
        with TemporaryDirectory() as tmp:
            context = {'profile_path': tmp}; accepted = []; errors = []
            host = Obj(profilePath=lambda: tmp)
            ui = Obj(organizer=host, profile_path=tmp, context=context,
                scene_available=True,
                scene_pending=None, scene_choices={'roads': 'Roads'},
                components={'roads': Obj(currentData=lambda: 'Roads')},
                assets=Obj(providers={'Roads': {'groups': {'roads': ['mesh']}}}),
                renderer=Obj(currentData=lambda: None),
                pbr=Obj(isChecked=lambda: False), lighting=Obj(isChecked=lambda: False),
                complex=Obj(isChecked=lambda: False), status=Obj(setText=errors.append), accept=lambda: accepted.append(True))
            with patch.object(scene, 'snapshot', return_value=context):
                module.GraphicsDialog.save_choices(ui)
            self.assertEqual([], errors)
            self.assertEqual([True], accepted)
            self.assertEqual({'roads': 'Roads'}, scene.load(host, 'pending')['choices'])
            self.assertIsNone(scene.load(host))
            # A catalog failure must leave those requested scenery choices alone,
            # while the existing renderer setup can still be saved.
            previous_request = scene.load(host, 'pending')
            ui.scene_available = False
            ui.renderer = Obj(currentData=lambda: 'Mesh fixes only')
            ui.lighting = Obj(isChecked=lambda: True)
            ui.components = {}
            module.GraphicsDialog.save_choices(ui)
            self.assertEqual([], errors)
            self.assertEqual(previous_request, scene.load(host, 'pending'))
            self.assertEqual('Mesh fixes only', module.workflow.load_pending(host)['choices']['renderer'])
