# Task 7B implementation report

## Scope and base

- Worktree: `C:\Users\red\Desktop\Modlab\.worktrees\mo2-guard-bridge-handshake`
- Branch: `feature/mo2-guard-bridge-handshake`
- Required and retained HEAD while uncommitted: `f57aff1be3f843fabd03a73a05a9c3d0cab24085`
- Required base tree: `317ee23200818547644339b2bae124ccad0551be`
- Task 7A v2 path-budget/layout interfaces were consumed without modification.
- The deferred Task 7A clarification was preserved as supplied: ReplaceExisting may
  accept an unchanged exact persisted OwnedProjection; an unowned/substituted redirect
  is rejected. The Task 7A table was not edited.

## Pre-implementation move and publication classification

Every bootstrap `_replace_path` caller was classified before editing against
`task-7B-move-classification.md`:

| Operation | Classification | Implemented boundary |
| --- | --- | --- |
| create final empty -> job prior | preservation authority move | retained source + retained job parent -> shared `rename_pinned_no_replace` |
| create stage -> final | activation authority move | retained stage + retained manager parent -> shared `rename_pinned_no_replace` |
| create prior -> final | restoration authority move | retained prior + retained manager parent -> shared `rename_pinned_no_replace` |
| recovery stage -> recovered-stage | recovery quarantine authority move | retained stage + retained job parent -> shared `rename_pinned_no_replace` |
| recovery prior -> final | recovery restoration authority move | retained prior + retained manager parent -> shared `rename_pinned_no_replace` |
| recovery activated -> recovered-activated | recovery quarantine authority move | retained activated root + retained job parent -> shared `rename_pinned_no_replace` |
| adopt manager | in-place observation/adoption | deliberately not relocated |
| plan, initial journal, receipt creation | immutable authority publication | shared `publish_new_pinned` on Windows |
| mutable journal CAS | mutable journal replacement | existing `os.replace` deliberately retained |

## Initial strict TDD chronology

1. Boundary REDs were added before implementation and failed because real bootstrap
   moves/publications did not reach the shared exact-object primitives.
   `task-7B-red-boundary.log`: 2 expected failures.
2. Behavioral REDs exercised real filesystem/evidence outcomes rather than retired
   `_replace_path`/`_promote_no_replace` mocks.
   `task-7B-red-behavior.log`: 7 expected failures.
3. Store ownership/context RED proved a raw `ExactObjectOwnershipError` did not carry
   immutable record kind/id/path/completion context.
   `task-7B-red-store-owner.log`: expected failure retained. The test resolved its
   owners after the assertion was corrected.
4. The first implementation iteration routed the classified moves and publications
   through the shared primitives and reached 106 focused create/recovery/store/adopt
   tests passing with one unavailable-symlink-privilege skip.
   `task-7B-focused-iteration-2.log` retains the complete output.

## Implementation

### Bootstrap tree moves

- Removed the bootstrap pathname `os.replace` move helper.
- Added one `_move_exact_directory` adapter around the existing reviewed
  `modlab.platform.windows_exact_fs` primitives.
- It pins the exact source directory and exact destination parent before caller
  validation, keeps both handles alive through validation, the shared same-volume
  no-replace rename, and post-move validation, and closes both only afterward.
- It delegates the actual rename exclusively to `rename_pinned_no_replace`; there is
  no second Windows rename implementation, pathname fallback, overwrite, or
  copy/delete path.
- `_ExactMoveProgress` records a completed rename even when post-move validation or
  owner cleanup raises, preserving correct rollback/recovery semantics.
- Compound failures union any still-live source verification owner and destination
  parent owner with ownership already returned by the shared primitive.
- Preservation, activation, create restoration, and all classified recovery
  relocations use this adapter. Adoption remains in place.

### Immutable bootstrap publication

- Windows immutable plan, initial-journal, and receipt creation now use the existing
  `publish_new_pinned` primitive.
- POSIX keeps its existing link-based no-replace implementation, renamed explicitly
  `_promote_no_replace_posix`.
- Mutable journal compare-and-swap still calls `os.replace`; it was not routed through
  immutable publication.
- `Mo2BootstrapStoreOwnershipError` preserves live exact-owner union plus record
  kind, record id, destination path, and whether publication completed.
- Incomplete immutable publication remains rollback-capable; completed publication
  followed by reload failure retains the immutable document and reports `changed=True`.

