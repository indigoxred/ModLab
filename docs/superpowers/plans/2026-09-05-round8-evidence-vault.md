# Round 8 Evidence Vault Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Make new containment completion depend on protected evidence, complete process ownership, and controller-observed causal completion.

**Architecture:** A focused security helper protects fresh evidence directories while existing exact-object publication stays responsible for files. Extend the suspended launcher to transfer a process/job owner; the watcher and its callers use canonical causal records and one reconstruction path each.

**Tech Stack:** Python 3.12, standard library unittest, ctypes Windows APIs; existing exact-object filesystem primitives.

**Spec:** `docs/superpowers/specs/2026-09-05-round8-evidence-vault.md`

## Global Constraints

- Protect containment evidence from direct modification by Low MO2 and ordinary descendants.
- Use raw integrity RID 0x2000 for controller and watcher and RID 0x1000 for the disposable process.
- Runtime data remain under Desktop/Modlab in actual use. Tests use fresh disposable roots.
- Historical attempts remain unchanged and cannot be adopted into the new protocol.
- Reuse existing pinned publication, readback and no-replace rename.
- No automatic kill-on-job-close is introduced.
- No live MO2/game launch, merge, push, or changes to main.
- Preserve the verified two-file Astra mitigation present at the starting tree.

## Native test command

Use `C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -B -m unittest <module> -v` from the feature worktree. Set TEMP/TMP to a fresh short directory under Desktop/Modlab. Native tests require execution as the real non-elevated account (sandbox cannot inspect user ancestors). Use require_escalated for these disposable tests and bounded file installation. Keep full test transcripts in this plan's workspace. Do not run the full suite for each task; run focused tests, then full suite once after integration. Do not concurrently inspect files during exact-sharing tests.

### Task 1: Verified evidence directory security

**Files:** Create `modlab/validation/windows_vault_security.py`, `tests/test_windows_vault_security.py`; narrowly modify `modlab/platform/windows_exact_fs.py` and its test module for an optional initial security descriptor and read-control access on rooted directory creation.

**Interfaces:**

```python
@dataclass(frozen=True)
class VaultPrincipal:
    sid: str
    integrity_rid: int
    mandatory_policy: int

def current_vault_principal() -> VaultPrincipal: ...
def create_vault(path: Path) -> EvidenceVault: ...  # parent exists; target must not exist
def open_vault(path: Path, *, expected_creator_sid: str | None = None) -> EvidenceVault: ...

class EvidenceVault:
    path: Path
    creator_sid: str
    identity: PinnedIdentity
    def verify(self) -> None: ...
    def verify_descendant(self, path: Path) -> None: ...
    def close(self) -> None: ...
    # context manager supported; explicit ownership survives failed close
```

Produces these primitives only; Task 3 integrates the watcher and Task 4 integrates store/harness. The root policy is protected DACL creator+SYSTEM only, explicit inheritable Medium NO_WRITE_UP, owner creator. Descendants inherit owner/allowed writers and label; do not require separately protected DACL on every child. Initial descriptor must be passed to rooted creation, not applied after a Low-accessible object exists. Use GetSecurityInfo on owned handles. Ancestors can be implicit Medium and broader normal DACLs, because MIC prevents Low mutation; require effective NO_WRITE_UP, direct identities, same volume and retained rename/delete guards. Do not reject normal trusted ancestors merely for existing administrator/SYSTEM owners.

Effective token must be exact Medium with NO_WRITE_UP, same creator SID and actual access under token restrictions. Impersonation must not be silently ignored. An unexpected SID never changes existing ACLs. Handles opened for security need READ_CONTROL; reuse PinnedObject and ExactObjectOwnershipError where practical, with every failed close retained. Security creation failures must not delete unrelated collisions. No generic ACL framework or mandatory marker files; root owner and policy are native facts. Keep focused exports and document intended lifetime.

- [ ] Write behavior tests before implementation, including this positive shape and negative mutations:

