"""Browse retained operations and restore checked managed-output snapshots."""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

from .outputs import MANIFEST, digest, read_manifest


@dataclass(frozen=True)
class HistoryEntry:
    path: Path
    time: str
    operation: str
    status: str
    scope: str
    subject: str
    record: dict


def read_history(instance, plugin_data, profile_path, profile_name):
    instance, plugin_data = Path(instance), Path(plugin_data)
    paths = list((instance / 'builds').glob('*/*/operation.json'))
    paths += list((instance / 'reports').glob('*/*/operation.json'))
    paths += list((plugin_data / 'modlab/loot').glob('*/operation.json'))
    paths += list((plugin_data / 'modlab/installations').glob('*.json'))
    entries = []
    for path in sorted(set(paths), key=lambda p: p.stat().st_mtime, reverse=True):
        kind = 'Installation' if path.parent.name == 'installations' else path.parent.parent.name
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(record, dict):
                raise ValueError('Expected an operation record')
            scope = ('This profile' if record['profile_path'] == profile_path else 'Other profile') if 'profile_path' in record else (
                'Profile name matches' if record.get('profile') == profile_name else 'Instance record')
            subject = record.get('installed_mod') or record.get('plugin') or record.get('target') or record.get('archive') or ''
            if not subject and isinstance(record.get('result'), dict):
                subject = record['result'].get('name', '')
            stamp = record.get('finished_at') or record.get('created_at') or record.get('started_at')
            stamp = stamp or datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
            entries.append(HistoryEntry(path, stamp, kind, record.get('status', 'Recorded'), scope, str(subject), record))
        except (OSError, ValueError, TypeError) as error:
            entries.append(HistoryEntry(path, '', kind, 'Unreadable record', 'Instance record', str(error), {}))
    return tuple(entries)


def _plain_tree(path):
    if any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()) for p in [path, *path.rglob('*')]):
        raise ValueError('A linked path was found in the output or backup. Automatic restoration was stopped.')


def synthesis_recovery_choices(instance, profile_path, manifest):
    """Require the settings and allocation bytes retained with this exact patch build."""
    from .synthesis_workflow import settings_hashes
    projects = list(manifest['projects'].values())
    records = {p['build_record'] for p in projects}
    checksums = {p.get('recovery_context_sha256') for p in projects}
    if len(records) != 1 or len(checksums) != 1 or None in checksums:
        raise ValueError('This older patch build has no complete recovery context. Rebuilds now retain it; do not guess its settings or FormID allocations.')
    job = Path(next(iter(records))).parent.resolve()
    job.relative_to(Path(instance).resolve() / 'builds/synthesis')
    _plain_tree(job)
    context = job / 'recovery-context.json'
    if digest(context) != next(iter(checksums)):
        raise ValueError('Retained patch recovery context changed.')
    data = json.loads(context.read_text(encoding='utf-8'))
    choices = data['choices']
    if choices['input_state']['profile_path'] != profile_path or choices['output_hashes'] != manifest['hashes']:
        raise ValueError('Retained patch choices belong to another profile or output build.')
    for name, folder in [('extra_data', 'Data'), ('persistence', 'persistence')]:
        if Path(choices[name]).resolve() != job / folder or not (job / folder).is_dir():
            raise ValueError('Retained patch settings or allocation folder is missing or belongs to another build.')
    if settings_hashes(job / 'Data') != choices['settings_hashes']:
        raise ValueError('Retained patch settings changed; restoration was stopped.')
    if settings_hashes(job / 'persistence') != data['persistence_hashes']:
        raise ValueError('Retained FormID allocation data changed; restoration was stopped.')
    return choices


def npc_recovery_choices(instance, profile_path, manifest):
    projects = list(manifest['projects'].values())
    records = {p['build_record'] for p in projects}
    checksums = {p.get('recovery_context_sha256') for p in projects}
    if len(records) != 1 or len(checksums) != 1 or None in checksums:
        raise ValueError('This older NPC build has no complete recovery context. Choose the intended appearances and regenerate instead.')
    job = Path(next(iter(records))).parent.resolve()
    job.relative_to(Path(instance).resolve() / 'builds/npc')
    _plain_tree(job)
    context = job / 'recovery-context.json'
    if digest(context) != next(iter(checksums)):
        raise ValueError('Retained NPC recovery context changed.')
    choices = json.loads(context.read_text(encoding='utf-8'))
    if (choices['input_state']['profile_path'] != profile_path or choices['hashes'] != manifest['hashes'] or
            Path(choices['build_record']).resolve() != job / 'operation.json' or not choices['selected']):
        raise ValueError('Retained NPC choices do not match this profile or output build.')
    return choices


