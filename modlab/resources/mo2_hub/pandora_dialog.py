"""One-time patch choices; later normal rechecks reuse them automatically."""
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QAbstractItemView, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QTextEdit, QVBoxLayout

from .pandora_workflow import load_choices, run_job, publish_job


from .dialog_workflow import capture_dialog, begin_apply, end_apply
from .operation_dialog import OperationDialog

class PandoraDialog(OperationDialog):
    def __init__(self, organizer, job, parent=None):
        super().__init__(parent)
        self.organizer, self.job = organizer, job
        capture_dialog(self)
        self.setWindowTitle('ModLab — Animation behavior choices')
        self.resize(880, 650)
        layout = QVBoxLayout(self)
        note = QLabel('Select the behavior patches required by your installed mods. Drag them to change patch priority '
                      '(later entries win conflicting edits). The Pandora base patch and supported FNIS animation lists '
                      'are handled by the engine. A successful generation still needs an in-game animation check.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.patches = QListWidget()
        self.patches.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        saved = load_choices(organizer) or {}
        selected = saved.get('selected', [])
        order = selected + [p.code for p in job.patches if p.code not in selected]
        lookup = {p.code: p for p in job.patches}
        for code in order:
            if code not in lookup:
                continue
            patch = lookup[code]
            item = QListWidgetItem(f'{patch.name} — {patch.source_mod} [{code}]')
            item.setData(Qt.ItemDataRole.UserRole, code)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if code in selected else Qt.CheckState.Unchecked)
            item.setToolTip(patch.site + '\n' + str(patch.metadata))
            self.patches.addItem(item)
        layout.addWidget(self.patches, 2)
        self.status = QTextEdit()
        self.status.setReadOnly(True)
        self.status.setPlainText('ModLab will remember successful choices and automatically rebuild when animation inputs change. '
                                 'Unselected optional patches remain off. Previous output is retained for recovery.')
        layout.addWidget(self.status, 1)
        row = QHBoxLayout()
        self.build = QPushButton('Generate and apply behaviors')
        self.build.clicked.connect(self.generate)
        row.addWidget(self.build)
        logs = QPushButton('Open build and log')
        logs.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.job.directory or self.job.executable.parent))))
        row.addWidget(logs)
        self.close_button = QPushButton('Close')
        self.close_button.clicked.connect(self.accept)
        row.addWidget(self.close_button)
        layout.addLayout(row)

    def generate(self):
        try:
            from .pandora import selection_entries
            selected = [self.patches.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.patches.count())
                        if self.patches.item(i).checkState() == Qt.CheckState.Checked]
            selection_entries(self.job.patches, selected)
            from .pandora_workflow import locate_engine, available_patches, patch_identity
            current = available_patches(self.organizer, locate_engine(self.organizer))
            if patch_identity(current) != self.job.record['patch_identity']:
                raise ValueError('Animation choices changed while this screen was open. Reopen it before applying.')
            begin_apply(self, self.generate_confirmed)
        except Exception as error:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, 'Preparation needs attention', str(error))

    def generate_confirmed(self):
        from .pandora_workflow import prepare_job
        self.job = prepare_job(self.organizer)
        selected = [self.patches.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.patches.count())
                    if self.patches.item(i).checkState() == Qt.CheckState.Checked]
        self.build.setEnabled(False)
        self.patches.setEnabled(False)
        self.close_button.setEnabled(False)
        try:
            result = run_job(self.organizer, self.job, selected)
            self.status.setPlainText(f'Behavior generation checked; applying {len(result.hashes)} files…')
            self.awaiting_publication = True
            publish_job(self.organizer, self.job, self.ready)
        except Exception as error:
            end_apply(self)
            self.status.setPlainText(str(error) + '\n\nReopen Animation choices after correcting the problem for a fresh attempt.')
            self.close_button.setEnabled(True)

    def ready(self, target, error):
        end_apply(self)
        self.applied = not bool(error)
        self.close_button.setEnabled(True)
        self.status.setPlainText(error if error else
            f'Applied {len(self.job.record["hashes"])} files to {target.name}. All generated files are effective in this profile. '
            'The log and export receipts passed the supported checks; test the intended animations in game. '
            'Your choices are saved for automatic rechecks.')
