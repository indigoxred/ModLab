import copy
import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from modlab.cli import main
from modlab.adapters.mo2.readset import Mo2ReadSet
from modlab.workflows.skyrim.store import (
    SkyrimEnvironmentConflictError,
    SkyrimEnvironmentStore,
)
from tests.support.skyrim_workflow import create_skyrim_workflow_fixture


def tree_state(root: Path):
    return tuple(
        (
            path.relative_to(root).as_posix(),
            "directory" if path.is_dir() else "file",
            None if path.is_dir() else path.read_bytes(),
        )
        for path in sorted(root.rglob("*"))
    )


class SkyrimWorkflowCliTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.fixture = create_skyrim_workflow_fixture(
            Path(self.directory.name)
        )

    @staticmethod
    def version_reader(path: Path):
        if Path(path).name.casefold() == "skyrimse.exe":
            return "1.7.104.0"
        if Path(path).name.casefold() == "modorganizer.exe":
            return "2.5.2.0"
        return None

    def run_cli(self, arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch(
            "modlab.workflows.skyrim.service.read_windows_file_version",
            side_effect=self.version_reader,
        ):
            code = main(arguments, stdout, stderr)
        return code, stdout.getvalue(), stderr.getvalue()

    def configure_arguments(self, *extra):
        return [
            "skyrim",
            "configure",
            "--steam-root",
            str(self.fixture.steam_root),
            "--recipe",
            str(self.fixture.recipe_path),
            "--environment",
            str(self.fixture.environment_path),
            "--workspace",
            str(self.fixture.workspace),
            *extra,
        ]

    def assert_refused_without_changes(self, arguments, reason):
        before = tree_state(self.fixture.root)
        code, output, error = self.run_cli(arguments)
        self.assertEqual(3, code)
        self.assertIn(reason.casefold(), (output + error).casefold())
        self.assertNotIn("Traceback", error)
        self.assertEqual(before, tree_state(self.fixture.root))
        if output.lstrip().startswith("{"):
            value = json.loads(output)
            self.assertEqual([], value["actionsPerformed"])
            self.assertEqual([], value["downloadsPerformed"])
            self.assertEqual([], value["installationActionsPerformed"])
            self.assertEqual([], value["programsLaunched"])

    def test_full_cli_flow_requires_paths_once_then_matches(self):
        configure_code, configure_out, configure_err = self.run_cli(
            self.configure_arguments()
        )
        capture_code, capture_out, capture_err = self.run_cli(
            [
                "skyrim", "baseline", "create",
                "--workspace", str(self.fixture.workspace),
            ]
        )
        status_code, status_out, status_err = self.run_cli(
            ["skyrim", "status", "--workspace", str(self.fixture.workspace)]
        )

        self.assertEqual(0, configure_code)
        self.assertIn("Configured", configure_out)
        self.assertEqual(0, capture_code)
        self.assertIn("Observed baseline captured", capture_out)
        self.assertEqual(0, status_code)
        self.assertIn("Matched", status_out)
        self.assertEqual("", configure_err + capture_err + status_err)

    def test_json_contract_and_status_exit_codes(self):
        no_baseline_code, no_baseline_out, no_baseline_err = self.run_cli(
            [
                "skyrim", "status",
                "--workspace", str(self.fixture.workspace),
                "--format", "json",
            ]
        )
        self.assertEqual(3, no_baseline_code)
        self.assertEqual("Blocked", json.loads(no_baseline_out)["status"]["outcome"])
        self.assertEqual("", no_baseline_err)

        code, output, error = self.run_cli(
            self.configure_arguments("--format", "json")
        )
        value = json.loads(output)
        self.assertEqual(0, code)
        self.assertEqual("Configured", value["configure"]["outcome"])
        self.assertEqual([], value["downloadsPerformed"])
        self.assertEqual([], value["installationActionsPerformed"])
        self.assertEqual([], value["programsLaunched"])
        self.assertEqual("", error)

        code, output, _ = self.run_cli(
            [
                "skyrim", "status",
                "--workspace", str(self.fixture.workspace),
                "--format", "json",
            ]
        )
        self.assertEqual(3, code)
        self.assertEqual("NoBaseline", json.loads(output)["status"]["outcome"])

    def test_repeated_selection_arguments_reach_recipe_review(self):
        value = json.loads(self.fixture.recipe_path.read_text(encoding="utf-8"))
        base = value["components"][0]
        for component_id, default_selected in (
            ("omit-one", True),
            ("omit-two", True),
            ("select-one", False),
            ("select-two", False),
        ):
            component = copy.deepcopy(base)
            component.update(
                componentId=component_id,
                displayName=component_id,
                importance="Optional",
                defaultSelected=default_selected,
                constraints=[],
                rationale="CLI selection fixture.",
            )
            value["components"].append(component)
        recipe = self.fixture.root / "intent" / "selection-recipe.json"
        recipe.write_text(json.dumps(value), encoding="utf-8")

        arguments = self.configure_arguments(
            "--select", "select-one",
            "--select", "select-two",
            "--omit", "omit-one",
            "--omit", "omit-two",
            "--format", "json",
        )
        arguments[arguments.index(str(self.fixture.recipe_path))] = str(recipe)
        code, output, error = self.run_cli(arguments)
        summary = json.loads(output)["configure"]["recipe"]

        self.assertEqual(0, code)
        self.assertIn("select-one", summary["selected"])
        self.assertIn("select-two", summary["selected"])
        self.assertEqual(["omit-one", "omit-two"], summary["omitted"])
        self.assertEqual("", error)

    def test_configuration_conflict_lists_changes_then_replace_succeeds(self):
        self.assertEqual(0, self.run_cli(self.configure_arguments())[0])
        alternate = self.fixture.root / "intent" / "alternate-environment.json"
        value = json.loads(self.fixture.environment_path.read_text(encoding="utf-8"))
        alternate.write_text(json.dumps(value), encoding="utf-8")
        arguments = self.configure_arguments()
        arguments[arguments.index(str(self.fixture.environment_path))] = str(alternate)

        code, output, error = self.run_cli(arguments)
        self.assertEqual(3, code)
        self.assertEqual("", output)
        self.assertIn("targetEnvironment.sourceSha256", error)
        self.assertIn("targetEnvironment.storedPath", error)
        self.assertEqual(1, len(error.splitlines()))

        code, output, error = self.run_cli([*arguments, "--replace"])
        self.assertEqual(0, code)
        self.assertIn("Configured", output)
        self.assertEqual("", error)

    def test_pointer_failure_returns_three_and_baseline_use_recovers_orphan(self):
        self.assertEqual(0, self.run_cli(self.configure_arguments())[0])
        with patch.object(
            SkyrimEnvironmentStore,
            "compare_and_swap",
            side_effect=SkyrimEnvironmentConflictError("changed"),
        ):
            code, output, error = self.run_cli(
                [
                    "skyrim", "baseline", "create",
                    "--workspace", str(self.fixture.workspace),
                    "--format", "json",
                ]
            )
        capture = json.loads(output)["baselineCapture"]
        self.assertEqual(3, code)
        self.assertFalse(capture["selected"])
        self.assertIn("not selected", capture["warning"])
        self.assertEqual("", error)

        code, output, error = self.run_cli(
            [
                "skyrim", "baseline", "use", capture["checkpointId"],
                "--workspace", str(self.fixture.workspace),
                "--format", "json",
            ]
        )
        self.assertEqual(0, code)
        self.assertEqual(
            capture["checkpointId"],
            json.loads(output)["baselineUse"]["selectedBaselineCheckpointId"],
        )
        self.assertEqual("", error)

    def test_status_is_read_only_for_matched_and_drifted_state(self):
        self.run_cli(self.configure_arguments())
        self.run_cli([
            "skyrim", "baseline", "create",
            "--workspace", str(self.fixture.workspace),
        ])
        before = tree_state(self.fixture.root)
        code, output, error = self.run_cli([
            "skyrim", "status", "--workspace", str(self.fixture.workspace)
        ])
        self.assertEqual(0, code)
        self.assertIn("Matched", output)
        self.assertEqual(before, tree_state(self.fixture.root))
        self.assertEqual("", error)

        archive = (
            self.fixture.workspace / "tools" / "mo2" / "skyrim-se-ae"
            / "profiles" / "ModLab - Lab" / "archives.txt"
        )
        archive.write_text("changed\n", encoding="utf-8")
        before = tree_state(self.fixture.root)
        code, output, error = self.run_cli([
            "skyrim", "status", "--workspace", str(self.fixture.workspace)
        ])
        self.assertEqual(3, code)
        self.assertIn("Drifted", output)
        self.assertEqual(before, tree_state(self.fixture.root))
        self.assertEqual("", error)

    def test_expected_refusal_is_one_line_and_unexpected_error_is_visible(self):
        bad_recipe = self.fixture.root / "intent" / "bad.json"
        bad_recipe.write_text("{}", encoding="utf-8")
        arguments = self.configure_arguments()
        arguments[arguments.index(str(self.fixture.recipe_path))] = str(bad_recipe)
        code, output, error = self.run_cli(arguments)
        self.assertEqual(3, code)
        self.assertEqual("", output)
        self.assertTrue(error.startswith("Skyrim workflow refused:"))
        self.assertEqual(1, len(error.splitlines()))
        self.assertNotIn("Traceback", error)

        with patch(
            "modlab.cli.configure_skyrim_environment",
            side_effect=RuntimeError("developer failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "developer failure"):
                self.run_cli(self.configure_arguments())

    def test_configure_failure_matrix_is_named_and_side_effect_free(self):
        cases = []

        manifest_fixture = create_skyrim_workflow_fixture(
            self.fixture.root / "missing-manifest"
        )
        (manifest_fixture.steam_root / "steamapps" / "appmanifest_489830.acf").unlink()
        cases.append((manifest_fixture, "manifest", None))

        game_fixture = create_skyrim_workflow_fixture(
            self.fixture.root / "missing-game-identity"
        )
        (game_fixture.game_root / "SkyrimSE.exe").unlink()
        cases.append((game_fixture, "executable-not-observed", None))

        manager_fixture = create_skyrim_workflow_fixture(
            self.fixture.root / "missing-manager-identity"
        )
        (manager_fixture.mo2_root / "ModOrganizer.exe").unlink()
        cases.append((manager_fixture, "mo2-executable-not-observed", None))

        escaped_fixture = create_skyrim_workflow_fixture(
            self.fixture.root / "escaped-path"
        )
        ini = escaped_fixture.mo2_root / "ModOrganizer.ini"
        ini.write_text(
            ini.read_text(encoding="utf-8").replace(
                "mod_directory=%BASE_DIR%/mods",
                f"mod_directory={(escaped_fixture.root / 'outside-mods').as_posix()}",
            ),
            encoding="utf-8",
        )
        cases.append((escaped_fixture, "paths-escaped", None))

        profile_fixture = create_skyrim_workflow_fixture(
            self.fixture.root / "missing-lab-profile"
        )
        lab = (
            profile_fixture.workspace / "tools" / "mo2" / "skyrim-se-ae"
            / "profiles" / "ModLab - Lab"
        )
        lab.rename(lab.with_name("Lab profile removed"))
        cases.append((profile_fixture, "lab-play", None))

        runtime_fixture = create_skyrim_workflow_fixture(
            self.fixture.root / "runtime-mismatch"
        )
        target = json.loads(runtime_fixture.environment_path.read_text(encoding="utf-8"))
        target["dimensions"]["executableRuntime"] = "9.9.9"
        runtime_target = runtime_fixture.root / "intent" / "runtime-mismatch.json"
        runtime_target.write_text(json.dumps(target), encoding="utf-8")
        cases.append((runtime_fixture, "executableRuntime", runtime_target))

        duplicate_fixture = create_skyrim_workflow_fixture(
            self.fixture.root / "duplicate-recipe-key"
        )
        original_recipe = duplicate_fixture.recipe_path.read_text(encoding="utf-8")
        duplicate_recipe = duplicate_fixture.root / "intent" / "duplicate.json"
        duplicate_recipe.write_text(
            original_recipe.replace(
                '"schemaVersion": 1,',
                '"schemaVersion": 1, "schemaVersion": 1,',
                1,
            ),
            encoding="utf-8",
        )
        cases.append((duplicate_fixture, "duplicate", None, duplicate_recipe))

        for item in cases:
            fixture, reason, target_override, *recipe_override = item
            with self.subTest(reason=reason):
                self.fixture = fixture
                arguments = self.configure_arguments()
                if target_override is not None:
                    arguments[arguments.index(str(fixture.environment_path))] = str(
                        target_override
                    )
                if recipe_override:
                    arguments[arguments.index(str(fixture.recipe_path))] = str(
                        recipe_override[0]
                    )
                self.assert_refused_without_changes(arguments, reason)

    def test_unstable_read_set_blocks_capture_and_status_without_writes(self):
        self.assertEqual(0, self.run_cli(self.configure_arguments())[0])
        self.assertEqual(0, self.run_cli([
            "skyrim", "baseline", "create",
            "--workspace", str(self.fixture.workspace),
        ])[0])
        real_verify = Mo2ReadSet.verify

        def unstable(read_set):
            result = real_verify(read_set)
            return replace(
                result,
                stable=False,
                changed_paths=(str(self.fixture.mo2_root / "ModOrganizer.ini"),),
            )

        for arguments in (
            [
                "skyrim", "baseline", "create",
                "--workspace", str(self.fixture.workspace),
            ],
            [
                "skyrim", "status",
                "--workspace", str(self.fixture.workspace),
                "--format", "json",
            ],
        ):
            with self.subTest(command=arguments[1:3]):
                with patch.object(Mo2ReadSet, "verify", autospec=True, side_effect=unstable):
                    self.assert_refused_without_changes(
                        arguments, "mo2-state-changed-during-inspection"
                    )

    def test_mutated_retained_source_blocks_capture_and_status_without_writes(self):
        self.assertEqual(0, self.run_cli(self.configure_arguments())[0])
        configuration = SkyrimEnvironmentStore(
            self.fixture.workspace
        ).load().configuration
        retained_recipe = (
            self.fixture.workspace / configuration.recipe.source.stored_path
        )
        retained_recipe.write_bytes(retained_recipe.read_bytes() + b" ")

        for arguments in (
            [
                "skyrim", "baseline", "create",
                "--workspace", str(self.fixture.workspace),
            ],
            [
                "skyrim", "status",
                "--workspace", str(self.fixture.workspace),
                "--format", "json",
            ],
        ):
            with self.subTest(command=arguments[1:3]):
                self.assert_refused_without_changes(arguments, "retained recipe")

    def test_modified_checkpoint_blocks_use_and_status_without_writes(self):
        self.assertEqual(0, self.run_cli(self.configure_arguments())[0])
        _, capture_output, _ = self.run_cli([
            "skyrim", "baseline", "create",
            "--workspace", str(self.fixture.workspace),
            "--format", "json",
        ])
        checkpoint_id = json.loads(capture_output)["baselineCapture"]["checkpointId"]
        checkpoint_path = (
            self.fixture.workspace / "games" / "skyrim-se-ae" / "checkpoints"
            / checkpoint_id.removeprefix("checkpoint-sha256:")
            / "modlab.lock.json"
        )
        checkpoint_value = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        checkpoint_value["recipeRevision"] = "tampered"
        checkpoint_path.write_text(json.dumps(checkpoint_value), encoding="utf-8")

        for arguments, reason in (
            ([
                "skyrim", "baseline", "use", checkpoint_id,
                "--workspace", str(self.fixture.workspace),
            ], "modified"),
            ([
                "skyrim", "status",
                "--workspace", str(self.fixture.workspace),
                "--format", "json",
            ], "recorded identity"),
        ):
            with self.subTest(command=arguments[1:3]):
                self.assert_refused_without_changes(arguments, reason)

    def test_redirected_configuration_blocks_every_operation_without_writes(self):
        self.assertEqual(0, self.run_cli(self.configure_arguments())[0])
        _, capture_output, _ = self.run_cli([
            "skyrim", "baseline", "create",
            "--workspace", str(self.fixture.workspace),
            "--format", "json",
        ])
        checkpoint_id = json.loads(capture_output)["baselineCapture"]["checkpointId"]
        configuration_path = (
            self.fixture.workspace / "games" / "skyrim-se-ae" / "environment.json"
        )
        real_is_symlink = Path.is_symlink

        def report_redirect(path):
            return path == configuration_path or real_is_symlink(path)

        commands = (
            self.configure_arguments(),
            [
                "skyrim", "baseline", "create",
                "--workspace", str(self.fixture.workspace),
            ],
            [
                "skyrim", "baseline", "use", checkpoint_id,
                "--workspace", str(self.fixture.workspace),
            ],
            [
                "skyrim", "status",
                "--workspace", str(self.fixture.workspace),
                "--format", "json",
            ],
        )
        with patch.object(
            Path, "is_symlink", autospec=True, side_effect=report_redirect
        ):
            for arguments in commands:
                with self.subTest(command=arguments[1:3]):
                    self.assert_refused_without_changes(arguments, "redirected")


if __name__ == "__main__":
    unittest.main()
