"""Where a parameter lives (pyquadcortex.protocol.targets).

These are pure values - no device, no I/O - so everything here is exact. The
container shapes below are the wire shapes `docs/protocol.md` records, and they
are asserted field by field because the four keying conventions are genuinely
different and a wrong one is silent: the device accepts a misaddressed write and
says nothing.
"""
import pytest

from pyquadcortex.protocol import catalog, targets
from pyquadcortex.protocol.errors import ControlNotDrivable
from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa
from pyquadcortex.protocol.targets import (Block, BranchControl, ChainTarget,
                                           LaneControl, LaneInput, LaneOutput,
                                           Mixer, ParamTarget, PresetTarget,
                                           Splitter, Tempo)


def _container(target):
    msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
    element = target.container(msg)
    element.params.add().index = 3
    return msg


# -- the four keying conventions ----------------------------------------------


def test_a_block_is_keyed_by_column_and_carries_no_hash():
    chain = _container(Block(1, 6)).preset.chains[0]
    assert chain.row == 1
    assert chain.models[0].column == 6
    assert not chain.models[0].HasField("hash"), "a block is keyed by column"


@pytest.mark.parametrize("target,collection,model_id", [
    (LaneOutput(2), "output_control", 23000),
    (LaneInput(2), "input_control", 28000),
    (Mixer(2), "mixer", 11000),
])
def test_a_lane_control_is_keyed_by_hash_and_carries_no_column(target, collection,
                                                               model_id):
    chain = _container(target).preset.chains[0]
    element = getattr(chain, collection)[0]
    assert chain.row == 2
    assert element.hash == model_id
    assert not element.HasField("column"), "a lane control has no column"


def test_the_splitter_carries_neither_hash_nor_column():
    """The device's own broadcast sends it bare, and this copies that."""
    chain = _container(Splitter(0)).preset.chains[0]
    element = chain.combined_splitter[0]
    assert not element.HasField("hash")
    assert not element.HasField("column")
    assert not chain.models, "must not touch models[]"


def test_tempo_hangs_off_the_preset_rather_than_a_chain():
    msg = _container(Tempo())
    assert not msg.preset.chains, "the tempo block is not on a chain"
    assert msg.preset.tempoProgramData[0].hash == 25000


# -- the hierarchy exists so a signature can say what it accepts ---------------


@pytest.mark.parametrize("target,bases", [
    (Block(0, 0), (ChainTarget, ParamTarget)),
    (LaneOutput(0), (LaneControl, ChainTarget, ParamTarget)),
    (LaneInput(0), (LaneControl, ChainTarget, ParamTarget)),
    (Mixer(0), (BranchControl, LaneControl, ChainTarget, ParamTarget)),
    (Splitter(0), (BranchControl, LaneControl, ChainTarget, ParamTarget)),
    (Tempo(), (PresetTarget, ParamTarget)),
])
def test_each_target_sits_where_the_hierarchy_says(target, bases):
    for base in bases:
        assert isinstance(target, base), f"{target!r} is not a {base.__name__}"


def test_only_the_preset_target_lacks_a_row():
    assert not isinstance(Tempo(), ChainTarget)
    assert not hasattr(Tempo(), "row")


@pytest.mark.parametrize("kind", [Mixer, Splitter])
@pytest.mark.parametrize("row", [1, 3])
def test_a_branch_control_refuses_an_odd_row(kind, row):
    """Rows 1 and 3 report these collections EMPTY, so a write there does nothing.

    Refused at construction rather than at send, because the address is what is
    wrong - and because a silently-ignored write is this device's default
    failure mode.
    """
    with pytest.raises(ValueError, match="row 0 or row 2"):
        kind(row)


@pytest.mark.parametrize("kind", [Mixer, Splitter])
def test_a_branch_control_accepts_an_even_row(kind):
    assert kind(0).row == 0 and kind(2).row == 2


