import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from modlab.adapters.mo2.archive import (
    Mo2ArchiveError,
    compare_package_to_existing,
    extract_and_inventory,
    inventory_tree,
    preflight_archive,
)
from tests.support.mo2_bootstrap import (
    FakeRunner,
    descriptor_for,
    descriptor_for_package,
    existing_app_fixture,
    extraction_package_files,
    package_inventory_fixture,
    unsafe_listing_cases,
    valid_names,
    valid_types,
)


class Mo2ArchiveTests(unittest.TestCase):
    def test_subprocess_runner_observes_creation_and_cleans_post_spawn_failures(self):
        from modlab.adapters.mo2 import archive as archive_module

        class Process:
            def __init__(self, *, returncode=0, communication_error=None):
                self.returncode = returncode
                self.communication_error = communication_error
                self.killed = False
                self.waited = False

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def communicate(self):
                if self.communication_error is not None:
                    raise self.communication_error
                return b"stdout", b"stderr"

            def kill(self):
                self.killed = True

            def wait(self):
                self.waited = True

        success = Process()
        nonzero = Process(returncode=9)
        failed_callback = Process()
        failed_communication = Process(
            communication_error=RuntimeError("communication failed")
        )
        created = []
        runner = archive_module.SubprocessCommandRunner()

        def callback_failure():
            created.append("callback")
            raise RuntimeError("creation callback failed")

        with patch.object(
            archive_module.subprocess,
            "Popen",
            side_effect=(
                success,
                nonzero,
                failed_callback,
                failed_communication,
                OSError("spawn failed"),
            ),
        ):
            self.assertEqual(
                0,
                runner.run(("tool", "success"), on_created=lambda: created.append("success")).returncode,
            )
            self.assertEqual(
                9,
                runner.run(("tool", "nonzero"), on_created=lambda: created.append("nonzero")).returncode,
            )
            with self.assertRaisesRegex(RuntimeError, "creation callback"):
                runner.run(("tool", "callback"), on_created=callback_failure)
            with self.assertRaisesRegex(RuntimeError, "communication"):
                runner.run(
                    ("tool", "communication"),
                    on_created=lambda: created.append("communication"),
                )
            with self.assertRaisesRegex(OSError, "spawn"):
                runner.run(("tool", "spawn"), on_created=lambda: created.append("spawn"))

        self.assertEqual(["success", "nonzero", "callback", "communication"], created)
        self.assertTrue(failed_callback.killed)
        self.assertTrue(failed_callback.waited)
        self.assertTrue(failed_communication.killed)
        self.assertTrue(failed_communication.waited)

    def test_spawn_boundary_programs_render_truthfully_on_success_and_failure(self):
        from modlab.workflows.skyrim.mo2_bootstrap import (
            Mo2BootstrapRefusal,
            _bootstrap_failure_result,
        )
        from modlab.workflows.skyrim.mo2_bootstrap_rendering import (
            refusal_result_to_dict,
            refusal_result_to_text,
        )
        programs = (
            r"C:\Windows\System32\tar.exe [version]",
            r"C:\Windows\System32\tar.exe [list-names]",
            r"C:\Windows\System32\tar.exe [list-names]",
        )
        refusal = Mo2BootstrapRefusal("extractor-failed", "post-spawn failure")
        failure = _bootstrap_failure_result(
            refusal,
            outcome="Blocked",
            plan_id=None,
            actions_complete=True,
            programs_launched=programs,
        )
        rendered = refusal_result_to_dict(refusal.code, str(refusal), failure)
        self.assertEqual(list(programs), rendered["programsLaunched"])
        text = refusal_result_to_text(refusal.code, str(refusal), failure)
        self.assertEqual(2, text.count("tar.exe [list-names]"))

    def test_recording_runner_records_only_created_processes_and_preserves_order(self):
        from modlab.workflows.skyrim.mo2_bootstrap import _RecordingCommandRunner

        class ObservedRunner:
            def __init__(self, outcomes):
                self.outcomes = iter(outcomes)

            def run(self, args, *, on_created):
                outcome = next(self.outcomes)
                if outcome == "spawn-failure":
                    raise OSError("CreateProcessW failed")
                on_created()
                if outcome == "communication-failure":
                    raise RuntimeError("communicate failed after creation")
                return CompletedProcess(args, 7 if outcome == "nonzero" else 0, b"", b"")

        runner = _RecordingCommandRunner(
            ObservedRunner(("success", "nonzero", "communication-failure", "spawn-failure"))
        )
        version = (r"C:\Windows\System32\tar.exe", "--version")
        names = (r"C:\Windows\System32\tar.exe", "-tf", "payload.7z")
        types = (r"C:\Windows\System32\tar.exe", "-tvf", "payload.7z")
        extract = (r"C:\Windows\System32\tar.exe", "-xf", "payload.7z", "-C", "stage")

        self.assertEqual(0, runner.run(version).returncode)
        self.assertEqual(7, runner.run(names).returncode)
        with self.assertRaisesRegex(RuntimeError, "communicate"):
            runner.run(types)
        with self.assertRaisesRegex(OSError, "CreateProcessW"):
            runner.run(extract)

        self.assertEqual(
            (
                r"C:\Windows\System32\tar.exe [version]",
                r"C:\Windows\System32\tar.exe [list-names]",
                r"C:\Windows\System32\tar.exe [list-types]",
            ),
            runner.programs_launched(),
        )

    def test_recording_runner_keeps_successful_repeated_launches(self):
        from modlab.workflows.skyrim.mo2_bootstrap import _RecordingCommandRunner

        class RepeatedRunner:
            def run(self, args, *, on_created):
                on_created()
                return CompletedProcess(args, 0, b"", b"")

        runner = _RecordingCommandRunner(RepeatedRunner())
        args = (r"C:\Windows\System32\tar.exe", "-tf", "payload.7z")
        runner.run(args)
        runner.run(args)
        self.assertEqual(
            (
                r"C:\Windows\System32\tar.exe [list-names]",
                r"C:\Windows\System32\tar.exe [list-names]",
            ),
            runner.programs_launched(),
        )

    def test_preflight_uses_argument_arrays_and_accepts_exact_listing(self):
        names = valid_names()
        runner = FakeRunner.for_listing(names, valid_types())

        result = preflight_archive(
            Path("payload.7z"),
            descriptor_for(names),
            Path(r"C:\Windows\System32\tar.exe"),
            runner=runner,
        )

        self.assertEqual(1780, result.entry_count)
        self.assertEqual(
            [
                [r"C:\Windows\System32\tar.exe", "-tf", "payload.7z"],
                [r"C:\Windows\System32\tar.exe", "-tvf", "payload.7z"],
            ],
            runner.calls,
        )

    def test_preflight_rejects_traversal_case_collision_and_links(self):
        for names, types, reason in unsafe_listing_cases():
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(Mo2ArchiveError, reason):
                    preflight_archive(
                        Path("payload.7z"),
                        descriptor_for(names),
                        Path("tar.exe"),
                        runner=FakeRunner.for_listing(names, types),
                    )

    def test_existing_comparison_requires_every_package_file(self):
        comparison = compare_package_to_existing(
            package_inventory_fixture(),
            existing_app_fixture(missing="usvfs_x64.dll"),
            descriptor_for(valid_names()),
        )

        self.assertFalse(comparison.compatible)
        self.assertEqual(("usvfs_x64.dll",), comparison.missing)

    def test_existing_comparison_records_only_explicitly_allowed_extras(self):
        allowed = existing_app_fixture(
            extras={
                "categories.dat": b"categories",
                "nxmhandler.ini": b"[General]\nnoregister=false\n",
                "logs/session.log": b"log",
                "plugins/tool/__pycache__/module.pyc": b"bytecode",
            }
        )
        accepted = compare_package_to_existing(
            package_inventory_fixture(), allowed, descriptor_for(valid_names())
        )
        refused = compare_package_to_existing(
            package_inventory_fixture(),
            existing_app_fixture(extras={"unknown.dll": b"unknown"}),
            descriptor_for(valid_names()),
        )

        self.assertTrue(accepted.compatible)
        self.assertEqual(
            (
                "categories.dat",
                "logs/session.log",
                "nxmhandler.ini",
                "plugins/tool/__pycache__/module.pyc",
            ),
            accepted.allowed_extras,
        )
        self.assertFalse(refused.compatible)
        self.assertEqual(("unknown.dll",), refused.unapproved_extras)

    def test_inventory_tree_hashes_exact_sorted_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "b.txt").write_bytes(b"bravo")
            (root / "Folder").mkdir()
            (root / "Folder" / "a.txt").write_bytes(b"alpha")

            result = inventory_tree(root)

        expected_rows = [
            ["b.txt", hashlib.sha256(b"bravo").hexdigest(), 5],
            ["Folder/a.txt", hashlib.sha256(b"alpha").hexdigest(), 5],
        ]
        expected_rows.sort(key=lambda row: (row[0].casefold(), row[0]))
        expected = hashlib.sha256(
            json.dumps(expected_rows, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self.assertEqual(expected, result.sha256)
        self.assertEqual(10, result.total_size)

    def test_extract_checks_space_then_uses_exact_confined_arguments(self):
        files = extraction_package_files()
        descriptor = descriptor_for_package(files)
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            stage = parent / "fresh-stage"
            runner = FakeRunner.for_extraction(files)

            result = extract_and_inventory(
                Path("payload.7z"),
                descriptor,
                Path(r"C:\Windows\System32\tar.exe"),
                stage,
                runner=runner,
                free_space_reader=lambda _: 2048,
            )

            self.assertEqual(len(files), result.file_count)
            self.assertEqual(
                [[r"C:\Windows\System32\tar.exe", "-tf", "payload.7z"],
                 [r"C:\Windows\System32\tar.exe", "-tvf", "payload.7z"],
                 [r"C:\Windows\System32\tar.exe", "-xf", "payload.7z", "-C", str(stage)]],
                runner.calls,
            )

    def test_low_space_refuses_before_stage_or_extractor(self):
        files = extraction_package_files()
        descriptor = descriptor_for_package(files)
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory) / "fresh-stage"
            runner = FakeRunner.for_extraction(files)

            with self.assertRaisesRegex(Mo2ArchiveError, "free space"):
                extract_and_inventory(
                    Path("payload.7z"),
                    descriptor,
                    Path(r"C:\Windows\System32\tar.exe"),
                    stage,
                    runner=runner,
                    free_space_reader=lambda _: 1023,
                )

            self.assertFalse(stage.exists())
            self.assertEqual([], runner.calls)


if __name__ == "__main__":
    unittest.main()
