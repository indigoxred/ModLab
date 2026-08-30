import unittest

from modlab.adapters.skyrim.steam_manifest import (
    SteamManifestError,
    parse_keyvalues,
)


VALID_MANIFEST = r'''
"AppState"
{
    "appid"        "489830"
    "name"         "The Elder Scrolls V: Skyrim Special Edition"
    "StateFlags"   "4"
    "installdir"   "Skyrim Special Edition"
    "InstalledDepots"
    {
        "489833"
        {
            "manifest" "123456"
        }
    }
}
'''


class SteamManifestParserTests(unittest.TestCase):
    def test_parses_nested_app_manifest_without_losing_case(self):
        parsed = parse_keyvalues(VALID_MANIFEST)

        app = parsed["AppState"]
        self.assertEqual("489830", app["appid"])
        self.assertEqual("4", app["StateFlags"])
        self.assertEqual(
            "123456",
            app["InstalledDepots"]["489833"]["manifest"],
        )

    def test_supports_quoted_escapes(self):
        parsed = parse_keyvalues(r'"Root" { "value" "A \\ path and \"quote\"" }')

        self.assertEqual('A \\ path and "quote"', parsed["Root"]["value"])

    def test_rejects_duplicate_keys_and_malformed_structure(self):
        cases = (
            r'"Root" { "key" "one" "key" "two" }',
            r'"Root" { "key" "value"',
            r'"Root" "value" "orphan"',
            r'"Root" { key "unquoted" }',
            r'"Root" { "key" { "nested" "value" } "key" "duplicate" }',
        )
        for text in cases:
            with self.subTest(text=text):
                with self.assertRaises(SteamManifestError):
                    parse_keyvalues(text)


if __name__ == "__main__":
    unittest.main()
