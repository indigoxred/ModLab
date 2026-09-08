"""Check what the native installer returned; never infer gameplay success."""

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import os
import stat


def write_record(path, record):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(record, indent=2), encoding='utf-8')
    temporary.replace(path)


@dataclass(frozen=True)
class InstallResult:
    status: str
    name: str
    path: str
    file_count: int
    detail: str


def file_stamp(path):
    value = Path(path).stat()
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _plain(path):
    if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
        raise ValueError('Installed files contain a linked path: ' + str(path))


def _inventory(target, *, cooperative=True):
    result = {}
    def read_error(error):
        raise error
    for folder, directories, files in os.walk(target, onerror=read_error):
        _plain(Path(folder))
        for name in directories:
            _plain(Path(folder) / name)
        for name in files:
            path = Path(folder) / name
            _plain(path)
            if not stat.S_ISREG(path.stat().st_mode):
                raise ValueError('Installed content is not an ordinary file: ' + str(path))
            relative = path.relative_to(target).as_posix()
            if relative.casefold() != 'meta.ini':
                result[relative] = file_stamp(path)
            if cooperative:
                yield 'Inspecting installed files'
    return result


def _hash_steps(path, expected, label):
    _plain(path)
    if file_stamp(path) != expected:
        raise ValueError(label + ' changed during installation verification.')
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            checksum.update(chunk)
            yield 'Checking ' + label
    if file_stamp(path) != expected:
        raise ValueError(label + ' changed during installation verification.')
    return checksum.hexdigest()


def capture_files(archive, target, mods_root, original_archive_stamp=None):
    """Yield between reads so MO2 stays responsive; return a stable byte inventory.

    This records the native installer's chosen output, not a proof that the
    archive author included every dependency or that the feature works in game.
    """
    archive, target, mods_root = Path(archive), Path(target), Path(mods_root).resolve(strict=True)
    _plain(target)
    if target.resolve(strict=True).parent != mods_root or not target.is_dir():
        raise ValueError('Returned installation is outside the selected mods directory.')
    original = original_archive_stamp or file_stamp(archive)
    if file_stamp(archive) != original:
        raise ValueError('The source archive changed after installation started.')
    before = yield from _inventory(target)
    if not before:
        raise ValueError('The installer returned a mod folder without content.')
    archive_hash = yield from _hash_steps(archive, original, 'source archive')
    files = {}
    for name, stamp in before.items():
        checksum = yield from _hash_steps(target / name, stamp, name)
        files[name] = dict(size=stamp[2], sha256=checksum)
        yield 'Checked ' + name
    # Do not hand control back to MO2 after checking an already-hashed file.
    # The final metadata pass and activation must share one event-loop turn.
    after = yield from _inventory(target, cooperative=False)
    if before != after or file_stamp(archive) != original:
        raise ValueError('Installed files or source archive changed during verification. Recheck before continuing.')
    return dict(version=1, archive_sha256=archive_hash, files=files,
                scope='Exact archive identity and native installer output; dependencies and effective winners are checked separately.')


def find_existing(history, archive, mods_root, profile_path):
    """Find a receipt whose entire installed output still matches this archive.

    No restoration or inferred FOMOD selections: absent/old/ambiguous evidence
    simply leaves the normal native installation path available.
    """
    archive, mods_root = Path(archive).resolve(), Path(mods_root).resolve()
    seen = set()
    paths = sorted(Path(history).glob('*.json'), key=lambda p:p.stat().st_mtime_ns, reverse=True)
    for path in paths:
        yield 'Checking saved installation choices'
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            if record.get('status') not in {'Installed, enabled', 'Installed, disabled'}:
                continue
            if record.get('profile_path') != str(profile_path) or Path(record.get('mods_root','')).resolve() != mods_root:
                continue
            if Path(record.get('archive','')).resolve() != archive:
                continue
            expected = record.get('file_verification', {})
            if expected.get('version') != 1 or not expected.get('files'):
                continue
            target = Path(record['result']['path'])
            if target.parent.resolve() != mods_root or target.name != record['result']['name']:
                continue
            if str(target).casefold() in seen:
                continue
            seen.add(str(target).casefold())
            current = yield from capture_files(archive, target, mods_root)
            if current['archive_sha256'] == expected['archive_sha256'] and current['files'] == expected['files']:
                return dict(record=str(path), target=str(target), name=target.name, verification=current)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return None


def inspect_install_result(mod, mods_root: Path, active: bool) -> InstallResult:
    if mod is None:
        return InstallResult("Not installed", "", "", 0,
                             "MO2 reported cancellation or failure. Check its messages and any retained backup.")
    root = mods_root.resolve(strict=True)
    target = Path(mod.absolutePath()).resolve(strict=True)
    if target.parent != root or not target.is_dir():
        raise ValueError("Returned installation is outside the selected mods directory.")
    content = [p for p in target.rglob("*") if p.is_file() and p.relative_to(target).as_posix() != "meta.ini"]
    if not content:
        return InstallResult("Incomplete", mod.name(), str(target), 0,
                             "The installer returned a mod folder without content. Inspect the archive and installer choices.")
    return InstallResult(
        "Installed, enabled" if active else "Installed, disabled",
        mod.name(), str(target), len(content),
        "Installed files were found. Dependencies, effective assets and the intended in-game feature are not yet verified.",
    )
