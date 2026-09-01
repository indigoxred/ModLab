# Fail-Closed Containment Watch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Task 4's recoverable multi-controller watcher protocol with an evidence-preserving fail-closed session whose interrupted attempts can be cleaned up but never promoted, while revising the shared Task 1 and future Task 6 contracts to preserve positive breach evidence and safe fresh-run behavior.

**Architecture:** One controller process owns completion authority and retains the exact worker process handle. The worker owns directory, guard-chain, journal, and launching-controller observation handles; immutable request, claim, launch, terminal, and outcome records bind all evidence. Another controller can inspect, refuse while the owner is live, or clean up after exact owner death, but can never complete the old run. Task 4 stores watcher evidence completion; Task 6 stores the separately content-bound scenario verdict and retry decision.

**Tech Stack:** Python 3.12 standard library, Windows Kernel32 through `ctypes`, canonical JSON/NDJSON, SHA-256 content identities, `unittest`, Git.

**Spec:** `docs/superpowers/specs/2026-09-01-fail-closed-containment-watch-design.md`

## Global Constraints

- Work only in `C:\Users\red\Desktop\Modlab\.worktrees\mo2-containment-spike` on `feature/mo2-containment-spike`.
- Start production/test replacement from the reviewed Task 4 source at commit `4d44130`; keep later design commits `c0abe07` and `3cb757a` in branch history.
- Preserve the current rejected round-5 WIP as an ignored local binary patch and verify that it applies cleanly before restoring either modified tracked file.
- Python 3.12 standard library only; do not add a package or service.
- Windows worker launch remains `sys.executable`, `-B`, `-m`, `modlab.validation.windows_watch`, `--worker`, canonical request path, with an argument array and `shell=False`.
- Only the uninterrupted launching controller process may publish `evidenceCompletion=Completed`.
- Controller crash, owner loss, uncertain identity, timeout, malformed evidence, unexpected worker exit, or evidence-critical close failure permanently makes watcher evidence Incomplete.
- Cleanup never promotes an interrupted run and never terminates MO2 or an unidentified process.
- Preserve exact request hashing, all eight logical roots, alias fan-out, no-follow root/guard handles, stable journal identity, overflow rejection, strict schemas, ordered rename pairs, exact worker PID plus creation time, and before/after manifests.
- Result precedence is Failed, then Incomplete, then Passed; a locally complete and content-bound positive breach proof survives unrelated evidence incompleteness.
- Same-command automatic retry is possible only before a durable `ScenarioStarted` boundary and only after cleanup proves the prior controller, watcher, and MO2 are absent.
- `CancelIoEx` is only a cancellation request. Every overlapped request must reach an observed completion before its buffer or `OVERLAPPED` storage is reused or freed.
- Quarantine uses an exact-handle, same-volume, no-replace rename. A collision refuses; there is no recursive copy/delete fallback.
- No production MO2, source mod, Play profile, game, Steam, save, or co-save path may be mutated by watcher cleanup or testing.
- Use `C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -B` for every test command.
- Each implementation task follows RED, minimal GREEN, focused regression, full affected-suite verification, commit, and fresh independent review.

## File Structure

- `modlab/validation/mo2_containment_model.py` — portable immutable Task 1/Task 6 values, including watcher evidence completion and recovery result types.
- `modlab/validation/mo2_containment_serialization.py` — strict canonical serialization and content IDs for watcher outcomes, scenario journals/results/recovery, and capability decisions.
- `modlab/validation/windows_watch_protocol.py` — pure Task 4 request, claim, launch, and receipt schemas plus durable no-replace publication; no native watch loop.
- `modlab/validation/windows_watch.py` — Windows native root monitoring, launching-controller liveness, same-controller lifecycle, cleanup-only recovery, and public façade/re-exports.
- `tests/test_mo2_containment_serialization.py` — portable type, cross-record binding, precedence, and impossible-combination tests.
- `tests/test_mo2_containment_watch_protocol.py` — cross-platform strict protocol and durable-publication tests.
- `tests/test_mo2_containment_watch.py` — native Windows worker, controller, failure, cleanup, and evidence tests.
- `docs/superpowers/plans/2026-09-01-mo2-prewrite-containment-capability-spike.md` — master-plan Task 1/4/6 interface and policy corrections.
- `docs/superpowers/reviews/2026-09-01-task-4-round-5-architecture-ruling.md` — tracked forensic summary of the rejected WIP and replacement ruling; the patch itself remains under the ignored Task 4 SDD `archives` directory.
- `.superpowers/sdd/2026-09-01-mo2-prewrite-containment-capability-spike/progress.md` — local execution ledger.

---

### Task 1: Archive the rejected fifth-round WIP and restore the reviewed source

**Files:**
- Create: `.superpowers/sdd/2026-09-01-mo2-prewrite-containment-capability-spike/archives/task-4-round-5-abandoned.patch` (ignored local forensic artifact)
- Create: `docs/superpowers/reviews/2026-09-01-task-4-round-5-architecture-ruling.md`
- Modify: `.superpowers/sdd/2026-09-01-mo2-prewrite-containment-capability-spike/progress.md`
- Restore from current `HEAD`: `modlab/validation/windows_watch.py`
- Restore from current `HEAD`: `tests/test_mo2_containment_watch.py`

**Interfaces:**
- Consumes: dirty source/test WIP based on reviewed commit `4d44130`, design commits `c0abe07` and `3cb757a`.
- Produces: a verified archival patch with SHA-256, a readable tracked ruling, and the exact reviewed Task 4 production/test baseline ready for replacement.

- [ ] **Step 1: Assert the expected branch, HEAD, and dirty scope**

Run:

```powershell
git branch --show-current
git log -1 --format=%s
git merge-base --is-ancestor 3cb757a HEAD
git status --short
```

Expected: branch `feature/mo2-containment-spike`, latest subject `docs: plan fail-closed watcher replacement`, ancestor check exit `0`, and exactly these dirty tracked files:

```text
 M modlab/validation/windows_watch.py
 M tests/test_mo2_containment_watch.py
```

Stop if any unrelated tracked path is dirty.

- [ ] **Step 2: Save the exact rejected diff without changing tracked files**

Run:

```powershell
New-Item -ItemType Directory -Force -Path '.superpowers\sdd\2026-09-01-mo2-prewrite-containment-capability-spike\archives'
git diff --binary --output='.superpowers/sdd/2026-09-01-mo2-prewrite-containment-capability-spike/archives/task-4-round-5-abandoned.patch' -- modlab/validation/windows_watch.py tests/test_mo2_containment_watch.py
(Get-FileHash -Algorithm SHA256 -LiteralPath '.superpowers\sdd\2026-09-01-mo2-prewrite-containment-capability-spike\archives\task-4-round-5-abandoned.patch').Hash.ToLowerInvariant()
```

