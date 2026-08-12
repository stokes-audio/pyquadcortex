# pyquadcortex

[![CI](https://github.com/stokes-audio/pyquadcortex/actions/workflows/ci.yml/badge.svg)](https://github.com/stokes-audio/pyquadcortex/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/stokes-audio/pyquadcortex/blob/main/LICENSE)

Control a Neural DSP Quad Cortex from Python, over USB.

`pyquadcortex` is a USB client for the Quad Cortex, in the same way Cortex
Control is a USB client for it: it connects over the cable and speaks the
protocol the device already speaks. **Nothing on the unit is modified, unlocked,
or jailbroken** - no custom firmware, no SD card surgery, no developer mode. Plug
in the USB cable and the device answers.

Recall and edit presets, build scenes, place and route blocks, drive the I/O and the
Global EQ, browse the unit's own catalogue of blocks and Neural Captures, and manage
setlists - the operations you would do in Cortex Control or on the touchscreen, from a
script. What is and is not covered is listed feature by feature in
[docs/manual-coverage.md](https://github.com/stokes-audio/pyquadcortex/blob/main/docs/manual-coverage.md).

The library imports as `pyquadcortex`; a command-line tool named `qcctl` comes
with it.

> **Upgrading from 0.40.0 or earlier?** In the next release the message-level API
> moves to `pyquadcortex.protocol`. Change `from pyquadcortex import X` to
> `from pyquadcortex.protocol import X`, and `pyquadcortex.connect()` to
> `protocol.connect()`. Submodule paths take the same step, so
> `pyquadcortex.proto`, `.client`, `.enums` and `.session` become
> `pyquadcortex.protocol.proto` and the rest. Nothing else about the API
> changed. See [Two ways in](#two-ways-in) below.

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
first thing to suspect - see [docs/protocol.md](https://github.com/stokes-audio/pyquadcortex/blob/main/docs/protocol.md).

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

## Two ways in

The package has two namespaces, and you can use either one or both.

**`pyquadcortex.protocol`** is the message-level API: one Python call per Quad
Cortex protocol message. It covers every message this library has confirmed on
hardware, it is what everything below is written against, and it is what this
library shipped as through 0.40.0. It moves here in the next release, unchanged,
and its submodules move with it (`pyquadcortex.proto` becomes
`pyquadcortex.protocol.proto`, and so on). What the unit can do that this library
still cannot is listed in
[docs/manual-coverage.md](https://github.com/stokes-audio/pyquadcortex/blob/main/docs/manual-coverage.md).

```python
from pyquadcortex import protocol

with protocol.connect() as qc:
    qc.switch_scene(1)
```

**`pyquadcortex` itself** is the model of the unit: objects that look and behave
the way the Quad Cortex does, so you write what you mean instead of holding
protocol facts in your head. It is being built now, and today it gives you the
unit's identity and not much else.

```python
import pyquadcortex

with pyquadcortex.connect() as device:
    print(device.firmware, device.serial)
```

Use the protocol layer for anything the model does not cover yet. To mix the two
in one script, wrap a connection you already have:

```python
from pyquadcortex import Device, protocol

with protocol.connect() as qc:
    device = Device.from_client(qc)
```

Where the model is going is in
[docs/domain-model.md](https://github.com/stokes-audio/pyquadcortex/blob/main/docs/domain-model.md).
Everything below on this page is the protocol layer.

## Quickstart

Everything here uses the factory library, so it works on any unit.

```python
from pyquadcortex import protocol
from pyquadcortex.protocol import Input, Instrument, Scene, Setlist

with protocol.connect() as qc:
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

`protocol.connect()` finds the device, opens it, and completes the handshake the
device requires, so what you get back is ready to use. As a context manager it
also releases the device when the block ends; otherwise call `qc.close()`.

Closing tells the device the client is leaving, which is what Cortex Control does on
quit. If you supplied your own transport and so own teardown yourself, send it with
`qc.disconnect()` before you tear down.

Presets are addressed by name via `find_preset()`, or directly by the slot name
shown on the unit (`"30A"`), or by linear index. Scenes, inputs, outputs, and
instrument tags all have readable names, so nothing here is a bare number.

## What it can do

Four things worth knowing about, each with the full detail in
**[docs/api.md](https://github.com/stokes-audio/pyquadcortex/blob/main/docs/api.md)**.

### Edit a preset

Blocks, parameters, routing and bypass, addressed by row and column. Rows are
**zero-based here and 1 to 4 on screen**, which is the single easiest thing to get
wrong - an edit to the wrong row still succeeds and still reads back correctly.

```python
row = free_rows(preset)[0]                  # not just "a row with no blocks"
qc.set_block(row=row, column=0, model=models.BassAmplifier.AMPED_FLIP_TOP_6464)
qc.set_chain_input(row=row, in_portid=Input.INPUT_2)
qc.set_chain_output(row=row, out_portid=Output.MULTIPLE)   # required, not optional
qc.set_param(row=row, column=0, param="MASTER", real=5.0, model=amp)
qc.save_current_preset(Setlist.USER, "30A", "Bass on In 2")
```

An edit persists only through this kind of row/column-keyed write followed by a save.
Writing a whole preset back does nothing.

### Build scenes

Scenes are the Quad Cortex's signature feature, and most factory presets build them
from per-scene parameter VALUES rather than from bypass. Name a scene and the value
belongs to that scene alone.

```python
qc.set_param(row=0, column=5, param="MIX", value=0.8, scene=Scene.C)
qc.set_lane_output(row=0, param="VOLUME", value=0.0, scene=Scene.E)  # a silent scene
qc.set_bypass(row=0, column=2, bypassed=True, scene=Scene.B)
```

### Drive the whole unit, not just presets

I/O port levels and ground lift, the Global EQ, footswitch modes, the tuner, the
Looper's state, master volume, Gig View, and most of the Device Settings menu.

```python
qc.set_input_port(1, level=0.4)              # global: no save, nothing to undo it
qc.set_global_eq(band=3, gain=0.75, filter_type=GlobalEQFilter.PEAK)
qc.set_scene_bypass_behavior(SceneBypassBehavior.ALWAYS_OVERWRITE)
```

Read [Reading global settings safely](https://github.com/stokes-audio/pyquadcortex/blob/main/docs/api.md#reading-global-settings-safely)
first - a read straight after a write can return the old value, which looks exactly
like a refusal.

### Find what is on the unit

The device tells you its own contents: every block it has, every preset, every folder,
and over two thousand Neural Captures.

```python
qc.catalog[21005].name                       # '212 Darkglass Neo (M)'
qc.list_presets(Setlist.FACTORY)             # or any folder key at all
qc.list_folders()                            # 399 of them on the observed unit
qc.captures()                                # the Neural Capture library
```

## Examples

Runnable examples are in **[examples/](https://github.com/stokes-audio/pyquadcortex/tree/main/examples)**, each described in the readme
there. Start with
[`inspect_preset.py`](https://github.com/stokes-audio/pyquadcortex/blob/main/examples/inspect_preset.py) - it only reads, and it prints what a
preset actually contains.

The ones that write are dry runs unless you pass `--write`, and they name the slot they
would overwrite.

## Troubleshooting

If the device disappears mid-session with `DeviceNotFoundError`, see
[docs/troubleshooting.md](https://github.com/stokes-audio/pyquadcortex/blob/main/docs/troubleshooting.md).

If all you need is to change presets or scenes, you may not need this library at all -
the unit accepts MIDI over USB and DIN: bank select plus program change for presets,
CC#43 for scenes, CC#35-42 for the footswitches. This library exists for everything MIDI
cannot reach.

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

- **[docs/api.md](https://github.com/stokes-audio/pyquadcortex/blob/main/docs/api.md)** - everything the library can do, grouped by what it
  touches. The reference to the introduction above.
- **[docs/manual-coverage.md](https://github.com/stokes-audio/pyquadcortex/blob/main/docs/manual-coverage.md)** - every feature the Quad
  Cortex manual describes, against what this library covers. Read this to find out
  whether your case is supported before you start.
- **[docs/protocol.md](https://github.com/stokes-audio/pyquadcortex/blob/main/docs/protocol.md)** - how the device's USB protocol works:
  framing, the connect handshake, each operation, and what has been verified.
- **[docs/architecture.md](https://github.com/stokes-audio/pyquadcortex/blob/main/docs/architecture.md)** - how this library is put
  together, and how to add support for something it does not do yet.
- **[docs/capture.md](https://github.com/stokes-audio/pyquadcortex/blob/main/docs/capture.md)** - how to read the device's own traffic when
  you need a message shape this library does not implement yet.
- **[docs/roadmap.md](https://github.com/stokes-audio/pyquadcortex/blob/main/docs/roadmap.md)** - where this is meant to go, including
  the object model of the device that should eventually hide the protocol's rough
  edges entirely.
- **[changelog.md](https://github.com/stokes-audio/pyquadcortex/blob/main/changelog.md)** - what changed between released versions.
- **[docs/releasing.md](https://github.com/stokes-audio/pyquadcortex/blob/main/docs/releasing.md)** - the release checklist, and why each
  step exists.
- **[docs/troubleshooting.md](https://github.com/stokes-audio/pyquadcortex/blob/main/docs/troubleshooting.md)** - when the device stops
  answering.
- **[contributing.md](https://github.com/stokes-audio/pyquadcortex/blob/main/contributing.md)** - development setup and how to submit a
  change.

## Acknowledgements

Inspired by [OpenCortex](https://github.com/VanIseghemThomas/OpenCortex).

## License

[MIT](https://github.com/stokes-audio/pyquadcortex/blob/main/LICENSE).
