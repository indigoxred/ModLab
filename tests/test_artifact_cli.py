import io
import json
import tempfile
import unittest
from pathlib import Path

from modlab.cli import main


MOD_ARCHIVE_SHA256 = "e8675c0b9bdb62561503c50a19d0b948c353e0b5016a1a3299f73aded414e0a8"
ARTIFACT_ID = f"archive-sha256:{MOD_ARCHIVE_SHA256}"


class ArtifactCliTests(unittest.TestCase):
    def test_import_json_reports_retention_but_no_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = base / "workspace"
            source = base / "Example Mod.7z"
            source.write_bytes(b"mod archive")
            stdout, stderr = io.StringIO(), io.StringIO()

            code = main(
                [
                    "artifact",
                    "import",
                    str(source),
                    "--workspace",
                    str(workspace),
                    "--source-note",
                    "Personal verified archive",
                    "--format",
                    "json",
                ],
                stdout,
                stderr,
            )

            result = json.loads(stdout.getvalue())
            self.assertEqual(0, code)
            self.assertEqual(1, result["schemaVersion"])
            self.assertEqual(ARTIFACT_ID, result["artifact"]["artifactId"])
            self.assertEqual(MOD_ARCHIVE_SHA256, result["artifact"]["sha256"])
            self.assertEqual(len(b"mod archive"), result["artifact"]["size"])
            self.assertEqual(["archive-import"], result["actionsPerformed"])
            self.assertEqual([], result["installationActionsPerformed"])
            self.assertTrue(result["sourceRetained"])
            self.assertEqual(b"mod archive", source.read_bytes())
            self.assertEqual("", stderr.getvalue())

    def test_list_reports_retained_archive_without_extracting_it(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = base / "workspace"
            source = base / "not-really-a-zip.zip"
            source.write_bytes(b"mod archive")
            main(
                [
                    "artifact",
                    "import",
                    str(source),
                    "--workspace",
                    str(workspace),
                    "--source-note",
                    "local",
                ],
                io.StringIO(),
                io.StringIO(),
            )
            stdout, stderr = io.StringIO(), io.StringIO()

            code = main(
                ["artifact", "list", "--workspace", str(workspace), "--format", "json"],
                stdout,
                stderr,
            )

            result = json.loads(stdout.getvalue())
            self.assertEqual(0, code)
            self.assertEqual([ARTIFACT_ID], [item["artifactId"] for item in result["artifacts"]])
            self.assertEqual([], result["installationActionsPerformed"])
            self.assertEqual([], list(workspace.rglob("*.esp")))
            self.assertEqual("", stderr.getvalue())

    def test_verify_returns_zero_for_available_and_three_for_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = base / "workspace"
            source = base / "mod.zip"
            source.write_bytes(b"mod archive")
            main(
                [
                    "artifact",
                    "import",
                    str(source),
                    "--workspace",
                    str(workspace),
                    "--source-note",
                    "local",
                ],
                io.StringIO(),
                io.StringIO(),
            )

            available_out = io.StringIO()
            available_code = main(
                ["artifact", "verify", ARTIFACT_ID, "--workspace", str(workspace)],
                available_out,
                io.StringIO(),
            )
            stored = next((workspace / "library" / "archives").rglob("payload.*"))
            stored.unlink()
            missing_out = io.StringIO()
            missing_code = main(
                ["artifact", "verify", ARTIFACT_ID, "--workspace", str(workspace)],
                missing_out,
                io.StringIO(),
            )

            self.assertEqual(0, available_code)
            self.assertIn("Available", available_out.getvalue())
            self.assertEqual(3, missing_code)
            self.assertIn("Missing", missing_out.getvalue())

    def test_modified_archive_returns_three_without_repairing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = base / "workspace"
            source = base / "mod.rar"
            source.write_bytes(b"mod archive")
            main(
                [
                    "artifact",
                    "import",
                    str(source),
                    "--workspace",
                    str(workspace),
                    "--source-note",
                    "local",
                ],
                io.StringIO(),
                io.StringIO(),
            )
            stored = next((workspace / "library" / "archives").rglob("payload.*"))
            stored.write_bytes(b"changed archive")
            stdout = io.StringIO()

            code = main(
                ["artifact", "verify", ARTIFACT_ID, "--workspace", str(workspace)],
                stdout,
                io.StringIO(),
            )

            self.assertEqual(3, code)
            self.assertIn("Modified", stdout.getvalue())
            self.assertEqual(b"changed archive", stored.read_bytes())

    def test_unsupported_file_returns_data_error_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "bad.exe"
            source.write_bytes(b"not an archive")
            stdout, stderr = io.StringIO(), io.StringIO()

            code = main(
                [
                    "artifact",
                    "import",
                    str(source),
                    "--workspace",
                    str(base / "workspace"),
                    "--source-note",
                    "local",
                ],
                stdout,
                stderr,
            )

            self.assertEqual(2, code)
            self.assertEqual("", stdout.getvalue())
            self.assertIn("Artifact error:", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
