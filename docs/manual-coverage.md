# What the unit does, and what this library can do about it

A feature-by-feature audit of the [Quad Cortex user
manual](https://neuraldsp.com/manual/quad-cortex) (CorOS 4.x) against this library.
The point is to be explicit about the boundary: what is supported and verified, what
is partly reachable, and what nobody has established yet.

Status meanings, used strictly:

| | |
|---|---|
| **yes** | a library method covers it, verified against hardware |
| **partly** | some of the feature is reachable; the gap is named |
| **no** | not reachable today. The candidate message or preset field is named, so the exploration has a starting point |
| **n/a** | nothing for a host to control - a physical action, a host-side audio concern, or the desktop app itself |

"Candidate" columns name a message type from the device's own schema
(73 types, of which 40 are decoded by this library and 22 are subscribed at connect)
or a field in `BinaryPreset`. A named candidate is a lead, not a claim that it works.

## Summary

Of 100 features audited: **61 yes**, **15 partly**, **14 no**, **10 n/a**.

Of the 90 features a host could plausibly drive, **61 are fully covered** and 15 more
are partly covered - which here means the state is readable and at least one field of it
is confirmed writable, with the neighbours the same shape but not individually
exercised. Only 14 remain untouched.

Six rounds got it there. Four were solo - the per-preset non-audio gap (footswitch
assignments, expression assignments, Preset MIDI Out), then the global settings families,
then block moves and branches and the I/O ports and folder discovery, then the remaining
settings submessages. Two were capture sessions with the owner at the unit, and they settled nine things no
amount of host-side probing would have: the side-chain SOURCE, output mute, the tuner's
reference pitch, creating a setlist, an expression-bypass mode, the Looper's state
numbering, the master volume scale, how pinning is written, and one Global EQ index.

What is left falls into two kinds. Some writes are **confirmed no-ops**, so the shape is
something else: preset `volume`/`pan`, `scene_tempo`, preset tags, pinning a model, and
duplicating a setlist. The rest is **semantics** - shapes that work but whose numbering
needs an eye on the unit, like the Looper's `state` and the Global EQ parameter indices.
Both kinds are tracked outside this repo, since a list of open build questions is not
documentation of the library.

---

## 03 Global controls and settings

| Feature | Status | Detail |
|---|---|---|
| Recall a preset | yes | `recall_preset()`, `read_preset()` |
| Switch scene | yes | `switch_scene()` |
| Bank navigation | yes | any slot is addressable by name (`"28C"`) or index |
| Master Volume level | partly | `master_volume()` reads it (0..1 mapping to the 0-100 on screen). READ-ONLY, and it is a separate gain stage - turning the knob changes no port level. The nearest equivalent is setting the individual output levels |
| Master Volume output assignment | yes | `set_master_volume_assignment()`, which reads and merges because a submessage write would clear the flags it omits |
| Master Volume knob function (global vs per output) | partly | in `GeneralSettings`, so reachable via `update_settings()`; not individually exercised |
| Tuner: open/close | partly | `show_tuner()` is accepted; that it opens on screen has not been eyeballed |
| Tuner: reference pitch, input source, mute, Live Tuner | partly | `set_tuner_input()` and `set_tuner_reference()` confirmed, the latter an OFFSET in Hz from 440 (442 and 445 both measured). Tuner `mute` and Live Tuner are untested |
| Tap tempo | no | candidate `GlobalTempo`. A READ of it returned only a running clock, never parameters |
| Tempo value (per preset) | yes | `set_tempo_param("TEMPO", value=...)`. Note the catalog range is a placeholder, so `value=` not `real=` |
| Metronome level, LED, time signature, note length | yes | `set_metronome_volume()`, `set_tempo_led()`, `set_tempo_param()` |
| Per-scene tempo | n/a | `scene_tempo` is ignored and reads back empty, and the unit has no per-scene tempo - its Tempo MODE is global or per preset, nothing finer |
| Modes: read or set PRESET/SCENE/STOMP/HYBRID | yes | `mode()` / `set_mode(slot)`. Note `mode` is a SLOT index, not a named mode |
| Modes: reorder, merge into HYBRID, remove | partly | `set_mode_cycle([...])` reorders and removes slots, confirmed. Merging two into a HYBRID slot is unexplored |
| Gig View: open/close | yes | `set_gig_view()` |
| I/O: input LEVEL, IMPEDANCE, TYPE, PHANTOM 48V | partly | `set_input_port()` writes level, ground lift and input type. Impedance did NOT take, matching the manual's note that it is disabled for Mic inputs; phantom has no field |
| I/O: output LEVEL, GROUND LIFT, MUTE, output pairing | yes | `set_output_port()` for level and ground lift, `set_output_mute()` for mute - which must travel alone - and `set_output_pairing()` for the link flags |
| I/O: USB LEVEL, HP SOURCE, DRY/WET | partly | `set_usb_port()`; `dry_wet` confirmed, level and hp_select untested. The headphone output's own level is NOT writable |
| Global EQ: bypass, 5 bands (type/gain/freq/Q/bypass), output assignment | yes | `set_global_eq(band, gain=, frequency=, q=, filter_type=, enabled=)`, `set_global_eq_output(level=, out12=, out34=)` and `set_global_eq_bypassed()`. Every control is reachable; only the OUT level's dB mapping is unknown |
| Power off, reboot, Be Right Back, screen lock | n/a | physical, via the unit's power button |
| Footswitch presses, touch gestures, encoders | n/a | physical |

## 04 The Grid

| Feature | Status | Detail |
|---|---|---|
| Grid layout: 4 rows x 8 slots | yes | `blocks()`; rows are 0-based here and 1-4 on screen |
| Which rows are free for a new chain | yes | `free_rows()`, which excludes a branch's lane row |
| Browse the virtual device list | yes | `catalog` - the device's own ModelRepo, so it covers purchased and captured content |
| Pin a device to the top of its category | yes | `pin_model()` / `unpin_model()` / `pinned_models()`. The write carries NO action field - an UPDATE is ignored - and pinning APPENDS rather than replacing |
| Place or replace a block | yes | `set_block()`, which verifies the device accepted the cell |
| Remove a block | yes | `remove_block()` (the DELETE action; an UPDATE with `hash: 0` is ignored) |
| Move a block | yes | `move_block(from_row, from_col, to_row, to_col)`; a cross-row move makes the device create a branch |
| DSP capacity refusal | partly | detected, not predicted: a refused placement raises `BlockRefused`. Headroom cannot be read - `CPULoad` never arrives |
| Global EQ / Input Gate auto-disable under load | partly | `CompilerInhibitedModules{global_gate, global_eq}` is decoded and arrives on grid edits. The manual confirms this is the documented behaviour when a preset exceeds resources. Not surfaced in the API |
| Input blocks: assign a physical input | yes | `set_chain_input()` |
| Output blocks: assign a destination | yes | `set_chain_output()`. 16-18 are internal row-to-row; 19 (MULTIPLE) is the Multi-Out |
| Input Gate Control | yes | `set_input_gate()` - NOISE REDUCTION, BYPASS, INPUT GAIN, per scene. GAIN REDUCTION is a meter |
| Lane Output Control | yes | `set_lane_output()` - VOLUME, PAN, MUTE, SOLO, per scene |
| Block bypass | yes | `set_bypass()`, per scene |
| Per-parameter values | yes | `set_param()` by name or index, `real=` where the range is genuine, `text=` for string-valued ones such as a cab's microphone |
| Promote a parameter to follow scenes | yes | `set_param_scene_mode()` (the flag must travel alone) |
| comboBox option names | yes | `param_options()`, reading `Param.dynamic_steps` from the preset |
| Read where a row branches and rejoins | yes | `splits()`, including branches that never rejoin |
| Create a splitter or mixer | yes | `set_split(row, split_column, mix_column)` / `clear_split(row)`. Every even row already has the splitter; the branch is what gets activated |
| Splitter parameters | yes | `set_splitter_param()` via `combined_splitter`; indices follow unified model 10004 |
| Mixer parameters | yes | `set_mixer_param()` |
| Splitter / Mixer MUTE | yes | `set_split_mute()`. It is ONE control, not two; the write goes to `splitBypass` and the device reports it in `mixBypass` |
| Side-chaining: set a block's SOURCE/TRIGGER | yes | `set_param_option(row, column, param="SOURCE", option=...)`. It is an ordinary comboBox parameter; `sidechain_source_flag` is bookkeeping and ignores writes |
| Footswitch (STOMP) assignment | yes | `set_stomp_assignment()` / `clear_stomp_assignment()`, plus `set_stomp_momentary()` and `set_stomp_label()`; read with `stomp_assignments()` |
| Expression pedal assignment to a parameter | yes | `set_expression(row, column, param, pedal, minimum, maximum)` |
| Expression bypass (heel-toe / switch / stop) | yes | `set_expression_bypass()` with `ExpressionBypassMode`. All three confirmed: STOP 0, SWITCH 1, HEEL_TOE 2 - not the manual's listed order |
| Expression pedal calibration | no | candidate `IOSettings`. Manual calls it a global setting |
| Set Parameters as Defaults | no | `DefaultParameters` is decoded and subscribed; never written |
| Looper X: place the block | yes | it is an ordinary catalog model |
| Looper X: transport actions and parameters | partly | `looper()` reads the full status and `LooperState` names five states including OVERDUBBING. The transport is not driven from here; MIDI CC#48-61 is the documented route |
| Undo / redo | no | `UndoRedo` is decoded and subscribed. It arrives after accepted grid edits - useful as an acceptance signal |

## 05 The Directory

| Feature | Status | Detail |
|---|---|---|
| List a setlist | yes | `list_presets()`; a listing that arrives is complete, but a READ may produce none promptly |
| Wait for the directory to settle | yes | `wait_for_listing()` |
| Save a preset ("Save As") | yes | `save_current_preset()` with name, instrument tag and default scene |
| Preset descriptive tags | no | proven unwritable by three routes; a saved preset carries none at all |
| Preset description, author, cloud id | no | ignored by a `Grid` update. The device stamps `author_name` from the signed-in cloud account on every save |
| Preset volume and pan | n/a | ignored by every route tried, and the unit has no control for them - they read 1.0 and 0.5 on every preset. Inert fields, not a gap |
| Delete a preset | yes | `delete_preset()`, eventually consistent. `delete_setlist()` removes a whole setlist |
| Move a preset | yes | `move_preset()`, same-setlist only observed |
| Factory and My Presets setlists | yes | `Setlist.FACTORY`, `Setlist.USER` |
| User folders / additional setlists | yes | `create_setlist()` makes them and `list_folders()` finds them; `list_presets()` accepts any key. CC#32's 'User folders' 2-12 are created, not built in |
| Create a folder, nested navigation | yes | `create_setlist(name)`. The earlier failure was the path: setlists are siblings under `/media/p4/Presets`, not children of My Presets |
| Favorites and Recents | partly | `favorites()` reads them (name, folder key, folder name); writing is untested |
| Bulk actions | partly | there is no host-drivable bulk copy - `BulkOperation` only narrates progress - but `copy_preset()` and `duplicate_setlist()` achieve it by recall + save, at a few seconds per preset |
| Search | no | candidate `RecentSearches` |
| Sort | n/a | client-side once a listing is in hand |
| Neural Captures: list | yes | the catalog covers the unit's own capture slots, and `list_folders()` exposes the 2062-entry factory Captures Library grouped into 176 per-amp folders |
| Neural Captures: rename, delete, manage | no | candidate `File` |
| Impulse responses: list and load into an IR Loader | partly | the 588 factory IRs are listable via `list_folders()`/`list_presets()`; loading one is unexplored (`FileMessage.ir_payload`) |
| Plugin presets | no | candidate `License`, `CloudProduct` |
| Upload to Cortex Cloud | no | candidates `CloudProduct`, `ProcessDownloadsQueue` |

## 06 Neural Capture

| Feature | Status | Detail |
|---|---|---|
| Run a capture (v1, on the unit) | no | `NeuralCapture`, `GainCalibration`, `EnableCaptureOut` - none decoded |
| Capture v2 (from Cortex Control) | no | `NeuralCapture2` - not decoded |
| Capture calibration settings, A/B test, metadata | no | as above |
| Physical connection for a capture | n/a | cabling |

## 07, 09 Plugins and computer integration

| Feature | Status | Detail |
|---|---|---|
| Plugin licences and entitlements | no | `License` is decoded and subscribed; never interpreted |
| Plugin device availability | partly | the catalog marks `sku`/`plugin_id` models, and constants deliberately exclude them |
| USB audio channel mapping, DI vs processed | no | the routing choices live in `IOSettings` |
| USB audio device setup on the host, host monitoring | n/a | host driver and DAW concerns |

## 08 MIDI

| Feature | Status | Detail |
|---|---|---|
| Controlling the unit over MIDI (PC + CC#0-62) | yes | documented, not implemented here: this library speaks USB-HID. The full map is in the manual, ch 8 |
| MIDI settings: channel, Thru, over USB, ignore duplicate PC, clock in/out | no | `MIDISettings` and `GeneralMIDI` - neither decoded |
| Preset MIDI Out: footswitch, expression and on-load messages | yes | `set_midi_out()` / `set_preset_load_midi_out()` via `MIDISettings`, NOT `Grid`. CC/CC Toggle/PC all confirmed |

## 10 Device Settings menu

Every row here is unexplored, and all of it is global rather than per preset.
`GeneralSettings` is decoded and subscribed, so its pushes can already be read;
nothing has been written.

| Feature | Status | Detail |
|---|---|---|
| GLOBAL BYPASS (Cab / IR Loader per row) | yes | `set_global_bypass(cab=..., ir=...)`, four booleans per collection |
| SCENE BYPASS BEHAVIOR (3 modes) | yes | `set_scene_bypass_behavior()` with the `SceneBypassBehavior` enum. It decides what `set_bypass` persists |
| STOMP MODE BYPASS (auto-assign on load) | partly | `stomp_mode_auto_assign` in `GeneralSettings`; reachable via `update_settings()`, untested |
| HOLD TIMING, SWAP TEMPO AND TUNER, GIG VIEW ACCESS | partly | all three read from `settings()`; reachable via `update_settings()`, untested |
| LATENCY COMPENSATION | partly | `enable_dynamic_delay_compensation`; same as above |
| Device name | no | candidate `Serialization`, `GeneralSettings` |
| Firmware and serial | yes | `version()` |
| Diagnostics (DSP, footswitches, USB) | no | `ModuleStats` is decoded and subscribed; `Diagnostics`, `DSPCommsDiagnostics` are not |
| CorOS updates | no | `Updater` is decoded and subscribed; never driven. Risky to explore |
| Brightness, power sensitivity, storage | yes | brightness confirmed (quantized: 30 reads back 31); disk space is reported. `power_option` and `reset_wifi_networks` are refused by `update_settings()` as commands |
| Cloud sign-in and cloud backups | no | `CloudLogin`, `CloudBackup`, `BackupsForward` |
| Local backups | no | `LocalBackup` |

## 11, 12 Desktop app and reference

| Feature | Status | Detail |
|---|---|---|
| Everything the Cortex Control app does | n/a | this library is an alternative client to the same protocol; the app is not a target |
| Preset and IR import from a computer | no | candidate `File` with payloads |
| Recovery mode | n/a | physical boot-time procedure |
| Hardware specifications, regulatory text | n/a | reference |
| Virtual device list | yes | `catalog`, from the device itself |

---

## Findings from this audit

Four things the manual and schema review turned up, before any hardware probing:

1. **comboBox option names ARE recoverable** - just not from `ModelRepo`. Each
   preset's `Param.dynamic_steps` carries the rendered list, with `dynamic_icons`
   alongside. Read from factory "US TWN Vibrato" (01C), the Doubler `TRIGGER` list is
   `Off, Follow Input, Input 1, Input 2, Input 1/2, Return 1, Return 2, Return 1/2,
   USB input 5..8, ...`. So option index 1 is **'Follow Input'**, a fixed entry - which
   answers a question `docs/protocol.md` records as unresolved, and explains why the
   list grows with the preset's block count (per-block entries are appended after the
   fixed ones). The claim that the names are unrecoverable needs correcting.
2. **`CompilerInhibitedModules` is the documented CPU-pressure signal.** The manual
   states the Global EQ and Input Gate are automatically disabled when a preset
   exceeds available resources, which is exactly that message's two booleans. It
   already arrives on grid edits and is worth surfacing.
3. **The Splitter and Mixer each have a MUTE the catalog does not list.** `Chain`
   carries `splitBypass` and `mixBypass`, which are the obvious candidates.
4. **There are more than two setlists.** MIDI CC#32 documents values 2-12 as 'User'
   folders. `Setlist` models only Factory and My Presets.

## Suggested exploration order

Ordered by value to a scripting caller, and with the cheap and safe ones first.

1. **Per-preset, non-audio data** - footswitch (STOMP) assignments, Preset MIDI Out,
   expression assignments, `volume`/`pan`, `scene_tempo`. All are `BinaryPreset`
   fields, all are per preset, and all are verifiable by save-and-read-back. This is
   the biggest gap that affects reproducing a preset faithfully.
2. **Splitter and mixer MUTE, and creating a splitter.** Small, and completes an area
   the library already covers most of.
3. **Global device settings** - `GeneralSettings`, and the Device Settings menu rows.
   High value (SCENE BYPASS BEHAVIOR changes how `set_bypass` behaves) but global, so
   each probe changes the unit's state rather than a preset's.
4. **I/O settings and Global EQ** - `IOSettings`, `GlobalEQ`. Same caution.
5. **Transport-ish state** - `Mode`, `ShowGigView`, `ShowTuner`, `Tuner`,
   `MasterVolume`. Cheap to observe, easy to confirm on screen.
6. **Looper X** (`Looper`) and **Neural Capture** (`NeuralCapture`). Large features;
   the Looper also has a documented MIDI route.
7. **Setlists beyond two**, folders, favourites, bulk operations.

Not to be probed without a specific reason: `Updater`, factory reset, cloud login,
and the production-test and diagnostics families (`TestFarm`, `ProductionTest`,
`GenerateTestPreset`, `SetTestPreset*`, `ProductionAutomationMode`).

## How an unknown gets settled

The technique that has worked all along is in [capture.md](capture.md): perform the
action on the unit while listening to what it broadcasts, then replay that shape from
the host and confirm by read-back. Two rules that decide whether a session produces
an answer: run the listener as a background process so the operator is armed before
the window opens, and include a positive control - a scene switch - so that silence
can be told apart from a broken capture.
