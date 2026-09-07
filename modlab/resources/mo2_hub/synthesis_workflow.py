"""Run chosen, pinned patchers in MO2 and publish complete output with recovery."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import shutil
from uuid import uuid4

from .helpers import HELPERS, load_locations, locate_helper
from .outputs import digest, output_name, publish_output, read_manifest
from .pandora_workflow import effective_issues, activate_generated_plugins
from .runtime import ensure_sdk10, sdk_environment
from .synthesis import inspect_pipeline, verify_output, command_line, selected_settings
from .body_workflow import readable_path


def choices_path(organizer):
    return Path(organizer.profilePath()) / 'modlab-patcher-choices.json'


def load_choices(organizer):
    path = choices_path(organizer)
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None


def settings_hashes(folder):
    folder = readable_path(folder)
    return {p.relative_to(folder).as_posix(): digest(p) for p in folder.rglob('*') if p.is_file()}


def copy_settings(source, destination):
    destination = readable_path(destination)
    if not source:
        destination.mkdir()
        return {}
    source = readable_path(source)
    if not source.is_dir():
        raise ValueError('Retained patch settings or FormID data are missing: ' + str(source))
    expected = settings_hashes(source)
    shutil.copytree(source, destination)
    if settings_hashes(destination) != expected or settings_hashes(source) != expected:
        raise ValueError('Patch settings changed or could not be copied completely. No new output was applied.')
    return expected


def own_name(organizer):
    return output_name(organizer.profile().name(), organizer.profilePath(), 'Synthesis')


def input_state(organizer, outputs, *, output_mod=None, excluded_outputs=()):
    import mobase
    plugins, mods = organizer.pluginList(), organizer.modList()
    own = output_mod or own_name(organizer)
    excluded = {own, *excluded_outputs}
    if output_mod is None:
        excluded.add(output_name(organizer.profile().name(), organizer.profilePath(), 'Graphics'))
    order, sources = [], []
    for name in sorted(plugins.pluginNames(), key=plugins.priority):
        if plugins.loadOrder(name) < 0:
            continue
        if name.casefold() in {n.casefold() for n in outputs}:
            if plugins.origin(name) != own:
                raise ValueError('A selected patch name already belongs to another mod: ' + name)
            continue
        if plugins.origin(name) in excluded:
            continue
        path = Path(organizer.resolvePath(name))
        order.append(name)
        sources.append((name, str(path), digest(path)))
    # Generic patchers can also read meshes and other assets. Preserve their
    # complete source layout/priority in the receipt, excluding our own output.
    roots = [('Game Data', Path(organizer.managedGame().gameDirectory().absolutePath()) / 'Data')]
    roots += [(name, Path(mods.getMod(name).absolutePath())) for name in sorted(mods.allMods(), key=mods.priority)
              if name not in excluded and mods.state(name) & mobase.ModState.ACTIVE and mods.getMod(name) is not None]
    assets = []
    for name, root in roots:
        entries = []
        for path in root.rglob('*'):
            if path.is_file() and path.name not in {'meta.ini', '.modlab-output.json', 'ModLab - Output contents.txt'}:
                stat = path.stat()
                entries.append((path.relative_to(root).as_posix(), stat.st_size, stat.st_mtime_ns))
        if entries:
            assets.append((name, str(root), sorted(entries)))
    return {'profile_path': organizer.profilePath(), 'order': order, 'sources': sources, 'assets': assets}


@dataclass
class SynthesisJob:
    directory: Path
    executable: Path
    settings: dict
    record: dict

    def save(self):
        (self.directory / 'operation.json').write_text(json.dumps(self.record, indent=2), encoding='utf-8')


def prepare_job(organizer, settings, extra_data=None, persistence=None):
    from .skse import require_game_closed
    require_game_closed()
    from .pgpatcher_workflow import require_upstream_view
    require_upstream_view(organizer)
    pipeline = inspect_pipeline(settings)
    if organizer.managedGame().gameName() != 'Skyrim Special Edition':
        raise ValueError('Select the Skyrim Special Edition profile before patching.')
    config = Path(organizer.getPluginDataPath()) / 'modlab/helpers.json'
    matches = locate_helper(HELPERS['synthesis-cli'], load_locations(config), [])
    if len(matches) != 1:
        raise ValueError('Select the Synthesis CLI executable in Setup / tool locations. The graphical release alone does not contain this CLI.')
    executable = matches[0]
    import mobase
    if mobase.getFileVersion(str(executable)) not in {'0.36.6', '0.36.6.0'}:
        raise ValueError('This adapter supports Synthesis CLI 0.36.6. Select that complete build in Setup / tool locations.')
    instance = Path(organizer.modsPath()).parent
    existing = instance / 'mods' / own_name(organizer)
    if (existing / '.modlab-output.json').is_file() and not persistence:
        raise ValueError('Existing generated patches need their retained FormID persistence data. Reopen Patch choices to reuse the saved build; do not reset patch identities in an existing save.')
    sdk = ensure_sdk10(instance / 'tools')
    state = input_state(organizer, pipeline.outputs)
    # Also protect an inactive mod's plugin from being silently displaced.
    for name in organizer.pluginList().pluginNames():
        if name.casefold() in {n.casefold() for n in pipeline.outputs} and organizer.pluginList().origin(name) != own_name(organizer):
            raise ValueError('Another mod already supplies the requested output plugin: ' + name)
    directory = instance / 'builds/synthesis' / uuid4().hex[:12]
    directory.mkdir(parents=True)
    copied = {folder: copy_settings(source, directory / folder)
              for folder, source in [('Data', extra_data), ('persistence', persistence)]}
    # This CLI prepares even disabled patchers. Pass only the chosen entries.
    configured = selected_settings(settings)
    configured.update(DotNetPathOverride=str(sdk / 'dotnet.exe'), Shortcircuit=True, BlockBuildingWithinMo2=False)
    profile = configured['Profiles'][0]
    profile.update(UpdateLoadOrderAfterRun=False, DataPathOverride=None, SplitIfMaxMastersExceeded=False,
                   FormIdPersistence='Text')
    # The private working directory is supplied through TEMP/TMP: 0.36.6 CLI
    # ignores PipelineSettings.WorkingDirectory.
    (directory / 'PipelineSettings.json').write_text(json.dumps(configured, indent=2), encoding='utf-8')
    (directory / 'plugins.txt').write_text(''.join('*' + name + '\n' for name in state['order']), encoding='utf-8')
    (directory / 'output').mkdir()
    job = SynthesisJob(directory, executable, configured, {
        'created_at': datetime.now(timezone.utc).isoformat(), 'profile': organizer.profile().name(),
        'profile_path': organizer.profilePath(), 'status': 'Prepared; patching not started',
        'input_state': state, 'outputs': pipeline.outputs, 'patchers': pipeline.patchers,
        'copied_settings': copied, 'settings_source': str(extra_data) if extra_data else None,
        'helper': str(executable), 'helper_sha256': digest(executable), 'sdk': str(sdk)})
    job.save()
    return job


def check_context(organizer, job):
    if json.dumps(input_state(organizer, job.record['outputs']), sort_keys=True) != json.dumps(job.record['input_state'], sort_keys=True):
        raise ValueError('The selected profile or patch inputs changed during this run. Recheck before applying it.')
    if digest(job.executable) != job.record['helper_sha256']:
        raise ValueError('The patching helper changed during this run.')


@contextmanager
def working_environment(organizer, sdk):
    identity = hashlib.sha256(organizer.profilePath().casefold().encode()).hexdigest()[:10]
    work = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'ModLab/syn' / identity
    work.mkdir(parents=True, exist_ok=True)
    previous = {key: os.environ.get(key) for key in ('TEMP', 'TMP')}
    os.environ.update(TEMP=str(work), TMP=str(work))
    try:
        with sdk_environment(sdk, Path(organizer.modsPath()).parent / 'tools/sdk-state'):
            yield
    finally:
        for key, value in previous.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value


def run_job(organizer, job):
    check_context(organizer, job)
    directory = job.directory
    args = [str(job.executable), 'run-pipeline', '--PipelineSettingsPath', str(directory / 'PipelineSettings.json'),
            '--ProfileIdentifier', job.settings['Profiles'][0]['ID'], '--OutputDirectory', str(directory / 'output'),
            '--DataFolderPath', str(Path(organizer.managedGame().gameDirectory().absolutePath()) / 'Data'),
            '--LoadOrderFilePath', str(directory / 'plugins.txt'), '--ExtraDataFolder', str(directory / 'Data'),
            '--PersistencePath', str(directory / 'persistence'), '--PersistenceMode', 'Text', '--TargetRuntime', 'win-x64']
    command = command_line(args, directory / 'tool.log')
    job.record.update(status='Building and running selected patchers', arguments=args)
    job.save()
    try:
        with working_environment(organizer, job.record['sdk']):
            handle = organizer.startApplication(str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/cmd.exe'),
                [command], str(directory), organizer.profile().name())
        if not handle:
            raise ValueError('Synthesis could not start. Inspect the retained job and helper path.')
        complete, code = organizer.waitForApplication(handle, False)
        if not complete:
            raise ValueError('Synthesis is still running. Let it finish before retrying; no output has been applied.')
        check_context(organizer, job)
        log = (directory / 'tool.log').read_text(encoding='utf-8-sig', errors='replace')
        current_settings = settings_hashes(directory / 'Data')
        if any(current_settings.get(name) != checksum for name, checksum in job.record['copied_settings']['Data'].items()):
            raise ValueError('A chosen patcher setting was removed or changed during generation. The result was withheld; inspect the retained settings and log.')
        job.record['hashes'] = verify_output(directory / 'output', job.record['outputs'], log, code, job.record['input_state']['order'])
        job.record.update(status='All patch exports and masters checked; checking records', exit_code=code)
        job.save()
        import mobase
        from .xedit_workflow import run_xedit
        generated = {name: directory / 'output' / name for name in job.record['outputs']}
        job.record['record_checks'] = []
        for name in job.record['outputs']:
            check, result, _, _ = run_xedit(organizer, name, 'check', 'Inspect selected Synthesis patch before publication',
                                          mobase.getFileVersion, generated_inputs=generated)
            job.record['record_checks'].append(str(check / 'operation.json'))
            job.save()
            if result['record_errors'] != 0:
                raise ValueError(f'{name} has {result["record_errors"]} record errors. Output was withheld; inspect {check / "tool.log"}.')
        check_context(organizer, job)
        job.record.update(status='Patch exports, masters and xEdit record checks passed; application pending')
    except Exception as error:
        job.record.update(status='Patching needs attention', error=str(error))
        raise
    finally:
        job.save()


def publish_job(organizer, job, on_done):
    import mobase
    from PyQt6.QtCore import QTimer
    check_context(organizer, job)
    name = own_name(organizer)
    target = Path(organizer.modsPath()) / name
    if not target.exists():
        mod = organizer.createMod(mobase.GuessedString(name))
        if mod is None or Path(mod.absolutePath()).resolve() != target.resolve():
            raise ValueError('MO2 could not create the expected patch output mod.')
    choices = {'settings': job.settings, 'input_state': job.record['input_state'],
        'build_record': str(job.directory / 'operation.json'), 'output_hashes': job.record['hashes'],
        'settings_hashes': settings_hashes(job.directory / 'Data'),
        'extra_data': str(job.directory / 'Data'), 'persistence': str(job.directory / 'persistence')}
    context = job.directory / 'recovery-context.json'
    context.write_text(json.dumps({'choices': choices,
        'persistence_hashes': settings_hashes(job.directory / 'persistence')}, indent=2), encoding='utf-8')
    projects = {output: {'outputs': [output], 'preset': ', '.join(p[1] for p in job.record['patchers'] if p[0] + '.esp' == output),
                 'source_mod': 'Selected active plugins; exact patcher revisions in build record',
                 'recovery_context_sha256': digest(context)} for output in job.record['outputs']}
    manifest = publish_output(target, job.directory, job.record['profile_path'], projects, job.record['hashes'], tool='Synthesis', replace_all=True)
    job.record.update(status='Patch output published; activation pending', installed_mod=name, previous_output=manifest['previous_output'])
    job.save()

    def ready():
        try:
            if organizer.profilePath() != job.record['profile_path']:
                raise ValueError('The profile changed. Output has not been enabled in another profile.')
            mods = organizer.modList()
            mods.setPriority(name, max(mods.priority(n) for n in mods.allMods()))
            if not mods.setActive(name, True):
                raise ValueError('MO2 could not enable the generated patch mod.')
            issues = effective_issues(organizer, target, job.record['hashes'])
            if issues:
                raise ValueError('\n'.join(issues))
            job.record['activated_plugins'] = activate_generated_plugins(organizer, target, job.record['hashes'], mobase.PluginState.ACTIVE)
            choices_path(organizer).write_text(json.dumps(choices, indent=2), encoding='utf-8')
            job.record.update(status='Patches applied and effective; record checks passed; gameplay check pending')
            job.save()
            on_done(target, None)
        except Exception as error:
            job.record.update(status='Patch activation needs attention', error=str(error)); job.save()
            on_done(target, str(error))

    organizer.onNextRefresh(lambda: QTimer.singleShot(0, ready), False)
    organizer.refresh()


def saved_build_current(organizer, saved):
    pipeline = inspect_pipeline(saved['settings'])
    target = Path(organizer.modsPath()) / own_name(organizer)
    if not target.is_dir(): return False
    manifest = read_manifest(target, organizer.profilePath(), 'Synthesis')
    if set(manifest['hashes']) != set(pipeline.outputs): return False
    expected = saved.get('output_hashes')
    if expected is None:
        expected = json.loads(Path(saved['build_record']).read_text(encoding='utf-8'))['hashes']
    if manifest['hashes'] != expected:
        raise ValueError('A different patch build is installed than the one described by your saved choices. Review Patch choices and its retained settings before regenerating.')
    issues = effective_issues(organizer, target, manifest['hashes'])
    if issues: raise ValueError('\n'.join(issues))
    if any(organizer.pluginList().loadOrder(name) < 0 for name in pipeline.outputs):
        raise ValueError('A generated patch is disabled. Enable it or review your patch choices.')
    if saved.get('settings_hashes', {}) != settings_hashes(saved['extra_data']):
        return False
    return json.loads(json.dumps(input_state(organizer, pipeline.outputs))) == saved['input_state']
