import copy
import unittest

from modlab.adapters.skyrim.model import (
    DataFileEvidence,
    DiscoveryFinding,
    ExecutableEvidence,
    SkyrimDiscoveryReport,
)
from modlab.adapters.skyrim.serialization import (
    SkyrimDiscoveryFormatError,
    discovery_from_dict,
    discovery_to_dict,
)
from modlab.recipes.model import CheckState


VALID_REPORT = {
    "schemaVersion": 1,
    "adapterId": "skyrim-steam",
    "steamRoot": "C:\\Steam",
    "manifestPath": "C:\\Steam\\steamapps\\appmanifest_489830.acf",
    "appId": "489830",
    "appName": "The Elder Scrolls V: Skyrim Special Edition",
    "installDirectory": "Skyrim Special Edition",
    "installStateFlags": 4,
    "gameRoot": "C:\\Steam\\steamapps\\common\\Skyrim Special Edition",
    "executable": {
        "relativePath": "SkyrimSE.exe",
        "fileVersion": "1.7.104.0",
        "sha256": "a" * 64,
        "size": 37910440,
    },
    "dataFiles": [
        {
            "relativePath": "Data/Skyrim.esm",
            "extension": ".esm",
            "size": 123,
        },
        {
            "relativePath": "Data/ccBGSSSE001-Fish.esm",
            "extension": ".esm",
            "size": 456,
        },
    ],
    "creationClubPluginCount": 1,
    "creationClubArchiveCount": 0,
    "mo2Path": None,
    "findings": [
        {
            "state": "Passed",
            "code": "runtime-observed",
            "message": "SkyrimSE.exe reports 1.7.104.0.",
        },
        {
            "state": "Unknown",
            "code": "anniversary-bundle-not-proven",
            "message": "Runtime version alone does not prove the full Anniversary bundle.",
        },
    ],
}


class SkyrimDiscoverySerializationTests(unittest.TestCase):
    def test_round_trip_preserves_observations_without_inventing_ae_or_mo2(self):
        report = discovery_from_dict(VALID_REPORT)

        restored = discovery_from_dict(discovery_to_dict(report))

        self.assertEqual(report, restored)
        self.assertIsInstance(report, SkyrimDiscoveryReport)
        self.assertIsInstance(report.executable, ExecutableEvidence)
        self.assertIsInstance(report.data_files[0], DataFileEvidence)
        self.assertIsInstance(report.findings[0], DiscoveryFinding)
        self.assertEqual(CheckState.UNKNOWN, report.findings[1].state)
        self.assertIsNone(report.mo2_path)

    def test_rejects_extra_fields_unsafe_paths_and_bad_counts(self):
        cases = []
        extra = copy.deepcopy(VALID_REPORT)
        extra["claimedSafe"] = True
        cases.append(extra)

        unsafe_data = copy.deepcopy(VALID_REPORT)
        unsafe_data["dataFiles"][0]["relativePath"] = "Data/../Saves/Save001.ess"
        cases.append(unsafe_data)

        save_data = copy.deepcopy(VALID_REPORT)
        save_data["dataFiles"][0]["relativePath"] = "Data/Saves/Save001.ess"
        save_data["dataFiles"][0]["extension"] = ".ess"
        cases.append(save_data)

        wrong_count = copy.deepcopy(VALID_REPORT)
        wrong_count["creationClubPluginCount"] = 99
        cases.append(wrong_count)

        for index, data in enumerate(cases):
            with self.subTest(case=index):
                with self.assertRaises(SkyrimDiscoveryFormatError):
                    discovery_from_dict(data)

    def test_rejects_executable_outside_the_fixed_relative_location(self):
        data = copy.deepcopy(VALID_REPORT)
        data["executable"]["relativePath"] = "../Other.exe"

        with self.assertRaisesRegex(SkyrimDiscoveryFormatError, "SkyrimSE.exe"):
            discovery_from_dict(data)


if __name__ == "__main__":
    unittest.main()
