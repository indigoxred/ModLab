from __future__ import annotations

import json
import hashlib
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from pathlib import PurePosixPath
from subprocess import CompletedProcess
from unittest.mock import patch

from modlab.adapters.mo2.archive import PackageFile, PackageInventory
from modlab.adapters.mo2.bootstrap_config import (
    observe_profile_seed,
    render_modorganizer_ini,
    render_profile_files,
)
from modlab.adapters.mo2.projection import project_mo2_state
from modlab.adapters.mo2.scanner import inspect_skyrim_mo2

from modlab.adapters.mo2.bootstrap_model import (
    ArchiveEvidence,
    BootstrapDisposition,
    BootstrapFinding,
    BootstrapJobState,
    BootstrapJournal,
    BootstrapPlan,
    BootstrapReceipt,
    BootstrapReceiptMode,
    CoverageEntry,
    ExtractorIdentity,
    FileIdentity,
    OptionalFileIdentity,
    ProcessIdentity,
    ProcessObservation,
    ProfileSeedEvidence,
    TargetSnapshot,
)
from modlab.adapters.mo2.bootstrap_serialization import (
    journal_from_bytes,
    journal_to_bytes,
    plan_from_bytes,
    plan_id_for,
    plan_to_bytes,
    receipt_from_bytes,
    receipt_id_for,
    receipt_to_bytes,
)
from modlab.recipes.model import CheckState, RecipeIdentity, RecipeMaturity
from modlab.adapters.mo2.release import (
    LoadedMo2Release,
    Mo2ReleaseDescriptor,
    ReleaseFileIdentity,
    bundled_mo2_252_path,
    load_mo2_release,
)
from modlab.artifacts.model import ArchiveArtifact
from modlab.artifacts.serialization import artifact_to_dict
from modlab.workflows.skyrim.configuration import (
    ManagerRegistration,
    RecipeIntent,
    SkyrimEnvironmentConfiguration,
    StoredSourceReference,
    TargetEnvironmentIntent,
)
from modlab.workflows.skyrim.store import SkyrimEnvironmentStore
from modlab.workflows.skyrim.mo2_bootstrap_store import Mo2BootstrapStore
from modlab.workspace import initialize_workspace


