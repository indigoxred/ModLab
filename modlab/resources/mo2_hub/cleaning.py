"""Apply supported xEdit maintenance to recoverable, profile-owned copies."""
import json
import shutil
from dataclasses import asdict
from pathlib import Path, PureWindowsPath
from uuid import uuid4

from .assessment import Finding
from .cleaning_policy import cleaning_decisions
from .outputs import MANIFEST, output_name, publish_output, read_manifest
from .xedit import check_result, dependency_order, digest, verify_job, write_record

TOOL = 'Cleaned plugins'


def output_target(organizer):
    return Path(organizer.modsPath()) / output_name(organizer.profile().name(), organizer.profilePath(), TOOL)


def withdraw_output(organizer, resume):
    """Return to MO2's event loop before resolving the underlying original plugins."""
    from .skse import require_game_closed
    require_game_closed()
    target = output_target(organizer)
    if not target.exists():
        return False
    read_manifest(target, organizer.profilePath(), TOOL)
    from PyQt6.QtCore import QTimer
    organizer.modList().setActive(target.name, False)

    def ready():
        try:
            for name in read_manifest(target, organizer.profilePath(), TOOL)['hashes']:
                resolved = Path(organizer.resolvePath(name))
                if target.resolve() in resolved.resolve().parents:
                    raise ValueError('The previous cleaned output is still effective. Disable it in MO2 before rechecking.')
        except Exception as error:
            resume(error)
        else:
            resume(None)

    organizer.onNextRefresh(lambda: QTimer.singleShot(0, ready), False)
    organizer.refresh()
    return True


def reusable_job(job, names, resolve):
    try:
        record = json.loads((job / 'operation.json').read_text(encoding='utf-8'))
        if record.get('mode') != 'clean' or not record.get('result', {}).get('changed'):
            return False
        if list(record['sources']) != list(names):
            return False
        if digest(job / 'Data' / names[-1]) != record['result']['sha256']:
            return False
        for name, source in record['sources'].items():
            current = Path(resolve(name))
            if current.resolve() != Path(source['path']).resolve() or digest(current) != source['sha256']:
                return False
        verify_job(job, 'clean', 0)
        return check_result((job / 'check.log').read_text(encoding='utf-8-sig'), 0) == 0
    except (OSError, ValueError, KeyError, TypeError):
        return False


def known_record_issue(job, names, resolve):
    """Do not repeat a completed rejected check while its actual input files are unchanged."""
    try:
        record = json.loads((job / 'operation.json').read_text(encoding='utf-8'))
        if record.get('mode') != 'clean' or record.get('status') != 'Needs attention' or list(record['sources']) != list(names):
            return None
        for name, source in record['sources'].items():
            path = Path(resolve(name))
            if path.resolve() != Path(source['path']).resolve() or digest(path) != source['sha256']:
                return None
        log = (job / 'check.log').read_text(encoding='utf-8-sig')
        import re
        match = re.search(r'Done: Checking for Errors, Processed Records: \d+, Errors found: (\d+)', log)
        if match and check_result(log, min(int(match[1]), 127)) > 0:
            return '\n'.join(line for line in log.splitlines() if ' -> ' in line)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def source_providers(organizer, names, exclude=''):
    wanted = {name.casefold() for name in names}
    return {PureWindowsPath(info.filePath).name.casefold(): [origin for origin in info.origins if origin != exclude]
            for info in organizer.findFileInfos('', lambda info: PureWindowsPath(info.filePath).name.casefold() in wanted)}