def test_scenes_belong_to_the_grid_so_tempo_has_none():
    assert Tempo().supports_scenes is False
    for target in (Block(0, 0), LaneOutput(0), LaneInput(0), Mixer(0), Splitter(0)):
        assert target.supports_scenes is True, f"{target!r} lost its scenes"


# -- the catalog is fetched only when it is genuinely needed -------------------


class _Exploding:
    """Stands in for a catalog fetch that must not happen.

    The catalog comes FROM the device, so fetching one costs a round trip, and a
    write addressed by wire index needs no catalog at all. An early draft made
    every write fetch one; it broke 27 tests that had never touched a device,
    and would have put a round trip in front of every indexed write on a real
    one. This is that regression, pinned.
    """

    def __call__(self):
        raise AssertionError("the catalog was fetched and should not have been")


@pytest.mark.parametrize("target", [Block(0, 1), LaneOutput(0), LaneInput(0),
                                    Mixer(0), Splitter(0), Tempo()])
def test_an_index_needs_no_catalog(target):
    assert target.index_of(4, _Exploding()) == (4, None)


def test_the_tempo_converts_bpm_without_a_catalog():
    """TEMPO's span was MEASURED, so the catalog has nothing to offer anyway."""
    assert Tempo().normalize(0, 120.0, _Exploding()) == pytest.approx(0.4)


def test_a_name_does_need_one():
    with pytest.raises(AssertionError, match="was fetched"):
        LaneOutput(0).index_of("VOLUME", _Exploding())


def test_naming_a_parameter_on_a_bare_block_says_what_is_missing():
    """A cell holds whatever the player put there, so the address cannot say."""
    with pytest.raises(TypeError, match="model="):
        Block(0, 1).index_of("GAIN", _Exploding())


# -- the one refusal ----------------------------------------------------------


def _lane_catalog():
    from tests.test_catalog import SAMPLE_XML, make_payload
    xml = SAMPLE_XML.replace("</Models>", """
<Category id="23" name="Lane Output">
  <Model blob="loc" id="23000" name="LaneOutputControl" internal="true">
    <Parameter defaultValue="0.769" max="1" min="0" name="VOLUME" type="float" units="dB"/>
    <Parameter defaultValue="0.5" max="1" min="0" name="PAN" type="float" units=""/>
    <Parameter defaultValue="0" max="1" min="0" name="MUTE" type="switch"/>
    <Parameter defaultValue="0" max="1" min="0" name="SOLO" type="switch"/>
  </Model>
</Category>
</Models>""")
    cat = catalog.parse_model_repo(make_payload(xml))
    return lambda: cat


@pytest.mark.parametrize("name", ["MUTE", "SOLO"])
def test_the_lane_output_refuses_its_two_unassignable_parameters(name):
    get = _lane_catalog()
    _, spec = LaneOutput(0).index_of(name, get)
    with pytest.raises(ControlNotDrivable) as refused:
        LaneOutput(0).refuse_if_unassignable(spec)
    assert refused.value.control.endswith(name)
    assert refused.value.evidence and refused.value.workaround


@pytest.mark.parametrize("name", ["VOLUME", "PAN"])
def test_the_lane_output_allows_the_other_two(name):
    get = _lane_catalog()
    _, spec = LaneOutput(0).index_of(name, get)
    LaneOutput(0).refuse_if_unassignable(spec)      # must not raise


def test_no_other_target_refuses_anything():
    """The refusal is a measured pair, not a rule - see LANE_OUTPUT_UNASSIGNABLE.

    Every other container took an assignment on hardware, on switch-typed
    parameters included, so anything else growing an `unassignable` needs its
    own evidence.
    """
    assert targets.LANE_OUTPUT_UNASSIGNABLE == ("MUTE", "SOLO")
    for target in (Block(0, 0), LaneInput(0), Mixer(0), Splitter(0), Tempo()):
        assert target.unassignable == (), f"{target!r} grew a refusal"


