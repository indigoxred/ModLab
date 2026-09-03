# MO2 Containment Authority Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the retired containment claim with a source-bound, terminal-only, accurately receipted, independently reviewed capability authority that is safe for bridge consumption.

**Architecture:** One canonical Windows exact-object module retains source and destination-parent handles through same-volume no-replace renames and proves post-move identity. Adjudication refuses without writing until all four current scenario results are terminal and deeply bound, then writes a versioned decision whose authority is resolved through immutable retirement, supersession, review, and eligibility records. Service methods return exact effect receipts; read-only commands use a non-creating store. After native verification and the MO2 bytecode capability matrix, all four scenarios are rerun with fresh identities and a separate reviewer must approve the complete graph before eligibility exists.

**Tech Stack:** Python 3.12 standard library, Windows APIs through `ctypes`, frozen dataclasses, strict canonical JSON, existing ModLab containment models/store/service/CLI, `unittest`, retained MO2 2.5.2, and visible computer control only for the explicit live gates.

**Spec:** `docs/superpowers/specs/2026-08-31-guided-mo2-lab-installation-design.md`

## Global Constraints

- Work only in `C:\Users\red\Desktop\Modlab\.worktrees\mo2-guard-bridge-handshake` until reviewed integration.
- Every `python.exe` command below means `C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe` and runs with `-B`.
- Historical run `containment-run:2c6220d4ceae48809653c18d5c74562b` and its decision are retained evidence but are always `RetiredInvalidated`.
- Use protocol version `2`, decision-binding version `2`, publication policy `handle-pinned-no-replace-v2`, fixture version `2`, effect-receipt version `1`, and authority-policy version `1`.
- A replacement decision binds: exact Git commit and tree IDs; a canonical source-artifact manifest ID; all five versions above; exact MO2 version and executable SHA-256; exact mechanism; exact run ID; and an ordered mapping of all four scenario names to result IDs.
- Missing current scenario evidence is an operational refusal: exit `3`, no `decision.json`, and no terminal decision object returned.
- A final `Supported`, `Rejected`, or evidence-complete `Incomplete` decision is immutable and idempotent for identical bytes.
- Every authority-bearing file/tree move uses the one shared retained-handle, same-volume, no-replace primitive. No pathname-only fallback exists.
- Public containment JSON keeps its approved field set. Service-authored effects map into `writtenPaths`, `launchedProcesses`, `sourceChanges`, `gameChanges`, and `productionMo2Changes`.
- `show` and every verifier are physically read-only: an absent validation root remains absent.
- Retirements, supersessions, independent reviews, and eligibility records are strict canonical immutable documents. Authority resolution fails closed on zero, multiple, malformed, redirected, or conflicting eligible records.
- No bridge task consumes the replacement decision until the independent review record and eligibility record both validate.
- Do not push or merge `main` before the replacement authority and review gate pass.

---

### Task 1: Shared retained-handle exact-object rename

**Files:**
- Create: `modlab/platform/__init__.py`
- Create: `modlab/platform/windows_exact_fs.py`
- Modify: `modlab/validation/windows_junction.py`
- Modify: `modlab/validation/windows_watch.py`
- Create: `tests/test_windows_exact_fs.py`
- Modify: `tests/test_mo2_containment_watch.py`
- Modify: `tests/test_mo2_containment_junction.py`

**Interfaces:**
- Consumes: direct Windows source path, direct destination path, required source kind, and a validator callback that runs while the source handle remains retained.
- Produces: `PinnedObject`, `PinnedIdentity(volume_serial, file_id, attributes)`, `pin_direct_object(path, kind)`, and `rename_pinned_no_replace(source, destination, destination_parent)`.

- [ ] **Step 1: Write deterministic substitution and collision tests**

```python
def test_validated_candidate_cannot_be_replaced_before_publication(self):
    candidate = self.root / "candidate.json"
    destination = self.root / "outcome.json"
    candidate.write_bytes(b'{"ok":true}\n')
    pinned = pin_direct_object(candidate, kind="file")
    with self.assertRaises(PermissionError):
        candidate.rename(self.root / "stolen.json")
    rename_pinned_no_replace(pinned, destination, pin_direct_object(self.root, kind="directory"))
    self.assertEqual(b'{"ok":true}\n', destination.read_bytes())
    self.assertEqual(pinned.identity, identity_at_path(destination))

def test_existing_case_insensitive_destination_is_never_replaced(self):
    (self.root / "OUTCOME.JSON").write_bytes(b"existing")
    with self.assertRaises(FileExistsError):
        rename_pinned_no_replace(
            pin_direct_object(self.root / "candidate.json", kind="file"),
            self.root / "outcome.json",
            pin_direct_object(self.root, kind="directory"),
        )
```

