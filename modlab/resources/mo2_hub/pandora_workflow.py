"""Run the checked Pandora release through MO2 with private, recoverable output."""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path, PureWindowsPath
import shutil
import subprocess
from uuid import uuid4

from .body_workflow import readable_path
from .helpers import HELPERS, load_locations, locate_helper
from .loot_workflow import context_signature
from .outputs import digest, output_name, publish_output, read_manifest
from .pandora import read_patch, selection_entries, verify_output
from .vfs import find_files
from .runtime import ensure_net10, dotnet_environment


# Official v4.4.0-beta release. Its FileVersion is 1.0.0.0, so that field cannot identify it.
SUPPORTED_EXE = '97de385420b4a37b2690eee5409deabb753237b0ebdcbe819b6248e4c64c41ac'
SUPPORTED_FNIS_STUB = '28edbfa461abf408edf3c8d279b7c4839b048e9cf3e4dd467124f33429498d06'
PATCH_ROOTS = (('Pandora_Engine/mod', 'info.xml'), ('Nemesis_Engine/mod', 'info.ini'))


def source_signature(roots):
    """Include shadowed source files and priority; exclude our output at the caller."""
    result = []
    for name, root in roots:
        root = readable_path(root)
        entries = []
        for folder in ('meshes', 'Pandora_Engine/mod', 'Nemesis_Engine/mod'):
            for path in (root / folder).rglob('*'):
                if path.is_file():
                    # FaceGen contains NPC face meshes, not behavior inputs. Including
                    # regenerated faces creates a cycle: faces rebuild behaviors,
                    # whose changed output timestamps then rebuild faces again.
                    if path.relative_to(root).as_posix().casefold().startswith('meshes/actors/character/facegendata/'):
                        continue
                    stat = path.stat()
                    entries.append((path.relative_to(root).as_posix(), stat.st_size, stat.st_mtime_ns))
        for path in root.glob('*.bsa'):
            stat = path.stat()
            entries.append((path.name, stat.st_size, stat.st_mtime_ns))
        if entries:
            result.append((name, str(root), tuple(sorted(entries))))
    return tuple(result)


def input_roots(organizer):
    import mobase
    mods = organizer.modList()
    own = output_name(organizer.profile().name(), organizer.profilePath(), 'Pandora')
    result = [('Game Data', Path(organizer.managedGame().gameDirectory().absolutePath()) / 'Data')]
    for name in sorted(mods.allMods(), key=mods.priority):
        if name != own and mods.state(name) & mobase.ModState.ACTIVE:
            mod = mods.getMod(name)
            if mod is not None:
                result.append((name, Path(mod.absolutePath())))
    return result


def effective_issues(organizer, target, hashes):
    issues = []
    for relative, expected in hashes.items():
        value = organizer.resolvePath(relative)
        path = Path(value) if value else None
        if (not path or not path.is_file() or path.resolve() != (target / relative).resolve()
                or digest(path) != expected):
            issues.append('Generated file is missing, changed or overridden: ' + relative)
    return issues


def activate_generated_plugins(organizer, target, hashes, active_state):
    plugins = organizer.pluginList()
    activated = []
    for name, checksum in hashes.items():
        if Path(name).suffix.casefold() not in {'.esp', '.esl', '.esm'}:
            continue
        if effective_issues(organizer, target, {name: checksum}):
            raise ValueError('Generated plugin is not the effective file: ' + name)
        if plugins.state(name) != active_state:
            plugins.setState(name, active_state)
            if plugins.state(name) != active_state:
                raise ValueError('MO2 could not enable the generated plugin: ' + name)
            activated.append(name)
    return activated


def choices_path(organizer):
    return Path(organizer.profilePath()) / 'modlab-animation-choices.json'


def load_choices(organizer):
    path = choices_path(organizer)
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None


def needs_behaviors(organizer):
    for root, pattern in PATCH_ROOTS:
        if next(iter(find_files(organizer, root, [pattern])), None):
            return True
    return next(iter(find_files(organizer, 'meshes', ['FNIS_*_List.txt'])), None) is not None


