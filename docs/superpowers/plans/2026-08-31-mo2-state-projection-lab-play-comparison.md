# MO2 State Projection and Lab-vs-Play Comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a trustworthy, completely read-only `manager compare mo2` command that projects the exact Skyrim MO2 Lab and Play profile state, blocks on incomplete or incoherent evidence, and explains Play-to-Lab differences without changing anything.

**Architecture:** Extend the existing scanner with an internal, stable two-pass read set while preserving the public `manager discover mo2` JSON contract. New pure projection and comparison modules consume validated scanner evidence; a separate strict serializer produces canonical JSON and bounded text. No comparison module reads the filesystem.

**Tech Stack:** Python 3.12.13 standard library, frozen dataclasses, `hashlib`/`json`/`pathlib`/`locale`, `argparse`, and `unittest` on Windows.

**Spec:** `docs/superpowers/specs/2026-08-31-mo2-state-projection-lab-play-comparison-design.md`

## Global Constraints

- Work in an isolated worktree created at execution time with `superpowers:using-git-worktrees`. Keep it under `C:\Users\red\Desktop\Modlab\.worktrees\mo2-compare` and ensure `.worktrees/` is ignored before creation.
- Use the bundled Python exactly: `C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`.
- Use the bundled Git executable at `C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe`; plan snippets use `git` as the readable name for that exact executable.
- Invoke every Python command with `-B` so tests and live validation create no bytecode files.
- Use only the Python standard library; add no package or network dependency.
- Preserve the existing `manager discover mo2` JSON field set and exit behavior.
- Comparison direction is always `Play-to-Lab` and profiles are exactly `ModLab - Lab` and `ModLab - Play`.
- Required profile files are `modlist.txt`, `plugins.txt`, `loadorder.txt`, and `settings.ini`.
- `modlist.txt` disk rows are reverse priority. Reverse the full parsed row sequence before assigning UI positions.
- `plugins.txt` uses the Windows system encoding; `*` is active, unmarked is inactive, and primary plug-ins are absent.
- Skyrim primary plug-ins are the five mandatory core masters followed by the ordered installed subset of unique policy entries from `Skyrim.ccc`; the file can also name uninstalled Creations.
- Ready means complete only for MO2 profile-state evidence. Installed payloads, asset conflicts, plug-in record conflicts, and runtime validation remain uninspected.
- A Ready comparison may retain contextual `Unknown` findings such as an unavailable executable version when the executable byte identity is still observed. A missing executable identity, any `Blocked` scanner finding, or any incomplete required capability blocks.
- A blocked result exposes observation context, capabilities, coverage, and findings, but `adapterState`, `adapterStateSha256`, and `differences` are `null`.
- The command performs no writes anywhere and returns explicit empty `actionsPerformed`, `downloadsPerformed`, `installationActionsPerformed`, and `programsLaunched` arrays.
- Do not add installation, promotion, checkpoint persistence, conflict solving, LOOT, xEdit, program launch, or support for another game.
- Commit after every task only after its focused tests and relevant regression tests pass.

## File Structure

- Modify `modlab/adapters/mo2/model.py`: retain existing discovery evidence and add internal-only comparison inspection evidence.
- Modify `modlab/adapters/mo2/profile.py`: apply MO2's actual list encodings and expose both local profile settings.
- Create `modlab/adapters/mo2/readset.py`: own deterministic first-pass file/directory captures and second-pass stability verification.
- Modify `modlab/adapters/mo2/scanner.py`: route authoritative reads through the read set, observe `Skyrim.ccc`, and attach internal comparison evidence.
- Create `modlab/adapters/mo2/projection.py`: define comparison-state models, explicit capability gating, plug-in reconciliation, canonical adapter state, and its SHA-256.
- Create `modlab/adapters/mo2/comparison.py`: calculate placement-aware Play-to-Lab changes and bounded-order metadata without filesystem access.
- Create `modlab/adapters/mo2/comparison_serialization.py`: strictly parse/serialize the full result and format concise text.
- Modify `modlab/cli.py`: add `manager compare mo2` and its stable exit/output behavior.
- Modify `README.md`: document the new command, its coverage, and its non-claims.
- Modify `tests/test_mo2_profile.py`, `tests/test_mo2_scanner.py`, `tests/test_mo2_cli.py`, and `tests/test_mo2_serialization.py`: retain discovery regressions.
- Create `tests/test_mo2_readset.py`, `tests/test_mo2_projection.py`, `tests/test_mo2_comparison.py`, `tests/test_mo2_comparison_serialization.py`, and `tests/test_mo2_compare_cli.py`.

---

### Task 1: Correct MO2 Profile Semantics

**Files:**
- Modify: `modlab/adapters/mo2/model.py:44-53`
- Modify: `modlab/adapters/mo2/profile.py:35-151`
- Modify: `tests/test_mo2_profile.py:15-101`
- Test: `tests/test_mo2_serialization.py`

**Interfaces:**
- Consumes: existing `Mo2ProfileEvidence` and strict discovery serializer.
- Produces: `parse_profile_settings_bytes(data: bytes) -> tuple[bool | None, bool | None]` and `parse_plugins_bytes(data: bytes, *, encoding: str | None = None) -> tuple[Mo2PluginEntry, ...]`.
- Produces: internal `Mo2ProfileEvidence.profile_local_settings: bool | None` with a default of `None`; existing discovery JSON deliberately omits this field.

- [ ] **Step 1: Add failing parser tests for the Windows encoding and both profile settings**

Add imports for `parse_profile_settings_bytes` and these tests:

```python
def test_plugins_use_explicit_windows_system_encoding(self):
    plugins = parse_plugins_bytes(
        "# generated\r\n*Café.esp\r\nAncien.esp\r\n".encode("cp1252"),
        encoding="cp1252",
    )
    self.assertEqual(("Café.esp", "Ancien.esp"), tuple(item.name for item in plugins))
    self.assertEqual((True, False), tuple(item.enabled for item in plugins))

    with self.assertRaisesRegex(Mo2ProfileError, "plugins.txt"):
        parse_plugins_bytes(b"*Caf\xe9.esp\r\n", encoding="ascii")

def test_parses_both_profile_isolation_settings(self):
    values = parse_profile_settings_bytes(
        b"[General]\nLocalSaves=false\nLocalSettings=true\n"
    )
    self.assertEqual((False, True), values)
```

Extend `test_inspects_only_known_state_files_without_reading_saves` with `LocalSettings=true` and:

```python
self.assertTrue(evidence.profile_local_settings)
```

- [ ] **Step 2: Run the focused tests and confirm the red state**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_profile -v
```

Expected: FAIL because `parse_profile_settings_bytes` and `profile_local_settings` do not exist and `parse_plugins_bytes` has no `encoding` keyword.

- [ ] **Step 3: Implement strict per-file decoding and settings parsing**

Add `import locale`. Change the content helper and its callers to use an explicit label and encoding:

```python
def parse_plugins_bytes(
    data: bytes, *, encoding: str | None = None
) -> tuple[Mo2PluginEntry, ...]:
    selected_encoding = encoding or locale.getencoding()
    entries: list[Mo2PluginEntry] = []
    for number, line in _content_lines(
        data, label="plugins.txt", encoding=selected_encoding
    ):
        if line[0] in {"+", "-"}:
            raise Mo2ProfileError(f"invalid plugin marker on line {number}")
        enabled = line.startswith("*")
        name = line[1:].strip() if enabled else line
        entries.append(
            Mo2PluginEntry(
                _safe_plugin_name(name, f"plugin on line {number}"),
                enabled,
            )
        )
    _reject_duplicates((item.name for item in entries), "plugins")
    return tuple(entries)

def parse_profile_settings_bytes(data: bytes) -> tuple[bool | None, bool | None]:
    try:
        settings = parse_ini_bytes(data)
        return (
            parse_qsettings_bool(settings.get("General", "LocalSaves")),
            parse_qsettings_bool(settings.get("General", "LocalSettings")),
        )
    except Mo2IniError as error:
        raise Mo2ProfileError(f"invalid profile settings.ini: {error}") from error

