import io
import json
import tempfile
import unittest
from pathlib import Path

from modlab.checkpoints.serialization import draft_from_dict
from modlab.checkpoints.store import CheckpointStore
from modlab.cli import main
from tests.test_checkpoint_serialization import VALID_DRAFT


class CheckpointCliTests(unittest.TestCase):
    def test_list_empty_store_is_read_only_and_machine_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory, "workspace")
            stdout, stderr = io.StringIO(), io.StringIO()

            code = main(
                [
                    "checkpoint",
                    "list",
                    "--workspace",
                    str(workspace),
                    "--game",
                    "skyrim-se-ae",
                    "--format",
                    "json",
                ],
                stdout,
                stderr,
            )

            result = json.loads(stdout.getvalue())
            self.assertEqual(0, code)
            self.assertEqual(1, result["schemaVersion"])
            self.assertEqual([], result["checkpoints"])
            self.assertEqual([], result["actionsPerformed"])
            self.assertEqual([], result["installationActionsPerformed"])
            self.assertEqual("", stderr.getvalue())
            self.assertFalse(workspace.exists())

    def test_show_returns_exact_checkpoint_without_promoting_it(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory, "workspace")
            store = CheckpointStore(workspace, "skyrim-se-ae")
            record = store.create(draft_from_dict(VALID_DRAFT))
            stdout, stderr = io.StringIO(), io.StringIO()

            code = main(
                [
                    "checkpoint",
                    "show",
                    record.checkpoint_id,
                    "--workspace",
                    str(workspace),
                    "--game",
                    "skyrim-se-ae",
                    "--format",
                    "json",
                ],
                stdout,
                stderr,
            )

            result = json.loads(stdout.getvalue())
            self.assertEqual(0, code)
            self.assertEqual(record.checkpoint_id, result["checkpoint"]["checkpointId"])
            self.assertEqual("Lab", result["checkpoint"]["adapterState"]["profile"])
            self.assertEqual([], result["actionsPerformed"])
            self.assertEqual([], result["installationActionsPerformed"])
            self.assertEqual("", stderr.getvalue())

    def test_verify_available_returns_zero_and_missing_returns_three(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory, "workspace")
            store = CheckpointStore(workspace, "skyrim-se-ae")
            record = store.create(draft_from_dict(VALID_DRAFT))
            available_out = io.StringIO()

            available_code = main(
                [
                    "checkpoint",
                    "verify",
                    record.checkpoint_id,
                    "--workspace",
                    str(workspace),
                    "--game",
                    "skyrim-se-ae",
                ],
                available_out,
                io.StringIO(),
            )
            store.path_for(record.checkpoint_id).unlink()
            missing_out = io.StringIO()
            missing_code = main(
                [
                    "checkpoint",
                    "verify",
                    record.checkpoint_id,
                    "--workspace",
                    str(workspace),
                    "--game",
                    "skyrim-se-ae",
                ],
                missing_out,
                io.StringIO(),
            )

            self.assertEqual(0, available_code)
            self.assertIn("Available", available_out.getvalue())
            self.assertEqual(3, missing_code)
            self.assertIn("Missing", missing_out.getvalue())

    def test_malformed_or_unknown_show_returns_checkpoint_error(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory, "workspace")
            for checkpoint_id in (
                "../../outside",
                "checkpoint-sha256:" + "f" * 64,
            ):
                with self.subTest(checkpoint_id=checkpoint_id):
                    stdout, stderr = io.StringIO(), io.StringIO()
                    code = main(
                        [
                            "checkpoint",
                            "show",
                            checkpoint_id,
                            "--workspace",
                            str(workspace),
                            "--game",
                            "skyrim-se-ae",
                        ],
                        stdout,
                        stderr,
                    )
                    self.assertEqual(2, code)
                    self.assertEqual("", stdout.getvalue())
                    self.assertIn("Checkpoint error:", stderr.getvalue())
                    self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
