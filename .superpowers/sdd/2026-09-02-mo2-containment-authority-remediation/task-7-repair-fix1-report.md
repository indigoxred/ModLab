# Task 7 prerequisite repair — fix round 1

Status: F1/F2 correction implemented and native gates passed; ready for independent
re-review. This report is not acceptance or live-run authorization.

Correction BASE: `8b6ae7fbcb99a7b5fe759c4d84113ac3184741f8`, tree
`470e6f9495a3b4e509ec54a657c9c4798eb3523e`. The root-owned plan amendment and
all original repair/review evidence remain unchanged. Only service, store,
preparation lifecycle models, and their preparation test module changed.

## Findings addressed and the explicit design ruling

F1: source commit/tree, actual working-source digest, new session, exact native
controller PID/creation time and canonical successor intent are acquired before
consuming preparation replacement authority. Failure at this point creates no
fresh run/intent/attempt and does not consume the old marker. This statement is
about successor startup: the enclosing explicit restart may already have recorded
the separately requested old preparation recovery and its real cleanup effects.

Consumption writes strict `PreparationReplacementV2`, schema version 2, containing
the exact complete successor Attempt and canonical successor intent string. The
old recovery, old intent/fingerprint, fresh run, source, session and process are
cross-bound. Historical v1 replacement records remain strict/readable as v1;
their absent metadata is never invented or upgraded in place. Records retain
canonical version/kind/field/type validation and deterministic content IDs.

Root explicitly approved storing new-successor startup failure beside the OLD
reservation when the new namespace/publication is uncertain. The record's old
run/intent/fingerprint and exact replacement ID locate its immutable authority;
its distinct `successor_run_id` identifies the NEW failed identity. This placement
does not mean the old attempt failed again, refund authority, or authorize a retry.
Lineage loading can resolve the reserved Attempt without adopting or rewriting any
possibly absent/ambiguous successor intent/Attempt file.

Root clarified the pre-promotion boundary during final verification: "no fresh
attempt effects before consumption" excludes successor namespace/fixtures/request
or authoritative successor lifecycle records, not the shared publisher's necessary
unpublished candidate under the old run. Atomic marker promotion is consumption
and durable lineage. A surviving `.part` is an actual publication effect, never a
no-write success or adoptable authority. This repair adds no stale-candidate
reconciliation, cleanup, ignore-unknown permission, or invented failure tied to a
nonexistent marker. Existing publisher collision/uncertainty/retained-owner and
unknown-link refusal rules remain binding. Ambiguous promotion is not claimed
confidently unconsumed without the existing exact evidence. The source-binding
no-fresh-effects test precedes entering the publisher; hard-cut tests cover the
post-promotion consumption boundary, not general pre-promotion candidate recovery.

Normal successor intent and Attempt use fresh-only publication, then an immutable
`startup-initialized` barrier is published before projection-receipt-parent or
fixture work. The barrier means startup initialization only, NOT successful
preparation, scenario execution, validation or bridge eligibility. The shared
exact-object no-replace publisher remains the publication primitive; same-byte
preexistence and publisher collisions refuse instead of becoming idempotent
success on this path. Existing default idempotent publication behavior elsewhere
is unchanged. A caught initialization error writes only a `CaughtFailure` with
the actual operation phase/error and observed effects; it claims no controller
absence. It never retries the failed publication, replenishes the marker, or
adopts its uncertain result. If both startup and failure publication retain native
owners, the existing ownership-union helper keeps both owners reachable.

For a v2 marker without a failure or initialization barrier, explicit recovery of
the old run can append `AbandonedStartup` only under the existing command/old-run
locks plus the successor run lock. It requires fresh exact reserved-controller
absence and complete supported-candidate absence using the existing two-snapshot
native proof, and checks startup-only state before and after that proof. Allowed
successor state is absent, or a direct root with exact canonical reserved intent/
Attempt bytes and empty direct `scenarios`/`quarantine` scaffolding. An Attempt
without its intent is contradictory. Any fixtures, scenario child, unknown file,
changed/noncanonical record, redirected entry, or present/uncertain initialization
barrier refuses this abandonment route. No inspected successor object is cleaned,
adopted, rewritten, or assigned creation ownership. The marker is reloaded before
the immutable disposition. Existing v1 records cannot take this route.

