"""Read BodySlide project declarations; validate a requested build, not gameplay."""

from dataclasses import dataclass, field
import hashlib
from pathlib import Path, PureWindowsPath
import xml.etree.ElementTree as ET


def relative_path(value):
    path = PureWindowsPath(value)
    if (not value or path.drive or path.root or '..' in path.parts or
            any(':' in p or p.endswith(('.', ' ')) for p in path.parts)):
        raise ValueError(f'Unsupported output path: {value!r}')
    return path.as_posix()


@dataclass
class Project:
    name: str
    outputs: tuple
    groups: set = field(default_factory=set)
    source_file: str = ''


@dataclass
class Catalog:
    projects: dict
    presets: dict
    groups: dict


def read_catalog(root):
    return read_catalog_files({p.relative_to(root).as_posix(): p for p in root.rglob('*') if p.is_file()})


def read_catalog_files(files):
    """Read MO2's effective declarations without copying a complete helper installation."""
    projects, presets, groups = {}, {}, {}
    for relative, path in sorted(files.items()):
        if not relative.casefold().startswith('slidersets/') or path.suffix.casefold() not in {'.xml', '.osp'}:
            continue
        for node in ET.parse(path).getroot().findall('SliderSet'):
            name = node.get('name', '')
            if not name:
                raise ValueError(f'Unnamed project in {path.name}.')
            if name in projects:
                raise ValueError(f'Duplicate project name: {name}. Choose its intended source mod first.')
            out = node.find('OutputFile')
            if out is None or not out.text:
                raise ValueError(f'{name} has no declared output file.')
            folder = node.findtext('OutputPath') or ''
            stem = relative_path((folder + '/' if folder else '') + out.text)
            weights = out.get('GenWeights', 'false').casefold()
            if weights not in {'true', 'false'}:
                raise ValueError(f'{name} has an unsupported weight setting.')
            suffixes = ('_0.nif', '_1.nif') if weights == 'true' else ('.nif',)
            projects[name] = Project(name, tuple(stem + suffix for suffix in suffixes),
                                     source_file=relative)
    for relative, path in sorted(files.items()):
        if not relative.casefold().startswith('slidergroups/') or path.suffix.casefold() != '.xml':
            continue
        for node in ET.parse(path).getroot().findall('Group'):
            group = node.get('name', '')
            members = groups.setdefault(group, set())
            for member in node.findall('Member'):
                name = member.get('name')
                if name in projects:
                    members.add(name)
                    projects[name].groups.add(group)
    for relative, path in sorted(files.items()):
        if not relative.casefold().startswith('sliderpresets/') or path.suffix.casefold() != '.xml':
            continue
        for node in ET.parse(path).getroot().findall('Preset'):
            name = node.get('name', '')
            if name in presets:
                raise ValueError(f'Duplicate preset name: {name}. Resolve the competing preset files first.')
            presets[name] = (node.get('set', ''), {g.get('name') for g in node.findall('Group')})
    return Catalog(projects, presets, groups)


def plan_build(catalog, selected, preset):
    if not selected:
        raise ValueError('Select the projects you want to build.')
    if preset not in catalog.presets:
        raise ValueError('Select an installed preset.')
    preset_set, preset_groups = catalog.presets[preset]
    outputs = {}
    for name in selected:
        if name not in catalog.projects:
            raise ValueError(f'Project is no longer available: {name}')
        project = catalog.projects[name]
        if name != preset_set and not project.groups.intersection(preset_groups):
            raise ValueError(f'The preset "{preset}" has no declared match for "{name}". '
                             'Choose a preset for that project/family, or inspect it in BodySlide.')
        for output in project.outputs:
            key = output.casefold()
            if key in outputs:
                raise ValueError(f'"{name}" and "{outputs[key][0]}" write the same output: {output}. '
                                 'Select one variant for that body or outfit.')
            outputs[key] = (name, output)
    return tuple(value[1] for value in outputs.values())


def verify_output(root, expected):
    if not expected:
        raise ValueError('No output was requested.')
    hashes = {}
    for relative in expected:
        path = root / relative_path(relative)
        if not path.is_file():
            raise ValueError(f'Missing generated mesh: {relative}')
        data = path.read_bytes()
        if len(data) < 128 or not data.startswith(b'Gamebryo File Format, Version '):
            raise ValueError(f'Invalid or incomplete generated mesh: {relative}')
        hashes[relative] = hashlib.sha256(data).hexdigest()
    actual = {p.relative_to(root).as_posix().casefold() for p in root.rglob('*') if p.is_file()}
    if actual != {p.casefold() for p in expected}:
        raise ValueError('The build produced unexpected files. Inspect the retained output before installing it.')
    return hashes


def write_build_config(runner, output, game_data, selected, group_name):
    config = ET.parse(runner / 'Config.xml')
    root = config.getroot()
    for key, value in {'TargetGame': '4', 'GameDataPath': str(game_data),
                       'OutputDataPath': str(output), 'ProjectPath': str(runner),
                       'AppDir': str(runner), 'WarnMissingGamePath': 'true'}.items():
        node = root.find(key)
        if node is None:
            node = ET.SubElement(root, key)
        node.text = value
    config.write(runner / 'Config.xml', encoding='utf-8', xml_declaration=True)
    group_root = ET.Element('SliderGroups')
    group = ET.SubElement(group_root, 'Group', name=group_name)
    for name in selected:
        ET.SubElement(group, 'Member', name=name)
    target = runner / 'SliderGroups' / 'ModLab Selected Build.xml'
    target.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(group_root).write(target, encoding='utf-8', xml_declaration=True)
