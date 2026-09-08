"""Run PGPatcher in a private working copy, then check and publish its output."""
from dataclasses import dataclass
from datetime import datetime, timezone
import configparser
import json
from pathlib import Path
import shutil
from uuid import uuid4

from .helpers import HELPERS, load_locations, locate_helper
from .outputs import MANIFEST, digest, output_name, publish_output, read_manifest
from .pgpatcher import SUPPORTED_EXE, make_config, verify_output, review_warnings
from .synthesis_workflow import input_state
from .body_workflow import readable_path
from .pandora_workflow import effective_issues, activate_generated_plugins
from .skse import require_game_closed
from .skse_workflow import store_for
from .vfs import find_files
from .body_inputs import mesh_helper_state


def own_name(organizer):
    return output_name(organizer.profile().name(), organizer.profilePath(), 'Graphics')


def choices_path(organizer):
    return Path(organizer.profilePath()) / 'modlab-graphics-choices.json'


def load_choices(organizer):
    path = choices_path(organizer)
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None


def pending_path(organizer):
    return Path(organizer.profilePath()) / 'modlab-graphics-pending.json'


def load_pending(organizer):
    path = pending_path(organizer)
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None


def begin_pending(organizer, choices):
    request = dict(id=uuid4().hex, choices=choices, profile_path=organizer.profilePath(),
                   created_at=datetime.now(timezone.utc).isoformat(), status='Requested')
    pending_path(organizer).write_text(json.dumps(request, indent=2), encoding='utf-8')
    return request


def fail_pending(organizer, request_id, error):
    request = load_pending(organizer)
    if not request or request['id'] != request_id:
        raise ValueError('The pending graphics request changed.')
    request.update(error=str(error), status='Needs attention')
    pending_path(organizer).write_text(json.dumps(request, indent=2), encoding='utf-8')


def clear_pending(organizer, request_id):
    request = load_pending(organizer)
    if not request or request['id'] != request_id:
        raise ValueError('The pending graphics request changed.')
    pending_path(organizer).unlink()


def withdrawn_path(organizer):
    return Path(organizer.profilePath()) / 'modlab-graphics-withdrawn.json'


def withdraw_for_upstream(organizer, on_ready):
    """Temporarily remove only managed downstream output before source helpers run."""
    import mobase
    from PyQt6.QtCore import QTimer
    target = Path(organizer.modsPath()) / own_name(organizer)
    marker = withdrawn_path(organizer)
    if not target.exists():
        if marker.exists():
            raise ValueError('Temporarily withdrawn graphics output is missing. Review its retained build before retrying.')
        return False
    manifest=read_manifest(target, organizer.profilePath(), 'Graphics')
    active = bool(organizer.modList().state(target.name) & mobase.ModState.ACTIVE)
    if marker.exists():
        record = json.loads(marker.read_text(encoding='utf-8'))
        if record['profile_path'] != organizer.profilePath() or record['manifest'] != digest(target / MANIFEST):
            raise ValueError('Temporarily withdrawn graphics output changed. Review it before continuing.')
    elif active:
        installed_inputs={}
        # A saved rebuild must not undo an outside choice of file provider.
        # A pending request is an explicit new selection from Graphics choices.
        saved = load_choices(organizer)
        if saved and not load_pending(organizer):
            issues = effective_issues(organizer, target, saved['hashes'])
            if issues:
                from .graphics_install_inputs import pending_mesh_inputs
                installed_inputs=pending_mesh_inputs(organizer,saved['hashes'],manifest.get('updated_at'))
                issues=effective_issues(organizer,target,{name:checksum for name,checksum in saved['hashes'].items()
                                                       if name not in installed_inputs})
            disabled = [name for name in saved['hashes']
                       if Path(name).suffix.casefold() in {'.esp', '.esm', '.esl'}
                       and organizer.pluginList().loadOrder(name) < 0]
            if disabled:
                raise ValueError('Generated graphics plugin is disabled: ' + ', '.join(disabled) +
                                 '. Your choice was retained. Open Graphics choices to confirm whether to prepare this setup again.')
            if issues:
                raise ValueError(f'{len(issues)} prepared graphics files or plugins have changed outside the pending verified installs. '
                                 'Open Graphics choices to confirm the setup you want before regenerating. '
                                 'Your existing files and provider choices have been retained.')
        require_game_closed()
        marker.write_text(json.dumps(dict(profile_path=organizer.profilePath(), mod=target.name,
            manifest=digest(target / MANIFEST), installation_inputs=installed_inputs,
            withdrawn_at=datetime.now(timezone.utc).isoformat()),indent=2),encoding='utf-8')
    elif (load_choices(organizer) or {}).get('hashes') and not load_pending(organizer):
        raise ValueError('The selected graphics output is disabled. Re-enable its retained mod in MO2, then recheck.')
    if not active:
        return False
    require_game_closed()
    if not organizer.modList().setActive(target.name, False):
        raise ValueError('MO2 could not withdraw the previous graphics output.')
    organizer.onNextRefresh(lambda: QTimer.singleShot(0, on_ready), False)
    organizer.refresh()
    return True


