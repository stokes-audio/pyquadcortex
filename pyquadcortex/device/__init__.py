"""The model of the unit: objects that look and behave the way the Quad Cortex does.

This package is what ``import pyquadcortex`` hands a caller. Nothing here speaks
the wire; it sits on :mod:`pyquadcortex.protocol` and turns the messages into the
unit's own vocabulary - presets, scenes, rows, slots, blocks.

The directory is named ``device`` rather than ``model`` because *model* is
already taken twice over: the protocol layer's ``models.py``, ``Model`` and
``ModelCatalog`` are the device's own word for an amp or a pedal block, and
``docs/domain-model.md`` section 5 gave that word to the virtual device list for
exactly that reason.

Its public names are re-exported from :mod:`pyquadcortex`, which is where callers
should import them from. The design is in ``docs/domain-model.md``.
"""

from pyquadcortex.device.device import Device, connect
from pyquadcortex.device.translate import (FootswitchLetter, PresetAddress,
                                           SceneLetter)

__all__ = ["Device", "connect", "FootswitchLetter", "SceneLetter",
           "PresetAddress"]
