# Verified MO2 Bootstrap and Configuration Design

**Date:** 2026-08-31

**Status:** Approved for implementation planning

**Scope:** Skyrim SE/AE on Steam and the contained portable Mod Organizer 2 instance

## Purpose

ModLab can already inspect the user's contained portable MO2 instance, compare its exact Lab and Play profiles, register a Skyrim environment, capture an immutable Observed baseline, and report drift. It cannot yet create that manager layout itself or prove which retained MO2 archive supplied the installation.

This increment adds a verified, staged, create-only bootstrap. It automates a clean MO2 setup and can safely adopt the existing compatible instance without rewriting it. It never repairs or upgrades a nonempty live manager in place.

The user experience is deliberately simple:

1. ask ModLab to prepare a setup plan from one retained MO2 archive;
2. review a short `Create`, `Adopt`, `Already managed`, or `Blocked` result;
3. explicitly apply the immutable plan;
4. receive a verified installation receipt or a precise refusal.

The design preserves the project's core rule: ModLab may automate evidence and reversible preparation, but it must not silently make ambiguous changes.

## Current evidence

The live machine establishes the first supported release and layout:

- the latest official stable MO2 release is `2.5.2`;
- retained archive ID: `archive-sha256:e6376efd87fd5ddd95aee959405e8f067afa526ea6c2c0c5aa03c5108bf4a815`;
- retained archive size: `149660212` bytes;
- recorded source: `https://github.com/ModOrganizer2/modorganizer/releases/download/v2.5.2/Mod.Organizer-2.5.2.7z`;
- installed `ModOrganizer.exe` version: `2.5.2.0`;
- installed executable SHA-256: `442b354a8f34754da0048654c44d27f51628feba54ce46c3187cf58d6c43e622`;
- archive extraction is supported by the Windows system `bsdtar` currently observed as `3.8.8`;
- the live manager uses the exact contained `workspace/tools/mo2/skyrim-se-ae` layout;
- `ModLab - Lab` and `ModLab - Play` both exist with profile-local saves disabled;
- the current selected Skyrim baseline is Matched.

The archive's source URL is provenance metadata, not a cryptographic signature. Authorization to bootstrap comes from an exact curated release descriptor and matching archive bytes, not from a filename or URL alone.

## User-facing commands

### Prepare a plan

```powershell
python -B -m modlab skyrim mo2 setup `
  --artifact archive-sha256:<hash> `
  --workspace .\workspace `
  --steam-root 'C:\Path\To\Steam'
```

`--steam-root` is optional when a valid registered Skyrim environment already supplies it. If both are present, they must resolve to the same Steam root and Skyrim installation. For a new workspace without `environment.json`, `--steam-root` is required.

The command verifies the retained artifact and current machine state, writes one immutable content-addressed plan under `workspace/runtime/jobs/mo2-bootstrap/plans/`, and changes no MO2, Skyrim, Steam, profile, mod, or save file.

It returns one outcome:

- **Create** — the exact target instance is absent or structurally empty and can be created;
- **Adopt** — a compatible, ready existing instance can be linked after deep package verification, without rewriting it;
- **Already managed** — a valid receipt already covers the same release and live state;
- **Blocked** — the plan cannot be applied safely, with exact reasons and suggested next action.

The result includes the plan ID, archive and release identities, resolved Skyrim and MO2 paths, intended writes, close-before-apply requirement, exclusions, Unknowns, and explicit empty arrays for anything it did not do.

### Apply a plan

```powershell
python -B -m modlab skyrim mo2 setup `
  --apply bootstrap-plan-sha256:<hash> `
  --workspace .\workspace
```

Apply accepts only an intact stored plan. It rechecks every precondition instead of trusting the earlier preview. A changed archive, game, target, release descriptor, extractor, environment, or selected baseline blocks before the first manager write. Relevant processes must be absent at apply; closing processes that the plan explicitly reported is the expected safe transition, not drift.

### Recover an interrupted create

```powershell
python -B -m modlab skyrim mo2 recover `
  bootstrap-job:<32-lowercase-hex> `
  --workspace .\workspace
