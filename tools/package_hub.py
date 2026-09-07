"""Build the MO2 plugin ZIP without bundling games, mods, helpers or user data."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import zipfile


def build(destination):
    root = Path(__file__).resolve().parents[1]
    entries = {}
    for path in sorted((root / 'modlab/resources/mo2_hub').glob('*.py')):
        content = path.read_bytes()
        ast.parse(content, filename=str(path))
        entries['plugins/modlab_hub/' + path.name] = content
    if 'plugins/modlab_hub/__init__.py' not in entries:
        raise ValueError('MO2 plugin entry point is missing.')
    entries['ModLab-Hub-README.md'] = (root / 'docs/HUB_QUICKSTART.md').read_bytes()
    files = {name: hashlib.sha256(content).hexdigest() for name, content in entries.items()}
    identity = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    entries['ModLab-Hub-build.json'] = (json.dumps({
        'name': 'ModLab Hub', 'version': '0.1.0-alpha', 'source_id': identity,
        'host': 'Mod Organizer 2.5.2',
        'qualification': 'Development snapshot; see README for outstanding tests.',
        'files': files,
    }, indent=2) + '\n').encode()
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents replacing a previous delivery or arbitrary file.
    with destination.open('xb') as stream:
        with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in entries.items():
                info = zipfile.ZipInfo(name, date_time=(2026, 9, 6, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, content)
    with zipfile.ZipFile(destination) as archive:
        if set(archive.namelist()) != set(entries):
            raise ValueError('Package members differ from the selected sources.')
        for name, content in entries.items():
            if archive.read(name) != content:
                raise ValueError('Package readback failed: ' + name)
    return {'archive': str(destination), 'source_id': identity,
            'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
            'modules': len(entries) - 2, 'members': len(entries)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.output), indent=2))
