from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


class InstallQueueTests(unittest.TestCase):
    def test_malformed_history_does_not_block_pending_batch_preparation(self):
        import json
        import os
        from modlab.resources.mo2_hub.install_queue import (
            create_queue, begin_item, finish_item, latest_unprepared)
        with TemporaryDirectory() as tmp:
            root = Path(tmp); archive = root/'mod.zip'; archive.write_bytes(b'archive')
            queue = create_queue(root, 'A', [archive])
            begin_item(queue, str(archive), 'A')
            finish_item(queue, 'Installed, enabled', 'receipt.json')
            malformed = queue.path.parent.parent/'malformed'/'operation.json'
            malformed.parent.mkdir()
            for data in ([], {'version': 1, 'profile_path': 'A', 'items': [None]}):
                with self.subTest(data=data):
                    malformed.write_text(json.dumps(data), encoding='utf-8')
                    stamp = queue.path.stat().st_mtime + 10
                    os.utime(malformed, (stamp, stamp))
                    self.assertEqual(queue.path, latest_unprepared(root, 'A').path)
                    self.assertEqual(data, json.loads(malformed.read_text(encoding='utf-8')))

    def test_restart_reconnects_completed_batch_to_preparation_without_reinstalling(self):
        from modlab.resources.mo2_hub.install_queue import (create_queue,begin_item,finish_item,
            latest_unprepared,resumable_items,record_setup)
        with TemporaryDirectory() as tmp:
            root=Path(tmp);archive=root/'mod.zip';archive.write_bytes(b'archive')
            queue=create_queue(root,'A',[archive])
            self.assertIsNone(latest_unprepared(root,'A'))
            begin_item(queue,str(archive),'A');finish_item(queue,'Installed, enabled','receipt.json')
            create_queue(root,'B',[archive])
            restored=latest_unprepared(root,'A')
            self.assertEqual(queue.path,restored.path)
            self.assertEqual([],resumable_items(restored))
            self.assertEqual('receipt.json',restored.data['items'][0]['record'])
            record_setup(restored,'A',root/'setup.json',needs_attention=True)
            self.assertIsNone(latest_unprepared(root,'A'))

    def test_final_setup_result_replaces_pending_status_and_tracks_later_recheck(self):
        from modlab.resources.mo2_hub.install_queue import create_queue, begin_item, finish_item, record_setup, load_queue
        with TemporaryDirectory() as tmp:
            root = Path(tmp); archive = root/'a.zip'; archive.write_bytes(b'archive')
            queue = create_queue(root, 'A', [archive])
            begin_item(queue, str(archive), 'A'); finish_item(queue, 'Installed, enabled', 'installer.json')
            record_setup(queue, 'A', root/'failed-setup.json', needs_attention=True)
            saved = load_queue(queue.path, 'A')
            self.assertEqual('Installations completed; setup needs attention', saved.data['status'])
            self.assertEqual(str(root/'failed-setup.json'), saved.data['setup_record'])
            record_setup(queue, 'A', root/'final-setup.json', needs_attention=False)
            saved = load_queue(queue.path, 'A')
            self.assertEqual(str(root/'final-setup.json'), saved.data['setup_record'])
            self.assertEqual('Installations completed; setup checks recorded', saved.data['status'])
            self.assertEqual('installer.json', saved.data['items'][0]['record'])

    def test_setup_result_cannot_complete_uninstalled_items_or_another_profile(self):
        from modlab.resources.mo2_hub.install_queue import create_queue, record_setup
        with TemporaryDirectory() as tmp:
            root = Path(tmp); archive=root/'a.zip'; archive.write_bytes(b'archive')
            queue=create_queue(root, 'A', [archive]); before=queue.path.read_bytes()
            record_setup(queue, 'B', root/'setup.json', needs_attention=False)
            self.assertEqual(before,queue.path.read_bytes())
            record_setup(queue, 'A', root/'setup.json', needs_attention=False)
            self.assertEqual('Queued',queue.data['items'][0]['state'])
            self.assertEqual('Queued',queue.data['status'])

    def test_failed_install_retains_remaining_archives_and_successful_records(self):
        from modlab.resources.mo2_hub.install_queue import create_queue, load_queue, begin_item, finish_item
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = [root / (name + '.zip') for name in ('a', 'b', 'c')]
            for path in files: path.write_bytes(b'archive')
            queue = create_queue(root, 'Profile A', files + [files[0]])
            self.assertEqual(len(queue.data['items']), 3)
            begin_item(queue, str(files[0]), 'Profile A')
            finish_item(queue, 'Installed, enabled', 'a-operation.json')
            begin_item(queue, str(files[1]), 'Profile A')
            finish_item(queue, 'Not installed', None, 'Installer cancelled')
            saved = load_queue(queue.path, 'Profile A')
            self.assertEqual([i['state'] for i in saved.data['items']], ['Completed', 'Needs attention', 'Queued'])
            self.assertEqual(saved.data['items'][0]['record'], 'a-operation.json')
            self.assertEqual(saved.data['items'][1]['detail'], 'Installer cancelled')

    def test_resume_does_not_reinstall_completed_or_interrupted_items_by_default(self):
        from modlab.resources.mo2_hub.install_queue import create_queue, load_queue, begin_item, finish_item, resumable_items
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = [root / (name + '.zip') for name in ('a', 'b', 'c')]
            for path in files: path.write_bytes(b'archive')
            queue = create_queue(root, 'Profile A', files)
            begin_item(queue, str(files[0]), 'Profile A'); finish_item(queue, 'Installed, enabled', 'record')
            begin_item(queue, str(files[1]), 'Profile A')
            saved = load_queue(queue.path, 'Profile A')
            self.assertEqual(resumable_items(saved), [str(files[2])])
            self.assertEqual(saved.data['items'][1]['state'], 'Installing')

    def test_changed_archive_or_profile_refuses_start_without_mutating_record(self):
        from modlab.resources.mo2_hub.install_queue import create_queue, begin_item, load_queue
        with TemporaryDirectory() as tmp:
            root = Path(tmp); archive = root / 'a.zip'; archive.write_bytes(b'archive')
            queue = create_queue(root, 'Profile A', [archive])
            before = queue.path.read_bytes()
            with self.assertRaisesRegex(ValueError, 'profile'):
                begin_item(queue, str(archive), 'Other')
            with self.assertRaisesRegex(ValueError, 'profile'):
                load_queue(queue.path, 'Other')
            archive.write_bytes(b'new download')
            with self.assertRaisesRegex(ValueError, 'changed'):
                begin_item(queue, str(archive), 'Profile A')
            self.assertEqual(before, queue.path.read_bytes())

    def test_history_and_latest_queue_keep_other_profiles_separate(self):
        from modlab.resources.mo2_hub.install_queue import create_queue, latest_unfinished, begin_item, finish_item
        with TemporaryDirectory() as tmp:
            root = Path(tmp); archive = root / 'a.zip'; archive.write_bytes(b'archive')
            first = create_queue(root, 'A', [archive])
            create_queue(root, 'B', [archive])
            self.assertEqual(latest_unfinished(root, 'A').path, first.path)
            begin_item(first, str(archive), 'A'); finish_item(first, 'Installed, enabled', 'record')
            self.assertIsNone(latest_unfinished(root, 'A'))
