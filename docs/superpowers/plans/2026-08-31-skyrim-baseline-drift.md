# Skyrim Baseline and Drift Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add simple `skyrim configure`, `skyrim baseline create/use`, and `skyrim status` commands that retain exact intent, capture an honest immutable observed baseline, and explain live drift without changing Skyrim or MO2.

**Architecture:** Add a Skyrim workflow package above the existing game discovery, MO2 projection, recipe review, and generic checkpoint store. Strict configuration/source stores own only ModLab files; pure observation, evidence, and drift modules remain independent of writes. Workflow services orchestrate these units and the CLI only parses arguments and renders fixed result contracts.

**Tech Stack:** Python 3.12.13 standard library, frozen dataclasses, `hashlib`/`json`/`pathlib`/`datetime`, `argparse`, and `unittest` on Windows.

**Spec:** `docs/superpowers/specs/2026-08-31-skyrim-baseline-drift-design.md`

## Global Constraints

- Work in an isolated worktree created at execution time with `superpowers:using-git-worktrees`: `C:\Users\red\Desktop\Modlab\.worktrees\skyrim-baseline-drift` on branch `feature/skyrim-baseline-drift`.
- Use `C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe` and always pass `-B`.
- Use `C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe`; snippets use `git` as a readable abbreviation for this exact executable.
- Add no package or network dependency.
- Keep every runtime file beneath the selected ModLab workspace. Steam and Skyrim remain external read-only observations.
- The portable manager root is exactly `workspace/tools/mo2/skyrim-se-ae/app`; do not accept an arbitrary manager root.
- Profiles are exactly `ModLab - Lab` and `ModLab - Play`.
- Configuration requires a Ready MO2 projection, concrete Skyrim and MO2 executable byte identities, a stable MO2 read set, a review-ready recipe selection, and no contradictory observable target dimension.
- Target-environment values are intent. Never copy an unobserved target dimension such as `scriptExtender` into live evidence.
- Derive Skyrim `1.7.104.0` to compatibility runtime `1.7.104` with the existing function. Derive MO2 `2.5.2.0` to `2.5.2` only when the raw value is exactly four numeric components and the fourth is zero.
- Every checkpoint produced here has `captureKind=ObservedBaseline`, `qualification=Observed`, `promotionReady=false`, `restorable=false`, `foundationAssembly=NotVerified`, `runtimeValidation=NotPerformed`, `smokeTest=NotPerformed`, and `artifactCoverage=NotLinked`.
- Baseline `artifactIds` is empty in this increment; do not invent archive, installed, generated, configuration, root, or recipe artifact identities.
- `status` is completely read-only. Configure/capture/use may write only the exact ModLab files they report.
- Structurally reject every `saves` path segment and `.ess` or `.skse` file from new source, configuration, evidence, drift, and action structures.
- Do not download, extract, install, enable, disable, reorder, clean, patch, repair, launch, test in-game, promote, restore, or roll back anything.
- Preserve all 185 existing tests and public JSON/text contracts.
- Commit after every task only after its focused tests and named regressions pass.

## File Structure

- Modify `modlab/recipes/loading.py`: strictly reject duplicate/unknown source fields and expose validated raw source bytes plus SHA-256.
- Modify `modlab/workspace.py`: add a pure no-write layout function, content-addressed recipe/target-environment paths, and the environment configuration path.
- Create `modlab/workflows/__init__.py` and `modlab/workflows/skyrim/__init__.py`: package boundaries only.
- Create `modlab/workflows/skyrim/configuration.py`: frozen environment configuration model plus strict canonical serialization.
- Create `modlab/workflows/skyrim/store.py`: atomic configuration CAS and immutable source retention.
- Create `modlab/workflows/skyrim/observation.py`: combined Skyrim/MO2 observation and target/live dimension reconciliation.
- Modify `modlab/adapters/mo2/comparison_serialization.py`: expose strict public serializers for the already-defined observation/state substructures.
- Create `modlab/workflows/skyrim/evidence.py`: fixed observed-baseline evidence model and strict serializer.
- Create `modlab/workflows/skyrim/capture.py`: checkpoint construction, parent validation, storage, and pointer selection.
- Create `modlab/workflows/skyrim/drift.py`: pure current-versus-baseline domain comparison with bounded details.
- Create `modlab/workflows/skyrim/service.py`: configure, capture, select, and status orchestration.
- Create `modlab/workflows/skyrim/serialization.py`: stable command result JSON and concise text.
- Modify `modlab/cli.py`: expose the four Skyrim workflow commands and stable exits.
- Modify `README.md`: document workflow, written files, outcomes, and non-claims.
- Create `tests/support/__init__.py` and `tests/support/skyrim_workflow.py`: realistic temporary Steam/MO2 fixtures shared only by new tests.
- Create `tests/test_recipe_sources.py`, `tests/test_skyrim_environment_configuration.py`, `tests/test_skyrim_environment_store.py`, `tests/test_skyrim_observation.py`, `tests/test_skyrim_baseline_evidence.py`, `tests/test_skyrim_baseline_capture.py`, `tests/test_skyrim_drift.py`, `tests/test_skyrim_service.py`, `tests/test_skyrim_workflow_serialization.py`, and `tests/test_skyrim_workflow_cli.py`.
- Modify `tests/test_workspace.py`: assert the documented new directories.
- Modify `tests/test_loading.py`: retain recipe-loader regressions after strictness changes.

---

### Task 1: Strict, Byte-Identified Recipe and Target-Environment Sources

**Files:**
- Modify: `modlab/recipes/loading.py`
- Create: `tests/test_recipe_sources.py`
- Modify: `tests/test_loading.py`

**Interfaces:**
- Produces: `LoadedRecipeSource(path: Path, data: bytes, sha256: str, recipe: FoundationRecipe)`.
- Produces: `LoadedEnvironmentSource(path: Path, data: bytes, sha256: str, environment: EnvironmentEvidence)`.
- Produces: `load_recipe_source(path: Path) -> LoadedRecipeSource` and `load_environment_source(path: Path) -> LoadedEnvironmentSource`.
- Preserves: `load_recipe(path)` and `load_environment(path)` return their existing models by delegating to the new source loaders.

- [ ] **Step 1: Write failing strict-source tests**

Create `tests/test_recipe_sources.py` with valid minimal recipe/environment helpers and these cases:

```python
class RecipeSourceTests(unittest.TestCase):
    def test_loads_exact_bytes_and_sha256(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "recipe.json")
            data = json.dumps(valid_recipe(), indent=2).encode("utf-8")
            path.write_bytes(data)

            source = load_recipe_source(path)

            self.assertEqual(data, source.data)
            self.assertEqual(hashlib.sha256(data).hexdigest(), source.sha256)
            self.assertEqual("skyrim.test", source.recipe.recipe_id)

    def test_rejects_duplicate_and_unknown_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            duplicate = Path(directory, "duplicate.json")
            duplicate.write_text(
                '{"schemaVersion":1,"schemaVersion":1}', encoding="utf-8"
            )
            with self.assertRaisesRegex(RecipeFormatError, "duplicate JSON key"):
                load_recipe_source(duplicate)

            value = valid_recipe()
            value["unexpected"] = True
            unknown = Path(directory, "unknown.json")
            unknown.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(RecipeFormatError, "fields differ"):
                load_recipe_source(unknown)

    def test_environment_source_is_also_strict_and_byte_identified(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "environment.json")
            data = b'{"schemaVersion":1,"environmentId":"skyrim.test","dimensions":{"executableRuntime":"1.7.104"}}'
            path.write_bytes(data)

            source = load_environment_source(path)

            self.assertEqual(data, source.data)
            self.assertEqual("skyrim.test", source.environment.environment_id)
```

Add subtests for unknown target, component, constraint, environment, and optional component field spellings. Retain tests proving `version`, `archiveSha256`, and `installedTreeSha256` remain optional.

