# Character-first appearance workflow — 8 September 2026

Increment of the active guided-workflow goal. Independent face/body/skin/shape/hair/physics customization remains unfinished; this increment supplies reliable character lookup and an actual appearance-package application flow.

## Changed behavior

The main character screen uses a searchable character directory instead of only listing NPCs for which an installed mod supplies FaceGen assets. It reads localized display names from the active profile's loose or packed string tables. Archive order selects the winning table; conflicting tables at the same loading position remain unresolved. Inspection errors retain the available replacement list and Advanced controls with an explanation.

The default view lists unique characters; searches also find other matching characters, and explicit filters expose all records or characters with installed replacements. A character panel offers only that character's appearance providers, labelled with their supplying mod names. Technical plugin identities and the original bulk controls remain in Advanced. Changing a choice there refreshes the character panel when returning to it.

The panel accurately describes the existing NPC Plugin Chooser operation as a package: paired face data/assets, hair and any body/skin assignment supplied by the selected appearance mod. It does not claim independent component customization. No source plugin is rewritten, and simply browsing or changing pending selections does not generate output.

## Evidence

- Pure directory inspection of this test profile: 6,631 NPC records, 1,326 marked unique. Lydia resolves from `HousecarlWhiterun` to her displayed name. She has no installed replacement FaceGen option in the current test profile; she is now visible rather than omitted.
- Actual MO2-hosted Qt screen: searched Lydia and Adrianne. Lydia's panel explained the absence of a replacement; Adrianne offered Bijin NPCs. No unrelated Apocalypse source appeared in either character's selector.
- Changed Adrianne to Bijin through Advanced, returned to Characters, and verified that the displayed choice and explanation matched the pending selection. Profile/order/default hashes and helper/LOOT records remained unchanged before Apply.
- Clicked Apply in the character-first screen. NPC Plugin Chooser generated the independent provider runs and combined patch for Hulda (Bijin), Carlotta (The Ordinary Women), and Adrianne (Bijin). The first two were retained existing choices.
- Receipt: `workspace/hub-test-20260906/builds/npc/66e65d55f7b3/operation.json`. Status: selected appearances applied, no error. Saved choices identify the same three NPCs and 26 output files. The workflow checks exact exported NPC identities, paired FaceGen files, referenced textures, available masters and effective installed providers before recording success.
- Follow-up setup reported `npc-current: Selected NPC appearances applied: 3`. Existing unrelated record findings still require attention. Gameplay appearance is left to the user.
- 311 hub tests passed, including localized names, absent replacers, master references, winning/deleted NPC records, bounded string-table parsing and archive-order precedence. Compilation passed. Code review identified and led to the Advanced synchronization correction and name-table fallback.

The disposable profile retains this three-character test selection and the prior verified shared-body setup. No game launch or external downloader-instance changes were made.

## Continuing requirements

Add real separately scoped body/skin/shape assignments and the helper customization flow, show default inheritance and deliberate exceptions, then connect newly installed outfits to accepted preferences. Hair/physics alternatives must use a supported preparation mechanism and preserve the selected appearance's requirements. A working appearance-package selector is a foundation for that work, not its replacement.