def clear_withdrawn(organizer):
    marker = withdrawn_path(organizer)
    if marker.exists():
        record = json.loads(marker.read_text(encoding='utf-8'))
        if record['profile_path'] != organizer.profilePath():
            raise ValueError('Graphics withdrawal belongs to another profile.')
        marker.unlink()


def require_upstream_view(organizer):
    import mobase
    target = Path(organizer.modsPath()) / own_name(organizer)
    if target.exists() and organizer.modList().state(target.name) & mobase.ModState.ACTIVE:
        raise ValueError('Managed graphics output is still active. Recheck through ModLab to run source helpers before graphics.')


def state(organizer):
    return mesh_helper_state(input_state(organizer, (), output_mod=own_name(organizer)))


def locate_engine(organizer):
    saved = load_locations(Path(organizer.getPluginDataPath()) / 'modlab/helpers.json')
    paths = locate_helper(HELPERS['pgpatcher'], saved, find_files(organizer, '', ['PGPatcher.exe']))
    if len(paths) != 1 or digest(paths[0]) != SUPPORTED_EXE:
        raise ValueError('Select the complete official PGPatcher 1.3.0 installation in Setup / tool locations. '
                         'Download: ' + HELPERS['pgpatcher'].download)
    return paths[0]


@dataclass
class GraphicsJob:
    directory: Path
    executable: Path
    record: dict

    def save(self):
        (self.directory / 'operation.json').write_text(json.dumps(self.record, indent=2), encoding='utf-8')


