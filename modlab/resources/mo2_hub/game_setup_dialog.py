"""Guided use of the upstream downgrade tool; no replacement patching engine."""
import os
from pathlib import Path
from uuid import uuid4

import mobase
from PyQt6.QtCore import QProcess, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTextBrowser, QVBoxLayout

from . import game_setup
from .skse import require_game_closed
from .outputs import digest


class GameSetupDialog(QDialog):
    def __init__(self, organizer, parent=None):
        super().__init__(parent)
        self.organizer = organizer
        self.game = Path(organizer.managedGame().gameDirectory().absolutePath())
        self.catalog = Path(os.environ['LOCALAPPDATA']) / 'Skyrim Special Edition/ContentCatalog.txt'
        self.setWindowTitle('ModLab — Skyrim 1.6.1170 setup')
        self.resize(900, 680)
        layout = QVBoxLayout(self)
        note = QLabel('This guided route uses wSkeever’s existing Steam 1.7.104 → 1.6.1170 patcher. '
                      'The game folder is shared by every MO2 profile. Keep the previous profile and use a fresh test save.')
        note.setWordWrap(True)
        layout.addWidget(note)
        instructions = QTextBrowser()
        instructions.setPlainText(
            '1. Close Skyrim. Prepare a backup below. Copy your MO2 profile before replacing its selected mods.\n\n'
            '2. Download “1.7.104 to 1.6.1170 Downgrade Patcher” from the linked author page. '
            'Extract it into the game folder shown below. Run Skyrim_1_7_104_to_1_6_1170_patcher.exe '
            'from that folder and follow its instructions. Keep its backup. Do not choose the 1.5.97 patcher.\n\n'
            '3. The upstream tool handles the executable, compatible shaders and stale ContentCatalog metadata. '
            'Retain the official game data and four free Creation components. This is the author’s Best of Both Worlds route, '
            'not a complete old-data Steam installation. Do not remove Creation content to fix unrelated native DLL mismatches.\n\n'
            '4. Run Check baseline. Install Steam SKSE 2.2.8 and the linked USSEP 4.3.8a archive through ModLab’s '
            'Install archives. Keep older and newer mod packages separate. Recheck and finish setup must resolve '
            'remaining native DLL and master requirements before launch. An old download date alone is not incompatibility.\n\n'
            '5. Launch using ModLab/MO2’s matched SKSE and a fresh test save. Steam updates or Verify integrity can '
            'replace downgraded files; recheck the baseline if that happens.\n\n'
            f'Selected game: {self.game}\n\n'
            'Recovery: close the game and retain the changed files, then restore the matching files recorded in backup.json '
            'and the prior profile together. Do not mix an old loader with a different game executable.')
        layout.addWidget(instructions)
        row = QHBoxLayout()
        for text, url in [('Patcher download', game_setup.PATCHER_URL),
                          ('Steam SKSE 2.2.8', game_setup.SKSE_URL), ('USSEP 4.3.8a archive', game_setup.USSEP_URL)]:
            button = QPushButton(text)
            button.clicked.connect(lambda checked=False, value=url: QDesktopServices.openUrl(QUrl(value)))
            row.addWidget(button)
        layout.addLayout(row)
        row = QHBoxLayout()
        for text, action in [('Prepare backup', self.prepare), ('Open game folder', self.open_game),
                              ('Run downloaded patcher', self.run_patcher),
                              ('Check baseline', self.refresh)]:
            button = QPushButton(text)
            button.clicked.connect(action)
            if text == 'Run downloaded patcher':
                self.run_button = button
            row.addWidget(button)
        layout.addLayout(row)
        self.result = QTextBrowser()
        layout.addWidget(self.result)
        self.refresh()

    def open_game(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.game)))

    def prepare(self):
        try:
            require_game_closed()
            game_setup.check_source(mobase.getFileVersion(str(self.game / 'SkyrimSE.exe')),
                                    (self.game / 'steam_api64.dll').is_file(), game_setup.sha1(self.game / 'SkyrimSE.exe'))
            mismatches = game_setup.mismatched_files(self.game, game_setup.SOURCE_FILES)
            if mismatches:
                raise ValueError('The patcher cannot use these changed or missing source files: ' + ', '.join(mismatches))
            target = Path(self.organizer.getPluginDataPath()) / 'modlab/game-backups' / uuid4().hex[:12]
            game_setup.backup_files(self.game, self.catalog, target)
            self.result.setPlainText('Backup verified. No game files have changed.\n\n' + str(target) +
                                     '\n\nNext: extract and run the upstream patcher, then Check baseline.')
            return target
        except Exception as error:
            QMessageBox.warning(self, 'Preparation stopped', str(error))
            return None

    def run_patcher(self):
        patcher = self.game / game_setup.PATCHER_NAME
        if not patcher.is_file() or patcher.is_symlink() or digest(patcher) != game_setup.PATCHER_SHA256:
            QMessageBox.warning(self, 'Patcher package required',
                'Extract the verified 1.7.104.3 upstream patcher into the selected game folder first. '
                'This launcher accepts only the package checked by ModLab; use the linked guide for a different release.')
            return
        if any((self.game / name).exists() for name in ('1.7.104 backup', '1.6.1170 backup')):
            QMessageBox.warning(self, 'Previous patcher run detected',
                'The upstream patcher’s backup folders already exist. Check baseline first. '
                'Preserve and resolve that earlier run before using the patcher again.')
            return
        backup = self.prepare()
        if backup is None:
            return
        started, pid = QProcess.startDetached(str(patcher), [], str(self.game))
        if not started:
            QMessageBox.warning(self, 'Patcher could not start', 'Your backup is retained at ' + str(backup))
            return
        self.run_button.setEnabled(False)
        self.result.setPlainText(f'Upstream patcher started (process {pid}). Follow its dialogs.\n'
            f'Backup: {backup}\n\nAfter it finishes, Check baseline. '
            'Use ModLab’s SKSE download link: the patcher’s own SKSE link currently points to the wrong archive.')

    def refresh(self):
        try:
            results = game_setup.inspect_baseline(self.game, self.catalog, mobase.getFileVersion)
            if not any(line.startswith('BLOCKED:') for line in results):
                record = game_setup.remember_baseline(self.game, Path(self.organizer.getPluginDataPath()) / 'modlab')
                results.append('SAVED: Normal rechecks and ModLab launches will verify these game files. Record: ' + str(record))
            self.result.setPlainText('\n\n'.join(results))
        except Exception as error:
            self.result.setPlainText('Baseline could not be checked: ' + str(error))
