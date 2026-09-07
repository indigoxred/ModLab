import tempfile
import unittest
from pathlib import Path
from modlab.resources.mo2_hub.archive_text import text_resources


class Reader:
    def extract(self, archive, names, destination):
        destination.mkdir(parents=True)
        output = destination / names[0]
        output.write_bytes(archive.read_bytes())
        return (output,)


class ArchiveTextTests(unittest.TestCase):
    def test_conflicting_archive_tables_are_not_arbitrarily_selected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            a, b = root / 'a.bsa', root / 'b.bsa'
            a.write_bytes(b'first text'); b.write_bytes(b'different text')
            with self.assertRaisesRegex(ValueError, 'Competing archive'):
                text_resources(Reader(), root, {'a.bsa': a, 'b.bsa': b}, ['NPC.esp'], 'English')

    def test_effective_loose_text_wins_over_archive_copies(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            a = root / 'a.bsa'; a.write_bytes(b'archive text')
            result = text_resources(Reader(), root, {'a.bsa': a, 'Strings/NPC_English.strings': root / 'loose'},
                                    ['NPC.esp'], 'ENGLISH')
            self.assertEqual({}, result)
