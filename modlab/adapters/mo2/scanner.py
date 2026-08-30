"""Read-only inspection of a contained Skyrim portable MO2 instance."""

import hashlib
import os
from collections.abc import Callable
from pathlib import Path, PureWindowsPath

from modlab.adapters.skyrim.windows_version import read_windows_file_version
from modlab.recipes.model import CheckState

from .ini import Mo2IniError, decode_qsettings_path, parse_ini_bytes
from .model import (
    Mo2ExecutableEvidence,
    Mo2Finding,
    Mo2InspectionReport,
    Mo2PathEvidence,
    Mo2ProfileEvidence,
)
from .profile import Mo2ProfileError, inspect_profile
from .serialization import report_from_dict, report_to_dict


_CHUNK_SIZE = 1024 * 1024
_PATH_KEYS = {
    "downloads": "download_directory",
    "mods": "mod_directory",
    "profiles": "profiles_directory",
    "overwrite": "overwrite_directory",
}
_LAB_PROFILE = "ModLab - Lab"
_PLAY_PROFILE = "ModLab - Play"


def inspect_skyrim_mo2(
    mo2_root: Path,
    game_root: Path,
    *,
    workspace_root: Path,
    version_reader: Callable[[Path], str | None] = read_windows_file_version,
) -> Mo2InspectionReport:
    requested_root = Path(mo2_root).expanduser().resolve(strict=False)
    requested_game = Path(game_root).expanduser().resolve(strict=False)
    workspace = Path(workspace_root).expanduser().resolve(strict=False)
    findings: list[Mo2Finding] = []

    if not requested_root.exists():
        findings.append(
            _finding(
                CheckState.UNKNOWN,
                "mo2-root-not-configured",
                f"Portable Skyrim MO2 is not configured: {requested_root}",
            )
        )
        return _report(
            requested_root,
            requested_root,
            requested_game,
            findings=findings,
        )

    try:
        resolved_root = requested_root.resolve(strict=True)
        resolved_root.relative_to(workspace.resolve(strict=True))
    except (FileNotFoundError, OSError, ValueError) as error:
        findings.append(
            _finding(
                CheckState.BLOCKED,
                "mo2-root-outside-workspace",
                f"Portable MO2 root is not safely contained by ModLab: {error}",
            )
        )
        return _report(
            requested_root,
            requested_root,
            requested_game,
            findings=findings,
        )
    if not resolved_root.is_dir():
        findings.append(
            _finding(CheckState.BLOCKED, "mo2-root-invalid", "MO2 root is not a directory.")
        )
        return _report(
            requested_root,
            resolved_root,
            requested_game,
            findings=findings,
        )
    findings.append(
        _finding(CheckState.PASSED, "mo2-root-contained", "MO2 root is contained by ModLab.")
    )

    executable = _observe_executable(resolved_root, version_reader, findings)
    config_path = resolved_root / "ModOrganizer.ini"
    if config_path.is_symlink() or not config_path.is_file():
        findings.append(
            _finding(
                CheckState.UNKNOWN,
                "portable-config-not-configured",
                "ModOrganizer.ini is not present as a regular file in the portable app folder.",
            )
        )
        return _report(
            requested_root,
            resolved_root,
            requested_game,
            executable=executable,
            findings=findings,
        )

    try:
        config = parse_ini_bytes(config_path.read_bytes())
    except (OSError, Mo2IniError) as error:
        findings.append(
            _finding(
                CheckState.BLOCKED,
                "portable-config-invalid",
                f"ModOrganizer.ini cannot be trusted: {error}",
            )
        )
        return _report(
            requested_root,
            resolved_root,
            requested_game,
            executable=executable,
            portable_config_present=True,
            findings=findings,
        )
    findings.append(
        _finding(CheckState.PASSED, "portable-config-observed", "Portable ModOrganizer.ini parsed without ambiguity.")
    )

    configured_game = _configured_absolute_path(
        config.get("General", "gamePath"), "gamePath", findings
    )
    if configured_game is None:
        findings.append(
            _finding(CheckState.BLOCKED, "game-path-not-configured", "MO2 gamePath is missing or unsafe.")
        )
    elif _same_path(configured_game, requested_game):
        findings.append(
            _finding(CheckState.PASSED, "game-path-matched", "MO2 gamePath matches the inspected Skyrim installation.")
        )
    else:
        findings.append(
            _finding(
                CheckState.BLOCKED,
                "game-path-mismatch",
                f"MO2 targets {configured_game}, not {requested_game}.",
            )
        )

    instance_root = resolved_root.parent
    paths = _observe_paths(config, instance_root, workspace, findings)
    path_by_kind = {item.kind: item for item in paths}
    profiles: tuple[Mo2ProfileEvidence, ...] = ()
    top_level_mods: tuple[str, ...] = ()
    overwrite_entries: tuple[str, ...] = ()

    profiles_path = _usable_path(path_by_kind.get("profiles"))
    if profiles_path is not None:
        profiles = _observe_profiles(profiles_path, findings)
    else:
        findings.append(
            _finding(CheckState.BLOCKED, "lab-play-incomplete", "The contained profiles directory is unavailable.")
        )

    mods_path = _usable_path(path_by_kind.get("mods"))
    if mods_path is not None:
        top_level_mods = _observe_safe_children(
            mods_path, "mods", findings, directories_only=True
        )

    overwrite_path = _usable_path(path_by_kind.get("overwrite"))
    if overwrite_path is not None:
        overwrite_entries = _observe_safe_children(
            overwrite_path, "overwrite", findings
        )
        findings.append(
            _finding(
                CheckState.PASSED if not overwrite_entries else CheckState.WARNING,
                "overwrite-empty" if not overwrite_entries else "overwrite-not-empty",
                (
                    "MO2 overwrite is empty."
                    if not overwrite_entries
                    else f"MO2 overwrite has {len(overwrite_entries)} top-level entries that require review."
                ),
            )
        )

    selected_profile = config.get("General", "selected_profile")
    active_profile = None
    if selected_profile is not None and any(
        item.name.casefold() == selected_profile.casefold() for item in profiles
    ):
        active_profile = next(
            item.name
            for item in profiles
            if item.name.casefold() == selected_profile.casefold()
        )
        findings.append(
            _finding(CheckState.PASSED, "active-profile-observed", f"Active profile is {active_profile}.")
        )
    else:
        findings.append(
            _finding(CheckState.WARNING, "active-profile-unmanaged", "The active profile is missing or is not a ModLab Lab/Play profile.")
        )

    return _report(
        requested_root,
        resolved_root,
        requested_game,
        executable=executable,
        portable_config_present=True,
        configured_game_path=configured_game,
        paths=paths,
        active_profile=active_profile,
        profiles=profiles,
        top_level_mods=top_level_mods,
        overwrite_entries=overwrite_entries,
        findings=findings,
    )


