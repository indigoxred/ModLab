import copy
import json
from dataclasses import replace
import unittest

from modlab.adapters.mo2.comparison import compare_mo2_profiles
from modlab.adapters.mo2.comparison_serialization import (
    Mo2ComparisonFormatError,
    comparison_result_from_dict,
    comparison_result_from_json,
    comparison_result_to_dict,
    comparison_result_to_json,
    comparison_result_to_text,
)
from modlab.adapters.mo2.model import (
    Mo2ExecutableEvidence,
    Mo2PathEvidence,
    Mo2StateFileEvidence,
)
from modlab.adapters.mo2.projection import (
    Mo2InstalledModState,
    adapter_state_sha256,
)
from tests.test_mo2_comparison import make_projection


def _state_file(profile: str, filename: str, identity: str) -> Mo2StateFileEvidence:
    return Mo2StateFileEvidence(
        f"profiles/{profile}/{filename}", identity * 64, 10
    )


def make_comparison_report():
    projection = make_projection(
        play_mods=("A", "B", "C"),
        lab_mods=("B", "A", "X", "C"),
        play_plugins=("One.esp", "Two.esp"),
        lab_plugins=("One.esp", "Extra.esp", "Two.esp"),
        play_state_files=(
            _state_file("ModLab - Play", "settings.ini", "1"),
        ),
        lab_state_files=(
            _state_file("ModLab - Lab", "settings.ini", "2"),
        ),
        installed_mods=(
            Mo2InstalledModState(
                "SkyUI",
                Mo2StateFileEvidence("mods/SkyUI/meta.ini", "3" * 64, 12),
            ),
        ),
        overwrite_entries=("Nemesis output",),
    )
    context = replace(
        projection.observation_context,
        executable=Mo2ExecutableEvidence(
            "ModOrganizer.exe", "2.5.2.0", "4" * 64, 100
        ),
        configured_paths=(
            Mo2PathEvidence(
                "base",
                r"C:\ModLab\workspace\tools\mo2\skyrim-se-ae",
                r"C:\ModLab\workspace\tools\mo2\skyrim-se-ae",
                True,
            ),
        ),
    )
    return compare_mo2_profiles(replace(projection, observation_context=context))


def make_large_order_difference(*, size: int, first_divergence: int):
    play = tuple(f"Entry-{index:02d}" for index in range(size))
    lab = list(play)
    lab[first_divergence], lab[first_divergence + 1] = (
        lab[first_divergence + 1],
        lab[first_divergence],
    )
    return compare_mo2_profiles(
        make_projection(play_mods=play, lab_mods=tuple(lab))
    )


