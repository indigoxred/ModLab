import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from modlab.recipes.loading import (
    RecipeFormatError,
    load_environment_source,
    load_recipe_source,
)


def valid_recipe() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "recipeId": "skyrim.test",
        "revision": "2026.08.31.1",
        "displayName": "Skyrim Test Foundation",
        "maturity": "Draft",
        "target": {
            "game": "Skyrim Special Edition",
            "edition": "SE/AE",
            "distribution": "Steam",
            "engineLane": "Creation Engine 64-bit",
            "adapter": "MO2",
        },
        "researchedAt": "2026-08-31",
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
                        "allowedValues": ["1.7.104"],
                        "reason": "The native build targets this runtime.",
                    }
                ],
                "rationale": "Provides the test runtime integration.",
                "sources": ["https://example.invalid/core"],
            }
        ],
    }


def valid_environment() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "environmentId": "skyrim.test",
        "dimensions": {"executableRuntime": "1.7.104"},
    }


class RecipeSourceTests(unittest.TestCase):
    def test_loads_exact_bytes_and_sha256(self):
        # Catches decoding/re-encoding the document before hashing or retention.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "recipe.json")
            data = json.dumps(valid_recipe(), indent=2).encode("utf-8")
            path.write_bytes(data)

            source = load_recipe_source(path)

            self.assertEqual(path, source.path)
            self.assertEqual(data, source.data)
            self.assertEqual(hashlib.sha256(data).hexdigest(), source.sha256)
            self.assertEqual("skyrim.test", source.recipe.recipe_id)

    def test_rejects_duplicate_and_unknown_recipe_fields(self):
        # Catches ambiguous duplicate keys and silently ignored root fields.
        with tempfile.TemporaryDirectory() as directory:
            duplicate = Path(directory, "duplicate.json")
            duplicate.write_text(
                '{"schemaVersion":1,"schemaVersion":1}', encoding="utf-8"
            )
            with self.assertRaisesRegex(RecipeFormatError, "duplicate JSON key"):
                load_recipe_source(duplicate)

            value = valid_recipe()
            value["unexpected"] = True
            unknown = Path(directory, "unknown.json")
            unknown.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(RecipeFormatError, "fields differ"):
                load_recipe_source(unknown)

    def test_rejects_unknown_nested_recipe_fields(self):
        # Each case catches one schema layer accidentally becoming permissive.
        cases = {
            "target": lambda value: value["target"].__setitem__("unexpected", True),
            "component": lambda value: value["components"][0].__setitem__(
                "unexpected", True
            ),
            "constraint": lambda value: value["components"][0]["constraints"][
                0
            ].__setitem__("unexpected", True),
            "misspelled optional component field": lambda value: value[
                "components"
            ][0].__setitem__("archiveSHA256", "a" * 64),
        }
        with tempfile.TemporaryDirectory() as directory:
            for label, mutate in cases.items():
                with self.subTest(label=label):
                    value = copy.deepcopy(valid_recipe())
                    mutate(value)
                    path = Path(directory, f"{label.replace(' ', '-')}.json")
                    path.write_text(json.dumps(value), encoding="utf-8")

                    with self.assertRaisesRegex(RecipeFormatError, "fields differ"):
                        load_recipe_source(path)

    def test_optional_component_identity_fields_remain_optional(self):
        # Catches strict field checking accidentally making identity pins mandatory.
        with tempfile.TemporaryDirectory() as directory:
            unpinned_path = Path(directory, "unpinned.json")
            unpinned_path.write_text(json.dumps(valid_recipe()), encoding="utf-8")

            pinned = valid_recipe()
            component = pinned["components"][0]
            component["version"] = "2.3.1"
            component["archiveSha256"] = "a" * 64
            component["installedTreeSha256"] = "b" * 64
            pinned_path = Path(directory, "pinned.json")
            pinned_path.write_text(json.dumps(pinned), encoding="utf-8")

            unpinned_source = load_recipe_source(unpinned_path)
            pinned_source = load_recipe_source(pinned_path)

            self.assertIsNone(unpinned_source.recipe.components[0].version)
            self.assertEqual("2.3.1", pinned_source.recipe.components[0].version)
            self.assertEqual(
                "a" * 64, pinned_source.recipe.components[0].archive_sha256
            )
            self.assertEqual(
                "b" * 64, pinned_source.recipe.components[0].installed_tree_sha256
            )

    def test_environment_source_is_strict_and_byte_identified(self):
        # Catches environment bytes being normalized or extra intent being ignored.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "environment.json")
            data = (
                b'{"schemaVersion":1,"environmentId":"skyrim.test",'
                b'"dimensions":{"executableRuntime":"1.7.104"}}'
            )
            path.write_bytes(data)

            source = load_environment_source(path)

            self.assertEqual(path, source.path)
            self.assertEqual(data, source.data)
            self.assertEqual(hashlib.sha256(data).hexdigest(), source.sha256)
            self.assertEqual("skyrim.test", source.environment.environment_id)

            unknown = valid_environment()
            unknown["unexpected"] = True
            unknown_path = Path(directory, "environment-unknown.json")
            unknown_path.write_text(json.dumps(unknown), encoding="utf-8")
            with self.assertRaisesRegex(RecipeFormatError, "fields differ"):
                load_environment_source(unknown_path)

    def test_rejects_non_regular_sources(self):
        # Catches trying to read a directory as a selected source file.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(RecipeFormatError, "regular non-redirected"):
                load_recipe_source(root)

    def test_rejects_redirected_sources(self):
        # Catches following a link outside the file the user selected.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.json"
            target.write_text(json.dumps(valid_recipe()), encoding="utf-8")
            link = root / "link.json"
            try:
                os.symlink(target, link)
            except OSError as error:
                self.skipTest(f"symbolic links unavailable: {error}")
            with self.assertRaisesRegex(RecipeFormatError, "regular non-redirected"):
                load_recipe_source(link)


if __name__ == "__main__":
    unittest.main()
