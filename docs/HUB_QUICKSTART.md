# ModLab Hub — development build

ModLab coordinates a Skyrim SE/AE setup inside Mod Organizer 2. It uses MO2's installation choices and virtual files, then runs supported checks and helpers for your selected setup. This alpha is under active testing; the full prototype remains incomplete.

## Install and open

1. Use the complete official **MO2 2.5.2** application, including its Python and installer plugins. Select the intended Skyrim Special Edition instance/profile. Use separate mod storage for testing: separate profiles alone share installed mod files.
2. Close MO2 and Skyrim. Extract the ZIP beside `ModOrganizer.exe`, producing `plugins/modlab_hub/__init__.py`. This is an application plugin, not a Skyrim mod. When updating, retain the old `plugins/modlab_hub` outside the plugins directory before replacing it. Preserve `plugins/data`, which contains saved tool locations.
3. Start MO2 and open **ModLab** from its tools menu. Confirm the profile, game path and runtime shown at the top.

The package contains ModLab code, instructions and a source-hash manifest. It contains no games, mods, MO2, third-party helpers or user profiles. A matching package hash identifies the build; it does not establish compatibility.

## Normal workflow

- **Setup / tool locations** provides official links, placement instructions and a one-time executable locator. Keep complete helper packages together: a lone EXE may be missing its libraries/resources. Most helpers still require a complete local installation. The NPC workflow obtains and builds a pinned NPC Plugin Chooser; supported workflows can obtain their managed Microsoft .NET runtime/SDK. This is not automatic acquisition of every helper.
- **Install archives** accepts several ZIP/7z/RAR files. MO2 installs them sequentially, displaying author-provided FOMOD choices. ModLab retains individual outcomes and rechecks the resulting setup.
- **Mod instructions** displays local text, Markdown and HTML instructions from installed mod folders. Choose the mod and document; the folder button provides access to other formats. Instructions are author guidance, not a claim that their steps have been completed. HTML is shown as text.
- **Body and outfit choices**, **Animation choices** and **Patch choices** save applicable helper selections. They are not required for every setup. Saved selections can rebuild when inputs change.
- **NPC appearances** lists installed characters and their source mods. Choose different replacers for different characters, or apply one provider to matching filtered rows. ModLab pairs the chosen NPC records with their FaceGen meshes, face tints and checked face textures. Original source mods remain dependencies; other characters follow normal load order. Missing pairs and divergent shared assets stop publication. Generated output is checked with xEdit and placed before downstream patches. Existing saves and body/head-part combinations still require an appropriate in-game check.
- **Recheck and finish setup** coordinates LOOT, specifically eligible cleaning, saved BodySlide/Pandora/NPC/Synthesis work and selected graphics work. Read-only xEdit checks run automatically for installed mod/generated plugins and Creations, then reuse results while their checked inputs remain unchanged. Findings identify affected mods/files, explain the concern and give the next action. Missing tools, pending choices and failed outputs remain visible.
- Web links in a selected finding are clickable. They open only when selected; displaying advice does not download or install its requirements.
- **Launch Skyrim** uses SKSE through MO2 when installed. Keep the hub open during the launch for startup observation. Startup checks do not establish that every mod feature works.

While the hub is open, MO2 mod/profile/plugin changes request a recheck. This is not a filesystem watcher for arbitrary changes outside MO2. Return to the hub and recheck after manual changes.

LOOT contributes advice and ordering. ModLab also checks masters, effective files, runtime declarations and managed outputs. Overlap alone is not an incompatibility. Unknowns are not silently declared compatible. No blanket cleaning runs: an exact reviewed rule is required, and applicable do-not-clean instructions take precedence. Current automatic cleaning coverage is narrow. A crash log or ITM count does not authorize cleaning.

Packed BSA files participate in the file-provider checks using the game's INI registrations and active plugin names; the optional MO2 archive cache is not required. Custom archive-ordering cases can still need review. File coverage does not establish semantic record, texture or script compatibility. Read-only xEdit checks load the active plugins preceding each target, including providers of injected records that are not declared masters. A later compatibility patch may correct a winning record while a source-file warning remains. Cleaning retains its separate target-and-masters scope.

