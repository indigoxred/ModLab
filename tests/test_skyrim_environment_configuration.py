import copy
import json
import unittest
from dataclasses import replace

from modlab.recipes.model import RecipeIdentity, RecipeMaturity
from modlab.workflows.skyrim.configuration import (
    ManagerRegistration,
    RecipeIntent,
    SkyrimConfigurationFormatError,
    SkyrimEnvironmentConfiguration,
    StoredSourceReference,
    TargetEnvironmentIntent,
    configuration_bytes,
    configuration_changes,
    configuration_from_bytes,
    configuration_from_dict,
    configuration_to_dict,
)


def valid_configuration() -> SkyrimEnvironmentConfiguration:
    return SkyrimEnvironmentConfiguration(
        schema_version=1,
        game_key="skyrim-se-ae",
        environment_id="skyrim.steam.current",
        lineage_id="skyrim-main",
        steam_root=r"C:\Games\Steam",
        game_root=r"C:\Games\Steam\steamapps\common\Skyrim Special Edition",
        manager=ManagerRegistration(
            adapter_id="portable-mo2-skyrim",
            root="tools/mo2/skyrim-se-ae/app",
        ),
        recipe=RecipeIntent(
            recipe_id="skyrim.foundation",
            revision="2026.08.31.1",
            maturity=RecipeMaturity.DRAFT,
            identity=RecipeIdentity.ORIGINAL,
            source=StoredSourceReference(
                source_sha256="a" * 64,
                stored_path=(
                    "games/skyrim-se-ae/recipes/"
                    + "a" * 64
                    + "/recipe.json"
                ),
            ),
            selected=("address-library", "core"),
            omitted=("optional-ui",),
        ),
        target_environment=TargetEnvironmentIntent(
            environment_id="skyrim.steam.current",
            source=StoredSourceReference(
                source_sha256="b" * 64,
                stored_path=(
                    "games/skyrim-se-ae/target-environments/"
                    + "b" * 64
                    + "/environment.json"
                ),
            ),
            dimensions=(
                ("adapterVersion", "2.5.2"),
                ("executableRuntime", "1.7.104"),
            ),
        ),
        baseline_checkpoint_id=None,
    )


