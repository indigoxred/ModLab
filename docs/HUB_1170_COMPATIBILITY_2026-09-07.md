# Skyrim 1.6.1170 compatibility checkpoint

This continues the existing hub alpha after the completed Best of Both Worlds setup. The current allowance began at 06:45:32 UTC on 7 September 2026 and has an 08:45:32 UTC ceiling. It does not renew the wider prototype's scope or imply its completion.

## Changes

- A DLL's missing Address Library database is reported as a dependency to install/enable, separately from its actual declared runtime incompatibility. A newer all-in-one library title does not reject a matching contained database.
- Native findings link the winning mod's recorded Nexus files page when MO2 metadata identifies it. Renaming the installed mod does not lose that link. Unknown metadata does not invent a replacement.
- PapyrusUtil mismatch advice for 1.6.1170 identifies the author's Steam 4.6 candidate and warns against replacing only a DLL inside a framework whose scripts also need matching. Wrong-runtime SKSE installation points to the checked Steam 2.2.8 archive.
- OStim Standalone 7.5.1's documented 1.7 virtual-machine incompatibility overrides its otherwise passing declaration for the exact checked DLL identity. The rule does not reject it on 1.6.1170 or apply to future different binaries.
- The setup screen records the verified 1.6.1170 executable, launcher and shader baseline. Normal recheck and managed launch compare those files; changed files block launch with recovery guidance. Users updating from the earlier alpha must open the setup screen and check once to register this record. Other game folders are not forced onto this baseline.
- Missing official-content advice warns that Steam repair may replace downgraded files. Missing-USSEP guidance for 1.6.1170 links the verified 4.3.8a archive and retains dependent-mod minimum-version qualifications.
- A failed OStim identity read remains an individual inspection failure, preserving the other actionable findings.

These changes reuse existing inspection, findings, setup and launch paths. They do not introduce a runtime manager, automatically exchange framework packages, or use cleaning to repair native mismatches.

## Actual download checks

The product foundation checker read 32 retained DLL members from 11 archives in the original deliberately mixed test folder, evaluating each on both 1.6.1170 and 1.7.104: **64 component/runtime observations**, not 64 installed mods. SHA-256 identifies each input in the [text results](../reports/1170-product-findings-20260907/results.json). No downloaded DLL was executed or installed by this check.

| Supplied component | Result on 1.6.1170 |
|---|---|
| CBPC 1.7.2 FOMOD | Its selected 1.6.1170 variant passes the declaration check; the 1.7.104 variant is rejected. Other choices do not invalidate the whole archive. |
| PapyrusUtil 4.8; JContainers 4.3.2 | Supplied DLLs declare newer runtimes and are rejected. Papyrus advice identifies the 4.6 candidate. |
| SexLab v166d's supplied native components | Both supplied DLLs declare newer runtimes and are rejected. This does not establish the compatibility of another SexLab release. |
| Fuz Ro D'oh 2.5; ConsoleUtilSSE NG 1.6.1; LSARNG's supplied 1.6.1130 build | Inspected declarations do not block 1.6.1170. Dependencies and functionality remain unverified. |
| OStim Standalone 7.5.1 | Declaration does not block 1.6.1170. The same exact DLL is functionally blocked on 1.7 based on the author exception. |
| Old HDT-SMP, OStim 5.7 and Ultimate Combat 3.5 components | Legacy declarations/layout requirements are rejected by the checker for this runtime. No unsupported binary port was attempted. |

The installed game executable, launcher and shaders also passed the source baseline checker against their actual bytes. The diagnostic record was stored outside the game and MO2 configuration. It did not register a deployed-profile baseline or alter game files.

The original **Desktop/Mods** folder remains the deliberate error test set. **Desktop/Mods 1.6** is the user's intended build, still being assembled, and is reserved for a subsequent test after the original folder. Neither folder has been certified as a complete working setup.

## Verification and limits

The focused source review found no Critical/Important issue. Its one file-read handling issue was reproduced and fixed. The full hub suite then passed **249 tests**; these include the real collector/assessment/managed-launch path refusing a changed recorded game file. The test replaces only game fixture hashes and operating-system process checks; it does not start Skyrim.

Earlier installation/FOMOD, LOOT, narrow cleaning, BodySlide, Pandora, SKSE, NPC, Synthesis, PGPatcher and recovery results remain recorded in the [previous delivery](HUB_DELIVERY_2026-09-07.md). Those observations were predominantly on 1.7.104; they are not rerun gameplay evidence on 1.6.1170. The [downgrade delivery](HUB_1170_SETUP_2026-09-07.md) reached the main menu and initialized SKSE 2.2.8 only.

Desktop access is still awaiting the user's availability confirmation. No new desktop/gameplay check, profile rematching, installation into the intended build, or deployed-module update is claimed here. Automatic Wrye Bash, TexGen/DynDOLOD, broad helper acquisition and other unfinished workflows remain outside this checkpoint; see the [quickstart](HUB_QUICKSTART.md). Unknown interactions remain unknown.

## Packaged checkpoint

`dist/ModLab-Hub-0.1.0-alpha-1170-20260907-0712.zip` contains 55 source modules and 57 total members. The builder parsed every module and read back every member. SHA-256: `e9633395ac4a3ba4dce53be8bb8a686a051654fc64f5c4c3cad6db0e3f706f03`; source identity: `89ed515418af6bde2ed3d460cad3714d457d036c8b8062f2a0bda3d5064e0fe5`. Third-party binaries, mods, user profiles and game files are excluded. Packaging does not deploy the update into MO2.

## Sources for the release-specific guidance

- [PapyrusUtil author files](https://www.nexusmods.com/skyrimspecialedition/mods/13048?tab=files): Steam 4.6 lists Skyrim 1.6.1170; 4.8 lists 1.7.104. Checked 7 September 2026.
- [JContainers author files](https://www.nexusmods.com/skyrimspecialedition/mods/16495?tab=files): current supplied 4.3.2 targets 1.7.104; an older 1.6.1170 download remains listed. No exact replacement file ID was verified in this pass.
- [OStim author files](https://www.nexusmods.com/skyrimspecialedition/mods/98163?tab=files): 7.5.1 explicitly does not work on 1.7.x; observation retained from the authenticated page and user screenshot.
- [Steam SKSE 2.2.8](https://www.nexusmods.com/skyrimspecialedition/mods/30379?file_id=792256&tab=files) and [USSEP 4.3.8a](https://www.nexusmods.com/skyrimspecialedition/mods/266?file_id=733846&tab=files): actual archives and included documentation checked in the downgrade delivery.
