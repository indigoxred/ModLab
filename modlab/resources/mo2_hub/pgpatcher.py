"""PGPatcher 1.3.0 settings and file receipts, based on its tagged source.

Sources: hakasapl/PGPatcher tag 1.3.0, PGConfig.cpp/.hpp, main.cpp,
PGPatcher.cpp; maintainer Basic Usage and Output wiki pages (2026-09-06).
"""
from dataclasses import dataclass
import configparser
import json
from pathlib import Path
import re
import zlib

from .bodyslide import relative_path
from .outputs import digest
from .synthesis import plugin_masters

SUPPORTED_EXE = 'bb629042273b5e2380aaafffceff015a4b07307a73c2fbec1eddf3c5af2f669e'
RENDERERS = ('Community Shaders', 'ENB', 'Mesh fixes only')
BLOCKLIST = ('*\\cameras\\*', '*\\dyndolod\\*', '*\\lod\\*', '*_lod_*', '*_lod.*', '*\\markers\\*')
VANILLA_BSAS = tuple(f'Skyrim - Textures{n}.bsa' for n in range(9)) + (
    'Project Clarity AIO Half Res Packed.bsa', 'Project Clarity AIO Half Res Packed - Textures.bsa',
    *(f'Project Clarity AIO Half Res Packed{n} - Textures.bsa' for n in range(7)))


def make_config(choices, game_root, mo2_instance, output, store, *, language='English'):
    renderer = choices.get('renderer')
    if renderer not in RENDERERS:
        raise ValueError('Choose the graphics setup before generating materials.')
    if store not in {'Steam', 'GOG'}:
        raise ValueError('PGPatcher requires an identified Steam or GOG Skyrim installation.')
    if choices.get('pbr') and renderer != 'Community Shaders':
        raise ValueError('TruePBR requires Community Shaders; it cannot be enabled for ENB or mesh-only repairs.')
    for key in ('pbr', 'fix_lighting', 'upgrade_complex'):
        if key in choices and type(choices[key]) is not bool:
            raise ValueError('Invalid graphics choice: ' + key)
    output = Path(output).resolve()
    if output.is_relative_to((Path(game_root) / 'Data').resolve()):
        raise ValueError('Generated output must be outside game Data and its subdirectories.')
    materials = renderer != 'Mesh fixes only'
    if not materials and not choices.get('fix_lighting'):
        raise ValueError('Select a mesh repair or a material setup; no operation was chosen.')
    return {'ui': {'language': 'en'}, 'params': {
        'game': {'dir': str(Path(game_root)), 'type': 0 if store == 'Steam' else 1},
        'modmanager': {'type': 2, 'mo2instancedir': str(Path(mo2_instance)), 'mo2useloosefileorder': True},
        'output': {'dir': str(output), 'zip': False, 'pluginlang': language},
        'processing': {'multithread': True, 'devmode': False, 'enabledebuglogging': False,
            'enabletracelogging': False, 'blocklist': list(BLOCKLIST), 'vanillabsalist': list(VANILLA_BSAS)},
        'prepatcher': {'fixmeshlighting': choices.get('fix_lighting', False)},
        'shaderpatcher': {'parallax': materials, 'complexmaterial': materials, 'truepbr': choices.get('pbr', False)},
        'shadertransforms': {'parallaxtocm': materials and choices.get('upgrade_complex', False)},
        'postpatcher': {'disableprepatchedmaterials': materials, 'fixsss': False, 'hairflowmap': False},
    }}


def crc32(path):
    value = 0
    with Path(path).open('rb') as stream:
        while block := stream.read(1024 * 1024):
            value = zlib.crc32(block, value)
    return value


def transformed_body_files(body, body_manifest, graphics, graphics_manifest, resolve_path):
    """Match a checked downstream transformation to its exact retained body mesh.

    Caller must first establish that this graphics build still matches its inputs.
    A CRC is a tool receipt, not a replacement for the retained SHA256 identities.
    """
    diff = graphics / 'ParallaxGen_Diff.json'
    if not diff.is_file() or digest(diff) != graphics_manifest['hashes'].get('ParallaxGen_Diff.json'):
        return set()
    receipt = {relative_path(name).casefold(): values
               for name, values in json.loads(diff.read_text(encoding='utf-8-sig')).items()}
    graphics_hashes = {name.casefold(): checksum for name, checksum in graphics_manifest['hashes'].items()}
    valid = set()
    for name, checksum in body_manifest['hashes'].items():
        key = name.casefold()
        values = receipt.get(key)
        if not isinstance(values, dict) or key not in graphics_hashes:
            continue
        source, output = body / relative_path(name), graphics / relative_path(name)
        effective = Path(resolve_path(name))
        if (source.is_file() and output.is_file() and effective.resolve() == output.resolve()
                and digest(source) == checksum and digest(output) == graphics_hashes[key]
                and values.get('crc32original') == crc32(source) and values.get('crc32patched') == crc32(output)):
            valid.add(key)
    return valid


@dataclass(frozen=True)
class PGResult:
    hashes: dict
    plugins: tuple
    checked_meshes: int
    warnings: tuple


