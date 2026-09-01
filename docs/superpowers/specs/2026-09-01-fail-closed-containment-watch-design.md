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
3. a controller crash, power loss, uncertain process identity, damaged evidence, timeout, or handle-cleanup failure reports Incomplete;
4. ModLab safely cleans or quarantines that disposable attempt;
5. the next validation uses a new run ID, new evidence root, and new disposable fixtures without asking the user to trust the interrupted run.

No real mod archive, MO2 installation, Play profile, game file, save, or Steam file is rebuilt merely because this internal validation restarts.

## Why the previous recovery design is rejected

The fifth Task 4 correction round proved that transparent same-run recovery is the wrong architecture for this spike. Native handles are owned by one Windows process, while recovery state is stored in files. There is no atomic operation that both transfers a process-local handle and commits a durable filesystem transition.

The previous design attempted to bridge that gap with nine owner states, three controller identities, five process-local ownership registries, a cross-process mutex, and recovery-to-success paths. Every additional close, publication, thread-death, controller-death, PID-reuse, or evidence-read failure created another point where durable state could disagree with actual handle ownership.

Continuing that design would amount to building a distributed transaction coordinator so a short disposable test can resume from the middle. A dedicated broker process could make ownership simpler, but would add IPC, authentication, broker discovery, service lifecycle, and another crash boundary. Neither cost is justified by the ModLab validation goal.

## Core safety rule

`Completed` is available only to the exact controller process that launched the watcher, retained the exact worker process handle, observed that handle signal, closed all evidence-critical controller handles successfully, and validated the complete evidence chain in one uninterrupted controller session.

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

`request.json` contains the canonical `WatchRequest`, all eight logical roots, exact root identities, evidence paths, and a new unpredictable validation session ID. Its canonical SHA-256 is the evidence identity.

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

### One immutable final outcome

`outcome.json` is atomically created once with exactly one outcome:

- `Completed` — the same controller session proved worker exit, successful evidence-critical handle closure, and valid complete evidence;
- `Incomplete` — the run was interrupted, uncertain, malformed, timed out, or could not close evidence-critical handles cleanly.

A genuine observed containment mutation may have structurally complete evidence while the scenario verdict is Fail; it is not classified as an interrupted run.

If no outcome exists after the claimed controller is proven dead, later inspection derives `Incomplete` and may atomically record it. No process may write `Completed` for another controller session.

## Normal lifecycle

The supported success path remains within one controller process:

