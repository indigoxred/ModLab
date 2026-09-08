"""Character-first directory over active records, separate from replacement availability."""
import configparser
from pathlib import Path
import struct
from tempfile import TemporaryDirectory

from .body_ownership import records
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


def character_records(path, strings=None):
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
    for key, record in records(path):
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
    sources = []
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
            sources.append((name, character_records(path, strings)))
    return character_rows(sources, appearances)
