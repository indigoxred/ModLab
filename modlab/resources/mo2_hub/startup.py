"""Associate fresh SKSE initialization evidence with a ModLab launch request.

SKSE's documented source writes a FILETIME in the runtime header and a result
for each loaded plugin. These are startup observations, never gameplay proof.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import time

from .assessment import Finding
from .native import inspect_binary, packed_version
from .outputs import digest


HEADER = re.compile(r'^SKSE64 runtime: initialize \(version = ([\d.]+) ([0-9A-F]{8}) ([0-9A-F]{16}),', re.M|re.I)
PLUGIN = re.compile(r'^plugin (.+\.dll) \([0-9A-F]{8} .* [0-9A-F]{8}\) (.+?) \(handle \d+\)\s*$', re.M|re.I)
MAX_LOG_BYTES = 4 * 1024 * 1024


def read_log(path):
    with Path(path).open('rb') as stream:
        content = stream.read(MAX_LOG_BYTES + 1)
    if len(content) > MAX_LOG_BYTES:
        raise ValueError('The SKSE log exceeds the startup inspection limit; open its full log for review.')
    return content


def windows_path(path):
    return PureWindowsPath(path).as_posix().rstrip('/').casefold()


def integration_findings(text):
    """Version-bounded advice from a retained initialization log, not gameplay proof."""
    # v4.1.1's bundled SDK stores GetModuleHandle at DLL initialization, then
    # reuses it during PostPostLoad registration. A later framework load leaves
    # those menu functions unavailable. Observed live with the official AVX DLL.
    # https://github.com/DaymareOn/hdtSMP64/blob/v4.1.1/extern/SKSEMenuFramework/include/SKSEMenuFramework.h
    checks = re.findall(r'^checking plugin (.+\.dll)\s*$', text, re.M | re.I)
    checks = [name.casefold() for name in checks]
    if (not re.search(r'^init complete\s*$', text, re.M) or
            not all(name in checks for name in ('hdtsmp64.dll', 'sksemenuframework.dll')) or
            checks.index('hdtsmp64.dll') > checks.index('sksemenuframework.dll')):
        return ()
    if not re.search(r'^plugin hdtsmp64\.dll \(00000001 hdtsmp64 04010010\) loaded correctly ', text, re.M | re.I):
        return ()
    if not re.search(r'^plugin SKSEMenuFramework\.dll \([^\n]+\) loaded correctly ', text, re.M | re.I):
        return ()
    return (Finding('Review', 'fsmp-menu-registration', 'FSMP 4.1.1 settings menu may be missing',
        'This launch loaded FSMP 4.1.1 before SKSE Menu Framework. The release caches the menu-library handle early, '
        'which can leave its settings pages unregistered even though both DLLs load.',
        'Open the in-game Menu Framework panel and check for FSMP. If absent, use FSMP’s configs.json or smp console '
        'commands for now, or install an author-provided release that fixes menu registration. '
        'Mod instructions and the mod folder provide access to its configuration guidance. '
        'Sorting plugins and cleaning will not repair DLL menu registration.',
        'This is a settings-menu concern, not evidence that physics failed. The diagnosis follows the observed DLL '
        'sequence and the v4.1.1 source; patched builds using the same version may differ. Physics still needs a '
        'targeted content check. Source: https://github.com/DaymareOn/hdtSMP64/blob/v4.1.1/extern/SKSEMenuFramework/include/SKSEMenuFramework.h'),)


def parse_log(text):
    header = HEADER.search(text)
    directory = re.search(r'^plugin directory = (.+)$', text, re.M)
    if not header or not directory:
        raise ValueError('A complete SKSE startup header has not appeared.')
    statuses = {}
    for match in PLUGIN.finditer(text):
        name = PureWindowsPath(match[1]).name.casefold()
        statuses.setdefault(name, []).append(match[2].strip())
    return dict(skse=header[1], runtime=int(header[2],16),
        started_at=int(header[3],16)/10000000 - 11644473600,
        plugin_directory=directory[1].strip(), statuses=statuses,
        initialized=bool(re.search(r'^init complete\s*$',text,re.M)))


def evaluate_log(text, request, observed_at):
    parsed = parse_log(text)
    requested = datetime.fromisoformat(request['started_at']).timestamp()
    if not requested <= parsed['started_at'] <= min(requested + 180, observed_at + 1):
        raise ValueError('The SKSE log is outside this launch request’s checked startup interval.')
    if parsed['started_at'] == request.get('baseline_start'):
        raise ValueError('The SKSE log still belongs to the previous startup.')
    if parsed['runtime'] != packed_version(request['runtime']):
        raise ValueError('The SKSE log describes a different Skyrim runtime.')
    expected = str(PureWindowsPath(request['game_root'])/'Data'/'SKSE'/'Plugins')
    if windows_path(parsed['plugin_directory']) != windows_path(expected):
        raise ValueError('The SKSE log describes another game installation.')
    result = dict(status='Waiting for SKSE initialization', loaded=[], failed=[],
        initialized=parsed['initialized'], skse_version=parsed['skse'], log_started_at=parsed['started_at'],
        observed_at=datetime.fromtimestamp(observed_at,timezone.utc).isoformat(), gameplay_verified=False)
    if not parsed['initialized']:
        return result
    for component in request['native']:
        if not component['is_plugin']:
            continue
        name = PureWindowsPath(component['path']).name
        statuses = parsed['statuses'].get(name.casefold(), [])
        errors = [value for value in statuses if value != 'loaded correctly']
        if not errors and 'loaded correctly' in statuses:
            result['loaded'].append(name)
        else:
            result['failed'].append(dict(name=name, mod=component['mod'],
                reason='; '.join(errors) if errors else 'SKSE recorded no successful load for this installed plugin.'))
    result['status'] = 'SKSE plugins need attention' if result['failed'] else 'SKSE initialization checked; gameplay unverified'
    return result


def fingerprint(organizer, setup):
    from .loot_workflow import context_signature
    native = []
    for asset in sorted(setup.assets,key=lambda item:item.path.casefold()):
        path = asset.path.replace('\\','/')
        if not re.fullmatch(r'SKSE/Plugins/[^/]+\.dll',path,re.I):
            continue
        physical = Path(organizer.resolvePath(asset.path))
        info = inspect_binary(physical)
        native.append(dict(path=path, physical=str(physical), sha256=digest(physical),
                           mod=asset.origins[0] if asset.origins else 'Unknown provider', is_plugin=info.is_plugin))
    root = Path(setup.game_root)
    files = [root/'skse64_loader.exe', root/('skse64_'+'_'.join(setup.runtime.split('.')[:3])+'.dll')]
    return dict(profile_path=organizer.profilePath(), runtime=setup.runtime, game_root=setup.game_root,
        plugin_context=json.loads(json.dumps(context_signature(organizer))), native=native,
        loader_hashes={p.name:digest(p) for p in files})


def prepare_request(organizer, setup):
    result = fingerprint(organizer, setup)
    log = Path(organizer.managedGame().documentsDirectory().absolutePath())/'SKSE/skse64.log'
    result['log_path'] = str(log)
    try:
        baseline = parse_log(read_log(log).decode('cp1252',errors='replace'))['started_at']
    except (OSError, ValueError):
        baseline = None
    result.update(baseline_start=baseline, started_at=datetime.now(timezone.utc).isoformat())
    return result


def request_matches(organizer, setup, request):
    current = fingerprint(organizer, setup)
    return all(current[key] == request.get(key) for key in current)


def update_startup(organizer, record_path, setup, *, finished=False):
    """One bounded read, called by the user window's existing event loop."""
    record_path = Path(record_path)
    record = json.loads(record_path.read_text(encoding='utf-8'))
    request = record.get('startup_request')
    if not request or record.get('startup_check',{}).get('initialized'):
        return True
    now = time.time()
    elapsed = now - datetime.fromisoformat(request['started_at']).timestamp()
    terminal = finished or elapsed > 180
    try:
        content = read_log(request['log_path'])
        result = evaluate_log(content.decode('cp1252',errors='replace'), request, now)
        if result['initialized']:
            if callable(setup):
                setup = setup()
            if not request_matches(organizer, setup, request):
                raise ValueError('The selected profile or native inputs changed after this launch request.')
            retained = record_path.parent/'skse64-observed.log'
            retained.write_bytes(content)
            result.update(log_file=str(retained), log_sha256=hashlib.sha256(content).hexdigest())
            record['startup_check'] = result
            record['status'] = result['status']
            terminal = True
        elif terminal:
            raise ValueError('SKSE did not reach a checked initialization. Inspect the loader/runtime logs before assuming the mods loaded.')
    except (OSError,ValueError) as error:
        if terminal:
            record['startup_check'] = dict(status='Startup could not be verified', initialized=False,
                error=str(error), gameplay_verified=False)
    if terminal:
        record_path.write_text(json.dumps(record,indent=2),encoding='utf-8')
    return terminal