Also inject: source pathname replacement at the API boundary; destination-parent reparse replacement; cross-volume identity; wrong object kind; post-move identity mismatch; close failure; file and directory moves; and absence of every `MoveFileExW`/pathname fallback.

- [ ] **Step 2: Run RED**

Run: `python.exe -B -m unittest tests.test_windows_exact_fs tests.test_mo2_containment_watch tests.test_mo2_containment_junction -v`

Expected: FAIL because `modlab.platform.windows_exact_fs` does not exist and outcome publication still closes the validated candidate before moving it.

- [ ] **Step 3: Implement the primitive and migrate existing callers**

Open source with `CreateFileW` using `DELETE|FILE_READ_ATTRIBUTES`, `FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE`, `OPEN_EXISTING`, `FILE_FLAG_OPEN_REPARSE_POINT`, and directory backup semantics when required. Retain the exact source and destination-parent handles. Compare handle volume serials, set `FILE_RENAME_INFO.Flags=0`, call `SetFileInformationByHandle`, update the pinned path, then require the destination path to resolve to the retained identity. Transfer the existing junction implementation to this module and make junction/watch callers import it.

`_publish_outcome_commit()` writes/fsyncs, pins the candidate, reads and validates through that same retained handle, calls `rename_pinned_no_replace()`, and only then closes. Cleanup deletes only through the retained object handle.

- [ ] **Step 4: Run GREEN and commit**

Run the Step 2 command, then the complete native Windows suite. Expected: focused PASS and no regression.

Commit: `fix: publish containment evidence by retained handle`

---

### Task 2: Terminal-only, source-bound decision schema

**Files:**
- Modify: `modlab/validation/mo2_containment_model.py`
- Modify: `modlab/validation/mo2_containment_serialization.py`
- Modify: `modlab/validation/mo2_containment_service.py`
- Modify: `modlab/validation/mo2_containment_store.py`
- Modify: `tests/test_mo2_containment_serialization.py`
- Modify: `tests/test_mo2_containment_service.py`
- Modify: `tests/test_mo2_containment_store.py`

**Interfaces:**
- Produces: `DecisionBindings`, `ContainmentDecisionNotReady`, `source_artifact_id_for(manifest)`, and a version-2 `CapabilityDecision` that embeds every binding.
- Consumes: exactly one valid terminal result/outcome pair for each `ContainmentScenario`.

- [ ] **Step 1: Write failing terminality and binding tests**

```python
def test_early_adjudication_refuses_without_freezing_decision(self):
    fixture = prepared_run_with_results(self.root, completed=("NewFolder",))
    with self.assertRaises(ContainmentDecisionNotReady):
        adjudicate_run(fixture.validation_root, fixture.run_id)
    self.assertFalse(fixture.store.decision_path(fixture.raw_run_id).exists())

def test_decision_binds_complete_corrected_authority(self):
    fixture = completed_four_scenario_run(self.root)
    decision = adjudicate_run(fixture.validation_root, fixture.run_id)
    self.assertEqual(2, decision.binding_version)
    self.assertEqual(fixture.git_commit_id, decision.source_commit_id)
    self.assertEqual(fixture.git_tree_id, decision.source_tree_id)
    self.assertEqual(fixture.source_artifact_id, decision.source_artifact_id)
    self.assertEqual("handle-pinned-no-replace-v2", decision.publication_policy_version)
    self.assertEqual(fixture.four_result_ids, decision.scenario_result_ids)
```

Mutation matrix: omit/duplicate a scenario; nonterminal journal; mismatched outcome/result; wrong source commit/tree/artifact; wrong protocol/publication/fixture/decision-binding version; wrong MO2 version/executable SHA; wrong mechanism; reordered or substituted result ID; bool-as-int; unknown field; noncanonical ID.

- [ ] **Step 2: Run RED**

Run: `python.exe -B -m unittest tests.test_mo2_containment_serialization tests.test_mo2_containment_service tests.test_mo2_containment_store -v`

Expected: early adjudication writes `Incomplete` and the current schema lacks exact bindings.