def prepare_cleaning(organizer, setup, report, version_reader):
    from .xedit_workflow import run_xedit
    decisions = cleaning_decisions(report, (p.name for p in setup.plugins if p.load_order >= 0), organizer.resolvePath)
    findings, steps, jobs = [], [], {}
    builds = Path(organizer.modsPath()).parent / 'builds' / 'xedit'
    existing = sorted(builds.glob('*/operation.json'), key=lambda p: p.stat().st_mtime_ns, reverse=True)
    for decision in decisions:
        if decision.action != 'clean':
            findings.append(Finding('Info' if decision.action == 'skip' else 'Review', 'cleaning-' + decision.action,
                decision.plugin + ': ' + ('cleaning excluded' if decision.action == 'skip' else 'cleaning needs specific guidance'),
                decision.reason + '\n' + decision.source,
                'Leave this plugin unchanged.' if decision.action == 'skip' else
                'Check the linked mod instructions for an update or specific cleaning exception; ModLab has left the plugin unchanged.'))
            continue
        names = dependency_order(setup.plugins, decision.plugin)
        rejected = next(((path.parent, detail) for path in existing
                         if (detail := known_record_issue(path.parent, names, organizer.resolvePath))), None)
        if rejected:
            findings.append(Finding('Review', 'cleaning-record-issue', decision.plugin + ': cleaning withheld after a record warning',
                rejected[1] + '\n\nRun: ' + str(rejected[0]),
                'Keep the original plugin. Check for an author update or applicable patch; Quick Auto Clean did not resolve this record warning.',
                'The source files are unchanged since the previous check, so ModLab has retained the result without running the same cleaning again. '
                'No cleaned copy of this plugin was enabled. The warning still needs interpretation; this does not by itself prove a gameplay failure.'))
            continue
        cached = next((path.parent for path in existing if reusable_job(path.parent, names, organizer.resolvePath)), None)
        if cached:
            job = cached
            steps.append('Reused the checked cleaned copy of ' + decision.plugin + '; source plugins and text resources still match.')
        else:
            try:
                job, result, archive, _ = run_xedit(organizer, decision.plugin, 'clean', decision.reason + '\n' + decision.source, version_reader)
            except Exception as error:
                findings.append(Finding('Review', 'cleaning-failed', decision.plugin + ': cleaned copy was not accepted', str(error),
                    'The original remains installed. Review the retained tool result for a missing requirement, author update or specific patch.'))
                steps.append('Withheld the cleaned copy of ' + decision.plugin + '; independent successful checks continue.')
                continue
            if not result['changed']:
                steps.append('xEdit completed without changing ' + decision.plugin + '.')
                continue
            if not archive:
                raise ValueError('xEdit did not produce a verified cleaned result: ' + decision.plugin)
            steps.append('Cleaned a private copy of ' + decision.plugin + ' and passed its follow-up record check.')
        jobs[decision.plugin] = (job, decision)
    if not jobs:
        return None, tuple(findings), tuple(steps)
    target = output_target(organizer)
    import mobase
    if not target.exists():
        mod = organizer.createMod(mobase.GuessedString(target.name))
        if mod is None or Path(mod.absolutePath()).resolve() != target.resolve():
            raise ValueError('MO2 could not create the expected cleaned-output directory.')
    projects, hashes = {}, {}
    for name, (job, decision) in jobs.items():
        hashes[name] = digest(job / 'Data' / name)
        projects[name] = {'outputs': [name], 'preset': 'xEdit Quick Auto Clean',
                          'source_mod': next(p.origin or 'Game data' for p in setup.plugins if p.name == name),
                          'cleaning_job': str(job), 'decision': asdict(decision),
                          'source_providers': source_providers(organizer, dependency_order(setup.plugins, name))}
    current = read_manifest(target, organizer.profilePath(), TOOL)
    previous_projects = {name: {key: value for key, value in project.items() if key != 'build_record'}
                         for name, project in current['projects'].items()}
    if current['hashes'] == hashes and previous_projects == projects:
        steps.append('Retained the existing checked cleaned output; its files and source provenance are unchanged.')
        return target, tuple(findings), tuple(steps)
    collection = builds / ('collection-' + uuid4().hex[:12])
    (collection / 'output').mkdir(parents=True)
    for name, (job, decision) in jobs.items():
        shutil.copy2(job / 'Data' / name, collection / 'output' / name)
    write_record(collection, {'status': 'Prepared cleaned collection', 'projects': projects, 'hashes': hashes})
    manifest = publish_output(target, collection, organizer.profilePath(), projects, hashes, tool=TOOL, replace_all=True)
    write_record(collection, {'status': 'Published; activation pending', 'manifest': manifest})
    return target, tuple(findings), tuple(steps)


