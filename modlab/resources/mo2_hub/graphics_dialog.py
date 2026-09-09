"""Choose a rendering workflow once; normal rechecks coordinate its helpers."""
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QCheckBox,
                            QPushButton, QTabWidget, QWidget, QFormLayout)

from . import pgpatcher_workflow as workflow
from .pgpatcher import RENDERERS
from . import scene_workflow as scene, background
from .scene_choices import GROUPS
from .guidance import display_name


class GraphicsDialog(QDialog):
    def __init__(self, organizer, parent=None):
        super().__init__(parent)
        self.organizer = organizer
        self.profile_path = organizer.profilePath()
        self.scene_pending = scene.load(organizer, 'pending')
        self.scene_saved = scene.load(organizer) or {}
        self.scene_choices = (self.scene_pending or self.scene_saved).get('choices', {})
        self.scene_available = False
        self.pending = workflow.load_pending(organizer)
        saved = self.pending or workflow.load_choices(organizer) or {}
        choices = saved.get('choices', {})
        self.setWindowTitle('ModLab — Graphics choices')
        self.resize(780, 610)
        layout = QVBoxLayout(self)
        tabs = QTabWidget(); layout.addWidget(tabs)
        scenery = QWidget(); scene_layout = QVBoxLayout(scenery)
        explanation = QLabel('Combine the parts you want: roads from one mod, mountains or trees from another. '
            'Your installed variants and matching patches are retained. Other parts keep their installed setup.')
        explanation.setWordWrap(True); scene_layout.addWidget(explanation)
        form = QFormLayout(); scene_layout.addLayout(form)
        self.components = {}
        for group, title in GROUPS.items():
            combo = QComboBox(); combo.addItem('Reading installed choices…', None); combo.setEnabled(False)
            form.addRow(title, combo); self.components[group] = combo
        scene_layout.addStretch()
        self.scene_note = QLabel('Reading active scenery mods. You can close this panel without applying anything.')
        self.scene_note.setWordWrap(True); scene_layout.addWidget(self.scene_note)
        tabs.addTab(scenery, 'Scenery')
        renderer_page = QWidget(); renderer_layout = QVBoxLayout(renderer_page)
        tabs.addTab(renderer_page, 'Renderer and mesh fixes')
        note = QLabel('Choose the renderer you actually use. ModLab prepares materials and selected mesh fixes after '
            'BodySlide and gameplay patches, then checks the resulting plugin order. It remembers these choices for '
            'later rebuilds. This does not install a renderer or generate distant landscape LOD.')
        note.setWordWrap(True); renderer_layout.addWidget(note)
        self.renderer = QComboBox(); self.renderer.addItem('Choose your graphics setup…', None)
        for name in RENDERERS: self.renderer.addItem(name, name)
        if choices.get('renderer') in RENDERERS:
            self.renderer.setCurrentIndex(RENDERERS.index(choices['renderer']) + 1)
        renderer_layout.addWidget(self.renderer)
        self.pbr = QCheckBox('Use installed TruePBR materials (Community Shaders required)')
        self.lighting = QCheckBox('Apply mesh lighting fixes')
        self.complex = QCheckBox('Upgrade compatible parallax meshes to complex materials')
        for widget, key in ((self.pbr,'pbr'),(self.lighting,'fix_lighting'),(self.complex,'upgrade_complex')):
            widget.setChecked(choices.get(key, False)); renderer_layout.addWidget(widget)
        renderer_layout.addStretch()
        self.status = QLabel((self.pending or {}).get('error', 'Previous output is retained when a build needs attention.'))
        self.status.setWordWrap(True); layout.addWidget(self.status)
        row = QHBoxLayout()
        self.apply = QPushButton('Save choices and finish setup'); self.apply.clicked.connect(self.save_choices)
        self.apply.setEnabled(False); row.addWidget(self.apply)
        if self.pending or self.scene_pending:
            cancel = QPushButton('Cancel pending request'); cancel.clicked.connect(self.cancel_pending); row.addWidget(cancel)
        close = QPushButton('Close'); close.clicked.connect(self.reject); row.addWidget(close)
        layout.addLayout(row)
        try:
            self.context = scene.snapshot(organizer)
            background.run(self, lambda: scene.catalog(self.context), self.scene_loaded)
        except Exception as error:
            self.scene_loaded(None, str(error))

    def scene_loaded(self, assets, error):
        if error:
            self.scene_note.setText('Scenery choices could not be read: ' + error +
                '. Existing scenery choices are retained. Renderer controls remain available on their tab.')
            self.apply.setEnabled(True)
            return
        self.assets = assets
        self.scene_available = True
        for group, combo in self.components.items():
            combo.clear(); combo.addItem('Keep the installed setup', None)
            available = sorted((name for name, provider in assets.providers.items() if group in provider['groups']),
                               key=lambda name: display_name(name).casefold())
            for name in available: combo.addItem(display_name(name), name)
            selected = self.scene_choices.get(group)
            if selected and selected not in available:
                combo.addItem(display_name(selected) + ' — unavailable; choose a replacement', selected)
            combo.setCurrentIndex(max(0, combo.findData(selected)))
            combo.setEnabled(bool(available) or bool(selected))
        self.scene_note.setText('Choose only the parts you want to change. ModLab checks shared textures before applying. '
            'Keeping the installed setup removes that part from ModLab’s scenery override; source mods stay installed. '
            'Distant landscape needs separate LOD preparation.')
        self.apply.setEnabled(True)

    def save_choices(self):
        try:
            if self.organizer.profilePath() != self.profile_path:
                raise ValueError('The selected profile changed; reopen these choices.')
            choices = dict(renderer=self.renderer.currentData(), pbr=self.pbr.isChecked(),
                fix_lighting=self.lighting.isChecked(), upgrade_complex=self.complex.isChecked())
            selected_scene = dict(self.scene_choices)
            if self.scene_available:
                selected_scene = {group: combo.currentData() for group, combo in self.components.items() if combo.currentData()}
                for group, name in selected_scene.items():
                    if name not in self.assets.providers or group not in self.assets.providers[name]['groups']:
                        raise ValueError(GROUPS[group] + ': select an available replacement or keep the installed setup.')
                scene.check_snapshot(self.context, scene.snapshot(self.organizer))
            if choices['renderer'] not in RENDERERS and not (self.scene_available and (selected_scene or self.scene_choices)):
                raise ValueError('Choose the renderer or mesh-only workflow you want.')
            if choices['pbr'] and choices['renderer'] != 'Community Shaders':
                raise ValueError('TruePBR requires Community Shaders.')
            if choices['renderer'] == 'Mesh fixes only' and not choices['fix_lighting']:
                raise ValueError('Select the mesh lighting fix for a mesh-only build.')
            # Save is an explicit confirmation, including reapplying the same
            # choices after an outside change; normal rechecks never infer it.
            if self.scene_available and (selected_scene or self.scene_choices or self.scene_pending):
                scene.begin_pending(self.organizer, selected_scene, expected=self.scene_pending)
            if choices['renderer'] in RENDERERS:
                workflow.begin_pending(self.organizer, choices)
            self.accept()
        except Exception as error:
            self.status.setText(str(error))

    def cancel_pending(self):
        try:
            if self.organizer.profilePath() != self.profile_path:
                raise ValueError('The selected profile changed; reopen these choices.')
            if self.scene_pending:
                scene.check_request(self.organizer, self.scene_pending)
            if self.pending:
                workflow.clear_pending(self.organizer, self.pending['id'])
            if self.scene_pending:
                scene.path_for(self.organizer, 'pending').unlink()
            self.reject()
        except Exception as error:
            self.status.setText(str(error))
