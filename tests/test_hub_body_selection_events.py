"""Exercise the actual dialog methods with lightweight Qt controls."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


def method(file,cls,name,scope):
    source=Path(__file__).resolve().parents[1]/'modlab/resources/mo2_hub'/file
    tree=ast.parse(source.read_text(encoding='utf-8'))
    body=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==cls)
    function=next(n for n in body.body if isinstance(n,ast.FunctionDef) and n.name==name)
    exec(compile(ast.Module(body=[function],type_ignores=[]),str(source),'exec'),scope)
    return scope[name]


class Item:
    def __init__(self,name,checked): self.name,self.checked=name,checked
    def text(self): return self.name
    def setCheckState(self,state): self.checked=state


class BodySelectionEventTests(unittest.TestCase):
    def dialog(self,preset):
        items=[Item('New outfit',True),Item('Old outfit',False)]
        return SimpleNamespace(preset=SimpleNamespace(currentData=lambda:preset),
            saved={'Old outfit':{'preset':'Old shape'}},items=lambda:items,
            selected=lambda:[i.name for i in items if i.checked],
            morphs=SimpleNamespace(setChecked=lambda value:None),update_selection=lambda:None)

    def test_preset_change_keeps_current_projects_including_placeholder(self):
        scope={'plan_build':lambda *args,**kwargs:None}
        fn=method('body_dialog.py','BodyDialog','preset_changed',scope)
        for preset in ('New shape',None):
            dialog=self.dialog(preset)
            dialog.job=SimpleNamespace(catalog=None)
            dialog.count=SimpleNamespace(setText=lambda text:None)
            dialog.morphs.isChecked=lambda:False
            fn(dialog)
            self.assertEqual(['New outfit'],dialog.selected())

    def test_explicit_restore_with_placeholder_does_not_select_unsaved_projects(self):
        scope={'Qt':SimpleNamespace(CheckState=SimpleNamespace(Checked=True,Unchecked=False))}
        fn=method('body_dialog.py','BodyDialog','restore_selection',scope)
        dialog=self.dialog(None); fn(dialog)
        self.assertFalse(dialog.selected())
