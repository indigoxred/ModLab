"""Disposable exact-MO2 inputs for the containment capability scenarios."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
from types import MappingProxyType
from typing import Callable, Mapping
import uuid
import zipfile

from modlab.adapters.mo2.bootstrap_model import (
    BootstrapReceiptMode,
    OptionalFileIdentity,
    ProfileSeedEvidence,
)
from modlab.adapters.mo2.bootstrap_config import observe_profile_seed
from modlab.adapters.mo2.archive import observe_bsdtar, preflight_archive
from modlab.adapters.mo2.path_budget import (
    PathBudget, PlannedPath, admit_paths, bootstrap_paths,
)
from modlab.adapters.mo2.release import bundled_mo2_252_path, load_mo2_release
from modlab.adapters.mo2.scanner import inspect_skyrim_mo2
from modlab.adapters.skyrim.primary_plugins import observe_skyrim_primary_plugins
from modlab.adapters.skyrim.windows_version import read_windows_file_version
from modlab.artifacts.vault import ArchiveVault
from modlab.validation.mo2_containment_model import ContainmentScenario
from modlab.validation.windows_integrity import (
    IntegrityLevel,
    inspect_path_integrity,
    set_low_integrity_tree,
    source_integrity_allowed,
    stage_integrity_allowed,
)
from modlab.validation.windows_junction import (
    OwnedProjection, build_projection, create_owned_projection, inspect_junction,
)
from modlab.workflows.skyrim.mo2_bootstrap import (
    apply_mo2_setup, prepare_mo2_setup, _observe_artifact, _require_release_artifact,
)
from modlab.workspace import WorkspaceLayout, workspace_layout


_ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)
_ZIP_MODE = 0o100644 << 16
_PROTECTED_MOD = "Protected Existing"
_ACTIVE_MODLIST = b"+Protected Existing\r\n"
PROTECTED_MOD_FILES = MappingProxyType({
    "marker.txt": b"active-projected-marker\n",
    "meshes/canary.bin": b"source-canary-must-remain\n",
    "meta.ini": b"[General]\ninstallationFile=ModLab\n",
})
SCENARIO_ADOPTION_NAMES = MappingProxyType({
    ContainmentScenario.NEW_FOLDER: "ModLab Spike New",
    ContainmentScenario.MERGE_EXISTING: _PROTECTED_MOD,
    ContainmentScenario.REPLACE_EXISTING: _PROTECTED_MOD,
    ContainmentScenario.FOMOD_DEPENDENCY: "ModLab Spike FOMOD",
})
SCENARIO_OUTPUT_FILES = MappingProxyType({
    ContainmentScenario.NEW_FOLDER: ("meshes/new-folder.bin", "meta.ini"),
    ContainmentScenario.FOMOD_DEPENDENCY: (
        "always.txt",
        "dependency-seen.txt",
        "meta.ini",
    ),
})
REPLACEMENT_OUTPUT_FILES = (
    "meshes/canary.bin",
    "meshes/new.bin",
    "meta.ini",
)
DOCUMENT_SEED_FILES = MappingProxyType({
    "Skyrim.ini": b"[General]\nfixture=true\n",
    "SkyrimPrefs.ini": b"[Display]\nfixture=true\n",
    "SkyrimCustom.ini": b"[Archive]\nfixture=true\n",
})
_FOMOD_INFO = b"""<?xml version="1.0" encoding="UTF-8"?>
<fomod>
  <Name>ModLab FOMOD Dependency Probe</Name>
  <Author>ModLab</Author>
  <Version>1.0</Version>
  <Description>Validates projected active-file dependency visibility.</Description>
</fomod>
"""
_FOMOD_MODULE = b"""<?xml version="1.0" encoding="UTF-8"?>
<config xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
        xsi:noNamespaceSchemaLocation="http://qconsulting.ca/fo3/ModConfig5.0.xsd">
  <moduleName>ModLab FOMOD Dependency Probe</moduleName>
  <requiredInstallFiles>
    <file source="payload\\always.txt" destination="always.txt" priority="0" />
  </requiredInstallFiles>
  <conditionalFileInstalls>
    <patterns>
      <pattern>
        <dependencies operator="And">
          <fileDependency file="marker.txt" state="Active" />
        </dependencies>
        <files>
          <file source="payload\\dependency-seen.txt" destination="dependency-seen.txt" priority="0" />
        </files>
      </pattern>
    </patterns>
  </conditionalFileInstalls>
