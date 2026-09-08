"""Prepare a neutral build and its separate, explicit desired shape.

Only stages presets in an isolated BodySlide runner. Publication and activating
a distribution helper must also preserve the shared default and NPC exceptions.
This module deliberately does not change the active game or helper configuration.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from .bodyslide import read_catalog, plan_build


@dataclass(frozen=True)
class Recipe:
    desired_preset: str
    projects: tuple
    base_name: str
    assignment_name: str
    base_file: str
    assignment_file: str
    base_xml: bytes
    assignment_xml: bytes


def number(text):
    try: value=float(text)
    except (ValueError,TypeError): raise ValueError('Body shape values must be finite numbers.')
    if not math.isfinite(value): raise ValueError('Body shape values must be finite numbers.')
    return value


def plan(runner, projects, preset):
    runner=Path(runner); projects=tuple(sorted(projects))
    catalog=read_catalog(runner)
    plan_build(catalog,projects,preset,morphs=True)
    matches=[node for path in (runner/'SliderPresets').rglob('*.xml')
        for node in ET.parse(path).getroot().findall('Preset') if node.get('name')==preset]
    if len(matches)!=1: raise ValueError('Choose one unambiguous installed shape preset.')
    original=matches[0]; values={}
    for node in original.findall('SetSlider'):
        key=(node.get('name'),node.get('size'))
        if not key[0] or key[1] not in {'small','big'}: raise ValueError('Invalid shape slider identity.')
        if key in values: raise ValueError('Duplicate shape slider: '+key[0])
        values[key]=number(node.get('value'))
    shape_values={}; zaps=set(); nodes=[]
    for name in projects:
        path=runner/catalog.projects[name].source_file
        found=[node for node in ET.parse(path).getroot().findall('SliderSet') if node.get('name')==name]
        if len(found)!=1: raise ValueError('The selected body/outfit project is ambiguous: '+name)
        nodes.append(found[0]); seen=set()
        for slider in found[0].findall('Slider'):
            key=slider.get('name')
            if not key or key in seen: raise ValueError('Duplicate or unnamed project slider: '+name)
            seen.add(key)
            if slider.get('zap','false').casefold()=='true':
                zaps.add(key); continue
            if slider.get('invert','false').casefold()=='true':
                raise ValueError('This project uses an inverted shape slider requiring a separate preparation adapter: '+name+' / '+key)
            for size in ('small','big'):
                identity=(key,size)
                value=values.get(identity,number(slider.get(size,'0')))
                if identity in shape_values and shape_values[identity]!=value:
                    raise ValueError(key+' has different body/outfit defaults. Choose matching projects or explicitly customize that slider before preparing individual shapes.')
                shape_values[identity]=value
    if not shape_values: raise ValueError('These projects have no body shape sliders.')
    if zaps.intersection(key for key,size in shape_values):
        raise ValueError('A slider changes shape in one project and removes geometry in another. Choose separate compatible project variants.')
    # Include source declarations in the identity; reusing a name after a source
    # update could otherwise mistake an old assignment for the prepared one.
    identity=hashlib.sha256(ET.tostring(original)+b''.join(ET.tostring(n) for n in nodes)).hexdigest()[:12]
    base_name='ModLab neutral body ['+identity+']'
    assignment_name='ModLab default - '+preset+' ['+identity+']'
    def document(name, neutral):
        node=deepcopy(original); node.set('name',name)
        for child in list(node):
            if child.tag=='SetSlider': node.remove(child)
        if neutral:
            # Retain the chosen geometry zaps. Unspecified zaps stay unspecified,
            # so each project's own geometry defaults are preserved as well.
            for (key,size),value in sorted(values.items()):
                if key in zaps: ET.SubElement(node,'SetSlider',name=key,size=size,value=format(value,'.17g'))
        for (key,size),value in sorted(shape_values.items()):
            ET.SubElement(node,'SetSlider',name=key,size=size,value='0' if neutral else format(value,'.17g'))
        root=ET.Element('SliderPresets');root.append(node)
        return ET.tostring(root,encoding='utf-8',xml_declaration=True)
    return Recipe(preset,projects,base_name,assignment_name,
        'ModLab-neutral-'+identity+'.xml','ModLab-default-'+identity+'.xml',
        document(base_name,True),document(assignment_name,False))


def stage(runner, recipe):
    """Stage only in a caller-owned scratch runner, refusing outside edits."""
    root=Path(runner)/'SliderPresets'
    files={root/recipe.base_file:recipe.base_xml,root/recipe.assignment_file:recipe.assignment_xml}
    for path,data in files.items():
        if path.exists() and path.read_bytes()!=data:
            raise ValueError('A different preparation file already exists: '+path.name)
    root.mkdir(parents=True,exist_ok=True)
    for path,data in files.items():
        if not path.exists():
            with path.open('xb') as stream:stream.write(data)