- [ ] **Step 2: Run the focused tests and confirm red**

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_recipe_sources tests.test_loading -v
```

Expected: FAIL because source loaders do not exist and duplicate/unknown fields are currently accepted.

- [ ] **Step 3: Implement strict source loading**

Add frozen source dataclasses, exact field sets, duplicate-key parsing, and one raw-byte loader:

```python
@dataclass(frozen=True)
class LoadedRecipeSource:
    path: Path
    data: bytes
    sha256: str
    recipe: FoundationRecipe

@dataclass(frozen=True)
class LoadedEnvironmentSource:
    path: Path
    data: bytes
    sha256: str
    environment: EnvironmentEvidence

_RECIPE_FIELDS = {
    "schemaVersion", "recipeId", "revision", "displayName", "maturity",
    "target", "researchedAt", "components",
}
_TARGET_FIELDS = {"game", "edition", "distribution", "engineLane", "adapter"}
_COMPONENT_REQUIRED_FIELDS = {
    "componentId", "displayName", "importance", "defaultSelected", "role",
    "deployment", "requires", "incompatibleWith", "constraints", "rationale",
    "sources",
}
_COMPONENT_OPTIONAL_FIELDS = {"version", "archiveSha256", "installedTreeSha256"}
_CONSTRAINT_FIELDS = {"dimension", "allowedValues", "reason"}
_ENVIRONMENT_FIELDS = {"schemaVersion", "environmentId", "dimensions"}

def _without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RecipeFormatError(f"duplicate JSON key: {key}")
        result[key] = value
    return result

