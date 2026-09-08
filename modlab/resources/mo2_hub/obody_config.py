"""Prepare explicit actor rules for an existing OBody NG configuration.

This is a configuration transform, not evidence that body meshes or runtime
assignments are ready. Callers supply the inspected character-specific preset
choices and retain the original upstream configuration for removing a choice.
It never creates a new distribution policy or changes other actors' rules.

Contract: OBody NG 4.4.3 schema and author's articles/4756 priority rules.
"""
from dataclasses import dataclass
import hashlib
import json
import re


PRESET_MAPS = ('npc', 'factionFemale', 'factionMale', 'npcPluginFemale',
               'npcPluginMale', 'raceFemale', 'raceMale')
FORM_LISTS = ('blacklistedNpcsFormID', 'blacklistedOutfitsFromORefitFormID',
              'outfitsForceRefitFormID')
STRING_LISTS = ('blacklistedNpcs', 'blacklistedNpcsPluginFemale',
    'blacklistedNpcsPluginMale', 'blacklistedRacesFemale', 'blacklistedRacesMale',
    'blacklistedOutfitsFromORefit', 'blacklistedOutfitsFromORefitPlugin',
    'outfitsForceRefit', 'blacklistedPresetsFromRandomDistribution')
REQUIRED = {*PRESET_MAPS, *FORM_LISTS, *STRING_LISTS,
            'npcFormID', 'blacklistedPresetsShowInOBodyMenu'}


@dataclass(frozen=True)
class ConfigPlan:
    text: str
    source_sha256: str
    sha256: str
    replaced: dict


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate configuration key: ' + key)
        result[key] = value
    return result


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and '\0' not in value


def _strings(value):
    return isinstance(value, list) and all(_text(v) for v in value)


def _plugin(value):
    if (not _text(value) or value != value.strip() or
            re.search(r'[<>:"/\\|?*\x00-\x1f]', value) or
            not value.casefold().endswith(('.esp', '.esm', '.esl'))):
        raise ValueError('Invalid owning plugin in character shape configuration.')
    return value.casefold()


def _form(value, light=False):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-fA-F]{3,8}', value):
        raise ValueError('Invalid actor base identity in character shape configuration.')
    if len(value) == 8 and value[:2].casefold() == 'fe' and not light:
        raise ValueError('Confirm light-plugin metadata before matching a compact actor identity.')
    # OBody accepts both local IDs and displayed load-order IDs. Never emit a
    # current load index; normalize existing aliases using the owning plugin.
    return int(value, 16) & (0xfff if light else 0xffffff)


def _plugins(mapping):
    if not isinstance(mapping, dict):
        raise ValueError('Invalid plugin rules in character shape configuration.')
    seen = set()
    for name in mapping:
        key = _plugin(name)
        if key in seen:
            raise ValueError('Plugin rule spelling is ambiguous: ' + name)
        seen.add(key)


def _validate(data, lights, preserve_actor_ids=False):
    def identity_form(form, plugin):
        if preserve_actor_ids:
            if not isinstance(form,str) or not re.fullmatch(r'[0-9a-fA-F]{3,8}',form):
                raise ValueError('Invalid actor base identity in character shape configuration.')
            return form.casefold()
        return _form(form, plugin.casefold() in lights or plugin.casefold().endswith('.esl'))

    if not isinstance(data, dict) or REQUIRED - data.keys():
        raise ValueError('OBody configuration is incomplete. Restore its matching configuration before preparing shapes.')
    for key in PRESET_MAPS:
        mapping = data[key]
        if not isinstance(mapping, dict) or any(not _text(k) or not _strings(v) for k, v in mapping.items()):
            raise ValueError('Invalid preset rules in OBody configuration: ' + key)
    for key in STRING_LISTS:
        if not _strings(data[key]):
            raise ValueError('Invalid list in OBody configuration: ' + key)
    if type(data['blacklistedPresetsShowInOBodyMenu']) is not bool:
        raise ValueError('Invalid menu setting in OBody configuration.')
    index = {}
    _plugins(data['npcFormID'])
    for plugin, rules in data['npcFormID'].items():
        if not isinstance(rules, dict):
            raise ValueError('Invalid character rules for ' + plugin)
        for form, presets in rules.items():
            identity = (_plugin(plugin), identity_form(form, plugin))
            if identity in index:
                raise ValueError('Actor rule aliases are ambiguous: ' + plugin + ' / ' + form)
            if not _strings(presets):
                raise ValueError('Invalid character preset list for ' + plugin)
            index[identity] = (plugin, form)
    exclusions = set()
    for key in FORM_LISTS:
        _plugins(data[key])
        for plugin, forms in data[key].items():
            if not isinstance(forms, list):
                raise ValueError('Invalid FormID list in ' + key)
            for form in forms:
                identity = (_plugin(plugin), identity_form(form, plugin))
                if key == 'blacklistedNpcsFormID':
                    exclusions.add(identity)
    return index, exclusions


