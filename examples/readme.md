# Examples

Runnable against a real unit, with Cortex Control quit. Roughly in order of how much
they touch.

| | |
|---|---|
| [`inspect_preset.py`](inspect_preset.py) | **Start here.** Read-only. Prints a preset's blocks by name, its routing, where rows branch into parallel lanes, which rows are genuinely free, and which parameters differ per scene. |
| [`list_presets.py`](list_presets.py) | Read-only. Lists a setlist with instrument tags. |
| [`switch_scenes.py`](switch_scenes.py) | Walks the unit through its scenes so you can watch it change. |
| [`scene_map.py`](scene_map.py) | Builds a preset whose eight scenes differ by parallel-lane level, the way factory presets do it - per-scene values rather than bypass. |
| [`footswitches.py`](footswitches.py) | Assigns blocks to STOMP footswitches and gives a footswitch a MIDI message to send. |
| [`device_settings.py`](device_settings.py) | Read-only by default. Prints the unit's global settings and I/O, and shows the read-modify-restore pattern those need. |
| [`use_capture.py`](use_capture.py) | Browses the Neural Capture library and places one on the grid. |
| [`reroute_and_save.py`](reroute_and_save.py) | Re-points a preset's input and saves a copy. |
| [`build_chain.py`](build_chain.py) | Builds a chain on a free row - block, input, output, a parameter in real units, and a scene that silences it. |

Anything that writes is a **dry run unless you pass `--write`**, and prints the slot it
would overwrite before touching it.

## Two things worth knowing before you run these

**Rows are zero-based here and 1 to 4 on the unit's screen.** An edit to the wrong row
still succeeds and still reads back correctly, so nothing tells you. The examples print
rows as the unit numbers them.

**Global settings are not presets.** `device_settings.py` touches settings that belong to
the UNIT: there is no save, and no recall to undo them. It reads first and restores what
it changed, which is the pattern to copy.
