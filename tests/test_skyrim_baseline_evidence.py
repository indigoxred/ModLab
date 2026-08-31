import copy
import tempfile
import unittest
from pathlib import Path

from modlab.adapters.mo2.projection import adapter_state_sha256
from modlab.recipes.loading import load_environment_source, load_recipe_source
from modlab.recipes.model import RecipeIdentity
from modlab.workflows.skyrim.evidence import (
    BaselineEvidenceFormatError,
    BaselineRecipeEvidence,
    BaselineTargetEnvironmentEvidence,
    ObservedBaselineEvidence,
    baseline_evidence_from_dict,
    baseline_evidence_to_dict,
)
from modlab.workflows.skyrim.observation import observe_skyrim_environment
from tests.support.skyrim_workflow import create_skyrim_workflow_fixture


def assign_nested(value: dict[str, object], path: tuple[str, ...], item) -> None:
    target = value
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = item


class SkyrimBaselineEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.fixture = create_skyrim_workflow_fixture(
            Path(self.directory.name)
        )
        self.recipe_source = load_recipe_source(self.fixture.recipe_path)
        self.environment_source = load_environment_source(
            self.fixture.environment_path
        )
        self.observation = observe_skyrim_environment(
            self.fixture.steam_root,
            self.fixture.workspace,
            self.environment_source.environment,
            skyrim_version_reader=lambda _: "1.7.104.0",
            mo2_version_reader=lambda _: "2.5.2.0",
        )
        self.state = self.observation.projection.adapter_state

    def valid_evidence(self) -> ObservedBaselineEvidence:
        return ObservedBaselineEvidence(
            game_discovery=self.observation.discovery,
            manager_observation_context=(
                self.observation.projection.observation_context
            ),
            manager_capabilities=self.observation.projection.capabilities,
            manager_findings=self.observation.projection.findings,
            adapter_state_sha256=adapter_state_sha256(self.state),
            recipe_review=BaselineRecipeEvidence(
                recipe_source_sha256=self.recipe_source.sha256,
                selected=("foundation-core",),
                omitted=(),
                identity=RecipeIdentity.ORIGINAL,
                ready_for_approval=True,
            ),
            target_environment=BaselineTargetEnvironmentEvidence(
                environment_source_sha256=self.environment_source.sha256,
                environment_id=self.environment_source.environment.environment_id,
                dimensions=tuple(
                    sorted(
                        self.environment_source.environment.dimensions.items()
                    )
                ),
                live_dimension_findings=self.observation.dimension_findings,
            ),
        )

    def test_observed_evidence_round_trip_has_fixed_non_claims(self):
        # Catches a baseline accidentally becoming a promotion or playability claim.
        evidence = self.valid_evidence()

        value = baseline_evidence_to_dict(evidence, adapter_state=self.state)
        restored = baseline_evidence_from_dict(
            value, adapter_state=self.state
        )

        self.assertEqual(evidence, restored)
        self.assertEqual("ObservedBaseline", value["captureKind"])
        self.assertEqual("Observed", value["qualification"])
        self.assertFalse(value["promotionReady"])
        self.assertFalse(value["restorable"])
        self.assertEqual("NotLinked", value["coverage"]["artifactCoverage"])
        self.assertEqual([], value["actionsPerformed"])
        self.assertEqual([], value["downloadsPerformed"])
        self.assertEqual([], value["installationActionsPerformed"])
        self.assertEqual([], value["programsLaunched"])

    def test_rejects_any_stronger_claim_or_side_effect(self):
        # Catches edited evidence overstating what this increment performed.
        mutations = (
            (("promotionReady",), True),
            (("restorable",), True),
            (("foundationAssembly",), "Verified"),
            (("validation", "runtimeValidation"), "Passed"),
            (("validation", "smokeTest"), "Passed"),
            (("coverage", "installedPayloadContent"), "Inspected"),
            (("coverage", "assetConflicts"), "Inspected"),
            (("coverage", "pluginRecordConflicts"), "Inspected"),
            (("coverage", "artifactCoverage"), "Linked"),
            (("actionsPerformed",), ["changed-profile"]),
            (("downloadsPerformed",), ["downloaded-mod"]),
            (("installationActionsPerformed",), ["installed-mod"]),
            (("programsLaunched",), ["SkyrimSE.exe"]),
        )
        for path, replacement in mutations:
            with self.subTest(path=path):
                value = baseline_evidence_to_dict(
                    self.valid_evidence(), adapter_state=self.state
                )
                assign_nested(value, path, replacement)
                with self.assertRaises(BaselineEvidenceFormatError):
                    baseline_evidence_from_dict(
                        value, adapter_state=self.state
                    )

    def test_rejects_unknown_or_missing_fields_at_every_evidence_layer(self):
        # Catches forward fields being silently ignored by an older safety parser.
        base = baseline_evidence_to_dict(
            self.valid_evidence(), adapter_state=self.state
        )

        def targets(value):
            return (
                value,
                value["managerObservation"],
                value["recipeReview"],
                value["targetEnvironment"],
                value["targetEnvironment"]["liveDimensionFindings"][0],
                value["validation"],
                value["coverage"],
            )

        for index in range(len(targets(copy.deepcopy(base)))):
            value = copy.deepcopy(base)
            targets(value)[index]["unexpected"] = True
            with self.subTest(layer=index, kind="extra"):
                with self.assertRaises(BaselineEvidenceFormatError):
                    baseline_evidence_from_dict(
                        value, adapter_state=self.state
                    )

            value = copy.deepcopy(base)
            key = next(iter(targets(value)[index]))
            del targets(value)[index][key]
            with self.subTest(layer=index, kind="missing"):
                with self.assertRaises(BaselineEvidenceFormatError):
                    baseline_evidence_from_dict(
                        value, adapter_state=self.state
                    )

    def test_rejects_adapter_state_hash_inconsistency(self):
        # Catches evidence text referring to a different checkpoint adapter state.
        value = baseline_evidence_to_dict(
            self.valid_evidence(), adapter_state=self.state
        )
        value["managerObservation"]["adapterStateSha256"] = "f" * 64

        with self.assertRaisesRegex(
            BaselineEvidenceFormatError, "adapterStateSha256"
        ):
            baseline_evidence_from_dict(value, adapter_state=self.state)

    def test_rejects_noncanonical_selection_dimensions_and_overlap(self):
        # Catches ambiguous recipe intent or target ordering entering a lockfile.
        base = baseline_evidence_to_dict(
            self.valid_evidence(), adapter_state=self.state
        )
        cases = []
        unsorted_selection = copy.deepcopy(base)
        unsorted_selection["recipeReview"]["selected"] = ["z", "a"]
        cases.append(unsorted_selection)
        overlap = copy.deepcopy(base)
        overlap["recipeReview"]["omitted"] = ["foundation-core"]
        cases.append(overlap)
        unsorted_dimensions = copy.deepcopy(base)
        unsorted_dimensions["targetEnvironment"]["dimensions"] = list(
            reversed(unsorted_dimensions["targetEnvironment"]["dimensions"])
        )
        cases.append(unsorted_dimensions)

        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(BaselineEvidenceFormatError):
                    baseline_evidence_from_dict(
                        value, adapter_state=self.state
                    )

    def test_rejects_invalid_live_dimension_relationships(self):
        # Catches Passed/Blocked/Unknown labels disagreeing with actual evidence.
        base = baseline_evidence_to_dict(
            self.valid_evidence(), adapter_state=self.state
        )
        findings = base["targetEnvironment"]["liveDimensionFindings"]
        script_index = next(
            index
            for index, item in enumerate(findings)
            if item["dimension"] == "scriptExtender"
        )
        cases = []
        passed_without_actual = copy.deepcopy(base)
        passed_without_actual["targetEnvironment"]["liveDimensionFindings"][
            script_index
        ]["state"] = "Passed"
        cases.append(passed_without_actual)
        wrong_target = copy.deepcopy(base)
        wrong_target["targetEnvironment"]["liveDimensionFindings"][0][
            "targetValue"
        ] = "wrong"
        cases.append(wrong_target)
        warning = copy.deepcopy(base)
        warning["targetEnvironment"]["liveDimensionFindings"][0][
            "state"
        ] = "Warning"
        cases.append(warning)

        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(BaselineEvidenceFormatError):
                    baseline_evidence_from_dict(
                        value, adapter_state=self.state
                    )

    def test_rejects_save_like_strings_in_path_bearing_evidence(self):
        # Catches checkpoint evidence growing a path into protected save data.
        value = baseline_evidence_to_dict(
            self.valid_evidence(), adapter_state=self.state
        )
        value["managerObservation"]["observationContext"]["mo2Root"] = (
            r"C:\ModLab\workspace\Saves\MO2"
        )

        with self.assertRaisesRegex(BaselineEvidenceFormatError, "save"):
            baseline_evidence_from_dict(value, adapter_state=self.state)


if __name__ == "__main__":
    unittest.main()
