"""An isolated, checked adaptation of the retained NPC chooser for body references."""
import json
from pathlib import Path
import shutil
import subprocess
from . import npc_helper
from .outputs import digest
from .runtime import sdk_environment

ADAPTER='body-reference-v6'

def adapt_source(program,settings):
    call='if (mergeJSONlist.Any())'
    method='public static Npc addNPCtoPatch('
    field='public class PatcherSettings\n    {'
    settings=settings.replace('\r\n','\n')
    if program.count(call)!=1 or program.count(method)!=1 or settings.count(field)!=1:
        raise ValueError('NPC helper source does not match the checked adaptation points.')
    program=program.replace(call,'ApplyModLabBodies(settings, state);\n                '+call)
    extension='public static void ApplyModLabBodies(PatcherSettings settings, IPatcherState<ISkyrimMod, ISkyrimModGetter> state)\n        {\n            ApplyModLabBodiesCore(settings, state.LinkCache, state.PatchMod);\n        }\n\n        public static void ApplyModLabBodiesCore(PatcherSettings settings, ILinkCache<ISkyrimMod, ISkyrimModGetter> cache, ISkyrimMod patch)\n        {\n            foreach (var request in settings.ModLabBodyAssignments)\n            {\n                var key = FormKey.Factory(request.Key);\n                var skin = FormKey.Factory(request.Value);\n                if (!cache.TryResolve<INpcGetter>(key, out var source))\n                    throw new Exception("Requested ModLab character is unavailable: " + request.Key);\n                if (!cache.TryResolve<IArmorGetter>(skin, out var armor))\n                    throw new Exception("Requested ModLab body armor is unavailable: " + request.Value);\n                var npc = patch.Npcs.GetOrAddAsOverride(source);\n                npc.WornArmor.SetTo(armor.FormKey);\n            }\n        }\n\n        '
    extension=extension.replace('npc.WornArmor.SetTo(armor.FormKey);', 'ApplyModLabModels(npc, armor, request.Key, settings, cache, patch);')
    extension += (Path(__file__).with_name('npc_body_models.cs')).read_text(encoding='utf-8')
    program=program.replace(method,extension+method)
    settings=settings.replace(field,field+'\n        public Dictionary<string, string> ModLabBodyAssignments = new Dictionary<string, string>();')
    settings=settings.replace(field,field+'\n        public Dictionary<string, Dictionary<string, string>> ModLabBodyModelSources = new Dictionary<string, Dictionary<string, string>>();\n        public Dictionary<string, string> ModLabBodySex = new Dictionary<string, string>();')
    return program,settings

def ensure_helper(tools_root):
    tools=Path(tools_root).resolve()
    sdk,original,data=npc_helper.ensure_helper(tools)
    source=data.parent
    root=tools/('npc-plugin-chooser-'+npc_helper.REVISION[:12]+'-'+ADAPTER)
    binary=root/'bin/Release/net5.0/NPC-Plugin-Chooser.dll'
    receipt=root/'modlab-adapter-build.json'
    if receipt.is_file():
        saved=json.loads(receipt.read_text(encoding='utf-8'))
        if saved.get('adapter')!=ADAPTER or saved.get('source')!=npc_helper.REVISION or any(
            not (root/name).is_file() or digest(root/name)!=value for name,value in saved['hashes'].items()):
            raise ValueError('The NPC body helper changed. Retain it for inspection before rebuilding: '+str(root))
        return sdk,binary,root/'Data'
    root.mkdir(parents=True,exist_ok=True)
    for name in npc_helper.FILES:
        target=root/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source/name,target)
    program,settings=adapt_source((root/'Program.cs').read_text(encoding='utf-8'),
        (root/'Settings/PatcherSettings.cs').read_text(encoding='utf-8'))
    (root/'Program.cs').write_text(program,encoding='utf-8')
    (root/'Settings/PatcherSettings.cs').write_text(settings,encoding='utf-8')
    with sdk_environment(sdk,tools/'sdk-state'):
        result=subprocess.run([str(sdk/'dotnet.exe'),'build',str(root/'NPC-Plugin-Chooser.csproj'),
            '--configuration','Release','--nologo','--verbosity','quiet','-p:NuGetAudit=false'],
            capture_output=True,timeout=300,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    (root/'build.log').write_bytes(result.stdout+result.stderr)
    if result.returncode or not binary.is_file():
        raise ValueError('NPC body helper could not be built. Inspect '+str(root/'build.log'))
    files=[root/name for name in npc_helper.FILES]+[p for p in binary.parent.rglob('*') if p.is_file()]
    receipt.write_text(json.dumps(dict(adapter=ADAPTER,source=npc_helper.REVISION,
        hashes={p.relative_to(root).as_posix():digest(p) for p in files}),indent=2),encoding='utf-8')
    return sdk,binary,root/'Data'