def activate_output(organizer, target):
    from .skse import require_game_closed
    require_game_closed()
    manifest = read_manifest(target, organizer.profilePath(), TOOL)
    for project in manifest['projects'].values():
        job = Path(project['cleaning_job'])
        record = json.loads((job / 'operation.json').read_text(encoding='utf-8'))
        if not reusable_job(job, tuple(record['sources']), organizer.resolvePath):
            raise ValueError('Source plugins or checked output changed before activation. Recheck this setup.')
    mods = organizer.modList()
    if not mods.setActive(target.name, True):
        raise ValueError('MO2 could not enable the checked cleaned copies.')
    # Rechecks temporarily disable these copies to inspect their originals.
    # Keep their existing priority when they already win, avoiding changes to
    # unrelated asset order and unnecessary downstream patch regeneration.
    if any(Path(organizer.resolvePath(name)).resolve() != (target / name).resolve()
           for name in manifest['hashes']):
        mods.setPriority(target.name, max(mods.priority(name) for name in mods.allMods()))
    for name, checksum in manifest['hashes'].items():
        effective = Path(organizer.resolvePath(name))
        if effective.resolve() != (target / name).resolve() or digest(effective) != checksum:
            mods.setActive(target.name, False)
            raise ValueError('The cleaned copy did not become effective: ' + name + '. Original sources remain installed.')
    return Finding('Info', 'cleaning-applied', f'Checked cleaning applied: {len(manifest["hashes"])} plugins',
                   '\n'.join(f"{name} — {project['source_mod']} — {project['cleaning_job']}" for name, project in manifest['projects'].items()),
                   'No further cleaning action is needed for these checked versions. Recheck after changing mods.',
                   'ModLab cleaned copies using the applicable version-specific guidance, checked their records, and verified '
                   'that MO2 uses those copies. Original source files and the previous generated output remain available for recovery. '
                   'This does not establish gameplay or compatibility with every other plugin.')


def inspect_cleaned_output(organizer):
    target = output_target(organizer)
    if not (target / MANIFEST).is_file():
        return (), ()
    manifest = read_manifest(target, organizer.profilePath(), TOOL)
    effective = {name: Path(organizer.resolvePath(name)) for name in manifest['hashes']}
    if not any(path.resolve() == (target / name).resolve() for name, path in effective.items()):
        return (), ()
    issues, checked_sources = [], {}
    for name, project in manifest['projects'].items():
        job = Path(project['cleaning_job']).resolve()
        job.relative_to(target.parent.parent.resolve() / 'builds' / 'xedit')
        record = json.loads((job / 'operation.json').read_text(encoding='utf-8'))
        if source_providers(organizer, record['sources'], target.name) != project.get('source_providers'):
            issues.append(name + ': the supplying mods or their priority changed.')
        for source in record['sources'].values():
            path = Path(source['path'])
            if path not in checked_sources:
                checked_sources[path] = digest(path)
            if checked_sources[path] != source['sha256']:
                issues.append(name + ': a source plugin or master changed.')
        if effective[name].resolve() != (target / name).resolve() or digest(effective[name]) != manifest['hashes'][name]:
            issues.append(name + ': the checked output is no longer effective.')
    if issues:
        return (), (Finding('Blocked', 'cleaning-stale', 'Cleaned copies need to be rebuilt for the current setup', '\n'.join(issues),
                            'Use Recheck and finish setup. ModLab will disable these copies, inspect the current originals, and rebuild only where supported.'),)
    names = tuple(manifest['hashes'])
    return tuple(name.casefold() for name in names), (Finding('Info', 'cleaning-current',
        f'Checked cleaned copies are current: {len(names)} plugins', target.name + '\n\n' +
        '\n'.join(f"{name} — {project['source_mod']} — {Path(project['cleaning_job']).name}" for name, project in manifest['projects'].items()),
        'No further cleaning is needed for these checked copies. Recheck after changing mods.',
        'MO2 is using the verified cleaned copies. Their recorded original plugins, masters and supplying mods still match. '
        'The original files remain installed and unchanged. Gameplay compatibility remains unverified.'),)
