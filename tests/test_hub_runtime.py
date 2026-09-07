from pathlib import Path
from tempfile import TemporaryDirectory
import os
import unittest

from modlab.resources.mo2_hub.runtime import has_net10, dotnet_environment, has_sdk10, sdk_environment


class RuntimeTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'MO2 embeds Python on Windows')
    def test_sdk_preserves_host_native_dll_search_path(self):
        import ctypes
        from modlab.resources.mo2_hub.runtime import _process_environment
        original = _process_environment('PATH')
        native = original + os.pathsep + 'C:/MO2/native-dll-path'
        set_value = ctypes.WinDLL('kernel32').SetEnvironmentVariableW
        set_value.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        try:
            set_value('PATH', native)
            self.assertNotEqual(os.environ['PATH'], native)
            with sdk_environment(Path('C:/sdk')):
                self.assertTrue(_process_environment('PATH').endswith('C:/MO2/native-dll-path'))
            self.assertEqual(_process_environment('PATH'), native)
        finally:
            os.environ['PATH'] = original

    def test_sdk_listing_requires_sdk_ten(self):
        self.assertTrue(has_sdk10('10.0.400 [C:/dotnet/sdk]\n'))
        self.assertFalse(has_sdk10('Microsoft.NETCore.App 10.0.11 [C:/dotnet/shared]\n'))
        self.assertFalse(has_sdk10('9.0.100 [C:/dotnet/sdk]\n'))

    def test_sdk_path_is_scoped_to_child_launch(self):
        from modlab.resources.mo2_hub.runtime import _process_environment
        before = dict(os.environ)
        before['PATH'] = _process_environment('PATH')
        with sdk_environment(Path('C:/ModLab/tools/dotnet-sdk-10')):
            self.assertEqual(os.environ['PATH'].split(os.pathsep)[0], str(Path('C:/ModLab/tools/dotnet-sdk-10')))
            self.assertEqual(os.environ['DOTNET_ROOT_X64'], str(Path('C:/ModLab/tools/dotnet-sdk-10')))
        self.assertEqual(dict(os.environ), before)

    def test_runtime_listing_requires_core_ten(self):
        self.assertTrue(has_net10('Microsoft.NETCore.App 10.0.11 [C:/dotnet/shared/Microsoft.NETCore.App]\n'))
        self.assertFalse(has_net10('Microsoft.NETCore.App 9.0.4 [C:/dotnet]\n'))
        self.assertFalse(has_net10('Microsoft.AspNetCore.App 10.0.11 [C:/dotnet]\n'))
        self.assertFalse(has_net10('error Microsoft.NETCore.App 10.0.11'))

    def test_private_runtime_environment_restored_even_when_launch_fails(self):
        before = os.environ.get('DOTNET_ROOT_X64')
        with TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                with dotnet_environment(Path(tmp)):
                    self.assertEqual(os.environ['DOTNET_ROOT_X64'], tmp)
                    raise RuntimeError('launch failed')
        self.assertEqual(os.environ.get('DOTNET_ROOT_X64'), before)
