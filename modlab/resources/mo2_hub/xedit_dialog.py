"""Keep the cleaning decision visible and retain its result and recovery path."""
import json
from pathlib import Path

import mobase
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel,
                             QLineEdit, QMessageBox, QPushButton, QTextEdit, QVBoxLayout)

from .loot_workflow import context_signature
from .native_install import install_archive
from .xedit import digest, verify_job, write_record
from .xedit_workflow import run_xedit


class XEditDialog(QDialog):
    def __init__(self, organizer, parent=None):
        super().__init__(parent)
        self.organizer = organizer
        self.run = None
        self.setWindowTitle('ModLab — Plugin checks and cleaning')
        self.resize(850, 600)
        layout = QVBoxLayout(self)
        note = QLabel('Check a plugin and its masters using xEdit. Cleaning is optional: follow the mod author’s current instructions and applicable LOOT advice. Some intentional overrides must remain. Neither cleaning nor an error-free record check proves gameplay or conflict compatibility.')
        note.setWordWrap(True); layout.addWidget(note)
        self.plugins = QComboBox()
        plugins = organizer.pluginList()
        self.plugins.addItems(sorted((n for n in plugins.pluginNames() if plugins.loadOrder(n) >= 0), key=str.casefold))
        layout.addWidget(self.plugins)
        self.reason = QLineEdit()
        self.reason.setPlaceholderText('For cleaning: paste the applicable author instruction / LOOT advice and source URL')
        layout.addWidget(self.reason)
        self.allowed = QCheckBox('I checked the author’s instructions; this plugin is suitable for Quick Auto Clean.')
        layout.addWidget(self.allowed)
        row = QHBoxLayout()
        for label, mode in (('Check for record errors', 'check'), ('Clean a copy and check it', 'clean')):
            button = QPushButton(label); button.clicked.connect(lambda _, m=mode: self.execute(m)); row.addWidget(button)
        layout.addLayout(row)
        self.detail = QTextEdit(); self.detail.setReadOnly(True); layout.addWidget(self.detail)
        row = QHBoxLayout()
        self.install = QPushButton('Install checked cleaned copy…'); self.install.setEnabled(False)
        self.install.clicked.connect(self.install_output); row.addWidget(self.install)
        self.files = QPushButton('Open run files'); self.files.setEnabled(False)
        self.files.clicked.connect(self.open_files); row.addWidget(self.files)
        close = QPushButton('Close'); close.clicked.connect(self.accept); row.addWidget(close)
        layout.addLayout(row)

    def refresh_plugins(self):
        selected = self.plugins.currentText()
        plugins = self.organizer.pluginList()
        self.plugins.clear()
        self.plugins.addItems(sorted((n for n in plugins.pluginNames() if plugins.loadOrder(n) >= 0), key=str.casefold))
        if self.plugins.findText(selected) >= 0:
            self.plugins.setCurrentText(selected)

    def execute(self, mode):
        if mode == 'clean' and (not self.allowed.isChecked() or not self.reason.text().strip()):
            QMessageBox.information(self, 'Check the cleaning recommendation', 'Enter the specific instruction and confirm that this plugin should be cleaned. You can check for record errors without cleaning.')
            return
        self.run = None; self.install.setEnabled(False); self.files.setEnabled(False)
        self.setEnabled(False)
        try:
            self.run = run_xedit(self.organizer, self.plugins.currentText(), mode,
                                 self.reason.text(), mobase.getFileVersion)
            job, result, archive, _ = self.run
            if mode == 'check':
                count = result['record_errors']
                message = f'xEdit found {"at least 127" if count == 127 else count} record errors in the selected plugin. See the tool log for affected records.'
            elif result['changed']:
                message = 'The copied plugin was cleaned and passed xEdit’s record-error check. Your source files are unchanged. Review the log before installing the cleaned override.'
            else:
                message = 'Quick Auto Clean completed without changing the plugin. There is no cleaned override to install.'
            self.detail.setPlainText(message + f'\n\nFiles and logs: {job}\n\nGame behavior and compatibility with unrelated plugins remain unverified.')
            self.install.setEnabled(archive is not None); self.files.setEnabled(True)
        except Exception as error:
            self.detail.setPlainText(str(error))
        finally:
            self.setEnabled(True)

    def open_files(self):
        if self.run:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.run[0])))

    def install_output(self):
        job, result, archive, signature = self.run
        try:
            if context_signature(self.organizer) != signature:
                raise ValueError('The setup changed since cleaning. Run the check again before installing.')
            verified = verify_job(job, 'clean', 0)
            if verified['sha256'] != result['sha256']:
                raise ValueError('The cleaned output changed after checking. Run again.')
            self.hide()
            installed, record_path = install_archive(self.organizer, archive, self.installed)
            self.install.setEnabled(False)
            if installed.status != 'Installed; activation pending':
                self.installed(installed, record_path)
        except Exception as error:
            QMessageBox.warning(self, 'Cleaned copy needs attention', str(error))
        finally:
            self.show()

    def installed(self, installed, record_path):
        job, result, _, _ = self.run
        record = json.loads((job / 'operation.json').read_text(encoding='utf-8'))
        target = record['target']
        effective = self.organizer.resolvePath(target)
        try:
            matched = bool(effective) and digest(effective) == result['sha256']
        except OSError as error:
            matched = False
            record['effective_check_error'] = str(error)
        record.update(installation_record=str(record_path), effective_hash_matched=matched,
                      installed_mod=installed.name)
        write_record(job, record)
        self.detail.setPlainText(f'{installed.status}: {installed.name}\n{installed.detail}\n\n' +
            ('The cleaned plugin is now effective in MO2.' if matched else 'The cleaned plugin is not confirmed as effective. Check mod priority and recheck.') +
            '\n\nRecovery: disable the cleaned output mod to reveal the original plugin. ' +
            f'Originals and run details remain retained at {job}. Gameplay is not verified.')
