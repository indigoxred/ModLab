import json
import tempfile
import unittest
from dataclasses import fields, replace
from datetime import datetime, timezone
from pathlib import Path

from modlab.adapters.mo2.comparison_serialization import adapter_state_from_dict
from modlab.adapters.mo2.model import Mo2StateFileEvidence
from modlab.adapters.mo2.projection import (
    Mo2InstalledModState,
    Mo2ProjectedMod,
    adapter_state_sha256,
    adapter_state_to_dict,
)
from modlab.checkpoints.model import CheckpointDraft
from modlab.checkpoints.serialization import checkpoint_record_from_draft
from modlab.checkpoints.store import CheckpointStore
from modlab.recipes.loading import load_environment_source, load_recipe_source
from modlab.recipes.model import RecipeIdentity
from modlab.recipes.reviewing import review_recipe
from modlab.workflows.skyrim.capture import create_observed_baseline
from modlab.workflows.skyrim.configuration import (
    ManagerRegistration,
    RecipeIntent,
    SkyrimEnvironmentConfiguration,
    TargetEnvironmentIntent,
)
from modlab.workflows.skyrim.drift import (
    SkyrimStatusOutcome,
    compare_observed_baseline,
)
from modlab.workflows.skyrim.evidence import (
    baseline_evidence_from_dict,
    baseline_evidence_to_dict,
)
from modlab.workflows.skyrim.observation import observe_skyrim_environment
from modlab.workflows.skyrim.store import SkyrimEnvironmentStore
from tests.support.skyrim_workflow import create_skyrim_workflow_fixture


FIXED_TIME = datetime(2026, 8, 31, 2, 0, 0, tzinfo=timezone.utc)


class SkyrimDriftTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.fixture = create_skyrim_workflow_fixture(
            Path(self.directory.name)
        )
        recipe_source = load_recipe_source(self.fixture.recipe_path)
        environment_source = load_environment_source(
            self.fixture.environment_path
        )
        self.review = review_recipe(
            recipe_source.recipe, environment_source.environment
        )
        self.observation = observe_skyrim_environment(
            self.fixture.steam_root,
            self.fixture.workspace,
            environment_source.environment,
            skyrim_version_reader=lambda _: "1.7.104.0",
            mo2_version_reader=lambda _: "2.5.2.0",
        )
        store = SkyrimEnvironmentStore(self.fixture.workspace)
        recipe_retention = store.retain_recipe(recipe_source)
        environment_retention = store.retain_target_environment(
            environment_source
        )
        initial = SkyrimEnvironmentConfiguration(
            schema_version=1,
            game_key="skyrim-se-ae",
            environment_id=environment_source.environment.environment_id,
            lineage_id="skyrim-main",
            steam_root=str(self.fixture.steam_root.resolve()),
            game_root=str(self.fixture.game_root.resolve()),
            manager=ManagerRegistration(
                "portable-mo2-skyrim", "tools/mo2/skyrim-se-ae/app"
            ),
            recipe=RecipeIntent(
                recipe_source.recipe.recipe_id,
                recipe_source.recipe.revision,
                recipe_source.recipe.maturity,
                self.review.identity,
                recipe_retention.reference,
                self.review.selected,
                self.review.omitted,
            ),
            target_environment=TargetEnvironmentIntent(
                environment_source.environment.environment_id,
                environment_retention.reference,
                tuple(sorted(environment_source.environment.dimensions.items())),
            ),
            baseline_checkpoint_id=None,
        )
        snapshot = store.write(initial, replace=False).snapshot
        result = create_observed_baseline(
            snapshot,
            self.observation,
            self.review,
            store,
            CheckpointStore(self.fixture.workspace, "skyrim-se-ae"),
            clock=lambda: FIXED_TIME,
        )
        self.configuration = result.configuration
        self.checkpoint = result.checkpoint
        self.baseline_state = adapter_state_from_dict(
            json.loads(self.checkpoint.adapter_state_json)
        )
        self.evidence = baseline_evidence_from_dict(
            json.loads(self.checkpoint.evidence_json),
            adapter_state=self.baseline_state,
        )

    def compare(
        self,
        *,
        configuration=None,
        checkpoint=None,
        evidence=None,
        observation=None,
        review=None,
    ):
        return compare_observed_baseline(
            configuration or self.configuration,
            self.checkpoint if checkpoint is None else checkpoint,
            self.evidence if evidence is None else evidence,
            observation or self.observation,
            review or self.review,
        )

    def with_state(self, state):
        projection = replace(
            self.observation.projection,
            adapter_state=state,
            adapter_state_sha256=adapter_state_sha256(state),
        )
        return replace(self.observation, projection=projection)

    def test_identical_complete_observation_matches(self):
        # Catches hash-only or incomplete comparisons reporting false drift.
        report = self.compare()

        self.assertEqual(SkyrimStatusOutcome.MATCHED, report.outcome)
        self.assertEqual((), report.domains)

    def test_each_identity_domain_reports_drift(self):
        # Catches an in-scope domain being omitted from status.
        game = replace(
            self.observation,
            discovery=replace(
                self.observation.discovery,
                executable=replace(
                    self.observation.discovery.executable, sha256="e" * 64
                ),
            ),
        )
        manager = replace(
            self.observation,
            projection=replace(
                self.observation.projection,
                observation_context=replace(
                    self.observation.projection.observation_context,
                    executable=replace(
                        self.observation.projection.observation_context.executable,
                        sha256="d" * 64,
                    ),
                ),
            ),
        )
        lab_mod = Mo2ProjectedMod("Added", "managed", "+", True, 0, 0)
        lab_state = replace(
            self.baseline_state,
            lab=replace(self.baseline_state.lab, mods=(lab_mod,)),
        )
        play_plugins = list(self.baseline_state.play.plugins)
        play_plugins[-1] = replace(play_plugins[-1], enabled=False)
        play_state = replace(
            self.baseline_state,
            play=replace(
                self.baseline_state.play,
                plugins=tuple(play_plugins),
                effective_load_order=tuple(
                    item.name for item in play_plugins if item.enabled
                ),
            ),
        )
        shared_state = replace(
            self.baseline_state, overwrite_entries=("Generated Output",)
        )
        changed_review = replace(
            self.review,
            selected=("foundation-core", "optional"),
            identity=RecipeIdentity.CUSTOM,
        )
        changed_target = replace(
            self.configuration,
            target_environment=replace(
                self.configuration.target_environment,
                dimensions=(
                    ("adapterVersion", "2.5.2"),
                    ("executableRuntime", "1.7.104"),
                    ("scriptExtender", "9.9.9"),
                ),
            ),
        )
        changed_coverage = replace(
            self.observation,
            projection=replace(
                self.observation.projection,
                coverage=replace(
                    self.observation.projection.coverage,
                    installed_payload_content="fingerprinted",
                ),
            ),
        )
        cases = {
            "game-identity": {"observation": game},
            "manager-identity": {"observation": manager},
            "lab-profile": {"observation": self.with_state(lab_state)},
            "play-profile": {"observation": self.with_state(play_state)},
            "shared-manager-state": {
                "observation": self.with_state(shared_state)
            },
            "recipe-intent": {"review": changed_review},
            "target-environment": {"configuration": changed_target},
            "coverage": {"observation": changed_coverage},
        }
        for domain, arguments in cases.items():
            with self.subTest(domain=domain):
                report = self.compare(**arguments)
                self.assertEqual(SkyrimStatusOutcome.DRIFTED, report.outcome)
                self.assertIn(
                    domain, tuple(item.name for item in report.domains)
                )

    def test_no_baseline_and_invalid_or_incomplete_inputs_have_precedence(self):
        # Catches missing evidence falling through to a misleading Matched result.
        no_baseline = self.compare(
            configuration=replace(
                self.configuration, baseline_checkpoint_id=None
            )
        )
        self.assertEqual(SkyrimStatusOutcome.NO_BASELINE, no_baseline.outcome)

        missing = compare_observed_baseline(
            self.configuration,
            None,
            None,
            self.observation,
            self.review,
        )
        self.assertEqual(SkyrimStatusOutcome.BLOCKED, missing.outcome)

        modified = self.compare(
            checkpoint=replace(self.checkpoint, adapter_state_json="{}")
        )
        self.assertEqual(SkyrimStatusOutcome.BLOCKED, modified.outcome)
        self.assertEqual((), modified.domains)

        incomplete = self.compare(
            observation=replace(self.observation, ready=False)
        )
        self.assertEqual(SkyrimStatusOutcome.BLOCKED, incomplete.outcome)

    def test_manager_containment_and_state_file_changes_are_explained(self):
        # Catches meaningful path/file drift being reduced to one changed hash.
        paths = list(
            self.observation.projection.observation_context.configured_paths
        )
        paths[0] = replace(paths[0], contained=False)
        context_drift = replace(
            self.observation,
            projection=replace(
                self.observation.projection,
                observation_context=replace(
                    self.observation.projection.observation_context,
                    configured_paths=tuple(paths),
                ),
            ),
        )
        manager = self.compare(observation=context_drift)
        manager_changes = next(
            item for item in manager.domains if item.name == "manager-identity"
        ).changes
        self.assertTrue(
            any("contained" in item.field for item in manager_changes)
        )

        files = list(self.baseline_state.lab.state_files)
        files[0] = replace(files[0], sha256="c" * 64)
        state = replace(
            self.baseline_state,
            lab=replace(self.baseline_state.lab, state_files=tuple(files)),
        )
        profile = self.compare(observation=self.with_state(state))
        changes = next(
            item for item in profile.domains if item.name == "lab-profile"
        ).changes
        self.assertTrue(any("stateFiles" in item.field for item in changes))

    def test_plugin_enablement_and_order_have_distinct_explanations(self):
        # Catches order and enablement collapsing into an unhelpful sequence hash.
        plugins = list(self.baseline_state.lab.plugins)
        plugins[0] = replace(plugins[0], enabled=False)
        enabled_state = replace(
            self.baseline_state,
            lab=replace(
                self.baseline_state.lab,
                plugins=tuple(plugins),
                effective_load_order=tuple(
                    item.name for item in plugins if item.enabled
                ),
            ),
        )
        enabled = self.compare(observation=self.with_state(enabled_state))
        enabled_fields = tuple(
            item.field
            for domain in enabled.domains
            if domain.name == "lab-profile"
            for item in domain.changes
        )
        self.assertIn("plugins.attributes", enabled_fields)

        reordered = list(self.baseline_state.lab.plugins)
        reordered[0], reordered[1] = reordered[1], reordered[0]
        reordered = [
            replace(item, load_order_index=index)
            for index, item in enumerate(reordered)
        ]
        ordered_state = replace(
            self.baseline_state,
            lab=replace(
                self.baseline_state.lab,
                plugins=tuple(reordered),
                effective_load_order=tuple(
                    item.name for item in reordered if item.enabled
                ),
            ),
        )
        ordered = self.compare(observation=self.with_state(ordered_state))
        ordered_fields = tuple(
            item.field
            for domain in ordered.domains
            if domain.name == "lab-profile"
            for item in domain.changes
        )
        self.assertIn("plugins.order", ordered_fields)

    def test_large_sequence_output_is_bounded_to_seven_entry_window(self):
        # Catches status dumping an unbounded mod list.
        mods = tuple(
            Mo2ProjectedMod(
                f"Mod-{index:02d}", "managed", "+", True, index, index
            )
            for index in range(20)
        )
        state = replace(
            self.baseline_state,
            lab=replace(self.baseline_state.lab, mods=mods),
        )

        report = self.compare(observation=self.with_state(state))

        membership = next(
            change
            for domain in report.domains
            if domain.name == "lab-profile"
            for change in domain.changes
            if change.field == "mods.membership"
        )
        self.assertLessEqual(len(membership.current_value), 9)
        self.assertIn("count=20", membership.current_value)

    def test_installed_meta_ini_change_is_shared_state_drift(self):
        # Catches metadata identity changes being hidden behind folder membership.
        old_meta = Mo2StateFileEvidence(
            "mods/SkyUI/meta.ini", "1" * 64, 10
        )
        baseline_state = replace(
            self.baseline_state,
            installed_mods=(Mo2InstalledModState("SkyUI", old_meta),),
        )
        evidence = replace(
            self.evidence,
            adapter_state_sha256=adapter_state_sha256(baseline_state),
        )
        evidence_json = json.dumps(
            baseline_evidence_to_dict(evidence, adapter_state=baseline_state),
            sort_keys=True,
            separators=(",", ":"),
        )
        draft_values = {
            field.name: getattr(self.checkpoint, field.name)
            for field in fields(CheckpointDraft)
        }
        draft_values.update(
            adapter_state_json=json.dumps(
                adapter_state_to_dict(baseline_state),
                sort_keys=True,
                separators=(",", ":"),
            ),
            evidence_json=evidence_json,
        )
        checkpoint = checkpoint_record_from_draft(CheckpointDraft(**draft_values))
        configuration = replace(
            self.configuration,
            baseline_checkpoint_id=checkpoint.checkpoint_id,
        )
        new_meta = replace(old_meta, sha256="2" * 64)
        current_state = replace(
            baseline_state,
            installed_mods=(Mo2InstalledModState("SkyUI", new_meta),),
        )

        report = self.compare(
            configuration=configuration,
            checkpoint=checkpoint,
            evidence=evidence,
            observation=self.with_state(current_state),
        )

        shared = next(
            item
            for item in report.domains
            if item.name == "shared-manager-state"
        )
        self.assertTrue(
            any("metaIni" in item.field for item in shared.changes)
        )

    def test_drift_values_retain_checkpoint_and_current_identity(self):
        # Catches explanations naming a field without the values needed to act.
        current_hash = "e" * 64
        observation = replace(
            self.observation,
            discovery=replace(
                self.observation.discovery,
                executable=replace(
                    self.observation.discovery.executable,
                    sha256=current_hash,
                ),
            ),
        )

        report = self.compare(observation=observation)

        change = next(
            item
            for domain in report.domains
            if domain.name == "game-identity"
            for item in domain.changes
            if item.field == "executable.sha256"
        )
        self.assertEqual(
            self.evidence.game_discovery.executable.sha256,
            change.checkpoint_value,
        )
        self.assertEqual(current_hash, change.current_value)


if __name__ == "__main__":
    unittest.main()
