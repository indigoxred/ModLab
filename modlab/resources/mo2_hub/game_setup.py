"""Small, read-only baseline checks and backups around an external patcher."""
import hashlib
import json
from pathlib import Path
import shutil

from .outputs import digest

# Steam executable identities from Reliquary's public version catalogue,
# checked 2026-09-07. A Windows version label alone is insufficient.
SOURCE_SHA1 = '2f784a183f884067a9a41338664b55f6dc198a48'
TARGET_SHA1 = '1c9636207b4613254b03d76c410540e10b539dc9'
# Hashes shipped in wSkeever's 1.7.104.3 source archive, Nexus file 797419.
SOURCE_FILES = {
    'SkyrimSE.exe': '846efccf0c1374d71f892907f46549560f2fcb0a75cb87a3eed438baa0f1402f',
    'SkyrimSELauncher.exe': '9204f00ba4e0093f00fd461883f171f1b98f7d7de27969ff5e41ba3608ae271b',
    'Data/Skyrim - Shaders.bsa': 'fd1ba630f443353ed21ed4ca0380968f9c7091c8434518ea3cc79bc6d38c18e7',
}
TARGET_FILES = {
    'SkyrimSE.exe': 'c434208894f07f604b852f29b8edc3a58c4de63de783373733e72b2b73f33be9',
    'SkyrimSELauncher.exe': 'ce2a1b3f9727c8b53ffb8e0bccd2bbed2bd8bec47b0ab8552c2850caa05e68b1',
    'Data/Skyrim - Shaders.bsa': '4685203a8f115937e34fab3879c6b599734d8a913a698891866ed72385b5cfa5',
}
PATCHER_URL = 'https://www.nexusmods.com/skyrimspecialedition/mods/169962?tab=files'
SKSE_URL = 'https://www.nexusmods.com/skyrimspecialedition/mods/30379?file_id=792256&tab=files'
USSEP_URL = 'https://www.nexusmods.com/skyrimspecialedition/mods/266?file_id=733846&tab=files'
PATCHER_NAME = 'Skyrim_1_7_104_to_1_6_1170_patcher.exe'
PATCHER_SHA256 = '70dc32296b0a57ca29851c40958461440cc4e09aae851df57b0111ed9dd21b7f'