def locate_engine(organizer):
    config = Path(organizer.getPluginDataPath()) / 'modlab/helpers.json'
    matches = locate_helper(HELPERS['pandora'], load_locations(config),
                            organizer.findFiles('', list(HELPERS['pandora'].filenames)))
    if len(matches) != 1:
        raise ValueError('Pandora is needed for these behavior choices. Download the complete supported 4.4.0-beta package from '
                         + HELPERS['pandora'].download + ' and select its executable in Setup / tool locations.')
    executable = matches[0]
    if digest(executable) != SUPPORTED_EXE:
        raise ValueError('This adapter supports the official Pandora 4.4.0-beta executable. Select that release in Setup / tool locations.')
    root = executable.parent
    if not (root / 'Pandora_Engine/Skyrim/Template').is_dir() or not (root / 'Pandora_Engine/mod/pandora/info.xml').is_file():
        raise ValueError('Pandora resources are missing. Extract the complete package before selecting the executable.')
    return executable


def available_patches(organizer, executable):
    patches = {}
    for root, pattern in PATCH_ROOTS:
        paths = [(p, 'Pandora bundled patches') for p in (executable.parent / root).glob('*/' + pattern)]
        for physical in find_files(organizer, root, [pattern]):
            path = readable_path(physical)
            if not path.is_file():
                path = readable_path(organizer.resolvePath(physical))
            try:
                origin = path.resolve().relative_to(readable_path(organizer.modsPath()).resolve()).parts[0]
            except ValueError:
                origin = 'Game Data'
            paths.append((path, origin))
        for path, origin in paths:
            patch = read_patch(path, origin)
            if patch.code.casefold() == 'pandora':
                continue
            previous = patches.get(patch.code.casefold())
            if previous and previous.metadata.resolve() != patch.metadata.resolve():
                raise ValueError(f'Two providers define the same animation patch: {patch.name} [{patch.code}]. '
                                 'Choose one provider before building; Pandora otherwise silently keeps its first copy.')
            patches[patch.code.casefold()] = patch
    return tuple(sorted(patches.values(), key=lambda p: p.name.casefold()))


def patch_identity(patches):
    return {p.code: [p.name, p.source_mod, digest(p.metadata)] for p in patches}


@dataclass
class PandoraJob:
    directory: Path
    executable: Path
    patches: tuple
    signature: tuple
    sources: tuple
    record: dict

    def save(self):
        (self.directory / 'operation.json').write_text(json.dumps(self.record, indent=2), encoding='utf-8')


def prepare_job(organizer, *, preview=False):
    if organizer.managedGame().gameName() != 'Skyrim Special Edition':
        raise ValueError('Select Skyrim Special Edition before generating behaviors.')
    executable = locate_engine(organizer)
    patches = available_patches(organizer, executable)
    if preview:
        return PandoraJob(None, executable, patches, context_signature(organizer), (),
            dict(profile=organizer.profile().name(), profile_path=organizer.profilePath(),
                 patch_identity=patch_identity(patches)))
    runtime = ensure_net10(Path(organizer.modsPath()).parent / 'tools')
    signature = context_signature(organizer)
    sources = source_signature(input_roots(organizer) + [('Pandora helper', executable.parent)])
    fnis_codes = sorted({''.join(PureWindowsPath(path).stem.split())
                         for path in find_files(organizer, 'meshes', ['FNIS_*_List.txt'])})
    directory = Path(organizer.modsPath()).parent / 'builds/pandora' / uuid4().hex[:12]
    runner = directory / 'runner'
    directory.mkdir(parents=True)
    job = PandoraJob(directory, runner / executable.name, patches, signature, sources,
        {'profile': organizer.profile().name(), 'profile_path': organizer.profilePath(), 'version': '4.4.0-beta',
         'created_at': datetime.now(timezone.utc).isoformat(), 'sources': sources,
         'patch_identity': patch_identity(patches), 'helper_source': str(executable.parent),
         'fnis_codes': fnis_codes, 'runtime': str(runtime), 'status': 'Awaiting patch choices'})
    job.save()
    try:
        shutil.copytree(readable_path(executable.parent), readable_path(runner),
                        ignore=shutil.ignore_patterns('Output', 'Engine.log', 'Settings.json', 'ActiveMods.json', 'PreviousOutput.txt'))
        (runner / 'Settings.json').write_text('{}', encoding='utf-8')
        check_context(organizer, job)
    except Exception as error:
        job.record.update(status='Preparation failed', error=str(error))
        job.save()
        raise ValueError('Pandora’s private working copy could not be prepared. No generated output was applied. '
                         f'Build details: {directory / "operation.json"}') from error
    return job


