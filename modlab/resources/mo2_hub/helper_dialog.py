"""Show known locations and acquire missing helper paths once."""

from pathlib import Path

import mobase
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
                             QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout)

from .helpers import HELPERS, load_locations, locate_helper, save_location
from .vfs import find_files


class HelperDialog(QDialog):
    def __init__(self, organizer, parent=None):
        super().__init__(parent)
        self.organizer = organizer
        self.config = Path(organizer.getPluginDataPath()) / 'modlab' / 'helpers.json'
        self.keys = tuple(HELPERS)
        self.paths = {}
        self.setWindowTitle('ModLab — Helpers and folders')
        self.resize(1000, 720)
        layout = QVBoxLayout(self)
        note = QLabel('Use the helpers your selected mods need. Finding an executable does not establish its compatibility or a complete installation.')
        note.setWordWrap(True)
        layout.addWidget(note)
        setup = QPushButton('Skyrim 1.6.1170 setup / downgrade…')
        setup.clicked.connect(self.game_setup)
        layout.addWidget(setup)
        self.table = QTableWidget(len(self.keys), 3)
        self.table.setHorizontalHeaderLabels(['Helper', 'Availability', 'Location'])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setColumnWidth(0, 150)
        self.table.setColumnWidth(1, 190)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self.selection)
        layout.addWidget(self.table)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details)
        row = QHBoxLayout()
        for text, action in (('Official download / instructions', self.download),
                             ('Locate executable…', self.locate), ('Open helper folder', self.open_helper),
                             ('Recheck locations', self.refresh)):
            button = QPushButton(text)
            button.clicked.connect(action)
            row.addWidget(button)
        layout.addLayout(row)
        for label, path in (
            ('Game', organizer.managedGame().gameDirectory().absolutePath()),
            ('Downloads', organizer.downloadsPath()), ('Installed mods', organizer.modsPath()),
            ('Selected profile', organizer.profilePath()), ('Overwrite', organizer.overwritePath()),
        ):
            line = QHBoxLayout()
            button = QPushButton('Open ' + label)
            button.clicked.connect(lambda checked=False, value=path: self.open_folder(value))
            line.addWidget(button)
            text = QLabel(path)
            text.setWordWrap(True)
            line.addWidget(text, 1)
            layout.addLayout(line)
        footer = QLabel('Overwrite can contain persistent mod settings as well as generated files. Its contents are preserved.')
        footer.setWordWrap(True)
        layout.addWidget(footer)
        self.refresh()
        self.table.selectRow(0)

    def selected(self):
        row = self.table.currentRow()
        return self.keys[row] if 0 <= row < len(self.keys) else None

    def game_setup(self):
        from .game_setup_dialog import GameSetupDialog
        GameSetupDialog(self.organizer, self).exec()

    def refresh(self):
        try:
            saved = load_locations(self.config)
            # The host's virtual tree already knows active mod installations.
            files = find_files(self.organizer, '', ['*.exe'])
            candidates = [path if Path(path).is_file() else self.organizer.resolvePath(path) for path in files]
            candidates.append(Path(self.organizer.managedGame().gameDirectory().absolutePath()) / 'skse64_loader.exe')
            self.paths = {key: locate_helper(helper, saved, candidates) for key, helper in HELPERS.items()}
            for row, key in enumerate(self.keys):
                found = self.paths[key]
                status = 'Not located' if not found else ('Choose a copy' if len(found) > 1 else 'Located; version unverified')
                if len(found) == 1:
                    version = mobase.getFileVersion(str(found[0]))
                    if version:
                        status = 'Located: ' + version
                for column, value in enumerate((HELPERS[key].label, status, '\n'.join(map(str, found)))):
                    self.table.setItem(row, column, QTableWidgetItem(value))
            self.selection()
        except Exception as error:
            QMessageBox.warning(self, 'Helper discovery needs attention', str(error))

    def selection(self):
        key = self.selected()
        if key:
            helper = HELPERS[key]
            self.details.setPlainText(f'{helper.label}\n\n{helper.purpose}\n\n{helper.placement}\n\n'
                                     'Keep all files supplied with the helper, including its libraries and resources. '
                                     'Use Locate once after extracting an external tool; archives installed through MO2 can be detected from its active files.')

    def download(self):
        key = self.selected()
        if key:
            QDesktopServices.openUrl(QUrl(HELPERS[key].download))

    def locate(self):
        key = self.selected()
        if not key:
            return
        filename, _ = QFileDialog.getOpenFileName(self, 'Locate ' + HELPERS[key].label,
                                                  self.organizer.basePath(), 'Applications (*.exe)')
        if filename:
            try:
                save_location(self.config, key, Path(filename))
                self.refresh()
            except Exception as error:
                QMessageBox.warning(self, 'Location could not be saved', str(error))

    def open_helper(self):
        key = self.selected()
        paths = self.paths.get(key, ())
        if len(paths) == 1:
            self.open_folder(paths[0].parent)
        else:
            QMessageBox.information(self, 'Choose a helper location', 'Use Locate executable to select the installed copy first.')

    def open_folder(self, path):
        if Path(path).is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:
            QMessageBox.warning(self, 'Folder unavailable', str(path))
