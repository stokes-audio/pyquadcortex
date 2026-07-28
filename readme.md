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

## Troubleshooting

### `DeviceNotFoundError` when it was working a moment ago

If a session was running fine and then the device vanishes mid-run, the usual advice
in that error message does not apply - Cortex Control is quit, the cable is in, and
the unit has booted. What can happen instead is that **the unit's USB link dies and
only a full power-down recovers it**.

This is field experience from one unit, not a protocol finding, and the root cause is
unknown. Recorded because the symptoms are misleading and cost a user about an hour.

**What it looks like:** `hid.enumerate()` reports zero Neural DSP interfaces and
`connect()` raises. Reseating the cable at either end changes nothing, and retrying in
software never succeeds (25 attempts over 75 seconds, never visible once).

**How to tell it apart from a plain disconnection:** the port is *flapping* -
asserting and dropping a connection several times a second - rather than idle. On
macOS:

```bash
log show --last 60s --predicate 'eventMessage CONTAINS "cableChangeOccurred"' \
    --style compact | grep -c cableChangeOccurred
```

Hundreds of events a minute with nobody touching the cable means the connection is
being made and lost repeatedly, so enumeration never completes. Roughly 264 events
were seen while attached, against about 1 per minute with the cable out - which is
also a clean way to exonerate the host: if it is quiet with nothing plugged in, the
Mac's port and USB stack are fine.

**What fixed it:** a **full shutdown** of the unit, then power on. A reboot was *not*
enough, and unplugging at the unit end does not reset its USB controller either.

**Then wait about three minutes before re-diagnosing.** After a restart the link flaps
for a while as it settles, and that looks identical to the fault: in one measurement
the unit was still flapping a minute later with zero interfaces, then enumerated on
its own two and a half minutes after the restart with no intervention. Sampling during
that window twice led a user to wrongly conclude the power cycle had failed.

**What is not established:** the cause. One unit, one host, and only ever the cable
that shipped with it, so a marginal cable is not ruled out. Onset followed roughly 20
minutes of continuous heavy write traffic, though whether that is connected is
unknown.

### I only need to change presets or scenes

Then you may not need this library at all. The Quad Cortex accepts **MIDI** for preset
and scene selection, which is documented by the manufacturer and needs no USB session:
bank select plus program change for presets, CC#43 for scenes, CC#35-42 for
footswitches, and more. It cannot create or edit preset content, which is what this
library is for - but if switching is all you want, MIDI is the simpler and better
supported route.

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

Closing tells the device the client is leaving, which is what Cortex Control does on
quit. If you supplied your own transport and so own teardown yourself, send it with
`qc.disconnect()` before you tear down.

Presets are addressed by name via `find_preset()`, or directly by the slot name
shown on the unit (`"30A"`), or by linear index. Scenes, inputs, outputs, and
instrument tags all have readable names, so nothing here is a bare number.

### Runnable examples

In **[examples/](examples/)**, roughly in order of how much they touch:

| | |
|---|---|
| [`inspect_preset.py`](examples/inspect_preset.py) | Read-only. Prints a preset's blocks by name, its routing, where rows branch into parallel lanes, and which parameters differ per scene. The best place to start. |
| [`list_presets.py`](examples/list_presets.py) | Read-only. Lists a setlist with instrument tags. |
| [`switch_scenes.py`](examples/switch_scenes.py) | Walks the unit through scenes so you can watch it change. |
| [`reroute_and_save.py`](examples/reroute_and_save.py) | Re-points a preset's input and saves a copy. Dry run unless you pass `--write`. |
| [`build_chain.py`](examples/build_chain.py) | Builds a chain on an empty row - block, input, output, a parameter in real units, and a scene that silences it. Dry run unless you pass `--write`. |

The two that write are dry runs by default and name the slot they would overwrite.

## What you can do

These are all methods on the object `connect()` returns.

