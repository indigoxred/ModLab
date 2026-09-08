# Character body selection checkpoint

Character appearances now offers a Body choice separately from Face & hair. A character can retain the appearance package's body or use the prepared shared body while retaining the appearance's private skin references. This is one part of the agreed character workflow; independent skin selection, individual shape assignment, hair and physics controls remain unfinished.

The shared-body path currently verifies prepared CBBE-family output. It requires matching body-part slots and weight interpolation, effective shared mesh hashes, and (when replacing private mesh paths) positive evidence that the installed original body/skin files match an author-declared CBBE FOMOD option. Missing evidence returns an explanation without modifying source assets. This is not a claim that all body/skin combinations are compatible.

The adapted existing NPC helper creates character-local skin armor and armature records, copies the selected shared model references, and retains private skin settings. Export inspection checks torso, hand, foot and first-person model references plus retained texture and weight settings. Deterministic character record IDs are checked for collisions. Source mods remain intact.

Pending face/body choices survive closing the panel. Changed or cancelled pending requests are checked before publication/activation; a completed older request cannot clear newer choices. Automatic preparation does not rebuild the old saved appearance selection while a new pending selection exists.

Validation at this checkpoint:

- 407 hub unit tests pass, including wrong non-torso mesh rejection and changed/cancelled pending request preservation.
- `scripts/verify_npc_body_native.py` passes against the native adapted helper: unrelated NPC fields remain unchanged, a second actor sharing the original body remains unchanged, and an actor's generated body identity survives another actor being added.
- Read-only preparation against the retained real profile verifies Adrianne's Bijin CBBE option and 24 body/skin inputs for the prepared CBBE NeverNude / CBBE Athletic body. This is a loose-file profile check, not a native MO2 publication test.
- Native UI application, full MO2 publication and user gameplay verification remain outstanding for this increment. Do not report the entire character customizer complete based on these checks.

## Subsequent native test

Commit `6298852` was deployed with a plugin backup. The real character panel retained four face choices and offered Adrianne a separate shared-body selection. Apply completed native provider runs, the combined patch, an xEdit check with zero record errors, publication, activation and effective-output checks (`builds/npc/5f89e1fda22c`). Saved choices include Adrianne's body independently of her Bijin face; pending choices cleared after success. Gameplay remains for the user.

The following automatic preparation exposed a dependency cycle: Pandora's input signature included generated FaceGen meshes, while NPC preparation included Pandora's outputs. Replacing FaceGen consequently invalidated behaviors and vice versa. The run was held with the current choices retained as pending. FaceGen paths are now excluded from behavior inputs; an additional regression test checks that FaceGen changes are ignored and actual animation changes remain detected. The suite is now 408 tests. The fix still requires a native recheck after deployment.

Character preparation progress now updates the normal character panel as well as Advanced. Independent skin, shape, hair and physics controls remain unfinished; this checkpoint is not full product completion.
