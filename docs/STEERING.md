# Steering: pyquadcortex

> **What this is:** durable technical context for the pyquadcortex library - what the system is and why it is shaped this way.
> **What this is not:** coding rules (see the repo-root `CLAUDE.md`) or decision rationale (see [`ADR.md`](ADR.md)).
> **Last reviewed:** 2026-08-14 by Stokes
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

The protocol layer is stateless between calls: every read is a live exchange, and the unit is the source of truth. It does carry one hook for a caller who wants to be told rather than to ask - `Transport.add_listener`, a subscription that sees every message the unit pushes for the life of the connection (ADR-0009) - but the transport stores none of it.

The model layer holds the state (design in [`domain-model.md`](domain-model.md) sections 9 and 10, decided in ADR-0011). `pyquadcortex/device/state.py` is a write-through cache above `protocol/client.py`, fed by one persistent listener registered before the connect handshake so it hears the handshake's burst. It applies what the unit pushes as data rather than as an invalidation signal, asks the unit directly for what the unit never announces, and stops trusting a part of its copy when a message names a field the model does not keep. Reads happen on the caller's thread; the RX thread only ever merges and marks. What is tracked is a registry in `device/entries.py` rather than code, and it currently holds two of section 9's rows - the unit's identity and the unsaved-changes flag. The rest arrive with the surfaces that read them, so callers still hold whatever state the model does not yet cover.

## 4. Owned Paths

- `pyquadcortex/` - the package. Two namespaces: `pyquadcortex/device/` (the model of the unit) and `pyquadcortex/protocol/` (the message-level API, including the committed generated bindings in `pyquadcortex/protocol/proto/`)
- `protocol/` - the recovered `.proto` schema and its tooling
- `tests/` - the fully offline suite and its fixtures
- `examples/` - runnable scripts, also used as hardware-verification shapes
- `docs/` - protocol record, architecture, coverage, this file
- `scripts/` - `compile_protos.sh`, `check_artifacts.py`, `generate_models.py`
- `.github/workflows/` - CI

## 5. Patterns in Use

| Pattern | What | Why (or `see ADR-000N`) | Canonical example | When the pattern does not apply |
|---------|------|-------------------------|-------------------|----------------------------------|
| Layered message flow | New operations are a registry entry plus a thin `QuadCortex` method that builds a protobuf and picks `send`/`request`/`await_broadcast` | Wire concerns stay below `client.py`, which keeps the whole API testable with a fake transport (see ADR-0002) | `QuadCortex.switch_scene` in `pyquadcortex/protocol/client.py` | `cli.py`'s `version` subcommand deliberately bypasses the connect handshake (`_open_unconnected`) |
| Fake-per-layer offline tests | Each layer has a purpose-built double: golden captured frames for `framing`, `FakeHid` for `transport`, `FakeTransport` for `client` | see ADR-0002 | `FakeTransport` in `tests/test_client.py` | Hardware verification happens manually via `examples/`, outside the suite |
| Evidence-bearing docstrings | Each operation's docstring states what is confirmed on hardware vs inferred from the schema | The device gives no errors for wrong writes, so recorded evidence is the only trail | `QuadCortex.read_preset` in `pyquadcortex/protocol/client.py` | Non-protocol helpers (pure functions) carry ordinary docstrings |
| Keyed grid edits | Mutations are row/column-keyed `Grid` UPDATEs | The device applies grid updates by key; wholesale preset writes are silently ignored (see [`architecture.md`](architecture.md), "write_preset is a trap") | `QuadCortex.set_bypass` in `pyquadcortex/protocol/client.py` | Read paths, and non-grid operations |
| One translation boundary | Screen values become wire values in exactly one PACKAGE, and a source-reading test proves no other module in the package does it - the whole package outside `protocol/`, not just `device/`. The exemption covers a directory, so a test names the package's modules and a new one has to come through that list | An off-by-one row is silent - the write lands on a real row and reads back perfectly - so a convention cannot be trusted to hold (design principle 5 in [`domain-model.md`](domain-model.md)) | `pyquadcortex/device/translate/` | The protocol layer, which keeps its zero-based indexes and raw scales |
| Model state goes through the cache | A model property reads `Device.state.value(entry, field)`; what it tracks is a `StateEntry` in `device/entries.py`, not an attribute the property fills in itself | One account of what the model believes and how it learned it. A property with its own cached attribute answers from a copy nothing invalidates, and a closed connection cannot take it away (see ADR-0011) | `Device.firmware` in `pyquadcortex/device/device.py` | Values derived from an entry rather than read from the unit, which compute from `value()` rather than caching alongside it |

