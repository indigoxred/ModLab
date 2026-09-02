# MO2 Guard Bridge Deployment and Handshake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transactionally deploy and verify the ModLab Guard inside the exact managed MO2 2.5.2 instance, then prove a one-use no-install handshake without changing Skyrim, mods, profiles, downloads, Overwrite, saves, or manager configuration.

**Architecture:** A versioned three-file Python module plug-in is copied from an explicit repository bundle into MO2 only through a durable setup/uninstall transaction. The plug-in is passive unless an exact one-use handoff is present; for a `Handshake` request it clears the handoff environment, atomically claims the request, registers a fail-closed launch veto, waits for MO2 UI initialization and a forced refresh boundary, verifies all configured identities, and publishes immutable events plus `SafeToClose`. The host captures exact before/after boundary evidence and accepts the handshake only when the bridge evidence and protected state agree.

**Tech Stack:** Python 3.12 standard library, frozen dataclasses, strict canonical JSON, `pathlib`, Windows process APIs through `ctypes`, MO2 2.5.2's bundled Python plug-in runtime, `subprocess` argument arrays, existing ModLab bootstrap/containment evidence, `unittest`, and computer control only for visible live MO2 validation.

**Spec:** `docs/superpowers/specs/2026-08-31-guided-mo2-lab-installation-design.md`

## Global Constraints

- Execute this plan in an isolated worktree created at execution time with `superpowers:using-git-worktrees`; suggested branch `feature/mo2-guard-bridge-handshake` and path `C:\Users\red\Desktop\Modlab\.worktrees\mo2-guard-bridge-handshake`.
- Read the complete approved spec and `docs/validation/mo2-containment-spike.md` before Task 1; both are authoritative.
- Python 3.12 standard library only; add no package-manager or runtime dependency.
- Use `C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe` with `-B` for every host-side command.
- The behavior target is the exact retained portable MO2 `2.5.2` / `ModOrganizer.exe` `2.5.2.0` build and the supported containment mechanism `isolated-low-integrity-junction-projection-v1`.
- This plan implements `Handshake` only. It does not call `installMod()`, open Quick/Manual/FOMOD installers, analyze a mod, adopt a staged mod, Keep/Undo a mod, run libloot, launch Skyrim/SKSE/xEdit/LOOT, or expose `skyrim mod install`.
- Public lifecycle operations derive the bridge target only as `workspace/tools/mo2/skyrim-se-ae/app/plugins/modlab_guard`; no CLI/API accepts an arbitrary MO2 executable, plug-in target, profile, mods root, or game root.
- The bridge is passive when the two handoff environment variables are absent. Ordinary MO2 launches must behave as before.
- Every claimed request clears both handoff variables before callbacks are registered or any external process can inherit them.
- A guarded handshake vetoes every MO2 external-program launch. There is no analyzer exception in this phase.
- Unknown, malformed, incomplete, redirected, changed, or ambiguous evidence fails closed. It never becomes `Verified`, `Safe`, or `Passed` by default.
- MO2 is never killed. If it is running, lifecycle mutations refuse. If a handshake is interrupted, recovery waits or refuses until the exact process is proven absent.
- Bridge setup, uninstall, and recovery never recursively delete data. Proven owned trees are moved by same-volume rename into job-local quarantine.
- `PYTHONDONTWRITEBYTECODE=1` is set for every guarded launch. Setup verification rejects `__pycache__`, `.pyc`, or any undeclared bridge entry.
- All requests, claims, events, journals, receipts, before/after evidence, temporary files, and quarantine remain beneath `C:\Users\red\Desktop\Modlab\workspace` in production.
- Steam and the Skyrim game are read-only. Save/co-save paths are never opened or hashed; live validation compares only non-content save-directory metadata in memory and persists only the boolean result.
- Disposable setup/handshake must pass before production bridge deployment. Production validation stops after a no-install handshake and byte-for-byte boundary comparison.
- A successful handshake proves only the bridge lifecycle and its declared preservation boundary. It does not prove mod compatibility or that Skyrim runs correctly.

## Scope Decomposition

This is the second dependency-ordered implementation plan for guided Lab installation. The completed containment spike selected the pre-write mechanism. This plan implements bridge deployment and the `Handshake` lifecycle only. A later plan will add isolated `Install`/`Analyze` requests, archive installation evidence, conflict providers, and Keep/Undo; it may consume only a verified bridge receipt and a passing handshake receipt produced here.

## File Structure

### Bridge bundle and protocol

- `modlab/resources/__init__.py` — resource package marker.
- `modlab/resources/mo2_guard/__init__.py` — MO2 module entry point with delayed import so host tests do not require `mobase`.
- `modlab/resources/mo2_guard/protocol.py` — dependency-free strict bundle-receipt/request/event protocol used unchanged by host and deployed plug-in.
- `modlab/resources/mo2_guard/plugin.py` — MO2 `IPluginTool` adapter, one-use claim, callback lifecycle, veto, and durable handshake publication.
- `modlab/adapters/mo2/bridge_bundle.py` — explicit bundle inventory and exact target verification.
- `modlab/adapters/mo2/bootstrap_inventory.py` — verifies the original bootstrap package identity while excluding only one exact receipt-backed bridge overlay.

### Host lifecycle

- `modlab/adapters/mo2/bridge_model.py` — immutable lifecycle, receipt, handshake, and protected-boundary values.
- `modlab/adapters/mo2/bridge_serialization.py` — canonical schemas and content identities.
- `modlab/workflows/skyrim/mo2_bridge_store.py` — confined jobs, receipts, request/event directories, locks, atomic writes, and exact reloads.
- `modlab/workflows/skyrim/mo2_bridge_lifecycle.py` — setup, verify, uninstall, and rollback/recovery orchestration.
- `modlab/workflows/skyrim/mo2_bridge_handshake.py` — supported-decision preflight, protected evidence, launch/wait, finalization, and interrupted-handshake recovery.
- `modlab/workflows/skyrim/mo2_bridge_rendering.py` — stable text/JSON output.
- `modlab/platform/__init__.py` — platform helper package marker.
- `modlab/platform/windows_process.py` — retained Windows process identity and exact wait/absence checks without process termination.

### Existing files

- `modlab/workspace.py` — bridge job, receipt, validation, and quarantine roots.
- `modlab/cli.py` — `skyrim mo2 bridge setup|verify|uninstall|recover|handshake` commands.
- `README.md` — lifecycle commands, passive behavior, and explicit no-install boundary.
- `docs/validation/mo2-guard-bridge-handshake.md` — sanitized disposable and production verdict.

### Tests

