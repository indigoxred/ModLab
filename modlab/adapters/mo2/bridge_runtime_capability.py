"""Disposable MO2 runtime layout evidence, never bridge/installation authority.

Support requires a candidate-absent control and four real, ordered launches.
The serialized selection is derived, not a caller-supplied success assertion.
All host publication uses the shared native retained-handle no-replace primitive.
"""

from __future__ import annotations

from contextlib import contextmanager, ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from modlab.platform.windows_exact_fs import (
    RetainedObjectOwner, RetainedObjectRole,
    identity_at_path, pin_stable_direct_object, publish_new_pinned, read_pinned_file,
    union_retained_ownership,
)


class CapabilityError(ValueError):
    """Evidence is malformed, unsafe, inconsistent, or cannot prove support."""


LAYOUTS = ("SingleFile", "Package")
PHASES = ("Control", "First", "Second", "Passive", "Guarded")
NAMESPACE = "modlab_capability_probe"
TOOL_NAME = "ModLab Capability Probe"
RUNTIME_PATHS = ("ModOrganizer.exe", "plugins/plugin_python/dlls/python312.dll",
                 "plugins/plugin_python/libs/mobase.cp312-win_amd64.pyd")
ARCHIVE = {"sha256": "e6376efd87fd5ddd95aee959405e8f067afa526ea6c2c0c5aa03c5108bf4a815", "size": 149660212}
EXE_HASH = "442b354a8f34754da0048654c44d27f51628feba54ce46c3187cf58d6c43e622"
MATRIX_FIELDS = {"schemaVersion", "runId", "root", "source", "archive", "candidates"}
OBS_FIELDS = {"phase", "sequence", "job", "nonce", "pid", "before", "after", "ownBefore", "ownAfter",
              "runtimeBefore", "runtimeAfter", "exitCode", "normalClose", "loaded", "guarded", "ui", "logs"}


def _require(condition, message):
    if not condition:
        raise CapabilityError(message)


def _fields(value, names, label):
    _require(type(value) is dict and set(value) == set(names), f"{label} fields are not exact")


def _text(value):
    return type(value) is str and bool(value) and not any(ord(c) < 32 for c in value)


def _hex(value, length):
    return type(value) is str and re.fullmatch(f"[0-9a-f]{{{length}}}", value) is not None


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _path(value):
    _require(_text(value) and Path(value).is_absolute(), "absolute path required")
    _require(str(Path(value).absolute()) == value, "absolute path must be canonical")
    return Path(value)


def _relative(value):
    _require(_text(value) and not any(c in value for c in '\\:<>"|?*'), "unsafe inventory path")
    parts = value.split("/")
    reserved = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
    _require(all(p not in ("", ".", "..") and not p.endswith((".", " "))
                 and p.split(".")[0].lower() not in reserved for p in parts), "unsafe inventory path")


def _inventory(value):
    _require(type(value) is list, "inventory must be a list")
    seen = set()
    for item in value:
        _fields(item, {"path", "kind", "sha256", "size", "volume", "fileId"}, "inventory entry")
        _relative(item["path"])
        key = item["path"].casefold()
        _require(key not in seen, "duplicate inventory path")
        seen.add(key)
        _require(item["kind"] in ("file", "directory"), "redirected/unsupported inventory kind")
        _require(_integer(item["size"]) and _integer(item["volume"]) and _integer(item["fileId"], 1), "invalid inventory identity")
        _require((_hex(item["sha256"], 64) if item["kind"] == "file" else
                  item["sha256"] is None and item["size"] == 0), "invalid inventory content")
    return value


def own_inventory(tree):
    """Diagnostic namespace projection; full-tree comparison remains authoritative."""
    return [item for item in tree if item["path"].split("/")[0].casefold() in (NAMESPACE, NAMESPACE + ".py")
            or (item["path"].lower().startswith("__pycache__/")
                and item["path"].split("/")[-1].lower().startswith(NAMESPACE + "."))]


def _same_tree(left, right):
    return sorted(left, key=lambda x: x["path"]) == sorted(right, key=lambda x: x["path"])


def _runtime(value):
    _inventory(value)
    _require(tuple(item["path"] for item in value) == RUNTIME_PATHS, "runtime file binding differs")
    _require(all(item["kind"] == "file" for item in value), "runtime must contain regular files")
    _require(value[0]["sha256"] == EXE_HASH and value[0]["size"] == 5028352, "MO2 executable identity differs")