@dataclass
class FakeRunner:
    responses: dict[str, CompletedProcess[bytes]]
    extraction_files: dict[str, bytes] = field(default_factory=dict)
    calls: list[list[str]] = field(default_factory=list)

    @classmethod
    def for_listing(cls, names: list[str], types: list[str]):
        name_bytes = ("\n".join(names) + "\n").encode("utf-8")
        verbose = "\n".join(
            f"{kind}rw-r--r--  0 0 0 0 Jan 01 2026 {name}"
            for name, kind in zip(names, types, strict=True)
        )
        return cls(
            {
                "-tf": CompletedProcess((), 0, name_bytes, b""),
                "-tvf": CompletedProcess(
                    (), 0, (verbose + "\n").encode("utf-8"), b""
                ),
            }
        )

    @classmethod
    def for_extraction(cls, files: dict[str, bytes]):
        runner = cls.for_listing(sorted(files), ["-"] * len(files))
        runner.responses["-xf"] = CompletedProcess((), 0, b"", b"")
        runner.extraction_files = dict(files)
        return runner

    def run(self, args: tuple[str, ...]) -> CompletedProcess[bytes]:
        self.calls.append(list(args))
        operation = args[1]
        if operation == "-xf":
            destination = Path(args[args.index("-C") + 1])
            for relative_path, data in self.extraction_files.items():
                target = destination / Path(*relative_path.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        try:
            response = self.responses[operation]
        except KeyError as error:
            raise AssertionError(f"unexpected fake command: {args}") from error
        return CompletedProcess(args, response.returncode, response.stdout, response.stderr)


def valid_names() -> list[str]:
    directories = [f"directory-{index:04d}/" for index in range(154)]
    files = [f"files/file-{index:04d}.bin" for index in range(1626)]
    return directories + files


def valid_types() -> list[str]:
    return ["d"] * 154 + ["-"] * 1626


def descriptor_for(names: list[str]) -> Mo2ReleaseDescriptor:
    listing = ("\n".join(names) + "\n").encode("utf-8")
    return replace(
        load_mo2_release(bundled_mo2_252_path()).descriptor,
        archive_entry_count=len(names),
        archive_listing_sha256=hashlib.sha256(listing).hexdigest(),
    )


def unsafe_listing_cases():
    return (
        (["../outside.dll"], ["-"], "safe relative path"),
        (["File.dll", "file.dll"], ["-", "-"], "duplicate"),
        (["linked.dll"], ["l"], "regular files and directories"),
    )


@dataclass(frozen=True)
class PrimaryPolicyFixture:
    game_root: Path


def make_primary_policy_fixture(
    root: Path,
    *,
    ccc_plugins: tuple[str, ...] = ("ccBGSSSE001-Fish.esm",),
    missing_data_files: tuple[str, ...] = (),
) -> PrimaryPolicyFixture:
    game_root = Path(root)
    data_root = game_root / "Data"
    data_root.mkdir(parents=True)
    core = (
        "Skyrim.esm",
        "Update.esm",
        "Dawnguard.esm",
        "HearthFires.esm",
        "Dragonborn.esm",
    )
    missing = {item.casefold() for item in missing_data_files}
    for name in core + ccc_plugins:
        if name.casefold() not in missing:
            (data_root / name).write_bytes((name + "\n").encode("utf-8"))
    if ccc_plugins:
        (game_root / "Skyrim.ccc").write_bytes(
            ("\n".join(ccc_plugins) + "\n").encode("utf-8")
        )
    return PrimaryPolicyFixture(game_root=game_root)


def invalid_primary_policy_cases(root: Path):
    missing_core = make_primary_policy_fixture(
        root / "missing-core",
        missing_data_files=("Update.esm",),
    )
    duplicate_ccc = make_primary_policy_fixture(
        root / "duplicate-ccc",
        ccc_plugins=("skyrim.ESM",),
    )
    nonregular_ccc_file = make_primary_policy_fixture(
        root / "nonregular-ccc-file",
        missing_data_files=("ccBGSSSE001-Fish.esm",),
    )
    (
        nonregular_ccc_file.game_root
        / "Data"
        / "ccBGSSSE001-Fish.esm"
    ).mkdir()
    return (
        (missing_core, "Update.esm"),
        (duplicate_ccc, "duplicate"),
        (nonregular_ccc_file, "ccBGSSSE001-Fish.esm"),
    )


def files_for_profile(
    files: dict[PurePosixPath, bytes], profile_name: str
) -> tuple[tuple[str, bytes], ...]:
    prefix = PurePosixPath("profiles", profile_name)
    result = []
    for path, data in files.items():
        try:
            relative = path.relative_to(prefix)
        except ValueError:
            continue
        result.append((relative.as_posix(), data))
    return tuple(sorted(result, key=lambda item: (item[0].casefold(), item[0])))


def package_inventory_fixture() -> PackageInventory:
    return _package_inventory(
        {
            "ModOrganizer.exe": b"organizer",
            "plugins/game_skyrimse.dll": b"game plugin",
            "usvfs_x64.dll": b"usvfs",
        }
    )


def existing_app_fixture(
    *, missing: str | None = None, extras: dict[str, bytes] | None = None
) -> PackageInventory:
    files = {
        item.relative_path: (item.sha256, item.size)
        for item in package_inventory_fixture().files
        if item.relative_path != missing
    }
    for path, data in (extras or {}).items():
        files[path] = (hashlib.sha256(data).hexdigest(), len(data))
    return _package_inventory_from_identities(files)


def extraction_package_files() -> dict[str, bytes]:
    return {
        "ModOrganizer.exe": b"portable organizer",
        "loot/loot.dll": b"loot library",
        "plugins/game_skyrimse.dll": b"skyrim game plugin",
        "usvfs_x64.dll": b"virtual filesystem",
    }


def descriptor_for_package(files: dict[str, bytes]) -> Mo2ReleaseDescriptor:
    base = load_mo2_release(bundled_mo2_252_path()).descriptor
    executable = _release_file("ModOrganizer.exe", files["ModOrganizer.exe"], "2.5.2.0")
    sentinels = tuple(
        _release_file(path, files[path])
        for path in (
            "loot/loot.dll",
            "plugins/game_skyrimse.dll",
            "usvfs_x64.dll",
        )
    )
    return replace(
        base,
        archive_entry_count=len(files),
        archive_listing_sha256=hashlib.sha256(("\n".join(sorted(files)) + "\n").encode()).hexdigest(),
        package_file_count=len(files),
        extracted_size=sum(len(data) for data in files.values()),
        minimum_free_bytes=1024,
        executable=executable,
        sentinels=sentinels,
    )


def _release_file(
    path: str, data: bytes, version: str | None = None
) -> ReleaseFileIdentity:
    return ReleaseFileIdentity(
        relative_path=path,
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
        file_version=version,
    )


def _package_inventory(files: dict[str, bytes]) -> PackageInventory:
    return _package_inventory_from_identities(
        {
            path: (hashlib.sha256(data).hexdigest(), len(data))
            for path, data in files.items()
        }
    )


def _package_inventory_from_identities(
    files: dict[str, tuple[str, int]],
) -> PackageInventory:
    rows = tuple(
        PackageFile(path, sha256, size)
        for path, (sha256, size) in sorted(
            files.items(), key=lambda item: (item[0].casefold(), item[0])
        )
    )
    canonical = json.dumps(
        [[item.relative_path, item.sha256, item.size] for item in rows],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return PackageInventory(
        files=rows,
        total_size=sum(item.size for item in rows),
        sha256=hashlib.sha256(canonical).hexdigest(),
    )


def make_plan_fixture(**overrides: object) -> BootstrapPlan:
    archive_hash = "a" * 64
    draft = BootstrapPlan(
        schema_version=1,
        plan_id="bootstrap-plan-sha256:" + "0" * 64,
        disposition=BootstrapDisposition.CREATE,
        workspace_root=r"C:\ModLab\workspace",
        steam_root=r"C:\Steam",
        game_root=r"C:\Steam\steamapps\common\Skyrim Special Edition",
        final_root=r"C:\ModLab\workspace\tools\mo2\skyrim-se-ae",
        staging_parent=r"C:\ModLab\workspace\tools\mo2",
        staging_name_template=".skyrim-se-ae.modlab-stage-<job-hex>",
        skyrim_executable=FileIdentity(
            path=(
                r"C:\Steam\steamapps\common\Skyrim Special Edition\SkyrimSE.exe"
            ),
            sha256="1" * 64,
            size=123456,
        ),
        environment_sha256="2" * 64,
        baseline_id="checkpoint-sha256:" + "3" * 64,
        baseline_status="Matched",
        release_descriptor_path="catalogue/tools/mo2-2.5.2.json",
        release_descriptor_sha256="4" * 64,
        release_id="mo2.windows-portable.2.5.2",
        archive=ArchiveEvidence(
            artifact_id="archive-sha256:" + archive_hash,
            metadata_sha256="5" * 64,
            original_name="Mod.Organizer-2.5.2.7z",
            stored_path=(
                "library/archives/aa/" + archive_hash + "/payload.7z"
            ),
            sha256=archive_hash,
            size=149660212,
            entry_count=1780,
            listing_sha256="6" * 64,
        ),
        extractor=ExtractorIdentity(
            executable=FileIdentity(
                path=r"C:\Windows\System32\tar.exe",
                sha256="7" * 64,
                size=777777,
            ),
            version="bsdtar 3.8.8",
        ),
        target=TargetSnapshot(
            kind="Empty",
            root=r"C:\ModLab\workspace\tools\mo2\skyrim-se-ae",
            inventory_sha256="8" * 64,
            entry_count=0,
        ),
        processes=ProcessObservation(complete=True, relevant=(), error=None),
        profile_seed=ProfileSeedEvidence(
            documents_root=r"C:\Users\red\Documents",
            primary_plugins=(
                "Skyrim.esm",
                "Update.esm",
                "Dawnguard.esm",
                "HearthFires.esm",
                "Dragonborn.esm",
            ),
            skyrim_ccc=OptionalFileIdentity(
                path=(
                    r"C:\Steam\steamapps\common\Skyrim Special Edition\Skyrim.ccc"
                ),
                present=False,
                sha256=None,
                size=None,
            ),
            ini_sources=(
                OptionalFileIdentity(
                    path=(
                        r"C:\Users\red\Documents\My Games\Skyrim Special Edition\Skyrim.ini"
                    ),
                    present=True,
                    sha256="9" * 64,
                    size=90,
                ),
                OptionalFileIdentity(
                    path=(
                        r"C:\Users\red\Documents\My Games\Skyrim Special Edition\SkyrimPrefs.ini"
                    ),
                    present=True,
                    sha256="b" * 64,
                    size=91,
                ),
                OptionalFileIdentity(
                    path=(
                        r"C:\Users\red\Documents\My Games\Skyrim Special Edition\SkyrimCustom.ini"
                    ),
                    present=False,
                    sha256=None,
                    size=None,
                ),
            ),
        ),
        write_templates=(
            "games/skyrim-se-ae/tool-installations/mo2/{receiptId}.json",
            "runtime/jobs/mo2-bootstrap/plans/{planId}.json",
            "runtime/jobs/mo2-bootstrap/{jobId}/journal.json",
        ),
        findings=(
            BootstrapFinding(
                state=CheckState.PASSED,
                code="release-unsupported",
                message="The curated release is supported.",
            ),
        ),
        exclusions=("installedModPayloads", "runtimeValidation", "smokeTest"),
        coverage=(
            CoverageEntry("installedModPayloads", "NotLinked"),
            CoverageEntry("managerArtifact", "VerifiedPackageSubset"),
            CoverageEntry("mutableManagerState", "ExcludedRecorded"),
            CoverageEntry("runtimeValidation", "NotPerformed"),
            CoverageEntry("smokeTest", "NotPerformed"),
        ),
        downloads=(),
        installations=(),
        manager_changes=(),
        game_changes=(),
        programs_launched=(
            r"C:\Windows\System32\tar.exe [version]",
            r"C:\Windows\System32\tar.exe [list-names]",
            r"C:\Windows\System32\tar.exe [list-types]",
        ),
    )
    draft = replace(draft, **overrides)
    return replace(draft, plan_id=plan_id_for(draft))


def make_journal_fixture(
    *,
    state: BootstrapJobState = BootstrapJobState.PLANNED,
    disposition: BootstrapDisposition = BootstrapDisposition.CREATE,
    **overrides: object,
) -> BootstrapJournal:
    has_stage = state in {
        BootstrapJobState.STAGED,
        BootstrapJobState.APPLYING,
        BootstrapJobState.ACTIVATED,
        BootstrapJobState.VERIFIED,
    }
    has_activated = state in {
        BootstrapJobState.ACTIVATED,
        BootstrapJobState.VERIFIED,
    }
    journal = BootstrapJournal(
        schema_version=1,
        job_id="bootstrap-job:0123456789abcdef0123456789abcdef",
        plan_id="bootstrap-plan-sha256:" + "c" * 64,
        disposition=disposition,
        state=state,
        stage_root=(
            r"C:\ModLab\workspace\tools\mo2\.skyrim-se-ae.modlab-stage-0123456789abcdef0123456789abcdef"
        ),
        prior_root=(
            r"C:\ModLab\workspace\runtime\jobs\mo2-bootstrap\0123456789abcdef0123456789abcdef\prior"
        ),
        final_root=r"C:\ModLab\workspace\tools\mo2\skyrim-se-ae",
        prior_target_kind=(
            "Existing"
            if disposition is BootstrapDisposition.ADOPT
            else "Empty"
        ),
        prior_inventory_sha256="d" * 64,
        prior_entry_count=0,
        stage_inventory_sha256="e" * 64 if has_stage else None,
        stage_entry_count=1626 if has_stage else None,
        activated_inventory_sha256="f" * 64 if has_activated else None,
        activated_entry_count=1648 if has_activated else None,
        created_at="2026-08-31T00:00:00Z",
        updated_at="2026-08-31T00:00:01Z",
        receipt_id=(
            "bootstrap-receipt-sha256:" + "1" * 64
            if state is BootstrapJobState.VERIFIED
            else None
        ),
        error=(
            "verification interrupted"
            if state is BootstrapJobState.RECOVERY_REQUIRED
            else None
        ),
    )
    return replace(journal, **overrides)


def make_receipt_fixture(**overrides: object) -> BootstrapReceipt:
    draft = BootstrapReceipt(
        schema_version=1,
        receipt_id="bootstrap-receipt-sha256:" + "0" * 64,
        qualification="VerifiedBootstrap",
        mode=BootstrapReceiptMode.CREATED,
        plan_id="bootstrap-plan-sha256:" + "2" * 64,
        job_id="bootstrap-job:0123456789abcdef0123456789abcdef",
        release_id="mo2.windows-portable.2.5.2",
        release_descriptor_sha256="3" * 64,
        archive_artifact_id="archive-sha256:" + "4" * 64,
        archive_metadata_sha256="5" * 64,
        archive_sha256="4" * 64,
        archive_size=149660212,
        skyrim_executable=FileIdentity(
            path=(
                r"C:\Steam\steamapps\common\Skyrim Special Edition\SkyrimSE.exe"
            ),
            sha256="6" * 64,
            size=123456,
        ),
        mo2_executable=FileIdentity(
            path=r"C:\ModLab\workspace\tools\mo2\skyrim-se-ae\app\ModOrganizer.exe",
            sha256="7" * 64,
            size=5028352,
        ),
        final_root=r"C:\ModLab\workspace\tools\mo2\skyrim-se-ae",
        downloads_root=r"C:\ModLab\workspace\tools\mo2\skyrim-se-ae\downloads",
        mods_root=r"C:\ModLab\workspace\tools\mo2\skyrim-se-ae\mods",
        profiles_root=r"C:\ModLab\workspace\tools\mo2\skyrim-se-ae\profiles",
        overwrite_root=r"C:\ModLab\workspace\tools\mo2\skyrim-se-ae\overwrite",
        webcache_root=r"C:\ModLab\workspace\tools\mo2\skyrim-se-ae\webcache",
        package_inventory_sha256="8" * 64,
        package_file_count=1626,
        package_size=408536933,
        mutable_package_paths=(),
        extra_entries=(),
        profile_state_sha256="9" * 64,
        extractor=ExtractorIdentity(
            executable=FileIdentity(
                path=r"C:\Windows\System32\tar.exe",
                sha256="a" * 64,
                size=777777,
            ),
            version="bsdtar 3.8.8",
        ),
        verified_at="2026-08-31T00:00:02Z",
        findings=(
            BootstrapFinding(
                state=CheckState.PASSED,
                code="post-activation-not-ready",
                message="The activated manager projected Ready.",
            ),
        ),
        coverage=(
            CoverageEntry("installedModPayloads", "NotLinked"),
            CoverageEntry("managerArtifact", "VerifiedPackageSubset"),
            CoverageEntry("mutableManagerState", "ExcludedRecorded"),
            CoverageEntry("runtimeValidation", "NotPerformed"),
            CoverageEntry("smokeTest", "NotPerformed"),
        ),
        repairable=False,
        upgrade_supported=False,
    )
    draft = replace(draft, **overrides)
    return replace(draft, receipt_id=receipt_id_for(draft))


def _with_extra_field(data: bytes) -> bytes:
    value = json.loads(data)
    value["extra"] = True
    return _json_bytes(value)


def _with_save_root(data: bytes) -> bytes:
    value = json.loads(data)
    value["finalRoot"] = r"C:\ModLab\workspace\Saves"
    return _json_bytes(value)


def _with_duplicate_schema(data: bytes) -> bytes:
    return data.replace(b'{"', b'{"schemaVersion":1,"', 1)


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def malformed_bootstrap_documents():
    return (
        (_with_duplicate_schema(plan_to_bytes(make_plan_fixture())), plan_from_bytes),
        (_with_extra_field(plan_to_bytes(make_plan_fixture())), plan_from_bytes),
        (_with_save_root(journal_to_bytes(make_journal_fixture())), journal_from_bytes),
        (_with_extra_field(receipt_to_bytes(make_receipt_fixture())), receipt_from_bytes),
    )


def tree_state(root: Path) -> tuple[tuple[str, str, bytes | str | None], ...]:
    """Record a tree without following redirected entries."""
    source = Path(root)
    if not source.exists() and not source.is_symlink():
        return ()
    rows: list[tuple[str, str, bytes | str | None]] = []

    def visit(directory: Path, relative: PurePosixPath) -> None:
        with os.scandir(directory) as entries:
            children = sorted(entries, key=lambda item: (item.name.casefold(), item.name))
        for child in children:
            path = Path(child.path)
            child_relative = relative / child.name
            name = child_relative.as_posix()
            if child.is_symlink():
                rows.append((name, "redirect", os.readlink(path)))
            elif child.is_dir(follow_symlinks=False):
                rows.append((name, "directory", None))
                visit(path, child_relative)
            elif child.is_file(follow_symlinks=False):
                rows.append((name, "file", path.read_bytes()))
            else:
                rows.append((name, "other", None))

    if source.is_symlink():
        return ((".", "redirect", os.readlink(source)),)
    if source.is_file():
        return ((".", "file", source.read_bytes()),)
    visit(source, PurePosixPath())
    return tuple(rows)


@dataclass
class BootstrapPlanningFixture:
    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.workspace = self.root / "ModLab" / "workspace"
        self.layout = initialize_workspace(self.workspace)
        self.steam_root = self.root / "Steam"
        self.game_root = (
            self.steam_root
            / "steamapps"
            / "common"
            / "Skyrim Special Edition"
        )
        self.documents_root = self.root / "Documents"
        self.release_path = bundled_mo2_252_path()
        self.processes = ProcessObservation(complete=True, relevant=(), error=None)
        self.free_bytes = 10_000_000
        self.game_version = "1.6.1170.0"
        self.mo2_version = "2.5.2.0"
        self._make_game()
        self._make_documents()
        self._make_archive_and_release()

    def _make_game(self) -> None:
        manifest = self.steam_root / "steamapps" / "appmanifest_489830.acf"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            '"AppState"\n'
            "{\n"
            '    "appid" "489830"\n'
            '    "name" "The Elder Scrolls V: Skyrim Special Edition"\n'
            '    "StateFlags" "4"\n'
            '    "installdir" "Skyrim Special Edition"\n'
            "}\n",
            encoding="utf-8",
        )
        self.game_root.mkdir(parents=True)
        (self.game_root / "SkyrimSE.exe").write_bytes(b"fixture Skyrim executable")
        make_primary_policy_fixture(
            self.game_root,
            ccc_plugins=("ccBGSSSE001-Fish.esm",),
        )

    def _make_documents(self) -> None:
        ini_root = self.documents_root / "My Games" / "Skyrim Special Edition"
        ini_root.mkdir(parents=True)
        (ini_root / "Skyrim.ini").write_bytes(b"[General]\r\nfixture=true\r\n")
        (ini_root / "SkyrimPrefs.ini").write_bytes(
            b"[Display]\nquality=fixture\n"
        )
        (ini_root / "SkyrimCustom.ini").write_bytes(b"[Archive]\nfixture=true\n")

    def _make_archive_and_release(self) -> None:
        archive_data = b"tiny retained MO2 archive fixture"
        archive_sha = hashlib.sha256(archive_data).hexdigest()
        self.artifact_id = f"archive-sha256:{archive_sha}"
        stored_relative = (
            f"library/archives/{archive_sha[:2]}/{archive_sha}/payload.7z"
        )
        archive_path = self.workspace.joinpath(*PurePosixPath(stored_relative).parts)
        archive_path.parent.mkdir(parents=True)
        archive_path.write_bytes(archive_data)
        record = ArchiveArtifact(
            schema_version=1,
            artifact_id=self.artifact_id,
            sha256=archive_sha,
            size=len(archive_data),
            original_name="Mod.Organizer-2.5.2.7z",
            stored_relative_path=stored_relative,
            imported_at="2026-08-31T00:00:00Z",
            source_note="planning fixture",
            source_url=None,
        )
        metadata = (
            json.dumps(
                artifact_to_dict(record),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        metadata_path = self.layout.metadata / "artifacts" / f"{archive_sha}.json"
        metadata_path.parent.mkdir(parents=True)
        metadata_path.write_bytes(metadata)

        self.package_files = {
            "ModOrganizer.exe": b"fixture MO2 executable",
            "loot/loot.dll": b"fixture LOOT library",
            "plugins/game_skyrimse.dll": b"fixture Skyrim game plug-in",
            "resources/base.dat": b"fixture optional package data",
            "usvfs_x64.dll": b"fixture virtual filesystem",
        }
        self.archive_names = list(self.package_files)
        self.archive_types = ["-"] * len(self.archive_names)
        listing = ("\n".join(self.archive_names) + "\n").encode("utf-8")
        executable_bytes = self.package_files["ModOrganizer.exe"]
        base = load_mo2_release(bundled_mo2_252_path()).descriptor
        descriptor = replace(
            base,
            archive_sha256=archive_sha,
            archive_size=len(archive_data),
            archive_entry_count=len(self.archive_names),
            archive_listing_sha256=hashlib.sha256(listing).hexdigest(),
            package_file_count=len(self.archive_names),
            extracted_size=sum(len(data) for data in self.package_files.values()),
            minimum_free_bytes=1024,
            executable=_release_file(
                "ModOrganizer.exe", executable_bytes, self.mo2_version
            ),
            sentinels=tuple(
                _release_file(path, self.package_files[path])
                for path in (
                    "loot/loot.dll",
                    "plugins/game_skyrimse.dll",
                    "usvfs_x64.dll",
                )
            ),
        )
        descriptor_data = b"fixture release descriptor\n"
        self.release = LoadedMo2Release(
            descriptor=descriptor,
            path=self.release_path,
            data=descriptor_data,
            sha256=hashlib.sha256(descriptor_data).hexdigest(),
        )
        self.mo2_executable_bytes = executable_bytes
        self.runner = FakeRunner.for_listing(self.archive_names, self.archive_types)
        self.runner.responses["--version"] = CompletedProcess(
            (), 0, b"bsdtar 3.8.8 - libarchive fixture\n", b""
        )

    def prepare(self, **overrides: object):
        from modlab.workflows.skyrim import mo2_bootstrap

        arguments: dict[str, object] = {
            "artifact_id": self.artifact_id,
            "workspace_root": self.workspace,
            "steam_root": self.steam_root,
            "release_path": self.release_path,
            "documents_root": self.documents_root,
            "version_reader": self.version_reader,
            "process_inspector": lambda _: self.processes,
            "command_runner": self.runner,
            "free_space_reader": lambda _: self.free_bytes,
        }
        arguments.update(overrides)
        with patch.object(
            mo2_bootstrap, "load_mo2_release", return_value=self.release
        ):
            return mo2_bootstrap.prepare_mo2_setup(**arguments)

    def version_reader(self, path: Path) -> str | None:
        return (
            self.mo2_version
            if Path(path).name.casefold() == "modorganizer.exe"
            else self.game_version
        )

    def make_ready_existing(self) -> None:
        app = self.layout.skyrim_mo2_app
        app.mkdir(parents=True, exist_ok=True)
        for relative, data in self.package_files.items():
            target = app.joinpath(*PurePosixPath(relative).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (app / "ModOrganizer.ini").write_bytes(
            render_modorganizer_ini(self.layout, self.game_root, self.release.descriptor)
        )
        seed = observe_profile_seed(self.game_root, self.documents_root)
        for relative, data in render_profile_files(seed).items():
            target = self.layout.skyrim_mo2.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

    def make_unknown_target(self) -> None:
        (self.layout.skyrim_mo2 / "unknown.txt").write_bytes(b"user-owned")

    def make_environment(
        self,
        *,
        steam_root: Path | None = None,
        game_root: Path | None = None,
        baseline_id: str | None = None,
    ) -> None:
        recipe_sha = "a" * 64
        target_sha = "b" * 64
        configuration = SkyrimEnvironmentConfiguration(
            schema_version=1,
            game_key="skyrim-se-ae",
            environment_id="skyrim.fixture.steam",
            lineage_id="skyrim-main",
            steam_root=str(steam_root or self.steam_root),
            game_root=str(game_root or self.game_root),
            manager=ManagerRegistration(
                adapter_id="portable-mo2-skyrim",
                root="tools/mo2/skyrim-se-ae/app",
            ),
            recipe=RecipeIntent(
                recipe_id="skyrim.fixture.foundation",
                revision="2026.08.31.1",
                maturity=RecipeMaturity.DRAFT,
                identity=RecipeIdentity.ORIGINAL,
                source=StoredSourceReference(
                    source_sha256=recipe_sha,
                    stored_path=(
                        f"games/skyrim-se-ae/recipes/{recipe_sha}/recipe.json"
                    ),
                ),
                selected=("foundation-core",),
                omitted=(),
            ),
            target_environment=TargetEnvironmentIntent(
                environment_id="skyrim.fixture.steam",
                source=StoredSourceReference(
                    source_sha256=target_sha,
                    stored_path=(
                        "games/skyrim-se-ae/target-environments/"
                        f"{target_sha}/environment.json"
                    ),
                ),
                dimensions=(
                    ("adapterVersion", "2.5.2"),
                    ("executableRuntime", "1.6.1170"),
                ),
            ),
            baseline_checkpoint_id=baseline_id,
        )
        SkyrimEnvironmentStore(self.workspace).write(configuration, replace=False)

    def make_receipt_covered(self):
        self.make_ready_existing()
        planned = self.prepare()
        store = Mo2BootstrapStore(self.workspace)
        from modlab.adapters.mo2.archive import preflight_archive
        listing = preflight_archive(Path("payload.7z"), self.release.descriptor,
                                    Path("tar.exe"), runner=self.runner)
        journal = store.create_job(planned.plan, listing=listing)
        staging = replace(
            journal.journal,
            state=BootstrapJobState.STAGING,
            updated_at=journal.journal.updated_at,
        )
        journal = store.transition_job(
            journal,
            BootstrapJobState.PLANNED,
            staging,
        )
        staged = replace(
            journal.journal,
            state=BootstrapJobState.STAGED,
            stage_inventory_sha256="d" * 64,
            stage_entry_count=self.release.descriptor.package_file_count,
            updated_at=journal.journal.updated_at,
        )
        journal = store.transition_job(
            journal,
            BootstrapJobState.STAGING,
            staged,
        )
        projection = project_mo2_state(
            inspect_skyrim_mo2(
                self.layout.skyrim_mo2_app,
                self.game_root,
                workspace_root=self.workspace,
                version_reader=self.version_reader,
            )
        )
        assert projection.adapter_state_sha256 is not None
        executable = self.layout.skyrim_mo2_app / "ModOrganizer.exe"
        executable_data = executable.read_bytes()
        package_inventory = _package_inventory(self.package_files)
        receipt = make_receipt_fixture(
            mode=BootstrapReceiptMode.ADOPTED,
            plan_id=planned.plan.plan_id,
            job_id=journal.journal.job_id,
            release_id=planned.plan.release_id,
            release_descriptor_sha256=planned.plan.release_descriptor_sha256,
            archive_artifact_id=planned.plan.archive.artifact_id,
            archive_metadata_sha256=planned.plan.archive.metadata_sha256,
            archive_sha256=planned.plan.archive.sha256,
            archive_size=planned.plan.archive.size,
            skyrim_executable=planned.plan.skyrim_executable,
            mo2_executable=FileIdentity(
                path=str(executable),
                sha256=hashlib.sha256(executable_data).hexdigest(),
                size=len(executable_data),
            ),
            final_root=str(self.layout.skyrim_mo2),
            downloads_root=str(self.layout.skyrim_mo2_downloads),
            mods_root=str(self.layout.skyrim_mo2_mods),
            profiles_root=str(self.layout.skyrim_mo2_profiles),
            overwrite_root=str(self.layout.skyrim_mo2_overwrite),
            webcache_root=str(self.layout.skyrim_mo2 / "webcache"),
            package_inventory_sha256=package_inventory.sha256,
            package_file_count=package_inventory.file_count,
            package_size=package_inventory.total_size,
            mutable_package_paths=self.release.descriptor.mutable_package_paths,
            extra_entries=("ModOrganizer.ini",),
            profile_state_sha256=projection.adapter_state_sha256,
            extractor=planned.plan.extractor,
            verified_at="2026-08-31T00:00:03Z",
            coverage=planned.plan.coverage,
        )
        stored_receipt = store.write_receipt(receipt)
        verified = replace(
            journal.journal,
            state=BootstrapJobState.VERIFIED,
            receipt_id=stored_receipt.receipt.receipt_id,
            updated_at=journal.journal.updated_at,
        )
        store.transition_job(
            journal,
            BootstrapJobState.STAGED,
            verified,
        )
        return planned, stored_receipt

    def external_state(self):
        return (
            tree_state(self.steam_root),
            tree_state(self.documents_root),
        )

    def workspace_state(self):
        return tree_state(self.workspace)
