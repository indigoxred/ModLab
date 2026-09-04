# Task 7A pause report — 2026-09-03

## Status and stop boundary

PAUSED INCOMPLETE at the user's overnight stop. Task 7A is unreviewed and not
live-ready. The one in-progress complete native run finished with exit 1:
1109 tests in 510.283s; failures=1, errors=5, skipped=10. No fixes or further test
runs followed that result. No source/evidence commit, push, merge, Task 7B work,
GUI/MO2/game launch, or restart/recovery of the original live attempt occurred.

Worktree: C:\Users\red\Desktop\Modlab\.worktrees\mo2-guard-bridge-handshake

HEAD/base remains 80ae17dd8c7a06f246a4abaae3b851b3d49a2fca.
HEAD tree: e44a22ab96ae7eede4c1f058099523875d009f98.
There is no commit/tree claiming the dirty implementation is reviewed or passing.
The worktree has 16 modified tracked source/test files and 2 new source/test files.
Root-owned plan/ledger/Task7B materials were not edited by this agent.

The frozen source snapshot is recorded in task-7A-tested-source-sha256.txt:
157 files under modlab and tests, including both new files; SHA-256 of that
manifest's actual bytes:
c5c774aa71d6df7816740a2bca484d30d8f2432efcfaffdfd279cb90468e28a7.
It was captured after the run; source/test bytes were kept frozen from run launch
through this report. There was no independent pre-launch hash manifest, so this
is the coordinated frozen-source snapshot, not a claimed before/after comparison.

## Implemented but not yet accepted behavior

- Added a pure preparation-operation budget with policyVersion=1,
  scope="mo2-preparation-only", runtimeQualified=false. These machine-readable
  fields are strict on read; a preparation budget does not confer runtime approval.
- Counts UTF-16 units (including non-BMP pairs), limits components to 255 units,
  rejects nonordinary/alias/malformed paths, unknown consumers and explicitly
  unbounded patterns. Refusal identifies stage, consumer, full path and limiting
  component. Limits are policy bounds, not universal Windows limits:
  tar-cwd=247 and preparation-wide=512, at most 100000 declared paths.
- New bootstrap plans/journals use schema 2 with pathBudget. New stage names are
  .s followed by the lossless lowercase base32 encoding of the full 128-bit job
  identity (26 characters). No ID truncation, hidden mappings or extended prefix.
  V1 plan/journal field sets and historical .skyrim-se-ae.modlab-stage-<32hex>
  interpretation remain in the readers. Receipt schema remains 1.
- New containment fixture physical paths are
  <validation>/<full-run-hex>/fixtures/v2/<scenario>/{s,t}; only new job-owned
  source/stage fixture roots change. Historical fixtures/<scenario>/
  {source-workspace,stage-workspace} are explicitly recognized for recovery.
  Configured workspaces, production final layout, member names and Steam are
  unchanged.
- Archive extraction repeats actual listing validation and path admission before
  creating the extraction directory. Bootstrap Create/Adopt collect and bind
  current actual archive listing before create_job; schema-2 create_job now
  requires listing=. Bootstrap plans/journals persist the versioned budget.
- Preparation preflights all four scenario fixture layouts before store/effect
  initialization. restart_preparation performs complete future-job preflight
  before recover_preparation or one-use replacement consumption, then preparation
  rechecks at its effect boundary. The outer artifact observation is read-only;
  it no longer instantiates ArchiveVault merely to inspect input, because that
  constructor initialized absent workspace branches.
- Read-only tar version/listing subprocess launches are recorded through existing
  service-authored launchedProcesses, including refusal. Public containment JSON
  field names are unchanged. Internal ContainmentEffects gains preparation_processes.
- Budget construction includes actual admitted archive entries, planned extraction,
  generated package/profile state, activation/prior/recovered-stage/
  recovered-activated trees, record/publication candidates, and fixture/service
  destinations. Publication candidates reserve DWORD-width PID/thread IDs
  (10 decimal digits each) and 16 hexadecimal random digits.
- The intended complete known-path inventory is NOT yet accepted as complete:
  see the explicit self-review concerns and failing full gate below.

Changed interfaces include BootstrapPlan.path_budget, BootstrapJournal.path_budget,
Mo2BootstrapStore.stage_root(..., layout_version=1),
Mo2BootstrapStore.create_job(..., listing=None), collection layout_version,
fixture preflight's returned PathBudget and optional command_runner, and internal
prepare_run(_admitted_run_id=...). V1 defaults preserve historical direct callers;
new production planning deliberately selects schema/layout 2.

## Root-cause measurement and exact native stack

Durable raw probe: task-7A-native-tar-probe.log.
Probe code: task-7A-probe.py.
Command: bundled python.exe -B
.superpowers/sdd/2026-09-02-mo2-containment-authority-remediation/task-7A-probe.py
(native scoped escalation, exclusively created worktree scratch). Probe exit 0.

Exact retained archive:
C:\Users\red\Desktop\Modlab\workspace\inbox\Mod.Organizer-2.5.2.7z
149660212 bytes; SHA-256
e6376efd87fd5ddd95aee959405e8f067afa526ea6c2c0c5aa03c5108bf4a815.

Python executable:
C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe
3.12.13, MSC v.1944 64-bit AMD64; SHA-256
d8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa.

Tar: C:\Windows\System32\tar.exe; bsdtar 3.8.8 / libarchive 3.8.8;
SHA-256 4e598a8cec84af779e3377442fce2b94b976f614ed4c6e5a665e19308fd1e379.
The raw log retains the complete --version dependency string and argv/stderr.

The exact release's longest member has 87 UTF-16 units:
stylesheets/Transparent-Style/Fallout4/d9h8u7q-2cc20407-84ae-4367-967c-3b635db2d8af.png

Extraction of that member succeeded at cwd lengths 180, 247, 253 and 258, with
resulting full paths 268, 335, 341 and 346 respectively. Python read and ordinary
Path.rename/read succeeded for those extracted files. Cwd 259 and 260 failed
with tar exit 1, "could not chdir", before output. Thus the observed failure is
specifically the ordinary tar -C shape, not evidence of a universal 260-file-path
limit. The admitted 247 cwd bound is conservative policy headroom.

Separate native boundary tests exercised a generated ZIP through the actual system
tar with cwd exactly 247, archive input exactly 512, extracted member exactly 512,
and a 513-unit member refused before a second stage was created. This is not
represented as a 512-unit full exact-release extraction.

Python write/read, shared retained-handle no-replace file rename, and shared
publication candidate creation/readback were exercised at 512. A recursive watcher
at root 506 captured file rename at 512; a root at 512 separately armed/completed
without an unbudgeted deeper child. The publication boundary used actual shared
publisher operations with PID/thread providers patched to their maximum admitted
DWORD-width representations. These checks are distinct from ordinary Path.rename
and from the archive extraction probe.

No native MO2 first/reload/passive/guarded launch was performed. Unknown native MO2
cache/temp/log/output patterns remain unqualified. The later source-bound actual
MO2 gate must establish its declared output/path policy; preparation-only scope
cannot substitute for it.

## RED and intermediate focused evidence

All commands use the bundled Python above with -B, scoped native escalation, and
TEMP/TMP beneath this worktree. No concurrent root Python/git/tar probes ran during
absence-sensitive gates.

