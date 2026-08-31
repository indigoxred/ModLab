# Guided MO2 Lab Installation and Conflict Review Design

**Date:** 2026-08-31

**Status:** Revised after written-spec review; pending final written-spec approval

**Scope:** One guided archive installation at a time for the verified Skyrim SE/AE portable MO2 2.5.2 instance

## Purpose

ModLab needs to make an ordinary hand-picked mod installation safer without replacing Mod Organizer 2 or choosing mods for the user. The user selects a retained archive, makes the normal Quick, Manual, or FOMOD choices in MO2, and then receives an evidence-backed review before deciding whether to keep or undo the result.

This increment is the guarded **Lab installation** boundary. It does not promote a mod to Play, decide that a mod is desirable, silently repair a load order, generate compatibility patches, or claim that Skyrim is stable. It creates the trustworthy evidence and recovery behavior on which those later workflows depend.

## Approved Product Decisions

- The user chooses every archive and every installer option. ModLab does not use a one-click list or Vortex.
- Only an archive whose bytes are currently Available in ModLab's archive vault may be installed.
- ModLab launches the exact managed portable MO2 instance into the exact `ModLab - Lab` profile. It refuses to attach to an already-running instance and never uses MO2's unsupported multiple-instance mode.
- MO2 remains the installer. Its normal Quick, Manual, and FOMOD interfaces collect the user's choices.
- Every completed install session ends at an explicit **Keep** or **Undo** decision. Nothing is kept merely because MO2 reported success.
- A serious compatibility warning does not remove the user's choice. Keep remains available behind a strong acknowledgement when the safety boundary is intact.
- A containment or recovery failure disables Keep. The only available path is proven recovery or a `RecoveryRequired` stop.
- A safe reversible fix is explained and previewed, then applied only after the user accepts it. Leaving it alone is always available.
- A new version gets a new uniquely named mod folder. ModLab never chooses MO2's Merge or Replace behavior and never deletes an older version.
- No installer may write into the production shared `mods` store until a capability test proves that every pre-existing folder is protected before the first write or deletion. A unique suggested name and postflight detection are not sufficient.
- Preflight requires Play to match its protected checkpoint and Lab to match the selected before checkpoint. Existing Lab drift must first be adopted through a separate explicit checkpoint operation.
- While a guarded MO2 session is active, every external executable is vetoed except the exact contained analyzer authorized by that job.
- Unknown means not proven. It never silently becomes Pass or Compatible.

## Safety Vocabulary

The portable MO2 instance has one important limitation: `mods` and `overwrite` are physically shared by Lab and Play even though selection, order, plug-in state, and profile INIs are separate.

This design therefore uses these exact meanings:

- **Raw Play evidence** is the exact byte state of `ModLab - Play`; it is retained for audit and containment checks.
- **Logical Play state** is the versioned canonical projection of effective enablement and order. For one retained job-owned shared folder, MO2 2.5.2's deterministic transition from absent to an explicit disabled `-Mod Name` row is normalization, not runtime drift, only when no other Play delta exists.
- **Existing shared content** is every mod folder present before the job, plus pre-existing downloads and Overwrite content.
- **Job-owned content** is at most one new mod folder whose creation is tied to the exact bridge request and install callback for this job.
- **Authorized change** is the new job-owned folder, its installer-produced metadata, its Lab membership/initial priority, and plug-ins physically supplied by it at MO2's deterministic initial positions.
- **Containment failure** is any modification to Play, an existing mod folder, the game, saves, MO2's configured roots, pre-existing downloads, or Overwrite; an unexpected new mod folder is also a containment failure.

Adding the job-owned folder to the shared `mods` directory is expected only after the pre-write containment capability has been proven. Enabling it in Play, changing any folder that existed before the job, or adding a folder that cannot be attributed to the job is not. Play's later exact disabled-row normalization is recognized only by the logical checkpoint comparison; it is never permitted as an in-session Play mutation.

## User Experience

The primary text workflow is:

```text
python -B -m modlab skyrim mod install \
  --artifact archive-sha256:<hash> \
  --workspace .\workspace
```

The command performs preflight checks, opens the managed `ModLab - Lab` profile, and starts exactly one normal MO2 installation. The user makes any Quick, Manual, or FOMOD selections, waits for the bridge's durable **SafeToClose** signal, and closes MO2. ModLab then shows five short sections:

1. **Safety** — whether Play, existing content, the game, Overwrite, and containment remained intact.
2. **Files** — what the new mod wins or loses, grouped as textures, meshes, scripts, configuration, plug-ins, archives, and other files.
3. **Plug-ins** — missing masters, duplicate plug-in providers, current order, and available contained-libloot messages.
4. **Known rules** — matched versioned requirements or incompatibilities from ModLab's curated rule catalogue.
5. **Not yet proven** — record conflicts, runtime behavior, performance, or any analyzer evidence that was unavailable.

