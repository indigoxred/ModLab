"""Native regression using self-created triangle meshes; no game assets required."""
from pathlib import Path
import json, os, shutil, subprocess, sys
import argparse
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--sdk',type=Path,required=True,help='Existing dotnet executable')
parser.add_argument('--library',type=Path,required=True,help='Existing niflysharp 1.1.0 net5.0 folder')
parser.add_argument('--output',type=Path,required=True,help='Dedicated scratch folder for generated test files')
args=parser.parse_args()
repo=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(repo))
from modlab.resources.mo2_hub.body_meshes import PROGRAM
directory=args.output.resolve()
directory.mkdir(parents=True,exist_ok=True)
library=args.library.resolve()
sdk=args.sdk.resolve()
generator=r'''
class Fixture {
 static void Link(nifly.NifFile nif, nifly.NiAVObject obj, string value) {
  var data=new nifly.NiStringExtraData(); data.name=new nifly.NiStringRef("BODYTRI"); data.stringData=new nifly.NiStringRef(value);
  nif.AssignExtraData(obj,data);
 }
 static void Main(string[] args) {
  var paths=new System.Collections.Generic.List<string>();
  foreach(var mode in new[]{"sibling", "conflict", "orphan"}) {
   using var nif=new nifly.NifFile(); nif.Create(nifly.NiVersion.getSSE());
   var vertices=new nifly.vectorVector3(); vertices.Add(new nifly.Vector3(0,0,0)); vertices.Add(new nifly.Vector3(1,0,0)); vertices.Add(new nifly.Vector3(0,1,0));
   var triangles=new nifly.vectorTriangle(); triangles.Add(new nifly.Triangle(0,1,2));
   var uvs=new nifly.vectorVector2(); for(int i=0;i<3;i++)uvs.Add(new nifly.Vector2(0,0));
   var shape=nif.CreateShapeFromData("Body",vertices,triangles,uvs);
   var root=nif.GetRootNode();
   var sibling=nif.AddNode("Morph Link",new nifly.MatTransform(),root);
   Link(nif,sibling,"actors/body.tri");
   if(mode=="conflict")Link(nif,shape,"actors/other.tri");
   if(mode=="orphan") {
    // An unreachable block must not affect the loaded scene.
    var orphan=new nifly.NiNode(); orphan.name=new nifly.NiStringRef("Orphan"); nif.GetHeader().AddBlock(orphan);
    Link(nif,orphan,"actors/unused.tri");
   }
   var path=System.IO.Path.Combine(args[0],mode+".nif");
   if(nif.Save(path)!=0)throw new System.Exception("Could not save synthetic mesh");
   paths.Add(path);
  }
  var input=System.IO.Path.Combine(args[0],"input.json");
  System.IO.File.WriteAllText(input,System.Text.Json.JsonSerializer.Serialize(paths));
  Program.Main(new[]{input,System.IO.Path.Combine(args[0],"result.json")});
 }
}
'''
(directory/'Program.cs').write_text(PROGRAM.replace('static void Main','public static void Main')+generator)
(directory/'Regression.csproj').write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net10.0</TargetFramework><StartupObject>Fixture</StartupObject></PropertyGroup><ItemGroup><Reference Include="nifly"><HintPath>'+str(library/'nifly.dll')+'</HintPath></Reference></ItemGroup></Project>')
(directory/'NuGet.Config').write_text('<configuration><packageSources><clear /></packageSources></configuration>')
env=dict(os.environ,DOTNET_ADD_GLOBAL_TOOLS_TO_PATH='false',DOTNET_GENERATE_ASPNET_CERTIFICATE='false',DOTNET_CLI_TELEMETRY_OPTOUT='1',DOTNET_CLI_HOME=str(directory/'sdk-home'))
subprocess.run([str(sdk),'build',str(directory/'Regression.csproj'),'--nologo','-v:q'],env=env,check=True)
binary=directory/'bin/Debug/net10.0'
shutil.copy2(library/'niflycpp.dll',binary/'niflycpp.dll')
subprocess.run([str(sdk),str(binary/'Regression.dll'),str(directory.resolve())],env=env,check=True)
result=json.loads((directory/'result.json').read_text())
for mode, expected in [('sibling',{'actors/body.tri'}),('conflict',{'actors/body.tri','actors/other.tri'}),('orphan',{'actors/body.tri'})]:
    data=result[str(directory.resolve()/(mode+'.nif'))]
    links={link for shape in data['shapes'].values() for link in shape['links']}
    assert links==expected, (mode,links,expected)
    assert data['shapes']['Body']['vertices']==3
print('PASS: reachable sibling BODYTRI, competing links, unreachable node ignored; three synthetic NIFs')
