# What the unit does, and what this library can do about it

> Purpose: each feature the Quad Cortex manual describes, against what this library covers today.

A feature-by-feature audit of the [Quad Cortex user
manual](https://neuraldsp.com/manual/quad-cortex) (CorOS 4.x) against this
library. Status meanings, used strictly:

| | |
|---|---|
| **yes** | a library method covers it, verified on a unit |
| **partly** | some of the feature is reachable; the gap is named |
| **no** | not reachable today. The candidate message or preset field is named, so the exploration has a starting point |
| **n/a** | nothing for a host to control: a physical action, a host-side audio concern, or the desktop app itself |

A "candidate" names a message type from the unit's own schema or a field in
`BinaryPreset`. It is a lead, not a claim that it works.

## Summary

Of 105 features audited: **65 yes**, **9 partly**, **20 no**, **11 n/a**.

Of the 94 features a host could plausibly drive, 65 are fully covered and 9 are
partly covered. Twenty remain untouched. Of those, a few are writes confirmed to
do nothing with no route found (preset tags, duplicating a setlist as one
operation), and two whole features need the physical world: creating a Neural
Capture, and loading from the factory Captures Library. How an unknown gets
settled is in [capture.md](capture.md).

---

## 03 Global controls and settings

| Feature | Status | Detail |
|---|---|---|
| Recall a preset | yes | `recall_preset()`, `read_preset()` |
| Switch scene | yes | `switch_scene()` |
| Bank navigation | yes | any slot is addressable by name (`"28C"`) or index |
| Master Volume level | yes | `master_volume()` and `set_master_volume()`. Takes `Encoded`: the wire is 0..1 and the unit displays `round(v * 100)`, but what that number is has not been established. A separate gain stage downstream of the port levels; after a host write the physical knob soft-takes-over. Never send `calibrate` with a level: it opens the calibration dialog |
| Master Volume output assignment | yes | `set_master_volume_assignment()`, which reads and merges because a submessage write clears the flags it omits |
| Master Volume knob function (global vs per output) | yes | `set_master_volume_assignment()` |
| Tuner: open/close | partly | `show_tuner()` is accepted; that it opens on screen has not been seen |
| Tuner: reference pitch, input source, mute | yes | `set_tuner_input()`, `set_tuner_reference()`, `set_tuner_mute()`. The reference is an offset in Hz from 440 and takes `Hertz`; there is no 0..1 line, so `Encoded` is refused. Input accepts both inputs, both returns, `INPUT_1_2` and USB 5/6; the unit refuses `RETURN_1_2` |
| Tuner: Live Tuner (the needle) | no | unsupported by decision. `enable_meter` refuses a host write, so the needle never streams. See `docs/roadmap.md` |
| Tap tempo | no | a `GlobalTempo` read carries the 25 device tempo parameters and none of the 23 attributed ones is a tap. MIDI CC#44 is the documented route |
| Tempo value (per preset) | yes | `set_param(Tempo(), "TEMPO", Bpm(120))`, or `Encoded(...)`. The catalog names the bounds `MIN_TEMPO` and `MAX_TEMPO`; `steps=201` fixes whole bpm over 40..240 |
| Metronome level, LED, time signature, note length | yes | `set_param(Tempo(), ...)` by screen name, `set_tempo_option()` by option number, and typed setters: `set_tempo_subdivision()`, `set_metronome_sound()`, `set_metronome_routing()`, `set_time_signature()` |
| Per-beat accents (customizing each beat of the bar) | yes | `set_beat(n, MetronomeBeat.DOWN)`, `set_beats([...])`, `protocol.beats(preset)`. Tempo parameters 10 to 22 are beats 1 to 13, each a four-option list at `option / 3`. The unit's names are `OFF`, `MUTE`, `DOWN`, `ON`, and they name the accent, not whether the beat sounds. Set the time signature first; changing it rewrites these |
| Tempo `MODE` (Global vs Preset) | yes | `tempo_mode()` and `set_tempo_mode()`: the device tempo block's parameter 1, `0.0` `PRESET` and `1.0` `GLOBAL`. Global, not per preset. The unit emits no change event when the switch moves; the value rides the ambient `GlobalTempo` params push, so the reader waits for a reply carrying parameters |
| Per-scene tempo | n/a | `scene_tempo` is ignored and reads back empty, and the unit has no per-scene tempo |
| Modes: read or set PRESET/SCENE/STOMP/HYBRID | yes | `mode()`, `set_mode(slot)`, `mode_cycle()`. `FootswitchMode` names the three base modes and `describe_mode()` names any value |
| Modes: reorder, merge into HYBRID, remove | yes | `set_mode_cycle([...])`. All six HYBRID pairings are mapped and built with `hybrid_mode(top, bottom)`: values 3 to 8 are the six ordered pairs. A cycle holds at most one hybrid and a hybrid cannot be the only slot. Value 9 is accepted by the unit and leaves the footswitches dead, so it is refused here |
| Gig View: open/close | yes | `set_gig_view()` |
| I/O: input LEVEL, IMPEDANCE, TYPE, PHANTOM 48V | yes | `set_input_port()` writes level, impedance, input type and ground lift, one field per message. The level takes `Db` on a measured -12..+60 dB span. Phantom power has no field in the schema |
| I/O: output LEVEL, GROUND LIFT, MUTE, output pairing | yes | `set_output_port()` for level and ground lift, `set_output_mute()` for mute (which must travel alone), `set_output_pairing()` for the link flags. The output level takes `Encoded` only: no dB span is known |
| I/O: USB LEVEL, HP SOURCE, DRY/WET | yes | `set_usb_port()`, one field per message. The headphone output's own level is not writable |
| Global EQ: bypass, 5 bands (type/gain/freq/Q/bypass), output assignment | yes | `set_global_eq(band, gain=, frequency=, q=, filter_type=, enabled=)`, `set_global_eq_output(level=, out12=, out34=)`, `set_global_eq_bypassed()`. `gain` takes `Db` over -12..+12 (measured on screen at both ends, 2026-09-11); `frequency`, `q` and the OUT level take `Encoded` |
| Power off, reboot, Be Right Back, screen lock | n/a | physical, via the unit's power button |
| Footswitch presses, touch gestures, encoders | n/a | physical |

## 04 The Grid

| Feature | Status | Detail |
|---|---|---|
| Grid layout: 4 rows x 8 slots | yes | `blocks()`; rows are 0-based here and 1-4 on screen |
| Which rows are free for a new chain | yes | `free_rows()`, which excludes a branch's lane row |
| Browse the virtual device list | yes | `catalog`: the unit's own catalog, so it covers purchased and captured content |
| Pin a device to the top of its category | yes | `pin_model()`, `unpin_model()`, `pinned_models()`. The write carries no action field, and pinning appends rather than replaces |
| Place or replace a block | yes | `set_block()`, which verifies the unit accepted the cell |
| Remove a block | yes | `remove_block()` (the `DELETE` action; an `UPDATE` with `hash: 0` is ignored) |
| Move a block | yes | `move_block(source, destination)`; a cross-row move makes the unit create a branch |
| DSP capacity refusal | partly | detected, not predicted: a refused placement raises `BlockRefused`. Headroom cannot be read; `CPULoad` never arrives |
| Global EQ / Input Gate auto-disable under load | partly | `CompilerInhibitedModules{global_gate, global_eq}` is decoded and arrives on grid edits. Not surfaced in the API |
| Input blocks: assign a physical input | yes | `set_chain_input()` |
| Output blocks: assign a destination | yes | `set_chain_output()`. 16 to 18 are internal row-to-row; 19 (`MULTIPLE`) is the Multi-Out |
| Input Gate Control | yes | `set_param(LaneInput(row), ...)`: `NOISE REDUCTION`, `BYPASS`, `INPUT GAIN`, per scene. `GAIN REDUCTION` is a meter |
| Lane Output Control | yes | `set_param(LaneOutput(row), ...)`: `VOLUME`, `PAN`, `MUTE`, `SOLO`, per scene |
| Block bypass | yes | `set_bypass()`, per scene |
| Per-parameter values | yes | `set_param()` by name or index, taking a typed value: a unit type, `Real`, `Encoded`, or a bare string for a string parameter such as a cab's microphone |
| Promote a parameter to follow scenes | yes | `set_param_scene_mode()` (the flag must travel alone) |
| comboBox option names | yes | `param_options()`, reading `Param.dynamic_steps` from the preset |
| Read where a row branches and rejoins | yes | `splits()`, including branches that never rejoin |
| Create a splitter or mixer | yes | `set_split(row, split_column, mix_column)`, `clear_split(row)`. Every even row already has the splitter; the branch is what gets activated |
| Splitter parameters | yes | `set_param(Splitter(row), ...)` via `combined_splitter`; indices follow unified model 10004 |
| Mixer parameters | yes | `set_param(Mixer(row), ...)` |
| Splitter / Mixer MUTE | yes | `set_split_mute()`. One control, not two; the write goes to `splitBypass` and the unit reports it in `mixBypass` |
| Side-chaining: set a block's SOURCE/TRIGGER | yes | `set_param_option(cell, "SOURCE", ...)`. An ordinary comboBox parameter; `sidechain_source_flag` is bookkeeping and ignores writes |
| Footswitch (STOMP) assignment | yes | `set_stomp_assignment()`, `clear_stomp_assignment()`, `set_stomp_momentary()`, `set_stomp_label()`; read with `stomp_assignments()`. Momentary is keyed by footswitch, not column, and lands only on a switch driving one block |
| Expression pedal assignment to a parameter | yes | `set_expression(target, param, pedal, minimum, maximum)` and `clear_expression(target, param)`, against any target: a block, the lane output or input, the mixer, the splitter. The sweep ends take the parameter's own typed values. Read back with `protocol.expression_assignments()` or `grid.pedals` |
| Expression pedal on a Lane Output MUTE or SOLO | no | the unit silently drops a host write of those two while accepting the same message on `VOLUME`, so they raise `ControlNotDrivable` (ADR-0007). The touchscreen writes the same field and the library reads it back |
| Expression bypass (heel-toe / switch / stop) | yes | `set_expression_bypass()` with `ExpressionSwitchMode`: `STOP` 0, `SWITCH` 1, `HEEL_TOE` 2. `SWITCH` greys out SWITCH DELAY, `HEEL_TOE` greys out LATCH EMULATION |
| Expression pedal calibration | partly | calibrating on the unit announces `exp_port{exp_port_id, calibrating: true}` then `false`. Observed, never driven from the host |
| Set Parameters as Defaults | no | `DefaultParameters` is decoded and subscribed; never written |
| Looper X: place the block | yes | an ordinary catalog model |
| Looper X: transport actions and parameters | partly | `looper()` reads the full status and `LooperState` names five states. The transport is not driven from here; MIDI CC#48 to 61 is the documented route |
| Undo / redo | no | `UndoRedo` is decoded and subscribed. It arrives after accepted grid edits, so it is useful as an acceptance signal |

## 05 The Directory

| Feature | Status | Detail |
|---|---|---|
| List a setlist | yes | `list_presets()`; a listing that arrives is complete, but a read may produce none promptly |
| Wait for the directory to settle | yes | `wait_for_listing()` |
| Save a preset ("Save As") | yes | `save_current_preset()` with name, instrument tag and default scene |
| Preset descriptive tags | n/a | not preserved by any save path, including the unit's own Save As, so they are build-chain metadata no library can write. The instrument category is separate and fully mapped (`Instrument`: Guitar 1, Bass 2, Synth 3, Vocal 4, Other 5) |
| Preset description, author, cloud id | no | ignored by a `Grid` update. The unit stamps `author_name` from the signed-in cloud account on every save |
| Preset volume and pan | n/a | ignored by every route tried, and the unit has no control for them; they read 1.0 and 0.5 on every preset |
| Delete a preset | yes | `delete_preset()`, eventually consistent. Pass a listing's `ProductData` to preserve the device-provided key, or the exact display name for the measured 4.0.1 path shape. `delete_setlist()` removes a whole setlist |
| Move a preset | yes | `move_preset()`, same-setlist only observed; accepts the same device-provided `ProductData` or exact-name forms as delete |
| Factory and My Presets setlists | yes | `Setlist.FACTORY`, `Setlist.USER` |
| User folders / additional setlists | yes | `create_setlist()` makes them and `list_folders()` finds them; `list_presets()` accepts any key. MIDI CC#32's "User folders" 2 to 12 are created, not built in |
| Create a folder, nested navigation | yes | `create_setlist(name)`. Setlists are siblings under `/media/p4/Presets`, not children of My Presets |
| Favorites and Recents | yes | `recents()` and `favorites()` read the two lists. The request's `is_favorites` flag selects which; the reply never sets it, so correlate on `request_id`. `add_favorite()` and `remove_favorite()` write one entry at a time. Only presets can be favourited |
| Bulk actions | partly | no host-drivable bulk copy; `BulkOperation` only narrates progress. `copy_preset()` and `duplicate_setlist()` do it by recall plus save, a few seconds per preset |
| Search | no | candidate `RecentSearches` |
| Sort | n/a | client-side once a listing is in hand |
| Neural Captures: list | yes | `captures()` browses the library, over 2000 entries. Not the catalog, which does not grow when a capture is saved |
| Load a capture onto the grid | yes | `set_capture(cell, entry)`: the block model plus a `file_name` string of content hash and name |
| Neural Captures: rename, delete, manage | no | candidate `File` |
| Impulse responses: list and load into an IR Loader | yes | `list_irs()` lists the loadable IRs (`FileMessage.type: 1`; `"2_q"` is "My IRs") and `set_ir()` points a loader at one, on either of its two slots. `IR PATH` takes the library entry's key, `CIR_` plus a content id, with the name in `IR NAME`. The 588 entries under `/opt/neuraldsp/impulse_responses` are plugin assets the unit cannot load and are excluded. Importing an IR from the host is unsolved; use Cortex Control |
| Plugin presets | no | candidate `License`, `CloudProduct` |
| Upload to Cortex Cloud | no | candidates `CloudProduct`, `ProcessDownloadsQueue` |

## 06 Neural Capture

| Feature | Status | Detail |
|---|---|---|
| Run a capture (v1, on the unit) | no | the unit hands the flow to a connected host via `NeuralCapture{try_to_show_dialog}`, so a connected client suppresses the on-device wizard. The engine is the `NC_Recorder`, `NC_Trainer` and `NC_Refiner` internal models |
| Capture v2 (from Cortex Control) | no | `NeuralCapture2` decodes; the flow is unexplored |
| Capture calibration settings, A/B test, metadata | no | as above. `NeuralCapture` carries `state`, `progress`, `toggle_ab_model`, `model_ab_bypass`, `save_info` and `error_id` |
| Physical connection for a capture | n/a | cabling |

## 07, 09 Plugins and computer integration

| Feature | Status | Detail |
|---|---|---|
| Plugin licences and entitlements | no | `License` is decoded and subscribed; its payload is encrypted and never interpreted (ADR-0019) |
| Plugin device availability | partly | the catalog marks `sku` and `plugin_id` models, and the generated constants exclude them |
| USB audio channel mapping, DI vs processed | no | the routing choices live in `IOSettings` |
| USB audio device setup on the host, host monitoring | n/a | host driver and DAW concerns |

## 08 MIDI

| Feature | Status | Detail |
|---|---|---|
| Controlling the unit over MIDI (PC + CC#0-62) | yes | documented by the manual, not implemented here: this library speaks USB HID. The map is in manual chapter 8 |
| MIDI settings: channel, Thru, over USB, ignore duplicate PC, clock in/out | partly | these live in `GeneralSettings`, not `MIDISettings`. `midi_channel`, `midi_over_usb`, `ignore_duplicate_pc`, `midi_clock_in_enabled` and all four `midi_clock_out` values are writable via `update_settings()`, and Thru via `set_midi_thru()`. `internal_midi_clock_enabled` refuses a write |
| Preset MIDI Out: footswitch, expression and on-load messages | yes | `set_midi_out()` and `set_preset_load_midi_out()` via `MIDISettings`, not `Grid`. CC, CC Toggle and PC all confirmed |

## 10 Device Settings menu

All of this is global rather than per preset. `GeneralSettings` carries most of
the menu; fifteen of its fields are confirmed writable one at a time.

| Feature | Status | Detail |
|---|---|---|
| GLOBAL BYPASS (Cab / IR Loader per row) | yes | `set_global_bypass(cab=..., ir=...)`, four booleans per collection |
| SCENE BYPASS BEHAVIOR (3 modes) | yes | `set_scene_bypass_behavior()` with `SceneBypassBehavior`. It decides what `set_bypass` persists |
| STOMP MODE BYPASS (auto-assign on load) | yes | `update_settings(stomp_mode_auto_assign=...)` |
| HOLD TIMING, SWAP TEMPO AND TUNER, GIG VIEW ACCESS | yes | all three via `update_settings()`. `set_hold_timing()` takes `Milliseconds` and writes the index the unit stores (500 to 1000 ms in 100 ms steps); `hold_timing_ms()` reads it back |
| LATENCY COMPENSATION | yes | `update_settings(enable_dynamic_delay_compensation=...)` |
| Device name | no | candidates `Serialization`, `GeneralSettings` |
| Firmware and serial | yes | `version()` |
| Diagnostics (DSP, footswitches, USB) | no | `ModuleStats` is decoded and subscribed; `Diagnostics` and `DSPCommsDiagnostics` are not |
| CorOS updates | no | `Updater` is decoded and subscribed; never driven, and out of scope for good |
| Brightness, power sensitivity, storage | yes | screen, LED and dimmed-LED brightness (quantized: 30 reads back 31; the dimmed value is capped below `led_brightness`), the three dimming toggles, and disk space. `power_option` and `reset_wifi_networks` are refused by `update_settings()` as commands |
| Cloud sign-in and cloud backups | no | `CloudLogin`, `CloudBackup`, `BackupsForward` |
| Local backups | partly | `create_local_backup()` collects and validates the native document; restoring one is not implemented. Confirmed on CorOS 4.1.0: 12 ordered chunks, one marked final |

## 11, 12 Desktop app and reference

| Feature | Status | Detail |
|---|---|---|
| Everything the Cortex Control app does | n/a | this library is an alternative client to the same protocol |
| Preset and IR import from a computer | no | candidate `File` with payloads |
| Recovery mode | n/a | physical boot-time procedure |
| Hardware specifications, regulatory text | n/a | reference |
| Virtual device list | yes | `catalog`, from the unit itself |

---

## Not to be probed

`Updater`, factory reset, cloud login, and the production-test and diagnostics
families (`TestFarm`, `ProductionTest`, `GenerateTestPreset`, `SetTestPreset*`,
`ProductionAutomationMode`). See the "Do not" list in `CLAUDE.md`.
