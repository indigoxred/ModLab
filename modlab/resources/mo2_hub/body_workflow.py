"""Build an immutable job copy of MO2's effective BodySlide files, then package output."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import subprocess
from uuid import uuid4
import zipfile

from .bodyslide import read_catalog_files, read_catalog, plan_build, project_outputs, relative_path, verify_output, write_build_config
from .loot_workflow import context_signature
from .vfs import find_files, readable_path
from .outputs import output_name, publish_output


VIRTUAL_ROOT = 'CalienteTools/BodySlide'


def effective_files(organizer):
    result = {}
    for virtual in find_files(organizer, VIRTUAL_ROOT, ['*']):
        parts = PureWindowsPath(virtual).parts
        folded = [p.casefold() for p in parts]
        try:
            index = next(i for i in range(len(parts) - 1)
                         if folded[i:i+2] == ['calientetools', 'bodyslide'])
        except StopIteration:
            raise ValueError(f'Unexpected BodySlide file location: {virtual}')
        relative = relative_path('/'.join(parts[index+2:]))
        physical = readable_path(virtual)
        if not physical.is_file():
            physical = readable_path(organizer.resolvePath(virtual))
        if not physical.is_file():
            raise ValueError(f'The active file could not be read: {virtual}')
        result[relative] = physical
    if not result:
        raise ValueError('Install and enable BodySlide and matching project files through MO2 first.')
    return result


def file_signature(files):
    return tuple(sorted((name, str(path), path.stat().st_size, path.stat().st_mtime_ns)
                        for name, path in files.items()))


@dataclass
class BodyJob:
    directory: Path
    catalog: object
    executable: Path
    signature: tuple
    source_signature: tuple
    record: dict
    archive: Path | None = None

    def save(self):
        (self.directory / 'operation.json').write_text(json.dumps(self.record, indent=2), encoding='utf-8')


def prepare_body_job(organizer, version_reader, *, preview=False, editing=False):
    if organizer.managedGame().gameName() != 'Skyrim Special Edition':
        raise ValueError('Select Skyrim Special Edition before building.')
    from .pgpatcher_workflow import require_upstream_view
    if not preview and not editing:
        require_upstream_view(organizer)
    signature = context_signature(organizer)
    files = effective_files(organizer)
    executables = [p for name, p in files.items() if name.casefold() in {'bodyslide.exe', 'bodyslide x64.exe'}]
    if len(executables) != 1:
        raise ValueError('Select one complete BodySlide installation in the active mods.')
    version = version_reader(str(executables[0]))
    if version not in {'5.8.2', '5.8.2.0'}:
        raise ValueError(f'This build adapter has been checked for BodySlide 5.8.2; found {version or "unknown"}.')
    source_signature = file_signature(files)
    directory = Path(organizer.modsPath()).parent / 'builds' / 'bodyslide' / uuid4().hex[:12]
    if preview:
        catalog = read_catalog_files(files)
        mods_root = readable_path(organizer.modsPath()).resolve()
        origins = {}
        for name, project in catalog.projects.items():
            try:
                origins[name] = files[project.source_file].resolve().relative_to(mods_root).parts[0]
            except (KeyError, ValueError):
                origins[name] = 'Source mod not identified'
        return BodyJob(None, catalog, executables[0], signature, source_signature,
            dict(profile=organizer.profile().name(), profile_path=organizer.profilePath(), project_sources=origins))
    runner = directory / 'runner'
    runner.mkdir(parents=True)
    # An independent working copy keeps helper settings and existing generated assets intact.
    for name, source in files.items():
        target = runner / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    if source_signature != file_signature(effective_files(organizer)) or signature != context_signature(organizer):
        raise ValueError('The setup changed while preparing BodySlide. Reopen this workflow.')
    catalog = read_catalog(runner)
    if not catalog.projects or not catalog.presets:
        raise ValueError('BodySlide was located, but matching project or preset files are missing. Install and enable them first.')
    job = BodyJob(directory, catalog, runner / executables[0].name, signature, source_signature,
                  {'status': 'Choose projects', 'profile': organizer.profile().name(),
                   'profile_path': organizer.profilePath(), 'version': version,
                   'created_at': datetime.now(timezone.utc).isoformat(), 'sources': source_signature})
    mods_root = readable_path(organizer.modsPath()).resolve()
    origins = {}
    for name, project in catalog.projects.items():
        source = files.get(project.source_file)
        try:
            origins[name] = source.resolve().relative_to(mods_root).parts[0]
        except (AttributeError, ValueError):
            origins[name] = 'Source mod not identified'
    job.record['project_sources'] = origins
    job.save()
    return job


def check_body_context(organizer, job):
    if (context_signature(organizer) != job.signature or
            file_signature(effective_files(organizer)) != job.source_signature):
        raise ValueError('The profile or BodySlide inputs changed. Prepare a new build before installing output.')


def run_body_job(organizer, job, selected, preset, *, morphs=False):
    check_body_context(organizer, job)
    expected = plan_build(job.catalog, selected, preset, morphs=morphs)
    output = job.directory / 'output'
    output.mkdir()  # A job is single-use; old meshes cannot satisfy a new build.
    group_name = 'ModLab-' + job.directory.name
    write_build_config(job.executable.parent, output,
                       Path(organizer.managedGame().gameDirectory().absolutePath()) / 'Data', selected, group_name)
    args = ['--groupbuild', group_name, '--preset', preset, '--targetdir', str(output)]
    if morphs:
        args.append('--trimorphs')
    job.record.update(status='Building', projects=list(selected), preset=preset, morphs=morphs, expected=list(expected), arguments=args)
    job.save()
    try:
        handle = organizer.startApplication(str(job.executable), [subprocess.list2cmdline(args)],
                                            str(job.executable.parent), organizer.profile().name())
        if not handle:
            raise RuntimeError('BodySlide could not start. The previous output remains unchanged.')
        complete, code = organizer.waitForApplication(handle, False)
        if not complete:
            raise RuntimeError('BodySlide has not finished. Close it normally before preparing another build.')
        if code != 0:
            raise RuntimeError(f'BodySlide exited with code {code}. Inspect its retained log.')
        check_body_context(organizer, job)
        hashes = verify_output(output, expected)
        if morphs:
            from .runtime import ensure_sdk10
            from .body_meshes import verify_generated_morphs
            tools = Path(organizer.modsPath()).parent / 'tools'
            sdk = ensure_sdk10(tools)
            job.record['morph_links'] = verify_generated_morphs(sdk, tools, output, expected, job.directory)
            check_body_context(organizer, job)
            if hashes != verify_output(output, expected):
                raise ValueError('Generated body files changed during morph-link inspection.')
        archive = job.directory / 'ModLab BodySlide Output.zip'
        with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as package:
            for name in expected:
                package.write(output / name, name)
        job.archive = archive
        job.record.update(status='Generated files checked; installation pending', hashes=hashes, archive=str(archive),
                          verification=('Expected meshes and linked TRI shapes/vertex bounds checked. Runtime assignment, topology, appearance and physics are separate checks.' if morphs else 'Expected meshes have NIF headers. Appearance and physics are separate checks.'))
        return hashes
    except Exception as error:
        job.record.update(status='Needs attention', error=str(error))
        raise
    finally:
        job.save()


def publish_body_job(organizer, job, on_ready):
    """Publish our generated collection, then let MO2 refresh before enabling/checking it."""
    import mobase
    from PyQt6.QtCore import QTimer
    check_body_context(organizer, job)
    if not job.archive or not job.record.get('hashes'):
        raise ValueError('Build and verify the output before publishing it.')
    name = output_name(job.record['profile'], job.record['profile_path'])
    target = Path(organizer.modsPath()) / name
    if not target.exists():
        mod = organizer.createMod(mobase.GuessedString(name))
        if mod is None:
            raise ValueError('MO2 could not create the generated output mod.')
        if Path(mod.absolutePath()).resolve() != target.resolve():
            raise ValueError('MO2 returned a different output directory. Nothing was published there.')
    projects = {name: {'outputs': list(project_outputs(job.catalog.projects[name], job.record.get('morphs', False))),
                       'morphs': job.record.get('morphs', False),
                       'preset': job.record['preset'], 'source_mod': job.record['project_sources'][name]}
                for name in job.record['projects']}
    manifest = publish_output(target, job.directory, job.record['profile_path'], projects, job.record['hashes'])
    job.record.update(installed_mod=name, installed_path=str(target),
                      previous_output=manifest['previous_output'], status='Output published; activation pending')
    job.save()

    def activate():
        error = None
        try:
            if organizer.profilePath() != job.record['profile_path']:
                raise ValueError('The selected profile changed. Output was not enabled in the other profile.')
            mods = organizer.modList()
            priority = max(mods.priority(item) for item in mods.allMods())
            mods.setPriority(name, priority)
            if not mods.setActive(name, True):
                raise ValueError('MO2 could not enable the generated output.')
        except Exception as problem:
            error = str(problem)
        on_ready(target, error)

    organizer.onNextRefresh(lambda: QTimer.singleShot(0, activate), False)
    organizer.refresh()
    return target