## 6. Constraints

- **Runtime dependencies are exactly `hid` and `protobuf`.** The wheel installs with no compiler, no protoc, no build step.
- **The protobuf runtime pin is coupled to the committed gencode, and so is the generator floor.** The runtime validates `runtime >= gencode` at import time; a mismatch is a hard `ImportError` for every user. Currently gencode 7.35.1, pinned `>=7.35.1,<8` (see ADR-0001). The generator is `grpcio-tools`, which carries its own protoc and so decides the gencode by which version is installed, hence the `grpcio-tools>=1.83.0` floor in the dev extra. Older gencode still imports, so both guards are explicit: `scripts/compile_protos.sh` refuses to write a downgrade, and `tests/test_packaging.py` proves the committed gencode and the pin floor are the same number (see ADR-0008).
- **Python >= 3.11.**
- **The default test suite runs fully offline.** No test imports `hid`, touches hardware, or needs `DYLD_LIBRARY_PATH`; CI runs the real suite on plain runners for every PR (see ADR-0002). A separate hardware-in-the-loop suite - state-neutral on success, best-effort restore on failure, never run in CI - lives in `tests/hardware/` and runs only under `pytest --hardware` (see ADR-0005). Its modules must stay import-safe offline: `tests/test_scene_echo_predicates.py` imports `tests/hardware/test_write_echo.py` to exercise its predicates with no unit attached, which is the only way a predicate that can never match gets caught cheaply.
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
| ADR-0008 | The generator floor joins the bindings/pin unit, with a gate at regeneration and a CI check on the pin |
| ADR-0009 | Persistent listeners run on the RX thread, which may not read from the device |
| ADR-0010 | A control with no known wire path gets a bounded search before it is modelled as refused |
| ADR-0011 | A push merges, a read replaces, and anything the cache cannot place forces one read |

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

### 2026-08-14 - The model keeps its own copy of what the unit is doing (ADR-0011)

**What changed:**
- `pyquadcortex/device/state.py`: the write-through cache every model read now goes
  through. One persistent listener (ADR-0009), registered before the connect handshake
  so it hears the burst, applies what the unit pushes into a per-entry copy. Reads
  happen on the caller's thread; the RX thread only merges and marks.
- `pyquadcortex/device/entries.py`: what is tracked, as data rather than code - the
  message types that carry each entry, the fields the model keeps from each, and the
  read that fetches it. Two of `domain-model.md` section 9's rows so far.
- `pyquadcortex/device/watch.py`: the write side - a watcher per write with section
  10's three outcomes, and one watchdog thread per connection that does not start
  until something is written.
- `pyquadcortex/device/device.py`: `Device.firmware` and `.serial` read through the
  cache instead of holding their own reply; `Device.state` exposes the layer;
  `connect()` subscribes before the handshake and `close()` unsubscribes.
- `tests/hardware/conftest.py`: the run's connection also carries a `DeviceState`
  subscribed before the handshake, plus a snapshot of what the burst warmed, taken
  before any test can read through it.