def test_the_lane_volume_speaks_db_and_pan_does_not():
    """VOLUME is the one placeholder range hiding a span measured at both ends."""
    get = _lane_catalog()
    index, spec = LaneOutput(0).index_of("VOLUME", get)
    assert LaneOutput(0).normalize(index, -3.1, get, spec) == pytest.approx(0.7096,
                                                                           abs=1e-4)
    index, spec = LaneOutput(0).index_of("PAN", get)
    assert LaneOutput(0).normalize(index, 0.25, get, spec) == pytest.approx(0.25)


# -- the spans measured for issue #26 -----------------------------------------
#
# Each of these is a screen reading against a wire value, taken at three or more
# well-separated points including both ends. The numbers in the asserts are the
# DISPLAYED values, so a wrong span fails here rather than shipping.


@pytest.mark.parametrize("target,index,real,wire", [
    # Block EQ band gains: dB = -12 + 24 * wire. Parametric-8 measured at four
    # points; Parametric-3 and Output Equalizer at both ends each.
    (Block(0, 1, 4000), 0, -12.0, 0.0),      # band 1, bottom end
    (Block(0, 1, 4000), 0, -9.6, 0.1),       # off-half: catches a curve
    (Block(0, 1, 4000), 0, 0.0, 0.5),
    (Block(0, 1, 4000), 30, 12.0, 1.0),      # band 7, top end
    (Block(0, 1, 4001), 0, 12.0, 1.0),       # Parametric-3
    (Block(0, 1, 4004), 0, -12.0, 0.0),      # Output Equalizer
    # The LEVEL family: dB = -40 + 52 * wire.
    (Mixer(0), 5, -24.4, 0.3),               # MIXER LEVEL
    (Mixer(0), 5, 12.0, 1.0),
    (Splitter(0), 3, -24.4, 0.3),            # LEVEL TO A
    (Splitter(0), 4, -3.1, 0.71),            # LEVEL TO B, the lane VOLUME's point
    (Splitter(0), 4, 12.0, 1.0),
    (Mixer(0), 0, -24.4, 0.3),               # LEVEL A, scene-following
    (Mixer(0), 2, -3.1, 0.71),               # LEVEL B, scene-following
    (LaneOutput(0), 0, -3.1, 0.71),          # the lane VOLUME itself
    (Tempo(), 0, 120.0, 0.4),                # bpm = 40 + 200 * wire
])
def test_a_measured_span_converts_to_the_wire_value_the_screen_showed(
        target, index, real, wire):
    assert target.normalize(index, real, _Exploding()) == pytest.approx(wire, abs=5e-4)


def test_a_measured_span_needs_no_catalog():
    """Keyed by index, so a conversion costs no round trip to the device."""
    for target, index, real in ((Mixer(0), 5, 0.0), (Splitter(0), 3, 0.0),
                                (LaneOutput(0), 0, 0.0), (Tempo(), 0, 120.0),
                                (Block(0, 1, 4000), 0, 0.0)):
        target.normalize(index, real, _Exploding())     # must not fetch


@pytest.mark.parametrize("target,index,real", [
    (LaneOutput(0), 0, -41.0),
    (Mixer(0), 5, 13.0),
    (Block(0, 1, 4000), 0, -12.5),
    (Tempo(), 0, 300.0),
])
def test_a_value_the_unit_has_no_position_for_is_refused(target, index, real):
    """Refused, not clamped. A clamped write looks like it worked."""
    with pytest.raises(ValueError, match="does not exist"):
        target.normalize(index, real, _Exploding())


def test_an_unmeasured_placeholder_still_refuses():
    """51 of 52 placeholder parameters have no measured span - #26.

    The cab LEVEL is the case to watch: it sits right beside parameters that DO
    convert now, so a table keyed too loosely would sweep it in.
    """
    from pyquadcortex.protocol import units

    assert (20000, 2) not in units.MEASURED_SPANS, "recorder OUT LEVEL is not measured"


