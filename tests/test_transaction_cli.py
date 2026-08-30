import io
import json
import tempfile
import unittest
from pathlib import Path

from modlab.cli import main
from modlab.transactions.manager import TransactionManager
from modlab.transactions.model import ChangeOperation, RequestedChange


FROM_CHECKPOINT = "checkpoint-sha256:" + "a" * 64
TO_CHECKPOINT = "checkpoint-sha256:" + "b" * 64
TRANSACTION_ID = "transaction:" + "1" * 32


class TransactionCliTests(unittest.TestCase):
    def prepare_transaction(self, directory: str):
        base = Path(directory)
        workspace = base / "workspace"
        play = base / "play"
        staged = base / "staged"
        (play / "profiles" / "Play").mkdir(parents=True)
        (staged / "profiles" / "Play").mkdir(parents=True)
        target = play / "profiles" / "Play" / "modlist.txt"
        desired = staged / "profiles" / "Play" / "modlist.txt"
        target.write_bytes(b"old")
        desired.write_bytes(b"new")
        manager = TransactionManager(workspace)
        journal = manager.prepare(
            play,
            staged,
            (RequestedChange("profiles/Play/modlist.txt", ChangeOperation.REPLACE),),
            FROM_CHECKPOINT,
            TO_CHECKPOINT,
            transaction_id=TRANSACTION_ID,
            created_at="2026-08-30T10:00:00Z",
        )
        return workspace, manager, journal, target

    def test_list_empty_store_is_read_only_and_machine_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory, "workspace")
            stdout, stderr = io.StringIO(), io.StringIO()

            code = main(
                [
                    "transaction",
                    "list",
                    "--workspace",
                    str(workspace),
                    "--format",
                    "json",
                ],
                stdout,
                stderr,
            )

            result = json.loads(stdout.getvalue())
            self.assertEqual(0, code)
            self.assertEqual([], result["transactions"])
            self.assertEqual([], result["actionsPerformed"])
            self.assertEqual([], result["installationActionsPerformed"])
            self.assertEqual("", stderr.getvalue())
            self.assertFalse(workspace.exists())

    def test_show_returns_exact_journal_without_changing_play(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, _, journal, target = self.prepare_transaction(directory)
            stdout, stderr = io.StringIO(), io.StringIO()

            code = main(
                [
                    "transaction",
                    "show",
                    TRANSACTION_ID,
                    "--workspace",
                    str(workspace),
                    "--format",
                    "json",
                ],
                stdout,
                stderr,
            )

            result = json.loads(stdout.getvalue())
            self.assertEqual(0, code)
            self.assertEqual(journal.plan_sha256, result["transaction"]["planSha256"])
            self.assertEqual("Prepared", result["transaction"]["state"])
            self.assertEqual([], result["actionsPerformed"])
            self.assertEqual([], result["installationActionsPerformed"])
            self.assertEqual(b"old", target.read_bytes())
            self.assertEqual("", stderr.getvalue())

    def test_verify_reports_available_modified_and_missing_without_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, manager, journal, target = self.prepare_transaction(directory)
            available_out = io.StringIO()

            available_code = main(
                [
                    "transaction",
                    "verify",
                    TRANSACTION_ID,
                    "--workspace",
                    str(workspace),
                ],
                available_out,
                io.StringIO(),
            )
            desired = manager.transaction_directory(TRANSACTION_ID) / Path(
                *journal.entries[0].desired.snapshot_relative_path.split("/")
            )
            desired.write_bytes(b"tampered")
            modified_out = io.StringIO()
            modified_code = main(
                [
                    "transaction",
                    "verify",
                    TRANSACTION_ID,
                    "--workspace",
                    str(workspace),
                ],
                modified_out,
                io.StringIO(),
            )
            manager.path_for(TRANSACTION_ID).unlink()
            missing_out = io.StringIO()
            missing_code = main(
                [
                    "transaction",
                    "verify",
                    TRANSACTION_ID,
                    "--workspace",
                    str(workspace),
                ],
                missing_out,
                io.StringIO(),
            )

            self.assertEqual(0, available_code)
            self.assertIn("Available", available_out.getvalue())
            self.assertEqual(3, modified_code)
            self.assertIn("Modified", modified_out.getvalue())
            self.assertEqual(3, missing_code)
            self.assertIn("Missing", missing_out.getvalue())
            self.assertEqual(b"tampered", desired.read_bytes())
            self.assertEqual(b"old", target.read_bytes())

    def test_verify_reports_malformed_journal_as_modified_without_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, manager, _, target = self.prepare_transaction(directory)
            journal_path = manager.path_for(TRANSACTION_ID)
            malformed = b'{"transactionId": "broken"'
            journal_path.write_bytes(malformed)
            stdout, stderr = io.StringIO(), io.StringIO()

            code = main(
                [
                    "transaction",
                    "verify",
                    TRANSACTION_ID,
                    "--workspace",
                    str(workspace),
                    "--format",
                    "json",
                ],
                stdout,
                stderr,
            )

            result = json.loads(stdout.getvalue())
            self.assertEqual(3, code)
            self.assertEqual("Modified", result["finding"]["health"])
            self.assertEqual([], result["actionsPerformed"])
            self.assertEqual([], result["installationActionsPerformed"])
            self.assertEqual(malformed, journal_path.read_bytes())
            self.assertEqual(b"old", target.read_bytes())
            self.assertEqual("", stderr.getvalue())

    def test_malformed_or_unknown_show_returns_transaction_error(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory, "workspace")
            for transaction_id in (
                "../../outside",
                "transaction:" + "f" * 32,
            ):
                with self.subTest(transaction_id=transaction_id):
                    stdout, stderr = io.StringIO(), io.StringIO()
                    code = main(
                        [
                            "transaction",
                            "show",
                            transaction_id,
                            "--workspace",
                            str(workspace),
                        ],
                        stdout,
                        stderr,
                    )
                    self.assertEqual(2, code)
                    self.assertEqual("", stdout.getvalue())
                    self.assertIn("Transaction error:", stderr.getvalue())
                    self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
