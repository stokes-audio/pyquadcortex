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

## Why the control test exists

`test_parameter_echo_latency_is_the_control` measures a write whose latency was
already known from earlier work (113-116 ms) using the same harness as everything
else, and asserts the answer. The first version of this file reported 2-11 ms for
all five unmeasured write types, which looked like a discovery and was very nearly
recorded as one; the control is what would have caught it had the predicates
actually been wrong. Any harness that measures something should measure a known
quantity alongside it.
