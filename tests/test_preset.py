"""The preset surface, and the two questions it must answer without asking.

`has_unsaved_changes` and `is_current` are both required to read with no device
round trip, so the double here COUNTS what the model sends and the tests assert
on the count. A test that merely called the property and checked the value would
pass just as happily if answering had taken a second and a USB transfer.

The counting is proved to work at the bottom of this file, by marking an entry
stale and watching the read appear. Without that, "the model sent nothing" is a
claim about the double rather than about the model.
"""
import pathlib

import pytest

from pyquadcortex import protocol
from pyquadcortex.device import errors
from pyquadcortex.device.device import Device
from pyquadcortex.device.state import DeviceState
from pyquadcortex.device.translate import SceneLetter
from pyquadcortex.protocol import targets
from pyquadcortex.protocol.proto import Preset_pb2 as preset_pb
from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa
from waiting import wait_for

PRESETS = pathlib.Path(__file__).parent / "fixtures" / "presets"


def load(name="structural_preset.bin"):
    payload = preset_pb.BinaryPreset()
    payload.ParseFromString((PRESETS / name).read_bytes())
    return payload


class FakeClient:
    """The protocol connection, counting every question the model asks.

    Three methods, because those are the three the model's reads go through. A
    counter watching only one of them would report a silence it never checked.
    """

    def __init__(self, preset=None, scene=0, dirty=False, position=0):
        self.asked = []
        self.listeners = []
        self.switched = []
        self.fail_switch = None
        self._preset = preset if preset is not None else load()
        self._scene = scene
        self._dirty = dirty
        self._position = position
        self.catalog = FakeCatalog()

    def add_listener(self, listener):
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    def push(self, message):
        for listener in list(self.listeners):
            listener(message)

    # -- what the entries read through ---------------------------------------

    def read_current_preset_push(self, timeout=15.0):
        self.asked.append("preset")
        push = pa.RecallPresetMessage(action=pa.MessageAction.UPDATE,
                                      reason=pa.RecallPresetReason.OTHER)
        push.preset.CopyFrom(self._preset)
        self.push(push)
        return push

    def active_scene(self, timeout=10.0):
        self.asked.append("scene")
        self.push(pa.SceneMessage(action=pa.MessageAction.UPDATE,
                                  selected_scene=self._scene))
        return protocol.Scene(self._scene)

    def preset_dirty(self, timeout=5.0):
        self.asked.append("dirty")
        self.push(pa.PresetDirtyMessage(action=pa.MessageAction.UPDATE,
                                        is_dirty=self._dirty))
        return self._dirty

    def loaded_position(self, timeout=10.0):
        self.asked.append("loaded")
        push = pa.SetlistPositionMessage(
            action=pa.MessageAction.UPDATE, position=self._position,
            folder_key="/media/p4/Presets/My Presets", is_factory=False)
        self.push(push)
        return push

    def version(self, timeout=10.0):
        self.asked.append("identity")
        return pa.VersionMessage(action=pa.MessageAction.UPDATE,
                                 app_fw_version="d14e",
                                 device_serial_number="QCS0000001")

    # -- the one write this story has ----------------------------------------

    def switch_scene(self, scene):
        if self.fail_switch is not None:
            raise self.fail_switch
        self.switched.append(int(scene))
        self._scene = int(scene)

    def close(self):
        pass


class FakeCatalog:
    def __getitem__(self, model_id):
        return protocol.Model(id=model_id, name=f"device-{model_id}",
                              category="AMP", category_id=1)


def the_connect_burst(client, scene=0, dirty=False, position=0):
    """What the unit pushes about 10 s into a connection, in the measured order."""
    recall = pa.RecallPresetMessage(action=pa.MessageAction.UPDATE,
                                    reason=pa.RecallPresetReason.OTHER)
    recall.preset.CopyFrom(client._preset)
    client.push(recall)
    client.push(pa.SetlistPositionMessage(
        action=pa.MessageAction.UPDATE, position=position,
        folder_key="/media/p4/Presets/My Presets", is_factory=False))
    client.push(pa.PresetDirtyMessage(action=pa.MessageAction.UPDATE,
                                      is_dirty=dirty))
    client.push(pa.SceneMessage(action=pa.MessageAction.UPDATE,
                                selected_scene=scene))


@pytest.fixture
def warm():
    """A Device whose cache the connect burst has already filled."""
    client = FakeClient()
    state = DeviceState()
    state.listen_on(client)
    device = Device(client, _state=state)
    the_connect_burst(client)
    client.asked.clear()
    try:
        yield device, client
    finally:
        device.close()


