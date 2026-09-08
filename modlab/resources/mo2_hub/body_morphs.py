"""Bounded inspection of BodySlide 5.8.2 body TRI morph records (not face TRI)."""
import math
import struct


def inspect_tri(data):
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
    for label, stride in (('position_morphs', 8), ('uv_morphs', 6)):
        shapes = set()
        for _ in range(number('<H')):
            shape = name()
            if shape in shapes:
                raise ValueError('Duplicate body morph shape.')
            shapes.add(shape)
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
                take(vertices * stride)
                if vertices and scale:
                    result[label] += 1
    if offset != len(data):
        raise ValueError('Unexpected trailing body morph data.')
    if not any(result.values()):
        raise ValueError('The generated body morph file has no usable morphs.')
    return result
