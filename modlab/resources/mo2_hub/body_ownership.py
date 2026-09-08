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

KINDS = {b'NPC_', b'RACE', b'ARMO', b'ARMA', b'TXST'}


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


class BodyIndex:
    """Winning record ownership for character choices; no assumptions from mod names.

    This resolves static base-actor records. Leveled templates, scripted skin swaps,
    and existing-save changes are not converted into a guessed static assignment.
    """
    def __init__(self):
        self.winning = {}
        self.masters = {}

    def add_plugin(self, path, parsed=None):
        path = Path(path)
        self.masters[path.name] = (*plugin_masters(path), path.name)
        for key, value in records(path) if parsed is None else parsed:
            if value is None: self.winning.pop(key.casefold(), None)
            else: self.winning[key.casefold()] = (*value, path.name)

    def _record(self, key, kind, description):
        value = self.winning.get((key or '').casefold())
        if not value or value[0] != kind:
            raise ValueError('The '+description+' is missing or cannot be resolved as a static record: '+str(key))
        return value[1], value[2]

    def _one(self, fields, tag, default=None):
        values = fields.get(tag, ())
        if len(values) > 1: raise ValueError('Ambiguous character body field: '+tag.decode())
        return values[0] if values else default

    def _reference(self, value, plugin):
        if value is None: return None
        if isinstance(value, str): return value.casefold()
        if len(value) != 4: raise ValueError('Invalid character body reference in '+plugin)
        form = struct.unpack('<I', value)[0]
        if not form: return None
        masters = self.masters[plugin]
        if form >> 24 >= len(masters): raise ValueError('Invalid character body master in '+plugin)
        return f'{form & 0xffffff:06x}:'+masters[form >> 24].casefold()

    def _ref(self, fields, tag, plugin):
        return self._reference(self._one(fields, tag), plugin)

    def _traits(self, key, seen=()):
        key = key.casefold()
        if key in seen or len(seen) >= 32: raise ValueError('Cyclic or excessive character traits template chain.')
        fields, plugin = self._record(key, b'NPC_', 'character traits template')
        config = self._one(fields, b'ACBS', b'')
        if len(config) != 24: raise ValueError('Character trait flags are missing or malformed: '+key)
        if struct.unpack_from('<H', config, 18)[0] & 1:
            template = self._ref(fields, b'TPLT', plugin)
            if not template: raise ValueError('Character requests traits from a missing template: '+key)
            return self._traits(template, (*seen, key))
        return key, fields, plugin, 'female' if struct.unpack_from('<I', config)[0] & 1 else 'male'

    def _races(self, key, seen=()):
        if key in seen or len(seen) >= 32: raise ValueError('Cyclic or excessive armor race chain.')
        fields, plugin = self._record(key, b'RACE', 'character race')
        parent = self._ref(fields, b'RNAM', plugin)
        return {key} | (self._races(parent, (*seen, key)) if parent and parent != key else set())

    def _parts(self, skin, sex, races):
        fields, plugin = self._record(skin, b'ARMO', 'character skin')
        addons = [self._reference(value, plugin) for value in fields.get(b'MODL', ()) if value]
        if not any(addons):
            raise ValueError('The character skin has no explicit armatures; its template needs separate inspection.')
        result = []
        for key in dict.fromkeys(addons):
            if not key: continue
            fields, provider = self._record(key, b'ARMA', 'skin armature')
            applicable = {self._ref(fields, b'RNAM', provider)}
            applicable.update(self._reference(value, provider) for value in fields.get(b'MODL', ()))
            if not races.intersection(applicable): continue
            config = self._one(fields, b'DNAM', b'')
            if len(config) != 12: raise ValueError('The skin armature weight settings are missing or malformed: '+key)
            body = self._one(fields, b'BOD2', self._one(fields, b'BODT', b''))
            if len(body) < 4: raise ValueError('The skin armature body parts are missing: '+key)
            mask = struct.unpack_from('<I', body)[0]
            models = set(); world_models = set(); first_person_models = set()
            for tag in ((b'MOD3', b'MOD5') if sex == 'female' else (b'MOD2', b'MOD4')):
                raw = self._one(fields, tag, b'').rstrip(b'\0')
                if not raw: continue
                path = relative_path(raw.decode('utf-8').replace('\\', '/')).casefold()
                if not path.startswith('meshes/'): path = 'meshes/'+path
                variants = {path}
                if config[3 if sex == 'female' else 2] & 2 and re.search(r'_[01]\.nif$', path):
                    variants.update((path[:-5]+'0.nif', path[:-5]+'1.nif'))
                models.update(variants)
                (world_models if tag in {b'MOD2', b'MOD3'} else first_person_models).update(variants)
            if not models: continue
            texture_set = self._ref(fields, b'NAM1' if sex == 'female' else b'NAM0', provider)
            textures = []
            if texture_set:
                texture_fields, _ = self._record(texture_set, b'TXST', 'skin texture set')
                for tag in (f'TX0{i}'.encode() for i in range(8)):
                    raw = self._one(texture_fields, tag, b'').rstrip(b'\0')
                    if not raw: continue
                    path = relative_path(raw.decode('utf-8').replace('\\', '/')).casefold()
                    textures.append(path if path.startswith('textures/') else 'textures/'+path)
            result.append(dict(armature=key, plugin=provider, slots=[i+30 for i in range(32) if mask & (1 << i)],
                priority=config[1 if sex == 'female' else 0],
                weight_slider=bool(config[3 if sex == 'female' else 2] & 2),
                models=sorted(models), world_models=sorted(world_models), first_person_models=sorted(first_person_models),
                texture_set=texture_set, textures=sorted(set(textures)),
                texture_swap=self._ref(fields, b'NAM3' if sex == 'female' else b'NAM2', provider)))
        occupied = set()
        for part in result:
            if not part['world_models']: continue
            if occupied.intersection(part['slots']):
                raise ValueError('This skin has overlapping body armatures. Their slot and priority rules need resolution before customization.')
            occupied.update(part['slots'])
        return result

    def character(self, key):
        actor, fields, plugin, sex = self._traits(key)
        race = self._ref(fields, b'RNAM', plugin)
        race_fields, race_plugin = self._record(race, b'RACE', 'character race')
        default_skin = self._ref(race_fields, b'WNAM', race_plugin)
        skin = self._ref(fields, b'WNAM', plugin) or default_skin
        races = self._races(race)
        parts = self._parts(skin, sex, races)
        body_models = {path for part in parts if 32 in part['slots'] for path in part['world_models']}
        if not body_models: raise ValueError('No applicable torso body was found for this character.')
        if skin == default_skin: scope = 'shared'
        elif default_skin:
            shared = {path for part in self._parts(default_skin, sex, races) if 32 in part['slots'] for path in part['world_models']}
            scope = 'shared' if body_models == shared else 'mixed' if body_models & shared else 'private'
        else: scope = 'private'
        return dict(traits_actor=actor, sex=sex, race=race, skin=skin,
            skin_plugin=self._record(skin, b'ARMO', 'character skin')[1], scope=scope,
            parts=parts, body_models=sorted(body_models),
            first_person_models=sorted({path for part in parts for path in part['first_person_models']}),
            textures=sorted({path for part in parts for path in part['textures']}))
