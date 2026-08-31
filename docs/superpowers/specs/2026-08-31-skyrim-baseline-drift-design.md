# Skyrim Baseline and Drift Design

**Date:** 2026-08-31
**Status:** Approved in chat; written specification awaiting review
**Scope:** Skyrim SE/AE on Steam with the contained portable MO2 instance

## Purpose

ModLab can now inspect the live Skyrim and portable MO2 installations, project a deterministic Lab/Play state, and explain the current differences between those two profiles. The next increment will turn that transient observation into a durable safety reference.

The user will register one Skyrim environment once, capture an immutable observed baseline, and later ask whether the live environment still matches it. This is the state reference ModLab needs before it changes MO2, installs a foundation, runs tools, or offers promotion and rollback.

An observed baseline is deliberately weaker than a tested or promoted checkpoint. It proves what ModLab read and stored at one instant. It does not prove that the selected Foundation Recipe has been assembled, that installed payload contents are recoverable, that conflicts are solved, or that Skyrim runs.

## User Experience

The workflow has three commands:

```powershell
python -B -m modlab skyrim configure `
  --steam-root 'C:\Users\red\Desktop\Steam' `
  --recipe '.\catalogue\recipes\skyrim-se-ae-current-draft.json' `
  --environment '.\catalogue\environments\skyrim-steam-1.7.104.json' `
  --workspace '.\workspace'

python -B -m modlab skyrim baseline create --workspace '.\workspace'

python -B -m modlab skyrim status --workspace '.\workspace'
```

`configure` verifies and remembers the environment. `baseline create` captures a stable checkpoint and selects it as the environment's comparison baseline. `status` re-inspects the live environment and explains whether it matches that checkpoint.

After configuration, the normal commands require no repeated game, MO2, recipe, or Steam paths. JSON output remains available through `--format json`.

The current live environment is a suitable first observed baseline: Skyrim and MO2 are readable, the exact Lab and Play profiles are coherent, no managed mod folders or Overwrite entries are present, and the only current Lab/Play difference is `archives.txt`.

## Scope

This increment will:

- persist one strictly validated Skyrim environment configuration under the ModLab workspace;
- retain an immutable copy of the exact Foundation Recipe source selected during configuration;
- retain an immutable copy of the exact target-environment evidence used to review that recipe;
- record the exact component selection and recipe-review result;
- inspect Skyrim and MO2 again before every baseline capture or status check;
- create an immutable checkpoint only from complete, coherent, stable evidence;
- select the newly created checkpoint as the environment baseline using an atomic pointer update;
- compare current environment identity and canonical MO2 state with the selected baseline;
- explain drift by domain rather than reporting only a changed hash;
- preserve explicit coverage limitations and side-effect reports; and
- keep every new file beneath `C:\Users\red\Desktop\Modlab\workspace`.

This increment will not:

- download, extract, install, update, enable, disable, reorder, clean, patch, repair, or remove a mod;
- create or modify an MO2 profile;
- modify Skyrim, Steam, MO2, saves, or SKSE co-saves;
- inspect complete installed mod payloads, asset conflicts, or plug-in records;
- assert that a Foundation Recipe is assembled merely because its selection was recorded;
- launch MO2, Skyrim, SKSE, LOOT, xEdit, or a generator;
- perform a smoke test or runtime validation;
- create a promotion-ready or restorable checkpoint;
- promote, restore, or roll back state; or
- add Morrowind, Oblivion Classic, or Oblivion Remastered behavior.

## Terminology and Truth Model

### Registered environment

A registered environment is a local configuration that identifies one Steam library, one derived Skyrim installation, the one contained portable Skyrim MO2 instance, one exact recipe snapshot, one exact recipe selection, one lineage, and an optional selected baseline checkpoint.

Registration is not installation. It records paths and intent only after live validation.

### Observed baseline

An observed baseline is a content-addressed checkpoint with `captureKind` equal to `ObservedBaseline`. Its evidence records that:

- the game and manager were inspected;
- the MO2 read set remained stable;
- the canonical adapter state was produced without a blocking finding;
- the Foundation Recipe source and selection were identified; and
- all uninspected domains remain explicitly uninspected.

Every observed baseline in this increment has:

- `qualification: Observed`;
- `promotionReady: false`;
- `restorable: false`;
- `runtimeValidation: NotPerformed`; and
- `artifactCoverage: NotLinked`.

These values are fixed, not inferred from an empty mod list or a green comparison.

### Matched

`Matched` means that all evidence in this increment's comparison boundary matches the selected checkpoint. It does not mean that the setup is conflict-free, complete, performant, or playable.

### Drifted

