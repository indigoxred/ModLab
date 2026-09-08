"""Plan an explicit shared shape while preserving static private NPC bodies.

OBody's documented race rules apply the chosen default to the player as well
as NPCs; actor rules can then override it. Random preset exclusions do not
disable explicit rules (author configuration guide, Nexus article 4756).
This only plans configuration. Callers must verify the neutral build and
publish both outputs before reporting preparation complete.
"""
import json
from .obody_config import PRESET_MAPS, plan_config
from .outputs import digest
from .vfs import readable_path


def _paths(values):
    return {value.replace('\\', '/').casefold() for value in values}


def plan(source, characters, sex, preset, body_models, built_models,
         available_presets, resolve):
    return plan_many(source, characters, {sex: dict(preset=preset,
        body_models=body_models, built_models=built_models)}, available_presets, resolve)


def plan_many(source, characters, defaults, available_presets, resolve):
    # Validate before touching any rules, including duplicate keys and unknown
    # extensions. Existing custom distribution is a decision, not permission
    # to silently replace it with a newly installed body preset.
    checked = plan_config(source, {}, {}, {}, preserve_actor_ids=True)
    data = json.loads(checked.text)
    if any(data[key] for key in (*PRESET_MAPS, 'npcFormID')):
        raise ValueError('Review the existing OBody distribution before replacing it with shared defaults. '
                         'Its current assignments have been retained.')
    available = set(available_presets)
    if not defaults or set(defaults) - {'female', 'male'}:
        raise ValueError('Choose a female or male shared shape before preparation.')
    inputs = {}; retained = set(); races = set(); targets = {}
    for sex, choice in defaults.items():
        preset = choice['preset']
        body_models, built_models = _paths(choice['body_models']), _paths(choice['built_models'])
        if not preset or preset not in available or not body_models or not body_models.issubset(built_models):
            raise ValueError('The selected shared shape or prepared body is unavailable.')
        applicable = []
        for key, actor in characters.items():
            body = actor.get('body') or {}
            if body.get('sex') != sex or not body_models.intersection(_paths(body.get('race_body_models', ()))):
                continue
            race = body.get('race_editor')
            if not isinstance(race, str) or not race.strip():
                raise ValueError(actor['name'] + ' has no inspected race identity for shared shape assignment.')
            # A private static model is unaffected by OBody. A private model
            # carrying BODYTRI is not proven neutral just because shared models
            # were built. Do not confuse a shared first-person model with it.
            models = _paths(p for part in body.get('parts', ()) for p in part['models'])
            for relative in sorted(models - built_models):
                if relative in inputs:
                    if body.get('scope') in {'private', 'mixed'}: retained.add(actor['name'])
                    continue
                resolved = resolve(relative)
                path = readable_path(resolved) if resolved else None
                if not path or not path.is_file() or not 40 <= path.stat().st_size <= 128 * 1024 * 1024:
                    raise ValueError(actor['name'] + ' uses a separate body that could not be inspected. '
                                     'Its files are retained; review its body choice before preparation.')
                raw = path.read_bytes()
                if not raw.startswith((b'Gamebryo File Format, Version ', b'NetImmerse File Format, Version ')):
                    raise ValueError(actor['name'] + ' has an unreadable separate body model.')
                if b'BODYTRI' in raw:
                    raise ValueError(actor['name'] + ' uses a separate body with its own shape data. '
                                     'Choose a compatible shared body or prepare that separate body first.')
                inputs[relative] = dict(path=str(path), sha256=digest(path))
                if body.get('scope') in {'private', 'mixed'}: retained.add(actor['name'])
            applicable.append((key, actor, race))
        if not applicable:
            raise ValueError('No inspected characters use this shared body; its shape assignments cannot be inferred.')
        suffix = sex.title()
        # Exclusions outrank assignments. Do not make a neutral mesh effective
        # while knowingly leaving a user-excluded character without its shape.
        if (data['blacklistedNpcs'] or data['blacklistedNpcsFormID'] or
                data['blacklistedNpcsPlugin' + suffix]):
            raise ValueError('Review existing OBody character exclusions before preparing shared defaults. '
                             'The exclusions and current bodies are retained.')
        sex_races = sorted({race for _, _, race in applicable})
        data['race' + suffix].update({race: [choice['preset']] for race in sex_races})
        data['blacklistedRaces' + suffix] = [race for race in data['blacklistedRaces' + suffix]
                                             if race not in sex_races]
        races.update(sex_races); targets[sex] = sex_races
    data['blacklistedPresetsFromRandomDistribution'] = sorted(
        set(data['blacklistedPresetsFromRandomDistribution']) | available, key=str.casefold)
    return dict(text=json.dumps(data, ensure_ascii=False, indent=2) + '\n',
                inputs=inputs, races=sorted(races), targets=targets,
                retained_private=sorted(retained), preset_inventory=sorted(available))