## Native diagnostic and regression chronology

An adjacent 75-test run initially exposed intermittent CLI failures. No production or
test edit was made until the root cause was captured.

- `task-7B-cli-stage-identity-diagnostic-01.log`: one formerly failing rendering
  fixture passed in isolation under full identity/pin/rename tracing.
- `task-7B-cli-stage-identity-diagnostic-02.log`: exact 75-test order passed under the
  same full tracing (75 run, 2 declared skips).
- `task-7B-cli-stage-identity-diagnostic-03.log`: shortest ordered 57-test prefix
  through the earliest symptom passed using only returned-metadata tracing and the
  original worktree `nt` TEMP/TMP.
- `task-7B-cli-stage-identity-diagnostic-04.log`: timing-neutral in-memory trace of the
  exact original 75-test order reproduced the original 2 failures + 2 errors. The
  first causal stage observation retained identical device, inode/file ID, and mode
  type but changed `st_file_attributes` from `0x10000010` at initial creation to
  `0x10` at pre-activation validation. All newly created empty stage directories had
  the same transient high bit. Directory size/time changes were not involved because
  `_EntryIdentity` already excludes them.

Accepted root cause: allowed direct directories were treating a transient,
non-identity Windows attribute bit as object identity.

Strict regression sequence:

1. Added
   `test_transient_non_identity_directory_attribute_does_not_block_exact_activation`.
   It changes only bit `0x10000000` in the initial metadata returned for the real
   staging-root observation and leaves extraction, retained pins, shared rename,
   receipt publication, and filesystem effects real.
2. RED: `task-7B-red-transient-directory-attribute.log`, 1 test, expected error
   `staged instance root identity changed`.
3. Minimal production correction: directory identities retain device, inode/file ID,
   mode type, and only directory/reparse safety/type attribute bits. File identity is
   unchanged. Redirect/reparse rejection remains at every production observation.
4. An intermediate GREEN attempt reached the activated outcome but the new test
   incorrectly asserted that the app directory itself was a file. The immutable
   `task-7B-green-transient-directory-attribute.log` records that test-only assertion
   failure. The assertion was corrected to the real `ModOrganizer.exe` path.
5. GREEN: `task-7B-green-transient-directory-attribute-2.log`, 1 test passed.

## Current focused verification

- Exact former failing order:
  `python.exe -B -m unittest tests.test_mo2_bootstrap_planning tests.test_mo2_bootstrap_inventory tests.test_mo2_bootstrap_serialization tests.test_mo2_bootstrap_cli -v`
  -> 75 tests passed, 2 declared symlink-privilege skips, zero failures/errors,
  25.837s. Complete log: `task-7B-green-prior-75.log`.
- Affected implementation set:
  `python.exe -B -m unittest tests.test_mo2_bootstrap_create tests.test_mo2_bootstrap_recovery tests.test_mo2_bootstrap_store tests.test_mo2_bootstrap_adopt -v`
  -> 107 tests passed, 1 declared symlink-privilege skip, zero failures/errors,
  80.803s. Complete log: `task-7B-green-focused-final.log`.
- All commands used bundled Python with `-B` and worktree-owned short TEMP/TMP.
- The complete native suite and all-real/absence-sensitive tests have not been run;
  they require the controller rendezvous.

## Self-review

- `git diff --check` is clean (only Git line-ending warnings were emitted).
- Searches show no `_replace_path` definition/caller remains.
- The only bootstrap Windows tree rename is the call to shared
  `windows_exact_fs.rename_pinned_no_replace` inside `_move_exact_directory`.
- The only Windows immutable publication call is shared
  `windows_exact_fs.publish_new_pinned` inside `_atomic_create`.
- The sole `os.replace` remaining in the bootstrap store is the deliberate mutable
  journal CAS in `_atomic_replace`.
- No `shutil.copy`, `copytree`, `move`, alternate Windows rename, pathname fallback,
  or overwrite implementation was introduced.
- `_apply_adopt_plan` contains no `_move_exact_directory` call; manager adoption is
  still in place.
- Source and destination-parent owners span validation, shared rename, and post-move
  validation. Owner union, completed-move tracking, immutable publication completion,
  and reload-failure tests pass in the focused set.
- Current tracked scope is exactly the two bootstrap source files and three focused
  test files required by Task 7B. Ignored diagnostic logs and existing test-owned
  scratch were retained and not cleaned.

