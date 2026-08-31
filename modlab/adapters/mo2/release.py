"""Strict trust descriptor for the one supported portable MO2 release."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


class Mo2ReleaseFormatError(ValueError):
    """Raised when a release descriptor cannot be trusted."""


@dataclass(frozen=True)
class ReleaseFileIdentity:
    relative_path: str
    sha256: str
    size: int
    file_version: str | None = None


@dataclass(frozen=True)
class Mo2ReleaseDescriptor:
    schema_version: int
    release_id: str
    adapter_id: str
    product_version: str
    supported_platform: str
    archive_name: str
    archive_sha256: str
    archive_size: int
    archive_entry_count: int
    archive_listing_sha256: str
    package_file_count: int
    extracted_size: int
    minimum_free_bytes: int
    source_page_url: str
    source_asset_url: str
    executable: ReleaseFileIdentity
    sentinels: tuple[ReleaseFileIdentity, ...]
    mutable_package_paths: tuple[str, ...]
    allowed_extra_files: tuple[str, ...]
    allow_root_logs: bool
    allow_plugin_python_bytecode: bool


@dataclass(frozen=True)
class LoadedMo2Release:
    descriptor: Mo2ReleaseDescriptor
    path: Path
    data: bytes
    sha256: str


_FIELDS = {
    "adapterId",
    "allowedExtraFiles",
    "allowPluginPythonBytecode",
    "allowRootLogs",
    "archiveEntryCount",
    "archiveListingSha256",
    "archiveName",
    "archiveSha256",
    "archiveSize",
    "executable",
    "extractedSize",
    "minimumFreeBytes",
    "mutablePackagePaths",
    "packageFileCount",
    "productVersion",
    "releaseId",
    "schemaVersion",
    "sentinels",
    "sourceAssetUrl",
    "sourcePageUrl",
    "supportedPlatform",
}
_FILE_FIELDS = {"relativePath", "sha256", "size"}
_EXECUTABLE_FIELDS = _FILE_FIELDS | {"fileVersion"}
_FIXED_TEXT = {
    "releaseId": "mo2.windows-portable.2.5.2",
    "adapterId": "portable-mo2-skyrim",
    "productVersion": "2.5.2",
    "supportedPlatform": "windows-x86_64",
    "archiveName": "Mod.Organizer-2.5.2.7z",
    "archiveSha256": (
        "e6376efd87fd5ddd95aee959405e8f067afa526ea6c2c0c5aa03c5108bf4a815"
    ),
    "archiveListingSha256": (
        "c6eee6e0a9e80e3759c5bd75b701af4aa44d55859e71987f9b4bc1594057d0ed"
    ),
    "sourcePageUrl": (
        "https://github.com/ModOrganizer2/modorganizer/releases/tag/v2.5.2"
    ),
    "sourceAssetUrl": (
        "https://github.com/ModOrganizer2/modorganizer/releases/download/"
        "v2.5.2/Mod.Organizer-2.5.2.7z"
    ),
}
_FIXED_INTEGERS = {
    "archiveSize": 149660212,
    "archiveEntryCount": 1780,
    "packageFileCount": 1626,
    "extractedSize": 408536933,
    "minimumFreeBytes": 1073741824,
}
_EXPECTED_EXECUTABLE = ReleaseFileIdentity(
    relative_path="ModOrganizer.exe",
    sha256="442b354a8f34754da0048654c44d27f51628feba54ce46c3187cf58d6c43e622",
    size=5028352,
    file_version="2.5.2.0",
)
_EXPECTED_SENTINELS = (
    ReleaseFileIdentity(
        relative_path="loot/loot.dll",
        sha256="b21012f43e92ab5599b7be5d60550cc32806289a0a6bbb575c861b2d3d5a40cd",
        size=9333248,
    ),
    ReleaseFileIdentity(
        relative_path="plugins/game_skyrimse.dll",
        sha256="5eaace8ec5e3f1e6dc6e85ffe22abdd30c99dfa414807e2d7e2ef242cc90a429",
        size=440320,
    ),
    ReleaseFileIdentity(
        relative_path="usvfs_x64.dll",
        sha256="e2b766f418575021b9d350f384195ce6f23173169b37222cdef3d7fe5495f8b5",
        size=1854976,
    ),
)
_EXPECTED_ALLOWED_EXTRAS = (
    "categories.dat",
    "ModOrganizer.ini",
    "nexuscatmap.dat",
    "nxmhandler.ini",
    "nxmhandler.log",
)
_RESERVED_WINDOWS_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


def bundled_mo2_252_path() -> Path:
    return Path(__file__).resolve().parents[3] / "catalogue" / "tools" / "mo2-2.5.2.json"


def load_mo2_release(path: Path) -> LoadedMo2Release:
    source = Path(path)
    try:
        first = source.read_bytes()
    except OSError as error:
        raise Mo2ReleaseFormatError(f"cannot read release descriptor {source}: {error}") from error

    loaded = load_mo2_release_bytes(first, source)
    try:
        second = source.read_bytes()
    except OSError as error:
        raise Mo2ReleaseFormatError(f"cannot re-read release descriptor {source}: {error}") from error
    if second != first:
        raise Mo2ReleaseFormatError(f"release descriptor {source} changed while reading")
    return loaded


def load_mo2_release_bytes(data: bytes, path: Path) -> LoadedMo2Release:
    if type(data) is not bytes:
        raise Mo2ReleaseFormatError("release descriptor data must be bytes")
    source = Path(path)
    value = _parse_json(data, source)
    descriptor = _parse_descriptor(value)
    return LoadedMo2Release(
        descriptor=descriptor,
        path=source,
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _parse_json(data: bytes, path: Path) -> object:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise Mo2ReleaseFormatError(f"{path} is not valid UTF-8: {error}") from error
    try:
        return json.loads(
            text,
            object_pairs_hook=_without_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except json.JSONDecodeError as error:
        raise Mo2ReleaseFormatError(
            f"{path} is not valid JSON: line {error.lineno}, column {error.colno}"
        ) from error


def _without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise Mo2ReleaseFormatError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> object:
    raise Mo2ReleaseFormatError(f"non-finite JSON value is not allowed: {value}")


def _parse_descriptor(value: object) -> Mo2ReleaseDescriptor:
    data = _object(value, "release descriptor")
    _exact_fields(data, _FIELDS, "release descriptor")
    schema_version = _positive_integer(data["schemaVersion"], "schemaVersion")
    if schema_version != 1:
        raise Mo2ReleaseFormatError("schemaVersion must be integer 1")

    fixed_text = {
        key: _fixed_text(data[key], key, expected)
        for key, expected in _FIXED_TEXT.items()
    }
    fixed_integers = {
        key: _fixed_integer(data[key], key, expected)
        for key, expected in _FIXED_INTEGERS.items()
    }

    executable = _file_identity(data["executable"], "executable", with_version=True)
    if executable != _EXPECTED_EXECUTABLE:
        raise Mo2ReleaseFormatError("executable identity does not match supported MO2 2.5.2")

    sentinels_value = data["sentinels"]
    if not isinstance(sentinels_value, list):
        raise Mo2ReleaseFormatError("sentinels must be an array")
    sentinels = tuple(
        _file_identity(item, f"sentinels[{index}]", with_version=False)
        for index, item in enumerate(sentinels_value)
    )
    _require_sorted_unique_paths(
        tuple(item.relative_path for item in sentinels), "sentinels"
    )
    if sentinels != _EXPECTED_SENTINELS:
        raise Mo2ReleaseFormatError("sentinel identities do not match supported MO2 2.5.2")

    mutable_paths = _path_array(data["mutablePackagePaths"], "mutablePackagePaths")
    if mutable_paths:
        raise Mo2ReleaseFormatError("mutablePackagePaths must be empty for MO2 2.5.2")
    allowed_extras = _path_array(data["allowedExtraFiles"], "allowedExtraFiles")
    if allowed_extras != _EXPECTED_ALLOWED_EXTRAS:
        raise Mo2ReleaseFormatError("allowedExtraFiles do not match supported MO2 2.5.2")

    allow_root_logs = _boolean(data["allowRootLogs"], "allowRootLogs")
    allow_python_bytecode = _boolean(
        data["allowPluginPythonBytecode"], "allowPluginPythonBytecode"
    )
    if not allow_root_logs or not allow_python_bytecode:
        raise Mo2ReleaseFormatError("supported MO2 2.5.2 extra-file policy must be enabled")

    return Mo2ReleaseDescriptor(
        schema_version=schema_version,
        release_id=fixed_text["releaseId"],
        adapter_id=fixed_text["adapterId"],
        product_version=fixed_text["productVersion"],
        supported_platform=fixed_text["supportedPlatform"],
        archive_name=fixed_text["archiveName"],
        archive_sha256=fixed_text["archiveSha256"],
        archive_size=fixed_integers["archiveSize"],
        archive_entry_count=fixed_integers["archiveEntryCount"],
        archive_listing_sha256=fixed_text["archiveListingSha256"],
        package_file_count=fixed_integers["packageFileCount"],
        extracted_size=fixed_integers["extractedSize"],
        minimum_free_bytes=fixed_integers["minimumFreeBytes"],
        source_page_url=fixed_text["sourcePageUrl"],
        source_asset_url=fixed_text["sourceAssetUrl"],
        executable=executable,
        sentinels=sentinels,
        mutable_package_paths=mutable_paths,
        allowed_extra_files=allowed_extras,
        allow_root_logs=allow_root_logs,
        allow_plugin_python_bytecode=allow_python_bytecode,
    )


def _file_identity(value: object, label: str, *, with_version: bool) -> ReleaseFileIdentity:
    data = _object(value, label)
    _exact_fields(data, _EXECUTABLE_FIELDS if with_version else _FILE_FIELDS, label)
    file_version = None
    if with_version:
        file_version = _text(data["fileVersion"], f"{label}.fileVersion")
    return ReleaseFileIdentity(
        relative_path=_safe_relative_path(data["relativePath"], f"{label}.relativePath"),
        sha256=_sha256(data["sha256"], f"{label}.sha256"),
        size=_positive_integer(data["size"], f"{label}.size"),
        file_version=file_version,
    )


def _path_array(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise Mo2ReleaseFormatError(f"{label} must be an array")
    result = tuple(
        _safe_relative_path(item, f"{label}[{index}]")
        for index, item in enumerate(value)
    )
    _require_sorted_unique_paths(result, label)
    return result


def _require_sorted_unique_paths(paths: tuple[str, ...], label: str) -> None:
    folded = tuple(path.casefold() for path in paths)
    if len(folded) != len(set(folded)):
        raise Mo2ReleaseFormatError(f"{label} must not contain duplicate paths")
    if paths != tuple(sorted(paths, key=lambda item: (item.casefold(), item))):
        raise Mo2ReleaseFormatError(f"{label} must be sorted case-insensitively")


def _safe_relative_path(value: object, label: str) -> str:
    text = _text(value, label)
    windows = PureWindowsPath(text)
    parts = text.split("/")
    if (
        "\\" in text
        or text.startswith("/")
        or windows.is_absolute()
        or bool(windows.drive)
        or ":" in text
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise Mo2ReleaseFormatError(f"{label} must be a safe relative path")
    for part in parts:
        if any(ord(character) < 32 for character in part) or part.endswith((" ", ".")):
            raise Mo2ReleaseFormatError(f"{label} must be a safe relative path")
        stem = part.split(".", 1)[0].casefold()
        if stem in _RESERVED_WINDOWS_NAMES:
            raise Mo2ReleaseFormatError(f"{label} must be a safe relative path")
    path = PurePosixPath(text)
    if any(part.casefold() == "saves" for part in path.parts) or path.suffix.casefold() in {
        ".ess",
        ".skse",
    }:
        raise Mo2ReleaseFormatError(f"{label} must not reference a save or co-save")
    return path.as_posix()


def _object(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise Mo2ReleaseFormatError(f"{label} must be an object")
    return value


def _exact_fields(value: Mapping[str, object], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise Mo2ReleaseFormatError(
            f"{label} fields differ: missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise Mo2ReleaseFormatError(f"{label} must be non-blank unpadded text")
    return value


def _fixed_text(value: object, label: str, expected: str) -> str:
    text = _text(value, label)
    if text != expected:
        raise Mo2ReleaseFormatError(f"{label} must equal {expected}")
    return text


def _positive_integer(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise Mo2ReleaseFormatError(f"{label} must be a positive integer")
    return value


def _fixed_integer(value: object, label: str, expected: int) -> int:
    integer = _positive_integer(value, label)
    if integer != expected:
        raise Mo2ReleaseFormatError(f"{label} must equal {expected}")
    return integer


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise Mo2ReleaseFormatError(f"{label} must be boolean")
    return value


def _sha256(value: object, label: str) -> str:
    text = _text(value, label)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise Mo2ReleaseFormatError(f"{label} must be a lowercase SHA-256")
    return text
