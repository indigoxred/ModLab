"""Obtain Microsoft's portable .NET runtime when a supported helper needs it."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import urlparse
from urllib.request import urlopen
from uuid import uuid4
import zipfile

from .bodyslide import relative_path

FEED = 'https://builds.dotnet.microsoft.com/dotnet/release-metadata/10.0/releases.json'


def has_net10(listing):
    return re.search(r'^Microsoft\.NETCore\.App 10\.\d+\.\d+ \[.+\]$', listing, re.M) is not None


def runtime_available(executable):
    if not executable or not Path(executable).is_file():
        return False
    result = subprocess.run([str(executable), '--list-runtimes'], capture_output=True, text=True,
                            timeout=15, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return result.returncode == 0 and has_net10(result.stdout)


def ensure_net10(tools_root):
    """Reuse a runtime or fetch its official ZIP, checking Microsoft's SHA-512."""
    return _ensure_net10(tools_root, 'runtime', runtime_available)


def has_sdk10(listing):
    return re.search(r'^10\.\d+\.\d+ \[.+\]$', listing, re.M) is not None


def sdk_available(executable):
    if not executable or not Path(executable).is_file():
        return False
    result = subprocess.run([str(executable), '--list-sdks'], capture_output=True, text=True,
                            timeout=15, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return result.returncode == 0 and has_sdk10(result.stdout)


def ensure_sdk10(tools_root):
    """The SDK is needed to build selected Synthesis patchers; a runtime is insufficient."""
    return _ensure_net10(tools_root, 'sdk', sdk_available)


def _ensure_net10(tools_root, component, available):
    for candidate in (shutil.which('dotnet'), Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'dotnet/dotnet.exe'):
        if available(candidate):
            return Path(candidate).parent
    target = Path(tools_root) / ('dotnet-10' if component == 'runtime' else 'dotnet-sdk-10')
    if available(target / 'dotnet.exe'):
        return target
    if target.exists():
        raise ValueError(f'The local .NET {component} is incomplete. Move it aside and recheck: {target}')
    with urlopen(FEED, timeout=30) as response:
        metadata = json.load(response)
    release = next(r for r in metadata['releases'] if r[component]['version'] == metadata['latest-' + component])
    package = next(f for f in release[component]['files']
                   if f['rid'] == 'win-x64' and f['name'] == 'dotnet-' + component + '-win-x64.zip')
    if urlparse(package['url']).scheme != 'https' or urlparse(package['url']).hostname != 'builds.dotnet.microsoft.com':
        raise ValueError('Microsoft runtime download location is not the expected official host.')
    downloads = Path(tools_root) / 'downloads'
    downloads.mkdir(parents=True, exist_ok=True)
    archive = downloads / ('dotnet-' + component + '-' + release[component]['version'] + '-win-x64.zip')
    if not archive.is_file() or hashlib.sha512(archive.read_bytes()).hexdigest().casefold() != package['hash'].casefold():
        temporary = archive.with_suffix('.partial')
        with urlopen(package['url'], timeout=60) as response, temporary.open('wb') as stream:
            shutil.copyfileobj(response, stream)
        if hashlib.sha512(temporary.read_bytes()).hexdigest().casefold() != package['hash'].casefold():
            raise ValueError('The Microsoft runtime download did not match its published checksum. Recheck to download again.')
        temporary.replace(archive)
    staging = Path(tools_root) / ('dotnet-prepare-' + uuid4().hex[:8])
    staging.mkdir()
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            if item.is_dir():
                continue
            name = relative_path(item.filename)
            destination = staging / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(item) as source, destination.open('wb') as stream:
                shutil.copyfileobj(source, stream)
    if not available(staging / 'dotnet.exe'):
        raise ValueError('The downloaded ' + component + ' could not report .NET 10. Inspect ' + str(staging))
    (staging / 'modlab-download.json').write_text(json.dumps({
        'url': package['url'], 'sha512': package['hash'], 'version': release[component]['version'],
        'obtained_at': datetime.now(timezone.utc).isoformat()}, indent=2), encoding='utf-8')
    staging.rename(target)
    return target


@contextmanager
def dotnet_environment(root):
    previous = os.environ.get('DOTNET_ROOT_X64')
    os.environ['DOTNET_ROOT_X64'] = str(root)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop('DOTNET_ROOT_X64', None)
        else:
            os.environ['DOTNET_ROOT_X64'] = previous


@contextmanager
def sdk_environment(root, state=None):
    root = Path(root)
    state = Path(state) if state else root.parent / 'sdk-state'
    values = {'PATH': str(root) + os.pathsep + (_process_environment('PATH') or ''),
              'DOTNET_CLI_HOME': str(state), 'NUGET_PACKAGES': str(state.parent / 'nuget'),
              'DOTNET_ADD_GLOBAL_TOOLS_TO_PATH': 'false', 'DOTNET_GENERATE_ASPNET_CERTIFICATE': 'false'}
    previous = {key: _process_environment(key) for key in values}
    os.environ.update(values)
    try:
        with dotnet_environment(root):
            yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _process_environment(key):
    # MO2 appends its DLL directory natively after embedded Python has cached
    # os.environ. Restoring that stale cache would break the next LOOT launch.
    if os.name != 'nt':
        return os.environ.get(key)
    import ctypes
    read = ctypes.WinDLL('kernel32', use_last_error=True).GetEnvironmentVariableW
    read.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    read.restype = ctypes.c_uint32
    size = read(key, None, 0)
    if not size:
        return None
    buffer = ctypes.create_unicode_buffer(size)
    read(key, buffer, size)
    return buffer.value
