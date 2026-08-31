# Verified MO2 Bootstrap and Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a verified, staged, create-only Skyrim MO2 bootstrap that can also adopt the existing compatible instance without changing it, retain immutable package linkage and recovery evidence, and expose a simple preview/apply/recover CLI.

**Architecture:** A strict curated release descriptor authorizes one exact MO2 archive. Pure evidence modules inspect Windows processes, Skyrim primary plug-ins, profile seed sources, the archive, and the fixed target; an immutable planner freezes those inputs. Apply revalidates the plan, stages and verifies package bytes, then either writes an adoption receipt without touching MO2 or atomically activates a newly generated portable instance through a durable recovery journal.

**Tech Stack:** Python 3.12 standard library, `dataclasses`, strict canonical JSON, `ctypes` Windows APIs, `subprocess` argument arrays, Windows `bsdtar`, `unittest`, existing Skyrim/MO2 scanners and projection.

**Spec:** `docs/superpowers/specs/2026-08-31-mo2-verified-bootstrap-design.md`

## Global Constraints

- Execute this plan in an isolated worktree created at execution time with `superpowers:using-git-worktrees`; use branch `feature/mo2-verified-bootstrap` and suggested path `C:\Users\red\Desktop\Modlab\.worktrees\mo2-verified-bootstrap`.
- Read the complete approved spec before Task 1 and treat it as authoritative.
- Python 3.12 standard library only; do not add package-manager or runtime dependencies.
- Windows 10 1809 or Windows 11 is the supported production platform.
- The only supported release in this increment is portable MO2 `2.5.2` / executable `2.5.2.0`.
- Authorize bytes only by the curated descriptor and exact SHA-256/size/listing identities; URLs and filenames are provenance, not trust.
- Never download, launch MO2/Skyrim, authenticate, install a mod, register NXM links, repair, merge, overwrite, or upgrade a nonempty MO2 tree from a public bootstrap command.
- Every ModLab write stays beneath the selected `workspace`; Steam, Skyrim, Documents, saves, co-saves, and the existing MO2 tree are read-only.
- The only external process used by production bootstrap is exact `C:\Windows\System32\tar.exe`, invoked with `shell=False` and an argument list.
- Report every extractor invocation by exact executable and purpose; “no MO2/game launch” must never be rendered as “no program launch” after `bsdtar` actually ran.
- Planning may observe running MO2 processes. Apply and recover require target `ModOrganizer.exe` and `nxmhandler.exe` processes to be absent; ModLab never terminates them.
- Saves are structurally unreachable: no directory enumeration beneath the Skyrim Documents game folder and no path containing a `saves` segment or ending in `.ess`/`.skse` enters a plan, journal, receipt, or operation.
- Create accepts only an absent target or the exact empty `workspace init` skeleton. Adopt accepts only a Ready existing instance and never writes beneath it.
- A changed or Unknown precondition fails closed before the first manager write. There is no `--force`.
- Plan, journal, receipt, and release JSON reject duplicate keys, extra/missing fields, unsafe paths, noncanonical ordering, invalid identities, and non-finite or wrong-typed values.
- Every user-visible result contains exact write/action arrays and narrow coverage claims.
- Use TDD for every task and commit each independently testable deliverable.
- Use the bundled interpreter exactly: `C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`, always with `-B`; the isolated shell does not provide `python` on PATH.
- Before implementation, run `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest discover -s tests -v` and confirm the current baseline (285 passing, 3 Windows symlink-permission skips at plan-writing time).

---

## File Map

### New production files

- `catalogue/tools/mo2-2.5.2.json` — curated exact release descriptor.
- `modlab/adapters/mo2/release.py` — strict descriptor model, loader, and bundled path.
- `modlab/adapters/mo2/bootstrap_model.py` — immutable plan, job, receipt, host, archive, and result value types.
- `modlab/adapters/mo2/bootstrap_serialization.py` — strict canonical JSON and content identities.
- `modlab/adapters/mo2/processes.py` — dependency-free Windows process-path inspection.
- `modlab/adapters/mo2/archive.py` — bsdtar identity, archive preflight, safe extraction, inventories, and package comparison.
- `modlab/adapters/skyrim/primary_plugins.py` — one shared strict Skyrim core/`Skyrim.ccc` policy.
- `modlab/adapters/mo2/bootstrap_config.py` — target classification and deterministic portable/profile seed bytes.
- `modlab/workflows/skyrim/mo2_bootstrap_store.py` — confined atomic plan, journal, and receipt storage.
- `modlab/workflows/skyrim/mo2_bootstrap.py` — plan/apply/recover orchestration.
- `modlab/workflows/skyrim/mo2_bootstrap_rendering.py` — honest text/JSON workflow rendering.

### Modified production files

- `modlab/workspace.py` — bootstrap plan/job/receipt paths.
- `modlab/adapters/mo2/scanner.py` — consume the shared primary plug-in policy without changing observations.
- `modlab/cli.py` — `skyrim mo2 setup` and `skyrim mo2 recover`.
- `README.md` — plain-language two-command flow, recovery, guarantees, and limits.

### New or extended tests

- `tests/test_mo2_release.py`
- `tests/test_mo2_bootstrap_serialization.py`
- `tests/test_mo2_processes.py`
- `tests/test_skyrim_primary_plugins.py`
- `tests/test_mo2_archive.py`
- `tests/test_mo2_bootstrap_config.py`
- `tests/test_mo2_bootstrap_store.py`
- `tests/test_mo2_bootstrap_planning.py`
- `tests/test_mo2_bootstrap_adopt.py`
- `tests/test_mo2_bootstrap_create.py`
- `tests/test_mo2_bootstrap_recovery.py`
- `tests/test_mo2_bootstrap_cli.py`
- `tests/test_mo2_bootstrap_windows_integration.py`
- `tests/support/mo2_bootstrap.py`
- `tests/test_workspace.py`
- existing scanner, projection, Skyrim workflow, and CLI suites for regression.

---

### Task 1: Curated MO2 2.5.2 release trust

**Files:**
- Create: `catalogue/tools/mo2-2.5.2.json`
- Create: `modlab/adapters/mo2/release.py`
- Create: `tests/test_mo2_release.py`

**Interfaces:**
- Consumes: repository-relative catalogue path and raw descriptor bytes.
- Produces: `ReleaseFileIdentity`, `Mo2ReleaseDescriptor`, `LoadedMo2Release`, `load_mo2_release_bytes(data: bytes, path: Path) -> LoadedMo2Release`, `load_mo2_release(path: Path) -> LoadedMo2Release`, and `bundled_mo2_252_path() -> Path`.

- [ ] **Step 1: Write failing exact-descriptor tests**

```python
class Mo2ReleaseTests(unittest.TestCase):
    def test_bundled_descriptor_has_observed_exact_identities(self):
        loaded = load_mo2_release(bundled_mo2_252_path())
        value = loaded.descriptor
        self.assertEqual('mo2.windows-portable.2.5.2', value.release_id)
        self.assertEqual(
            'e6376efd87fd5ddd95aee959405e8f067afa526ea6c2c0c5aa03c5108bf4a815',
            value.archive_sha256,
        )
        self.assertEqual(149660212, value.archive_size)
        self.assertEqual(1780, value.archive_entry_count)
        self.assertEqual(
            'c6eee6e0a9e80e3759c5bd75b701af4aa44d55859e71987f9b4bc1594057d0ed',
            value.archive_listing_sha256,
        )
        self.assertEqual(1626, value.package_file_count)
        self.assertEqual(408536933, value.extracted_size)
        self.assertEqual('2.5.2.0', value.executable.file_version)

    def test_duplicate_extra_unsorted_and_unsafe_fields_are_rejected(self):
        for mutation, reason in release_mutation_cases():
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(Mo2ReleaseFormatError, reason):
                    load_mo2_release_bytes(
                        mutation(valid_release_bytes()), Path('fixture.json')
                    )
```