It then asks for **Keep** or **Undo**, with no default. If a safe fix is available, ModLab first explains what it changes, why, and the exact before/after state, then asks **Apply** or **Leave Alone**. Applying a fix creates a new report; a stale report can never authorize Keep.

If the installer was cancelled, failed, or never produced one unambiguous completed-install callback, the same decision screen explains the incomplete result, disables Keep, and offers Undo/recovery.

The durable recovery commands are:

```text
python -B -m modlab skyrim mod review mod-install-job:<id> --workspace .\workspace
python -B -m modlab skyrim mod keep mod-install-job:<id> \
  --report mod-install-report-sha256:<hash> \
  --workspace .\workspace
python -B -m modlab skyrim mod undo mod-install-job:<id> --workspace .\workspace
python -B -m modlab skyrim mod recover mod-install-job:<id> --workspace .\workspace
```

`review` resumes the same Keep/Undo interaction after a terminal or Windows interruption. `keep` requires the current report identity; when acknowledgement is required it additionally requires `--acknowledge mod-install-report-sha256:<same-hash>`. The duplication is deliberate proof that the warned-about report was shown. `undo` and `recover` are idempotent.

Machine-readable review output is available with `--format json`. A JSON invocation never makes a decision or prompts; it returns the exact job/report identities for a later explicit `keep` or `undo` command.

## High-Level Flow

1. Verify Play matches its protected checkpoint, Lab matches the selected before checkpoint, no other job is nonterminal, and the selected Skyrim environment, archive, MO2 receipt, guard bridge, configured roots, trusted manifests, Overwrite, download metadata, and process state match their expected baselines.
2. Persist the job request and exact before-state evidence before launching anything.
3. Launch only the managed `ModOrganizer.exe` with `--profile "ModLab - Lab"` and a one-use job handoff.
4. The guard bridge atomically claims the request, clears its handoff environment, registers the executable veto, waits for UI/profile readiness, refreshes Lab, verifies the profile and configured roots again, and only then calls MO2's normal `installMod()` once.
5. On a completed path, the bridge requires both the `installMod()` return and its matching installed-mod callback, refreshes MO2, waits for refresh completion, captures bounded authoritative VFS and plug-in evidence, runs applicable contained libloot analysis, durably writes all evidence, and emits SafeToClose. A `not-completed` or `ambiguous-callback` path may emit SafeToClose only after durable failure evidence and a completed safety refresh; it is permanently ineligible for Keep.
6. The user closes MO2 only after SafeToClose. Closing earlier produces incomplete evidence and can never produce a Keep-capable report.
7. ModLab captures immediate post-exit evidence, restores only proven allowlisted transient manager configuration, captures stable final evidence, and checks containment and exact authorized deltas.
8. Analyzer providers produce one immutable report with explicit applicability, coverage, and decision gating.
9. Optional accepted fixes run transactionally against Lab-only state. A distinct one-use `Analyze` bridge request reopens MO2, refreshes Lab, captures new evidence, runs applicable contained analysis, and creates a replacement report.
10. Keep retains the unique installed folder, creates the after checkpoint and immutable receipt, and selects the new checkpoint. Undo restores the exact pre-job Lab state and moves only proven job-owned content to quarantine.

MO2 may create its target directory before extraction completes. A cancellation, crash, or forced shutdown can therefore leave a partial folder. A folder is job-owned only when the proven containment mechanism confines the installation to that new path and the bridge evidence identifies it; a name suggestion alone never establishes ownership.

## Architecture

The subsystem extends the current archive vault, Skyrim observation/checkpoint workflow, MO2 scanner/projection, process inspection, and transaction safety patterns. It does not create a second manager model.

### 0. Pre-write containment capability gate

The first implementation task is a bounded spike against the exact retained MO2 2.5.2 build. It must prove one of these mechanisms before any real archive is installed into the production shared `mods` store:

1. a supported MO2 capability fixes the final destination and refuses an existing name before writing;
2. an isolated installation store preserves the Lab-visible conditions required by Quick, Manual, and FOMOD installers, then adopts exactly one completed unique directory; or
3. a proven filesystem write barrier permits the job-owned new directory while preventing mutation, rename, or deletion of every pre-existing directory.

The spike deliberately does not predetermine the mechanism. In particular, an empty isolated store is unacceptable if it changes FOMOD dependency detection or the effective Lab view. A filesystem barrier is unacceptable if MO2 can bypass it, partially mutate metadata, or leave ambiguous backup folders.

The destructive acceptance test changes the final installer target to an existing Play-referenced folder and attempts both Merge and Replace. The operation must fail before the existing folder's first write, rename, or deletion; its complete payload hash and identity must remain unchanged; Play must remain intact; no backup ambiguity may be created; and recovery must not need reconstructed bytes. If no mechanism passes, direct installation into the shared production store is unsupported in this increment. Synthetic isolated validation may continue, but the production command refuses safely.

