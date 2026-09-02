"""Bootstrap package identity remains exact with one strict Guard overlay."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from modlab.adapters.mo2.archive import inventory_tree
from modlab.adapters.mo2.bootstrap_inventory import (
    BootstrapInventoryError,
    verify_bootstrap_inventory_with_bridge_overlay,
)
from modlab.adapters.mo2.bridge_bundle import declared_guard_bundle
from modlab.resources.mo2_guard.protocol import BridgeReceipt, bridge_receipt_id_for


class Mo2BootstrapInventoryTests(unittest.TestCase):
    def test_bootstrap_inventory_accepts_only_the_exact_receipted_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app_root = Path(temporary)
            (app_root / "ModOrganizer.exe").write_bytes(b"managed")
            bootstrap_inventory = inventory_tree(app_root)
            bootstrap_receipt = SimpleNamespace(
                final_root=str(app_root),
                package_inventory_sha256=bootstrap_inventory.sha256,
                package_file_count=bootstrap_inventory.file_count,
                package_size=bootstrap_inventory.total_size,
            )
            bundle = declared_guard_bundle()
            overlay = app_root / "plugins" / "modlab_guard"
            overlay.mkdir(parents=True)
            for item in bundle.files:
                (overlay / item.relative_path).write_bytes(item.data)
            receipt = BridgeReceipt(
                schema_version=1,
                receipt_id="",
                qualification="Verified",
                job_id="bridge-job:" + "1" * 32,
                bootstrap_receipt_id="bootstrap-receipt-sha256:" + "2" * 64,
                target_root=str(overlay),
                bundle_version="mo2-guard-v1",
                files=tuple(item.metadata for item in bundle.files),
                verified_at="2026-09-02T00:00:00Z",
            )
            receipt = receipt.__class__(**{**receipt.__dict__, "receipt_id": bridge_receipt_id_for(receipt)})
            self.assertEqual(
                bootstrap_inventory.sha256,
                verify_bootstrap_inventory_with_bridge_overlay(
                    app_root, bootstrap_receipt, receipt
                ).sha256,
            )
            (overlay / "extra.py").write_text("pass\n", encoding="utf-8")
            with self.assertRaisesRegex(BootstrapInventoryError, "undeclared"):
                verify_bootstrap_inventory_with_bridge_overlay(
                    app_root, bootstrap_receipt, receipt
                )
