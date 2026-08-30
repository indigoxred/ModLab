import unittest

from modlab.artifacts.model import ArchiveArtifact
from modlab.artifacts.serialization import (
    ArtifactFormatError,
    artifact_from_dict,
    artifact_to_dict,
)


class ArtifactSerializationTests(unittest.TestCase):
    def test_round_trip_preserves_portable_identity_and_source_metadata(self):
        record = ArchiveArtifact(
            schema_version=1,
            artifact_id="archive-sha256:" + "a" * 64,
            sha256="a" * 64,
            size=123,
            original_name="Example Mod.7z",
            stored_relative_path="library/archives/aa/" + "a" * 64 + "/payload.7z",
            imported_at="2026-08-30T08:00:00Z",
            source_note="Old verified local download",
            source_url="https://example.invalid/mod",
        )

        restored = artifact_from_dict(artifact_to_dict(record))

        self.assertEqual(record, restored)
        self.assertNotIn("C:\\", restored.stored_relative_path)

    def test_rejects_identity_that_does_not_match_hash(self):
        data = artifact_to_dict(
            ArchiveArtifact(
                1,
                "archive-sha256:" + "a" * 64,
                "a" * 64,
                1,
                "mod.zip",
                "library/archives/aa/" + "a" * 64 + "/payload.zip",
                "2026-08-30T08:00:00Z",
                "local",
                None,
            )
        )
        data["artifactId"] = "archive-sha256:" + "b" * 64

        with self.assertRaisesRegex(ArtifactFormatError, "artifactId"):
            artifact_from_dict(data)

    def test_rejects_absolute_or_parent_traversing_stored_path(self):
        data = {
            "schemaVersion": 1,
            "artifactId": "archive-sha256:" + "a" * 64,
            "sha256": "a" * 64,
            "size": 1,
            "originalName": "mod.zip",
            "storedRelativePath": "../outside.zip",
            "importedAt": "2026-08-30T08:00:00Z",
            "sourceNote": "local",
            "sourceUrl": None,
        }

        with self.assertRaisesRegex(ArtifactFormatError, "storedRelativePath"):
            artifact_from_dict(data)
