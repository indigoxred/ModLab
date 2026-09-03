## Spec Compliance — F1 and F2 addressed

Independent reviewer `/root/review_task7_preparation_repair`, gpt-5.6-sol, xhigh.
Reviewed correction BASE `8b6ae7fbcb99a7b5fe759c4d84113ac3184741f8` through HEAD
`44c290876048bf941582e40ab91301aa5dd177f8`, tree
`c83f79b04d8a0d746001075cb5ad251a17ca616f`. Root-preserved final review.

This review accompanies `task-7-repair-fix1-report.md` together with the required
`task-7-fix1-publication-boundary-clarification.md`. The report's earlier promotion
wording must not be used without that correction.

- F1 addressed: `modlab/validation/mo2_containment_service.py:1936` acquires
  source/native/session metadata before consumption. The initializer at :2023
  reserves complete canonical successor intent and Attempt in the immutable v2
  marker before successor-file publication. Publication failures retain exact
  new-successor lineage rather than stranding an unbound intent.
- F1 interruption boundary addressed: startup reconciliation at :4551 requires
  exact reserved-controller/candidate absence, exclusion locks and startup-only
  state. Predecessor enumeration at :4435 retains the failed reserved identity as
  unresolved even without its directory. The disposition does not refund authority
  or permit ordinary preparation.
- F2 addressed: `tests/test_mo2_preparation_recovery.py:130` includes explicitly
  known projection receipts in the old-byte snapshot and checks their IDs against
  the original failure. After-operation equality now covers those receipts without
  traversing fixture junctions.

## Strengths

- Strict v1/v2 decoding (`modlab/validation/mo2_preparation_recovery.py:289`)
  preserves historical replacements without inventing missing reservation metadata.
  Nested successor intent/Attempt bindings are validated canonically.
- Fresh-only publication (`modlab/validation/mo2_containment_store.py:1768`)
  refuses same-byte pre-existing records and collisions on the new startup path,
  while preserving established default behavior elsewhere.
- Caught startup failures (`mo2_containment_service.py:2048`) record the actual
  failed phase without claiming absence. Dual publication failures retain the
  union of native owners.
- Native hard-cut tests (`test_mo2_preparation_recovery.py:368`) cover interruption
  after consumption and around Attempt publication. Their initializer-seam limit
  is disclosed; the separate real archive regression covers production recovery/restart.
- Adversarial startup tests (`test_mo2_preparation_recovery.py:404`) cover substituted
  records, unknown children, uncertain barriers/controllers, late state changes,
  ambiguous publication and same-byte collisions.

## Issues

### Critical

None identified in the scoped correction.

### Important

None identified in the scoped correction.

### Minor

Documentation boundary requiring root's explicit correction: fix1 implementation
report line38 describes atomic marker promotion as consumption too broadly. The
unchanged publisher may still delete its exact retained candidate after rename if
validation or closure fails. Root explicitly corrected the interpretation:

- Exact-candidate failed-publication rollback is permitted before consumption
  returns and before successor initialization.
- Successfully completed consumption cannot be rolled back or replenished by this repair.
- Exception alone proves neither rollback nor absence. A surviving marker or
  unresolved publication remains subject to exact reload and uncertainty handling.
- This does not authorize stale-candidate cleanup/adoption.

The preserved root correction must accompany the report; its original sentence
must not be used independently as operational policy. No remaining code blocker.

## Named Checks and Verification

- Canonical intent compatibility: unchanged store canonicalization at :159 and
  service intent-ID calculation at :4632 both match reservation UTF-8,
  ensure_ascii=False, sorted compact JSON with trailing newline.
- Publication rollback/ownership: `modlab/platform/windows_exact_fs.py:1064` and
  store failure-handling continuation at :1797 (cut off between diff hunks) confirm
  the corrected boundary. No alternative rename primitive was introduced.
- Startup-only classification (`mo2_containment_service.py:4524`) rejects fixtures,
  unknown state, contradictory Attempt-without-intent and present/uncertain barrier.
  Known startup bytes are compared without adopting/rewriting/cleaning successor objects.
- Native gate: root-verified 1097 tests, 1087 passed, 10 declared skips, zero
  failures/errors/warnings, exit0, 384.931s. New real archive recovery/restart and F2
  passed. Skips: eight existing symlink-privilege cases, two older unconfigured opt-ins.
- Byte binding: root verified all12 frozen files against the committed correction.
  Tests preceded commits but exercised those exact bytes; not misrepresented as
  execution after commits.
- Scope: correction package read once. No suite/Git checks repeated; no extra probe
  necessary. No source/index/HEAD, actual runtime, GUI or capability-matrix operation.

## Cannot Verify / Separate Prerequisites

Approval does not establish actual legacy-run recovery or live readiness. Original
extraction-path limit, inherited bootstrap os.replace boundary, non-startup hard-
interruption refusal limits and restricted historical diagnostic directory remain
separate disclosed prerequisites. Earlier assessment was not repeated or waived.

## Assessment

**Task quality: Approved.** F1 preserves exact successor lineage across the reviewed
startup failures/interruptions without refunding old authority; F2 closes the byte-
preservation coverage gap. Approval is limited to this correction and does not
authorize live consumption, merge or push.
