"""Character-first appearance choices; technical bulk controls remain in Advanced."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget)
from .npc_dialog import NpcDialog
from .characters import active_characters, body_description
from .guidance import display_name
from .npc_reset import KEEP_SAVED, appearance_selection


def paragraph(text=''):
    label=QLabel(text); label.setWordWrap(True); return label


class CharacterDialog(NpcDialog):
    def __init__(self, organizer, parent=None):
        super().__init__(organizer, parent)
        from .body_choices import load_defaults
        self.shared_defaults=load_defaults(self.profile_path)
        self.setWindowTitle('ModLab — Character appearances'); self.resize(1100, 790)
        self.directory_problem=None
        try: self.characters=active_characters(organizer, self.rows)
        except (ValueError,OSError) as error:
            self.directory_problem=str(error)
            self.characters={key:dict(name=row['label'],editor='',unique=True,skin=None,options=row['options'],
                search=(row['label']+' '+key+' '+' '.join(row['options'])).casefold()) for key,row in self.rows.items()}
        root=self.layout(); advanced=QWidget(); old=QVBoxLayout(advanced)
        while root.count():
            item=root.takeAt(0)
            if item.widget(): old.addWidget(item.widget())
            elif item.layout(): old.addLayout(item.layout())
            else: old.addItem(item)
        self.tabs=QTabWidget(); root.addWidget(self.tabs)
        self.character_page=QWidget(); layout=QVBoxLayout(self.character_page)
        layout.addWidget(paragraph('Find a character, then choose from the installed appearances that cover them. '
            'You can make several choices before applying them together.'))
        if self.directory_problem:
            layout.addWidget(paragraph('The full character directory could not be read: '+self.directory_problem+
                ' Available replacement choices and Advanced remain accessible.'))
        filters=QHBoxLayout(); self.character_search=QLineEdit()
        self.character_search.setPlaceholderText('Find Lydia, Serana, another character or a mod…'); filters.addWidget(self.character_search, 1)
        self.character_filter=QComboBox()
        self.character_filter.addItem('Named characters', 'unique')
        self.character_filter.addItem('With installed appearances', 'options')
        self.character_filter.addItem('All characters', 'all'); filters.addWidget(self.character_filter)
        layout.addLayout(filters)
        split=QSplitter(); layout.addWidget(split, 1)
        self.character_list=QListWidget(); split.addWidget(self.character_list)
        panel=QWidget(); detail=QVBoxLayout(panel); split.addWidget(panel); split.setSizes([320,700])
        self.character_name=QLabel('Select a character'); self.character_name.setStyleSheet('font-size: 23px; font-weight: 600;')
        detail.addWidget(self.character_name)
        self.character_note=paragraph(); detail.addWidget(self.character_note)
        form=QFormLayout(); self.appearance_choice=QComboBox(); form.addRow('Face && hair',self.appearance_choice); detail.addLayout(form)
        self.appearance_note=paragraph(); detail.addWidget(self.appearance_note)
        body_form=QFormLayout()
        self.character_body_choice=QComboBox();body_form.addRow('Body',self.character_body_choice)
        self.character_body_note=paragraph();body_form.addRow('',self.character_body_note)
        self.body_assignment=paragraph(); body_form.addRow('Current body', self.body_assignment)
        self.skin_assignment=paragraph(); body_form.addRow('Current skin', self.skin_assignment)
        detail.addLayout(body_form)
        self.shape_button=QPushButton('Choose or customize this character’s shape…')
        self.shape_button.clicked.connect(self.character_shape);detail.addWidget(self.shape_button)
        self.shape_note=paragraph();detail.addWidget(self.shape_note)
        detail.addStretch(1)
        detail.addWidget(paragraph('An appearance package keeps its face, hair and related assets together. '
            'You can use its body or choose your prepared shared body while retaining its skin. '
            'Winning gameplay data and outfits are retained.'))
        self.character_status=paragraph(); layout.addWidget(self.character_status)
        buttons=QHBoxLayout(); self.character_apply=QPushButton('Apply appearance choices')
        self.character_apply.clicked.connect(self.generate); buttons.addWidget(self.character_apply)
        close=QPushButton('Close'); close.clicked.connect(self.accept); buttons.addWidget(close); layout.addLayout(buttons)
        self.tabs.addTab(self.character_page,'Characters'); self.tabs.addTab(advanced,'Advanced & bulk choices')
        self.tabs.currentChanged.connect(lambda index: self.show_character(self.character_list.currentItem()) if index == 0 else None)
        self.character_search.textChanged.connect(self.refresh_characters)
        self.character_filter.currentIndexChanged.connect(self.refresh_characters)
        self.character_list.currentItemChanged.connect(self.show_character)
        self.appearance_choice.currentIndexChanged.connect(self.select_appearance)
        self.character_body_choice.currentIndexChanged.connect(self.select_body)
        self.current_character=None
        self.refresh_characters(); self.update_pending()

    def refresh_characters(self):
        previous=self.current_character
        query=self.character_search.text().strip().casefold(); group=self.character_filter.currentData()
        self.character_list.blockSignals(True); self.character_list.clear(); selected=None
        for key,row in sorted(self.characters.items(),key=lambda item:(item[1]['name'].casefold(),item[0])):
            if query and query not in row['search']: continue
            if not query and group=='unique' and not row['unique'] and key not in self.selected: continue
            if group=='options' and not row['options']: continue
            item=QListWidgetItem(row['name']); item.setData(Qt.ItemDataRole.UserRole,key)
            item.setToolTip(row['editor']+'\n'+key); self.character_list.addItem(item)
            if key==previous: selected=item
        self.character_list.blockSignals(False)
        self.character_list.setCurrentItem(selected or self.character_list.item(0))
        if not self.character_list.count(): self.show_character(None)

    def show_character(self, item, previous=None):
        self.current_character=item.data(Qt.ItemDataRole.UserRole) if item else None
        self.appearance_choice.blockSignals(True); self.appearance_choice.clear()
        self.character_body_choice.blockSignals(True);self.character_body_choice.clear()
        self.character_body_choice.addItem('Use the body supplied by the appearance','')
        reset_label='Follow current mod setup'
        if self.current_character in self.applied_selection: reset_label+=' (remove my override)'
        self.appearance_choice.addItem(reset_label,'')
        if self.current_character in self.applied_selection:
            self.appearance_choice.addItem('Keep my saved appearance',KEEP_SAVED)
        if not item:
            self.character_name.setText('No matching characters')
            self.character_note.setText('Try another name or choose All characters.')
            self.appearance_choice.setEnabled(False); self.appearance_note.setText(''); self.body_assignment.setText(''); self.skin_assignment.setText('')
            self.character_body_choice.setEnabled(False);self.character_body_note.setText('')
            self.shape_button.setEnabled(False);self.shape_note.setText('')
        else:
            key=self.current_character; row=self.characters[key]
            self.character_name.setText(row['name']); self.character_name.setToolTip(row['editor']+'\n'+key)
            self.character_note.setText(f"{len(row['options'])} installed appearance source(s) cover this character." if row['options'] else
                'No installed appearance package supplies this character. Their current appearance is retained. '
                'Install a replacer that includes this character to add another choice.')
            labels=[display_name(o['mod']) for o in row['options'].values()]
            for plugin,option in row['options'].items():
                name=display_name(option['mod'])
                if labels.count(name)>1: name+=' ('+plugin+')'
                self.appearance_choice.addItem(name,plugin)
                self.appearance_choice.setItemData(self.appearance_choice.count()-1, plugin, Qt.ItemDataRole.ToolTipRole)
            chosen=self.selected.get(key,'')
            if chosen and chosen not in row['options']: self.appearance_choice.addItem(chosen+' — unavailable',chosen)
            self.appearance_choice.setCurrentIndex(max(0,self.appearance_choice.findData(chosen)))
            self.appearance_choice.setEnabled(bool(row['options']) or bool(chosen))
            description=body_description(row, self.organizer.resolvePath, self.organizer.modsPath(),
                overwrite_path=self.organizer.overwritePath(),
                game_data=self.organizer.managedGame().gameDirectory().absolutePath()+'/Data')
            self.body_assignment.setText(description['body'])
            self.skin_assignment.setText(description['skin'])
            self.body_assignment.setToolTip(description['evidence'])
            self.skin_assignment.setToolTip(description['evidence'])
            sex=row.get('body',{}).get('sex',row.get('sex'))
            default=self.shared_defaults.get(sex,{})
            if default.get('body'):
                self.character_body_choice.addItem('Shared '+str(sex)+' body — '+default['body']+' / '+default.get('preset',''),'shared')
            chosen_body=self.body_choices.get(key,'')
            if chosen_body and self.character_body_choice.findData(chosen_body)<0:
                self.character_body_choice.addItem('Shared body — preparation needed',chosen_body)
            self.character_body_choice.setCurrentIndex(max(0,self.character_body_choice.findData(chosen_body)))
            self.character_body_choice.setEnabled(True)
            self.character_body_note.setText('Keeps the selected appearance’s skin. Applying checks the installed body variant and prepares this character’s own body references. It does not change other characters.' if default else
                'Prepare a shared body in Bodies & outfits to add a body choice here. The appearance’s current body is retained.')
            self.body_labels[key]=row['name']
            self.shape_button.setEnabled(True)
            from .character_shapes import load_choices
            try:
                shapes=load_choices(self.profile_path);choice=shapes['choices'].get(key)
                applied=(shapes.get('applied') or {}).get('choices',{}).get(key)
                self.shape_note.setText(('Saved shape: '+choice['preset']+(' — assignment prepared' if choice==applied else ' — awaiting preparation')) if choice else
                    'Optional individual shapes. Your shared body remains the default until you choose an exception.')
            except (OSError,ValueError) as error:self.shape_note.setText(str(error))
        self.character_body_choice.blockSignals(False)
        self.appearance_choice.blockSignals(False); self.explain_appearance()

    def character_shape(self):
        if not self.current_character:return
        try:
            from .character_shape_dialog import CharacterShapeDialog
            self.shape_window=CharacterShapeDialog(self.organizer,self.current_character,self.characters,self)
            self.shape_window.finished.connect(self.shape_closed)
            self.shape_window.setModal(True);self.shape_window.show()
        except Exception as error:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self,'Character shape setup',str(error))

    def shape_closed(self):
        if self.shape_window.changed:
            self.preferences_changed=True
        if self.shape_window.files_changed:
            from .loot_workflow import context_signature
            self.choice_signature=context_signature(self.organizer)
        self.show_character(self.character_list.currentItem())

    def select_body(self):
        if not self.current_character:return
        value=self.character_body_choice.currentData()
        if value:self.body_choices[self.current_character]=value
        else:self.body_choices.pop(self.current_character,None)
        self.update_pending()

    def select_appearance(self):
        if not self.current_character: return
        key=self.current_character; chosen=self.appearance_choice.currentData()
        updated=appearance_selection(self.selected,self.applied_selection,key,chosen)
        chosen=updated.get(key,'')
        if key in self.controls:
            combo=self.controls[key]; combo.blockSignals(True); combo.setCurrentIndex(max(0,combo.findData(chosen))); combo.blockSignals(False)
        self.choose(key,chosen); self.explain_appearance()

    def choose(self,key,provider):
        super().choose(key,provider)
        if hasattr(self,'character_status'): self.update_pending()

    def update_pending(self):
        removed=len(set(self.applied_selection)-set(self.selected))
        bodies=getattr(self,'body_choices',{})
        previous_bodies=getattr(self,'applied_body_choices',{})
        removed+=len(set(previous_bodies)-set(bodies))
        self.character_status.setText(f'{len(self.selected)} appearances selected. '+(f'{removed} saved override(s) will be removed; source mods will supply those appearances.' if removed else 'Applying prepares their paired files and checks the installed result.'))
        if bodies:self.character_status.setText(f'{len(self.selected)} face choices; {len(bodies)} shared body choice(s). Pending changes are saved when you close. Apply checks compatibility and prepares the selected characters.')
        self.character_apply.setText('Clear saved appearance overrides' if not self.selected and not bodies and removed else 'Apply character choices')
        self.character_apply.setEnabled(bool(self.selected) or bool(self.applied_selection) or bool(bodies) or bool(previous_bodies))

    def explain_appearance(self):
        key=self.current_character; chosen=self.appearance_choice.currentData()
        if chosen==KEEP_SAVED: chosen=self.applied_selection.get(key,'')
        option=self.characters.get(key,{}).get('options',{}).get(chosen)
        if not chosen:
            self.appearance_note.setText("Applying removes ModLab's explicit appearance override for this character. The current source mods then supply their appearance. Other saved character choices remain." if key in self.applied_selection else "The installed source mods supply this character. No explicit ModLab appearance override will be added.")
        elif not option: self.appearance_note.setText('This saved appearance is no longer available. Enable its source mod, choose an installed alternative, or explicitly follow the current mod setup.')
        elif not option['ready']: self.appearance_note.setText(option['problem'])
        else: self.appearance_note.setText('The matching face mesh and face tint are present in '+display_name(option['mod'])+'. They will be prepared together.')

    def ready(self,target,error):
        super().ready(target,error)
        if error:
            self.character_status.setText(self.status.text())