```

Recovery resumes verification when the staged instance was already activated, or restores the exact prior absent/empty target when activation was incomplete. It never guesses which nonempty manager tree should win.

## Fixed paths

The Skyrim MO2 target is not user-selectable:

```text
workspace/tools/mo2/skyrim-se-ae/
  app/
  downloads/
  mods/
  profiles/
    ModLab - Lab/
    ModLab - Play/
  overwrite/
  webcache/
```

Plans, staging evidence, journals, and recovery state remain under:

```text
workspace/runtime/jobs/mo2-bootstrap/
```

Verified installation receipts remain under:

```text
workspace/games/skyrim-se-ae/tool-installations/mo2/
```

Temporary activation staging is a uniquely named sibling of the final Skyrim MO2 instance under `workspace/tools/mo2/`. This keeps the final directory rename on the same volume. It may exist only while a journal identifies it. Orphan staging directories block later setup until recovered; they are never silently removed.

All path comparisons use resolved, case-insensitive Windows identities. Existing symlinks, junctions, reparse points, alternate data streams, nonregular files, or paths escaping the resolved workspace block the operation.

## Supported release descriptor

The repository contains a strict curated descriptor for MO2 `2.5.2`. It records:

- descriptor schema and stable release ID;
- expected archive filename, SHA-256, and byte size;
- official release page and asset URL as provenance;
- supported game adapter and Windows platform;
- expected `ModOrganizer.exe` relative path, file version, SHA-256, and size;
- canonical archive-entry count and listing identity;
- the small set of package paths MO2 may legitimately mutate after first launch;
- required immutable package sentinels;
- minimum supported system extractor behavior.

Only exact descriptor/archive matches are supported. A newly released MO2 version requires a reviewed descriptor update; ModLab does not treat “newer” as automatically safe.

## Immutable bootstrap plan

The plan is canonical JSON with schema version `1`. Its identity is the SHA-256 of the canonical plan body and uses:

```text
bootstrap-plan-sha256:<64-lowercase-hex>
```

The plan records:

- workspace physical identity;
- Steam root and discovered Skyrim executable identity;
- optional registered environment identity and selected-baseline status;
- release descriptor identity;
- retained archive ID, size, SHA-256, metadata identity, and health;
- system extractor path, version, size, and SHA-256;
- exact final root plus the fixed staging parent and job-derived naming rule;
- current target classification and bounded inventory identity;
- running-process observations and the required process-absence predicate for apply;
- profile seed source identities;
- proposed manager, profile, receipt, journal, and recovery writes;
- archive and installed-content coverage limits;
- exclusions and Unknowns;
- outcome and blocking findings.

Paths are recorded as both workspace-relative logical paths and resolved physical identities where needed for containment. The exact staging root cannot exist until apply creates a job; it is derived solely from the stored plan ID and new `bootstrap-job:<32-lowercase-hex>` identity, then recorded in that job's journal before extraction. Volatile timestamps and the later job identity do not contribute to the plan ID. The stored plan file is itself verified before apply.

Repeating plan with unchanged inputs returns the same plan ID and does not replace its bytes. A conflicting file at that content-addressed path blocks.

## Target classification

### Create

Create is allowed only when the fixed instance root is absent or contains solely the exact empty directories created by `workspace init`. No unknown file or nonempty directory may be present. An empty prior root is preserved in the job's recovery area until activation verifies.

### Adopt

Adopt applies to an existing instance only when all of the following are true:

- the existing scanner and state projection report Ready;
- `ModOrganizer.exe` matches the curated release descriptor exactly;
- the game path and all writable manager paths match the fixed contained layout;
- both exact ModLab profiles exist;
- profile-local saves are false in both profiles;
- a registered environment, when present, is valid and not Drifted or Blocked;
- no conflicting bootstrap receipt exists;
- deep verification against a staged copy of the retained archive proves every nonmutable packaged file required by the descriptor;
- every allowed mutable difference and extra top-level runtime entry is recorded honestly in the receipt.

Adopt writes only ModLab-owned plan, staging, journal, and verified-receipt state. Temporary extracted package evidence is cleaned only after the journal proves it is redundant. Adopt does not rewrite, delete, rename, normalize, or timestamp any existing MO2 app, download, mod, profile, Overwrite, webcache, log, game, or save path.

### Already managed

Already managed requires a valid receipt whose archive, release, game, executable, configured paths, profiles, and covered installed-package identities still match. It is an idempotent no-op. Missing or modified receipt evidence never silently falls back to this result.

### Blocked

Any other nonempty target is Blocked. The command may recommend manual inspection, a separate future upgrade/repair workflow, or choosing a clean workspace, but it does not offer a force flag.

## Process safety

Planning reports relevant processes but remains read-only. Apply and recover refuse while either `ModOrganizer.exe` or `nxmhandler.exe` is running from the target instance.

Process identity is based on the resolved executable path, not only a process name. Failure to inspect process paths is Unknown and blocks. ModLab never terminates either process automatically.

The current live instance is therefore expected to plan as **Adopt, close required** while its two observed processes remain running. The user closes MO2 before apply or computer-controlled validation begins.

## Archive preflight and extraction

ModLab invokes the exact system extractor executable with an argument array, never through a shell and never using archive-provided command text.

Before extraction it:

1. re-verifies the vault artifact's metadata and bytes;
2. requires the exact curated release descriptor match;
3. obtains the archive entry list without extracting;
4. requires the canonical entry count and listing identity from the descriptor;
5. rejects absolute, drive-relative, parent-traversing, empty, control-character, duplicate case-insensitive, link, device, reparse, and alternate-stream entries;
6. verifies staging and target resolve beneath the expected workspace roots;
7. confirms sufficient free space using a conservative descriptor bound.

Extraction occurs only in a fresh job-owned staging directory. After extraction ModLab walks the whole staged tree without following links, rejects redirected or unexpected object types, hashes the exact file inventory, and validates the executable plus required package sentinels.

An extraction or verification failure leaves the final target untouched. The failed job and diagnostics remain inspectable; no partial stage is reused without recovery.

## Generated portable configuration

For Create, ModLab writes a minimal deterministic `ModOrganizer.ini` into the staged app. It contains only the stable values needed to bypass the first-instance wizard and locate the contained instance:

- `first_start=false`;
- `game_edition=Steam`;
- `gameName=Skyrim Special Edition`;
- the exact discovered `gamePath`;
- `selected_profile=ModLab - Lab`;
- `version=2.5.2`;
- the exact contained `base_directory`;
- `profile_archive_invalidation=true`;
- `profile_local_inis=true`;
- `profile_local_saves=false`.

Paths use the QSettings-compatible encoding already accepted by the strict MO2 reader. ModLab does not write Nexus credentials, API keys, browser state, custom executable lists, theme preferences, download history, or arbitrary user settings. MO2 may add its own defaults after first launch.

The four writable paths are derived from the fixed base directory rather than independently accepted from the user:

- `downloads`;
- `mods`;
- `profiles`;
- `overwrite`.

## Lab and Play profile seeds

Create produces both exact profile directories. Each begins with:

- `settings.ini` containing `LocalSaves=false`, `LocalSettings=true`, and `AutomaticArchiveInvalidation=true`;
- comment-only `modlist.txt`, `plugins.txt`, and `lockedorder.txt`;
- `loadorder.txt` containing the five fixed Skyrim masters followed by the exact valid plug-in sequence read from the fixed root `Skyrim.ccc`, when present;
- an empty `archives.txt`;
- `Skyrim.ini`, `SkyrimPrefs.ini`, and `SkyrimCustom.ini` copied only from the three exact game-scoped INI paths when present as regular, nonredirected files; a missing source becomes an explicit empty seed.

The five fixed masters are `Skyrim.esm`, `Update.esm`, `Dawnguard.esm`, `HearthFires.esm`, and `Dragonborn.esm`. Each must exist as an observed top-level `Data` plug-in before Create is eligible. `Skyrim.ccc` is parsed with the same strict plug-in-name rules used by the current MO2 adapter; duplicates, unsafe names, or missing listed files block. Its presence, bytes, size, SHA-256, and resulting ordered plug-in sequence are frozen in the plan and rechecked at apply.

ModLab never enumerates or copies the adjacent saves directory. The exact three INI source paths, presence, size, and SHA-256 values are frozen in the plan and rechecked at apply.

Lab and Play seeds are byte-identical at creation. Their selections and generated outputs diverge only through later explicit work. MO2 may populate implicit DLC and Creation Club entries on first launch; that later normalization is observed and checkpointed rather than guessed during bootstrap.

## Activation and recovery

Create uses a job journal with these durable states:

```text
Planned -> Staging -> Staged -> Applying -> Activated -> Verified
                                      \-> RecoveryRequired -> Recovered
