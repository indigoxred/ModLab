import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modlab.transactions.manager import TransactionManager, TransactionManagerError
from modlab.transactions.model import ChangeOperation, RequestedChange, TransactionState


FROM_CHECKPOINT = "checkpoint-sha256:" + "a" * 64
TO_CHECKPOINT = "checkpoint-sha256:" + "b" * 64
TRANSACTION_ID = "transaction:" + "1" * 32
CREATED_AT = "2026-08-30T10:00:00Z"


class TransactionPreparationTests(unittest.TestCase):
    def make_roots(self, directory: str):
        base = Path(directory)
        workspace = base / "workspace"
        play = base / "play"
        staged = base / "staged"
        (play / "profiles" / "Play" / "saves").mkdir(parents=True)
        (staged / "profiles" / "Play").mkdir(parents=True)
        return workspace, play, staged

    def test_prepare_snapshots_both_states_without_changing_play_or_saves(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, play, staged = self.make_roots(directory)
            modlist = play / "profiles" / "Play" / "modlist.txt"
            obsolete = play / "profiles" / "Play" / "obsolete.ini"
            save = play / "profiles" / "Play" / "saves" / "Save001.ess"
            modlist.write_bytes(b"old mod list")
            obsolete.write_bytes(b"old")
            save.write_bytes(b"precious save")
            (staged / "profiles" / "Play" / "modlist.txt").write_bytes(b"new mod list")
            (staged / "profiles" / "Play" / "new.ini").write_bytes(b"new setting")
            manager = TransactionManager(workspace)

            journal = manager.prepare(
                play,
                staged,
                (
                    RequestedChange("profiles/Play/modlist.txt", ChangeOperation.REPLACE),
                    RequestedChange("profiles/Play/new.ini", ChangeOperation.REPLACE),
                    RequestedChange("profiles/Play/obsolete.ini", ChangeOperation.DELETE),
                ),
                FROM_CHECKPOINT,
                TO_CHECKPOINT,
                transaction_id=TRANSACTION_ID,
                created_at=CREATED_AT,
            )

            self.assertEqual(TransactionState.PREPARED, journal.state)
            self.assertEqual(b"old mod list", modlist.read_bytes())
            self.assertEqual(b"old", obsolete.read_bytes())
            self.assertFalse((play / "profiles" / "Play" / "new.ini").exists())
            self.assertEqual(b"precious save", save.read_bytes())
            tx_dir = manager.transaction_directory(TRANSACTION_ID)
            self.assertEqual(
                b"old mod list",
                (tx_dir / "prior" / "profiles" / "Play" / "modlist.txt").read_bytes(),
            )
            self.assertEqual(
                b"new mod list",
                (tx_dir / "desired" / "profiles" / "Play" / "modlist.txt").read_bytes(),
            )
            self.assertTrue((tx_dir / "journal.json").is_file())
            self.assertEqual((), manager.preflight(TRANSACTION_ID))

    def test_invalid_or_missing_desired_inputs_create_no_transaction(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, play, staged = self.make_roots(directory)
            manager = TransactionManager(workspace)
            cases = (
                RequestedChange("profiles/Play/missing.txt", ChangeOperation.REPLACE),
                RequestedChange("../outside.txt", ChangeOperation.DELETE),
                RequestedChange("profiles/Play/saves/Save001.ess", ChangeOperation.DELETE),
            )

            for change in cases:
                with self.subTest(path=change.relative_path):
                    with self.assertRaises(TransactionManagerError):
                        manager.prepare(
                            play,
                            staged,
                            (change,),
                            FROM_CHECKPOINT,
                            TO_CHECKPOINT,
                            created_at=CREATED_AT,
                        )

            self.assertFalse(manager.transactions_path.exists())

    def test_existing_transaction_id_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, play, staged = self.make_roots(directory)
            target = play / "profiles" / "Play" / "modlist.txt"
            desired = staged / "profiles" / "Play" / "modlist.txt"
            target.write_bytes(b"old")
            desired.write_bytes(b"new")
            manager = TransactionManager(workspace)
            change = RequestedChange("profiles/Play/modlist.txt", ChangeOperation.REPLACE)
            manager.prepare(
                play,
                staged,
                (change,),
                FROM_CHECKPOINT,
                TO_CHECKPOINT,
                transaction_id=TRANSACTION_ID,
                created_at=CREATED_AT,
            )
            journal_path = manager.path_for(TRANSACTION_ID)
            original_journal = journal_path.read_bytes()

            with self.assertRaisesRegex(TransactionManagerError, "already exists"):
                manager.prepare(
                    play,
                    staged,
                    (change,),
                    FROM_CHECKPOINT,
                    TO_CHECKPOINT,
                    transaction_id=TRANSACTION_ID,
                    created_at=CREATED_AT,
                )

            self.assertEqual(original_journal, journal_path.read_bytes())
            self.assertEqual(b"old", target.read_bytes())

    def test_snapshot_failure_removes_only_its_new_transaction_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, play, staged = self.make_roots(directory)
            target = play / "profiles" / "Play" / "modlist.txt"
            desired = staged / "profiles" / "Play" / "modlist.txt"
            target.write_bytes(b"old")
            desired.write_bytes(b"new")
            manager = TransactionManager(workspace)

            with patch(
                "modlab.transactions.manager._copy_snapshot",
                side_effect=OSError("snapshot failed"),
            ):
                with self.assertRaisesRegex(TransactionManagerError, "snapshot failed"):
                    manager.prepare(
                        play,
                        staged,
                        (RequestedChange("profiles/Play/modlist.txt", ChangeOperation.REPLACE),),
                        FROM_CHECKPOINT,
                        TO_CHECKPOINT,
                        transaction_id=TRANSACTION_ID,
                        created_at=CREATED_AT,
                    )

            self.assertFalse(manager.transaction_directory(TRANSACTION_ID).exists())
            self.assertEqual(b"old", target.read_bytes())
            self.assertEqual(b"new", desired.read_bytes())

    def test_preflight_reports_drift_without_mutating_journal_or_target(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, play, staged = self.make_roots(directory)
            target = play / "profiles" / "Play" / "modlist.txt"
            desired = staged / "profiles" / "Play" / "modlist.txt"
            target.write_bytes(b"old")
            desired.write_bytes(b"new")
            manager = TransactionManager(workspace)
            manager.prepare(
                play,
                staged,
                (RequestedChange("profiles/Play/modlist.txt", ChangeOperation.REPLACE),),
                FROM_CHECKPOINT,
                TO_CHECKPOINT,
                transaction_id=TRANSACTION_ID,
                created_at=CREATED_AT,
            )
            journal_before = manager.path_for(TRANSACTION_ID).read_bytes()
            target.write_bytes(b"manual edit")

            drift = manager.preflight(TRANSACTION_ID)

            self.assertEqual(("profiles/Play/modlist.txt",), drift)
            self.assertEqual(b"manual edit", target.read_bytes())
            self.assertEqual(journal_before, manager.path_for(TRANSACTION_ID).read_bytes())
            self.assertEqual(TransactionState.PREPARED, manager.load(TRANSACTION_ID).state)


if __name__ == "__main__":
    unittest.main()
