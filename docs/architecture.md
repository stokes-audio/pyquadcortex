# Architecture and contributor guide

This document is for someone who wants to add support for a Quad Cortex feature
`pyquadcortex` does not implement yet, or to adapt the library to a newer CorOS
release, and does not know where to start. Read it before writing code: the
layering is deliberate, and the recipe in
[How to add a new operation](#how-to-add-a-new-operation) is short if you follow
the layers and long if you fight them.

For the wire protocol itself (frame layout, handshake, per-operation message
shapes), see [`protocol.md`](protocol.md). This document covers the code.

> `pyquadcortex` is unofficial and not affiliated with Neural DSP. It speaks the
> device's own Protobuf control protocol, re-implemented from the recovered
> schema in `protocol/proto/` and from observing real Cortex Control sessions
> against CorOS / Cortex Control **4.0.1** and device firmware **d14e**.

## Contents

- [Layer map](#layer-map)
- [What flows through the layers](#what-flows-through-the-layers)
- [send vs request vs await_broadcast](#send-vs-request-vs-await_broadcast)
- [How to add a new operation](#how-to-add-a-new-operation)
- [The generated protobuf bindings](#the-generated-protobuf-bindings)
- [Capturing the device's traffic](#capturing-the-devices-traffic)
- [Testing philosophy](#testing-philosophy)
- [What is not implemented yet](#what-is-not-implemented-yet)
- [Adapting to a new CorOS version](#adapting-to-a-new-coros-version)

## Layer map

The package has two public namespaces (ADR-0006). `pyquadcortex` is the model of
the unit; `pyquadcortex.protocol` is the message-level API everything below the
model is built from. Each file owns exactly one concern, and each layer knows
only about the layer directly below it.

```
    pyquadcortex/            THE MODEL - what import pyquadcortex hands back
      model/device.py        connect(): opens the unit, returns a Device.
      |                      Speaks the unit's vocabulary, never the wire.
      |                      (Directory, cache and grid land in later stories.)
      |
      |                      -- the model/protocol seam --
      |
    pyquadcortex/protocol/   THE PROTOCOL LAYER - one call per protocol message
      cli.py                 argparse subcommands -> client methods
      |
      session.py             connect(): find + open the HID device, start the
      |                      transport, run the connect handshake, hand back a
      |                      client
      |
      client.py              QuadCortex: the message-level API. Builds protobuf
      |                      messages. Knows NOTHING about HID, reports, or
      |                      framing.
      |
      transport.py           Framed I/O over an hidapi-like device: write
      |                      reports, RX thread + reassembly, request/response
      |                      and broadcast correlation, keepalive thread.
      |
      registry.py            CortexMessageType enum integer <-> generated
      |                      protobuf class
      |
      framing.py             HID frame codec: logical (message_type,
      |                      protobuf_bytes) <-> raw 129-byte HID reports.
      |                      Pure bytes and ints.
      |
    [ hidapi / the device ]

      proto/                 Generated bindings (committed; see below)
      enums.py               Named port / instrument / setlist-path values
      hid_ids.py             Vendor and product IDs, interface number

    pyquadcortex/_version.py The version string, read by both namespaces and by
                             pyproject.toml
```

The model calls the protocol layer and never the other way round: nothing under
`pyquadcortex/protocol/` may import from `pyquadcortex/model/`. A caller can use
either namespace, or both - `Device.from_client(qc)` puts a model on a protocol
connection that is already open.

### framing.py

A pure codec. It converts a logical message `(message_type: int, payload:
bytes)` into a list of 129-byte HID reports and back, and answers "is this list
of reports a complete message yet?". No hidapi, no protobuf, no threads, no I/O
whatsoever. `message_type` is just an integer here, which is why this module can
be tested against real captured frames byte for byte
(`tests/fixtures/frames/*.json`).

Public surface: `encode_message`, `decode_reports`, `is_complete`, and the
confirmed wire constants (`REPORT_SIZE`, `CHUNK_SIZE`, `TRAILER_SIZE`,
`FLAG_FIRST`, `FLAG_LAST`, the two report IDs).

### transport.py

`Transport` wraps any object with hidapi's `write(report)` / `read(size,
timeout_ms)` / `close()` shape. It owns everything time-dependent and
concurrent:

- outbound: frames a message via `framing`, writes its reports as an atomic
  group under a write lock (a keepalive must never interleave its report between
  a multi-report message's fragments, because continuation reports carry no
  header);
- inbound: a daemon RX thread reads reports, reassembles them by frame flags,
  gunzips frame-level compressed payloads, parses the protobuf, and dispatches;
- correlation: `request()` waiters keyed by `request_id`, plus
  `await_broadcast()` waiters keyed by message class and an optional predicate;
- persistent subscriptions: `add_listener()` registers a callable that sees every
  decoded message for as long as the connection lasts, including the unsolicited
  pushes no waiter is expecting. It consumes nothing, so waiters and collectors
  behave exactly as they do with no listener registered;
- a keepalive thread;
- tolerating the device's benign write STALL (see
  [protocol.md](protocol.md#the-benign-write-stall)): write errors are logged at
  debug and swallowed, and a genuinely dead device is detected by `request()`
  timeouts instead.

The RX thread must never die. Every decode/parse is wrapped, unknown message
types and non-protobuf pushes are skipped at debug level, and the reassembly
buffer is reset on anything malformed so one bad frame cannot wedge the stream.
If you add code to the RX path, preserve that property.

Listeners run on that thread, so the same rule covers them: one that raises is
logged and skipped, its peers still get the message, and the message still
reaches its waiter. A listener may also not read from the device -
`request`, `await_broadcast` and `collect` raise `RuntimeError` when called from
the RX thread, because the RX thread is the one that would have to deliver the
answer, so such a call could only ever time out with the read loop stopped behind
it. A listener applies what a push carries and notes what needs re-reading; the
caller's thread does the re-reading (see [domain-model.md](domain-model.md)
section 9, and ADR-0009).

### registry.py

The only place that knows the mapping between the schema's
`CortexMessageType.Enum` integers and the generated `*Message` classes.
`_BY_NAME` maps enum names to classes; the two lookup helpers are
`type_for(cls)` and `class_for(message_type)`. A message type absent from
`_BY_NAME` cannot be sent (`type_for` raises `KeyError`) and inbound frames of
that type are dropped as undecodable.

### client.py

`QuadCortex` is the public API. It builds protobuf messages and calls
`send` / `request` / `await_broadcast` / `next_request_id` on whatever transport
object was injected into its constructor. It deliberately imports no hidapi and
never touches a report, a frame, or a byte offset.

**Why this split matters:** because `QuadCortex` only depends on four transport
methods, the whole high-level API is testable with a ~20-line fake (see
`tests/test_client.py`), with no device, no `hid` import, and no timing. Every
wire concern (report size, fragment flags, the trailer, the write stall, thread
safety, timeouts) stays below this line. When you add an operation, the protobuf
building belongs here and nothing else does.

Also in this module: `slot_to_position("28C") -> 218` and
`input_chain_rows(preset, port)`, two pure helpers with no transport dependency.

### session.py

`protocol.connect()` is the protocol layer's front door: `open_device()` finds
and opens the HID interface, a `Transport` is started around it,
`QuadCortex._hello()` runs the connect handshake, and the returned client is
ready for commands. The client
remembers what it opened (`_owned_resources`) so `close()` and the context
manager tear down only what `connect()` created. A client built around a
caller-supplied transport owns nothing and `close()` is a no-op.

`connect(before_handshake=...)` is the hook for anything that has to be watching
before the handshake runs. The subscription burst the handshake sends is what
makes the unit start pushing state, and that state arrives AFTER `connect()` has
returned (measured: the client comes back at 2 s, the ModelRepo lands at 4.9 s and
the current preset at 10.1 s - see [protocol.md](protocol.md), "Connect burst,
measured"). So a listener registered on the returned client has already missed it;
one registered through this hook has not.

`import hid` lives *inside* `open_device()`. That laziness is a contract, not an
accident: see [Testing philosophy](#testing-philosophy).

### cli.py

`qcctl`. `build_parser()` must stay import-safe and device-free. `main()` does
the device work; the `version` subcommand deliberately bypasses the handshake
(`_open_unconnected()`) because a plain `Version` READ works without the connect
gate, and the handshake's own version announce would race that READ's reply.

`pyproject.toml` declares the console script as
`pyquadcortex.protocol.cli:main`; `qcctl` itself is unchanged.

### model/device.py

`pyquadcortex.connect()` opens the unit through `protocol.connect()` and returns
a `Device`, which carries the unit's identity and owns the connection.
`Device.from_client(qc)` wraps a protocol connection the caller already has, and
does NOT take ownership of it. `Device.client` is the way back down to the
message level for anything the model does not cover yet.

The rest of the model - the Directory, the write-through cache, the loaded preset
and the grid - is designed in [domain-model.md](domain-model.md) and is being
built story by story. Nothing is stubbed out to look finished.

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

A device message, bottom to top:

```
device.read() -> one 129-byte input report
  -> RX thread appends to the reassembly buffer
  -> framing.is_complete(buffer)?  (flag-driven; no length field exists)
  -> framing.decode_reports(buffer) -> (message_type, payload)
  -> gunzip payload if it starts 1f 8b
  -> registry.class_for(message_type) -> parse
  -> _dispatch: every listener, then collectors, then a request_id waiter,
     else a broadcast waiter, else dropped
```

## send vs request vs await_broadcast

Choosing correctly is most of the work of adding an operation. The first three
rows serve ONE exchange, which is what an operation needs. The last one is not an
operation at all: it is how a long-lived caller watches the link.

| Transport method | Use when | Blocking | Correlation |
|---|---|---|---|
| `send(msg)` | The device acts on the message and you do not need its answer: scene switch, grid edits, recall, keepalive. | No | None |
| `request(msg, timeout=)` | The device answers a message of the **same type**: `Version` READ, `ResetCommsBuffers`, the `File` mutations. | Yes | Fresh `request_id` is assigned and registered before the write. Reply is the first inbound message of the same type whose `request_id`, if present on both sides, matches. |
| `await_broadcast(cls, trigger, timeout=, match=)` | The answer arrives as a **push of a different type**, or as an unsolicited broadcast the device emits in response to an action: the `RecallPreset` push that carries a full preset, the `File` folder listings. | Yes | By message class, plus your optional `match` predicate. A right-type message the predicate rejects is left undelivered so a later one can satisfy the waiter. |
| `add_listener(fn)` | You want EVERY message for the life of the connection, not the answer to one call: a cache fed by the unit's own pushes, or a log of the link. | No, but `fn` runs on the RX thread | None. Every message, every type, whether or not a waiter also gets it. Removed with the returned callable or `remove_listener(fn)`. |

Two gotchas the current code already encodes, and that new operations must
respect:

- **READ replies carry no `request_id`.** `_dispatch` falls back to "first
  waiter of the same type wins" for those, which is why two concurrent READs of
  the same type cannot be disambiguated. This is exactly why `_hello()` does not
  issue its own `Version` READ (it would race a caller's).
- **A state-changing request triggers a cascade of other-type messages that all
  echo its `request_id`.** Correlation is therefore by type first, id second.
  If you need one specific push out of a cascade, use `await_broadcast` with a
  `match` predicate on the id, the way `read_preset` does to avoid returning a
  stale or seed push.

## How to add a new operation

Worked example: suppose you want `set_global_tempo(bpm)`.

**1. Find the message in the schema.** The recovered schema lives in
`protocol/proto/ProductionAutomation.proto` (control messages, ~45 KB) and
`protocol/proto/Preset.proto` (the `BinaryPreset` grid model). Start from the
`CortexMessageType.Enum` block at the top of `ProductionAutomation.proto`: it
lists all 71 message types with their wire integers. Find the type name
(`GlobalTempo = 33`), then find `message GlobalTempoMessage` and read its
fields. Note that nearly every scalar field is wrapped in a synthetic
`oneof _field`, i.e. proto3 `optional`, so `HasField()` distinguishes "set to
zero" from "not set" and the device can tell a real value from a default.

**2. Register the type if it is not already there.** Add
`"GlobalTempo": pa.GlobalTempoMessage` to `_BY_NAME` in `registry.py`. Without
this, `transport.send` raises `KeyError` and inbound frames of that type are
silently dropped. Many types are already registered but have no client method;
check first.

**3. Add a method to `QuadCortex`.** Build the protobuf and hand it to the
transport. Keep it thin: no HID, no bytes, no sleeps.

```python
def set_global_tempo(self, bpm: float):
    """Set the device's global tempo."""
    msg = pa.GlobalTempoMessage(action=pa.MessageAction.UPDATE)
    msg.tempo = bpm
    return self._t.send(msg)
```

Pick the transport method from the table above. If the device answers with a
different message type, use `await_broadcast` with a `trigger` closure, as
`read_preset` and `list_presets` do. Document in the docstring what is
confirmed on hardware and what is inferred from the schema; the existing
docstrings are the project's record of protocol facts, so state your evidence.

**4. Add named constants if the field is an enum.** Device-side enums that a
caller has to pass belong in `enums.py` (`Input`, `Output`, `Instrument`,
`Setlist`), mirroring the schema's own names and values. Do not invent values;
copy them from the schema, and mark in a comment which ones were actually
confirmed on hardware.

**5. Write offline tests.** `tests/test_client.py` has the pattern: a
`FakeTransport` that records `sent` messages, returns canned responses from
`request`, and replays a `broadcast` from `await_broadcast` while capturing the
`match` predicate you passed. A good test asserts the exact wire shape:

```python
def test_set_global_tempo_sends_a_global_tempo_update():
    fake = FakeTransport()
    client.QuadCortex(fake).set_global_tempo(120)
    (msg,) = fake.sent
    assert isinstance(msg, pa.GlobalTempoMessage)
    assert msg.action == pa.MessageAction.UPDATE
    assert msg.tempo == 120
```

If your change touches framing or the transport instead, use
`tests/test_framing.py` (real captured frames as golden fixtures) and
`tests/test_transport.py` (an in-memory `FakeHid` that frames its own replies,
so reassembly and correlation are exercised for real). No test may import `hid`
or need hardware.

**6. Verify on hardware.** Offline tests prove you built the message you
intended; only the device proves the message is the right one. Connect over USB,
quit Cortex Control first (it opens the interface exclusively, so nothing else
can open the device while it runs), and check the effect two ways where you can:
read the state back over the protocol, and look at the unit's screen. Then
record the result: update your docstring and the coverage table in
[`protocol.md`](protocol.md#operation-coverage) with what was verified and how.
An operation whose shape comes only from the schema should say so.

Useful shapes for hardware work live in `examples/` (`switch_scenes.py`,
`list_presets.py`, `reroute_and_save.py`). `scripts/compile_protos.sh` is the
only script in the repo.

## Capturing the device's traffic

If the operation you want is not documented in [protocol.md](protocol.md), or a write
you believe is correct has no effect, do not keep guessing shapes. The device
broadcasts what it does, so perform the action on the unit and read what arrives, then
replay it.

**[capture.md](capture.md)** has the listener, the pitfalls that decide whether a
capture is interpretable, and how to compare what you get against what the library
sends. This is the most reliable tool here, because a write the device does not
understand is accepted and ignored rather than rejected - there is no error to work
from.

## The generated protobuf bindings

`pyquadcortex/protocol/proto/ProductionAutomation_pb2.py` and `Preset_pb2.py` are
**generated code that is deliberately committed to git**. That is unusual, and
it is on purpose:

- `pip install pyquadcortex` then needs **no protoc toolchain and no build
  step**. A user gets a working wheel with only `hid` and `protobuf` as runtime
  dependencies.
- The wheel stays self-contained: the bindings are inside the package, not
  produced at install time, so there is nothing to go wrong on a user's machine
  and nothing platform-specific to get wrong.
- CI installs the package and runs the suite without a protobuf compiler.

**Do not add `pyquadcortex/protocol/proto/*_pb2.py` to `.gitignore`, and do not
delete them as "build output".** Doing so breaks installs from PyPI and from a
plain checkout.

`pyquadcortex/protocol/proto/__init__.py` is load-bearing: protoc emits absolute
sibling imports (`ProductionAutomation_pb2` does `import Preset_pb2`), which fail
inside a package, so `__init__.py` appends its own directory to `sys.path`. That
is what lets unmodified protoc output keep working after a regeneration.

### Regenerating

```bash
scripts/compile_protos.sh
```

It prefers the version-matched generator from the dev extra
(`grpcio-tools`, hence `.venv/bin/python -m grpc_tools.protoc`) and falls back
to a system `protoc`. It generates into a temporary directory first and copies
into `pyquadcortex/protocol/proto/` only after the gencode check below passes,
so a refusal leaves the tree untouched.

**The runtime pin must match the gencode version.** The protobuf runtime
validates at import time that `runtime >= gencode` (see the
`_runtime_version.ValidateProtobufRuntimeVersion(...)` call at the top of each
generated file). The committed bindings were generated with **protobuf 7.35.1**,
which is why `pyproject.toml` pins `protobuf>=7.35.1,<8`. If you regenerate with
a newer generator, bump that lower bound to the new gencode version in the same
commit; if you cross a major version, bump the upper bound too. A mismatch is a
hard `ImportError` for every user, not a warning.

**The generator floor moves with it.** `grpcio-tools` bundles its own protoc, so
whichever version is installed is what decides the gencode. That makes an *older*
generator the quiet failure: `runtime >= gencode` is still satisfied, so bindings
regenerated backwards import fine and pass every test while the pin no longer
describes them. `pyproject.toml`'s dev extra therefore floors `grpcio-tools` at
the oldest release whose protoc emits the committed gencode - `>=1.83.0` for
gencode 7.35.1 - and that floor is raised in the same commit as any gencode bump.

The floor cannot be read off package metadata. `grpcio-tools` releases do not
track `protobuf` releases, and the declared dependency is a runtime floor rather
than the gencode stamp: 1.82.1 requires `protobuf>=7.35.1` and still emits
gencode 7.35.0. Find the floor by running candidates and reading the stamp:

```bash
printf 'syntax = "proto3";\nmessage Ping { int32 n = 1; }\n' > /tmp/ping.proto
python -m grpc_tools.protoc -I /tmp --python_out=/tmp /tmp/ping.proto
grep "Protobuf Python Version" /tmp/ping_pb2.py
```

Two guards keep this honest, and they cover different routes (ADR-0008):

| Guard | Catches | When |
|---|---|---|
| `scripts/compile_protos.sh` | a generator that would write older gencode than what is committed - it refuses and writes nothing | at regeneration, before the tree changes |
| `tests/test_packaging.py` | committed gencode that disagrees with itself or with the pin, however it got there | every PR, no protoc needed |

Commit regenerated bindings together with the `.proto` change, the pyproject
pin and the generator floor, so the tree is never internally inconsistent.

## Testing philosophy

The suite is **fully offline**. No Quad Cortex, no USB, no `hid` import, and on
macOS no `DYLD_LIBRARY_PATH` prefix. That is what makes almost all development
possible with no hardware attached, and it is what lets CI run the real suite on
plain Linux runners.

How each layer is faked:

| Layer | Test double | File |
|---|---|---|
| `framing` | none needed (pure functions) plus real captured frames as golden fixtures | `tests/test_framing.py`, `tests/fixtures/frames/` |
| `transport` | `FakeHid`: an in-memory hidapi stand-in that frames its own `Version` replies, so reassembly, multi-report messages, and correlation run for real | `tests/test_transport.py` |
| `client` | `FakeTransport`: records `sent`, returns canned `request` responses, replays a `broadcast` and captures the `match` predicate | `tests/test_client.py` |
| `session` | `open_device` and `Transport` monkeypatched | `tests/test_session.py` |
| `cli` | `build_parser()` exercised directly | `tests/test_cli.py` |
| `model` | `FakeClient`: answers the calls the model makes on a `QuadCortex`, plus the same monkeypatched device+transport as `session` | `tests/test_device.py` |
| schema | asserts the enum integers the code relies on and that core messages instantiate | `tests/test_schema_compiles.py` |
| namespaces | the pre-flip `__all__`, read verbatim from git, must all resolve under `pyquadcortex.protocol` | `tests/test_namespace.py` |

### The import-safety contract

**`import pyquadcortex` and `qcctl --help` must never require hidapi.**

Concretely:

- `import hid` appears exactly once, lazily, inside `session.open_device()`.
  Nothing at module scope anywhere in the package may import it.
- `pyquadcortex/__init__.py` may keep importing the model and the whole protocol
  surface, because none of those import `hid` at module scope.
- `cli.build_parser()` must construct no transport and open no device; `main()`
  imports `session` inside the function body.
- `tests/test_import_cleanliness.py` walks every module in the package and
  imports each one in a subprocess, so a new module in either namespace is
  covered the day it is added.

Why it matters: the `hid` package is a ctypes binding that needs the native
hidapi library present, which is an OS-level install (`brew install hidapi`,
`apt install libhidapi-hidraw0`) and, on macOS, usually also a
`DYLD_LIBRARY_PATH` prefix. If any of that were required at import time, then
`--help`, `pip check`, CI, and the whole test suite would fail on machines
without hidapi, and every test would need the dyld prefix. Keeping the import
lazy also gives a good error message in one place: `open_device()` raises
`DeviceNotFoundError` distinguishing "hidapi missing" from "device not
openable".

If you add a module that needs `hid`, import it inside the function that opens a
device, and add a test that the new module imports cleanly without hidapi.

## What is not implemented yet

For where the library is *meant* to go - in particular an object model of the
device that would absorb the protocol quirks listed here rather than documenting
them - see [roadmap.md](roadmap.md).

Being honest about the gaps is more useful than a feature list. Places to look
next, roughly in order of how well the ground is prepared:

- **Registered but unwrapped message types.** `registry.py` registers around
  three dozen types so the RX thread can decode device chatter, but `client.py`
  exposes methods for only about fifteen operations. `IOSettings`,
  `GeneralSettings`, `GlobalEQ`, `MasterVolume`, `Mode`,
  `RecentsFavorites`, `PresetDirty`, `Updater`, `ModelRepo` and others are
  decoded and pushed to us but have no API. These are the cheapest additions:
  the type already exists in the registry, so it is one client method plus
  tests. (`GlobalTempo` is a special case: it is global rather than per preset and
  only ever returned a running clock, so the useful per-preset tempo controls live
  in `tempoProgramData` instead - see `set_tempo_param`.)
- **Types not in the registry at all.** The schema declares 71 message types.
  Whole feature areas are untouched: `Tuner` / `ShowTuner`, `Looper`,
  `MIDISettings`, `NeuralCapture` / `NeuralCapture2`, `Screenshot`,
  `Diagnostics`, `LocalBackup` / `CloudBackup`, `Confirmation`,
  `GigViewButton`, `SuspendConnection`, `GenericError`, the `*Forward` transport
  wrappers, and the production/test-farm messages. Nothing about these has been
  observed on the wire by this project, so treat the schema as a starting
  hypothesis and verify.
- **`copy_scene` is the one message whose shape did not come from Cortex Control's
  traffic**, because Cortex Control cannot copy a scene at all. It was read off the
  device's own broadcast when a scene was copied on the unit, and is now fully
  verified on hardware (see [protocol.md](protocol.md#74-scenes)). Nothing is
  outstanding; noted only so the different provenance is not a surprise.
- **`GridMove`** is registered and its captured shape is documented in
  [protocol.md](protocol.md#grid-block-move), but there is no client method for
  moving a block between grid positions.
- **`write_preset()` is a trap, kept as a primitive.** It sends a whole
  `BinaryPreset` as a `Grid` UPDATE, which the device applies only for
  row/column-keyed elements. A recalled preset carries no explicit `row`, so
  writing it back wholesale does nothing. Any new edit operation should follow
  the keyed pattern (`set_chain_input` / `set_param` / `set_bypass`), not extend
  the wholesale path.
- **The splitter accepts no host writes.** `chain.mixer[]` is writable with the
  ordinary row-keyed shape, but `chain.splitter[]` is not: four shapes were tried
  and each saved and read back unchanged, so `set_splitter_param()` raises rather
  than pretend. It has NOT been confirmed by capture that the device stays silent
  when a splitter is edited on the unit, so this is "no known write path" rather
  than "impossible" - and reading that broadcast is the obvious way to settle it.
- **Splitter and mixer positions cannot be read.** Neither carries `column`, so
  where a split sits on the grid is unknowable from a recall and can only be
  inferred. Grid topology is therefore only partly recoverable, which limits
  anything that tries to reconstruct a preset's shape.
- **`enums.Output` is still schema-derived, though better anchored now.** Eight
  further ids were written and read back verbatim, which also established that the
  device does NOT validate: a meaningless id is stored, not rejected. What no
  read-back can tell you is which ids reach a physical jack, so that part remains
  inference (see [protocol.md](protocol.md#output-ports-chainout_portid)).
- **Two envelope bytes remain unexplained** (the device-filled trailer bytes),
  and the "raw payload" trailer flag is an inference. Neither blocks anything,
  but do not write code that depends on them.

## Adapting to a new CorOS version

**The protocol carries no version number.** There is no capability negotiation
and no schema version on the wire, so nothing tells you at runtime that a
firmware update changed a message. Assume nothing survives a major update until
you re-check it.

When moving to a new CorOS / Cortex Control release:

1. **Re-recover and re-diff the schema.** The `.proto` files in
   `protocol/proto/` were recovered from the Cortex Control application for
   4.0.1. Field numbers and enum values are what the wire format actually
   depends on, so diff a freshly recovered schema against the committed one and
   look for renumbered fields, renumbered `CortexMessageType.Enum` values (the
   type tag in every frame), and changed enum members for ports and
   instruments. Regenerate the bindings and bump the protobuf pin as described
   above.
2. **Re-verify the framing.** `tests/test_framing.py` asserts the 4.0.1
   envelope against real captured frames. If reports stop reassembling, suspect
   the `len`/`flags` layout or the 8-byte trailer before anything higher up.
3. **Re-verify the connect handshake.** This is the most likely thing to break,
   because it is behavioural rather than structural. `QuadCortex.CC_VERSION` is
   the Cortex Control version string the client announces
   (`Version{action: UPDATE, cortex_control_version: "4.0.1"}`), and the device
   **gates its push behaviour on receiving a valid version**: without it, the
   device answers direct requests but pushes no state, so `read_preset` and the
   live-sync broadcasts go quiet. If a newer device rejects `"4.0.1"`, update
   `CC_VERSION` to what the matching Cortex Control build announces. The
   `ModelRepo` READ in the handshake is also empirically required, apparently as
   a readiness gate; if pushes stop flowing, re-check the whole burst
   (`_hello()` and `_SUBSCRIBE_TYPES`) against a current session rather than
   tweaking one step.
4. **Re-check the write stall.** The transport assumes every HID write "fails"
   and succeeded anyway. If a future firmware stops stalling, nothing breaks
   (errors are only swallowed, never required). But if writes start failing *for
   real*, the symptom will be `request()` timeouts, not write errors, so debug
   from the timeout end.
5. **Re-verify the edit path and the file-operation semantics.** That a `File`
   CREATE snapshots the grid and ignores `preset_payload`, and that `Grid`
   UPDATEs are applied by `row`/`column` key, are behavioural findings, not
   schema facts. Re-run the recall-edit-save flow (`examples/reroute_and_save.py`
   is the smallest end-to-end check) and read the result back.
6. **Record what you re-verified**, with the CorOS version and firmware build,
   in the coverage table in [`protocol.md`](protocol.md#operation-coverage). The
   value of that table is that every row says how it was checked.
