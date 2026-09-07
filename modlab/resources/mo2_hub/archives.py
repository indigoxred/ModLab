"""Check MO2's native BSA index against actual archive entry lists."""
from pathlib import Path

from .assessment import Asset, Finding

_reader = None


def archive_reader():
    global _reader
    if _reader is None:
        from PyQt6.QtCore import QCoreApplication
        from .archive_text import ArchiveReader
        _reader = ArchiveReader(Path(QCoreApplication.applicationDirPath()) / 'dlls/libbsarch.dll')
    return _reader


def check_archive_index(archives, selected, indexed, plugins, resolve_path, reader):
    selected = {name.casefold() for name in selected}
    findings, checked, count = [], [], 0
    complete = True
    for archive in archives:
        name = archive.path
        provider = archive.origins[0] if archive.origins else ''
        if name.casefold() not in selected:
            # Store/game resource archives may deliberately have no active loader.
            if provider.casefold() in {'data', 'game data', 'unmanaged', ''}:
                continue
            loaders = [p for p in plugins if name.casefold().startswith(Path(p.name).stem.casefold())]
            action = ('Enable ' + ', '.join(p.name for p in loaders) + ' in MO2 if this mod is wanted, then recheck.'
                      if loaders and all(p.load_order < 0 for p in loaders) else
                      'Check the supplying mod’s installer and archive-loading requirements. Reinstall the appropriate '
                      'option through ModLab or disable the mod if it is not wanted; then recheck.')
            findings.append(Finding('Review', 'archive-not-loaded', provider + ': packed assets are not selected to load',
                name + '\nProvider: ' + provider, action,
                'This archive is installed, but MO2 does not list it among the profile’s active archives. Its files '
                'cannot be assumed available in game. Sorting or cleaning does not enable a missing loader.'))
            complete = False
            continue
        try:
            entries = reader.entries(resolve_path(name))
            missing = [entry for entry in entries if provider not in indexed.get(entry, ())]
            if missing:
                raise ValueError(f'{len(missing)} archive entries are absent from the provider index.\n' + '\n'.join(missing[:20]))
            checked.append(f'{name} — {provider} — {len(entries):,} entries')
            count += len(entries)
        except Exception as error:
            complete = False
            findings.append(Finding('Unknown', 'archive-index-incomplete', 'Packed assets could not be fully checked: ' + provider,
                name + '\n' + str(error),
                'Enable MO2 Settings → Workarounds → Enable parsing of Archives, refresh MO2, and recheck. '
                'If this archive still fails, reinstall its mod from a complete download. Do not unpack or clean it to hide the error.'))
    if checked:
        findings.append(Finding('Info', 'archive-index-checked',
            f'Checked packed-file coverage: {len(checked)} archives, {count:,} entries', '\n'.join(checked),
            'No archive extraction is needed. Review the named replacement choices if you want a different appearance or behavior.',
            'ModLab read the archive filenames, checked MO2’s mod-archive providers, and included game archives using '
            'their INI and plugin loading order. Overlap findings include packed files and loose replacements. '
            'This checks file availability and selected winners; it does not '
            'prove that NPC records, meshes, textures or scripts are compatible with each other.'))
    return complete and bool(checked), tuple(findings)


def archive_rank(name, plugins, registered):
    name = name.casefold()
    # SSE: registered archives first; plugin-associated archives follow plugin
    # order. Loose files override both. Ambiguous sibling archives are not sorted
    # alphabetically or by MO2 mod priority.
    positions = [i for i, value in enumerate(registered) if value.casefold() == name]
    if positions:
        return (0, positions[-1])
    loaders = [p for p in plugins if p.load_order >= 0 and
               (name == Path(p.name).stem.casefold() + '.bsa' or
                name.startswith(Path(p.name).stem.casefold() + ' -'))]
    if len(loaders) == 1:
        return (1, loaders[0].load_order)
    raise ValueError('No unambiguous INI or active-plugin loading order was found for ' + name)


def selected_archives(names, registered, plugins):
    registered = {name.casefold() for name in registered}
    stems = {Path(p.name).stem.casefold() for p in plugins if p.load_order >= 0}
    return [name for name in names if name.casefold() in registered or any(
        name.casefold() == stem + '.bsa' or name.casefold().startswith(stem + ' -') for stem in stems)]


def active_archive_paths(organizer):
    import mobase
    from .assessment import Plugin
    feature = organizer.gameFeatures().gameFeature(mobase.DataArchives)
    if feature is None: raise ValueError('The game archive configuration is unavailable.')
    registered = list(feature.archives(organizer.profile()))
    plugins = organizer.pluginList()
    loaded = [Plugin(name, plugins.loadOrder(name), (), plugins.origin(name)) for name in plugins.pluginNames()]
    paths = {Path(p).name: p for p in organizer.findFiles('', ['*.bsa']) if Path(p).is_file()}
    return {name: paths[name] for name in selected_archives(paths, registered, loaded)}


