import copy
import unittest
from types import MappingProxyType

from modlab.recipes.loading import parse_recipe
from modlab.recipes.model import CheckState, EnvironmentEvidence, RecipeIdentity
from modlab.recipes.reviewing import review_recipe
from tests.test_loading import VALID_RECIPE


class RecipeReviewTests(unittest.TestCase):
    def setUp(self):
        self.recipe = parse_recipe(VALID_RECIPE)
        self.environment = EnvironmentEvidence(
            1,
            "example.steam",
            MappingProxyType({"executableRuntime": "1.2.3"}),
        )

    def test_defaults_preserve_original_recipe_identity(self):
        review = review_recipe(self.recipe, self.environment)

        self.assertEqual(RecipeIdentity.ORIGINAL, review.identity)
        self.assertEqual(("core",), review.selected)
        self.assertTrue(review.ready_for_approval)

    def test_optional_selection_proposes_dependency_without_silently_approving_it(self):
        recipe_data = copy.deepcopy(VALID_RECIPE)
        recipe_data["components"][0]["defaultSelected"] = False
        recipe_data["components"][0]["importance"] = "Conditional"
        recipe = parse_recipe(recipe_data)

        review = review_recipe(
            recipe,
            self.environment,
            select=("optional-ui",),
        )

        self.assertEqual(("optional-ui",), review.selected)
        self.assertEqual(("core",), review.dependency_proposals)
        self.assertEqual(RecipeIdentity.INCOMPLETE, review.identity)
        self.assertFalse(review.ready_for_approval)

    def test_omitting_required_component_is_incomplete(self):
        review = review_recipe(self.recipe, self.environment, omit=("core",))

        self.assertEqual(RecipeIdentity.INCOMPLETE, review.identity)
        self.assertFalse(review.ready_for_approval)

    def test_non_required_deviation_is_custom(self):
        review = review_recipe(
            self.recipe,
            self.environment,
            select=("optional-ui",),
        )

        self.assertEqual(RecipeIdentity.CUSTOM, review.identity)
        self.assertEqual((), review.dependency_proposals)

    def test_explicit_omission_wins_when_same_component_is_selected(self):
        review = review_recipe(
            self.recipe,
            self.environment,
            select=("optional-ui",),
            omit=("optional-ui",),
        )

        self.assertEqual(("core",), review.selected)
        self.assertEqual(RecipeIdentity.ORIGINAL, review.identity)

    def test_selected_incompatible_pair_is_incomplete(self):
        recipe_data = copy.deepcopy(VALID_RECIPE)
        recipe_data["components"][0]["incompatibleWith"] = ["optional-ui"]
        recipe = parse_recipe(recipe_data)

        review = review_recipe(
            recipe,
            self.environment,
            select=("optional-ui",),
        )

        self.assertEqual((("core", "optional-ui"),), review.incompatibilities)
        self.assertEqual(RecipeIdentity.INCOMPLETE, review.identity)

    def test_blocked_compatibility_prevents_approval(self):
        wrong_environment = EnvironmentEvidence(
            1,
            "wrong",
            MappingProxyType({"executableRuntime": "9.9.9"}),
        )

        review = review_recipe(self.recipe, wrong_environment)

        self.assertEqual(CheckState.BLOCKED, review.findings[0].state)
        self.assertFalse(review.ready_for_approval)

    def test_missing_evidence_remains_unknown(self):
        unknown_environment = EnvironmentEvidence(
            1,
            "uninspected",
            MappingProxyType({}),
        )

        review = review_recipe(self.recipe, unknown_environment)

        self.assertEqual(CheckState.UNKNOWN, review.findings[0].state)
        self.assertNotEqual(CheckState.PASSED, review.findings[0].state)

    def test_unknown_component_selection_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not-in-recipe"):
            review_recipe(
                self.recipe,
                self.environment,
                select=("not-in-recipe",),
            )


if __name__ == "__main__":
    unittest.main()