- `tests/test_mo2_bridge_protocol.py`
- `tests/test_mo2_bridge_serialization.py`
- `tests/test_mo2_bridge_bundle.py`
- `tests/test_mo2_bootstrap_inventory.py`
- `tests/test_mo2_bridge_store.py`
- `tests/test_mo2_bridge_lifecycle.py`
- `tests/test_mo2_guard_plugin.py`
- `tests/test_mo2_bridge_handshake.py`
- `tests/test_mo2_bridge_cli.py`
- `tests/test_mo2_bridge_windows_integration.py`
- `tests/support/mo2_bridge.py`

---

### Task 1: Exact bundle inventory and strict one-use handshake protocol

**Files:**
- Create: `modlab/resources/__init__.py`
- Create: `modlab/resources/mo2_guard/__init__.py`
- Create: `modlab/resources/mo2_guard/protocol.py`
- Create: `modlab/resources/mo2_guard/plugin.py`
- Create: `modlab/adapters/mo2/bridge_bundle.py`
- Create: `modlab/adapters/mo2/bootstrap_inventory.py`
- Create: `tests/test_mo2_bridge_protocol.py`
- Create: `tests/test_mo2_bridge_bundle.py`
- Create: `tests/test_mo2_bootstrap_inventory.py`

**Interfaces:**
- Consumes: three repository source files and normalized request/event values; no live MO2 import.
- Produces: `BridgeBundleFile`, `BridgeReceipt`, `BridgeRequestKind`, `BridgeEventKind`, `BridgeRequest`, `BridgeEvent`, `SafeToClose`, strict receipt/request/event byte converters, content-ID functions, `declared_guard_bundle()`, `verify_guard_bundle()`, and `verify_bootstrap_inventory_with_bridge_overlay()`.

- [ ] **Step 1: Write failing strict-protocol and inventory tests**

```python
class Mo2BridgeProtocolTests(unittest.TestCase):
    def test_handshake_request_round_trips_canonically(self):
        request = valid_handshake_request()
        encoded = request_to_bytes(request)
        self.assertEqual(request, request_from_bytes(encoded))
        self.assertEqual(encoded, request_to_bytes(request))
        self.assertEqual(hashlib.sha256(encoded).hexdigest(), request_sha256(request))

    def test_protocol_rejects_duplicate_extra_unsafe_and_impossible_values(self):
        for data, message in malformed_bridge_protocol_documents():
            with self.subTest(message=message):
                with self.assertRaisesRegex(BridgeProtocolError, message):
                    parse_bridge_document(data)

class Mo2BridgeBundleTests(unittest.TestCase):
    def test_bundle_contains_only_three_declared_python_files(self):
        bundle = declared_guard_bundle()
        self.assertEqual(
            ("__init__.py", "plugin.py", "protocol.py"),
            tuple(item.relative_path for item in bundle.files),
        )
        self.assertTrue(all(item.sha256 == hashlib.sha256(item.data).hexdigest() for item in bundle.files))

    def test_bootstrap_inventory_accepts_only_the_exact_receipted_overlay(self):
        fixture = bootstrap_inventory_fixture()
        receipt = fixture.install_exact_guard()
        self.assertEqual(
            fixture.bootstrap_inventory_sha256,
            verify_bootstrap_inventory_with_bridge_overlay(
                fixture.app_root, fixture.bootstrap_receipt, receipt
            ).sha256,
        )
```

Malformed cases must include duplicate JSON keys, unknown fields, bool-as-int, mixed-case SHA-256, an unsupported schema/kind, mismatched job/request IDs, unsafe/relative/UNC/redirect-capable paths, a request path outside its exact job directory, a SafeToClose record not bound to the final event, duplicate/non-contiguous event sequence numbers, and a `SafeToClose` event after a failed identity check.

- [ ] **Step 2: Run focused tests and verify failure**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_bridge_protocol tests.test_mo2_bridge_bundle tests.test_mo2_bootstrap_inventory -v`

Expected: FAIL because the bridge resource/protocol modules do not exist.

- [ ] **Step 3: Add the exact immutable protocol values**

```python
class BridgeRequestKind(StrEnum):
    HANDSHAKE = "Handshake"

class BridgeEventKind(StrEnum):
    REQUEST_CLAIMED = "RequestClaimed"
    VETO_REGISTERED = "VetoRegistered"
    UI_READY = "UiReady"
    REFRESH_COMPLETED = "RefreshCompleted"
    LAUNCH_REJECTED = "LaunchRejected"
    FAILED = "Failed"
    SAFE_TO_CLOSE = "SafeToClose"

@dataclass(frozen=True)
class BridgeRequest:
    schema_version: int
    request_id: str
    kind: BridgeRequestKind
    nonce: str
    job_id: str
    workspace_root: str
    request_path: str
    claimed_path: str
    event_root: str
    safe_to_close_path: str
    mo2_version: str
    bootstrap_receipt_id: str
    bridge_receipt_id: str
    bridge_receipt_path: str
    containment_run_id: str
    containment_decision_id: str
    profile_name: str
    profile_path: str
    base_path: str
    downloads_path: str
    mods_path: str
    overwrite_path: str

@dataclass(frozen=True)
class BridgeEvent:
    schema_version: int
    job_id: str
    request_id: str
    request_sha256: str
    sequence: int
    kind: BridgeEventKind
    detail_code: str
    observed_profile_name: str | None
    observed_profile_path: str | None
    observed_base_path: str | None
    observed_downloads_path: str | None
    observed_mods_path: str | None
    observed_overwrite_path: str | None
    observed_mo2_version: str | None
    rejected_executable: str | None

@dataclass(frozen=True)
class SafeToClose:
    schema_version: int
    job_id: str
    request_id: str
    request_sha256: str
    safe_event_sha256: str
    safe_sequence: int
    outcome: str
