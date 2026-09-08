"""Explain active MO2 evidence without pretending to certify gameplay."""

from dataclasses import dataclass
import re


# Author pages checked 2026-09-06. These identify downloads, not universal
# recommendations or permission to install/clean a particular release.
MASTER_PAGES = {
    'unofficial skyrim special edition patch.esp': 'https://www.nexusmods.com/skyrimspecialedition/mods/266',
    'skyui_se.esp': 'https://www.nexusmods.com/skyrimspecialedition/mods/12604',
    'racemenu.esp': 'https://www.nexusmods.com/skyrimspecialedition/mods/19080',
}
GAME_MASTERS = {'skyrim.esm', 'update.esm', 'dawnguard.esm', 'hearthfires.esm',
                'dragonborn.esm', '_resourcepack.esl'}


def missing_master_action(name, runtime=''):
    key = name.casefold()
    preservation = (' Keep a backup of the selected game files before store repair or content downloads: '
                    'Steam can update a downgraded installation. Restore the intended runtime and matching files '
                    f'afterward, then recheck (current runtime: {runtime or "unknown"}).')
    if key in GAME_MASTERS:
        return ('Check that this is the correct game installation. Verify/repair its files through the owning '
                'store launcher if necessary, then use Recheck and finish setup. '
                'Do not replace an official master with a mod download.' + preservation)
    if re.fullmatch(r'cc[a-z0-9-]+\.(?:esm|esl|esp)', key):
        return ('Install the required Creation you own through the game’s Creations menu, or disable the mods that '
                'require it. Not every Creation is included with every purchase. Then use Recheck and finish setup.' + preservation)
    if key == 'unofficial skyrim special edition patch.esp' and runtime in {'1.6.1170', '1.6.1170.0'}:
        from .game_setup import USSEP_URL
        return ('For Skyrim 1.6.1170, the checked USSEP archive is 4.3.8a: ' + USSEP_URL +
                ' . Install through ModLab and enable its plugin. Keep its required official content. '
                'Check whether the dependent mod requires a newer USSEP release; this link does not make '
                'every dependent mod compatible. Then use Recheck and finish setup.')
    source = MASTER_PAGES.get(key)
    if source:
        return ('Install the matching dependency archive through ModLab and enable its plugin. Author download and '
                'version requirements: ' + source + '. Then use Recheck and finish setup. '
                'If you do not want that dependency, disable the affected mods instead.')
    return ("Install or enable the mod supplying this exact dependency, or disable the affected mods. "
            "Use the dependent mod's requirements page to identify the correct download; ModLab has no verified "
            "download mapping for this name. Then use Recheck and finish setup.")


@dataclass(frozen=True)
class Plugin:
    name: str
    load_order: int
    masters: tuple[str, ...] = ()
    origin: str = ""


@dataclass(frozen=True)
class Asset:
    path: str
    origins: tuple[str, ...] = ()
    archive: str = ""


@dataclass(frozen=True)
class SetupSnapshot:
    game_name: str
    runtime: str
    profile: str
    game_root: str
    plugins: tuple[Plugin, ...] = ()
    assets: tuple[Asset, ...] = ()
    errors: tuple[str, ...] = ()
    skse_loader_present: bool = False
    archives_inspected: bool = False
    generated_verified: tuple[str, ...] = ()
    generated_findings: tuple = ()
    startup_checked: bool = False


@dataclass(frozen=True)
class Finding:
    level: str
    code: str
    title: str
    detail: str
    action: str
    explanation: str = ""


