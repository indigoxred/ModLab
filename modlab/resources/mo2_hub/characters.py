"""Character-first directory over active records, separate from replacement availability."""
import configparser
from pathlib import Path
import struct
from tempfile import TemporaryDirectory

from .body_ownership import records, BodyIndex
from .synthesis import plugin_masters


def parse_strings(data):
    if len(data) < 8 or len(data) > 64 * 1024 * 1024:
        raise ValueError('Character name table has an invalid size.')
    count, size = struct.unpack_from('<II', data)
    start = 8 + count * 8
    if start + size != len(data): raise ValueError('Character name table is truncated.')
    result = {}
    for index in range(count):
        key, offset = struct.unpack_from('<II', data, 8 + index * 8)
        if offset >= size: raise ValueError('Character name offset is outside the table.')
        end = data.find(b'\0', start + offset)
        if end < 0: raise ValueError('Character name is not terminated.')
        raw = data[start + offset:end]
        try: result[key] = raw.decode('utf-8')
        except UnicodeDecodeError: result[key] = raw.decode('cp1252', errors='replace')
    return result


def winning_strings(candidates):
    if not candidates: return {}
    rank=max(rank for rank, _ in candidates)
    winners=[data for position,data in candidates if position==rank]
    if any(data != winners[0] for data in winners[1:]):
        raise ValueError('Competing character name tables at the same archive loading position.')
    return parse_strings(winners[0])


def character_records(path, strings=None, *, parsed=None):
    path = Path(path); strings = strings or {}
    with path.open('rb') as stream:
        header = stream.read(24)
    if len(header) != 24: raise ValueError('Character plugin header is truncated.')
    localized = bool(struct.unpack_from('<I', header, 8)[0] & 0x80)
    masters = (*plugin_masters(path), path.name)
    def reference(value):
        if not value: return None
        if len(value) != 4: raise ValueError('Character reference is malformed.')
        form = struct.unpack('<I', value)[0]
        if not form: return None
        if form >> 24 >= len(masters): raise ValueError('Character master reference is invalid.')
        return f'{form & 0xffffff:06X}:' + masters[form >> 24].casefold()
    result = {}
    for key, record in records(path) if parsed is None else parsed:
        key = key.split(':')[0].upper() + ':' + key.split(':')[1]
        if record is None:
            result[key] = None; continue
        kind, fields = record
        if kind != b'NPC_': continue
        def first(tag, default=b''): return fields.get(tag, [default])[0]
        editor = first(b'EDID').rstrip(b'\0').decode('utf-8', errors='replace')
        full = first(b'FULL')
        if localized:
            full = strings.get(struct.unpack('<I', full)[0], '') if len(full) == 4 else ''
        else: full = full.rstrip(b'\0').decode('utf-8', errors='replace')
        flags = struct.unpack_from('<I', first(b'ACBS'))[0] if len(first(b'ACBS')) >= 4 else 0
        skin = first(b'WNAM', None)  # The bounded record reader resolves WNAM references.
        if skin: skin = skin.split(':')[0].upper() + ':' + skin.split(':')[1]
        result[key] = dict(name=full or editor or key, editor=editor,
            localized_name_missing=localized and not bool(full), unique=bool(flags & 0x20),
            sex='female' if flags & 1 else 'male', skin=skin,
            race=reference(first(b'RNAM')), template=reference(first(b'TPLT')))
    return result


def character_rows(sources, appearances):
    rows = {}
    for plugin, characters in sources:
        for key, character in characters.items():
            if character is None: rows.pop(key, None)
            else: rows[key] = dict(character, plugin=plugin)
    for key, row in rows.items():
        row['options'] = appearances.get(key, {}).get('options', {})
        row['search'] = ' '.join([key, row['name'], row['editor'], row['plugin'],
            *row['options'], *(o['mod'] for o in row['options'].values())]).casefold()
    return rows


