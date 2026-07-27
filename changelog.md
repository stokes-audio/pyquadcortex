# changelog

What changed between released versions, from the point of view of someone
installing the package. The git history has the detail and the reasoning; this
file answers the narrower question "I upgraded, what is different for me?".

Versions follow the usual 0.x convention: the minor number moves for new
capability, the patch number for fixes. Anything may still change while the
major number is 0.

## 0.10.0 - 2026-07-27

Everything below was re-derived on hardware against factory presets before being
changed, so each item states what the device does rather than what was reported.

### Fixed

- **`splits()` dropped a branch that never recombines.** A row can report
  `split >= 0` with `mix == -1`: it branches and the lane does not rejoin. Those rows
  were skipped entirely, and the docstring explained the omission as "rows that do not
  branch report -1 for both", which is not what they report. Verified on three factory
  presets - "Strat Ambience" (05B) `(2, -1)`, "Classic Pedalboard" (07C) `(7, -1)`,
  "Stereo Lead" (11B) `(5, -1)`. A branch is now recognised by `split` alone, and
  `Split.rejoins` answers the other question. `Split.lane_row` gives the row the
  parallel lane occupies.
- **`set_block` could fail silently.** A placement the preset has no DSP capacity for
  is accepted on the wire and then is not there, with no error of any kind. It is now
  verified by default against the echo the device sends for each cell it accepts, and
  raises the new `BlockRefused` when none arrives; `verify=False` restores
  fire-and-forget. Reproduced deterministically: a six-block chain added to "OneStar
  Clean Tweed" (02C) places five and drops the bass cab, while the cheaper block after
  it in the same chain lands.
- **`real=` silently produced meaningless values for some parameters.** Where the
  catalog publishes `0..1` with a real-world unit - mixer and splitter levels, lane
  `VOLUME`, `TEMPO` - that range is the wire's own scale, not the span the control
  covers, so converting against it yields a number meaning something else. Those now
  raise `ValueError` (`Parameter.range_is_placeholder` is the test). This corrects a
  documented example: `set_lane_output(param="VOLUME", real=-3.0)` did not attenuate
  3 dB, it silenced the row.
- **`set_splitter_param` and `set_mixer_param` accepted rows that have no splitter or
  mixer.** They now raise `ValueError` for an odd row instead of sending a write into a
  collection the device does not have there.

### Added

- **`set_input_gate(row, param, value=/real=, scene=)`** and
  `INPUT_GATE_CONTROL = 28000`, for the per-row noise gate in
  `chains[].input_control[]` - the last of the four chain sub-collections without a
  setter. `NOISE REDUCTION`, `BYPASS` and `INPUT GAIN` are confirmed writable in both
  directions, per-scene included. `GAIN REDUCTION` is a meter, not a control: the
  catalog types it `grMeter`, and it is sampled at save time, which matters when
  diffing presets.
- **`free_rows(preset)`** - the rows available for an independent chain. A row is free
  only when it holds no blocks AND is not the parallel lane of a branch above it; that
  lane is frequently empty and still spoken for, so block count alone answers the
  question wrongly.
- **`UNITY_LEVEL`** (0.76923077) - what the mixer, splitter and lane level parameters
  hold when nothing is attenuated, measured on every row carrying one across 17 factory
  presets. Knowing it is what distinguishes a deliberately silenced lane from a default.
- **`SCENE_UNLABELLED`**, and `set_scene_label(index, None)` to write it. The unit
  stores an unlabelled scene as a single space, so `label.strip()` detects a blank
  scene and `label == ""` does not.

### Documentation

- **The claim that every row carries a splitter and mixer was wrong.** They exist only
  on rows 0 and 2 - counted across all 68 rows of 17 factory presets - because a branch
  can only originate on an even row with its lane below it. `output_control` and
  `input_control` are padded on all four rows; those four are not.
- **`copy_scene` carries the scene COLOUR as well as the label.** Verified with nothing
  else sent, and on the unit's own screen. So reproducing a scene map needs no
  `set_scene_color` calls for copied scenes.
