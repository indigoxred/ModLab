import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modlab.adapters.mo2.processes import (
    ProcessInspectionError,
    RawProcess,
    inspect_mo2_processes,
    windows_documents_root,
)


class Mo2ProcessTests(unittest.TestCase):
    def test_only_exact_target_executables_are_relevant(self):
        root = Path(r"C:\ModLab\workspace\tools\mo2\skyrim-se-ae")
        records = (
            RawProcess(10, "ModOrganizer.exe", str(root / "app" / "ModOrganizer.exe")),
            RawProcess(11, "nxmhandler.exe", str(root / "app" / "nxmhandler.exe")),
            RawProcess(12, "ModOrganizer.exe", r"D:\Other\ModOrganizer.exe"),
            RawProcess(13, "SkyrimSE.exe", r"D:\Games\SkyrimSE.exe"),
        )

        result = inspect_mo2_processes(root, enumerator=lambda: records)

        self.assertTrue(result.complete)
        self.assertEqual((10, 11), tuple(item.pid for item in result.relevant))
        self.assertIsNone(result.error)

    def test_target_matching_is_case_insensitive_but_not_name_only(self):
        root = Path(r"C:\ModLab\workspace\tools\mo2\skyrim-se-ae")
        records = (
            RawProcess(
                20,
                "MODORGANIZER.EXE",
                r"c:\modlab\workspace\tools\mo2\skyrim-se-ae\app\MODORGANIZER.EXE",
            ),
            RawProcess(21, "nxmhandler.exe", r"C:\Elsewhere\nxmhandler.exe"),
        )

        result = inspect_mo2_processes(root, enumerator=lambda: records)

        self.assertEqual((20,), tuple(item.pid for item in result.relevant))

    def test_unreadable_candidate_process_is_unknown(self):
        def fail():
            raise ProcessInspectionError("candidate path unavailable")

        result = inspect_mo2_processes(Path(r"C:\ModLab\workspace"), enumerator=fail)

        self.assertFalse(result.complete)
        self.assertEqual((), result.relevant)
        self.assertIn("unavailable", result.error)

    def test_nonabsolute_candidate_path_is_unknown(self):
        result = inspect_mo2_processes(
            Path(r"C:\ModLab\workspace"),
            enumerator=lambda: (RawProcess(30, "ModOrganizer.exe", "relative.exe"),),
        )

        self.assertFalse(result.complete)
        self.assertIn("absolute", result.error)

    def test_documents_root_uses_known_folder_result_and_requires_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            expected = Path(directory).resolve()
            with patch(
                "modlab.adapters.mo2.processes._resolve_documents_path",
                return_value=str(expected),
            ):
                self.assertEqual(expected, windows_documents_root())

            missing = expected / "missing"
            with patch(
                "modlab.adapters.mo2.processes._resolve_documents_path",
                return_value=str(missing),
            ):
                with self.assertRaisesRegex(ProcessInspectionError, "directory"):
                    windows_documents_root()

    @unittest.skipUnless(os.name == "nt", "Windows Known Folder API is unavailable")
    def test_real_documents_known_folder_is_absolute_and_existing(self):
        result = windows_documents_root()

        self.assertTrue(result.is_absolute())
        self.assertTrue(result.is_dir())


if __name__ == "__main__":
    unittest.main()