```

Before the first final-target rename, the journal is durably `Applying`. The staged and final instance directories are siblings on the same volume.

Activation performs bounded directory renames:

1. preserve the exact prior absent/structurally-empty target state in the job recovery area;
2. rename the verified staged instance to the fixed final name;
3. record `Activated`;
4. run the existing MO2 scanner and projection against the final location;
5. require Ready, exact paths, exact profiles, local saves disabled, expected executable identity, and empty installed mods/Overwrite;
6. write and verify the receipt;
7. record `Verified`.

If activation fails before the target rename, the target remains unchanged. If it fails after preserving the empty target but before verification, recovery either finalizes an already-correct target or restores the recorded empty/absent state. Recovery refuses unexpected nonempty state and reports `RecoveryRequired` rather than deleting it.

No generic recursive delete is part of the public workflow. Job-owned temporary cleanup occurs only after its journal proves the exact directory was never activated or is a verified redundant stage.

## Installation receipt and linkage

A successful Create or Adopt writes an immutable content-addressed receipt with:

- `qualification=VerifiedBootstrap`;
- mode `Created` or `Adopted`;
- plan and job identities;
- release descriptor and archive identities;
- Skyrim executable identity;
- final MO2 executable identity;
- fixed manager and writable roots;
- Lab and Play profile seed or observed identities;
- verified immutable packaged-file inventory identity;
- explicitly excluded mutable package paths;
- recorded extra runtime entries for Adopt;
- extractor evidence;
- verification findings and coverage limits;
- `repairable=false` and `upgradeSupported=false` for this increment.

The receipt proves the stated package subset came from the exact retained archive and that the final contained configuration was Ready at receipt time. It does not prove every future mutable MO2 byte, installed mod payload, Nexus download, game runtime behavior, or Foundation Recipe component.

This is manager-artifact linkage only. It does not change the existing Skyrim baseline's `artifactCoverage=NotLinked`; full installed-mod payload/archive linkage remains the next independent milestone. Writing a receipt therefore does not invalidate the currently selected Observed baseline by itself.

## Interaction with existing workflows

- `workspace init` remains non-destructive and may create the empty target skeleton.
- a clean bootstrap can occur before `skyrim configure`, solving the current clean-install ordering problem;
- after Create, the user runs `skyrim configure`, then captures a new Observed baseline;
- Adopt requires existing scanner/projection readiness and respects any registered baseline drift result;
- `manager discover`, `manager compare`, `skyrim status`, artifact verification, checkpoints, and transaction inspection remain backward compatible;
- no current promotion or rollback command is exposed by this work;
- the current live instance is adopted, not rebuilt.

## Refusals and error reporting

Every refusal is fail-closed and names the first safe next action. Stable finding codes cover at least:

- unsupported or modified release descriptor;
- missing, modified, or unrecognized archive artifact;
- archive listing mismatch or unsafe entry;
- extractor missing, redirected, changed, or failed;
- insufficient disk space;
- Steam/Skyrim discovery mismatch;
- registered environment mismatch or drift;
- target outside the fixed layout;
- target redirected, nonempty unknown, or changed since plan;
- compatible existing app failing deep archive comparison;
- MO2 or NXM handler still running;
- profile seed source changed since plan;
- staging collision or orphan;
- invalid, modified, or incompatible plan/journal/receipt;
- interrupted activation requiring recovery;
- post-activation scanner/projection failure.

Human-readable output leads with `Created`, `Adopted`, `Already managed`, `Blocked`, or `Recovery required`. It states what changed and what did not. JSON exposes the complete evidence without stronger claims.

Exit codes remain:

- `0` — plan prepared, Create/Adopt verified, Already managed, or recovery completed;
- `2` — invalid command, identifier, schema, or unsupported release request;
- `3` — safe refusal, changed precondition, blocked target, failed verification, or recovery required.

No `--force`, automatic process termination, automatic repair, or implicit upgrade escape hatch exists.

## Explicit non-goals

This increment does not:

- download MO2 or any mod;
- trust a Nexus/GitHub URL as proof of bytes;
- repair, overwrite, merge, or upgrade a nonempty MO2 app;
- install, enable, disable, reorder, clean, or patch a mod;
- authenticate to Nexus;
- register `nxm://` links globally;
- launch MO2, Skyrim, SKSE, LOOT, xEdit, or a generator from the public command;
- inspect or copy saves/co-saves;
- change Steam or the Skyrim game directory;
- solve asset or plug-in conflicts;
- claim runtime stability;
- promote Lab to Play;
- implement Morrowind or either Oblivion adapter.