def prepare_job(organizer, choices):
    import mobase
    if organizer.managedGame().gameName() != 'Skyrim Special Edition':
        raise ValueError('Select the intended Skyrim SE/AE profile.')
    require_game_closed()
    executable = locate_engine(organizer)
    root = Path(organizer.managedGame().gameDirectory().absolutePath())
    target = Path(organizer.modsPath()) / own_name(organizer)
    prior = read_manifest(target, organizer.profilePath(), 'Graphics') if target.exists() else None
    active = organizer.modList().state(target.name) & mobase.ModState.ACTIVE if prior else False
    current = state(organizer)
    if any(name.casefold() == 'dyndolod.esp' for name in current['order']):
        raise ValueError('Disable existing DynDOLOD and TexGen output before changing materials. '
                         'Keep their files for recovery; regenerate LOD after the new graphics output is applied.')
    if organizer.resolvePath('vramroutput.tmp'):
        raise ValueError('Disable the VRAMr output while generating materials; its files are retained.')
    marker = organizer.resolvePath('ParallaxGen_Diff.json')
    if marker and not readable_path(marker).resolve().is_relative_to(target.resolve()):
        raise ValueError('Another PGPatcher output is active. Disable that output mod before generating a new one; preserve its files.')
    renderer = choices.get('renderer')
    if renderer == 'Community Shaders' and not organizer.resolvePath('SKSE/Plugins/CommunityShaders.dll'):
        raise ValueError('The selected Community Shaders renderer is not present in the active profile. '
                         'Install its runtime-compatible release first: https://www.nexusmods.com/skyrimspecialedition/mods/86492')
    if renderer == 'ENB' and not ((root / 'enbseries.ini').is_file() and (root / 'd3d11.dll').is_file()):
        raise ValueError('The selected ENB installation is incomplete. Install the intended ENB binaries and preset before generating materials.')
    if choices.get('pbr'):
        if organizer.resolvePath('SKSE/Plugins/AutoParallax.dll'):
            raise ValueError('Auto Parallax is incompatible with this PBR choice. Disable its mod; PGPatcher supplies the material setup.')
        if any(name.casefold() == 'simplicity of snow.esp' for name in current['order']):
            raise ValueError('This PBR choice is incompatible with Simplicity of Snow’s multipass rendering. '
                             'Choose a supported snow setup or use non-PBR materials.')
    ini = configparser.ConfigParser(interpolation=None, strict=False)
    ini.read(Path(organizer.profilePath()) / 'Skyrim.ini', encoding='utf-8-sig')
    language = ini.get('General', 'sLanguage', fallback='English').strip().title()
    directory = Path(organizer.modsPath()).parent / 'builds/pgpatcher' / uuid4().hex[:12]
    config = make_config(choices, root, directory / 'instance', directory / 'output', store_for(root), language=language)
    directory.mkdir(parents=True)
    job = GraphicsJob(directory, directory / 'runner/PGPatcher.exe', dict(
        created_at=datetime.now(timezone.utc).isoformat(), profile_path=organizer.profilePath(),
        profile=organizer.profile().name(), choices=choices, input_state=current,
        prior_active=bool(active), prior_manifest=digest(target / MANIFEST) if prior and (target / MANIFEST).is_file() else None,
        helper_source=str(executable), helper_sha256=SUPPORTED_EXE, status='Preparing graphics build'))
    job.save()
    try:
        # PGPatcher uses this read-only snapshot for mod priority. The actual game
        # view still comes from MO2's VFS; its VFS check remains enabled.
        instance = directory / 'instance'; (instance / 'profiles/Input').mkdir(parents=True)
        (instance / 'ModOrganizer.ini').write_text(
            '[General]\ngameName=Skyrim Special Edition\ngame_edition=' + store_for(root) +
            '\ngamePath=@ByteArray(' + root.as_posix() + ')\nselected_profile=@ByteArray(Input)\n'
            '[Settings]\nprofiles_directory=' + (instance / 'profiles').as_posix() +
            '\nmod_directory=' + Path(organizer.modsPath()).as_posix() + '\n', encoding='utf-8')
        mods = organizer.modList()
        names = [name for name in sorted(mods.allMods(), key=mods.priority, reverse=True)
                 if name != target.name and mods.state(name) & mobase.ModState.ACTIVE]
        (instance / 'profiles/Input/modlist.txt').write_text(''.join('+' + name + '\n' for name in names), encoding='utf-8')
        shutil.copytree(readable_path(executable.parent), readable_path(job.executable.parent),
                        ignore=shutil.ignore_patterns('cfg', 'log', '*.pdb'))
        cfg = job.executable.parent / 'cfg'; cfg.mkdir()
        (cfg / 'settings.json').write_text(json.dumps(config, indent=2), encoding='utf-8')
        # Upstream reads the previous base plugin before clearing its output.
        # Supply that input in the private directory, preserving the original.
        if prior and (target / 'PGPatcher.esp').is_file():
            (directory / 'output').mkdir()
            shutil.copy2(target / 'PGPatcher.esp', directory / 'output/PGPatcher.esp')
            job.record['previous_base_plugin'] = digest(target / 'PGPatcher.esp')
        previous = load_choices(organizer)
        if previous and previous.get('modrules'):
            source = readable_path(previous['modrules'])
            if not source.is_file() or digest(source) != previous.get('modrules_sha256'):
                raise ValueError('Retained graphics conflict choices changed or are missing. Review them before rebuilding.')
            shutil.copy2(source, cfg / 'modrules.json')
        job.record['config_sha256'] = digest(cfg / 'settings.json')
        job.record['status'] = 'Prepared; waiting to withdraw previous output'
        check_context(organizer, job)
    except Exception as error:
        job.record.update(status='Graphics preparation failed', error=str(error))
        raise
    finally:
        job.save()
    return job