```python
with create_vault(self.root / "authority") as vault:
    target = vault.path / "evidence.json"
    publish_new_pinned(target, b"protected\n")
    vault.verify_descendant(target)
with open_vault(self.root / "authority") as reopened:
    self.assertEqual(reopened.creator_sid, current_vault_principal().sid)
```

Run a real Low child against create/overwrite/rename/delete/relabel for root and a protected file, with a Low disposable sibling write as positive control. Each protected write must be denied and original identity/bytes unchanged. Test inherited subdirectory/file policy, unknown ACE policy, exact RID values 0x1000/0x2010/0x3000 rejection, unsafe Low ancestor, different expected SID, collision immutability, and injected failed close retaining every handle. Preserve underlying original errors. Use tests that observe Windows behavior as well as isolated policy parsing edge cases.

- [ ] Run RED, retaining transcript; missing module failures first are expected.
- [ ] Implement the narrow helper and optional exact-directory security support; no unrelated caller migration.
- [ ] Run focused native GREEN plus affected exact-directory tests, inspect diff and commit only task files.
- [ ] Report spec compliance, test commands/results, retained ownership behavior, and any concern. Controller supplies separate task review.

### Task 2: Retained process tree ownership

**Files:** Modify `modlab/validation/windows_integrity.py`; create `modlab/validation/windows_process_job.py`, `tests/test_windows_process_job.py`; update affected launcher tests.

**Interfaces:**

```python
def launch_low_integrity_process(..., *, on_created=None,
                                 retain_owner: bool = False,
                                 before_resume=None) -> ProcessLaunch: ...
# ProcessLaunch gains owner: ProcessJobOwner | None = None (not serialized).
# before_resume receives the verified ProcessLaunch before child resume.

@dataclass(frozen=True)
class ProcessTreeObservation:
    pid: int
    creation_time: int
    root_exit_code: int | None
    active_processes: int
    total_processes: int

class ProcessJobOwner:
    def observe(self) -> ProcessTreeObservation: ...
    def close(self) -> None: ...
```

Consumes existing suspended launch; produces exact original process/job ownership for Task 3/4. Opt-in retained mode must create private unnamed job, assign and verify before callback/resume, prohibit both breakaway flags, verify exact Low RID, and transfer handles without reopen. Ordinary callers keep existing return/close behavior. Root exit must come from retained process wait+exit query; ActiveProcesses comes from job accounting. Unknown is an error. Guard against use after close, nonowner PID/controller continuation, double transfer and failed closes. No kill-on-job-close. Do not close a live process tree on normal completion path; report still-active so caller retains ownership. Failure before resume may terminate the just-created suspended process using existing cleanup semantics; all incomplete cleanup ownership must remain explicit.

- [ ] Write failing native tests for a root that starts a child which starts a grandchild, all writing only in the Low test root. Root exit alone must leave active_processes > 0; only after descendants naturally exit may observation be empty.

```python
launch = launch_low_integrity_process(python, args, low_root, env,
                                    retain_owner=True,
                                    before_resume=record_suspended_admission)
observation = launch.owner.observe()
self.assertEqual(observation.pid, launch.pid)
```

- [ ] Add tests that assignment/verification failure never runs the child marker, breakaway child cannot escape, callback happens before executable code, unrelated process is untouched, and close failures retain original ownership. Run RED.
- [ ] Implement Job Object owner and opt-in launcher transfer. Run new native tests and existing integrity/launch tests GREEN.
- [ ] Self-review and commit task files; write report with exact test evidence.

### Task 3: Protected watcher causal completion

**Files:** Modify `modlab/validation/windows_watch.py`, `windows_watch_protocol.py`, containment outcome model/serialization only as required, `tests/test_mo2_containment_watch.py`; add focused protocol tests if existing test file would become less readable.

