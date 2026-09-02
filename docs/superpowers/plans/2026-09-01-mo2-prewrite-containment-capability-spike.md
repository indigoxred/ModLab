# MO2 Pre-write Containment Capability Spike Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove or reject a pre-write-safe MO2 2.5.2 installation mechanism before ModLab is allowed to install an archive into the production shared mod store.

**Architecture:** Build an opt-in validation harness around a disposable source MO2 instance and a separate low-integrity staging instance. Existing source mods are projected into staging as read-only directory junctions, MO2 runs with a low-integrity token and contained writable roots, and only one verified non-colliding real staging folder may be adopted afterward; mutation monitors and immutable manifests make the verdict evidence-based rather than inferred from the final state.

**Tech Stack:** Python 3.12 standard library, frozen dataclasses, strict canonical JSON, `ctypes` Windows token/file APIs, NTFS reparse points, `ReadDirectoryChangesW`, `subprocess` argument arrays, ZIP/FOMOD fixtures, existing ModLab MO2 bootstrap/vault code, `unittest`, and computer control for the four visible MO2 installer scenarios.

**Spec:** `docs/superpowers/specs/2026-08-31-guided-mo2-lab-installation-design.md`

## Global Constraints

- Execute this plan in an isolated worktree created at execution time with `superpowers:using-git-worktrees`; suggested branch `feature/mo2-containment-spike` and path `C:\Users\red\Desktop\Modlab\.worktrees\mo2-containment-spike`.
- Read the complete approved spec before Task 1 and treat it as authoritative.
- Python 3.12 standard library only; add no package-manager or runtime dependency.
- Use the bundled interpreter exactly: `C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`, always with `-B`; the isolated shell does not provide `python` on `PATH`.
- The behavior target is the exact retained portable MO2 `2.5.2` / `ModOrganizer.exe` `2.5.2.0` build.
- The spike may return `Supported`, `Rejected`, or `Incomplete`; it must never promote uncertainty to `Supported`.
- Every source instance, stage instance, archive fixture, monitor log, ACL/integrity record, receipt, and quarantine directory stays beneath `workspace/runtime/validation/mo2-containment/`.
- The first live run uses only disposable validation instances. It never points a junction, mutation monitor, integrity operation, or adoption operation at the managed production MO2 instance.
- Steam and the actual Skyrim game are read-only. Skyrim, SKSE, xEdit, LOOT, and every configured executable remain unlaunched.
- Every pre-existing source mod folder, source Play/Lab profile, source download, source Overwrite entry, and bounded game file must remain byte-identical throughout every MO2-controlled phase. New-folder and FOMOD scenarios may perform the separately evidenced post-MO2 adoption of one collision-free sibling into the disposable source store, then move that sibling to quarantine; they may never alter or reconstruct an existing source folder.
- Low-integrity staging is intentional and confined. Source and game paths must be Medium integrity or higher; a Low or unprovable source integrity level blocks the scenario.
- Only direct, non-reparse validation roots are accepted. The only allowed reparse points are harness-created top-level staging mod projections whose exact targets and reparse payloads are recorded.
- No scenario recursively deletes data. Failed or extra staging content is moved to the run's quarantine after MO2 exits and after evidence capture.
- The candidate passes only if forced Merge and Replace cannot mutate the source folder, create a production backup, or require reconstruction; new-folder install and FOMOD dependency visibility must also succeed.
- Existing payload projection must scale with the number of mod directories and declared metadata, not their total bytes; no full payload copy is allowed.
- A successful adoption accepts exactly one new direct regular directory, rejects collisions case-insensitively, rejects reparse content, normalizes its integrity to Medium, atomically moves it within the validation volume, and verifies the same tree hash afterward.
- All subprocesses use `shell=False` and argument arrays. Every launched executable, PID, integrity level, argument, working directory, and purpose is retained in evidence.
- The normal ModLab `skyrim mod install` command remains absent after this plan. A `Supported` receipt authorizes only the next implementation plan.

## Scope Decomposition

This is the first of several dependency-ordered implementation plans. It implements only the architecture's mandatory capability gate. A `Supported` result selects the isolated low-integrity staging mechanism for the next bridge/lifecycle plan; a `Rejected` or `Incomplete` result keeps direct production installation unsupported and requires a separate alternative-containment design before downstream installer work.

## File Structure

### New production-independent validation modules

- `modlab/validation/__init__.py` — validation package marker; exports no public end-user API.
- `modlab/validation/mo2_containment_model.py` — immutable scenario, journal, evidence, result, and decision values.
- `modlab/validation/mo2_containment_serialization.py` — exact schemas, canonical JSON, IDs, and round trips.
- `modlab/validation/windows_integrity.py` — Windows integrity inspection, low-integrity staging labels, and low-token process launch.
- `modlab/validation/windows_junction.py` — exact directory-junction creation, inspection, and target verification.
- `modlab/validation/windows_watch.py` — loss-detecting recursive mutation monitor using `ReadDirectoryChangesW`.
- `modlab/validation/mo2_containment_fixtures.py` — disposable source/stage instances and deterministic ZIP/FOMOD archives.
- `modlab/validation/mo2_containment_store.py` — confined atomic run/journal/result/decision storage and recovery records.
- `modlab/validation/mo2_containment_service.py` — prepare, arm, launch, capture, recover, adopt, quarantine, and adjudicate orchestration.
- `modlab/validation/mo2_containment_cli.py` — opt-in validation-only command boundary and plain-language runbook output.

### Modified files

- `modlab/workspace.py` — validation-root layout only; no public install paths.
- `README.md` — developer-facing containment-spike command sequence and explicit non-production warning.
- `docs/validation/mo2-containment-spike.md` — final sanitized machine verdict after visible validation.

### Tests

- `tests/test_mo2_containment_serialization.py`
- `tests/test_mo2_containment_integrity.py`
- `tests/test_mo2_containment_junction.py`
- `tests/test_mo2_containment_watch.py`
- `tests/test_mo2_containment_fixtures.py`
- `tests/test_mo2_containment_store.py`
- `tests/test_mo2_containment_service.py`
- `tests/test_mo2_containment_cli.py`
- `tests/test_mo2_containment_windows_integration.py`
- `tests/support/mo2_containment.py`

---

### Task 1: Immutable capability records and strict serialization

**Files:**
- Create: `modlab/validation/__init__.py`
- Create: `modlab/validation/mo2_containment_model.py`
- Create: `modlab/validation/mo2_containment_serialization.py`
- Create: `tests/test_mo2_containment_serialization.py`

**Interfaces:**
- Consumes: normalized evidence values only; no filesystem access.
- Produces: `ContainmentScenario`, `ScenarioState`, `ScenarioOutcome`, `WatchEvidenceCompletion`, `ScenarioCleanupStatus`, `CapabilityVerdict`, `IntegrityObservation`, `TreeIdentity`, `ProtectedState`, `WatcherEvent`, `ProcessEvidence`, `ScenarioJournal`, `WatchOutcome`, `ScenarioResult`, `ScenarioRecovery`, `CapabilityDecision`, `scenario_journal_to_bytes()`, `scenario_journal_from_bytes()`, `watch_outcome_to_bytes()`, `watch_outcome_from_bytes()`, `watch_outcome_id_for()`, `scenario_result_to_bytes()`, `scenario_result_from_bytes()`, `scenario_recovery_to_bytes()`, `scenario_recovery_from_bytes()`, `capability_decision_to_bytes()`, `capability_decision_from_bytes()`, `scenario_result_id_for()`, and `capability_decision_id_for()`.

- [ ] **Step 1: Write failing round-trip and rejection tests**

```python
class Mo2ContainmentSerializationTests(unittest.TestCase):
    def test_scenario_result_round_trips_canonically(self):
        watch_outcome = valid_watch_outcome(ContainmentScenario.MERGE_EXISTING)
        value = valid_scenario_result(ContainmentScenario.MERGE_EXISTING, watch_outcome)
        encoded = scenario_result_to_bytes(value, watch_outcome)
        self.assertEqual(value, scenario_result_from_bytes(encoded, watch_outcome))
        self.assertEqual(encoded, scenario_result_to_bytes(value, watch_outcome))
        self.assertEqual(
            f"containment-result-sha256:{hashlib.sha256(encoded).hexdigest()}",
            scenario_result_id_for(value, watch_outcome),
        )

    def test_unknown_duplicate_unsafe_and_impossible_values_are_rejected(self):
        for data, message in invalid_containment_documents():
            with self.subTest(message=message):
                with self.assertRaisesRegex(ContainmentFormatError, message):
                    scenario_result_from_bytes(data, valid_watch_outcome())
```

