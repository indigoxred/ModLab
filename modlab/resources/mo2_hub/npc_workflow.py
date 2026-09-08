"""Per-NPC appearance choices, executed with paired assets through MO2."""
from collections import defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
from uuid import uuid4

from .npc import npc_records, facegen_paths, validate_selections, merge_assets
from .npc_helper import ensure_helper, REVISION
from .outputs import digest, output_name, publish_output, read_manifest
from .synthesis import command_line, plugin_masters
from .synthesis_workflow import input_state, SynthesisJob, settings_hashes, working_environment
from .pandora_workflow import effective_issues, activate_generated_plugins
from .vfs import readable_path
from .body_inputs import mesh_helper_state

OUTPUT = 'ModLab NPC Appearance.esp'


def own_name(organizer):
    return output_name(organizer.profile().name(), organizer.profilePath(), 'NPC Appearance')


def path_for(organizer, kind='choices'):
    return Path(organizer.profilePath()) / ('modlab-npc-' + kind + '.json')


def load(organizer, kind='choices'):
    path = path_for(organizer, kind)
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None


def check_pending(organizer, expected):
    if load(organizer,'pending')!=expected:
        raise ValueError('Character choices changed elsewhere. Reopen the character panel to use the newer choices.')


def state(organizer):
    excluded = [output_name(organizer.profile().name(), organizer.profilePath(), tool) for tool in ('Synthesis', 'Graphics')]
    return mesh_helper_state(input_state(organizer, [OUTPUT], output_mod=own_name(organizer), excluded_outputs=excluded))


def catalog(organizer):
    from .archives import archive_reader
    import mobase
    plugins, mods = organizer.pluginList(), organizer.modList()
    reader, roots, rows = None, {}, {}
    excluded = {own_name(organizer), *(output_name(organizer.profile().name(), organizer.profilePath(), t) for t in ('Synthesis', 'Graphics'))}
    for name in sorted(plugins.pluginNames(), key=plugins.priority):
        if plugins.loadOrder(name) < 0 or plugins.origin(name) in excluded: continue
        mod = mods.getMod(plugins.origin(name))
        if mod is None or not mods.state(mod.name()) & mobase.ModState.ACTIVE: continue
        root = Path(mod.absolutePath())
        cache_key = (root, name.casefold())
        if cache_key not in roots:
            assets = {p.relative_to(readable_path(root)).as_posix().casefold() for p in readable_path(root).rglob('*') if p.is_file()}
            for archive in root.glob('*.bsa'):
                # The helper supports archives matching the provider's plugin name.
                if archive.stem.casefold() not in {Path(name).stem.casefold(), Path(name).stem.casefold() + ' - textures'}: continue
                if reader is None: reader = archive_reader()
                assets.update(reader.entries(archive))
            roots[cache_key] = assets
        assets = roots[cache_key]
        if not any('/facegendata/facegeom/' in p for p in assets): continue
        source = Path(organizer.resolvePath(name))
        if source.resolve().parent != root.resolve():
            raise ValueError('The effective NPC plugin does not match its mod folder: ' + name)
        for key, label in npc_records(source).items():
            pair = facegen_paths(key)
            present = [p.casefold() in assets for p in pair]
            if not any(present): continue  # Gameplay-only NPC overrides are not appearance choices.
            entry = rows.setdefault(key, {'label': label, 'options': {}})
            entry['options'][name] = dict(root=str(root), mod=mod.name(), ready=all(present),
                problem=f'{label}: {name} is missing its paired FaceGen mesh or face tint. Reinstall its matching files before choosing it.')
    return rows


