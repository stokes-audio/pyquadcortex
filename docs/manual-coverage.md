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

Of 103 features audited: **64 yes**, **8 partly**, **20 no**, **11 n/a**.

Of the 91 features a host could plausibly drive, **65 are fully covered** and 13 more
are partly covered - which here means the state is readable and at least one field of it
is confirmed writable, with the neighbours the same shape but not individually
exercised. Only 14 remain untouched.

Four solo rounds and several sessions with the owner at the unit got it there. The solo
rounds closed the per-preset non-audio gap (footswitch assignments, expression
assignments, Preset MIDI Out), the global settings families, block moves and branches and
the I/O ports and folder discovery, and the settings submessages. The sessions at the unit
settled what no host-side probing could: the side-chain SOURCE, output mute, the tuner's
reference pitch, creating a setlist, the expression-bypass numbering, the Looper's states,
the master volume scale, how pinning is written, the Global EQ's whole 28-index layout,
every option of the metronome's four lists, and the per-beat accent cells.

What is left is of two kinds. A few writes are **confirmed no-ops** with no route found:
preset tags, and duplicating a setlist as a device operation (the library does it by
recall-and-save instead). One feature is **simply not on the wire** - the Tempo menu's MODE
broadcasts nothing, even on commit. And two whole features remain unexplored because they
need the physical world: Neural Capture, and loading from the factory Captures Library.

---

## 03 Global controls and settings

