# Guided MO2 Lab Installation and Conflict Review Design

**Date:** 2026-08-31

**Status:** Chat design approved; pending written-spec review

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
- Unknown means not proven. It never silently becomes Pass or Compatible.

## Safety Vocabulary

The portable MO2 instance has one important limitation: `mods` and `overwrite` are physically shared by Lab and Play even though selection, order, plug-in state, and profile INIs are separate.

This design therefore uses these exact meanings:

- **Play state** is the byte state of `ModLab - Play` and its logical enabled/order projection.
- **Existing shared content** is every mod folder present before the job, plus pre-existing downloads and Overwrite content.
- **Job-owned content** is at most one new mod folder whose creation is tied to the exact bridge request and install callback for this job.
- **Authorized change** is the new job-owned folder plus bounded changes to the Lab profile that result from installing it.
- **Containment failure** is any modification to Play, an existing mod folder, the game, saves, MO2's configured roots, pre-existing downloads, or Overwrite; an unexpected new mod folder is also a containment failure.

Adding the job-owned folder to the shared `mods` directory is expected. Enabling it in Play, changing any folder that existed before the job, or adding a folder that cannot be attributed to the job is not.

## User Experience

The primary text workflow is:

```text
python -B -m modlab skyrim mod install \
  --artifact archive-sha256:<hash> \
  --workspace .\workspace
```

The command performs preflight checks, opens the managed `ModLab - Lab` profile, and starts exactly one normal MO2 installation. The user makes any Quick, Manual, or FOMOD selections and closes MO2. ModLab then shows five short sections:

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

1. Verify the selected Skyrim environment, selected checkpoint, archive, MO2 receipt, guard bridge, path containment, and idle process state.
2. Persist the job request and exact before-state evidence before launching anything.
3. Launch only the managed `ModOrganizer.exe` with `--profile "ModLab - Lab"` and a one-use job handoff.
4. The guard bridge verifies its environment and calls MO2's normal `installMod()` once with a collision-free name suggestion.
5. The user completes or cancels the installer and closes MO2.
6. ModLab captures immediate post-exit evidence, restores only proven allowlisted transient manager configuration, captures stable final evidence, and checks containment.
7. Analyzer providers produce one immutable report with explicit coverage and decision gating.
8. Optional accepted fixes run transactionally against Lab-only state and force complete reanalysis.
9. Keep retains the unique installed folder, creates the after checkpoint and immutable receipt, and selects the new checkpoint. Undo restores the exact Lab before-state and moves only proven job-owned content to quarantine.

MO2 may create its target directory before extraction completes. A cancellation, crash, or forced shutdown can therefore leave a partial folder. The job owns its unique suggested name before launch and also records the actual successful install callback. This lets recovery identify the expected partial folder without treating arbitrary new content as owned.

## Architecture

The subsystem extends the current archive vault, Skyrim observation/checkpoint workflow, MO2 scanner/projection, process inspection, and transaction safety patterns. It does not create a second manager model.

### 1. Guided-install service

The service owns preflight, the durable state machine, child-process launch/wait, analysis orchestration, decisions, and recovery. Files remain focused by responsibility: job model/serialization, job store, preflight/evidence, launch, analysis, decision/recovery, rendering, and CLI dispatch.

It accepts only immutable identifiers and resolved configured roots. No public method accepts an arbitrary mod destination, MO2 executable, profile name, quarantine target, or game path.

### 2. ModLab Guard bridge

A small standalone Python module plugin is stored as source in the repository and deployed to:

```text
workspace/tools/mo2/skyrim-se-ae/app/plugins/modlab_guard/
```

The current live manager does not yet contain this bridge. Deployment is therefore part of this increment, not an assumed precondition. Deployment occurs only while MO2 is idle, uses exact bundled-file hashes and atomic replacement, and creates a separate bridge receipt. MO2 bootstrap verification recognizes the bridge only when that receipt and every declared bridge byte match; it does not broadly trust third-party plug-ins.

The bridge has a deliberately narrow job protocol. A request kind is either `Install` or `Handshake`; `Handshake` performs all identity/path checks and emits evidence but is reserved for deployment validation and never opens an installer.