task-7A-red.log contains the real pre-implementation RED command and full intended
failures: deep archive destination did not refuse; new planning still selected
schema 1; unsupported restart mutated old evidence and produced recovery/replacement
state. Native exit 1, 3 tests, 3 failures. An initial malformed test fixture omitted
ModOrganizer.exe and failed before assertion; that setup error was corrected and
the true RED rerun before production edits. The original schema assertion was
subsequently strengthened to inspect persisted preparation budget and layout/member
consumption; it is not the sole current new-layout assertion.

task-7A-effects-red.log records a later focused real archive refusal RED: read-only
tar subprocesses were not reported in the public effects contract. The new
service command runner was then introduced.

Intermediate durable results (not accepted final GREEN gates):

- task-7A-focused-01.log: 86 tests; failures=4, errors=2, skipped=1; exit 1.
  Modules: tests.test_mo2_path_budget, tests.test_mo2_archive,
  tests.test_mo2_bootstrap_serialization, tests.test_mo2_bootstrap_store,
  tests.test_mo2_bootstrap_create, tests.test_mo2_bootstrap_adopt (-q).
- task-7A-focused-02.log: 109 tests; errors=1, skipped=2; exit 1.
  Modules: tests.test_mo2_path_budget, tests.test_mo2_containment_fixtures,
  tests.test_mo2_bootstrap_create, tests.test_mo2_bootstrap_adopt,
  tests.test_mo2_bootstrap_recovery (-q).
- task-7A-focused-03.log: 192 tests; failures=3, errors=1, skipped=2; exit 1.
  Modules: tests.test_mo2_path_budget, tests.test_mo2_containment_fixtures,
  tests.test_mo2_containment_service, tests.test_mo2_preparation_recovery (-v).
  Archive refusal opt-in enabled, full Steam restart opt-in not enabled.
  That file notes its earlier initial-chunk summary versus its retained raw tail.
- task-7A-focused-04.log: 5 tests, OK, exit 0. Native real archive old-attempt
  byte-preservation/refusal effects test plus four changed service tests
  (partial-fixture effects, pre-mutation refusal effects, identity/layout creation,
  run-enumeration refusal). These five exact test IDs are in the raw file.
- The two dedicated native boundary methods also passed in their focused native
  invocation and in the complete run's observed middle output. The full suite
  still failed elsewhere; those passes do not qualify the complete implementation.

## Complete native run — failed, raw capture incomplete

Exact invocation from the worktree, require_escalated:

    $env:TEMP = 'C:\Users\red\Desktop\Modlab\.worktrees\mo2-guard-bridge-handshake\nt'
    $env:TMP = $env:TEMP
    $env:MODLAB_PREPARATION_ARCHIVE = 'C:\Users\red\Desktop\Modlab\workspace\inbox\Mod.Organizer-2.5.2.7z'
    $env:MODLAB_PREPARATION_STEAM = 'C:\Users\red\Desktop\Steam'
    & 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest discover -s tests -v

Session 42036 completed, exit 1:
Ran 1109 tests in 510.283s
FAILED (failures=1, errors=5, skipped=10)

Durable output files:
task-7A-full-01-start.log
task-7A-full-01-chunk2.log
task-7A-full-01-tail-1.log

CRITICAL CAPTURE LIMITATION: the middle session chunk bf0b52 had original token
count 32687 but was polled with max_output_tokens=10000 after context compaction.
The session tool truncated it (and the outer tool response had another small
truncation); that chunk was not durably stored. Its available excerpt remains in
the tool transcript, spanning the end of planning through the start of real
preparation restart. It cannot be reconstructed as raw output. No rerun was
started after the user pause. The required complete-raw-evidence gate remains
INCOMPLETE independently of the failing test result.

The final 63446-character tail was preserved through apply_patch in
task-7A-full-01-tail-1.log; all final failure/error tracebacks and summary are there.
Raw content is preserved with newline normalization by apply_patch, not represented
as a byte-identical redirected process stream. The summary reports 10 skips.
Visible reasons include WinError 1314 symlink privilege and the two separate older
real archive/Steam opt-ins that were not enabled by the preparation-specific env
variables. The output gap prevents a complete durable per-test skip inventory;
do not silently infer that inventory from the prior baseline.

Final failing cases, preserved without fixes:

1. ERROR: test_create_and_adopt_report_a_receipt_promoted_before_reload_failure
   (Create and Adopt subtests): refusal rendering raises BootstrapFormatError,
   "programsLaunched cannot contain duplicates".
2. ERROR: test_real_post_activation_refusal_reports_recovery_job_and_actions:
   the same duplicate programsLaunched rendering exception.
3. ERROR: test_active_job_stage_collision_requires_recovery:
   direct create_job(plan) refuses "new layout requires current archive listing
   admission".
4. ERROR: test_foreground_failed_attempt_recovers_once_into_completely_fresh_real_preparation:
   the child failed-attempt preparation completed, but fresh restart errored with
   "delegated mutation effect observation is unavailable after operation" at
   <exclusive t0f>/runtime/validation/mo2-containment/
   c5a1ad55ba96439e884787b5e7f8bc28/fixtures/v2/NewFolder.
   This is not dismissed as a fixture-only issue; actual cause must be debugged.
5. FAIL: test_real_post_quarantine_failure_reports_both_recovery_paths:
   assertion still searches for the old .skyrim-se-ae.modlab-stage- prefix.

Do not use the baseline 1097-test GREEN as evidence for this dirty snapshot.

## Unfinished self-review and next gate

These observations were recorded before/at the pause, not fixed afterward:

- _preflight_preparation uses "new-folder"/"fomod-dependency" as quarantine mod
  names, while production _EXPECTED_NEW names are "ModLab Spike New" and
  "ModLab Spike FOMOD". Enumerate exact consumer-derived quarantine names,
  collision candidates and records before claiming the known path inventory is
  complete. protected-state.json also needs comparison to actual emitted labels.
- bootstrap_paths currently reserves plan-<64hex>-<32hex>.part and
  receipt-<64hex>-<32hex>.part, while _stage_document consumes the passed label
  (plan/receipt) plus <32hex>.part. This is conservative length overbudgeting,
  but not the exact declared temporary-path inventory. Verify all concrete labels.
- Strengthen legacy-byte coverage with baseline-bound/golden canonical bytes, not
  just current serializer round trips. Current tests do exercise schema-specific
  consumption and existing recovery cases, but that extra proof is unfinished.
- Review path-budget exception mapping at direct schema-2 store entry points,
  complete refusal coverage for ordinary prepare/apply, and actual listing
  identity at every admission boundary before accepting Task7A.
- Repeated archive preflights add real launches/work. Audit effect serialization
  against the duplicate-program failures without weakening public effects,
  ownership or failure-lineage invariants.
- Complete the new-layout native mutation-observation diagnosis using the actual
  failing root cause. Do not weaken watcher/absence/ownership checks to hide it.
- Independent task review has not begun; Task7B must not consume this checkpoint.

Resume only when the user resumes: first compare the frozen manifest/dirty diff,
diagnose the recorded failing cases with TDD and complete the path-inventory and
legacy-byte checks. Then run the affected tests and a complete native discovery
with direct durable stdout/stderr capture and explicit exit/skip inventory,
coordinating root's process-absence exclusion. Only after a passing, fully captured
gate should source be committed with the approved message and sent to root's
independent review. No live gate or original-attempt retry is authorized by this
pause report.