Expected: a nonempty patch and one SHA-256 printed for the ledger/ruling.

- [ ] **Step 3: Prove the patch exactly reverses the current WIP**

Run:

```powershell
git apply --check --reverse -- '.superpowers/sdd/2026-09-01-mo2-prewrite-containment-capability-spike/archives/task-4-round-5-abandoned.patch'
```

Expected: exit `0` and no output. Do not restore files if this check fails.

- [ ] **Step 4: Restore only the two rejected WIP files**

Run:

```powershell
git restore --source=HEAD --worktree -- modlab/validation/windows_watch.py tests/test_mo2_containment_watch.py
```

Expected: the specification commits remain intact and the two source/test paths match reviewed Task 4 commit `4d44130` content.

- [ ] **Step 5: Prove the archived patch remains replayable**

Run:

```powershell
git apply --check -- '.superpowers/sdd/2026-09-01-mo2-prewrite-containment-capability-spike/archives/task-4-round-5-abandoned.patch'
git diff --exit-code 4d44130 -- modlab/validation/windows_watch.py tests/test_mo2_containment_watch.py
```

Expected: both commands exit `0` with no output.

- [ ] **Step 6: Run the restored focused baseline**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_containment_watch -v
```

Expected: 94 tests pass. A different count requires inspection before replacement work.

- [ ] **Step 7: Record the ruling and local patch identity**

Create `docs/superpowers/reviews/2026-09-01-task-4-round-5-architecture-ruling.md` with these exact sections and the observed lowercase patch SHA-256 from Step 2:

```markdown
# Task 4 Round 5 Architecture Ruling

**Baseline:** `4d44130`

**Rejected WIP base:** `4d44130`

**Local archive:** `.superpowers/sdd/2026-09-01-mo2-prewrite-containment-capability-spike/archives/task-4-round-5-abandoned.patch`

## Ruling

The multi-controller recovery-to-success protocol is rejected. Native handles are process-local and cannot be atomically transferred with filesystem owner state. Independent review continued to find cross-thread and cross-process promotion gaps after five controller correction rounds.

The replacement is the approved fail-closed design in `docs/superpowers/specs/2026-09-01-fail-closed-containment-watch-design.md`: interruption remains permanently incomplete, cleanup is non-promoting, and a fresh run is required.

## Preserved work

Reviewed root, alias, guard-chain, journal, cancellation, strict-schema, process-identity, and manifest evidence remains eligible for reuse. The archived round-5 diff is forensic evidence only and must not be merged wholesale.
```

Append the exact lowercase SHA-256 printed in Step 2 and the same ruling to the local progress ledger. The tracked ruling intentionally records the stable archive path but not a machine-local patch hash.

- [ ] **Step 8: Commit the tracked ruling only**

Run:

```powershell
git add docs/superpowers/reviews/2026-09-01-task-4-round-5-architecture-ruling.md
git commit -m "docs: record rejected watcher recovery architecture"
```

Expected: the ignored patch and ledger are not staged; tracked source/test remain clean.

---

### Task 2: Revise the portable containment evidence contracts

**Files:**
- Modify: `modlab/validation/mo2_containment_model.py`
- Modify: `modlab/validation/mo2_containment_serialization.py`
- Modify: `tests/test_mo2_containment_serialization.py`

**Interfaces:**
- Consumes: existing portable `ContainmentScenario`, `ScenarioJournal`, `ScenarioResult`, and `CapabilityDecision` values.
- Produces: `WatchEvidenceCompletion`, `WatchOutcome`, `watch_outcome_to_bytes()`, `watch_outcome_from_bytes()`, `watch_outcome_id_for()`, revised `ScenarioState`, revised `ScenarioResult`, `ScenarioCleanupStatus`, `ScenarioRecovery`, and strict serializers/content IDs consumed by Tasks 3–6 and the future Task 6 service.

- [ ] **Step 1: Write failing model and canonical round-trip tests**

Add tests that import and construct these exact portable types:

```python
class WatchEvidenceCompletion(StrEnum):
    COMPLETED = "Completed"
    INCOMPLETE = "Incomplete"

class ScenarioCleanupStatus(StrEnum):
    SUCCEEDED = "Succeeded"
    REFUSED = "Refused"

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
class ScenarioRecovery:
    schema_version: int
    run_id: str
    scenario: ContainmentScenario
    journal_id: str
    result_id: str | None
    cleanup_status: ScenarioCleanupStatus
    fresh_run_permitted: bool
    blockers: tuple[str, ...]
```

Require `ScenarioState.SCENARIO_STARTED = "ScenarioStarted"`. Replace the current `ScenarioResult` with this exact field order; `watcher_complete` is removed because it would duplicate authority:

```python
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
    projection_payload_bytes_copied: int
    production_backup_names: tuple[str, ...]
    staging_new_names: tuple[str, ...]
    staging_output_names: tuple[str, ...]
    adopted_name: str | None
    adopted_tree: TreeIdentity | None
    adopted_integrity: IntegrityObservation | None
    source_restored_after_quarantine: bool
    reasons: tuple[str, ...]
```

Tests must prove:

```python
self.assertEqual(value, watch_outcome_from_bytes(watch_outcome_to_bytes(value)))
self.assertEqual(
    "watch-outcome-sha256:" + hashlib.sha256(watch_outcome_to_bytes(value)).hexdigest(),
    watch_outcome_id_for(value),
)
self.assertEqual(value, scenario_recovery_from_bytes(scenario_recovery_to_bytes(value)))
```

Update the existing `valid_scenario_result()` helper to accept a bound `WatchOutcome`. Add these fixed helpers beside it so every later test name is defined:

```python
WATCH_ROOT_KINDS = (
    "SourceMods", "LabProfile", "PlayProfile", "Downloads",
    "Overwrite", "BoundedGame", "ExternalLocalLow", "ExternalTempLow",
)

def forbidden_play_event() -> WatcherEvent:
    return WatcherEvent(1, "PlayProfile", "Modified", "modlist.txt")

