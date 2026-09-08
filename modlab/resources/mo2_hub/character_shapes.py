"""Saved actor-specific shape intent and evidence for OBody assignment."""
import json
import math
import os
from pathlib import Path
import re
from uuid import uuid4
import xml.etree.ElementTree as ET

from .body_choices import load_defaults
from .body_customization import OBODY_CONFIG
from .obody_config import plan_config
from .outputs import digest, output_name, publish_output, read_manifest
from .vfs import readable_path

TOOL = 'Character Shape Assignments'


class ShapeRequirementError(ValueError):
    def __init__(self, finding):
        self.finding=finding
        super().__init__(finding.title+'\n'+finding.action)


class ShapeBodyPreparationError(ValueError):
    body_setup=True


def choices_path(profile):
    return Path(profile)/'modlab-character-shapes.json'


def load_choices(profile):
    path = choices_path(profile)
    if not path.exists(): return dict(profile_path=str(profile), choices={}, applied=None)
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('profile_path') != str(profile) or not isinstance(data.get('choices'), dict):
        raise ValueError('Character shape choices belong to another profile or could not be read.')
    return data


def _write(profile, data, expected):
    if load_choices(profile) != expected:
        raise ValueError('Character shape choices changed in another window. Reopen this character before saving.')
    path = choices_path(profile)
    temporary = path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
        if load_choices(profile) != expected:
            raise ValueError('Character shape choices changed while saving.')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return data


def save_choice(profile, actor, choice, *, expected):
    if not re.fullmatch(r'[0-9A-Fa-f]{6}:[^:/\\]+\.(?:esm|esp|esl)', actor, re.I):
        raise ValueError('A stable character identity is required.')
    actor = actor[:6].upper()+actor[6:].casefold()
    data = dict(expected, choices=dict(expected['choices']))
    if choice is None: data['choices'].pop(actor, None)
    else:
        if any(not isinstance(choice.get(k), str) or not choice[k].strip() for k in ('name','body','preset')):
            raise ValueError('Choose an installed body and shape preset for this character.')
        data['choices'][actor] = {k: choice[k] for k in ('name','body','preset')}
    return _write(profile, data, expected)


def neutral_preset(runner, name, projects=None):
    if projects:
        from .author_neutral import inspect
        if inspect(runner,name,projects):return True
    matches = []
    for path in (Path(runner)/'SliderPresets').rglob('*.xml'):
        matches.extend(p for p in ET.parse(path).getroot().findall('Preset') if p.get('name') == name)
    if len(matches) != 1: return False
    sliders = matches[0].findall('SetSlider')
    if not sliders: return False
    seen = {}
    for node in sliders:
        identity = (node.get('name'), node.get('size'))
        if not identity[0] or identity[1] not in {'small','big'} or identity in seen: return False
        try: value = float(node.get('value',''))
        except ValueError: return False
        if not math.isfinite(value): return False
        seen[identity]=value
    if projects is None:return all(value==0 for value in seen.values())
    found={}
    for path in (Path(runner)/'SliderSets').rglob('*'):
        if path.suffix.casefold() not in {'.xml','.osp'}:continue
        for node in ET.parse(path).getroot().findall('SliderSet'):
            if node.get('name') in projects:
                if node.get('name') in found:return False
                found[node.get('name')]=node
    if not projects or set(found)!=set(projects):return False
    for node in found.values():
        for slider in node.findall('Slider'):
            if slider.get('zap','false').casefold()=='true':continue
            if slider.get('invert','false').casefold()=='true':return False
            for size in ('small','big'):
                try:value=seen.get((slider.get('name'),size),float(slider.get(size,'0')))
                except ValueError:return False
                if not math.isfinite(value) or value!=0:return False
    return True