```

Add `BridgeBundleFile(relative_path, sha256, size)` and a strict `BridgeReceipt` containing schema, content ID, qualification, job/bootstrap IDs, target root, bundle version, the three ordered bundle files, and UTC verification time. `bridge_receipt_id_for()` hashes the canonical receipt body with `receiptId` omitted, then the serializer requires that ID in the complete document; it never attempts a self-referential full-document hash. Require schema `1`, exact `Handshake`, exact profile `ModLab - Lab`, exact MO2 version `2.5.2.0`, `bridge-job:<32 lowercase hex>`, `bridge-request:<32 lowercase hex>`, 64-lowercase-hex nonce/SHA values, typed bootstrap/bridge/containment IDs, absolute direct Windows paths, and exact containment relationships among the request, receipt, claim, events, and SafeToClose paths. The receipt path is exactly `workspace/games/skyrim-se-ae/tool-installations/mo2-guard/<receipt-digest>.json`. `SafeToClose.outcome` is only `Passed`; a failure is durable evidence but never a SafeToClose success in this phase.

- [ ] **Step 4: Implement canonical JSON and the explicit three-file bundle**

Use UTF-8 without BOM, `json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)`, duplicate-key rejection, exact field sets, bool-not-int checks, and a stable re-read before accepting bytes. `declared_guard_bundle()` must read only `__init__.py`, `plugin.py`, and `protocol.py`; it must reject a missing source and never enumerate-and-copy arbitrary directory content. `verify_guard_bundle(root, expected_files)` must reject every undeclared entry, directory, symlink, junction, reparse point, `.pyc`, and `__pycache__` before comparing size/SHA-256.

`verify_bootstrap_inventory_with_bridge_overlay()` walks the managed MO2 package using the bootstrap inventory's existing rules while excluding exactly `plugins/modlab_guard` only after that direct directory and all three children match the supplied strict bridge receipt. It then requires the remaining package inventory to equal the original bootstrap receipt. An absent overlay is accepted only before first setup; an unreceipted, changed, redirected, nested-extra, or second overlay fails closed.

The entry point is deliberately import-safe on the host:

```python
def createPlugin():
    from .plugin import ModLabGuard
    return ModLabGuard()
```

Task 1's `plugin.py` is a complete passive `IPluginTool`: it implements the required metadata/tool methods, `init()` returns true without registering callbacks, and `display()` returns without mutation. Task 5 replaces only that passive `init()` behavior with the tested guarded session lifecycle; the bundle is therefore valid and loadable at every task boundary rather than containing a placeholder.

- [ ] **Step 5: Run focused tests**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_bridge_protocol tests.test_mo2_bridge_bundle tests.test_mo2_bootstrap_inventory -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add modlab/resources modlab/adapters/mo2/bridge_bundle.py modlab/adapters/mo2/bootstrap_inventory.py tests/test_mo2_bridge_protocol.py tests/test_mo2_bridge_bundle.py tests/test_mo2_bootstrap_inventory.py
git commit -m "feat: define strict MO2 guard handshake protocol"
```

---

### Task 2: Durable lifecycle, receipt, and handshake records

**Files:**
- Create: `modlab/adapters/mo2/bridge_model.py`
- Create: `modlab/adapters/mo2/bridge_serialization.py`
- Create: `tests/test_mo2_bridge_serialization.py`
- Create: `tests/support/mo2_bridge.py`

**Interfaces:**
- Consumes: Task 1 bundle/protocol identities plus existing `FileIdentity`, `ProcessObservation`, and `TreeIdentity` evidence values.
- Produces: `BridgeAction`, `BridgeJobState`, `BridgeHealth`, `BridgeBundleIdentity`, `BridgeTargetSnapshot`, `BridgeJournal`, `ProtectedBoundary`, `HandshakeState`, `HandshakeJournal`, `HandshakeReceipt`, strict lifecycle/handshake byte converters, and content-ID functions. Task 1's `BridgeReceipt` is imported and re-used; no second receipt schema is created.

- [ ] **Step 1: Write failing round-trip, transition, and rejection tests**

```python
class Mo2BridgeSerializationTests(unittest.TestCase):
    def test_receipt_and_handshake_receipt_round_trip_canonically(self):
        for value, to_bytes, from_bytes, identity_for in bridge_documents():
            encoded = to_bytes(value)
            self.assertEqual(value, from_bytes(encoded))
            self.assertEqual(encoded, to_bytes(value))
            self.assertEqual(identity_for(value), identity_for(from_bytes(encoded)))
            self.assertEqual(identity_for(value), from_bytes(encoded).receipt_id)

    def test_impossible_state_and_evidence_combinations_are_rejected(self):
        for parser, data, message in malformed_bridge_documents():
            with self.subTest(message=message):
                with self.assertRaisesRegex(BridgeFormatError, message):
                    parser(data)
```

Cases must cover `Verified` without a receipt, `Uninstalled` while the target is present, `Recovered` without a recovery action, a receipt not bound to the exact bootstrap receipt/bundle/target, `Passed` handshake without a SafeToClose digest, before/after inequality labeled unchanged, a launch-veto violation labeled Passed, incomplete process identity, containment verdict other than Supported, the wrong mechanism/version/profile, save/co-save paths in evidence, and action arrays that claim unperformed changes.

- [ ] **Step 2: Run and verify failure**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_bridge_serialization -v`

Expected: FAIL because the model and serializer do not exist.

- [ ] **Step 3: Add exact states and records**

```python
class BridgeAction(StrEnum):
    SETUP = "Setup"
    UNINSTALL = "Uninstall"

class BridgeJobState(StrEnum):
    PREPARED = "Prepared"
    STAGING = "Staging"
    STAGED = "Staged"
    APPLYING = "Applying"
    ACTIVATED = "Activated"
    VERIFIED = "Verified"
    UNINSTALLING = "Uninstalling"
    UNINSTALLED = "Uninstalled"
    RECOVERY_REQUIRED = "RecoveryRequired"
    RECOVERED = "Recovered"

class HandshakeState(StrEnum):
    PREPARED = "Prepared"
    MO2_STARTING = "Mo2Starting"
    MO2_ACTIVE = "Mo2Active"
    SAFE_TO_CLOSE = "SafeToClose"
    VERIFIED = "Verified"
    RECOVERY_REQUIRED = "RecoveryRequired"
    RECOVERED = "Recovered"

@dataclass(frozen=True)
class ProtectedBoundary:
    manager_configuration: tuple[OptionalFileIdentity, ...]
    lab_profile: TreeIdentity
    play_profile: TreeIdentity
    existing_mods: TreeIdentity
    downloads: TreeIdentity
    overwrite: TreeIdentity
    bounded_game: TreeIdentity

@dataclass(frozen=True)
class HandshakeReceipt:
    schema_version: int
    receipt_id: str
    job_id: str
    request_id: str
    request_sha256: str
    bridge_receipt_id: str
    bootstrap_receipt_id: str
    containment_run_id: str
    containment_decision_id: str
    safe_to_close_sha256: str
    before: ProtectedBoundary
    after: ProtectedBoundary
    save_directory_unchanged: bool
    rejected_launch_count: int
    mo2_executable: FileIdentity
    verified_at: str