`invalid_containment_documents()` must include duplicate JSON keys, an extra field, an absolute `relativePath`, mixed-case SHA-256, duplicate watcher sequence numbers, `Passed` with a mutation event, `Passed` with a changed protected hash, `Captured` without after evidence, `Supported` without four passing scenarios, and mismatched run/scenario IDs.

- [ ] **Step 2: Run the focused test and verify failure**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_containment_serialization -v`

Expected: FAIL because `modlab.validation.mo2_containment_model` does not exist.

- [ ] **Step 3: Add the exact immutable types**

```python
class ContainmentScenario(StrEnum):
    NEW_FOLDER = "NewFolder"
    MERGE_EXISTING = "MergeExisting"
    REPLACE_EXISTING = "ReplaceExisting"
    FOMOD_DEPENDENCY = "FomodDependency"

class ScenarioState(StrEnum):
    PREPARED = "Prepared"
    ARMED = "Armed"
    SCENARIO_STARTED = "ScenarioStarted"
    LAUNCHED = "Launched"
    CAPTURED = "Captured"
    RECOVERY_REQUIRED = "RecoveryRequired"

class ScenarioOutcome(StrEnum):
    PASSED = "Passed"
    FAILED = "Failed"
    INCOMPLETE = "Incomplete"

class WatchEvidenceCompletion(StrEnum):
    COMPLETED = "Completed"
    INCOMPLETE = "Incomplete"

class ScenarioCleanupStatus(StrEnum):
    SUCCEEDED = "Succeeded"
    REFUSED = "Refused"

class CapabilityVerdict(StrEnum):
    SUPPORTED = "Supported"
    REJECTED = "Rejected"
    INCOMPLETE = "Incomplete"

class IntegrityObservation(StrEnum):
    UNTRUSTED = "Untrusted"
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    SYSTEM = "System"
    UNKNOWN = "Unknown"

@dataclass(frozen=True)
class TreeIdentity:
    sha256: str
    regular_file_count: int
    directory_count: int
    total_size: int

@dataclass(frozen=True)
class WatcherEvent:
    sequence: int
    root_kind: str
    action: str
    relative_path: str

@dataclass(frozen=True)
class WatchOutcome:
    schema_version: int
    request_id: str
    request_sha256: str
    session_id: str
    run_id: str
    scenario: ContainmentScenario
    controller_pid: int
    controller_creation_time: int
    worker_pid: int
    worker_creation_time: int
    evidence_completion: WatchEvidenceCompletion
    worker_exit_code: int | None
    ready: bool
    opened_root_kinds: tuple[str, ...]
    events: tuple[WatcherEvent, ...]
    event_bytes_sha256: str
    journal_volume_serial: int
    journal_file_id: int
    journal_byte_count: int
    journal_event_count: int
    journal_final_sequence: int
    terminal_bytes_sha256: str
    root_identities_unchanged: bool
    reason_codes: tuple[str, ...]

@dataclass(frozen=True)
class ProtectedState:
    source_mods: TreeIdentity
    lab_profile_sha256: str
    play_profile_sha256: str
    downloads: TreeIdentity
    overwrite: TreeIdentity
    bounded_game: TreeIdentity

@dataclass(frozen=True)
class ProcessEvidence:
    pid: int
    executable: str
    executable_version: str
    arguments: tuple[str, ...]
    working_directory: str
    integrity: IntegrityObservation

@dataclass(frozen=True)
class ScenarioJournal:
    schema_version: int
    run_id: str
    scenario: ContainmentScenario
    state: ScenarioState
    source_root: str
    stage_root: str
    archive_path: str
    protected_mod_name: str
    expected_new_mod_name: str
    protected_before: ProtectedState
    monitor_pid: int | None
    mo2_pid: int | None
    error: str | None

@dataclass(frozen=True)
class ScenarioResult:
    schema_version: int
    run_id: str
    scenario: ContainmentScenario
    outcome: ScenarioOutcome
    protected_before: ProtectedState
    protected_after: ProtectedState
    mo2_process: ProcessEvidence | None
    source_integrity: IntegrityObservation
    stage_integrity: IntegrityObservation
    watch_outcome_id: str
    watch_evidence_completion: WatchEvidenceCompletion
    scenario_started: bool
    fresh_retry_eligible: bool
    watcher_events: tuple[WatcherEvent, ...]
    projection_count: int
    projection_targets_verified: bool
    projection_observation_complete: bool
    projection_payload_bytes_copied: int
    production_backup_names: tuple[str, ...]
    production_observation_complete: bool
    staging_new_names: tuple[str, ...]
    staging_observation_complete: bool
    staging_output_names: tuple[str, ...]
    output_observation_complete: bool
    adopted_name: str | None
    adopted_tree: TreeIdentity | None
    adopted_integrity: IntegrityObservation | None
    source_restored_after_quarantine: bool
    reasons: tuple[str, ...]

@dataclass(frozen=True)
class ScenarioRecovery:
    schema_version: int
    run_id: str
    scenario: ContainmentScenario
    journal_id: str
    result_id: str | None
    cleanup_status: ScenarioCleanupStatus
    fresh_run_permitted: bool
    blockers: tuple[str, ...]

@dataclass(frozen=True)
class CapabilityDecision:
    schema_version: int
    run_id: str
    mechanism: str
    verdict: CapabilityVerdict
    scenario_result_ids: tuple[str, ...]
    reasons: tuple[str, ...]
```

Use `mechanism="isolated-low-integrity-junction-projection-v1"`. Strictly require all four scenarios in enum order for `Supported`. Every passing result requires an exact content-addressed `WatchOutcome` bound to the same request, session, run, and scenario; identical `protected_before`/`protected_after`; `Completed` watcher evidence with no event; `scenario_started=True`; `fresh_retry_eligible=False`; exact MO2 version `2.5.2.0`; a Low MO2 process and Low stage; Medium-or-higher source; verified projections with zero copied payload bytes; no production backup; and a restored source root after any adoption/quarantine. New-folder and FOMOD results additionally require the exact adopted name, a non-null tree, Medium adopted integrity, and exact expected staging outputs. The four observation-completeness fields distinguish a producer that definitely inspected projection, production, staging, or output state from one whose access/identity proof failed; only a complete category may turn its typed values into deterministic failure proof, and `Passed` requires every applicable category complete. A `Failed` result requires durable `ScenarioStarted` plus positive proof consisting of a bound watcher event, a protected-state delta, or `Completed` watcher evidence together with a deterministic typed policy violation recomputed from the result fields; `Incomplete` watcher evidence permits `Failed` only for the event/delta cases. A result without one of those proofs, including missing/malformed/incomplete watcher evidence, unknown process state, arbitrary reason text, or unresolved cleanup/adoption evidence, is `Incomplete`; later cleanup alone never promotes it to `Passed` or `Failed`.

- [ ] **Step 4: Implement strict canonical JSON**

Use the repository's exact-field, duplicate-key-rejecting, bool-not-int, lowercase-SHA, sorted-array, and `json.dumps(..., sort_keys=True, separators=(",", ":"))` patterns. Reject paths containing an empty, `.`, `..`, absolute, drive, UNC, slash-confused, quote, control, or reserved-device segment. Content IDs are SHA-256 of canonical bytes and are recomputed during every load.

- [ ] **Step 5: Run focused tests**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_containment_serialization -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add modlab/validation tests/test_mo2_containment_serialization.py
git commit -m "feat: add containment capability evidence contracts"
```

---

### Task 2: Windows integrity-level process boundary

**Files:**
- Create: `modlab/validation/windows_integrity.py`
- Create: `tests/test_mo2_containment_integrity.py`

**Interfaces:**
- Consumes: direct validation paths and exact executable/argument/environment values.
- Produces: `IntegrityLevel`, `ProcessLaunch`, `inspect_path_integrity(path: Path) -> IntegrityLevel`, `set_low_integrity_tree(path: Path) -> None`, `set_medium_integrity_tree(path: Path) -> None`, `launch_low_integrity_process(executable: Path, args: tuple[str, ...], cwd: Path, environment: Mapping[str, str]) -> ProcessLaunch`, and `inspect_process_integrity(pid: int) -> IntegrityLevel`.

- [ ] **Step 1: Write failing pure and Windows integration tests**

```python
class IntegrityPolicyTests(unittest.TestCase):
    def test_source_must_be_medium_or_higher_and_stage_must_be_low(self):
        self.assertTrue(source_integrity_allowed(IntegrityLevel.MEDIUM))
        self.assertTrue(source_integrity_allowed(IntegrityLevel.HIGH))
        self.assertFalse(source_integrity_allowed(IntegrityLevel.LOW))
        self.assertTrue(stage_integrity_allowed(IntegrityLevel.LOW))
        self.assertFalse(stage_integrity_allowed(IntegrityLevel.MEDIUM))

@unittest.skipUnless(os.name == "nt", "Windows integrity APIs are unavailable")
class WindowsIntegrityTests(unittest.TestCase):
    def test_low_process_can_write_low_stage_but_not_medium_source(self):
        result = run_integrity_probe_in_temporary_directory()
        self.assertEqual(IntegrityLevel.LOW, result.process_integrity)
        self.assertEqual(b"stage-write", result.stage_bytes)
        self.assertEqual(b"source-original", result.source_bytes)
        self.assertEqual("PermissionError", result.source_write_error)
```

