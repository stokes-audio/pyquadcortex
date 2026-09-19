# pyquadcortex - Claude Guidance

> Purpose: the rules an agent follows in this repository, one bullet each, pointing at the document that holds the detail.

Steering: `docs/STEERING.md`. Decisions: `docs/ADR.md`. Writing rules: `docs/writing.md`.

Read `docs/STEERING.md` before non-trivial work: a new operation, a transport or
framing change, an unfamiliar subsystem. Skip it for typos, dependency bumps and
small documentation fixes.

## Conventions

### Working in this repository

- Dev setup: `uv venv && uv pip install -e ".[dev]"`. Run tests with
  `.venv/bin/python -m pytest`. The suite runs offline: no unit, no `hid` import,
  no `DYLD_LIBRARY_PATH`.
- Hardware tests live in `tests/hardware/` behind `--hardware`. How to run them,
  how a test names what it verifies, and how to add one: `tests/hardware/readme.md`.
  Every module there stays importable offline; `tests/test_hardware_gate.py` and
  `tests/test_scene_echo_predicates.py` hold that.
- Quit Cortex Control before a hardware session. It holds the HID interface
  exclusively.
- A pull request opens as a draft (`gh pr create --draft`). It is marked ready
  only after the hardware suite has run on its final commit, with the run recorded
  in the description, or the owner has waived the run there. The rule and the
  three states: `contributing.md`, "Before you mark a pull request ready".
- Never waive the run yourself. The contributor's no-unit state is not yours: you
  work where the unit is.
- A draft is not a handover. The sequence is one unit of work: open the draft,
  run the hardware suite on the final commit, run `/triage-pr` on that commit
  through a subagent, fix what it finds, re-run whatever the fixes invalidated,
  mark ready with `gh pr ready`, then send the link. Triage again after any
  substantive change.
- A finding you choose not to fix is named in the pull request description, with
  the reason.
- Changed code under a path in `docs/STEERING.md` section 4? Update STEERING,
  this file and `docs/ADR.md` in the same pull request.
- Write every document by `docs/writing.md`. `tests/test_writing.py` checks the
  mechanical part.
- Describe the protocol work as documenting the device's protocol as it is. Do
  not write "reverse engineering" in docs, comments, commits or issues.

### Two namespaces, one package (ADR-0006)

- `pyquadcortex` is the model of the unit. `pyquadcortex.protocol` is the
  message API. Nothing under `pyquadcortex/protocol/` imports from
  `pyquadcortex/device/`.
- The model's code lives in `pyquadcortex/device/`, not `model/`. In this
  codebase `model` means an amp or pedal block.

### Rules that tests prove

- Every conversion between a screen value and a wire value lives in the
  `pyquadcortex/device/translate/` package. No other module outside `protocol/`
  may do that arithmetic in any spelling. A new module inside the package joins
  `BOUNDARY_MODULES` in `tests/test_translation.py` with a reason. A protocol
  name the boundary reaches for joins `PROTOCOL_CONVERSIONS` or
  `PROTOCOL_NON_CONVERSIONS` there. (ADR-0013)
- A model API takes `FootswitchLetter`, never a bare footswitch number. A
  footswitch index and a block's column are different numbers that usually agree.
- `import hid` appears once, lazily, inside `session.open_device()`.
  `tests/test_import_cleanliness.py` proves it.
- Never gitignore or delete `pyquadcortex/protocol/proto/*_pb2.py` or `*_pb2.pyi`.
  Regenerate only with `scripts/compile_protos.sh`, and bump the `protobuf` pin
  and the `grpcio-tools` floor in the same commit. Details in
  `docs/architecture.md`, "The generated protobuf bindings". (ADR-0001, ADR-0008)
- mypy is a blocking CI job with one suppression, for `hid`. Keep `set_param`'s
  `int` overload to `Encoded`, a string or a switch; `tests/test_typing.py` holds
  both directions. (ADR-0018)
- A hardware test names the operations it verifies and asserts on with
  `@pytest.mark.verifies(...)`. An operation no hardware test names goes in
  `UNMARKED_OPERATIONS` in `tests/test_hardware_markers.py` with a reason.
- A new `StateEntry` comes through `BURST_TAIL`, `OUTSIDE_THE_BURST` or
  `NOT_WARMED_BY_THE_BURST` in `tests/test_handshake_burst_recorder.py`, with a
  reason. Wait for the connect burst as a group, never for one message of it.

### The model never guesses

- A state-cache read clears its reread mark only when concurrent messages
  restate its answer exactly. `FieldPlan.accepts` separates independent
  conversations sharing a protobuf type; it never suppresses fields within one
  conversation. See ADR-0011.

- The model shows what the unit shows, in the unit's words. A control we
  understand but cannot drive is modelled and refuses with
  `protocol.ControlNotDrivable(control, evidence, workaround)`, all three fields
  filled. A control we do not understand is left out, with the reason in
  `docs/domain-model.md`'s appendix. (ADR-0007)
- Before writing a refusal, run the differential capture in `docs/capture.md`,
  "Diff the whole state". "The unit announces nothing" is not evidence about the
  wire. Do not derive a refusal from a rule about parameter types;
  `LANE_OUTPUT_UNASSIGNABLE` is a measured list. (ADR-0010)
