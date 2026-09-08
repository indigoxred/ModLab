from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from modlab.resources.mo2_hub.assessment import Asset, Plugin, SetupSnapshot
from modlab.resources.mo2_hub import shape_support
from modlab.resources.mo2_hub.guidance import finding_action
from modlab.resources.mo2_hub.resolution import resolution_context


class ShapeSupportTests(unittest.TestCase):
    def setup(self, root, plugins=(), assets=()):
        return SetupSnapshot('Skyrim Special Edition', '1.6.1170.0', 'Test', str(root),
            plugins=plugins, assets=assets, archives_inspected=True)

    def resolver(self, root):
        return lambda path: str(root/path)

    def put(self, root, path, content=b'installed'):
        p=root/path; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(content)

    def test_unrequested_helper_is_not_required_for_an_ordinary_profile(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            self.assertEqual((), shape_support.inspect_support(self.setup(root), self.resolver(root)))
            findings=shape_support.inspect_support(self.setup(root), self.resolver(root), requested=True)
            self.assertTrue(any('OBody' in f.title and '77016' in f.action for f in findings))
            self.assertTrue(any('RaceMenu' in f.title and '19080' in f.action for f in findings))
            self.assertTrue(all(finding_action(f)[0] == 'resolve_finding' for f in findings))

    def test_effective_dll_triggers_requirements_even_if_plugin_is_missing(self):
        with TemporaryDirectory() as folder:
            root=Path(folder); self.put(root, 'SKSE/Plugins/OBody.dll')
            findings=shape_support.inspect_support(self.setup(root,
                assets=(Asset('SKSE/Plugins/OBody.dll', ('Shapes',)),)), self.resolver(root))
            self.assertTrue(any('OBody' in f.title for f in findings))
            self.assertTrue(any('PapyrusUtil' in f.title for f in findings))
            self.assertTrue(any('UIExtensions' in f.title for f in findings))

    def test_disabled_dependency_uses_enable_flow_instead_of_redownload(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            plugins=(Plugin('OBody.esp', 4, origin='Shapes'), Plugin('UIExtensions.esp', -1, origin='UI Extensions'))
            setup=self.setup(root, plugins)
            findings=shape_support.inspect_support(setup, self.resolver(root))
            finding=next(f for f in findings if f.code=='shape-support-dependency-disabled' and f.detail.startswith('UIExtensions.esp'))
            context=resolution_context(finding, setup)
            self.assertTrue(context.can_enable)
            self.assertEqual('UIExtensions.esp', context.dependency)
            self.assertEqual(('Shapes',), context.providers)

    def test_disabled_helper_plugin_can_be_enabled_when_its_dll_is_active(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            setup=self.setup(root, (Plugin('OBody.esp', -1, origin='Shapes'),),
                (Asset('SKSE/Plugins/OBody.dll', ('Shapes',)),))
            findings=shape_support.inspect_support(setup, self.resolver(root))
            finding=next(f for f in findings if f.code=='shape-support-dependency-disabled')
            self.assertTrue(resolution_context(finding, setup).can_enable)

    def test_configuration_checks_real_features_section_without_changing_it(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            ini=b'[Features]\nbEnableBodyMorph=0 ; disabled\nbEnableBodyGen=1 ; deliberate\n[General]\nbEnableBodyGen=0\n'
            self.put(root, 'SKSE/Plugins/skee64.ini', ini)
            findings=shape_support.inspect_support(self.setup(root), self.resolver(root), requested=True)
            self.assertTrue(any(f.code=='shape-support-morph-disabled' for f in findings))
            self.assertTrue(any(f.code=='shape-support-controller-choice' for f in findings))
            self.assertEqual(ini, (root/'SKSE/Plugins/skee64.ini').read_bytes())

    def test_packed_scripts_count_as_present_but_packed_native_dll_does_not(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            assets=(Asset('scripts/PapyrusUtil.pex', ('Utilities',), 'utilities.bsa'),
                    Asset('scripts/StorageUtil.pex', ('Utilities',), 'utilities.bsa'),
                    Asset('scripts/JsonUtil.pex', ('Utilities',), 'utilities.bsa'),
                    Asset('scripts/MiscUtil.pex', ('Utilities',), 'utilities.bsa'))
            self.put(root, 'SKSE/Plugins/PapyrusUtil.dll')
            findings=shape_support.inspect_support(self.setup(root, assets=assets), self.resolver(root), requested=True)
            self.assertFalse(any('PapyrusUtil' in f.title for f in findings))
            (root/'SKSE/Plugins/PapyrusUtil.dll').unlink()
            assets=(*assets, Asset('SKSE/Plugins/PapyrusUtil.dll', ('Utilities',), 'utilities.bsa'))
            findings=shape_support.inspect_support(self.setup(root, assets=assets), self.resolver(root), requested=True)
            self.assertTrue(any('PapyrusUtil' in f.title for f in findings))

    def test_missing_script_without_archive_inspection_is_unknown_not_proven_missing(self):
        with TemporaryDirectory() as folder:
            root=Path(folder); self.put(root, 'SKSE/Plugins/PapyrusUtil.dll')
            setup=replace(self.setup(root), archives_inspected=False)
            findings=shape_support.inspect_support(setup, self.resolver(root), requested=True)
            finding=next(f for f in findings if 'PapyrusUtil' in f.title)
            self.assertEqual('Unknown', finding.level)

    def test_duplicate_settings_do_not_pass_as_configured(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            self.put(root, 'SKSE/Plugins/skee64.ini', b'[Features]\nbEnableBodyGen=0\nbEnableBodyGen=1\n')
            findings=shape_support.inspect_support(self.setup(root), self.resolver(root), requested=True)
            self.assertTrue(any(f.code=='shape-support-config-unreadable' for f in findings))


if __name__=='__main__': unittest.main()