- [ ] **Step 2: Run and verify failure**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_containment_integrity -v`

Expected: FAIL because the integrity module does not exist.

- [ ] **Step 3: Implement integrity inspection and labels**

Define exact RIDs and results:

```python
class IntegrityLevel(IntEnum):
    UNTRUSTED = 0x0000
    LOW = 0x1000
    MEDIUM = 0x2000
    HIGH = 0x3000
    SYSTEM = 0x4000

@dataclass(frozen=True)
class ProcessLaunch:
    pid: int
    executable: str
    arguments: tuple[str, ...]
    working_directory: str
    integrity: IntegrityLevel
```

Use `GetNamedSecurityInfoW`/mandatory-label SACL inspection for paths and `OpenProcessToken` plus `GetTokenInformation(TokenIntegrityLevel)` for processes. Apply Low or Medium labels with an exact `C:\Windows\System32\icacls.exe` argument array and verify the result through the native inspector; a zero exit code without the expected observed label is failure. Record the `icacls` path, arguments, exit code, stdout SHA-256, and stderr SHA-256 in the caller's evidence.

- [ ] **Step 4: Implement low-token launch**

Open the current primary token, duplicate it as `TokenPrimary` with `DuplicateTokenEx`, pass that duplicate to `CreateRestrictedToken(DISABLE_MAX_PRIVILEGE)`, set the restricted primary token's `TokenIntegrityLevel` to Low, and call `CreateProcessAsUserW` with a Unicode environment block, `CREATE_UNICODE_ENVIRONMENT`, and an explicit command line built with `subprocess.list2cmdline`. Using the restricted form of the caller's own token is mandatory so the standard interactive account does not depend on `SeAssignPrimaryTokenPrivilege`. Immediately inspect the child token and terminate only the just-created child if it is not Low before returning control. Close every token/process/thread handle on every branch. Never use `ShellExecute`, `cmd /c`, `powershell`, or `shell=True` for process launch.

- [ ] **Step 5: Run focused tests**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_containment_integrity -v`

Expected: PASS on Windows; pure policy tests pass and native tests skip elsewhere.

- [ ] **Step 6: Commit**

```powershell
git add modlab/validation/windows_integrity.py tests/test_mo2_containment_integrity.py
git commit -m "feat: add low-integrity validation process boundary"
```

---

### Task 3: Exact staging junction projection and safe adoption

**Files:**
- Create: `modlab/validation/windows_junction.py`
- Create: `tests/test_mo2_containment_junction.py`

**Interfaces:**
- Consumes: one direct source `mods` root and one direct low-integrity staging `mods` root, both beneath the same validation run.
- Produces: `JunctionEvidence`, `AdoptionEvidence`, `ReplacementQuarantineEvidence`, `create_mod_projection(source_mod: Path, staging_mod: Path) -> JunctionEvidence`, `inspect_junction(path: Path) -> JunctionEvidence`, `build_projection(source_mods: Path, stage_mods: Path) -> tuple[JunctionEvidence, ...]`, `adopt_unique_staged_mod(stage_mods: Path, source_mods: Path, expected_name: str, before_names: tuple[str, ...], quarantine_root: Path) -> AdoptionEvidence`, `ensure_direct_subdirectory(parent: Path, name: str) -> Path`, `quarantine_exact_object(source: Path, quarantine_root: Path) -> Path`, and `quarantine_replacement_tree(stage_mods: Path, expected_name: str, quarantine_root: Path) -> ReplacementQuarantineEvidence`.

Use these exact evidence values:

```python
@dataclass(frozen=True)
class JunctionEvidence:
    link_path: Path
    target_path: Path
    substitute_name: str
    print_name: str
    reparse_tag: int
    reparse_payload_sha256: str

@dataclass(frozen=True)
class AdoptionEvidence:
    adopted_name: str
    destination_path: Path
    before_tree: TreeIdentity
    after_tree: TreeIdentity
    final_integrity: IntegrityLevel
    final_is_reparse: bool
```

- [ ] **Step 1: Write failing junction and adoption tests**

```python
@unittest.skipUnless(os.name == "nt", "NTFS junction tests require Windows")
class Mo2ContainmentJunctionTests(unittest.TestCase):
    def test_projection_reads_source_without_copying_payload(self):
        fixture = make_junction_fixture()
        evidence = create_mod_projection(fixture.source_mod, fixture.stage_mod)
        self.assertEqual(fixture.source_mod.resolve(), evidence.target_path)
        self.assertEqual(b"source", (fixture.stage_mod / "marker.txt").read_bytes())
        self.assertEqual(IO_REPARSE_TAG_MOUNT_POINT, evidence.reparse_tag)

    def test_adoption_rejects_collision_reparse_and_extra_folder(self):
        for mutation, message in invalid_adoption_cases():
            with self.subTest(message=message):
                with self.assertRaisesRegex(ContainmentSafetyError, message):
                    mutation()

    def test_adoption_moves_one_medium_integrity_directory_with_same_hash(self):
        result = adopt_fixture()
        self.assertEqual(result.before_tree, result.after_tree)
        self.assertEqual(IntegrityLevel.MEDIUM, result.final_integrity)
        self.assertFalse(result.final_is_reparse)
```

- [ ] **Step 2: Run and verify failure**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_containment_junction -v`

Expected: FAIL because the junction module does not exist.

- [ ] **Step 3: Implement junction creation and inspection**

Create a Windows mount-point reparse point directly with `CreateFileW(..., FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS)` and `DeviceIoControl(FSCTL_SET_REPARSE_POINT)`. Store the exact substitute name (`\??\C:\...`) and print name in the mount-point reparse buffer. `inspect_junction()` must read `FSCTL_GET_REPARSE_POINT`, require `IO_REPARSE_TAG_MOUNT_POINT`, decode both names, resolve the target without following any other reparse component, and compare it case-insensitively with the requested direct source directory.

`build_projection()` sorts source top-level directories case-insensitively, rejects duplicate/colliding names and all source reparse points, creates exactly one staging junction per source directory, and verifies every reparse payload after creation. It copies no payload byte.

- [ ] **Step 4: Implement adoption**

```python
def adopt_unique_staged_mod(
    *,
    stage_mods: Path,
    source_mods: Path,
    expected_name: str,
    before_names: tuple[str, ...],
    quarantine_root: Path,
) -> AdoptionEvidence:
    candidate = select_unique_new_directory(stage_mods, before_names, expected_name)
    reject_reparse_tree(candidate)
    before_tree = stable_tree_identity(candidate, required_equal_passes=2)
    set_medium_integrity_tree(candidate)
    require_tree_integrity(candidate, IntegrityLevel.MEDIUM)
    destination = collision_free_destination(source_mods, expected_name)
    rename_no_replace_same_volume(candidate, destination)
    after_tree = stable_tree_identity(destination, required_equal_passes=2)
    if after_tree != before_tree:
        quarantine_after_failed_adoption(destination, quarantine_root)
        raise ContainmentSafetyError("adopted tree identity changed")
    return adoption_evidence(destination, before_tree, after_tree)
```

`select_unique_new_directory()` requires MO2 to be closed, exactly one new top-level staging entry, the exact case-sensitive expected name, no case-insensitive source collision, and a direct regular directory. `reject_reparse_tree()` opens every descendant without following reparse points and rejects any nonzero reparse tag. `rename_no_replace_same_volume()` requires equal volume serial numbers and uses a no-replace rename so a race-created destination fails closed. `quarantine_after_failed_adoption()` uses the same no-replace rename into the run quarantine and never deletes recursively. Any unexpected staging folder, backup, replaced projection, or changed source junction is quarantined and prevents adoption.

- [ ] **Step 5: Run focused tests**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_containment_junction -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add modlab/validation/windows_junction.py tests/test_mo2_containment_junction.py
git commit -m "feat: add isolated mod projection and adoption proof"
```

---

### Task 4: Loss-detecting source mutation monitor

**Files:**
- Create: `modlab/validation/windows_watch.py`
- Create: `tests/test_mo2_containment_watch.py`

**Interfaces:**
- Consumes: direct Medium-or-higher source directories and a contained evidence directory.
- Produces: `WatchRequest`, `WatchReceipt`, an immutable `WatchOutcome`, `start_watch(request: WatchRequest) -> int`, `run_watch_worker(request_path: Path) -> int`, `stop_watch(request_path: Path) -> WatchReceipt`, and `watch_receipt_from_files(request: WatchRequest, worker_pid: int, ready_path: Path, events_path: Path, terminal_path: Path) -> WatchReceipt`.

