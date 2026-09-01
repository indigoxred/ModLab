# Fail-Closed Containment Watch Design

**Date:** 2026-09-01

**Status:** Approved for implementation planning

**Scope:** Task 4's Windows mutation watcher and Task 6's interrupted-scenario recovery contract

## Purpose

ModLab validates risky MO2 behavior in a disposable Lab before it can affect the user's real modding environment. During that validation, a native Windows watcher records every mutation beneath the declared source, profile, download, Overwrite, bounded-game, LocalLow, and TempLow roots. Before/after manifests provide a second proof.

This design makes one rule explicit: an interrupted validation attempt can never later become a successful proof. ModLab may clean up an interrupted attempt, but it must create a fresh run to obtain a pass.

The user-facing behavior is simple:

1. a normal validation runs and reports Pass, Fail, or Incomplete;
2. a genuine containment mutation reports Fail and is not hidden by a retry;
3. a controller crash, power loss, uncertain process identity, damaged evidence, timeout, or handle-cleanup failure reports Incomplete unless independently trustworthy evidence has already proved a containment breach;
4. ModLab safely cleans or quarantines that disposable attempt;
5. the next validation uses a new run ID, new evidence root, and new disposable fixtures without asking the user to trust the interrupted run.

No real mod archive, MO2 installation, Play profile, game file, save, or Steam file is rebuilt merely because this internal validation restarts.

Results use asymmetric precedence: **Fail, then Incomplete, then Pass**. A positively proved breach is still Fail when unrelated evidence is incomplete. Pass always requires the complete evidence chain.

## Why the previous recovery design is rejected

The fifth Task 4 correction round proved that transparent same-run recovery is the wrong architecture for this spike. Native handles are owned by one Windows process, while recovery state is stored in files. There is no atomic operation that both transfers a process-local handle and commits a durable filesystem transition.

The previous design attempted to bridge that gap with nine owner states, three controller identities, five process-local ownership registries, a cross-process mutex, and recovery-to-success paths. Every additional close, publication, thread-death, controller-death, PID-reuse, or evidence-read failure created another point where durable state could disagree with actual handle ownership.

Continuing that design would amount to building a distributed transaction coordinator so a short disposable test can resume from the middle. A dedicated broker process could make ownership simpler, but would add IPC, authentication, broker discovery, service lifecycle, and another crash boundary. Neither cost is justified by the ModLab validation goal.

## Verified Windows behavior

The design relies on documented Windows contracts:

