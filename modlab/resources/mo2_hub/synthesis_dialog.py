"""Choose patch behavior once; the normal setup workflow handles later rebuilds."""
import json
from pathlib import Path

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QApplication, QDialog, QFileDialog, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout

from . import synthesis_workflow as workflow
from .synthesis import inspect_pipeline, load_pending, begin_pending, update_pending, clear_pending


def speed_reach_recipe():
    return {'Version': 2, 'Profiles': [{
        '$type': 'Synthesis.Bethesda.Execution.Settings.V2.SynthesisProfile, Synthesis.Bethesda.Execution',
        'ID': 'modlab-speed-reach', 'Nickname': 'Speed and Reach', 'TargetRelease': 'SkyrimSE',
        'MutagenVersioning': 'Match', 'SynthesisVersioning': 'Match',
        'Groups': [{'On': True, 'Name': 'ModLab Speed and Reach', 'Patchers': [{
            '$type': 'Synthesis.Bethesda.Execution.Settings.GithubPatcherSettings, Synthesis.Bethesda.Execution',
            'ID': 'speed-reach', 'Nickname': 'Speed and Reach Fixes Updated', 'On': True,
            'RemoteRepoPath': 'https://github.com/beefclot/SpeedAndReachFixesUpdated',
            'SelectedProjectSubpath': 'SpeedAndReachFixesUpdated/SpeedAndReachFixesUpdated.csproj',
            'PatcherVersioning': 'Commit', 'TargetCommit': '8d7b4d0761058b5177d7f6128234e34db0889f16',
            'MutagenVersionType': 'Match', 'SynthesisVersionType': 'Match', 'AutoUpdateToBranchTip': False}]}]}]}


from .dialog_workflow import capture_dialog, begin_apply, end_apply
from .operation_dialog import OperationDialog