## Process / ownership closeout

Full suite Python PID 25272, child PID 9148 and shell PID 18908 were observed during
the run. After session 42036 returned exit 1, scoped native read-only
Win32_Process enumeration checked all python.exe/tar.exe, these IDs, and their
children: no matches. The process query itself exited 0. All tool exec sessions
used for closeout completed. No running Task7A child, watcher, shell or known
retained native owner remains; process-owned handles have been released by exit.
No agents were spawned.

The native test helper cleaned its own exclusive t0f scratch. Earlier disposable
probe payloads were also cleaned by their own guarded helpers. Historical scratch
was not deleted; git status still reports the existing permission warning for
modlab-owner-union-2l18r1i0, which is intentionally untouched.

Root independently confirmed the original actual intent hash remains
12a775a3bd559d6a3a3306c4fc1775c894dac3cf439b5694d655f983dfca4937.
This report does not grant it eligibility or spend its retry.

## Exact dirty source/test file list

Modified:
modlab/adapters/mo2/archive.py
modlab/adapters/mo2/bootstrap_model.py
modlab/adapters/mo2/bootstrap_serialization.py
modlab/validation/mo2_containment_fixtures.py
modlab/validation/mo2_containment_service.py
modlab/workflows/skyrim/mo2_bootstrap.py
modlab/workflows/skyrim/mo2_bootstrap_store.py
tests/support/mo2_bootstrap.py
tests/support/mo2_containment.py
tests/test_mo2_archive.py
tests/test_mo2_bootstrap_adopt.py
tests/test_mo2_bootstrap_create.py
tests/test_mo2_bootstrap_recovery.py
tests/test_mo2_containment_fixtures.py
tests/test_mo2_containment_service.py
tests/test_mo2_preparation_recovery.py

New:
modlab/adapters/mo2/path_budget.py
tests/test_mo2_path_budget.py

Report and task-7A evidence files remain durable but uncommitted in the ignored
.superpowers/sdd directory. They require explicit paths/force-add if committed
after acceptance; do not sweep in root-owned brief, plan, ledger or Task7B files.

# Task 7A resumed completion report — 2026-09-04

## Final status

IMPLEMENTED AND FULLY GREEN, pending root-owned independent review. The failed
pause run and its incomplete raw capture above remain historical evidence; they
are superseded for implementation verification by the complete, directly
captured run below. This Task 7A result is not live-ready by itself and grants no
MO2 runtime capability.

The strict machine-readable authority remains:

    policyVersion = 1
    scope = "mo2-preparation-only"
    runtimeQualified = false
    limits = {"tar-cwd": 247, "preparation-wide": 512}
    componentLimit = 255

The 247-unit tar cwd limit is conservative policy headroom derived from the exact
system-tar probe, not a universal Windows limit. The 512-unit wide limit is only
the boundary exercised through the declared Python, shared retained-handle,
publication, and watcher operations. Neither value qualifies unknown native MO2
outputs or implies support to 32767 characters.

No GUI, MO2 process, game, original failed live attempt, original recovery, or
original retry authority was launched, changed, recovered, or spent. Root
independently confirmed the original actual intent hash remains:

    12a775a3bd559d6a3a3306c4fc1775c894dac3cf439b5694d655f983dfca4937

## Resume diagnosis and TDD corrections

Every paused full-suite failure was traced through its call chain before its
correction:

1. Bootstrap Create/Adopt actually launch six ordered extractor processes:
   version, list-names, list-types, list-names, list-types, extract. The second
   listing pair is the apply-time source/listing revalidation. Results now report
   the runner's actual ordered invocation trace. The ordered renderer accepts
   those exact repetitions; unordered fields still reject duplicates. No launch
   is discarded or invented.
2. The active-job collision unit seam now supplies the exact current
   ArchiveListing required by schema-2 create_job. Production implicit admission
   was not restored.
3. The post-quarantine assertion now consumes the schema-2 lossless stage name
   rather than looking for the historical schema-1 prefix.
4. The native NewFolder post-observation failure was investigated with one
   exclusively coordinated, instrumented real restart. After two harness setup
   failures before containment, the corrected isolated test passed naturally in
   806.935 seconds. No watcher, ownership, mutation-observation, or projection
   check was weakened. The same real restart subsequently passed inside the
   complete exclusive suite, so the earlier failure is retained as suite-context
   interference evidence rather than papered over by production code.
5. Schema-2 PathBudgetError now maps to Mo2BootstrapStoreError at the public store
   boundary before job/stage creation. Prepare and apply one-over tests assert
   absent roots and pre-existing bytes remain unchanged.
6. Bootstrap plan, receipt, and journal staging candidates now come from the same
   shared constructor used by the mutator: plan-<32hex>.part,
   receipt-<32hex>.part, and journal-<jobhex>-<32hex>.part at the bootstrap jobs
   root. Mutable containment journal replacement likewise shares the exact
   .journal.json.<32hex>.part constructor with its mutator.
7. The shared immutable publisher exposes and consumes one constructor for
   .<destination.name>.<pid>.<thread-ident>.<16hex>.tmp. PID and thread identity
   must each be in 1..4294967295 and the token must be 16 lowercase hexadecimal
   characters. One-over tests refuse before pinning or target mutation.
8. Archive admission reconstructs canonical listing bytes from every actual
   entry's normalized spelling plus kind (including restored directory slash),
   compares the listing digest, and rejects case collisions, forged spelling,
   forged kind, forged digest, missing attributes, and invalid Unicode through
   PathBudgetError.
9. Containment record inventory uses the store's actual before.json and
   after.json labels, not protected-state.json. Publication candidates are
   included for every immutable run, scenario, watcher, and preparation-
   projection record, plus the separate mutable journal part.
10. Quarantine destinations use the same production _EXPECTED_NEW names as the
    mutator: ModLab Spike New, Protected Existing, and ModLab Spike FOMOD. The
    protected fixture file map is now a single production source consumed by
    both fixture creation and merge-quarantine budgeting, so marker.txt,
    meta.ini, and meshes/canary.bin cannot drift independently. Replace and
    FOMOD/NewFolder member inventories remain bound to their production output
    constants.
11. Containment preparation subprocess receipts are appended losslessly and in
    order across nested effect receipts. Identical labels are not deduplicated;
    every successfully created Popen is recorded immediately with operation and
    PID, including read-only preflight launches and later refusal paths.
12. A contract regression exposed Path(target) reparsing an already concrete
    WindowsPath after a test selected the POSIX promotion branch. The shared
    mutable-part constructor is now a pure target.with_name lexical transform,
    preserving the caller's path flavor without host-dependent reparsing.

The schema-1 canonical golden proofs now bind the legacy plan to 3657 bytes and
SHA-256 19a06a8e47ef46345efa06779ad01bbc44ea97e8052ed0a39317dc93792a5030,
and the legacy journal to 840 bytes and SHA-256
6dc6f352012c0febf42ee33de47aabe4a6cda841ba8074057a57e1a97afec550.
Schema-1 readers retain the original field set, stage naming, and canonical
bytes; schema 2 explicitly carries pathBudget and the new layout.

## Exact operation inventory closure

New planning admits the current actual archive listing and both possible Create
and Adopt operation shapes before persisting a plan. Apply binds the newly
observed listing identity to the plan before create_job. Archive extraction
separately revalidates and admits its actual destination immediately before
creating the stage.

