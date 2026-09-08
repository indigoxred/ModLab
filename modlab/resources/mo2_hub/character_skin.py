"""Prepare a selected installed skin for one actor without changing other actors."""
import configparser
import hashlib
import json
from pathlib import Path
from .skin_sources import scan,prove
from .outputs import digest
from .vfs import readable_path


def catalog(organizer):
    import mobase
    mods=organizer.modList();result={}
    for name in mods.allMods():
        if not mods.state(name)&mobase.ModState.ACTIVE:continue
        mod=mods.getMod(name)
        if mod is None:continue
        root=Path(mod.absolutePath())
        if (root/'.modlab-output.json').is_file():continue
        config=configparser.ConfigParser(interpolation=None,strict=False);config.read(root/'meta.ini',encoding='utf-8-sig')
        archive=Path(config.get('General','installationFile',fallback=''))
        if not archive.is_absolute():archive=Path(organizer.downloadsPath())/archive
        for source in scan(readable_path(root)):
            key=name+'|'+source['folder']+'|'+source['sex']
            result[key]=dict(source,mod=name,root=str(root),archive=str(archive) if archive.is_file() else '')
    return result


def current_skin_name(organizer,actor,row,saved):
    """Resolve generated skin provenance only while its records/files still match."""
    if not saved or actor not in saved.get('skin_choices',{}):return None
    try:
        job=json.loads(Path(saved['build_record']).read_text(encoding='utf-8'))
        plan=job['skin_plans'][actor];body=job['body_plans'][actor]
        if row.get('body',{}).get('textures')!=body['retained_textures']:return None
        hashes={name.replace('\\','/').casefold():value for name,value in saved['hashes'].items()}
        for roles in plan['output'].values():
            for relative in roles.values():
                path=organizer.resolvePath(relative)
                if not path or not readable_path(path).is_file() or digest(readable_path(path))!=hashes[relative.replace('\\','/').casefold()]:return None
        return plan['source']['mod']
    except (OSError,ValueError,KeyError,TypeError):return None


def check_skin_swaps(index,body):
    for part in body['parts']:
        key=part.get('texture_swap')
        if not key:continue
        fields,plugin=index._record(key,b'FLST','skin texture swap list')
        values=fields.get(b'LNAM',[])
        if len(values)!=1:
            raise ValueError('This skin has multiple or empty texture swap variants. A matching skin conversion is needed to retain those variants.')
        index._record(index._reference(values[0],plugin),b'TXST','skin swap texture')


def texture_fields(fields):
    from .npc_assets import texture_path
    result={}
    for slot in range(8):
        tag=f'TX0{slot}';values=fields.get(tag.encode(),[])
        if len(values)>1:raise ValueError('Ambiguous skin texture channel: '+tag)
        value=values[0].rstrip(b'\0').decode('utf-8') if values else ''
        if value:result[tag]=texture_path(value)
    return result


def planned_texture_fields(fields,roles):
    result=texture_fields(fields)
    result.update({tag:roles[role] for tag,role in [('TX00','diffuse'),('TX01','normal'),('TX02','subsurface'),('TX07','specular')]})
    return result