```

Add full `BridgeJournal` and `HandshakeJournal` fields for exact paths, prior/target/bundle identities, PID plus creation time, state markers, receipt IDs, errors, action arrays, and timestamps. Legal transitions are explicit adjacency maps; no serializer or store infers a skipped state.

- [ ] **Step 4: Implement strict canonical serialization**

IDs are `bridge-job:<32hex>`, `bridge-receipt-sha256:<64hex>`, `bridge-handshake-receipt-sha256:<64hex>`, and the existing typed IDs. Receipt IDs hash their canonical bodies with `receiptId` omitted; complete serialized documents include and validate the computed ID. Recompute every content ID while loading. Sort only fields declared unordered; preserve ordered event/file evidence. Reject unknown fields, unsafe paths, redirects represented in evidence, duplicate case-insensitive paths, non-UTC timestamps, and impossible state/action combinations.

- [ ] **Step 5: Run focused tests and commit**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_bridge_serialization -v`

Expected: PASS.

```powershell
git add modlab/adapters/mo2/bridge_model.py modlab/adapters/mo2/bridge_serialization.py tests/test_mo2_bridge_serialization.py tests/support/mo2_bridge.py
git commit -m "feat: model durable MO2 guard lifecycle"
```

---

### Task 3: Confined bridge job and receipt store

**Files:**
- Create: `modlab/workflows/skyrim/mo2_bridge_store.py`
- Modify: `modlab/workspace.py`
- Create: `tests/test_mo2_bridge_store.py`
- Modify: `tests/test_workspace.py`

**Interfaces:**
- Consumes: Task 2 canonical records and Task 1 protocol bytes.
- Produces: `Mo2BridgeStore`, `new_bridge_job_id()`, immutable receipt/request/event writes, exact state transitions, one-job locking, and confined paths.

- [ ] **Step 1: Write failing store and workspace tests**

```python
class Mo2BridgeStoreTests(unittest.TestCase):
    def test_create_transition_and_receipt_reload_exactly(self):
        store = Mo2BridgeStore(self.root)
        created = store.create_lifecycle_job(valid_bridge_journal())
        verified = store.transition_lifecycle_job(created.job_id, BridgeJobState.STAGING, BridgeJobState.STAGED)
        stored = store.write_bridge_receipt(valid_bridge_receipt(job_id=verified.job_id))
        self.assertEqual(stored.receipt, store.load_bridge_receipt(stored.receipt.receipt_id).receipt)

    def test_redirect_case_collision_replacement_and_replay_are_rejected(self):
        for mutation, message in unsafe_store_mutations(self.root):
            with self.subTest(message=message):
                mutation()
```

Cover concurrent writers, stale transitions, immutable-content mismatch, a receipt file whose name/content disagree, redirected job/receipt/target ancestors, request replay after claim, event overwrite, sequence gaps, and an unknown file in an otherwise valid store.

- [ ] **Step 2: Run and verify failure**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_bridge_store tests.test_workspace -v`

Expected: FAIL because bridge layout/store fields do not exist.

- [ ] **Step 3: Extend the organized workspace**

Add direct non-reparse directories and `WorkspaceLayout` fields for:

```text
runtime/jobs/mo2-bridge/
runtime/validation/mo2-bridge/
library/quarantine/mo2-bridge/
games/skyrim-se-ae/tool-installations/mo2-guard/
games/skyrim-se-ae/tool-installations/mo2-guard-handshakes/
```

The final plug-in target remains beneath the already managed app and is created only by lifecycle setup, not `workspace init`.

- [ ] **Step 4: Implement confined atomic storage**

Use exact-field reload after every write, per-job Windows named mutexes with file-lock fallback for tests, `xb`/flush/fsync for immutable documents, atomic replace only for mutable journals, and case-insensitive target validation. Store layout is:

```text
runtime/jobs/mo2-bridge/<job-id>/
  journal.json
  prior/
  stage/
  quarantine/
  pending/request.json
  claimed/request.json
  events/000001.json
  safe-to-close.json
  before.json
  after.json
```

`claim_request()` uses Windows `MoveFileExW` without `MOVEFILE_REPLACE_EXISTING` from the exact pending path to the exact claimed path; public Windows execution refuses when that primitive is unavailable. `write_event()` writes a job-local unique temporary file, flushes/fsyncs it, then uses the same no-replace primitive for the next exact integer sequence. Setup activation and quarantine renames use the same helper. Historical terminal jobs and receipts are read-only.

- [ ] **Step 5: Run focused tests and commit**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_bridge_store tests.test_workspace -v`

Expected: PASS.

```powershell
git add modlab/workspace.py modlab/workflows/skyrim/mo2_bridge_store.py tests/test_workspace.py tests/test_mo2_bridge_store.py
git commit -m "feat: store MO2 guard jobs and receipts safely"
```

---

### Task 4: Transactional setup, verification, uninstall, and recovery

**Files:**
- Create: `modlab/workflows/skyrim/mo2_bridge_lifecycle.py`
- Create: `tests/test_mo2_bridge_lifecycle.py`

**Interfaces:**
- Consumes: exact compatible MO2 bootstrap receipt, Task 1 bundle verification, Task 2 records, Task 3 store, and `inspect_mo2_processes()`.
- Produces: `setup_mo2_guard(workspace_root)`, `verify_mo2_guard(workspace_root)`, `uninstall_mo2_guard(workspace_root)`, and `recover_mo2_guard(job_id, workspace_root)`.

- [ ] **Step 1: Write failing lifecycle and fault-injection tests**

```python
class Mo2BridgeLifecycleTests(unittest.TestCase):
    def test_setup_is_atomic_verified_and_idempotent(self):
        first = setup_mo2_guard(self.root, dependencies=self.dependencies)
        second = setup_mo2_guard(self.root, dependencies=self.dependencies)
        self.assertEqual("Verified", first.outcome)
        self.assertEqual("AlreadyVerified", second.outcome)
        self.assertEqual(first.receipt.receipt_id, second.receipt.receipt_id)
        verify_guard_bundle(
            self.layout.skyrim_mo2_app / "plugins" / "modlab_guard",
            first.receipt.bundle_files,
        )

    def test_uninstall_moves_exact_owned_bridge_to_quarantine(self):
        setup = setup_mo2_guard(self.root, dependencies=self.dependencies)
        result = uninstall_mo2_guard(self.root, dependencies=self.dependencies)
        self.assertEqual("Uninstalled", result.outcome)
        self.assertFalse(Path(setup.receipt.target_root).exists())
        self.assertTrue(Path(result.journal.quarantine_root).is_dir())
```

Fault cases must interrupt before/after stage creation, stage verification, target activation, receipt write, uninstall rename, and terminal transition. Also cover running/unknown process evidence, missing/malformed/incompatible bootstrap receipt, modified MO2 executable, unknown target bytes, target redirect, source bundle change during staging, insufficient same-volume rename proof, extra target content, and recovery when current bytes match neither recorded state.

