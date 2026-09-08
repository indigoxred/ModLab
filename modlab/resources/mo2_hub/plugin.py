"""ModLab's user window, hosted by MO2 rather than a second manager."""

import json
import re
from dataclasses import asdict
from pathlib import Path

import mobase
from PyQt6.QtCore import QCoreApplication, QTimer, QUrl, Qt
from PyQt6.QtGui import QDesktopServices, QIcon, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget,
)

from .assessment import Finding, assess
from .inspection import collect_setup
from .native_install import install_archive
from .loot_workflow import run_loot, context_signature
from .loot_dialog import LootDialog
from .helper_dialog import HelperDialog
from .body_workflow import prepare_body_job
from .body_dialog import BodyDialog
from .xedit_dialog import XEditDialog
from .workflow import finish_setup, complete_cleaning
from .cleaning import withdraw_output
from .body_setup import inspect_body_setup, rebuild_saved
from .launch import launch_game
from . import pandora_workflow as animations
from .pandora_dialog import PandoraDialog
from . import skse, skse_workflow
from .history_dialog import HistoryDialog
from . import synthesis_workflow as patching
from .synthesis_dialog import SynthesisDialog
from . import npc_workflow as appearances
from .npc_dialog import NpcDialog
from . import install_queue as queue_records
from .queue_dialog import QueueDialog
from . import pgpatcher_workflow as graphics
from .graphics_dialog import GraphicsDialog
from .documents_dialog import DocumentsDialog


