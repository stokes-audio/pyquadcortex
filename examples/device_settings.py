#!/usr/bin/env python3
"""Print the unit's global settings, and show how to change one safely.

Global settings are not presets. There is no save, and no recall to undo them - so the
pattern is read first, change, then put back what you found. This prints everything
read-only, and with --write demonstrates the round trip on screen brightness, which is
the most harmless setting there is.

    python examples/device_settings.py           # read only
    python examples/device_settings.py --write   # also round-trips the brightness
"""

import sys
import time

import pyquadcortex
from pyquadcortex import SceneBypassBehavior


def main():
    write = "--write" in sys.argv[1:]

    with pyquadcortex.connect() as qc:
        s = qc.settings()
        print("device settings")
        print(f"  screen / LED brightness   {s.screen_brightness} / {s.led_brightness}")
        print(f"  scene bypass behaviour    "
              f"{SceneBypassBehavior(s.scene_block_bypass).name}")
        print(f"  latency compensation      {s.enable_dynamic_delay_compensation}")
        print(f"  tempo/tuner swapped       {s.swap_tempo_tuner_access}")
        print(f"  MIDI channel / over USB   {s.midi_channel} / {s.midi_over_usb}")
        print(f"  disk free / total         "
              f"{s.available_disk_space} / {s.total_disk_space} GB")
        mv = s.master_volume_assignment
        print(f"  master volume governs     out12={mv.out12} out34={mv.out34} "
              f"send12={mv.send12} hp={mv.headphones}")

        # scene_block_bypass is the one worth noticing: it decides whether set_bypass()
        # edits are KEPT. Under NEVER_OVERWRITE a bypass write applies and is discarded,
        # which looks exactly like a failed write.
        if SceneBypassBehavior(s.scene_block_bypass) is not \
                SceneBypassBehavior.ALWAYS_OVERWRITE:
            print("\n  NOTE: bypass edits are not being saved per scene on this unit")

        io = qc.io_settings()
        print("\nI/O")
        for port in io.settings.in_port:
            print(f"  input {port.input_port_id}   level {port.level:.4f}   "
                  f"{'plugged' if port.plugged else 'not plugged'}")
        for port in io.settings.out_port:
            print(f"  output {port.output_port_id}  level {port.level:.4f}   "
                  f"{'muted' if port.mute else 'unmuted'}")

        eq = qc.global_eq()
        print(f"\nglobal EQ: {'off' if eq.bypassed else 'ON'}, "
              f"{len(eq.parameters)} parameters (5 per band, GAIN at offset 0)")
        m = qc.mode()
        print(f"footswitch mode: slot {m.mode} of {list(m.available_modes.modes)}")
        print(f"master volume:   {qc.master_volume().volume:.3f} "
              f"(the unit shows this as 0-100; read-only)")

        if not write:
            print("\nRead only. Pass --write to see the restore pattern in action.")
            return 0

        # The pattern: keep the old value, change it, put it back. And re-read rather
        # than trusting one read - a read straight after a write can return the OLD
        # value, which is not a refusal.
        was = s.screen_brightness
        print(f"\nbrightness {was} -> 30, then back")
        qc.update_settings(screen_brightness=30)
        for _ in range(5):
            time.sleep(2.0)
            now = qc.settings().screen_brightness
            if now != was:
                break
        print(f"  now {now} (the device quantizes, so 30 may read back as 31)")
        qc.update_settings(screen_brightness=was)
        for _ in range(5):
            time.sleep(2.0)
            if qc.settings().screen_brightness == was:
                break
        print(f"  restored to {qc.settings().screen_brightness}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
