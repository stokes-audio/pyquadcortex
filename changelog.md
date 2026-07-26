# changelog

What changed between released versions, from the point of view of someone
installing the package. The git history has the detail and the reasoning; this
file answers the narrower question "I upgraded, what is different for me?".

Versions follow the usual 0.x convention: the minor number moves for new
capability, the patch number for fixes. Anything may still change while the
major number is 0.

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