def valid_watch_outcome(
    *,
    events: tuple[WatcherEvent, ...] = (),
) -> WatchOutcome:
    event_bytes = b"".join(
        (json.dumps({
            "action": event.action,
            "relativePath": event.relative_path,
            "rootKind": event.root_kind,
            "sequence": event.sequence,
        }, sort_keys=True, separators=(",", ":")) + "\n").encode()
        for event in events
    )
    return WatchOutcome(
        schema_version=1,
        request_id="watch-request:" + "1" * 64,
        request_sha256="2" * 64,
        session_id="watch-session:" + "3" * 64,
        run_id="containment-run:0123456789abcdef0123456789abcdef",
        scenario=ContainmentScenario.MERGE_EXISTING,
        controller_pid=41,
        controller_creation_time=1001,
        worker_pid=42,
        worker_creation_time=1002,
        evidence_completion=WatchEvidenceCompletion.COMPLETED,
        worker_exit_code=0,
        ready=True,
        opened_root_kinds=WATCH_ROOT_KINDS,
        events=events,
        event_bytes_sha256=hashlib.sha256(event_bytes).hexdigest(),
        journal_volume_serial=7,
        journal_file_id=8,
        journal_byte_count=len(event_bytes),
        journal_event_count=len(events),
        journal_final_sequence=events[-1].sequence if events else 0,
        terminal_bytes_sha256="4" * 64,
        root_identities_unchanged=True,
        reason_codes=(),
    )

def incomplete_watch_outcome(
    *,
    events: tuple[WatcherEvent, ...] = (),
) -> WatchOutcome:
    return replace(
        valid_watch_outcome(events=events),
        evidence_completion=WatchEvidenceCompletion.INCOMPLETE,
        worker_exit_code=None,
        reason_codes=("controller-session-lost",),
    )

def failed_result_for(
    outcome: WatchOutcome,
    *,
    reasons: tuple[str, ...],
) -> ScenarioResult:
    return replace(
        valid_scenario_result(outcome.scenario, outcome),
        outcome=ScenarioOutcome.FAILED,
        watch_evidence_completion=outcome.evidence_completion,
        watcher_events=outcome.events,
        scenario_started=True,
        fresh_retry_eligible=False,
        reasons=reasons,
    )
```

- [ ] **Step 2: Run the portable tests and verify RED**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_containment_serialization -v
```

Expected: import or constructor failures for the new types/fields.

- [ ] **Step 3: Implement the immutable values and exact serializers**

Add exact-field canonical serializers:

```python
def watch_outcome_to_bytes(value: WatchOutcome) -> bytes:
    return _bytes(watch_outcome_to_dict(value))

def watch_outcome_from_bytes(data: bytes) -> WatchOutcome:
    return watch_outcome_from_dict(_decode(data, "watch outcome"))

def watch_outcome_id_for(value: WatchOutcome) -> str:
    return "watch-outcome-sha256:" + hashlib.sha256(
        watch_outcome_to_bytes(value)
    ).hexdigest()

def scenario_recovery_to_bytes(value: ScenarioRecovery) -> bytes:
    return _bytes(scenario_recovery_to_dict(value))

def scenario_recovery_from_bytes(data: bytes) -> ScenarioRecovery:
    return scenario_recovery_from_dict(_decode(data, "scenario recovery"))
```

Use exact patterns:

```python
_WATCH_OUTCOME_ID = re.compile(r"^watch-outcome-sha256:[0-9a-f]{64}$")
_WATCH_REQUEST_ID = re.compile(r"^watch-request:[0-9a-f]{64}$")
_WATCH_SESSION_ID = re.compile(r"^watch-session:[0-9a-f]{64}$")
_JOURNAL_ID = re.compile(r"^containment-journal-sha256:[0-9a-f]{64}$")
```

Enforce these local invariants:

- `Armed` requires a positive monitor PID and no MO2 PID; `ScenarioStarted` requires the same and is durably written before `CreateProcess`; `Launched` and `Captured` require both positive monitor and MO2 PIDs; `RecoveryRequired` requires an error. No other journal state may record an error.
- `Completed` watcher evidence requires exit code `0`, ready true, full eight-kind coverage, root identities unchanged, empty reason codes, canonical ordered events, matching event hash/count/final sequence, and nonzero journal identity values.
- `Incomplete` watcher evidence requires at least one reason code and permits a missing exit code.
- `Passed` scenario requires `watch_evidence_completion=Completed`, `scenario_started=True`, `fresh_retry_eligible=False`, no events, identical protected states, and every existing pass invariant.
- `Failed` requires reasons, `scenario_started=True`, `fresh_retry_eligible=False`, and a local positive breach signal: nonempty canonical watcher events or a protected before/after mismatch. It may bind Completed or Incomplete watcher evidence.
- `Incomplete` requires reasons. `fresh_retry_eligible=True` requires `scenario_started=False`, `watch_evidence_completion=Incomplete`, and cleanup proof supplied later by Task 6; the portable value cannot independently assert cleanup success.
- `ScenarioRecovery.fresh_run_permitted=True` requires `cleanup_status=Succeeded`, no blockers, and a non-null immutable incomplete `result_id`.
- `ScenarioRecovery.cleanup_status=Refused` requires blockers and forbids fresh-run permission.

Change `scenario_result_to_bytes()` and `scenario_result_from_bytes()` to accept a required corresponding `WatchOutcome` for cross-record validation, mirroring the existing `CapabilityDecision`/scenario-results pattern:

```python
def scenario_result_to_bytes(
    value: ScenarioResult,
    watch_outcome: WatchOutcome,
) -> bytes:
    checked = scenario_result_from_dict(_result_dict(value), watch_outcome)
    return _bytes(_result_dict(checked))

def scenario_result_from_bytes(
    data: bytes,
    watch_outcome: WatchOutcome,
) -> ScenarioResult:
    return scenario_result_from_dict(_decode(data, "scenario result"), watch_outcome)

def scenario_result_id_for(
    value: ScenarioResult,
    watch_outcome: WatchOutcome,
) -> str:
    return "containment-result-sha256:" + hashlib.sha256(
        scenario_result_to_bytes(value, watch_outcome)
    ).hexdigest()
```

Cross-record validation requires exact `watch_outcome_id`, run ID, scenario, evidence completion, event tuple, and protected request/session association supplied by the Task 6 store. Never accept a copied boolean as proof.

Change `capability_decision_to_bytes()`, `capability_decision_from_bytes()`, `capability_decision_id_for()`, and their deep validator to accept scenario results and an equal-length tuple of bound watcher outcomes. Recompute every result ID with its corresponding outcome; reject missing, reordered, run/scenario-mismatched, or content-ID-mismatched pairs.

- [ ] **Step 4: Add precedence and impossible-combination regressions**

Add explicit cases:

```python
def test_incomplete_watch_with_bound_positive_event_may_be_failed(self):
    outcome = incomplete_watch_outcome(events=(forbidden_play_event(),))
    result = failed_result_for(outcome, reasons=("forbidden-play-profile-mutation",))
    encoded = scenario_result_to_bytes(result, outcome)
    self.assertEqual(result, scenario_result_from_bytes(encoded, outcome))

def test_incomplete_watch_without_positive_breach_cannot_be_failed(self):
    outcome = incomplete_watch_outcome(events=())
    result = failed_result_for(outcome, reasons=("terminal-missing",))
    with self.assertRaisesRegex(ContainmentFormatError, "positive breach"):
        scenario_result_to_bytes(result, outcome)

def test_retry_eligibility_requires_pre_scenario_incomplete(self):
    outcome = incomplete_watch_outcome()
    value = replace(
        valid_scenario_result(outcome.scenario, outcome),
        outcome=ScenarioOutcome.INCOMPLETE,
        watch_evidence_completion=WatchEvidenceCompletion.INCOMPLETE,
        scenario_started=False,
        fresh_retry_eligible=True,
        reasons=("controller-exited-before-scenario",),
    )
    self.assertEqual(value, scenario_result_from_bytes(
        scenario_result_to_bytes(value, outcome),
        outcome,
    ))
    with self.assertRaises(ContainmentFormatError):
        scenario_result_to_bytes(replace(value, scenario_started=True), outcome)
```

Also reject outcome-ID replay, run/scenario mismatch, copied completion divergence, Passed with Incomplete evidence, Failed without positive proof, fresh retry after `ScenarioStarted`, cleanup refusal with permission true, duplicate reason codes, bool-as-int, and extra fields.

- [ ] **Step 5: Run focused and dependent serialization suites**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_containment_serialization -v
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest discover -s tests -p 'test_mo2_containment_*.py' -v
```

Expected: PASS. Update every existing helper/caller to supply the bound `WatchOutcome`; do not add a default that bypasses cross-record checking.

- [ ] **Step 6: Commit**

Run:

```powershell
git add modlab/validation/mo2_containment_model.py modlab/validation/mo2_containment_serialization.py tests/test_mo2_containment_serialization.py
git commit -m "feat: separate watcher evidence from scenario verdicts"
```

---

### Task 3: Extract strict fail-closed watcher protocol records

**Files:**
- Create: `modlab/validation/windows_watch_protocol.py`
- Create: `tests/test_mo2_containment_watch_protocol.py`
- Modify: `modlab/validation/windows_watch.py`
- Modify: `tests/test_mo2_containment_watch.py`

**Interfaces:**
- Consumes: `ContainmentScenario`, `WatchEvidenceCompletion`, `WatchOutcome`, and outcome serialization from Task 2.
- Produces: re-exported `WatchRoot`, `WatchRequest`, `WatchReceipt`, immutable `ControllerClaim`, immutable `WorkerLaunch`, immutable `ControllerLoss`, canonical serializers, strict parsers, `publish_new_verified()`, and fixed protocol paths used by worker/controller tasks.

- [ ] **Step 1: Write failing pure protocol tests**

Create tests for these exact values:

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
class ControllerClaim:
    schema_version: int
    request_sha256: str
    session_id: str
    run_id: str
    scenario: ContainmentScenario
    controller_pid: int
    controller_creation_time: int

@dataclass(frozen=True)
class WorkerLaunch:
    schema_version: int
    request_sha256: str
    session_id: str
    run_id: str
    scenario: ContainmentScenario
    worker_pid: int
    worker_creation_time: int

@dataclass(frozen=True)
class ControllerLoss:
    schema_version: int
    request_sha256: str
    session_id: str
    run_id: str
    scenario: ContainmentScenario
    controller_pid: int
    controller_creation_time: int
    worker_pid: int
    worker_creation_time: int
    reason_code: str

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

Verify exact fields, canonical path spelling, bool-not-int integers, all eight logical roots, alias-preserving request serialization, session/run/scenario binding, claim/launch/loss mismatch rejection, fixed controller-loss reason `controller-session-lost`, and `complete` as a derived property.

- [ ] **Step 2: Run the pure protocol module and verify RED**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_containment_watch_protocol -v
```

Expected: import failure because `windows_watch_protocol.py` does not exist.

- [ ] **Step 3: Implement protocol schemas and durable publication**

Move only pure schema/canonicalization behavior from `windows_watch.py`; keep native Kernel32 calls in `windows_watch.py`. Define fixed names:

```python
REQUEST_NAME = "request.json"
CLAIM_NAME = "controller-claim.json"
LAUNCH_NAME = "worker-launch.json"
READY_NAME = "ready.json"
EVENTS_NAME = "events.ndjson"
TERMINAL_NAME = "terminal.json"
CONTROLLER_LOSS_NAME = "controller-loss.json"
OUTCOME_NAME = "outcome.json"
STOP_NAME = "stop.token"
```

Implement one durable create-only primitive:

```python
from collections.abc import Callable

def publish_new_verified(
    path: Path,
    data: bytes,
    parse: Callable[[bytes], object],
) -> bytes:
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{secrets.token_hex(8)}.tmp"
    )
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_BINARY, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError(f"incomplete write for {path}")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.rename(temporary, path)
    reloaded = path.read_bytes()
    parse(reloaded)
    if reloaded != data:
        raise WatchProtocolError(f"durable readback mismatch for {path.name}")
    return reloaded
```

On Windows, `os.rename()` supplies no-replace semantics. Every failure removes only the exact unpublished temporary file when its job-owned identity is still known. Do not use `os.replace()` for immutable records.

- [ ] **Step 4: Prove publication and binding failure directions**

Add tests that inject partial writes, `fsync` failure, close failure, destination collision, rename failure, readback mismatch, duplicate keys, extra fields, request replay under another run/scenario/session, and changed claim/launch identities. No dependent action may run after publication failure.

Add a test that imports `WatchRequest`, `WatchRoot`, `WatchReceipt`, and `ROOT_KINDS` from `modlab.validation.windows_watch` to prove the façade remains backward-compatible.

