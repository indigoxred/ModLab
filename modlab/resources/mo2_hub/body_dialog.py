"""Choose projects and a preset; build and install checked output through MO2."""

import hashlib
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                             QTextEdit, QVBoxLayout)

from .bodyslide import plan_build
from .body_workflow import check_body_context, run_body_job, publish_body_job
from .body_setup import remember_catalog
from .outputs import output_name, read_manifest


from .dialog_workflow import capture_dialog, begin_apply, end_apply
from .operation_dialog import OperationDialog

class BodyDialog(OperationDialog):
    def __init__(self, organizer, job, parent=None, review_names=None):
        super().__init__(parent)
        self.organizer, self.job = organizer, job
        capture_dialog(self)
        self.installed = None
        self.review_names = set(review_names) if review_names is not None else None
        target = Path(organizer.modsPath()) / output_name(job.record['profile'], job.record['profile_path'])
        self.saved = read_manifest(target, job.record['profile_path'])['projects'] if target.exists() else {}
        self.setWindowTitle('ModLab — Build bodies and outfits')
        self.resize(1050, 740)
        layout = QVBoxLayout(self)
        note = QLabel('Choose the projects you want and a preset for their body family. '
                      'Alternative bodies and physics variants may write the same mesh: select one variant per output. '
                      'Existing BodySlide zap choices are retained. Physics and skeleton requirements still need checking.')
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        row.addWidget(QLabel('Preset'))
        self.preset = QComboBox()
        self.preset.addItem('Choose a preset…', None)
        for name in sorted(job.catalog.presets, key=str.casefold):
            self.preset.addItem(name, name)
        row.addWidget(self.preset, 1)
        row.addWidget(QLabel('Show group'))
        self.group = QComboBox()
        self.group.addItem('All projects', None)
        for name in sorted(job.catalog.groups, key=str.casefold):
            self.group.addItem(name, name)
        self.group.currentIndexChanged.connect(self.filter_projects)
        row.addWidget(self.group, 1)
        layout.addLayout(row)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Filter projects by name; hidden checked projects remain selected')
        self.search.textChanged.connect(self.filter_projects)
        layout.addWidget(self.search)
        self.projects = QListWidget()
        for name in sorted(job.catalog.projects, key=str.casefold):
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setToolTip('Source mod: ' + job.record['project_sources'][name] + '\n' +
                            '\n'.join(job.catalog.projects[name].outputs))
            self.projects.addItem(item)
        self.projects.itemChanged.connect(self.update_selection)
        self.preset.currentIndexChanged.connect(self.restore_selection)
        layout.addWidget(self.projects, 3)
        self.count = QLabel('No projects selected')
        layout.addWidget(self.count)
        selection_row = QHBoxLayout()
        for label, action in (('Select visible', lambda: self.select_visible(True)),
                              ('Clear selection', lambda: self.select_visible(False))):
            button = QPushButton(label)
            button.clicked.connect(action)
            selection_row.addWidget(button)
        layout.addLayout(selection_row)
        self.status = QTextEdit()
        self.status.setReadOnly(True)
        self.status.setPlainText('Output will be generated in a separate working folder. '
                                 'Your installed output and helper settings are preserved. '
                                 'ModLab will install checked files into this profile\'s named BodySlide output, '
                                 'with the source mod, outfit/project and preset recorded for every build.')
        layout.addWidget(self.status, 1)
        actions = QHBoxLayout()
        self.build = QPushButton('Build and apply selected projects')
        self.build.clicked.connect(self.generate)
        actions.addWidget(self.build)
        self.install = QPushButton('Retry applying checked output')
        self.install.setEnabled(False)
        self.install.clicked.connect(self.install_output)
        actions.addWidget(self.install)
        self.check = QPushButton('Check effective output')
        self.check.setEnabled(False)
        self.check.clicked.connect(self.check_effective)
        actions.addWidget(self.check)
        folder = QPushButton('Open build files and log')
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.job.directory or self.job.executable.parent))))
        actions.addWidget(folder)
        close = QPushButton('Close')
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        layout.addLayout(actions)
        keep = QPushButton('Keep current choices; leave other projects unbuilt')
        keep.clicked.connect(self.keep_choices)
        layout.addWidget(keep)
        self.filter_projects()

    def keep_choices(self):
        try:
            remember_catalog(self.organizer, self.job)
            self.preferences_changed = True
            self.accept()
        except Exception as error:
            QMessageBox.warning(self, 'Choices could not be saved', str(error))

    def restore_selection(self):
        preset = self.preset.currentData()
        for item in self.items():
            checked = self.saved.get(item.text(), {}).get('preset') == preset
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.update_selection()

    def items(self):
        return [self.projects.item(i) for i in range(self.projects.count())]

    def selected(self):
        return [item.text() for item in self.items() if item.checkState() == Qt.CheckState.Checked]

    def update_selection(self):
        self.count.setText(f'{len(self.selected())} projects selected')

    def filter_projects(self):
        text, group = self.search.text().casefold(), self.group.currentData()
        for item in self.items():
            item.setHidden(text not in item.text().casefold() or
                           (self.review_names is not None and item.text() not in self.review_names and item.text() not in self.saved) or
                           (group is not None and item.text() not in self.job.catalog.groups[group]))

    def select_visible(self, checked):
        self.projects.blockSignals(True)
        for item in self.items():
            if not checked or not item.isHidden():
                item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.projects.blockSignals(False)
        self.update_selection()

    def generate(self):
        self.applied = False
        try:
            plan_build(self.job.catalog, self.selected(), self.preset.currentData())
            check_body_context(self.organizer, self.job)
            begin_apply(self, self.generate_confirmed)
        except Exception as error:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, 'Preparation needs attention', str(error))

    def generate_confirmed(self):
        import mobase
        from .body_workflow import prepare_body_job
        self.job = prepare_body_job(self.organizer, mobase.getFileVersion)
        selected, preset = self.selected(), self.preset.currentData()
        try:
            plan_build(self.job.catalog, selected, preset)
            check_body_context(self.organizer, self.job)
        except Exception as error:
            QMessageBox.warning(self, 'Review the build selection', str(error))
            return
        self.build.setEnabled(False)
        self.setEnabled(False)
        try:
            hashes = run_body_job(self.organizer, self.job, selected, preset)
            self.status.setPlainText(f'{len(selected)} projects built; all {len(hashes)} expected mesh files checked. Applying output…')
            self.install.setEnabled(True)
            self.install_output()
        except Exception as error:
            self.status.setPlainText(str(error) + '\n\nThe prior installed output is unchanged. '
                                     'Inspect the log, then reopen this workflow for a fresh attempt.')
        finally:
            self.setEnabled(True)

    def install_output(self):
        try:
            check_body_context(self.organizer, self.job)
            self.awaiting_publication = True
            publish_body_job(self.organizer, self.job, self.output_ready)
            self.install.setEnabled(False)
            self.status.setPlainText('Generated output published. Waiting for MO2 to refresh before checking its effective files…')
        except Exception as error:
            end_apply(self)
            self.status.setPlainText(str(error))

    def output_ready(self, target, error):
        end_apply(self)
        self.installed = target
        self.check.setEnabled(True)
        if error:
            self.job.record.update(status='Output activation needs attention', error=error)
            self.job.save()
            self.status.setPlainText(error)
        else:
            self.check_effective()

    def check_effective(self):
        self.applied = False
        failures = []
        if self.organizer.profilePath() != self.job.record['profile_path']:
            failures.append('The selected profile changed.')
        for name, expected in self.job.record.get('hashes', {}).items():
            try:
                resolved = self.organizer.resolvePath(name)
                path = Path(resolved) if resolved else None
                if not path or not path.is_file():
                    failures.append(f'Not effective: {name}')
                elif self.installed.resolve() not in path.resolve().parents:
                    failures.append(f'Another source wins: {name} → {path}')
                elif hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    failures.append(f'Generated content changed: {name}')
            except OSError as error:
                failures.append(f'Could not check {name}: {error}')
        self.job.record['effective_output_issues'] = failures
        self.job.record['status'] = 'Effective output needs attention' if failures else 'Generated output installed and effective; gameplay unverified'
        self.job.save()
        if not failures:
            self.applied = True
            remember_catalog(self.organizer, self.job)
        self.status.setPlainText('\n'.join(failures) if failures else
                                 f"Applied to {self.job.record['installed_mod']}. Every generated mesh is now effective in this profile. "
                                 'Other generated outfits were retained. The selected appearance still needs an in-game check.\n\n' +
                                 '\n'.join(f"{name} — {self.job.record['project_sources'][name]} — {self.job.record['preset']}"
                                           for name in self.job.record['projects']))
