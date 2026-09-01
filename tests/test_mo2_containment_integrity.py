import hashlib
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import time
import unittest
from unittest import mock

from modlab.validation.mo2_containment_model import IntegrityObservation
from modlab.validation import windows_integrity
from modlab.validation.windows_integrity import (
    IntegrityLabelBatchError,
    IntegrityLabelError,
    IntegrityLevel,
    inspect_path_integrity,
    inspect_process_integrity,
    launch_low_integrity_process,
    set_low_integrity_tree,
    set_medium_integrity_entries,
    set_medium_integrity_tree,
    source_integrity_allowed,
    stage_integrity_allowed,
    to_integrity_observation,
)


PYTHON = Path(sys.executable)


def _create_disposable_junction(link: Path, target: Path) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.DeviceIoControl.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    kernel32.DeviceIoControl.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    link.mkdir()
    handle = kernel32.CreateFileW(
        str(link),
        0x40000000,
        0x00000001 | 0x00000002 | 0x00000004,
        None,
        3,
        0x00200000 | 0x02000000,
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        substitute = ("\\??\\" + str(target)).encode("utf-16-le")
        display = str(target).encode("utf-16-le")
        path_buffer = substitute + b"\0\0" + display + b"\0\0"
        payload = struct.pack(
            "<IHHHHHH",
            0xA0000003,
            8 + len(path_buffer),
            0,
            0,
            len(substitute),
            len(substitute) + 2,
            len(display),
        ) + path_buffer
        native_buffer = ctypes.create_string_buffer(payload)
        returned = wintypes.DWORD()
        if not kernel32.DeviceIoControl(
            handle,
            0x000900A4,
            native_buffer,
            len(payload),
            None,
            0,
            ctypes.byref(returned),
            None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel32.CloseHandle(handle)


def _open_delete_denying_path(path: Path, *, is_directory: bool):
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    desired_access = (
        0x00010000 | 0x00000080
        if is_directory
        else 0x00010000 | 0x80000000
    )
    handle = kernel32.CreateFileW(
        str(path),
        desired_access,
        0x00000001,
        None,
        3,
        0x00200000 | 0x02000000,
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return kernel32, handle


class IntegrityPolicyTests(unittest.TestCase):
    def test_source_must_be_medium_or_higher_and_stage_must_be_low(self):
        self.assertFalse(source_integrity_allowed(IntegrityLevel.UNTRUSTED))
        self.assertFalse(source_integrity_allowed(IntegrityLevel.LOW))
        self.assertTrue(source_integrity_allowed(IntegrityLevel.MEDIUM))
        self.assertTrue(source_integrity_allowed(IntegrityLevel.HIGH))
        self.assertTrue(source_integrity_allowed(IntegrityLevel.SYSTEM))
        self.assertFalse(source_integrity_allowed(0x2000))

        self.assertFalse(stage_integrity_allowed(IntegrityLevel.UNTRUSTED))
        self.assertTrue(stage_integrity_allowed(IntegrityLevel.LOW))
        self.assertFalse(stage_integrity_allowed(IntegrityLevel.MEDIUM))
        self.assertFalse(stage_integrity_allowed(IntegrityLevel.HIGH))
        self.assertFalse(stage_integrity_allowed(IntegrityLevel.SYSTEM))

    def test_native_integrity_conversion_is_exhaustive_and_unknown_fails_closed(self):
        expected = {
            IntegrityLevel.UNTRUSTED: IntegrityObservation.UNTRUSTED,
            IntegrityLevel.LOW: IntegrityObservation.LOW,
            IntegrityLevel.MEDIUM: IntegrityObservation.MEDIUM,
            IntegrityLevel.HIGH: IntegrityObservation.HIGH,
            IntegrityLevel.SYSTEM: IntegrityObservation.SYSTEM,
            None: IntegrityObservation.UNKNOWN,
        }

        for native, portable in expected.items():
            with self.subTest(native=native):
                self.assertIs(portable, to_integrity_observation(native))

        for unknown in (0x1000, 0x5000):
            with self.subTest(unknown=unknown), self.assertRaises(ValueError):
                to_integrity_observation(unknown)


@unittest.skipUnless(os.name == "nt", "Windows integrity APIs are unavailable")
class WindowsIntegrityTests(unittest.TestCase):
    def test_medium_entries_snapshots_stateful_path_once_and_preserves_receipts(self):
        class RedirectingPath(type(Path())):
            def __new__(cls, intended: Path, redirect: Path):
                instance = super().__new__(cls, intended)
                instance._intended = os.fspath(intended)
                instance._redirect = os.fspath(redirect)
                instance.snapshot_calls = 0
                return instance

            def _next_path(self):
                self.snapshot_calls += 1
                if self.snapshot_calls == 1:
                    return self._intended
                return self._redirect

            def __fspath__(self):
                return self._next_path()

            def __str__(self):
                return self._next_path()

        with tempfile.TemporaryDirectory(
            prefix="modlab-integrity-entries-snapshot-"
        ) as temporary:
            root = Path(temporary)
            first = root / "first.bin"
            intended = root / "intended.bin"
            redirect = root / "redirect.bin"
            for path in (first, intended, redirect):
                path.write_bytes(path.name.encode("ascii"))
            set_low_integrity_tree(root)
            stateful = RedirectingPath(intended, redirect)
            real_inspect = windows_integrity.inspect_path_integrity
            verification_error = OSError("injected intended-object inspection failure")

            def inspect_or_fail(path):
                if Path(path) == intended:
                    raise verification_error
                return real_inspect(path)

            with mock.patch.object(
                windows_integrity,
                "inspect_path_integrity",
                side_effect=inspect_or_fail,
            ):
                with self.assertRaises(IntegrityLabelBatchError) as captured:
                    set_medium_integrity_entries((first, stateful))

            error = captured.exception
            self.assertIs(verification_error, error.__cause__)
            self.assertEqual(1, stateful.snapshot_calls)
            self.assertEqual(
                (str(first), str(intended)),
                tuple(receipt.arguments[0] for receipt in error.evidence.receipts),
            )
            self.assertEqual(str(intended), error.evidence.failed_path)
            self.assertEqual(IntegrityLevel.MEDIUM, real_inspect(first))
            self.assertEqual(IntegrityLevel.MEDIUM, real_inspect(intended))
            self.assertEqual(IntegrityLevel.LOW, real_inspect(redirect))

    def test_medium_entries_rejects_invalid_or_reparse_inputs_before_mutation(self):
        with tempfile.TemporaryDirectory(prefix="modlab-integrity-entries-invalid-") as temporary:
            root = Path(temporary)
            direct_file = root / "Entry.bin"
            missing = root / "missing.bin"
            outside = root / "outside"
            junction = root / "redirect"
            direct_file.write_bytes(b"entry")
            outside.mkdir()
            _create_disposable_junction(junction, outside)
            try:
                cases = (
                    [direct_file],
                    (),
                    (Path("relative.bin"),),
                    (direct_file, direct_file.with_name("ENTRY.BIN")),
                    (missing,),
                    (junction,),
                )
                for paths in cases:
                    with self.subTest(paths=paths), self.assertRaises((TypeError, ValueError)):
                        set_medium_integrity_entries(paths)
            finally:
                junction.rmdir()

            self.assertEqual(IntegrityLevel.MEDIUM, inspect_path_integrity(direct_file))

    def test_medium_entries_labels_every_pinned_nested_object_without_recursive_walk(self):
        with tempfile.TemporaryDirectory(prefix="modlab-integrity-entries-pinned-") as temporary:
            root = Path(temporary) / "root"
            child = root / "child"
            payload = child / "payload.bin"
            child.mkdir(parents=True)
            payload.write_bytes(b"payload")
            set_low_integrity_tree(root)
            pins = []
            try:
                pins.append(_open_delete_denying_path(root, is_directory=True))
                pins.append(_open_delete_denying_path(child, is_directory=True))
                pins.append(_open_delete_denying_path(payload, is_directory=False))
                with self.assertRaises(IntegrityLabelError) as recursive:
                    set_medium_integrity_tree(root)
                self.assertEqual(0, recursive.exception.receipt.exit_code)
                self.assertEqual(IntegrityLevel.LOW, inspect_path_integrity(payload))

                receipts = set_medium_integrity_entries((root, child, payload))
                self.assertEqual(3, len(receipts))
                self.assertEqual(
                    (str(root), "/setintegritylevel", "(OI)(CI)M", "/Q"),
                    receipts[0].arguments,
                )
                self.assertEqual(
                    (str(child), "/setintegritylevel", "(OI)(CI)M", "/Q"),
                    receipts[1].arguments,
                )
                self.assertEqual(
                    (str(payload), "/setintegritylevel", "M", "/Q"),
                    receipts[2].arguments,
                )
                for receipt in receipts:
                    self.assertEqual(0, receipt.exit_code)
                    self.assertNotIn("/T", receipt.arguments)
                    self.assertNotIn("/C", receipt.arguments)
                for entry in (root, child, payload):
                    with self.subTest(entry=entry):
                        self.assertEqual(
                            IntegrityLevel.MEDIUM, inspect_path_integrity(entry)
                        )
            finally:
                for kernel32, pin in reversed(pins):
                    kernel32.CloseHandle(pin)

    def test_medium_entries_partial_verification_failure_preserves_all_command_receipts(self):
        with tempfile.TemporaryDirectory(prefix="modlab-integrity-entries-receipts-") as temporary:
            root = Path(temporary)
            first = root / "first.bin"
            second = root / "second.bin"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            set_low_integrity_tree(root)
            real_inspect = windows_integrity.inspect_path_integrity
            verification_error = OSError("injected second-object inspection failure")

            def inspect_or_fail(path):
                if Path(path) == second:
                    raise verification_error
                return real_inspect(path)

            with mock.patch.object(
                windows_integrity,
                "inspect_path_integrity",
                side_effect=inspect_or_fail,
            ):
                with self.assertRaises(IntegrityLabelBatchError) as captured:
                    set_medium_integrity_entries((first, second))

            error = captured.exception
            self.assertIs(verification_error, error.__cause__)
            self.assertEqual(2, len(error.evidence.receipts))
            self.assertEqual(str(first), error.evidence.receipts[0].arguments[0])
            self.assertEqual(str(second), error.evidence.receipts[1].arguments[0])
            self.assertEqual((0, 0), tuple(r.exit_code for r in error.evidence.receipts))
            self.assertEqual(IntegrityLevel.MEDIUM, real_inspect(first))
            self.assertEqual(IntegrityLevel.MEDIUM, real_inspect(second))

    def test_medium_directory_entry_has_native_inheritable_label_flags(self):
        with tempfile.TemporaryDirectory(
            prefix="modlab-integrity-entries-flags-"
        ) as temporary:
            directory = Path(temporary) / "directory"
            directory.mkdir()
            set_low_integrity_tree(directory)

            receipts = set_medium_integrity_entries((directory,))
            observed, ace_flags = windows_integrity._inspect_path_integrity_evidence(
                directory
            )

            self.assertEqual(1, len(receipts))
            self.assertEqual(IntegrityLevel.MEDIUM, observed)
            self.assertIsNotNone(ace_flags)
            self.assertEqual(0x03, ace_flags & 0x03)

    def test_medium_directory_entry_rejects_missing_inheritance_flags_with_receipt(self):
        with tempfile.TemporaryDirectory(
            prefix="modlab-integrity-entries-flags-error-"
        ) as temporary:
            directory = Path(temporary) / "directory"
            directory.mkdir()
            set_low_integrity_tree(directory)

            with mock.patch.object(
                windows_integrity,
                "_inspect_path_integrity_evidence",
                return_value=(IntegrityLevel.MEDIUM, 0),
                create=True,
            ):
                with self.assertRaises(IntegrityLabelBatchError) as captured:
                    set_medium_integrity_entries((directory,))

            error = captured.exception
            self.assertEqual(1, len(error.evidence.receipts))
            self.assertEqual(0, error.evidence.receipts[0].exit_code)
            self.assertEqual(str(directory), error.evidence.failed_path)
            self.assertIsInstance(error.__cause__, OSError)
            self.assertIn("inheritance flags", str(error.__cause__))

    def test_medium_directory_entry_rejects_low_second_native_observation(self):
        with tempfile.TemporaryDirectory(
            prefix="modlab-integrity-entries-inconsistent-"
        ) as temporary:
            directory = Path(temporary) / "directory"
            directory.mkdir()
            set_low_integrity_tree(directory)

            with (
                mock.patch.object(
                    windows_integrity,
                    "inspect_path_integrity",
                    return_value=IntegrityLevel.MEDIUM,
                ),
                mock.patch.object(
                    windows_integrity,
                    "_inspect_path_integrity_evidence",
                    return_value=(IntegrityLevel.LOW, 0x03),
                ),
            ):
                with self.assertRaises(IntegrityLabelBatchError) as captured:
                    set_medium_integrity_entries((directory,))

            error = captured.exception
            self.assertEqual(1, len(error.evidence.receipts))
            self.assertEqual(0, error.evidence.receipts[0].exit_code)
            self.assertEqual(str(directory), error.evidence.failed_path)
            self.assertIsInstance(error.__cause__, OSError)
            self.assertIn("LOW", str(error.__cause__))

    def test_post_icacls_verification_error_preserves_receipt_and_cause(self):
        with tempfile.TemporaryDirectory(prefix="modlab-integrity-receipt-") as temporary:
            root = Path(temporary)
            stage = root / "stage"
            stage.mkdir()
            verification_error = OSError("injected native inspection failure")

            with mock.patch.object(
                windows_integrity,
                "inspect_path_integrity",
                side_effect=verification_error,
            ):
                with self.assertRaises(IntegrityLabelError) as captured:
                    set_low_integrity_tree(stage)

            error = captured.exception
            self.assertIs(verification_error, error.__cause__)
            self.assertEqual(r"C:\Windows\System32\icacls.exe", error.receipt.executable)
            self.assertEqual(0, error.receipt.exit_code)
            self.assertEqual(str(stage), error.receipt.arguments[0])
            self.assertEqual("/setintegritylevel", error.receipt.arguments[1])
            self.assertEqual("(OI)(CI)L", error.receipt.arguments[2])

    def test_explicit_label_without_no_write_up_policy_is_rejected(self):
        import ctypes

        sid = struct.pack(
            "<BB6sI",
            1,
            1,
            b"\0\0\0\0\0\x10",
            int(IntegrityLevel.LOW),
        )

        def label_ace(mask: int):
            payload = struct.pack("<BBHI", 0x11, 0, 8 + len(sid), mask) + sid
            return ctypes.create_string_buffer(payload)

        enforced = label_ace(0x1)
        self.assertEqual(
            IntegrityLevel.LOW,
            windows_integrity._integrity_from_label_ace(ctypes.addressof(enforced)),
        )

        unenforced = label_ace(0x0)
        with self.assertRaisesRegex(OSError, "no-write-up"):
            windows_integrity._integrity_from_label_ace(ctypes.addressof(unenforced))

    def test_low_child_without_no_write_up_token_policy_is_terminated_before_resume(self):
        with tempfile.TemporaryDirectory(prefix="modlab-integrity-policy-") as temporary:
            root = Path(temporary)
            stage = root / "stage"
            stage.mkdir()
            marker = stage / "must-not-run.marker"
            set_low_integrity_tree(stage)
            launch = None

            code = "from pathlib import Path; import sys; Path(sys.argv[1]).write_bytes(b'ran')"
            try:
                with mock.patch.object(
                    windows_integrity,
                    "_token_mandatory_policy",
                    return_value=0,
                    create=True,
                ):
                    with self.assertRaisesRegex(OSError, "no-write-up"):
                        launch = launch_low_integrity_process(
                            PYTHON,
                            ("-B", "-c", code, str(marker)),
                            root,
                            dict(os.environ),
                        )
            finally:
                if launch is not None:
                    self._wait_for_process_exit(launch.pid)

            self.assertFalse(marker.exists())

    def test_ordering_test_retains_root_and_primary_when_exit_wait_fails(self):
        primary_error = AssertionError("injected primary marker failure")
        cleanup_error = OSError("injected cleanup wait failure")
        launch = mock.Mock(pid=424242)
        retained_root = Path(tempfile.mkdtemp(prefix="modlab-integrity-retained-"))
        caught = None

        try:
            with (
                mock.patch.object(
                    tempfile, "mkdtemp", return_value=str(retained_root)
                ),
                mock.patch(f"{__name__}.set_low_integrity_tree"),
                mock.patch(
                    f"{__name__}.launch_low_integrity_process", return_value=launch
                ),
                mock.patch.object(self, "_wait_for_file", side_effect=primary_error),
                mock.patch.object(
                    self, "_wait_for_process_exit", side_effect=cleanup_error
                ),
            ):
                try:
                    self.test_child_is_low_before_its_first_instruction_runs()
                except BaseException as observed:
                    caught = observed
                else:
                    self.fail(
                        "ordering test unexpectedly returned without its primary error"
                    )

            self.assertIs(primary_error, caught)
            self.assertEqual("injected primary marker failure", str(caught))
            self.assertTrue(retained_root.is_dir())
            notes = getattr(caught, "__notes__", ())
            self.assertTrue(any("cleanup wait failure" in note for note in notes))
            self.assertTrue(any(str(retained_root) in note for note in notes))
        finally:
            if retained_root.exists():
                shutil.rmtree(retained_root)

    def test_ordering_test_retains_root_when_launch_raises_after_attempt(self):
        primary_error = OSError("injected post-create launch failure")
        retained_root = Path(tempfile.mkdtemp(prefix="modlab-integrity-launch-error-"))
        deletion_attempts = []
        caught = None

        def record_deletion(path):
            deletion_attempts.append(Path(path))

        try:
            with (
                mock.patch.object(
                    tempfile, "mkdtemp", return_value=str(retained_root)
                ),
                mock.patch(f"{__name__}.set_low_integrity_tree"),
                mock.patch(
                    f"{__name__}.launch_low_integrity_process",
                    side_effect=primary_error,
                ),
                mock.patch.object(shutil, "rmtree", side_effect=record_deletion),
            ):
                try:
                    self.test_child_is_low_before_its_first_instruction_runs()
                except BaseException as observed:
                    caught = observed
                else:
                    self.fail("ordering test unexpectedly accepted launch failure")

            self.assertIs(primary_error, caught)
            self.assertEqual("injected post-create launch failure", str(caught))
            self.assertEqual([], deletion_attempts)
            self.assertTrue(retained_root.is_dir())
            notes = getattr(caught, "__notes__", ())
            self.assertTrue(any(str(retained_root) in note for note in notes))
        finally:
            if retained_root.exists():
                shutil.rmtree(retained_root)

    def test_child_is_low_before_its_first_instruction_runs(self):
        root = Path(tempfile.mkdtemp(prefix="modlab-integrity-order-"))
        launch = None
        launch_attempted = False
        primary_error = None
        try:
            stage = root / "stage"
            stage.mkdir()
            marker = stage / "first-instruction.marker"
            set_low_integrity_tree(stage)
            real_resume = windows_integrity._resume_verified_child
            observed_barrier = []

            def inspect_barrier(process, thread, pid):
                self.assertFalse(marker.exists())
                self.assertEqual(IntegrityLevel.LOW, inspect_process_integrity(pid))
                observed_barrier.append(pid)
                return real_resume(process, thread, pid)

            code = "from pathlib import Path; import sys; Path(sys.argv[1]).write_bytes(b'ran')"
            with mock.patch.object(
                windows_integrity,
                "_resume_verified_child",
                side_effect=inspect_barrier,
            ):
                launch_attempted = True
                launch = launch_low_integrity_process(
                    PYTHON,
                    ("-B", "-c", code, str(marker)),
                    root,
                    dict(os.environ),
                )

            self._wait_for_file(marker)
            self.assertEqual([launch.pid], observed_barrier)
            self.assertEqual(b"ran", marker.read_bytes())
        except BaseException as error:
            primary_error = error
            raise
        finally:
            exit_confirmed = not launch_attempted
            if launch is not None:
                try:
                    self._wait_for_process_exit(launch.pid)
                except BaseException as wait_error:
                    retained = f"retained test root {root}"
                    if primary_error is None:
                        wait_error.add_note(retained)
                        raise
                    primary_error.add_note(
                        f"cleanup wait for child pid {launch.pid} failed; "
                        f"{retained}: {wait_error!r}"
                    )
                else:
                    exit_confirmed = True
            elif launch_attempted:
                retained = f"retained test root {root}"
                if primary_error is None:
                    lifecycle_error = RuntimeError(
                        "launch attempt returned no exact child PID"
                    )
                    lifecycle_error.add_note(retained)
                    raise lifecycle_error
                primary_error.add_note(
                    f"launch attempt returned no exact child PID; "
                    f"exit unconfirmed; {retained}"
                )
            if exit_confirmed:
                try:
                    shutil.rmtree(root)
                except BaseException as deletion_error:
                    retained = f"retained test root {root}"
                    if primary_error is None:
                        deletion_error.add_note(retained)
                        raise
                    primary_error.add_note(
                        f"test root deletion failed; {retained}: "
                        f"{deletion_error!r}"
                    )

    def test_pre_resume_failure_terminates_exact_child_without_running_marker(self):
        with tempfile.TemporaryDirectory(prefix="modlab-integrity-failure-") as temporary:
            root = Path(temporary)
            stage = root / "stage"
            stage.mkdir()
            marker = stage / "must-not-run.marker"
            set_low_integrity_tree(stage)
            suspended_pid = []
            handles_before = self._handle_count()

            def fail_before_resume(process, thread, pid):
                self.assertFalse(marker.exists())
                self.assertEqual(IntegrityLevel.LOW, inspect_process_integrity(pid))
                suspended_pid.append(pid)
                raise OSError("injected pre-resume failure")

            code = "from pathlib import Path; import sys; Path(sys.argv[1]).write_bytes(b'ran')"
            with mock.patch.object(
                windows_integrity,
                "_resume_verified_child",
                side_effect=fail_before_resume,
            ):
                with self.assertRaisesRegex(OSError, "injected pre-resume failure"):
                    launch_low_integrity_process(
                        PYTHON,
                        ("-B", "-c", code, str(marker)),
                        root,
                        dict(os.environ),
                    )

            self.assertEqual(1, len(suspended_pid))
            self._wait_for_process_exit(suspended_pid[0])
            self.assertFalse(marker.exists())
            self.assertLessEqual(self._handle_count(), handles_before + 2)

    def test_low_process_can_write_low_stage_but_not_medium_source(self):
        with tempfile.TemporaryDirectory(prefix="modlab-integrity-") as temporary:
            root = Path(temporary)
            source = root / "source"
            stage = root / "stage"
            source.mkdir()
            stage.mkdir()
            source_file = source / "source.bin"
            stage_file = stage / "stage.bin"
            result_file = stage / "result.txt"
            source_file.write_bytes(b"source-original")

            medium_receipt = set_medium_integrity_tree(source)
            low_receipt = set_low_integrity_tree(stage)

            code = (
                "from pathlib import Path; import sys; "
                "stage, source, result = map(Path, sys.argv[1:]); "
                "stage.write_bytes(b'stage-write'); "
                "error = 'NO_ERROR'; "
                "\ntry:\n source.write_bytes(b'source-write')"
                "\nexcept Exception as exc:\n error = type(exc).__name__"
                "\nresult.write_text(error, encoding='ascii'); "
                "import time; time.sleep(2)"
            )
            launch = launch_low_integrity_process(
                PYTHON,
                ("-B", "-c", code, str(stage_file), str(source_file), str(result_file)),
                root,
                dict(os.environ),
            )
            self._wait_for_file(result_file)

            self.assertEqual(IntegrityLevel.MEDIUM, medium_receipt.integrity)
            self.assertEqual(IntegrityLevel.LOW, low_receipt.integrity)
            self.assertEqual("C:\\Windows\\System32\\icacls.exe", low_receipt.executable)
            self.assertEqual(0, low_receipt.exit_code)
            self.assertEqual(hashlib.sha256(b"").hexdigest(), low_receipt.stderr_sha256)
            self.assertEqual(IntegrityLevel.LOW, launch.integrity)
            self.assertEqual(IntegrityLevel.LOW, inspect_process_integrity(launch.pid))
            self.assertEqual(b"stage-write", stage_file.read_bytes())
            self.assertEqual(b"source-original", source_file.read_bytes())
            self.assertEqual("PermissionError", result_file.read_text(encoding="ascii"))
            self._wait_for_process_exit(launch.pid)

    def test_integrity_labeling_rejects_a_reparse_descendant_before_icacls(self):
        with tempfile.TemporaryDirectory(prefix="modlab-integrity-reparse-") as temporary:
            root = Path(temporary)
            direct = root / "direct"
            outside = root / "outside"
            direct.mkdir()
            outside.mkdir()
            link = direct / "redirect"
            _create_disposable_junction(link, outside)
            try:
                with self.assertRaisesRegex(ValueError, "reparse"):
                    set_low_integrity_tree(direct)

                self.assertEqual(IntegrityLevel.MEDIUM, inspect_path_integrity(outside))
            finally:
                link.rmdir()

    def test_repeated_failed_process_inspection_does_not_leak_handles(self):
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetProcessHandleCount.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetProcessHandleCount.restype = wintypes.BOOL
        process = kernel32.GetCurrentProcess()
        before = ctypes.c_ulong()
        self.assertTrue(kernel32.GetProcessHandleCount(process, ctypes.byref(before)))

        for _ in range(128):
            with self.assertRaises(OSError):
                inspect_process_integrity(0x7FFFFFFF)

        after = ctypes.c_ulong()
        self.assertTrue(kernel32.GetProcessHandleCount(process, ctypes.byref(after)))
        self.assertLessEqual(after.value, before.value + 2)

    def test_process_exit_wait_rejects_open_process_access_error(self):
        import ctypes

        kernel32 = mock.Mock()
        kernel32.OpenProcess.return_value = 0
        with (
            mock.patch.object(ctypes, "WinDLL", return_value=kernel32),
            mock.patch.object(ctypes, "get_last_error", return_value=5),
        ):
            with self.assertRaises(OSError) as captured:
                self._wait_for_process_exit(0x7FFFFFFE)

        self.assertEqual(5, captured.exception.winerror)

    def test_process_exit_wait_rejects_wait_failure_and_timeout(self):
        import ctypes

        cases = (
            (0xFFFFFFFF, 6, OSError),
            (0x00000102, 0, TimeoutError),
        )
        for wait_result, last_error, error_type in cases:
            with self.subTest(wait_result=wait_result):
                kernel32 = mock.Mock()
                kernel32.OpenProcess.return_value = 123
                kernel32.WaitForSingleObject.return_value = wait_result
                with (
                    mock.patch.object(ctypes, "WinDLL", return_value=kernel32),
                    mock.patch.object(
                        ctypes, "get_last_error", return_value=last_error
                    ),
                ):
                    with self.assertRaises(error_type):
                        self._wait_for_process_exit(0x7FFFFFFE)

    def test_process_exit_wait_accepts_empirical_invalid_parameter_for_known_child(self):
        import ctypes

        kernel32 = mock.Mock()
        kernel32.OpenProcess.return_value = 0
        with (
            mock.patch.object(ctypes, "WinDLL", return_value=kernel32),
            mock.patch.object(ctypes, "get_last_error", return_value=87),
        ):
            self._wait_for_process_exit(0x7FFFFFFE)

    def _wait_for_file(self, path: Path) -> None:
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if path.is_file():
                return
            time.sleep(0.02)
        self.fail(f"low-integrity child did not create completion file: {path}")

    def _wait_for_process_exit(self, pid: int) -> None:
        import ctypes
        from ctypes import wintypes

        synchronize = 0x00100000
        error_invalid_parameter = 87
        wait_object_0 = 0x00000000
        wait_timeout = 0x00000102
        wait_failed = 0xFFFFFFFF
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            # Empirical already-gone result for our known positive child PID;
            # this is not a general documented missing-PID guarantee.
            if error == error_invalid_parameter:
                return
            raise ctypes.WinError(error)
        try:
            wait_result = kernel32.WaitForSingleObject(handle, 10_000)
            if wait_result == wait_object_0:
                return
            if wait_result == wait_failed:
                raise ctypes.WinError(ctypes.get_last_error())
            if wait_result == wait_timeout:
                raise TimeoutError(f"process {pid} did not exit within 10 seconds")
            raise OSError(
                f"WaitForSingleObject returned 0x{wait_result:08x} for pid {pid}"
            )
        finally:
            kernel32.CloseHandle(handle)

    def _handle_count(self) -> int:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetProcessHandleCount.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetProcessHandleCount.restype = wintypes.BOOL
        count = wintypes.DWORD()
        if not kernel32.GetProcessHandleCount(kernel32.GetCurrentProcess(), ctypes.byref(count)):
            raise ctypes.WinError(ctypes.get_last_error())
        return count.value


if __name__ == "__main__":
    unittest.main()