The existing process policy remains
`supported-preparation-python-git-tar-mo2-absence-v1`. A disposition attests current
absence under that bounded supported-mechanism policy, not a reconstructed historic
exit code or universally safe legacy recovery. In particular, absent controller
alone never substitutes for absence of an orphaned extractor/candidate process.
The new disposition has no receipt/retry authority: even when the successor died
before its run directory existed, predecessor enumeration retains the reserved
failed identity as unresolved and blocks an implicit ordinary prepare.

F2: the real archive test now snapshots each explicitly known pre-existing
projection receipt, validates its exact ID against the old failure's projection
ID tuple, and compares those bytes after real recovery/restart alongside the
pre-existing run-root records. It does not recursively traverse old fixture
junctions or include later-created recovery records in the before snapshot.

## TDD and native verification chronology

All commands used the bundled native interpreter
`C:/Users/red/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe`
with `-B`, native escalation, and cwd
`C:/Users/red/Desktop/Modlab/.worktrees/mo2-guard-bridge-handshake`.
Logs named below live beside this report and preserve actual terminal outputs.

1. Before source edits, the three new startup tests failed for the intended gaps:
   source-binding failure consumed the old marker; Attempt publication failure
   lacked reserved metadata; interruption immediately after real consumption lost
   reserved metadata. `task-7-fix1-red.log`: 3 tests, 1.098 s, failures=3, exit 1
   (native chunk `441389`).
2. Initial implementation: `task-7-fix1-green-initial.log`: 27 tests, 5.744 s,
   OK/skipped=1, exit 0. Added hard-cut/refusal coverage:
   `task-7-fix1-green-hard-cut.log`: 33 tests, 10.937 s, OK/skipped=1, exit 0.
3. Expanded service/store gate: `task-7-fix1-focused-v1.log`: 195 tests, 23.912 s,
   OK/skipped=2, exit 0. A separate new no-implicit-retry assertion then correctly
   failed after an absent-successor cut: `task-7-fix1-no-implicit-retry-red.log`,
   1 test, 0.879 s, failure=1, exit 1 (chunk `f0b5ee`). After correction,
   `task-7-fix1-green-no-implicit-retry.log`: 37 tests, 14.209 s,
   OK/skipped=1, exit 0.
4. Dual-publication ownership test initially expected the wrong outer exception
   class: `task-7-fix1-owner-union-red.log`, 1 test, 0.413 s, ERROR=1, exit 1.
   This harness error is not counted as the intended RED. Moving the assertion to
   the initializer seam exposed the actual missing owner:
   `task-7-fix1-owner-union-v2-red.log`, 1 test, 0.367 s, failure=1, exit 1
   (chunk `5a1ec2`). Both real test handles were closed in `finally` on every run.
5. Frozen-file focused gate: `-m unittest tests.test_mo2_preparation_recovery
   tests.test_mo2_containment_store tests.test_mo2_containment_service -v`.
   `task-7-fix1-focused-final.log`: 197 tests, 26.647 s, 195 passed, 2 skips,
   no failures/errors, exit 0 (session `71874`, completion `784535`). Skips: the
   all-real test deliberately lacks opt-in environment in this focused command;
   one existing directory symlink test lacks Windows privilege (WinError 1314).

The three abrupt-cut regressions are real native child `os._exit(73)` cuts after
consumption, before Attempt publication and after Attempt publication. They target
the actual initializer with real source/native binding and real publication. They
are NOT a complete unmodified production restart driver: the parent Python remains
alive, so the child initializer seam receives its already-established parent
recovery proof without invoking production restart's inventory. No inventory is
stubbed in production; the later parent `recover_preparation` performs genuine
reserved-child and complete candidate absence checks. The separate all-real
archive regression exercises the actual production recovery/restart pipeline.

