"""Connect the guided body screen to BodySlide's existing slider editor."""
from pathlib import Path
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QMessageBox
from .body_customization import prepare_customizer, collect_preset, publish_preset, reset_build_result
from .body_workflow import prepare_body_job, check_body_context
from .dialog_workflow import end_apply
from .loot_workflow import context_signature


def process_finished(handle):
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
    kernel.WaitForSingleObject.restype=wintypes.DWORD
    result=kernel.WaitForSingleObject(wintypes.HANDLE(int(handle)),0)
    if result==0: return True
    if result==258: return False
    raise OSError(ctypes.get_last_error(),'The BodySlide process could not be checked.')


class CustomizationActions:
    def customize_shared(self):
        if self.operation_running: return
        body,preset=self.body_choice.currentData(),self.shape_choice.currentData()
        if not body or not preset: return
        try:
            from .skse import require_game_closed
            import mobase
            require_game_closed(); check_body_context(self.organizer,self.job)
            QMessageBox.information(self,'Customize this shape',
                'BodySlide will open with your selected body and a separate ModLab preset. '
                'Adjust the sliders and preview, click Save (not Save As), then close BodySlide.\n\n'
                'ModLab will select your saved shape here. Choose Prepare and apply afterwards to use it for the shared body and selected outfits.')
            self.custom_job=prepare_body_job(self.organizer,mobase.getFileVersion,editing=True)
            label='Shared '+self.sex_choice.currentData()+' - '+body
            name=prepare_customizer(self.custom_job,body,preset,label,
                Path(self.organizer.managedGame().gameDirectory().absolutePath())/'Data')
            self.custom_previous=(body,preset)
            self.operation_running=True; self.guided.setEnabled(False); self.tabs.setTabEnabled(1,False)
            self.custom_handle=self.organizer.startApplication(str(self.custom_job.executable),[],
                str(self.custom_job.executable.parent),self.organizer.profile().name())
            if not self.custom_handle: raise ValueError('MO2 could not open the body editor.')
            self.custom_job.record['status']='Editing custom shape'; self.custom_job.save()
            self.guided_status.setText('Editing '+name+'. Save the preset in BodySlide, then close it to return here.')
            self.custom_timer=QTimer(self); self.custom_timer.setInterval(750)
            self.custom_timer.timeout.connect(self.customizer_finished); self.custom_timer.start()
        except Exception as error:
            self.customizer_error(error)

    def customizer_error(self,error):
        if hasattr(self,'custom_timer'): self.custom_timer.stop()
        if hasattr(self,'custom_job'):
            self.custom_job.record.update(status='Customization needs attention',error=str(error))
            try: self.custom_job.save()
            except OSError: pass
        end_apply(self); self.guided.setEnabled(True); self.tabs.setTabEnabled(1,True)
        self.guided_status.setText(str(error)+' Your previously installed body and outfits are unchanged.')

    def customizer_finished(self):
        try:
            if not process_finished(self.custom_handle): return
            self.custom_timer.stop()
            complete,code=self.organizer.waitForApplication(self.custom_handle,False)
            if not complete or code!=0: raise ValueError('BodySlide did not finish normally; its edit was retained for review.')
            check_body_context(self.organizer,self.custom_job)
            data=collect_preset(self.custom_job)
            if data is None:
                self.custom_job.record['status']='Closed without a saved preset change'; self.custom_job.save()
                end_apply(self); self.guided.setEnabled(True); self.tabs.setTabEnabled(1,True)
                self.guided_status.setText('No saved shape change was found. Your selected preset and installed output are unchanged.')
                return
            self.awaiting_publication=True
            self.guided_status.setText('Saving your custom preset and checking that it is available through MO2…')
            publish_preset(self.organizer,self.custom_job,data,self.custom_preset_ready)
        except Exception as error: self.customizer_error(error)

    def custom_preset_ready(self,target,error):
        if error:
            self.customizer_error(error); return
        try:
            import mobase
            self.job=prepare_body_job(self.organizer,mobase.getFileVersion,preview=True)
            reset_build_result(self)
            self.choice_signature=context_signature(self.organizer)
            self.preferences_changed=True
            name=self.custom_job.record['custom_preset']
            self.preset.addItem(name,name)
            self.body_changed()
            self.shape_choice.setCurrentIndex(self.shape_choice.findData(name))
            self.save_pending_choice()
            end_apply(self); self.guided.setEnabled(True); self.tabs.setTabEnabled(1,True)
            self.guided_status.setText('Your custom preset is saved and selected. Choose Prepare and apply to use this shape for the shared body and selected outfits.')
        except Exception as problem: self.customizer_error(problem)
