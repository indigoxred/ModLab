import copy
import hashlib
import json
import unittest

from modlab.transactions.model import ChangeOperation, TransactionState
from modlab.transactions.serialization import (
    TransactionFormatError,
    journal_from_dict,
    journal_to_dict,
)


FROM_CHECKPOINT = "checkpoint-sha256:" + "a" * 64
TO_CHECKPOINT = "checkpoint-sha256:" + "b" * 64

VALID_JOURNAL = {
    "schemaVersion": 1,
    "transactionId": "transaction:" + "1" * 32,
    "state": "Prepared",
    "playRoot": "C:\\ModLab\\Play",
    "stagedRoot": "C:\\ModLab\\Staged",
    "fromCheckpointId": FROM_CHECKPOINT,
    "toCheckpointId": TO_CHECKPOINT,
    "createdAt": "2026-08-30T10:00:00Z",
    "updatedAt": "2026-08-30T10:00:00Z",
    "entries": [
        {
            "relativePath": "profiles/Play/modlist.txt",
            "operation": "Replace",
            "prior": {
                "present": True,
                "sha256": "c" * 64,
                "size": 12,
                "snapshotRelativePath": "prior/profiles/Play/modlist.txt",
            },
            "desired": {
                "present": True,
                "sha256": "d" * 64,
                "size": 13,
                "snapshotRelativePath": "desired/profiles/Play/modlist.txt",
            },
        },
        {
            "relativePath": "profiles/Play/obsolete.ini",
            "operation": "Delete",
            "prior": {
                "present": True,
                "sha256": "e" * 64,
                "size": 3,
                "snapshotRelativePath": "prior/profiles/Play/obsolete.ini",
            },
            "desired": {
                "present": False,
                "sha256": None,
                "size": None,
                "snapshotRelativePath": None,
            },
        },
    ],
    "error": None,
}


def _plan_sha256(data):
    immutable_fields = (
        "schemaVersion",
        "transactionId",
        "playRoot",
        "stagedRoot",
        "fromCheckpointId",
        "toCheckpointId",
        "createdAt",
        "entries",
    )
    payload = {field: data[field] for field in immutable_fields}
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


VALID_JOURNAL["planSha256"] = _plan_sha256(VALID_JOURNAL)


class TransactionSerializationTests(unittest.TestCase):
    def test_round_trip_preserves_allowlisted_file_states(self):
        journal = journal_from_dict(VALID_JOURNAL)

        restored = journal_from_dict(journal_to_dict(journal))

        self.assertEqual(journal, restored)
        self.assertEqual(TransactionState.PREPARED, journal.state)
        self.assertEqual(ChangeOperation.REPLACE, journal.entries[0].operation)
        self.assertEqual(ChangeOperation.DELETE, journal.entries[1].operation)

    def test_immutable_plan_tampering_is_rejected(self):
        data = copy.deepcopy(VALID_JOURNAL)
        data["playRoot"] = "D:\\Redirected\\Play"

        with self.assertRaisesRegex(TransactionFormatError, "planSha256"):
            journal_from_dict(data)

    def test_rejects_traversal_absolute_backslash_and_duplicate_paths(self):
        unsafe_paths = (
            "../outside.txt",
            "/absolute.txt",
            "C:/outside.txt",
            "profiles\\Play\\modlist.txt",
            "profiles//Play/modlist.txt",
            "profiles/./Play/modlist.txt",
        )
        for path in unsafe_paths:
            with self.subTest(path=path):
                data = copy.deepcopy(VALID_JOURNAL)
                data["entries"][0]["relativePath"] = path
                with self.assertRaises(TransactionFormatError):
                    journal_from_dict(data)

        duplicate = copy.deepcopy(VALID_JOURNAL)
        duplicate["entries"][1]["relativePath"] = duplicate["entries"][0]["relativePath"]
        duplicate["entries"][1]["prior"]["snapshotRelativePath"] = (
            "prior/profiles/Play/modlist.txt"
        )
        with self.assertRaisesRegex(TransactionFormatError, "duplicate"):
            journal_from_dict(duplicate)

    def test_rejects_save_and_cosave_targets(self):
        save_paths = (
            "profiles/Play/saves/Save001.ess",
            "profiles/Play/SAVES/Save001.ess",
            "profiles/Play/Save001.ess",
            "profiles/Play/Save001.skse",
        )
        for path in save_paths:
            with self.subTest(path=path):
                data = copy.deepcopy(VALID_JOURNAL)
                data["entries"][0]["relativePath"] = path
                with self.assertRaisesRegex(TransactionFormatError, "save|co-save"):
                    journal_from_dict(data)

    def test_rejects_impossible_snapshot_and_operation_combinations(self):
        cases = []

        absent_with_hash = copy.deepcopy(VALID_JOURNAL)
        absent_with_hash["entries"][0]["prior"]["present"] = False
        cases.append(absent_with_hash)

        present_without_hash = copy.deepcopy(VALID_JOURNAL)
        present_without_hash["entries"][0]["desired"]["sha256"] = None
        cases.append(present_without_hash)

        delete_with_desired_file = copy.deepcopy(VALID_JOURNAL)
        delete_with_desired_file["entries"][0]["operation"] = "Delete"
        cases.append(delete_with_desired_file)

        replace_without_desired_file = copy.deepcopy(VALID_JOURNAL)
        replace_without_desired_file["entries"][1]["operation"] = "Replace"
        cases.append(replace_without_desired_file)

        for index, data in enumerate(cases):
            with self.subTest(case=index):
                with self.assertRaises(TransactionFormatError):
                    journal_from_dict(data)

    def test_rejects_invalid_roots_identity_and_state(self):
        cases = {
            "relative play root": ("playRoot", "relative/play"),
            "same roots": ("stagedRoot", "C:\\ModLab\\Play"),
            "bad transaction": ("transactionId", "transaction:bad"),
            "bad checkpoint": ("toCheckpointId", "checkpoint-sha256:bad"),
            "bad state": ("state", "Finished"),
        }
        for label, (field, value) in cases.items():
            with self.subTest(label=label):
                data = copy.deepcopy(VALID_JOURNAL)
                data[field] = value
                with self.assertRaises(TransactionFormatError):
                    journal_from_dict(data)


if __name__ == "__main__":
    unittest.main()
