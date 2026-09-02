"""Passive MO2 IPluginTool adapter; guarded lifecycle is added in a later task."""

from __future__ import annotations

try:  # Host-side inventory tests deliberately do not import MO2's mobase package.
    import mobase  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - exercised by host import safety.
    class _PluginTool:
        pass
else:  # pragma: no cover - exercised only inside the retained MO2 runtime.
    _PluginTool = mobase.IPluginTool


class ModLabGuard(_PluginTool):
    """A complete passive tool that leaves ordinary MO2 launches unchanged."""

    def name(self):
        return "ModLab Guard"

    def localizedName(self):
        return self.name()

    def author(self):
        return "ModLab"

    def description(self):
        return "Passive ModLab handshake guard."

    def version(self):
        if "mobase" in globals():
            return mobase.VersionInfo(1, 0, 0, mobase.ReleaseType.FINAL)
        return (1, 0, 0)

    def init(self, organizer):
        return True

    def display(self):
        return None
