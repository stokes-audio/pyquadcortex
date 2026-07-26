"""Opening a connection to a Quad Cortex.

This is the front door: :func:`connect` finds the device, opens it, starts the
framed transport, performs the connect handshake, and hands back a
:class:`~pyquadcortex.client.QuadCortex` that is ready for commands. Callers
never deal with HID devices, vendor/product IDs, or the handshake themselves.

    import pyquadcortex

    with pyquadcortex.connect() as qc:
        print(qc.version())

An advanced caller who needs to supply their own device or transport (a test
double, a non-default HID backend) can still assemble the layers by hand -
see :class:`pyquadcortex.transport.Transport` and
:class:`pyquadcortex.client.QuadCortex`.
"""

from pyquadcortex import hid_ids
from pyquadcortex.client import QuadCortex
from pyquadcortex.transport import Transport


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
            f"(underlying error: {type(exc).__name__}: {exc})"
        ) from exc


def connect(*, timeout: float = 5.0, settle: float = 2.0) -> QuadCortex:
    """Open a Quad Cortex and return a connected, ready-to-use client.

    Finds and opens the device, starts the transport, and performs the connect
    handshake the device requires before it will act on commands and push state.
    The returned :class:`~pyquadcortex.client.QuadCortex` is ready immediately.

    Use it as a context manager so the device is always released::

        with pyquadcortex.connect() as qc:
            qc.switch_scene(1)

    Otherwise call :meth:`~pyquadcortex.client.QuadCortex.close` when done.

    Args:
        timeout: seconds to wait for the handshake reply.
        settle: seconds to wait after the handshake before returning. The device
            needs a moment before it treats the client as connected; lowering
            this makes the first command less reliable.

    Returns:
        A connected :class:`~pyquadcortex.client.QuadCortex`.

    Raises:
        DeviceNotFoundError: if no Quad Cortex could be opened.
    """
    device = open_device()
    transport = Transport(device)
    # Tear down in reverse order, and only what we opened.
    owned = [device.close, transport.stop]
    try:
        transport.start()
        qc = QuadCortex(transport, _owned_resources=owned)
        qc._hello(timeout=timeout, settle=settle)
        return qc
    except BaseException:
        # Never leak the device if bring-up fails part-way.
        for closer in reversed(owned):
            try:
                closer()
            except Exception:  # pragma: no cover - best-effort teardown
                pass
        raise
