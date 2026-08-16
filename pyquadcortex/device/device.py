"""The unit itself, as an object.

:func:`connect` is the library's front door: it opens the Quad Cortex over USB
and hands back a :class:`Device`::

    import pyquadcortex

    with pyquadcortex.connect() as device:
        print(device.firmware, device.serial)

Everything a `Device` reports comes through the state layer
(:mod:`pyquadcortex.device.state`), which listens to what the unit announces and
asks it directly for the rest. So a value read here is what the unit is doing
now, not what it was doing when somebody last asked.

The `Device` is deliberately small right now. It carries the unit's identity and
owns the connection; the Directory, the loaded preset and the grid arrive in the
stories that follow, per ``docs/domain-model.md``. What is here is what has been
built - nothing is stubbed out to look finished.
"""

from pyquadcortex import protocol
from pyquadcortex.device.preset import Preset
from pyquadcortex.device.state import DeviceState


class Device:
    """One connected Quad Cortex.

    Build one by calling :func:`pyquadcortex.connect`, or by wrapping a protocol
    connection you already hold with :meth:`from_client`.
    """

    def __init__(self, client, *, _owns_client: bool = False, _state=None):
        """Internal. Use :func:`connect` or :meth:`from_client`."""
        self._client = client
        self._owns_client = _owns_client
        self._closed = False
        # `connect` builds the cache itself so it can subscribe before the
        # handshake, which is the only moment early enough to hear the burst.
        # Anything else is joining a connection already up, so it subscribes to
        # the client and starts cold.
        self._state = _state if _state is not None else DeviceState()
        if _state is None:
            self._state.listen_on(client)
        self._state.bind(client)
        self._preset = None

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
    def state(self):
        """The state layer this `Device` reads through.

        The place to look when you want to know what the model knows - what it
        has cached, what it is about to re-read - rather than what the unit is
        doing. Reading a property is the way to ask the unit.

        Raises ``RuntimeError`` once this `Device` is closed.
        """
        self._check_open()
        return self._state

    @property
    def preset(self) -> Preset:
        """The preset on the grid right now.

        Never ``None`` on a connected device: the unit always has one loaded.

        The object is rebuilt when the unit loads a DIFFERENT preset, so this
        always hands back the current one - and a `Preset` somebody held across
        that recall reports `is_current` False rather than quietly describing the
        preset that used to be there (``docs/domain-model.md`` section 12).

        An EDIT does not rebuild it. The preset is still the same preset; only
        the model's copy of its contents is behind, and putting that right is
        the state layer's job rather than this object's.

        The first access on a connection this `Device` did not open may read the
        loaded slot from the unit, which takes about 3 ms. On one opened by
        `connect` the handshake's burst has already delivered it.
        """
        self._check_open()
        # Read through `value` first so a cold cache asks the unit, then take
        # the whole entry: `is_current` compares the slot as a whole, and a
        # Preset built from an empty one would call itself current forever.
        self._state.value("loaded", "position")
        loaded = self._state.cached("loaded")
        if self._preset is None or self._preset._loaded != loaded:
            self._preset = Preset(self, loaded)
        return self._preset

    @property
    def events(self):
        """Subscribe here to hear what the model noticed.

        :class:`~pyquadcortex.device.events.Changed` when a push moves a value
        the model holds, and
        :class:`~pyquadcortex.device.events.Invalidated` when it stops trusting
        part of its copy - which is how a script following the unit closely
        learns that the grid moved without waiting for somebody to read a
        property::

            def watch(event):
                print(event)

            device.events.subscribe(watch)

        A subscriber runs on a thread the model owns and MAY read from the
        device, which is the whole reason that thread exists.
        """
        self._check_open()
        return self._state.events

    @property
    def firmware(self) -> str:
        """The firmware version the unit reports, e.g. ``"d14e"``.

        The unit never announces this, so the first read asks it and every read
        after that is free for as long as this connection lasts. Firmware and
        serial cannot change while a connection is up: the only thing that
        changes either is a firmware update, and the firmware `Updater` surface
        is permanently out of scope for this library (repo-root ``CLAUDE.md``).
        That is an inference from scope rather than a measurement, which is why
        the state layer still treats it as ordinary cached state rather than as
        something read once and settled.
        """
        return self._identity("app_fw_version")

    @property
    def serial(self) -> str:
        """The unit's serial number."""
        return self._identity("device_serial_number")

    def _identity(self, field: str) -> str:
        """One field of the unit's identity, through the cache.

        Both fields sit in a synthetic ``oneof`` in the schema, so protobuf
        hands back ``""`` for a field the unit never sent rather than
        complaining. An empty string behind a signature promising a version is a
        guess, so the state layer refuses a field the unit did not send and
        caches nothing for it, leaving a retry able to recover.
        """
        self._check_open()
        return self._state.value("identity", field)

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

        The state layer is closed first, so a `Device` built by
        :meth:`from_client` stops listening on a connection it never owned
        rather than quietly staying subscribed to somebody else's.
        """
        self._closed = True
        # Dropped rather than left in place: a closed Device holding a Preset
        # whose every property raises is a live-looking object with nothing
        # behind it.
        self._preset = None
        self._state.close()
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

    The model subscribes to the unit's pushes BEFORE the handshake runs, which
    is the only moment early enough to hear the handshake's own burst of state -
    one message of nearly every state type the unit has, the current preset
    included, arriving over about nine seconds. That is what makes the cache warm
    for free: by the time a caller asks for something, the unit has usually
    already said it.

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
    state = DeviceState()
    client = protocol.connect(timeout=timeout, settle=settle,
                              handshake_patience=handshake_patience,
                              before_handshake=state.listen_on)
    return Device(client, _owns_client=True, _state=state)
