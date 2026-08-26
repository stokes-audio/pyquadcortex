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
    """No Quad Cortex or Quad Cortex Mini could be opened over USB.

    The usual causes, in order of likelihood: Cortex Control is still running
    (it opens the interface exclusively), the unit is not connected by USB, or
    the hidapi library is missing. The message says which applies where it can
    be told apart.
    """


def _looks_like_ours(info):
    """True if this hid.enumerate() dict is a Quad Cortex or Quad Cortex Mini.

    Match the Neural DSP vendor ID when it is filled in. On macOS hidapi some
    interfaces report vendor_id 0, so also accept a product or manufacturer
    string that names the unit - otherwise a Mini that enumerated namelessly
    under a non-0x880A PID would be the only device we could have opened, and
    we would skip it.
    """
    if info.get("vendor_id") == hid_ids.VENDOR_ID:
        return True
    blob = (
        f"{info.get('manufacturer_string') or ''} "
        f"{info.get('product_string') or ''}"
    ).lower()
    return "neural dsp" in blob or "quad cortex" in blob


def _enumerate(hid_mod):
    """Neural DSP HID interfaces the backend can see, or empty if it cannot list.

    Lists every HID device the backend reports, then keeps ours. Filtering by
    vendor ID inside hidapi would miss a Mini whose vendor_id came back as 0
    (macOS does that for some interfaces). ``hid.enumerate`` is optional: the
    older ``hidapi`` package and a stub that only implements ``Device(vid, pid)``
    still go through the direct-open fallback in :func:`open_device`.
    """
    enumerate = getattr(hid_mod, "enumerate", None)
    if enumerate is None:
        return []
    try:
        found = list(enumerate() or [])
    except TypeError:
        found = []
    if not found:
        try:
            found = list(enumerate(hid_ids.VENDOR_ID, 0) or [])
        except TypeError:
            try:
                found = list(enumerate(hid_ids.VENDOR_ID) or [])
            except TypeError:
                found = []
    return [info for info in found if _looks_like_ours(info)]


def _control_interfaces(infos):
    """HID interfaces that look like the Quad Cortex control pipe.

    Cortex Control talks to both Quad Cortex (``DeviceType.QC``) and Quad Cortex
    Mini (``DeviceType.ATMA``) over the same protocol. Mini may enumerate under a
    product ID this library has not read off hardware, so matching is by vendor
    (or name) and - when the backend reports it - the control usage page, not by
    a single PID.

    A usage page of 0 is treated as "not reported" (Linux hidraw often leaves it
    blank) rather than as a mismatch, because the Quad Cortex's own usage is
    already 0 and cannot be used as a filled-in signal.
    """
    ours = [info for info in infos if _looks_like_ours(info)]
    if not ours:
        return []
    # usage_page 0 means "not reported" on some backends (Linux hidraw), and
    # is also the Quad Cortex's real usage, so it cannot mean "not this
    # interface". A filled-in page that is not the control page is some other
    # Neural DSP HID surface; skip it when a control-looking interface exists.
    control = [
        info for info in ours
        if (info.get("usage_page") or 0) in (0, hid_ids.USAGE_PAGE)
    ]
    return control or ours


def _open_info(hid_mod, info):
    """Open one enumerated interface, preferring its path when the backend has one."""
    path = info.get("path")
    vendor_id = info.get("vendor_id", hid_ids.VENDOR_ID)
    product_id = info.get("product_id")
    if hasattr(hid_mod, "Device"):
        if path:
            return hid_mod.Device(path=path)
        return hid_mod.Device(vendor_id, product_id)
    dev = hid_mod.device()
    if path:
        dev.open_path(path)
    else:
        dev.open(vendor_id, product_id)
    return dev


def _open_vid_pid(hid_mod, vendor_id, product_id):
    """The original one-PID open, used when enumerate sees nothing."""
    if hasattr(hid_mod, "Device"):
        return hid_mod.Device(vendor_id, product_id)
    dev = hid_mod.device()
    dev.open(vendor_id, product_id)
    return dev


def _not_found(exc, *, seen):
    """Build DeviceNotFoundError. ``seen`` is the Neural DSP HID dicts enumerate returned.

    The 0x880A fallback raises 'No HID devices with requested VID/PID found' even
    when the real problem is 'nothing Neural DSP is plugged in' (a Mini is not
    0x880A). Saying that first is the difference between a Mini user reseating a
    charge-only USB-C cable and one thinking this library only opens Quad Cortex.
    """
    if seen:
        pids = ", ".join(
            f"{info['product_id']:#06x}" if info.get("product_id") is not None
            else "?"
            for info in seen
        )
        detail = (
            f"hidapi enumerated {len(seen)} Neural DSP HID interface(s) "
            f"(product id {pids}) but none could be opened"
        )
    else:
        detail = (
            "hidapi does not currently see any Neural DSP HID interface. "
            "A Mini that is only charging will not appear here - it needs a "
            "data-capable USB-C cable"
        )
    return DeviceNotFoundError(
        f"could not open a Quad Cortex or Quad Cortex Mini over USB: {detail}. "
        "Check that: Cortex Control is quit (it holds the USB interface "
        "exclusively), the unit is connected by USB, and it has finished "
        "booting. If it was working moments ago and none of those apply, the "
        "unit's USB link may have died mid-session - see the Troubleshooting "
        "section of the readme, since only a full power-down recovers it. "
        f"(underlying error: {type(exc).__name__}: {exc})"
    )


def open_device():
    """Open the Quad Cortex HID interface and return the raw device.

    Discovers any Neural DSP HID control interface - Quad Cortex (product
    ``0x880A``) or Quad Cortex Mini, whose product ID may differ. Most callers
    want :func:`connect` instead. This is exposed for the rare case of wiring a
    custom transport around the device.

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

    last_error = None
    candidates = _control_interfaces(_enumerate(hid))
    for info in candidates:
        try:
            return _open_info(hid, info)
        except Exception as exc:
            last_error = exc

    try:
        return _open_vid_pid(hid, hid_ids.VENDOR_ID, hid_ids.PRODUCT_ID)
    except Exception as exc:
        # Deliberately broad. The `hid` package raises hid.HIDException, which
        # inherits straight from Exception and NOT from OSError - so catching
        # OSError did nothing on the very path this library takes, and a new user
        # with no unit attached got a raw traceback instead of the guidance
        # below. Every way of failing to open the device means the same thing to
        # a caller, so they all become DeviceNotFoundError; the original is
        # chained for anyone who needs the detail.
        raise _not_found(last_error or exc, seen=candidates) from (
            last_error or exc
        )


def connect(*, timeout: float = 5.0, settle: float = 2.0,
            handshake_patience: float = 30.0,
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
        DeviceNotFoundError: if no Quad Cortex or Quad Cortex Mini could be opened.
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
                qc._hello(timeout=timeout, settle=settle)
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
