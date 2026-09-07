"""Run MO2's bundled LOOT in its VFS, then validate before applying its proposal."""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .inspection import collect_setup, plugin_load_orders
from .loot import report_findings, validate_order


def context_signature(organizer):
    plugins = organizer.pluginList()
    load_orders = plugin_load_orders(plugins)
    rows = []
    for name in sorted(plugins.pluginNames(), key=str.casefold):
        resolved = organizer.resolvePath(name)
        path = Path(resolved) if resolved else None
        stat = path.stat() if path and path.is_file() else None
        rows.append((name, plugins.priority(name), load_orders[name],
                     plugins.origin(name), str(path) if path else '',
                     stat.st_size if stat else None, stat.st_mtime_ns if stat else None))
    return (organizer.profilePath(), organizer.modsPath(), tuple(rows))


def current_order(organizer):
    plugins = organizer.pluginList()
    return tuple(sorted(plugins.pluginNames(), key=plugins.priority))


def apply_order(organizer, proposal, expected_signature):
    from .skse import require_game_closed
    require_game_closed()
    if context_signature(organizer) != expected_signature:
        raise ValueError('The setup changed since LOOT inspected it. Run LOOT again.')
    previous = current_order(organizer)
    if {p.casefold() for p in proposal} != {p.casefold() for p in previous} or len(proposal) != len(previous):
        raise ValueError('The proposed order does not match the current plugins.')
    plugins = organizer.pluginList()
    try:
        plugins.setLoadOrder(list(proposal))
        if tuple(p.casefold() for p in current_order(organizer)) != tuple(p.casefold() for p in proposal):
            raise RuntimeError('MO2 did not apply the proposed order.')
    except Exception as error:
        try:
            plugins.setLoadOrder(list(previous))
            restored = current_order(organizer) == previous
        except Exception:
            restored = False
        raise RuntimeError(str(error) + ' ' +
                           ('The previous order was restored.' if restored else
                            'Restoring the previous order also needs attention.')) from error
    return previous


@dataclass
class LootRun:
    directory: Path
    proposal: tuple
    previous: tuple
    signature: tuple
    findings: tuple


def prepare_loot_input(directory, plugins, order):
    # lootcli 1.6.0 overwrites pluginListPath. Its parent also supplies the
    # active plugins list to libloot. Keep both files away from the live profile.
    active = {p.name.casefold() for p in plugins if p.load_order >= 0}
    source = directory / 'loadorder.txt'
    source.write_text('\n'.join(order) + '\n', encoding='utf-8')
    (directory / 'plugins.txt').write_text(''.join(
        ('*' if name.casefold() in active else '') + name + '\n' for name in order), encoding='utf-8')
    return source


def run_loot(organizer, app_root, version_reader):
    executable = app_root / 'loot' / 'lootcli.exe'
    if not executable.is_file() or not (app_root / 'dlls' / 'Qt6Core.dll').is_file():
        raise ValueError('MO2’s bundled LOOT or Qt libraries are missing. Restore the complete MO2 installation.')
    if version_reader(str(executable)) not in {'1.6.0', '1.6.0.0'}:
        raise ValueError('This integration currently expects MO2’s bundled lootcli 1.6.0. Use MO2 Sort for another helper version.')
    setup = collect_setup(organizer, version_reader=version_reader)
    if setup.game_name != 'Skyrim Special Edition' or setup.errors:
        raise ValueError('The selected Skyrim setup could not be fully read. Recheck before sorting.')
    signature = context_signature(organizer)
    previous = current_order(organizer)
    directory = Path(organizer.getPluginDataPath()) / 'modlab' / 'loot' / uuid4().hex
    directory.mkdir(parents=True)
    record_path = directory / 'operation.json'
    record = {'profile': setup.profile, 'profile_path': organizer.profilePath(),
              'game_root': setup.game_root, 'previous_order': previous,
              'status': 'Inspecting', 'order_applied': False}
    record_path.write_text(json.dumps(record, indent=2), encoding='utf-8')
    source = prepare_loot_input(directory, setup.plugins, previous)
    report_path = directory / 'report.json'
    output_path = source
    args = ['--game', 'Skyrim Special Edition', '--gamePath', setup.game_root,
            '--pluginListPath', str(source), '--out', str(report_path),
            '--logLevel', 'info', '--language', 'en']
    # Ask the game's API for the identifier used by this installed MO2 build.
    args[1] = organizer.managedGame().lootGameName()
    try:
        # MO2's ProcessRunner joins this API's list with spaces without quoting.
        # Use Windows argv quoting, not shell quoting; no shell is launched.
        command = subprocess.list2cmdline(args)
        record['arguments'] = args
        handle = organizer.startApplication(str(executable), [command], str(executable.parent), setup.profile)
        if not handle:
            raise RuntimeError('LOOT could not start. Check the MO2 log.')
        completed, code = organizer.waitForApplication(handle, False)
        if not completed:
            raise RuntimeError('LOOT completion was not observed. No order was applied. Wait for it to close before retrying.')
        if code != 0:
            raise RuntimeError(f'LOOT exited with code {code}. No order was applied. Check the MO2 log and tool installation.')
        if context_signature(organizer) != signature:
            raise ValueError('The setup changed while LOOT was running. Recheck and run again.')
        if not report_path.is_file():
            raise RuntimeError('LOOT did not produce its report, even though it returned success. No order was applied.')
        proposal = validate_order(output_path.read_text(encoding='utf-8-sig'), setup.plugins)
        findings = report_findings(json.loads(report_path.read_text(encoding='utf-8-sig')), setup.plugins)
        # A per-NPC selection is an explicit choice of record winner. Keep its
        # generated override after sources and before our downstream patchers.
        from . import npc_workflow as appearances
        if appearances.load(organizer) and appearances.OUTPUT in proposal:
            from .npc import constrain_order
            from .outputs import output_name
            from .assessment import Finding
            owners = {output_name(organizer.profile().name(), organizer.profilePath(), t) for t in ('Synthesis', 'Graphics')}
            downstream = {p for p in proposal if organizer.pluginList().origin(p) in owners}
            adjusted = constrain_order(proposal, appearances.OUTPUT, downstream)
            if adjusted != proposal:
                proposal = validate_order('\n'.join(adjusted), setup.plugins)
                findings += (Finding('Info', 'loot-npc-choice', 'Plugin order preserves your selected NPC appearances',
                    'ModLab placed its NPC appearance patch after source plugins and before its downstream generated patches.',
                    'No manual order change is needed. Other LOOT dependency and incompatibility advice remains visible.'),)
        record.update(status='Ready for review', proposed_order=proposal)
        return LootRun(directory, proposal, previous, signature, findings)
    except Exception as error:
        record.update(status='Needs attention', error=str(error))
        raise
    finally:
        record_path.write_text(json.dumps(record, indent=2), encoding='utf-8')
