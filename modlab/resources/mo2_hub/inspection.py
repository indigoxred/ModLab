"""Read the selected MO2 context using the host's existing metadata APIs."""

from pathlib import Path, PureWindowsPath
from dataclasses import replace

from .assessment import Asset, Finding, Plugin, SetupSnapshot
from .outputs import CONTENTS, MANIFEST, inspect_output, output_name, read_manifest
from .vfs import directories
from .foundations import foundation_asset, inspect_foundations


def plugin_load_orders(plugins):
    # MO2 2.5.2 setLoadOrder updates priorities but not cached loadOrder values.
    # Recreate active positions from current priorities, as MO2 syncLoadOrder does.
    result, position = {}, 0
    for name in sorted(plugins.pluginNames(), key=plugins.priority):
        if plugins.loadOrder(name) < 0:
            result[name] = -1
        else:
            result[name] = position
            position += 1
    return result


def collect_setup(organizer, *, version_reader) -> SetupSnapshot:
    game = organizer.managedGame()
    game_name = game.gameName()
    game_root = Path(game.gameDirectory().absolutePath())
    profile = organizer.profile().name()
    errors = []
    runtime = ""
    try:
        runtime = version_reader(str(game_root / "SkyrimSE.exe"))
        if not runtime:
            errors.append("Skyrim executable runtime could not be identified.")
    except Exception as error:
        errors.append(f"Cannot read Skyrim runtime: {error}")

    plugins = []
    assets = []
    indexed = {}
    packed = {}
    archives_inspected, archive_findings = False, ()
    if game_name == "Skyrim Special Edition":
        try:
            plugin_list = organizer.pluginList()
            load_orders = plugin_load_orders(plugin_list)
            for name in plugin_list.pluginNames():
                try:
                    plugins.append(Plugin(
                        name, load_orders[name],
                        tuple(plugin_list.masters(name)), plugin_list.origin(name),
                    ))
                except Exception as error:
                    errors.append(f"Cannot inspect plugin {name}: {error}")
        except Exception as error:
            errors.append(f"Cannot inspect the plugin list: {error}")
        try:
            def relevant(info):
                path = info.filePath.replace("\\", "/").casefold()
                return len(info.origins) > 1 or path.endswith((".bsa", ".dll")) or foundation_asset(path)

            for directory in directories(organizer):
                for info in organizer.findFileInfos(directory, lambda info: True):
                    filename = PureWindowsPath(info.filePath).name
                    relative = directory + '/' + filename if directory else filename
                    if relative.casefold() in {MANIFEST.casefold(), CONTENTS.casefold()}:
                        continue  # Per-mod provenance documents are not game assets.
                    indexed[relative.casefold()] = tuple(info.origins)
                    if info.archive:
                        packed[relative.casefold()] = info.archive
                    if relevant(info):
                        assets.append(Asset(relative, tuple(info.origins), info.archive))
        except Exception as error:
            errors.append(f"Cannot inspect effective assets: {error}")
        if hasattr(organizer, 'profilePath'):
            try:
                from .archives import inspect_archives
                assets, archives_inspected, archive_findings = inspect_archives(organizer, assets, indexed, packed, plugins)
            except Exception as error:
                archive_findings = (Finding('Unknown', 'archive-index-incomplete',
                    'Packed-file inspection could not finish', str(error),
                    'Refresh MO2 and recheck. Preserve the original archives if a read error remains.'),)
    if organizer.profile().name() != profile:
        errors.append("The profile changed during inspection. Recheck the selected profile.")
    generated_verified, generated_findings = (), list(archive_findings)
    if game_name == 'Skyrim Special Edition' and hasattr(organizer, 'getPluginDataPath'):
        from .game_setup import inspect_saved_baseline
        generated_findings.extend(inspect_saved_baseline(game_root, Path(organizer.getPluginDataPath()) / 'modlab'))
    if hasattr(organizer, 'modsPath'):
        from .npc_workflow import inspect_choices
        generated_findings.extend(inspect_choices(organizer))
        try:
            from .install_queue import latest_unfinished
            queue = latest_unfinished(Path(organizer.modsPath()).parent, organizer.profilePath())
            if queue:
                remaining = [item for item in queue.data['items'] if item['state'] not in {'Completed', 'Dismissed'}]
                generated_findings.append(Finding('Review', 'installation-queue-pending',
                    f'Installation queue has {len(remaining)} unfinished archives',
                    '\n'.join(Path(item['archive']).name + ' — ' + item['state'] + '\n' + item.get('detail', '')
                              for item in remaining) + '\n\nSaved queue: ' + str(queue.path),
                    'Choose Review unfinished installs. Continue unattempted archives, '
                    'resolve failed installers before retrying, or dismiss requests you no longer want.',
                    'Earlier installed mods are retained, but the full requested batch has not been installed. '
                    'Checks on the active setup do not certify the unfinished archives.'))
        except Exception as error:
            generated_findings.append(Finding('Unknown', 'installation-queue-unreadable', 'Installation queue could not be read',
                str(error), 'Inspect the saved queue in History before retrying any interrupted installation.'))
        try:
            from .synthesis import load_pending, inspect_pipeline, pending_path
            pending = load_pending(organizer.profilePath())
            if pending:
                pipeline = inspect_pipeline(pending['settings'])
                generated_findings.append(Finding('Blocked', 'patching-pending', 'Selected patches have not been applied',
                    'Requested output: ' + ', '.join(pipeline.outputs) + '\n' +
                    (pending.get('error') or 'The attempt did not reach an accepted completion.') +
                    '\nRetained request: ' + str(pending_path(organizer.profilePath())),
                    'Open Patch choices to resolve the reported problem and retry, or cancel the pending request. '
                    'ModLab will not silently substitute an older successful selection.'))
        except Exception as error:
            generated_findings.append(Finding('Blocked', 'patching-pending-unreadable',
                'Pending patch request could not be inspected', str(error),
                'Inspect modlab-patcher-pending.json in this profile before retrying. Preserve its settings and build record.'))
        target = Path(organizer.modsPath()) / output_name(profile, organizer.profilePath())
        transformed = set()
        try:
            from .pgpatcher_workflow import inspect_graphics
            graphics_findings, transformed = inspect_graphics(organizer, target,
                read_manifest(target, organizer.profilePath()) if target.exists() else None)
            generated_findings.extend(graphics_findings)
        except Exception as error:
            generated_findings.append(Finding('Unknown', 'graphics-inspection-incomplete',
                'Graphics output could not be checked', str(error),
                'Open Graphics choices and the retained build record before launching. Preserve manually changed output.'))
        if target.exists():
            try:
                from .body_workflow import effective_files, file_signature
                verified, issues, manifest = inspect_output(target, organizer.profilePath(), organizer.resolvePath,
                                                            file_signature(effective_files(organizer)), transformed=transformed)
                generated_verified = tuple(verified)
                description = '\n'.join(f"{name} — {p['source_mod']} — {p['preset']}" for name, p in manifest['projects'].items())
                if issues:
                    generated_findings.append(Finding('Review', 'generated-output-stale',
                        'Generated bodies or outfits need an update', '\n'.join(issues),
                        'Open Advanced tools → BodySlide choices and rebuild the affected projects with your intended preset.',
                        'The previous build no longer matches the current inputs or effective files. ModLab has retained it, '
                        'but has not marked these replacements as checked.\n\n' + description))
                else:
                    generated_findings.append(Finding('Info', 'generated-output-verified',
                        f'Generated bodies and outfits applied: {len(manifest["projects"])} projects', description,
                        'No file-replacement fix is needed for this checked output. Appearance still needs an in-game check.',
                        f'ModLab generated these files from your selected projects and presets. {len(verified) - len(transformed)} files '
                        f'remain effective directly; {len(transformed)} have a checked downstream graphics transformation. '
                        'Their recorded build inputs are unchanged. Replacing the supplied meshes '
                        'is the purpose of this build.\n\n' + description))
            except Exception as error:
                generated_findings.append(Finding('Unknown', 'generated-output-unverified', 'Generated output could not be checked',
                    str(error), 'Open the output mod and retained build record before rebuilding; preserve any manual edits in a separate mod.'))
        try:
            from .cleaning import inspect_cleaned_output
            clean_verified, clean_findings = inspect_cleaned_output(organizer)
            generated_verified = tuple(generated_verified) + clean_verified
            generated_findings.extend(clean_findings)
        except Exception as error:
            generated_findings.append(Finding('Unknown', 'cleaning-unverified', 'Cleaned output could not be checked', str(error),
                'Use Recheck and finish setup before relying on the cleaned copies. Preserve any manual edits in a separate mod.'))
        try:
            from .skse_workflow import inspect_skse
            skse_verified, skse_findings = inspect_skse(organizer)
            generated_verified = tuple(generated_verified) + skse_verified
            generated_findings.extend(skse_findings)
        except Exception as error:
            generated_findings.append(Finding('Unknown', 'skse-check-incomplete', 'SKSE installation could not be checked',
                str(error), 'Inspect the SKSE script mod and its retained installation record before launching.'))
        if any(f.code == 'npc-current' for f in generated_findings):
            from .npc_workflow import load as load_npc_choices
            generated_verified = tuple(generated_verified) + tuple(name.casefold() for name in load_npc_choices(organizer)['hashes'])
    snapshot = SetupSnapshot(
        game_name=game_name, runtime=runtime, profile=profile,
        game_root=str(game_root), plugins=tuple(plugins), assets=tuple(assets),
        errors=tuple(errors), skse_loader_present=(game_root / "skse64_loader.exe").is_file(),
        archives_inspected=archives_inspected,
        generated_verified=generated_verified, generated_findings=tuple(generated_findings),
    )
    if game_name == 'Skyrim Special Edition' and hasattr(organizer, 'resolvePath'):
        skse_version = None
        if snapshot.skse_loader_present:
            try:
                from .skse import packed_loader_version
                skse_version = packed_loader_version(version_reader(str(game_root / 'skse64_loader.exe')) or '')
            except Exception:
                pass  # DLLs declaring a minimum remain Unknown if the loader cannot be read.
        try:
            from .mod_documents import download_page
            source_page = (lambda name: download_page(organizer.modsPath(), name)) if hasattr(organizer, 'modsPath') else None
            foundation_findings = inspect_foundations(snapshot, organizer.resolvePath, source_page=source_page,
                                                     skse_version=skse_version)
        except Exception as error:
            foundation_findings = (Finding('Unknown', 'inspection-incomplete', 'Foundation checks could not finish',
                str(error), 'Resolve the reported file access problem and recheck.'),)
        snapshot = replace(snapshot, generated_findings=snapshot.generated_findings + foundation_findings)
    if game_name == 'Skyrim Special Edition' and snapshot.skse_loader_present and hasattr(organizer, 'modsPath'):
        try:
            from .startup import inspect_startup
            startup_findings, checked = inspect_startup(organizer, snapshot)
            snapshot = replace(snapshot, startup_checked=checked,
                generated_findings=snapshot.generated_findings + startup_findings)
        except Exception as error:
            snapshot = replace(snapshot, generated_findings=snapshot.generated_findings + (
                Finding('Unknown','startup-inspection-incomplete','Previous startup result could not be checked',str(error),
                    'Open its launch record in History; launch through ModLab to collect a fresh result.'),))
    if game_name == 'Skyrim Special Edition' and hasattr(organizer, 'modsPath'):
        from .record_checks import inspect_record_checks
        snapshot = replace(snapshot, generated_findings=snapshot.generated_findings + inspect_record_checks(organizer, snapshot))
    if game_name == 'Skyrim Special Edition' and hasattr(organizer, 'profilePath'):
        from .character_shapes import inspect_choices as inspect_character_shapes
        snapshot = replace(snapshot, generated_findings=snapshot.generated_findings + inspect_character_shapes(organizer))
    return snapshot
