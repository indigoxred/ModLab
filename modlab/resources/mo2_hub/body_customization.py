"""An isolated BodySlide edit session followed by checked preset publication."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from .bodyslide import plan_build, read_catalog, write_build_config
from .outputs import digest, output_name, publish_output, read_manifest

OBODY_CONFIG = 'SKSE/Plugins/OBody_presetDistributionConfig.json'
TOOL = 'Shape Presets'
VIRTUAL = 'CalienteTools/BodySlide/'


def reset_build_result(dialog):
    dialog.installed=None; dialog.applied=False; dialog.pending_default=None
    dialog.install.setEnabled(False); dialog.check.setEnabled(False); dialog.build.setEnabled(True)


def exclude_random_preset(source, name):
    from .obody_config import plan_config
    # Validate the known schema without altering any actor or group assignment.
    data=json.loads(plan_config(source,{}, {}, {},preserve_actor_ids=True).text)
    values=data['blacklistedPresetsFromRandomDistribution']
    if name not in values: values.append(name)
    return json.dumps(data,ensure_ascii=False,indent=2)+'\n'


def prepare_customizer(job, body, preset, label, game_data):
    plan_build(job.catalog, [body], preset)
    runner=job.executable.parent
    matches=[]
    for path in (runner/'SliderPresets').rglob('*.xml'):
        matches.extend(node for node in ET.parse(path).getroot().findall('Preset') if node.get('name')==preset)
    if len(matches)!=1: raise ValueError('The starting shape preset is missing or ambiguous.')
    label=re.sub(r'[<>:"/\\|?*\x00-\x1f]', '-', label).strip(' .')[:55] or 'Custom shape'
    name=f'ModLab - {label} [{job.directory.name}]'
    if name in job.catalog.presets: raise ValueError('This custom shape session has already been prepared.')
    node=deepcopy(matches[0]); node.set('name',name); node.set('set',body)
    root=ET.Element('SliderPresets'); root.append(node)
    relative='SliderPresets/ModLab-'+job.directory.name+'.xml'
    file=runner/relative; ET.ElementTree(root).write(file,encoding='utf-8',xml_declaration=True)
    output=job.directory/'preview-output'; output.mkdir()
    write_build_config(runner,output,Path(game_data),[body],'ModLab-Custom-'+job.directory.name)
    settings_path=runner/'BodySlide.xml'
    settings=ET.parse(settings_path) if settings_path.exists() else ET.ElementTree(ET.Element('BodySlideConfig'))
    for key,value in {'SelectedOutfit':body,'SelectedPreset':name}.items():
        element=settings.getroot().find(key)
        if element is None: element=ET.SubElement(settings.getroot(),key)
        element.text=value
    frame=settings.getroot().find('BodySlideFrame')
    if frame is None: frame=ET.SubElement(settings.getroot(),'BodySlideFrame')
    frame.attrib.update(x='50',y='50',width='1100',height='800',maximized='false',previewVisible='true',previewPoppedOut='false')
    settings.write(settings_path,encoding='utf-8',xml_declaration=True)
    job.record.update(status='Ready to customize',custom_body=body,starting_preset=preset,
        custom_preset=name,custom_file=relative,custom_before=digest(file),custom_scope=label)
    job.save()
    return name


def collect_preset(job):
    file=job.executable.parent/job.record['custom_file']
    if not file.is_file(): raise ValueError('Save the named ModLab preset in BodySlide before returning.')
    if digest(file)==job.record['custom_before']: return None
    if file.stat().st_size>8*1024*1024: raise ValueError('The saved preset exceeds the inspection limit.')
    tree=ET.parse(file); nodes=tree.getroot().findall('Preset')
    if len(nodes)!=1 or nodes[0].get('name')!=job.record['custom_preset']:
        raise ValueError('The editing preset was renamed or replaced. Save the named ModLab preset; the original mods are unchanged.')
    node=nodes[0]
    if node.get('set')!=job.record['custom_body']:
        raise ValueError('A different body was selected while editing. Reopen Customize for the body you want.')
    seen=set()
    for slider in node.findall('SetSlider'):
        identity=(slider.get('name'),slider.get('size'))
        if not identity[0] or identity[1] not in {'small','big'} or identity in seen:
            raise ValueError('The saved shape has ambiguous slider values.')
        seen.add(identity)
        try: value=float(slider.get('value',''))
        except ValueError: raise ValueError('The saved shape contains an invalid slider value.') from None
        if not math.isfinite(value): raise ValueError('The saved shape contains an invalid slider value.')
    if not seen: raise ValueError('No saved slider values were found in this shape.')
    catalog=read_catalog(job.executable.parent)
    plan_build(catalog,[job.record['custom_body']],job.record['custom_preset'])
    # Only the selected preset is imported, never helper settings or preview builds.
    return ET.tostring(tree.getroot(),encoding='utf-8',xml_declaration=True)


def publish_preset(organizer, job, data, on_ready):
    import mobase
    from PyQt6.QtCore import QTimer
    from .body_workflow import check_body_context
    from .skse import require_game_closed
    require_game_closed(); check_body_context(organizer,job)
    if data != collect_preset(job): raise ValueError('The custom shape changed before it could be saved.')
    profile=job.record['profile_path']; name=output_name(job.record['profile'],profile,TOOL)
    target=Path(organizer.modsPath())/name
    relative=VIRTUAL+job.record['custom_file']; output=job.directory/'output'/relative
    output.parent.mkdir(parents=True); output.write_bytes(data)
    hashes={relative:digest(output)}
    config_source=organizer.resolvePath(OBODY_CONFIG)
    config_hash=None
    if config_source:
        config_path=Path(config_source)
        if not config_path.is_file() or config_path.stat().st_size>8*1024*1024:
            raise ValueError('The active body-distribution settings could not be preserved.')
        original_config=config_path.read_bytes(); config_hash=hashlib.sha256(original_config).hexdigest()
        changed=exclude_random_preset(original_config.decode('utf-8-sig'),job.record['custom_preset'])
        (job.directory/'original-obody.json').write_bytes(original_config)
        configured=job.directory/'output'/OBODY_CONFIG; configured.parent.mkdir(parents=True,exist_ok=True)
        configured.write_bytes(changed.encode('utf-8')); hashes[OBODY_CONFIG]=digest(configured)
        job.record['distribution_source']={'path':config_source,'sha256':config_hash}
    published=False
    def configuration_current():
        current=organizer.resolvePath(OBODY_CONFIG)
        if not config_source:
            if current: raise ValueError('Body distribution was enabled during customization. Reopen Customize to preserve its settings.')
            return
        if published and Path(current).resolve()==(target/OBODY_CONFIG).resolve():
            if digest(target/OBODY_CONFIG)!=hashes[OBODY_CONFIG]:
                raise ValueError('The prepared body-distribution settings changed.')
        elif Path(current).resolve()!=Path(config_source).resolve() or digest(Path(current))!=config_hash:
            raise ValueError('Body-distribution settings changed during customization.')

    original=organizer.resolvePath(relative)
    if original: raise ValueError('This custom preset already has an active provider. Start a new editing session.')
    if not target.exists():
        mod=organizer.createMod(mobase.GuessedString(name))
        if mod is None or Path(mod.absolutePath()).resolve()!=target.resolve():
            raise ValueError('MO2 could not create the expected custom-shape output.')
    projects={job.record['custom_preset']:dict(outputs=[relative],preset=job.record['starting_preset'],
        source_mod=job.record['project_sources'][job.record['custom_body']],scope=job.record['custom_scope'])}
    if config_source:
        projects['Custom presets excluded from random distribution']=dict(outputs=[OBODY_CONFIG],
            preset='Preserve existing distribution',source_mod=config_source)
    configuration_current()
    manifest=publish_output(target,job.directory,profile,projects,hashes,tool=TOOL)
    published=True
    job.record.update(status='Preset published; activation pending',hashes=hashes,installed_mod=name,
        installed_path=str(target),previous_output=manifest['previous_output']); job.save()
    finished=False
    def finish(error=None):
        nonlocal finished
        if finished: return
        finished=True
        job.record.update(status='Custom preset needs attention' if error else 'Custom preset installed and effective',error=str(error) if error else None)
        try: job.save()
        except OSError as problem: error=(str(error)+'\n' if error else '')+'Could not record the final preset result: '+str(problem)
        on_ready(target,str(error) if error else None)
    def context():
        require_game_closed()
        if organizer.profilePath()!=profile: raise ValueError('The profile changed during customization.')
    def verify():
        if finished: return
        try:
            context(); read_manifest(target,profile,TOOL)
            for name,checksum in hashes.items():
                effective=Path(organizer.resolvePath(name))
                if effective.resolve()!=(target/name).resolve() or digest(effective)!=checksum:
                    raise ValueError('A saved custom-shape file did not become effective in MO2: '+name)
        except Exception as error: finish(error)
        else: finish()
    def activate():
        if finished: return
        try:
            context(); configuration_current()
            effective=organizer.resolvePath(relative)
            if effective and Path(effective).resolve()!=(target/relative).resolve():
                raise ValueError('Another custom-preset provider became active during preparation.')
            mods=organizer.modList(); mods.setPriority(name,max(mods.priority(item) for item in mods.allMods()))
            if not mods.setActive(name,True): raise ValueError('MO2 could not enable the saved preset.')
            organizer.onNextRefresh(lambda:QTimer.singleShot(0,verify),False); organizer.refresh()
        except Exception as error: finish(error)
    try:
        organizer.onNextRefresh(lambda:QTimer.singleShot(0,activate),False); organizer.refresh()
    except Exception as error: finish(error)
    return target
