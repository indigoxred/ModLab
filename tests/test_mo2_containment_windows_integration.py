"""Opt-in verification of the disposable real MO2 containment fixture."""

import os
import unittest
from pathlib import Path

from modlab.validation.windows_integrity import IntegrityLevel, inspect_path_integrity
from tests.support.mo2_containment import prepare_real_containment_fixture


@unittest.skipUnless(
    os.name == "nt"
    and os.environ.get("MODLAB_MO2_ARCHIVE")
    and os.environ.get("MODLAB_STEAM_ROOT"),
    "real MO2 archive and Steam root were not supplied",
)
class Mo2ContainmentWindowsIntegrationTests(unittest.TestCase):
    def test_real_fixture_is_exact_isolated_and_low_integrity(self):
        with prepare_real_containment_fixture(
            Path(os.environ["MODLAB_MO2_ARCHIVE"]),
            Path(os.environ["MODLAB_STEAM_ROOT"]),
        ) as evidence:
            fixture = evidence.fixture
            self.assertEqual("2.5.2.0", evidence.executable_version)
            self.assertEqual(IntegrityLevel.LOW, inspect_path_integrity(fixture.stage_workspace))
            self.assertGreaterEqual(
                inspect_path_integrity(fixture.source_workspace), IntegrityLevel.MEDIUM
            )
            self.assertEqual(0, evidence.payload_bytes_copied_for_projection)
            self.assertEqual((), evidence.production_paths_written)


if __name__ == "__main__":
    unittest.main()