def check_context(organizer, job):
    roots = input_roots(organizer) + [('Pandora helper', Path(job.record['helper_source']))]
    if job.signature != context_signature(organizer) or job.sources != source_signature(roots):
        raise ValueError('The profile or animation inputs changed. Recheck before generating or applying output.')


def run_job(organizer, job, selected):
    check_context(organizer, job)
    entries = selection_entries(job.patches, selected)
    output = job.directory / 'output'
    (output / 'Pandora_Engine').mkdir(parents=True)
    (output / 'Pandora_Engine/ActiveMods.json').write_text(json.dumps(entries, indent=2), encoding='utf-8')
    # PathSettings initializes from saved settings; --output alone is insufficient in 4.4.0.
    paths = {'gameDataPath': str(Path(organizer.managedGame().gameDirectory().absolutePath()) / 'Data'),
             'outputPath': str(output)}
    settings_path = job.executable.parent / 'Settings.json'
    settings_path.write_text(json.dumps({'games': {'SkyrimSE': paths}}, indent=2), encoding='utf-8')
    args = ['--tesv', organizer.managedGame().gameDirectory().absolutePath(), '--output', str(output), '--auto_run', '--auto_close']
    job.record.update(status='Generating behaviors', selected=list(selected), arguments=args)
    job.save()
    try:
        with dotnet_environment(job.record['runtime']):
            handle = organizer.startApplication(str(job.executable), [subprocess.list2cmdline(args)], str(job.executable.parent), organizer.profile().name())
        if not handle:
            raise ValueError('Pandora could not start. Previous generated output remains unchanged.')
        complete, code = organizer.waitForApplication(handle, False)
        if not complete:
            raise ValueError('Pandora has not completed. Close it normally and inspect the retained build log.')
        check_context(organizer, job)
        recorded_paths = json.loads(settings_path.read_text(encoding='utf-8-sig')).get('games', {}).get('SkyrimSE', {})
        if any(Path(recorded_paths.get(key, '')).resolve() != Path(value).resolve() for key, value in paths.items()):
            raise ValueError('Pandora changed its game or output directory. Inspect the retained build before retrying.')
        names = {p.code: p.name for p in job.patches}
        result = verify_output(output, {key: names[key] for key in selected}, code, fnis_codes=job.record['fnis_codes'])
        # Pandora ships an empty compatibility plugin. Preserve any existing provider;
        # add this exact supplied stub only for FNIS lists that lack one.
        existing_stub = organizer.resolvePath('FNIS.esp')
        owned_stub = Path(organizer.modsPath()) / output_name(job.record['profile'], job.record['profile_path'], 'Pandora') / 'FNIS.esp'
        if job.record['fnis_codes'] and (not existing_stub or Path(existing_stub).resolve() == owned_stub.resolve()):
            stub = job.executable.parent / 'FNIS.esp'
            if not stub.is_file() or digest(stub) != SUPPORTED_FNIS_STUB:
                raise ValueError('The supported Pandora FNIS compatibility plugin is missing or changed. Restore the complete package.')
            shutil.copy2(stub, output / 'FNIS.esp')
            result.hashes['FNIS.esp'] = SUPPORTED_FNIS_STUB
            job.record['compatibility_plugin'] = 'FNIS.esp supplied with Pandora 4.4.0-beta; not the FNIS generator'
        job.record.update(status='Generated files checked; application pending', hashes=result.hashes, animations=result.animations)
        return result
    except Exception as error:
        job.record.update(status='Needs attention', error=str(error))
        raise
    finally:
        job.save()


