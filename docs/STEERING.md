# Steering: pyquadcortex

> **What this is:** durable technical context for the pyquadcortex library - what the system is and why it is shaped this way.
> **What this is not:** coding rules (see the repo-root `CLAUDE.md`) or decision rationale (see [`ADR.md`](ADR.md)).
> **Last reviewed:** 2026-08-03 by Stokes
> **Owners:** Stokes

## 1. Purpose

pyquadcortex is an unofficial Python library for controlling the Neural DSP Quad Cortex over USB HID, speaking the device's own protobuf control protocol. Explicit context matters here because nearly every fact in this codebase is empirical: the schema was recovered from Cortex Control, behavior was verified by observing and driving real hardware, and the device accepts-and-ignores writes it does not understand rather than rejecting them. The written record (docstrings, [`protocol.md`](protocol.md), the coverage table) is the only trail of what is actually known, so work that ignores it can look correct while doing the wrong thing on the unit.

## 2. Scope and Boundaries

### In scope

The whole repository: the `pyquadcortex/` package (including the committed generated bindings in `pyquadcortex/protocol/proto/`), the recovered schema in `protocol/`, tests, examples, docs, and tooling.

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

Two public namespaces in one package (see ADR-0006). `pyquadcortex` is the model of the unit - what `import pyquadcortex` hands back - and `pyquadcortex.protocol` is the message-level API it is built on. The model calls the protocol layer and never the reverse.

Inside the protocol layer, a strict one-concern-per-file layering: `cli` → `session` → `client` → `transport` → `registry`/`framing` → hidapi, where each layer knows only the layer directly below it. `QuadCortex` (`protocol/client.py`) is the message-level API and knows nothing about HID reports, framing, or bytes; everything time-dependent and concurrent lives in `protocol/transport.py`. The full layer map, message flow, and the recipe for adding an operation are in [`architecture.md`](architecture.md) - that document is the deep reference and is not duplicated here.

### Data and state

The protocol layer is stateless between calls: every read is a live exchange, and the unit is the source of truth. The model layer (design in [`domain-model.md`](domain-model.md)) introduces a broadcast-fed write-through cache above `protocol/client.py`; at the time of writing the model is a skeleton and that cache is not built, so callers still hold whatever state they need.

## 4. Owned Paths

- `pyquadcortex/` - the package. Two namespaces: `pyquadcortex/model/` (the model of the unit) and `pyquadcortex/protocol/` (the message-level API, including the committed generated bindings in `pyquadcortex/protocol/proto/`)
- `protocol/` - the recovered `.proto` schema and its tooling
- `tests/` - the fully offline suite and its fixtures
- `examples/` - runnable scripts, also used as hardware-verification shapes
- `docs/` - protocol record, architecture, coverage, this file
- `scripts/` - `compile_protos.sh`
- `.github/workflows/` - CI

## 5. Patterns in Use

| Pattern | What | Why (or `see ADR-000N`) | Canonical example | When the pattern does not apply |
|---------|------|-------------------------|-------------------|----------------------------------|
| Layered message flow | New operations are a registry entry plus a thin `QuadCortex` method that builds a protobuf and picks `send`/`request`/`await_broadcast` | Wire concerns stay below `client.py`, which keeps the whole API testable with a fake transport (see ADR-0002) | `QuadCortex.switch_scene` in `pyquadcortex/protocol/client.py` | `cli.py`'s `version` subcommand deliberately bypasses the connect handshake (`_open_unconnected`) |
| Fake-per-layer offline tests | Each layer has a purpose-built double: golden captured frames for `framing`, `FakeHid` for `transport`, `FakeTransport` for `client` | see ADR-0002 | `FakeTransport` in `tests/test_client.py` | Hardware verification happens manually via `examples/`, outside the suite |
| Evidence-bearing docstrings | Each operation's docstring states what is confirmed on hardware vs inferred from the schema | The device gives no errors for wrong writes, so recorded evidence is the only trail | `QuadCortex.read_preset` in `pyquadcortex/protocol/client.py` | Non-protocol helpers (pure functions) carry ordinary docstrings |
| Keyed grid edits | Mutations are row/column-keyed `Grid` UPDATEs | The device applies grid updates by key; wholesale preset writes are silently ignored (see [`architecture.md`](architecture.md), "write_preset is a trap") | `QuadCortex.set_bypass` in `pyquadcortex/protocol/client.py` | Read paths, and non-grid operations |