| | |
|---|---|
| **Inspect** | `version()`, `list_presets(setlist)`, `find_preset(name, setlist)`, `read_preset(setlist, slot)` |
| **Navigate** | `recall_preset(setlist, slot)`, `switch_scene(scene)` |
| **Edit the grid** | `set_chain_input(row, input)`, `reroute_grid_input(preset, input)`, `set_param(row, column, param_index, value)`, `set_bypass(row, column, bypassed)` |
| **Add and remove blocks** | `set_block(row, column, model)`, `remove_block(row, column)`, `move_block(...)`, `catalog` |
| **Parallel lanes** | `set_split(row, split_column, mix_column)`, `clear_split(row)`, `set_split_mute(row)`, `splits(preset)` |
| **Route a row** | `set_chain_input(row, input)`, `set_chain_output(row, output)` |
| **Lane output** | `set_lane_output(row, param, value=/real=)` - VOLUME, PAN, MUTE, SOLO |
| **Input gate** | `set_input_gate(row, param, value=/real=)` - NOISE REDUCTION, BYPASS, INPUT GAIN |
| **Split and mix** | `set_splitter_param(row, param, ...)`, `set_mixer_param(row, param, ...)`, `set_split_mute(row)`, `splits(preset)` |
| **Footswitches** | `set_stomp_assignment(row, column, footswitch)`, `set_stomp_momentary()`, `set_stomp_label()`, `stomp_assignments(preset)` |
| **Expression pedals** | `set_expression(row, column, param, pedal, minimum, maximum)` |
| **Preset MIDI Out** | `set_midi_out(source, [MidiOut.cc(...)])`, `set_preset_load_midi_out([...])`, `midi_out(preset)` |
| **Per-preset tempo** | `set_tempo_param(name, ...)`, `set_tempo_option(name, n)`, `tempo_params(preset)`, `set_tempo_led(on)`, `set_metronome_volume(v)` |
| **Metronome** | `set_tempo_subdivision()`, `set_metronome_sound()`, `set_metronome_routing()`, `set_time_signature()` - all taking full enums |
| **Inspect a preset** | `blocks(preset)`, `splits(preset)`, `free_rows(preset)`, `param_options(preset, row, column, index)`, `input_chain_rows(preset, input)`, `field_present(msg, field)` |
| **Wait for the device** | `wait_for_listing(setlist, until=...)` |
| **Scenes** | `copy_scene(from_scene, to_scene, swap=False)`, `set_scene_label(scene, label)`, `set_scene_color(scene, argb)` |
| **Global settings** | `settings()`, `update_settings(**fields)`, `set_scene_bypass_behavior()`, `set_global_bypass()`, `set_master_volume_assignment()`, `mode()`, `set_mode()`, `set_mode_cycle()`, `set_gig_view()` |
| **Global EQ** | `global_eq()`, `set_global_eq(band, gain=, frequency=, q=, filter_type=, enabled=)`, `set_global_eq_output(level=, out12=, out34=)`, `set_global_eq_bypassed()` |
| **I/O ports** | `io_settings()`, `set_input_port()`, `set_output_port()`, `set_usb_port()`, `set_midi_thru()`, `set_output_pairing()` |
| **Tuner and Looper** | `tuner()`, `show_tuner()`, `set_tuner_input()`, `set_tuner_reference()`, `looper()` (states named by `LooperState`) |
| **List parameters** | `set_param_option(row, column, param, option, source)`, `param_options(preset, ...)` - includes a block's side-chain SOURCE |
| **Setlists** | `create_setlist(name)`, `delete_setlist(name)`, `duplicate_setlist(src, dest)`, `list_folders()` |
| **Copying** | `copy_preset(from_setlist, position, to_setlist)` - recall + save, so it loads each source |
| **Device list** | `pin_model()`, `unpin_model()`, `pinned_models()`, `master_volume()` |
| **Discovery** | `list_folders()` - every folder the device knows, including the factory Captures Library and plugin artist presets; `favorites()` |
| **Manage presets** | `save_current_preset(setlist, slot, name)`, `delete_preset(setlist, name)`, `move_preset(setlist, name, to_slot)` |

**Rows and columns are zero-based, and the unit displays rows 1 to 4.** `row=0` is
the top row on screen and `row=2` is the one labelled 3. This matters more than it
looks: an edit to the wrong row still succeeds and still reads back correctly, so
nothing tells you. If the change is meant to be audible, check which row actually
reaches an output - `out_portid` values **16 to 18** are internal row-to-row routing,
so a lane set to one of those can be muted without silencing anything. **19
(`MULTIPLE`) is a real destination**, and is what factory presets use for the
Multi-Out.