- [ ] **Step 2: Run and verify failure**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_bridge_lifecycle -v`

Expected: FAIL because lifecycle functions do not exist.

- [ ] **Step 3: Implement setup preflight and activation**

`setup_mo2_guard()` must:

1. find and fully verify the one compatible bootstrap receipt, requiring the current package inventory to match it with no overlay before first setup or with only the exact already-receipted bridge overlay on an idempotent setup;
2. prove complete process evidence and no managed `ModOrganizer.exe`/`nxmhandler.exe` process;
3. classify the target as absent, exact current receipt, or unknown;
4. return `AlreadyVerified` without mutation for the exact current receipt;
5. refuse unknown or redirected content without moving it;
6. write `Prepared` before creating stage content;
7. copy only the three declared files with `xb`, fsync, and exact re-read;
8. verify no undeclared stage entry, write `Staged`, then `Applying`;
9. atomically rename stage to the absent target, write `Activated`, verify again;
10. write/reload the content-addressed receipt and terminal `Verified`.

No v1 in-place upgrade is supported. A later bundle version must first use the exact uninstall transaction and then setup.

- [ ] **Step 4: Implement verify, uninstall, and rollback recovery**

`verify_mo2_guard()` is read-only and requires the receipt, original bootstrap inventory after excluding only the exact receipt-backed bridge overlay, source bundle, target bundle, MO2 executable, and target path all to match. `uninstall_mo2_guard()` requires the exact verified receipt and idle MO2, writes `Uninstalling`, then renames the exact target into job quarantine and writes `Uninstalled`; it never deletes the quarantine or receipt.

`recover_mo2_guard()` never kills MO2. For an interrupted setup it restores the exact pre-action absent state by moving only a proven stage/activated three-file bundle into job quarantine. For an interrupted uninstall it restores the exact pre-action verified state by renaming the proven quarantine bundle back to the exact absent target. Any unknown content, changed hash, extra entry, redirect, process uncertainty, or collision remains `RecoveryRequired` with all content left in place.

- [ ] **Step 5: Run focused tests and commit**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_bridge_lifecycle -v`

Expected: PASS.

```powershell
git add modlab/workflows/skyrim/mo2_bridge_lifecycle.py tests/test_mo2_bridge_lifecycle.py
git commit -m "feat: deploy MO2 guard transactionally"
```

---

### Task 5: Passive MO2 plug-in and fail-closed handshake callbacks

**Files:**
- Modify: `modlab/resources/mo2_guard/plugin.py`
- Create: `tests/test_mo2_guard_plugin.py`
- Modify: `tests/support/mo2_bridge.py`

**Interfaces:**
- Consumes: Task 1 protocol, exact two-variable environment handoff, and a real/fake `mobase.IOrganizer`.
- Produces: `ModLabGuard`, passive normal-launch behavior, one-use claim, launch veto, UI/refresh validation, ordered events, and SafeToClose publication.

- [ ] **Step 1: Write failing fake-MO2 lifecycle tests**

```python
class Mo2GuardPluginTests(unittest.TestCase):
    def test_no_handoff_is_completely_passive(self):
        plugin, organizer = load_guard_with_fake_mobase(environment={})
        self.assertTrue(plugin.init(organizer))
        self.assertEqual([], organizer.registered_callbacks)
        self.assertEqual([], organizer.calls)

    def test_handshake_claims_clears_vetoes_refreshes_and_publishes(self):
        fixture = GuardPluginFixture(self.root)
        plugin, organizer = fixture.load()
        self.assertTrue(plugin.init(organizer))
        self.assertNotIn("MODLAB_MO2_BRIDGE_REQUEST", os.environ)
        self.assertNotIn("MODLAB_MO2_BRIDGE_REQUEST_SHA256", os.environ)
        organizer.fire_ui_initialized()
        organizer.fire_next_refresh()
        self.assertEqual(
            ["RequestClaimed", "VetoRegistered", "UiReady", "RefreshCompleted", "SafeToClose"],
            fixture.event_kinds(),
        )
        self.assertTrue(fixture.safe_to_close_path.is_file())
        self.assertFalse(organizer.fire_about_to_run("SkyrimSE.exe", "", ""))
```

Also test partial/missing handoff, request-hash mismatch, replayed claim, bundle/receipt mismatch, wrong profile/version/path, callback registration failure, refresh registration failure, refresh exception, event write failure, unsafe requested paths, a launch before and after refresh, and `display()` performing no manager mutation.

- [ ] **Step 2: Run and verify failure**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_guard_plugin -v`

Expected: FAIL because the Task 1 plug-in is intentionally passive and has no guarded handshake behavior.

- [ ] **Step 3: Implement the minimal MO2 `IPluginTool` adapter**

```python
class ModLabGuard(mobase.IPluginTool):
    def __init__(self):
        super().__init__()
        self._organizer = None
        self._session = None

    def name(self):
        return "ModLab Guard"

    def localizedName(self):
        return self.name()

    def author(self):
        return "ModLab"

    def description(self):
        return "Guards explicit ModLab MO2 sessions."

    def version(self):
        return mobase.VersionInfo(1, 0, 0, mobase.ReleaseType.FINAL)

    def settings(self):
        return []

    def displayName(self):
        return "ModLab Guard status"

    def tooltip(self):
        return self.description()

    def icon(self):
        return QIcon()

    def setParentWidget(self, widget):
        self._parent = widget

    def display(self):
        return None
```

`init()` reads both handoff variables once, removes both in a `finally` block, and is passive when both are absent. A partial pair is an inert failed session with no untrusted path write. With a full pair, it stable-reads and hashes the request, validates exact job and receipt-path containment, uses its dependency-free `ctypes` `MoveFileExW` no-replace helper to rename pending to claimed, re-reads claimed bytes, strict-loads the content-addressed bridge receipt, verifies the receipt's job/bootstrap/target bindings and its own three files, writes `RequestClaimed`, registers the three-argument `onAboutToRun` callback and `onUserInterfaceInitialized`, and writes `VetoRegistered` only after both registration calls return true.

- [ ] **Step 4: Implement UI/refresh and fail-closed evidence**

The UI callback verifies `organizer.version()`, `profileName()`, `profilePath()`, `basePath()`, `downloadsPath()`, `modsPath()`, and `overwritePath()` against the request using case-insensitive canonical Windows paths. It writes `UiReady`, registers `onNextRefresh(callback, False)`, and calls `organizer.refresh(False)`. The zero-argument refresh callback repeats every identity check, writes `RefreshCompleted`, then atomically publishes the `SafeToClose` event and a `safe-to-close.json` bound to that event's SHA/sequence. The SafeToClose record marks the close boundary, not the end of the event stream; a later veto rejection is appended at the next sequence and is reviewed after MO2 exits.

The veto callback always returns `False` during a claimed handshake and best-effort writes `LaunchRejected` with the executable path; a write failure keeps returning `False` and prevents SafeToClose. Any validation/callback/persistence failure best-effort writes one `Failed` event, never writes SafeToClose, and leaves the plug-in passive except that an already-registered veto remains fail-closed for that MO2 process.

- [ ] **Step 5: Run focused tests and commit**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_guard_plugin -v`

