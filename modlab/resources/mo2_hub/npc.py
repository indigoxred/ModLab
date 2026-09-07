"""Read NPC identities and preserve paired appearance assets; never edit source plugins."""
from pathlib import Path
import re
import shutil
import struct
import zlib

from .outputs import digest
from .synthesis import plugin_masters
from .vfs import readable_path


def constrain_order(order, appearance, downstream):
    if appearance not in order: return tuple(order)
    return tuple(p for p in order if p != appearance and p not in downstream) + (appearance,) + tuple(p for p in order if p in downstream)


def npc_records(path):
    path = readable_path(path)
    masters = (*plugin_masters(path), path.name)
    result = {}
    with path.open('rb') as stream:
        end = path.stat().st_size
        header = stream.read(24)
        localized = bool(struct.unpack_from('<I', header, 8)[0] & 0x80)
        stream.seek(0)
        def scan(limit, depth=0):
            if depth > 16:
                raise ValueError('Plugin group nesting exceeds the inspection limit: ' + path.name)
            while stream.tell() < limit:
                start = stream.tell(); header = stream.read(24)
                if len(header) != 24:
                    raise ValueError('Truncated plugin record: ' + path.name)
                kind, size, flags, form = struct.unpack_from('<4sIII', header)
                finish = start + size if kind == b'GRUP' else start + 24 + size
                if finish > limit or finish <= start + (23 if kind == b'GRUP' else 0):
                    raise ValueError('Invalid or truncated plugin group: ' + path.name)
                if kind == b'GRUP':
                    # Only the NPC top-level group is needed; nested NPC groups are bounded too.
                    if header[8:12] == b'NPC_' or depth:
                        scan(finish, depth + 1)
                elif kind == b'NPC_' and not flags & 0x20:
                    if size > 32 * 1024 * 1024:
                        raise ValueError('NPC record exceeds the inspection limit.')
                    data = stream.read(size)
                    if flags & 0x40000:
                        if len(data) < 4: raise ValueError('Compressed NPC record is truncated.')
                        declared = struct.unpack_from('<I', data)[0]
                        if declared > 32 * 1024 * 1024: raise ValueError('Compressed NPC record is oversized.')
                        decoder = zlib.decompressobj()
                        data = decoder.decompress(data[4:], declared + 1)
                        if len(data) != declared or not decoder.eof: raise ValueError('Compressed NPC record is incomplete.')
                    offset, fields, extended = 0, {}, None
                    while offset < len(data):
                        if offset + 6 > len(data): raise ValueError('NPC subrecord is truncated.')
                        tag, length = struct.unpack_from('<4sH', data, offset); offset += 6
                        if extended is not None: length, extended = extended, None
                        value = data[offset:offset + length]; offset += length
                        if len(value) != length: raise ValueError('NPC subrecord is truncated.')
                        if tag == b'XXXX':
                            if length != 4: raise ValueError('Invalid extended NPC subrecord.')
                            extended = struct.unpack('<I', value)[0]
                        else: fields[tag] = value
                    index = form >> 24
                    if index >= len(masters): raise ValueError('NPC master index is invalid: ' + path.name)
                    key = f'{form & 0xffffff:06X}:' + masters[index].casefold()
                    editor = fields.get(b'EDID', b'').rstrip(b'\0').decode('utf-8', errors='replace') or key
                    full = '' if localized else fields.get(b'FULL', b'').rstrip(b'\0').decode('utf-8', errors='replace')
                    result[key] = f'{full} ({editor})' if full else editor
                stream.seek(finish)
        scan(end)
    return result


def facegen_paths(key):
    match = re.fullmatch(r'([0-9A-Fa-f]{6}):([^/\\:]+\.(?:esm|esp|esl))', key, re.I)
    if not match: raise ValueError('Invalid NPC identity: ' + key)
    form, plugin = match.groups()
    return (f'meshes/actors/character/FaceGenData/FaceGeom/{plugin}/00{form}.nif',
            f'textures/actors/character/FaceGenData/FaceTint/{plugin}/00{form}.dds')


def validate_selections(catalog, selected):
    if not selected: raise ValueError('Choose an appearance for at least one NPC.')
    for key, plugin in selected.items():
        option = catalog.get(key, {}).get('options', {}).get(plugin)
        if not option: raise ValueError(f'The selected appearance is no longer available: {key} — {plugin}')
        if not option['ready']: raise ValueError(option['problem'])


def merge_assets(source, output):
    """Combine independently generated provider assets, rejecting divergent shared paths."""
    for path in readable_path(source).rglob('*'):
        if not path.is_file(): continue
        relative = path.relative_to(readable_path(source))
        if path.is_symlink(): raise ValueError('Generated appearance assets cannot be linked paths.')
        target = readable_path(output) / relative
        if target.exists():
            if digest(path) != digest(target):
                raise ValueError('Selected appearances require different versions of the same asset: ' + relative.as_posix() +
                                 '. Choose appearances with compatible shared assets, or install an author-supported compatibility patch.')
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
