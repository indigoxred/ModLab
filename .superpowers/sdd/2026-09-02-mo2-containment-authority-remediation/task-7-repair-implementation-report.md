# Task 7 preparation repairs — implementation and native evidence

## Status and scope

The two scoped preparation code repairs are implemented and the final complete native suite is green: **1082 tests in 389.721s, OK (10 declared skips), exit 0**. Independent review is still required before consumption. No live preparation recovery/restart, MO2/game launch, four-scenario validation, eligibility, bridge receipt, merge or push was performed. The original failed run was not modified. Root owns all later runtime operations and review dispatch. A separate predicted original-workspace tar path-length limitation remains explicitly unresolved below.

Requirements: `task-7-repair-implementation-brief.md`, the original `task-7-prepare-refusal-report.md` and `task-7-controller-attempt-1.md`, and root's bounded clarification in `task-7-legacy-process-assessment.md`. Pre-repair BASE is `28554debfefe75348b7c8c712eaf976c72b4e5fc`, tree `4c6fac6e520ba35bf5c983bf9359f98b82a84d3c`. Root's existing approved plan amendment remains intact and is included in the repair.

Worktree: `C:/Users/red/Desktop/Modlab/.worktrees/mo2-guard-bridge-handshake`.

Read-only recheck of the actual old intent (`b0e3f9`) still gives SHA-256 `12a775a3bd559d6a3a3306c4fc1775c894dac3cf439b5694d655f983dfca4937`. It was read for binding/path-budget evidence only; no operation was invoked against that run.

## Design and exact safety boundaries

### Creation-bound protective projection

`windows_junction.create_owned_projection` creates the link through the shared exact-filesystem child creator, transfers its first native handle rather than reopening a path, sets/reads the canonical junction payload, and retains four DELETE-denying native pins: link, target, target parent, link parent. `OwnedProjection.verify()` checks the retained handles, exact current path identities, directory/reparse types, one volume, exact target and canonical reparse bytes. `close()` retains unresolved pins in `JunctionOwnershipError`; callers can deterministically resolve them.

The fixture carries this owner through its real delegated post-observation. `_mutation_root_observation(root, expected_projections=())` remains default-deny for every generic reparse, accepts only exact supplied owners, represents the link as an opaque identity/payload-hash row, and never traverses it. It reuses retained target/parent pins and rechecks identities and directory membership. Failed delegates carry their actual created owners and actual partial effects through the same observer. Projection finalizers preserve the union of prior operation/observer ownership and newly failed closes; generic no-projection Task 4 error behavior remains unchanged.

The real native test showed why READ_ATTRIBUTES alone is insufficient: the target could still be renamed. Final pins use DELETE access with sharing that denies deletion/rename. Tests attempt native rename of the exact link, target and both parents while retained, and separately inject native identity/volume/payload mismatches.

### Separate immutable preparation lifecycle

New `mo2_preparation_recovery.py` uses its own explicit `preparationSchemaVersion: 1`, strict dataclass field/type checks, canonical JSON bytes, unique JSON keys and content IDs. It does not reinterpret a ScenarioResult, watch result or authority record. Records are:

- Attempt: exact intent content ID/fingerprint, Git commit/tree, digest of actual `modlab/**/*.py` working bytes, fresh preparation session, native preparing-controller PID/creation time, and optional exact replacement lineage.
- Projection: durable creation-time link/target/both-parent native identities and canonical reparse bytes, bound to attempt and scenario.
- Failure: exact attempt/intent, observed written paths, exact projection receipt IDs and actual failure. Legacy disposition explicitly has no invented attempt or projection ownership.
- Cleanup: exact original projection ID, exact-created quarantine parent identity, and destination; this is published before moving the link.
- Recovery: failure ID, native process proof and versioned policy, cleaned/preserved paths, exact cleanup IDs, blockers and whether one fresh attempt is permitted.
- Replacement: exact recovery ID, consumed-by fresh run and newly obtained process proof; immutable no-replace publication allows only one consumption.

