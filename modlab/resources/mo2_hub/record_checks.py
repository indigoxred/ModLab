"""Automatic, cached xEdit record checks; never infer permission to clean."""
import hashlib
import json
from pathlib import Path
import re

from .assessment import Finding, GAME_MASTERS
from .vfs import readable_path
from .xedit import check_result, inspection_order

_hashes = {}


def file_hash(value):
    path = readable_path(value).resolve(strict=True)
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    if key not in _hashes:
        with path.open('rb') as stream:
            checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
        after = path.stat()
        if key[1:] != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError('File changed while inspecting: ' + str(path))
        if len(_hashes) > 2048:
            _hashes.clear()
        _hashes[key] = checksum
    return _hashes[key]


def targets(plugins):
    # Inspect installed/generated mod plugins and Creations. Immutable base
    # masters are dependency inputs, not an invitation to repair the base game.
    return [p for p in plugins if p.load_order >= 0 and p.name.casefold() not in GAME_MASTERS]


def signature(plugins, target, resolve, context):
    inputs = [(name, str(resolve(name)), file_hash(resolve(name))) for name in inspection_order(plugins, target)]
    return hashlib.sha256(json.dumps(['active-prefix-v1', context, inputs], sort_keys=True).encode()).hexdigest()


def error_details(log):
    """Keep the affected record identity alongside xEdit's field errors."""
    lines, record, emitted = [], None, None
    for line in log.splitlines():
        if re.search(r'\[[A-Z0-9_]{4}:[0-9A-Fa-f]{8}\]\s*$', line) and not ('<Error:' in line or ' -> ' in line):
            record = line
        if '<Error:' in line or ' -> ' in line:
            if record and record != emitted:
                lines.append(record)
                emitted = record
            lines.append(line)
    return '\n'.join(lines)[:10000]

def read_cache(path):
    if not path.is_file():
        return {'version': 1, 'entries': {}}
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('version') != 1 or not isinstance(data.get('entries'), dict):
        raise ValueError('Saved record checks could not be read. Preserve ' + str(path) + ' and recheck.')
    return data


def retained_result(entry, expected):
    if not entry or entry.get('signature') != expected or entry.get('error'):
        return False
    log = Path(entry['job']) / 'tool.log'
    return log.is_file() and file_hash(log) == entry['log_hash']


def inspect_targets(plugins, resolve, context, cache_path):
    data = read_cache(cache_path)
    findings, pending, completed = [], [], []
    for plugin in targets(plugins):
        entry = data['entries'].get(plugin.name.casefold())
        try:
            expected = signature(plugins, plugin.name, resolve, context)
            if entry and entry.get('signature') == expected and entry.get('error'):
                findings.append(Finding('Unknown', 'record-check-incomplete', 'Record check could not finish: ' + plugin.name,
                    entry['error'], 'Resolve the reported helper or file problem, then use Recheck and finish setup. '
                    'Earlier results do not cover this plugin’s current inputs.'))
                continue
            if not retained_result(entry, expected):
                pending.append(plugin.name)
                continue
            completed.append(plugin.name)
            if entry['errors']:
                entry['details'] = error_details((Path(entry['job'])/'tool.log').read_text(encoding='utf-8-sig', errors='replace'))
                findings.append(Finding('Review', 'record-errors', f'Record problems reported: {plugin.origin or plugin.name}',
                    plugin.name + f' — {entry["errors"]} record errors\n' + entry.get('details', '') + '\nLog: ' + str(Path(entry['job'])/'tool.log'),
                    'Check the author’s explanation, updated matching release or required compatibility patch. Install supported fixes through '
                    'ModLab and recheck. A patch may correct the winning game record while this source-file warning remains. '
                    'Disable the mod and its dependents if the problem is confirmed and no supported fix is available. '
                    'Advanced tools → xEdit remains available for expert diagnosis.',
                    'xEdit found record-level problems in ' + plugin.name + '. This is separate from ordinary file overlap '
                    'and dirty-record counts. Cleaning is not a general repair for these errors and was not performed. '
                    'This checks the plugin with the active plugins preceding it, including injected-record providers, not every later override. A record warning alone does not prove '
                    'a crash or that an installed compatibility patch failed.\n\n' + entry.get('details','')))
        except (OSError, ValueError) as error:
            findings.append(Finding('Unknown', 'record-check-incomplete', 'Record inputs need attention: ' + plugin.name,
                str(error), 'Resolve the named dependency or file problem, then use Recheck and finish setup.'))
    if pending:
        findings.append(Finding('Unknown', 'record-checks-pending', f'Record checks pending: {len(pending)} plugins',
            '\n'.join(pending), 'Use Recheck and finish setup. ModLab will run xEdit on copied inputs and retain the results. '
            'Changed plugins and dependencies invalidate earlier checks.'))
    if completed:
        findings.append(Finding('Info', 'record-checks-current', f'Current xEdit record checks: {len(completed)} plugins',
            '\n'.join(completed), 'No repeated check is needed while these plugin inputs and the helper are unchanged. '
            'Any reported record errors remain separate findings.',
            'ModLab checked these installed or generated plugins with xEdit. Base masters were loaded as dependencies; '
            'they were not modified. These checks find record errors, not every conflict, script bug or gameplay problem.'))
    return tuple(findings)


