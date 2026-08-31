"""Read-only parsing of the fixed state files in one MO2 profile."""

import hashlib
import locale
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath

from modlab.adapters.skyrim.primary_plugins import (
    SkyrimPrimaryPluginError,
    validate_skyrim_plugin_name,
)

from .ini import Mo2IniError, parse_ini_bytes, parse_qsettings_bool
from .model import (
    Mo2ModEntry,
    Mo2PluginEntry,
    Mo2ProfileEvidence,
    Mo2StateFileEvidence,
)
from .readset import Mo2ReadSet


class Mo2ProfileError(ValueError):
    """Raised when a profile state file is missing, unsafe, or ambiguous."""


_FIXED_STATE_FILES = (
    "archives.txt",
    "initweaks.ini",
    "loadorder.txt",
    "lockedorder.txt",
    "modlist.txt",
    "plugins.txt",
    "settings.ini",
    "Skyrim.ini",
    "SkyrimCustom.ini",
    "SkyrimPrefs.ini",
)


def parse_modlist_bytes(data: bytes) -> tuple[Mo2ModEntry, ...]:
    entries: list[Mo2ModEntry] = []
    for number, line in _content_lines(
        data, label="modlist.txt", encoding="utf-8-sig"
    ):
        marker = line[0]
        if marker not in {"+", "-", "*"}:
            raise Mo2ProfileError(f"invalid mod marker on line {number}")
        name = _safe_name(line[1:].strip(), f"mod on line {number}")
        entries.append(Mo2ModEntry(name, marker, marker in {"+", "*"}))
    _reject_duplicates((item.name for item in entries), "modlist")
    return tuple(entries)


def parse_plugins_bytes(
    data: bytes, *, encoding: str | None = None
) -> tuple[Mo2PluginEntry, ...]:
    selected_encoding = encoding or locale.getencoding()
    entries: list[Mo2PluginEntry] = []
    for number, line in _content_lines(
        data, label="plugins.txt", encoding=selected_encoding
    ):
        if line[0] in {"+", "-"}:
            raise Mo2ProfileError(f"invalid plugin marker on line {number}")
        enabled = line.startswith("*")
        name = line[1:].strip() if enabled else line
        entries.append(
            Mo2PluginEntry(
                _safe_plugin_name(name, f"plugin on line {number}"),
                enabled,
            )
        )
    _reject_duplicates((item.name for item in entries), "plugins")
    return tuple(entries)


def parse_load_order_bytes(data: bytes) -> tuple[str, ...]:
    entries = tuple(
        _safe_plugin_name(line, f"load order entry on line {number}")
        for number, line in _content_lines(
            data, label="loadorder.txt", encoding="utf-8-sig"
        )
    )
    _reject_duplicates(entries, "load order")
    return entries


def parse_profile_settings_bytes(data: bytes) -> tuple[bool | None, bool | None]:
    try:
        settings = parse_ini_bytes(data)
        return (
            parse_qsettings_bool(settings.get("General", "LocalSaves")),
            parse_qsettings_bool(settings.get("General", "LocalSettings")),
        )
    except Mo2IniError as error:
        raise Mo2ProfileError(f"invalid profile settings.ini: {error}") from error


def inspect_profile(
    profile_root: Path,
    profiles_root: Path,
    read_set: Mo2ReadSet | None = None,
) -> Mo2ProfileEvidence:
    requested_profile = Path(profile_root)
    requested_profiles = Path(profiles_root)
    try:
        resolved_profiles = requested_profiles.resolve(strict=True)
        resolved_profile = requested_profile.resolve(strict=True)
        resolved_profile.relative_to(resolved_profiles)
    except (FileNotFoundError, OSError, ValueError) as error:
        raise Mo2ProfileError("profile must resolve beneath the profiles root") from error
    if not resolved_profile.is_dir() or resolved_profile.parent != resolved_profiles:
        raise Mo2ProfileError("profile must be one directory under the profiles root")

    name = _safe_name(resolved_profile.name, "profile name")
    observer = read_set or Mo2ReadSet()
    try:
        entries = {
            entry.name.casefold(): entry
            for entry in observer.list_directory(requested_profile)
        }
    except OSError as error:
        raise Mo2ProfileError(f"profile directory cannot be observed: {error}") from error
    observed: dict[str, bytes] = {}
    for filename in _FIXED_STATE_FILES:
        entry = entries.get(filename.casefold())
        if entry is None:
            continue
        if entry.redirected or entry.kind != "file":
            raise Mo2ProfileError(
                f"profile state path is not a direct file: {filename}"
            )
        candidate = requested_profile / entry.name
        try:
            observed[filename] = observer.read_bytes(candidate)
        except OSError as error:
            raise Mo2ProfileError(
                f"profile state file cannot be observed: {filename}"
            ) from error

    if "modlist.txt" not in observed:
        raise Mo2ProfileError("required profile modlist.txt is missing")

    mods = parse_modlist_bytes(observed["modlist.txt"])
    plugins = (
        ()
        if "plugins.txt" not in observed
        else parse_plugins_bytes(observed["plugins.txt"])
    )
    load_order = (
        ()
        if "loadorder.txt" not in observed
        else parse_load_order_bytes(observed["loadorder.txt"])
    )
    local_saves = None
    local_settings = None
    if "settings.ini" in observed:
        local_saves, local_settings = parse_profile_settings_bytes(
            observed["settings.ini"]
        )

    state_files = tuple(
        Mo2StateFileEvidence(
            relative_path=PurePosixPath("profiles", name, filename).as_posix(),
            sha256=hashlib.sha256(data).hexdigest(),
            size=len(data),
        )
        for filename, data in sorted(observed.items())
    )
    return Mo2ProfileEvidence(
        name=name,
        relative_path=PurePosixPath("profiles", name).as_posix(),
        profile_local_saves=local_saves,
        state_files=state_files,
        mods=mods,
        plugins=plugins,
        load_order=load_order,
        profile_local_settings=local_settings,
    )


def _content_lines(
    data: bytes, *, label: str, encoding: str
) -> tuple[tuple[int, str], ...]:
    try:
        text = data.decode(encoding)
    except (LookupError, UnicodeDecodeError) as error:
        raise Mo2ProfileError(f"{label} cannot be decoded as {encoding}") from error
    lines: list[tuple[int, str]] = []
    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if line and not line.startswith("#"):
            lines.append((number, line))
    return tuple(lines)


def _safe_plugin_name(value: str, label: str) -> str:
    try:
        return validate_skyrim_plugin_name(value, label=label)
    except SkyrimPrimaryPluginError as error:
        raise Mo2ProfileError(str(error)) from error


def _safe_name(value: str, label: str) -> str:
    if (
        not value
        or value != value.strip()
        or value in {".", ".."}
        or any(character in value for character in "/\\")
        or any(ord(character) < 32 for character in value)
        or value.casefold() == "saves"
        or PureWindowsPath(value).suffix.casefold() in {".ess", ".skse"}
    ):
        raise Mo2ProfileError(f"{label} must be one safe non-save name")
    return value


def _reject_duplicates(values, label: str) -> None:
    normalized = [value.casefold() for value in values]
    if len(normalized) != len(set(normalized)):
        raise Mo2ProfileError(f"{label} contains a duplicate name")
