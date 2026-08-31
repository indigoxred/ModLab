import tempfile
import unittest
from pathlib import Path

from modlab.recipes.loading import load_environment
from modlab.recipes.model import CheckState, EnvironmentEvidence
from modlab.workflows.skyrim.observation import (
    normalize_mo2_compatibility_version,
    observe_skyrim_environment,
)
from tests.support.skyrim_workflow import create_skyrim_workflow_fixture


def tree_state(root: Path) -> tuple[tuple[str, str, bytes | None], ...]:
    return tuple(
        (
            path.relative_to(root).as_posix(),
            "directory" if path.is_dir() else "file",
            None if path.is_dir() else path.read_bytes(),
        )
        for path in sorted(root.rglob("*"))
    )


class SkyrimObservationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.fixture = create_skyrim_workflow_fixture(self.root)

    def observe(
        self,
        target: EnvironmentEvidence | None = None,
        *,
        skyrim_version: str | None = "1.7.104.0",
        mo2_version: str | None = "2.5.2.0",
    ):
        return observe_skyrim_environment(
            self.fixture.steam_root,
            self.fixture.workspace,
            target or load_environment(self.fixture.environment_path),
            skyrim_version_reader=lambda _: skyrim_version,
            mo2_version_reader=lambda _: mo2_version,
        )

    def test_observes_ready_game_manager_and_only_real_live_dimensions(self):
        # Catches target intent being copied into supposedly live evidence.
        before = tree_state(self.fixture.root)

        observation = self.observe()

        self.assertTrue(observation.ready)
        self.assertEqual(
            "1.7.104",
            dict(observation.live_dimensions)["executableRuntime"],
        )
        self.assertEqual(
            "2.5.2", dict(observation.live_dimensions)["adapterVersion"]
        )
        self.assertNotIn("scriptExtender", dict(observation.live_dimensions))
        script = next(
            item
            for item in observation.dimension_findings
            if item.dimension == "scriptExtender"
        )
        self.assertEqual(CheckState.UNKNOWN, script.state)
        self.assertIsNone(script.actual_value)
        self.assertEqual(before, tree_state(self.fixture.root))

    def test_observed_target_mismatch_blocks(self):
        # Catches a known incompatible live version being treated as unverified.
        target = EnvironmentEvidence(
            1,
            "skyrim.test",
            {
                "adapterVersion": "2.4.0",
                "executableRuntime": "1.7.104",
            },
        )

        observation = self.observe(target)

        self.assertFalse(observation.ready)
        adapter = next(
            item
            for item in observation.dimension_findings
            if item.dimension == "adapterVersion"
        )
        self.assertEqual(CheckState.BLOCKED, adapter.state)
        self.assertEqual("2.5.2", adapter.actual_value)

    def test_normalizes_only_unambiguous_mo2_versions(self):
        # Catches arbitrary version coercion hiding a manager compatibility change.
        cases = {
            "2.5.2.0": "2.5.2",
            "2.5.2": "2.5.2",
            "2.5.2.1": None,
            "2.5": None,
            "2.5.2.0.0": None,
            "v2.5.2": None,
            None: None,
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(
                    expected, normalize_mo2_compatibility_version(value)
                )

    def test_three_component_mo2_version_is_retained_as_live(self):
        # Catches valid raw three-part identity being discarded.
        observation = self.observe(mo2_version="2.5.2")

        self.assertTrue(observation.ready)
        self.assertEqual(
            "2.5.2", dict(observation.live_dimensions)["adapterVersion"]
        )
        self.assertEqual(
            "2.5.2",
            observation.projection.observation_context.executable.file_version,
        )

    def test_ambiguous_mo2_version_prevents_ready_observation(self):
        # Catches an observable target dimension becoming silently Unknown-ready.
        observation = self.observe(mo2_version="2.5.2.1")

        self.assertFalse(observation.ready)
        self.assertNotIn("adapterVersion", dict(observation.live_dimensions))
        adapter = next(
            item
            for item in observation.dimension_findings
            if item.dimension == "adapterVersion"
        )
        self.assertEqual(CheckState.UNKNOWN, adapter.state)

    def test_missing_skyrim_or_mo2_executable_identity_blocks_ready(self):
        # Catches checkpoint capture proceeding without byte identities.
        skyrim_executable = self.fixture.game_root / "SkyrimSE.exe"
        skyrim_executable.unlink()
        self.assertFalse(self.observe().ready)

        self.fixture = create_skyrim_workflow_fixture(self.root / "second")
        (self.fixture.mo2_root / "ModOrganizer.exe").unlink()
        self.assertFalse(self.observe().ready)

    def test_blocked_skyrim_discovery_returns_no_manager_projection(self):
        # Catches following a manager against an untrusted game root.
        manifest = (
            self.fixture.steam_root / "steamapps" / "appmanifest_489830.acf"
        )
        manifest.write_text(
            manifest.read_text(encoding="utf-8").replace("489830", "123", 1),
            encoding="utf-8",
        )

        observation = self.observe()

        self.assertFalse(observation.ready)
        self.assertIsNone(observation.projection)
        self.assertTrue(
            any(
                item.state is CheckState.BLOCKED
                for item in observation.discovery.findings
            )
        )

    def test_blocked_projection_capability_prevents_ready(self):
        # Catches partial Lab/Play evidence being accepted for a baseline.
        (
            self.fixture.workspace
            / "tools"
            / "mo2"
            / "skyrim-se-ae"
            / "profiles"
            / "ModLab - Play"
            / "settings.ini"
        ).unlink()

        observation = self.observe()

        self.assertFalse(observation.ready)
        self.assertEqual("Blocked", observation.projection.readiness.value)
        self.assertTrue(
            any(
                item.status == "incomplete"
                for item in observation.projection.capabilities
            )
        )

    def test_mo2_game_root_mismatch_prevents_ready(self):
        # Catches a contained manager targeting a different Skyrim installation.
        ini = self.fixture.mo2_root / "ModOrganizer.ini"
        ini.write_text(
            ini.read_text(encoding="utf-8").replace(
                f"gamePath={self.fixture.game_root.as_posix()}",
                "gamePath=C:/Games/Other Skyrim",
            ),
            encoding="utf-8",
        )

        observation = self.observe()

        self.assertFalse(observation.ready)
        self.assertTrue(
            any(
                item.code == "game-path-mismatch"
                and item.state is CheckState.BLOCKED
                for item in observation.projection.findings
            )
        )


if __name__ == "__main__":
    unittest.main()