## Pending gate

Source is frozen pending the required controller rendezvous. No commit, complete
native suite, all-real/archive/Steam opt-in, review, push, merge, UI, MO2/game launch,
or authority consumption has occurred.

## Post-gate diagnosis and shared normalization correction

### Failed gate execution-mode finding

- The first complete discovery was mistakenly launched with the default sandbox;
  the earlier permission grant covered evidence/worktree writes but the discovery
  `exec_command` omitted `sandbox_permissions=require_escalated`.
- `task-7B-final-native.log` preserves that single run: 1,168 tests in 182.859s,
  36 failures, 65 errors, 10 skips, exit 1, with all five then-frozen Task 7B hashes
  unchanged. Log SHA-256:
  `984f785ee7609250d7a86cb385410e302795c4a411557a215d25ddb3faebde3b`.
- The two real integration skips were expected for that exact command: those tests
  opt in with `MODLAB_MO2_ARCHIVE` and `MODLAB_STEAM_ROOT`, whereas the authorized
  gate supplied only `MODLAB_PREPARATION_ARCHIVE` and
  `MODLAB_PREPARATION_STEAM`.
- The representative owner-union test hung in the default sandbox. Controller-
  authorized termination stopped only PID 20484 after 337.825s (333.578 CPU s),
  with no descendants before or after. The partial log exited `-1` at 337.875s.
  `task-7B-diagnostic-05-sandbox.log` SHA-256:
  `3cd3e8015f340bbf017cb3ec3b50957b79f33d3108d04b1297d503b312c073a7`;
  termination evidence SHA-256:
  `03ecb95d6a3b000ad64cc4099604604676cfb2d5b75c172546ee9ad7429cf634`.
- The same representative test outside the sandbox returned immediately but still
  failed before its delegated operation: `_mutation_root_observation` was
  unavailable. `task-7B-diagnostic-06-unsandboxed.log` SHA-256:
  `4139912e58cb3ae9220007287e64bb885fed7a82dea71babb639577dd9a9d502`.

### Timing-neutral returned-metadata diagnosis

- A single native run wrapped the production mutation-observation boundary and
  buffered only metadata/identity values already returned by production. It made no
  additional stat, lstat, open, or filesystem observation.
- The sole mismatch was the direct observed directory
  `observed/source/Protected`: its initial lstat returned attributes `0x10000010`;
  the retained native pin and the next production lstat returned `0x10`.
- Device, inode/file ID, mode and type, size, atime, mtime, ctime, directory state,
  and symlink/reparse state were identical. The transient `0x10000000` bit alone
  made the full raw-attribute tuple differ and caused the observation to return
  `None`.
- `task-7B-diagnostic-07-effect-observation.log` preserves the complete returned
  values and unchanged source hashes. Log SHA-256:
  `c3e28e592c32a60d71f66b961fd63cf458be0c29809571589ff1a75e2485248f`.
- Read-only mapping confirmed watch root identity already compares volume serial,
  file ID, directory type, and reparse state only. It does not compare directory
  size/times or unrelated attribute bits and required no source change.

### Strict RED to GREEN chronology

1. Added the exact-FS contract test
   `test_directory_identity_attributes_ignore_transient_bits_while_files_remain_raw`.
   It requires directory attributes differing only by `0x10000000` to normalize
   identically while preserving raw file attributes.
2. Added the real service regression
   `test_transient_non_identity_directory_attribute_does_not_block_delegated_mutation_observation`.
   It changes only the transient bit at the existing returned-lstat seam, requires
   the real delegated operation to run, and proves the five-owner union outcome.
3. RED: `task-7B-red-shared-normalization.log` ran both tests. The helper test errored
   because the helper did not exist; the service test errored because observation
   was unavailable before the operation. Two tests, two expected errors, exit 1,
   0.473s. Log SHA-256:
   `a67fb2e0c1fb00db5c347e095f3922f04b777ea976779ef465b8707971e0e6b1`.
4. Added pure `normalize_identity_attributes` to
   `modlab/platform/windows_exact_fs.py`. Directories retain only DIRECTORY and
   REPARSE safety/type bits; files retain their complete raw attributes.
5. Reused that helper in bootstrap `_entry_identity` and in the service's two raw
   directory-attribute comparisons (`identity` and `metadata_row`). No other
   identity field or consumer changed, and watch code was untouched.