| Feature | Status | Detail |
|---|---|---|
| Recall a preset | yes | `recall_preset()`, `read_preset()` |
| Switch scene | yes | `switch_scene()` |
| Bank navigation | yes | any slot is addressable by name (`"28C"`) or index |
| Master Volume level | yes | `master_volume()` reads it and `set_master_volume()` writes it, normalized 0..1 with the unit displaying `round(v * 100)`. The recorded "read-only" was a STALE READ, not a refusal - a read straight after a write returns the previous value. It is a separate gain stage applied downstream of the port levels, and after a host write the physical knob soft-takes-over, which is what the manual describes Cortex Control doing. Never send `calibrate` alongside a level: it opens the calibration dialog on the unit |
| Master Volume output assignment | yes | `set_master_volume_assignment()`, which reads and merges because a submessage write would clear the flags it omits |
| Master Volume knob function (global vs per output) | yes | `set_master_volume_assignment()`, which reads and merges - the raw field is a submessage, and writing one flag through `update_settings()` clears the other three |
| Tuner: open/close | partly | `show_tuner()` is accepted; that it opens on screen has not been eyeballed |
| Tuner: reference pitch, input source, mute | yes | `set_tuner_input()`, `set_tuner_reference()` and `set_tuner_mute()` all confirmed. Reference is an OFFSET in Hz from 440. Input accepts both inputs, both returns, INPUT_1_2 and USB 5/6; `RETURN_1_2` is refused by the DEVICE, so combined-returns tuning does not exist |
| Tuner: Live Tuner (the needle) | no | UNSUPPORTED by decision. `enable_meter` refuses a host write - it stays false and `meter` stays 0.0 - so the needle never streams. Not worth chasing for an instrument you have to be holding; see `docs/roadmap.md` |
| Tap tempo | no | candidate `GlobalTempo`. A READ of it returned only a running clock, never parameters |
| Tempo value (per preset) | yes | `set_tempo_param("TEMPO", value=...)`. Note the catalog range is a placeholder, so `value=` not `real=` |
| Metronome level, LED, time signature, note length | yes | `set_tempo_param()` by screen name, `set_tempo_option()` by option number, and typed setters with full enums: `set_tempo_subdivision()`, `set_metronome_sound()`, `set_metronome_routing()`, `set_time_signature()`. The menu's MODE is not on the wire at all |
| Per-beat accents (customizing each beat of the bar) | yes | `set_beat(n, MetronomeBeat.ACCENT)` and `set_beats([...])`; `pyquadcortex.beats(preset)` reads them back. Tempo parameters 10-22 - the catalog's `STEPSTATE0` to `STEPSTATE12` - are beats 1 to 13, each a four-option list at `option / 3`: normal, off, accented, de-emphasized. Traced by touching cells on the unit; a cell cycles UP by 1/3 and wraps, so four touches return it to where it started. Set the time signature FIRST - changing it rewrites these |
| Per-scene tempo | n/a | `scene_tempo` is ignored and reads back empty, and the unit has no per-scene tempo - its Tempo MODE is global or per preset, nothing finer |
| Modes: read or set PRESET/SCENE/STOMP/HYBRID | yes | `mode()` / `set_mode(slot)`, plus `mode_cycle()` to read the cycle - `mode()` accepts partial pushes and can report an empty one. `FootswitchMode` names the three base modes and `describe_mode()` names any value |
| Modes: reorder, merge into HYBRID, remove | yes | `set_mode_cycle([...])`. All six HYBRID pairings are mapped and built with `hybrid_mode(top, bottom)`: a hybrid gives footswitches A-D one mode and E-H another, so 3-8 are the six ORDERED pairs (4 and 7 being the same pair swapped). A cycle holds at most one hybrid and a hybrid cannot be the only slot; value 9 is ACCEPTED by the device but leaves the footswitches dead, so it is refused here |
| Gig View: open/close | yes | `set_gig_view()` |
| I/O: input LEVEL, IMPEDANCE, TYPE, PHANTOM 48V | yes | `set_input_port()` writes level, impedance, input type and ground lift - each in its own message, since some fields are dropped when packed together. Phantom power has no field in the schema |
| I/O: output LEVEL, GROUND LIFT, MUTE, output pairing | yes | `set_output_port()` for level and ground lift, `set_output_mute()` for mute - which must travel alone - and `set_output_pairing()` for the link flags |
| I/O: USB LEVEL, HP SOURCE, DRY/WET | yes | `set_usb_port()`, all three confirmed writable. Like the other I/O ports they must travel one field per message, which the method now does for you. The headphone output's own level is NOT writable |
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
| Footswitch (STOMP) assignment | yes | `set_stomp_assignment()` / `clear_stomp_assignment()`, plus `set_stomp_momentary()` and `set_stomp_label()`; read with `stomp_assignments()`. Momentary is keyed by footswitch, not column, and only lands on a switch driving ONE block - the device refuses multi-block switches silently, as its own toggle does. The manual never mentions stomp momentary; the touchscreen's Assign footswitch modal has it |
| Expression pedal assignment to a parameter | yes | `set_expression(row, column, param, pedal, minimum, maximum)` |
| Expression bypass (heel-toe / switch / stop) | yes | `set_expression_bypass()` with `ExpressionBypassMode`. All three confirmed: STOP 0, SWITCH 1, HEEL_TOE 2 - not the manual's listed order |
| Expression pedal calibration | partly | the flow IS `IOSettings`, as guessed: calibrating a pedal on the unit broadcasts `exp_port{exp_port_id, calibrating: true}` and `false` on completion, for EXP 1 and EXP 2 alike. Observed, never driven from the host |
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
| Preset descriptive tags | n/a | not preserved by ANY save path - the unit's own Save As strips them too (factory 5D's six tags -> none), so they are build-chain/cloud metadata no library can write. The instrument category is separate, survives, and is fully mapped (`Instrument`: Guitar 1, Bass 2, Synth 3, Vocal 4, Other 5) |
| Preset description, author, cloud id | no | ignored by a `Grid` update. The device stamps `author_name` from the signed-in cloud account on every save |
| Preset volume and pan | n/a | ignored by every route tried, and the unit has no control for them - they read 1.0 and 0.5 on every preset. Inert fields, not a gap |
| Delete a preset | yes | `delete_preset()`, eventually consistent. `delete_setlist()` removes a whole setlist |
| Move a preset | yes | `move_preset()`, same-setlist only observed |
| Factory and My Presets setlists | yes | `Setlist.FACTORY`, `Setlist.USER` |
| User folders / additional setlists | yes | `create_setlist()` makes them and `list_folders()` finds them; `list_presets()` accepts any key. CC#32's 'User folders' 2-12 are created, not built in |
| Create a folder, nested navigation | yes | `create_setlist(name)`. The earlier failure was the path: setlists are siblings under `/media/p4/Presets`, not children of My Presets |
| Favorites and Recents | yes | `recents()` and `favorites()` read the two lists - the request's `is_favorites` flag selects which, though the REPLY never sets it, so correlate on `request_id`. `add_favorite()`/`remove_favorite()` write, one entry at a time, confirmed by the device's echo of the changed entry. Entries feed straight into `find_preset()`/`recall_preset()`. Only presets can be favourited |
| Bulk actions | partly | there is no host-drivable bulk copy - `BulkOperation` only narrates progress - but `copy_preset()` and `duplicate_setlist()` achieve it by recall + save, at a few seconds per preset |
| Search | no | candidate `RecentSearches` |
| Sort | n/a | client-side once a listing is in hand |
| Neural Captures: list | yes | `captures()` browses the library - over 2000 entries, shown on the unit as Factory Captures V1/V2 and My Captures. NOT the catalog, which does not grow when a capture is saved |
| Load a capture onto the grid | yes | `set_capture(row, column, entry)` - the block model plus a `file_name` string of content hash + name |
| Neural Captures: rename, delete, manage | no | candidate `File` |
| Impulse responses: list and load into an IR Loader | yes | `list_irs()` lists the loadable IRs (`FileMessage.type: 1`; `"2_q"` is "My IRs") and `set_ir()` points a loader at one, on either of its TWO slots. `IR PATH` takes the library entry's KEY - `CIR_` plus a content id - NOT a path, with the display name in a separate `IR NAME` parameter. The 588 entries under `/opt/neuraldsp/impulse_responses` are plugin assets with no key that the unit cannot load, and are excluded. Importing an IR from the host is still unsolved - use Cortex Control |
| Plugin presets | no | candidate `License`, `CloudProduct` |
| Upload to Cortex Cloud | no | candidates `CloudProduct`, `ProcessDownloadsQueue` |

