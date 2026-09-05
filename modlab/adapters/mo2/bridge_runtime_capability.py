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

from modlab.validation.windows_vault_security import open_vault

from modlab.platform.windows_exact_fs import (
    RetainedObjectOwner, RetainedObjectRole,
    identity_at_path, pin_stable_direct_object, publish_new_pinned, read_pinned_file,
    union_retained_ownership, hash_pinned_file,
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
RUNTIME_CONTENT = ((EXE_HASH, 5028352),
                   ("0522fc235f8ffb2b673d9ced0cd31e4c28da6743b977ffc45253ed261b171a56", 7439360),
                   ("bc4dd10aef20df58f55e00d407ea15c66dda0618d8468ddea5fbe9c293ce2a00", 1516544))
MATRIX_FIELDS = {"schemaVersion", "runId", "root", "source", "archive", "candidates"}
PROTECTED_MATRIX_FIELDS = MATRIX_FIELDS | {"authorityRoot"}
OBS_FIELDS = {"phase", "sequence", "job", "nonce", "pid", "before", "after", "ownBefore", "ownAfter",
              "runtimeBefore", "runtimeAfter", "exitCode", "normalClose", "loaded", "guarded", "ui", "logs", "launchProcess"}


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
    _require(tuple((item["sha256"], item["size"]) for item in value) == RUNTIME_CONTENT,
             "exact retained MO2/Python/mobase content identity differs")


def _loaded_format(value):
    _fields(value, {"schemaVersion", "runId", "layout", "phase", "nonce", "pid", "pythonVersion", "implementation", "cacheTag",
                    "bytecodeDisabled", "executableRelative", "mobaseRelative"}, "loaded runtime")
    _require(type(value["schemaVersion"]) is int and value["schemaVersion"] == 2, "loaded runtime version differs")
    _require(_integer(value["pid"], 1), "loaded PID invalid")
    _require(_text(value["pythonVersion"]) and _text(value["implementation"]) and _text(value["cacheTag"])
             and type(value["bytecodeDisabled"]) is bool, "loaded Python identity invalid")
    _require(value["executableRelative"] == RUNTIME_PATHS[0] and value["mobaseRelative"] == RUNTIME_PATHS[2],
             "loaded relative runtime path binding differs")


def _loaded(value, observation, candidate, matrix):
    if value is None:
        return
    _loaded_format(value)
    _require(value["runId"] == matrix["runId"] and value["layout"] == candidate["layout"], "loaded run/layout binding differs")
    _require(all(value[k] == observation[k] for k in ("phase", "nonce", "pid")), "loaded launch binding differs")


def _visible_process_binding(ui, launcher, executable, pid):
    """Validate observed executable correlation, never infer HWND ownership from a sky ID."""
    _require(ui["app"] == "process:" + executable, "visible app is not the exact disposable executable")
    _fields(launcher, {"pid", "creationTime", "executable"}, "launcher process identity")
    _require(_integer(launcher["pid"], 1) and _integer(launcher["creationTime"], 1)
             and launcher["pid"] == pid and launcher["executable"] == executable, "launcher process binding differs")
    correlation = ui["processCorrelation"]
    _fields(correlation, {"kind", "events"}, "visible process correlation")
    events = correlation["events"]
    _require(correlation["kind"] == "unique-exact-executable-correlation" and type(events) is list and len(events) == 3,
             "three ordered process/window correlation events required")
    for index, event in enumerate(events):
        if index == 1:
            _fields(event, {"kind", "windowId", "app", "screenshotIds"}, "correlated window state")
            _require(_integer(event["windowId"], 1)
                     and all(event[key] == ui[key] for key in ("windowId", "app", "screenshotIds")),
                     "correlation is not for the captured visible window")
        else:
            _fields(event, {"kind", "complete", "processes"}, "native process observation")
            _require(event["complete"] is True and type(event["processes"]) is list and len(event["processes"]) == 1,
                     "one completely observed matching native process required")
            process = event["processes"][0]
            _fields(process, {"pid", "creationTime", "executable", "running"}, "observed native process identity")
            _require(_integer(process["pid"], 1) and _integer(process["creationTime"], 1) and process["running"] is True
                     and all(process[key] == launcher[key] for key in launcher),
                     "observed native process is not the live launcher identity")
        _require(event["kind"] == ("native-before", "window-state", "native-after")[index],
                 "native process observations do not bracket the window observation")


def _observation(value, candidate, matrix, index):
    _fields(value, OBS_FIELDS, "observation")
    _require(value["phase"] == PHASES[index] and type(value["sequence"]) is int and value["sequence"] == index,
             "missing, duplicate, or unordered launch phase")
    job = Path(matrix["authorityRoot"] if matrix.get("schemaVersion") == 3 else matrix["root"]) / candidate["layout"] / "jobs" / PHASES[index]
    _require(value["job"] == str(job) and _hex(value["nonce"], 32), "launch job/nonce binding differs")
    _require(_integer(value["pid"], 1) and type(value["exitCode"]) is int and type(value["normalClose"]) is bool,
             "launch outcome types invalid")
    for field in ("before", "after", "ownBefore", "ownAfter"):
        _inventory(value[field])
    _require(_same_tree(value["ownBefore"], own_inventory(value["before"]))
             and _same_tree(value["ownAfter"], own_inventory(value["after"])), "candidate inventory projection differs")
    for runtime_field, tree_field in (("runtimeBefore", "before"), ("runtimeAfter", "after")):
        _runtime(value[runtime_field])
        tree = {item["path"]: item for item in value[tree_field]}
        for item in value[runtime_field][1:]:
            relative = item["path"][len("plugins/"):]
            _require(tree.get(relative) == {**item, "path": relative},
                     "runtime observation contradicts the complete plugins inventory")
    _loaded(value["loaded"], value, candidate, matrix)
    _loaded(value["guarded"], value, candidate, matrix)
    if index != 4:
        _require(value["guarded"] is None, "non-guarded launch has guarded output")
    if index == 0:
        _require(value["loaded"] is None, "control cannot claim candidate loading")
    ui = value["ui"]
    _fields(ui, {"windowId", "app", "title", "loadedTool", "closeAction", "screenshotIds", "processCorrelation"}, "visible observation")
    _require(_integer(ui["windowId"], 1) and _text(ui["app"]) and _text(ui["title"]), "invalid visible target")
    _require(ui["loadedTool"] in (None, TOOL_NAME) and ui["closeAction"] in (None, "Alt+F4", "Close button", "File/Exit"), "invalid visible action")
    _require(type(ui["screenshotIds"]) is list and bool(ui["screenshotIds"])
             and all(_text(item) for item in ui["screenshotIds"]), "visible observation references missing")
    _visible_process_binding(ui, value["launchProcess"], str(Path(candidate["appRoot"]) / "ModOrganizer.exe"), value["pid"])
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
    fields = PROTECTED_MATRIX_FIELDS if type(matrix) is dict and matrix.get("schemaVersion") == 3 else MATRIX_FIELDS
    _fields(matrix, fields, "matrix")
    _require(type(matrix["schemaVersion"]) is int and matrix["schemaVersion"] in (2, 3) and _hex(matrix["runId"], 32), "invalid matrix identity")
    root = _path(matrix["root"])
    _require(root.name == matrix["runId"], "run root binding differs")
    if matrix["schemaVersion"] == 3:
        authority = _path(matrix["authorityRoot"])
        _require(".." not in root.parts and ".." not in authority.parts
                 and authority.name == matrix["runId"] and not authority.is_relative_to(root)
                 and not root.is_relative_to(authority), "authority/disposable root binding differs")
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
        generated = candidate_sources(root, candidate["layout"])
        for item in declared:
            if item["kind"] == "file":
                data = generated[item["path"]]
                _require(item["sha256"] == hashlib.sha256(data).hexdigest() and item["size"] == len(data),
                         "candidate content differs from the exact generated harmless probe")
        _require(type(candidate["observations"]) is list and len(candidate["observations"]) == 4, "four ordered launch phases required")
        _observation(candidate["control"], candidate, matrix, 0)
        for phase, observation in enumerate(candidate["observations"], 1):
            _observation(observation, candidate, matrix, phase)
    selected = next((candidate["layout"] for candidate in candidates if _candidate_passes(candidate)), "NotSupported")
    result = {**matrix, "selection": selected}
    result["capabilityId"] = "mo2-runtime-capability-sha256:" + hashlib.sha256(_canonical(result)).hexdigest()
    return parse_json(_canonical(result))


def capability_to_bytes(value):
    fields = PROTECTED_MATRIX_FIELDS if type(value) is dict and value.get("schemaVersion") == 3 else MATRIX_FIELDS
    _fields(value, fields | {"selection", "capabilityId"}, "capability")
    expected = select_capability({key: value[key] for key in fields})
    _require(value == expected, "fabricated capability selection/content identity")
    return _canonical(value)


def capability_from_bytes(data):
    value = parse_json(data)
    _require(capability_to_bytes(value) == data, "capability bytes are not canonical")
    return value


@contextmanager
def _pinned(path, kind, *, delete_access=True, allow_writes=False):
    pinned = pin_stable_direct_object(path, kind, delete_access=delete_access, allow_writes=allow_writes)
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
def _parents(path, *, delete_access=True, allow_writes=False):
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
    with _pinned(absolute, "directory", delete_access=delete_access, allow_writes=allow_writes) as retained:
        _require(direct_chain() == before, "ancestor identity changed during acquisition")
        yield
        _require(direct_chain() == before, "ancestor identity changed during verification")
        _require(identity_at_path(absolute) == retained.identity, "retained root path identity changed")


def read_exact(path, *, maximum_bytes=None):
    target = Path(path).absolute()
    with _parents(target.parent, delete_access=False, allow_writes=True), _pinned(target, "file", delete_access=False) as pinned:
        return read_pinned_file(pinned, maximum_bytes=maximum_bytes)


def _entry(path, relative, kind):
    with _pinned(path, kind) as pinned:
        data = read_pinned_file(pinned) if kind == "file" else b""
        return {"path": relative, "kind": kind, "sha256": hashlib.sha256(data).hexdigest() if kind == "file" else None,
                "size": len(data), "volume": pinned.identity.volume_serial, "fileId": pinned.identity.file_id}


def snapshot_tree(root, *, maximum_entries=None, maximum_log_files=None,
                  maximum_log_bytes=None, stream_files=False):
    """Retain descendants and bracket membership; optional limits precede reads."""
    for bound in (maximum_entries, maximum_log_files, maximum_log_bytes):
        _require(bound is None or type(bound) is int and bound >= 0, "invalid inventory bound")
    result = []
    root = Path(root).absolute()
    members = {}
    files = []
    discovered = 0
    log_count = 0

    def paths_at(directory, limit):
        if limit is None:
            return sorted(directory.iterdir(), key=lambda p: (p.name.casefold(), p.name))
        paths = []
        # Path.iterdir may materialize the entire native listing before yielding.
        with os.scandir(directory) as entries:
            for entry in entries:
                _require(len(paths) < limit, "inventory entry count exceeds bound")
                paths.append(directory / entry.name)
        return sorted(paths, key=lambda p: (p.name.casefold(), p.name))

    def walk(directory, stack):
        nonlocal discovered, log_count
        paths = paths_at(directory, None if maximum_entries is None else maximum_entries - discovered)
        discovered += len(paths)
        _require(len({p.name.casefold() for p in paths}) == len(paths), "case-insensitive inventory collision")
        children = []
        members[directory] = []
        for path in paths:
            metadata = path.lstat()
            _require(not stat.S_ISLNK(metadata.st_mode) and not getattr(metadata, "st_file_attributes", 0) & 0x400, "redirected inventory entry")
            kind = "directory" if stat.S_ISDIR(metadata.st_mode) else "file"
            _require(kind == "directory" or stat.S_ISREG(metadata.st_mode), "unsupported inventory entry")
            is_log = kind == "file" and path.name.casefold().endswith(".log")
            if is_log:
                log_count += 1
                _require(maximum_log_files is None or log_count <= maximum_log_files,
                         "inventory log count exceeds bound")
            relative = path.relative_to(root).as_posix()
            _relative(relative)
            expected = identity_at_path(path)
            pinned = stack.enter_context(_pinned(path, kind))
            _require(expected == pinned.identity, "inventory member changed during acquisition")
            members[directory].append((path, pinned.identity))
            row = {"path": relative, "kind": kind, "sha256": None, "size": 0,
                   "volume": pinned.identity.volume_serial, "fileId": pinned.identity.file_id}
            result.append(row)
            if kind == "file":
                files.append((pinned, row, maximum_log_bytes if is_log else None))
            else:
                children.append(path)
        for child in children:
            walk(child, stack)

    with _parents(root), ExitStack() as stack:
        walk(root, stack)
        # Complete bounded discovery before consuming any Low content.
        for pinned, row, maximum_bytes in files:
            if stream_files:
                row["sha256"], row["size"] = hash_pinned_file(pinned, maximum_bytes=maximum_bytes)
            else:
                data = (read_pinned_file(pinned) if maximum_bytes is None else
                        read_pinned_file(pinned, maximum_bytes=maximum_bytes))
                row["sha256"], row["size"] = hashlib.sha256(data).hexdigest(), len(data)
        for directory, expected in members.items():
            actual = paths_at(directory, len(expected) if maximum_entries is not None else None)
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
    data = capability_to_bytes(value)
    if value["schemaVersion"] == 3:
        target = Path(path).absolute()
        authority = Path(value["authorityRoot"])
        _require(target.is_relative_to(authority), "capability file is outside authority vault")
        with open_vault(authority) as vault:
            vault.verify_descendant(target.parent)
            _verify_raw_evidence(value, vault=vault)
            result = publish_new_pinned(target, data, capability_from_bytes)
            vault.verify_descendant(target)
            return result
    _verify_raw_evidence(value)
    return publish_new_pinned(Path(path), data, capability_from_bytes)


def load_capability(path, *, authority_root=None):
    target = Path(path).absolute()
    initial = None
    if authority_root is None:
        # Discovery is non-authorizing. Schema 3 must be reread under its vault.
        initial = read_exact(target)
        value = capability_from_bytes(initial)
        if value["schemaVersion"] == 2:
            _verify_raw_evidence(value)
            return value
        authority = Path(value["authorityRoot"])
    else:
        authority = _path(str(Path(authority_root).absolute()))
    _require(target.is_relative_to(authority), "capability file is outside authority vault")
    with open_vault(authority) as vault:
        vault.verify_descendant(target)
        exact = read_exact(target)
        if initial is not None:
            _require(exact == initial, "capability changed during vault acquisition")
        value = capability_from_bytes(exact)
        _require(value["schemaVersion"] == 3 and value["authorityRoot"] == str(authority),
                 "capability does not bind the required authority vault")
        _verify_raw_evidence(value, vault=vault)
        vault.verify()
        return value


def _verify_raw_evidence(value, *, vault=None):
    if value.get("schemaVersion") == 3 and vault is None:
        with open_vault(Path(value["authorityRoot"])) as guarded:
            return _verify_raw_evidence(value, vault=guarded)
    for candidate in value["candidates"]:
        for observation in (candidate["control"], *candidate["observations"]):
            logs = []
            for reference in observation["logs"]:
                if vault is not None:
                    vault.verify_descendant(Path(reference["path"]))
                data = read_exact(reference["path"], maximum_bytes=reference["size"])
                _require(len(data) == reference["size"] and hashlib.sha256(data).hexdigest() == reference["sha256"], "raw log identity differs")
                logs.append(data)
            loaded = loaded_from_logs(logs, observation["phase"], observation["nonce"])
            _require(loaded == observation["loaded"], "loaded claim differs from raw log evidence")
            guarded_path = Path(observation["job"]) / "guarded.json"
            if vault is not None:
                vault.verify_descendant(guarded_path.parent)
            if observation["guarded"] is not None:
                if vault is not None:
                    vault.verify_descendant(guarded_path)
                _require(parse_json(read_exact(guarded_path)) == observation["guarded"], "guarded claim differs from raw evidence")
            else:
                _require(not guarded_path.exists(), "undeclared guarded evidence exists")
    if vault is not None:
        vault.verify()


def candidate_sources(root, layout):
    """Generate the same complete passive tool contract for both tested layouts."""
    root = _path(str(root))
    _require(_hex(root.name, 32) and layout in LAYOUTS, "invalid candidate binding")
    source = '''# Disposable capability probe; not a bridge or installation interface.
import json
import os
from pathlib import Path
import re
import sys
import mobase
from PyQt6.QtCore import qInfo
from PyQt6.QtGui import QIcon

ROOT = Path(ROOT_LITERAL)
LAYOUT = LAYOUT_LITERAL
APP = ROOT / LAYOUT / "app"

def runtime_relative(value, expected):
    if type(value) is not str:
        return None
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        return None
    try:
        relative = path.relative_to(APP).as_posix()
    except ValueError:
        return None
    # Derive from the observation, refusing aliases instead of resolving or guessing.
    return relative if relative == expected and value == str(APP / relative) else None

class CapabilityProbe(mobase.IPluginTool):
    def init(self, organizer):
        phase = os.environ.get("MODLAB_CAPABILITY_PHASE", "Passive")
        nonce = os.environ.get("MODLAB_CAPABILITY_NONCE", "")
        if phase not in ("First", "Second", "Passive", "Guarded"):
            return False
        executable = runtime_relative(sys.executable, "ModOrganizer.exe")
        mobase_path = runtime_relative(getattr(mobase, "__file__", None), "plugins/plugin_python/libs/mobase.cp312-win_amd64.pyd")
        if executable is None or mobase_path is None:
            return False
        # MO2 v2.5.2 loglist.cpp filters username prefixes; do not log unnecessary absolute paths.
        observed = {"schemaVersion": 2, "runId": ROOT.name, "layout": LAYOUT, "phase": phase, "nonce": nonce,
                    "pid": os.getpid(), "pythonVersion": sys.version,
                    "implementation": sys.implementation.name, "cacheTag": sys.implementation.cache_tag,
                    "bytecodeDisabled": sys.dont_write_bytecode,
                    "executableRelative": executable, "mobaseRelative": mobase_path}
        if phase == "Guarded" and (
            re.fullmatch(r"[0-9a-f]{32}", nonce) is None
            or os.environ.get("MODLAB_CAPABILITY_GUARD") != nonce
        ):
            return False
        qInfo("MODLAB_CAPABILITY_V2 " + json.dumps(observed, sort_keys=True, separators=(",", ":")))
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
    # MO2 v2.5.2 src/loglist.cpp initLogging concatenates getenv("USERNAME") without a null check.
    usernames = [value for name, value in inherited.items() if name.upper() == "USERNAME"]
    _require(len(usernames) == 1 and type(usernames[0]) is str, "one required Windows USERNAME must be supplied")
    username = usernames[0]
    _require(username.strip() and username.isprintable() and not any(char in username for char in "/\\"),
             "required Windows USERNAME is invalid")
    system = next((value for name, value in inherited.items() if name.upper() == "SYSTEMROOT"), r"C:\Windows")
    environment = {"SystemRoot": system, "WINDIR": system, "PATH": str(Path(system) / "System32"), "USERNAME": username}
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
            prefix = b"MODLAB_CAPABILITY_"
            if prefix not in line:
                continue
            payload = line.split(prefix, 1)[1]
            _require(payload.startswith(b"V2 "), "unsupported runtime marker version")
            value = parse_json(payload[len(b"V2 "):])
            _loaded_format(value)
            if value.get("nonce") == nonce:
                _require(value.get("phase") == phase, "runtime marker phase binding differs")
                markers.append(value)
    _require(len(markers) <= 1, "duplicate runtime loading markers")
    return markers[0] if markers else None


def run_root(run_id):
    """Resolve only a fresh capability ID beneath this source worktree."""
    _require(_hex(run_id, 32), "invalid disposable run ID")
    return Path(__file__).absolute().parents[3] / "workspace/runtime/validation/mo2-bridge-capability" / run_id
