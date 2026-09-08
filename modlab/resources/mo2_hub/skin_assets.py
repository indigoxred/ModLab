"""Character-local skin texture edits; preserve FaceGen geometry and other parts."""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import struct
from .npc_assets import texture_path

SKIN_SLOTS={0:'diffuse',1:'normal',2:'subsurface',7:'specular'}


def face_replacements(shapes, skin):
    heads=[shape for shape in shapes if shape.get('shader')==4]
    if len(heads)!=1:
        raise ValueError('The selected face needs one recognised face skin shader before changing its skin.')
    head=heads[0]
    if any(shape is not head and shape.get('texture_set')==head.get('texture_set') for shape in shapes):
        raise ValueError('The face skin texture set is shared with another part. A matching appearance patch is needed.')
    textures=head['textures'];changes={}
    for slot,role in SKIN_SLOTS.items():
        if slot>=len(textures):raise ValueError('The face skin texture slots are incomplete.')
        if not skin.get(role):raise ValueError('The selected skin is missing its matching head '+role+' map.')
        changes[slot]=texture_path(skin[role])
    # Slot 6 is the character's baked tint. Slot 3 is its detail map.
    # Neither is a general skin replacement; never infer them from filenames.
    return {head['name']:changes}


def verify_shapes(before,after,changes):
    expected=deepcopy(before)
    if len({shape['name'] for shape in expected})!=len(expected):
        raise ValueError('The face has ambiguous shape names.')
    for shape in expected:
        for slot,path in changes.get(shape['name'],{}).items():shape['textures'][int(slot)]=path
    def normalized(shapes):
        result=deepcopy(shapes)
        for shape in result:shape['textures']=[texture_path(p) if p else '' for p in shape['textures']]
        return result
    if normalized(expected)!=normalized(after):
        raise ValueError('The exported face contains an unrequested geometry, shader or texture change.')


def block_hashes(path,inspection):
    data=Path(path).read_bytes();sizes=[int(block['size']) for block in inspection['blocks']]
    # SSE FaceGen uses one root. Do not guess offsets for an unsupported footer.
    if data[-8:]!=struct.pack('<II',1,0) or any(size<0 for size in sizes):
        raise ValueError('The face mesh has an unsupported block footer.')
    offset=len(data)-8-sum(sizes)
    if offset<40:raise ValueError('The face mesh block sizes are invalid.')
    hashes=[]
    for size in sizes:
        hashes.append(hashlib.sha256(data[offset:offset+size]).hexdigest());offset+=size
    return hashes


def inspect(sdk,tools,meshes,directory):
    from .npc_assets import inspect_nifs
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    if (directory/'skin-replacements.json').exists():raise ValueError('Use a fresh directory for skin inspection.')
    return inspect_nifs(sdk,tools,meshes,directory,program=Path(__file__).with_name('skin_nifs.cs').read_text(encoding='utf-8'),tool='modlab-skin-editor-v1')


def change_face(sdk,tools,source,target,skin,directory):
    from .npc_assets import inspect_nifs
    source,target,directory=Path(source),Path(target),Path(directory)
    if source.resolve()==target.resolve() or target.exists():raise ValueError('Use a new output for the character face.')
    directory.mkdir(parents=True,exist_ok=True);target.parent.mkdir(parents=True,exist_ok=True)
    before=inspect(sdk,tools,[source],directory/'before')[str(source)]
    changes=face_replacements(before['shapes'],skin)
    source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    request={str(source):dict(output=str(target),changes=changes)}
    (directory/'skin-replacements.json').write_text(json.dumps(request),encoding='utf-8')
    result=inspect_nifs(sdk,tools,[source],directory,program=Path(__file__).with_name('skin_nifs.cs').read_text(encoding='utf-8'),tool='modlab-skin-editor-v1')[str(source)]
    if hashlib.sha256(source.read_bytes()).hexdigest()!=source_hash:raise ValueError('Source face changed during preparation.')
    after=result['after']
    verify_shapes(before['shapes'],after['shapes'],changes)
    if before['strings']!=after['strings'] or [b['type'] for b in before['blocks']]!=[b['type'] for b in after['blocks']]:
        raise ValueError('The skin edit changed unrelated face records.')
    original_hashes=block_hashes(source,before);new_hashes=block_hashes(target,after)
    # Nifly normalizes texture path spelling on load/save. The preceding
    # per-shape comparison proves these still reference exactly the same files.
    # Permit that only for inspected texture sets, never arbitrary other blocks.
    allowed={shape['texture_set'] for shape in before['shapes'] if shape['texture_set']>=0}
    if any(before['blocks'][i]['type']!='BSShaderTextureSet' for i in allowed):
        raise ValueError('A face texture reference points to an unexpected block.')
    if any(a!=b for i,(a,b) in enumerate(zip(original_hashes,new_hashes)) if i not in allowed):
        raise ValueError('The skin edit changed face geometry or unrelated shader data.')
    return dict(source_sha256=source_hash,output_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                changes=changes,unchanged_nontexture_blocks=len(original_hashes)-len(allowed),
                verified_texture_sets=len(allowed))