class HubWindow(QDialog):
    def __init__(self, organizer, parent=None):
        super().__init__(parent)
        self.organizer = organizer
        self.snapshot = None
        self.snapshot_profile_path = None
        self.findings = ()
        self.xedit_window = None
        self.body_window = None
        self.automatic_signature = None
        self.automatic_findings = ()
        self.automatic_steps = ()
        self.processing = False
        self.installing = False
        self.install_queue = []
        self.queue_profile = None
        self.queue_completed = []
        self.queue_journal = None
        self.recheck_pending = False
        self.body_prompt_identity = None
        self.animation_prompt_identity = None
        self.launch_pending = False
        self.startup_record = None
        self.startup_timer = QTimer(self)
        self.startup_timer.setInterval(2000)
        self.startup_timer.timeout.connect(self.poll_startup)
        self.setWindowTitle("ModLab — Skyrim")
        from .hub_view import HubView
        self.view = HubView(self)
        self.refresh()
        self.change_timer = QTimer(self)
        self.change_timer.setSingleShot(True)
        self.change_timer.setInterval(2000)
        self.change_timer.timeout.connect(self.recheck_changes)
        mods, plugins = organizer.modList(), organizer.pluginList()
        for owner, signal in ((mods, 'onModInstalled'), (mods, 'onModRemoved'),
                              (mods, 'onModStateChanged'), (mods, 'onModMoved'),
                              (plugins, 'onPluginStateChanged'), (plugins, 'onPluginMoved'),
                              (organizer, 'onProfileChanged')):
            callback = getattr(owner, signal, None)
            if callback is not None:
                callback(self.setup_changed)

    def setup_changed(self, *unused):
        if self.processing:
            return  # The current run checks its resulting state, including its own sort.
        self.recheck_pending = True
        self.summary.setText('Setup changed — the previous result is out of date. Rechecking when the current operation finishes…')
        self.change_timer.start()

    def recheck_changes(self):
        if not self.recheck_pending or not self.isVisible():
            return
        if self.processing or self.installing or QApplication.activeModalWidget() is not None:
            self.change_timer.start()
            return
        # A notification establishes changed state, not permission to restore saved
        # winners. Inspect first; installation and explicit choices prepare separately.
        self.recheck_pending = False
        self.automatic_signature = None
        self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        if self.recheck_pending and hasattr(self, 'change_timer'):
            self.change_timer.start()

    def install(self):
        if self.processing or self.installing:
            return
        filenames, _ = QFileDialog.getOpenFileNames(
            self, "Choose mod archives — installers run sequentially", self.organizer.downloadsPath(),
            "Mod archives (*.zip *.7z *.rar)",
        )
        if not filenames:
            return
        try:
            queue = queue_records.create_queue(Path(self.organizer.modsPath()).parent,
                                               self.organizer.profilePath(), filenames)
            self.start_queue(queue, queue_records.resumable_items(queue))
        except Exception as error:
            QMessageBox.warning(self, 'Installation queue needs attention', str(error))

    def start_queue(self, queue, archives):
        self.queue_journal = queue
        self.install_queue = list(archives)
        self.queue_profile = queue.data['profile_path']
        self.queue_completed = [item['record'] for item in queue.data['items']
                                if item['state'] == 'Completed' and item.get('record')]
        self.install_next()

    def resume_queue(self):
        if self.processing or self.installing:
            return
        try:
            queue = queue_records.latest_unfinished(Path(self.organizer.modsPath()).parent, self.organizer.profilePath())
            if queue is None:
                QMessageBox.information(self, 'Installation queue', 'No unfinished queue exists for this profile. Earlier results remain in History.')
                return
            dialog = QueueDialog(queue, self)
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.archives:
                self.start_queue(queue, dialog.archives)
            else:
                self.refresh()
        except Exception as error:
            QMessageBox.warning(self, 'Installation queue needs attention', str(error))

    def install_next(self):
        if self.organizer.profilePath() != self.queue_profile:
            self.install_queue.clear()
            self.installing = False
            self.summary.setText('Installation queue stopped because the selected profile changed. Earlier installations are retained.')
            return
        if not self.install_queue:
            return
        filename = self.install_queue.pop(0)
        self.installing = True
        self.change_timer.stop()
        self.summary.setText(f'Installing {Path(filename).name}; {len(self.install_queue)} archives remaining. FOMOD choices remain yours.')
        self.hide()
        try:
            queue_records.begin_item(self.queue_journal, filename, self.organizer.profilePath())
            checksum, package = skse.identify(Path(filename))
            if package:
                result, record_path = skse_workflow.install_package(self.organizer, Path(filename), checksum, package,
                                                                   mobase.getFileVersion, self.install_finished)
            else:
                result, record_path = install_archive(self.organizer, Path(filename), self.install_finished)
            self.show()
            if result.status == 'Installed; activation pending':
                self.summary.setText(result.detail)
            else:
                self.install_finished(result, record_path)
        except Exception as error:
            try:
                queue_records.pause_queue(self.queue_journal, error)
            except Exception as record_error:
                error = RuntimeError(str(error) + '\nQueue status could not be saved: ' + str(record_error))
            self.install_queue.clear()
            self.installing = False
            self.show()
            self.refresh()
            QMessageBox.warning(self, "Installation needs attention", str(error) +
                '\nUse Advanced tools → Resume installation queue to continue unattempted archives.')

    def install_finished(self, result, record_path):
        try:
            queue_records.finish_item(self.queue_journal, result.status, record_path, result.detail)
        except Exception as error:
            self.install_queue.clear()
            self.installing = False
            self.show()
            self.refresh()
            QMessageBox.warning(self, 'Installation result needs attention',
                'The installer returned, but queue completion could not be recorded: ' + str(error) +
                '\nInspect the installed mod before retrying. Installer record: ' + str(record_path))
            return
        if result.status == 'Installed, enabled':
            self.queue_completed.append(str(record_path))
            if self.install_queue:
                self.installing = True
                self.summary.setText(f'{result.name} installed. Continuing with {len(self.install_queue)} queued archives…')
                QTimer.singleShot(0, self.install_next)
                return
            self.installing = False
            self.refresh()
            self.summary.setText(f'{result.name} installed. Checking dependencies and applicable tool work…')
            QTimer.singleShot(0, lambda: self.finish_setup(installation_record=record_path))
            return
        remaining = len(self.install_queue)
        self.install_queue.clear()
        self.installing = False
        self.refresh()
        message = QMessageBox(QMessageBox.Icon.Information, result.status,
                             f"{result.name}\n{result.detail}\n\nQueue stopped; {remaining} remaining archives were not installed. "
                             'Earlier completed installations are retained. Use Advanced tools → Resume installation queue '
                             'to continue the remaining archives, then recheck the resulting setup.', parent=self)
        message.setDetailedText(f"Saved installation details: {record_path}")
        message.exec()

    def finish_setup(self, checked=False, *, installation_record=None):
        if self.processing or self.installing:
            return
        self.processing = True
        self.change_timer.stop()
        self.recheck_pending = False
        self.setEnabled(False)
        self.summary.setText('Checking the selected setup and running applicable tool work…')
        try:
            skse.require_game_closed()
            if graphics.withdraw_for_upstream(self.organizer, lambda: self.begin_source_setup(installation_record)):
                return
            self.begin_source_setup(installation_record)
        except Exception as error:
            self.processing = False
            self.setEnabled(True)
            self.summary.setText('Setup check could not start: ' + str(error))

    def begin_source_setup(self, installation_record=None):
        try:
            if withdraw_output(self.organizer, lambda error: self.run_setup(installation_record, error)):
                return
            self.run_setup(installation_record)
        except Exception as error:
            self.processing = False
            self.setEnabled(True)
            self.summary.setText('Setup check could not start: ' + str(error))

    def run_setup(self, installation_record=None, preflight_error=None):
        try:
            if preflight_error:
                raise preflight_error
            result = finish_setup(self.organizer, Path(QCoreApplication.applicationDirPath()),
                                  mobase.getFileVersion, previous_signature=self.automatic_signature,
                                  previous_findings=self.automatic_findings,
                                  installation_record=installation_record)
            if installation_record and self.queue_completed:
                record = json.loads(result.record.read_text(encoding='utf-8'))
                record['installation_records'] = list(self.queue_completed)
                result.record.write_text(json.dumps(record, indent=2), encoding='utf-8')
                if self.queue_journal:
                    self.queue_journal.data['setup_record'] = str(result.record)
                    if not any(item['state'] not in {'Completed', 'Dismissed'} for item in self.queue_journal.data['items']):
                        self.queue_journal.data['status'] = 'Installations completed; setup check recorded'
                    self.queue_journal.save()
            if result.cleaned_output is not None and not any(f.code == 'workflow-incomplete' for f in result.findings):
                self.summary.setText('Applying the checked cleaned copies and checking the resulting setup…')
                self.organizer.onNextRefresh(lambda: QTimer.singleShot(0, lambda: self.cleaning_ready(result)), False)
                self.organizer.refresh()
                return
            self.continue_body_setup(result)
        except Exception as error:
            self.summary.setText('Setup check needs attention: ' + str(error))
            self.automatic_signature = None
            self.processing = False
            self.setEnabled(True)

    def cleaning_ready(self, result):
        try:
            result = complete_cleaning(self.organizer, Path(QCoreApplication.applicationDirPath()), mobase.getFileVersion, result)
            self.continue_body_setup(result)
        except Exception as error:
            self.automatic_signature = None
            self.summary.setText('Cleaning completion needs attention: ' + str(error))
            self.processing = False
            self.setEnabled(True)

    def continue_body_setup(self, result):
        if any(f.level == 'Blocked' or f.code == 'workflow-incomplete' for f in result.findings):
            self.show_setup_result(result)
            return
        try:
            plan = inspect_body_setup(self.organizer)
            if plan.groups:
                rebuild_saved(self.organizer, mobase.getFileVersion, plan.groups,
                              lambda steps, error: self.body_setup_done(result, plan, steps, error),
                              self.summary.setText)
                return
            self.body_setup_done(result, plan, (), None)
        except Exception as error:
            result.findings += (Finding('Review', 'body-build-review', 'Body and outfit setup needs attention', str(error),
                                        'Open Body and outfit choices or Setup / tool locations to resolve the reported requirement.'),)
            self.show_setup_result(result)

    def body_setup_done(self, result, plan, steps, error):
        if error or plan.choices:
            self.all_setup_done(result, plan, steps, error)
            return
        try:
            saved = animations.load_choices(self.organizer)
            if not saved and not animations.needs_behaviors(self.organizer):
                self.all_setup_done(result, plan, steps, error)
                return
            executable = animations.locate_engine(self.organizer)
            patches = animations.available_patches(self.organizer, executable)
            identity = animations.patch_identity(patches)
            if saved and saved.get('patch_identity') == identity:
                if animations.saved_build_current(self.organizer, saved):
                    result.findings += (Finding('Info', 'animation-current', 'Generated behaviors are current',
                        'The selected patch choices, source inputs and effective generated files match the saved build.',
                        'No regeneration is needed. Test intended animations in game after changing the setup.'),)
                    self.all_setup_done(result, plan, steps, error)
                    return
                self.summary.setText('Regenerating behaviors with your saved animation choices…')
                job = animations.prepare_job(self.organizer)
                animations.run_job(self.organizer, job, saved['selected'])

                def ready(target, problem):
                    if problem:
                        result.findings += (Finding('Blocked', 'animation-failed', 'Behavior generation needs attention', problem,
                            'Open Animation choices and the retained build log, resolve the reported problem and recheck.'),)
                    else:
                        result.steps += (f'Regenerated and verified behaviors with saved choices. Build: {job.directory}',)
                        if job.record.get('activated_plugins'):
                            record = json.loads(result.record.read_text(encoding='utf-8'))
                            record.update(steps=result.steps, status='Behavior plugin activated; checking updated plugin order')
                            result.record.write_text(json.dumps(record, indent=2), encoding='utf-8')
                            self.run_setup()
                            return
                    self.all_setup_done(result, plan, steps, error)

                animations.publish_job(self.organizer, job, ready)
                return
            result.findings += (Finding('Blocked', 'animation-choices', 'Animation behavior choices need review',
                'New or changed behavior patches were detected. Their options must match the installed mods.',
                'Choose the required patches in Animation choices. ModLab will remember them and handle later rebuilds.'),)
            self.all_setup_done(result, plan, steps, error)
            prompt = (self.organizer.profilePath(), identity)
            if prompt != self.animation_prompt_identity:
                self.animation_prompt_identity = prompt
                QTimer.singleShot(0, self.pandora)
        except Exception as problem:
            result.findings += (Finding('Blocked', 'animation-failed', 'Animation setup needs attention', str(problem),
                'Resolve the reported helper or output requirement, then use Recheck and finish setup. Animation choices are under Advanced tools.'),)
            self.all_setup_done(result, plan, steps, error)

    def all_setup_done(self, result, plan, steps, error):
        if not error and not plan.choices and not any(f.level == 'Blocked' for f in result.findings):
            try:
                saved = appearances.load(self.organizer)
                if saved and not appearances.saved_build_current(self.organizer, saved):
                    self.summary.setText('Rebuilding your selected NPC appearances and checking paired records and assets…')
                    job = appearances.prepare_job(self.organizer, saved['selected'])
                    appearances.run_job(self.organizer, job)
                    def ready(target, problem):
                        if problem:
                            result.findings += (Finding('Blocked', 'npc-failed', 'NPC appearance generation needs attention', problem,
                                'Open NPC appearances, resolve the reported requirement and retry.'),)
                            self.complete_setup_result(result, plan, steps, error)
                        else:
                            self.automatic_signature = None
                            self.run_setup()
                    appearances.publish_job(self.organizer, job, ready)
                    return
            except Exception as problem:
                result.findings += (Finding('Blocked', 'npc-failed', 'NPC appearance generation needs attention', str(problem),
                    'Open NPC appearances, resolve the reported requirement and retry.'),)
        self.finish_patching(result, plan, steps, error)

    def finish_patching(self, result, plan, steps, error):
        if not error and not plan.choices and not any(f.level == 'Blocked' for f in result.findings):
            try:
                saved = patching.load_choices(self.organizer)
                if saved:
                    if not patching.saved_build_current(self.organizer, saved):
                        self.summary.setText('Rebuilding your selected patches for the changed setup and checking their records…')
                        job = patching.prepare_job(self.organizer, saved['settings'], saved.get('extra_data'), saved.get('persistence'))
                        patching.run_job(self.organizer, job)
                        def ready(target, problem):
                            if problem:
                                result.findings += (Finding('Blocked', 'patching-failed', 'Selected patch output needs attention', problem,
                                    'Open Patch choices and the retained log, resolve the reported problem and recheck.'),)
                                self.complete_setup_result(result, plan, steps, error)
                            else:
                                # Publication introduces new/changed records. Re-run the existing
                                # order/dependency checks before calling this setup current.
                                self.automatic_signature = None
                                self.run_setup()
                        patching.publish_job(self.organizer, job, ready)
                        return
                    result.findings += (Finding('Info', 'patching-current', 'Selected generated patches are current',
                        'Saved patcher revisions, input files and order match the checked build; output is enabled and effective.',
                        'No rebuild is needed. Test the selected gameplay changes in game.'),)
            except Exception as problem:
                result.findings += (Finding('Blocked', 'patching-failed', 'Selected patch workflow needs attention', str(problem),
                    'Open Patch choices and the retained build log. Resolve the reported requirement and recheck.'),)
        self.finish_graphics(result, plan, steps, error)

    def finish_graphics(self, result, plan, steps, error):
        if error or plan.choices or any(f.level == 'Blocked' for f in result.findings):
            self.complete_setup_result(result, plan, steps, error)
            return
        pending = None
        def failed(problem):
            if pending:
                try:
                    if (graphics.load_pending(self.organizer) or {}).get('id') == pending['id']:
                        graphics.fail_pending(self.organizer, pending['id'], str(problem))
                except Exception as save_error:
                    problem = str(problem) + '\nCould not retain request status: ' + str(save_error)
            result.findings += (Finding('Blocked', 'graphics-failed', 'Selected graphics work needs attention', str(problem),
                'Resolve the reported requirement in Graphics choices or Setup / tool locations, then recheck. Earlier output is retained.'),)
            self.complete_setup_result(result, plan, steps, error)
        def ready(target, problem):
            if problem:
                failed(problem); return
            try:
                # Check the new plugin order once without restarting source helpers.
                # If sorting changes their inputs, report it for a bounded recheck.
                from .loot_workflow import apply_order
                run = run_loot(self.organizer, Path(QCoreApplication.applicationDirPath()), mobase.getFileVersion)
                if not any(f.level == 'Blocked' for f in run.findings) and run.proposal != run.previous:
                    apply_order(self.organizer, run.proposal, run.signature)
                result.findings = tuple(f for f in result.findings if not f.code.startswith('loot-')) + run.findings
                result.signature = context_signature(self.organizer)
                result.steps += ('Applied checked graphics after source helpers; checked final plugin order. LOOT: ' + str(run.directory),)
                saved = graphics.load_choices(self.organizer)
                if saved and not graphics.saved_build_current(self.organizer, saved):
                    raise ValueError('Final plugin sorting changed graphics inputs. Recheck once more before launching; no automatic rebuild loop was started.')
                self.complete_setup_result(result, plan, steps, error)
            except Exception as problem:
                failed(problem)
        try:
            pending, saved = graphics.load_pending(self.organizer), graphics.load_choices(self.organizer)
            if not pending and not saved:
                self.complete_setup_result(result, plan, steps, error); return
            if not pending and graphics.saved_build_current(self.organizer, saved, withdrawn=True):
                if saved['hashes']:
                    graphics.reapply_saved(self.organizer, saved, ready)
                else:
                    graphics.clear_withdrawn(self.organizer)
                    self.complete_setup_result(result, plan, steps, error)
                return
            self.summary.setText('Generating the selected graphics output after bodies and patches…')
            job = graphics.prepare_job(self.organizer, (pending or saved)['choices'])
            if pending:
                job.record['request_id'] = pending['id']; job.save()
            graphics.run_job(self.organizer, job)
            graphics.publish_job(self.organizer, job, ready)
        except Exception as problem:
            failed(problem)

    def complete_setup_result(self, result, plan, steps, error):
        try:
            setup = collect_setup(self.organizer, version_reader=mobase.getFileVersion)
            current = assess(setup)
            # Keep completed tool advice; replace observations about previous generated assets.
            result.findings = tuple(f for f in current.findings if f.code != 'cleaning-current') + tuple(f for f in result.findings
                if f.code.startswith(('loot-', 'cleaning-', 'animation-', 'patching-', 'npc-failed', 'graphics-failed'))) + plan.findings
            result.steps += tuple(steps)
            if not error and not setup.errors and not any(f.level == 'Blocked' for f in result.findings):
                from .record_checks import run_record_checks
                record_findings, record_steps = run_record_checks(self.organizer, setup, mobase.getFileVersion, self.summary.setText)
                result.findings = tuple(f for f in result.findings if not f.code.startswith('record-check') and f.code != 'record-errors') + record_findings
                result.steps += record_steps
            if error:
                result.findings += (Finding('Review', 'body-build-failed', 'Automatic body/outfit rebuild needs attention', error,
                                            'Review the retained build result, correct the reported problem and recheck. Earlier successful outputs remain recoverable.'),)
            record = json.loads(result.record.read_text(encoding='utf-8'))
            record.update(steps=result.steps, findings=[asdict(f) for f in result.findings],
                          status='Needs attention' if any(f.level in {'Blocked', 'Review', 'Unknown'} for f in result.findings)
                          else 'File checks completed; gameplay unverified')
            result.record.write_text(json.dumps(record, indent=2), encoding='utf-8')
            self.show_setup_result(result)
            identity = (self.organizer.profilePath(), plan.identity)
            if plan.choices and not error and identity != self.body_prompt_identity:
                self.body_prompt_identity = identity
                QTimer.singleShot(0, lambda: self.bodyslide(review_names=plan.choices))
            elif self.launch_pending:
                self.launch_pending = False
                if not plan.choices and not error:
                    QTimer.singleShot(0, self.launch)
        except Exception as problem:
            self.processing = False
            self.setEnabled(True)
            self.summary.setText('Body/outfit completion needs attention: ' + str(problem))

    def show_setup_result(self, result):
        try:
            blockers = [f for f in result.findings if f.level == 'Blocked' or f.code == 'workflow-incomplete']
            queue_records.record_setup(self.queue_journal, self.organizer.profilePath(), result.record,
                needs_attention=any(f.level in {'Blocked', 'Review', 'Unknown'} for f in result.findings))
            if blockers:
                self.launch_pending = False
            self.automatic_findings = result.findings
            self.automatic_steps = result.steps
            # A failed run must be retried, not cached as completed tool advice.
            self.automatic_signature = None if any(f.code == 'workflow-incomplete' for f in result.findings) else result.signature
            self.refresh()
            self.findings = tuple(sorted(result.findings, key=lambda f: {'Blocked': 0, 'Unknown': 1, 'Review': 2, 'Info': 3}.get(f.level, 1)))
            self.table.setRowCount(len(self.findings))
            for row, finding in enumerate(self.findings):
                for column, value in enumerate((finding.level, finding.title, finding.action)):
                    self.table.setItem(row, column, QTableWidgetItem(value))
            if blockers:
                # A previous search/scroll position must not hide why launch stopped.
                self.search.clear()
            self.filter_rows()
            self.table.scrollToTop()
            completed = 'Completed steps\n\n' + '\n'.join(result.steps) + f'\n\nSaved result: {result.record}'
            if blockers:
                self.detail.setPlainText('Setup stopped — resolve these problems\n\n' +
                    '\n\n'.join(f.title + '\nNext action: ' + f.action for f in blockers) + '\n\n' + completed)
            else:
                self.detail.setPlainText(completed)
            self.view.render(self.findings, self.snapshot,
                checked=self.automatic_signature is not None)
        except Exception as error:
            self.automatic_signature = None
            self.summary.setText(f'Automatic checks need attention: {error}')
        finally:
            self.processing = False
            self.setEnabled(True)

    def loot(self):
        self.hide()
        try:
            run = run_loot(self.organizer, Path(QCoreApplication.applicationDirPath()), mobase.getFileVersion)
            self.show()
            LootDialog(self.organizer, run, self).exec()
        except Exception as error:
            self.show()
            QMessageBox.warning(self, 'LOOT needs attention', str(error))
        finally:
            self.refresh()

    def helpers(self):
        HelperDialog(self.organizer, self).exec()

    def synthesis(self):
        if self.processing or self.installing: return
        try:
            self.patching_window = SynthesisDialog(self.organizer, self)
            self.patching_window.finished.connect(lambda _, d=self.patching_window: self.dialog_closed(d))
            self.patching_window.setModal(True)
            self.patching_window.show()
        except Exception as error:
            QMessageBox.warning(self, 'Patch choices need attention', str(error))

    def npc_appearances(self):
        if self.processing or self.installing: return
        try:
            self.npc_window = NpcDialog(self.organizer, self)
            self.npc_window.finished.connect(lambda _, d=self.npc_window: self.dialog_closed(d))
            self.npc_window.setModal(True); self.npc_window.show()
        except Exception as error:
            QMessageBox.warning(self, 'NPC appearances need attention', str(error))

    def pandora(self):
        try:
            job = animations.prepare_job(self.organizer, preview=True)
            self.animation_window = PandoraDialog(self.organizer, job, self)
            self.animation_window.finished.connect(lambda _, d=self.animation_window: self.dialog_closed(d))
            self.animation_window.setModal(True)
            self.animation_window.show()
            self.animation_window.raise_()
            self.animation_window.activateWindow()
        except Exception as error:
            QMessageBox.warning(self, 'Animation setup needs attention', str(error))

    def launch(self):
        if self.processing or self.installing:
            return
        try:
            if self.automatic_signature != context_signature(self.organizer):
                self.launch_pending = True
                self.finish_setup()
                return
            blockers = [f for f in self.automatic_findings if f.level == 'Blocked' or f.code == 'workflow-incomplete']
            if blockers:
                self.launch_pending = False
                raise ValueError('\n\n'.join(f.title + '\n' + f.action for f in blockers))
            launcher, record = launch_game(self.organizer, mobase.getFileVersion)
            self.startup_record = record
            if launcher.executable.name.casefold() == 'skse64_loader.exe':
                self.startup_timer.start()
            self.summary.setText(launcher.label + ' started. Gameplay and feature checks remain separate from file checks.')
            self.detail.setPlainText('Launcher: ' + str(launcher.executable) + '\nSaved launch record: ' + str(record))
        except Exception as error:
            self.view.navigate(0)
            self.summary.setText('Launch needs attention: ' + str(error))
            self.detail.setPlainText(str(error))

    def poll_startup(self):
        if not self.startup_record:
            self.startup_timer.stop(); return
        if self.processing or self.installing:
            return
        try:
            from .startup import update_startup
            if update_startup(self.organizer, self.startup_record,
                              lambda: collect_setup(self.organizer, version_reader=mobase.getFileVersion)):
                self.startup_timer.stop()
                self.refresh()
        except Exception as error:
            self.startup_timer.stop()
            self.summary.setText('Startup reporting needs attention: ' + str(error))

    def xedit(self):
        if self.xedit_window is None:
            self.xedit_window = XEditDialog(self.organizer, self)
            self.xedit_window.finished.connect(self.refresh)
        self.xedit_window.refresh_plugins()
        self.xedit_window.setModal(True)
        self.xedit_window.show()
        self.xedit_window.raise_()
        self.xedit_window.activateWindow()

    def bodyslide(self, checked=False, *, review_names=None):
        try:
            job = prepare_body_job(self.organizer, mobase.getFileVersion, preview=True)
            self.body_window = BodyDialog(self.organizer, job, self, review_names=review_names)
            self.body_window.finished.connect(lambda _, d=self.body_window: self.dialog_closed(d))
            self.body_window.setModal(True)
            self.body_window.show()
            self.body_window.raise_()
            self.body_window.activateWindow()
        except Exception as error:
            QMessageBox.warning(self, 'BodySlide needs attention', str(error))

    def dialog_closed(self, dialog):
        from .dialog_workflow import finish_dialog
        finish_dialog(self, dialog, lambda action: QTimer.singleShot(0, action))

    def resolve_finding(self, finding):
        if self.processing or self.installing or self.snapshot is None:
            return
        from .resolution_dialog import ResolutionDialog
        from .resolution import enable_dependency
        try:
            if self.snapshot_profile_path != self.organizer.profilePath():
                raise ValueError('The selected profile changed. Recheck its findings before taking action.')
            dialog = ResolutionDialog(self.organizer, finding, self.snapshot, self)
            if not dialog.exec():
                return
            if Path(self.organizer.profilePath()).resolve() != Path(dialog.profile_path).resolve():
                raise ValueError('The selected profile changed. Recheck its requirements before continuing.')
            if dialog.next_action == 'enable':
                self.processing = True
                try:
                    enable_dependency(self.organizer, dialog.context.dependency,
                        dialog.context.dependency_provider, dialog.profile_path, mobase.PluginState.ACTIVE)
                finally:
                    self.processing = False
                self.automatic_signature = None
                self.finish_setup()
            elif dialog.next_action == 'install':
                self.install()
            elif dialog.next_action == 'documents':
                self.documents(provider=dialog.provider)
            elif dialog.next_action == 'recheck':
                self.finish_setup()
        except Exception as error:
            QMessageBox.warning(self, 'Setup requirement needs attention', str(error))
            self.refresh()

    def documents(self, provider=None):
        if self.processing or self.installing:
            return
        try:
            DocumentsDialog(self.organizer, self, provider=provider).exec()
        except Exception as error:
            QMessageBox.warning(self, 'Mod instructions unavailable', str(error))

    def history(self):
        if self.processing or self.installing:
            return
        self.processing = True
        dialog = None
        try:
            dialog = HistoryDialog(self.organizer, self)
            dialog.exec()
        except Exception as error:
            QMessageBox.warning(self, 'History needs attention', str(error))
        finally:
            self.processing = False
            self.change_timer.stop()
            self.recheck_pending = False
            if dialog and dialog.changed:
                self.automatic_signature = None
                self.automatic_findings = ()
                self.automatic_steps = ()
            self.refresh()

    def graphics(self):
        if self.processing or self.installing: return
        try:
            self.graphics_window = GraphicsDialog(self.organizer, self)
            self.graphics_window.accepted.connect(lambda: QTimer.singleShot(0, self.finish_setup))
            self.graphics_window.setModal(True)
            self.graphics_window.show()
        except Exception as error:
            QMessageBox.warning(self, 'Graphics choices need attention', str(error))

    def refresh(self):
        # Never leave a previous profile's successful report visible after a failure.
        self.snapshot = None
        self.snapshot_profile_path = None
        self.findings = ()
        self.table.setRowCount(0)
        self.detail.clear()
        try:
            inspected_profile = self.organizer.profilePath()
            snapshot = collect_setup(self.organizer, version_reader=mobase.getFileVersion)
            if self.organizer.profilePath() != inspected_profile:
                raise ValueError('The profile changed during inspection. Recheck the selected profile.')
            self.snapshot_profile_path = inspected_profile
            result = assess(snapshot)
            self.snapshot = snapshot
            self.findings = result.findings
            if self.automatic_signature is not None and context_signature(self.organizer) == self.automatic_signature:
                from .guidance import retain_run_findings
                self.findings = retain_run_findings(self.findings, self.automatic_findings)
            self.findings = tuple(sorted(self.findings, key=lambda f: {'Blocked': 0, 'Unknown': 1, 'Review': 2, 'Info': 3}.get(f.level, 1)))
            enabled = sum(p.load_order >= 0 for p in snapshot.plugins)
            self.context.setText(
                f"Profile: {snapshot.profile}  |  Skyrim: {snapshot.runtime or 'unidentified'}  |  "
                f"Active plugins: {enabled}"
            )
            self.summary.setText(result.summary)
            self.table.setRowCount(len(self.findings))
            for row, finding in enumerate(self.findings):
                for column, value in enumerate((finding.level, finding.title, finding.action)):
                    self.table.setItem(row, column, QTableWidgetItem(value))
            self.filter_rows()
            self.view.render(self.findings, snapshot,
                checked=self.automatic_signature is not None and context_signature(self.organizer) == self.automatic_signature)
        except Exception as error:
            self.findings = (Finding('Unknown', 'inspection-incomplete', 'Current setup could not be read',
                str(error), 'Resolve this inspection problem before continuing.'),)
            self.view.render(self.findings, None)
            self.context.setText("Current setup could not be read.")
            self.summary.setText(f"Inspection incomplete: {error}")

    def filter_rows(self):
        text = self.search.text().casefold()
        for row, finding in enumerate(self.findings):
            self.table.setRowHidden(row, text not in
                                    f"{finding.level} {finding.title} {finding.detail} {finding.action} {finding.explanation}".casefold())

    def show_selection(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.findings):
            self.view.select(self.findings[row])

    def open_mods(self):
        path = Path(self.organizer.modsPath())
        if path.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:
            QMessageBox.warning(self, "Mods folder unavailable", str(path))

    def save_report(self):
        if self.snapshot is None:
            QMessageBox.warning(self, "No current report", "Recheck the setup first.")
            return
        reports = Path(self.organizer.modsPath()).parent / 'reports'
        reports.mkdir(parents=True, exist_ok=True)
        filename, _ = QFileDialog.getSaveFileName(self, "Save ModLab report", str(reports / 'ModLab report.json'), "JSON (*.json)")
        if filename:
            try:
                Path(filename).write_text(json.dumps({
                    "setup": asdict(self.snapshot),
                    "findings": [asdict(f) for f in self.findings],
                    "runtime_and_features_verified": False,
                }, indent=2), encoding="utf-8")
            except OSError as error:
                QMessageBox.warning(self, "Report could not be saved", str(error))


