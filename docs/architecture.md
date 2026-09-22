# Architecture and contributor guide

> Purpose: how the code is layered, and how to add an operation or a device profile without fighting the layers.

For the wire itself (frame layout, handshake, message shapes) see
[`protocol.md`](protocol.md). This document covers the code.

> `pyquadcortex` is unofficial and not affiliated with Neural DSP. It speaks the
> device's own protobuf protocol, implemented from the recovered schema in
> `protocol/proto/` and from observing Cortex Control sessions against CorOS
> 4.0.1, firmware `d14e`.

## Contents

- [Layer map](#layer-map)
- [What flows through the layers](#what-flows-through-the-layers)
- [send vs request vs await_broadcast](#send-vs-request-vs-await_broadcast)
- [How to add a new operation](#how-to-add-a-new-operation)
- [The generated protobuf bindings](#the-generated-protobuf-bindings)
- [Testing philosophy](#testing-philosophy)
- [What is not implemented yet](#what-is-not-implemented-yet)
- [Adding a device profile](#adding-a-device-profile)

## Layer map

The package has two public namespaces (ADR-0006). `pyquadcortex` is the model of
the unit. `pyquadcortex.protocol` is the message-level API the model is built
on. Each file owns one concern, and each layer knows only the layer below it.

```
    pyquadcortex/            THE MODEL - what import pyquadcortex hands back
      device/device.py       connect(): opens the unit, returns a Device.
      |                      Speaks the unit's vocabulary, never the wire.
      |
      device/state.py        The write-through cache every model read goes
      |                      through: applies what the unit pushes, asks for
      |                      what it does not push, and knows when to stop
      |                      trusting its copy
      |
      device/entries.py      What the cache tracks, as data: which messages
      |                      carry each part, which fields the model keeps,
      |                      and how to read each one
      |
      device/watch.py        Whether a write landed: the echo watcher and its
      |                      three outcomes, plus the one thread that gives up
      |
      device/preset.py       The preset on the grid: its name, its scenes, and
      |                      whether it is still the loaded one
      |
      device/grid.py         Four rows of eight slots, and the two ways to look
      |                      at them - live-bound to the active scene, or fixed
      |
      device/blocks.py       What sits in a cell: a virtual device, the ends of
      |                      a row, a splitter, a mixer
      |
      device/events.py       What the model noticed, for a caller who wants to
      |                      know as it happens - on a thread of its own
      |
      device/errors.py       The refusals that mirror something the unit cannot do
      |
      device/translate/      Screen values <-> wire values, and the ONLY place
      |                      either becomes the other
      |
      |                      -- the model/protocol seam --
      |
    pyquadcortex/protocol/   THE PROTOCOL LAYER - one call per protocol message
      cli.py                 argparse subcommands -> client methods
      |
      session.py             connect(): find and open the HID device, start the
      |                      transport, resolve the profile, run the handshake
      |
      client.py              QuadCortex: the message-level API. Builds protobuf
      |                      messages. Knows nothing about HID or framing.
      |
      transport.py           Framed I/O over an hidapi-like device: write
      |                      reports, RX thread and reassembly, correlation,
      |                      keepalive thread, listeners
      |
      registry.py            CortexMessageType integer <-> generated class
      |
      framing.py             HID frame codec: (message type, bytes) <-> raw
      |                      129-byte reports. Pure bytes and ints.
      |
    [ hidapi / the unit ]

      proto/                 Generated bindings and stubs (committed; see below)
      profiles.py            The device profiles: one class per measured unit
      catalogs/              Generated constants, one snapshot per profile
      enums.py               Named port, instrument and setlist values
      targets.py             WHERE a parameter lives: Block, LaneOutput,
                             LaneInput, Mixer, Splitter, Tempo
      values.py              Encoded, Real and the unit types
      units.py               The numbers the catalog names but does not spell
                             out, and the two measured setting spans
      errors.py              BlockRefused, ControlNotDrivable
      hid_ids.py             Vendor and product IDs, interface number

    pyquadcortex/_version.py The version string, read by both namespaces
```

The model calls the protocol layer and never the reverse: nothing under
`pyquadcortex/protocol/` imports from `pyquadcortex/device/`. A caller can use
either namespace or both; `Device.from_client(qc)` puts a model on a protocol
connection that is already open.

### framing.py

A pure codec. It turns a message type and a payload into 129-byte HID reports,
turns reports back into a `Frame`, and answers "is this list of reports a
complete message yet?". No hidapi, no protobuf, no threads. `message_type` is an
integer here, which is why the module is tested against real captured frames byte
for byte (`tests/fixtures/frames/*.json`).

`decode_reports` returns a frozen `Frame`: `message_type`, `payload`,
`encrypted`, `compressed` and `device_bytes`. The two flag bytes were confirmed
over 15,675 captured messages ([protocol.md 2.3](protocol.md#23-the-message-envelope-trailer)).
`payload` is exactly what the trailer wrapped, still gzipped if `compressed` and
still encrypted if `encrypted`. The codec labels and leaves the bytes alone.

Public surface: `encode_message`, `decode_reports`, `is_complete`, `Frame`, and
the wire constants (`REPORT_SIZE`, `CHUNK_SIZE`, `TRAILER_SIZE`, the `TRAILER_*`
offsets, `FLAG_FIRST`, `FLAG_LAST`, the two report IDs).

### transport.py

`Transport` wraps any object with hidapi's `write(report)`, `read(size,
timeout_ms)` and `close()`. It owns everything time-dependent and concurrent:

- outbound: frames a message and writes its reports as one group under a write
  lock, so a keepalive can never land between a multi-report message's fragments
  (continuation reports carry no header);
- inbound: a daemon RX thread reads reports, reassembles them by frame flags,
  gunzips a compressed payload, parses the protobuf, and dispatches;
- correlation: `request()` waiters keyed by `request_id`, and `await_broadcast()`
  waiters keyed by message class plus an optional predicate;
- listeners: `add_listener()` registers a callable that sees every decoded
  message for the life of the connection. It consumes nothing;
- a keepalive thread;
- the unit's benign write stall ([protocol.md](protocol.md#the-benign-write-stall)):
  write errors are logged at debug and swallowed, and a dead unit is detected by
  `request()` timeouts.

The RX thread must never die. Every decode is wrapped, unknown types are skipped
at debug level, and the reassembly buffer resets on anything malformed. Code
added to the RX path keeps that property.

Listeners run on that thread, so the same rule covers them. One that raises is
logged and skipped. A listener may not read from the unit: `request`,
`await_broadcast` and `collect` raise `RuntimeError` on the RX thread, because
that thread is the one that would deliver the answer. A listener applies what a
push carries and notes what needs re-reading; the caller's thread does the
reading (ADR-0009, and [domain-model.md](domain-model.md) section 9).

### registry.py

The only place that maps `CortexMessageType.Enum` integers to generated `*Message`
classes: `type_for(cls)` and `class_for(message_type)`. A type absent from
`_BY_NAME` cannot be sent, and inbound frames of that type are dropped.

### client.py

`QuadCortex` is the message-level API. It builds protobuf messages and calls
`send`, `request`, `await_broadcast` and `next_request_id` on the transport it
was given. It never touches a report, a frame or a byte offset.

Because it depends on four transport methods, the whole API is testable with a
short fake (`tests/test_client.py`) and no unit, no `hid`, no timing. Every wire
concern stays below this line. When you add an operation, the protobuf building
belongs here and nothing else does.

Two pure helpers also live here: `slot_to_position("28C") -> 218` and
`input_chain_rows(preset, port)`.

### session.py

`protocol.connect()` is the protocol layer's front door: `open_device()` finds and
opens the HID interface, a `Transport` starts around it, the unit's `Version` is
read and resolved to a profile class (ADR-0020), `QuadCortex._hello()` runs the
handshake, and the client comes back ready. The client remembers what it opened
so `close()` tears down only what `connect()` created. A client built around a
caller-supplied transport owns nothing and `close()` is a no-op.

`connect(before_handshake=...)` is the hook for anything that has to be watching
before the handshake. The unit's burst of state starts seconds after `connect()`
returns (measured: the client returns at 2 s, the catalog lands at 4.9 s, the
current preset at 10.1 s; [protocol.md](protocol.md), "Connect burst"). A listener
registered on the returned client has missed it. One registered through this hook
has not.

`import hid` lives inside `open_device()`. That is a contract; see
[Testing philosophy](#testing-philosophy).

### cli.py

`qcctl`. `build_parser()` stays import-safe and device-free. `main()` does the
device work. The `version` subcommand bypasses the handshake
(`_open_unconnected()`) because a plain `Version` read works without the connect
gate, and the handshake's own version announce would race that read's reply.

### device/device.py

`pyquadcortex.connect()` opens the unit through `protocol.connect()` and returns a
`Device`. `Device.from_client(qc)` wraps a protocol connection you already have
and does not take ownership of it. `Device.client` is the way back down to the
message level.

Every value a `Device` reports comes out of the state layer, reached as
`Device.state`. `connect()` builds that cache first and hands its subscription to
`protocol.connect(before_handshake=...)`, so the handshake's burst warms it.
`Device.from_client` subscribes to the live connection and starts cold.

The rest of the model is designed in [domain-model.md](domain-model.md) and built
story by story. Nothing is stubbed out to look finished.

### device/state.py, device/entries.py, device/watch.py

The state layer, designed in [domain-model.md](domain-model.md) sections 9 and 10
and decided in ADR-0011 and ADR-0012.

`state.py` holds the cache. It registers one listener and, for each message,
merges the fields the model keeps into its copy. A message that sets a field the
model does not keep marks that entry, and the next read goes to the unit on the
caller's thread. A message type no entry tracks returns at once, which is what
makes the metronome's tempo stream free.

`entries.py` is the table of what is tracked: per entry, the message types that
carry it, the fields kept, and the read that answers it.

`watch.py` is the write side. A write updates the cache at once and the unit's
echo confirms it in the background against one rule: every field we sent must
come back with the value we sent. One watchdog thread per connection, started on
the first write.

### device/translate/

The model speaks what the screen shows: rows 1 to 4, slots 1 to 8, scenes and
footswitches as letters, dB, Hz, bpm, ms. The wire speaks zero-based indexes and
raw scales. Every conversion between the two lives here, and `tests/test_translation.py`
reads the source of the whole package outside `protocol/` to prove nothing else
does it (design principle 5 in [domain-model.md](domain-model.md), ADR-0013).

The package is split by responsibility: `guards`, `coordinates`, `letters`,
`addresses`, `units`, and `grid` for reading a whole preset in screen numbers.
Every public name is re-exported. Because the test exempts the whole directory, it
names the modules inside it (`BOUNDARY_MODULES`), and a new one has to be added
there with a reason.

Where a protocol helper already performs a conversion (`bpm_to_tempo`,
`db_to_lane_level`, the slot-name pair), this package calls it rather than
restating the arithmetic. `bpm_to_tempo` stays in the protocol layer because
`QuadCortex.set_param(Tempo(), ...)` calls it, and the protocol layer may not
import the model.

Public value types: `PresetAddress`, `FootswitchLetter`, `SceneLetter`,
re-exported from `pyquadcortex`. The conversion functions are reached as
`translate.row_to_wire(...)` and are not re-exported.

## What flows through the layers

A host command, top to bottom:

```
qc.switch_scene(1)
  -> client builds SceneMessage{action: UPDATE, selected_scene: 1}
  -> transport.send(msg): registry.type_for(SceneMessage) -> 13
  -> framing.encode_message(13, msg.SerializeToString())
       -> [b'\x02' + len + flags + payload + trailer + padding]
  -> device.write(report)  (the STALL "error" is swallowed)
```

A message from the unit, bottom to top:

```
device.read() -> one 129-byte input report
  -> RX thread appends to the reassembly buffer
  -> framing.is_complete(buffer)?  (flag-driven; no length field exists)
  -> framing.decode_reports(buffer) -> Frame
  -> frame.encrypted?  log which type it was and stop (nothing decrypts)
  -> registry.class_for(frame.message_type), or log the unregistered number
  -> gunzip the payload if it starts 1f 8b, then parse
  -> _dispatch: every listener, then collectors, then a request_id waiter,
     else a broadcast waiter, else dropped
```

## send vs request vs await_broadcast

Choosing correctly is most of the work of adding an operation. The first three
rows serve one exchange. The last is how a long-lived caller watches the link.

| Transport method | Use when | Blocking | Correlation |
|---|---|---|---|
| `send(msg)` | The unit acts on the message and you do not need its answer: scene switch, grid edits, recall, keepalive. | No | None |
| `request(msg, timeout=)` | The unit answers with a message of the **same type**: `Version` read, `ResetCommsBuffers`, the `File` mutations. | Yes | A fresh `request_id` is registered before the write. The reply is the first inbound message of the same type whose `request_id`, if present on both sides, matches. |
| `await_broadcast(cls, trigger, timeout=, match=)` | The answer arrives as a **push of a different type**, or as an unsolicited push the unit emits in response to an action: the `RecallPreset` push that carries a preset, the `File` folder listings. | Yes | By message class, plus your `match` predicate. A message the predicate rejects is left for a later waiter. |
| `add_listener(fn)` | You want every message for the life of the connection: a cache fed by the unit's pushes, or a log of the link. | No, but `fn` runs on the RX thread | None. Every message, every type. Removed with the returned callable or `remove_listener(fn)`. |

Two facts the current code encodes and new operations must respect:

- **`READ` replies carry no `request_id`.** `_dispatch` falls back to "first
  waiter of the same type wins", so two concurrent reads of one type cannot be
  told apart. That is why `_hello()` issues no `Version` read of its own.
- **A state-changing request triggers a cascade of other-type messages that all
  echo its `request_id`.** Correlation is by type first, id second. To pick one
  push out of a cascade, use `await_broadcast` with a `match` on the id, as
  `read_preset` does.

## How to add a new operation

Worked example: `set_global_tempo(bpm)`.

**1. Find the message in the schema.** The recovered schema is
`protocol/proto/ProductionAutomation.proto` (control messages) and
`protocol/proto/Preset.proto` (the `BinaryPreset` grid model). Start from the
`CortexMessageType.Enum` block at the top of `ProductionAutomation.proto`, which
lists all 71 types with their wire integers. Find `GlobalTempo = 33`, then
`message GlobalTempoMessage`. Nearly every scalar field sits in a synthetic
`oneof`, so `HasField()` tells "set to zero" from "not set".

**2. Register the type if it is missing.** Add
`"GlobalTempo": pa.GlobalTempoMessage` to `_BY_NAME` in `registry.py`. Without it
`transport.send` raises `KeyError` and inbound frames of that type are dropped.
Many types are registered but have no client method; check first.

**3. Add a method to `QuadCortex`.** Build the protobuf and hand it to the
transport. No HID, no bytes, no sleeps.

If the operation writes a parameter, it needs no new method: `set_param` covers
every container through a target, so a container the library has never reached is
a new class in `targets.py`. A target says which collection on `Chain` holds it,
how that collection is keyed, which catalog model describes its parameters,
whether it has per-scene values, and any conversion the catalog cannot do
(ADR-0014).

```python
def set_global_tempo(self, bpm: float):
    """Set the device's global tempo."""
    msg = pa.GlobalTempoMessage(action=pa.MessageAction.UPDATE)
    msg.tempo = bpm
    return self._t.send(msg)
```

Pick the transport method from the table above. Say in the docstring what is
confirmed on hardware and what is inferred from the schema.

**4. Add named constants if the field is an enum.** Device-side enums a caller
passes belong in `enums.py`, mirroring the schema's names and values. Copy them
from the schema, and say in a comment which ones were confirmed on hardware.

**5. Write offline tests.** `tests/test_client.py` has the pattern: a
`FakeTransport` that records `sent` messages, returns canned `request` replies,
and replays a `broadcast` from `await_broadcast`. A good test asserts the exact
wire shape:

```python
def test_set_global_tempo_sends_a_global_tempo_update():
    fake = FakeTransport()
    client.QuadCortex(fake).set_global_tempo(120)
    (msg,) = fake.sent
    assert isinstance(msg, pa.GlobalTempoMessage)
    assert msg.action == pa.MessageAction.UPDATE
    assert msg.tempo == 120
```

For framing or transport changes use `tests/test_framing.py` (captured frames as
golden fixtures) and `tests/test_transport.py` (an in-memory `FakeHid`). No test
may import `hid` or need a unit.

**6. Verify on hardware.** Offline tests prove you built the message you
intended. Only the unit proves it is the right one. Quit Cortex Control, connect
over USB, and check the effect two ways where you can: read the state back over
the protocol, and look at the screen. Then record it: the docstring, and the
coverage table in [`protocol.md`](protocol.md#operation-coverage). Add a hardware
test that names the operation with `@pytest.mark.verifies` (see
`tests/hardware/readme.md`).

If the operation is undocumented, or a write you believe correct has no effect,
do not keep guessing shapes. Perform the action on the unit and read what it
sends: [capture.md](capture.md).

## The generated protobuf bindings

`pyquadcortex/protocol/proto/*_pb2.py` and `*_pb2.pyi` are generated code that is
**committed on purpose** (ADR-0001). `pip install pyquadcortex` then needs no
protoc and no build step, the wheel is self-contained, and CI runs the suite
without a compiler.

**Never add them to `.gitignore` or delete them as build output.** That breaks
installs and drops the type checking.

`proto/__init__.py` appends its own directory to `sys.path`, because protoc emits
flat sibling imports (`import Preset_pb2`) that fail inside a package. The `.pyi`
stubs cannot use that shim: a type checker resolves imports statically, so a flat
import makes every `Preset`-typed field silently `Any`. `compile_protos.sh`
rewrites the stubs' imports package-relative and refuses to write a stub that
still imports a sibling flat; `tests/test_packaging.py` checks it (ADR-0018).

### Regenerating

```bash
scripts/compile_protos.sh
```

It uses the generator from the dev extra (`grpcio-tools`), generates into a
temporary directory, and copies into the package only after the gencode check
passes.

Three numbers move together, in one commit (ADR-0001, ADR-0008):

| what | where | today |
|---|---|---|
| the gencode stamp in each generated file | `pyquadcortex/protocol/proto/*_pb2.py` | 7.35.1 |
| the `protobuf` runtime pin | `pyproject.toml` | `>=7.35.1,<8` |
| the `grpcio-tools` floor | `pyproject.toml`, dev extra | `>=1.83.0` |

The runtime checks `runtime >= gencode` at import, so a pin below the gencode is
an `ImportError` for every user. An older generator is the quiet failure: older
gencode still imports, and the pin stops describing the bindings. The floor is
found by running candidate versions and reading the stamp they write, because
`grpcio-tools` metadata does not say which gencode a release emits:

```bash
printf 'syntax = "proto3";\nmessage Ping { int32 n = 1; }\n' > /tmp/ping.proto
python -m grpc_tools.protoc -I /tmp --python_out=/tmp /tmp/ping.proto
grep "Protobuf Python Version" /tmp/ping_pb2.py
```

Two guards, covering different routes:

| Guard | Catches | When |
|---|---|---|
| `scripts/compile_protos.sh` | a generator that would write older gencode than what is committed; it refuses and writes nothing | at regeneration |
| `tests/test_packaging.py` | committed gencode that disagrees with itself or with the pin | every pull request |

## Testing philosophy

The suite is **fully offline**. No unit, no USB, no `hid` import, and on macOS no
`DYLD_LIBRARY_PATH`. That is what lets almost all development happen with no
hardware and lets CI run the real suite on plain runners (ADR-0002).

| Layer | Test double | File |
|---|---|---|
| `framing` | none needed (pure functions), plus real captured frames as golden fixtures | `tests/test_framing.py`, `tests/fixtures/frames/` |
| `transport` | `FakeHid`: an in-memory hidapi stand-in that frames its own replies, so reassembly and correlation run for real | `tests/test_transport.py` |
| `client` | `FakeTransport`: records `sent`, returns canned `request` replies, replays a `broadcast` | `tests/test_client.py` |
| `session` | `open_device` and `Transport` monkeypatched | `tests/test_session.py` |
| `cli` | `build_parser()` exercised directly | `tests/test_cli.py` |
| the model | `FakeClient`, plus the same monkeypatched device and transport as `session` | `tests/test_device.py` |
| the state layer | `LoopbackTransport`: canned replies under the real `QuadCortex`, notifying listeners before the caller wakes | `tests/test_state.py` |
| the state layer's threading | a real `Transport` over a fake HID link, because "the RX thread never reads" is a claim about a thread | `tests/test_state_rx.py` |
| schema | the enum integers the code relies on, and that core messages instantiate | `tests/test_schema_compiles.py` |
| namespaces | the pre-flip `__all__` must all resolve under `pyquadcortex.protocol` | `tests/test_namespace.py` |
| the translation boundary | called directly; two tests read the package's source with `ast` | `tests/test_translation.py` |

### The import-safety contract

**`import pyquadcortex` and `qcctl --help` never require hidapi.**

- `import hid` appears once, lazily, inside `session.open_device()`.
- `cli.build_parser()` constructs no transport and opens no device; `main()`
  imports `session` inside the function body.
- `tests/test_import_cleanliness.py` imports every module in the package in a
  subprocess, so a new module is covered the day it is added.

The `hid` package needs the native hidapi library, an OS-level install that on
macOS usually also needs a `DYLD_LIBRARY_PATH` prefix. If any of that were
required at import time, `--help`, `pip check`, CI and the whole suite would fail
on machines without hidapi. The one lazy import also gives one good error message:
`open_device()` raises `DeviceNotFoundError` distinguishing "hidapi missing" from
"device not openable".

If you add a module that needs `hid`, import it inside the function that opens
the device.

## What is not implemented yet

Where the library is meant to go is [roadmap.md](roadmap.md). Feature by feature,
what is covered and what is not is [manual-coverage.md](manual-coverage.md). The
wire's open questions are at the end of [protocol.md](protocol.md#open-questions).
Three code-level gaps matter to someone extending the library:

- **Registered but unwrapped message types.** `registry.py` registers about three
  dozen types so the RX thread can decode chatter, and `client.py` exposes methods
  for a subset. A registered type with no method is the cheapest addition: one
  client method plus tests. `GlobalTempo` is the awkward one: it alternates a
  clock shape with a 25-parameter shape, so a reader must match on a reply that
  carries parameters.
- **Types not in the registry at all.** Whole areas are untouched: `Screenshot`,
  `Diagnostics`, `CloudBackup`, `Confirmation`, `SuspendConnection`, the
  `*Forward` wrappers, and the production and test-farm messages. Nothing about
  them has been observed on the wire.
- **`write_preset()` is a trap, kept as a primitive.** It sends a whole
  `BinaryPreset` as a `Grid` `UPDATE`, which the unit applies only for
  row/column-keyed elements. A recalled preset carries no explicit `row`, so
  writing it back does nothing. New edit operations follow the keyed pattern.

## Adding a device profile

The protocol carries no version number, so nothing tells you at runtime that a
firmware update changed a message. A new CorOS release, or a new model, is a new
`QuadCortex` subclass in `pyquadcortex/protocol/profiles.py`, not a change to the
existing one (ADR-0020). `connect()` resolves `(device_type, zenos_git_hash)` in
the registry before the handshake and refuses an unknown pair. Name the profile by
the CorOS version, never by `app_fw`: a contributor reports `d14e` on both 4.0.1
and 4.1.0 (PR #44).

On the unit it covers:

1. **Generate the snapshot.** `scripts/generate_models.py --snapshot coros_x_y_z`
   and the params and options generators, against the new unit. Bind the three
   modules on the new class.
2. **Run the suite.** `pytest tests/hardware --hardware --profile YourClass`.
   `--profile` connects as that class instead of the one the unit resolves to, so
   the suite runs on a unit the registry would refuse. The suite connects with
   `Support.EXPERIMENTAL`, so nothing refuses before it is measured.
3. **Fill `VERIFIED`.** The report at the end of the run names the operations
   that passed. Put them in the class's `VERIFIED` set. `MEASURED_ON` lists the
   exact `zenos_git_hash` strings a suite run has covered; a patch release is
   added after a run confirms it.
4. **Record differences beside the 4.0.1 record.** Anything that behaved
   differently is written into `protocol.md` next to the existing entry, dated and
   named, and overridden on the new class.

Step 1 is the only one of these that needs the unit twice. Each generator also takes
`--payload`, a saved `ModelRepo` reply, so a snapshot can be rebuilt afterwards
with nothing attached:

    python scripts/generate_options.py --snapshot coros_4_0_1 \
        --payload tests/fixtures/catalog/model_repo_coros_4_0_1.bin

Save the new profile's payload beside that one with its provenance record, and
add a row to `PAYLOADS` in `tests/test_catalog_payload.py`, which holds each
committed payload to the snapshot it generates (ADR-0022).

When measuring a new CorOS release, check these in order:

1. **The schema.** Re-recover the `.proto` files from the matching Cortex
   Control build and diff them against `protocol/proto/`. Field numbers and enum
   values are what the wire depends on. Regenerate the bindings and bump the pin
   as above.
2. **The framing.** `tests/test_framing.py` asserts the 4.0.1 envelope against
   captured frames. If reports stop reassembling, suspect the `len`/`flags`
   layout or the 8-byte trailer first.
3. **The connect handshake.** The most likely thing to break, because it is
   behavioural. The unit gates its pushes on receiving a valid
   `cortex_control_version` (`QuadCortex.CC_VERSION`, `"4.0.1"`) and on a
   `ModelRepo` read. If pushes stop flowing, re-check the whole burst
   (`_hello()` and `_SUBSCRIBE_TYPES`) against a current Cortex Control session.
4. **The write stall.** The transport assumes every HID write "fails" and
   succeeded anyway. If writes start failing for real, the symptom is `request()`
   timeouts, not write errors.
5. **The edit path.** That a `File` `CREATE` snapshots the grid and ignores
   `preset_payload`, and that `Grid` updates apply by `row`/`column` key, are
   behavioural findings. Re-run the recall, edit, save flow
   (`examples/reroute_and_save.py`) and read the result back.
6. **Record what you re-verified**, with the CorOS version, in the coverage
   table in [`protocol.md`](protocol.md#operation-coverage).
