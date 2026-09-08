"""Plan actor-local body references without editing shared armor or mesh files."""
from copy import copy
from .body_ownership import records

def plan(index, choices):
    result={}
    for actor,choice in choices.items():
        if choice!='shared': raise ValueError('Unknown character body choice: '+str(choice))
        key=actor.casefold()
        traits,fields,plugin,sex=index._traits(key)
        if traits!=key:
            raise ValueError('This character inherits body traits from a template; an actor-local override needs explicit template handling.')
        race=index._ref(fields,b'RNAM',plugin)
        race_fields,race_plugin=index._record(race,b'RACE','character race')
        skin=index._ref(race_fields,b'WNAM',race_plugin)
        if not skin: raise ValueError('The character race has no shared body assignment.')
        # Inspect the proposed result without mutating the source index.
        proposed=copy(index); proposed.winning=dict(index.winning)
        assigned=dict(fields); assigned[b'WNAM']=[skin]
        proposed.winning[key]=(b'NPC_',assigned,plugin)
        body=proposed.character(key)
        result[actor]=dict(skin=skin,body=body)
    return result

def verify(path, assignments):
    exported={key.casefold():value for key,value in records(path)}
    for actor,requested in assignments.items():
        value=exported.get(actor.casefold())
        if not value or value[0]!=b'NPC_' or value[1].get(b'WNAM')!=[requested['skin']]:
            raise ValueError('Generated character body assignment does not match the requested body: '+actor)


def plan_preserving_skin(index, choices):
    shared=plan(index,choices);result={}
    for actor,request in shared.items():
        current=index.character(actor);sources={}
        for part in current['parts']:
            matches=[p for p in request['body']['parts'] if p['slots']==part['slots']]
            if len(matches)!=1:
                raise ValueError('The shared body does not have one matching part for this character. Choose a compatible body conversion.')
            if part['weight_slider']!=matches[0]['weight_slider']:
                raise ValueError('The body parts use different weight interpolation. A matching body conversion is needed.')
            if not part['texture_set']:
                raise ValueError('This body part uses mesh-embedded skin textures; those need a matching conversion before keeping its skin.')
            sources[part['armature']]=matches[0]['armature']
        result[actor]=dict(skin=current['skin'],sex=current['sex'],model_sources=sources,
            retained_textures=current['textures'],shared=request['body'],original=current)
    return result


def source_index(state, selected):
    from pathlib import Path
    from .body_ownership import BodyIndex
    index=BodyIndex();paths={name.casefold():Path(path) for name,path,*rest in state['sources']}
    for name in state['order']: index.add_plugin(paths[name.casefold()])
    # Body/skin supplied by the chosen face may differ from the load-order winner.
    for actor,plugin in selected.items():
        key=actor.casefold();value=dict(records(paths[plugin.casefold()])).get(key)
        if not value or value[0]!=b'NPC_': raise ValueError('Selected appearance has no character record: '+actor)
        index.winning[key]=(*value,paths[plugin.casefold()].name)
    return index


