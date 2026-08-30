import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modlab.artifacts.vault import ArchiveImportError, ArchiveVault


IMPORTED_AT = "2026-08-30T08:00:00Z"
MOD_ARCHIVE_SHA256 = "e8675c0b9bdb62561503c50a19d0b948c353e0b5016a1a3299f73aded414e0a8"
CHANGED_ARCHIVE_SHA256 = "b21e837baffc37f61d453c447a3958e04df7959ef028ed7fdb6d5bf779ff99a8"


class ArchiveVaultImportTests(unittest.TestCase):
    def test_import_copies_source_and_records_hand_checked_sha256(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "Example Mod.7z"
            source.write_bytes(b"mod archive")
            vault = ArchiveVault(base / "workspace")

            record = vault.import_archive(
                source,
                source_note="Old verified local download",
                source_url="https://example.invalid/mod",
                imported_at=IMPORTED_AT,
            )

            self.assertEqual(b"mod archive", source.read_bytes())
            self.assertEqual(MOD_ARCHIVE_SHA256, record.sha256)
            self.assertEqual(f"archive-sha256:{MOD_ARCHIVE_SHA256}", record.artifact_id)
            self.assertEqual(len(b"mod archive"), record.size)
            self.assertEqual(b"mod archive", record.stored_path(vault.workspace_root).read_bytes())
            metadata_path = (
                vault.workspace_root / "library" / "metadata" / "artifacts" / f"{MOD_ARCHIVE_SHA256}.json"
            )
            self.assertTrue(metadata_path.is_file())
            self.assertEqual(record.artifact_id, json.loads(metadata_path.read_text(encoding="utf-8"))["artifactId"])

    def test_same_bytes_under_another_filename_reuse_one_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            first = base / "first.zip"
            second = base / "renamed.rar"
            first.write_bytes(b"mod archive")
            second.write_bytes(b"mod archive")
            vault = ArchiveVault(base / "workspace")

            first_record = vault.import_archive(first, "first source", imported_at=IMPORTED_AT)
            second_record = vault.import_archive(second, "second source", imported_at=IMPORTED_AT)

            self.assertEqual(first_record.artifact_id, second_record.artifact_id)
            self.assertEqual(first_record, second_record)
            self.assertEqual(1, len(list((vault.workspace_root / "library" / "archives").rglob("payload.*"))))

    def test_changed_bytes_create_a_different_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            first = base / "mod.zip"
            second = base / "mod-update.zip"
            first.write_bytes(b"mod archive")
            second.write_bytes(b"changed archive")
            vault = ArchiveVault(base / "workspace")

            first_record = vault.import_archive(first, "first", imported_at=IMPORTED_AT)
            second_record = vault.import_archive(second, "second", imported_at=IMPORTED_AT)

            self.assertEqual(MOD_ARCHIVE_SHA256, first_record.sha256)
            self.assertEqual(CHANGED_ARCHIVE_SHA256, second_record.sha256)
            self.assertNotEqual(first_record.artifact_id, second_record.artifact_id)
            self.assertTrue(first_record.stored_path(vault.workspace_root).is_file())
            self.assertTrue(second_record.stored_path(vault.workspace_root).is_file())

    def test_unsupported_and_empty_files_create_no_metadata_or_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            executable = base / "not-a-mod.exe"
            empty = base / "empty.zip"
            executable.write_bytes(b"executable")
            empty.write_bytes(b"")
            vault = ArchiveVault(base / "workspace")

            with self.assertRaises(ArchiveImportError):
                vault.import_archive(executable, "unsafe", imported_at=IMPORTED_AT)
            with self.assertRaises(ArchiveImportError):
                vault.import_archive(empty, "empty", imported_at=IMPORTED_AT)

            self.assertEqual([], list((vault.workspace_root / "library" / "archives").rglob("payload.*")))
            self.assertEqual([], list((vault.workspace_root / "library" / "metadata" / "artifacts").glob("*.json")))

    def test_copy_failure_removes_only_its_staging_file(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "mod.zip"
            source.write_bytes(b"mod archive")
            vault = ArchiveVault(base / "workspace")

            with patch("modlab.artifacts.vault._copy_and_hash", side_effect=OSError("copy failed")):
                with self.assertRaisesRegex(ArchiveImportError, "mod.zip"):
                    vault.import_archive(source, "local", imported_at=IMPORTED_AT)

            self.assertEqual(b"mod archive", source.read_bytes())
            self.assertEqual([], list((vault.workspace_root / "runtime" / "jobs").glob("import-*.part")))
            self.assertEqual([], list((vault.workspace_root / "library" / "archives").rglob("payload.*")))
            self.assertEqual([], list((vault.workspace_root / "library" / "metadata" / "artifacts").glob("*.json")))


if __name__ == "__main__":
    unittest.main()
