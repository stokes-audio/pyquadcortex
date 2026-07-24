#!/usr/bin/env python3
"""Re-route a preset's input and save it to a user slot (the edit-path pattern).

This demonstrates the one non-obvious pattern for editing presets. The device
applies grid edits by row/column key and then saves whatever is on the grid, so
an edit is always:

    recall (loads the preset onto the grid)
      -> row-keyed edit(s): set_chain_input / set_param / set_bypass
      -> save_current_preset (snapshots the grid into a slot)

Here it recalls a factory preset, re-points its Input-1 chains to another input
port, and saves the result as a user preset.

SAFETY: saving writes to a user slot and OVERWRITES whatever is there. This
script is a DRY RUN by default - it recalls and prints the plan without saving.
Pass --write to actually save, and set DEST_SLOT below to a slot you are happy
to overwrite.

Usage (Quad Cortex on USB, Cortex Control quit; macOS needs the dyld prefix):

    python examples/reroute_and_save.py            # dry run, no changes
    python examples/reroute_and_save.py --write     # actually saves
"""

import sys
import time

from pyquadcortex import client, hid_ids, transport

# --- edit these to taste -----------------------------------------------------
SOURCE_POSITION = 212            # factory "Cali Basswalk" (see list_presets.py)
DEST_SLOT = "27A"                # user slot to overwrite when --write is given
DEST_NAME = "Cali Basswalk [Ret1]"
TO_PORT = client.RETURN_1        # re-point Input 1 chains to Return 1
INSTRUMENT = client.INSTRUMENT_BASS
# -----------------------------------------------------------------------------


def open_device():
    """Open the Quad Cortex, tolerating both `hid` package API variants."""
    import hid

    if hasattr(hid, "Device"):
        return hid.Device(hid_ids.VENDOR_ID, hid_ids.PRODUCT_ID)
    dev = hid.device()
    dev.open(hid_ids.VENDOR_ID, hid_ids.PRODUCT_ID)
    return dev


def _ports(preset):
    return [c.in_portid for c in preset.chains if c.HasField("in_portid")]


def main():
    do_write = "--write" in sys.argv[1:]
    dest_pos = client.slot_to_position(DEST_SLOT)

    dev = open_device()
    t = transport.Transport(dev)
    t.start()
    qc = client.QuadCortex(t)
    try:
        qc.hello()

        # 1. Recall the factory preset - this loads it onto the grid.
        p = qc.read_preset(client.FACTORY_LIBRARY_PATH, SOURCE_POSITION, is_factory=True)
        rows = client.input_chain_rows(p, client.INPUT_1)
        print(f"Recalled factory position {SOURCE_POSITION}: {p.name!r}")
        print(f"  current input ports {_ports(p)}; Input-1 rows = {rows}")
        print(f"  plan: re-point those rows to port {TO_PORT}, save to "
              f"{DEST_SLOT} (pos {dest_pos}) as {DEST_NAME!r}")

        if not do_write:
            print("\nDRY RUN - no changes made. Re-run with --write to save.")
            return 0

        # 2. Re-point each Input-1 chain on the grid (row-keyed sparse update).
        for r in rows:
            qc.set_chain_input(r, TO_PORT)
        time.sleep(2.0)

        # 3. Snapshot the grid into the destination user slot.
        qc.save_current_preset(client.USER_PRESETS_PATH, dest_pos, DEST_NAME,
                               instrument=INSTRUMENT)
        time.sleep(2.0)

        # 4. Verify by recalling the saved slot.
        back = qc.read_preset(client.USER_PRESETS_PATH, dest_pos)
        got = _ports(back)
        name = back.name if back.HasField("name") else "(unnamed)"
        ok = TO_PORT in got and client.INPUT_1 not in got
        print(f"\nSaved. Verify: name={name!r} ports={got} -> {'OK' if ok else 'FAIL'}")
        return 0 if ok else 1
    finally:
        t.stop()
        dev.close()


if __name__ == "__main__":
    sys.exit(main())