The bootstrap inventory covers the new lossless stage root, final root, job
directory, prior, recovered-stage, recovered-activated, Create stage/app
extraction, Adopt direct-stage extraction, every actual archive entry, generated
portable/config/profile/directory path, plan/journal/receipt targets, shared
publication candidates, mutable staging parts, retained archive input, and
fixture vault import/metadata candidates. The preserved prior can only be the
classifier's exact five-directory empty skeleton; those paths are included in the
generated tree inventory. No historical/configured workspace is shortened,
aliased, hidden, or moved to satisfy the policy.

Containment preparation covers both disposable workspaces, documents and
environment roots, three fixture archives, curated bootstrap archive, all four
scenario records and watcher records, exact publication candidates, preparation
projection records, preparation recovery projection quarantine, both ordinary
and recovery scenario quarantines, and every known child member at those
destinations. Unknown user-selected mod names and unknown MO2 runtime outputs are
deliberately unsupported.

The native evidence distinguishes three stacks rather than extrapolating among
them:

- archive extraction: actual system tar at admitted cwd 247 and generated member
  destination 512, with 513 refused before stage creation;
- ordinary pathlib: extracted-file read and Path.rename through observed exact
  release full-path length 346;
- shared exact operations: retained-handle no-replace rename and immutable
  publication at declared wide boundary 512, plus recursive watcher rename at
  512 and separately armed/completed watcher root 512.

## Resume evidence and final verification

Durable resume evidence includes:

- task-7A-resume-green-01.log is historical failed evidence and is explicitly
  excluded from GREEN results: its six-test run failed the old exact containment
  path-name expectation.
- task-7A-affected-green-01.log is historical failed evidence and is explicitly
  excluded from GREEN results: its 329-test run failed the bootstrap CLI apply
  rendering regression.

- task-7A-resume-red-01.log: intended schema-2 error mapping, exact temporary
  names, forged listing identity, and containment inventory REDs.
- task-7A-focused-green-02.log: 28 tests, OK.
- task-7A-lexical-path-red-02.log and task-7A-lexical-path-green-01.log: one
  exact host-flavor constructor RED then GREEN. task-7A-lexical-path-red-01.log is
  only a recorded PATH/harness failure and is not treated as test evidence.
- task-7A-self-review-red-01.log and task-7A-self-review-green-02.log: duplicate
  process merge and malformed listing RED/GREEN. The intermediate green-01 log
  exposed a test-only accessor typo, which was corrected without relaxing the
  production contract.
- task-7A-quarantine-members-red-01.log and
  task-7A-quarantine-members-green-01.log: omitted known merge members RED, then
  31 tests OK with one symlink-privilege skip.
- task-7A-contract-green-01.log: 157 tests, three errors demonstrating the
  host-dependent Path reparsing defect. task-7A-contract-green-02.log: 158 tests,
  OK, one symlink-privilege skip.
- task-7A-affected-green-05.log: final affected set, 333 tests in 88.039 seconds,
  OK, four skips.
- task-7A-native-boundaries-01.log: two actual native boundary tests in 0.697
  seconds, OK, exit 0, run with worktree-owned TEMP/TMP in an exclusive window.
- task-7A-observer-diagnostic-03.log: isolated real restart, one test in 806.935
  seconds, OK. Diagnostic 01/02 retain pre-containment harness setup failures.

One attempted boundary invocation accidentally inherited default AppData TEMP
before the coordinated rendezvous. It failed pre-ready in 0.396 seconds on root
identity, created no product/live state, launched no MO2/game, and left zero
scoped processes. It is excluded from GREEN evidence; the correctly scoped
exclusive boundary run above is the evidence.

The later task-7A-review-service-focused-01.log is also excluded from gate
evidence. Although its three tests exited 0 in 0.314 seconds and left no scoped
processes or handles, it entered Windows retained-handle paths without the
required exclusive rendezvous.

Final authoritative command, launched from the worktree with direct stdout/stderr
Tee capture from process start:

    TEMP=TMP=C:\Users\red\Desktop\Modlab\.worktrees\mo2-guard-bridge-handshake\nt
    MODLAB_PREPARATION_ARCHIVE=C:\Users\red\Desktop\Modlab\workspace\inbox\Mod.Organizer-2.5.2.7z
    MODLAB_PREPARATION_STEAM=C:\Users\red\Desktop\Steam
    C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -B -m unittest discover -s tests -v

Complete raw log: task-7A-full-native-resume-01.log.

    Ran 1119 tests in 987.514s
    OK (skipped=10)
    Exit: 0

The ten skips are explicit in the raw log: eight unavailable symbolic-link tests
due WinError 1314, plus test_real_archive_creates_ready_disposable_instance and
test_real_fixture_is_exact_isolated_and_low_integrity, which use separate older
integration opt-ins intentionally not supplied by this preparation-only gate.
The preparation-specific real foreground-failure/restart test did run and pass.

The pre-run and post-run manifests each cover the same 24 modified/new source and
test files. Their combined content hash is identical:

    5d2b0f56bf5f692b28043ccd1b5479adf5958d97260b08790bd35c2932478cfe

Post-run mismatch count is zero. Files:
task-7A-source-manifest-before-full.log and
task-7A-source-manifest-after-full.log.

After suite exit, scoped Win32 process enumeration found zero Python, PythonW,
tar, bsdtar, suite-child, or watcher processes. The completed session returned
exit 0, and the durable log accepted an exclusive ReadWrite/FileShare.None open,
confirming the capture handle was closed. No spawned agent or other Task 7A owner
remains.

## Remaining gate and concern

The implementation is ready for root's independent source review, but Task 7A
does not authorize Task 7B publication or any live MO2 validation on its own.
Unknown native MO2 cache, log, plugin, download, temp, and other outputs remain
unqualified until the separate source-bound full-stack validation establishes and
tests its declared output/path policy. No push was performed.

# Independent-review repair report — 2026-09-04

## Review status and bounded corrections

The first Task 7A commit, ac31cb6cd57ccc32ca7f37d2e134f04b86813020,
was not approved. The following implementation remains preparation-only and is
awaiting a fresh complete native gate and re-review. It does not qualify MO2
runtime paths, authorize Task 7B, or permit a live/original-attempt operation.

The critical admission gap is closed by one immutable outer preparation
admission. Its four fixture admissions now bind the exact configured Steam root,
manifest, discovered game root and SkyrimSE.exe, every Data entry actually
consumed by discovery, generated Documents seed paths and bytes, retained archive
payload and metadata, bundled release path/descriptor, extractor identity, and
canonical archive listing. Optional inputs encode absence as well as presence.
The locked repeat and every guarded fixture repeat must equal the original full
snapshot. The nested fixture builder verifies the declared Documents bytes before
bootstrap preparation and receives that same expected admission. All of these
checks precede fixture/store material mutation at their respective boundary.

Source-side adoption and both ordinary/recovery quarantine destinations use the
shared production scenario constants, including `ModLab Spike New`, `Protected
Existing`, and `ModLab Spike FOMOD` and their exact known members. Capture and
recovery no longer use empty relocation preflights. While the existing retained
source/stage guards and persisted projection owner are held, they inventory the
exact source and stage trees without following foreign reparses; rows bind path
spelling, kind, canonical native volume/file identity, metadata, file digest,
projection payload/owner identity, mutation names, and destination mapping. The
admission is re-observed for exact equality under the same guards immediately
before the exact admitted name set can be moved. Long/one-over members,
case-colliding trees, unknown reparses, substituted persisted projections,
cross-volume mappings, and unavailable re-observation all refuse before a
quarantine root is created or source/stage bytes are changed. Recovery returns a
blocker and performs no later normalization after such refusal.

