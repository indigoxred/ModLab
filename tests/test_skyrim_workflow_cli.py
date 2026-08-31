import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modlab.cli import main
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


if __name__ == "__main__":
    unittest.main()