def test_all_five_mixer_and_splitter_levels_are_measured():
    """Including the two that were briefly mistaken for undrivable.

    `LEVEL A` and `LEVEL B` are scene-following, so the wire carries eight
    values and a write lands on the ACTIVE scene. A reader taking
    `param_values[0]` reads scene A instead, and on a unit sitting in scene E
    that looks exactly like a refused write. Both measured once read correctly.
    """
    from pyquadcortex.protocol import units

    for key in ((11000, 0), (11000, 2), (11000, 5), (10004, 3), (10004, 4)):
        assert key in units.MEASURED_SPANS, key
        span = units.MEASURED_SPANS[key]
        assert (span.low, span.high, span.exponent) == (-40.0, 12.0, 1.0)


def test_real_on_a_bare_block_names_the_missing_model_not_the_catalog():
    """The conversion depends on WHICH block is in the cell.

    Worth its own message: blaming the catalog sends a reader looking for a
    missing catalog entry when the address simply never said what it points at.
    """
    with pytest.raises(TypeError, match="model="):
        Block(0, 1).normalize(0, 6.0, _Exploding())


#: Every cab LEVEL reading, screen against wire. The law is held to these rather
#: than to itself, so a future refit cannot quietly drift away from the unit.
CAB_LEVEL_READINGS = [
    (0.01, -21.8), (0.02, -19.1), (0.05, -14.9), (0.10, -11.1), (0.15, -8.6),
    (0.25, -5.2), (0.35, -2.8), (0.50, 0.0), (0.60, 1.5), (0.75, 3.4),
    (0.95, 5.5), (1.00, 6.0),
]


@pytest.mark.parametrize("wire,screen", CAB_LEVEL_READINGS)
def test_the_cab_taper_reproduces_every_reading(wire, screen):
    """A TAPERED control - the only one measured so far, and a warning.

    Three points in its upper half fit a straight line beautifully and are 12 dB
    wrong at wire 0.01. It was written up as having no closed form until four
    more points and an exponent produced a fit good to 0.034 dB. The display
    rounds to 0.1 dB, so that is the tolerance here.
    """
    from pyquadcortex.protocol import units

    span = units.MEASURED_SPANS[(12000, 2)]
    assert units.measured_from_wire(span, wire) == pytest.approx(screen, abs=0.05)


def test_the_cab_taper_was_confirmed_by_prediction_not_only_by_fitting():
    """0.15 and 0.60 were predicted from the law and then found on the unit.

    The lab used the same standard for the lane VOLUME's +3.2 dB at 0.830769:
    a curve fitted to its own points proves nothing about the points between.
    """
    from pyquadcortex.protocol import units

    span = units.MEASURED_SPANS[(12000, 2)]
    assert units.measured_from_wire(span, 0.15) == pytest.approx(-8.6, abs=0.05)
    assert units.measured_from_wire(span, 0.60) == pytest.approx(1.5, abs=0.05)


def test_a_taper_round_trips():
    from pyquadcortex.protocol import units

    span = units.MEASURED_SPANS[(12000, 2)]
    for wire in (0.05, 0.25, 0.5, 0.9):
        real = units.measured_from_wire(span, wire)
        assert units.measured_to_wire(span, real) == pytest.approx(wire, abs=1e-6)


def test_a_linear_span_is_a_taper_with_exponent_one():
    """Adding taper support must not have moved any linear conversion."""
    from pyquadcortex.protocol import units

    assert units.measured_to_wire((-40.0, 12.0), -3.1) == pytest.approx(
        units.measured_to_wire((-40.0, 12.0, 1.0), -3.1))
    assert units.measured_to_wire((-40.0, 12.0), -3.1) == pytest.approx(0.709615,
                                                                        abs=1e-6)