In the same test file, `valid_release_bytes()` serializes the exact Step 3 object and `release_mutation_cases()` returns named mutations for a duplicate top-level key, extra field, unsorted extra-file array, unsafe relative path, boolean integer, wrong fixed version, and mismatched executable hash.

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_release -v`
Expected: FAIL because `modlab.adapters.mo2.release` does not exist.

- [ ] **Step 3: Add the exact curated descriptor**

Write canonical pretty JSON with these values:

```json
{
  "adapterId": "portable-mo2-skyrim",
  "allowedExtraFiles": [
    "categories.dat",
    "ModOrganizer.ini",
    "nexuscatmap.dat",
    "nxmhandler.log"
  ],
  "allowPluginPythonBytecode": true,
  "allowRootLogs": true,
  "archiveEntryCount": 1780,
  "archiveListingSha256": "c6eee6e0a9e80e3759c5bd75b701af4aa44d55859e71987f9b4bc1594057d0ed",
  "archiveName": "Mod.Organizer-2.5.2.7z",
  "archiveSha256": "e6376efd87fd5ddd95aee959405e8f067afa526ea6c2c0c5aa03c5108bf4a815",
  "archiveSize": 149660212,
  "executable": {
    "fileVersion": "2.5.2.0",
    "relativePath": "ModOrganizer.exe",
    "sha256": "442b354a8f34754da0048654c44d27f51628feba54ce46c3187cf58d6c43e622",
    "size": 5028352
  },
  "extractedSize": 408536933,
  "minimumFreeBytes": 1073741824,
  "mutablePackagePaths": [],
  "packageFileCount": 1626,
  "productVersion": "2.5.2",
  "releaseId": "mo2.windows-portable.2.5.2",
  "schemaVersion": 1,
  "sentinels": [
    {
      "relativePath": "loot/loot.dll",
      "sha256": "b21012f43e92ab5599b7be5d60550cc32806289a0a6bbb575c861b2d3d5a40cd",
      "size": 9333248
    },
    {
      "relativePath": "plugins/game_skyrimse.dll",
      "sha256": "5eaace8ec5e3f1e6dc6e85ffe22abdd30c99dfa414807e2d7e2ef242cc90a429",
      "size": 440320
    },
    {
      "relativePath": "usvfs_x64.dll",
      "sha256": "e2b766f418575021b9d350f384195ce6f23173169b37222cdef3d7fe5495f8b5",
      "size": 1854976
    }
  ],
  "sourceAssetUrl": "https://github.com/ModOrganizer2/modorganizer/releases/download/v2.5.2/Mod.Organizer-2.5.2.7z",
  "sourcePageUrl": "https://github.com/ModOrganizer2/modorganizer/releases/tag/v2.5.2",
  "supportedPlatform": "windows-x86_64"
}
```

- [ ] **Step 4: Implement the strict descriptor reader**

Use frozen dataclasses and exact field sets:

```python
@dataclass(frozen=True)
class ReleaseFileIdentity:
    relative_path: str
    sha256: str
    size: int
    file_version: str | None = None

@dataclass(frozen=True)
class Mo2ReleaseDescriptor:
    schema_version: int
    release_id: str
    adapter_id: str
    product_version: str
    supported_platform: str
    archive_name: str
    archive_sha256: str
    archive_size: int
    archive_entry_count: int
    archive_listing_sha256: str
    package_file_count: int
    extracted_size: int
    minimum_free_bytes: int
    source_page_url: str
    source_asset_url: str
    executable: ReleaseFileIdentity
    sentinels: tuple[ReleaseFileIdentity, ...]
    mutable_package_paths: tuple[str, ...]
    allowed_extra_files: tuple[str, ...]
    allow_root_logs: bool
    allow_plugin_python_bytecode: bool

@dataclass(frozen=True)
class LoadedMo2Release:
    descriptor: Mo2ReleaseDescriptor
    path: Path
    data: bytes
    sha256: str

def bundled_mo2_252_path() -> Path:
    return Path(__file__).resolve().parents[3] / 'catalogue' / 'tools' / 'mo2-2.5.2.json'
```

Reject unsafe POSIX paths, saves/co-saves, unordered/duplicate arrays, booleans masquerading as integers, descriptor values that do not equal the fixed adapter/platform/version, mismatched executable identity, and a descriptor file that changes between read and validation. Preserve exact source bytes and SHA-256 in `LoadedMo2Release`.

- [ ] **Step 5: Run tests**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_release -v`
Expected: PASS.

- [x] **Step 6: Commit**

```powershell
git add catalogue/tools/mo2-2.5.2.json modlab/adapters/mo2/release.py tests/test_mo2_release.py
git commit -m "feat: define trusted MO2 release"
```

---

### Task 2: Canonical bootstrap plan, journal, and receipt records

**Files:**
- Create: `modlab/adapters/mo2/bootstrap_model.py`
- Create: `modlab/adapters/mo2/bootstrap_serialization.py`
- Create: `tests/support/mo2_bootstrap.py`
- Create: `tests/test_mo2_bootstrap_serialization.py`

**Interfaces:**
- Consumes: `ReleaseFileIdentity` and existing `CheckState`.
- Produces: all bootstrap dataclasses/enums; `plan_to_bytes` / `plan_from_bytes` / `plan_id_for`; `journal_to_bytes` / `journal_from_bytes`; `receipt_to_bytes` / `receipt_from_bytes` / `receipt_id_for`.

- [ ] **Step 1: Write failing round-trip and rejection tests**

```python
class BootstrapSerializationTests(unittest.TestCase):
    def test_plan_identity_excludes_no_fields_except_its_derived_id(self):
        plan = make_plan_fixture()
        self.assertEqual(plan, plan_from_bytes(plan_to_bytes(plan)))
        self.assertEqual(plan.plan_id, plan_id_for(plan))
        changed = replace(plan, target=replace(plan.target, inventory_sha256='b' * 64))
        self.assertNotEqual(plan.plan_id, plan_id_for(changed))

    def test_job_identity_and_state_transition_fields_round_trip(self):
        journal = make_journal_fixture(state=BootstrapJobState.STAGED)
        self.assertEqual(journal, journal_from_bytes(journal_to_bytes(journal)))

    def test_receipt_identity_covers_package_extras_and_profiles(self):
        receipt = make_receipt_fixture()
        changed = replace(receipt, extra_entries=receipt.extra_entries + ('unknown.dll',))
        self.assertNotEqual(receipt.receipt_id, receipt_id_for(changed))

    def test_every_document_rejects_duplicates_saves_and_extra_fields(self):
        for data, loader in malformed_bootstrap_documents():
            with self.subTest(loader=loader.__name__):
                with self.assertRaises(BootstrapFormatError):
                    loader(data)
```

- [ ] **Step 2: Verify the tests fail**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_bootstrap_serialization -v`
Expected: FAIL on missing bootstrap modules.

- [ ] **Step 3: Define the immutable domain types**

Use these exact public enums and records:

```python
class BootstrapDisposition(StrEnum):
    CREATE = 'Create'
    ADOPT = 'Adopt'
    ALREADY_MANAGED = 'AlreadyManaged'
    BLOCKED = 'Blocked'

class BootstrapJobState(StrEnum):
    PLANNED = 'Planned'
    STAGING = 'Staging'
    STAGED = 'Staged'
    APPLYING = 'Applying'
    ACTIVATED = 'Activated'
    VERIFIED = 'Verified'
    RECOVERY_REQUIRED = 'RecoveryRequired'
    RECOVERED = 'Recovered'

class BootstrapReceiptMode(StrEnum):
    CREATED = 'Created'
    ADOPTED = 'Adopted'

@dataclass(frozen=True)
class FileIdentity:
    path: str
    sha256: str
    size: int

@dataclass(frozen=True)
class OptionalFileIdentity:
    path: str
    present: bool
    sha256: str | None
    size: int | None

@dataclass(frozen=True)
class ExtractorIdentity:
    executable: FileIdentity
    version: str

@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    image_name: str
    executable_path: str

@dataclass(frozen=True)
class ProcessObservation:
    complete: bool
    relevant: tuple[ProcessIdentity, ...]
    error: str | None

@dataclass(frozen=True)
class TargetSnapshot:
    kind: str
    root: str
    inventory_sha256: str
    entry_count: int

@dataclass(frozen=True)
class ProfileSeedEvidence:
    documents_root: str
    primary_plugins: tuple[str, ...]
    skyrim_ccc: OptionalFileIdentity
    ini_sources: tuple[OptionalFileIdentity, ...]

@dataclass(frozen=True)
class BootstrapFinding:
    state: CheckState
    code: str
    message: str
```

Define `BootstrapPlan` with the exact fields required by the spec: derived `plan_id`, disposition, workspace/Steam/game/final/staging-parent paths, Skyrim executable, environment SHA or null, baseline ID/status, descriptor path/source SHA/release ID, archive metadata and listing identities, extractor, target snapshot, process observation, profile seed evidence, and sorted write templates/findings/exclusions/coverage entries. Its evidence arrays are exact: `downloads=()`, `installations=()`, `manager_changes=()`, `game_changes=()`, and the three observed preflight `programs_launched` entries. Write templates use literal `{planId}`, `{jobId}`, and `{receiptId}` tokens so no field depends circularly on a derived identity.

Define `BootstrapJournal` with schema, job ID, plan ID, mode/disposition, state, exact stage/prior/final roots, prior target kind/inventory, timestamps, receipt ID or null, and error or null.

Define `BootstrapReceipt` with derived receipt ID, `VerifiedBootstrap` qualification, mode, plan/job/release/archive identities, Skyrim/MO2 executables, final manager root, package inventory SHA/count, mutable exclusions, observed extras, profile state SHA, extractor, verification time, `repairable=False`, and `upgrade_supported=False`.

Define the three public result records explicitly. `SetupPlanResult` contains `plan`, `plan_path`, and the common action fields. `SetupApplyResult` contains `outcome`, `plan_id`, `journal` (null only for AlreadyManaged), `receipt`, and the common action fields. `RecoveryResult` contains `outcome`, `journal`, optional `receipt`, and the common action fields. The common fields are sorted workspace-relative `paths_written`, `downloads`, `installations`, `manager_changes`, `game_changes`, and ordered `programs_launched`, all as immutable string tuples. No renderer reconstructs actions from filesystem state.

Add local canonical fixture builders in `tests/support/mo2_bootstrap.py`: `make_plan_fixture`, `make_journal_fixture`, `make_receipt_fixture`, and `malformed_bootstrap_documents`. Each builder supplies every field defined above with fixed `a`/`b` hashes, `C:\ModLab` paths, UTC timestamps, sorted tuples, no save paths, and recomputes the derived ID after applying keyword overrides.

- [ ] **Step 4: Implement strict canonical serialization**

Canonical bytes use UTF-8, sorted keys, compact separators, no NaN, and one trailing newline:

```python
def _canonical_bytes(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(',', ':'),
        )
        + '\n'
    ).encode('utf-8')