def restore_previous_output(target, profile_path, tool, expected_manifest, *, expected_choices=None):
    from .skse import require_game_closed
    require_game_closed()
    target = Path(target).absolute()
    instance = target.parent.parent.resolve()
    if target.parent.resolve() != instance / 'mods':
        raise ValueError('Restore requires a managed mod inside this instance.')
    _plain_tree(target)
    if digest(target / MANIFEST) != expected_manifest:
        raise ValueError('The selected output changed. Refresh history before restoring.')
    current = read_manifest(target, profile_path, tool)
    coordinated = tool in ('Synthesis', 'NPC Appearance')
    choice_kind = 'npc' if tool == 'NPC Appearance' else 'patcher'
    choices_path = Path(profile_path) / ('modlab-' + choice_kind + '-choices.json')
    recovery_choices = npc_recovery_choices if tool == 'NPC Appearance' else synthesis_recovery_choices
    if coordinated:
        Path(profile_path).resolve().relative_to(instance / 'profiles')
        if (Path(profile_path) / ('modlab-' + choice_kind + '-pending.json')).exists():
            raise ValueError('Resolve or cancel the pending patch request before restoring a previous build.')
        if choices_path.is_symlink() or digest(choices_path) != expected_choices:
            raise ValueError('Saved patch choices changed. Refresh history before restoring.')
    backup = Path(current['previous_output']).absolute()
    backup.resolve().relative_to(instance / 'builds')
    if backup.name != 'previous-output' or not backup.is_dir():
        raise ValueError('The previous output backup is missing.')
    _plain_tree(backup)
    previous = read_manifest(backup, profile_path, tool)
    choices = recovery_choices(instance, profile_path, previous) if coordinated else None
    job = instance / 'builds/recovery' / uuid4().hex[:12]
    job.mkdir(parents=True)
    pending, retained = job / 'pending-output', job / 'previous-output'
    record_path = job / 'operation.json'
    record = dict(created_at=datetime.now(timezone.utc).isoformat(), profile_path=profile_path,
                  tool=tool, target=str(target), previous_output=str(backup), retained_output=str(retained),
                  status='Preparing restoration', restored_hashes=previous['hashes'])
    def save():
        record_path.write_text(json.dumps(record, indent=2), encoding='utf-8')
    save()
    try:
        if choices is not None:
            choices = dict(choices)
            if tool == 'Synthesis':
                from .synthesis_workflow import copy_settings
                for name, folder in [('extra_data', 'Data'), ('persistence', 'persistence')]:
                    copy_settings(choices[name], job / folder)
                    choices[name] = str(job / folder)
            (job / 'previous-choices.json').write_bytes(choices_path.read_bytes())
            (job / 'restored-choices.json').write_text(json.dumps(choices, indent=2), encoding='utf-8')
            record.update(retained_choices=str(job / 'previous-choices.json'), restored_choices=str(choices_path))
            save()
        shutil.copytree(backup, pending)
        if (target / 'meta.ini').is_file():
            shutil.copy2(target / 'meta.ini', pending / 'meta.ini')
        if read_manifest(pending, profile_path, tool) != previous:
            raise ValueError('The retained output changed during restoration preparation.')
        restored = dict(previous, previous_output=str(retained), updated_at=datetime.now(timezone.utc).isoformat())
        (pending / MANIFEST).write_text(json.dumps(restored, indent=2), encoding='utf-8')
        read_manifest(pending, profile_path, tool)
        if read_manifest(target, profile_path, tool) != current or digest(target / MANIFEST) != expected_manifest:
            raise ValueError('The current output changed during restoration preparation.')
        if choices is not None:
            recovery_choices(instance, profile_path, previous)
            if digest(choices_path) != expected_choices:
                raise ValueError('Saved patch choices changed during restoration preparation.')
        # Both resolved move targets are inside this verified instance/job.
        os.replace(target, retained)
        try:
            os.replace(pending, target)
        except OSError:
            os.replace(retained, target)
            raise
        if choices is not None:
            try:
                os.replace(job / 'restored-choices.json', choices_path)
            except OSError:
                os.replace(target, pending)
                os.replace(retained, target)
                raise
        record['status'] = 'Previous output restored; effective check pending'
        return record_path
    except Exception as error:
        record.update(status='Restoration needs attention', error=str(error))
        raise
    finally:
        save()