def prepared_body(profile, character, choice, resolve, *, defaults=None):
    body = character.get('body', {})
    default = (load_defaults(profile) if defaults is None else defaults).get(body.get('sex'), {})
    if default.get('body') != choice['body'] or not default.get('build_record'):
        raise ShapeBodyPreparationError('Prepare '+choice['body']+' in Bodies & outfits before applying this character shape.')
    path = Path(default['build_record'])
    record = json.loads(path.read_text(encoding='utf-8'))
    if (record.get('profile_path') != str(profile) or not record.get('morphs') or
            not record.get('morph_links') or record.get('effective_output_issues') != []):
        raise ShapeBodyPreparationError('Prepare the body and matching outfits with body shape data before applying individual shapes.')
    if not record.get('neutral_shape_base') or not neutral_preset(path.parent/'runner', record.get('preset'),record.get('projects')):
        raise ShapeBodyPreparationError('Your shared body needs preparation for individual shapes. ModLab can build the matching body and outfits while keeping your chosen shared shape and character exceptions.')
    hashes = {p.casefold(): h for p,h in record.get('hashes',{}).items()}
    if not hashes: raise ValueError('The body preparation record has no checked files.')
    models = {p.casefold() for part in body.get('parts', []) for p in part['models']}
    if not models or not models.issubset(hashes):
        raise ValueError('This character uses a separate body. Apply its compatible shared body choice before assigning this shape.')
    inputs = {}
    for relative, checksum in hashes.items():
        current = resolve(relative)
        if not current or not readable_path(current).is_file() or digest(readable_path(current)) != checksum:
            raise ValueError('A prepared body or outfit changed. Recheck Bodies & outfits before applying character shapes.')
        inputs[relative] = dict(path=str(readable_path(current)),sha256=checksum)
    return inputs


def plan_assignments(upstream, choices, characters, available, *, light_plugins=()):
    return plan_config(upstream, {key: value['preset'] for key,value in choices.items()},
        characters, available, light_plugins=light_plugins)


def preset_inputs(organizer, choices):
    from .body_workflow import effective_files
    wanted={choice['preset'] for choice in choices.values()}
    if not wanted:return {}
    found={};inputs={}
    for relative,path in effective_files(organizer).items():
        if not relative.casefold().startswith('sliderpresets/') or path.suffix.casefold()!='.xml':continue
        for node in ET.parse(path).getroot().findall('Preset'):
            name=node.get('name')
            if name not in wanted:continue
            if name in found:raise ValueError('The selected shape preset has competing sources: '+name)
            found[name]=relative
            inputs['CalienteTools/BodySlide/'+relative]=dict(path=str(readable_path(path)),sha256=digest(path))
    if set(found)!=wanted:raise ValueError('A selected character shape preset is missing. Restore its source or choose another shape.')
    return inputs


