# Capturing the unit's own traffic

> Purpose: how to observe what the unit sends, and how to read a capture without fooling yourself.

The Quad Cortex announces what it does. When someone operates the touchscreen,
the unit sends the host the same messages a client would send to cause that
change. So the reliable way to learn an operation's wire shape is to perform it
on the unit and read what arrives.

Reach for this when an operation is undocumented, or when a write you believe is
correct has no effect. Guessing is unreliable because a write the unit does not
understand is accepted and ignored. There is no error to learn from (see
[the benign write stall](protocol.md#the-benign-write-stall)).

## The listener

Subscribe with `add_listener` and record everything, then perform the action on
the unit. A listener sees every decoded message for the life of the connection
and consumes nothing (ADR-0009).

Two rules come with it, both enforced: a listener must not block, because it
runs on the RX thread, and it may not read from the unit. `request`,
`await_broadcast` and `collect` raise if called from that thread. Record what
arrives and do the reading from your own thread.

```python
import threading, time
from pyquadcortex import protocol
from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa

# Chatter that arrives constantly. On CorOS 4.0.1 GlobalTempoMessage is the only
# heavy one, about 60 every 15 seconds. Count arrivals by type first and filter
# from what you see, not from this list.
#
# Filter the SHAPE, never the whole type. GlobalTempoMessage alternates a
# running clock with a 25-parameter shape, and the parameters are real state:
# parameter 1 is the Tempo menu's MODE switch. Drop the clock shape only:
#     m.HasField("metronome_status") and not m.params
#
# This is a noise list, not an allow-list. Pair it with a heartbeat (below).
NOISE = {"GlobalTempoMessage", "IOMeterMessage", "GridModelMeterMessage",
         "KeepAliveMessage", "ModuleStatsMessage"}

seen, lock = [], threading.Lock()

def tap(message):
    name = type(message).__name__
    if name not in NOISE:
        with lock:
            seen.append((name, str(message).replace("\n", " ")))

with protocol.connect() as qc:
    remove = qc.add_listener(tap)
    try:
        time.sleep(120)      # perform the action on the unit during this window
    finally:
        remove()

with lock:
    for name, body in seen:
        print(f"{name}: {body[:400]}")
```

## Four ways your instrument lies about silence

Each of these produced a wrong conclusion in this project, and all four look
the same from outside: the unit appears not to answer. Each time the unit was
behaving correctly.

A useful rule: a flaky negative is usually the unit, because reads here are lazy
and the first request after connecting is often dropped. A perfectly consistent
negative is often the observer, because a filter that rejects the answer rejects
it every time. The cleaner your negative result, the harder you should look at
the tool.

**1. An unregistered message type is discarded before you see it.** The RX path
decodes by type and drops what it cannot decode, so a listener is blind to every
type the registry lacks. "New Neural Capture" appeared to produce nothing on the
wire; with the type registered, the same tap showed
`NeuralCapture{try_to_show_dialog: true}`. The library decodes 70 of the unit's
72 types for this reason. If you filter by type in your own listener, make it a
noise list rather than an allow-list.

**2. Filtering chatter makes a dead link look like a quiet one.** `GlobalTempo`
arrives constantly, so it is the first thing anyone filters out, and then an
empty log is indistinguishable from a USB link that died mid-session (see
[troubleshooting.md](troubleshooting.md)). Write a heartbeat that counts the
chatter you suppressed:

```python
# every 15 seconds
LOG.write(f"-- heartbeat: {suppressed} chatter msgs, "
          f"{'ALIVE' if quiet < 10 else 'LINK MAY BE DEAD'}\n")
```

A silent log with a beating heart is a finding. A silent log without one is
nothing.

A listener proves only that the unit does not announce something. The Tempo
menu's `MODE` switch was recorded for eight releases as "not on the wire" on the
strength of three listening runs. It was on the wire the whole time, in the
`GlobalTempo` params push those runs were filtering out. So a noise filter is a
claim that a message type cannot carry the answer, and it needs checking. When a
listener comes back silent, do not reach for a longer window. Ask, and diff what
comes back (next section).

**3. A predicate that tests a field the reply never sets rejects every valid
answer.** Reading the Favorites list needs `RecentsFavorites{READ, is_favorites: true}`, and the reply comes back with `is_favorites` absent: the flag selects
which list you get and is not repeated in the answer. Waiting with

```python
match=lambda m: bool(m.is_favorites) == want    # discards the correct reply
```

timed out cleanly every time, and "Favorites cannot be read over USB" went into
the documentation. Correlate on `request_id`, which the unit does echo, and when a
predicate times out, log what did arrive before concluding nothing did.

**4. A listener you never registered records nothing, and nothing looks like
silence.** The listener above is attached after `connect()` returns, which is too
late for the connect handshake's own burst. Catching the burst means subscribing
through `protocol.connect(before_handshake=...)`, and that hook is called with
the started transport:

```python
protocol.connect(before_handshake=tap)                                    # WRONG
protocol.connect(before_handshake=lambda t: t.add_listener(tap))          # right
```

The wrong form raises nothing. `tap` is called once, with the `Transport` as its
"message", and never again. Measured 2026-09-09: one recorded message in 35
seconds on a healthy link that had just delivered a 399-message folder listing.

The heartbeat from point 2 does not save you here if it counts what the recorder
holds: one spurious entry satisfies it. Count something you know the unit sends
unprompted, such as the tempo stream. What does catch it is a positive control:
an arm of the experiment where you already know what the unit should say. Run
that arm first. `tests/hardware/conftest.py` has the correct registration.

## Diff the whole state, do not hunt for a field

The listener answers "what does the unit say when I do this?". When the answer
is "nothing", the next instrument answers a different question: "what does the
unit's answer look like in each position?" Capture everything readable with the
control one way, have the operator move it, capture again, and diff.

The discipline is refusing to look for the field you expect. `MODE` had been
hunted for in `GeneralSettings` and in the preset, and it was one index away
inside a message shape the search had written off. A diff finds it without
knowing where to look.

`tests/hardware/state_snapshot.py` is the harness. Four things in it matter, each
because of a way this kind of capture can lie:

- **Record every set field, flattened to `path -> value`.** `ListFields()` is
  the presence-correct reading of this schema, so an absent field shows in the
  diff as a key appearing, not as a zero that could mean either thing.
- **Record field numbers the schema does not know.** The schema is recovered from
  one Cortex Control build, so a field the firmware sends and that build never had
  decodes to nothing. Use `google.protobuf.unknown_fields.UnknownFieldSet`; the
  upb runtime raises `NotImplementedError` on `msg.UnknownFields()`.
- **Collect values as a set per path over a window, not one sample.**
  `GlobalTempo` alternates two shapes; one sample per type compares a clock reply
  against a params reply and reports the difference as real.
- **Label noise, never filter it.** Clocks, meters and request ids move on their
  own and are printed under their own heading.

ADR-0010 makes this capture the step that happens before a control is recorded
as having no wire path. Prove the instrument offline first:
`tests/test_state_snapshot.py` feeds it a message carrying each thing it must not
miss and fails if the snapshot comes back empty. Expect a large diff: the connect
burst's `File` enumeration arrives in a different order every run.

## Check a believed polarity against factory content

When you think you know which way a boolean goes, ask what every factory preset
holds there and whether the whole factory library would behave absurdly under
your reading. The metronome transport was documented as "1.0 = muted" for two
releases; all 17 factory presets hold 0.0 there with the volume at a normal
level, so under that reading every factory preset would click constantly. None
does. The check is one loop over presets you already have. A mirrored parameter's
name proves linkage, never meaning.

## Five things that make the difference between a result and a wasted hour

- **Record every message type, not the one you expect.** Filter only the noise
  list, and filter after recording if you can.
- **Include a positive control.** Ask for a scene switch as well as the action
  under investigation. If the scene switch appears and the action does not, the
  silence is a finding. If neither appears, the capture is broken.
- **Run the listener as a background process, writing to a file.** A script that
  prints a prompt and then sleeps is useless for coordinating with a person: the
  output reaches them when the process exits. Start it detached and read the log
  afterwards.
- **Have them repeat the action for the whole window.** Overlap is then
  guaranteed rather than negotiated.
- **Ask whether the change needs committing.** Some editors on the unit
  broadcast only when a value is confirmed. If a drag produces nothing, have them
  press the confirm control and watch again. The `HYBRID` mode merge broadcast
  nothing until `OK` was pressed.

## Reading what you get

Compare the captured message against what the library sends for the nearest
operation. Differences that have each mattered at least once:

- **Which field.** Sub-elements of a chain live in separate repeated fields
  (`models`, `splitter`, `combined_splitter`, `mixer`, `output_control`,
  `input_control`), and only some accept writes.
- **Whether a model hash is present.** The unit usually omits it when
  broadcasting a parameter change.
- **Whether a `column` is present.** Grid blocks carry one; per-row elements do
  not.
- **What else is in the message.** An empty sibling element can void the whole
  update, so a message carrying only what it means to change is safest.
- **Parameter indices.** Positional, following the model's own parameter order,
  which for a family of related models may be the unified model's order rather
  than the one a preset reports.

The echo also tells you whether the unit accepted a write. An accepted
`set_block` draws two or three `Grid` echoes naming the cell plus an
`UndoRedoMessage`; one refused for DSP capacity draws neither.

Then replay the captured shape host to device, save, and read it back. A shape is
confirmed once the value survives a save and recall; see
[Operation coverage](protocol.md#operation-coverage).

## Caveats

`_dispatch` and `_t` are private. This is a debugging technique, not an API.

Cortex Control must be quit, since it holds the USB interface exclusively. So you
cannot capture Cortex Control's own traffic this way on macOS. For that you need a
USB analyser at the bus level; the lab repository holds three such captures.
