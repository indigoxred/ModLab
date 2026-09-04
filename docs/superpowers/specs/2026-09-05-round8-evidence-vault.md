# Round 8 containment evidence correction

Date: 2026-09-05. This implements the user's request to resume the reviewed Evidence Vault correction. Earlier handoff approval gates describe the previous review, not this authorization. Live MO2/game testing, merge, and publication remain outside this change.

## Scope and trust

Protect containment evidence from direct modification by Low MO2 and ordinary descendants. Trust the non-elevated controller, watcher, their code/dependencies, and the creating account at Medium or higher. This does not authenticate applications sharing an account and does not cover administrator compromise, privilege escalation, or externally brokered process creation.

Use raw integrity RID 0x2000 for controller and watcher and RID 0x1000 for the disposable process. Broad IntegrityLevel classification is retained for unrelated callers. Windows MIC denies Low writes to Medium objects independently of the DACL; unlabeled ancestors have effective Medium policy. [Microsoft MIC](https://learn.microsoft.com/en-us/windows/win32/secauthz/mandatory-integrity-control).

The fresh authority tree is `workspace/runtime/validation-authority/mo2-containment/<run>/...`; disposable jobs stay in `workspace/runtime/validation/...`. Runtime data remain under Desktop/Modlab in actual use. Tests use fresh disposable roots. Historical attempts remain unchanged and cannot be adopted into the new protocol.

## Small platform boundary

Add a focused Windows vault security module. It obtains the actual effective token (including impersonation/restrictions), creator SID, exact integrity RID and mandatory policy. It creates a new direct directory using the existing rooted exact-directory primitive with a security descriptor supplied before creation. The root has explicit inheritable Medium NO_WRITE_UP, a protected DACL allowing only creator SID and SYSTEM, and the creator SID owner. Descendants may inherit the verified policy. No ACL broadening, development group, recursive historical relabeling or elevation.

Retain root and ancestor handles denying deletion/rename, inspect owner/DACL/label through handles, reject reparses, volume changes, unsafe Low-writable ancestors, unexpected writers and unexpected principals. Verify root policy, descendants and identity before authoritative reads/writes and after delegated work. Every failure to close an owned handle remains an explicit retryable ownership error. Use actual effective-token access checks where restrictions matter.

Reuse existing pinned publication, readback and no-replace rename. Add only the optional security-descriptor/access support required for first-handle directory creation. Do not invent a second publication framework. Capture Low-produced input by bounded exact reads into newly created protected files, binding identity, byte count, hash and stage; moving the original does not relabel it.

## Process ownership

Extend the existing suspended launcher with opt-in retained ownership. Create a private unnamed Job Object without breakaway flags, assign and verify the original suspended process before resume, retain the original process and job handles, and transfer them to one explicit owner. No automatic kill-on-job-close is introduced. Failed suspended startup can terminate only the just-created process, as before.

Observe exact root exit and query job accounting for zero ActiveProcesses. A helper or grandchild keeps the attempt live even after the root exits. Query failures are unknown, never empty. Lost controller ownership cannot be recreated from process names. Recovery without original job ownership stays cleanup-only and must not relocate/remove possibly used transient files. [Microsoft Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects).

## Causal protocol

New protocol records bind attempt/request/session/controller/launch identities and canonical hashes. Required order:

1. Vault and runtime/code policy verified; all watches armed.
2. Exact suspended root assigned to job; launch admission bound before resume.
3. Controller observes exact root exit and job ActiveProcesses==0.
4. Original unbroken controller publishes NormalControllerStop bound to the launch and quiescence observation.
5. Watcher retains and validates that exact stop object (volume/file ID and bytes), drains events, and binds it in terminal evidence.
6. Controller observes exact watcher exit using its retained handle, persists protected worker-exit evidence, then derives and publishes the outcome.

RecoveryCleanupStop is a different record, never a substitute for normal completion. A missing normal stop or interrupted original controller is permanently Incomplete. Fresh read-only reconstruction may verify a previously completed attempt using the protected durable exit observation, even after the original controller later exits; it cannot manufacture that observation. Original controller loss before normal completion poisons the attempt. Recovery publishes separate cleanup facts without overwriting the outcome.

One shared watcher derivation consumes all raw inputs; outcome verdict fields are comparison targets. Task 6 likewise re-derives results from protected inputs. Timestamp ordering alone is insufficient. Incomplete old attempts are not upgraded; ambiguous legacy bytes remain non-authorizing.

## Evidence inventory and callers

The shared watcher enforces the vault contract, including in worker and restart paths. Task 6 and a tracked Round 8 harness consume the same protected boundary. Protect every actual verdict input: request/claim/launch/ready/journal/terminal/controller-loss/stop/worker-exit/outcome; ScenarioStarted/launch/protected before-after manifests; preparation/selection/installation/envelope references used in judgment; phase-begin/process/operator/runtime/observation/effect/guarded-phase/final records; exact screenshot and log bytes used in judgment; separate recovery/cleanup/result records. Unused display logs/caches need no authority status.

The old ignored Round 7 harness is a historical reference only. A maintained Round 8 harness and its offline tests must be versioned so review and future execution can reproduce this correction. No live UI evidence is fabricated: this task lacks native Computer Use.

## Verification

Native disposable probes cover Low create/overwrite/rename/delete/relabel attempts against root and children; inherited policy and exact Medium rejection; unsafe ancestors/reparses/principal/close ownership; positive fresh create/reopen/publication; surviving root/child/grandchild and attempted breakaway; failed assignment before resume; early stop while draining, recovery stop, controller crash, missing durable exit proof, exact byte/identity substitution and outcome-only forgery; Task 6 and Round 8 full protected input reconstruction. Run focused tests after each edit, then the full repository suite and the offline gate once integration is stable. Record actual commands, source state and outputs. No live or release claims follow from offline success.
