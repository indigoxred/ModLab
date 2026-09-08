# Scene Choices Implementation Plan

> Execute inline against the existing MO2 hub. Prior user approval covers this workflow; do not add an approval wait.

**Goal:** Let users combine installed graphics by scene component and prepare those choices through ModLab.

**Architecture:** Add a small scene-selection adapter to the existing Graphics dialog and source-preparation stage. Reuse output publication, dependency inspection and failure recovery. Keep renderer controls and diagnostic evidence available.

**Spec:** `docs/superpowers/specs/2026-09-09-scene-choices-design.md`

## Work

- [ ] Add `scene_choices.py`: deterministic category routing, provider/file catalog, saved requests and source-specific selection planning. Tests in `test_hub_scene_choices.py` cover roads versus mountains, mixed-provider fallback, missing sources, shared dependencies and case/path handling.
- [ ] Add `scene_workflow.py`: check selected NIF dependencies using the existing inspector, publish/recover the profile-owned output, verify source hashes/effective winners and preserve manual changes. Tests exercise two providers with shared dependencies, changed bytes and reset.
- [ ] Extend `graphics_dialog.py` with scene component cards and separate renderer controls. Integrate application before source-helper preparation in `plugin.py`, and findings in `inspection.py`/`guidance.py`. Opening or cancelling the panel must not start helpers. Saving must retain both scene and renderer requests.
- [ ] Deploy only with the disposable MO2 instance closed. Exercise selecting installed roads/mountains, reopening and resetting through the actual UI. Verify the source patch bytes and output provenance, run the hub suite, then commit/push a checkpoint with explicit remaining coverage.

## Constraints

Keep source mods and archived downloads unchanged. Preserve original scope, character functions and advanced helpers. Avoid unnecessary NPC rebuilds as a separate targeted improvement; never omit genuine record dependencies just to reduce processing. Do not claim file verification proves in-game appearance.

## Preparation checkpoint

`scene_choices.py` and `scene_build.py` now cover loose-file component selection, inspected texture dependencies, source-change checks and staged publication through existing owned outputs. Twelve focused tests and the full 526-test hub suite pass. The real installed-file probe selected 276 assets and inspected 256 models for Blended Roads plus Majestic Mountains. A reviewer-identified shared fallback texture replacement was reproduced and corrected before this checkpoint.

The main work items remain open: saved native requests, active-state reconciliation, packed providers/dependencies, reset, the actual Graphics UI and downstream helper integration are not yet implemented by these modules. Continue with that integration; do not present this checkpoint as completed scene customization.
