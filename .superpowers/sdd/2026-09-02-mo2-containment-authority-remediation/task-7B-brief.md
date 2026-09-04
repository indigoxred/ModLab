### Task 7B: Classify and migrate bootstrap exact-object moves

**Files:** `modlab/workflows/skyrim/mo2_bootstrap.py`, relevant bootstrap store
immutable-publication helpers if classification proves they are authority moves,
and focused bootstrap/native exact-filesystem tests. Consume Task 7A's versioned
layout/budget. Reuse `modlab/platform/windows_exact_fs.py`; do not add a second
Windows rename algorithm or broaden unrelated recovery/watch behavior.

**Contract:** Classify every bootstrap `_replace_path` caller and relevant publication
as activation, preservation/quarantine, recovery/restoration, in-place adoption
verification, immutable publication, or mutable journal update before editing it.
Adoption currently verifies the existing manager in place: do not invent relocation.
Preserve legitimate mutable-journal `os.replace` with its existing locking/byte checks.

Every actual authority-bearing move must retain exact source and destination-parent
handles through validation, same-volume no-replace rename and post-move identity/
inventory validation. Preserve source ownership, both path ancestry contracts, journal
ordering and immutable receipts. No pathname-only fallback, overwrite, copy/delete,
cross-volume move or reconstruction of unknown objects. Still-live handle ownership
must survive combined operation/cleanup failures. A rename-visible candidate is not
a completed successful receipt; apply the existing shared publisher's exact rollback
boundary without refunding already-consumed replacement authority.

- [ ] **Step 1: Write native RED substitution and recovery regressions**

Exercise real bootstrap activation and old-state preservation/restoration. Test
source/parent substitution, unknown reparse, case-insensitive destination collision,
cross-volume refusal, wrong type, post-move mismatch, retained-handle close failure,
and interruption before/after move and before journal/receipt publication. Test
restart against exact old objects and refusal for substituted ones; assert prior
evidence and unknown content remain unchanged. Cover mutable journal transitions
and in-place adoption behavior so the migration cannot break them.

- [ ] **Step 2: Migrate actual moves and obtain focused GREEN**

Route each classified actual move through the existing shared retained-handle
primitive and preserve caller-specific inventory checks and recovery effects. Prove
normal activation, failed activation preservation, recovery and idempotent reads.

- [ ] **Step 3: Complete combined native gate and independent review**

Run the complete native Windows suite on final combined corrected source, retaining
raw output, exit code, source identity and every skip reason. Commit the source and
evidence; obtain independent review before any live test consumes it. Do not treat
Task 7A's earlier suite or legacy capability evidence as repaired-head proof.

- [ ] **Step 4: Reviewed disposable full-stack gate, then original Task 7**

Only after both code reviews and the complete suite pass, use fresh IDs at the
corrected committed head for exact Python/MO2/archive/watcher/rename live tests.
Include first, reload/second, passive and guarded bytecode/no-drift observations;
freeze no bundle or successful bridge receipt before proof. Preserve every failed
attempt. Independent live-evidence review and the existing fresh four-scenario
decision/eligibility gates still precede main integration or bridge consumption.

---

## Global Constraints

- Work only in `C:\Users\red\Desktop\Modlab\.worktrees\mo2-guard-bridge-handshake` until reviewed integration.
- Every `python.exe` command below means `C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe` and runs with `-B`.
- Historical run `containment-run:2c6220d4ceae48809653c18d5c74562b` and its decision are retained evidence but are always `RetiredInvalidated`.
- Use protocol version `2`, decision-binding version `2`, publication policy `handle-pinned-no-replace-v2`, fixture version `2`, effect-receipt version `1`, and authority-policy version `1`.
- A replacement decision binds: exact Git commit and tree IDs; a canonical source-artifact manifest ID; all five versions above; exact MO2 version and executable SHA-256; exact mechanism; exact run ID; and an ordered mapping of all four scenario names to result IDs.
- Missing current scenario evidence is an operational refusal: exit `3`, no `decision.json`, and no terminal decision object returned.
- A final `Supported`, `Rejected`, or evidence-complete `Incomplete` decision is immutable and idempotent for identical bytes.
- Every authority-bearing file/tree move uses the one shared retained-handle, same-volume, no-replace primitive. No pathname-only fallback exists.
- Public containment JSON keeps its approved field set. Service-authored effects map into `writtenPaths`, `launchedProcesses`, `sourceChanges`, `gameChanges`, and `productionMo2Changes`.
- `show` and every verifier are physically read-only: an absent validation root remains absent.
- Retirements, supersessions, independent reviews, and eligibility records are strict canonical immutable documents. Authority resolution fails closed on zero, multiple, malformed, redirected, or conflicting eligible records.
- No bridge task consumes the replacement decision until the independent review record and eligibility record both validate.
- Do not push or merge `main` before the replacement authority and review gate pass.

