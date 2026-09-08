"""Reuse the user's selected projects and presets in the normal setup workflow."""
from dataclasses import dataclass
import json
from pathlib import Path

from .assessment import Finding
from .bodyslide import plan_build, read_catalog_files
from .body_workflow import effective_files, file_signature, prepare_body_job, run_body_job, publish_body_job
from .outputs import digest, output_name, read_manifest
from .vfs import find_files


def plan_rebuilds(catalog, manifest, source_signature):
    groups, issues, records = {}, [], {}
    signature = json.loads(json.dumps(source_signature))
    for name, project in manifest['projects'].items():
        try:
            morphs = project.get('morphs', False)
            outputs = plan_build(catalog, [name], project['preset'], morphs=morphs)
            if {p.casefold() for p in outputs} != {p.casefold() for p in project['outputs']}:
                raise ValueError('Its output paths changed; choose how to replace the old output.')
            record_path = Path(project['build_record'])
            if record_path not in records:
                records[record_path] = json.loads(record_path.read_text(encoding='utf-8'))
            if records[record_path].get('sources') != signature:
                key = (project['preset'], True) if morphs else project['preset']
                groups.setdefault(key, []).append(name)
        except (ValueError, OSError, KeyError) as error:
            issues.append(name + ': ' + str(error))
    return groups, issues


def review_path(organizer):
    return Path(organizer.profilePath()) / 'modlab-body-choices.json'


def catalog_identity(catalog, files):
    return {name: [list(project.outputs), str(files.get(project.source_file, ''))]
            for name, project in catalog.projects.items()}


def remember_catalog(organizer, job):
    """Retain which alternatives were presented, without selecting them automatically."""
    if organizer.profilePath() != job.record['profile_path']:
        raise ValueError('The profile changed; choices were not saved into another profile.')
    identity = catalog_identity(job.catalog, {name: Path(path) for name, path, *_ in job.source_signature})
    review_path(organizer).write_text(json.dumps(identity, indent=2), encoding='utf-8')


@dataclass
class BodySetup:
    groups: dict
    choices: tuple
    findings: tuple
    identity: dict


def inspect_body_setup(organizer):
    # A setup without any BodySlide projects does not need this workflow.
    if next(iter(find_files(organizer, 'CalienteTools/BodySlide/SliderSets', ['*.osp', '*.xml'])), None) is None:
        return BodySetup({}, (), (), {})
    files = effective_files(organizer)
    catalog = read_catalog_files(files)
    target = Path(organizer.modsPath()) / output_name(organizer.profile().name(), organizer.profilePath())
    manifest = read_manifest(target, organizer.profilePath()) if target.exists() else {'projects': {}, 'hashes': {}}
    for project in manifest['projects'].values():
        Path(project['build_record']).resolve().relative_to(target.parent.parent.resolve() / 'builds')
    groups, issues = plan_rebuilds(catalog, manifest, file_signature(files))
    # Do not silently undo a user's chosen file winner or re-enable a disabled collection.
    for relative, checksum in manifest['hashes'].items():
        effective = Path(organizer.resolvePath(relative))
        if effective.resolve() != (target / relative).resolve() or not effective.is_file() or digest(effective) != checksum:
            issues.append(relative + ': the generated output is disabled or another mod wins. Choose the intended provider before rebuilding.')
            groups = {}
            break
    identity = catalog_identity(catalog, files)
    reviewed = json.loads(review_path(organizer).read_text(encoding='utf-8')) if review_path(organizer).is_file() else {}
    choices = tuple(name for name, value in identity.items() if reviewed.get(name) != value)
    findings = []
    if issues:
        findings.append(Finding('Review', 'body-build-review', 'Body or outfit choices need attention', '\n'.join(issues),
            'Open Body and outfit choices to select an available project/preset. If another mod wins, choose the intended provider in MO2 before rebuilding.'))
    if choices:
        findings.append(Finding('Review', 'body-choices', f'Body and outfit choices available: {len(choices)} projects', '\n'.join(choices),
            'Choose the bodies/outfits and presets you want. Alternatives are optional; ModLab will remember the choices and rebuild selected projects when their inputs change.'))
    return BodySetup(groups, choices, tuple(findings), identity)


def rebuild_saved(organizer, version_reader, groups, on_done, on_progress):
    """Sequential private builds; each publication completes its MO2 refresh before the next."""
    pending, completed = list(groups.items()), []

    def advance():
        if not pending:
            on_done(completed, None)
            return
        key, selected = pending.pop(0)
        preset, morphs = key if isinstance(key, tuple) else (key, False)
        try:
            on_progress('Rebuilding ' + ', '.join(selected) + ' using ' + preset + '…')
            job = prepare_body_job(organizer, version_reader)
            run_body_job(organizer, job, selected, preset, morphs=morphs)

            def ready(target, error):
                try:
                    if error:
                        raise ValueError(error)
                    for relative, checksum in job.record['hashes'].items():
                        effective = Path(organizer.resolvePath(relative))
                        if effective.resolve() != (target / relative).resolve() or digest(effective) != checksum:
                            raise ValueError('Generated output did not become effective: ' + relative)
                    job.record.update(status='Automatic rebuild applied; gameplay unverified')
                    job.save()
                    completed.append(f'Rebuilt {len(selected)} selected projects with {preset}; verified effective output. Run: {job.directory}')
                    advance()
                except Exception as problem:
                    on_done(completed, str(problem))

            publish_body_job(organizer, job, ready)
        except Exception as error:
            on_done(completed, str(error))

    advance()
