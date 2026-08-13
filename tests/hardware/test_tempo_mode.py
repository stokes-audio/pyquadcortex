"""Where the unit keeps TEMPO MODE (GLOBAL or PRESET).

The unit's Tempo and Metronome menu has a MODE switch, and in PRESET mode the
tempo and all seven metronome settings belong to the preset. Cortex Control has
the same switch, so a route to it exists. Three earlier tests watched for a
broadcast when the switch moves and saw nothing, and that was written up as "not
on the wire at all" - which is more than those tests measured. **They listened;
none of them asked.** See ADR-0008 and ``protocol.md`` "MODE is the DEVICE tempo
block's parameter 1".

**It was asked, and it answered.** MODE is the DEVICE tempo block's parameter 1,
carried in ``GlobalTempo.params``: ``0.0`` PRESET, ``1.0`` GLOBAL. The winning
hypothesis was the third of three - the other two were killed by the same
capture, and both negatives are recorded in ``protocol.md``:

1. ``BinaryPreset.tempo`` (field 10) as the discriminator. **Dead**: absent in
   both positions, and ``tempoProgramData`` was identical across the flip.
2. ``GeneralSettings`` carrying it in a field number the recovered schema does
   not know - its schema uses 1-39 with no gaps. **Dead**: identical in both
   positions, and no message anywhere carried an unknown field number.
3. ``GlobalTempo.params`` holding an unmapped index. **This one.**

This module keeps two tests, and they do different jobs:

* :func:`test_tempo_mode_is_writable` is the REGRESSION test. It drives the
  shipped ``tempo_mode`` / ``set_tempo_mode`` and always runs.
* :func:`test_capture_tempo_mode_state` is the INSTRUMENT that found the answer,
  kept because ADR-0008 makes a differential state capture the thing you do
  before recording a control as having no wire path. It is read-only, needs an
  operator, and skips unless one asks for a capture.

To use the instrument on some other control, run it once per position with the
control moved on the touchscreen in between::

    QC_SNAPSHOT_LABEL=global  pytest tests/hardware --hardware -s -k tempo_mode
    # flip MODE on the unit, then:
    QC_SNAPSHOT_LABEL=preset  pytest tests/hardware --hardware -s -k tempo_mode

The second run diffs the two. ``-s`` matters: the finding is what it prints.

Nothing here looks for a field it expects. It records every set field of every
message the device answers with and diffs the two positions, so a difference is
found wherever it is rather than only where it was predicted.
"""
import importlib.util
import json
import os
import time
from pathlib import Path

import pytest

from pyquadcortex.protocol.client import QuadCortex
from pyquadcortex.protocol.enums import TempoMode

_MODULE = Path(__file__).parent / "state_snapshot.py"
_spec = importlib.util.spec_from_file_location("qc_state_snapshot", _MODULE)
state_snapshot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(state_snapshot)

#: Working artifacts, not evidence to commit - the findings go in the docs.
CAPTURES = Path(__file__).parent / "captures"


