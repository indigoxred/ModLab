"""Fail-closed runtime capability evidence; synthetic fixtures are not live proof."""

from __future__ import annotations

import abc
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

try:
    from modlab.adapters.mo2 import bridge_runtime_capability as cap
except ImportError:
    cap = None


def entry(path, kind="file", data=b"fixture", file_id=7):
    return {"path": path, "kind": kind, "sha256": hashlib.sha256(data).hexdigest() if kind == "file" else None,
            "size": len(data) if kind == "file" else 0, "volume": 1, "fileId": file_id}


def fixture(parent=None):
    root = str((Path(parent) if parent else Path(tempfile.gettempdir()) / "capability-fixture") / ("a" * 32))
    runtime = [entry("ModOrganizer.exe"), entry("plugins/plugin_python/dlls/python312.dll"),
               entry("plugins/plugin_python/libs/mobase.cp312-win_amd64.pyd")]
    runtime[0].update(sha256="442b354a8f34754da0048654c44d27f51628feba54ce46c3187cf58d6c43e622", size=5028352)
    candidates = []
    for layout in ("SingleFile", "Package"):
        app = str(Path(root) / layout / "app")
        baseline = [entry("bundled.py")]
        declared = ([entry("modlab_capability_probe.py", file_id=8)] if layout == "SingleFile" else
                    [entry("modlab_capability_probe", "directory", file_id=8),
                     entry("modlab_capability_probe/__init__.py", file_id=9),
                     entry("modlab_capability_probe/plugin.py", file_id=10)])
        full = baseline + declared
        observations = []
        for index, phase in enumerate(("Control", "First", "Second", "Passive", "Guarded")):
            nonce = str(index + 1) * 32
            job = str(Path(root) / layout / "jobs" / phase)
            loaded = None if phase == "Control" else {
                "runId": "a" * 32, "layout": layout, "phase": phase, "nonce": nonce, "pid": 123,
                "pythonVersion": "3.12.8 (fixture)", "implementation": "cpython", "cacheTag": "cpython-312",
                "bytecodeDisabled": False, "executable": str(Path(app) / "ModOrganizer.exe"),
                "mobasePath": str(Path(app) / "plugins/plugin_python/libs/mobase.cp312-win_amd64.pyd")}
            observations.append({
                "phase": phase, "sequence": index, "job": job, "nonce": nonce, "pid": 123,
                "before": copy.deepcopy(baseline if index == 0 else full),
                "after": copy.deepcopy(baseline if index == 0 else full),
                "ownBefore": [] if index == 0 else copy.deepcopy(declared),
                "ownAfter": [] if index == 0 else copy.deepcopy(declared),
                "runtimeBefore": copy.deepcopy(runtime), "runtimeAfter": copy.deepcopy(runtime),
                "exitCode": 0, "normalClose": True, "loaded": loaded,
                "guarded": copy.deepcopy(loaded) if phase == "Guarded" else None,
                "ui": {"windowId": 42, "app": app + "\\ModOrganizer.exe", "title": "Mod Organizer v2.5.2",
                       "loadedTool": None if index == 0 else "ModLab Capability Probe",
                       "closeAction": "Alt+F4", "screenshotIds": ["fixture-observation"]},
                "logs": [{"path": str(Path(job) / "observed.log"), "sha256": "f" * 64, "size": 10}],
            })
        candidates.append({"layout": layout, "appRoot": app, "declared": declared,
                           "control": observations[0], "observations": observations[1:]})
    return {"schemaVersion": 1, "runId": "a" * 32, "root": root,
            "source": {"commit": "b" * 40, "tree": "c" * 40},
            "archive": {"sha256": "e6376efd87fd5ddd95aee959405e8f067afa526ea6c2c0c5aa03c5108bf4a815", "size": 149660212},
            "candidates": candidates}


class CapabilitySelectionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(cap, "runtime capability implementation is missing")

    def test_single_file_wins_ties_and_package_wins_only_alone(self):
        for single, package, expected in ((True, True, "SingleFile"), (True, False, "SingleFile"),
                                          (False, True, "Package"), (False, False, "NotSupported")):
            value = fixture()
            for candidate, passing in zip(value["candidates"], (single, package)):
                if not passing:
                    candidate["observations"][0]["after"].append(entry("surprise.py"))
            with self.subTest(expected=expected, single=single, package=package):
                result = cap.select_capability(value)
                self.assertEqual(expected, result["selection"])
                self.assertEqual(result, cap.capability_from_bytes(cap.capability_to_bytes(result)))

    def test_each_phase_requires_exact_full_tree_and_own_inventory(self):
        for phase in range(4):
            for field in ("before", "after", "ownBefore", "ownAfter"):
                value = fixture()
                observation = value["candidates"][0]["observations"][phase]
                observation[field][0]["sha256"] = "e" * 64
                with self.subTest(phase=phase, field=field):
                    if field.startswith("own"):
                        with self.assertRaises(cap.CapabilityError):
                            cap.select_capability(value)
                    else:
                        self.assertEqual("Package", cap.select_capability(value)["selection"])

    def test_unchanged_unloaded_abnormal_or_guardless_candidate_cannot_pass(self):
        for change in ("loaded", "ui", "exitCode", "normalClose", "guarded"):
            value = fixture()
            obs = value["candidates"][0]["observations"][3]
            if change == "ui":
                obs["ui"]["loadedTool"] = None
            elif change == "exitCode":
                obs[change] = 1
            elif change == "normalClose":
                obs[change] = False
            else:
                obs[change] = None
            with self.subTest(change=change):
                self.assertEqual("Package", cap.select_capability(value)["selection"])

    def test_control_must_be_candidate_absent_and_normally_closed(self):
        for change in ("present", "close", "identity"):
            value = fixture()
            control = value["candidates"][0]["control"]
            if change == "present":
                control["after"].append(entry("__pycache__", "directory"))
                control["after"].append(entry("__pycache__/modlab_capability_probe.cpython-312.pyc"))
                control["ownAfter"] = control["after"][-1:]
            elif change == "close":
                control["normalClose"] = False
            else:
                control["runtimeAfter"][1]["sha256"] = "0" * 64
            with self.subTest(change=change):
                self.assertEqual("Package", cap.select_capability(value)["selection"])

    def test_rejects_missing_duplicate_or_reordered_launches(self):
        for mode in ("missing", "duplicate", "reordered"):
            value = fixture()
            observations = value["candidates"][0]["observations"]
            if mode == "missing":
                observations.pop()
            elif mode == "duplicate":
                observations[1] = copy.deepcopy(observations[0])
            else:
                observations.reverse()
            with self.subTest(mode=mode), self.assertRaises(cap.CapabilityError):
                cap.select_capability(value)

    def test_rejects_unknown_fields_wrong_types_and_bindings(self):
        mutations = [lambda v: v.update(extra=True), lambda v: v.update(schemaVersion=True),
                     lambda v: v["archive"].update(size=True),
                     lambda v: v["candidates"][0]["observations"][0].update(pid=True),
                     lambda v: v["candidates"][0]["observations"][0]["loaded"].update(runId="d" * 32),
                     lambda v: v["candidates"][0]["observations"][0]["loaded"].update(mobasePath="C:/wrong.pyd"),
                     lambda v: v["candidates"][0]["observations"][0].update(job=v["root"]),
                     lambda v: v["candidates"][0]["declared"][0].update(size=True)]
        for mutate in mutations:
            value = fixture()
            mutate(value)
            with self.subTest(mutation=mutate), self.assertRaises(cap.CapabilityError):
                cap.select_capability(value)

    def test_rejects_unsafe_duplicate_redirected_inventory(self):
        for path in ("../outside", "C:/outside", "a\\b", "nul", "a.", "a:stream", "a//b"):
            value = fixture()
            value["candidates"][0]["observations"][0]["after"].append(entry(path))
            with self.subTest(path=path), self.assertRaises(cap.CapabilityError):
                cap.select_capability(value)
        for kind in ("symlink", "junction", "reparse"):
            value = fixture()
            value["candidates"][0]["observations"][0]["after"][0]["kind"] = kind
            with self.subTest(kind=kind), self.assertRaises(cap.CapabilityError):
                cap.select_capability(value)

    def test_serialization_rejects_fabricated_selection_duplicate_keys_and_noncanonical_bytes(self):
        result = cap.select_capability(fixture())
        result["selection"] = "Package"
        with self.assertRaises(cap.CapabilityError):
            cap.capability_to_bytes(result)
        for data in (b'{"schemaVersion":1,"schemaVersion":1}', b"{}", b"NaN"):
            with self.assertRaises(cap.CapabilityError):
                cap.capability_from_bytes(data)
        good = cap.capability_to_bytes(cap.select_capability(fixture()))
        with self.assertRaises(cap.CapabilityError):
            cap.capability_from_bytes(b" " + good)