class SynthesisDialog(OperationDialog):
    def __init__(self, organizer, parent=None):
        super().__init__(parent)
        self.organizer = organizer
        capture_dialog(self)
        self.profile_path = organizer.profilePath()
        self.pending = load_pending(self.profile_path)
        self.pending_id = None
        self.saved = self.pending or workflow.load_choices(organizer) or {}
        self.settings = self.saved.get('settings')
        self.extra_data = self.saved.get('extra_data')
        self.persistence = self.saved.get('persistence')
        self.job = None
        self.setWindowTitle('ModLab — Patch choices')
        self.resize(880, 600)
        layout = QVBoxLayout(self)
        note = QLabel('Patchers make specific changes; they do not automatically reconcile every conflict. '
                      'Choose the behavior you want. ModLab runs it against this profile, checks the exported plugins with xEdit, '
                      'retains previous output, and remembers successful choices for later rebuilds.')
        note.setWordWrap(True); layout.addWidget(note)
        row = QHBoxLayout()
        self.recipe = QPushButton('Choose speed / reach changes')
        self.recipe.clicked.connect(self.choose_recipe); row.addWidget(self.recipe)
        self.import_button = QPushButton('Import Synthesis choices…')
        self.import_button.clicked.connect(self.import_settings); row.addWidget(self.import_button)
        layout.addLayout(row)
        self.description = QTextEdit(); self.description.setReadOnly(True); layout.addWidget(self.description)
        self.status = QLabel('No patcher runs until you choose Generate and apply.'); self.status.setWordWrap(True); layout.addWidget(self.status)
        row = QHBoxLayout()
        self.build = QPushButton('Generate and apply selected patches'); self.build.clicked.connect(self.generate); row.addWidget(self.build)
        details = QPushButton('Open retained settings / log'); details.clicked.connect(self.open_details); row.addWidget(details)
        self.close_button = QPushButton('Close'); self.close_button.clicked.connect(self.accept); row.addWidget(self.close_button)
        self.cancel_pending = QPushButton('Cancel pending attempt')
        self.cancel_pending.clicked.connect(self.cancel_attempt)
        self.cancel_pending.setVisible(bool(self.pending)); row.addWidget(self.cancel_pending)
        layout.addLayout(row)
        self.describe()
        if self.pending:
            self.status.setText('Your previous patch request has not been accepted. ' +
                                (self.pending.get('error') or 'It was interrupted before completion.') +
                                '\nResolve the problem and retry, or cancel this pending request. Existing installed files are preserved.')

    def cancel_attempt(self):
        try:
            clear_pending(self.profile_path, self.pending['id'])
            self.preferences_changed = True
            self.accept()
        except Exception as error:
            self.status.setText(str(error))

    def describe(self):
        self.build.setEnabled(bool(self.settings))
        if not self.settings:
            self.description.setPlainText('No patcher selected. This is normal when your chosen mods do not require a generated patch.')
            return
        pipeline = inspect_pipeline(self.settings)
        text = '\n\n'.join(f'{name} → {group}.esp\n{url}\nRevision: {commit}' for group, name, url, commit in pipeline.patchers)
        if any('SpeedAndReachFixesUpdated' in p[2] for p in pipeline.patchers):
            text += ('\n\nSpeed and Reach changes weapon speed/reach and combat distance settings across matching active records. '
                     'Its author defaults also add 7 degrees to race attack strike angles (an experimental setting). '
                     'These are gameplay choices, not universal bug fixes. Test combat in game after applying. '
                     'Imported patcher settings are retained; a new selection uses author defaults.')
        text += '\n\nLOOT is rechecked after application. Successful file/record checks do not prove the intended gameplay changes.'
        self.description.setPlainText(text)

    def choose_recipe(self):
        self.settings = speed_reach_recipe()
        self.extra_data = self.persistence = None
        self.describe()

    def import_settings(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Select Synthesis PipelineSettings.json', '', 'Pipeline settings (*.json)')
        if not path: return
        try:
            settings = json.loads(Path(path).read_text(encoding='utf-8-sig'))
            inspect_pipeline(settings)
            self.settings = settings
            self.extra_data = Path(path).parent / 'Data'
            self.persistence = None
            self.describe()
            self.status.setText('Imported the selected pipeline and its adjacent Data settings. Existing save-dependent patches require their original FormID persistence data before rebuilding.')
        except Exception as error:
            self.status.setText(str(error))

    def generate(self):
        try:
            inspect_pipeline(self.settings)
            begin_apply(self, self.generate_confirmed)
        except Exception as error:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, 'Preparation needs attention', str(error))

    def generate_confirmed(self):
        for widget in (self.build, self.recipe, self.import_button, self.close_button, self.cancel_pending): widget.setEnabled(False)
        self.status.setText('Preparing the SDK, building selected patchers, then checking their output. Initial package downloads may take a few minutes…')
        QApplication.processEvents()
        try:
            if self.organizer.profilePath() != self.profile_path:
                raise ValueError('The selected MO2 profile changed. Reopen Patch choices for the intended profile.')
            self.pending_id = begin_pending(self.profile_path, self.settings, self.extra_data, self.persistence)
            self.job = workflow.prepare_job(self.organizer, self.settings, self.extra_data, self.persistence)
            update_pending(self.profile_path, self.pending_id, build_record=self.job.directory / 'operation.json')
            workflow.run_job(self.organizer, self.job)
            self.status.setText('Export and record checks passed. Applying the generated patches…')
            self.awaiting_publication = True
            workflow.publish_job(self.organizer, self.job, self.ready)
        except Exception as error:
            end_apply(self)
            message = self.retain_failure(error)
            self.status.setText(message + '\nReopen Patch choices after resolving this problem for a fresh attempt.')
            self.close_button.setEnabled(True)

    def retain_failure(self, error):
        if self.pending_id:
            try:
                update_pending(self.profile_path, self.pending_id, error=str(error),
                               build_record=self.job.directory / 'operation.json' if self.job else None)
            except Exception as persistence_error:
                return str(error) + '\nCould not update the pending record: ' + str(persistence_error)
        return str(error)

    def ready(self, target, error):
        end_apply(self)
        self.applied = not bool(error)
        self.close_button.setEnabled(True)
        if error:
            error = self.retain_failure(error)
        else:
            try:
                clear_pending(self.profile_path, self.pending_id)
            except Exception as problem:
                self.status.setText('Patches were applied, but the pending request could not be cleared: ' + str(problem))
                return
        self.status.setText(error or f'Applied {len(self.job.record["hashes"])} checked patch plugins in {target.name}. '
                            'Close this window to recheck the resulting order. Your choices and settings are retained for automatic rebuilds.')

    def open_details(self):
        path = self.job.directory if self.job else (
            Path(self.pending['build_record']).parent if self.pending and self.pending.get('build_record') else
            Path(self.extra_data) if self.extra_data else Path(self.profile_path))
        if path: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