def prepare(organizer,state,selected,body_choices,skin_choices,body_plans):
    from .body_assignment import prepare as prepare_bodies
    from .body_assignment import source_index
    from .body_variant import unambiguous_family
    choices=catalog(organizer);plans={}
    index=source_index(state,selected) if skin_choices else None
    for actor,choice in skin_choices.items():
        source=choices.get(choice['source'])
        if not source:raise ValueError('The selected skin is no longer installed. Choose an available skin for '+actor+'.')
        archive=Path(choice.get('archive') or source['archive'])
        if not archive.is_file():raise ValueError('Locate the original download for '+source['mod']+' in the character skin controls so its installed variant can be checked.')
        # Existing conversion checks prove the recipient's family even when its
        # private body is retained. Do not infer UV compatibility from the name.
        body=body_plans.get(actor)
        if body is None:
            body=prepare_bodies(organizer,state,selected,{actor:'shared'})[actor]
            body['shared']=body['original']
            body['model_sources']={part['armature']:part['armature'] for part in body['original']['parts']}
            body['shared_body']='Retained appearance body';body['shared_preset']='Unchanged'
        if body['sex']!=source['sex']:raise ValueError('Choose a skin matching this character’s body.')
        check_skin_swaps(index,body['original'])
        files={name:readable_path(Path(source['root'])/name) for name in source['files']}
        hashes={name:digest(path) for name,path in files.items()}
        stamp=dict(archive=str(archive.resolve()),archive_sha256=digest(archive),family='CBBE',files=hashes)
        cache=Path(organizer.modsPath()).parent/'cache/skin-variants';cache.mkdir(parents=True,exist_ok=True)
        receipt=cache/(hashlib.sha256(json.dumps(stamp,sort_keys=True).encode()).hexdigest()+'.json')
        proof=json.loads(receipt.read_text(encoding='utf-8')) if receipt.is_file() else prove(archive,source['root'],source,'CBBE')
        if proof.get('family')!='CBBE' or not unambiguous_family(proof.get('option',''),'CBBE') or proof.get('source_hashes')!=hashes:raise ValueError('Skin source evidence does not match the installed textures and one body family.')
        if not receipt.is_file():receipt.write_text(json.dumps(proof,indent=2),encoding='utf-8')
        # Actor-specific paths allow two characters to use different skins from
        # the same original texture paths without global file replacements.
        identity=hashlib.sha256(actor.casefold().encode()).hexdigest()[:16]
        output={part:{role:f'textures/modlab/characters/{identity}/skin/{part}-{role}.dds' for role in roles}
                for part,roles in source['parts'].items()}
        armatures={};expected={}
        for part in body['original']['parts']:
            slots=set(part['slots'])
            if 32 in slots:kind='body'
            elif slots&{33,34}:kind='hands'
            elif slots&{37,38}:kind='body'
            else:raise ValueError('This character has an additional skin body part that needs a matching conversion.')
            armatures[part['armature']]=output[kind]
            fields,_=index._record(part['texture_set'],b'TXST','original skin textures')
            expected[part['armature']]=planned_texture_fields(fields,output[kind])
        body['skin_textures']=armatures
        body['skin_expected_fields']=expected
        body['retained_textures']=sorted({p for fields in expected.values() for p in fields.values()})
        body['inputs'].update({str(path):hashes[name] for name,path in files.items()})
        body_plans[actor]=body
        plans[actor]=dict(source=source,proof=proof,output=output,body_textures=armatures,head_textures=output['head'])
    return plans


def verify_head_records(path,index,plans):
    from copy import copy
    from .npc_assets import texture_path
    changed=copy(index);changed.winning=dict(index.winning);changed.masters=dict(index.masters);changed.add_plugin(path)
    for actor,plan in plans.items():
        fields,plugin=changed._record(actor,b'NPC_','selected character')
        texture=changed._ref(fields,b'FTST',plugin)
        fields,_=changed._record(texture,b'TXST','selected head skin')
        for tag,role in ((b'TX00','diffuse'),(b'TX01','normal'),(b'TX02','subsurface'),(b'TX07','specular')):
            value=changed._one(fields,tag,b'').rstrip(b'\0').decode('utf-8')
            if not value or texture_path(value)!=plan['head_textures'][role]:
                raise ValueError('Generated head texture references do not match the chosen skin.')


class SourceView:
    """Use retained source priorities, excluding our previous generated output."""
    def __init__(self,organizer,state):self.organizer=organizer;self.state=state
    def __getattr__(self,name):return getattr(self.organizer,name)
    def resolvePath(self,relative):
        for _,root,entries in reversed(self.state['assets']):
            found=next((name for name,*_ in entries if name.replace('\\','/').casefold()==relative.replace('\\','/').casefold()),None)
            if found:return str(readable_path(Path(root)/found))
        return ''


def apply_assets(organizer,job):
    import shutil
    from .npc import facegen_paths
    from .shape_preparation import InspectedAssets
    from .skin_assets import change_face
    output=job.directory/'output';evidence={}
    view=SourceView(organizer,job.record['input_state'])
    sources=InspectedAssets(view,job.directory/'skin-source-assets')
    for actor,plan in job.record.get('skin_plans',{}).items():
        for relative in facegen_paths(actor):
            target=output/relative
            if not target.is_file():
                source=sources.resolve(relative)
                if source is None:raise ValueError('The character’s matching face mesh or tint is missing: '+actor)
                target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
        for part,roles in plan['source']['parts'].items():
            for role,relative in roles.items():
                source=readable_path(Path(plan['source']['root'])/relative)
                if digest(source)!=plan['proof']['source_hashes'][relative]:raise ValueError('Selected skin changed during preparation.')
                target=output/plan['output'][part][role];target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
        face=output/facegen_paths(actor)[0]
        directory=job.directory/'skin-edits'/hashlib.sha256(actor.casefold().encode()).hexdigest()[:16]
        changed=directory/'face.nif'
        evidence[actor]=change_face(job.record['sdk'],Path(organizer.modsPath()).parent/'tools',face,changed,plan['head_textures'],directory/'check')
        shutil.copy2(changed,face)
    job.record['skin_face_evidence']=evidence
    job.record['skin_archive_inputs']=sources.inputs