Additional tests cover live reserved controller, uncertainty/PID absence policy,
substituted intent and Attempt bytes, unknown scenario children and uncertain
barrier, state change during absence proof, acknowledged/ambiguous publication,
same-byte collision, marker replay, strict v1/v2 decoding, and retained native
ownership. Existing cleanup, legacy, process close, no-refund and scenario tests
remain in the focused/full suites.

## Final full-native command and frozen evidence

The final command sets only these additional process environment names:
`TEMP`, `TMP`, `MODLAB_PREPARATION_ARCHIVE`, `MODLAB_PREPARATION_STEAM`.
TEMP/TMP name the existing worktree-owned `nt` test parent. The archive is read-only
`C:/Users/red/Desktop/Modlab/workspace/inbox/Mod.Organizer-2.5.2.7z`
(149660212 bytes); Steam input is read-only `C:/Users/red/Desktop/Steam`.
Free space was checked before the run: 1463.49 GiB available on C: as displayed by
PowerShell. Each real test exclusively creates its own short `tXX` root and private
`native-temp/Low`, labels only that test-owned Low subtree, and preserves the seven
non-secret environment keys COMSPEC, PATH, SYSTEMROOT, TEMP, TMP, USERNAME, WINDIR
for child and parent restart. No original TEMP or live-runtime changes occur.

```powershell
$env:TEMP='C:/Users/red/Desktop/Modlab/.worktrees/mo2-guard-bridge-handshake/nt'
$env:TMP=$env:TEMP
$env:MODLAB_PREPARATION_ARCHIVE='C:/Users/red/Desktop/Modlab/workspace/inbox/Mod.Organizer-2.5.2.7z'
$env:MODLAB_PREPARATION_STEAM='C:/Users/red/Desktop/Steam'
& 'C:/Users/red/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' -B -m unittest discover -s tests -v 2>&1 |
  Tee-Object -FilePath '.superpowers/sdd/2026-09-02-mo2-containment-authority-remediation/task-7-fix1-full-native-final.log'
exit $LASTEXITCODE
```

Final result: **1097 tests in 384.931 s, 1087 passed, 10 skips, zero failures/errors,
exit 0**. Session `55801`; terminal completion chunk `f0402e`. The all-real
`test_foreground_failed_attempt_recovers_once_into_completely_fresh_real_preparation`
emitted `ok` (chunk `8e8252`; final log line 855). This is the final full gate,
not a claim based only on the focused test or the old repair's earlier run.

The ten declared skips are eight existing symlink-privilege tests (WinError 1314)
and two older separately opted-in integration tests. The eight test names are:
`test_redirected_package_file_refuses_before_job`,
`test_redirected_ini_refuses_without_plan`,
`test_redirected_workspace_refuses_without_plan`,
`test_production_snapshot_records_reparse_without_following_it`,
`test_list_run_ids_rejects_symlink_when_supported`,
`test_rejects_redirected_sources`, `test_rejects_redirected_source_paths`,
`test_rejects_redirected_target_paths`. The two older tests are
`Mo2BootstrapWindowsIntegrationTests.test_real_archive_creates_ready_disposable_instance`
and `Mo2ContainmentWindowsIntegrationTests.test_real_fixture_is_exact_isolated_and_low_integrity`;
their separate switches were not supplied. The new real preparation recovery test
was explicitly enabled and passed, not skipped.

`task-7-fix1-final-files.sha256.json` records the 12 exact files frozen before full
discovery, including the unchanged root plan. All 12 matched after termination
(chunk `4c9f52`). No source/test file changed during either final gate. `git diff
--check` passed. The four-file implementation commit is
`e84ecb74de21ee19fa60ebcf3edecf21c7100a65`, tree
`fd6aa8c7c25aa82e278cd7f72b1a1c67a0ea64b1`. The native gates preceded that commit:
their actual source commit/tree fields therefore identify BASE `8b6ae7f...` /
`470e6f94...`, while the working-source digest binds the repaired source bytes.
They do not falsely claim the uncommitted repair was already present in BASE.
The evidence-only commit follows this source commit; its identity is supplied in
the handoff rather than inserting a self-referential report hash/commit.