## 06 Neural Capture

| Feature | Status | Detail |
|---|---|---|
| Run a capture (v1, on the unit) | no | the unit hands the flow to a connected HOST via `NeuralCapture{try_to_show_dialog}`, so a connected client suppresses the on-device wizard. The engine is reachable as the `NC_Recorder`/`NC_Trainer`/`NC_Refiner` internal models |
| Capture v2 (from Cortex Control) | no | `NeuralCapture2` now decodes, but the flow is unexplored |
| Capture calibration settings, A/B test, metadata | no | as above. `NeuralCapture` carries `state`, `progress`, `toggle_ab_model`, `model_ab_bypass`, `save_info` and `error_id` |
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
| MIDI settings: channel, Thru, over USB, ignore duplicate PC, clock in/out | partly | these are in `GeneralSettings`, not the undecoded `MIDISettings`. `midi_channel`, `midi_over_usb`, `ignore_duplicate_pc`, `midi_clock_in_enabled` and all four `midi_clock_out` values (OFF / DIN / USB / BOTH) are confirmed writable via `update_settings()`, and Thru via `set_midi_thru()`. One gap: `internal_midi_clock_enabled` REFUSES a write, and it stays true with external clock either way |
| Preset MIDI Out: footswitch, expression and on-load messages | yes | `set_midi_out()` / `set_preset_load_midi_out()` via `MIDISettings`, NOT `Grid`. CC/CC Toggle/PC all confirmed |

## 10 Device Settings menu

Every row here is unexplored, and all of it is global rather than per preset.
`GeneralSettings` carries most of this menu. Fifteen of its fields are now confirmed
writable one at a time and restored; `internal_midi_clock_enabled` is the only one that
refused. Two scales mislead: brightness is quantized, and `dimmed_led_brightness` is
capped just below `led_brightness` so the dimmed state stays dimmer (asking for 100 landed
on 25, 9 and 56 as `led_brightness` was 28, 13 and 59).

| Feature | Status | Detail |
|---|---|---|
| GLOBAL BYPASS (Cab / IR Loader per row) | yes | `set_global_bypass(cab=..., ir=...)`, four booleans per collection |
| SCENE BYPASS BEHAVIOR (3 modes) | yes | `set_scene_bypass_behavior()` with the `SceneBypassBehavior` enum. It decides what `set_bypass` persists |
| STOMP MODE BYPASS (auto-assign on load) | yes | `update_settings(stomp_mode_auto_assign=...)`, confirmed writable |
| HOLD TIMING, SWAP TEMPO AND TUNER, GIG VIEW ACCESS | yes | all three confirmed writable via `update_settings()`. `set_hold_timing()` takes MILLISECONDS and writes the index the device stores - the unit offers 500-1000 ms in 100 ms steps and the field is the index, confirmed by reading 3 while the screen showed 800 ms. `hold_timing_ms()` reads it back |
| LATENCY COMPENSATION | yes | `update_settings(enable_dynamic_delay_compensation=...)`, confirmed writable |
| Device name | no | candidate `Serialization`, `GeneralSettings` |
| Firmware and serial | yes | `version()` |
| Diagnostics (DSP, footswitches, USB) | no | `ModuleStats` is decoded and subscribed; `Diagnostics`, `DSPCommsDiagnostics` are not |
| CorOS updates | no | `Updater` is decoded and subscribed; never driven. Risky to explore |
| Brightness, power sensitivity, storage | yes | screen, LED and dimmed-LED brightness all confirmed (quantized: 30 reads back 31), plus the three dimming toggles; disk space is reported. `power_option` and `reset_wifi_networks` are refused by `update_settings()` as commands |
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
