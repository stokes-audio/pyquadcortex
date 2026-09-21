# Steering: pyquadcortex

> Purpose: what the pyquadcortex library is and why it is shaped this way.

> Coding rules are in the repo-root `CLAUDE.md`. Decisions and their rationale are in [`ADR.md`](ADR.md).
> **Last reviewed:** 2026-09-16 by Stokes. **Owners:** Stokes.

## 1. Purpose

pyquadcortex is an unofficial Python library that controls the Neural DSP Quad
Cortex over USB HID, speaking the unit's own protobuf protocol. Nearly every fact
in the codebase is empirical. The schema was recovered from Cortex Control, the
behaviour was measured on a real unit, and the unit accepts and ignores a write
it does not understand instead of rejecting it. The written record (docstrings,
[`protocol.md`](protocol.md), the coverage table) is the only trail of what is
known, so work that ignores it can look correct and still do the wrong thing on
the unit.

## 2. Scope and Boundaries

### In scope

The whole repository: the `pyquadcortex/` package (including the committed
generated bindings in `pyquadcortex/protocol/proto/`), the recovered schema in
`protocol/`, tests, examples, docs and tooling.

### Out of scope

- The firmware update path (`Updater`). Permanently, not pending. See
  [`roadmap.md`](roadmap.md).
- The owner's cloud account surfaces (`CloudLogin`, `CloudBackup`, capture
  sharing). Parked. Driving them needs the owner's explicit permission first.

### Integration points

- **The unit.** Wire behaviour is measured per device profile (ADR-0020): a
  `device_type` plus a `zenos_git_hash` (the CorOS version), read from the unit's
  own `Version` reply. One profile is measured by the maintainer: Quad Cortex,
  CorOS 4.0.1, firmware `d14e`. Quad Cortex 4.1.0 has contributed evidence the
  maintainer cannot check. The Quad Cortex Mini (`device_type` `ATMA`) has none.
  The protocol carries no version number, so the profile is the only version
  information there is.
- **hidapi.** The `hid` pip package is a ctypes binding to the hidapi C library,
  which users install themselves (see the README).
- **PyPI.** Published as `pyquadcortex` with the `qcctl` console script. Release
  process in [`releasing.md`](releasing.md).
- **The lab repository.** Planning material, raw USB captures, the catalog dump,
  and the narrative history behind the documents here live in the private
  `quad-cortex` repository. This repository carries the library and its
  engineering documents.

## 3. Architecture Overview

### System shape

Two public namespaces in one package (ADR-0006). `pyquadcortex` is the model of
the unit, which `import pyquadcortex` hands back. `pyquadcortex.protocol` is the
message-level API it is built on. The model calls the protocol layer, never the
reverse.

Inside the protocol layer, one concern per file: `cli` -> `session` -> `client`
-> `transport` -> `registry` and `framing` -> hidapi. Each layer knows only the
layer below it. `QuadCortex` in `protocol/client.py` builds protobuf messages and
knows nothing about HID reports or bytes. Everything time-dependent and
concurrent lives in `protocol/transport.py`. The full layer map and the recipe for
adding an operation are in [`architecture.md`](architecture.md).

### Data and state

The protocol layer is stateless between calls. Every read is a live exchange and
the unit is the source of truth. Its one hook for being told rather than asking
is `Transport.add_listener`, which sees every message the unit pushes for the
life of the connection (ADR-0009). The transport stores none of it.

The model layer holds the state. `pyquadcortex/device/state.py` is a
write-through cache fed by one listener registered before the connect handshake,
so it hears the handshake's burst. A push merges into its copy; a message naming
a field the model does not keep marks the entry for one re-read; the caller's
thread does the reading. What is tracked is a table in `device/entries.py`, and it
holds five entries today: the unit's identity, the unsaved-changes flag, the
loaded preset's position, the preset on the grid, and the active scene. The design
is [`domain-model.md`](domain-model.md) sections 9 and 10, decided in ADR-0011 and
ADR-0012.

## 4. Owned Paths

- `pyquadcortex/` - the package: `pyquadcortex/device/` (the model) and
  `pyquadcortex/protocol/` (the message API and the committed bindings in
  `pyquadcortex/protocol/proto/`)