The store validates cross-references before publication, and again on canonical read. Reads use `open_readonly` and do not initialize absent storage. Receipts live in the run root and a separately exact-created `preparation-projections` directory. That separate publication parent is necessary: the live fixture operation holds DELETE-denying ancestor guards, so publishing directly through the same pinned run parent caused a real native sharing conflict during development. No shared rename/publication implementation was changed.

### Cleanup and replacement

Recovery holds command and old-run locks. Request/decision presence refuses. An empty direct `scenarios` directory is only store-created scaffolding; any child, including an otherwise empty scenario directory, is started/unknown evidence and refuses. New-format failure/projection receipt sets must match exactly. Every projection is pinned by creation-time identity, and the entire failed fixture is observed with only those exact links allowed before any cleanup begins. Unknown/substituted reparses are left untouched and block recovery.

Only the exact original protective junction is moved, using the existing shared handle-pinned, same-volume, no-replace primitive. No source payload, game, Play profile or unrelated object is deleted/adopted. Its quarantine parent is created and receipted by native identity. All old source and other fixture state remains retained.

Cached recovery is not trusted as a fresh filesystem/process proof. Both recovery replay and actual consumption reload exact receipts, hold the moved link, original target, original parents and quarantine parent by identity, verify payload/type/path absence, and reprove native process absence. Actual consumption is inside the old-run lock as well as command exclusion. Substituting a same-target link in quarantine cannot consume the marker.

Restart creates a new run, native-bound attempt/session, independent fixture roots and fresh request. Old intent/failure/projection evidence is immutable, no old request/result/decision is manufactured, and repeated/concurrent consumption cannot create another attempt. Failure in the fresh attempt records that new failure/lineage without replenishing the old marker. Nested recovery/prepare effect receipts retain their actual written paths and child-mutation roots on both success and failure.

Preparation predecessor handling also exact-reloads the consumed marker, successor attempt/intent, fingerprint, predecessor cohort and recovery lineage. Adjudication can recognize this abandoned preparation without demanding fictitious scenario history; it gains no successful scenario evidence and cannot bypass real failed/incomplete scenario predecessors.

### Native absence proof and its supported-policy assumption

The explicit policy is `supported-preparation-python-git-tar-mo2-absence-v1`. Two complete native Toolhelp process inventories must finish with `ERROR_NO_MORE_FILES`, contain no duplicate PIDs, and identify exactly the current verifier by its real PID plus creation time. Candidate images are Python/Pythonw, current interpreter basename, git, tar, ModOrganizer and nxmhandler. Every other matching live/uncertain candidate refuses irrespective of its path; arbitrary ancestors are not excluded. `git.exe` is included because new preparation source binding runs synchronous `git rev-parse`; pre-repair archive calls were synchronous system `tar.exe` and did not launch MO2/watch.

New-format recovery additionally proves the recorded exact controller absent. Raw native `OpenProcess` failure is absence only for immediate GetLastError 87; otherwise the exact creation identity must match and its handle must be signaled. PID reuse, timeout/wait failure, access denied, incomplete/empty enumeration and handle-close uncertainty refuse. Snapshot and process close failures retain both owners, rather than dropping one during unwinding.

This is a deliberately conservative supported-mechanism policy, not a universal process-history reconstruction. The legacy intent cannot bind its historic Git revision or historic exit code. Root explicitly approved proving **current abandonment** under this versioned mechanism/candidate policy, command/run exclusion and preservation of all unproved legacy fixtures. The report and legacy failure record do not attest source or exit history. Unsupported mechanism, lineage or run shape refuses; pathname/payload resemblance never grants legacy cleanup ownership. Independent review must judge completeness of this supported candidate policy. Unrelated matching Python/git/tar processes can deliberately cause false refusal.

The admitted legacy shape is the strict existing schema-1 intent, no retry/predecessors, no request/decision/scenario children, only allowed root record/scaffolding names, direct fixtures containing exactly `NewFolder`, and empty direct scenarios/quarantine scaffolding where present. Legacy cleanup is empty, all old fixture objects remain untouched, and only the replacement receives new source/session identities. Tests cover this exact shape, native uncertainty refusal before writing abandonment, unknown shape refusal, and actual legacy replacement consumption into a deliberately failed fresh attempt.

