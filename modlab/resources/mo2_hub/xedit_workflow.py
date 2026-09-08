"""Use xEdit 4.1.5's own check/clean modes against copied plugin inputs."""
import json
import configparser
import shutil
import subprocess
import zipfile
from pathlib import Path
from uuid import uuid4

from .helpers import HELPERS, load_locations, locate_helper
from .inspection import collect_setup
from .loot_workflow import context_signature
from .xedit import check_result, dependency_order, inspection_order, digest, retain_notice_preferences, stage_plugins, stage_resources, verify_job, write_record
from .archive_text import ArchiveReader, text_resources
from .vfs import find_files


def arguments(job, target, mode, log, *, context=()):
    operation = '-script:' + str(job/'winning-records.pas') if mode == 'winners' else ('-quickautoclean' if mode == 'clean' else '-checkforerrors')
    return ['-SSE', operation,
            '-autoload', '-autoexit', '-forcebsa', '-DontCache',
            '-D:' + str(job / 'Data'), '-O:' + str(job / 'Data'),
            '-P:' + str(job / 'plugins.txt'), '-I:' + str(job / 'Skyrim.ini'),
            '-M:' + str(job / 'settings'), '-B:' + str(job / 'backups'),
            '-T:' + str(job / 'temp'), '-R:' + str(log)] + (list(context) if mode in {'check','winners'} and context else [target])