@pytest.fixture
def cold():
    """A Device on a connection somebody else opened: no burst, nothing cached."""
    client = FakeClient()
    device = Device(client)
    try:
        yield device, client
    finally:
        device.close()


# -- the preset itself --------------------------------------------------------


def test_a_connected_device_always_has_a_preset(warm):
    device, client = warm
    assert device.preset is not None
    assert device.preset.name == "Structural Fixture"


def test_the_name_is_refused_when_the_unit_did_not_send_one(cold):
    """An absent string decodes as "" and reporting that would be a guess."""
    device, client = cold
    client._preset.ClearField("name")
    with pytest.raises(RuntimeError, match="no name"):
        device.preset.name


def test_the_same_preset_object_comes_back_while_it_is_the_loaded_one(warm):
    device, client = warm
    assert device.preset is device.preset


def test_a_recall_makes_the_device_build_a_fresh_preset(warm):
    device, client = warm
    held = device.preset
    client.push(pa.SetlistPositionMessage(
        action=pa.MessageAction.UPDATE, position=17,
        folder_key="/media/p4/Presets/My Presets", is_factory=False))
    assert device.preset is not held
    assert not held.is_current
    assert device.preset.is_current


def test_an_edit_does_not(warm):
    """Someone turning a knob is still the same preset - only the model's copy
    of its contents is behind."""
    device, client = warm
    held = device.preset
    client.push(pa.GridMessage(action=pa.MessageAction.UPDATE))
    assert device.preset is held
    assert held.is_current


def test_reading_the_preset_again_after_an_edit_asks_the_unit(warm):
    """The other half of the same decision: the identity is unchanged, so the
    object stands, but the contents are re-read."""
    device, client = warm
    device.preset.name
    assert client.asked == []
    client.push(pa.GridMessage(action=pa.MessageAction.UPDATE))
    device.preset.name
    assert client.asked == ["preset"]


# -- the two questions that must not cost a round trip ------------------------


def test_has_unsaved_changes_reads_from_a_warm_cache(warm):
    device, client = warm
    assert device.preset.has_unsaved_changes is False
    assert client.asked == [], "the burst already delivered this"


def test_is_current_reads_from_a_warm_cache(warm):
    device, client = warm
    preset = device.preset
    client.asked.clear()
    assert preset.is_current is True
    assert client.asked == []


def test_is_current_costs_nothing_even_when_the_contents_are_stale(warm):
    """The identity and the contents are different questions. Marking the
    contents must not make asking about the identity a round trip."""
    device, client = warm
    preset = device.preset
    client.push(pa.GridMessage(action=pa.MessageAction.UPDATE))
    client.asked.clear()
    assert preset.is_current is True
    assert client.asked == []


# -- scenes -------------------------------------------------------------------


def test_scenes_are_keyed_by_letter(warm):
    device, client = warm
    assert device.preset.scenes["B"].letter is SceneLetter.B
    assert device.preset.scenes[SceneLetter.H].letter is SceneLetter.H


def test_a_bare_scene_number_is_refused(warm):
    """Scene B is wire index 1, so a number here reads as either one."""
    device, client = warm
    with pytest.raises(TypeError):
        device.preset.scenes[1]


def test_a_letter_no_scene_carries_is_refused(warm):
    device, client = warm
    with pytest.raises(ValueError, match="A to H"):
        device.preset.scenes["I"]


def test_there_are_eight_scenes(warm):
    device, client = warm
    assert len(device.preset.scenes) == 8
    assert [s.letter for s in device.preset.scenes] == list("ABCDEFGH")


def test_a_scene_reads_its_name(warm):
    device, client = warm
    assert device.preset.scenes["B"].name == "Scene B"


def test_an_unlabelled_scene_reads_as_no_name(cold):
    device, client = cold
    client._preset.scene_labels[1] = protocol.SCENE_UNLABELLED
    assert device.preset.scenes["B"].name == ""


def test_the_active_scene_reads_from_the_cache(warm):
    device, client = warm
    assert device.preset.scenes.active.letter is SceneLetter.A
    assert client.asked == []


def test_the_active_scene_follows_the_unit(warm):
    device, client = warm
    client.push(pa.SceneMessage(action=pa.MessageAction.UPDATE, selected_scene=2))
    assert device.preset.scenes.active.letter is SceneLetter.C
    assert device.preset.scenes["C"].is_active
    assert not device.preset.scenes["A"].is_active


