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
import pyquadcortex
from pyquadcortex.proto import ProductionAutomation_pb2 as pa

# Chatter that arrives constantly and drowns everything else. On the firmware
# measured, GlobalTempoMessage is the only heavy one - about 50 in 10 seconds -
# so start by counting arrivals BY TYPE and filter from what you actually see
# rather than from this list. (CPULoadMessage, for instance, never arrives at
# all, subscribed or not, so filtering it is harmless but pointless.)
NOISE = {"GlobalTempoMessage", "IOMeterMessage", "GridModelMeterMessage",
         "KeepAliveMessage", "ModuleStatsMessage"}

seen, lock = [], threading.Lock()

with pyquadcortex.connect() as qc:
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
