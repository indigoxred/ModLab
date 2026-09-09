"""Resolve loose and active packed scenery from a main-thread MO2 snapshot."""
from hashlib import sha256
from pathlib import Path

from .installation import _plain
from .outputs import MANIFEST, digest
from .scene_choices import GROUPS, category, scan_provider, file_source
from .vfs import readable_path


class Catalog:
    def __init__(self, roots, archives, cache, reader):
        self.roots, self.archives = roots, archives
        self.cache, self.reader = Path(cache), reader
        self.providers, self.loose, self.packed = {}, {}, {}
        self.archive_hashes = {}
        for name, location in roots:
            root = Path(location); _plain(root)
            if (root/MANIFEST).is_file():
                # Generated output can provide a dependency, but isn't a new source choice.
                files = {}
                for part in ('meshes', 'textures'):
                    for path in readable_path(root/part).rglob('*'):
                        if path.is_file() and path.suffix.casefold() in {'.nif', '.dds'}:
                            files[path.relative_to(readable_path(root)).as_posix().casefold()] = str(path)
            else:
                provider = scan_provider(name, root)
                files = provider['files']
                if name not in {'Game Data', 'Overwrite'}: self.providers[name] = provider
            self.loose.update({key: (path, name) for key, path in files.items()})
        for archive in archives:
            _plain(Path(archive['path']))
            for name in reader.entries(archive['path']):
                if not name.endswith(('.nif', '.dds')): continue
                self.packed.setdefault(name, []).append(archive)
        for name, provider in self.providers.items():
            packed = {path: rows for path, rows in self.packed.items()
                      if any(a['provider'] == name for a in rows)}
            provider['packed'] = packed
            provider['packed_source'] = lambda path, owner=name: self.packed_source(path, owner)
            all_paths = set(provider['files']) | set(packed)
            provider['groups'] = {key: tuple(sorted(path for path in all_paths if category(path) == key)) for key in GROUPS}
            provider['groups'] = {key: paths for key, paths in provider['groups'].items() if paths}

    def packed_source(self, name, owner=None):
        candidates = [row for row in self.packed.get(name, ()) if owner is None or row['provider'] == owner]
        if not candidates: return None
        rank = max(tuple(row['rank']) for row in candidates)
        winners = [row for row in candidates if tuple(row['rank']) == rank]
        if len(winners) != 1:
            raise ValueError('Two active archives have no established winner for ' + name +
                             '. Keep the installed setup or use a matching compatibility patch.')
        archive = winners[0]; path = Path(archive['path'])
        if str(path) not in self.archive_hashes: self.archive_hashes[str(path)] = digest(path)
        destination = self.cache/sha256(str(path).encode()).hexdigest()[:16]/name
        self.reader.extract_asset(path, name, destination)
        result = file_source(destination, archive['provider'])
        result['containers'] = {str(path): self.archive_hashes[str(path)]}
        return result

    def resolve(self, name):
        if name in self.loose:
            path, owner = self.loose[name]
            return file_source(Path(path), owner)
        return self.packed_source(name)
