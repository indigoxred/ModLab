import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from modlab.adapters.mo2.archive import (
    Mo2ArchiveError,
    compare_package_to_existing,
    extract_and_inventory,
    inventory_tree,
    preflight_archive,
)
from tests.support.mo2_bootstrap import (
    FakeRunner,
    descriptor_for,
    descriptor_for_package,
    existing_app_fixture,
    extraction_package_files,
    package_inventory_fixture,
    unsafe_listing_cases,
    valid_names,
    valid_types,
)


class Mo2ArchiveTests(unittest.TestCase):
    def test_preflight_uses_argument_arrays_and_accepts_exact_listing(self):
        names = valid_names()
        runner = FakeRunner.for_listing(names, valid_types())

        result = preflight_archive(
            Path("payload.7z"),
            descriptor_for(names),
            Path(r"C:\Windows\System32\tar.exe"),
            runner=runner,
        )

        self.assertEqual(1780, result.entry_count)
        self.assertEqual(
            [
                [r"C:\Windows\System32\tar.exe", "-tf", "payload.7z"],
                [r"C:\Windows\System32\tar.exe", "-tvf", "payload.7z"],
            ],
            runner.calls,
        )

    def test_preflight_rejects_traversal_case_collision_and_links(self):
        for names, types, reason in unsafe_listing_cases():
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(Mo2ArchiveError, reason):
                    preflight_archive(
                        Path("payload.7z"),
                        descriptor_for(names),
                        Path("tar.exe"),
                        runner=FakeRunner.for_listing(names, types),
                    )

    def test_existing_comparison_requires_every_package_file(self):
        comparison = compare_package_to_existing(
            package_inventory_fixture(),
            existing_app_fixture(missing="usvfs_x64.dll"),
            descriptor_for(valid_names()),
        )

        self.assertFalse(comparison.compatible)
        self.assertEqual(("usvfs_x64.dll",), comparison.missing)

    def test_existing_comparison_records_only_explicitly_allowed_extras(self):
        allowed = existing_app_fixture(
            extras={
                "categories.dat": b"categories",
                "logs/session.log": b"log",
                "plugins/tool/__pycache__/module.pyc": b"bytecode",
            }
        )
        accepted = compare_package_to_existing(
            package_inventory_fixture(), allowed, descriptor_for(valid_names())
        )
        refused = compare_package_to_existing(
            package_inventory_fixture(),
            existing_app_fixture(extras={"unknown.dll": b"unknown"}),
            descriptor_for(valid_names()),
        )

        self.assertTrue(accepted.compatible)
        self.assertEqual(
            (
                "categories.dat",
                "logs/session.log",
                "plugins/tool/__pycache__/module.pyc",
            ),
            accepted.allowed_extras,
        )
        self.assertFalse(refused.compatible)
        self.assertEqual(("unknown.dll",), refused.unapproved_extras)

    def test_inventory_tree_hashes_exact_sorted_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "b.txt").write_bytes(b"bravo")
            (root / "Folder").mkdir()
            (root / "Folder" / "a.txt").write_bytes(b"alpha")

            result = inventory_tree(root)

        expected_rows = [
            ["b.txt", hashlib.sha256(b"bravo").hexdigest(), 5],
            ["Folder/a.txt", hashlib.sha256(b"alpha").hexdigest(), 5],
        ]
        expected_rows.sort(key=lambda row: (row[0].casefold(), row[0]))
        expected = hashlib.sha256(
            json.dumps(expected_rows, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self.assertEqual(expected, result.sha256)
        self.assertEqual(10, result.total_size)

    def test_extract_checks_space_then_uses_exact_confined_arguments(self):
        files = extraction_package_files()
        descriptor = descriptor_for_package(files)
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            stage = parent / "fresh-stage"
            runner = FakeRunner.for_extraction(files)

            result = extract_and_inventory(
                Path("payload.7z"),
                descriptor,
                Path(r"C:\Windows\System32\tar.exe"),
                stage,
                runner=runner,
                free_space_reader=lambda _: 2048,
            )

            self.assertEqual(len(files), result.file_count)
            self.assertEqual(
                [[r"C:\Windows\System32\tar.exe", "-xf", "payload.7z", "-C", str(stage)]],
                runner.calls,
            )

    def test_low_space_refuses_before_stage_or_extractor(self):
        files = extraction_package_files()
        descriptor = descriptor_for_package(files)
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory) / "fresh-stage"
            runner = FakeRunner.for_extraction(files)

            with self.assertRaisesRegex(Mo2ArchiveError, "free space"):
                extract_and_inventory(
                    Path("payload.7z"),
                    descriptor,
                    Path(r"C:\Windows\System32\tar.exe"),
                    stage,
                    runner=runner,
                    free_space_reader=lambda _: 1023,
                )

            self.assertFalse(stage.exists())
            self.assertEqual([], runner.calls)


if __name__ == "__main__":
    unittest.main()
