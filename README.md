# pyquadcortex

[![CI](https://github.com/stokes-audio/pyquadcortex/actions/workflows/ci.yml/badge.svg)](https://github.com/stokes-audio/pyquadcortex/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A Python library and command-line tool to control a Neural DSP Quad Cortex over
USB using the device's own Protobuf protocol - recall, read, edit, copy, create,
delete, and move presets and scenes, the same operations Cortex Control performs.
The library imports as `pyquadcortex`; the command-line tool is `qcctl`.

> Unofficial and not affiliated with, endorsed by, or supported by Neural DSP
> Technologies. "Quad Cortex" and "Neural DSP" are trademarks of their owner and
> are used here only to describe what this software talks to.

## Status

**Working and verified on macOS (Apple Silicon) and Windows.** The full control
path - connect, read the firmware version, recall and read presets, switch
scenes, re-route inputs, edit parameters and bypass, save/delete/move presets,
and enumerate setlists - has been exercised live on hardware (firmware `d14e`).
The unit test suite (63 tests) runs fully offline with no device attached.

The one subtlety worth knowing up front: the Quad Cortex accepts every HID
output report but then STALLs its USB status stage, which host stacks surface as
a write error (macOS IOKit `0xE0005000`, hidapi `-1`). That "error" is benign -
the device acted on the data. The transport ignores it and detects a genuinely
dead device via request timeouts instead. See
[`pyquadcortex/framing_spec.md`](pyquadcortex/framing_spec.md) for the full
wire-protocol writeup and a per-operation coverage table.

## Install

```bash
pip install pyquadcortex
```

The wheel ships pre-generated protocol bindings, so a plain install works with
no build tools or `protoc`. You still need the **hidapi C library** at runtime -
the `hid` dependency is a ctypes binding to it, and it is what actually opens the
device:

- macOS: `brew install hidapi`
- Debian/Ubuntu: `sudo apt install libhidapi-hidraw0`
- Windows: ships with the `hid` wheel; no extra step.

Importing the library or running `qcctl --help` needs neither hidapi nor a
device; only opening the Quad Cortex does. On macOS, see the dyld note below.

## Prerequisites (from source)

- **macOS or Linux or Windows**, **Python >= 3.11**.
- The **Quad Cortex connected by USB** (not Wi-Fi). On macOS, hidapi enumerates
  it as VID `0x152A` / PID `0x880A`, HID interface 5.
- The **hidapi and protobuf C libraries**. macOS: `brew install hidapi protobuf`.
- **`uv`** recommended (a `pip` fallback is shown below).

### macOS dyld note

The PyPI `hid` package loads `libhidapi` by bare name, and Homebrew's lib
directory is not on the default dyld search path. Prefix any command that opens
the device (the CLI and the `examples/`) with:

```bash
DYLD_LIBRARY_PATH=/opt/homebrew/lib
```

The unit-test suite does not import `hid`, so it needs no prefix.

## Setup (from source)

```bash
git clone https://github.com/stokes-audio/pyquadcortex
cd pyquadcortex
uv venv && uv pip install -e ".[dev]"
```

pip fallback:

```bash
git clone https://github.com/stokes-audio/pyquadcortex
cd pyquadcortex
python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
```

## Compile the Protobuf schema

Pre-generated `*_pb2.py` bindings are committed under `pyquadcortex/proto/` and shipped
in the wheel, so PyPI installs need no compile step. Regenerate them only when
working from source against an updated schema - the protocol is unversioned and
can change across Cortex Control updates:

```bash
scripts/compile_protos.sh
```

The bindings are tied to the `protobuf` runtime major version they were
generated with (see the `protobuf` pin in `pyproject.toml`); if you regenerate
with a newer `protobuf`, bump that pin to match.

## Safety

1. **USB only** - the Quad Cortex must be on USB, not Wi-Fi.
2. **Quit Cortex Control before using qcctl.** It opens the HID interface
   exclusively (seize mode); while it runs, no other process can open the
   device. Keep it closed while qcctl runs.
3. **The protocol is unversioned** - re-verify the schema and framing after a
   CorOS / Cortex Control update.

## Quickstart (library)

```python
from pyquadcortex import client, hid_ids, transport
import hid

dev = hid.Device(hid_ids.VENDOR_ID, hid_ids.PRODUCT_ID)  # macOS 'hid' package
t = transport.Transport(dev)
t.start()
qc = client.QuadCortex(t)
try:
    qc.hello()                        # REQUIRED connect gate - do this first

    # Read the preset currently recalled from a factory slot.
    p = qc.read_preset(client.FACTORY_LIBRARY_PATH, 212, is_factory=True)
    print(p.name)                     # "Cali Basswalk"

    # Re-point its input(s) from Input 1 to Return 1 and save a user copy.
    qc.reroute_grid_input(p, client.RETURN_1)
    qc.save_current_preset(client.USER_PRESETS_PATH,
                           client.slot_to_position("27A"),
                           "Cali Basswalk [Ret1]",
                           instrument=client.INSTRUMENT_BASS)
finally:
    t.stop()
    dev.close()
```

`hello()` performs the connect handshake the device requires before it will act
on commands and push state; always call it first.

## Supported operations (`client.QuadCortex`)

- **Session:** `hello()`.
- **Read:** `read_preset(setlist_path, position, is_factory=False)` - recalls the
  slot and returns the full `BinaryPreset` from the device's push.
- **Navigate:** `recall_preset(...)`, `switch_scene(index)`.
- **Edit (grid):** `set_chain_input(row, in_portid)`,
  `reroute_grid_input(preset, to_port)`, `set_param(row, column, param_index,
  value, scene=0)`, `set_bypass(row, column, bypassed, scene=0)`. See the
  edit-path note below.
- **Scenes:** `copy_scene(from, to)`, `set_scene_label(index, label)`,
  `set_scene_color(index, argb)`.
- **File:** `save_current_preset(setlist_path, position, name, instrument=0)`
  (saves the grid), `delete_preset(setlist_path, name)`,
  `move_preset(setlist_path, name, to_position)`.
- **Enumerate:** send a `File` READ and read the pushed folder listings
  (`FolderInfo.files[]` of `ProductData` with index/name/instrument). See
  `examples/list_presets.py`.

### The edit-path pattern

The device applies a grid edit by locating the chain/model by its `row`/`column`
key, and it saves whatever is on the grid. So an edit is always:

```
recall (loads the preset onto the grid)
  -> row/column-keyed edit(s): set_chain_input / set_param / set_bypass
  -> save_current_preset (snapshots the grid into a slot)
```

Writing a whole `BinaryPreset` back wholesale does NOT work (a recalled preset
carries no explicit `row`, so the device drops the change), and `File` CREATE
ignores any preset payload - it snapshots the grid. Use the keyed edit methods.

### Port and instrument constants

`Chain.in_portid` / `out_portid` use the device's own enums, confirmed on
hardware. `client` exposes named constants:

- Inputs: `INPUT_1=1, INPUT_2=2, INPUT_1_2=3, RETURN_1=4, RETURN_2=5,
  RETURN_1_2=6, PREV_ROW=7, USB_IN_5..8=8..11, USB_IN_5_6=12, USB_IN_7_8=13,
  SIDECHAIN_BUFFER=14` (`IN_EMPTY=0` = internally fed).
- Outputs: `OUT_XLR_1_2=1, OUT_3_4=2, OUT_SEND_1_2=3, OUT_XLR_1=4, OUT_XLR_2=5,
  OUT_3=6, OUT_4=7, OUT_SEND_1=8, OUT_SEND_2=9, OUT_USB_5..8=10..13,
  OUT_USB_5_6=14, OUT_USB_7_8=15, OUT_USB_3/4/3_4=20/21/22` (16-19 are internal
  grid-routing states).
- Instrument tags: `INSTRUMENT_GUITAR=1, INSTRUMENT_BASS=2, INSTRUMENT_VOCAL=4`.

Setlists are addressed by device path (`client.USER_PRESETS_PATH`,
`client.FACTORY_LIBRARY_PATH`); slots by linear index -
`client.slot_to_position("28C") == 218`.

## CLI

Installed as `qcctl` (prefix with the dyld path on macOS):

```bash
DYLD_LIBRARY_PATH=/opt/homebrew/lib qcctl version
DYLD_LIBRARY_PATH=/opt/homebrew/lib qcctl recall --slot 28C
DYLD_LIBRARY_PATH=/opt/homebrew/lib qcctl scene --index 3
DYLD_LIBRARY_PATH=/opt/homebrew/lib qcctl dump-preset --slot 28C
```

## Testing

```bash
.venv/bin/python -m pytest -q
```

The suite is fully offline (no device, no `hid` import, no dyld prefix needed) -
63 tests over the
frame codec, registry, transport (fake HID device), client (fake transport), and
CLI.

## Module layout

`pyquadcortex/` is one concern per file:

- **`framing.py`** - HID frame codec: logical `(message_type, protobuf_bytes)`
  <-> raw 129-byte HID reports.
- **`registry.py`** - `CortexMessageType` enum <-> generated protobuf classes.
- **`transport.py`** - framed I/O over an hidapi-like device: RX reassembly,
  request/response and broadcast correlation, keepalive; ignores the benign
  write stall.
- **`client.py`** - the high-level `QuadCortex` API (protobuf only; no HID).
- **`cli.py`** - argparse CLI (import-safe, device-free for testing).

Supporting: `hid_ids.py`, `proto/` (generated bindings), `framing_spec.md` (wire
protocol + coverage table). Runnable examples live in `examples/`
(`switch_scenes.py`, `list_presets.py`, `reroute_and_save.py`).

## Acknowledgements

Built by observing and re-implementing the device's own Protobuf control
protocol (schema recovered into `protocol/proto/`), verified against a real
Quad Cortex.
