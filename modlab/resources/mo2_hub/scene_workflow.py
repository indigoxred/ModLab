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


def catalog(context, *, cache=None):
    from .archive_text import ArchiveReader
    from .scene_assets import Catalog
    cache = Path(cache) if cache is not None else Path(context['instance'])/'builds/scene-inputs'/uuid4().hex[:12]
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


def verify_saved(target, saved, context, effective, *, transformed=()):
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
        from tempfile import TemporaryDirectory
        # Inspection extractions are disposable. Only preparation caches belong
        # in retained build provenance, not every passive refresh.
        with TemporaryDirectory(prefix='modlab-scene-check-') as cache:
            active_assets = catalog(context, cache=cache)
            for name, previous_source in dependencies.items():
                current_source = active_assets.resolve(name)
                if current_source is None or current_source['sha256'] != previous_source['sha256']:
                    raise ValueError('A required scenery texture is missing or has a different active replacement: ' + name +
                                     '. Review Graphics choices before preparing again.')
    for name, checksum in saved['hashes'].items():
        # The caller has matched the current downstream build's receipt to this
        # exact source mesh, published output hash and effective provider.
        if name.casefold() in transformed:
            continue
        path = Path(effective[name]) if effective.get(name) else None
        if not path or not path.is_file() or path.resolve() != (Path(target)/name).resolve() or digest(path) != checksum:
            raise ValueError('Selected scenery was changed or overridden outside ModLab: ' + name +
                             '. Open Graphics choices to confirm the parts you want. The manual change was retained.')


def capture_inspection(organizer, *, graphics_findings=()):
    """Capture host state on the UI thread; no asset hashing or extraction."""
    saved, pending = load(organizer), load(organizer, 'pending')
    captured = dict(saved=saved, pending=pending, profile_path=organizer.profilePath())
    if not saved or pending:
        return captured
    import mobase
    from .pgpatcher_workflow import own_name as graphics_name
    target = Path(organizer.modsPath())/own_name(organizer)
    names = set(saved.get('hashes', {})) | set(saved.get('plan', {}).get('dependencies', {}))
    return dict(captured, target=str(target), active=bool(organizer.modList().state(target.name) & mobase.ModState.ACTIVE),
                context=snapshot(organizer) if saved.get('status') != 'reset' else {},
                effective={name: organizer.resolvePath(name) for name in names},
                graphics_target=str(Path(organizer.modsPath())/graphics_name(organizer)),
                graphics_ready=any(f.code == 'graphics-current' and f.level == 'Info' for f in graphics_findings)
                    and not any(f.code.startswith('graphics-') and not f.code.startswith('graphics-scene-')
                                and f.level != 'Info' for f in graphics_findings))


def inspect_scene(organizer, *, graphics_findings=()):
    """Synchronous check for preparation/launch gates; refresh uses a worker."""
    try:
        return evaluate_inspection(capture_inspection(organizer, graphics_findings=graphics_findings))
    except Exception as error:
        return stale_result(error)


def checking_result(organizer):
    from .assessment import Finding
    if load(organizer) or load(organizer, 'pending'):
        return (), (Finding('Unknown', 'graphics-scene-checking', 'Checking your scenery choices',
            'Checking current files and textures in the background.', 'You can continue browsing ModLab while this check finishes.'),)
    return (), ()


def stale_result(error):
    from .assessment import Finding
    return (), (Finding('Review', 'graphics-scene-stale', 'Your scenery choices need review', str(error),
        'Open Graphics to keep or change your component choices, then apply them. Existing files are retained.',
        'A saved choice no longer has a confirmed current result. ModLab has kept your changes for review.'),)


