import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from modlab.adapters.mo2.release import (
    Mo2ReleaseFormatError,
    bundled_mo2_252_path,
    load_mo2_release,
    load_mo2_release_bytes,
)


VALID_RELEASE = {
    "adapterId": "portable-mo2-skyrim",
    "allowedExtraFiles": [
        "categories.dat",
        "ModOrganizer.ini",
        "nexuscatmap.dat",
        "nxmhandler.ini",
        "nxmhandler.log",
    ],
    "allowPluginPythonBytecode": True,
    "allowRootLogs": True,
    "archiveEntryCount": 1780,
    "archiveListingSha256": (
        "c6eee6e0a9e80e3759c5bd75b701af4aa44d55859e71987f9b4bc1594057d0ed"
    ),
    "archiveName": "Mod.Organizer-2.5.2.7z",
    "archiveSha256": (
        "e6376efd87fd5ddd95aee959405e8f067afa526ea6c2c0c5aa03c5108bf4a815"
    ),
    "archiveSize": 149660212,
    "executable": {
        "fileVersion": "2.5.2.0",
        "relativePath": "ModOrganizer.exe",
        "sha256": (
            "442b354a8f34754da0048654c44d27f51628feba54ce46c3187cf58d6c43e622"
        ),
        "size": 5028352,
    },
    "extractedSize": 408536933,
    "minimumFreeBytes": 1073741824,
    "mutablePackagePaths": [],
    "packageFileCount": 1626,
    "productVersion": "2.5.2",
    "releaseId": "mo2.windows-portable.2.5.2",
    "schemaVersion": 1,
    "sentinels": [
        {
            "relativePath": "loot/loot.dll",
            "sha256": (
                "b21012f43e92ab5599b7be5d60550cc32806289a0a6bbb575c861b2d3d5a40cd"
            ),
            "size": 9333248,
        },
        {
            "relativePath": "plugins/game_skyrimse.dll",
            "sha256": (
                "5eaace8ec5e3f1e6dc6e85ffe22abdd30c99dfa414807e2d7e2ef242cc90a429"
            ),
            "size": 440320,
        },
        {
            "relativePath": "usvfs_x64.dll",
            "sha256": (
                "e2b766f418575021b9d350f384195ce6f23173169b37222cdef3d7fe5495f8b5"
            ),
            "size": 1854976,
        },
    ],
    "sourceAssetUrl": (
        "https://github.com/ModOrganizer2/modorganizer/releases/download/"
        "v2.5.2/Mod.Organizer-2.5.2.7z"
    ),
    "sourcePageUrl": (
        "https://github.com/ModOrganizer2/modorganizer/releases/tag/v2.5.2"
    ),
    "supportedPlatform": "windows-x86_64",
}


def valid_release_bytes(value: object = VALID_RELEASE) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _changed(*path_and_value: object):
    *path, replacement = path_and_value

    def mutate(data: bytes) -> bytes:
        value = json.loads(data)
        target = value
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = replacement
        return valid_release_bytes(value)

    return mutate


def _duplicate_schema_key(data: bytes) -> bytes:
    marker = b'  "schemaVersion": 1,\n'
    return data.replace(marker, marker + marker, 1)


def _extra_field(data: bytes) -> bytes:
    value = json.loads(data)
    value["unexpected"] = "value"
    return valid_release_bytes(value)


def _unsorted_allowed_extras(data: bytes) -> bytes:
    value = json.loads(data)
    value["allowedExtraFiles"][0:2] = reversed(value["allowedExtraFiles"][0:2])
    return valid_release_bytes(value)


def release_mutation_cases():
    return (
        (_duplicate_schema_key, "duplicate JSON key"),
        (_extra_field, "fields differ"),
        (_unsorted_allowed_extras, "allowedExtraFiles must be sorted"),
        (_changed("sentinels", 0, "relativePath", "../loot.dll"), "safe relative path"),
        (_changed("archiveSize", True), "archiveSize must be a positive integer"),
        (_changed("productVersion", "2.5.3"), "productVersion"),
        (_changed("executable", "sha256", "0" * 64), "executable identity"),
        (_changed("allowedExtraFiles", ["Saves/slot.ess"]), "save"),
    )


class Mo2ReleaseTests(unittest.TestCase):
    def test_bundled_descriptor_has_observed_exact_identities(self):
        loaded = load_mo2_release(bundled_mo2_252_path())
        value = loaded.descriptor

        self.assertEqual("mo2.windows-portable.2.5.2", value.release_id)
        self.assertEqual(
            "e6376efd87fd5ddd95aee959405e8f067afa526ea6c2c0c5aa03c5108bf4a815",
            value.archive_sha256,
        )
        self.assertEqual(149660212, value.archive_size)
        self.assertEqual(1780, value.archive_entry_count)
        self.assertEqual(
            "c6eee6e0a9e80e3759c5bd75b701af4aa44d55859e71987f9b4bc1594057d0ed",
            value.archive_listing_sha256,
        )
        self.assertEqual(1626, value.package_file_count)
        self.assertEqual(408536933, value.extracted_size)
        self.assertEqual("2.5.2.0", value.executable.file_version)
        self.assertIn("nxmhandler.ini", value.allowed_extra_files)
        self.assertEqual(loaded.path.read_bytes(), loaded.data)
        self.assertEqual(hashlib.sha256(loaded.data).hexdigest(), loaded.sha256)

    def test_duplicate_extra_unsorted_and_unsafe_fields_are_rejected(self):
        for mutation, reason in release_mutation_cases():
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(Mo2ReleaseFormatError, reason):
                    load_mo2_release_bytes(
                        mutation(valid_release_bytes()), Path("fixture.json")
                    )

    def test_source_change_during_load_is_rejected(self):
        first = valid_release_bytes()
        changed = _changed("archiveSize", 149660213)(first)
        with patch.object(Path, "read_bytes", side_effect=(first, changed)):
            with self.assertRaisesRegex(Mo2ReleaseFormatError, "changed while reading"):
                load_mo2_release(Path("changing.json"))


if __name__ == "__main__":
    unittest.main()