Watcher-controller ownership recovery is evidence-only and never restores a scenario to success. The former recoverable-owner-state path is replaced by [docs/superpowers/plans/2026-09-01-fail-closed-containment-watch.md](2026-09-01-fail-closed-containment-watch.md) and [docs/superpowers/specs/2026-09-01-fail-closed-containment-watch-design.md](../specs/2026-09-01-fail-closed-containment-watch-design.md): interruption remains incomplete unless Task 6 captures independent positive breach evidence, and cleanup cannot promote a prior run.

Use these exact worker-boundary values:

```python
@dataclass(frozen=True)
class WatchRoot:
    root_kind: str
    path: Path
    volume_serial: int
    file_id: int

@dataclass(frozen=True)
class WatchRequest:
    request_id: str
    session_id: str
    run_id: str
    scenario: ContainmentScenario
    evidence_root: Path
    stop_token_path: Path
    roots: tuple[WatchRoot, ...]

@dataclass(frozen=True)
class WatchReceipt:
    request_id: str
    session_id: str
    run_id: str
    scenario: ContainmentScenario
    worker_pid: int
    evidence_completion: WatchEvidenceCompletion
    ready: bool
    opened_root_kinds: tuple[str, ...]
    events: tuple[WatcherEvent, ...]
    event_bytes_sha256: str
    worker_exit_code: int | None
    watch_outcome_id: str | None
    request_bytes_sha256: str
    error: str | None

    @property
    def complete(self) -> bool:
        return self.evidence_completion is WatchEvidenceCompletion.COMPLETED
```

`WatchReceipt.complete` is a derived compatibility property, never a serialized or copied success authority. The authoritative result is the exact immutable `WatchOutcome` whose content ID equals `watch_outcome_id`; a receipt must carry the outcome's request/session/run/scenario bindings and `evidence_completion`. A receipt with no valid outcome ID is incomplete, never successful.

Allow exactly these `root_kind` values: `SourceMods`, `LabProfile`, `PlayProfile`, `Downloads`, `Overwrite`, `BoundedGame`, `ExternalLocalLow`, and `ExternalTempLow`. Each event copies the originating root kind into `WatcherEvent.root_kind`. The final two watchers cover the current account's native LocalLow and Low temporary roots after path-identity de-duplication; any event there makes the scenario non-passing because child-generated state must stay inside ModLab.

- [ ] **Step 1: Write failing monitor tests**

```python
@unittest.skipUnless(os.name == "nt", "ReadDirectoryChangesW requires Windows")
class MutationWatchTests(unittest.TestCase):
    def test_recursive_create_write_rename_and_delete_are_ordered(self):
        receipt = run_watch_mutation_fixture()
        outcome = load_exact_watch_outcome(receipt.watch_outcome_id)
        self.assertEqual(receipt.watch_outcome_id, watch_outcome_id_for(outcome))
        self.assertEqual(WatchEvidenceCompletion.COMPLETED, receipt.evidence_completion)
        self.assertTrue(receipt.complete)
        self.assertEqual(
            ("Added", "Modified", "RenamedOld", "RenamedNew", "Removed"),
            tuple(event.action for event in receipt.events),
        )

    def test_overflow_worker_death_and_unclosed_handle_are_incomplete(self):
        for fixture, message in incomplete_watch_cases():
            with self.subTest(message=message):
                receipt = fixture()
                self.assertEqual(
                    WatchEvidenceCompletion.INCOMPLETE,
                    receipt.evidence_completion,
                )
                self.assertFalse(receipt.complete)
                self.assertIn(message, receipt.error)

    def test_receipt_completion_is_derived_from_bound_outcome(self):
        receipt = run_watch_completion_fixture()
        outcome = load_exact_watch_outcome(receipt.watch_outcome_id)
        self.assertEqual(receipt.watch_outcome_id, watch_outcome_id_for(outcome))
        self.assertEqual(outcome.evidence_completion, receipt.evidence_completion)
        self.assertEqual(
            WatchEvidenceCompletion.COMPLETED is receipt.evidence_completion,
            receipt.complete,
        )
```

- [ ] **Step 2: Run and verify failure**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_containment_watch -v`

Expected: FAIL because the watcher module does not exist.

- [ ] **Step 3: Implement the worker protocol**

Use one worker process launched from the same Python executable with this exact argument-array construction:

```python
command = (
    sys.executable,
    "-B",
    "-m",
    "modlab.validation.windows_watch",
    "--worker",
    str(request_path.resolve(strict=True)),
)
```

The strict request lists source `mods`, raw Play profile, downloads, Overwrite, and bounded game evidence roots. The worker opens each root with `FILE_LIST_DIRECTORY`, `FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE`, `FILE_FLAG_BACKUP_SEMANTICS`, and one blocking `ReadDirectoryChangesW` thread per root. Watch recursively for file name, directory name, size, last write, creation, attributes, and security changes. Events are sequence-numbered and atomically appended as canonical NDJSON beneath the evidence directory.

Write a ready record only after every handle is open. Write a terminal record only after the stop token is observed, all threads return, buffers parse completely, handles close, and event bytes reload. The same-controller `stop_watch()` publishes the immutable `WatchOutcome` and returns its content-ID reference in `WatchReceipt`; `WatchReceipt.complete` is derived solely from that outcome's `evidence_completion`. `ERROR_NOTIFY_ENUM_DIR`, malformed records, a sequence gap, worker death, root identity change, or an unclosed handle sets `WatchOutcome.evidence_completion=Incomplete`; no copied boolean may report success.

- [ ] **Step 4: Add before/after manifests as a second proof**

The caller computes complete SHA-256 tree identities for the small synthetic protected folder and exact bytes for Play, downloads, and Overwrite before and after. A scenario cannot pass from a zero-event, bound `WatchOutcome` if any manifest differs, and identical final hashes cannot pass if that outcome records a transient mutation.

- [ ] **Step 5: Run focused tests**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_containment_watch -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add modlab/validation/windows_watch.py tests/test_mo2_containment_watch.py
git commit -m "feat: add loss-detecting containment mutation monitor"
```

---

### Task 5: Disposable exact-MO2 scenario fixtures

**Files:**
- Create: `modlab/validation/mo2_containment_fixtures.py`
- Create: `tests/support/mo2_containment.py`
- Create: `tests/test_mo2_containment_fixtures.py`
- Modify: `modlab/workspace.py`
- Modify: `tests/test_workspace.py`

**Interfaces:**
- Consumes: source ModLab workspace, exact MO2 artifact ID, Steam root, validation root, and scenario.
- Produces: `ContainmentFixture`, `ScenarioArchives`, `prepare_containment_fixture(source_workspace: Path, mo2_artifact_id: str, steam_root: Path, validation_root: Path, scenario: ContainmentScenario) -> ContainmentFixture`, `write_scenario_archives(root: Path) -> ScenarioArchives`, and new `WorkspaceLayout.mo2_containment_validation`.

The disposable stage instance, and only that instance, pre-seeds
`app/nxmhandler.ini` with `General/noregister=true` before its tree is labeled
Low. This prevents an unrelated operating-system NXM-association prompt from
interrupting the operator scenario without registering a handler or changing
the disposable source instance.

- [ ] **Step 1: Write failing fixture tests**

```python
class ContainmentFixtureTests(unittest.TestCase):
    def test_archives_have_exact_safe_contents_and_fomod_dependency(self):
        archives = write_scenario_archives(self.root)
        self.assertEqual(
            ("meshes/new-folder.bin",),
            zip_names(archives.new_folder),
        )
        self.assertIn("meshes/canary.bin", zip_names(archives.overwrite_probe))
        module = read_zip(archives.fomod_dependency, "fomod/ModuleConfig.xml")
        self.assertIn(b'fileDependency file="marker.txt" state="Active"', module)
        self.assertIn(b'destination="dependency-seen.txt"', module)

    def test_fixture_uses_two_confined_instances_and_payload_junctions(self):
        fixture = prepare_fixture_with_fake_bootstrap(self.root)
        self.assertTrue(fixture.source_workspace.is_relative_to(fixture.run_root))
        self.assertTrue(fixture.stage_workspace.is_relative_to(fixture.run_root))
        self.assertEqual(
            fixture.source_mods / "Protected Existing",
            inspect_junction(fixture.stage_mods / "Protected Existing").target_path,
        )
        self.assertEqual(b"+Protected Existing\r\n", fixture.stage_lab_modlist.read_bytes())
```