## TDD and native test chronology

Every Python invocation used `C:/Users/red/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe -B`, run natively with `require_escalated`, from the worktree. No unchanged full-suite baseline or old capability matrix was rerun. Focused test commands use `-m unittest <fully qualified test names/modules> -v`. The first RED tool outputs and the later focused transcript are retained in this task's tool evidence; those early outputs were not falsely reconstructed as raw log files.

Important actual RED/GREEN checkpoints (tool chunk IDs identify the retained execution outputs):

| Evidence | Actual result |
| --- | --- |
| `5950a2` initial real projection/absent-storage contracts | 3 intended assertion failures, 0.341s: missing creation API, genuine guarded fixture rejected its own junction, missing recovery API. |
| `03275f` owner contract RED | 3 failures, 0.003s, missing API. |
| `aba735` first native pin experiment | 3 tests, 1 failure/1 error, 0.030s: target rename succeeded with READ_ATTRIBUTES-only retention. |
| `c92749`, `8cc381` | Owner tests 3 OK/0.020s; genuine fixture and opaque observation 2 OK/0.588s. |
| `6f7fe4`, `e2a2eb` | Separate lifecycle API/schema missing (3 intended failures); CLI commands returned parser exit 2 instead of live absent-root refusal 3 (two failing subcases). |
| `c2d475` then `497371` | Unknown reparse and unsupported legacy shape were accepted (2 failures/7 tests), then 11 contracts/adversarial tests OK/1.514s. |
| `1b6c95` then `d62f85` | Cached same-target quarantine substitution incorrectly consumed authority (1 failure/0.379s), then 12 tests OK/1.838s. |
| `d71608` then `55f05d` | Wrong failure reference left an immutable poison record; early predecessor poisoned fresh adjudication (2 intended failures/0.730s), then 14 tests OK/2.614s. |
| `1369b9` then `4cbeb7` | Dual native close retained only 1 of 2 owners (1 failure/0.005s), then 20 tests OK/3.182s. |
| `a3daac` | Final receipt RED: old cleanup child-root effects lost in restart failure, and fresh receipt-parent creation failure lacked its own failure record (2 failures/7 tests/1.562s). |
| `809904` then `043fac` | Delegate+projection failed-close union retained 4 rather than 5 owners (1 failure/0.017s), then all 27 focused preparation/owner tests OK/4.329s. |
| `task-7-focused-native.log` / `4459ff`, `a7bf44` | Broader affected suite caught an overbroad wrapper changing generic Task4 ownership type: 265 tests, 1 failure/5 errors/2 skips, 12.156s. Finalizer was narrowed to actual expected projections. |
| `task-7-focused-native-final.log` / `35fe1f`, `896fb7` | 265 tests, OK (2 skips), 11.885s; exit 0. |
| `12f853` then `task-7-focused-native-v2.log` / `f1f1cd`, `7b73e0` | A canonical legacy Failure was accepted alongside a new-format Attempt (1 intended failure/0.218s). Store now refuses that inconsistent combination before publishing it; 266 affected tests OK (2 skips), 15.415s, exit 0. |

Initial setup/import mistakes were not counted as meaningful RED: an enum member typo (`VALIDATED` versus `SUPPORTED`), a wrong watch module import, and a test helper omitting required `allow_reparse` caused errors before their corrected intentional assertions. The last mistake interrupted temporary test cleanup; the exact residual `modlab-owner-union-2l18r1i0` was left as failure evidence, not silently deleted. Those interpreter processes are terminal; subsequent successful owner tests explicitly retain every local pin before another potentially failing operation. Earlier real fixture diagnostics used existing native temporary-directory behavior; all new tests use worktree-owned scratch, and the final affected/full gate sets TEMP/TMP to the worktree's exact-created `nt` directory.

Affected-module command:

```text
<bundled-python> -B -m unittest tests.test_mo2_containment_junction tests.test_mo2_containment_service tests.test_mo2_containment_store tests.test_mo2_containment_cli tests.test_mo2_preparation_recovery -v
```

