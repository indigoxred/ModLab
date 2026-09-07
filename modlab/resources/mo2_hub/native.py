"""Read SKSE declarations from PE files without loading or executing a DLL.

Layout and compatibility rules: ianpatt/skse64 PluginAPI.h / PluginManager.cpp,
checked 2026-09-06. A declaration is not evidence of successful runtime behavior.
"""
from dataclasses import dataclass
from pathlib import Path
import struct


SOURCE = 'https://github.com/ianpatt/skse64/blob/master/skse64/PluginManager.cpp'


@dataclass(frozen=True)
class NativeInfo:
    machine: int
    timestamp: int
    exports: tuple[str, ...] = ()
    data_version: int | None = None
    name: str = ''
    flags: int = 0
    extended: int = 0
    versions: tuple[int, ...] = ()
    skse_required: int = 0
    plugin_version: int = 0

    @property
    def is_plugin(self):
        return any(name.startswith('SKSEPlugin_') for name in self.exports)


def inspect_binary(path):
    with Path(path).open('rb') as stream:
        stream.seek(0, 2)
        size = stream.tell()

        def read(offset, length):
            if offset < 0 or length < 0 or offset + length > size or length > 1024 * 1024:
                raise ValueError('Native metadata points outside the file or inspection limit.')
            stream.seek(offset)
            data = stream.read(length)
            if len(data) != length:
                raise ValueError('Native metadata could not be read completely.')
            return data

        dos = read(0, 64)
        if dos[:2] != b'MZ':
            raise ValueError('Missing Windows executable header.')
        pe = struct.unpack_from('<I', dos, 60)[0]
        if not 64 <= pe <= 1024 * 1024:
            raise ValueError('Invalid Windows executable header offset.')
        header = read(pe, 24)
        if header[:4] != b'PE\0\0':
            raise ValueError('Missing PE signature.')
        machine, count, timestamp, _, _, optional_size, _ = struct.unpack_from('<HHIIIHH', header, 4)
        if machine != 0x8664:
            return NativeInfo(machine, timestamp)
        if not 1 <= count <= 96 or not 120 <= optional_size <= 4096:
            raise ValueError('Unsupported or incomplete native section headers.')
        optional = read(pe + 24, optional_size)
        if struct.unpack_from('<H', optional)[0] != 0x20b:
            raise ValueError('An x64 component has no PE32+ optional header.')
        headers_size = struct.unpack_from('<I', optional, 60)[0]
        directory_count = struct.unpack_from('<I', optional, 108)[0]
        if not directory_count:
            return NativeInfo(machine, timestamp)
        export_rva, export_size = struct.unpack_from('<II', optional, 112)
        if not export_rva and not export_size:
            return NativeInfo(machine, timestamp)
        if not export_rva or export_size < 40:
            raise ValueError('Incomplete export directory.')
        sections = []
        for i in range(count):
            section = read(pe + 24 + optional_size + 40*i, 40)
            virtual_size, rva, raw_size, raw = struct.unpack_from('<IIII', section, 8)
            sections.append((rva, raw_size, raw))

        def offset(rva, length):
            candidates = [raw + rva - start for start, amount, raw in sections
                          if start <= rva and rva + length <= start + amount]
            if rva + length <= headers_size:
                candidates.append(rva)
            if len(candidates) != 1:
                raise ValueError('Native export address is unmapped or ambiguous.')
            return candidates[0]

        def at(rva, length):
            return read(offset(rva, length), length)

        exports = at(export_rva, 40)
        _, functions, names_count, funcs_rva, names_rva, ordinals_rva = struct.unpack_from('<IIIIII', exports, 16)
        if functions > 65536 or names_count > 65536:
            raise ValueError('Native export table exceeds the inspection limit.')
        funcs = at(funcs_rva, functions * 4) if functions else b''
        names = at(names_rva, names_count * 4) if names_count else b''
        ordinals = at(ordinals_rva, names_count * 2) if names_count else b''
        exported = {}
        for i in range(names_count):
            name_rva = struct.unpack_from('<I', names, i*4)[0]
            # C++ mangled names can exceed 255 bytes. Read one bounded chunk,
            # confined to its mapped region, rather than seeking for every byte.
            available = [start + amount - name_rva for start, amount, raw in sections
                         if start <= name_rva < start + amount]
            if name_rva < headers_size:
                available.append(headers_size - name_rva)
            if len(available) != 1:
                raise ValueError('Native export name is unmapped or ambiguous.')
            name, terminator, _ = at(name_rva, min(4096, available[0])).partition(b'\0')
            if not terminator:
                raise ValueError('Unterminated native export name.')
            try:
                name = name.decode('ascii')
            except UnicodeDecodeError as error:
                raise ValueError('Non-ASCII native export name.') from error
            ordinal = struct.unpack_from('<H', ordinals, i*2)[0]
            if ordinal >= functions or name in exported:
                raise ValueError('Invalid or duplicate native export.')
            exported[name] = struct.unpack_from('<I', funcs, ordinal*4)[0]
        if 'SKSEPlugin_Version' not in exported:
            return NativeInfo(machine, timestamp, tuple(exported))
        version_rva = exported['SKSEPlugin_Version']
        if export_rva <= version_rva < export_rva + export_size:
            raise ValueError('Forwarded SKSE version export cannot be inspected as data.')
        version = at(version_rva, 848)
        name = version[8:264].split(b'\0', 1)[0].decode('ascii', errors='replace')
        extended, flags = struct.unpack_from('<II', version, 772)
        versions = []
        for value in struct.unpack_from('<16I', version, 780):
            if not value:
                break
            versions.append(value)
        return NativeInfo(machine, timestamp, tuple(exported), struct.unpack_from('<I', version)[0],
                          name, flags, extended, tuple(versions), struct.unpack_from('<I', version, 844)[0],
                          struct.unpack_from('<I', version, 4)[0])


