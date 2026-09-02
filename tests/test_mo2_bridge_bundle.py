"""Exact source/deployed inventory tests for the three-file MO2 Guard bundle."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from modlab.adapters.mo2.bridge_bundle import BridgeBundleError, declared_guard_bundle, verify_guard_bundle


class Mo2BridgeBundleTests(unittest.TestCase):
    def test_bundle_contains_only_three_declared_python_files(self) -> None:
        bundle = declared_guard_bundle()
        self.assertEqual(
            ("__init__.py", "plugin.py", "protocol.py"),
            tuple(item.relative_path for item in bundle.files),
        )
        self.assertTrue(
            all(item.sha256 == hashlib.sha256(item.data).hexdigest() for item in bundle.files)
        )

    def test_verification_rejects_an_undeclared_or_redirected_overlay_entry(self) -> None:
        bundle = declared_guard_bundle()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for item in bundle.files:
                (root / item.relative_path).write_bytes(item.data)
            self.assertEqual(bundle.files, verify_guard_bundle(root, bundle.files))
            (root / "surprise.py").write_text("pass\n", encoding="utf-8")
            with self.assertRaisesRegex(BridgeBundleError, "undeclared"):
                verify_guard_bundle(root, bundle.files)