def plan_id_for(value: BootstrapPlan) -> str:
    body = plan_to_dict(value, include_identity=False)
    return 'bootstrap-plan-sha256:' + hashlib.sha256(_canonical_bytes(body)).hexdigest()

def receipt_id_for(value: BootstrapReceipt) -> str:
    body = receipt_to_dict(value, include_identity=False)
    return 'bootstrap-receipt-sha256:' + hashlib.sha256(_canonical_bytes(body)).hexdigest()
```

Require exact field sets at every nesting level. Sort and deduplicate identities case-insensitively where paths/names are Windows identities. Validate timestamps as UTC `YYYY-MM-DDTHH:MM:SSZ`, job IDs as `bootstrap-job:<32 lowercase hex>`, plan/receipt IDs against their recomputed bodies, and every path against save/co-save and traversal bans. Restrict write-template tokens to the three exact names above and substitute them only after their validated identities exist.

- [ ] **Step 5: Run tests**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_bootstrap_serialization -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add modlab/adapters/mo2/bootstrap_model.py modlab/adapters/mo2/bootstrap_serialization.py tests/support/mo2_bootstrap.py tests/test_mo2_bootstrap_serialization.py
git commit -m "feat: define MO2 bootstrap records"
```

---

### Task 3: Windows process and Documents evidence

**Files:**
- Create: `modlab/adapters/mo2/processes.py`
- Create: `tests/test_mo2_processes.py`

**Interfaces:**
- Consumes: fixed instance root and dependency-injected raw Windows process enumerator.
- Produces: `ProcessInspectionError`, `RawProcess`, `enumerate_windows_processes() -> tuple[RawProcess, ...]`, `inspect_mo2_processes(instance_root: Path, *, enumerator: Callable[[], tuple[RawProcess, ...]] = enumerate_windows_processes) -> ProcessObservation`, and `windows_documents_root() -> Path`.

- [ ] **Step 1: Write failing process-path tests**

```python
class Mo2ProcessTests(unittest.TestCase):
    def test_only_exact_target_executables_are_relevant(self):
        root = Path(r'C:\ModLab\workspace\tools\mo2\skyrim-se-ae')
        records = (
            RawProcess(10, 'ModOrganizer.exe', str(root / 'app' / 'ModOrganizer.exe')),
            RawProcess(11, 'nxmhandler.exe', str(root / 'app' / 'nxmhandler.exe')),
            RawProcess(12, 'ModOrganizer.exe', r'D:\Other\ModOrganizer.exe'),
        )
        result = inspect_mo2_processes(root, enumerator=lambda: records)
        self.assertTrue(result.complete)
        self.assertEqual((10, 11), tuple(item.pid for item in result.relevant))

    def test_unreadable_candidate_process_is_unknown(self):
        def fail():
            raise ProcessInspectionError('candidate path unavailable')
        result = inspect_mo2_processes(Path(r'C:\ModLab\workspace'), enumerator=fail)
        self.assertFalse(result.complete)
        self.assertEqual((), result.relevant)
        self.assertIn('unavailable', result.error)
```

- [ ] **Step 2: Verify failure**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_processes -v`
Expected: FAIL on missing module.

- [ ] **Step 3: Implement dependency-free Windows enumeration**

Use `CreateToolhelp32Snapshot` / `Process32FirstW` / `Process32NextW` for PID and image names, then `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` plus `QueryFullProcessImageNameW` for candidate paths. Close every handle in `finally`. A failure to resolve either candidate image name raises `ProcessInspectionError`; unrelated process-access failures are ignored.

```python
@dataclass(frozen=True)
class RawProcess:
    pid: int
    image_name: str
    executable_path: str

def inspect_mo2_processes(
    instance_root: Path,
    *,
    enumerator: Callable[[], tuple[RawProcess, ...]] = enumerate_windows_processes,
) -> ProcessObservation:
    expected = {
        _norm(instance_root / 'app' / 'ModOrganizer.exe'),
        _norm(instance_root / 'app' / 'nxmhandler.exe'),
    }
    try:
        records = enumerator()
    except ProcessInspectionError as error:
        return ProcessObservation(False, (), str(error))
    relevant = tuple(
        ProcessIdentity(item.pid, item.image_name, item.executable_path)
        for item in sorted(records, key=lambda value: value.pid)
        if item.image_name.casefold() in {'modorganizer.exe', 'nxmhandler.exe'}
        and _norm(Path(item.executable_path)) in expected
    )
    return ProcessObservation(True, relevant, None)
```

Resolve Documents with `SHGetKnownFolderPath(FOLDERID_Documents)`. Require an absolute existing direct directory; redirected Documents is allowed as an external read root, but each exact INI source is later required to be a direct regular file. Never enumerate the Documents game directory.

- [ ] **Step 4: Run tests**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_processes -v`
Expected: PASS, with Windows API tests mocked on non-Windows hosts.

- [ ] **Step 5: Commit**

```powershell
git add modlab/adapters/mo2/processes.py tests/test_mo2_processes.py
git commit -m "feat: observe MO2 host processes"
```

---

### Task 4: Safe archive preflight, extraction, and package comparison

**Files:**
- Create: `modlab/adapters/mo2/archive.py`
- Create: `tests/test_mo2_archive.py`

**Interfaces:**
- Consumes: `Mo2ReleaseDescriptor`, retained archive path, exact `tar.exe` path, and fresh staging root.
- Produces: `CommandRunner` protocol; `BsdtarArchive`; `ArchiveListing`; `PackageInventory`; `PackageComparison`; `observe_bsdtar`; `preflight_archive`; `extract_and_inventory`; `inventory_tree`; `compare_package_to_existing`.

- [ ] **Step 1: Write failing preflight and comparison tests**

```python
class Mo2ArchiveTests(unittest.TestCase):
    def test_preflight_uses_argument_arrays_and_accepts_exact_listing(self):
        runner = FakeRunner.for_listing(valid_names(), valid_types())
        result = preflight_archive(
            Path('payload.7z'), release_fixture(), Path(r'C:\Windows\System32\tar.exe'),
            runner=runner,
        )
        self.assertEqual(1780, result.entry_count)
        self.assertEqual(
            [
                [r'C:\Windows\System32\tar.exe', '-tf', 'payload.7z'],
                [r'C:\Windows\System32\tar.exe', '-tvf', 'payload.7z'],
            ],
            runner.calls,
        )

    def test_preflight_rejects_traversal_case_collision_and_links(self):
        for names, types, reason in unsafe_listing_cases():
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(Mo2ArchiveError, reason):
                    preflight_archive(
                        Path('payload.7z'), descriptor_for(names), Path('tar.exe'),
                        runner=FakeRunner.for_listing(names, types),
                    )

    def test_existing_comparison_requires_every_package_file(self):
        comparison = compare_package_to_existing(
            package_inventory_fixture(),
            existing_app_fixture(missing='usvfs_x64.dll'),
            release_fixture(),
        )
        self.assertFalse(comparison.compatible)
        self.assertEqual(('usvfs_x64.dll',), comparison.missing)
```

Extend `tests/support/mo2_bootstrap.py` with `FakeRunner`, `valid_names`, `valid_types`, `descriptor_for`, `package_inventory_fixture`, and `existing_app_fixture`. `FakeRunner` maps an exact argument tuple to fixed `CompletedProcess` stdout/stderr/return code and records every call as a list; the listing helpers generate 1780 safe entries and a matching descriptor identity rather than bypassing validation.