def _observe_executable(
    root: Path,
    version_reader: Callable[[Path], str | None],
    findings: list[Mo2Finding],
) -> Mo2ExecutableEvidence | None:
    path = root / "ModOrganizer.exe"
    if path.is_symlink() or not path.is_file():
        findings.append(
            _finding(CheckState.UNKNOWN, "mo2-executable-not-observed", "ModOrganizer.exe is missing or redirected.")
        )
        return None
    try:
        sha256, size = _hash_file(path)
        version = version_reader(path)
    except OSError as error:
        findings.append(
            _finding(CheckState.BLOCKED, "mo2-executable-unreadable", f"ModOrganizer.exe could not be identified: {error}")
        )
        return None
    findings.append(
        _finding(CheckState.PASSED, "mo2-executable-observed", "ModOrganizer.exe has a recorded SHA-256 identity.")
    )
    findings.append(
        _finding(
            CheckState.PASSED if version is not None else CheckState.UNKNOWN,
            "mo2-version-observed" if version is not None else "mo2-version-not-observed",
            f"ModOrganizer.exe reports {version}." if version is not None else "ModOrganizer.exe did not expose a readable file version.",
        )
    )
    return Mo2ExecutableEvidence("ModOrganizer.exe", version, sha256, size)


def _observe_paths(config, instance_root: Path, workspace: Path, findings: list[Mo2Finding]) -> tuple[Mo2PathEvidence, ...]:
    observed: list[Mo2PathEvidence] = []
    base_value = _decode_observed_path(config.get("Settings", "base_directory"))
    base_path = _resolve_configured_path(base_value, None)
    if base_value is None or base_path is None:
        findings.append(
            _finding(CheckState.BLOCKED, "paths-incomplete", "MO2 base_directory is missing or unsafe.")
        )
        return ()

    base_contained = _contained(base_path, instance_root, workspace)
    observed.append(Mo2PathEvidence("base", base_value, str(base_path), base_contained))
    missing: list[str] = []
    for kind, key in _PATH_KEYS.items():
        configured = _decode_observed_path(config.get("Settings", key))
        resolved = _resolve_configured_path(configured, base_path)
        if configured is None or resolved is None:
            missing.append(kind)
            continue
        observed.append(
            Mo2PathEvidence(
                kind,
                configured,
                str(resolved),
                _contained(resolved, instance_root, workspace),
            )
        )

    if missing:
        findings.append(
            _finding(CheckState.BLOCKED, "paths-incomplete", f"MO2 paths are missing or unsafe: {', '.join(missing)}.")
        )
    if any(not item.contained for item in observed):
        findings.append(
            _finding(CheckState.BLOCKED, "paths-escaped", "One or more writable MO2 paths escape the ModLab Skyrim instance.")
        )
    elif not missing:
        findings.append(
            _finding(CheckState.PASSED, "paths-contained", "All configured writable MO2 paths remain under the ModLab Skyrim instance.")
        )
    return tuple(observed)


