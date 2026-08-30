import copy
import json
import tempfile
import unittest
from pathlib import Path

from modlab.recipes.loading import (
    RecipeFormatError,
    load_environment,
    load_recipe,
    parse_environment,
    parse_recipe,
)
from modlab.recipes.model import ComponentImportance, RecipeMaturity


VALID_RECIPE = {
    "schemaVersion": 1,
    "recipeId": "example.foundation",
    "revision": "2026.08.30.1",
    "displayName": "Example Foundation",
    "maturity": "Draft",
    "target": {
        "game": "Example Game",
        "edition": "Special",
        "distribution": "Steam",
        "engineLane": "Example Engine",
        "adapter": "Example Adapter",
    },
    "researchedAt": "2026-08-30",
    "components": [
        {
            "componentId": "core",
            "displayName": "Core",
            "importance": "Required",
            "defaultSelected": True,
            "role": "Permanent Core",
            "deployment": "Data/VFS",
            "requires": [],
            "incompatibleWith": [],
            "constraints": [
                {
                    "dimension": "executableRuntime",
                    "allowedValues": ["1.2.3"],
                    "reason": "The native build targets runtime 1.2.3.",
                }
            ],
            "rationale": "Provides the example runtime integration.",
            "sources": ["https://example.invalid/core"],
        },
        {
            "componentId": "optional-ui",
            "displayName": "Optional UI",
            "importance": "Optional",
            "defaultSelected": False,
            "role": "Optional Mod",
            "deployment": "Data/VFS",
            "requires": ["core"],
            "incompatibleWith": [],
            "constraints": [],
            "rationale": "Demonstrates an opt-in component.",
            "sources": [],
        },
    ],
}

VALID_ENVIRONMENT = {
    "schemaVersion": 1,
    "environmentId": "example.steam",
    "dimensions": {"executableRuntime": "1.2.3"},
}


class RecipeLoadingTests(unittest.TestCase):
    def test_parse_recipe_keeps_maturity_separate_from_importance(self):
        recipe = parse_recipe(VALID_RECIPE)

        self.assertEqual(RecipeMaturity.DRAFT, recipe.maturity)
        self.assertEqual(ComponentImportance.REQUIRED, recipe.components[0].importance)
        self.assertEqual(ComponentImportance.OPTIONAL, recipe.components[1].importance)

    def test_parse_recipe_rejects_unknown_maturity(self):
        malformed = {**VALID_RECIPE, "maturity": "Popular"}

        with self.assertRaisesRegex(RecipeFormatError, "maturity"):
            parse_recipe(malformed)

    def test_parse_recipe_rejects_dependency_on_unknown_component(self):
        malformed = copy.deepcopy(VALID_RECIPE)
        malformed["components"][1]["requires"] = ["missing-component"]

        with self.assertRaisesRegex(RecipeFormatError, "missing-component"):
            parse_recipe(malformed)

    def test_parse_recipe_rejects_required_component_disabled_by_default(self):
        malformed = copy.deepcopy(VALID_RECIPE)
        malformed["components"][0]["defaultSelected"] = False

        with self.assertRaisesRegex(RecipeFormatError, "Required"):
            parse_recipe(malformed)

    def test_parse_environment_preserves_missing_dimensions_as_missing_evidence(self):
        environment = parse_environment(VALID_ENVIRONMENT)

        self.assertEqual("1.2.3", environment.dimensions["executableRuntime"])
        self.assertNotIn("scriptExtender", environment.dimensions)
        with self.assertRaises(TypeError):
            environment.dimensions["scriptExtender"] = "changed"

    def test_file_loaders_translate_invalid_json_to_recipe_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "broken.json")
            path.write_text("{not-json", encoding="utf-8")

            with self.assertRaisesRegex(RecipeFormatError, "valid JSON"):
                load_recipe(path)

    def test_file_loaders_accept_valid_recipe_and_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            recipe_path = Path(directory, "recipe.json")
            environment_path = Path(directory, "environment.json")
            recipe_path.write_text(json.dumps(VALID_RECIPE), encoding="utf-8")
            environment_path.write_text(json.dumps(VALID_ENVIRONMENT), encoding="utf-8")

            recipe = load_recipe(recipe_path)
            environment = load_environment(environment_path)

        self.assertEqual("example.foundation", recipe.recipe_id)
        self.assertEqual("example.steam", environment.environment_id)


if __name__ == "__main__":
    unittest.main()
