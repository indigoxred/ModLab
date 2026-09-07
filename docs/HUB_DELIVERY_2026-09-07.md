# ModLab Hub — additional allowance delivery

This snapshot extends the existing Skyrim/MO2 alpha. The full agreed prototype remains incomplete. The additional four-hour allowance runs from 2026-09-06 17:48:18 to 21:48:18 UTC; this report does not authorize an extension.

## What changed in this allowance

- Packed BSA resources now participate in effective file-provider checks using the game's INI registrations and active plugin names, including when MO2's optional archive cache is empty. Loose-file winners remain authoritative. Unknown/custom archive ordering stays explicit.
- Normal installation/recheck automatically runs the existing read-only xEdit checks on copied plugin inputs, retaining results and reusing them when their relevant inputs match. Diagnostic errors do not authorize cleaning. The existing narrow cleaning rules and author prohibitions remain separate.
- The mixed gameplay batch exposed an important exception to declared-master-only checks: Cutting Room Floor provides an injected keyword used by a generated weapon patch. Read-only checks now load the preceding active plugins too. The same generated patch then passed 324 record checks with zero errors; the mod records were not rewritten to hide the initial diagnostic.
- A per-character NPC appearance picker coordinates selected NPC overrides with their face meshes, face tints and referenced textures. It reuses a pinned author helper, retains source masters and original identities, checks paired resources and rejects divergent selected assets at a shared path. The live case combines Bijin Hulda with Ordinary Women Carlotta.
- New NPC builds retain character choices with their output for coordinated restoration in History. Earlier builds without that recovery context cannot use automatic restore. This extends the existing recovery path instead of adding another framework.
- Queue history now attaches the final setup result after asynchronous helper callbacks, including a later successful recheck after a failure. It preserves individual installer receipts and does not mark unattempted archives installed.
- Fixed an archive classifier that mistook SKSE-dependent mod names for the SKSE loader package. The classifier now inspects archive entries before rejecting an unknown loader.
- Corrected a false FSMP dependency blocker: Menu Framework is optional for physics. An exact FSMP 4.1.1/load-sequence finding explains the observed missing settings panel and the author's JSON/console fallback. Physics remains unverified; native loading alone is not a physics test.

## Evidence and practical limits

The previous snapshot's tested installation, LOOT, BodySlide, Pandora, approved cleaning, SKSE, Synthesis and PGPatcher workflows remain described in [the quickstart](HUB_QUICKSTART.md) and [the previous delivery](HUB_DELIVERY_2026-09-06.md).

Before the final mixed batch, live BSA coverage included 30 archives and 181,303 indexed entries. Fifteen plugin targets had cached xEdit checks. The surviving base Creation quest-stage warning is reported for review, not automatically cleaned and not asserted to be a crash cause. Updated checks load each target's preceding active context, but this still does not establish that every winning interaction is correct.

Installed FSMP 4.1.1, SKSE Menu Framework 3.14.1, XPMSSE 5.06 (Basic skeleton), Engine Fixes 7.0.21 beta, Bijin NPCs 1.2.1 and The Ordinary Women 4.1 through the normal ModLab/MO2 workflows. Engine Fixes used the matching release's preload mechanism; obsolete Part 2 was not added.

Launch `3b59de9d55a4` recorded all six selected native DLLs loading with no failed load entries. Its retained log SHA256 is `4245700190f3b143e62bfe1610c1aee4d5c38922fdaa0cad5f16670f2e798afc`. In the private test save, Hulda's selected Bijin appearance and Carlotta's selected Ordinary Women appearance were visible, and both had normal dialogue. No obvious dark-face or missing/purple texture appeared in those views. Carlotta's front close-up was partly obstructed by her stall; this was a narrow appearance/dialogue test, not full replacer coverage or a long-term save test. Skyrim exited normally and the earlier save was retained.

The game remained in 1920×1080 borderless mode. Standard absolute automated pointer input still has an offset; the approved foreground/PID/executable-guarded relative mouse and scan-code diagnostic allowed unattended game controls. It is test infrastructure and is excluded from the package.