def prepare_job(organizer, selected, body_choices=None, body_labels=None):
    from .skse import require_game_closed
    require_game_closed()
    from .pgpatcher_workflow import require_upstream_view
    require_upstream_view(organizer)
    choices = catalog(organizer)
    saved = load(organizer)
    body_choices=dict(body_choices if body_choices is not None else (saved or {}).get('body_choices',{}))
    if selected: validate_selections(choices, selected)
    elif not body_choices: raise ValueError('Choose a character appearance or body before preparing.')
    instance = Path(organizer.modsPath()).parent
    current = state(organizer)
    body_plans={}
    if body_choices:
        from .body_assignment import prepare
        from .npc_body_helper import ensure_helper as ensure_body_helper
        body_plans=prepare(organizer,current,selected,body_choices)
        sdk,binary,data=ensure_body_helper(instance/'tools')
    else: sdk, binary, data = ensure_helper(instance / 'tools')
    target = Path(organizer.modsPath()) / own_name(organizer)
    if (target / '.modlab-output.json').is_file() and not saved:
        raise ValueError('Existing NPC output needs its retained choices. Restore those before rebuilding.')
    directory = instance / 'builds/npc' / uuid4().hex[:12]
    directory.mkdir(parents=True)
    (directory / 'output').mkdir()
    (directory / 'plugins.txt').write_text(''.join('*' + p + '\n' for p in current['order']), encoding='utf-8')
    grouped = defaultdict(list)
    for key, plugin in selected.items(): grouped[plugin].append(key)
    settings = dict(Mode='Deep', MO2DataPath=organizer.modsPath(), ClearAssetOutputDirectory=False,
        ForwardConflictWinnerData=True, ForwardConflictWinnerOutifts=True, CopyExtraAssets=False,
        # Original mods stay installed. Retain references to their existing records
        # instead of cloning head parts into new, potentially unstable FormIDs.
        PluginsExcludedFromMerge=current['order'],
        HandleBSAFiles_Patching=True, AbortIfMissingFaceGen=True, AbortIfMissingExtraAssets=False,
        GetMissingExtraAssetsFromAvailableWinners=True, SuppressAllMissingFileWarnings=False,
        SuppressKnownMissingFileWarnings=False,
        PluginsToForward=[dict(Plugin=p, NPCs=keys, ForcedAssetDirectory=choices[keys[0]]['options'][p]['root'],
                              SelectAll=False, InvertSelection=False, AddToMergeJSON=False, FindExtraTexturesInNifs=True)
                          for p, keys in sorted(grouped.items())])
    if body_plans:
        settings.update(ModLabBodyAssignments={k:p['skin'] for k,p in body_plans.items()},
            ModLabBodyModelSources={k:p['model_sources'] for k,p in body_plans.items()},
            ModLabBodySex={k:p['sex'] for k,p in body_plans.items()})
    labels=dict((saved or {}).get('labels',{}));labels.update(body_labels or {})
    labels.update({k:choices[k]['label'] for k in selected})
    for key in body_choices: labels.setdefault(key,key)
    job = SynthesisJob(directory, binary, settings, dict(profile=organizer.profile().name(),
        profile_path=organizer.profilePath(), selected=selected, labels=labels,
        body_choices=body_choices,body_plans=body_plans,pending_request=load(organizer,'pending'),
        input_state=current, sdk=str(sdk), defaults=str(data), helper_sha256=digest(binary), revision=REVISION,
        status='Prepared', created_at=datetime.now(timezone.utc).isoformat()))
    job.save()
    return job


def check_context(organizer, job):
    if 'pending_request' in job.record: check_pending(organizer,job.record['pending_request'])
    from .body_assignment import check_inputs
    check_inputs(job.record.get('body_plans',{}))
    if json.loads(json.dumps(state(organizer))) != job.record['input_state']:
        raise ValueError('The NPC patch inputs changed during generation. No new output was applied; recheck.')
    if digest(job.executable) != job.record['helper_sha256']: raise ValueError('NPC helper changed during generation.')


