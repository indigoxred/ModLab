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
from tests.test_hub_npc import record,sub
from tests.test_hub_character_body import npc,addon,ref
import struct
rows=[record(b'RACE',ref(b'WNAM',0x901),0x900),
    record(b'ARMO',ref(b'MODL',0x902),0x901),addon(0x902,'shared/body_1.nif',0x903),
    record(b'TXST',sub(b'TX00',b'shared/body.dds\0'),0x903),
    record(b'ARMO',ref(b'MODL',0x911),0x910),
    record(b'ARMA',addon(0x911,'private/body_1.nif',0x912)[24:]+ref(b'NAM3',0x913),0x911),
    record(b'FLST',ref(b'LNAM',0x912),0x913),
    record(b'TXST',sub(b'TX00',b'private/body.dds\0'),0x912),npc(0x801,skin=0x910),npc(0x802,skin=0x910)]
groups=[]
for kind in dict.fromkeys(row[:4] for row in rows):
    payload=b''.join(row for row in rows if row[:4]==kind)
    groups.append(struct.pack('<4sI4sIII',b'GRUP',24+len(payload),kind,0,0,0)+payload)
(root/'Fixture.esm').write_bytes(record(b'TES4',sub(b'HEDR',struct.pack('<fII',1.7,len(rows),0x1000)))+b''.join(groups))
(root/'Probe.csproj').write_text(f'''<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net10.0-windows</TargetFramework></PropertyGroup><ItemGroup><ProjectReference Include="{data.parent/'NPC-Plugin-Chooser.csproj'}" /></ItemGroup></Project>''',encoding='utf-8')
(root/'Program.cs').write_text(r'''using System;
using System.IO;
using System.Linq;
using System.Reflection;
using Mutagen.Bethesda;
using Mutagen.Bethesda.Skyrim;
using Mutagen.Bethesda.Synthesis;
using NPCPluginChooser.Settings;
class Probe {
 static int Main(string[] args) {try {Run(args);return 0;}catch(Exception e){Console.Error.WriteLine(e);return 1;}}
 static void Run(string[] args) {
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
  using var fixture=SkyrimMod.CreateFromBinaryOverlay(Path.Combine(args[0],"Fixture.esm"),SkyrimRelease.SkyrimSE);
  var skinPatch=new SkyrimMod(ModKey.FromNameAndExtension("SkinResult.esp"),SkyrimRelease.SkyrimSE);
  var skinSettings=new PatcherSettings();
  skinSettings.ModLabBodyAssignments.Add("000801:Fixture.esm","000910:Fixture.esm");
  skinSettings.ModLabBodySex.Add("000801:Fixture.esm","female");
  skinSettings.ModLabBodyModelSources.Add("000801:Fixture.esm",new System.Collections.Generic.Dictionary<string,string>{{"000911:fixture.esm","000902:Fixture.esm"}});
  NPCPluginChooser.Program.ApplyModLabBodiesCore(skinSettings,fixture.ToImmutableLinkCache(),skinPatch);
  var reordered=new SkyrimMod(ModKey.FromNameAndExtension("SkinResult.esp"),SkyrimRelease.SkyrimSE);
  var reorderedSettings=new PatcherSettings();
  foreach(var actor in new[]{"000802:Fixture.esm","000801:Fixture.esm"}) {
   reorderedSettings.ModLabBodyAssignments.Add(actor,"000910:Fixture.esm");
   reorderedSettings.ModLabBodySex.Add(actor,"female");
   reorderedSettings.ModLabBodyModelSources.Add(actor,skinSettings.ModLabBodyModelSources["000801:Fixture.esm"]);
  }
  NPCPluginChooser.Program.ApplyModLabBodiesCore(reorderedSettings,fixture.ToImmutableLinkCache(),reordered);
  var actorKey=FormKey.Factory("000801:Fixture.esm");
  if(skinPatch.Npcs[actorKey].WornArmor.FormKey!=reordered.Npcs[actorKey].WornArmor.FormKey)throw new Exception("Adding another actor changed the existing actor's generated body identity");
  skinPatch.WriteToBinary(Path.Combine(args[0],"SkinResult.esp"));
  var texturePatch=new SkyrimMod(ModKey.FromNameAndExtension("TextureResult.esp"),SkyrimRelease.SkyrimSE);
  skinSettings.ModLabSkinTextures.Add("000801:Fixture.esm",new System.Collections.Generic.Dictionary<string,System.Collections.Generic.Dictionary<string,string>> {
   {"000911:fixture.esm",new System.Collections.Generic.Dictionary<string,string>{{"diffuse","modlab/actor/body.dds"},{"normal","modlab/actor/body_msn.dds"},{"subsurface","modlab/actor/body_sk.dds"},{"specular","modlab/actor/body_s.dds"}}}});
  skinSettings.ModLabHeadSkinTextures.Add("000801:Fixture.esm",new System.Collections.Generic.Dictionary<string,string>{{"diffuse","modlab/actor/head.dds"},{"normal","modlab/actor/head_msn.dds"},{"subsurface","modlab/actor/head_sk.dds"},{"specular","modlab/actor/head_s.dds"}});
  NPCPluginChooser.Program.ApplyModLabBodiesCore(skinSettings,fixture.ToImmutableLinkCache(),texturePatch);
  texturePatch.WriteToBinary(Path.Combine(args[0],"TextureResult.esp"));
  if(texturePatch.Npcs.Count!=1 || texturePatch.TextureSets.Count!=2)throw new Exception("Skin changes escaped character scope");
  var generatedAddon=texturePatch.ArmorAddons.First();
  var swap=generatedAddon.TextureSwapList.Female.FormKey;
  if(swap==FormKey.Factory("000913:Fixture.esm") || texturePatch.FormLists.Count!=1 ||
     texturePatch.FormLists[swap].Items.Count!=1 || texturePatch.FormLists[swap].Items[0].FormKey!=generatedAddon.SkinTexture.Female.FormKey)
      throw new Exception("Selected skin still points to the old shared texture swap list");
  Console.WriteLine("PASS: native body reference and preserved-skin model clone exported");
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
    from modlab.resources.mo2_hub.body_ownership import BodyIndex
    index=BodyIndex();index.add_plugin(root/'Fixture.esm');index.add_plugin(root/'SkinResult.esp')
    changed=index.character('000801:fixture.esm');untouched=index.character('000802:fixture.esm')
    assert changed['body_models']==['meshes/shared/body_0.nif','meshes/shared/body_1.nif']
    assert changed['textures']==['textures/private/body.dds']
    assert untouched['body_models']==['meshes/private/body_0.nif','meshes/private/body_1.nif']
    assert changed['skin'].endswith(':skinresult.esp') and untouched['skin']=='000910:fixture.esm'
    print('PASS: shared meshes with retained private skin; second actor keeps original meshes and skin')
    changed_index=BodyIndex();changed_index.add_plugin(root/'Fixture.esm');changed_index.add_plugin(root/'TextureResult.esp')
    skin_actor=changed_index.character('000801:fixture.esm');other=changed_index.character('000802:fixture.esm')
    assert skin_actor['body_models']==['meshes/shared/body_0.nif','meshes/shared/body_1.nif']
    assert skin_actor['textures']==['textures/modlab/actor/body.dds','textures/modlab/actor/body_msn.dds','textures/modlab/actor/body_s.dds','textures/modlab/actor/body_sk.dds']
    assert other['textures']==['textures/private/body.dds']
    print('PASS: actor-local skin texture records with original body intent and second actor preserved')

print((r.stdout+r.stderr) if r.returncode else r.stdout.splitlines()[-1])
raise SystemExit(r.returncode)
