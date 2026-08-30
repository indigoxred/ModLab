# MO2 State Projection and Lab-vs-Play Comparison Design

**Date:** 2026-08-31

**Status:** Approved in chat; awaiting written-spec review

**Scope:** Skyrim SE/AE portable MO2 adapter only

## Purpose

ModLab needs a trustworthy answer to a simple question: **what is different in the experimental Lab profile compared with the known Play profile?**

This subsystem will inspect the existing portable MO2 instance, convert the relevant evidence into a deterministic state projection, and explain the Lab-vs-Play differences. It is a read-only foundation for later checkpoint drift detection and promotion review. It does not install mods, edit profiles, resolve conflicts, launch programs, or claim that a setup works in-game.

## User Experience

The command will be:

```text
python -B -m modlab manager compare mo2 \
  --root <portable-mo2-app-folder> \
  --game-root <skyrim-folder> \
  --workspace <modlab-workspace> \
  --format text|json
```

Text output will lead with one of two outcomes:

- **Ready:** both profiles were safely and completely compared.
- **Blocked:** ModLab could not obtain sufficiently complete, trustworthy evidence and will not guess.

When ready, the report will show:

- mods enabled only in Lab or only in Play;
- mods whose enabled state differs;
- meaningful left-pane mod-order changes;
- plugins enabled only in Lab or only in Play;
- meaningful plugin/load-order changes;
- profile configuration files that were added, removed, or changed;
- whether MO2's shared `overwrite` area is non-empty;
- whether installed mod folders are shared between Lab and Play without full content fingerprints.

An empty setup will produce a clear “no differences” result, not an error.

## Architecture

The existing MO2 scanner remains the only code that reads the live filesystem. The new subsystem consumes its validated `Mo2InspectionReport`; comparison code will not independently walk arbitrary paths.

Three focused units will be added under `modlab/adapters/mo2/`:

1. **Projection** converts a complete inspection report into a stable, versioned `Mo2StateProjection`.
2. **Comparison** compares the exact `ModLab - Lab` and `ModLab - Play` profile projections and produces structured differences and findings.
3. **Comparison serialization** strictly validates and canonically serializes the projection and comparison report.

The CLI will orchestrate scanner → projection/comparison → text or JSON output. Existing discovery behavior remains unchanged.

## Stable State Projection

The projection will contain only observed state, with no timestamps or generated identifiers. It will include:

- projection schema version and adapter ID;
- MO2 executable identity: SHA-256, size, and observed file version;
- resolved MO2 and game roots;
- normalized configured MO2 path evidence;
- active profile as contextual safety evidence;
- exact Lab and Play profile states;
- installed top-level mod-folder names and observed `meta.ini` identities;
- top-level overwrite entries.

Each profile state will include:

- exact profile name and local-save setting;
- ordered mod entries with marker and enabled state;
- ordered plugin entries with enabled state;
- explicit load-order sequence;
- known profile-state files with profile-relative filename, SHA-256, and size.

Canonical serialization will use fixed field sets, explicit schema version `1`, stable ordering, and the repository's existing strict-validation style. Names will retain their observed spelling for display while uniqueness and matching remain case-insensitive, matching Windows and MO2 behavior.

The same projection can later be embedded into a checkpoint's `adapterState` without redesigning the live-state model. This feature will not create persistent checkpoints yet.

## Difference Semantics

The comparison direction is **Play → Lab**: the report explains what the experimental Lab profile changes relative to Play.

### Mods

- Membership differences identify entries present in only one profile.
- Enablement differences identify common entries whose enabled states differ.
- Order comparison uses the common enabled entries only. Each profile's sequence is filtered to that shared set before comparison, so merely adding a Lab-only mod does not falsely report every later mod as reordered.
- When relative order differs, the report returns the two filtered sequences rather than attempting to guess which single mod “moved.”

### Plugins and load order

- Plugin membership and enablement use the same rules as mods.
- Plugin order and `loadorder.txt` are compared using shared enabled entries, filtered in each profile, so additions do not create noisy index-shift reports.
- ModLab reports the observed ordering difference; it does not declare an order correct or run LOOT.

### Profile configuration

Known state files are compared by filename relative to the profile root, never by the profile-specific absolute path. Files are classified as Lab-only, Play-only, or content-changed using SHA-256 and size.

