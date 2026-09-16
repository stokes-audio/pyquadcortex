# Contributing

> Purpose: how to set up, test, and submit a change to pyquadcortex, including the rules a pull request follows before it is marked ready.

Thanks for your interest. Bug reports, fixes, new operations, documentation and
tests are all welcome. By submitting a contribution you agree that it is licensed
under the project's [MIT License](LICENSE).

**New here?** [docs/architecture.md](docs/architecture.md) explains how the
library is put together and walks through adding an operation it does not
implement yet.

## How contributions work

You do not need to ask for access first.

1. **Fork** this repository.
2. Create a **branch** for your change.
3. Commit, push to your fork, and open a **draft pull request** against `main`.
   Mark it ready once the checks under "Before you mark a pull request ready"
   have run, or the description says why they could not.
4. A maintainer reviews it. Every change is reviewed before it is merged, so
   expect a round or two of feedback.

CI runs the offline suite, mypy and a packaging build on every pull request. A red
build blocks the merge. Green proves the library agrees with itself. Only a unit
proves it agrees with the unit, which is why the hardware suite is part of every
pull request too.

## Development setup

You need **Python 3.11 or newer**. To talk to a unit you also need the **hidapi**
C library (macOS: `brew install hidapi`; Debian/Ubuntu:
`sudo apt install libhidapi-hidraw0`; Windows: included with the `hid` wheel).
The tests do not need it.

[`uv`](https://docs.astral.sh/uv/) is recommended:

```bash
git clone https://github.com/stokes-audio/pyquadcortex
cd pyquadcortex
uv venv && uv pip install -e ".[dev]"
```

With plain `pip`:

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
```

### The protobuf bindings

The generated `pyquadcortex/protocol/proto/*_pb2.py` bindings and their `.pyi`
stubs are **committed on purpose**. That is what lets `pip install` work without
a protoc toolchain. Do not add them to `.gitignore`.

Regenerate them only when the schema changes:

```bash
scripts/compile_protos.sh
```

The script refuses to write bindings older than the committed ones. If it does,
reinstall the dev extra (`pip install -U -e ".[dev]"`). A newer generator means
raising the `protobuf` pin in `pyproject.toml` in the same commit. Details and
the reasons are in [docs/architecture.md](docs/architecture.md), "The generated
protobuf bindings".

## Running the tests

```bash
.venv/bin/python -m pytest -q
```

mypy blocks a merge, so run it before pushing:

```bash
.venv/bin/python -m mypy
```

The package is expected to be clean. No error is suppressed except the missing
stubs for `hid`. See ADR-0018.

The suite is fully offline. It needs no unit and does not import `hid`, so no
`DYLD_LIBRARY_PATH` prefix is needed even on macOS. Add or update tests for any
behaviour you change.

Two contracts the tests protect:

- `import pyquadcortex` and `qcctl --help` work without hidapi installed. Any
  `import hid` stays inside the function that opens the device.
- The client layer speaks only protobuf, never HID, so it is tested against a
  fake transport.

## Working with hardware

- Connect the Quad Cortex over USB. Wi-Fi may stay on.
- **Quit Cortex Control first.** It holds the USB interface exclusively.
- The protocol is unversioned and can change across CorOS releases. This library
  is verified against **Quad Cortex, CorOS 4.0.1** (firmware `d14e`). A different
  firmware or a Mini is a different device profile (ADR-0020). Record what you
  measure beside the 4.0.1 record in `docs/protocol.md`, dated and named by CorOS
  version. To add a profile, follow [docs/architecture.md](docs/architecture.md),
  "Adding a device profile".

### Before you mark a pull request ready

A draft is work in progress, and a maintainer does not review one. Two things
happen before it becomes reviewable, both on the **same final commit**: the
hardware suite runs, and a review pass's findings are addressed. Changing the code
afterwards means doing both again, because each is evidence about the commit it
ran on.

Run the hardware suite on the final commit:

```bash
pytest tests/hardware --hardware
```

On macOS prefix the command with `DYLD_LIBRARY_PATH=/opt/homebrew/lib`. On a unit
the registry refuses, add `--profile` with the class to measure as (see
`tests/hardware/readme.md`).

Put four things in the description:

1. the short commit hash the run was made on
2. the CorOS version of the unit
3. the `operations on ...` block the suite prints
4. pytest's own last line (the pass, fail, error and skip counts)

If your change adds or alters an operation, also say how you verified it on the
unit: a read-back, or the screen. The suite measures only the operations a
hardware test names.

If you have no unit, say so in the description and mark the pull request ready
anyway. A maintainer runs the suite before merging. If the change cannot reach the
wire (documentation, packaging), a maintainer may waive the run, in the
description. Either way the description says which it was. A description that says
nothing about hardware is not ready for review, whatever GitHub shows.

## Style

Match the style of the surrounding code. Keep changes focused. Unrelated cleanups
are easier to review as separate pull requests.

Documents follow [docs/writing.md](docs/writing.md).

In user-facing text, describe the project as speaking the device's own protobuf
protocol. It is a USB client, like Cortex Control, and requires no modification to
the device.
