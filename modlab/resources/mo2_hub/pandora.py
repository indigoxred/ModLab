"""Pandora 4.4.0 patch choices and output checks, based on its tagged source.

Source: Monitor221hz/Pandora-Behaviour-Engine-Plus, v4.4.0-beta:
Paths/OutputPaths.cs, Mods/ModSettingsService.cs, Models/Patch.IO/Skyrim64/
BasePackFileExporter.cs, Models/Patch.Skyrim64/SkyrimPatcher.cs.
An engine receipt establishes generation, not in-game animation compatibility.
"""

from dataclasses import dataclass
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from .outputs import digest


@dataclass(frozen=True)
class Patch:
    code: str
    name: str
    source_mod: str
    metadata: Path
    site: str = ''


@dataclass(frozen=True)
class BuildResult:
    hashes: dict
    animations: int | None
    warnings: tuple


def read_patch(path, source_mod):
    path = Path(path)
    text = path.read_text(encoding='utf-8-sig')
    if path.suffix.casefold() == '.xml':
        if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
            raise ValueError(f'Unsupported patch metadata: {path}')
        root = ET.fromstring(text)
        if root.tag != 'mod':
            raise ValueError(f'Unrecognized Pandora patch metadata: {path}')
        code, name, site = root.get('code', ''), root.findtext('name', ''), root.findtext('site', '')
    else:
        properties = {}
        for line in text.splitlines():
            if '=' in line:
                key, value = line.split('=', 1)
                key = key.strip().casefold()
                if key in properties:
                    raise ValueError(f'Duplicate patch metadata field: {path}: {key}')
                properties[key] = value.strip()
        if not all(key in properties for key in ('name', 'author', 'site')):
            raise ValueError(f'Incomplete Nemesis patch metadata: {path}')
        code, name, site = path.parent.name, properties['name'], properties['site']
    if not code.strip() or not name.strip() or any(c in code for c in '\r\n'):
        raise ValueError(f'Patch name or code is missing: {path}')
    return Patch(code, name, source_mod, path, site)


def selection_entries(patches, selected):
    selected = list(selected)
    codes = {patch.code for patch in patches}
    if len(codes) != len(patches) or len({c.casefold() for c in codes}) != len(codes):
        raise ValueError('Multiple patches have the same identifier. Resolve their providers before generating.')
    if set(selected) - codes:
        raise ValueError('A selected patch is no longer available: ' + ', '.join(sorted(set(selected) - codes)))
    # Include explicit false entries: Pandora otherwise enables all patches on a fresh run.
    order = list(dict.fromkeys(selected)) + [p.code for p in patches if p.code not in selected]
    return [{'code': code, 'active': code in selected, 'priority': i + 1} for i, code in enumerate(order)]


