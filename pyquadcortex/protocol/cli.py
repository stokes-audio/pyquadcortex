"""Command-line entry point for qcctl.

Wires argparse subcommands to :class:`pyquadcortex.protocol.client.QuadCortex` methods over a
real hidapi transport. Declared in ``pyproject.toml`` as ``pyquadcortex.protocol.cli:main``.

Testability contract: ``import pyquadcortex.protocol.cli`` and :func:`build_parser` MUST stay
device-free - they must not import hidapi or open a device. Device opening is
deferred to :mod:`pyquadcortex.protocol.session`, whose ``import hid`` is itself lazy, so
the parser stays fully unit-testable without hardware or the native libhidapi
library present.
"""

import argparse

from pyquadcortex.protocol import client
from pyquadcortex.protocol.enums import Setlist
from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa


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
        default=Setlist.USER,
        help="Device path of the setlist (default: the user 'My Presets' setlist, "
        f"{str(Setlist.USER)!r}).",
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
        default=Setlist.USER,
        help=f"Device path of the setlist (default: {str(Setlist.USER)!r}).",
    )
    d.add_argument("--slot", required=True, help="Slot name like 28C.")

    return p


def _open_unconnected():
    """Open the device and start the transport WITHOUT the connect handshake.

    Only the ``version`` subcommand wants this: a plain Version READ works
    without the connect gate, and the handshake's own version announce would
    race that READ's reply (READ replies carry no request_id to disambiguate).
    Everything else goes through :func:`pyquadcortex.protocol.connect`.
    """
    from pyquadcortex.protocol import session, transport

    device = session.open_device()
    # Leave the device in BLOCKING mode: transport._read_loop paces itself on the
    # blocking read(1024, timeout=200), blocking up to 200ms per iteration and
    # re-checking _running. Setting nonblocking=True would make read() return
    # immediately, turning the RX thread into a 100% CPU busy-spin whenever the
    # (normally quiet) device emits nothing.
    try:
        t = transport.Transport(device)
        t.start()
        return device, t
    except Exception:
        device.close()
        raise


def main(argv=None):
    """Parse ``argv`` and dispatch to the matching client method."""
    ns = build_parser().parse_args(argv)
    from pyquadcortex.protocol import session

    try:
        if ns.command == "version":
            device, t = _open_unconnected()
            try:
                print(client.QuadCortex(t).version())
            finally:
                t.stop()
                device.close()
            return
        # Everything else needs the device fully connected so it acts on
        # commands and pushes state; connect() handles that and cleans up.
        with session.connect() as qc:
            if ns.command == "recall":
                qc.recall_preset(ns.setlist, client.slot_to_position(ns.slot))
            elif ns.command == "scene":
                qc.switch_scene(ns.index)
            elif ns.command == "dump-preset":
                print(qc.read_preset(ns.setlist, client.slot_to_position(ns.slot)))
    except session.DeviceNotFoundError as e:
        raise SystemExit(str(e))


if __name__ == "__main__":
    main()