def _loaded(value, observation, candidate, matrix):
    if value is None:
        return
    _fields(value, {"runId", "layout", "phase", "nonce", "pid", "pythonVersion", "implementation", "cacheTag",
                    "bytecodeDisabled", "executable", "mobasePath"}, "loaded runtime")
    _require(value["runId"] == matrix["runId"] and value["layout"] == candidate["layout"], "loaded run/layout binding differs")
    _require(all(value[k] == observation[k] for k in ("phase", "nonce", "pid")), "loaded launch binding differs")
    _require(_integer(value["pid"], 1), "loaded PID invalid")
    _require(_text(value["pythonVersion"]) and _text(value["implementation"]) and _text(value["cacheTag"])
             and type(value["bytecodeDisabled"]) is bool, "loaded Python identity invalid")
    _require(value["executable"] == str(Path(candidate["appRoot"]) / RUNTIME_PATHS[0])
             and value["mobasePath"] == str(Path(candidate["appRoot"]) / RUNTIME_PATHS[2]), "loaded runtime path binding differs")


def _observation(value, candidate, matrix, index):
    _fields(value, OBS_FIELDS, "observation")
    _require(value["phase"] == PHASES[index] and type(value["sequence"]) is int and value["sequence"] == index,
             "missing, duplicate, or unordered launch phase")
    job = Path(matrix["root"]) / candidate["layout"] / "jobs" / PHASES[index]
    _require(value["job"] == str(job) and _hex(value["nonce"], 32), "launch job/nonce binding differs")
    _require(_integer(value["pid"], 1) and type(value["exitCode"]) is int and type(value["normalClose"]) is bool,
             "launch outcome types invalid")
    for field in ("before", "after", "ownBefore", "ownAfter"):
        _inventory(value[field])
    _require(_same_tree(value["ownBefore"], own_inventory(value["before"]))
             and _same_tree(value["ownAfter"], own_inventory(value["after"])), "candidate inventory projection differs")
    _runtime(value["runtimeBefore"])
    _runtime(value["runtimeAfter"])
    _loaded(value["loaded"], value, candidate, matrix)
    _loaded(value["guarded"], value, candidate, matrix)
    if index != 4:
        _require(value["guarded"] is None, "non-guarded launch has guarded output")
    if index == 0:
        _require(value["loaded"] is None, "control cannot claim candidate loading")
    ui = value["ui"]
    _fields(ui, {"windowId", "app", "title", "loadedTool", "closeAction", "screenshotIds"}, "visible observation")
    _require(_integer(ui["windowId"], 1) and _text(ui["app"]) and _text(ui["title"]), "invalid visible target")
    _require(ui["loadedTool"] in (None, TOOL_NAME) and ui["closeAction"] in (None, "Alt+F4", "Close button", "File/Exit"), "invalid visible action")
    _require(type(ui["screenshotIds"]) is list and bool(ui["screenshotIds"])
             and all(_text(item) for item in ui["screenshotIds"]), "visible observation references missing")
    _require(type(value["logs"]) is list, "logs must be a list")
    seen = set()
    for log in value["logs"]:
        _fields(log, {"path", "sha256", "size"}, "log")
        path = _path(log["path"])
        _require(path.parent == job and path not in seen and _hex(log["sha256"], 64) and _integer(log["size"]), "log binding invalid")
        seen.add(path)


def _candidate_passes(candidate):
    control = candidate["control"]
    baseline = control["after"]
    runtime = control["runtimeBefore"]
    expected = baseline + candidate["declared"]
    valid = (not own_inventory(control["before"]) and not own_inventory(baseline)
             and control["normalClose"] and control["exitCode"] == 0 and bool(control["logs"])
             and control["ui"]["closeAction"] is not None and control["runtimeAfter"] == runtime)
    for observation in candidate["observations"]:
        loaded = observation["loaded"]
        valid = valid and all((
            _same_tree(observation["before"], expected), _same_tree(observation["after"], expected),
            observation["runtimeBefore"] == runtime, observation["runtimeAfter"] == runtime,
            observation["normalClose"], observation["exitCode"] == 0, bool(observation["logs"]),
            observation["ui"]["loadedTool"] == TOOL_NAME, observation["ui"]["closeAction"] is not None,
            loaded is not None,
        ))
        if loaded is not None:
            valid = valid and loaded["implementation"] == "cpython" and loaded["cacheTag"] == "cpython-312"
        if observation["phase"] == "Guarded":
            valid = valid and observation["guarded"] is not None and observation["guarded"] == loaded
    return bool(valid)


