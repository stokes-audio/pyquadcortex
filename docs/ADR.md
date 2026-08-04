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
