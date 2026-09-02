"""Fail-closed verification of a bootstrap package with one Guard overlay."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Protocol

from modlab.adapters.mo2.archive import Mo2ArchiveError, PackageFile, PackageInventory, inventory_tree
from modlab.adapters.mo2.bridge_bundle import BridgeBundleError, verify_guard_bundle
from modlab.resources.mo2_guard.protocol import BridgeReceipt, bridge_receipt_to_bytes


class BootstrapInventoryError(RuntimeError):
    """Raised when bootstrap package identity is not provably preserved."""


class _BootstrapInventoryReceipt(Protocol):
    final_root: str
    package_inventory_sha256: str
    package_file_count: int
    package_size: int


_OVERLAY_PARTS = ("plugins", "modlab_guard")


def verify_bootstrap_inventory_with_bridge_overlay(
    app_root: Path,
    bootstrap_receipt: _BootstrapInventoryReceipt,
    bridge_receipt: BridgeReceipt | None,
) -> PackageInventory:
    """Compare managed MO2 bytes while excluding only an exact receipt-backed overlay."""
    root = Path(app_root)
    _require_bootstrap_receipt(root, bootstrap_receipt)
    overlay = root.joinpath(*_OVERLAY_PARTS)
    exists = _direct_entry_exists(overlay)
    if not exists:
        if bridge_receipt is not None:
            raise BootstrapInventoryError("receipted bridge overlay is absent")
        return _verify_baseline(root, bootstrap_receipt)
    if bridge_receipt is None:
        raise BootstrapInventoryError("bridge overlay is unreceipted")
    _verify_receipted_overlay(root, overlay, bootstrap_receipt, bridge_receipt)
    inventory = _inventory_without_overlay(root)
    _require_baseline_identity(inventory, bootstrap_receipt)
    return inventory


def _require_bootstrap_receipt(root: Path, receipt: _BootstrapInventoryReceipt) -> None:
    required = ("receipt_id", "final_root", "package_inventory_sha256", "package_file_count", "package_size")
    if any(not hasattr(receipt, name) for name in required):
        if not hasattr(receipt, "receipt_id"):
            raise BootstrapInventoryError("bootstrap receipt identity is missing")
        raise BootstrapInventoryError("bootstrap receipt lacks package inventory identity")
    if str(Path(receipt.final_root)).casefold() != str(root).casefold():
        raise BootstrapInventoryError("bootstrap receipt is not bound to this app root")
    if not isinstance(receipt.receipt_id, str) or not receipt.receipt_id:
        raise BootstrapInventoryError("bootstrap receipt identity is invalid")
    _require_direct_chain(root, "app root")


def _verify_receipted_overlay(root: Path, overlay: Path, bootstrap_receipt: _BootstrapInventoryReceipt, receipt: BridgeReceipt) -> None:
    try:
        bridge_receipt_to_bytes(receipt)
    except Exception as error:
        raise BootstrapInventoryError(f"bridge receipt is not strict: {error}") from error
    if receipt.target_root.casefold() != str(overlay).casefold():
        raise BootstrapInventoryError("bridge receipt target root is not the exact overlay")
    expected_bootstrap_id = bootstrap_receipt.receipt_id
    if receipt.bootstrap_receipt_id != expected_bootstrap_id:
        raise BootstrapInventoryError("bridge receipt is not bound to the bootstrap receipt")
    try:
        verify_guard_bundle(overlay, receipt.files)
    except BridgeBundleError as error:
        raise BootstrapInventoryError(str(error)) from error


def _verify_baseline(root: Path, receipt: _BootstrapInventoryReceipt) -> PackageInventory:
    try:
        inventory = inventory_tree(root)
    except Mo2ArchiveError as error:
        raise BootstrapInventoryError(str(error)) from error
    _require_baseline_identity(inventory, receipt)
    return inventory


def _inventory_without_overlay(root: Path) -> PackageInventory:
    try:
        inventory = inventory_tree(root)
    except Mo2ArchiveError as error:
        raise BootstrapInventoryError(str(error)) from error
    prefix = "plugins/modlab_guard/"
    kept = tuple(item for item in inventory.files if not item.relative_path.casefold().startswith(prefix))
    if len(kept) + 3 != len(inventory.files):
        raise BootstrapInventoryError("bridge overlay does not contain exactly three inventory files")
    canonical = json.dumps(
        [[item.relative_path, item.sha256, item.size] for item in kept],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return PackageInventory(
        files=kept,
        total_size=sum(item.size for item in kept),
        sha256=hashlib.sha256(canonical).hexdigest(),
    )


def _require_baseline_identity(inventory: PackageInventory, receipt: _BootstrapInventoryReceipt) -> None:
    if (
        inventory.sha256 != receipt.package_inventory_sha256
        or inventory.file_count != receipt.package_file_count
        or inventory.total_size != receipt.package_size
    ):
        raise BootstrapInventoryError("managed bootstrap inventory differs from its receipt")


def _direct_entry_exists(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as error:
        raise BootstrapInventoryError(f"cannot inspect bridge overlay: {error}") from error
    _require_direct_chain(path.parent, "bridge overlay parent")
    if path.is_symlink() or bool(getattr(metadata, "st_file_attributes", 0) & 0x400):
        raise BootstrapInventoryError("bridge overlay is redirected")
    if not path.is_dir():
        raise BootstrapInventoryError("bridge overlay is not a directory")
    return True


def _require_direct_chain(path: Path, label: str) -> None:
    current = Path(path).absolute()
    while True:
        try:
            metadata = current.lstat()
        except OSError as error:
            raise BootstrapInventoryError(f"cannot inspect {label}: {error}") from error
        if current.is_symlink() or bool(getattr(metadata, "st_file_attributes", 0) & 0x400):
            raise BootstrapInventoryError(f"{label} is redirected")
        parent = current.parent
        if parent == current:
            return
        current = parent


__all__ = ["BootstrapInventoryError", "verify_bootstrap_inventory_with_bridge_overlay"]