def test_the_cab_level_is_not_the_lane_level_scale():
    """It sits in the same `0..1 "dB"` bucket and is a different control.

    The lane, mixer and splitter levels are -40..+12, LINEAR, with unity at
    10/13. The cab LEVEL is tapered, has unity at 0.5, and reaches only +6 dB.
    Assuming the placeholder bucket implied one scale would have put a value
    20 dB wrong on the wire.
    """
    from pyquadcortex.protocol import units

    assert units.CAB_LEVEL_UNITY == 0.5
    assert units.UNITY_LEVEL != units.CAB_LEVEL_UNITY
    lane = units.MEASURED_SPANS[(23000, 0)]
    cab = units.MEASURED_SPANS[(12000, 2)]
    assert (lane.low, lane.high, lane.exponent) == (-40.0, 12.0, 1.0)
    assert cab.exponent != 1.0, "the cab entry is tapered"
    assert cab.high == 6.0 and lane.high == 12.0


def test_unconvertible_is_empty_and_that_is_a_result():
    """It held the cab LEVEL for an hour. Four more points emptied it."""
    from pyquadcortex.protocol import units

    assert units.UNCONVERTIBLE == {}


def test_a_cab_converts_through_the_shared_cabsim_layout():
    """A cab reports its OWN model id and uses the Default Cabsim layout.

    The catalog lists two parameters for a cab - its mic selectors - while the
    wire carries 22, so a measurement of the layout is what applies. Resolved
    through the catalog rather than a table of cab ids, so a cab this build has
    never seen works too.
    """
    from tests.test_catalog import SAMPLE_XML, make_payload
    from pyquadcortex.protocol import catalog, units

    xml = SAMPLE_XML.replace("</Models>", """
<Category id="21" name="Cabsim Bass (M)">
  <Model blob="c1" id="21005" name="212 Darkglass Neo (M)">
    <Parameter defaultValue="0" max="999" min="0" name="ir selector" type="string"/>
    <Parameter defaultValue="0" max="999" min="0" name="ir selector" type="string"/>
  </Model>
</Category>
<Category id="20" name="Neural Capture Internal">
  <Model blob="rec" id="20000" name="NC_Recorder" skip_self_test="true">
    <Parameter defaultValue="0" max="1" min="0" name="A" type="float"/>
    <Parameter defaultValue="0" max="1" min="0" name="B" type="float"/>
    <Parameter defaultValue="0.5" max="1" min="0" name="OUT LEVEL" type="float" units="dB"/>
  </Model>
</Category>
<Category id="12" name="Cabsim Guitar (M)">
  <Model blob="c0" id="12000" name="Default Cabsim" internal="true">
    <Parameter defaultValue="0" max="1" min="0" name="bypass" type="switch"/>
    <Parameter defaultValue="0" max="999" min="0" name="ir selector" type="string"/>
    <Parameter defaultValue="0.5" max="1" min="0" name="LEVEL" type="float" units="dB"/>
  </Model>
</Category>
</Models>""")
    cat = catalog.parse_model_repo(make_payload(xml))
    get = lambda: cat

    cab = Block(0, 5, 21005)
    assert (21005, 2) not in units.MEASURED_SPANS, "the cab's own id is not keyed"
    assert cab.normalize(2, -3.0, get) == pytest.approx(0.3400, abs=5e-4)

    # A NON-cab whose own span is unmeasured still refuses, so the alias is not
    # a back door that sweeps in every placeholder range near a measured one.
    with pytest.raises(ValueError, match="placeholder"):
        Block(0, 1, 20000).normalize(2, -3.0, get)


# -- `low` is a fit parameter; `floor` is where the knob actually stops --------


@pytest.mark.parametrize("key,asked", [
    ((12000, 2), -30.0),      # cab: the law extrapolates to -39.96, knob stops at -21.8
    ((12000, 10), -25.0),
    ((23000, 0), -40.0),      # lane: the scale's floor, not a knob position
    ((11000, 5), -39.9),
])
def test_a_value_below_the_knobs_floor_is_refused_not_silently_muted(key, asked):
    """Asking for -30 dB on a cab used to return wire 0.0005, which is OFF.

    The unit's quietest NUMERIC cab setting is -21.8 dB at wire 0.01, and the
    law's -39.96 is a fit parameter extrapolated below the knob's travel. So the
    caller got silence instead of attenuation, with no error - the exact
    silently-wrong-value failure this library is built to prevent.
    """
    from pyquadcortex.protocol import units

    with pytest.raises(ValueError, match="does not exist"):
        units.measured_to_wire(units.MEASURED_SPANS[key], asked)