def _observe_profiles(root: Path, findings: list[Mo2Finding]) -> tuple[Mo2ProfileEvidence, ...]:
    profiles: list[Mo2ProfileEvidence] = []
    errors: list[str] = []
    for name in (_LAB_PROFILE, _PLAY_PROFILE):
        try:
            profiles.append(inspect_profile(root / name, root))
        except Mo2ProfileError as error:
            errors.append(f"{name}: {error}")
    if errors:
        findings.append(
            _finding(CheckState.BLOCKED, "lab-play-incomplete", "; ".join(errors))
        )
    else:
        findings.append(
            _finding(CheckState.PASSED, "lab-play-ready", "Both exact ModLab Lab and Play profiles were inspected.")
        )
    if profiles and any(item.profile_local_saves is True for item in profiles):
        findings.append(
            _finding(CheckState.WARNING, "profile-local-saves-enabled", "At least one ModLab profile has local saves enabled; save contents remain excluded.")
        )
    elif profiles and all(item.profile_local_saves is False for item in profiles):
        findings.append(
            _finding(CheckState.PASSED, "profile-local-saves-disabled", "Profile-local saves are disabled for both ModLab profiles.")
        )
    else:
        findings.append(
            _finding(CheckState.UNKNOWN, "profile-local-saves-unknown", "Profile-local save settings were not fully observed.")
        )
    return tuple(profiles)


def _observe_safe_children(
    root: Path,
    label: str,
    findings: list[Mo2Finding],
    *,
    directories_only: bool = False,
) -> tuple[str, ...]:
    names: list[str] = []
    skipped = 0
    try:
        children = tuple(root.iterdir())
    except OSError as error:
        findings.append(
            _finding(CheckState.BLOCKED, f"{label}-unreadable", f"{label} could not be listed: {error}")
        )
        return ()
    for child in children:
        if directories_only and not child.is_dir():
            continue
        if child.is_symlink() or not _safe_entry_name(child.name):
            skipped += 1
            continue
        try:
            child.resolve(strict=True).relative_to(root.resolve(strict=True))
        except (FileNotFoundError, OSError, ValueError):
            skipped += 1
            continue
        names.append(child.name)
    if skipped:
        findings.append(
            _finding(CheckState.BLOCKED, f"{label}-entries-skipped", f"Skipped {skipped} redirected or save-like {label} entries.")
        )
    return tuple(sorted(names, key=str.casefold))