def _canonical(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _unique(pairs):
    value = {}
    for key, item in pairs:
        _require(key not in value, "duplicate JSON key")
        value[key] = item
    return value


def parse_json(data):
    _require(type(data) is bytes, "JSON input must be bytes")
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_unique,
                          parse_constant=lambda value: (_ for _ in ()).throw(CapabilityError("nonfinite JSON")))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise CapabilityError("invalid JSON") from error


def select_capability(matrix):
    _fields(matrix, MATRIX_FIELDS, "matrix")
    _require(type(matrix["schemaVersion"]) is int and matrix["schemaVersion"] == 1 and _hex(matrix["runId"], 32), "invalid matrix identity")
    root = _path(matrix["root"])
    _require(root.name == matrix["runId"], "run root binding differs")
    _fields(matrix["source"], {"commit", "tree"}, "source")
    _require(all(_hex(v, 40) for v in matrix["source"].values()), "source identity invalid")
    _fields(matrix["archive"], {"sha256", "size"}, "archive")
    _require(type(matrix["archive"]["size"]) is int and matrix["archive"] == ARCHIVE, "archive identity differs")
    candidates = matrix["candidates"]
    _require(type(candidates) is list and len(candidates) == 2, "two ordered candidates required")
    for index, candidate in enumerate(candidates):
        _fields(candidate, {"layout", "appRoot", "declared", "control", "observations"}, "candidate")
        _require(candidate["layout"] == LAYOUTS[index] and candidate["appRoot"] == str(root / LAYOUTS[index] / "app"), "candidate binding differs")
        declared = _inventory(candidate["declared"])
        names = ([NAMESPACE + ".py"] if index == 0 else [NAMESPACE, NAMESPACE + "/__init__.py", NAMESPACE + "/plugin.py"])
        _require([item["path"] for item in declared] == names, "candidate declared inventory differs")
        _require(all(item["kind"] == ("directory" if item["path"] == NAMESPACE else "file") for item in declared), "candidate declared types differ")
        _require(type(candidate["observations"]) is list and len(candidate["observations"]) == 4, "four ordered launch phases required")
        _observation(candidate["control"], candidate, matrix, 0)
        for phase, observation in enumerate(candidate["observations"], 1):
            _observation(observation, candidate, matrix, phase)
    selected = next((candidate["layout"] for candidate in candidates if _candidate_passes(candidate)), "NotSupported")
    result = {**matrix, "selection": selected}
    result["capabilityId"] = "mo2-runtime-capability-sha256:" + hashlib.sha256(_canonical(result)).hexdigest()
    return parse_json(_canonical(result))


def capability_to_bytes(value):
    _fields(value, MATRIX_FIELDS | {"selection", "capabilityId"}, "capability")
    expected = select_capability({key: value[key] for key in MATRIX_FIELDS})
    _require(value == expected, "fabricated capability selection/content identity")
    return _canonical(value)


def capability_from_bytes(data):
    value = parse_json(data)
    _require(capability_to_bytes(value) == data, "capability bytes are not canonical")
    return value


@contextmanager
def _pinned(path, kind):
    pinned = pin_stable_direct_object(path, kind)
    prior = None
    try:
        yield pinned
    except BaseException as error:
        prior = error
        raise
    finally:
        try:
            pinned.close()
        except BaseException as error:
            if pinned.handle:
                raise union_retained_ownership("capability verification retains live ownership", prior=prior,
                      owners=(RetainedObjectOwner(RetainedObjectRole.VERIFICATION, pinned),)) from error
            raise


@contextmanager
def _parents(path):
    absolute = Path(path).absolute()
    def direct_chain():
        identities = []
        for parent in (absolute, *absolute.parents):
            metadata = parent.lstat()
            _require(stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)
                     and not getattr(metadata, "st_file_attributes", 0) & 0x400, "redirected parent directory")
            identities.append((metadata.st_dev, metadata.st_ino, metadata.st_mode, getattr(metadata, "st_file_attributes", 0)))
        return identities
    before = direct_chain()
    with _pinned(absolute, "directory") as retained:
        _require(direct_chain() == before, "ancestor identity changed during acquisition")
        yield
        _require(direct_chain() == before, "ancestor identity changed during verification")
        _require(identity_at_path(absolute) == retained.identity, "retained root path identity changed")


def read_exact(path):
    target = Path(path).absolute()
    with _parents(target.parent), _pinned(target, "file") as pinned:
        return read_pinned_file(pinned)


