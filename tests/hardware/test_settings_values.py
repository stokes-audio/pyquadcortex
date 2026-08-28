"""ADR-0017's typed settings, against the real unit.

`tests/hardware/test_values.py` covers a parameter the CATALOG describes. These
are the settings it does not: an input port's gain, the Global EQ, the HOLD
threshold, the tuner reference. Their scales come from `units.SETTING_SPANS`
rather than from the device's own description, so "the unit agrees" is a
different claim here and worth its own file - if a span is wrong, offline tests
cannot know.

State-neutral per ADR-0005. An input gain in particular is something a player
has dialled in by ear, so every one of these snapshots first and restores in
teardown.

The reconnect-shaped delay is not ceremony either: a read straight after a write
returns the PREVIOUS value on this firmware, and `set_input_port(confirm=True)`
exists because that trap already cost this project a wrong conclusion.
"""

import time

import pytest

from pyquadcortex.protocol import client, units, values
from pyquadcortex.protocol.enums import Input

SETTLE = 2.0

#: The port these tests drive. Input 1 is the one the suite's other tests leave
#: alone, and its gain is restored in teardown either way.
PORT = Input.INPUT_1


def _input_level(qc, port_id):
    for port in qc.io_settings().settings.in_port:
        if port.input_port_id == int(port_id):
            return port.level
    raise AssertionError(f"no in_port entry for {port_id}")


def test_an_input_gain_written_in_db_reads_back_as_that_db(qc, restores):
    """The one setting with a measured span, driven both ways.

    -12..+60 dB from four screen/wire pairs. This does not re-derive the span -
    it checks the unit stores what the span predicts, which is what would fail
    if the four readings had been misread or the firmware changed.
    """
    before = _input_level(qc, PORT)
    restores("input 1 gain",
             lambda: qc.set_input_port(PORT, level=values.Encoded(before)))

    qc.set_input_port(PORT, level=values.Db(24.0), confirm=True)
    time.sleep(SETTLE)

    wire = _input_level(qc, PORT)
    assert wire == pytest.approx(0.5, abs=1e-4), (
        f"wrote Db(24.0); the span says wire 0.5 and the unit stored {wire}")
    assert units.input_level_db(wire) == pytest.approx(24.0, abs=0.05)


def test_zero_db_is_exactly_one_sixth_on_the_unit(qc, restores):
    """The point that fixes the span's zero, checked on the device rather than
    in a fixture: 0 dB over -12..+60 is 12/72, and nothing else lands there."""
    before = _input_level(qc, PORT)
    restores("input 1 gain",
             lambda: qc.set_input_port(PORT, level=values.Encoded(before)))

    qc.set_input_port(PORT, level=values.Db(0.0), confirm=True)
    time.sleep(SETTLE)
    assert _input_level(qc, PORT) == pytest.approx(1 / 6, abs=1e-4)


def test_a_global_eq_gain_in_db_lands_where_the_manuals_span_says(qc, restores):
    """The span here is the MANUAL's on two points, so this is the weakest
    claim in the file and is labelled as such rather than presented beside the
    input port's as equal evidence. What it pins is the wire value; whether the
    SCREEN reads -3.0 dB there is the reading still owed - see
    ``units.SETTING_SPANS``.
    """
    band, offset = 1, 0
    before = [p.value for p in qc.global_eq().parameters
              if p.parameter_index == offset]
    assert before, "the Global EQ reported no parameter 0"
    restores("global EQ band 1 gain",
             lambda: qc.set_global_eq_band(offset, values.Encoded(before[0])))

    qc.set_global_eq(band, gain=values.Db(-3.0))
    time.sleep(SETTLE)

    now = [p.value for p in qc.global_eq().parameters
           if p.parameter_index == offset]
    # Through the same object the write used, which is the point: one law.
    assert now[0] == pytest.approx(
        client._GLOBAL_EQ_GAIN.to_normalized(-3.0), abs=1e-4)


def test_the_hold_threshold_takes_milliseconds_and_stores_an_index(qc, restores):
    """No 0..1 line at all: the wire carries the index, the caller says ms."""
    before = qc.hold_timing_ms()
    restores("hold timing",
             lambda: qc.set_hold_timing(values.Milliseconds(before)))

    target = 500 if before != 500 else 1000
    qc.set_hold_timing(values.Milliseconds(target))
    time.sleep(SETTLE)
    assert qc.hold_timing_ms() == target


def test_a_setting_with_no_measured_scale_refuses_rather_than_guessing(qc):
    """Nothing reaches the wire, so this needs no restore.

    Worth running on hardware anyway: it proves the refusal happens BEFORE the
    send, on a live connection where a guess would otherwise have landed.
    """
    from pyquadcortex.protocol.errors import ControlNotDrivable

    with pytest.raises(ControlNotDrivable):
        qc.set_master_volume(values.Db(-6.0))
    with pytest.raises(ControlNotDrivable):
        qc.set_global_eq(1, frequency=values.Hertz(400.0))