- [ ] **Step 5: Run focused protocol and restored watcher tests**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_containment_watch_protocol tests.test_mo2_containment_watch -v
```

Expected: PASS after updating test request helpers with exact run/scenario/session IDs. Do not change runtime ownership behavior in this task.

- [ ] **Step 6: Commit**

Run:

```powershell
git add modlab/validation/windows_watch_protocol.py modlab/validation/windows_watch.py tests/test_mo2_containment_watch_protocol.py tests/test_mo2_containment_watch.py
git commit -m "refactor: isolate containment watch protocol"
```

---

### Task 4: Make the worker observe its controller and drain every native operation

**Files:**
- Modify: `modlab/validation/windows_watch.py`
- Modify: `tests/test_mo2_containment_watch.py`

**Interfaces:**
- Consumes: immutable request/claim/launch records from Task 3.
- Produces: `run_watch_worker(request_path: Path) -> int` with exact controller-liveness observation, complete cancellation draining, immutable terminal/controller-loss behavior, and documented exit codes `0`, `1`, and `2`.

- [ ] **Step 1: Write deterministic controller-death and cancellation RED tests**

Add a helper controller subprocess that starts a worker, publishes its claim, signals a test barrier, and exits without a stop token. Assert the worker:

- opens the exact controller PID and creation time before ready;
- detects the controller process handle signalling;
- cancels and drains every watcher request;
- writes terminal evidence with `complete=false` and reason `controller-session-lost`;
- exits with code `1`;
- never overwrites an existing terminal.

Add explicit cancellation cases:

```python
@dataclass(frozen=True)
class CancelDrainProof:
    every_completion_observed: bool
    storage_reused_before_completion: bool

for completion in (
    "operation-aborted",
    "normal-final-event",
    "cancel-not-found-after-completion",
):
    with self.subTest(completion=completion):
        proof = run_cancel_completion_fixture(completion)
        self.assertTrue(proof.every_completion_observed)
        self.assertFalse(proof.storage_reused_before_completion)
```

Inject another error completion and require worker exit `1`, incomplete terminal, and no buffer reuse.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_containment_watch.MutationWatchTests.test_worker_self_cancels_after_exact_controller_death tests.test_mo2_containment_watch.MutationWatchTests.test_cancel_io_completion_races_are_drained -v
```

Expected: failure because the restored worker does not own an exact launching-controller handle or expose the deterministic completion proof.

- [ ] **Step 3: Add exact launching-controller observation**

Before opening watched roots, the worker loads and validates `controller-claim.json`, then opens the exact controller with `SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION`, verifies PID plus creation time, and retains that handle through worker cleanup.

Define `_WAIT_FAILED = 0xFFFFFFFF` beside the existing wait constants and bind every added Kernel32 signature with exact `ctypes` argument/result types before use.

In the existing stop-poll loop, check both the stop token and controller handle:

```python
controller_wait = _kernel32.WaitForSingleObject(controller_handle, 0)
if controller_wait == _WAIT_OBJECT_0:
    errors.append("controller-session-lost")
    stopping.set()
elif controller_wait == _WAIT_FAILED:
    errors.append("controller-liveness-wait-failed")
    stopping.set()
```

Access denial, mismatch, wait failure, or controller-handle close failure makes terminal evidence incomplete. Never infer death from access denial.

- [ ] **Step 4: Centralize exact overlapped cancellation draining**

Implement one worker-owned function used by stop-token, controller-loss, setup-error, and exception unwinds:

```python
def _cancel_and_drain(state: _WatchState) -> None:
    if state.pending:
        cancelled = _kernel32.CancelIoEx(state.directory_handle, ctypes.byref(state.overlapped))
        if not cancelled and ctypes.get_last_error() != _ERROR_NOT_FOUND:
            state.fail(_winerror_text("watch cancellation failed"))
        wait_result = _kernel32.WaitForSingleObject(state.event_handle, _INFINITE)
        if wait_result != _WAIT_OBJECT_0:
            state.fail(f"watch completion wait failed: {wait_result}")
            return
        state.consume_completion_after_stop()
```

`consume_completion_after_stop()` handles normal final data, `ERROR_OPERATION_ABORTED`, and every other status distinctly. It processes a normal final event before journal finalization. The worker cannot close the directory/event handles, free the buffer, or leave the thread until `state.pending` is false from an observed completion.

Define `consume_completion_after_stop()` in this task. It calls `_kernel32.GetOverlappedResult(state.directory_handle, ctypes.byref(state.overlapped), ctypes.byref(transferred), False)` exactly once after the event signals, treats a successful byte count as final notification data, treats `ERROR_OPERATION_ABORTED` as the expected intentional-stop completion, and records every other error as incomplete before setting `pending=False`. Define the test helper `run_cancel_completion_fixture(completion: str) -> CancelDrainProof` in `tests/test_mo2_containment_watch.py`; it injects the exact Kernel32 return sequence for the requested case and returns the proof above.

- [ ] **Step 5: Make terminal and exit behavior exact**

Use this contract:

- return `0` only after ready publication, complete root/guard/journal cleanup, exact terminal publication, and no worker protocol error;
- return `1` after an exact incomplete terminal is published;
- return `2` when request/claim parsing or terminal publication prevents a trustworthy terminal.

If controller loss is observed before terminal publication, include it in the one terminal. If an immutable terminal already exists, write at most one separate immutable `controller-loss.json`; never replace terminal bytes. A controller dying after worker exit is handled by absent outcome inspection, not terminal replacement.

- [ ] **Step 6: Run the complete worker/evidence subset**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_containment_watch -v
```

Expected: PASS for retained root, alias, guard-chain, journal, overflow, rename-pair, malformed-record, and new controller/cancellation cases. Recovery-to-success tests removed later in Task 5 may remain failing only if they encode superseded behavior; update or remove them in Task 5, not by weakening this worker.

- [ ] **Step 7: Commit**

Run:

```powershell
git add modlab/validation/windows_watch.py tests/test_mo2_containment_watch.py
git commit -m "feat: make watcher fail closed with its controller"
```

---

### Task 5: Replace cross-process promotion with one controller session and cleanup-only recovery

**Files:**
- Modify: `modlab/validation/windows_watch.py`
- Modify: `tests/test_mo2_containment_watch.py`

**Interfaces:**
- Consumes: exact worker exit contract and protocol records from Tasks 3–4.
- Produces: `start_watch(request: WatchRequest) -> int`, `stop_watch(request_path: Path) -> WatchReceipt`, process-local `_LocalWatchSession`, live-owner refusal, dead-owner cleanup-only recovery, immutable `WatchOutcome`, and no recovery-to-success path.

- [ ] **Step 1: Write same-controller completion RED tests**

Add deterministic barriers proving this exact success order:

```text
controller claim durable
worker exact handle retained
worker launch record durable
ready bound and valid
stop token created
worker handle WAIT_OBJECT_0
GetExitCodeProcess returns 0
same handle PID/creation identity matches
terminal/journal evidence captured
all evidence-critical handles close
Completed outcome published from captured material
outcome reloaded for reporting
```

Tests inject each failure separately and require Incomplete with no later promotion: wait timeout/failure, `GetExitCodeProcess` failure, `STILL_ACTIVE`, nonzero exit, injected unhandled exception after terminal publication, post-signal identity mismatch, evidence capture failure, process/evidence handle close failure, outcome write/flush/rename/readback failure.

- [ ] **Step 2: Write non-owner cleanup RED tests**

Use real controller subprocesses and assert:

```python
def test_non_owner_refuses_without_mutation_while_controller_is_live(self):
    before = self.protocol_bytes()
    receipt = stop_watch(self.request_path)
    self.assertFalse(receipt.complete)
    self.assertIn("claimed controller is live", receipt.error)
    self.assertEqual(before, self.protocol_bytes())
    self.assertFalse(self.stop_token.exists())