- [ ] **Step 2: Verify failure**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_archive -v`
Expected: FAIL on missing archive module.

- [ ] **Step 3: Implement extractor identity and preflight**

`CommandRunner.run(args: tuple[str, ...]) -> CompletedProcess[bytes]` must always call:

```python
subprocess.run(
    list(args),
    check=False,
    capture_output=True,
    shell=False,
    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
)
```

`observe_bsdtar` requires exact resolved `C:\Windows\System32\tar.exe`, hashes it, records size, and parses the first nonempty line of `tar --version`.

`preflight_archive` runs `-tf` and `-tvf`. Decode UTF-8 strictly. The name list and verbose list must have identical counts. Pair each name with the first mode character of the corresponding verbose row; only `-` and `d` are accepted. Reject absolute/UNC/drive-relative paths, `.`/`..`, empty segments, colons, control characters, trailing spaces/dots, reserved device components, save/co-save names, and case-insensitive duplicates. Canonical listing bytes are exact names joined with `\n` plus a final `\n`; require descriptor count and SHA-256.

- [ ] **Step 4: Implement confined extraction and inventory**

Before extraction require `shutil.disk_usage(staging_parent).free >= descriptor.minimum_free_bytes`. Require staging absent, create it directly, then run:

```python
runner.run((
    str(extractor_path),
    '-xf',
    str(archive_path),
    '-C',
    str(staging_root),
))
```

Walk with `os.scandir` and `follow_symlinks=False`. Reject reparse points, links, devices, streams, nonregular objects, unexpected entries, and any resolved path outside staging. Hash files in 1 MiB chunks. Require 1626 files, 408536933 bytes, the exact executable, and all three sentinels. Canonical inventory rows are `[relativePath, sha256, size]` sorted case-insensitively and SHA-256 hashed as compact JSON.

- [ ] **Step 5: Implement deep Adopt comparison**

For every package inventory row, require an exact same-path regular existing file with matching size/SHA-256. Classify extras only through these explicit rules:

```python
def _allowed_extra(path: PurePosixPath, release: Mo2ReleaseDescriptor) -> bool:
    text = path.as_posix()
    if text in release.allowed_extra_files:
        return True
    if release.allow_root_logs and len(path.parts) == 2:
        return path.parts[0].casefold() == 'logs' and path.suffix.casefold() == '.log'
    if release.allow_plugin_python_bytecode:
        return (
            len(path.parts) >= 3
            and path.parts[0].casefold() == 'plugins'
            and '__pycache__' in {part.casefold() for part in path.parts[1:-1]}
            and path.suffix.casefold() == '.pyc'
        )
    return False
```

Record every allowed extra. Any missing, modified, redirected, nonregular, duplicate, or unapproved extra makes `PackageComparison.compatible=False`.

- [ ] **Step 6: Run tests**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_archive -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add modlab/adapters/mo2/archive.py tests/test_mo2_archive.py
git commit -m "feat: verify MO2 archive packages"
```

---

### Task 5: Shared Skyrim primary policy and deterministic MO2 seeds

**Files:**
- Create: `modlab/adapters/skyrim/primary_plugins.py`
- Create: `modlab/adapters/mo2/bootstrap_config.py`
- Create: `tests/test_skyrim_primary_plugins.py`
- Create: `tests/test_mo2_bootstrap_config.py`
- Modify: `modlab/adapters/mo2/scanner.py`
- Modify: `modlab/adapters/mo2/readset.py`
- Modify: `modlab/adapters/mo2/profile.py`
- Modify: `modlab/adapters/mo2/bootstrap_serialization.py`
- Test: `tests/test_mo2_scanner.py`
- Test: `tests/test_mo2_projection.py`
- Test: `tests/test_mo2_readset.py`
- Test: `tests/test_mo2_profile.py`
- Test: `tests/test_mo2_bootstrap_serialization.py`

**Interfaces:**
- Consumes: game root, exact Documents root, workspace layout, release descriptor, and `Mo2ReadSet`.
- Produces: `SkyrimPrimaryPluginEvidence`; `observe_skyrim_primary_plugins`; `classify_mo2_target`; `observe_profile_seed`; `render_modorganizer_ini`; `render_profile_files`; `write_staged_configuration`.

- [ ] **Step 1: Write failing primary-policy tests**

```python
def test_primary_policy_is_core_then_exact_ccc_order(self):
    evidence = observe_skyrim_primary_plugins(game_root, read_set=Mo2ReadSet())
    self.assertEqual(
        (
            'Skyrim.esm', 'Update.esm', 'Dawnguard.esm',
            'HearthFires.esm', 'Dragonborn.esm',
            'ccBGSSSE001-Fish.esm',
        ),
        evidence.plugins,
    )

def test_primary_policy_blocks_missing_core_duplicate_and_nonregular_ccc_file(self):
    for fixture, reason in invalid_primary_policy_cases():
        with self.subTest(reason=reason):
            with self.assertRaisesRegex(SkyrimPrimaryPluginError, reason):
                observe_skyrim_primary_plugins(fixture.game_root, read_set=Mo2ReadSet())
```

Extend the shared support module with `invalid_primary_policy_cases` and `files_for_profile`. The first creates three concrete directory fixtures for a missing fixed master, a duplicate catalogue identity, and a present-but-nonregular catalogue entry; the second strips only the exact `profiles/<name>/` prefix and returns a sorted `(relative_path, bytes)` tuple. Add a separate positive case proving absent `Skyrim.ccc` catalogue entries are omitted while installed entries retain exact source order.

- [ ] **Step 2: Extract the scanner's policy into one shared module**

Move the fixed five-master tuple and strict `Skyrim.ccc` parsing into `primary_plugins.py`. Require all five fixed masters as direct top-level regular `Data` files. Treat `Skyrim.ccc` as a catalogue/order source: omit absent entries, require every present entry to be a direct regular top-level `Data` file, retain the installed subset in exact catalogue order, and reject duplicates case-insensitively. Reject unsafe Windows characters, reserved device stems, separators, controls, and trailing dots/spaces through one shared plug-in-name validator used by observation, profile parsing, seed validation, and bootstrap serialization. Observe plug-in presence through stable file metadata rather than reading whole master payloads. `SkyrimPrimaryPluginEvidence` owns its adapter-neutral `plugins`, `ccc_path`, `ccc_present`, `ccc_sha256`, and `ccc_size` fields; `bootstrap_config.py` converts those fields to `OptionalFileIdentity`, while `scanner.py` converts them to its existing `Mo2StateFileEvidence` without changing public serialized evidence.

- [ ] **Step 3: Run scanner and projection regression tests**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_skyrim_primary_plugins tests.test_mo2_readset tests.test_mo2_profile tests.test_mo2_bootstrap_serialization tests.test_mo2_scanner tests.test_mo2_projection -v`
Expected: PASS.

- [ ] **Step 4: Write failing target and seed tests**

```python
class Mo2BootstrapConfigTests(unittest.TestCase):
    def test_workspace_init_skeleton_is_create_and_unknown_file_is_blocked(self):
        layout = initialize_workspace(self.workspace)
        self.assertEqual('Empty', classify_mo2_target(layout).kind)
        (layout.skyrim_mo2 / 'unknown.txt').write_text('mine', encoding='utf-8')
        self.assertEqual('Blocked', classify_mo2_target(layout).kind)

    def test_rendered_profiles_are_equal_and_projection_ready(self):
        seed = observe_profile_seed(
            self.game_root, self.documents_root, read_set=Mo2ReadSet()
        )
        files = render_profile_files(seed)
        self.assertEqual(
            files_for_profile(files, 'ModLab - Lab'),
            files_for_profile(files, 'ModLab - Play'),
        )
        loadorder = files[
            PurePosixPath('profiles/ModLab - Lab/loadorder.txt')
        ].decode('utf-8')
        self.assertTrue(loadorder.startswith('# This file was automatically generated'))
        self.assertIn('Skyrim.esm\n', loadorder)
        self.assertNotIn('saves', '\n'.join(path.as_posix() for path in files).casefold())
```

- [ ] **Step 5: Implement target classification**

`classify_mo2_target(layout) -> TargetSnapshot` recognizes only:

- `Empty`: root absent, or exact empty `app/downloads/mods/profiles/overwrite` skeleton with no other entries;
- `Existing`: direct `app/ModOrganizer.exe` exists and the root has no redirection;
- `Blocked`: every other state.

Build the bounded inventory identity from direct entry kind/name plus file size/SHA where files are inspected. Do not create or remove a path.

- [ ] **Step 6: Implement exact seed observation and rendering**

Read only these exact paths:

```python
INI_NAMES = ('Skyrim.ini', 'SkyrimPrefs.ini', 'SkyrimCustom.ini')
game_ini_root = documents_root / 'My Games' / 'Skyrim Special Edition'
sources = tuple(
    observe_optional_regular_file(game_ini_root / name)
    for name in INI_NAMES
)
```

Never call `iterdir`, `glob`, `rglob`, or `os.scandir` on `game_ini_root`. A missing INI becomes a present empty staged file; a redirect or nonregular source blocks.

Render `ModOrganizer.ini` with the exact approved stable keys and QSettings path escaping. Render each profile with:

```python
PROFILE_SETTINGS = (
    b'[General]\n'
    b'LocalSaves=false\n'
    b'LocalSettings=true\n'
    b'AutomaticArchiveInvalidation=true\n'
)
LIST_HEADER = b'# This file was automatically generated by Mod Organizer.\n'
```

`modlist.txt`, `plugins.txt`, and `lockedorder.txt` equal `LIST_HEADER`; `archives.txt` is empty; `loadorder.txt` is `LIST_HEADER` followed by every observed installed primary plug-in in order. Copy exact INI source bytes or empty bytes. `write_staged_configuration` uses exclusive file creation beneath a validated fresh stage and returns the written `PackageInventory` delta.

- [ ] **Step 7: Run focused tests**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_bootstrap_config tests.test_skyrim_primary_plugins tests.test_mo2_readset tests.test_mo2_profile tests.test_mo2_bootstrap_serialization tests.test_mo2_scanner tests.test_mo2_projection -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add modlab/adapters/skyrim/primary_plugins.py modlab/adapters/mo2/bootstrap_config.py modlab/adapters/mo2/bootstrap_serialization.py modlab/adapters/mo2/profile.py modlab/adapters/mo2/readset.py modlab/adapters/mo2/scanner.py tests/test_skyrim_primary_plugins.py tests/test_mo2_bootstrap_config.py tests/test_mo2_bootstrap_serialization.py tests/test_mo2_profile.py tests/test_mo2_readset.py tests/test_mo2_scanner.py tests/test_mo2_projection.py
git commit -m "feat: render contained MO2 profiles"
```

