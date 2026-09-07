"""Create a fresh, disposable MO2 host for the ModLab product test. Never launch it."""

import argparse
import configparser
import json
from pathlib import Path
import shutil
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-app", type=Path, required=True)
    parser.add_argument("--source-profile", type=Path, required=True)
    parser.add_argument("--game", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    source = args.source_app.resolve(strict=True)
    destination = args.destination.resolve()
    game = args.game.resolve(strict=True)
    if destination.exists():
        parser.error("Destination already exists; choose a fresh disposable directory.")
    if destination == source or source in destination.parents or game in destination.parents:
        parser.error("Disposable destination must be outside the source application and game.")
    if not (source / "ModOrganizer.exe").is_file() or not (game / "SkyrimSE.exe").is_file():
        parser.error("Expected MO2 and Skyrim executables were not found.")
    app = destination / "app"
    shutil.copytree(source, app, ignore=shutil.ignore_patterns(
        "logs", "crashDumps", "__pycache__", "*.log", "*.dmp",
    ))
    for name in ("mods", "downloads", "overwrite", "reports"):
        (destination / name).mkdir()
    profile = destination / "profiles" / "ModLab Test"
    profile.mkdir(parents=True)
    for name in ("archives.txt", "initweaks.ini", "loadorder.txt", "lockedorder.txt",
                 "modlist.txt", "plugins.txt", "skyrim.ini", "skyrimcustom.ini", "skyrimprefs.ini"):
        path = args.source_profile / name
        if path.is_file():
            shutil.copy2(path, profile / name)
    (profile / "settings.ini").write_text(
        "[General]\nLocalSaves=true\nLocalSettings=true\nAutomaticArchiveInvalidation=true\n",
        encoding="utf-8",
    )
    settings = configparser.ConfigParser(interpolation=None, strict=False)
    settings.optionxform = str
    settings.read(app / "ModOrganizer.ini", encoding="utf-8-sig")
    settings["General"]["selected_profile"] = "@ByteArray(ModLab Test)"
    settings["General"]["gamePath"] = f"@ByteArray({game.as_posix()})"
    settings["Settings"]["base_directory"] = destination.as_posix()
    for key, directory in (("mod_directory", "mods"), ("download_directory", "downloads"),
                           ("profiles_directory", "profiles"), ("overwrite_directory", "overwrite")):
        settings["Settings"][key] = (destination / directory).as_posix()
    settings["Settings"]["profile_local_saves"] = "true"
    # Keep the test host from offering unrelated configured external programs.
    settings["customExecutables"] = {"size": "0"}
    with (app / "ModOrganizer.ini").open("w", encoding="utf-8") as stream:
        settings.write(stream, space_around_delimiters=False)
    (app / "portable.txt").touch()
    bundle = Path(__file__).resolve().parents[1] / "modlab" / "resources" / "mo2_hub"
    shutil.copytree(bundle, app / "plugins" / "modlab_hub", ignore=shutil.ignore_patterns("__pycache__"))
    with zipfile.ZipFile(destination / "downloads" / "ModLab Test Archive.zip", "w") as archive:
        archive.writestr("meshes/modlab-test-marker.txt", "Harmless ModLab installation test.\n")
        archive.writestr("readme.txt", "Test fixture only; this does not add a gameplay feature.\n")
    print(json.dumps({"application": str(app / "ModOrganizer.exe"), "profile": str(profile),
                      "mods": str(destination / "mods"), "game": str(game)}, indent=2))


if __name__ == "__main__":
    main()