SHA-256 evidence (raw logs remain local; report and frozen manifest are committed):

| File | SHA-256 |
| --- | --- |
| task-7-fix1-red.log | `2f3d60f31253a04c7d849376e93c40662f34fb7548a7fe02533ee5c03196472f` |
| task-7-fix1-green-initial.log | `0c6ffa17bc68ad91bdb7575ce720d2f93bbf923bced32966eccbad1d5c3c9cd4` |
| task-7-fix1-green-hard-cut.log | `7156541432516911938d23e42c3fb611568e3f05e7201a348c66b72e74c9e47f` |
| task-7-fix1-focused-v1.log | `422962cca771c05dc9403a956548a094319bb9d4756c5c8d0e66431033d18898` |
| task-7-fix1-no-implicit-retry-red.log | `6a4674912fe54035d9cf3e167d2bd69f8819ed7430b17c14f36ecdc6b5d829ea` |
| task-7-fix1-green-no-implicit-retry.log | `3c29c010e5ba250f2c657ac98c5dc3ddb46034b3f8ba66157415005e167af8e4` |
| task-7-fix1-owner-union-red.log | `5e97f688becfd0fdfab95126be239207043d439fe7844259fdf503806aa2144e` |
| task-7-fix1-owner-union-v2-red.log | `e97b2ab92ebfda3631d2682658ccca008eed703fadb67750dbe28df67e63e562` |
| task-7-fix1-focused-final.log | `71629363cd101d0b12c64c1e855f1d371c57e58ea8fad4dd72aa8865fa6566a0` |
| task-7-fix1-full-native-final.log | `e33cd21133946fee64a676a3aec4b9f7470f258e9850036a302d1e69fecc6251` |
| task-7-fix1-final-files.sha256.json | `18bc3f12927998d5ac047efde007aed85db90125ad5a87eacd9bed9ed0fa2e3b` |

The original report still hashes to
`380294d2673cb24c01547626a0c68e80ffc2ca3f7ff0deffef37f0a75e1bfa12`,
and its original frozen manifest still hashes to
`7c8d5c6f38307e559e6924f92b0cac466097c8cd3aa274b891247971b97f6c17`.
All earlier log filenames were preserved. An attempted hash of the actively written
full log was denied by Windows sharing; no hash was asserted until terminal close.

## Scope and remaining limitations

- No actual failed-run recovery/restart, MO2/game launch, GUI operation, bridge
  consumption, push, merge or independent self-review was performed. Root owns
  the independent re-review gate and all later live operations.
- Original run `containment-run:7b664285ad8c442d97acde65ce22b65a` and every old
  failure byte remain untouched. New-format test success is not a claim that the
  actual retained legacy failure is recovered.
- Original prospective extraction cwd lengths 253/257/259 versus short test
  247/251/253 remain a separate live prerequisite. System tar previously refused
  259. No production archive/path workaround was introduced.
- Inherited bootstrap activation `os.replace` remains a separate prerequisite;
  this repair establishes fresh-only shared no-replace publication for its own
  new evidence, not an end-to-end every-bootstrap-move claim.
- Missing ordinary fixture-failure receipts and interrupted cleanup after an
  exact move remain fail-closed, not resumable general cleanup. A present or
  uncertain startup-initialized barrier does not enter startup abandonment.
- Unknown/unsupported legacy mechanism shapes remain refused. No historical
  PID, source revision, exit status or fixture creation ownership is fabricated.
- Existing watch/scenario errno-vs-winerror false-refusal limitation is unchanged.
  The exact-fs, archive, process and windows-integrity dependencies used by Task 6
  were not edited. No old capability matrix was replayed or rebound.
- The previously disclosed restricted diagnostic directory
  `modlab-owner-union-2l18r1i0` was not inspected or removed. A clean tracked diff
  is not a claim that every historical untracked diagnostic directory is clean.

Receiving-code-review, TDD, systematic debugging and verification-before-completion
guided the reproduced boundaries, scoped correction and evidence-first handoff.
The approved placement/policy tradeoff above remains subject to root's independent
review; this report itself grants no runtime authority.
