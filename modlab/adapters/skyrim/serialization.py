"""Strict machine-readable Skyrim discovery results."""

import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import Mapping

from modlab.recipes.model import CheckState

from .model import (
    DataFileEvidence,
    DiscoveryFinding,
    ExecutableEvidence,
    SkyrimDiscoveryReport,
)


class SkyrimDiscoveryFormatError(ValueError):
    """Raised when discovery evidence is ambiguous or unsafe."""


_REPORT_FIELDS = {
    "schemaVersion",
    "adapterId",
    "steamRoot",
    "manifestPath",
    "appId",
    "appName",
    "installDirectory",
    "installStateFlags",
    "gameRoot",
    "executable",
    "dataFiles",
    "creationClubPluginCount",
    "creationClubArchiveCount",
    "mo2Path",
    "findings",
}
_EXECUTABLE_FIELDS = {
    "relativePath",
    "fileVersion",
    "compatibilityRuntime",
    "sha256",
    "size",
}
_DATA_FILE_FIELDS = {"relativePath", "extension", "size"}
_FINDING_FIELDS = {"state", "code", "message"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_APP_ID = re.compile(r"^[0-9]{1,20}$")
_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){2,3}$")
_CODE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_DATA_EXTENSIONS = {".esm", ".esl", ".esp", ".bsa"}
_WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


def discovery_from_dict(data: object) -> SkyrimDiscoveryReport:
    mapping = _exact_mapping(data, _REPORT_FIELDS, "Skyrim discovery report")
    if type(mapping["schemaVersion"]) is not int or mapping["schemaVersion"] != 1:
        raise SkyrimDiscoveryFormatError("schemaVersion must be integer 1")
    if mapping["adapterId"] != "skyrim-steam":
        raise SkyrimDiscoveryFormatError("adapterId must be skyrim-steam")

    steam_root = _absolute_path(mapping["steamRoot"], "steamRoot")
    manifest_path = _absolute_path(mapping["manifestPath"], "manifestPath")
    expected_manifest = PureWindowsPath(
        steam_root, "steamapps", "appmanifest_489830.acf"
    )
    if PureWindowsPath(manifest_path) != expected_manifest:
        raise SkyrimDiscoveryFormatError(
            "manifestPath must be appmanifest_489830.acf under steamRoot/steamapps"
        )

    app_id = mapping["appId"]
    if app_id is not None and (
        not isinstance(app_id, str) or _APP_ID.fullmatch(app_id) is None
    ):
        raise SkyrimDiscoveryFormatError("appId must be null or numeric text")
    app_name = _optional_text(mapping["appName"], "appName")
    install_directory = _optional_install_directory(mapping["installDirectory"])
    state_flags = mapping["installStateFlags"]
    if state_flags is not None and (type(state_flags) is not int or state_flags < 0):
        raise SkyrimDiscoveryFormatError(
            "installStateFlags must be null or a non-negative integer"
        )

    game_root_value = mapping["gameRoot"]
    game_root = (
        None
        if game_root_value is None
        else _absolute_path(game_root_value, "gameRoot")
    )
    if install_directory is None:
        if game_root is not None:
            raise SkyrimDiscoveryFormatError(
                "gameRoot requires an observed installDirectory"
            )
    else:
        expected_game_root = PureWindowsPath(
            steam_root, "steamapps", "common", install_directory
        )
        if game_root is not None and PureWindowsPath(game_root) != expected_game_root:
            raise SkyrimDiscoveryFormatError(
                "gameRoot must match steamRoot/steamapps/common/installDirectory"
            )

    executable = _executable_from_dict(mapping["executable"])
    data_values = mapping["dataFiles"]
    if not isinstance(data_values, list):
        raise SkyrimDiscoveryFormatError("dataFiles must be an array")
    data_files = tuple(_data_file_from_dict(item) for item in data_values)
    paths = [item.relative_path.casefold() for item in data_files]
    if len(paths) != len(set(paths)):
        raise SkyrimDiscoveryFormatError("dataFiles contain a duplicate relativePath")
    if game_root is None and (executable is not None or data_files):
        raise SkyrimDiscoveryFormatError(
            "executable and dataFiles require an observed gameRoot"
        )

    plugin_count = _non_negative_int(
        mapping["creationClubPluginCount"], "creationClubPluginCount"
    )
    archive_count = _non_negative_int(
        mapping["creationClubArchiveCount"], "creationClubArchiveCount"
    )
    actual_plugin_count = sum(
        item.relative_path.rsplit("/", 1)[-1].casefold().startswith("cc")
        and item.extension in {".esm", ".esl", ".esp"}
        for item in data_files
    )
    actual_archive_count = sum(
        item.relative_path.rsplit("/", 1)[-1].casefold().startswith("cc")
        and item.extension == ".bsa"
        for item in data_files
    )
    if plugin_count != actual_plugin_count or archive_count != actual_archive_count:
        raise SkyrimDiscoveryFormatError(
            "Creation Club counts must match the observed cc-prefixed Data files"
        )

    mo2_value = mapping["mo2Path"]
    mo2_path = None if mo2_value is None else _absolute_path(mo2_value, "mo2Path")
    finding_values = mapping["findings"]
    if not isinstance(finding_values, list) or not finding_values:
        raise SkyrimDiscoveryFormatError("findings must be a non-empty array")
    findings = tuple(_finding_from_dict(item) for item in finding_values)
    codes = [item.code for item in findings]
    if len(codes) != len(set(codes)):
        raise SkyrimDiscoveryFormatError("findings contain a duplicate code")

    return SkyrimDiscoveryReport(
        schema_version=1,
        adapter_id="skyrim-steam",
        steam_root=steam_root,
        manifest_path=manifest_path,
        app_id=app_id,
        app_name=app_name,
        install_directory=install_directory,
        install_state_flags=state_flags,
        game_root=game_root,
        executable=executable,
        data_files=tuple(sorted(data_files, key=lambda item: item.relative_path.casefold())),
        creation_club_plugin_count=plugin_count,
        creation_club_archive_count=archive_count,
        mo2_path=mo2_path,
        findings=findings,
    )