6. GREEN after final formatting: the helper contract, real service regression, and
   existing bootstrap activation regression all passed, 3 tests in 0.979s, exit 0.
   `task-7B-green-shared-normalization-final.log` SHA-256:
   `6fe1478b61b311af42f44663308fb6106c7076df244c8643e91a1b3c91e0b078`.
7. The previously failing native owner-union test passed, 1 test in 0.023s, exit 0.
   `task-7B-green-owner-union-native.log` SHA-256:
   `fe1e033e26c7ea1b4006988ca1b4bbbb983b59f95825973ba496cae07e4c141b`.
8. The established planning/inventory/serialization/CLI set passed: 75 tests,
   2 declared symlink-privilege skips, 22.572s, exit 0.
   `task-7B-green-shared-prior-75.log` SHA-256:
   `5f92c5c73b440493b5053784be78432caad3d26336514c621ca7ab82a509bd5b`.
9. The established create/recovery/store/adopt set passed: 107 tests, 1 declared
   symlink-privilege skip, 73.841s, exit 0.
   `task-7B-green-shared-bootstrap-focused.log` SHA-256:
   `7bab543ca5ea913836c3fed42b7e4f31a5d33224a48701d20a4960ec20a2526b`.
10. Four existing native safety tests passed: same-file-ID/foreign-volume rejection,
    delegated reparse creation rejection, watch pre-transfer identity/type/reparse
    failures, and watched-root replacement detection. Four tests in 0.331s, exit 0.
    `task-7B-green-shared-safety-focused.log` SHA-256:
    `c50eb9a90955b207f3e868d318a92d5fe36817c955aff9a1553bd56c7043124d`.

### Post-correction self-review and frozen scope

- `git diff --check` remains clean apart from Git's line-ending warnings.
- `modlab/validation/windows_watch.py` has no diff.
- Directory normalization changes only the Windows attribute field; device,
  inode/file ID, mode/type, size, times, digests, retained pins, redirect/reparse
  refusal, and file raw-attribute identity remain unchanged.
- The earlier Task 7B move/publication invariants still hold: one shared
  `rename_pinned_no_replace` call, one shared `publish_new_pinned` call, and the sole
  `os.replace` is the intentional mutable-journal CAS. There is no `_replace_path`,
  copy/delete fallback, alternate rename, or adoption relocation.
- Current tracked scope is the original five Task 7B files plus the shared exact-FS
  helper, containment service consumer, and their two test files (nine files total).
- Current SHA-256 values are:
  - `cec0fa0bb0672441fef03613c61b103702ac8f4bd8e61de298c046580aaf0feb`
    `modlab/platform/windows_exact_fs.py`
  - `935954bbc8c541cdfe1b383f8831ab243a742fc834943e488b88d5d1e8081577`
    `modlab/validation/mo2_containment_service.py`
  - `9b04ae4883ee3455d6eaae1d43b76cc6c395ffdc09255d1bf75a1e61b57fd89f`
    `modlab/workflows/skyrim/mo2_bootstrap.py`
  - `ebb888bc4ae08680230dbb3ef0769c64bd00ab020338d202ce61266d51beb627`
    `modlab/workflows/skyrim/mo2_bootstrap_store.py`
  - `97bb933703edada3c08f2e3deee233302f26ac611b89dd3c3ed5f0517e4a4005`
    `tests/test_mo2_bootstrap_create.py`
  - `3c838e7d0691ce64f3149e23de6e14ebd7bccf4df25a9893acd62112a4f43012`
    `tests/test_mo2_bootstrap_recovery.py`
  - `4f75be7debea6e2cd58d22e5cdf1df584b07f7852b67d6ae186df59b07d4cb62`
    `tests/test_mo2_bootstrap_store.py`
  - `fce0928d1ca0b4c64f3c60919c95624ef9bcc69571da715bcf62755fab56c979`
    `tests/test_mo2_containment_service.py`
  - `b8d40a75973f05b2af0695e337ce0fb05e78d7b5690211094be50edf8cd0b8a8`
    `tests/test_windows_exact_fs.py`

The corrected source is frozen for controller rendezvous. The complete native suite
has not been rerun after this correction. No all-real/absence-sensitive run, scratch
cleanup, commit, UI/MO2/game launch, live/original action, push, or merge occurred.

### Final native Windows gate v2

