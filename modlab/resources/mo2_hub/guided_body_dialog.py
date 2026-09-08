"""Beginner body/shape/outfit choices over the existing verified build workflow."""
from pathlib import Path
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QScrollArea, QTabWidget, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget)

from .body_dialog import BodyDialog
from .body_customization_dialog import CustomizationActions
from .body_choices import (shared_bodies, compatible_presets, choice_groups,
    select_projects, load_defaults, save_default, verified_default, selected_morph_mode,
    outfit_batch, outfit_batches)
from .guidance import display_name
from .body_drafts import load_drafts, save_draft, choice_fields, missing_decisions


def note(text):
    widget = QLabel(text); widget.setWordWrap(True)
    return widget


class GuidedBodyDialog(CustomizationActions, BodyDialog):
    def __init__(self, organizer, job, parent=None, review_names=None):
        super().__init__(organizer, job, parent, review_names)
        self.setWindowTitle('ModLab — Bodies & outfits')
        self.resize(1080, 820)
        self.pending_default = None
        self.shape_recipe = None
        self.shape_record = None
        self.shape_defaults_published=False
        self.defaults = load_defaults(organizer.profilePath())
        self.drafts = load_drafts(self.profile_path)
        self.editing_sex = None
        from .body_ownership import active_outfit_models
        self.outfit_error = None
        try:
            self.outfit_paths_by_sex = {sex: active_outfit_models(organizer, sex) for sex in ('female', 'male')}
        except (ValueError, OSError) as error:
            self.outfit_paths_by_sex = {'female': set(), 'male': set()}
            self.outfit_error = str(error)
        root = self.layout()
        advanced = QWidget(); advanced_layout = QVBoxLayout(advanced)
        while root.count():
            item = root.takeAt(0)
            if item.widget(): advanced_layout.addWidget(item.widget())
            elif item.layout(): advanced_layout.addLayout(item.layout())
            else: advanced_layout.addItem(item)
        self.tabs = QTabWidget(); root.addWidget(self.tabs)
        self.guided = QWidget(); layout = QVBoxLayout(self.guided)
        layout.setSpacing(12)
        layout.addWidget(note('Choose a shared body and shape, then prepare matching outfits. '
            'This changes characters using the shared body. Outfits are identified from the active game records; skin meshes are kept out of the outfit batch.'))
        form = QFormLayout()
        self.sex_choice = QComboBox(); self.sex_choice.addItem('Female characters', 'female'); self.sex_choice.addItem('Male characters', 'male')
        form.addRow('Who uses this body?', self.sex_choice)
        self.body_choice = QComboBox(); form.addRow('Body', self.body_choice)
        self.shape_choice = QComboBox(); form.addRow('Shape preset', self.shape_choice)
        self.shape_support = QComboBox()
        self.shape_support.addItem('Keep existing setting', None)
        self.shape_support.addItem('Include body shape data', True)
        self.shape_support.addItem('Static body only', False)
        self.shape_support.setToolTip('Shape data is used by RaceMenu and body distribution helpers. Preparing it does not assign a preset to individual characters.')
        form.addRow('In-game shape support', self.shape_support)
        layout.addLayout(form)
        self.customize_button = QPushButton('Customize this shape in BodySlide...')
        self.customize_button.clicked.connect(self.customize_shared)
        layout.addWidget(self.customize_button)
        self.body_note = note(''); layout.addWidget(self.body_note)
        self.pending_note = note(''); layout.addWidget(self.pending_note)
        self.outfit_choice = QCheckBox('Prepare compatible installed outfits with this shape')
        self.outfit_choice.setChecked(not self.outfit_error); self.outfit_choice.setEnabled(not self.outfit_error)
        layout.addWidget(self.outfit_choice)
        self.individual_shapes=QCheckBox('Keep this shared shape and allow individual character shapes (OBody)')
        self.individual_shapes.setToolTip('Optional. ModLab prepares the author’s neutral body and matching outfits, then applies this shared shape and your saved character exceptions together. Separate static NPC bodies and skins are retained.')
        layout.addWidget(self.individual_shapes)
        self.individual_shapes.toggled.connect(self.individual_changed)
        if self.outfit_error:
            layout.addWidget(note('Outfit matching needs attention: ' + self.outfit_error +
                ' Shared body choices and Advanced projects remain available. No outfits will be selected automatically.'))
        self.batch_row=QWidget(); batch=QHBoxLayout(self.batch_row);batch.setContentsMargins(0,0,0,0)
        self.batch_choice=QComboBox();batch.addWidget(self.batch_choice,1)
        self.batch_apply=QPushButton('Use this outfit group');self.batch_apply.clicked.connect(self.apply_outfit_batch)
        batch.addWidget(self.batch_apply);layout.addWidget(self.batch_row)
        self.batch_choice.setToolTip('Use an author-provided group to choose several matching outfit variants together. '
            'Only outfits with exactly one matching variant change. You can still change each outfit below.')
        self.parts = QTreeWidget(); self.parts.setColumnCount(3)
        self.parts.setHeaderLabels(['Body part or outfit', 'Use', 'From mod'])
        self.parts.setColumnWidth(0, 265); self.parts.setColumnWidth(1, 385)
        self.parts.setMinimumHeight(220); layout.addWidget(self.parts, 1)
        self.choice_note = note(''); layout.addWidget(self.choice_note)
        self.guided_status = note('Your current files stay in place until you choose Prepare and apply. '
            'Physics variants still need their skeleton and physics dependencies; sharing a preset is only a BodySlide build match.')
        layout.addWidget(self.guided_status)
        actions = QHBoxLayout()
        self.guided_build = QPushButton('Prepare and apply'); self.guided_build.clicked.connect(self.prepare_guided)
        actions.addWidget(self.guided_build)
        close = QPushButton('Close'); close.clicked.connect(self.accept); actions.addWidget(close)
        layout.addLayout(actions)
        self.guided_scroll=QScrollArea();self.guided_scroll.setWidgetResizable(True)
        self.guided_scroll.setWidget(self.guided)
        self.tabs.addTab(self.guided_scroll, 'Body & outfit choices')
        self.tabs.addTab(advanced, 'Advanced projects')
        self.sex_choice.currentIndexChanged.connect(self.sex_changed)
        self.body_choice.currentIndexChanged.connect(self.body_changed)
        self.shape_choice.currentIndexChanged.connect(self.refresh_choices)
        self.outfit_choice.toggled.connect(self.refresh_choices)
        self.status.textChanged.connect(lambda: self.guided_status.setText(self.status.toPlainText()))
        self.sex_changed()

    def draft_choice(self):
        body,preset=self.body_choice.currentData(),self.shape_choice.currentData()
        prior=self.drafts.get(self.editing_sex,self.defaults.get(self.editing_sex,{}))
        decisions=dict(prior.get('decisions',{})) if prior.get('body')==body and prior.get('preset')==preset else {}
        decisions.update({key:combo.currentData() for key,combo in self.decisions.items()})
        result=dict(body=body,preset=preset,
            decisions=decisions,
            outfits=self.outfit_choice.isChecked(),shape_support=self.shape_support.currentData())
        if self.individual_shapes.isChecked():result['individual_shapes']=True
        return result

    def individual_changed(self, enabled):
        if enabled:self.shape_support.setCurrentIndex(self.shape_support.findData(True))
        self.shape_support.setEnabled(not enabled)

    def save_pending_choice(self):
        if not self.editing_sex: return
        if self.organizer.profilePath()!=self.profile_path:
            raise ValueError('The selected profile changed. Reopen body choices before saving.')
        sex=self.editing_sex; choice=self.draft_choice()
        prepared=choice_fields(self.defaults.get(sex,{}))
        # Untouched browsing does not rewrite files or mark outputs as changed.
        desired=None if choice==prepared or not choice['body'] else choice
        if desired==self.drafts.get(sex): return
        save_draft(self.profile_path,sex,desired,expected=self.drafts.get(sex))
        if desired is None: self.drafts.pop(sex,None)
        else: self.drafts[sex]=desired

    def done(self,result):
        if self.operation_running: return
        try: self.save_pending_choice()
        except (ValueError,OSError) as error:
            QMessageBox.warning(self,'Pending choices could not be saved',str(error)+' These edits were not saved. Previously saved choices are retained; reopen this screen to continue.')
        super().done(result)

    def sex_changed(self):
        try: self.save_pending_choice()
        except (ValueError,OSError) as error:
            self.sex_choice.blockSignals(True)
            self.sex_choice.setCurrentIndex(self.sex_choice.findData(self.editing_sex))
            self.sex_choice.blockSignals(False)
            QMessageBox.warning(self,'Pending choices could not be saved',str(error)); return
        sex = self.sex_choice.currentData()
        self.editing_sex=sex
        self.outfit_paths = self.outfit_paths_by_sex[sex]
        self.body_choice.blockSignals(True); self.body_choice.clear()
        self.body_choice.addItem('Choose an installed body…', None)
        for name in shared_bodies(self.job.catalog, sex):
            self.body_choice.addItem(name, name)
        saved = self.drafts.get(sex, self.defaults.get(sex, {}))
        if saved.get('body') and self.body_choice.findData(saved['body'])<0:
            self.body_choice.addItem(saved['body']+' (unavailable)',saved['body'])
        index = self.body_choice.findData(saved.get('body'))
        self.body_choice.setCurrentIndex(max(index, 0)); self.body_choice.blockSignals(False)
        self.outfit_choice.blockSignals(True)
        self.outfit_choice.setChecked(bool(saved.get('outfits', True)) and not self.outfit_error); self.outfit_choice.blockSignals(False)
        self.shape_support.setCurrentIndex(max(0,self.shape_support.findData(saved.get('shape_support'))))
        self.individual_shapes.setChecked(bool(saved.get('individual_shapes')))
        self.individual_changed(self.individual_shapes.isChecked())
        self.decision_context=None
        self.body_changed()

    def body_changed(self):
        body = self.body_choice.currentData()
        saved = self.drafts.get(self.sex_choice.currentData(), self.defaults.get(self.sex_choice.currentData(), {}))
        self.shape_choice.blockSignals(True); self.shape_choice.clear()
        self.shape_choice.addItem('Choose a shape…', None)
        available = body in shared_bodies(self.job.catalog,self.sex_choice.currentData())
        for preset in compatible_presets(self.job.catalog, body) if available else ():
            self.shape_choice.addItem(preset, preset)
        if saved.get('body')==body and saved.get('preset') and self.shape_choice.findData(saved['preset'])<0:
            self.shape_choice.addItem(saved['preset']+' (unavailable)',saved['preset'])
        self.shape_choice.setCurrentIndex(max(0, self.shape_choice.findData(saved.get('preset'))))
        self.shape_choice.blockSignals(False)
        if available:
            self.body_note.setText('Body supplied by ' + display_name(self.job.record['project_sources'][body]) +
                '. Hands, feet and outfit alternatives are shown below. Keep current files is available for each part.')
        else:
            self.body_note.setText('Select the body you want to prepare.' if self.body_choice.count() > 1 else
                'No shared BodySlide body for this group is installed. Current bodies stay in place. '
                'Install a matching body with BodySlide files to build a shape; other projects remain available in Advanced.')
        self.refresh_choices()

    def refresh_choices(self, *, carried_decisions=None):
        previous = {key: combo.currentData() for key, combo in getattr(self, 'decisions', {}).items()}
        context = (self.body_choice.currentData(), self.shape_choice.currentData())
        if getattr(self, 'decision_context', None) != context:
            previous = {}
        if carried_decisions is not None:
            # Keys describe output-path groups, not preset names. Existing
            # availability checks retain incompatible selections for review.
            previous = dict(carried_decisions)
        self.decision_context = context
        self.parts.clear(); self.decisions = {}
        body, preset = context
        available = body in shared_bodies(self.job.catalog,self.sex_choice.currentData())
        available = available and preset in compatible_presets(self.job.catalog,body)
        self.guided_build.setEnabled(available)
        self.customize_button.setEnabled(available)
        self.pending_note.setText('Pending choices restored; not applied. Review them, then choose Prepare and apply.' if self.sex_choice.currentData() in self.drafts else 'Selections are kept when you close. Prepare and apply changes the installed body and outfits.')
        if not available:
            self.batch_row.setVisible(False)
            self.choice_note.setText('A saved body or shape is unavailable. Enable its source mod or choose an installed alternative. Current files are retained.' if body and preset else 'Choose a body and shape to see the matching parts and outfits.')
            return
        saved = self.drafts.get(self.sex_choice.currentData(), self.defaults.get(self.sex_choice.currentData(), {}))
        saved_decisions = saved.get('decisions', {}) if saved.get('body') == body and saved.get('preset') == preset else {}
        groups = choice_groups(self.job.catalog, body, preset, self.saved, outfit_paths=self.outfit_paths)
        parents = {}; total = 0; ambiguous = 0
        for group in groups:
            if group.kind == 'outfit' and not self.outfit_choice.isChecked():
                continue
            section = 'Hands, feet & first-person body' if group.kind == 'body part' else 'Outfits'
            if section not in parents:
                parents[section] = QTreeWidgetItem(self.parts, [section]); parents[section].setExpanded(True)
            row = QTreeWidgetItem(parents[section], [group.label])
            combo = QComboBox(); combo.addItem('Choose a variant…', None)
            combo.addItem('Keep current files', '')
            for name in group.options:
                combo.addItem(name, name)
            choice = previous.get(group.key, saved_decisions.get(group.key, group.suggested))
            if choice and combo.findData(choice)<0: combo.addItem(choice+' (unavailable)',choice)
            combo.setCurrentIndex(max(0, combo.findData(choice)))
            self.parts.setItemWidget(row, 1, combo); self.decisions[group.key] = combo
            sources = sorted({display_name(self.job.record['project_sources'][name]) for name in group.options})
            row.setText(2, '\n'.join(sources))
            row.setToolTip(0, '\n'.join(group.options))
            row.setToolTip(2, '\n'.join(self.job.record['project_sources'][name] for name in group.options))
            total += group.kind == 'outfit'; ambiguous += len(group.options) > 1
        # A removed group must not silently disappear from the pending request.
        remembered=dict(saved_decisions); remembered.update(previous)
        self.unavailable_decisions=missing_decisions(remembered,{group.key for group in groups})
        for key,value in self.unavailable_decisions.items():
            row=QTreeWidgetItem(self.parts,['Unavailable saved choice'])
            combo=QComboBox(); combo.addItem(value+' (unavailable)',value)
            combo.addItem('Keep current files; clear this pending choice','')
            self.parts.setItemWidget(row,1,combo); self.decisions[key]=combo
            row.setText(2,'Enable the source mod, or clear this pending choice.')
        self.choice_note.setText(f'{total} matching outfit choices. {ambiguous} parts/outfits have alternatives. '
            'Only one variant in each overlapping group is prepared here; Advanced retains individual project combinations. '
            'Projects without a declared match remain available in Advanced.')
        previous_group=self.batch_choice.currentData();self.batch_choice.clear()
        self.batch_choice.addItem('Choose several outfit variants together…',None)
        for name,count in outfit_batches(self.job.catalog,groups):
            self.batch_choice.addItem(f'{name} — {count} outfit choices',name)
        if previous_group and self.batch_choice.findData(previous_group)>=0:
            self.batch_choice.setCurrentIndex(self.batch_choice.findData(previous_group))
        self.batch_row.setVisible(self.outfit_choice.isChecked() and self.batch_choice.count()>1)

    def apply_outfit_batch(self):
        group=self.batch_choice.currentData()
        if not group or not self.outfit_choice.isChecked():return
        groups=choice_groups(self.job.catalog,self.body_choice.currentData(),self.shape_choice.currentData(),
            self.saved,outfit_paths=self.outfit_paths)
        changed=outfit_batch(self.job.catalog,groups,group)
        for key,value in changed.items():
            combo=self.decisions[key];combo.setCurrentIndex(combo.findData(value))
        self.guided_status.setText(f'Chose {group} for {len(changed)} matching outfits. '
            'Other choices are retained. You can adjust any outfit below, then choose Prepare and apply.')

    def prepare_guided(self):
        self.shape_recipe=None;self.shape_record=None;self.shape_defaults_published=False
        try:
            body, preset = self.body_choice.currentData(), self.shape_choice.currentData()
            decisions = {key: combo.currentData() for key, combo in self.decisions.items()}
            if any(decisions.get(key) for key in getattr(self,'unavailable_decisions',{})):
                raise ValueError('A saved body or outfit choice is unavailable. Enable its source mod or clear that pending choice before preparing.')
            self.save_pending_choice()
            selected = select_projects(self.job.catalog, body, preset, decisions,
                outfits=self.outfit_choice.isChecked(), saved=self.saved, outfit_paths=self.outfit_paths)
            morphs = selected_morph_mode(selected, self.saved, self.shape_support.currentData())
            self.pending_default = dict(sex=self.sex_choice.currentData(), body=body, preset=preset,
                decisions=decisions, outfits=self.outfit_choice.isChecked(), selected=selected,
                shape_support=self.shape_support.currentData())
            if self.individual_shapes.isChecked():
                from .characters import active_characters
                from .shape_preparation import prepare_request
                self.guided_status.setText('Checking the shared shape, character exceptions and optional helper before changing any bodies…')
                self.shape_recipe=prepare_request(self.organizer,self.job.catalog,self.pending_default,
                    active_characters(self.organizer,{},include_races=True))
                self.pending_default=self.shape_recipe['request'];morphs=True
            elif Path(self.organizer.resolvePath('SKSE/Plugins/OBody.dll') or '').is_file():
                raise ValueError('OBody is enabled. Choose “Keep this shared shape and allow individual character shapes” so the body is not shaped twice, or disable OBody before preparing a static body.')
            build_preset=self.pending_default.get('build_preset',preset)
            self.preset.setCurrentIndex(self.preset.findData(build_preset))
            for item in self.items():
                item.setCheckState(Qt.CheckState.Checked if item.text() in selected else Qt.CheckState.Unchecked)
            self.morphs.setChecked(morphs)
            BodyDialog.generate(self)
            if not self.operation_running and not self.applied:
                self.pending_default = None
        except Exception as error:
            self.pending_default = None
            QMessageBox.warning(self, 'Choose how to prepare this body', str(error))

    def generate(self):
        # Advanced selections must never silently rewrite the shared-body preference.
        self.pending_default = None
        self.shape_recipe=None
        BodyDialog.generate(self)

    def generate_confirmed(self):
        if self.shape_recipe:
            from .shape_preparation import mark_pending
            mark_pending(self.profile_path,dict(status='Building bodies and outfits',preflight=self.shape_recipe['directory']))
        BodyDialog.generate_confirmed(self)
        if self.shape_recipe and not self.awaiting_publication and not self.applied:
            self.shapes_failed(self.status.toPlainText())

    def install_output(self):
        if self.pending_default and (set(self.pending_default['selected']) == set(self.job.record.get('projects', ()))
                and self.pending_default.get('build_preset',self.pending_default['preset']) == self.job.record.get('preset')):
            self.pending_default['build_record'] = str(self.job.directory / 'operation.json')
        if self.shape_recipe:
            from .shape_preparation import mark_pending,upstream,check_archive_inputs
            from .character_shapes import load_choices
            state=load_choices(self.profile_path)
            if state!=self.shape_recipe['choices']:
                raise ValueError('Character choices changed during the body build. The new output was not applied.')
            if load_defaults(self.profile_path)!=self.shape_recipe['defaults_before']:
                raise ValueError('Shared body choices changed during the body build. The new output was not applied.')
            current,checksum,_=upstream(self.organizer,state)
            if str(current)!=self.shape_recipe['source'] or checksum!=self.shape_recipe['source_sha256']:
                raise ValueError('The shape configuration changed during the body build. The new output was not applied.')
            check_archive_inputs(self.organizer,self.shape_recipe['plan']['archive_inputs'])
            mark_pending(self.profile_path,dict(status='Applying bodies, then shape assignments',body_job=str(self.job.directory),preflight=self.shape_recipe['directory']))
        BodyDialog.install_output(self)

    def output_ready(self,target,error):
        if self.shape_recipe and error:
            self.installed=target;self.shapes_failed(error);return
        BodyDialog.output_ready(self,target,error)

    def check_effective(self):
        BodyDialog.check_effective(self)
        if verified_default(self.pending_default, self.job.record, self.job.directory, self.applied):
            try:
                choice = dict(self.pending_default); sex = choice.pop('sex')
                choice['build_record'] = str(self.job.directory / 'operation.json')
                if self.shape_recipe:
                    from .characters import active_characters
                    from .character_shapes import apply_choices
                    self.applied=False;self.operation_running=True;self.awaiting_publication=True;self.setEnabled(False)
                    self.shape_choice_to_save=(sex,choice)
                    defaults=load_defaults(self.profile_path);defaults[sex]=choice
                    self.guided_status.setText('Bodies and outfits are checked. Applying the shared shape and saved character exceptions…')
                    apply_choices(self.organizer,active_characters(self.organizer,{},include_races=True),self.job.catalog,
                        self.shapes_ready,prepared_defaults=defaults,on_prepared=lambda path:setattr(self,'shape_record',path))
                    return
                save_default(self.profile_path, sex, choice)
                self.defaults = load_defaults(self.profile_path)
                self.pending_default = None
                self.save_pending_choice()
                self.pending_note.setText('Applied choices. Changes you make here are saved as pending until you prepare again.')
                self.guided_status.setText('Prepared and applied. Your shared body and shape are saved for this profile. '
                    'Generated files were checked and are effective. NPC body assignments were not changed. '
                    'You can change these choices here at any time.')
            except Exception as error:
                if self.shape_recipe:self.shapes_failed(str(error));return
                self.guided_status.setText('Output was applied, but the body default could not be saved: ' + str(error))
        elif self.shape_recipe:
            self.shapes_failed(self.status.toPlainText())

    def shapes_ready(self,target,error):
        if error:self.shapes_failed(error);return
        from .dialog_workflow import end_apply
        from .shape_preparation import pending_path
        try:
            sex,choice=self.shape_choice_to_save
            if load_defaults(self.profile_path)!=self.shape_recipe['defaults_before']:
                raise ValueError('Shared body choices changed during assignment. Review them before preparing again.')
            save_default(self.profile_path,sex,choice)
            self.shape_defaults_published=True
            self.defaults=load_defaults(self.profile_path)
            self.job.record.update(shape_config_record=str(self.shape_record),status='Body, outfits and shape assignments installed and effective')
            self.job.save();pending_path(self.profile_path).unlink(missing_ok=True)
            self.pending_default=None;self.applied=True
            self.guided_status.setText('Prepared and applied: the shared shape and saved character exceptions are effective. '
                'Separate static NPC bodies and skins were retained. Existing saves may need OBody’s assignment reset; visual checks remain in game.')
        except Exception as problem:
            self.shapes_failed(str(problem));return
        self.shape_recipe=None
        try:self.save_pending_choice()
        except Exception as problem:self.guided_status.setText('The body and shapes are applied, but clearing the saved draft needs attention: '+str(problem))
        self.pending_note.setText('Applied choices. Your shared shape and character exceptions are saved for this profile.')
        end_apply(self)

    def shapes_failed(self,error):
        from .shape_preparation import restore_attempt,mark_pending
        from .dialog_workflow import end_apply
        from .character_shapes import load_choices,_write,TOOL
        from .outputs import output_name
        from pathlib import Path
        recovery=[]
        for target,directory,tool in ((self.installed,self.job.directory,'BodySlide'),
                (Path(self.organizer.modsPath())/output_name(self.organizer.profile().name(),self.profile_path,TOOL),
                 Path(self.shape_record).parent if self.shape_record else None,TOOL)):
            if target is None or directory is None:continue
            try:
                if restore_attempt(target,directory,self.profile_path,tool):recovery.append(tool+': previous files restored')
            except Exception as problem:recovery.append(tool+': recovery needs attention — '+str(problem))
        try:
            state=load_choices(self.profile_path)
            if self.shape_recipe and (state.get('applied') or {}).get('record_path')==str(self.shape_record):
                _write(self.profile_path,self.shape_recipe['choices'],state)
        except Exception as problem:recovery.append('Saved choices: '+str(problem))
        if self.shape_defaults_published and self.shape_recipe:
            try:
                import json,os
                from .body_choices import defaults_path
                expected=dict(self.shape_recipe['defaults_before']);sex,choice=self.shape_choice_to_save;expected[sex]=choice
                if load_defaults(self.profile_path)!=expected:raise ValueError('Shared defaults were edited outside this operation.')
                path=defaults_path(self.profile_path);temporary=path.with_suffix('.tmp')
                temporary.write_text(json.dumps(dict(profile_path=self.profile_path,defaults=self.shape_recipe['defaults_before']),indent=2),encoding='utf-8')
                os.replace(temporary,path);self.defaults=load_defaults(self.profile_path)
            except Exception as problem:recovery.append('Shared defaults: '+str(problem))
        mark_pending(self.profile_path,dict(status='Preparation needs attention',error=str(error),
            body_job=str(self.job.directory),shape_job=str(self.shape_record),
            preflight=(self.shape_recipe or {}).get('directory'),recovery=recovery))
        self.applied=False;end_apply(self)
        self.guided_status.setText('Preparation did not finish: '+str(error)+'\n'+'\n'.join(recovery)+
            '\nYour choices are retained. Review the named requirement, then Prepare and apply again. Launch remains stopped until preparation is resolved.')
        self.organizer.refresh()