def apply_choices(organizer, characters, catalog, on_ready, *, prepared_defaults=None, on_prepared=None):
    """Publish checked actor assignments; never rewrite source configs or defaults."""
    import mobase
    from PyQt6.QtCore import QTimer
    from .body_choices import compatible_presets
    from .inspection import collect_setup
    from .shape_support import inspect_support
    from .skse import require_game_closed
    require_game_closed()
    profile = organizer.profilePath(); selected = load_choices(profile)
    setup = collect_setup(organizer, version_reader=mobase.getFileVersion)
    needs = inspect_support(setup, organizer.resolvePath, requested=True)
    if needs: raise ShapeRequirementError(needs[0])
    # The same native/runtime inspection used by launch must not be bypassed by
    # installing only the expected filenames for this optional helper.
    from .foundations import inspect_foundations
    blockers = [f for f in inspect_foundations(setup, organizer.resolvePath) if f.level == 'Blocked']
    if blockers: raise ShapeRequirementError(blockers[0])
    saved_defaults=load_defaults(profile)
    defaults=saved_defaults if prepared_defaults is None else prepared_defaults
    managed={sex:{k:v for k,v in value.items() if k!='sex'} for sex,value in defaults.items() if value.get('individual_shapes')}
    available = {}; inputs = {}
    for actor, choice in selected['choices'].items():
        if actor not in characters: raise ValueError('The selected character is no longer available: '+choice['name'])
        available[actor] = compatible_presets(catalog, choice['body'])
        inputs.update(prepared_body(profile, characters[actor], choice, organizer.resolvePath,defaults=defaults))
    for sex,value in managed.items():
        representative=dict(body=dict(sex=sex,parts=[dict(models=catalog.projects[value['body']].outputs)]))
        inputs.update(prepared_body(profile,representative,value,organizer.resolvePath,defaults=defaults))
    inputs.update(preset_inputs(organizer,dict(selected['choices'],**{'default-'+sex:value for sex,value in managed.items()})))
    from .shape_preparation import upstream,plan_defaults,check_archive_inputs
    current,current_hash,source=upstream(organizer,selected)
    target = Path(organizer.modsPath())/output_name(organizer.profile().name(),profile,TOOL)
    # Actor identities are local IDs. The config planner needs the ESL owner
    # flags only when existing upstream rules use a full light-plugin ID.
    light_plugins = []
    for plugin in setup.plugins:
        path = organizer.resolvePath(plugin.name)
        if path:
            with readable_path(path).open('rb') as stream: header=stream.read(24)
            if len(header)!=24 or header[:4]!=b'TES4': raise ValueError('A character source plugin header could not be read: '+plugin.name)
            if int.from_bytes(header[8:12],'little') & 0x200: light_plugins.append(plugin.name)
    directory = Path(organizer.modsPath()).parent/'builds/character-shapes'/uuid4().hex[:12]
    directory.mkdir(parents=True)
    baseline=plan_defaults(organizer,catalog,characters,managed,source,directory)
    if baseline:inputs.update(baseline['inputs'])
    planned = plan_assignments(baseline['text'] if baseline else source,selected['choices'],characters,available,light_plugins=light_plugins)
    file = directory/'output'/OBODY_CONFIG; file.parent.mkdir(parents=True)
    file.write_text(planned.text,encoding='utf-8')
    hashes = {OBODY_CONFIG:digest(file)}
    record = dict(profile_path=profile,choices=selected['choices'],upstream=source,source=str(current),
        source_sha256=current_hash,hashes=hashes,inputs=inputs,status='Prepared',installed_path=str(target),
        baseline_defaults=managed,archive_inputs=baseline['archive_inputs'] if baseline else {},
        preset_inventory=baseline['preset_inventory'] if baseline else None)
    record_path = directory/'operation.json'
    def save(): record_path.write_text(json.dumps(record,indent=2),encoding='utf-8')
    def context(published=False):
        require_game_closed()
        if organizer.profilePath()!=profile or load_choices(profile)!=selected:
            raise ValueError('The profile or character choices changed during preparation.')
        if load_defaults(profile)!=saved_defaults:
            raise ValueError('The shared body choices changed during preparation.')
        check_archive_inputs(organizer,record['archive_inputs'])
        for relative, evidence in inputs.items():
            active=organizer.resolvePath(relative)
            if (not active or readable_path(active).resolve()!=Path(evidence['path']).resolve() or
                    not readable_path(active).is_file() or digest(readable_path(active))!=evidence['sha256']):
                raise ValueError('A prepared body, outfit or shape preset changed during assignment.')
        active=readable_path(organizer.resolvePath(OBODY_CONFIG))
        if published and active.resolve()==readable_path(target/OBODY_CONFIG).resolve():
            if digest(active)!=hashes[OBODY_CONFIG]: raise ValueError('The prepared character configuration changed.')
        elif active.resolve()!=current.resolve() or digest(active)!=current_hash:
            raise ValueError('The active character shape configuration changed during preparation.')
    save(); context()
    if on_prepared:on_prepared(record_path)
    if not target.exists():
        mod=organizer.createMod(mobase.GuessedString(target.name))
        if mod is None or Path(mod.absolutePath()).resolve()!=target.resolve():
            raise ValueError('MO2 could not create the character shape output.')
    projects={'Individual character shapes':dict(outputs=[OBODY_CONFIG],
        preset='; '.join(choice['name']+': '+choice['preset']+' ['+actor+']' for actor,choice in selected['choices'].items()) or 'Restore upstream choices',
        source_mod=str(current),characters=selected['choices'])}
    publish_output(target,directory,profile,projects,hashes,tool=TOOL)
    record['status']='Published; activation pending';save()
    finished=False
    def finish(error=None):
        nonlocal finished
        if finished:return
        finished=True
        record.update(status='Needs attention' if error else 'Assignments installed and effective',error=str(error) if error else None)
        try:
            save()
            if not error:_write(profile,dict(selected,applied=dict(record,record_path=str(record_path))),selected)
        except Exception as problem:error=str(problem)
        on_ready(target,str(error) if error else None)
    def verify():
        if finished:return
        try:
            context(True);read_manifest(target,profile,TOOL)
            if readable_path(organizer.resolvePath(OBODY_CONFIG)).resolve()!=readable_path(target/OBODY_CONFIG).resolve():
                raise ValueError('The character shape configuration did not become effective in MO2.')
        except Exception as error:finish(error)
        else:finish()
    def activate():
        if finished:return
        try:
            context(True)
            mods=organizer.modList();mods.setPriority(target.name,max(mods.priority(n) for n in mods.allMods()))
            if not mods.setActive(target.name,True):raise ValueError('MO2 could not enable character shape assignments.')
            organizer.onNextRefresh(lambda:QTimer.singleShot(0,verify),False);organizer.refresh()
        except Exception as error:finish(error)
    try:
        organizer.onNextRefresh(lambda:QTimer.singleShot(0,activate),False);organizer.refresh()
    except Exception as error:finish(error)
    return record_path