Expected: PASS.

```powershell
git add modlab/resources/mo2_guard/plugin.py tests/test_mo2_guard_plugin.py tests/support/mo2_bridge.py
git commit -m "feat: run fail-closed MO2 guard handshake"
```

---

### Task 6: Host launch, protected-boundary comparison, and recovery

**Files:**
- Create: `modlab/platform/__init__.py`
- Create: `modlab/platform/windows_process.py`
- Create: `modlab/workflows/skyrim/mo2_bridge_handshake.py`
- Create: `tests/test_mo2_bridge_handshake.py`

**Interfaces:**
- Consumes: Supported containment decision, verified bootstrap/bridge receipts, selected Skyrim checkpoint, Task 3 request/event store, Task 5 plug-in, and retained process identity.
- Produces: `run_mo2_guard_handshake(workspace_root, containment_run_id, progress=None)` and `recover_mo2_guard_handshake(job_id, workspace_root)`.

- [ ] **Step 1: Write failing preflight, launch, evidence, and recovery tests**

```python
class Mo2BridgeHandshakeTests(unittest.TestCase):
    def test_exact_handshake_reaches_verified_with_no_boundary_delta(self):
        fixture = HandshakeFixture(self.root)
        result = run_mo2_guard_handshake(
            self.root,
            fixture.containment_run_id,
            dependencies=fixture.dependencies(),
        )
        self.assertEqual("Verified", result.outcome)
        self.assertEqual(result.receipt.before, result.receipt.after)
        self.assertEqual([], result.game_changes)
        self.assertEqual([], result.manager_changes)
        self.assertEqual([str(fixture.mo2_executable)], result.programs_launched)

    def test_changed_protected_byte_is_recovery_required(self):
        fixture = HandshakeFixture(self.root, mutate_after_safe="profiles/ModLab - Play/modlist.txt")
        result = run_mo2_guard_handshake(self.root, fixture.containment_run_id, dependencies=fixture.dependencies())
        self.assertEqual("RecoveryRequired", result.outcome)
        self.assertIsNone(result.receipt)
```

Cover absent/unselected/drifted Skyrim checkpoint, non-Supported/wrong-mechanism containment decision, missing scenario evidence, invalid bootstrap/bridge receipt, running or incompletely observed MO2, request replay, wrong executable/version/profile/path, premature process exit, SafeToClose timeout, failed event, launch rejection, event gap/hash mismatch, changed Lab/Play/mod/download/Overwrite/game/config bytes, save metadata change, MO2 closure after valid SafeToClose, terminal replay, and recovery while the exact process is running/absent/ambiguous.

- [ ] **Step 2: Run and verify failure**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_bridge_handshake -v`

Expected: FAIL because handshake orchestration does not exist.

- [ ] **Step 3: Add retained Windows process identity without kill capability**

```python
@dataclass(frozen=True)
class ManagedProcessIdentity:
    pid: int
    creation_time: int
    executable_path: str

class ManagedProcess:
    identity: ManagedProcessIdentity
    def poll(self) -> int | None: ...
    def wait(self, timeout: float | None = None) -> int: ...
```

The concrete launcher uses `subprocess.Popen(args, cwd=..., env=..., shell=False)` and Windows `GetProcessTimes` on the retained process handle. The module exposes poll/wait/exact-absence inspection only—no terminate, kill, taskkill, or arbitrary command execution function.

- [ ] **Step 4: Implement exact protected-boundary evidence**

Capture stable two-equal-pass identities for the complete direct Lab profile, Play profile, existing `mods`, `downloads`, `overwrite`, and bounded game root, plus present/absent exact bytes for the allowlisted manager configuration files named by bootstrap policy. Reject every symlink/reparse point and every tree that changes during capture. Save handling calls `lstat` and direct child-count observation only in memory before/after; the receipt persists only the comparison `save_directory_unchanged=True|False`, never names, hashes, or content.

Reuse the validated `stable_tree_identity()` implementation from `modlab.validation.windows_junction` and its `TreeIdentity` contract; do not introduce a second tree-hash algorithm in this phase.

- [ ] **Step 5: Implement preflight, launch, SafeToClose, and finalization**

`run_mo2_guard_handshake()` must:

1. verify current Skyrim status is Matched and MO2 Lab/Play comparison is structurally Ready;
2. load the named containment run from the current workspace and revalidate its full decision/scenario/watch evidence as `Supported` for the exact mechanism;
3. load and reverify compatible bootstrap and bridge receipts plus source/target bundle bytes;
4. prove MO2 idle and capture stable before evidence;
5. create the job/request and persist `Prepared` before launch;
6. launch only exact `ModOrganizer.exe --profile "ModLab - Lab"` with cwd `app`, job `TEMP`/`TMP`, `PYTHONDONTWRITEBYTECODE=1`, and the two handoff variables;
7. persist PID/creation time and transition through `Mo2Starting`/`Mo2Active`;
8. poll immutable events until valid SafeToClose or a bounded five-minute timeout, reporting `Safe to close MO2.` through `progress` only after validation;
9. wait for the user to close MO2 normally and never send a close/kill signal;
10. verify exit code, request claim, event order/digests, no failed event, no successful external launch, no bytecode cache, and exact bridge bytes;
11. capture stable after evidence, require exact before/after equality, write/reload the handshake receipt, and transition to `Verified`.

A durable `LaunchRejected` event proves the veto worked but makes this handshake non-pristine and `RecoveryRequired`; veto behavior receives a separate disposable test. Premature close, timeout, process ambiguity, or any evidence delta is `RecoveryRequired`, never Passed.

- [ ] **Step 6: Implement interrupted-handshake recovery**

Recovery loads the exact request/journal/events. If the retained process identity is still present it refuses without competing. Once absent, a valid SafeToClose plus identical protected evidence may finalize the original receipt. Any missing/failed/ambiguous bridge evidence or protected delta remains `RecoveryRequired`; because Handshake authorizes no manager mutation, recovery never edits manager/game/profile/mod bytes and never guesses restoration.

- [ ] **Step 7: Run focused tests and commit**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_bridge_handshake -v`

Expected: PASS.

