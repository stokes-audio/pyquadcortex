#!/usr/bin/env python3
"""List the presets in a setlist, with their instrument tags.

Run it with the unit connected by USB and Cortex Control quit:

    python examples/list_presets.py           # the factory library
    python examples/list_presets.py user      # your own "My Presets" setlist
"""

import collections
import sys

from pyquadcortex import protocol
from pyquadcortex.protocol import Instrument, Setlist


def main():
    want_user = len(sys.argv) > 1 and sys.argv[1] == "user"
    setlist = Setlist.USER if want_user else Setlist.FACTORY

    with protocol.connect() as qc:
        presets = qc.list_presets(setlist)

    print(f"{len(presets)} presets in {'My Presets' if want_user else 'the factory library'}\n")
    counts = collections.Counter()
    for pd in presets:
        name = pd.name if pd.HasField("name") else "(unnamed)"
        try:
            instrument = Instrument(pd.instrument).name.title()
        except ValueError:
            instrument = "-"
        counts[instrument] += 1
        print(f"  {pd.index:>4}  {instrument:<7}  {name}")

    print("\nBy instrument:")
    for instrument, n in counts.most_common():
        print(f"  {instrument}: {n}")


if __name__ == "__main__":
    main()
