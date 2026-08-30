from dataclasses import replace
import unittest

from modlab.adapters.mo2.model import (
    Mo2ComparisonInspectionEvidence,
    Mo2ExecutableEvidence,
    Mo2Finding,
    Mo2InspectionReport,
    Mo2ModEntry,
    Mo2PathEvidence,
    Mo2PluginEntry,
    Mo2ProfileComparisonEvidence,
    Mo2ProfileEvidence,
    Mo2StateFileEvidence,
)
from modlab.adapters.mo2.projection import (
    Mo2Readiness,
    project_mo2_state,
)
from modlab.recipes.model import CheckState


CORE = (
    "Skyrim.esm",
    "Update.esm",
    "Dawnguard.esm",
    "HearthFires.esm",
    "Dragonborn.esm",
)


def _profile(
    name: str,
    mods: tuple[Mo2ModEntry, ...],
    plugins: tuple[Mo2PluginEntry, ...],
    load_order: tuple[str, ...],
) -> Mo2ProfileEvidence:
    state_files = tuple(
        Mo2StateFileEvidence(
            f"profiles/{name}/{filename}",
            (str(index + 1) * 64)[:64],
            index + 1,
        )
        for index, filename in enumerate(
            ("loadorder.txt", "modlist.txt", "plugins.txt", "settings.ini")
        )
    )
    return Mo2ProfileEvidence(
        name=name,
        relative_path=f"profiles/{name}",
        profile_local_saves=False,
        state_files=state_files,
        mods=mods,
        plugins=plugins,
        load_order=load_order,
        profile_local_settings=True,
    )


def make_report(
    *,
    lab_mods: tuple[Mo2ModEntry, ...] = (),
    play_mods: tuple[Mo2ModEntry, ...] = (),
    non_primary_plugins: tuple[str, ...] = ("SkyUI_SE.esp",),
    lab_plugins: tuple[Mo2PluginEntry, ...] | None = None,
    play_plugins: tuple[Mo2PluginEntry, ...] | None = None,
    lab_load_order: tuple[str, ...] | None = None,
    play_load_order: tuple[str, ...] | None = None,
    resolved_root: str = r"C:\ModLab\workspace\tools\mo2\skyrim-se-ae\app",
    active_profile: str = "ModLab - Lab",
    file_version: str | None = "2.5.2.0",
    read_set_stable: bool = True,
) -> Mo2InspectionReport:
    default_plugins = tuple(
        Mo2PluginEntry(name, True) for name in non_primary_plugins
    )
    default_load_order = CORE + non_primary_plugins
    lab = _profile(
        "ModLab - Lab",
        lab_mods,
        default_plugins if lab_plugins is None else lab_plugins,
        default_load_order if lab_load_order is None else lab_load_order,
    )
    play = _profile(
        "ModLab - Play",
        play_mods,
        default_plugins if play_plugins is None else play_plugins,
        default_load_order if play_load_order is None else play_load_order,
    )
    instance_root = resolved_root.removesuffix(r"\app")
    paths = tuple(
        Mo2PathEvidence(
            kind,
            instance_root if kind == "base" else f"%BASE_DIR%/{kind}",
            instance_root if kind == "base" else f"{instance_root}\\{kind}",
            True,
        )
        for kind in ("base", "downloads", "mods", "profiles", "overwrite")
    )
    findings = (
        Mo2Finding(CheckState.PASSED, "mo2-root-contained", "contained"),
        Mo2Finding(CheckState.PASSED, "portable-config-observed", "parsed"),
        Mo2Finding(CheckState.PASSED, "game-root-observed", "observed"),
        Mo2Finding(CheckState.PASSED, "game-path-matched", "matched"),
        Mo2Finding(CheckState.PASSED, "paths-contained", "contained"),
        Mo2Finding(CheckState.PASSED, "lab-play-ready", "ready"),
        Mo2Finding(CheckState.PASSED, "overwrite-empty", "empty"),
    )
    return Mo2InspectionReport(
        schema_version=1,
        adapter_id="portable-mo2-skyrim",
        requested_root=resolved_root,
        resolved_root=resolved_root,
        game_root=r"C:\Steam\Skyrim Special Edition",
        executable=Mo2ExecutableEvidence(
            "ModOrganizer.exe", file_version, "a" * 64, 100
        ),
        portable_config_present=True,
        configured_game_path=r"C:\Steam\Skyrim Special Edition",
        paths=paths,
        active_profile=active_profile,
        profiles=(lab, play),
        top_level_mods=(),
        mod_metadata_files=(),
        overwrite_entries=(),
        findings=findings,
        actions=(),
        downloads=(),
        installations=(),
        program_launches=(),
        comparison_evidence=Mo2ComparisonInspectionEvidence(
            primary_plugins=CORE,
            skyrim_ccc=None,
            profile_settings=(
                Mo2ProfileComparisonEvidence("ModLab - Lab", True),
                Mo2ProfileComparisonEvidence("ModLab - Play", True),
            ),
            read_set_sha256="f" * 64,
            read_set_stable=read_set_stable,
            changed_paths=() if read_set_stable else (r"C:\changed",),
        ),
    )