def evaluate_inspection(captured):
    """Report component results; never promote remembered intent to verified files.

    Host state was captured on the UI thread. This worker uses only paths and
    immutable data; the UI must reject its result if that captured state changed.
    """
    from .assessment import Finding
    from .outputs import read_manifest
    from .guidance import display_name
    try:
        pending = captured['pending']
        if pending:
            descriptions = '\n'.join(GROUPS[group] + ': ' + display_name(name) for group, name in pending['choices'].items())
            return (), (Finding('Review', 'graphics-scene-pending', 'Your scenery choices need preparation',
                pending.get('error', '') + '\n' + descriptions,
                'Open Graphics to apply these choices or cancel the pending request.'),)
        saved = captured['saved']
        if not saved:
            return (), ()
        if saved.get('profile_path') != captured['profile_path']:
            raise ValueError('The saved scenery belongs to another profile. Choose the scenery for this profile.')
        target, active = Path(captured['target']), captured['active']
        if saved.get('status') == 'reset':
            if active:
                raise ValueError('The previously cleared scenery output is enabled again. Choose whether to keep it in Graphics.')
            return (), (Finding('Info', 'graphics-scene-reset', 'Scenery follows your installed setup',
                'No saved component override is active. Source mods and the previous generated files are retained.',
                'Open Graphics to choose different roads, mountains or other scenery.'),)
        if saved.get('status') != 'Applied; file providers checked':
            raise ValueError('Your scenery selection has not finished applying. Open Graphics to finish it.')
        if not active:
            raise ValueError('Your scenery output is disabled. Open Graphics to confirm which parts you want; the manual change was retained.')
        context = captured['context']
        manifest = read_manifest(target, captured['profile_path'], 'Scene appearance')
        transformed = set()
        if captured['graphics_ready']:
            from .pgpatcher import transformed_body_files
            graphics_target = Path(captured['graphics_target'])
            if graphics_target.exists():
                transformed = transformed_body_files(target, manifest, graphics_target,
                    read_manifest(graphics_target, captured['profile_path'], 'Graphics'), lambda name: captured['effective'].get(name, ''))
        effective = captured['effective']
        verify_saved(target, saved, context, effective, transformed=transformed)
        from .outputs import digest
        for name, source in saved['plan']['dependencies'].items():
            winning = Path(effective[name]) if effective.get(name) else None
            # Packed dependencies were checked through the current archive catalog.
            # Any effective loose replacement, including Graphics, must also match.
            if winning and winning.is_file() and digest(winning) != source['sha256']:
                raise ValueError('A later output changed a required scenery texture: ' + name +
                                 '. Review Graphics before using this combination.')
        findings = []
        for group, provider in saved['choices'].items():
            names = [name for name, entry in saved['plan']['files'].items() if entry['group'] == group]
            patched = sum(name.casefold() in transformed for name in names)
            findings.append(Finding('Info', 'graphics-scene-current', GROUPS[group] + ': ' + display_name(provider),
                'Source mod: ' + provider + '\nBuild: ' + saved['directory'] + '\n' +
                str(len(names)) + ' selected files; ' + str(patched) + ' checked downstream transformations.',
                'Open Graphics to change this component. Other scenery choices stay yours.',
                'Applied from ' + display_name(provider) + '. The selected files, required textures and current replacements '
                'passed their checks. Source mods remain installed. Visual appearance is checked in game.'))
        return tuple(saved['hashes']), tuple(findings)
    except Exception as error:
        return stale_result(error)


def choice_summary(findings):
    """Brief result for Mods & choices, without sending users into diagnostics."""
    rows = [f for f in findings if f.code.startswith('graphics-scene-')]
    unresolved = [f for f in rows if f.level != 'Info']
    if unresolved:
        if unresolved[0].code == 'graphics-scene-checking':
            return 'Checking current scenery files and textures in the background…'
        return unresolved[0].title + '. Open Graphics to continue.'
    current = [f.title for f in rows if f.code == 'graphics-scene-current']
    if current:
        return 'Applied scenery choices:\n' + '\n'.join(current)
    if rows:
        return rows[0].title + '. Choose different components in Graphics.'
    return 'Choose roads, mountains, trees and other scenery from your installed mods. Renderer and mesh preparation remain available.'