def discovery_to_dict(report: SkyrimDiscoveryReport) -> dict[str, object]:
    return {
        "schemaVersion": report.schema_version,
        "adapterId": report.adapter_id,
        "steamRoot": report.steam_root,
        "manifestPath": report.manifest_path,
        "appId": report.app_id,
        "appName": report.app_name,
        "installDirectory": report.install_directory,
        "installStateFlags": report.install_state_flags,
        "gameRoot": report.game_root,
        "executable": (
            None
            if report.executable is None
            else {
                "relativePath": report.executable.relative_path,
                "fileVersion": report.executable.file_version,
                "compatibilityRuntime": report.executable.compatibility_runtime,
                "sha256": report.executable.sha256,
                "size": report.executable.size,
            }
        ),
        "dataFiles": [
            {
                "relativePath": item.relative_path,
                "extension": item.extension,
                "size": item.size,
            }
            for item in report.data_files
        ],
        "creationClubPluginCount": report.creation_club_plugin_count,
        "creationClubArchiveCount": report.creation_club_archive_count,
        "mo2Path": report.mo2_path,
        "findings": [
            {
                "state": item.state.value,
                "code": item.code,
                "message": item.message,
            }
            for item in report.findings
        ],
    }


def _executable_from_dict(value: object) -> ExecutableEvidence | None:
    if value is None:
        return None
    mapping = _exact_mapping(value, _EXECUTABLE_FIELDS, "executable")
    if mapping["relativePath"] != "SkyrimSE.exe":
        raise SkyrimDiscoveryFormatError(
            "executable.relativePath must be SkyrimSE.exe"
        )
    file_version = _optional_text(mapping["fileVersion"], "executable.fileVersion")
    if file_version is not None and _VERSION.fullmatch(file_version) is None:
        raise SkyrimDiscoveryFormatError(
            "executable.fileVersion must be a dotted numeric version"
        )
    compatibility_runtime = _optional_text(
        mapping["compatibilityRuntime"],
        "executable.compatibilityRuntime",
    )
    expected_runtime = normalize_compatibility_runtime(file_version)
    if compatibility_runtime != expected_runtime:
        raise SkyrimDiscoveryFormatError(
            "executable.compatibilityRuntime must match the normalized fileVersion"
        )
    sha256 = mapping["sha256"]
    if not isinstance(sha256, str) or _SHA256.fullmatch(sha256) is None:
        raise SkyrimDiscoveryFormatError(
            "executable.sha256 must be a lowercase SHA-256"
        )
    size = _non_negative_int(mapping["size"], "executable.size")
    return ExecutableEvidence(
        "SkyrimSE.exe",
        file_version,
        compatibility_runtime,
        sha256,
        size,
    )


