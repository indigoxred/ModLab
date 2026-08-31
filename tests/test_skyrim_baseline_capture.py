import json
import tempfile
import unittest
from dataclasses import fields, replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from modlab.adapters.mo2.comparison_serialization import adapter_state_from_dict
from modlab.adapters.mo2.model import Mo2Finding
from modlab.checkpoints.model import CheckpointDraft
from modlab.checkpoints.store import CheckpointStore
from modlab.recipes.loading import load_environment_source, load_recipe_source
from modlab.recipes.model import CheckState, RecipeIdentity, RecipeMaturity
from modlab.recipes.reviewing import review_recipe
from modlab.workflows.skyrim.capture import (
    SkyrimBaselineCaptureError,
    SkyrimBaselineSelectionError,
    create_observed_baseline,
    select_observed_baseline,
)
from modlab.workflows.skyrim.configuration import (
    ManagerRegistration,
    RecipeIntent,
    SkyrimEnvironmentConfiguration,
    TargetEnvironmentIntent,
)
from modlab.workflows.skyrim.evidence import baseline_evidence_from_dict
from modlab.workflows.skyrim.observation import observe_skyrim_environment
from modlab.workflows.skyrim.store import (
    SkyrimEnvironmentConflictError,
    SkyrimEnvironmentStore,
)
from tests.support.skyrim_workflow import create_skyrim_workflow_fixture


FIRST_TIME = datetime(2026, 8, 31, 1, 2, 3, tzinfo=timezone.utc)
SECOND_TIME = datetime(2026, 8, 31, 1, 2, 4, tzinfo=timezone.utc)


def draft_from_record(record, **changes) -> CheckpointDraft:
    values = {
        field.name: getattr(record, field.name)
        for field in fields(CheckpointDraft)
    }
    values.update(changes)
    return CheckpointDraft(**values)