`Drifted` means that the current complete and coherent observation differs from the selected checkpoint in at least one compared domain. Drift is evidence, not an automatic repair instruction.

### Blocked

`Blocked` means ModLab could not obtain or trust all evidence required for a comparison. It will not downgrade missing or contradictory evidence into a partial `Matched` result.

## Storage Layout

The increment adds these user-owned files beneath the existing organized workspace:

```text
workspace/
  games/
    skyrim-se-ae/
      environment.json
      recipes/
        <recipe-source-sha256>/
          recipe.json
      target-environments/
        <environment-source-sha256>/
          environment.json
      checkpoints/
        <checkpoint-sha256>/
          modlab.lock.json
  runtime/
    jobs/
      <temporary atomic-write files only>
```

The recipe and target-environment directories are content-addressed by the SHA-256 of the exact validated source bytes. `configure` copies each source; it never moves or modifies one. An existing content-addressed path must contain the same bytes or configuration blocks.

The existing checkpoint store remains the only checkpoint writer. No second checkpoint format or duplicate installed-mod tree is introduced.

## Environment Configuration

`environment.json` has a strict schema with exact fields. Unknown, missing, duplicate, incorrectly typed, unsafe, or inconsistent fields block use.

Its logical content is:

```text
schemaVersion: 1
gameKey: skyrim-se-ae
environmentId: copied from the retained target-environment evidence
lineageId: skyrim-main
steamRoot: absolute direct directory
gameRoot: absolute path derived from Steam app 489830
manager:
  adapterId: portable-mo2-skyrim
  root: workspace-relative tools/mo2/skyrim-se-ae/app
recipe:
  recipeId
  revision
  maturity
  identity
  sourceSha256
  storedPath: workspace-relative content-addressed recipe path
  selected: sorted component IDs
  omitted: sorted component IDs
targetEnvironment:
  sourceSha256
  storedPath: workspace-relative content-addressed environment path
  dimensions: exact sorted target dimensions
baselineCheckpointId: checkpoint-sha256:<hash> or null
```

`steamRoot` and `gameRoot` are Windows absolute paths because the game intentionally lives outside ModLab. Every manager, recipe-storage, and target-environment-storage path is relative to the workspace and must resolve beneath it without a symlink, junction, or reparse-point escape.

The configured `gameRoot` must equal the path derived from Steam's app `489830` manifest. The user cannot independently supply a conflicting Skyrim directory.

The first successful `configure` creates the file atomically. Repeating the same command is idempotent. A valid existing configuration that would change requires `--replace`; ModLab shows the changed logical fields before replacement. An invalid existing file is never overwritten automatically.

Replacing configuration never deletes a recipe snapshot or checkpoint and never changes Skyrim or MO2. It clears `baselineCheckpointId` unless the old checkpoint is independently proven to belong to the same environment identity, lineage, recipe source, and selection.

## Recipe Snapshot and Selection

`configure` strictly loads the recipe and target-environment evidence, then runs the existing recipe review. `--select ID` and `--omit ID` retain their current meanings and may be repeated. The exact resulting selected and omitted sets are stored.

Target-environment evidence describes the environment the selected recipe is meant to build. It is not treated as proof that every dimension is already present on the live machine. ModLab separately reconciles every dimension it can observe directly:

- Skyrim discovery supplies the live executable runtime;
- MO2 inspection supplies both the raw Windows executable version and a compatibility adapter version; and
- a dimension such as `scriptExtender` remains planned and unverified until a later payload/root inspection actually observes it.

An observed live value that contradicts the target blocks configuration. A target dimension that is not yet observable is recorded as unverified and contributes to `foundationAssembly: NotVerified`; ModLab does not copy the target value into live evidence.

Compatibility-version derivation is fixed and auditable. Skyrim continues using its existing `1.7.104.0` to `1.7.104` runtime derivation. For MO2, an exact four-numeric-component Windows version with a final zero, such as `2.5.2.0`, derives adapter compatibility version `2.5.2`; every raw version, derived value, and target value remains in evidence. No other arbitrary version normalization is allowed.

Configuration blocks when the recipe review is not ready for approval because of a missing Required component, unresolved dependency, declared incompatibility, or blocked environment constraint.

Recipe-selection identity and recipe assembly are separate facts:

- `Original`, `Custom`, or `Incomplete` describes the selection relative to the recipe;
- this command accepts only `Original` or `Custom` selections that are ready for approval; and
- neither value claims that the corresponding mod payloads are installed.

The baseline evidence therefore stores `foundationAssembly: NotVerified` in this increment. Missing archive hashes, installed-tree hashes, exact optional component versions, or configuration artifacts remain visible coverage limitations rather than being silently filled in.

