"""Native evidence-vault boundary tests; no game or MO2 processes."""
import ctypes
from ctypes import wintypes
from dataclasses import replace
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from modlab.platform import windows_exact_fs as exact
from modlab.validation import windows_integrity as integrity
from modlab.validation import windows_vault_security as security
from modlab.validation.windows_vault_security import (
    create_vault, open_vault, current_vault_principal,
)


@contextmanager
def impersonating(*, rid=None, restricted=False):
    """Real disposable thread token; always revert before filesystem cleanup."""
    api = ctypes.WinDLL('advapi32', use_last_error=True)
    pointer = ctypes.c_void_p
    api.DuplicateTokenEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, pointer, ctypes.c_int, ctypes.c_int, ctypes.POINTER(wintypes.HANDLE)]
    api.DuplicateTokenEx.restype = wintypes.BOOL
    api.SetThreadToken.argtypes = [pointer, wintypes.HANDLE]
    api.SetThreadToken.restype = wintypes.BOOL
    api.RevertToSelf.argtypes = []
    api.RevertToSelf.restype = wintypes.BOOL
    api.CreateRestrictedToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, pointer, wintypes.DWORD, pointer, wintypes.DWORD, pointer, ctypes.POINTER(wintypes.HANDLE)]
    api.CreateRestrictedToken.restype = wintypes.BOOL
    api.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(pointer)]
    api.ConvertStringSidToSidW.restype = wintypes.BOOL
    handles = []
    try:
        with security._effective_token() as token:
            duplicate = wintypes.HANDLE()
            if not api.DuplicateTokenEx(token.handle, 0x02000000, None, 2, 2, ctypes.byref(duplicate)):
                raise ctypes.WinError(ctypes.get_last_error())
            handles.append(duplicate.value)
        if rid is not None:
            integrity._set_token_integrity(duplicate, rid)
        if restricted:
            world = pointer()
            if not api.ConvertStringSidToSidW('S-1-1-0', ctypes.byref(world)):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                sid = security._SID_ATTRIBUTES(world.value, 0)
                restricted_token = wintypes.HANDLE()
                if not api.CreateRestrictedToken(duplicate, 0, 0, None, 0, None, 1, ctypes.byref(sid), ctypes.byref(restricted_token)):
                    raise ctypes.WinError(ctypes.get_last_error())
                duplicate = restricted_token
                handles.append(duplicate.value)
            finally:
                security._kernel.LocalFree(world)
        if not api.SetThreadToken(None, duplicate):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            if not api.RevertToSelf():
                raise ctypes.WinError(ctypes.get_last_error())
    finally:
        for handle in reversed(handles):
            exact._close_handle(handle)


