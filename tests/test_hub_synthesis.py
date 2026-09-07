import copy
from pathlib import Path
import struct
import tempfile
import unittest

from modlab.resources.mo2_hub.synthesis import inspect_pipeline, verify_output, command_line, selected_settings
from modlab.resources.mo2_hub.synthesis_workflow import copy_settings


def pipeline():
    return {'Version': 2, 'Profiles': [{'ID': 'test', 'TargetRelease': 'SkyrimSE',
        'Groups': [{'On': True, 'Name': 'Chosen Patch', 'Patchers': [{
            '$type': 'Synthesis.Bethesda.Execution.Settings.GithubPatcherSettings, Synthesis.Bethesda.Execution',
            'On': True, 'Nickname': 'Chosen patcher', 'ID': 'patcher',
            'RemoteRepoPath': 'https://github.com/example/patcher',
            'SelectedProjectSubpath': 'Patcher/Patcher.csproj',
            'PatcherVersioning': 'Commit', 'TargetCommit': 'a' * 40}]}]}]}


def plugin(master='Skyrim.esm'):
    value = master.encode() + b'\0'
    data = b'HEDR' + struct.pack('<HfII', 12, 1.7, 0, 2048)
    data += b'MAST' + struct.pack('<H', len(value)) + value + b'DATA\x08\0' + bytes(8)
    return b'TES4' + struct.pack('<I', len(data)) + bytes(16) + data