- [ ] **Step 2: Run and verify failure**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_containment_fixtures tests.test_workspace -v`

Expected: FAIL because the fixture module and workspace field do not exist.

- [ ] **Step 3: Add the validation layout**

Add only:

```python
mo2_containment_validation = resolved / "runtime" / "validation" / "mo2-containment"
```

to `_DIRECTORIES` and `WorkspaceLayout`. Preserve every existing path and test that initialization creates the new direct directory without removing unknown content.

- [ ] **Step 4: Generate the three deterministic ZIP payloads**

Use `zipfile.ZipFile(..., ZIP_DEFLATED)` with fixed `ZipInfo.date_time=(2026, 1, 1, 0, 0, 0)`, fixed POSIX mode, sorted entries, no absolute/traversal path, and no archive comment.

`new-folder.zip` contains only `meshes/new-folder.bin` with bytes `b"modlab-new-folder-v1\n"`.

`overwrite-probe.zip` contains `meshes/canary.bin` with bytes `b"MUTATION-MUST-NEVER-REACH-SOURCE\n"` and `meshes/new.bin` with bytes `b"new\n"`.

`fomod-dependency.zip` contains `fomod/info.xml`, `fomod/ModuleConfig.xml`, `payload/always.txt`, and `payload/dependency-seen.txt`. Store these exact UTF-8 XML bytes with LF line endings and no byte-order mark:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<fomod>
  <Name>ModLab FOMOD Dependency Probe</Name>
  <Author>ModLab</Author>
  <Version>1.0</Version>
  <Description>Validates projected active-file dependency visibility.</Description>
</fomod>
```

```xml
<?xml version="1.0" encoding="UTF-8"?>
<config xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
        xsi:noNamespaceSchemaLocation="http://qconsulting.ca/fo3/ModConfig5.0.xsd">
  <moduleName>ModLab FOMOD Dependency Probe</moduleName>
  <requiredInstallFiles>
    <file source="payload\always.txt" destination="always.txt" priority="0" />
  </requiredInstallFiles>
  <conditionalFileInstalls>
    <patterns>
      <pattern>
        <dependencies operator="And">
          <fileDependency file="marker.txt" state="Active" />
        </dependencies>
        <files>
          <file source="payload\dependency-seen.txt" destination="dependency-seen.txt" priority="0" />
        </files>
      </pattern>
    </patterns>
  </conditionalFileInstalls>
</config>
```

Use `b"always\n"` and `b"dependency-visible\n"` for the two payload files. The conditional file must be absent when the projected `marker.txt` is disabled and present when `+Protected Existing` is active.

- [ ] **Step 5: Prepare independent source and stage instances**

Before Task 6 consumes them, `tests/support/mo2_containment.py` must implement deterministic disposable fail-closed fixtures: `capture_interrupted_with_valid_event(root_kind: str) -> ScenarioResult` creates one request-bound event and then kills the exact controller; `recover_pre_scenario_interruption() -> ScenarioRecovery` interrupts before the durable `ScenarioStarted` boundary and proves cleanup; `recover_with_uncertain_watcher_identity() -> ScenarioRecovery` injects access denial and records refusal; `old_run_id() -> str` and `fresh_run_id() -> str` return the two persisted run identities; and `fresh_launch_count() -> int` returns the fake process launch counter. These fixtures are test-owned and disposable, and must be complete before the Task 6 service tests consume them.

For each scenario, verify the MO2 artifact in the source vault, import the exact retained payload into two fresh scenario-local vaults, and call the existing `prepare_mo2_setup()`/`apply_mo2_setup()` workflow twice. Require both receipts to be `Created`, executable version `2.5.2.0`, and all paths beneath the scenario run.

Populate source `mods/Protected Existing` with `marker.txt`, `meshes/canary.bin`, and deterministic `meta.ini`. Add `+Protected Existing` to source Lab and Play. Copy the exact profile bytes into stage, replace stage's empty mod folder with a verified junction projection, and re-run the existing MO2 scanner against both instances.

Create separate low-integrity scenario-local directories for stage `downloads`, `profiles`, `mods`, `overwrite`, `cache`, `logs`, `TEMP`, `TMP`, `APPDATA`, `LOCALAPPDATA`, `USERPROFILE`, and `HOME`. Create a direct `Desktop` child beneath the disposable `USERPROFILE` before labeling that profile tree Low. Pass their paths only in the child environment/configuration; never mutate the parent environment. Resolve `FOLDERID_LocalAppDataLow` and the current Low temporary directory before launch, de-duplicate equal file identities, and watch them as `ExternalLocalLow`/`ExternalTempLow`. A write there is an outside-ModLab containment failure, not an allowlisted MO2 side effect.

- [ ] **Step 6: Run focused tests**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_containment_fixtures tests.test_workspace -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add modlab/workspace.py modlab/validation/mo2_containment_fixtures.py tests/support/mo2_containment.py tests/test_mo2_containment_fixtures.py tests/test_workspace.py
git commit -m "feat: add disposable MO2 containment fixtures"
```

---

### Task 6: Crash-safe scenario orchestration and adjudication

**Files:**
- Create: `modlab/validation/mo2_containment_store.py`
- Create: `modlab/validation/mo2_containment_service.py`
- Create: `tests/test_mo2_containment_store.py`
- Create: `tests/test_mo2_containment_service.py`

**Interfaces:**
- Consumes: Tasks 1-5 interfaces.
- Produces: `ContainmentStore`, `prepare_run(source_workspace: Path, mo2_artifact_id: str, steam_root: Path, validation_root: Path) -> str`, `arm_scenario(validation_root: Path, run_id: str, scenario: ContainmentScenario) -> ScenarioJournal`, `launch_scenario(validation_root: Path, run_id: str, scenario: ContainmentScenario) -> ScenarioJournal`, `capture_scenario(validation_root: Path, run_id: str, scenario: ContainmentScenario) -> ScenarioResult`, `recover_scenario(validation_root: Path, run_id: str, scenario: ContainmentScenario) -> ScenarioRecovery`, and `adjudicate_run(validation_root: Path, run_id: str) -> CapabilityDecision`.

- [ ] **Step 1: Write failing store/state-machine tests**

```python
class ContainmentStoreTests(unittest.TestCase):
    def test_claim_and_transitions_are_atomic_and_idempotent(self):
        store = ContainmentStore(self.root)
        journal = store.create(valid_prepared_journal())
        armed = store.transition(journal, ScenarioState.ARMED)
        self.assertEqual(armed, store.load_journal(armed.run_id, armed.scenario))
        with self.assertRaisesRegex(ContainmentStoreError, "Armed -> Prepared"):
            store.transition(armed, ScenarioState.PREPARED)

    def test_result_and_decision_are_immutable(self):
        store = ContainmentStore(self.root)
        first = store.write_result(valid_scenario_result())
        second = store.write_result(valid_scenario_result())
        self.assertFalse(first.existed)
        self.assertTrue(second.existed)
```

- [ ] **Step 2: Write failing service policy tests**

```python
class ContainmentServiceTests(unittest.TestCase):
    def test_merge_and_replace_pass_only_with_zero_source_events_and_hash_match(self):
        for scenario in (ContainmentScenario.MERGE_EXISTING, ContainmentScenario.REPLACE_EXISTING):
            result = capture_with_fakes(scenario=scenario)
            self.assertEqual(ScenarioOutcome.PASSED, result.outcome)
            self.assertEqual(result.protected_before, result.protected_after)
            self.assertEqual((), result.watcher_events)
            self.assertEqual((), result.production_backup_names)
            self.assertIsNone(result.adopted_name)

    def test_supported_requires_all_four_exact_passes(self):
        decision = adjudicate_results(tuple(valid_result(s) for s in ContainmentScenario))
        self.assertEqual(CapabilityVerdict.SUPPORTED, decision.verdict)

    def test_valid_forbidden_event_then_controller_death_is_failed_without_retry(self):
        result = capture_interrupted_with_valid_event("PlayProfile")
        self.assertEqual(ScenarioOutcome.FAILED, result.outcome)
        self.assertEqual(WatchEvidenceCompletion.INCOMPLETE, result.watch_evidence_completion)
        self.assertFalse(result.fresh_retry_eligible)

    def test_pre_scenario_interruption_may_retry_once_after_cleanup(self):
        recovery = recover_pre_scenario_interruption()
        self.assertEqual(ScenarioCleanupStatus.SUCCEEDED, recovery.cleanup_status)
        self.assertTrue(recovery.fresh_run_permitted)
        self.assertNotEqual(old_run_id(), fresh_run_id())

    def test_cleanup_refusal_never_starts_fresh_run(self):
        recovery = recover_with_uncertain_watcher_identity()
        self.assertEqual(ScenarioCleanupStatus.REFUSED, recovery.cleanup_status)
        self.assertFalse(recovery.fresh_run_permitted)
        self.assertEqual(0, fresh_launch_count())
