"""Decode RC003's keyboard report before Windows drops unsupported usages.

Protocol facts are documented by qi-o/miremote-vibe and the original macOS
profile. This module is independently implemented and never injects input.
"""
from __future__ import annotations
from .buttons import RemoteButton

EXTRA_USAGES = {0xF1: RemoteButton.BACK, 0x80: RemoteButton.VOLUME_UP, 0x81: RemoteButton.VOLUME_DOWN}
DRIVER_USAGES = {**EXTRA_USAGES, 0x3E: RemoteButton.VOICE, 0x35: RemoteButton.TV}


def report_usages(report: bytes) -> frozenset[int] | None:
    """None is malformed; an empty set is a valid all-keys-released report."""
    if len(report) != 9 or report[:3] != b'\x01\x00\x00':
        return None
    return frozenset(value for offset in (3, 5, 7)
                     if (value := int.from_bytes(report[offset:offset+2], 'little')))


class ExtraKeyReducer:
    def __init__(self, include_voice_tv=False):
        self.held: frozenset[int] = frozenset()
        self.usages = DRIVER_USAGES if include_voice_tv else EXTRA_USAGES

    def feed(self, report: bytes):
        usages = report_usages(report)
        if usages is None:
            return []  # malformed input must not synthesize releases
        current = usages & self.usages.keys()
        edges = [(self.usages[u], False) for u in sorted(self.held-current)]
        edges += [(self.usages[u], True) for u in sorted(current-self.held)]
        self.held = frozenset(current)
        return edges

    def reset(self):
        edges = [(self.usages[u], False) for u in sorted(self.held)]
        self.held = frozenset()
        return edges
