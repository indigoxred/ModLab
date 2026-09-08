# Body morph preparation verification — 8 September 2026

This increment adds BodySlide morph-file preparation to the existing shared-body and Advanced build paths. It is a prerequisite for individual NPC shape assignment, not a completed per-character customizer.

## Product behavior

- Guided preparation has an explicit in-game shape support choice. Keep existing setting derives from the selected projects, independent of hidden Advanced controls. New items inherit a consistent selected saved setting. Mixed existing settings require an explicit choice before any output changes.
- BodySlide 5.8.2 runs in its existing isolated job through MO2 with `--trimorphs` when requested.
- Each weighted project owns two NIFs and one unweighted-stem body TRI. Expected files must all exist; unexpected files prevent publication. TRI position/UV records are checked for bounded structure, complete payloads, finite scales and usable morphs.
- Publication records the mode and file hashes, preserves other projects, backs up the old output and removes obsolete files of replaced projects. Automatic rebuilds retain each saved mode and preset. Saved settings refresh after successful publication.
- This checks file preparation and effective MO2 providers. It does not yet verify each NIF's BODYTRI linkage, actor runtime assignment, OBody configuration, physics, or visual appearance.

## Live integration evidence

Test instance: `workspace/hub-test-20260906`; profile: `ModLab 1170 Test`; Skyrim 1.6.1170.

`builds/bodyslide/dfc0ae7bc452/operation.json` records the successful build of CBBE NeverNude with CBBE Athletic and matching hands, feet and first-person projects. All 10 NIFs and 5 TRI files were generated, checked, published and verified effective by ModLab. `effective_output_issues` is empty. The guided screen displayed Prepared and applied.

Output: `ModLab - ModLab 1170 Test - BodySlide [68caae]`. This remains disposable test output, not the final intended character setup. OBody was not installed or enabled, and no per-character preset was assigned. An Athletic static shape with morph data is not a claim of the zeroed base required by a later OBody setup.

The first attempt, `7bd31b9f3d55`, exposed an incorrect `--tri` spelling. BodySlide rejected it before producing any files. Computer Use access to its separate help window timed out; only that identified private test process was stopped. ModLab recorded failure, left the prior body output and saved default intact, and restored its previous downstream graphics output. The option was corrected to the upstream long name `--trimorphs` before the successful run.

Before Apply, browsing and selecting options left enabled mods, plugin order, saved defaults, helper-job inventory and LOOT runs unchanged. No Skyrim launch occurred. The separate MO2 Downloader installation was not touched.

## Checks and review

Focused tests were observed failing before implementation, then passed. Coverage includes shared TRI expectations for weighted meshes, malformed/truncated morph data, preserving morph mode during rebuilds, selected-only guided settings, deliberate mixed-mode choices, and retaining unrelated outfit morphs during a static rebuild.

Code review found hidden Advanced setting leakage and a stale saved-project snapshot after publication; both were corrected. Full hub regression suite and compilation pass as recorded alongside this change.

## Primary references

- [BodySlide 5.8.2 command definitions](https://github.com/ousnius/BodySlide-and-Outfit-Studio/blob/v5.8.2/src/program/BodySlideApp.h)
- [BodySlide 5.8.2 TRI reader/writer](https://github.com/ousnius/BodySlide-and-Outfit-Studio/blob/v5.8.2/src/files/TriFile.cpp)

## Remaining work

Implement the actual character-scoped assignment/customization flow with effective dependency checks, matching neutral base and outfits, preserved user configuration, and clear scope. Continue the broader body/skin/physics, appearance and guided workflow commitments; this increment does not close that goal.
