"""UI modules must compile even when Qt/MO2 are unavailable to unit tests."""
from pathlib import Path
import unittest


class HubModuleCompilationTests(unittest.TestCase):
    def test_all_deployed_python_sources_can_be_compiled(self):
        root=Path(__file__).resolve().parents[1]/'modlab/resources/mo2_hub'
        for source in root.rglob('*.py'):
            with self.subTest(source=source.name):
                compile(source.read_bytes(),str(source),'exec')