def test_non_owner_after_exact_controller_death_is_cleanup_only(self):
    self.terminate_exact_controller_fixture()
    receipt = stop_watch(self.request_path)
    self.assertFalse(receipt.complete)
    self.assertEqual("Incomplete", self.load_outcome().evidence_completion.value)
    self.assertTrue(self.worker_exit_was_proved_or_cleanup_refused())
```

Define the fixture helpers in the same test class:

```python
def protocol_bytes(self) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in sorted(self.evidence.iterdir(), key=lambda item: item.name)
        if path.is_file()
    }

def terminate_exact_controller_fixture(self) -> None:
    controller = self.external_controller
    self.assertTrue(windows_watch._kernel32.TerminateProcess(controller._handle, 73))
    self.assertEqual(73, controller.wait(timeout=10))

def load_outcome(self) -> WatchOutcome:
    return watch_outcome_from_bytes((self.evidence / OUTCOME_NAME).read_bytes())

def worker_exit_was_proved_or_cleanup_refused(self) -> bool:
    outcome = self.load_outcome()
    return (
        self.worker_exit_marker.exists()
        or "worker-identity-uncertain" in outcome.reason_codes
        or "worker-cleanup-refused" in outcome.reason_codes
    )
```

The external-controller helper publishes a ready marker only after the controller claim and worker launch are durable. The parent waits for that marker, terminates that exact retained controller handle with injected exit code `73`, and waits for the same handle before invoking cleanup, so launch failure cannot masquerade as controller death. `self.worker_exit_marker` is a test-only `threading.Event` set by a wrapper around the exact worker-handle wait only when it returns `WAIT_OBJECT_0`; refusal cases instead require one of the two exact reason codes shown above.

Add access-denied, PID-reuse, creation-time mismatch, unknown liveness, live MO2, and two concurrent non-owner cases. Uncertainty must make no stop-token, outcome, quarantine, or process mutation.

- [ ] **Step 3: Run targeted tests and verify RED**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_containment_watch.MutationWatchTests.test_same_controller_requires_exact_zero_exit_before_completion tests.test_mo2_containment_watch.MutationWatchTests.test_non_owner_refuses_without_mutation_while_controller_is_live tests.test_mo2_containment_watch.MutationWatchTests.test_non_owner_after_exact_controller_death_is_cleanup_only -v
```

Expected: failures because the restored code closes launcher handles and permits cross-process recovery promotion.

- [ ] **Step 4: Implement one process-local completion session**

Replace `_LocalWorker`, owner-state promotion, retained auxiliary registries, named stop mutexes, and observer/retainer identities with:

```python
@dataclass
class _LocalWatchSession:
    request: WatchRequest
    request_sha256: str
    controller_pid: int
    controller_creation_time: int
    worker_pid: int
    worker_creation_time: int
    process: subprocess.Popen[bytes]
    lock: threading.Lock
```

Store it in `_LOCAL_SESSIONS` keyed by canonical request path. `subprocess.Popen._handle` is the one retained exact worker process handle; do not detach it and do not open a duplicate normal-operation handle. Validate it immediately with PID and creation time. Python already closes the primary-thread handle inside `Popen`.

`start_watch()` publishes the immutable controller claim before spawn and immutable worker launch after exact handle verification. It returns only after matching ready evidence. Any later publication/readiness failure writes or attempts an Incomplete outcome, creates the stop token only for its own exact worker, waits boundedly, and never exposes completion authority to another controller.

- [ ] **Step 5: Implement exact same-controller stop and outcome publication**

Under the session's process-local lock:

1. revalidate request, claim, launch, local session, PID, creation time, run, scenario, and session ID;
2. create the exact idempotent stop token;
3. wait on `Popen._handle` and require `WAIT_OBJECT_0`;
4. call `GetExitCodeProcess`, reject `STILL_ACTIVE`, and require exit `0`;
5. revalidate PID and creation time through the retained handle;
6. open no-follow evidence handles, verify and capture canonical ready/terminal/journal material and event snapshot;
7. close every evidence-critical evidence/process handle successfully;
8. construct a `WatchOutcome` with the exact captured request/session/run/scenario/controller/worker identities, `evidence_completion=WatchEvidenceCompletion.COMPLETED`, `worker_exit_code=0`, captured roots/events/journal/terminal hashes, and empty reason codes;
9. write, flush, close, reopen through an exact no-follow handle, parse, and byte-verify a private outcome candidate; then use one no-replace `MoveFileExW(..., MOVEFILE_WRITE_THROUGH)` operation as the sole `outcome.json` commit point and return a receipt derived from the already verified outcome.

Any exception before the Step 9 commit constructs or later permits only Incomplete. No fallible verification occurs after the commit point: failure before the move leaves `Completed` unpublished, while a successful write-through move means every evidence and handle check already passed. No later controller may reconstruct completion from terminal files.

- [ ] **Step 6: Implement non-owner policy without cross-process promotion**

When no matching local session exists:

- exact claimed controller live: return read-only refusal; do not create token/outcome/quarantine;
- exact claimed controller dead: atomically publish or infer Incomplete, then reopen the exact recorded worker solely for stop/wait cleanup;
- controller identity or liveness uncertain: return Incomplete refusal without cleanup mutation;
- worker PID/creation mismatch or access denial: refuse process interaction and preserve the stale run;
- outcome already Incomplete: cleanup may be retried but can never change completion;
- outcome already Completed: report the immutable outcome; do not reopen authority-bearing evidence.

Delete durable states `Starting`, `Running`, `StopArmed`, `ExitObserved`, `CleanupComplete`, recoverable `RecoveryRequired`, `Completed` owner promotion, `_LOCAL_WORKERS`, retained-handle maps, and Task 4 named mutex code. Keep only worker-side handle accounting and local error cleanup required to produce honest terminal evidence.

