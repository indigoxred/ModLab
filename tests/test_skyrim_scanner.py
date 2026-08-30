import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modlab.adapters.skyrim.scanner import discover_skyrim_steam
from modlab.recipes.model import CheckState
from tests.test_steam_manifest import VALID_MANIFEST


class SkyrimSteamScannerTests(unittest.TestCase):
    def make_library(self, directory: str):
        steam_root = Path(directory, "Steam")
        steamapps = steam_root / "steamapps"
        game_root = steamapps / "common" / "Skyrim Special Edition"
        data = game_root / "Data"
        data.mkdir(parents=True)
        manifest = steamapps / "appmanifest_489830.acf"
        manifest.write_text(VALID_MANIFEST, encoding="utf-8")
        executable = game_root / "SkyrimSE.exe"
        executable.write_bytes(b"fake skyrim executable")
        (data / "Skyrim.esm").write_bytes(b"base plugin")
        (data / "ccBGSSSE001-Fish.esm").write_bytes(b"cc plugin")
        (data / "ccBGSSSE001-Fish.bsa").write_bytes(b"cc archive")
        (data / "notes.txt").write_bytes(b"not adapter state")
        (data / "accidental.ess").write_bytes(b"must be excluded")
        saves = data / "Saves"
        saves.mkdir()
        (saves / "Save001.ess").write_bytes(b"precious save")
        (saves / "Save001.skse").write_bytes(b"precious cosave")
        return steam_root, game_root, executable

    @staticmethod
    def file_bytes(root: Path):
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

    def test_discovers_exact_runtime_and_top_level_data_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            steam_root, game_root, executable = self.make_library(directory)
            before = self.file_bytes(steam_root)
            version_calls = []

            def version_reader(path):
                version_calls.append(path)
                return "1.7.104.0"

            report = discover_skyrim_steam(steam_root, version_reader=version_reader)

            self.assertEqual("489830", report.app_id)
            self.assertEqual(4, report.install_state_flags)
            self.assertEqual(str(game_root.resolve()), report.game_root)
            self.assertEqual("1.7.104.0", report.executable.file_version)
            self.assertEqual(
                hashlib.sha256(b"fake skyrim executable").hexdigest(),
                report.executable.sha256,
            )
            self.assertEqual([executable], version_calls)
            self.assertEqual(
                (
                    "Data/ccBGSSSE001-Fish.bsa",
                    "Data/ccBGSSSE001-Fish.esm",
                    "Data/Skyrim.esm",
                ),
                tuple(item.relative_path for item in report.data_files),
            )
            self.assertEqual(1, report.creation_club_plugin_count)
            self.assertEqual(1, report.creation_club_archive_count)
            self.assertIsNone(report.mo2_path)
            findings = {finding.code: finding for finding in report.findings}
            self.assertEqual(CheckState.PASSED, findings["runtime-observed"].state)
            self.assertEqual(
                CheckState.WARNING,
                findings["anniversary-bundle-not-proven"].state,
            )
            self.assertEqual(CheckState.UNKNOWN, findings["mo2-not-configured"].state)
            self.assertEqual(before, self.file_bytes(steam_root))

    def test_missing_steam_root_remains_absent_and_returns_blocked_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            steam_root = Path(directory, "missing-Steam")

            report = discover_skyrim_steam(
                steam_root,
                version_reader=lambda _: self.fail("version reader must not run"),
            )

            self.assertFalse(steam_root.exists())
            self.assertIsNone(report.game_root)
            self.assertIsNone(report.executable)
            self.assertEqual((), report.data_files)
            findings = {finding.code: finding for finding in report.findings}
            self.assertEqual(CheckState.BLOCKED, findings["steam-root-missing"].state)
            self.assertEqual(CheckState.UNKNOWN, findings["runtime-not-observed"].state)

    def test_wrong_app_id_and_unsafe_install_directory_are_never_followed(self):
        cases = (
            VALID_MANIFEST.replace('"489830"', '"123"', 1),
            VALID_MANIFEST.replace(
                '"Skyrim Special Edition"',
                '"..\\Outside"',
                1,
            ),
        )
        for index, manifest_text in enumerate(cases):
            with self.subTest(case=index), tempfile.TemporaryDirectory() as directory:
                steam_root = Path(directory, "Steam")
                steamapps = steam_root / "steamapps"
                steamapps.mkdir(parents=True)
                (steamapps / "appmanifest_489830.acf").write_text(
                    manifest_text,
                    encoding="utf-8",
                )
                outside = Path(directory, "Outside")
                outside.mkdir()
                sentinel = outside / "SkyrimSE.exe"
                sentinel.write_bytes(b"outside")

                report = discover_skyrim_steam(
                    steam_root,
                    version_reader=lambda _: self.fail("unsafe path was followed"),
                )

                self.assertIsNone(report.game_root)
                self.assertIsNone(report.executable)
                self.assertEqual(b"outside", sentinel.read_bytes())
                self.assertTrue(
                    any(finding.state is CheckState.BLOCKED for finding in report.findings)
                )

    def test_version_read_failure_remains_unknown_but_keeps_file_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            steam_root, _, _ = self.make_library(directory)

            def fail_version(_):
                raise OSError("version resource unavailable")

            report = discover_skyrim_steam(steam_root, version_reader=fail_version)

            self.assertIsNotNone(report.executable)
            self.assertIsNone(report.executable.file_version)
            findings = {finding.code: finding for finding in report.findings}
            self.assertEqual(CheckState.UNKNOWN, findings["runtime-not-observed"].state)

    def test_unreadable_executable_returns_blocked_evidence_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            steam_root, _, executable = self.make_library(directory)
            before = self.file_bytes(steam_root)

            with patch(
                "modlab.adapters.skyrim.scanner._hash_file",
                side_effect=PermissionError("hash denied"),
            ):
                report = discover_skyrim_steam(
                    steam_root,
                    version_reader=lambda _: self.fail("version reader must not run"),
                )

            self.assertIsNone(report.executable)
            findings = {finding.code: finding for finding in report.findings}
            self.assertEqual(CheckState.BLOCKED, findings["executable-unreadable"].state)
            self.assertEqual(CheckState.UNKNOWN, findings["runtime-not-observed"].state)
            self.assertEqual(before, self.file_bytes(steam_root))
            self.assertTrue(executable.is_file())


if __name__ == "__main__":
    unittest.main()