def _exact_fields(value: Mapping[str, object], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise RecipeFormatError(
            f"{label} fields differ: missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )
```

Validate exact fields before existing value parsing. Component expected fields are required fields union only the optional fields actually present. Decode `data` with `text = data.decode("utf-8")`, call `json.loads(text, object_pairs_hook=_without_duplicate_keys)`, and compute SHA-256 over the original bytes. Reject non-regular or redirected source paths before reading.

Implement delegating compatibility functions:

```python
def load_recipe(path: Path) -> FoundationRecipe:
    return load_recipe_source(path).recipe

def load_environment(path: Path) -> EnvironmentEvidence:
    return load_environment_source(path).environment
```

- [ ] **Step 4: Run recipe tests**

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_recipe_sources tests.test_loading tests.test_checking tests.test_reviewing tests.test_catalogue -v
```

Expected: PASS.

- [ ] **Step 5: Commit strict sources**

```powershell
git add modlab/recipes/loading.py tests/test_recipe_sources.py tests/test_loading.py
git commit -m "fix: identify recipe sources strictly"
```

---

### Task 2: Canonical Skyrim Environment Configuration

**Files:**
- Create: `modlab/workflows/__init__.py`
- Create: `modlab/workflows/skyrim/__init__.py`
- Create: `modlab/workflows/skyrim/configuration.py`
- Create: `tests/test_skyrim_environment_configuration.py`

**Interfaces:**
- Produces the frozen models `StoredSourceReference`, `ManagerRegistration`, `RecipeIntent`, `TargetEnvironmentIntent`, `SkyrimEnvironmentConfiguration`, and `ConfigurationChange`.
- Produces: `configuration_to_dict(value) -> dict[str, object]`, `configuration_from_dict(value) -> SkyrimEnvironmentConfiguration`, `configuration_from_bytes(data: bytes) -> SkyrimEnvironmentConfiguration`, and `configuration_bytes(value) -> bytes`.
- Produces: `configuration_changes(before, after) -> tuple[ConfigurationChange, ...]` with stable dotted field names and bounded scalar/identity values.
- Produces: `SkyrimConfigurationFormatError(ValueError)`.

- [ ] **Step 1: Write failing canonical round-trip and rejection tests**

Create a `valid_configuration()` fixture and tests:

```python
def test_configuration_round_trip_is_canonical(self):
    configuration = valid_configuration()
    restored = configuration_from_dict(configuration_to_dict(configuration))
    self.assertEqual(configuration, restored)
    self.assertEqual(
        configuration_bytes(configuration),
        configuration_bytes(restored),
    )

def test_rejects_unknown_fields_unsafe_relative_paths_and_save_paths(self):
    cases = []
    value = configuration_to_dict(valid_configuration())
    cases.append({**value, "unexpected": True})
    escaped = copy.deepcopy(value)
    escaped["manager"]["root"] = "../outside"
    cases.append(escaped)
    save_path = copy.deepcopy(value)
    save_path["recipe"]["storedPath"] = "games/skyrim-se-ae/saves/recipe.json"
    cases.append(save_path)
    for case in cases:
        with self.subTest(case=case):
            with self.assertRaises(SkyrimConfigurationFormatError):
                configuration_from_dict(case)

def test_baseline_id_is_null_or_exact_checkpoint_identity(self):
    value = configuration_to_dict(valid_configuration())
    value["baselineCheckpointId"] = "checkpoint-sha256:" + "a" * 64
    self.assertIsNotNone(configuration_from_dict(value).baseline_checkpoint_id)
    value["baselineCheckpointId"] = "latest"
    with self.assertRaisesRegex(SkyrimConfigurationFormatError, "baselineCheckpointId"):
        configuration_from_dict(value)

def test_configuration_changes_names_every_changed_logical_field(self):
    before = valid_configuration()
    after = replace(
        before,
        steam_root=r"D:\Steam",
        recipe=replace(before.recipe, selected=("core", "skyui")),
    )
    changes = configuration_changes(before, after)
    self.assertEqual(
        ("recipe.selected", "steamRoot"),
        tuple(item.field for item in changes),
    )
```

Add cases for wrong game/adapter/schema, unsorted or duplicate selections/dimensions, absolute workspace-relative paths, backslashes, drive prefixes, `.ess`, `.skse`, malformed SHA-256, unsafe IDs, inconsistent selected/omitted overlap, and a recipe identity of `Incomplete`.

- [ ] **Step 2: Run and confirm red**

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_skyrim_environment_configuration -v
```

Expected: FAIL because the workflow package does not exist.

- [ ] **Step 3: Implement the exact model and serializer**

Use these model shapes:

```python
@dataclass(frozen=True)
class StoredSourceReference:
    source_sha256: str
    stored_path: str

@dataclass(frozen=True)
class ManagerRegistration:
    adapter_id: str
    root: str

@dataclass(frozen=True)
class RecipeIntent:
    recipe_id: str
    revision: str
    maturity: RecipeMaturity
    identity: RecipeIdentity
    source: StoredSourceReference
    selected: tuple[str, ...]
    omitted: tuple[str, ...]

@dataclass(frozen=True)
class TargetEnvironmentIntent:
    environment_id: str
    source: StoredSourceReference
    dimensions: tuple[tuple[str, str], ...]

@dataclass(frozen=True)
class SkyrimEnvironmentConfiguration:
    schema_version: int
    game_key: str
    environment_id: str
    lineage_id: str
    steam_root: str
    game_root: str
    manager: ManagerRegistration
    recipe: RecipeIntent
    target_environment: TargetEnvironmentIntent
    baseline_checkpoint_id: str | None

@dataclass(frozen=True)
class ConfigurationChange:
    field: str
    before: str | bool | None | tuple[str, ...]
    after: str | bool | None | tuple[str, ...]
```

Require exact camelCase JSON fields from the spec. Canonical bytes use UTF-8, `ensure_ascii=False`, `sort_keys=True`, `separators=(",", ":")`, and one trailing newline. Validate Windows absolute external paths with `PureWindowsPath`; validate workspace-relative paths with `PurePosixPath`, forbidding empty/dot/dot-dot parts, drive/root syntax, backslashes, `saves`, `.ess`, and `.skse`.

`configuration_from_bytes` decodes UTF-8 and uses an object-pairs hook that raises `SkyrimConfigurationFormatError` on every duplicate key before delegating to `configuration_from_dict`.

`configuration_changes` flattens only the fixed configuration schema, never arbitrary JSON. It reports sorted dotted names and bounds selected/omitted/dimension sequences to count plus a seven-entry first-divergence window.

- [ ] **Step 4: Run the configuration tests**

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_skyrim_environment_configuration -v
```

Expected: PASS.

- [ ] **Step 5: Commit canonical configuration**

```powershell
git add modlab/workflows tests/test_skyrim_environment_configuration.py
git commit -m "feat: model Skyrim environment configuration"
```

---

### Task 3: Atomic Environment Store and Immutable Source Retention

**Files:**
- Modify: `modlab/workspace.py`
- Modify: `tests/test_workspace.py`
- Create: `modlab/workflows/skyrim/store.py`
- Create: `tests/test_skyrim_environment_store.py`

**Interfaces:**
- Produces: `ConfigurationSnapshot(configuration, sha256, data, path)`.
- Produces: `ConfigurationWrite(configuration, snapshot, changed, paths_written)`.
- Produces: `SourceRetention(reference, changed, path)` so callers report only a source file actually created in this invocation.
- Produces: pure `workspace_layout(root: Path) -> WorkspaceLayout`; `initialize_workspace` delegates to it only after creating the documented directories.
- Produces: `SkyrimEnvironmentStore.load()`, `retain_recipe(source)`, `retain_target_environment(source)`, `write(configuration, *, replace)`, and `compare_and_swap(expected_sha256, configuration)`.
- Produces: `SkyrimEnvironmentNotConfiguredError`, `SkyrimEnvironmentStoreError`, and `SkyrimEnvironmentConflictError(message: str, changes: tuple[ConfigurationChange, ...] = ())`.

- [ ] **Step 1: Add failing workspace and store tests**

Extend `EXPECTED_RELATIVE_DIRECTORIES` with:

```python
"games/skyrim-se-ae/recipes",
"games/skyrim-se-ae/target-environments",
```

Create store tests covering:

```python
def test_retains_exact_recipe_bytes_content_addressed(self):
    source = loaded_recipe_source(self.root)
    stored = self.store.retain_recipe(source)
    target = self.workspace / stored.reference.stored_path
    self.assertEqual(source.data, target.read_bytes())
    self.assertEqual(source.sha256, stored.reference.source_sha256)
    self.assertTrue(stored.changed)
    duplicate = self.store.retain_recipe(source)
    self.assertFalse(duplicate.changed)

def test_identical_configuration_is_idempotent_but_change_requires_replace(self):
    configuration = valid_configuration()
    first = self.store.write(configuration, replace=False)
    second = self.store.write(configuration, replace=False)
    self.assertTrue(first.changed)
    self.assertFalse(second.changed)
    changed = replace(configuration, steam_root=r"D:\Steam")
    with self.assertRaises(SkyrimEnvironmentConflictError):
        self.store.write(changed, replace=False)

def test_compare_and_swap_refuses_intervening_change(self):
    snapshot = self.store.write(valid_configuration(), replace=False).snapshot
    intervening = replace(snapshot.configuration, lineage_id="other-lineage")
    self.store.write(intervening, replace=True)
    desired = replace(snapshot.configuration, baseline_checkpoint_id=CHECKPOINT_ID)
    with self.assertRaisesRegex(SkyrimEnvironmentConflictError, "changed"):
        self.store.compare_and_swap(snapshot.sha256, desired)

def test_pure_workspace_layout_creates_nothing(self):
    root = Path(self.directory, "absent-workspace")
    layout = workspace_layout(root)
    self.assertEqual(root.resolve(), layout.root)
    self.assertFalse(root.exists())
```

Add tests for invalid existing JSON never overwritten, changed configuration exposing exact `ConfigurationChange` values before replacement, redirected source/target rejection, mismatched bytes in a content-addressed target, staging cleanup under injected `os.replace` failures, same-volume job paths, strict re-read after write, and source paths resolving beneath the workspace.

- [ ] **Step 2: Run and confirm red**

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_workspace tests.test_skyrim_environment_store -v
```

Expected: FAIL because the layout/store fields and module do not exist.

- [ ] **Step 3: Extend the workspace layout**

Split path calculation from directory creation and add directories/fields:

```python
def workspace_layout(root: Path) -> WorkspaceLayout:
    resolved = Path(root).expanduser().resolve()
    skyrim = resolved / "games" / "skyrim-se-ae"
    skyrim_mo2 = resolved / "tools" / "mo2" / "skyrim-se-ae"
    return WorkspaceLayout(
        root=resolved,
        inbox=resolved / "inbox",
        archives=resolved / "library" / "archives",
        metadata=resolved / "library" / "metadata",
        quarantine=resolved / "library" / "quarantine",
        games=resolved / "games",
        skyrim=skyrim,
        stable_environment=skyrim / "environments" / "stable",
        candidate_environment=skyrim / "environments" / "candidate",
        checkpoints=skyrim / "checkpoints",
        generated=skyrim / "generated",
        logs=skyrim / "logs",
        tools=resolved / "tools",
        skyrim_mo2=skyrim_mo2,
        skyrim_mo2_app=skyrim_mo2 / "app",
        skyrim_mo2_downloads=skyrim_mo2 / "downloads",
        skyrim_mo2_mods=skyrim_mo2 / "mods",
        skyrim_mo2_profiles=skyrim_mo2 / "profiles",
        skyrim_mo2_overwrite=skyrim_mo2 / "overwrite",
        exports=resolved / "exports",
        cache=resolved / "runtime" / "cache",
        jobs=resolved / "runtime" / "jobs",
        transactions=resolved / "runtime" / "transactions",
        skyrim_environment_configuration=skyrim / "environment.json",
        skyrim_recipes=skyrim / "recipes",
        skyrim_target_environments=skyrim / "target-environments",
    )

def initialize_workspace(root: Path) -> WorkspaceLayout:
    layout = workspace_layout(root)
    for relative in _DIRECTORIES:
        (layout.root / relative).mkdir(parents=True, exist_ok=True)
    return layout

"games/skyrim-se-ae/recipes",
"games/skyrim-se-ae/target-environments",

skyrim_environment_configuration: Path
skyrim_recipes: Path
skyrim_target_environments: Path
```

Return `skyrim / "environment.json"`, `skyrim / "recipes"`, and `skyrim / "target-environments"` from `workspace_layout`.

- [ ] **Step 4: Implement immutable retention and atomic CAS**

Use source paths:

```python
def _source_target(root: Path, kind: str, sha256: str, filename: str) -> Path:
    return root / "games" / "skyrim-se-ae" / kind / sha256 / filename
```

Construct the store with `workspace_layout`, which creates nothing. Before any write, validate direct existing ancestors and target path containment, then create only the documented required ModLab parent directories. Stage exact bytes under `workspace/runtime/jobs/<uuid>.part`, flush and `os.fsync`, then `os.replace`. If a target exists, compare bytes and return `SourceRetention(..., changed=False)` or block on mismatch; a new exact target returns `changed=True`.

`write` and `compare_and_swap` must re-read through `configuration_from_bytes`. The CAS compares SHA-256 of the exact existing bytes, not only parsed equality. Its target write uses `configuration_bytes` and returns the verified post-write snapshot. Cleanup the staging file in `finally`.

- [ ] **Step 5: Run store, workspace, and checkpoint regressions**

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_workspace tests.test_skyrim_environment_store tests.test_checkpoint_store -v
```

Expected: PASS.

- [ ] **Step 6: Commit the store**

```powershell
git add modlab/workspace.py modlab/workflows/skyrim/store.py tests/test_workspace.py tests/test_skyrim_environment_store.py
git commit -m "feat: retain Skyrim environment intent atomically"
```

---

### Task 4: Complete Live Observation and Target-Dimension Reconciliation

**Files:**
- Create: `tests/support/__init__.py`
- Create: `tests/support/skyrim_workflow.py`
- Create: `modlab/workflows/skyrim/observation.py`
- Create: `tests/test_skyrim_observation.py`

**Interfaces:**
- Produces: `LiveDimensionFinding(dimension, target_value, actual_value, state, source, message)`.
- Produces: `SkyrimLiveObservation(discovery, projection, live_dimensions, dimension_findings, ready)`.
- Produces: `normalize_mo2_compatibility_version(value: str | None) -> str | None`.
- Produces: `observe_skyrim_environment(steam_root, workspace_root, target_environment) -> SkyrimLiveObservation`.

- [ ] **Step 1: Create a reusable realistic fixture**

Create `tests/support/skyrim_workflow.py` with:

```python
@dataclass(frozen=True)
class SkyrimWorkflowFixture:
    root: Path
    workspace: Path
    steam_root: Path
    game_root: Path
    mo2_root: Path
    recipe_path: Path
    environment_path: Path

def create_skyrim_workflow_fixture(root: Path) -> SkyrimWorkflowFixture:
    # Write appmanifest_489830.acf, SkyrimSE.exe, Skyrim.ccc, the five core
    # master files, contained MO2 2.5.2.0, portable paths, and exact Lab/Play
    # profile files. Use the same primary-only semantics as the live machine.
```

Use existing scanner-test byte layouts rather than calling production helpers. Include `LocalSaves=false`, `LocalSettings=true`, zero managed mod folders, and empty Overwrite.

- [ ] **Step 2: Write failing observation tests**

```python
def test_observes_ready_game_manager_and_only_real_live_dimensions(self):
    fixture = create_skyrim_workflow_fixture(self.root)
    target = load_environment(fixture.environment_path)

    observation = observe_skyrim_environment(
        fixture.steam_root, fixture.workspace, target,
        skyrim_version_reader=lambda _: "1.7.104.0",
        mo2_version_reader=lambda _: "2.5.2.0",
    )

    self.assertTrue(observation.ready)
    self.assertEqual("1.7.104", dict(observation.live_dimensions)["executableRuntime"])
    self.assertEqual("2.5.2", dict(observation.live_dimensions)["adapterVersion"])
    self.assertNotIn("scriptExtender", dict(observation.live_dimensions))
    script = next(item for item in observation.dimension_findings if item.dimension == "scriptExtender")
    self.assertEqual(CheckState.UNKNOWN, script.state)
    self.assertIsNone(script.actual_value)

def test_observed_target_mismatch_blocks(self):
    target = EnvironmentEvidence(1, "skyrim.test", {"adapterVersion": "2.4.0"})
    observation = observe_fixture(target, mo2_version="2.5.2.0")
    self.assertFalse(observation.ready)
    self.assertEqual(CheckState.BLOCKED, observation.dimension_findings[0].state)
```

Add cases for exact MO2 normalization, three-component raw value retained, nonnumeric/extra-component ambiguity, missing Skyrim/MO2 executable identity, blocked Skyrim finding, blocked projection capability, game-root mismatch, target dimension absent from live, and no writes to the fixture.

- [ ] **Step 3: Run and confirm red**

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_skyrim_observation -v
```

Expected: FAIL because observation types/functions do not exist.

- [ ] **Step 4: Implement observation without writes**

Use the pure layout and existing adapters in this order:

```python
layout = workspace_layout(workspace_root)
discovery = discover_skyrim_steam(
    steam_root,
    mo2_path=layout.skyrim_mo2_app,
    version_reader=skyrim_version_reader,
)
if discovery.game_root is None:
    return blocked_observation(discovery, target_environment)
inspection = inspect_skyrim_mo2(
    layout.skyrim_mo2_app,
    Path(discovery.game_root),
    workspace_root=workspace_root,
    version_reader=mo2_version_reader,
)
projection = project_mo2_state(inspection)
```

Implement exact MO2 normalization:

```python
def normalize_mo2_compatibility_version(value: str | None) -> str | None:
    if value is None:
        return None
    parts = value.split(".")
    if len(parts) == 4 and all(part.isdecimal() for part in parts) and parts[-1] == "0":
        return ".".join(parts[:3])
    if len(parts) == 3 and all(part.isdecimal() for part in parts):
        return value
    return None
```

Set live dimensions only from actual adapters. A target-only dimension receives Unknown. A known equal dimension receives Passed. A known unequal dimension receives Blocked. `ready` requires complete game identity, Ready projection, stable read set, concrete manager identity, and no blocked dimension finding.

- [ ] **Step 5: Run observation and adapter regressions**

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_skyrim_observation tests.test_skyrim_scanner tests.test_mo2_projection tests.test_mo2_scanner -v
```

Expected: PASS.

- [ ] **Step 6: Commit observation**

```powershell
git add modlab/workflows/skyrim/observation.py tests/support tests/test_skyrim_observation.py
git commit -m "feat: observe registered Skyrim environments"
```

---

### Task 5: Strict Observed-Baseline Evidence Contract

**Files:**
- Modify: `modlab/adapters/mo2/comparison_serialization.py`
- Modify: `tests/test_mo2_comparison_serialization.py`
- Create: `modlab/workflows/skyrim/evidence.py`
- Create: `tests/test_skyrim_baseline_evidence.py`

**Interfaces:**
- Promotes existing strict helpers to public functions: `adapter_state_from_dict`, `observation_context_to_dict/from_dict`, `capabilities_to_dict/from_dict`, `coverage_to_dict/from_dict`, and `findings_to_dict/from_dict`.
- Produces: `ObservedBaselineEvidence`, `BaselineRecipeEvidence`, and `BaselineTargetEnvironmentEvidence`.
- Produces: `baseline_evidence_to_dict(evidence, *, adapter_state: Mo2AdapterState) -> dict[str, object]`, `baseline_evidence_from_dict(value, *, adapter_state: Mo2AdapterState) -> ObservedBaselineEvidence`, and `BaselineEvidenceFormatError`.

- [ ] **Step 1: Add public-helper regressions first**

Extend the existing comparison serializer tests:

```python
def test_public_adapter_state_parser_round_trips_canonical_state(self):
    state = ready_report().adapter_state
    serialized = adapter_state_to_dict(state)
    self.assertEqual(state, adapter_state_from_dict(serialized))

def test_public_observation_parts_retain_strict_validation(self):
    report = ready_report()
    context = observation_context_from_dict(
        observation_context_to_dict(report.observation_context)
    )
    self.assertEqual(report.observation_context, context)
```

- [ ] **Step 2: Write failing evidence tests**

```python
def test_observed_evidence_round_trip_has_fixed_non_claims(self):
    evidence = valid_baseline_evidence()
    state = valid_adapter_state()
    value = baseline_evidence_to_dict(evidence, adapter_state=state)
    restored = baseline_evidence_from_dict(value, adapter_state=state)
    self.assertEqual(evidence, restored)
    self.assertEqual("ObservedBaseline", value["captureKind"])
    self.assertFalse(value["promotionReady"])
    self.assertFalse(value["restorable"])
    self.assertEqual("NotLinked", value["coverage"]["artifactCoverage"])
    self.assertEqual([], value["actionsPerformed"])

def test_rejects_any_stronger_claim_or_side_effect(self):
    for path, value in (
        (("promotionReady",), True),
        (("restorable",), True),
        (("foundationAssembly",), "Verified"),
        (("validation", "runtimeValidation"), "Passed"),
        (("coverage", "assetConflicts"), "Inspected"),
        (("actionsPerformed",), ["changed-profile"]),
    ):
        state = valid_adapter_state()
        data = baseline_evidence_to_dict(valid_baseline_evidence(), adapter_state=state)
        assign_nested(data, path, value)
        with self.subTest(path=path):
            with self.assertRaises(BaselineEvidenceFormatError):
                baseline_evidence_from_dict(data, adapter_state=state)
```

Add rejection cases for extra/missing/duplicate fields, inconsistent adapter-state hash, mismatched recipe/target IDs, non-sorted selections/dimensions, invalid live dimension states, and save-like strings in all path-bearing fields.

- [ ] **Step 3: Run and confirm red**

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_comparison_serialization tests.test_skyrim_baseline_evidence -v
```

Expected: FAIL because public helpers and evidence module do not exist.

- [ ] **Step 4: Expose existing strict substructure helpers without weakening them**

Rename private functions and update internal callers; do not duplicate their logic. The key public state parser is:

```python
def adapter_state_from_dict(value: object) -> Mo2AdapterState:
    return _adapter_state(value)
```

Public context/capability/coverage/finding helpers must preserve exact field-set, duplicate-identity, enum, SHA-256, and cross-reference validation already used by the full comparison result.

- [ ] **Step 5: Implement the fixed baseline evidence model and serializer**

Model the spec fields exactly. Serialize `game_discovery` with `discovery_to_dict`; deserialize with `discovery_from_dict`. Use promoted MO2 helpers for manager evidence. Both public evidence functions require the separately supplied checkpoint adapter state and recompute `adapterStateSha256` rather than trusting evidence text.

Require these literal values:

```python
_CAPTURE_KIND = "ObservedBaseline"
_QUALIFICATION = "Observed"
_FOUNDATION_ASSEMBLY = "NotVerified"
_NOT_PERFORMED = "NotPerformed"
_NOT_INSPECTED = "NotInspected"
_NOT_LINKED = "NotLinked"
```

All four side-effect tuples must be empty. Represent target dimensions as sorted two-item arrays and live dimension findings as fixed objects with `dimension`, `targetValue`, `actualValue`, `state`, `source`, and `message`.

- [ ] **Step 6: Run serializer tests**

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_comparison_serialization tests.test_skyrim_baseline_evidence tests.test_skyrim_discovery_serialization -v
```

Expected: PASS.

- [ ] **Step 7: Commit evidence**

```powershell
git add modlab/adapters/mo2/comparison_serialization.py modlab/workflows/skyrim/evidence.py tests/test_mo2_comparison_serialization.py tests/test_skyrim_baseline_evidence.py
git commit -m "feat: define observed baseline evidence"
```

---

### Task 6: Immutable Baseline Capture, Parent Validation, and Pointer CAS

**Files:**
- Create: `modlab/workflows/skyrim/capture.py`
- Create: `tests/test_skyrim_baseline_capture.py`

**Interfaces:**
- Produces: `BaselineCaptureResult(checkpoint, selected, configuration, actions_performed, warning)`.
- Produces: `create_observed_baseline(snapshot, observation, recipe_review, store, checkpoint_store, *, clock) -> BaselineCaptureResult`.
- Produces: `select_observed_baseline(snapshot, checkpoint_id, store, checkpoint_store) -> ConfigurationWrite`.
- Produces: `SkyrimBaselineCaptureError` and `SkyrimBaselineSelectionError`.

- [ ] **Step 1: Write failing capture tests**

```python
def test_capture_creates_observed_checkpoint_and_selects_it(self):
    result = create_ready_baseline(self.fixture, clock=lambda: FIXED_TIME)
    self.assertTrue(result.selected)
    self.assertEqual((), result.checkpoint.artifact_ids)
    self.assertEqual("portable-mo2-skyrim", result.checkpoint.adapter_id)
    self.assertEqual(result.checkpoint.checkpoint_id, result.configuration.baseline_checkpoint_id)
    self.assertEqual(2, len(result.actions_performed))
    state = adapter_state_from_dict(json.loads(result.checkpoint.adapter_state_json))
    evidence = baseline_evidence_from_dict(
        json.loads(result.checkpoint.evidence_json), adapter_state=state
    )
    self.assertFalse(evidence.promotion_ready)
    self.assertFalse(evidence.restorable)

def test_second_capture_uses_selected_valid_baseline_as_parent(self):
    first = create_ready_baseline(self.fixture, clock=lambda: FIRST_TIME)
    second = create_ready_baseline(self.fixture, clock=lambda: SECOND_TIME)
    self.assertEqual(first.checkpoint.checkpoint_id, second.checkpoint.parent_checkpoint_id)

def test_pointer_failure_leaves_checkpoint_available_but_unselected(self):
    with patch.object(self.store, "compare_and_swap", side_effect=SkyrimEnvironmentConflictError("changed")):
        result = create_ready_baseline(self.fixture, clock=lambda: FIXED_TIME)
    self.assertFalse(result.selected)
    self.assertIsNotNone(self.checkpoint_store.get(result.checkpoint.checkpoint_id))
    self.assertIn(result.checkpoint.checkpoint_id, result.warning)
```

Add tests blocking capture before writes for unready observation, unstable read set, missing executable identity, non-ready recipe review, changed retained source, invalid/cross-game/cross-environment/cross-adapter/cross-lineage/cross-recipe parent, modified parent lockfile, and a nonempty artifact ID attempt. Add selection tests for valid orphan recovery and every cross-link rejection.

Also test same-second idempotence: a repeated identical draft reuses the checkpoint, does not report the checkpoint path as newly written, and reports the configuration path only if its selected pointer actually changes.

- [ ] **Step 2: Run and confirm red**

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_skyrim_baseline_capture -v
```

Expected: FAIL because capture functions do not exist.

- [ ] **Step 3: Build checkpoint drafts only from Ready evidence**

Validate observation and sources first, then create:

```python
def _canonical_json(value: dict[str, object]) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )

state = observation.projection.adapter_state
draft = CheckpointDraft(
    schema_version=1,
    game="skyrim-se-ae",
    environment_id=configuration.environment_id,
    adapter_id="portable-mo2-skyrim",
    created_at=clock().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    parent_checkpoint_id=validated_parent_id,
    lineage_id=configuration.lineage_id,
    recipe_id=configuration.recipe.recipe_id,
    recipe_revision=configuration.recipe.revision,
    recipe_maturity=configuration.recipe.maturity,
    recipe_identity=configuration.recipe.identity,
    artifact_ids=(),
    adapter_state_json=_canonical_json(adapter_state_to_dict(state)),
    evidence_json=_canonical_json(
        baseline_evidence_to_dict(evidence, adapter_state=state)
    ),
)
```

Parent validation must load and verify the parent, parse its fixed baseline evidence, and compare game, environment, adapter, lineage, recipe ID/revision/maturity/identity, recipe source hash, target source hash, target dimensions, selected, and omitted values.

- [ ] **Step 4: Store then CAS-select without deleting an orphan**

Resolve `CheckpointStore.path_for(expected_checkpoint_id)` before creation and record whether it exists. Call `CheckpointStore.create(draft)` first, then report the checkpoint path only when this invocation created it. Build a new configuration with its baseline ID and call `store.compare_and_swap(snapshot.sha256, new_configuration)`; report the configuration path only when CAS replaced it. Catch only store conflict/write failures around the pointer update; return the existing checkpoint ID, accurate action paths, and `selected=False`. Do not delete or rewrite the checkpoint.

`select_observed_baseline` applies the same cross-link validation before CAS. It cannot select a missing/modified or stronger-claim checkpoint.

- [ ] **Step 5: Run capture, store, and checkpoint regressions**

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_skyrim_baseline_capture tests.test_skyrim_environment_store tests.test_checkpoint_store tests.test_checkpoint_serialization -v
```

Expected: PASS.

- [ ] **Step 6: Commit capture**

```powershell
git add modlab/workflows/skyrim/capture.py tests/test_skyrim_baseline_capture.py
git commit -m "feat: capture observed Skyrim baselines"
```

---

### Task 7: Pure, Bounded Baseline Drift Comparison

**Files:**
- Create: `modlab/workflows/skyrim/drift.py`
- Create: `tests/test_skyrim_drift.py`

**Interfaces:**
- Produces: `SkyrimStatusOutcome(StrEnum)` with `MATCHED`, `DRIFTED`, `NO_BASELINE`, and `BLOCKED`.
- Produces: `DriftValue(field, checkpoint_value, current_value)`, `DriftDomain(name, changes)`, and `SkyrimDriftReport(outcome, baseline_checkpoint_id, domains, findings)`.
- Produces: `compare_observed_baseline(configuration, checkpoint, checkpoint_evidence, current_observation, current_recipe_review) -> SkyrimDriftReport`.

- [ ] **Step 1: Write failing status and domain tests**

```python
def test_identical_complete_observation_matches(self):
    report = compare_fixture()
    self.assertEqual(SkyrimStatusOutcome.MATCHED, report.outcome)
    self.assertEqual((), report.domains)

def test_each_identity_domain_reports_drift(self):
    mutations = {
        "game-identity": mutate_game_executable_hash,
        "manager-identity": mutate_mo2_executable_hash,
        "lab-profile": add_lab_mod,
        "play-profile": disable_play_plugin,
        "shared-manager-state": add_overwrite_entry,
        "recipe-intent": change_recipe_selection,
        "target-environment": change_target_dimension,
        "coverage": change_coverage,
    }
    for domain, mutation in mutations.items():
        with self.subTest(domain=domain):
            report = compare_fixture(mutation=mutation)
            self.assertEqual(SkyrimStatusOutcome.DRIFTED, report.outcome)
            self.assertIn(domain, tuple(item.name for item in report.domains))
```

Add tests for NoBaseline, modified/missing checkpoint as Blocked, incomplete current observation as Blocked, no hash-only fallback, case-sensitive byte/hash changes, manager containment drift, bounded seven-entry sequence windows, add/remove/enable/order explanations, state-file changes, installed `meta.ini` changes, and current versus checkpoint values.

- [ ] **Step 2: Run and confirm red**

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_skyrim_drift -v
```

Expected: FAIL because drift models/functions do not exist.

- [ ] **Step 3: Implement domain comparison from typed state**

Parse checkpoint state through `adapter_state_from_dict`. Compare explicit projections:

```python
domains = tuple(
    domain
    for domain in (
        _compare_game_identity(checkpoint_evidence, current_observation),
        _compare_manager_identity(checkpoint_evidence, current_observation),
        _compare_profile("lab-profile", baseline_state.lab, current_state.lab),
        _compare_profile("play-profile", baseline_state.play, current_state.play),
        _compare_shared_state(baseline_state, current_state),
        _compare_recipe_intent(configuration, checkpoint_evidence, current_recipe_review),
        _compare_target_environment(configuration, checkpoint_evidence, current_observation),
        _compare_coverage(checkpoint_evidence, current_observation),
    )
    if domain.changes
)
```

Use typed tuples and exact SHA/version/path values, not rendered text. Game identity includes app ID, manifest install directory, executable version/runtime/size/SHA-256, the complete sorted top-level discovery `data_files` identities, and Creation Club counts. Manager identity includes executable raw version/size/SHA-256, game root, all five configured path values/containment flags, and the stable-read requirement. For a changed sequence, return length, membership/enablement changes, and a first-divergence window containing at most three entries before, the differing entry, and three after. Never emit a complete unbounded mod/plugin list.

- [ ] **Step 4: Implement outcome precedence**

Return NoBaseline when the configuration has no pointer. Return Blocked before comparing when the checkpoint is not Available, evidence parsing fails, current observation is incomplete, or any required domain cannot be built. Return Drifted for nonempty domains and Matched only for a complete empty-domain comparison.

- [ ] **Step 5: Run drift and projection tests**

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_skyrim_drift tests.test_mo2_projection tests.test_mo2_comparison -v
```

Expected: PASS.

- [ ] **Step 6: Commit drift comparison**

```powershell
git add modlab/workflows/skyrim/drift.py tests/test_skyrim_drift.py
git commit -m "feat: explain Skyrim baseline drift"
```

---

### Task 8: Skyrim Workflow Service

**Files:**
- Create: `modlab/workflows/skyrim/service.py`
- Create: `tests/test_skyrim_service.py`

**Interfaces:**
- Produces: `configure_skyrim_environment(*, steam_root: Path, recipe_path: Path, target_environment_path: Path, workspace_root: Path, select: tuple[str, ...] = (), omit: tuple[str, ...] = (), replace_existing: bool = False, skyrim_version_reader: Callable[[Path], str | None] = read_windows_file_version, mo2_version_reader: Callable[[Path], str | None] = read_windows_file_version) -> ConfigureResult`.
- Produces: `create_skyrim_baseline(workspace_root, *, clock) -> BaselineCaptureResult`.
- Produces: `use_skyrim_baseline(workspace_root, checkpoint_id) -> BaselineUseResult`.
- Produces: `get_skyrim_status(workspace_root) -> SkyrimDriftReport`.
- Produces: `SkyrimWorkflowError` subclasses with stable user-safe messages.

Use these result models:

```python
@dataclass(frozen=True)
class ConfigureResult:
    configuration: SkyrimEnvironmentConfiguration
    observation: SkyrimLiveObservation
    recipe_review: RecipeReview
    changed: bool
    actions_performed: tuple[str, ...]

@dataclass(frozen=True)
class BaselineUseResult:
    checkpoint_id: str
    configuration: SkyrimEnvironmentConfiguration
    changed: bool
    actions_performed: tuple[str, ...]
```

- [ ] **Step 1: Write failing configure-service tests**

```python
def test_configure_retains_sources_and_writes_one_valid_registration(self):
    result = configure_skyrim_environment(
        steam_root=self.fixture.steam_root,
        recipe_path=self.fixture.recipe_path,
        target_environment_path=self.fixture.environment_path,
        workspace_root=self.fixture.workspace,
        select=(),
        omit=(),
        replace_existing=False,
        skyrim_version_reader=lambda _: "1.7.104.0",
        mo2_version_reader=lambda _: "2.5.2.0",
    )
    self.assertTrue(result.changed)
    self.assertTrue(result.observation.ready)
    self.assertTrue(result.recipe_review.ready_for_approval)
    self.assertIsNone(result.configuration.baseline_checkpoint_id)
    self.assertEqual(3, len(result.actions_performed))
```

Add cases for idempotence, `--replace` requirement with exact pre-replacement changed-field reporting, changed recipe clearing the old baseline, safe preservation only when all identity/intent fields match, selected/omitted pass-through, rejected Incomplete review, blocked live observation, mismatched target dimension, source mutation between load and retention, no workspace-directory creation before validation, and exact three-file action reporting.

- [ ] **Step 2: Write failing capture/use/status orchestration tests**

```python
def test_full_service_flow_configures_captures_and_matches(self):
    configure_fixture(self.fixture)
    capture = create_skyrim_baseline(
        self.fixture.workspace, clock=lambda: FIXED_TIME,
        skyrim_version_reader=lambda _: "1.7.104.0",
        mo2_version_reader=lambda _: "2.5.2.0",
    )
    self.assertTrue(capture.selected)
    status = get_skyrim_status(
        self.fixture.workspace,
        skyrim_version_reader=lambda _: "1.7.104.0",
        mo2_version_reader=lambda _: "2.5.2.0",
    )
    self.assertEqual(SkyrimStatusOutcome.MATCHED, status.outcome)
```

Add NoBaseline, drift, blocked source/checkpoint/current observation, valid orphan use, and status filesystem before/after identity tests.

- [ ] **Step 3: Run and confirm red**

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_skyrim_service -v
```

Expected: FAIL because the service does not exist.

- [ ] **Step 4: Implement configure in validation-before-write order**

Use this sequence:

```python
recipe_source = load_recipe_source(recipe_path)
target_source = load_environment_source(target_environment_path)
observation = observe_skyrim_environment(
    steam_root, workspace_root, target_source.environment,
    skyrim_version_reader=skyrim_version_reader,
    mo2_version_reader=mo2_version_reader,
)
review = review_recipe(
    recipe_source.recipe, target_source.environment, select, omit
)
_require_configurable(observation, review)
proposed = _build_configuration(
    steam_root=Path(observation.discovery.steam_root),
    game_root=Path(observation.discovery.game_root),
    recipe_source=recipe_source,
    target_source=target_source,
    recipe_review=review,
)
_preflight_existing(store, proposed, replace_existing)
recipe_retention = store.retain_recipe(recipe_source)
environment_retention = store.retain_target_environment(target_source)
configuration = replace(
    proposed,
    recipe=replace(proposed.recipe, source=recipe_retention.reference),
    target_environment=replace(
        proposed.target_environment, source=environment_retention.reference
    ),
)
write = store.write(configuration, replace=replace_existing)
action_paths: list[Path] = []
if recipe_retention.changed:
    action_paths.append(recipe_retention.path)
if environment_retention.changed:
    action_paths.append(environment_retention.path)
action_paths.extend(Path(path) for path in write.paths_written)
actions = tuple(str(path) for path in action_paths)
return ConfigureResult(
    configuration=write.configuration,
    observation=observation,
    recipe_review=review,
    changed=write.changed,
    actions_performed=actions,
)
```

Preflight must happen before source retention so a missing `--replace` causes no write. Build all action paths from verified store results.

When a valid existing configuration differs and `replace_existing` is false, `_preflight_existing` raises `SkyrimEnvironmentConflictError` containing `configuration_changes(existing, proposed)`. The public error message and CLI rendering list those dotted fields and bounded before/current values so the user sees the proposed change before deciding to rerun with `--replace`.

- [ ] **Step 5: Implement capture/use/status orchestration**

Each operation loads one exact configuration snapshot, re-verifies retained source bytes, reconstructs the recipe review, and re-observes live state. Capture calls Task 6 only when all gates pass. Use calls Task 6 selection. Status never calls an initializing or write-capable method; it loads existing paths directly, verifies the checkpoint, and calls Task 7.

- [ ] **Step 6: Run service and subsystem regressions**

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_skyrim_service tests.test_skyrim_baseline_capture tests.test_skyrim_drift tests.test_skyrim_environment_store tests.test_skyrim_observation -v
```

Expected: PASS.

- [ ] **Step 7: Commit the workflow service**

```powershell
git add modlab/workflows/skyrim/service.py tests/test_skyrim_service.py
git commit -m "feat: orchestrate Skyrim baseline workflows"
```

---

### Task 9: Strict Command Results and User-Facing CLI

**Files:**
- Create: `modlab/workflows/skyrim/serialization.py`
- Create: `tests/test_skyrim_workflow_serialization.py`
- Modify: `modlab/cli.py`
- Create: `tests/test_skyrim_workflow_cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces strict `configure_result_to_dict/text`, `capture_result_to_dict/text`, `use_result_to_dict/text`, and `status_result_to_dict/text`.
- Adds: `skyrim configure`, `skyrim baseline create`, `skyrim baseline use`, and `skyrim status`.
- Exit contract: `0` for successful configure/capture/use and Matched; `3` for Drifted, NoBaseline, Blocked, or safe workflow refusal; argparse remains `2`.

- [ ] **Step 1: Write failing result-serialization tests**

```python
def test_status_json_and_text_are_bounded_and_explicitly_read_only(self):
    result = drifted_status_result()
    value = status_result_to_dict(result)
    text = status_result_to_text(result)
    self.assertEqual("Drifted", value["status"]["outcome"])
    self.assertEqual([], value["actionsPerformed"])
    self.assertEqual([], value["downloadsPerformed"])
    self.assertEqual([], value["installationActionsPerformed"])
    self.assertEqual([], value["programsLaunched"])
    self.assertIn("Drifted", text)
    self.assertIn("Nothing was written, launched, installed, repaired, restored, or promoted.", text)
    self.assertLessEqual(max_order_window_length(value), 7)
```

Add strict round-trip/rejection tests for all result field sets, action-path containment, command-specific side effects, checkpoint selection failure warning, NoBaseline/Blocked nullability, duplicate domains/fields, and false installation/download/program actions.

- [ ] **Step 2: Run serialization tests and confirm red**

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_skyrim_workflow_serialization -v
```

Expected: FAIL because result serializers do not exist.

- [ ] **Step 3: Implement strict JSON and concise text**

Every top-level result uses:

```text
schemaVersion
<configure|baselineCapture|baselineUse|status>
actionsPerformed
downloadsPerformed
installationActionsPerformed
programsLaunched
```

Configure text lists verified game/runtime, manager/version, recipe selection identity, unverified target dimensions, and exact ModLab files written. Capture text names checkpoint ID, parent, selected state, fixed non-claims, and exact files. Status text leads with Matched/Drifted/NoBaseline/Blocked, then bounded domains and coverage. Never print complete adapter state in text.

- [ ] **Step 4: Add failing CLI tests**

```python
def test_full_cli_flow_requires_paths_once_then_matches(self):
    configure_code = main([
        "skyrim", "configure",
        "--steam-root", str(self.fixture.steam_root),
        "--recipe", str(self.fixture.recipe_path),
        "--environment", str(self.fixture.environment_path),
        "--workspace", str(self.fixture.workspace),
    ], self.stdout, self.stderr)
    self.assertEqual(0, configure_code)

    capture_code = main([
        "skyrim", "baseline", "create",
        "--workspace", str(self.fixture.workspace),
    ], self.stdout, self.stderr)
    self.assertEqual(0, capture_code)

    status_code = main([
        "skyrim", "status",
        "--workspace", str(self.fixture.workspace),
    ], self.stdout, self.stderr)
    self.assertEqual(0, status_code)
    self.assertIn("Matched", self.stdout.getvalue())
```

Patch version readers at their service import boundaries. Add JSON tests, repeated `--select/--omit`, pre-replacement changed-field output without `--replace`, successful `--replace`, baseline use, all exits, stderr-safe errors, no config, malformed config, pointer failure, and before/after file inventories proving status writes nothing.

- [ ] **Step 5: Add parser and dispatch**

Parser shape:

```text
skyrim configure --steam-root PATH --recipe PATH --environment PATH
                 [--select ID] [--omit ID] [--replace]
                 [--workspace PATH] [--format text|json]
skyrim baseline create [--workspace PATH] [--format text|json]
skyrim baseline use CHECKPOINT_ID [--workspace PATH] [--format text|json]
skyrim status [--workspace PATH] [--format text|json]
```

Catch only named recipe/config/store/evidence/capture/workflow format and safety exceptions. Print one concise error to stderr and return `3`; do not expose a traceback for expected user errors. Unexpected exceptions remain visible to tests/developers.

- [ ] **Step 6: Run CLI and all existing command regressions**

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_skyrim_workflow_serialization tests.test_skyrim_workflow_cli tests.test_cli tests.test_checkpoint_cli tests.test_mo2_compare_cli tests.test_skyrim_discovery_cli -v
```

Expected: PASS.

- [ ] **Step 7: Commit the CLI**

```powershell
git add modlab/workflows/skyrim/serialization.py modlab/cli.py tests/test_skyrim_workflow_serialization.py tests/test_skyrim_workflow_cli.py tests/test_cli.py
git commit -m "feat: expose Skyrim baseline workflow"
```

---

### Task 10: Failure Matrix, Documentation, Full and Live Verification

**Files:**
- Modify: `tests/test_skyrim_workflow_cli.py`
- Modify: `README.md`
- Verify: all source/tests/spec/plan files
- Validate live: `C:\Users\red\Desktop\Steam\steamapps\common\Skyrim Special Edition`
- Validate live: `C:\Users\red\Desktop\Modlab\workspace\tools\mo2\skyrim-se-ae\app`

**Interfaces:**
- Validates the complete public workflow and exact acceptance criteria.
- Produces the first real environment registration and observed baseline beneath `C:\Users\red\Desktop\Modlab\workspace`.
- Does not add a new production interface.

- [ ] **Step 1: Add the final cross-cutting failure matrix**

In `tests/test_skyrim_workflow_cli.py`, parameterize fixture mutations for:

```python
FAILURE_CASES = (
    ("missing Steam manifest", remove_manifest),
    ("missing Skyrim identity", remove_skyrim_executable),
    ("missing MO2 identity", remove_mo2_executable),
    ("escaped MO2 path", escape_mod_directory),
    ("missing Lab profile", remove_lab_profile),
    ("unstable MO2 read set", mutate_during_inspection),
    ("duplicate recipe key", duplicate_recipe_key),
    ("target runtime mismatch", mismatch_target_runtime),
    ("modified checkpoint", modify_checkpoint),
    ("redirected configuration target", redirect_configuration_path),
)
```

Run missing manifest, missing Skyrim identity, missing MO2 identity, escaped MO2 path, missing Lab profile, and target-runtime mismatch through `configure`. Run unstable read-set and mutated retained-source cases through both `baseline create` and `status`. Run modified checkpoint through `baseline use` and `status`. Run redirected configuration through all four public operations. For every named command/case pair, assert exit `3`, the named reason, no traceback, no game/MO2/profile/save change, no unexpected ModLab file, and empty download/install/launch arrays in JSON results where a result is available.

- [ ] **Step 2: Run the failure matrix**

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_skyrim_workflow_cli -v
```

Expected: PASS.

- [ ] **Step 3: Update README accurately**

Add the three normal commands plus recovery selection. Document these exact facts:

- paths are entered once during configuration;
- recipe and target environment are retained by exact source hash;
- baseline is Observed, not assembled/tested/promotable/restorable;
- status compares game identity, manager identity/containment, both profiles, shared manager state, recipe intent, target environment, and coverage;
- Matched is limited to that boundary;
- status writes nothing;
- configure/capture/use write only reported ModLab files;
- saves and co-saves are excluded;
- payloads/assets/plugin records/runtime remain uninspected; and
- MO2 bootstrap, installation, tool launch, smoke testing, promotion, rollback, and other games remain later work.

Change the build-sequence paragraph so the proven baseline/drift slice is current and the next independent builds remain MO2 bootstrap, payload/archive linkage, and contained tool execution.

- [ ] **Step 4: Run the complete automated gate**

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest discover -s tests -v
git diff --check
rg -n "T[B]D|T[O]DO|F[I]XME|H[A]CK|Not[I]mplemented" modlab tests README.md docs/superpowers/specs/2026-08-31-skyrim-baseline-drift-design.md docs/superpowers/plans/2026-08-31-skyrim-baseline-drift.md
```

Expected: all tests PASS, `git diff --check` exits `0`, and the marker scan has no new feature marker. An intentional older exception-handler `pass` is not a marker failure.

- [ ] **Step 5: Commit documentation and final tests**

```powershell
git add README.md tests/test_skyrim_workflow_cli.py
git commit -m "docs: document Skyrim baseline workflow"
```

- [ ] **Step 6: Snapshot the real read-only boundary before configuration**

From the isolated worktree, run a PowerShell verifier that records:

- exact hashes of Skyrim discovery files used by the adapter;
- `ModOrganizer.exe`, `ModOrganizer.ini`, `Skyrim.ccc`, every fixed Lab/Play profile file, and every observed top-level `meta.ini`;
- top-level entry name/type/reparse identities for profiles, mods, and Overwrite;
- a recursive name/size/SHA-256 inventory of any save/co-save directories if present, without parsing their contents; and
- the preexisting workspace inventory outside the expected configuration, retained-source, checkpoint, and transient job paths.

Keep the snapshot only in process memory; do not persist a report.

- [ ] **Step 7: Configure the real environment**

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m modlab skyrim configure `
  --steam-root 'C:\Users\red\Desktop\Steam' `
  --recipe 'C:\Users\red\Desktop\Modlab\catalogue\recipes\skyrim-se-ae-current-draft.json' `
  --environment 'C:\Users\red\Desktop\Modlab\catalogue\environments\skyrim-steam-1.7.104.json' `
  --workspace 'C:\Users\red\Desktop\Modlab\workspace' `
  --format json
```

Expected: exit `0`; exact game/MO2/recipe/target identities; SKSE target remains unverified; only three expected ModLab files are reported when none existed (recipe snapshot, target-environment snapshot, registration).

- [ ] **Step 8: Capture and verify the first real observed baseline**

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m modlab skyrim baseline create `
  --workspace 'C:\Users\red\Desktop\Modlab\workspace' `
  --format json
```

Expected: exit `0`; one `checkpoint-sha256:<64 lowercase hex>` ID; `selected=true`; empty artifact IDs; fixed observed/non-restorable/non-promotable evidence; only the checkpoint and registration pointer are reported as written.

Use the returned ID with existing `checkpoint verify` and require Available.

- [ ] **Step 9: Require immediate Matched status and zero status writes**

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m modlab skyrim status `
  --workspace 'C:\Users\red\Desktop\Modlab\workspace' `
  --format json
```

Expected: exit `0`, `outcome=Matched`, all side-effect arrays empty, and no coverage overclaim.

Repeat the in-memory snapshot and prove:

- the Skyrim/MO2/profile/mod/Overwrite/save boundary matches byte-for-byte;
- status itself changed no ModLab file;
- only the expected content-addressed source, registration, and checkpoint files differ from the original workspace inventory; and
- `workspace/runtime/jobs` contains no staging file.

- [ ] **Step 10: Perform inline code review and fix substantive findings**

Review every commit against the spec and plan. Inspect validation-before-write ordering, strict deserialization, source re-verification, CAS behavior, orphan recovery, checkpoint cross-links, outcome precedence, bounded output, save exclusion, and accurate side-effect reporting. Add a failing regression test before every behavioral fix and commit each accepted correction.

Do not dispatch a reviewer subagent unless the user explicitly requests subagents; the session-level collaboration policy forbids unrequested delegation.

- [ ] **Step 11: Re-run the exact post-review gate**

Run the full suite, `git diff --check`, marker scan, real configure idempotence, real checkpoint verification, real status, and the before/after live boundary proof on the final feature commit. Expected: all pass; configure reports no change on identical re-run; status remains Matched.

- [ ] **Step 12: Integrate, verify from main, publish, and clean up**

```powershell
git fetch origin
git -C 'C:\Users\red\Desktop\Modlab' merge --ff-only feature/skyrim-baseline-drift
```

From main, rerun the full automated suite, checkpoint verification, live status, and zero-write proof. Then push:

```powershell
git -C 'C:\Users\red\Desktop\Modlab' push origin main
```

Verify local `main` and `origin/main` resolve to the exact tested commit. Only after the worktree is clean, merged, published, and reverified, remove `C:\Users\red\Desktop\Modlab\.worktrees\skyrim-baseline-drift`, prune worktrees, and delete the merged local feature branch.

Report the automated test count, live baseline ID, Matched status, exact commit, remote equality, written ModLab paths, and unchanged Skyrim/MO2/save proof. Repeat that payload content, asset conflicts, plug-in records, foundation assembly, runtime validation, restoration, and promotion remain outside this milestone.
