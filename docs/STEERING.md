# Steering: pyquadcortex

> **What this is:** durable technical context for the pyquadcortex library - what the system is and why it is shaped this way.
> **What this is not:** coding rules (see the repo-root `CLAUDE.md`) or decision rationale (see [`ADR.md`](ADR.md)).
> **Last reviewed:** 2026-08-03 by Stokes
> **Owners:** Stokes

## 1. Purpose

pyquadcortex is an unofficial Python library for controlling the Neural DSP Quad Cortex over USB HID, speaking the device's own protobuf control protocol. Explicit context matters here because nearly every fact in this codebase is empirical: the schema was recovered from Cortex Control, behavior was verified by observing and driving real hardware, and the device accepts-and-ignores writes it does not understand rather than rejecting them. The written record (docstrings, [`protocol.md`](protocol.md), the coverage table) is the only trail of what is actually known, so work that ignores it can look correct while doing the wrong thing on the unit.

## 2. Scope and Boundaries

### In scope

The whole repository: the `pyquadcortex/` package (including the committed generated bindings in `pyquadcortex/proto/`), the recovered schema in `protocol/`, tests, examples, docs, and tooling.

### Out of scope

- The device's firmware update path (`Updater`): permanently out of scope, not pending (see [`roadmap.md`](roadmap.md)).
- The owner's cloud account surfaces (`CloudLogin`, `CloudBackup`, capture sharing): parked; driving them needs the owner's explicit permission first.

### Integration points

- **The device itself:** wire behavior is verified against CorOS / Cortex Control 4.0.1, device firmware d14e. The protocol carries no version number, so nothing is negotiated at runtime.
- **hidapi:** the `hid` pip package is a ctypes binding to the OS-level hidapi C library, which users install themselves (see README).
- **PyPI:** published as `pyquadcortex` with the `qcctl` console script; release process in [`releasing.md`](releasing.md).
- **Planning notes:** the maintainer's planning material for future work (including the domain model) lives in a separate private repo; this repo carries only the library and its engineering docs.

## 3. Architecture Overview

### System shape

A strict one-concern-per-file layering: `cli` → `session` → `client` → `transport` → `registry`/`framing` → hidapi, where each layer knows only the layer directly below it. `QuadCortex` (`client.py`) is the public API and knows nothing about HID reports, framing, or bytes; everything time-dependent and concurrent lives in `transport.py`. The full layer map, message flow, and the recipe for adding an operation are in [`architecture.md`](architecture.md) - that document is the deep reference and is not duplicated here.

### Data and state

The library is stateless between calls: every read is a live exchange, and the unit is the source of truth. The planned domain model layer (see [`roadmap.md`](roadmap.md)) introduces a broadcast-fed write-through cache above `client.py`; until it lands, callers hold whatever state they need.

## 4. Owned Paths

- `pyquadcortex/` - the package, including the committed generated bindings in `pyquadcortex/proto/`
- `protocol/` - the recovered `.proto` schema and its tooling
- `tests/` - the fully offline suite and its fixtures
- `examples/` - runnable scripts, also used as hardware-verification shapes
- `docs/` - protocol record, architecture, coverage, this file
- `scripts/` - `compile_protos.sh`
- `.github/workflows/` - CI

## 5. Patterns in Use

| Pattern | What | Why (or `see ADR-000N`) | Canonical example | When the pattern does not apply |
|---------|------|-------------------------|-------------------|----------------------------------|
| Layered message flow | New operations are a registry entry plus a thin `QuadCortex` method that builds a protobuf and picks `send`/`request`/`await_broadcast` | Wire concerns stay below `client.py`, which keeps the whole API testable with a fake transport (see ADR-0002) | `QuadCortex.switch_scene` in `pyquadcortex/client.py` | `cli.py`'s `version` subcommand deliberately bypasses the connect handshake (`_open_unconnected`) |
| Fake-per-layer offline tests | Each layer has a purpose-built double: golden captured frames for `framing`, `FakeHid` for `transport`, `FakeTransport` for `client` | see ADR-0002 | `FakeTransport` in `tests/test_client.py` | Hardware verification happens manually via `examples/`, outside the suite |
| Evidence-bearing docstrings | Each operation's docstring states what is confirmed on hardware vs inferred from the schema | The device gives no errors for wrong writes, so recorded evidence is the only trail | `QuadCortex.read_preset` in `pyquadcortex/client.py` | Non-protocol helpers (pure functions) carry ordinary docstrings |
| Keyed grid edits | Mutations are row/column-keyed `Grid` UPDATEs | The device applies grid updates by key; wholesale preset writes are silently ignored (see [`architecture.md`](architecture.md), "write_preset is a trap") | `QuadCortex.set_bypass` in `pyquadcortex/client.py` | Read paths, and non-grid operations |