Presets live in a setlist (`Setlist.USER` or `Setlist.FACTORY`). Identify one by
**name** with `find_preset()`, or by the **slot name shown on the unit** (`"28C"`),
or by linear index if you have it. Scenes are `Scene.A` through `Scene.H`; inputs,
outputs, and instrument tags likewise have readable names (`Input.RETURN_1`,
`Output.XLR_1_2`, `Instrument.BASS`), so nothing needs a bare number.

Things worth knowing before you script against this:

- **Editing goes recall, change, save.** The device saves whatever is currently on
  the grid, so an edit means recalling the preset first. The methods above are
  built for that order; [docs/protocol.md](docs/protocol.md) explains why.
- **Saving may rename.** If the setlist already holds a preset of that name, the
  device appends a `_N` suffix (trimming the base to fit). Pass `confirm=True` to
  get back the name the device actually stored.
- **Naming a scene leaves the unit on that scene.** `set_param(scene=...)`,
  `set_lane_output(scene=...)` and `set_bypass(scene=...)` all work by switching to
  the scene and writing, because that is what the device honours.
- **`read_preset` recalls the slot**, so there is no side-effect-free way to
  inspect a preset, and no way to check a grid edit without saving it somewhere
  first. Verification workflows need a scratch slot.
- **File operations are asynchronous** and the device often does not reply at all,
  so save, delete and move do not raise on a missing reply. Device state is the
  arbiter: confirm with `wait_for_listing()` rather than a fixed sleep, because
  settling time grows with the number of changes.
- **Don't count a row's blocks with `len()`.** Every row reports all 8 column
  slots whether or not they hold anything. Use `blocks(preset)`.

### Blocks and the model catalog

A grid cell holds a block. `set_block()` fills an empty cell or replaces an
occupied one, and `remove_block()` clears it:

```python
from pyquadcortex import models

qc.read_preset(Setlist.FACTORY, "27A")                 # load it onto the grid
qc.set_block(row=0, column=2, model=models.GuitarOverdrive.CHIEF_DS1)
qc.remove_block(row=0, column=5)
qc.save_current_preset(Setlist.USER, "30A", "My Patch")
```

`pyquadcortex.models` has constants for the **412 factory blocks** every unit
has, grouped by category. Anything else - purchased plugin models, and the Neural
Captures you made yourself - has ids that differ per device, so look those up on
the connected unit through `qc.catalog`:

```python
qc.catalog.find("My Capture").id           # by name
qc.catalog[5005].name                      # 'VCA Comp (M)'
qc.catalog.by_category("Bass Amplifier")   # browse
```

The catalog also knows each block's knobs, so parameters can be set by name, and
in their own units rather than as a 0..1 fraction:

```python
comp = qc.catalog[5005]
qc.set_param(row=0, column=1, param="THRESHOLD", real=-20, model=comp)  # dB
```

