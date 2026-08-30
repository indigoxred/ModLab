"""Read-only discovery of one explicit Skyrim Steam library."""

import hashlib
import os
from collections.abc import Callable
from pathlib import Path

from modlab.recipes.model import CheckState

from .model import (
    DataFileEvidence,
    DiscoveryFinding,
    ExecutableEvidence,
    SkyrimDiscoveryReport,
)
from .serialization import discovery_from_dict, discovery_to_dict
from .steam_manifest import SteamManifestError, parse_keyvalues
from .windows_version import read_windows_file_version


_APP_ID = "489830"
_APP_MANIFEST = f"appmanifest_{_APP_ID}.acf"
_DATA_EXTENSIONS = {".esm", ".esl", ".esp", ".bsa"}
_CHUNK_SIZE = 1024 * 1024


def discover_skyrim_steam(
    steam_root: Path,
    *,
    version_reader: Callable[[Path], str | None] = read_windows_file_version,
    mo2_path: Path | None = None,
) -> SkyrimDiscoveryReport:
    root = Path(steam_root).expanduser().resolve(strict=False)
    manifest_path = root / "steamapps" / _APP_MANIFEST
    findings: list[DiscoveryFinding] = []

    if not root.is_dir():
        findings.extend(
            (
                _finding(CheckState.BLOCKED, "steam-root-missing", f"Steam root is missing: {root}"),
                _finding(CheckState.UNKNOWN, "manifest-not-observed", "The Skyrim Steam manifest was not inspected."),
                _finding(CheckState.UNKNOWN, "install-state-not-observed", "Steam installation state was not observed."),
                _finding(CheckState.UNKNOWN, "game-root-not-observed", "The Skyrim game root was not observed."),
                _finding(CheckState.UNKNOWN, "executable-not-observed", "SkyrimSE.exe was not observed."),
                _finding(CheckState.UNKNOWN, "runtime-not-observed", "The executable runtime was not observed."),
                _finding(CheckState.UNKNOWN, "data-not-observed", "The Skyrim Data directory was not inspected."),
                _finding(CheckState.UNKNOWN, "anniversary-bundle-not-proven", "No claim about Anniversary content can be made."),
                _mo2_finding(mo2_path),
            )
        )
        return _report(
            root,
            manifest_path,
            findings=findings,
            mo2_path=mo2_path,
        )

    findings.append(
        _finding(CheckState.PASSED, "steam-root-observed", f"Steam root exists: {root}")
    )
    try:
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise SteamManifestError(f"manifest is missing or redirected: {manifest_path}")
        parsed = parse_keyvalues(manifest_path.read_text(encoding="utf-8-sig"))
        if set(parsed) != {"AppState"} or not isinstance(parsed["AppState"], dict):
            raise SteamManifestError("manifest must contain exactly one AppState object")
        app_state = parsed["AppState"]
    except (OSError, UnicodeError, SteamManifestError) as error:
        findings.extend(
            (
                _finding(CheckState.BLOCKED, "manifest-invalid", f"Skyrim manifest cannot be trusted: {error}"),
                _finding(CheckState.UNKNOWN, "install-state-not-observed", "Steam installation state was not observed."),
                _finding(CheckState.UNKNOWN, "game-root-not-observed", "The Skyrim game root was not observed."),
                _finding(CheckState.UNKNOWN, "executable-not-observed", "SkyrimSE.exe was not observed."),
                _finding(CheckState.UNKNOWN, "runtime-not-observed", "The executable runtime was not observed."),
                _finding(CheckState.UNKNOWN, "data-not-observed", "The Skyrim Data directory was not inspected."),
                _finding(CheckState.UNKNOWN, "anniversary-bundle-not-proven", "No claim about Anniversary content can be made."),
                _mo2_finding(mo2_path),
            )
        )
        return _report(root, manifest_path, findings=findings, mo2_path=mo2_path)

    findings.append(
        _finding(CheckState.PASSED, "manifest-observed", "The Skyrim Steam manifest parsed without ambiguity.")
    )
    app_id = _optional_manifest_text(app_state.get("appid"))
    app_name = _optional_manifest_text(app_state.get("name"))
    if app_id != _APP_ID:
        findings.extend(
            (
                _finding(CheckState.BLOCKED, "app-id-mismatch", f"Manifest app ID is {app_id or '(missing)'}, expected {_APP_ID}."),
                _finding(CheckState.UNKNOWN, "install-state-not-observed", "Installation state is not trusted for the wrong app ID."),
                _finding(CheckState.UNKNOWN, "game-root-not-observed", "The Skyrim game root was not followed."),
                _finding(CheckState.UNKNOWN, "executable-not-observed", "SkyrimSE.exe was not observed."),
                _finding(CheckState.UNKNOWN, "runtime-not-observed", "The executable runtime was not observed."),
                _finding(CheckState.UNKNOWN, "data-not-observed", "The Skyrim Data directory was not inspected."),
                _finding(CheckState.UNKNOWN, "anniversary-bundle-not-proven", "No claim about Anniversary content can be made."),
                _mo2_finding(mo2_path),
            )
        )
        return _report(
            root,
            manifest_path,
            app_id=app_id,
            app_name=app_name,
            findings=findings,
            mo2_path=mo2_path,
        )
    findings.append(
        _finding(CheckState.PASSED, "app-id-matched", "Manifest app ID matches Skyrim Special Edition (489830).")
    )

    state_flags = _manifest_integer(app_state.get("StateFlags"))
    if state_flags == 4:
        findings.append(
            _finding(CheckState.PASSED, "install-complete", "Steam reports installation StateFlags 4.")
        )
    elif state_flags is None:
        findings.append(
            _finding(CheckState.UNKNOWN, "install-state-not-observed", "Steam StateFlags is missing or invalid.")
        )
    else:
        findings.append(
            _finding(CheckState.WARNING, "install-state-unexpected", f"Steam reports StateFlags {state_flags}, not the completed value 4.")
        )

    install_directory = _safe_install_directory(app_state.get("installdir"))
    if install_directory is None:
        findings.extend(
            (
                _finding(CheckState.BLOCKED, "install-directory-invalid", "Manifest installdir is missing or unsafe."),
                _finding(CheckState.UNKNOWN, "game-root-not-observed", "The Skyrim game root was not followed."),
                _finding(CheckState.UNKNOWN, "executable-not-observed", "SkyrimSE.exe was not observed."),
                _finding(CheckState.UNKNOWN, "runtime-not-observed", "The executable runtime was not observed."),
                _finding(CheckState.UNKNOWN, "data-not-observed", "The Skyrim Data directory was not inspected."),
                _finding(CheckState.UNKNOWN, "anniversary-bundle-not-proven", "No claim about Anniversary content can be made."),
                _mo2_finding(mo2_path),
            )
        )
        return _report(
            root,
            manifest_path,
            app_id=app_id,
            app_name=app_name,
            state_flags=state_flags,
            findings=findings,
            mo2_path=mo2_path,
        )

    game_root = root / "steamapps" / "common" / install_directory
    if _redirected_or_missing_directory(game_root):
        findings.extend(
            (
                _finding(CheckState.BLOCKED, "game-root-invalid", f"Game root is missing or redirected: {game_root}"),
                _finding(CheckState.UNKNOWN, "executable-not-observed", "SkyrimSE.exe was not observed."),
                _finding(CheckState.UNKNOWN, "runtime-not-observed", "The executable runtime was not observed."),
                _finding(CheckState.UNKNOWN, "data-not-observed", "The Skyrim Data directory was not inspected."),
                _finding(CheckState.UNKNOWN, "anniversary-bundle-not-proven", "No claim about Anniversary content can be made."),
                _mo2_finding(mo2_path),
            )
        )
        return _report(
            root,
            manifest_path,
            app_id=app_id,
            app_name=app_name,
            install_directory=install_directory,
            state_flags=state_flags,
            findings=findings,
            mo2_path=mo2_path,
        )
    findings.append(
        _finding(CheckState.PASSED, "game-root-observed", f"Game root exists without redirection: {game_root}")
    )

    executable = _observe_executable(game_root / "SkyrimSE.exe", version_reader, findings)
    data_files = _observe_data(game_root / "Data", findings)
    cc_plugins = sum(
        item.relative_path.rsplit("/", 1)[-1].casefold().startswith("cc")
        and item.extension in {".esm", ".esl", ".esp"}
        for item in data_files
    )
    cc_archives = sum(
        item.relative_path.rsplit("/", 1)[-1].casefold().startswith("cc")
        and item.extension == ".bsa"
        for item in data_files
    )
    if cc_plugins or cc_archives:
        findings.append(
            _finding(
                CheckState.WARNING,
                "anniversary-bundle-not-proven",
                f"Observed {cc_plugins} cc-prefixed plugins and {cc_archives} archives; counts do not prove a complete Anniversary bundle.",
            )
        )
    else:
        findings.append(
            _finding(CheckState.UNKNOWN, "anniversary-bundle-not-proven", "No cc-prefixed Data files were observed; full Anniversary content is not proven.")
        )
    findings.append(_mo2_finding(mo2_path))
    normalized_mo2 = _normalized_mo2_path(mo2_path)
    return _report(
        root,
        manifest_path,
        app_id=app_id,
        app_name=app_name,
        install_directory=install_directory,
        state_flags=state_flags,
        game_root=game_root,
        executable=executable,
        data_files=data_files,
        cc_plugins=cc_plugins,
        cc_archives=cc_archives,
        mo2_path=normalized_mo2,
        findings=findings,
    )


