"""Resolve worn-body versus outfit meshes from winning Skyrim records.

Field definitions: TES5Edit/TES5Edit, Core/wbDefinitionsTES5.pas (ARMO,
ARMA, NPC_ and RACE). Read-only; no source plugin is rewritten.
"""
from pathlib import Path
import re
import struct
import zlib
from .bodyslide import relative_path
from .synthesis import plugin_masters

KINDS = {b'NPC_', b'RACE', b'ARMO', b'ARMA'}


def records(path):
    path = Path(path)
    masters = (*plugin_masters(path), path.name)
    def identity(form):
        if not form: return None
        index = form >> 24
        if index >= len(masters): raise ValueError('Invalid body/armor master reference in ' + path.name)
        return f'{form & 0xffffff:06x}:' + masters[index].casefold()
    with path.open('rb') as stream:
        def scan(limit, depth=0):
            if depth > 16: raise ValueError('Body/armor record nesting exceeds the inspection limit.')
            while stream.tell() < limit:
                start = stream.tell(); header = stream.read(24)
                if len(header) != 24: raise ValueError('Truncated body/armor record in ' + path.name)
                kind, size, flags, form = struct.unpack_from('<4sIII', header)
                finish = start + size if kind == b'GRUP' else start + 24 + size
                if finish > limit or finish < start + 24: raise ValueError('Invalid body/armor group in ' + path.name)
                if kind == b'GRUP':
                    if header[8:12] in KINDS or depth:
                        yield from scan(finish, depth + 1)
                elif kind in KINDS:
                    if flags & 0x20:
                        yield identity(form), None
                    else:
                        if size > 32 * 1024 * 1024: raise ValueError('Oversized body/armor record.')
                        data = stream.read(size)
                        if flags & 0x40000:
                            if len(data) < 4: raise ValueError('Truncated compressed body/armor record.')
                            declared = struct.unpack_from('<I', data)[0]
                            if declared > 32 * 1024 * 1024: raise ValueError('Oversized compressed body/armor record.')
                            decoder = zlib.decompressobj(); data = decoder.decompress(data[4:], declared + 1)
                            if len(data) != declared or not decoder.eof: raise ValueError('Incomplete compressed body/armor record.')
                        offset, fields, extended = 0, {}, None
                        while offset < len(data):
                            if offset + 6 > len(data): raise ValueError('Truncated body/armor field.')
                            tag, length = struct.unpack_from('<4sH', data, offset); offset += 6
                            if extended is not None: length, extended = extended, None
                            value = data[offset:offset+length]; offset += length
                            if len(value) != length: raise ValueError('Truncated body/armor field.')
                            if tag == b'XXXX':
                                if length != 4: raise ValueError('Invalid extended body/armor field.')
                                extended = struct.unpack('<I', value)[0]
                            else:
                                if (kind in {b'NPC_', b'RACE'} and tag == b'WNAM') or (kind == b'ARMO' and tag in {b'MODL', b'TNAM'}):
                                    if len(value) != 4: raise ValueError('Invalid skin/armor reference.')
                                    value = identity(struct.unpack('<I', value)[0])
                                fields.setdefault(tag, []).append(value)
                        yield identity(form), (kind, fields)
                stream.seek(finish)
        yield from scan(path.stat().st_size)


def model_paths(fields, sex=None):
    result = set()
    tags = (b'MOD3', b'MOD5') if sex == 'female' else (b'MOD2', b'MOD4') if sex == 'male' else (b'MOD2', b'MOD3', b'MOD4', b'MOD5')
    for tag in tags:
        for value in fields.get(tag, ()):
            text = value.rstrip(b'\0').decode('utf-8').replace('\\', '/').casefold()
            if not text: continue
            text = relative_path(text)
            if not text.startswith('meshes/'): text = 'meshes/' + text
            result.add(text)
            if re.search(r'_[01]\.nif$', text):
                result.update((text[:-5] + '0.nif', text[:-5] + '1.nif'))
    return result


def outfit_models(plugins, sex=None):
    winning = {}
    for path in plugins:
        for key, value in records(path):
            if value is None: winning.pop(key, None)
            else: winning[key] = value
    skins = {ref for kind, fields in winning.values() if kind in {b'NPC_', b'RACE'}
             for ref in fields.get(b'WNAM', ()) if ref}
    def armatures(key, seen, strict=False):
        if key in seen: raise ValueError('Cyclic skin/armor template reference.')
        record = winning.get(key)
        if not record or record[0] != b'ARMO':
            if strict: raise ValueError('A skin armor reference is missing; outfit ownership needs attention: ' + str(key))
            return set()
        fields = record[1]; result = {r for r in fields.get(b'MODL', ()) if r}
        for template in fields.get(b'TNAM', ()):
            if template: result.update(armatures(template, seen | {key}, strict))
        return result
    skin_addons = set()
    for skin in skins: skin_addons.update(armatures(skin, set(), True))
    protected = set()
    for key in skin_addons:
        addon = winning.get(key)
        if not addon or addon[0] != b'ARMA': raise ValueError('A skin armature is missing: ' + key)
        protected.update(model_paths(addon[1]))
    outfits = set()
    for key, (kind, _) in winning.items():
        if kind != b'ARMO' or key in skins: continue
        for addon_key in armatures(key, set()):
            addon = winning.get(addon_key)
            if addon and addon[0] == b'ARMA': outfits.update(model_paths(addon[1], sex))
    return outfits - protected


def active_outfit_models(organizer, sex=None):
    from .inspection import plugin_load_orders
    order = plugin_load_orders(organizer.pluginList())
    plugins = [Path(organizer.resolvePath(name)) for name in sorted(order, key=order.get) if order[name] >= 0]
    return outfit_models(plugins, sex)