def run_job(organizer, job):
    # JSON-normalize tuples once for a stable receipt comparison.
    job.record['input_state'] = json.loads(json.dumps(job.record['input_state']))
    check_context(organizer, job)
    job.record.update(status='Generating paired NPC appearances'); job.save()
    try:
        settings = job.settings
        groups = settings['PluginsToForward']
        # Independent provider runs expose divergent shared assets before a combined
        # helper run could silently overwrite them. Only the final ESP is published.
        runs = [(f'provider-{i}', [g]) for i, g in enumerate(groups)] if len(groups) > 1 else []
        runs.append(('combined', groups))
        for label, providers in runs:
            folder = job.directory / label
            assets = folder / 'assets'; assets.mkdir(parents=True)
            shutil.copytree(job.record['defaults'], folder / 'Data')
            configured = dict(settings, AssetOutputDirectory=str(assets), PluginsToForward=providers)
            face_keys={key for provider in providers for key in provider['NPCs']}
            body_plans={key:plan for key,plan in job.record.get('body_plans',{}).items() if label=='combined' or key in face_keys}
            for setting in ('ModLabBodyAssignments','ModLabBodyModelSources','ModLabBodySex'):
                if setting in configured: configured[setting]={k:v for k,v in configured[setting].items() if k in body_plans}
            (folder / 'Data/settings.json').write_text(json.dumps(configured, indent=2), encoding='utf-8')
            patch = folder / OUTPUT
            args = [str(Path(job.record['sdk']) / 'dotnet.exe'), '--roll-forward', 'Major', str(job.executable),
                    'run-patcher', '-o', str(patch), '-g', 'SkyrimSE',
                    '-d', str(Path(organizer.managedGame().gameDirectory().absolutePath()) / 'Data'),
                    '-l', str(job.directory / 'plugins.txt'), '-e', str(folder / 'Data')]
            logpath = folder / 'tool.log'
            with working_environment(organizer, job.record['sdk']):
                handle = organizer.startApplication(str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/cmd.exe'),
                    [command_line(args, logpath)], str(folder), organizer.profile().name())
            if not handle: raise ValueError('NPC helper did not start.')
            complete, code = organizer.waitForApplication(handle, False)
            if not complete: raise ValueError('NPC helper is still running. Wait for it to finish; output was not applied.')
            log = logpath.read_text(encoding='utf-8-sig', errors='replace')
            if code or not patch.is_file() or 'Writing to output: ' + str(patch) not in log:
                raise ValueError('NPC generation did not complete. Inspect ' + str(logpath))
            if 'Remapping Dependencies' in log:
                raise ValueError('The helper attempted to replace source record identities. Output was withheld: ' + str(logpath))
            warnings = [line for line in log.splitlines() if re.search(r'\bwarning:', line, re.I)]
            # The helper's old per-file suppression implementation can suppress every
            # missing file for a plugin. Disable it and hold unexplained missing assets.
            if warnings:
                raise ValueError('NPC appearance assets need attention:\n' + '\n'.join(warnings[:12]) + '\nLog: ' + str(logpath))
            expected = face_keys|set(body_plans)
            if set(npc_records(patch)) != expected:
                raise ValueError('The helper did not export exactly the selected NPC records: ' + str(logpath))
            if body_plans:
                from .body_assignment import source_index,verify_preserved
                verify_preserved(patch,source_index(job.record['input_state'],job.record['selected']),body_plans)
            for key in face_keys:
                for relative in facegen_paths(key):
                    if not (assets / relative).is_file(): raise ValueError('Generated FaceGen pair is incomplete: ' + relative)
            from .npc_assets import inspect_textures, retain_textures
            meshes = [assets / facegen_paths(key)[0] for key in face_keys]
            textures = inspect_textures(job.record['sdk'], Path(organizer.modsPath()).parent / 'tools', meshes, folder) if meshes else {}
            evidence = []
            for provider in providers:
                required = {texture for key in provider['NPCs'] for texture in textures[str(assets / facegen_paths(key)[0])]}
                evidence.extend(retain_textures(organizer, provider, required, assets))
            job.record.setdefault('face_textures', {})[label] = evidence
            if any(m.casefold() not in {p.casefold() for p in job.record['input_state']['order']} for m in plugin_masters(patch)):
                raise ValueError('Generated appearance patch has an unavailable master.')
            merge_assets(assets, job.directory / 'output')
            if label == 'combined': shutil.copy2(patch, job.directory / 'output' / OUTPUT)
        check_context(organizer, job)
        import mobase
        from .xedit_workflow import run_xedit
        check, result, _, _ = run_xedit(organizer, OUTPUT, 'check', 'Check generated NPC appearance records before application',
            mobase.getFileVersion, generated_inputs={OUTPUT: job.directory / 'output' / OUTPUT})
        job.record['record_check'] = str(check / 'operation.json')
        if result['record_errors']: raise ValueError('Generated NPC records have errors. Output was withheld; inspect ' + str(check))
        job.record.update(hashes=settings_hashes(job.directory / 'output'), status='NPC records and paired assets checked; application pending')
    except Exception as error:
        job.record.update(status='NPC generation needs attention', error=str(error)); raise
    finally: job.save()


