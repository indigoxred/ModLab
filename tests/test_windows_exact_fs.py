"""Native retained-handle exact-object rename tests."""

from __future__ import annotations

from dataclasses import replace
import ctypes
import inspect
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from modlab.platform import windows_exact_fs
from modlab.platform.windows_exact_fs import (
    ExactObjectError,
    ExactObjectOwnershipError,
    PinnedIdentity,
    PinnedObject,
    create_pinned_new,
    identity_at_path,
    pin_direct_object,
    pin_stable_direct_object,
    publish_new_pinned,
    rename_pinned_no_replace,
    resolve_retained_ownership,
)


@unittest.skipUnless(os.name == "nt", "Windows retained-handle APIs are required")
class WindowsExactFsTests(unittest.TestCase):
    def test_rooted_directory_initial_descriptor_and_read_control(self):
        from modlab.validation import windows_vault_security as security

        principal = security.current_vault_principal()
        descriptor = ctypes.c_void_p()
        sddl = f'O:{principal.sid}G:{principal.sid}D:P(A;OICI;FA;;;{principal.sid})(A;OICI;FA;;;SY)S:(ML;OICI;NW;;;ME)'
        self.assertTrue(security._advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None))
        parent = pin_stable_direct_object(self.root, 'directory', allow_writes=True)
        child = None
        try:
            child = windows_exact_fs.create_pinned_directory_child(self.root / 'secure', parent, initial_security_descriptor=descriptor.value, read_control=True)
            policy = security._security_policy(child)
            self.assertEqual(principal.sid, policy.owner)
            self.assertTrue(policy.protected)
            self.assertEqual(((17, 3, 1, 'S-1-16-8192'),), policy.labels)
        finally:
            if child is not None:
                child.close()
            parent.close()
            security._kernel.LocalFree(descriptor)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="modlab-windows-exact-fs-")
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_directory_identity_attributes_ignore_transient_bits_while_files_remain_raw(
        self,
    ) -> None:
        directory_attributes = windows_exact_fs._FILE_ATTRIBUTE_DIRECTORY
        transient_attributes = directory_attributes | 0x10000000

        self.assertEqual(
            windows_exact_fs.normalize_identity_attributes(
                directory_attributes,
                is_directory=True,
            ),
            windows_exact_fs.normalize_identity_attributes(
                transient_attributes,
                is_directory=True,
            ),
        )
        self.assertEqual(
            transient_attributes,
            windows_exact_fs.normalize_identity_attributes(
                transient_attributes,
                is_directory=False,
            ),
        )

    def test_stable_snapshot_pin_denies_write_and_rename_until_close(self) -> None:
        path = self.root / "snapshot.bin"
        renamed = self.root / "renamed.bin"
        path.write_bytes(b"before")
        pinned = pin_stable_direct_object(path, "file")
        try:
            with self.assertRaises(PermissionError):
                path.write_bytes(b"changed")
            with self.assertRaises(PermissionError):
                path.rename(renamed)
        finally:
            pinned.close()

        path.write_bytes(b"after")
        self.assertEqual(b"after", path.read_bytes())

    def test_validated_candidate_handle_publishes_original_after_old_path_is_replaced(self) -> None:
        candidate = self.root / "candidate.json"
        old_object_path = self.root / "old-object.json"
        destination = self.root / "outcome.json"
        candidate.write_bytes(b'{"ok":true}\n')
        source = pin_direct_object(candidate, kind="file")
        parent = pin_direct_object(self.root, kind="directory")
        try:
            candidate.rename(old_object_path)
            candidate.write_bytes(b'{"replacement":true}\n')

            rename_pinned_no_replace(source, destination, parent)
            self.assertEqual(source.identity, identity_at_path(destination))
        finally:
            source.close()
            parent.close()
        self.assertEqual(b'{"ok":true}\n', destination.read_bytes())
        self.assertEqual(b'{"replacement":true}\n', candidate.read_bytes())

    def test_case_insensitive_destination_collision_is_never_replaced(self) -> None:
        candidate = self.root / "candidate.json"
        candidate.write_bytes(b"candidate")
        existing = self.root / "OUTCOME.JSON"
        existing.write_bytes(b"existing")
        source = pin_direct_object(candidate, kind="file")
        parent = pin_direct_object(self.root, kind="directory")
        try:
            with self.assertRaises(FileExistsError):
                rename_pinned_no_replace(source, self.root / "outcome.json", parent)
        finally:
            source.close()
            parent.close()
        self.assertEqual(b"candidate", candidate.read_bytes())
        self.assertEqual(b"existing", existing.read_bytes())

    def test_file_and_directory_moves_preserve_the_pinned_identity(self) -> None:
        file_source = self.root / "source.json"
        directory_source = self.root / "source-directory"
        file_source.write_bytes(b"file")
        directory_source.mkdir()
        (directory_source / "child.txt").write_bytes(b"directory")
        parent = pin_direct_object(self.root, kind="directory")
        file_pin = pin_direct_object(file_source, kind="file")
        directory_pin = pin_direct_object(directory_source, kind="directory")
        try:
            rename_pinned_no_replace(file_pin, self.root / "file.json", parent)
            rename_pinned_no_replace(directory_pin, self.root / "directory", parent)
            self.assertEqual(file_pin.identity, identity_at_path(self.root / "file.json"))
            self.assertEqual(directory_pin.identity, identity_at_path(self.root / "directory"))
            self.assertEqual(b"directory", (self.root / "directory" / "child.txt").read_bytes())
        finally:
            file_pin.close()
            directory_pin.close()
            parent.close()

    def test_wrong_source_kind_is_rejected(self) -> None:
        file_path = self.root / "file.json"
        file_path.write_bytes(b"file")
        with self.assertRaises(ExactObjectError):
            pin_direct_object(file_path, kind="directory")

    def test_cross_volume_and_destination_parent_identity_mismatch_refuse_before_rename(self) -> None:
        candidate = self.root / "candidate.json"
        candidate.write_bytes(b"candidate")
        source = pin_direct_object(candidate, kind="file")
        parent = pin_direct_object(self.root, kind="directory")
        try:
            other_volume_parent = replace(
                parent,
                identity=replace(parent.identity, volume_serial=parent.identity.volume_serial + 1),
            )
            with mock.patch.object(
                windows_exact_fs,
                "_handle_identity",
                side_effect=(source.identity, other_volume_parent.identity),
            ):
                with self.assertRaisesRegex(ExactObjectError, "same volume"):
                    rename_pinned_no_replace(
                        source,
                        self.root / "outcome.json",
                        other_volume_parent,
                        identity_at_path_fn=lambda _path: other_volume_parent.identity,
                    )
            with self.assertRaisesRegex(ExactObjectError, "destination parent changed"):
                rename_pinned_no_replace(
                    source,
                    self.root / "outcome.json",
                    parent,
                    identity_at_path_fn=lambda _path: source.identity,
                )
            self.assertTrue(candidate.exists())
        finally:
            source.close()
            parent.close()

    def test_post_move_identity_mismatch_is_explicit(self) -> None:
        candidate = self.root / "candidate.json"
        destination = self.root / "outcome.json"
        candidate.write_bytes(b"candidate")
        source = pin_direct_object(candidate, kind="file")
        parent = pin_direct_object(self.root, kind="directory")
        observations = iter((parent.identity, parent.identity, parent.identity))
        try:
            with self.assertRaisesRegex(ExactObjectError, "not the pinned source"):
                rename_pinned_no_replace(
                    source,
                    destination,
                    parent,
                    identity_at_path_fn=lambda _path: next(observations),
                )
            self.assertTrue(destination.exists())
        finally:
            source.close()
            parent.close()

    def test_close_failure_keeps_explicit_handle_ownership(self) -> None:
        pinned = PinnedObject(self.root / "unused", 123, identity_at_path(self.root))
        with mock.patch.object(windows_exact_fs, "_close_handle", side_effect=OSError("close failed")):
            with self.assertRaisesRegex(OSError, "close failed"):
                pinned.close()
        self.assertEqual(123, pinned.handle)

    def test_identity_lookup_close_failure_retains_verification_handle_across_retries(self) -> None:
        original_path = self.root / "identity.json"
        held_path = self.root / "held-identity.json"
        original_path.write_bytes(b"original")
        real_close = windows_exact_fs._close_handle
        close_failures = 0

        def fail_first_two_closes(handle: int) -> None:
            nonlocal close_failures
            if close_failures < 2:
                close_failures += 1
                raise OSError("injected identity close failure")
            real_close(handle)

        with (
            mock.patch.object(
                windows_exact_fs,
                "_handle_identity",
                side_effect=ExactObjectError("injected identity validation failure"),
            ),
            mock.patch.object(
                windows_exact_fs,
                "_close_handle",
                side_effect=fail_first_two_closes,
            ),
        ):
            observed_error = None
            try:
                identity_at_path(original_path)
            except BaseException as error:
                observed_error = error

            self.assertIsInstance(observed_error, ExactObjectOwnershipError)
            ownership = observed_error
            self.assertIsInstance(ownership.__cause__, ExactObjectError)
            self.assertEqual("injected identity validation failure", str(ownership.__cause__))
            self.assertIsNone(ownership.candidate)
            self.assertIsNone(ownership.destination_parent)
            self.assertEqual(1, len(ownership.verification))
            retained = ownership.verification[0]
            self.assertNotEqual(0, retained.handle)

            original_path.rename(held_path)
            original_path.write_bytes(b"pathname substitute")
            with self.assertRaises(ExactObjectOwnershipError) as retry:
                ownership.resolve()
            self.assertIs(retained, retry.exception.verification[0])
            retry.exception.resolve()

        self.assertEqual(2, close_failures)
        self.assertEqual(0, retained.handle)
        self.assertEqual(b"original", held_path.read_bytes())
        self.assertEqual(b"pathname substitute", original_path.read_bytes())

    def test_pin_validation_close_failure_retains_close_only_acquisition_across_retries(self) -> None:
        original_path = self.root / "pin.json"
        held_path = self.root / "held-pin.json"
        original_path.write_bytes(b"original")
        real_close = windows_exact_fs._close_handle
        close_failures = 0

        def fail_first_two_closes(handle: int) -> None:
            nonlocal close_failures
            if close_failures < 2:
                close_failures += 1
                raise OSError("injected pin close failure")
            real_close(handle)

        with (
            mock.patch.object(windows_exact_fs._kernel32, "GetFileType", return_value=0),
            mock.patch.object(
                windows_exact_fs,
                "_close_handle",
                side_effect=fail_first_two_closes,
            ),
        ):
            observed_error = None
            try:
                pin_direct_object(original_path, kind="file")
            except BaseException as error:
                observed_error = error

            self.assertIsInstance(observed_error, ExactObjectOwnershipError)
            ownership = observed_error
            self.assertIsInstance(ownership.__cause__, ExactObjectError)
            self.assertIn("direct regular file required", str(ownership.__cause__))
            self.assertIsNone(ownership.candidate)
            self.assertIsNone(ownership.destination_parent)
            self.assertEqual(1, len(ownership.verification))
            retained = ownership.verification[0]

            original_path.rename(held_path)
            original_path.write_bytes(b"pathname substitute")
            with self.assertRaises(ExactObjectOwnershipError) as retry:
                ownership.resolve()
            retry.exception.resolve()

        self.assertEqual(2, close_failures)
        self.assertEqual(0, retained.handle)
        self.assertEqual(b"original", held_path.read_bytes())
        self.assertEqual(b"pathname substitute", original_path.read_bytes())

    def test_create_validation_close_failure_retains_candidate_across_retries(self) -> None:
        candidate_path = self.root / "candidate.json"
        parent = pin_direct_object(self.root, kind="directory")
        real_identity = windows_exact_fs._handle_identity
        real_close = windows_exact_fs._close_handle
        candidate_handle = 0
        close_failures = 0

        def reject_created_candidate(handle: int, path: Path) -> PinnedIdentity:
            nonlocal candidate_handle
            if path == candidate_path:
                candidate_handle = handle
                observed = real_identity(handle, path)
                return replace(
                    observed,
                    attributes=observed.attributes
                    | windows_exact_fs._FILE_ATTRIBUTE_DIRECTORY,
                )
            return real_identity(handle, path)

        def fail_first_two_candidate_closes(handle: int) -> None:
            nonlocal close_failures
            if handle == candidate_handle and close_failures < 2:
                close_failures += 1
                raise OSError("injected create close failure")
            real_close(handle)

        try:
            with (
                mock.patch.object(
                    windows_exact_fs,
                    "_handle_identity",
                    side_effect=reject_created_candidate,
                ),
                mock.patch.object(
                    windows_exact_fs,
                    "_close_handle",
                    side_effect=fail_first_two_candidate_closes,
                ),
            ):
                observed_error = None
                try:
                    create_pinned_new(candidate_path, parent)
                except BaseException as error:
                    observed_error = error

                self.assertIsInstance(observed_error, ExactObjectOwnershipError)
                ownership = observed_error
                self.assertIsInstance(ownership.__cause__, ExactObjectError)
                self.assertIn("new candidate is not a direct regular file", str(ownership.__cause__))
                self.assertIsNotNone(ownership.candidate)
                self.assertIsNone(ownership.destination_parent)
                self.assertEqual((), ownership.verification)
                retained = ownership.candidate
                self.assertNotEqual(0, retained.handle)

                with self.assertRaises(ExactObjectOwnershipError) as retry:
                    ownership.resolve()
                retry.exception.resolve()

            self.assertEqual(2, close_failures)
            self.assertEqual(0, retained.handle)
            self.assertFalse(candidate_path.exists())
            candidate_path.write_bytes(b"unrelated pathname occupant")
            self.assertEqual(b"unrelated pathname occupant", candidate_path.read_bytes())
        finally:
            parent.close()

    def test_publish_unions_candidate_parent_and_verification_ownership(self) -> None:
        destination = self.root / "nested-ownership.json"
        real_identity = windows_exact_fs._handle_identity
        real_close = windows_exact_fs._close_handle
        root_identity_calls = 0
        handles: dict[str, int] = {}
        failed_roles: set[str] = set()

        def fail_second_create_parent_verification(
            handle: int,
            path: Path,
        ) -> PinnedIdentity:
            nonlocal root_identity_calls
            if path == self.root:
                root_identity_calls += 1
                if root_identity_calls == 1:
                    handles["parent"] = handle
                if root_identity_calls == 3:
                    handles["verification"] = handle
                    raise ExactObjectError("injected create parent verification failure")
            elif path.name.startswith(".nested-ownership.json."):
                handles["candidate"] = handle
            return real_identity(handle, path)

        def fail_each_owned_close_once(handle: int) -> None:
            for role in ("verification", "candidate", "parent"):
                if handles.get(role) == handle and role not in failed_roles:
                    failed_roles.add(role)
                    raise OSError(f"injected {role} close failure")
            real_close(handle)

        try:
            with (
                mock.patch.object(
                    windows_exact_fs,
                    "_handle_identity",
                    side_effect=fail_second_create_parent_verification,
                ),
                mock.patch.object(
                    windows_exact_fs,
                    "_close_handle",
                    side_effect=fail_each_owned_close_once,
                ),
            ):
                observed_error = None
                try:
                    publish_new_pinned(destination, b"payload", lambda data: data)
                except BaseException as error:
                    observed_error = error

                self.assertIsInstance(observed_error, ExactObjectOwnershipError)
                ownership = observed_error
                self.assertIsNotNone(ownership.candidate)
                self.assertIsNotNone(ownership.destination_parent)
                self.assertEqual(handles["candidate"], ownership.candidate.handle)
                self.assertEqual(
                    handles["parent"],
                    ownership.destination_parent.handle,
                )
                self.assertEqual(
                    (handles["verification"],),
                    tuple(pinned.handle for pinned in ownership.verification),
                )
                self.assertEqual(
                    {
                        handles["candidate"],
                        handles["parent"],
                        handles["verification"],
                    },
                    {pinned.handle for pinned in ownership.retained_objects},
                )
                retained_objects = ownership.retained_objects

                ownership.resolve()

            self.assertEqual(
                {"verification", "candidate", "parent"},
                failed_roles,
            )
            self.assertTrue(
                all(pinned.handle == 0 for pinned in retained_objects)
            )
            self.assertFalse(destination.exists())
            self.assertEqual([], list(self.root.glob(".nested-ownership.json.*.tmp")))
        finally:
            for handle in set(handles.values()):
                try:
                    real_close(handle)
                except OSError:
                    pass

    def test_temporary_name_failure_does_not_leave_parent_ownership(self) -> None:
        destination = self.root / "name-generation.json"
        real_pin = windows_exact_fs.pin_direct_object
        acquired: list[PinnedObject] = []

        def record_pin(path: Path, kind: str) -> PinnedObject:
            pinned = real_pin(path, kind)
            acquired.append(pinned)
            return pinned

        try:
            with (
                mock.patch.object(
                    windows_exact_fs,
                    "pin_direct_object",
                    side_effect=record_pin,
                ),
                mock.patch.object(
                    windows_exact_fs.secrets,
                    "token_hex",
                    side_effect=RuntimeError("injected name-generation failure"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "name-generation"):
                    publish_new_pinned(destination, b"payload", lambda data: data)

            self.assertEqual([], acquired)
            self.assertFalse(destination.exists())
        finally:
            for pinned in acquired:
                if pinned.handle:
                    pinned.close()

    def test_ownership_union_preserves_same_role_owners_through_partial_retries(self) -> None:
        candidate_paths = (
            self.root / "candidate-one.json",
            self.root / "candidate-two.json",
        )
        held_paths = (
            self.root / "held-candidate-one.json",
            self.root / "held-candidate-two.json",
        )
        verification_paths = (
            self.root / "verification-one.json",
            self.root / "verification-two.json",
        )
        for index, path in enumerate(candidate_paths, start=1):
            path.write_bytes(f"candidate-{index}".encode("ascii"))
        for index, path in enumerate(verification_paths, start=1):
            path.write_bytes(f"verification-{index}".encode("ascii"))

        candidates = tuple(
            pin_direct_object(path, kind="file") for path in candidate_paths
        )
        parents = (
            pin_direct_object(self.root, kind="directory"),
            pin_direct_object(self.root, kind="directory"),
        )
        verifications = tuple(
            pin_direct_object(path, kind="file") for path in verification_paths
        )
        for path, held in zip(candidate_paths, held_paths, strict=True):
            path.rename(held)
            path.write_bytes(b"pathname substitute")

        owner_type = windows_exact_fs.RetainedObjectOwner
        role = windows_exact_fs.RetainedObjectRole
        prior = ExactObjectOwnershipError(
            "prior ownership",
            owners=(
                owner_type(role.CANDIDATE, candidates[0]),
                owner_type(role.DESTINATION_PARENT, parents[0]),
                owner_type(role.VERIFICATION, verifications[0]),
            ),
        )
        ownership = windows_exact_fs._union_ownership(
            "combined ownership",
            prior=prior,
            owners=(
                owner_type(role.CANDIDATE, candidates[1]),
                owner_type(role.DESTINATION_PARENT, parents[1]),
                owner_type(role.VERIFICATION, verifications[1]),
                owner_type(role.CANDIDATE, candidates[0]),
                owner_type(role.DESTINATION_PARENT, parents[0]),
                owner_type(role.VERIFICATION, verifications[0]),
            ),
        )
        expected_owners = (
            owner_type(role.CANDIDATE, candidates[0]),
            owner_type(role.DESTINATION_PARENT, parents[0]),
            owner_type(role.VERIFICATION, verifications[0]),
            owner_type(role.CANDIDATE, candidates[1]),
            owner_type(role.DESTINATION_PARENT, parents[1]),
            owner_type(role.VERIFICATION, verifications[1]),
        )
        self.assertEqual(expected_owners, ownership.owners)
        self.assertEqual(candidates[0], ownership.candidate)
        self.assertEqual(parents[0], ownership.destination_parent)

        real_delete = windows_exact_fs.delete_pinned_object
        real_close = PinnedObject.close
        delete_attempts: list[PinnedObject] = []
        close_attempts: list[PinnedObject] = []
        retry_failures = {
            id(candidates[0]): 0,
            id(parents[0]): 0,
            id(verifications[0]): 0,
        }

        def delete_with_partial_failures(pinned: PinnedObject) -> None:
            delete_attempts.append(pinned)
            if pinned is candidates[0] and retry_failures[id(pinned)] < 2:
                retry_failures[id(pinned)] += 1
                raise OSError("injected candidate delete failure")
            real_delete(pinned)

        def close_with_partial_failures(pinned: PinnedObject) -> None:
            close_attempts.append(pinned)
            if id(pinned) in retry_failures and pinned is not candidates[0]:
                if retry_failures[id(pinned)] < 2:
                    retry_failures[id(pinned)] += 1
                    raise OSError("injected close-only owner failure")
            real_close(pinned)

        observed = ownership
        try:
            with (
                mock.patch.object(
                    windows_exact_fs,
                    "delete_pinned_object",
                    side_effect=delete_with_partial_failures,
                ),
                mock.patch.object(
                    PinnedObject,
                    "close",
                    new=close_with_partial_failures,
                ),
            ):
                for _attempt in range(2):
                    with self.assertRaises(ExactObjectOwnershipError) as raised:
                        observed.resolve()
                    observed = raised.exception
                    self.assertEqual(
                        (
                            owner_type(role.CANDIDATE, candidates[0]),
                            owner_type(role.DESTINATION_PARENT, parents[0]),
                            owner_type(role.VERIFICATION, verifications[0]),
                        ),
                        observed.owners,
                    )
                observed.resolve()

            self.assertEqual(3, delete_attempts.count(candidates[0]))
            self.assertEqual(1, delete_attempts.count(candidates[1]))
            self.assertFalse(any(owner in delete_attempts for owner in parents))
            self.assertFalse(
                any(owner in delete_attempts for owner in verifications)
            )
            self.assertTrue(
                all(pinned.handle == 0 for pinned in (*candidates, *parents, *verifications))
            )
            for path in candidate_paths:
                self.assertEqual(b"pathname substitute", path.read_bytes())
            for held in held_paths:
                self.assertFalse(held.exists())
            for index, path in enumerate(verification_paths, start=1):
                self.assertEqual(
                    f"verification-{index}".encode("ascii"),
                    path.read_bytes(),
                )
            self.assertGreaterEqual(close_attempts.count(parents[0]), 3)
            self.assertGreaterEqual(close_attempts.count(verifications[0]), 3)
        finally:
            for pinned in (*candidates, *parents, *verifications):
                if pinned.handle:
                    real_close(pinned)

    def test_shared_rename_implementation_has_no_pathname_fallback(self) -> None:
        source = inspect.getsource(windows_exact_fs.rename_pinned_no_replace)
        self.assertNotIn("MoveFileExW", source)
        self.assertNotIn("os.rename", source)
        self.assertNotIn("Path.rename", source)

    def test_rooted_rename_uses_retained_parent_handle_and_direct_child_name(self) -> None:
        source_path = self.root / "candidate.json"
        source_path.write_bytes(b"candidate")
        source = pin_direct_object(source_path, kind="file")
        parent = pin_direct_object(self.root, kind="directory")
        observed: dict[str, object] = {}
        real_set_information = windows_exact_fs._ntdll.NtSetInformationFile

        def inspect_rooted_rename(handle, io_status, buffer, size, information_class):
            if information_class == windows_exact_fs._FILE_RENAME_INFORMATION_CLASS:
                rename = windows_exact_fs._FILE_RENAME_INFO.from_buffer(buffer)
                offset = windows_exact_fs._FILE_RENAME_INFO.FileName.offset
                raw = ctypes.string_at(ctypes.addressof(buffer) + offset, rename.FileNameLength)
                observed["root"] = rename.RootDirectory
                observed["name"] = raw.decode("utf-16-le")
            return real_set_information(handle, io_status, buffer, size, information_class)

        try:
            with mock.patch.object(
                windows_exact_fs._ntdll,
                "NtSetInformationFile",
                side_effect=inspect_rooted_rename,
            ):
                rename_pinned_no_replace(source, self.root / "outcome.json", parent)
            self.assertEqual(parent.handle, observed["root"])
            self.assertEqual("outcome.json", observed["name"])
        finally:
            source.close()
            parent.close()

    def test_create_pinned_new_retains_the_candidate_without_reopening_its_path(self) -> None:
        parent = pin_direct_object(self.root, kind="directory")
        try:
            candidate = create_pinned_new(self.root / "candidate.json", parent)
            try:
                self.assertEqual(candidate.identity, identity_at_path(candidate.path))
            finally:
                candidate.close()
        finally:
            parent.close()

    def test_rooted_directory_creation_returns_the_exact_unreplaceable_child(self) -> None:
        creator = getattr(windows_exact_fs, "create_pinned_directory_child", None)
        self.assertIsNotNone(
            creator,
            "the retained-parent native directory creation primitive is missing",
        )
        child_path = self.root / "created-child"
        displaced = self.root / "displaced-child"
        parent = pin_stable_direct_object(
            self.root,
            kind="directory",
            allow_writes=True,
        )
        observed: dict[str, object] = {}
        real_create = windows_exact_fs._ntdll.NtCreateFile

        def inspect_create(
            output_handle,
            desired_access,
            object_attributes,
            io_status,
            allocation_size,
            file_attributes,
            share_access,
            create_disposition,
            create_options,
            ea_buffer,
            ea_length,
        ):
            attributes = ctypes.cast(
                object_attributes,
                ctypes.POINTER(windows_exact_fs._OBJECT_ATTRIBUTES),
            ).contents
            name = attributes.ObjectName.contents
            observed["root"] = attributes.RootDirectory
            observed["name"] = ctypes.wstring_at(
                name.Buffer,
                name.Length // ctypes.sizeof(ctypes.c_wchar),
            )
            return real_create(
                output_handle,
                desired_access,
                object_attributes,
                io_status,
                allocation_size,
                file_attributes,
                share_access,
                create_disposition,
                create_options,
                ea_buffer,
                ea_length,
            )

        child = None
        try:
            with mock.patch.object(
                windows_exact_fs._ntdll,
                "NtCreateFile",
                side_effect=inspect_create,
            ):
                child = creator(child_path, parent)
            self.assertEqual(parent.handle, observed["root"])
            self.assertEqual("created-child", observed["name"])
            self.assertEqual(child.identity, identity_at_path(child_path))
            self.assertTrue(
                child.identity.attributes & windows_exact_fs._FILE_ATTRIBUTE_DIRECTORY
            )
            self.assertFalse(
                child.identity.attributes & windows_exact_fs._FILE_ATTRIBUTE_REPARSE_POINT
            )
            with self.assertRaises(OSError):
                child_path.rename(displaced)
        finally:
            if child is not None and child.handle:
                child.close()
            parent.close()

        child_path.rename(displaced)
        self.assertTrue(displaced.is_dir())

    def test_rooted_directory_creation_refuses_collision_without_mutation(self) -> None:
        creator = getattr(windows_exact_fs, "create_pinned_directory_child", None)
        self.assertIsNotNone(creator)
        child_path = self.root / "existing-child"
        child_path.mkdir()
        marker = child_path / "marker.txt"
        marker.write_bytes(b"original\n")
        before = identity_at_path(child_path)
        parent = pin_stable_direct_object(
            self.root,
            kind="directory",
            allow_writes=True,
        )
        try:
            with self.assertRaises(FileExistsError):
                creator(child_path, parent)
        finally:
            parent.close()

        self.assertEqual(before, identity_at_path(child_path))
        self.assertEqual(b"original\n", marker.read_bytes())

    def test_rooted_directory_creation_requires_native_created_disposition(self) -> None:
        creator = getattr(windows_exact_fs, "create_pinned_directory_child", None)
        self.assertIsNotNone(creator)
        child_path = self.root / "uncertain-disposition-child"
        parent = pin_stable_direct_object(
            self.root,
            kind="directory",
            allow_writes=True,
        )
        real_create = windows_exact_fs._ntdll.NtCreateFile
        raw_handle = 0

        def report_opened_instead_of_created(
            output_handle,
            desired_access,
            object_attributes,
            io_status,
            allocation_size,
            file_attributes,
            share_access,
            create_disposition,
            create_options,
            ea_buffer,
            ea_length,
        ):
            nonlocal raw_handle
            status = real_create(
                output_handle,
                desired_access,
                object_attributes,
                io_status,
                allocation_size,
                file_attributes,
                share_access,
                create_disposition,
                create_options,
                ea_buffer,
                ea_length,
            )
            raw_handle = ctypes.cast(
                output_handle,
                ctypes.POINTER(windows_exact_fs.wintypes.HANDLE),
            ).contents.value
            ctypes.cast(
                io_status,
                ctypes.POINTER(windows_exact_fs._IO_STATUS_BLOCK),
            ).contents.Information = 1  # FILE_OPENED, never valid for FILE_CREATE
            return status

        ownership = None
        try:
            with (
                mock.patch.object(
                    windows_exact_fs._ntdll,
                    "NtCreateFile",
                    side_effect=report_opened_instead_of_created,
                ),
                self.assertRaises(ExactObjectOwnershipError) as raised,
            ):
                creator(child_path, parent)
            ownership = raised.exception
            self.assertEqual(
                "ExactDirectoryCreationOwnershipError",
                type(ownership).__name__,
            )
            self.assertEqual(child_path, ownership.outcome.path)
            self.assertEqual(0, ownership.outcome.status)
            self.assertEqual(1, ownership.outcome.information)
            self.assertTrue(ownership.outcome.has_valid_handle)
            self.assertFalse(ownership.outcome.proven_created)
            self.assertEqual((), ownership.candidates)
            self.assertEqual(1, len(ownership.verification))
            self.assertEqual(child_path, ownership.verification[0].path)
            self.assertEqual(raw_handle, ownership.verification[0].handle)
            ownership.resolve()
            self.assertEqual(0, ownership.verification[0].handle)
            self.assertTrue(child_path.is_dir())
        finally:
            if ownership is not None:
                for owner in ownership.owners:
                    if owner.pinned.handle:
                        windows_exact_fs._close_handle(owner.pinned.handle)
                        owner.pinned.handle = 0
            elif raw_handle:
                windows_exact_fs._close_handle(raw_handle)
            parent.close()

    def test_rooted_directory_creation_owns_failure_status_handle_as_verification(self) -> None:
        child_path = self.root / "contradictory-failure-child"
        parent = pin_stable_direct_object(
            self.root,
            kind="directory",
            allow_writes=True,
        )
        real_create = windows_exact_fs._ntdll.NtCreateFile
        raw_handle = 0
        observed_error = None

        def create_then_report_failure(
            output_handle,
            desired_access,
            object_attributes,
            io_status,
            allocation_size,
            file_attributes,
            share_access,
            create_disposition,
            create_options,
            ea_buffer,
            ea_length,
        ):
            nonlocal raw_handle
            status = real_create(
                output_handle,
                desired_access,
                object_attributes,
                io_status,
                allocation_size,
                file_attributes,
                share_access,
                create_disposition,
                create_options,
                ea_buffer,
                ea_length,
            )
            self.assertEqual(0, status)
            raw_handle = int(
                ctypes.cast(
                    output_handle,
                    ctypes.POINTER(windows_exact_fs.wintypes.HANDLE),
                ).contents.value
            )
            self.assertEqual(
                windows_exact_fs._FILE_CREATED_INFORMATION,
                int(
                    ctypes.cast(
                        io_status,
                        ctypes.POINTER(windows_exact_fs._IO_STATUS_BLOCK),
                    ).contents.Information
                ),
            )
            return ctypes.c_long(0xC0000001).value

        try:
            try:
                with mock.patch.object(
                    windows_exact_fs._ntdll,
                    "NtCreateFile",
                    side_effect=create_then_report_failure,
                ):
                    windows_exact_fs.create_pinned_directory_child(child_path, parent)
            except BaseException as error:
                observed_error = error

            self.assertIsNotNone(observed_error)
            self.assertEqual(
                "ExactDirectoryCreationOwnershipError",
                type(observed_error).__name__,
            )
            self.assertEqual(child_path, observed_error.outcome.path)
            self.assertNotEqual(0, observed_error.outcome.status)
            self.assertEqual(
                windows_exact_fs._FILE_CREATED_INFORMATION,
                observed_error.outcome.information,
            )
            self.assertTrue(observed_error.outcome.has_valid_handle)
            self.assertFalse(observed_error.outcome.proven_created)
            self.assertEqual((), observed_error.candidates)
            self.assertEqual(1, len(observed_error.verification))
            self.assertEqual(raw_handle, observed_error.verification[0].handle)

            real_close = windows_exact_fs._close_handle
            close_attempts = 0

            def fail_once(handle: int) -> None:
                nonlocal close_attempts
                close_attempts += 1
                if close_attempts == 1:
                    raise OSError("injected verification close failure")
                real_close(handle)

            with (
                mock.patch.object(
                    windows_exact_fs,
                    "_close_handle",
                    side_effect=fail_once,
                ),
                self.assertRaises(ExactObjectOwnershipError) as retry,
            ):
                observed_error.resolve()
            self.assertEqual(
                "ExactDirectoryCreationOwnershipError",
                type(retry.exception).__name__,
            )
            self.assertIs(observed_error.outcome, retry.exception.outcome)
            self.assertEqual(1, len(retry.exception.verification))
            retry.exception.resolve()
            self.assertEqual(0, observed_error.verification[0].handle)
            self.assertTrue(child_path.is_dir())
        finally:
            if raw_handle and (
                observed_error is None
                or not isinstance(observed_error, ExactObjectOwnershipError)
                or any(owner.pinned.handle for owner in observed_error.owners)
            ):
                try:
                    windows_exact_fs._close_handle(raw_handle)
                except OSError:
                    pass
            parent.close()

    def test_rooted_directory_creation_success_without_handle_is_typed_created_evidence(self) -> None:
        child_path = self.root / "created-without-handle"
        parent = pin_stable_direct_object(
            self.root,
            kind="directory",
            allow_writes=True,
        )

        def report_created_without_handle(
            _output_handle,
            _desired_access,
            _object_attributes,
            io_status,
            _allocation_size,
            _file_attributes,
            _share_access,
            _create_disposition,
            _create_options,
            _ea_buffer,
            _ea_length,
        ):
            ctypes.cast(
                io_status,
                ctypes.POINTER(windows_exact_fs._IO_STATUS_BLOCK),
            ).contents.Information = windows_exact_fs._FILE_CREATED_INFORMATION
            return 0

        try:
            with (
                mock.patch.object(
                    windows_exact_fs._ntdll,
                    "NtCreateFile",
                    side_effect=report_created_without_handle,
                ),
                self.assertRaises(ExactObjectError) as raised,
            ):
                windows_exact_fs.create_pinned_directory_child(child_path, parent)
            error = raised.exception
            self.assertEqual("ExactDirectoryCreationError", type(error).__name__)
            self.assertEqual(child_path, error.outcome.path)
            self.assertEqual(0, error.outcome.status)
            self.assertEqual(
                windows_exact_fs._FILE_CREATED_INFORMATION,
                error.outcome.information,
            )
            self.assertFalse(error.outcome.has_valid_handle)
            self.assertTrue(error.outcome.proven_created)
        finally:
            parent.close()

    def test_rooted_directory_creation_status_handle_matrix_is_verification_only(self) -> None:
        cases = (
            (ctypes.c_long(0xC0000035).value, 2),
            (0x40000000, 2),
            (0x00000103, 2),
            (0, 1),
        )
        parent = pin_stable_direct_object(
            self.root,
            kind="directory",
            allow_writes=True,
        )
        try:
            for index, (status, information) in enumerate(cases):
                with self.subTest(status=status, information=information):
                    target = self.root / f"uncertain-{index}"
                    raw_handle = 8500 + index

                    def report_uncertain(*arguments):
                        ctypes.cast(
                            arguments[0],
                            ctypes.POINTER(windows_exact_fs.wintypes.HANDLE),
                        ).contents.value = raw_handle
                        ctypes.cast(
                            arguments[3],
                            ctypes.POINTER(windows_exact_fs._IO_STATUS_BLOCK),
                        ).contents.Information = information
                        return status

                    with (
                        mock.patch.object(
                            windows_exact_fs._ntdll,
                            "NtCreateFile",
                            side_effect=report_uncertain,
                        ),
                        self.assertRaises(ExactObjectOwnershipError) as raised,
                    ):
                        windows_exact_fs.create_pinned_directory_child(target, parent)

                    error = raised.exception
                    self.assertEqual((), error.candidates)
                    self.assertEqual(1, len(error.verification))
                    self.assertEqual(raw_handle, error.verification[0].handle)
                    self.assertFalse(error.outcome.proven_created)
                    self.assertEqual(parent.path, error.outcome.parent_path)
                    self.assertEqual(parent.identity, error.outcome.parent_identity)
                    with mock.patch.object(
                        windows_exact_fs,
                        "_close_handle",
                    ) as close_handle:
                        error.resolve()
                    close_handle.assert_called_once_with(raw_handle)
                    self.assertEqual(0, error.verification[0].handle)
                    self.assertFalse(target.exists())
        finally:
            parent.close()

    def test_rooted_directory_native_interrupt_closes_before_preserving_interrupt(self) -> None:
        parent = PinnedObject(self.root, 8900, PinnedIdentity(1, 1, 0x10))
        for interruption_type in (KeyboardInterrupt, SystemExit):
            for handle in (8901, 0, windows_exact_fs._INVALID_HANDLE_VALUE):
                with self.subTest(interruption=interruption_type, handle=handle):
                    interruption = interruption_type("native call interrupted")

                    def interrupt_native(*arguments):
                        ctypes.cast(
                            arguments[0],
                            ctypes.POINTER(windows_exact_fs.wintypes.HANDLE),
                        ).contents.value = handle
                        ctypes.cast(
                            arguments[3],
                            ctypes.POINTER(windows_exact_fs._IO_STATUS_BLOCK),
                        ).contents.Information = 2
                        raise interruption

                    with (
                        mock.patch.object(windows_exact_fs, "_handle_identity", return_value=parent.identity),
                        mock.patch.object(windows_exact_fs._ntdll, "NtCreateFile", side_effect=interrupt_native),
                        mock.patch.object(windows_exact_fs, "_close_handle") as close,
                        mock.patch.object(windows_exact_fs, "delete_pinned_object") as delete,
                        self.assertRaises(interruption_type) as raised,
                    ):
                        windows_exact_fs.create_pinned_directory_child(
                            self.root / "interrupted", parent,
                            identity_at_path_fn=lambda _path: parent.identity,
                        )
                    self.assertIs(interruption, raised.exception)
                    self.assertEqual([mock.call(8901)] if handle == 8901 else [], close.call_args_list)
                    delete.assert_not_called()
                    self.assertIn("status=None", " ".join(getattr(interruption, "__notes__", ())))

    def test_rooted_directory_native_interrupt_retains_typed_verification_on_close_failure(self) -> None:
        parent = PinnedObject(self.root, 8910, PinnedIdentity(1, 1, 0x10))
        for interruption_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(interruption=interruption_type):
                interruption = interruption_type("native call interrupted")

                def interrupt_native(*arguments):
                    ctypes.cast(
                        arguments[0], ctypes.POINTER(windows_exact_fs.wintypes.HANDLE),
                    ).contents.value = 8911
                    ctypes.cast(
                        arguments[3], ctypes.POINTER(windows_exact_fs._IO_STATUS_BLOCK),
                    ).contents.Information = 2
                    raise interruption

                observed = None
                with (
                    mock.patch.object(windows_exact_fs, "_handle_identity", return_value=parent.identity),
                    mock.patch.object(windows_exact_fs._ntdll, "NtCreateFile", side_effect=interrupt_native),
                    mock.patch.object(windows_exact_fs, "_close_handle", side_effect=OSError("close failed")),
                ):
                    try:
                        windows_exact_fs.create_pinned_directory_child(
                            self.root / "interrupted", parent,
                            identity_at_path_fn=lambda _path: parent.identity,
                        )
                    except BaseException as error:
                        observed = error
                self.assertIsInstance(observed, windows_exact_fs.ExactDirectoryCreationOwnershipError)
                self.assertIs(interruption, observed.__cause__)
                self.assertIsNone(observed.outcome.status)
                self.assertEqual(2, observed.outcome.information)
                self.assertFalse(observed.outcome.proven_created)
                self.assertEqual((), observed.candidates)
                self.assertEqual((8911,), tuple(pin.handle for pin in observed.verification))
                with (
                    mock.patch.object(windows_exact_fs, "_close_handle", side_effect=SystemExit("retry interrupted")),
                    self.assertRaises(windows_exact_fs.ExactDirectoryCreationOwnershipError) as retry,
                ):
                    observed.resolve()
                self.assertIs(observed.outcome, retry.exception.outcome)
                with (
                    mock.patch.object(windows_exact_fs, "_close_handle") as close,
                    mock.patch.object(windows_exact_fs, "delete_pinned_object") as delete,
                ):
                    retry.exception.resolve()
                close.assert_called_once_with(8911)
                delete.assert_not_called()
                self.assertEqual(0, observed.verification[0].handle)

    def test_rooted_directory_creation_invalid_handle_matrix_is_explicit(self) -> None:
        parent = pin_stable_direct_object(
            self.root,
            kind="directory",
            allow_writes=True,
        )
        try:
            for handle in (0, windows_exact_fs._INVALID_HANDLE_VALUE):
                for information in (1, 2):
                    with self.subTest(handle=handle, information=information):
                        target = self.root / "unowned-result"

                        def report_unowned(*arguments):
                            ctypes.cast(
                                arguments[0],
                                ctypes.POINTER(windows_exact_fs.wintypes.HANDLE),
                            ).contents.value = handle
                            ctypes.cast(
                                arguments[3],
                                ctypes.POINTER(windows_exact_fs._IO_STATUS_BLOCK),
                            ).contents.Information = information
                            return 0

                        with (
                            mock.patch.object(
                                windows_exact_fs._ntdll,
                                "NtCreateFile",
                                side_effect=report_unowned,
                            ),
                            self.assertRaises(ExactObjectError) as raised,
                        ):
                            windows_exact_fs.create_pinned_directory_child(target, parent)

                        error = raised.exception
                        self.assertEqual(
                            "ExactDirectoryCreationError",
                            type(error).__name__,
                        )
                        self.assertEqual(target, error.outcome.path)
                        self.assertFalse(error.outcome.has_valid_handle)
                        self.assertEqual(information == 2, error.outcome.proven_created)
                        self.assertFalse(target.exists())
        finally:
            parent.close()

    def test_rooted_directory_creation_blocks_substitution_during_validation(self) -> None:
        creator = getattr(windows_exact_fs, "create_pinned_directory_child", None)
        self.assertIsNotNone(creator)
        child_path = self.root / "created-child"
        displaced = self.root / "displaced-child"
        parent = pin_stable_direct_object(
            self.root,
            kind="directory",
            allow_writes=True,
        )
        real_identity = windows_exact_fs._handle_identity
        substitution_attempted = False
        substitution_succeeded = False

        def inspect_identity(handle: int, path: Path) -> PinnedIdentity:
            nonlocal substitution_attempted, substitution_succeeded
            if path == child_path and not substitution_attempted:
                substitution_attempted = True
                try:
                    child_path.rename(displaced)
                except OSError:
                    pass
                else:
                    substitution_succeeded = True
                    child_path.mkdir()
            return real_identity(handle, path)

        child = None
        try:
            with mock.patch.object(
                windows_exact_fs,
                "_handle_identity",
                side_effect=inspect_identity,
            ):
                child = creator(child_path, parent)
            self.assertTrue(substitution_attempted)
            self.assertFalse(substitution_succeeded)
            self.assertFalse(displaced.exists())
            self.assertEqual(child.identity, identity_at_path(child_path))
        finally:
            if child is not None and child.handle:
                child.close()
            parent.close()

    def test_rooted_directory_creation_rejects_foreign_volume_child_identity(self) -> None:
        creator = getattr(windows_exact_fs, "create_pinned_directory_child", None)
        self.assertIsNotNone(creator)
        child_path = self.root / "foreign-volume-child"
        parent = pin_stable_direct_object(
            self.root,
            kind="directory",
            allow_writes=True,
        )
        real_identity = windows_exact_fs._handle_identity

        def foreign_child_identity(handle: int, path: Path) -> PinnedIdentity:
            identity = real_identity(handle, path)
            if path == child_path:
                return replace(identity, volume_serial=identity.volume_serial + 1)
            return identity

        try:
            with (
                mock.patch.object(
                    windows_exact_fs,
                    "_handle_identity",
                    side_effect=foreign_child_identity,
                ),
                self.assertRaisesRegex(ExactObjectError, "same volume"),
            ):
                creator(child_path, parent)
        finally:
            parent.close()
        self.assertFalse(child_path.exists())

    def test_rooted_directory_creation_validates_parent_path_and_direct_child(self) -> None:
        creator = getattr(windows_exact_fs, "create_pinned_directory_child", None)
        self.assertIsNotNone(creator)
        parent = pin_stable_direct_object(
            self.root,
            kind="directory",
            allow_writes=True,
        )
        wrong_parent = replace(
            parent.identity,
            volume_serial=parent.identity.volume_serial + 1,
        )
        try:
            with (
                mock.patch.object(windows_exact_fs._ntdll, "NtCreateFile") as native,
                self.assertRaisesRegex(ExactObjectError, "parent path changed"),
            ):
                creator(
                    self.root / "child",
                    parent,
                    identity_at_path_fn=lambda _path: wrong_parent,
                )
            native.assert_not_called()
            with (
                mock.patch.object(windows_exact_fs._ntdll, "NtCreateFile") as native,
                self.assertRaisesRegex(ExactObjectError, "parent mismatch"),
            ):
                creator(self.root.parent / "escaped-child", parent)
            native.assert_not_called()
        finally:
            parent.close()

    def test_rooted_directory_creation_preserves_interrupt_after_exact_cleanup(self) -> None:
        child_path = self.root / "interrupted-created-child"
        parent = pin_stable_direct_object(
            self.root,
            kind="directory",
            allow_writes=True,
        )

        def interrupted_identity(path: Path) -> PinnedIdentity:
            if path == child_path:
                raise KeyboardInterrupt("injected late validation interruption")
            return identity_at_path(path)

        observed = None
        try:
            try:
                windows_exact_fs.create_pinned_directory_child(
                    child_path,
                    parent,
                    identity_at_path_fn=interrupted_identity,
                )
            except BaseException as error:
                observed = error
            self.assertIsInstance(observed, KeyboardInterrupt)
            self.assertFalse(child_path.exists())
        finally:
            parent.close()

    def test_rooted_directory_creation_cleanup_failure_returns_exact_owner(self) -> None:
        creator = getattr(windows_exact_fs, "create_pinned_directory_child", None)
        self.assertIsNotNone(creator)
        child_path = self.root / "invalid-created-child"
        parent = pin_stable_direct_object(
            self.root,
            kind="directory",
            allow_writes=True,
        )
        real_identity = windows_exact_fs._handle_identity
        real_delete = windows_exact_fs.delete_pinned_object

        def invalid_child_identity(handle: int, path: Path) -> PinnedIdentity:
            identity = real_identity(handle, path)
            if path == child_path:
                return replace(
                    identity,
                    attributes=identity.attributes
                    | windows_exact_fs._FILE_ATTRIBUTE_REPARSE_POINT,
                )
            return identity

        ownership = None
        try:
            with (
                mock.patch.object(
                    windows_exact_fs,
                    "_handle_identity",
                    side_effect=invalid_child_identity,
                ),
                mock.patch.object(
                    windows_exact_fs,
                    "delete_pinned_object",
                    side_effect=OSError("injected exact cleanup failure"),
                ),
                self.assertRaises(ExactObjectOwnershipError) as raised,
            ):
                creator(child_path, parent)
            ownership = raised.exception
            self.assertEqual(
                "ExactDirectoryCreationOwnershipError",
                type(ownership).__name__,
            )
            self.assertTrue(ownership.outcome.proven_created)
            self.assertTrue(ownership.outcome.has_valid_handle)
            self.assertIsNotNone(ownership.candidate)
            self.assertEqual(child_path, ownership.candidate.path)
            self.assertNotEqual(0, ownership.candidate.handle)
            with mock.patch.object(
                windows_exact_fs,
                "delete_pinned_object",
                side_effect=real_delete,
            ):
                ownership.resolve()
            self.assertEqual(0, ownership.candidate.handle)
        finally:
            if ownership is not None:
                for retained in ownership.retained_objects:
                    if retained.handle:
                        real_delete(retained)
            parent.close()
        self.assertFalse(child_path.exists())

    def test_post_rename_validator_failure_deletes_the_moved_exact_candidate(self) -> None:
        destination = self.root / "outcome.json"
        validations = 0

        def validate(data: bytes) -> bytes:
            nonlocal validations
            validations += 1
            if validations == 2:
                raise ExactObjectError("injected post-rename validation failure")
            return data

        with self.assertRaisesRegex(ExactObjectError, "post-rename"):
            publish_new_pinned(destination, b"payload", validate)

        self.assertEqual(2, validations)
        self.assertFalse(destination.exists())
        self.assertEqual([], list(self.root.glob(".*.tmp")))

    def test_failed_post_publish_cleanup_returns_the_live_retained_candidate(self) -> None:
        destination = self.root / "outcome.json"
        real_close = PinnedObject.close
        real_create = windows_exact_fs.create_pinned_new
        candidate_owner = None

        def record_candidate(path, destination_parent):
            nonlocal candidate_owner
            candidate_owner = real_create(path, destination_parent)
            return candidate_owner

        def fail_final_candidate_close(pinned):
            if pinned is candidate_owner and pinned.path == destination:
                raise OSError("injected candidate close failure")
            return real_close(pinned)

        retained = None
        try:
            with (
                mock.patch.object(
                    PinnedObject,
                    "close",
                    new=fail_final_candidate_close,
                ),
                mock.patch.object(
                    windows_exact_fs,
                    "create_pinned_new",
                    side_effect=record_candidate,
                ),
                mock.patch.object(
                    windows_exact_fs,
                    "delete_pinned_object",
                    side_effect=OSError("injected exact cleanup failure"),
                ),
            ):
                with self.assertRaises(ExactObjectOwnershipError) as raised:
                    publish_new_pinned(destination, b"payload", lambda data: data)
            retained = raised.exception.pinned_object
            self.assertEqual(destination, retained.path)
            self.assertNotEqual(0, retained.handle)
        finally:
            if retained is not None and retained.handle:
                windows_exact_fs.delete_pinned_object(retained)

    def test_ownership_resolver_preserves_explicit_roles_across_retries(self) -> None:
        candidate = PinnedObject(
            self.root / "outcome.json", 101, PinnedIdentity(1, 1, 0)
        )
        parent = PinnedObject(self.root, 102, PinnedIdentity(1, 2, 0x10))
        ownership = ExactObjectOwnershipError(
            "injected ownership failure",
            candidate=candidate,
            destination_parent=parent,
        )
        with (
            mock.patch.object(
                windows_exact_fs,
                "delete_pinned_object",
                side_effect=OSError("injected delete failure"),
            ),
            mock.patch.object(
                PinnedObject,
                "close",
                side_effect=OSError("injected parent close failure"),
            ),
        ):
            with self.assertRaises(ExactObjectOwnershipError) as raised:
                resolve_retained_ownership(ownership)

        self.assertIs(candidate, raised.exception.candidate)
        self.assertIs(parent, raised.exception.destination_parent)
        self.assertEqual((candidate, parent), raised.exception.retained_objects)

        def exact_delete(pinned):
            pinned.handle = 0

        def exact_close(pinned):
            pinned.handle = 0

        with (
            mock.patch.object(windows_exact_fs, "delete_pinned_object", side_effect=exact_delete),
            mock.patch.object(PinnedObject, "close", new=exact_close),
        ):
            resolve_retained_ownership(raised.exception)

        self.assertEqual(0, candidate.handle)
        self.assertEqual(0, parent.handle)

    def test_repeated_retry_deletes_only_exact_moved_candidate_and_closes_all_roles(self) -> None:
        candidate_path = self.root / "candidate.json"
        moved_path = self.root / "moved-candidate.json"
        candidate_path.write_bytes(b"original")
        candidate = pin_direct_object(candidate_path, kind="file")
        parent = pin_direct_object(self.root, kind="directory")
        candidate_path.rename(moved_path)
        candidate_path.write_bytes(b"pathname substitute")
        ownership = ExactObjectOwnershipError(
            "injected ownership failure",
            candidate=candidate,
            destination_parent=parent,
        )

        real_set_information = windows_exact_fs._kernel32.SetFileInformationByHandle
        real_close_handle = windows_exact_fs._kernel32.CloseHandle
        delete_failures = 0
        parent_close_failures = 0

        def fail_first_two_deletes(handle, information_class, information, size):
            nonlocal delete_failures
            if (
                handle == candidate.handle
                and information_class == windows_exact_fs._FILE_DISPOSITION_INFO_CLASS
                and delete_failures < 2
            ):
                delete_failures += 1
                ctypes.set_last_error(5)
                return False
            return real_set_information(handle, information_class, information, size)

        def fail_first_two_parent_closes(handle):
            nonlocal parent_close_failures
            if handle == parent.handle and parent_close_failures < 2:
                parent_close_failures += 1
                ctypes.set_last_error(5)
                return False
            return real_close_handle(handle)

        try:
            with (
                mock.patch.object(
                    windows_exact_fs._kernel32,
                    "SetFileInformationByHandle",
                    side_effect=fail_first_two_deletes,
                ),
                mock.patch.object(
                    windows_exact_fs._kernel32,
                    "CloseHandle",
                    side_effect=fail_first_two_parent_closes,
                ),
            ):
                for _attempt in range(2):
                    with self.assertRaises(ExactObjectOwnershipError) as raised:
                        ownership.resolve()
                    ownership = raised.exception
                    self.assertEqual((candidate, parent), ownership.retained_objects)
                    self.assertEqual(
                        b"original",
                        windows_exact_fs.read_pinned_file(candidate),
                    )
                    self.assertEqual(b"pathname substitute", candidate_path.read_bytes())

                ownership.resolve()

            self.assertEqual(2, delete_failures)
            self.assertEqual(2, parent_close_failures)
            self.assertEqual(0, candidate.handle)
            self.assertEqual(0, parent.handle)
            self.assertFalse(moved_path.exists())
            self.assertEqual(b"pathname substitute", candidate_path.read_bytes())
        finally:
            if candidate.handle:
                real_close_handle(candidate.handle)
                candidate.handle = 0
            if parent.handle:
                real_close_handle(parent.handle)
                parent.handle = 0

    def test_all_containment_immutable_publishers_use_the_shared_primitive(self) -> None:
        from modlab.validation import mo2_containment_store, windows_junction, windows_watch
        from modlab.validation import windows_watch_protocol

        self.assertIn(
            "publish_new_pinned",
            inspect.getsource(windows_watch._write_new),
        )
        self.assertIn(
            "publish_new_pinned",
            inspect.getsource(windows_watch._publish_outcome_commit),
        )
        self.assertIn(
            "publish_new_pinned",
            inspect.getsource(windows_watch_protocol.publish_new_verified),
        )
        self.assertIn(
            "publish_new_pinned",
            inspect.getsource(mo2_containment_store.ContainmentStore._write_immutable),
        )
        self.assertEqual(
            1,
            inspect.getsource(mo2_containment_store.ContainmentStore).count(
                "self._atomic_replace("
            ),
        )
        self.assertIn(
            "rename_pinned_no_replace",
            inspect.getsource(windows_junction._rename_pinned_object),
        )

    def test_watcher_new_write_normalizes_an_exact_object_failure(self) -> None:
        from modlab.validation import windows_watch

        with mock.patch.object(
            windows_watch,
            "publish_new_pinned",
            side_effect=ExactObjectError("injected exact failure"),
        ):
            with self.assertRaisesRegex(
                windows_watch.WatchProtocolError,
                "exact immutable write failed",
            ):
                windows_watch._write_new(self.root / "ready.json", b"ready\n")


if __name__ == "__main__":
    unittest.main()
