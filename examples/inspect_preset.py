#!/usr/bin/env python3
"""Print what a preset actually contains: blocks, routing, splits, and scenes.

A good first example, because it only reads. It also shows the helpers that exist
because a preset cannot be read naively: `blocks()` because every row reports all
eight column slots whether or not they hold anything, `splits()` because a lane's
branch point is not on the splitter block, and `field_present()` because `HasField`
raises on some fields.

Run it with the unit connected by USB and Cortex Control quit:

    python examples/inspect_preset.py                    # a factory preset
    python examples/inspect_preset.py "Brit 2203"        # any preset by name
    python examples/inspect_preset.py 28C --user         # or by slot, from My Presets
"""

import sys

import pyquadcortex
from pyquadcortex import Input, Output, Setlist, blocks, field_present, splits


def main():
    args = [a for a in sys.argv[1:] if a != "--user"]
    setlist = Setlist.USER if "--user" in sys.argv else Setlist.FACTORY
    wanted = args[0] if args else "Brit 2203"

    with pyquadcortex.connect() as qc:
        # A name, or a slot name like "28C". find_preset resolves the name to a slot.
        if wanted[0].isdigit():
            preset = qc.read_preset(setlist, wanted)
        else:
            preset = qc.read_preset(setlist, qc.find_preset(wanted, setlist).index)

        print(f"\n{preset.name!r}")
        print(f"  scenes: {', '.join(preset.scene_labels[:8])}")

        # Rows are zero-based here; the unit labels them 1 to 4.
        print("\n  rows (as the unit numbers them):")
        occupied = {}
        for block in blocks(preset):
            occupied.setdefault(block.row, []).append(block)
        for row, chain in enumerate(preset.chains):
            here = occupied.get(row, [])
            source = (Input(chain.in_portid).name
                      if field_present(chain, "in_portid") else "-")
            destination = (Output(chain.out_portid).name
                           if field_present(chain, "out_portid") and chain.out_portid
                           else "-")
            print(f"    row {row + 1}: {len(here)} block(s)   "
                  f"in={source:<12} out={destination}")
            for block in here:
                name = qc.catalog[block.model_id].name
                print(f"        column {block.column + 1}: {name}")

        # Where a row branches into a parallel lane and rejoins. Serial rows are
        # omitted rather than reported with the device's -1 sentinel.
        parallel = splits(preset)
        print("\n  parallel lanes:", "none" if not parallel else "")
        for split in parallel:
            print(f"    row {split.row + 1} branches at column "
                  f"{split.split_column + 1} and rejoins at {split.mix_column + 1}")

        # Parameters that hold a different value per scene are what make scenes do
        # anything beyond bypass, so they are worth seeing.
        print("\n  parameters that follow scenes:")
        found = False
        for row, chain in enumerate(preset.chains):
            for label, collection in (("block", chain.models),
                                      ("mixer", chain.mixer),
                                      ("lane output", chain.output_control)):
                for element in collection:
                    if not (field_present(element, "hash") and element.hash):
                        continue
                    for index, param in enumerate(element.params):
                        if not (field_present(param, "scene_mode") and param.scene_mode):
                            continue
                        values = [round(v.float_value, 2) for v in param.param_values
                                  if field_present(v, "float_value")]
                        if len(set(values)) < 2:
                            continue          # follows scenes but is set the same in all
                        model = qc.catalog[element.hash]
                        pname = (model.parameters[index].name
                                 if index < len(model.parameters) else f"#{index}")
                        print(f"    row {row + 1} {label} {model.name!r} {pname}: {values}")
                        found = True
        if not found:
            print("    none - this preset's scenes differ only by bypass, or not at all")


if __name__ == "__main__":
    main()
