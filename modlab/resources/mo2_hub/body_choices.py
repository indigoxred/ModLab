"""Translate BodySlide declarations into shared bodies and outfit decisions.

Preset metadata establishes build applicability only. It does not establish
runtime physics support or permission to replace a unique NPC's assets.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re

from .bodyslide import plan_build


def body_role(project):
    roles = set()
    for output in project.outputs:
        path = output.replace('\\', '/').casefold()
        match = re.fullmatch(r'meshes/actors/character/character assets/(1stperson)?(female|male)(body|hands|feet)(?:_[01])?\.nif', path)
        if not match:
            return None
        first, sex, part = match.groups()
        roles.add((sex, ('First-person ' if first else '') + part))
    return next(iter(roles)) if len(roles) == 1 else None


def shared_bodies(catalog, sex):
    return sorted((n for n, p in catalog.projects.items() if body_role(p) == (sex, 'body')), key=str.casefold)


def compatible_presets(catalog, name):
    result = []
    for preset in catalog.presets:
        try:
            plan_build(catalog, [name], preset)
            result.append(preset)
        except ValueError:
            pass
    return sorted(result, key=str.casefold)


def is_outfit(project, models):
    return bool(project.outputs) and all(p.replace('\\', '/').casefold() in models for p in project.outputs)


@dataclass
class ChoiceGroup:
    key: str
    label: str
    kind: str
    options: tuple
    suggested: str | None


def choice_groups(catalog, body, preset, saved=None, *, outfit_paths=()):
    plan_build(catalog, [body], preset)
    role = body_role(catalog.projects[body])
    if not role or role[1] != 'body':
        raise ValueError('Choose an installed shared body.')
    saved = saved or {}
    candidates = []
    for name, project in catalog.projects.items():
        part = body_role(project)
        kind = 'body part' if part and part[0] == role[0] and part[1] != 'body' else 'outfit' if is_outfit(project, outfit_paths) else None
        if not kind or name == body:
            continue
        try:
            plan_build(catalog, [name], preset)
        except ValueError:
            continue
        candidates.append((name, kind, {p.casefold() for p in project.outputs}))
    # Connected intersections also catch an outfit bundle overlapping individual pieces.
    clusters = []
    for name, kind, paths in candidates:
        overlaps = [g for g in clusters if g['paths'] & paths]
        cluster = dict(names=[name], kind=kind, paths=set(paths))
        for group in overlaps:
            cluster['names'].extend(group['names']); cluster['paths'].update(group['paths'])
            clusters.remove(group)
        clusters.append(cluster)
    result = []
    for group in clusters:
        names = tuple(sorted(group['names'], key=str.casefold))
        roles = {body_role(catalog.projects[n]) for n in names}
        if group['kind'] == 'body part' and len(roles) == 1:
            label = next(iter(roles))[1].capitalize()
        else:
            label = names[0] if len(names) == 1 else ' / '.join(names)
        previous = [n for n in names if n in saved]
        suggested = previous[0] if len(previous) == 1 else names[0] if len(names) == 1 else None
        key = hashlib.sha256('\n'.join(sorted(group['paths'])).encode()).hexdigest()[:16]
        result.append(ChoiceGroup(key, label, group['kind'], names, suggested))
    return sorted(result, key=lambda g: (g.kind == 'outfit', g.label.casefold()))


def select_projects(catalog, body, preset, decisions, *, outfits, saved=None, outfit_paths=()):
    selected = [body]
    for group in choice_groups(catalog, body, preset, saved, outfit_paths=outfit_paths):
        if group.kind == 'outfit' and not outfits:
            continue
        value = decisions.get(group.key, group.suggested)
        if value is None:
            raise ValueError('Choose a variant for ' + group.label + ', or keep its current files.')
        if value == '':
            continue
        if value not in group.options:
            raise ValueError('That body/outfit variant is no longer available. Review ' + group.label + '.')
        selected.append(value)
    plan_build(catalog, selected, preset)
    return selected


def outfit_batch(catalog, choices, group):
    """An explicit author-group choice, never a guess from project names."""
    members=catalog.groups.get(group,set()); result={}
    for choice in choices:
        if choice.kind!='outfit' or len(choice.options)<2: continue
        matches=set(choice.options)&members
        if len(matches)==1: result[choice.key]=next(iter(matches))
    return result


def outfit_batches(catalog, choices):
    return [(name,count) for name in sorted(catalog.groups,key=str.casefold)
            if (count:=len(outfit_batch(catalog,choices,name)))>1]


def defaults_path(profile):
    return Path(profile) / 'modlab-body-defaults.json'


def load_defaults(profile):
    path = defaults_path(profile)
    if not path.exists():
        return {}
    record = json.loads(path.read_text(encoding='utf-8'))
    if record.get('profile_path') != str(profile):
        raise ValueError('Saved body defaults belong to another profile.')
    return record['defaults']


def save_default(profile, sex, choice):
    if sex not in {'female', 'male'}:
        raise ValueError('Shared body defaults must be female or male; character exceptions are separate.')
    defaults = load_defaults(profile)
    defaults[sex] = choice
    path = defaults_path(profile)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(dict(profile_path=str(profile), defaults=defaults), indent=2), encoding='utf-8')
    os.replace(temporary, path)


def verified_default(request, record, directory, applied):
    """A failed new request must never borrow success from an older build."""
    if not request or not applied or record.get('effective_output_issues') != []:
        return False
    return (request.get('build_record') == str(Path(directory) / 'operation.json')
            and request.get('build_preset', request.get('preset')) == record.get('preset')
            and set(request.get('selected', ())) == set(record.get('projects', ()))
            and request.get('body') in record.get('projects', ())
            and bool(record.get('hashes')))


def selected_morph_mode(selected, saved, choice):
    """Preserve the selected items' mode; Advanced's current checkbox is irrelevant."""
    if choice is not None:
        return bool(choice)
    modes = {bool(saved[name].get('morphs', False)) for name in selected if name in saved}
    if len(modes) > 1:
        raise ValueError('These selected bodies/outfits have different in-game shape settings. '
            'Choose Include body shape data or Static body only for this build. Current files are unchanged.')
    return next(iter(modes), False)
