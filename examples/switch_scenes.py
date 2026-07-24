#!/usr/bin/env python3
"""Switch scenes on a connected Quad Cortex so you can watch it change.

The simplest end-to-end example: connect, then switch scenes.

Run it with the unit connected by USB and Cortex Control quit:

    python examples/switch_scenes.py
"""

import time

import pyquadcortex


def main():
    with pyquadcortex.connect() as qc:
        print(">>> Look at the Quad Cortex screen. Starting in:")
        for n in (3, 2, 1):
            print(f"      {n} ...")
            time.sleep(1.0)

        # Scenes are 0-based: A=0, B=1, C=2, D=3.
        for index, label in [(1, "B"), (2, "C"), (3, "D"), (0, "A")]:
            qc.switch_scene(index)
            print(f"      -> scene {label}")
            time.sleep(2.5)

        print(">>> Done. The active scene should have moved B, C, D, A.")


if __name__ == "__main__":
    main()
