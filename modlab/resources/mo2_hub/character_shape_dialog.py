"""One character's preset selection and the existing BodySlide slider editor."""
from pathlib import Path
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QComboBox, QFormLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .operation_dialog import OperationDialog
from .body_choices import compatible_presets, load_defaults
from .body_workflow import prepare_body_job, check_body_context
from .body_customization import prepare_customizer, collect_preset, publish_preset
from .body_customization_dialog import process_finished
from . import character_shapes as shapes


def paragraph(text):
    label=QLabel(text);label.setWordWrap(True);return label


class CharacterShapeDialog(OperationDialog):
    def __init__(self, organizer, actor, characters, parent=None):
        super().__init__(parent)
        import mobase
        self.organizer=organizer;self.actor=actor;self.characters=characters
        self.profile_path=organizer.profilePath();self.saved=shapes.load_choices(self.profile_path)
        self.operation_running=False;self.changed=False;self.applied=False
        self.job=prepare_body_job(organizer,mobase.getFileVersion,preview=True)
        row=characters[actor];self.name=row['name']
        default=load_defaults(self.profile_path).get(row.get('body',{}).get('sex'),{})
        choice=self.saved['choices'].get(actor,{})
        self.body=choice.get('body') or default.get('body')
        if not self.body:
            raise ValueError('Choose and prepare a shared body in Bodies & outfits first, then return to customize '+self.name+'.')
        self.setWindowTitle('ModLab — '+self.name+'’s body shape');self.resize(700,490)
        root=QVBoxLayout(self);root.setSpacing(14)
        title=paragraph(self.name+'’s body shape');title.setStyleSheet('font-size:24px;font-weight:600;');root.addWidget(title)
        root.addWidget(paragraph('Choose a shape for this character, or open the sliders to make your own. '
            'Their face, skin and other characters’ saved choices are retained.'))
        form=QFormLayout();form.addRow('Body',paragraph(self.body))
        self.preset=QComboBox();self.preset.addItem('Follow the existing shape setup','')
        for name in compatible_presets(self.job.catalog,self.body):self.preset.addItem(name,name)
        selected=choice.get('preset','')
        if selected and self.preset.findData(selected)<0:self.preset.addItem(selected+' — source unavailable',selected)
        self.preset.setCurrentIndex(max(0,self.preset.findData(selected)))
        form.addRow('Shape',self.preset);root.addLayout(form)
        self.customize=QPushButton('Customize '+self.name+' in BodySlide…');self.customize.clicked.connect(self.edit);root.addWidget(self.customize)
        self.default_preset=default.get('preset')
        self.status=paragraph('Saving remembers this character’s choice. Applying uses optional OBody support and checks '
            'that the character’s body and prepared outfits can take individual shapes.');root.addWidget(self.status)
        root.addWidget(paragraph('Outfits need matching body-shape data to follow this choice. Existing saves may need this '
            'character’s preset reset in OBody before showing a changed assignment.'))
        root.addStretch(1)
        buttons=QHBoxLayout()
        self.save_button=QPushButton('Save character choice');self.save_button.clicked.connect(self.save_choice);buttons.addWidget(self.save_button)
        self.apply_button=QPushButton('Apply saved character shapes');self.apply_button.clicked.connect(self.apply);buttons.addWidget(self.apply_button)
        root.addLayout(buttons)
        self.close_button=QPushButton('Back to characters');self.close_button.clicked.connect(self.accept);root.addWidget(self.close_button)

    def context(self):
        if self.organizer.profilePath()!=self.profile_path:raise ValueError('The profile changed. Reopen this character.')
        if shapes.load_choices(self.profile_path)!=self.saved:raise ValueError('Character choices changed elsewhere. Reopen this character.')

    def save_choice(self):
        try:
            self.context();preset=self.preset.currentData()
            if preset and preset not in compatible_presets(self.job.catalog,self.body):
                raise ValueError('This shape preset is no longer available. Choose an installed preset or restore its source.')
            self.saved=shapes.save_choice(self.profile_path,self.actor,
                dict(name=self.name,body=self.body,preset=preset) if preset else None,expected=self.saved)
            self.changed=True
            self.status.setText(self.name+'’s choice is saved. Choose Apply saved character shapes to prepare it.' if preset else
                self.name+' will follow the existing shape setup when saved choices are applied. Other character choices are retained.')
            return True
        except Exception as error:self.status.setText(str(error));return False

    def busy(self,value):
        self.operation_running=value
        for control in (self.preset,self.customize,self.save_button,self.apply_button,self.close_button):control.setEnabled(not value)

    def edit(self):
        try:
            import mobase
            from .skse import require_game_closed
            self.context();require_game_closed();check_body_context(self.organizer,self.job)
            preset=self.preset.currentData() or self.default_preset
            if preset not in compatible_presets(self.job.catalog,self.body):raise ValueError('Choose a starting shape first.')
            self.custom_job=prepare_body_job(self.organizer,mobase.getFileVersion,editing=True)
            name=prepare_customizer(self.custom_job,self.body,preset,self.name+' - '+self.actor,
                Path(self.organizer.managedGame().gameDirectory().absolutePath())/'Data')
            self.custom_job.record['character']=self.actor;self.custom_job.save()
            self.busy(True)
            self.handle=self.organizer.startApplication(str(self.custom_job.executable),[],
                str(self.custom_job.executable.parent),self.organizer.profile().name())
            if not self.handle:raise ValueError('MO2 could not open BodySlide.')
            self.status.setText('Editing '+name+'. Adjust the sliders, click Save (keep this preset’s name), then close BodySlide. '
                'ModLab will save the result as '+self.name+'’s choice.')
            self.timer=QTimer(self);self.timer.setInterval(750);self.timer.timeout.connect(self.editor_finished);self.timer.start()
        except Exception as error:self.failed(error)

    def failed(self,error):
        if hasattr(self,'timer'):self.timer.stop()
        self.busy(False);self.status.setText(str(error)+' Your installed character shape has not been reported as changed.')

    def editor_finished(self):
        try:
            if not process_finished(self.handle):return
            self.timer.stop()
            complete,code=self.organizer.waitForApplication(self.handle,False)
            if not complete or code!=0:raise ValueError('BodySlide did not finish normally. The editing files have been retained.')
            self.context();check_body_context(self.organizer,self.custom_job)
            data=collect_preset(self.custom_job)
            if data is None:
                self.busy(False);self.status.setText('BodySlide closed without a saved slider change. Your choices are unchanged.');return
            self.status.setText('Saving '+self.name+'’s preset and checking its installed files…')
            publish_preset(self.organizer,self.custom_job,data,self.preset_ready)
        except Exception as error:self.failed(error)

    def preset_ready(self,target,error):
        if error:self.failed(error);return
        try:
            import mobase
            self.context();self.job=prepare_body_job(self.organizer,mobase.getFileVersion,preview=True)
            name=self.custom_job.record['custom_preset'];self.preset.addItem(name,name)
            self.preset.setCurrentIndex(self.preset.findData(name));self.busy(False)
            self.save_choice()
        except Exception as problem:self.failed(problem)

    def apply(self):
        if not self.save_choice():return
        try:
            check_body_context(self.organizer,self.job)
            self.busy(True);self.status.setText('Checking the helper, prepared bodies and outfits, then applying saved character shapes…')
            from .characters import active_characters
            characters=active_characters(self.organizer,{})
            shapes.apply_choices(self.organizer,characters,self.job.catalog,self.applied_ready)
        except Exception as error:self.failed(error)

    def applied_ready(self,target,error):
        if error:self.failed(error);return
        self.saved=shapes.load_choices(self.profile_path);self.applied=True;self.changed=True;self.busy(False)
        self.status.setText('Saved character shapes are installed and effective in this profile. '+
            'Check their appearance in game; existing saves may still contain an older OBody assignment.')
