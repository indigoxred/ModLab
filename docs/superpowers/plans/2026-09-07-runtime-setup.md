# Skyrim 1.6.1170 setup implementation plan

**Goal:** Deliver the user's approved one-hour button/guide/check route using wSkeever's existing Steam patcher, then use it on this machine if downloads complete.

**Architecture:** Keep MO2 and all existing workflows. A small setup dialog exposes upstream downloads, recoverable preparation and a read-only baseline check. It does not implement binary patching or a runtime manager.

**Spec:** Latest user decisions recorded in MODLAB_RESUME_2026-09-07.md in the Codex task's outputs directory. Execution authorized by Resume. Ceiling 03:50:01 UTC, 7 September 2026.

**Delivery:** Completed bounded setup at approximately 03:45 UTC. See `docs/HUB_1170_SETUP_2026-09-07.md` for evidence and exact limitations. Main-menu/SKSE initialization verified on the fresh baseline; full copied-profile dependency rematching and gameplay remain future work. No new broader allowance assumed.

- [x] Preserve current source/tests/docs in an exclusive ZIP outside the worktree.
- [x] Add tests for wrong-store/runtime preflight, retained game/catalog backups, and rejection of an executable version label without the expected binary identity.
- [x] Implement `game_setup.py`: exact Steam source/target identities, backup and read-only known-defect checks. Add `game_setup_dialog.py`, accessible from HelperDialog. Expose official patcher/SKSE/USSEP links and precise instructions; keep all official game content.
- [x] Download/inspect selected archives. Add the matching official SKSE package hash to the existing installer, retaining its runtime/store guards.
- [x] Run only setup/SKSE tests and compile changed UI modules; deploy changed modules into the existing test host.
- [x] Use ModLab setup preparation, upstream patcher and after-check. Install matched SKSE/USSEP through existing ModLab installation. Preserve the old profile; stop before gameplay if any selected native dependency remains mismatched.
- [x] Record exact completed work, remaining limitations and elapsed allowance. Do not expand remaining product scope or call it complete.
