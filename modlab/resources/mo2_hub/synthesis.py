"""Inspect selected Synthesis pipelines and require complete, attributable output."""
from dataclasses import dataclass
import copy
import json
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import re
import struct
from urllib.parse import urlparse
from uuid import uuid4

from .outputs import digest


def pending_path(profile):
    return Path(profile) / 'modlab-patcher-pending.json'


def load_pending(profile):
    path = pending_path(profile)
    if not path.exists():
        return None
    record = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(record.get('id'), str) or not record['id']:
        raise ValueError('The pending patch attempt has no identity: ' + str(path))
    inspect_pipeline(record['settings'])
    return record


def _write_pending(profile, record):
    path = pending_path(profile)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(record, indent=2), encoding='utf-8')
    temporary.replace(path)


def begin_pending(profile, settings, extra_data=None, persistence=None):
    inspect_pipeline(settings)
    attempt = uuid4().hex[:12]
    _write_pending(profile, dict(id=attempt, settings=settings,
        extra_data=str(extra_data) if extra_data else None, persistence=str(persistence) if persistence else None,
        started_at=datetime.now(timezone.utc).isoformat(), error='', build_record=None))
    return attempt


def update_pending(profile, attempt, *, error='', build_record=None):
    record = load_pending(profile)
    if not record or record['id'] != attempt:
        raise ValueError('The pending patch attempt changed; its status was not overwritten.')
    record.update(error=error, build_record=str(build_record) if build_record else None)
    _write_pending(profile, record)


def clear_pending(profile, attempt):
    record = load_pending(profile)
    if not record or record['id'] != attempt:
        raise ValueError('The pending patch attempt changed; the newer request was retained.')
    pending_path(profile).unlink()


@dataclass(frozen=True)
class Pipeline:
    profile_id: str
    outputs: tuple
    patchers: tuple


def checked_label(value):
    if value and (re.search(r'[<>:"/\\|?*\x00-\x1f]', value) or value.endswith((' ', '.'))):
        raise ValueError('Profile and patcher names must be valid folder names: ' + value)


def inspect_pipeline(settings):
    if settings.get('Version') != 2:
        raise ValueError('Select a Synthesis version 2 pipeline settings file.')
    profiles = settings.get('Profiles', [])
    if len(profiles) != 1 or profiles[0].get('TargetRelease') != 'SkyrimSE':
        raise ValueError('Use a pipeline containing one SkyrimSE profile.')
    profile = profiles[0]
    checked_label(profile.get('Nickname', ''))
    if profile.get('IgnoreMissingMods') or profile.get('Localize') or profile.get('ExportAsMasterFiles'):
        raise ValueError('This adapter requires all masters present and non-localized ESP output. Review these pipeline options.')
    outputs, patchers, ids = [], [], set()
    for group in profile.get('Groups', []):
        if not group.get('On', True):
            continue
        enabled = [p for p in group.get('Patchers', []) if p.get('On', True)]
        if not enabled:
            continue
        name = group.get('Name', '')
        if not name or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith((' ', '.')):
            raise ValueError('Each patch group needs a valid plugin name without a file extension.')
        if name.casefold().endswith(('.esp', '.esm', '.esl')):
            raise ValueError('Remove the plugin extension from the patch group name: ' + name)
        outputs.append(name + '.esp')
        for patcher in enabled:
            checked_label(patcher.get('Nickname', ''))
            if not patcher.get('$type', '').startswith('Synthesis.Bethesda.Execution.Settings.GithubPatcherSettings,'):
                raise ValueError('This workflow supports Git patchers. Use Synthesis for other patcher types.')
            address = urlparse(patcher.get('RemoteRepoPath', ''))
            if address.scheme != 'https' or address.hostname != 'github.com' or address.username or address.query or address.fragment:
                raise ValueError('Choose a patcher from its HTTPS GitHub repository.')
            project = patcher.get('SelectedProjectSubpath', '')
            if not project.endswith('.csproj') or '\\' in project or PurePosixPath(project).is_absolute() or '..' in PurePosixPath(project).parts:
                raise ValueError('The patcher project path is not valid.')
            if patcher.get('PatcherVersioning') != 'Commit' or not re.fullmatch(r'[a-fA-F0-9]{40}', patcher.get('TargetCommit', '')):
                raise ValueError('Select an exact patcher commit revision before importing. This keeps automatic rebuilds on your chosen version.')
            identifier = patcher.get('ID', '')
            if not identifier or identifier.casefold() in ids or re.search(r'[^a-zA-Z0-9_.-]', identifier) or identifier in {'.', '..'}:
                raise ValueError('The pipeline has a missing or duplicate patcher identifier.')
            ids.add(identifier.casefold())
            patchers.append((name, patcher.get('Nickname') or project, patcher['RemoteRepoPath'], patcher['TargetCommit']))
    if len(set(n.casefold() for n in outputs)) != len(outputs):
        raise ValueError('The pipeline has duplicate output plugin names.')
    if not outputs:
        raise ValueError('Enable at least one patcher in the selected pipeline.')
    identifier = profile.get('ID', '')
    if not identifier or re.search(r'[^a-zA-Z0-9_.-]', identifier) or identifier in {'.', '..'}:
        raise ValueError('The pipeline profile identifier is missing or invalid.')
    return Pipeline(identifier, tuple(outputs), tuple(patchers))


