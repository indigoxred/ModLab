# Character shape integration — implementation checkpoint

The approved workflow includes shared body/shape defaults and optional character-specific customization. OBody is a possible helper for the latter, not a requirement for ordinary body/outfit builds or appearance packages. ModLab must handle helper acquisition and setup when requested; it cannot assume a user's machine already has it.

## Implemented in this increment

`obody_config.plan_config` prepares deterministic, explicit NPC base-identity assignments from an existing retained upstream configuration. It preserves other actors, named fallbacks, general distribution, outfit settings and unknown additional settings. It validates structure, duplicate JSON keys, semantic aliases, exact available preset names and selected character presence. Specific exclusions are retained and reported rather than removed. A source fingerprint detects outside edits before saved intent is replayed.

Light-plugin handling accepts explicit classification and `.esl` owners. An FE-prefixed identity in an otherwise unclassified owner requires classification rather than guessing. Inspected character/preset maps reject case aliases. Removing a saved selection is implemented by rebuilding from retained upstream configuration without that selection, not by deleting someone else's original rule.

This is a configuration preparation component. It is not yet connected to the character UI or active-output publication. It does not prove preset/body compatibility, install dependencies, change existing-save assignments, or establish a new distribution policy.

## Verification

- Nine focused tests cover scope preservation, compact IDs, unavailable choices, blacklist precedence, duplicate aliases, outside edits and restoring upstream rules.
- The supplied OBody NG 4.4.3 archive's default configuration was read without extracting/installing the mod. A candidate Lydia/CBBE Athletic assignment changed only `npcFormID`, retained all 21 schema property names and restored exactly when choices were omitted.
- Source config SHA-256: `914db72d8e603fa63622f7e9f16336c4be61d9b84157284ed1dce3f5ec89893d`.
- The candidate was not published. No OBody activation or gameplay verification was performed.

## Remaining integration, in execution order

1. Optional capability setup: inspect effective helper/dependency files and matched runtime; route missing components through acquisition/install/resume. Preserve the ordinary workflow when not requested.
2. Inspect actual NPC body ownership and compatible presets. Private body paths cannot be treated as shared bodies. Check body/outfit morph output and RaceMenu configuration before offering an applicable shape.
3. For a fresh OBody setup, explicitly configure how existing shared defaults and exceptions are retained. Do not enable its default random distribution just to customize one actor. Account for neutral base builds and ORefit settings.
4. Connect shape selection and scoped Customize to the character panel, publish a checked managed configuration with provenance, and reconcile effective-provider changes. Keep face/body/skin/hair/physics choices distinct where installed components support separation.
5. Explain any required actor reset for existing saves; do not report configuration publication as proof of in-game application. Supply the user a focused check at handover.

The guide documents specific exclusion precedence and notes that already assigned actors retain their saved presets until reset: [OBody configuration guide](https://www.nexusmods.com/skyrimspecialedition/articles/4756). Neutral base and morph preparation requirements come from the [installation guide](https://www.nexusmods.com/skyrimspecialedition/articles/4580); an author-supplied neutral preset need not have every slider numerically zero.

## Optional setup integration checkpoint

The ordinary workflow does not require OBody. An explicit individual body-shape support action checks required effective files, active plugins and RaceMenu controller settings, then uses the existing acquisition/install/recheck flow. With OBody present, these checks also run during preparation. Archive-contained scripts are distinguished from proven missing files; native DLLs must be loose files. File presence alone does not establish runtime compatibility or a prepared character.

The two supported RaceMenu changes are explicit actions: enable body morphs, or choose OBody instead of BodyGen. They create a managed Character Shapes output, preserving an exact source-settings snapshot and the previous output. Changed source files, providers, profiles and manually edited managed output prevent silent replay. Successful activation is reported only after the effective path and bytes are checked. Terminal failures release the UI operation guard, and retained refresh callbacks cannot later activate a failed operation.

Verification: 342 focused hub tests passed, including publication, source preservation, profile changes, activation failure, final-journal failure and late refresh callbacks. Independent review found no remaining important issues in this increment. A read-only check of this machine's enabled loose files produced no optional-helper findings during ordinary preparation; explicitly requesting support identified OBody setup and the BodyGen controller choice. Uninspected archive scripts remained Unknown. The RaceMenu source was unchanged; no settings output was published.

Desktop deployment is pending after the Escape interruption. Individual shape selection, neutral body/outfit preparation, scoped custom presets and OBody assignment publication remain incomplete. The optional setup entry does not represent those features as ready.


## Character body ownership checkpoint

The character directory now resolves static base-actor body ownership through winning NPC traits, skin armor, applicable race armatures and texture-set records. It reuses the directory's parsed records instead of scanning plugins twice. The panel names current loose body and base-skin providers; plugin IDs and paths are secondary evidence. Record ownership alone is not file ownership.

Resolution respects the actor's Use Traits flag, master identities, deletion overrides, sex, armor-race links, weight variants and the distinction between third-person torso meshes and first-person arms. A private/replacer body means different from the race default, not exclusive to one actor. Editing its shared file is not authorized by that classification. Leveled/missing/cyclic templates, missing armatures and overlapping world-model slots remain unresolved for customization, without rejecting installation of the mod. Slot-priority selection, texture-swap evaluation, embedded mesh textures and existing-save/script overrides are not claimed verified.

Read-only profile evidence: Lydia uses shared body meshes supplied by the existing ModLab BodySlide output and CBBE base-skin textures. Hulda and Adrianne use Bijin NPCs body meshes and base-skin textures; their body paths are shared with other Bijin actors. Carlotta has a separate skin record from The Ordinary Women but resolves to the shared body meshes and CBBE base-skin paths. Indexing the active profile's records took approximately 0.4 seconds. This check did not publish files or test gameplay.

Validation: 354 hub tests passed, including new cases for inheritance, winning private-body overrides, shared-mesh private-skin records, incorrect races, fixed-weight meshes, deleted armatures, first-person separation, overlapping slots and file-provider labels, including MO2 Overwrite and external paths. Desktop deployment/rendering remains pending; these current-assignment displays are not the finished per-character body/skin/shape selectors.

Field definitions were checked against [TES5Edit's Skyrim record definitions](https://raw.githubusercontent.com/TES5Edit/TES5Edit/dev-4.1.6/Core/wbDefinitionsTES5.pas). The next implementation step is to use resolved ownership and checked morph capability to offer applicable character shape choices and preserve shared defaults without assuming OBody exists.



### Linked morph output gate

Morph-enabled BodySlide jobs now inspect generated NIFs in an isolated native reader before making an install archive. A usable TRI next to a mesh is insufficient: the loaded scene must refer to it, matching shape names must exist, and referenced vertex indices must fit. Missing/conflicting links or changed output stop publication; source and prior installed output remain intact. This establishes file linkage, not neutral base shape, topology equivalence, physics or appearance.

The reader visits the reachable scene from its root, including sibling nodes without geometry. `scripts/verify_body_mesh_native.py` generates three original triangle fixtures for a sibling-only link, competing links, and an unreachable node. It requires an existing .NET SDK and the existing niflysharp 1.1.0 library; it downloads nothing. Native production inspection is read-only.

Desktop check after deploying 7a482a9: Lydia is searchable and shows the shared generated body and CBBE skin providers. Closing the character panel returned promptly without a LOOT window in this observation. OBody remains optional and uninstalled. The full character editor is still in progress.