@dataclass(frozen=True)
class Assessment:
    findings: tuple[Finding, ...]

    @property
    def has_blockers(self) -> bool:
        return any(f.level == "Blocked" for f in self.findings)

    @property
    def summary(self) -> str:
        if self.has_blockers:
            return "Resolve the identified blockers before launching this setup."
        if any(f.code in {"inspection-incomplete", 'archive-index-incomplete', 'archive-order-unknown', 'record-check-incomplete'} for f in self.findings):
            return "File inspection is incomplete. Review the unresolved checks before relying on this setup."
        if any(f.code == 'record-checks-pending' for f in self.findings):
            return 'Record checks are pending. Use Recheck and finish setup to check the current plugins.'
        if any(f.code == 'native-load-failed' for f in self.findings):
            return 'Some installed plugins did not load in the checked launch. Review their reported requirements.'
        if any(f.code == 'native-load-checked' for f in self.findings):
            return 'No blockers identified by these checks. SKSE startup was checked; gameplay features remain unverified.'
        return "No blockers identified by these checks. Runtime and features are not yet checked."


def asset_category(path: str) -> tuple[str, str]:
    """Group replacement decisions, keeping specialist assets distinguishable."""
    path = path.replace("\\", "/").casefold()
    name = path.rsplit("/", 1)[-1]
    if '/facegendata/' in path:
        return 'NPC faces', ('Different appearances can share a character. Open NPC appearances to choose a provider for that character; '
                             'ModLab will keep its face records, mesh and face tint together. Plugin order alone may leave a mismatched face.')
    if name.startswith("skeleton") and path.endswith(".nif"):
        return "Skeletons", "Check the winning skeleton against the body, physics and animation requirements for the affected actor. Different actor paths can legitimately use different skeletons."
    if path.endswith(".hkx"):
        return "Animations and behaviors", "Check the animation framework and any required behavior generation. Plugin sorting does not rebuild behaviors."
    if path.startswith("scripts/"):
        return "Scripts", "Check the script providers' versions and compatibility instructions. Replacing a script can change behavior even when plugins load correctly."
    if path.startswith("textures/"):
        if path.startswith('textures/actors/character/'):
            return "Character textures", "Choose the intended character skin and check its compatibility with the selected body and face assets."
        return "Textures", "Review which texture provider you want for these files. This overlap identifies the effective replacement; it does not establish a visual defect or a required repair."
    if path.startswith("meshes/"):
        return "Meshes", "Check that the winning meshes are the intended replacements and match their textures, body and physics requirements where applicable."
    return "Other files", "Check the intended replacement and its requirements."