The important launch-truth gap is closed at the actual Popen creation boundary.
The runner callback fires only after native creation; spawn failure records
nothing, while nonzero exit, callback failure, and post-spawn communication
failure retain the launch. Callback/communication failure kills and waits the
created process before re-raising. Successful repeated invocations remain ordered
and undeduplicated through both bootstrap rendering and nested containment effect
merging. Injected runners must implement the callback protocol; there is no
attempted-call fallback.

Final self-review found one missing callback-exception assertion after the
affected gate. The existing production cleanup already satisfied it; the focused
mocked regression now proves callback failure is recorded and the created process
is killed/waited, alongside spawn failure, nonzero exit, communication failure,
and success. It passed 1/1 in 0.001 seconds. No production change was required.

## Review-repair TDD and affected evidence

Focused REDs preserved for the three findings include
`task-7A-review-launch-red.log`, `task-7A-review-input-red.log`,
`task-7A-review-relocation-red.log`, `task-7A-review-projection-owner-red.log`,
`task-7A-review-owner-source-red.log`, and
`task-7A-review-recovery-refusal-red.log`. They are deliberately failed TDD
evidence and are excluded from GREEN/gate results. Corresponding focused GREEN
files include `task-7A-review-launch-green-04.log`,
`task-7A-review-input-green-01.log`,
`task-7A-review-fixture-inputs-green-02.log`,
`task-7A-review-release-input-green-01.log`,
`task-7A-review-relocation-green-01.log`,
`task-7A-review-projection-owner-green-02.log`,
`task-7A-review-owner-source-green.log`, and
`task-7A-review-recovery-refusal-green.log`.

The following review-era logs are explicitly historical failed or incomplete
evidence and are excluded from GREEN/gate claims:

- `task-7A-review-native-focused-01.log`: failed relocation observations plus a
  watcher invocation later proven to have run under the sandbox token.
- `task-7A-review-native-relocation-owner-01.log`: valid RED exposing the invalid
  `st_dev` versus retained-handle volume comparison.
- `task-7A-review-native-watcher-isolated-01.log`: sandbox-token watcher failure,
  invalid for native product gating.
- `task-7A-review-affected-escalated-01.log`: incomplete/interrupted invocation
  containing the coordinator's nonexistent `tests.test_mo2_bootstrap` module;
  it has no terminal summary or exit record.
- `task-7A-review-affected-escalated-02.log` through
  `task-7A-review-affected-escalated-06.log`: stopped affected-suite REDs for,
  respectively, incomplete fake discovery inputs, a retired re-observation seam,
  a retired changed-name seam, a synthetic pinned identity, and a missing
  configured Steam fixture. Each is excluded from GREEN results.
- `task-7A-review-fixture-builder-escalated-01.log`: focused RED for the fake
  release object's missing concrete bundled-release path.

The following are invalid environment/harness evidence and are also excluded:

- `task-7A-review-native-watcher-escalated-01.log`: only the red token was
  captured; the unsupported Tee parameter prevented Python from starting.
- `task-7A-review-watcher-identity-diagnostic-01.log`: useful diagnostic showing
  all eight watched-root aliases retained exact identity, but its worker ran as
  `desktop-947h2kl\\codexsandboxoffline`; it is not gate evidence.
- `task-7A-review-service-focused-01.log`: three passing retained-handle tests
  launched without the required exclusive rendezvous, already excluded above.

The escalated watcher rerun under `desktop-947h2kl\\red` passed 1/1 in 0.453
seconds (`task-7A-review-native-watcher-escalated-02.log`). The ordered full
NativePathBudgetTests class then passed 8/8 in 0.820 seconds
(`task-7A-review-native-class-escalated-01.log`). No watcher or ancestor-access
check was weakened.

Several stale tests were corrected only at their intended v2 seams: fake fixture
builders now provide the exact manifest/game/Data/Documents/release evidence;
re-observation failures target the canonical relocation observer; replaced
baseline classification asserts the immutable mutation-name/destination mapping
without restoring `_changed_projection_names`; close-only ownership injects the
pre-captured canonical native root identity instead of `(1, 1)`; and the
real-archive unsupported-future-path test binds the explicit real Steam opt-in so
it reaches the intended `tar-cwd` refusal before recovery consumption.

The latest corrected 13-module affected command is captured completely in
`task-7A-review-affected-escalated-07.log`:

    Ran 447 tests in 961.268s
    OK (skipped=5)
    Exit: 0

SHA-256:
`e29330c0e00a740186f7bcae8bda9d999bfc59a600cc850badd7950dc9f6d6e0`.
It used worktree-owned TEMP/TMP, exact archive and Steam opt-ins, and the verified
unsandboxed red token. After exit, scoped Python/PythonW/tar/bsdtar and watcher
counts were zero and the log accepted an exclusive open.

The focused native corrections immediately preceding it also passed:

- canonical close-only completed snapshot: 1/1, 0.003s, exit 0,
  `task-7A-review-completed-snapshot-escalated-01.log`, SHA-256
  `23f133dce3928cb841dcfa07bd8acb4e89f98672c3376bfe0d6f50e907570dec`;
- real archive/Steam unsupported future path: 1/1, 1.151s, exit 0,
  `task-7A-review-future-path-escalated-01.log`, SHA-256
  `ed2a4a81241298e5fc6cdfcea6f4929e953d2a4f2a044f286d6e3f57d47eb328`.

The fresh complete native discovery remains the final implementation gate. A new
before/after 24-file source/test manifest and direct durable full-suite capture
will be recorded; source/test bytes will remain frozen from manifest creation
through suite completion and post-run comparison.

## Directory-container stability correction

The first review-era complete native attempt is preserved in
`task-7A-review-full-native-01.log` (SHA-256
`0353b8939f508bfbb71a2c90c4273e66028fff17f2d0aa55206eb41c20dfcba9`).
It ran 1,138 tests in 826.867 seconds and ended with one error and ten skips,
Exit 1. It is failed historical evidence and is explicitly excluded from all
GREEN/gate claims.

The failure was the all-real foreground restart's guarded `ReplaceExisting`
fixture preflight. The corrected in-order prefix diagnostic is preserved in
`task-7A-review-prefix-diagnostic-02.log` (SHA-256
`464763c08230794128075989189db5bdf363cade1e7bb561e664224375456da6`).
Its exact 890-test prefix passed; the diagnostic then stopped on one and only
one admission leaf difference: the configured Steam container directory's
`modified_ns`. Canonical path, kind, volume/file identity, mode, attributes,
and every actually consumed manifest/game executable/Data/CCC/archive/release
input remained identical. Unrelated Steam child creation/deletion had changed
the parent directory timestamp. This diagnostic ended Exit 1 by intentionally
retaining the production refusal and is excluded from GREEN/gate claims.
`task-7A-review-prefix-diagnostic-01.log` is separately excluded as an invalid
environment diagnostic because it enabled the unrelated MO2 integration
opt-ins that the accepted full command deliberately leaves unset.