def inspect_choices(organizer):
    from .assessment import Finding
    try:
        from .shape_preparation import pending_finding,check_archive_inputs
        pending=pending_finding(organizer.profilePath())
        if pending:return (pending,)
        state=load_choices(organizer.profilePath());applied=state.get('applied')
        managed={sex:{k:v for k,v in value.items() if k!='sex'} for sex,value in load_defaults(organizer.profilePath()).items() if value.get('individual_shapes')}
        if not state['choices'] and not applied and not managed:return ()
        if not applied or state['choices']!=applied.get('choices'):
            names=', '.join(c['name'] for c in state['choices'].values()) or 'Remove saved shape overrides'
            return (Finding('Review','character-shapes-pending','Apply saved character shapes',names,
                'Open the character’s shape panel to finish preparation and apply these choices.',
                'The choices are saved; the in-game assignments have not yet been updated.'),)
        issues=[]
        if managed!=applied.get('baseline_defaults',{}):issues.append('The shared body or default shape changed.')
        if applied.get('preset_inventory') is not None:
            from .body_workflow import effective_files
            inventory=set()
            for relative,path in effective_files(organizer).items():
                if relative.casefold().startswith('sliderpresets/') and path.suffix.casefold()=='.xml':
                    inventory.update(node.get('name') for node in ET.parse(path).getroot().findall('Preset'))
            if sorted(inventory)!=applied['preset_inventory']:issues.append('Available shape presets changed. Reconcile the default and character assignments.')
        try:check_archive_inputs(organizer,applied.get('archive_inputs',{}))
        except (OSError,ValueError) as error:issues.append(str(error))
        for relative,expected in applied['hashes'].items():
            active=organizer.resolvePath(relative)
            target=readable_path(Path(applied['installed_path'])/relative)
            if not active or readable_path(active).resolve()!=target.resolve() or not target.is_file() or digest(target)!=expected:
                issues.append('Character shape assignments were changed, disabled or replaced.')
        for relative,evidence in applied.get('inputs',{}).items():
            active=organizer.resolvePath(relative)
            if (not active or readable_path(active).resolve()!=Path(evidence['path']).resolve() or
                    not readable_path(active).is_file() or digest(readable_path(active))!=evidence['sha256']):
                issues.append('A body, outfit or preset used by character shapes changed: '+relative)
        if issues:
            return (Finding('Review','character-shapes-stale','Review changed character shapes','\n'.join(issues),
                'Review your current body and outfit setup, then reapply the character choices you want to keep.',
                'ModLab has retained your choices and has not overwritten the outside change.'),)
        return (Finding('Info','character-shapes-current','Character shape assignments applied',
            ', '.join(c['name']+': '+c['preset'] for c in state['choices'].values()) or 'Upstream shape choices restored.',
            'Check appearance in game. Existing saves can retain an earlier OBody assignment.',
            'The generated configuration and its recorded body/outfit inputs are effective. This is not a gameplay check.'),)
    except (OSError,ValueError,KeyError,TypeError,ET.ParseError) as error:
        return (Finding('Unknown','character-shapes-unreadable','Character shape choices need attention',str(error),
            'Open character choices to inspect the saved request before preparing again.'),)
