import copy
import json
import unittest

from modlab.adapters.mo2.model import Mo2InspectionReport
from modlab.adapters.mo2.serialization import (
    Mo2EvidenceFormatError,
    report_from_dict,
    report_from_json,
    report_to_dict,
    report_to_json,
)
from modlab.recipes.model import CheckState


VALID_REPORT = {
    "schemaVersion": 1,
    "adapterId": "portable-mo2-skyrim",
    "requestedRoot": "C:\\ModLab\\workspace\\tools\\mo2\\skyrim-se-ae\\app",
    "resolvedRoot": "C:\\ModLab\\workspace\\tools\\mo2\\skyrim-se-ae\\app",
    "gameRoot": "C:\\Steam\\steamapps\\common\\Skyrim Special Edition",
    "executable": {
        "relativePath": "ModOrganizer.exe",
        "fileVersion": "2.5.2.0",
        "sha256": "a" * 64,
        "size": 123456,
    },
    "portableConfigPresent": True,
    "configuredGamePath": "C:\\Steam\\steamapps\\common\\Skyrim Special Edition",
    "paths": [
        {
            "kind": "downloads",
            "configuredPath": "%BASE_DIR%/../downloads",
            "resolvedPath": "C:\\ModLab\\workspace\\tools\\mo2\\skyrim-se-ae\\downloads",
            "contained": True,
        },
        {
            "kind": "profiles",
            "configuredPath": "%BASE_DIR%/../profiles",
            "resolvedPath": "C:\\ModLab\\workspace\\tools\\mo2\\skyrim-se-ae\\profiles",
            "contained": True,
        },
    ],
    "activeProfile": "ModLab - Lab",
    "profiles": [
        {
            "name": "ModLab - Lab",
            "relativePath": "profiles/ModLab - Lab",
            "profileLocalSaves": False,
            "stateFiles": [
                {
                    "relativePath": "profiles/ModLab - Lab/modlist.txt",
                    "sha256": "b" * 64,
                    "size": 18,
                }
            ],
            "mods": [
                {"name": "DLC: HearthFires", "marker": "*", "enabled": True},
                {"name": "SkyUI", "marker": "+", "enabled": True},
            ],
            "plugins": [
                {"name": "Skyrim.esm", "enabled": True},
                {"name": "SkyUI_SE.esp", "enabled": True},
            ],
            "loadOrder": ["Skyrim.esm", "SkyUI_SE.esp"],
        },
        {
            "name": "ModLab - Play",
            "relativePath": "profiles/ModLab - Play",
            "profileLocalSaves": False,
            "stateFiles": [],
            "mods": [],
            "plugins": [],
            "loadOrder": [],
        },
    ],
    "topLevelMods": ["DLC: HearthFires", "SkyUI"],
    "overwriteEntries": [],
    "findings": [
        {
            "state": "Passed",
            "code": "portable-layout-contained",
            "message": "All writable MO2 paths remain under ModLab.",
        }
    ],
    "actions": [],
    "downloads": [],
    "installations": [],
    "programLaunches": [],
}


class Mo2SerializationTests(unittest.TestCase):
    def test_round_trip_is_canonical_and_keeps_actions_explicitly_empty(self):
        report = report_from_dict(VALID_REPORT)

        encoded = report_to_json(report)
        restored = report_from_json(encoded)

        self.assertIsInstance(restored, Mo2InspectionReport)
        self.assertEqual(report, restored)
        self.assertEqual(encoded, report_to_json(restored))
        self.assertEqual([], json.loads(encoded)["actions"])
        self.assertEqual(CheckState.PASSED, report.findings[0].state)

    def test_rejects_unknown_schema_status_duplicate_paths_and_nonempty_actions(self):
        cases = []

        schema = copy.deepcopy(VALID_REPORT)
        schema["schemaVersion"] = 2
        cases.append(schema)

        status = copy.deepcopy(VALID_REPORT)
        status["findings"][0]["state"] = "Fine"
        cases.append(status)

        duplicate_path = copy.deepcopy(VALID_REPORT)
        duplicate_path["paths"].append(copy.deepcopy(duplicate_path["paths"][0]))
        cases.append(duplicate_path)

        unsafe_state_path = copy.deepcopy(VALID_REPORT)
        unsafe_state_path["profiles"][0]["stateFiles"][0]["relativePath"] = (
            "../../../../Saves/save.ess"
        )
        cases.append(unsafe_state_path)

        actions = copy.deepcopy(VALID_REPORT)
        actions["actions"] = ["launch"]
        cases.append(actions)

        for index, value in enumerate(cases):
            with self.subTest(case=index):
                with self.assertRaises(Mo2EvidenceFormatError):
                    report_from_dict(value)

    def test_rejects_duplicate_json_keys_before_mapping_conversion(self):
        text = report_to_json(report_from_dict(VALID_REPORT))
        duplicate = text.replace(
            '"schemaVersion":1',
            '"schemaVersion":1,"schemaVersion":1',
            1,
        )

        with self.assertRaisesRegex(Mo2EvidenceFormatError, "duplicate JSON key"):
            report_from_json(duplicate)

    def test_to_dict_does_not_accept_unvalidated_action_data(self):
        report = report_from_dict(VALID_REPORT)
        value = report_to_dict(report)

        self.assertEqual([], value["downloads"])
        self.assertEqual([], value["installations"])
        self.assertEqual([], value["programLaunches"])


if __name__ == "__main__":
    unittest.main()