- receive the exact request path and request SHA-256 through the child process environment;
- verify schema, nonce, job ID, archive hash/path, MO2 version, `profileName`, `profilePath`, `modsPath`, `overwritePath`, and workspace containment;
- register the non-deprecated mod-installed callback;
- for an `Install` request, call `IOrganizer.installMod(archive, unique_name_suggestion)` exactly once;
- record start, return, cancellation/failure, and successful installed-mod callback evidence by atomic writes under the job directory;
- emit the current virtual-file and plug-in evidence required by providers; and
- remain passive after the one requested installation.

It never downloads, authenticates, changes Play, sorts a load order, starts Skyrim, installs a second archive, or writes persistent settings. It does not import the main ModLab package inside MO2; the bridge protocol is independently strict and versioned.

The generated folder name includes a sanitized archive display name, a short archive hash, and a job suffix. Preflight proves it is absent case-insensitively. If the user changes that name to an existing folder or chooses Merge/Replace in MO2, the existing-folder fingerprint changes, containment fails, and Keep is disabled. ModLab never tries to reconstruct overwritten bytes it did not snapshot.

### 3. Evidence collector

The evidence collector is the only component that walks live game, manager, profile, or mod paths. It captures a stable two-pass read set and produces canonical manifests consumed by the safety checker and analyzers.

Before launch it records:

- current selected checkpoint identity and verified bytes;
- archive identity and stored payload path;
- MO2 executable, configuration, bridge, and configured-root identities;
- complete bytes for the bounded Lab and Play profile state files;
- complete identities for every existing installed mod folder and regular file;
- top-level and recursive identities for downloads and Overwrite;
- bounded Skyrim executable and top-level `Data` identities already covered by the Skyrim workflow; and
- the exact pre-launch manager process observation.

The Lab profile and bounded manager configuration files that may need restoration are copied into the job's before-state directory. Existing installed mod payloads, game files, downloads, and Play are fingerprinted but not copied wholesale. If one of those changes, ModLab can prove the failure but cannot pretend it has safe replacement bytes.

After MO2 exits, the collector first repeats the same read set before ModLab changes anything and adds the new folder's complete file manifest and tree hash. It then verifies that configured roots did not change and that any manager-configuration delta is limited to an explicit allowlist of MO2 session/UI keys. Only that proven transient configuration is restored to its exact before bytes. A second stable final capture must then match the authorized state. A configured-root change or any non-allowlisted manager delta is a containment failure, not something ModLab hides by restoration.

Every regular-file identity contains its normalized relative path, SHA-256, and size; directory manifests also record entry type and redirection state. Every observed path is case-insensitively unique, direct, non-reparse, and confined to its declared root. Evidence that changes during any capture blocks analysis.

The normal workflow never opens or hashes `.ess` or `.skse` save/co-save content. Profile-local saves remain disabled. Live validation may compare save directory identities in memory only; it never persists save names or content.

### 4. Analyzer providers

Analysis uses a provider interface so later xEdit record analysis and runtime evidence can be added without changing the decision or receipt contracts. Every provider returns its own version, evidence digest, coverage state, findings, and zero or more proposed fixes. Provider failure becomes Unknown; it cannot manufacture a clean result.

The initial providers are:

- **Safety provider (mandatory):** proves containment and exact authorized changes. Any failure makes the whole report `RecoveryOnly`.
- **File-conflict provider:** combines disk manifests with the bridge's MO2 virtual-file evidence. For every path supplied by the new mod, it shows all enabled Lab origins and the current winner. MO2's virtual-file result is authoritative for loose/archive precedence; ModLab does not invent simplified BSA rules. If archive contents or a winner cannot be enumerated, the relevant coverage is Unknown.
- **Plug-in provider:** enumerates `.esm`, `.esp`, and `.esl` origins, case-insensitive duplicates, enabled state, order, and declared masters. A missing enabled master and a plug-in with competing providers are Serious findings.
- **Contained libloot provider:** uses a version-pinned official libloot wrapper/helper retained under `workspace/tools/libloot/` with a separate verified tool receipt. The helper is launched through MO2's Lab virtual filesystem so it sees the effective Lab plug-ins, but every writable/data argument points to the current job or an immutable masterlist/prelude retained under `workspace/library/loot/skyrim-se-ae/`. It loads those explicit paths, evaluates metadata and calculates a proposed order in memory, and writes only a canonical job-local report. It never updates metadata, applies the proposed order, or writes the live profile. A missing/unverified helper or metadata snapshot, unproven write location, launch failure, or report parse failure makes LOOT coverage Unknown while preserving the local plug-in checks.
- **Known-rule provider:** evaluates versioned, reviewable catalogue rules against archive hash, trusted source identity, installed metadata, files, and plug-ins. Each match names its rule revision and evidence. Web popularity, comments, or filenames alone never become a compatibility rule.

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

