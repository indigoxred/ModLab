# MO2 State Projection and Lab-vs-Play Comparison Design

**Date:** 2026-08-31

**Status:** Approved for implementation

**Scope:** Skyrim SE/AE portable MO2 adapter only

## Purpose

ModLab needs a trustworthy answer to a simple question: **what is different in the experimental Lab profile compared with the known Play profile?**

This subsystem will inspect the existing portable MO2 instance, convert the relevant evidence into a deterministic logical state, and explain the Lab-vs-Play differences. It is a read-only foundation for later checkpoint drift detection and promotion review. It does not install mods, edit profiles, resolve conflicts, launch programs, or claim that a setup works in-game.

## Verified MO2 Semantics

The design is based on the current MO2 implementation and the live portable MO2 2.5.2 installation, rather than assuming that MO2's files are interchangeable:

- `modlist.txt` is stored in reverse priority order. ModLab reverses the parsed row sequence before assigning zero-based MO2 left-pane positions and classifying runtime versus organisational rows.
- Managed mods, foreign Creation/DLC rows, and separator rows are classified separately. Managed and foreign rows can affect the runtime; names ending in MO2's `_separator` suffix are organisational only.
- For Skyrim SE/AE, `loadorder.txt` is the ordered visible plug-in inventory.
- `plugins.txt` records every non-primary plug-in's enabled state in load order: `*` means enabled and an unmarked entry means disabled. Skyrim's primary plug-ins are omitted.
- The primary plug-in set is the five core masters followed by the entries observed in `Skyrim.ccc`. Its observed spelling is retained and identities are matched case-insensitively.
- Current MO2 writes `plugins.txt` with the Windows system encoding. ModLab will use the same decoding policy and will block instead of silently substituting text on a decoding failure.

The two fresh live profiles, `ModLab - Lab` and `ModLab - Play`, both contain readable `modlist.txt`, `plugins.txt`, `loadorder.txt`, and `settings.ini`. Their primary-only state is valid: the ten primary plug-ins appear in `loadorder.txt`, while `plugins.txt` contains only its header. This observed case is a required regression fixture. A missing required file will not be inferred to mean an empty default.

## User Experience

The command will be:

```text
python -B -m modlab manager compare mo2 \
  --root <portable-mo2-app-folder> \
  --game-root <skyrim-folder> \
  --workspace <modlab-workspace> \
  --format text|json
```

Text output leads with one outcome:

- **Ready:** all required MO2 profile-state evidence was compared.
- **Blocked:** ModLab could not obtain sufficiently complete, coherent, and stable evidence and will not guess.

When ready, the report shows:

- mod and plug-in membership or enablement changes, including their meaningful placement;
- meaningful relative-order changes without index-shift noise;
- profile configuration files that were added, removed, or changed;
- whether MO2's shared `overwrite` area is non-empty;
- whether installed mod folders are shared by Lab and Play without full payload fingerprints; and
- the exact limits of what this command inspected.

An empty setup produces a clear in-scope no-differences result, not an error.

## Architecture

The existing MO2 scanner remains the only component that reads the live filesystem. It will gain the minimum additional evidence needed to prove a stable, coherent inspection. Projection and comparison code consume its validated `Mo2InspectionReport`; they do not independently walk arbitrary paths.

Three focused units will be added under `modlab/adapters/mo2/`:

1. **Projection** converts a complete inspection report into a versioned observation context and canonical adapter state.
2. **Comparison** compares the exact `ModLab - Lab` and `ModLab - Play` profile states and produces structured differences and findings.
3. **Comparison serialization** strictly validates and canonically serializes the projection and comparison report.

The CLI orchestrates scanner → projection/comparison → text or JSON output. Existing `manager discover mo2` output and serialization remain backward compatible.

## Coherent Read Set

Ready means the scanner observed one best-effort stable point in time. It does not claim an atomic filesystem lock.

The scanner captures one deterministic authoritative read set:

- `ModOrganizer.ini`;
- the MO2 executable identity when an executable is present;
- Skyrim's `Skyrim.ccc`;
- each target profile's known-file directory listing and the bytes of every observed known state file;
- the top-level `mods` listing and every observed `meta.ini` used for identity;
- the top-level `overwrite` listing.

For directory identities, the scanner records a sorted sequence of entry name, entry type, and redirection status. It hashes the bytes of small state files rather than relying only on size or modification time.

The scanner parses only the first captured bytes and listings. After the complete inspection, it captures the exact same read set again and compares identities. A file or directory added, removed, redirected, replaced, or changed during inspection blocks the result with `mo2-state-changed-during-inspection`. Projection and differences are then `null`; partial comparisons are never returned.

There is no automatic retry in this increment. A retry could conceal an actively changing MO2 session and would complicate deterministic tests. The user can rerun the command after MO2 becomes idle.

## Observation Context and Canonical Adapter State

The complete report deliberately separates where an observation occurred from the logical state that was observed.

`observationContext` contains:

- resolved MO2, game, profiles, mods, and overwrite roots;
- the active MO2 profile;
- MO2 executable SHA-256, size, and observed file version;
- normalized configured-path evidence;
- read-set stability evidence.

`adapterState` contains:

- schema version and adapter ID;
- the exact Lab and Play logical profile states;
- installed top-level mod inventory and observed `meta.ini` identities; and
- top-level overwrite inventory.

Each profile state contains:

- exact profile name and local-save/local-settings values;
- ordered mod entries in MO2 priority order, including classification, marker, enabled state, and zero-based runtime priority where applicable;
- ordered plug-in entries, including primary/non-primary classification, enabled state, and zero-based load-order position;
- the reconciled effective enabled load order; and
- known profile-state files by profile-relative filename, SHA-256, and size.

Canonical serialization uses fixed field sets, explicit schema version `1`, stable ordering, and the repository's strict-validation style. Observed spelling is retained for display; uniqueness and matching use case-insensitive Windows/MO2 identity rules.

`adapterStateSha256` is the SHA-256 of canonical `adapterState` JSON only. Physical roots, active profile, executable identity, findings, coverage claims, and observation timing do not affect it. Therefore selecting a different active profile or relocating an otherwise identical portable installation changes observation context but not logical state or Lab-vs-Play differences.

This canonical state can later become a checkpoint's `adapterState` without redesign. This increment does not persist a checkpoint.

## MO2 State Reconciliation

Before projection is Ready, each profile's plug-in evidence must obey the verified Skyrim SE/AE policy:

1. `loadorder.txt` contains a case-insensitively unique ordered inventory.
2. `plugins.txt` contains a case-insensitively unique state row for every non-primary entry in `loadorder.txt`, in that same relative order.
3. Every `plugins.txt` entry exists in `loadorder.txt` and is non-primary.
4. Every expected primary entry appears exactly once in `loadorder.txt` and does not appear in `plugins.txt`; that load-order-only presence is expected, not inconsistent.
5. The expected primary sequence is the prefix of `loadorder.txt`, in official relative order, before all non-primary entries.
6. A primary entry is effectively enabled by its presence. A non-primary entry is effectively enabled only when its `plugins.txt` row is starred.

An unexplained non-primary load-order-only entry, a plug-in-state-only entry, a duplicate case-insensitive identity, an unexpected primary state row, or contradictory relative order blocks with `mo2-plugin-order-evidence-inconsistent`. ModLab retains the raw scanner evidence for diagnosis but does not manufacture an effective order.

This intentionally differs from a blanket rule that every `loadorder.txt` entry must appear in `plugins.txt`; that rule would reject the valid primary-only live state.

## Difference Semantics

The direction is **Play → Lab**: the report explains what the experimental Lab profile changes relative to Play.

### Placement model

Every mod or plug-in membership/enablement change records each available profile position and its nearest shared enabled runtime predecessor and successor. A mod position contains its full left-pane list index plus its filtered runtime priority, which is `null` for an organisational row; a plug-in position contains its load-order index:

