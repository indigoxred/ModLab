"""Product findings from MO2 observations, without launching the desktop."""

import unittest

from modlab.resources.mo2_hub import assessment


class HubAssessmentTests(unittest.TestCase):
    def test_missing_ussep_on_1170_links_the_checked_archive_instead_of_latest(self):
        from dataclasses import replace
        setup = replace(self.snapshot([self.plugin('Example.esp', 1, ['Unofficial Skyrim Special Edition Patch.esp'])]),
                        runtime='1.6.1170.0')
        finding = next(f for f in assessment.assess(setup).findings if f.code == 'missing-master')
        self.assertIn('file_id=733846', finding.action)
        self.assertIn('4.3.8a', finding.action)

    def test_missing_official_content_advice_preserves_downgraded_setup(self):
        from dataclasses import replace
        for master in ('Skyrim.esm', 'ccBGSSSE001-Fish.esm'):
            setup = replace(self.snapshot([self.plugin('Example.esp', 1, [master])]), runtime='1.6.1170.0')
            finding = next(f for f in assessment.assess(setup).findings if f.code == 'missing-master')
            self.assertIn('backup', finding.action.lower())
            self.assertIn('downgrad', finding.action.lower())
            self.assertIn('1.6.1170', finding.action)

    def test_shared_dependency_is_one_action_with_all_affected_mods(self):
        result = assessment.assess(self.snapshot([
            assessment.Plugin('First.esp', 1, ('Unofficial Skyrim Special Edition Patch.esp',), 'First mod'),
            assessment.Plugin('Second.esp', 2, ('Unofficial Skyrim Special Edition Patch.esp',), 'Second mod'),
            assessment.Plugin('Disabled.esp', -1, ('Unofficial Skyrim Special Edition Patch.esp',), 'Disabled mod'),
        ]))
        missing = [f for f in result.findings if f.code == 'missing-master']
        self.assertEqual(len(missing), 1)
        self.assertIn('First mod', missing[0].detail)
        self.assertIn('Second mod', missing[0].detail)
        self.assertNotIn('Disabled mod', missing[0].detail)
        self.assertIn('https://www.nexusmods.com/skyrimspecialedition/mods/266', missing[0].action)
        self.assertIn('Recheck and finish setup', missing[0].action)

    def test_missing_game_master_is_not_described_as_a_downloadable_mod(self):
        result = assessment.assess(self.snapshot([self.plugin('Example.esp', 1, ['Skyrim.esm'])]))
        missing = next(f for f in result.findings if f.code == 'missing-master')
        self.assertIn('game installation', missing.action)
        self.assertNotIn('nexusmods', missing.action)

    def snapshot(self, plugins=(), assets=(), **kwargs):
        return assessment.SetupSnapshot(
            game_name="Skyrim Special Edition", runtime="1.7.104.0",
            profile="Adventure", game_root="C:/Games/Skyrim",
            plugins=tuple(plugins), assets=tuple(assets), **kwargs,
        )

    def plugin(self, name, order, masters=()):
        return assessment.Plugin(name, order, tuple(masters), "My mod")

    def test_distinguishes_missing_disabled_and_late_masters(self):
        result = assessment.assess(self.snapshot([
            self.plugin("Follower.esp", 1, ["Missing.esm", "Disabled.esm", "Late.esm"]),
            self.plugin("Disabled.esm", -1), self.plugin("Late.esm", 2),
        ]))
        findings = {f.code: f for f in result.findings}
        self.assertIn("missing-master", findings)
        self.assertIn("inactive-master", findings)
        self.assertIn("master-order", findings)
        self.assertIn("Missing.esm", findings["missing-master"].detail)
        self.assertIn("Follower.esp", findings["missing-master"].detail)
        self.assertTrue(result.has_blockers)

    def test_disabled_plugins_do_not_block_active_setup(self):
        result = assessment.assess(self.snapshot([
            self.plugin("Unused.esp", -1, ["Missing.esm"]),
        ]))
        self.assertFalse(result.has_blockers)

    def test_master_identity_is_case_insensitive(self):
        result = assessment.assess(self.snapshot([
            self.plugin("Base.esm", 0), self.plugin("Child.esp", 1, ["BASE.ESM"]),
        ]))
        self.assertFalse(result.has_blockers)

    def test_skeleton_overwrite_is_review_not_automatic_incompatibility(self):
        result = assessment.assess(self.snapshot(assets=[assessment.Asset(
            "meshes/actors/character/character assets/skeleton.nif",
            ("XPMSSE", "Ragdoll patch"),
        )]))
        overlap = next(f for f in result.findings if f.code == "asset-overlap")
        self.assertEqual("Review", overlap.level)
        self.assertIn("Ragdoll patch", overlap.detail)
        self.assertFalse(result.has_blockers)

    def test_empty_findings_do_not_claim_game_or_features_verified(self):
        result = assessment.assess(self.snapshot())
        self.assertIn("not yet checked", result.summary.lower())
        self.assertNotIn("ready to play", result.summary.lower())

    def test_incomplete_host_observation_cannot_be_reported_clean(self):
        result = assessment.assess(self.snapshot(errors=("Cannot read plugin masters",)))
        self.assertTrue(any(f.code == "inspection-incomplete" for f in result.findings))
        self.assertIn("incomplete", result.summary.lower())

    def test_native_plugin_requires_loader_but_does_not_prove_version_support(self):
        result = assessment.assess(self.snapshot(assets=[assessment.Asset(
            "SKSE/Plugins/SomePlugin.dll", ("Unknown mod",),
        )], skse_loader_present=False))
        self.assertTrue(any(f.code == "skse-loader-missing" for f in result.findings))
        self.assertTrue(any(f.code == "native-compatibility-unverified" for f in result.findings))

    def test_unsupported_game_does_not_receive_skyrim_advice(self):
        setup = assessment.SetupSnapshot("Enderal Special Edition", "", "P", "C:/Enderal")
        result = assessment.assess(setup)
        self.assertTrue(result.has_blockers)
        self.assertEqual(["unsupported-game"], [f.code for f in result.findings])

    def test_hundreds_of_mesh_overlaps_form_one_actionable_group_without_losing_paths(self):
        assets = [assessment.Asset(f'meshes/armor/outfit{i}.nif', ('Generated outfits', 'Base outfits'))
                  for i in range(841)]
        result = assessment.assess(self.snapshot(assets=assets))
        groups = [f for f in result.findings if f.code == 'asset-overlap']
        self.assertEqual(1, len(groups))
        self.assertIn('841', groups[0].explanation)
        self.assertIn('Winning provider: Generated outfits', groups[0].detail)
        self.assertIn('meshes/armor/outfit840.nif', groups[0].detail)

    def test_skeletons_and_reversed_winners_remain_separate_decisions(self):
        result = assessment.assess(self.snapshot(assets=[
            assessment.Asset('meshes/armor/a.nif', ('A', 'B')),
            assessment.Asset('meshes/armor/b.nif', ('B', 'A')),
            assessment.Asset('meshes/actors/character/skeleton_female.nif', ('A', 'B')),
        ]))
        self.assertEqual(3, len(result.findings))
        skeleton = next(f for f in result.findings if 'skeleton' in f.title.lower())
        self.assertIn('skeleton', skeleton.action.lower())
        self.assertFalse(result.has_blockers)
