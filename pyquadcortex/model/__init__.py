"""The model of the unit: objects that look and behave the way the Quad Cortex does.

This package is what ``import pyquadcortex`` hands a caller. Nothing here speaks
the wire; it sits on :mod:`pyquadcortex.protocol` and turns the messages into the
unit's own vocabulary - presets, scenes, rows, slots, blocks.

Its public names are re-exported from :mod:`pyquadcortex`, which is where callers
should import them from. The design is in ``docs/domain-model.md``.
"""

from pyquadcortex.model.device import Device, connect

__all__ = ["Device", "connect"]
