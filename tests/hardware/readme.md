# The hardware-in-the-loop suite

Drives a real Quad Cortex over USB. This is ADR-0005's suite, and its contract is
that **a successful run leaves the unit exactly as it found it**.

```bash
pytest tests/hardware --hardware
```

Without `--hardware` nothing here is collected at all - not skipped, not collected.
A hardware test that reports itself as a skip in an offline run is a test nobody
notices has stopped running.

## Before you run it

- **Quit Cortex Control.** It holds the USB HID interface exclusively.
- Expect the unit to be edited. Every test snapshots what it touches and restores
  it in teardown, pass or fail, but the edits are real while they happen.
- Nothing here saves a preset, so the unsaved-edit escape hatch still applies: if
  a run dies badly, recalling any preset discards whatever it left on the grid.

## If a restore fails

The `restores` fixture re-raises at the end of the test naming **every** item it
could not put back, rather than aborting on the first. That message is the list
to fix by hand. Global settings are the ones worth checking first, since they
survive a preset recall.

## One connection, and why it records the connect burst

Every test shares one connection, because the unit lets only one process hold the
HID interface - a test that opened a second one would fail on whatever order it
happened to run in.

That connection attaches a listener before the handshake and records the type of
every message the unit pushes. It is attached on every run, not only for the tests
that read it, because it cannot be attached later: the burst happens during
`connect()`. It records type names and stops at 4000 of them, so the metronome's
endless tempo stream cannot grow it for the length of the run.

## Why the control test exists

`test_parameter_echo_latency_is_the_control` measures a write whose latency was
already known from earlier work (113-116 ms) using the same harness as everything
else, and asserts the answer. The first version of this file reported 2-11 ms for
all five unmeasured write types, which looked like a discovery and was very nearly
recorded as one. Any harness that measures something should measure a known
quantity alongside it.

The control only proves the harness matches the right message **for the write type
it measures**, so it is not a blanket guarantee for the others. That is why every
predicate in this file matches on CONTENT - the value written, at the index written
- rather than on message type alone. A type-only match is what produced the 2-11 ms
band, and the three fastest write types are the ones where it is most tempting,
because their echoes are single messages that look unambiguous.

Each measurement also asserts an upper bound derived from `set_block`'s timeout,
which is the one echo watcher the library ships. These numbers exist to justify
that timeout, so a latency creeping toward it has to fail here rather than leave
the suite green and the documented figure stale.