def publish_job(organizer, job, on_done):
    import mobase
    from PyQt6.QtCore import QTimer
    check_context(organizer, job)
    name = output_name(job.record['profile'], job.record['profile_path'], 'Pandora')
    target = Path(organizer.modsPath()) / name
    if not target.exists():
        mod = organizer.createMod(mobase.GuessedString(name))
        if mod is None or Path(mod.absolutePath()).resolve() != target.resolve():
            raise ValueError('MO2 could not create the expected generated behavior mod.')
    selected = [p for p in job.patches if p.code in job.record['selected']]
    projects = {'Behaviors': {'outputs': list(job.record['hashes']),
        'preset': ', '.join(p.name for p in selected) or 'Base behaviors and detected FNIS animation lists',
        'source_mod': ', '.join(dict.fromkeys(p.source_mod for p in selected)) or 'Detected active animation inputs'}}
    manifest = publish_output(target, job.directory, job.record['profile_path'], projects, job.record['hashes'], tool='Pandora', replace_all=True)
    job.record.update(status='Output published; activation pending', installed_mod=name, previous_output=manifest['previous_output'])
    job.save()

    def ready():
        try:
            if organizer.profilePath() != job.record['profile_path']:
                raise ValueError('The selected profile changed; output was not enabled in another profile.')
            mods = organizer.modList()
            mods.setPriority(name, max(mods.priority(n) for n in mods.allMods()))
            if not mods.setActive(name, True):
                raise ValueError('The generated behavior mod could not be enabled.')
            issues = effective_issues(organizer, target, job.record['hashes'])
            if issues:
                raise ValueError('\n'.join(issues[:20]))
            job.record['activated_plugins'] = activate_generated_plugins(organizer, target, job.record['hashes'], mobase.PluginState.ACTIVE)
            # Settings describe the successfully applied build, not an attempted/failed run.
            choices_path(organizer).write_text(json.dumps({
                'selected': job.record['selected'], 'patch_identity': job.record['patch_identity'],
                'sources': job.sources, 'build_record': str(job.directory / 'operation.json')}, indent=2), encoding='utf-8')
            job.record.update(status='Generated behaviors applied and effective; in-game animation check pending')
            job.save()
            on_done(target, None)
        except Exception as error:
            job.record.update(status='Behavior output needs attention', error=str(error))
            job.save()
            on_done(target, str(error))

    organizer.onNextRefresh(lambda: QTimer.singleShot(0, ready), False)
    organizer.refresh()


def saved_build_current(organizer, saved):
    target = Path(organizer.modsPath()) / output_name(organizer.profile().name(), organizer.profilePath(), 'Pandora')
    manifest = read_manifest(target, organizer.profilePath(), 'Pandora') if target.is_dir() else None
    if not manifest:
        return False
    issues = effective_issues(organizer, target, manifest['hashes'])
    if issues:
        raise ValueError('\n'.join(issues[:12]) + '\nChoose the intended provider before rebuilding; ModLab will not undo your override automatically.')
    for name in manifest['hashes']:
        if Path(name).suffix.casefold() in {'.esp', '.esl', '.esm'} and organizer.pluginList().loadOrder(name) < 0:
            raise ValueError('The generated compatibility plugin is disabled: ' + name + '. Enable it or review the animation setup before launching.')
    roots = input_roots(organizer) + [('Pandora helper', locate_engine(organizer).parent)]
    # Earlier receipts included mods without animation inputs. Ignore those empty
    # entries so updating ModLab itself does not force an unnecessary build.
    previous = [entry for entry in saved.get('sources', []) if entry[2]]
    return previous == json.loads(json.dumps(source_signature(roots)))
