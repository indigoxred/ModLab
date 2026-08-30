import io
import json
import tempfile
import unittest
from pathlib import Path

from modlab.cli import main
from modlab.workspace import initialize_workspace


class Mo2CliTests(unittest.TestCase):
    def make_instance(self, directory: str):
        layout = initialize_workspace(Path(directory, "ModLab", "workspace"))
        game_root = Path(directory, "Steam", "Skyrim Special Edition")
        game_root.mkdir(parents=True)
        (layout.skyrim_mo2_app / "ModOrganizer.exe").write_bytes(b"fake mo2")
        for name in ("ModLab - Lab", "ModLab - Play"):
            profile = layout.skyrim_mo2_profiles / name
            profile.mkdir()
            (profile / "modlist.txt").write_text("+SkyUI\n", encoding="utf-8")
            (profile / "plugins.txt").write_text(
                "*Skyrim.esm\n*SkyUI_SE.esp\n", encoding="utf-8"
            )
            (profile / "settings.ini").write_text(
                "[General]\nLocalSaves=false\n", encoding="utf-8"
            )
        (layout.skyrim_mo2_mods / "SkyUI").mkdir()
        (layout.skyrim_mo2_app / "ModOrganizer.ini").write_text(
            (
                "[General]\n"
                f"gamePath={game_root.as_posix()}\n"
                "selected_profile=ModLab - Lab\n"
                "[Settings]\n"
                f"base_directory={layout.skyrim_mo2.as_posix()}\n"
                "download_directory=%BASE_DIR%/downloads\n"
                "mod_directory=%BASE_DIR%/mods\n"
                "profiles_directory=%BASE_DIR%/profiles\n"
                "overwrite_directory=%BASE_DIR%/overwrite\n"
            ),
            encoding="utf-8",
        )
        return layout, game_root

    @staticmethod
    def file_bytes(root: Path):
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

    def test_json_discovery_is_read_only_and_has_explicit_empty_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            layout, game_root = self.make_instance(directory)
            before = self.file_bytes(layout.root)
            stdout, stderr = io.StringIO(), io.StringIO()

            code = main(
                [
                    "manager",
                    "discover",
                    "mo2",
                    "--root",
                    str(layout.skyrim_mo2_app),
                    "--game-root",
                    str(game_root),
                    "--workspace",
                    str(layout.root),
                    "--format",
                    "json",
                ],
                stdout,
                stderr,
            )

            result = json.loads(stdout.getvalue())
            self.assertEqual(0, code)
            self.assertEqual("portable-mo2-skyrim", result["managerEvidence"]["adapterId"])
            self.assertEqual("ModLab - Lab", result["managerEvidence"]["activeProfile"])
            self.assertEqual([], result["actionsPerformed"])
            self.assertEqual([], result["downloadsPerformed"])
            self.assertEqual([], result["installationActionsPerformed"])
            self.assertEqual([], result["programsLaunched"])
            self.assertEqual("", stderr.getvalue())
            self.assertEqual(before, self.file_bytes(layout.root))

    def test_missing_root_returns_three_without_creating_it(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory, "workspace")
            root = workspace / "tools" / "mo2" / "skyrim-se-ae" / "app"
            game_root = Path(directory, "Skyrim Special Edition")
            game_root.mkdir()
            stdout, stderr = io.StringIO(), io.StringIO()

            code = main(
                [
                    "manager",
                    "discover",
                    "mo2",
                    "--root",
                    str(root),
                    "--game-root",
                    str(game_root),
                    "--workspace",
                    str(workspace),
                ],
                stdout,
                stderr,
            )

            self.assertEqual(3, code)
            self.assertIn("Unknown", stdout.getvalue())
            self.assertIn("Nothing was launched", stdout.getvalue())
            self.assertEqual("", stderr.getvalue())
            self.assertFalse(root.exists())

    def test_text_output_explains_lab_play_and_overwrite_state(self):
        with tempfile.TemporaryDirectory() as directory:
            layout, game_root = self.make_instance(directory)
            stdout = io.StringIO()

            code = main(
                [
                    "manager",
                    "discover",
                    "mo2",
                    "--root",
                    str(layout.skyrim_mo2_app),
                    "--game-root",
                    str(game_root),
                    "--workspace",
                    str(layout.root),
                ],
                stdout,
                io.StringIO(),
            )

            text = stdout.getvalue()
            self.assertEqual(0, code)
            self.assertIn("Portable Skyrim MO2 discovery", text)
            self.assertIn("Active profile: ModLab - Lab", text)
            self.assertIn("Lab / Play profiles: ready", text)
            self.assertIn("Overwrite entries: 0", text)
            self.assertIn("Nothing was launched", text)


if __name__ == "__main__":
    unittest.main()
