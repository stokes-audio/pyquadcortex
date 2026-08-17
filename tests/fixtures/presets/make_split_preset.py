#!/usr/bin/env python3
"""Derive a preset fixture that HAS branches from the one that has none.

``structural_preset.bin`` came off a real Quad Cortex, but it is serial on every
row - ``protocol.splits`` reports nothing for it. So nothing in the offline suite
exercised the split half of the grid, and a reader that ignored ``mix`` entirely
passed every test.

Rather than invent a preset shape, this sets the two branch shapes the protocol
layer already records from hardware, in ``QuadCortex.splits``' own docstring:

* row 0 branches at column 2 and NEVER REJOINS - factory "Strat Ambience" (05B)
* row 2 branches and rejoins - factory "Darkglass AO900 1" (27H) does this on
  both its branching rows. Here at columns 3 and 4, so that the splitter slot and
  the mixer slot are different numbers and a reader that returned one for the
  other would be caught.

Everything else is the real fixture's own bytes. Only ``split_control_points``
and the name are touched, so the padding, presence flags, scene-mode flags and
routing all stay verbatim.

Re-run this if the source fixture changes::

    .venv/bin/python tests/fixtures/presets/make_split_preset.py

``tests/test_translation.py`` checks the result still has the two shapes, so a
fixture regenerated from a different source cannot quietly stop testing splits.
"""

import pathlib

from pyquadcortex.protocol.proto import Preset_pb2 as preset_pb

HERE = pathlib.Path(__file__).parent


def main():
    payload = preset_pb.BinaryPreset()
    payload.ParseFromString((HERE / "structural_preset.bin").read_bytes())
    payload.name = "Split Fixture"
    # Wire rows 0 and 2, which are screen rows 1 and 3 - the only two that can
    # carry a branch at all.
    payload.chains[0].split_control_points[0].split = 2
    payload.chains[0].split_control_points[0].mix = -1
    payload.chains[2].split_control_points[0].split = 3
    payload.chains[2].split_control_points[0].mix = 4
    (HERE / "split_preset.bin").write_bytes(payload.SerializeToString())
    print(f"wrote {HERE / 'split_preset.bin'}")


if __name__ == "__main__":
    main()
