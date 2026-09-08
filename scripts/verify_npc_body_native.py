from pathlib import Path
import sys,subprocess
repo=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(repo))
from modlab.resources.mo2_hub.npc_body_helper import ensure_helper
from modlab.resources.mo2_hub.runtime import sdk_environment
import argparse
parser=argparse.ArgumentParser(description='Native actor-local body assignment test; uses existing helper dependencies.')
parser.add_argument('--tools',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
tools=args.tools.resolve()
sdk,binary,data=ensure_helper(tools)
root=args.output.resolve();root.mkdir(parents=True,exist_ok=True)
(root/'Probe.csproj').write_text(f'''<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net10.0-windows</TargetFramework></PropertyGroup><ItemGroup><ProjectReference Include="{data.parent/'NPC-Plugin-Chooser.csproj'}" /></ItemGroup></Project>''',encoding='utf-8')
(root/'Program.cs').write_text(r'''using System;
using System.IO;
using System.Reflection;
using Mutagen.Bethesda;
using Mutagen.Bethesda.Skyrim;
using Mutagen.Bethesda.Synthesis;
using NPCPluginChooser.Settings;
class Probe {
 static void Main(string[] args) {
  var source=new SkyrimMod(ModKey.FromNameAndExtension("Base.esm"),SkyrimRelease.SkyrimSE);
  var armor=source.Armors.AddNew(); armor.EditorID="SharedSkin";
  var old=source.Armors.AddNew(); old.EditorID="PrivateSkin";
  var a=source.Npcs.AddNew(); a.EditorID="Lydia";a.Weight=65; a.WornArmor.SetTo(old.FormKey);
  var b=source.Npcs.AddNew(); b.EditorID="Adrianne";b.Weight=40;b.WornArmor.SetTo(old.FormKey);
  var patch=new SkyrimMod(ModKey.FromNameAndExtension("Result.esp"),SkyrimRelease.SkyrimSE);
  var face=patch.Npcs.GetOrAddAsOverride(a);face.Height=1.03f;
  Directory.CreateDirectory(Path.Combine(args[0],"before"));
  patch.WriteToBinary(Path.Combine(args[0],"before","Result.esp"));
  var settings=new PatcherSettings();settings.ModLabBodyAssignments.Add(a.FormKey.ToString(),armor.FormKey.ToString());
  NPCPluginChooser.Program.ApplyModLabBodiesCore(settings,source.ToImmutableLinkCache(),patch);
  if(patch.Npcs.Count!=1 || face.WornArmor.FormKey!=armor.FormKey || face.Height!=1.03f || face.Weight!=65 || a.WornArmor.FormKey!=old.FormKey || b.WornArmor.FormKey!=old.FormKey)throw new Exception("Body assignment changed unrelated state");
  patch.WriteToBinary(Path.Combine(args[0],"Result.esp"));
  using var reloaded=SkyrimMod.CreateFromBinaryOverlay(Path.Combine(args[0],"Result.esp"),SkyrimRelease.SkyrimSE);
  if(reloaded.Npcs.Count!=1 || reloaded.Npcs[a.FormKey].WornArmor.FormKey!=armor.FormKey || reloaded.Npcs[a.FormKey].Height!=1.03f)throw new Exception("Export mismatch");
  Console.WriteLine("PASS: exported body reference; retained selected appearance height and actor weight; untouched source and second actor");
 }
}''',encoding='utf-8')
with sdk_environment(sdk,tools/'sdk-state'):
    r=subprocess.run([str(sdk/'dotnet.exe'),'run','--project',str(root/'Probe.csproj'),'-p:NuGetAudit=false','--',str(root)],capture_output=True,text=True,timeout=180)
(root/'probe.log').write_text(r.stdout+r.stderr,encoding='utf-8')
if r.returncode==0:
    from modlab.resources.mo2_hub.body_ownership import records
    before=dict(records(root/'before/Result.esp'));after=dict(records(root/'Result.esp'))
    assert set(before)==set(after) and len(after)==1
    for key,(kind,fields) in before.items():
        actual=after[key][1]
        assert {k:v for k,v in fields.items() if k!=b'WNAM'}=={k:v for k,v in actual.items() if k!=b'WNAM'}, 'Non-body NPC fields changed'
    print('PASS: independent record inspection confirms every non-WNAM NPC field unchanged')
print((r.stdout+r.stderr) if r.returncode else r.stdout.splitlines()[-1])
raise SystemExit(r.returncode)