---

### Task 6: Confined content-addressed bootstrap storage

**Files:**
- Modify: `modlab/workspace.py`
- Create: `modlab/workflows/skyrim/mo2_bootstrap_store.py`
- Create: `tests/test_mo2_bootstrap_store.py`
- Modify: `tests/test_workspace.py`

**Interfaces:**
- Consumes: canonical plan/journal/receipt bytes.
- Produces: extended `WorkspaceLayout`; `Mo2BootstrapStore` methods `write_plan`, `load_plan`, `create_job`, `load_job`, `transition_job`, `write_receipt`, `load_receipt`, `find_compatible_receipt`, and path helpers.

- [ ] **Step 1: Write failing workspace/storage tests**

```python
def test_bootstrap_paths_are_organized_under_workspace(self):
    layout = initialize_workspace(self.workspace)
    self.assertEqual(
        self.workspace.resolve() / 'runtime/jobs/mo2-bootstrap/plans',
        layout.mo2_bootstrap_plans,
    )
    self.assertEqual(
        self.workspace.resolve() / 'games/skyrim-se-ae/tool-installations/mo2',
        layout.mo2_bootstrap_receipts,
    )

def test_plan_is_content_addressed_idempotent_and_conflict_safe(self):
    stored = self.store.write_plan(make_plan_fixture())
    repeated = self.store.write_plan(make_plan_fixture())
    self.assertTrue(stored.changed)
    self.assertFalse(repeated.changed)
    stored.path.write_bytes(b'changed')
    with self.assertRaisesRegex(Mo2BootstrapStoreError, 'different bytes'):
        self.store.write_plan(make_plan_fixture())
```

- [ ] **Step 2: Extend `WorkspaceLayout`**

Add:

```python
mo2_bootstrap_jobs: Path
mo2_bootstrap_plans: Path
mo2_bootstrap_receipts: Path
```

Initialize only `runtime/jobs/mo2-bootstrap/plans` and `games/skyrim-se-ae/tool-installations/mo2`. Exact staging/prior directories are created only by `create_job` after apply authorization.

- [ ] **Step 3: Implement confined atomic writes**

Every store path is derived from a validated identity; never accept a raw relative path. Use an exclusive `.part` file beneath `mo2_bootstrap_jobs` with `flush` + `os.fsync`. Promote immutable plans and receipts with Windows atomic no-replace semantics; serialize journal compare-and-swap beneath a workspace-derived cross-session mutex and promote it with `os.replace`. Validate every existing ancestor with `lstat` and reject symlinks/reparse points/non-directories.

```python
def write_plan(self, plan: BootstrapPlan) -> StoredBootstrapDocument:
    data = plan_to_bytes(plan)
    digest = plan.plan_id.removeprefix('bootstrap-plan-sha256:')
    target = self.layout.mo2_bootstrap_plans / f'{digest}.json'
    return self._write_immutable(target, data, 'plan')
```

`create_job` derives a new UUID-backed `bootstrap-job:<32 hex>`, exact sibling stage root `tools/mo2/.skyrim-se-ae.modlab-stage-<job hex>`, and prior root beneath the job directory. It writes `Planned` before returning.

`transition_job` requires the stored journal bytes/hash and expected prior state, validates only the allowed transition table, writes atomically, and re-loads exact bytes. Create permits `Planned -> Staging -> Staged -> Applying -> Activated -> Verified`, failure transitions to `RecoveryRequired`, safe restoration to `Recovered`, and verified recovery through `RecoveryRequired -> Activated -> Verified`. Adopt permits `Planned -> Staging -> Staged -> Verified` plus `RecoveryRequired -> Recovered`. `write_receipt` is content-addressed and immutable. `find_compatible_receipt` returns only a fully verified parsed receipt or no result; malformed matching files raise.

- [ ] **Step 4: Add redirection, crash, and CAS tests**

Patch `os.replace` and file writes at each boundary. Assert no target document appears after pre-promotion failure, an intact promoted document survives a cleanup failure, wrong prior state blocks, duplicate/modified journal bytes block, and redirected plan/job/receipt ancestors block.

- [ ] **Step 5: Run tests**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_workspace tests.test_mo2_bootstrap_store -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add modlab/workspace.py modlab/workflows/skyrim/mo2_bootstrap_store.py tests/test_workspace.py tests/test_mo2_bootstrap_store.py
git commit -m "feat: retain MO2 bootstrap state"
```

---

### Task 7: Immutable setup planning

**Files:**
- Create: `modlab/workflows/skyrim/mo2_bootstrap.py`
- Modify: `tests/support/mo2_bootstrap.py`
- Create: `tests/test_mo2_bootstrap_planning.py`

**Interfaces:**
- Consumes: release/vault/archive/host/seed/target evidence; optional existing environment and baseline.
- Produces: `Mo2BootstrapError`, `Mo2BootstrapRefusal`, `SetupPlanResult`, and the exact `prepare_mo2_setup` API shown in Step 3.

- [x] **Step 1: Build a realistic bootstrap fixture**

Extend `tests/support/mo2_bootstrap.py` so it creates:

- a Steam `489830` manifest and `SkyrimSE.exe`;
- all five core `Data` masters and optional `Skyrim.ccc` entries;
- three exact Documents INI source files in a sibling fixture root, never a saves fixture;
- a tiny fake retained archive record with descriptor-consistent IDs;
- empty, ready-existing, receipt-covered, and conflicting target builders;
- fake release, extractor, archive, process, clock, UUID, version-reader, and disk-space dependencies.

The fixture exposes `tree_state(root)` that records directory/file kind and bytes without following links.

- [x] **Step 2: Write failing disposition and idempotency tests**

```python
class Mo2BootstrapPlanningTests(unittest.TestCase):
    def test_empty_target_plans_create_and_writes_only_plan(self):
        before_external = self.fixture.external_state()
        result = self.fixture.prepare()
        self.assertEqual(BootstrapDisposition.CREATE, result.plan.disposition)
        self.assertEqual((result.plan_path,), result.paths_written)
        self.assertEqual(before_external, self.fixture.external_state())

    def test_ready_existing_target_plans_adopt_even_when_process_close_is_required(self):
        self.fixture.make_ready_existing()
        self.fixture.processes = target_processes()
        result = self.fixture.prepare()
        self.assertEqual(BootstrapDisposition.ADOPT, result.plan.disposition)
        self.assertTrue(result.plan.processes.relevant)

    def test_same_inputs_return_same_plan_id_and_bytes(self):
        first = self.fixture.prepare()
        second = self.fixture.prepare()
        self.assertEqual(first.plan.plan_id, second.plan.plan_id)
        self.assertEqual(first.plan_path.read_bytes(), second.plan_path.read_bytes())
        self.assertEqual((), second.paths_written)
