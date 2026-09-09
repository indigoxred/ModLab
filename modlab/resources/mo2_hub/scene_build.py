"""Prepare component selections in a working folder; publish through owned outputs."""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from uuid import uuid4

from .outputs import digest, publish_output
from .scene_choices import GROUPS, plan_choices, complete_textures, check_sources


def save(job):
    (Path(job['directory'])/'operation.json').write_text(json.dumps(job, indent=2), encoding='utf-8')


def prepare(instance, profile_path, providers, choices, inspect_meshes, resolve_texture, *, texture_choices=None):
    directory = Path(instance)/'builds/scene'/uuid4().hex[:12]
    directory.mkdir(parents=True)
    job = dict(directory=str(directory), profile_path=profile_path, choices=dict(choices),
               created_at=datetime.now(timezone.utc).isoformat(), status='Preparing')
    save(job)
    try:
        plan = plan_choices(providers, choices)
        if not plan['files']:
            raise ValueError('Choose at least one scenery appearance before building an output.')
        meshes = {name: value['source'] for name, value in plan['files'].items() if name.endswith('.nif')}
        references = inspect_meshes(meshes, directory)
        from .scene_shared import SharedTextureChoices
        try:
            complete_textures(plan, providers, references, resolve_texture, texture_choices=texture_choices)
        except SharedTextureChoices as decision:
            job.update(status='Shared appearance choices needed', texture_conflicts=decision.conflicts,
                       texture_choices=texture_choices or {}, error=str(decision))
            save(job)
            return job
        hashes = {}
        for name, entry in plan['files'].items():
            destination = directory/'output'/name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry['source'], destination)
            hashes[name] = digest(destination)
            if hashes[name] != entry['sha256']:
                raise ValueError('A scenery file changed while it was copied: ' + name)
        check_sources(plan)
        job.update(plan=plan, hashes=hashes, texture_choices=plan['texture_choices'], status='Checked; application pending')
        save(job)
        return job
    except Exception as error:
        job.update(status='Needs attention', error=str(error))
        save(job)
        raise


def publish(target, job, profile_path):
    if job['profile_path'] != profile_path:
        raise ValueError('The selected profile changed; reopen the scenery choices.')
    if job['status'] != 'Checked; application pending':
        raise ValueError('This scenery output has not been checked for publication.')
    check_sources(job['plan'])
    projects = {}
    for name, entry in job['plan']['files'].items():
        group = entry['group']
        project = projects.setdefault(GROUPS[group], dict(outputs=[], source_mod=job['choices'][group],
                                                          preset=GROUPS[group]))
        project['outputs'].append(name)
    manifest = publish_output(target, job['directory'], profile_path, projects, job['hashes'],
                              tool='Scene appearance', replace_all=True)
    job.update(status='Published; activation pending', previous_output=manifest['previous_output'])
    save(job)
    return manifest
