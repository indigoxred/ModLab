"""MO2 entry point; pure assessment modules also import outside the host."""


def createPlugin():
    from .plugin import ModLabHub

    return ModLabHub()
