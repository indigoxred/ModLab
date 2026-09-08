# Menu browsing and requirement actions — 2026-09-08

Opening and closing BodySlide, character, Pandora and gameplay-patch choices no longer withdraws graphics output or schedules the preparation pipeline. BodySlide/Pandora browse catalogs without copying helper installations or obtaining runtimes. Explicit Apply validates the captured setup and selections, then begins preparation. Dialog dismissal remains blocked until asynchronous output publication ends; failed attempts restore only still-current downstream graphics output rather than retrying the failed helper.

Requirement actions now keep the affected mods, available source advice and installed instructions together. Installed inactive plugin dependencies can be enabled with profile validation and rollback. Findings with no verified repair say so. Engine Fixes preloaders are distinguished from normal Data archives; automatic preloader installation remains unfinished.

Validation: 290 hub tests passed; Python compilation passed. Actual MO2-hosted open/Close checks covered all four changed dialogs. Setup/build directory counts stayed at 120/10/27/25/24 (setup/BodySlide/NPC/Pandora/PGPatcher), with no LOOT window. Saved choice files and plugin/load-order hashes stayed unchanged. Address Library was found disabled during navigation and restored; the final modlist hash matches the pre-check baseline. All deployed Python files match source. No game was launched.

This increment does not complete the approved beginner customization workflow. Shared body defaults, per-character component controls, scoped customization and guided outfit decisions remain required; the existing detailed selectors are still the old technical controls.
