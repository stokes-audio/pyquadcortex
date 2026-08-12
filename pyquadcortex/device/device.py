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
        self._closed = False
        self._version = None

    def _check_open(self) -> None:
        """Refuse to answer through a `Device` the caller has finished with.

        Without this, `close()` sets a flag nothing reads: a `Device` that had
        already cached its identity would keep answering `firmware` and `serial`
        from that cache with no device behind it, and one built by
        :meth:`from_client` would keep reading live through a connection this
        object no longer claims. Both report the unit's state through an object
        that has none, which is the failure ``__repr__`` below refuses to make.
        """
        if self._closed:
            raise RuntimeError(
                "this Device is closed - open a new one with "
                "pyquadcortex.connect(), or build one on a live protocol "
                "connection with Device.from_client()"
            )

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

        Fetch it where you use it rather than stashing it in a long-lived
        variable. Raises ``RuntimeError`` once this `Device` is closed.
        """
        self._check_open()
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

        Firmware and serial cannot change while a connection is up, so one read
        answers both properties for as long as this `Device` is connected. That
        rests on an inference, not a measurement: the only thing that changes
        either value is a firmware update, and the firmware `Updater` surface is
        permanently out of scope for this library (repo-root ``CLAUDE.md``), so
        the claim cannot be tested here. It is why the reply is only cached once
        it is known to be complete.

        Both fields sit in a synthetic ``oneof`` in the schema, so protobuf hands
        back ``""`` for a field the unit never sent rather than complaining. An
        empty string behind a signature promising a version is a guess, so a
        reply missing either field raises and is not cached, leaving a retry able
        to recover.
        """
        self._check_open()
        if self._version is None:
            reply = self._client.version()
            missing = [f for f in ("app_fw_version", "device_serial_number")
                       if not protocol.field_present(reply, f)]
            if missing:
                raise RuntimeError(
                    f"the unit's Version reply did not carry {', '.join(missing)}, "
                    f"so its firmware and serial cannot be reported. Nothing was "
                    f"cached, so asking again can still succeed."
                )
            self._version = reply
        return self._version

    def close(self) -> None:
        """Finish with this `Device`, releasing the unit if it opened it.

        Safe to call more than once. A `Device` built by :meth:`from_client` owns
        nothing, so this marks the `Device` done and leaves the caller's
        connection open for them to close.

        After this, `firmware`, `serial` and `client` raise ``RuntimeError``
        immediately, whichever way the `Device` was built. That is the only thing
        `close()` defines. A connection that goes away on its own - the cable
        pulled, the unit rebooted - is a different event with its own handling,
        and belongs to the reconnect story (#15).
        """
        self._closed = True
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "Device":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __repr__(self) -> str:
        # Says nothing about the unit, only about this object. repr() is called
        # by debuggers and logging and must never trigger a device read - and a
        # model that reports itself wrongly is the one thing this library
        # cannot do. It names ownership because the same type releases the unit
        # or does not depending on how it was built, and that is otherwise
        # invisible from the outside.
        state = "closed" if self._closed else "open"
        owning = "owns" if self._owns_client else "borrows"
        return f"<{type(self).__name__} {state}, {owning} its connection>"


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
        TimeoutError: if the unit opened but never answered the handshake within
            ``handshake_patience``. A unit that has just booted is enumerated and
            openable for 9 to 17 seconds before its control protocol answers, so
            this is the failure to expect when a script starts alongside the unit
            rather than after it. Raising ``handshake_patience`` is the fix; the
            30 second default already covers the measured window.
    """
    client = protocol.connect(timeout=timeout, settle=settle,
                              handshake_patience=handshake_patience)
    return Device(client, _owns_client=True)