## Configuration Data Flow

`skyrim configure` performs the following sequence:

1. Resolve the workspace and require the standard contained MO2 location.
2. Parse Steam app `489830`, derive the Skyrim game root, and capture the executable identity.
3. Inspect the contained MO2 instance and require a Ready projection for the exact Lab and Play profiles.
4. Strictly load the requested Foundation Recipe and target-environment evidence, reconcile observable live dimensions, and run the recipe review against the retained target environment.
5. Read and validate any existing environment configuration.
6. Build the proposed configuration and show its outcome.
7. If creation or an explicitly authorized replacement is allowed, atomically retain the exact recipe and target-environment bytes.
8. Atomically write `environment.json` through a staging file under `runtime/jobs`.
9. Re-read the stored configuration and recipe snapshot and verify their identities before reporting success.

No configuration, recipe, or target-environment file is written if steps 1 through 6 fail.

## Baseline Capture

`skyrim baseline create` performs the following sequence:

1. Strictly load the environment configuration and retained recipe snapshot.
2. Verify that the recipe and target-environment bytes, identities, revision, selection, and constraints still match the configuration.
3. Rediscover Skyrim from the configured Steam root and require the same configured game root.
4. Inspect MO2, project the canonical adapter state, and require every readiness capability to be complete.
5. Require a stable authoritative MO2 read set and a concrete Skyrim and MO2 executable identity.
6. Construct checkpoint evidence from the game observation, MO2 observation context, readiness capabilities, findings, recipe review, hashes, and explicit coverage values.
7. Store the canonical adapter state and evidence through the existing content-addressed checkpoint store.
8. Use the currently selected baseline as `parentCheckpointId`, if one exists and verifies as Available in the same game store and lineage.
9. Atomically update `environment.json` to select the new checkpoint.
10. Re-read and verify both the checkpoint and selected pointer before reporting success.

If checkpoint storage succeeds but the pointer update fails, the checkpoint remains immutable and available. The command reports its exact ID and states that it was not selected. A later `skyrim baseline use <checkpoint-id>` command may select it after independently revalidating its game, environment, adapter, lineage, and recipe metadata.

Creating the same canonical checkpoint at the same recorded second is idempotent. A later capture has a different `createdAt` and therefore a different checkpoint ID even when the adapter state is unchanged; status comparison still reports that the logical state matches.

## Checkpoint Content

The existing checkpoint schema is sufficient and remains at version 1. No migration is introduced.

The checkpoint fields use:

- `game: skyrim-se-ae`;
- the registered `environmentId` and `lineageId`;
- `adapterId: portable-mo2-skyrim`;
- the selected baseline as the optional parent;
- the exact recipe ID, revision, maturity, and selection identity;
- no invented artifact IDs; and
- the canonical MO2 adapter state already produced by the projection subsystem.

`artifactIds` contains only artifact identities that ModLab can prove are linked to this state. Because this increment does not fingerprint or link installed payloads, the initial live baseline will use an empty list. Empty does not mean reproducible; the evidence says `artifactCoverage: NotLinked` and `restorable: false`.

The checkpoint `evidence` object has an exact, versioned adapter-defined shape containing:

```text
schemaVersion: 1
captureKind: ObservedBaseline
qualification: Observed
promotionReady: false
restorable: false
gameDiscovery: canonical Skyrim discovery evidence
managerObservation:
  observationContext
  capabilities
  findings
  adapterStateSha256
recipeReview:
  recipeSourceSha256
  selected
  omitted
  identity
  readyForApproval
targetEnvironment:
  environmentSourceSha256
  environmentId
  dimensions
  liveDimensionFindings
foundationAssembly: NotVerified
validation:
  runtimeValidation: NotPerformed
  smokeTest: NotPerformed
coverage:
  profileState: Complete
  installedPayloadContent: NotInspected
  artifactCoverage: NotLinked
  assetConflicts: NotInspected
  pluginRecordConflicts: NotInspected
actionsPerformed: []
downloadsPerformed: []
installationActionsPerformed: []
programsLaunched: []
```

The serializer rejects a baseline evidence object that claims promotion readiness, restoration, runtime validation, payload inspection, conflict inspection, or any side effect in this increment.

## Status and Drift Comparison

`skyrim status` is read-only. It performs the same strict configuration, recipe, Skyrim, and MO2 observations as capture, verifies the selected checkpoint lockfile, then compares live evidence with the baseline.

The outcome is one of:

- `Matched`: all compared domains match;
- `Drifted`: complete current evidence differs from the baseline;
- `NoBaseline`: configuration is valid but no checkpoint is selected; or
- `Blocked`: required evidence is missing, unstable, redirected, malformed, contradictory, or untrusted.

