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

from pyquadcortex.protocol import hid_ids
from pyquadcortex.protocol.client import QuadCortex
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


def connect(*, timeout: float = 5.0, settle: float = 2.0,
            handshake_patience: float = 30.0,
            initial_file_listing: bool = True,
            before_handshake=None) -> QuadCortex:
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
        handshake_patience: total seconds to keep re-attempting the handshake
            when the device is OPENABLE BUT SILENT. That window is real and
            varies: ~9-12 s post-enumeration in one session's measurements, and
            ~17 s in a live host-triggered reboot here - a successful open
            proves nothing about readiness, and a 15 s budget was measured
            failing, which is why the default is 30. Each attempt restarts the
            full handshake (safe: it begins with a fresh session id). Set to 0
            for the old single-attempt behaviour.
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

    Returns:
        A connected :class:`~pyquadcortex.protocol.client.QuadCortex`.

    Raises:
        DeviceNotFoundError: if no Quad Cortex could be opened.
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
        qc = QuadCortex(transport, _owned_resources=owned)
        deadline = time.monotonic() + handshake_patience
        attempt = 0
        while True:
            attempt += 1
            try:
                qc._hello(
                    timeout=timeout,
                    settle=settle,
                    initial_file_listing=initial_file_listing,
                )
                break
            except TimeoutError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"the device is enumerated and open but the control "
                        f"protocol did not answer in {attempt} handshake "
                        f"attempt(s) over {handshake_patience:.0f}s. This "
                        f"openable-but-silent window has measured 9-17s after a "
                        f"reboot or cold boot; if it persists far longer, see "
                        f"the USB-link-death section of troubleshooting.md."
                    ) from None
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
