#!/usr/bin/env python3
"""Switch scenes on a connected Quad Cortex so you can watch it change.

The simplest end-to-end example: open the device, run the required `hello()`
handshake, then cycle through a few scenes with a pause between each.

Prerequisites:
  * The Quad Cortex connected over USB (not Wi-Fi).
  * Cortex Control quit - it opens the HID interface exclusively.
  * macOS: prefix the command with `DYLD_LIBRARY_PATH=/opt/homebrew/lib` so the
    `hid` package can find libhidapi (see the README).

    python examples/switch_scenes.py
"""

import time

from pyquadcortex import client, hid_ids, transport


def open_device():
    """Open the Quad Cortex, tolerating both `hid` package API variants."""
    import hid

    if hasattr(hid, "Device"):
        return hid.Device(hid_ids.VENDOR_ID, hid_ids.PRODUCT_ID)
    dev = hid.device()
    dev.open(hid_ids.VENDOR_ID, hid_ids.PRODUCT_ID)
    return dev


def main():
    dev = open_device()
    t = transport.Transport(dev)
    t.start()
    qc = client.QuadCortex(t)
    try:
        qc.hello()  # REQUIRED connect gate - always first.
        print(">>> Look at the Quad Cortex screen. Starting in:")
        for n in (3, 2, 1):
            print(f"      {n} ...")
            time.sleep(1.0)
        # (scene_index, label) - scenes are 0-based (A=0, B=1, ...).
        sequence = [(1, "B"), (2, "C"), (3, "D"), (0, "A")]
        for idx, letter in sequence:
            qc.switch_scene(idx)
            print(f"      -> scene {letter} (index {idx})")
            time.sleep(2.5)
        print(">>> Done. The active scene should have moved B, C, D, A.")
    finally:
        t.stop()
        dev.close()


if __name__ == "__main__":
    main()
