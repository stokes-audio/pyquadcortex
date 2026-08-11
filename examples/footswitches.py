#!/usr/bin/env python3
"""Assign blocks to STOMP footswitches, and give a footswitch a MIDI message.

Two things a preset carries that are not audio at all, and that a script has to set if it
is reproducing a preset faithfully:

  * which footswitch toggles which block, in STOMP mode
  * what MIDI a footswitch sends to the rest of your rig

SAFETY: a DRY RUN by default. Pass --write to save.

    python examples/footswitches.py             # dry run
    python examples/footswitches.py --write     # actually saves
"""

import sys
import time

from pyquadcortex import protocol
from pyquadcortex.protocol import (Footswitch, MidiOut, MidiSource, Setlist, blocks,
                          midi_out, stomp_assignments)

SOURCE_NAME = "Brit 2203"
DEST_SLOT = "30A"
DEST_NAME = "Footswitches"


def main():
    write = "--write" in sys.argv[1:]

    with protocol.connect() as qc:
        source = qc.find_preset(SOURCE_NAME, Setlist.FACTORY)
        preset = qc.read_preset(Setlist.FACTORY, source.index)
        time.sleep(1.5)

        # What is already bound. Factory presets populate this, so read before writing.
        existing = stomp_assignments(preset)
        print(f"recalled {preset.name!r}; {len(existing)} footswitch assignment(s):")
        for a in existing:
            name = qc.catalog[next(b.model_id for b in blocks(preset)
                                   if b.row == a.row and b.column == a.column)].name
            print(f"    row {a.row + 1} col {a.column + 1} -> "
                  f"{Footswitch(a.footswitch).name}   ({name})")

        # Pick the first two blocks on the top row to rebind.
        top = sorted((b for b in blocks(preset) if b.row == 0),
                     key=lambda b: b.column)[:2]
        if len(top) < 2:
            print("  need two blocks on row 1; try another preset")
            return 1
        plan = list(zip(top, (Footswitch.A, Footswitch.B)))
        print("\nplan:")
        for block, switch in plan:
            print(f"  bind {qc.catalog[block.model_id].name!r} to footswitch "
                  f"{switch.name}")
        print(f"  footswitch A also sends CC#10 value 64 on channel 3")
        print(f"  save to {DEST_SLOT} as {DEST_NAME!r}")

        if not write:
            print("\nDRY RUN - nothing changed. Re-run with --write.")
            return 0

        # Assigning takes the unit's own two-message sequence: the existing binding for
        # that cell is deleted first. An update on its own leaves the old one in place.
        for block, switch in plan:
            qc.set_stomp_assignment(row=block.row, column=block.column,
                                    footswitch=switch)
            qc.set_stomp_label(switch, qc.catalog[block.model_id].name[:10])
            time.sleep(0.5)

        # Preset MIDI Out does NOT travel by a Grid write - the preset stores it but a
        # Grid update carrying those fields is ignored. MIDISettings applies it.
        qc.set_midi_out(MidiSource.FOOTSWITCH_A,
                        [MidiOut.cc(channel=3, cc=10, value=64)])
        time.sleep(1.5)

        stored = qc.save_current_preset(Setlist.USER, DEST_SLOT, DEST_NAME,
                                        confirm=True)
        print(f"\nsaved as {stored!r}")

        saved = qc.read_preset(Setlist.USER, DEST_SLOT)
        print("  read back:")
        for a in stomp_assignments(saved):
            print(f"    row {a.row + 1} col {a.column + 1} -> "
                  f"{Footswitch(a.footswitch).name}")
        for source_id, messages in midi_out(saved).items():
            print(f"    {MidiSource(source_id).name} sends {messages}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