Text and JSON output group comparisons into these domains:

1. **Game identity** — Steam app ID, manifest install directory, Skyrim executable version, size, and SHA-256, plus the observed top-level game data inventory already covered by Skyrim discovery.
2. **Manager identity and containment** — MO2 executable version, size, SHA-256, configured game path, and all five contained writable paths.
3. **Lab profile** — mod rows, enablement, priority, plug-in enablement/order, and fixed profile state-file identities.
4. **Play profile** — the same logical and fixed-file state for Play.
5. **Shared manager state** — installed mod folder names, observed top-level `meta.ini` identities, and top-level Overwrite entries.
6. **Recipe intent** — exact retained recipe bytes, revision, maturity, selection identity, selected components, and omitted components.
7. **Target environment** — exact retained target-environment bytes and dimensions, plus every currently observable live-dimension reconciliation.
8. **Coverage** — whether either observation claims a different or incomplete comparison boundary.

For every drifted domain, the report includes the changed field or stable identity and both checkpoint/current values where safe and reasonably small. Profile membership, enablement, and order changes reuse the existing normalized state and placement concepts. Long sequences use bounded first-divergence windows rather than dumping unbounded lists.

`Matched` requires every domain above. The comparison does not fall back to matching only `adapterStateSha256` when game, manager, recipe, or coverage evidence is unavailable.

The command returns exit code `0` for `Matched` and `3` for `Drifted`, `NoBaseline`, or `Blocked`. Argument and format errors retain argparse's exit code `2`. The JSON outcome distinguishes all four states.

## Side Effects and Output Contracts

Every JSON result contains explicit side-effect arrays. Read-only commands require all arrays to be empty.

`configure`, `baseline create`, and `baseline use` report only the ModLab files they actually created or atomically replaced. They also include empty arrays for downloads, installations, game changes, manager changes, and launched programs.

Text output ends with a precise scope statement:

- configuration: only the listed ModLab configuration, recipe, and target-environment files were written;
- baseline capture: only the listed ModLab checkpoint/configuration files were written; or
- status: nothing was written, launched, installed, repaired, restored, or promoted.

No command describes checkpoint creation as promotion.

## Safety and Failure Handling

The following conditions block configuration, capture, or matching as applicable:

- the Steam manifest or Skyrim executable identity is missing or ambiguous;
- the derived game path differs from the configured path;
- the portable MO2 root is outside the exact organized workspace location;
- configured MO2 writable paths escape their expected directories;
- either exact Lab or Play profile is missing, redirected, or incoherent;
- the MO2 authoritative read set changes during inspection;
- the MO2 or Skyrim executable has no byte identity;
- the retained recipe bytes do not match their recorded SHA-256;
- recipe ID, revision, maturity, selection, or review differs from configuration;
- target-environment ID, dimensions, bytes, or an observable live dimension differs from configuration;
- the selected parent or baseline checkpoint is missing, modified, cross-game, cross-environment, cross-adapter, or cross-lineage;
- a configuration or checkpoint contains duplicate JSON keys or unknown fields; or
- any target or staging path is redirected, unsafe, or outside the workspace.

All writes use same-volume staging files and `os.replace`. Staging files are removed after both success and handled failure. Existing unknown files are not deleted. Existing invalid configuration, recipe, or checkpoint files are never repaired or overwritten automatically.

The baseline pointer update uses compare-and-swap semantics: it verifies that the configuration bytes are unchanged from the version used to create the checkpoint. Concurrent or intervening configuration changes leave the new checkpoint unselected rather than overwriting newer intent.

Saves and co-saves are structurally excluded from discovery, projection, checkpoint evidence, drift comparison, configuration writes, and tests. No path containing a `saves` segment and no `.ess` or `.skse` file may enter the new serializers or write plans.

## Components and Boundaries

The implementation will add focused units rather than expanding the CLI or existing scanner into a monolith:

1. **Environment configuration model and serializer** define the strict logical configuration and canonical JSON.
2. **Environment store** owns atomic configuration and content-addressed recipe and target-environment retention.
3. **Baseline capture adapter** combines existing Skyrim discovery, MO2 projection, recipe review, and checkpoint storage without duplicating their validation logic.
4. **Checkpoint evidence serializer** defines and validates the fixed observed-baseline evidence contract.
5. **Drift comparator** compares a complete live observation with one valid baseline by domain.
6. **Skyrim workflow service** coordinates configure, capture, select, and status operations.
7. **CLI serialization** renders bounded text and strict JSON with accurate side effects.