```powershell
git add modlab/platform modlab/workflows/skyrim/mo2_bridge_handshake.py tests/test_mo2_bridge_handshake.py
git commit -m "feat: verify guarded MO2 handshake boundary"
```

---

### Task 7: Plain-language CLI and machine-readable action reporting

**Files:**
- Create: `modlab/workflows/skyrim/mo2_bridge_rendering.py`
- Modify: `modlab/cli.py`
- Create: `tests/test_mo2_bridge_cli.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: Tasks 4 and 6 lifecycle results/refusals.
- Produces: `skyrim mo2 bridge setup|verify|uninstall|recover|handshake` text/JSON commands with stable exit codes.

- [ ] **Step 1: Write failing CLI tests**

```python
class Mo2BridgeCliTests(unittest.TestCase):
    def test_setup_verify_and_uninstall_report_exact_actions(self):
        code, value = invoke_json("setup", service=verified_lifecycle_service())
        self.assertEqual(0, code)
        self.assertEqual("Verified", value["outcome"])
        self.assertEqual([], value["downloadsPerformed"])
        self.assertEqual([], value["gameChangesPerformed"])

    def test_handshake_prints_safe_close_only_after_durable_signal(self):
        code, output = invoke_text("handshake", service=ordered_handshake_service())
        self.assertEqual(0, code)
        self.assertLess(output.index("Safe to close MO2"), output.index("Handshake verified"))
```

Also cover malformed job/run IDs as exit `2`; blocked/running/recovery-required as exit `3`; JSON stdout containing one object and no prompts; diagnostics/progress on stderr for JSON; verify being read-only; no implicit setup during handshake; and every result containing `pathsWritten`, `downloadsPerformed`, `installationActionsPerformed`, `managerChangesPerformed`, `gameChangesPerformed`, and `programsLaunched`.

- [ ] **Step 2: Run and verify failure**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_bridge_cli -v`

Expected: FAIL because commands/renderers do not exist.

- [ ] **Step 3: Add exact commands**

```text
python -B -m modlab skyrim mo2 bridge setup --workspace PATH [--format text|json]
python -B -m modlab skyrim mo2 bridge verify --workspace PATH [--format text|json]
python -B -m modlab skyrim mo2 bridge uninstall --workspace PATH [--format text|json]
python -B -m modlab skyrim mo2 bridge recover bridge-job:<32hex> --workspace PATH [--format text|json]
python -B -m modlab skyrim mo2 bridge handshake --containment-run containment-run:<32hex> --workspace PATH [--format text|json]
```

Exit `0` for Verified/AlreadyVerified/Uninstalled/Recovered or a completed passing read-only verify. Exit `2` for malformed arguments/IDs/schemas. Exit `3` for safe refusal, running process, drift, unknown target, failed verification, incomplete handshake, timeout, or RecoveryRequired. Expected failures are plain language, not tracebacks.

- [ ] **Step 4: Document the boundary**

README must state that setup installs only the ModLab Guard, normal MO2 launches remain passive, handshake opens no installer, the user closes MO2 only after SafeToClose, and `skyrim mod install` remains unavailable. Include uninstall/recover examples and the disposable-before-production validation rule.

- [ ] **Step 5: Run focused tests and commit**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_bridge_cli -v`

Expected: PASS.

```powershell
git add modlab/cli.py modlab/workflows/skyrim/mo2_bridge_rendering.py tests/test_mo2_bridge_cli.py README.md
git commit -m "feat: expose MO2 guard lifecycle commands"
```

---

### Task 8: Disposable exact-MO2 integration and visible handshake validation

**Files:**
- Create: `tests/test_mo2_bridge_windows_integration.py`
- Modify: `tests/support/mo2_bridge.py`

**Interfaces:**
- Consumes: exact retained MO2 archive, Steam root, existing containment fixture builder, Tasks 4-7, and computer control for visible MO2.
- Produces: automated disposable setup/uninstall/recovery coverage plus one immutable passing disposable handshake receipt.

- [ ] **Step 1: Write opt-in Windows integration tests**

```python
@unittest.skipUnless(
    os.name == "nt" and os.environ.get("MODLAB_MO2_ARCHIVE") and os.environ.get("MODLAB_STEAM_ROOT"),
    "real MO2 archive and Steam root were not supplied",
)
class Mo2BridgeWindowsIntegrationTests(unittest.TestCase):
    def test_exact_disposable_bridge_setup_verify_uninstall_and_recovery(self):
        fixture = prepare_real_bridge_fixture(Path(os.environ["MODLAB_MO2_ARCHIVE"]), Path(os.environ["MODLAB_STEAM_ROOT"]))
        setup = setup_mo2_guard(fixture.stage_workspace)
        self.assertEqual("Verified", setup.outcome)
        self.assertEqual("Passed", verify_mo2_guard(fixture.stage_workspace).health.value)
        self.assertEqual("Uninstalled", uninstall_mo2_guard(fixture.stage_workspace).outcome)
        self.assertEqual(fixture.protected_before, fixture.capture_protected())
```

The test also injects one interruption after activation and proves recovery restores the absent target, rejects bytecode/extra content, and leaves source/game/profiles/mods/downloads/Overwrite unchanged. It must run rather than skip when the two exact environment variables are supplied.

- [ ] **Step 2: Run all automated tests and the exact opt-in integration prerequisite**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest discover -s tests -v
$env:MODLAB_MO2_ARCHIVE = 'C:\Users\red\Desktop\Modlab\workspace\library\archives\e6\e6376efd87fd5ddd95aee959405e8f067afa526ea6c2c0c5aa03c5108bf4a815\payload.7z'
$env:MODLAB_STEAM_ROOT = 'C:\Users\red\Desktop\Steam'
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_bridge_windows_integration -v
$bridgeIntegrationExit = $LASTEXITCODE
Remove-Item Env:MODLAB_MO2_ARCHIVE
Remove-Item Env:MODLAB_STEAM_ROOT
if ($bridgeIntegrationExit -ne 0) { throw "bridge integration failed with exit $bridgeIntegrationExit" }
```

Expected: the native suite passes with declared opt-in skips, and the exact bridge integration runs and passes without skip.

- [ ] **Step 3: Prepare one fresh disposable exact MO2 instance**

Use the existing containment fixture builder beneath `workspace/runtime/validation/mo2-bridge/<validation-id>/`, retaining exact fixture/bootstrap lineage. Do not reuse or mutate historical containment-run directories. Capture source/stage/game/profile/mod/download/Overwrite identities before bridge setup.

- [ ] **Step 4: Deploy and verify the bridge in the disposable stage**