def _content_lines(
    data: bytes, *, label: str, encoding: str
) -> tuple[tuple[int, str], ...]:
    try:
        text = data.decode(encoding)
    except (LookupError, UnicodeDecodeError) as error:
        raise Mo2ProfileError(f"{label} cannot be decoded as {encoding}") from error
    lines: list[tuple[int, str]] = []
    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if line and not line.startswith("#"):
            lines.append((number, line))
    return tuple(lines)
```

Call `_content_lines` from `parse_modlist_bytes` and `parse_load_order_bytes` with `label` equal to the filename and `encoding="utf-8-sig"`. In `inspect_profile`, parse `settings.ini` once and pass both booleans to:

```python
return Mo2ProfileEvidence(
    name=name,
    relative_path=PurePosixPath("profiles", name).as_posix(),
    profile_local_saves=local_saves,
    state_files=state_files,
    mods=mods,
    plugins=plugins,
    load_order=load_order,
    profile_local_settings=local_settings,
)
```

Append this defaulted field to `Mo2ProfileEvidence` so existing positional construction remains valid:

```python
profile_local_settings: bool | None = None
```

- [ ] **Step 4: Run parser and discovery-serialization tests**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_profile tests.test_mo2_serialization -v
```

Expected: all tests PASS. The canonical `manager discover mo2` schema remains unchanged.

- [ ] **Step 5: Commit the semantic correction**

```powershell
git add modlab/adapters/mo2/model.py modlab/adapters/mo2/profile.py tests/test_mo2_profile.py
git commit -m "fix: match MO2 profile file semantics"
```

---

### Task 2: Capture One Stable, Authoritative Read Set

**Files:**
- Create: `modlab/adapters/mo2/readset.py`
- Create: `tests/test_mo2_readset.py`
- Modify: `modlab/adapters/mo2/model.py:1-86`
- Modify: `modlab/adapters/mo2/profile.py:69-137`
- Modify: `modlab/adapters/mo2/scanner.py:1-588`
- Modify: `tests/test_mo2_scanner.py:11-326`
- Test: `tests/test_mo2_cli.py`

**Interfaces:**
- Produces: `Mo2ReadSet.read_bytes(path: Path) -> bytes`, `optional_bytes(path: Path) -> bytes | None`, `list_directory(path: Path) -> tuple[Mo2DirectoryEntry, ...]`, and `verify() -> Mo2ReadSetVerification`.
- Produces: `Mo2ComparisonInspectionEvidence` attached to `Mo2InspectionReport.comparison_evidence` without adding fields to discovery JSON.
- Changes: `inspect_skyrim_mo2(..., _before_stability_check: Callable[[], None] | None = None)` for deterministic mutation tests only.
- Preserves: `inspect_skyrim_mo2`'s normal positional/keyword interface and `report_to_dict` field set.

- [ ] **Step 1: Write failing unit tests for stable and changing read sets**

Create `tests/test_mo2_readset.py`:

```python
import tempfile
import unittest
from pathlib import Path

from modlab.adapters.mo2.readset import Mo2ReadSet


class Mo2ReadSetTests(unittest.TestCase):
    def test_stable_file_optional_file_and_directory_are_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "state.txt").write_bytes(b"state")
            read_set = Mo2ReadSet()
            self.assertEqual(b"state", read_set.read_bytes(root / "state.txt"))
            self.assertIsNone(read_set.optional_bytes(root / "optional.txt"))
            self.assertEqual(("state.txt",), tuple(
                item.name for item in read_set.list_directory(root)
            ))

            result = read_set.verify()

            self.assertTrue(result.stable)
            self.assertEqual(64, len(result.sha256))
            self.assertEqual((), result.changed_paths)

    def test_detects_file_change_optional_creation_and_directory_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state.txt"
            state.write_bytes(b"before")
            read_set = Mo2ReadSet()
            read_set.read_bytes(state)
            read_set.optional_bytes(root / "optional.txt")
            read_set.list_directory(root)

            state.write_bytes(b"after")
            (root / "optional.txt").write_bytes(b"new")
            (root / "new-folder").mkdir()
            result = read_set.verify()

            self.assertFalse(result.stable)
            self.assertIn(str(state), result.changed_paths)
            self.assertIn(str(root / "optional.txt"), result.changed_paths)
            self.assertIn(str(root), result.changed_paths)
```

- [ ] **Step 2: Run the read-set test and confirm the red state**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_readset -v
```

Expected: FAIL with `ModuleNotFoundError` for `modlab.adapters.mo2.readset`.

- [ ] **Step 3: Implement the deterministic read-set primitive**

Define these frozen public values and one mutable scanner helper in `readset.py`:

```python
@dataclass(frozen=True)
class Mo2DirectoryEntry:
    name: str
    kind: str
    redirected: bool


@dataclass(frozen=True)
class Mo2ReadSetVerification:
    stable: bool
    sha256: str
    changed_paths: tuple[str, ...]


_UNREADABLE = object()


class Mo2ReadSet:
    def __init__(self) -> None:
        self._files: dict[Path, bytes | None] = {}
        self._directories: dict[Path, tuple[Mo2DirectoryEntry, ...]] = {}

    def read_bytes(self, path: Path) -> bytes:
        resolved = Path(path)
        if resolved in self._files:
            value = self._files[resolved]
            if value is None:
                raise FileNotFoundError(resolved)
            return value
        value = self._capture_required_file(resolved)
        self._files[resolved] = value
        return value

    def optional_bytes(self, path: Path) -> bytes | None:
        observed = Path(path)
        if observed not in self._files:
            self._files[observed] = self._capture_optional_file(observed)
        return self._files[observed]

    def list_directory(self, path: Path) -> tuple[Mo2DirectoryEntry, ...]:
        observed = Path(path)
        if observed not in self._directories:
            self._directories[observed] = self._capture_directory(observed)
        return self._directories[observed]

    def verify(self) -> Mo2ReadSetVerification:
        changed: list[str] = []
        for path, first in self._files.items():
            try:
                second = self._capture_optional_file(path)
            except OSError:
                second = _UNREADABLE
            if second != first:
                changed.append(str(path))
        for path, first in self._directories.items():
            try:
                second = self._capture_directory(path)
            except OSError:
                second = _UNREADABLE
            if second != first:
                changed.append(str(path))
        identity = self._canonical_identity()
        return Mo2ReadSetVerification(
            stable=not changed,
            sha256=hashlib.sha256(identity).hexdigest(),
            changed_paths=tuple(sorted(set(changed), key=str.casefold)),
        )