The correction excludes size, modification time, and change time only from
directory identity equality. A directory remains bound by exact canonical
spelling, kind, native volume/file identity, mode, attributes and reparse
state. Every admitted regular file retains strict size, modification/change
times and its selected content digest; exact enumerated manifest, game
executable, Data entries and CCC evidence remain strict. No runtime path scope
was added.

The focused RED is `task-7A-review-directory-mtime-red.log` (SHA-256
`bffc158f6bf70b2d51cb48d70fa7b7a2871f490615519192cd3671995af34f82`):
unrelated direct-child churn changed the admission before the correction. Its
minimal GREEN is `task-7A-review-directory-mtime-green.log` (SHA-256
`ceed164a09a2090422b1b160d291943cb4c0d9c138b2b6a5cafe8ef4e285cde6`).
The expanded behavioral set proves unrelated child create/delete is stable,
while same-path game-directory substitution and consumed manifest timestamp
or same-length content drift refuse before fixture creation: 4/4 in 0.481
seconds, Exit 0, `task-7A-review-directory-semantics-green.log` (SHA-256
`bb1e95dcd608762612d3fd3eca1f76bf2243a40e74260096efdefebe1b6e1d1c`).
The complete pure policy class passed 22/22 in 0.582 seconds, Exit 0,
`task-7A-review-directory-policy-green.log` (SHA-256
`3fa31d5c239b7e8bf1e590c39abbe01b1b0632a49a6331ab89e525f1ef522473`).

The staged unsandboxed native gate then passed under
`desktop-947h2kl\\red` with worktree-owned TEMP/TMP and exact preparation
archive/Steam inputs:

- external game 512/513-unit admission and configured Steam junction refusal:
  2/2 in 0.205 seconds, Exit 0,
  `task-7A-review-directory-native-focused-01.log`, SHA-256
  `07a62193020d746bb4815fd3ef3e756224d43cb35c49c75638e59f554e4aee05`;
- the exact formerly failing all-real restart: 1/1 in 867.018 seconds, Exit 0,
  `task-7A-review-directory-real-restart-01.log`, SHA-256
  `dc7b5dbda487ffd3a86bbc073deb1d549aaec8523a1d901c964737e3c2527f77`.

After each native stage, scoped Python/PythonW/tar/bsdtar and watcher counts
were zero and the durable logs admitted exclusive opens. Source/test bytes were
frozen throughout. These staged results are worker evidence; a new complete
native run and same-reviewer approval still remain required.

## Final review-repair native gate

After the directory-container correction, the final diff/self-review found no
additional issue within the three bounded independent-review findings. The
critical preparation/relocation admission, important actual-spawn observation,
and minor evidence-label repairs described above remain intact. `git diff
--check` reported no whitespace error (only the repository's existing Windows
line-ending notices).

The exact pre-run inventory contains 24 source/test paths in
`task-7A-review-source-manifest-before-final.log`. Its manifest-file SHA-256 is
`299580c615f921a3ef1ba852eadbba576b9c931f6bc24ce009967d95c38f06ff`.
The path-framed content SHA-256 is
`f1ede0b8ae001fa9e2ef453e602012da06ed87f80f74349a0a21058f5db8212b`,
computed in manifest order over repeated
`uint32le(path UTF-8 length) || path UTF-8 || uint64le(content length) ||
content` frames.

The final authoritative command ran under the verified
`desktop-947h2kl\\red` token with worktree-owned TEMP/TMP, exact
`MODLAB_PREPARATION_ARCHIVE` and `MODLAB_PREPARATION_STEAM`, and with the older
`MODLAB_MO2_ARCHIVE`/`MODLAB_STEAM_ROOT` integration opt-ins deliberately
unset:

    C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -B -m unittest discover -s tests -v

Direct stdout/stderr capture from process start is preserved in
`task-7A-review-full-native-final-01.log` (SHA-256
`14ba3565d64f9c12e5fd2cd5d7c3e4f61425617730ae87581bbf0554c7a04b10`):

    Ran 1141 tests in 1031.891s
    OK (skipped=10)
    Exit=0

The ten skips are the same explicit privilege/older-integration exclusions
reported by the previous accepted complete suite. The preparation-specific
all-real foreground failure/restart ran and passed in-order, as did all eight
NativePathBudgetTests.

`task-7A-review-source-manifest-after-final.log` has the identical manifest-file
SHA-256 and path-framed content SHA-256. Its mismatch count against the frozen
pre-run inventory is zero. After suite exit, scoped Python/PythonW/tar/bsdtar,
suite-child and watcher counts were all zero, and the completed log accepted an
exclusive read/write open after its closure record. No GUI, MO2, game, live
validation, or original-attempt action occurred.

This is implementation-worker evidence only. Task 7A remains preparation-only
with `runtimeQualified=false`; it does not authorize Task 7B or live MO2 work
until the same independent reviewer approves the complete repaired range.

# Second independent-review relocation-authority repair — 2026-09-04

## Scope and reviewed defects

This bounded repair addresses only the second review's three relocation findings.
It does not expand the versioned preparation-operation admission into a general
runtime guarantee: the machine-readable scope remains preparation-only and
`runtimeQualified=false`.

1. **Scenario-blind persisted projection.** The old observer admitted a missing
   or direct `Protected Existing` baseline in every scenario. The repaired
   observer requires the exact persisted owner-bound projection in NewFolder,
   MergeExisting, and FomodDependency. ReplaceExisting alone may carry the exact
   admitted direct regular replacement tree. Missing or substituted baselines
   refuse before quarantine-root creation.
2. **Late destination collision detection.** Adoption-name and existing
   quarantine-destination collisions were previously left to low-level mutation
   code, after a quarantine root or stage movement could already occur. Guarded
   preflight now rejects exact and casefold source collisions for the scenario's
   adoption destination, and rejects every pre-existing quarantine destination
   collision. MergeExisting and ReplaceExisting retain their intentional
   `Protected Existing` destination policy.
3. **Descriptive rather than operative admission.** The old relocation record
   described rows and a digest, but mutation reopened/re-enumerated pathnames.
   The repaired authority retains the exact admitted source/stage root, every
   moveable member/tree object, and destination parents. Mutation consumes only
   this admitted name/object mapping through the existing shared retained-handle,
   same-volume, no-replace rename primitive. No pathname-authority fallback or
   second rename algorithm was added.

## Baseline and collision matrix

| Scenario | Required admitted baseline | Adoption destination | Collision rule |
| --- | --- | --- | --- |
| NewFolder | Exact persisted owner-bound projection | `ModLab Spike New` | Exact/casefold source destination and any existing quarantine destination refuse |
| MergeExisting | Exact persisted owner-bound projection | None; intentional existing `Protected Existing` destination | Any other baseline or quarantine collision refuses |
| ReplaceExisting | Exact admitted direct regular `Protected Existing` tree | None; exact replacement is quarantined | Missing, redirected, or substituted direct baseline and any quarantine collision refuse |
| FomodDependency | Exact persisted owner-bound projection | `ModLab Spike FOMOD` | Exact/casefold source destination and any existing quarantine destination refuse |

