"""Command-line entry point for qcctl.

Wires argparse subcommands to :class:`pyquadcortex.client.QuadCortex` methods over a
real hidapi transport. Declared in ``pyproject.toml`` as ``pyquadcortex.cli:main``.

Testability contract: ``import pyquadcortex.cli`` and :func:`build_parser` MUST stay
device-free - they must not import hidapi or open a device. The ``import hid``
therefore lives lazily inside :func:`_connect` (called from :func:`main`), never
at module top level. This keeps the parser fully unit-testable without hardware
or the native libhidapi library present.
"""

import argparse

from pyquadcortex import client
from pyquadcortex.proto import ProductionAutomation_pb2 as pa


def build_parser() -> argparse.ArgumentParser:
    """Build the qcctl argument parser.

    Import-safe and device-free: constructs no transport and imports no hidapi,
    so it can be exercised directly in tests.
    """
    p = argparse.ArgumentParser(prog="qcctl", description="Control a Neural DSP Quad Cortex over USB-HID.")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser(
        "recall",
        help="Recall a preset by slot (e.g. 28C) within a setlist.",
    )
    r.add_argument(
        "--setlist",
        default=client.USER_PRESETS_PATH,
        help="Device path of the setlist (default: the user 'My Presets' setlist, "
        f"{client.USER_PRESETS_PATH!r}).",
    )
    r.add_argument(
        "--slot",
        required=True,
        help="Slot name like 28C (bank number + letter A-H).",
    )

    s = sub.add_parser("scene", help="Switch the active scene.")
    s.add_argument("--index", type=int, required=True)

    sub.add_parser("version", help="Read the device firmware version.")

    d = sub.add_parser(
        "dump-preset",
        help="Recall a preset slot and print the full BinaryPreset it loads.",
    )
    d.add_argument(
        "--setlist",
        default=client.USER_PRESETS_PATH,
        help=f"Device path of the setlist (default: {client.USER_PRESETS_PATH!r}).",
    )
    d.add_argument("--slot", required=True, help="Slot name like 28C.")

    return p


def _connect():
    """Open the device and return ``(device, transport, QuadCortex)``.

    The ``import hid`` is lazy so that :func:`build_parser` and the tests do not
    require hidapi. On macOS the Homebrew libhidapi may not be on the default
    dyld path; surface a clear, actionable message instead of a raw ctypes error.
    """
    try:
        import hid
    except Exception as e:
        raise SystemExit(
            "Failed to load hidapi. Install it (macOS: `brew install hidapi`) "
            "and, if needed, set DYLD_LIBRARY_PATH=/opt/homebrew/lib "
            "(or /usr/local/lib on Intel Macs). "
            f"Underlying error: {e}"
        )
    from pyquadcortex import hid_ids, transport

    # Two PyPI packages expose a module named ``hid`` with slightly different
    # APIs: ``hid`` (ctypes, used on macOS) has ``hid.Device(vid, pid)``;
    # ``hidapi`` (Cython, used on Windows) has ``hid.device()`` + ``open()``.
    # Both expose the same read/write/close surface the transport needs.
    if hasattr(hid, "Device"):
        dev = hid.Device(hid_ids.VENDOR_ID, hid_ids.PRODUCT_ID)
    else:
        dev = hid.device()
        dev.open(hid_ids.VENDOR_ID, hid_ids.PRODUCT_ID)
    # Leave the device in BLOCKING mode: transport._read_loop paces itself on the
    # blocking read(1024, timeout=200), blocking up to 200ms per iteration and
    # re-checking _running. Setting nonblocking=True would make read() return
    # immediately, turning the RX thread into a 100% CPU busy-spin whenever the
    # (normally quiet) device emits nothing.
    #
    # If any post-open step raises, main()'s try/finally never receives the
    # (dev, t, qc) tuple, so it can't close the handle. Close it here before
    # re-raising to avoid leaking the open device.
    try:
        t = transport.Transport(dev)
        t.start()
        return dev, t, client.QuadCortex(t)
    except Exception:
        dev.close()
        raise


def main(argv=None):
    """Parse ``argv`` and dispatch to the matching client method."""
    ns = build_parser().parse_args(argv)
    dev, t, qc = _connect()
    try:
        if ns.command == "version":
            # A plain Version READ works without the full connect gate, and
            # issuing hello()'s version announce first would race this READ's
            # reply (READ replies carry no request_id). So: no hello here.
            print(t.request(pa.VersionMessage(action=pa.MessageAction.READ)))
            return
        # Everything else needs the device fully "connected" so it acts on
        # commands and pushes state (framing_spec.md "Third capture").
        qc.hello()
        if ns.command == "recall":
            qc.recall_preset(ns.setlist, client.slot_to_position(ns.slot))
        elif ns.command == "scene":
            qc.switch_scene(ns.index)
        elif ns.command == "dump-preset":
            print(qc.read_preset(ns.setlist, client.slot_to_position(ns.slot)))
    finally:
        t.stop()
        dev.close()


if __name__ == "__main__":
    main()