def command_line(arguments, log):
    """cmd.exe capture with expansion disabled and each fixed argument quoted."""
    def quote(value):
        value = str(value)
        if any(c in value for c in ('"', '%', '!', '\r', '\n', '\0')):
            raise ValueError('The helper path contains characters unsupported by console capture: ' + value)
        # None of these arguments may end in a slash: Windows argv parsing treats
        # a trailing backslash before a quote specially.
        if value.endswith('\\'):
            raise ValueError('Remove the trailing slash from the helper path.')
        return '"' + value + '"'
    return '/D /V:OFF /S /C "' + ' '.join(quote(v) for v in arguments) + ' > ' + quote(log) + ' 2>&1"'


def selected_settings(settings):
    inspect_pipeline(settings)
    selected = copy.deepcopy(settings)
    profile = selected['Profiles'][0]
    profile['Groups'] = [g for g in profile['Groups'] if g.get('On', True) and
                         any(p.get('On', True) for p in g.get('Patchers', []))]
    for group in profile['Groups']:
        group['Patchers'] = [p for p in group['Patchers'] if p.get('On', True)]
    return selected


def plugin_masters(path):
    with path.open('rb') as stream:
        header = stream.read(24)
        if len(header) != 24 or header[:4] != b'TES4':
            raise ValueError('Generated plugin has an invalid TES4 header: ' + path.name)
        size = struct.unpack_from('<I', header, 4)[0]
        if size > 16 * 1024 * 1024:
            raise ValueError('Generated plugin header is oversized: ' + path.name)
        data = stream.read(size)
    if len(data) != size:
        raise ValueError('Generated plugin header is truncated: ' + path.name)
    offset, masters, hedr, extended = 0, [], False, None
    while offset < len(data):
        if offset + 6 > len(data):
            raise ValueError('Generated plugin subrecord is truncated: ' + path.name)
        kind, length = data[offset:offset + 4], struct.unpack_from('<H', data, offset + 4)[0]
        offset += 6
        if extended is not None:
            length, extended = extended, None
        value = data[offset:offset + length]
        if len(value) != length:
            raise ValueError('Generated plugin subrecord is truncated: ' + path.name)
        if kind == b'XXXX':
            if length != 4:
                raise ValueError('Generated plugin extended subrecord size is invalid: ' + path.name)
            extended = struct.unpack('<I', value)[0]
        if kind == b'HEDR':
            hedr = length == 12
        if kind == b'MAST':
            if not value.endswith(b'\0'):
                raise ValueError('Generated plugin master name is invalid: ' + path.name)
            masters.append(value[:-1].decode('utf-8'))
        offset += length
    if extended is not None:
        raise ValueError('Generated plugin extended subrecord is missing: ' + path.name)
    if not hedr:
        raise ValueError('Generated plugin HEDR record is missing: ' + path.name)
    return tuple(masters)


def verify_output(output, expected, log, code, active_plugins):
    if code != 0:
        raise ValueError(f'Synthesis exited with code {code}. Read the retained build log; previous output remains unchanged.')
    output = Path(output)
    files = [p for p in output.rglob('*') if p.is_file()]
    actual = {p.relative_to(output).as_posix() for p in files}
    if actual != set(expected):
        raise ValueError('Unexpected or missing patch files. Expected: ' + ', '.join(expected) + '; found: ' + ', '.join(sorted(actual)))
    available = {p.casefold() for p in active_plugins}
    # The CLI reports each group's filenames, followed by the destination
    # directory (not an individual file path).
    exported, pending, collecting = set(), [], False
    for line in log.splitlines():
        if line == 'Files to export:':
            pending, collecting = [], True
        elif collecting and line.startswith('   '):
            pending.append(line.strip())
        else:
            collecting = False
            prefix = 'Exported patch to final destination: '
            if line.startswith(prefix) and Path(line[len(prefix):]).resolve() == output.resolve():
                exported.update(pending)
                pending = []
    hashes = {}
    for name in expected:
        target = output / name
        if target.is_symlink() or not target.is_file():
            raise ValueError('Generated patch is not a regular file: ' + name)
        if name not in exported:
            raise ValueError('Synthesis completion receipt is missing for ' + name)
        for master in plugin_masters(target):
            if master.casefold() not in available:
                raise ValueError(f'{name} requires a missing or later master: {master}')
        available.add(name.casefold())
        hashes[name] = digest(target)
    return hashes
