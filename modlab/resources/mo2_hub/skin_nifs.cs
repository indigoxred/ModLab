using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Text.Json;
using nifly;

class SkinRequest { public string output {get;set;} public Dictionary<string,Dictionary<int,string>> changes {get;set;} }
class Program {
 static object Inspect(NifFile nif) {
  var header=nif.GetHeader();
  var shapes=new List<object>();
  foreach(var shape in nif.GetShapes()) {
   var shader=nif.GetShader(shape);
   var textureSet=shader?.TextureSetRef().index ?? -1;
   var textures=textureSet>=0?header.GetBlockById<BSShaderTextureSet>(textureSet).textures.items().Select(t=>t.get()).ToArray():Array.Empty<string>();
   shapes.Add(new {name=shape.name.get(),shader=shader?.GetShaderType(),texture_set=textureSet,textures});
  }
  return new {shapes,blocks=Enumerable.Range(0,(int)header.GetNumBlocks()).Select(i=>new {type=header.GetBlockById(i).GetBlockName(),size=header.GetBlockSize((uint)i)}).ToArray(),
    strings=Enumerable.Range(0,(int)header.GetStringCount()).Select(i=>header.GetStringById(i)).ToArray()};
 }
 static void Main(string[] args) {
  var requestPath=Path.Combine(Path.GetDirectoryName(args[0]),"skin-replacements.json");
  var requests=File.Exists(requestPath)?JsonSerializer.Deserialize<Dictionary<string,SkinRequest>>(File.ReadAllText(requestPath)):new();
  var result=new Dictionary<string,object>();
  foreach(var path in JsonSerializer.Deserialize<string[]>(File.ReadAllText(args[0]))) {
   using var nif=new NifFile();
   if(nif.Load(path)!=0 || nif.HasUnknown())throw new Exception("Face mesh is unreadable or has unknown blocks: "+path);
   var before=Inspect(nif);
   if(!requests.TryGetValue(path,out var request)) {result.Add(path,before);continue;}
   if(Path.GetFullPath(path)==Path.GetFullPath(request.output))throw new Exception("Source mesh must be retained");
   var seen=new HashSet<string>();
   foreach(var shape in nif.GetShapes()) {
    if(!request.changes.TryGetValue(shape.name.get(),out var changes))continue;
    if(!seen.Add(shape.name.get()))throw new Exception("Ambiguous face shape");
    var shader=nif.GetShader(shape);
    if(shader==null || shader.GetShaderType()!=4)throw new Exception("Requested shape is not face skin");
    foreach(var pair in changes) {
     if(!new[]{0,1,2,7}.Contains(pair.Key))throw new Exception("Refusing to change character tint or unrelated channels");
     nif.SetTextureSlot(shader,pair.Value.Replace('/','\\'),pair.Key);
    }
   }
   if(seen.Count!=request.changes.Count)throw new Exception("Requested face shape missing");
   var options=new NifSaveOptions {optimize=false,sortBlocks=false};
   if(nif.Save(request.output,options)!=0)throw new Exception("Cannot export face skin");
   using var exported=new NifFile();if(exported.Load(request.output)!=0)throw new Exception("Cannot reopen face skin");
   result.Add(path,new {before,after=Inspect(exported)});
  }
  File.WriteAllText(args[1],JsonSerializer.Serialize(result));
 }
}