def run_xedit(organizer, target, mode, reason, version_reader, *, generated_inputs=None):
    if mode not in {'clean', 'check', 'winners'} or (mode == 'clean' and not reason.strip()):
        raise ValueError('Cleaning requires a specific reason from the author or applicable LOOT advice.')
    if mode == 'clean' and target.casefold() == 'skyrim.esm':
        raise ValueError('Skyrim.esm is not offered for automatic cleaning. Check the official xEdit guidance.')
    setup = collect_setup(organizer, version_reader=version_reader)
    if setup.game_name != 'Skyrim Special Edition' or setup.errors:
        raise ValueError('The current Skyrim setup could not be fully read. Recheck it first.')
    plugins = setup.plugins
    resolve = organizer.resolvePath
    if generated_inputs:
        if mode != 'check':
            raise ValueError('Generated inputs may only use record inspection, not automatic cleaning.')
        from .assessment import Plugin
        from .synthesis import plugin_masters
        overrides = {name.casefold(): Path(path) for name, path in generated_inputs.items()}
        plugins = tuple(p for p in plugins if p.name.casefold() not in overrides) + tuple(
            Plugin(name, len(plugins) + index, plugin_masters(Path(path)), 'Pending generated patch')
            for index, (name, path) in enumerate(generated_inputs.items()))
        resolve = lambda name: str(overrides[name.casefold()]) if name.casefold() in overrides else organizer.resolvePath(name)
    if mode == 'winners':
        from .winning_records import active_order, script
        names=active_order(plugins)
        if not names:raise ValueError('No active plugins to check.')
    else:
        names = inspection_order(plugins, target) if mode == 'check' else dependency_order(plugins, target)
    config = Path(organizer.getPluginDataPath()) / 'modlab' / 'helpers.json'
    paths = locate_helper(HELPERS['xedit'], load_locations(config), ())
    if len(paths) != 1:
        raise ValueError('Choose the installed xEdit executable in Helpers and folders first.')
    executable = paths[0]
    if version_reader(str(executable)) not in {'4.1.5', '4.1.5.0'}:
        raise ValueError('This workflow is verified against xEdit 4.1.5f. Select that release or use your helper manually.')
    signature = context_signature(organizer)
    job = stage_plugins(Path(organizer.modsPath()).parent / 'builds' / 'xedit' / uuid4().hex[:12],
                        names, resolve)
    if mode == 'winners':
        (job/'winning-records.pas').write_text(script(job,names),encoding='utf-8-sig')
    resources = {}
    for path in organizer.findFiles('', ['*.bsa']):
        resolved = Path(path)
        if not resolved.is_file():
            resolved = Path(organizer.resolvePath(path))
        resources[resolved.name] = resolved
    for path in find_files(organizer, 'Strings', ['*.strings', '*.dlstrings', '*.ilstrings']):
        resolved = Path(path)
        if not resolved.is_file():
            resolved = Path(organizer.resolvePath(path))
        resources['Strings/' + resolved.name] = resolved
    ini = configparser.ConfigParser(interpolation=None, strict=False)
    ini.read(Path(organizer.profilePath()) / 'Skyrim.ini', encoding='utf-8-sig')
    language = ini.get('General', 'sLanguage', fallback='ENGLISH').strip()
    if not language.isalpha():
        raise ValueError('The selected profile has an unrecognized language setting.')
    from PyQt6.QtCore import QCoreApplication
    reader = ArchiveReader(Path(QCoreApplication.applicationDirPath()) / 'dlls' / 'libbsarch.dll')
    resources.update(text_resources(reader, job, resources, names, language))
    stage_resources(job, resources, language)
    retain_notice_preferences(job, executable)
    record = json.loads((job / 'operation.json').read_text(encoding='utf-8'))
    record.update(profile_path=organizer.profilePath(), mode=mode, reason=reason,
                  helper=str(executable), status='Running', signature=signature, checked_text_resources=True,
                  record_context='Winning records in complete active load order' if mode == 'winners' else
                                 ('Active plugins through target' if mode == 'check' else 'Declared masters only'))
    write_record(job, record)
    try:
        # Settings next to an existing helper take precedence over -P in xEdit.
        # Run a private complete copy so neither its settings nor source plugins change.
        shutil.copytree(executable.parent, job / 'runner')
        runner = job / 'runner' / executable.name

        def execute(run_mode, log):
            args = arguments(job, names[-1], run_mode, log, context=names)
            record.setdefault('commands', []).append(args)
            write_record(job, record)
            handle = organizer.startApplication(str(runner), [subprocess.list2cmdline(args)],
                                                str(job / 'runner'), setup.profile)
            if not handle:
                raise RuntimeError('xEdit could not start. Inspect the MO2 log.')
            timer = None
            if run_mode == 'winners':
                from PyQt6.QtCore import QTimer
                from .xedit_script_ui import advance
                timer=QTimer();timer.setInterval(500)
                handshake={}
                def acknowledge():
                    try:
                        closed=advance(handle,job,names,handshake)
                        if closed:timer.stop()
                    except Exception as problem:
                        record.setdefault('dialog_errors',[]).append(str(problem));write_record(job,record)
                        timer.stop()
                timer.timeout.connect(acknowledge);timer.start()
            try:
                complete, code = organizer.waitForApplication(handle, False)
            finally:
                if timer:timer.stop()
            if not complete:
                raise RuntimeError('xEdit completion was not observed. Wait for it to close before retrying. No output was installed.')
            if context_signature(organizer) != signature:
                raise ValueError('The selected setup changed during xEdit. Recheck before retrying.')
            return code

        code = execute(mode, job / 'tool.log')
        result = verify_job(job, mode, code)
        archive = None
        if mode == 'clean' and result['changed']:
            # Check the cleaned copy for record errors before offering it for installation.
            before = {name: digest(job / 'Data' / name) for name in names}
            check_code = execute('check', job / 'check.log')
            check_log = (job / 'check.log').read_text(encoding='utf-8-sig', errors='replace')
            if check_result(check_log, check_code, target=target) != 0:
                raise ValueError('The cleaned plugin did not pass xEdit record checks. Inspect check.log; no output was installed.')
            if any(digest(job / 'Data' / name) != before[name] for name in names):
                raise ValueError('The follow-up inspection changed an input. No output was installed.')
            verify_job(job, mode, code)
            archive = job / ('ModLab Cleaned ' + Path(target).stem + '.zip')
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
                bundle.write(job / 'Data' / names[-1], names[-1])
            record['archive'] = str(archive)
        record.update(status='Checked; review result', result=result)
        return job, result, archive, signature
    except Exception as error:
        record.update(status='Needs attention', error=str(error))
        raise RuntimeError(f'{error}\n\nRetained files: {job}') from error
    finally:
        write_record(job, record)