The final focused skips were the existing directory-symlink test (WinError 1314: privilege unavailable) and the explicitly opt-in all-real archive test, omitted only in this fast affected-module pass. The latter is enabled in complete discovery. Final focused log SHA-256: `40dd183b4a87f7cd0e720a79129b80c8558024c06e09801968608754fafe3bde`.

The amended final focused log is `task-7-focused-native-v2.log`, SHA-256 `83e356bab3eb0141ac8e8b6aba62e0e8f8d7721929903513396636eafd2580c6`; its same two skips are declared above.

For precise RED reproduction, the initial command was `<bundled-python> -B -m unittest tests.test_mo2_containment_service.PreparationProjectionRegressionTests -v` (at its original three-test form). Later critical assertion excerpts are: `AssertionError: True is not false` for poison-record existence; `AssertionError: 2 != 1` for native process/snapshot close-owner union; `AssertionError: 5 != 4` for delegate/projection owner union. Corrected tests are retained in source, with the failing run IDs and counts above; no import/setup errors are substituted for those assertion failures.

### Real archive/process/publication integration

Read-only prerequisites: verified original `C:/Users/red/Desktop/Modlab/workspace/inbox/Mod.Organizer-2.5.2.7z` and `C:/Users/red/Desktop/Steam`. Curated originalName remains `Mod.Organizer-2.5.2.7z`, never `payload.7z`. Free C: space was checked (~1.577 TB before the first real attempt, 1,574,520,193,024 bytes immediately before final full discovery). No download or input mutation.

Command uses `MODLAB_PREPARATION_ARCHIVE` and `MODLAB_PREPARATION_STEAM` with those exact paths, then `<bundled-python> -B -m unittest tests.test_mo2_preparation_recovery.RealPreparationRestartTests -v`. A seven-key non-secret native environment is retained for foreground child and parent restart; no environment dump was performed.

The child performs real preparation, real tar extraction/bootstrap, real junction creation/publication, then one deliberate exception **after** the genuine expected-projection observer. It exits in the foreground, leaving actual intent/attempt/failure/projection receipts. Parent verifies native exact child absence, recovers and restarts through production APIs, compares original evidence bytes, checks disjoint run/session/fixture identities, exact four-record fresh request, absence of old success records, and repeated-consumption refusal. No bootstrap, filesystem, publication or process proof is mocked in this integration; it is stronger than the permitted extraction-only stub.

Actual development runs retained as errors, not passed evidence:

- `5eed8c`: 1 failure/3.388s; test-root extraction cwd exceeded tar path limits.
- `64ce99`/`26cf31`: 1 failure/47.121s; real sharing conflict publishing creation receipt through a retained ancestor.
- `0a2d32`/`346759`: 1 failure/44.978s; old scenario/watch errno/winerror false-absence-refusal defect exposed.
- `ae5d55`/`ddddf5`: real regression error/44.778s; empty direct scenarios scaffolding incorrectly treated as started evidence. Companion native absence test passed.
- `1c2e64`/`f3b592`, session 75610: 1 error/96.405s; real recovery and fresh NewFolder succeeded, then tar refused fresh MergeExisting extraction cwd length 259.
- `f950e2`/`e8cfd6`, session 63299: **1 test, OK, 228.739s, exit 0, zero skips** after exclusively allocating a new three-character test-owned root. Source remained unchanged throughout. Additional test-only receipt assertions written while that process ran were not loaded by it; final full discovery includes them.
- `0a0669`/`2269bf`, session 56911: **1 test, OK, 237.950s, exit 0, zero skips**, with its own real Low-integrity TEMP/Low and the additional effect-receipt assertions. Raw log `task-7-real-native-final.log`, SHA-256 `047a9f4761dbbf2587b6d0079eaee4b530e71ca33f4ed98c65cc2007bf4073bf`. Source did not change during this run. The subsequent store inconsistency check and its test are included in the final complete gate.

### Full native gate and file identity

Final code/test byte hashes were captured before full discovery in `task-7-final-files.sha256.json` and will be verified unchanged afterward. The test runner runs at BASE HEAD with final repaired working-file bytes (as required before committing); this is not a claim that old HEAD's tree contains the repairs. New attempt records include both HEAD/tree and actual source-byte digest. The eventual clean repair commit is the live source identity only after review.

