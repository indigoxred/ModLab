"""Read concrete runtime requirements; absence of a rule is not compatibility.

Sources checked 2026-09-06: SKSE author; SkyUI and RaceMenu requirements;
Address Library author and CommonLibSSE-NG REL/ID.cpp; Microsoft PE format.
These checks neither load DLLs nor modify installed files.
"""
from pathlib import Path
import re

from .assessment import Finding
from .native import inspect_binary, compatibility_problem, display_version, SOURCE as NATIVE_SOURCE
from .vfs import readable_path

SKSE_URL = 'https://skse.silverlock.org/'
ADDRESS_URL = 'https://www.nexusmods.com/skyrimspecialedition/mods/32444'
SKSE_DEPENDENTS = {
    'skyui_se.esp': 'https://www.nexusmods.com/skyrimspecialedition/mods/12604',
    'racemenu.esp': 'https://www.nexusmods.com/skyrimspecialedition/mods/19080',
}
DATABASE = re.compile(r'version(?:lib)?-(\d+-\d+-\d+-\d+)\.bin', re.I)


def skse_files_error(setup, resolve_path):
    version = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)(?:\.\d+)?', setup.runtime)
    if version is None:
        return 'The Skyrim runtime could not be read; ModLab cannot check the installed SKSE files.'
    runtime_dll = 'skse64_' + '_'.join(version.groups()) + '.dll'
    if not (Path(setup.game_root) / runtime_dll).is_file():
        return f'SKSE is incomplete or targets another Skyrim version: missing {runtime_dll}.'
    script = resolve_path('scripts/skse.pex')
    if not script or not Path(script).is_file():
        return 'SKSE scripts are missing from the active profile. Root loader files alone are insufficient.'
    # Official package members, not provider names: profiles can inherit scripts
    # from a different runtime without having their own SKSE deployment manifest.
    from .outputs import digest
    mismatches = {
        ('1.6.1170', '96a817c867a2dbbf0d96536e29145853972c8e6472a115038563b9b165c9845b'): '2.3.1',
        ('1.7.104', '681b90c953340fcbf7d0f6d4243e16bf51ae60b973b980dad141ea498c10a53c'): '2.2.8',
    }
    wrong_version = mismatches.get(('.'.join(version.groups()), digest(Path(script))))
    if wrong_version:
        return (f'The active SKSE script is from SKSE {wrong_version}, which does not match Skyrim {setup.runtime}. '
                f'Winning script: {script}. Reinstall the matching SKSE package through ModLab; '
                'changing plugin order or cleaning cannot repair this script mismatch.')
    return ''


def foundation_asset(path):
    """Include non-overlapping address databases in the existing VFS pass."""
    path = path.replace('\\', '/').casefold()
    from .shape_support import EVIDENCE_FILES
    return path in EVIDENCE_FILES or ('/skse/plugins/' in '/' + path and bool(DATABASE.fullmatch(path.rsplit('/', 1)[-1])))


