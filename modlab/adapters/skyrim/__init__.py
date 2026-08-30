"""Read-only Skyrim Steam evidence adapter."""

from .model import (
    DataFileEvidence,
    DiscoveryFinding,
    ExecutableEvidence,
    SkyrimDiscoveryReport,
)
from .serialization import (
    SkyrimDiscoveryFormatError,
    discovery_from_dict,
    discovery_to_dict,
)
from .steam_manifest import SteamManifestError, parse_keyvalues
from .scanner import discover_skyrim_steam
from .windows_version import read_windows_file_version

__all__ = [
    "DataFileEvidence",
    "DiscoveryFinding",
    "ExecutableEvidence",
    "SkyrimDiscoveryFormatError",
    "SkyrimDiscoveryReport",
    "SteamManifestError",
    "discovery_from_dict",
    "discovery_to_dict",
    "discover_skyrim_steam",
    "parse_keyvalues",
    "read_windows_file_version",
]