```

- [x] **Step 3: Implement Steam/environment selection**

`prepare_mo2_setup` signature:

```python
def prepare_mo2_setup(
    *,
    artifact_id: str,
    workspace_root: Path,
    steam_root: Path | None,
    release_path: Path = bundled_mo2_252_path(),
    extractor_path: Path = Path(r'C:\Windows\System32\tar.exe'),
    documents_root: Path | None = None,
    version_reader: Callable[[Path], str | None] = read_windows_file_version,
    process_inspector: Callable[[Path], ProcessObservation] = inspect_mo2_processes,
    command_runner: CommandRunner | None = None,
    free_space_reader: Callable[[Path], int] = free_bytes_at,
) -> SetupPlanResult:
```

`free_bytes_at(path)` returns `shutil.disk_usage(path).free`; tests inject a fixed integer. A null `command_runner` constructs the production `SubprocessCommandRunner`. All other callable defaults are named production functions, not lambdas or ambient globals.

If `environment.json` is absent, require explicit Steam root. If present, load it strictly; omitted Steam root inherits it and supplied Steam root must resolve identically. Discover Skyrim and require Ready executable/app `489830` evidence. For existing configured state with a selected baseline, call the read-only status service and block Drifted/Blocked; allow Matched or NoBaseline.

- [x] **Step 4: Implement complete evidence collection**

In order, with no writes before all evidence is coherent:

1. load descriptor and verify source remains unchanged;
2. get and verify vault artifact, exact hash/size/name/`.7z`;
3. observe extractor identity and archive listing;
4. discover Skyrim and primary plug-ins;
5. observe exact profile INI sources;
6. classify target;
7. inspect target processes;
8. for Existing, run current scanner/projection and require exact executable/release/paths/profiles/local-save policy;
9. inspect compatible receipt;
10. produce sorted findings, exclusions, coverage, and intended write templates.

Use these stable finding codes exactly:

```python
BOOTSTRAP_FINDING_CODES = {
    'release-unsupported', 'release-modified',
    'artifact-missing', 'artifact-modified', 'artifact-release-mismatch',
    'archive-listing-unsafe', 'archive-listing-mismatch',
    'extractor-missing', 'extractor-redirected', 'extractor-changed',
    'extractor-failed', 'disk-space-insufficient',
    'skyrim-discovery-blocked', 'environment-mismatch', 'baseline-drifted',
    'target-redirected', 'target-unknown-nonempty', 'target-changed',
    'existing-package-mismatch', 'mo2-process-running',
    'process-inspection-unknown', 'profile-seed-changed',
    'stage-collision', 'orphan-stage', 'plan-invalid', 'journal-invalid',
    'receipt-invalid', 'recovery-required', 'post-activation-not-ready',
}
```

Plan and receipt coverage maps are sorted tuples with exact values `managerArtifact=VerifiedPackageSubset`, `mutableManagerState=ExcludedRecorded`, `installedModPayloads=NotLinked`, `runtimeValidation=NotPerformed`, and `smokeTest=NotPerformed`.

Disposition is AlreadyManaged only with a matching verified receipt; otherwise Existing Ready is Adopt, Empty is Create, and any blocked finding forces Blocked.

- [x] **Step 5: Freeze and store the plan**

Construct a plan with no timestamp. `write_templates` contains stable workspace-relative templates such as `runtime/jobs/mo2-bootstrap/plans/{planId}.json` and `games/skyrim-se-ae/tool-installations/mo2/{receiptId}.json`; it never embeds the derived plan ID. Manager, game, download, and installation action tuples remain empty for preview. Derive `plan_id` from the complete body, write through `Mo2BootstrapStore`, and report only the actual newly written plan path in `SetupPlanResult.paths_written`. Report the exact preflight extractor invocations as `<tar path> [version]`, `<tar path> [list-names]`, and `<tar path> [list-types]`.

- [x] **Step 6: Add the full refusal matrix**

Test missing Steam root, conflicting environment root, invalid/mutated archive, descriptor mismatch, archive listing drift, extractor Unknown, low disk, missing master, invalid `Skyrim.ccc`, redirected INI, unknown nonempty target, existing scanner/projection Blocked, wrong executable, drifted baseline, malformed receipt, process-inspection Unknown, redirected workspace, and evidence changed during its stability re-read. Each case must preserve external tree bytes and write no plan when input safety cannot be represented; a coherent Blocked plan may be retained and exits 3 later.

- [x] **Step 7: Run tests**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_bootstrap_planning -v`
Expected: PASS.

- [x] **Step 8: Commit**

```powershell
git add modlab/workflows/skyrim/mo2_bootstrap.py tests/support/mo2_bootstrap.py tests/test_mo2_bootstrap_planning.py
git commit -m "feat: plan verified MO2 setup"
```

---

### Task 8: Deep-verified, zero-manager-write Adopt

**Files:**
- Modify: `modlab/workflows/skyrim/mo2_bootstrap.py`
- Create: `tests/test_mo2_bootstrap_adopt.py`

**Interfaces:**
- Consumes: stored Adopt plan and injected extractor/process/clock/UUID dependencies.
- Produces: `SetupApplyResult` and the exact `apply_mo2_setup` API below for Adopt/AlreadyManaged; Task 9 adds its Create branch without changing the signature.

```python
def apply_mo2_setup(
    plan_id: str,
    workspace_root: Path,
    *,
    release_path: Path = bundled_mo2_252_path(),
    extractor_path: Path = Path(r'C:\Windows\System32\tar.exe'),
    documents_root: Path | None = None,
    version_reader: Callable[[Path], str | None] = read_windows_file_version,
    process_inspector: Callable[[Path], ProcessObservation] = inspect_mo2_processes,
    command_runner: CommandRunner | None = None,
    free_space_reader: Callable[[Path], int] = free_bytes_at,
    clock: Callable[[], datetime] = utc_now,
    job_id_factory: Callable[[], str] = new_bootstrap_job_id,
) -> SetupApplyResult:
```

- [x] **Step 1: Write failing Adopt invariance tests**

```python
class Mo2BootstrapAdoptTests(unittest.TestCase):
    def test_adopt_links_package_without_changing_existing_manager(self):
        plan = self.fixture.plan_adopt()
        before = self.fixture.existing_mo2_tree_state()
        result = self.fixture.apply(plan.plan_id)
        self.assertEqual(BootstrapReceiptMode.ADOPTED, result.receipt.mode)
        self.assertEqual(before, self.fixture.existing_mo2_tree_state())
        self.assertEqual(1626, result.receipt.package_file_count)
        self.assertEqual(BootstrapJobState.VERIFIED, result.journal.state)
        self.assertEqual([], list(self.fixture.stage_parent.glob('.skyrim-se-ae.modlab-stage-*')))

    def test_running_target_process_refuses_before_job_or_stage(self):
        plan = self.fixture.plan_adopt(processes=target_processes())
        before = self.fixture.workspace_state()
        with self.assertRaisesRegex(Mo2BootstrapRefusal, 'running'):
            self.fixture.apply(plan.plan_id, current_processes=target_processes())
        self.assertEqual(before, self.fixture.workspace_state())
```

- [x] **Step 2: Implement apply revalidation**

Load and verify the stored plan. Reject Blocked. Re-observe every immutable precondition through the planner's pure evidence function and compare field-by-field while allowing only:

- the planned relevant-process tuple to transition from present to empty;
- an existing valid receipt to turn Adopt into AlreadyManaged.

Current process inspection must be complete and empty. Changed game/archive/listing/extractor/target/environment/baseline/seed/release evidence blocks before `create_job`.

- [x] **Step 3: Implement Adopt staging and deep comparison**

Create the journal in `Planned`, transition `Staging`, extract the archive to the exact job stage, inventory it, transition `Staged`, and compare all 1626 package files to the existing app. Require no packaged difference and only descriptor-allowed extras.

Before and after comparison, capture the existing manager's complete regular-file inventory and direct directory-entry identity. If it changes during verification, set `RecoveryRequired` with an error but never write beneath the manager.

- [x] **Step 4: Write and verify the adoption receipt**

Build `BootstrapReceipt(mode=ADOPTED)` from the exact comparison, current scanner/projection, archive/release/extractor, profile-state hash, allowed extras, and coverage exclusions. Write/reload the receipt, set journal `Verified` with receipt ID, then clean only the exact job stage after checking it remains a direct sibling with the recorded inventory.

`SetupApplyResult` reports only ModLab plan/job/receipt paths under `paths_written`. `manager_changes`, `game_changes`, `downloads`, and `installations` are empty. `programs_launched` records the exact revalidation and extraction `bsdtar` invocations; it never claims that no program ran.

For an Adopt apply, `programs_launched` is exactly `<tar path> [version]`, `<tar path> [list-names]`, `<tar path> [list-types]`, then `<tar path> [extract]`. AlreadyManaged performs the first three revalidation invocations but does not extract, create a job, or report a write.

- [x] **Step 5: Add deep-comparison and failure tests**

Cover one missing package file, one changed byte, one redirected file, unknown extra DLL, allowed root log, allowed generated Python bytecode, extractor failure, manager mutation during comparison, receipt-write failure, journal failure, cleanup failure, repeated apply, and a pre-existing valid receipt. Assert manager bytes remain exactly equal in every case.

- [x] **Step 6: Run tests**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_bootstrap_adopt -v`
Expected: PASS.

- [x] **Step 7: Commit**

```powershell
git add modlab/workflows/skyrim/mo2_bootstrap.py tests/test_mo2_bootstrap_adopt.py
git commit -m "feat: adopt verified MO2 package"
```

---

### Task 9: Staged Create and atomic activation

**Files:**
- Modify: `modlab/workflows/skyrim/mo2_bootstrap.py`
- Create: `tests/test_mo2_bootstrap_create.py`

**Interfaces:**
- Consumes: stored Create plan, package stage, seed bytes, fixed target.
- Produces: Create branch of `apply_mo2_setup` with `Created` receipt and verified Ready projection.

- [x] **Step 1: Write failing Create success test**

```python
def test_create_activates_exact_contained_ready_instance(self):
    plan = self.fixture.plan_create()
    result = self.fixture.apply(plan.plan_id)
    layout = workspace_layout(self.fixture.workspace)
    report = inspect_skyrim_mo2(
        layout.skyrim_mo2_app,
        self.fixture.game_root,
        workspace_root=self.fixture.workspace,
        version_reader=self.fixture.version_reader,
    )
    projection = project_mo2_state(report)
    self.assertEqual(Mo2Readiness.READY, projection.readiness)
    self.assertEqual(BootstrapReceiptMode.CREATED, result.receipt.mode)
    self.assertEqual((), projection.adapter_state.installed_mods)
    self.assertEqual((), projection.adapter_state.overwrite_entries)
    self.assertFalse(projection.adapter_state.lab.profile_local_saves)
    lab_files = tuple(
        (PurePosixPath(item.relative_path).name, item.sha256, item.size)
        for item in projection.adapter_state.lab.state_files
    )
    play_files = tuple(
        (PurePosixPath(item.relative_path).name, item.sha256, item.size)
        for item in projection.adapter_state.play.state_files
    )
    self.assertEqual(lab_files, play_files)