def check_context(organizer, job):
    if json.loads(json.dumps(state(organizer))) != json.loads(json.dumps(job.record['input_state'])):
        raise ValueError('The selected profile or material inputs changed. No new graphics output was accepted.')


def withdraw_previous(organizer, job, on_ready):
    from PyQt6.QtCore import QTimer
    check_context(organizer, job)
    if job.record['prior_active']:
        organizer.modList().setActive(own_name(organizer), False)
        organizer.onNextRefresh(lambda: QTimer.singleShot(0, on_ready), False)
        organizer.refresh()
    else:
        on_ready()


def restore_prior(organizer, job):
    if organizer.profilePath() != job.record['profile_path'] or not job.record['prior_active']:
        return
    target = Path(organizer.modsPath()) / own_name(organizer)
    if digest(target / MANIFEST) != job.record['prior_manifest']:
        raise ValueError('The prior graphics output changed; it was not automatically re-enabled.')
    read_manifest(target, organizer.profilePath(), 'Graphics')
    organizer.modList().setActive(target.name, True)
    organizer.refresh()


def close_completion(process_handle):
    """Acknowledge only this helper process's final dialog; never use it as a verdict."""
    import ctypes
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel.GetProcessId.argtypes = [w.HANDLE]; kernel.GetProcessId.restype = w.DWORD
    process_id = kernel.GetProcessId(w.HANDLE(int(process_handle)))
    if not process_id:
        raise OSError('The launched PGPatcher process could not be identified.')
    callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    user.EnumWindows.argtypes = [callback_type, w.LPARAM]; user.EnumWindows.restype = w.BOOL
    user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
    user.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
    user.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]; user.PostMessageW.restype = w.BOOL
    closed = []
    @callback_type
    def visit(window, unused):
        owner = w.DWORD(); user.GetWindowThreadProcessId(window, ctypes.byref(owner))
        if owner.value == process_id:
            title = ctypes.create_unicode_buffer(256)
            user.GetWindowTextW(window, title, len(title))
            if title.value == 'PGPatcher Generation Complete':
                closed.append(bool(user.PostMessageW(window, 0x0010, 0, 0)))
        return True
    user.EnumWindows(visit, 0)
    return any(closed)


def run_job(organizer, job):
    from PyQt6.QtCore import QTimer
    import mobase
    from .xedit_workflow import run_xedit
    check_context(organizer, job)
    if organizer.resolvePath('ParallaxGen_Diff.json'):
        raise ValueError('Previous PGPatcher output is still effective. Wait for MO2 refresh before retrying.')
    job.record['status'] = 'Generating graphics'; job.save()
    timer = QTimer(); timer.setInterval(500)
    ui_errors = []
    try:
        handle = organizer.startApplication(str(job.executable), ['--autostart'], str(job.executable.parent), organizer.profile().name())
        if not handle:
            raise ValueError('MO2 could not launch PGPatcher.')
        def acknowledge():
            try:
                if close_completion(handle):
                    timer.stop()
            except Exception as error:
                ui_errors.append(str(error)); timer.stop()
        timer.timeout.connect(acknowledge); timer.start()
        complete, code = organizer.waitForApplication(handle, False)
        if not complete:
            raise ValueError('PGPatcher is still running; let the existing process finish before retrying.')
        timer.stop()
        check_context(organizer, job)
        if digest(job.executable.parent / 'cfg/settings.json') != job.record['config_sha256']:
            raise ValueError('Graphics settings changed during generation. The result was withheld.')
        log = (job.executable.parent / 'log/PGPatcher.log').read_text(encoding='utf-8-sig', errors='replace')
        result = verify_output(job.directory / 'output', log, code, job.record['input_state']['order'])
        job.record.update(exit_code=code, hashes=result.hashes, outputs=list(result.plugins),
                          checked_meshes=result.checked_meshes, warnings=result.warnings, completion_ui_errors=ui_errors)
        held, explained = review_warnings(result.warnings,
            organizer.managedGame().gameDirectory().absolutePath(), organizer.resolvePath)
        job.record['explained_warnings'] = explained
        if held:
            raise ValueError('PGPatcher reported warnings that need review before this output can be applied:\n' + '\n'.join(held))
        generated = {name: job.directory / 'output' / name for name in result.plugins}
        job.record['record_checks'] = []
        for name in result.plugins:
            record, check, _, _ = run_xedit(organizer, name, 'check', 'Check generated graphics plugin before publication',
                                          mobase.getFileVersion, generated_inputs=generated)
            job.record['record_checks'].append(str(record / 'operation.json')); job.save()
            if check['record_errors']:
                raise ValueError(f'{name} has record errors. The generated output was withheld; see {record}.')
        check_context(organizer, job)
        job.record['status'] = 'Graphics output checked; application pending' if result.hashes else 'No graphics changes generated'
    except Exception as error:
        job.record.update(status='Graphics generation needs attention', error=str(error))
        raise
    finally:
        timer.stop(); job.save()