The same matrix is enforced in capture and recovery. A quarantine root is either
absent or an exact direct directory. When it exists, its canonical top-level
names must be collision-free and must not collide, including by casefold, with
any admitted mutation destination. Tests bind source/stage bytes and verify that
invalid missing/direct/substituted baselines, source collisions, quarantine
collisions, and injected membership changes produce no move. Refusal at the
pre-create boundary leaves an absent quarantine root absent.

## Immutable repeat and retained authority

Both existing-root and missing-root guarded branches call `preflight()` exactly
once. That exact immutable admission value is passed to retained preflight;
`_require_relocation_admission` re-observes and requires equality with it before
authority retention. A mismatch cannot be silently replaced by a freshly valid
record and refuses before root creation or movement.

Persisted projection ownership already holds restrictive pins on the source or
stage parent. Acquiring a second incompatible root handle would fail on Windows.
The mutation guard therefore borrows only candidates derived from an exact
`OwnedProjection`: the owner is verified, canonical path spelling must match its
target-parent or link-parent evidence, and native identity is revalidated. The
wrapper records borrowed and newly opened guards separately; it closes only its
new acquisitions, while the projection lifecycle closes the borrowed owner.

The authority-only move path is:

    capture/recovery
      -> _delegated_mutations_with_created_root
      -> _preflight_projection_relocation
      -> _retained_projection_relocation
      -> _require_relocation_admission (exact equality)
      -> retain_relocation_authority
      -> authority.verify (after the injected race boundary)
      -> bind created/existing quarantine parent and authority.verify
      -> _finalize_projection or recovery relocation
      -> adopt_retained_relocation / quarantine_retained_relocation /
         quarantine_retained_replacement
      -> _rename_pinned_object
      -> shared rename_pinned_no_replace

Fresh verification may enumerate to prove that the admitted membership and
identities remain exact, but it does not select a different mutation name or
object. The retired service pathname relocation functions and imports have no
remaining call sites. Stable-name member substitution is rejected by the
restored per-member pinned identity check. A partial retained tree carried by a
pin rejection is deterministically closed, and cleanup ownership errors are
unioned without masking the primary refusal.

## Race boundary and exact guarantee

After immutable admission equality is established under the outer guards, the
service retains source/stage/member owners and the destination parent. The test
hook runs at that boundary, followed by `authority.verify()` before any missing
quarantine component is created. Thus a membership addition, substitution, or
collision injected there refuses with source/stage bytes unchanged and an absent
quarantine root still absent. If that check succeeds, exact root creation returns
the final-root pinned owner directly to the authority; a second verification runs
immediately before the first rename. Failure at this second check performs zero
renames and honestly receipts the already-created empty job-owned root.

These handles and last-boundary checks do **not** claim immunity from an
adversarial change in the final gap between verification and the NT rename call.
The shared exact pinned no-replace primitive prevents overwriting a destination
or moving a substituted pinned object, but no oplock or child-membership freeze
was introduced or inferred.

## Review-2 TDD and regression evidence

The complete filename, byte length, classification, and SHA-256 inventory is
preserved in `task-7A-review2-evidence-inventory.txt`. Valid focused RED evidence
includes the baseline-policy, collision, authority-lifecycle, borrowed-guard,
canonical-order, stable-name member-identity, partial-tree cleanup, and immutable
repeat failures. The valid native product REDs are
`task-7A-review2-native-focused-03.log` (incompatible double acquisition) and
`task-7A-review2-native-focused-04.log` (admitted/retained order mismatch).

The pure/non-native checkpoint after the final inspection correction was 95/95
in 7.663 seconds, Exit 0, `task-7A-review2-nonnative-06.log` (SHA-256
`8f0d3f5e71e83c6e86070a28b639404d312e1becf4252eefc7f7e5a28cc1d9eb`).

Primary inspection found that the retained replacement helper returned file
names in breadth-first pin-acquisition order, while the established replacement
effect contract requires canonical lexical order. The focused RED
`task-7A-review2-output-order-red-01.log` proves the mismatch with a root sibling
and nested files. The correction sorts only the returned `output_names`; pinned
tree acquisition, identity verification, and mutation order remain unchanged.
`task-7A-review2-output-order-green-02.log` is the final focused pure GREEN for
that contract. The earlier complete suite did not expose the defect because the
direct junction test exercised the retired pathname helper, the service-level
Replace test supplied the canonical mocked return, and the live all-scenario
integration remained correctly skipped without runtime/live opt-ins.

Two genuine outer-call-path tests now prove that `capture_scenario` and
`_perform_recovery_cleanup` route baseline-missing, exact/casefold adoption
collision, and injected post-retention membership races through the real
retained relocation pipeline. The exhaustive direct matrix remains the
all-scenario policy proof; these representative outer tests additionally prove
zero quarantine creation/moves, unchanged source/stage bytes and native
identities, capture never reaching `_finalize_projection`, recovery never
reaching later normalization, and handle closure. Together with the output-order
test they passed 3/3 in 0.274 seconds under `desktop-947h2kl\\red`, Exit 0, in
`task-7A-review2-native-outer-focused-01.log` (SHA-256
`303f302e12bec21f2e0da25be62062bc716ddc0fbfca76458e220e500f9dc0c5`).

The final authoritative affected native set passed 520/520 in 959.363 seconds
with five explicit skips, Exit 0, in
`task-7A-review2-native-affected-03.log` (SHA-256
`a61c172a1e42a12cccb3aa21349995fb34a4b046c3994c3b9cdc1ee470bc1b45`).
The final authoritative complete suite used bundled Python, worktree-owned
TEMP/TMP, only the exact preparation archive/Steam opt-ins, and no runtime/live
opt-ins:

    C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -B -m unittest discover -s tests -v

Its direct durable capture is `task-7A-review2-full-native-02.log` (SHA-256
`84fc9d0530ebde61278d3a6e1748bbbd9249249edd8f3e0584063c0765f08aae`):

    Ran 1158 tests in 1010.802s
    OK (skipped=10)
    Exit 0

The exact four-file production/test manifest is unchanged before and after every
final native stage. The manifest-file SHA-256 is
`b253ac89240be0a617573f7c13f605a92e2e10a17ada792913da45ecb378d3b1`;
the post-run mismatch count is zero. Final scoped and system
Python/PythonW/tar/bsdtar/watcher/child inventories were empty, the temp-root
rename probe passed, and the completed log admitted an exclusive open.

`task-7A-review2-nonnative-05.log`,
`task-7A-review2-native-focused-06.log`,
`task-7A-review2-native-affected-02.log`, and
`task-7A-review2-full-native-01.log` remain valid intermediate GREEN evidence,
but are superseded checkpoints and are not the current authoritative gates.

## Explicit evidence exclusions

Filename wording does not override outcome. The following files are preserved
but excluded from all GREEN and gate claims:

- `task-7A-review2-native-focused-01.log`: test-only `RUN_ID` fixture failure;
- `task-7A-review2-native-focused-02.log`: WindowsApps alias launched no Python;
- `task-7A-review2-nonnative-01.log`: overlong TEMP made the harness inadmissible;
- `task-7A-review2-native-affected-01.log`: the affected harness TEMP prefix was
  itself over budget;
- `task-7A-review2-service-nonnative-01.log`: intermediate failed regression;
- `task-7A-review2-native-focused-03.log` and `-04.log`: valid product REDs, not
  GREEN evidence;
- `task-7A-review2-native-focused-05.log`: historical intermediate GREEN before
  the final source freeze, not the final focused or complete gate;