### 1. Guided-install service

The service owns preflight, the durable state machine, child-process launch/wait, analysis orchestration, decisions, and recovery. Files remain focused by responsibility: job model/serialization, job store, preflight/evidence, launch, analysis, decision/recovery, rendering, and CLI dispatch.

It accepts only immutable identifiers and resolved configured roots. No public method accepts an arbitrary mod destination, MO2 executable, profile name, quarantine target, or game path. Preflight also requires no other nonterminal install, analysis, fix, promotion, recovery, or bridge-bootstrap job.

### 2. ModLab Guard bridge

A small standalone Python module plugin is stored as source in the repository and deployed to:

```text
workspace/tools/mo2/skyrim-se-ae/app/plugins/modlab_guard/
```

The current live manager does not yet contain this bridge. Deployment is a separate explicit `bridge setup`, `bridge verify`, or `bridge uninstall` transaction, never an implicit repair inside a mod-install job. Setup occurs only while MO2 is idle, uses exact bundled-file hashes and atomic replacement, records exact prior bytes, and creates its own receipt and rollback path. Normal install and analysis jobs require the verified receipt and every declared bridge byte to match. The launched MO2 process disables Python bytecode generation for this bridge; setup verification rejects unowned `__pycache__` or other undeclared bridge content.

The bridge has a deliberately narrow job protocol. A request kind is `Install`, `Analyze`, or `Handshake`; `Handshake` performs all identity/path checks and emits evidence but is reserved for deployment validation and never opens an installer. Every request is one-use and is atomically renamed from `pending` to `claimed` before any action. A reload, restart, or recovery attempt cannot claim it twice.

- receive the exact request path and request SHA-256 through the child process environment;
- atomically claim the request, then clear the handoff environment variables before any child process can inherit them;
- verify schema, nonce, job ID, archive hash/path when applicable, MO2 version, bridge receipt, `profileName`, `profilePath`, `modsPath`, `overwritePath`, and workspace containment;
- register the full `onAboutToRun` veto and all lifecycle callbacks before initiating work;
- wait for `onUserInterfaceInitialized`, verify `ModLab - Lab`, request refresh, and wait for the matching refresh boundary;
- for an `Install` request, register the non-deprecated mod-installed callback and call `IOrganizer.installMod(archive, unique_name_suggestion)` exactly once;
- require both a returned installed-mod object and one matching callback for a completed result; otherwise report `not-completed` or `ambiguous-callback` without claiming a more specific cancellation/failure reason;
- refresh after installation and wait for completion before reading the VFS, mod list, or plug-in list;
- for `Install` and `Analyze`, stream bounded VFS and plug-in evidence, run applicable contained libloot analysis through MO2, and report `Partial` if a declared enumeration limit is reached;
- atomically persist every event and evidence digest before writing SafeToClose; and
- remain passive after the one requested operation.

While any guarded session is nonterminal, the launch veto rejects Skyrim, SKSE, xEdit, and every other external executable. The only exception is the exact job-approved contained analyzer, whose canonical executable identity, arguments, working directory, job identity, profile, and writable output roots must all match the request. The bridge starts that analyzer itself and waits for it. A rejected launch is durably recorded. Failure to register the veto or any violation of its allowlist makes the session RecoveryOnly.

It never downloads, authenticates, changes Play, sorts or applies a load order, starts Skyrim, installs a second requested archive, or writes persistent settings. It does not import the main ModLab package inside MO2; the bridge protocol is independently strict and versioned. Unexpected user actions remain detectable unauthorized deltas even when MO2 offers no UI veto for them.

The generated folder name includes a sanitized archive display name, a short archive hash, and a job suffix. Preflight proves it is absent case-insensitively, but this suggestion is not a safety mechanism. The separate pre-write containment capability must prevent a user-selected existing name, Merge, or Replace from touching pre-existing content. Any mismatch between the proven mechanism, callback object, actual new directory, and job request is RecoveryOnly.

### 3. Evidence collector

The evidence collector is the only component that walks live game, manager, profile, or mod paths. It produces canonical manifests consumed by the safety checker and analyzers. Routine work must scale primarily with job-owned and observed-changed files, not total installed bytes.

Before an existing legacy mod becomes a trusted ModLab artifact, adoption performs one complete payload hash and records a versioned immutable manifest. Later jobs reuse that manifest while a verified change-proof provider watches the underlying volume. The provider records stable file/directory identities and a loss-detecting filesystem-change interval, such as a bounded NTFS USN journal range or another independently validated mechanism. Every changed, replaced, suspicious, newly discovered, or identity-mismatched path is fully rehashed. A same-name, same-size replacement must still be detected. File size, timestamps, and file IDs without loss-detecting change evidence are not sufficient.

If the selected volume does not support the verified mechanism, its journal has wrapped, a watcher overflows, or the captured interval is incomplete, ModLab either performs a clearly disclosed slow full rescan or refuses the high-assurance workflow. It never silently returns Clear from metadata-only evidence.

