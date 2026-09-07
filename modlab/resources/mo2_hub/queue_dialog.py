"""Resume unattempted archives; retries of ambiguous installs require selection."""
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout

from .install_queue import dismiss_queue


class QueueDialog(QDialog):
    def __init__(self, queue, parent=None):
        super().__init__(parent)
        self.queue = queue
        self.archives = []
        self.setWindowTitle('ModLab — Installation queue')
        self.resize(960, 560)
        layout = QVBoxLayout(self)
        note = QLabel('Unattempted archives are selected. Failed or interrupted installers need explicit selection to retry; '
                      'check their installed files first. Completed archives are not reinstalled. FOMOD choices remain interactive.')
        note.setWordWrap(True); layout.addWidget(note)
        self.note = note
        self.table = QTableWidget(len(queue.data['items']), 4)
        self.table.setHorizontalHeaderLabels(['Continue', 'Archive', 'Result', 'Details'])
        self.table.setColumnWidth(0, 65); self.table.setColumnWidth(1, 360); self.table.setColumnWidth(2, 135)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for row, item in enumerate(queue.data['items']):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                           if item['state'] not in {'Completed', 'Dismissed'} else Qt.ItemFlag.NoItemFlags)
            check.setCheckState(Qt.CheckState.Checked if item['state'] == 'Queued' else Qt.CheckState.Unchecked)
            self.table.setItem(row, 0, check)
            for column, text in enumerate((Path(item['archive']).name, item['state'], item.get('detail', '')), 1):
                cell = QTableWidgetItem(text); cell.setToolTip(item['archive'] + '\n' + item.get('detail', ''))
                self.table.setItem(row, column, cell)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        for label, handler in (('Continue selected archives', self.resume), ('Open saved queue', self.open_record),
                               ('Dismiss remaining requests', self.dismiss), ('Close', self.reject)):
            button = QPushButton(label); button.clicked.connect(handler); row.addWidget(button)
        layout.addLayout(row)

    def resume(self):
        self.archives = [item['archive'] for row, item in enumerate(self.queue.data['items'])
                         if self.table.item(row, 0).checkState() == Qt.CheckState.Checked]
        if self.archives:
            self.accept()

    def dismiss(self):
        try:
            dismiss_queue(self.queue)
            self.reject()
        except Exception as error:
            self.note.setText('The remaining requests could not be dismissed: ' + str(error))

    def open_record(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.queue.path)))
