"""Separate editing inputs from files consumed by a prepared game output."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def mesh_helper_state(state):
    """NPC Plugin Chooser and PGPatcher do not consume BodySlide preset XML.

Use for these known adapters only. Generic Synthesis patchers retain their full
input inventory because an arbitrary selected patcher can read other formats.
Both retained and current states are projected, avoiding a migration rebuild.
"""
    result=json.loads(json.dumps(state));assets=[]
    if 'assets' not in result:return result
    for name,root,entries in result['assets']:
        entries=[e for e in entries if not (e[0].replace('\\','/').casefold().startswith('calientetools/bodyslide/sliderpresets/')
                                           and e[0].casefold().endswith('.xml'))]
        if entries:assets.append([name,root,entries])
    result['assets']=assets
    return result


def preset_members(files, preset):
    result=[]
    for relative,path in files:
        if not relative.replace('\\','/').casefold().startswith('sliderpresets/') or Path(relative).suffix.casefold()!='.xml':continue
        for node in ET.parse(path).getroot().findall('Preset'):
            if node.get('name')==preset:result.append(relative.casefold())
    return result


def build_sources_match(record, current, record_path):
    old=record.get('sources')
    # Retained legacy receipts without a named preset cannot establish a narrower
    # dependency set. Never manufacture a successful match for those receipts.
    if not record.get('preset'):return old==current
    runner=Path(record_path).parent/'runner'
    if not runner.is_dir():return old==current
    try:
        previous=preset_members(((p.relative_to(runner).as_posix(),p) for p in (runner/'SliderPresets').rglob('*.xml')),record['preset'])
        active=preset_members(((entry[0],Path(entry[1])) for entry in current),record['preset'])
    except (OSError,ValueError,ET.ParseError):return False
    if len(previous)!=1 or active!=previous:return False
    relevant=set(previous)
    def select(signature):
        return [entry for entry in signature if not entry[0].replace('\\','/').casefold().startswith('sliderpresets/')
                or entry[0].casefold() in relevant]
    return select(old)==select(current)
