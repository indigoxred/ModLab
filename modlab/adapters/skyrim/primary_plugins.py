"""Strict observation of Skyrim's fixed primary plug-in policy."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path, PureWindowsPath
import stat

from modlab.adapters.mo2.readset import Mo2ReadSet


class SkyrimPrimaryPluginError(ValueError):
    """Raised when Skyrim's primary plug-in policy is unsafe or incomplete."""


CORE_PRIMARY_PLUGINS = (
    "Skyrim.esm",
    "Update.esm",
    "Dawnguard.esm",
    "HearthFires.esm",
    "Dragonborn.esm",
)

_PLUGIN_EXTENSIONS = {".esm", ".esl", ".esp"}


@dataclass(frozen=True)
class SkyrimPrimaryPluginEvidence:
    plugins: tuple[str, ...]
    ccc_path: str
    ccc_present: bool
    ccc_sha256: str | None
    ccc_size: int | None


def observe_skyrim_primary_plugins(
    game_root: Path,
    *,
    read_set: Mo2ReadSet | None = None,
) -> SkyrimPrimaryPluginEvidence:
    root = Path(game_root).expanduser().absolute()
    data_root = root / "Data"
    _require_direct_directory(root, "Skyrim game root")
    _require_direct_directory(data_root, "Skyrim Data directory")
    observer = read_set or Mo2ReadSet()

    for name in CORE_PRIMARY_PLUGINS:
        _require_plugin_file(data_root, name, observer)

    ccc_path = root / "Skyrim.ccc"
    try:
        ccc_data = observer.optional_bytes(ccc_path)
    except OSError as error:
        raise SkyrimPrimaryPluginError(
            f"Skyrim.ccc is not a direct regular file: {error}"
        ) from error

    creation_catalogue = () if ccc_data is None else _parse_ccc(ccc_data)
    complete_policy = CORE_PRIMARY_PLUGINS + creation_catalogue
    normalized = tuple(item.casefold() for item in complete_policy)
    if len(normalized) != len(set(normalized)):
        raise SkyrimPrimaryPluginError(
            "Skyrim.ccc contains a duplicate primary plug-in identity"
        )
    installed_creation_plugins = tuple(
        name
        for name in creation_catalogue
        if _observe_optional_plugin_file(data_root, name, observer)
    )

    return SkyrimPrimaryPluginEvidence(
        plugins=CORE_PRIMARY_PLUGINS + installed_creation_plugins,
        ccc_path=str(ccc_path),
        ccc_present=ccc_data is not None,
        ccc_sha256=(
            None if ccc_data is None else hashlib.sha256(ccc_data).hexdigest()
        ),
        ccc_size=None if ccc_data is None else len(ccc_data),
    )


def _parse_ccc(data: bytes) -> tuple[str, ...]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise SkyrimPrimaryPluginError("Skyrim.ccc must be valid UTF-8") from error
    plugins: list[str] = []
    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        plugins.append(_safe_plugin_name(line, number))
    normalized = tuple(item.casefold() for item in plugins)
    if len(normalized) != len(set(normalized)):
        raise SkyrimPrimaryPluginError("Skyrim.ccc contains a duplicate plug-in")
    return tuple(plugins)


def _safe_plugin_name(value: str, line_number: int) -> str:
    if (
        not value
        or value != value.strip()
        or value in {".", ".."}
        or any(character in value for character in "/\\")
        or any(ord(character) < 32 for character in value)
        or value.casefold() == "saves"
        or PureWindowsPath(value).suffix.casefold() not in _PLUGIN_EXTENSIONS
    ):
        raise SkyrimPrimaryPluginError(
            f"Skyrim.ccc line {line_number} must name one safe ESM, ESL, or ESP"
        )
    return value


def _require_plugin_file(
    data_root: Path, name: str, observer: Mo2ReadSet
) -> None:
    try:
        observer.read_bytes(data_root / name)
    except OSError as error:
        raise SkyrimPrimaryPluginError(
            f"required primary plug-in is missing or unsafe: {name}"
        ) from error


def _observe_optional_plugin_file(
    data_root: Path, name: str, observer: Mo2ReadSet
) -> bool:
    try:
        return observer.optional_bytes(data_root / name) is not None
    except OSError as error:
        raise SkyrimPrimaryPluginError(
            f"listed primary plug-in is present but unsafe: {name}"
        ) from error


def _require_direct_directory(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise SkyrimPrimaryPluginError(f"{label} is missing or unreadable") from error
    attributes = getattr(metadata, "st_file_attributes", 0)
    redirected = path.is_symlink() or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )
    if redirected or not stat.S_ISDIR(metadata.st_mode):
        raise SkyrimPrimaryPluginError(f"{label} must be one direct directory")
