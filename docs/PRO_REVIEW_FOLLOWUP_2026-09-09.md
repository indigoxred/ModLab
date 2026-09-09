# Follow-up to Pro's review through 12e784e

The report was checked against a89719b, not merely the reviewed historical commits. Independent temporary-file probes reproduced both shared-texture issues, the selected-component inventory omission, and publication occurring before the durable root journal's applied list. The recorded-source modification control was detected correctly.

## Corrected in source

- Saved scenery verification now compares the current selected component's path inventory with its prepared inventory. A newly added matching mesh requires reassessment while retaining the preference and old output. Unrelated objects and documentation in the same mod do not invalidate that component.
- Verification also resolves the selected source's current loose/packed variant. A newly installed loose patch over a formerly selected packed mesh cannot inherit the old build's success.
- Root recovery reconciles all journaled before/after identities against actual destination files, including publication missing from the durable applied list. Unknown later changes stop recovery before other root files are modified. Known withdrawn files support retry after an interrupted restoration; final previous-state verification precedes success. Journal writes use the existing atomic record writer.

Validation: five new regression tests were observed failing before the corresponding changes. The complete hub suite passes 552 tests. Fixtures exercise real planning/publication/recovery and temporary files; only native archive reading and the live-game-running check are substituted at their external boundaries. This is not a power-loss test or a new native installation trial.

## Shared-resource correction

Scenery planning now distinguishes a bundled competing appearance, an explicitly chosen component texture, and the incidental current texture. Cross-component replacements with competing appearances produce a structured choice before any new output is copied/published. When a tree mod supplies no competing texture, an explicitly selected ground texture is not rejected because it differs from the incidental previous winner.

The preparation flow opens a grouped shared-appearance dialog, naming affected components and source mods. A group decision records each underlying texture identity separately. Cancelling retains the previous output and pending request; accepting resumes preparation. Current decisions survive ordinary saves, and Graphics exposes Change shared appearances for intentional reconsideration. Changed decision evidence is reassessed on preparation. Applied summaries also show shared-resource exceptions to the component selections.

The selected-archive follow-up permits an unrelated packed object update after checking current selected payloads. Whole-container consistency remains mandatory during preparation/publication. Passive recheck preserves an explicit shared override if a losing alternative changes while its chosen appearance remains valid; deliberate re-preparation reassesses the current competing evidence.

Focused independent review found no actionable blocking defects in these changes. Tests cover both original shared-texture reproductions, grouped per-file decisions, retained-ground resume without premature publication, save/reconsider persistence, and unrelated packed updates versus new component inventory. The complete hub suite now passes 559 tests. Native rendering and full shared-choice round trips remain separate checks.

## Native follow-up

Native scenery reset was submitted and independently verified to disable the owned scene output while retaining its 277 files and both source mods. Current desktop/process inspection confirms downstream reset preparation finished: ModLab shows the same three existing record/cleaning findings and no scenery helper remains running. Native restoration of the selected roads/mountains remains to be checked. The test MO2 instance was closed through its UI before deploying these changes. Deployment and further native results will be recorded separately.
