"""Read selected localized text tables through MO2's bundled libbsarch."""
import ctypes as c
from pathlib import Path


class Message(c.Structure):
    _pack_ = 1
    _fields_ = [('code', c.c_int8), ('text', c.c_wchar * 1024)]


class ArchiveReader:
    # ABI: https://github.com/deorder/libbsarch/blob/master/src/libbsarch.h
    def __init__(self, library):
        self.dll = c.WinDLL(str(library))
        self._entries = {}
        for name, result, args in (
            ('bsa_create', c.c_void_p, []),
            ('bsa_free', Message, [c.c_void_p]),
            ('bsa_load_from_file', Message, [c.c_void_p, c.c_wchar_p]),
            ('bsa_find_file_record', c.c_void_p, [c.c_void_p, c.c_wchar_p]),
            ('bsa_extract_file', Message, [c.c_void_p, c.c_wchar_p, c.c_wchar_p]),
            ('bsa_file_count_get', c.c_uint32, [c.c_void_p]),
        ):
            function = getattr(self.dll, name)
            function.restype, function.argtypes = result, args

    def entries(self, archive):
        """List filenames without extracting asset data; invalidate on file changes."""
        archive = Path(archive).resolve()
        stat = archive.stat()
        signature = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        previous = self._entries.get(archive)
        if previous and previous[0] == signature:
            return previous[1]
        handle = self.dll.bsa_create()
        if not handle:
            raise ValueError('The archive reader could not start.')
        names = []
        try:
            self.checked(self.dll.bsa_load_from_file(handle, str(archive)))
            count = self.dll.bsa_file_count_get(handle)
            if count > 2_000_000:
                raise ValueError('Archive entry count exceeds the inspection limit.')
            callback_type = c.CFUNCTYPE(c.c_bool, c.c_void_p, c.c_wchar_p, c.c_void_p, c.c_void_p, c.c_void_p)
            def collect(unused, name, record, folder, context):
                names.append(name)
                return False  # libbsarch: true skips the remaining files in this folder.
            callback = callback_type(collect)
            iterate = self.dll.bsa_iterate_files
            iterate.restype, iterate.argtypes = Message, [c.c_void_p, callback_type, c.c_void_p]
            self.checked(iterate(handle, callback, None))
            if len(names) != count:
                raise ValueError('Archive listing did not match its declared file count.')
            normalized = []
            for name in names:
                name = name.replace('\\', '/').casefold()
                if not name or name.startswith('/') or ':' in name or any(p in {'', '.', '..'} for p in name.split('/')):
                    raise ValueError('Archive contains an invalid asset path.')
                normalized.append(name)
            after = archive.stat()
            if signature != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError('Archive changed during inspection; recheck it.')
            result = tuple(normalized)
            if len(self._entries) >= 128:
                self._entries.clear()
            self._entries[archive] = signature, result
            return result
        finally:
            self.checked(self.dll.bsa_free(handle))

    @staticmethod
    def checked(message):
        if message.code:
            raise ValueError('Archive text could not be read: ' + message.text)

    def extract(self, archive, names, destination):
        handle = self.dll.bsa_create()
        if not handle:
            raise ValueError('The archive reader could not start.')
        found = []
        try:
            self.checked(self.dll.bsa_load_from_file(handle, str(archive)))
            for name in names:
                if Path(name).name != name or ':' in name or '/' in name or '\\' in name:
                    raise ValueError('Invalid text table filename.')
                resource = 'strings\\' + name
                if self.dll.bsa_find_file_record(handle, resource):
                    output = Path(destination) / name
                    output.parent.mkdir(parents=True, exist_ok=True)
                    self.checked(self.dll.bsa_extract_file(handle, resource, str(output)))
                    if not output.is_file():
                        raise ValueError('Archive reader did not produce the requested text table: ' + name)
                    found.append(output)
            return tuple(found)
        finally:
            self.checked(self.dll.bsa_free(handle))

    def extract_asset(self, archive, relative, destination):
        relative = relative.replace('\\', '/').casefold()
        if relative not in self.entries(archive):
            raise ValueError('The requested asset is absent from its archive: ' + relative)
        output = Path(destination)
        output.parent.mkdir(parents=True, exist_ok=True)
        handle = self.dll.bsa_create()
        if not handle: raise ValueError('The archive reader could not start.')
        try:
            self.checked(self.dll.bsa_load_from_file(handle, str(archive)))
            self.checked(self.dll.bsa_extract_file(handle, relative.replace('/', '\\'), str(output)))
            if not output.is_file(): raise ValueError('The archive did not produce ' + relative)
        finally:
            self.checked(self.dll.bsa_free(handle))


def text_resources(reader, job, resources, plugins, language):
    from .xedit import digest
    names = [Path(plugin).stem.casefold() + '_' + language.casefold() + extension
             for plugin in plugins for extension in ('.strings', '.dlstrings', '.ilstrings')]
    loose = {name.casefold() for name in resources if name.casefold().startswith('strings/')}
    found = {}
    for index, (name, archive) in enumerate(resources.items()):
        if not name.casefold().endswith('.bsa'):
            continue
        for path in reader.extract(archive, names, job / 'text-resources' / str(index)):
            key = 'Strings/' + path.name
            if key.casefold() in loose:
                continue
            if key in found and digest(found[key]) != digest(path):
                raise ValueError('Competing archive text tables need an explicit winner: ' + path.name)
            found[key] = path
    return found
