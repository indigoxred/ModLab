"""Profile-scoped scenery requests and snapshots for background preparation."""
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from .installation import write_record
from .outputs import output_name
from .scene_choices import GROUPS


def own_name(organizer):
    return output_name(organizer.profile().name(), organizer.profilePath(), 'Scene appearance')


def path_for(organizer, kind='choices'):
    return Path(organizer.profilePath())/('modlab-scene-' + kind + '.json')


def load(organizer, kind='choices'):
    path = path_for(organizer, kind)
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None


def inspect_request(organizer):
    from .assessment import Finding
    request = load(organizer, 'pending')
    if not request: return ()
    descriptions = '\n'.join(GROUPS[group] + ': ' + name for group, name in request['choices'].items())
    return (Finding('Review', 'graphics-scene-pending', 'Your scenery choices need preparation',
                    request.get('error', '') + '\n' + descriptions,
                    'Open Graphics choices to review and apply these selections or cancel the pending request.',
                    'The requested scenery has not been confirmed as applied. Your previous output and source mods are retained.'),)


def begin_pending(organizer, choices, *, expected):
    if load(organizer, 'pending') != expected:
        raise ValueError('Scenery choices changed elsewhere; reopen the panel to retain the newer request.')
    if any(key not in GROUPS or not isinstance(value, str) or not value for key, value in choices.items()):
        raise ValueError('Select an installed provider for each chosen scenery component.')
    record = dict(id=uuid4().hex, profile_path=organizer.profilePath(), choices=dict(choices),
                  created_at=datetime.now(timezone.utc).isoformat(), status='Requested')
    write_record(path_for(organizer, 'pending'), record)
    return record


def check_request(organizer, expected):
    if organizer.profilePath() != expected['profile_path']:
        raise ValueError('The selected profile changed; reopen Graphics choices.')
    if load(organizer, 'pending') != expected:
        raise ValueError('Scenery choices changed during preparation; reopen Graphics choices.')


def check_snapshot(expected, current):
    if json.loads(json.dumps(expected)) != json.loads(json.dumps(current)):
        raise ValueError('The active mods, profile or archive loading order changed. Prepare the scenery choices again.')


def check_archive_inis(paths):
    import configparser
    for path in paths:
        path = Path(path)
        if not path.is_file(): continue
        ini = configparser.ConfigParser(interpolation=None, strict=False)
        ini.read(path, encoding='utf-8-sig')
        if any(key.casefold().startswith('sresourcearchive') for section in ini.sections()
               if section.casefold() == 'archive' for key in ini[section]):
            raise ValueError('A custom archive-loading configuration needs review before selecting packed scenery: ' + str(path))


def snapshot(organizer):
    """MO2 calls stay on its UI thread; filesystem scans happen in the worker."""
    import mobase
    from PyQt6.QtCore import QCoreApplication
    from .archives import active_archive_paths, archive_rank
    from .assessment import Plugin
    from . import pgpatcher_workflow as graphics
    mods, plugins = organizer.modList(), organizer.pluginList()
    excluded = {own_name(organizer), graphics.own_name(organizer)}
    roots = [('Game Data', str(Path(organizer.managedGame().gameDirectory().absolutePath())/'Data'))]
    roots.extend((name, mods.getMod(name).absolutePath()) for name in sorted(mods.allMods(), key=mods.priority)
                 if name not in excluded and mods.state(name) & mobase.ModState.ACTIVE and mods.getMod(name) is not None)
    roots.append(('Overwrite', organizer.overwritePath()))
    feature = organizer.gameFeatures().gameFeature(mobase.DataArchives)
    if feature is None: raise ValueError('The game archive configuration is unavailable.')
    registered = list(feature.archives(organizer.profile()))
    loaded = [Plugin(name, plugins.loadOrder(name), (), plugins.origin(name)) for name in plugins.pluginNames()]
    check_archive_inis([Path(organizer.profile().absoluteIniFilePath('SkyrimCustom.ini'))] +
                       [Path(organizer.resolvePath(Path(p.name).stem + '.ini')) for p in loaded if p.load_order >= 0
                        and organizer.resolvePath(Path(p.name).stem + '.ini')])
    archives = []
    for name, path in active_archive_paths(organizer).items():
        owners = [owner for owner, root in roots if Path(path).parent.resolve() == Path(root).resolve()]
        if len(owners) != 1:
            raise ValueError('The active scenery archive has no unique source mod: ' + name)
        archives.append(dict(path=path, provider=owners[0], rank=list(archive_rank(name, loaded, registered))))
    return dict(profile_path=organizer.profilePath(), instance=str(Path(organizer.modsPath()).parent),
                roots=roots, archives=sorted(archives, key=lambda row: row['path'].casefold()),
                renderer=(graphics.load_pending(organizer) or graphics.load_choices(organizer) or {}).get('choices', {}),
                custom_renderer=bool(organizer.resolvePath('SKSE/Plugins/CommunityShaders.dll')) or any(
                    (Path(organizer.managedGame().gameDirectory().absolutePath())/name).is_file()
                    for name in ('d3d11.dll', 'enbseries.ini')),
                library=str(Path(QCoreApplication.applicationDirPath())/'dlls/libbsarch.dll'))