def test_capture_tempo_mode_state(qc):
    """READ everything readable and save it under ``QC_SNAPSHOT_LABEL``.

    An operator-driven CAPTURE INSTRUMENT, not a regression test - it needs a
    person to have set the control to a known position and to say which. So it
    skips rather than fails when unlabelled, and the skip names what to do.

    That is the one skip this directory's no-silent-skips rule tolerates, and
    only because of what it is: a test that has stopped exercising the device is
    invisible as a skip, but this one has nothing to exercise until somebody asks
    for a capture. ``test_tempo_mode_is_writable`` below is the regression test,
    and it always runs.
    """
    label = os.environ.get("QC_SNAPSHOT_LABEL")
    if not label:
        pytest.skip(
            "no QC_SNAPSHOT_LABEL - this is an operator-driven capture, not a "
            "regression test. To take one, set the label to the MODE position "
            "the unit is showing RIGHT NOW: QC_SNAPSHOT_LABEL=global pytest "
            "tests/hardware --hardware -s -k capture_tempo")

    snapshot = state_snapshot.capture(qc, label)

    preset = snapshot["preset"]
    tempo_params = sorted(p for p in preset if p.startswith("tempoProgramData"))
    global_tempo = snapshot["shapes"].get("GlobalTempoMessage", [])
    with_params = [s for s in global_tempo
                   if any(p.startswith("params") for p in s["fields"])]
    arrivals = sum(s["count"] for s in with_params)
    unknown = sorted(
        f"{name}: {path_}"
        for name, shapes in snapshot["shapes"].items()
        for shape in shapes for path_ in shape["fields"] if "UNKNOWN" in path_)
    unknown += sorted(f"preset: {p}" for p in preset if "UNKNOWN" in p)

    print(f"\n=== snapshot '{label}' ===")
    print(f"message types answered : {len(snapshot['shapes'])}")
    print(f"preset name            : {preset.get('name', '<absent>')}")
    print(f"preset.tempo (field 10): {preset.get('tempo', '<ABSENT>')}")
    print(f"tempoProgramData count : {preset.get('tempoProgramData.<count>', 0)}"
          f" block(s), {len(tempo_params)} field path(s)")
    print(f"GlobalTempo            : {len(global_tempo)} distinct shape(s); the "
          f"params shape arrived {arrivals}x")
    print(f"UNKNOWN field numbers  : {unknown if unknown else 'none'}")
    if snapshot["tap_errors"]:
        # Not decoration. A describe() that raises on one type would otherwise
        # look exactly like that type never arriving, which is the failure this
        # whole investigation exists to undo.
        print(f"TAP ERRORS (the snapshot is incomplete): {snapshot['tap_errors']}")

    # Asserted BEFORE the file is written. A snapshot that fails any of these is
    # not evidence, and writing it anyway is worse than not capturing: the diff
    # step globs the directory, so a bad file gets compared and reported as a
    # result. The tap_errors check is the same argument - a type that raised in
    # describe() is missing from `shapes`, and the diff renders that as
    # "<absent> -> ...", which reads exactly like a discovery.
    assert snapshot["shapes"], "no device traffic at all - is the link up?"
    assert not snapshot["tap_errors"], snapshot["tap_errors"]
    assert with_params, (
        "no GlobalTempo push carrying tempo PARAMETERS arrived in the window. "
        "That shape is the only one that answers, so this capture cannot see "
        "MODE at all - and a diff of it would report 'nothing differed', which "
        "is verbatim the wrong answer this harness exists to overturn. Re-run, "
        "or lengthen the window.")

    CAPTURES.mkdir(exist_ok=True)
    path = CAPTURES / f"{label}.json"
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True, default=repr))
    print(f"written                : {path}")


#: How long the written value is left in place before the restore puts it back.
#: Sized for a PERSON: script output does not reach the operator until the run
#: exits, so the only way they can confirm the unit's own menu moved is to be
#: watching it while this window is open.
HOLD_SECONDS = 8.0

#: A read straight after a write returns the PREVIOUS value - three settings have
#: already looked like they refused a write that had in fact landed (client.py).
#:
#: This one has to clear a specific, measured bar rather than be merely generous.
#: ``tempo_mode()`` cannot correlate its reply (this type never echoes
#: ``request_id``), so it returns the next AMBIENT params push - and that shape
#: arrives only about every seven seconds. A settle shorter than that interval can
#: hand back a push generated BEFORE the write. Caught in the act: a restore with a
#: 3.0 s settle read back the old value while the write had in fact landed, and
#: four reads two seconds apart afterwards all agreed it had. Ten seconds clears
#: the interval with room to spare.
SETTLE_SECONDS = 10.0


def test_tempo_mode_is_writable(qc, restores):
    """Drive MODE from the host, and prove which scope the write landed in.

    Exercises the SHIPPED methods - ``tempo_mode`` and ``set_tempo_mode`` - rather
    than building the messages here, so what the coverage table claims is verified
    is the code a caller actually runs.

    Sets MODE to whichever value the unit is NOT currently showing, holds it long
    enough to be seen on the unit's own screen, reads it back, and restores.
    ADR-0005: the restore is registered BEFORE the write, so the unit is put back
    whether this passes or fails.

    The second assertion is the one ADR-0007 actually cares about. Option (c) -
    let a tempo write through and work out the scope afterwards - was rejected
    because a guess and a success look identical to the caller. So this checks
    that the write moved the DEVICE block and left the preset's
    ``tempoProgramData`` alone, rather than trusting that it went where it was
    aimed.
    """
    before = qc.tempo_mode()
    target = TempoMode.GLOBAL if before is TempoMode.PRESET else TempoMode.PRESET
    preset_before = _preset_mode_param(qc)

    # The restore READS BACK. Everywhere else in this suite a restore is a blind
    # write, which is tolerable for preset state - unsaved and discarded by any
    # recall. MODE is GLOBAL: it survives a recall, so an unnoticed failed restore
    # leaves the unit changed for good. And this test's own thesis is that a
    # guess and a success are indistinguishable on this device.
    def put_back():
        qc.set_tempo_mode(before)
        time.sleep(SETTLE_SECONDS)
        landed = qc.tempo_mode()
        assert landed is before, (
            f"MODE left on {landed.name}, should be {before.name} - set it by hand")

    restores(f"tempo MODE -> {before.name}", put_back)
    qc.set_tempo_mode(target)

    time.sleep(SETTLE_SECONDS)
    after = qc.tempo_mode()
    preset_after = _preset_mode_param(qc)
    time.sleep(HOLD_SECONDS)

    print("\n=== TEMPO MODE write ===")
    print(f"was                    : {before.name}")
    print(f"wrote                  : {target.name}")
    print(f"read back              : {after.name}")
    print(f"preset tempoProgramData: param {QuadCortex.TEMPO_MODE_PARAM} "
          f"{preset_before} -> {preset_after}")
    print(f"restoring to           : {before.name}")

    assert after is target, (
        f"set_tempo_mode({target.name}) and tempo_mode() returned {after.name}. "
        f"The device accepts a write it does not understand and says nothing, so "
        f"this is exactly what an unsupported write looks like.")
    assert preset_after == preset_before, (
        f"the write was aimed at the DEVICE tempo block but the preset's "
        f"tempoProgramData param moved too ({preset_before} -> {preset_after}). "
        f"Scope is not what it appears - do not model this as a device setting.")