- Controller authorized one complete discovery from the frozen nine-file source,
  executed with `sandbox_permissions=require_escalated` and an 1,800-second outer
  ceiling. The exact command was
  `C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -B -m unittest discover -s tests -v`.
- `TEMP` and `TMP` were the worktree-owned `nt` directory. The only `MODLAB_*`
  variables were `MODLAB_PREPARATION_ARCHIVE=C:\Users\red\Desktop\Modlab\workspace\inbox\Mod.Organizer-2.5.2.7z`
  and `MODLAB_PREPARATION_STEAM=C:\Users\red\Desktop\Steam`.
- Preflight proved exact HEAD
  `f57aff1be3f843fabd03a73a05a9c3d0cab24085`, exactly the nine tracked modified
  paths listed above, their exact frozen hashes, and zero relevant processes.
- The gate passed with exit 0: 1,170 tests in 1,075.531 seconds; 1,160 passed,
  0 failures, 0 errors, and 10 skips. Outer evidence duration was 1,077.540
  seconds.
- All skip lines are preserved verbatim in the log. Eight were Windows symlink or
  directory-symlink privilege skips (`WinError 1314`). Two real integration tests
  skipped because they require the separate `MODLAB_MO2_ARCHIVE` and
  `MODLAB_STEAM_ROOT` opt-ins, which this exact gate intentionally did not set.
- Periodic process evidence recorded suite PID 24368, the expected watch workers,
  the all-real failure child PID 2904, and its later `tar.exe`/`icacls.exe`
  extraction and integrity-label phases. The known-process remainder was empty and
  the final relevant-process count was zero.
- Postflight HEAD and all nine source/test hashes exactly matched preflight. No
  timeout or process termination occurred.
- Complete evidence is `task-7B-final-native-v2.log`, SHA-256
  `f5fb9e66eec5a94bbc2ae3907be79a3a1363daa56a164593754613f4133aaff1`.
  Raw stderr SHA-256 is
  `7e05808da4d54300b0c3b1988f587ecb58a17930a64a6133b232574d861887ee`;
  raw stdout was empty with SHA-256
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
  The checksum manifest SHA-256 is
  `a97320da9632e9cb1f08da3a2bc8d2bae0dbcfc005eca9c3f02fa3b4695d6ef4`.
- No scratch/workspace cleanup, commit, UI/MO2/game launch, live/original action,
  push, or merge occurred. The corrected source remains frozen for controller
  verification.

### Local source/test commit

- The independently verified nine-file source/test set was committed locally as
  `f234376d37d44582b6cab162a1e6eadc4a97d10c` with message
  `fix: harden bootstrap exact-object moves` and tree
  `7cdc88a0890837574800d1351109008ded2d2e96`.
- The staged name list contained exactly the nine production/test paths recorded
  above, and `git diff --cached --check` exited 0 before the commit.
- This commit used the passing final-native-v2 evidence above; no test was rerun,
  no scratch was cleaned, and no push or merge occurred.

## Task 7B review fix round 1/5

### Accepted findings and correction

- Verified the review's critical finding: all three immutable-store ownership
  wrappers derived `changed` from `bool(error.candidates)`, although candidate
  ownership says nothing about whether the no-replace rename became visible.
- Added immutable internal `ExactPublicationFailure` evidence with separately
  typed phase (`private-candidate` or `rename-visible`) and residual effect
  (`no-destination-change`, `rolled-back`, or `rollback-incomplete`). A
  rename-visible failure remains incomplete even after exact rollback, and
  rollback never upgrades it to completed publication.
- `publish_new_pinned` is still the sole immutable publisher. It derives phase
  from its own rename result / exact retained object's destination update and
  effect from exact candidate cleanup, and `union_retained_ownership` preserves
  that evidence across owner additions and partial cleanup retries. Store
  wrappers now consume this evidence; they never infer phase from owner roles.
- The existing shared publisher can optionally borrow an already-retained exact
  destination-parent owner. This is used only for a newly rooted-created job
  directory, because that creation handle intentionally prevents a pathname
  reopen with delete access. Borrowed authority is never closed by the publisher.
- Initial job creation on Windows now uses `create_pinned_directory_child` under
  a retained jobs-root parent. The exact new empty job directory stays live
  through initial journal publication. An incomplete journal publication unions
  the exact journal owners with that exact job-directory candidate; resolution
  deletes only those exact objects in dependency order and supports partial
  cleanup retry. No pathname cleanup fallback is used on this Windows path.
