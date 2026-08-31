import copy
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from modlab.workflows.skyrim.serialization import (
    SkyrimWorkflowFormatError,
    capture_result_from_dict,
    capture_result_to_dict,
    capture_result_to_text,
    configure_result_from_dict,
    configure_result_to_dict,
    configure_result_to_text,
    status_result_from_dict,
    status_result_to_dict,
    status_result_to_text,
    use_result_from_dict,
    use_result_to_dict,
    use_result_to_text,
)
from modlab.workflows.skyrim.service import (
    configure_skyrim_environment,
    create_skyrim_baseline,
    get_skyrim_status,
    use_skyrim_baseline,
)
from tests.support.skyrim_workflow import create_skyrim_workflow_fixture


FIXED_TIME = datetime(2026, 8, 31, 4, 5, 6, tzinfo=timezone.utc)


class SkyrimWorkflowSerializationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.fixture = create_skyrim_workflow_fixture(
            Path(self.directory.name)
        )
        self.configure_result = configure_skyrim_environment(
            steam_root=self.fixture.steam_root,
            recipe_path=self.fixture.recipe_path,
            target_environment_path=self.fixture.environment_path,
            workspace_root=self.fixture.workspace,
            skyrim_version_reader=lambda _: "1.7.104.0",
            mo2_version_reader=lambda _: "2.5.2.0",
        )

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

    def assert_common_contract(self, value):
        self.assertEqual(1, value["schemaVersion"])
        self.assertEqual([], value["downloadsPerformed"])
        self.assertEqual([], value["installationActionsPerformed"])
        self.assertEqual([], value["programsLaunched"])

    def test_configure_json_and_text_report_verified_and_unverified_scope(self):
        value = configure_result_to_dict(
            self.configure_result, workspace_root=self.fixture.workspace
        )
        text = configure_result_to_text(
            self.configure_result, workspace_root=self.fixture.workspace
        )

        self.assert_common_contract(value)
        self.assertEqual(value, configure_result_from_dict(
            value, workspace_root=self.fixture.workspace
        ))
        self.assertEqual("1.7.104", value["configure"]["game"]["runtime"])
        self.assertEqual("2.5.2", value["configure"]["manager"]["version"])
        self.assertEqual(
            ["scriptExtender"],
            value["configure"]["targetEnvironment"]["unverifiedDimensions"],
        )
        self.assertEqual(3, len(value["actionsPerformed"]))
        self.assertIn("Configured", text)
        self.assertIn("scriptExtender (target intent only)", text)
        self.assertIn("Exact ModLab files written", text)

    def test_capture_and_use_results_make_fixed_nonclaims_and_writes_explicit(self):
        captured = self.capture()
        capture_value = capture_result_to_dict(
            captured, workspace_root=self.fixture.workspace
        )
        capture_text = capture_result_to_text(
            captured, workspace_root=self.fixture.workspace
        )
        used = use_skyrim_baseline(
            self.fixture.workspace, captured.checkpoint.checkpoint_id
        )
        use_value = use_result_to_dict(
            used, workspace_root=self.fixture.workspace
        )

        self.assert_common_contract(capture_value)
        self.assertEqual(capture_value, capture_result_from_dict(
            capture_value, workspace_root=self.fixture.workspace
        ))
        summary = capture_value["baselineCapture"]
        self.assertEqual("Observed", summary["qualification"])
        self.assertFalse(summary["promotionReady"])
        self.assertFalse(summary["restorable"])
        self.assertEqual("NotVerified", summary["foundationAssembly"])
        self.assertEqual("NotPerformed", summary["runtimeValidation"])
        self.assertEqual("NotPerformed", summary["smokeTest"])
        self.assertEqual([], summary["artifactIds"])
        self.assertIn("not a playable-build approval", capture_text)
        self.assertIn("Exact ModLab files written", capture_text)

        self.assert_common_contract(use_value)
        self.assertEqual(use_value, use_result_from_dict(
            use_value, workspace_root=self.fixture.workspace
        ))
        self.assertIn("Selected observed baseline", use_result_to_text(
            used, workspace_root=self.fixture.workspace
        ))

    def test_status_json_and_text_are_bounded_and_explicitly_read_only(self):
        captured = self.capture()
        profile = (
            self.fixture.workspace / "tools" / "mo2" / "skyrim-se-ae"
            / "profiles" / "ModLab - Lab" / "archives.txt"
        )
        profile.write_text("changed archive\n", encoding="utf-8")
        report = self.status()
        value = status_result_to_dict(report)
        text = status_result_to_text(report)

        self.assert_common_contract(value)
        self.assertEqual([], value["actionsPerformed"])
        self.assertEqual(value, status_result_from_dict(value))
        self.assertEqual("Drifted", value["status"]["outcome"])
        self.assertEqual(
            captured.checkpoint.checkpoint_id,
            value["status"]["baselineCheckpointId"],
        )
        windows = [
            change[key]
            for domain in value["status"]["domains"]
            for change in domain["changes"]
            for key in ("checkpointValue", "currentValue")
            if isinstance(change[key], list)
        ]
        self.assertTrue(all(len(window) <= 7 for window in windows))
        self.assertIn("Drifted", text.splitlines()[0])
        self.assertIn(
            "Nothing was written, launched, installed, repaired, restored, or promoted.",
            text,
        )
        self.assertNotIn("adapterState", str(value))

    def test_no_baseline_and_blocked_have_strict_nullable_pointer_contract(self):
        no_baseline = status_result_to_dict(self.status())
        self.assertEqual("NoBaseline", no_baseline["status"]["outcome"])
        self.assertIsNone(no_baseline["status"]["baselineCheckpointId"])

        broken = copy.deepcopy(no_baseline)
        broken["status"]["outcome"] = "Blocked"
        broken["status"]["baselineCheckpointId"] = "not-a-checkpoint"
        with self.assertRaises(SkyrimWorkflowFormatError):
            status_result_from_dict(broken)

        blocked = get_skyrim_status(self.fixture.root / "not-configured")
        blocked_value = status_result_to_dict(blocked)
        self.assertEqual("Blocked", blocked_value["status"]["outcome"])
        self.assertEqual(
            "NotVerified", blocked_value["status"]["coverage"]["profileState"]
        )
        self.assertIn("profile state NotVerified", status_result_to_text(blocked))

    def test_strict_result_fields_duplicate_domains_and_action_containment(self):
        configured = configure_result_to_dict(
            self.configure_result, workspace_root=self.fixture.workspace
        )
        unknown = copy.deepcopy(configured)
        unknown["surprise"] = True
        with self.assertRaises(SkyrimWorkflowFormatError):
            configure_result_from_dict(
                unknown, workspace_root=self.fixture.workspace
            )

        escaped = copy.deepcopy(configured)
        escaped["actionsPerformed"] = [str(self.fixture.root / "outside.txt")]
        with self.assertRaises(SkyrimWorkflowFormatError):
            configure_result_from_dict(
                escaped, workspace_root=self.fixture.workspace
            )

        wrong_configure_target = copy.deepcopy(configured)
        wrong_configure_target["actionsPerformed"] = [
            str(self.fixture.mo2_root / "ModOrganizer.ini")
        ]
        with self.assertRaises(SkyrimWorkflowFormatError):
            configure_result_from_dict(
                wrong_configure_target, workspace_root=self.fixture.workspace
            )

        captured_result = self.capture()
        captured = capture_result_to_dict(
            captured_result, workspace_root=self.fixture.workspace
        )
        wrong_capture_target = copy.deepcopy(captured)
        wrong_capture_target["actionsPerformed"] = [
            str(self.fixture.mo2_root / "ModOrganizer.ini")
        ]
        with self.assertRaises(SkyrimWorkflowFormatError):
            capture_result_from_dict(
                wrong_capture_target, workspace_root=self.fixture.workspace
            )

        used_result = use_skyrim_baseline(
            self.fixture.workspace, captured_result.checkpoint.checkpoint_id
        )
        used = use_result_to_dict(
            used_result, workspace_root=self.fixture.workspace
        )
        wrong_use_target = copy.deepcopy(used)
        wrong_use_target["actionsPerformed"] = [
            str(self.fixture.mo2_root / "ModOrganizer.ini")
        ]
        with self.assertRaises(SkyrimWorkflowFormatError):
            use_result_from_dict(
                wrong_use_target, workspace_root=self.fixture.workspace
            )

        profile = (
            self.fixture.workspace / "tools" / "mo2" / "skyrim-se-ae"
            / "profiles" / "ModLab - Lab" / "archives.txt"
        )
        profile.write_text("changed\n", encoding="utf-8")
        status = status_result_to_dict(self.status())
        duplicate = copy.deepcopy(status)
        duplicate["status"]["domains"].append(
            copy.deepcopy(duplicate["status"]["domains"][0])
        )
        with self.assertRaises(SkyrimWorkflowFormatError):
            status_result_from_dict(duplicate)

        duplicate_field = copy.deepcopy(status)
        duplicate_field["status"]["domains"][0]["changes"].append(
            copy.deepcopy(
                duplicate_field["status"]["domains"][0]["changes"][0]
            )
        )
        with self.assertRaises(SkyrimWorkflowFormatError):
            status_result_from_dict(duplicate_field)


if __name__ == "__main__":
    unittest.main()
