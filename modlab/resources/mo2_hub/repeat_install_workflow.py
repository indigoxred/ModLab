"""Offer existing verified choices before opening a duplicate native installer."""
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import mobase
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QMessageBox

from .installation import InstallResult, capture_files, file_stamp, find_existing
from .native_install import install_archive as native_install, write_record
from .skse import require_game_closed

PENDING = 'Checking existing installation'


def install_archive(organizer, archive, on_ready, *, on_progress=None, parent=None):
    require_game_closed()
    archive = Path(archive).resolve(strict=True)
    stamp = file_stamp(archive)
    profile = organizer.profilePath()
    mods_root = Path(organizer.modsPath()).resolve(strict=True)
    history = Path(organizer.getPluginDataPath())/'modlab/installations'
    steps = find_existing(history, archive, mods_root, profile)
    ticks = 0
    candidate = None
    phase = 'find'
    cancelled = False

    def context():
        if cancelled or organizer.profilePath() != profile or Path(organizer.modsPath()).resolve() != mods_root:
            raise ValueError('Installation stopped because the window or selected profile changed. Existing files were retained.')
        if file_stamp(archive) != stamp:
            raise ValueError('The archive changed while checking previous choices. Add the current download again.')

    def disconnect():
        if parent is not None:
            try:parent.finished.disconnect(cancel)
            except (TypeError, RuntimeError):pass

    def done(result, path=None):
        disconnect()
        on_ready(result, path)

    def cancel(*unused):
        nonlocal cancelled
        cancelled = True

    def failed(error):
        steps.close()
        done(InstallResult('Installation needs attention', archive.stem, '', 0, str(error)))

    def run_native():
        context()
        disconnect()
        if parent is not None:parent.hide()
        try:
            result, path = native_install(organizer, archive, on_ready, on_progress=on_progress)
        finally:
            if parent is not None:parent.show()
        if result.status != 'Installed; activation pending':
            on_ready(result, path)

    def offer(found):
        nonlocal candidate, phase, steps
        if found is None:
            run_native()
            return
        candidate = found
        mods = organizer.modList()
        mod = mods.getMod(found['name'])
        if mod is None or Path(mod.absolutePath()).resolve() != Path(found['target']).resolve():
            run_native()
            return
        active = bool(mods.state(found['name']) & mobase.ModState.ACTIVE)
        message = QMessageBox(parent)
        message.setWindowTitle('ModLab — This mod is already installed')
        message.setText(found['name'] + '\n\nThis download matches the previous installation, and all its installed files are unchanged.')
        message.setInformativeText('Keep your current installer choices, or run the installer again to choose different options. '
            + ('' if active else 'Using the existing installation will enable this mod in the selected profile. ')
            + 'ModLab will check dependencies and preparation afterward.')
        keep = message.addButton('Keep my current choices', QMessageBox.ButtonRole.AcceptRole)
        change = message.addButton('Run installer again', QMessageBox.ButtonRole.ActionRole)
        message.addButton(QMessageBox.StandardButton.Cancel)
        message.setDefaultButton(keep)
        message.exec()
        context()
        if message.clickedButton() is change:
            run_native()
        elif message.clickedButton() is keep:
            # User deliberation runs a nested event loop. Recheck the bytes;
            # never overwrite edits made while this question was open.
            phase = 'keep'
            steps = capture_files(archive, Path(found['target']), mods_root, stamp)
            QTimer.singleShot(0, advance)
        else:
            done(InstallResult('Cancelled', found['name'], found['target'], 0,
                               'No files or saved installer choices were changed. The queued request remains available.'))

    def keep(verification):
        context()
        require_game_closed()
        expected = candidate['verification']
        if verification['archive_sha256'] != expected['archive_sha256'] or verification['files'] != expected['files']:
            raise ValueError('Installed files changed while choosing. They were retained; review the current setup before retrying.')
        mods = organizer.modList()
        mod = mods.getMod(candidate['name'])
        if mod is None or Path(mod.absolutePath()).resolve() != Path(candidate['target']).resolve():
            raise ValueError('The installed mod changed in MO2. Recheck the selected profile.')
        now = datetime.now(timezone.utc).isoformat()
        record = dict(archive=str(archive), profile=organizer.profile().name(), profile_path=profile,
            mods_root=str(mods_root), started_at=now, status='Verified; activation pending',
            file_verification=verification, reused_installation_record=candidate['record'])
        history.mkdir(parents=True, exist_ok=True)
        path = history/(uuid4().hex+'.json')
        write_record(path, record)
        active = bool(mods.state(candidate['name']) & mobase.ModState.ACTIVE)
        if not active:
            active = bool(mods.setActive(candidate['name'], True))
        result = InstallResult('Installed, enabled' if active else 'Installed, disabled', candidate['name'],
            candidate['target'], len(verification['files']),
            'Your existing installation and installer choices were kept. All recorded files still match. '
            'Dependencies and preparation are checked next.')
        record.update(status=result.status, result=asdict(result), finished_at=datetime.now(timezone.utc).isoformat())
        write_record(path, record)
        done(result, path)

    def advance():
        nonlocal ticks
        try:
            context()
            text = next(steps)
            ticks += 1
            if on_progress and (ticks == 1 or ticks % 16 == 0):on_progress(text)
            QTimer.singleShot(0, advance)
        except StopIteration as result:
            try:
                if phase == 'find':offer(result.value)
                else:keep(result.value)
            except Exception as error:failed(error)
        except Exception as error:failed(error)

    if parent is not None:parent.finished.connect(cancel)
    QTimer.singleShot(0, advance)
    return InstallResult(PENDING, archive.stem, '', 0, 'Checking whether your current installation and choices can be kept…'), None
