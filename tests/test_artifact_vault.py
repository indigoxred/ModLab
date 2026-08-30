import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modlab.artifacts.model import ArtifactHealth
from modlab.artifacts.serialization import ArtifactFormatError
from modlab.artifacts.vault import (
    ArtifactNotFoundError,
    ArchiveImportError,
    ArchiveVault,
)


IMPORTED_AT = "2026-08-30T08:00:00Z"
MOD_ARCHIVE_SHA256 = "e8675c0b9bdb62561503c50a19d0b948c353e0b5016a1a3299f73aded414e0a8"
CHANGED_ARCHIVE_SHA256 = "b21e837baffc37f61d453c447a3958e04df7959ef028ed7fdb6d5bf779ff99a8"
SECOND_ARCHIVE_SHA256 = "cb7469f44122ba751d137a8fef6a36b8e56c6b524a35d7abfa7677955a252a4d"


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

    def test_source_inspection_failure_is_reported_as_an_import_error(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "mod.zip"
            source.write_bytes(b"mod archive")
            vault = ArchiveVault(base / "workspace")

            with (
                patch.object(Path, "is_file", return_value=True),
                patch.object(Path, "stat", side_effect=OSError("access changed")),
                self.assertRaisesRegex(ArchiveImportError, "Could not inspect"),
            ):
                vault.import_archive(source, "local", imported_at=IMPORTED_AT)

    def test_metadata_promotion_failure_rolls_back_new_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "mod.zip"
            source.write_bytes(b"mod archive")
            vault = ArchiveVault(base / "workspace")
            real_replace = os.replace

            def fail_metadata_replace(source_path, destination_path):
                if Path(destination_path).suffix == ".json":
                    raise OSError("metadata promotion failed")
                return real_replace(source_path, destination_path)

            with patch("modlab.artifacts.vault.os.replace", side_effect=fail_metadata_replace):
                with self.assertRaisesRegex(ArchiveImportError, "metadata promotion failed"):
                    vault.import_archive(source, "local", imported_at=IMPORTED_AT)

            self.assertEqual(b"mod archive", source.read_bytes())
            self.assertEqual([], list((vault.workspace_root / "library" / "archives").rglob("payload.*")))
            self.assertEqual([], list(vault.metadata_path.glob("*.json")))
            self.assertEqual([], list(vault.metadata_path.glob("*.part")))
            self.assertEqual([], list(vault.jobs_path.glob("import-*.part")))


class ArchiveVaultVerificationTests(unittest.TestCase):
    def test_list_sorts_by_imported_time_then_artifact_id(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            late = base / "late.zip"
            early_b = base / "early-b.7z"
            early_c = base / "early-c.rar"
            late.write_bytes(b"mod archive")
            early_b.write_bytes(b"changed archive")
            early_c.write_bytes(b"second archive")
            vault = ArchiveVault(base / "workspace")
            vault.import_archive(late, "late", imported_at="2026-08-30T09:00:00Z")
            vault.import_archive(early_c, "early c", imported_at=IMPORTED_AT)
            vault.import_archive(early_b, "early b", imported_at=IMPORTED_AT)

            records = vault.list()

            self.assertEqual(
                [CHANGED_ARCHIVE_SHA256, SECOND_ARCHIVE_SHA256, MOD_ARCHIVE_SHA256],
                [record.sha256 for record in records],
            )

    def test_get_rejects_malformed_and_reports_unknown_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            vault = ArchiveVault(Path(directory, "workspace"))

            with self.assertRaisesRegex(ArtifactFormatError, "artifact ID"):
                vault.get("../../outside.json")
            unknown_id = "archive-sha256:" + "f" * 64
            with self.assertRaisesRegex(ArtifactNotFoundError, unknown_id):
                vault.get(unknown_id)

    def test_exact_stored_bytes_are_available(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "mod.zip"
            source.write_bytes(b"mod archive")
            vault = ArchiveVault(base / "workspace")
            record = vault.import_archive(source, "local", imported_at=IMPORTED_AT)

            finding = vault.verify(record.artifact_id)

            self.assertEqual(ArtifactHealth.AVAILABLE, finding.health)
            self.assertEqual(MOD_ARCHIVE_SHA256, finding.expected_sha256)
            self.assertEqual(MOD_ARCHIVE_SHA256, finding.actual_sha256)

    def test_deleted_payload_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "mod.zip"
            source.write_bytes(b"mod archive")
            vault = ArchiveVault(base / "workspace")
            record = vault.import_archive(source, "local", imported_at=IMPORTED_AT)
            record.stored_path(vault.workspace_root).unlink()

            finding = vault.verify(record.artifact_id)

            self.assertEqual(ArtifactHealth.MISSING, finding.health)
            self.assertIsNone(finding.actual_sha256)
            self.assertFalse(record.stored_path(vault.workspace_root).exists())

    def test_changed_payload_is_modified_but_never_repaired(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "mod.zip"
            source.write_bytes(b"mod archive")
            vault = ArchiveVault(base / "workspace")
            record = vault.import_archive(source, "local", imported_at=IMPORTED_AT)
            stored = record.stored_path(vault.workspace_root)
            stored.write_bytes(b"changed archive")

            finding = vault.verify(record.artifact_id)

            self.assertEqual(ArtifactHealth.MODIFIED, finding.health)
            self.assertEqual(CHANGED_ARCHIVE_SHA256, finding.actual_sha256)
            self.assertEqual(b"changed archive", stored.read_bytes())

    def test_malformed_metadata_is_not_hidden_from_listing(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "mod.zip"
            source.write_bytes(b"mod archive")
            vault = ArchiveVault(base / "workspace")
            vault.import_archive(source, "local", imported_at=IMPORTED_AT)
            metadata = vault.metadata_path / f"{MOD_ARCHIVE_SHA256}.json"
            metadata.write_text("{}", encoding="utf-8")

            with self.assertRaises(ArtifactFormatError):
                vault.list()

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