A fix plan records the report it was derived from, every current precondition, exact before/after profile bytes, explanation, affected findings, and inverse operation. Apply uses the existing transaction safety pattern while MO2 is closed. It cannot touch Play, payload files, Overwrite, the game, or saves. A changed precondition refuses the fix. Successful apply regenerates the complete evidence set and report before Keep can be chosen.

No initial fix edits INI payloads, deletes files, merges folders, cleans plug-ins, runs xEdit, creates patches, or claims two mods have been made compatible.

## Decision Policy

One report has exactly one gate:

- **Clear:** all mandatory safety capabilities are complete and no Serious or safety-relevant Unknown finding exists. Keep and Undo are shown normally.
- **AcknowledgementRequired:** safety is complete, but at least one Serious compatibility finding or material analysis Unknown exists. Unknown coverage from the file, plug-in, contained libloot, or known-rule provider is material; the explicitly out-of-scope runtime and xEdit categories are always shown but do not by themselves require acknowledgement. Keep remains available only after showing the findings and receiving acknowledgement tied to the exact report SHA-256. Undo remains immediate.
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

This install workflow never downloads or updates either input. If a separately verified setup has not retained them, the provider reports Unknown and the decision gate requires acknowledgement.

All request, evidence, report, fix-plan, receipt, and quarantine-manifest documents use strict schemas, canonical JSON, lowercase SHA-256 identities, case-insensitive Windows-name uniqueness, and atomic write/replace. Unknown fields, unsafe paths, identifier mismatches, and impossible state combinations are rejected.

The high-level durable states are:

```text
Prepared -> Mo2Running -> AwaitingReview -> Kept
                                      \-> Undone
any nonterminal state -----------------> RecoveryRequired
```

Internal `Fixing`, `Keeping`, and `Undoing` markers are written before their first mutation so interrupted actions can be resumed. Terminal `Kept` and `Undone` are immutable and idempotently readable.

Every job records:

- archive ID/hash and exact stored bytes;
- selected before checkpoint;
- expected unique folder and bridge request identity;
- all before/after evidence digests;
- actual bridge events and installed-mod callback identity;
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
3. retain the new unique folder exactly as installed and prove it is disabled in Play;
4. create a new immutable Observed Skyrim checkpoint for the resulting state;
5. create and reload an immutable installation receipt linking before checkpoint, after checkpoint, archive hash, installed folder tree hash, bridge evidence, installer result, accepted fixes, conflict report, acknowledgement, and coverage;
6. select the after checkpoint; and
7. verify status is Matched before writing terminal `Kept`.

If the checkpoint, receipt, pointer, or final verification step is interrupted, recovery resumes from the recorded evidence. It never creates a second folder or reruns the installer. Older version folders remain present and unchanged; their Lab enablement changes only through an accepted fix.

The receipt records the resultant payload and observed metadata. MO2 does not expose a universal trustworthy record of every individual button clicked in every third-party installer, so the receipt does not claim to reproduce unobserved click history. Reproducibility is based on the retained archive plus the exact installed tree and evidence, with explicit Unknown when installer-choice metadata is unavailable.

## Undo and Recovery Semantics

Undo is allowed after a safe completed install even when compatibility findings are serious. It:

1. verifies MO2 is closed and current paths still match the reviewed state;
2. writes `Undoing`;
3. atomically renames only the proven job-owned folder into the exact job quarantine path;
4. restores the captured Lab/profile and bounded manager configuration bytes using allowlisted transaction operations;
5. verifies Play, all pre-existing mod folders, downloads, Overwrite, game evidence, and selected checkpoint match the before-state; and
6. writes the quarantine manifest and terminal `Undone` state.

No recovery branch recursively deletes a mod. Quarantine is a same-workspace rename when possible and remains recoverable.

At the next ModLab invocation, unfinished jobs are inspected before a new job can start:

- If the managed MO2 process is still running, ModLab waits or refuses; it never competes with it.
- `Prepared` with no launched process and no changes can become `Undone` without touching manager state.
- A canceled/failed install with only the exact expected partial folder may quarantine that folder and restore Lab state.
- A completed callback with exactly one new confined folder may continue to review or undo.
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
- archive missing/modified, checkpoint drift, manager/bridge drift, running process, profile mismatch, and redirected path refusal;
- unique-name collision, user rename, Merge/Replace evidence, no callback, duplicate callback, cancellation, partial extraction, and unexpected extra folder;
- mutation of Play, existing mod content, game evidence, downloads, and Overwrite at every boundary;
- loose-file and archive-origin winner chains, case collisions, scripts/configuration grouping, and incomplete archive inventory;
- missing masters, duplicate plug-in providers, disabled masters, order findings, malformed/absent libloot reports, and proof that proposed order output never replaces the live list;
- known-rule matching by exact trusted identity and rejection of ambiguous filename-only matches;
- fix preview, decline, apply, rollback, interruption, changed precondition, and mandatory reanalysis;
- Keep crash boundaries around checkpoint, receipt, pointer, and terminal journal writes;
- Undo crash boundaries around quarantine rename, profile restore, verification, and terminal journal writes; and
- structural proof that save/co-save paths cannot enter a plan, manifest, fix, or transaction.

### Contained integration coverage

A disposable portable MO2 instance and harmless synthetic archives cover:

- Quick install;
- Manual install;
- a deterministic FOMOD fixture;
- user cancellation and forced MO2 termination during extraction;
- Keep, Undo, optional fixes, restart recovery, and a second version retaining the first;
- VFS loose/archive winner evidence through the real bridge;
- the contained libloot provider with immutable input metadata and every output redirected to the job; and
- verification that the disposable Play profile, game tree, existing mod folders, downloads, and Overwrite remain unchanged except for the explicitly expected job folder while Kept.

### Live Windows validation

Live validation proceeds in increasing-risk order:

1. deploy and verify the bridge in a disposable contained MO2 copy under `workspace/runtime/validation`;
2. run synthetic Quick, Manual, and FOMOD installs only in that disposable copy, using computer control for the real installer windows and closing MO2 normally;
3. deploy and verify the already-proven bridge while the production managed MO2 instance is closed;
4. perform a no-install handshake against the production `ModLab - Lab` profile and prove byte-for-byte preservation;
5. verify before/after identities for production Play, game, existing mods, downloads, Overwrite, manager configuration, and in-memory save-directory observations; and
6. only later, with the user present, install one user-selected real mod into the production Lab and ask Keep/Undo.

The production test never chooses a real mod or FOMOD option on the user's behalf. Computer access is required only for the visible MO2/FOMOD validation stages.

The full automated suite, `git diff --check`, current Skyrim status, archive verification, checkpoint verification, and post-action tree identities must all pass before the feature is merged. No test result may be generalized into “the game runs perfectly”; runtime launch/save testing is a later gate.

## Primary Implementation Evidence

The design is pinned to the currently managed MO2 2.5.2 behavior and these primary interfaces:

- MO2's Python wrapper exposes `profileName`, `profilePath`, `modsPath`, `installMod`, `getFileOrigins`, `findFileInfos`, `virtualFileTree`, plug-in/mod lists, and mod-installed callbacks: <https://github.com/ModOrganizer2/modorganizer-plugin_python/blob/master/src/mobase/wrappers/basic_classes.cpp>
- MO2 module plug-ins and the `IOrganizer` initialization contract: <https://www.modorganizer.org/python-plugins-doc/writing-plugins.html>
- MO2 command-line profile selection and multiple-process handling: <https://github.com/ModOrganizer2/modorganizer/blob/master/src/commandline.cpp>
- MO2 creates a new target directory before extraction and exposes explicit Merge/Replace paths for collisions, which is why unique ownership and postflight verification are mandatory: <https://github.com/ModOrganizer2/modorganizer/blob/master/src/installationmanager.cpp>
- Current libloot provides C++, Python, and Node.js wrappers for explicit metadata access, issue checks, and sorting: <https://github.com/loot/libloot>
- The older helper bundled with MO2 2.5.2 hardcodes `%LOCALAPPDATA%\LOOT`, so ModLab deliberately does not run it: <https://github.com/ModOrganizer2/modorganizer-lootcli/blob/master/src/lootthread.cpp>

These upstream `master` references support interface understanding only. Implementation and tests remain pinned to the exact retained MO2 2.5.2 executable and verified contained-libloot helper identities in their ModLab receipts.

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