def test_the_floor_is_the_lowest_wire_position_with_a_numeric_display():
    from pyquadcortex.protocol import units

    assert units.MEASURED_SPANS[(12000, 2)].floor == pytest.approx(-21.8, abs=0.05)
    assert units.MEASURED_SPANS[(23000, 0)].floor == pytest.approx(-39.5, abs=0.05)
    assert units.MEASURED_SPANS[(4000, 0)].floor == -12.0, "EQ gain has no Off detent"
    assert units.MEASURED_SPANS[(25000, 0)].floor == 40.0, "tempo has no Off detent"


def test_a_refusal_names_the_parameter_s_own_unit_and_hint():
    """One shared message told a tempo caller about an Off position it has not got."""
    from pyquadcortex.protocol import units

    with pytest.raises(ValueError, match="bpm") as tempo:
        units.measured_to_wire(units.MEASURED_SPANS[(25000, 0)], 300.0)
    assert "Off position" not in str(tempo.value)

    with pytest.raises(ValueError, match="Off position"):
        units.measured_to_wire(units.MEASURED_SPANS[(23000, 0)], -41.0)


def test_a_bool_is_refused_rather_than_written_as_a_level():
    """A bool IS an int in Python, so this would look deliberate."""
    from pyquadcortex.protocol import units

    with pytest.raises(TypeError, match="bool"):
        units.measured_to_wire(units.MEASURED_SPANS[(11000, 5)], True)


def test_measured_from_wire_refuses_a_wire_value_outside_zero_to_one():
    """A tapered span would otherwise return a complex number, silently."""
    from pyquadcortex.protocol import units

    with pytest.raises(ValueError, match="0..1"):
        units.measured_from_wire(units.MEASURED_SPANS[(12000, 2)], -0.1)


def test_the_worst_taper_error_is_what_the_docs_claim():
    """Stated in four places, so pin the real bound rather than a loose one."""
    from pyquadcortex.protocol import units

    span = units.MEASURED_SPANS[(12000, 2)]
    worst = max(abs(d - units.measured_from_wire(span, v))
                for v, d in CAB_LEVEL_READINGS)
    assert worst == pytest.approx(0.034, abs=0.001)


# -- the FX loop family: five parameters, two scales --------------------------

SEND_READINGS = [(0.01, -39.6), (0.10, -36.0), (0.50, -20.0), (0.75, -10.0),
                 (1.00, 0.0)]
RETURN_READINGS = [(0.01, -39.5), (0.10, -34.8), (0.50, -14.0), (1.00, 12.0)]


@pytest.mark.parametrize("wire,screen", SEND_READINGS)
def test_the_send_side_reproduces_every_reading(wire, screen):
    """`dB = -40 + 40 * wire`. It tops out at UNITY - a send cannot boost."""
    from pyquadcortex.protocol import units

    span = units.MEASURED_SPANS[(13000, 0)]
    assert units.measured_from_wire(span, wire) == pytest.approx(screen, abs=0.05)


@pytest.mark.parametrize("wire,screen", RETURN_READINGS)
def test_the_return_side_reproduces_every_reading(wire, screen):
    from pyquadcortex.protocol import units

    span = units.MEASURED_SPANS[(13002, 0)]
    assert units.measured_from_wire(span, wire) == pytest.approx(screen, abs=0.05)


def test_the_return_side_is_the_same_scale_as_the_lane_levels():
    """Four families now share -40..+12: lane, mixer, splitter and FX return."""
    from pyquadcortex.protocol import units

    assert units.MEASURED_SPANS[(13002, 0)] is units.MEASURED_SPANS[(23000, 0)]


