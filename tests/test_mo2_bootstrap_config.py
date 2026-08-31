import hashlib
import json
import tempfile
import unittest
from pathlib import Path, PurePosixPath

from modlab.adapters.mo2.bootstrap_config import (
    LIST_HEADER,
    PROFILE_SETTINGS,
    Mo2BootstrapConfigError,
    classify_mo2_target,
    observe_profile_seed,
    render_modorganizer_ini,
    render_profile_files,
    write_staged_configuration,
)
from modlab.adapters.mo2.ini import decode_qsettings_path, parse_ini_bytes
from modlab.adapters.mo2.readset import Mo2ReadSet
from modlab.adapters.mo2.release import bundled_mo2_252_path, load_mo2_release
from modlab.adapters.mo2.projection import Mo2Readiness, project_mo2_state
from modlab.adapters.mo2.scanner import inspect_skyrim_mo2
from modlab.workspace import initialize_workspace, workspace_layout
from tests.support.mo2_bootstrap import (
    files_for_profile,
    make_primary_policy_fixture,
)


class Mo2BootstrapConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.workspace = root / "ModLab" / "workspace"
        self.game_root = root / "Steam" / "steamapps" / "common" / "Skyrim Special Edition"
        make_primary_policy_fixture(
            self.game_root,
            ccc_plugins=("ccBGSSSE001-Fish.esm",),
        )
        self.documents_root = root / "Documents"
        self.game_ini_root = (
            self.documents_root / "My Games" / "Skyrim Special Edition"
        )
        self.game_ini_root.mkdir(parents=True)
        (self.game_ini_root / "Skyrim.ini").write_bytes(b"[General]\r\nseed=exact\r\n")
        (self.game_ini_root / "SkyrimPrefs.ini").write_bytes(
            b"[Display]\nquality=fixture\n"
        )
        self.release = load_mo2_release(bundled_mo2_252_path()).descriptor

    def test_absent_target_and_workspace_init_skeleton_are_empty_without_writes(self):
        layout = workspace_layout(self.workspace)
        absent_before = self.workspace.exists()

        absent = classify_mo2_target(layout)

        self.assertEqual("Empty", absent.kind)
        self.assertEqual(0, absent.entry_count)
        self.assertEqual(absent_before, self.workspace.exists())

        layout = initialize_workspace(self.workspace)
        before = self._file_bytes(layout.root)

        skeleton = classify_mo2_target(layout)

        self.assertEqual("Empty", skeleton.kind)
        self.assertEqual(5, skeleton.entry_count)
        self.assertEqual(before, self._file_bytes(layout.root))

    def test_unknown_target_file_is_blocked_and_existing_executable_is_bounded(self):
        layout = initialize_workspace(self.workspace)
        unknown = layout.skyrim_mo2 / "unknown.txt"
        unknown.write_bytes(b"mine")

        blocked = classify_mo2_target(layout)

        self.assertEqual("Blocked", blocked.kind)
        unknown.unlink()
        executable = layout.skyrim_mo2_app / "ModOrganizer.exe"
        executable.write_bytes(b"fixture organizer")

        existing = classify_mo2_target(layout)

        self.assertEqual("Existing", existing.kind)
        self.assertGreater(existing.entry_count, 5)
        self.assertEqual(64, len(existing.inventory_sha256))

    def test_rendered_profiles_are_equal_exact_and_never_include_saves(self):
        saves = self.game_ini_root / "Saves"
        saves.mkdir()
        (saves / "NeverRead.ess").write_bytes(b"save")
        (self.game_ini_root / "Unrelated.ini").write_bytes(b"unrelated")
        seed = observe_profile_seed(
            self.game_root,
            self.documents_root,
            read_set=Mo2ReadSet(),
        )

        files = render_profile_files(seed)

        self.assertEqual(
            files_for_profile(files, "ModLab - Lab"),
            files_for_profile(files, "ModLab - Play"),
        )
        lab = PurePosixPath("profiles/ModLab - Lab")
        self.assertEqual(PROFILE_SETTINGS, files[lab / "settings.ini"])
        for name in ("modlist.txt", "plugins.txt", "lockedorder.txt"):
            self.assertEqual(LIST_HEADER, files[lab / name])
        self.assertEqual(b"", files[lab / "archives.txt"])
        self.assertEqual(
            LIST_HEADER
            + (
                "Skyrim.esm\nUpdate.esm\nDawnguard.esm\n"
                "HearthFires.esm\nDragonborn.esm\n"
                "ccBGSSSE001-Fish.esm\n"
            ).encode("utf-8"),
            files[lab / "loadorder.txt"],
        )
        self.assertEqual(
            b"[General]\r\nseed=exact\r\n", files[lab / "Skyrim.ini"]
        )
        self.assertEqual(b"", files[lab / "SkyrimCustom.ini"])
        self.assertNotIn(
            "saves", "\n".join(path.as_posix() for path in files).casefold()
        )
        self.assertEqual(
            ("Skyrim.ini", "SkyrimPrefs.ini", "SkyrimCustom.ini"),
            tuple(Path(item.path).name for item in seed.ini_sources),
        )

    def test_nonregular_ini_and_source_drift_block(self):
        (self.game_ini_root / "SkyrimCustom.ini").mkdir()
        with self.assertRaisesRegex(Mo2BootstrapConfigError, "SkyrimCustom.ini"):
            observe_profile_seed(
                self.game_root,
                self.documents_root,
                read_set=Mo2ReadSet(),
            )
        (self.game_ini_root / "SkyrimCustom.ini").rmdir()
        seed = observe_profile_seed(
            self.game_root,
            self.documents_root,
            read_set=Mo2ReadSet(),
        )
        (self.game_ini_root / "Skyrim.ini").write_bytes(b"changed")

        with self.assertRaisesRegex(Mo2BootstrapConfigError, "changed"):
            render_profile_files(seed)

    def test_reusing_observation_read_set_cannot_hide_source_drift(self):
        observer = Mo2ReadSet()
        seed = observe_profile_seed(
            self.game_root,
            self.documents_root,
            read_set=observer,
        )
        (self.game_ini_root / "Skyrim.ini").write_bytes(b"changed")

        with self.assertRaisesRegex(Mo2BootstrapConfigError, "changed"):
            render_profile_files(seed, read_set=observer)

    def test_modorganizer_ini_is_minimal_deterministic_and_strictly_readable(self):
        layout = workspace_layout(self.workspace)

        first = render_modorganizer_ini(layout, self.game_root, self.release)
        second = render_modorganizer_ini(layout, self.game_root, self.release)
        document = parse_ini_bytes(first)

        self.assertEqual(first, second)
        self.assertEqual("false", document.get("General", "first_start"))
        self.assertEqual("Steam", document.get("General", "game_edition"))
        self.assertEqual("2.5.2", document.get("General", "version"))
        self.assertEqual(
            str(self.game_root),
            decode_qsettings_path(document.get("General", "gamePath")),
        )
        self.assertEqual(
            str(layout.skyrim_mo2),
            str(Path(decode_qsettings_path(document.get("Settings", "base_directory")))),
        )
        escaped_base = str(layout.skyrim_mo2).replace("\\", "\\\\")
        self.assertIn(f"base_directory={escaped_base}\n".encode("utf-8"), first)
        self.assertNotIn(b"base_directory=@ByteArray(", first)
        self.assertIsNone(document.get("Settings", "download_directory"))
        self.assertEqual(
            {
                "first_start",
                "game_edition",
                "gamename",
                "gamepath",
                "selected_profile",
                "version",
            },
            set(document.sections["general"]),
        )
        self.assertEqual(
            {
                "base_directory",
                "profile_archive_invalidation",
                "profile_local_inis",
                "profile_local_saves",
            },
            set(document.sections["settings"]),
        )

    def test_staged_configuration_uses_exclusive_writes_and_returns_delta(self):
        layout = workspace_layout(self.workspace)
        seed = observe_profile_seed(
            self.game_root,
            self.documents_root,
            read_set=Mo2ReadSet(),
        )
        manager_ini = render_modorganizer_ini(layout, self.game_root, self.release)
        profiles = render_profile_files(seed)
        stage = Path(self.temporary.name, "stage")
        (stage / "app").mkdir(parents=True)

        inventory = write_staged_configuration(stage, manager_ini, profiles)

        self.assertEqual(19, inventory.file_count)
        self.assertEqual(manager_ini, (stage / "app" / "ModOrganizer.ini").read_bytes())
        canonical = json.dumps(
            [
                [item.relative_path, item.sha256, item.size]
                for item in inventory.files
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.assertEqual(hashlib.sha256(canonical).hexdigest(), inventory.sha256)
        with self.assertRaisesRegex(Mo2BootstrapConfigError, "already exists"):
            write_staged_configuration(stage, manager_ini, profiles)

    def test_generated_contained_instance_projects_ready(self):
        layout = workspace_layout(self.workspace)
        layout.skyrim_mo2_app.mkdir(parents=True)
        for path in (
            layout.skyrim_mo2_downloads,
            layout.skyrim_mo2_mods,
            layout.skyrim_mo2_overwrite,
        ):
            path.mkdir()
        (layout.skyrim_mo2_app / "ModOrganizer.exe").write_bytes(b"fixture MO2")
        seed = observe_profile_seed(
            self.game_root,
            self.documents_root,
            read_set=Mo2ReadSet(),
        )
        write_staged_configuration(
            layout.skyrim_mo2,
            render_modorganizer_ini(layout, self.game_root, self.release),
            render_profile_files(seed),
        )

        report = inspect_skyrim_mo2(
            layout.skyrim_mo2_app,
            self.game_root,
            workspace_root=layout.root,
            version_reader=lambda _: "2.5.2.0",
        )

        self.assertEqual(Mo2Readiness.READY, project_mo2_state(report).readiness)

    @staticmethod
    def _file_bytes(root: Path):
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }


if __name__ == "__main__":
    unittest.main()