- Non-publication ownership remains backward-compatible: it carries no
  publication evidence and callers must supply their operation-known `changed`
  state explicitly.

### TDD chronology and focused evidence

1. Baseline production was restored exactly to committed HEAD `2f5c79d` before
   the combined behavioral RED. The earlier four-test RED is retained as
   `task-7B-fix-round-1-red.log`, SHA-256
   `301cce1b22d739614604a3cee02a97a41456af608e15755778fb881201ab8b15`:
   four intended assertion failures plus one downstream temporary-directory
   cleanup error caused by the deliberately retained handle after an early
   assertion.
2. The first combined attempt found only a missing test-file `os` import before
   discovery and is preserved as `task-7B-fix-round-1-combined-red.log`, SHA-256
   `167923c56ccd8a65085f15c01c17b1d3f0d7fde31d12b95e3c4b1b84491806ff`.
3. The corrected baseline combined RED ran all ten new cases. The four
   publication/job-owner tests failed for the intended false/missing authority
   evidence (plus the same downstream retained-handle teardown error); all six
   real recovery-relocation safety cases passed on unchanged production. Ten
   tests in 7.165s, exit 1. Evidence:
   `task-7B-fix-round-1-combined-red-v2.log`, SHA-256
   `00b8fd6b4388293703dfd064b144957f859a67c6f1d5b351bf72a689026ced78`.
4. The first implementation run proved that the rooted-created directory owner
   cannot be reopened as a publisher parent because its authority handle denies
   delete sharing. Three publication tests passed and seven job-dependent tests
   errored at that exact reopen; preserved as
   `task-7B-fix-round-1-focused-green-01.log`, SHA-256
   `29baf0e5340a2fd09ed1daf3a7366bbe7e01e90071d62e765d30d0246aebbeb8`.
   The minimal correction was the shared publisher's borrowed-parent interface.
5. The same ten cases then passed: 10/10 in 7.157s, exit 0.
   `task-7B-fix-round-1-focused-green-02.log`, SHA-256
   `f16b80e9c03ca8ec0027969841a7a83091f6ef0ac66a93f00e18763df76be392`.
6. The complete focused bootstrap store and recovery modules passed: 68/68 in
   46.631s, exit 0. `task-7B-fix-round-1-bootstrap-focused-green.log`, SHA-256
   `78a0327b19184203f1836ddacc01911bb5c3e3caaeb2876dbbbb9a4eb1d1a0ac`.
7. Eleven selected shared exact-FS publisher, owner-union, same-volume,
   no-pathname-fallback, rollback, and retained-close tests passed in 0.127s,
   exit 0. `task-7B-fix-round-1-exact-fs-focused-green.log`, SHA-256
   `074f7ad2f09023cecf5c5ea4cf54f8ee59f5f83da2f43cee60b91a316f2803cf`.
8. The strengthened initial-journal test separately proved publication evidence
   survives a failed first cleanup and the store wrapper's narrowed ownership:
   1/1 in 0.081s, exit 0. `task-7B-fix-round-1-union-green.log`, SHA-256
   `039c2d352a4b605025af8b58d440db76fc43a06f341082253627f20bd0f0122d`.

### Recovery relocation boundary coverage

- Six new native tests call the real shared relocation primitive through the
  recovery caller and cover source substitution, destination-parent
  substitution, wrong source type, unknown reparse identity, cross-volume
  refusal, and retained source/parent close failure. They prove journal state and
  recovery evidence remain retryable or `RecoveryRequired` as appropriate while
  unknown/prior bytes remain unchanged.
- Existing unchanged tests mapped alongside them are
  `test_restore_rename_failure_is_retryable_from_filesystem_evidence`,
  `test_stage_quarantine_survives_journal_failure_and_retries`,
  `test_staged_job_quarantines_only_the_exact_recorded_stage`, and
  `test_adopt_recovery_quarantines_stage_without_changing_manager`. These cover
  caller retry/state semantics but do not replace the six new tests at the real
  shared recovery-move injection boundary.

### Self-review and rendezvous state

