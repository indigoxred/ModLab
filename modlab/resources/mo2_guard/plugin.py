"""Passive MO2 IPluginTool adapter; guarded lifecycle is added in a later task."""

from __future__ import annotations

import mobase
from PyQt5.QtGui import QIcon


class ModLabGuard(mobase.IPluginTool):
    """A complete passive tool that leaves ordinary MO2 launches unchanged."""

    def name(self):
        return "ModLab Guard"

    def localizedName(self):
        return self.name()

    def settings(self):
        return []

    def displayName(self):
        return self.name()

    def tooltip(self):
        return self.description()

    def icon(self):
        return QIcon()

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