```

- [x] **Step 2: Implement staged instance assembly**

After common revalidation and empty-target proof:

1. create job and transition `Staging`;
2. extract package directly to `<stage>/app`;
3. create empty `downloads/mods/overwrite/webcache`;
4. write deterministic `ModOrganizer.ini` and both profiles;
5. re-inventory the whole stage;
6. verify executable, sentinels, config parser, profile parser, no installed mods/Overwrite, no redirects, and exact planned seed identities;
7. transition `Staged`.

Do not call `initialize_workspace` after stage construction because it could create paths in the final target.

- [x] **Step 3: Implement durable activation**

Transition the journal to `Applying` before the first rename. If the target skeleton exists, rename it to the journal's exact prior root beneath its job directory; if absent, record absent and do not invent it. Re-verify stage/target/prior identities, then `os.replace(stage, final_root)` and transition `Activated`.

Run `inspect_skyrim_mo2` and `project_mo2_state` from the final root. Require Ready, exact release executable, contained paths, both profiles, local saves false, identical planned initial profile bytes, empty mods, and empty Overwrite.

- [x] **Step 4: Write Created receipt and finalize**

Write/reload `BootstrapReceipt(mode=CREATED)`, transition `Verified`, and retain the exact prior absent/empty state until the job proves verified. Clean a preserved empty skeleton only by enumerating and removing known empty directories bottom-up; never use a recursive generic delete.

- [x] **Step 5: Add creation refusal and interruption tests**

Cover target changed after plan, target nonempty, stage collision, insufficient disk, extraction failure, generated config collision, profile source drift, failure preserving prior, failure activating stage, post-activation scanner Blocked, receipt failure, journal failure, and final verification byte drift. Assert Steam/game/Documents bytes never change and every pre-activation failure leaves the target exact prior state.

- [x] **Step 6: Run tests**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_bootstrap_create -v`
Expected: PASS.

- [x] **Step 7: Commit**

```powershell
git add modlab/workflows/skyrim/mo2_bootstrap.py tests/test_mo2_bootstrap_create.py
git commit -m "feat: create contained MO2 instance"
```

---

### Task 10: Explicit interrupted-job recovery

**Files:**
- Modify: `modlab/workflows/skyrim/mo2_bootstrap.py`
- Create: `tests/test_mo2_bootstrap_recovery.py`

**Interfaces:**
- Consumes: exact stored job ID and current target/process evidence.
- Produces: `RecoveryResult` and the exact `recover_mo2_setup` API below.

```python
def recover_mo2_setup(
    job_id: str,
    workspace_root: Path,
    *,
    release_path: Path = bundled_mo2_252_path(),
    extractor_path: Path = Path(r'C:\Windows\System32\tar.exe'),
    documents_root: Path | None = None,
    version_reader: Callable[[Path], str | None] = read_windows_file_version,
    process_inspector: Callable[[Path], ProcessObservation] = inspect_mo2_processes,
    command_runner: CommandRunner | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> RecoveryResult:
```

Recovery reports only the extractor invocations it actually performs. Safe restore-only branches launch nothing; branches that revalidate the archive before finalizing a receipt report `[version]`, `[list-names]`, and `[list-types]` in that order.

- [x] **Step 1: Write failing state-matrix recovery tests**

```python
class Mo2BootstrapRecoveryTests(unittest.TestCase):
    def test_activated_valid_target_is_finalized(self):
        job = self.fixture.interrupted_job(BootstrapJobState.ACTIVATED)
        result = self.fixture.recover(job.job_id)
        self.assertEqual(BootstrapJobState.VERIFIED, result.journal.state)
        self.assertIsNotNone(result.receipt)

    def test_applying_without_activation_restores_exact_empty_target(self):
        job = self.fixture.interrupted_job(
            BootstrapJobState.APPLYING, stage_present=True, final_present=False
        )
        result = self.fixture.recover(job.job_id)
        self.assertEqual(BootstrapJobState.RECOVERED, result.journal.state)
        self.assertEqual(self.fixture.initial_empty_tree, self.fixture.target_state())

    def test_unexpected_nonempty_target_remains_recovery_required(self):
        job = self.fixture.interrupted_job(BootstrapJobState.APPLYING)
        (self.fixture.final_root / 'unknown.txt').write_text('mine', encoding='utf-8')
        with self.assertRaisesRegex(Mo2BootstrapRefusal, 'unexpected'):
            self.fixture.recover(job.job_id)
        self.assertTrue((self.fixture.final_root / 'unknown.txt').is_file())
```

- [x] **Step 2: Implement fail-closed recovery**

Validate job ID, journal bytes, referenced plan, all confined roots, and current empty process observation. State behavior:

- `Verified` or `Recovered`: idempotently return verified state;
- `Planned` / `Staging` / `Staged`: target must still match planned prior; quarantine the exact recorded stage by rename, then `Recovered`;
- `Applying`: if final absent and prior preserved, restore prior; if final is exact activated inventory, continue the Activated branch; otherwise `RecoveryRequired`;
- `Activated`: if final validates Ready and exact, create/verify receipt and finalize; if it matches recorded activated bytes but fails readiness, move it to the job recovery area and restore prior; any unknown bytes remain untouched and `RecoveryRequired`;
- `RecoveryRequired`: re-evaluate the same evidence and take only one of the safe branches above.

Write `RecoveryRequired` before raising after any failure that follows `Applying`.

- [x] **Step 3: Test every crash boundary**

Inject failures before/after each durable state write and rename. Restart with a new store/service object and prove recovery from filesystem evidence alone. Cover changed journal/plan/receipt, running process, missing prior, modified stage, modified activated target, rename failure, receipt failure, cleanup failure, and repeated recovery.

- [x] **Step 4: Run tests**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_bootstrap_recovery -v`
Expected: PASS.

- [x] **Step 5: Commit**

```powershell
git add modlab/workflows/skyrim/mo2_bootstrap.py tests/test_mo2_bootstrap_recovery.py
git commit -m "feat: recover MO2 bootstrap jobs"
```

---

### Task 11: Simple CLI and honest result contracts

**Files:**
- Create: `modlab/workflows/skyrim/mo2_bootstrap_rendering.py`
- Modify: `modlab/cli.py`
- Create: `tests/test_mo2_bootstrap_cli.py`

**Interfaces:**
- Consumes: planning/apply/recovery services.
- Produces: `plan_result_to_dict/text`, `apply_result_to_dict/text`, `recovery_result_to_dict/text`, and CLI commands from the spec.

- [x] **Step 1: Write failing parser/contract tests**

```python
class Mo2BootstrapCliTests(unittest.TestCase):
    def run_parser(self, argv: list[str]) -> argparse.Namespace:
        return _parser().parse_args(argv)

    def test_preview_apply_and_recover_contracts(self):
        preview = self.run_cli([
            'skyrim', 'mo2', 'setup', '--artifact', self.artifact_id,
            '--steam-root', str(self.steam_root),
            '--workspace', str(self.workspace), '--format', 'json',
        ])
        self.assertEqual(0, preview.code)
        value = json.loads(preview.stdout)
        self.assertEqual('Create', value['mo2Setup']['outcome'])
        self.assertEqual([], value['downloadsPerformed'])
        self.assertEqual(
            [
                r'C:\Windows\System32\tar.exe [version]',
                r'C:\Windows\System32\tar.exe [list-names]',
                r'C:\Windows\System32\tar.exe [list-types]',
            ],
            value['programsLaunched'],
        )

    def test_artifact_and_apply_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            self.run_parser([
                'skyrim', 'mo2', 'setup', '--artifact', self.artifact_id,
                '--apply', 'bootstrap-plan-sha256:' + 'a' * 64,
            ])
