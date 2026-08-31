import tempfile
import unittest
import os
from pathlib import Path

from modlab.adapters.mo2.readset import Mo2ReadSet


class Mo2ReadSetTests(unittest.TestCase):
    def test_stable_file_optional_file_and_directory_are_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "state.txt").write_bytes(b"state")
            read_set = Mo2ReadSet()
            self.assertEqual(b"state", read_set.read_bytes(root / "state.txt"))
            self.assertIsNone(read_set.optional_bytes(root / "optional.txt"))
            self.assertEqual(
                ("state.txt",),
                tuple(item.name for item in read_set.list_directory(root)),
            )

            result = read_set.verify()

            self.assertTrue(result.stable)
            self.assertEqual(64, len(result.sha256))
            self.assertEqual((), result.changed_paths)

    def test_detects_file_change_optional_creation_and_directory_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state.txt"
            state.write_bytes(b"before")
            read_set = Mo2ReadSet()
            read_set.read_bytes(state)
            read_set.optional_bytes(root / "optional.txt")
            read_set.list_directory(root)

            state.write_bytes(b"after")
            (root / "optional.txt").write_bytes(b"new")
            (root / "new-folder").mkdir()
            result = read_set.verify()

            self.assertFalse(result.stable)
            self.assertIn(str(state), result.changed_paths)
            self.assertIn(str(root / "optional.txt"), result.changed_paths)
            self.assertIn(str(root), result.changed_paths)

    def test_regular_file_metadata_observation_tracks_presence_without_payload_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            required = root / "required.esm"
            required.write_bytes(b"payload")
            read_set = Mo2ReadSet()

            observed = read_set.required_regular_file(required)
            optional = read_set.optional_regular_file(root / "optional.esl")

            self.assertTrue(observed.present)
            self.assertEqual(7, observed.size)
            self.assertFalse(optional.present)
            self.assertTrue(read_set.verify().stable)

    def test_regular_file_metadata_verification_detects_change_and_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            required = root / "required.esm"
            optional = root / "optional.esl"
            required.write_bytes(b"payload")
            read_set = Mo2ReadSet()
            first = read_set.required_regular_file(required)
            read_set.optional_regular_file(optional)

            required.write_bytes(b"changed")
            os.utime(
                required,
                ns=(first.modified_ns + 2_000_000_000,) * 2,
            )
            optional.write_bytes(b"new")
            result = read_set.verify()

            self.assertFalse(result.stable)
            self.assertIn(str(required), result.changed_paths)
            self.assertIn(str(optional), result.changed_paths)


if __name__ == "__main__":
    unittest.main()
