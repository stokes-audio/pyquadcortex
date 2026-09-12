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

# The offline module, by bare name. What puts `tests/` on sys.path for a module
# down here in `tests/hardware/` is pytest importing `tests/conftest.py` - which
# exists and must, since it is where `--hardware` is declared. That is NOT the
# same mechanism as `tests/test_profiles.py` importing `test_client`, where the
# module's own basedir is already `tests/`. Worth the distinction if this file
# ever moves.
from test_scales import SETTING_READINGS

from pyquadcortex.protocol import client, units, values
from pyquadcortex.protocol.enums import Input
from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa

SETTLE = 2.0

#: The port these tests drive. Input 1 is the one the suite's other tests leave
#: alone, and its gain is restored in teardown either way.
PORT = Input.INPUT_1


@pytest.mark.verifies("inhibited_modules")
def test_inhibited_modules_is_a_complete_read_only_snapshot(qc):
    """Both false fields were explicitly present on CorOS 4.1.0 / d14e.

    Presence matters here: protobuf also returns false when an optional field was
    never sent, and reporting that default as device state would be a guess.
    """
    # Read twice because a first state READ can be dropped; both successful
    # replies must carry explicit fields rather than protobuf defaults.
    for _ in range(2):
        state = qc.inhibited_modules()
        assert state.action == pa.MessageAction.UPDATE
        assert state.HasField("global_gate")
        assert state.HasField("global_eq")


def _input_level(qc, port_id):
    for port in qc.io_settings().settings.in_port:
        if port.input_port_id == int(port_id):
            return port.level
    raise AssertionError(f"no in_port entry for {port_id}")


@pytest.mark.verifies("set_input_port")
def test_an_input_gain_written_in_db_reads_back_as_that_db(qc, restores):
    """One of the two settings with a measured span, driven both ways.

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


@pytest.mark.verifies("set_input_port")
def test_zero_db_is_exactly_one_sixth_on_the_unit(qc, restores):
    """The point that fixes the span's zero, checked on the device rather than
    in a fixture: 0 dB over -12..+60 is 12/72, and nothing else lands there."""
    before = _input_level(qc, PORT)
    restores("input 1 gain",
             lambda: qc.set_input_port(PORT, level=values.Encoded(before)))

    qc.set_input_port(PORT, level=values.Db(0.0), confirm=True)
    time.sleep(SETTLE)
    assert _input_level(qc, PORT) == pytest.approx(1 / 6, abs=1e-4)


#: The 2026-09-11 screen readings as `(dB on the page, wire value)`, derived from
#: the one place CLAUDE.md puts screen readings so the two cannot drift apart. A
#: correction there reaches the unit through this test rather than leaving it
#: driving points nobody has read any more. Sorted by wire, so the sweep is
#: monotonic instead of jumping end to end.
GLOBAL_EQ_GAIN_READINGS = [(screen, wire) for key, wire, screen, _
                           in sorted(SETTING_READINGS, key=lambda r: r[1])
                           if key == "GLOBAL_EQ_GAIN_DB"]


@pytest.mark.verifies("set_global_eq")
def test_a_global_eq_gain_in_db_lands_where_the_measured_span_says(qc, restores):
    """Every point of the 2026-09-11 screen measurement, driven again.

    The unit has to ACCEPT the ends, not just the middle: a span measured at its
    ends is worth nothing if writing them is refused or clamped. The dB half of
    each pair cannot be checked from here - that is `tests/test_scales.py`,
    which holds the readings against the span. This drives the wire half back
    onto the unit.
    """
    # The list is derived, so it can go empty or lose its ends without anything
    # else noticing - and this test would then pass having asserted nothing
    # while `verifies` still reported `set_global_eq` as measured. The ENDS
    # specifically: the offline taper test only needs the two interior readings,
    # so dropping 0.0 and 1.0 would leave it green while this one quietly
    # stopped checking the thing its docstring calls the point.
    wires = {wire for _, wire in GLOBAL_EQ_GAIN_READINGS}
    assert {0.0, 1.0} <= wires, (
        f"the measured readings no longer carry both ends (have {sorted(wires)}); "
        f"this test exists to drive them back onto the unit")

    # An OFFSET is not a wire index. `set_global_eq_band` and `parameter_index`
    # both want the whole index, so the base has to be added - band 1 is the
    # one band where forgetting that still works, which is exactly why it is
    # spelled out here rather than left as the offset alone.
    band = 1
    # Off `qc`, not off `QuadCortex`: under ADR-0020 the connection is a profile
    # subclass, and a profile that ever carried a different layout would be
    # measured against the base class's numbers if these were read off the class.
    index = ((band - 1) * qc.GLOBAL_EQ_BAND_STRIDE + qc.GLOBAL_EQ_BAND_GAIN)
    before = [p.value for p in qc.global_eq().parameters
              if p.parameter_index == index]
    assert before, f"the Global EQ reported no parameter {index}"
    restores(f"global EQ band {band} gain",
             lambda: qc.set_global_eq_band(index, values.Encoded(before[0])))

    for db, wire in GLOBAL_EQ_GAIN_READINGS:
        qc.set_global_eq(band, gain=values.Db(db))
        time.sleep(SETTLE)

        now = [p.value for p in qc.global_eq().parameters
               if p.parameter_index == index]
        assert now, f"the Global EQ stopped reporting parameter {index}"
        assert now[0] == pytest.approx(wire, abs=1e-4), (
            f"{db} dB was read on screen at wire {wire}")
        # Through the same object the write used, which is the point: one law.
        assert now[0] == pytest.approx(
            client._GLOBAL_EQ_GAIN.to_normalized(db), abs=1e-4)


@pytest.mark.verifies("set_hold_timing")
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