def assess(setup: SetupSnapshot) -> Assessment:
    if setup.game_name != "Skyrim Special Edition":
        return Assessment((Finding(
            "Blocked", "unsupported-game", "Select Skyrim Special Edition",
            f"This instance manages {setup.game_name or 'an unidentified game'}.",
            "Open a Skyrim SE/AE instance. Separate game environments are outside this prototype.",
        ),))

    findings = [Finding(
        "Unknown", "inspection-incomplete", "Part of the setup could not be inspected",
        error, "Resolve the inspection error and recheck before relying on this report.",
    ) for error in setup.errors]
    findings.extend(setup.generated_findings)
    plugins = {p.name.casefold(): p for p in setup.plugins}
    dependency_issues = {}
    for plugin in setup.plugins:
        if plugin.load_order < 0:
            continue
        for name in plugin.masters:
            master = plugins.get(name.casefold())
            if master is None:
                code, title, action = 'missing-master', f'Missing dependency: {name}', missing_master_action(name, setup.runtime)
            elif master.load_order < 0:
                code, title, action = ('inactive-master', f'Dependency is disabled: {master.name}',
                    f'Enable {master.name} from {master.origin or "its supplying mod"}, then use Recheck and finish setup. '
                    'Its files are already installed; another download is not needed to fix this disabled state.')
            elif master.load_order >= plugin.load_order:
                code, title, action = ('master-order', f'Dependency loads too late: {master.name}',
                    'Use Recheck and finish setup. ModLab will ask LOOT for an order and check that each master precedes '
                    'its dependents before applying it. A missing or disabled dependency must be resolved first.')
            else:
                continue
            group = dependency_issues.setdefault((code, name.casefold()), [title, action, []])
            group[2].append(f'{plugin.name} requires {name}. Provider: {plugin.origin or "not reported"}.')
    for (code, _), (title, action, affected) in dependency_issues.items():
        detail = '\n'.join(dict.fromkeys(affected))
        findings.append(Finding('Blocked', code, title, detail, action,
            'These active plugins declare this master as required. Sorting or cleaning cannot substitute for '
            'a missing dependency; resolving this one requirement helps every affected mod listed below.\n\n' + detail
            if code == 'missing-master' else 'The dependency exists, but this active setup cannot use it in its current state.\n\n' + detail))

    native = []
    has_archives = False
    overlaps = {}
    for asset in setup.assets:
        path = asset.path.replace("\\", "/").casefold()
        if path.startswith("skse/plugins/") and path.endswith(".dll"):
            native.append(asset.path)
        has_archives |= path.endswith(".bsa") or bool(asset.archive)
        origins = tuple(dict.fromkeys(asset.origins))
        if len(origins) > 1 and path not in setup.generated_verified:
            category, action = asset_category(asset.path)
            overlaps.setdefault((category, action, origins, asset.archive), []).append(asset.path)
    for (category, action, origins, archive), paths in overlaps.items():
        base_replacement = category in {'Meshes', 'Textures'} and all(o.casefold() in {'data', 'game data'} for o in origins[1:])
        findings.append(Finding(
            "Info" if base_replacement else "Review", "asset-overlap",
            f"{category}: {origins[0]} replaces files from {', '.join(origins[1:])}",
            f"Winning provider: {origins[0]}\nOverridden providers: " + ", ".join(origins[1:]) +
            (f"\nArchive: {archive}" if archive else "") +
            "\n\nAffected files:\n" + "\n".join(sorted(paths, key=str.casefold)),
            ('No conflict repair is needed solely because this replaces the game’s original assets. '
             'Check the intended appearance in game; matching meshes and textures still matter.' if base_replacement else
             action + " An overlap alone does not prove incompatibility."),
            f"{len(paths)} file{'s are' if len(paths) != 1 else ' is'} supplied by more than one mod. "
            f"MO2 uses the version from {origins[0]}; the other copies remain installed. "
            "This is how many replacements and generated outfits work. It is not an installation error by itself. "
            "ModLab has identified which files win, but has not yet established whether this particular combination is intended.",
        ))
    from .foundations import SKSE_DEPENDENTS
    script_dependents = [p for p in setup.plugins if p.load_order >= 0 and p.name.casefold() in SKSE_DEPENDENTS]
    if (native or script_dependents) and not setup.skse_loader_present:
        findings.append(Finding(
                "Blocked", "skse-loader-missing", "SKSE loader is missing",
                "Active components requiring SKSE: " + ", ".join(native + [p.name for p in script_dependents]) +
                ''.join('\n' + p.name + ' — ' + p.origin + '\nRequirements: ' + SKSE_DEPENDENTS[p.name.casefold()]
                        for p in script_dependents),
                "Download SKSE matching this Skyrim runtime and store from https://skse.silverlock.org/ "
                "and use Install archives in ModLab. Enable its scripts mod, then recheck.",
        ))
    if native and not setup.startup_checked:
        findings.append(Finding(
            "Unknown", "native-compatibility-unverified", "Native plugin compatibility needs checking",
            f"Skyrim runtime: {setup.runtime or 'unavailable'}. Components: " + ", ".join(native),
            "Check these releases' runtime requirements and SKSE load results. DLL presence is not proof of loading.",
        ))
    if has_archives and not setup.archives_inspected:
        findings.append(Finding(
            "Unknown", "archive-coverage", "Archive-contained assets are not fully inspected",
            "This report includes visible files, but does not establish complete BSA content or conflict coverage.",
            "Check MO2 archive parsing and archive-loading plugins before concluding that assets have no conflicts.",
        ))
    return Assessment(tuple(findings))
