"""Coordinate optional individual shapes with the existing body/output helpers."""
import json
import os
from pathlib import Path
from uuid import uuid4

from .body_customization import OBODY_CONFIG
from .outputs import MANIFEST, digest, output_name, read_manifest
from .vfs import readable_path

_archive_digests={}


def archive_checksum(path):
    stat=path.stat();key=(str(path),stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)
    if key not in _archive_digests:
        checksum=digest(path);after=path.stat()
        if (after.st_size,after.st_mtime_ns,after.st_ctime_ns)!=key[1:]:
            raise ValueError('The body asset archive changed during inspection.')
        if len(_archive_digests)>=16:_archive_digests.clear()
        _archive_digests[key]=checksum
    return _archive_digests[key]


def pending_path(profile):
    return Path(profile)/'modlab-shape-preparation.json'


def mark_pending(profile, record):
    path=pending_path(profile);temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(record,indent=2),encoding='utf-8')
    os.replace(temporary,path)


def pending_finding(profile):
    from .assessment import Finding
    path=pending_path(profile)
    if not path.exists():return None
    return Finding('Blocked','character-shape-preparation-pending','Finish individual body preparation',
        path.read_text(encoding='utf-8'),
        'Open Bodies & outfits to finish preparation. History & recovery retains the previous outputs.',
        'The body build and shape assignments must both finish before this setup is ready to launch.')


def restore_attempt(target, directory, profile, tool):
    """Only recover our exact publication, retaining changed files for review."""
    from .recovery import restore_previous_output
    target, directory=Path(target),Path(directory)
    if not (target/MANIFEST).is_file():return False
    current=read_manifest(target,profile,tool)
    if Path(current.get('previous_output','')).resolve()!=(directory/'previous-output').resolve():return False
    restore_previous_output(target,profile,tool,digest(target/MANIFEST))
    return True


def upstream(organizer, state):
    current=organizer.resolvePath(OBODY_CONFIG)
    if not current:raise ValueError('Install OBody NG before preparing individual shapes.')
    current=readable_path(current);checksum=digest(current)
    source=current.read_text(encoding='utf-8-sig')
    previous=state.get('applied')
    target=Path(organizer.modsPath())/output_name(organizer.profile().name(),organizer.profilePath(),'Character Shape Assignments')
    if current.resolve()==readable_path(target/OBODY_CONFIG).resolve():
        if not previous or checksum!=previous['hashes'].get(OBODY_CONFIG):
            raise ValueError('Character shape configuration was edited outside ModLab. Reconcile that file before applying saved choices.')
        source=previous['upstream']
    return current,checksum,source


class InspectedAssets:
    """Read winning packed meshes through MO2's existing archive adapter."""
    def __init__(self,organizer,directory):
        self.organizer=organizer;self.directory=Path(directory)
        self.inputs={};self.archives=None;self.archive_hashes={}

    def resolve(self,relative):
        loose=self.organizer.resolvePath(relative)
        if loose and readable_path(loose).is_file():return readable_path(loose)
        source=self.packed_source(relative)
        if source is None:return None
        target=self.directory/relative;target.parent.mkdir(parents=True,exist_ok=True)
        self.reader.extract_asset(source,relative,target)
        return target

    def packed_source(self,relative):
        from .archives import active_archive_paths,archive_reader,archive_rank
        from .assessment import Plugin
        from .inspection import plugin_load_orders
        import mobase
        if self.archives is None:
            self.reader=archive_reader();self.archives=active_archive_paths(self.organizer)
            feature=self.organizer.gameFeatures().gameFeature(mobase.DataArchives)
            self.registered=list(feature.archives(self.organizer.profile())) if feature else []
            self.plugins=[Plugin(n,i,(),self.organizer.pluginList().origin(n))
                for n,i in plugin_load_orders(self.organizer.pluginList()).items()]
        candidates=[(archive_rank(n,self.plugins,self.registered),n,p) for n,p in self.archives.items()
                    if relative.casefold() in self.reader.entries(p)]
        if not candidates:return None
        highest=max(rank for rank,_,_ in candidates)
        winners=[(name,path) for rank,name,path in candidates if rank==highest]
        if len(winners)!=1:raise ValueError('Competing packed body assets need a choice: '+relative)
        name,source=winners[0];source=readable_path(source)
        if name not in self.archive_hashes:self.archive_hashes[name]=archive_checksum(source)
        self.inputs[relative]=dict(archive=name,path=str(source),sha256=self.archive_hashes[name])
        return source


