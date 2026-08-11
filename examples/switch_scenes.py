#!/usr/bin/env python3
"""Switch scenes on a connected Quad Cortex so you can watch it change.

The simplest end-to-end example: connect, then switch scenes.

Run it with the unit connected by USB and Cortex Control quit:

    python examples/switch_scenes.py
"""

import time

from pyquadcortex import protocol
from pyquadcortex.protocol import Scene


def main():
    with protocol.connect() as qc:
        print(">>> Look at the Quad Cortex screen. Starting in:")
        for n in (3, 2, 1):
            print(f"      {n} ...")
            time.sleep(1.0)

        for scene in (Scene.B, Scene.C, Scene.D, Scene.A):
            qc.switch_scene(scene)
            print(f"      -> scene {scene.name}")
            time.sleep(2.5)

        print(">>> Done. The active scene should have moved B, C, D, A.")


if __name__ == "__main__":
    main()