The generic checkpoint store remains game-agnostic. Skyrim/MO2 semantics belong in the Skyrim workflow and MO2 adapter modules, not in the generic store.

## Testing

Implementation will be test-driven and cover:

- strict configuration and baseline-evidence round trips;
- exact raw-to-compatibility MO2 version derivation and rejection of ambiguous versions;
- rejection of missing, extra, duplicate, malformed, unsafe, redirected, or cross-linked fields;
- content-addressed recipe and target-environment retention and byte-identity verification;
- separation of target dimensions from live observed dimensions, including unverified SKSE state;
- idempotent initial configuration and explicit replacement;
- atomic-write cleanup and injected failures before and after `os.replace`;
- compare-and-swap refusal when configuration changes during capture;
- capture refusal for every incomplete MO2 readiness capability;
- capture refusal when Skyrim or MO2 identity is missing;
- checkpoint parent, lineage, environment, adapter, and recipe validation;
- no invented artifact IDs and fixed non-restorable/non-promotable evidence;
- Matched, Drifted, NoBaseline, and Blocked status outcomes;
- domain-specific game, manager, Lab, Play, shared-state, recipe, and coverage drift;
- bounded order-difference output;
- no writes from `status`;
- no writes before failed configuration or capture validation;
- explicit side-effect arrays and exit codes;
- exact save/co-save exclusion; and
- backward compatibility for all 185 existing tests and current CLI output.

Live verification will:

1. snapshot all authoritative Skyrim/MO2 evidence and relevant directory identities;
2. configure the real contained Skyrim environment using the exact retained recipe and target-environment sources;
3. create and verify its first observed baseline;
4. run `skyrim status` and require `Matched`;
5. prove that only the expected ModLab recipe, target-environment, registration, and checkpoint files changed;
6. prove that Skyrim, MO2, both profiles, installed mods, Overwrite, and save locations did not change; and
7. verify the exact published commit against the live environment again after integration.

Live drift mutation will not be performed against the real MO2 profiles. Drift cases will use exact temporary fixtures derived from the observed semantics.

Computer control is not required because this increment does not drive the MO2 interface or launch a program. It becomes required for the later verified MO2 bootstrap, tool-launch, and smoke-test milestones.

## Acceptance Criteria

The increment is complete when:

1. One command registers the exact live Skyrim/MO2 state, target-environment evidence, and recipe intent under the workspace without treating target-only dimensions as observed.
2. Repeating identical configuration is idempotent.
3. Changed configuration requires explicit replacement and never deletes prior checkpoints or recipe snapshots.
4. One command creates a verified immutable observed-baseline checkpoint and selects it atomically.
5. A stored checkpoint contains the canonical MO2 adapter state and complete fixed-shape observation evidence.
6. Baseline evidence always says Observed, not promotion-ready, not restorable, not runtime-tested, and not artifact-linked.
7. A parent baseline is accepted only from the same valid game, environment, adapter, lineage, and recipe intent.
8. One command reports Matched for the unchanged live state without requiring repeated paths.
9. Every in-scope changed domain produces Drifted with a bounded, useful explanation.
10. Missing, unstable, contradictory, redirected, malformed, or incomplete required evidence produces Blocked rather than Matched.
11. No status check writes any file.
12. Failed configuration or capture does not leave a selected partial result.
13. A pointer-update failure leaves an immutable, discoverable, unselected checkpoint and reports its ID.
14. All writes remain beneath the ModLab workspace.
15. Skyrim, Steam, MO2, mods, profiles, Overwrite, saves, and co-saves remain byte-for-byte unchanged during live verification.
16. Existing discovery, comparison, recipe, artifact, checkpoint, transaction, and workspace behavior remains backward compatible.
17. The complete automated suite passes.
18. Live capture and immediate status return a stable Matched result on the exact merged commit.
19. Local `main` and GitHub `origin/main` resolve to that verified commit.
20. Documentation states the exact capabilities and limitations without implying conflict resolution, restoration, promotion, or playability.

## Subsequent Milestones

This increment establishes the reference state required by later work. It does not collapse the roadmap into checkpointing.

The next independent Skyrim slices remain:

- verified MO2 bootstrap and profile configuration, tested through computer control;
- immutable side-by-side installed payload fingerprinting and archive linkage;
- guided Foundation assembly;
- conflict and compatibility diagnostics;
- contained generator execution with separate output mods;
- smoke-test evidence;
- transactional, save-preserving promotion and rollback; and
- expansion through separate OpenMW, Oblivion Classic, and Oblivion Remastered adapters.

Each later slice must preserve the observed-baseline truth boundary rather than silently upgrading an old checkpoint's qualification.