# -- the two grid bindings ----------------------------------------------------


def test_preset_blocks_is_live_bound_to_the_active_scene(warm):
    """The grid is HELD across the scene change on purpose. `preset.blocks` is a
    property, so asking again would build a fresh grid and a binding pinned at
    construction would pass - which is what the first version of this test did.
    """
    device, client = warm
    blocks = device.preset.blocks
    assert blocks.scene is SceneLetter.A
    client.push(pa.SceneMessage(action=pa.MessageAction.UPDATE, selected_scene=5))
    assert blocks.scene is SceneLetter.F


def test_scene_blocks_is_fixed_to_its_own_scene(warm):
    device, client = warm
    preset = device.preset
    fixed = preset.scenes["B"].blocks
    client.push(pa.SceneMessage(action=pa.MessageAction.UPDATE, selected_scene=5))
    assert fixed.scene is SceneLetter.B


def test_the_two_bindings_agree_on_the_active_scene(warm):
    device, client = warm
    preset = device.preset
    assert preset.blocks[1, 1] == preset.scenes["A"].blocks[1, 1]
    assert preset.blocks[1, 1].device == preset.scenes["A"].blocks[1, 1].device


def test_the_rows_read_through_the_preset(warm):
    device, client = warm
    rows = device.preset.rows
    assert len(rows) == 4
    assert rows[1].slots[1].device.id == 18010
    assert rows[1].input.source == protocol.Input.INPUT_1


def test_an_inactive_scene_s_grid_refuses_writes(warm):
    device, client = warm
    fixed = device.preset.scenes["B"].blocks
    assert not fixed.writable
    with pytest.raises(errors.InactiveSceneError, match=r"scene\.activate\(\)"):
        fixed.check_writable()


def test_reading_through_an_inactive_scene_works_fine(warm):
    device, client = warm
    fixed = device.preset.scenes["B"].blocks
    assert fixed[1, 1].device.id == 18010
    assert len(list(fixed)) == 14


# -- activate: the model's first write ----------------------------------------


def test_activate_sends_the_switch(warm):
    device, client = warm
    device.preset.scenes["C"].activate()
    assert client.switched == [protocol.Scene.C]


def test_activate_updates_the_cache_before_any_echo(warm):
    """Section 9's third rule. Waiting for the echo would make every write pay
    for information we almost always already have."""
    device, client = warm
    device.preset.scenes["C"].activate()
    assert device.state.cached("scene")["selected_scene"] == protocol.Scene.C
    assert device.preset.scenes.active.letter is SceneLetter.C


def test_a_matching_echo_confirms_the_write(warm):
    device, client = warm
    from pyquadcortex.device.watch import WatchOutcome

    watch = device.preset.scenes["C"].activate()
    client.push(pa.SceneMessage(action=pa.MessageAction.UPDATE, selected_scene=2))
    assert watch.settled(timeout=2.0)
    assert watch.outcome is WatchOutcome.CONFIRMED
    assert watch.disagreement is None


def test_an_echo_the_unit_disagrees_with_is_reported(warm):
    """A write the unit contradicted is a bug in our code, now with a name and a
    location - and the entry is marked, because any OTHER field we sent is still
    sitting in the cache on our say-so."""
    from pyquadcortex.device.watch import WatchOutcome

    device, client = warm
    watch = device.preset.scenes["C"].activate()
    client.push(pa.SceneMessage(action=pa.MessageAction.UPDATE, selected_scene=6))
    assert watch.settled(timeout=2.0)
    assert watch.outcome is WatchOutcome.DIFFERENT
    assert watch.disagreement[0] == "selected_scene"
    assert device.state.needs_read("scene")


def test_a_write_that_never_reaches_the_unit_marks_the_scene(warm):
    """Our copy would otherwise be the only place that value exists."""
    device, client = warm
    client.fail_switch = protocol.DeviceLostError("the cable came out")
    with pytest.raises(protocol.DeviceLostError):
        device.preset.scenes["C"].activate()
    assert device.state.needs_read("scene")


def test_activating_the_scene_already_active_still_writes(warm):
    """The unit is the authority on what is active. Skipping the write because
    the model believes it is already there would make the model the authority
    instead, and it would be wrong the first time the two disagreed."""
    device, client = warm
    assert device.preset.scenes.active.letter is SceneLetter.A
    device.preset.scenes["A"].activate()
    assert client.switched == [protocol.Scene.A]


