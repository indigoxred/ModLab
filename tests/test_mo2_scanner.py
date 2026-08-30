import hashlib
import tempfile
import unittest
from pathlib import Path

from modlab.adapters.mo2.scanner import inspect_skyrim_mo2
from modlab.adapters.mo2.projection import Mo2Readiness, project_mo2_state
from modlab.adapters.mo2.serialization import report_to_dict
from modlab.recipes.model import CheckState
from modlab.workspace import initialize_workspace


class Mo2ScannerTests(unittest.TestCase):
    def make_instance(self, directory: str):
        workspace = Path(directory, "ModLab", "workspace")
        layout = initialize_workspace(workspace)
        game_root = Path(
            directory,
            "Steam",
            "steamapps",
            "common",
            "Skyrim Special Edition",
        )
        game_root.mkdir(parents=True)
        (game_root / "Skyrim.ccc").write_text(
            "ccBGSSSE001-Fish.esm\n_ResourcePack.esl\n", encoding="utf-8"
        )
        primary = (
            "Skyrim.esm\nUpdate.esm\nDawnguard.esm\nHearthFires.esm\n"
            "Dragonborn.esm\nccBGSSSE001-Fish.esm\n_ResourcePack.esl\n"
        )
        app = layout.skyrim_mo2_app
        executable = app / "ModOrganizer.exe"
        executable.write_bytes(b"fake mo2 executable")

        for profile_name in ("ModLab - Lab", "ModLab - Play"):
            profile = layout.skyrim_mo2_profiles / profile_name
            profile.mkdir()
            (profile / "modlist.txt").write_text(
                "# generated\n+SkyUI\n", encoding="utf-8"
            )
            (profile / "plugins.txt").write_text(
                "# generated\n*SkyUI_SE.esp\n", encoding="utf-8"
            )
            (profile / "loadorder.txt").write_text(
                primary + "SkyUI_SE.esp\n", encoding="utf-8"
            )
            (profile / "settings.ini").write_text(
                "[General]\nLocalSaves=false\nLocalSettings=true\n", encoding="utf-8"
            )

        (layout.skyrim_mo2_mods / "SkyUI").mkdir()
        (layout.skyrim_mo2_mods / "SkyUI" / "meta.ini").write_text(
            "[General]\nversion=5.2SE\n", encoding="utf-8"
        )
        (layout.skyrim_mo2_mods / "not-an-installed-mod.txt").write_text(
            "ignored", encoding="utf-8"
        )
        (app / "ModOrganizer.ini").write_text(
            (
                "[General]\n"
                "gameName=Skyrim Special Edition\n"
                f"gamePath={game_root.as_posix()}\n"
                "selected_profile=@ByteArray(ModLab - Lab)\n"
                "[Settings]\n"
                f"base_directory={layout.skyrim_mo2.as_posix()}\n"
                "download_directory=%BASE_DIR%/downloads\n"
                "mod_directory=%BASE_DIR%/mods\n"
                "profiles_directory=%BASE_DIR%/profiles\n"
                "overwrite_directory=%BASE_DIR%/overwrite\n"
            ),
            encoding="utf-8",
        )
        return layout, game_root, executable

    @staticmethod
    def file_bytes(root: Path):
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

    def test_inspects_contained_lab_and_play_profiles_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            layout, game_root, executable = self.make_instance(directory)
            before = self.file_bytes(layout.root)
            version_calls = []

            def version_reader(path):
                version_calls.append(path)
                return "2.5.2.0"

            report = inspect_skyrim_mo2(
                layout.skyrim_mo2_app,
                game_root,
                workspace_root=layout.root,
                version_reader=version_reader,
            )

            self.assertEqual("2.5.2.0", report.executable.file_version)
            self.assertEqual(
                hashlib.sha256(b"fake mo2 executable").hexdigest(),
                report.executable.sha256,
            )
            self.assertEqual([executable], version_calls)
            self.assertTrue(report.portable_config_present)
            self.assertEqual("ModLab - Lab", report.active_profile)
            self.assertEqual(
                ("ModLab - Lab", "ModLab - Play"),
                tuple(profile.name for profile in report.profiles),
            )
            self.assertEqual(("SkyUI",), report.top_level_mods)
            self.assertEqual(1, len(report.mod_metadata_files))
            self.assertEqual(
                "mods/SkyUI/meta.ini",
                report.mod_metadata_files[0].relative_path,
            )
            self.assertEqual((), report.overwrite_entries)
            self.assertTrue(all(item.contained for item in report.paths))
            findings = {item.code: item for item in report.findings}
            self.assertEqual(CheckState.PASSED, findings["paths-contained"].state)
            self.assertEqual(CheckState.PASSED, findings["lab-play-ready"].state)
            self.assertEqual(CheckState.PASSED, findings["overwrite-empty"].state)
            self.assertEqual(before, self.file_bytes(layout.root))

    def test_attaches_stable_internal_comparison_evidence_without_changing_json(self):
        with tempfile.TemporaryDirectory() as directory:
            layout, game_root, _ = self.make_instance(directory)
            report = inspect_skyrim_mo2(
                layout.skyrim_mo2_app,
                game_root,
                workspace_root=layout.root,
                version_reader=lambda _: "2.5.2.0",
            )

            evidence = report.comparison_evidence
            self.assertIsNotNone(evidence)
            self.assertTrue(evidence.read_set_stable)
            self.assertEqual(64, len(evidence.read_set_sha256))
            self.assertEqual("Skyrim.esm", evidence.primary_plugins[0])
            self.assertEqual("_ResourcePack.esl", evidence.primary_plugins[-1])
            self.assertEqual(
                (True, True),
                tuple(
                    item.profile_local_settings for item in evidence.profile_settings
                ),
            )
            serialized = report_to_dict(report)
            self.assertNotIn("comparisonEvidence", serialized)
            self.assertEqual(
                {
                    "schemaVersion",
                    "adapterId",
                    "requestedRoot",
                    "resolvedRoot",
                    "gameRoot",
                    "executable",
                    "portableConfigPresent",
                    "configuredGamePath",
                    "paths",
                    "activeProfile",
                    "profiles",
                    "topLevelMods",
                    "modMetadataFiles",
                    "overwriteEntries",
                    "findings",
                    "actions",
                    "downloads",
                    "installations",
                    "programLaunches",
                },
                set(serialized),
            )
            self.assertEqual(
                Mo2Readiness.READY,
                project_mo2_state(report).readiness,
            )

    def test_blocks_if_authoritative_state_changes_during_inspection(self):
        cases = ("profile", "metadata", "overwrite")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                layout, game_root, _ = self.make_instance(directory)
                if case == "profile":
                    target = (
                        layout.skyrim_mo2_profiles
                        / "ModLab - Lab"
                        / "plugins.txt"
                    )
                    mutate = lambda: target.write_bytes(b"*Changed.esp\n")
                elif case == "metadata":
                    target = layout.skyrim_mo2_mods / "SkyUI" / "meta.ini"
                    target.unlink()
                    mutate = lambda: target.write_bytes(b"[General]\nversion=changed\n")
                else:
                    target = layout.skyrim_mo2_overwrite / "new-output"
                    mutate = target.mkdir

                report = inspect_skyrim_mo2(
                    layout.skyrim_mo2_app,
                    game_root,
                    workspace_root=layout.root,
                    version_reader=lambda _: "2.5.2.0",
                    _before_stability_check=mutate,
                )

                findings = {item.code: item for item in report.findings}
                self.assertEqual(
                    CheckState.BLOCKED,
                    findings["mo2-state-changed-during-inspection"].state,
                )
                self.assertFalse(report.comparison_evidence.read_set_stable)

    def test_derives_standard_paths_when_only_base_directory_is_configured(self):
        with tempfile.TemporaryDirectory() as directory:
            layout, game_root, _ = self.make_instance(directory)
            ini = layout.skyrim_mo2_app / "ModOrganizer.ini"
            explicit_paths = {
                "download_directory=%BASE_DIR%/downloads\n",
                "mod_directory=%BASE_DIR%/mods\n",
                "profiles_directory=%BASE_DIR%/profiles\n",
                "overwrite_directory=%BASE_DIR%/overwrite\n",
            }
            ini.write_text(
                "".join(
                    line
                    for line in ini.read_text(encoding="utf-8").splitlines(keepends=True)
                    if line not in explicit_paths
                ),
                encoding="utf-8",
            )

            report = inspect_skyrim_mo2(
                layout.skyrim_mo2_app,
                game_root,
                workspace_root=layout.root,
                version_reader=lambda _: "2.5.2.0",
            )

            paths = {item.kind: item for item in report.paths}
            self.assertEqual(
                {
                    "base": layout.skyrim_mo2,
                    "downloads": layout.skyrim_mo2_downloads,
                    "mods": layout.skyrim_mo2_mods,
                    "profiles": layout.skyrim_mo2_profiles,
                    "overwrite": layout.skyrim_mo2_overwrite,
                },
                {
                    kind: Path(item.resolved_path)
                    for kind, item in paths.items()
                },
            )
            self.assertEqual("%BASE_DIR%/downloads", paths["downloads"].configured_path)
            self.assertEqual("ModLab - Lab", report.active_profile)
            findings = {item.code: item for item in report.findings}
            self.assertEqual(CheckState.PASSED, findings["paths-contained"].state)
            self.assertEqual(CheckState.PASSED, findings["lab-play-ready"].state)

    def test_escaped_writable_path_is_blocked_and_never_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            layout, game_root, _ = self.make_instance(directory)
            outside = Path(directory, "outside-downloads")
            outside.mkdir()
            sentinel = outside / "do-not-read.7z"
            sentinel.write_bytes(b"private")
            ini = layout.skyrim_mo2_app / "ModOrganizer.ini"
            text = ini.read_text(encoding="utf-8").replace(
                "download_directory=%BASE_DIR%/downloads",
                f"download_directory={outside.as_posix()}",
            )
            ini.write_text(text, encoding="utf-8")

            report = inspect_skyrim_mo2(
                layout.skyrim_mo2_app,
                game_root,
                workspace_root=layout.root,
                version_reader=lambda _: "2.5.2.0",
            )

            downloads = next(item for item in report.paths if item.kind == "downloads")
            self.assertFalse(downloads.contained)
            findings = {item.code: item for item in report.findings}
            self.assertEqual(CheckState.BLOCKED, findings["paths-escaped"].state)
            self.assertEqual(b"private", sentinel.read_bytes())

    def test_game_mismatch_missing_play_and_nonempty_overwrite_are_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            layout, game_root, _ = self.make_instance(directory)
            other_game = Path(directory, "Another Skyrim")
            other_game.mkdir()
            ini = layout.skyrim_mo2_app / "ModOrganizer.ini"
            ini.write_text(
                ini.read_text(encoding="utf-8").replace(
                    game_root.as_posix(), other_game.as_posix()
                ),
                encoding="utf-8",
            )
            play = layout.skyrim_mo2_profiles / "ModLab - Play"
            for child in play.iterdir():
                child.unlink()
            play.rmdir()
            (layout.skyrim_mo2_overwrite / "Nemesis output").mkdir()

            report = inspect_skyrim_mo2(
                layout.skyrim_mo2_app,
                game_root,
                workspace_root=layout.root,
                version_reader=lambda _: "2.5.2.0",
            )

            findings = {item.code: item for item in report.findings}
            self.assertEqual(CheckState.BLOCKED, findings["game-path-mismatch"].state)
            self.assertEqual(CheckState.BLOCKED, findings["lab-play-incomplete"].state)
            self.assertEqual(CheckState.WARNING, findings["overwrite-not-empty"].state)
            self.assertEqual(("Nemesis output",), report.overwrite_entries)

    def test_unconfigured_root_remains_unknown_and_is_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory, "ModLab", "workspace")
            game_root = Path(directory, "Skyrim Special Edition")
            game_root.mkdir()
            missing = workspace / "tools" / "mo2" / "skyrim-se-ae" / "app"

            report = inspect_skyrim_mo2(
                missing,
                game_root,
                workspace_root=workspace,
                version_reader=lambda _: self.fail("version reader must not run"),
            )

            self.assertFalse(missing.exists())
            self.assertIsNone(report.executable)
            self.assertFalse(report.portable_config_present)
            self.assertEqual((), report.paths)
            findings = {item.code: item for item in report.findings}
            self.assertEqual(CheckState.UNKNOWN, findings["mo2-root-not-configured"].state)

    def test_malformed_qsettings_path_is_blocked_instead_of_crashing(self):
        with tempfile.TemporaryDirectory() as directory:
            layout, game_root, _ = self.make_instance(directory)
            ini = layout.skyrim_mo2_app / "ModOrganizer.ini"
            ini.write_text(
                ini.read_text(encoding="utf-8").replace(
                    "download_directory=%BASE_DIR%/downloads",
                    "download_directory=@Variant(not-a-path)",
                ),
                encoding="utf-8",
            )

            report = inspect_skyrim_mo2(
                layout.skyrim_mo2_app,
                game_root,
                workspace_root=layout.root,
                version_reader=lambda _: "2.5.2.0",
            )

            findings = {item.code: item for item in report.findings}
            self.assertEqual(CheckState.BLOCKED, findings["paths-incomplete"].state)

    def test_save_like_overwrite_entry_is_not_claimed_as_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            layout, game_root, _ = self.make_instance(directory)
            (layout.skyrim_mo2_overwrite / "accidental.ess").write_bytes(b"save")

            report = inspect_skyrim_mo2(
                layout.skyrim_mo2_app,
                game_root,
                workspace_root=layout.root,
                version_reader=lambda _: "2.5.2.0",
            )

            findings = {item.code: item for item in report.findings}
            self.assertNotIn("overwrite-empty", findings)
            self.assertEqual(
                CheckState.UNKNOWN, findings["overwrite-status-unknown"].state
            )

    def test_nonstandard_app_folder_inside_workspace_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory, "ModLab", "workspace")
            wrong_root = workspace / "misc" / "app"
            wrong_root.mkdir(parents=True)
            (wrong_root / "ModOrganizer.exe").write_bytes(b"do not inspect")
            game_root = Path(directory, "Skyrim Special Edition")
            game_root.mkdir()

            report = inspect_skyrim_mo2(
                wrong_root,
                game_root,
                workspace_root=workspace,
                version_reader=lambda _: self.fail("wrong layout was followed"),
            )

            findings = {item.code: item for item in report.findings}
            self.assertEqual(
                CheckState.BLOCKED, findings["mo2-root-layout-mismatch"].state
            )

    def test_missing_game_root_is_blocked_even_when_ini_text_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            layout, game_root, _ = self.make_instance(directory)
            (game_root / "Skyrim.ccc").unlink()
            game_root.rmdir()

            report = inspect_skyrim_mo2(
                layout.skyrim_mo2_app,
                game_root,
                workspace_root=layout.root,
                version_reader=lambda _: "2.5.2.0",
            )

            findings = {item.code: item for item in report.findings}
            self.assertEqual(CheckState.BLOCKED, findings["game-root-invalid"].state)


if __name__ == "__main__":
    unittest.main()
