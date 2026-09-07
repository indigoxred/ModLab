"""Review, apply and undo one checked LOOT proposal."""

import json

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTextEdit, QVBoxLayout

from .loot_workflow import apply_order, context_signature


class LootDialog(QDialog):
    def __init__(self, organizer, run, parent=None):
        super().__init__(parent)
        self.organizer = organizer
        self.run = run
        self.signature = run.signature
        self.setWindowTitle('ModLab — LOOT results')
        self.resize(900, 650)
        layout = QVBoxLayout(self)
        changes = sum(a != b for a, b in zip(run.previous, run.proposal))
        self.status = QLabel(f'LOOT proposed {changes} changed positions. Your order has not been changed.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        details = QTextEdit()
        details.setReadOnly(True)
        lines = ['LOOT checks plugin order and known metadata. Asset conflicts and in-game behavior need separate checks.']
        for finding in run.findings:
            lines.append(f'{finding.level}: {finding.title}\n{finding.detail}\nNext: {finding.action}')
        if not run.findings:
            lines.append('LOOT reported no messages for the active plugins.')
        lines.append('Proposed order:\n' + '\n'.join(f'{i + 1}. {name}' for i, name in enumerate(run.proposal)))
        details.setPlainText('\n\n'.join(lines))
        layout.addWidget(details)
        row = QHBoxLayout()
        self.apply_button = QPushButton('Apply checked order')
        self.apply_button.setEnabled(not any(f.level == 'Blocked' for f in run.findings))
        self.apply_button.clicked.connect(self.apply)
        row.addWidget(self.apply_button)
        self.undo_button = QPushButton('Restore previous order')
        self.undo_button.setEnabled(False)
        self.undo_button.clicked.connect(self.undo)
        row.addWidget(self.undo_button)
        reports = QPushButton('Open saved results')
        reports.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(run.directory))))
        row.addWidget(reports)
        close = QPushButton('Close')
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)

    def record(self, **changes):
        path = self.run.directory / 'operation.json'
        record = json.loads(path.read_text(encoding='utf-8'))
        record.update(changes)
        path.write_text(json.dumps(record, indent=2), encoding='utf-8')

    def apply(self):
        try:
            self.record(status='Applying checked order')
            apply_order(self.organizer, self.run.proposal, self.signature)
            self.signature = context_signature(self.organizer)
            self.apply_button.setEnabled(False)
            self.undo_button.setEnabled(True)
            self.record(status='Order applied and checked', order_applied=True)
            self.status.setText('The proposed order is applied and matches MO2. The previous order is saved for recovery. Gameplay is not verified.')
        except Exception as error:
            QMessageBox.warning(self, 'Order needs attention', str(error))

    def undo(self):
        try:
            self.record(status='Restoring previous order')
            apply_order(self.organizer, self.run.previous, self.signature)
            self.signature = context_signature(self.organizer)
            self.undo_button.setEnabled(False)
            self.record(status='Previous order restored', order_applied=False)
            self.status.setText('The previous plugin order has been restored and checked.')
        except Exception as error:
            QMessageBox.warning(self, 'Restore needs attention', str(error))