def publish_job(organizer, job, on_done):
    import mobase
    from PyQt6.QtCore import QTimer
    check_context(organizer, job)
    target = Path(organizer.modsPath()) / own_name(organizer)
    if not target.exists():
        mod = organizer.createMod(mobase.GuessedString(target.name))
        if mod is None or Path(mod.absolutePath()).resolve() != target.resolve(): raise ValueError('MO2 could not create the NPC output mod.')
    saved = dict(selected=job.record['selected'],body_choices=job.record.get('body_choices',{}),labels=job.record['labels'],
        input_state=job.record['input_state'], hashes=job.record['hashes'],
        build_record=str(job.directory / 'operation.json'))
    context = job.directory / 'recovery-context.json'
    context.write_text(json.dumps(saved, indent=2), encoding='utf-8')
    descriptions=[job.record['labels'][k]+' → '+p for k,p in job.record['selected'].items()]
    descriptions.extend(job.record['labels'][k]+' → '+p['shared_body']+' / '+p['shared_preset']+
        '; retained appearance skin' for k,p in job.record.get('body_plans',{}).items())
    projects = {'Selected character faces and bodies': dict(outputs=list(job.record['hashes']),
        source_mod=', '.join(sorted(set(job.record['selected'].values()))),
        preset='; '.join(descriptions),
        recovery_context_sha256=digest(context))}
    manifest = publish_output(target, job.directory, organizer.profilePath(), projects, job.record['hashes'], tool='NPC Appearance', replace_all=True)
    job.record.update(status='NPC output published; activation pending', previous_output=manifest['previous_output']); job.save()
    def ready():
        try:
            if organizer.profilePath() != job.record['profile_path']: raise ValueError('Selected profile changed before activation.')
            if 'pending_request' in job.record: check_pending(organizer,job.record['pending_request'])
            mods = organizer.modList(); mods.setPriority(target.name, max(mods.priority(n) for n in mods.allMods()))
            if not mods.setActive(target.name, True): raise ValueError('MO2 could not enable the NPC output.')
            issues = effective_issues(organizer, target, job.record['hashes'])
            if issues: raise ValueError('\n'.join(issues))
            activate_generated_plugins(organizer, target, job.record['hashes'], mobase.PluginState.ACTIVE)
            path_for(organizer).write_text(json.dumps(saved, indent=2), encoding='utf-8')
            pending = path_for(organizer, 'pending')
            if pending.exists(): pending.unlink()
            job.record.update(status='Selected NPC appearances applied; in-game appearance check pending'); job.save()
            on_done(target, None)
        except Exception as error:
            job.record.update(status='NPC activation needs attention', error=str(error)); job.save(); on_done(target, str(error))
    organizer.onNextRefresh(lambda: QTimer.singleShot(0, ready), False); organizer.refresh()


