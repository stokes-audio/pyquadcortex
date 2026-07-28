#!/usr/bin/env python3
"""Browse the Neural Capture library and put one on the grid.

The catalog does NOT list captures and does not grow when you save one: a capture BLOCK
is an ordinary model, and which capture it plays is a string parameter naming a library
file. So the library is what you browse.

SAFETY: a DRY RUN by default. Pass --write to save.

    python examples/use_capture.py                    # list, and show the plan
    python examples/use_capture.py --write            # place the first match
    python examples/use_capture.py --write "Kyle"     # place a capture by name
"""

import sys
import time

import pyquadcortex
from pyquadcortex import Setlist, blocks, field_present, free_rows

DEST_SLOT = "30A"
DEST_NAME = "Capture probe"


def main():
    args = [a for a in sys.argv[1:] if a != "--write"]
    write = "--write" in sys.argv[1:]
    wanted = args[0] if args else None

    with pyquadcortex.connect() as qc:
        captures = qc.captures()
        print(f"the Captures Library holds {len(captures)} captures")
        matches = ([c for c in captures if wanted.lower() in c.name.lower()]
                   if wanted else captures)
        if not matches:
            print(f"  nothing matching {wanted!r}")
            return 1
        for c in matches[:5]:
            print(f"    {c.name!r}  key={c.key[:12]}...")
        if len(matches) > 5:
            print(f"    ... and {len(matches) - 5} more")

        chosen = matches[0]
        preset = qc.read_preset(Setlist.FACTORY,
                                qc.find_preset("Brit 2203", Setlist.FACTORY).index)
        time.sleep(1.5)
        row = free_rows(preset)[0]
        print(f"\nplan: put {chosen.name!r} on row {row + 1} of {preset.name!r}, "
              f"save to {DEST_SLOT}")

        if not write:
            print("\nDRY RUN - nothing changed. Re-run with --write.")
            return 0

        qc.set_capture(row=row, column=0, capture=chosen)
        time.sleep(1.5)
        stored = qc.save_current_preset(Setlist.USER, DEST_SLOT, DEST_NAME,
                                       confirm=True)
        print(f"\nsaved as {stored!r}")

        saved = qc.read_preset(Setlist.USER, DEST_SLOT)
        block = next(b for b in blocks(saved) if b.row == row)
        stored_name = [x.string_value for x in
                       saved.chains[row].models[0].params[5].param_values
                       if field_present(x, "string_value")]
        print(f"  block model {block.model_id} on row {row + 1}")
        # the string is <64-char content hash><display name>
        print(f"  plays: {stored_name[0][64:]!r}" if stored_name else "  no capture set")
        return 0


if __name__ == "__main__":
    sys.exit(main())
