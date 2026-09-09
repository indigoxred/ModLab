"""Resolve shared scenery resources as user choices, not invented requirements."""
from hashlib import sha256
import json

from .scene_choices import GROUPS, category, provider_file, check_sources
from .npc_assets import texture_path


class SharedTextureChoices(ValueError):
    def __init__(self, conflicts):
        self.conflicts = conflicts
        providers = sorted({row['provider'] for item in conflicts for row in item['options']})
        parts = sorted({GROUPS.get(group, 'Other scenery') for item in conflicts for group in item['groups']})
        super().__init__(', '.join(parts) + ': ' + ', '.join(providers) + ' share textures across your scenery choices. '
            'Choose which appearance to keep for the affected parts; different files alone do not prove incompatibility.')


def group_conflicts(conflicts):
    grouped = {}
    for item in conflicts:
        key = (tuple(sorted(item['groups'])), tuple(sorted((row['provider'], row['kind']) for row in item['options'])))
        grouped.setdefault(key, dict(groups=list(key[0]), options=list(key[1]), textures=[]))['textures'].append(item)
    return list(grouped.values())


def choose_groups(groups, selections):
    if len(groups) != len(selections):
        raise ValueError('Choose an appearance for each shared group.')
    result = {}
    for group, choice in zip(groups, selections):
        if choice not in group['options']:
            raise ValueError('Choose an appearance for each shared group.')
        for item in group['textures']:
            option = next(row for row in item['options'] if (row['provider'], row['kind']) == choice)
            result[item['path']] = dict(provider=option['provider'], sha256=option['sha256'], evidence=item['evidence'])
    return result


def complete(plan, providers, references, resolve_texture, texture_choices=None):
    meshes = {name for name in plan['files'] if name.endswith('.nif')}
    if set(references) != meshes:
        raise ValueError('The selected scenery mesh inspection is incomplete.')
    uses = {}
    for mesh in sorted(meshes):
        for value in references[mesh]:
            name = texture_path(value)
            uses.setdefault(name, {})[mesh] = plan['files'][mesh]
    files, dependencies, decisions, conflicts, effects = dict(plan['files']), {}, {}, [], []
    for name, users in sorted(uses.items()):
        options = {}
        def add(source, kind, group):
            if source:
                key = source['provider'], source['sha256']
                options.setdefault(key, dict(source=source, kind=kind, group=group))
        explicit = plan['files'].get(name)
        if explicit: add(explicit, 'selected', explicit['group'])
        bundled = []
        missing = False
        for mesh_source in users.values():
            provider = providers[mesh_source['provider']]
            if name in provider['files'] or name in provider.get('packed', {}):
                source = provider_file(provider, name)
                bundled.append(source)
                add(source, 'bundled', mesh_source['group'])
            else:
                missing = True
        current = resolve_texture(name)
        groups = {row['group'] for row in users.values()}
        texture_group = category(name)
        if texture_group: groups.add(texture_group)
        # A texture supplied by an explicit component selection is authoritative
        # unless another selected package also supplies a competing appearance.
        # An incidental previous winner is not a mesh compatibility requirement.
        if explicit:
            conflict = any(row['sha256'] != explicit['sha256'] for row in bundled)
        elif bundled:
            hashes = {row['sha256'] for row in bundled}
            conflict = len(hashes) > 1
            crosses = texture_group is None or any(texture_group != row['group'] for row in users.values()) or missing
            if current and crosses and current['sha256'] not in hashes:
                conflict = True
                add(current, 'installed', next(iter(users.values()))['group'])
                if texture_group is None: groups.add('other')
        else:
            if current is None:
                raise ValueError(next(iter(users.values()))['provider'] + ' needs a missing texture: ' + name)
            dependencies[name] = current
            continue
        if conflict:
            rows = [dict(provider=value['source']['provider'], sha256=value['source']['sha256'],
                         kind=value['kind']) for key, value in sorted(options.items())]
            evidence = sha256(json.dumps(dict(name=name, options=rows, groups=sorted(groups),
                users={mesh: row['sha256'] for mesh, row in sorted(users.items())}), sort_keys=True).encode()).hexdigest()
            item = dict(path=name, groups=sorted(groups), options=rows, evidence=evidence)
            choice = (texture_choices or {}).get(name, {})
            key = choice.get('provider'), choice.get('sha256')
            if choice.get('evidence') != evidence or key not in options:
                conflicts.append(item)
                continue
            winner = options[key]
            decisions[name] = dict(provider=key[0], sha256=key[1], evidence=evidence)
            effects.append(dict(path=name, groups=sorted(groups), provider=key[0]))
        else:
            winner = next(iter(options.values()))
        if winner['kind'] == 'installed':
            files.pop(name, None)
            dependencies[name] = winner['source']
        else:
            files[name] = dict(winner['source'], group=winner['group'])
    if conflicts:
        raise SharedTextureChoices(conflicts)
    checked = dict(plan, files=files, dependencies=dependencies, texture_choices=decisions, shared_effects=effects)
    check_sources(checked)
    plan.update(checked)
    return plan