@unittest.skipUnless(os.name == 'nt', 'native Windows security required')
class WindowsVaultSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='v-')
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_create_publish_reopen_and_inherited_descendants(self):
        with create_vault(self.root / 'authority') as vault:
            target = vault.path / 'evidence.json'
            exact.publish_new_pinned(target, b'protected\n', lambda data: data)
            vault.verify_descendant(target)
            sub = vault.path / 'nested'
            sub.mkdir()
            exact.publish_new_pinned(sub / 'child', b'inherited', lambda data: data)
            vault.verify_descendant(sub / 'child')
            with self.assertRaises(OSError):
                vault.path.rename(self.root / 'renamed')
        with open_vault(self.root / 'authority') as reopened:
            self.assertEqual(reopened.creator_sid, current_vault_principal().sid)

    def test_reopened_vault_continuously_denies_root_rename_and_delete(self):
        for operation in ('rename', 'delete'):
            with self.subTest(operation=operation):
                path = self.root / operation
                with create_vault(path):
                    pass
                with open_vault(path) as vault:
                    with self.assertRaises(OSError):
                        if operation == 'rename':
                            path.rename(self.root / 'moved')
                        else:
                            path.rmdir()
                    vault.verify()

    def test_watcher_and_creator_guards_coexist_and_survive_independent_close(self):
        path = self.root / 'authority'
        ready = self.root / 'watcher-ready'
        controller = create_vault(path)
        code = '''
from pathlib import Path
import sys
from modlab.validation.windows_vault_security import open_vault
with open_vault(Path(sys.argv[1])) as watcher:
    Path(sys.argv[2]).write_text('ready')
    if sys.stdin.readline().strip() != 'stop':
        raise RuntimeError('missing stop')
    watcher.verify()
'''
        worker = subprocess.Popen([sys.executable, '-B', '-c', code, str(path), str(ready)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 15
            while not ready.exists() and worker.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.exists(), 'watcher did not acquire its independent guard')
            controller.verify()
            # Each remaining owner must independently keep the root guarded.
            with self.assertRaises(OSError):
                path.rename(self.root / 'moved')
            controller.close()
            with self.assertRaises(OSError):
                path.rename(self.root / 'moved')
            with self.assertRaises(OSError):
                path.rmdir()
            with open_vault(path) as another_controller:
                another_controller.verify()
                stdout, stderr = worker.communicate('stop\n', timeout=15)
                self.assertEqual(0, worker.returncode, stderr)
                with self.assertRaises(OSError):
                    path.rename(self.root / 'moved')
            # The final close releases the guard, rather than permanently locking it.
            path.rename(self.root / 'moved')
        finally:
            if worker.poll() is None:
                try:
                    worker.communicate('stop\n', timeout=15)
                except subprocess.TimeoutExpired:
                    worker.kill()
                    worker.communicate(timeout=15)
            controller.close()

    def test_exact_medium_and_mandatory_policy_are_required(self):
        principal = current_vault_principal()
        for rid, policy in ((0x1000, 1), (0x2010, 1), (0x3000, 1), (0x2000, 0)):
            with self.subTest(rid=rid, policy=policy):
                with mock.patch.object(security, 'current_vault_principal', return_value=replace(principal, integrity_rid=rid, mandatory_policy=policy)):
                    with self.assertRaises(exact.ExactObjectError):
                        create_vault(self.root / 'bad')
                self.assertFalse((self.root / 'bad').exists())

    def test_actual_impersonated_low_token_is_not_ignored(self):
        creator = current_vault_principal().sid
        with impersonating(rid=0x1000):
            principal = current_vault_principal()
            self.assertEqual(0x1000, principal.integrity_rid)
            self.assertEqual(creator, principal.sid)
            with self.assertRaises(exact.ExactObjectError):
                create_vault(self.root / 'bad')
        self.assertFalse((self.root / 'bad').exists())

    def test_same_sid_medium_restricted_token_lacks_actual_vault_access(self):
        with create_vault(self.root / 'authority') as vault:
            with impersonating(restricted=True):
                principal = current_vault_principal()
                self.assertEqual(vault.creator_sid, principal.sid)
                self.assertEqual(0x2000, principal.integrity_rid)
                with self.assertRaises(exact.ExactObjectError):
                    security._check_access(vault._root, vault.creator_sid)
                with self.assertRaises((OSError, exact.ExactObjectError)):
                    open_vault(vault.path)

    def test_inaccessible_impersonation_token_does_not_fall_back(self):
        def denied(*args):
            ctypes.set_last_error(5)
            return False
        with mock.patch.object(security._advapi, 'OpenThreadToken', side_effect=denied):
            with self.assertRaises(OSError):
                current_vault_principal()

    def test_principal_mismatch_and_collision_leave_native_policy_unchanged(self):
        path = self.root / 'authority'
        with create_vault(path) as vault:
            before = security._security_policy(vault._root)
            identity = vault.identity
            with self.assertRaises(exact.ExactObjectError):
                open_vault(path, expected_creator_sid='S-1-5-18')
            with self.assertRaises(FileExistsError):
                create_vault(path)
            self.assertEqual(before, security._security_policy(vault._root))
            self.assertEqual(identity, exact.identity_at_path(path))

    def test_low_ancestor_and_unprotected_root_are_rejected(self):
        low = self.root / 'low'
        low.mkdir()
        integrity.set_low_integrity_tree(low)
        with self.assertRaises(exact.ExactObjectError):
            create_vault(low / 'authority')
        self.assertFalse((low / 'authority').exists())
        ordinary = self.root / 'ordinary'
        ordinary.mkdir()
        with self.assertRaises(exact.ExactObjectError):
            open_vault(ordinary)

    def test_foreign_writer_and_removed_label_are_rejected(self):
        path = self.root / 'authority'
        with create_vault(path) as vault:
            target = path / 'child'
            target.write_bytes(b'original')
            subprocess.run(['icacls', str(target), '/grant', '*S-1-1-0:(W)', '/Q'], check=True, capture_output=True)
            with self.assertRaises(exact.ExactObjectError):
                vault.verify_descendant(target)
            subprocess.run(['icacls', str(path), '/setintegritylevel', '(OI)(CI)L', '/Q'], check=True, capture_output=True)
            with self.assertRaises(exact.ExactObjectError):
                vault.verify()

    def test_unknown_ace_and_noninheriting_label_policy_fail_closed(self):
        with create_vault(self.root / 'authority') as vault:
            policy = security._security_policy(vault._root)
            for changed in (replace(policy, dacl=((99, 3, 0x1f01ff, vault.creator_sid),)), replace(policy, labels=((17, 0, 1, 'S-1-16-8192'),))):
                with self.subTest(policy=changed):
                    with mock.patch.object(security, '_security_policy', return_value=changed):
                        with self.assertRaises(exact.ExactObjectError):
                            vault.verify()

    def test_failed_close_retains_all_handles_and_original_error(self):
        vault = create_vault(self.root / 'authority')
        handles = tuple(vault._pins)
        failure = OSError('injected close failure')
        with mock.patch.object(exact.PinnedObject, 'close', side_effect=failure):
            with self.assertRaises(exact.ExactObjectOwnershipError) as raised:
                vault.close()
        self.assertEqual({id(p) for p in handles}, {id(p) for p in raised.exception.retained_objects})
        self.assertIs(raised.exception.__cause__, failure)
        raised.exception.resolve()
        vault.close()

    def test_failed_verification_cleanup_retains_root_ancestors_and_original_error(self):
        with create_vault(self.root / 'authority'):
            pass
        real_close = exact.PinnedObject.close
        failure = RuntimeError('original policy failure')
        acquired = []
        real_pin = security._pin
        def capture_pin(path):
            pinned = real_pin(path)
            acquired.append(pinned)
            return pinned
        def fail_close(pinned):
            if not any(pinned is owner for owner in acquired):
                return real_close(pinned)
            raise OSError('close failed')
        real_policy = security._validate_policy
        def fail_policy(pinned, *args, **kwargs):
            if pinned.path.name == 'authority':
                raise failure
            return real_policy(pinned, *args, **kwargs)
        with mock.patch.object(security, '_pin', side_effect=capture_pin), mock.patch.object(security, '_validate_policy', side_effect=fail_policy), mock.patch.object(exact.PinnedObject, 'close', new=fail_close):
            with self.assertRaises(exact.ExactObjectOwnershipError) as raised:
                open_vault(self.root / 'authority')
        self.assertEqual({id(p) for p in acquired}, {id(p) for p in raised.exception.retained_objects})
        self.assertEqual(len((self.root / 'authority').parents) + 1, len(raised.exception.retained_objects))
        self.assertIs(failure, raised.exception.__cause__)
        for pinned in raised.exception.retained_objects:
            real_close(pinned)

    def test_native_acl_parser_rejects_unknown_and_truncated_aces(self):
        # Independent literal ACL / ACCESS_ALLOWED_ACE containing SYSTEM SID.
        import struct
        ace = struct.pack('<BBHI', 0, 3, 20, 0x1f01ff) + bytes.fromhex('010100000000000512000000')
        acl = struct.pack('<BBHHH', 2, 0, 28, 1, 0) + ace
        for changed in (acl[:8] + bytes([99]) + acl[9:], acl[:10] + b'\xff\xff' + acl[12:]):
            buffer = ctypes.create_string_buffer(changed)
            with self.assertRaises(exact.ExactObjectError):
                security._aces(ctypes.c_void_p(ctypes.addressof(buffer)))

    def test_unknown_effective_ancestor_integrity_fails_closed(self):
        with create_vault(self.root / 'authority') as vault:
            policy = security._security_policy(vault._root)
            unknown = replace(policy, labels=((17, 0, 1, 'S-1-16-4294967295'),))
            with mock.patch.object(security, '_security_policy', return_value=unknown):
                with self.assertRaises(exact.ExactObjectError):
                    security._validate_policy(vault._root)

    def test_foreign_volume_observation_is_rejected(self):
        with create_vault(self.root / 'authority') as vault:
            original = vault._root.identity
            vault._root.identity = replace(original, volume_serial=original.volume_serial + 1)
            try:
                with self.assertRaises(exact.ExactObjectError):
                    vault.verify()
            finally:
                vault._root.identity = original

    def test_access_failure_and_every_token_close_failure_preserve_ownership(self):
        with create_vault(self.root / 'authority') as vault:
            failure = RuntimeError('original AccessCheck failure')
            with mock.patch.object(security._advapi, 'AccessCheck', side_effect=failure), mock.patch.object(exact.PinnedObject, 'close', side_effect=OSError('close failed')):
                with self.assertRaises(exact.ExactObjectOwnershipError) as raised:
                    security._check_access(vault._root, vault.creator_sid)
            self.assertEqual(2, len(raised.exception.retained_objects))
            self.assertIs(failure, raised.exception.__cause__.__cause__)
            raised.exception.resolve()

    def test_reparse_ancestor_and_descendant_are_rejected(self):
        real = self.root / 'real'
        real.mkdir()
        link = self.root / 'link'
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(real)], check=True, capture_output=True)
        try:
            with self.assertRaises(exact.ExactObjectError):
                create_vault(link / 'authority')
        finally:
            link.rmdir()
        with create_vault(self.root / 'authority') as vault:
            link = vault.path / 'link'
            subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(real)], check=True, capture_output=True)
            try:
                with self.assertRaises(exact.ExactObjectError):
                    vault.verify_descendant(link)
            finally:
                link.rmdir()

    def test_low_child_cannot_mutate_root_or_file_with_writable_sibling_control(self):
        stage = self.root / 'low'
        stage.mkdir()
        integrity.set_low_integrity_tree(stage)
        with create_vault(self.root / 'authority') as vault:
            target = vault.path / 'protected'
            target.write_bytes(b'original')
            identity = exact.identity_at_path(target)
            code = '''
import ctypes, json, os, subprocess, sys
from pathlib import Path
stage, root, target = map(Path, sys.argv[1:])
(stage / 'control').write_bytes(b'low write succeeded')
results = {}
operations = {
 'create_file': lambda: (root / 'new').write_bytes(b'bad'),
 'create_directory': lambda: (root / 'newdir').mkdir(),
 'overwrite_file': lambda: target.write_bytes(b'bad'),
 'rename_file': lambda: target.rename(root / 'moved'),
 'delete_file': lambda: target.unlink(),
 'rename_root': lambda: root.rename(root.with_name('moved-root')),
 'delete_root': lambda: root.rmdir(),
}
for name, operation in operations.items():
 try: operation(); results[name] = 'ALLOWED'
 except OSError as error: results[name] = [error.winerror, error.errno]
for name, path in [('relabel_root', root), ('relabel_file', target)]:
 result = subprocess.run(['icacls', str(path), '/setintegritylevel', 'L', '/Q'], capture_output=True)
 results[name] = result.returncode
(stage / 'result.json').write_text(json.dumps(results))
'''
            process = []
            kernel = integrity._kernel32
            def retain(pid):
                handle = kernel.OpenProcess(0x100000, False, pid)
                if not handle:
                    raise ctypes.WinError(ctypes.get_last_error())
                process.append(handle)
            try:
                integrity.launch_low_integrity_process(Path(sys.executable), ('-B', '-c', code, str(stage), str(vault.path), str(target)), self.root, dict(os.environ), on_created=retain)
                self.assertEqual(0, kernel.WaitForSingleObject(process[0], 20000))
                results = json.loads((stage / 'result.json').read_text())
                self.assertEqual(b'low write succeeded', (stage / 'control').read_bytes())
                for name, outcome in results.items():
                    with self.subTest(operation=name):
                        if name.startswith('relabel'):
                            self.assertEqual(5, outcome)
                        else:
                            self.assertTrue(outcome[0] in (5, 32) or outcome == [None, 13], outcome)
                self.assertEqual(9, len(results))
                self.assertEqual(identity, exact.identity_at_path(target))
                self.assertEqual(b'original', target.read_bytes())
                vault.verify_descendant(target)
            finally:
                for handle in process:
                    exact._close_handle(handle)


if __name__ == '__main__':
    unittest.main()