Before launch it records:

- current selected checkpoint identity and verified bytes;
- archive identity and stored payload path;
- MO2 executable, configuration, bridge, and configured-root identities;
- complete bytes for the bounded Lab and Play profile state files;
- trusted immutable payload-manifest identities for existing installed mods, plus complete rehashes for change-reported or suspicious paths;
- separate complete identities for small mutable MO2 metadata such as each `meta.ini`, download-side `.meta`, profile files, and manager configuration;
- trusted retained-archive identities, top-level download membership, and change-proof coverage for download payloads;
- complete identities for Overwrite, which must match its expected baseline and remain unchanged;
- bounded Skyrim executable and top-level `Data` identities already covered by the Skyrim workflow; and
- the exact pre-launch manager process observation and change-proof start boundary.

The Lab profile and bounded manager configuration files that may need restoration are copied into the job's before-state directory. Existing installed mod payloads, game files, downloads, and Play are not copied wholesale. The pre-write containment gate must prevent damage that those manifests cannot reverse.

After MO2 exits, the collector closes the change-proof interval, fully hashes the job-owned new folder, and rehashes every reported or suspicious pre-existing path before ModLab changes anything. It then verifies that configured roots did not change and that any manager-configuration delta is limited to an explicit allowlist of MO2 session/UI keys. Only that proven transient configuration is restored to its exact before bytes. A second stable final capture covers the small mutable state, new folder, and any paths affected during restoration. It must match the authorized state. A configured-root change, incomplete change proof, or non-allowlisted manager delta is a containment failure, not something ModLab hides by restoration.

Every fully observed regular-file identity contains its normalized relative path, SHA-256, and size; directory manifests also record entry type, stable identity when available, and redirection state. Runtime payload and MO2/download metadata are separate evidence domains: a `meta.ini` change is manager metadata, while a changed ESP, DLL, script, mesh, texture, BSA, or retained archive byte invalidates its payload artifact. Every observed path is case-insensitively unique, direct, non-reparse, and confined to its declared root. Evidence that changes during any capture blocks analysis.

The direct install-session allowlist permits only one new folder membership, its MO2-assigned initial Lab enablement and priority, plug-in entries physically supplied by that folder at deterministic initial positions, and identified installer-produced metadata inside the new folder. Reordering or toggling an existing mod or plug-in, editing a profile INI, changing Play, changing another artifact, touching Overwrite, or installing a second archive makes the result RecoveryOnly. Undo restores the exact captured pre-job Lab state, not merely a possibly older selected checkpoint.

### 3a. Play normalization contract

The MO2 projection retains raw Play bytes but also produces a versioned logical form. A retained job-owned regular folder that is absent from Play's `modlist.txt` is represented as an implicit disabled entry at MO2 2.5.2's verified deterministic new-mod position. If a later Play refresh produces exactly the expected explicit `-Mod Name` row with no other Play delta, both forms have the same logical identity and Play remains Matched. The raw-byte change is still reported as normalization evidence.

This exception is narrow: it is tied to the installation receipt, exact folder identity, verified MO2 version, expected marker and position, and absence of every other raw or logical change. It does not hide enablement, arbitrary disabled-mod reordering, unrelated comment/byte edits, plug-in changes, INI changes, or an unknown new folder. This is a targeted amendment to the MO2 state-projection and Skyrim drift contracts, not a general weakening of byte validation.

The normal workflow never opens or hashes `.ess` or `.skse` save/co-save content. Profile-local saves remain disabled. Live validation may compare save directory identities in memory only; it never persists save names or content.

### 4. Analyzer providers

Analysis uses a provider interface so later xEdit record analysis and runtime evidence can be added without changing the decision or receipt contracts. Every provider returns its own version, evidence digest, applicability reason, coverage state, findings, and zero or more proposed fixes. Coverage is exactly one of `Complete`, `Partial`, `Unknown`, or `NotApplicable`:

- `Complete` means the applicable declared scope ran to completion, including a successful zero-match result.
- `Partial` means only a declared subset was inspected and the omitted part is identified.
- `Unknown` means an applicable provider could not establish a result.
- `NotApplicable` means the archive has no input in that provider's declared scope and requires no acknowledgement.

Provider failure becomes Unknown; it cannot manufacture a clean result. Out-of-scope runtime, performance, and xEdit categories are `NotInspected` product claims rather than provider failures.

The initial providers are:

