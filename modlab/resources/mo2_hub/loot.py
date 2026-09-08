"""Validate MO2 lootcli proposals and explain its metadata without blanket fixes.

Protocol: ModOrganizer2/modorganizer-lootcli src/main.cpp and lootthread.cpp.
Original reports are retained by the caller alongside the proposed order.
"""

from .assessment import Finding


def reconcile_requirements(findings, game_root, resolve_path, runtime):
    """Explain one obsolete metadata rule from exact installed package evidence.

    LOOT's current upstream masterlist distinguishes v7 preloader-only from older
    TBB requirements: https://github.com/loot/skyrimse/blob/master/masterlist.yaml
    Author package: https://www.nexusmods.com/skyrimspecialedition/mods/17230?tab=files
    Checksummed pair also retained from this project's successful 7.0.20 startup.
    This resolves the file requirement only, never overall native compatibility.
    """
    from pathlib import Path
    from .outputs import digest
    legacy = ('It appears you have installed **SSE Engine Fixes - Part 1**, but some of its requirements seem to be missing. '
              'Please ensure you have correctly installed **[SSE Engine Fixes - Part 2](https://www.nexusmods.com/skyrimspecialedition/mods/17230/)**.')
    if runtime not in {'1.6.1170', '1.6.1170.0'} or not any(
            f.code == 'loot-message' and f.level == 'Review' and f.detail == legacy for f in findings):
        return tuple(findings)
    try:
        native = Path(resolve_path('SKSE/Plugins/EngineFixes.dll') or '')
        preloader = Path(game_root) / 'd3dx9_42.dll'
        if (not native.is_file() or not preloader.is_file()
                or digest(native) != '5d1384acfb523abd1333f5af71af0b7d131b6ebb1a0ee6b3edff86fb4c93adf3'
                or digest(preloader) != 'cf366987da6237559eb6e113ea717ec21762c9eec3a87d9dc4fa9ddfe7789c26'):
            return tuple(findings)
    except OSError:
        return tuple(findings)
    return tuple(Finding('Info', 'loot-requirement-explained', 'Engine Fixes preloader requirement is satisfied',
        f.detail + '\n\nMatched Engine Fixes 7.0.20 / 1.6.1170 DLL and preloader by SHA-256.\n'
        + str(native) + '\n' + str(preloader),
        'No extra TBB download is required for this matched build. Keep startup checks enabled; file identity does not prove every fix works.',
        'The older LOOT rule asks for separate TBB libraries used by earlier releases. The installed 7.0.20 variant '
        'requires its preloader instead, and the matching file is installed beside SkyrimSE.exe. The original advice remains in technical evidence.')
        if f.code == 'loot-message' and f.level == 'Review' and f.detail == legacy else f for f in findings)


def validate_order(text, plugins):
    names = {p.name.casefold(): p.name for p in plugins}
    lines = [line.strip() for line in text.lstrip('\ufeff').splitlines()
             if line.strip() and not line.lstrip().startswith('#')]
    keys = [name.casefold() for name in lines]
    if len(keys) != len(set(keys)) or set(keys) != set(names):
        raise ValueError('LOOT output does not contain exactly the current plugins. Recheck and sort again.')
    positions = {name: index for index, name in enumerate(keys)}
    for plugin in plugins:
        if plugin.load_order < 0:
            continue
        for master in plugin.masters:
            if master.casefold() not in positions:
                raise ValueError(f'{plugin.name} requires missing master {master}.')
            if positions[master.casefold()] >= positions[plugin.name.casefold()]:
                raise ValueError(f'{master} must load before {plugin.name}.')
    return tuple(names[key] for key in keys)


def report_findings(report, plugins):
    if not isinstance(report, dict) or not isinstance(report.get('stats'), dict):
        raise ValueError('LOOT report is missing its expected structure.')
    findings = []
    active = {p.name.casefold() for p in plugins if p.load_order >= 0}

    def array(value, label):
        if not isinstance(value, list):
            raise ValueError(f'LOOT report has invalid {label}.')
        return value

    def messages(items, name):
        for item in array(items, 'messages'):
            if not isinstance(item, dict) or not isinstance(item.get('text'), str):
                raise ValueError('LOOT report contains an unreadable message.')
            level = {'info': 'Info', 'warn': 'Review', 'error': 'Blocked'}.get(item.get('type'), 'Unknown')
            findings.append(Finding(level, 'loot-message', f'LOOT advice: {name}', item['text'],
                'Follow the applicable mod instructions and linked patch requirements. '
                'LOOT metadata does not check every asset, script or gameplay interaction.'))

    messages(report.get('messages', []), 'this setup')
    for plugin in array(report.get('plugins', []), 'plugins'):
        if not isinstance(plugin, dict) or not isinstance(plugin.get('name'), str):
            raise ValueError('LOOT report contains an unnamed plugin.')
        name = plugin['name']
        if name.casefold() not in active:
            continue
        messages(plugin.get('messages', []), name)
        for key, title in (('missingMasters', 'Missing masters'), ('incompatibilities', 'Reported incompatibilities')):
            entries = array(plugin.get(key, []), key)
            if entries:
                labels = []
                for entry in entries:
                    if isinstance(entry, str):
                        labels.append(entry)
                    elif isinstance(entry, dict) and isinstance(entry.get('name'), str):
                        labels.append(entry.get('displayName') or entry['name'])
                    else:
                        raise ValueError(f'LOOT report has an unreadable {key} entry.')
                findings.append(Finding('Blocked', f'loot-{key}', f'LOOT: {title} — {name}',
                    ', '.join(labels),
                    'Check the named requirements and current author instructions. Resolve the combination '
                    'or disable the affected plugin; a load-order change alone may not fix it.'))
        dirty = array(plugin.get('dirty', []), 'cleaning advice')
        if dirty:
            descriptions = []
            for entry in dirty:
                if not isinstance(entry, dict):
                    raise ValueError('LOOT report has unreadable cleaning advice.')
                descriptions.append(
                    f"{entry.get('cleaningUtility', 'xEdit')} reports "
                    f"{entry.get('itm', 0)} identical-to-master records, "
                    f"{entry.get('deletedReferences', 0)} deleted references, and "
                    f"{entry.get('deletedNavmesh', 0)} deleted navmeshes. "
                    f"{entry.get('info', '')}")
            findings.append(Finding('Review', 'loot-cleaning', f'Cleaning advice: {name}',
                '\n'.join(descriptions),
                'Check the author instructions and exact plugin version before selecting xEdit cleaning. '
                'Intentional edits and special instructions take precedence; do not clean everything.',
                'LOOT has a cleaning recommendation for this plugin. Identical-to-master records repeat data from a dependency; '
                'their presence alone does not prove that the mod is broken. Deleted references and navmeshes need separate consideration. '
                'ModLab has left the plugin unchanged because it has not yet verified an applicable cleaning procedure. '
                'This recommendation remains unresolved; automatic cleaning is not yet available for this plugin.'))
    return tuple(findings)
