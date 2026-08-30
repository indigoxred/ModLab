import io
import json
import tempfile
import unittest
from pathlib import Path

from modlab.cli import main
from tests.test_steam_manifest import VALID_MANIFEST


class SkyrimDiscoveryCliTests(unittest.TestCase):
    def make_library(self, directory: str):
        steam_root = Path(directory, "Steam")
        steamapps = steam_root / "steamapps"
        game_root = steamapps / "common" / "Skyrim Special Edition"
        data = game_root / "Data"
        data.mkdir(parents=True)
        (steamapps / "appmanifest_489830.acf").write_text(
            VALID_MANIFEST,
            encoding="utf-8",
        )
        (game_root / "SkyrimSE.exe").write_bytes(b"fake executable")
        (data / "Skyrim.esm").write_bytes(b"plugin")
        return steam_root, game_root

    def test_json_discovery_is_read_only_and_machine_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            steam_root, game_root = self.make_library(directory)
            manifest = steam_root / "steamapps" / "appmanifest_489830.acf"
            executable = game_root / "SkyrimSE.exe"
            before = (manifest.read_bytes(), executable.read_bytes())
            stdout, stderr = io.StringIO(), io.StringIO()

            code = main(
                [
                    "game",
                    "discover",
                    "skyrim",
                    "--steam-root",
                    str(steam_root),
                    "--format",
                    "json",
                ],
                stdout,
                stderr,
            )

            result = json.loads(stdout.getvalue())
            self.assertEqual(0, code)
            self.assertEqual("489830", result["discovery"]["appId"])
            self.assertEqual("skyrim-steam", result["discovery"]["adapterId"])
            self.assertEqual([], result["actionsPerformed"])
            self.assertEqual([], result["installationActionsPerformed"])
            self.assertEqual([], result["programsLaunched"])
            self.assertEqual("", stderr.getvalue())
            self.assertEqual(before, (manifest.read_bytes(), executable.read_bytes()))

    def test_missing_root_returns_three_without_creating_it(self):
        with tempfile.TemporaryDirectory() as directory:
            steam_root = Path(directory, "missing")
            stdout, stderr = io.StringIO(), io.StringIO()

            code = main(
                [
                    "game",
                    "discover",
                    "skyrim",
                    "--steam-root",
                    str(steam_root),
                ],
                stdout,
                stderr,
            )

            self.assertEqual(3, code)
            self.assertIn("Blocked", stdout.getvalue())
            self.assertIn("Nothing was launched", stdout.getvalue())
            self.assertEqual("", stderr.getvalue())
            self.assertFalse(steam_root.exists())

    def test_text_output_keeps_runtime_ae_and_mo2_as_separate_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            steam_root, _ = self.make_library(directory)
            stdout = io.StringIO()

            code = main(
                [
                    "game",
                    "discover",
                    "skyrim",
                    "--steam-root",
                    str(steam_root),
                ],
                stdout,
                io.StringIO(),
            )

            text = stdout.getvalue()
            self.assertEqual(0, code)
            self.assertIn("Executable runtime:", text)
            self.assertIn("Compatibility runtime:", text)
            self.assertIn("Anniversary bundle: not proven", text)
            self.assertIn("MO2: not configured", text)
            self.assertIn("Nothing was launched", text)


if __name__ == "__main__":
    unittest.main()