## Recovery and fallback

**History / recovery** shows outcomes, operation IDs and retained paths. It restores checked prior BodySlide, Pandora and cleaned-plugin output. New NPC builds retain matching character choices with their face output; new Synthesis builds retain matching settings and FormID allocations. Older builds without the necessary context cannot use automatic restore. Use a save from before the build being undone: file restoration cannot undo changes already stored in a newer save. Recheck regenerates affected downstream output. Other records explain the available recovery route; not every operation has one-click undo. Modified/untracked output is preserved rather than overwritten by an assumed rollback.

**Advanced tools** includes LOOT details/order restore, xEdit, animation/graphics choices, queue resume, reports and the mods folder. Original MO2 remains available with its installer and executable configuration.

## Tested and outstanding

The checks described below were performed on earlier builds, primarily Skyrim 1.7.104. They are retained evidence of those workflows, not gameplay verification of the current 1.6.1170 setup. The 1.6.1170 baseline has reached the main menu and initialized SKSE 2.2.8; gameplay and the full rematched profile remain untested.

Real product checks have exercised MO2/FOMOD installation, LOOT execution/order handling, BodySlide generation/restore, Pandora generation, specifically approved xEdit cleaning, SKSE installation/in-game launch, and a selected Synthesis patch followed by xEdit record checks. Short in-game body/outfit and input checks were performed. CrashLogger loaded in a checked launch; no crash was deliberately induced.

Native runtime inspection has also rejected the supplied RaceMenu binary for the current game and prevented a managed launch. The live startup observer recorded CrashLogger loading through SKSE and displayed that result in ModLab. Cancelling FSMP's installer preserved a prior completed installation and recorded the unfinished request. After restarting MO2, selecting that request reopened only the cancelled installer. A separate live queue test cancelled FSMP, then successfully installed the unattempted Media Keys archive without retrying FSMP.

The live PGPatcher mesh-repair workflow generated and applied 631 checked meshes plus one plugin, checked that plugin with xEdit, and checked final order with LOOT. Two confirmed base-game archive notices are explained using the helper author's guidance; unknown/mod archive warnings still withhold output. The resulting profile launched and loaded a disposable save. This does not verify all generated meshes, ENB/PBR appearance or quest behavior.

Subsequent real installations exercised Alternate Start's missing-USSEP blocker and resolution, then a two-mod Ordinator/Footprints queue with automatic helper rechecks. In game, Ordinator's expanded Destruction tree matched its author documentation. Alternate Start's normal statue dialogue, Dawnstar arrival and resulting journal entry worked. Distinct boot prints were visible on snow, and Footprints' MCM reported its player effect active. These were narrow feature checks, not complete perk, NPC or quest tests. A separate disposable save retains this setup.

The game used 1920×1080 borderless mode. Standard automated pointer coordinates remain unreliable, but guarded relative mouse and scan-code input supported menus, movement and camera control without user assistance in the latest test. This test-only input fallback is not shipped as a ModLab feature. Media Keys loaded successfully; its media-key behavior has not been tested.

The latest fixes prevent publishing output or changing plugin order while Skyrim runs, retain identical cleaned output and its existing winning priority instead of triggering unnecessary downstream rebuilds, and show the actual native mismatch reason outside Technical details. ModLab derives active plugin positions from MO2's current priorities because its cached positions can remain stale after sorting. Focused regressions pass. The real recheck retained cleaned output without another Synthesis or graphics build. Coordinated Synthesis restoration was exercised through History, including retained settings and allocation files.

Additional real checks covered 30 selected BSAs / 181,303 entries and cached xEdit results for 15 plugins before the next installation batch. A two-character patch selected Hulda from Bijin NPCs and Carlotta from The Ordinary Women in the same profile; paired assets and source hashes were checked, and both characters' replacers and normal dialogue appeared in game. This was a short inspection in a disposable save, not complete coverage of either replacer or long-term save compatibility.

