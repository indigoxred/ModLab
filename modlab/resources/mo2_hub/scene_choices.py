"""Select parts of installed scenery without reordering entire source mods."""
from pathlib import Path
import re

from .bodyslide import relative_path
from .installation import _plain
from .outputs import digest, MANIFEST
from .vfs import readable_path

GROUPS = {
    'roads': 'Roads and bridges',
    'mountains': 'Mountains and rocks',
    'trees': 'Trees',
    'grass': 'Grass',
    'plants': 'Other plants',
    'buildings': 'Buildings and ruins',
    'objects': 'Objects and furniture',
    'landscape': 'Ground surfaces',
}


def category(name):
    name = relative_path(name).replace('\\', '/').casefold()
    if not name.endswith(('.nif', '.dds')) or not name.startswith(('meshes/', 'textures/')):
        return None
    local = name.split('/', 1)[1]
    local = re.sub(r'^dlc0[12]/', '', local)
    if local.startswith(('actors/', 'terrain/', 'lod/')) or '/lod/' in local:
        return None
    if ('roads' in local.split('/')[:-1] or local.startswith('landscape/bridges/') or
            local.startswith(('architecture/solitude/sbridge', 'dungeons/nordic/exterior/dragonbridge',
                              'clutter/blackreachroad', 'landscape/dlc2road'))):
        return 'roads'
    if local.startswith(('landscape/mountains/', 'landscape/rocks/')):
        return 'mountains'
    if local.startswith(('landscape/trees/', 'trees/')):
        return 'trees'
    if local.startswith(('landscape/grass/', 'grass/')):
        return 'grass'
    if local.startswith(('plants/', 'landscape/plants/')):
        return 'plants'
    if local.startswith(('architecture/', 'dungeons/')):
        return 'buildings'
    if local.startswith(('clutter/', 'furniture/')):
        return 'objects'
    # Terrain-generated LOD was excluded above. This covers ordinary ground maps.
    if local.startswith('landscape/') and name.endswith('.dds'):
        return 'landscape'
    return None


def scan_provider(name, root):
    root = Path(root)
    _plain(root)
    if (root/MANIFEST).exists():
        raise ValueError('Generated output is not a source appearance: ' + name)
    files = {}
    for part in ('meshes', 'textures'):
        folder = readable_path(root/part)
        if not folder.is_dir():
            continue
        for path in folder.rglob('*'):
            if not path.is_file() or path.suffix.casefold() not in {'.nif', '.dds'}:
                continue
            relative = relative_path(path.relative_to(readable_path(root)).as_posix()).casefold()
            if relative in files:
                raise ValueError('Two files use the same game path in ' + name + ': ' + relative)
            files[relative] = str(path)
    groups = {key: tuple(n for n in files if category(n) == key) for key in GROUPS}
    return dict(name=name, root=str(root), files=files,
                groups={key: names for key, names in groups.items() if names},
                archives=[str(p) for p in root.glob('*.bsa')])


def file_source(path, provider):
    path = Path(path)
    _plain(path)
    if not path.is_file():
        raise ValueError('A selected scenery source is missing: ' + provider)
    return dict(source=str(path), provider=provider, size=path.stat().st_size, sha256=digest(path))


def provider_file(provider, name):
    if name in provider.get('packed', {}) and name not in provider['files']:
        return provider['packed_source'](name)
    path = Path(provider['files'][name])
    root = readable_path(provider['root']).resolve()
    if not path.resolve().is_relative_to(root):
        raise ValueError('The selected scenery source is outside its mod: ' + provider['name'])
    for part in (path, *path.parents):
        _plain(part)
        if part.resolve() == root:
            break
    return file_source(path, provider['name'])


def plan_choices(providers, choices):
    files, groups = {}, {}
    for group, selected in choices.items():
        if group not in GROUPS:
            raise ValueError('This scenery group is not supported: ' + str(group))
        provider = providers.get(selected)
        if not provider or group not in provider['groups']:
            raise ValueError(GROUPS[group] + ': the selected appearance is no longer available: ' + str(selected))
        groups[group] = dict(provider=selected, paths=list(provider['groups'][group]))
        for name in provider['groups'][group]:
            files[name] = dict(provider_file(provider, name), group=group)
    return dict(choices=dict(choices), groups=groups, files=files, dependencies={})


def complete_textures(plan, providers, references, resolve_texture, *, texture_choices=None):
    from .scene_shared import complete
    return complete(plan, providers, references, resolve_texture, texture_choices)

def check_sources(plan, *, check_containers=True):
    containers = {}
    for name, entry in {**plan['dependencies'], **plan['files']}.items():
        source = Path(entry['source'])
        _plain(source)
        if not source.is_file() or source.stat().st_size != entry['size'] or digest(source) != entry['sha256']:
            raise ValueError(entry['provider'] + ': a scenery source changed; keep your choice and prepare again: ' + name)
        if check_containers:
            containers.update(entry.get('containers', {}))
    for path, checksum in containers.items():
        archive = Path(path); _plain(archive)
        if not archive.is_file() or digest(archive) != checksum:
            raise ValueError('A scenery source archive changed; prepare the choices again: ' + path)
