"""Where the unit keeps TEMPO MODE (GLOBAL or PRESET).

The unit's Tempo and Metronome menu has a MODE switch, and in PRESET mode the
tempo and all seven metronome settings belong to the preset. Cortex Control has
the same switch, so a route to it exists. Three earlier tests watched for a
broadcast when the switch moves and saw nothing, and that was written up as "not
on the wire at all" - which is more than those tests measured. **They listened;
none of them asked.** See ADR-0007 and ``protocol.md`` "Per-preset tempo, LED
and metronome".

This asks. It is read-only - every message it sends is a ``READ`` - so it writes
nothing to the unit and needs no restore.

Run it once per MODE position, with the switch moved on the touchscreen in
between::

    QC_SNAPSHOT_LABEL=global  pytest tests/hardware --hardware -s -k tempo_mode
    # flip MODE on the unit, then:
    QC_SNAPSHOT_LABEL=preset  pytest tests/hardware --hardware -s -k tempo_mode

The second run diffs the two. ``-s`` matters: the finding is what it prints.

The three hypotheses it covers at once, cheapest first:

1. ``BinaryPreset.tempo`` (field 10) and ``tempoProgramData`` (field 19) are
   presence-tracked, and presence may itself be the discriminator - a preset
   saved under PRESET mode carries them, one saved under GLOBAL does not.
2. ``GeneralSettings`` carries the mode and never broadcasts it. A READ would
   show it. Its schema uses field numbers 1-39 with no gaps, so if it is there
   it is in a number the recovered schema does not know - which is why unknown
   field numbers are recorded rather than dropped.
3. ``GlobalTempo.params`` holds an unmapped index.

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
    """READ everything readable and save it under ``QC_SNAPSHOT_LABEL``."""
    label = os.environ.get("QC_SNAPSHOT_LABEL")
    if not label:
        pytest.fail(
            "set QC_SNAPSHOT_LABEL to the MODE position shown on the unit RIGHT "
            "NOW, e.g. QC_SNAPSHOT_LABEL=global. The label is what the diff is "
            "reported against, so a wrong one makes the answer unreadable.")

    snapshot = state_snapshot.capture(qc, label)
    CAPTURES.mkdir(exist_ok=True)
    path = CAPTURES / f"{label}.json"
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True, default=repr))

    preset = snapshot["preset"]
    tempo_params = sorted(p for p in preset if p.startswith("tempoProgramData"))
    global_tempo = snapshot["shapes"].get("GlobalTempoMessage", [])
    with_params = [s for s in global_tempo
                   if any(p.startswith("params") for p in s["fields"])]
    unknown = sorted(
        f"{name}: {path_}"
        for name, shapes in snapshot["shapes"].items()
        for shape in shapes for path_ in shape["fields"] if "UNKNOWN" in path_)
    unknown += sorted(f"preset: {p}" for p in preset if "UNKNOWN" in p)

    print(f"\n=== snapshot '{label}' -> {path} ===")
    print(f"message types answered : {len(snapshot['shapes'])}")
    print(f"preset name            : {preset.get('name', '<absent>')}")
    print(f"preset.tempo (field 10): {preset.get('tempo', '<ABSENT>')}")
    print(f"tempoProgramData count : {preset.get('tempoProgramData.<count>', 0)}"
          f" block(s), {len(tempo_params)} field path(s)")
    print(f"GlobalTempo shapes     : {len(global_tempo)} distinct, "
          f"{len(with_params)} carrying params")
    print(f"UNKNOWN field numbers  : {unknown if unknown else 'none'}")
    if snapshot["tap_errors"]:
        # Not decoration. A describe() that raises on one type would otherwise
        # look exactly like that type never arriving, which is the failure this
        # whole investigation exists to undo.
        print(f"TAP ERRORS (the snapshot is incomplete): {snapshot['tap_errors']}")

    assert snapshot["shapes"], "no device traffic at all - is the link up?"
    assert not snapshot["tap_errors"], snapshot["tap_errors"]


#: How long the written value is left in place before the restore puts it back.
#: Sized for a PERSON: script output does not reach the operator until the run
#: exits, so the only way they can confirm the unit's own menu moved is to be
#: watching it while this window is open.
HOLD_SECONDS = 8.0

#: A read straight after a write returns the PREVIOUS value - three settings have
#: already looked like they refused a write that had in fact landed (client.py).
SETTLE_SECONDS = 3.0


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

    restores(f"tempo MODE -> {before.name}", lambda: qc.set_tempo_mode(before))
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


def test_diff_captured_snapshots():
    """Diff every pair of snapshots on disk. Needs no unit; needs two files."""
    files = sorted(CAPTURES.glob("*.json")) if CAPTURES.exists() else []
    if len(files) < 2:
        pytest.skip(f"{len(files)} snapshot(s) in {CAPTURES} - need two to diff")

    snapshots = [json.loads(f.read_text()) for f in files]
    for index, before in enumerate(snapshots):
        for after in snapshots[index + 1:]:
            signal, noise = state_snapshot.diff(before, after)
            print(f"\n=== {before['label']} -> {after['label']} ===")
            print(f"--- {len(signal)} field(s) moved ---")
            for line in signal:
                print(f"  {line}")
            if not signal:
                print("  nothing outside the known-noisy paths differed")
            print(f"--- {len(noise)} known-noisy path(s), shown for completeness ---")
            for line in noise:
                print(f"  {line}")
