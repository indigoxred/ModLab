from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest


def packed(major=1, minor=7, build=104, sub=0):
    return major << 24 | minor << 16 | build << 4 | sub


def dll_fixture(*, version=True, plugin=True, flags=5, extended=2, versions=(), timestamp=1700000000):
    data = bytearray(4096)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 0x3c, 128)
    data[128:132] = b'PE\0\0'
    struct.pack_into('<HHIIIHH', data, 132, 0x8664, 1, timestamp, 0, 0, 240, 0x2022)
    struct.pack_into('<H', data, 152, 0x20b)
    struct.pack_into('<I', data, 152+60, 512)
    struct.pack_into('<I', data, 152+108, 16)
    struct.pack_into('<II', data, 152+112, 0x1000, 256)
    data[392:400] = b'.rdata\0\0'
    struct.pack_into('<IIII', data, 400, 3584, 0x1000, 3584, 512)
    names = ([b'SKSEPlugin_Version'] if version else []) + ([b'SKSEPlugin_Load'] if plugin else [b'DllGetClassObject'])
    struct.pack_into('<IIIIII', data, 512+16, 1, len(names), len(names), 0x1040, 0x1060, 0x1080)
    for i, name in enumerate(names):
        struct.pack_into('<I', data, 576+i*4, 0x1400 if name == b'SKSEPlugin_Version' else 0x1900)
        struct.pack_into('<I', data, 608+i*4, 0x10a0+i*32)
        struct.pack_into('<H', data, 640+i*2, i)
        data[672+i*32:672+i*32+len(name)+1] = name + b'\0'
    offset = 1536
    struct.pack_into('<II', data, offset, 1, 123)
    data[offset+8:offset+16] = b'Example\0'
    struct.pack_into('<II', data, offset+772, extended, flags)
    for i, value in enumerate(versions):
        struct.pack_into('<I', data, offset+780+i*4, value)
    return bytes(data)


class NativeTests(unittest.TestCase):
    def inspect(self, data, runtime='1.7.104.0', address=True):
        from modlab.resources.mo2_hub.native import inspect_binary, compatibility_problem
        with TemporaryDirectory() as directory:
            path = Path(directory)/'test.dll'
            path.write_bytes(data)
            info = inspect_binary(path)
            return info, compatibility_problem(info, runtime, address)

    def test_explicit_runtime_list_rejects_wrong_runtime(self):
        info, problem = self.inspect(dll_fixture(flags=0, extended=0, versions=(packed(1,6,1170),)))
        self.assertEqual('Example', info.name)
        self.assertEqual(123, info.plugin_version)
        self.assertEqual('Blocked', problem[0])
        self.assertIn('1.6.1170.0', problem[1])
        self.assertIsNone(self.inspect(dll_fixture(flags=0, versions=(packed(),)))[1])

    def test_address_library_and_old_encoding_are_distinct_problems(self):
        self.assertIn('database', self.inspect(dll_fixture(), address=False)[1][1])
        self.assertIn('encoding', self.inspect(dll_fixture(extended=0))[1][1])
        self.assertIsNone(self.inspect(dll_fixture())[1])
        # SKSE permits an explicit runtime declaration despite old encoding flags.
        self.assertIsNone(self.inspect(dll_fixture(extended=0, versions=(packed(),)))[1])

    def test_recent_unflagged_build_follows_skse_compatibility_exception(self):
        self.assertIsNone(self.inspect(dll_fixture(extended=0, timestamp=1780000000))[1])

    def test_legacy_plugin_is_not_confused_with_companion_library(self):
        info, problem = self.inspect(dll_fixture(version=False))
        self.assertTrue(info.is_plugin)
        self.assertEqual('Blocked', problem[0])
        info, problem = self.inspect(dll_fixture(version=False, plugin=False))
        self.assertFalse(info.is_plugin)
        self.assertIsNone(problem)

    def test_structure_layout_and_unknown_flags_do_not_pass_silently(self):
        self.assertIn('1.6.629', self.inspect(dll_fixture(flags=2, extended=0))[1][1])
        self.assertIsNone(self.inspect(dll_fixture(flags=2, extended=1))[1])
        self.assertEqual('Unknown', self.inspect(dll_fixture(flags=0x80))[1][0])

    def test_invalid_export_pointer_and_forwarder_are_not_read_as_version_data(self):
        from modlab.resources.mo2_hub.native import inspect_binary
        for pointer in (0xfffff000, 0x1010):
            data = bytearray(dll_fixture())
            struct.pack_into('<I', data, 576, pointer)
            with TemporaryDirectory() as directory:
                path = Path(directory)/'broken.dll'; path.write_bytes(data)
                with self.assertRaises(ValueError):
                    inspect_binary(path)

    def test_long_cpp_export_name_does_not_hide_valid_skse_declaration(self):
        data = bytearray(dll_fixture())
        struct.pack_into('<I', data, 612, 0x1a00)
        data[3072:3673] = b'?' + b'A'*599 + b'\0'
        info, problem = self.inspect(data)
        self.assertEqual('Example', info.name)
        self.assertIsNone(problem)

    def test_foundation_check_names_the_mod_and_stops_managed_launch(self):
        from modlab.resources.mo2_hub.assessment import SetupSnapshot, Asset
        from modlab.resources.mo2_hub.foundations import inspect_foundations
        with TemporaryDirectory() as directory:
            path=Path(directory)/'old.dll'; path.write_bytes(dll_fixture(version=False))
            setup=SetupSnapshot('Skyrim Special Edition','1.7.104.0','Test',directory,
                assets=(Asset('SKSE/Plugins/old.dll',('Old downloaded mod',)),))
            findings=inspect_foundations(setup,lambda p:str(path))
            finding=next(f for f in findings if f.code=='native-runtime-incompatible')
            self.assertEqual('Blocked',finding.level)
            self.assertIn('Old downloaded mod',finding.detail)
            self.assertIn('cleaning',finding.action.lower())