- **Safety provider (mandatory):** proves containment and exact authorized changes. Any failure makes the whole report `RecoveryOnly`.
- **File-conflict provider:** combines disk manifests with the bridge's MO2 virtual-file evidence. For every path supplied by the new mod, it shows all enabled Lab origins and the current winner. MO2's virtual-file result is authoritative for loose/archive precedence; ModLab does not invent simplified BSA rules. Incomplete BSA or VFS enumeration is `Partial` when the inspected subset is known, otherwise `Unknown`.
- **Plug-in provider:** enumerates `.esm`, `.esp`, and `.esl` origins, case-insensitive duplicates, enabled state, order, and declared masters. A missing enabled master and a plug-in with competing providers are Serious findings. An archive supplying no plug-ins is `NotApplicable`.
- **Contained libloot provider:** uses a version-pinned official libloot wrapper/helper retained under `workspace/tools/libloot/` with a separate verified tool receipt. During an `Install` or `Analyze` bridge session, the helper is launched through MO2's Lab virtual filesystem so it sees the effective Lab plug-ins, but every writable/data argument points to the current job or an immutable masterlist/prelude retained under `workspace/library/loot/skyrim-se-ae/`. It loads those explicit paths, evaluates metadata and calculates a proposed order in memory, and writes only a canonical job-local report. It never updates metadata, applies the proposed order, or writes the live profile. No supplied plug-ins is `NotApplicable`. A missing/unverified helper or metadata snapshot, unproven write location, launch failure, or report parse failure for a plug-in-changing archive is `Unknown` while preserving local plug-in checks.
- **Known-rule provider:** evaluates versioned, reviewable catalogue rules against archive hash, trusted source identity, installed metadata, files, and plug-ins. Each match names its rule revision and evidence. A successful catalogue evaluation with zero matches is `Complete`, not Unknown. Web popularity, comments, or filenames alone never become a compatibility rule.

The report explicitly leaves these areas unproven in this increment:

- xEdit record-level conflicts and generated patches;
- Papyrus or native-code behavior;
- animation, behavior, shader, body, and physics pipeline correctness;
- performance and VRAM impact; and
- launch, save-load, quest, and long-session stability.

### 5. Fix planner

Initial automatic fixes are intentionally limited to reversible Lab-profile operations whose inverse is exact:

- enable or disable the new mod in Lab;
- disable a retained older version in Lab without deleting it;
- enable or disable a Lab plug-in; and
- move a Lab mod or plug-in relative to an exact shared anchor when a trusted rule supplies that requirement.

A fix plan records the report it was derived from, every current precondition, exact before/after profile bytes, explanation, affected findings, and inverse operation. Apply uses the existing transaction safety pattern while MO2 is closed. It cannot touch Play, payload files, Overwrite, the game, or saves. A changed precondition refuses the fix. Successful apply invalidates the old report, creates a one-use `Analyze` request, reopens MO2 in Lab, waits for UI and refresh completion, captures fresh VFS/plug-in evidence, runs applicable contained analysis, closes only after SafeToClose, and generates a replacement report. Old evidence or its report hash can never authorize Keep after a fix.

No initial fix edits INI payloads, deletes files, merges folders, cleans plug-ins, runs xEdit, creates patches, or claims two mods have been made compatible.

## Decision Policy

One report has exactly one gate:

- **Clear:** all mandatory safety capabilities and every applicable compatibility provider are Complete, and no Serious finding exists. Keep and Undo are shown normally. A pluginless loose-file mod can be Clear when every applicable check completes.
- **AcknowledgementRequired:** safety is Complete, but at least one Serious compatibility finding or material applicable `Partial`/`Unknown` result exists. `NotApplicable`, a completed zero-match rule evaluation, and explicitly out-of-scope `NotInspected` runtime/xEdit categories do not require acknowledgement. Keep remains available only after showing the findings and receiving acknowledgement tied to the exact report SHA-256. Undo remains immediate.
- **RecoveryOnly:** containment, job ownership, stable evidence, bridge identity, or recovery prerequisites are incomplete or failed. Keep is disabled. ModLab offers Undo/recovery and explains the exact blocker.

Warnings do not automatically mean incompatibility, and absence of a detected conflict does not mean compatibility. `Clear` means only that this increment's declared checks completed without a serious finding.

## Durable State and Storage

One job is stored beneath:

```text
workspace/runtime/jobs/mo2-mod-install/<job-id>/
  request.json
  journal.json
  before/
  bridge/
  evidence/
  reports/
  fixes/
  temp/
```

Kept receipts are stored beneath:

```text
workspace/games/skyrim-se-ae/mod-installations/
```

Undo quarantine is stored beneath:

```text
workspace/library/quarantine/mo2-mod-install/<job-id>/
```

Optional verified LOOT analysis inputs remain organized beneath:

```text
workspace/tools/libloot/<version>/
workspace/library/loot/skyrim-se-ae/<metadata-revision>/
```

Trusted adopted payload manifests and separate bridge-bootstrap transactions remain beneath:

```text
workspace/library/manifests/skyrim-se-ae/
workspace/runtime/jobs/mo2-bridge/<job-id>/
```

This install workflow never downloads or updates either input. If an applicable plug-in-changing archive requires contained libloot and a separately verified setup has not retained the inputs, that provider reports Unknown and the decision gate requires acknowledgement. For a pluginless archive, the provider is NotApplicable.

