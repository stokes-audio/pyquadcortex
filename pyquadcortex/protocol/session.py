"""Opening a connection to a Quad Cortex.

This is the protocol layer's front door: :func:`connect` finds the device, opens
it, starts the framed transport, performs the connect handshake, and hands back a
:class:`~pyquadcortex.protocol.client.QuadCortex` that is ready for commands. Callers
never deal with HID devices, vendor/product IDs, or the handshake themselves.

    from pyquadcortex import protocol

    with protocol.connect() as qc:
        print(qc.version())

For the model of the unit rather than the messages, use :func:`pyquadcortex.connect`.

An advanced caller who needs to supply their own device or transport (a test
double, a non-default HID backend) can still assemble the layers by hand -
see :class:`pyquadcortex.protocol.transport.Transport` and
:class:`pyquadcortex.protocol.client.QuadCortex`.
"""

import time

from pyquadcortex.protocol import hid_ids, profiles
from pyquadcortex.protocol.client import QuadCortex
from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa
from pyquadcortex.protocol.support import Support
from pyquadcortex.protocol.transport import Transport


class DeviceNotFoundError(RuntimeError):
    """No Quad Cortex could be opened over USB.

    The usual causes, in order of likelihood: Cortex Control is still running
    (it opens the interface exclusively), the unit is not connected by USB, or
    the hidapi library is missing. The message says which applies where it can
    be told apart.
    """


def open_device():
    """Open the Quad Cortex HID interface and return the raw device.

    Most callers want :func:`connect` instead. This is exposed for the rare case
    of wiring a custom transport around the device.

    Raises:
        DeviceNotFoundError: if the device cannot be opened.
    """
    try:
        import hid
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise DeviceNotFoundError(
            "the 'hid' package is not installed - install pyquadcortex's "
            "dependencies, and the hidapi C library (macOS: brew install hidapi)"
        ) from exc

    try:
        # The PyPI 'hid' package exposes hid.Device; the older 'hidapi'
        # package exposes hid.device(). Support both.
        if hasattr(hid, "Device"):
            return hid.Device(hid_ids.VENDOR_ID, hid_ids.PRODUCT_ID)
        dev = hid.device()
        dev.open(hid_ids.VENDOR_ID, hid_ids.PRODUCT_ID)
        return dev
    except Exception as exc:
        # Deliberately broad. The `hid` package raises hid.HIDException, which
        # inherits straight from Exception and NOT from OSError - so catching
        # OSError did nothing on the very path this library takes, and a new user
        # with no unit attached got a raw traceback instead of the guidance
        # below. Every way of failing to open the device means the same thing to
        # a caller, so they all become DeviceNotFoundError; the original is
        # chained for anyone who needs the detail.
        raise DeviceNotFoundError(
            "could not open the Quad Cortex over USB. Check that: Cortex "
            "Control is quit (it holds the USB interface exclusively), the "
            "unit is connected by USB, and it has finished booting. "
            "If it was working moments ago and none of those apply, the unit's "
            "USB link may have died mid-session - see the Troubleshooting "
            "section of the readme, since only a full power-down recovers it. "
            f"(underlying error: {type(exc).__name__}: {exc})"
        ) from exc


#: What went unanswered, for each phase's ``_patience_exhausted``. The guidance
#: is one sentence in one place (below); only the clause naming what this
#: library asked for differs, because "no answer at all" and "no answer
#: carrying what the registry resolves on" are different things to go and look
#: at on the unit.
_NO_HANDSHAKE = "the control protocol did not answer"
_NO_IDENTITY = ("the unit answered no Version carrying device_type and "
                "zenos_git_hash, the two fields a profile is resolved from")