class Mo2ComparisonSerializationTests(unittest.TestCase):
    def test_ready_result_round_trip_is_canonical_and_hash_checked(self):
        report = make_comparison_report()
        mapping = comparison_result_to_dict(report)
        self.assertEqual(report, comparison_result_from_dict(mapping))
        encoded = comparison_result_to_json(report)
        restored = comparison_result_from_json(encoded)
        result = json.loads(encoded)

        self.assertEqual(report, restored)
        self.assertEqual(encoded, comparison_result_to_json(restored))
        self.assertEqual([], result["actionsPerformed"])
        self.assertEqual([], result["downloadsPerformed"])
        self.assertEqual([], result["installationActionsPerformed"])
        self.assertEqual([], result["programsLaunched"])
        self.assertEqual(
            adapter_state_sha256(report.adapter_state),
            result["managerComparison"]["adapterStateSha256"],
        )

    def test_rejects_unknown_field_at_every_object_level(self):
        base = comparison_result_to_dict(make_comparison_report())

        def targets(value):
            manager = value["managerComparison"]
            state = manager["adapterState"]
            differences = manager["differences"]
            return (
                value,
                manager,
                manager["observationContext"],
                manager["observationContext"]["executable"],
                manager["observationContext"]["configuredPaths"][0],
                manager["capabilities"][0],
                state,
                state["lab"],
                state["lab"]["mods"][0],
                state["lab"]["plugins"][0],
                state["lab"]["stateFiles"][0],
                state["installedMods"][0],
                differences,
                differences["modChanges"][0],
                differences["modChanges"][0]["labPosition"],
                differences["modOrder"],
                differences["configuration"],
                manager["observations"],
                manager["coverage"],
                manager["findings"][0],
            )

        for index in range(len(targets(copy.deepcopy(base)))):
            value = copy.deepcopy(base)
            targets(value)[index]["unexpected"] = True
            with self.subTest(level=index):
                with self.assertRaises(Mo2ComparisonFormatError):
                    comparison_result_from_dict(value)

    def test_rejects_duplicate_json_key_and_wrong_contract_values(self):
        encoded = comparison_result_to_json(make_comparison_report())
        duplicate = encoded.replace(
            '"schemaVersion":1',
            '"schemaVersion":1,"schemaVersion":1',
            1,
        )
        with self.assertRaisesRegex(Mo2ComparisonFormatError, "duplicate JSON key"):
            comparison_result_from_json(duplicate)

        base = comparison_result_to_dict(make_comparison_report())
        mutations = (
            lambda value: value.__setitem__("schemaVersion", 2),
            lambda value: value["managerComparison"].__setitem__("schemaVersion", 1.0),
            lambda value: value["managerComparison"].__setitem__("adapterId", "wrong"),
            lambda value: value["managerComparison"].__setitem__("direction", "Lab-to-Play"),
            lambda value: value["managerComparison"].__setitem__("readiness", "Maybe"),
            lambda value: value["managerComparison"]["coverage"].__setitem__("profileState", "done"),
            lambda value: value["managerComparison"]["coverage"].__setitem__("installedPayloadContent", "fingerprinted"),
            lambda value: value["managerComparison"]["coverage"].__setitem__("runtimeValidation", "passed"),
        )
        for index, mutate in enumerate(mutations):
            value = copy.deepcopy(base)
            mutate(value)
            with self.subTest(case=index):
                with self.assertRaises(Mo2ComparisonFormatError):
                    comparison_result_from_dict(value)

    def test_rejects_duplicate_case_insensitive_identities(self):
        base = comparison_result_to_dict(make_comparison_report())
        mutations = (
            lambda manager: manager["adapterState"]["lab"]["mods"].append(
                copy.deepcopy(manager["adapterState"]["lab"]["mods"][0])
            ),
            lambda manager: manager["adapterState"]["lab"]["plugins"].append(
                copy.deepcopy(manager["adapterState"]["lab"]["plugins"][0])
            ),
            lambda manager: manager["adapterState"]["installedMods"].append(
                copy.deepcopy(manager["adapterState"]["installedMods"][0])
            ),
            lambda manager: manager["capabilities"].append(
                copy.deepcopy(manager["capabilities"][0])
            ),
            lambda manager: manager["findings"].append(
                copy.deepcopy(manager["findings"][0])
            ),
        )
        for index, mutate in enumerate(mutations):
            value = copy.deepcopy(base)
            mutate(value["managerComparison"])
            with self.subTest(case=index):
                with self.assertRaises(Mo2ComparisonFormatError):
                    comparison_result_from_dict(value)

    def test_rejects_readiness_hash_position_anchor_order_and_side_effect_inconsistency(self):
        ready = comparison_result_to_dict(make_comparison_report())
        cases = []
        for field in ("adapterState", "adapterStateSha256", "differences"):
            value = copy.deepcopy(ready)
            value["managerComparison"][field] = None
            cases.append(value)

        blocked = comparison_result_to_dict(
            compare_mo2_profiles(make_projection(blocked=True))
        )
        blocked["managerComparison"]["adapterState"] = copy.deepcopy(
            ready["managerComparison"]["adapterState"]
        )
        cases.append(blocked)

        wrong_hash = copy.deepcopy(ready)
        wrong_hash["managerComparison"]["adapterStateSha256"] = "0" * 64
        cases.append(wrong_hash)

        wrong_position = copy.deepcopy(ready)
        wrong_position["managerComparison"]["differences"]["modChanges"][0][
            "labPosition"
        ]["sequenceIndex"] = 999
        cases.append(wrong_position)

        wrong_anchor = copy.deepcopy(ready)
        wrong_anchor["managerComparison"]["differences"]["modChanges"][0][
            "labPosition"
        ]["previousSharedAnchor"] = "Not-shared"
        cases.append(wrong_anchor)

        wrong_order = copy.deepcopy(ready)
        wrong_order["managerComparison"]["differences"]["modOrder"][
            "differingPositionCount"
        ] = 0
        cases.append(wrong_order)

        for field in (
            "actionsPerformed",
            "downloadsPerformed",
            "installationActionsPerformed",
            "programsLaunched",
        ):
            value = copy.deepcopy(ready)
            value[field] = ["unexpected"]
            cases.append(value)

        for index, value in enumerate(cases):
            with self.subTest(case=index):
                with self.assertRaises(Mo2ComparisonFormatError):
                    comparison_result_from_dict(value)

    def test_text_order_output_is_bounded_to_three_neighbors(self):
        report = make_large_order_difference(size=30, first_divergence=12)
        text = comparison_result_to_text(report)
        self.assertIn("First divergence: 12", text)
        self.assertIn("Use --format json for complete sequences.", text)
        self.assertLessEqual(text.count("Play["), 7)
        self.assertLessEqual(text.count("Lab["), 7)
        self.assertTrue(
            text.rstrip().endswith(
                "Nothing was launched, changed, installed, repaired, promoted, or downloaded."
            )
        )

    def test_text_no_difference_shared_content_uses_bounded_claim(self):
        report = compare_mo2_profiles(
            make_projection(
                installed_mods=(Mo2InstalledModState("SkyUI", None),)
            )
        )
        text = comparison_result_to_text(report)

        self.assertIn("No in-scope profile-state differences were found.", text)
        self.assertIn(
            "Installed mod payload contents were not fingerprinted by this command.",
            text,
        )
        self.assertIn(
            "Lab and Play may still be affected by changes to their shared mod directories.",
            text,
        )


if __name__ == "__main__":
    unittest.main()