- `protocol/` - the recovered `.proto` schema
- `tests/` - the offline suite and its fixtures; `tests/hardware/` is the
  hardware suite
- `examples/` - runnable scripts
- `docs/` - the documents listed in [`writing.md`](writing.md)
- `scripts/` - `compile_protos.sh`, `check_artifacts.py`, the three generators
  and `_snapshots.py`, `extract_scale_fixture.py`
- `.github/workflows/` - CI

## 5. Patterns in Use

| Pattern | What | Why | Canonical example | When it does not apply |
|---|---|---|---|---|
| Layered message flow | A new operation is a registry entry plus a thin `QuadCortex` method that builds a protobuf and picks `send`, `request` or `await_broadcast` | Wire concerns stay below `client.py`, so the whole API is testable with a fake transport (ADR-0002) | `QuadCortex.switch_scene` in `pyquadcortex/protocol/client.py` | `cli.py`'s `version` subcommand bypasses the connect handshake on purpose (`_open_unconnected`) |
| A fake per layer | Golden captured frames for `framing`, `FakeHid` for `transport`, `FakeTransport` for `client` | ADR-0002 | `FakeTransport` in `tests/test_client.py` | Hardware verification is `tests/hardware` behind `--hardware` (ADR-0005), run before every pull request is marked ready |
| Evidence in docstrings | Each operation's docstring says what is confirmed on hardware and what is inferred from the schema | The unit gives no error for a wrong write, so the record is the only trail | `QuadCortex.read_preset` in `pyquadcortex/protocol/client.py` | Pure helpers carry ordinary docstrings |
| Keyed grid edits | A mutation is a row/column-keyed `Grid` `UPDATE` | The unit applies grid updates by key and ignores a wholesale preset write ([`architecture.md`](architecture.md), "write_preset is a trap") | `QuadCortex.set_bypass` | Reads, and operations outside the grid |
| One translation boundary | Screen values become wire values in one package, and a source-reading test proves no other module in the package does it | A wrong row is silent: the write lands on a real row and reads back perfectly (design principle 5 in [`domain-model.md`](domain-model.md); ADR-0013) | `pyquadcortex/device/translate/` | The protocol layer keeps zero-based coordinates and quotes the catalog's own units (ADR-0016) |
| Model state goes through the cache | A property reads `Device.state.value(entry, field)`; what it tracks is a `StateEntry` in `device/entries.py` | One account of what the model believes and how it learned it (ADR-0011) | `Device.firmware` in `pyquadcortex/device/device.py` | Values derived from an entry compute from `value()` instead of caching beside it |
| Catalog for structure, a reading for presentation | Option count, wire index and parameter index come from the catalog; anything a person sees needs a reading | Every fixed list the loaded preset reaches lands where the catalog says, re-driven each hardware run. The drawn order of three lists and one list's option names disagreed with the file; `display_pos` has matched twice and rests on those two readings | `tests/hardware/test_option_structure_on_unit.py` | Anything drawn |
| Evidence-stamped option lists | Each option list carries a status saying whether a person read its names off the unit | The catalog's `stepNames` differ from the unit's own `dynamic_steps` at 18 of 20 shared positions, and two of the fourteen lists read on the screen did not match it, so an unchecked list must not look checked | `options.OPTION_AUDIT` and `tests/fixtures/catalog/option_readings.json` | A list the unit does not draw is `absent` by observation |
| The profile is the class | `connect()` resolves `(device_type, zenos_git_hash)` to a client class before the handshake; a subclass declares what differs and refuses what it has not verified | One `if firmware ==` in a method body is what polymorphism removes (ADR-0020) | `QuadCortex41` in `pyquadcortex/protocol/profiles.py` | `ALWAYS`: the lifecycle methods every profile needs to connect and clean up |

## 6. Constraints

- **Runtime dependencies are `hid`, `protobuf` and `typing-extensions`.** The
  wheel installs with no compiler, no protoc and no build step.
  `typing-extensions` carries the `TypeVar` defaults that `typing` gained in
  3.13, while this package supports 3.11.
