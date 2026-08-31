import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SkyrimWorkflowFixture:
    root: Path
    workspace: Path
    steam_root: Path
    game_root: Path
    mo2_root: Path
    recipe_path: Path
    environment_path: Path


_CORE_PLUGINS = (
    "Skyrim.esm",
    "Update.esm",
    "Dawnguard.esm",
    "HearthFires.esm",
    "Dragonborn.esm",
)


def create_skyrim_workflow_fixture(root: Path) -> SkyrimWorkflowFixture:
    root = Path(root)
    workspace = root / "ModLab" / "workspace"
    steam_root = root / "Steam"
    steamapps = steam_root / "steamapps"
    game_root = steamapps / "common" / "Skyrim Special Edition"
    data_root = game_root / "Data"
    mo2_instance = workspace / "tools" / "mo2" / "skyrim-se-ae"
    mo2_root = mo2_instance / "app"
    downloads = mo2_instance / "downloads"
    mods = mo2_instance / "mods"
    profiles = mo2_instance / "profiles"
    overwrite = mo2_instance / "overwrite"
    for directory in (
        data_root,
        mo2_root,
        downloads,
        mods,
        profiles,
        overwrite,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    (steamapps / "appmanifest_489830.acf").write_text(
        '"AppState"\n'
        "{\n"
        '    "appid" "489830"\n'
        '    "Universe" "1"\n'
        '    "name" "The Elder Scrolls V: Skyrim Special Edition"\n'
        '    "StateFlags" "4"\n'
        '    "installdir" "Skyrim Special Edition"\n'
        "}\n",
        encoding="utf-8",
    )
    (game_root / "SkyrimSE.exe").write_bytes(b"fixture SkyrimSE executable")
    for plugin in _CORE_PLUGINS:
        (data_root / plugin).write_bytes(f"fixture {plugin}".encode("utf-8"))
    (game_root / "Skyrim.ccc").write_bytes(b"")

    (mo2_root / "ModOrganizer.exe").write_bytes(
        b"fixture ModOrganizer executable"
    )
    (mo2_root / "ModOrganizer.ini").write_text(
        "[General]\n"
        "gameName=Skyrim Special Edition\n"
        f"gamePath={game_root.as_posix()}\n"
        "selected_profile=@ByteArray(ModLab - Lab)\n"
        "[Settings]\n"
        f"base_directory={mo2_instance.as_posix()}\n"
        "download_directory=%BASE_DIR%/downloads\n"
        "mod_directory=%BASE_DIR%/mods\n"
        "profiles_directory=%BASE_DIR%/profiles\n"
        "overwrite_directory=%BASE_DIR%/overwrite\n",
        encoding="utf-8",
    )
    load_order = "".join(f"{plugin}\n" for plugin in _CORE_PLUGINS)
    for profile_name in ("ModLab - Lab", "ModLab - Play"):
        profile = profiles / profile_name
        profile.mkdir()
        (profile / "modlist.txt").write_text("# primary-only\n", encoding="utf-8")
        (profile / "plugins.txt").write_text("# primary-only\n", encoding="utf-8")
        (profile / "loadorder.txt").write_text(load_order, encoding="utf-8")
        (profile / "settings.ini").write_text(
            "[General]\nLocalSaves=false\nLocalSettings=true\n",
            encoding="utf-8",
        )
    (profiles / "ModLab - Lab" / "archives.txt").write_text(
        "Skyrim - Misc.bsa\n", encoding="utf-8"
    )
    (profiles / "ModLab - Play" / "archives.txt").write_text(
        "", encoding="utf-8"
    )

    intent = root / "intent"
    intent.mkdir()
    recipe_path = intent / "skyrim-foundation.json"
    recipe_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "recipeId": "skyrim.fixture.foundation",
                "revision": "2026.08.31.1",
                "displayName": "Fixture Skyrim Foundation",
                "maturity": "Draft",
                "target": {
                    "game": "Skyrim Special Edition",
                    "edition": "SE/AE",
                    "distribution": "Steam",
                    "engineLane": "Creation Engine 64-bit",
                    "adapter": "MO2",
                },
                "researchedAt": "2026-08-31",
                "components": [
                    {
                        "componentId": "foundation-core",
                        "displayName": "Foundation Core",
                        "importance": "Required",
                        "defaultSelected": True,
                        "role": "Permanent Core",
                        "deployment": "Data/VFS",
                        "requires": [],
                        "incompatibleWith": [],
                        "constraints": [
                            {
                                "dimension": "executableRuntime",
                                "allowedValues": ["1.7.104"],
                                "reason": "Fixture runtime lane.",
                            },
                            {
                                "dimension": "adapterVersion",
                                "allowedValues": ["2.5.2"],
                                "reason": "Fixture manager lane.",
                            },
                            {
                                "dimension": "scriptExtender",
                                "allowedValues": ["2.2.6"],
                                "reason": "Planned payload, not live evidence.",
                            },
                        ],
                        "rationale": "Provides one review-ready fixture component.",
                        "sources": ["https://example.invalid/foundation-core"],
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    environment_path = intent / "skyrim-target-environment.json"
    environment_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "environmentId": "skyrim.fixture.steam",
                "dimensions": {
                    "adapterVersion": "2.5.2",
                    "executableRuntime": "1.7.104",
                    "scriptExtender": "2.2.6",
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return SkyrimWorkflowFixture(
        root=root,
        workspace=workspace,
        steam_root=steam_root,
        game_root=game_root,
        mo2_root=mo2_root,
        recipe_path=recipe_path,
        environment_path=environment_path,
    )
