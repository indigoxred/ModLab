"""Known official SKSE packages and recoverable deployment of their root files.

Package identities: author's Nexus page 30379, checked 2026-09-06.
The original package is retained locally and is never redistributed by ModLab.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import zipfile

from .bodyslide import relative_path
from .outputs import digest


def packed_loader_version(text):
    """SKSE's Windows resource stores 2.2.8 as 0.2.2.8 (also comma-formatted)."""
    from .native import packed_version
    parts = [part.strip() for part in text.replace(',', '.').split('.')]
    if len(parts) == 4 and parts[0] == '0':
        parts = parts[1:]
    if not parts or not parts[0].isdigit() or int(parts[0]) == 0:
        raise ValueError('The SKSE loader version could not be identified.')
    return packed_version('.'.join(parts))


@dataclass(frozen=True)
class Package:
    version: str
    runtime: str
    store: str
    folder: str


PACKAGES = {
    'ada6a771e18245cd0a6106b7e019b3b2bdf7a6bee390bc069c6e0a35f89544fa':
        Package('2.2.8', '1.6.1170', 'Steam', 'skse64_2_02_08'),
    '7bad616ed360823a027f8828801d91e3a4aa2eacd952023af7f3e31adb2ae250':
        Package('2.3.1', '1.7.104', 'Steam', 'skse64_2_03_01'),
    '25d161cc82f7048458af442c8accc3c97bde86288dbc75cf622afa317791d654':
        Package('2.2.6', '1.6.1179', 'GOG', 'skse64_2_02_06'),
}


def check_target(package, runtime, store):
    if runtime.removesuffix('.0') != package.runtime or store.casefold() != package.store.casefold():
        resolution = 'Get the package matching the game runtime and store from https://skse.silverlock.org/.'
        if runtime in {'1.6.1170', '1.6.1170.0'} and store.casefold() == 'steam':
            from .game_setup import SKSE_URL
            resolution = 'For this Steam 1.6.1170 setup, install SKSE 2.2.8: ' + SKSE_URL + ' .'
        raise ValueError(f'SKSE {package.version} requires Skyrim {package.runtime} from {package.store}; '
                         f'this instance reports {runtime or "unknown runtime"} / {store or "unknown store"}. '
                         + resolution)


