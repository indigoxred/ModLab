import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from modlab.cli import main
from tests.test_loading import VALID_ENVIRONMENT, VALID_RECIPE


class CliTests(unittest.TestCase):
    def write_json(self, directory: str, name: str, value: object) -> Path:
        path = Path(directory, name)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_check_reports_identity_revision_and_no_action(self):
        with tempfile.TemporaryDirectory() as directory:
            recipe = self.write_json(directory, "recipe.json", VALID_RECIPE)
            stdout, stderr = io.StringIO(), io.StringIO()

            code = main(["recipe", "check", str(recipe)], stdout, stderr)

        self.assertEqual(0, code)
        self.assertIn("example.foundation @ 2026.08.30.1", stdout.getvalue())
        self.assertIn("Maturity: Draft", stdout.getvalue())
        self.assertIn(
            "No downloads or installation actions were performed.",
            stdout.getvalue(),
        )
        self.assertEqual("", stderr.getvalue())

    def test_review_json_has_stable_schema_and_explicit_dependency_proposal(self):
        with tempfile.TemporaryDirectory() as directory:
            recipe_data = copy.deepcopy(VALID_RECIPE)
            recipe_data["components"][0]["defaultSelected"] = False
            recipe_data["components"][0]["importance"] = "Conditional"
            recipe = self.write_json(directory, "recipe.json", recipe_data)
            environment = self.write_json(
                directory,
                "environment.json",
                VALID_ENVIRONMENT,
            )
            stdout, stderr = io.StringIO(), io.StringIO()

            code = main(
                [
                    "recipe",
                    "review",
                    str(recipe),
                    "--environment",
                    str(environment),
                    "--select",
                    "optional-ui",
                    "--format",
                    "json",
                ],
                stdout,
                stderr,
            )

        result = json.loads(stdout.getvalue())
        self.assertEqual(3, code)
        self.assertEqual(1, result["schemaVersion"])
        self.assertEqual("Incomplete", result["identity"])
        self.assertEqual(["core"], result["dependencyProposals"])
        self.assertFalse(result["readyForApproval"])
        self.assertEqual([], result["actionsPerformed"])
        self.assertEqual("", stderr.getvalue())

    def test_text_review_keeps_unknown_evidence_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            recipe = self.write_json(directory, "recipe.json", VALID_RECIPE)
            environment = self.write_json(
                directory,
                "environment.json",
                {
                    "schemaVersion": 1,
                    "environmentId": "uninspected",
                    "dimensions": {},
                },
            )
            stdout, stderr = io.StringIO(), io.StringIO()

            code = main(
                [
                    "recipe",
                    "review",
                    str(recipe),
                    "--environment",
                    str(environment),
                ],
                stdout,
                stderr,
            )

        self.assertEqual(0, code)
        self.assertIn("Unknown", stdout.getvalue())
        self.assertNotIn("Passed  core executableRuntime", stdout.getvalue())
        self.assertEqual("", stderr.getvalue())

    def test_blocked_review_returns_three(self):
        with tempfile.TemporaryDirectory() as directory:
            recipe = self.write_json(directory, "recipe.json", VALID_RECIPE)
            environment_data = copy.deepcopy(VALID_ENVIRONMENT)
            environment_data["dimensions"]["executableRuntime"] = "9.9.9"
            environment = self.write_json(
                directory,
                "environment.json",
                environment_data,
            )
            stdout, stderr = io.StringIO(), io.StringIO()

            code = main(
                [
                    "recipe",
                    "review",
                    str(recipe),
                    "--environment",
                    str(environment),
                ],
                stdout,
                stderr,
            )

        self.assertEqual(3, code)
        self.assertIn("Blocked", stdout.getvalue())
        self.assertEqual("", stderr.getvalue())

    def test_invalid_recipe_returns_data_error(self):
        with tempfile.TemporaryDirectory() as directory:
            recipe = self.write_json(
                directory,
                "recipe.json",
                {"schemaVersion": 99},
            )
            stdout, stderr = io.StringIO(), io.StringIO()

            code = main(["recipe", "check", str(recipe)], stdout, stderr)

        self.assertEqual(2, code)
        self.assertEqual("", stdout.getvalue())
        self.assertIn("Recipe error:", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