**Interfaces:** Consumes `EvidenceVault`, `ProcessJobOwner`, `ProcessTreeObservation`. `start_watch` retains a verified vault for the entire session; worker and detached readers independently verify it. Add `admit_watch_launch(request_path: Path, launch: ProcessLaunch) -> None` to call only from before_resume; bind ready/request/controller and process creation identity. Add `complete_watch_launch(request_path: Path, owner: ProcessJobOwner) -> None` to independently query retained ownership and publish canonical quiescence only if exact root exited and job empty. `stop_watch` normal completion uses these retained facts; nonowner/restart stop publishes distinct recovery record and cannot complete. Existing watch-only fixture tests must explicitly admit/complete a disposable child for successful completion rather than synthetic process claims.

- [ ] Tests first: Low early stop while watcher still draining must fail; valid normal stop binds exact stop identity/hash into terminal; recovery stop never completes; missing admission/quiescence/worker-exit refuses Completed; altered raw/outcome bytes refuse reconstruction. Fresh legitimate completion must reconstruct after controller has subsequently exited, solely from protected durable observations.

```python
launch = launch_low_integrity_process(python, args, low_root, env,
    retain_owner=True,
    before_resume=lambda value: admit_watch_launch(request_path, value))
# Wait for disposable child and its descendants to exit.
complete_watch_launch(request_path, launch.owner)
receipt = stop_watch(request_path)
self.assertTrue(receipt.complete)
```

- [ ] Extend strict canonical protocol with versioned admission, quiescence, NormalControllerStop/RecoveryCleanupStop and WorkerExitObservation bindings. Do not silently promote legacy records. Retain shared raw derivation; consume outcome verdict only for final equality. Persist observed worker exit before Completed publication.
- [ ] Retain vault and record pins through operations, enforce actual code/runtime integrity policy, and propagate complete close ownership. Verify collision bytes against exact canonical serialization.
- [ ] Run watcher/protocol/model tests, including earlier Astra regressions updated only for the new explicit contract. Commit and report.

### Task 4: Task 6 and Round 8 integration

**Files:** Modify containment store/service/preparation recovery and relevant tests; create tracked `tools/mo2_round8_gate.py` and `tests/test_mo2_round8_gate.py`, deriving only necessary flow from historical ignored Round 7 harness; update current docs. Historical ignored harness is read-only reference.

**Interfaces:** Store keeps disposable and authority roots explicit, and creates fresh protected run roots using Task 1. Shared watcher calls use Task 3 with before_resume admission and original owner retained until finalization. Task 6 completion and fresh reconstruction consume protected originals and common raw derivation. New gate retains existing phase/evidence behavior needed for the ten-phase matrix, with every verdict input stored in protected evidence tree. Arguments provide fresh authority/disposable roots; existing history is never migrated.

- [ ] Add failing integration tests proving ordinary Medium-but-unverified or Low evidence cannot start a new authoritative watch; store refuses historical adoption, input snapshots are protected new objects, and child/grandchild liveness prevents capture or recovery relocation.

```python
self.assertFalse(reconstruct_attempt(missing_worker_exit_attempt).complete)
self.assertFalse(reconstruct_attempt(recovery_only_attempt).complete)
self.assertEqual(read_capture(screenshot_capture), original_screenshot_bytes)
```

The snippet states required observable behavior; adapt to the actual existing result interface (do not introduce wrapper functions solely for this example). Cover every inventory item named in the spec with a source-path/capture-path table checked by offline tests; forbid authoritative reads back into Low paths. Screenshot/log claims remain provenance only.

- [ ] Integrate scoped roots/ownership/protocol; add a bounded exact capture routine at the store boundary if existing primitives lack bounded read capability. Do not move Low originals into the vault. Expose clear failure text for missing creating SID, unsafe storage, live descendants and missing causal records.
- [ ] Preserve separate immutable attempt and cleanup results, fail closed for missing original job ownership, and re-derive Task 6 result rather than trusting verdict fields. No unrelated storage/adapter/archive refactor.
- [ ] Run focused service/recovery/gate tests; run full repository suite with real archive/Steam opt-ins unset, plus isolated Round 8 offline gate. Record skips honestly. Review complete diff and current evidence, fix findings, commit only this scope, export report/patch/verification to task outputs. Leave live validation for a later UI-capable task.