def identify(archive):
    checksum = digest(Path(archive))
    package = PACKAGES.get(checksum)
    if package is None and ('skse' in Path(archive).name.casefold() or 'script extender' in Path(archive).name.casefold()):
        # Many ordinary mods include SKSE in their names. Only loader contents
        # belong to the root-file installer; dependent mods use native MO2.
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as opened:
                names = opened.namelist()
        else:
            listed = subprocess.run([r'C:\Windows\System32\tar.exe', '-tf', str(archive)],
                capture_output=True, timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if listed.returncode:
                raise ValueError('Could not inspect this archive before choosing its installer: ' + str(archive))
            names = listed.stdout.decode('utf-8', errors='replace').splitlines()
        if any(name.replace('\\', '/').rsplit('/', 1)[-1].casefold() == 'skse64_loader.exe' for name in names):
            raise ValueError('This SKSE archive is not a recognized current official package. '
                             'Download the matching Steam or GOG main file from https://skse.silverlock.org/. '
                             'ModLab has not copied it into the game.')
    return checksum, package


def extract_package(archive, checksum, package, job):
    retained = job / 'original.7z'
    shutil.copy2(archive, retained)
    if digest(retained) != checksum or checksum not in PACKAGES:
        raise ValueError('The SKSE package changed before extraction.')
    stage = job / 'package'
    stage.mkdir()
    # Only exact published packages reach extraction, into a new job directory.
    command = [r'C:\Windows\System32\tar.exe', '-xf', str(retained), '-C', str(stage)]
    result = subprocess.run(command, capture_output=True, timeout=60,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError('SKSE extraction failed: ' + result.stderr.decode(errors='replace')[:1500])
    source = stage / package.folder
    required = [source / 'skse64_loader.exe', source / ('skse64_' + package.runtime.replace('.', '_') + '.dll'),
                source / 'Data/Scripts/skse.pex']
    if any(not p.is_file() or p.is_symlink() for p in required):
        raise ValueError('The extracted SKSE package is incomplete.')
    return source


def require_game_closed():
    result = subprocess.run([r'C:\Windows\System32\tasklist.exe', '/FI', 'IMAGENAME eq SkyrimSE.exe', '/FO', 'CSV', '/NH'],
                            capture_output=True, timeout=15, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError('Could not check whether Skyrim is running. Close it and retry setup.')
    if b'skyrimse.exe' in result.stdout.lower():
        raise ValueError('Close Skyrim before changing installed mods, generated output or plugin order. Then recheck the setup.')


def _root_name(name):
    if name not in {'skse64_loader.exe', 'd3dx9_42.dll'} and not re.fullmatch(r'skse64_\d+_\d+_\d+\.dll', name):
        raise ValueError('Not a supported SKSE root file: ' + name)
    return relative_path(name)


def deploy_root(game, source, job, game_hash):
    source=Path(source)
    names = ['skse64_loader.exe'] + sorted(p.name for p in source.glob('skse64_*.dll'))
    if len(names) != 2:
        raise ValueError('The SKSE package must contain one loader and one runtime DLL.')
    return deploy_root_files(game,source,job,game_hash,names)


def deploy_root_files(game, source, job, game_hash, names, *, expected=None):
    """Shared checked publication for the named SKSE loader/preloader files only."""
    from .installation import write_record
    game, source, job = Path(game).resolve(), Path(source), Path(job)
    if not (game / 'SkyrimSE.exe').is_file() or digest(game / 'SkyrimSE.exe') != game_hash:
        raise ValueError('The selected game executable changed before root-component installation.')
    if not names or len(set(names))!=len(names):
        raise ValueError('Choose distinct supported root-component files.')
    previous = job / 'previous-root'
    previous.mkdir()
    record = {'game_root': str(game), 'game_hash': game_hash, 'files': {}, 'applied': []}
    for name in names:
        _root_name(name)
        target = game / name
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError('SKSE destination is not a regular file: ' + name)
        old = digest(target) if target.exists() else None
        record['files'][name] = {'before': old, 'after': digest(source / name)}
        if expected is not None and record['files'][name]!=expected.get(name):
            raise ValueError('The root-component source or destination changed before publication: '+name)
        if old is not None:
            shutil.copy2(target, previous / name)
            if digest(previous / name) != old:
                raise ValueError('SKSE backup could not be verified: ' + name)
    journal = job / 'root-deployment.json'
    write_record(journal, record)
    pending=None
    try:
        for name in names:
            target = game / name
            state = record['files'][name]
            actual = digest(target) if target.exists() else None
            if actual != state['before']:
                raise ValueError('An SKSE root file changed during installation: ' + name)
            # Prepare in the game filesystem before replacing the destination.
            candidate = game / (name + '.modlab-pending')
            if candidate.exists() or candidate.is_symlink():
                raise ValueError('A previous root-component installation needs recovery: ' + str(candidate))
            pending=candidate
            shutil.copy2(source / name, pending)
            if digest(pending) != state['after']:
                raise ValueError('SKSE copy verification failed: ' + name)
            if ((digest(target) if target.exists() else None)!=state['before'] or target.is_symlink() or
                    digest(game/'SkyrimSE.exe')!=game_hash):
                raise ValueError('The game or root-component destination changed during publication: '+name)
            # Skyrim/MO2 are Windows-only here. Windows rename fails if a new
            # destination appeared; replace would silently overwrite that file.
            if state['before'] is None and os.name=='nt':os.rename(pending,target)
            else:os.replace(pending, target)
            pending=None
            record['applied'].append(name)
            write_record(journal, record)
        return record
    except Exception:
        if pending is not None and pending.is_file() and not pending.is_symlink():
            retained=job/'failed-root';retained.mkdir(exist_ok=True)
            destination=retained/pending.name
            if not destination.exists():shutil.move(pending,destination)
        restore_root(record, job)
        raise


def restore_root(record, job):
    from .installation import _plain, write_record
    game, job = Path(record['game_root']).resolve(), Path(job)
    if digest(game / 'SkyrimSE.exe') != record['game_hash']:
        raise ValueError('The game changed since this SKSE deployment; automatic restoration was stopped.')
    withdrawn = job / 'withdrawn-root'
    _plain(withdrawn); _plain(job/'previous-root')
    def fingerprint(path):
        _plain(path)
        if path.exists() and not path.is_file():
            raise ValueError('A root restoration path changed: ' + str(path))
        return digest(path) if path.is_file() else None
    # The journal can lag publication by one rename. Reconcile every recorded
    # before/after identity, not just the last durable `applied` list.
    pending = []
    for name, state in record['files'].items():
        target = game / _root_name(name)
        actual = fingerprint(target)
        if actual == state['before']:
            continue  # Never published, or already restored before a receipt write.
        retained = fingerprint(withdrawn/name)
        if not (actual == state['after'] or (actual is None and retained == state['after'])):
            raise ValueError('An SKSE root file changed after installation; preserve that change before restoring: ' + name)
        if retained is not None and retained != state['after']:
            raise ValueError('A retained root file changed; restoration was stopped: ' + name)
        if state['before'] is not None and fingerprint(job/'previous-root'/name) != state['before']:
            raise ValueError('The retained SKSE backup changed: ' + name)
        candidate = game/(name + '.modlab-restore')
        if fingerprint(candidate) not in (None, state['before']):
            raise ValueError('A pending root restoration changed: ' + name)
        pending.append((name, actual))
    withdrawn.mkdir(exist_ok=True)
    for name, expected in reversed(pending):
        target, destination = game/name, withdrawn/name
        state = record['files'][name]
        if fingerprint(target) != expected or digest(game/'SkyrimSE.exe') != record['game_hash']:
            raise ValueError('A root file or game changed during restoration: ' + name)
        if expected is not None:
            if fingerprint(destination) == state['after']:
                target.unlink()  # Exact published bytes are already retained.
            elif destination.exists():
                raise ValueError('A retained root file changed during restoration: ' + name)
            else:
                shutil.move(target, destination)
        if state['before'] is not None:
            candidate = game/(name + '.modlab-restore')
            if fingerprint(candidate) is None:
                shutil.copy2(job/'previous-root'/name, candidate)
            if fingerprint(candidate) != state['before'] or target.exists() or target.is_symlink():
                raise ValueError('A root restoration destination or backup changed: ' + name)
            os.rename(candidate, target)
        if fingerprint(target) != state['before']:
            raise ValueError('The previous root file was not restored: ' + name)
        record['applied'] = [item for item in record['applied'] if item != name]
        write_record(job/'root-deployment.json', record)
    # Validate the final destination even when the journal said nothing applied.
    if any(fingerprint(game/name) != state['before'] for name, state in record['files'].items()):
        raise ValueError('Root restoration did not reach its recorded previous state.')
    record['applied'] = []
    write_record(job/'root-deployment.json', record)