All request, evidence, report, fix-plan, receipt, and quarantine-manifest documents use strict schemas, canonical JSON, lowercase SHA-256 identities, case-insensitive Windows-name uniqueness, and atomic write/replace. Unknown fields, unsafe paths, identifier mismatches, and impossible state combinations are rejected.

The high-level durable states are:

```text
Prepared -> Mo2Starting -> Mo2Active -> SafeToClose -> AwaitingReview -> Kept
                                                            \-> Undone
AwaitingReview -> Fixing -> Analyzing -> SafeToClose -> AwaitingReview
any nonterminal state ---------------------------------------> RecoveryRequired
```

`SafeToClose` is a durable session boundary, not Keep eligibility. A completed path requires matching return/callback evidence, completed refresh, applicable contained analysis, and all durable event writes. A `not-completed` or `ambiguous-callback` path requires durable failure evidence and a completed safety refresh and can never become Keep-capable. Internal `Fixing`, `Keeping`, and `Undoing` markers are written before their first mutation so interrupted actions can be resumed. Terminal `Kept` and `Undone` are immutable and idempotently readable.

Every job records:

- archive ID/hash and exact stored bytes;
- selected before checkpoint;
- expected unique folder, containment-capability receipt, atomic bridge claim, and request identity;
- all before/after evidence digests;
- actual bridge lifecycle events, executable-veto evidence, SafeToClose boundary, installed-mod return/callback identity, and result class;
- trusted payload-manifest identities, change-proof range/coverage, rehashed paths, and separately classified manager metadata;
- any exact pending Play disabled-row normalization allowance;
- every report and accepted acknowledgement;
- every previewed/applied fix and inverse;
- decision transitions and exact paths written/launched;
- quarantine identity when undone; and
- after checkpoint and kept receipt when kept.

Child `TEMP` and `TMP` point to the job's contained `temp` directory. Bridge, libloot, reports, logs, caches, and recovery data created by this subsystem remain under `C:\Users\red\Desktop\Modlab`. The external Steam game remains at its registered Steam path and is read-only for this workflow.

## Keep Semantics

Keep is allowed only from the current `AwaitingReview` state and exact current report. It performs this crash-safe sequence:

1. verify MO2 is closed and revalidate all report preconditions;
2. write `Keeping` before the first persistent decision mutation;
3. retain the new unique folder exactly as installed, prove it is absent or disabled in Play, and record the narrow implicit-disabled normalization allowance without writing Play;
4. create a new immutable Observed Skyrim checkpoint for the resulting state;
5. create and reload an immutable installation receipt linking before checkpoint, after checkpoint, archive hash, installed folder tree hash, bridge evidence, installer result, accepted fixes, conflict report, acknowledgement, and coverage;
6. select the after checkpoint; and
7. verify raw containment and canonical logical status are Matched before writing terminal `Kept`.

If the checkpoint, receipt, pointer, or final verification step is interrupted, recovery resumes from the recorded evidence. It never creates a second folder or reruns the installer. Older version folders remain present and unchanged; their Lab enablement changes only through an accepted fix. Opening Play later may materialize the exact disabled row covered by the receipt; any wider change is ordinary Play drift.

The receipt records the resultant payload and observed metadata. MO2 does not expose a universal trustworthy record of every individual button clicked in every third-party installer, so the receipt does not claim to reproduce unobserved click history. Reproducibility is based on the retained archive plus the exact installed tree and evidence, with explicit Unknown when installer-choice metadata is unavailable.

## Undo and Recovery Semantics

Undo is allowed after a safe completed install even when compatibility findings are serious. It:

1. verifies MO2 is closed and current paths still match the reviewed state;
2. writes `Undoing`;
3. atomically renames only the proven job-owned folder into the exact job quarantine path;
4. restores the exact captured pre-job Lab/profile and bounded manager configuration bytes using allowlisted transaction operations, regardless of which older checkpoint was originally selected;
5. verifies Play, all pre-existing mod folders, downloads, Overwrite, game evidence, and selected checkpoint match the before-state; and
6. writes the quarantine manifest and terminal `Undone` state.

No recovery branch recursively deletes a mod. Quarantine is a same-workspace rename when possible and remains recoverable.

At the next ModLab invocation, unfinished jobs are inspected before a new job can start:

- If the managed MO2 process is still running, ModLab waits or refuses; it never competes with it.
- `Prepared` with no launched process and no changes can become `Undone` without touching manager state.
- A canceled/failed install with only the exact expected partial folder may quarantine that folder and restore Lab state.
- A completed callback with exactly one new confined folder may continue to review or undo.
- MO2 closure before SafeToClose leaves incomplete evidence, disables Keep, and permits only proven recovery/Undo.
- An interrupted fix, Keep, or Undo resumes from its durable marker and exact before/after evidence.
- A changed existing mod, Play, game, Overwrite, unknown new folder, modified job record, ambiguous callback, redirected path, or unprovable current state remains `RecoveryRequired`. Unknown content is left in place.

