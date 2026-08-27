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


def test_speaking_real_units_does_need_one():
    """Even for TEMPO, which used to be served by a hand-measured span.

    That shortcut existed because the catalog was believed to publish nothing
    usable for TEMPO. It publishes min="MIN_TEMPO", max="MAX_TEMPO" and
    steps="201", so the shortcut is gone and this path fetches like any other.
    `protocol.bpm_to_tempo` is the route for a caller with no device.
    """
    with pytest.raises(AssertionError, match="was fetched"):
        Tempo().normalize(0, 120.0, _Exploding())


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
    <Parameter defaultValue="0.769" max="MAX_MIXER_DB" min="MIN_MIXER_DB" name="VOLUME" type="float" units="dB" min_string="OFF"/>
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


# -- addressing a parameter that needs converting -----------------------------
#
# The NUMBERS live in tests/test_scales.py, held against every reading taken off
# the unit. What is tested here is the addressing: that a target reaches the
# right catalog parameter, and that the paths which must not fetch a catalog
# still do not.


def _scale_catalog():
    """A catalog carrying the containers whose bounds the device names."""
    from tests.test_catalog import SAMPLE_XML, make_payload
    xml = SAMPLE_XML.replace("</Models>", """
<Category id="23" name="Lane Output">
  <Model blob="loc" id="23000" name="LaneOutputControl" internal="true">
    <Parameter defaultValue="0.769" max="MAX_MIXER_DB" min="MIN_MIXER_DB" name="VOLUME" type="float" units="dB" min_string="OFF"/>
    <Parameter defaultValue="0.5" max="1" min="0" name="PAN" type="float" units=""/>
  </Model>
</Category>
<Category id="10" name="Splitter">
  <Model blob="spl" id="10004" name="Splitter" internal="true">
    <Parameter defaultValue="0" max="1" min="0" name="TYPE" steps="3" type="comboBox" stepNames="Y,A/B,Crossover"/>
    <Parameter defaultValue="0" max="1" min="0" name="BYPASS" type="switch"/>
    <Parameter defaultValue="0.5" max="1" min="0" name="PAN" type="float"/>
    <Parameter defaultValue="0.769" max="MAX_MIXER_DB" min="MIN_MIXER_DB" name="LEVEL TO A" type="float" units="dB" min_string="OFF"/>
    <Parameter defaultValue="0.769" max="MAX_MIXER_DB" min="MIN_MIXER_DB" name="LEVEL TO B" type="float" units="dB" min_string="OFF"/>
    <Parameter defaultValue="400.0" max="MAX_EQ_FREQ" min="MIN_EQ_FREQ" name="FREQUENCY" type="float" units="Hz" skew="0.17722914651016206" steps="200"/>
  </Model>
</Category>
<Category id="12" name="Cabsim Guitar (M)">
  <Model blob="cab" id="12000" name="Default Cabsim" internal="true">
    <Parameter defaultValue="0" max="1" min="0" name="bypass" type="switch"/>
    <Parameter defaultValue="0" max="999" min="0" name="IR PATH SLOT 1" type="string"/>
    <Parameter defaultValue="0.5" max="MAX_CABSIM_DB" min="MIN_CABSIM_DB" name="MIC 1 LEVEL" type="float" units="dB" skew="4.9594844" min_string="OFF"/>
  </Model>
  <Model blob="dgn" id="21005" name="212 Darkglass Neo (M)">
    <Parameter defaultValue="0" max="999" min="0" name="ir selector" type="string"/>
  </Model>
</Category>
""" + "</Models>")
    cat = catalog.parse_model_repo(make_payload(xml))
    return lambda: cat


@pytest.mark.parametrize("target,index,real,wire", [
    (LaneOutput(0), 0, -3.1, 0.7096),        # the lane VOLUME, linear
    (LaneOutput(0), 0, 0.0, 0.76923),        # unity, 10/13
    (Splitter(0), 3, -24.4, 0.30),           # LEVEL TO A, the same family
    (Splitter(0), 4, 12.0, 1.00),            # LEVEL TO B, top end
    (Splitter(0), 5, 400.0, 0.49548),        # the crossover, a taper
    (Mixer(0), 0, 12.0, 1.00),               # MIXER LEVEL
    (Tempo(), 0, 120.0, 0.40),               # bpm
    (Block(0, 1, 5005), 0, -24.0, 0.50),     # a plain literal range
])
def test_a_target_converts_through_the_parameter_it_addresses(
        target, index, real, wire):
    assert target.normalize(index, real, _scale_catalog()) == pytest.approx(
        wire, abs=5e-4)