- **Adding a block rewrites comboBox values on rows never written to.** A selector whose
  options enumerate the preset's blocks has its stored value recomputed when the block
  count changes: on "US TWN Vibrato" (01C) a Doubler `TRIGGER` moves 1/19 -> 1/20 -> 1/23
  as blocks are added, denominator `blocks + 6`, while a recall-and-save with no edit
  leaves it alone. Anyone diffing a preset before and after an edit will meet this.
  comboBox option names are not in `ModelRepo`, so what an index denotes is not knowable
  from the catalog.
- **`param_values` can contain NaN**, in at least four factory presets. Since
  `nan != nan`, a preset compared against itself reports differences - a false failure
  about a build that is in fact identical.
- **Preset `tags` cannot be written, and a saved preset has none.** Three routes are
  confirmed no-ops: `ProductData.tags` on the File CREATE, a File UPDATE carrying them,
  and a `Grid` UPDATE carrying `preset.tags`. The control settles it - a plain save
  reads back with an empty tag list whatever the source had - so nothing stale is
  inherited, and `instrument`, which is settable, is what the unit filters on.
- **DSP load is not readable.** `CPULoad{READ}` times out, adding `"CPULoad"` to the
  connect burst's subscribe READs produces no pushes, and listening across both saw
  none. So headroom cannot be checked before placing a block.
- **Enumeration:** a listing that arrives is complete (five READs on an 18-preset
  setlist, no short listing seen), but a READ does not reliably produce one promptly -
  two of those five saw nothing within 8 s. A timeout means "ask again", not "the
  setlist is empty".
- The per-unit capture-id claim was stated as fact on no evidence; it is now what was
  observed. What IS established: 13 of 17 surveyed factory presets reference capture id
  14000 from positions no single capture could fill at once, so factory presets appear
  to reference capture slots. Whether an id denotes different content on another unit is
  untested here.
- New `docs/protocol.md` sections for the capacity refusal, placeholder ranges, NaN and
  the comboBox behaviour, all listed in the contents; `docs/capture.md`'s noise list
  corrected to what actually arrives.

### Examples

- `inspect_preset.py` reports whether a branch rejoins, which row its lane occupies,
  and which rows are genuinely free; it also skips NaN before comparing per-scene
  values, for the reason above. `build_chain.py` picks its row with `free_rows()`,
  handles `BlockRefused`, and uses `UNITY_LEVEL` for the lane level.

## 0.9.0 - 2026-07-27

### Added

- Two new examples covering the newer API. **`inspect_preset.py`** is read-only and
  prints a preset's blocks by name, its routing, where rows branch into parallel lanes,
  and which parameters differ per scene - a good first thing to run.
  **`build_chain.py`** builds a chain on an empty row: block, input, output, a parameter
  in its own units, and a scene that silences the row. Both avoid bare numbers, and the
  examples are now listed in the readme with what each one touches.
- **[docs/capture.md](docs/capture.md)** - how to read the device's own broadcasts when
  you need a message shape this library does not implement yet, including the pitfalls
  that decide whether a capture is interpretable. Linked from the contributor guide.

### Documentation

- The docs described how several findings were arrived at, which is of no use to someone
  using the library. Rewritten to state how the device behaves and what has been
  verified against hardware. Where a constraint matters it is now stated as a constraint:
  for instance `chain.splitter[]` is a read-only view whose writes are silently ignored,
  which a caller needs to know, without the account of how that was discovered.

## 0.8.0 - 2026-07-27

Field feedback from a couple of dozen sessions and several thousand writes.

### Fixed

- **`wait_for_listing()` no longer aborts on a missed push.** It exists to absorb
  eventual consistency, but called `list_presets()` bare in its poll loop, so a single
  quiet interval raised `TimeoutError` straight out of it - producing exactly the false
  negative its own docstring warns about. One report had it kill a 14-preset build at
  preset 8, after a save that had already succeeded. It now rides out missed pushes
  until its own `timeout`, and you no longer need to wrap it in a retry.
  Its two failures are also now distinguishable: *the condition never became true*
  means listings arrived and your predicate stayed false, while *the device stopped
  pushing listings* means nothing was evaluated - so only the first tells you anything
  about whether your change landed.