```

The test module imports the private `_parser` only to test argparse rejection without dispatch. Its `run_cli` helper calls `main(argv, stdout, stderr)` with fresh `StringIO` objects and returns a small record containing `code`, `stdout`, and `stderr`.

- [x] **Step 2: Add the exact argparse tree**

```python
skyrim_mo2 = skyrim_commands.add_parser('mo2', help='set up contained portable MO2')
skyrim_mo2_commands = skyrim_mo2.add_subparsers(
    dest='skyrim_mo2_command', required=True
)
setup = skyrim_mo2_commands.add_parser('setup', help='preview or apply verified MO2 setup')
mode = setup.add_mutually_exclusive_group(required=True)
mode.add_argument('--artifact')
mode.add_argument('--apply', dest='plan_id')
setup.add_argument('--steam-root', type=Path)
setup.add_argument('--workspace', type=Path)
setup.add_argument('--format', choices=('text', 'json'), default='text')
recover = skyrim_mo2_commands.add_parser('recover', help='recover one interrupted MO2 setup')
recover.add_argument('job_id')
recover.add_argument('--workspace', type=Path)
recover.add_argument('--format', choices=('text', 'json'), default='text')
```

Reject `--steam-root` with `--apply` in dispatch as an invalid command (exit 2).

- [x] **Step 3: Implement exact JSON/text rendering**

Top-level JSON always contains:

```python
{
    'schemaVersion': 1,
    'mo2Setup': result_specific_object,
    'pathsWritten': list(result.paths_written),
    'downloadsPerformed': list(result.downloads),
    'installationActionsPerformed': list(result.installations),
    'managerChangesPerformed': list(result.manager_changes),
    'gameChangesPerformed': list(result.game_changes),
    'programsLaunched': list(result.programs_launched),
}
```

Preview may report only its plan path. Adopt must report only ModLab plan/job/receipt paths. Create reports exact staged/final manager files as manager changes and exactly `portable-mo2-create` as its installation action. Preview, Adopt, and Create all report the `bsdtar` invocations they actually performed; download/game arrays remain empty. Text begins with `Create`, `Adopt`, `Already managed`, `Blocked`, `Created`, `Adopted`, `Recovery required`, or `Recovered` and ends with precise no-action claims, including “MO2 and Skyrim were not launched” rather than “nothing was launched.”

Exit 0 for prepared non-Blocked plan, verified apply, AlreadyManaged, or completed recovery; exit 2 for invalid identifier/schema/unsupported release; exit 3 for coherent Blocked plans, safe refusals, changed preconditions, verification failure, or RecoveryRequired. Catch only `BootstrapFormatError` as invalid and `Mo2BootstrapRefusal` as safe refusal; unexpected exceptions remain visible to developers.

- [x] **Step 4: Add CLI failure and side-effect tests**

Cover text/JSON for all outcomes, fallback Steam root, invalid argument combinations, modified plan, running process, changed target, blocked plan, invalid job, recovery required, idempotent apply/recovery, and exception boundaries. For every refusal compare external tree state before/after and assert no traceback for expected errors.

- [x] **Step 5: Run tests**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_bootstrap_cli tests.test_cli -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add modlab/workflows/skyrim/mo2_bootstrap_rendering.py modlab/cli.py tests/test_mo2_bootstrap_cli.py
git commit -m "feat: expose verified MO2 setup"
```

---

### Task 12: Full regression, real archive integration, documentation, and live computer proof

**Files:**
- Create: `tests/test_mo2_bootstrap_windows_integration.py`
- Modify: `README.md`
- Create during live validation: `workspace/games/skyrim-se-ae/logs/mo2-bootstrap-validation-<UTC timestamp>.json`

**Interfaces:**
- Consumes: complete public CLI, retained real archive, real Skyrim installation, computer-control validation.
- Produces: automated regression evidence, live adoption receipt, disposable Create proof, concise validation log, README, and pushed commits.

- [x] **Step 1: Add opt-in real-archive integration test**

The test skips unless `MODLAB_MO2_ARCHIVE` points to the retained archive and `MODLAB_STEAM_ROOT` points to Steam. It imports the archive into a temporary workspace, uses real `C:\Windows\System32\tar.exe`, prepares/applies Create, and requires Ready projection.

```python
@unittest.skipUnless(
    os.environ.get('MODLAB_MO2_ARCHIVE') and os.environ.get('MODLAB_STEAM_ROOT'),
    'real MO2 archive and Steam root were not supplied',
)
def test_real_archive_creates_ready_disposable_instance(self):
    artifact = ArchiveVault(self.workspace).import_archive(
        Path(os.environ['MODLAB_MO2_ARCHIVE']),
        source_note='opt-in integration fixture',
    )
    planned = prepare_mo2_setup(
        artifact_id=artifact.artifact_id,
        workspace_root=self.workspace,
        steam_root=Path(os.environ['MODLAB_STEAM_ROOT']),
        documents_root=self.documents_root,
    )
    applied = apply_mo2_setup(planned.plan.plan_id, self.workspace)
    self.assertEqual(BootstrapReceiptMode.CREATED, applied.receipt.mode)
```

- [x] **Step 2: Run the complete automated suite**

Run: `& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest discover -s tests -v`
Expected: all tests PASS; only documented platform/opt-in skips remain.

- [x] **Step 3: Run the real-archive integration**

Set:

```powershell
$env:MODLAB_MO2_ARCHIVE = 'C:\Users\red\Desktop\Modlab\workspace\library\archives\e6\e6376efd87fd5ddd95aee959405e8f067afa526ea6c2c0c5aa03c5108bf4a815\payload.7z'
$env:MODLAB_STEAM_ROOT = 'C:\Users\red\Desktop\Steam'
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_bootstrap_windows_integration -v
```

Expected: PASS, with temporary workspace removed by `TemporaryDirectory` and no live ModLab/Steam/Skyrim changes.

- [x] **Step 4: Document the plain-language workflow**

Update README Current Build, Commands, Folder Organization, MO2 section, safety claims, recovery, and Build Sequence. Show:

```powershell
python -B -m modlab skyrim mo2 setup --artifact archive-sha256:<hash> --workspace .\workspace
python -B -m modlab skyrim mo2 setup --apply bootstrap-plan-sha256:<hash> --workspace .\workspace
python -B -m modlab skyrim mo2 recover bootstrap-job:<id> --workspace .\workspace
```

Explain Create vs Adopt vs Already managed vs Blocked in one sentence each. State that setup never downloads MO2, closes programs, edits an existing manager, reads saves, or launches MO2/a game; it does run the recorded Windows system extractor. State that the receipt links only the verified MO2 package subset.

- [x] **Step 5: Commit code/docs after automated proof**

```powershell
git add tests/test_mo2_bootstrap_windows_integration.py README.md
git commit -m "docs: document verified MO2 setup"
```

- [ ] **Step 6: Prepare live validation without touching GUI**

Re-run `git status --short`, `skyrim status --format json`, artifact verification, manager discovery, and process inspection. Capture in memory:

- repository HEAD and clean state;
- full existing MO2 app/downloads/mods/profiles/Overwrite regular-file identities;
- Skyrim executable and top-level Data identities;
- selected checkpoint identity and verification;
- exact `Documents\My Games\Skyrim Special Edition\Saves` regular `.ess`/`.skse` identities without persisting their names.

Do not proceed if baseline is not Matched, artifact/checkpoint is not Available, or observations are unstable.

- [ ] **Step 7: Pause and request the user close MO2**

Tell the user computer access is now required and ask them to close the live MO2 window. Do not terminate `ModOrganizer.exe` or `nxmhandler.exe`. Re-inspect process paths and continue only when both target processes are absent.

- [ ] **Step 8: Adopt the live existing manager**

Run preview and apply against `C:\Users\red\Desktop\Modlab\workspace`. Require preview `Adopt` and apply `Adopted`. Compare before/after existing MO2 trees byte-for-byte; only ModLab plan/job/receipt state may be new.

- [ ] **Step 9: Create and launch a disposable validation instance**

Create a nested workspace under:

```text
C:\Users\red\Desktop\Modlab\workspace\runtime\validation\mo2-bootstrap-<job-id>
```

Import the already-retained archive into that nested workspace, preview/apply Create with real Steam, and require scanner/projection Ready. Announce that `computer-use` now drives only the disposable `ModOrganizer.exe`, read that skill completely, then launch it through computer control.

Verify visually:

- no instance-selection or first-run wizard;
- Skyrim Special Edition is the managed game;
- `ModLab - Lab` is selected;
- both exact profiles exist;
- downloads/mods/profiles/Overwrite paths resolve beneath the disposable workspace;
- local saves are disabled;
- no live mods are visible from the real instance.

Close the disposable MO2 normally through its UI.

- [ ] **Step 10: Verify preservation and retain concise evidence**

Re-run scanner/projection on disposable and live instances. Recompute all in-memory before/after identities. Require the live app/downloads/mods/profiles/Overwrite, Skyrim, and save/co-save identities unchanged; expected new files are only bootstrap plan/job/receipt/validation evidence.

Write a strict JSON validation record under the live Skyrim logs directory containing UTC time, commit, plan/job/receipt IDs, disposable workspace ID, pass/fail findings, pre/post aggregate hashes, action arrays, and no save filenames/content.

After validating the absolute disposable root is exactly beneath `workspace/runtime/validation` and its receipt/journal prove it was test-created, remove only that disposable root with native PowerShell `Remove-Item -LiteralPath <exact-root> -Recurse`. Recheck the resolved target before deletion and never compute the delete target from an unverified environment variable or glob.

- [ ] **Step 11: Request code review and fix only verified findings**

Invoke `superpowers:requesting-code-review` for the complete feature diff. Address findings through `superpowers:receiving-code-review`, re-run focused and full tests, and repeat any affected live read-only checks. Do not normalize live MO2 to satisfy a test.

- [ ] **Step 12: Final verification and integration**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest discover -s tests -v
git diff --check
git status --short --branch
git log --oneline --decorate -12
```

Require a clean feature worktree and passing tests. Use `superpowers:verification-before-completion`, then `superpowers:finishing-a-development-branch` to integrate into `main`. Push `main` to `https://github.com/indigoxred/ModLab` and verify local `HEAD` equals `origin/main`.