- `task-7A-self-review-green-01.log`: failed test-only accessor state despite its
  name, superseded by `task-7A-self-review-green-02.log`;
- `task-7A-contract-green-01.log`: three errors despite its name, superseded by
  `task-7A-contract-green-02.log`;
- all earlier diagnostic setup failures described above, including the initial
  watcher Tee failure, sandbox-token watcher diagnostics, the invalid first
  prefix diagnostic, the first publication/input diagnostic harness attempts,
  and intentional diagnostic Exit 1 captures. They remain diagnostic or failed
  evidence only and are not counted as GREEN/gate evidence.

The job-owned empty temp roots `nt\\a2`, `nt\\f2`, `nt\\a3`, `nt\\f3`, and
`nt\\task7a-review2-focused-temp` were verified as direct, non-reparse, empty
directories and removed by exact literal path. A post-removal real-token process
inventory found zero external command lines referencing those paths, and all
five paths are absent. No historical scratch or other temp path was touched.

The ordinary unstaged source/test/report `git diff --check` was clean apart from
the repository's CRLF conversion notices. After adding the complete immutable
evidence package, cached `diff --check` reports trailing whitespace at exactly
`task-7A-review2-baseline-red-01.log:1`,
`task-7A-review2-collision-red-01.log:1`,
`task-7A-review2-collision-red-02.log:1`,
`task-7A-review2-native-focused-01.log:6`,
`task-7A-review2-native-focused-03.log:8`, and
`task-7A-review2-nonnative-01.log:19`. Those six raw test-output lines are
intentionally preserved byte-for-byte, with their sizes and SHA-256 values
verified against the evidence inventory; they are evidence-format exceptions,
not source or test code-quality failures.

This remains implementation-worker evidence awaiting the same independent
reviewer's third decision. No Task 7B, live MO2/game action, original-attempt
action, public-main mutation, or push is authorized by this report.

# Fix round 3 — relocation cleanup ownership — 2026-09-04

## Root cause and bounded repair

The third review found three connected ownership-finalization gaps. First,
`retain_relocation_authority` cleaned only items returned by earlier successful
pin operations. If the current `_pin_tree` or `_pin_object` raised an operative
`JunctionOwnershipError` and an earlier retained item also failed to close, the
cleanup error replaced the current failure and omitted its live pins. The
acquisition cleanup now includes the current error only when it is an actual
`JunctionOwnershipError` or `ExactObjectOwnershipError`, then delegates the
complete set to the existing retained-owner aggregator. Unknown non-owner
exceptions are never treated as cleanup owners.

Second, `_retained_projection_relocation` used to replace an owner-bearing body
failure with `authority.close()`'s ownership error. Finalization now extracts
only a real owner-bearing primary (`JunctionOwnershipError`,
`ExactObjectOwnershipError`, or a `ContainmentStoreOwnershipError`'s exact
ownership), combines it with the authority-close error through the existing
`_close_retained_owners` path, and chains the combined resolvable ownership from
the primary diagnostic. Identical retained objects remain deduplicated by the
existing owner normalizers.

Third, `_perform_recovery_cleanup` caught `JunctionOwnershipError` through its
`ContainmentSafetyError` base and returned an ordinary
`staging-quarantine-failed` blocker. `recover_scenario` could then publish an
ordinary refused recovery while no exception retained the retry authority.
Recovery cleanup now re-raises junction ownership before the ordinary safety
catch. The public receipt wrapper preserves that same operative error, attaches
the merged effect receipt consistently with store ownership errors, and leaves
its `.resolve()` path intact. No recovery or result document is published for
this owner-bearing failure. Normal non-owner recovery refusals keep their prior
blocker/publication behavior.

No ownership model, relocation policy, pathname fallback, or path-budget scope
changed. The preparation-only scope remains `runtimeQualified=false`.

## Strict TDD evidence

The first three-test RED capture, `task-7A-review3-red-01.log` (4,222 bytes,
SHA-256 `4ab8214ab4b9e32a48f88cc053b9b75a6f380fe59f2323806257a716e9c4de03`),
is excluded as a complete RED because the finalizer test's admission double
stopped early with a missing-field `AttributeError`. Its other two failures were
consistent with the defects, but it is retained as harness-correction evidence
only.

After correcting only that double to carry the complete production field shape,
the same command produced the valid RED in `task-7A-review3-red-02.log` (4,557
bytes, SHA-256
`7401b8b0926402f3a4dcc3d34d722082ac2c3ef18826df85d2d924e4e58302f8`):

    C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -B -m unittest -v tests.test_mo2_containment_junction.Mo2ContainmentJunctionTests.test_relocation_acquisition_unions_current_and_prior_cleanup_owners tests.test_mo2_containment_service.Task4ExactEffectsFixTests.test_relocation_finalizer_unions_primary_and_authority_owners tests.test_mo2_containment_service.ContainmentRecoveryTests.test_recovery_rethrows_relocation_ownership_without_publishing_refusal

All three tests failed for their intended missing behavior: the current
acquisition owner was omitted, the primary exact owner was omitted during
authority finalization, and recovery returned rather than re-raising junction
ownership. Exit was 1.

After the minimal production repair, the identical focused command passed 3/3
in 0.041 seconds, Exit 0. Its durable capture is
`task-7A-review3-green-01.log` (728 bytes, SHA-256
`14f6bc8fd48249a6056946d9c720269aeb4185d8ff9f253dba96eb31dc906611`).
The tests prove compound current/prior ownership, primary exact plus local
authority-finalizer ownership, deterministic resolution of every live pin, and
outer recovery refusal to publish ordinary result/recovery evidence.

## Affected native verification and self-review

The exact two affected modules ran under the verified
`desktop-947h2kl\\red` token, worktree-owned `TEMP`/`TMP`, with preparation and
runtime/live archive/Steam opt-ins absent:

    C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -B -m unittest -v tests.test_mo2_containment_junction tests.test_mo2_containment_service

The complete durable capture is `task-7A-review3-affected-01.log` (37,268 bytes,
SHA-256 `c8480530156759d03ccecdff3b1c46e666845f1af92f8324c3a8a6dd373f0488`):

    Ran 180 tests in 10.374s
    OK
    Exit=0

The four exact changed source/test files are recorded before and after in
`task-7A-review3-source-before.txt` and
`task-7A-review3-source-after.txt`; the per-path hashes are identical and the
post-run mismatch count is zero. After exit, scoped
Python/PythonW/tar/bsdtar/ModOrganizer/nxmhandler/worktree-command process count
was zero. The short test temp was empty, passed an exact rename round trip, was
removed, and is absent. The completed log accepted an exclusive read/write open.

Final review found the changes confined to the two ownership finalizers, public
ownership preservation, and their three focused tests. Existing aggregation is
used rather than duplicated; unknown exceptions are not probed as owners; all
operative local and exact owners remain resolvable; ordinary recovery refusal
semantics are unchanged. `git diff --check` is clean apart from the repository's
CRLF notices. Per the coordinator's bounded-gate decision, the complete 1,158-
test suite was not rerun because the change does not expand beyond these two
ownership-finalization modules. The reviewer report-table wording minor remains
ledgered/deferred and was not changed here.

This is implementation-worker evidence pending the same reviewer's next
decision. It grants no Task 7B or live MO2/game authority, and no original
attempt, push, or merge action occurred.