class SkyrimEnvironmentConfigurationTests(unittest.TestCase):
    def test_configuration_round_trip_is_canonical(self):
        # Catches a serializer/parser disagreement or unstable canonical bytes.
        configuration = valid_configuration()

        restored = configuration_from_dict(configuration_to_dict(configuration))

        self.assertEqual(configuration, restored)
        self.assertEqual(
            configuration_bytes(configuration),
            configuration_bytes(restored),
        )
        self.assertTrue(configuration_bytes(restored).endswith(b"\n"))
        self.assertFalse(configuration_bytes(restored).endswith(b"\n\n"))

    def test_rejects_unknown_fields_unsafe_relative_paths_and_save_paths(self):
        # Catches permissive config parsing and paths escaping or touching saves.
        value = configuration_to_dict(valid_configuration())
        cases: dict[str, dict[str, object]] = {
            "unknown root field": {**value, "unexpected": True},
        }
        escaped = copy.deepcopy(value)
        escaped["manager"]["root"] = "../outside"
        cases["parent traversal"] = escaped
        absolute = copy.deepcopy(value)
        absolute["recipe"]["storedPath"] = "C:/outside/recipe.json"
        cases["absolute stored path"] = absolute
        backslash = copy.deepcopy(value)
        backslash["targetEnvironment"]["storedPath"] = (
            "games\\skyrim-se-ae\\environment.json"
        )
        cases["backslash stored path"] = backslash
        saves = copy.deepcopy(value)
        saves["recipe"]["storedPath"] = (
            "games/skyrim-se-ae/saves/recipe.json"
        )
        cases["saves segment"] = saves
        ess = copy.deepcopy(value)
        ess["recipe"]["storedPath"] = "games/skyrim-se-ae/recipe.ess"
        cases["save extension"] = ess
        cosave = copy.deepcopy(value)
        cosave["targetEnvironment"]["storedPath"] = (
            "games/skyrim-se-ae/environment.skse"
        )
        cases["co-save extension"] = cosave

        for label, case in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(SkyrimConfigurationFormatError):
                    configuration_from_dict(case)

    def test_rejects_wrong_fixed_values_and_nested_fields(self):
        # Catches loading a different game, adapter, schema, or shape as Skyrim.
        base = configuration_to_dict(valid_configuration())
        cases: dict[str, dict[str, object]] = {}
        for label, path, replacement in (
            ("schema", ("schemaVersion",), 2),
            ("game", ("gameKey",), "morrowind"),
            ("adapter", ("manager", "adapterId"), "vortex"),
            ("manager root", ("manager", "root"), "tools/mo2/other/app"),
        ):
            value = copy.deepcopy(base)
            cursor = value
            for key in path[:-1]:
                cursor = cursor[key]
            cursor[path[-1]] = replacement
            cases[label] = value
        nested_extra = copy.deepcopy(base)
        nested_extra["recipe"]["unexpected"] = True
        cases["nested extra"] = nested_extra

        for label, case in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(SkyrimConfigurationFormatError):
                    configuration_from_dict(case)

    def test_baseline_id_is_null_or_exact_checkpoint_identity(self):
        # Catches an alias such as "latest" becoming an ambiguous pointer.
        value = configuration_to_dict(valid_configuration())
        value["baselineCheckpointId"] = "checkpoint-sha256:" + "a" * 64
        self.assertIsNotNone(
            configuration_from_dict(value).baseline_checkpoint_id
        )
        value["baselineCheckpointId"] = "latest"
        with self.assertRaisesRegex(
            SkyrimConfigurationFormatError, "baselineCheckpointId"
        ):
            configuration_from_dict(value)

    def test_rejects_malformed_hashes_unsafe_ids_and_incomplete_recipe(self):
        # Catches weak identities entering later checkpoint validation.
        base = configuration_to_dict(valid_configuration())
        cases: dict[str, dict[str, object]] = {}
        malformed_hash = copy.deepcopy(base)
        malformed_hash["recipe"]["sourceSha256"] = "A" * 64
        cases["malformed hash"] = malformed_hash
        unsafe_id = copy.deepcopy(base)
        unsafe_id["environmentId"] = "unsafe environment"
        cases["unsafe environment id"] = unsafe_id
        unsafe_component = copy.deepcopy(base)
        unsafe_component["recipe"]["selected"] = ["bad/component"]
        cases["unsafe component id"] = unsafe_component
        incomplete = copy.deepcopy(base)
        incomplete["recipe"]["identity"] = "Incomplete"
        cases["incomplete recipe"] = incomplete

        for label, case in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(SkyrimConfigurationFormatError):
                    configuration_from_dict(case)

    def test_rejects_source_path_identity_mismatch(self):
        # Catches a retained hash being paired with some other content address.
        value = configuration_to_dict(valid_configuration())
        value["recipe"]["storedPath"] = (
            "games/skyrim-se-ae/recipes/" + "c" * 64 + "/recipe.json"
        )

        with self.assertRaisesRegex(
            SkyrimConfigurationFormatError, "recipe.storedPath"
        ):
            configuration_from_dict(value)

    def test_rejects_model_with_inconsistent_target_environment_id(self):
        # Catches serialization silently discarding contradictory model intent.
        configuration = valid_configuration()
        inconsistent = replace(
            configuration,
            target_environment=replace(
                configuration.target_environment,
                environment_id="skyrim.other",
            ),
        )

        with self.assertRaisesRegex(
            SkyrimConfigurationFormatError, "environmentId"
        ):
            configuration_to_dict(inconsistent)

    def test_rejects_unsorted_duplicate_or_overlapping_collections(self):
        # Catches noncanonical or contradictory retained intent.
        base = configuration_to_dict(valid_configuration())
        cases: dict[str, dict[str, object]] = {}
        unsorted = copy.deepcopy(base)
        unsorted["recipe"]["selected"] = ["core", "address-library"]
        cases["unsorted selection"] = unsorted
        duplicate = copy.deepcopy(base)
        duplicate["recipe"]["selected"] = ["core", "core"]
        cases["duplicate selection"] = duplicate
        overlap = copy.deepcopy(base)
        overlap["recipe"]["omitted"] = ["core"]
        cases["overlap"] = overlap
        unsorted_dimensions = copy.deepcopy(base)
        unsorted_dimensions["targetEnvironment"]["dimensions"] = {
            "executableRuntime": "1.7.104",
            "adapterVersion": "2.5.2",
        }
        cases["unsorted dimensions"] = unsorted_dimensions

        for label, case in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(SkyrimConfigurationFormatError):
                    configuration_from_dict(case)

    def test_rejects_duplicate_json_keys(self):
        # Catches parser ambiguity before logical validation.
        data = configuration_bytes(valid_configuration()).replace(
            b'{"baselineCheckpointId":null,',
            b'{"baselineCheckpointId":null,"baselineCheckpointId":null,',
            1,
        )

        with self.assertRaisesRegex(
            SkyrimConfigurationFormatError, "duplicate JSON key"
        ):
            configuration_from_bytes(data)

    def test_configuration_changes_names_every_changed_logical_field(self):
        # Catches replacement previews omitting a changed user decision.
        before = valid_configuration()
        after = replace(
            before,
            steam_root=r"D:\Steam",
            recipe=replace(before.recipe, selected=("core", "skyui")),
        )

        changes = configuration_changes(before, after)

        self.assertEqual(
            ("recipe.selected", "steamRoot"),
            tuple(item.field for item in changes),
        )

    def test_configuration_changes_bound_large_collections(self):
        # Catches a replacement preview dumping an unbounded mod selection.
        before = replace(
            valid_configuration(),
            recipe=replace(
                valid_configuration().recipe,
                selected=tuple(f"component-{index:02d}" for index in range(20)),
            ),
        )
        after = replace(
            before,
            recipe=replace(
                before.recipe,
                selected=tuple(
                    [
                        *(f"component-{index:02d}" for index in range(10)),
                        "component-10a",
                    ]
                    + [f"component-{index:02d}" for index in range(11, 20)]
                ),
            ),
        )

        change = configuration_changes(before, after)[0]

        self.assertEqual("recipe.selected", change.field)
        self.assertLessEqual(len(change.before), 9)
        self.assertLessEqual(len(change.after), 9)
        self.assertIn("count=20", change.before)
        self.assertIn("firstDifference=10", change.after)


if __name__ == "__main__":
    unittest.main()