- [ ] **Step 3: Implement minimal terminal-only adjudication**

First load and deeply validate all four current result/outcome pairs in enumeration order. If any is missing, malformed, nonterminal, duplicated, or mismatched, raise `ContainmentDecisionNotReady` before cohort adjudication and before `write_decision()`. Once complete, compute the final verdict including predecessor/cohort evidence, attach exact bindings, write one immutable decision, and exact-reload it. Repeated identical adjudication is idempotent; different bytes fail.

- [ ] **Step 4: Run GREEN and commit**

Commit: `fix: bind containment decisions to complete corrected evidence`

---

### Task 3: Immutable retirement, supersession, review, and eligibility

**Files:**
- Create: `modlab/validation/mo2_containment_authority.py`
- Modify: `modlab/validation/mo2_containment_store.py`
- Modify: `modlab/validation/mo2_containment_serialization.py`
- Create: `tests/test_mo2_containment_authority.py`
- Modify: `docs/validation/mo2-containment-spike.md`

**Interfaces:**
- Produces: `CapabilityRetirement`, `CapabilitySupersession`, `CapabilityReview`, `CapabilityEligibility`, immutable write/load methods, and `resolve_current_eligible_decision(validation_root)`.
- Resolver accepts no caller-selected run or decision ID.

- [ ] **Step 1: Write failing authority-resolution tests**

```python
def test_historical_decision_is_rejected_even_when_bytes_still_exist(self):
    fixture = authority_fixture(self.root)
    fixture.write_historical_decision()
    fixture.write_retirement(status="RetiredInvalidated")
    with self.assertRaisesRegex(ContainmentAuthorityError, "retired"):
        fixture.resolve_specific(fixture.historical_decision_id)

def test_only_independently_reviewed_replacement_is_eligible(self):
    fixture = authority_fixture(self.root)
    fixture.write_retirement()
    fixture.write_replacement_decision()
    with self.assertRaisesRegex(ContainmentAuthorityError, "review"):
        fixture.resolve_current()
    fixture.write_passing_review()
    fixture.write_supersession()
    fixture.write_eligibility()
    self.assertEqual(fixture.replacement_decision, fixture.resolve_current().decision)
```

Cover two eligible records, eligibility without retirement/supersession, review bound to different bytes, changed source/version/result binding, malformed direct entry, reparse ancestor, caller attempting the old run, and an eligibility record whose decision is later retired.

- [ ] **Step 2: Run RED**

Run: `python.exe -B -m unittest tests.test_mo2_containment_authority -v`

- [ ] **Step 3: Implement canonical authority graph**

Retirement marks the historical decision `RetiredInvalidated` with exact reason codes for the publication, adjudication, and effect-contract defects. Supersession binds old retirement, new decision, all corrected bindings, and review ID. Eligibility binds the supersession and passing independent review. Resolver enumerates only canonical direct immutable records, validates the complete graph, and requires exactly one current eligible leaf.

Put a prominent first-page banner in the historical repository verdict: `RETIRED / INVALIDATED — MUST NOT BE USED AS CAPABILITY AUTHORITY`, including the three reasons and the immutable retirement record ID once generated.

- [ ] **Step 4: Run GREEN and commit**

Commit: `feat: retire and supersede containment authority immutably`

---

### Task 4: Read-only access and service-authored effect receipts

**Files:**
- Modify: `modlab/validation/mo2_containment_store.py`
- Modify: `modlab/validation/mo2_containment_service.py`
- Modify: `modlab/validation/mo2_containment_cli.py`
- Modify: `tests/test_mo2_containment_store.py`
- Modify: `tests/test_mo2_containment_service.py`
- Modify: `tests/test_mo2_containment_cli.py`

**Interfaces:**
- Produces: `ContainmentStore.open_readonly(root)`, `ContainmentEffects`, and service result envelopes carrying exact effects.
- Public JSON fields remain unchanged.

- [ ] **Step 1: Write failing physical-read-only and exact-effect tests**

```python
def test_show_against_absent_workspace_creates_nothing(self):
    workspace = self.root / "absent"
    code, document = invoke_json("show", OLD_RUN, workspace=workspace)
    self.assertEqual(3, code)
    self.assertFalse(workspace.exists())
    self.assertEqual([], document["writtenPaths"])

def test_validate_reports_every_service_authored_effect(self):
    result = self.service.validate_scenario(...)
    document = render_validate(result)
    self.assertCountEqual(map(str, result.effects.written_paths), document["writtenPaths"])
    self.assertIn(f"Watcher PID: {result.effects.watcher_pid}", document["launchedProcesses"])
    self.assertIn(f"MO2 PID: {result.effects.mo2_pid}", document["launchedProcesses"])
```

