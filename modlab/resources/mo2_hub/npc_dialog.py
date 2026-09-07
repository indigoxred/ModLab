"""Choose NPC appearances by character, without exposing patcher configuration."""
import json
from PyQt6.QtWidgets import (QApplication, QComboBox, QDialog, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)
from . import npc_workflow as workflow


class NpcDialog(QDialog):
    def __init__(self, organizer, parent=None):
        super().__init__(parent)
        self.organizer = organizer
        self.profile_path = organizer.profilePath()
        self.rows = workflow.catalog(organizer)
        pending = workflow.load(organizer, 'pending')
        saved = pending or workflow.load(organizer) or {}
        self.selected = dict(saved.get('selected', {}))
        self.setWindowTitle('ModLab — NPC appearance choices'); self.resize(1050, 680)
        layout = QVBoxLayout(self)
        note = QLabel('Choose which installed appearance supplies each character. ModLab keeps its face records, face mesh '
                      'and face tint together, while retaining winning gameplay data and outfits. Other NPCs keep normal load-order behavior. '
                      'Your source mods stay installed. Existing saves can retain old NPC state; verify appearance in a new test game first.')
        note.setWordWrap(True); layout.addWidget(note)
        self.search = QLineEdit(); self.search.setPlaceholderText('Find character, mod or NPC identifier…')
        self.search.textChanged.connect(self.filter); layout.addWidget(self.search)
        bulk = QHBoxLayout(); self.provider = QComboBox()
        providers = sorted({p for row in self.rows.values() for p in row['options']})
        self.provider.addItems(providers); bulk.addWidget(self.provider)
        self.apply_visible = QPushButton('Use this appearance for matching visible characters')
        self.apply_visible.clicked.connect(self.fill_visible); bulk.addWidget(self.apply_visible); layout.addLayout(bulk)
        self.table = QTableWidget(len(self.rows), 3)
        self.table.setHorizontalHeaderLabels(['Character / stable identifier', 'Selected appearance', 'Installed source / requirements'])
        self.keys = sorted(self.rows, key=lambda k: self.rows[k]['label'].casefold())
        self.controls = {}
        for index, key in enumerate(self.keys):
            row = self.rows[key]
            self.table.setItem(index, 0, QTableWidgetItem(row['label'] + '\n' + key))
            combo = QComboBox(); combo.addItem('Use normal load order', '')
            for plugin in row['options']: combo.addItem(plugin, plugin)
            chosen = self.selected.get(key, '')
            if chosen and chosen not in row['options']: combo.addItem(chosen + ' — unavailable', chosen)
            combo.setCurrentIndex(max(0, combo.findData(chosen)))
            combo.currentIndexChanged.connect(lambda _, k=key, c=combo: self.choose(k, c.currentData()))
            self.controls[key] = combo; self.table.setCellWidget(index, 1, combo)
            self.table.setItem(index, 2, QTableWidgetItem('\n'.join(p + ' — ' + o['mod'] +
                ('' if o['ready'] else '\n' + o['problem']) for p, o in row['options'].items())))
            self.table.setRowHeight(index, 57)
        self.table.setColumnWidth(0, 310); self.table.setColumnWidth(1, 235); self.table.setColumnWidth(2, 420)
        layout.addWidget(self.table)
        self.status = QLabel((pending or {}).get('error') or f'{len(self.rows)} characters with installed FaceGen assets. Select appearances, then generate.')
        self.status.setWordWrap(True); layout.addWidget(self.status)
        row = QHBoxLayout()
        self.build = QPushButton('Generate and apply appearances'); self.build.clicked.connect(self.generate); row.addWidget(self.build)
        self.cancel = QPushButton('Cancel pending selection'); self.cancel.setVisible(bool(pending)); self.cancel.clicked.connect(self.cancel_pending); row.addWidget(self.cancel)
        self.close_button = QPushButton('Close'); self.close_button.clicked.connect(self.accept); row.addWidget(self.close_button)
        layout.addLayout(row)

    def choose(self, key, provider):
        if provider: self.selected[key] = provider
        else: self.selected.pop(key, None)

    def filter(self):
        text = self.search.text().casefold()
        for i, key in enumerate(self.keys):
            row = self.rows[key]
            self.table.setRowHidden(i, text not in (key + row['label'] + ' '.join(row['options'])).casefold())

    def fill_visible(self):
        provider = self.provider.currentText()
        for i, key in enumerate(self.keys):
            if not self.table.isRowHidden(i) and provider in self.rows[key]['options']:
                self.controls[key].setCurrentIndex(self.controls[key].findData(provider))

    def cancel_pending(self):
        if self.organizer.profilePath() != self.profile_path:
            self.status.setText('Selected profile changed. Reopen choices.'); return
        path = workflow.path_for(self.organizer, 'pending')
        if path.exists(): path.unlink()
        self.accept()

    def generate(self):
        if self.organizer.profilePath() != self.profile_path:
            self.status.setText('Selected profile changed. Reopen choices.'); return
        if not self.selected:
            self.status.setText('Select at least one character. Existing applied output is retained; removing all output from an existing save needs separate review.'); return
        self.build.setEnabled(False); self.close_button.setEnabled(False); self.table.setEnabled(False)
        self.apply_visible.setEnabled(False); self.cancel.setEnabled(False)
        pending = dict(selected=self.selected)
        workflow.path_for(self.organizer, 'pending').write_text(json.dumps(pending, indent=2), encoding='utf-8')
        self.status.setText('Obtaining the pinned appearance helper if needed, checking inputs, then generating paired records and assets…')
        QApplication.processEvents()
        try:
            self.job = workflow.prepare_job(self.organizer, self.selected)
            workflow.run_job(self.organizer, self.job)
            workflow.publish_job(self.organizer, self.job, self.ready)
        except Exception as error: self.ready(None, str(error))

    def ready(self, target, error):
        if error:
            workflow.path_for(self.organizer, 'pending').write_text(json.dumps(dict(selected=self.selected, error=error), indent=2), encoding='utf-8')
            self.status.setText(error + '\nPrevious output is retained. Resolve this problem and reopen choices to retry.')
            self.close_button.setEnabled(True); self.cancel.setEnabled(True); self.cancel.setVisible(True)
        else: self.accept()
