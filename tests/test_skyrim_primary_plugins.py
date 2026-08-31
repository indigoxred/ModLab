import hashlib
import tempfile
import unittest
from pathlib import Path

from modlab.adapters.mo2.readset import Mo2ReadSet
from modlab.adapters.skyrim.primary_plugins import (
    CORE_PRIMARY_PLUGINS,
    SkyrimPrimaryPluginError,
    observe_skyrim_primary_plugins,
)
from tests.support.mo2_bootstrap import (
    invalid_primary_policy_cases,
    make_primary_policy_fixture,
)


class SkyrimPrimaryPluginTests(unittest.TestCase):
    def test_primary_policy_is_core_then_exact_ccc_order(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = make_primary_policy_fixture(
                Path(directory, "Skyrim Special Edition"),
                ccc_plugins=(
                    "ccBGSSSE001-Fish.esm",
                    "_ResourcePack.esl",
                ),
            )
            evidence = observe_skyrim_primary_plugins(
                fixture.game_root, read_set=Mo2ReadSet()
            )

            self.assertEqual(
                CORE_PRIMARY_PLUGINS
                + ("ccBGSSSE001-Fish.esm", "_ResourcePack.esl"),
                evidence.plugins,
            )
            ccc_bytes = b"ccBGSSSE001-Fish.esm\n_ResourcePack.esl\n"
            self.assertEqual(str(fixture.game_root / "Skyrim.ccc"), evidence.ccc_path)
            self.assertTrue(evidence.ccc_present)
            self.assertEqual(hashlib.sha256(ccc_bytes).hexdigest(), evidence.ccc_sha256)
            self.assertEqual(len(ccc_bytes), evidence.ccc_size)

    def test_absent_ccc_is_an_explicit_core_only_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = make_primary_policy_fixture(
                Path(directory, "Skyrim Special Edition"), ccc_plugins=()
            )

            evidence = observe_skyrim_primary_plugins(
                fixture.game_root, read_set=Mo2ReadSet()
            )

            self.assertEqual(CORE_PRIMARY_PLUGINS, evidence.plugins)
            self.assertFalse(evidence.ccc_present)
            self.assertIsNone(evidence.ccc_sha256)
            self.assertIsNone(evidence.ccc_size)

    def test_missing_ccc_entries_are_omitted_in_exact_catalogue_order(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = make_primary_policy_fixture(
                Path(directory, "Skyrim Special Edition"),
                ccc_plugins=(
                    "ccBGSSSE001-Fish.esm",
                    "ccASVSSE001-ALMSIVI.esm",
                    "_ResourcePack.esl",
                ),
                missing_data_files=("ccASVSSE001-ALMSIVI.esm",),
            )

            evidence = observe_skyrim_primary_plugins(
                fixture.game_root, read_set=Mo2ReadSet()
            )

            self.assertEqual(
                CORE_PRIMARY_PLUGINS
                + ("ccBGSSSE001-Fish.esm", "_ResourcePack.esl"),
                evidence.plugins,
            )

    def test_primary_policy_blocks_missing_core_duplicate_and_nonregular_ccc_file(self):
        with tempfile.TemporaryDirectory() as directory:
            for fixture, reason in invalid_primary_policy_cases(Path(directory)):
                with self.subTest(reason=reason):
                    with self.assertRaisesRegex(SkyrimPrimaryPluginError, reason):
                        observe_skyrim_primary_plugins(
                            fixture.game_root, read_set=Mo2ReadSet()
                        )


if __name__ == "__main__":
    unittest.main()
