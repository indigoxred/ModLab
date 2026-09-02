"""Exact source/deployed inventory tests for the three-file MO2 Guard bundle."""

from __future__ import annotations

import hashlib
import abc
import importlib
import sys
import tempfile
import types
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

    def test_verification_rejects_missing_changed_directories_and_bytecode(self) -> None:
        bundle = declared_guard_bundle()
        cases = ("missing", "changed", "directory", "pyc", "cache")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                for item in bundle.files:
                    (root / item.relative_path).write_bytes(item.data)
                if case == "missing":
                    (root / "plugin.py").unlink()
                elif case == "changed":
                    (root / "plugin.py").write_bytes(b"changed")
                elif case == "directory":
                    (root / "plugin.py").unlink()
                    (root / "plugin.py").mkdir()
                elif case == "pyc":
                    (root / "plugin.pyc").write_bytes(b"bytecode")
                else:
                    (root / "__pycache__").mkdir()
                with self.assertRaises(BridgeBundleError):
                    verify_guard_bundle(root, bundle.files)

    def test_plugin_loads_against_the_complete_mo2_tool_contract_only(self) -> None:
        module_name = "modlab.resources.mo2_guard.plugin"
        previous_plugin = sys.modules.pop(module_name, None)
        previous_mobase = sys.modules.get("mobase")
        previous_pyqt = sys.modules.get("PyQt5")
        previous_qtgui = sys.modules.get("PyQt5.QtGui")

        class ToolContract(abc.ABC):
            @abc.abstractmethod
            def settings(self): ...

            @abc.abstractmethod
            def displayName(self): ...

            @abc.abstractmethod
            def tooltip(self): ...

            @abc.abstractmethod
            def icon(self): ...

            @abc.abstractmethod
            def display(self): ...

        fake_mobase = types.ModuleType("mobase")
        fake_mobase.IPluginTool = ToolContract
        fake_mobase.VersionInfo = lambda *parts: parts
        fake_mobase.ReleaseType = types.SimpleNamespace(FINAL="final")
        fake_qtgui = types.ModuleType("PyQt5.QtGui")

        class QIcon:
            pass

        fake_qtgui.QIcon = QIcon
        fake_pyqt = types.ModuleType("PyQt5")
        fake_pyqt.QtGui = fake_qtgui
        sys.modules["mobase"] = fake_mobase
        sys.modules["PyQt5"] = fake_pyqt
        sys.modules["PyQt5.QtGui"] = fake_qtgui
        try:
            entry = importlib.import_module("modlab.resources.mo2_guard")
            plugin = entry.createPlugin()
            self.assertIsInstance(plugin, ToolContract)
            self.assertEqual("ModLab Guard", plugin.name())
            self.assertEqual("ModLab Guard", plugin.displayName())
            self.assertEqual([], plugin.settings())
            self.assertIsInstance(plugin.icon(), QIcon)
            self.assertIsNone(plugin.display())
        finally:
            sys.modules.pop(module_name, None)
            if previous_plugin is not None:
                sys.modules[module_name] = previous_plugin
            if previous_mobase is None:
                sys.modules.pop("mobase", None)
            else:
                sys.modules["mobase"] = previous_mobase
            if previous_pyqt is None:
                sys.modules.pop("PyQt5", None)
            else:
                sys.modules["PyQt5"] = previous_pyqt
            if previous_qtgui is None:
                sys.modules.pop("PyQt5.QtGui", None)
            else:
                sys.modules["PyQt5.QtGui"] = previous_qtgui

    def test_plugin_module_requires_mobase_outside_the_delayed_entrypoint(self) -> None:
        module_name = "modlab.resources.mo2_guard.plugin"
        previous_plugin = sys.modules.pop(module_name, None)
        previous_mobase = sys.modules.pop("mobase", None)
        try:
            with self.assertRaises(ModuleNotFoundError):
                importlib.import_module(module_name)
        finally:
            sys.modules.pop(module_name, None)
            if previous_plugin is not None:
                sys.modules[module_name] = previous_plugin
            if previous_mobase is not None:
                sys.modules["mobase"] = previous_mobase