def save_applied_choices(organizer, job):
    rules = job.executable.parent / 'cfg/modrules.json'
    choices_path(organizer).write_text(json.dumps(dict(
        status='applied', choices=job.record['choices'], build_record=str(job.directory / 'operation.json'),
        hashes=job.record['hashes'], input_state=job.record['input_state'], warnings=job.record.get('warnings', []),
        explained_warnings=job.record.get('explained_warnings', []),
        modrules=str(rules) if rules.is_file() else None, modrules_sha256=digest(rules) if rules.is_file() else None,
    ), indent=2), encoding='utf-8')
    if job.record.get('request_id'):
        clear_pending(organizer, job.record['request_id'])
    clear_withdrawn(organizer)


def publish_job(organizer, job, on_done):
    import mobase
    from PyQt6.QtCore import QTimer
    check_context(organizer, job)
    hashes = job.record['hashes']
    if not hashes:
        save_applied_choices(organizer, job)
        on_done(None, None)
        return
    target = Path(organizer.modsPath()) / own_name(organizer)
    if not target.exists():
        mod = organizer.createMod(mobase.GuessedString(target.name))
        if mod is None or Path(mod.absolutePath()).resolve() != target.resolve():
            raise ValueError('MO2 could not create the intended graphics output mod.')
    project = {'Graphics materials and meshes': {
        'outputs': list(hashes), 'preset': job.record['choices']['renderer'],
        'source_mod': 'Active mesh/texture providers and exact input order retained in the build record'}}
    manifest = publish_output(target, job.directory, organizer.profilePath(), project, hashes, tool='Graphics', replace_all=True)
    job.record.update(status='Graphics output published; activation pending', installed_mod=target.name,
                      previous_output=manifest.get('previous_output')); job.save()
    def ready():
        try:
            check_context(organizer, job)
            mods = organizer.modList()
            mods.setPriority(target.name, max(mods.priority(name) for name in mods.allMods()))
            if not mods.setActive(target.name, True):
                raise ValueError('MO2 could not enable the new graphics output.')
            issues = effective_issues(organizer, target, hashes)
            if issues:
                raise ValueError('\n'.join(issues))
            job.record['activated_plugins'] = activate_generated_plugins(organizer, target, hashes, mobase.PluginState.ACTIVE)
            save_applied_choices(organizer, job)
            job.record['status'] = 'Graphics output applied; gameplay unverified'; job.save()
            on_done(target, None)
        except Exception as error:
            job.record.update(status='Graphics activation needs attention', error=str(error)); job.save()
            on_done(target, str(error))
    organizer.onNextRefresh(lambda: QTimer.singleShot(0, ready), False)
    organizer.refresh()


def saved_build_current(organizer, saved, *, withdrawn=False):
    import mobase
    if saved.get('status') != 'applied':
        raise ValueError(saved.get('error') or 'Your selected graphics changes have not been accepted. Open Graphics choices to retry or cancel the request.')
    target = Path(organizer.modsPath()) / own_name(organizer)
    if saved['hashes']:
        try:
            target.stat()
        except FileNotFoundError:
            # A copied profile can retain choices without its generated mod.
            # Rebuild it; existing output still requires the ownership checks below.
            return False
        manifest = read_manifest(target, organizer.profilePath(), 'Graphics')
        if manifest['hashes'] != saved['hashes'] or (not withdrawn and effective_issues(organizer, target, saved['hashes'])):
            return False
        if not withdrawn and any(organizer.pluginList().loadOrder(name) < 0 for name in saved['hashes'] if Path(name).suffix.casefold() == '.esp'):
            raise ValueError('A generated graphics plugin is disabled. Enable it or review Graphics choices.')
    elif target.exists() and organizer.modList().state(target.name) & mobase.ModState.ACTIVE:
        raise ValueError('Older graphics output is active even though the last build generated no changes. Disable that older output.')
    return json.loads(json.dumps(state(organizer))) == mesh_helper_state(saved['input_state'])


