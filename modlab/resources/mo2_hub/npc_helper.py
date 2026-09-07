"""Obtain and build a pinned NPC Plugin Chooser from its author's repository."""
import json
from pathlib import Path
import subprocess
from urllib.request import urlopen
from urllib.parse import quote

from .outputs import digest
from .runtime import ensure_sdk10, sdk_environment

REVISION = '50112de1482cd251a43883a3c63e7ed83fe3d773'
SOURCE = 'https://raw.githubusercontent.com/Piranha91/NPC-Plugin-Chooser/' + REVISION + '/NPC-Plugin-Chooser/'
FILES = ('NPC-Plugin-Chooser.csproj', 'Program.cs', 'BSAHandler.cs', 'NifHandler.cs',
         'Settings/PatcherSettings.cs', 'Data/Paths To Ignore.json', 'Data/Warnings To Suppress.json')


def ensure_helper(tools_root):
    tools_root = Path(tools_root).resolve()
    sdk = ensure_sdk10(tools_root)
    root = tools_root / ('npc-plugin-chooser-' + REVISION[:12])
    binary = root / 'bin/Release/net5.0/NPC-Plugin-Chooser.dll'
    receipt = root / 'modlab-build.json'
    if receipt.is_file():
        record = json.loads(receipt.read_text(encoding='utf-8'))
        if record['revision'] != REVISION or any(not (root / name).is_file() or digest(root / name) != checksum
                                               for name, checksum in record['hashes'].items()):
            raise ValueError('The retained NPC helper build changed. Preserve the changed folder before obtaining it again: ' + str(root))
        if binary.is_file(): return sdk, binary, root / 'Data'
    root.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        with urlopen(SOURCE + quote(name), timeout=30) as response:
            content = response.read(2 * 1024 * 1024 + 1)
        if len(content) > 2 * 1024 * 1024: raise ValueError('NPC helper source download exceeds its expected size.')
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    with sdk_environment(sdk, tools_root / 'sdk-state'):
        result = subprocess.run([str(sdk / 'dotnet.exe'), 'build', str(root / FILES[0]), '--configuration', 'Release',
                                 '--nologo', '--verbosity', 'quiet'], cwd=root, capture_output=True,
                                timeout=300, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    (root / 'build.log').write_bytes(result.stdout + result.stderr)
    if result.returncode or not binary.is_file():
        raise ValueError('NPC appearance helper could not be built. Check the retained download/build log: ' + str(root / 'build.log'))
    files = [root / name for name in FILES] + [p for p in binary.parent.rglob('*') if p.is_file()]
    receipt.write_text(json.dumps({'revision': REVISION, 'source': SOURCE,
        'hashes': {p.relative_to(root).as_posix(): digest(p) for p in files}}, indent=2), encoding='utf-8')
    return sdk, binary, root / 'Data'
