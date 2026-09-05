# Contributing

Thanks for your interest in improving this project. Contributions of all kinds are
welcome - bug reports, fixes, new operations, documentation, and tests.

By submitting a contribution you agree that it is licensed under the project's
[MIT License](LICENSE).

**New here?** [docs/architecture.md](docs/architecture.md) explains how the library
is put together and walks through adding support for an operation it does not
implement yet. That is the place to start if you want to extend it.

## How contributions work

You do **not** need to ask for access or to be added to the project first. The flow is:

1. **Fork** this repository to your own account.
2. Create a **branch** for your change.
3. Commit your work, push it to your fork, and open a **pull request** against `main`.
4. A maintainer reviews it. Every change is reviewed and approved before it is merged,
   so please be patient and expect a round or two of feedback.

Continuous integration runs the test suite on every pull request. Please make sure it
is green - a red build will block the merge.

## Development setup

You need **Python 3.11 or newer** and, to talk to real hardware, the **hidapi** C
library (macOS: `brew install hidapi`; Debian/Ubuntu:
`sudo apt install libhidapi-hidraw0`; Windows: included with the `hid` wheel).
hidapi is not needed to run the tests.

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

The generated `pyquadcortex/protocol/proto/*_pb2.py` bindings are **committed to the
repository on purpose** - that is what lets `pip install` work without a protoc
toolchain. Please do not add them to `.gitignore`.

You only need to regenerate them when working against an updated device schema:

```bash
scripts/compile_protos.sh
```

If you regenerate with a newer `protobuf`, the runtime pin in `pyproject.toml` must
be raised to match the generated code, or imports will fail for everyone else.

Regenerating with an *older* generator is the quieter mistake, so the script
checks for it: if your `grpcio-tools` would write older gencode than what is
committed, it refuses and leaves the bindings alone. Reinstall the dev extra
(`pip install -U -e ".[dev]"`) to get a generator at or above the pinned floor.
See [docs/architecture.md](docs/architecture.md) for the details.

## Running the tests

```bash
.venv/bin/python -m pytest -q
```

CI also runs a type checker, and it BLOCKS a merge, so run it before pushing:

```bash
.venv/bin/python -m mypy
```

The package is expected to be clean - no error is suppressed, and the generated
protobuf bindings are checked through the committed `*_pb2.pyi` stubs beside
them. See ADR-0018.

The suite is fully **offline** - it needs no Quad Cortex and does not import `hid`
(so no `DYLD_LIBRARY_PATH` prefix is needed, even on macOS), which means you can
develop and test most changes with no hardware attached. Please add or update tests
for any behavior you change.

Two contracts the tests protect, worth knowing before you change import structure:

- `import pyquadcortex` and `qcctl --help` must work **without** hidapi installed.
  Any `import hid` therefore stays lazy, inside the function that opens the device.
- The client layer speaks only protobuf, never HID, so it can be tested against a
  fake transport.

## Working with hardware

If your change touches the live device path and you want to verify it on a real unit:

- Connect the Quad Cortex over **USB**. (Wi-Fi may stay on; it makes no difference.)
- **Quit Cortex Control first.** It holds the USB interface exclusively, so nothing
  else can talk to the device while it is running.
- The device protocol is **unversioned** and can change across CorOS / Cortex Control
  updates. This library is verified against **Quad Cortex, CorOS 4.0.1** (firmware
  `d14e`); if you are on a newer version, re-verify the framing and schema before
  assuming a bug. A different firmware or a Mini is a different DEVICE PROFILE
  (ADR-0020): record what you measure beside the 4.0.1 record in `docs/protocol.md`,
  dated and named by CorOS version, rather than in its place.

When you confirm behavior on hardware, say so in the pull request: which operation,
which CorOS version, and how you verified it (a read-back, or watching the unit).
That evidence is what keeps [docs/protocol.md](docs/protocol.md) trustworthy.

## Style

Match the style of the surrounding code. Keep changes focused - unrelated cleanups are
easier to review as separate pull requests.

In user-facing text, describe the project as speaking and re-implementing the device's
own protobuf protocol - it is a USB client, like Cortex Control, and requires no
modification to the device.
