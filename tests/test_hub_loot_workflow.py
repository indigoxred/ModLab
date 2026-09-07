from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as Obj
import unittest

from modlab.resources.mo2_hub.loot_workflow import apply_order, context_signature, prepare_loot_input
from modlab.resources.mo2_hub.assessment import Plugin


class LootApplyTests(unittest.TestCase):
    def test_cached_load_positions_do_not_invalidate_unchanged_priority_order(self):
        with TemporaryDirectory() as directory:
            host, order, calls = self.host(Path(directory))
            cached = list(order)
            host.pluginList().loadOrder = cached.index
            order[:] = ['Skyrim.esm', 'B.esp', 'A.esp']
            before = context_signature(host)
            cached[:] = order  # MO2 refreshes its cached active positions on restart.
            self.assertEqual(before, context_signature(host))
            host.pluginList().loadOrder = lambda name: -1 if name == 'B.esp' else cached.index(name)
            self.assertNotEqual(before, context_signature(host))

    def setUp(self):
        from unittest.mock import patch
        guard = patch('modlab.resources.mo2_hub.skse.require_game_closed')
        guard.start(); self.addCleanup(guard.stop)

    def test_partial_change_followed_by_host_exception_is_restored(self):
        with TemporaryDirectory() as directory:
            host, order, calls = self.host(Path(directory))
            def fail_once(names):
                calls.append(tuple(names))
                order[:] = names
                if len(calls) == 1:
                    raise RuntimeError('host error after changing order')
            host.pluginList().setLoadOrder = fail_once
            with self.assertRaisesRegex(RuntimeError, 'restored'):
                apply_order(host, ('Skyrim.esm', 'B.esp', 'A.esp'), context_signature(host))
            self.assertEqual(['Skyrim.esm', 'A.esp', 'B.esp'], order)

    def test_loot_writes_its_proposal_into_an_independent_profile_copy(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = prepare_loot_input(root, (Plugin('Skyrim.esm', 0), Plugin('Disabled.esp', -1)),
                                        ('Skyrim.esm', 'Disabled.esp'))
            self.assertEqual(root / 'loadorder.txt', source)
            self.assertEqual('Skyrim.esm\nDisabled.esp\n', source.read_text())
            self.assertEqual('*Skyrim.esm\nDisabled.esp\n', (root / 'plugins.txt').read_text())

    def host(self, root):
        order = ['Skyrim.esm', 'A.esp', 'B.esp']
        calls = []
        def set_order(names):
            calls.append(tuple(names))
            order[:] = names
        plugins = Obj(pluginNames=lambda: list(order), priority=order.index,
                      loadOrder=order.index, origin=lambda name: 'data',
                      masters=lambda name: [] if name == 'Skyrim.esm' else ['Skyrim.esm'],
                      setLoadOrder=set_order)
        return Obj(pluginList=lambda: plugins, profilePath=lambda: str(root / 'profile'),
                   modsPath=lambda: str(root / 'mods'), resolvePath=lambda name: str(root / name)), order, calls

    def test_applies_current_proposal_and_keeps_previous_order(self):
        with TemporaryDirectory() as directory:
            host, order, calls = self.host(Path(directory))
            signature = context_signature(host)
            result = apply_order(host, ('Skyrim.esm', 'B.esp', 'A.esp'), signature)
            self.assertEqual(('Skyrim.esm', 'A.esp', 'B.esp'), result)
            self.assertEqual(['Skyrim.esm', 'B.esp', 'A.esp'], order)

    def test_changed_profile_cannot_receive_an_old_proposal(self):
        with TemporaryDirectory() as directory:
            host, order, calls = self.host(Path(directory))
            signature = context_signature(host)
            host.profilePath = lambda: 'different profile'
            with self.assertRaisesRegex(ValueError, 'changed'):
                apply_order(host, ('Skyrim.esm', 'B.esp', 'A.esp'), signature)
            self.assertEqual([], calls)

    def test_plugin_file_change_invalidates_old_advice(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            host, order, calls = self.host(root)
            (root / 'A.esp').write_bytes(b'before')
            signature = context_signature(host)
            (root / 'A.esp').write_bytes(b'changed content')
            with self.assertRaisesRegex(ValueError, 'changed'):
                apply_order(host, ('Skyrim.esm', 'B.esp', 'A.esp'), signature)
            self.assertEqual([], calls)

    def test_host_rejecting_order_is_reported_and_previous_order_restored(self):
        with TemporaryDirectory() as directory:
            host, order, calls = self.host(Path(directory))
            plugins = host.pluginList()
            plugins.setLoadOrder = lambda names: calls.append(tuple(names))
            with self.assertRaisesRegex(RuntimeError, 'did not apply'):
                apply_order(host, ('Skyrim.esm', 'B.esp', 'A.esp'), context_signature(host))
            self.assertEqual(2, len(calls))
            self.assertEqual(('Skyrim.esm', 'A.esp', 'B.esp'), calls[-1])
