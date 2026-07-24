#!/usr/bin/env python3
"""Re-route a preset's input and save it to one of your own slots.

Shows the pattern for editing presets: recall the preset, make row-keyed edits,
then save. (The device edits and saves whatever is currently on the grid - see
docs/protocol.md for why that matters.)

SAFETY: saving OVERWRITES the destination slot. This is a DRY RUN by default -
it shows what it would do and changes nothing. Pass --write to actually save,
and set DEST_SLOT below to a slot you do not mind overwriting.

Run it with the unit connected by USB and Cortex Control quit:

    python examples/reroute_and_save.py            # dry run
    python examples/reroute_and_save.py --write    # actually saves
"""

import sys
import time

import pyquadcortex
from pyquadcortex import Input, Instrument, Setlist

# --- edit to taste -----------------------------------------------------------
SOURCE_NAME = "Cali Basswalk"       # any factory preset (see list_presets.py)
DEST_SLOT = "30A"                   # OVERWRITTEN when --write is given
DEST_NAME = "Cali Basswalk [Ret1]"
TO_INPUT = Input.RETURN_1           # re-point the preset's input here
INSTRUMENT = Instrument.BASS
# -----------------------------------------------------------------------------


def input_ports(preset):
    return [chain.in_portid for chain in preset.chains if chain.HasField("in_portid")]


def main():
    write = "--write" in sys.argv[1:]

    with pyquadcortex.connect() as qc:
        # Look the source preset up by name, then recall it onto the grid.
        entry = qc.find_preset(SOURCE_NAME, Setlist.FACTORY)
        source = qc.read_preset(Setlist.FACTORY, entry.index)
        rows = pyquadcortex.input_chain_rows(source, Input.INPUT_1)
        print(f"Recalled factory preset {source.name!r}")
        print(f"  inputs currently {[Input(p).name for p in input_ports(source)]}")
        print(f"  {'re-pointing' if write else 'would re-point'} rows {rows} to "
              f"{TO_INPUT.name}, saving to {DEST_SLOT} as {DEST_NAME!r}")

        if not write:
            print("\nDRY RUN - nothing changed. Re-run with --write to save.")
            return 0

        # Re-point each input row, then snapshot the grid into the slot.
        qc.reroute_grid_input(source, TO_INPUT)
        time.sleep(2.0)
        qc.save_current_preset(Setlist.USER, DEST_SLOT, DEST_NAME,
                               instrument=INSTRUMENT)
        time.sleep(2.0)

        # Confirm by reading the slot back.
        saved = qc.read_preset(Setlist.USER, DEST_SLOT)
        ports = input_ports(saved)
        ok = TO_INPUT in ports and Input.INPUT_1 not in ports
        print(f"\nSaved {saved.name!r}; inputs now {[Input(p).name for p in ports]} "
              f"-> {'OK' if ok else 'FAILED'}")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
