from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from modlab.resources.mo2_hub.mod_documents import documents, read_document


class ModDocumentsTests(unittest.TestCase):
    def test_download_page_uses_nexus_metadata_not_a_version_in_the_folder_name(self):
        from modlab.resources.mo2_hub import mod_documents
        with TemporaryDirectory() as temporary:
            root = Path(temporary); mod = root / 'Renamed mod 1.7.104'; mod.mkdir()
            meta = mod / 'meta.ini'
            meta.write_text('[General]\ngameName=SkyrimSE\nmodid=17230\n', encoding='utf-8')
            reader = mod_documents.download_page
            self.assertEqual('https://www.nexusmods.com/skyrimspecialedition/mods/17230?tab=files',
                             reader(root, mod.name))
            meta.write_text('[General]\nmodid=0\nurl=https://www.nexusmods.com/skyrimspecialedition/mods/17230\n', encoding='utf-8')
            self.assertEqual('https://www.nexusmods.com/skyrimspecialedition/mods/17230?tab=files',
                             reader(root, mod.name))

    def test_download_page_does_not_invent_a_link_from_unrelated_or_missing_metadata(self):
        from modlab.resources.mo2_hub import mod_documents
        with TemporaryDirectory() as temporary:
            root = Path(temporary); mod = root / '17230 - looks like a Nexus ID'; mod.mkdir()
            reader = mod_documents.download_page
            for content in ('[General]\ngameName=Skyrim\nmodid=17230\n',
                            '[General]\nurl=https://example.org/skyrimspecialedition/mods/17230\n',
                            'broken metadata'):
                (mod / 'meta.ini').write_text(content, encoding='utf-8')
                self.assertEqual('', reader(root, mod.name))
            self.assertEqual('', reader(root, '../outside'))

    def test_author_text_is_available_but_scripts_are_not_documents(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'Footprints.txt').write_text('Do not remove from an exterior save.')
            (root / 'Docs').mkdir()
            (root / 'Docs' / 'Installation.md').write_text('Choose your options.')
            (root / 'install.ps1').write_text('anything')
            (root / 'meta.ini').write_text('metadata')
            self.assertEqual(['Docs/Installation.md', 'Footprints.txt'],
                             [p.relative_to(root).as_posix() for p in documents(root)])

    def test_reading_is_bounded_and_preserves_author_encoding(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            doc = root / 'readme.txt'
            doc.write_bytes('Author’s instructions'.encode('utf-16'))
            self.assertEqual('Author’s instructions', read_document(root, doc))
            doc.write_bytes(b'x' * (1024 * 1024 + 1))
            with self.assertRaisesRegex(ValueError, 'too large'):
                read_document(root, doc)

    def test_selected_document_must_belong_to_the_mod(self):
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / 'mod'; root.mkdir()
            other = base / 'outside.txt'; other.write_text('other')
            with self.assertRaisesRegex(ValueError, 'outside'):
                read_document(root, other)

    def test_html_is_displayed_as_text_without_running_content(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            doc = root / 'readme.html'
            doc.write_text('<h1>Setup</h1><script>run()</script><p>Install the dependency.</p>')
            result = read_document(root, doc)
            self.assertIn('Setup', result)
            self.assertIn('Install the dependency.', result)
            self.assertNotIn('run()', result)
