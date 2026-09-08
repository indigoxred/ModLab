# Engine Fixes preloader through ModLab

The normal archive queue now recognizes the exact official Engine Fixes v7 preloader package and places its checked DLL beside SkyrimSE.exe. The matching missing-preloader repair offers a download link and the same installation action. This closes the manual-copy step for this package on Skyrim 1.6.1170; it does not claim support for arbitrary root installers or every Engine Fixes release.

## Source and handling

Author file: https://www.nexusmods.com/skyrimspecialedition/mods/17230?tab=files&file_id=725261 . Its file-specific instructions require the main game directory, not Data. The archive's Vortex placement metadata is not executed or imported.

- Archive SHA-256: `35633dc2904521f35861dbb582e084a10e77bec5b8f08fb08963d7578b07452f`.
- Installed d3dx9_42.dll SHA-256: `cf366987da6237559eb6e113ea717ec21762c9eec3a87d9dc4fa9ddfe7789c26`.
- The selected runtime, executable, archive, extracted DLL and destination are checked before publication. A matching existing loader is reused without rewriting it; a different loader is preserved and reported.
- Root publication reuses the existing bounded SKSE backup/journal machinery. A failed final receipt rolls back its root change. Windows publication into an absent destination uses rename so a newly appearing file is not silently overwritten.
- History offers undo only for an operation that actually installed the preloader. Changed live files or unverified recovery evidence stop automatic restoration. The component affects all profiles using the same game folder.

## Verification

509 hub unittest cases passed. The new cases cover correct root placement, reuse, undo, wrong runtime/archive, changed game/source/destination, interrupted copy, receipt failure, and preservation of a loader appearing during publication. Focused independent review reproduced the destination race; the checked publication and Windows rename corrections address it.

The real retained author archive was extracted and installed into a disposable game fixture, then reused and undone. Its DLL hash matched, reuse preserved its timestamp, no Data installation or Vortex metadata was produced, and undo retained the removed file. Only the fixture's runtime reader was substituted; this was not a live missing-preloader installation.

Eight production modules were deployed into the closed test MO2 instance, with prior files retained under `builds/ui-backups/before-preloader-workflow` and deployed bytes compared with source.

Native ModLab Add mods selected the actual author archive on `ModLab 1170 Test` (37 active plugins). Operation `f22f827b723e` recorded `reused_existing: true`. The real game DLL remained 88,576 bytes with the expected hash and UTC timestamp `2025-08-18T06:24:27.3008821Z`. Queue `443ee2361277` completed, automatic LOOT/setup returned to the responsive window, and History displayed both completed queue and preloader receipt. The undo action was correctly absent for this reuse operation.

## Remaining limits

This does not establish a new game startup or gameplay test. The existing Apocalypse and Ars Metallica winning-record findings and retained Creation Club cleaning advice remain unresolved; the preloader did not clear them or label the whole setup ready. The broader ModLab goal, graphics/helper coverage and remaining archive installation work are still unfinished. Character review fixes are recorded separately in CHARACTER_ROUNDTRIP_CHECKPOINT_2026-09-09.md.
