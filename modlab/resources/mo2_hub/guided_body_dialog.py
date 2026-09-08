"""Beginner body/shape/outfit choices over the existing verified build workflow."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QScrollArea, QTabWidget, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget)

from .body_dialog import BodyDialog
from .body_customization_dialog import CustomizationActions
from .body_choices import (shared_bodies, compatible_presets, choice_groups,
    select_projects, load_defaults, save_default, verified_default, selected_morph_mode)
from .guidance import display_name


def note(text):
    widget = QLabel(text); widget.setWordWrap(True)
    return widget


class GuidedBodyDialog(CustomizationActions, BodyDialog):
    def __init__(self, organizer, job, parent=None, review_names=None):
        super().__init__(organizer, job, parent, review_names)
        self.setWindowTitle('ModLab — Bodies & outfits')
        self.resize(1080, 820)
        self.pending_default = None
        self.defaults = load_defaults(organizer.profilePath())
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
        self.outfit_choice = QCheckBox('Prepare compatible installed outfits with this shape')
        self.outfit_choice.setChecked(not self.outfit_error); self.outfit_choice.setEnabled(not self.outfit_error)
        layout.addWidget(self.outfit_choice)
        if self.outfit_error:
            layout.addWidget(note('Outfit matching needs attention: ' + self.outfit_error +
                ' Shared body choices and Advanced projects remain available. No outfits will be selected automatically.'))
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
        self.tabs.addTab(self.guided, 'Body & outfit choices')
        self.tabs.addTab(advanced, 'Advanced projects')
        self.sex_choice.currentIndexChanged.connect(self.sex_changed)
        self.body_choice.currentIndexChanged.connect(self.body_changed)
        self.shape_choice.currentIndexChanged.connect(self.refresh_choices)
        self.outfit_choice.toggled.connect(self.refresh_choices)
        self.status.textChanged.connect(lambda: self.guided_status.setText(self.status.toPlainText()))
        self.sex_changed()

    def sex_changed(self):
        sex = self.sex_choice.currentData()
        self.outfit_paths = self.outfit_paths_by_sex[sex]
        self.body_choice.blockSignals(True); self.body_choice.clear()
        self.body_choice.addItem('Choose an installed body…', None)
        for name in shared_bodies(self.job.catalog, sex):
            self.body_choice.addItem(name, name)
        saved = self.defaults.get(sex, {})
        index = self.body_choice.findData(saved.get('body'))
        self.body_choice.setCurrentIndex(max(index, 0)); self.body_choice.blockSignals(False)
        self.outfit_choice.blockSignals(True)
        self.outfit_choice.setChecked(bool(saved.get('outfits', True)) and not self.outfit_error); self.outfit_choice.blockSignals(False)
        self.body_changed()

    def body_changed(self):
        body = self.body_choice.currentData()
        saved = self.defaults.get(self.sex_choice.currentData(), {})
        self.shape_choice.blockSignals(True); self.shape_choice.clear()
        self.shape_choice.addItem('Choose a shape…', None)
        for preset in compatible_presets(self.job.catalog, body) if body else ():
            self.shape_choice.addItem(preset, preset)
        self.shape_choice.setCurrentIndex(max(0, self.shape_choice.findData(saved.get('preset'))))
        self.shape_choice.blockSignals(False)
        if body:
            self.body_note.setText('Body supplied by ' + display_name(self.job.record['project_sources'][body]) +
                '. Hands, feet and outfit alternatives are shown below. Keep current files is available for each part.')
        else:
            self.body_note.setText('Select the body you want to prepare.' if self.body_choice.count() > 1 else
                'No shared BodySlide body for this group is installed. Current bodies stay in place. '
                'Install a matching body with BodySlide files to build a shape; other projects remain available in Advanced.')
        self.refresh_choices()

    def refresh_choices(self):
        previous = {key: combo.currentData() for key, combo in getattr(self, 'decisions', {}).items()}
        context = (self.body_choice.currentData(), self.shape_choice.currentData())
        if getattr(self, 'decision_context', None) != context:
            previous = {}
        self.decision_context = context
        self.parts.clear(); self.decisions = {}
        body, preset = context
        self.guided_build.setEnabled(bool(body and preset))
        self.customize_button.setEnabled(bool(body and preset))
        if not body or not preset:
            self.choice_note.setText('Choose a body and shape to see the matching parts and outfits.')
            return
        saved = self.defaults.get(self.sex_choice.currentData(), {})
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
            combo.setCurrentIndex(max(0, combo.findData(choice)))
            self.parts.setItemWidget(row, 1, combo); self.decisions[group.key] = combo
            sources = sorted({display_name(self.job.record['project_sources'][name]) for name in group.options})
            row.setText(2, '\n'.join(sources))
            row.setToolTip(0, '\n'.join(group.options))
            row.setToolTip(2, '\n'.join(self.job.record['project_sources'][name] for name in group.options))
            total += group.kind == 'outfit'; ambiguous += len(group.options) > 1
        self.choice_note.setText(f'{total} matching outfit choices. {ambiguous} parts/outfits have alternatives. '
            'Only one variant in each overlapping group is prepared here; Advanced retains individual project combinations. '
            'Projects without a declared match remain available in Advanced.')

    def prepare_guided(self):
        try:
            body, preset = self.body_choice.currentData(), self.shape_choice.currentData()
            decisions = {key: combo.currentData() for key, combo in self.decisions.items()}
            selected = select_projects(self.job.catalog, body, preset, decisions,
                outfits=self.outfit_choice.isChecked(), saved=self.saved, outfit_paths=self.outfit_paths)
            morphs = selected_morph_mode(selected, self.saved, self.shape_support.currentData())
            self.pending_default = dict(sex=self.sex_choice.currentData(), body=body, preset=preset,
                decisions=decisions, outfits=self.outfit_choice.isChecked(), selected=selected)
            self.preset.setCurrentIndex(self.preset.findData(preset))
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
        BodyDialog.generate(self)

    def install_output(self):
        if self.pending_default and (set(self.pending_default['selected']) == set(self.job.record.get('projects', ()))
                and self.pending_default['preset'] == self.job.record.get('preset')):
            self.pending_default['build_record'] = str(self.job.directory / 'operation.json')
        BodyDialog.install_output(self)

    def check_effective(self):
        BodyDialog.check_effective(self)
        if verified_default(self.pending_default, self.job.record, self.job.directory, self.applied):
            try:
                choice = dict(self.pending_default); sex = choice.pop('sex')
                choice['build_record'] = str(self.job.directory / 'operation.json')
                save_default(self.profile_path, sex, choice)
                self.defaults = load_defaults(self.profile_path)
                self.pending_default = None
                self.guided_status.setText('Prepared and applied. Your shared body and shape are saved for this profile. '
                    'Generated files were checked and are effective. NPC body assignments were not changed. '
                    'You can change these choices here at any time.')
            except Exception as error:
                self.guided_status.setText('Output was applied, but the body default could not be saved: ' + str(error))
