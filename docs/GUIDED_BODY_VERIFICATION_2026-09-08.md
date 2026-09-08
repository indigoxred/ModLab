# Guided shared body preparation — 8 September 2026

This is a verified implementation increment in the ongoing ModLab workflow goal, not completion of the full beginner customization experience.

The Bodies & outfits screen now starts with female/male shared body, compatible shape preset, and matching body-part/outfit choices. It uses the existing BodySlide generation, staged publication and effective-provider verification. The original full project controls remain in Advanced. Saved defaults are profile-specific, independent for female/male, and recorded only against the exact successfully applied build. A failed new request cannot reuse a previous successful build's result.

Outfit eligibility comes from winning ARMO/ARMA game records, with NPC/RACE skin references excluded. Male and female outfit paths are selected separately, while skin paths for both remain protected. Missing ownership information disables automatic outfit matching with a specific explanation; shared body and Advanced choices stay available. This is static record evidence, not proof about scripted runtime skin changes.

## Actual MO2 test

Profile: ModLab 1170 Test, Skyrim 1.6.1170, 52 active mods / 36 plugins.

- Selected CBBE NeverNude, CBBE Athletic, matching CBBE feet/hands and first-person body/hands; outfit batch left off.
- ModLab ran BodySlide and applied five projects / ten expected NIF files. Output existence/header checks passed and effective_output_issues was empty.
- Receipt: workspace/hub-test-20260906/builds/bodyslide/e1dc9ef72c29/operation.json. Output: ModLab - ModLab 1170 Test - BodySlide [68caae]. Prior output retained by the existing publisher.
- Saved female preference names that exact receipt. Reopening the deployed UI restored body, preset and four component choices.
- Male selector showed no installed shared male BodySlide body and disabled preparation. It did not change the saved female preference.
- Fixed Qt layout ownership: hidden Advanced widgets no longer paint behind the guided tab. Advanced controls remain accessible.
- Browsing both tabs and closing without applying preserved hashes of enabled mods, plugin order and saved defaults, with no additional helper jobs or LOOT directories.
- After the actual applied build, the intended follow-up preparation completed; existing unresolved record findings remain visible. No claim that the entire mod setup is ready.
- 305 hub tests pass; Python compilation and git diff whitespace check pass. Source/deployed plugin files match.

The disposable profile retains the CBBE NeverNude/Athletic test output. No Skyrim launch or gameplay verification was performed in this increment. The separate MO2 Downloader installation was untouched.

## Remaining work within the approved goal

Per-character component selectors and customization, physics-aware suggestions, automatic preparation of newly installed outfits using accepted preferences, and simpler grouping of large outfit sets remain unfinished. The current guided tree can still expose many project alternatives, including non-clothing helper objects represented as armor records. Advanced retains project combinations that the simplified one-per-overlap-group picker cannot express. These are continuing requirements, not excluded features.
