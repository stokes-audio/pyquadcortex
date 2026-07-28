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
| 6 | capture | Set a **per-scene tempo**, if the unit exposes one | `BinaryPreset.scene_tempo` is ignored by a `Grid` update and reads back empty |
| 7 | confirm | Press each **Looper X** action and say which you pressed | `Looper{status{state}}` reads fine but the `state` numbering is unknown, so the transport is not driven. MIDI CC#48-61 is a second route to compare |
| 9 | confirm | Turn the **Master Volume** knob and report the value shown | `MasterVolume` pushes carry `calibrate` but no level; writing it changes output loudness, so it was not probed with headphones connected |
| 10 | confirm | Open the **Tuner** and play a note | `show_tuner()` is accepted but nobody has watched the screen; also confirms whether `frequency` tracks the played note |
| 11 | confirm | Check whether **MidiSource 8 and 9** are Expression 1 and 2 on screen | Inferred from slot arithmetic and confirmed for 8 by an assignment landing in slot 96; 9 is by symmetry |
| 12 | capture | Run a **Neural Capture** start to finish | `NeuralCapture`, `GainCalibration`, `EnableCaptureOut` undecoded, and the flow needs real cabling |
| 13 | capture | **Load a capture from the Captures Library** onto the grid | The 2062-entry library is listable, but how an entry becomes a usable block is unexplored |
| 14 | capture | **Pin a device** to the top of its category in the Virtual Device List | A `PinnedModels` UPDATE listing model ids was accepted and pinned nothing |
| 15 | confirm | Say which **Global EQ band control** each parameter index is | `set_global_eq_band()` writes any of the 28 indices, but the mapping to band type / gain / frequency / Q is unknown |
| 16 | capture | **Merge two modes into a HYBRID slot** | `set_mode_cycle()` reorders and removes slots; merging is a different operation |
| 17 | capture | **Duplicate a setlist** | The unit's duplicate broadcasts `BulkOperation{source_folder, destination_folder}`, but that is a progress report - replaying it created nothing. Creating a setlist DOES work |
| 18 | confirm | Set **Expression Bypass** to Heel-Toe and to Switch | `STOP = 2` is confirmed; the other two are assumed from the manual's ordering |
| 19 | confirm | Set the tuner **FREQ to 445** and say what the screen shows | Confirms `Tuner.frequency` is an offset in **Hz** (442 gave 1.99999809) rather than cents or steps |

## Settled in the first interactive session

Side-chain SOURCE (an ordinary comboBox parameter, not the flag it looks like), output
mute (writable, but only when it travels alone), the tuner's reference pitch (an offset
in Hz from 440), creating a setlist (a sibling of My Presets, not a child), and
`ExpressionBypassMode.STOP = 2`.

## Settled without help

STOMP assignments and their label/momentary maps, expression assignment, per-preset MIDI
Out (all three containers, all three type codes), the splitter/mixer mute, creating and
clearing a branch, moving blocks, expression-bypass shape, string-valued parameters,
comboBox option lists, `GeneralSettings`, `IOSettings` levels and ground lift and input
type and USB dry/wet and MIDI thru and output pairing, Global EQ bypass, footswitch
mode, Gig View, folder discovery, favourites.
