#!/usr/bin/env python3
"""Build a signal chain on a free row, with a scene that silences it.

Shows the pieces a script needs to create something rather than tweak it: finding a
row that is genuinely free, placing blocks, routing a row in and out, setting a
parameter in its own units, and giving one scene a different value from the rest.

The whole thing goes recall -> edit -> save, because the device saves whatever is on
the grid rather than a payload you hand it.

SAFETY: this is a DRY RUN by default - it recalls and prints the plan without writing.
Pass --write to save, and set DEST_SLOT below to a slot you are happy to overwrite.

Run it with the unit connected by USB and Cortex Control quit:

    python examples/build_chain.py                 # dry run
    python examples/build_chain.py --write         # actually saves
"""

import sys
import time

from pyquadcortex import protocol
from pyquadcortex.protocol import (Block, BlockRefused, Input, Instrument, LaneOutput,
                                   Output, Scene, Setlist, UNITY_LEVEL, blocks,
                                   field_present, free_rows, models)

# --- edit to taste -----------------------------------------------------------
SOURCE_NAME = "Brit 2203"        # any factory preset (see inspect_preset.py)
DEST_SLOT = "30A"                # OVERWRITTEN when --write is given
DEST_NAME = "Bass on Input 2"
BUILD_ROW = None                 # None picks a free row; or set one, zero-based
AMP = models.BassAmplifier.AMPED_FLIP_TOP_6464
SILENT_SCENE = Scene.E           # this scene mutes the new row
# -----------------------------------------------------------------------------


def main():
    write = "--write" in sys.argv[1:]

    with protocol.connect() as qc:
        source = qc.find_preset(SOURCE_NAME, Setlist.FACTORY)
        preset = qc.read_preset(Setlist.FACTORY, source.index)

        # A row is free only if it holds no blocks AND is not the parallel lane of
        # a branch on the row above - that lane is often empty and still spoken for.
        free = free_rows(preset)
        print(f"recalled {preset.name!r}; rows free for a new chain: "
              f"{[r + 1 for r in free]} (as the unit numbers them)")
        row = free[0] if BUILD_ROW is None else BUILD_ROW
        if row not in free:
            print(f"row {row + 1} is not free - pick another BUILD_ROW")
            return 1

        amp = qc.catalog[AMP]
        print(f"plan for row {row + 1}:")
        print(f"  place {amp.name!r} in column 1")
        print(f"  feed it from {Input.INPUT_2.name}")
        print(f"  send it to {Output.MULTIPLE.name}")
        print(f"  silence the row in scene {SILENT_SCENE.name}")
        print(f"  save to {DEST_SLOT} as {DEST_NAME!r}")

        if not write:
            print("\nDRY RUN - nothing changed. Re-run with --write to build it.")
            return 0

        # 1. A block, and the routing that makes it audible. The device does NOT
        #    assign an output on its own, so without set_chain_output the row would
        #    have blocks and an input but reach no jack.
        #    A placement can be refused when the preset has no DSP capacity left
        #    for it; set_block checks and says so rather than failing quietly.
        try:
            qc.set_block(Block(row, 0, AMP))
        except BlockRefused as refused:
            print(f"\n{refused}")
            return 1
        qc.set_chain_input(row=row, in_portid=Input.INPUT_2)
        qc.set_chain_output(row=row, out_portid=Output.MULTIPLE)

        # 2. A parameter in its own units rather than a 0..1 fraction. Naming the
        #    parameter is safer than an index, since indices are positional and not
        #    every one is a knob you can see.
        qc.set_param(Block(row, 0, amp), param="MASTER", real=5.0)

        # 3. The row's own level, then one scene that silences it. Lane VOLUME is
        #    one of the parameters whose catalog range is a placeholder, so it takes
        #    the wire's 0..1 - UNITY_LEVEL is the value meaning "no attenuation".
        #    Naming a scene leaves the unit sitting on it.
        qc.set_param(LaneOutput(row), param="VOLUME", value=UNITY_LEVEL)
        qc.set_param(LaneOutput(row), param="VOLUME", value=0.0,
                           scene=SILENT_SCENE)

        time.sleep(2.0)
        stored = qc.save_current_preset(Setlist.USER, DEST_SLOT, DEST_NAME,
                                        instrument=Instrument.BASS, confirm=True)
        print(f"\nsaved as {stored!r}")

        # 4. Confirm by reading the slot back. Device state is the arbiter.
        saved = qc.read_preset(Setlist.USER, DEST_SLOT)
        chain = saved.chains[row]
        volumes = [round(v.float_value, 3)
                   for v in chain.output_control[0].params[0].param_values
                   if field_present(v, "float_value")]
        print(f"  row {row + 1}: "
              f"{len([b for b in blocks(saved) if b.row == row])} block(s), "
              f"in={Input(chain.in_portid).name}, "
              f"out={Output(chain.out_portid).name}")
        print(f"  lane volume per scene A-H: {volumes}")
        print(f"  scene {SILENT_SCENE.name} silent, others not: "
              f"{volumes[SILENT_SCENE] == 0.0 and volumes.count(0.0) == 1}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