def active_characters(organizer, appearances):
    from .archives import active_archive_paths, archive_reader, archive_rank
    from .assessment import Plugin
    import mobase
    from .inspection import plugin_load_orders
    config = configparser.ConfigParser(interpolation=None, strict=False)
    config.read(organizer.profile().absoluteIniFilePath('Skyrim.ini'), encoding='utf-8-sig')
    language = config.get('General', 'sLanguage', fallback='english').casefold()
    if not language.isalpha(): raise ValueError('The profile language is invalid.')
    order = plugin_load_orders(organizer.pluginList())
    reader, archives = None, None
    feature=organizer.gameFeatures().gameFeature(mobase.DataArchives)
    registered=list(feature.archives(organizer.profile())) if feature else []
    ordered_plugins=[Plugin(name,position,(),organizer.pluginList().origin(name)) for name,position in order.items()]
    sources = []; bodies = BodyIndex()
    with TemporaryDirectory(prefix='modlab-character-names-') as temp:
        for name in sorted(order, key=order.get):
            if order[name] < 0: continue
            path = Path(organizer.resolvePath(name))
            with path.open('rb') as stream: header = stream.read(24)
            strings = {}
            if len(header) == 24 and struct.unpack_from('<I', header, 8)[0] & 0x80:
                resource = 'strings/' + path.stem.casefold() + '_' + language + '.strings'
                loose = organizer.resolvePath(resource)
                if loose and Path(loose).is_file():
                    strings = parse_strings(Path(loose).read_bytes())
                else:
                    if archives is None: archives = active_archive_paths(organizer)
                    if reader is None: reader = archive_reader()
                    contents = []
                    for archive_name,archive in archives.items():
                        if resource not in reader.entries(archive): continue
                        target = Path(temp)/Path(resource).name
                        reader.extract_asset(archive, resource, target)
                        contents.append((archive_rank(archive_name,ordered_plugins,registered),target.read_bytes()))
                    strings = winning_strings(contents)
            parsed = list(records(path))
            bodies.add_plugin(path, parsed)
            sources.append((name, character_records(path, strings, parsed=parsed)))
    rows = character_rows(sources, appearances)
    for key, row in rows.items():
        try: row['body'] = bodies.character(key)
        except ValueError as error: row['body_problem'] = str(error)
    return rows


def body_description(row, resolve, mods_path, *, overwrite_path=None, game_data=None):
    """Name effective loose providers without mistaking a record owner for a file owner."""
    from .guidance import display_name
    body = row.get('body')
    if not body:
        return dict(body='This character needs a closer body check before customization. Their current setup is retained.',
                    skin='Current skin retained.', evidence=row.get('body_problem', 'Body assignment could not be read.'))
    root = Path(mods_path).resolve()
    locations = [(Path(path).resolve(), label) for path, label in
                 ((overwrite_path, 'MO2 Overwrite'), (game_data, 'Game Data')) if path]
    def providers(paths):
        names = set(); unresolved = False
        for relative in paths:
            value = resolve(relative)
            if not value or not Path(value).is_file():
                unresolved = True; continue
            path = Path(value).resolve()
            try: name = path.relative_to(root).parts[0]
            except ValueError:
                name = next((label for folder, label in locations if path.is_relative_to(folder)), 'External files')
            names.add(display_name(name))
        return sorted(names, key=str.casefold), unresolved
    names, unresolved = providers(body['body_models'])
    scope = {'shared':'Uses the shared '+body['sex']+' body.',
             'private':'Uses replacer body files instead of the shared body. Other characters may use those files too.',
             'mixed':'Uses a mixture of shared and separate body parts.'}[body['scope']]
    if names: scope += '\nSupplied by '+', '.join(names)+'.'
    if unresolved: scope += '\nSome body files need archive inspection before customization.'
    textures, texture_unknown = providers(body['textures'])
    skin = 'Base skin supplied by '+', '.join(textures)+'.' if textures else 'Uses the skin linked to this body.'
    if texture_unknown: skin += '\nSome skin files still need archive inspection.'
    if any(not part['texture_set'] for part in body['parts']):
        skin += '\nSome skin textures are linked inside the body meshes.'
    evidence = '\n'.join(['Static base-record assignment; existing saves and scripts may alter it.',
        'Skin record: '+body['skin'], 'Skin record provider: '+body['skin_plugin'], *body['body_models'], *body['textures']])
    variations = sorted({part['texture_swap'] for part in body['parts'] if part['texture_swap']})
    if variations: evidence += '\nTexture variation rules also apply (not resolved here): '+', '.join(variations)
    return dict(body=scope, skin=skin, evidence=evidence)