Cover prepare fixture mutation roots, watcher request/ready/event/outcome/terminal files, monitor and MO2 processes, recovery writes, adjudication refusal with no decision write, final decision write, historical `show` aggregation through every result, and malformed evidence exit `2` without mutation.

- [ ] **Step 2: Run RED**

Run: `python.exe -B -m unittest tests.test_mo2_containment_cli tests.test_mo2_containment_service tests.test_mo2_containment_store -v`

- [ ] **Step 3: Implement non-creating reads and exact effects**

The read-only constructor requires the direct root to exist and never calls mkdir/preparation. Live `show` uses it. Mutating service methods capture every direct write, child mutation root, and launched process when the effect occurs, then return an immutable effect receipt. CLI maps receipts into the existing public fields and aggregates all historical scenario changes in `show`; it never guesses paths.

- [ ] **Step 4: Run GREEN and commit**

Commit: `fix: report exact containment effects without read mutation`

---

### Task 5: Native verification and retired-claim audit

**Files:**
- Modify as failures require: only files from Tasks 1–4.
- Update: `docs/validation/mo2-containment-spike.md`

- [ ] **Step 1: Run focused suites**

Run all tests named in Tasks 1–4 with verbose output. Expected: PASS.

- [ ] **Step 2: Run the complete native Windows suite**

Run: `python.exe -B -m unittest discover -s tests -v`

Expected: zero failures/errors; declared environment-dependent skips only. A restricted-sandbox result is diagnostic, never authoritative for Windows handles/watchers.

- [ ] **Step 3: Audit absence of stale authority**

Search code/docs/commands for the historical run and decision IDs. They may appear only in retirement/supersession tests and the prominent retired verdict. No bridge API/CLI accepts them as authority.

- [ ] **Step 4: Commit any verified corrections**

Commit: `test: verify corrected containment authority offline`

---

### Task 6: Exact MO2 bytecode/no-drift capability matrix

**Files:**
- Create: `modlab/adapters/mo2/bridge_runtime_capability.py`
- Create: `tests/test_mo2_bridge_runtime_capability.py`
- Create after live run: immutable runtime-capability evidence below `workspace/runtime/validation/mo2-bridge-capability/<id>/`

**Interfaces:**
- Produces a strict capability record selecting `SingleFile`, `Package`, or `NotSupported`.

- [ ] **Step 1: Add offline selection tests**

Single-file wins when both layouts pass; package wins only when package passes and single-file fails; no passing layout yields `NotSupported`. A layout passes only if first, reload/second, passive, and guarded observations all preserve its exact declared inventory and produce no undeclared/cache entry.

- [ ] **Step 2: Prepare disposable candidates without touching production target**

Use a fresh disposable exact MO2 2.5.2 copy. Each harmless candidate implements a passive `IPluginTool` and writes only job-local capability evidence when explicitly guarded; it never installs, launches a tool/game, or changes profiles/settings.

Live-evidence amendment (2026-09-03): MO2's normal logging privacy filter transforms username-bearing absolute paths. Use an explicitly versioned V2 runtime marker/schema with runtime-observed app-relative path fields instead. Derive them from actual absolute `sys.executable` and `mobase.__file__` using component-aware containment beneath the exact generated app root; refuse outside/sibling roots, traversal, aliases and unexpected relative names. Require exactly `ModOrganizer.exe` and `plugins/plugin_python/libs/mobase.cp312-win_amd64.pyd`. Preserve the independent exact app-root/native process correlation, runtime content/native identities, generated candidate-byte binding, full no-drift checks and guarded-only file output. Do not de-redact logs, disable privacy filtering, encode around it, add passive file writes, relax identity checks, or reinterpret V1 evidence as V2. Behavioral regressions must execute generated probe code and cover privacy filtering, containment/refusal, strict version parsing and guarded parity. Corrected committed source and its native/review gates must precede a genuinely fresh matrix with fresh controls; the incomplete old-source First observation remains preserved and unaccepted.

- [ ] **Step 3: Execute the four-launch matrix with visible computer control**

