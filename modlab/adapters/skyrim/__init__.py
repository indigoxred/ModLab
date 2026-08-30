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

__all__ = [
    "DataFileEvidence",
    "DiscoveryFinding",
    "ExecutableEvidence",
    "SkyrimDiscoveryFormatError",
    "SkyrimDiscoveryReport",
    "SteamManifestError",
    "discovery_from_dict",
    "discovery_to_dict",
    "parse_keyvalues",
]