- **The protobuf pin, the committed gencode and the `grpcio-tools` floor move
  together.** Gencode 7.35.1, pinned `>=7.35.1,<8`, floor `grpcio-tools>=1.83.0`.
  `scripts/compile_protos.sh` refuses to write a downgrade and
  `tests/test_packaging.py` proves the pin equals the gencode (ADR-0001, ADR-0008).
- **Python 3.11 or newer.**
- **The environment is held to the pins.** `tests/test_packaging.py` compares
  each installed version against the requirement that declares it, and skips what
  is not installed. CI installs from `pyproject.toml` and always agrees; a working
  copy is installed by hand and drifted once, running mypy 2.3.1 against a `<2`
  pin. Any pin refuses an install beneath its floor. Only the `protobuf` and
  `mypy` pins can refuse a newer release, so upgrading anything else stays quiet
  however far it goes, and upgrading those two past their bound does not.
- **The default test suite runs offline.** No test imports `hid`, touches a unit
  or needs `DYLD_LIBRARY_PATH` (ADR-0002). The hardware suite in
  `tests/hardware/` runs only under `--hardware`, is state-neutral on success and
  never runs in CI (ADR-0005). Its gate and its rules are in
  `tests/hardware/readme.md`. Its modules stay importable offline:
  `tests/test_hardware_gate.py` collects the whole tree with `hid` poisoned, and
  `tests/test_scene_echo_predicates.py` imports `test_write_echo.py`.
- **Wire behaviour is stated per profile, named by CorOS version.** An unknown
  profile refuses to connect. An observation from another profile is recorded
  beside the 4.0.1 record in `protocol.md`, dated (ADR-0020).
- **Exclusive device access.** Cortex Control holds the HID interface, so the
  library and Cortex Control cannot be connected at the same time.

## 7. Decision Records

Decisions are recorded in [`ADR.md`](ADR.md):

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
| ADR-0012 | A grid push is noted and re-read, not merged, and the model publishes what it noticed |
| ADR-0013 | The translation boundary is a package, and what it may reach for is two lists |
| ADR-0014 | A parameter is addressed by a target, and the target owns what differs |
| ADR-0015 | The catalog is the source of truth for a parameter's scale; measurements are tests |
| ADR-0016 | Parameter values carry their own scale, and the protocol layer may speak the device's units |
| ADR-0017 | Every setting takes a typed value, and says so when it has no scale to convert against |
| ADR-0018 | A parameter constant carries its unit, and CI runs a type checker |
| ADR-0019 | The frame trailer's flags are read and reported; an encrypted payload is labelled, not decrypted |
| ADR-0020 | Connect resolves a device profile, and nothing else branches on firmware or model |
| ADR-0021 | An approval stops counting once the pull request's code changes |

## 8. Open Questions

- **Whether the mypy pin should allow 2.x.** The pin is `mypy>=1.15,<2`. No
  decision record holds the bound; its reason is in b726d3e (2026-08-28), "mypy
  is pinned below 2 so the enforcer cannot change under CI". It landed one day
  after ADR-0016 recorded the static unit checking as verified with mypy 2.3.1,
  the version the bound excludes. mypy 2.3.1 ran clean on this tree on
  2026-09-16, both halves of the blocking job. One run does not settle
  which checker every contributor runs. Trying 2.x puts the environment outside
  the pin, so `tests/test_packaging.py` fails until the pin moves with it.

Protocol unknowns are tracked in [`protocol.md`](protocol.md), "Open
questions", and [`roadmap.md`](roadmap.md).

## 9. Pointers

- Repo: <https://github.com/stokes-audio/pyquadcortex>. PyPI:
  <https://pypi.org/project/pyquadcortex/>
