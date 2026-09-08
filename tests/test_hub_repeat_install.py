import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from modlab.resources.mo2_hub import installation


def finish(steps):
    while True:
        try:next(steps)
        except StopIteration as done:return done.value


class RepeatInstallTests(unittest.TestCase):
    def fixture(self, root):
        mods=root/'mods'; mods.mkdir(); target=mods/'Saved choices'; target.mkdir()
        archive=root/'mod.zip'; archive.write_bytes(b'same archive')
        (target/'selected.esp').write_bytes(b'chosen option')
        history=root/'history'; history.mkdir()
        receipt=finish(installation.capture_files(archive,target,mods))
        record=dict(archive=str(archive),profile_path='A',mods_root=str(mods),
                    status='Installed, enabled',result=dict(name=target.name,path=str(target)),file_verification=receipt)
        path=history/'installed.json'; path.write_text(json.dumps(record))
        return archive,mods,target,history,path,record

    def find(self, history,archive,mods,profile='A'):
        self.assertTrue(hasattr(installation,'find_existing'),'Duplicate choices require current byte evidence')
        return finish(installation.find_existing(history,archive,mods,profile))

    def test_same_archive_and_all_selected_files_prove_existing_installation(self):
        with TemporaryDirectory() as tmp:
            archive,mods,target,history,path,record=self.fixture(Path(tmp))
            found=self.find(history,archive,mods)
            self.assertEqual(found['record'],str(path))
            self.assertEqual(found['target'],str(target))
            self.assertEqual(found['verification'],record['file_verification'])

    def test_changed_missing_extra_or_different_archive_is_not_reused(self):
        for change in ('changed','missing','extra','archive'):
            with self.subTest(change=change),TemporaryDirectory() as tmp:
                archive,mods,target,history,path,record=self.fixture(Path(tmp))
                if change=='changed':(target/'selected.esp').write_bytes(b'edited choice')
                if change=='missing':(target/'selected.esp').unlink()
                if change=='extra':(target/'extra.dll').write_bytes(b'added')
                if change=='archive':archive.write_bytes(b'different download')
                self.assertIsNone(self.find(history,archive,mods))

    def test_legacy_failed_other_profile_and_outside_records_do_not_prove_reuse(self):
        for change in ('legacy','failed','profile','outside','wrongname'):
            with self.subTest(change=change),TemporaryDirectory() as tmp:
                root=Path(tmp);archive,mods,target,history,path,record=self.fixture(root)
                if change=='legacy':record.pop('file_verification')
                if change=='failed':record['status']='Not installed'
                if change=='profile':record['profile_path']='B'
                if change=='outside':record['result']['path']=str(root)
                if change=='wrongname':record['result']['name']='Someone else'
                path.write_text(json.dumps(record))
                self.assertIsNone(self.find(history,archive,mods))

    def test_mo2_metadata_updates_do_not_change_installer_choices(self):
        with TemporaryDirectory() as tmp:
            archive,mods,target,history,path,record=self.fixture(Path(tmp))
            (target/'meta.ini').write_text('changed MO2 metadata')
            self.assertIsNotNone(self.find(history,archive,mods))

