"""Optional OBody dependency checks, not a universal body-mod requirement.

Known OBody NG 4.4.3 support files and author setup guide articles/4580.
Presence resolves missing-file findings only; native checks, body preparation
and effective generated-output checks remain separate.
"""
from dataclasses import dataclass
from pathlib import Path
from .assessment import Finding
from .vfs import readable_path


GUIDE = 'https://www.nexusmods.com/skyrimspecialedition/articles/4580'
NEXUS = 'https://www.nexusmods.com/skyrimspecialedition/mods/'


@dataclass(frozen=True)
class Requirement:
    name: str
    url: str
    files: tuple
    plugin: str = ''


REQUIREMENTS = (
    Requirement('OBody NG', NEXUS+'77016?tab=files', (
        'SKSE/Plugins/OBody.dll', 'SKSE/Plugins/OBody_presetDistributionConfig.json',
        'scripts/OBodyNative.pex', 'scripts/OBodyNGScript.pex', 'scripts/OBodyPlayerAliasScript.pex'), 'OBody.esp'),
    Requirement('RaceMenu', NEXUS+'19080?tab=files', (
        'SKSE/Plugins/skee64.dll', 'SKSE/Plugins/skee64.ini', 'scripts/NiOverride.pex')),
    Requirement('PapyrusUtil', NEXUS+'13048?tab=files', (
        'SKSE/Plugins/PapyrusUtil.dll', 'scripts/PapyrusUtil.pex', 'scripts/StorageUtil.pex',
        'scripts/JsonUtil.pex', 'scripts/MiscUtil.pex')),
    Requirement('UIExtensions', NEXUS+'17561?tab=files', (
        'scripts/UIExtensions.pex', 'scripts/UIListMenu.pex'), 'UIExtensions.esp'),
)
EVIDENCE_FILES = {name.casefold() for requirement in REQUIREMENTS for name in requirement.files}


def inspect_support(setup, resolve_path, *, requested=False):
    if setup.game_name != 'Skyrim Special Edition':
        return ()
    plugins = {p.name.casefold(): p for p in setup.plugins}
    assets = {a.path.replace('\\', '/').casefold(): a for a in setup.assets}
    def loose(name):
        path = resolve_path(name)
        return bool(path and readable_path(path).is_file() and readable_path(path).stat().st_size)
    def present(name):
        if loose(name): return True
        asset = assets.get(name.casefold())
        return bool(name.casefold().endswith('.pex') and asset and asset.archive)
    helper_plugin = plugins.get('obody.esp')
    helper_dll = assets.get('skse/plugins/obody.dll')
    # The collector inventories every DLL, including packed ones. Gate the
    # optional checks on that active inventory, not a speculative path lookup.
    if not (requested or helper_dll or
            (helper_plugin and helper_plugin.load_order >= 0)):
        return ()
    findings = []
    if not setup.skse_loader_present:
        findings.append(Finding('Blocked', 'shape-support-dependency-missing',
            'Install SKSE for character body customization', 'SKSE loader is not present in the selected game folder.',
            'Install the official SKSE package matching Skyrim '+setup.runtime+' through ModLab: https://skse.silverlock.org/ .',
            'Individual body shapes use SKSE. This requirement applies because character body customization was requested or OBody is active.'))
    for requirement in REQUIREMENTS:
        plugin = plugins.get(requirement.plugin.casefold()) if requirement.plugin else None
        if plugin and plugin.load_order < 0:
            findings.append(Finding('Blocked', 'shape-support-dependency-disabled',
                'Enable '+requirement.name+' for character body customization',
                requirement.plugin+'\nNeeded by: OBody NG\nInstalled provider: '+plugin.origin,
                'Enable this installed dependency, then recheck its effective support files. No new download is needed for the disabled-plugin finding.',
                requirement.name+' is installed but its plugin is disabled in this profile. ModLab will not re-enable it without your choice.'))
        missing = [name for name in requirement.files if not present(name)]
        if requirement.plugin and plugin is None:
            missing.insert(0, requirement.plugin)
        if not missing: continue
        uncertain = all(name.casefold().endswith('.pex') for name in missing) and not setup.archives_inspected
        findings.append(Finding('Unknown' if uncertain else 'Blocked', 'shape-support-dependency-missing',
            ('Check ' if uncertain else 'Complete ')+requirement.name+' for character body customization',
            'Support files not resolved:\n'+'\n'.join(missing),
            ('Inspect loaded archives before replacing this package; the scripts may be packed. ' if uncertain else '')+
            'Install or repair the complete '+requirement.name+' package for Skyrim '+setup.runtime+
            ' through ModLab, retaining its matching scripts and supporting files. Author files: '+requirement.url+' .',
            requirement.name+' supplies part of the requested individual-body workflow. '
            'These are support-file checks; an installed DLL alone does not complete that workflow.'))
    path = resolve_path('SKSE/Plugins/skee64.ini')
    if path and readable_path(path).is_file():
        try:
            from .shape_settings import feature_values
            with readable_path(path).open('r', encoding='utf-8-sig') as stream:
                text = stream.read(2 * 1024 * 1024 + 1)
            if len(text) > 2 * 1024 * 1024: raise ValueError('RaceMenu settings exceed the inspection limit.')
            settings = feature_values(text)
            values = [settings.get(name.casefold(), '') for name in ('bEnableBodyMorph', 'bEnableBodyGen')]
            if any(value not in {'0', '1'} for value in values):
                raise ValueError('RaceMenu body-controller settings could not be read as explicit on/off choices.')
            if values[0] == '0':
                findings.append(Finding('Blocked', 'shape-support-morph-disabled',
                    'Enable body shape support in RaceMenu', str(path)+'\n[Features] bEnableBodyMorph=0',
                    'Enable RaceMenu body morph support before preparing individual shapes. Keep a recoverable copy of the existing settings. Setup guide: '+GUIDE,
                    'RaceMenu currently disables the mechanism OBody uses to apply shapes. Sorting or cleaning will not enable it.'))
            if values[1] == '1':
                findings.append(Finding('Review', 'shape-support-controller-choice',
                    'Choose which system controls body shapes', str(path)+'\n[Features] bEnableBodyGen=1',
                    'Choose whether to retain RaceMenu BodyGen or use OBody for individual shapes. '
                    'The OBody setup guide requires BodyGen disabled: '+GUIDE+' . Existing controller settings were preserved.',
                    'RaceMenu body randomization is enabled. Adding OBody must not silently replace an existing distribution choice.'))
        except (OSError, UnicodeError, ValueError) as error:
            findings.append(Finding('Unknown', 'shape-support-config-unreadable',
                'Character body settings need review', str(path)+'\n'+str(error),
                'Restore or resolve the RaceMenu settings, then recheck. Setup guide: '+GUIDE,
                'ModLab could not establish the active body-controller settings. It has not rewritten them.'))
    return tuple(findings)