- [process handles remain valid until closed even after process termination](https://learn.microsoft.com/en-us/windows/win32/procthread/process-handles-and-identifiers);
- [`GetExitCodeProcess` distinguishes `STILL_ACTIVE`, application exit values, and unhandled-exception termination values](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getexitcodeprocess);
- [`ReadDirectoryChangesW` reports buffer loss and `ERROR_NOTIFY_ENUM_DIR`, neither of which can support a clean proof](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-readdirectorychangesw);
- [`CancelIoEx` requests cancellation but does not wait, and the `OVERLAPPED` storage cannot be reused until completion is observed](https://learn.microsoft.com/en-us/windows/win32/api/ioapiset/nf-ioapiset-cancelioex);
- [a rename without `FILE_RENAME_REPLACE_IF_EXISTS` must fail when the destination already exists](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-fscc/4217551b-d2c0-42cb-9dc1-69a716cf6d0c).

## Core safety rule

`Completed` evidence is available only to the exact controller process that launched the watcher, retained the exact worker process handle, observed `WAIT_OBJECT_0`, read the exact documented worker success exit code `0`, reverified the retained process identity, closed all evidence-critical controller handles successfully, and validated the complete evidence chain in one uninterrupted controller session.

Any uncertainty permanently makes that run `Incomplete`. Cleanup can improve machine state, but it cannot upgrade `Incomplete` to `Completed`.

An actual recorded mutation remains a containment failure. Automatic fresh-run behavior applies only to interruption or evidence uncertainty; it never converts a real failure into a pass.

## Preserved watcher guarantees

The redesign retains the hardening that directly protects evidence:

- the SHA-256 of the exact canonical request binds every ready, terminal, event, and outcome record;
- every request contains all eight logical root kinds: `SourceMods`, `LabProfile`, `PlayProfile`, `Downloads`, `Overwrite`, `BoundedGame`, `ExternalLocalLow`, and `ExternalTempLow`;
- physical root handles may be de-duplicated by stable volume/file identity, while events still fan out to every covered logical kind;
- each watched root is opened without following reparse points and protected by its retained volume-to-root membership guard chain;
- the worker uses cancellable overlapped `ReadDirectoryChangesW` requests and waits for every completion before terminal publication;
- overflow, malformed records, sequence gaps, invalid rename pairs, root replacement, worker errors, and worker-side unclosed handles make evidence incomplete;
- the event journal is written, flushed, reloaded, and hashed through one stable native handle whose identity, byte count, event count, and final sequence are bound to terminal evidence;
- strict schemas reject extra fields, polymorphic integer values, noncanonical paths, and mismatched request identities;
- before/after manifests cannot hide transient watcher events, and zero watcher events cannot overrule changed manifests;
- validation watches only disposable source fixtures and bounded read-only game evidence; it never mutates a watched production root.

## Durable records

Each validation attempt has a fresh, content-bound evidence directory containing these records:

### Immutable request

`request.json` contains the canonical `WatchRequest`, all eight logical roots, exact root identities, evidence paths, run ID, scenario, and a new unpredictable validation session ID. Its canonical SHA-256 is the evidence identity.

### Immutable controller claim

`controller-claim.json` is created atomically before worker launch. It binds:

- request hash and session ID;
- controller PID and creation time;
- intended worker command and request path;
- schema version.

The claim is never replaced or reconstructed from `ready.json`. A missing, malformed, conflicting, or unverifiable claim permanently prevents completion.

### Immutable worker launch identity

`worker-launch.json` binds the request hash to the worker PID and creation time. It is published only after the launching controller has an exact verified worker process handle. Publication failure makes the attempt incomplete and triggers cleanup; it cannot fall back to a recoverable Running state.

### Worker evidence

The worker publishes bound ready, events, and terminal evidence using the existing strict evidence rules. The worker also opens and verifies the exact launching-controller process identity. If that controller process exits, the worker initiates its own cancellation, closes its watcher and journal handles, and publishes terminal evidence marked incomplete because the controller session was lost.

### One immutable watcher outcome

Task 4's `outcome.json` is atomically created once. It records watcher evidence, not the Task 6 scenario verdict. Its `evidenceCompletion` is exactly one of:

- `Completed` — the same controller session proved the expected worker exit, successful evidence-critical handle closure, and valid complete evidence;
- `Incomplete` — the run was interrupted, uncertain, malformed, timed out, or could not close evidence-critical handles cleanly.

The record also binds the request hash, session ID, run ID, scenario, controller identity, worker identity, observed worker exit code, canonical evidence hash, and watcher reason codes. Its content ID is `watch-outcome-sha256:<64-lowercase-hex>`, computed from its exact canonical bytes. Worker exit code `0` means the watcher protocol completed; it does not mean the containment scenario passed. A genuine observed mutation can therefore have `evidenceCompletion=Completed` while Task 6 records Fail.

If no outcome exists after the claimed controller is proven dead, later inspection derives `Incomplete` and may atomically record it. No process may write `Completed` for another controller session.

The worker success path is exactly exit code `0`. Wait failure, timeout, `GetExitCodeProcess` failure, `STILL_ACTIVE`, any nonzero exit code, an unhandled-exception exit status, identity mismatch, or process-handle close failure makes watcher evidence Incomplete even when a terminal file exists. The retained process identity is verified when acquired and reverified after signal while the same handle remains valid.

### Durable Task 6 scenario result

Task 6 stores the scenario verdict separately in immutable `result.json`. Its existing `ScenarioOutcome` remains `Passed`, `Failed`, or `Incomplete`; it also records the exact Task 4 watcher-outcome content ID, watcher evidence completion, whether the durable scenario-started boundary was reached, fresh-retry eligibility, and reason codes.

Loading or adjudicating `result.json` reloads the exact watcher outcome by content ID and verifies matching request hash, validation session ID, run ID, and scenario. A copied completion string or boolean is never sufficient evidence and cannot substitute for the bound watcher outcome.

Valid combinations are:

| Watcher evidence | Scenario outcome | Meaning |
| --- | --- | --- |
| Completed | Passed | Complete evidence proved containment. |
| Completed | Failed | Complete evidence proved a breach. |
| Incomplete | Failed | An independently trustworthy breach was proved, but other evidence is incomplete. |
| Incomplete | Incomplete | No breach was proved, but Pass cannot be established. |

`Incomplete` watcher evidence can never produce a Passed scenario. Completed watcher evidence never implicitly means Passed; Task 6 applies the scenario policy to events, manifests, process evidence, projections, and quarantine evidence.

### Result precedence and scenario-started boundary

Task 6 applies these rules in order:

1. **Failed:** at least one independently valid forbidden watcher event or protected-manifest delta positively proves a breach, even if unrelated evidence is incomplete.
2. **Incomplete:** no breach is positively proved, but the evidence chain cannot establish Pass.
3. **Passed:** every required evidence source is complete and proves no breach.

A malformed, unauthenticated, request-mismatched, sequence-invalid, or otherwise untrustworthy event is not positive breach proof. It makes the run Incomplete.

Before the first disposable scenario action that could mutate state, Task 6 durably records an explicit `ScenarioStarted` boundary. This occurs after watcher readiness and before launching MO2. Same-command automatic retry is eligible only when durable evidence proves interruption occurred before this boundary, the scenario outcome is not Failed, cleanup succeeded, and no protected mutation was proved. After `ScenarioStarted`, Failed or Incomplete is returned to the user; only a later explicit validation command may create a fresh attempt.

## Normal lifecycle

The supported success path remains within one controller process:

```text
Create request and controller claim
  -> launch worker and retain exact process handle
  -> publish worker launch identity
  -> wait for bound ready evidence
  -> durably publish Task 6 ScenarioStarted
  -> execute the disposable MO2 scenario
  -> create the idempotent stop token
  -> wait for the retained exact worker handle to signal
  -> require WAIT_OBJECT_0 and GetExitCodeProcess == 0
  -> reverify exact worker PID and creation time through the retained handle
  -> validate and capture canonical terminal, journal, identities, sequences, and manifests through the owned evidence handles
  -> close every evidence-critical controller handle
  -> durably publish evidenceCompletion=Completed solely from the captured material
  -> reload and verify outcome.json for reporting without reopening evidence authority
```

A process-local lock serializes calls inside the controller. Another process may inspect status, but it cannot complete the run. Task 6's existing run-level mutex serializes scenario execution and cleanup; Task 4 no longer maintains a second cross-process completion mutex.

## Interruption and cleanup lifecycle

Any failure before `Completed` follows a fail-closed path:

```text
Uncertain or interrupted attempt
  -> atomically publish or infer Incomplete
  -> request idempotent worker shutdown when safe
  -> verify exact PID plus creation time before waiting or cleanup
  -> quarantine disposable staging when identity is proven
  -> retain all diagnostics
  -> create a fresh run for the next validation attempt
```

Cleanup rules are deliberately asymmetric:

- a non-launching caller that proves the exact claimed controller remains live refuses without mutation: it does not create the stop token, alter the outcome, or quarantine anything;
- a non-launching caller that proves the claimed controller is dead may stop or wait for the recorded exact worker solely to make the machine tidy and fixes the old run as Incomplete;
- uncertain controller identity or liveness refuses cleanup and returns an incomplete receipt naming the blocker;
- PID reuse, access denial, owner loss, identity mismatch, or uncertain liveness causes cleanup refusal rather than process termination;
- ModLab never kills MO2, an unrelated process, or a process it cannot identify exactly;
- cleanup success never changes the old outcome from Incomplete;
- unknown disposable content is quarantined by exact-handle rename to a prevalidated same-volume destination without replace-existing semantics; a destination collision refuses cleanup, and there is no recursive copy/delete fallback;
- production MO2, source mods, Play, game, Steam, and saves remain outside cleanup authority.

Controller handle-close failure no longer needs a cross-process ownership-recovery protocol. It immediately fixes the outcome as Incomplete. Any leaked handle remains confined to that controller process and is closed by Windows when the process exits; no later controller can promote the run.

## Fresh-run behavior

Task 6 distinguishes three results:

- **Pass:** complete evidence and no forbidden mutation;
- **Fail:** complete evidence proves a forbidden mutation or another genuine containment violation;
- **Incomplete:** the attempt cannot support either conclusion.

An active validation command may automatically perform at most one fresh retry only when durable state proves that infrastructure interruption occurred before `ScenarioStarted`. The retry receives a new run ID, request hash, evidence root, and disposable fixtures. Both attempts remain visible in the report.

After a process or computer restart, the next validation command first performs cleanup-only recovery for stale attempts. A fresh run starts only after cleanup succeeds and durable evidence proves that no prior controller, watcher, or MO2 process remains active. Access denial, uncertain identity, live MO2, an unverified watcher, or any other cleanup refusal returns Incomplete and preserves the stale run without starting another. Successful cleanup does not require the user to approve reuse of old evidence because no old evidence is reused. Repeated interruption is reported rather than retried indefinitely.

A Failed result is never retried automatically. An Incomplete result after `ScenarioStarted` is also returned without same-command retry. A later explicit validation command may start a new independent attempt, but the earlier Failed or Incomplete result remains visible and immutable. This prevents a second clean execution from hiding a real or possible escape in the first.

## Public interface behavior

The existing Task 4 API names remain, but the architectural revision intentionally amends the previously exact Task 1, Task 4, and Task 6 data contracts:

- a new `WatchEvidenceCompletion` enum has exact values `Completed` and `Incomplete`;
- `WatchRequest` adds exact `run_id`, `scenario`, and unpredictable `session_id` fields, all covered by canonical request hashing;
- `WatchReceipt` adds watcher `evidence_completion`, `worker_exit_code`, and `watch_outcome_id`; its legacy `complete` convenience value must equal `evidence_completion == Completed` and cannot be serialized as independent authority;
- `ScenarioState` adds the durable `ScenarioStarted` boundary before MO2 launch;
- `ScenarioResult` adds `watch_outcome_id`, `watch_evidence_completion`, `scenario_started`, and `fresh_retry_eligible`;
- `recover_scenario()` returns a strict `ScenarioRecovery` value containing the resulting journal identity, immutable Incomplete result identity when available, cleanup status, blockers, and `fresh_run_permitted`. It no longer returns a bare `ScenarioJournal` that could leave retry authority implicit.

The behavioral APIs are:

- `start_watch(request) -> int` creates the controller claim, launches the worker, and retains the exact worker handle in the current controller session;
- `run_watch_worker(request_path) -> int` performs native monitoring and controller-liveness observation;
- `stop_watch(request_path) -> WatchReceipt` may return `complete=True` only in the launching controller session. A non-launching caller refuses read-only while the exact owner is live, performs cleanup-only after the owner is proven dead, and refuses cleanup when owner identity or liveness is uncertain;
- `watch_receipt_from_files(...) -> WatchReceipt` reconstructs evidence but requires a valid `Completed` outcome before returning complete.

Receipt reconstruction is read-only with respect to watched roots. Missing or malformed protocol files become an incomplete receipt rather than an exception after cleanup begins.

Strict serialization changes with these interfaces. `Passed` still requires complete bound watcher evidence and every scenario invariant. `Failed` may pair with incomplete watcher evidence only when a separately valid, request-bound forbidden event or protected-manifest delta positively proves the breach. Incomplete evidence without such positive proof yields `Incomplete`. Adjudication maps any immutable Failed scenario to `Rejected`; missing/incomplete evidence without a proved breach maps to `Incomplete`.

## Evidence-critical controller handles

Completion accounts for every native handle actually retained by ModLab whose lifetime participates in worker identity, mutable evidence protection, or the proof decision:

- the exact retained worker process handle, including the native `Popen` process handle if it is a distinct owned handle;
- any controller-owned journal or evidence-file handle retained to bind stable file identity or prevent mutation;
- every temporary process identity, liveness, or exit-observation handle opened during the completion decision;
- any controller-owned directory or native lock handle whose continued ownership protects evidence used for completion.

Python 3.12's Windows `subprocess.Popen` closes the worker primary-thread handle inside process creation and does not expose it as a retained ModLab handle. It is therefore not part of the current completion inventory. If a future launcher exposes or retains that handle, it becomes evidence-critical until closed.

Ordinary process-local Python locks have no native handle to inventory. A read-only handle opened solely to confirm an already immutable outcome after publication is reporting infrastructure rather than completion authority; a close warning is retained diagnostically but cannot reverse immutable evidence whose critical handles were already closed. Such a reporting handle may compare the published outcome with captured hashes, but it cannot supply or repair evidence needed to authorize completion.

The temporary descriptor used to publish `outcome.json` is handled by the durable publication protocol below, rather than being listed as a pre-publication evidence handle.

## Durable publication

`controller-claim.json`, `worker-launch.json`, and `outcome.json` use atomic create/no-replace publication. Before a dependent action or successful user-visible result, ModLab:

1. writes the exact canonical bytes to a unique contained temporary file;
2. flushes the bytes;
3. closes the temporary descriptor successfully;
4. atomically promotes without replacement;
5. reloads the promoted record;
6. revalidates its exact schema, request hash, controller/worker identity where applicable, and canonical hash.

Failure at any step prevents the dependent action or successful result. `outcome.json` is never silently replaced, and ModLab never displays Pass before the successful outcome is durable and revalidated.

For a Completed watcher outcome, all authority-bearing terminal, journal, process-identity, and manifest material is validated and captured before the last evidence-critical handle closes. Publication uses only that immutable captured material. After the final critical close, no new handle may be opened to obtain or repair completion authority. Outcome reload verifies the publication itself for reporting; it does not reconstruct completion from the underlying files.

If the worker publishes its immutable terminal and the controller dies afterward, the worker does not overwrite terminal evidence with a second controller-loss terminal. Where possible it publishes a separate immutable controller-loss observation. `outcome.json` remains absent, and later inspection records or derives Incomplete because the launching controller did not finish the completion protocol.

## Removed behavior

The redesign removes:

- cross-process recovery of an interrupted run to `Completed`;
- `StopArmed`, `ExitObserved`, and `CleanupComplete` as durable promotion states;
- recoverable `RecoveryRequired -> Completed` transitions;
- controller-retainer and observer identities used to transfer completion authority;
- process-local retained-handle registries used as cross-process proof;
- reconstruction of Running ownership from ready evidence;
- the Task 4 cross-process stop mutex;
- tests whose only purpose was proving recovery-to-success after controller ownership became uncertain.

An implementation may use small process-local cleanup helpers, but they do not appear in durable evidence and cannot authorize completion.

## Task 6 integration

`recover_scenario()` becomes cleanup-only for interrupted watcher execution. It may:

- mark or infer the old run Incomplete;
- request exact watcher shutdown;
- verify worker exit when identity is available;
- quarantine exact disposable staging;
- restore only journal-proven fixture state;
- report whether a fresh scenario can start.

It may not promote the interrupted run, reuse its evidence root, or claim the scenario passed. A new scenario has a new run identity and reruns every mutation and manifest check.

Task 4's watcher outcome and Task 6's scenario result remain distinct durable artifacts joined by the exact watcher-outcome content ID and matching request/session/run/scenario bindings. Task 6 stores and enforces `freshRetryEligible` mechanically as true only when watcher evidence is Incomplete, the scenario outcome is not Failed, interruption is proved to precede `ScenarioStarted`, cleanup succeeded, and durable liveness evidence proves no prior controller, watcher, or MO2 remains active.

Run-level recovery continues to refuse while the exact MO2 process remains live. ModLab never terminates MO2 automatically.

## Test strategy

Development remains test-driven. Focused native Windows tests cover:

1. normal same-controller start, ready, stop, exact exit observation, closure, validation, and Completed publication;
2. exact request binding, eight-kind logical coverage, physical alias fan-out, guard chains, journal identity, sequence completeness, and before/after manifests;
3. actual mutation, overflow, malformed event, invalid rename pair, root replacement, and worker error outcomes;
4. controller exit before ready, during the scenario, before the stop token, after the token, and after worker terminal publication—all permanently non-passing;
5. worker self-cancellation after exact launching-controller death;
6. terminal evidence without same-session exact process-exit observation never completing, plus expected exit code `0`, nonzero exit, unhandled exception after terminal publication, `GetExitCodeProcess` failure, `STILL_ACTIVE`, and post-signal identity mismatch;
7. worker PID reuse, access denial, identity mismatch, and unknown liveness refusing unsafe cleanup;
8. controller and worker handle-close injection producing Incomplete without a later promotion path;
9. missing, malformed, replaced, or conflicting request, claim, launch, journal, terminal, and outcome records;
10. concurrent external controller refusal while the claimed controller remains live, proving no token, outcome, or quarantine mutation;
11. cleanup-only recovery after the controller is proven dead;
12. a fresh run using distinct identities while the interrupted run remains immutable and incomplete;
13. valid forbidden event followed by controller death and protected-manifest change followed by damaged terminal evidence both producing Failed without retry;
14. malformed/untrusted event after `ScenarioStarted` producing Incomplete without same-command retry, while pre-boundary controller death permits one fresh attempt;
15. manual clean validation after a Failed attempt preserving both independent results;
16. `CancelIoEx` followed by `ERROR_OPERATION_ABORTED`, cancellation racing a normal final event, `ERROR_NOT_FOUND` after prior completion, no `OVERLAPPED` or buffer reuse before observed completion, and journal finalization only after every completion is drained;
17. atomic no-replace outcome publication, flush, reload, exact revalidation, and no successful report before durability;
18. unchanged protected production roots throughout cleanup and fresh-run execution.
19. strict serialization and adjudication of the revised Task 1/4/6 types, including the valid Incomplete-evidence/Failed-verdict combination and rejection of every impossible combination;
20. watcher outcome content-ID binding to request hash, session ID, run ID, and scenario, with replay or copied-boolean divergence rejected;
21. cleanup refusal preventing any fresh run while controller, watcher, or MO2 absence remains unproved;
22. canonical evidence capture through retained handles, successful critical-handle closure, outcome-only publication, and no later authority-bearing evidence open.

The focused watcher suite and complete repository suite must pass with the bundled Python 3.12 runtime. Native integration tests use only disposable roots beneath ModLab and bounded read-only game evidence.

## Migration from the abandoned fifth-round work

Before implementation, the current uncommitted fifth-round diff is saved as an archival patch for forensic reference. It is not merged because its independent review still contains Important cross-thread and cross-process ownership findings.

Implementation resumes from reviewed commit `4d44130`, then replaces the recoverable completion protocol rather than stacking more fixes on the abandoned diff. Evidence protections that remain valid are carried forward with focused tests. Obsolete recovery-promotion code and tests are deleted only after the archival patch is verified as readable.

The progress ledger records the architectural rejection, approved fail-closed ruling, preserved patch identity, replacement commit, and verification results.

## Acceptance criteria

This redesign is complete only when:

1. only the launching controller session can publish `evidenceCompletion=Completed`;
2. every interruption or ownership uncertainty makes watcher evidence permanently Incomplete, while independently proved breach evidence may still make the separate scenario outcome Failed;
3. cleanup can never promote an interrupted run;
4. a genuine recorded containment failure is never hidden by automatic retry;
5. same-command retry is permitted only before the durable `ScenarioStarted` boundary, never after a Failed result or post-boundary Incomplete result, and uses new run, request, evidence, and fixture identities;
6. the worker self-cancels when its exact launching controller dies;
7. all eight logical roots and all existing evidence-integrity guarantees remain enforced;
8. unsafe process cleanup is refused on PID reuse, access denial, or identity uncertainty;
9. cleanup cannot mutate production MO2, mods, profiles, games, Steam, or saves;
10. Task 4 evidence completion and Task 6 scenario outcome remain distinct, with Failed taking precedence over Incomplete when a breach is independently proved;
11. the exact worker handle signals, reports exit code `0`, and retains matching identity before Completed evidence is possible;
12. non-owner stop is read-only while the owner is live and cleanup-only only after exact owner death;
13. every cancelled overlapped request is drained before storage reuse or terminal finalization;
14. final outcome and prerequisite claims are durably flushed, promoted without replacement, reloaded, and revalidated;
15. Task 6 recovery is cleanup-only and starts or permits a fresh scenario;
16. focused and full automated suites pass;
17. a fresh independent review finds no Critical or Important issue;
18. verified progress is committed and later published with the complete containment spike.
19. revised Task 1/4/6 types and serializers express the new boundary without contradictory impossible-combination rules;
20. every Task 6 result is immutably bound to the exact Task 4 watcher outcome content ID and matching request/session/run/scenario identities;
21. cleanup refusal or uncertain residual liveness prevents a fresh run;
22. completion captures evidence before critical-handle closure and publishes solely from that captured material, with no later authority-bearing open.

## Explicit non-goals

This design does not:

- make watcher validation highly available across controller crashes;
- resume a disposable test from its midpoint;
- create a Windows service or long-lived broker;
- terminate MO2 or unidentified processes;
- mutate, repair, merge, or normalize the user's mod setup;
- resolve mod conflicts, load order, patches, or game crashes;
- change the broader Skyrim-first product scope or future game-adapter roadmap.