def normalize_compatibility_runtime(file_version: str | None) -> str | None:
    if file_version is None:
        return None
    parts = file_version.split(".")
    if len(parts) == 4 and parts[-1] == "0":
        return ".".join(parts[:-1])
    return file_version


def _data_file_from_dict(value: object) -> DataFileEvidence:
    mapping = _exact_mapping(value, _DATA_FILE_FIELDS, "Data file")
    relative_path = mapping["relativePath"]
    if not isinstance(relative_path, str) or "\\" in relative_path:
        raise SkyrimDiscoveryFormatError(
            "Data relativePath must use safe forward slashes"
        )
    path = PurePosixPath(relative_path)
    if (
        path.is_absolute()
        or len(path.parts) != 2
        or path.parts[0] != "Data"
        or any(part in {"", ".", ".."} for part in relative_path.split("/"))
        or any(part.casefold() == "saves" for part in path.parts)
    ):
        raise SkyrimDiscoveryFormatError(
            "Data relativePath must name one top-level non-save file under Data"
        )
    extension = mapping["extension"]
    if (
        not isinstance(extension, str)
        or extension not in _DATA_EXTENSIONS
        or path.suffix.casefold() != extension
    ):
        raise SkyrimDiscoveryFormatError(
            "Data extension must match .esm, .esl, .esp, or .bsa"
        )
    size = _non_negative_int(mapping["size"], "Data file size")
    return DataFileEvidence(path.as_posix(), extension, size)


def _finding_from_dict(value: object) -> DiscoveryFinding:
    mapping = _exact_mapping(value, _FINDING_FIELDS, "discovery finding")
    try:
        state = CheckState(mapping["state"])
    except (TypeError, ValueError) as error:
        raise SkyrimDiscoveryFormatError(
            "finding.state must be Passed, Warning, Blocked, or Unknown"
        ) from error
    code = mapping["code"]
    if not isinstance(code, str) or _CODE.fullmatch(code) is None:
        raise SkyrimDiscoveryFormatError("finding.code must be a lowercase slug")
    message = mapping["message"]
    if not isinstance(message, str) or not message.strip():
        raise SkyrimDiscoveryFormatError("finding.message must be non-blank text")
    return DiscoveryFinding(state, code, message)


def _exact_mapping(
    value: object, expected_fields: set[str], label: str
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise SkyrimDiscoveryFormatError(f"{label} must be an object")
    fields = set(value)
    if fields != expected_fields:
        missing = sorted(expected_fields - fields)
        extra = sorted(str(field) for field in fields - expected_fields)
        raise SkyrimDiscoveryFormatError(
            f"{label} fields differ: missing={missing}, extra={extra}"
        )
    return value


def _absolute_path(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SkyrimDiscoveryFormatError(f"{field_name} must be an absolute path")
    path = PureWindowsPath(value)
    if not path.is_absolute() or not path.drive or ".." in path.parts:
        raise SkyrimDiscoveryFormatError(f"{field_name} must be a safe absolute path")
    return str(path)


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise SkyrimDiscoveryFormatError(f"{field_name} must be null or non-blank text")
    return value


def _optional_install_directory(value: object) -> str | None:
    if value is None:
        return None
    return validate_install_directory(value)


def validate_install_directory(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or value in {".", ".."}
        or value.endswith(".")
        or any(character in value for character in '<>:"/\\|?*')
        or any(ord(character) < 32 for character in value)
        or value.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_NAMES
    ):
        raise SkyrimDiscoveryFormatError(
            "installDirectory must be null or one safe directory name"
        )
    return value


def _non_negative_int(value: object, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise SkyrimDiscoveryFormatError(
            f"{field_name} must be a non-negative integer"
        )
    return value
