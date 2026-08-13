# Everything the library can do

The complete surface, for looking things up. The
[readme](../README.md) is the introduction; this is the reference.

**This page is the protocol layer**, which lives at `pyquadcortex.protocol`: one
Python call per Quad Cortex protocol message. Everything on this page is imported
from there.

```python
from pyquadcortex import protocol

with protocol.connect() as qc:
    print(qc.version())
```

The other half of the library is the model, at the top level
(`pyquadcortex.connect()`), which represents the unit itself rather than the
messages. It is being built now; its design is in
[domain-model.md](domain-model.md). Use the protocol layer for anything the model
does not cover yet.

`QuadCortex` has over a hundred methods, so they are grouped below by what they
touch. Anything marked as global changes the UNIT rather than a preset - there is
nothing to save and nothing to recall to undo it.

## Contents

- [Method groups](#method-groups)
- [Blocks and the model catalog](#blocks-and-the-model-catalog)
- [Building a chain on an empty row](#building-a-chain-on-an-empty-row)
- [Scenes, and how factory presets build them](#scenes-and-how-factory-presets-build-them)
- [Per-preset tempo and the metronome](#per-preset-tempo-and-the-metronome)
- [Neural Captures](#neural-captures)
- [Reading global settings safely](#reading-global-settings-safely)

## Method groups

Most of these are methods on the object `protocol.connect()` returns. Entries
written `protocol.name(...)` are MODULE-LEVEL functions - they take a preset you
already read and need no connection; calling them as methods raises
`AttributeError`.

| | |
|---|---|
| **Inspect** | `version()`, `list_presets(setlist)`, `find_preset(name, setlist)`, `read_preset(setlist, slot)` |
| **Navigate** | `recall_preset(setlist, slot)`, `switch_scene(scene)` |
| **Edit the grid** | `set_chain_input(row, input)`, `reroute_grid_input(preset, input)`, `set_param(row, column, param_index, value)`, `set_bypass(row, column, bypassed)` |
| **Add and remove blocks** | `set_block(row, column, model)`, `remove_block(row, column)`, `move_block(...)`, `catalog` |
| **Parallel lanes** | `set_split(row, split_column, mix_column)`, `clear_split(row)`, `set_split_mute(row)`, `protocol.splits(preset)` |
| **Route a row** | `set_chain_input(row, input)`, `set_chain_output(row, output)` |
| **Lane output** | `set_lane_output(row, param, value=/real=)` - VOLUME, PAN, MUTE, SOLO |
| **Input gate** | `set_input_gate(row, param, value=/real=)` - NOISE REDUCTION, BYPASS, INPUT GAIN |
| **Split and mix** | `set_splitter_param(row, param, ...)`, `set_mixer_param(row, param, ...)`, `set_split_mute(row)`, `protocol.splits(preset)` |
| **Footswitches** | `set_stomp_assignment(row, column, footswitch)`, `set_stomp_momentary()`, `set_stomp_label()`, `protocol.stomp_assignments(preset)` |
| **Expression pedals** | `set_expression(row, column, param, pedal, minimum, maximum)` |
| **Preset MIDI Out** | `set_midi_out(source, [MidiOut.cc(...)])`, `set_preset_load_midi_out([...])`, `protocol.midi_out(preset)` |
| **Tempo MODE** | `tempo_mode()`, `set_tempo_mode(TempoMode.GLOBAL)` - global, and it picks which tempo block plays |
| **Per-preset tempo** | `set_tempo_param(name, ...)`, `set_tempo_option(name, n)`, `protocol.tempo_params(preset)`, `set_tempo_led(on)`, `set_metronome_volume(v)` |
| **Metronome** | `set_tempo_subdivision()`, `set_metronome_sound()`, `set_metronome_routing()`, `set_time_signature()` - all taking full enums |
| **Per-beat accents** | `set_beat(n, MetronomeBeat.ACCENT)`, `set_beats([...])`, `protocol.beats(preset)` |
| **Inspect a preset** (module functions) | `protocol.blocks(preset)`, `protocol.splits(preset)`, `protocol.free_rows(preset)`, `protocol.row_status(preset)`, `protocol.bypass_state(preset, row, column)`, `protocol.param_state(preset, row, column, index)`, `protocol.param_options(preset, row, column, index)`, `protocol.input_chain_rows(preset, input)`, `protocol.params_equal(a, b, option_count=)`, `protocol.field_present(msg, field)` |
| **Wait for the device** | `wait_for_listing(setlist, until=...)` |
| **Scenes** | `copy_scene(from_scene, to_scene, swap=False)`, `set_scene_label(scene, label)`, `set_scene_color(scene, argb)` |
| **Global settings** | `settings()`, `update_settings(**fields)`, `set_scene_bypass_behavior()`, `set_global_bypass()`, `set_master_volume_assignment()`, `mode()`, `set_mode()`, `set_mode_cycle()`, `set_gig_view()` |
| **Global EQ** | `global_eq()`, `set_global_eq(band, gain=, frequency=, q=, filter_type=, enabled=)`, `set_global_eq_output(level=, out12=, out34=)`, `set_global_eq_bypassed()` |
| **I/O ports** | `io_settings()`, `set_input_port()`, `set_output_port()`, `set_usb_port()`, `set_midi_thru()`, `set_output_pairing()` |
| **Tuner and Looper** | `tuner()`, `show_tuner()`, `set_tuner_input()`, `set_tuner_reference()`, `set_tuner_mute()`, `looper()` (states named by `LooperState`) |
| **List parameters** | `set_param_option(row, column, param, option, source)`, `protocol.param_options(preset, ...)` - includes a block's side-chain SOURCE |
| **Setlists** | `create_setlist(name)`, `delete_setlist(name)`, `duplicate_setlist(src, dest)`, `list_folders()` |
| **Copying** | `copy_preset(from_setlist, position, to_setlist)` - recall + save, so it loads each source |
| **Device list** | `pin_model()`, `unpin_model()`, `pinned_models()`, `master_volume()` |
| **Neural Captures** | `captures()`, `list_irs()` to browse the library, `set_capture(row, column, entry)` to place one. Creating a capture is the unit's own wizard - disconnect first, since a connected client suppresses it |
| **Discovery** | `list_folders()` - every folder the device knows, including the factory Captures Library and plugin artist presets; `recents()`, `favorites()`, `add_favorite()`, `remove_favorite()` |
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
  built for that order; [docs/protocol.md](protocol.md) explains why.
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
  slots whether or not they hold anything. Use `protocol.blocks(preset)`.

## Blocks and the model catalog

A grid cell holds a block. `set_block()` fills an empty cell or replaces an
occupied one, and `remove_block()` clears it:

```python
from pyquadcortex.protocol import models

qc.read_preset(Setlist.FACTORY, "27A")                 # load it onto the grid
qc.set_block(row=0, column=2, model=models.GuitarOverdrive.CHIEF_DS1)
qc.remove_block(row=0, column=5)
qc.save_current_preset(Setlist.USER, "30A", "My Patch")
```

`pyquadcortex.protocol.models` has constants for the **412 factory blocks** every unit
has, grouped by category. Anything else - purchased plugin models, and the Neural
Captures you made yourself - has ids that differ per device, so look those up on
the connected unit through `qc.catalog`:

```python
qc.catalog.find("My Capture").id           # by name
qc.catalog[5005].name                      # 'VCA Comp (M)'
qc.catalog.by_category("Bass Amplifier")   # browse
```

The catalog also knows each block's knobs, on `Model.parameters` (NOT `.params`, which is the wire proto's name), so parameters can be set by name, and
in their own units rather than as a 0..1 fraction:

```python
comp = qc.catalog[5005]
qc.set_param(row=0, column=1, param="THRESHOLD", real=-20, model=comp)  # dB
```

That is worth preferring: parameter indices are positional, and not every index
is a visible knob (a cab's are internal `ir selector` entries).

## Building a chain on an empty row

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

## Scenes, and how factory presets build them

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
that means something else - pass `value=`, or speak dB through the helpers:

```python
from pyquadcortex.protocol import db_to_lane_level, lane_level_db

qc.set_lane_output(row=0, param="VOLUME", value=db_to_lane_level(-6.0))
lane_level_db(0.76923077)     # 0.0
```

The span is **-40 to +12 dB**. The knob's lowest numeric step is -39.5 dB; below it
the unit shows "Off", which is wire `0.0` - so for silence write `0.0` rather than
the bottom of the dB scale.

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

Scenes are the Quad Cortex's performance feature, and a scene is more than which
blocks are bypassed - a parameter can hold a different value in each one. Name a
scene and the library does the rest:

```python
from pyquadcortex.protocol import Scene

qc.read_preset(Setlist.FACTORY, "1A")                 # load it onto the grid

# a different drive level in scene C - naming the scene switches to it,
# promotes the parameter to follow scenes, and writes, in the right order
qc.set_param(row=0, column=3, param_index=0, value=0.4, scene=Scene.C)

# per-scene bypass works the same way
qc.set_bypass(row=0, column=3, bypassed=True, scene=Scene.D)
```

Read what a preset stores per scene with the module-level readers - no proto
spelunking needed, and the proto's shape is a trap (its bypass table is addressed
positionally; the `row`/`column` fields inside it read 0 everywhere):

```python
from pyquadcortex.protocol import bypass_state, param_state

st = bypass_state(preset, row=0, column=3)     # .scene_mode, .scenes (8 bools)
pv = param_state(preset, row=0, column=3, param_index=0)   # .scene_mode, .values
```

## Per-preset tempo and the metronome

Each preset carries its own tempo block, separate from the global tempo. **The unit
holds both at once, and the Tempo menu's MODE switch picks which one plays:**

```python
from pyquadcortex.protocol import TempoMode

qc.tempo_mode()                          # TempoMode.PRESET or TempoMode.GLOBAL
qc.set_tempo_mode(TempoMode.GLOBAL)      # run every preset on the device's tempo
```

`set_tempo_mode` is **global**: it affects every preset and there is nothing to save,
so read it first if you mean to put it back. It moves neither tempo block.

Which block you HEAR follows MODE - measured, on one unit minutes apart: 111 bpm under
PRESET from the preset's stored `0.355`, 120 under GLOBAL from the device's `0.400`. The
setters below address the preset's block by construction, since they write
`tempoProgramData`, so writing one while MODE is GLOBAL should store a value you will not
hear until you switch back. That last step is inferred from those two facts rather than
measured, so treat it as a caution and not as a verified behaviour.

The device never broadcasts this switch, so a state tracker cannot learn it from
pushes - `tempo_mode()` has to ask, and it waits for a `GlobalTempo` reply carrying
parameters rather than the running clock, which can take a few seconds.

The per-preset controls:

```python
qc.set_tempo_param("TEMPO", real=120)   # bpm - the span is 40..240, measured
qc.set_tempo_led(False)                 # this preset's TEMPO LED off
qc.set_metronome_muted(True)            # silence the click - the unit's own MUTE
qc.set_tempo_param("TIME SIGNATURE", value=0.1)
```

`TEMPO` is the one tempo parameter whose `real=` comes from a measurement rather than
the catalog, which publishes a placeholder range for it. `tempo_bpm()` and
`bpm_to_tempo()` convert if you need the numbers directly.

Use `set_metronome_muted` and not the volume to silence a click:
`set_metronome_volume(0.0)` is **-60 dB, quiet but still audible**, not silence.

The metronome's list controls have named enums, so nothing needs a magic number:

```python
from pyquadcortex.protocol import (GlobalEQFilter, MetronomeRouting, MetronomeSound,
                                   TempoSubdivision, TimeSignature)

qc.set_time_signature(TimeSignature.SEVEN_EIGHT_2_3_2)
qc.set_tempo_subdivision(TempoSubdivision.EIGHTH_TRIPLET)
qc.set_metronome_sound(MetronomeSound.COWBELL)
qc.set_metronome_routing(MetronomeRouting.OUT_3_4)
```

### Accenting individual beats

Every beat of the bar carries its own state, the cells on the unit's Tempo page.
There are four, and `MetronomeBeat` names them in the order a cell cycles when you
touch it:

```python
from pyquadcortex.protocol import MetronomeBeat, beats

qc.set_time_signature(TimeSignature.FOUR_FOUR)   # FIRST - see the warning below
qc.set_beat(1, MetronomeBeat.ACCENT)             # emphasize the downbeat
qc.set_beat(3, MetronomeBeat.OFF)                # skip beat 3 entirely
qc.set_beats([MetronomeBeat.ACCENT, MetronomeBeat.NORMAL,
              MetronomeBeat.OFF, MetronomeBeat.QUIET])   # a whole bar at once

beats(qc.read_current_preset())    # {1: ACCENT, 2: NORMAL, 3: OFF, 4: QUIET, ...}
```

**Set the time signature first.** Changing it rewrites these, because the device
re-lays the accent pattern out for the new bar - so beats written beforehand are
lost.

The unit stores 13 of them whatever the signature is, enough for its largest, 13/4.
`beats()` reports all 13 rather than trimming to the current signature: beats past
the end are stored, simply not sounded, and how many a *compound* signature sounds
(whether 6/8 has six beats or two) has not been measured.

## Neural Captures

A capture BLOCK is an ordinary model; which capture it plays is a string naming a
library file. So browse the library rather than the catalog - the catalog does not
list captures and does not grow when you save one.

```python
mine = [c for c in qc.captures() if 'My' in c.name or True]
qc.set_capture(row=1, column=0, capture=mine[0])
```

**Creating** a capture is the unit's own wizard, and a connected client SUPPRESSES
it - the unit hands its capture flow to the host and waits. Disconnect to capture.

## Settings only your ears can verify

Three known settings share the worst failure shape this device offers: **the write is
accepted, the read-back agrees exactly with what was written, and the instrument is
silent or making a noise it should not.** A build that verifies every write by reading it
back - this library's own advice - reports complete success while leaving the rig
unusable. A field session did precisely that: 36 presets, every check green, and the
owner plugged into a silent unit with a faint metronome running.

If your automation touches any of these, hand the final check to a human with ears:

| setting | what read-back cannot see |
|---|---|
| **Any tuner write** (`set_tuner_input`, `set_tuner_mute`) | engages an INVISIBLE tuner state; with the mute preference true, the outputs are silent with no on-screen cause. Survives recalls, saves and scene switches. **Call `restore_audio()` afterwards** - it clears the preference, which is the only host-side release (the physical close broadcasts nothing, so there is no message to send). Both setters now warn when a write will leave the rig silent |
| **The metronome's MUTE** (tempo parameter 4; `set_metronome_muted` / `set_metronome_running`) | 1.0 is AUDIBLE, 0.0 is muted - inverted against the label the unit shows, and the opposite polarity to the identically-named lane `MUTE`. Whether a click is actually sounding is not represented anywhere a read reaches, and a lane mute does not silence it |
| **Metronome level** (`set_metronome_volume`) | wire 0.0 is -60 dB, quiet but audible - not silence. The value reads back perfectly while the click ticks on |

| **Any preset recall** (`recall_preset`, and `read_preset` which recalls) | interrupts the audio EVERY time - including a redundant recall of the preset already loaded (measured across four consecutive recalls; only the duration varies, a real change being longer). Loading a preset reloads the engine, so it is expected behaviour - but a verify-by-re-reading loop on `read_preset` stutters a rig on every iteration. `read_current_preset()` has no side effects |

Honourable mention, already documented elsewhere: a lane routed to `out_portid` 16-18
(internal row-to-row routing) can be "muted" without silencing anything a jack carries.

This list is expected to grow. If a setting's only symptom is audio, read-back verifying
it is a category error - the read confirms the device STORED your value, not that the
rig sounds right.

## Reading global settings safely

Three behaviours to know before trusting a read-back, all of which have caused
wrong conclusions in this project:

1. **State pushes can be partial.** A push after an UPDATE may carry only what
   changed, so a reader must wait for one that holds the field it wants.
2. **A read straight after a write can return the previous value.** Allow a settle
   or re-read before deciding a write was refused.
3. **A nested submessage is replaced wholesale.** Setting one flag of
   `master_volume_assignment` clears the others, so use
   `set_master_volume_assignment()`, which reads and merges.

```python
before = qc.settings()                      # read first if you mean to restore
qc.update_settings(screen_brightness=30)    # sparse: only what you name
qc.update_settings(screen_brightness=before.screen_brightness)
```

Fifteen `GeneralSettings` fields are confirmed writable this way; the exceptions are
worth knowing before you trust a read-back. `internal_midi_clock_enabled` refuses writes
outright. `dimmed_led_brightness` is capped just below `led_brightness`, so a high value
silently lands lower. `hold_timing` is an index into six values (500-1000 ms in 100 ms steps), so use `set_hold_timing()` / `hold_timing_ms()`, which convert and validate.
See `update_settings()`'s docstring for the full list.

`update_settings()` refuses `power_option` and `reset_wifi_networks`: those are
commands rather than settings, and one of them shuts the unit down.

