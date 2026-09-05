"""Fail-closed runtime capability evidence; synthetic fixtures are not live proof."""

from __future__ import annotations

import abc
import copy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sys
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
    runtime[1].update(sha256="0522fc235f8ffb2b673d9ced0cd31e4c28da6743b977ffc45253ed261b171a56", size=7439360)
    runtime[2].update(sha256="bc4dd10aef20df58f55e00d407ea15c66dda0618d8468ddea5fbe9c293ce2a00", size=1516544)
    candidates = []
    for layout_index, layout in enumerate(("SingleFile", "Package")):
        app = str(Path(root) / layout / "app")
        for runtime_index, item in enumerate(runtime):
            item["fileId"] = 100 + layout_index * 10 + runtime_index
        baseline = [entry("bundled.py")]
        baseline += [entry(path, "directory", file_id=200 + index) for index, path in enumerate(
            ("plugin_python", "plugin_python/dlls", "plugin_python/libs"))]
        baseline += [{**item, "path": item["path"][len("plugins/"):]} for item in runtime[1:]]
        declared = ([entry("modlab_capability_probe.py", file_id=8)] if layout == "SingleFile" else
                    [entry("modlab_capability_probe", "directory", file_id=8),
                     entry("modlab_capability_probe/__init__.py", file_id=9),
                     entry("modlab_capability_probe/plugin.py", file_id=10)])
        generated = cap.candidate_sources(Path(root), layout)
        for item in declared:
            if item["kind"] == "file":
                data = generated[item["path"]]
                item.update(sha256=hashlib.sha256(data).hexdigest(), size=len(data))
        full = baseline + declared
        observations = []
        for index, phase in enumerate(("Control", "First", "Second", "Passive", "Guarded")):
            nonce = str(index + 1) * 32
            job = str(Path(root) / layout / "jobs" / phase)
            loaded = None if phase == "Control" else {
                "runId": "a" * 32, "layout": layout, "phase": phase, "nonce": nonce, "pid": 123,
                "pythonVersion": "3.12.8 (fixture)", "implementation": "cpython", "cacheTag": "cpython-312",
                "schemaVersion": 2, "bytecodeDisabled": False, "executableRelative": "ModOrganizer.exe",
                "mobaseRelative": "plugins/plugin_python/libs/mobase.cp312-win_amd64.pyd"}
            observations.append({
                "phase": phase, "sequence": index, "job": job, "nonce": nonce, "pid": 123,
                "before": copy.deepcopy(baseline if index == 0 else full),
                "after": copy.deepcopy(baseline if index == 0 else full),
                "ownBefore": [] if index == 0 else copy.deepcopy(declared),
                "ownAfter": [] if index == 0 else copy.deepcopy(declared),
                "runtimeBefore": copy.deepcopy(runtime), "runtimeAfter": copy.deepcopy(runtime),
                "exitCode": 0, "normalClose": True, "loaded": loaded,
                "guarded": copy.deepcopy(loaded) if phase == "Guarded" else None,
                "ui": {"windowId": 42, "app": "process:" + str(Path(app) / "ModOrganizer.exe"), "title": "Mod Organizer v2.5.2",
                       "loadedTool": None if index == 0 else "ModLab Capability Probe",
                       "closeAction": "Alt+F4", "screenshotIds": ["fixture-observation"]},
                "logs": [{"path": str(Path(job) / "observed.log"), "sha256": "f" * 64, "size": 10}],
            })
            observation = observations[-1]
            launcher = {"pid": 123, "creationTime": 1000 + index, "executable": str(Path(app) / "ModOrganizer.exe")}
            observation["launchProcess"] = launcher
            native = {**launcher, "running": True}
            observation["ui"]["processCorrelation"] = {
                "kind": "unique-exact-executable-correlation",
                "events": [
                    {"kind": "native-before", "complete": True, "processes": [copy.deepcopy(native)]},
                    {"kind": "window-state", "windowId": 42, "app": observation["ui"]["app"],
                     "screenshotIds": ["fixture-observation"]},
                    {"kind": "native-after", "complete": True, "processes": [copy.deepcopy(native)]},
                ]}
        candidates.append({"layout": layout, "appRoot": app, "declared": declared,
                           "control": observations[0], "observations": observations[1:]})
    return {"schemaVersion": 2, "runId": "a" * 32, "root": root,
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

    def test_rejects_consistently_substituted_candidate_content(self):
        for layout_index, target in ((0, "modlab_capability_probe.py"),
                                     (1, "modlab_capability_probe/__init__.py"),
                                     (1, "modlab_capability_probe/plugin.py")):
            for changed_field, replacement in (("sha256", "e" * 64), ("size", 1)):
                value = fixture()
                candidate = value["candidates"][layout_index]
                trees = [candidate["declared"]]
                trees.extend(observation[field] for observation in candidate["observations"]
                             for field in ("before", "after", "ownBefore", "ownAfter"))
                for tree in trees:
                    next(item for item in tree if item["path"] == target)[changed_field] = replacement
                with self.subTest(target=target, field=changed_field), self.assertRaises(cap.CapabilityError):
                    cap.select_capability(value)

    def test_retained_runtime_content_is_fixed_even_if_every_record_is_substituted(self):
        for runtime_index in (1, 2):
            for field, replacement in (("sha256", "e" * 64), ("size", 1)):
                value = fixture()
                for candidate in value["candidates"]:
                    for observation in [candidate["control"], *candidate["observations"]]:
                        for side, runtime_side in (("before", "runtimeBefore"), ("after", "runtimeAfter")):
                            runtime = observation[runtime_side][runtime_index]
                            runtime[field] = replacement
                            relative = runtime["path"][len("plugins/"):]
                            next(item for item in observation[side] if item["path"] == relative)[field] = replacement
                with self.subTest(runtime=runtime_index, field=field), self.assertRaises(cap.CapabilityError):
                    cap.select_capability(value)

    def test_runtime_requires_matching_full_tree_content_kind_and_native_identity(self):
        for runtime_index in (1, 2):
            for side, phase in (("before", "Control"), ("after", "Guarded")):
                for field, replacement in (("missing", None), ("sha256", "e" * 64), ("size", 1),
                                           ("volume", 2), ("fileId", 999), ("kind", "directory")):
                    value = fixture()
                    candidate = value["candidates"][0]
                    observation = candidate["control"] if phase == "Control" else candidate["observations"][3]
                    relative = observation["runtimeBefore"][runtime_index]["path"][len("plugins/"):]
                    item = next(item for item in observation[side] if item["path"] == relative)
                    if field == "missing":
                        observation[side].remove(item)
                    elif field == "kind":
                        item.update(kind="directory", sha256=None, size=0)
                    else:
                        item[field] = replacement
                    with self.subTest(runtime=runtime_index, side=side, field=field), self.assertRaises(cap.CapabilityError):
                        cap.select_capability(value)

    def test_exact_runtime_content_allows_distinct_native_ids_between_copies(self):
        value = fixture()
        single = value["candidates"][0]["control"]["runtimeBefore"]
        package = value["candidates"][1]["control"]["runtimeBefore"]
        self.assertNotEqual([item["fileId"] for item in single], [item["fileId"] for item in package])
        self.assertEqual("SingleFile", cap.select_capability(value)["selection"])

    def test_visible_target_requires_exact_recognized_disposable_process_app(self):
        for phase in ("Control", "Guarded"):
            for target in ("process:C:\\unrelated\\ModOrganizer.exe", "unrecognized-app-id"):
                value = fixture()
                candidate = value["candidates"][0]
                observation = candidate["control"] if phase == "Control" else candidate["observations"][3]
                observation["ui"]["app"] = target
                with self.subTest(phase=phase, target=target), self.assertRaises(cap.CapabilityError):
                    cap.select_capability(value)

    def test_visible_target_refuses_missing_launcher_or_native_process_correlation(self):
        for missing in ("launchProcess", "processCorrelation"):
            value = fixture()
            observation = value["candidates"][0]["control"]
            (observation if missing == "launchProcess" else observation["ui"]).pop(missing, None)
            with self.subTest(missing=missing), self.assertRaises(cap.CapabilityError):
                cap.select_capability(value)

    def test_correlation_refuses_incomplete_ambiguous_or_unobservable_native_records(self):
        for event_index in (0, 2):
            for mode in ("empty", "duplicate", "incomplete", "bool-completeness", "unknown", "missing-identity"):
                value = fixture()
                event = value["candidates"][0]["control"]["ui"]["processCorrelation"]["events"][event_index]
                if mode == "empty":
                    event["processes"] = []
                elif mode == "duplicate":
                    event["processes"].append(copy.deepcopy(event["processes"][0]))
                elif mode == "incomplete":
                    event["complete"] = False
                elif mode == "bool-completeness":
                    event["complete"] = 1
                elif mode == "unknown":
                    event["nativeWindowHandle"] = 42
                else:
                    event["processes"][0].pop("creationTime")
                with self.subTest(event=event_index, mode=mode), self.assertRaises(cap.CapabilityError):
                    cap.select_capability(value)

    def test_correlation_binds_both_live_native_identities_to_actual_launcher(self):
        for event_index in (0, 2):
            for field, replacement in (("pid", 124), ("pid", True), ("creationTime", 999),
                                       ("creationTime", None), ("creationTime", True),
                                       ("executable", "C:\\unrelated\\ModOrganizer.exe"),
                                       ("running", False), ("running", 1)):
                value = fixture()
                event = value["candidates"][0]["control"]["ui"]["processCorrelation"]["events"][event_index]
                event["processes"][0][field] = replacement
                with self.subTest(event=event_index, field=field, replacement=replacement), self.assertRaises(cap.CapabilityError):
                    cap.select_capability(value)

    def test_correlation_requires_ordered_brackets_around_the_exact_captured_window(self):
        for mode in ("reversed", "missing", "duplicate", "window", "bool-window", "app", "screenshots", "unknown"):
            value = fixture()
            events = value["candidates"][0]["control"]["ui"]["processCorrelation"]["events"]
            if mode == "reversed":
                events.reverse()
            elif mode == "missing":
                events.pop()
            elif mode == "duplicate":
                events[2] = copy.deepcopy(events[0])
            elif mode == "window":
                events[1]["windowId"] = 43
            elif mode == "bool-window":
                events[1]["windowId"] = True
            elif mode == "app":
                events[1]["app"] = "process:C:\\unrelated\\ModOrganizer.exe"
            elif mode == "screenshots":
                events[1]["screenshotIds"] = ["different-capture"]
            else:
                events[1]["nativeWindowHandle"] = 42
            with self.subTest(mode=mode), self.assertRaises(cap.CapabilityError):
                cap.select_capability(value)

    def test_correlation_rejects_a_stale_prior_launch_even_if_pid_and_window_are_reused(self):
        value = fixture()
        candidate = value["candidates"][0]
        candidate["observations"][0]["ui"]["processCorrelation"] = copy.deepcopy(candidate["control"]["ui"]["processCorrelation"])
        with self.assertRaises(cap.CapabilityError):
            cap.select_capability(value)

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
                if change == "identity":
                    with self.assertRaises(cap.CapabilityError):
                        cap.select_capability(value)
                else:
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
                     lambda v: v["candidates"][0]["observations"][0]["loaded"].update(mobaseRelative="C:/wrong.pyd"),
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

    def test_bounded_snapshot_limits_enumeration_before_retaining_entries(self):
        for index in range(10):
            (self.root / f"{index:03}.bin").write_bytes(b"data")
        original = os.scandir
        yielded = []
        @contextmanager
        def counted(path):
            with original(path) as entries:
                def rows():
                    for entry in entries:
                        yielded.append(entry.name)
                        yield entry
                yield rows()
        with patch.object(cap.os, "scandir", counted), \
                self.assertRaisesRegex(cap.CapabilityError, "entry count"):
            cap.snapshot_tree(self.root, maximum_entries=8, stream_files=True)
        self.assertEqual(9, len(yielded))
        rows = cap.snapshot_tree(self.root, maximum_entries=10, stream_files=True)
        self.assertEqual(10, len(rows))

    def test_bounded_snapshot_streams_large_binary_and_preserves_native_sharing(self):
        earlier = self.root / "a.bin"
        earlier.write_bytes(b"first")
        last = self.root / "z.exe"
        with last.open("wb") as stream:
            stream.truncate(16 * 1024 * 1024 + 1)
        original = cap.hash_pinned_file
        def hashing(pinned, **kwargs):
            if pinned.path == last:
                with self.assertRaises(OSError):
                    earlier.write_bytes(b"changed")
            return original(pinned, **kwargs)
        with patch.object(cap, "hash_pinned_file", side_effect=hashing), \
                patch.object(cap, "read_pinned_file", side_effect=AssertionError("unbounded bytes inventory")):
            rows = cap.snapshot_tree(self.root, maximum_entries=10,
                                     maximum_log_files=128, maximum_log_bytes=16 * 1024 * 1024,
                                     stream_files=True)
        self.assertEqual(16 * 1024 * 1024 + 1, rows[-1]["size"])
        self.assertEqual(hashlib.sha256(b"first").hexdigest(), rows[0]["sha256"])
    def test_bounded_snapshot_preserves_all_partial_close_owners(self):
        from modlab.platform import windows_exact_fs as exact
        for name in ("a.bin", "b.bin", "z.log"):
            (self.root / name).write_bytes(b"data")
        original = exact.PinnedObject.close
        retained = []
        armed = False
        hashing = cap.hash_pinned_file
        def hash_file(pinned, **kwargs):
            nonlocal armed
            if pinned.path.name == "z.log":
                armed = True
            return hashing(pinned, **kwargs)
        def close(pinned):
            if armed and pinned.path.name in {"a.bin", "b.bin"}:
                retained.append(pinned)
                raise OSError("injected snapshot close failure")
            return original(pinned)
        try:
            with patch.object(exact.PinnedObject, "close", close), \
                    patch.object(cap, "hash_pinned_file", side_effect=hash_file), \
                    self.assertRaises(exact.ExactObjectOwnershipError) as raised:
                cap.snapshot_tree(self.root, maximum_entries=3, maximum_log_bytes=1, stream_files=True)
            self.assertEqual({self.root / "a.bin", self.root / "b.bin"},
                             {owner.pinned.path for owner in raised.exception.owners})
            self.assertTrue(all(owner.pinned.handle for owner in raised.exception.owners))
        finally:
            for pinned in retained:
                original(pinned)

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
                data = b"control log\n" if observation["loaded"] is None else b"MODLAB_CAPABILITY_V2 " + json.dumps(observation["loaded"], separators=(",", ":")).encode() + b"\n"
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


class CandidateRuntimeV2Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "Users" / "red" / ("a" * 32)
        for layout in ("SingleFile", "Package"):
            (self.root / layout / "jobs" / "Guarded").mkdir(parents=True)

    @contextmanager
    def probe(self, layout, paths=None):
        """Execute generated code; only unavailable MO2/Qt imports and runtime paths are doubles."""
        app = self.root / layout / "app"
        runtime = {"executable": str(app / "ModOrganizer.exe"),
                   "mobase": str(app / "plugins/plugin_python/libs/mobase.cp312-win_amd64.pyd")}
        runtime.update(paths or {})
        names = ("init", "name", "localizedName", "author", "description", "version", "requirements",
                 "settings", "displayName", "tooltip", "icon", "setParentWidget", "display")
        mobase = types.ModuleType("mobase")
        mobase.IPluginTool = abc.ABCMeta("IPluginTool", (), {
            name: abc.abstractmethod(lambda self, *args: None) for name in names})
        mobase.VersionInfo = lambda *args: args
        mobase.__file__ = runtime["mobase"]
        messages = []
        qt, core, gui = (types.ModuleType(name) for name in ("PyQt6", "PyQt6.QtCore", "PyQt6.QtGui"))
        core.qInfo, gui.QIcon = messages.append, object
        package = types.ModuleType("probe_under_test")
        package.__path__ = []
        package.__package__ = "probe_under_test"
        module = types.ModuleType("probe_under_test.plugin")
        module.__package__ = "probe_under_test"
        modules = {"mobase": mobase, "PyQt6": qt, "PyQt6.QtCore": core, "PyQt6.QtGui": gui,
                   "probe_under_test": package, "probe_under_test.plugin": module}
        sources = cap.candidate_sources(self.root, layout)
        with patch.dict(sys.modules, modules), patch.object(sys, "executable", runtime["executable"]):
            if layout == "SingleFile":
                exec(compile(sources["modlab_capability_probe.py"], "candidate.py", "exec"), module.__dict__)
                factory = module.createPlugin
            else:
                exec(compile(sources["modlab_capability_probe/plugin.py"], "plugin.py", "exec"), module.__dict__)
                exec(compile(sources["modlab_capability_probe/__init__.py"], "__init__.py", "exec"), package.__dict__)
                factory = package.createPlugin
            yield factory(), messages

    def launch(self, plugin, phase, nonce="c" * 32, guard=None):
        environment = {"MODLAB_CAPABILITY_PHASE": phase, "MODLAB_CAPABILITY_NONCE": nonce}
        if guard is not None:
            environment["MODLAB_CAPABILITY_GUARD"] = guard
        with patch.dict(os.environ, environment, clear=True):
            return plugin.init(object())

    def test_both_generated_layouts_emit_v2_observed_relative_paths_without_passive_files(self):
        for layout in ("SingleFile", "Package"):
            for phase in ("First", "Second", "Passive"):
                with self.subTest(layout=layout, phase=phase), self.probe(layout) as (plugin, messages):
                    before = list(self.root.rglob("*"))
                    self.assertTrue(self.launch(plugin, phase))
                    self.assertIsNone(plugin.display())
                    self.assertEqual(before, list(self.root.rglob("*")))
                    self.assertEqual(1, len(messages))
                    self.assertTrue(messages[0].startswith("MODLAB_CAPABILITY_V2 "), messages)
                    observed = cap.loaded_from_logs([messages[0].encode()], phase, "c" * 32)
                    self.assertEqual({"schemaVersion", "runId", "layout", "phase", "nonce", "pid", "pythonVersion",
                                      "implementation", "cacheTag", "bytecodeDisabled", "executableRelative", "mobaseRelative"}, set(observed))
                    self.assertEqual((2, "a" * 32, layout, phase, "c" * 32, os.getpid()),
                                     tuple(observed[k] for k in ("schemaVersion", "runId", "layout", "phase", "nonce", "pid")))
                    self.assertEqual("ModOrganizer.exe", observed["executableRelative"])
                    self.assertEqual("plugins/plugin_python/libs/mobase.cp312-win_amd64.pyd", observed["mobaseRelative"])
                    self.assertEqual(sys.dont_write_bytecode, observed["bytecodeDisabled"])
                    self.assertNotIn(str(self.root), messages[0])

    def test_both_generated_layouts_refuse_outside_traversal_alias_and_wrong_runtime_paths(self):
        for layout in ("SingleFile", "Package"):
            app = self.root / layout / "app"
            for field, relative in (("executable", "ModOrganizer.exe"),
                                     ("mobase", "plugins/plugin_python/libs/mobase.cp312-win_amd64.pyd")):
                bad = [None, True, 42, b"path", "", relative, "C:" + relative, str(app / "wrong-name"),
                       str(app.parent / "outside" / relative), str(Path(str(app) + "-sibling") / relative),
                       str(app / ".." / "app" / relative), str(app / "extra" / ".." / relative),
                       str(app) + os.sep + "." + os.sep + relative,
                       str(app) + os.sep * 2 + relative]
                if os.name == "nt":
                    bad.extend((str(app / relative).replace("\\", "/"), "\\" + relative,
                                "\\\\?\\" + str(app / relative), str(app / relative).swapcase()))
                for actual in bad:
                    with self.subTest(layout=layout, field=field, actual=actual), self.probe(layout, {field: actual}) as (plugin, messages):
                        before = list(self.root.rglob("*"))
                        try:
                            accepted = self.launch(plugin, "First")
                        except (TypeError, ValueError) as error:
                            self.fail(f"invalid observed runtime path was not refused cleanly: {error}")
                        self.assertFalse(accepted)
                        self.assertEqual([], messages)
                        self.assertEqual(before, list(self.root.rglob("*")))

    def test_generated_relative_payload_survives_representative_username_privacy_filter(self):
        for layout in ("SingleFile", "Package"):
            with self.subTest(layout=layout), self.probe(layout) as (plugin, messages):
                self.assertTrue(self.launch(plugin, "First"))
                filtered = messages[0].replace("\\red", "\\USERNAME").replace("/red", "/USERNAME")
                self.assertEqual(messages[0], filtered)
                observed = cap.loaded_from_logs([filtered.encode()], "First", "c" * 32)
                self.assertEqual("ModOrganizer.exe", observed["executableRelative"])
                self.assertEqual("plugins/plugin_python/libs/mobase.cp312-win_amd64.pyd", observed["mobaseRelative"])

    def test_guarded_probe_is_passive_and_requires_exact_guard_nonce(self):
        for layout in ("SingleFile", "Package"):
            with self.subTest(layout=layout), self.probe(layout) as (plugin, messages):
                job = self.root / layout / "jobs" / "Guarded"
                for nonce, guard in (("c" * 32, None), ("c" * 32, "d" * 32), ("invalid", "invalid")):
                    self.assertFalse(self.launch(plugin, "Guarded", nonce, guard))
                    self.assertEqual([], list(job.iterdir()))
                messages.clear()
                self.assertTrue(self.launch(plugin, "Guarded", guard="c" * 32))
                self.assertEqual([], list(job.iterdir()))
                loaded = cap.loaded_from_logs([messages[0].encode()], "Guarded", "c" * 32)
                self.assertIsNotNone(loaded, "generated guarded evidence did not emit the V2 marker")
                self.assertEqual(2, loaded.get("schemaVersion"))
                self.assertEqual("c" * 32, loaded["nonce"])
                self.assertTrue(self.launch(plugin, "Guarded", guard="c" * 32))
                self.assertEqual([], list(job.iterdir()))


class RuntimeV2SchemaTests(unittest.TestCase):
    def test_v2_selection_round_trips_and_old_or_wrong_matrix_versions_refuse(self):
        value = fixture()
        try:
            result = cap.select_capability(value)
        except cap.CapabilityError as error:
            self.fail(f"valid V2 matrix refused: {error}")
        self.assertEqual(2, result["schemaVersion"])
        self.assertEqual(result, cap.capability_from_bytes(cap.capability_to_bytes(result)))
        for version in (1, 3, True, 2.0, "2", None):
            changed = copy.deepcopy(value)
            changed["schemaVersion"] = version
            with self.subTest(version=version), self.assertRaises(cap.CapabilityError):
                cap.select_capability(changed)

    def test_loaded_format_rejects_v1_mixed_unknown_fields_versions_types_and_path_aliases(self):
        mutations = [lambda loaded: loaded.pop("schemaVersion"), lambda loaded: loaded.update(extra=True),
                     lambda loaded: loaded.update(executable="C:\\old\\ModOrganizer.exe"),
                     lambda loaded: loaded.update(mobasePath="C:\\old\\mobase.pyd")]
        mutations += [lambda loaded, version=version: loaded.update(schemaVersion=version)
                      for version in (1, 3, True, 2.0, "2", None)]
        mutations += [lambda loaded, field=field, value=value: loaded.update({field: value})
                      for field in ("executableRelative", "mobaseRelative")
                      for value in (None, True, 42, [], {}, "../ModOrganizer.exe", "/ModOrganizer.exe",
                                    "C:\\ModOrganizer.exe", "C:ModOrganizer.exe", "./ModOrganizer.exe",
                                    "plugins\\plugin_python\\libs\\mobase.cp312-win_amd64.pyd")]
        for mutate in mutations:
            matrix = fixture()
            loaded = matrix["candidates"][0]["observations"][0]["loaded"]
            mutate(loaded)
            with self.subTest(mutation=mutate), self.assertRaises(cap.CapabilityError):
                cap.select_capability(matrix)
            log = b"MODLAB_CAPABILITY_V2 " + json.dumps(loaded).encode()
            with self.subTest(parser_mutation=mutate), self.assertRaises(cap.CapabilityError):
                cap.loaded_from_logs([log], "First", "2" * 32)

    def test_parser_refuses_v1_unknown_and_mixed_markers_instead_of_reinterpreting_them(self):
        loaded = fixture()["candidates"][0]["observations"][0]["loaded"]
        payload = json.dumps(loaded).encode()
        good = b"MODLAB_CAPABILITY_V2 " + payload
        for marker in (b"MODLAB_CAPABILITY_V1 ", b"MODLAB_CAPABILITY_V3 ", b"MODLAB_CAPABILITY_V02 "):
            for logs in ([marker + payload], [good, marker + payload]):
                with self.subTest(marker=marker, mixed=len(logs) == 2), self.assertRaises(cap.CapabilityError):
                    cap.loaded_from_logs(logs, "First", loaded["nonce"])


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(cap, "runtime capability implementation is missing")

    def test_candidate_is_complete_and_passive_including_guarded_init(self):
        self.assertTrue(hasattr(cap, "candidate_sources"), "candidate generator is missing")
        names = ("init", "name", "localizedName", "author", "description", "version", "requirements",
                 "settings", "displayName", "tooltip", "icon", "setParentWidget", "display")
        contract = abc.ABCMeta("IPluginTool", (), {name: abc.abstractmethod(lambda self, *args: None) for name in names})
        messages = []
        mobase = types.ModuleType("mobase")
        mobase.IPluginTool = contract
        mobase.VersionInfo = lambda *args: args
        qt = types.ModuleType("PyQt6")
        gui = types.ModuleType("PyQt6.QtGui")
        core = types.ModuleType("PyQt6.QtCore")
        gui.QIcon = object
        core.qInfo = messages.append
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / ("a" * 32)
            mobase.__file__ = str(root / "SingleFile/app/plugins/plugin_python/libs/mobase.cp312-win_amd64.pyd")
            job = root / "SingleFile/jobs/Guarded"
            job.mkdir(parents=True)
            sources = cap.candidate_sources(root, "SingleFile")
            self.assertEqual(["modlab_capability_probe.py"], list(sources))
            self.assertNotIn(b'.open("x")', sources["modlab_capability_probe.py"])
            package = cap.candidate_sources(root, "Package")
            self.assertEqual(["modlab_capability_probe/__init__.py", "modlab_capability_probe/plugin.py"], list(package))
            with patch.dict("sys.modules", {"mobase": mobase, "PyQt6": qt, "PyQt6.QtGui": gui, "PyQt6.QtCore": core}), \
                    patch.object(sys, "executable", str(root / "SingleFile/app/ModOrganizer.exe")):
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
                self.assertEqual([], list(job.iterdir()))
                self.assertEqual("ModLab Capability Probe", plugin.displayName())
                self.assertEqual([], plugin.settings())
                self.assertEqual([], plugin.requirements())
                self.assertTrue(messages)

    def test_environment_refuses_inherited_bytecode_controls_and_confines_writes(self):
        self.assertTrue(hasattr(cap, "launch_environment"), "environment builder is missing")
        root = Path(tempfile.gettempdir()) / ("a" * 32)
        for name in ("PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX", "PYTHONHOME", "PYTHONPATH", "PythonPath"):
            with self.subTest(name=name), self.assertRaises(cap.CapabilityError):
                cap.launch_environment(root, "SingleFile", "First", "c" * 32, {name: "anything", "USERNAME": "fixture"})
        result = cap.launch_environment(root, "SingleFile", "First", "c" * 32,
                                        {"SystemRoot": "C:\\Windows", "USERNAME": "fixture", "SECRET": "do-not-copy"})
        self.assertNotIn("SECRET", result)
        for name in ("TEMP", "TMP", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME"):
            self.assertTrue(Path(result[name]).is_relative_to(root))
        self.assertNotIn("MODLAB_CAPABILITY_GUARD", result)

    def test_environment_preserves_required_username_case_insensitively_without_other_values(self):
        root = Path(tempfile.gettempdir()) / ("a" * 32)
        for key in ("USERNAME", "username", "UserName"):
            with self.subTest(key=key):
                supplied = {key: "Réd Mod", "SystemRoot": "C:\\Windows", "SECRET": "private", "PATH": "untrusted"}
                result = cap.launch_environment(root, "SingleFile", "First", "c" * 32, supplied)
                self.assertEqual("Réd Mod", result.get("USERNAME"))
                self.assertEqual(["USERNAME"], [name for name in result if name.upper() == "USERNAME"])
                self.assertNotIn("SECRET", result)
                self.assertNotEqual("untrusted", result["PATH"])
                self.assertEqual({key: "Réd Mod", "SystemRoot": "C:\\Windows", "SECRET": "private", "PATH": "untrusted"}, supplied)

    def test_environment_reads_required_username_from_inherited_environment(self):
        root = Path(tempfile.gettempdir()) / ("a" * 32)
        with patch.dict(os.environ, {"USERNAME": " Inherited User ", "SECRET": "private"}, clear=True):
            result = cap.launch_environment(root, "SingleFile", "First", "c" * 32)
        self.assertEqual(" Inherited User ", result.get("USERNAME"))
        self.assertNotIn("SECRET", result)

    def test_environment_refuses_missing_invalid_or_ambiguous_required_username(self):
        root = Path(tempfile.gettempdir()) / ("a" * 32)
        invalid = [{}, {"USERNAME": "red", "username": "other"}, {"USERNAME": "red", "username": "red"}]
        invalid.extend({"USERNAME": value} for value in
                       (None, True, 42, b"red", "", " \t", "red\0suffix", "red\n", "red/path", "red\\path"))
        for supplied in invalid:
            with self.subTest(supplied=supplied), self.assertRaises(cap.CapabilityError):
                cap.launch_environment(root, "SingleFile", "First", "c" * 32, supplied)

    def test_log_parser_requires_one_exact_loaded_marker_for_this_launch(self):
        self.assertTrue(hasattr(cap, "loaded_from_logs"), "live observation parser is missing")
        observed = fixture()["candidates"][0]["observations"][0]["loaded"]
        log = b"[info] MODLAB_CAPABILITY_V2 " + json.dumps(observed, separators=(",", ":")).encode() + b"\n"
        self.assertEqual(observed, cap.loaded_from_logs([log], "First", observed["nonce"]))
        self.assertIsNone(cap.loaded_from_logs([b"[info] plugin failed\n"], "First", observed["nonce"]))
        for logs in ([log, log], [log.replace(b'"First"', b'"Second"')], [b"MODLAB_CAPABILITY_V2 invalid\n"]):
            with self.subTest(logs=logs), self.assertRaises(cap.CapabilityError):
                cap.loaded_from_logs(logs, "First", observed["nonce"])

    def test_live_entry_rejects_paths_outside_exact_disposable_namespace(self):
        self.assertTrue(hasattr(cap, "run_root"), "live namespace validator is missing")
        with self.assertRaises(cap.CapabilityError):
            cap.run_root("../production")
        with self.assertRaises(cap.CapabilityError):
            cap.run_root("C:/production")
        self.assertEqual("a" * 32, cap.run_root("a" * 32).name)



class ProtectedCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.parent = Path(tempfile.mkdtemp(prefix="r8-cap-"))
        self.value = fixture(self.parent / "disposable")
        self.authority = self.parent / "authority" / self.value["runId"]
        self.value.update(schemaVersion=3, authorityRoot=str(self.authority))
        for candidate in self.value["candidates"]:
            for observation in (candidate["control"], *candidate["observations"]):
                job = self.authority / candidate["layout"] / "jobs" / observation["phase"]
                observation["job"] = str(job)
                data = b"control\n" if observation["loaded"] is None else (
                    "MODLAB_CAPABILITY_V2 " + json.dumps(observation["loaded"], sort_keys=True, separators=(",", ":")) + "\n"
                ).encode()
                observation["logs"] = [{"path": str(job / "observed.log"), "size": len(data),
                                         "sha256": hashlib.sha256(data).hexdigest()}]

    def test_separate_authority_preserves_disposable_runtime_and_classifier(self):
        selected = cap.select_capability(self.value)
        self.assertEqual(selected["selection"], "SingleFile")
        self.assertEqual(selected, cap.capability_from_bytes(cap.capability_to_bytes(selected)))
        self.assertEqual(str(Path(self.value["root"]) / "SingleFile" / "app"), selected["candidates"][0]["appRoot"])
        for change in ("overlap", "wrong-id", "low-job", "alias", "unknown"):
            altered = copy.deepcopy(self.value)
            if change == "overlap":
                altered["authorityRoot"] = altered["root"]
            elif change == "wrong-id":
                altered["authorityRoot"] = str(self.authority.with_name("b" * 32))
            elif change == "alias":
                altered["authorityRoot"] = str(self.authority.parent / "unused" / ".." / self.authority.name)
                for candidate in altered["candidates"]:
                    for observation in (candidate["control"], *candidate["observations"]):
                        job = Path(altered["authorityRoot"]) / candidate["layout"] / "jobs" / observation["phase"]
                        observation["job"] = str(job)
                        observation["logs"][0]["path"] = str(job / "observed.log")
            elif change == "low-job":
                altered["candidates"][0]["control"]["job"] = str(Path(altered["root"]) / "SingleFile" / "jobs" / "Control")
            else:
                altered["legacyAuthority"] = True
            with self.subTest(change=change), self.assertRaises(cap.CapabilityError):
                cap.select_capability(altered)

    def test_load_protects_capability_and_reads_only_vault_raw_inputs(self):
        from modlab.validation.windows_vault_security import create_vault
        self.authority.parent.mkdir()
        with create_vault(self.authority) as vault:
            for candidate in self.value["candidates"]:
                for observation in (candidate["control"], *candidate["observations"]):
                    job = Path(observation["job"])
                    job.mkdir(parents=True)
                    data = b"control\n" if observation["loaded"] is None else (
                        "MODLAB_CAPABILITY_V2 " + json.dumps(observation["loaded"], sort_keys=True, separators=(",", ":")) + "\n"
                    ).encode()
                    (job / "observed.log").write_bytes(data)
                    if observation["guarded"] is not None:
                        cap.publish_document(job / "guarded.json", observation["guarded"])
            selected = cap.select_capability(self.value)
            target = self.authority / "selection.json"
            cap.publish_capability(target, selected)
            observed = []
            real_read = cap.read_exact
            def record_read(path, **kwargs):
                observed.append(Path(path))
                return real_read(path, **kwargs)
            with patch.object(cap, "read_exact", side_effect=record_read):
                self.assertEqual(selected, cap.load_capability(target))
                self.assertEqual(selected, cap.load_capability(target, authority_root=self.authority))
            self.assertTrue(observed)
            self.assertTrue(all(path.is_relative_to(self.authority) for path in observed))
            self.assertIn(target, observed)
            (self.parent / "read-inventory.json").write_text(json.dumps({
                "authorityRoot": str(self.authority), "reads": [str(path) for path in observed],
            }, indent=2) + "\n", encoding="utf-8")
            unprotected = self.parent / "selection.json"
            unprotected.write_bytes(cap.capability_to_bytes(selected))
            with self.assertRaises(cap.CapabilityError):
                cap.load_capability(unprotected)
            observed.clear()
            with patch.object(cap, "read_exact", side_effect=record_read):
                with self.assertRaises(cap.CapabilityError):
                    cap.load_capability(unprotected, authority_root=self.authority)
            self.assertEqual([], observed, "known vault must reject external input before reading it")
            legacy_target = self.authority / "legacy-selection.json"
            legacy = cap.select_capability(fixture(self.parent / "legacy-disposable"))
            legacy_target.write_bytes(cap.capability_to_bytes(legacy))
            with self.assertRaisesRegex(cap.CapabilityError, "required authority vault"):
                cap.load_capability(legacy_target, authority_root=self.authority)
            vault.verify()


if __name__ == "__main__":
    unittest.main()
