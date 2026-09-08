"""Use MO2's installer and its destination-specific backup on merge/replace."""

import json
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import mobase
from PyQt6.QtCore import QCoreApplication, QEvent, QObject, QTimer
from PyQt6.QtWidgets import QApplication, QCheckBox, QDialog

from .installation import inspect_install_result, capture_files, file_stamp, write_record


class NativeBackupOption(QObject):
    """Keep the native backup option selected during this install only (MO2 2.5.2)."""
    def __init__(self):
        super().__init__()
        self.dialogs = 0
        self.errors = []

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Show and watched.metaObject().className() == "QueryOverwriteDialog":
            self.dialogs += 1
            try:
                boxes = watched.findChildren(QCheckBox)
                if len(boxes) != 1:
                    raise RuntimeError("Native replacement backup control could not be identified.")
                boxes[0].setChecked(True)
                boxes[0].setEnabled(False)
                boxes[0].setToolTip("ModLab keeps a backup before merging or replacing an installed mod.")
            except Exception as error:
                self.errors.append(str(error))
                if isinstance(watched, QDialog):
                    QTimer.singleShot(0, watched.reject)
        return False


def install_archive(organizer, archive: Path, on_ready=None, *, on_progress=None):
    from .skse import require_game_closed
    require_game_closed()
    archive = archive.resolve(strict=True)
    if archive.suffix.casefold() not in {".zip", ".7z", ".rar"} or not archive.is_file():
        raise ValueError("Select a ZIP, 7z or RAR mod archive.")
    if organizer.managedGame().gameName() != "Skyrim Special Edition":
        raise ValueError("Select a Skyrim Special Edition instance before installing.")
    host_version = mobase.getFileVersion(QCoreApplication.applicationFilePath())
    if host_version not in {"2.5.2", "2.5.2.0"}:
        raise ValueError(f"Native installation requires MO2 2.5.2; detected {host_version or 'unknown'}.")
    profile = organizer.profile().name()
    profile_path = organizer.profilePath()
    source_stamp = file_stamp(archive)
    mods_root = Path(organizer.modsPath()).resolve(strict=True)
    before = sorted(p.name for p in mods_root.iterdir() if p.is_dir())
    history = Path(organizer.getPluginDataPath()) / "modlab" / "installations"
    history.mkdir(parents=True, exist_ok=True)
    record_path = history / f"{uuid4().hex}.json"
    record = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "archive": str(archive), "profile": profile, "profile_path": profile_path, "mods_root": str(mods_root),
        "existing_mods": before, "status": "Installing",
        "scope": "Native MO2 archive installation. Game files, saves and arbitrary external installers are not covered.",
    }
    write_record(record_path, record)
    backup_option = NativeBackupOption()
    application = QApplication.instance()
    application.installEventFilter(backup_option)
    refresh_callback = None
    try:
        mod = organizer.installMod(str(archive), archive.stem)
        if backup_option.errors:
            raise RuntimeError("; ".join(backup_option.errors))
        if organizer.profilePath() != profile_path or Path(organizer.modsPath()).resolve() != mods_root:
            raise RuntimeError("Selected profile or mods directory changed during installation; recheck before enabling.")
        # Copy the returned metadata now. MO2 refresh may replace its native ModInfo object.
        result = inspect_install_result(mod, mods_root, False)
        if result.path and result.file_count:
            installed = result
            result = replace(result, status='Installed; activation pending',
                             detail='Checking the installed files before enabling them. ModLab will continue when verification finishes.')

            verification = capture_files(archive, Path(installed.path), mods_root, source_stamp)
            ticks = 0

            def complete(final):
                record.update(status=final.status, result=asdict(final),
                              finished_at=datetime.now(timezone.utc).isoformat())
                try:
                    write_record(record_path, record)
                except OSError as error:
                    final = replace(final, status='Installation record needs attention',
                                    detail='Files were retained, but verification could not be saved: ' + str(error))
                if on_ready:
                    on_ready(final, record_path)

            def activate():
                try:
                    require_game_closed()
                    if organizer.profilePath() != profile_path or Path(organizer.modsPath()).resolve() != mods_root:
                        raise ValueError('The selected profile changed. Enable the installed mod in the intended profile.')
                    active = organizer.modList().setActive(installed.name, True)
                    count = len(record['file_verification']['files'])
                    final = replace(installed, status='Installed, enabled' if active else 'Installed, disabled',
                                    file_count=count, detail=f'{count} installed files verified and recorded. '
                                    'Dependencies, effective file winners and preparation are checked next.')
                except Exception as error:
                    final = replace(installed, status='Activation needs attention', detail=str(error))
                complete(final)

            def verify_next():
                nonlocal ticks
                try:
                    if organizer.profilePath() != profile_path or Path(organizer.modsPath()).resolve() != mods_root:
                        raise ValueError('The selected profile changed. Files were retained; verify them in the original profile.')
                    message = next(verification)
                    ticks += 1
                    if on_progress and (ticks == 1 or ticks % 16 == 0):
                        on_progress('Verifying ' + installed.name + ': ' + message)
                    QTimer.singleShot(0, verify_next)
                except StopIteration as done:
                    record['file_verification'] = done.value
                    try:
                        record['status'] = 'Verified; activation pending'
                        write_record(record_path, record)
                    except OSError as error:
                        complete(replace(installed, status='Installation record needs attention', detail=str(error)))
                        return
                    activate()
                except Exception as error:
                    verification.close()
                    complete(replace(installed, status='Installation verification needs attention',
                                     detail=str(error) + ' Installed files and native backups were retained.'))

            # Return to MO2's event loop; do not re-enter its refresh with a nested wait.
            refresh_callback = lambda: QTimer.singleShot(0, verify_next)
        record["status"] = result.status
        record["result"] = asdict(result)
    except Exception as error:
        record["status"] = "Needs attention"
        record["error"] = str(error)
        raise
    finally:
        application.removeEventFilter(backup_option)
        record["replacement_dialogs"] = backup_option.dialogs
        record["new_directories"] = sorted(p.name for p in mods_root.iterdir()
                                             if p.is_dir() and p.name not in before)
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_record(record_path, record)
    # A failed save must not leave a callback that enables files after the
    # caller has paused the queue or started recovering another installation.
    if refresh_callback:
        organizer.onNextRefresh(refresh_callback)
    return result, record_path