def saved_build_current(organizer, saved):
    if saved.get('status')=='reset':
        import mobase
        if organizer.modList().state(own_name(organizer)) & mobase.ModState.ACTIVE or organizer.pluginList().loadOrder(OUTPUT)>=0:
            raise ValueError('The cleared ModLab appearance output was enabled outside these choices. Review Character appearances before preparing again.')
        return True
    target = Path(organizer.modsPath()) / own_name(organizer)
    if not target.is_dir(): return False
    manifest = read_manifest(target, organizer.profilePath(), 'NPC Appearance')
    if manifest['hashes'] != saved['hashes']: raise ValueError('Installed NPC output does not match the retained selection.')
    issues = effective_issues(organizer, target, saved['hashes'])
    if issues:
        # Generated graphics may legitimately transform these exact appearance
        # meshes. Recognize the retained byte-to-byte receipt, not any later winner.
        from . import pgpatcher_workflow as graphics
        from .pgpatcher import transformed_body_files
        downstream = graphics.load_choices(organizer)
        if downstream and downstream.get('status') == 'applied':
            graphics_target = Path(organizer.modsPath()) / graphics.own_name(organizer)
            graphics_manifest = read_manifest(graphics_target, organizer.profilePath(), 'Graphics')
            if graphics_manifest['hashes'] == downstream['hashes']:
                transformed = transformed_body_files(target, manifest, graphics_target, graphics_manifest, organizer.resolvePath)
                issues = effective_issues(organizer, target,
                    {name: value for name, value in saved['hashes'].items() if name.casefold() not in transformed})
    if issues:
        raise ValueError('\n'.join(issues[:12]) + '\nChoose the intended appearance provider before rebuilding. '
                         'ModLab will preserve the current overrides until you select and apply appearances again.')
    if organizer.pluginList().loadOrder(OUTPUT) < 0: raise ValueError('The selected NPC appearance patch is disabled.')
    plugins = organizer.pluginList()
    if any(plugins.priority(p) > plugins.priority(OUTPUT) for p in saved['input_state']['order']):
        return False
    return json.loads(json.dumps(state(organizer))) == mesh_helper_state(saved['input_state'])


def inspect_choices(organizer):
    from .assessment import Finding
    try:
        pending, saved = load(organizer, 'pending'), load(organizer)
        if pending:
            return (Finding('Blocked', 'npc-pending', 'Your NPC appearance selection has not been applied',
                pending.get('error') or 'The requested build has not completed.',
                'Open NPC appearance choices, resolve the reported requirement and retry, or cancel the pending selection.'),)
        if saved:
            if not saved_build_current(organizer, saved):
                return (Finding('Review', 'npc-stale', 'NPC appearances need an update',
                    'Installed mods or asset winners changed after your saved NPC selection.', 'Use Recheck and finish setup. ModLab will rebuild your selected appearances before downstream patchers.'),)
            if saved.get('status')=='reset':
                return (Finding('Info','npc-reset','Character appearance overrides cleared',
                    'The previous generated appearance output is disabled and retained for recovery. Source mods now supply appearances.',
                    'Choose character appearances again whenever you want to add an override.'),)
            count=len(set(saved['selected'])|set(saved.get('body_choices',{})))
            return (Finding('Info', 'npc-current', f"Character choices applied: {count}",
                f"{len(saved['selected'])} face selections and {len(saved.get('body_choices',{}))} body selections; checked output is enabled. Build: " + saved['build_record'],
                'Check the selected NPCs in game. Source mods remain installed; other NPCs follow normal load order.'),)
        return ()
    except Exception as error:
        return (Finding('Blocked', 'npc-incomplete', 'NPC output could not be checked', str(error), 'Open NPC appearance choices and resolve the reported problem.'),)
