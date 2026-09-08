"""Read body NIF links and shapes using the existing isolated NIF tooling."""
from pathlib import Path

PROGRAM = '''using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Text.Json;
using nifly;
class Program {
 static void Main(string[] args) {
  var result = new Dictionary<string, object>();
  foreach (var path in JsonSerializer.Deserialize<string[]>(File.ReadAllText(args[0]))) {
   using var nif = new NifFile();
   if (Convert.ToInt32(nif.Load(path)) != 0) throw new Exception("Cannot read body mesh: " + path);
   var shapes = new Dictionary<string, object>();
   var nodes = nif.GetNodes().ToDictionary(node => nif.GetBlockID(node));
   var meshShapes = nif.GetShapes().ToDictionary(shape => nif.GetBlockID(shape));
   var links = new List<string>();
   var visited = new HashSet<int>(); var active = new HashSet<int>();
   var header = nif.GetHeader();
   void Visit(int id, int depth) {
    if (id < 0 || id >= header.GetNumBlocks()) throw new Exception("Invalid body scene reference");
    if (depth > 128 || active.Contains(id)) throw new Exception("Cyclic or excessive body scene");
    if (!visited.Add(id)) return;
    active.Add(id);
    var current = header.GetBlockById<NiAVObject>(id);
    if (current == null) throw new Exception("Invalid body scene object");
    var extras = new vectorint(); current.extraDataRefs.GetIndices(extras);
    foreach (var index in extras) {
     if (index < 0 || index >= header.GetNumBlocks()) throw new Exception("Invalid body extra-data reference");
     if (header.GetBlockById(index).GetBlockName() != "NiStringExtraData") continue;
     var extra = header.GetBlockById<NiStringExtraData>(index);
     if (extra.name.get() == "BODYTRI") links.Add(extra.stringData.get());
    }
    if (meshShapes.TryGetValue(id, out var shape))
     shapes.Add(shape.name.get(), new {vertices = (int)shape.GetNumVertices(), links});
    if (nodes.TryGetValue(id, out var node)) {
     var children = new vectorint(); node.childRefs.GetIndices(children);
     foreach (var child in children) {
      if (child == -1) continue; // Empty child slots are permitted.
      Visit(child, depth + 1);
     }
    }
    active.Remove(id);
   }
   var root = nif.GetRootNode();
   if (root == null) throw new Exception("Body mesh has no scene root");
   // RaceMenu searches the loaded scene, including sibling nodes without geometry.
   // A BODYTRI found anywhere in that scene supplies its matching shapes.
   Visit(nif.GetBlockID(root), 0);
   result.Add(path, new {shapes});
  }
  File.WriteAllText(args[1], JsonSerializer.Serialize(result));
 }
}'''


def inspect_meshes(sdk, tools_root, meshes, directory):
    from .npc_assets import inspect_nifs
    meshes = [Path(path) for path in meshes]
    for path in meshes:
        if not path.is_file() or not 128 <= path.stat().st_size <= 128 * 1024 * 1024:
            raise ValueError('Body mesh is missing, incomplete or exceeds the inspection limit: '+str(path))
    return inspect_nifs(sdk, tools_root, meshes, directory, program=PROGRAM, tool='modlab-body-inspector-v2')


def verify_generated_morphs(sdk, tools_root, root, expected, directory):
    from .body_morphs import verify_mesh_links
    from .bodyslide import relative_path
    root = Path(root)
    meshes = [root/relative_path(name) for name in expected if name.casefold().endswith('.nif')]
    found = inspect_meshes(sdk, tools_root, meshes, directory)
    relative_meshes = {Path(path).relative_to(root).as_posix(): info for path, info in found.items()}
    files = {name.casefold(): (root/relative_path(name)).read_bytes() for name in expected if name.casefold().endswith('.tri')}
    return verify_mesh_links(relative_meshes, files)
