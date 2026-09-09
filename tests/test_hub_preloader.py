import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as Obj
from unittest.mock import patch
import unittest
from modlab.resources.mo2_hub import preloader


PAYLOAD=b'checked test preloader'


class PreloaderTests(unittest.TestCase):
    def test_recovery_reconciles_publication_missing_from_durable_applied_list(self):
        with TemporaryDirectory() as folder:
            root = Path(folder); host = self.host(root); _, path = self.install(host)
            journal = path.parent/'root-deployment.json'
            record = json.loads(journal.read_text()); record['applied'] = []
            journal.write_text(json.dumps(record))
            with patch.object(preloader, 'DLL_SHA', hashlib.sha256(PAYLOAD).hexdigest()), patch.object(preloader.skse, 'require_game_closed'):
                preloader.restore(host, path)
            self.assertFalse((root/'game'/preloader.NAME).exists())
            self.assertEqual(PAYLOAD, (path.parent/'withdrawn-root'/preloader.NAME).read_bytes())
            self.assertEqual('Root preloader installation restored', json.loads(path.read_text())['status'])

    def host(self, root):
        game=root/'game';game.mkdir();(game/'SkyrimSE.exe').write_bytes(b'game1170')
        instance=root/'instance';(instance/'mods').mkdir(parents=True)
        profile=str(instance/'profiles/Test')
        return Obj(profilePath=lambda:profile,modsPath=lambda:str(instance/'mods'),
            managedGame=lambda:Obj(gameDirectory=lambda:Obj(absolutePath=lambda:str(game))))

    def install(self, host, *, extract_hook=None):
        def extracted(archive,job):
            source=job/'package';source.mkdir();(source/preloader.NAME).write_bytes(PAYLOAD)
            if extract_hook:extract_hook()
            return source
        with patch.object(preloader,'DLL_SHA',hashlib.sha256(PAYLOAD).hexdigest()), \
                patch.object(preloader,'extract',side_effect=extracted),patch.object(preloader.skse,'require_game_closed'):
            return preloader.install_package(host,Path('retained-author-archive.7z'),lambda _: '1.6.1170.0')

    def test_install_is_in_game_root_and_recovery_retains_removed_file(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);host=self.host(root);result,path=self.install(host)
            self.assertEqual('Installed, enabled',result.status)
            self.assertEqual(PAYLOAD,(root/'game'/preloader.NAME).read_bytes())
            self.assertFalse((root/'game/Data'/preloader.NAME).exists())
            self.assertTrue((path.parent/'root-deployment.json').is_file())
            with patch.object(preloader,'DLL_SHA',hashlib.sha256(PAYLOAD).hexdigest()),patch.object(preloader.skse,'require_game_closed'):
                preloader.restore(host,path)
            self.assertFalse((root/'game'/preloader.NAME).exists())
            self.assertEqual(PAYLOAD,(path.parent/'withdrawn-root'/preloader.NAME).read_bytes())

    def test_repeat_install_does_not_replace_existing_matching_preloader(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);host=self.host(root);self.install(host)
            target=root/'game'/preloader.NAME;before=target.stat().st_mtime_ns
            result,path=self.install(host)
            self.assertEqual('Installed, enabled',result.status)
            self.assertEqual(before,target.stat().st_mtime_ns)
            self.assertTrue(json.loads(path.read_text())['reused_existing'])

    def test_unknown_existing_preloader_and_later_manual_edit_are_preserved(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);host=self.host(root);target=root/'game'/preloader.NAME
            target.write_bytes(b'other loader')
            with self.assertRaisesRegex(ValueError,'different'):
                self.install(host)
            self.assertEqual(b'other loader',target.read_bytes())
            target.unlink();_,path=self.install(host);target.write_bytes(b'manual update')
            with patch.object(preloader,'DLL_SHA',hashlib.sha256(PAYLOAD).hexdigest()),patch.object(preloader.skse,'require_game_closed'):
                with self.assertRaisesRegex(ValueError,'changed'):
                    preloader.restore(host,path)
            self.assertEqual(b'manual update',target.read_bytes())

    def test_runtime_or_context_change_stops_before_root_write(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);host=self.host(root)
            with patch.object(preloader.skse,'require_game_closed'),self.assertRaisesRegex(ValueError,'1.6.1170'):
                preloader.install_package(host,Path('unused'),lambda _: '1.7.104.0')
            with self.assertRaisesRegex(ValueError,'changed'):
                self.install(host,extract_hook=lambda:(root/'game/SkyrimSE.exe').write_bytes(b'updated'))
            self.assertFalse((root/'game'/preloader.NAME).exists())

    def test_different_archive_never_reaches_extraction(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);archive=root/'bad.7z';archive.write_bytes(b'wrong archive')
            with patch.object(preloader.subprocess,'run') as run,self.assertRaisesRegex(ValueError,'not the checked'):
                preloader.extract(archive,root)
            run.assert_not_called()

    def test_failed_success_receipt_rolls_back_root_change(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);host=self.host(root);write=preloader.write_record
            def fail_success(path,record):
                if record.get('status')=='Installed, enabled':raise OSError('receipt storage failed')
                write(path,record)
            with patch.object(preloader,'write_record',side_effect=fail_success),self.assertRaisesRegex(OSError,'receipt storage'):
                self.install(host)
            self.assertFalse((root/'game'/preloader.NAME).exists())

    def test_changed_source_and_copy_failure_leave_no_active_or_pending_preloader(self):
        for mode in ('changed source','partial copy'):
            with self.subTest(mode=mode),TemporaryDirectory() as folder:
                root=Path(folder);host=self.host(root);original=preloader.skse.shutil.copy2
                def broken(src,dst,*args,**kwargs):
                    if str(dst).endswith('.modlab-pending'):
                        if mode=='changed source':Path(src).write_bytes(b'changed binary')
                        else:
                            Path(dst).write_bytes(b'partial');raise OSError('copy interrupted')
                    return original(src,dst,*args,**kwargs)
                with patch.object(preloader.skse.shutil,'copy2',side_effect=broken),self.assertRaises((OSError,ValueError)):
                    self.install(host)
                self.assertFalse((root/'game'/preloader.NAME).exists())
                self.assertFalse((root/'game'/(preloader.NAME+'.modlab-pending')).exists())

    def test_loader_appearing_before_publication_is_preserved(self):
        for phase in ('before helper','during copy'):
            with self.subTest(phase=phase),TemporaryDirectory() as folder:
                root=Path(folder);host=self.host(root);target=root/'game'/preloader.NAME
                deploy=preloader.skse.deploy_root_files;copy=preloader.skse.shutil.copy2
                def changed_deploy(*args,**kwargs):
                    if phase=='before helper':target.write_bytes(b'other loader')
                    return deploy(*args,**kwargs)
                def changed_copy(src,dst,*args,**kwargs):
                    result=copy(src,dst,*args,**kwargs)
                    if phase=='during copy' and str(dst).endswith('.modlab-pending'):target.write_bytes(b'other loader')
                    return result
                with patch.object(preloader.skse,'deploy_root_files',side_effect=changed_deploy), \
                        patch.object(preloader.skse.shutil,'copy2',side_effect=changed_copy),self.assertRaises(ValueError):
                    self.install(host)
                self.assertEqual(b'other loader',target.read_bytes())
