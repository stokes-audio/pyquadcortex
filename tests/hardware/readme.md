# The hardware-in-the-loop suite

Drives a real Quad Cortex over USB. This is ADR-0005's suite, and its contract is
that **a successful run leaves the unit exactly as it found it**.

```bash
pytest tests/hardware --hardware
```

The native undo/redo test has a second guard because it needs a disposable
loaded preset. Set `PYQUADCORTEX_UNDO_SCRATCH_SLOT` to that preset's zero-based
slot index; the test refuses to run if a different slot is loaded or the preset
already has unsaved changes.

`pytest --hardware` from the repo root works too, since the rename described
below, and it runs BOTH suites - the offline one and this one, against your unit.
Name the directory unless you want that.

Without `--hardware` nothing here runs. A hardware test that reports itself as a
skip in an offline run is a test nobody notices has stopped running, so it is
never a skip - which of the two stronger things happens depends on how the path
reached pytest:

- **Reached by walking the tree** (`pytest`, `pytest tests/`, `pytest tests/hardware`):
  not collected at all. `pytest_ignore_collect` vetoes the file before it is even
  imported.
- **Named on the command line** (`pytest tests/hardware/test_write_echo.py`, or one
  node id): the run stops with `ERROR: these tests drive a real Quad Cortex and
  need --hardware`, naming every path it refused. pytest does not offer
  command-line arguments to `pytest_ignore_collect` at all, so these are
  collected first and then refused by `pytest_collection_modifyitems`; no test
  runs, and `--collect-only` still prints the item list before it exits. The
  refusal is loud rather than a silent deselect because you asked for those tests
  by name and are owed the reason they did not run.

Both halves are pinned offline in `tests/test_hardware_gate.py`, in a subprocess
running the developer's own command. The second half was missing until
2026-08-28: pytest exempts an initial command-line path from
`pytest_ignore_collect` (`Dir.collect` skips the hook for anything
`Session.isinitpath` claims - `_pytest/main.py`, pytest 9.1.1), so a named path
walked straight past the flag, and with a unit attached those tests ran and drove
it. That exemption is observed in pytest's code and absent from its hookspec,
which says the hook is consulted for every file and directory - so treat it as
behaviour rather than a promise. Nothing here depends on which it is: if pytest
ever closes the gap, a named path stops being collected and the tests in
`tests/test_hardware_gate.py` fail on the exit code they assert.

One seam worth knowing, because it is not this gate's doing: if collection
itself fails first, pytest stops there and the refusal never gets to speak.
Nothing runs in that case either - you just get pytest's collection error
instead of the message naming the flag.

That is why two files here end in `_on_unit`. **A module in this directory needs
a basename no module under `tests/` already owns.** pytest maps
`tests/hardware/test_scales.py` and `tests/test_scales.py` to one module name and
refuses the second, which until 2026-08-28 meant `pytest --hardware` from the
repo root could not collect this suite at all - two collection errors, exit 2 -
and only the documented `pytest tests/hardware --hardware` worked. Renaming was
the fix rather than making `tests/` a package, because three offline modules do
`from waiting import ...`, which works only while pytest keeps putting `tests/`
on `sys.path`. `tests/test_hardware_gate.py` now fails if the whole tree stops
collecting under the flag, so the rule does not depend on being remembered.

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
`connect()`.

The fixture then waits for the burst to finish before handing the connection to
the first test, and stops the recorder there. The recording is therefore exactly
the burst, whatever order the tests run in. The metronome's tempo stream never
stops, so a recorder left running would hold the whole run's traffic and a test
asserting on it would really be asserting on whatever other tests provoked first.

The wait costs about 8 seconds once per run and buys more than it costs.
`connect()` returns roughly 3 seconds before the unit starts streaming several
hundred messages, so without it every latency measurement below would be taken on
a link still busy answering the handshake.

The burst test's `assert handshake_burst.closed` and `settled_in is not None` are
what hold that up. They are not belt-and-braces: they are the only things that
fail if the fixture stops waiting for the burst, since every other assertion in
that test is a floor and contamination satisfies a floor. Do not delete them as
redundant.

What they cannot see is a recorder that sets its flag and keeps recording anyway,
or one that stops recording but stays attached to the transport. Both read like a
working recorder from the outside, so both are pinned offline in
`tests/test_handshake_burst_recorder.py`.

## The model's cache rides the same connection

`test_model_state.py` covers the model's state layer, and the connection fixture
attaches a `DeviceState` before the handshake for the same reason it attaches the
burst recorder: that is the only moment early enough. It stays attached for the
whole run and costs the RX thread one small message copy per `Version` or
`PresetDirty` push - nothing at all for anything else, and orders of magnitude
under the latencies measured below.

It needs one thing of the unit that nothing else here does: **a loaded preset with
no unsaved changes**. `PresetDirty` announces a CHANGE of the flag rather than an
edit, so only the first edit of a run produces an announcement, and the test that
proves an outside edit reaches the model needs that announcement. It skips with a
message saying so if the preset arrives already dirty. If you see that skip, save
or reload the preset on the unit and run again.

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
