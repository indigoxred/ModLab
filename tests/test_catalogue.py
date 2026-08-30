import unittest
from pathlib import Path

from modlab.recipes.loading import load_environment, load_recipe
from modlab.recipes.model import ComponentImportance, RecipeMaturity


ROOT = Path(__file__).resolve().parents[1]


class BundledCatalogueTests(unittest.TestCase):
    def test_every_bundled_recipe_loads_and_remains_draft(self):
        paths = sorted((ROOT / "catalogue" / "recipes").glob("*.json"))

        self.assertEqual(2, len(paths))
        self.assertTrue(
            all(load_recipe(path).maturity is RecipeMaturity.DRAFT for path in paths)
        )

    def test_skyrim_draft_records_current_runtime_and_skse_as_data(self):
        recipe = load_recipe(
            ROOT / "catalogue" / "recipes" / "skyrim-se-ae-current-draft.json"
        )
        environment = load_environment(
            ROOT / "catalogue" / "environments" / "skyrim-steam-1.7.104.json"
        )
        components = recipe.component_map()

        self.assertEqual("1.7.104", environment.dimensions["executableRuntime"])
        self.assertEqual("2.3.1", environment.dimensions["scriptExtender"])
        self.assertIn("content conditional", recipe.target.edition.casefold())
        self.assertEqual(ComponentImportance.REQUIRED, components["skse64"].importance)
        self.assertEqual("2.3.1", components["skse64"].version)
        runtime_constraint = next(
            item
            for item in components["skse64"].constraints
            if item.dimension == "executableRuntime"
        )
        self.assertEqual(("1.7.104",), runtime_constraint.allowed_values)

    def test_skyrim_draft_keeps_play_mods_out_of_foundation(self):
        recipe = load_recipe(
            ROOT / "catalogue" / "recipes" / "skyrim-se-ae-current-draft.json"
        )
        component_ids = set(recipe.component_map())

        self.assertTrue({"skse64", "skyui", "ussep"}.issubset(component_ids))
        self.assertTrue(
            {"rdo", "cutting-room-floor", "racemenu"}.isdisjoint(component_ids)
        )

    def test_openmw_draft_uses_openmw_adapter_without_mo2(self):
        recipe = load_recipe(
            ROOT / "catalogue" / "recipes" / "openmw-foundation-draft.json"
        )

        self.assertEqual("OpenMW", recipe.target.adapter)
        self.assertNotIn("MO2", {component.deployment for component in recipe.components})


if __name__ == "__main__":
    unittest.main()