def inspect_startup(organizer, setup):
    directory = Path(organizer.modsPath()).parent/'reports/launch'
    for path in sorted(directory.glob('*/operation.json'),key=lambda p:p.stat().st_mtime_ns,reverse=True):
        record=json.loads(path.read_text(encoding='utf-8'))
        if record.get('profile_path') != organizer.profilePath():
            continue
        request, result = record.get('startup_request'), record.get('startup_check')
        if not request or not result:
            return (), False
        if not request_matches(organizer,setup,request):
            return (Finding('Info','startup-outdated','Previous startup check is out of date',
                'This profile or its native/plugin inputs changed after the recorded launch.',
                'Launch through ModLab to collect a fresh startup result.'),), False
        if not result.get('initialized'):
            return (Finding('Unknown','startup-unverified','The last launch was not verified',result.get('error',''),
                'Review the SKSE loader/runtime logs in History. Resolve the startup problem and launch through ModLab again.'),), False
        log = Path(result['log_file'])
        log.resolve().relative_to(path.parent.resolve())
        if digest(log) != result['log_sha256']:
            raise ValueError('The retained startup log changed; its previous result is no longer accepted.')
        findings = list(integration_findings(read_log(log).decode('cp1252', errors='replace')))
        for failure in result['failed']:
            action = ('A required DLL could not be found. Install the mod author’s matching prerequisites; '
                      'the named plugin itself may still be present.' if re.search(r'\b126\b',failure['reason']) else
                      'Check the supplying mod’s release for this Skyrim runtime, update its dependencies or disable it and dependent mods.')
            findings.append(Finding('Review','native-load-failed','Plugin did not load: '+failure['mod'],
                failure['name']+'\n'+failure['reason']+'\nLaunch record: '+str(path),
                action+' Then launch again through ModLab. Cleaning and sorting cannot fix a DLL load failure.'))
        if result['loaded'] or not request['native']:
            findings.append(Finding('Info','native-load-checked',f"SKSE loaded {len(result['loaded'])} native plugins in the checked launch",
                '\n'.join(result['loaded'])+'\nObserved: '+result['observed_at']+'\nLaunch record: '+str(path),
                'Startup succeeded for these plugins. Their actual features and gameplay still require targeted in-game checks.'))
        return tuple(findings), True
    return (), False
