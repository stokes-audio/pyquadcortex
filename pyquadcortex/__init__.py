"""Control a Neural DSP Quad Cortex over USB from Python.

    import pyquadcortex

    with pyquadcortex.connect() as qc:
        print(qc.version())
        qc.switch_scene(1)

See the readme for a tour, and ``docs/`` for the protocol and internals.
"""

import logging

__version__ = "0.9.0"

# A library must not write to the application's console. Without a handler,
# Python's handler of last resort prints WARNING and above to stderr, so a
# routine transport message would appear in a caller's output uninvited.
# Applications that want our logs can configure the "pyquadcortex" logger.
logging.getLogger(__name__).addHandler(logging.NullHandler())

from pyquadcortex.client import (Block, QuadCortex, Split, blocks, field_present,
                                 input_chain_rows, position_to_slot,
                                 slot_to_position, splits)  # noqa: E402
from pyquadcortex.enums import Input, Instrument, Output, Scene, Setlist
from pyquadcortex.session import DeviceNotFoundError, connect, open_device
from pyquadcortex import models  # noqa: E402  generated factory-block constants
from pyquadcortex.catalog import Model, ModelCatalog, Parameter  # noqa: E402

__all__ = [
    "__version__",
    "connect",
    "open_device",
    "DeviceNotFoundError",
    "QuadCortex",
    "Block",
    "blocks",
    "Split",
    "splits",
    "field_present",
    "Input",
    "Output",
    "Scene",
    "Instrument",
    "Setlist",
    "slot_to_position",
    "position_to_slot",
    "input_chain_rows",
    "models",
    "Model",
    "ModelCatalog",
    "Parameter",
]