def verify_output(output, selected, exit_code, *, fnis_codes=()):
    output = Path(output).resolve()
    if exit_code != 0:
        raise ValueError(f'Pandora exited with code {exit_code}. Previous output remains unchanged.')
    log_path = output / 'Engine.log'
    log = log_path.read_text(encoding='utf-8-sig', errors='replace') if log_path.is_file() else ''
    failures = [line for line in log.splitlines() if re.search(
        r'^(?:ERROR|FATAL)\s*:|>\s*FAILED\b|CRITICAL FAILURE|Critical error:|Update failed', line, re.I)]
    if failures:
        raise ValueError('Pandora reported a problem; output was not applied.\n' + '\n'.join(failures[:12]))
    count = re.search(r'\b(\d+) total animations added\.', log)
    # 4.4.0 filters UiInfo (including count/finished) out of Engine.log.
    # GetPostRunMessages emits the base patch receipt only after successful RunAsync.
    if (not re.search(r'Pandora Mod \d+ : Pandora Base - v\.1\.0\.0\s*$', log, re.M)
            or 'INFO : Mod settings saved.' not in log):
        raise ValueError('Pandora completion evidence is missing. Close it after a completed build and inspect Engine.log.')
    receipt_path = output / 'Pandora_Engine/ActiveMods.json'
    entries = json.loads(receipt_path.read_text(encoding='utf-8-sig')) if receipt_path.is_file() else []
    if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
        raise ValueError('Pandora selected patch receipt could not be read.')
    actual = {e.get('code') for e in entries if e.get('active') is True}
    if actual != set(selected):
        raise ValueError('Pandora selected patch receipt does not match the requested choices. Output was not applied.')
    active_entries = sorted((e for e in entries if e.get('active') is True), key=lambda e: e.get('priority', -1))
    if [e.get('code') for e in active_entries] != list(selected):
        raise ValueError('Pandora patch priority does not match the requested order. Output was not applied.')
    for code, name in selected.items():
        if not re.search(r'Pandora Mod \d+ : ' + re.escape(name) + r' - v\.', log):
            raise ValueError(f'The selected patch was not confirmed in the engine result: {name} [{code}]')
    for code in fnis_codes:
        if not re.search(r'FNIS Mod \d+ : ' + re.escape(code) + r'\s*$', log, re.M):
            raise ValueError(f'An installed FNIS animation list was not confirmed by Pandora: {code}. '
                             'Check that this mod and its required behavior files support this engine.')
    metadata = output / 'Pandora_Engine/PreviousOutput.txt'
    if not metadata.is_file():
        raise ValueError('Pandora export receipt is missing.')
    exported = []
    for line in metadata.read_text(encoding='utf-8-sig').splitlines():
        if not line.strip():
            continue
        path = Path(line.strip()).resolve()
        try:
            path.relative_to(output)
        except ValueError:
            raise ValueError('Pandora export receipt points outside this build output.') from None
        if not path.is_file():
            raise ValueError(f'An exported behavior file is missing: {path.name}')
        exported.append(path)
    if not exported:
        raise ValueError('Pandora produced no exported behavior files.')
    hashes = {}
    for path in output.rglob('*'):
        if path.is_symlink():
            raise ValueError('Generated output contains a linked path.')
        if not path.is_file():
            continue
        relative = path.relative_to(output).as_posix()
        lower = relative.casefold()
        if lower == 'engine.log' or lower.startswith('pandora_engine/'):
            continue  # Retain helper diagnostics/settings in the build, not the game.
        if lower.startswith('meshes/') and path.suffix.casefold() in {'.hkx', '.txt'}:
            if path.stat().st_size == 0:
                raise ValueError(f'Generated file is empty: {relative}')
            if path.suffix.casefold() == '.hkx':
                with path.open('rb') as stream:
                    if stream.read(8) != bytes.fromhex('57e0e05710c0c010'):
                        raise ValueError(f'Generated behavior has an invalid Havok header: {relative}')
        elif lower == 'skse/plugins/fnis_aa/config.json':
            config = json.loads(path.read_text(encoding='utf-8-sig'))
            if (not isinstance(config, dict) or type(config.get('crc')) is not int
                    or not isinstance(config.get('mods'), list)
                    or not all(isinstance(config.get(key), str) for key in ('fnis_version', 'fnis_creature_version'))):
                raise ValueError('Generated FNIS AA configuration is incomplete or malformed.')
        elif lower == 'fnis.esp':
            with path.open('rb') as stream:
                if stream.read(4) != b'TES4':
                    raise ValueError('Generated FNIS.esp has an invalid plugin header.')
        else:
            raise ValueError(f'Unrecognized generated file; review it before installation: {relative}')
        hashes[relative] = digest(path)
    if any(path.relative_to(output).as_posix() not in hashes for path in exported):
        raise ValueError('An exported file is not an accepted game asset.')
    warnings = tuple(line for line in log.splitlines() if line.startswith('WARN')
                     and 'Previous output file not found' not in line)
    if warnings:
        raise ValueError('Pandora warnings need review before applying output.\n' + '\n'.join(warnings[:12]))
    return BuildResult(hashes, int(count.group(1)) if count else None, warnings)
