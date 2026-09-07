from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from modlab.resources.mo2_hub.helpers import HELPERS, locate_helper, save_location, load_locations


class HelperTests(unittest.TestCase):
    def test_saved_location_is_reused_without_a_new_search(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            exe = root / 'SSEEdit.exe'
            exe.write_bytes(b'fixture')
            config = root / 'helpers.json'
            save_location(config, 'xedit', exe)
            result = locate_helper(HELPERS['xedit'], load_locations(config), ())
            self.assertEqual((exe.resolve(),), result)

    def test_multiple_body_tools_need_a_choice_instead_of_arbitrary_first(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for folder in ('Style A', 'Style B'):
                exe = root / folder / 'BodySlide x64.exe'
                exe.parent.mkdir()
                exe.write_bytes(b'fixture')
                paths.append(exe)
            self.assertEqual(2, len(locate_helper(HELPERS['bodyslide'], {}, paths)))

    def test_moved_tool_is_missing_rather_than_reported_available(self):
        self.assertEqual((), locate_helper(HELPERS['xedit'], {'xedit': 'C:/absent/SSEEdit.exe'}, ()))

    def test_wrong_executable_cannot_be_saved_as_body_tool(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            exe = root / 'Different.exe'
            exe.write_bytes(b'fixture')
            with self.assertRaisesRegex(ValueError, 'BodySlide'):
                save_location(root / 'helpers.json', 'bodyslide', exe)
