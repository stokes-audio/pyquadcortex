#!/usr/bin/env python3
"""Build a preset whose scenes differ by level, the way factory presets do it.

Most factory presets do NOT switch sounds by bypassing blocks. They run a parallel lane
and give the mixer's LEVEL A and LEVEL B a different value per scene, so scene A hears
one path and scene B the other. That is what this builds, on a serial preset:

    branch the row, put a block in the lane, then set the two mixer levels per scene.

SAFETY: a DRY RUN by default - it recalls and prints the plan without writing. Pass
--write to save, and set DEST_SLOT to a slot you are happy to overwrite.

    python examples/scene_map.py                # dry run
    python examples/scene_map.py --write        # actually saves
"""

import sys
import time

from pyquadcortex import protocol
from pyquadcortex.protocol import (UNITY_LEVEL, Block, BlockRefused, Mixer, Scene,
from pyquadcortex.protocol.values import Db, Encoded, Real
                                   Setlist, blocks, field_present, free_rows, models,
                                   splits)

# --- edit to taste -----------------------------------------------------------
SOURCE_NAME = "Brit 2203"     # a serial factory preset, so there is a row to branch
DEST_SLOT = "30A"             # OVERWRITTEN when --write is given
DEST_NAME = "Scene map"
LANE_BLOCK = models.GuitarOverdrive.BRIT_GOVERNOR   # what the parallel lane holds
SPLIT_COLUMN = 3              # where the row branches, zero-based
MIX_COLUMN = 6                # where it rejoins
# Which path each scene hears: True = the lane (B), False = the main path (A).
LANE_SCENES = (False, True, False, True, False, True, False, True)
# -----------------------------------------------------------------------------


def main():
    write = "--write" in sys.argv[1:]

    with protocol.connect() as qc:
        source = qc.find_preset(SOURCE_NAME, Setlist.FACTORY)
        preset = qc.read_preset(Setlist.FACTORY, source.index)
        time.sleep(1.5)

        # A branch needs an even row - only rows 0 and 2 have a splitter and mixer,
        # because a lane lives on the row below.
        even_free = [r for r in free_rows(preset) if r % 2 == 0]
        occupied = sorted({b.row for b in blocks(preset)})
        print(f"recalled {preset.name!r}")
        print(f"  rows with blocks: {[r + 1 for r in occupied]} (as the unit numbers them)")
        print(f"  existing branches: {splits(preset) or 'none'}")

        row = 0 if 0 in occupied and 1 in free_rows(preset) else (
            even_free[0] if even_free else None)
        if row is None or (row + 1) not in free_rows(preset):
            print("  no even row with a free lane below it - try another preset")
            return 1

        block = qc.catalog[LANE_BLOCK]
        print(f"\nplan:")
        print(f"  branch row {row + 1} at column {SPLIT_COLUMN + 1}, "
              f"rejoin at column {MIX_COLUMN + 1}")
        print(f"  put {block.name!r} in the lane on row {row + 2}")
        print(f"  scenes hearing the lane: "
              f"{[Scene(i).name for i, lane in enumerate(LANE_SCENES) if lane]}")
        print(f"  save to {DEST_SLOT} as {DEST_NAME!r}")

        if not write:
            print("\nDRY RUN - nothing changed. Re-run with --write to build it.")
            return 0

        # 1. Activate the branch. Every even row already HAS a splitter and mixer;
        #    they are dormant until the columns are set, so nothing is created here.
        qc.set_split(row=row, split_column=SPLIT_COLUMN, mix_column=MIX_COLUMN)
        time.sleep(1.0)

        # 2. A block in the lane, which is the row below the branch.
        try:
            qc.set_block(Block(row + 1, SPLIT_COLUMN + 1, LANE_BLOCK))
        except BlockRefused as refused:
            print(f"\n{refused}")
            return 1

        # 3. The scene map. Naming a scene promotes the parameter to follow scenes and
        #    leaves the unit sitting on that scene, so this walks all eight in order.
        for index, hears_lane in enumerate(LANE_SCENES):
            scene = Scene(index)
            qc.set_param(Mixer(row), "LEVEL A", scene=scene, Encoded(0.0 if hears_lane else UNITY_LEVEL))
            qc.set_param(Mixer(row), "LEVEL B", scene=scene, Encoded(UNITY_LEVEL if hears_lane else 0.0))
            qc.set_scene_label(index, "Lane" if hears_lane else "Main")
            time.sleep(0.4)

        time.sleep(2.0)
        stored = qc.save_current_preset(Setlist.USER, DEST_SLOT, DEST_NAME,
                                        confirm=True)
        print(f"\nsaved as {stored!r}")

        # 4. Read it back and show the map, which is the point of the exercise.
        saved = qc.read_preset(Setlist.USER, DEST_SLOT)
        mixer = saved.chains[row].mixer[0]
        for name, index in (("LEVEL A", 0), ("LEVEL B", 2)):
            values = [round(v.float_value, 3) for v in mixer.params[index].param_values
                      if field_present(v, "float_value")]
            print(f"  {name} per scene A-H: {values}")
        print(f"  scene labels: {list(saved.scene_labels[:8])}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