- [ ] **Step 7: Replace obsolete recovery tests with fail-closed tests**

Remove tests whose asserted success depends on another controller promoting `Running`, `StopArmed`, `ExitObserved`, `CleanupComplete`, or `RecoveryRequired`. Preserve their fault injectors when they still prove an Incomplete or cleanup-refusal direction.

The final lifecycle matrix must contain:

- normal same-controller Completed;
- external live-owner refusal with byte-for-byte protocol immutability;
- dead-owner cleanup-only Incomplete;
- controller crash before ready, before token, after token, after terminal, and before outcome;
- nonzero and unhandled-exception worker exits after terminal;
- identity uncertainty/PID reuse refusal;
- evidence-critical close failure permanently Incomplete;
- missing/malformed claim, launch, terminal, journal, outcome, and request;
- two processes racing cleanup without either publishing Completed.

- [ ] **Step 8: Run focused and full tests**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_containment_watch_protocol tests.test_mo2_containment_watch -v
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest discover -s tests -v
```

Expected: PASS with only documented pre-existing environment skips. Search for forbidden recovery machinery:

```powershell
rg -n "StopArmed|ExitObserved|CleanupComplete|_LOCAL_WORKERS|_RETAINED_AUXILIARY_HANDLES|_stop_mutex|RecoveryRequired.*Completed" modlab/validation/windows_watch.py tests/test_mo2_containment_watch.py
```

Expected: no matches except explicit comments/tests asserting absence; remove those comments if they create ambiguous matches.

- [ ] **Step 9: Commit**

Run:

```powershell
git add modlab/validation/windows_watch.py tests/test_mo2_containment_watch.py
git commit -m "fix: make watcher completion single-controller only"
```

---

### Task 6: Bind receipt reconstruction to immutable captured outcomes

**Files:**
- Modify: `modlab/validation/windows_watch.py`
- Modify: `tests/test_mo2_containment_watch.py`
- Modify: `tests/test_mo2_containment_watch_protocol.py`

**Interfaces:**
- Consumes: immutable `WatchOutcome` and same-controller lifecycle from Tasks 2–5.
- Produces: `watch_receipt_from_files(request: WatchRequest, worker_pid: int, ready_path: Path, events_path: Path, terminal_path: Path) -> WatchReceipt` that treats the outcome snapshot as authority, and `watch_proves_unchanged(receipt: WatchReceipt, before: ProtectedState, after: ProtectedState) -> bool` that cannot conflate complete evidence with scenario Pass.

- [ ] **Step 1: Write reconstruction RED tests**

Add tests proving:

- a valid Completed outcome reconstructs the exact captured receipt without opening terminal/journal/root authority;
- an Incomplete outcome always reconstructs `complete=False`, even with a valid terminal and matching final manifests;
- absent/malformed/replayed outcome returns incomplete;
- outcome request/session/run/scenario/worker identity or content-ID mismatch returns incomplete;
- a complete receipt containing a forbidden event remains evidence-complete but `watch_proves_unchanged()` returns false;
- a post-publication reporting read-handle close warning is diagnostic and cannot manufacture or reverse completion.

Instrument every native evidence-open function and assert no authority-bearing call occurs after Completed publication.

- [ ] **Step 2: Run targeted tests and verify RED**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_containment_watch.MutationWatchTests.test_completed_outcome_reconstructs_without_evidence_reopen tests.test_mo2_containment_watch.MutationWatchTests.test_complete_mutation_evidence_is_not_unchanged -v
```

Expected: failures because the restored reconstruction reads mutable protocol files and uses the old owner state.

- [ ] **Step 3: Reconstruct exclusively from the outcome snapshot**

`watch_receipt_from_files()` still validates that supplied ready/events/terminal paths are the exact confined protocol paths, but Completed authority comes only from the verified immutable outcome snapshot. It parses `WatchOutcome`, recomputes its content ID, verifies the request hash plus session/run/scenario/worker identity, and constructs `WatchReceipt` from embedded captured events and hashes.

Do not reopen the event journal, terminal, roots, process, or manifests to create completion authority. Missing/malformed outcome, Incomplete outcome, or any binding mismatch returns an incomplete receipt with an exact reason.

- [ ] **Step 4: Keep unchanged proof strict and verdict-neutral**

`watch_proves_unchanged()` returns true only when:

```python
return (
    receipt.evidence_completion is WatchEvidenceCompletion.COMPLETED
    and receipt.ready
    and receipt.error is None
    and receipt.opened_root_kinds == ROOT_KINDS
    and receipt.worker_exit_code == 0
    and receipt.events == ()
    and receipt.event_bytes_sha256 == hashlib.sha256(b"").hexdigest()
    and before == after
)
```

Task 4 does not emit Passed/Failed. Task 6 separately maps complete event/manifests to the scenario outcome using the precedence rules.

- [ ] **Step 5: Run focused and full tests**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_containment_watch_protocol tests.test_mo2_containment_watch tests.test_mo2_containment_serialization -v
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest discover -s tests -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```powershell
git add modlab/validation/windows_watch.py tests/test_mo2_containment_watch.py tests/test_mo2_containment_watch_protocol.py
git commit -m "fix: bind watcher receipts to immutable outcomes"
```

---

### Task 7: Make the master Task 6 contract implement the fail-closed boundary

**Files:**
- Modify: `docs/superpowers/plans/2026-09-01-mo2-prewrite-containment-capability-spike.md`
- Modify: `.superpowers/sdd/2026-09-01-mo2-prewrite-containment-capability-spike/progress.md`
- Test: `tests/test_mo2_containment_serialization.py`

**Interfaces:**
- Consumes: reviewed Task 4 `WatchOutcome`, revised `ScenarioJournal`, `ScenarioResult`, and `ScenarioRecovery` types.
- Produces: an exact future Task 6 implementation contract that cannot retry or adjudicate from an unbound watcher boolean and cannot start fresh while stale liveness is unresolved.

- [ ] **Step 1: Replace stale master-plan interfaces**

In the master plan:

