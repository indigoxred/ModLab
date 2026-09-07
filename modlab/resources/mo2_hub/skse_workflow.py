"""Install the official loader beside Skyrim and its scripts in the selected MO2 profile."""
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from uuid import uuid4

from .assessment import Finding
from .installation import InstallResult
from .outputs import digest, output_name, publish_output, read_manifest
from . import skse


def store_for(game_root):
    steam = (game_root / 'steam_api64.dll').is_file()
    gog = any(game_root.glob('goggame-*.info'))
    return ('Steam' if steam else 'GOG') if steam != gog else ''


def install_package(organizer, archive, checksum, package, version_reader, on_ready):
    import mobase
    from PyQt6.QtCore import QTimer
    game = organizer.managedGame()
    if game.gameName() != 'Skyrim Special Edition':
        raise ValueError('Select a Skyrim Special Edition instance before installing SKSE.')
    root = Path(game.gameDirectory().absolutePath()).resolve()
    profile_path, profile = organizer.profilePath(), organizer.profile().name()
    runtime = version_reader(str(root / 'SkyrimSE.exe'))
    skse.check_target(package, runtime, store_for(root))
    skse.require_game_closed()
    game_hash = digest(root / 'SkyrimSE.exe')
    job = Path(organizer.modsPath()).parent / 'builds/skse' / uuid4().hex[:12]
    job.mkdir(parents=True)
    target = Path(organizer.modsPath()) / output_name(profile, profile_path, 'SKSE scripts')
    record = dict(profile=profile, profile_path=profile_path, game_root=str(root), runtime=runtime,
                  package=asdict(package), archive=str(archive), archive_sha256=checksum,
                  created_at=datetime.now(timezone.utc).isoformat(), status='Preparing SKSE', installed_mod=target.name)
    record_path = job / 'operation.json'
    def save():
        record_path.write_text(json.dumps(record, indent=2), encoding='utf-8')
    save()
    deployment = None
    try:
        source = skse.extract_package(archive, checksum, package, job)
        output = job / 'output'
        scripts = output / 'Scripts'
        scripts.mkdir(parents=True)
        for path in (source / 'Data/Scripts').glob('*.pex'):
            shutil.copy2(path, scripts / path.name)
        if not (scripts / 'skse.pex').is_file():
            raise ValueError('The SKSE package has no compiled core script.')
        hashes = {p.relative_to(output).as_posix(): digest(p) for p in scripts.iterdir()}
        for name in ('skse64_readme.txt', 'skse64_whatsnew.txt'):
            shutil.copy2(source / name, job / name)
        if organizer.profilePath() != profile_path or Path(game.gameDirectory().absolutePath()).resolve() != root:
            raise ValueError('The selected profile or game changed before SKSE installation.')
        if target.exists():
            read_manifest(target, profile_path, 'SKSE scripts')
        else:
            mod = organizer.createMod(mobase.GuessedString(target.name))
            if mod is None or Path(mod.absolutePath()).resolve() != target.resolve():
                raise ValueError('MO2 could not create the SKSE script mod.')
        skse.require_game_closed()
        deployment = skse.deploy_root(root, source, job, game_hash)
        record['root_deployment'] = str(job / 'root-deployment.json')
        projects = {'SKSE scripts': dict(outputs=list(hashes), preset=package.version, source_mod='Official SKSE ' + package.store)}
        manifest = publish_output(target, job, profile_path, projects, hashes, tool='SKSE scripts', replace_all=True)
        record.update(status='Installed; activation pending', hashes=hashes, previous_output=manifest['previous_output'])
        save()
    except Exception as error:
        if deployment and deployment['applied']:
            try:
                skse.restore_root(deployment, job)
            except Exception as recovery_error:
                record['recovery_error'] = str(recovery_error)
        record.update(status='SKSE installation needs attention', error=str(error))
        save()
        raise

    def ready():
        error = None
        try:
            if organizer.profilePath() != profile_path:
                raise ValueError('The profile changed; SKSE scripts were not enabled in another profile.')
            mods = organizer.modList()
            mods.setPriority(target.name, max(mods.priority(n) for n in mods.allMods()))
            if not mods.setActive(target.name, True):
                raise ValueError('MO2 could not enable the SKSE scripts.')
            for name, expected in hashes.items():
                effective = Path(organizer.resolvePath(name))
                if effective.resolve() != (target / name).resolve() or digest(effective) != expected:
                    raise ValueError('An installed SKSE script is not effective: ' + name)
            for name, state in deployment['files'].items():
                if digest(root / name) != state['after']:
                    raise ValueError('An installed SKSE root file changed: ' + name)
            record['status'] = 'Installed, enabled'
            record['detail'] = (f'SKSE {package.version} installed for {package.store} Skyrim {package.runtime}. '
                f'{len(hashes)} scripts are effective in this profile. Launch Skyrim uses SKSE through MO2. '
                'Loader files are shared by profiles using this game directory. In-game loading is not yet verified.')
        except Exception as failure:
            error = str(failure)
            record.update(status='Activation needs attention', error=error)
        save()
        on_ready(InstallResult(record['status'], target.name, str(target), len(hashes),
                               error or record['detail']), record_path)

    organizer.onNextRefresh(lambda: QTimer.singleShot(0, ready), False)
    organizer.refresh()
    return InstallResult('Installed; activation pending', target.name, str(target), len(hashes),
                         'SKSE files installed; checking the active scripts and root files.'), record_path


def inspect_skse(organizer):
    target = Path(organizer.modsPath()) / output_name(organizer.profile().name(), organizer.profilePath(), 'SKSE scripts')
    if not target.exists():
        return (), ()
    manifest = read_manifest(target, organizer.profilePath(), 'SKSE scripts')
    if not manifest.get('projects'):
        return (), ()
    record_path = Path(manifest['projects']['SKSE scripts']['build_record']).resolve()
    record_path.relative_to(Path(organizer.modsPath()).parent.resolve() / 'builds/skse')
    job = record_path.parent
    record = json.loads(record_path.read_text(encoding='utf-8'))
    deployment = json.loads((job / 'root-deployment.json').read_text(encoding='utf-8'))
    root = Path(organizer.managedGame().gameDirectory().absolutePath()).resolve()
    issues = []
    if root != Path(deployment['game_root']).resolve() or digest(root / 'SkyrimSE.exe') != deployment['game_hash']:
        issues.append('The Skyrim installation changed. Obtain SKSE for the current runtime before launching.')
    for name, state in deployment['files'].items():
        path = root / skse._root_name(name)
        if name not in deployment['applied'] or not path.is_file() or digest(path) != state['after']:
            issues.append('SKSE loader component is missing or changed: ' + name)
    verified = []
    for name, expected in manifest['hashes'].items():
        path = Path(organizer.resolvePath(name))
        if not path.is_file() or digest(path) != expected:
            issues.append('SKSE script is disabled, missing or replaced: ' + name)
        else:
            verified.append(name.casefold())
    if issues:
        return (), (Finding('Blocked', 'skse-installation-changed', 'SKSE setup needs attention', '\n'.join(issues),
            'Reinstall the matching official SKSE archive through ModLab, or restore the intended script provider. '
            'Check compatibility before keeping a replacement script.'),)
    return tuple(verified), (Finding('Info', 'skse-installation-checked', 'SKSE loader and scripts are installed',
        record.get('detail', '') + '\nReadme: ' + str(job / 'skse64_readme.txt'),
        'Use Launch Skyrim to run SKSE through MO2. Its startup result is reported separately.'),)
