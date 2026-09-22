# The hardware-in-the-loop suite

> Purpose: how to run the hardware suite, what it promises about the unit, and the rules a test in this directory follows.

This suite drives a real Quad Cortex over USB. Its contract (ADR-0005) is that a
successful run leaves the unit exactly as it found it.

```bash
pytest tests/hardware --hardware
```

The undo/redo test makes a disposable copy of the loaded preset, edits that
copy, then independently restores the original and deletes the copy. Saving the
copy clears its instrument tag and makes the scene active at save time its
default scene; those metadata changes affect only the disposable copy.

`pytest --hardware` from the repo root also works, and runs both suites, the
offline one and this one. Name the directory unless you want that.

Two more options go with the flag (ADR-0020):

- `--verifies OPERATION` narrows the run to the tests marked
  `verifies(OPERATION)`. It refuses a name that is not an operation or that no
  collected test names.
- `--profile CLASSNAME` connects as that profile class instead of the one the
  unit's identity resolves to. That is how a unit the registry would refuse, an
  unmeasured firmware or a Mini, gets measured. The name is a class in
  `pyquadcortex.protocol.profiles`, or `QuadCortex` itself; an unknown name stops
  the run naming the real ones. Without `--hardware` it is ignored.

  ```bash
  pytest tests/hardware --hardware --profile QuadCortexMini
  ```

## The gate

Without `--hardware` nothing here runs, and it is never a skip. A hardware test
that reports itself skipped in an offline run is a test nobody notices has stopped
running. Which of two stronger things happens depends on how pytest reached the
path:

- **Reached by walking the tree** (`pytest`, `pytest tests/`,
  `pytest tests/hardware`): not collected at all. `pytest_ignore_collect` vetoes
  the file before it is imported.
- **Named on the command line** (`pytest tests/hardware/test_write_echo.py`):
  the run stops with `ERROR: these tests drive a real Quad Cortex and need
  --hardware`, naming every path it refused. pytest does not consult
  `pytest_ignore_collect` for a path named on the command line, so these are
  collected first and then refused by `pytest_collection_modifyitems`. The
  refusal is loud because you asked for those tests by name. `--collect-only`
  still prints the item list before it exits. That pytest exempts a named path
  from `pytest_ignore_collect` is observed behaviour, not its hookspec; if pytest
  ever closes the gap, `tests/test_hardware_gate.py` fails on the exit code it
  asserts.

The gate is those two hooks in `conftest.py`, and it stays two. Folding them into
one restores the bug for whichever half is dropped. `tests/test_hardware_gate.py`
holds both halves in a subprocess. If collection itself fails first, pytest stops
there and the refusal never speaks.

**A module here needs a basename no module under `tests/` owns**, which is why
files end in `_on_unit`. pytest maps `tests/hardware/test_scales.py` and
`tests/test_scales.py` to one module name and refuses the second, so the whole
tree stops collecting. Rename; do not make `tests/` a package, because three
offline modules do `from waiting import ...`, which works only while pytest puts
`tests/` on `sys.path`.

## What a test here does

- **It names what it verifies.** `@pytest.mark.verifies(*operations)` lists the
  `QuadCortex` operations the test both exercises and asserts on, checked against
  `QuadCortex.operations()` at collection. An operation no test names has to
  appear in `UNMARKED_OPERATIONS` in `tests/test_hardware_markers.py` with the
  reason.
- **It restores what it touched**, in teardown, pass or fail. The `restores`
  fixture re-raises at the end naming every item it could not put back. Global
  settings are the ones to check first after a failure, since they survive a
  preset recall.
- **The edited flag is put back once, at the end.** A write marks the preset
  edited, and writing the original value back is another write, so the undo
  callables put the grid right and leave the flag set. A session teardown clears
  it with a recall, and only when the preset was clean before the first test:
  unsaved edits that were already there are the owner's.
- **It edits a scratch copy.** `scratch_preset` saves a disposable copy of the
  loaded preset and hands that copy to the test. It never saves over one of the
  owner's presets; if a run dies badly, the fixed scratch name identifies the
  copy that may need removing.

## Before you run it

- Quit Cortex Control. It holds the USB HID interface exclusively.
- Expect the unit to be edited. The edits are real while they happen.
- Do not touch the unit while a run is going. The suite reads the edited flag
  once, before the first test, to tell your unsaved edits from its own; an edit
  you make after that reads as the suite's and is discarded at the end.
- `test_model_state.py` needs a loaded preset with no unsaved changes, because
  `PresetDirty` announces a change of the flag rather than an edit, so only the
  first edit of a run produces one. The `a_clean_preset` fixture reloads to get
  it. It skips instead when the preset was already edited before the session
  started, since clearing that would discard your work.

## One connection, and the connect burst

Every test shares one connection, because the unit lets only one process hold the
HID interface.

That connection attaches a listener before the handshake and records the type of
every message the unit pushes. It is attached on every run because it cannot be
attached later: the burst happens during `connect()`. The fixture registers the
model's cache before the recorder, then waits for the burst to finish before
handing the connection to the first test, and stops the recorder there. The
recording is therefore exactly the burst.

The burst is waited for as a group. `HandshakeBurst.BURST_TAIL` names the four
messages that close it: `RecallPreset`, `SetlistPosition`, `PresetDirty`,
`Scene`. `RecallPreset` is the first of the four and the other three trail it by
3.6 to 6.0 ms against a 100 ms poll, so waiting for `RecallPreset` alone lost one
of the others a few runs in a hundred. `unfinished()` names what never arrived,
in the run's own report and in the tests that guard on it.

The burst test's `assert handshake_burst.closed` and `unfinished() is None` are
the only assertions that fail if the fixture stops waiting for the burst; every
other assertion in that test is a floor, and contamination satisfies a floor. Do
not delete them as redundant. The cache costs the RX thread one small message
copy per `Version` or `PresetDirty` push and nothing for anything else.

A new cache entry comes through `BURST_TAIL`, `OUTSIDE_THE_BURST` or
`NOT_WARMED_BY_THE_BURST` in `tests/test_handshake_burst_recorder.py`, with its
reason. That file also pins offline the three ways a recorder can look like it is
working and not be: setting its flag and recording on, stopping but staying
attached, and stopping on the first message of the group.

The wait costs about 9 seconds once per run. `connect()` returns about 3 seconds
before the unit starts streaming several hundred messages, so without it every
latency measurement below would be taken on a busy link.

## The control test

`test_parameter_echo_latency_is_the_control` measures a write whose latency was
already known (113 to 116 ms) with the same harness as everything else, and
asserts the answer. A harness that measures something should measure a known
quantity alongside it: an earlier harness reported 2 to 11 ms for five write
types because its predicate matched the wrong message.

The control proves the harness only for the write type it measures. So every
predicate in `test_write_echo.py` matches on content, the value written at the
index written, rather than on message type alone. Each measurement also asserts an
upper bound derived from `set_block`'s timeout, so a latency creeping toward it
fails here rather than leaving the documented figure stale.