- **Corrected `out_portid` 19.** The docs and `set_chain_output`'s docstring lumped
  16-19 together as internal routing. Wrong, and it steered users away from the right
  answer: **16 to 18** are internal row-to-row routing, but **19 (`MULTIPLE`) is a real
  destination** and is what factory presets use to reach the Multi-Out - often exactly
  the value you want when building a chain that has to be audible.

### Documentation

- **A Troubleshooting section**, covering a failure mode whose symptoms actively
  mislead: the unit's USB link can die mid-session, with Cortex Control quit, the cable
  in and the unit booted - so the existing error message sends you the wrong way. It
  now points at the readme. Includes how to tell a flapping port from a plain
  disconnection, that only a full power-down recovers it, and that the link flaps for
  a couple of minutes afterwards in a way that looks identical to the fault. Framed as
  one user's field experience with the cause unknown, not as a diagnosis.
- **MIDI is the simpler route if you only need to switch presets or scenes** - it is
  manufacturer-documented and needs no USB session. This library is for creating and
  editing content, which MIDI cannot do.

## 0.7.0 - 2026-07-27

### Added

- **The device is now told when a client goes away.** Closing a session sends
  `Connection{connected: false}` first, before the transport stops and the handle
  closes, which is what Cortex Control does on quit. Previously this library
  announced the connect and then simply went quiet, so from the unit's point of view
  a client never left - it just stopped sending keepalives.
- **`QuadCortex.disconnect()`** is public, for callers who supply their own transport
  and therefore own teardown. There was no non-private way to send this before.
  It is best effort: a failure never prevents the rest of teardown, which matters
  little in practice since every write on this device is reported as failing anyway
  thanks to the deliberate status-stage STALL.
- `qcctl` gets this for free - it already goes through `connect()`.

### Note

Whether an abandoned session leaves state behind on the device is not established -
there is no device state to read back. This change matches Cortex Control's behaviour;
it is not a fix for a known fault, and no workaround was added for one.

## 0.6.0 - 2026-07-27

Parallel routing is now fully writable, and the grid can be read.

### Added

