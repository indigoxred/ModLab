import hashlib
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
import unittest
from unittest import mock

from modlab.validation.mo2_containment_model import IntegrityObservation
from modlab.validation import windows_integrity
from modlab.validation.windows_integrity import (
    IntegrityLevel,
    inspect_path_integrity,
    inspect_process_integrity,
    launch_low_integrity_process,
    set_low_integrity_tree,
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
    def test_child_is_low_before_its_first_instruction_runs(self):
        with tempfile.TemporaryDirectory(prefix="modlab-integrity-order-") as temporary:
            root = Path(temporary)
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
                launch = launch_low_integrity_process(
                    PYTHON,
                    ("-B", "-c", code, str(marker)),
                    root,
                    dict(os.environ),
                )

            self._wait_for_file(marker)
            self.assertEqual([launch.pid], observed_barrier)
            self.assertEqual(b"ran", marker.read_bytes())

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

    def _wait_for_file(self, path: Path) -> None:
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if path.is_file():
                return
            time.sleep(0.02)
        self.fail(f"low-integrity child did not create completion file: {path}")

    def _wait_for_process_exit(self, pid: int) -> None:
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            try:
                inspect_process_integrity(pid)
            except OSError:
                return
            time.sleep(0.02)
        self.fail(f"suspended child was not terminated: {pid}")

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