def test_an_indexed_write_by_value_still_needs_no_catalog():
    """The regression that matters for cost, pinned.

    The catalog comes FROM the device, so fetching one costs a round trip. An
    early draft made every write fetch one; it broke 27 tests that had never
    touched a device. Converting real units DOES need the catalog now - that is
    the honest price of reading the device's own scale - but addressing by index
    and writing a wire value must stay free.
    """
    for target, index in ((Mixer(0), 5), (Splitter(0), 3), (LaneOutput(0), 0),
                          (Tempo(), 0), (Block(0, 1, 4000), 0)):
        assert target.index_of(index, _Exploding()) == (index, None)


@pytest.mark.parametrize("target,index,real", [
    (LaneOutput(0), 0, -41.0),
    (Mixer(0), 0, 13.0),
    (Block(0, 1, 5005), 0, -61.0),
    (Tempo(), 0, 300.0),
])
def test_a_value_the_unit_has_no_position_for_is_refused(target, index, real):
    """Refused, not clamped. A clamped write looks like it worked."""
    with pytest.raises(ValueError, match="does not exist"):
        target.normalize(index, real, _scale_catalog())


def test_a_value_below_the_knobs_floor_is_refused_not_silently_muted():
    """The blocking bug this floor exists to prevent.

    A cab LEVEL's law runs to -40 dB but its quietest real position is -21.8 dB.
    Without the floor, asking for -30 dB converts to wire 0.0005 and MUTES the
    microphone: a write that looks like it worked and did something else.
    """
    with pytest.raises(ValueError, match="does not exist there"):
        Block(0, 5, 12000).normalize(2, -30.0, _scale_catalog())


def test_a_refusal_names_the_parameters_own_floor_and_the_way_out():
    with pytest.raises(ValueError) as excinfo:
        LaneOutput(0).normalize(0, -39.9, _scale_catalog())
    message = str(excinfo.value)
    assert "-39.5" in message and "dB" in message
    assert "the Off position" in message


def test_a_bool_is_refused_rather_than_written_as_a_level():
    """A bool IS an int in Python, so True would quietly write the top end."""
    with pytest.raises(TypeError, match="bool IS an int"):
        LaneOutput(0).normalize(0, True, _scale_catalog())


def test_an_unmeasured_bound_still_refuses():
    """The recorder's OUT LEVEL, and it is now the only one left.

    It sits right beside parameters that DO convert, so a table keyed too
    loosely would sweep it in. Reading the whole catalog resolved every other
    symbolic bound; this one cannot be read because placing the block to see
    the screen crashes the unit.
    """
    from pyquadcortex.protocol import units

    assert "MIN_INPUT_TRIM" in units.UNMEASURED_BOUNDS
    assert (20000, 2) in units.DO_NOT_PROBE


def test_real_on_a_bare_block_names_the_missing_model_not_the_catalog():
    """The conversion depends on WHICH block is in the cell.

    Worth its own message: blaming the catalog sends a reader looking for a
    missing catalog entry when the address simply never said what it points at.
    """
    with pytest.raises(TypeError, match="model="):
        Block(0, 1).normalize(0, 6.0, _Exploding())


def test_a_cab_converts_through_the_shared_cabsim_layout():
    """The catalog UNDER-DESCRIBES most cabs, so they borrow `Default Cabsim`.

    A cab model lists its mic selectors while the wire carries the whole layout.
    The taper it borrows is confirmed on three blocks in three different
    categories, and the catalog agrees: every one of those LEVEL entries carries
    the same skew and the same MIN_CABSIM_DB bound.
    """
    get = _scale_catalog()
    # 21005 describes ONE parameter, so index 2 is not its own.
    assert Block(0, 5, 21005).spec_at(2, get) is None
    borrowed = Block(0, 5, 21005)._layout_spec(2, get)
    assert borrowed.name == "MIC 1 LEVEL"
    assert Block(0, 5, 21005).normalize(2, -3.0, get) == pytest.approx(0.3400,
                                                                      abs=5e-4)


def test_a_non_cab_does_not_borrow_the_cabsim_layout():
    """The alias is not a back door into every model near a cab."""
    get = _scale_catalog()
    assert Block(0, 1, 5005)._layout_spec(2, get) is None