def _observe_executable(
    path: Path,
    version_reader: Callable[[Path], str | None],
    findings: list[DiscoveryFinding],
) -> ExecutableEvidence | None:
    if path.is_symlink() or not path.is_file():
        findings.extend(
            (
                _finding(CheckState.BLOCKED, "executable-not-observed", f"SkyrimSE.exe is missing or redirected: {path}"),
                _finding(CheckState.UNKNOWN, "runtime-not-observed", "The executable runtime was not observed."),
            )
        )
        return None
    try:
        sha256, size = _hash_file(path)
    except OSError as error:
        findings.extend(
            (
                _finding(
                    CheckState.BLOCKED,
                    "executable-unreadable",
                    f"SkyrimSE.exe could not be identified: {error}",
                ),
                _finding(
                    CheckState.UNKNOWN,
                    "runtime-not-observed",
                    "The executable runtime was not observed.",
                ),
            )
        )
        return None
    findings.append(
        _finding(CheckState.PASSED, "executable-observed", "SkyrimSE.exe is a regular file and has a recorded SHA-256 identity.")
    )
    try:
        version = version_reader(path)
    except OSError as error:
        version = None
        message = f"SkyrimSE.exe version could not be read: {error}"
    else:
        message = (
            f"SkyrimSE.exe reports {version}."
            if version is not None
            else "SkyrimSE.exe did not expose a readable file version."
        )
    findings.append(
        _finding(
            CheckState.PASSED if version is not None else CheckState.UNKNOWN,
            "runtime-observed" if version is not None else "runtime-not-observed",
            message,
        )
    )
    return ExecutableEvidence("SkyrimSE.exe", version, sha256, size)