def _report(
    requested_root: Path,
    resolved_root: Path,
    game_root: Path,
    *,
    executable: Mo2ExecutableEvidence | None = None,
    portable_config_present: bool = False,
    configured_game_path: Path | None = None,
    paths: tuple[Mo2PathEvidence, ...] = (),
    active_profile: str | None = None,
    profiles: tuple[Mo2ProfileEvidence, ...] = (),
    top_level_mods: tuple[str, ...] = (),
    overwrite_entries: tuple[str, ...] = (),
    findings: list[Mo2Finding],
) -> Mo2InspectionReport:
    report = Mo2InspectionReport(
        schema_version=1,
        adapter_id="portable-mo2-skyrim",
        requested_root=str(requested_root),
        resolved_root=str(resolved_root),
        game_root=str(game_root),
        executable=executable,
        portable_config_present=portable_config_present,
        configured_game_path=(
            None if configured_game_path is None else str(configured_game_path)
        ),
        paths=paths,
        active_profile=active_profile,
        profiles=profiles,
        top_level_mods=top_level_mods,
        overwrite_entries=overwrite_entries,
        findings=tuple(findings),
        actions=(),
        downloads=(),
        installations=(),
        program_launches=(),
    )
    return report_from_dict(report_to_dict(report))


def _configured_absolute_path(value: str | None, label: str, findings: list[Mo2Finding]) -> Path | None:
    try:
        decoded = decode_qsettings_path(value)
    except Mo2IniError as error:
        findings.append(_finding(CheckState.BLOCKED, f"{label.casefold()}-invalid", str(error)))
        return None
    return _resolve_configured_path(decoded, None)


def _decode_observed_path(value: str | None) -> str | None:
    try:
        return decode_qsettings_path(value)
    except Mo2IniError:
        return None


def _resolve_configured_path(value: str | None, base: Path | None) -> Path | None:
    if value is None or not value.strip():
        return None
    expanded = value
    token = "%BASE_DIR%"
    if value.casefold().startswith(token.casefold()):
        if base is None:
            return None
        remainder = value[len(token) :]
        if remainder and remainder[0] not in {"/", "\\"}:
            return None
        expanded = str(base) + remainder
    elif "%" in value:
        return None
    windows = PureWindowsPath(expanded)
    if not windows.is_absolute() or ".." in windows.parts:
        return None
    return Path(str(windows)).resolve(strict=False)


def _contained(path: Path, instance_root: Path, workspace: Path) -> bool:
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(instance_root.resolve(strict=True))
        resolved.relative_to(workspace.resolve(strict=True))
    except (FileNotFoundError, OSError, ValueError):
        return False
    return True


def _usable_path(evidence: Mo2PathEvidence | None) -> Path | None:
    if evidence is None or not evidence.contained:
        return None
    path = Path(evidence.resolved_path)
    return path if path.is_dir() else None


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve(strict=False))) == os.path.normcase(
        str(right.resolve(strict=False))
    )


def _safe_entry_name(name: str) -> bool:
    return (
        bool(name)
        and name not in {".", ".."}
        and not any(character in name for character in "/\\")
        and not any(ord(character) < 32 for character in name)
        and name.casefold() != "saves"
        and PureWindowsPath(name).suffix.casefold() not in {".ess", ".skse"}
    )


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _finding(state: CheckState, code: str, message: str) -> Mo2Finding:
    return Mo2Finding(state, code, message)
