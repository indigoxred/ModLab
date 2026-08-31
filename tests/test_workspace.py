import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modlab.cli import main
from modlab.workspace import (
    WorkspaceError,
    default_workspace_root,
    initialize_workspace,
    workspace_layout,
)


EXPECTED_RELATIVE_DIRECTORIES = {
    "inbox",
    "library",
    "library/archives",
    "library/metadata",
    "library/quarantine",
    "games",
    "games/skyrim-se-ae",
    "games/skyrim-se-ae/environments",
    "games/skyrim-se-ae/environments/stable",
    "games/skyrim-se-ae/environments/candidate",
    "games/skyrim-se-ae/checkpoints",
    "games/skyrim-se-ae/generated",
    "games/skyrim-se-ae/logs",
    "games/skyrim-se-ae/recipes",
    "games/skyrim-se-ae/target-environments",
    "games/skyrim-se-ae/tool-installations",
    "games/skyrim-se-ae/tool-installations/mo2",
    "tools",
    "tools/mo2",
    "tools/mo2/skyrim-se-ae",
    "tools/mo2/skyrim-se-ae/app",
    "tools/mo2/skyrim-se-ae/downloads",
    "tools/mo2/skyrim-se-ae/mods",
    "tools/mo2/skyrim-se-ae/overwrite",
    "tools/mo2/skyrim-se-ae/profiles",
    "exports",
    "runtime",
    "runtime/cache",
    "runtime/jobs",
    "runtime/jobs/mo2-bootstrap",
    "runtime/jobs/mo2-bootstrap/plans",
    "runtime/transactions",
}


class WorkspaceTests(unittest.TestCase):
    def test_pure_workspace_layout_creates_nothing(self):
        # Catches read-only observation accidentally initializing a workspace.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory, "absent-workspace")

            layout = workspace_layout(root)

            self.assertEqual(root.resolve(), layout.root)
            self.assertFalse(root.exists())

    def test_initialize_creates_the_documented_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory, "workspace")

            layout = initialize_workspace(root)

            actual = {
                path.relative_to(root).as_posix()
                for path in root.rglob("*")
                if path.is_dir()
            }
            self.assertEqual(EXPECTED_RELATIVE_DIRECTORIES, actual)
            self.assertEqual(root.resolve() / "inbox", layout.inbox)
            self.assertEqual(
                root.resolve() / "tools" / "mo2" / "skyrim-se-ae" / "app",
                layout.skyrim_mo2_app,
            )
            self.assertEqual(
                root.resolve() / "games" / "skyrim-se-ae" / "environment.json",
                layout.skyrim_environment_configuration,
            )

    def test_bootstrap_paths_are_organized_under_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory, "workspace")

            layout = initialize_workspace(workspace)

            self.assertEqual(
                workspace.resolve() / "runtime" / "jobs" / "mo2-bootstrap",
                layout.mo2_bootstrap_jobs,
            )
            self.assertEqual(
                workspace.resolve()
                / "runtime"
                / "jobs"
                / "mo2-bootstrap"
                / "plans",
                layout.mo2_bootstrap_plans,
            )
            self.assertEqual(
                workspace.resolve()
                / "games"
                / "skyrim-se-ae"
                / "tool-installations"
                / "mo2",
                layout.mo2_bootstrap_receipts,
            )

    def test_reinitializing_never_removes_an_unknown_user_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory, "workspace")
            initialize_workspace(root)
            archive = root / "inbox" / "my-old-mod.7z"
            archive.write_bytes(b"user-owned archive")

            initialize_workspace(root)

            self.assertEqual(b"user-owned archive", archive.read_bytes())

    def test_initialize_rejects_redirected_ancestor_before_creating_children(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory, "workspace")
            runtime = root / "runtime"
            runtime.mkdir(parents=True)
            real_is_symlink = Path.is_symlink

            def report_redirect(path):
                return Path(path) == runtime or real_is_symlink(path)

            with patch.object(
                Path,
                "is_symlink",
                autospec=True,
                side_effect=report_redirect,
            ):
                with self.assertRaisesRegex(WorkspaceError, "redirected"):
                    initialize_workspace(root)

            self.assertEqual([], list(runtime.iterdir()))
            self.assertFalse((root / "inbox").exists())

    def test_initialize_rejects_non_directory_ancestor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory, "workspace")
            root.mkdir()
            (root / "runtime").write_bytes(b"not a directory")

            with self.assertRaisesRegex(WorkspaceError, "not a directory"):
                initialize_workspace(root)

            self.assertFalse((root / "runtime" / "jobs").exists())

    def test_layout_keeps_recovery_state_separate_from_mod_archives(self):
        with tempfile.TemporaryDirectory() as directory:
            layout = initialize_workspace(Path(directory, "workspace"))

            self.assertNotEqual(layout.archives.parent, layout.transactions.parent)
            self.assertEqual("library", layout.archives.parent.name)
            self.assertEqual("runtime", layout.transactions.parent.name)

    def test_cli_initializes_an_explicit_root_and_reports_key_locations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory, "my-modlab-data")
            stdout, stderr = io.StringIO(), io.StringIO()

            code = main(
                ["workspace", "init", "--root", str(root)],
                stdout,
                stderr,
            )

            self.assertEqual(0, code)
            self.assertTrue((root / "inbox").is_dir())
            self.assertIn(str(root.resolve()), stdout.getvalue())
            self.assertIn("Inbox:", stdout.getvalue())
            self.assertEqual("", stderr.getvalue())

    def test_cli_reports_redirected_workspace_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory, "workspace")
            runtime = root / "runtime"
            runtime.mkdir(parents=True)
            stdout, stderr = io.StringIO(), io.StringIO()
            real_is_symlink = Path.is_symlink

            def report_redirect(path):
                return Path(path) == runtime or real_is_symlink(path)

            with patch.object(
                Path,
                "is_symlink",
                autospec=True,
                side_effect=report_redirect,
            ):
                code = main(
                    ["workspace", "init", "--root", str(root)],
                    stdout,
                    stderr,
                )

            self.assertEqual(3, code)
            self.assertEqual("", stdout.getvalue())
            self.assertIn("Workspace error:", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())

    def test_default_workspace_is_inside_the_modlab_application_root(self):
        application_root = Path(__file__).resolve().parents[1]

        self.assertEqual(application_root / "workspace", default_workspace_root())


if __name__ == "__main__":
    unittest.main()