def _entry(path, relative, kind):
    with _pinned(path, kind) as pinned:
        data = read_pinned_file(pinned) if kind == "file" else b""
        return {"path": relative, "kind": kind, "sha256": hashlib.sha256(data).hexdigest() if kind == "file" else None,
                "size": len(data), "volume": pinned.identity.volume_serial, "fileId": pinned.identity.file_id}


def snapshot_tree(root):
    """Retain all descendants and bracket every directory's exact membership."""
    result = []
    root = Path(root).absolute()
    members = {}

    def walk(directory, stack):
        paths = sorted(directory.iterdir(), key=lambda p: (p.name.casefold(), p.name))
        _require(len({p.name.casefold() for p in paths}) == len(paths), "case-insensitive inventory collision")
        children = []
        members[directory] = []
        for path in paths:
            metadata = path.lstat()
            _require(not stat.S_ISLNK(metadata.st_mode) and not getattr(metadata, "st_file_attributes", 0) & 0x400, "redirected inventory entry")
            kind = "directory" if stat.S_ISDIR(metadata.st_mode) else "file"
            _require(kind == "directory" or stat.S_ISREG(metadata.st_mode), "unsupported inventory entry")
            relative = path.relative_to(root).as_posix()
            _relative(relative)
            expected = identity_at_path(path)
            pinned = stack.enter_context(_pinned(path, kind))
            _require(expected == pinned.identity, "inventory member changed during acquisition")
            members[directory].append((path, pinned.identity))
            data = read_pinned_file(pinned) if kind == "file" else b""
            result.append({"path": relative, "kind": kind,
                "sha256": hashlib.sha256(data).hexdigest() if kind == "file" else None,
                "size": len(data), "volume": pinned.identity.volume_serial, "fileId": pinned.identity.file_id})
            if kind == "directory":
                children.append(path)
        for child in children:
            walk(child, stack)

    with _parents(root), ExitStack() as stack:
        walk(root, stack)
        for directory, expected in members.items():
            actual = sorted(directory.iterdir(), key=lambda p: (p.name.casefold(), p.name))
            _require(actual == [path for path, _identity in expected], "inventory directory membership changed")
            for path, identity in expected:
                _require(identity_at_path(path) == identity, "inventory member identity changed")
    return sorted(result, key=lambda item: (item["path"].casefold(), item["path"]))


def publish_document(path, value):
    """No replacement or pathname fallback; native ownership errors remain typed."""
    data = _canonical(value)
    def validate(actual):
        _require(actual == data, "published bytes differ")
        _require(_canonical(parse_json(actual)) == actual, "published JSON is noncanonical")
        return parse_json(actual)
    return publish_new_pinned(Path(path), data, validate)


def publish_capability(path, value):
    capability_to_bytes(value)
    _verify_raw_evidence(value)
    return publish_new_pinned(Path(path), capability_to_bytes(value), capability_from_bytes)


def load_capability(path):
    value = capability_from_bytes(read_exact(path))
    _verify_raw_evidence(value)
    return value


def _verify_raw_evidence(value):
    for candidate in value["candidates"]:
        for observation in (candidate["control"], *candidate["observations"]):
            logs = []
            for reference in observation["logs"]:
                data = read_exact(reference["path"])
                _require(len(data) == reference["size"] and hashlib.sha256(data).hexdigest() == reference["sha256"], "raw log identity differs")
                logs.append(data)
            loaded = loaded_from_logs(logs, observation["phase"], observation["nonce"])
            _require(loaded == observation["loaded"], "loaded claim differs from raw log evidence")
            guarded_path = Path(observation["job"]) / "guarded.json"
            if observation["guarded"] is not None:
                _require(parse_json(read_exact(guarded_path)) == observation["guarded"], "guarded claim differs from raw evidence")
            else:
                _require(not guarded_path.exists(), "undeclared guarded evidence exists")


