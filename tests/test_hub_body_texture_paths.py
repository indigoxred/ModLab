import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
import xml.etree.ElementTree as ET
from modlab.resources.mo2_hub.bodyslide import write_build_config


class BodyTexturePathTests(unittest.TestCase):
    def test_bodyslide_concatenated_texture_and_archive_paths_resolve(self):
        # BodySlide concatenates the configured base and relative resource name.
        with TemporaryDirectory() as folder:
            root=Path(folder); runner=root/'runner'; runner.mkdir()
            (runner/'Config.xml').write_text('<Config/>',encoding='utf-8')
            game=root/'Game with spaces'/'Data'; game.mkdir(parents=True)
            texture=game/'textures'/'test.dds'; texture.parent.mkdir(); texture.write_bytes(b'texture')
            archive=game/'Skyrim - Textures0.bsa'; archive.write_bytes(b'archive')
            write_build_config(runner,root/'output',game,['Body'],'Selected')
            base=ET.parse(runner/'Config.xml').getroot().findtext('GameDataPath')
            self.assertEqual(texture.read_bytes(),Path(base+'textures/test.dds').read_bytes())
            self.assertEqual(archive.read_bytes(),Path(base+archive.name).read_bytes())
