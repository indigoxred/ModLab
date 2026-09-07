"""User-visible operation history and recovery of managed generated files."""
import json
from pathlib import Path

from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QTabWidget, QTextEdit, QVBoxLayout, QWidget)

from .outputs import MANIFEST, digest, output_name, read_manifest
from .recovery import read_history, restore_previous_output, synthesis_recovery_choices, npc_recovery_choices
from .skse import require_game_closed


class HistoryDialog(QDialog):
    def __init__(self, organizer, parent=None):
        super().__init__(parent)
        self.organizer = organizer
        self.profile_path = organizer.profilePath()
        self.profile_name = organizer.profile().name()
        self.instance = Path(organizer.modsPath()).parent
        self.busy = False
        self.changed = False
        self.selection = None
        self.setWindowTitle('ModLab — History and recovery')
        self.resize(1040, 680)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Profile: ' + self.profile_name))
        tabs = QTabWidget()
        layout.addWidget(tabs)
        history = QWidget()
        history_layout = QVBoxLayout(history)
        self.rows = QTableWidget(0, 4)
        self.rows.setHorizontalHeaderLabels(['Time (UTC)', 'Operation', 'Result', 'Profile scope'])
        self.rows.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.rows.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for column, width in enumerate((195, 110, 480, 150)):
            self.rows.setColumnWidth(column, width)
        history_layout.addWidget(self.rows)
        self.detail = QTextEdit(); self.detail.setReadOnly(True)
        history_layout.addWidget(self.detail)
        buttons = QHBoxLayout()
        for title, handler in (('Open operation folder', self.open_folder), ('Open full record', self.open_record)):
            button = QPushButton(title); button.clicked.connect(handler); buttons.addWidget(button)
        history_layout.addLayout(buttons)
        self.rows.itemSelectionChanged.connect(self.show_record)
        tabs.addTab(history, 'Operations')
        recovery = QWidget()
        recovery_layout = QVBoxLayout(recovery)
        note = QLabel('Restore a previous generated output for this profile. The replaced version is retained. '
            'Restoration may affect other profiles that explicitly enabled this same output mod. '
            'Recheck afterward; older output may need rebuilding if its inputs changed.')
        note.setWordWrap(True); recovery_layout.addWidget(note)
        self.outputs = QComboBox()
        recovery_layout.addWidget(self.outputs)
        self.preview = QTextEdit(); self.preview.setReadOnly(True)
        recovery_layout.addWidget(self.preview)
        self.restore_button = QPushButton('Restore previous output')
        self.restore_button.clicked.connect(self.restore)
        recovery_layout.addWidget(self.restore_button)
        self.outputs.currentIndexChanged.connect(self.show_output)
        tabs.addTab(recovery, 'Restore generated output')
        self.status = QLabel(''); self.status.setWordWrap(True); layout.addWidget(self.status)
        close = QPushButton('Close'); close.clicked.connect(self.accept); layout.addWidget(close)
        self.reload()

    def accept(self):
        if not self.busy:
            super().accept()

    def reject(self):
        if not self.busy:
            super().reject()

    def reload(self):
        self.entries = read_history(self.instance, self.organizer.getPluginDataPath(), self.profile_path, self.profile_name)
        self.rows.setRowCount(len(self.entries))
        for index, entry in enumerate(self.entries):
            for column, value in enumerate((entry.time[:19].replace('T', ' '), entry.operation, entry.status, entry.scope)):
                self.rows.setItem(index, column, QTableWidgetItem(value))
        if self.entries:
            self.rows.selectRow(0)
        self.outputs.clear()
        for tool in ('BodySlide', 'Pandora', 'NPC Appearance', 'Synthesis', 'Graphics', 'Cleaned plugins', 'SKSE scripts'):
            target = Path(self.organizer.modsPath()) / output_name(self.profile_name, self.profile_path, tool)
            if (target / MANIFEST).is_file():
                self.outputs.addItem(target.name, (tool, target))
        self.show_output()

    def selected_record(self):
        index = self.rows.currentRow()
        return self.entries[index] if 0 <= index < len(self.entries) else None

    def show_record(self):
        entry = self.selected_record()
        if not entry:
            return
        data = entry.record
        # Archive queues reuse the same history records as individual operations.
        lines = [entry.operation + ' — ' + entry.status, entry.scope, 'ModLab operation: ' + entry.path.parent.name,
                 entry.subject, str(entry.path)]
        for item in (data.get('items', []) if entry.operation == 'install-queue' else []):
            if not isinstance(item, dict):
                continue
            lines.append(item.get('state', 'Recorded') + ' — ' + item.get('archive', '') + '\n' + item.get('detail', ''))
            if item.get('record'):
                lines.append('Installer record: ' + item['record'])
        if data.get('setup_record'):
            lines.append('Resulting setup check: ' + data['setup_record'])
        for key in ('detail', 'error', 'recovery_error', 'archive', 'previous_output', 'retained_output'):
            if data.get(key):
                lines.append(key.replace('_', ' ').capitalize() + ': ' + str(data[key]))
        if data.get('steps'):
            lines.append('Completed steps:\n' + '\n'.join(data['steps']))
        for finding in data.get('findings', []):
            if isinstance(finding, dict) and finding.get('level') in ('Blocked', 'Review', 'Unknown'):
                lines.append(finding.get('title', 'Finding') + '\nNext action: ' + finding.get('action', 'Open the full record.'))
        backups = [name for name in data.get('new_directories', []) if 'backup' in name.casefold()]
        if entry.operation == 'Installation':
            lines.append('Archive installation uses MO2’s native backups. To restore one, open MO2, right-click the '
                         'retained backup and choose its restore action. Review the destination before replacing it.')
            if backups:
                lines.append('Retained backup directories:\n' + '\n'.join(str(Path(data['mods_root']) / name) for name in backups))
        self.detail.setPlainText('\n\n'.join(str(line) for line in lines if line))

    def open_folder(self):
        entry = self.selected_record()
        if entry:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(entry.path.parent)))

    def open_record(self):
        entry = self.selected_record()
        if entry:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(entry.path)))

    def show_output(self):
        self.selection = None
        self.restore_button.setEnabled(False)
        selected = self.outputs.currentData()
        if not selected:
            self.preview.setPlainText('No managed generated output is installed for this profile.')
            return
        tool, target = selected
        try:
            current = read_manifest(target, self.profile_path, tool)
            backup = Path(current['previous_output'])
            backup.resolve().relative_to(self.instance.resolve() / 'builds')
            previous = read_manifest(backup, self.profile_path, tool)
            changed = sum(current['hashes'].get(name) != previous['hashes'].get(name)
                          for name in set(current['hashes']) | set(previous['hashes']))
            lines = [target.name, f"Current: {len(current['hashes'])} files. Previous: {len(previous['hashes'])} files.",
                     f'{changed} file contents or paths differ.', 'Restore from: ' + str(backup),
                     'Previous projects:\n' + ('\n'.join(previous['projects']) or 'No generated files; this restores the original empty output.')]
            if tool == 'SKSE scripts':
                lines.append('SKSE requires coordinated restoration of shared root files and profile scripts. '
                             'This output-only action is unavailable for SKSE. The installation folder retains its '
                             'root-deployment record and backups; reinstall a matching official archive through ModLab for now.')
            elif tool in ('Synthesis', 'NPC Appearance'):
                kind = 'npc' if tool == 'NPC Appearance' else 'patcher'
                if (Path(self.profile_path) / ('modlab-' + kind + '-pending.json')).exists():
                    raise ValueError('Resolve or cancel the pending patch request before restoring.')
                check = npc_recovery_choices if tool == 'NPC Appearance' else synthesis_recovery_choices
                check(self.instance, self.profile_path, previous)
                choices = Path(self.profile_path) / ('modlab-' + kind + '-choices.json')
                self.selection = (tool, target, digest(target / MANIFEST), previous, digest(choices))
                self.restore_button.setEnabled(True)
                lines.append(('Restores NPC records, paired assets and saved character choices together. '
                              if tool == 'NPC Appearance' else
                              'Restores the patch, its chosen settings and matching FormID allocation data together. ') +
                             'Use a save from before the build being undone; this cannot undo changes already stored in newer saves. '
                             'Recheck may rebuild the restored choices when installed mod inputs have changed.')
            elif tool == 'Graphics':
                lines.append('Graphics recovery must preserve its renderer/conflict choices and generated plugin identities. '
                             'One-click restoration is not enabled. The operation folder retains the previous files and '
                             'settings for review; use Graphics choices and Recheck for a build matching the current setup.')
            else:
                self.selection = (tool, target, digest(target / MANIFEST), previous, None)
                self.restore_button.setEnabled(True)
            self.preview.setPlainText('\n\n'.join(lines))
        except Exception as error:
            self.preview.setPlainText('Restoration is unavailable: ' + str(error) + '\n\nExisting files have not been changed.')

    def restore(self):
        if not self.selection or self.busy:
            return
        tool, target, expected, previous, expected_choices = self.selection
        if QMessageBox.question(self, 'Restore generated output',
                f'Restore {target.name} to its previous {len(previous["hashes"])} files? '
                'The current version will be retained in this recovery operation.' +
                ('\n\nThis also restores the matching saved choices. Use a save from before the build being undone.'
                 if tool in ('Synthesis', 'NPC Appearance') else '')) != QMessageBox.StandardButton.Yes:
            return
        try:
            require_game_closed()
            if self.organizer.profilePath() != self.profile_path:
                raise ValueError('The selected profile changed. Reopen history in the intended profile.')
            record_path = restore_previous_output(target, self.profile_path, tool, expected, expected_choices=expected_choices)
        except Exception as error:
            QMessageBox.warning(self, 'Restore needs attention', str(error))
            return
        self.busy = True
        self.setEnabled(False)
        self.changed = True

        def ready():
            data = json.loads(record_path.read_text(encoding='utf-8'))
            try:
                require_game_closed()
                if self.organizer.profilePath() != self.profile_path:
                    raise ValueError('Profile changed after restoration. Effective files need checking in the original profile.')
                import mobase
                active = self.organizer.modList().state(target.name) & mobase.ModState.ACTIVE
                if active:
                    if tool in ('Synthesis', 'NPC Appearance'):
                        from .pandora_workflow import activate_generated_plugins
                        data['activated_plugins'] = activate_generated_plugins(self.organizer, target, previous['hashes'], mobase.PluginState.ACTIVE)
                    for name, checksum in previous['hashes'].items():
                        value = self.organizer.resolvePath(name)
                        effective = Path(value) if value else None
                        if not effective or effective.resolve() != (target / name).resolve() or not effective.is_file() or digest(effective) != checksum:
                            raise ValueError('The restored output is not effective: ' + name + '. Review the winning mod before launching.')
                data['status'] = 'Previous output restored and effective' if active else 'Previous output restored; mod remains disabled'
                self.status.setText(data['status'] + '. Current files retained at ' + data['retained_output'])
            except Exception as error:
                data.update(status='Restored files need attention', error=str(error))
                self.status.setText(str(error))
            finally:
                record_path.write_text(json.dumps(data, indent=2), encoding='utf-8')
                self.busy = False
                self.setEnabled(True)
                self.reload()
        self.organizer.onNextRefresh(lambda: QTimer.singleShot(0, ready), False)
        self.organizer.refresh()
