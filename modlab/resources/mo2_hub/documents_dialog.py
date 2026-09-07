"""Installed mod instructions, kept separate from ModLab's assessment."""
from pathlib import Path

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QComboBox, QDialog, QLabel, QPushButton, QTextEdit, QVBoxLayout

from .mod_documents import documents, read_document


class DocumentsDialog(QDialog):
    def __init__(self, organizer, parent=None):
        super().__init__(parent)
        self.setWindowTitle('ModLab — Mod instructions')
        self.resize(940, 650)
        layout = QVBoxLayout(self)
        note = QLabel('Instructions supplied with installed mods. These may describe manual setup or removal steps; '
                      'displaying them does not mean ModLab has performed or verified those steps.')
        note.setWordWrap(True); layout.addWidget(note)
        self.mods = QComboBox(); layout.addWidget(self.mods)
        self.files = QComboBox(); layout.addWidget(self.files)
        self.location = QLabel(); self.location.setWordWrap(True); layout.addWidget(self.location)
        self.text = QTextEdit(); self.text.setReadOnly(True); layout.addWidget(self.text)
        folder = QPushButton('Open this mod’s folder'); folder.clicked.connect(self.open_folder)
        layout.addWidget(folder)
        close = QPushButton('Close'); close.clicked.connect(self.accept); layout.addWidget(close)
        mods = organizer.modList()
        for name in sorted(mods.allMods(), key=str.casefold):
            mod = mods.getMod(name)
            if mod and Path(mod.absolutePath()).is_dir():
                self.mods.addItem(name, mod.absolutePath())
        self.mods.currentIndexChanged.connect(self.show_mod)
        self.files.currentIndexChanged.connect(self.show_file)
        self.show_mod()

    def show_mod(self):
        self.files.clear()
        root = self.mods.currentData()
        if not root:
            self.text.setPlainText('No installed mod folders are available.'); return
        self.location.setText(str(root))
        try:
            for path in documents(root):
                self.files.addItem(str(path.relative_to(root)), str(path))
            if not self.files.count():
                self.text.setPlainText('No supported local instructions found. The author may provide them on the mod page, '
                    'inside the original download, or in another format. Open the mod folder to inspect other files.\n\n'
                    'This preview checks text/Markdown/HTML files in the mod root and its documentation folders, up to two levels deep.')
        except Exception as error:
            self.text.setPlainText('Instructions could not be listed: ' + str(error))

    def show_file(self):
        path = self.files.currentData()
        if not path:
            return
        try:
            self.location.setText(str(path))
            self.text.setPlainText(read_document(self.mods.currentData(), path))
        except Exception as error:
            self.text.setPlainText('Document preview unavailable: ' + str(error))

    def open_folder(self):
        root = self.mods.currentData()
        if root:
            QDesktopServices.openUrl(QUrl.fromLocalFile(root))
