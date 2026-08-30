import unittest

from modlab.adapters.mo2.comparison import compare_mo2_profiles
from modlab.adapters.mo2.model import (
    Mo2ExecutableEvidence,
    Mo2Finding,
    Mo2StateFileEvidence,
)
from modlab.adapters.mo2.projection import (
    Mo2AdapterState,
    Mo2Capability,
    Mo2Coverage,
    Mo2InstalledModState,
    Mo2ObservationContext,
    Mo2ProfileState,
    Mo2ProjectedMod,
    Mo2ProjectedPlugin,
    Mo2Projection,
    Mo2Readiness,
    adapter_state_sha256,
)
from modlab.recipes.model import CheckState


ModSpec = str | tuple[str, bool]
CAPABILITIES = (
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
)


def _value(spec: ModSpec) -> tuple[str, bool]:
    return (spec, True) if isinstance(spec, str) else spec


def _profile(
    name: str,
    mods: tuple[ModSpec, ...],
    plugins: tuple[ModSpec, ...],
    state_files: tuple[Mo2StateFileEvidence, ...],
) -> Mo2ProfileState:
    projected_mods = []
    runtime_priority = 0
    for index, spec in enumerate(mods):
        entry_name, enabled = _value(spec)
        separator = entry_name.casefold().endswith("_separator")
        projected_mods.append(
            Mo2ProjectedMod(
                entry_name,
                "separator" if separator else "managed",
                "+" if enabled else "-",
                enabled,
                index,
                None if separator else runtime_priority,
            )
        )
        if not separator:
            runtime_priority += 1
    projected_plugins = tuple(
        Mo2ProjectedPlugin(entry_name, False, enabled, index)
        for index, spec in enumerate(plugins)
        for entry_name, enabled in (_value(spec),)
    )
    return Mo2ProfileState(
        name=name,
        profile_local_saves=False,
        profile_local_settings=True,
        mods=tuple(projected_mods),
        plugins=projected_plugins,
        effective_load_order=tuple(
            item.name for item in projected_plugins if item.enabled
        ),
        state_files=state_files,
    )


def make_projection(
    *,
    play_mods: tuple[ModSpec, ...] = (),
    lab_mods: tuple[ModSpec, ...] = (),
    play_plugins: tuple[ModSpec, ...] = (),
    lab_plugins: tuple[ModSpec, ...] = (),
    play_state_files: tuple[Mo2StateFileEvidence, ...] = (),
    lab_state_files: tuple[Mo2StateFileEvidence, ...] = (),
    installed_mods: tuple[Mo2InstalledModState, ...] = (),
    overwrite_entries: tuple[str, ...] = (),
    blocked: bool = False,
) -> Mo2Projection:
    context = Mo2ObservationContext(
        mo2_root=r"C:\ModLab\workspace\tools\mo2\skyrim-se-ae\app",
        game_root=r"C:\Steam\Skyrim Special Edition",
        active_profile="ModLab - Lab",
        executable=Mo2ExecutableEvidence(
            "ModOrganizer.exe", "2.5.2.0", "b" * 64, 100
        ),
        configured_paths=(),
        read_set_sha256="a" * 64,
        read_set_stable=not blocked,
    )
    status = "incomplete" if blocked else "complete"
    capabilities = tuple(
        Mo2Capability(name, status, f"{name} is {status}")
        for name in CAPABILITIES
    )
    coverage = Mo2Coverage("incomplete" if blocked else "complete")
    findings = (
        Mo2Finding(
            CheckState.BLOCKED if blocked else CheckState.PASSED,
            "projection-blocked" if blocked else (
                "overwrite-not-empty" if overwrite_entries else "overwrite-empty"
            ),
            "blocked" if blocked else "overwrite observed",
        ),
    )
    if blocked:
        return Mo2Projection(
            Mo2Readiness.BLOCKED,
            context,
            capabilities,
            None,
            None,
            coverage,
            findings,
        )
    state = Mo2AdapterState(
        schema_version=1,
        adapter_id="portable-mo2-skyrim",
        lab=_profile("ModLab - Lab", lab_mods, lab_plugins, lab_state_files),
        play=_profile(
            "ModLab - Play", play_mods, play_plugins, play_state_files
        ),
        installed_mods=installed_mods,
        overwrite_entries=overwrite_entries,
    )
    return Mo2Projection(
        Mo2Readiness.READY,
        context,
        capabilities,
        state,
        adapter_state_sha256(state),
        coverage,
        findings,
    )


