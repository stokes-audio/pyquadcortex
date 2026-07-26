# changelog

What changed between released versions, from the point of view of someone
installing the package. The git history has the detail and the reasoning; this
file answers the narrower question "I upgraded, what is different for me?".

Versions follow the usual 0.x convention: the minor number moves for new
capability, the patch number for fixes. Anything may still change while the
major number is 0.

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