class SynthesisTests(unittest.TestCase):
    def test_failed_preparation_stops_before_patch_execution_and_retains_log(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from modlab.resources.mo2_hub.synthesis_workflow import prepare_compilation
        with tempfile.TemporaryDirectory() as tmp:
            job = SimpleNamespace(directory=Path(tmp), record={}, save=lambda:None)
            def fail(args, **options):
                options['stdout'].write(b'compiler failed')
                return SimpleNamespace(returncode=7)
            with patch('modlab.resources.mo2_hub.synthesis_workflow.subprocess.run', side_effect=fail):
                with self.assertRaisesRegex(ValueError, 'preparation failed'):
                    prepare_compilation(job, ['Synthesis.exe', 'run-pipeline'])
            self.assertEqual(b'compiler failed', (Path(tmp)/'prepare.log').read_bytes())
            self.assertEqual(7, job.record['prepare_exit_code'])

    def test_large_override_list_in_master_header_uses_extended_size(self):
        from modlab.resources.mo2_hub.synthesis import plugin_masters
        with tempfile.TemporaryDirectory() as tmp:
            original = plugin()
            extended = b'XXXX' + struct.pack('<HI', 4, 70000) + b'ONAM\x00\x00' + bytes(70000)
            data = original[24:] + extended
            path = Path(tmp) / 'Master.esp'
            path.write_bytes(original[:4] + struct.pack('<I', len(data)) + original[8:24] + data)
            self.assertEqual(('Skyrim.esm',), plugin_masters(path))
            path.write_bytes(path.read_bytes()[:-10])
            with self.assertRaises(ValueError): plugin_masters(path)

    def test_pending_choice_survives_failure_and_only_matching_attempt_can_clear_it(self):
        from modlab.resources.mo2_hub.synthesis import begin_pending, load_pending, update_pending, clear_pending
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            successful = root / 'modlab-patcher-choices.json'
            successful.write_text('{"previous":"successful build"}')
            attempt = begin_pending(root, pipeline(), 'chosen/Data', 'chosen/persistence')
            update_pending(root, attempt, error='Missing SDK', build_record='job/operation.json')
            pending = load_pending(root)
            self.assertEqual(pending['settings'], pipeline())
            self.assertEqual(pending['error'], 'Missing SDK')
            self.assertEqual(pending['extra_data'], 'chosen/Data')
            self.assertEqual(successful.read_text(), '{"previous":"successful build"}')
            next_attempt = begin_pending(root, pipeline())
            with self.assertRaisesRegex(ValueError, 'changed'):
                clear_pending(root, attempt)
            self.assertEqual(load_pending(root)['id'], next_attempt)
            clear_pending(root, next_attempt)
            self.assertIsNone(load_pending(root))
            self.assertTrue(successful.exists())

    def test_disabled_patchers_are_not_passed_to_cli_preparation(self):
        source = pipeline()
        source['Profiles'][0]['Groups'][0]['Patchers'].append({'On': False, '$type': 'Unsupported'})
        source['Profiles'][0]['Groups'].append({'On': False, 'Name': 'Unused', 'Patchers': [{'On': True}]})
        selected = selected_settings(source)
        self.assertEqual(len(selected['Profiles'][0]['Groups']), 1)
        self.assertEqual(len(selected['Profiles'][0]['Groups'][0]['Patchers']), 1)
        self.assertEqual(len(source['Profiles'][0]['Groups']), 2)

    def test_saved_options_are_copied_and_missing_settings_do_not_fall_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'previous'; source.mkdir()
            (source / 'Patcher').mkdir(); (source / 'Patcher/settings.json').write_text('{"option":false}')
            copied = copy_settings(source, root / 'next')
            self.assertIn('Patcher/settings.json', copied)
            self.assertEqual((root / 'next/Patcher/settings.json').read_text(), '{"option":false}')
            with self.assertRaisesRegex(ValueError, 'missing'):
                copy_settings(root / 'absent', root / 'another')

    def test_inspection_preserves_choices_and_requires_exact_revision(self):
        source = pipeline(); before = copy.deepcopy(source)
        result = inspect_pipeline(source)
        self.assertEqual(result.outputs, ('Chosen Patch.esp',))
        self.assertEqual(source, before)
        source['Profiles'][0]['Groups'][0]['Patchers'][0]['PatcherVersioning'] = 'Branch'
        with self.assertRaisesRegex(ValueError, 'revision'):
            inspect_pipeline(source)

    def test_missing_mods_wrong_game_and_duplicate_outputs_are_rejected(self):
        for field, value in [('IgnoreMissingMods', True), ('TargetRelease', 'SkyrimLE')]:
            source = pipeline(); source['Profiles'][0][field] = value
            with self.assertRaises(ValueError): inspect_pipeline(source)
        source = pipeline(); source['Profiles'][0]['Groups'] *= 2
        with self.assertRaisesRegex(ValueError, 'duplicate'): inspect_pipeline(source)

    def test_zero_exit_does_not_accept_missing_group_or_export_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'Chosen Patch.esp').write_bytes(plugin())
            with self.assertRaisesRegex(ValueError, 'completion'):
                verify_output(root, ('Chosen Patch.esp',), '', 0, ['Skyrim.esm'])
            log = 'Files to export:\n   Chosen Patch.esp\nExported patch to workspace: elsewhere\nExported patch to final destination: ' + str(root)
            hashes = verify_output(root, ('Chosen Patch.esp',), log, 0, ['Skyrim.esm'])
            self.assertEqual(set(hashes), {'Chosen Patch.esp'})
            with self.assertRaises(ValueError):
                verify_output(root, ('Chosen Patch.esp', 'Absent.esp'), log, 0, ['Skyrim.esm'])

    def test_missing_master_or_unexpected_file_cannot_be_applied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); target = root / 'Chosen Patch.esp'; target.write_bytes(plugin('Missing.esm'))
            log = 'Files to export:\n   Chosen Patch.esp\nExported patch to final destination: ' + str(root)
            with self.assertRaisesRegex(ValueError, 'master'):
                verify_output(root, ('Chosen Patch.esp',), log, 0, ['Skyrim.esm'])
            target.write_bytes(plugin()); (root / 'Unexpected.esp').write_bytes(plugin())
            with self.assertRaisesRegex(ValueError, 'Unexpected'):
                verify_output(root, ('Chosen Patch.esp',), log, 0, ['Skyrim.esm'])

    def test_console_capture_quotes_spaces_and_rejects_expansion(self):
        command = command_line(['C:/My Tools/Synthesis.exe', 'run-pipeline'], 'C:/My Build/tool.log')
        self.assertIn('"C:/My Tools/Synthesis.exe"', command)
        for value in ['%PATH%', 'x" y', 'x\ny', '!value!']:
            with self.assertRaises(ValueError): command_line([value], 'log.txt')