Automatic restoration is limited to paths whose exact prior bytes were captured and whose current bytes match a recorded intermediate state. ModLab reports manual recovery evidence when that proof is absent; it does not guess.

## Error and Exit Contracts

Expected failures are plain-language safe refusals, not tracebacks:

- exit `0`: terminal Keep/Undo/recovery succeeded, or an explicit read-only `review` produced a current Clear/AcknowledgementRequired report;
- exit `2`: invalid identifier, schema, argument combination, or unsupported bridge/release;
- exit `3`: preflight blocked, MO2 still running, an unattended `install` stopped at AwaitingReview without a decision, a `keep` call lacks its required acknowledgement, the current gate is RecoveryOnly, a precondition changed, or recovery remains required.

Every result reports exact `pathsWritten`, `downloadsPerformed`, `installationActionsPerformed`, `managerChangesPerformed`, `gameChangesPerformed`, and `programsLaunched` arrays. MO2, the contained libloot helper, and no other process may appear unless actually launched. `downloadsPerformed` and `gameChangesPerformed` remain empty in this increment.

## Testing

Implementation follows test-driven development.

### Unit and fixture coverage

- strict round trips and rejection for every request, journal, event, evidence, report, fix, receipt, and quarantine schema;
- every valid and invalid state transition, repeated decisions, stale report acknowledgement, and idempotent recovery;
- archive missing/modified, pre-existing Lab or Play checkpoint drift, another nonterminal job, manager/bridge/root/manifest drift, running process, profile mismatch, and redirected path refusal;
- atomic request claim/replay refusal, pre-UI startup, premature refresh evidence rejection, completed/not-completed/ambiguous-callback results, duplicate callback, cancellation, partial extraction, premature close, and unexpected extra folder;
- forced user rename, Merge, and Replace against an existing Play-referenced folder, proving rejection before its first mutation and proving no backup/reconstruction dependency;
- mutation of Play, existing mod content, game evidence, downloads, and Overwrite at every boundary;
- exact install-session allowlisting: only the new mod, its own plug-ins, deterministic placement, and installer metadata pass; unrelated mod/plugin state or order changes, profile INI edits, Play changes, and second archives are RecoveryOnly;
- implicit-disabled versus explicit `-Mod Name` Play normalization after Keep, plus rejection of the wrong marker, wrong position, wrong folder, wrong MO2 version, or any accompanying delta;
- trusted-manifest reuse for a large unchanged library, changed-path rehashing, same-name/same-size replacement detection, manager-metadata separation, retained-archive mutation, and journal wrap/watcher overflow fallback or refusal;
- executable-veto registration and rejection of Skyrim, SKSE, xEdit, and unrelated tools, while permitting only the exact contained analyzer path/arguments/output roots;
- loose-file and archive-origin winner chains, case collisions, scripts/configuration grouping, and incomplete archive inventory;
- missing masters, duplicate plug-in providers, disabled masters, order findings, malformed/absent libloot reports, `NotApplicable` pluginless analysis, and proof that proposed order output never replaces the live list;
- known-rule matching by exact trusted identity, `Complete` zero-match evaluation, and rejection of ambiguous filename-only matches;
- fix preview, decline, apply, rollback, interruption, changed precondition, a distinct Analyze request, fresh winner/order evidence, and mandatory replacement-report reanalysis;
- bounded VFS enumeration returning Partial rather than blocking MO2, and material gating for applicable Partial/Unknown only;
- Keep crash boundaries around checkpoint, receipt, pointer, and terminal journal writes;
- Undo crash boundaries around quarantine rename, profile restore, verification, and terminal journal writes; and
- structural proof that save/co-save paths cannot enter a plan, manifest, fix, or transaction.

### Contained integration coverage

A disposable portable MO2 instance and harmless synthetic archives cover:

- the selected pre-write containment mechanism, including forced Merge and Replace attempts against a protected existing folder;
- Quick install;
- Manual install;
- a deterministic FOMOD fixture;
- user cancellation and forced MO2 termination during extraction;
- UI-ready and refresh barriers, SafeToClose persistence, blocked executable launches, and closing before SafeToClose;
- Keep, Undo, optional fixes, restart recovery, and a second version retaining the first;
- Play's first refresh after Keep, proving the new folder remains disabled and the logical checkpoint stays Matched;
- VFS loose/archive winner evidence through the real bridge;
- the contained libloot provider before MO2 exit, with immutable input metadata and every output redirected to the job;
- post-fix Analyze sessions proving mod-priority and plug-in-order changes replace stale evidence;
- a large unchanged synthetic library proving routine bytes read are driven by job-owned/changed paths rather than total payload size; and
- verification that the disposable raw Play profile, game tree, existing mod folders, downloads, and Overwrite remain unchanged during the guarded session, with only the explicitly expected new shared mod folder retained after Keep.