For both candidates capture exact pre/post inventory and MO2/Python identity across first import, second/reload, passive launch, and guarded launch. Close MO2 normally. Preserve every attempt. Do not set or trust bytecode environment variables.

- [ ] **Step 4: Write/reload the immutable selection record**

If no layout passes, stop with `NotSupported`. Do not freeze bridge inventory or issue a bridge receipt.

---

### Task 7: Four fresh visible containment scenarios and replacement decision

#### Approved prerequisite repair — 2026-09-03

The user explicitly approved fixing both observed preparation defects, including stable
link/target/parent/volume/file identity, cleanup-only fresh restart, adversarial tests, the
complete native Windows suite, and independent review before consumption or merge. This
amendment is part of Task 7; completed Tasks 1–6 are not silently repeated or relabeled.
The actual failed preparation is `containment-run:7b664285ad8c442d97acde65ce22b65a` at source
`28554debfefe75348b7c8c712eaf976c72b4e5fc`, tree `4c6fac6e520ba35bf5c983bf9359f98b82a84d3c`.
It has an intent and partial NewFolder fixture, but no request, journal, launch, result or
decision. Its evidence must remain immutable; never fill in missing successful history.

**Repair files and responsibilities:**

- Modify `modlab/validation/windows_junction.py`: retain creation-bound projection ownership
  and verify the exact link, target, both direct parents and native volume/file identities.
- Modify `modlab/validation/mo2_containment_fixtures.py`: carry that ownership through the
  fixture operation and its effect observation, including failure unwinding.
- Modify `modlab/validation/mo2_containment_service.py`: narrow expected-projection
  observation; durable early-failure ownership/effects; cleanup-only recovery and fresh-run
  predecessor consumption. Do not restructure unrelated service behavior.
- Create `modlab/validation/mo2_preparation_recovery.py`: focused strict preparation-failure,
  recovery and one-use replacement models/serialization/validation, kept distinct from
  scenario results and capability authority.
- Modify `modlab/validation/mo2_containment_store.py`: immutable canonical preparation
  records and atomic one-use replacement consumption using the existing shared publisher.
- Modify `modlab/validation/mo2_containment_cli.py`: explicit `recover-preparation` and
  `restart-preparation` operator commands; preserve the approved public response field set.
- Test in `tests/test_mo2_containment_junction.py`, `tests/test_mo2_containment_service.py`,
  `tests/test_mo2_containment_store.py`, `tests/test_mo2_containment_cli.py`, and new focused
  `tests/test_mo2_preparation_recovery.py`.

**Required interfaces and invariants:**

1. Provide `create_owned_projection(source_mod: Path, staging_mod: Path)` returning a
   retained projection owner through the existing junction machinery, with explicit `close()`
   and creation-bound identity verification. It must
   retain creation-time link identity, canonical reparse bytes, exact direct target and both
   parent identities until the guarded post-observation completes. An after-the-fact
   pathname/target match is not creation ownership. Preserve existing non-owning callers'
   behavior without inventing a second rename primitive.
2. `_mutation_root_observation(root, *, expected_projections=())` and the delegated boundary
   accept an explicit, default-empty tuple of those owners only for the fixture's actual
   created projections. Generic callers
   continue to reject every reparse. Record an opaque junction row and never descend through
   it. Verify all retained identities before and after observation, including rescans. A
   replaced target/parent/link, same-name different file ID, wrong volume, unknown reparse,
   missing object, payload change or unresolved handle ownership refuses. Never treat failed
   cleanup as success or lose a still-live handle.
3. Preparation failure is a separate lifecycle, never a ScenarioResult or decision. Record
   exact intent/source identity and observed job-owned effects before authorizing later
   cleanup. Bind creation ownership while it is available, rather than reconstructing it
   from path names after a crash. Existing evidence files are never overwritten.
4. `recover_preparation(validation_root: Path, run_id: str)` returns an exact receipted
   `PreparationRecovery` value. It proves all old processes/owners absent using native
   identity-aware observations and the existing command/run exclusion mechanisms. Timeouts,
   PID reuse, access-denied or incomplete process enumeration are not absence. It unwinds
   only retained/proven job-owned transient objects, never game, Play, source payload or
   unrelated state. Shared no-replace exact-object moves may preserve cleanup targets in
   quarantine. Unknown/substituted objects are left untouched and reported; absence of a
   request/journal alone never proves safe cleanup or grants retry.