def _unique_actor_keys(mapping):
    result = {}
    for key, value in mapping.items():
        if not isinstance(key, str):
            raise ValueError('Invalid inspected character identity.')
        normalized = key.casefold()
        if normalized in result:
            raise ValueError('Inspected character identities are ambiguous: ' + key)
        result[normalized] = value
    return result


def plan_config(source, choices, characters, available_presets, *, light_plugins=(), expected_source=None, preserve_actor_ids=False):
    """Build one deterministic rule per selected NPC; leave upstream data intact.

`choices` is the full current selection, keyed by local BaseID:owningPlugin.
`source` must be the retained upstream config, not the last generated overlay.
Use expected_source when rebuilding to detect outside edits before replaying
saved intent. Output publication must separately verify the effective provider.
Empty choices reproduce upstream configuration without ModLab's assignments.
Existing-save actors may still require OBody's actor reset after publication.
"""
    if source is None:
        raise ValueError('Set up character body customization first; OBody configuration is not available.')
    if not isinstance(source, str) or len(source.encode('utf-8')) > 8 * 1024 * 1024:
        raise ValueError('Invalid or oversized OBody configuration.')
    checksum = hashlib.sha256(source.encode('utf-8')).hexdigest()
    if expected_source is not None and checksum != expected_source:
        raise ValueError('Character shape configuration changed outside ModLab. Reconcile those changes before preparing saved choices.')
    def invalid_constant(value):
        raise ValueError('Invalid JSON number in OBody configuration: ' + value)
    try:
        data = json.loads(source.lstrip('\ufeff'), object_pairs_hook=_pairs, parse_constant=invalid_constant)
    except json.JSONDecodeError as error:
        raise ValueError('OBody configuration is not valid JSON: ' + str(error)) from error
    if preserve_actor_ids and choices:
        raise ValueError('Actor assignment requires resolved identities; preserve-only validation cannot assign characters.')
    lights = {_plugin(name) for name in light_plugins}
    index, exclusions = _validate(data, lights, preserve_actor_ids)
    replaced, seen = {}, set()
    directory = _unique_actor_keys(characters)
    available = _unique_actor_keys(available_presets)
    for key, preset in choices.items():
        if not isinstance(key, str) or ':' not in key:
            raise ValueError('A selected character identity is not available.')
        form, plugin = key.split(':', 1)
        if not re.fullmatch(r'[0-9a-fA-F]{6}', form):
            raise ValueError('Selected characters must use local base identities.')
        owner = _plugin(plugin)
        number = int(form, 16)
        if not number or ((owner in lights or owner.endswith('.esl')) and number > 0xfff):
            raise ValueError('Selected characters must use local base identities.')
        identity = (owner, number)
        if identity in seen:
            raise ValueError('Selected actor aliases are ambiguous: ' + key)
        seen.add(identity)
        character = directory.get(key.casefold())
        presets = available.get(key.casefold(), ())
        if character is None or isinstance(presets, str) or not _text(preset) or preset not in presets:
            raise ValueError('The selected character or matching shape preset is no longer available: ' + key)
        name = character['name']
        if identity in exclusions or name.casefold() in {n.casefold() for n in data['blacklistedNpcs']}:
            raise ValueError(name + ' is excluded from automatic body customization. Review that existing choice before assigning a shape.')
        canonical = f'{number:06X}:' + owner
        if identity in index:
            existing_plugin, existing_form = index[identity]
            replaced[canonical] = data['npcFormID'][existing_plugin][existing_form]
        else:
            existing_plugin = next((p for p in data['npcFormID'] if p.casefold() == owner), owner)
            existing_form = f'{number:06X}'
            replaced[canonical] = None
        data['npcFormID'].setdefault(existing_plugin, {})[existing_form] = [preset]
    text = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    return ConfigPlan(text, checksum, hashlib.sha256(text.encode('utf-8')).hexdigest(), replaced)
