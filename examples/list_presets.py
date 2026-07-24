#!/usr/bin/env python3
"""List the presets in a setlist, with their instrument tags.

A File READ makes the device push its folder listings (each `FolderInfo.files`
entry is a `ProductData` with index / name / instrument). This waits for the
requested folder's listing and prints every preset, plus a histogram of the
instrument tags (guitar=1, bass=2, vocal=4).

Usage (defaults to the factory library; pass `user` for My Presets):

    python examples/list_presets.py
    python examples/list_presets.py user

Prerequisites: Quad Cortex on USB, Cortex Control quit, and on macOS the
`DYLD_LIBRARY_PATH=/opt/homebrew/lib` prefix (see the README).
"""

import collections
import sys

from pyquadcortex import client, hid_ids, transport
from pyquadcortex.proto import ProductionAutomation_pb2 as pa


def open_device():
    """Open the Quad Cortex, tolerating both `hid` package API variants."""
    import hid

    if hasattr(hid, "Device"):
        return hid.Device(hid_ids.VENDOR_ID, hid_ids.PRODUCT_ID)
    dev = hid.device()
    dev.open(hid_ids.VENDOR_ID, hid_ids.PRODUCT_ID)
    return dev


def main():
    want_user = len(sys.argv) > 1 and sys.argv[1] == "user"
    folder_key = client.USER_PRESETS_PATH if want_user else client.FACTORY_LIBRARY_PATH

    dev = open_device()
    t = transport.Transport(dev)
    t.start()
    qc = client.QuadCortex(t)
    try:
        qc.hello()
        print(f"Requesting File listing; waiting for {folder_key!r} ...")
        listing = t.await_broadcast(
            pa.FileMessage,
            lambda: t.send(pa.FileMessage(action=pa.MessageAction.READ)),
            timeout=25.0,
            match=lambda m: m.folder.key.startswith(folder_key) and len(m.folder.files) > 0,
        )
        folder = listing.folder
        files = list(folder.files)
        print(f"folder.key={folder.key!r} is_factory={folder.is_factory} files={len(files)}\n")

        by_instrument = collections.Counter()
        rows = []
        for pd in files:
            idx = pd.index if pd.HasField("index") else None
            name = pd.name if pd.HasField("name") else "(unnamed)"
            instr = pd.instrument if pd.HasField("instrument") else None
            by_instrument[instr] += 1
            rows.append((idx, instr, name))

        print("=== presets (index, instrument, name) ===")
        for idx, instr, name in sorted(rows, key=lambda r: (r[0] is None, r[0])):
            print(f"  {idx:>4}  instr={instr}  {name}")

        print("\n=== instrument tag histogram ===")
        for instr, n in sorted(by_instrument.items(), key=lambda x: (x[0] is None, x[0])):
            print(f"  instrument={instr}: {n} presets")
    finally:
        t.stop()
        dev.close()


if __name__ == "__main__":
    main()