```text
TEMP=TMP=C:/Users/red/Desktop/Modlab/.worktrees/mo2-guard-bridge-handshake/nt
MODLAB_PREPARATION_ARCHIVE=C:/Users/red/Desktop/Modlab/workspace/inbox/Mod.Organizer-2.5.2.7z
MODLAB_PREPARATION_STEAM=C:/Users/red/Desktop/Steam
<bundled-python> -B -m unittest discover -s tests -v
```

The first full invocation completed with **1081 tests, 1 failure, 10 skips, 160.430s, exit 1** (`d5f4ba`/`d2a2ab`, session 37522). Its only failure was the all-real test's missing test-owned TEMP/Low prerequisite, before the intended controlled failure could occur; 1,070 tests passed and 10 were skipped. The raw log was moved without modification to `task-7-full-native-prerequisite-failure.log`; before/after SHA-256 is `f70f0a349ce3a1cb4d12a02c108313179a732196dcda25bde96aede464706857`. No failing log was overwritten or relabelled as passing.

Root explicitly approved correcting this bounded test prerequisite and rerunning the affected real test followed by full discovery. The real test now exclusively creates `native-temp/Low` beneath its own short test root, applies the real Low integrity label, and sets only its child/parent operation environment to that test-owned TEMP. No original TEMP or production path logic was changed. The initial manifest comparison confirmed this prerequisite fix changed only the new test file; the subsequent separately reported consistency fix changed the store and added one test. `task-7-final-files-v2.sha256.json` freezes all final code/test/plan byte hashes before the final complete gate.

Final stdout/stderr are preserved separately by PowerShell `2>&1 | Tee-Object` in `task-7-full-native-final.log`. Session 97289 (`936c7b` through final `b78b22`) terminated with actual exit 0:

```text
Ran 1082 tests in 389.721s
OK (skipped=10)
```

That is 1,072 passing tests and 10 skips, including a passing all-real foreground failure/recovery/fresh-four-fixture regression. Final full log SHA-256: `cf2eae26ffc68d6c88eaceaaa7b5c30bbedd13cec316164bc29d1104b3d3532f`. Frozen manifest SHA-256: `7c8d5c6f38307e559e6924f92b0cac466097c8cd3aa274b891247971b97f6c17`. All 12 frozen source/test/plan hashes were rechecked after termination and matched (`68a228`). `git diff --check` and the staged check passed; Git emitted only the existing CRLF normalization notices and the disclosed residual test-directory enumeration warning. No source/test bytes changed during this final gate.

The final ten skips are explicit, not silently counted as passing:

- Eight WinError 1314 symbolic-link privilege skips: `test_redirected_package_file_refuses_before_job`; `test_redirected_ini_refuses_without_plan`; `test_redirected_workspace_refuses_without_plan`; `test_production_snapshot_records_reparse_without_following_it`; `test_list_run_ids_rejects_symlink_when_supported`; `test_rejects_redirected_sources`; `test_rejects_redirected_source_paths`; `test_rejects_redirected_target_paths`.
- Two existing opt-in tests used their separate unconfigured input settings: `test_real_archive_creates_ready_disposable_instance` and `test_real_fixture_is_exact_isolated_and_low_integrity`, reason “real MO2 archive and Steam root were not supplied.” The new, separately configured full archive/preparation regression **did run and pass**; its supplied inputs were not represented as enabling those older switches.

This second full invocation was the explicitly approved failed-prerequisite correction, not a repeated unchanged baseline. Raw logs remain retained beside this report; the evidence commit includes this summary and the final file-hash manifest, not fabricated stdout.

## Files and source-bound evidence implications

Production: `windows_junction.py`, `mo2_containment_fixtures.py`, `mo2_containment_service.py`, `mo2_containment_store.py`, `mo2_containment_cli.py`, new `mo2_preparation_recovery.py`. Tests: junction/service/CLI, new focused preparation tests, and the existing `tests/support/mo2_containment.py` fixture helper. Store behavior is covered with real store tests in the new preparation test module and the unchanged entire existing store suite. Root's approved plan amendment is retained.

