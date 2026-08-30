import hashlib
import tempfile
import unittest
from pathlib import Path

from modlab.adapters.mo2.profile import (
    Mo2ProfileError,
    inspect_profile,
    parse_load_order_bytes,
    parse_modlist_bytes,
    parse_plugins_bytes,
)


class Mo2ProfileTests(unittest.TestCase):
    def test_parses_generated_mod_plugin_and_load_order_files(self):
        mods = parse_modlist_bytes(
            b"\xef\xbb\xbf# generated\r\n*DLC: HearthFires\r\n+SkyUI\r\n-Old UI\r\n"
        )
        plugins = parse_plugins_bytes(
            b"# generated\r\n*Skyrim.esm\r\n*SkyUI_SE.esp\r\nOldPatch.esp\r\n"
        )
        load_order = parse_load_order_bytes(
            b"# generated\r\nSkyrim.esm\r\nSkyUI_SE.esp\r\n"
        )

        self.assertEqual(("*", "+", "-"), tuple(item.marker for item in mods))
        self.assertEqual((True, True, False), tuple(item.enabled for item in mods))
        self.assertEqual((True, True, False), tuple(item.enabled for item in plugins))
        self.assertEqual(("Skyrim.esm", "SkyUI_SE.esp"), load_order)

    def test_rejects_malformed_markers_duplicates_and_save_names(self):
        cases = (
            lambda: parse_modlist_bytes(b"SkyUI\n"),
            lambda: parse_modlist_bytes(b"+SkyUI\n-SkyUI\n"),
            lambda: parse_plugins_bytes(b"+SkyUI_SE.esp\n"),
            lambda: parse_plugins_bytes(b"*SkyUI_SE.esp\n*skyui_se.ESP\n"),
            lambda: parse_load_order_bytes(b"Saves/save.ess\n"),
            lambda: parse_load_order_bytes(b"SkyUI_SE.esp\nskyui_se.ESP\n"),
        )

        for index, call in enumerate(cases):
            with self.subTest(case=index):
                with self.assertRaises(Mo2ProfileError):
                    call()

    def test_inspects_only_known_state_files_without_reading_saves(self):
        with tempfile.TemporaryDirectory() as temp:
            profile = Path(temp) / "ModLab - Lab"
            profile.mkdir()
            modlist = b"# generated\r\n+SkyUI\r\n"
            (profile / "modlist.txt").write_bytes(modlist)
            (profile / "plugins.txt").write_bytes(b"*Skyrim.esm\r\n")
            (profile / "settings.ini").write_text(
                "[General]\nLocalSaves=false\n", encoding="utf-8"
            )
            saves = profile / "saves"
            saves.mkdir()
            unreadable_save = saves / "do-not-read.ess"
            unreadable_save.write_bytes(b"secret save bytes")

            evidence = inspect_profile(profile, Path(temp))

            self.assertFalse(evidence.profile_local_saves)
            self.assertEqual(("SkyUI",), tuple(item.name for item in evidence.mods))
            self.assertEqual([], list(evidence.load_order))
            self.assertEqual(
                hashlib.sha256(modlist).hexdigest(),
                next(
                    item.sha256
                    for item in evidence.state_files
                    if item.relative_path.endswith("modlist.txt")
                ),
            )
            self.assertTrue(
                all("saves" not in item.relative_path.casefold() for item in evidence.state_files)
            )

    def test_missing_required_modlist_and_escaped_profile_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile = root / "ModLab - Lab"
            profile.mkdir()

            with self.assertRaisesRegex(Mo2ProfileError, "modlist"):
                inspect_profile(profile, root)

            outside = root.parent / f"{root.name}-outside"
            outside.mkdir(exist_ok=True)
            try:
                (outside / "modlist.txt").write_bytes(b"+SkyUI\n")
                with self.assertRaisesRegex(Mo2ProfileError, "profiles root"):
                    inspect_profile(outside, root)
            finally:
                (outside / "modlist.txt").unlink(missing_ok=True)
                outside.rmdir()


if __name__ == "__main__":
    unittest.main()