def check_targets(plugins, resolve, context, cache_path, run, status=lambda message: None):
    data, steps = read_cache(cache_path), []
    selected = targets(plugins)
    for index, plugin in enumerate(selected):
        expected = None
        try:
            expected = signature(plugins, plugin.name, resolve, context)
            if retained_result(data['entries'].get(plugin.name.casefold()), expected):
                continue
            status(f'Checking plugin records {index + 1}/{len(selected)}: {plugin.name}…')
            job, result, _, _ = run(plugin.name)
            log_path = Path(job)/'tool.log'
            log = log_path.read_text(encoding='utf-8-sig', errors='replace')
            errors = check_result(log, min(result['record_errors'], 127), target=plugin.name)
            if signature(plugins, plugin.name, resolve, context) != expected:
                raise ValueError('Plugin inputs changed during the record check.')
            details = error_details(log)
            data['entries'][plugin.name.casefold()] = dict(signature=expected, job=str(job), log_hash=file_hash(log_path),
                                                         errors=errors, details=details)
            steps.append(f'xEdit checked {plugin.name}: {errors} record errors. Retained job: {job}')
        except Exception as error:
            data['entries'][plugin.name.casefold()] = dict(signature=expected, error=str(error))
            steps.append('Stopped record checking after an incomplete attempt: ' + plugin.name)
            # Do not launch another helper after uncertain completion.
            break
        finally:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
            temporary.replace(cache_path)
    return inspect_targets(plugins, resolve, context, cache_path), tuple(steps)


def host_context(organizer):
    from .helpers import HELPERS, load_locations, locate_helper
    from .vfs import find_files
    paths = locate_helper(HELPERS['xedit'], load_locations(Path(organizer.getPluginDataPath())/'modlab/helpers.json'), ())
    if len(paths) != 1:
        raise ValueError('Download the complete xEdit package from https://github.com/TES5Edit/TES5Edit/releases '
                         'and select SSEEdit.exe in Setup / tool locations. Then recheck.')
    archives = []
    for value in organizer.findFiles('', ['*.bsa']):
        path = readable_path(value); stat = path.stat()
        archives.append((str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
    ini = organizer.profile().absoluteIniFilePath('Skyrim.ini')
    strings = [(str(readable_path(p)),file_hash(p)) for p in
               find_files(organizer,'Strings',['*.strings','*.dlstrings','*.ilstrings'])]
    return dict(profile=organizer.profilePath(), helper=file_hash(paths[0]), ini=file_hash(ini),
                archives=sorted(archives), strings=sorted(strings))


def inspect_record_checks(organizer, setup):
    if not targets(setup.plugins):
        return ()
    try:
        from .winning_records import inspect_setup
        return inspect_setup(setup.plugins, organizer.resolvePath, host_context(organizer),
                             Path(organizer.profilePath())/'modlab-winning-record-checks.json')
    except Exception as error:
        return (Finding('Unknown', 'record-check-incomplete', 'Automatic record checks need setup', str(error),
            'Resolve the reported requirement in Setup / tool locations, then use Recheck and finish setup.'),)


def run_record_checks(organizer, setup, version_reader, status=lambda message: None):
    from .xedit_workflow import run_xedit
    if not targets(setup.plugins):
        return (), ()
    try:
        from .winning_records import check_setup
        return check_setup(setup.plugins, organizer.resolvePath, host_context(organizer),
            Path(organizer.profilePath())/'modlab-winning-record-checks.json',
            lambda: run_xedit(organizer, '', 'winners', '', version_reader), status)
    except Exception as error:
        return (Finding('Unknown', 'record-check-incomplete', 'Automatic record checks could not start', str(error),
            'Resolve the helper or file requirement, then use Recheck and finish setup.'),), ()
