"""Separate inactive vanilla shader slots from required texture references.

NifTools nif.xml: BSLightingShaderType 0 is Default; type 3 enables Height
(texture slot 3). This rule is used only without a custom renderer/material route.
https://github.com/niftools/nifxml/blob/develop/nif.xml
"""
from .npc_assets import texture_path


def required_textures(references, inspection):
    required = {texture_path(path) for path in references}
    if any(('Texture' in block['type'] and block['type'] != 'BSShaderTextureSet') or
           ('Shader' in block['type'] and block['type'] not in {'BSLightingShaderProperty', 'BSShaderTextureSet'})
           for block in inspection['blocks']):
        return sorted(required), []
    inactive, active = set(), set()
    for shape in inspection['shapes']:
        for slot, path in enumerate(shape['textures']):
            if not path: continue
            target = inactive if slot == 3 and shape['shader'] == 0 else active
            target.add(texture_path(path))
    unused = (inactive - active) & required
    return sorted(required - unused), sorted(unused)
