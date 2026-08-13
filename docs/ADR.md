# pyquadcortex - Decision Records

Architectural decisions for this repository are captured in this file, appended in order. It is a separate, co-located file - not inlined into `STEERING.md` - so a record can carry full detail without bloating the steering doc, and each record can be reviewed on its own. The format follows the [Nygard ADR convention](https://www.cognitect.com/blog/2011/11/15/documenting-architecture-decisions).

## Format

Each ADR has: ID, Title, Status, Decision, Context, Options, Open Questions, Rationale, Consequences. A `Supersedes: ADR-NNNN` line appears only when the record replaces a prior decision.

**Status** is one of `Proposed`, `Under Discussion`, `Decided`, `Superseded`, `Rejected`, optionally annotated: e.g. `Decided (confirmed 2026-08-03)` or `Superseded by ADR-NNNN (2026-08-03)`.

## Lifecycle

Records are append-only once `Decided` and built upon: a shipped decision is never rewritten. To reverse one, add a new record with a `Supersedes:` line and flip the old record's status. A `Proposed` or `Under Discussion` record is still an editable draft, and resolving an Open Question in place (`Resolved (date): ...`) is always fine.

---

## ADR-0001: Commit the generated protobuf bindings, with the runtime pin coupled to them

- **Status:** Decided (long-standing; recorded 2026-08-03)
- **Decision:** The generated `pyquadcortex/proto/*_pb2.py` bindings are committed to git and shipped inside the wheel. `pyproject.toml` pins the `protobuf` runtime to at least the gencode version that produced them, below the next major.
- **Context:** `pip install pyquadcortex` has to produce a working library. The protobuf runtime validates `runtime >= gencode` at import time, so the bindings and the pin are one unit: whoever regenerates one owns the other.
- **Options:** (a) Commit the bindings - chosen. (b) Generate at install time - requires a protoc toolchain on every user machine and adds a build step that can fail in user-specific ways. (c) Generate in CI at package-build time - keeps wheels clean but leaves a plain git checkout broken and adds release machinery.
- **Open Questions:** None.
- **Rationale:** Zero toolchain for users and CI; the wheel is self-contained; a plain checkout works. The cost - regeneration discipline - falls on maintainers, which is the right side of the trade.
- **Consequences:** Regenerating bindings means bumping the pin in the same commit, so the `.proto` files, the bindings, and the pin never diverge. `pyquadcortex/proto/__init__.py` stays load-bearing (it fixes protoc's sibling-import emit) and the bindings never go into `.gitignore`.

## ADR-0002: Fully offline test suite behind a single lazy `hid` import

- **Status:** Decided (long-standing; recorded 2026-08-03)
- **Decision:** The test suite runs with no device, no USB, and no `hid` import. `import hid` exists in exactly one place, lazily, inside `session.open_device()`. Each layer is tested against a purpose-built double (golden captured frames, `FakeHid`, `FakeTransport`).
- **Context:** `hid` is a ctypes binding that needs the native hidapi library, an OS-level install that on macOS usually also needs a `DYLD_LIBRARY_PATH` prefix. Anything importing it at module scope would break `--help`, CI, and the suite on machines without hidapi.
- **Options:** (a) Lazy import plus per-layer fakes - chosen. (b) Hardware-marked tests skipped in CI - leaves the interesting paths untested in CI and rots quietly. (c) Globally mocked `hid` module - hides the import-time dependency instead of removing it.
- **Open Questions:** None.
- **Rationale:** Nearly all development is possible with no unit attached; CI runs the real suite on plain Linux runners; the one lazy import site gives a single good error message distinguishing "hidapi missing" from "device not openable".
- **Consequences:** Hardware verification is a separate, manual step (via `examples/`), recorded in `docs/protocol.md`'s coverage table. New modules that need the device keep the import inside the opening function and prove import-cleanliness with a test.

## ADR-0003: USB HID is the only transport

- **Status:** Decided (long-standing; recorded 2026-08-03)
- **Decision:** The library speaks the device's protobuf control protocol over USB HID, and nothing else.
- **Context:** The Quad Cortex exposes several conceivable control surfaces, and the library needed one it could fully drive on an unmodified unit.
- **Options:** (a) USB HID - chosen; it is Cortex Control's own channel, offers full capability, and needs no modification. (b) The network surface - considered and found closed without access this project does not have (see `roadmap.md`, "Looked at and set aside"). (c) SSH via the OpenCortex SD-card modification - requires physically modifying the unit, stale past CorOS 3, warranty-ending. (d) MIDI - real and documented, but it cannot edit preset content and needs no library like this one.
- **Open Questions:** None.
- **Rationale:** Only USB HID gives full control of a stock unit. Everything empirical in the repo (framing, the write stall, exclusive access) is anchored to this one transport.
- **Consequences:** One device per connection; Cortex Control and the library cannot be connected simultaneously; transport facts like the 129-byte report and the benign write STALL are USB-HID facts and live at or below `transport.py`.

## ADR-0004: The domain model lands additively on top of the unchanged protocol layer

- **Status:** Decided (2026-07-31; recorded 2026-08-03)
- **Decision:** The planned object model of the device is a new namespace layered above `QuadCortex`. The existing message-level API stays public and unchanged.
- **Context:** `roadmap.md` sets the goal: an object model that represents what the unit shows and behaves the way the unit behaves, absorbing protocol quirks instead of documenting them. The question was where that model lives relative to the current API.
- **Options:** (a) Additive namespace above the protocol layer - chosen. (b) Evolve `QuadCortex` in place into the model - breaks existing scripts and mixes wire concerns with model state. (c) Replace the protocol API - destroys the foundation used for protocol work and for anything the model does not cover yet.
- **Open Questions:** None.
- **Rationale:** Existing code keeps working; the protocol layer remains available for gaps; the layering already supports a stateful model above `client.py` with no new wire knowledge.
- **Consequences:** Two public surfaces to document, with the model becoming the front door once it covers enough. The model wraps only behavior verified on hardware; anything less stays at the protocol layer rather than shipping a guessed abstraction.

## ADR-0005: A hardware-in-the-loop integration suite, state-neutral on success

- **Status:** Decided (2026-08-04)
- **Decision:** Alongside the offline suite (ADR-0002), the project maintains an online integration suite that drives a real unit over USB. A successful run is state-neutral: everything the suite changed on the unit is put back. A failed run restores as best it can and reports what it could not restore.
- **Context:** The offline suite proves the library builds the messages it intends; only hardware proves those messages are right. Today hardware verification is manual (`examples/` scripts plus the coverage table in `protocol.md`), so it is not repeatable and regressions can hide until someone happens to re-check. But an automated suite edits a real player's unit - presets, scenes, and settings someone gigs with - so it has to leave the unit as it found it.
- **Options:** (a) Manual-only verification (status quo) - not repeatable, no regression protection. (b) A destructive suite that assumes a dedicated test unit or sacrificial slots - simpler restore story, but demands hardware most contributors (and the maintainer) do not have to spare. (c) A state-neutral suite with a restore contract - chosen.
- **Open Questions:** Where it lives and how it is invoked (a pytest marker vs a separate runner). Which device state is genuinely restorable, and what the suite does about actions with no undo. Whether it reserves scratch user slots for create/delete coverage.
- **Rationale:** Repeatable hardware verification without demanding a second unit. The restore contract is what makes it safe to run on the only unit that exists.
- **Consequences:** Tests snapshot what they touch before changing it and restore in teardown, success or failure. The suite never runs in CI and stays fully separate from the offline suite, whose guarantee (ADR-0002) is unchanged. Failures name whatever they could not restore, so the owner knows what to fix by hand.

## ADR-0006: The domain model takes the top-level namespace; the protocol layer moves to `pyquadcortex.protocol`

- **Status:** Decided (2026-08-05)
- **Decision:** When the domain model first ships (M1), `pyquadcortex.connect()` returns the model's `Device`, and today's protocol layer moves - unchanged except for the import path - to `pyquadcortex.protocol`. One package, two namespaces.
- **Context:** ADR-0004 placed the model in a new namespace above an untouched top level. But once a full domain model exists, protocol-level calls are not what a new user should land on: the model is the library's reason to exist, and `import pyquadcortex` should hand them the front door. The library is deliberately 0.x with ~no users, so the import-path break is as cheap now as it will ever be.
- **Options:** (a) Model at top level, protocol at `pyquadcortex.protocol` - chosen. (b) ADR-0004's original shape (protocol keeps the top level, model in a sub-namespace) - permanently taxes every future user to spare a handful of current ones. (c) Two separate PyPI packages - coupled releases and shared internals across a distribution boundary, for no benefit. (d) `pyquadcortex.usb` - names the transport, not the layer.
- **Open Questions:** None.
- **Rationale:** The model becomes the documented front door the moment it exists, with no release where `connect()` is ambiguous. The protocol layer loses nothing: same API, same support, one import deeper.
- **Consequences:** Refines ADR-0004's "additive namespace" consequence: the model is still additive code-wise and the protocol API is still public and unchanged, but import paths flip at M1 - existing 0.x scripts update one import line. The flip and its changelog/readme messaging land in the M1 Epic. The Intent Brief's "Additive, not breaking" requirement is amended to match (owner decision, 2026-08-05).

## ADR-0007: The model may represent a control whose wire path is still open

- **Status:** Decided (2026-08-11)
- **Decision:** When we understand what a control does on the unit but have not found the message that drives it, the model represents the control the way the unit presents it, and refuses the operation it cannot yet perform. It never guesses. This is a different case from design principle 3's "omission over caveat", which covers a control whose behaviour ON THE DEVICE we do not understand; that one is still omitted.
- **Context:** TEMPO MODE is the case that raised this. The unit's Tempo menu has a GLOBAL / PRESET switch. In PRESET mode the tempo and all seven metronome settings are stored in the preset, which is exactly what the wire shows: every preset carries a `TempoControl` block and it is writable (`protocol.md`, "Per-preset tempo, LED and metronome"). Cortex Control offers the same switch, so some route to it exists. What we have not found is that route. Three independent tests watched for a broadcast when the switch is changed, and saw nothing; two of the three also committed the menu, and the third's action script records only the toggle. The design doc recorded all of this as "not on the wire at all". That conclusion is now understood to be too strong. **"Broadcasts nothing" is not "not readable".** A control the device never announces may still answer a READ, or be carried in a message we have not decoded, and no test so far has ruled either out.
- **Options:**
  - **(a) Model the switch, refuse what cannot be driven - chosen.** The player sees a switch, so the model has one. Reading it, or writing it, raises with a message saying the wire path is not known yet.
  - **(b) Omit tempo from the model entirely.** Throws away a control we do understand - the BPM and the whole metronome are confirmed writable - to avoid one field we do not.
  - **(c) Let a tempo write through and work out the scope afterwards.** A guess behind a clean API. The write lands in whichever scope the unit happens to be in, and the caller is told nothing. This is the "the model lies" failure the Intent names, and it is the worst of the three because it looks like it worked.
- **Open Questions:** How the refusal reads in practice - one exception type for "no wire path known", or a per-feature message. Settled when the first such control ships, which is M3 at the earliest.
- **Rationale:** The model's job is to look like the unit. A switch the player can see, missing from the model with no explanation, is its own kind of lie. Refusing is honest in both directions: it tells the caller the control is real and tells them we cannot drive it. It also keeps the door open, because a model with a `mode` on it has somewhere to put the answer the day the wire path turns up. Option (c) is rejected on the project's oldest rule: the device accepts a write it does not understand and says nothing, so a guess and a success are indistinguishable to the caller.
- **Consequences:**
  - The design doc's `Tempo` gains `mode`, and the appendix's "Tempo MODE" row changes from a permanent omission to an open protocol investigation. `docs/domain-model.md` §13 says the same.
  - Finding the MODE wire path becomes a prerequisite of M3's device-settings Epic, not of M1. **No tempo surface ships at M1**, so nothing in this record is user-visible yet.
  - Design principle 3 keeps its meaning and gains a boundary: omission is for behaviour we do not understand, refusal is for behaviour we understand and cannot yet drive. A record that says which one applies is now expected of anything the model leaves out.
  - This does not license modelling controls on a hunch. It applies where the unit's behaviour is confirmed and only the message is missing; a control we have not understood on the hardware is still omitted.

## ADR-0008: A control with no known wire path gets a bounded search before it is modelled as refused

- **Status:** Decided (2026-08-12)
- **Decision:** ADR-0007's rule stands and is unchanged: a control we understand but cannot drive is modelled and refuses. What changes is what has to happen first. Before a control is recorded as having no wire path, it gets a **differential state capture**: read everything the device will answer, in each position of the control, and diff the two - including field numbers the recovered schema does not know. "No broadcast was observed" is not a finding about a wire path and does not on its own justify the refusal. TEMPO MODE, the case that raised ADR-0007, is no longer an instance of it: `Tempo.mode` is an ordinary readable, writable property.
- **Context:** ADR-0007 recorded that three tests had watched for a broadcast when the MODE switch moves and seen nothing, that this had been over-read as "not on the wire at all", and that the honest state was an open investigation. It was right about all of that, and the investigation took one session. **MODE is the DEVICE tempo block's parameter 1**, carried in `GlobalTempo.params`: `0.0` PRESET, `1.0` GLOBAL, readable by `GlobalTempo{READ}` and writable by a `GlobalTempo{UPDATE, params{index: 1, param_values}}`. Confirmed three ways - the wire value moved and moved back with nothing else in the device's readable state moving either way; a host write moved the unit's own menu, watched at the unit; and the tempo in effect switched between the two blocks' stored values, 111 bpm from the preset's `0.355` and 120 from the device's `0.400`, both exact on a 40-240 range. `protocol.md`, "MODE is the DEVICE tempo block's parameter 1", is the record.
- **Options:**
  - **(a) Require a differential capture before recording "no wire path" - chosen.** Costs one session per control. The cost is bounded and known, because it is now a harness (`tests/hardware/state_snapshot.py`) rather than a bespoke experiment.
  - **(b) Leave ADR-0007 as it stands.** Its rule is sound, and the failure was not in the rule. But nothing in it required anyone to ASK before concluding, and the eight releases the wrong claim survived were the cost of that gap.
  - **(c) Treat every unfound control as merely undiscovered and model nothing until found.** Collapses back into design principle 3's omission, which ADR-0007 rejected for good reasons that have not changed.
- **Open Questions:** ADR-0007's - how a refusal reads in practice - is now unforced, because the model has no refused control left. It stays open and gets settled by the first genuine instance rather than by TEMPO MODE.
- **Rationale:** The three tests were good instruments honestly reported. What went wrong is that a listener answers "does the device announce this?" and the conclusion drawn was "is this on the wire?" - a different question, never asked. Diffing rather than hunting is what makes the search bounded: the earlier work looked for the field it expected in the message it expected, so a field one index away in a message shape it had already written off was invisible to it. Requiring the capture also removes the incentive to reach for ADR-0007 as an easier answer than another hardware session.
- **Consequences:**
  - `Tempo.mode` is readable and writable, and is a **device** setting - it affects every preset and there is nothing to save. It ships with the rest of `Tempo` at M3. `QuadCortex.tempo_mode()` / `set_tempo_mode()` and `TempoMode` exist at the protocol layer now.
  - ADR-0007 keeps its status and its rule. It currently has no instance, which is the healthy state for it.
  - Epic #8's dependency on the TEMPO MODE wire path is resolved. It never gated M1; it no longer gates M3.
  - A negative result about device traffic now states which question the instrument answered. "The unit does not announce X" and "X is not on the wire" are separate claims and the second needs a READ.
