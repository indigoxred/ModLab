"""Apply explicit body-controller settings as a recoverable MO2 output mod."""
import json
from pathlib import Path
import re
from uuid import uuid4

from .outputs import digest, output_name, publish_output, read_manifest
from .vfs import readable_path

RELATIVE = 'SKSE/Plugins/skee64.ini'
TOOL = 'Character Shapes'
CHOICES = {'enable-morphs': ('bEnableBodyMorph', '1'), 'use-obody': ('bEnableBodyGen', '0')}


def feature_values(text):
    """Read explicit controller flags, accepting indented Windows INI keys."""
    section = None; features = 0; result = {}
    for line in text.splitlines():
        heading = re.match(r'^\ufeff?\s*\[([^]]+)\]\s*(?:[;#].*)?$', line)
        if heading:
            section = heading[1].casefold()
            if section == 'features': features += 1
            continue
        match = re.match(r'^\s*(bEnableBodyMorph|bEnableBodyGen)\s*=(.*)$', line, re.I)
        if section != 'features' or not match: continue
        key = match[1].casefold()
        value = re.split('[;#]', match[2], maxsplit=1)[0].strip()
        if key in result or value not in {'0', '1'}:
            raise ValueError('RaceMenu body-controller setting is ambiguous: '+match[1])
        result[key] = value
    if features != 1:
        raise ValueError('RaceMenu Features section is missing or ambiguous.')
    return result


def prepare_settings(text, action):
    if action not in CHOICES:
        raise ValueError('Unknown body-controller choice.')
    if len(text.encode('utf-8')) > 2 * 1024 * 1024:
        raise ValueError('RaceMenu settings exceed the inspection limit.')
    key, value = CHOICES[action]
    if key.casefold() not in feature_values(text):
        raise ValueError('The selected RaceMenu setting is missing or ambiguous.')
    section = None; changed = 0; result = []
    pattern = re.compile(r'^(\s*'+key+r'\s*=\s*)([01])(?=\s|$)', re.I)
    for line in text.splitlines(keepends=True):
        heading = re.match(r'^\ufeff?\s*\[([^]]+)\]', line)
        if heading: section = heading[1].casefold()
        if section == 'features' and pattern.match(line):
            line = pattern.sub(lambda match: match[1]+value, line, count=1)
            changed += 1
        result.append(line)
    if changed != 1:
        raise ValueError('The selected setting could not be changed without rewriting other configuration.')
    return ''.join(result)


def apply_settings(organizer, action, expected_path, expected_hash, on_ready):
    """Publish one selected setting; callback only after checking its effective file.

No source mod is edited. Errors retain a job/backup for recovery and never report
an unverified activation as applied. Existing managed output edits are protected
by publish_output. The calling hub holds its operation guard until on_ready.
"""
    import mobase
    from PyQt6.QtCore import QTimer
    from .skse import require_game_closed
    require_game_closed()
    profile = organizer.profilePath()
    source = Path(organizer.resolvePath(RELATIVE))
    if source.resolve() != Path(expected_path).resolve() or digest(readable_path(source)) != expected_hash:
        raise ValueError('Body settings changed while this choice was open. Recheck before applying it.')
    with readable_path(source).open('r', encoding='utf-8', newline='') as stream:
        text = stream.read(2 * 1024 * 1024 + 1)
    prepared = prepare_settings(text, action)
    instance = Path(organizer.modsPath()).parent
    directory = instance/'builds/character-shapes'/uuid4().hex[:12]
    output = directory/'output'/RELATIVE
    output.parent.mkdir(parents=True)
    backup = directory/'source-settings.ini'
    backup.write_bytes(text.encode('utf-8'))
    if digest(backup) != expected_hash:
        raise ValueError('The source settings snapshot does not match the inspected file.')
    output.write_bytes(prepared.encode('utf-8'))
    name = output_name(organizer.profile().name(), profile, TOOL)
    target = Path(organizer.modsPath())/name
    record_path = directory/'operation.json'
    record = dict(profile_path=profile, action=action, source=str(source), source_sha256=expected_hash, source_backup=str(backup),
                  installed_mod=name, installed_path=str(target), status='Prepared', hashes={RELATIVE:digest(output)})
    def save(): record_path.write_text(json.dumps(record, indent=2), encoding='utf-8')
    def context():
        require_game_closed()
        if organizer.profilePath() != profile:
            raise ValueError('The selected profile changed. Settings were not enabled in another profile.')
    finished = False
    def finish(error=None):
        nonlocal finished
        if finished: return
        finished = True
        record['status'] = 'Needs attention' if error else 'Settings applied and effective'
        record['error'] = str(error) if error else None
        try: save()
        except OSError as write_error:
            error = (str(error)+'\n' if error else '')+'The final settings result could not be recorded: '+str(write_error)
        on_ready(record_path, str(error) if error else None)
    save()
    try:
        context()
        if digest(readable_path(source)) != expected_hash:
            raise ValueError('Body settings changed during preparation.')
        if not target.exists():
            mod = organizer.createMod(mobase.GuessedString(name))
            if mod is None or Path(mod.absolutePath()).resolve() != target.resolve():
                raise ValueError('MO2 could not create the expected character settings output.')
        projects = {'RaceMenu body-controller settings':dict(outputs=[RELATIVE], preset=action, source_mod=str(source))}
        manifest = publish_output(target, directory, profile, projects, record['hashes'], tool=TOOL)
        record.update(previous_output=manifest['previous_output'], status='Published; activation pending'); save()
    except Exception as error:
        record.update(status='Preparation failed', error=str(error)); save(); raise
    def verify():
        if finished: return
        try:
            context()
            read_manifest(target, profile, TOOL)
            current = Path(organizer.resolvePath(RELATIVE))
            if current.resolve() != (target/RELATIVE).resolve() or digest(readable_path(current)) != record['hashes'][RELATIVE]:
                raise ValueError('Another file is effective or the prepared settings changed. The settings were not verified.')
        except Exception as error: finish(error)
        else: finish()
    def activate():
        if finished: return
        try:
            context()
            # A manual winner change while MO2 was refreshing is not permission
            # to override that new provider by moving our output to the bottom.
            current = Path(organizer.resolvePath(RELATIVE))
            if current.resolve() not in {source.resolve(), (target/RELATIVE).resolve()}:
                raise ValueError('The active settings provider changed during preparation.')
            if source.resolve() != (target/RELATIVE).resolve() and digest(readable_path(source)) != expected_hash:
                raise ValueError('Source settings changed during preparation.')
            mods = organizer.modList()
            mods.setPriority(name, max(mods.priority(item) for item in mods.allMods()))
            if not mods.setActive(name, True):
                raise ValueError('MO2 could not enable the character settings output.')
            organizer.onNextRefresh(lambda: QTimer.singleShot(0, verify), False)
            organizer.refresh()
        except Exception as error: finish(error)
    try:
        organizer.onNextRefresh(lambda: QTimer.singleShot(0, activate), False)
        organizer.refresh()
    except Exception as error: finish(error)
    return record_path
