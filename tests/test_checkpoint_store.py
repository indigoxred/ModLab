import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modlab.checkpoints.serialization import CheckpointFormatError, draft_from_dict
from modlab.checkpoints.store import CheckpointStore, CheckpointStoreError
from tests.test_checkpoint_serialization import VALID_DRAFT


class CheckpointStoreTests(unittest.TestCase):
    def test_create_writes_content_addressed_lockfile_and_detaches_input(self):
        with tempfile.TemporaryDirectory() as directory:
            data = copy.deepcopy(VALID_DRAFT)
            draft = draft_from_dict(data)
            data["adapterState"]["profile"] = "Play"
            store = CheckpointStore(Path(directory, "workspace"), "skyrim-se-ae")

            record = store.create(draft)

            sha256 = record.checkpoint_id.removeprefix("checkpoint-sha256:")
            lockfile = store.checkpoints_path / sha256 / "modlab.lock.json"
            self.assertTrue(lockfile.is_file())
            self.assertEqual("Lab", json.loads(record.adapter_state_json)["profile"])
            stored = json.loads(lockfile.read_text(encoding="utf-8"))
            self.assertEqual(record.checkpoint_id, stored["checkpointId"])

    def test_identical_draft_reuses_one_checkpoint_but_changed_state_does_not(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CheckpointStore(Path(directory, "workspace"), "skyrim-se-ae")
            original = draft_from_dict(VALID_DRAFT)
            changed_data = copy.deepcopy(VALID_DRAFT)
            changed_data["adapterState"]["profile"] = "Play"
            changed = draft_from_dict(changed_data)

            first = store.create(original)
            duplicate = store.create(original)
            second = store.create(changed)

            self.assertEqual(first, duplicate)
            self.assertNotEqual(first.checkpoint_id, second.checkpoint_id)
            self.assertEqual(2, len(list(store.checkpoints_path.glob("*/modlab.lock.json"))))

    def test_child_requires_existing_parent_in_same_game_store(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CheckpointStore(Path(directory, "workspace"), "skyrim-se-ae")
            missing_parent_data = copy.deepcopy(VALID_DRAFT)
            missing_parent_data["createdAt"] = "2026-08-30T09:01:00Z"
            missing_parent_data["parentCheckpointId"] = "checkpoint-sha256:" + "f" * 64

            with self.assertRaisesRegex(CheckpointStoreError, "parent checkpoint"):
                store.create(draft_from_dict(missing_parent_data))

            parent = store.create(draft_from_dict(VALID_DRAFT))
            child_data = copy.deepcopy(VALID_DRAFT)
            child_data["createdAt"] = "2026-08-30T09:01:00Z"
            child_data["parentCheckpointId"] = parent.checkpoint_id
            child_data["adapterState"]["profile"] = "Lab candidate 2"
            child = store.create(draft_from_dict(child_data))

            self.assertEqual(parent.checkpoint_id, child.parent_checkpoint_id)
            self.assertEqual(child, store.get(child.checkpoint_id))

    def test_promotion_failure_leaves_no_lockfile_or_staging_file(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory, "workspace")
            store = CheckpointStore(workspace, "skyrim-se-ae")

            with patch(
                "modlab.checkpoints.store.os.replace",
                side_effect=OSError("promotion failed"),
            ):
                with self.assertRaisesRegex(CheckpointStoreError, "promotion failed"):
                    store.create(draft_from_dict(VALID_DRAFT))

            self.assertEqual([], list(store.checkpoints_path.glob("*/modlab.lock.json")))
            self.assertEqual([], list((workspace / "runtime" / "jobs").glob("checkpoint-*.part")))

    def test_list_sorts_by_creation_time_then_id(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CheckpointStore(Path(directory, "workspace"), "skyrim-se-ae")
            late_data = copy.deepcopy(VALID_DRAFT)
            late_data["createdAt"] = "2026-08-30T10:00:00Z"
            early_b_data = copy.deepcopy(VALID_DRAFT)
            early_b_data["adapterState"]["revision"] = "b"
            early_c_data = copy.deepcopy(VALID_DRAFT)
            early_c_data["adapterState"]["revision"] = "c"
            late = store.create(draft_from_dict(late_data))
            early_c = store.create(draft_from_dict(early_c_data))
            early_b = store.create(draft_from_dict(early_b_data))

            records = store.list()

            early_expected = sorted(
                [early_b.checkpoint_id, early_c.checkpoint_id]
            )
            self.assertEqual(
                [*early_expected, late.checkpoint_id],
                [record.checkpoint_id for record in records],
            )

    def test_malformed_lockfile_is_not_hidden_from_listing(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CheckpointStore(Path(directory, "workspace"), "skyrim-se-ae")
            record = store.create(draft_from_dict(VALID_DRAFT))
            lockfile = store.path_for(record.checkpoint_id)
            lockfile.write_text("{}", encoding="utf-8")

            with self.assertRaises(CheckpointFormatError):
                store.list()


if __name__ == "__main__":
    unittest.main()