def catalog(context):
    from .archive_text import ArchiveReader
    from .scene_assets import Catalog
    cache = Path(context['instance'])/'builds/scene-inputs'/uuid4().hex[:12]
    return Catalog(context['roots'], context['archives'], cache, ArchiveReader(context['library']))


def prepare(context, request):
    from . import scene_build
    from .npc_assets import inspect_textures
    from .runtime import ensure_sdk10
    assets = catalog(context)
    tools = Path(context['instance'])/'tools'
    inactive_references = {}
    def inspect(meshes, directory):
        if not meshes: return {}
        sdk = ensure_sdk10(tools)
        found = inspect_textures(sdk, tools, list(meshes.values()), directory)
        rendering = context.get('renderer', {})
        if (rendering.get('renderer') == 'Mesh fixes only' and not rendering.get('pbr')
                and not rendering.get('upgrade_complex') and not context.get('custom_renderer', True)):
            from .skin_assets import inspect as inspect_shapes
            from .scene_textures import required_textures
            shapes = inspect_shapes(sdk, tools, list(meshes.values()), directory/'shader-slots')
            for name, path in meshes.items():
                found[path], ignored = required_textures(found[path], shapes[path])
                if ignored: inactive_references[name] = ignored
        return {name: found[path] for name, path in meshes.items()}
    job = scene_build.prepare(context['instance'], context['profile_path'], assets.providers,
                              request['choices'], inspect, assets.resolve)
    job.update(context=context, request=request, inactive_vanilla_references=inactive_references)
    scene_build.save(job)
    return job


def verify_saved(target, saved, context, effective):
    from .outputs import read_manifest, digest
    from .scene_choices import check_sources
    roots, previous = dict(context['roots']), dict(saved['context']['roots'])
    if saved.get('inactive_vanilla_references') and any(context.get(key) != saved['context'].get(key)
            for key in ('renderer', 'custom_renderer')):
        raise ValueError('The rendering setup changed; prepare scenery textures again for that renderer.')
    for name in saved['choices'].values():
        if name not in roots or Path(roots[name]).resolve() != Path(previous[name]).resolve():
            raise ValueError('A chosen scenery source is disabled, missing or moved: ' + name +
                             '. Choose its replacement in Graphics choices.')
    manifest = read_manifest(Path(target), saved['profile_path'], 'Scene appearance')
    if manifest['hashes'] != saved['hashes']:
        raise ValueError('Scenery output no longer matches the saved choices; review Graphics choices.')
    check_sources(saved['plan'])
    dependencies = saved['plan']['dependencies']
    if dependencies:
        active_assets = catalog(context)
        for name, previous_source in dependencies.items():
            current_source = active_assets.resolve(name)
            if current_source is None or current_source['sha256'] != previous_source['sha256']:
                raise ValueError('A required scenery texture is missing or has a different active replacement: ' + name +
                                 '. Review Graphics choices before preparing again.')
    for name, checksum in saved['hashes'].items():
        path = Path(effective[name]) if effective.get(name) else None
        if not path or not path.is_file() or path.resolve() != (Path(target)/name).resolve() or digest(path) != checksum:
            raise ValueError('Selected scenery was changed or overridden outside ModLab: ' + name +
                             '. Open Graphics choices to confirm the parts you want. The manual change was retained.')
