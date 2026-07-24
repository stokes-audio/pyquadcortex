# pyquadcortex

[![CI](https://github.com/stokes-audio/pyquadcortex/actions/workflows/ci.yml/badge.svg)](https://github.com/stokes-audio/pyquadcortex/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Control a Neural DSP Quad Cortex from Python, over USB.

`pyquadcortex` is a USB client for the Quad Cortex, in the same way Cortex
Control is a USB client for it: it connects over the cable and speaks the
protocol the device already speaks. **Nothing on the unit is modified, unlocked,
or jailbroken** - no custom firmware, no SD card surgery, no developer mode. Plug
in the USB cable and the device answers.

Recall presets, read them, switch scenes, re-route inputs, change parameters and
bypasses, and save, delete, or move presets - the same operations you would do in
Cortex Control, from a script.

The library imports as `pyquadcortex`; a command-line tool named `qcctl` comes
with it.

> Unofficial and not affiliated with, endorsed by, or supported by Neural DSP
> Technologies. "Quad Cortex" and "Neural DSP" are trademarks of their owner and
> are used here only to describe what this software talks to.

## Status

**Working, and verified on real hardware** on macOS (Apple Silicon) and Windows,
against **CorOS / Cortex Control 4.0.1** (device firmware `d14e`). Every
operation listed below has been exercised on a physical unit. The test suite runs
fully offline, with no device attached.

The device protocol carries no version of its own, so a future CorOS update could
change it. If you are on a newer version and something misbehaves, that is the
first thing to suspect - see [docs/protocol.md](docs/protocol.md).

## Install

```bash
pip install pyquadcortex
```

You also need the **hidapi** C library, which is what actually opens the USB
device:

- macOS: `brew install hidapi`
- Debian/Ubuntu: `sudo apt install libhidapi-hidraw0`
- Windows: included; nothing to do.

Python 3.11 or newer.

> **macOS note:** the `hid` package looks for `libhidapi` by bare name, and
> Homebrew's library directory is not on the default search path. Prefix commands
> that talk to the device with `DYLD_LIBRARY_PATH=/opt/homebrew/lib`.

## Before you connect

**Quit Cortex Control first.** It holds the USB interface exclusively, so while
it is running nothing else can talk to the device. (Wi-Fi can stay on, it makes
no difference. The Quad Cortex just has to be plugged in over USB.)

## Quickstart

Everything here uses the factory library, so it works on any unit.

```python
import pyquadcortex
from pyquadcortex import Input, Instrument, Scene, Setlist

with pyquadcortex.connect() as qc:
    # What are we talking to?
    print(qc.version().app_fw_version)

    # What is in the factory library?
    for entry in qc.list_presets(Setlist.FACTORY)[:5]:
        print(entry.name)

    # Recall a preset by the name shown on the unit, then switch scenes.
    amp = qc.find_preset("Brit 2203", Setlist.FACTORY)
    qc.recall_preset(Setlist.FACTORY, amp.index)
    qc.switch_scene(Scene.B)

    # Re-point a preset's input to Return 1 and save it as your own.
    # (Pick an empty slot; this overwrites whatever is in it.)
    bass = qc.find_preset("Cali Basswalk", Setlist.FACTORY)
    preset = qc.read_preset(Setlist.FACTORY, bass.index)
    qc.reroute_grid_input(preset, Input.RETURN_1)
    qc.save_current_preset(Setlist.USER, "30A", "Cali Basswalk [Ret1]",
                           instrument=Instrument.BASS)
```

`connect()` finds the device, opens it, and completes the handshake the device
requires, so what you get back is ready to use. As a context manager it also
releases the device when the block ends; otherwise call `qc.close()`.

Presets are addressed by name via `find_preset()`, or directly by the slot name
shown on the unit (`"30A"`), or by linear index. Scenes, inputs, outputs, and
instrument tags all have readable names, so nothing here is a bare number.

More runnable examples are in **[examples/](examples/)**: listing presets,
switching scenes, and re-routing and saving a preset.

## What you can do

These are all methods on the object `connect()` returns.

| | |
|---|---|
| **Inspect** | `version()`, `list_presets(setlist)`, `find_preset(name, setlist)`, `read_preset(setlist, slot)` |
| **Navigate** | `recall_preset(setlist, slot)`, `switch_scene(scene)` |
| **Edit the grid** | `set_chain_input(row, input)`, `reroute_grid_input(preset, input)`, `set_param(row, column, param_index, value)`, `set_bypass(row, column, bypassed)` |
| **Scenes** | `copy_scene(from_scene, to_scene, swap=False)`, `set_scene_label(scene, label)`, `set_scene_color(scene, argb)` |
| **Manage presets** | `save_current_preset(setlist, slot, name)`, `delete_preset(setlist, name)`, `move_preset(setlist, name, to_slot)` |

Presets live in a setlist (`Setlist.USER` or `Setlist.FACTORY`). Identify one by
**name** with `find_preset()`, or by the **slot name shown on the unit** (`"28C"`),
or by linear index if you have it. Scenes are `Scene.A` through `Scene.H`; inputs,
outputs, and instrument tags likewise have readable names (`Input.RETURN_1`,
`Output.XLR_1_2`, `Instrument.BASS`), so nothing needs a bare number.

Two things worth knowing:

- **Editing goes recall, change, save.** The device saves whatever is currently on
  the grid, so an edit means recalling the preset first. The methods above are
  built for that order; [docs/protocol.md](docs/protocol.md) explains why.
- **Saving may rename.** If the setlist already holds a preset of that name, the
  device appends a `_N` suffix (trimming the base to fit). Read the slot back if
  the final name matters.

## Command line

`qcctl` covers the common one-off actions (on macOS, with the
`DYLD_LIBRARY_PATH` prefix from above):

```bash
qcctl version
qcctl recall --slot 28C
qcctl scene --index 3
qcctl dump-preset --slot 28C
```

## Documentation

- **[docs/protocol.md](docs/protocol.md)** - how the device's USB protocol works:
  framing, the connect handshake, each operation, and what has been verified.
- **[docs/architecture.md](docs/architecture.md)** - how this library is put
  together, and how to add support for something it does not do yet.
- **[docs/roadmap.md](docs/roadmap.md)** - where this is meant to go, including
  the object model of the device that should eventually hide the protocol's rough
  edges entirely.
- **[docs/releasing.md](docs/releasing.md)** - the release checklist, and why each
  step exists.
- **[contributing.md](contributing.md)** - development setup and how to submit a
  change.

## Acknowledgements

Inspired by [OpenCortex](https://github.com/VanIseghemThomas/OpenCortex).

## License

[MIT](LICENSE).