class Mo2ProjectionTests(unittest.TestCase):
    def test_projects_reverse_modlist_priority_and_reconciles_primary_plugins(self):
        report = make_report(
            lab_mods=(
                Mo2ModEntry("Late_separator", "+", True),
                Mo2ModEntry("SkyUI", "+", True),
                Mo2ModEntry("DLC: Dawnguard", "*", True),
            ),
            play_mods=(
                Mo2ModEntry("Late_separator", "+", True),
                Mo2ModEntry("SkyUI", "+", True),
                Mo2ModEntry("DLC: Dawnguard", "*", True),
            ),
        )
        projection = project_mo2_state(report)
        self.assertEqual(Mo2Readiness.READY, projection.readiness)
        self.assertEqual(
            (
                "scanner-safety",
                "configuration",
                "game-match",
                "contained-paths",
                "exact-profiles",
                "required-profile-files",
                "primary-plugin-policy",
                "plugin-order-coherence",
                "installed-inventory",
                "overwrite-inventory",
                "stable-read-set",
                "read-only-side-effects",
            ),
            tuple(item.name for item in projection.capabilities),
        )
        self.assertTrue(
            all(item.status == "complete" for item in projection.capabilities)
        )
        lab = projection.adapter_state.lab
        self.assertEqual(
            ("DLC: Dawnguard", "SkyUI", "Late_separator"),
            tuple(item.name for item in lab.mods),
        )
        self.assertEqual(
            ("foreign", "managed", "separator"),
            tuple(item.kind for item in lab.mods),
        )
        self.assertEqual(
            (0, 1, None), tuple(item.runtime_priority for item in lab.mods)
        )
        self.assertTrue(all(item.enabled for item in lab.plugins[:5]))
        self.assertEqual(
            ("SkyUI_SE.esp",),
            tuple(item.name for item in lab.plugins if not item.primary),
        )

    def test_valid_primary_only_profiles_are_ready(self):
        projection = project_mo2_state(make_report(non_primary_plugins=()))
        self.assertEqual(Mo2Readiness.READY, projection.readiness)
        self.assertEqual(
            (),
            tuple(item.name for item in projection.adapter_state.lab.plugins[5:]),
        )

    def test_skyrim_ccc_policy_allows_an_ordered_installed_subset(self):
        report = make_report()
        policy = CORE + (
            "Creation-A.esl",
            "Creation-B.esl",
            "Creation-C.esl",
        )
        installed_load_order = CORE + (
            "Creation-A.esl",
            "Creation-C.esl",
            "SkyUI_SE.esp",
        )
        report = replace(
            report,
            profiles=tuple(
                replace(profile, load_order=installed_load_order)
                for profile in report.profiles
            ),
            comparison_evidence=replace(
                report.comparison_evidence,
                primary_plugins=policy,
            ),
        )

        projection = project_mo2_state(report)

        self.assertEqual(Mo2Readiness.READY, projection.readiness)
        self.assertEqual(
            (True, True, True, True, True, True, True, False),
            tuple(item.primary for item in projection.adapter_state.lab.plugins),
        )

    def test_skyrim_ccc_installed_subset_must_retain_policy_order(self):
        report = make_report()
        report = replace(
            report,
            profiles=tuple(
                replace(
                    profile,
                    load_order=CORE
                    + ("Creation-C.esl", "Creation-A.esl", "SkyUI_SE.esp"),
                )
                for profile in report.profiles
            ),
            comparison_evidence=replace(
                report.comparison_evidence,
                primary_plugins=CORE
                + ("Creation-A.esl", "Creation-B.esl", "Creation-C.esl"),
            ),
        )

        projection = project_mo2_state(report)

        self.assertEqual(Mo2Readiness.BLOCKED, projection.readiness)
        self.assertIsNone(projection.adapter_state)

    def test_nonprimary_membership_or_order_mismatch_blocks_without_state(self):
        for report in (
            make_report(lab_plugins=(Mo2PluginEntry("OnlyState.esp", True),)),
            make_report(lab_load_order=CORE + ("Unknown.esp",)),
            make_report(
                lab_load_order=CORE + ("B.esp", "A.esp"),
                lab_plugins=(
                    Mo2PluginEntry("A.esp", True),
                    Mo2PluginEntry("B.esp", True),
                ),
            ),
        ):
            with self.subTest(report=report):
                projection = project_mo2_state(report)
                self.assertEqual(Mo2Readiness.BLOCKED, projection.readiness)
                self.assertIsNone(projection.adapter_state)
                self.assertIn(
                    "mo2-plugin-order-evidence-inconsistent",
                    {item.code for item in projection.findings},
                )

    def test_context_changes_do_not_change_adapter_state_hash(self):
        first = project_mo2_state(
            make_report(
                resolved_root=r"C:\First\workspace\tools\mo2\skyrim-se-ae\app",
                active_profile="ModLab - Lab",
            )
        )
        second = project_mo2_state(
            make_report(
                resolved_root=r"D:\Relocated\workspace\tools\mo2\skyrim-se-ae\app",
                active_profile="ModLab - Play",
            )
        )
        self.assertNotEqual(first.observation_context, second.observation_context)
        self.assertEqual(first.adapter_state, second.adapter_state)
        self.assertEqual(
            first.adapter_state_sha256, second.adapter_state_sha256
        )

    def test_unknown_version_is_contextual_but_unstable_state_blocks(self):
        unknown_version = project_mo2_state(make_report(file_version=None))
        self.assertEqual(Mo2Readiness.READY, unknown_version.readiness)

        missing_executable = project_mo2_state(
            replace(make_report(), executable=None)
        )
        self.assertEqual(Mo2Readiness.BLOCKED, missing_executable.readiness)
        self.assertIsNone(missing_executable.adapter_state)

        unstable = project_mo2_state(make_report(read_set_stable=False))
        self.assertEqual(Mo2Readiness.BLOCKED, unstable.readiness)
        self.assertIsNone(unstable.adapter_state)

    def test_missing_required_file_or_conflicting_primary_identity_blocks(self):
        missing = make_report()
        lab = missing.profiles[0]
        missing = replace(
            missing,
            profiles=(replace(lab, state_files=lab.state_files[:-1]), missing.profiles[1]),
        )
        conflicting = make_report()
        conflicting = replace(
            conflicting,
            comparison_evidence=replace(
                conflicting.comparison_evidence,
                primary_plugins=CORE + ("skyrim.ESM",),
            ),
        )

        for report in (missing, conflicting):
            with self.subTest(report=report):
                projection = project_mo2_state(report)
                self.assertEqual(Mo2Readiness.BLOCKED, projection.readiness)
                self.assertIsNone(projection.adapter_state)

    def test_absent_metadata_is_valid_but_failed_metadata_observation_blocks(self):
        absent = replace(make_report(), top_level_mods=("SkyUI",))
        ready = project_mo2_state(absent)
        self.assertEqual(Mo2Readiness.READY, ready.readiness)
        self.assertIsNone(ready.adapter_state.installed_mods[0].meta_ini)

        for label in ("unreadable", "redirected"):
            report = replace(
                absent,
                findings=absent.findings
                + (
                    Mo2Finding(
                        CheckState.BLOCKED,
                        "mod-metadata-skipped",
                        f"metadata was {label}",
                    ),
                ),
            )
            with self.subTest(label=label):
                self.assertEqual(
                    Mo2Readiness.BLOCKED,
                    project_mo2_state(report).readiness,
                )

    def test_incomplete_overwrite_scanner_block_side_effects_and_missing_evidence_block(self):
        base = make_report()
        incomplete_overwrite = replace(
            base,
            findings=tuple(
                item for item in base.findings if item.code != "overwrite-empty"
            )
            + (
                Mo2Finding(
                    CheckState.UNKNOWN,
                    "overwrite-status-unknown",
                    "not fully observed",
                ),
            ),
        )
        scanner_blocked = replace(
            base,
            findings=base.findings
            + (Mo2Finding(CheckState.BLOCKED, "scanner-blocked", "blocked"),),
        )
        reports = [
            incomplete_overwrite,
            scanner_blocked,
            replace(base, comparison_evidence=None),
        ]
        for field in ("actions", "downloads", "installations", "program_launches"):
            reports.append(replace(base, **{field: ("unexpected",)}))

        for report in reports:
            with self.subTest(report=report):
                projection = project_mo2_state(report)
                self.assertEqual(Mo2Readiness.BLOCKED, projection.readiness)
                self.assertIsNone(projection.adapter_state)


if __name__ == "__main__":
    unittest.main()
