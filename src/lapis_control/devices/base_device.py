"""Provide an optional minimal starting point for future device classes."""


class BaseDevice:
    """Store a device name without opening a hardware connection."""

    def __init__(self, name: str) -> None:
        self.name = name
