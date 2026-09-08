# Independent character body assignment: implementation in progress

The actor-local record mechanism is implemented in body_assignment.py and npc_body_helper.py. It is not yet connected to the production character screen or automatically applied to live profiles. No live NPC choices, game files or source mods were changed by these tests.

The planner resolves the race's shared skin armor for an individual NPC, evaluates the proposed armatures through the existing BodyIndex, and leaves the source index untouched. Trait inheritance is held for explicit handling. The separately built body-reference-v3 adaptation of the pinned NPC Plugin Chooser updates only the selected actors' WNAM references after face forwarding. It can add a body-only actor override without copying a new FaceGen pair. Source armor, meshes, textures and other actors are not edited. The original cached upstream helper is preserved and the adaptation has its own source/binary receipt.

Native verification uses self-created NPC and armor records, invokes the actual adaptation core, writes/reopens the plugin, and independently parses before/after records with ModLab's reader. Every non-WNAM NPC field stayed identical; the selected appearance height and actor weight were retained; source and second actor were unchanged. This is not a full MO2 publication test or a visual skin-compatibility result.

Run: python scripts/verify_npc_body_native.py --tools workspace/hub-test-20260906/tools --output workspace/body-assignment-native

## Bijin and the next integration step

A body-reference change is insufficient to claim a supported face/body/skin combination. Shared body references can also change body textures while the selected face retains private paths. The next integration must resolve the resulting head/body/hand/foot texture sources and applicable body-family/patch requirements before offering preparation, then persist actor intent, validate generated references/assets and effective output, and expose the actual choice in the existing Qt panel. Separate skin selection and individual shape/physics controls remain unfinished; this backend does not satisfy those controls by itself.

Primary-source examples checked on 2026-09-08:

- https://www.nexusmods.com/skyrimspecialedition/mods/57788 — author specifies a normal-map mismatch with Bijin Skin and CBBE Muscle Solution; this is a combination-specific patch, not a universal Bijin repair.
- https://www.nexusmods.com/skyrimspecialedition/mods/72957 — author describes Bijin AIO De-Standalone as using the default female body and skin for covered NPCs. Its existence supports investigating reuse, not assuming all selected Bijin variants or combinations match it.

The work preserves the broader independent face/body/skin/shape workflow requirement. Do not present this module, or the current whole-appearance selector, as the completed character customizer.