FSMP 4.1.1 and Menu Framework loaded, but the expected FSMP settings pages were absent in two game checks. ModLab now reports the observed release/load-sequence concern and the JSON/console configuration fallback. Menu Framework is optional for FSMP physics; it is not a launch requirement. Physics simulation remains unverified. Engine Fixes 7.0.21 beta loaded through its current preload mechanism; no obsolete Part 2 was added, and individual engine fixes were not exhaustively tested.

The final mixed batch added Cutting Room Floor 3.1.26, Run For Your Lives 4.0.7, Wildcat 7.1.0 and Immersive Patrols 3.0b. Automatic rechecks finished with 36 indexed BSAs / 181,622 entries and current xEdit results for 20 plugin targets; the known Creation quest-stage warning remains. A generated weapon patch exposed a missing injected-record provider in the earlier isolated check; including the preceding active plugins resolved that false rejection without altering the records. The final installation queue links its completed setup report and retains the remaining review findings. The expanded profile loaded the private Dawnstar save through SKSE/MO2. Wildcat's MCM page populated and Run For Your Lives appeared in MCM; combat, fleeing AI, patrol encounters and the new quest content were not tested. No blanket cleaning or long-term compatibility claim follows from this batch.
The full mixed-mod folder has not been installed or validated. Automatic Wrye Bash and TexGen/DynDOLOD are not implemented. FNIS/Nemesis coexistence is not automatically managed. Broader style/dependency coverage, coordinated SKSE/graphics recovery and helper acquisition remain incomplete. Listing a helper's location does not mean its workflow is automated.

Targeted in-game checks can verify a specific NPC, location or feature. They do not replace quest playthroughs, establish save compatibility or prove the whole game crash-free. Enderal's separate installation/workflow is outside this Skyrim profile's scope.

## Build from source

From the repository using Python 3.12:

```powershell
python -B tools/package_hub.py --output dist/ModLab-Hub-alpha.zip
```

The output must not exist. The builder parses sources and reads back every archived member. Run the hub suite separately with `python -B -m unittest discover -s tests -p "test_hub_*.py"`. Packaging does not test the product in MO2.

# Skyrim 1.6.1170 setup (7 September 2026)

Setup / tool locations → Skyrim 1.6.1170 setup / downgrade offers a narrow Steam 1.7.104 route using wSkeever's existing patcher. It prepares verified backups, launches the known downloaded patcher from the selected game directory, and checks the executable, launcher and shaders against the upstream 1.6.1170 hashes. The tool itself is downloaded separately; it is not bundled.

The linked Steam SKSE 2.2.8 archive is recognized by the existing recoverable installer. The linked USSEP 4.3.8a archive's included readme supports 1.6.1130 and later. Keep official game data and the four free Creation components. This is a Best of Both Worlds executable/shader downgrade, not an old-data Steam reinstall. The patcher's embedded SKSE link is stale; use ModLab's corrected link.

After updating ModLab, open this setup screen and run its baseline check once. A matching executable, launcher and shader set is recorded for that game folder. Normal rechecks and managed launches then detect changed/missing baseline files and stop launch until they are resolved. This does not verify every retained official content file. Other game folders without this saved setup record are not forced onto 1.6.1170.

Choose native DLL releases for the **running executable**, even when newer official content is retained. A missing Address Library database is reported separately from an incompatible DLL. All-in-one library packages can contain the required older database despite a newer title. Download links use the supplying mod's recorded Nexus metadata where available; a link is not a verified replacement. Frameworks may bundle matching DLLs and scripts, so replacing only the DLL can leave another mismatch.

The author-documented OStim 7.5.1 functional failure on 1.7 is scoped to the checked DLL's exact identity. A passing DLL declaration does not override that exception; neither this rule nor declaration checks establish that unknown releases or complete framework combinations work.

Use a copied profile and fresh test save. Existing native plugins may need different downloads or variants. A successful baseline check is not a promise that every previously installed mod works. Recheck the selected profile and test its intended features.