def test_activating_makes_that_scene_s_grid_writable(warm):
    """The refusal names a step; this is the step working."""
    device, client = warm
    fixed = device.preset.scenes["B"].blocks
    assert not fixed.writable
    device.preset.scenes["B"].activate()
    assert fixed.writable
    fixed.check_writable()


# -- a closed device answers nothing -----------------------------------------


def test_a_closed_device_refuses_its_preset(warm):
    device, client = warm
    device.close()
    with pytest.raises(RuntimeError, match="closed"):
        device.preset


def test_a_preset_held_across_close_answers_nothing(warm):
    device, client = warm
    preset = device.preset
    device.close()
    with pytest.raises(RuntimeError, match="closed"):
        preset.name


def test_a_closed_device_stops_delivering_events(warm):
    device, client = warm
    device.events.subscribe(lambda e: None)
    device.close()
    with pytest.raises(RuntimeError, match="closed"):
        device.events.subscribe(lambda e: None)


# -- the counter that the "no round trip" tests rest on ----------------------


def test_the_counter_can_see_a_read(warm):
    """Every "asked == []" above is worthless if this fails."""
    device, client = warm
    device.state.mark_for_reread("dirty", "proving the counter works")
    device.preset.has_unsaved_changes
    assert client.asked == ["dirty"]


def test_a_cold_device_really_does_ask(warm):
    """And the same, for a connection with no burst behind it."""
    client = FakeClient()
    device = Device(client)
    try:
        assert device.preset.name == "Structural Fixture"
        assert "loaded" in client.asked and "preset" in client.asked
    finally:
        device.close()


def test_an_edit_reaches_a_subscriber(warm):
    device, client = warm
    seen = []
    device.events.subscribe(seen.append)
    client.push(pa.GridMessage(action=pa.MessageAction.UPDATE))
    wait_for(seen, 1)
    assert seen[0].part == "preset"


# -- a preset that is no longer loaded answers nothing ------------------------
#
# The failure this closes: `is_current` went False while `name`, `blocks` and
# the rest went on reading live state - so a held Preset reported the NEW
# preset's contents. Worse than reporting the old ones, and the exact shape
# `Device._check_open` refuses for a closed connection.


def recall_elsewhere(client, name="SOMETHING ELSE", position=99):
    """The unit loading a different preset, as it really announces it."""
    other = load()
    other.name = name
    client._preset = other
    client.push(pa.SetlistPositionMessage(
        action=pa.MessageAction.UPDATE, position=position,
        folder_key="/media/p4/Presets/My Presets", is_factory=False))
    recall = pa.RecallPresetMessage(action=pa.MessageAction.UPDATE,
                                    reason=pa.RecallPresetReason.OTHER)
    recall.preset.CopyFrom(other)
    client.push(recall)


def test_a_stale_preset_does_not_report_the_new_presets_name(warm):
    device, client = warm
    held = device.preset
    assert held.name == "Structural Fixture"
    recall_elsewhere(client)
    assert not held.is_current
    with pytest.raises(RuntimeError, match="no longer the one on the grid"):
        held.name


@pytest.mark.parametrize("read", [
    lambda p: p.name,
    lambda p: p.wire,
    lambda p: p.has_unsaved_changes,
    lambda p: p.active_scene,
    lambda p: p.blocks[1, 1],
    lambda p: list(p.blocks),
    lambda p: p.rows[1].slots[1],
    lambda p: p.rows[1].input.source,
    lambda p: p.scenes["B"].name,
    lambda p: p.scenes.active,
    lambda p: p.scenes["B"].blocks[1, 1],
], ids=["name", "wire", "has_unsaved_changes", "active_scene", "blocks",
        "iterate blocks", "slot", "row input", "scene name", "active scene",
        "scene blocks"])
def test_every_read_through_a_stale_preset_refuses(warm, read):
    device, client = warm
    held = device.preset
    recall_elsewhere(client)
    with pytest.raises(RuntimeError, match="no longer the one on the grid"):
        read(held)


def test_a_stale_preset_still_answers_is_current(warm):
    """The one property that must not raise - asking whether an object is
    still good is how a caller avoids every error above."""
    device, client = warm
    held = device.preset
    recall_elsewhere(client)
    assert held.is_current is False


def test_a_stale_preset_still_has_a_repr(warm):
    """repr() is called by debuggers and logging, so it must never raise."""
    device, client = warm
    held = device.preset
    recall_elsewhere(client)
    assert "Preset" in repr(held)


