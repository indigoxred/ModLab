# ModLab alpha delivery — 6 September 2026

The MO2-hosted alpha is runnable. The full agreed prototype remains incomplete. The 12-hour estimate was too optimistic for all of the intended helper workflows; a working subset and tested changes do not erase that shortfall. No additional development allowance is assumed.

## Open the tested instance

Run `workspace/hub-test-20260906/app/ModOrganizer.exe` from this worktree, retain the **ModLab Test** profile, then select **ModLab** from MO2's tools menu. ModLab is currently open there. Skyrim is closed.

The profile uses separate test mod storage, private INIs and private saves. It currently has 22 active mod entries, including generated outputs and test fixtures, and 18 active plugins. These counts are not the number of user archives tested. Skyrim is Steam runtime 1.7.104.0 with SKSE 2.3.1. The original supplied downloads are retained.

The portable application plugin package is `dist/ModLab-Hub-0.1.0-alpha-20260906-1510.zip`. It contains 46 Python modules, the quickstart and a per-file hash manifest. It does not bundle third-party tools, mod assets, saves or game files. See [installation and workflow instructions](HUB_QUICKSTART.md).

- Package SHA256: `d19b1df9b15a8610c0ecdbdded62d651eef51015d6a79ba760e34b43053fdefd`
- Source identity: `5b706b703e58b2b266e9b0a7b719791743eb7a67764c50f38bcd9d38aaa9add1`
- Every archive member was read back; every deployed hub module matches packaged source.
- Latest focused suite: **194 tests passed, 1.097 seconds**. The real product tests below are separate from those tests.
- No commit, push or public release was made. Source changes remain in the worktree.

## Working user workflow

Select archives in ModLab. MO2 installs them sequentially and presents each mod's FOMOD choices. ModLab records completion, cancellation and pending entries, then runs applicable setup checks and selected helpers. Recheck repeats that coordination after changes. The original tools remain available for advanced use.

| Workflow | Current result and boundary |
|---|---|
| Installation queue | Real multi-archive installs, cancellation, saved retry and continuing a later archive were exercised. Personal FOMOD choices remain choices. |
| Dependencies and runtime | Missing masters name affected plugins/providers; known download links are supplied. A wrong-runtime RaceMenu DLL blocked managed launch. This is not a complete database of every dependency. |
| LOOT and cleaning | LOOT runs automatically during setup. Independent checks cover masters, native declarations and managed files. Cleaning requires a narrow reviewed rule; neither a crash nor a dirty-record count authorizes it. |
| BodySlide | Saved project/preset choices generate managed output with project/source identification, input checks and prior-output restoration. |
| Pandora | Selected behavior generation uses separate managed output and result checks. This does not implement universal FNIS/Nemesis/Pandora coexistence. |
| Synthesis | A selected pinned patcher ran, its plugin passed xEdit checks, and History restored a prior output together with its matching settings and FormID allocations. The automation CLI currently requires a source build. |
| PGPatcher | Selected mesh repair produced 631 checked meshes and one plugin; xEdit and final LOOT checks followed. Broad ENB/PBR appearance and coordinated graphics restoration are not established. |
| SKSE | Matching root loader files and an MO2 scripts mod were installed. Managed launch uses SKSE through MO2. Fresh startup logs distinguish loaded DLLs from missing/failed loads. |
| Author instructions | Mod instructions displays local text/Markdown/HTML readmes from installed mod folders, with an open-folder fallback. It does not automatically carry out every readme instruction. |

Saved helper choices drive automation. ModLab does not yet choose every patcher, renderer or appearance combination for the user, obtain all helpers automatically, or make unknown mods compatible by generic merging.

## Actual in-game outcomes

Short checks avoided long walks and quest playthroughs. A disposable character now provides a quick return point.

- Earlier body/outfit and SkyUI inventory checks succeeded.
- Alternate Start's character creation, normal Mara dialogue, ship destination selection, Dawnstar arrival and resulting journal entry worked.
- Ordinator's expanded Destruction perk tree appeared with the expected nodes and description. Actual perk combat effects were not tested.
- Footprints reported its player effect active in MCM, and distinct boot prints were visible behind the character on snow. Other races/creatures were not exhaustively tested.
- SKSE recorded CrashLogger, Media Keys Fix and SSE Display Tweaks loaded successfully. No crash was deliberately induced, so crash-report generation is unverified; Media Keys' actual key behavior is also untested.

`profiles/ModLab Test/saves/ModLabFeatureCheck20260906.ess` and its `.skse` companion retain the Dawnstar test character. The earlier Alternate Start room save remains available. Neither is a long-term playthrough certification.

Skyrim is set to 1920×1080 borderless for the connected monitor. Standard absolute automated clicks remain offset. The guarded relative-mouse/scancode fallback worked for menus, dialogue, movement and camera without user assistance. That fallback is test infrastructure, not shipped ModLab functionality.

## Defects corrected during product testing

- Refused relevant file/order mutations while Skyrim is running.
- Retained identical cleaned output instead of replacing it and causing avoidable downstream rebuilds.
- Preserved the cleaned mod's priority when its checked files already win.
- Displayed the actual incompatible native runtime reason outside Technical details.
- Used current plugin priorities to avoid MO2's stale cached active positions after sorting. A new launch's checked startup result survived a complete MO2 restart.
- Preserved cancelled and pending installation/patch requests so earlier success cannot hide an unfinished operation.
- Added coordinated Synthesis restoration with matched settings/allocation data and rollback on publication failure.
- Made explicit web links in findings clickable. A focused rendering check using MO2's Qt libraries verified exact URL anchors, unchanged literal text and Unicode positioning. No external page was opened by the check.

Detailed evidence, operation IDs and narrowly stated test results are in [the implementation record](superpowers/plans/2026-09-06-modlab-hub.md). Latest startup: `reports/launch/cd2ba94d9551/operation.json`; gameplay checks: launch `8cd3938691e5`.

The final report saved through ModLab is `workspace/hub-test-20260906/reports/ModLab delivery profile 20260907.json`. Readback shows 18 active plugins, checked startup and no blockers identified by these checks. It explicitly retains the unfinished installer, overlapping meshes and incomplete archive inspection; full gameplay verification remains false.

## What remains incomplete

The supplied folder contains 64 archives. It was inventoried and selected representative installs were tested; the entire batch was not installed or validated. RaceMenu remains installed but disabled because its supplied DLL targets another runtime. FSMP's cancelled installer remains an explicit unfinished request. Do not mistake the active profile's successful startup for success of those entries or the full folder.

Automatic Wrye Bash, TexGen/DynDOLOD and per-NPC appearance coordination are missing. Broader framework/style rules, BSA conflict inspection, coordinated SKSE/graphics recovery and helper acquisition remain incomplete. A helper entry in Setup does not mean its workflow is automated. Enderal's separate installation is outside this profile's scope.

These are remaining product gaps, not new scope proposals. Completing them requires a revised delivery decision after the user returns; do not silently extend the approved ceiling or report the overall goal complete. Preserve the tested build and receipts while that decision is pending.