That is worth preferring: parameter indices are positional, and not every index
is a visible knob (a cab's are internal `ir selector` entries).

### Building a chain on an empty row

Blocks and an input are not enough. **The device never assigns a row's output for
you** - a row given blocks and a physical input keeps its output unset and so never
reaches a jack. Point it somewhere yourself:

```python
row = free_rows(preset)[0]           # not just "a row with no blocks" - see below
qc.set_block(row=row, column=0, model=models.BassAmplifier.AMPED_FLIP_TOP_6464)
qc.set_chain_input(row=row, in_portid=Input.INPUT_2)
qc.set_chain_output(row=row, out_portid=Output.XLR_1_2)   # required, not optional
qc.save_current_preset(Setlist.USER, "30A", "Bass on In 2")
```

**Pick the row with `free_rows()`, not by counting blocks.** When a row branches into
a parallel lane, that lane lives on the row BELOW it, which is frequently empty and is
nonetheless spoken for: building there puts your blocks inside the existing chain's
parallel path. `free_rows()` excludes those.

**A block can be refused for want of DSP capacity.** The preset has a processing
budget, and a block that does not fit is accepted on the wire and then simply is not
there - no error, since every host write is STALLed anyway. `set_block()` checks the
device's echo for you and raises `BlockRefused` when a placement did not take, so this
is loud rather than silent; pass `verify=False` if you would rather send and not wait.
There is no way to ask how much headroom is left, so the answer to a refusal is a
cheaper block or one fewer.

Two things to watch. `Output` values **16 to 18** are internal row-to-row routing
rather than jacks - but **19 (`MULTIPLE`) is a real destination**, and often the right
answer, since it is what factory presets use to reach the Multi-Out. And the device
stores whatever id you send without validating it, so a wrong value is kept rather
than rejected and reads back cleanly.

### The mixer, and how factory presets build scenes

Factory presets often produce their scenes with the **mixer**, not with bypass. In
"Darkglass AO900 1" nothing is bypassed in any scene: all eight come from per-scene
`LEVEL A` / `LEVEL B` across two rows, giving four amp paths.

```python
qc.set_mixer_param(row=0, param="LEVEL A", value=0.0, scene=Scene.C)
```

A level of `0.0` is silence, and unity is **`UNITY_LEVEL`** (0.76923077), which is
what every mixer, splitter and lane level in the factory content sits at when nothing
is attenuated. Those parameters are published with a placeholder `0..1` range that
claims to be dB, so `real=` is refused for them rather than converting into a number
that means something else - pass `value=`.

The **splitter** divides a row into two lanes:

```python
qc.set_splitter_param(row=0, param="LEVEL TO A", value=0.25)
```

Address its parameters by the **unified** model's names - `TYPE`, `STEREO`, `BALANCE`,
`LEVEL TO A`, `LEVEL TO B`, `FREQUENCY`, `MODE` - whatever type-specific block the
preset reports. Note that a preset also exposes a read-only `chain.splitter[]` view of
the same state; writes there are ignored, so always go through `set_splitter_param()`. Which ones apply depends on `TYPE`: the levels for A/B, `BALANCE` for
Balance, `FREQUENCY`/`MODE` for Crossover.

**Where a row splits is readable** with `splits()`, which reports the columns at which
a lane leaves and rejoins. Rows that do not branch are omitted:

```python
for s in splits(preset):
    print(f"row {s.row} branches at {s.split_column}, rejoins at {s.mix_column}")
```

### Per-preset tempo, LED and metronome

Each preset carries its own tempo block, separate from the global tempo:

```python
qc.set_tempo_led(False)              # this preset's TEMPO LED off
qc.set_metronome_volume(0.0)         # silence its metronome (there is no mute flag)
qc.set_tempo_param("TIME SIGNATURE", value=0.1)
```

### Per-scene values

Scenes are the Quad Cortex's performance feature, and a scene is more than which
blocks are bypassed - a parameter can hold a different value in each one. Name a
scene and the library does the rest:

```python
from pyquadcortex import Scene

qc.read_preset(Setlist.FACTORY, "1A")                 # load it onto the grid

# A delay that is wetter in scene C, and nowhere else.
qc.set_param(row=2, column=5, param="MIX", real=45, model=delay, scene=Scene.C)

# A silent scene: mute the rig without leaving the preset.
qc.set_lane_output(row=0, param="VOLUME", value=0.0, scene=Scene.E)

# Bypass follows scenes too.
qc.set_bypass(row=0, column=2, bypassed=True, scene=Scene.B)

qc.save_current_preset(Setlist.USER, "30A", "Scened Patch")
```

A parameter only keeps per-scene values once it is *scene-following*, which on the
unit is the long-press assignment. Naming a scene does that promotion for you; pass
`promote=False` if you know it is already set, or call
`set_param_scene_mode(row, column, param_index)` yourself.

Two things to expect: naming a scene **switches the unit to it**, and without a
scene a write lands on whatever scene is active - which for a parameter that is not
scene-following is its single global value, so it appears everywhere.

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
- **[docs/capture.md](docs/capture.md)** - how to read the device's own traffic when
  you need a message shape this library does not implement yet.
- **[docs/roadmap.md](docs/roadmap.md)** - where this is meant to go, including
  the object model of the device that should eventually hide the protocol's rough
  edges entirely.
- **[changelog.md](changelog.md)** - what changed between released versions.
- **[docs/releasing.md](docs/releasing.md)** - the release checklist, and why each
  step exists.
- **[contributing.md](contributing.md)** - development setup and how to submit a
  change.

## Acknowledgements

Inspired by [OpenCortex](https://github.com/VanIseghemThomas/OpenCortex).

## License

[MIT](LICENSE).