class ModLabHub(mobase.IPluginTool):
    def __init__(self):
        super().__init__()
        self.organizer = None
        self.parent = None
        self.window = None
        self.archive_refresh_pending = False

    def init(self, organizer):
        self.organizer = organizer
        return True

    def name(self):
        return "ModLab Hub"

    def localizedName(self):
        return self.name()

    def author(self):
        return "ModLab"

    def description(self):
        return "Understand the selected Skyrim setup and coordinate its modding workflow."

    def version(self):
        return mobase.VersionInfo(0, 1, 0, mobase.ReleaseType.ALPHA)

    def settings(self):
        return []

    def displayName(self):
        return "ModLab"

    def tooltip(self):
        return self.description()

    def icon(self):
        return QIcon()

    def setParentWidget(self, parent):
        self.parent = parent

    def display(self):
        if self.archive_refresh_pending:
            return
        try:
            from .archives import enable_portable_index
            if enable_portable_index(self.organizer, QCoreApplication.applicationDirPath()):
                self.archive_refresh_pending = True
                def ready():
                    if self.archive_refresh_pending:
                        self.archive_refresh_pending = False
                        QTimer.singleShot(0, self.display)
                self.organizer.pluginList().onRefreshed(ready)
                self.organizer.refresh()
                return
        except Exception as error:
            QMessageBox.warning(self.parent, 'Archive inspection needs attention', str(error))
        if self.window is None:
            self.window = HubWindow(self.organizer, self.parent)
        else:
            self.window.refresh()
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
