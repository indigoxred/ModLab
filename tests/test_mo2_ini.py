import unittest

from modlab.adapters.mo2.ini import (
    Mo2IniError,
    decode_qsettings_path,
    parse_ini_bytes,
    parse_qsettings_bool,
)


class Mo2IniTests(unittest.TestCase):
    def test_parses_utf8_bom_sections_and_qsettings_values(self):
        document = parse_ini_bytes(
            (
                "\ufeff[General]\r\n"
                "gameName=Skyrim Special Edition\r\n"
                "gamePath=@ByteArray(C:\\\\Steam\\\\Skyrim Special Edition)\r\n"
                "selected_profile=ModLab - Lab\r\n"
                "[Settings]\r\n"
                "base_directory=C:/ModLab/workspace/tools/mo2/skyrim-se-ae\r\n"
            ).encode("utf-8")
        )

        self.assertEqual(
            "Skyrim Special Edition", document.get("General", "gameName")
        )
        self.assertEqual("ModLab - Lab", document.get("general", "SELECTED_PROFILE"))
        self.assertEqual(
            "C:\\Steam\\Skyrim Special Edition",
            decode_qsettings_path(document.get("General", "gamePath")),
        )

    def test_rejects_case_insensitive_duplicate_keys_and_malformed_lines(self):
        cases = (
            b"[General]\ngamePath=C:/One\nGAMEPATH=C:/Two\n",
            b"[General]\nnot a key value\n",
            b"key=value\n[General\n",
            b"[General]\n=missing\n",
        )

        for index, data in enumerate(cases):
            with self.subTest(case=index):
                with self.assertRaises(Mo2IniError):
                    parse_ini_bytes(data)

    def test_parses_only_explicit_qsettings_boolean_values(self):
        for value in ("true", "1", "yes"):
            with self.subTest(value=value):
                self.assertTrue(parse_qsettings_bool(value))
        for value in ("false", "0", "no"):
            with self.subTest(value=value):
                self.assertFalse(parse_qsettings_bool(value))

        with self.assertRaises(Mo2IniError):
            parse_qsettings_bool("enabled")

    def test_rejects_unsupported_qsettings_wrappers_and_broken_escapes(self):
        for value in ("@Variant(data)", "@ByteArray(C:\\broken\\)"):
            with self.subTest(value=value):
                with self.assertRaises(Mo2IniError):
                    decode_qsettings_path(value)


if __name__ == "__main__":
    unittest.main()
