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


class TransactionApplyAndRecoveryTests(unittest.TestCase):
    def prepare_three_changes(self, directory: str):
        base = Path(directory)
        workspace = base / "workspace"
        play = base / "play"
        staged = base / "staged"
        play_profile = play / "profiles" / "Play"
        staged_profile = staged / "profiles" / "Play"
        (play_profile / "saves").mkdir(parents=True)
        staged_profile.mkdir(parents=True)
        (play_profile / "modlist.txt").write_bytes(b"old mod list")
        (play_profile / "plugins.txt").write_bytes(b"old plugins")
        (play_profile / "obsolete.ini").write_bytes(b"old obsolete")
        (play_profile / "unrelated.txt").write_bytes(b"unrelated")
        (play_profile / "saves" / "Save001.ess").write_bytes(b"precious save")
        (play_profile / "saves" / "Save001.skse").write_bytes(b"precious cosave")
        (staged_profile / "modlist.txt").write_bytes(b"new mod list")
        (staged_profile / "plugins.txt").write_bytes(b"new plugins")
        manager = TransactionManager(workspace)
        journal = manager.prepare(
            play,
            staged,
            (
                RequestedChange("profiles/Play/modlist.txt", ChangeOperation.REPLACE),
                RequestedChange("profiles/Play/plugins.txt", ChangeOperation.REPLACE),
                RequestedChange("profiles/Play/obsolete.ini", ChangeOperation.DELETE),
            ),
            FROM_CHECKPOINT,
            TO_CHECKPOINT,
            transaction_id=TRANSACTION_ID,
            created_at=CREATED_AT,
        )
        return manager, journal, play_profile

    def assert_protected_files_unchanged(self, play_profile: Path):
        self.assertEqual(b"unrelated", (play_profile / "unrelated.txt").read_bytes())
        self.assertEqual(
            b"precious save",
            (play_profile / "saves" / "Save001.ess").read_bytes(),
        )
        self.assertEqual(
            b"precious cosave",
            (play_profile / "saves" / "Save001.skse").read_bytes(),
        )

    def test_apply_then_commit_changes_only_allowlisted_files(self):
        with tempfile.TemporaryDirectory() as directory:
            manager, _, play_profile = self.prepare_three_changes(directory)

            applied = manager.apply(
                TRANSACTION_ID, updated_at="2026-08-30T10:01:00Z"
            )

            self.assertEqual(TransactionState.APPLIED, applied.state)
            self.assertEqual(b"new mod list", (play_profile / "modlist.txt").read_bytes())
            self.assertEqual(b"new plugins", (play_profile / "plugins.txt").read_bytes())
            self.assertFalse((play_profile / "obsolete.ini").exists())
            self.assert_protected_files_unchanged(play_profile)

            committed = manager.commit(
                TRANSACTION_ID, updated_at="2026-08-30T10:02:00Z"
            )

            self.assertEqual(TransactionState.COMMITTED, committed.state)
            self.assertTrue(
                (
                    manager.transaction_directory(TRANSACTION_ID)
                    / "prior"
                    / "profiles"
                    / "Play"
                    / "modlist.txt"
                ).is_file()
            )
            self.assert_protected_files_unchanged(play_profile)

    def test_target_drift_blocks_apply_before_any_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            manager, _, play_profile = self.prepare_three_changes(directory)
            (play_profile / "plugins.txt").write_bytes(b"manual edit")

            with self.assertRaisesRegex(TransactionManagerError, "drift"):
                manager.apply(TRANSACTION_ID, updated_at="2026-08-30T10:01:00Z")

            self.assertEqual(b"old mod list", (play_profile / "modlist.txt").read_bytes())
            self.assertEqual(b"manual edit", (play_profile / "plugins.txt").read_bytes())
            self.assertEqual(b"old obsolete", (play_profile / "obsolete.ini").read_bytes())
            self.assertEqual(TransactionState.PREPARED, manager.load(TRANSACTION_ID).state)
            self.assert_protected_files_unchanged(play_profile)

    def test_desired_snapshot_drift_blocks_apply_before_any_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            manager, journal, play_profile = self.prepare_three_changes(directory)
            desired = next(
                entry.desired
                for entry in journal.entries
                if entry.relative_path == "profiles/Play/modlist.txt"
            )
            snapshot = (
                manager.transaction_directory(TRANSACTION_ID)
                / Path(*desired.snapshot_relative_path.split("/"))
            )
            snapshot.write_bytes(b"tampered desired snapshot")

            with self.assertRaisesRegex(TransactionManagerError, "snapshot drift"):
                manager.apply(TRANSACTION_ID, updated_at="2026-08-30T10:01:00Z")

            self.assertEqual(b"old mod list", (play_profile / "modlist.txt").read_bytes())
            self.assertEqual(b"old plugins", (play_profile / "plugins.txt").read_bytes())
            self.assertEqual(b"old obsolete", (play_profile / "obsolete.ini").read_bytes())
            self.assertEqual(TransactionState.PREPARED, manager.load(TRANSACTION_ID).state)
            self.assert_protected_files_unchanged(play_profile)

    def test_caught_mid_apply_failure_restores_every_prior_state(self):
        with tempfile.TemporaryDirectory() as directory:
            manager, _, play_profile = self.prepare_three_changes(directory)
            from modlab.transactions import manager as manager_module

            real_promote = manager_module._promote_file
            calls = 0

            def fail_second(source, target, transaction_id):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("second replacement failed")
                return real_promote(source, target, transaction_id)

            with patch(
                "modlab.transactions.manager._promote_file",
                side_effect=fail_second,
            ):
                with self.assertRaisesRegex(TransactionManagerError, "second replacement failed"):
                    manager.apply(
                        TRANSACTION_ID, updated_at="2026-08-30T10:01:00Z"
                    )

            journal = manager.load(TRANSACTION_ID)
            self.assertEqual(TransactionState.ROLLED_BACK, journal.state)
            self.assertIn("second replacement failed", journal.error)
            self.assertEqual(b"old mod list", (play_profile / "modlist.txt").read_bytes())
            self.assertEqual(b"old plugins", (play_profile / "plugins.txt").read_bytes())
            self.assertEqual(b"old obsolete", (play_profile / "obsolete.ini").read_bytes())
            self.assertEqual([], list(play_profile.glob(".modlab-*.part")))
            self.assert_protected_files_unchanged(play_profile)

    def test_process_interruption_is_recovered_by_a_new_manager_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            manager, _, play_profile = self.prepare_three_changes(directory)
            from modlab.transactions import manager as manager_module

            real_promote = manager_module._promote_file
            calls = 0

            def interrupt_second(source, target, transaction_id):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise KeyboardInterrupt("simulated process loss")
                return real_promote(source, target, transaction_id)

            with patch(
                "modlab.transactions.manager._promote_file",
                side_effect=interrupt_second,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    manager.apply(
                        TRANSACTION_ID, updated_at="2026-08-30T10:01:00Z"
                    )

            self.assertEqual(TransactionState.APPLYING, manager.load(TRANSACTION_ID).state)
            self.assertEqual(b"new mod list", (play_profile / "modlist.txt").read_bytes())
            self.assertEqual(b"old plugins", (play_profile / "plugins.txt").read_bytes())

            restarted = TransactionManager(manager.workspace_root)
            recovered = restarted.recover(
                TRANSACTION_ID, updated_at="2026-08-30T10:02:00Z"
            )

            self.assertEqual(TransactionState.ROLLED_BACK, recovered.state)
            self.assertEqual(b"old mod list", (play_profile / "modlist.txt").read_bytes())
            self.assertEqual(b"old plugins", (play_profile / "plugins.txt").read_bytes())
            self.assertEqual(b"old obsolete", (play_profile / "obsolete.ini").read_bytes())
            self.assert_protected_files_unchanged(play_profile)

    def test_recovering_applied_transaction_restores_deleted_and_absent_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = base / "workspace"
            play = base / "play"
            staged = base / "staged"
            play_profile = play / "profiles" / "Play"
            staged_profile = staged / "profiles" / "Play"
            play_profile.mkdir(parents=True)
            staged_profile.mkdir(parents=True)
            (play_profile / "obsolete.ini").write_bytes(b"restore me")
            (staged_profile / "new.ini").write_bytes(b"remove on recovery")
            manager = TransactionManager(workspace)
            manager.prepare(
                play,
                staged,
                (
                    RequestedChange("profiles/Play/new.ini", ChangeOperation.REPLACE),
                    RequestedChange("profiles/Play/obsolete.ini", ChangeOperation.DELETE),
                ),
                FROM_CHECKPOINT,
                TO_CHECKPOINT,
                transaction_id=TRANSACTION_ID,
                created_at=CREATED_AT,
            )
            manager.apply(TRANSACTION_ID, updated_at="2026-08-30T10:01:00Z")

            recovered = manager.recover(
                TRANSACTION_ID, updated_at="2026-08-30T10:02:00Z"
            )

            self.assertEqual(TransactionState.ROLLED_BACK, recovered.state)
            self.assertFalse((play_profile / "new.ini").exists())
            self.assertEqual(b"restore me", (play_profile / "obsolete.ini").read_bytes())

    def test_missing_prior_snapshot_requires_recovery_without_more_play_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            manager, journal, play_profile = self.prepare_three_changes(directory)
            manager.apply(TRANSACTION_ID, updated_at="2026-08-30T10:01:00Z")
            prior = next(
                entry.prior
                for entry in journal.entries
                if entry.relative_path == "profiles/Play/modlist.txt"
            )
            snapshot = (
                manager.transaction_directory(TRANSACTION_ID)
                / Path(*prior.snapshot_relative_path.split("/"))
            )
            snapshot.unlink()

            with self.assertRaisesRegex(TransactionManagerError, "unavailable"):
                manager.recover(
                    TRANSACTION_ID, updated_at="2026-08-30T10:02:00Z"
                )

            self.assertEqual(
                TransactionState.RECOVERY_REQUIRED,
                manager.load(TRANSACTION_ID).state,
            )
            self.assertEqual(b"new mod list", (play_profile / "modlist.txt").read_bytes())
            self.assertEqual(b"new plugins", (play_profile / "plugins.txt").read_bytes())
            self.assertFalse((play_profile / "obsolete.ini").exists())
            self.assert_protected_files_unchanged(play_profile)

    def test_commit_refuses_modified_applied_state(self):
        with tempfile.TemporaryDirectory() as directory:
            manager, _, play_profile = self.prepare_three_changes(directory)
            manager.apply(TRANSACTION_ID, updated_at="2026-08-30T10:01:00Z")
            (play_profile / "modlist.txt").write_bytes(b"changed after apply")

            with self.assertRaisesRegex(TransactionManagerError, "desired state"):
                manager.commit(
                    TRANSACTION_ID, updated_at="2026-08-30T10:02:00Z"
                )

            self.assertEqual(TransactionState.APPLIED, manager.load(TRANSACTION_ID).state)
            self.assertEqual(b"changed after apply", (play_profile / "modlist.txt").read_bytes())
            self.assert_protected_files_unchanged(play_profile)


if __name__ == "__main__":
    unittest.main()