def prepare(organizer,state,selected,choices):
    """Resolve, prove installed variants, and retain all relevant file hashes."""
    import configparser,json
    from pathlib import Path
    from .body_choices import load_defaults
    from .body_variant import Archive
    from .outputs import digest
    from .vfs import readable_path
    index=source_index(state,selected);plans=plan_preserving_skin(index,choices)
    defaults=load_defaults(organizer.profilePath());readers={}
    for actor,plan in plans.items():
        default=defaults.get(plan['sex'],{})
        if not default.get('build_record') or not default.get('body','').startswith('CBBE'):
            raise ValueError('Prepare a matching shared body in Bodies & outfits first. This conversion currently verifies the CBBE family.')
        build=json.loads(Path(default['build_record']).read_text(encoding='utf-8'))
        if build.get('profile_path')!=organizer.profilePath() or build.get('effective_output_issues')!=[]:
            raise ValueError('The shared body needs preparation before applying it to a character.')
        expected={key.casefold():value for key,value in build.get('hashes',{}).items()}
        inputs={}
        for part in plan['shared']['parts']:
            for relative in part['models']:
                resolved=organizer.resolvePath(relative)
                if not resolved or relative not in expected or digest(readable_path(resolved))!=expected[relative]:
                    raise ValueError('The shared body or its hands/feet changed outside the saved build. Review Bodies & outfits before applying this character choice.')
                inputs[str(readable_path(resolved))]=expected[relative]
        if {p for part in plan['original']['parts'] for p in part['models']}=={p for part in plan['shared']['parts'] for p in part['models']}:
            plan.update(inputs=inputs,variant=dict(basis='Existing and requested mesh paths match'),
                shared_body=default['body'],shared_preset=default['preset'])
            continue
        source=organizer.modList().getMod(organizer.pluginList().origin(plan['original']['skin_plugin']))
        if source is None: raise ValueError('The character skin source needs a matching body conversion.')
        root=Path(source.absolutePath())
        config=configparser.ConfigParser(interpolation=None,strict=False)
        config.read(root/'meta.ini',encoding='utf-8-sig')
        archive=Path(config.get('General','installationFile',fallback=''))
        if not archive.is_file(): raise ValueError('Locate the original archive for '+source.name()+' so ModLab can check its installed body variant.')
        required=set(plan['retained_textures'])|{p for part in plan['original']['parts'] for p in part['world_models']}
        installed={}
        for relative in required:
            resolved=organizer.resolvePath(relative)
            if not resolved or not readable_path(resolved).is_file():
                raise ValueError('A body or skin file is missing: '+relative+'. Reinstall its selected source.')
            path=readable_path(resolved);installed[relative]=path.read_bytes();inputs[str(path)]=digest(path)
        stamp=dict(archive=str(archive),archive_sha256=digest(archive),family='CBBE',inputs=inputs)
        import hashlib
        cache=Path(organizer.modsPath()).parent/'cache/body-variants'
        cache.mkdir(parents=True,exist_ok=True)
        receipt=cache/(hashlib.sha256(json.dumps(stamp,sort_keys=True).encode()).hexdigest()+'.json')
        if receipt.is_file(): proof=json.loads(receipt.read_text(encoding='utf-8'))
        else:
            reader=readers.setdefault(str(archive),None)
            if reader is None: reader=readers[str(archive)]=Archive(archive)
            proof=reader.check(installed,'CBBE')
            receipt.write_text(json.dumps(proof,indent=2),encoding='utf-8')
        if proof.get('family')!='CBBE' or set(proof.get('hashes',{}))!={p.casefold() for p in installed} or any(
                proof['hashes'][name.casefold()].get('sha256')!=hashlib.sha256(data).hexdigest() for name,data in installed.items()):
            raise ValueError('Body variant evidence is incomplete. Recheck the source installer.')
        plan.update(inputs=inputs,variant=proof,shared_body=default['body'],shared_preset=default['preset'])
    return plans


def check_inputs(plans):
    from pathlib import Path
    from .outputs import digest
    for plan in plans.values():
        for path,checksum in plan.get('inputs',{}).items():
            if not Path(path).is_file() or digest(Path(path))!=checksum:
                raise ValueError('A selected character body/skin file changed during preparation. Existing output is retained.')


def verify_preserved(path,index,plans):
    changed=copy(index);changed.winning=dict(index.winning);changed.masters=dict(index.masters)
    changed.add_plugin(path)
    for actor,plan in plans.items():
        actual=changed.character(actor)
        if actual['skin']==plan['skin']:
            raise ValueError('The body helper did not create the requested actor-local skin armor.')
        if actual['textures']!=plan['retained_textures']:
            raise ValueError('The character body operation changed its retained skin textures.')
        if actual['body_models']!=plan['shared']['body_models']:
            raise ValueError('Generated character body meshes do not match the selected shared body.')
        for original in plan['original']['parts']:
            matches=[p for p in actual['parts'] if p['slots']==original['slots']]
            if len(matches)!=1: raise ValueError('Generated body parts differ from the selected character.')
            part=matches[0]
            shared=[p for p in plan['shared']['parts'] if p['slots']==original['slots']]
            if len(shared)!=1 or any(part[key]!=shared[0][key]
                    for key in ('models','world_models','first_person_models')):
                raise ValueError('Generated character body part meshes do not match the selected shared body.')
            if any(part[key]!=original[key] for key in ('texture_set','texture_swap','priority','weight_slider')):
                raise ValueError('Generated body parts did not retain the character skin and weight settings.')
