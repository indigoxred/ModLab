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