## 6. Constraints

- **Runtime dependencies are exactly `hid` and `protobuf`.** The wheel installs with no compiler, no protoc, no build step.
- **The protobuf runtime pin is coupled to the committed gencode.** The runtime validates `runtime >= gencode` at import time; a mismatch is a hard `ImportError` for every user. Currently gencode 7.35.1, pinned `>=7.35.1,<8` (see ADR-0001).
- **Python >= 3.11.**
- **The default test suite runs fully offline.** No test imports `hid`, touches hardware, or needs `DYLD_LIBRARY_PATH`; CI runs the real suite on plain runners for every PR (see ADR-0002). A separate hardware-in-the-loop suite - state-neutral on success, best-effort restore on failure, never run in CI - lives in `tests/hardware/` and runs with `pytest tests/hardware --hardware` (see ADR-0005).
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
| ADR-0005 | A hardware-in-the-loop integration suite, state-neutral on success |
| ADR-0006 | The domain model takes the top-level namespace; the protocol layer moves to `pyquadcortex.protocol` |
| ADR-0007 | The model may represent a control whose wire path is still open |

## 8. Open Questions

None yet. Protocol unknowns (the splitter write path, the IR import payload format, and the rest) are investigation gaps tracked in [`roadmap.md`](roadmap.md) and [`architecture.md`](architecture.md), not deferred decisions.

## 9. Pointers

- Repo: <https://github.com/stokes-audio/pyquadcortex> · PyPI: <https://pypi.org/project/pyquadcortex/>
- Deep references: [`architecture.md`](architecture.md) (code), [`protocol.md`](protocol.md) (wire), [`capture.md`](capture.md) (observing device traffic), [`domain-model.md`](domain-model.md) (the object model design)
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

---

## Change Log

### 2026-08-11 - The namespace flip lands, and ADR-0007

**What changed:**
- The package now has two namespaces: `pyquadcortex` is the model, `pyquadcortex.protocol` is today's protocol layer moved verbatim. Sections 3 and 4 describe both; the patterns table's file paths moved with the code
- ADR.md: ADR-0007 - the model may represent a control whose wire path is still open, provided the operation it cannot perform refuses rather than guesses
- `docs/domain-model.md`: TEMPO MODE reopened. `Tempo` gains `mode`; the appendix row and §13 changed from "not on the wire at all" to an open investigation
- The same correction landed in the protocol record itself, which is where the over-strong claim actually lived: `protocol.md` ("Per-preset tempo, LED and metronome"), `manual-coverage.md` (two places), and `capture.md`, whose listener chapter used the claim as its exemplar and now carries the second lesson too - a listener proves only that the device does not ANNOUNCE something
- Section 6's offline-suite constraint: the hardware suite is built, not merely decided - it shipped in 0.39.0 and this line had not caught up