def sha1(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha1').hexdigest()


def check_source(runtime, steam, identity):
    if runtime.removesuffix('.0') != '1.7.104' or not steam or identity != SOURCE_SHA1:
        raise ValueError('This patcher route requires the original Steam Skyrim 1.7.104 executable. '
                         'The selected game differs; do not run this patcher on it.')


def target_matches(runtime, steam, identity):
    return runtime.removesuffix('.0') == '1.6.1170' and steam and identity == TARGET_SHA1


def mismatched_files(game, expected):
    return [name for name, checksum in expected.items()
            if not (Path(game) / name).is_file() or digest(Path(game) / name) != checksum]


def baseline_path(game, storage):
    key = hashlib.sha256(str(Path(game).resolve()).casefold().encode('utf-8')).hexdigest()[:24]
    return Path(storage) / 'game-baselines' / (key + '.json')


def remember_baseline(game, storage):
    """Remember only the supported, verified target; never adopt changed files."""
    game = Path(game).resolve()
    if not (game / 'steam_api64.dll').is_file() or mismatched_files(game, TARGET_FILES):
        raise ValueError('The Steam 1.6.1170 game files must match before recording this baseline.')
    path = baseline_path(game, storage)
    record = {'route': 'steam-1170-bobw', 'game': str(game), 'runtime': '1.6.1170.0'}
    if path.exists():
        if json.loads(path.read_text(encoding='utf-8')) != record:
            raise ValueError('The saved game baseline differs. Preserve it and resolve the earlier setup record first.')
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(record, stream, indent=2)
    return path


def inspect_saved_baseline(game, storage):
    """Run during normal recheck/launch, for game folders explicitly set up here."""
    from .assessment import Finding
    game = Path(game).resolve()
    path = baseline_path(game, storage)
    if not path.exists():
        return ()
    action = ('Keep the existing backups. Open Setup / tool locations → Skyrim 1.6.1170 setup / downgrade '
              'and restore the matching baseline before launching. Steam updates or Verify integrity can replace '
              'downgraded files. Do not rerun a patcher unless its source-file check passes.')
    try:
        record = json.loads(path.read_text(encoding='utf-8'))
        if record != {'route': 'steam-1170-bobw', 'game': str(game), 'runtime': '1.6.1170.0'}:
            raise ValueError('Unrecognized or mismatched saved game baseline.')
        mismatches = mismatched_files(game, TARGET_FILES)
        if not (game / 'steam_api64.dll').is_file():
            mismatches.append('steam_api64.dll (missing)')
        if mismatches:
            return (Finding('Blocked', 'game-baseline-changed', 'The recorded Skyrim 1.6.1170 setup has changed',
                'Game: ' + str(game) + '\nChanged or missing files: ' + ', '.join(mismatches) + '\nRecord: ' + str(path),
                action, 'The executable, launcher or supporting files no longer match the recorded downgrade. '
                'Installing or sorting mods cannot restore that game baseline.\n\nAffected files: ' + ', '.join(mismatches)),)
    except (OSError, ValueError, TypeError) as error:
        return (Finding('Blocked', 'game-baseline-unreadable', 'The saved Skyrim baseline could not be checked',
            str(error) + '\nRecord: ' + str(path), action),)
    return (Finding('Info', 'game-baseline-checked', 'Skyrim 1.6.1170 downgrade files checked',
        'Game: ' + str(game) + '\nRecord: ' + str(path),
        'Use SKSE and DLL releases for 1.6.1170. Address Library must include its database; BEES is unnecessary. '
        'Content mods still need their actual official files and mod dependencies.',
        'The game executable, launcher and shaders match the supported Best of Both Worlds target. '
        'This route retains the other official content rather than reinstalling old game data. '
        'This check does not certify that retained content or the active mods work in game.'),)


def backup_files(game, catalog, destination):
    """Retain the patcher's affected files and root helper files without modifying them."""
    game, catalog, destination = Path(game).resolve(), Path(catalog), Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    names = {'SkyrimSE.exe', 'SkyrimSELauncher.exe', 'Data/Skyrim - Shaders.bsa'}
    names.update(p.name for p in game.glob('*.dll'))
    names.update(p.name for p in game.glob('skse*.exe'))
    record = {'game': str(game), 'catalog': str(catalog), 'game_files': {}, 'catalog_sha256': None}
    for name in sorted(names):
        source = game / name
        if source.is_symlink():
            raise ValueError('Cannot back up a linked game file: ' + str(source))
        if not source.exists():
            continue
        checksum = digest(source)
        target = destination / 'game' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if digest(target) != checksum or digest(source) != checksum:
            raise ValueError('Game file changed while preparing its backup: ' + name)
        record['game_files'][name] = checksum
    if not {'SkyrimSE.exe', 'Data/Skyrim - Shaders.bsa'} <= record['game_files'].keys():
        raise ValueError('Game executable or shaders are missing; do not patch this installation.')
    if catalog.exists():
        if catalog.is_symlink() or not catalog.is_file():
            raise ValueError('Content catalog is not a regular file.')
        checksum = digest(catalog)
        shutil.copy2(catalog, destination / 'ContentCatalog.txt')
        if digest(destination / 'ContentCatalog.txt') != checksum or digest(catalog) != checksum:
            raise ValueError('Content catalog changed during backup.')
        record['catalog_sha256'] = checksum
    (destination / 'backup.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    return record


def inspect_baseline(game, catalog, version_reader):
    game, catalog = Path(game), Path(catalog)
    executable = game / 'SkyrimSE.exe'
    if not executable.is_file():
        return ['BLOCKED: SkyrimSE.exe is missing from the selected game folder.']
    runtime = version_reader(str(executable)) or ''
    steam = (game / 'steam_api64.dll').is_file()
    identity = sha1(executable)
    results = [f'Game: {game}', f'Runtime: {runtime or "unknown"}']
    if not target_matches(runtime, steam, identity):
        results.append('BLOCKED: The original Steam 1.6.1170 executable has not been verified. '
                       'Follow the patcher instructions for the detected source version.')
        return results
    results.append('CHECKED: Steam 1.6.1170 executable identity matches.')
    loader = game / 'skse64_loader.exe'
    loader_version = (version_reader(str(loader)) or '') if loader.is_file() else ''
    if loader_version.removesuffix('.0') != '2.2.8' or not (game / 'skse64_1_6_1170.dll').is_file():
        results.append('ACTION: Install the matching Steam SKSE 2.2.8 archive through ModLab. '
                       'The existing loader/runtime pair is missing or different.')
    else:
        results.append('CHECKED: SKSE 2.2.8 loader and 1.6.1170 runtime DLL are present; launch verification still required.')
    mismatches = mismatched_files(game, TARGET_FILES)
    if mismatches:
        results.append('BLOCKED: These game files do not match the patcher’s 1.6.1170 baseline: '
                       + ', '.join(mismatches) + '. Restore the matched files before launching.')
    else:
        results.append('CHECKED: Game executable, launcher and shaders match the upstream 1.6.1170 hashes.')
    masters = ('ccBGSSSE001-Fish.esm', 'ccQDRSSE001-SurvivalMode.esl', 'ccBGSSSE037-Curios.esl',
               'ccBGSSSE025-AdvDSGS.esm', '_ResourcePack.esl')
    missing = [name for name in masters if not (game / 'Data' / name).is_file()]
    if missing:
        results.append('ACTION: Required foundation content is missing: ' + ', '.join(missing) +
                       '. Obtain the official content required by the selected USSEP release; do not remove its master requirements.')
    if catalog.is_file() and catalog.stat().st_size:
        results.append('REVIEW: ContentCatalog.txt is nonempty. The downgrade tool must reset stale 1.7 metadata; '
                       'a newly regenerated catalog is not itself an error. Preserve a copy before any reset.')
    results.append('NEXT: Install the matched USSEP archive; run Recheck and finish setup to inspect active '
                   'plugins, missing masters and native DLLs. Use a fresh test save. This baseline check '
                   'does not certify mod compatibility or gameplay.')
    return results