def test_the_send_side_is_a_different_scale_from_the_return_side():
    """Five parameters across three blocks turned out to be TWO scales.

    Established by setting all five to one wire value and reading them together,
    twice, at 0.10 and 0.50 - so the grouping is not two laws crossing at a
    point. Pooling them would have put a send 12 dB out at full travel.
    """
    from pyquadcortex.protocol import units

    send = units.MEASURED_SPANS[(13000, 0)]
    ret = units.MEASURED_SPANS[(13002, 0)]
    assert send.high == 0.0 and ret.high == 12.0
    assert send.low == ret.low == -40.0


@pytest.mark.parametrize("key", [
    (13000, 0), (13000, 1), (13001, 0), (13001, 1), (13006, 0), (13006, 1),
    (13004, 0), (13005, 0), (13008, 0),
])
def test_every_send_side_parameter_is_keyed(key):
    from pyquadcortex.protocol import units

    assert units.MEASURED_SPANS[key] is units.MEASURED_SPANS[(13000, 0)]


@pytest.mark.parametrize("key", [
    (13002, 0), (13003, 0), (13007, 0), (13004, 1), (13005, 1), (13008, 1),
])
def test_every_return_side_parameter_is_keyed(key):
    from pyquadcortex.protocol import units

    assert units.MEASURED_SPANS[key] is units.MEASURED_SPANS[(13002, 0)]


def test_both_fx_loop_scales_have_the_off_detent():
    """Both read OFF at wire 0.0, so -40 dB is not a position either one has."""
    from pyquadcortex.protocol import units

    for key, floor in (((13000, 0), -39.6), ((13002, 0), -39.5)):
        span = units.MEASURED_SPANS[key]
        assert span.floor == pytest.approx(floor, abs=0.05)
        with pytest.raises(ValueError, match="does not exist"):
            units.measured_to_wire(span, -40.0)


def test_the_cab_taper_is_confirmed_across_three_categories():
    """Fitted on a Cabsim Bass; predicted and found on a Cabsim Guitar and in
    Parallax, a Bass Overdrive with its own embedded cab section.

    Parallax is keyed explicitly because it is NOT a Cabsim, so the category
    aliasing cannot reach it - and because it is the evidence that the taper
    belongs to the cab section rather than to cab models.
    """
    from pyquadcortex.protocol import units

    cab = units.MEASURED_SPANS[(12000, 2)]
    for key in ((3008, 16), (3008, 24)):
        assert units.MEASURED_SPANS[key] is cab
    assert units.measured_from_wire(cab, 0.50) == pytest.approx(0.0, abs=0.05)
    assert units.measured_from_wire(cab, 0.25) == pytest.approx(-5.2, abs=0.05)
    assert units.measured_from_wire(cab, 0.20) == pytest.approx(-6.75, abs=0.06)
    assert units.measured_from_wire(cab, 0.85) == pytest.approx(4.5, abs=0.05)


def test_the_recorder_is_recorded_as_off_limits_rather_than_merely_unmeasured():
    """Placing NC_Recorder to measure it CRASHED the unit and forced a reboot.

    Its `internal` and `hidden` flags are both false, which is what made it look
    placeable. Without this entry the next session sees an unmeasured parameter,
    reaches the same conclusion, and crashes the unit again.
    """
    from pyquadcortex.protocol import units

    assert (20000, 2) in units.DO_NOT_PROBE
    assert (20000, 2) not in units.MEASURED_SPANS
    assert "crash" in units.DO_NOT_PROBE[(20000, 2)].lower()


def test_the_legacy_splitter_view_shares_the_splitters_span():
    """`chain.splitter[]` (model 10000) is a read-only view of `combined_splitter`.

    One state seen through two model entries, so it is the same knob and the same
    span. Keyed rather than measured, because measuring it would be measuring the
    control that was already measured.
    """
    from pyquadcortex.protocol import units

    assert units.MEASURED_SPANS[(10000, 0)] is units.MEASURED_SPANS[(10004, 3)]
    assert units.MEASURED_SPANS[(10000, 1)] is units.MEASURED_SPANS[(10004, 4)]