Contained external-tool execution, guided mod installation, full payload/archive linkage, Foundation assembly, smoke tests, conflict resolution, promotion, and the other game adapters remain separate reviewed increments.

## Test strategy

Development is test-driven. Unit and integration coverage includes:

1. strict release descriptor, plan, journal, and receipt serialization;
2. stable canonical identities and duplicate-key rejection;
3. Steam root fallback and registered-environment agreement;
4. Create, Adopt, Already managed, and every Blocked classification;
5. structural-empty target recognition without deleting unknown files;
6. exact archive/descriptor matching and vault re-verification;
7. unsafe archive names, case collisions, link/device entries, listing drift, extraction failure, and post-extraction redirects;
8. extractor argument-array construction without a shell;
9. staged executable and package-sentinel verification;
10. deterministic minimal MO2 configuration, authoritative primary load order, and both profile seeds;
11. exact INI-only source reads with saves structurally unreachable;
12. process-path detection and Unknown inspection failure;
13. changed archive, game, target, extractor, profile seed, environment, or baseline between plan and apply;
14. idempotent plan, apply, Adopt, and Already managed behavior;
15. interruption before staging, after staging, during target preservation, after activation, and before receipt verification;
16. recovery finalization, exact empty-state restoration, and refusal on unexpected nonempty state;
17. scanner/projection verification after Create and deep package comparison for Adopt;
18. exact action arrays and honest coverage language;
19. backward compatibility of all existing commands and tests.