class SkyrimBaselineCaptureTests(unittest.TestCase):
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
        self.review = review_recipe(
            self.recipe_source.recipe,
            self.environment_source.environment,
        )
        self.observation = observe_skyrim_environment(
            self.fixture.steam_root,
            self.fixture.workspace,
            self.environment_source.environment,
            skyrim_version_reader=lambda _: "1.7.104.0",
            mo2_version_reader=lambda _: "2.5.2.0",
        )
        self.store = SkyrimEnvironmentStore(self.fixture.workspace)
        recipe_retention = self.store.retain_recipe(self.recipe_source)
        environment_retention = self.store.retain_target_environment(
            self.environment_source
        )
        configuration = SkyrimEnvironmentConfiguration(
            schema_version=1,
            game_key="skyrim-se-ae",
            environment_id=(
                self.environment_source.environment.environment_id
            ),
            lineage_id="skyrim-main",
            steam_root=str(self.fixture.steam_root.resolve()),
            game_root=str(self.fixture.game_root.resolve()),
            manager=ManagerRegistration(
                adapter_id="portable-mo2-skyrim",
                root="tools/mo2/skyrim-se-ae/app",
            ),
            recipe=RecipeIntent(
                recipe_id=self.recipe_source.recipe.recipe_id,
                revision=self.recipe_source.recipe.revision,
                maturity=self.recipe_source.recipe.maturity,
                identity=self.review.identity,
                source=recipe_retention.reference,
                selected=self.review.selected,
                omitted=self.review.omitted,
            ),
            target_environment=TargetEnvironmentIntent(
                environment_id=(
                    self.environment_source.environment.environment_id
                ),
                source=environment_retention.reference,
                dimensions=tuple(
                    sorted(
                        self.environment_source.environment.dimensions.items()
                    )
                ),
            ),
            baseline_checkpoint_id=None,
        )
        self.snapshot = self.store.write(
            configuration, replace=False
        ).snapshot
        self.checkpoint_store = CheckpointStore(
            self.fixture.workspace, "skyrim-se-ae"
        )

    def capture(self, when=FIRST_TIME):
        self.snapshot = self.store.load()
        return create_observed_baseline(
            self.snapshot,
            self.observation,
            self.review,
            self.store,
            self.checkpoint_store,
            clock=lambda: when,
        )

    def test_capture_creates_observed_checkpoint_and_selects_it(self):
        # Catches capture failing to bind state, evidence, and selected pointer.
        result = self.capture()

        self.assertTrue(result.selected)
        self.assertEqual((), result.checkpoint.artifact_ids)
        self.assertEqual(
            "portable-mo2-skyrim", result.checkpoint.adapter_id
        )
        self.assertEqual(
            result.checkpoint.checkpoint_id,
            result.configuration.baseline_checkpoint_id,
        )
        self.assertEqual(2, len(result.actions_performed))
        state = adapter_state_from_dict(
            json.loads(result.checkpoint.adapter_state_json)
        )
        evidence = baseline_evidence_from_dict(
            json.loads(result.checkpoint.evidence_json), adapter_state=state
        )
        self.assertFalse(evidence.promotion_ready)
        self.assertFalse(evidence.restorable)

    def test_second_capture_uses_selected_valid_baseline_as_parent(self):
        # Catches baseline lineage silently disconnecting from its selected parent.
        first = self.capture(FIRST_TIME)
        second = self.capture(SECOND_TIME)

        self.assertEqual(
            first.checkpoint.checkpoint_id,
            second.checkpoint.parent_checkpoint_id,
        )

    def test_same_second_capture_reuses_checkpoint_without_false_actions(self):
        # Catches duplicate same-second checkpoints or inaccurate write reporting.
        first = self.capture(FIRST_TIME)
        second = self.capture(FIRST_TIME)

        self.assertEqual(
            first.checkpoint.checkpoint_id, second.checkpoint.checkpoint_id
        )
        self.assertTrue(second.selected)
        self.assertEqual((), second.actions_performed)

    def test_pointer_failure_leaves_checkpoint_available_but_unselected(self):
        # Catches CAS failure deleting the useful immutable orphan checkpoint.
        with patch.object(
            self.store,
            "compare_and_swap",
            side_effect=SkyrimEnvironmentConflictError("changed"),
        ):
            result = self.capture()

        self.assertFalse(result.selected)
        self.assertEqual(
            result.checkpoint,
            self.checkpoint_store.get(result.checkpoint.checkpoint_id),
        )
        self.assertIn(result.checkpoint.checkpoint_id, result.warning)
        self.assertIsNone(self.store.load().configuration.baseline_checkpoint_id)
        self.assertEqual(1, len(result.actions_performed))

    def test_valid_orphan_can_be_selected_later(self):
        # Catches a pointer failure making a verified checkpoint unrecoverable.
        with patch.object(
            self.store,
            "compare_and_swap",
            side_effect=SkyrimEnvironmentConflictError("changed"),
        ):
            orphan = self.capture()
        snapshot = self.store.load()

        write = select_observed_baseline(
            snapshot,
            orphan.checkpoint.checkpoint_id,
            self.store,
            self.checkpoint_store,
        )

        self.assertTrue(write.changed)
        self.assertEqual(
            orphan.checkpoint.checkpoint_id,
            write.configuration.baseline_checkpoint_id,
        )

    def test_unready_or_incoherent_inputs_block_before_checkpoint_write(self):
        # Catches callers forcing a capture with independently incomplete evidence.
        cases = {
            "unready observation": replace(self.observation, ready=False),
            "unstable read set": replace(
                self.observation,
                projection=replace(
                    self.observation.projection,
                    observation_context=replace(
                        self.observation.projection.observation_context,
                        read_set_stable=False,
                    ),
                ),
            ),
            "missing manager identity": replace(
                self.observation,
                projection=replace(
                    self.observation.projection,
                    observation_context=replace(
                        self.observation.projection.observation_context,
                        executable=None,
                    ),
                ),
            ),
            "missing game identity": replace(
                self.observation,
                discovery=replace(self.observation.discovery, executable=None),
            ),
            "blocking manager finding": replace(
                self.observation,
                projection=replace(
                    self.observation.projection,
                    findings=(
                        *self.observation.projection.findings,
                        Mo2Finding(
                            CheckState.BLOCKED,
                            "forced-block",
                            "forced blocking evidence",
                        ),
                    ),
                ),
            ),
        }
        for label, observation in cases.items():
            with self.subTest(label=label):
                before = tuple(self.checkpoint_store.list())
                with self.assertRaises(SkyrimBaselineCaptureError):
                    create_observed_baseline(
                        self.snapshot,
                        observation,
                        self.review,
                        self.store,
                        self.checkpoint_store,
                        clock=lambda: FIRST_TIME,
                    )
                self.assertEqual(before, tuple(self.checkpoint_store.list()))
                self.assertIsNone(
                    self.store.load().configuration.baseline_checkpoint_id
                )

    def test_non_ready_recipe_review_blocks_before_checkpoint_write(self):
        # Catches Incomplete intent being stored as an observed baseline.
        review = replace(
            self.review,
            identity=RecipeIdentity.INCOMPLETE,
            ready_for_approval=False,
        )

        with self.assertRaises(SkyrimBaselineCaptureError):
            create_observed_baseline(
                self.snapshot,
                self.observation,
                review,
                self.store,
                self.checkpoint_store,
                clock=lambda: FIRST_TIME,
            )
        self.assertEqual((), self.checkpoint_store.list())

    def test_changed_retained_source_blocks_before_checkpoint_write(self):
        # Catches configuration identity being trusted after retained bytes change.
        retained = (
            self.fixture.workspace
            / self.snapshot.configuration.recipe.source.stored_path
        )
        retained.write_bytes(retained.read_bytes() + b"\n")

        with self.assertRaises(SkyrimBaselineCaptureError):
            self.capture()
        self.assertEqual((), self.checkpoint_store.list())

    def test_modified_selected_parent_blocks_next_capture(self):
        # Catches a modified lockfile being used as a trusted lineage parent.
        first = self.capture()
        path = self.checkpoint_store.path_for(first.checkpoint.checkpoint_id)
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                '"foundationAssembly": "NotVerified"',
                '"foundationAssembly": "Verified"',
            ),
            encoding="utf-8",
        )

        with self.assertRaises(SkyrimBaselineCaptureError):
            self.capture(SECOND_TIME)
        self.assertEqual(1, len(tuple(self.checkpoint_store.checkpoints_path.glob(
            "*/modlab.lock.json"
        ))))

    def test_selection_rejects_every_checkpoint_cross_link(self):
        # Catches a valid lockfile from another intent being selected locally.
        base = self.capture().checkpoint
        snapshot = self.store.load()
        variants = {
            "environment": {"environment_id": "skyrim.other"},
            "adapter": {"adapter_id": "other-adapter"},
            "lineage": {"lineage_id": "other-lineage"},
            "recipe id": {"recipe_id": "other.recipe"},
            "recipe revision": {"recipe_revision": "other.revision"},
            "recipe maturity": {"recipe_maturity": RecipeMaturity.CANDIDATE},
            "recipe identity": {"recipe_identity": RecipeIdentity.CUSTOM},
            "artifact linkage": {"artifact_ids": ("archive-sha256:" + "a" * 64,)},
        }
        for label, changes in variants.items():
            with self.subTest(label=label):
                draft = draft_from_record(
                    base,
                    created_at="2026-08-31T01:03:00Z",
                    parent_checkpoint_id=None,
                    **changes,
                )
                record = self.checkpoint_store.create(draft)
                with self.assertRaises(SkyrimBaselineSelectionError):
                    select_observed_baseline(
                        snapshot,
                        record.checkpoint_id,
                        self.store,
                        self.checkpoint_store,
                    )

    def test_selection_rejects_cross_game_and_modified_checkpoint(self):
        # Catches store location or lockfile presence being mistaken for validity.
        base = self.capture().checkpoint
        snapshot = self.store.load()
        other_store = CheckpointStore(self.fixture.workspace, "morrowind")
        other = other_store.create(
            draft_from_record(
                base,
                game="morrowind",
                created_at="2026-08-31T01:04:00Z",
                parent_checkpoint_id=None,
            )
        )
        with self.assertRaises(SkyrimBaselineSelectionError):
            select_observed_baseline(
                snapshot,
                other.checkpoint_id,
                self.store,
                other_store,
            )

        path = self.checkpoint_store.path_for(base.checkpoint_id)
        path.write_text("{}", encoding="utf-8")
        with self.assertRaises(SkyrimBaselineSelectionError):
            select_observed_baseline(
                snapshot,
                base.checkpoint_id,
                self.store,
                self.checkpoint_store,
            )

    def test_selection_rejects_cross_linked_evidence_and_stronger_claim(self):
        # Catches semantically wrong evidence hidden in a canonically valid checkpoint.
        base = self.capture().checkpoint
        snapshot = self.store.load()
        mutations = {
            "recipe source": lambda value: value["recipeReview"].__setitem__(
                "recipeSourceSha256", "f" * 64
            ),
            "target id": lambda value: value["targetEnvironment"].__setitem__(
                "environmentId", "skyrim.other"
            ),
            "selection": lambda value: value["recipeReview"].__setitem__(
                "selected", ["other-component"]
            ),
            "stronger claim": lambda value: value.__setitem__(
                "promotionReady", True
            ),
        }
        for index, (label, mutate) in enumerate(mutations.items()):
            with self.subTest(label=label):
                evidence = json.loads(base.evidence_json)
                mutate(evidence)
                draft = draft_from_record(
                    base,
                    created_at=f"2026-08-31T01:05:{index:02d}Z",
                    parent_checkpoint_id=None,
                    evidence_json=json.dumps(
                        evidence, sort_keys=True, separators=(",", ":")
                    ),
                )
                record = self.checkpoint_store.create(draft)
                with self.assertRaises(SkyrimBaselineSelectionError):
                    select_observed_baseline(
                        snapshot,
                        record.checkpoint_id,
                        self.store,
                        self.checkpoint_store,
                    )


if __name__ == "__main__":
    unittest.main()
