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
