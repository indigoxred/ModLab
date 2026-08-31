import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from modlab.recipes.loading import (
    LoadedRecipeSource,
    load_environment_source,
    load_recipe_source,
)
from modlab.workflows.skyrim.store import (
    SkyrimEnvironmentConflictError,
    SkyrimEnvironmentNotConfiguredError,
    SkyrimEnvironmentStore,
    SkyrimEnvironmentStoreError,
)
from tests.test_recipe_sources import valid_environment, valid_recipe
from tests.test_skyrim_environment_configuration import valid_configuration


CHECKPOINT_ID = "checkpoint-sha256:" + "c" * 64


def loaded_recipe_source(root: Path) -> LoadedRecipeSource:
    path = root / "source-recipe.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(valid_recipe(), indent=2), encoding="utf-8")
    return load_recipe_source(path)


class SkyrimEnvironmentStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.workspace = self.root / "workspace"
        self.store = SkyrimEnvironmentStore(self.workspace)

    def test_load_reports_not_configured_without_creating_workspace(self):
        # Catches a read path mutating the workspace or conflating absence with damage.
        with self.assertRaises(SkyrimEnvironmentNotConfiguredError):
            self.store.load()
        self.assertFalse(self.workspace.exists())

    def test_retains_exact_recipe_bytes_content_addressed(self):
        # Catches source normalization, movement, or non-content-addressed storage.
        source = loaded_recipe_source(self.root)

        stored = self.store.retain_recipe(source)

        target = self.workspace / stored.reference.stored_path
        self.assertEqual(source.data, target.read_bytes())
        self.assertEqual(source.sha256, stored.reference.source_sha256)
        self.assertEqual(target, stored.path)
        self.assertTrue(target.resolve().is_relative_to(self.workspace.resolve()))
        self.assertTrue(stored.changed)

        duplicate = self.store.retain_recipe(source)
        self.assertFalse(duplicate.changed)

    def test_retains_target_environment_under_its_own_content_address(self):
        # Catches recipe and target intent being mixed in one retention namespace.
        path = self.root / "target-environment.json"
        path.write_text(json.dumps(valid_environment()), encoding="utf-8")
        source = load_environment_source(path)

        retained = self.store.retain_target_environment(source)

        self.assertEqual(source.data, retained.path.read_bytes())
        self.assertIn("target-environments", retained.reference.stored_path)
        self.assertTrue(retained.changed)

    def test_identical_configuration_is_idempotent_but_change_requires_replace(self):
        # Catches silent intent replacement and redundant rewrites.
        configuration = valid_configuration()

        first = self.store.write(configuration, replace=False)
        second = self.store.write(configuration, replace=False)

        self.assertTrue(first.changed)
        self.assertFalse(second.changed)
        self.assertEqual((), second.paths_written)
        changed = replace(configuration, steam_root=r"D:\Steam")
        with self.assertRaises(SkyrimEnvironmentConflictError) as raised:
            self.store.write(changed, replace=False)
        self.assertEqual(
            ("steamRoot",), tuple(item.field for item in raised.exception.changes)
        )
        self.assertEqual(r"C:\Games\Steam", raised.exception.changes[0].before)
        self.assertEqual(r"D:\Steam", raised.exception.changes[0].after)

        replaced = self.store.write(changed, replace=True)
        self.assertTrue(replaced.changed)
        self.assertEqual(changed, replaced.configuration)
        self.assertEqual((replaced.snapshot.path,), replaced.paths_written)

    def test_invalid_existing_configuration_is_never_overwritten(self):
        # Catches --replace being treated as permission to repair unknown bytes.
        target = self.workspace / "games" / "skyrim-se-ae" / "environment.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        original = b'{"unknown":true}\n'
        target.write_bytes(original)

        with self.assertRaises(SkyrimEnvironmentStoreError):
            self.store.write(valid_configuration(), replace=True)

        self.assertEqual(original, target.read_bytes())

    def test_compare_and_swap_refuses_intervening_change(self):
        # Catches baseline selection overwriting a newer configuration decision.
        snapshot = self.store.write(
            valid_configuration(), replace=False
        ).snapshot
        intervening = replace(snapshot.configuration, lineage_id="other-lineage")
        self.store.write(intervening, replace=True)
        desired = replace(
            snapshot.configuration, baseline_checkpoint_id=CHECKPOINT_ID
        )

        with self.assertRaisesRegex(SkyrimEnvironmentConflictError, "changed"):
            self.store.compare_and_swap(snapshot.sha256, desired)

        self.assertEqual(intervening, self.store.load().configuration)

    def test_compare_and_swap_selects_from_exact_unchanged_bytes(self):
        # Catches CAS comparing only parsed values rather than the observed bytes.
        snapshot = self.store.write(
            valid_configuration(), replace=False
        ).snapshot
        desired = replace(
            snapshot.configuration, baseline_checkpoint_id=CHECKPOINT_ID
        )

        written = self.store.compare_and_swap(snapshot.sha256, desired)

        self.assertEqual(CHECKPOINT_ID, written.configuration.baseline_checkpoint_id)
        self.assertTrue(written.changed)

    def test_mismatched_content_at_source_target_blocks_retention(self):
        # Catches overwriting bytes already occupying a content-addressed identity.
        source = loaded_recipe_source(self.root)
        target = (
            self.workspace
            / "games"
            / "skyrim-se-ae"
            / "recipes"
            / source.sha256
            / "recipe.json"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"different bytes")

        with self.assertRaisesRegex(SkyrimEnvironmentStoreError, "different bytes"):
            self.store.retain_recipe(source)

        self.assertEqual(b"different bytes", target.read_bytes())

    def test_rejects_redirected_source_paths(self):
        # Catches following a source junction/symlink during a write.
        source = loaded_recipe_source(self.root)
        source_link = self.root / "source-link.json"
        try:
            os.symlink(source.path, source_link)
        except OSError as error:
            self.skipTest(f"symbolic links unavailable: {error}")

        redirected_source = LoadedRecipeSource(
            path=source_link,
            data=source.data,
            sha256=source.sha256,
            recipe=source.recipe,
        )
        with self.assertRaisesRegex(SkyrimEnvironmentStoreError, "redirected"):
            self.store.retain_recipe(redirected_source)

    def test_rejects_redirected_target_paths(self):
        # Catches following a workspace junction/symlink during a write.
        source = loaded_recipe_source(self.root)
        target_directory = self.root / "redirect-target"
        target_directory.mkdir()
        target_parent = (
            self.workspace
            / "games"
            / "skyrim-se-ae"
            / "recipes"
            / source.sha256
        )
        target_parent.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(target_directory, target_parent, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"symbolic links unavailable: {error}")
        with self.assertRaisesRegex(SkyrimEnvironmentStoreError, "redirected"):
            self.store.retain_recipe(source)

    def test_rejects_non_regular_source_and_target_files(self):
        # Catches treating directories as immutable source files.
        source = loaded_recipe_source(self.root)
        directory_source = LoadedRecipeSource(
            path=self.root,
            data=source.data,
            sha256=source.sha256,
            recipe=source.recipe,
        )
        with self.assertRaisesRegex(SkyrimEnvironmentStoreError, "regular file"):
            self.store.retain_recipe(directory_source)

        target = (
            self.workspace
            / "games"
            / "skyrim-se-ae"
            / "recipes"
            / source.sha256
            / "recipe.json"
        )
        target.mkdir(parents=True)
        with self.assertRaisesRegex(SkyrimEnvironmentStoreError, "regular file"):
            self.store.retain_recipe(source)

    def test_atomic_write_uses_workspace_jobs_and_cleans_failed_staging(self):
        # Catches cross-volume promotion or leaked partial configuration files.
        observed: dict[str, Path] = {}
        real_replace = os.replace

        def recording_replace(source, target):
            observed["source"] = Path(source)
            observed["target"] = Path(target)
            real_replace(source, target)

        with patch(
            "modlab.workflows.skyrim.store.os.replace",
            side_effect=recording_replace,
        ):
            self.store.write(valid_configuration(), replace=False)

        self.assertEqual(
            self.workspace.resolve() / "runtime" / "jobs",
            observed["source"].parent,
        )
        self.assertEqual(observed["source"].drive, observed["target"].drive)

        other_workspace = self.root / "failed-workspace"
        failing_store = SkyrimEnvironmentStore(other_workspace)
        with patch(
            "modlab.workflows.skyrim.store.os.replace",
            side_effect=OSError("promotion failed"),
        ):
            with self.assertRaisesRegex(
                SkyrimEnvironmentStoreError, "promotion failed"
            ):
                failing_store.write(valid_configuration(), replace=False)

        self.assertFalse(
            (
                other_workspace
                / "games"
                / "skyrim-se-ae"
                / "environment.json"
            ).exists()
        )
        self.assertEqual(
            [], list((other_workspace / "runtime" / "jobs").glob("*.part"))
        )

    def test_post_write_re_read_is_strict(self):
        # Catches reporting success without validating the bytes that landed.
        real_replace = os.replace

        def replace_then_corrupt(source, target):
            real_replace(source, target)
            Path(target).write_bytes(b"{}")

        with patch(
            "modlab.workflows.skyrim.store.os.replace",
            side_effect=replace_then_corrupt,
        ):
            with self.assertRaises(SkyrimEnvironmentStoreError):
                self.store.write(valid_configuration(), replace=False)
        self.assertEqual(
            [], list((self.workspace / "runtime" / "jobs").glob("*.part"))
        )


if __name__ == "__main__":
    unittest.main()