5. `restart_preparation(validation_root: Path, run_id: str)` explicitly performs/loads that
   cleanup-only recovery, consumes one exact immutable preparation replacement authority,
   and calls the normal preparation path with a fresh run and fresh fixture/session/request
   identities. It never resumes or upgrades the failed attempt. Concurrent/replayed
   consumption cannot create multiple new attempts. Failure while creating the replacement
   leaves that new attempt failed with its own lineage; it does not replenish old authority.
6. Existing scenario recovery stays separate. A prepared/request-bearing run, started
   scenario, launch evidence, inconsistent records, conflicting predecessor or malformed
   record cannot take the early-failure shortcut. Existing `prepare_run` without exact
   replacement authority continues to refuse unresolved same-command predecessors.
7. The older retained intent-only failure lacks new creation receipts. Do not fabricate
   those receipts, retroactively declare observed objects job-owned, or delete/rewrite its
   fixture. Support only a verified, explicitly recorded legacy failure disposition using
   actual existing evidence and native absence proof. Preserve any state whose creation
   ownership cannot be proved. If a safe fresh replacement cannot be proven while preserving
   that state, refuse that operation and report the exact missing proof; do not evade it by
   changing the workspace or command fingerprint. This limitation must be tested and
   disclosed, not hidden behind success for new-format failures only.
8. Every failure/recovery/replacement record binds its exact source intent and bytes, actual
   effects, process proof, and consumed-by fresh run. Canonical reads are non-creating;
   collisions, substitutions, cross-run and cross-command replay refuse. Cleanup grants
   no successful receipt, validation verdict, Keep, bridge or eligibility authority.

- [ ] **Repair step 1: Add failing real-native regressions before production edits**

Use the existing unittest fixture helpers and real retained-handle/junction/store behavior.
The initial integration regression must reach the real guarded post-observer with a real
job-created projection, not a mock replacing `prepare_containment_fixture` with only a
regular-file tree. These concrete native/negative regressions additionally define the new
observation and physically-read-only recovery interfaces:

```python
def test_job_created_projection_is_observed_without_following_target(self):
    with tempfile.TemporaryDirectory(prefix="modlab-owned-link-") as directory:
        root = Path(directory).absolute()
        source = root / "source" / "Protected Existing"
        stage = root / "stage"
        source.mkdir(parents=True)
        stage.mkdir()
        (source / "marker.txt").write_bytes(b"unchanged")
        owner = junction.create_owned_projection(source, stage / "Protected Existing")
        try:
            rows = service._mutation_root_observation(
                root, expected_projections=(owner,)
            )
            self.assertIsNotNone(rows)
            names = {row[0] for row in rows}
            self.assertIn("stage/Protected Existing", names)
            self.assertNotIn("stage/Protected Existing/marker.txt", names)
            self.assertEqual(b"unchanged", (source / "marker.txt").read_bytes())
        finally:
            owner.close()

def test_recover_preparation_refuses_absent_storage_without_creating_it(self):
    with tempfile.TemporaryDirectory(prefix="modlab-preparation-recovery-") as directory:
        absent = Path(directory).absolute() / "absent-validation"
        with self.assertRaises((service.ContainmentServiceError, ContainmentStoreError)):
            service.recover_preparation(absent, "containment-run:" + "a" * 32)
        self.assertFalse(absent.exists())
```

Import `Path`, `tempfile`, the service module, junction module and `ContainmentStoreError`
normally in the test module. Native test scratch belongs under ModLab. Use existing test
cleanup that removes only the exact test-created link, never target traversal. The complete
fresh-attempt regression must run a real controlled preparation failure in a foreground
native child, retain old evidence bytes, prove that child exited, then recover/restart through
the production APIs: assert old evidence byte equality, disjoint old/new generated identities,
no old success authority, and rejection of a second consumption. Keep filesystem effects,
process identity and publication real; replace only slow archive extraction with a controlled
fixture at its actual dependency boundary. Also cover link/target/parent substitution between creation and
observation, payload/type/volume mismatch, unknown links, no target traversal, retained-handle
close failure, genuine delegate exception with partial effects, old processes alive or
uncertain, PID reuse, substituted cleanup objects, interrupted cleanup, duplicate/concurrent
replacement consumption, failure in the fresh replacement, absent root read-only behavior,
prepared/started-run rejection, and the exact old-format intent-only failure shape.

Run focused tests with bundled Python `-B -m unittest <affected modules> -v` natively;
retain actual RED output proving the intended failures, not unrelated import/setup errors.