- Deep references: [`architecture.md`](architecture.md) (code),
  [`protocol.md`](protocol.md) (wire), [`capture.md`](capture.md) (observing the
  unit), [`domain-model.md`](domain-model.md) (the model's design)
- Status: [`manual-coverage.md`](manual-coverage.md), [`roadmap.md`](roadmap.md),
  [`../changelog.md`](../changelog.md)
- Operations: [`releasing.md`](releasing.md), [`troubleshooting.md`](troubleshooting.md),
  [`api.md`](api.md)
- Device reference: the [Quad Cortex manual](https://neuraldsp.com/manual/quad-cortex)

## 10. Infrastructure Dependencies

### Reused infrastructure

- **PyPI** for releases ([`releasing.md`](releasing.md))
- **GitHub Actions** for the offline suite, mypy and a packaging build on every
  pull request (`.github/workflows/ci.yml`)
- **hidapi** on any machine that talks to a unit
- **One physical Quad Cortex** (CorOS 4.0.1, `d14e`). The hardware suite runs on
  it before each pull request is marked ready, so runs are serialized.

### Workload characteristics

Single-device, single-connection USB HID at interactive rates (129-byte reports).
No server, no persistent storage, no capacity planning. The binding constraint is
access to the unit, not compute.

## What goes elsewhere

| If you are writing... | Put it in... |
|---|---|
| A rule ("always use X") | the repo-root `CLAUDE.md` |
| A decision and its rationale | [`ADR.md`](ADR.md) |
| A fact about the wire | [`protocol.md`](protocol.md) |
| Code-level architecture | [`architecture.md`](architecture.md) |
| How a finding was reached | the pull request, or the lab repository |

---

## Change Log

Entries are short by design ([`writing.md`](writing.md)). The full narrative
behind each one is in the lab repository,
`doc/pyquadcortex/history/steering-change-log.md`, and in the pull requests.

### 2026-09-21 - The hardware suite puts the edited flag back

- **What changed:** a session teardown clears the preset's edited flag with a
  recall. The option-structure fixture now asks whose the edits are, not whether
  any exist.
- **Why:** a write marks the preset edited and an undo is a write, so every run
  handed the unit back edited, and that fixture refused on every full run.
- **Scope:** `tests/hardware/`, its readme, `tests/test_hardware_report.py`,
  which drives every row of the decision offline. No decision record: ADR-0005
  already promises this.

### 2026-09-17 - The environment is held to the pins it claims to satisfy

- **What changed:** `tests/test_packaging.py` compares each installed version
  against the requirement that declares it. Three stale records were corrected:
  a test docstring, a sentence in [`domain-model.md`](domain-model.md), and the
  runtime-dependency count here.
- **Why:** this checkout ran mypy 2.3.1 against a `<2` pin, so the local check
  and CI's were different tools and nothing said so.
- **Scope:** `pyproject.toml`, `uv.lock`, `CLAUDE.md`, `contributing.md`,
  `changelog.md`, [`domain-model.md`](domain-model.md), sections 6 and 8 here,
  three test files. No decision record.

### 2026-09-16 - Documents rewritten for readability

- **What changed:** every document gained a purpose line and was rewritten to
  `writing.md`. Narrative history moved to the lab repository.
- **Why:** the documents had become transcripts of review arguments.
- **Scope:** all `.md` files; `tests/test_writing.py` added. ADR decisions unchanged.

### 2026-09-16 - The screen's shortening is one control's, and the unread audit is ranked

- **What changed:** a Flanger Engine's `WAVEFORM` spells `Sine` out where a Mono
  Synth draws `SIN`, so the shortening is the control's.
  `options.OPTION_USAGE` counts the parameters each list decides.
- **Why:** the catalog's words do not predict the shortening, and 94 unread
  lists are not 94 equal jobs.
- **Scope:** the generator, the options snapshot, the readings fixture, tests,
  four documents. No ADR: this settles a question in
  [`domain-model.md`](domain-model.md)'s appendix. No hardware test: screen
  readings live in the fixture.

### 2026-09-15 - The catalog carries the option vocabulary; the screen is a second renderer

- **What changed:** Cortex Control reads `ModelRepo` third in every session,
  and the reply carries every `stepNames` string. The unit draws its own
  abbreviations over the same data. Pink and white noise were confirmed swapped by
  an acoustic measurement.
- **Why:** an earlier rule said presentation was not downloadable. The captures
  said otherwise.
- **Scope:** `CLAUDE.md`, this file, `domain-model.md`, `changelog.md`,
  `scripts/generate_options.py`. No code change; `ADR.md` unchanged.

### 2026-09-15 - The catalog is trusted for structure and never for presentation

- **What changed:** structural facts (option count, wire index, parameter index)
  come from the catalog without a screen reading. `Parameter.display_pos` and
  `Model.resources` are published as the catalog's own claims. `Model.hidden`
  now reads the attribute's value, which restored two amps to `models.ALL` (414).
- **Why:** every fixed list the loaded preset reaches landed where the catalog
  said, 156 positions with no mismatch.
- **Scope:** `catalog.py`, the regenerated 4.0.1 snapshot, tests, `CLAUDE.md`,
  `domain-model.md`, `api.md`, `protocol.md`, `changelog.md`; new
  `tests/hardware/test_option_structure_on_unit.py`. `ADR.md` unchanged.

### 2026-09-14 - An option list says whether anyone has checked its names

- **What changed:** `options.OPTION_AUDIT` and a readings fixture record which
  lists have been read on a unit; the generator stamps each enum from it.
  `Parameter.hidden` is published and nothing branches on it.
- **Why:** `stepNames` disagrees with the unit's own `dynamic_steps` at 18 of 20
  shared positions.
- **Scope:** generator, `catalog.py`, `client.py`, the options snapshot, tests,
  five documents. `ADR.md` unchanged: the convention moved three times in one
  session and is not settled enough to record.

### 2026-09-14 - The connect burst is waited for as a group

- **What changed:** the hardware suite waits for all four messages in
  `HandshakeBurst.BURST_TAIL`, and a new cache entry has to be listed in
  `tests/test_handshake_burst_recorder.py` with a reason.
- **Why:** the other three follow `RecallPreset` by 3.6 to 6.0 ms against a
  100 ms poll, which failed two tests a few runs in a hundred.
- **Scope:** `tests/hardware/`, `tests/test_handshake_burst_recorder.py`,
  `protocol.md`, `CLAUDE.md`. No library change. `BURST_TAIL` stays off the
  profile class until a second profile has measured its own burst.

### 2026-09-12 - The Off-detent table is deleted; the floor is derived (ADR-0015)

- **What changed:** `Parameter.floor` is derived from `min_string`, `min`, `max`
  and `showAsInteger`. `units.FLOOR_WIRE` is gone. A cab accepts `Db(-30.0)`
  again, and the exact bottom of a labelled range is refused everywhere.
- **Why:** the unit's numeric entry states exactly the catalog's range, and the
  hand-measured table was wrong by 16 dB.
- **Scope:** `catalog.py`, `units.py`, tests, `protocol.md`, `changelog.md`.

### 2026-09-11 - The Global EQ gain span is measured at its ends (ADR-0017)

- **What changed:** `units.SETTING_SPANS["GLOBAL_EQ_GAIN_DB"]` rests on four
  screen readings across the whole travel, ends included.
- **Why:** the number was right and the evidence was the manual plus two close
  interior points, which cannot tell one span from a wider one.
- **Scope:** `units.py`, `tests/test_scales.py`, the hardware suite, `api.md`.
  `FREQUENCY`, `Q` and the `OUT` level still take `Encoded`.

### 2026-09-11 - A pan's drawn span is measured, not declared (ADR-0015)

- **What changed:** `units.LABELLED_END_SPAN` holds -50..50 for the 36 parameters
  carrying `min_string`, `mid_string` and `max_string`; `Parameter.mid_label`
  carries the middle label.
- **Why:** the catalog declares that one drawn control four different ways and
  the screen shows none of them.
- **Scope:** `catalog.py`, `units.py`, `tests/test_scales.py`, `protocol.md`,
  `domain-model.md`. The first and only span the library overrides; a second
  needs an ADR.

### 2026-09-07 - A pull request is a draft until the hardware suite has run on it

- **What changed:** `contributing.md` gains "Before you mark a pull request
  ready"; the template gains a Hardware section; `CLAUDE.md` states the rule.
- **Why:** a post-merge run found two stale records that a green offline suite
  had passed.
- **Scope:** `contributing.md`, `CLAUDE.md`, the pull request template. `ADR.md`
  unchanged: process, not architecture.

### 2026-09-06 - The profile seam is built (ADR-0020)

- **What changed:** `connect()` reads `Version`, resolves the profile class, and
  refuses an unknown pair. `QuadCortex41` and `QuadCortexMini` exist. Generated
  constants live under `protocol/catalogs/coros_4_0_1/` with shims at the old
  paths. The hardware suite reports which operations passed per profile.
- **Why:** the decision below.
- **Scope:** `session.py`, `client.py`, new `profiles.py` and `support.py`,
  generators, tests, docs. `CC_VERSION` still announces 4.0.1 on every profile.

### 2026-09-03 - One baseline becomes a registry of device profiles (ADR-0020)

- **What changed:** the repo describes device profiles instead of one baseline.
  `version()` accepts only a reply carrying an identity field.
- **Why:** contributions from a Mini (#31) and a 4.1.0 unit (#42, #44) could not
  be verified on the maintainer's unit.
- **Scope:** `protocol.md`, this file, `CLAUDE.md`, `ADR.md`. No code branched on
  a version yet.

### 2026-09-03 - The trailer's two unread bytes are named (ADR-0019)

- **What changed:** `framing.decode_reports` returns a `Frame` naming the
  message type, the `encrypted` and `compressed` flags, and the two device bytes.
  The RX path tells an encrypted payload, an unregistered type and corruption apart.
- **Why:** 15,675 captured messages settled the flags: `ENCRYPTED` only on
  `License` and `CloudLogin`; `COMPRESSED` agreeing with the gzip magic every time.
- **Scope:** `framing.py`, `transport.py`, tests, `protocol.md`, `ADR.md`.
  Compression is still detected by the magic bytes, and nothing decrypts.

### 2026-08-28 - Constants carry their unit, and mypy runs in CI (ADR-0018)

- **What changed:** `params.py` constants are `Param[Unit]`; mypy is a blocking
  CI job; `*_pb2.pyi` stubs are committed with package-relative imports.
- **Why:** ADR-0016 deferred static checking until a checker could run here.
- **Scope:** generator, `values.py`, `client.py` overloads, `compile_protos.sh`,
  CI, `tests/test_typing.py`.

### 2026-08-28 - The `--hardware` gate covers a path named on the command line

- **What changed:** a second hook in `tests/hardware/conftest.py` refuses a
  hardware test named on the command line without `--hardware`. Two files gained
  an `_on_unit` suffix so the tree collects from the repo root.
- **Why:** pytest does not consult `pytest_ignore_collect` for a named path, so
  `pytest tests/hardware/test_write_echo.py` drove the unit with no flag.
- **Scope:** `tests/hardware/`, `tests/test_hardware_gate.py`, the readme, this
  file, `CLAUDE.md`, ADR-0005's open question.

### 2026-08-28 - Every setting takes a typed value (ADR-0017)

- **What changed:** every method that writes a value takes a typed one, not only
  `set_param`.
- **Why:** `set_master_volume(30)` meaning "30 on screen" wrote full output.
- **Scope:** `client.py`, `units.SETTING_SPANS`, tests, `migration.md`, `api.md`.

### 2026-08-27 - A parameter value carries its own scale (ADR-0016)

- **What changed:** new `protocol/values.py` with `Encoded`, `Real` and eight
  unit types; `set_param` takes one positional value.
- **Why:** on a lane `VOLUME`, `real=0.0` was unity and `value=0.0` was silence.
- **Scope:** `values.py`, `client.py`, `targets.py`, tests, docs. `ParamTarget.spec_at`
  is the one resolver of "what sits at this wire index".

### 2026-08-27 - The catalog is the source of truth for scales (ADR-0015)

- **What changed:** `catalog.py` reads `skew`, `stepNames`, `dynamic`,
  `min_string` and `expAssignable`; `units.MEASURED_SPANS` is replaced by
  `FIRMWARE_CONSTANTS`; `options.py` is generated.
- **Why:** 615 parameters converted as straight lines because `skew` was unread.
- **Scope:** `catalog.py`, `units.py`, new `options.py`, tests, `protocol.md`.

### 2026-08-14 - The model keeps its own copy of what the unit is doing (ADR-0011)

- **What changed:** `device/state.py`, `device/entries.py` and `device/watch.py`
  land. `Device.firmware` and `.serial` read through the cache.
- **Why:** the model has to be right about a change made on the touchscreen while
  a script is connected. Story #11, Epic #8.
- **Scope:** the model package, `tests/hardware/conftest.py`, this file, `CLAUDE.md`.

### 2026-08-15 - A grid push is re-read, not merged (ADR-0012)

- **What changed:** a `Grid`, `SceneLabel` or `SceneColor` push voids the preset
  entry's copy; `device.events` publishes `Changed` and `Invalidated`. The
  translation boundary became a package with two allowlists (ADR-0013).
- **Why:** a hardware session corrected three assumptions about the burst and a
  recall.
- **Scope:** `ADR.md`, `domain-model.md`, `CLAUDE.md`, this file, `architecture.md`,
  `protocol.md`.

### 2026-08-13 - One translation boundary, and the model package is `device/`

- **What changed:** `pyquadcortex/device/translate.py` owns every screen-to-wire
  conversion; `PresetAddress`, `FootswitchLetter` and `SceneLetter` are exported.
  The model directory is `device/`, not `model/`.
- **Why:** an off-by-one row is a silent failure, so the arithmetic lives in one
  reviewable place. `model` collides with the protocol layer's word for a block.
- **Scope:** the model package, `tests/test_translation.py`, this file, `CLAUDE.md`,
  `architecture.md`, `domain-model.md`.

### 2026-08-12 - Tempo `MODE` closes, and ADR-0010

- **What changed:** `tempo_mode()` and `set_tempo_mode()` ship. ADR-0010 requires
  a differential state capture before a control is recorded as having no wire path.
  `docs/capture.md` gains "Diff the whole state".
- **Why:** three tests had listened for an announcement and heard none; one `READ` found
  the switch in `GlobalTempo.params[1]`.
- **Scope:** `client.py`, `protocol.md`, `domain-model.md`, `manual-coverage.md`,
  `capture.md`, `ADR.md`, `tests/hardware/state_snapshot.py`.

### 2026-08-12 - A persistent listener at the protocol layer (ADR-0009)

- **What changed:** `Transport.add_listener` and `remove_listener`; the transport
  refuses `request`, `await_broadcast` and `collect` on the RX thread;
  `protocol.connect(before_handshake=...)`.
- **Why:** a push-fed cache needs every message for the life of the connection.
- **Scope:** `transport.py`, `client.py`, `session.py`, tests, `ADR.md`, docs.

### 2026-08-12 - The generator floor joins the bindings/pin unit (ADR-0008)

- **What changed:** `grpcio-tools>=1.83.0`; `compile_protos.sh` refuses a
  downgrade; `tests/test_packaging.py` proves the pin equals the gencode.
- **Why:** an older generator emitted older gencode that imported cleanly and
  walked the pin backwards.
- **Scope:** `pyproject.toml`, `compile_protos.sh`, tests, `ADR.md`, docs.

### 2026-08-11 - The namespace flip lands, and ADR-0007

- **What changed:** `pyquadcortex` is the model and `pyquadcortex.protocol` is
  the protocol layer, moved verbatim. `Device` checks field presence and refuses
  reads once closed.
- **Why:** M1 story #9; every later story imports through the new layout.
- **Scope:** the whole package, CI, `scripts/`, every document. ADR-0001 keeps
  its `pyquadcortex/proto/` paths as written, because a decided record is
  append-only; the directory now lives at `pyquadcortex/protocol/proto/`.

### 2026-08-06 - Domain model Part 2: state tracking and save behaviour

- **What changed:** `domain-model.md` sections 9 to 13.
- **Why:** M0 story #4; both empirical questions answered on hardware.
- **Scope:** `domain-model.md`, this file.

### 2026-08-05 - Domain model structural design, and ADR-0006

- **What changed:** new `domain-model.md` (Part I and the appendix); ADR-0006
  decides the namespace flip.
- **Why:** M0 story #3.
- **Scope:** `domain-model.md`, `ADR.md`, this file.

### 2026-08-04 - ADR-0005: hardware-in-the-loop integration suite

- **What changed:** ADR-0005 recorded; section 6 says "the default test suite".
- **Why:** hardware verification was manual and not repeatable.
- **Scope:** `ADR.md`, this file.