def _preset_mode_param(qc):
    """The PRESET copy of the same parameter, read positionally.

    The stored preset carries all 24 with ``index`` absent, so position is the
    index (``protocol.md``). Returns ``None`` if the block is not there at all,
    which is a different answer from zero and has to stay so.
    """
    index = QuadCortex.TEMPO_MODE_PARAM
    params = qc.read_current_preset().tempoProgramData
    if not params or len(params[0].params) <= index:
        return None
    values = params[0].params[index].param_values
    return values[0].float_value if values else None


#: How far apart two captures may be and still be treated as one experiment.
#: The point of the pair is that ONE thing changed between them - the operator
#: moving the control. Two files hours apart differ in whatever else happened in
#: between (a preset recall, an edit, a reboot), and the diff cannot tell those
#: from the answer. `captures/` is gitignored and nothing prunes it, so without
#: this an old file silently becomes half of a new comparison.
PAIR_WINDOW_SECONDS = 3600.0


def test_diff_captured_snapshots():
    """Diff every pair of snapshots on disk. Needs no unit; needs two files."""
    files = sorted(CAPTURES.glob("*.json")) if CAPTURES.exists() else []
    if len(files) < 2:
        pytest.skip(f"{len(files)} snapshot(s) in {CAPTURES} - need two to diff")

    loaded = [(f, json.loads(f.read_text())) for f in files]
    compared = 0
    for index, (left_file, before) in enumerate(loaded):
        for right_file, after in loaded[index + 1:]:
            if before["label"] == after["label"]:
                continue                    # same position; nothing to learn
            gap = abs(left_file.stat().st_mtime - right_file.stat().st_mtime)
            if gap > PAIR_WINDOW_SECONDS:
                print(f"\n=== SKIPPED {before['label']} -> {after['label']}: "
                      f"{gap / 60:.0f} min apart, outside the pairing window. "
                      f"Delete the stale one and re-capture. ===")
                continue

            compared += 1
            signal, noise = state_snapshot.diff(before, after)
            print(f"\n=== {before['label']} -> {after['label']} ===")
            print(f"--- {len(signal)} field(s) moved ---")
            for line in signal:
                print(f"  {line}")
            if not signal:
                print("  nothing outside the known-noisy fields differed")
            print(f"--- {len(noise)} known-noisy path(s), shown for completeness ---")
            for line in noise:
                print(f"  {line}")

            # The pair has to be diffable at all. Both snapshots must carry the
            # shape that answers, or "nothing differed" means "the instrument was
            # blind", not "the device did not move" - the confusion that cost
            # this project eight releases.
            for snapshot in (before, after):
                assert any(
                    p.startswith("params")
                    for shape in snapshot["shapes"].get("GlobalTempoMessage", [])
                    for p in shape["fields"]), (
                    f"snapshot {snapshot['label']!r} carries no GlobalTempo params "
                    f"shape, so this diff cannot see MODE")
                assert not snapshot["tap_errors"], (
                    f"snapshot {snapshot['label']!r} was captured with tap errors "
                    f"and is not evidence: {snapshot['tap_errors']}")

    assert compared, (
        f"{len(files)} snapshot(s) present but no valid pair to diff - they share "
        f"a label, or are further than {PAIR_WINDOW_SECONDS / 60:.0f} min apart")
