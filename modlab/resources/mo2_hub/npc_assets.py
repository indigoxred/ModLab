"""Inspect actual selected FaceGen textures with the helper's existing NIF library."""
import json
from pathlib import Path
import shutil
import subprocess

from .outputs import digest
from .runtime import sdk_environment
from .vfs import readable_path

PROJECT = '''<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net10.0</TargetFramework></PropertyGroup><ItemGroup><PackageReference Include="niflysharp" Version="1.1.0" /></ItemGroup></Project>'''
PROGRAM = '''using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Text.Json;
using nifly;
class Program {
 static void Main(string[] args) {
  var result = new Dictionary<string, string[]>();
  foreach (var path in JsonSerializer.Deserialize<string[]>(File.ReadAllText(args[0]))) {
   using var nif = new NifFile();
   if (Convert.ToInt32(nif.Load(path)) != 0) throw new Exception("Cannot read FaceGen: " + path);
   result[path] = new niflycpp.TextureFinder(nif.GetHeader()).UniqueTextures.ToArray();
  }
  File.WriteAllText(args[1], JsonSerializer.Serialize(result));
 }
}'''


def texture_path(value):
    value = value.replace('\\', '/').strip().casefold()
    if value.startswith('data/'): value = value[5:]
    if not value.startswith('textures/'): value = 'textures/' + value
    if ':' in value or any(p in {'', '.', '..'} for p in value.split('/')) or not value.endswith('.dds'):
        raise ValueError('FaceGen references an unsupported texture path: ' + value)
    return value


def inspect_textures(sdk, tools_root, meshes, directory):
    tools_root, sdk = Path(tools_root), Path(sdk)
    root = tools_root / 'modlab-facegen-inspector-v1'
    binary = root / 'bin/Release/net10.0/Facegen.dll'
    receipt = root / 'modlab-build.json'
    if receipt.is_file():
        saved = json.loads(receipt.read_text(encoding='utf-8'))
        if any(not (root / name).is_file() or digest(root / name) != checksum for name, checksum in saved.items()):
            raise ValueError('The retained FaceGen inspector changed: ' + str(root))
    else:
        root.mkdir(parents=True, exist_ok=True)
        (root / 'Facegen.csproj').write_text(PROJECT, encoding='utf-8')
        (root / 'Program.cs').write_text(PROGRAM, encoding='utf-8')
        with sdk_environment(sdk, tools_root / 'sdk-state'):
            result = subprocess.run([str(sdk / 'dotnet.exe'), 'build', str(root / 'Facegen.csproj'), '-c', 'Release',
                '--nologo', '--verbosity', 'quiet'], cwd=root, capture_output=True, timeout=180,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        (root / 'build.log').write_bytes(result.stdout + result.stderr)
        if result.returncode or not binary.is_file(): raise ValueError('FaceGen inspector build failed. See ' + str(root / 'build.log'))
        files = [root / 'Program.cs', root / 'Facegen.csproj', *[p for p in binary.parent.rglob('*') if p.is_file()]]
        receipt.write_text(json.dumps({p.relative_to(root).as_posix(): digest(p) for p in files}, indent=2), encoding='utf-8')
    request, response = Path(directory) / 'facegen-input.json', Path(directory) / 'facegen-textures.json'
    request.write_text(json.dumps([str(p) for p in meshes]), encoding='utf-8')
    result = subprocess.run([str(sdk / 'dotnet.exe'), str(binary), str(request), str(response)],
        cwd=binary.parent, capture_output=True, timeout=60, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    (Path(directory) / 'facegen-inspection.log').write_bytes(result.stdout + result.stderr)
    if result.returncode or not response.is_file(): raise ValueError('The selected face mesh could not be inspected: ' + str(directory))
    found = json.loads(response.read_text(encoding='utf-8'))
    if set(found) != {str(p) for p in meshes}: raise ValueError('The FaceGen inspection is incomplete.')
    return {p: tuple(sorted({texture_path(t) for t in values if t})) for p, values in found.items()}


def retain_textures(organizer, provider, required, output):
    from .archives import archive_reader, active_archive_paths
    reader, output = archive_reader(), Path(output)
    source = Path(provider['ForcedAssetDirectory'])
    stem = Path(provider['Plugin']).stem.casefold()
    archives = [p for p in source.glob('*.bsa') if p.stem.casefold() in {stem, stem + ' - textures'}]
    available = active_archive_paths(organizer)
    evidence = []
    for relative in sorted(required):
        target, original = readable_path(output / relative), readable_path(source / relative)
        if target.is_file():
            evidence.append(dict(path=relative, source='paired/generated output', sha256=digest(target))); continue
        if original.is_file():
            target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(original, target)
            evidence.append(dict(path=relative, source=str(original), sha256=digest(target))); continue
        packed = [p for p in archives if relative in reader.entries(p)]
        if len(packed) > 1: raise ValueError('The selected appearance has ambiguous texture archives: ' + relative)
        if packed:
            reader.extract_asset(packed[0], relative, target)
            evidence.append(dict(path=relative, source=str(packed[0]), sha256=digest(target))); continue
        effective = organizer.resolvePath(relative)
        if effective and readable_path(effective).is_file():
            evidence.append(dict(path=relative, source=effective, sha256=digest(readable_path(effective)))); continue
        packed = [name for name, path in available.items() if relative in reader.entries(path)]
        if packed:
            evidence.append(dict(path=relative, source='Active archives: ' + ', '.join(packed))); continue
        raise ValueError('The selected face requires a missing texture: ' + relative +
            '. Reinstall the named appearance mod with its dependencies, or select another appearance. Output was withheld.')
    return evidence