def _state_file(profile: str, filename: str, identity: str) -> Mo2StateFileEvidence:
    return Mo2StateFileEvidence(
        f"profiles/{profile}/{filename}", identity * 64, 10
    )


class Mo2ComparisonTests(unittest.TestCase):
    def test_added_mod_keeps_placement_without_false_reorder(self):
        report = compare_mo2_profiles(
            make_projection(
                play_mods=("A", "B"),
                lab_mods=("A", "X", "B"),
            )
        )
        change = report.differences.mod_changes[0]
        self.assertEqual("X", change.name)
        self.assertEqual("added-in-lab", change.change)
        self.assertEqual(1, change.lab_position.sequence_index)
        self.assertEqual("A", change.lab_position.previous_shared_anchor)
        self.assertEqual("B", change.lab_position.next_shared_anchor)
        self.assertFalse(report.differences.mod_order.changed)

    def test_removed_mod_reports_play_placement(self):
        report = compare_mo2_profiles(
            make_projection(
                play_mods=("A", "X", "B"),
                lab_mods=("A", "B"),
            )
        )
        change = report.differences.mod_changes[0]
        self.assertEqual("removed-from-lab", change.change)
        self.assertEqual(1, change.play_position.sequence_index)
        self.assertEqual("A", change.play_position.previous_shared_anchor)
        self.assertEqual("B", change.play_position.next_shared_anchor)

    def test_newly_enabled_mod_reports_both_positions(self):
        report = compare_mo2_profiles(
            make_projection(
                play_mods=(("A", True), ("X", False), ("B", True)),
                lab_mods=(("A", True), ("X", True), ("B", True)),
            )
        )
        change = report.differences.mod_changes[0]
        self.assertEqual("enabled-in-lab", change.change)
        self.assertIsNotNone(change.play_position)
        self.assertIsNotNone(change.lab_position)
        self.assertEqual("A", change.lab_position.previous_shared_anchor)
        self.assertEqual("B", change.lab_position.next_shared_anchor)

    def test_plugin_membership_and_enablement_keep_placement(self):
        cases = (
            (
                make_projection(
                    play_plugins=("A.esp", "B.esp"),
                    lab_plugins=("A.esp", "X.esp", "B.esp"),
                ),
                "added-in-lab",
                "lab_position",
            ),
            (
                make_projection(
                    play_plugins=("A.esp", "X.esp", "B.esp"),
                    lab_plugins=("A.esp", "B.esp"),
                ),
                "removed-from-lab",
                "play_position",
            ),
            (
                make_projection(
                    play_plugins=(
                        ("A.esp", True),
                        ("X.esp", False),
                        ("B.esp", True),
                    ),
                    lab_plugins=(
                        ("A.esp", True),
                        ("X.esp", True),
                        ("B.esp", True),
                    ),
                ),
                "enabled-in-lab",
                "lab_position",
            ),
        )
        for projection, expected, position_name in cases:
            with self.subTest(change=expected):
                report = compare_mo2_profiles(projection)
                change = report.differences.plugin_changes[0]
                position = getattr(change, position_name)
                self.assertEqual("X.esp", change.name)
                self.assertEqual(expected, change.change)
                self.assertEqual(1, position.sequence_index)
                self.assertIsNone(position.runtime_priority)
                self.assertEqual("A.esp", position.previous_shared_anchor)
                self.assertEqual("B.esp", position.next_shared_anchor)
                self.assertFalse(report.differences.plugin_order.changed)

    def test_separators_are_reported_but_never_become_runtime_anchors(self):
        report = compare_mo2_profiles(
            make_projection(
                play_mods=("A", "B"),
                lab_mods=("A", "Tools_separator", "X", "B"),
            )
        )
        separator, added = report.differences.mod_changes
        self.assertEqual("separator", separator.entry_kind)
        self.assertIsNone(separator.lab_position.runtime_priority)
        self.assertEqual("A", added.lab_position.previous_shared_anchor)
        self.assertEqual("B", added.lab_position.next_shared_anchor)

    def test_relative_order_returns_full_filtered_sequences(self):
        for kind, projection in (
            (
                "mod",
                make_projection(
                    play_mods=("A", "B", "C"),
                    lab_mods=("B", "A", "C"),
                ),
            ),
            (
                "plugin",
                make_projection(
                    play_plugins=("A.esp", "B.esp", "C.esp"),
                    lab_plugins=("B.esp", "A.esp", "C.esp"),
                ),
            ),
        ):
            with self.subTest(kind=kind):
                differences = compare_mo2_profiles(projection).differences
                order = (
                    differences.mod_order
                    if kind == "mod"
                    else differences.plugin_order
                )
                self.assertTrue(order.changed)
                self.assertEqual(2, order.differing_position_count)
                self.assertEqual(0, order.first_divergence)
                self.assertEqual(3, len(order.play_sequence))
                self.assertEqual(3, len(order.lab_sequence))

    def test_configuration_excludes_semantic_lists_and_compares_other_files(self):
        play = (
            _state_file("ModLab - Play", "modlist.txt", "1"),
            _state_file("ModLab - Play", "plugins.txt", "1"),
            _state_file("ModLab - Play", "loadorder.txt", "1"),
            _state_file("ModLab - Play", "settings.ini", "1"),
            _state_file("ModLab - Play", "SkyrimPrefs.ini", "1"),
            _state_file("ModLab - Play", "PlayOnly.ini", "1"),
        )
        lab = (
            _state_file("ModLab - Lab", "modlist.txt", "2"),
            _state_file("ModLab - Lab", "plugins.txt", "2"),
            _state_file("ModLab - Lab", "loadorder.txt", "2"),
            _state_file("ModLab - Lab", "settings.ini", "2"),
            _state_file("ModLab - Lab", "SkyrimPrefs.ini", "2"),
            _state_file("ModLab - Lab", "LabOnly.ini", "2"),
        )
        config = compare_mo2_profiles(
            make_projection(play_state_files=play, lab_state_files=lab)
        ).differences.configuration

        self.assertEqual(("LabOnly.ini",), config.lab_only)
        self.assertEqual(("PlayOnly.ini",), config.play_only)
        self.assertEqual(
            ("settings.ini", "SkyrimPrefs.ini"), config.content_changed
        )

    def test_blocked_projection_retains_context_and_findings_without_differences(self):
        projection = make_projection(blocked=True)
        report = compare_mo2_profiles(projection)

        self.assertEqual(Mo2Readiness.BLOCKED, report.readiness)
        self.assertIsNone(report.adapter_state)
        self.assertIsNone(report.adapter_state_sha256)
        self.assertIsNone(report.differences)
        self.assertEqual(projection.findings, report.findings)

    def test_shared_state_is_explicit_and_identical_profiles_are_bounded_claims(self):
        projection = make_projection(
            installed_mods=(Mo2InstalledModState("SkyUI", None),),
            overwrite_entries=("Nemesis output",),
        )
        report = compare_mo2_profiles(projection)
        codes = {item.code for item in report.findings}

        self.assertEqual(("SkyUI",), report.observations.installed_mod_folders)
        self.assertEqual(("Nemesis output",), report.observations.overwrite_entries)
        self.assertEqual("not-inspected", report.observations.installed_payload_content)
        self.assertEqual("not-inspected", report.coverage.installed_payload_content)
        self.assertIn("mo2-shared-installed-content-not-fingerprinted", codes)
        self.assertIn("mo2-profile-state-no-differences", codes)
        self.assertEqual(1, sum(item.code == "overwrite-not-empty" for item in report.findings))


if __name__ == "__main__":
    unittest.main()