Run the lifecycle service against the disposable stage workspace. Verify the three exact target files, exact receipt reload, no target `__pycache__`, and unchanged protected evidence. Then perform uninstall and setup once more so rollback and repeatability are proven before the visible handshake.

- [ ] **Step 5: Run one visible disposable no-install handshake with computer control**

The main agent reads `computer-use` completely and announces that visible MO2 control is now required. Start the handshake in one retained foreground PTY. In MO2, do not open Downloads, install a mod, change profiles, toggle/reorder anything, edit settings, or launch a program. Wait until the CLI prints the durable `Safe to close MO2` message, close MO2 normally with the window close control, and let the same CLI process finalize.

Expected: one claimed request, exact event order through SafeToClose, no LaunchRejected/Failed event, exact `ModLab - Lab`, one MO2 process with `--profile "ModLab - Lab"`, no bytecode, no protected delta, and a verified handshake receipt. Any unexpected prompt/UI blocks validation; close normally only when safe and preserve the attempt as Incomplete/RecoveryRequired rather than clicking through.

- [ ] **Step 6: Commit automated integration coverage**

```powershell
git add tests/test_mo2_bridge_windows_integration.py tests/support/mo2_bridge.py
git commit -m "test: validate MO2 guard in disposable instance"
```

---

### Task 9: Production no-install handshake and sanitized verdict

**Files:**
- Create: `docs/validation/mo2-guard-bridge-handshake.md`

**Interfaces:**
- Consumes: passing Task 8 evidence, production workspace, supported containment run `containment-run:2c6220d4ceae48809653c18d5c74562b`, and computer control.
- Produces: exact production bridge/handshake receipts and a sanitized repository verdict; no mod-install authority beyond the next reviewed plan.

- [ ] **Step 1: Revalidate production prerequisites read-only**

Run exact artifact verification, Skyrim status, MO2 Lab-vs-Play comparison, bootstrap receipt verification, supported containment decision reload, bridge source inventory, managed-process absence, and pre-action protected identities. Abort on any drift or running/unknown process. Do not repair implicitly.

- [ ] **Step 2: Deploy and verify the production bridge**

Run:

```powershell
$pythonPath = 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$workspacePath = 'C:\Users\red\Desktop\Modlab\workspace'
& $pythonPath -B -m modlab skyrim mo2 bridge setup --workspace $workspacePath --format json
if ($LASTEXITCODE -ne 0) { throw "production bridge setup failed with exit $LASTEXITCODE" }
& $pythonPath -B -m modlab skyrim mo2 bridge verify --workspace $workspacePath --format json
if ($LASTEXITCODE -ne 0) { throw "production bridge verify failed with exit $LASTEXITCODE" }
```

Expected: only `app/plugins/modlab_guard/{__init__.py,plugin.py,protocol.py}`, lifecycle job documents, and one bridge receipt are written. Skyrim, profiles, mods, downloads, Overwrite, saves, and other manager bytes remain unchanged.

- [ ] **Step 3: Prove normal MO2 launch is passive in a disposable copy**

Before production handshake, use the disposable copy to launch MO2 once without either handoff variable. Verify the plug-in produces no request/claim/event/SafeToClose file, registers no guard action observable in logs, permits normal closure, creates no bridge bytecode, and leaves protected evidence unchanged. Do not perform this optional normal-launch check against production because it adds no production safety evidence.

- [ ] **Step 4: Run the production no-install handshake with computer control**

Start:

```powershell
& $pythonPath -B -m modlab skyrim mo2 bridge handshake `
    --containment-run 'containment-run:2c6220d4ceae48809653c18d5c74562b' `
    --workspace $workspacePath
if ($LASTEXITCODE -ne 0) { throw "production handshake failed with exit $LASTEXITCODE" }
```

Leave MO2 untouched. Do not install, change a profile, change load order, toggle mods/plugins, alter settings, or launch anything. Close MO2 normally only after the foreground command prints `Safe to close MO2.` Any unexpected window or state mismatch stops the run without improvisation.

- [ ] **Step 5: Revalidate exact production preservation**

Reload the handshake receipt and independently verify before/after equality for manager configuration, full Lab/Play profile trees, existing mods, downloads, Overwrite, bounded game, and in-memory-only save-directory metadata. Re-run Skyrim status, MO2 comparison, bootstrap/bridge receipt verification, and capability-decision verification. Confirm no `__pycache__` or undeclared plug-in entry and no process remains.

- [ ] **Step 6: Write the sanitized verdict**

Record exact bridge/handshake receipt IDs, containment decision ID, MO2 version, test totals/skips, disposable outcome, production outcome, and the precise paths intentionally written. Do not include usernames, absolute paths, raw logs, save names, or machine-only secrets. Use this conclusion only if every check passes:

```text
SUPPORTED — the exact ModLab Guard bundle was transactionally deployed and a one-use no-install handshake completed through MO2 2.5.2's UI/refresh lifecycle without changing the declared production boundary. Archive installation remains unavailable until the next reviewed Install/Analyze plan consumes these receipts.
```

Otherwise record `NOT SUPPORTED`, preserve evidence, recover only proven lifecycle state, and keep installation unavailable.

- [ ] **Step 7: Run final verification**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest discover -s tests -v
git diff --check
git status --short
```

Expected: all tests pass with only declared opt-in skips, diff check is silent, and status contains only the verdict document before commit.

- [ ] **Step 8: Commit**

```powershell
git add docs/validation/mo2-guard-bridge-handshake.md
git commit -m "test: record MO2 guard handshake verdict"
```

## Self-review Checklist

- The plan consumes the exact Supported containment evidence but does not reinterpret it as installation support.
- The bridge target is fixed by the managed workspace and exact bootstrap receipt; public commands accept no arbitrary executable or destination.
- The deployed bundle contains exactly three declared files and rejects bytecode/unknown entries.
- Normal MO2 launches are passive; guarded launches require an exact two-variable one-use handoff.
- The bridge clears handoff environment before child inheritance, claims once, registers veto before UI work, forces a refresh boundary, and publishes SafeToClose only after durable evidence.
- Every external launch is vetoed in Handshake; there is no analyzer exception yet.
- Setup/uninstall/recovery are crash-safe, receipt-bound, idle-only, and quarantine rather than delete.
- The host retains exact process identity, never kills MO2, and does not finalize from PID-only or malformed evidence.
- Before/after evidence covers manager configuration, Lab/Play, mods, downloads, Overwrite, bounded game, and in-memory-only save metadata.
- Disposable validation precedes production deployment; production work stops after a no-install handshake.
- Quick/Manual/FOMOD, Install/Analyze, libloot, conflict reporting, fixes, Keep/Undo, and real mods remain outside this plan.
- Final claims remain narrower than “the game runs perfectly.”