def _patience_exhausted(attempt: int, handshake_patience: float,
                        unanswered: str = _NO_HANDSHAKE) -> TimeoutError:
    """The ``TimeoutError`` raised when ``handshake_patience`` elapses.

    Shared between the identity read and the handshake loop below (ADR-0020):
    both are just this library asking the control protocol something, and the
    measured 9-17s openable-but-silent window after a boot covers either one
    equally - so a caller gets the same guidance no matter which one hit it,
    from one place rather than two copies drifting apart. ``unanswered`` is the
    only part that differs, and it says which question went unanswered.
    """
    return TimeoutError(
        f"the device is enumerated and open but {unanswered} in {attempt} "
        f"handshake attempt(s) over {handshake_patience:.0f}s. This "
        f"openable-but-silent window has measured 9-17s after a "
        f"reboot or cold boot; if it persists far longer, see "
        f"the USB-link-death section of troubleshooting.md."
    )


def _retry_until_patient(attempt_fn, deadline: float, handshake_patience: float,
                         unanswered: str = _NO_HANDSHAKE):
    """Call ``attempt_fn()`` and retry it on ``TimeoutError`` until ``deadline``.

    ``deadline`` is computed once by the caller and shared across every phase
    retried this way: ``handshake_patience`` is a total budget for getting the
    unit to answer at all, not a per-phase allowance (ADR-0020).
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return attempt_fn()
        except TimeoutError:
            if time.monotonic() >= deadline:
                raise _patience_exhausted(
                    attempt, handshake_patience, unanswered) from None


def _resolves_a_profile(reply: pa.VersionMessage) -> bool:
    """Whether a ``Version`` reply carries what a profile is resolved from.

    ``version()`` accepts a reply carrying the serial OR the firmware, which is
    right for a caller and right for the cache rule - the unit answers a
    ``Version`` READ twice, and a partial answer is kept rather than thrown
    away (protocol.md section 4.4). Resolution is a stricter question: it needs
    ``device_type`` AND ``zenos_git_hash``, so a reply carrying only identity
    fields is not the answer to it and the wait goes on.
    """
    return reply.HasField("device_type") and reply.HasField("zenos_git_hash")


def _read_identity(transport, timeout: float) -> pa.VersionMessage:
    """Send a ``Version`` READ and wait for a reply a profile can be resolved from."""
    return transport.await_broadcast(
        pa.VersionMessage,
        lambda: transport.send(pa.VersionMessage(action=pa.MessageAction.READ)),
        timeout=timeout,
        match=_resolves_a_profile,
    )


def connect(*, timeout: float = 5.0, settle: float = 2.0,
            handshake_patience: float = 30.0,
            initial_file_listing: bool = True,
            before_handshake=None,
            profile: type[QuadCortex] | None = None,
            support: Support = Support.VERIFIED) -> QuadCortex:
    """Open a Quad Cortex and return a connected, ready-to-use client.

    Finds and opens the device, starts the transport, and performs the connect
    handshake the device requires before it will act on commands and push state.
    The returned :class:`~pyquadcortex.protocol.client.QuadCortex` is ready immediately.

    Use it as a context manager so the device is always released::

        with pyquadcortex.protocol.connect() as qc:
            qc.switch_scene(1)

    Otherwise call :meth:`~pyquadcortex.protocol.client.QuadCortex.close` when done.

    Args:
        timeout: seconds to wait for each handshake reply.
        settle: seconds to wait after the handshake before returning. The device
            needs a moment before it treats the client as connected; lowering
            this makes the first command less reliable.
        handshake_patience: total seconds to keep re-attempting the identity
            read and the handshake when the device is OPENABLE BUT SILENT. That
            window is real and varies: ~9-12 s post-enumeration in one
            session's measurements, and ~17 s in a live host-triggered reboot
            here - a successful open proves nothing about readiness, and a
            15 s budget was measured failing, which is why the default is 30.
            This is ONE budget for both phases, not one each: the identity read
            below is the first thing this library asks of the unit, and it
            lands in the same silent window as the handshake that follows it.
            Each attempt restarts fully (safe: the identity read is a bare READ,
            and the handshake begins with a fresh session id). Set to 0 for the
            old single-attempt behaviour.
        initial_file_listing: whether the handshake should immediately ask the
            unit to enumerate its full folder tree. Keep the default for the
            usual eager state burst; set this false when startup traffic matters
            more than preloading listings. Explicit calls such as
            :meth:`~pyquadcortex.protocol.client.QuadCortex.list_folders` still
            work and fetch the listing on demand.
        before_handshake: optional ``callable(transport)``, called once with the
            started :class:`~pyquadcortex.protocol.transport.Transport` after it
            starts and before the handshake runs. This is the only way to
            register a listener
            (:meth:`~pyquadcortex.protocol.transport.Transport.add_listener`)
            early enough to see the handshake's own burst of state, which
            delivers one message of nearly every state type the unit has - the
            cheapest way to learn what the unit is currently doing. Called once,
            not once per handshake attempt. An exception from it aborts the
            connect and releases the device, like any other bring-up failure.
        profile: a profile class to use instead of the one the unit's identity
            resolves to (ADR-0020). For measuring a unit this library has no
            profile for: the class's ``DEVICE_TYPE`` must still match what the
            unit reports, and ``MEASURED_ON`` is not checked. Combine with
            ``support=Support.EXPERIMENTAL`` to run operations the profile has
            not verified.
        support: how the connection treats an operation its profile has not
            verified. ``Support.VERIFIED`` (default) refuses it;
            ``Support.EXPERIMENTAL`` runs it with a warning. On ``QuadCortex``
            every operation is verified, so this changes nothing there.

    Returns:
        A connected :class:`~pyquadcortex.protocol.client.QuadCortex`.

    Raises:
        DeviceNotFoundError: if no Quad Cortex could be opened.
        TimeoutError: if ``handshake_patience`` runs out. Either the unit
            answered nothing at all, or - for the identity read - it answered
            no ``Version`` carrying both ``device_type`` and
            ``zenos_git_hash``; the message says which.
        UnsupportedDevice: if the unit reports a device type and CorOS version
            no profile has measured, or ``profile`` names a class for a
            different device type. Raised before the handshake; the device is
            released.
    """
    device = open_device()
    transport = Transport(device)
    # Tear down in reverse order, and only what we opened.
    owned = [device.close, transport.stop]
    try:
        transport.start()
        # Before the handshake, so a listener registered here sees the state
        # burst the handshake provokes rather than joining after it.
        if before_handshake is not None:
            before_handshake(transport)
        # One budget for the whole bring-up (ADR-0020): the identity read below
        # and the handshake that follows it both land in the same
        # openable-but-silent window, so they share one deadline rather than
        # each getting handshake_patience of their own.
        deadline = time.monotonic() + handshake_patience
        # Who are we talking to? Read before the handshake. This is a stricter
        # wait than version()'s: the unit answers a Version READ twice and a
        # reply may carry only the serial, which version() rightly accepts and
        # a profile cannot be resolved from. A partial answer is left unmatched
        # and the wait goes on, inside the same budget (ADR-0020).
        identity = _retry_until_patient(
            lambda: _read_identity(transport, timeout),
            deadline, handshake_patience, _NO_IDENTITY)
        if profile is None:
            cls = profiles.resolve(identity)
        else:
            cls = profile
            if identity.device_type != cls.DEVICE_TYPE:
                raise profiles.UnsupportedDevice(
                    identity.device_type, identity.zenos_git_hash,
                    f"you asked for {cls.__name__}, which serves "
                    f"{pa.VersionMessage.DeviceType.Name(cls.DEVICE_TYPE)}, and the unit "
                    f"says {pa.VersionMessage.DeviceType.Name(identity.device_type)}")
        qc = cls(transport, _owned_resources=owned, support=support)
        _retry_until_patient(lambda: qc._hello(
            timeout=timeout, settle=settle,
            initial_file_listing=initial_file_listing),
                              deadline, handshake_patience)
        # Say goodbye BEFORE the transport and handle go away, since the send needs
        # a live transport. close() pops this list, so appending last runs it first.
        owned.append(qc.disconnect)
        return qc
    except BaseException:
        # Never leak the device if bring-up fails part-way.
        for closer in reversed(owned):
            try:
                closer()
            except Exception:  # pragma: no cover - best-effort teardown
                pass
        raise