def reapply_saved(organizer, saved, on_done):
    import mobase
    from PyQt6.QtCore import QTimer
    target = Path(organizer.modsPath()) / own_name(organizer)
    if not saved_build_current(organizer, saved, withdrawn=True):
        raise ValueError('Graphics inputs changed; the previous output cannot be reapplied as current.')
    if not saved['hashes']:
        clear_withdrawn(organizer)
        on_done(target, None)
        return
    marker = withdrawn_path(organizer)
    if not marker.is_file():
        raise ValueError('Graphics output was not withdrawn by this workflow; review its enabled state.')
    record = json.loads(marker.read_text(encoding='utf-8'))
    if record['profile_path'] != organizer.profilePath() or record['manifest'] != digest(target / MANIFEST):
        raise ValueError('The previous graphics output changed while withdrawn.')
    if not organizer.modList().setActive(target.name, True):
        raise ValueError('MO2 could not re-enable the checked graphics output.')
    def ready():
        try:
            if organizer.profilePath() != record['profile_path']:
                raise ValueError('The profile changed during graphics activation.')
            issues = effective_issues(organizer, target, saved['hashes'])
            if issues:
                raise ValueError('\n'.join(issues))
            activate_generated_plugins(organizer, target, saved['hashes'], mobase.PluginState.ACTIVE)
            clear_withdrawn(organizer)
            on_done(target, None)
        except Exception as error:
            on_done(target, str(error))
    organizer.onNextRefresh(lambda: QTimer.singleShot(0, ready), False)
    organizer.refresh()


def inspect_graphics(organizer, body_target=None, body_manifest=None):
    """Report saved choices and only recognize transformations from a current build."""
    from .assessment import Finding
    from .pgpatcher import transformed_body_files
    pending, saved = load_pending(organizer), load_choices(organizer)
    findings = []
    if pending:
        findings.append(Finding('Review', 'graphics-pending', 'Selected graphics work has not finished',
            pending.get('error') or 'The requested material/mesh setup has not reached an accepted result.',
            'Use Recheck and finish setup to run it after bodies and patches, or open Graphics choices to change/cancel the pending request.'))
    if withdrawn_path(organizer).exists():
        findings.append(Finding('Review', 'graphics-withdrawn', 'Graphics output is temporarily inactive',
            'ModLab retained the previous output while its source helpers run.',
            'Finish the setup check before launching; ModLab will reapply current output or rebuild it.'))
        return tuple(findings), set()
    if not saved:
        return tuple(findings), set()
    if not saved_build_current(organizer, saved):
        findings.append(Finding('Review', 'graphics-stale', 'Generated graphics need an update',
            'The current inputs or output no longer match the saved graphics build.',
            'Use Recheck and finish setup. ModLab will run graphics after the applicable source helpers.'))
        return tuple(findings), set()
    findings.append(Finding('Info', 'graphics-current', 'Selected graphics output is current',
        'Rendering choice: ' + saved['choices']['renderer'] + '\nBuild: ' + saved['build_record'],
        'No regeneration is needed. Check the affected surfaces in game; file checks cannot establish appearance.'))
    for index, item in enumerate(saved.get('explained_warnings', [])):
        findings.append(Finding('Info', f'graphics-archive-note-{index}', 'Graphics archive notice explained',
            item['explanation'], 'No archive change is needed for this notice. Original helper message: ' + item['warning']))
    transformed = set()
    target = Path(organizer.modsPath()) / own_name(organizer)
    if body_manifest and saved['hashes']:
        transformed = transformed_body_files(body_target, body_manifest, target,
            read_manifest(target, organizer.profilePath(), 'Graphics'), organizer.resolvePath)
    return tuple(findings), transformed
