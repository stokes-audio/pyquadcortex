# Capturing the device's own traffic

The Quad Cortex broadcasts what it does. When someone operates the touchscreen, the
device sends the host the same messages a client would send to cause that change - so
the authoritative way to learn an operation's wire shape is to perform it on the unit
and read what arrives.

This is the technique to reach for when an operation is undocumented here, or when a
write you believe is correct has no effect. It has settled several message shapes that
guessing did not, including scene copy, block removal, and splitter parameters. Guessing
is unreliable because a write the device does not understand is **accepted and ignored**
- there is no error to learn from (see
[the benign write STALL](protocol.md#the-benign-write-stall)).

## The listener

Tap the transport's dispatch and record everything, then perform the action on the unit.

```python
import threading, time
from pyquadcortex import protocol
from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa

# Chatter that arrives constantly and drowns everything else. On the firmware
# measured, GlobalTempoMessage is the only heavy one - about 60 every 15 seconds -
# so start by counting arrivals BY TYPE and filter from what you actually see
# rather than from this list. (CPULoadMessage, for instance, never arrives at
# all, subscribed or not, so filtering it is harmless but pointless.)
#
# WARNING, learned the expensive way: GlobalTempoMessage is heavy because it
# alternates a running clock with a 25-parameter shape, and the PARAMETERS are
# real device state - parameter 1 is the Tempo menu's MODE switch. Filtering the
# whole type discards them. Filter the clock shape, not the type:
#     m.HasField("metronome_status") and not m.params
#
# Note this is a NOISE list, not an allow-list, and pair it with a heartbeat -
# see "Three ways your instrument lies about silence" below.
NOISE = {"GlobalTempoMessage", "IOMeterMessage", "GridModelMeterMessage",
         "KeepAliveMessage", "ModuleStatsMessage"}

seen, lock = [], threading.Lock()

with protocol.connect() as qc:
    transport = qc._t
    original = transport._dispatch

    def tap(message, *args, **kwargs):
        name = type(message).__name__
        if name not in NOISE:
            with lock:
                seen.append((name, str(message).replace("\n", " ")))
        return original(message, *args, **kwargs)

    transport._dispatch = tap
    time.sleep(120)          # perform the action on the unit during this window

with lock:
    for name, body in seen:
        print(f"{name}: {body[:400]}")
```

## Three ways your instrument lies about silence

Every one of these produced a confident wrong conclusion in this project, and all three
look identical from the outside: **the device appears not to answer.** The device was
behaving correctly all three times.

A useful heuristic came out of it. A *flaky* negative is usually the device - reads here are
lazy and the first request after connecting is often dropped. A *perfectly consistent*
negative is often the observer, because a filter or predicate that rejects the answer
rejects it every single time. So the cleaner and more repeatable your negative result, the
harder you should look at the tool before believing it.

**1. A message type you have not registered is DISCARDED before you see it.**

Both of these produced wrong conclusions in this project, and both look exactly like "the
device broadcasts nothing".

The RX path
decodes by type and drops what it cannot decode, so a listener watching decoded traffic is
blind to every unregistered type. "New Neural Capture" appeared to do nothing on the unit
AND produce nothing on the wire; with the type registered, the same tap immediately showed
`NeuralCapture{try_to_show_dialog: true}`. This library now decodes 70 of the device's 72
types for exactly this reason - but if you are filtering by type in your own listener, make
sure the filter is a NOISE list rather than an allow-list.

**2. Filtering chatter makes a dead link look like a quiet one.** The device sends
`GlobalTempo` constantly, so it is the first thing anyone filters out - and then a log with
nothing in it is indistinguishable from a USB link that died mid-session, which does happen
(see [troubleshooting.md](troubleshooting.md)). Write a periodic heartbeat that COUNTS the
chatter you suppressed:

```python
# every 15 seconds
LOG.write(f"-- heartbeat: {suppressed} chatter msgs, "
          f"{'ALIVE' if quiet < 10 else 'LINK MAY BE DEAD'}\n")
```

A silent log with a beating heart is a finding. A silent log without one is nothing at all.
The finding that the Tempo menu's MODE control emits no change event was reached three times:
the first time with neither safeguard, and twice more with both, and only the later two
were worth anything.

The same example carries a second lesson, learned later and the harder way. A listener
proves only what a listener can prove: **that the device does not ANNOUNCE something.** For
eight releases - 0.33.0 through 0.40.0 - that measurement was written up as "MODE is not on
the wire at all", which is a claim about readability that no amount of listening can
support. Say what the instrument measured, not what it implies.

And the ending, which is the point: **MODE was on the wire the whole time** - and worse,
it was in the traffic those very runs were recording. It is the device tempo block's
parameter 1, carried in the params-shaped `GlobalTempo` push, which arrives about twice
per 14 seconds whether or not anyone touches the switch.

Look at the NOISE list above. `GlobalTempoMessage` is first in it, because it is the
heaviest chatter on the link. So the most likely account of all three runs is not that
the device stayed silent, but that **the listener threw the answer away before it reached
the log** - three times, using a filter this very document recommends.

So this one example teaches the whole section, and the sharpest part of it last: **a
noise filter is a claim that a message type cannot carry the answer**, and nobody had
checked it. Filter a SHAPE, never a whole type. And when a listener comes back silent,
do not reach for a longer window - ASK, and diff what comes back. See "Diff the whole
state, do not hunt for a field" below.

**3. A match predicate that tests a field the reply never sets rejects every valid answer.**
Reading the unit's Favorites list needs `RecentsFavorites{READ, is_favorites: true}`, and
the reply comes back with `is_favorites` ABSENT - the flag selects which list you get, it is
not repeated in the answer. Waiting with

```python
match=lambda m: bool(m.is_favorites) == want    # discards the correct reply
```

timed out cleanly and repeatably, and "Favorites cannot be read over USB" went into the
documentation, along with a method that quietly returned the wrong list. Correlate on
`request_id`, which the device does echo, and when a match predicate times out, log what DID
arrive before concluding nothing did.

## Diff the whole state, do not hunt for a field

The listener above answers "what does the device SAY when I do this?". When the answer is
"nothing", the next instrument answers a different question: "what does the device's
ANSWER look like in each position?" Capture everything readable with the control one way,
have the operator move it, capture again, and diff.

The discipline that makes it work is refusing to look for the field you expect. TEMPO
MODE had been hunted for in `GeneralSettings` and in the preset, and it was one index away
inside a message shape the investigation had already written off. A diff finds it without
knowing where to look; a search only finds it where someone guessed.

`tests/hardware/state_snapshot.py` is the harness. Four things in it are load-bearing, and
each is there because of a specific way this kind of capture lies:

- **Record every SET field, flattened to `path -> value`.** `ListFields()` is the
  presence-correct reading of this schema - a synthetic-`oneof` field appears only if the
  device sent it - so an absent field shows in the diff as a key appearing rather than as
  a zero that could mean either thing.
- **Record field numbers the schema does not know.** The schema is recovered from one
  Cortex Control build, so a field the firmware sends and that build never had decodes to
  nothing at all. `GeneralSettingsMessage` uses numbers 1-39 with no gaps, so anything new
  there would have been invisible. Use `google.protobuf.unknown_fields.UnknownFieldSet` -
  the upb runtime raises `NotImplementedError` on `msg.UnknownFields()`.
- **Collect values as a SET per path over a window, not one sample.** `GlobalTempo`
  alternates two shapes, one push each; sampling one message per type compares a clock
  reply against a params reply and reports the difference as real.
- **Label noise, never filter it.** Clocks, meters and request ids move on their own and
  are printed under their own heading. A filter is how the question got its previous wrong
  answer, and the answer here turned out to sit in a message the noise list would have
  been a natural home for.

Two practical notes. This is not merely a suggestion: ADR-0008 makes it the step that has to happen
before a control is recorded as having no wire path.

Prove the instrument offline first - `tests/test_state_snapshot.py`
feeds it a message carrying each thing it must not miss and fails if the snapshot comes
back empty, which is the only cheap way to tell "the device said nothing" from "the
capture cannot see it". And expect a large, boring diff: the connect burst's `File`
enumeration arrives in a different order every run, so several thousand lines of it are
noise around the one line that matters.

## Check a believed polarity against factory content

When you think you know which way a boolean or toggle goes, ask what every factory preset
holds there and whether the whole factory library would behave absurdly under your reading.
This library documented the metronome transport as "1.0 = muted" for two releases; all 17
factory presets hold 0.0 there with the volume at a normal level, and under that reading
every factory preset on every unit would click constantly. None does. The check takes one
loop over presets you already have, it needs no capture session, and it would have caught
the error before it shipped. (The wrong polarity had been inferred from a linked
parameter's NAME - a mirror proves linkage, never meaning.)

## Five things that make the difference between a result and a wasted hour

**Record every message type, not the one you expect.** Filtering to `GridMessage`
because a grid edit is expected will hide the answer if the operation travels as
something else. Filter only the noise list, and filter *after* recording if you can.

**Include a positive control.** Ask for a scene switch as well as the action under
investigation. A scene switch reliably broadcasts, so if it appears and the action does
not, the silence is a finding; if neither appears, the capture is broken. Without a
control, "nothing arrived" is uninterpretable.

**Run the listener as a background process, writing to a file.** Anything that prints
its prompt and then sleeps is useless for coordinating with a person: the output only
reaches them when the process exits, by which time the window has closed. Start it
detached, tell the person it is already running, and read the log afterwards.

**Have them repeat the action for the whole window.** Overlap is then guaranteed rather
than negotiated. A single gesture at an agreed moment is easy to miss by a few seconds.

**Ask whether the change needs committing.** Some editors on the unit only broadcast
when a value is confirmed rather than while it is being dragged. If a drag produces
nothing, have them press the confirm control and watch again.

## Reading what you get

Compare the captured message against what the library sends for the nearest equivalent
operation. Differences worth checking, each of which has mattered at least once:

- **Which field**, not just which shape. Sub-elements of a chain live in separate
  repeated fields (`models`, `splitter`, `combined_splitter`, `mixer`,
  `output_control`, `input_control`), and only some of them accept writes.
- **Whether a model hash is present.** The device usually omits it when broadcasting a
  parameter change.
- **Whether a `column` is present.** Grid blocks carry one; per-row elements do not.
- **What else is in the message.** An empty sibling element can void the whole update,
  so a message carrying only what it means to change is safest.
- **Parameter indices.** These are positional and follow the model's own parameter
  order, which for a family of related models may be the unified model's order rather
  than the type-specific one a preset reports.

A second thing the echo tells you: **whether the device accepted the write at all.**
An accepted `set_block` draws 2-3 `Grid` echoes naming the cell plus an
`UndoRedoMessage`; one refused for want of DSP capacity draws neither. That is the
only signal a refusal produces, and the same listener finds it.

Then replay the captured shape host to device, save, and read it back. A shape is only
confirmed once the value survives a save and recall - see
[Operation coverage](protocol.md#operation-coverage) for how each operation in this
library was established.

## Caveats

`_dispatch` and `_t` are private. This is a debugging technique, not an API: expect it
to need adjusting, and do not build on it.

Cortex Control must be quit, since it holds the USB interface exclusively. That also
means you cannot capture Cortex Control's own traffic this way on macOS - for that you
need a USB analyser at the bus level.
