#!/usr/bin/env python3
"""Build a signal chain on an empty row, with a scene that silences it.

Shows the pieces a script needs to create something rather than tweak it: placing
blocks, routing a row in and out, setting a parameter in its own units, and giving one
scene a different value from the rest.

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

import pyquadcortex
from pyquadcortex import (Input, Instrument, Output, Scene, Setlist, blocks,
                          field_present, models)

# --- edit to taste -----------------------------------------------------------
SOURCE_NAME = "Brit 2203"        # any factory preset (see inspect_preset.py)
DEST_SLOT = "30A"                # OVERWRITTEN when --write is given
DEST_NAME = "Bass on Input 2"
BUILD_ROW = 1                    # zero-based: row 2 on the unit's screen
AMP = models.BassAmplifier.AMPED_FLIP_TOP_6464
SILENT_SCENE = Scene.E           # this scene mutes the new row
# -----------------------------------------------------------------------------


def main():
    write = "--write" in sys.argv[1:]

    with pyquadcortex.connect() as qc:
        source = qc.find_preset(SOURCE_NAME, Setlist.FACTORY)
        preset = qc.read_preset(Setlist.FACTORY, source.index)

        # Only build on a row that is actually free.
        used = {b.row for b in blocks(preset)}
        print(f"recalled {preset.name!r}; rows in use: "
              f"{sorted(r + 1 for r in used)} (as the unit numbers them)")
        if BUILD_ROW in used:
            print(f"row {BUILD_ROW + 1} already has blocks - pick another BUILD_ROW")
            return 1

        amp = qc.catalog[AMP]
        print(f"plan for row {BUILD_ROW + 1}:")
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
        qc.set_block(row=BUILD_ROW, column=0, model=AMP)
        qc.set_chain_input(row=BUILD_ROW, in_portid=Input.INPUT_2)
        qc.set_chain_output(row=BUILD_ROW, out_portid=Output.MULTIPLE)

        # 2. A parameter in its own units rather than a 0..1 fraction. Naming the
        #    parameter is safer than an index, since indices are positional and not
        #    every one is a knob you can see.
        qc.set_param(row=BUILD_ROW, column=0, param="MASTER", real=5.0, model=amp)

        # 3. One scene that mutes this row, via its Lane Output Control volume.
        #    Naming a scene leaves the unit sitting on it.
        qc.set_lane_output(row=BUILD_ROW, param="VOLUME", value=0.0,
                           scene=SILENT_SCENE)

        time.sleep(2.0)
        stored = qc.save_current_preset(Setlist.USER, DEST_SLOT, DEST_NAME,
                                        instrument=Instrument.BASS, confirm=True)
        print(f"\nsaved as {stored!r}")

        # 4. Confirm by reading the slot back. Device state is the arbiter.
        saved = qc.read_preset(Setlist.USER, DEST_SLOT)
        chain = saved.chains[BUILD_ROW]
        volumes = [round(v.float_value, 3)
                   for v in chain.output_control[0].params[0].param_values
                   if field_present(v, "float_value")]
        print(f"  row {BUILD_ROW + 1}: "
              f"{len([b for b in blocks(saved) if b.row == BUILD_ROW])} block(s), "
              f"in={Input(chain.in_portid).name}, "
              f"out={Output(chain.out_portid).name}")
        print(f"  lane volume per scene A-H: {volumes}")
        print(f"  scene {SILENT_SCENE.name} silent, others not: "
              f"{volumes[SILENT_SCENE] == 0.0 and volumes.count(0.0) == 1}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