No changes to shared `modlab/platform/windows_exact_fs.py`, archive runner, MO2 process adapter, Windows integrity implementation, Windows watcher, bridge runtime capability, frozen capability-matrix helper or driver. The new preparation module borrows native watch primitives and the process-entry structure but does not change those dependencies. Existing policy versions remain protocol 2, decision binding 2, `handle-pinned-no-replace-v2`, fixture 2, effects 1, authority 1.

The Task 6 capability record and matrix remain at their original source identities. This repair's tests do not relabel them as repaired-head live evidence. Root's impact precheck found the frozen capability driver does not import the changed service/fixtures/junction modules; shared dependencies it uses are unchanged. Independent review must confirm that dependency assessment and choose any necessary revalidation. All later four-scenario results/decision must be genuinely fresh on clean reviewed repaired source.

## Explicit limitations and review concerns

- **Separate live path prerequisite:** prospective ORIGINAL workspace source-extraction cwd lengths are NewFolder 253, MergeExisting 257, FomodDependency 259. Failed long test scratch lengths were 255/259/261; final short scratch is 247/251/253. System tar demonstrably refused a cwd of length 259. Thus original FomodDependency has the same observed-risk length. Shortening test scratch does not fix or hide this live limitation. Root will not consume original replacement authority just to reproduce it. No production path/archive workaround is part of this repair.
- **Supported legacy policy, not universal recovery:** current abandonment depends on the explicit candidate-image/call-graph assumption described above. No old PID, source revision, exit code or fixture creation ownership is fabricated. Unknown/unsupported cases refuse. Exact old fixture state stays untouched, including reparses whose creation cannot be proved.
- **Hard interruption without final receipts:** a new-format attempt interrupted before its final failure/effect receipt refuses cleanup/restart; absence of a request does not fabricate missing ownership. Interruption after an exact cleanup move but before recovery publication retains durable cleanup intent/quarantined state and refuses retry without reconciliation. No automatic general crash-resume/reconciliation was added. Tests explicitly demand refusal and no replacement marker in that interrupted state.
- **Pre-existing watcher false refusal:** `windows_watch._winerror` puts the raw Windows code in `OSError.errno`, while its existing exact-process status path checks `winerror`. The new preparation helper reads raw GetLastError correctly; existing watch/scenario behavior was deliberately not changed or claimed fixed. This limitation is separately disclosed for later follow-up.
- Process policy is intentionally conservative: unrelated candidate processes and all native ownership uncertainty block progress, rather than being ignored.
- Strict canonical content IDs and immutable publisher semantics are consistency/identity evidence within the existing trusted controller/storage model, not cryptographic attestations against arbitrary same-user code rewriting every record and its hashes.

Self-review checked exact source/target/parent pins, opaque non-following observation, failure effects, no-replace ownership transfer, failure-record-before-retry constraints, immutable/cross-command replay refusal, fresh-only lineage, and refusal after cleanup substitution/interruption. Actual test failures led to specific corrections rather than relaxed assertions. Independent review remains mandatory and is intentionally **not** self-dispatched; root will provide BASE-to-repair diff, this report, requirements and native logs to its reviewer before any result is consumed.

## Commit record

Source/test/approved-plan repair commit: `40db6ef160b7857231e21fda91071bd646e318fb`, tree `f63988fa33bf2880d603fb01cb1e5c07e92a8b65`, parent BASE `28554debfefe75348b7c8c712eaf976c72b4e5fc`. It contains the exact tested code/test files and root's existing plan amendment (12 files, 2001 insertions, 52 deletions).

This report and `task-7-final-files-v2.sha256.json` are committed in the following evidence-only commit. Its ID is provided in the handoff rather than creating a self-referential document hash. No merge or push. The full native gate preceded the commits as instructed; commit creation changed source HEAD identity, not the tested working bytes. Root's independent reviewer must evaluate the entire BASE-to-final repair and these disclosed scope limits before any live result is consumed.
