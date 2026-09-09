# Component scenery integration — verification checkpoint

The Graphics panel now has separate Scenery and Renderer tabs in source. Scenery selects installed providers for roads/bridges, mountains/rocks, trees, grass, plants, buildings/ruins, objects/furniture and ground surfaces. It retains source mods and installed FOMOD/patch bytes. This is not a whole-mod priority chooser. Renderer controls remain available if the scene catalog cannot be read.

Scene requests are stored separately from applied results. Explicitly saving the same selection creates a fresh request, allowing confirmation after a manual change; ordinary rechecks do not silently restore an outside override. Preparation runs off Qt's UI thread. MO2 context capture, output activation and refresh callbacks remain on the UI thread. Selecting the installed setup for every group requests a recoverable reset that disables the owned scene output without deleting source mods or the retained output.

Preparation runs after withdrawal of the managed downstream Graphics output and before existing source preparation. The material helper therefore sees the selected scene assets. Pending scenery remains a visible actionable finding until application is confirmed. Existing renderer and advanced workflows remain present.

## Verification completed

- 539 hub tests pass, including component selection, packed/loose assets, archive order, shared textures, changed/disabled sources, explicit same-choice requests and shader texture classification.
- Packed dependencies are resolved using the active archive ordering supplied by the MO2 snapshot, not source-mod priority. Loose files win over packed files. Ambiguous archive ties are withheld; the original archive hash accompanies extracted data. Custom archive-list overrides are not silently treated as the standard order.
- Review reproduced and corrected two defects: a disabled fallback texture mod could pass a saved check, and the user could not reapply an unchanged choice. Current dependency winners are now resolved; the real Save handler's request write is tested.
- A private preparation run using the on-disk `ModLab 1170 Test` profile selected Blended Roads plus Majestic Mountains. Build `fa2d387f9227` reached `Checked; application pending`: 277 output files and 55 retained dependencies. It did not publish or activate an output, so it is not evidence of native MO2 application.

The first real preparation run found `textures/landscape/dirt02_p.dds` in Majestic Mountains' `meshes/Landscape/mountains/snow/mountaintrim01.nif`. The existing inspector showed a Default shader (type 0) with the reference in slot 3. [NifTools' schema](https://github.com/niftools/nifxml/blob/develop/nif.xml) identifies slot 3 as height/parallax, enabled by the Parallax shader type 3. The new exception only ignores that inactive slot for the selected mesh-fixes-only route with no PBR/complex upgrade or detected custom renderer. A reference used by another shape or an uninspected texture-bearing block remains required. Omitted references are recorded and rendering changes require reassessment. This is not a blanket exception for missing `_p.dds` files.

## Native deployment and remaining checks

An initial nine-module integration was deployed with the disposable MO2 instance closed, preserving previous modules under `builds/ui-backups/before-scene-panel-20260909`. The instance restarted, but Computer Use state capture failed repeatedly with `foreground window did not report a process id`. The process was independently confirmed live. Native panel appearance, responsiveness, selection, application, reopening and reset have **not** been verified.

Later shader-slot and custom-archive/UI fallback corrections are in source but have **not** been redeployed over that running instance. Next: regain a valid returned window, close the test instance, deploy the current modules including `scene_textures.py`, then exercise the native panel and output activation. Do not claim the running application already contains these later corrections.

Further work remains on final setup reporting of scene choices after downstream transformations, mixed texture/mesh preference detail, distant LOD preparation, remaining installations and the broader goal. This checkpoint does not establish complete scenery compatibility or in-game appearance.
