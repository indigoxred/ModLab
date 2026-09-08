from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as Obj
from unittest.mock import Mock, patch
import unittest

from modlab.resources.mo2_hub.assessment import Plugin, SetupSnapshot, Finding, assess
from modlab.resources.mo2_hub.resolution import resolution_context, enable_dependency, web_links


class ResolutionTests(unittest.TestCase):
    def test_missing_dependency_names_all_affected_mods_and_uses_existing_source_advice(self):
        setup = SetupSnapshot('Skyrim Special Edition', '1.6.1170', 'Test', 'C:/Game', plugins=(
            Plugin('A.esp', 0, ('SkyUI_SE.esp',), 'First mod'),
            Plugin('B.esp', 1, ('SkyUI_SE.esp',), 'Second mod')))
        missing = next(f for f in assess(setup).findings if f.code == 'missing-master')
        context = resolution_context(missing, setup)
        self.assertEqual('SkyUI_SE.esp', context.dependency)
        self.assertEqual(('First mod', 'Second mod'), context.providers)
        self.assertEqual(('A.esp', 'B.esp'), context.dependents)
        self.assertEqual(('https://www.nexusmods.com/skyrimspecialedition/mods/12604',), context.links)
        self.assertFalse(context.can_enable)

    def test_installed_disabled_dependency_offers_enable_not_another_download(self):
        setup = SetupSnapshot('Skyrim Special Edition', '1.6.1170', 'Test', 'C:/Game', plugins=(
            Plugin('Base.esp', -1, (), 'Foundation'), Plugin('A.esp', 0, ('Base.esp',), 'Follower')))
        item = next(f for f in assess(setup).findings if f.code == 'inactive-master')
        context = resolution_context(item, setup)
        self.assertTrue(context.can_enable)
        self.assertEqual('Foundation', context.dependency_provider)

    def test_record_instructions_open_the_affected_provider(self):
        setup = SetupSnapshot('Skyrim Special Edition', '1.6.1170', 'Test', 'C:/Game',
            plugins=(Plugin('Magic.esp', 0, (), 'My magic mod'),))
        item = Finding('Review', 'record-errors', 'Record problems reported: My magic mod',
            'Magic.esp — 2 record errors\nSome details', 'Look for a matching patch.')
        self.assertEqual(('My magic mod',), resolution_context(item, setup).providers)

    def test_links_preserve_queries_and_do_not_include_markdown_delimiters(self):
        text = 'Open [files](https://www.nexusmods.com/skyrimspecialedition/mods/1?tab=files&file_id=22). '
        self.assertEqual(('https://www.nexusmods.com/skyrimspecialedition/mods/1?tab=files&file_id=22',), web_links(text))
        self.assertEqual((), web_links('javascript:bad file:///secret'))

    def host(self, root, fail=False):
        profile = root / 'profile'; profile.mkdir()
        (profile / 'plugins.txt').write_text('*A.esp\nBase.esp\n')
        states = {'Base.esp': 0, 'A.esp': 1}
        def set_state(name, value):
            states[name] = value
            if fail and value == 1:
                raise RuntimeError('host failed after setting state')
        plugins = Obj(pluginNames=lambda: list(states), state=lambda n: states[n], setState=Mock(side_effect=set_state),
                      origin=lambda n: 'Foundation', masters=lambda n: ())
        host = Obj(profilePath=lambda: str(profile), modsPath=lambda: str(root / 'mods'),
                   pluginList=lambda: plugins)
        return host, states

    def test_enable_checks_identity_records_backup_and_changes_only_named_plugin(self):
        with TemporaryDirectory() as tmp:
            host, states = self.host(Path(tmp))
            with patch('modlab.resources.mo2_hub.skse.require_game_closed'):
                record = enable_dependency(host, 'Base.esp', 'Foundation', host.profilePath(), 1)
            self.assertEqual({'Base.esp': 1, 'A.esp': 1}, states)
            host.pluginList().setState.assert_called_once_with('Base.esp', 1)
            self.assertTrue(record.is_file())
            self.assertEqual('*A.esp\nBase.esp\n', (record.parent / 'plugins.txt.before').read_text())

    def test_profile_change_and_provider_change_leave_plugin_untouched(self):
        with TemporaryDirectory() as tmp:
            host, states = self.host(Path(tmp))
            with patch('modlab.resources.mo2_hub.skse.require_game_closed'):
                for provider, profile in (('Different provider', host.profilePath()), ('Foundation', 'Other profile')):
                    with self.assertRaises(ValueError):
                        enable_dependency(host, 'Base.esp', provider, profile, 1)
            host.pluginList().setState.assert_not_called()

    def test_partial_host_failure_restores_disabled_state(self):
        with TemporaryDirectory() as tmp:
            host, states = self.host(Path(tmp), fail=True)
            with patch('modlab.resources.mo2_hub.skse.require_game_closed'):
                with self.assertRaisesRegex(RuntimeError, 'restored'):
                    enable_dependency(host, 'Base.esp', 'Foundation', host.profilePath(), 1)
            self.assertEqual(0, states['Base.esp'])


if __name__ == '__main__':
    unittest.main()