def check_archive_inputs(organizer, inputs):
    # Re-resolve the winner as well as the archive bytes. A new loose override
    # must invalidate the retained evidence even if its old archive is intact.
    if not inputs:return
    assets=InspectedAssets(organizer,Path())
    for relative,expected in inputs.items():
        loose=organizer.resolvePath(relative)
        if loose and readable_path(loose).is_file():
            raise ValueError('A packed body asset was replaced by a loose file: '+relative)
        assets.packed_source(relative)
        if assets.inputs.get(relative)!=expected:
            raise ValueError('A packed body asset changed or was replaced: '+relative)


def plan_defaults(organizer, catalog, characters, defaults, source, directory):
    from .shape_defaults import plan_many
    requested={sex:value for sex,value in defaults.items() if value.get('individual_shapes')}
    if not requested:return None
    assets=InspectedAssets(organizer,Path(directory)/'shape-inputs')
    rules={sex:dict(preset=value['preset'],body_models=catalog.projects[value['body']].outputs,
        built_models=[path for name in value['selected'] for path in catalog.projects[name].outputs])
        for sex,value in requested.items()}
    result=plan_many(source,characters,rules,catalog.presets,assets.resolve)
    result['archive_inputs']=assets.inputs
    for relative in assets.inputs:result['inputs'].pop(relative,None)
    return result


def prepare_request(organizer,catalog,request,characters):
    from .author_neutral import recommend
    from .body_workflow import effective_files
    from .body_choices import choice_groups,load_defaults
    from .bodyslide import plan_build
    from .character_shapes import load_choices,ShapeRequirementError
    from .inspection import collect_setup
    from .shape_support import inspect_support
    from .foundations import inspect_foundations
    import mobase
    setup=collect_setup(organizer,version_reader=mobase.getFileVersion)
    requirements=inspect_support(setup,organizer.resolvePath,requested=True)
    if requirements:raise ShapeRequirementError(requirements[0])
    blockers=[f for f in inspect_foundations(setup,organizer.resolvePath) if f.level=='Blocked']
    if blockers:raise ShapeRequirementError(blockers[0])
    proof=recommend(effective_files(organizer),catalog,request['body'])
    plan_build(catalog,request['selected'],proof['preset'],morphs=True)
    from .body_ownership import active_outfit_models
    groups=choice_groups(catalog,request['body'],request['preset'],outfit_paths=active_outfit_models(organizer,request['sex']))
    missing=[group.label for group in groups if not set(group.options).intersection(request['selected'])]
    if missing:
        raise ValueError('Individual shapes need the matching body parts and outfits prepared together. '
            'Enable compatible outfits and choose a variant for the remaining '+str(len(missing))+' groups: '+', '.join(missing[:5])+'.')
    updated=dict(request,build_preset=proof['preset'],individual_shapes=True,shape_support=True,neutral_proof=proof)
    defaults_before=load_defaults(organizer.profilePath());defaults=dict(defaults_before);defaults[request['sex']]=updated
    state=load_choices(organizer.profilePath());current,checksum,source=upstream(organizer,state)
    directory=Path(organizer.modsPath()).parent/'builds/shape-preparation'/uuid4().hex[:12]
    directory.mkdir(parents=True)
    planned=plan_defaults(organizer,catalog,characters,defaults,source,directory)
    result=dict(request=updated,defaults=defaults,source=str(current),source_sha256=checksum,
                choices=state,defaults_before=defaults_before,directory=str(directory),plan=planned)
    (directory/'operation.json').write_text(json.dumps(dict(result,status='Preflight checked; not applied'),indent=2),encoding='utf-8')
    return result