def _observe_data(
    data_root: Path,
    findings: list[DiscoveryFinding],
) -> tuple[DataFileEvidence, ...]:
    if _redirected_or_missing_directory(data_root):
        findings.append(
            _finding(CheckState.BLOCKED, "data-not-observed", f"Data directory is missing or redirected: {data_root}")
        )
        return ()
    observed: list[DataFileEvidence] = []
    skipped_redirects = 0
    try:
        children = tuple(data_root.iterdir())
    except OSError as error:
        findings.append(
            _finding(CheckState.BLOCKED, "data-not-observed", f"Data directory could not be listed: {error}")
        )
        return ()
    for path in children:
        extension = path.suffix.casefold()
        if extension not in _DATA_EXTENSIONS:
            continue
        if path.is_symlink() or not path.is_file():
            skipped_redirects += 1
            continue
        try:
            size = path.stat().st_size
        except OSError:
            skipped_redirects += 1
            continue
        observed.append(
            DataFileEvidence(f"Data/{path.name}", extension, size)
        )
    findings.append(
        _finding(CheckState.PASSED, "data-observed", f"Observed {len(observed)} top-level plugin/archive files; saves and co-saves were excluded.")
    )
    if skipped_redirects:
        findings.append(
            _finding(CheckState.WARNING, "data-files-skipped", f"Skipped {skipped_redirects} redirected or unreadable Data files.")
        )
    return tuple(sorted(observed, key=lambda item: item.relative_path.casefold()))


def _report(
    root: Path,
    manifest_path: Path,
    *,
    app_id: str | None = None,
    app_name: str | None = None,
    install_directory: str | None = None,
    state_flags: int | None = None,
    game_root: Path | None = None,
    executable: ExecutableEvidence | None = None,
    data_files: tuple[DataFileEvidence, ...] = (),
    cc_plugins: int = 0,
    cc_archives: int = 0,
    mo2_path: Path | str | None = None,
    findings: list[DiscoveryFinding],
) -> SkyrimDiscoveryReport:
    report = SkyrimDiscoveryReport(
        1,
        "skyrim-steam",
        str(root),
        str(manifest_path),
        app_id,
        app_name,
        install_directory,
        state_flags,
        str(game_root) if game_root is not None else None,
        executable,
        data_files,
        cc_plugins,
        cc_archives,
        (
            str(_normalized_mo2_path(Path(mo2_path)))
            if mo2_path is not None
            else None
        ),
        tuple(findings),
    )
    return discovery_from_dict(discovery_to_dict(report))


def _finding(state: CheckState, code: str, message: str) -> DiscoveryFinding:
    return DiscoveryFinding(state, code, message)


def _mo2_finding(path: Path | None) -> DiscoveryFinding:
    normalized = _normalized_mo2_path(path)
    if normalized is not None and normalized.name.casefold() == "modorganizer.exe" and normalized.is_file() and not normalized.is_symlink():
        return _finding(CheckState.PASSED, "mo2-observed", f"Configured ModOrganizer.exe exists: {normalized}")
    if path is None:
        return _finding(CheckState.UNKNOWN, "mo2-not-configured", "MO2 has not been configured; no manager was launched or substituted.")
    return _finding(CheckState.UNKNOWN, "mo2-not-observed", f"Configured MO2 path was not observed as a regular ModOrganizer.exe: {normalized}")


def _normalized_mo2_path(path: Path | None) -> Path | None:
    return None if path is None else Path(path).expanduser().resolve(strict=False)


def _safe_install_directory(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    if value in {".", ".."} or any(character in value for character in "\\/:"):
        return None
    return value


def _optional_manifest_text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _manifest_integer(value: object) -> int | None:
    if not isinstance(value, str) or not value.isdecimal():
        return None
    return int(value)


def _redirected_or_missing_directory(path: Path) -> bool:
    if path.is_symlink() or not path.is_dir():
        return True
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return True
    return os.path.normcase(str(resolved)) != os.path.normcase(str(path))


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size