### Live Windows validation

Live validation proceeds in increasing-risk order:

1. in a disposable exact MO2 2.5.2 copy under `workspace/runtime/validation`, prove the pre-write containment mechanism by forcing both Merge and Replace against a protected existing folder;
2. run the separate bridge setup/verify transaction in that disposable copy and prove rollback, uninstall, bytecode-cache control, and one-use request behavior;
3. run synthetic Quick, Manual, and FOMOD installs only in that disposable copy, using computer control for the real installer windows, lifecycle barriers, veto tests, and normal closure after SafeToClose;
4. validate large-library change tracking, Play's post-Keep disabled-row normalization, contained libloot timing, and post-fix Analyze sessions in the disposable copy;
5. only if the containment spike passed, deploy and verify the already-proven bridge through the separate setup transaction while the production managed MO2 instance is closed;
6. perform a no-install handshake against the production `ModLab - Lab` profile and prove byte-for-byte preservation;
7. verify before/after identities for production Play, game, existing mods, downloads, Overwrite, manager configuration, and in-memory save-directory observations; and
8. only later, with the user present, install one user-selected real mod into the production Lab and ask Keep/Undo.

The destructive Merge/Replace tests never target production. If the capability spike fails, steps 5-8 are prohibited and production installation remains unsupported. The production test never chooses a real mod or FOMOD option on the user's behalf. Computer access is required only for the visible MO2/FOMOD validation stages.

The full automated suite, `git diff --check`, current Skyrim status, archive verification, checkpoint verification, and post-action tree identities must all pass before the feature is merged. No test result may be generalized into “the game runs perfectly”; runtime launch/save testing is a later gate.

## Primary Implementation Evidence

The design is pinned to the currently managed MO2 2.5.2 behavior and these primary interfaces:

- MO2's Python wrapper exposes `profileName`, `profilePath`, `modsPath`, `installMod`, `getFileOrigins`, `findFileInfos`, `virtualFileTree`, plug-in/mod lists, installed/refreshed callbacks, UI initialization, refresh, application launch/wait, and `onAboutToRun`: <https://github.com/ModOrganizer2/modorganizer-plugin_python/blob/master/src/mobase/wrappers/basic_classes.cpp>
- MO2 module plug-ins and the `IOrganizer` initialization contract: <https://www.modorganizer.org/python-plugins-doc/writing-plugins.html>
- MO2 command-line profile selection and multiple-process handling: <https://github.com/ModOrganizer2/modorganizer/blob/master/src/commandline.cpp>
- MO2 2.5.2 treats the supplied name as a suggestion, deletes the target for Replace, extracts into it for Merge, and creates a new target before extraction, which requires the pre-write capability gate: <https://github.com/ModOrganizer2/modorganizer/blob/v2.5.2/src/installationmanager.cpp>
- MO2 2.5.2 assigns missing regular folders disabled profile state/priority and writes them into `modlist.txt`, which requires the narrow Play normalization contract: <https://github.com/ModOrganizer2/modorganizer/blob/v2.5.2/src/profile.cpp>
- MO2's organizer proxy exposes refresh completion, application launch/wait, UI initialization, and launch-veto callback plumbing used by the guarded lifecycle: <https://github.com/ModOrganizer2/modorganizer/blob/master/src/organizerproxy.cpp>
- Current libloot provides C++, Python, and Node.js wrappers for explicit metadata access, issue checks, and sorting: <https://github.com/loot/libloot>
- The older helper bundled with MO2 2.5.2 hardcodes `%LOCALAPPDATA%\LOOT`, so ModLab deliberately does not run it: <https://github.com/ModOrganizer2/modorganizer-lootcli/blob/master/src/lootthread.cpp>

Upstream `master` references support interface understanding only; behavior-critical installation and Play-normalization claims use the MO2 `v2.5.2` source. Implementation and tests remain pinned to the exact retained MO2 2.5.2 executable, deployed bridge, containment capability, change-proof provider, and contained-libloot helper identities in their ModLab receipts.

## Non-Goals

This increment will not:

- download mods or log into Nexus/GameBanana;
- browse popularity or select “essential” mods;
- promote Lab changes to Play;
- install more than one archive per job;
- merge, replace, update in place, or delete an older mod folder;
- clean plug-ins, run xEdit record analysis, generate patches, or run Nemesis/Pandora;
- automatically apply LOOT's sorted list;
- launch Skyrim, SKSE, a save, or a benchmark;
- inspect save/co-save content;
- support Morrowind, Oblivion GOTY, or Oblivion Remastered in this job model yet; or
- claim compatibility, performance, or runtime stability beyond the exact reported coverage.

The provider and job boundaries are game-adapter-friendly, but each later Bethesda game receives its own researched adapter and validation rather than inheriting Skyrim assumptions.