Tests use temporary workspaces and a fake extractor for fault injection. A Windows integration test uses the retained official archive and real `bsdtar` in a disposable workspace under ModLab.

## Live computer validation

Computer control is required only after implementation tests pass. It is used to validate MO2's real interface, not as the production configuration engine.

The live procedure is:

1. record the current repository, workspace, Skyrim, MO2, selected baseline, and exact save/co-save identities in memory;
2. have the user close the currently running MO2 and NXM handler; ModLab does not terminate them;
3. plan and apply **Adopt** to the existing live instance, verifying that only ModLab plan/journal/receipt files changed;
4. run **Create** against a disposable nested validation workspace that references the real discovered Skyrim installation;
5. launch only that disposable contained MO2 through computer control;
6. verify it opens directly for Skyrim without the instance wizard, selects `ModLab - Lab`, exposes both profiles, uses all contained writable paths, and keeps profile-local saves disabled;
7. close the disposable MO2 and re-run scanner/projection checks;
8. compare before/after identities to prove the live MO2 app, downloads, mods, profiles, Overwrite, Skyrim directory, and saves/co-saves were unchanged by Adopt and disposable validation;
9. retain concise validation evidence under `workspace/games/skyrim-se-ae/logs/` and clean only journal-proven disposable scratch state.

If the generated configuration does not survive real MO2 startup, implementation stops and the design is revised. The test does not normalize the user's live installation to make the test pass.

## Acceptance criteria

The increment is complete only when:

1. a supported retained MO2 archive produces a stable immutable setup plan;
2. a clean workspace is staged, activated, and projected Ready at the exact fixed paths;
3. the real existing compatible instance is deeply verified and adopted without any MO2 byte changing;
4. an unknown nonempty instance is always blocked;
5. apply refuses while the target MO2/NXM handler is running or process inspection is Unknown;
6. interrupted Create is recoverable without guessing or deleting unknown state;
7. a verified receipt links the exact retained archive to the stated installed package coverage;
8. both profiles exist, begin equal, use local INIs, and keep saves outside MO2;
9. every operation reports exact writes and narrow evidence claims;
10. all automated tests pass;
11. real computer validation proves generated configuration works in MO2 and the user's live game, manager data, mods, and saves remain unchanged;
12. documentation explains the two-command flow in plain language;
13. verified work is committed and pushed to the public ModLab repository.