def candidate_sources(root, layout):
    """Generate the same complete passive tool contract for both tested layouts."""
    root = _path(str(root))
    _require(_hex(root.name, 32) and layout in LAYOUTS, "invalid candidate binding")
    source = '''# Disposable capability probe; not a bridge or installation interface.
import json
import os
from pathlib import Path
import re
import stat
import sys
import mobase
from PyQt6.QtCore import qInfo
from PyQt6.QtGui import QIcon

ROOT = Path(ROOT_LITERAL)
LAYOUT = LAYOUT_LITERAL

class CapabilityProbe(mobase.IPluginTool):
    def init(self, organizer):
        phase = os.environ.get("MODLAB_CAPABILITY_PHASE", "Passive")
        nonce = os.environ.get("MODLAB_CAPABILITY_NONCE", "")
        if phase not in ("First", "Second", "Passive", "Guarded"):
            return False
        observed = {"runId": ROOT.name, "layout": LAYOUT, "phase": phase, "nonce": nonce,
                    "pid": os.getpid(), "pythonVersion": sys.version,
                    "implementation": sys.implementation.name, "cacheTag": sys.implementation.cache_tag,
                    "bytecodeDisabled": sys.dont_write_bytecode,
                    "executable": str(Path(sys.executable).absolute()),
                    "mobasePath": str(Path(mobase.__file__).absolute())}
        if phase == "Guarded" and re.fullmatch(r"[0-9a-f]{32}", nonce) and os.environ.get("MODLAB_CAPABILITY_GUARD") == nonce:
            job = ROOT / LAYOUT / "jobs" / "Guarded"
            for path in (job, *job.parents):
                meta = path.lstat()
                if not stat.S_ISDIR(meta.st_mode) or stat.S_ISLNK(meta.st_mode) or getattr(meta, "st_file_attributes", 0) & 0x400:
                    return False
            with (job / "guarded.json").open("x", encoding="utf-8", newline="\\n") as output:
                json.dump(observed, output, sort_keys=True, separators=(",", ":"))
                output.write("\\n")
        qInfo("MODLAB_CAPABILITY_V1 " + json.dumps(observed, sort_keys=True, separators=(",", ":")))
        return True
    def name(self): return "ModLab Capability Probe"
    def localizedName(self): return self.name()
    def author(self): return "ModLab"
    def description(self): return "Harmless disposable runtime capability observation"
    def version(self): return mobase.VersionInfo(1, 0, 0, 0)
    def requirements(self): return []
    def settings(self): return []
    def displayName(self): return self.name()
    def tooltip(self): return self.description()
    def icon(self): return QIcon()
    def setParentWidget(self, widget): pass
    def display(self): pass

def createPlugin():
    return CapabilityProbe()
'''.replace("ROOT_LITERAL", repr(str(root))).replace("LAYOUT_LITERAL", repr(layout)).encode("utf-8")
    if layout == "SingleFile":
        return {NAMESPACE + ".py": source}
    return {NAMESPACE + "/__init__.py": b"from .plugin import createPlugin\n", NAMESPACE + "/plugin.py": source}


def launch_environment(root, layout, phase, nonce, inherited=None):
    """Build a local-only environment; never set, remove-and-ignore, or trust bytecode flags."""
    root = _path(str(root))
    _require(layout in LAYOUTS and phase in PHASES and _hex(nonce, 32), "invalid launch binding")
    inherited = os.environ if inherited is None else inherited
    _require(not any(name.upper().startswith("PYTHON") for name in inherited), "inherited Python environment controls require operator resolution")
    system = next((value for name, value in inherited.items() if name.upper() == "SYSTEMROOT"), r"C:\Windows")
    environment = {"SystemRoot": system, "WINDIR": system, "PATH": str(Path(system) / "System32")}
    base = root / layout / "environment"
    environment.update({name: str(base / name) for name in ("TEMP", "TMP", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME")})
    environment.update(MODLAB_CAPABILITY_PHASE=phase, MODLAB_CAPABILITY_NONCE=nonce)
    if phase == "Guarded":
        environment["MODLAB_CAPABILITY_GUARD"] = nonce
    return environment


def loaded_from_logs(logs, phase, nonce):
    """Require one marker for this nonce; stale launches cannot establish loading."""
    markers = []
    for data in logs:
        _require(type(data) is bytes, "log must be bytes")
        for line in data.splitlines():
            marker = b"MODLAB_CAPABILITY_V1 "
            if marker not in line:
                continue
            value = parse_json(line.split(marker, 1)[1])
            _require(type(value) is dict, "runtime marker must be an object")
            if value.get("nonce") == nonce:
                _require(value.get("phase") == phase, "runtime marker phase binding differs")
                markers.append(value)
    _require(len(markers) <= 1, "duplicate runtime loading markers")
    return markers[0] if markers else None


def run_root(run_id):
    """Resolve only a fresh capability ID beneath this source worktree."""
    _require(_hex(run_id, 32), "invalid disposable run ID")
    return Path(__file__).absolute().parents[3] / "workspace/runtime/validation/mo2-bridge-capability" / run_id