- Tracked changes are exactly four files:
  `modlab/platform/windows_exact_fs.py`,
  `modlab/workflows/skyrim/mo2_bootstrap_store.py`,
  `tests/test_mo2_bootstrap_store.py`, and
  `tests/test_mo2_bootstrap_recovery.py`. Current SHA-256 values respectively are
  `81884f4231d9f11dd7e1ae55d5510e40f0db9582f22cfa4201ea8242a7d88d70`,
  `3da0d59ab1770ddeccc36dc26661c1dadfda3bf2c5126ab9e423896ca84aa257`,
  `0c4ad1985a8d7f30920c08030ad5b35984bcba2e834b0b0f1b7dcee9b0c24233`,
  and `eb2ce17d83ca4456c6dccb4fbec14edf4b3af6b84b7cf41a036f0495e7113a3c`.
- `git diff --check` is clean apart from line-ending warnings. Review found no
  second rename/publisher, pathname mutation fallback on the Windows job cleanup
  path, copy/delete or overwrite publication, mutable-journal `os.replace`
  rewrite, adoption relocation, watch change, or live-owner loss.
- HEAD remains `2f5c79de1e41febe3ad535af14a6d7fddb65ac1d`. No complete
  native/all-real/absence-sensitive suite, scratch cleanup, commit, UI/MO2/game
  action, live/original operation, push, or merge occurred in this fix round.
  Focused source is ready for controller rendezvous.

### Fix round 1 complete native gate and source commit

- After controller acceptance and independent focused verification, the exact
  four source/test hashes above were frozen and the single complete native
  Windows discovery ran with `sandbox_permissions=require_escalated`, a
  1,800-second ceiling, worktree-owned `TEMP`/`TMP`, and only
  `MODLAB_PREPARATION_ARCHIVE=C:\Users\red\Desktop\Modlab\workspace\inbox\Mod.Organizer-2.5.2.7z`
  plus `MODLAB_PREPARATION_STEAM=C:\Users\red\Desktop\Steam`. The separate
  `MODLAB_MO2_ARCHIVE`, `MODLAB_STEAM_ROOT`, and runtime/live variables were
  unset.
- Exact command:
  `C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -B -m unittest discover -s tests -v`.
- Result: exit 0, 1,179 tests in 1,140.282s; 1,169 passed, 0 failures,
  0 errors, and 10 skips. Outer evidence duration was 1,141.731s. All ten
  skip lines are retained verbatim: eight unavailable symlink-privilege cases
  and the two expected real-integration tests requiring the deliberately unset
  `MODLAB_MO2_ARCHIVE` / `MODLAB_STEAM_ROOT` opt-ins.
- Preflight HEAD was `2f5c79de1e41febe3ad535af14a6d7fddb65ac1d` with zero
  relevant processes. Periodic evidence captured the suite, expected watch and
  all-real child, and `tar.exe` extraction. Postflight recorded zero relevant
  processes, unchanged HEAD, and exact pre/post equality for all four frozen
  source/test hashes.
- Complete combined evidence is `task-7B-fix-round-1-final-native.log`, SHA-256
  `56e7f1945590410b6e179f3f414b49c073d9ff571e51a28cd69462d9485200a9`.
  Raw stderr SHA-256 is
  `d146b6c4bbe81f47b0baefcec9c152ff1072e138a12ea3b223b4d6e36ef2f909`;
  raw stdout is empty with SHA-256
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
- The original manifest is preserved with SHA-256
  `a9725caae9995709adfb98b320970778f1cd091a8fd03919635192ba54b5618f`.
  Its post-suite artifact-list expression emitted a non-terminating PowerShell
  syntax error only after the pass, unchanged hashes, and zero-process
  postflight had been recorded. No test was rerun; the separately retained
  `task-7B-fix-round-1-final-native.checksums-v2.txt` records the independently
  computed raw/combined hashes and the complete result; its SHA-256 is
  `86b6dd4a139686e3aed26cc9a9729e32c0ab40e88ef7c196f8b94d1155c9aaf5`.
- The staged source name list contained exactly
  `modlab/platform/windows_exact_fs.py`,
  `modlab/workflows/skyrim/mo2_bootstrap_store.py`,
  `tests/test_mo2_bootstrap_recovery.py`, and
  `tests/test_mo2_bootstrap_store.py`; `git diff --cached --check` exited 0.
  Source/test commit: `f2be28360472c358146eb617971c15e71f2782d5`, tree
  `a7a9469c25cf5f8ca70b72f8ad0594430bb0e36c`, message
  `fix: retain truthful bootstrap publication authority`.
- No scratch cleanup, UI/MO2/game action, live/original operation, push, or merge
  occurred. The source/test bytes were not edited after the native gate.
