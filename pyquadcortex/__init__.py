"""Control a Neural DSP Quad Cortex over USB from Python.

Two namespaces, one package. This one is the model of the unit::

    import pyquadcortex

    with pyquadcortex.connect() as device:
        print(device.firmware)

and the message-level API this library shipped through 0.40.0 is one import
deeper, unchanged (ADR-0006)::

    from pyquadcortex import protocol

    with protocol.connect() as qc:
        qc.switch_scene(1)

`Device.from_client(qc)` puts a model on a protocol connection you already hold,
so both layers work in one script.

See the readme for a tour, ``docs/domain-model.md`` for the model, and ``docs/``
for the protocol and internals.
"""

import logging

from pyquadcortex._version import __version__

# A library must not write to the application's console. Without a handler,
# Python's handler of last resort prints WARNING and above to stderr, so a
# routine transport message would appear in a caller's output uninvited.
# Applications that want our logs can configure the "pyquadcortex" logger.
logging.getLogger(__name__).addHandler(logging.NullHandler())

from pyquadcortex import protocol  # noqa: E402
from pyquadcortex.device import (Device, FootswitchLetter,  # noqa: E402
                                 PresetAddress, SceneLetter, connect)
from pyquadcortex.protocol import (DeviceLostError,  # noqa: E402
                                   DeviceNotFoundError)

__all__ = [
    "__version__",
    "connect",
    "Device",
    "FootswitchLetter",
    "SceneLetter",
    "PresetAddress",
    "protocol",
    "DeviceNotFoundError",
    "DeviceLostError",
]