</config>
"""


class ContainmentFixtureError(RuntimeError):
    """A disposable fixture cannot establish its required containment boundary."""


@dataclass(frozen=True)
class ScenarioArchives:
    new_folder: Path
    overwrite_probe: Path
    fomod_dependency: Path


@dataclass(frozen=True)
class ContainmentFixture:
    scenario: ContainmentScenario
    run_root: Path
    source_workspace: Path
    stage_workspace: Path
    source_layout: WorkspaceLayout
    stage_layout: WorkspaceLayout
    source_mods: Path
    stage_mods: Path
    source_lab_modlist: Path
    source_play_modlist: Path
    stage_lab_modlist: Path
    stage_play_modlist: Path
    stage_cache: Path
    stage_logs: Path
    archives: ScenarioArchives
    source_receipt_id: str
    stage_receipt_id: str
    stage_environment: Mapping[str, str]
    external_watch_roots: tuple[tuple[str, Path], ...]
    projection_owners: tuple[OwnedProjection, ...] = ()


@dataclass(frozen=True)
class FixtureInputSnapshot:
    steam_root: str
    discovery: object
    source_seed: ProfileSeedEvidence
    stage_seed: ProfileSeedEvidence
    release: object
    source_observation: object
    source_record: object
    extractor: object
    listing: object
    external_identities: tuple[object, ...]


@dataclass(frozen=True)
class _FixturePathIdentity:
    path: str
    kind: str
    device: int
    inode: int
    mode: int
    attributes: int
    size: int | None
    modified_ns: int | None
    changed_ns: int | None
    sha256: str | None


@dataclass(frozen=True)
class FixtureAdmission:
    budget: PathBudget
    inputs: FixtureInputSnapshot

    @property
    def paths(self):
        return self.budget.paths

    @property
    def scope(self):
        return self.budget.scope

    @property
    def runtime_qualified(self):
        return self.budget.runtime_qualified


@dataclass(frozen=True)
class _FixtureInputs:
    scenario: ContainmentScenario
    source_layout: WorkspaceLayout
    validation: Path
    source_observation: object
    source_record: object
    payload: Path
    fixture_parent: Path | None
    steam_root: Path


def write_scenario_archives(root: Path) -> ScenarioArchives:
    """Write the three exact, deterministic scenario ZIP inputs beneath *root*."""
    archive_root = _require_direct_directory(Path(root), create=True) / "archives"
    _require_direct_directory(archive_root, create=True)
    new_folder = archive_root / "new-folder.zip"
    overwrite_probe = archive_root / "overwrite-probe.zip"
    fomod_dependency = archive_root / "fomod-dependency.zip"
    _write_zip(new_folder, {"meshes/new-folder.bin": b"modlab-new-folder-v1\n"})
    _write_zip(
        overwrite_probe,
        {
            "meshes/canary.bin": b"MUTATION-MUST-NEVER-REACH-SOURCE\n",
            "meshes/new.bin": b"new\n",
        },
    )
    _write_zip(
        fomod_dependency,
        {
            "fomod/info.xml": _FOMOD_INFO,
            "fomod/ModuleConfig.xml": _FOMOD_MODULE,
            "payload/always.txt": b"always\n",
            "payload/dependency-seen.txt": b"dependency-visible\n",
        },
    )
    return ScenarioArchives(new_folder, overwrite_probe, fomod_dependency)


def prepare_containment_fixture(
    source_workspace: Path,
    mo2_artifact_id: str,
    steam_root: Path,
    validation_root: Path,
    scenario: ContainmentScenario,
    *,
    fixture_parent: Path | None = None,
    retain_projection_owners: bool = False,
    on_projection_created: Callable[[OwnedProjection], None] | None = None,
    command_runner=None,
    version_reader=read_windows_file_version,
    expected_admission: FixtureAdmission | None = None,
) -> ContainmentFixture:
    """Create two disposable portable instances and project the protected source mod."""
    inputs = _fixture_inputs(
        source_workspace,
        mo2_artifact_id,
        validation_root,
        scenario,
        steam_root=steam_root,
        fixture_parent=fixture_parent,
        create_validation=False,
    )
    source_layout = inputs.source_layout
    validation = inputs.validation
    source_record = inputs.source_record
    payload = inputs.payload

    if inputs.fixture_parent is None:
        run_root = validation / ("fixture-v2-" + uuid.uuid4().hex) / scenario.value
    else:
        run_root = inputs.fixture_parent
    # Pure complete path admission precedes even the validation/scenario directory.
    admission = _admit_fixture(
        inputs,
        run_root,
        command_runner=command_runner,
        version_reader=version_reader,
    )
    if expected_admission is not None and admission != expected_admission:
        raise ContainmentFixtureError("fixture immutable input admission changed")
    _require_direct_directory(validation, create=True)
    if inputs.fixture_parent is not None:
        relative_parent = inputs.fixture_parent.relative_to(validation)
        current = validation
        for part in relative_parent.parts:
            current = _require_direct_directory(current / part, create=True)
            _require_beneath(validation, current)
        run_root = current
    run_root = _require_direct_directory(run_root, create=True)
    _require_beneath(validation, run_root)
    bootstrap_archive = _materialize_verified_bootstrap_archive(
        source_record, payload, run_root
    )
    source_instance = run_root / "s"
    stage_instance = run_root / "t"
    source_documents = _write_documents_root(run_root / "source-documents")
    stage_documents = _write_documents_root(run_root / "stage-documents")
    _require_declared_seed(source_documents, admission.inputs.source_seed)
    _require_declared_seed(stage_documents, admission.inputs.stage_seed)
    source_artifact = ArchiveVault(source_instance).import_archive(
        bootstrap_archive, source_note="disposable containment source fixture"
    )
    stage_artifact = ArchiveVault(stage_instance).import_archive(
        bootstrap_archive, source_note="disposable containment stage fixture"
    )

    source_planned = prepare_mo2_setup(
        artifact_id=source_artifact.artifact_id,
        workspace_root=source_instance,
        steam_root=steam_root,
        documents_root=source_documents,
        command_runner=command_runner,
        version_reader=version_reader,
    )
    source_applied = apply_mo2_setup(
        source_planned.plan.plan_id, source_instance, documents_root=source_documents,
        command_runner=command_runner,
        version_reader=version_reader,
    )
    stage_planned = prepare_mo2_setup(
        artifact_id=stage_artifact.artifact_id,
        workspace_root=stage_instance,
        steam_root=steam_root,
        documents_root=stage_documents,
        command_runner=command_runner,
        version_reader=version_reader,
    )
    stage_applied = apply_mo2_setup(
        stage_planned.plan.plan_id, stage_instance, documents_root=stage_documents,
        command_runner=command_runner,
        version_reader=version_reader,
    )
    _require_created_receipt(source_applied)
    _require_created_receipt(stage_applied)

    source_layout = workspace_layout(source_instance)
    stage_layout = workspace_layout(stage_instance)
    _require_beneath(run_root, source_layout.root, stage_layout.root)
    _populate_protected_mod(source_layout)
    _copy_profile_bytes(source_layout.skyrim_mo2_profiles, stage_layout.skyrim_mo2_profiles)
    (stage_layout.skyrim_mo2_app / "nxmhandler.ini").write_bytes(
        b"[General]\r\nnoregister=true\r\n"
    )
    _enable_stage_fomod_file_dependencies(stage_layout.skyrim_mo2_app)

    stage_environment = _prepare_stage_environment(run_root, stage_layout)
    _verify_stage_integrity_before_projection(stage_environment)
    source_report = inspect_skyrim_mo2(
        source_layout.skyrim_mo2_app,
        Path(source_planned.plan.game_root),
        workspace_root=source_layout.root,
    )
    stage_report = inspect_skyrim_mo2(
        stage_layout.skyrim_mo2_app,
        Path(stage_planned.plan.game_root),
        workspace_root=stage_layout.root,
    )
    _require_exact_version(source_report)
    _require_exact_version(stage_report)
    source_level = inspect_path_integrity(source_layout.skyrim_mo2)
    if not source_integrity_allowed(source_level):
        raise ContainmentFixtureError("source instance integrity is not Medium or higher")
    if not stage_integrity_allowed(inspect_path_integrity(stage_layout.skyrim_mo2)):
        raise ContainmentFixtureError("stage instance integrity is not Low")

    archives = write_scenario_archives(run_root)
    external_roots = _external_low_watch_roots()
    owners = ()
    if retain_projection_owners:
        if tuple(path.name for path in source_layout.skyrim_mo2_mods.iterdir()) != (_PROTECTED_MOD,):
            raise ContainmentFixtureError("exactly one protected source mod is required")
        if tuple(stage_layout.skyrim_mo2_mods.iterdir()):
            raise ContainmentFixtureError("projection staging root must be empty")
        owners = (create_owned_projection(
            source_layout.skyrim_mo2_mods / _PROTECTED_MOD,
            stage_layout.skyrim_mo2_mods / _PROTECTED_MOD,
        ),)
        if on_projection_created is not None:
            try:
                on_projection_created(owners[0])
            except BaseException as error:
                error.projection_owners = owners
                raise
    else:
        projections = build_projection(source_layout.skyrim_mo2_mods, stage_layout.skyrim_mo2_mods)
        if len(projections) != 1:
            raise ContainmentFixtureError("exactly one protected mod projection is required")
        projection = inspect_junction(stage_layout.skyrim_mo2_mods / _PROTECTED_MOD)
        if projection.target_path != source_layout.skyrim_mo2_mods / _PROTECTED_MOD:
            raise ContainmentFixtureError("protected mod projection target does not match source")

    return ContainmentFixture(
        scenario=scenario,
        run_root=run_root,
        source_workspace=source_layout.root,
        stage_workspace=stage_layout.root,
        source_layout=source_layout,
        stage_layout=stage_layout,
        source_mods=source_layout.skyrim_mo2_mods,
        stage_mods=stage_layout.skyrim_mo2_mods,
        source_lab_modlist=source_layout.skyrim_mo2_profiles / "ModLab - Lab" / "modlist.txt",
        source_play_modlist=source_layout.skyrim_mo2_profiles / "ModLab - Play" / "modlist.txt",
        stage_lab_modlist=stage_layout.skyrim_mo2_profiles / "ModLab - Lab" / "modlist.txt",
        stage_play_modlist=stage_layout.skyrim_mo2_profiles / "ModLab - Play" / "modlist.txt",
        stage_cache=stage_layout.skyrim_mo2 / "webcache",
        stage_logs=stage_layout.skyrim_mo2 / "logs",
        archives=archives,
        source_receipt_id=source_applied.receipt.receipt_id,
        stage_receipt_id=stage_applied.receipt.receipt_id,
        stage_environment=MappingProxyType(stage_environment),
        external_watch_roots=external_roots,
        projection_owners=owners,
    )


def preflight_containment_fixture(
    source_workspace: Path,
    mo2_artifact_id: str,
    steam_root: Path,
    validation_root: Path,
    scenario: ContainmentScenario,
    *,
    fixture_parent: Path,
    command_runner=None,
    version_reader=read_windows_file_version,
) -> FixtureAdmission:
    """Validate fixture inputs without creating the fixture root or children."""
    inputs = _fixture_inputs(
        source_workspace,
        mo2_artifact_id,
        validation_root,
        scenario,
        steam_root=steam_root,
        fixture_parent=fixture_parent,
        create_validation=False,
    )
    return _admit_fixture(
        inputs,
        Path(fixture_parent),
        command_runner=command_runner,
        version_reader=version_reader,
    )


def _fixture_inputs(
    source_workspace: Path,
    mo2_artifact_id: str,
    validation_root: Path,
    scenario: ContainmentScenario,
    *,
    steam_root: Path,
    fixture_parent: Path | None,
    create_validation: bool,
) -> _FixtureInputs:
    if not isinstance(scenario, ContainmentScenario):
        raise ContainmentFixtureError("scenario must be a ContainmentScenario")
    source_root = _require_direct_directory(Path(source_workspace), create=False)
    source_layout = workspace_layout(source_root)
    requested_validation = Path(validation_root).expanduser().absolute()
    if os.path.normcase(str(requested_validation)) != os.path.normcase(
        str(source_layout.mo2_containment_validation)
    ):
        raise ContainmentFixtureError("validation root must be the workspace containment root")
    validation = _require_future_directory(source_layout.mo2_containment_validation)

    try:
        observed = _observe_artifact(source_layout, mo2_artifact_id)
        source_record = observed.record
    except Exception as error:
        raise ContainmentFixtureError("exact MO2 artifact is unavailable in source vault") from error
    payload = _require_direct_regular_file(
        source_record.stored_path(source_layout.root),
        "retained MO2 archive",
    )

    if fixture_parent is None:
        requested_parent = None
    else:
        requested_parent = Path(fixture_parent).expanduser().absolute()
        try:
            relative_parent = requested_parent.relative_to(validation)
        except ValueError as error:
            raise ContainmentFixtureError(
                "fixture parent must be beneath the workspace containment root"
            ) from error
        if not relative_parent.parts:
            raise ContainmentFixtureError(
                "fixture parent must be beneath the workspace containment root"
            )
        if any(part in {"", ".", ".."} for part in relative_parent.parts):
            raise ContainmentFixtureError("fixture parent contains an unsafe segment")
    return _FixtureInputs(
        scenario,
        source_layout,
        validation,
        observed,
        source_record,
        payload,
        requested_parent,
        Path(steam_root).expanduser().absolute(),
    )


def _require_future_directory(path: Path) -> Path:
    candidate = Path(path).expanduser().absolute()
    admit_paths((PlannedPath("fixture-input-root", "preparation-wide", str(candidate)),))
    for ancestor in reversed((candidate, *candidate.parents)):
        try:
            metadata = ancestor.lstat()
        except FileNotFoundError:
            continue
        if ancestor.is_symlink() or not stat.S_ISDIR(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise ContainmentFixtureError(f"fixture path must be direct: {ancestor}")
    return candidate


def _admit_fixture(
    inputs: _FixtureInputs,
    run_root: Path,
    *,
    command_runner=None,
    version_reader=read_windows_file_version,
):
    """Exact release listing plus bounded generated preparation paths, no writes."""
    from modlab.workspace import _DIRECTORIES
    run_root = _require_future_directory(run_root)
    loaded_release = load_mo2_release(bundled_mo2_252_path())
    release = loaded_release.descriptor
    _require_release_artifact(release, inputs.source_record)
    _require_curated_archive_filename(inputs.source_record.original_name, release.archive_name)
    extractor = observe_bsdtar(runner=command_runner)
    listing = preflight_archive(inputs.payload, release, Path(extractor.executable.path), runner=command_runner)
    repeated_source_observation = _observe_artifact(
        inputs.source_layout,
        inputs.source_record.artifact_id,
    )
    if repeated_source_observation != inputs.source_observation:
        raise ContainmentFixtureError(
            "retained archive input changed during fixture admission"
        )
    discovery = _stable_ready_discovery(inputs.steam_root, version_reader)
    if os.path.normcase(str(Path(discovery.steam_root).absolute())) != os.path.normcase(
        str(inputs.steam_root)
    ):
        raise ContainmentFixtureError(
            "configured Steam input resolved through an unqualified path"
        )
    game_root = Path(discovery.game_root or "")
    source_seed = _declared_profile_seed(game_root, run_root / "source-documents")
    stage_seed = _declared_profile_seed(game_root, run_root / "stage-documents")
    external_paths = [
        (inputs.payload, "file", False),
        (
            inputs.source_layout.metadata
            / "artifacts"
            / f"{inputs.source_record.sha256}.json",
            "file",
            False,
        ),
        (Path(loaded_release.path), "file", False),
        (Path(extractor.executable.path), "file", False),
        (inputs.steam_root, "directory", False),
        (Path(discovery.manifest_path), "file", True),
        (game_root, "directory", False),
        (game_root / "SkyrimSE.exe", "file", False),
        (game_root / "Data", "directory", False),
        *(
            (
                game_root.joinpath(*item.relative_path.split("/")),
                "file",
                False,
            )
            for item in discovery.data_files
        ),
    ]
    if source_seed.skyrim_ccc.present:
        external_paths.append((Path(source_seed.skyrim_ccc.path), "file", True))
    external_identities = tuple(
        _stable_fixture_input_identity(path, kind, hash_bytes=hash_bytes)
        for path, kind, hash_bytes in external_paths
    )
    rows = [
        PlannedPath("fixture-retained-archive-input", "preparation-wide", str(inputs.payload)),
        PlannedPath(
            "fixture-retained-archive-metadata-input",
            "preparation-wide",
            str(
                inputs.source_layout.metadata
                / "artifacts"
                / f"{inputs.source_record.sha256}.json"
            ),
        ),
        PlannedPath("fixture-extractor-input", "preparation-wide", extractor.executable.path),
        PlannedPath("fixture-release-input", "preparation-wide", str(loaded_release.path)),
        PlannedPath("fixture-steam-input", "preparation-wide", str(inputs.steam_root)),
        PlannedPath("fixture-steam-manifest-input", "preparation-wide", discovery.manifest_path),
        PlannedPath("fixture-game-input", "preparation-wide", str(game_root)),
        PlannedPath("fixture-game-executable-input", "preparation-wide", str(game_root / "SkyrimSE.exe")),
        PlannedPath("fixture-game-data-input", "preparation-wide", str(game_root / "Data")),
        PlannedPath("fixture-game-seed-input", "preparation-wide", source_seed.skyrim_ccc.path),
    ]
    rows.extend(
        PlannedPath("fixture-game-data-entry", "preparation-wide", str(game_root.joinpath(*item.relative_path.split("/"))))
        for item in discovery.data_files
    )
    for role in ("s", "t"):
        root = run_root / role
        _require_future_directory(root)
        if root.exists():
            raise ContainmentFixtureError(f"fixture workspace must be fresh: {root}")
        digest = inputs.source_record.sha256
        rows.extend(bootstrap_paths(root, listing,
            archive_path=root / f"library/archives/{digest[:2]}/{digest}/payload.7z"))
        rows.extend(PlannedPath("fixture-workspace", "preparation-wide", str(root / name)) for name in _DIRECTORIES)
        digest = inputs.source_record.sha256
        for name in (f"library/archives/{digest[:2]}/{digest}/payload.7z",
                     f"library/metadata/artifacts/{digest}.json", "runtime/jobs/import-" + "f" * 32 + ".part",
                     "library/metadata/artifacts/metadata." + "f" * 32 + ".part"):
            rows.append(PlannedPath("fixture-vault", "preparation-wide", str(root / name)))
        manager = root / "tools/mo2/skyrim-se-ae"
        for name in (*(f"mods/Protected Existing/{relative}" for relative in PROTECTED_MOD_FILES),
                     "app/nxmhandler.ini", "logs", "webcache"):
            rows.append(PlannedPath("fixture-state", "preparation-wide", str(manager / name)))
        adoption = manager / "mods" / SCENARIO_ADOPTION_NAMES[inputs.scenario]
        rows.append(PlannedPath("fixture-source-adoption", "preparation-wide", str(adoption)))
        adoption_members = (
            SCENARIO_OUTPUT_FILES.get(inputs.scenario, ())
            if inputs.scenario is not ContainmentScenario.REPLACE_EXISTING
            else REPLACEMENT_OUTPUT_FILES
        )
        rows.extend(
            PlannedPath("fixture-source-adoption:member", "preparation-wide", str(adoption / member))
            for member in adoption_members
        )
    for documents in ("source-documents", "stage-documents"):
        for name in ("Skyrim.ini", "SkyrimPrefs.ini", "SkyrimCustom.ini"):
            rows.append(PlannedPath("fixture-seed", "preparation-wide", str(run_root / documents / "My Games/Skyrim Special Edition" / name)))
    for name in ("TEMP", "TMP", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME", "USERPROFILE/Desktop"):
        rows.append(PlannedPath("fixture-environment-root", "preparation-wide", str(run_root / "stage-environment" / name)))
    for name in ("new-folder.zip", "overwrite-probe.zip", "fomod-dependency.zip"):
        rows.append(PlannedPath("fixture-archive", "preparation-wide", str(run_root / "archives" / name)))
    rows.append(PlannedPath("fixture-bootstrap-archive", "preparation-wide", str(run_root / "bootstrap-archive" / release.archive_name)))
    budget = admit_paths(rows)
    return FixtureAdmission(
        budget,
        FixtureInputSnapshot(
            str(inputs.steam_root),
            discovery,
            source_seed,
            stage_seed,
            loaded_release,
            inputs.source_observation,
            inputs.source_record,
            extractor,
            listing,
            external_identities,
        ),
    )


def _stable_ready_discovery(steam_root: Path, version_reader):
    from modlab.workflows.skyrim.mo2_bootstrap import _discover_ready_skyrim

    first = _discover_ready_skyrim(steam_root, version_reader)
    second = _discover_ready_skyrim(steam_root, version_reader)
    if first != second:
        raise ContainmentFixtureError("Steam/game discovery changed during fixture admission")
    return first


def _stable_fixture_input_identity(
    path: Path,
    kind: str,
    *,
    hash_bytes: bool,
) -> _FixturePathIdentity:
    candidate = Path(path).expanduser().absolute()

    def observe() -> _FixturePathIdentity:
        metadata = candidate.lstat()
        attributes = int(getattr(metadata, "st_file_attributes", 0))
        if candidate.is_symlink() or attributes & 0x400:
            raise ContainmentFixtureError(
                f"fixture immutable input is redirected: {candidate}"
            )
        if kind == "directory" and not stat.S_ISDIR(metadata.st_mode):
            raise ContainmentFixtureError(
                f"fixture immutable input is not a directory: {candidate}"
            )
        if kind == "file" and not stat.S_ISREG(metadata.st_mode):
            raise ContainmentFixtureError(
                f"fixture immutable input is not a regular file: {candidate}"
            )
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest() if hash_bytes else None
        after = candidate.lstat()
        is_file = kind == "file"
        # A directory's size and timestamps describe container churn, not the
        # exact children consumed below it.  Bind the directory object and
        # reparse/type state here; each consumed file is admitted separately.
        values = (
            int(metadata.st_dev),
            int(metadata.st_ino),
            int(metadata.st_mode),
            attributes,
            int(metadata.st_size) if is_file else None,
            int(metadata.st_mtime_ns) if is_file else None,
            int(metadata.st_ctime_ns) if is_file else None,
        )
        if values != (
            int(after.st_dev),
            int(after.st_ino),
            int(after.st_mode),
            int(getattr(after, "st_file_attributes", 0)),
            int(after.st_size) if is_file else None,
            int(after.st_mtime_ns) if is_file else None,
            int(after.st_ctime_ns) if is_file else None,
        ):
            raise ContainmentFixtureError(
                f"fixture immutable input changed during observation: {candidate}"
            )
        return _FixturePathIdentity(str(candidate), kind, *values, digest)

    first = observe()
    second = observe()
    if first != second:
        raise ContainmentFixtureError(
            f"fixture immutable input changed between observations: {candidate}"
        )
    return first


def _declared_profile_seed(game_root: Path, documents_root: Path) -> ProfileSeedEvidence:
    primary = observe_skyrim_primary_plugins(game_root)
    ini_root = documents_root / "My Games" / "Skyrim Special Edition"
    sources = tuple(
        OptionalFileIdentity(
            str(ini_root / name),
            True,
            hashlib.sha256(data).hexdigest(),
            len(data),
        )
        for name, data in DOCUMENT_SEED_FILES.items()
    )
    return ProfileSeedEvidence(
        str(documents_root),
        primary.plugins,
        OptionalFileIdentity(
            primary.ccc_path,
            primary.ccc_present,
            primary.ccc_sha256,
            primary.ccc_size,
        ),
        sources,
    )


def _require_declared_seed(documents_root: Path, expected: ProfileSeedEvidence) -> None:
    try:
        observed = observe_profile_seed(Path(expected.skyrim_ccc.path).parent, documents_root)
    except Exception as error:
        raise ContainmentFixtureError("declared fixture profile seed is unavailable") from error
    if observed != expected:
        raise ContainmentFixtureError("declared fixture profile seed changed")


def _write_zip(destination: Path, entries: dict[str, bytes]) -> None:
    for name in entries:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or path.as_posix() != name:
            raise ContainmentFixtureError(f"unsafe archive fixture member: {name}")
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.comment = b""
        for name in sorted(entries):
            info = zipfile.ZipInfo(name, _ZIP_TIMESTAMP)
            info.create_system = 3
            info.external_attr = _ZIP_MODE
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, entries[name])


def _require_direct_directory(path: Path, *, create: bool) -> Path:
    candidate = Path(path).expanduser().absolute()
    if create:
        candidate.mkdir(parents=True, exist_ok=True)
    try:
        metadata = candidate.lstat()
    except OSError as error:
        raise ContainmentFixtureError(f"fixture directory is unavailable: {candidate}") from error
    if candidate.is_symlink() or not candidate.is_dir() or bool(getattr(metadata, "st_file_attributes", 0) & 0x400):
        raise ContainmentFixtureError(f"fixture directory must be direct: {candidate}")
    return candidate.resolve(strict=True)


def _materialize_verified_bootstrap_archive(
    source_record, payload: Path, run_root: Path
) -> Path:
    """Copy the retained MO2 archive into the disposable run under its original name."""
    release = load_mo2_release(bundled_mo2_252_path()).descriptor
    name = _require_curated_archive_filename(
        source_record.original_name, release.archive_name
    )
    source = _require_direct_regular_file(Path(payload), "retained MO2 archive")
    destination_root = _require_direct_directory(
        run_root / "bootstrap-archive", create=True
    )
    _require_beneath(run_root, destination_root)
    destination = destination_root / name

    digest = hashlib.sha256()
    size = 0
    destination_created = False
    destination_identity: tuple[int, int] | None = None
    try:
        with source.open("rb") as reader, destination.open("xb") as writer:
            destination_created = True
            destination_identity = _file_identity(os.fstat(writer.fileno()))
            while chunk := reader.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
                writer.write(chunk)
    except OSError as error:
        if destination_created:
            try:
                _cleanup_owned_partial(destination, destination_identity)
            except ContainmentFixtureError as cleanup_error:
                raise ContainmentFixtureError(
                    "could not materialize disposable MO2 archive and could not clean "
                    "its partial copy"
                ) from cleanup_error
        raise ContainmentFixtureError(
            "could not materialize disposable MO2 archive"
        ) from error

    if size != source_record.size or digest.hexdigest() != source_record.sha256:
        try:
            _cleanup_owned_partial(destination, destination_identity)
        except ContainmentFixtureError as cleanup_error:
            raise ContainmentFixtureError(
                "retained MO2 archive changed during materialization and its partial "
                "copy could not be cleaned"
            ) from cleanup_error
        raise ContainmentFixtureError(
            "retained MO2 archive changed during materialization"
        )
    return _require_direct_regular_file(destination, "disposable MO2 archive")


def _require_curated_archive_filename(name: object, expected_name: str) -> str:
    if name != expected_name:
        raise ContainmentFixtureError(
            "retained MO2 archive does not have the curated original name"
        )
    return expected_name


def _file_identity(metadata) -> tuple[int, int]:
    return (metadata.st_dev, metadata.st_ino)


def _cleanup_owned_partial(
    destination: Path, expected_identity: tuple[int, int] | None
) -> None:
    if expected_identity is None:
        raise ContainmentFixtureError("owned partial MO2 archive has no file identity")
    try:
        current_identity = _file_identity(destination.lstat())
    except FileNotFoundError:
        return
    except OSError as error:
        raise ContainmentFixtureError(
            "could not inspect owned partial MO2 archive"
        ) from error
    if current_identity != expected_identity:
        raise ContainmentFixtureError("owned partial MO2 archive was replaced")
    try:
        destination.unlink()
    except OSError as error:
        raise ContainmentFixtureError("could not remove owned partial MO2 archive") from error


def _require_direct_regular_file(path: Path, description: str) -> Path:
    candidate = Path(path).expanduser().absolute()
    try:
        metadata = candidate.lstat()
    except OSError as error:
        raise ContainmentFixtureError(f"{description} is unavailable: {candidate}") from error
    if (
        candidate.is_symlink()
        or bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
        or not stat.S_ISREG(metadata.st_mode)
    ):
        raise ContainmentFixtureError(f"{description} must be a direct regular file")
    return candidate.resolve(strict=True)


def _write_documents_root(root: Path) -> Path:
    documents = _require_direct_directory(root, create=True)
    ini = documents / "My Games" / "Skyrim Special Edition"
    ini.mkdir(parents=True, exist_ok=True)
    for name, data in DOCUMENT_SEED_FILES.items():
        (ini / name).write_bytes(data)
    return documents


def _enable_stage_fomod_file_dependencies(app: Path) -> None:
    manager_ini = _require_direct_regular_file(
        Path(app) / "ModOrganizer.ini", "disposable stage ModOrganizer.ini"
    )
    content = manager_ini.read_bytes()
    if content and not content.endswith(b"\n"):
        content += b"\n"
    manager_ini.write_bytes(
        content + b"[Plugins]\nFomod%20Installer\\use_any_file=true\n"
    )


def _require_created_receipt(applied) -> None:
    if applied.receipt.mode is not BootstrapReceiptMode.CREATED:
        raise ContainmentFixtureError("disposable MO2 bootstrap did not create an instance")


def _require_beneath(root: Path, *paths: Path) -> None:
    for path in paths:
        try:
            path.resolve(strict=True).relative_to(root)
        except (OSError, ValueError) as error:
            raise ContainmentFixtureError(f"fixture path escapes scenario run: {path}") from error


def _populate_protected_mod(layout: WorkspaceLayout) -> None:
    protected = layout.skyrim_mo2_mods / _PROTECTED_MOD
    protected.mkdir(parents=True, exist_ok=False)
    for relative, data in PROTECTED_MOD_FILES.items():
        destination = protected.joinpath(*PurePosixPath(relative).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    for profile in ("ModLab - Lab", "ModLab - Play"):
        (layout.skyrim_mo2_profiles / profile / "modlist.txt").write_bytes(_ACTIVE_MODLIST)


def _copy_profile_bytes(source: Path, destination: Path) -> None:
    for path in sorted(source.rglob("*")):
        if path.is_dir():
            continue
        if path.is_symlink():
            raise ContainmentFixtureError(f"source profile contains a redirect: {path}")
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())


def _prepare_stage_environment(run_root: Path, layout: WorkspaceLayout) -> dict[str, str]:
    paths = {
        "stage-root": layout.skyrim_mo2,
        "downloads": layout.skyrim_mo2_downloads,
        "profiles": layout.skyrim_mo2_profiles,
        "mods": layout.skyrim_mo2_mods,
        "overwrite": layout.skyrim_mo2_overwrite,
        "cache": layout.skyrim_mo2 / "webcache",
        "logs": layout.skyrim_mo2 / "logs",
        "TEMP": run_root / "stage-environment" / "TEMP",
        "TMP": run_root / "stage-environment" / "TMP",
        "APPDATA": run_root / "stage-environment" / "APPDATA",
        "LOCALAPPDATA": run_root / "stage-environment" / "LOCALAPPDATA",
        "USERPROFILE": run_root / "stage-environment" / "USERPROFILE",
        "HOME": run_root / "stage-environment" / "HOME",
    }
    for path in paths.values():
        _require_direct_directory(path, create=True)
    _require_direct_directory(paths["USERPROFILE"] / "Desktop", create=True)
    for path in paths.values():
        set_low_integrity_tree(path)
    environment = dict(os.environ)
    environment.update({name: str(path) for name, path in paths.items() if name.isupper()})
    return environment


def _verify_stage_integrity_before_projection(environment: Mapping[str, str]) -> None:
    for name in ("TEMP", "TMP", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME"):
        if inspect_path_integrity(Path(environment[name])) is not IntegrityLevel.LOW:
            raise ContainmentFixtureError(f"stage environment path is not Low: {name}")


def _require_exact_version(report) -> None:
    executable = getattr(report, "executable", None)
    if executable is None or executable.file_version != "2.5.2.0":
        raise ContainmentFixtureError("disposable MO2 executable version is not 2.5.2.0")


def _external_low_watch_roots(
    *,
    local_low_resolver: Callable[[], Path] | None = None,
    current_temp_base_resolver: Callable[[], Path] | None = None,
    integrity_reader: Callable[[Path], IntegrityLevel] = inspect_path_integrity,
) -> tuple[tuple[str, Path], ...]:
    if os.name != "nt":
        return ()
    local_low = (local_low_resolver or _known_folder_local_app_data_low)()
    temporary = _current_low_temp_directory(
        temp_base_resolver=current_temp_base_resolver,
        integrity_reader=integrity_reader,
    )
    unique: list[tuple[str, Path]] = []
    for name, path in (("ExternalLocalLow", local_low), ("ExternalTempLow", temporary)):
        resolved = path.resolve(strict=False)
        if any(_same_path_identity(resolved, existing) for _, existing in unique):
            continue
        unique.append((name, resolved))
    return tuple(unique)


def _same_path_identity(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except OSError:
        return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _current_low_temp_directory(
    *,
    temp_base_resolver: Callable[[], Path] | None = None,
    integrity_reader: Callable[[Path], IntegrityLevel] = inspect_path_integrity,
) -> Path:
    base = _require_direct_directory(
        Path((temp_base_resolver or _current_temp_base_directory)()), create=False
    )
    low = _require_direct_directory(base / "Low", create=False)
    if integrity_reader(low) is not IntegrityLevel.LOW:
        raise ContainmentFixtureError("current Low temporary directory is not Low")
    return low


def _current_temp_base_directory() -> Path:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetTempPathW.argtypes = (wintypes.DWORD, wintypes.LPWSTR)
    kernel32.GetTempPathW.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel32.GetTempPathW(len(buffer), buffer)
    if length == 0 or length >= len(buffer):
        raise ContainmentFixtureError("could not resolve the current Low temporary directory")
    return Path(buffer.value)


def _known_folder_local_app_data_low() -> Path:
    class Guid(ctypes.Structure):
        _fields_ = [
            ("data1", wintypes.DWORD),
            ("data2", wintypes.WORD),
            ("data3", wintypes.WORD),
            ("data4", ctypes.c_ubyte * 8),
        ]

    identifier = Guid(
        0xA520A1A4,
        0x1780,
        0x4FF6,
        (ctypes.c_ubyte * 8)(0xBD, 0x18, 0x16, 0x73, 0x43, 0xC5, 0xAF, 0x16),
    )
    path = ctypes.c_wchar_p()
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    shell32.SHGetKnownFolderPath.argtypes = (
        ctypes.POINTER(Guid),
        wintypes.DWORD,
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_wchar_p),
    )
    shell32.SHGetKnownFolderPath.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = (ctypes.c_void_p,)
    ole32.CoTaskMemFree.restype = None
    result = shell32.SHGetKnownFolderPath(ctypes.byref(identifier), 0, None, ctypes.byref(path))
    if result != 0 or not path.value:
        raise ContainmentFixtureError("could not resolve FOLDERID_LocalAppDataLow")
    try:
        return Path(path.value)
    finally:
        ole32.CoTaskMemFree(ctypes.cast(path, ctypes.c_void_p))


__all__ = [
    "ContainmentFixture",
    "ContainmentFixtureError",
    "ScenarioArchives",
    "preflight_containment_fixture",
    "prepare_containment_fixture",
    "write_scenario_archives",
]
