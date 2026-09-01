# Task 4 Round 5 Architecture Ruling

**Baseline:** `4d44130`

**Rejected WIP base:** `4d44130`

**Local archive:** `.superpowers/sdd/2026-09-01-mo2-prewrite-containment-capability-spike/archives/task-4-round-5-abandoned.patch`

## Ruling

The multi-controller recovery-to-success protocol is rejected. Native handles are process-local and cannot be atomically transferred with filesystem owner state. Independent review continued to find cross-thread and cross-process promotion gaps after five controller correction rounds.

The replacement is the approved fail-closed design in `docs/superpowers/specs/2026-09-01-fail-closed-containment-watch-design.md`: interruption remains permanently incomplete, cleanup is non-promoting, and a fresh run is required.

## Preserved work

Reviewed root, alias, guard-chain, journal, cancellation, strict-schema, process-identity, and manifest evidence remains eligible for reuse. The archived round-5 diff is forensic evidence only and must not be merged wholesale.
