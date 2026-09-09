# Follow-up to Pro's review through 12e784e

The report was checked against a89719b, not merely the reviewed historical commits. Independent temporary-file probes reproduced both shared-texture issues, the selected-component inventory omission, and publication occurring before the durable root journal's applied list. The recorded-source modification control was detected correctly.

## Corrected in source

- Saved scenery verification now compares the current selected component's path inventory with its prepared inventory. A newly added matching mesh requires reassessment while retaining the preference and old output. Unrelated objects and documentation in the same mod do not invalidate that component.
- Verification also resolves the selected source's current loose/packed variant. A newly installed loose patch over a formerly selected packed mesh cannot inherit the old build's success.
- Root recovery reconciles all journaled before/after identities against actual destination files, including publication missing from the durable applied list. Unknown later changes stop recovery before other root files are modified. Known withdrawn files support retry after an interrupted restoration; final previous-state verification precedes success. Journal writes use the existing atomic record writer.

Validation: five new regression tests were observed failing before the corresponding changes. The complete hub suite passes 552 tests. Fixtures exercise real planning/publication/recovery and temporary files; only native archive reading and the live-game-running check are substituted at their external boundaries. This is not a power-loss test or a new native installation trial.

## Still open

Shared-resource choice needs its own correction: tree selection must not silently change retained ground textures, and incidental fallback hash differences must not be called proven incompatibility. Preserve component choices and provide an understandable shared-resource decision where needed. No universal texture conversion is proposed.

Native scenery reset was submitted and independently verified to disable the owned scene output while retaining its 277 files and both source mods. Completion of downstream reset preparation and native restoration of the selected roads/mountains still need current-state inspection. The latest source changes in this follow-up have not yet been deployed to the running MO2 instance. Preserve that distinction when reporting progress.