**Why:** the model has to be right about a change somebody made on the touchscreen
while a script was connected, and no property may ship with a "might be stale"
caveat. Story OM-M1.3 (#11), Epic #8.

**What this constrains going forward:**
- A model property reads through `Device.state`, and what it reads is a `StateEntry`.
  A property that caches its own answer is a second account of the same fact, with
  nothing to invalidate it and nothing to take it away when the connection closes.
- An entry with no read is not an entry. Section 9's table is longer than the
  registry on purpose; each row lands with the surface that reads it.
- The RX thread's rule is now load-bearing in the model as well as the transport:
  push-handling code merges and marks and returns, and never reads.
- A field with no wire presence needs recorded evidence before the model keeps it,
  and `tests/test_state.py` holds every such declaration against the schema.

**Not covered here:** reconnect and device loss (#15), the Directory, presets, the
grid and parameters (#12 and after), and the counters and event taxonomy (#16). The
log events this code emits are named for #16 to pick up, but nothing reads them yet.

### 2026-08-15 - ADR-0012: a grid push is re-read, not merged; the model publishes what it noticed

**What changed:**
- ADR.md: added ADR-0012 (a `Grid` or `SceneLabel` push voids the entry's copy and the next read fetches the whole live preset; `device.events` carries `Changed` and `Invalidated` on a thread the model owns)
- domain-model.md: §9 gains the by-type rule, the no-merge decision with what merging would take written beside it, the complete-push rule, and the event surface; §2 and §3 record what is built and the four things deliberately omitted; the §9 table is corrected against hardware
- CLAUDE.md: the translation boundary is a package with a named module list and a second allowlist for non-conversions; `Grid`'s `action` decision is recorded; submessages are cached by copy
- STEERING.md: the "One translation boundary" pattern row now says package
- protocol.md: `read_current_preset_push` and `loaded_position` added to the coverage table, with the measured shape of a recall and of the connect burst

**Why:**
- A hardware session contradicted three assumptions this work had been built on - the burst's seed preset push carries `reason`, a recall pushes no `PresetDirty`, and `SetlistPosition{READ}` really does answer - and the corrections belong where the next person meets them rather than in a commit message

**Scope of impact:**
- **Updated:** ADR.md, domain-model.md, CLAUDE.md, STEERING.md, protocol.md
- **Not updated (intentionally):** architecture.md - the layer map is unchanged, the model still sits on the protocol layer exactly as before; api.md - it documents the protocol layer, whose two additions are in the coverage table, and the model's surface is documented in domain-model.md

**Downstream to consider:**
- The Directory half of issue #12 needs `StateEntry` to carry how many messages a read expects, since a setlist listing answers with several hundred
- Whether merging grid deltas is worth doing is now a recorded question rather than an omission; #13's parameter work is the first thing that would feel the cost

### 2026-08-13 - One translation boundary, and the model package is `device/`

**What changed:**
- `pyquadcortex/device/translate.py`: the one module where a screen value becomes a wire
  value and back - rows 1-4, slots 1-8, scene and footswitch letters, preset addresses,
  and five display-unit mappings (input gain dB, lane and mixer dB, tempo bpm, tuner
  reference Hz, hold timing ms). The two level scales and the tempo call the protocol
  helper that carries the measurement; the other two have no helper to call, so they are
  pinned against what the protocol write method expects, and the tuner's docstring says
  how thin its evidence is - one observed pair. `PresetAddress`, `FootswitchLetter` and
  `SceneLetter` are its public value types, re-exported from `pyquadcortex`
- Section 5 gained the pattern row; section 4's owned-paths line and CLAUDE.md name the
  new rule. `architecture.md` carries the module in its layer map and a section on it;
  `domain-model.md` marks principle 5, `PresetAddress` and `FootswitchLetter` as built
- **The model package directory is `pyquadcortex/device/`, renamed from `model/`.** Done
  as its own commit so the story's diff stays readable

**Why:**
- M1 Epic (stokes-audio/pyquadcortex#8), Story #10. It ships before the surfaces that use
  it so no later story invents its own conversion. The Intent Brief names off-by-one as a
  silent failure mode, and the protocol layer's own header agrees: an edit to the wrong
  row still succeeds and still reads back correctly, so nothing tells you. A centralized,
  exhaustively tested boundary is the whole mitigation, which is why two of its tests read
  the model package's source instead of calling it
- The rename is an owner decision. In this codebase the identifier `model` means an amp or
  pedal block - `protocol/models.py`, `catalog.Model`, `ModelCatalog`,
  `set_block(model=...)`. `domain-model.md` §5 renamed that concept to *virtual device* in
  the model's vocabulary, because that is what the screen calls it, but the protocol layer
  still spells it `model` and will keep doing so. A directory named `model/` therefore
  collides with real code a reader is looking at, whatever the design doc calls the
  concept

**Scope of impact:**
- **Updated:** STEERING.md, CLAUDE.md, architecture.md, domain-model.md, roadmap.md,
  changelog.md, `pyquadcortex/device/`, `pyquadcortex/__init__.py`,
  `scripts/check_artifacts.py`, `tests/test_translation.py` (new),
  `tests/test_namespace.py`, `tests/test_import_cleanliness.py`, `tests/test_docs.py`
- **Not updated (intentionally):** ADR.md - neither change reverses or refines a recorded
  decision. The boundary IS design principle 5, already written and reviewed in
  `domain-model.md`; the rename is a directory name, chosen to stop colliding with an
  identifier the protocol layer uses. README.md and api.md - the new value types have
  no surface handing them out yet (the Directory is story #12), and the readme tour should
  show what a caller can do, not what exists. The protocol layer - it keeps its zero-based
  indexes, its `Footswitch` enum and its measured scales, and nothing below the seam
  changed
- **No deprecation shim for the rename.** `pyquadcortex.__all__` lists `protocol` and never
  listed `model`, and the model namespace has not been released - 0.40.0 predates the flip
  - so nothing published points at the old path

**Downstream to consider:**
- Stories #11 through #16 convert through this module rather than doing their own
  arithmetic, and the source-reading tests will fail them if they do not
- The conversions M1 does not need yet land here too, with the surface that needs them.
  A parameter whose display mapping is unverified stays out of the model entirely
  (principle 3), so no mapping is ever invented in this module
- The arithmetic check is deliberately blunt and deliberately wide: a literal one in any
  spelling (`1`, `1.0`, `True`, `-1`), `ord`/`chr`, the literal 65, a letter table as a
  string, tuple, list or dict, `string.ascii_uppercase`, `divmod`, a one-based
  `enumerate`, and `.index()` on `ROWS` or `SLOTS` all fail it, anywhere in the package
  outside the boundary and the protocol layer. If a future module has a genuine counter,
  narrowing the check is a deliberate edit with a reason, not a quiet one. What it cannot
  see - a one behind a name, a table built at run time, arithmetic inside somebody else's
  helper - is pinned as a failing-if-it-changes list in the same file, because a sample
  table where every case passes reads like a completeness proof and is not one
- The scan is scoped to the whole package rather than to `pyquadcortex/device/`, because
  a rule scoped to a directory is satisfiable by moving the code one directory up - which
  is precisely what a failure message naming a directory invites
- A protocol conversion can be delegated to and still not be movable. `bpm_to_tempo`
  (PR #22) is called by `QuadCortex.set_tempo_param` from inside the protocol layer, so
  the helper stays there and the boundary wraps it, the same way it wraps the level
  scales. Adding the name to the boundary's allowlist is the half that matters: without
  it, a model module reaching for `protocol.tempo_bpm` passes the check
- The allowlist is judgement, and its criterion is what a name HANDS OVER rather than
  whether it reads like a conversion. `protocol.stomp_assignments` returns three raw wire
  indexes including the footswitch one, so a model module could key a mapping by it and
  reintroduce the exact bug `FootswitchLetter` exists to prevent, without writing a `- 1`
  anywhere. The readers are therefore listed next to the converters

**Also in this branch:**
- Merged main (PRs #19, #20, #22) up. The three change logs conflicted in the same place
  and both sides were kept in the order they landed
- The review found the boundary's own front door open: `translate.slot_to_position`
  accepted non-ASCII digits, so `"٢٨C"` returned preset 218. Only `PresetAddress.parse`
  carried the ASCII pattern, while a comment and a test both read as though the module
  was covered. The two doors now share one pattern, and one list of malformed names is
  run through both
- Both source-reading checks were narrower than they read, again. The arithmetic check
  now sees a letter table in a tuple, list or dict, `string.ascii_uppercase`, the literal
  65, and `ROWS.index(row)` - a coordinate conversion written with the boundary's own
  exported table and no arithmetic in it at all. The allowlist gained the protocol
  readers that hand back wire coordinates. A file doing all of that at once passed both
  checks before and fails both now
- The backstop that proves the boundary still converts is anchored to the four converters
  by name. It had been satisfied by an error-message formatter elsewhere in the file, so
  the converters could have gone arithmetic-free with nothing failing
- Each check now pins its KNOWN blind spots as blind spots. A sample table where every
  "should be caught" case is caught reads like a completeness proof; these fail if a
  listed gap ever closes, which is the edit where the prose gets corrected too
- The layering check could not see `from pyquadcortex import PresetAddress`, a hole this
  story opened by re-exporting the value types at top level. It reads `device.__all__`
  now, so it follows the code

### 2026-08-12 - TEMPO MODE closes, and ADR-0010

**What changed:**
- **The Tempo menu's MODE switch is readable and writable.** It is the DEVICE tempo block's parameter 1, carried in `GlobalTempo.params`: `0.0` PRESET, `1.0` GLOBAL. `QuadCortex.tempo_mode()` / `set_tempo_mode()` and the `TempoMode` enum ship at the protocol layer; `docs/protocol.md` gains "MODE is the DEVICE tempo block's parameter 1" and a coverage-table row
- ADR.md: ADR-0010 - a control with no known wire path gets a differential state capture before it is recorded as having none. ADR-0007's rule is unchanged and now has no instance, which is the healthy state for it
- `docs/domain-model.md`: `Tempo.mode` stops being refused and becomes an ordinary property; §13's *Genuinely open* loses its first entry and the *Closed* table records where the answer lives; both appendix tempo rows updated. `manual-coverage.md` gains a MODE row and its tally moves to 104 / 65 yes
- `docs/capture.md` gains "Diff the whole state, do not hunt for a field" - the method that found it, and the four things in the harness that are load-bearing. Its listener chapter, which used this claim as its exemplar, now carries the ending
- **`TEMPO`'s span fits 40..240 bpm**, from three INTERIOR screen-vs-wire points measured during the same session, exact to the displayed integer at each. The endpoints are the fit's, not driven. `real=` on that parameter now takes bpm, via `tempo_bpm()` / `bpm_to_tempo()`; `protocol.md`'s placeholder-span list now has two of its eight parameters' spans measured and seven covered; splitter `FREQUENCY` is the one still unrecovered
- `tests/hardware/state_snapshot.py` is the harness, reusable for the next control of this kind. It subscribes through `Transport.add_listener` (ADR-0009), which landed in the same release and is exactly the hook it needs - the first version predated it and monkey-patched `_dispatch`; `tests/test_state_snapshot.py` proves offline that it can see an unknown field number, a presence-tracked zero, and a value in only one of two message shapes

**Why:**
- The wire path was a named dependency of Epic #8 and a prerequisite of M3's device-settings work. Three earlier tests had established that the unit never BROADCASTS the switch, which had been over-read as "not on the wire"; a READ found it in one session
- The method is the durable part. Earlier attempts hunted for the field they expected, in the messages they expected; MODE was one index away inside a message shape the investigation had already written off. Diffing the whole answerable state finds a thing without knowing where to look

### 2026-08-12 - A persistent broadcast subscription at the protocol layer, and ADR-0009

**What changed:**
- `Transport.add_listener` / `remove_listener`: a subscription that sees every decoded inbound message for the life of the connection, including the unsolicited pushes `_dispatch` used to drop for want of a waiter. `QuadCortex` passes both through so the layer above never reaches into `_t`
- The transport now refuses `request`, `await_broadcast` and `collect` when they are called from the RX thread. That is what makes "a listener never reads from the device" enforced rather than requested
- `protocol.connect(before_handshake=...)` calls back with the started transport before the handshake runs, which is the only moment early enough to hear the handshake's own state burst
- ADR.md: ADR-0009 - listeners run on the RX thread, and the RX thread may not read; the queue-and-delivery-thread alternative and the document-but-do-not-enforce alternative are recorded with why each was rejected
- Section 3's "Data and state" names the one hook that is not a live exchange; section 7's table gained the ADR-0009 row
- `docs/protocol.md` "Connect burst, measured" gained the fact that decided the hook: `connect()` returns at 2.0 s, the ModelRepo lands at 4.9 s and the seed preset at 10.1 s, so a listener attached to the returned client has missed the burst it wanted
- `tests/hardware/` gained `test_broadcast_listener.py`, and the suite's connection fixture now records the burst - it cannot be attached on demand later, because the burst happens during `connect()`

**Why:**
- M1 Epic (stokes-audio/pyquadcortex#8), Story #11. This is the protocol-layer half of that story, carved out because it is independent of the model work: `docs/domain-model.md` section 9 needs a push-fed cache, and a cache cannot be fed by three hooks that are all one-shot and scoped to a trigger

**Scope of impact:**
- **Updated:** `pyquadcortex/protocol/transport.py`, `client.py`, `session.py`, `tests/test_transport.py`, `tests/test_client.py`, `tests/test_session.py`, `tests/test_handshake_burst_recorder.py` (new), `tests/hardware/conftest.py`, `tests/hardware/test_broadcast_listener.py` (new), `tests/hardware/readme.md`, ADR.md, CLAUDE.md, STEERING.md, architecture.md, api.md, protocol.md, changelog.md
- **Not updated (intentionally):** ADR-0002 - the offline suite still imports no `hid` and the new tests run against `FakeHid` like the rest; ADR-0005 - the new hardware tests only listen, so they write nothing and have nothing to restore, which meets the contract rather than changing it; `docs/domain-model.md` - section 9 designed this and needed no correction; the coverage table in `protocol.md` - no new message type is involved

**Also in this branch:**
- Merged main (PR #19) in. That change took ADR-0008 for the generator floor, so the listener record is ADR-0009; the two commit messages on this branch predate the renumber and still say 0008

**Downstream to consider:**
- The model-side cache (the other half of #11) is the intended consumer and is being written separately. It registers through `before_handshake` so the burst warms it for free
- `tests/hardware/test_write_echo.py` still taps `Transport._dispatch` by monkeypatching it, which predates this and could now be an ordinary listener. Left alone deliberately: it is a working measurement harness, and `tests/test_scene_echo_predicates.py` imports it offline
- ADR-0009 leaves one question open on purpose - whether a listener hears about device loss. It stops receiving today, and the answer belongs with reconnect (#15)

### 2026-08-12 - The generator floor joins the bindings/pin unit (ADR-0008)

**What changed:**
- The dev extra's `grpcio-tools` floor went from `>=1.68` to `>=1.83.0`, with the reason written next to it. `grpcio-tools` ships its own protoc, so the installed version decides the gencode stamped into the committed bindings. The old floor let `pip install -e ".[dev]"` resolve to 1.82.1, which emits gencode 7.35.0 against bindings committed at 7.35.1; the script's system-`protoc` fallback has no floor at all
- `scripts/compile_protos.sh` now generates into a temporary directory, compares the gencode it produced against the committed one, and refuses to install a downgrade. On refusal the tree is untouched
- `tests/test_packaging.py` proves the committed state on every PR: all bindings from one generator, the pin floor equal to the committed gencode, the pin's ceiling one major above it
- ADR.md: ADR-0008. Section 6's pin constraint says the floor is part of the same unit, and section 4 lists the two scripts added since it was last written

**Why:**
- Found while working the PR #17 review. ADR-0001 makes the bindings and the pin one unit, but nothing enforced it: protobuf validates `runtime >= gencode` and nothing else, so bindings regenerated by an older generator import cleanly and pass the whole suite while walking the pin backwards
- The floor is not derivable from package metadata. `grpcio-tools` 1.82.1 declares `protobuf>=7.35.1` and still emits gencode 7.35.0, so 1.83.0 was found by running each candidate and reading the stamp it writes

**Scope of impact:**
- **Updated:** `pyproject.toml`, `scripts/compile_protos.sh`, `tests/test_packaging.py`, ADR.md, STEERING.md, CLAUDE.md, architecture.md, contributing.md, changelog.md
- **Not updated (intentionally):** the bindings themselves - regenerating is its own change with its own pin bump (ADR-0001), and this one deliberately leaves the generated files byte-identical

**Downstream to consider:**
- The floor now moves with every gencode bump. `compile_protos.sh` prints the number to put in the pin when the gencode moves up, but the `grpcio-tools` floor is the maintainer's to raise

### 2026-08-11 - The namespace flip lands, and ADR-0007

**What changed:**
- The package now has two namespaces: `pyquadcortex` is the model, `pyquadcortex.protocol` is today's protocol layer moved verbatim. Sections 3 and 4 describe both; the patterns table's file paths moved with the code
- ADR.md: ADR-0007 - the model may represent a control whose wire path is still open, provided the operation it cannot perform refuses rather than guesses
- `docs/domain-model.md`: TEMPO MODE reopened. `Tempo` gains `mode`; the appendix row and §13 changed from "not on the wire at all" to an open investigation, and the appendix legend gained *open* as a status so the row's value is defined rather than improvised
- One account of the TEMPO MODE evidence, the same in every document: **three** tests, not two; the strong instrument (70 of 72 message types, 420-second window, liveness heartbeat) is **the second**, and the third is the 2026-08-06 device-wide sweep, whose script toggles MODE without a written OK step; the over-strong claim stood for **eight** releases, 0.33.0 through 0.40.0. `changelog.md` carries the withdrawal under Unreleased, which it had been missing
- `docs/protocol.md` no longer files `GlobalTempo` as a dead end. One READ of it returned a clock; the same document records that it alternates two shapes and that the other one carries the 25 params, so it is the first place to ask, not a closed door
- The same correction landed in the protocol record itself, which is where the over-strong claim actually lived: `protocol.md` ("Per-preset tempo, LED and metronome"), `manual-coverage.md` (two places), and `capture.md`, whose listener chapter used the claim as its exemplar and now carries the second lesson too - a listener proves only that the device does not ANNOUNCE something
- Section 6's offline-suite constraint: the hardware suite is built, not merely decided - it shipped in 0.39.0 and this line had not caught up

**Why:**
- M1 Epic (stokes-audio/pyquadcortex#8), Story #9. The flip goes first because every other story in the Epic imports through the new layout (ADR-0006)
- ADR-0007 is an owner decision taken during the same story: "three tests saw no broadcast" had been over-read as "not on the wire", and a control we understand but cannot drive should refuse rather than be omitted or guessed at

**Also closed in review, on the same branch:**
- `Device` now checks field PRESENCE before reporting firmware or serial, and caches only a complete reply. Both fields sit in synthetic `oneof`s, so an absent one decodes as `""` and would have been reported as the unit's answer - the guess ADR-0007 forbids, in shipped code. CLAUDE.md carries the rule
- A closed `Device` refuses `firmware`, `serial` and `client` instead of answering from cache. `_closed` had been read by nothing but `__repr__`. This defines only the explicit `close()`; a connection that goes away on its own stays with the reconnect story (#15)
- `__repr__` says whether the `Device` owns or borrows its connection, which decides whether `close()` releases the unit and was otherwise invisible
- Test guards that were weaker than they read: the pre-flip export snapshot is pinned by content hash and exact count (a live `git show` cannot work - CI checks out one commit deep); the parity check asserts each name still resolves to something in the protocol layer rather than merely existing; the layering check reads every import spelling, including `from pyquadcortex import model`, which is the house style and was invisible to it; the import-cleanliness sentinel takes the trailing dot
- `scripts/check_artifacts.py`, run by CI's `build` job: `twine check` reads metadata, so nothing was looking inside the wheel for the generated bindings ADR-0001 exists to ship
- `scripts/compile_protos.sh` refuses an output directory that is not the bindings directory instead of `mkdir -p`-ing a new one, writing into it and reporting success

**Scope of impact:**
- **Updated:** STEERING.md, ADR.md, CLAUDE.md, domain-model.md, architecture.md, api.md, README.md, capture.md, protocol.md, manual-coverage.md, releasing.md, contributing.md, changelog.md, `.github/workflows/ci.yml`, `scripts/`
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