The semantic list files `modlist.txt`, `plugins.txt`, and `loadorder.txt` are already explained by their dedicated sections. They remain in the projection for exact future drift detection but will be excluded from the generic configuration-change summary to avoid duplicate noise.

### Shared-content and overwrite risks

MO2's installed `mods` directory and `overwrite` directory are shared by both profiles:

- A non-empty overwrite area produces a warning and lists its observed top-level entries.
- If installed mod folders exist, the report warns that their contents affect both profiles and are not yet fully fingerprinted. Only folder names and readable `meta.ini` files are currently identified.
- If no installed mod folders exist, the report states that no current shared mod content was observed.

This warning is intentional: profile isolation covers enablement and ordering, not independent copies of installed mod files.

## Safety and Blocking Rules

The command is ready only when all of the following are true:

- the existing scanner produces no `Blocked` or `Unknown` finding;
- its action, download, installation, and program-launch collections are explicitly empty;
- the exact Lab and Play profiles are present once each;
- each profile contains readable `modlist.txt`, `plugins.txt`, `loadorder.txt`, and `settings.ini` evidence;
- paths and entries have already passed the scanner's containment and ambiguity checks.

If any rule fails, the comparison is **Blocked**, structured differences are empty, the scanner findings are preserved, and the CLI exits with status `3`. Malformed arguments or internally invalid serialized data remain usage/data errors and exit with status `2`.

Warnings such as a non-empty overwrite area or shared installed content do not prevent a truthful comparison. A ready comparison exits with status `0` even when differences or warnings exist, because differences are the expected result of using Lab.

The comparison result and CLI JSON envelope will preserve explicit empty arrays for:

- actions performed;
- downloads performed;
- installation actions performed;
- programs launched.

No output may say that conflicts were resolved, that incompatible mods were made compatible, or that the game is stable. Those claims require later conflict analysis and live-game validation.

## Output Contract

The structured result will contain:

- schema version, adapter ID, readiness, and comparison direction;
- the normalized state projection when evidence is complete;
- categorized mod, plugin, load-order, and configuration differences;
- overwrite and shared-content observations;
- ordered findings with stable machine-readable codes;
- explicit empty side-effect arrays.

All existing scanner findings will be retained, in their observed order, before comparison-specific findings are appended. This keeps the report auditable without requiring a second discovery run.

Blocked results will use `null` for the unavailable projection and difference payload rather than manufacturing partial state. Serializers will reject unknown fields, duplicate case-insensitive identities, unsafe paths/names, inconsistent readiness, non-empty side-effect arrays, and impossible references.

Human-readable output will be concise and will always finish with an explicit statement that nothing was launched, changed, installed, repaired, promoted, or downloaded.

## Testing

Implementation will follow test-driven development and cover:

- deterministic projection from complete scanner evidence;
- strict projection/comparison serialization round trips and rejection cases;
- identical empty Lab and Play profiles;
- Lab-only and Play-only mods/plugins;
- enablement changes;
- relative-order changes without false positives from additions;
- profile file add/remove/change classification;
- non-empty overwrite warnings;
- shared installed-content warnings;
- blocking on every incomplete or unsafe evidence category;
- CLI text, JSON, and exit-code contracts;
- preservation of all explicit empty side-effect arrays;
- regression coverage for existing `manager discover mo2` behavior.

After unit and full-suite verification, the command will be run with Python bytecode writing disabled against the real portable MO2 installation at:

```text
C:\Users\red\Desktop\Modlab\workspace\tools\mo2\skyrim-se-ae\app
```

using Skyrim at:

```text
C:\Users\red\Desktop\Steam\steamapps\common\Skyrim Special Edition
```

The live run must report a ready, read-only comparison and leave the repository and MO2 state unchanged.

## Non-Goals

This increment will not:

- create or restore persistent checkpoints;
- copy changes from Lab to Play;
- edit MO2 profiles or INI files;
- download, install, remove, enable, disable, or reorder mods;
- run LOOT, xEdit, SKSE, the game, or any other program;
- inspect loose-file or archive conflicts inside installed mods;
- generate compatibility patches;
- decide that two mods are compatible;
- support Morrowind, Oblivion Classic, or Oblivion Remastered yet.

Those capabilities can build on this projection after the read-only comparison is proven against the real setup.