```

Add explicit failure cases for source event, final hash mismatch, Play byte change, watcher overflow, MO2 still running, wrong process integrity, unexpected stage backup, source junction replaced, FOMOD marker absent, adoption collision, more than one new folder, and failed integrity normalization. Also prove that a protected-manifest delta plus damaged terminal is `Failed` with no retry; a malformed event after `ScenarioStarted` is `Incomplete` with no retry; a manual clean run after `Failed` preserves both immutable result IDs; and adjudication rejects `Failed` without hiding it behind a later `Passed` result.

- [ ] **Step 3: Run and verify failure**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_containment_store tests.test_mo2_containment_service -v`

Expected: FAIL because store/service modules do not exist.

- [ ] **Step 4: Implement confined storage and recovery**

Store each run at:

```text
runtime/validation/mo2-containment/<run-hex>/
  request.json
  scenarios/<scenario>/journal.json
  scenarios/<scenario>/before.json
  scenarios/<scenario>/watch/
  scenarios/<scenario>/after.json
  scenarios/<scenario>/result.json
  quarantine/
  decision.json
```

Use a named Windows mutex per run, atomic create/no-replace for immutable documents, atomic replacement only for legal journal transitions, exact readback, and the transition graph `Prepared -> Armed -> ScenarioStarted -> Launched -> Captured`, with any nonterminal state able to enter `RecoveryRequired`. `ScenarioStarted` is the durable boundary: recovery never returns a journal or transitions a recovery state back to a success path. It returns `ScenarioRecovery`, records `Succeeded` or `Refused` cleanup, and makes a fresh run permissible only for one same-command retry before `ScenarioStarted`, after successful cleanup and proof that the prior exact controller, watcher, and MO2 processes are all absent. Recovery and every fresh launch refuse after cleanup refusal, access denial, identity uncertainty, a live prior controller/watcher/MO2 process, or unverified watcher liveness. Recovery never kills MO2, restores/normalizes only stage integrity, never changes source integrity, quarantines only exact staging content, and re-verifies source evidence.

- [ ] **Step 5: Implement scenario policies**

`prepare_run()` creates four independent fixtures. `arm_scenario()` captures the pre-existing source/Play/download/Overwrite/game evidence, verifies Medium-or-higher source and Low stage, starts watchers, and stops at watcher-ready while writing `Armed`. Immediately before `CreateProcess` launches MO2, `launch_scenario()` must durably transition to `ScenarioStarted`; it then launches exact stage `ModOrganizer.exe --profile "ModLab - Lab"` with the low token and contained environment, verifies its PID/path/integrity, and writes `Launched`. A permitted fresh launch must re-prove the absence of the prior exact controller, watcher, and MO2 processes. No retry after this boundary can reuse the prior run.

`capture_scenario()` requires MO2 closed. Its same-controller `stop_watch()` first publishes and returns the immutable watch-outcome content-ID reference; capture then reloads that exact `WatchOutcome` by content ID, verifies its request/session/run/scenario bindings, and only then derives the scenario verdict, captures the stable post-MO2 state, and inspects all stage projection entries. For new-folder and FOMOD, it then performs the isolated adoption/quarantine proof and captures a second stable final protected state; `ScenarioResult.protected_after` is this final state and must equal the pre-MO2 state. Outcome precedence is `Failed`, then `Incomplete`, then `Passed`. A `Failed` result with `Incomplete` watcher evidence is allowed only when the bound watcher outcome records a positive event or the protected state changed; otherwise watcher damage, malformed events, or liveness uncertainty remain `Incomplete` and are never retry-eligible after `ScenarioStarted`. It applies these exact policies:

For an adoption scenario, complete staging observation of a non-empty wrong or
extra folder set is a deterministic violation. An empty new-folder set while
output/adoption observation is incomplete is not proof that containment
failed: it may represent an interrupted operator workflow and remains
`Incomplete`. If output observation is complete, any mismatch remains a
deterministic violation.