```text
Create request and controller claim
  -> launch worker and retain exact process handle
  -> publish worker launch identity
  -> wait for bound ready evidence
  -> execute the disposable MO2 scenario
  -> create the idempotent stop token
  -> wait for the retained exact worker handle to signal
  -> close every evidence-critical controller handle
  -> validate terminal, journal, identities, sequences, and manifests
  -> atomically publish Completed
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

- a restarted controller may stop or wait for the recorded exact worker solely to make the machine tidy;
- PID reuse, access denial, owner loss, identity mismatch, or uncertain liveness causes cleanup refusal rather than process termination;
- ModLab never kills MO2, an unrelated process, or a process it cannot identify exactly;
- cleanup success never changes the old outcome from Incomplete;
- unknown disposable content is quarantined by exact-handle/no-replace operations, never recursively deleted by pathname;
- production MO2, source mods, Play, game, Steam, and saves remain outside cleanup authority.

Controller handle-close failure no longer needs a cross-process ownership-recovery protocol. It immediately fixes the outcome as Incomplete. Any leaked handle remains confined to that controller process and is closed by Windows when the process exits; no later controller can promote the run.

## Fresh-run behavior

Task 6 distinguishes three results:

- **Pass:** complete evidence and no forbidden mutation;
- **Fail:** complete evidence proves a forbidden mutation or another genuine containment violation;
- **Incomplete:** the attempt cannot support either conclusion.

An active validation command may automatically perform at most one fresh retry after it successfully cleans an Incomplete attempt caused by infrastructure interruption. The retry receives a new run ID, request hash, evidence root, and disposable fixtures. Both attempts remain visible in the report.

After a process or computer restart, the next validation command first performs cleanup-only recovery for stale attempts and then starts a fresh run. It does not require the user to approve reuse of old evidence. Repeated interruption is reported rather than retried indefinitely.

A Fail result is never retried automatically. This prevents a second clean execution from hiding a real escape detected by the first.

## Public interface behavior

The existing Task 4 API names remain:

- `start_watch(request) -> int` creates the controller claim, launches the worker, and retains the exact worker handle in the current controller session;
- `run_watch_worker(request_path) -> int` performs native monitoring and controller-liveness observation;
- `stop_watch(request_path) -> WatchReceipt` may return `complete=True` only in the launching controller session; any other caller performs or requests cleanup and returns incomplete;
- `watch_receipt_from_files(...) -> WatchReceipt` reconstructs evidence but requires a valid `Completed` outcome before returning complete.

Receipt reconstruction is read-only with respect to watched roots. Missing or malformed protocol files become an incomplete receipt rather than an exception after cleanup begins.

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

Run-level recovery continues to refuse while the exact MO2 process remains live. ModLab never terminates MO2 automatically.

## Test strategy

Development remains test-driven. Focused native Windows tests cover:

1. normal same-controller start, ready, stop, exact exit observation, closure, validation, and Completed publication;
2. exact request binding, eight-kind logical coverage, physical alias fan-out, guard chains, journal identity, sequence completeness, and before/after manifests;
3. actual mutation, overflow, malformed event, invalid rename pair, root replacement, and worker error outcomes;
4. controller exit before ready, during the scenario, before the stop token, after the token, and after worker terminal publication—all permanently Incomplete;
5. worker self-cancellation after exact launching-controller death;
6. terminal evidence without same-session exact process-exit observation never completing;
7. worker PID reuse, access denial, identity mismatch, and unknown liveness refusing unsafe cleanup;
8. controller and worker handle-close injection producing Incomplete without a later promotion path;
9. missing, malformed, replaced, or conflicting request, claim, launch, journal, terminal, and outcome records;
10. concurrent external controller refusal while the claimed controller remains live;
11. cleanup-only recovery after the controller is proven dead;
12. a fresh run using distinct identities while the interrupted run remains immutable and incomplete;
13. one bounded infrastructure retry and no automatic retry after a genuine containment failure;
14. unchanged protected production roots throughout cleanup and fresh-run execution.

The focused watcher suite and complete repository suite must pass with the bundled Python 3.12 runtime. Native integration tests use only disposable roots beneath ModLab and bounded read-only game evidence.

## Migration from the abandoned fifth-round work

Before implementation, the current uncommitted fifth-round diff is saved as an archival patch for forensic reference. It is not merged because its independent review still contains Important cross-thread and cross-process ownership findings.

Implementation resumes from reviewed commit `4d44130`, then replaces the recoverable completion protocol rather than stacking more fixes on the abandoned diff. Evidence protections that remain valid are carried forward with focused tests. Obsolete recovery-promotion code and tests are deleted only after the archival patch is verified as readable.

The progress ledger records the architectural rejection, approved fail-closed ruling, preserved patch identity, replacement commit, and verification results.

## Acceptance criteria

This redesign is complete only when:

1. only the launching controller session can publish Completed;
2. every interruption or ownership uncertainty is permanently Incomplete;
3. cleanup can never promote an interrupted run;
4. a genuine recorded containment failure is never hidden by automatic retry;
5. a fresh retry uses new run, request, evidence, and fixture identities;
6. the worker self-cancels when its exact launching controller dies;
7. all eight logical roots and all existing evidence-integrity guarantees remain enforced;
8. unsafe process cleanup is refused on PID reuse, access denial, or identity uncertainty;
9. cleanup cannot mutate production MO2, mods, profiles, games, Steam, or saves;
10. Task 6 recovery is cleanup-only and starts or permits a fresh scenario;
11. focused and full automated suites pass;
12. a fresh independent review finds no Critical or Important issue;
13. verified progress is committed and later published with the complete containment spike.

## Explicit non-goals

This design does not:

- make watcher validation highly available across controller crashes;
- resume a disposable test from its midpoint;
- create a Windows service or long-lived broker;
- terminate MO2 or unidentified processes;
- mutate, repair, merge, or normalize the user's mod setup;
- resolve mod conflicts, load order, patches, or game crashes;
- change the broader Skyrim-first product scope or future game-adapter roadmap.
