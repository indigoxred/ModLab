from __future__ import annotations

import json
from dataclasses import replace

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
                        r"C:\Users\red\Documents\My Games\Skyrim Special Edition\SkyrimCustom.ini"
                    ),
                    present=False,
                    sha256=None,
                    size=None,
                ),
                OptionalFileIdentity(
                    path=(
                        r"C:\Users\red\Documents\My Games\Skyrim Special Edition\SkyrimPrefs.ini"
                    ),
                    present=True,
                    sha256="b" * 64,
                    size=91,
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