- **`set_splitter_param(row, param, ...)`**, with `scene=` like the others. It writes
  `chain.combined_splitter`; the `chain.splitter[]` a preset exposes is a read-only view
  of the same state, and writes addressed there are silently ignored. Parameters are
  addressed by the unified model 10004's order (`TYPE`, `STEREO`, `BALANCE`, `LEVEL TO
  A`, `LEVEL TO B`, `FREQUENCY`, `MODE`) whatever type-specific id a preset reports.
- **`splits(preset)`** reports where each row branches into a parallel lane and where
  it rejoins, so grid topology can be read rather than inferred. It reads
  `Chain.split_control_points`, whose `split` and `mix` fields have no presence - so
  anything gating on `HasField` sees nothing and must read them directly. Rows that do
  not branch report `-1` and are omitted.

## 0.5.0 - 2026-07-26

Four gaps that stopped a tester building bass presets from a script. Everything here
was verified on hardware by read-back.

### Added

- **`set_chain_output(row, out_portid)`** - the sibling of `set_chain_input`, and the
  piece that was blocking. Without it a chain built on an empty row could be given
  blocks and an input but never pointed at a jack. **The device does not assign an
  output on its own**, confirmed by adding a block and Input 2 to an empty row and
  reading back `out_portid` still unset - so this is a requirement, not a convenience.
- **`set_mixer_param(row, param, ...)`**, with `scene=`. This is how factory presets
  build scenes: "Darkglass AO900 1" bypasses nothing in any scene and produces all
  eight from per-scene mixer `LEVEL A` / `LEVEL B` across two rows.
- **Per-preset tempo**: `set_tempo_led(on)`, `set_metronome_volume(v)` and the general
  `set_tempo_param(param, ...)`. Reported as having no write path; it turned out
  `tempoProgramData` is applied by a `Grid` UPDATE even though it is not row or column
  keyed. `LED LIGHT` 1.0 -> 0.0 turns the LED off and `VOLUME` -> 0.0 silences the
  metronome, both surviving save and recall.
- **`save_current_preset(default_scene=...)`**, which switches to that scene first
  because the device records whichever is active at save time.
- **`position_to_slot(pos, pad=True)`** for the zero-padded form.

### Fixed

- `slot_to_position` accepted banks past the end of a setlist: `"33A"` returned 256,
  the device ignored the save, and it surfaced 40 seconds later as a read timeout -
  exactly the "the save failed" symptom reported. Both slot helpers now range-check.
- `position_to_slot`'s output could not be compared against the padded form
  `slot_to_position` accepts. Documented, with comparing linear positions recommended.

### Known limits

- **The splitter does not accept host writes.** Four shapes tried - with and without
  the model hash, on a level and on a switch - each saved and read back unchanged,
  while the identical shape against the mixer works. `set_splitter_param()` raises
  instead of silently doing nothing.
- **Splitter and mixer carry no column**, so where a split sits on the grid cannot be
  read, only inferred. The grid topology is only partly recoverable.
- `GlobalTempo` is global and returns only a running clock;
  `MetronomeStatusUpdate` has no mute or level field, which is why muting means
  setting `TempoControl.VOLUME` to zero.

## 0.4.0 - 2026-07-26

**Per-scene parameter values.** A parameter can now hold a different value in each
scene, which was the biggest functional gap: scripts could reproduce a preset's
structure but not its scene behaviour, so anything performable had to be finished
by hand on the unit.

### Added

- **`set_param(..., scene=Scene.D)`** writes one scene and leaves the other seven
  alone, promoting the parameter to scene-following if it is not already.
- **`set_lane_output(..., scene=Scene.E)`** does the same for the per-row Lane
  Output Control, so a **silent scene** - one that mutes the rig without leaving
  the preset - is now scriptable:
  `qc.set_lane_output(row=0, param="VOLUME", value=0.0, scene=Scene.E)`
- **`set_param_scene_mode(row, column, param_index, enabled)`** and
  **`set_lane_output_scene_mode(row, param_index, enabled)`** for explicit control
  over whether a parameter follows scenes at all.

### Documentation

- **Rows and columns are zero-based, and the unit labels rows 1 to 4.** This was
  never stated, and getting it wrong is silent: the edit lands on a real row and
  reads back perfectly, just not the row intended. Also noted that `out_portid` 16
  to 19 are internal grid routes rather than physical outputs, so a lane can be
  muted without silencing anything that leaves the unit.

### How per-scene values work

Three things have to hold, and the library sequences them for you:

- `param_values[0]` applies to whichever scene is **active**; the index is not a scene
  selector, so nothing is ever padded.
- Per-scene values are kept only for a parameter whose `scene_mode` is set. Without it
  a parameter has one global value, which appears in all eight scenes.
- `scene_mode` must travel in a message carrying nothing else; sent alongside a value
  it is dropped.

So a per-scene write is three messages: the flag alone, a scene switch, then the value.
No settle delay is needed between them.

## 0.3.0 - 2026-07-26

Fixes from a review written while building a real preset-generation script against
0.1.0. Two of these were silent and destructive, so upgrade before scripting
anything that edits scenes.

### Fixed

- **`set_param` no longer destroys a parameter across all scenes.** Passing
  `scene=N` above zero padded the message with protobuf defaults below index N;
  the device reads index 0, so the parameter was set to **0.0 in every scene**. It
  now refuses a non-zero scene and explains why: the device applies a parameter
  write to all eight scenes at once and cannot target one.
- **`set_bypass(scene=...)` now works instead of corrupting a different scene.**
  The same padding wrote a default `False` to whichever scene was ACTIVE and did
  nothing to the one asked for. Bypass really is per scene, just not by index: the
  device applies `sceneBypass[0]` to the active scene. Naming a scene now switches
  to it and writes, which leaves the unit on that scene - a visible side effect.
- **`DeviceNotFoundError` is actually raised.** `hid.HIDException` is not an
  `OSError`, so the guidance for the most common first-run failure - unit not
  connected, or Cortex Control still holding the port - was dead code and users got
  a raw traceback.
- **Saving, deleting and moving no longer raise `TimeoutError` on success.** File
  operations are asynchronous and the device often does not reply; a missing reply
  never meant failure.
- Corrected `input_chain_rows`'s worked example, which cited a preset that does not
  have the routing described and contradicted the rule it illustrated.

### Added

- **`field_present(msg, field)`** - `HasField` raises on fields without presence,
  and the schema has many, including `SceneBypass.bypass`. This answers `False`
  instead of crashing, so walking per-scene bypass works.
- **`blocks(preset)`** - the occupied grid cells. Every row reports all 8 column
  slots whether or not they hold anything, so `len(chain.models)` is not a block
  count, and `in_portid == EMPTY` is not an occupancy signal either.
- **`wait_for_listing(setlist, until=...)`** - polls until a listing settles.
  Settling time grows with the number of changes, so a fixed sleep reports failure
  on work that succeeded.
- **`set_lane_output(row, param, value=/real=)`** - the per-row Lane Output
  Control (VOLUME, PAN, MUTE, SOLO), which lives outside `models[]` and so was
  unreachable through the API.
- **`position_to_slot(218) -> "28C"`**, the inverse of `slot_to_position`.
- **`Instrument.NONE`**, so an untagged save is a real enum member rather than a
  bare 0.
- `save_current_preset(confirm=True)` returns the name the device actually stored,
  which can differ from the one requested when it de-duplicates.

### Documentation

- The per-scene write ceiling is now stated plainly, because it decides whether a
  whole class of automation is possible.
- Corrected the claim that nearly every scalar has presence, with the real rule and
  the exceptions.
- Documented slot padding, the lane output block, and that listing lag scales with
  the number of mutations.

### Testing

- Added tests against a **real preset payload** read off a device, rather than only
  against messages this library builds. Both presence and padding findings were
  invisible to construction tests, and two existing tests had asserted the buggy
  construction was correct.

## 0.2.0 - 2026-07-26

Grid blocks and the device's own model catalog. Before this, the library could
edit the blocks a preset already had but could not add or remove one, and had no
idea what any block actually was.

### Added

- **`set_block(row, column, model)`** places a block on the grid, whether the
  cell is empty or already occupied. **`remove_block(row, column)`** clears one.
- **`qc.catalog`** reads the block catalog off the connected unit and caches it:
  every model that unit has, with categories, parameter names, real-world ranges,
  and units. Look models up by id, by name, or by category.
- **`pyquadcortex.models`** holds generated constants for the 412 factory blocks,
  grouped by category, so common blocks can be named in code:
  `models.GuitarOverdrive.CHIEF_DS1`. Purchased plugin content and Neural
  Captures are deliberately excluded, because their ids differ from unit to unit;
  resolve those through `qc.catalog` at runtime.
- **`set_param` accepts a parameter by name** (`param="THRESHOLD", model=...`)
  instead of a positional index, and accepts **`real=`** to pass a value in the
  parameter's own units. Wire values are normalized 0 to 1, and the catalog's
  range is what makes the conversion possible, so `real=-12` on a threshold in dB
  now means what it says.
- `docs/releasing.md`, the release checklist, written after an earlier upload
  shipped a readme that had been built before the last edit to it.

### Changed

- Source distributions now carry the whole `scripts/` directory. The include list
  previously named `compile_protos.sh` on its own, which quietly left
  `generate_models.py` out of the sdist even though the docs tell contributors to
  run it.

### Notes

- There is no 0.1.1 release. The version was bumped for a documentation fix and
  the block and catalog work landed before it was ever published, so it shipped as
  0.2.0 instead.

## 0.1.0 - 2026-07-24

First release, published to TestPyPI only.

Control of a Quad Cortex over USB, speaking the device's own protobuf protocol:

- `connect()` opens the device, runs the connect handshake, and hands back a
  ready client that also works as a context manager.
- Read the firmware version, list a setlist, find a preset by name, recall a
  preset, read a preset back in full, and switch scenes.
- Edit the grid: input routing, parameters, and bypass.
- Scenes: copy or swap, and set labels and colors.
- Manage presets: save, delete, and move.
- Presets are addressed by name, by the slot name shown on the unit (`"28C"`), or
  by index. Ports, instruments, scenes, and setlists are named enums rather than
  bare numbers.
- A `qcctl` command-line tool for the common one-off actions.