- A property reads a field only if it is present (`protocol.field_present`).
  `device/state.py` does this for you. A field the schema gives no presence is
  declared in its entry's `FieldPlan.no_presence` with evidence, held by
  `tests/test_state.py`.
- The cache keeps a copy of any submessage, never the container the RX thread
  decoded and shares with every listener.
- Never cache an incomplete reply as if it were complete. The cache keeps what
  the unit sent and re-reads for the rest.
- Model state is a `StateEntry` in `device/entries.py`, read through
  `device/state.py`. A push merges; a read replaces. Anything that stops trusting
  an entry without a message for it calls `mark_for_reread`. A message that sets
  a field the entry does not keep marks it; there is no "harmless field" list.
  (ADR-0011)
- `Grid` pushes invalidate rather than merge (`FieldPlan(invalidates=True)`), and
  so does `SceneLabel`, whose `index` and `label` have no presence and so blind
  the per-field check. Never widen the shared `SCAFFOLDING` skip to quiet a new
  entry. (ADR-0012)
- A closed `Device` refuses reads. `Device.close()` closes the state layer first.

### Parameters and scales

- A parameter's scale comes from the catalog: `min`, `max`, `skew`, one law.
  The numbers the catalog names but does not spell out live in
  `units.FIRMWARE_CONSTANTS`, each with evidence; an unknown name raises. The
  one measured override is `units.LABELLED_END_SPAN`; a second needs a new ADR.
  Screen readings are tests in `tests/test_scales.py`. (ADR-0015)
- A bound nobody can measure goes in `units.UNMEASURED_BOUNDS` and the parameter
  refuses. There is one, and its block crashes the unit.
- Before parsing a new catalog attribute, check `docs/domain-model.md`, "Catalog
  attributes", which lists the ones we can see and cannot yet explain.
- The catalog is trusted for structure (option count, wire index of each option,
  parameter index) and not for what the unit draws (option names, drawn order,
  `display_pos`). A two-position list is read by driving each position. Readings
  go in `tests/fixtures/catalog/option_readings.json`, one row per position;
  `absent` means someone looked and did not find the control. Detail:
  `docs/domain-model.md`, "Catalog attributes".
- A screen that shortens a word is a fact about that control, not about the
  catalog's text: a Flanger Engine spells `Sine` out where a Mono Synth draws
  `SIN`. What predicts a shortened control is unknown. `Parameter.hidden` is a
  candidate on two readings, and two readings are not a rule.
- `options.OPTION_USAGE` says how many parameters each option list decides.
  Quote it rather than counting again; `tests/test_option_audit.py` holds
  `docs/domain-model.md` to the snapshot's own numbers.
- A value says which scale it is on: `Real` or a unit type for the screen's line,
  `Encoded` for the device's 0..1, a string for a string parameter. A bare number
  is refused. `Encoded` is accepted everywhere and advertised nowhere a unit type
  would serve; `tests/test_examples.py` and `tests/test_docs.py` hold that.
  (ADR-0016)
- Every method that writes a value takes a typed one. A known span converts
  through a `catalog.Parameter`. An unknown span takes `Encoded` only and refuses
  `Real` with `ControlNotDrivable`. A setting with no 0..1 line refuses `Encoded`.
  Spans and their evidence: `units.SETTING_SPANS`. Selectors stay enums or bools.
  (ADR-0017)
- Grid edits use the row/column-keyed writes (`set_param`, `set_bypass`). Never
  extend `write_preset`.

### Profiles and the wire

- `connect()` resolves a device profile from `device_type` and `zenos_git_hash`
  and refuses an unknown pair. Everything that differs by firmware or model
  lives on the profile class, named by CorOS version; nothing else tests a
  version string. `protocol.models` is the 4.0.1 snapshot; a connection's own is
  `qc.models`. (ADR-0020)
- An observation from another profile goes beside the 4.0.1 record in
  `docs/protocol.md`, dated and named.
- A docstring states its evidence: confirmed on hardware, or inferred from the
  schema. Record a hardware verification in the docstring and in
  `docs/protocol.md`'s coverage table in the same change.
- The frame trailer's two flag bytes are reported and never decide what happens
  to a payload. Compression is detected by the gzip magic bytes. Nothing decrypts.
  (ADR-0019)
- The RX thread never dies: wrap every decode, skip unknown types at debug level,
  reset the reassembly buffer on anything malformed.
- A `Transport.add_listener` listener runs on the RX thread. It merges, marks and
  returns. It never reads from the device; `request`, `await_broadcast` and
  `collect` refuse on that thread. To see the connect burst, register through
  `protocol.connect(before_handshake=...)`. (ADR-0009)
- New operations follow `docs/architecture.md`, "How to add a new operation".

## Do not

- Send anything to the `Updater` surface. A botched firmware write is the one
  mistake a factory reset cannot fix.
- Drive cloud or account messages (`CloudLogin`, `CloudBackup`, capture sharing)
  without the owner's explicit go-ahead.
- Depend on the two device-filled trailer bytes at `n+6`.
- Run IR-import probing unattended. A past run killed the USB link and needed a
  power cycle.
- Treat the schema as ground truth for an unobserved message type. It is a
  hypothesis until measured.
