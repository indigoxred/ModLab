# Round 8 Disposable Profile Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to execute this bounded correction inline, with independent review before live testing.

**Goal:** Allow normal MO2 saves in a disposable active profile while preserving the original profiles and game.

**Architecture:** Keep the bootstrap manager and original profiles at Medium integrity. Copy the Lab seed once to `manager/test-profiles/ModLab - Lab`; select it using MO2's supported `profiles_directory` setting. Reuse existing continuous original-root watching, bounded runtime inventories, durable transition chain, and protected evidence captures.

**Tech Stack:** Existing Python gate, Windows integrity/watch helpers, strict MO2 INI reader.

**Spec:** User approval in Modlab V2 Astra: “Yes—use the disposable copy (Recommended)”, 2026-09-05. This amends the active-profile zero-write condition in the 2026-09-02 handshake plan for fresh Round 8 attempts only.

## Constraints and resulting claim

- Original Lab/Play profiles, mods, downloads, overwrite and game remain under the existing zero-event and unchanged-state checks.
- Medium layout/bootstrap/manager ancestry protects original profile directories from the Low MO2 process. Only App, Environment, logs/webcache/cache and test-profiles receive Low labels.
- Initial copy must have equal contents, distinct native objects and admitted destination paths. Its protected preparation baseline proves this relationship.
- Runtime allowances add only the selected disposable Lab subtree and exact `App/ModOrganizer.ini`. Retain each completed phase's INI bytes and provenance in the vault, match them to the App snapshot, and check all configured path boundaries.
- Writable state is recorded by complete before/after inventories; this does not claim a complete log of transient writes inside disposable storage. Continuous zero-event proof applies to the eight original/external watch roots.
- Current state must equal the latest durable transition. Do not restore or rebaseline unexplained drift. Old failed attempts remain failed and untouched; new source and fresh preparation are mandatory.
- No general workspace/scanner/watcher redesign, game launch, push or merge.

## Task 1: Implement and verify the corrected boundary

**Files:** `tools/mo2_round8_gate.py`, `tests/test_mo2_round8_gate.py`, `docs/validation/mo2-round8-preparation-evidence.md`.

- [x] Add failing tests for distinct copy, Low target separation, accepted disposable saves, rejected original writes, and INI configuration escape.
- [ ] Run those focused tests; confirm existing code refuses expected saves or lacks required separation.
- [ ] Implement `_disposable_profile_ini`, `_configure_disposable_profile`, `_disposable_low_roots`, and `_validate_runtime_ini`; bind the copy to prepared Manager inventory and the exact initial INI capture.
- [ ] Replace whole-layout Low labelling with exact Low target receipts and current checks of protected Medium ancestors.
- [ ] Add post-phase protected INI capture and provenance validation against the recorded App inventory. Preserve exact prepared INI validation and use semantic validation plus durable state equality for current use.
- [ ] Verify focused tests including altered copy, wrong INI bytes/identity, changed original, root substitution, state drift and historical reconstruction. Consolidate JPEG and failed-cleanup regressions.
- [ ] Request independent review of the complete diff, fix findings, commit, then run one complete gate on that clean source.
- [ ] Use a fresh preparation for ten real MO2 phases, retain actual UI evidence, and report the observed outcome. Unattended MO2 UI tests and retries are explicitly authorized by the user; no further readiness question is needed for that scope.

MO2 source for the profile override: https://github.com/ModOrganizer2/modorganizer/blob/v2.5.2/src/settings.cpp#L1575-L1578


## Task 2: Record ordinary startup output without changing support criteria

The first real Control inventory exposed stock plugin caches, App log creation/rotation, and Windows cache fallback paths. The 2026-09-02 plan's Task 0 explicitly requires retaining created cache entries. This correction restores that measurement contract and leaves `_candidate_passes` unchanged.

**Files:** `tools/mo2_round8_gate.py`, `modlab/adapters/mo2/bridge_runtime_capability.py`, `tests/test_mo2_round8_gate.py`, and the validation evidence document.

- [x] Reproduce refusal of stock caches, new App logs, and missing Windows shared-data variables.
- [x] Add bounded source-corresponding App/plugins cache observation rules to snapshots, deltas and output validation; retain every observed entry. Keep installation source-only and the original frozen Control baseline.
- [x] Add a complete matrix regression proving stock caches are valid evidence while candidate cache drift produces NotSupported.
- [x] Unify allowed App log creation/rotation, record the exact nxmhandler log, seed the empty crashDumps directory, and bind PROGRAMDATA/ALLUSERSPROFILE/SystemDrive explicitly.
- [x] Verify focused runtime tests and the existing capability suite (48 tests).
- [ ] Independently review this correction, commit it, then run the one complete gate covering both correction batches before fresh live preparation.
