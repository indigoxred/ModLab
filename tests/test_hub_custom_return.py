"""Drive the real customizer-return and choice-refresh methods without native Qt."""
from types import SimpleNamespace as NS
import sys
import unittest
from unittest.mock import patch
from test_hub_body_selection_events import method

class Combo:
    def __init__(self,value=None): self.values=[value]; self.index=0; self.blocked=False; self.changed=None
    def currentData(self): return self.values[self.index]
    def addItem(self,label,value): self.values.append(value)
    def clear(self): self.values=[]; self.index=0
    def count(self): return len(self.values)
    def findData(self,value):
        return self.values.index(value) if value in self.values else -1
    def setCurrentIndex(self,index):
        changed=index!=self.index
        self.index=index
        if changed and not self.blocked and self.changed: self.changed()
    def blockSignals(self,value): self.blocked=value

class Row:
    def __init__(self,*args): pass
    def setExpanded(self,*args): pass
    def setText(self,*args): pass
    def setToolTip(self,*args): pass

def control(): return NS(setEnabled=lambda value:None,setText=lambda value:None)

class CustomReturnTests(unittest.TestCase):
    def run_return(self,selected,options=('Outfit',)):
        group=NS(key='same-output-paths',kind='outfit',label='Armour',options=options,suggested=options[0])
        scope=dict(shared_bodies=lambda *args:['Body'],compatible_presets=lambda *args:['Old','Custom'],
            choice_groups=lambda *args,**kwargs:[group],outfit_batches=lambda *args:[],QTreeWidgetItem=Row,QComboBox=Combo,
            display_name=lambda name:name,missing_decisions=lambda choices,keys:{k:v for k,v in choices.items() if k not in keys and v})
        refresh=method('guided_body_dialog.py','GuidedBodyDialog','refresh_choices',scope)
        saved=[]; errors=[]
        dialog=NS(body_choice=Combo('Body'),shape_choice=Combo('Old'),sex_choice=Combo('female'),
            decision_context=('Body','Old'),decisions={group.key:Combo(selected)},
            parts=NS(clear=lambda:None,setItemWidget=lambda *args:None),
            guided_build=control(),customize_button=control(),pending_note=control(),choice_note=control(),
            batch_choice=Combo(),batch_row=NS(setVisible=lambda value:None),
            drafts={},defaults={},saved={},outfit_paths=set(),outfit_choice=NS(isChecked=lambda:True),
            custom_job=NS(record={'custom_preset':'Custom'}),preset=Combo('Old'),organizer=None,
            guided=control(),tabs=NS(setTabEnabled=lambda *args:None),guided_status=control(),
            customizer_error=lambda error:errors.append(error))
        dialog.draft_choice=lambda:dict(body='Body',preset=dialog.shape_choice.currentData(),decisions={k:c.currentData() for k,c in dialog.decisions.items()})
        dialog.refresh_choices=lambda **kwargs:refresh(dialog,**kwargs)
        def body_changed():
            dialog.shape_choice=Combo('Old');dialog.shape_choice.addItem('Custom','Custom')
            dialog.shape_choice.changed=dialog.refresh_choices
            refresh(dialog)
        dialog.body_changed=body_changed
        dialog.save_pending_choice=lambda:saved.append(dialog.draft_choice())
        job=NS(catalog=None,record={'project_sources':{name:'Source mod' for name in options}})
        callback=method('body_customization_dialog.py','CustomizationActions','custom_preset_ready',
            dict(prepare_body_job=lambda *args,**kwargs:job,reset_build_result=lambda obj:None,
                 context_signature=lambda obj:None,end_apply=lambda obj:None))
        with patch.dict(sys.modules,{'mobase':NS(getFileVersion=None)}): callback(dialog,None,None)
        self.assertEqual([],errors)
        self.assertEqual('Custom',dialog.shape_choice.currentData())
        self.assertEqual(selected,dialog.decisions[group.key].currentData())
        self.assertEqual(selected,saved[-1]['decisions'][group.key])
        return dialog

    def test_customization_keeps_explicit_outfit_exclusion(self): self.run_return('')
    def test_customization_keeps_unresolved_variant(self): self.run_return(None)
    def test_customization_keeps_chosen_variant(self): self.run_return('Alternate',('Outfit','Alternate'))
    def test_no_longer_compatible_choice_stays_visible_for_review(self): self.run_return('Unavailable')
