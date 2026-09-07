"""Check what the native installer returned; never infer gameplay success."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class InstallResult:
    status: str
    name: str
    path: str
    file_count: int
    detail: str


def inspect_install_result(mod, mods_root: Path, active: bool) -> InstallResult:
    if mod is None:
        return InstallResult("Not installed", "", "", 0,
                             "MO2 reported cancellation or failure. Check its messages and any retained backup.")
    root = mods_root.resolve(strict=True)
    target = Path(mod.absolutePath()).resolve(strict=True)
    if target.parent != root or not target.is_dir():
        raise ValueError("Returned installation is outside the selected mods directory.")
    content = [p for p in target.rglob("*") if p.is_file() and p.relative_to(target).as_posix() != "meta.ini"]
    if not content:
        return InstallResult("Incomplete", mod.name(), str(target), 0,
                             "The installer returned a mod folder without content. Inspect the archive and installer choices.")
    return InstallResult(
        "Installed, enabled" if active else "Installed, disabled",
        mod.name(), str(target), len(content),
        "Installed files were found. Dependencies, effective assets and the intended in-game feature are not yet verified.",
    )