@unittest.skipUnless(os.name == "nt", "requires native retained-handle Windows filesystem")
class CapabilityFilesystemTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(cap, "runtime capability implementation is missing")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_inventory_detects_content_identity_empty_directory_and_cache_drift(self):
        (self.root / "probe.py").write_bytes(b"pass\n")
        before = cap.snapshot_tree(self.root)
        self.assertEqual(["probe.py"], [item["path"] for item in before])
        (self.root / "__pycache__").mkdir()
        after = cap.snapshot_tree(self.root)
        self.assertNotEqual(before, after)
        self.assertEqual("directory", after[0]["kind"])
        (self.root / "probe.py").write_bytes(b"fail\n")
        self.assertNotEqual(before[-1]["sha256"], cap.snapshot_tree(self.root)[-1]["sha256"])

    def test_read_only_load_does_not_create_missing_root(self):
        missing = self.root / "absent" / "selection.json"
        with self.assertRaises((cap.CapabilityError, OSError)):
            cap.load_capability(missing)
        self.assertFalse(missing.parent.exists())

    def test_no_replace_publication_and_tamper_rejection(self):
        matrix = fixture(self.root)
        for candidate in matrix["candidates"]:
            for observation in [candidate["control"], *candidate["observations"]]:
                log = observation["logs"][0]
                path = Path(log["path"])
                path.parent.mkdir(parents=True)
                data = b"control log\n" if observation["loaded"] is None else b"MODLAB_CAPABILITY_V1 " + json.dumps(observation["loaded"], separators=(",", ":")).encode() + b"\n"
                path.write_bytes(data)
                log.update(sha256=hashlib.sha256(data).hexdigest(), size=len(data))
                if observation["guarded"] is not None:
                    (path.parent / "guarded.json").write_text(json.dumps(observation["guarded"]), encoding="utf-8")
        value = cap.select_capability(matrix)
        target = Path(value["root"]) / "selection.json"
        cap.publish_capability(target, value)
        self.assertEqual(value, cap.load_capability(target))
        with self.assertRaises(Exception):
            cap.publish_capability(target, value)
        self.assertEqual(value, cap.load_capability(target))
        target.write_bytes(b"{}\n")
        with self.assertRaises(cap.CapabilityError):
            cap.load_capability(target)

    def test_publication_refuses_fabricated_support_with_missing_raw_evidence(self):
        value = cap.select_capability(fixture(self.root))
        target = self.root / "selection.json"
        with self.assertRaises((cap.CapabilityError, OSError)):
            cap.publish_capability(target, value)
        self.assertFalse(target.exists())

    def test_ancestor_identity_substitution_is_rejected(self):
        (self.root / "probe.py").write_bytes(b"pass\n")
        original = Path.lstat
        calls = 0
        def substitute(path):
            nonlocal calls
            result = original(path)
            if path == self.root.parent:
                calls += 1
                if calls > 1:
                    values = {name: getattr(result, name) for name in dir(result) if name.startswith("st_")}
                    values["st_ino"] += 1
                    return types.SimpleNamespace(**values)
            return result
        with patch.object(Path, "lstat", substitute), self.assertRaises(cap.CapabilityError):
            cap.snapshot_tree(self.root)

    def test_snapshot_rejects_membership_added_after_initial_enumeration(self):
        (self.root / "first.py").write_bytes(b"first")
        original = Path.iterdir
        raced = False
        def add_after_enumeration(path):
            nonlocal raced
            entries = list(original(path))
            if path == self.root and not raced:
                raced = True
                (path / "late.py").write_bytes(b"late")
            return iter(entries)
        with patch.object(Path, "iterdir", add_after_enumeration), self.assertRaises(cap.CapabilityError):
            cap.snapshot_tree(self.root)
        self.assertTrue(raced)

    def test_snapshot_keeps_earlier_file_pinned_until_all_content_is_read(self):
        earlier = self.root / "a.py"
        earlier.write_bytes(b"before")
        (self.root / "z.py").write_bytes(b"last")
        original = cap.read_pinned_file
        attempted = False
        blocked = False
        def change_earlier(pinned):
            nonlocal attempted, blocked
            if pinned.path.name == "z.py":
                attempted = True
                try:
                    earlier.write_bytes(b"after")
                except OSError:
                    blocked = True
            return original(pinned)
        with patch.object(cap, "read_pinned_file", change_earlier):
            cap.snapshot_tree(self.root)
        self.assertTrue(attempted)
        self.assertTrue(blocked, "earlier content became mutable before snapshot completion")


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(cap, "runtime capability implementation is missing")

    def test_candidate_is_complete_passive_and_only_guarded_init_writes_job_output(self):
        self.assertTrue(hasattr(cap, "candidate_sources"), "candidate generator is missing")
        names = ("init", "name", "localizedName", "author", "description", "version", "requirements",
                 "settings", "displayName", "tooltip", "icon", "setParentWidget", "display")
        contract = abc.ABCMeta("IPluginTool", (), {name: abc.abstractmethod(lambda self, *args: None) for name in names})
        messages = []
        mobase = types.ModuleType("mobase")
        mobase.IPluginTool = contract
        mobase.VersionInfo = lambda *args: args
        mobase.__file__ = "C:/fixture/mobase.pyd"
        qt = types.ModuleType("PyQt6")
        gui = types.ModuleType("PyQt6.QtGui")
        core = types.ModuleType("PyQt6.QtCore")
        gui.QIcon = object
        core.qInfo = messages.append
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / ("a" * 32)
            job = root / "SingleFile/jobs/Guarded"
            job.mkdir(parents=True)
            sources = cap.candidate_sources(root, "SingleFile")
            self.assertEqual(["modlab_capability_probe.py"], list(sources))
            package = cap.candidate_sources(root, "Package")
            self.assertEqual(["modlab_capability_probe/__init__.py", "modlab_capability_probe/plugin.py"], list(package))
            with patch.dict("sys.modules", {"mobase": mobase, "PyQt6": qt, "PyQt6.QtGui": gui, "PyQt6.QtCore": core}):
                scope = {"__name__": "test_candidate"}
                exec(compile(sources["modlab_capability_probe.py"], "candidate.py", "exec"), scope)
                plugin = scope["createPlugin"]()
                with patch.dict(os.environ, {}, clear=True):
                    self.assertTrue(plugin.init(object()))
                    self.assertIsNone(plugin.display())
                self.assertEqual([], list(job.iterdir()))
                with patch.dict(os.environ, {"MODLAB_CAPABILITY_PHASE": "Guarded", "MODLAB_CAPABILITY_NONCE": "c" * 32,
                                             "MODLAB_CAPABILITY_GUARD": "c" * 32}, clear=True):
                    self.assertTrue(plugin.init(object()))
                self.assertEqual(["guarded.json"], [p.name for p in job.iterdir()])
                self.assertEqual("Guarded", json.loads((job / "guarded.json").read_bytes())["phase"])
                self.assertEqual("ModLab Capability Probe", plugin.displayName())
                self.assertEqual([], plugin.settings())
                self.assertEqual([], plugin.requirements())
                self.assertTrue(messages)

    def test_environment_refuses_inherited_bytecode_controls_and_confines_writes(self):
        self.assertTrue(hasattr(cap, "launch_environment"), "environment builder is missing")
        root = Path(tempfile.gettempdir()) / ("a" * 32)
        for name in ("PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX", "PYTHONHOME", "PYTHONPATH"):
            with self.subTest(name=name), self.assertRaises(cap.CapabilityError):
                cap.launch_environment(root, "SingleFile", "First", "c" * 32, {name: "anything"})
        result = cap.launch_environment(root, "SingleFile", "First", "c" * 32, {"SystemRoot": "C:\\Windows", "SECRET": "do-not-copy"})
        self.assertNotIn("SECRET", result)
        for name in ("TEMP", "TMP", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME"):
            self.assertTrue(Path(result[name]).is_relative_to(root))
        self.assertNotIn("MODLAB_CAPABILITY_GUARD", result)

    def test_log_parser_requires_one_exact_loaded_marker_for_this_launch(self):
        self.assertTrue(hasattr(cap, "loaded_from_logs"), "live observation parser is missing")
        observed = fixture()["candidates"][0]["observations"][0]["loaded"]
        log = b"[info] MODLAB_CAPABILITY_V1 " + json.dumps(observed, separators=(",", ":")).encode() + b"\n"
        self.assertEqual(observed, cap.loaded_from_logs([log], "First", observed["nonce"]))
        self.assertIsNone(cap.loaded_from_logs([b"[info] plugin failed\n"], "First", observed["nonce"]))
        for logs in ([log, log], [log.replace(b'"First"', b'"Second"')], [b"MODLAB_CAPABILITY_V1 invalid\n"]):
            with self.subTest(logs=logs), self.assertRaises(cap.CapabilityError):
                cap.loaded_from_logs(logs, "First", observed["nonce"])

    def test_live_entry_rejects_paths_outside_exact_disposable_namespace(self):
        self.assertTrue(hasattr(cap, "run_root"), "live namespace validator is missing")
        with self.assertRaises(cap.CapabilityError):
            cap.run_root("../production")
        with self.assertRaises(cap.CapabilityError):
            cap.run_root("C:/production")
        self.assertEqual("a" * 32, cap.run_root("a" * 32).name)


if __name__ == "__main__":
    unittest.main()
