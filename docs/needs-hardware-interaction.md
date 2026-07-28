# Things that need someone at the unit

A running list of what this library cannot settle from the host alone, so an interactive
session can work straight down it. Everything NOT here was settled by driving the device
and reading back.

Two kinds of entry:

- **capture** - the write shape is unknown; learn it by performing the action on the unit
  while listening to what it broadcasts (see [capture.md](capture.md)).
- **confirm** - the shape works, but what it MEANS needs an eye on the screen.

## Open

| # | kind | what to do | why it is stuck |
|---|---|---|---|
| 1 | capture | Change a preset's **volume or pan**, if the unit exposes them | `BinaryPreset.volume`/`pan` are ignored by a `Grid` update and by `ProductData.gain` on the save. Possibly not user-editable at all |
| 2 | capture | Set a block's **side-chain SOURCE/TRIGGER** on a `(S/C)` block | `Model.sidechain_source_flag` is ignored by a row/column-keyed write, so the real write goes somewhere else. The source LIST is already readable via `Param.dynamic_steps` |
| 3 | capture | **Mute an output** in I/O Settings | `OutputPortSettings.mute` is accepted and reads back unmuted |
| 4 | capture | **Create a folder** in the Directory, and put a preset in it | A `File` CREATE naming a new folder key created nothing. Also settles how the MIDI CC#32 'User folders 2-12' come into being |
| 5 | capture | Change the tuner's **FREQ [Hz]** reference pitch | `Tuner.frequency` is the DETECTED pitch - it reads 0 in silence and ignores writes - so the reference pitch is stored elsewhere |
| 6 | capture | Set a **per-scene tempo**, if the unit exposes one | `BinaryPreset.scene_tempo` is ignored by a `Grid` update and reads back empty |
| 7 | confirm | Press each **Looper X** action and say which you pressed | `Looper{status{state}}` reads fine but the `state` numbering is unknown, so the transport is not driven. MIDI CC#48-61 is a second route to compare |
| 8 | confirm | Set **Expression Bypass** to Heel-Toe, then Switch, then Stop | `expression_bypass_info.type` round-trips, but which integer is which behaviour is unknown |
| 9 | confirm | Turn the **Master Volume** knob and report the value shown | `MasterVolume` pushes carry `calibrate` but no level; writing it changes output loudness, so it was not probed with headphones connected |
| 10 | confirm | Open the **Tuner** and play a note | `show_tuner()` is accepted but nobody has watched the screen; also confirms whether `frequency` tracks the played note |
| 11 | confirm | Check whether **MidiSource 8 and 9** are Expression 1 and 2 on screen | Inferred from slot arithmetic and confirmed for 8 by an assignment landing in slot 96; 9 is by symmetry |
| 12 | capture | Run a **Neural Capture** start to finish | `NeuralCapture`, `GainCalibration`, `EnableCaptureOut` undecoded, and the flow needs real cabling |
| 13 | capture | **Load a capture from the Captures Library** onto the grid | The 2062-entry library is listable, but how an entry becomes a usable block is unexplored |
| 14 | capture | **Pin a device** to the top of its category in the Virtual Device List | A `PinnedModels` UPDATE listing model ids was accepted and pinned nothing |
| 15 | confirm | Say which **Global EQ band control** each parameter index is | `set_global_eq_band()` writes any of the 28 indices, but the mapping to band type / gain / frequency / Q is unknown |
| 16 | capture | **Merge two modes into a HYBRID slot** | `set_mode_cycle()` reorders and removes slots; merging is a different operation |

## Settled without help

STOMP assignments and their label/momentary maps, expression assignment, per-preset MIDI
Out (all three containers, all three type codes), the splitter/mixer mute, creating and
clearing a branch, moving blocks, expression-bypass shape, string-valued parameters,
comboBox option lists, `GeneralSettings`, `IOSettings` levels and ground lift and input
type and USB dry/wet and MIDI thru and output pairing, Global EQ bypass, footswitch
mode, Gig View, folder discovery, favourites.
