"""Private xEdit inputs and checks; cleaning never writes into a source mod."""
import hashlib
import configparser
import json
import os
import re
import shutil
from pathlib import Path, PureWindowsPath
from .bodyslide import relative_path


def retain_notice_preferences(job, executable):
    """Keep actual acknowledged notices across private jobs, without importing path/edit settings."""
    candidates = sorted(job.parent.glob('*/plugins.sseviewsettings'), key=lambda p: p.stat().st_mtime_ns)
    candidates.insert(0, executable.parent / 'SSEEdit.ini')
    preferences = configparser.ConfigParser(interpolation=None, strict=False)
    preferences.optionxform = str
    for path in candidates:
        if path == job / 'plugins.sseviewsettings':
            continue
        existing = configparser.ConfigParser(interpolation=None, strict=False)
        existing.read(path, encoding='utf-8-sig')
        for section, keys in (('Init', ('First64Start',)), ('DeveloperMessage', ('Version', 'LastShownOn'))):
            for key in keys:
                if existing.has_option(section, key):
                    if not preferences.has_section(section):
                        preferences.add_section(section)
                    preferences.set(section, key, existing.get(section, key))
    with (job / 'plugins.sseviewsettings').open('w', encoding='utf-8') as stream:
        preferences.write(stream)


def check_resources(log):
    if any(message in log.casefold() for message in ('no strings file', 'unknown lstring id', 'could not be loaded. <error:')) or re.search(r'lstring ID[^\n]*could not be resolved', log, re.I):
        raise ValueError('xEdit could not resolve required text or archive resources. No cleaned output was accepted.')


def check_result(log, code, *, target=None):
    check_resources(log)
    matches = re.findall(r'Done: Checking for Errors, Processed Records: (\d+), Errors found: (\d+)', log)
    if not 0 <= code <= 127 or '--= All Done =--' not in log or len(matches) != 1 or int(matches[0][0]) < 1 or 'exception' in log.casefold() or 'fatal:' in log.casefold():
        raise ValueError('xEdit did not complete the selected plugin record check. Read the retained log.')
    if min(int(matches[0][1]), 127) != code:
        raise ValueError('xEdit exit status and record-check result disagree.')
    if target and not re.search(r'Checking for Errors in \[[^\]]+\]\s+' + re.escape(target) + r'\s*$', log, re.M | re.I):
        raise ValueError('The xEdit log did not identify the requested plugin: ' + target)
    return int(matches[0][1])


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def dependency_order(plugins, target):
    by_name = {p.name.casefold(): p for p in plugins}
    result, visiting = [], set()

    def visit(name):
        path = PureWindowsPath(name)
        if path.name != name or ':' in name or path.suffix.casefold() not in {'.esm', '.esp', '.esl'}:
            raise ValueError(f'Invalid plugin filename: {name}')
        plugin = by_name.get(name.casefold())
        if plugin is None or plugin.load_order < 0:
            raise ValueError(f'Missing or disabled dependency: {name}')
        if plugin.name in result:
            return
        if plugin.name in visiting:
            raise ValueError(f'Cyclic plugin dependencies: {name}')
        visiting.add(plugin.name)
        for master in plugin.masters:
            visit(master)
        visiting.remove(plugin.name)
        result.append(plugin.name)
    visit(target)
    return tuple(result)


def write_record(job, record):
    temporary = job / 'operation.tmp'
    temporary.write_text(json.dumps(record, indent=2), encoding='utf-8')
    temporary.replace(job / 'operation.json')


def inspection_order(plugins, target):
    """Load the active prefix, including non-master providers of injected records."""
    required = dependency_order(plugins, target)
    selected = next(p for p in plugins if p.name.casefold() == target.casefold())
    result = []
    for plugin in sorted((p for p in plugins if 0 <= p.load_order <= selected.load_order), key=lambda p: p.load_order):
        for name in dependency_order(plugins, plugin.name):
            if name not in result:
                result.append(name)
    if not result or result[-1] != required[-1]:
        raise ValueError('The record-check target is not last in its dependency context. Check plugin order first.')
    return tuple(result)


