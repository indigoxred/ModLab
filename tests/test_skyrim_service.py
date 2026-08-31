import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from modlab.workflows.skyrim.drift import SkyrimStatusOutcome
from modlab.workflows.skyrim.service import (
    SkyrimConfigurationConflictError,
    SkyrimWorkflowError,
    configure_skyrim_environment,
    create_skyrim_baseline,
    get_skyrim_status,
    use_skyrim_baseline,
)
from modlab.workflows.skyrim.store import (
    SkyrimEnvironmentConflictError,
    SkyrimEnvironmentStore,
)
from tests.support.skyrim_workflow import create_skyrim_workflow_fixture


FIXED_TIME = datetime(2026, 8, 31, 3, 0, 0, tzinfo=timezone.utc)


def tree_state(root: Path) -> tuple[tuple[str, str, bytes | None], ...]:
    return tuple(
        (
            path.relative_to(root).as_posix(),
            "directory" if path.is_dir() else "file",
            None if path.is_dir() else path.read_bytes(),
        )
        for path in sorted(root.rglob("*"))
    )


class SkyrimServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.fixture = create_skyrim_workflow_fixture(self.root)

    def configure(self, **overrides):
        arguments = {
            "steam_root": self.fixture.steam_root,
            "recipe_path": self.fixture.recipe_path,
            "target_environment_path": self.fixture.environment_path,
            "workspace_root": self.fixture.workspace,
            "select": (),
            "omit": (),
            "replace_existing": False,
            "skyrim_version_reader": lambda _: "1.7.104.0",
            "mo2_version_reader": lambda _: "2.5.2.0",
        }
        arguments.update(overrides)
        return configure_skyrim_environment(**arguments)

    def capture(self):
        return create_skyrim_baseline(
            self.fixture.workspace,
            clock=lambda: FIXED_TIME,
            skyrim_version_reader=lambda _: "1.7.104.0",
            mo2_version_reader=lambda _: "2.5.2.0",
        )

    def status(self):
        return get_skyrim_status(
            self.fixture.workspace,
            skyrim_version_reader=lambda _: "1.7.104.0",
            mo2_version_reader=lambda _: "2.5.2.0",
        )

    def test_configure_retains_sources_and_writes_one_valid_registration(self):
        # Catches configuration reporting success before all three files exist.
        result = self.configure()

        self.assertTrue(result.changed)
        self.assertTrue(result.observation.ready)
        self.assertTrue(result.recipe_review.ready_for_approval)
        self.assertIsNone(result.configuration.baseline_checkpoint_id)
        self.assertEqual(3, len(result.actions_performed))
        self.assertTrue(all(Path(path).is_file() for path in result.actions_performed))
        self.assertEqual(result.configuration, SkyrimEnvironmentStore(
            self.fixture.workspace
        ).load().configuration)

    def test_identical_configuration_is_idempotent(self):
        # Catches needless source or configuration rewrites.
        first = self.configure()
        before = tree_state(self.fixture.workspace)

        second = self.configure()

        self.assertFalse(second.changed)
        self.assertEqual((), second.actions_performed)
        self.assertEqual(first.configuration, second.configuration)
        self.assertEqual(before, tree_state(self.fixture.workspace))

    def test_changed_configuration_requires_replace_and_lists_exact_fields(self):
        # Catches source retention occurring before the user authorizes replacement.
        self.configure()
        alternate = self.root / "alternate-environment.json"
        value = json.loads(self.fixture.environment_path.read_text(encoding="utf-8"))
        alternate.write_text(json.dumps(value), encoding="utf-8")
        alternate_hash = hashlib.sha256(alternate.read_bytes()).hexdigest()
        target = (
            self.fixture.workspace
            / "games"
            / "skyrim-se-ae"
            / "target-environments"
            / alternate_hash
            / "environment.json"
        )

        with self.assertRaises(SkyrimConfigurationConflictError) as raised:
            self.configure(target_environment_path=alternate)

        fields = tuple(item.field for item in raised.exception.changes)
        self.assertEqual(
            (
                "targetEnvironment.sourceSha256",
                "targetEnvironment.storedPath",
            ),
            fields,
        )
        self.assertFalse(target.exists())

        replaced = self.configure(
            target_environment_path=alternate, replace_existing=True
        )
        self.assertTrue(replaced.changed)
        self.assertEqual(2, len(replaced.actions_performed))
        self.assertTrue(target.is_file())

    def test_changed_recipe_clears_old_baseline_but_identical_intent_preserves_it(self):
        # Catches stale baselines surviving changed recipe intent.
        self.configure()
        captured = self.capture()

        identical = self.configure()
        self.assertEqual(
            captured.checkpoint.checkpoint_id,
            identical.configuration.baseline_checkpoint_id,
        )

        changed_recipe = self.root / "changed-recipe.json"
        value = json.loads(self.fixture.recipe_path.read_text(encoding="utf-8"))
        value["revision"] = "2026.08.31.2"
        changed_recipe.write_text(json.dumps(value), encoding="utf-8")
        replaced = self.configure(
            recipe_path=changed_recipe, replace_existing=True
        )

        self.assertIsNone(replaced.configuration.baseline_checkpoint_id)
        self.assertTrue(replaced.changed)

    def test_selected_and_omitted_intent_pass_through_review(self):
        # Catches service arguments being dropped before recipe review.
        optional_recipe = self.root / "optional-recipe.json"
        value = json.loads(self.fixture.recipe_path.read_text(encoding="utf-8"))
        optional = copy.deepcopy(value["components"][0])
        optional.update(
            componentId="optional-ui",
            displayName="Optional UI",
            importance="Optional",
            defaultSelected=False,
            constraints=[],
            rationale="Optional fixture component.",
        )
        value["components"].append(optional)
        optional_recipe.write_text(json.dumps(value), encoding="utf-8")

        result = self.configure(
            recipe_path=optional_recipe, select=("optional-ui",)
        )

        self.assertEqual(
            ("foundation-core", "optional-ui"), result.recipe_review.selected
        )
        self.assertEqual((), result.recipe_review.omitted)

    def test_incomplete_review_and_target_mismatch_write_nothing(self):
        # Catches rejected intent leaving partial registration files.
        with self.assertRaises(SkyrimWorkflowError):
            self.configure(omit=("foundation-core",))
        configuration = (
            self.fixture.workspace
            / "games"
            / "skyrim-se-ae"
            / "environment.json"
        )
        self.assertFalse(configuration.exists())

        mismatched = self.root / "mismatched-environment.json"
        value = json.loads(self.fixture.environment_path.read_text(encoding="utf-8"))
        value["dimensions"]["adapterVersion"] = "2.4.0"
        mismatched.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(SkyrimWorkflowError):
            self.configure(target_environment_path=mismatched)
        self.assertFalse(configuration.exists())

    def test_blocked_observation_does_not_create_absent_workspace(self):
        # Catches validation calling workspace initialization on a read-only path.
        absent = self.root / "absent-workspace"

        with self.assertRaises(SkyrimWorkflowError):
            self.configure(workspace_root=absent)

        self.assertFalse(absent.exists())

    def test_source_mutation_between_load_and_retention_is_blocked(self):
        # Catches loaded bytes changing before immutable retention.
        original = SkyrimEnvironmentStore.retain_recipe

        def mutate_then_retain(store, source):
            source.path.write_bytes(source.data + b"\n")
            return original(store, source)

        with patch.object(
            SkyrimEnvironmentStore,
            "retain_recipe",
            autospec=True,
            side_effect=mutate_then_retain,
        ):
            with self.assertRaises(SkyrimWorkflowError):
                self.configure()

        self.assertFalse(
            (
                self.fixture.workspace
                / "games"
                / "skyrim-se-ae"
                / "environment.json"
            ).exists()
        )

    def test_full_service_flow_configures_captures_and_matches(self):
        # Catches subsystem contracts failing when orchestrated end to end.
        self.configure()
        capture = self.capture()

        status = self.status()

        self.assertTrue(capture.selected)
        self.assertEqual(SkyrimStatusOutcome.MATCHED, status.outcome)

    def test_status_without_baseline_and_with_profile_drift(self):
        # Catches status conflating a missing pointer with drift.
        self.configure()
        self.assertEqual(SkyrimStatusOutcome.NO_BASELINE, self.status().outcome)
        self.capture()
        profile = (
            self.fixture.workspace
            / "tools"
            / "mo2"
            / "skyrim-se-ae"
            / "profiles"
            / "ModLab - Lab"
            / "archives.txt"
        )
        profile.write_text("changed archive\n", encoding="utf-8")

        report = self.status()

        self.assertEqual(SkyrimStatusOutcome.DRIFTED, report.outcome)
        self.assertIn("lab-profile", tuple(item.name for item in report.domains))

    def test_status_blocks_for_changed_source_missing_checkpoint_or_current_identity(self):
        # Catches damaged prerequisites falling through to Matched.
        self.configure()
        capture = self.capture()
        recipe = (
            self.fixture.workspace
            / capture.configuration.recipe.source.stored_path
        )
        recipe.write_bytes(recipe.read_bytes() + b"\n")
        self.assertEqual(SkyrimStatusOutcome.BLOCKED, self.status().outcome)

        recipe.write_bytes(recipe.read_bytes().rstrip() + b"\n")
        checkpoint = (
            self.fixture.workspace
            / "games"
            / "skyrim-se-ae"
            / "checkpoints"
            / capture.checkpoint.checkpoint_id.removeprefix("checkpoint-sha256:")
            / "modlab.lock.json"
        )
        checkpoint.unlink()
        self.assertEqual(SkyrimStatusOutcome.BLOCKED, self.status().outcome)

        # Restore by reconfiguring/capturing in a second independent fixture.
        second = create_skyrim_workflow_fixture(self.root / "second")
        self.fixture = second
        self.configure()
        self.capture()
        (self.fixture.game_root / "SkyrimSE.exe").unlink()
        self.assertEqual(SkyrimStatusOutcome.BLOCKED, self.status().outcome)

    def test_valid_orphan_can_be_used_through_service(self):
        # Catches recoverable checkpoint selection being inaccessible to the user.
        self.configure()
        with patch.object(
            SkyrimEnvironmentStore,
            "compare_and_swap",
            side_effect=SkyrimEnvironmentConflictError("changed"),
        ):
            orphan = self.capture()
        self.assertFalse(orphan.selected)

        used = use_skyrim_baseline(
            self.fixture.workspace, orphan.checkpoint.checkpoint_id
        )

        self.assertTrue(used.changed)
        self.assertEqual(orphan.checkpoint.checkpoint_id, used.checkpoint_id)
        self.assertEqual(1, len(used.actions_performed))

    def test_status_is_filesystem_read_only(self):
        # Catches status touching jobs, profiles, checkpoint, or source files.
        self.configure()
        self.capture()
        before = tree_state(self.fixture.root)

        report = self.status()

        self.assertEqual(SkyrimStatusOutcome.MATCHED, report.outcome)
        self.assertEqual(before, tree_state(self.fixture.root))


if __name__ == "__main__":
    unittest.main()