def review_warnings(warnings, game_root, resolve_path):
    """Explain two base-game archive cases; retain every other warning for review.

    https://github.com/hakasapl/PGPatcher/wiki/Error-Message-Guide permits
    ignoring these archive warnings unless the archive should be loading.
    Do not generalize this exception to mod archives or change archive loading.
    """
    root = Path(game_root)
    defaults = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        defaults.read(root / 'Skyrim_Default.ini', encoding='utf-8-sig')
        default_archives = {name.strip().casefold() for key, value in defaults.items('Archive')
                            if key.casefold() in {'sresourcearchivelist', 'sresourcearchivelist2'}
                            for name in value.split(',')}
    except (OSError, UnicodeError, configparser.Error):
        default_archives = set()
    held, explained = [], []
    for warning in warnings:
        message = re.sub(r'^.*?\[(?:warning|warn)\]\s*', '', warning, flags=re.I).strip()
        note = None
        if message == 'Output directory is empty. No files were generated.':
            note = 'The helper reported no generated files; this is not proof that a visual feature works.'
        elif message == 'BSA file MarketplaceTextures.bsa not loaded by any active plugin or INI.':
            archive = root / 'Data/MarketplaceTextures.bsa'
            effective = resolve_path(archive.name)
            if archive.is_file() and effective and Path(effective).resolve() == archive.resolve():
                note = ('The base-game MarketplaceTextures archive is outside this profile’s plugin/INI archive list. '
                        'PGPatcher skipped it. No mod archive was skipped by this exception; archive loading is unchanged.')
        elif message.startswith('BSA is in INI but does not exist: '):
            missing = Path(message.removeprefix('BSA is in INI but does not exist: '))
            optional = root / 'Data/Skyrim - Patch.bsa'
            if (missing.resolve() == optional.resolve() and not optional.exists()
                    and not resolve_path(optional.name) and optional.name.casefold() in default_archives):
                note = ('Skyrim - Patch.bsa is absent, and its reference is also present in this game’s shipped default INI. '
                        'This default archive warning does not require cleaning or a replacement mod. The INI is unchanged.')
        if note:
            explained.append({'warning': warning, 'explanation': note})
        else:
            held.append(warning)
    return tuple(held), tuple(explained)


def verify_output(output, log, exit_code, active_plugins):
    if exit_code != 0:
        raise ValueError(f'PGPatcher exited with code {exit_code}. Output was not accepted.')
    errors = re.findall(r'^.*\[(?:error|critical)\].*$', log, re.I | re.M)
    if errors:
        raise ValueError('PGPatcher reported errors:\n' + '\n'.join(errors))
    if not re.search(r'PGPatcher took \d+ seconds to complete', log):
        raise ValueError('PGPatcher has no completion receipt. A zero exit alone is insufficient.')
    warnings = tuple(re.findall(r'^.*\[(?:warning|warn)\].*$', log, re.I | re.M))
    output = Path(output)
    hashes, plugins, seen = {}, {}, set()
    for path in sorted(output.rglob('*')):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('Linked paths are not accepted in generated output: ' + str(path))
        if not path.is_file():
            continue
        name = relative_path(path.relative_to(output).as_posix())
        key = name.casefold()
        if key in seen:
            raise ValueError('Duplicate output identity: ' + name)
        seen.add(key)
        if re.fullmatch(r'(?:pgpatcher|pg_\d+)\.esp', key):
            plugins[name] = plugin_masters(path)
        elif key.startswith('meshes/') and key.endswith('.nif'):
            with path.open('rb') as stream:
                if not stream.read(96).startswith(b'Gamebryo File Format, Version 20.2.0.7'):
                    raise ValueError('Generated mesh has an unexpected Skyrim SE header: ' + name)
        elif key.startswith('textures/') and key.endswith('.dds'):
            with path.open('rb') as stream:
                header = stream.read(128)
            if len(header) != 128 or header[:4] != b'DDS ':
                raise ValueError('Generated texture is incomplete: ' + name)
        elif key == 'parallaxgen_diff.json' or (key.startswith('lightplacer/') and key.endswith('.json')):
            json.loads(path.read_text(encoding='utf-8-sig'))
        else:
            raise ValueError('Unexpected PGPatcher output file: ' + name)
        hashes[name] = digest(path)
    checked_meshes = 0
    diff = output / 'ParallaxGen_Diff.json'
    if diff.is_file():
        receipt = json.loads(diff.read_text(encoding='utf-8-sig'))
        if not isinstance(receipt, dict):
            raise ValueError('The mesh checksum receipt is not an object.')
        for name, values in receipt.items():
            relative = relative_path(name)
            if not relative.casefold().startswith('meshes/') or not relative.casefold().endswith('.nif'):
                raise ValueError('Unexpected path in mesh checksum receipt: ' + relative)
            checksum = values.get('crc32patched') if isinstance(values, dict) else None
            target = output / relative
            if type(checksum) is not int or not target.is_file() or crc32(target) != checksum:
                raise ValueError('Generated mesh checksum does not match its receipt: ' + relative)
            checked_meshes += 1
        if not receipt and set(hashes) == {'ParallaxGen_Diff.json'}:
            hashes = {}  # An empty metadata file is not a generated game feature.
    order, known = [], {name.casefold() for name in active_plugins}
    pending = dict(plugins)
    while pending:
        ready = [name for name, masters in pending.items() if all(master.casefold() in known for master in masters)]
        if not ready:
            raise ValueError('Generated plugins have missing or cyclic masters: ' + ', '.join(pending))
        for name in ready:
            order.append(name); known.add(name.casefold()); del pending[name]
    if not hashes and 'Output directory is empty. No files were generated.' not in log:
        raise ValueError('PGPatcher produced no output and did not report a no-change result.')
    return PGResult(hashes, tuple(order), checked_meshes, warnings)