FSMP's settings pages were absent despite both native plugins loading. The matching author source caches the Menu Framework handle during DLL initialization, before the framework is loaded in the observed sequence. This is a likely cause, not a verified third-party fix. No third-party DLL was renamed, patched or rebuilt to hide this result. See the [author's v4 changelog](https://github.com/DaymareOn/hdtSMP64/wiki/10-%E2%80%90-Changelog) and [v4.1.1 integration header](https://github.com/DaymareOn/hdtSMP64/blob/v4.1.1/extern/SKSEMenuFramework/include/SKSEMenuFramework.h).

## Still outside the delivered automation

Automatic Wrye Bash and TexGen/DynDOLOD are not implemented. Broad framework/style compatibility knowledge, general helper acquisition, coordinated SKSE/graphics restoration and FNIS/Nemesis coexistence handling remain incomplete. The full user folder has not been installed or validated. Enderal retains its separate installation/workflow boundary.

The product does useful automatic work for enabled workflows, but it is not yet the complete general modding hub requested. Unknown mod interactions must not be labelled compatible merely because installation, LOOT or xEdit completed. Some findings still require human interpretation; the alpha does not promise arbitrary merging or crash-free gameplay.

## Final verification and package

History operation `7e5b1323da98` restored the 19 NPC appearance files and matching saved choices, then verified the files were effective. The recent live builds had identical appearance bytes and the same two selected providers; this checks recovery integration/provenance, not a visual transition between different choices. Unit tests additionally cover differing choices and rollback if publication fails.

Queue `b7ebeb03ecd6` installed Cutting Room Floor 3.1.26, Run For Your Lives 4.0.7 and Wildcat 7.1.0. Its initial patch check found the missing injected-record context described above. After the correction, Synthesis job `2ffd2111ab77` passed xEdit job `273c541f24a9` and was applied effectively. The original source and failed attempt remain retained.

Queue `05bd1bbb1500` subsequently installed and enabled Immersive Patrols 3.0b. Its final state is `Installations completed; setup needs attention`, linked to setup `f1c762c6450a`, with the individual installation receipt retained. This verifies the asynchronous queue-reporting fix. The final setup has 26 active plugins, 36 indexed BSAs / 181,622 entries and current read-only xEdit results for 20 plugin targets. Immersive Patrols check `44368491239e` reported zero record errors. The known Creation quest-stage warning and review findings remain visible; none of the four newly added gameplay mods was cleaned. Final LOOT result: `8ef4126afffd4baa8dd9893c5a241cd9`.

Launch `2152f377fd11` used SKSE through MO2 with the expanded profile and recorded all six selected DLLs loading, no failed load entries, and SKSE 2.3.1 initialization. Retained log SHA256: `af1c97e9aefceaafcbc11c64925f5d301e703d588aeddfb5a32d6e34ba2c40b7`. The normal Continue menu loaded the private Dawnstar test save. Wildcat Combat appeared in MCM and its page populated with dynamic-combat options and the expected 5/10 stamina attack settings; Run For Your Lives also appeared in the MCM list. This checks startup/menu initialization, not combat results, fleeing AI, natural patrol encounters or Cutting Room Floor quests. Skyrim closed normally without saving. The retained feature-test ESS and SKSE co-save hashes matched their pre-launch values.

Automatic approval review rejected one proposed console text-entry step because it could not establish console focus from the screenshot. No text was sent. Closing the overlay and using the normal Continue menu provided a successful alternative; no approval or user assistance was needed.

Current source suite: 229 hub tests passed in 1.304 seconds after the NPC recovery, injected-record context, target validation and queue-reporting fixes. All 53 source modules match the deployed instance. No commit or push is implied by this delivery.

Package: `dist/ModLab-Hub-0.1.0-alpha-20260907-0734.zip` (53 modules, 55 members). The builder parsed all modules and read back every archived member. ZIP SHA256: `9114ff103721fde95c566a898a08a9dca4985b17e6bba5a7a8de97c9d3b5e321`; source identity: `1493f8beac25069738ece81f6b6c33d86b2ba7d36faeaae8bf62288c749e03c2`. Games, helpers, downloads, test instrumentation and user data are excluded. The previous ZIP remains intact.

The tested instance is `workspace/hub-test-20260906/app/ModOrganizer.exe`; choose profile **ModLab Test**, then **ModLab** from the tools menu. It already contains this deployed build. Both Skyrim and MO2 were closed normally after testing, with no helper processes left running. Saved source archives, prior generated outputs, private saves and operation records remain available.

Next work should prioritize reducing unresolved user-facing decisions for common supported combinations, then complete the remaining planned helper workflows with a real mixed-install test. Do not restart broad infrastructure validation. The full prototype goal has not been marked complete.


## Additional download checks within the remaining allowance

The unchanged packaged runtime inspector checked 32 DLL variants from 11 further supplied archives without installing or executing them. It identified mismatched supplied Fuz Ro D'oh, old HDT-SMP, LSARNG, old OStim and Ultimate Combat builds. The CBPC FOMOD has one matching 1.7.104 variant among 19 alternatives; the whole archive must not be rejected for containing older choices. The standalone and SexLab-bundled PapyrusUtil DLLs have identical hashes. Other accepted declarations are not gameplay or framework-compatibility verdicts. See [the retained readiness report](../reports/remaining-native-20260907/READINESS.md) and its exact archive/member hashes. No product code, installed profile or package changed after the delivered build.

Follow-up PapyrusUtil comparison: of 13 shared paths, the DLL and six source scripts match exactly; six compiled PEX files differ. The report does not classify those compiled scripts as compatible or incompatible based only on hashes. Compiler/debug metadata and executable instructions were not compared. The profile remains unchanged; these results narrow the remaining question without treating every overlap as a failure.
