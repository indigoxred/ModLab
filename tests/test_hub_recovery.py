import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from modlab.resources.mo2_hub.outputs import MANIFEST, digest, publish_output
from modlab.resources.mo2_hub.recovery import restore_previous_output, read_history


class SynthesisRecoveryTests(unittest.TestCase):
    def setUp(self):
        guard = patch('modlab.resources.mo2_hub.skse.require_game_closed')
        guard.start(); self.addCleanup(guard.stop)
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / 'mods/Patches'; self.target.mkdir(parents=True)
        self.profile = self.root / 'profiles/Test'; self.profile.mkdir(parents=True)
        self.choices = self.profile / 'modlab-patcher-choices.json'
        self.jobs = []
        for index, content in enumerate((b'old patch', b'new patch')):
            job = self.root / f'builds/synthesis/{index}'; self.jobs.append(job)
            for folder in ('output', 'Data', 'persistence'): (job / folder).mkdir(parents=True)
            (job / 'output/Patch.esp').write_bytes(content)
            (job / 'Data/settings.json').write_text(str(index))
            (job / 'persistence/ids.txt').write_text('retained IDs ' + str(index))
            hashes = {'Patch.esp': digest(job / 'output/Patch.esp')}
            choices = dict(settings={'choice': index}, input_state={'profile_path': str(self.profile)},
                build_record=str(job / 'operation.json'), output_hashes=hashes,
                extra_data=str(job / 'Data'), persistence=str(job / 'persistence'),
                settings_hashes={'settings.json': digest(job / 'Data/settings.json')})
            context = dict(choices=choices, persistence_hashes={'ids.txt': digest(job / 'persistence/ids.txt')})
            (job / 'recovery-context.json').write_text(json.dumps(context))
            (job / 'operation.json').write_text('{}')
            publish_output(self.target, job, str(self.profile), {'Patch': dict(outputs=['Patch.esp'], preset='Test',
                recovery_context_sha256=digest(job / 'recovery-context.json'))}, hashes, tool='Synthesis', replace_all=True)
            self.choices.write_text(json.dumps(choices))

    def restore(self):
        return restore_previous_output(self.target, str(self.profile), 'Synthesis', digest(self.target / MANIFEST),
                                       expected_choices=digest(self.choices))

    def test_restores_patch_settings_and_ids_together_and_can_undo(self):
        record = json.loads(self.restore().read_text())
        choices = json.loads(self.choices.read_text())
        self.assertEqual((self.target / 'Patch.esp').read_bytes(), b'old patch')
        self.assertEqual(choices['settings'], {'choice': 0})
        self.assertEqual((Path(choices['persistence']) / 'ids.txt').read_text(), 'retained IDs 0')
        self.assertEqual((Path(choices['extra_data']) / 'settings.json').read_text(), '0')
        self.assertTrue(Path(record['retained_choices']).is_file())
        self.restore()
        self.assertEqual((self.target / 'Patch.esp').read_bytes(), b'new patch')
        self.assertEqual(json.loads(self.choices.read_text())['settings'], {'choice': 1})

    def test_changed_retained_ids_stop_before_any_mutation(self):
        before = self.choices.read_bytes()
        (self.jobs[0] / 'persistence/ids.txt').write_text('manual edit')
        with self.assertRaisesRegex(ValueError, 'allocation|persistence'):
            self.restore()
        self.assertEqual(self.choices.read_bytes(), before)
        self.assertEqual((self.target / 'Patch.esp').read_bytes(), b'new patch')

    def test_stale_choices_selection_is_rejected(self):
        selected = digest(self.choices)
        self.choices.write_text('{"new":"user choice"}')
        with self.assertRaisesRegex(ValueError, 'choices changed'):
            restore_previous_output(self.target, str(self.profile), 'Synthesis', digest(self.target / MANIFEST),
                                    expected_choices=selected)
        self.assertEqual((self.target / 'Patch.esp').read_bytes(), b'new patch')

    def test_missing_context_and_pending_request_do_not_guess_recovery(self):
        (self.jobs[0] / 'recovery-context.json').unlink()
        with self.assertRaises((ValueError, OSError)): self.restore()
        self.assertEqual((self.target / 'Patch.esp').read_bytes(), b'new patch')
        (self.profile / 'modlab-patcher-pending.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'pending'): self.restore()

    def test_failed_choices_swap_restores_current_patch_and_choices(self):
        original = os.replace; before = self.choices.read_bytes()
        def fail(source, target):
            if Path(target) == self.choices: raise OSError('choices swap failed')
            return original(source, target)
        with patch('modlab.resources.mo2_hub.recovery.os.replace', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'choices swap'): self.restore()
        self.assertEqual(self.choices.read_bytes(), before)
        self.assertEqual((self.target / 'Patch.esp').read_bytes(), b'new patch')


class NpcRecoveryTests(unittest.TestCase):
    def setUp(self):
        guard = patch('modlab.resources.mo2_hub.skse.require_game_closed')
        guard.start(); self.addCleanup(guard.stop)
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / 'mods/NPC'; self.target.mkdir(parents=True)
        self.profile = self.root / 'profiles/Test'; self.profile.mkdir(parents=True)
        self.choices = self.profile / 'modlab-npc-choices.json'
        self.contexts = []
        for index in range(2):
            job = self.root / f'builds/npc/{index}'; (job / 'output').mkdir(parents=True)
            (job / 'output/NPC.esp').write_bytes(bytes([index]))
            hashes = {'NPC.esp': digest(job / 'output/NPC.esp')}
            choices = dict(selected={'001234:skyrim.esm': f'Provider {index}.esp'},
                input_state={'profile_path': str(self.profile)}, hashes=hashes,
                build_record=str(job / 'operation.json'))
            context = job / 'recovery-context.json'; self.contexts.append(context)
            context.write_text(json.dumps(choices))
            (job / 'operation.json').write_text('{}')
            publish_output(self.target, job, str(self.profile), {'Faces': dict(outputs=['NPC.esp'], preset='Selected',
                recovery_context_sha256=digest(context))}, hashes, tool='NPC Appearance', replace_all=True)
            self.choices.write_text(json.dumps(choices))

    def restore(self):
        return restore_previous_output(self.target, str(self.profile), 'NPC Appearance', digest(self.target / MANIFEST),
                                       expected_choices=digest(self.choices))

    def test_restore_faces_and_selections_together_and_undo(self):
        self.restore()
        self.assertEqual(b'\x00', (self.target / 'NPC.esp').read_bytes())
        self.assertEqual({'001234:skyrim.esm': 'Provider 0.esp'}, json.loads(self.choices.read_text())['selected'])
        self.restore()
        self.assertEqual(b'\x01', (self.target / 'NPC.esp').read_bytes())
        self.assertEqual({'001234:skyrim.esm': 'Provider 1.esp'}, json.loads(self.choices.read_text())['selected'])

    def test_changed_context_and_pending_choices_stop_before_restore(self):
        self.contexts[0].write_text('{}')
        with self.assertRaisesRegex(ValueError, 'context'): self.restore()
        self.assertEqual(b'\x01', (self.target / 'NPC.esp').read_bytes())
        (self.profile / 'modlab-npc-pending.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'pending'): self.restore()

    def test_failed_choice_publication_puts_current_faces_back(self):
        original = os.replace; before = self.choices.read_bytes()
        def fail(source, target):
            if Path(target) == self.choices: raise OSError('choices swap failed')
            return original(source, target)
        with patch('modlab.resources.mo2_hub.recovery.os.replace', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'choices swap'): self.restore()
        self.assertEqual(before, self.choices.read_bytes())
        self.assertEqual(b'\x01', (self.target / 'NPC.esp').read_bytes())


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        guard = patch('modlab.resources.mo2_hub.skse.require_game_closed')
        guard.start(); self.addCleanup(guard.stop)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / 'mods/Generated'
        self.target.mkdir(parents=True)
        self.profile = str(self.root / 'profiles/Test')
        for index, value in enumerate((b'old', b'new')):
            job = self.root / f'builds/body/{index}'
            (job / 'output/meshes').mkdir(parents=True)
            source = job / 'output/meshes/body.nif'
            source.write_bytes(value)
            (job / 'operation.json').write_text(json.dumps({'profile_path': self.profile, 'status': 'Applied'}))
            publish_output(self.target, job, self.profile,
                {'Body': {'outputs': ['meshes/body.nif'], 'preset': 'Test', 'source_mod': 'Test body'}},
                {'meshes/body.nif': digest(source)})

    def test_restore_keeps_replaced_output_and_original_backup(self):
        expected = digest(self.target / MANIFEST)
        record = restore_previous_output(self.target, self.profile, 'BodySlide', expected)
        self.assertEqual((self.target / 'meshes/body.nif').read_bytes(), b'old')
        data = json.loads(record.read_text())
        self.assertEqual((Path(data['retained_output']) / 'meshes/body.nif').read_bytes(), b'new')
        self.assertEqual((self.root / 'builds/body/1/previous-output/meshes/body.nif').read_bytes(), b'old')
        self.assertEqual(data['status'], 'Previous output restored; effective check pending')

    def test_restoration_itself_can_be_undone(self):
        restore_previous_output(self.target, self.profile, 'BodySlide', digest(self.target / MANIFEST))
        restore_previous_output(self.target, self.profile, 'BodySlide', digest(self.target / MANIFEST))
        self.assertEqual((self.target / 'meshes/body.nif').read_bytes(), b'new')

    def test_restore_refuses_edits_in_current_or_backup(self):
        for path in (self.target / 'meshes/body.nif', self.root / 'builds/body/1/previous-output/meshes/body.nif'):
            previous = path.read_bytes()
            path.write_bytes(b'manual edit')
            with self.assertRaisesRegex(ValueError, 'changed'):
                restore_previous_output(self.target, self.profile, 'BodySlide', digest(self.target / MANIFEST))
            self.assertEqual(path.read_bytes(), b'manual edit')
            path.write_bytes(previous)

    def test_restore_refuses_stale_selection_wrong_profile_and_external_backup(self):
        with self.assertRaisesRegex(ValueError, 'changed'):
            restore_previous_output(self.target, self.profile, 'BodySlide', 'old selected manifest')
        with self.assertRaisesRegex(ValueError, 'another profile'):
            restore_previous_output(self.target, 'Another', 'BodySlide', digest(self.target / MANIFEST))
        data = json.loads((self.target / MANIFEST).read_text())
        data['previous_output'] = str(self.root / 'outside')
        (self.target / MANIFEST).write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            restore_previous_output(self.target, self.profile, 'BodySlide', digest(self.target / MANIFEST))
        self.assertEqual((self.target / 'meshes/body.nif').read_bytes(), b'new')

    def test_failed_swap_puts_current_output_back(self):
        original = os.replace
        def fail(source, target):
            if Path(source).name == 'pending-output':
                raise OSError('simulated swap failure')
            return original(source, target)
        with patch('modlab.resources.mo2_hub.recovery.os.replace', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'swap failure'):
                restore_previous_output(self.target, self.profile, 'BodySlide', digest(self.target / MANIFEST))
        self.assertEqual((self.target / 'meshes/body.nif').read_bytes(), b'new')

    def test_history_includes_legacy_instance_records_and_marks_other_profiles(self):
        other = self.root / 'reports/setup/other'
        other.mkdir(parents=True)
        (other / 'operation.json').write_text(json.dumps({'profile_path': 'Other', 'status': 'Failed'}))
        malformed = self.root / 'reports/setup/malformed'
        malformed.mkdir()
        (malformed / 'operation.json').write_text('{')
        rows = read_history(self.root, self.root / 'plugin-data', self.profile, 'Test')
        self.assertEqual(len(rows), 4)
        self.assertTrue(any(row.status == 'Unreadable record' for row in rows))
        self.assertTrue(any(row.scope == 'Other profile' for row in rows))
        self.assertTrue(any(row.scope == 'This profile' for row in rows))
