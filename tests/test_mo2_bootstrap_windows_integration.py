"""Opt-in proof against the retained real MO2 archive and installed Skyrim."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from modlab.adapters.mo2.bootstrap_model import (
    BootstrapJobState,
    BootstrapReceiptMode,
)
from modlab.adapters.mo2.projection import Mo2Readiness, project_mo2_state
from modlab.adapters.mo2.scanner import inspect_skyrim_mo2
from modlab.artifacts.vault import ArchiveVault
from modlab.workspace import workspace_layout
from modlab.workflows.skyrim.mo2_bootstrap import (
    apply_mo2_setup,
    prepare_mo2_setup,
)


_ARCHIVE = os.environ.get("MODLAB_MO2_ARCHIVE")
_STEAM_ROOT = os.environ.get("MODLAB_STEAM_ROOT")


@unittest.skipUnless(
    os.name == "nt" and _ARCHIVE and _STEAM_ROOT,
    "real MO2 archive and Steam root were not supplied",
)
class Mo2BootstrapWindowsIntegrationTests(unittest.TestCase):
    def test_real_archive_creates_ready_disposable_instance(self):
        assert _ARCHIVE is not None and _STEAM_ROOT is not None
        source_archive = Path(_ARCHIVE)
        steam_root = Path(_STEAM_ROOT)

        with tempfile.TemporaryDirectory() as temporary:
            curated_source = Path(temporary, "Mod.Organizer-2.5.2.7z")
            shutil.copyfile(source_archive, curated_source)
            workspace = Path(temporary, "workspace")
            artifact = ArchiveVault(workspace).import_archive(
                curated_source,
                source_note="opt-in integration fixture",
            )

            planned = prepare_mo2_setup(
                artifact_id=artifact.artifact_id,
                workspace_root=workspace,
                steam_root=steam_root,
            )
            self.assertEqual("Create", planned.plan.disposition.value)

            applied = apply_mo2_setup(planned.plan.plan_id, workspace)
            layout = workspace_layout(workspace)
            projection = project_mo2_state(
                inspect_skyrim_mo2(
                    layout.skyrim_mo2_app,
                    Path(planned.plan.game_root),
                    workspace_root=workspace,
                )
            )

            self.assertEqual(BootstrapReceiptMode.CREATED, applied.receipt.mode)
            self.assertIsNotNone(applied.journal)
            assert applied.journal is not None
            self.assertEqual(BootstrapJobState.VERIFIED, applied.journal.state)
            self.assertEqual(Mo2Readiness.READY, projection.readiness)
            self.assertEqual((), projection.adapter_state.installed_mods)
            self.assertEqual((), projection.adapter_state.overwrite_entries)
            self.assertFalse(projection.adapter_state.lab.profile_local_saves)
            self.assertFalse(projection.adapter_state.play.profile_local_saves)
            self.assertEqual((), applied.downloads)
            self.assertEqual((), applied.game_changes)


if __name__ == "__main__":
    unittest.main()
