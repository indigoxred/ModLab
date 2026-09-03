"""Pure, bounded admission for declared preparation operations, never MO2 runtime.

Policy 1 deliberately reserves headroom for bsdtar's ordinary -C argument (247
UTF-16 units). Other declared preparation consumers have a separately tested
512-unit bound. Neither number purports to be the universal Windows path limit.
No aliases, extended prefixes, output-name rewriting, or filesystem effects.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
from pathlib import PureWindowsPath
import re

from modlab.platform.windows_exact_fs import publication_candidate_path

POLICY_VERSION = 1
SCOPE = "mo2-preparation-only"
LIMITS = {"tar-cwd": 247, "preparation-wide": 512}
COMPONENT_LIMIT = 255
MAX_PATHS = 100000
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\.|$)", re.I)


class PathBudgetError(ValueError):
    """The exact declared operation cannot be admitted before mutation."""


@dataclass(frozen=True, order=True)
class PlannedPath:
    stage: str
    consumer: str
    path: str


@dataclass(frozen=True)
class PathBudget:
    paths: tuple[PlannedPath, ...]

    @property
    def scope(self) -> str:
        return SCOPE

    @property
    def runtime_qualified(self) -> bool:
        return False

    @property
    def sha256(self) -> str:
        return hashlib.sha256(json.dumps(budget_to_dict(self), sort_keys=True,
            separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def utf16_units(value: str) -> int:
    try:
        return len(value.encode("utf-16-le")) // 2
    except UnicodeError as error:
        raise PathBudgetError("path budget: unpaired Unicode surrogate") from error


def _ordinary(value: str, stage: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not re.match(r"^[A-Za-z]:\\", value):
        raise PathBudgetError(f"path budget: {stage}: ordinary absolute local Windows path required: {value!r}")
    parts = value[3:].split("\\") if value[3:] else []
    for part in parts:
        if (not part or part in {".", ".."} or part[-1] in " ." or
                any(ord(char) < 32 or char in '<>:"/|?*' for char in part) or
                _RESERVED.match(part) or re.search(r"~[0-9]+(?:\.|$)", part)):
            raise PathBudgetError(f"path budget: {stage}: unsupported component {part!r}: {value}")
        if utf16_units(part) > COMPONENT_LIMIT:
            raise PathBudgetError(f"path budget: {stage}: component {part!r} uses {utf16_units(part)} > {COMPONENT_LIMIT} UTF-16 units: {value}")
    return tuple(parts)


def admit_paths(paths, *, bounded: bool = True) -> PathBudget:
    if bounded is not True:
        raise PathBudgetError("path budget: unknown or unbounded output pattern is unsupported")
    selected = []
    for item in paths:
        if len(selected) >= MAX_PATHS:
            raise PathBudgetError("path budget: declared path count exceeds bounded policy")
        if not isinstance(item, PlannedPath) or not item.stage or item.consumer not in LIMITS:
            raise PathBudgetError("path budget: unknown stage or consumer")
        parts = _ordinary(item.path, item.stage)
        units = utf16_units(item.path)
        if units > LIMITS[item.consumer]:
            component = max(parts, key=utf16_units, default="")
            raise PathBudgetError(f"path budget: stage={item.stage}; consumer={item.consumer}; "
                f"path={item.path}; units={units}; limit={LIMITS[item.consumer]}; "
                f"limiting component={component!r} ({utf16_units(component)} units)")
        selected.append(item)
    if not selected:
        raise PathBudgetError("path budget: an empty operation is not admitted")
    return PathBudget(tuple(sorted(set(selected))))


def budget_to_dict(value: PathBudget) -> dict:
    if not isinstance(value, PathBudget):
        raise PathBudgetError("path budget: missing preparation admission")
    admitted = admit_paths(value.paths)
    return {"policyVersion": POLICY_VERSION, "scope": SCOPE, "runtimeQualified": False,
            "limits": dict(LIMITS), "componentLimit": COMPONENT_LIMIT,
            "paths": [[row.stage, row.consumer, row.path] for row in admitted.paths]}


def budget_from_dict(value: object) -> PathBudget:
    keys = {"policyVersion", "scope", "runtimeQualified", "limits", "componentLimit", "paths"}
    if (type(value) is not dict or set(value) != keys or type(value["policyVersion"]) is not int
            or value["policyVersion"] != POLICY_VERSION or value["scope"] != SCOPE
            or value["runtimeQualified"] is not False or value["limits"] != LIMITS
            or type(value["limits"]) is not dict or any(type(n) is not int for n in value["limits"].values())
            or type(value["componentLimit"]) is not int or value["componentLimit"] != COMPONENT_LIMIT
            or type(value["paths"]) is not list):
        raise PathBudgetError("path budget: unsupported or malformed policy")
    rows = value["paths"]
    if any(type(row) is not list or len(row) != 3 or any(type(x) is not str for x in row) for row in rows):
        raise PathBudgetError("path budget: malformed path row")
    result = admit_paths(PlannedPath(*row) for row in rows)
    if budget_to_dict(result) != value:
        raise PathBudgetError("path budget: noncanonical declared paths")
    return result


def stage_name(job_id: str, layout_version: int) -> str:
    if type(layout_version) is not int or layout_version not in (1, 2):
        raise PathBudgetError("path budget: unsupported bootstrap layout version")
    if not isinstance(job_id, str) or not re.fullmatch(r"bootstrap-job:[0-9a-f]{32}", job_id):
        raise PathBudgetError("path budget: invalid complete bootstrap job identity")
    identity = job_id.split(":")[1]
    if layout_version == 1:
        return ".skyrim-se-ae.modlab-stage-" + identity
    # Lossless 128-bit encoding, not a truncated hash or a filesystem alias.
    return ".s" + base64.b32encode(bytes.fromhex(identity)).decode("ascii").lower().rstrip("=")


def publication_paths(path, stage: str):
    target = PureWindowsPath(path)
    # Windows PID and threading.get_ident() are DWORD identifiers: <=10 digits.
    candidate = publication_candidate_path(
        target,
        0xFFFFFFFF,
        0xFFFFFFFF,
        "f" * 16,
    )
    return (PlannedPath(stage, "preparation-wide", str(target)),
            PlannedPath(stage + ":publication-candidate", "preparation-wide", str(candidate)))


def bootstrap_staging_part_path(jobs_root, label: str, token: str) -> PureWindowsPath:
    """Return the exact bootstrap store staging name for one bounded token."""
    if not isinstance(label, str) or not label or not re.fullmatch(r"[a-z0-9-]+", label):
        raise PathBudgetError("path budget: invalid bootstrap staging label")
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}", token):
        raise PathBudgetError("path budget: invalid bootstrap staging token")
    return PureWindowsPath(jobs_root) / f"{label}-{token}.part"


def archive_paths(listing, destination, stage="archive-extraction"):
    root = PureWindowsPath(destination)
    rows = [PlannedPath(stage, "tar-cwd", str(root))]
    entries = getattr(listing, "entries", None)
    canonical_sha256 = getattr(listing, "canonical_sha256", None)
    if type(entries) is not tuple or not isinstance(canonical_sha256, str):
        raise PathBudgetError("path budget: archive listing identity is unavailable")
    canonical_names = []
    identities = set()
    for entry in entries:
        kind = getattr(entry, "kind", None)
        relative = getattr(entry, "relative_path", None)
        if kind not in {"file", "directory"}:
            raise PathBudgetError("path budget: archive listing identity has an unbounded entry kind")
        if (not isinstance(relative, str) or relative.startswith("/") or "\\" in relative
                or any(part in {"", ".", ".."} for part in relative.split("/"))):
            raise PathBudgetError("path budget: archive listing identity has a malformed relative path")
        identity = relative.casefold()
        if identity in identities:
            raise PathBudgetError("path budget: archive listing identity has a collision")
        identities.add(identity)
        canonical_names.append(relative + ("/" if kind == "directory" else ""))
        rows.append(PlannedPath(stage + ":member", "preparation-wide", str(root.joinpath(*relative.split("/")))))
    try:
        canonical = ("\n".join(canonical_names) + "\n").encode("utf-8")
    except UnicodeError as error:
        raise PathBudgetError("path budget: archive listing identity has invalid Unicode") from error
    if (not entries or not re.fullmatch(r"[0-9a-f]{64}", canonical_sha256)
            or hashlib.sha256(canonical).hexdigest() != canonical_sha256):
        raise PathBudgetError("path budget: archive listing identity is inconsistent")
    return tuple(rows)


def bootstrap_paths(workspace, listing, *, job_id="bootstrap-job:" + "0" * 32,
                    layout_version=2, disposition="Create", archive_path=None):
    root = PureWindowsPath(workspace)
    final = root / "tools/mo2/skyrim-se-ae"
    stage = final.parent / stage_name(job_id, layout_version)
    job = root / "runtime/jobs/mo2-bootstrap" / job_id.split(":")[1]
    rows = list(archive_paths(listing, stage / "app" if disposition == "Create" else stage))
    if archive_path is not None:
        rows.append(PlannedPath("bootstrap-archive-input", "preparation-wide", str(PureWindowsPath(archive_path))))
    # Every tree destination, including the 7B exact-object recovery destinations.
    destinations = (stage, final, job / "prior", job / "recovered-stage", job / "recovered-activated")
    generated = ["app/portable.txt", "app/ModOrganizer.ini", "downloads", "mods", "overwrite", "webcache", "logs"]
    for profile in ("ModLab - Lab", "ModLab - Play"):
        generated.extend(f"profiles/{profile}/{name}" for name in (
            "modlist.txt", "plugins.txt", "lockedorder.txt", "archives.txt", "loadorder.txt", "settings.ini", "Skyrim.ini", "SkyrimPrefs.ini", "SkyrimCustom.ini"))
    for destination in destinations:
        label = "bootstrap-tree:" + destination.name
        rows.append(PlannedPath(label, "preparation-wide", str(destination)))
        for entry in listing.entries:
            relative = entry.relative_path if disposition == "Adopt" and destination in (stage, job / "recovered-stage") else "app/" + entry.relative_path
            rows.append(PlannedPath(label, "preparation-wide", str(destination / relative)))
        rows.extend(PlannedPath(label, "preparation-wide", str(destination / relative)) for relative in generated)
    documents = (job / "journal.json", root / "runtime/jobs/mo2-bootstrap/plans" / ("f" * 64 + ".json"),
                 root / "games/skyrim-se-ae/tool-installations/mo2" / ("f" * 64 + ".json"))
    for document in documents:
        rows.extend(publication_paths(document, "bootstrap-record"))
    for label in ("plan", "receipt", "journal-" + job_id.split(":")[1]):
        rows.append(PlannedPath(
            "bootstrap-mutable-part",
            "preparation-wide",
            str(bootstrap_staging_part_path(job.parent, label, "f" * 32)),
        ))
    return tuple(rows)