- `NewFolder`: all pre-existing source evidence unchanged through the MO2 phase; one exact `ModLab Spike New` direct folder containing only `meshes/new-folder.bin` and MO2's generated `meta.ini`; after watchers stop, adopt the collision-free sibling, verify it, move it to quarantine, and prove the source root returned to its original identity; no extra folder.
- `MergeExisting`: source unchanged with zero events; `Protected Existing` source junction still exact; no production backup; nothing adopted.
- `ReplaceExisting`: same source guarantees; the exact disposable replacement payload (`meshes/canary.bin`, `meshes/new.bin`, and MO2's generated `meta.ini`) is inspected and quarantined; the source junction is recreated from metadata and verified; any staging backup is rejected; nothing is adopted.
- `FomodDependency`: all pre-existing source evidence unchanged through the MO2 phase; one exact `ModLab Spike FOMOD` folder containing only `always.txt`, `dependency-seen.txt`, and MO2's generated `meta.ini`; after watchers stop, adopt/verify/quarantine it and prove the source root returned to its original identity.

`adjudicate_run()` computes `Supported` only from four immutable passing result IDs. Any safety failure is `Rejected`; missing or incomplete machine evidence is `Incomplete`. Every earlier immutable result remains visible after a later explicit clean run. A prior `Failed` result causes `Rejected` and cannot be hidden by a later `Passed` result within the same exact command-policy fingerprint.

The command fingerprint also binds the exact scenario-classification policy.
`scenario-classification-v2` corrected interrupted empty-output handling.
`scenario-classification-v3` additionally recognizes the exact MO2-generated
`meta.ini` beside each adoption probe's payload while continuing to reject any
missing payload or other extra output. `scenario-classification-v4` recognizes
MO2's exact Replace behavior inside the disposable store. It pins and hashes the
direct replacement tree, derives output names from that stable identity, moves
the same pinned root with a no-replace rename into a direct pinned quarantine,
re-pins and proves every member identity and byte tree unchanged, and proves the
old staging path absent. Only then does it recreate and verify the source
junction. Any backup, adoption, unknown output, source event, redirected
quarantine, identity race, collision, or restoration uncertainty remains
rejected or incomplete. The retained legacy, v2, and v3 diagnostic results
remain parseable and visible, but their exact fingerprints are not predecessors
of the v4 cohort. Therefore an over-conservative older diagnostic
cannot permanently poison a corrected-policy capability decision, while a
`Failed` result still cannot be hidden by a later `Passed` result within the
same exact policy fingerprint.

The command fingerprint also binds the disposable shell-environment protocol.
`disposable-shell-environment-v2` creates a direct `Desktop` directory beneath
the disposable `USERPROFILE` before the profile tree is labeled Low. This keeps
the Windows file picker inside a complete disposable shell profile.
`disposable-shell-environment-v3` additionally writes
`Fomod%20Installer\use_any_file=true` only to the disposable stage
`ModOrganizer.ini` before Low labeling. This lets MO2 2.5.2 evaluate the
fixture's projected non-plugin `marker.txt` dependency while leaving the source
MO2 configuration absent and unchanged. Immutable pre-v3 fixture results remain
readable, but are not predecessors of the corrected fixture cohort; all results
within one exact fixture-policy hash still retain normal failed-history
precedence.

- [ ] **Step 6: Run focused tests**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_containment_store tests.test_mo2_containment_service -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add modlab/validation/mo2_containment_store.py modlab/validation/mo2_containment_service.py tests/test_mo2_containment_store.py tests/test_mo2_containment_service.py
git commit -m "feat: orchestrate MO2 containment capability runs"
```

---

### Task 7: Opt-in validation CLI and operator runbook

**Files:**
- Create: `modlab/validation/mo2_containment_cli.py`
- Create: `tests/test_mo2_containment_cli.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 6 service functions.
- Produces: `C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -B -m modlab.validation.mo2_containment_cli prepare|validate|recover|show|adjudicate`.

`validate` is the only public scenario-execution command. It remains one uninterrupted foreground controller from watcher startup through scenario result publication. The lower-level Task 6 `arm_scenario()`, `launch_scenario()`, and `capture_scenario()` functions remain internal service boundaries and are never exposed as independent CLI processes.

- [ ] **Step 1: Write failing CLI tests**

```python
class ContainmentCliTests(unittest.TestCase):
    def test_prepare_reports_no_production_action(self):
        code, output = invoke_cli("prepare", valid_prepare_args(), service=fake_service())
        self.assertEqual(0, code)
        self.assertIn("Disposable validation only", output)
        self.assertIn("Production MO2 changes: none", output)

    def test_validate_prints_exact_visible_scenario_instructions(self):
        code, output = invoke_cli("validate", merge_args(), service=passing_service())
        self.assertEqual(0, code)
        self.assertIn("Select overwrite-probe.zip", output)
        self.assertIn("rename the target to Protected Existing", output)
        self.assertIn("choose Merge", output)
        self.assertIn("MO2 PID", output)

    def test_validate_keeps_arm_launch_and_capture_in_one_controller(self):
        service = ordered_passing_service()
        code, _ = invoke_cli("validate", merge_args(), service=service)
        self.assertEqual(0, code)
        self.assertEqual(("arm", "launch", "capture"), service.calls)

    def test_validate_distinguishes_proven_failure_from_incomplete(self):
        failed_code, _ = invoke_cli("validate", merge_args(), service=failed_service())
        incomplete_code, _ = invoke_cli(
            "validate", merge_args(), service=incomplete_service()
        )
        self.assertEqual(1, failed_code)
        self.assertEqual(3, incomplete_code)

    def test_adjudicate_never_defaults_incomplete_to_supported(self):
        code, output = invoke_cli("adjudicate", run_args(), service=incomplete_service())
        self.assertEqual(3, code)
        self.assertIn("Incomplete", output)

    def test_json_output_exposes_exact_run_id_for_follow_up_commands(self):
        code, output = invoke_cli("prepare", json_prepare_args(), service=fake_service())
        self.assertEqual(0, code)
        self.assertEqual("containment-run:" + "a" * 32, json.loads(output)["runId"])

    def test_json_validate_writes_one_final_object_to_stdout(self):
        code, stdout, stderr = invoke_json_validate(service=passing_service())
        self.assertEqual(0, code)
        self.assertIsInstance(json.loads(stdout), dict)
        self.assertNotIn("Select overwrite-probe.zip", stdout)
        self.assertIn("Select overwrite-probe.zip", stderr)

    def test_show_is_read_only_and_returns_zero_for_failed_history(self):
        service = failed_history_service()
        code, output = invoke_cli("show", run_args(), service=service)
        self.assertEqual(0, code)
        self.assertIn("Failed", output)
        self.assertEqual((), service.mutations)

    def test_recover_success_returns_zero_without_upgrading_old_result(self):
        service = cleanup_succeeded_service()
        code, output = invoke_cli("recover", scenario_args(), service=service)
        self.assertEqual(0, code)
        self.assertIn("Succeeded", output)
        self.assertEqual("Incomplete", service.old_outcome)
```

- [ ] **Step 2: Run and verify failure**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_containment_cli -v`

Expected: FAIL because the CLI module does not exist.

- [ ] **Step 3: Implement exact commands and exit codes**

Use strict subcommands:

```text
prepare --source-workspace PATH --artifact ARTIFACT_ID --steam-root PATH --workspace PATH [--format text|json]
validate RUN_ID NewFolder|MergeExisting|ReplaceExisting|FomodDependency --workspace PATH [--format text|json]
recover RUN_ID SCENARIO --workspace PATH [--format text|json]
show RUN_ID --workspace PATH [--format text|json]
adjudicate RUN_ID --workspace PATH [--format text|json]
```

`ARTIFACT_ID` must match `archive-sha256:[0-9a-f]{64}`, `RUN_ID` must match `containment-run:[0-9a-f]{32}`, and `SCENARIO` must be one exact `ContainmentScenario` value. The CLI revalidates parsed identifiers through the strict Task 1/6 boundary before trusting them.

JSON output is one strict final object with `command`, `runId`, `scenario`, `state`, `outcome`, `verdict`, `writtenPaths`, `launchedProcesses`, `sourceChanges`, `gameChanges`, `productionMo2Changes`, `instructions`, and `reasons`; non-applicable scalar fields are JSON `null`, and empty collections remain present. JSON stdout contains exactly that one object and no prompts or explanatory text. During an interactive JSON `validate`, live operator guidance and diagnostics go to stderr; the final object is written to stdout only after capture or safe refusal.

Exit `0` for successful preparation/recovery/display, a `Passed` validation result, or a `Supported` decision. Exit `1` only for a proven `Failed` validation result or `Rejected` adjudication. Exit `2` for malformed identifiers, arguments, or schemas. Exit `3` for safe refusal, invalid transition, `Incomplete`, running-process uncertainty, or `RecoveryRequired`. Unexpected operational uncertainty is reported as `Incomplete`/refused with exit `3`, never mislabeled as proven failure. `show` returns `0` when it successfully displays Failed, Rejected, or Incomplete history. `recover` returns `0` when cleanup succeeds even though the old attempt remains permanently Incomplete, and `3` when cleanup is refused.

Every response lists paths written, processes launched, source changes, game changes, and production MO2 changes. Text output says `none` rather than omitting an empty category. `show` is strictly read-only and never recovers or adjudicates implicitly.

`validate` performs `arm_scenario()`, `launch_scenario()`, operator guidance/wait, and `capture_scenario()` in one foreground process. After printing the exact procedure, it waits for the operator to close the disposable MO2 normally and explicitly continue; capture still independently proves the exact launched process is absent. Closing or interrupting the controller before publication permanently leaves the attempt Incomplete and eligible only for cleanup-only recovery. The procedure is:

- `NewFolder`: install `new-folder.zip`, leave/enter `ModLab Spike New`, complete, close MO2.
- `MergeExisting`: install `overwrite-probe.zip`, enter `Protected Existing`, choose Merge, leave backup unchecked, acknowledge any access-denied/cancel result, close MO2.
- `ReplaceExisting`: same but choose Replace.
- `FomodDependency`: install `fomod-dependency.zip` as `ModLab Spike FOMOD`, complete the normal FOMOD, close MO2.

The CLI does not click, infer installer success, close MO2, or kill a process. The scenario service publishes immutable `result.json` once. `adjudicate` consumes authoritative scenario results and publishes immutable `decision.json` once; it never replaces or repurposes `result.json`.

- [ ] **Step 4: Document the validation-only workflow**

Add a README section that labels these commands developer validation, explains that source/stage instances are disposable, states that computer control is required only while foreground `validate` is displaying its procedure, and warns that no `Supported` result enables production installation until the next reviewed implementation plan consumes its receipt.

- [ ] **Step 5: Run focused tests**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_containment_cli -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add modlab/validation/mo2_containment_cli.py tests/test_mo2_containment_cli.py README.md
git commit -m "feat: expose opt-in containment validation workflow"
```

---

### Task 8: Exact MO2 2.5.2 integration gate and machine verdict

**Files:**
- Create: `tests/test_mo2_containment_windows_integration.py`
- Create: `docs/validation/mo2-containment-spike.md`

**Interfaces:**
- Consumes: `MODLAB_MO2_ARCHIVE`, `MODLAB_STEAM_ROOT`, the validation CLI, computer control, and exact retained MO2 2.5.2.
- Produces: one immutable workspace `CapabilityDecision` and one sanitized repository verdict document.
- The opt-in prerequisite's `production_paths_written` field is scoped to the
  observed Skyrim production inputs only: the exact Steam appmanifest for app
  `489830` and the resolved direct Skyrim game-root tree. It makes no claim
  about unrelated live Steam client cache, log, userdata, or client-root paths.

- [ ] **Step 1: Write the opt-in prerequisite/invariant test**

```python
@unittest.skipUnless(
    os.name == "nt" and os.environ.get("MODLAB_MO2_ARCHIVE") and os.environ.get("MODLAB_STEAM_ROOT"),
    "real MO2 archive and Steam root were not supplied",
)
class Mo2ContainmentWindowsIntegrationTests(unittest.TestCase):
    def test_real_fixture_is_exact_isolated_and_low_integrity(self):
        fixture = prepare_real_containment_fixture(
            Path(os.environ["MODLAB_MO2_ARCHIVE"]),
            Path(os.environ["MODLAB_STEAM_ROOT"]),
        )
        self.assertEqual("2.5.2.0", fixture.executable_version)
        self.assertEqual(IntegrityLevel.LOW, inspect_path_integrity(fixture.stage_workspace))
        self.assertGreaterEqual(inspect_path_integrity(fixture.source_workspace), IntegrityLevel.MEDIUM)
        self.assertEqual(0, fixture.payload_bytes_copied_for_projection)
        self.assertEqual((), fixture.production_paths_written)
```

- [ ] **Step 2: Run all automated tests and the exact opt-in prerequisite before GUI work**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest discover -s tests -v`

Expected: all tests pass; only explicitly opt-in integrations skip when their environment variables are absent.

Then run the opt-in Windows prerequisite against the exact retained archive and Steam root:

```powershell
$env:MODLAB_MO2_ARCHIVE = 'C:\Users\red\Desktop\Modlab\workspace\library\archives\e6\e6376efd87fd5ddd95aee959405e8f067afa526ea6c2c0c5aa03c5108bf4a815\payload.7z'
$env:MODLAB_STEAM_ROOT = 'C:\Users\red\Desktop\Steam'
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_containment_windows_integration -v
$integrationExit = $LASTEXITCODE
Remove-Item Env:MODLAB_MO2_ARCHIVE
Remove-Item Env:MODLAB_STEAM_ROOT
if ($integrationExit -ne 0) { throw "containment integration prerequisite failed with exit $integrationExit" }
```

Expected: the real integration prerequisite runs rather than skips, reports MO2 `2.5.2.0`, observes a Low stage and Medium-or-higher disposable source, copies zero projection payload bytes, and reports that the observed Skyrim production inputs (the exact Steam manifest and game-root tree) were unchanged. This is not a broad whole-Steam-root claim.

- [ ] **Step 3: Prepare the real disposable run**

Run from the isolated worktree, using the managed workspace and exact retained MO2 artifact:

```powershell
$pythonPath = 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$workspacePath = 'C:\Users\red\Desktop\Modlab\workspace'
$artifactId = 'archive-sha256:e6376efd87fd5ddd95aee959405e8f067afa526ea6c2c0c5aa03c5108bf4a815'
$prepareJson = & $pythonPath -B -m modlab.validation.mo2_containment_cli prepare `
    --source-workspace $workspacePath `
    --artifact $artifactId `
    --steam-root 'C:\Users\red\Desktop\Steam' `
    --workspace $workspacePath `
    --format json
if ($LASTEXITCODE -ne 0) { throw "containment preparation failed with exit $LASTEXITCODE" }
$prepareRecord = $prepareJson | ConvertFrom-Json
$runId = [string]$prepareRecord.runId
if ($runId -notmatch '^containment-run:[0-9a-f]{32}$') { throw 'prepare returned an invalid run ID' }
```

Before invoking `prepare`, verify that the exact `$artifactId` is present by running `& $pythonPath -B -m modlab artifact list --workspace $workspacePath --format json`; abort instead of substituting any other archive if it is absent.

- [ ] **Step 4: Execute all four visible scenarios with computer control**

Before GUI action, the main agent reads `C:\Users\red\.codex\plugins\cache\openai-bundled\computer-use\26.825.51511\skills\computer-use\SKILL.md` completely and announces that computer control is now required by the plan. For each scenario in this order—`NewFolder`, `MergeExisting`, `ReplaceExisting`, `FomodDependency`—start one foreground `validate`, follow only its exact printed MO2 actions with computer control, close MO2 normally, and explicitly continue the same waiting CLI process so it can capture and publish the result without transferring watcher ownership:

```powershell
& $pythonPath -B -m modlab.validation.mo2_containment_cli validate $runId NewFolder --workspace $workspacePath
if ($LASTEXITCODE -ne 0) { throw "NewFolder validation ended with exit $LASTEXITCODE" }

& $pythonPath -B -m modlab.validation.mo2_containment_cli validate $runId MergeExisting --workspace $workspacePath
if ($LASTEXITCODE -ne 0) { throw "MergeExisting validation ended with exit $LASTEXITCODE" }

& $pythonPath -B -m modlab.validation.mo2_containment_cli validate $runId ReplaceExisting --workspace $workspacePath
if ($LASTEXITCODE -ne 0) { throw "ReplaceExisting validation ended with exit $LASTEXITCODE" }

& $pythonPath -B -m modlab.validation.mo2_containment_cli validate $runId FomodDependency --workspace $workspacePath
if ($LASTEXITCODE -ne 0) { throw "FomodDependency validation ended with exit $LASTEXITCODE" }
```

Each command stays foreground and owns its watcher until the result is durably published. Do not start the next scenario while it is running. Exit `1` is a proven containment failure: stop the remaining live scenarios and preserve it for `Rejected` adjudication. Exit `3` is Incomplete or a safe refusal: close MO2 normally if needed, run `recover` for that exact scenario, preserve the Incomplete evidence, and stop the live run. Do not improvise a mod name, backup choice, or installer option. If the UI differs from the printed procedure, do not click through it: close MO2 normally and continue the same foreground command only so it can fail closed, then recover if instructed.

An unexpected NXM-association prompt is one such UI mismatch. If it prevents
the operator from performing the requested install, the absence of the
expected staging folder is `Incomplete`, not a proven `Failed`, unless separate
positive breach evidence exists.

The first diagnostic run used the legacy classification fingerprint. The
second used `scenario-classification-v2` but the pre-v2 disposable shell
environment and stopped `Incomplete` when the Windows file picker found no
disposable Desktop. The third used `scenario-classification-v2` with the v2
shell environment and safely installed/quarantined the exact NewFolder probe,
but recorded an over-conservative `Failed` because v2 did not recognize MO2's
generated `meta.ini`. All three are retained read-only. The replacement v3 run
passed NewFolder and Merge, then stopped `Incomplete` when MO2 safely
replaced only the disposable projection but v3 could not re-establish and prove
that projection. It is also retained read-only. The first v4/v2 run,
`containment-run:a8131800fb524a6a9aac567c11382a00`, passed NewFolder, Merge,
and Replace. Its FomodDependency scenario safely produced only `always.txt` and
MO2's `meta.ini`, then recorded `Failed` because the v2 stage configuration made
MO2 treat the non-plugin `marker.txt` dependency as missing. It recorded no
source, game, or production-MO2 changes and remains immutable and read-only.

Prepare the corrected run under `scenario-classification-v4` and
`disposable-shell-environment-v3`. Verify its command fingerprint differs from
the retained v4/v2 run and its `predecessorRunIds` is empty. Do not edit, delete,
reuse, or list any diagnostic run as predecessor evidence for the corrected
fixture-policy decision.

- [ ] **Step 5: Adjudicate and inspect the decision**

Run:

```powershell
$decisionJson = & $pythonPath -B -m modlab.validation.mo2_containment_cli adjudicate $runId --workspace $workspacePath --format json
$decisionExit = $LASTEXITCODE
$decisionRecord = $decisionJson | ConvertFrom-Json
if ($decisionExit -notin 0, 1, 3) { throw "unexpected adjudication exit $decisionExit" }
if ([string]$decisionRecord.runId -ne $runId) { throw 'decision run ID mismatch' }
```

Expected for `Supported`: four passing immutable result IDs; exact source before/after identities; zero source watcher events; no production backup; successful new-folder and FOMOD adoption/quarantine; source Play unchanged; Low MO2 process; Medium adopted tree; exact mechanism `isolated-low-integrity-junction-projection-v1`.

Any positively proved source mutation or deterministic safety violation produces `Rejected` with exit `1`. Missing fields, watcher uncertainty, unproved process state, or adoption uncertainty produce `Incomplete` with exit `3`. Neither may produce exit `0`.

- [ ] **Step 6: Write the sanitized verdict document**

Write exactly one of these evidence-backed conclusions, followed by the run/decision IDs, MO2 version, four scenario outcomes, tests run, and confirmation that the observed Skyrim production inputs (the exact Steam manifest and game-root tree) were unchanged. State explicitly that unrelated live Steam client cache, log, userdata, and client-root paths are excluded from attribution; do not make a broad whole-Steam-root claim.

```text
SUPPORTED — isolated low-integrity staging with verified junction projection satisfied the approved pre-write containment gate in the disposable MO2 2.5.2 validation run. Production use still requires the reviewed bridge/lifecycle implementation plan.
```

or:

```text
NOT SUPPORTED — isolated low-integrity staging did not satisfy the approved pre-write containment gate. Direct installation into the production shared store remains prohibited; no bridge/lifecycle implementation may assume this mechanism.
```

Use `NOT SUPPORTED` for both `Rejected` and `Incomplete`, and state which one occurred. Do not include absolute user paths, usernames, archive filenames, or raw machine logs in the committed document.

- [ ] **Step 7: Re-run verification**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest discover -s tests -v`

Run: `git diff --check`

Expected: tests pass, expected opt-in skips are disclosed, and diff check prints nothing.

- [ ] **Step 8: Commit**

```powershell
git add tests/test_mo2_containment_windows_integration.py docs/validation/mo2-containment-spike.md
git commit -m "test: record MO2 containment capability verdict"
```

## Self-review Checklist

- The plan implements the approved pre-write capability gate without exposing production installation.
- Merge and Replace are tested against a Play-referenced source folder with transient-event monitoring, final hashes, and production-backup inspection.
- FOMOD dependency visibility is tested through the projected active `marker.txt`, not assumed from a copied profile.
- Existing payload bytes are projected, not copied; routine preparation scales by mod-directory count rather than payload size.
- Low-integrity enforcement protects Medium source/game paths while allowing contained staging writes.
- Junctions exist only in the isolated staging store and are exact-target verified.
- Adoption accepts one direct unique folder, removes Low integrity, preserves its full tree hash, and never adopts a collision, projection, backup, or extra folder.
- Watcher overflow, worker death, process ambiguity, incomplete UI work, and any source mutation prevent `Supported`.
- All runtime state stays beneath the organized ModLab workspace; the committed verdict is sanitized.
- The next bridge/lifecycle plan is conditional on this spike's result.
