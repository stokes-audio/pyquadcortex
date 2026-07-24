"""Control a Neural DSP Quad Cortex over USB from Python.

    import pyquadcortex

    with pyquadcortex.connect() as qc:
        print(qc.version())
        qc.switch_scene(1)

See the readme for a tour, and ``docs/`` for the protocol and internals.
"""

import logging

__version__ = "0.1.0"

# A library must not write to the application's console. Without a handler,
# Python's handler of last resort prints WARNING and above to stderr, so a
# routine transport message would appear in a caller's output uninvited.
# Applications that want our logs can configure the "pyquadcortex" logger.
logging.getLogger(__name__).addHandler(logging.NullHandler())

from pyquadcortex.client import QuadCortex, input_chain_rows, slot_to_position  # noqa: E402
from pyquadcortex.enums import Input, Instrument, Output, Setlist
from pyquadcortex.session import DeviceNotFoundError, connect, open_device

__all__ = [
    "__version__",
    "connect",
    "open_device",
    "DeviceNotFoundError",
    "QuadCortex",
    "Input",
    "Output",
    "Instrument",
    "Setlist",
    "slot_to_position",
    "input_chain_rows",
]
