"""MO2 2.5.2 search APIs are nonrecursive and return physical file paths."""

from pathlib import Path, PureWindowsPath
import os


def readable_path(value):
    path = Path(value)
    # Embedded Python inherits MO2's manifest and can otherwise lose long paths.
    if os.name == 'nt' and path.is_absolute() and not str(path).startswith('\\\\?\\'):
        text = str(path)
        return Path('\\\\?\\UNC\\' + text[2:] if text.startswith('\\\\') else '\\\\?\\' + text)
    return path


def directories(organizer, root=''):
    pending = [root]
    while pending:
        current = pending.pop()
        yield current
        for name in organizer.listDirectories(current):
            if not name or PureWindowsPath(name).name != name or name in {'.', '..'}:
                raise ValueError(f'Unexpected virtual directory name: {name!r}')
            pending.append(current + '/' + name if current else name)


def find_files(organizer, root, patterns):
    for directory in directories(organizer, root):
        # With MO2 archive parsing enabled, findFiles also returns pseudo-paths
        # for packed assets. External helpers cannot open these as loose files.
        yield from (path for path in organizer.findFiles(directory, patterns) if readable_path(path).is_file())
