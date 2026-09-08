"""Choose NPC appearances by character, without exposing patcher configuration."""
import json
from PyQt6.QtWidgets import (QApplication, QComboBox, QDialog, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)
from . import npc_workflow as workflow


from .dialog_workflow import capture_dialog, begin_apply, end_apply
from .operation_dialog import OperationDialog

class NpcDialog(OperationDialog):
    def __init__(self, organizer, parent=None):
        super().__init__(parent)
        self.organizer = organizer
        capture_dialog(self)
        self.profile_path = organizer.profilePath()
        self.rows = workflow.catalog(organizer)
        pending = workflow.load(organizer, 'pending')
        self.pending_snapshot=pending
        self.applied_selection = dict((workflow.load(organizer) or {}).get('selected',{}))
        saved = pending or workflow.load(organizer) or {}
        self.selected = dict(saved.get('selected', {}))
        self.body_choices=dict(saved.get('body_choices',{}))
        self.applied_body_choices=dict((workflow.load(organizer) or {}).get('body_choices',{}))
        self.body_labels=dict(saved.get('labels',{}))
        self.persisted_request=dict(selected=dict(self.selected),body_choices=dict(self.body_choices))
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

    def report_status(self,text):
        self.status.setText(text)
        if hasattr(self,'character_status'): self.character_status.setText(text)

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
            self.report_status('Selected profile changed. Reopen choices.'); return
        path = workflow.path_for(self.organizer, 'pending')
        try: workflow.check_pending(self.organizer,self.pending_snapshot)
        except ValueError as error: self.report_status(str(error));return
        if path.exists(): path.unlink()
        self.pending_snapshot=None
        self.persisted_request=dict(selected=dict(self.selected),body_choices=dict(self.body_choices))
        self.preferences_changed = True
        self.accept()

    def generate(self):
        try:
            from .npc import validate_selections
            if self.selected: validate_selections(self.rows, self.selected)
            elif not self.body_choices and not self.applied_selection and not self.applied_body_choices:
                raise ValueError('Choose a character appearance or body before preparing.')
            begin_apply(self, self.generate_confirmed)
        except Exception as error:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, 'Preparation needs attention', str(error))

    def generate_confirmed(self):
        if self.organizer.profilePath() != self.profile_path:
            self.report_status('Selected profile changed. Reopen choices.'); return
        if not self.selected and not self.body_choices:
            from .npc_reset import reset_choices
            self.awaiting_publication = True
            try:
                self.write_pending()
                reset_choices(self.organizer,self.ready)
            except Exception as error: self.ready(None,str(error))
            return
        self.build.setEnabled(False); self.close_button.setEnabled(False); self.table.setEnabled(False)
        self.apply_visible.setEnabled(False); self.cancel.setEnabled(False)
        self.report_status('Checking selected body/skin variants and appearance sources, then preparing character records and paired assets…')
        QApplication.processEvents()
        try:
            self.write_pending()
            self.job = workflow.prepare_job(self.organizer, self.selected,self.body_choices,self.body_labels)
            workflow.run_job(self.organizer, self.job)
            self.awaiting_publication = True
            workflow.publish_job(self.organizer, self.job, self.ready)
        except Exception as error: self.ready(None, str(error))

    def ready(self, target, error):
        end_apply(self)
        if error:
            try: self.write_pending(error)
            except (OSError,ValueError) as changed: error=str(error)+'\n'+str(changed)
            self.report_status(error + '\nPrevious output is retained. Resolve this problem and reopen choices to retry.')
            self.close_button.setEnabled(True); self.cancel.setEnabled(True); self.cancel.setVisible(True)
        else:
            self.applied = True
            self.accept()

    def write_pending(self,error=None):
        if self.organizer.profilePath()!=self.profile_path: raise ValueError('The selected profile changed. Reopen character choices.')
        workflow.check_pending(self.organizer,self.pending_snapshot)
        request=dict(selected=dict(self.selected),body_choices=dict(self.body_choices))
        payload=dict(request,labels=self.body_labels)
        if error: payload['error']=error
        path=workflow.path_for(self.organizer,'pending');temporary=path.with_suffix('.tmp')
        temporary.write_text(json.dumps(payload,indent=2),encoding='utf-8');temporary.replace(path)
        self.pending_snapshot=payload
        self.persisted_request=request

    def done(self,result):
        if self.operation_running: return
        request=dict(selected=dict(self.selected),body_choices=dict(self.body_choices))
        if not self.applied and request!=self.persisted_request:
            try: self.write_pending();self.preferences_changed=True
            except (OSError,ValueError) as error:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(self,'Character choices were not saved',str(error))
        super().done(result)
