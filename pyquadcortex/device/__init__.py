"""The model of the unit: objects that look and behave the way the Quad Cortex does.

This package is what ``import pyquadcortex`` hands a caller. Nothing here speaks
the wire; it sits on :mod:`pyquadcortex.protocol` and turns the messages into the
unit's own vocabulary - presets, scenes, rows, slots, blocks.

The directory is named ``device`` rather than ``model`` because in this codebase
the identifier ``model`` already means an amp or a pedal block: the protocol
layer's ``models.py``, ``Model``, ``ModelCatalog`` and ``set_block(model=...)``
are all that sense of the word. ``docs/domain-model.md`` section 5 renamed that
concept to *virtual device* in the model's own vocabulary, which is what the
screen calls it - but the protocol layer still spells it ``model``, so a
directory named ``model/`` collides with real code a reader is looking at.

Its public names are re-exported from :mod:`pyquadcortex`, which is where callers
should import them from. The design is in ``docs/domain-model.md``.
"""

from pyquadcortex.device.blocks import (Block, DeviceBlock, InputBlock,
                                        InputSource, LaneOutput, MixerBlock,
                                        OutputBlock, OutputDestination,
                                        SplitterBlock, VirtualDevice)
from pyquadcortex.device.device import Device, connect
from pyquadcortex.device.errors import InactiveSceneError
from pyquadcortex.device.events import Changed, Invalidated, ModelEvent
from pyquadcortex.device.grid import BlockGrid, Row, Rows, Slots, SplittableRow
from pyquadcortex.device.preset import Preset, Scene, Scenes
from pyquadcortex.device.translate import (FootswitchLetter, PresetAddress,
                                           SceneLetter)

__all__ = [
    "Device", "connect",
    "FootswitchLetter", "SceneLetter", "PresetAddress",
    "Preset", "Scene", "Scenes",
    "Rows", "Row", "SplittableRow", "Slots", "BlockGrid",
    "Block", "DeviceBlock", "InputBlock", "OutputBlock",
    "SplitterBlock", "MixerBlock", "LaneOutput",
    "VirtualDevice", "InputSource", "OutputDestination",
    "ModelEvent", "Changed", "Invalidated",
    "InactiveSceneError",
]
