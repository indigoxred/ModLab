"""Choose a rendering workflow once; normal rechecks coordinate its helpers."""
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QCheckBox, QPushButton

from . import pgpatcher_workflow as workflow
from .pgpatcher import RENDERERS


class GraphicsDialog(QDialog):
    def __init__(self, organizer, parent=None):
        super().__init__(parent)
        self.organizer = organizer
        self.profile_path = organizer.profilePath()
        self.pending = workflow.load_pending(organizer)
        saved = self.pending or workflow.load_choices(organizer) or {}
        choices = saved.get('choices', {})
        self.setWindowTitle('ModLab — Graphics choices')
        self.resize(650, 330)
        layout = QVBoxLayout(self)
        note = QLabel('Choose the renderer you actually use. ModLab prepares materials and selected mesh fixes after '
            'BodySlide and gameplay patches, then checks the resulting plugin order. It remembers these choices for '
            'later rebuilds. This does not install a renderer or generate distant landscape LOD.')
        note.setWordWrap(True); layout.addWidget(note)
        self.renderer = QComboBox(); self.renderer.addItem('Choose your graphics setup…', None)
        for name in RENDERERS: self.renderer.addItem(name, name)
        if choices.get('renderer') in RENDERERS:
            self.renderer.setCurrentIndex(RENDERERS.index(choices['renderer']) + 1)
        layout.addWidget(self.renderer)
        self.pbr = QCheckBox('Use installed TruePBR materials (Community Shaders required)')
        self.lighting = QCheckBox('Apply mesh lighting fixes')
        self.complex = QCheckBox('Upgrade compatible parallax meshes to complex materials')
        for widget, key in ((self.pbr,'pbr'),(self.lighting,'fix_lighting'),(self.complex,'upgrade_complex')):
            widget.setChecked(choices.get(key, False)); layout.addWidget(widget)
        self.status = QLabel((self.pending or {}).get('error', 'Previous output is retained when a build needs attention.'))
        self.status.setWordWrap(True); layout.addWidget(self.status)
        row = QHBoxLayout()
        apply = QPushButton('Save choices and finish setup'); apply.clicked.connect(self.save_choices); row.addWidget(apply)
        if self.pending:
            cancel = QPushButton('Cancel pending request'); cancel.clicked.connect(self.cancel_pending); row.addWidget(cancel)
        close = QPushButton('Close'); close.clicked.connect(self.reject); row.addWidget(close)
        layout.addLayout(row)

    def save_choices(self):
        try:
            if self.organizer.profilePath() != self.profile_path:
                raise ValueError('The selected profile changed; reopen these choices.')
            choices = dict(renderer=self.renderer.currentData(), pbr=self.pbr.isChecked(),
                fix_lighting=self.lighting.isChecked(), upgrade_complex=self.complex.isChecked())
            if choices['renderer'] not in RENDERERS:
                raise ValueError('Choose the renderer or mesh-only workflow you want.')
            if choices['pbr'] and choices['renderer'] != 'Community Shaders':
                raise ValueError('TruePBR requires Community Shaders.')
            if choices['renderer'] == 'Mesh fixes only' and not choices['fix_lighting']:
                raise ValueError('Select the mesh lighting fix for a mesh-only build.')
            workflow.begin_pending(self.organizer, choices)
            self.accept()
        except Exception as error:
            self.status.setText(str(error))

    def cancel_pending(self):
        try:
            if self.organizer.profilePath() != self.profile_path:
                raise ValueError('The selected profile changed; reopen these choices.')
            workflow.clear_pending(self.organizer, self.pending['id'])
            self.accept()
        except Exception as error:
            self.status.setText(str(error))
