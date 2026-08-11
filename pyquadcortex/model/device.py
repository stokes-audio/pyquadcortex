"""The unit itself, as an object.

:func:`connect` is the library's front door: it opens the Quad Cortex over USB
and hands back a :class:`Device`::

    import pyquadcortex

    with pyquadcortex.connect() as device:
        print(device.firmware, device.serial)

The `Device` is deliberately small right now. It carries the unit's identity and
owns the connection; the Directory, the live cache, the loaded preset and the
grid arrive in the stories that follow, per ``docs/domain-model.md``. What is
here is what has been built - nothing is stubbed out to look finished.
"""

from pyquadcortex import protocol


class Device:
    """One connected Quad Cortex.

    Build one by calling :func:`pyquadcortex.connect`, or by wrapping a protocol
    connection you already hold with :meth:`from_client`.
    """

    def __init__(self, client, *, _owns_client: bool = False):
        """Internal. Use :func:`connect` or :meth:`from_client`."""
        self._client = client
        self._owns_client = _owns_client
        self._version = None

    @classmethod
    def from_client(cls, client) -> "Device":
        """Build a model on a protocol connection the caller already has.

        The caller keeps ownership: closing this `Device` does not close their
        connection. Use it to mix the two layers in one script::

            from pyquadcortex import Device, protocol

            with protocol.connect() as qc:
                device = Device.from_client(qc)
        """
        return cls(client)

    @property
    def client(self):
        """The :class:`~pyquadcortex.protocol.client.QuadCortex` underneath.

        The way down to the message level for anything the model does not cover
        yet. A `Device` from :func:`connect` opened this connection and closes it;
        one from :meth:`from_client` did not and does not.
        """
        return self._client

    @property
    def firmware(self) -> str:
        """The firmware version the unit reports, e.g. ``"d14e"``."""
        return self._identity().app_fw_version

    @property
    def serial(self) -> str:
        """The unit's serial number."""
        return self._identity().device_serial_number

    def _identity(self):
        """The unit's Version reply, read once per connection.

        Firmware and serial cannot change while a connection is up - a firmware
        update reboots the unit, which ends the session - so one read answers
        both properties for as long as this `Device` is connected.
        """
        if self._version is None:
            self._version = self._client.version()
        return self._version

    def close(self) -> None:
        """Release the unit, if this `Device` opened it.

        Safe to call more than once. A `Device` built by :meth:`from_client` owns
        nothing, so this does nothing to the caller's connection.
        """
        if self._owns_client and self._client is not None:
            self._client.close()

    def __enter__(self) -> "Device":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __repr__(self) -> str:
        # Deliberately says nothing about the unit: repr() is called by
        # debuggers and logging, and must never trigger a device read.
        return f"<{type(self).__name__} connected={self._client is not None}>"


def connect(*, timeout: float = 5.0, settle: float = 2.0,
            handshake_patience: float = 30.0) -> Device:
    """Open a Quad Cortex over USB and return it as a :class:`Device`.

    Finds and opens the device, starts the transport, and performs the connect
    handshake the unit requires before it will act on commands and push state.

    Use it as a context manager so the unit is always released::

        with pyquadcortex.connect() as device:
            print(device.firmware)

    Otherwise call :meth:`Device.close` when done.

    Args:
        timeout: seconds to wait for each handshake reply.
        settle: seconds to wait after the handshake before returning.
        handshake_patience: total seconds to keep re-attempting the handshake
            while the unit is openable but silent. See
            :func:`pyquadcortex.protocol.connect`, which this passes through to.

    Returns:
        A connected :class:`Device`.

    Raises:
        DeviceNotFoundError: if no Quad Cortex could be opened.
    """
    client = protocol.connect(timeout=timeout, settle=settle,
                              handshake_patience=handshake_patience)
    return Device(client, _owns_client=True)
