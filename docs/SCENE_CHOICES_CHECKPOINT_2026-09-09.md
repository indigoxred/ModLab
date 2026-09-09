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

## Native deployment and application

Computer Use capture recovered after resetting its JavaScript session. With the disposable MO2 instance closed, ten current modules from `12e784e`, including the shader-slot and custom-archive corrections, were deployed and SHA-checked. Previous modules remain under `builds/ui-backups/before-scene-corrections-12e784e`; the earlier deployment backup remains intact.

The running Graphics panel was exercised through the native UI. Selected **Blended Roads** for roads/bridges and **Majestic Mountains** for mountains/rocks, leaving the six other component groups on their installed setup. Save initiated preparation. Scene job `80a3e9f2ce8e` reached `Applied; file providers checked`: 277 output files and 55 retained dependencies. The pending request was removed only after MO2 activation and effective-provider checks.

An independent read-only check matched all 277 published file hashes against both the applied manifest and original selected sources. Both source mods remain active below the owned scene output. All five installed Blended Roads bridge patch files match their installation receipt in the new scene output. This preserves the installed patch variants; it does not establish every possible road compatibility combination.

Downstream PGPatcher job `dfd2ce69c420` reached `Graphics output applied; gameplay unverified`: 654 checked meshes and 656 output files. Its recorded inputs contain all 277 scene output entries. Independent hashes of the published graphics output match its job record, and the active graphics output follows the scene output. These checks establish the preparation chain, not in-game appearance.

After preparation returned control, reopening Graphics restored both component selections. The separate renderer tab retained the previous mesh-fixes-only selection and lighting fix option. Closing the panel returned immediately to Mods & choices without initiating setup. No helper was launched by opening, switching tabs or closing the panel in this check. The full hub suite was rerun: 539 tests passed.

Local evidence: `outputs/ui-rework/check_scene_applied.py` and its JSON result in the Codex task workspace. Native reset and same-choice reapply are still not tested end to end; their existing unit coverage is not a substitute for those checks. The deliberately unresolved record/cleaning findings remain visible, so this is not an all-clear for the entire profile.

Further work remains on final setup reporting of scene choices after downstream transformations, mixed texture/mesh preference detail, distant LOD preparation, remaining installations and the broader goal. This checkpoint does not establish complete scenery compatibility or in-game appearance.

## Review corrections and native reset/restore follow-up

Source commits `4e53791` and `3d74df0` address the reproduced inventory, interrupted-root-recovery and shared-texture findings; see `PRO_REVIEW_FOLLOWUP_2026-09-09.md` for their boundaries. All 559 hub tests pass, compilation succeeds, and focused independent review found no blocking defects. Twelve current modules were deployed and SHA256 checked with the test MO2 instance closed. The prior modules are retained in `builds/ui-backups/before-shared-choices-20260909`.

Native reset finished without deleting the 277 old scene files or disabling source mods. On restarting the updated instance, Mods & choices reported that scenery followed the installed setup. In the actual Graphics panel, Blended Roads and Majestic Mountains were selected again independently, leaving the other components on Keep the installed setup. Scene job `045ad7b5970c` applied 277 files and retained 55 dependencies. This real combination produced no unresolved shared-resource decision.

Downstream graphics job `bc1e0c087d4f` applied 654 checked meshes/656 files. Independent verification matched every published scene file to its source, retained all five installed bridge patch identities, checked source/output activation and priority, found all 277 scene paths in downstream inputs, and matched every published graphics hash. Evidence is `outputs/ui-rework/check_scene_restored.py` and its JSON result in the Codex task workspace.

The finished native Mods & choices page now displays Applied scenery choices, Roads and bridges: Blended Roads, and Mountains and rocks: Majestic Mountains after downstream transformation. Existing Apocalypse, Ars Metallica and cleaning-advice findings remain; this is not a profile-wide all-clear. The reset/restore chain and current-result reporting are now observed end to end. Same-choice reapply after a manual override, and the new grouped shared-appearance dialog's accept/cancel/reconsider path, still need their own native round trips. Their source and fixture checks are not substituted for that coverage.

The scene-only restore also reassessed NPC and gameplay preparation before graphics. Reducing unnecessary unrelated rebuilds remains the separate targeted improvement in the plan; do not remove actual record dependencies merely to shorten testing.
