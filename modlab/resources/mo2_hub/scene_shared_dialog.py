"""Grouped appearance decisions shown during scenery preparation."""
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
                            QPushButton, QScrollArea, QWidget)

from .guidance import display_name
from .scene_choices import GROUPS
from .scene_shared import group_conflicts, choose_groups


class SharedAppearanceDialog(QDialog):
    def __init__(self, conflicts, previous=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('ModLab — Choose shared scenery appearances')
        self.resize(740, 560)
        self.groups = group_conflicts(conflicts)
        self.resolutions = None
        layout = QVBoxLayout(self)
        note = QLabel('Some of your chosen scenery parts use the same textures. Choose the appearance for each group below. '
            'The choice affects all listed parts; other files keep your component selections. '
            'Different textures alone do not mean the mods are incompatible.')
        note.setWordWrap(True); layout.addWidget(note)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        content = QWidget(); rows = QVBoxLayout(content); scroll.setWidget(content); layout.addWidget(scroll)
        self.combos = []
        previous = previous or {}
        for group in self.groups:
            title = ' + '.join(GROUPS.get(key, 'Other scenery') for key in group['groups'])
            label = QLabel(title); label.setWordWrap(True); rows.addWidget(label)
            combo = QComboBox(); combo.addItem('Choose the appearance to keep…', None)
            for provider, kind in group['options']:
                prefix = 'Keep installed appearance — ' if kind == 'installed' else 'Use shared textures from '
                combo.addItem(prefix + display_name(provider), (provider, kind))
            # Reuse only choices whose exact evidence still matches every texture.
            for option in group['options']:
                selected = choose_groups([group], [option])
                if all(previous.get(name) == value for name, value in selected.items()):
                    combo.setCurrentIndex(combo.findData(option)); break
            combo.setToolTip('\n'.join(item['path'] for item in group['textures']))
            rows.addWidget(combo); self.combos.append(combo)
            detail = QLabel(str(len(group['textures'])) + ' shared textures. Their source files remain installed.')
            detail.setWordWrap(True); rows.addWidget(detail)
        rows.addStretch()
        self.status = QLabel('Your previous scenery output stays in place until these choices pass preparation.')
        self.status.setWordWrap(True); layout.addWidget(self.status)
        buttons = QHBoxLayout(); layout.addLayout(buttons)
        apply = QPushButton('Apply choices and continue'); apply.clicked.connect(self.save); buttons.addWidget(apply)
        cancel = QPushButton('Keep previous output'); cancel.clicked.connect(self.reject); buttons.addWidget(cancel)

    def save(self):
        try:
            self.resolutions = choose_groups(self.groups, [combo.currentData() for combo in self.combos])
            self.accept()
        except ValueError as error:
            self.status.setText(str(error))