```

Implement `_capture_required_file`, `_capture_optional_file`, `_capture_directory`, and `_canonical_identity` with these invariants:

- required files must be regular and not symlink/reparse points;
- optional absence is recorded as `None` so later creation is detected;
- directory entries are sorted case-insensitively and record `file`, `directory`, or `other` plus redirection status;
- duplicate case-insensitive child names raise `OSError`;
- canonical identity is compact sorted UTF-8 JSON containing every path, presence, byte SHA-256/size, and directory entry tuple;
- no method creates, opens for write, resolves through, or recurses beneath an observed child.

Detect Windows redirection without following it:

```python
def _is_redirected(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return path.is_symlink() or bool(
        attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )
```

Use `os.scandir` plus `entry.is_file(follow_symlinks=False)` and
`entry.is_dir(follow_symlinks=False)` for directory entry types. In `verify`,
use a private `_UNREADABLE = object()` sentinel for second-pass exceptions;
never substitute `None` or `()` because those can equal a genuinely missing
optional file or genuinely empty directory and hide a change.

- [ ] **Step 4: Add failing scanner tests for coherent state and Skyrim primary evidence**

Update the scanner fixture so the game contains:

```python
(game_root / "Skyrim.ccc").write_text(
    "ccBGSSSE001-Fish.esm\n_ResourcePack.esl\n", encoding="utf-8"
)
primary = (
    "Skyrim.esm\nUpdate.esm\nDawnguard.esm\nHearthFires.esm\n"
    "Dragonborn.esm\nccBGSSSE001-Fish.esm\n_ResourcePack.esl\n"
)
```

Write `primary + "SkyUI_SE.esp\n"` to each `loadorder.txt` and only `"*SkyUI_SE.esp\n"` after the `plugins.txt` header.
Write both `LocalSaves=false` and `LocalSettings=true` to each fixture
`settings.ini`.

Add:

```python
def test_attaches_stable_internal_comparison_evidence_without_changing_json(self):
    with tempfile.TemporaryDirectory() as directory:
        layout, game_root, _ = self.make_instance(directory)
        report = inspect_skyrim_mo2(
            layout.skyrim_mo2_app,
            game_root,
            workspace_root=layout.root,
            version_reader=lambda _: "2.5.2.0",
        )
        evidence = report.comparison_evidence
        self.assertIsNotNone(evidence)
        self.assertTrue(evidence.read_set_stable)
        self.assertEqual(64, len(evidence.read_set_sha256))
        self.assertEqual("Skyrim.esm", evidence.primary_plugins[0])
        self.assertEqual("_ResourcePack.esl", evidence.primary_plugins[-1])
        self.assertEqual(
            (True, True),
            tuple(item.profile_local_settings for item in evidence.profile_settings),
        )
        self.assertNotIn("comparisonEvidence", report_to_dict(report))

def test_blocks_if_authoritative_state_changes_during_inspection(self):
    with tempfile.TemporaryDirectory() as directory:
        layout, game_root, _ = self.make_instance(directory)
        target = (
            layout.skyrim_mo2_profiles / "ModLab - Lab" / "plugins.txt"
        )

        report = inspect_skyrim_mo2(
            layout.skyrim_mo2_app,
            game_root,
            workspace_root=layout.root,
            version_reader=lambda _: "2.5.2.0",
            _before_stability_check=lambda: target.write_bytes(b"*Changed.esp\n"),
        )

        findings = {item.code: item for item in report.findings}
        self.assertEqual(
            CheckState.BLOCKED,
            findings["mo2-state-changed-during-inspection"].state,
        )
        self.assertFalse(report.comparison_evidence.read_set_stable)
```

Repeat the mutation assertion as subtests for creating a previously absent `meta.ini` and adding an overwrite child. Assert existing `manager discover mo2` output still has exactly the original discovery fields.

- [ ] **Step 5: Run scanner tests and confirm the red state**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_scanner tests.test_mo2_cli tests.test_mo2_serialization -v
```

Expected: FAIL because comparison evidence, read-set routing, the mutation hook, and primary-policy capture are absent.

- [ ] **Step 6: Add internal comparison evidence without changing discovery serialization**

Add to `model.py`:

```python
@dataclass(frozen=True)
class Mo2ProfileComparisonEvidence:
    name: str
    profile_local_settings: bool | None


@dataclass(frozen=True)
class Mo2ComparisonInspectionEvidence:
    primary_plugins: tuple[str, ...]
    skyrim_ccc: Mo2StateFileEvidence | None
    profile_settings: tuple[Mo2ProfileComparisonEvidence, ...]
    read_set_sha256: str
    read_set_stable: bool
    changed_paths: tuple[str, ...]
```

Append to `Mo2InspectionReport`:

```python
comparison_evidence: Mo2ComparisonInspectionEvidence | None = field(
    default=None, compare=False, repr=False
)
```

Import `field` from `dataclasses`. Do not add this field to `_REPORT_FIELDS`, `report_to_dict`, or `report_from_dict`.

- [ ] **Step 7: Route authoritative scanner reads through one shared read set**

Create one `Mo2ReadSet` after root containment succeeds. Pass it to `_observe_executable`, `inspect_profile`, `_observe_safe_children`, and `_observe_mods`. Change `inspect_profile` to accept `read_set: Mo2ReadSet | None = None` and enumerate the profile's captured top-level directory before reading known files from captured bytes.
Inside direct profile inspection use `observer = read_set or Mo2ReadSet()`; the
scanner always supplies its one shared observer, while existing focused profile
tests receive a local read-only observer.

Add:

```python
_CORE_PRIMARY_PLUGINS = (
    "Skyrim.esm",
    "Update.esm",
    "Dawnguard.esm",
    "HearthFires.esm",
    "Dragonborn.esm",
)

def _observe_primary_plugins(
    game_root: Path,
    read_set: Mo2ReadSet,
    findings: list[Mo2Finding],
) -> tuple[tuple[str, ...], Mo2StateFileEvidence | None]:
    ccc_path = game_root / "Skyrim.ccc"
    data = read_set.optional_bytes(ccc_path)
    if data is None:
        return _CORE_PRIMARY_PLUGINS, None
    creation_plugins = parse_load_order_bytes(data)
    combined = _CORE_PRIMARY_PLUGINS + creation_plugins
    if len({item.casefold() for item in combined}) != len(combined):
        findings.append(_finding(
            CheckState.BLOCKED,
            "skyrim-primary-plugin-policy-invalid",
            "Skyrim.ccc duplicates a primary plug-in identity.",
        ))
    state_file = Mo2StateFileEvidence(
        relative_path="Skyrim.ccc",
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
    )
    return combined, state_file
```

Enumerate the profiles root and require one exact, non-redirected directory for each target name before inspecting it. Observe every installed mod's optional `meta.ini` through `optional_bytes`. Observe overwrite only at its top level.

Immediately before final report construction:

```python
if _before_stability_check is not None:
    _before_stability_check()
verification = read_set.verify()
if not verification.stable:
    findings.append(_finding(
        CheckState.BLOCKED,
        "mo2-state-changed-during-inspection",
        "Authoritative MO2 state changed during inspection; no comparison is safe.",
    ))
comparison_evidence = Mo2ComparisonInspectionEvidence(
    primary_plugins=primary_plugins,
    skyrim_ccc=skyrim_ccc,
    profile_settings=tuple(
        Mo2ProfileComparisonEvidence(item.name, item.profile_local_settings)
        for item in profiles
    ),
    read_set_sha256=verification.sha256,
    read_set_stable=verification.stable,
    changed_paths=verification.changed_paths,
)
```

In `_report`, first validate the unchanged public report through `report_from_dict(report_to_dict(report))`, then return `dataclasses.replace(validated, comparison_evidence=comparison_evidence)`. This keeps discovery serialization byte-for-byte compatible.

- [ ] **Step 8: Run the read-set, scanner, CLI, and serialization tests**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_readset tests.test_mo2_scanner tests.test_mo2_cli tests.test_mo2_serialization -v
```

Expected: all tests PASS, including mutation blocking and unchanged discovery JSON fields.

- [ ] **Step 9: Commit coherent scanner evidence**

```powershell
git add modlab/adapters/mo2/readset.py modlab/adapters/mo2/model.py modlab/adapters/mo2/profile.py modlab/adapters/mo2/scanner.py tests/test_mo2_readset.py tests/test_mo2_scanner.py tests/test_mo2_cli.py
git commit -m "feat: capture stable MO2 comparison evidence"
```

---

### Task 3: Project and Gate Canonical Adapter State

**Files:**
- Create: `modlab/adapters/mo2/projection.py`
- Create: `tests/test_mo2_projection.py`
- Test: `tests/test_mo2_scanner.py`

**Interfaces:**
- Consumes: `Mo2InspectionReport` with internal `Mo2ComparisonInspectionEvidence`.
- Produces: `project_mo2_state(report: Mo2InspectionReport) -> Mo2Projection`.
- Produces: `adapter_state_to_dict(state: Mo2AdapterState) -> dict[str, object]` and `adapter_state_sha256(state: Mo2AdapterState) -> str`.
- Produces exact readiness values `Ready` and `Blocked` and capability status values `complete` and `incomplete`.

- [ ] **Step 1: Write the projection tests first**

Create `tests/test_mo2_projection.py` with a `make_report` helper that constructs two complete profiles, the five core primary plug-ins, one non-primary plug-in, complete path evidence, stable comparison evidence, and empty side effects.

Use this exact helper contract:

```python
CORE = (
    "Skyrim.esm",
    "Update.esm",
    "Dawnguard.esm",
    "HearthFires.esm",
    "Dragonborn.esm",
)

def make_report(
    *,
    lab_mods: tuple[Mo2ModEntry, ...] = (),
    play_mods: tuple[Mo2ModEntry, ...] = (),
    non_primary_plugins: tuple[str, ...] = ("SkyUI_SE.esp",),
    lab_plugins: tuple[Mo2PluginEntry, ...] | None = None,
    play_plugins: tuple[Mo2PluginEntry, ...] | None = None,
    lab_load_order: tuple[str, ...] | None = None,
    play_load_order: tuple[str, ...] | None = None,
    resolved_root: str = r"C:\ModLab\workspace\tools\mo2\skyrim-se-ae\app",
    active_profile: str = "ModLab - Lab",
    file_version: str | None = "2.5.2.0",
    read_set_stable: bool = True,
) -> Mo2InspectionReport:
```

The body creates both profiles with all four required state-file hashes,
explicit false/true local-save/local-settings values, default non-primary
state rows in the same order as `CORE + non_primary_plugins`, all five
contained path kinds, Passed configuration/game/profile/inventory findings,
and one `Mo2ComparisonInspectionEvidence`. An explicitly supplied plug-in or
load-order tuple replaces only that profile's default so each test mutates one
invariant.

The core tests must contain these assertions:

```python
def test_projects_reverse_modlist_priority_and_reconciles_primary_plugins(self):
    report = make_report(
        lab_mods=(
            Mo2ModEntry("Late_separator", "+", True),
            Mo2ModEntry("SkyUI", "+", True),
            Mo2ModEntry("DLC: Dawnguard", "*", True),
        ),
        play_mods=(
            Mo2ModEntry("Late_separator", "+", True),
            Mo2ModEntry("SkyUI", "+", True),
            Mo2ModEntry("DLC: Dawnguard", "*", True),
        ),
    )
    projection = project_mo2_state(report)
    self.assertEqual(Mo2Readiness.READY, projection.readiness)
    lab = projection.adapter_state.lab
    self.assertEqual(
        ("DLC: Dawnguard", "SkyUI", "Late_separator"),
        tuple(item.name for item in lab.mods),
    )
    self.assertEqual(("foreign", "managed", "separator"), tuple(
        item.kind for item in lab.mods
    ))
    self.assertEqual((0, 1, None), tuple(
        item.runtime_priority for item in lab.mods
    ))
    self.assertTrue(all(item.enabled for item in lab.plugins[:5]))
    self.assertEqual(("SkyUI_SE.esp",), tuple(
        item.name for item in lab.plugins if not item.primary
    ))

def test_valid_primary_only_profiles_are_ready(self):
    projection = project_mo2_state(make_report(non_primary_plugins=()))
    self.assertEqual(Mo2Readiness.READY, projection.readiness)
    self.assertEqual(
        (),
        tuple(item.name for item in projection.adapter_state.lab.plugins[5:]),
    )

def test_nonprimary_membership_or_order_mismatch_blocks_without_state(self):
    for report in (
        make_report(lab_plugins=(Mo2PluginEntry("OnlyState.esp", True),)),
        make_report(lab_load_order=CORE + ("Unknown.esp",)),
        make_report(
            lab_load_order=CORE + ("B.esp", "A.esp"),
            lab_plugins=(
                Mo2PluginEntry("A.esp", True),
                Mo2PluginEntry("B.esp", True),
            ),
        ),
    ):
        with self.subTest(report=report):
            projection = project_mo2_state(report)
            self.assertEqual(Mo2Readiness.BLOCKED, projection.readiness)
            self.assertIsNone(projection.adapter_state)
            self.assertIn(
                "mo2-plugin-order-evidence-inconsistent",
                {item.code for item in projection.findings},
            )

def test_context_changes_do_not_change_adapter_state_hash(self):
    first = project_mo2_state(make_report(
        resolved_root=r"C:\First\workspace\tools\mo2\skyrim-se-ae\app",
        active_profile="ModLab - Lab",
    ))
    second = project_mo2_state(make_report(
        resolved_root=r"D:\Relocated\workspace\tools\mo2\skyrim-se-ae\app",
        active_profile="ModLab - Play",
    ))
    self.assertNotEqual(first.observation_context, second.observation_context)
    self.assertEqual(first.adapter_state, second.adapter_state)
    self.assertEqual(first.adapter_state_sha256, second.adapter_state_sha256)

def test_unknown_version_is_contextual_but_unstable_state_blocks(self):
    unknown_version = project_mo2_state(make_report(file_version=None))
    self.assertEqual(Mo2Readiness.READY, unknown_version.readiness)

    unstable = project_mo2_state(make_report(read_set_stable=False))
    self.assertEqual(Mo2Readiness.BLOCKED, unstable.readiness)
    self.assertIsNone(unstable.adapter_state)
```

Add subtests for missing required files, duplicate/case-conflicting primary
identities, an absent `meta.ini` being valid, an observed unreadable or
redirected `meta.ini` blocking, incomplete overwrite evidence, an existing
scanner `Blocked` finding, non-empty side-effect tuples, and missing internal
comparison evidence.

- [ ] **Step 2: Run projection tests and confirm the red state**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_projection -v
```

Expected: FAIL with `ModuleNotFoundError` for `modlab.adapters.mo2.projection`.

- [ ] **Step 3: Define immutable projection types**

Define these enums and frozen dataclasses in `projection.py`:

```python
class Mo2Readiness(StrEnum):
    READY = "Ready"
    BLOCKED = "Blocked"


@dataclass(frozen=True)
class Mo2Capability:
    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class Mo2ObservationContext:
    mo2_root: str
    game_root: str
    active_profile: str | None
    executable: Mo2ExecutableEvidence | None
    configured_paths: tuple[Mo2PathEvidence, ...]
    read_set_sha256: str | None
    read_set_stable: bool


@dataclass(frozen=True)
class Mo2ProjectedMod:
    name: str
    kind: str
    marker: str
    enabled: bool
    list_index: int
    runtime_priority: int | None


@dataclass(frozen=True)
class Mo2ProjectedPlugin:
    name: str
    primary: bool
    enabled: bool
    load_order_index: int


@dataclass(frozen=True)
class Mo2ProfileState:
    name: str
    profile_local_saves: bool
    profile_local_settings: bool
    mods: tuple[Mo2ProjectedMod, ...]
    plugins: tuple[Mo2ProjectedPlugin, ...]
    effective_load_order: tuple[str, ...]
    state_files: tuple[Mo2StateFileEvidence, ...]


@dataclass(frozen=True)
class Mo2InstalledModState:
    name: str
    meta_ini: Mo2StateFileEvidence | None


@dataclass(frozen=True)
class Mo2AdapterState:
    schema_version: int
    adapter_id: str
    lab: Mo2ProfileState
    play: Mo2ProfileState
    installed_mods: tuple[Mo2InstalledModState, ...]
    overwrite_entries: tuple[str, ...]


@dataclass(frozen=True)
class Mo2Coverage:
    profile_state: str
    installed_payload_content: str = "not-inspected"
    asset_conflicts: str = "not-inspected"
    plugin_record_conflicts: str = "not-inspected"
    runtime_validation: str = "not-performed"


@dataclass(frozen=True)
class Mo2Projection:
    readiness: Mo2Readiness
    observation_context: Mo2ObservationContext
    capabilities: tuple[Mo2Capability, ...]
    adapter_state: Mo2AdapterState | None
    adapter_state_sha256: str | None
    coverage: Mo2Coverage
    findings: tuple[Mo2Finding, ...]
```

- [ ] **Step 4: Implement explicit capability gating and plug-in reconciliation**

Create capabilities in this fixed order:

```python
_CAPABILITY_NAMES = (
    "scanner-safety",
    "configuration",
    "game-match",
    "contained-paths",
    "exact-profiles",
    "required-profile-files",
    "primary-plugin-policy",
    "plugin-order-coherence",
    "installed-inventory",
    "overwrite-inventory",
    "stable-read-set",
    "read-only-side-effects",
)
```

Each capability is complete only from explicit evidence. `scanner-safety` requires no scanner `Blocked` finding; it does not reject `Unknown` by itself. Required profile files are checked by case-insensitive basename and both `LocalSaves` and `LocalSettings` must be explicit booleans.

Reconcile one profile with:

```python
def _project_plugins(
    profile: Mo2ProfileEvidence,
    primary_plugins: tuple[str, ...],
) -> tuple[Mo2ProjectedPlugin, ...]:
    load_keys = tuple(item.casefold() for item in profile.load_order)
    primary_keys = tuple(item.casefold() for item in primary_plugins)
    state_keys = tuple(item.name.casefold() for item in profile.plugins)
    core_count = 5
    if load_keys[:core_count] != primary_keys[:core_count]:
        raise Mo2ProjectionError("expected core primary plug-ins are not the load-order prefix")
    if any(key in set(primary_keys) for key in state_keys):
        raise Mo2ProjectionError("plugins.txt contains a primary plug-in state row")
    primary_count = len(load_keys) - len(state_keys)
    observed_creation_keys = load_keys[core_count:primary_count]
    policy_positions = {
        key: index for index, key in enumerate(primary_keys[core_count:])
    }
    if any(key not in policy_positions for key in observed_creation_keys):
        raise Mo2ProjectionError("loadorder.txt has an unexplained load-order-only plug-in")
    positions = tuple(policy_positions[key] for key in observed_creation_keys)
    if positions != tuple(sorted(positions)):
        raise Mo2ProjectionError("installed Creation plug-ins do not follow Skyrim.ccc order")
    if state_keys != load_keys[primary_count:]:
        raise Mo2ProjectionError("plugins.txt does not match non-primary load order")
    states = {item.name.casefold(): item.enabled for item in profile.plugins}
    return tuple(
        Mo2ProjectedPlugin(
            name=name,
            primary=index < primary_count,
            enabled=True if index < primary_count else states[name.casefold()],
            load_order_index=index,
        )
        for index, name in enumerate(profile.load_order)
    )
```

Catch projection errors from either profile, append one `Blocked` finding with code `mo2-plugin-order-evidence-inconsistent`, mark the coherence capability incomplete, and return no adapter state.

Project mods by reversing `profile.mods` first. `*` means `foreign`, a case-insensitive `_separator` suffix means `separator`, and all other rows are `managed`. Increment `runtime_priority` only for non-separators.

- [ ] **Step 5: Canonicalize adapter state independently of observation context**

Implement `adapter_state_to_dict` with camelCase fields and only logical state. Serialize with:

```python
def adapter_state_sha256(state: Mo2AdapterState) -> str:
    encoded = json.dumps(
        adapter_state_to_dict(state),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
```

Do not include roots, active profile, executable, read-set identity, capabilities, findings, or coverage. Pair installed folder names to `meta.ini` evidence by the validated `mods/<name>/meta.ini` relative path.

Use these exact nested adapter-state fields:

```text
adapterState:
  schemaVersion, adapterId, lab, play, installedMods, overwriteEntries
profile:
  name, profileLocalSaves, profileLocalSettings, mods, plugins,
  effectiveLoadOrder, stateFiles
mod:
  name, kind, marker, enabled, listIndex, runtimePriority
plugin:
  name, primary, enabled, loadOrderIndex
installedMod:
  name, metaIni
stateFile:
  relativePath, sha256, size
```

`metaIni` is either the complete state-file object or `null`. Arrays retain
the canonical order established by projection; the serializer never resorts
profile priority or load order.

`project_mo2_state` returns `profileState="complete"` only when every capability is complete. Otherwise it returns `profileState="incomplete"` and null state/hash. Preserve scanner findings in order, then append projection findings in deterministic capability order.

- [ ] **Step 6: Run projection and scanner tests**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_projection tests.test_mo2_scanner -v
```

Expected: all tests PASS, including the primary-only live-state fixture and context-independent hash.

- [ ] **Step 7: Commit projection and readiness gating**

```powershell
git add modlab/adapters/mo2/projection.py tests/test_mo2_projection.py
git commit -m "feat: project canonical MO2 profile state"
```

---

### Task 4: Compare Lab and Play with Placement Anchors

**Files:**
- Create: `modlab/adapters/mo2/comparison.py`
- Create: `tests/test_mo2_comparison.py`
- Test: `tests/test_mo2_projection.py`

**Interfaces:**
- Consumes: `Mo2Projection`.
- Produces: `compare_mo2_profiles(projection: Mo2Projection) -> Mo2ComparisonReport`.
- Produces: complete filtered order sequences in `Mo2OrderDifference` and exact placement through `Mo2EntryPosition`.

- [ ] **Step 1: Write placement, enablement, and order tests**

Create `tests/test_mo2_comparison.py` using projected profile builders. Cover the three required insertion cases:

Use a test-local input alias and helper with this exact contract:

```python
ModSpec = str | tuple[str, bool]

def make_projection(
    *,
    play_mods: tuple[ModSpec, ...] = (),
    lab_mods: tuple[ModSpec, ...] = (),
    play_plugins: tuple[ModSpec, ...] = (),
    lab_plugins: tuple[ModSpec, ...] = (),
    play_state_files: tuple[Mo2StateFileEvidence, ...] = (),
    lab_state_files: tuple[Mo2StateFileEvidence, ...] = (),
    installed_mods: tuple[Mo2InstalledModState, ...] = (),
    overwrite_entries: tuple[str, ...] = (),
    blocked: bool = False,
) -> Mo2Projection:
```

Strings mean enabled entries; pairs carry explicit enabled state. The helper
assigns passed order as UI/load order, uses managed mods and non-primary
plug-ins, creates complete capabilities/coverage when `blocked=False`, and
creates null state/hash plus incomplete coverage when `blocked=True`.

```python
def test_added_mod_keeps_placement_without_false_reorder(self):
    report = compare_mo2_profiles(make_projection(
        play_mods=("A", "B"),
        lab_mods=("A", "X", "B"),
    ))
    change = report.differences.mod_changes[0]
    self.assertEqual("X", change.name)
    self.assertEqual("added-in-lab", change.change)
    self.assertEqual(1, change.lab_position.sequence_index)
    self.assertEqual("A", change.lab_position.previous_shared_anchor)
    self.assertEqual("B", change.lab_position.next_shared_anchor)
    self.assertFalse(report.differences.mod_order.changed)

def test_removed_mod_reports_play_placement(self):
    report = compare_mo2_profiles(make_projection(
        play_mods=("A", "X", "B"),
        lab_mods=("A", "B"),
    ))
    change = report.differences.mod_changes[0]
    self.assertEqual("removed-from-lab", change.change)
    self.assertEqual(1, change.play_position.sequence_index)
    self.assertEqual("A", change.play_position.previous_shared_anchor)
    self.assertEqual("B", change.play_position.next_shared_anchor)

def test_newly_enabled_mod_reports_both_positions(self):
    report = compare_mo2_profiles(make_projection(
        play_mods=(("A", True), ("X", False), ("B", True)),
        lab_mods=(("A", True), ("X", True), ("B", True)),
    ))
    change = report.differences.mod_changes[0]
    self.assertEqual("enabled-in-lab", change.change)
    self.assertIsNotNone(change.play_position)
    self.assertIsNotNone(change.lab_position)
    self.assertEqual("A", change.lab_position.previous_shared_anchor)
    self.assertEqual("B", change.lab_position.next_shared_anchor)
```

Mirror these tests for plug-ins. Add tests that:

- separators report membership but have `runtime_priority=None` and never act as anchors;
- `A,B,C` versus `B,A,C` reports two differing positions, first divergence `0`, and both complete filtered sequences;
- config comparison excludes `modlist.txt`, `plugins.txt`, and `loadorder.txt` but includes changed `settings.ini`/`SkyrimPrefs.ini`;
- a blocked projection yields null state/hash/differences and retains findings;
- installed mods and overwrite entries become explicit shared-state observations;
- an otherwise identical result appends a no-in-scope-differences finding without claiming payload equality.

Use `mo2-shared-installed-content-not-fingerprinted` for the shared installed
folder warning and `mo2-profile-state-no-differences` for a Ready result with
no in-scope difference. Reuse the scanner's existing overwrite finding rather
than adding a duplicate code.

- [ ] **Step 2: Run comparison tests and confirm the red state**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_comparison -v
```

Expected: FAIL with `ModuleNotFoundError` for `modlab.adapters.mo2.comparison`.

- [ ] **Step 3: Define exact comparison values**

Add:

```python
@dataclass(frozen=True)
class Mo2EntryPosition:
    sequence_index: int
    runtime_priority: int | None
    previous_shared_anchor: str | None
    next_shared_anchor: str | None


@dataclass(frozen=True)
class Mo2EntryChange:
    name: str
    entry_kind: str
    change: str
    play_position: Mo2EntryPosition | None
    lab_position: Mo2EntryPosition | None


@dataclass(frozen=True)
class Mo2OrderDifference:
    changed: bool
    differing_position_count: int
    first_divergence: int | None
    play_sequence: tuple[str, ...]
    lab_sequence: tuple[str, ...]


@dataclass(frozen=True)
class Mo2ConfigurationDifferences:
    lab_only: tuple[str, ...]
    play_only: tuple[str, ...]
    content_changed: tuple[str, ...]


@dataclass(frozen=True)
class Mo2Differences:
    mod_changes: tuple[Mo2EntryChange, ...]
    mod_order: Mo2OrderDifference
    plugin_changes: tuple[Mo2EntryChange, ...]
    plugin_order: Mo2OrderDifference
    configuration: Mo2ConfigurationDifferences


@dataclass(frozen=True)
class Mo2SharedStateObservations:
    overwrite_entries: tuple[str, ...]
    installed_mod_folders: tuple[str, ...]
    installed_payload_content: str


@dataclass(frozen=True)
class Mo2ComparisonReport:
    schema_version: int
    adapter_id: str
    readiness: Mo2Readiness
    direction: str
    observation_context: Mo2ObservationContext
    capabilities: tuple[Mo2Capability, ...]
    adapter_state: Mo2AdapterState | None
    adapter_state_sha256: str | None
    differences: Mo2Differences | None
    observations: Mo2SharedStateObservations
    coverage: Mo2Coverage
    findings: tuple[Mo2Finding, ...]
```

- [ ] **Step 4: Implement deterministic membership, placement, and order algorithms**

Use case-insensitive maps but retain observed target spelling. Changes are ordered by:

1. `added-in-lab`/`enabled-in-lab` in Lab sequence order;
2. `removed-from-lab`/`disabled-in-lab` in Play sequence order.

For anchors, build the baseline from entries present, runtime-classified, and enabled in both profiles. For a position, search backward and forward through that profile's complete sequence until a baseline identity is found.
For plug-ins, set `sequence_index` to `load_order_index` and
`runtime_priority` to `None`. For mods, set `sequence_index` to `list_index`
and retain the projected runtime priority.

For order comparison:

```python
def _order_difference(
    play_sequence: tuple[str, ...],
    lab_sequence: tuple[str, ...],
) -> Mo2OrderDifference:
    changed_positions = tuple(
        index
        for index, (play, lab) in enumerate(zip(play_sequence, lab_sequence))
        if play.casefold() != lab.casefold()
    )
    return Mo2OrderDifference(
        changed=bool(changed_positions),
        differing_position_count=len(changed_positions),
        first_divergence=changed_positions[0] if changed_positions else None,
        play_sequence=play_sequence,
        lab_sequence=lab_sequence,
    )
```

The two filtered order sequences contain only runtime entries enabled and present in both profiles, so their membership and length are equal.

Compare state files by basename. Exclude only the three semantic list files. Sort configuration filenames case-insensitively.

- [ ] **Step 5: Run comparison and projection tests**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_comparison tests.test_mo2_projection -v
```

Expected: all tests PASS with no index-shift false positives.

- [ ] **Step 6: Commit the difference engine**

```powershell
git add modlab/adapters/mo2/comparison.py tests/test_mo2_comparison.py
git commit -m "feat: compare MO2 Lab and Play state"
```

---

### Task 5: Strict JSON and Bounded Text Serialization

**Files:**
- Create: `modlab/adapters/mo2/comparison_serialization.py`
- Create: `tests/test_mo2_comparison_serialization.py`
- Test: `tests/test_mo2_comparison.py`

**Interfaces:**
- Consumes: `Mo2ComparisonReport` and `adapter_state_to_dict`.
- Produces: `comparison_result_to_dict`, `comparison_result_from_dict`, `comparison_result_to_json`, `comparison_result_from_json`, and `comparison_result_to_text`.
- Produces: `Mo2ComparisonFormatError(ValueError)`.

- [ ] **Step 1: Write strict round-trip and rejection tests**

Create a complete Ready result and assert:

Define `make_comparison_report() -> Mo2ComparisonReport` by passing a complete
projection from Task 4 to `compare_mo2_profiles`. Define
`make_large_order_difference(*, size: int, first_divergence: int) ->
Mo2ComparisonReport` with shared names `Entry-00` through `Entry-29`, swapping
the entries at `first_divergence` and `first_divergence + 1` only in Lab.

```python
def test_ready_result_round_trip_is_canonical_and_hash_checked(self):
    report = make_comparison_report()
    mapping = comparison_result_to_dict(report)
    self.assertEqual(report, comparison_result_from_dict(mapping))
    encoded = comparison_result_to_json(report)
    restored = comparison_result_from_json(encoded)
    result = json.loads(encoded)

    self.assertEqual(report, restored)
    self.assertEqual(encoded, comparison_result_to_json(restored))
    self.assertEqual([], result["actionsPerformed"])
    self.assertEqual([], result["downloadsPerformed"])
    self.assertEqual([], result["installationActionsPerformed"])
    self.assertEqual([], result["programsLaunched"])
    self.assertEqual(
        adapter_state_sha256(report.adapter_state),
        result["managerComparison"]["adapterStateSha256"],
    )
```

Add explicit rejection subtests for:

- an unknown field at every object level;
- a duplicate JSON key;
- schema version other than integer `1`;
- wrong adapter/direction/readiness/coverage strings;
- duplicate case-insensitive mod, plug-in, installed-folder, capability, or finding identities;
- a Ready result with null state/hash/differences;
- a Blocked result with non-null state/hash/differences;
- a mismatched adapter-state SHA-256;
- a position index outside its referenced profile sequence;
- an anchor that is not in the shared enabled baseline;
- inconsistent order metadata;
- a non-empty side-effect array.

Add text tests:

```python
def test_text_order_output_is_bounded_to_three_neighbors(self):
    report = make_large_order_difference(size=30, first_divergence=12)
    text = comparison_result_to_text(report)
    self.assertIn("First divergence: 12", text)
    self.assertIn("Use --format json for complete sequences.", text)
    self.assertLessEqual(text.count("Play["), 7)
    self.assertLessEqual(text.count("Lab["), 7)
    self.assertTrue(text.rstrip().endswith(
        "Nothing was launched, changed, installed, repaired, promoted, or downloaded."
    ))
```

- [ ] **Step 2: Run serialization tests and confirm the red state**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_comparison_serialization -v
```

Expected: FAIL with `ModuleNotFoundError` for `comparison_serialization`.

- [ ] **Step 3: Implement the exact top-level JSON contract**

Serialize this fixed blocked-result shape; Ready uses the complete
`adapter_state_to_dict` mapping defined in Task 3:

```json
{
  "schemaVersion": 1,
  "managerComparison": {
    "schemaVersion": 1,
    "adapterId": "portable-mo2-skyrim",
    "readiness": "Blocked",
    "direction": "Play-to-Lab",
    "observationContext": {
      "mo2Root": "C:\\ModLab\\workspace\\tools\\mo2\\skyrim-se-ae\\app",
      "gameRoot": "C:\\Steam\\steamapps\\common\\Skyrim Special Edition",
      "activeProfile": null,
      "executable": null,
      "configuredPaths": [],
      "readSetSha256": null,
      "readSetStable": false
    },
    "capabilities": [
      {"name": "scanner-safety", "status": "incomplete", "detail": "Scanner evidence is blocked."},
      {"name": "configuration", "status": "incomplete", "detail": "Configuration evidence is incomplete."},
      {"name": "game-match", "status": "incomplete", "detail": "Game-match evidence is incomplete."},
      {"name": "contained-paths", "status": "incomplete", "detail": "Path evidence is incomplete."},
      {"name": "exact-profiles", "status": "incomplete", "detail": "Profile identity evidence is incomplete."},
      {"name": "required-profile-files", "status": "incomplete", "detail": "Required profile files are incomplete."},
      {"name": "primary-plugin-policy", "status": "incomplete", "detail": "Primary plug-in evidence is incomplete."},
      {"name": "plugin-order-coherence", "status": "incomplete", "detail": "Plug-in order evidence is incomplete."},
      {"name": "installed-inventory", "status": "incomplete", "detail": "Installed inventory is incomplete."},
      {"name": "overwrite-inventory", "status": "incomplete", "detail": "Overwrite inventory is incomplete."},
      {"name": "stable-read-set", "status": "incomplete", "detail": "The read set was not stable."},
      {"name": "read-only-side-effects", "status": "complete", "detail": "Side-effect collections are empty."}
    ],
    "adapterState": null,
    "adapterStateSha256": null,
    "differences": null,
    "observations": {
      "overwriteEntries": [],
      "installedModFolders": [],
      "installedPayloadContent": "not-inspected"
    },
    "coverage": {
      "profileState": "incomplete",
      "installedPayloadContent": "not-inspected",
      "assetConflicts": "not-inspected",
      "pluginRecordConflicts": "not-inspected",
      "runtimeValidation": "not-performed"
    },
    "findings": [
      {
        "state": "Blocked",
        "code": "mo2-root-outside-workspace",
        "message": "Portable MO2 root is not safely contained by ModLab."
      }
    ]
  },
  "actionsPerformed": [],
  "downloadsPerformed": [],
  "installationActionsPerformed": [],
  "programsLaunched": []
}
```

Use exact-field validators and
`json.loads(text, object_pairs_hook=_unique_object)`. Require all twelve
capability names exactly once and require at least one finding. Recompute the
state hash during both parse and serialization for every Ready result.

Use these exact difference fields:

```text
entryChange:
  name, entryKind, change, playPosition, labPosition
position:
  sequenceIndex, runtimePriority, previousSharedAnchor, nextSharedAnchor
orderDifference:
  changed, differingPositionCount, firstDivergence, playSequence, labSequence
configuration:
  labOnly, playOnly, contentChanged
```

`playPosition` and `labPosition` are complete position objects or `null`.
Allowed change values are exactly `added-in-lab`, `removed-from-lab`,
`enabled-in-lab`, and `disabled-in-lab`. Allowed entry kinds are exactly
`managed`, `foreign`, `separator`, and `plugin`.

- [ ] **Step 4: Validate cross-references and readiness invariants**

After parsing nested values:

```python
if readiness is Mo2Readiness.READY:
    if adapter_state is None or differences is None or adapter_hash is None:
        raise Mo2ComparisonFormatError(
            "Ready comparison requires adapterState, adapterStateSha256, and differences"
        )
    if adapter_state_sha256(adapter_state) != adapter_hash:
        raise Mo2ComparisonFormatError("adapterStateSha256 does not match adapterState")
    if coverage.profile_state != "complete":
        raise Mo2ComparisonFormatError("Ready comparison requires complete profileState")
else:
    if any(value is not None for value in (
        adapter_state, adapter_hash, differences
    )):
        raise Mo2ComparisonFormatError(
            "Blocked comparison requires null state, hash, and differences"
        )
    if coverage.profile_state != "incomplete":
        raise Mo2ComparisonFormatError("Blocked comparison requires incomplete profileState")
```

Validate positions against the full projected profile sequences, `runtimePriority` against its projected mod, anchors against the shared enabled baseline, and order metadata by recomputation.

- [ ] **Step 5: Format concise text with a fixed seven-entry order window**

`comparison_result_to_text` starts with exactly one of:

```text
Ready: all required MO2 profile-state evidence was compared.
Blocked: required MO2 profile-state evidence is incomplete or incoherent.
```

For each changed order, render differing-position count, first divergence, indices from `max(0, first - 3)` through `min(length, first + 4)`, then `Use --format json for complete sequences.` Never print full long sequences.

When no in-scope difference exists but installed folders do, print the three lines required by the spec. Always print coverage limits and finish with the exact no-side-effects sentence tested above.

- [ ] **Step 6: Run serialization and comparison tests**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_comparison_serialization tests.test_mo2_comparison -v
```

Expected: all tests PASS, including strict rejection and bounded output.

- [ ] **Step 7: Commit strict output**

```powershell
git add modlab/adapters/mo2/comparison_serialization.py tests/test_mo2_comparison_serialization.py
git commit -m "feat: serialize MO2 comparison results"
```

---

### Task 6: Expose the Read-Only CLI

**Files:**
- Modify: `modlab/cli.py:11-13, 76-96, 389-461, 738-741`
- Create: `tests/test_mo2_compare_cli.py`
- Modify: `tests/test_mo2_cli.py:52-145`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `inspect_skyrim_mo2` → `project_mo2_state` → `compare_mo2_profiles` → serializer.
- Produces: `python -B -m modlab manager compare mo2 --root PATH --game-root PATH --workspace PATH --format text|json`.
- Produces: exit `0` for Ready, `3` for Blocked, and `2` for argument/data-format errors.

- [ ] **Step 1: Write end-to-end CLI tests**

Create `tests/test_mo2_compare_cli.py` with a realistic temporary portable layout that includes all four required files, five core primary masters, an empty `Skyrim.ccc`, explicit `LocalSaves=false`/`LocalSettings=true`, and no installed payload content.

Test Ready JSON:

```python
code = main([
    "manager", "compare", "mo2",
    "--root", str(layout.skyrim_mo2_app),
    "--game-root", str(game_root),
    "--workspace", str(layout.root),
    "--format", "json",
], stdout, stderr)
result = json.loads(stdout.getvalue())
self.assertEqual(0, code)
self.assertEqual("Ready", result["managerComparison"]["readiness"])
self.assertEqual("Play-to-Lab", result["managerComparison"]["direction"])
self.assertEqual("complete", result["managerComparison"]["coverage"]["profileState"])
self.assertEqual([], result["actionsPerformed"])
self.assertEqual(before, self.file_bytes(layout.root))
self.assertEqual("", stderr.getvalue())
```

Add tests for:

- a Lab-only mod inserted between two anchors in JSON;
- a non-primary plug-in mismatch returning `3` with null state/hash/differences;
- missing required `settings.ini` returning `3`;
- unknown executable version remaining Ready;
- text containing the Ready line, placement, coverage limits, and final no-side-effects sentence;
- a 30-entry reorder producing a bounded text window;
- no cache/log/result/checkpoint/temp file added beneath workspace;
- current `manager discover mo2 --format json` retaining its original exact field set.

- [ ] **Step 2: Run CLI tests and confirm the red state**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_compare_cli -v
```

Expected: argparse exits because `manager compare` does not exist.

- [ ] **Step 3: Add the compare parser without altering discover arguments**

Under `manager_commands` add:

```python
manager_compare = manager_commands.add_parser(
    "compare",
    help="compare exact MO2 Lab and Play profile state read-only",
)
manager_compare.add_argument("manager_key", choices=("mo2",))
manager_compare.add_argument("--root", required=True, type=Path)
manager_compare.add_argument("--game-root", required=True, type=Path)
manager_compare.add_argument("--workspace", type=Path, default=None)
manager_compare.add_argument(
    "--format", choices=("text", "json"), default="text"
)
```

- [ ] **Step 4: Wire the pure pipeline and stable exits**

Before the existing discover branch, add:

```python
if args.command == "manager" and args.manager_command == "compare":
    workspace_root = (
        args.workspace if args.workspace is not None else default_workspace_root()
    )
    inspection = inspect_skyrim_mo2(
        args.root,
        args.game_root,
        workspace_root=workspace_root,
    )
    projection = project_mo2_state(inspection)
    comparison = compare_mo2_profiles(projection)
    if args.format == "json":
        _write_json(output, comparison_result_to_dict(comparison))
    else:
        print(comparison_result_to_text(comparison), file=output)
    return 0 if comparison.readiness is Mo2Readiness.READY else 3
```

Import the new functions and catch `Mo2ComparisonFormatError` separately:

```python
except Mo2ComparisonFormatError as error:
    print(f"Manager comparison error: {error}", file=errors)
    return 2
```

Do not call `workspace init`, create an export, persist JSON, or open any program.

- [ ] **Step 5: Run focused CLI and regression tests**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest tests.test_mo2_compare_cli tests.test_mo2_cli tests.test_cli -v
```

Expected: all tests PASS and discover behavior remains unchanged.

- [ ] **Step 6: Commit the command**

```powershell
git add modlab/cli.py tests/test_mo2_compare_cli.py tests/test_mo2_cli.py
git commit -m "feat: expose read-only MO2 profile comparison"
```

---

### Task 7: Full Regression, Live Read-Only Validation, and User Documentation

**Files:**
- Modify: `README.md:5-37, 179-205`
- Test: all `tests/test_*.py`
- Validate: live portable MO2 and Skyrim installation

**Interfaces:**
- Consumes: completed `manager compare mo2` command.
- Produces: verified documentation and the evidence required for branch integration and publishing.

- [ ] **Step 1: Run the complete test suite from the worktree**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest discover -s tests -v
```

Expected: every test PASS with zero failures and zero errors.

- [ ] **Step 2: Verify source formatting and repository cleanliness**

Run:

```powershell
git diff --check
$markerPattern = '\b(T' + 'BD|T' + 'ODO|F' + 'IXME|X' + 'XX)\b'
rg -n -i $markerPattern modlab tests README.md
git status --short
```

Expected: `git diff --check` exits `0`, the marker search returns no matches,
and the worktree is clean before live validation.

- [ ] **Step 3: Capture live state in memory, run comparison, and prove no mutation**

From the worktree, run this single PowerShell validation. It stores comparison evidence only in process memory:

```powershell
$pythonPath = 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$mainRoot = 'C:\Users\red\Desktop\Modlab'
$workspaceRoot = Join-Path $mainRoot 'workspace'
$mo2Root = Join-Path $workspaceRoot 'tools\mo2\skyrim-se-ae\app'
$instanceRoot = Split-Path $mo2Root -Parent
$gameRoot = 'C:\Users\red\Desktop\Steam\steamapps\common\Skyrim Special Edition'
$profileRoots = @(
    (Join-Path $instanceRoot 'profiles\ModLab - Lab'),
    (Join-Path $instanceRoot 'profiles\ModLab - Play')
)
$authoritativeFiles = @(
    (Join-Path $mo2Root 'ModOrganizer.ini'),
    (Join-Path $mo2Root 'ModOrganizer.exe'),
    (Join-Path $gameRoot 'Skyrim.ccc')
) + @(
    $profileRoots | ForEach-Object {
        Get-ChildItem -LiteralPath $_ -File |
            Where-Object Name -In @(
                'archives.txt', 'initweaks.ini', 'loadorder.txt',
                'lockedorder.txt', 'modlist.txt', 'plugins.txt',
                'settings.ini', 'Skyrim.ini', 'SkyrimCustom.ini',
                'SkyrimPrefs.ini'
            ) | Select-Object -ExpandProperty FullName
    }
) + @(
    Get-ChildItem -LiteralPath (Join-Path $instanceRoot 'mods') -Directory |
        ForEach-Object {
            $metadataPath = Join-Path $_.FullName 'meta.ini'
            if (Test-Path -LiteralPath $metadataPath -PathType Leaf) {
                $metadataPath
            }
        }
)
$authoritativeFiles = $authoritativeFiles |
    Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
    Sort-Object -Unique
$modRoots = Get-ChildItem -LiteralPath (Join-Path $instanceRoot 'mods') -Directory |
    Select-Object -ExpandProperty FullName
$directoryRoots = @(
    (Join-Path $instanceRoot 'profiles')
    $profileRoots
    (Join-Path $instanceRoot 'mods')
    (Join-Path $instanceRoot 'overwrite')
) + $modRoots
$beforeFiles = $authoritativeFiles | ForEach-Object {
    $hash = Get-FileHash -LiteralPath $_ -Algorithm SHA256
    "$($_)|$($hash.Hash)|$((Get-Item -LiteralPath $_).Length)"
}
$beforeDirectories = $directoryRoots | ForEach-Object {
    $root = $_
    Get-ChildItem -LiteralPath $root -Force | ForEach-Object {
        "$root|$($_.Name)|$($_.PSIsContainer)|$(($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)"
    }
}
$jsonText = & $pythonPath -B -m modlab manager compare mo2 `
    --root $mo2Root `
    --game-root $gameRoot `
    --workspace $workspaceRoot `
    --format json
if ($LASTEXITCODE -ne 0) {
    throw "Live comparison exited $LASTEXITCODE"
}
$result = ($jsonText -join [Environment]::NewLine) | ConvertFrom-Json
if ($result.managerComparison.readiness -ne 'Ready') {
    throw "Live comparison was not Ready"
}
$afterFiles = $authoritativeFiles | ForEach-Object {
    $hash = Get-FileHash -LiteralPath $_ -Algorithm SHA256
    "$($_)|$($hash.Hash)|$((Get-Item -LiteralPath $_).Length)"
}
$afterDirectories = $directoryRoots | ForEach-Object {
    $root = $_
    Get-ChildItem -LiteralPath $root -Force | ForEach-Object {
        "$root|$($_.Name)|$($_.PSIsContainer)|$(($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)"
    }
}
if (Compare-Object $beforeFiles $afterFiles) {
    throw "Authoritative file state changed"
}
if (Compare-Object $beforeDirectories $afterDirectories) {
    throw "Authoritative directory state changed"
}
"Ready; adapterStateSha256=$($result.managerComparison.adapterStateSha256)"
```

Expected: exit `0` and one `Ready; adapterStateSha256=` line. No compared file or directory listing changes.

- [ ] **Step 4: Run the live text form and inspect bounded, honest wording**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m modlab manager compare mo2 `
  --root 'C:\Users\red\Desktop\Modlab\workspace\tools\mo2\skyrim-se-ae\app' `
  --game-root 'C:\Users\red\Desktop\Steam\steamapps\common\Skyrim Special Edition' `
  --workspace 'C:\Users\red\Desktop\Modlab\workspace'
```

Expected: exit `0`, the Ready sentence, any real Lab/Play config differences such as `archives.txt` reported without a compatibility claim, coverage limits, and the final no-side-effects sentence.

- [ ] **Step 5: Document the command only after live validation succeeds**

Update the README current-build paragraph to say ModLab can compare exact Lab and Play MO2 profile state read-only. Add:

```powershell
python -B -m modlab manager compare mo2 --root '.\workspace\tools\mo2\skyrim-se-ae\app' --game-root 'C:\Path\To\Skyrim Special Edition' --workspace '.\workspace'
python -B -m modlab manager compare mo2 --root '.\workspace\tools\mo2\skyrim-se-ae\app' --game-root 'C:\Path\To\Skyrim Special Edition' --workspace '.\workspace' --format json
```

State plainly that it:

- compares MO2 priority, enablement, plug-in order, and profile configuration;
- reports placement anchors for additions/removals/enablement changes;
- blocks on changing or contradictory evidence;
- reads no save/co-save and writes no cache, report, profile, mod, game, or workspace file;
- does not inspect installed payload contents, asset conflicts, plug-in records, or runtime stability.

Change the build-sequence paragraph so the next step is checkpoint use of the proven projection, not projection itself.

- [ ] **Step 6: Re-run the full suite and verify the final worktree**

Run:

```powershell
& 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest discover -s tests -v
git diff --check
git status --short
```

Expected: all tests PASS, diff check exits `0`, and only `README.md` is uncommitted.

- [ ] **Step 7: Commit verified documentation**

```powershell
git add README.md
git commit -m "docs: document verified MO2 profile comparison"
```

- [ ] **Step 8: Hand off for integration and publishing**

Invoke `superpowers:verification-before-completion`, then `superpowers:requesting-code-review`. After review findings are resolved and the full suite/live proof are fresh, invoke `superpowers:finishing-a-development-branch` to integrate into `main`. Push `main` to `https://github.com/indigoxred/ModLab` only after the integrated main worktree is clean and the pushed commit is verified on `origin/main`.

The completion report must include:

- total passing test count;
- live readiness and adapter-state SHA-256;
- confirmation that before/after live state matched;
- integrated commit hash and pushed remote hash;
- the exact coverage limits that remain.