- [ ] **Repair step 2: Implement only the approved invariants and obtain focused GREEN**

Carry exact projection owners through the real fixture pipeline; add strict early-failure
records, verified cleanup and one-time new preparation. Keep all public effect claims based
on actual operation receipts, including failures. Preserve all original failed-run artifacts.
Run each new regression and existing affected junction/service/store/CLI tests; report every
skip and unresolved limitation. No live MO2 launch or real failed-run cleanup during coding.

- [ ] **Repair step 3: Complete native suite, commit and independent repair review**

Run the complete native Windows suite once on the final repair, preserving stdout/stderr,
exit code, counts and declared skip reasons. Use the existing configured native runner if
needed for equivalent full discovery. Check the tracked diff for unrelated changes, then
commit the repair and test evidence summary. The independent reviewer receives the entire
repair diff from the pre-repair BASE, these user requirements, the diagnostic and RED/GREEN/
full-suite evidence, and verifies both spec compliance and quality. Fix and re-review actual
findings before consuming this repair. No main merge/push, live recovery/restart, fresh
containment decision, eligibility or bridge receipt before the gate passes.

- [ ] **Repair step 4: Review source-bound evidence implications before returning to live work**

Keep the existing Task 6 capability record at its original commit/tree; do not edit its
source fields or call it fresh repaired-head evidence. Independently determine required
revalidation for any exercised code changed by this repair. The four containment scenarios
and their eventual decision must be genuinely fresh on the repaired, reviewed source. Only
then operate the supported cleanup/restart command against the retained failure, preserving
all refusals and real effects. Resume the following original Task 7 steps in order.

**Files:**
- Create after live run: fresh evidence below `workspace/runtime/validation/mo2-containment/<new-run>/`
- Create: `docs/validation/mo2-containment-replacement.md`

- [ ] **Step 1: Prepare a new run after all offline gates pass**

Require a clean committed corrected source tree. Record commit/tree/source-artifact IDs, all policy versions, exact MO2 executable/version, mechanism, fixture identity, and fresh run/session/request IDs.

- [ ] **Step 2: Run NewFolder, MergeExisting, ReplaceExisting, and FomodDependency**

Use computer control only for the exact visible procedures. Every scenario gets fresh evidence, fixture, outcome, and result IDs. Preserve failures; never overwrite/reuse historical evidence.

- [ ] **Step 3: Adjudicate only after all four terminal results exist**

The decision must exact-reload and contain the ordered four-result map plus every corrected binding. Write retirement/supersession records; do not write eligibility yet.

- [ ] **Step 4: Write the sanitized replacement verdict**

State exact inspected coverage and keep archive installation unavailable pending review.

---

### Task 8: Independent replacement-decision review and eligibility

**Files:**
- Create: `docs/validation/mo2-containment-replacement-review.md`
- Create in runtime store: immutable review and eligibility documents.

- [ ] **Step 1: Dispatch a fresh read-only reviewer**

The reviewer receives the corrected commit/tree, complete test evidence, runtime-capability record, all four scenario graphs, replacement decision, retirement, and supersession. It must verify every binding and effect claim, inspect no unrelated secrets, and issue `Passed` or `Failed`.

- [ ] **Step 2: Write eligibility only after a passing review**

A failed/missing/ambiguous review leaves zero eligible decisions. A passing review is canonicalized, content-addressed, exact-reloaded, then bound by the one eligibility record.

- [ ] **Step 3: Prove bridge consumption is gated**

Run a focused bridge preflight test showing the old decision, new unreviewed decision, or caller-selected run is rejected; only `resolve_current_eligible_decision()` succeeds.

- [ ] **Step 4: Run final native suite and commit**

Commit: `test: qualify replacement containment authority`

## Self-review Checklist

- Historical authority is mechanically retired, not merely undocumented.
- Decision bindings include source commit/tree/artifact, protocol/publication/fixture/binding/effect/authority versions, exact MO2/mechanism, and four result IDs.
- No missing scenario can create `decision.json`.
- One shared retained-handle rename primitive covers every authority-bearing host move with no pathname fallback.
- Read-only commands create no directories or files.
- Public effects come from service receipts and preserve the approved JSON field set.
- Bytecode layout is selected only after first/reload/passive/guarded proof.
- Four fresh scenarios and decision identities are created after corrected code is committed.
- A fresh independent review precedes eligibility and bridge consumption.