def inspect_foundations(setup, resolve_path, *, source_page=None, skse_version=None):
    if setup.game_name != 'Skyrim Special Edition':
        return ()
    from .shape_support import inspect_support
    findings = list(inspect_support(setup, resolve_path))
    if setup.skse_loader_present:
        problem = skse_files_error(setup, resolve_path)
        if problem:
            findings.append(Finding('Blocked' if setup.runtime else 'Unknown', 'skse-files-incomplete',
                'SKSE installation needs attention', problem,
                'Use Install archives to install the official SKSE package for this game runtime and store. '
                'Enable its matching scripts mod, then recheck. Download: ' + SKSE_URL))
    runtime = setup.runtime.split('.')
    if len(runtime) == 3:
        runtime.append('0')
    required = '-'.join(runtime)
    # Resolve a real effective file, independently of asset traversal order.
    address_path = resolve_path('SKSE/Plugins/versionlib-' + required + '.bin')
    address_present = bool(address_path and Path(address_path).is_file())
    databases = []
    for asset in setup.assets:
        path = asset.path.replace('\\', '/').casefold()
        if not path.startswith('skse/plugins/'):
            continue
        if '/' in path.removeprefix('skse/plugins/'):
            continue  # SKSE's directly loaded DLLs; nested tool dependencies are not all native plugins.
        match = DATABASE.fullmatch(path.rsplit('/', 1)[-1])
        if match:
            databases.append((match[1], asset))
        if not path.endswith('.dll'):
            continue
        provider = ', '.join(asset.origins[:1]) or 'provider not reported'
        detail = f'{asset.path}\nWinning mod: {provider}'
        page = source_page(asset.origins[0]) if source_page and asset.origins else ''
        download = (' Supplying mod’s files page (from MO2 metadata): ' + page +
                    ' . Choose a release explicitly supporting your runtime; this link is not a verified replacement.'
                    if page else ' Open Mod instructions for the supplying mod. Its download page is not recorded; '
                    'ModLab will not guess a replacement from its folder name.')
        if asset.archive:
            findings.append(Finding('Blocked', 'native-component-packed',
                'SKSE cannot load a DLL from an archive: ' + provider,
                detail + '\nArchive: ' + asset.archive,
                'Reinstall the correct package through ModLab. Native DLLs and their dependencies must be loose files '
                'in the locations specified by the author; a BSA cannot make them available to the Windows loader.'))
            continue
        try:
            physical = resolve_path(asset.path)
            if not physical:
                raise ValueError('MO2 did not resolve the effective DLL.')
            info = inspect_binary(physical)
            machine = info.machine
            if info.skse_required:
                detail += '\nRequired SKSE: ' + display_version(info.skse_required)
                detail += '\nInstalled SKSE loader: ' + (display_version(skse_version) if skse_version is not None else 'unreadable or missing')
            checksum = ''
            if ((path == 'skse/plugins/ostim.dll' and setup.runtime.startswith('1.7.'))
                    or (path == 'skse/plugins/enginefixes.dll' and required == '1-6-1170-0')):
                from .outputs import digest
                checksum = digest(Path(physical))
        except (OSError, ValueError) as error:
            findings.append(Finding('Unknown', 'native-inspection-incomplete',
                'Could not inspect native component: ' + provider, detail + '\n' + str(error),
                'Reinstall the supplying mod through ModLab if its files are missing or damaged, then recheck. '
                'This inspection failure does not establish compatibility.'))
            continue
        # Verified 7.0.20 / 1.6.1170 FOMOD variant. Newer 1.7 builds use a
        # different preload mechanism; do not infer their requirements here.
        if (path == 'skse/plugins/enginefixes.dll' and required == '1-6-1170-0'
                and checksum == '5d1384acfb523abd1333f5af71af0b7d131b6ebb1a0ee6b3edff86fb4c93adf3'
                and not (Path(setup.game_root) / 'd3dx9_42.dll').is_file()):
            findings.append(Finding('Blocked', 'engine-fixes-preloader-missing',
                'Engine Fixes is missing its game-folder preloader', detail,
                'Download Engine Fixes - SKSE64 Preloader from '
                'https://www.nexusmods.com/skyrimspecialedition/mods/17230?tab=files&file_id=725261 . '
                'Extract d3dx9_42.dll alongside SkyrimSE.exe in ' + str(setup.game_root) +
                ', not inside Data. Then recheck and verify startup.',
                'This installed 7.0.20 variant requires the separate preloader. Its startup error '
                'confirms Skyrim exits without it. File presence resolves this missing-file check; '
                'successful preloading must still be verified when the game starts.'))
        # This exact installed binary was reported incompatible by SKSE 2.2.8
        # on 1.6.1170. Its independence flags alone did not predict load success.
        if (path == 'skse/plugins/enginefixes.dll' and required == '1-6-1170-0'
                and checksum == '26af56098f739821558ac07e1b5730a223bcd8dd7af87ef1edb0288c22cae179'):
            findings.append(Finding('Blocked', 'native-functional-incompatible',
                'Installed Engine Fixes build failed to load on Skyrim 1.6.1170',
                detail + '\nExact DLL SHA-256: ' + checksum,
                'Install a complete Engine Fixes package supporting 1.6.1170 through ModLab, including its '
                'required preloader setup. Disable this incompatible provider, then recheck. Author files: '
                'https://www.nexusmods.com/skyrimspecialedition/mods/17230?tab=files . '
                'Do not replace only the DLL while retaining mismatched supporting files.',
                'SKSE reported this exact installed binary as incompatible during load on 1.6.1170. '
                'Its compatibility declaration passed the static check, which does not prove it can run. '
                'This finding is limited to that binary and runtime; it does not reject other Engine Fixes builds.'))
            continue
        # Exact downloaded 7.5.1 DLL; author's files/changelog checked 2026-09-07.
        # Its newer declaration does not resolve the documented 1.7 VM problem.
        # Do not carry this exception onto a future release or onto 1.6 runtimes.
        if path == 'skse/plugins/ostim.dll' and setup.runtime.startswith('1.7.'):
            if checksum == '62d531c5dd138fcf9bb909487e7a4b61bdfecd0b584b5a8d570bc0d8cce1f078':
                findings.append(Finding('Blocked', 'native-functional-incompatible',
                    'OStim Standalone 7.5.1 does not support Skyrim 1.7.x',
                    detail + '\nExact DLL SHA-256: 62d531c5dd138fcf9bb909487e7a4b61bdfecd0b584b5a8d570bc0d8cce1f078',
                    'Use a supported runtime with matched dependencies, or a release whose author confirms 1.7 support. '
                    'Author files and changelog: https://www.nexusmods.com/skyrimspecialedition/mods/98163?tab=files . '
                    'If keeping 1.7, disable this release and its dependent functionality until a supported replacement exists.',
                    'The author reports that Skyrim 1.7 changed its virtual machine and this release does not work. '
                    'Its DLL declaration can pass while the framework still fails. Sorting, cleaning or installing '
                    'Address Library cannot fix that documented functional limitation. This finding applies only '
                    'to the identified 7.5.1 DLL on 1.7.x, not every release of this framework.'))
        # FSMP 4's menu requires the framework; the physics engine does not.
        # Author's v4 changelog explicitly preserves JSON/console configuration:
        # https://github.com/DaymareOn/hdtSMP64/wiki/10-%E2%80%90-Changelog
        # Match the effective DLL declaration, not a mod-folder label.
        if (path == 'skse/plugins/hdtsmp64.dll' and info.name.casefold() == 'hdtsmp64'
                and info.plugin_version >> 24 == 4):
            framework = resolve_path('SKSE/Plugins/SKSEMenuFramework.dll')
            if not framework or not readable_path(framework).is_file():
                findings.append(Finding('Info', 'fsmp-optional-menu',
                    'Optional FSMP settings menu: SKSE Menu Framework is not installed',
                    detail + '\nMenu component: SKSE/Plugins/SKSEMenuFramework.dll',
                    'For an in-game settings menu, install SKSE Menu Framework for your runtime through ModLab: '
                    'https://www.nexusmods.com/skyrimspecialedition/mods/120352 . '
                    'Otherwise FSMP supports configs.json and its smp console commands; no cleaning or sorting is needed.',
                    'The framework is optional for physics. Its absence removes the settings menu, not the physics engine. '
                    'Physics content still needs its own matching meshes, skeletons and configurations, and an in-game feature check. '
                    'Source: https://github.com/DaymareOn/hdtSMP64/wiki/10-%E2%80%90-Changelog\n\nAffected mod: ' + provider))
        if machine != 0x8664:
            architecture = {0x14c: '32-bit x86', 0xaa64: 'ARM64'}.get(machine, f'machine 0x{machine:04x}')
            findings.append(Finding('Blocked', 'native-wrong-architecture',
                'Wrong game binary: ' + provider, detail + '\nDetected: ' + architecture,
                'Install the Skyrim SE/AE x64 release of this component, or disable its supplying mod and dependent mods. '
                'Sorting, cleaning and merging cannot convert this binary.' + download,
                'Skyrim SE/AE cannot load this DLL architecture into its x64 process. '
                'An x64 file still needs a release supporting your exact Skyrim runtime.'))
            continue
        try:
            # Check the runtime independently so installing a missing database
            # cannot conceal an incompatible structure layout or declaration.
            problem = compatibility_problem(info, setup.runtime, True, skse_version)
        except ValueError as error:
            problem = ('Unknown', str(error))
        if info.is_plugin and info.data_version == 1 and info.flags & 1 and not address_present:
            findings.append(Finding('Blocked', 'native-dependency-missing',
                'Address Library is missing for: ' + provider,
                detail + '\nRequired effective file: SKSE/Plugins/versionlib-' + required + '.bin',
                'Install or enable the Address Library all-in-one package containing Skyrim ' + setup.runtime +
                ' through ModLab, then use Recheck and finish setup. Download: ' + ADDRESS_URL,
                'This DLL declares an Address Library dependency. Its database for your running game is missing '
                'from the active profile. This finding does not say the DLL needs replacing. '
                'Any separate runtime incompatibility must also be resolved. A newer all-in-one package can '
                'contain older game databases; its title is not a reason to reject it.'))
        if problem:
            level, reason = problem
            # Author files pages checked 2026-09-07. These are named candidates,
            # not an automatic replacement of a framework's bundled scripts.
            if setup.runtime in {'1.6.1170', '1.6.1170.0'} and info.name == 'PapyrusUtil' and path == 'skse/plugins/papyrusutil.dll':
                download += (' For 1.6.1170, the author lists PapyrusUtil 4.6 (Steam): '
                             'https://www.nexusmods.com/skyrimspecialedition/mods/13048?tab=files . '
                             'If this DLL is bundled in a framework, obtain that framework’s matching package; '
                             'do not swap only its DLL and leave incompatible scripts. Verify the dependent mods’ '
                             'minimum PapyrusUtil requirements before choosing an older release.')
            findings.append(Finding(level, 'native-runtime-incompatible' if level == 'Blocked' else 'native-runtime-unknown',
                'Native component needs attention: ' + provider,
                detail + '\nSkyrim: ' + setup.runtime + '\n' + reason + '\nSKSE declaration rules: ' + NATIVE_SOURCE,
                ('Install the supplying mod\'s release for this exact Skyrim runtime and its dependencies, '
                 'or disable it and dependent mods, then recheck. Sorting, cleaning and merging cannot repair '
                 'a native runtime mismatch.' + download),
                reason + '\nInstalled game: Skyrim ' + setup.runtime + '.\n\n'
                'This is the requirement declared by the installed native component. '
                'A compatible replacement must still load successfully and pass its feature check.'))
    if databases and len(runtime) == 4 and all(part.isdigit() for part in runtime):
        required = '-'.join(runtime)
        if required not in {version for version, asset in databases}:
            findings.append(Finding('Review', 'address-library-runtime',
                'Installed Address Library has no database for this runtime',
                f'Skyrim runtime: {setup.runtime}\nInstalled databases:\n' + '\n'.join(
                    asset.path + ' — ' + (asset.origins[0] if asset.origins else 'unknown provider')
                    for version, asset in databases),
                f'Install the Address Library package containing the database for {required}, then recheck. '
                'Download: ' + ADDRESS_URL,
                'These databases target other game versions. Mods that use Address Library need the matching database. '
                'Some native plugins do not use it, so this is not a blanket incompatibility finding. '
                'A matching filename alone does not verify the database contents or that a DLL supports this runtime.'))
    return tuple(findings)