def stage_plugins(job, names, resolve):
    job = Path(job)
    job.mkdir(parents=True, exist_ok=False)
    data = job / 'Data'; data.mkdir()
    sources = {}
    for name in names:
        source = Path(resolve(name)).resolve(strict=True)
        before = digest(source)
        shutil.copyfile(source, data / name)
        if digest(data / name) != before or digest(source) != before:
            raise ValueError(f'Source changed while copying {name}. Recheck and retry.')
        sources[name] = {'path': str(source), 'sha256': before}
    record = {'target': names[-1], 'sources': sources, 'status': 'Prepared'}
    write_record(job, record)
    (job / 'plugins.txt').write_text(''.join('*' + n + '\n' for n in names), encoding='utf-8')
    (job / 'loadorder.txt').write_text('\n'.join(names) + '\n', encoding='utf-8')
    (job / 'Skyrim.ini').write_text('[General]\nsLanguage=ENGLISH\n', encoding='utf-8')
    for directory in ('backups', 'cache', 'temp', 'settings'):
        (job / directory).mkdir()
    return job


def stage_resources(job, resources, language):
    record = json.loads((job / 'operation.json').read_text(encoding='utf-8'))
    entries = {}
    for name, source in resources.items():
        name = relative_path(name)
        source = Path(source).resolve(strict=True)
        destination = job / 'Data' / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        stat = source.stat()
        if source.suffix.casefold() == '.bsa':
            # xEdit's archive reader only reads BSA resources. Avoid duplicating tens of GB per job.
            try:
                os.link(source, destination)
                method = 'Archive read access through hard link'
            except OSError:
                shutil.copyfile(source, destination)
                method = 'Archive copy'
        else:
            shutil.copyfile(source, destination)
            method = 'Loose text table copy'
        entries[name] = {'path': str(source), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'method': method}
        if source.suffix.casefold() != '.bsa':
            entries[name]['sha256'] = digest(source)
            if digest(destination) != entries[name]['sha256']:
                raise ValueError(f'Text resource changed while staging: {name}')
    record.update(resources=entries, language=language)
    write_record(job, record)
    archives = [name for name in entries if name.casefold().endswith('.bsa')]
    (job / 'Skyrim.ini').write_text('[General]\nsLanguage=' + language + '\n[Archive]\nsResourceArchiveList=' +
                                   ', '.join(archives) + '\n', encoding='utf-8')


def verify_job(job, mode, code):
    record = json.loads((job / 'operation.json').read_text(encoding='utf-8'))
    log = (job / 'tool.log').read_text(encoding='utf-8-sig', errors='replace')
    checked = mode == 'check' and record.get('checked_text_resources')
    errors = check_result(log, code, target=record['target']) if checked else None
    marker = ('ModLab winning-record check complete: ' + job.name) if mode == 'winners' else ('Quick Clean mode finished.' if mode == 'clean' else '--= All Done =--')
    if marker not in log or 'fatal:' in log.casefold() or 'exception' in log.casefold():
        raise ValueError('xEdit did not report reliable completion. Read the retained log.')
    check_resources(log)
    for name, resource in record.get('resources', {}).items():
        source, staged = Path(resource['path']), job / 'Data' / relative_path(name)
        if any(not path.is_file() or path.stat().st_size != resource['size'] or
               (path == source and path.stat().st_mtime_ns != resource['mtime_ns']) for path in (source, staged)):
            raise ValueError(f'Text/archive resource changed: {name}')
        if resource.get('sha256') and any(digest(path) != resource['sha256'] for path in (source, staged)):
            raise ValueError(f'Text resource changed: {name}')
    if (mode in {'clean','winners'} and code != 0) or (mode == 'check' and not 0 <= code <= 127):
        raise ValueError(f'xEdit exited unexpectedly ({code}). Read the retained log.')
    target = record['target']
    for name, source in record['sources'].items():
        if digest(source['path']) != source['sha256']:
            raise ValueError(f'Source changed: {name}. Recheck before using this result.')
        changed = digest(job / 'Data' / name) != source['sha256']
        if changed and name != target:
            raise ValueError(f'xEdit changed a copied master: {name}. Output is not offered for installation.')
        if changed and mode in {'check','winners'}:
            raise ValueError('An inspection changed its copied input. Review the tool log.')
    output = job / 'Data' / target
    with output.open('rb') as stream:
        header = stream.read(24)
    if len(header) != 24 or header[:4] != b'TES4':
        raise ValueError('The resulting plugin header is missing or invalid.')
    if any((job / 'Data').glob('*.save')):
        raise ValueError('xEdit left an unfinished save. Output is not ready for installation.')
    result = {'changed': digest(output) != record['sources'][target]['sha256'],
              'sha256': digest(output), 'record_errors': (errors if checked else code) if mode == 'check' else None}
    if mode == 'winners':
        from .winning_records import parse_report, targets
        names=tuple(record['sources'])
        result['winning_records']=parse_report((job/'winning-records.tsv').read_text(encoding='utf-8-sig'),
                                               job.name,names,targets(names))
    return result