**Why:**
- M1 Epic (stokes-audio/pyquadcortex#8), Story #9. The flip goes first because every other story in the Epic imports through the new layout (ADR-0006)
- ADR-0007 is an owner decision taken during the same story: "three tests saw no broadcast" had been over-read as "not on the wire", and a control we understand but cannot drive should refuse rather than be omitted or guessed at

**Scope of impact:**
- **Updated:** STEERING.md, ADR.md, CLAUDE.md, domain-model.md, architecture.md, api.md, README.md, capture.md, protocol.md, manual-coverage.md, releasing.md, contributing.md, changelog.md
- **Not updated (intentionally):** ADR-0001 - it is `Decided` and append-only, so its `pyquadcortex/proto/` paths stay as written; the directory it names moved under ADR-0006 and now lives at `pyquadcortex/protocol/proto/`. The decision itself is unchanged. ADR-0004 and ADR-0006 - the flip is what ADR-0006 already decided, not a new decision. `roadmap.md`'s illustrative model snippet - it reads `pyquadcortex.connect()`, which is now exactly right

**Downstream to consider:**
- The version moved to `pyquadcortex/_version.py` so both namespaces can publish it without one importing the other; `pyproject.toml` and `releasing.md` follow it
- `qcctl` is declared as `pyquadcortex.protocol.cli:main`. The command is unchanged, but an editable install from before the flip needs reinstalling before the console script resolves
- No release is cut here. Per ADR-0006 the version is cut once the M1 anchor works, so no release ever has `connect()` meaning two different things
- Finding the TEMPO MODE wire path is now a prerequisite of M3's device-settings Epic

### 2026-08-06 - Domain model Part II: state tracking and save behavior

**What changed:**
- `docs/domain-model.md`: Part II replaces its stub - how the model keeps its cached facts current (§9), write verification via the unit's own echo (§10), the save lifecycle (§11), and disconnect/standby/reconnect (§12), with the breadth the hardware session did not reach named explicitly in §13
- Part I forward references resolved in the same pass: `DeviceLostError` replaces the placeholder `NotConnectedError` in §8, writes to an inactive scene are refused, and the appendix's *Part II* rows now point at §13 or the section that answers them

**Why:**
- M0 Epic (stokes-audio/pyquadcortex#2), Story #4. Both of the Epic's empirical questions were answered on hardware (`d14e` / CorOS 4.0.1) rather than carried as M1 risks: unsaved-change detection is readable and pushed, and device loss is detectable for free because a HID read raising means the device is gone while a write raising means nothing

**Scope of impact:**
- **Updated:** domain-model.md, STEERING.md
- **Not updated (intentionally):** ADR.md - the behavioral decisions (optimistic writes confirmed by echo, abandon-on-switch matching the unit, transparent reconnect) are design choices recorded in the doc, not reversals of a prior decision; CLAUDE.md - still no model code, so no new imperatives; protocol.md and the coverage table - the session's protocol-layer findings were handed to the protocol work and shipped there, not duplicated here

**Downstream to consider:**
- §13's open items are the natural first jobs for ADR-0005's hardware suite rather than more one-off scripts; the write-echo check that produced §10's latencies already snapshots, writes, verifies, and restores
- The design assumes the protocol layer's `DeviceLostError`, `preset_dirty()`, `RecallReason` and `handshake_patience`, so M1 depends on those staying public
- Whether host writes are honoured during standby is untested, and a script can talk to a sleeping unit over a healthy connection - worth closing before M1 exposes `power_state`

### 2026-08-05 - Domain model structural design + ADR-0006 (namespace flip at M1)

**What changed:**
- New `docs/domain-model.md`: the M0 structural design of the object model (hierarchy, typing, full manual-feature appendix); its behavioral half lands from the companion design story
- ADR.md: ADR-0006 - at M1 the model takes the top-level namespace and the protocol layer moves to `pyquadcortex.protocol`, refining ADR-0004
- STEERING.md: section 7 table gained the ADR-0006 row; section 9 points at the design doc

**Why:**
- M0 Epic (stokes-audio/pyquadcortex#2), Story #3: the full object model is designed before M1 implementation starts; the namespace flip was an owner decision during design review

**Scope of impact:**
- **Updated:** domain-model.md (new), ADR.md, STEERING.md
- **Not updated (intentionally):** CLAUDE.md - no code exists yet, so no new imperatives; architecture.md - the layer map changes only when M1 lands

**Downstream to consider:**
- The Intent Brief's "Additive, not breaking" requirement and Customer FAQ need the ADR-0006 amendment (planning repo)
- Part II (state/save behavior, Story #4) merges into domain-model.md and must resolve the rows marked *Part II* / *unaudited* in its appendix

### 2026-08-04 - ADR-0005: hardware-in-the-loop integration suite

**What changed:**
- ADR.md: added ADR-0005 (an online integration suite that drives a real unit; state-neutral on success, best-effort restore on failure, never in CI)
- STEERING.md: section 6 constraint reworded from "the test suite" to "the default test suite" and now points at ADR-0005; section 7 table gained the ADR-0005 row

**Why:**
- Owner decision: hardware verification is manual today and should become repeatable, but any automated suite edits the only unit that exists, so the restore contract is the safety condition

**Scope of impact:**
- **Updated:** STEERING.md, ADR.md
- **Not updated (intentionally):** CLAUDE.md - the suite does not exist yet, so there are no commands or rules to state; ADR-0002 - unchanged, the offline guarantee still holds for the default suite

**Downstream to consider:**
- ADR-0005's open questions (invocation mechanism, restorable-state inventory, scratch slots) need answers before the suite is built
- The domain-model Epics (M1+) are natural first consumers - their hardware verification could land as online tests instead of one-off scripts