def packed_version(text):
    parts = [int(part) for part in text.split('.')]
    if len(parts) == 3:
        parts.append(0)
    if len(parts) != 4 or any(not 0 <= value <= maximum for value, maximum in zip(parts, (255,255,4095,15))):
        raise ValueError('Unrecognized Skyrim runtime version.')
    return parts[0] << 24 | parts[1] << 16 | parts[2] << 4 | parts[3]


def display_version(value):
    return f'{value >> 24}.{(value >> 16) & 255}.{(value >> 4) & 4095}.{value & 15}'


def compatibility_problem(info, runtime, address_present, skse_version=None):
    if not info.is_plugin:
        return None  # A companion library is not a failed SKSE plugin.
    current = packed_version(runtime)
    if not packed_version('1.6.318.0') <= current <= packed_version('1.7.104.0'):
        return 'Unknown', 'This runtime is outside the checked SKSE declaration rules.'
    if info.data_version is None:
        return 'Blocked', 'This legacy SKSE plugin has no version declaration and is ignored by this runtime\'s SKSE loader.'
    if info.data_version != 1 or info.flags & ~7 or info.extended & ~3:
        return 'Unknown', 'This component uses an unrecognized SKSE declaration format or compatibility flag.'
    if not info.name:
        return 'Blocked', 'The SKSE version declaration has no plugin name.'
    independent = bool(info.flags & 3)
    reason = 'The DLL declares support for other game versions: ' + (', '.join(map(display_version, info.versions)) or 'no matching runtime listed')
    if info.flags & 1:
        if not address_present:
            return 'Blocked', 'This DLL requires the Address Library database for Skyrim ' + runtime + '.'
        if current >= packed_version('1.7.99.0') and not info.extended & 2 and 520128000 <= info.timestamp < 1748217600:
            independent = False
            reason = 'This build uses the older Address Library encoding and has no explicit declaration for Skyrim ' + runtime + '.'
    if independent and current >= packed_version('1.6.629.0') and not info.flags & 4 and not info.extended & 1:
        return 'Blocked', 'The DLL declares the older game structure layout, before Skyrim 1.6.629.'
    if not independent and current not in info.versions:
        return 'Blocked', reason
    if info.skse_required:
        if skse_version is None:
            return 'Unknown', 'The minimum SKSE version required by this DLL has not been checked.'
        if info.skse_required > skse_version:
            return 'Blocked', 'This DLL requires a newer SKSE build than the installed loader.'
    return None