- add `WatchEvidenceCompletion`, `WatchOutcome`, `ScenarioCleanupStatus`, `ScenarioRecovery`, and `ScenarioState.SCENARIO_STARTED` to Task 1;
- replace `ScenarioResult.watcher_complete` with `watch_outcome_id`, `watch_evidence_completion`, `scenario_started`, and `fresh_retry_eligible`;
- change `recover_scenario(validation_root: Path, run_id: str, scenario: ContainmentScenario) -> ScenarioJournal` to `recover_scenario(validation_root: Path, run_id: str, scenario: ContainmentScenario) -> ScenarioRecovery`;
- replace Task 4's recoverable owner-state description with a link to this replacement plan and the approved spec;
- require `arm_scenario()` to stop at watcher-ready, then durably transition to `ScenarioStarted` immediately before `CreateProcess` launches MO2;
- require `capture_scenario()` to load the exact watcher outcome by content ID and verify request/session/run/scenario bindings;
- define outcome precedence Failed, Incomplete, Passed;
- allow Failed plus Incomplete watcher evidence only for a bound positive event or protected-state delta;
- permit one same-command retry only before `ScenarioStarted`, after successful cleanup and proved process absence;
- forbid fresh-run launch after cleanup refusal, access denial, identity uncertainty, live MO2, or unverified watcher liveness;
- require every earlier result to remain visible and immutable after a later explicit clean run.

- [ ] **Step 2: Replace stale Task 6 policy tests in the plan**

Add exact future tests:

```python
def test_valid_forbidden_event_then_controller_death_is_failed_without_retry():
    result = capture_interrupted_with_valid_event("PlayProfile")
    self.assertEqual(ScenarioOutcome.FAILED, result.outcome)
    self.assertEqual(WatchEvidenceCompletion.INCOMPLETE, result.watch_evidence_completion)
    self.assertFalse(result.fresh_retry_eligible)

def test_pre_scenario_interruption_may_retry_once_after_cleanup():
    recovery = recover_pre_scenario_interruption()
    self.assertEqual(ScenarioCleanupStatus.SUCCEEDED, recovery.cleanup_status)
    self.assertTrue(recovery.fresh_run_permitted)
    self.assertNotEqual(old_run_id(), fresh_run_id())

def test_cleanup_refusal_never_starts_fresh_run():
    recovery = recover_with_uncertain_watcher_identity()
    self.assertEqual(ScenarioCleanupStatus.REFUSED, recovery.cleanup_status)
    self.assertFalse(recovery.fresh_run_permitted)
    self.assertEqual(0, fresh_launch_count())
```

Define those future fixture contracts in the master plan's Task 5 support-module step: `capture_interrupted_with_valid_event(root_kind: str) -> ScenarioResult` creates one request-bound event then kills the exact controller; `recover_pre_scenario_interruption() -> ScenarioRecovery` interrupts before the durable boundary and proves cleanup; `recover_with_uncertain_watcher_identity() -> ScenarioRecovery` injects access denial and records refusal; `old_run_id() -> str` and `fresh_run_id() -> str` return the two persisted run identities; `fresh_launch_count() -> int` returns the fake process launch counter. Task 5 must implement these deterministic disposable fixtures before Task 6 consumes them.

Also specify protected-manifest delta plus damaged terminal => Failed/no retry; malformed event after started => Incomplete/no retry; manual clean run after Failed preserves both result IDs; adjudication rejects Failed and does not hide it behind later Passed.

- [ ] **Step 3: Run current portable and watcher suites**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_containment_serialization tests.test_mo2_containment_watch_protocol tests.test_mo2_containment_watch -v
```

Expected: PASS. This step verifies the already implemented contract; Task 6 service tests remain future RED tests until the master plan reaches Task 6 after Task 5 fixtures.

- [ ] **Step 4: Update the local execution ledger**

Record:

- rejected round-5 patch SHA-256 and reviewed base;
- fail-closed design commits `c0abe07` and `3cb757a`;
- each replacement implementation commit;
- focused/full test commands and exact counts;
- removed recovery-to-success states and tests;
- independent review verdicts and remaining blockers.

- [ ] **Step 5: Commit the master-plan correction**

Run:

```powershell
git add docs/superpowers/plans/2026-09-01-mo2-prewrite-containment-capability-spike.md
git commit -m "docs: bind containment orchestration to fail-closed evidence"
```

---

### Task 8: Verify and independently review the complete replacement

**Files:**
- Review: every file changed from the Task 1 implementation baseline through Task 7
- Modify only if review identifies a verified issue in the approved scope

**Interfaces:**
- Consumes: Tasks 1–7 commits.
- Produces: a clean Task 4 replacement gate and an unambiguous handoff back to master-plan Task 5.

- [ ] **Step 1: Run formatting, forbidden-state, and dirty-scope checks**

Run:

```powershell
git diff --check 3cb757a..HEAD
rg -n "StopArmed|ExitObserved|CleanupComplete|_LOCAL_WORKERS|_RETAINED_AUXILIARY_HANDLES|_stop_mutex|RecoveryRequired.*Completed" modlab/validation/windows_watch.py tests/test_mo2_containment_watch.py
git status --short
```

Expected: no whitespace errors, no forbidden recovery machinery, and a clean tracked worktree. The ignored forensic patch and ledger may remain.

- [ ] **Step 2: Run all focused replacement suites**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_containment_serialization tests.test_mo2_containment_watch_protocol tests.test_mo2_containment_watch -v
```

Expected: PASS.

- [ ] **Step 3: Run the complete repository suite**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest discover -s tests -v
```

Expected: PASS with only documented environment skips.

- [ ] **Step 4: Perform a fresh independent whole-diff review**

Review from the Task 1 ruling commit through `HEAD` against:

- `docs/superpowers/specs/2026-09-01-fail-closed-containment-watch-design.md`;
- this implementation plan;
- preserved Task 4 root/journal/cancellation evidence requirements;
- Pro's accepted result-precedence, non-owner, exit-code, durability, and quarantine amendments.

Reject any Critical or Important issue. Specifically search for cross-process completion, copied-boolean authority, post-completion evidence reopen, automatic retry after `ScenarioStarted`, cleanup refusal followed by launch, mutation Fail hidden by Incomplete, unobserved cancellation completion, and unexpected exit accepted as success.

- [ ] **Step 5: Apply review fixes with bounded rounds**

For each verified finding, add a deterministic failing test, run it RED, implement one root-cause fix, run focused/full GREEN, commit, and request fresh re-review. Stop and question the architecture if three fixes expose new shared-state failures; do not recreate the rejected recovery protocol.

- [ ] **Step 6: Record the final gate**

Append the final focused/full counts, review verdict, commit range, and `Task 4 fail-closed replacement complete; master Task 5 unblocked` to the local progress ledger.

Do not push or merge in this task. Publication remains part of the complete containment-spike gate after Tasks 5–8 and live-machine validation.