```text
name: X
change: added-in-lab
labIndex: 42
previousSharedAnchor: A
nextSharedAnchor: B
```

The anchor set contains entries that are present, runtime-classified, and enabled in both profiles. A missing predecessor or successor is represented as `null`. Organisational separator rows retain their observed placement for audit but do not become runtime anchors and do not by themselves justify a “meaningful runtime order changed” claim.

### Mods

- Membership differences identify entries present in only one profile and report their placement.
- Enablement differences identify common entries whose enabled state differs and report placement in both profiles.
- Order comparison uses runtime-affecting, enabled entries common to both profiles. Filtering both sequences to this shared set prevents an insertion from falsely reporting every later entry as reordered.
- When relative order differs, JSON returns both complete filtered sequences. ModLab reports observed order, not which individual mod “should” move.

### Plug-ins and load order

- Membership and enablement differences use the same placement model after plug-in evidence has been reconciled.
- Relative-order comparison uses shared, effectively enabled plug-ins only, so additions do not create index-shift noise.
- JSON returns the complete filtered load-order sequences when they differ.
- ModLab does not declare either order correct or run LOOT.

### Profile configuration

Known state files are compared by profile-relative filename, never by profile-specific absolute path. Files are classified as Lab-only, Play-only, or content-changed using SHA-256 and size.

The semantic list files `modlist.txt`, `plugins.txt`, and `loadorder.txt` are already explained by their dedicated differences. They remain in adapter state for exact future drift detection but are excluded from the generic configuration summary to avoid duplicate noise.

### Shared content and overwrite

MO2's installed `mods` and `overwrite` directories are shared by both profiles:

- A non-empty overwrite area produces a warning and lists its top-level entries.
- Installed mod folders produce a warning that their contents affect both profiles and were not fingerprinted. Only top-level folder names and readable `meta.ini` identity fields are observed.
- A missing `meta.ini` is valid and means no metadata identity was available. If an observed `meta.ini` exists but is unreadable, undecodable, ambiguous, or redirected, installed-inventory evidence is incomplete and comparison blocks.
- If no installed mod folders exist, the report states that no current shared mod content was observed.

## Readiness Capabilities

Comparison readiness is not derived from “no Unknown findings.” It is derived from explicit capabilities needed for this projection. Every capability must be proven `complete`:

- MO2 configuration was parsed completely;
- the requested Skyrim installation was matched;
- configured paths and all observed entries are contained and unambiguous;
- the exact Lab and Play profiles exist once each;
- required profile files and known-file listings are complete;
- the Skyrim primary plug-in policy is established;
- each profile's plug-in/load-order evidence is coherent;
- installed mod inventory evidence is complete;
- overwrite inventory evidence is complete;
- the authoritative read set remained stable; and
- actions, downloads, installations, and program launches are explicitly empty.

A scanner `Unknown` that affects one of these capabilities blocks projection. A contextual `Unknown`, such as an unavailable executable version string when executable identity and all profile evidence remain trustworthy, is preserved in the report but does not block comparison.

If a required capability is incomplete, readiness is **Blocked**, `adapterState`, `adapterStateSha256`, and differences are `null`, and the CLI exits with status `3`. Malformed arguments or internally invalid serialized data remain usage/data errors and exit with status `2`.

Warnings such as non-empty overwrite, shared installed content, or a non-blocking contextual Unknown do not prevent a truthful comparison. Ready exits with status `0` even when differences or warnings exist, because differences are expected in Lab.

## Coverage and Claims

Every result includes explicit coverage:

```json
{
  "coverage": {
    "profileState": "complete",
    "installedPayloadContent": "not-inspected",
    "assetConflicts": "not-inspected",
    "pluginRecordConflicts": "not-inspected",
    "runtimeValidation": "not-performed"
  }
}
```

For a blocked result, `profileState` is `incomplete`; the other limits remain unchanged.

If no projected difference is found while installed mod folders exist, text says:

```text
No in-scope profile-state differences were found.
Installed mod payload contents were not fingerprinted by this command.
Lab and Play may still be affected by changes to their shared mod directories.
```

No output may say that conflicts were resolved, incompatible mods were made compatible, or the game is stable. Those claims require later conflict analysis and live-game validation.

## Output Contract

The structured result contains:

- schema version, adapter ID, readiness, and comparison direction;
- `observationContext`;
- `adapterState` and `adapterStateSha256` when Ready;
- categorized mod, plug-in, load-order, and configuration differences when Ready;
- overwrite and shared-content observations;
- coverage;
- ordered findings with stable machine-readable codes; and
- explicit empty `actionsPerformed`, `downloadsPerformed`, `installationActionsPerformed`, and `programsLaunched` arrays.

All scanner findings remain in observed order before comparison-specific findings are appended. Blocked results use `null` for unavailable state, hash, and difference payloads instead of manufacturing partial state. Serializers reject unknown fields, unsafe names or paths, duplicate case-insensitive identities, inconsistent readiness/capabilities, invalid references, incorrect canonical hashes, and non-empty side-effect arrays.

For an order difference, human-readable output shows the number of differing-position entries, the first divergence, and at most three entries before and after it. It directs the user to JSON for the complete filtered sequences. Text always ends with an explicit statement that nothing was launched, changed, installed, repaired, promoted, or downloaded.

## Read-Only Workspace Contract

This command performs no writes anywhere. `--workspace` supplies the containment boundary used to validate the portable MO2 layout; it is not an output destination. The command creates no cache, log, result, checkpoint, temporary, bytecode, MO2, game, profile, or mod files. Development and live validation invoke Python with `-B` and verify relevant state before and after execution.

Path redaction may be added later for shareable support reports. It is not part of this increment.

## Testing

Implementation will follow test-driven development and cover:

- deterministic projection and strict serialization round trips/rejections;
- identical empty profiles and the verified primary-only live-state fixture;
- reversed `modlist.txt` conversion to MO2 priority and separator classification;
- Lab-only/Play-only insertion and removal placement for mods and plug-ins;
- enable/disable placement for mods and plug-ins;
- relative-order changes without false positives from additions;
- full JSON order sequences and bounded text windows;
- valid and invalid `plugins.txt`/`loadorder.txt` reconciliation, including duplicate identities and contradictory order;
- stable inspection and mutation of a profile file, installed-mod listing, or overwrite listing between passes;
- identical adapter state across active-profile changes and relocated fixtures;
- blocking and non-blocking Unknown findings based on required capabilities;
- missing versus invalid installed-mod metadata;
- profile configuration add/remove/change classification;
- non-empty overwrite and shared-content warnings;
- coverage wording, CLI text/JSON, and exit-code contracts;
- preservation of all explicit empty side-effect arrays; and
- regression coverage for existing `manager discover mo2` behavior and JSON schema.

After unit and full-suite verification, the command will run with bytecode writing disabled against:

```text
C:\Users\red\Desktop\Modlab\workspace\tools\mo2\skyrim-se-ae\app
```

using Skyrim at:

```text
C:\Users\red\Desktop\Steam\steamapps\common\Skyrim Special Edition
```

The live test records hashes/listings before and after, must return a Ready read-only comparison, and must leave the repository, workspace, MO2, profiles, mods, overwrite, and game state unchanged.

## Non-Goals

This increment will not:

- create or restore persistent checkpoints;
- copy changes from Lab to Play;
- edit MO2 profiles or INI files;
- download, install, remove, enable, disable, or reorder mods;
- run LOOT, xEdit, SKSE, the game, or any other program;
- hash installed mod payloads or inspect loose-file/archive conflicts;
- inspect plug-in records or generate compatibility patches;
- decide that two mods are compatible;
- validate the game at runtime; or
- support Morrowind, Oblivion Classic, or Oblivion Remastered yet.

Those later capabilities can build on this projection after the bounded read-only comparison is proven against the real setup.