## Controller classification and execution contract

### Path admission dependency from Task7A

Task7A's `path_budget.bootstrap_paths` covers job/prior, job/recovered-stage,
job/recovered-activated, final and admitted archive/config paths. New journal budget
uses the actual job identity. Strict stage naming consumes journal.schema_version.
If a recovery source legitimately contains additional entries permitted by its existing
ownership/inventory contract, budget every actual relocated destination with `admit_paths`
before effects, or refuse unsupported patterns. This length admission never grants
ownership or permits otherwise unknown content; the existing evidence checks remain
mandatory. A preparation-only budget does not authorize unbounded native runtime output.

Before code, read `.superpowers/sdd/2026-09-02-mo2-containment-authority-remediation/task-7B-move-classification.md` completely. It supplies the actual operation map and concrete ownership/effect risks. The preceding reviewed Task7A report is `task-7A-report.md` in this directory: use only its new layout/budget interfaces and report; do not repeat Task7A or read the whole historical plan/ledger. Root supplies the exact BASE at dispatch.

Worktree `C:\Users\red\Desktop\Modlab\.worktrees\mo2-guard-bridge-handshake`. Python `C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe` always `-B`. Git `C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe`. Native tests require scoped `require_escalated`. No blanket interpreter approval prefixes. Use apply_patch for every source edit. No subagents or reviewer dispatches; root owns independent review and plan/ledger. No push, merge, UI/MO2/game launch, actual original-attempt restart, global settings, hidden mappings, unrelated cleanup or historical evidence edits.

Native test outputs/scratch stay in this worktree. Use the existing short exclusive native test layout. For the complete Windows suite: TEMP/TMP `<worktree>\nt`, MODLAB_PREPARATION_ARCHIVE `C:\Users\red\Desktop\Modlab\workspace\inbox\Mod.Organizer-2.5.2.7z`, MODLAB_PREPARATION_STEAM `C:\Users\red\Desktop\Steam`; `python -B -m unittest discover -s tests -v`. Coordinate absence-sensitive tests with root to avoid concurrent candidate processes. Keep failure logs immutable and distinguish privileged skips from pass. Do not run the full suite after every edit; focused RED/GREEN while iterating, complete suite once on final combined corrected source.

Test files: `tests/test_mo2_bootstrap_create.py`, `test_mo2_bootstrap_recovery.py`, `test_mo2_bootstrap_store.py`, `test_mo2_bootstrap_adopt.py`, `test_mo2_bootstrap_cli.py`, and new focused native bootstrap move tests if separation is useful. Do not rewrite unrelated test helpers; migrate only retired injection seams to the actual move/publication boundary with behavioral assertions.

Read the existing `task-7-fix1-publication-boundary-clarification.md`. Preserve incomplete-publication rollback versus completed-publication later reload failure. A failed attempt never gains success, and existing tree handles are not deletable temporary candidates. Report any concrete newly discovered authority/architecture expansion to root before implementing it.

Full report: `.superpowers/sdd/2026-09-02-mo2-containment-authority-remediation/task-7B-report.md`. Include classification/changes; native RED failures and GREEN commands/output; full suite command/raw log/exit/counts/skips; changed source hashes and commit; move-completed-but-postcheck-failed behavior; live-owner failure behavior; immutable publication versus journal regressions; exact tests/cases still unavailable; self-review. Commit explicit scoped source/test/report/evidence paths, not root's ledger/plan. Return only status, commits, concise tests, concerns and report path (under15 lines).