def merge_game_archives(assets, indexed, packed, archives, game_archives, plugins, registered, resolve_path, reader):
    """MO2 2.5.2 omits BSAs physically in Game Data from its native file index."""
    from .foundations import foundation_asset
    result = {a.path.casefold(): a for a in assets}
    findings = []
    ordered = []
    for archive in archives:
        if archive.path not in game_archives:
            continue
        try:
            ordered.append((archive_rank(archive.path, plugins, registered), archive))
        except ValueError as error:
            findings.append(Finding('Unknown', 'archive-order-unknown', 'Archive loading order needs review',
                str(error), 'Check this mod’s loading instructions and selected plugin. ModLab has not chosen a winner for this archive.'))
    for rank, archive in sorted(ordered, key=lambda item: item[0]):
        provider = archive.origins[0] if archive.origins else 'data'
        try:
            for path in reader.entries(resolve_path(archive.path)):
                previous = indexed.get(path, ())
                winner = packed.get(path, '')
                if previous and winner:
                    prior_rank = archive_rank(winner, plugins, registered)
                    if prior_rank == rank and winner.casefold() != archive.path.casefold():
                        raise ValueError('Two archives at the same loading position contain ' + path + ': ' + winner + ', ' + archive.path)
                incoming_wins = not previous or bool(winner and rank > prior_rank)
                origins = tuple(dict.fromkeys(((provider,) + previous) if incoming_wins else (previous + (provider,))))
                indexed[path] = origins
                if incoming_wins:
                    packed[path] = archive.path
                if previous or path.endswith('.dll') or foundation_asset(path):
                    result[path] = Asset(path, origins, packed.get(path, ''))
        except (OSError, ValueError) as error:
            findings.append(Finding('Unknown', 'archive-order-unknown', 'Game archive coverage needs review: ' + archive.path,
                str(error), 'Review this archive’s download and loading instructions. Its complete conflict coverage has not been established.'))
    return list(result.values()), tuple(findings)


def inspect_archives(organizer, assets, indexed, packed, plugins):
    archives = [a for a in assets if a.path.casefold().endswith('.bsa') and '/' not in a.path]
    if not archives:
        return assets, False, ()
    import mobase
    feature = organizer.gameFeatures().gameFeature(mobase.DataArchives)
    if feature is None:
        raise ValueError('The game’s archive-loading feature is unavailable.')
    registered = list(feature.archives(organizer.profile()))
    # archives.txt is MO2's optional index cache, not Skyrim SE's loading rule.
    # It can be empty after a refresh even while the game loads its INI/plugin BSAs.
    selected = selected_archives([a.path for a in archives], registered, plugins)
    # The host feature does not account for archive-list overrides in custom or
    # plugin INIs. Keep those configurations explicit instead of certifying a
    # loading order that this adapter has not established.
    import configparser
    candidates = [Path(organizer.profile().absoluteIniFilePath('SkyrimCustom.ini'))]
    candidates += [Path(organizer.resolvePath(Path(p.name).stem + '.ini')) for p in plugins if p.load_order >= 0
                   and organizer.resolvePath(Path(p.name).stem + '.ini')]
    for path in candidates:
        if not path.is_file():
            continue
        ini = configparser.ConfigParser(interpolation=None, strict=False)
        ini.read(path, encoding='utf-8-sig')
        if any(key.casefold().startswith('sresourcearchive') for section in ini.sections()
               if section.casefold() == 'archive' for key in ini[section]):
            raise ValueError('Custom archive loading requires review: ' + str(path))
    assets, order_findings = merge_game_archives(assets, indexed, packed, archives, set(selected), plugins,
        registered, organizer.resolvePath, archive_reader())
    checked, findings = check_archive_index(archives, selected, indexed, plugins, organizer.resolvePath, archive_reader())
    return assets, checked and not order_findings, order_findings + findings


def enable_portable_index(organizer, app_root):
    """Use the known portable instance's setting; never guess another instance."""
    from PyQt6.QtCore import QSettings
    if organizer.managedGame().gameName() != 'Skyrim Special Edition':
        return False
    app_root = Path(app_root)
    ini = app_root / 'ModOrganizer.ini'
    if not (app_root / 'portable.txt').is_file() or not ini.is_file():
        return False
    settings = QSettings(str(ini), QSettings.Format.IniFormat)
    base = str(settings.value('Settings/base_directory', str(app_root)))
    profiles = str(settings.value('Settings/profiles_directory', '%BASE_DIR%/profiles')).replace('%BASE_DIR%', base)
    if Path(profiles).resolve() != Path(organizer.profilePath()).resolve().parent:
        return False
    if settings.value('Settings/archive_parsing_experimental', False, type=bool):
        return False
    backup = ini.with_name('ModOrganizer.ini.before-modlab-archive-index')
    if not backup.exists():
        with backup.open('xb') as stream:
            stream.write(ini.read_bytes())
    settings.setValue('Settings/archive_parsing_experimental', True)
    settings.sync()
    if settings.status() != QSettings.Status.NoError:
        raise OSError('MO2’s archive inspection setting could not be saved.')
    return True
