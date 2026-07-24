"""Control a Neural DSP Quad Cortex over USB from Python.

    import pyquadcortex

    with pyquadcortex.connect() as qc:
        print(qc.version())
        qc.switch_scene(1)

See the readme for a tour, and ``docs/`` for the protocol and internals.
"""

__version__ = "0.1.0"

from pyquadcortex.client import QuadCortex, input_chain_rows, slot_to_position
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
