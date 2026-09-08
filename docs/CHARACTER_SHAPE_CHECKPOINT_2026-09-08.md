# Individual character shape workflow checkpoint

The character panel now opens a character-specific preset selector and the existing isolated BodySlide editor. This extends the shared-body editor; it does not replace the independent face/body choice work or make OBody mandatory for ordinary modding.

## Verified in the native ModLab/MO2 interface

- Found Lydia in the character directory and opened her shape panel.
- Opened CBBE NeverNude with a separately named Lydia preset, starting from CBBE Athletic.
- Changed only the high-weight `Arms` slider from the project's effective default of 0 to 15, saved in BodySlide and closed it normally.
- ModLab published the preset through its managed Shape Presets output, verified its effective file, and saved the choice for `0A2C8E:skyrim.esm`.
- Job: `workspace/hub-test-20260906/builds/bodyslide/ea347aee6ca9`.
- Preset SHA-256: `7866c2edea019b56086d241c6972ec2cc19a0ccb38b92b255094f964c11d13e5`.
- Independent file inspection confirmed the exact slider change, preservation of previously published presets, and the unchanged CBBE Athletic shared default.
- The profile correctly records `applied: null`. No character body mesh or game assignment was changed by saving this preset.

## Software checks

419 hub tests pass. New checks cover separate saved choices, stale-dialog protection, two-character config publication, change/removal of actor rules while retaining upstream rules, output activation failure, late refresh callbacks after failure, manual config edits and changed effective providers. Neutral-base checks inspect slider values and project defaults rather than trusting a preset title. Runtime assignment publication checks optional helper requirements, prepared morph output, current body/outfit files, current preset files and the effective generated config.

The publication tests use a fixture MO2 host. They prove those software paths, not an OBody gameplay result on this machine.

## Remaining work, not a completed capability claim

OBody is not active in this profile, and its shared body is currently baked as CBBE Athletic rather than prepared for individual morph assignment. The next step is the guided optional-helper setup and neutral-body/outfit preparation while preserving the desired shared appearance and private-character exceptions. Do not simply build zeroed bodies globally and call the current appearance preserved. Then apply Lydia's saved choice through the real interface and verify the exact configuration and input providers.

Independent skin/hair/physics choices, the remaining broader mod preparation workflows and the final user gameplay checklist remain part of the active project. The existing four face selections and Adrianne's verified shared-body/private-skin choice remain intact.

## Preset-only invalidation fix

After adding Lydia's preset, the previous checks incorrectly requested BodySlide, NPC and graphics preparation. Commit `c679acd` compares BodySlide against the preset actually used by each retained build, including detection of duplicate or missing named presets. The NPC and PGPatcher adapters exclude only BodySlide preset XML from their asset inventories; generic Synthesis input checking remains unchanged. Publishing another unique preset also retains an existing preset output's priority when no distribution config needs precedence.

423 hub tests pass. The deployed native interface was checked again: all three unnecessary rebuild warnings disappeared without running the helpers, leaving Lydia's pending assignment and the three existing record findings. Skyrim remains unlaunched during this check.

A separate filesystem reconstruction saw additional long-path BodySlide source entries absent from older embedded-MO2 inventories. Its NPC/graphics comparison is therefore not a substitute for the native result. The broader long-path inventory discrepancy remains a follow-up; no recorded inputs were fabricated or rewritten to make this test pass.

## Grouped outfit decisions and neutral-preparation work

The real Bodies & outfits dialog now offers author-declared outfit groups. Selecting CBBE Vanilla Outfits and clicking Use this outfit group chose 44 regular variants in one action. Closing the dialog saved those pending decisions; an independent read of the draft confirmed all 44. Individual outfit controls remain. The batch changes only groups with exactly one matching variant, retaining other selections. The new row exposed a layout overlap at the current window size; the guided page now uses a scroll area to retain all controls without overlapping text.

431 hub tests pass. The neutral-shape recipe module is preparation work, not a connected or completed OBody onboarding flow. It stages only scratch presets and retains geometry zaps, hidden fit corrections and clamps. The actual five core CBBE NeverNude projects produced a separate neutral recipe and explicit CBBE Athletic default without modifying the active shared default or installed meshes.

Expanding that check to the 423 matching outfit groups exposed differing project defaults, first NeckSeam (a hidden authored correction, now retained) and then WristSize. Do not push these details back onto the user or simply zero every project slider. Finish the supported author's neutral-build/default-assignment handling before publishing a global rebuild. The strict existing character_shapes.neutral_preset check also needs to be reconciled with retained project corrections, using actual preparation evidence; it must not be weakened just to accept the recipe. Source behavior was checked against BodySlide 5.8.2 BodySlideApp.cpp: batch builds use project defaults when preset values are absent; hidden sliders are not displayed; TRI export excludes clamps/zaps but can include hidden sliders. Hidden metadata alone therefore does not prove arbitrary downloaded assignments are safe.

Native pending draft now includes the prior shared custom preset plus the selected outfit variants, with outfits enabled. This remains distinct from the applied CBBE Athletic default. Lydia's preset remains saved and unapplied. OBody is still inactive; no new body or outfit build was installed during these checks.