## 6. Constraints

- **Runtime dependencies are exactly `hid` and `protobuf`.** The wheel installs with no compiler, no protoc, no build step.
- **The protobuf runtime pin is coupled to the committed gencode.** The runtime validates `runtime >= gencode` at import time; a mismatch is a hard `ImportError` for every user. Currently gencode 7.35.1, pinned `>=7.35.1,<8` (see ADR-0001).
- **Python >= 3.11.**
- **The test suite runs fully offline.** No test imports `hid`, touches hardware, or needs `DYLD_LIBRARY_PATH`; CI runs the real suite on plain runners for every PR (see ADR-0002).
- **Wire baseline: CorOS / Cortex Control 4.0.1, firmware d14e.** The protocol is unversioned, so no behavior is guaranteed across firmware updates; [`architecture.md`](architecture.md) has the re-verification checklist.
- **Exclusive device access.** Cortex Control holds the HID interface exclusively, so the library and Cortex Control cannot be connected at the same time.

## 7. Decision Records

Decisions for this area are recorded in [`ADR.md`](ADR.md):

| ADR | Title |
|---|---|
| ADR-0001 | Commit the generated protobuf bindings, with the runtime pin coupled to them |
| ADR-0002 | Fully offline test suite behind a single lazy `hid` import |
| ADR-0003 | USB HID is the only transport |
| ADR-0004 | The domain model lands additively on top of the unchanged protocol layer |

## 8. Open Questions

None yet. Protocol unknowns (the splitter write path, the IR import payload format, and the rest) are investigation gaps tracked in [`roadmap.md`](roadmap.md) and [`architecture.md`](architecture.md), not deferred decisions.

## 9. Pointers

- Repo: <https://github.com/stokes-audio/pyquadcortex> · PyPI: <https://pypi.org/project/pyquadcortex/>
- Deep references: [`architecture.md`](architecture.md) (code), [`protocol.md`](protocol.md) (wire), [`capture.md`](capture.md) (observing device traffic)
- Status: [`manual-coverage.md`](manual-coverage.md) (feature audit), [`roadmap.md`](roadmap.md) (direction), [`../changelog.md`](../changelog.md)
- Operations: [`releasing.md`](releasing.md), [`troubleshooting.md`](troubleshooting.md), [`api.md`](api.md)
- Device reference: the [Quad Cortex manual](https://neuraldsp.com/manual/quad-cortex)

## 10. Infrastructure Dependencies

### Reused infrastructure

- **PyPI** for releases (process and credentials handling in [`releasing.md`](releasing.md))
- **GitHub Actions** for the offline suite on every PR (`.github/workflows/ci.yml`)
- **hidapi** as the OS-level native library on any machine that talks to hardware
- **One physical Quad Cortex** (CorOS 4.0.1 / d14e) - the scarce resource; hardware verification is manual and serialized on it

### Workload characteristics

Single-device, single-connection USB HID at interactive rates (129-byte reports); no server components, no persistent storage, no capacity planning. The binding constraint is hardware access, not compute.

## What goes elsewhere (not here)

| If you're writing...           | Put it in...                          |
|---                             |---                                    |
| Imperatives ("always use X")   | repo-root `CLAUDE.md`                 |
| Decision rationale             | [`ADR.md`](ADR.md)                    |
| Per-Epic plan                  | the Epic's implementation plan        |
| Architecture / current state   | here, or [`architecture.md`](architecture.md) for code-level depth |
