import copy
import json
import unittest

from modlab.checkpoints.serialization import (
    CheckpointFormatError,
    checkpoint_from_dict,
    checkpoint_id_for_draft,
    checkpoint_record_from_draft,
    checkpoint_to_dict,
    draft_from_dict,
)


ARCHIVE_ID = "archive-sha256:" + "a" * 64
INSTALLED_ID = "installed-sha256:" + "b" * 64

VALID_DRAFT = {
    "schemaVersion": 1,
    "game": "skyrim-se-ae",
    "environmentId": "skyrim-stable",
    "adapterId": "mo2",
    "createdAt": "2026-08-30T09:00:00Z",
    "parentCheckpointId": None,
    "lineageId": "skyrim-main",
    "recipe": {
        "recipeId": "skyrim.current.foundation",
        "revision": "2026.08.30.1",
        "maturity": "Candidate",
        "identity": "Original",
    },
    "artifactIds": [INSTALLED_ID, ARCHIVE_ID],
    "adapterState": {
        "profile": "Lab",
        "plugins": [
            {"name": "Skyrim.esm", "enabled": True, "order": 0},
            {"name": "SkyUI_SE.esp", "enabled": True, "order": 1},
        ],
    },
    "evidence": {
        "findings": [],
        "test": {"status": "Not Run"},
    },
}


class CheckpointSerializationTests(unittest.TestCase):
    def test_round_trip_preserves_checkpoint_state_and_identity(self):
        draft = draft_from_dict(VALID_DRAFT)

        record = checkpoint_record_from_draft(draft)
        restored = checkpoint_from_dict(checkpoint_to_dict(record))

        self.assertEqual(record, restored)
        self.assertTrue(record.checkpoint_id.startswith("checkpoint-sha256:"))
        self.assertEqual((ARCHIVE_ID, INSTALLED_ID), record.artifact_ids)
        self.assertEqual("Lab", json.loads(record.adapter_state_json)["profile"])
        self.assertEqual("Not Run", json.loads(record.evidence_json)["test"]["status"])

    def test_mapping_key_order_does_not_change_identity(self):
        reordered = copy.deepcopy(VALID_DRAFT)
        reordered["adapterState"] = {
            "plugins": reordered["adapterState"]["plugins"],
            "profile": "Lab",
        }
        reordered["evidence"] = {
            "test": {"status": "Not Run"},
            "findings": [],
        }

        first = draft_from_dict(VALID_DRAFT)
        second = draft_from_dict(reordered)

        self.assertEqual(first.adapter_state_json, second.adapter_state_json)
        self.assertEqual(first.evidence_json, second.evidence_json)
        self.assertEqual(checkpoint_id_for_draft(first), checkpoint_id_for_draft(second))

    def test_rejects_unsafe_or_malformed_identities(self):
        cases = {
            "unsafe game": ("game", "../skyrim"),
            "bad environment": ("environmentId", "C:\\Skyrim"),
            "bad artifact hash": (
                "artifactIds",
                ["archive-sha256:" + "A" * 64],
            ),
            "duplicate artifact": (
                "artifactIds",
                [ARCHIVE_ID, ARCHIVE_ID],
            ),
        }
        for label, (field, value) in cases.items():
            with self.subTest(label=label):
                data = copy.deepcopy(VALID_DRAFT)
                data[field] = value
                with self.assertRaises(CheckpointFormatError):
                    draft_from_dict(data)

    def test_rejects_non_object_or_non_canonical_json_values(self):
        cases = {
            "state list": ("adapterState", []),
            "evidence list": ("evidence", []),
            "nested float": ("adapterState", {"scale": 1.5}),
            "non-string key": ("evidence", {1: "invalid"}),
        }
        for label, (field, value) in cases.items():
            with self.subTest(label=label):
                data = copy.deepcopy(VALID_DRAFT)
                data[field] = value
                with self.assertRaises(CheckpointFormatError):
                    draft_from_dict(data)

    def test_checkpoint_id_must_match_canonical_body(self):
        record = checkpoint_record_from_draft(draft_from_dict(VALID_DRAFT))
        data = checkpoint_to_dict(record)
        data["checkpointId"] = "checkpoint-sha256:" + "f" * 64

        with self.assertRaisesRegex(CheckpointFormatError, "checkpointId"):
            checkpoint_from_dict(data)


if __name__ == "__main__":
    unittest.main()
