# Independent character skin selection — 9 September 2026

This advances the active character workflow; it does not complete independent hair/physics controls, all body families, or the broader ModLab goal.

## User flow

Character appearances now offers a separate **Skin** choice labelled with the installed mod name. Keeping the appearance's skin remains the default. Selecting a different skin leaves the saved face/hair and body choices intact. Apply prepares the selected characters together, checks the output, publishes it through MO2 and runs the existing follow-up setup workflow. Pending choices survive closing, restart and preparation failures. Reset preserves recoverable output and cannot discard a newer pending skin request.

The initial supported conversion proves CBBE body compatibility against the original installer/archive and installed bytes. It discovers complete head/body/hands sets with diffuse, normal, subsurface and specular maps. The original download is resolved from MO2 metadata or located once from the panel. No original archive, mod plugin or source mesh is edited.

The UI's prepared skin name is checked against the effective body texture references and generated skin bytes. If those have changed, it falls back to describing the actual file providers rather than presenting the saved source as current.

## Preparation and checks

- Clone the selected character's skin armor, matching armor addons and texture records into the generated plugin. Keep the selected body models, weights, priorities and unrelated texture channels.
- Clone single-texture swap lists for that character. The Bijin test uses Skyrim's `SkinFemaleHumanBody` list; the original blanket refusal of any swap list was too restrictive. Multiple/empty variants and missing texture references still require a matching conversion; they are not silently flattened.
- Give the character unique output texture paths. Change only the recognized face-skin shader's four skin maps. Preserve baked character tint, hair, eyes, detail references, geometry and other shader data.
- Reopen the generated face and independently compare texture references and binary block hashes. Nifly's normalization of texture path spelling is accepted only when the referenced files are equivalent; non-texture blocks must remain byte-identical.
- Independently inspect exported body/head texture records, actor-local swap lists, paired face assets, source hashes and xEdit record results. Verify MO2 activation and effective output before clearing the pending selection.
- Batch archive member reads in memory rather than repeatedly decompressing one skin map at a time. Retain byte-backed proof for unchanged input files.

Field definitions were checked against [xEdit's Skyrim definitions](https://github.com/TES5Edit/TES5Edit/blob/dev-4.1.5/Core/wbDefinitionsTES5.pas) and [CommonLibSSE-NG texture slots](https://github.com/CharmedBaryon/CommonLibSSE-NG/blob/main/include/RE/B/BSTextureSet.h). These definitions support record interpretation; they do not certify appearance in game.

## Actual MO2 application

Profile: `ModLab 1170 Test`, Skyrim 1.6.1170. Applied through ModLab's character panel:

- Adrianne: Bijin NPCs face/hair; shared CBBE NeverNude body with existing shared shape setup; independent CBBE 2.0.3 skin.
- Other saved face choices: Hulda (Bijin), Carlotta (The Ordinary Women), Aleuc (Cutting Room Floor), retained unchanged.
- NPC build: `workspace/hub-test-20260906/builds/npc/e867737343db/operation.json`.
- Generated-record check: `workspace/hub-test-20260906/builds/xedit/79db473e55aa/operation.json`, zero record errors.
- 40 installed output files; 12 selected skin maps verified as effective. Pending selection removed only after application.
- 53 non-texture FaceGen blocks unchanged, six texture sets inspected. Adrianne's baked face tint unchanged. The other three selected characters' records and FaceGen files match the previous output.
- The previous output is retained in the build's `previous-output` directory.

Independent installed-file verifier and report are in the task workspace at `outputs/ui-rework/verify_skin_application.py` and `.json`. Native fixture coverage in `scripts/verify_npc_body_native.py` exercises actor-local texture/swap records and a second unaffected actor. Unit coverage includes coherent map discovery, archive-byte/family evidence, slot preservation, missing/ambiguous swap sources, pending/reset recovery and changed effective providers.

459 hub tests and compilation passed. Focused code review found an ambiguous-family acceptance bug: a `CBBE / UNP` option could previously satisfy a CBBE request. The shared verifier and both body/skin cached-proof checks now require one declared family. The regression test rejects the mixed option while accepting an explicit CBBE option inside a multi-family installer. The reviewer rechecked this correction, and the actual Bijin/CBBE build also passes the stricter rule.

## Limits and remaining work

Visual appearance is left for the user's final checklist: inspect Adrianne's face/neck/hands with her chosen skin, compare another Bijin character, and check her clothed body with the selected outfit setup. No gameplay appearance claim is made here.

Skin discovery currently requires complete loose texture sets and author installer evidence for the supported CBBE conversion. Texture-only packages without that metadata, different families, multiple swap variants and other specialized body/skin layouts need additional supported conversion paths. Per-outfit skin assignments are not introduced by this change. Hair stays with its matching face package; independent hair and physics workflows remain unfinished.

The current profile still has unrelated installation-queue and record/cleaning findings. The successful character operation does not mark the whole setup ready.