def test_activating_a_scene_through_a_stale_preset_is_refused(warm):
    """The audible half. Without this, a Scene reached through a held Preset
    switches the scene of whatever is loaded NOW."""
    device, client = warm
    scene = device.preset.scenes["B"]
    recall_elsewhere(client)
    with pytest.raises(RuntimeError, match="no longer the one on the grid"):
        scene.activate()
    assert client.switched == [], "the unit was told to switch anyway"


def test_the_device_hands_back_a_working_preset_after_the_recall(warm):
    """The refusal has to leave the caller somewhere to go."""
    device, client = warm
    device.preset
    recall_elsewhere(client)
    assert device.preset.name == "SOMETHING ELSE"
    assert device.preset.is_current


# -- block identity survives what the model does to the payload ---------------


def test_two_handles_on_a_cell_stay_equal_across_a_re_read(warm):
    """Keyed on the preset, not on the payload. The model replaces the payload
    on every re-read, so keying on it meant a Block put in a set was silently
    lost the moment somebody touched the unit."""
    device, client = warm
    preset = device.preset
    before = preset.blocks[1, 1]
    held = {before}
    client.push(pa.GridMessage(action=pa.MessageAction.UPDATE))
    after = preset.blocks[1, 1]
    assert after == before
    assert after in held


def test_hashing_a_block_never_asks_the_unit(warm):
    """`hash()` reaching through to the cache could issue a 21 KB read with a
    fifteen-second timeout, and raise on a closed device."""
    device, client = warm
    block = device.preset.blocks[1, 1]
    device.state.mark_for_reread("preset", "this test")
    client.asked.clear()
    hash(block)
    block == block
    assert client.asked == []


# -- reading back an expression assignment ------------------------------------


def _preset(name):
    p = preset_pb.BinaryPreset()
    p.ParseFromString(
        (pathlib.Path(__file__).parent / "fixtures" / "presets"
         / f"{name}.bin").read_bytes())
    return p


def test_expression_assignments_finds_the_one_the_fixtures_carry():
    """The library could write a pedal assignment and not read one back.

    All three committed presets carry the same one, on the block at row 0
    column 1, over the full sweep.
    """
    for name in ("structural_preset", "scene_preset", "split_preset"):
        found = protocol.expression_assignments(_preset(name))
        assert len(found) == 1, f"{name}: {found}"
        one = found[0]
        assert (one.target.row, one.target.column) == (0, 1)
        assert one.pedal == 1
        assert (float(one.minimum), float(one.maximum)) == (0.0, 1.0)
        assert not one.reversed


def test_an_unassigned_parameter_is_absent_rather_than_pedal_zero():
    """`expression: 0` is what `clear_expression` writes, and the field has no
    presence - so "never touched" and "pedal removed" are the same bytes."""
    p = _preset("structural_preset")
    every = sum(len(m.params) for ch in p.chains for m in ch.models)
    assert every > 50, "the fixture should carry plenty of unassigned params"
    assert len(protocol.expression_assignments(p)) == 1


def test_the_index_reported_is_the_position_not_the_wire_field():
    """The trap that would have made every assignment read as knob 0.

    `params[].index` has no presence and is 0 for all 576 parameters across the
    committed fixtures, so a reader trusting it reports the first knob every
    time. This moves the assignment to the THIRD parameter of its block and
    checks the reader follows.
    """
    p = _preset("structural_preset")
    model = p.chains[0].models[1]
    assert len(model.params) >= 2
    while len(model.params) < 3:
        model.params.add()
    for prm in model.params:
        prm.expression = 0
    third = model.params[2]
    third.expression = 2
    third.expression_min, third.expression_max = 0.8, 0.2

    found = protocol.expression_assignments(p)
    assert len(found) == 1
    assert found[0].param_index == 2, "reported the wire's index, not the position"
    assert found[0].pedal == 2
    assert found[0].reversed, "minimum above maximum reverses the pedal"


def test_a_lane_output_assignment_comes_back_as_a_lane_output():
    """Every container the unit has, not just blocks - `set_expression` was
    confirmed on all of them, so the reader has to cover all of them."""
    p = _preset("structural_preset")
    entry = p.chains[2].output_control[0]
    while len(entry.params) < 1:
        entry.params.add()
    entry.params[0].expression = 2
    entry.params[0].expression_min = 0.0
    entry.params[0].expression_max = 0.75

    found = [a for a in protocol.expression_assignments(p)
             if isinstance(a.target, targets.LaneOutput)]
    assert len(found) == 1
    assert found[0].target.row == 2
    assert found[0].pedal == 2
