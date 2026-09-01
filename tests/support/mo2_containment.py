"""Disposable fakes for containment-fixture unit tests."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import tempfile
from unittest.mock import patch

from modlab.adapters.mo2.bootstrap_model import BootstrapReceiptMode
from modlab.artifacts.vault import ArchiveVault
from modlab.validation.mo2_containment_model import ContainmentScenario
from modlab.validation.windows_integrity import IntegrityLevel
from modlab.workspace import initialize_workspace


def prepare_fixture_with_fake_bootstrap(root: Path, *, fixture_parent: Path | None = None):
    """Exercise fixture assembly without extracting or launching a real MO2 archive."""
    from modlab.validation import mo2_containment_fixtures as fixtures

    base = Path(root)
    source_workspace = base / "source-vault"
    initialize_workspace(source_workspace)
    archive_path = base / "Mod.Organizer-2.5.2.7z"
    archive_path.write_bytes(b"exact-retained-fixture-archive")
    artifact = ArchiveVault(source_workspace).import_archive(
        archive_path, source_note="fixture source archive"
    )
    steam_root = base / "Steam"
    steam_root.mkdir()
    counter = 0

    def fake_prepare(*, workspace_root: Path, **_kwargs):
        nonlocal counter
        counter += 1
        return SimpleNamespace(
            plan=SimpleNamespace(
                plan_id=f"fixture-plan-{counter}",
                game_root=str(steam_root / "steamapps" / "common" / "Skyrim Special Edition"),
            )
        )

    def fake_apply(_plan_id: str, workspace_root: Path, **_kwargs):
        layout = initialize_workspace(workspace_root)
        (layout.skyrim_mo2_app / "ModOrganizer.exe").write_bytes(b"fixture mo2")
        (layout.skyrim_mo2_app / "ModOrganizer.ini").write_text(
            f"[Settings]\nbase_directory={layout.skyrim_mo2}\n", encoding="utf-8"
        )
        for profile in ("ModLab - Lab", "ModLab - Play"):
            directory = layout.skyrim_mo2_profiles / profile
            directory.mkdir()
            (directory / "modlist.txt").write_bytes(b"")
        return SimpleNamespace(
            receipt=SimpleNamespace(
                mode=BootstrapReceiptMode.CREATED,
                receipt_id=f"fixture-receipt-{counter}",
            )
        )

    def fake_integrity(path: Path) -> IntegrityLevel:
        return (
            IntegrityLevel.LOW
            if "stage-workspace" in str(path).casefold() or "stage-environment" in str(path).casefold()
            else IntegrityLevel.MEDIUM
        )

    report = SimpleNamespace(executable=SimpleNamespace(file_version="2.5.2.0"))
    with (
        patch.object(fixtures, "prepare_mo2_setup", side_effect=fake_prepare),
        patch.object(fixtures, "apply_mo2_setup", side_effect=fake_apply),
        patch.object(fixtures, "inspect_skyrim_mo2", return_value=report),
        patch.object(fixtures, "inspect_path_integrity", side_effect=fake_integrity),
        patch.object(fixtures, "set_low_integrity_tree"),
        patch.object(fixtures, "_external_low_watch_roots", return_value=()),
    ):
        return fixtures.prepare_containment_fixture(
            source_workspace,
            artifact.artifact_id,
            steam_root,
            initialize_workspace(source_workspace).mo2_containment_validation,
            ContainmentScenario.NEW_FOLDER,
            fixture_parent=fixture_parent,
        )


@contextmanager
def prepare_real_containment_fixture(archive_path: Path, steam_root: Path):
    """Yield a disposable real-archive fixture without touching a managed instance."""
    from modlab.validation.mo2_containment_fixtures import prepare_containment_fixture

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        workspace = root / "source-vault"
        layout = initialize_workspace(workspace)
        artifact = ArchiveVault(workspace).import_archive(
            Path(archive_path), source_note="opt-in real containment fixture"
        )
        yield prepare_containment_fixture(
            workspace,
            artifact.artifact_id,
            Path(steam_root),
            layout.mo2_containment_validation,
            ContainmentScenario.NEW_FOLDER,
        )


__all__ = [
    "prepare_fixture_with_fake_bootstrap",
    "prepare_real_containment_fixture",
]
