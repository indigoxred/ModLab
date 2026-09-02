"""Import-safe MO2 Guard plug-in entry point."""


def createPlugin():
    """Create the MO2 tool only inside MO2's Python runtime."""
    from .plugin import ModLabGuard

    return ModLabGuard()
