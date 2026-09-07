# Skyrim 1.6.1170 desktop checkpoint — 7 September 2026

This follows the [compatibility checkpoint](HUB_1170_COMPATIBILITY_2026-09-07.md). Its statements about deployment awaiting desktop availability are now superseded. The alpha is not a completed or certified modlist.

## Verified in the running product

- Deployed the 0730 package after closing MO2 and Skyrim, retaining the previous plugin and checking all packaged payload hashes.
- ModLab's Setup screen checked and saved the actual 1.6.1170 executable, launcher and shader baseline in plugin storage. It recognized the installed SKSE 2.2.8 loader. This is now a product record, not only an external diagnostic.
- Used **ModLab → Install archives** for PapyrusUtil 4.8 from the deliberately mixed test folder. Automatic inspection identified its declared 1.7.104 runtime, named the effective supplying mod, explained why sorting/cleaning cannot fix that mismatch, and linked the author files page with the 4.6 Steam candidate. The advice also warned against replacing only a framework's bundled DLL and ignoring its scripts/dependencies.
- Used **ModLab → Launch Skyrim** with that blocker present. ModLab refused the launch and displayed the reason; no Skyrim process started.
- Installed the existing PapyrusUtil 4.6 archive through the same product workflow. Its effective DLL cleared that runtime blocker. Disabled the 4.8 test package in this profile afterward, preserving the downloaded archive and installed files. This establishes the runtime decision, not complete PapyrusUtil or dependent-mod functionality.
- Recheck ran LOOT and found the existing plugin order already matched its checked proposal. ModLab also generated and activated a new, profile-specific Pandora output. In-game animation verification remains pending.

Local evidence under `workspace/hub-test-20260906`: setup receipts `bb9bbea256e9` (wrong runtime), `3be70cb52df0` (matching replacement), `57e53f0bf04c` (final recheck), and Pandora build `a2768f6446a0`. These are local test records, not bundled user data.

## Copied-profile failure and small product repair

The copied test profile still contains NPC, Synthesis and graphics outputs owned by the original profile. NPC generation stopped because `ModLab NPC Appearance.esp` was supplied by that original output. Existing output was preserved.

The diagnostic previously named only the plugin. It now names both the supplying mod and the intended new output, explains disabling an inherited generated output in the current profile before rebuilding, and distinguishes an independently installed patch-name conflict. The same ownership check remains enforced. Verified the revised explanation in the running ModLab panel after restarting the host. No profile-migration subsystem was added, and the inherited outputs have not yet been reconciled.

## Game input diagnostic

In a separate baseline game test, standard Computer Use inputs did not reliably operate Skyrim. A bounded foreground-checked scan-code/relative-mouse diagnostic, run with access to the interactive desktop, did: it opened and cancelled New Game, moved the actual game cursor, entered `coc qasmoke`, loaded the test cell and moved the player without user assistance. Skyrim was then closed normally before plugin deployment.

The same diagnostic inside the execution sandbox could not observe the foreground window. That explains the diagnostic's earlier focus failure; it does not prove the precise fault in the standard Computer Use backend. This is a verified testing workaround, not a repaired general input backend. Precise mouse clicking and repeatability across focus/display changes remain unverified. No display settings or game mods were changed for this probe.

## Delivery and remaining work

All **251 existing hub tests passed** after the diagnostic wording change. Package `dist/ModLab-Hub-0.1.0-alpha-1170-20260907-0835.zip` parsed 55 modules and passed 57-member readback. SHA-256: `e577d184bb951b30ee6e9358af3ec572201bb4bf1507e9a1c93294cb9deeba2b`. Source identity: `8b8da3645e849cfb4aae9cd19de04a5512687851950e4b8682b9f4ab0189a684`. The only source change from 0730 is the ownership explanation; that module was backed up and deployed before its live verification.

The original baseline remains separate. The mixed test profile still has unreconciled older-profile outputs and packages from the 1.7 setup, plus pending record/native checks. It must not be treated as ready for gameplay. The full intended `Mods 1.6` plus `Stim 1.6` installation has not been tested. The matched SexLab 1.66b archive has only been inspected, not installed or functionally checked.

Continue by reconciling the selected test profile's matching packages and generated outputs, then finish applicable checks and narrow in-game observations. After the deliberate-error cases, test the intended folders together. Preserve the existing queue/FOMOD and helper workflows. Wrye Bash, TexGen/DynDOLOD, broader helper acquisition and remaining dependency/semantic conflict coverage are still unfinished; this checkpoint does not remove those obligations.
