# Task 7B bootstrap operation classification — controller read-only assessment

Starting source: `80ae17d` (plan-only atop source/evidence `2a4246a`). Line locations
below refer to that source; Task7A may shift them. No move repair is implemented by
this assessment. Follow the Task7B brief after Task7A's independent review.

| Entry point | Actual operation | Required treatment |
| --- | --- | --- |
| `_apply_create_plan` ~2487 | Existing exact empty final target to job prior | Preservation move: retain source/destination parent across existing snapshot validation and shared no-replace rename. |
| `_apply_create_plan` ~2523 | Staged manager tree to final target | Activation move: same primitive and retained identities, preserving strict inventory and journal order. |
| `_restore_create_prior` ~3032 | Preserved prior back to absent final after failed activation | Restoration move: exact source/prior evidence, no overwrite, no string-only swallowing of live owner errors. |
| `_recover_inactive_job` → `_relocate_recovery_tree` ~1124 | Proven stage to job `recovered-stage` | Quarantine/preservation move; never adopt unknown partial stage by pathname. |
| `_restore_planned_prior_for_recovery` → helper ~1220 | Proven prior to final | Recovery restoration, with planned target predicate and process-absence checks intact. |
| `_rollback_unready_recovery_target` → helper ~2069 | Exact activated tree to job `recovered-activated` | Recovery quarantine, preserving snapshots/identity and the existing journal barrier. |
| `_apply_adopt_plan` ~3118 | Extract reference package into owned stage, compare existing app, publish receipt | Existing manager remains in place. No tree move to add; migrate its immutable receipt publication via shared store path. |
| `Mo2BootstrapStore._atomic_create` ~631 | Immutable plan, initial journal, receipt part to absent target | Authority-bearing immutable publication. Windows `_promote_no_replace` ~1155 currently calls pathname `MoveFileExW`; migrate to shared `publish_new_pinned`, including canonical exact bytes/ID, collision/idempotence and effect/owner errors. |
| `Mo2BootstrapStore._atomic_replace` ~672 | CAS-like mutable journal transition under lock and expected-byte checks | Keep legitimate mutable replacement. Do not mechanically make journal updates no-replace or remove validation/locking. |
| `_remove_preserved_empty_prior` and existing owned-stage cleanup | Validated deletion of exact empty prior/stage from prior design | Not a file-move migration. Do not expand this task to arbitrary deletion or rewrite unrelated cleanup. If a concrete load-bearing interaction appears, raise it with evidence. |

## Integration risks that must be tested

1. `_replace_path` currently returns `None`; `prior_moved` and `activated` are set only
   after return. A shared move can occur before post-move verification throws. Callers
   must distinguish actual movement, verified movement and unknown state without
   treating a thrown error as proof nothing moved, or deleting/reconstructing a tree.
   Preserve exact lifecycle observations/effects and the immutable failed attempt.
2. Existing `_restore_create_prior` catches `Exception` and returns a string;
   `_mark_create_recovery` catches broadly too. New live handle ownership cannot be
   flattened through these paths or through apply/adopt/recovery generic refusals.
   Use existing shared ownership union/resolution contracts, preserving every owner.
3. An existing/preserved tree is NOT the deletable CANDIDATE role used by the shared
   immutable-file publisher. Its unresolved pin must be close-only verification
   ownership unless separate exact cleanup authority genuinely exists. Do not call
   generic candidate cleanup on an existing tree.
4. Keep source validation inside its retained ownership scope. Reopening by pathname
   after validation and only then pinning is not a fix. Retain and validate destination
   parent as well, and preserve the current whole-tree content checks around relocation.
5. Shared `publish_new_pinned` deletes only its exact newly created candidate if it
   fails before completed publication (including failure after rename but before
   successful owner closes). This differs from a later outer store reload failure
   after completed publication. Preserve that distinction, including `changed` and
   record identity in `Mo2BootstrapStorePromotionError`; do not claim no effects just
   because an exception occurred. Read task-7-fix1-publication-boundary-clarification.md.
6. Existing tests hook `_replace_path` and `_promote_no_replace`; moving a seam can
   make those tests pass vacuously. Migrate to the real lowest relevant operation and
   assert actual attempted injection plus final filesystem/evidence behavior. Preserve
   mutable-journal race tests on their real replacement seam.
7. Task7A must include both old `label-<32hex>.part` mutable-document paths and shared
   immutable `.<target>.<pid>.<thread>.<16hex>.tmp` paths. New Task7B destinations may not
   exceed or bypass the reviewed operation budget.

## Scope boundaries

- No full-file restructuring of the large bootstrap module, second rename algorithm,
  new environment bypass, copy/delete, overwrite or cross-volume fallback.
- No actual original preparation restart or MO2 launch during implementation.
- No changed old records, main push/merge, receipt/decision/eligibility consumption.
- Native fault injection may model otherwise unavailable cross-volume conditions,
  but report precisely which cases are native objects versus injected API identity
  failures. No hidden mappings/new volumes merely to manufacture a test environment.
- Before live work, combined final native suite and fresh independent code review.
  Actual-MO2/path/bytecode capability remains a separate reviewed source-bound gate.
