from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field, replace
from pathlib import Path
from pathlib import PurePosixPath
from subprocess import CompletedProcess

from modlab.adapters.mo2.archive import PackageFile, PackageInventory

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
from modlab.recipes.model import CheckState
from modlab.adapters.mo2.release import (
    Mo2ReleaseDescriptor,
    ReleaseFileIdentity,
    bundled_mo2_252_path,
    load_mo2_release,
)


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
        return cls(
            {"-xf": CompletedProcess((), 0, b"", b"")},
            extraction_files=dict(files),
        )

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
    *, state: BootstrapJobState = BootstrapJobState.PLANNED, **overrides: object
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
        disposition=BootstrapDisposition.CREATE,
        state=state,
        stage_root=(
            r"C:\ModLab\workspace\tools\mo2\.skyrim-se-ae.modlab-stage-0123456789abcdef0123456789abcdef"
        ),
        prior_root=(
            r"C:\ModLab\workspace\runtime\jobs\mo2-bootstrap\0123456789abcdef0123456789abcdef\prior"
        ),
        final_root=r"C:\ModLab\workspace\tools\mo2\skyrim-se-ae",
        prior_target_kind="Empty",
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
