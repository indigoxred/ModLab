"""Bounded inspection of BodySlide 5.8.2 body TRI morph records (not face TRI)."""
import math
import struct


def inspect_tri(data, *, details=False):
    if len(data) > 128 * 1024 * 1024 or not data.startswith(b'PIRT'):
        raise ValueError('Invalid body morph file: expected BodySlide body TRI data.')
    offset = 4

    def take(size):
        nonlocal offset
        if size > len(data) - offset:
            raise ValueError('Incomplete body morph file.')
        start = offset
        offset += size
        return data[start:offset]

    def number(fmt):
        return struct.unpack(fmt, take(struct.calcsize(fmt)))[0]

    def name():
        value = take(number('<B'))
        if not value or b'\0' in value:
            raise ValueError('Invalid body morph name.')
        return value

    result = {'position_morphs': 0, 'uv_morphs': 0}
    shape_details = {}
    for label, stride in (('position_morphs', 8), ('uv_morphs', 6)):
        shapes = set()
        for _ in range(number('<H')):
            shape = name()
            if shape in shapes:
                raise ValueError('Duplicate body morph shape.')
            shapes.add(shape)
            info = shape_details.setdefault(shape.decode('utf-8'), dict(morphs=set(), max_vertex=-1))
            morphs = set()
            for _ in range(number('<H')):
                morph = name()
                if morph in morphs:
                    raise ValueError('Duplicate body morph.')
                morphs.add(morph)
                scale = number('<f')
                if not math.isfinite(scale):
                    raise ValueError('Invalid body morph scale.')
                vertices = number('<H')
                deltas = take(vertices * stride)
                if vertices:
                    info['max_vertex'] = max(info['max_vertex'], max(struct.unpack_from('<H', deltas, i * stride)[0] for i in range(vertices)))
                if vertices and scale:
                    result[label] += 1
                    info['morphs'].add(morph.decode('utf-8'))
    if offset != len(data):
        raise ValueError('Unexpected trailing body morph data.')
    if not any(result.values()):
        raise ValueError('The generated body morph file has no usable morphs.')
    if details:
        result['shapes'] = {name:dict(morphs=sorted(info['morphs']), max_vertex=info['max_vertex']) for name,info in shape_details.items()}
    return result


def morph_path(value):
    from .bodyslide import relative_path
    if not isinstance(value, str) or not value or '\0' in value or len(value) > 1024:
        raise ValueError('Invalid BODYTRI link.')
    relative = relative_path(value.replace('\\', '/')).casefold()
    if not relative.endswith('.tri'): raise ValueError('BODYTRI does not reference a body morph file.')
    # RaceMenu prefixes meshes; a second meshes prefix is not silently repaired.
    return 'meshes/'+relative


def verify_mesh_links(meshes, tri_files):
    """Verify linked shape names and packed vertex bounds, not topology or appearance.

    RaceMenu finds a BODYTRI on the loaded object tree and applies matching shape
    records across that object. It need not be attached to every individual shape.
    Multiple distinct links need separate runtime-order analysis, so are withheld.
    """
    if not meshes: raise ValueError('No body meshes were inspected.')
    files = {key.casefold(): value for key, value in tri_files.items()}
    if len(files) != len(tri_files): raise ValueError('Ambiguous body morph file names.')
    parsed = {}; result = {}
    for path, mesh in meshes.items():
        shapes = mesh.get('shapes', {})
        if not shapes: raise ValueError('No shapes were read from '+path)
        links = set()
        for name, shape in shapes.items():
            if not isinstance(name, str) or not name or type(shape.get('vertices')) is not int or not 0 < shape['vertices'] <= 65535:
                raise ValueError('Invalid mesh shape or vertex count in '+path)
            if not isinstance(shape.get('links'), list): raise ValueError('Missing BODYTRI inspection for '+path)
            links.update(morph_path(value) for value in shape['links'])
        if not links: raise ValueError('The mesh has no attached BODYTRI link: '+path)
        if len(links) != 1: raise ValueError('Competing BODYTRI links need resolution: '+path)
        relative = next(iter(links))
        if relative not in files: raise ValueError('The linked body morph file is missing: '+relative)
        if relative not in parsed: parsed[relative] = inspect_tri(files[relative], details=True)
        data = parsed[relative]['shapes']
        matched = set(shapes).intersection(data)
        usable = [name for name in matched if data[name]['morphs']]
        if not usable: raise ValueError('The linked TRI has no usable matching mesh shape: '+path)
        for name in matched:
            if data[name]['max_vertex'] >= shapes[name]['vertices']:
                raise ValueError('The linked morph vertex index exceeds its mesh shape: '+path+' — '+name)
        result[path] = dict(tri=relative, matched_shapes=sorted(usable),
            morphs=sorted({morph for name in usable for morph in data[name]['morphs']}),
            unused_shapes=sorted(set(data)-set(shapes)))
    return result
