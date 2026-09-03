"""Tests for the high-level QuadCortex client (pyquadcortex.protocol.client).

The client builds protobuf messages and hands them to a transport-like object
exposing ``send(message)``, ``send_sequence(messages, ...)`` and
``request(message, timeout=...)``. It never
touches hidapi or framing directly. These tests inject a FakeTransport so the
client can be exercised without a device.
"""

import itertools

import pytest

from pyquadcortex.protocol import catalog, client
from pyquadcortex.protocol.enums import (Footswitch, Input, Instrument, MidiSource,
                                Output, SceneBypassBehavior, Setlist, TempoMode)
from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa
from pyquadcortex.protocol.proto import Preset_pb2 as preset
from pyquadcortex.protocol.targets import (Block, LaneInput, LaneOutput, Mixer, Splitter, Tempo)
from pyquadcortex.protocol import units as units_module
from pyquadcortex.protocol.errors import ControlNotDrivable
from pyquadcortex.protocol.values import Db, Encoded, Hertz, Milliseconds, Real


class FakeTransport:
    """Records outbound messages and returns canned responses by class name."""

    def __init__(self, canned=None):
        self.sent = []
        self.canned = canned or {}
        self.broadcast = None
        self.last_match = None  # the match predicate read_preset passed, if any
        self.listeners = []
        self._ids = itertools.count(1)

    def send(self, msg):
        self.sent.append(msg)

    def send_sequence(self, messages, *, interval=0.0, delay=0.0):
        self.sent.extend(messages)
        self.sequence_timing = (delay, interval)

    def request(self, msg, timeout=5.0):
        self.sent.append(msg)
        return self.canned.get(type(msg).__name__)

    def next_request_id(self):
        return next(self._ids)

    def await_broadcast(self, expected_class, trigger, timeout=40.0, match=None):
        self.last_match = match
        trigger()
        return self.broadcast

    def add_listener(self, listener):
        self.listeners.append(listener)
        return lambda: self.remove_listener(listener)

    def remove_listener(self, listener):
        if listener in self.listeners:
            self.listeners.remove(listener)
            return True
        return False


# -- 5.1 read_current_preset -------------------------------------------------


def test_read_preset_recalls_then_returns_broadcast_preset():
    # read_preset recalls the slot (a SetlistPosition UPDATE) and returns the
    # BinaryPreset from the device's RecallPreset broadcast.
    push = pa.RecallPresetMessage(preset=preset.BinaryPreset(name="Test Patch"))
    fake = FakeTransport()
    fake.broadcast = push
    qc = client.QuadCortex(fake)
    p = qc.read_preset("/media/p4/Presets/My Presets", 218)
    assert p.name == "Test Patch"
    # The trigger recalled the slot.
    assert isinstance(fake.sent[-1], pa.SetlistPositionMessage)
    assert fake.sent[-1].position == 218


def test_read_preset_correlates_the_push_by_request_id():
    # Confirmed on hardware: a host recall echoes its request_id on the
    # RecallPreset push, while the unsolicited seed push carries none. read_preset
    # must recall WITH a request_id and match the push by it, so it never returns
    # a stale/lagging push (the lag-by-one bug).
    push = pa.RecallPresetMessage(preset=preset.BinaryPreset(name="Right One"))
    fake = FakeTransport()
    fake.broadcast = push
    qc = client.QuadCortex(fake)
    qc.read_preset("/media/p4/Presets/My Presets", 218)
    recall = fake.sent[-1]
    assert isinstance(recall, pa.SetlistPositionMessage)
    assert recall.HasField("request_id")
    rid = recall.request_id
    # The match predicate accepts a push echoing that id and rejects one without.
    assert fake.last_match is not None
    accepted = pa.RecallPresetMessage()
    accepted.request_id = rid
    assert fake.last_match(accepted) is True
    seed = pa.RecallPresetMessage()  # unsolicited seed: no request_id
    assert fake.last_match(seed) is False
    wrong = pa.RecallPresetMessage()
    wrong.request_id = rid + 1
    assert fake.last_match(wrong) is False


# -- listing a setlist --------------------------------------------------------


def test_list_presets_sends_file_read_and_returns_entries_in_slot_order():
    listing = pa.FileMessage()
    listing.folder.key = str(Setlist.USER)
    for index, name in ((2, "Third"), (0, "First"), (1, "Second")):
        pd = listing.folder.files.add()
        pd.index = index
        pd.name = name
    fake = FakeTransport()
    fake.broadcast = listing
    qc = client.QuadCortex(fake)

    entries = qc.list_presets(Setlist.USER)

    # It triggers a File READ (no host-initiated "list" request exists).
    assert [type(m).__name__ for m in fake.sent] == ["FileMessage"]
    assert fake.sent[0].action == pa.MessageAction.READ
    # Returned in slot order, not wire order.
    assert [pd.name for pd in entries] == ["First", "Second", "Third"]


def test_list_presets_omits_empty_slots_by_default():
    # The device reports a setlist as all 256 slots; most are usually empty. The
    # default should be the occupied ones, with the full map available on request.
    listing = pa.FileMessage()
    listing.folder.key = str(Setlist.USER)
    for index, name in ((0, ""), (1, "Real Preset"), (2, ""), (3, "Another")):
        pd = listing.folder.files.add()
        pd.index = index
        if name:
            pd.name = name
    fake = FakeTransport()
    fake.broadcast = listing
    qc = client.QuadCortex(fake)

    assert [pd.name for pd in qc.list_presets(Setlist.USER)] == ["Real Preset", "Another"]
    full = qc.list_presets(Setlist.USER, include_empty=True)
    assert len(full) == 4
    assert [pd.index for pd in full] == [0, 1, 2, 3]


def test_list_presets_matches_the_factory_listing_despite_the_trailing_slash():
    # Setlist.FACTORY carries a trailing slash because RECALLS require it, but the
    # device reports that same folder's LISTING key without one. A naive
    # startswith() match therefore never fires and list_presets times out. This
    # regression test locks in the normalized comparison.
    assert str(Setlist.FACTORY).endswith("/"), "premise: the recall path has a slash"
    fake = FakeTransport()
    fake.broadcast = pa.FileMessage()
    qc = client.QuadCortex(fake)
    qc.list_presets(Setlist.FACTORY)

    as_device_sends_it = pa.FileMessage()
    as_device_sends_it.folder.key = "/opt/neuraldsp/Factory Library"   # no slash
    as_device_sends_it.folder.files.add().index = 0
    assert fake.last_match(as_device_sends_it) is True


def test_list_presets_ignores_listings_for_other_setlists():
    fake = FakeTransport()
    fake.broadcast = pa.FileMessage()
    qc = client.QuadCortex(fake)
    qc.list_presets(Setlist.FACTORY)

    # The match predicate must accept only the requested setlist, and only a
    # listing that actually carries entries (the device pushes empty ones).
    wanted = pa.FileMessage()
    wanted.folder.key = str(Setlist.FACTORY)
    wanted.folder.files.add().index = 0
    assert fake.last_match(wanted) is True

    other = pa.FileMessage()
    other.folder.key = str(Setlist.USER)
    other.folder.files.add().index = 0
    assert fake.last_match(other) is False

    empty = pa.FileMessage()
    empty.folder.key = str(Setlist.FACTORY)
    assert fake.last_match(empty) is False


# -- input rerouting (Phase B) ------------------------------------------------


def test_input_port_constants_match_schema_enum():
    # Chain.in_portid uses GainCalInputPortParameter.InputPortId verbatim -
    # confirmed exhaustively on hardware (ids 0-14 accepted; 15 rejected).
    InP = pa.GainCalInputPortParameter.InputPortId
    assert Input.INPUT_1 == InP.INPUT_1
    assert Input.INPUT_2 == InP.INPUT_2
    assert Input.INPUT_1_2 == InP.INPUT_1_2
    assert Input.RETURN_1 == InP.RETURN_1
    assert Input.RETURN_2 == InP.RETURN_2
    assert Input.RETURN_1_2 == InP.RETURN_1_2
    assert Input.PREV_ROW == InP.PREV_ROW
    assert Input.USB_5 == InP.USB_IN_5
    assert Input.USB_8 == InP.USB_IN_8
    assert Input.USB_5_6 == InP.USB_IN_5_6
    assert Input.USB_7_8 == InP.USB_IN_7_8
    assert Input.SIDECHAIN_BUFFER == InP.SIDECHAIN_BUFFER
    # Anchors confirmed against the unit's own display: Input 1, Input 2, Return 1.
    assert (Input.INPUT_1, Input.INPUT_2, Input.RETURN_1) == (1, 2, 4)


def test_output_port_constants_match_schema_enum():
    # Chain.out_portid uses GainCalOutputPortParameter.OutputPortId verbatim -
    # anchored by a preset read back from the unit (out 4="Output 1",
    # 1="Output 1/2") and spot-confirmed on hardware (2="Output 3/4",
    # 3="Send 1/2", 10="USB 5").
    OutP = pa.GainCalOutputPortParameter.OutputPortId
    assert Output.XLR_1_2 == OutP.XLR_1_2      # "Output 1/2"
    assert Output.XLR_1 == OutP.XLR_1          # "Output 1"
    assert Output.OUT_3_4 == OutP.OUTPUT_3_4       # "Output 3/4"
    assert Output.SEND_1_2 == OutP.SEND_1_2    # "Send 1/2"
    assert Output.USB_5 == OutP.USB_OUT_5      # "USB 5"
    assert Output.USB_7_8 == OutP.USB_OUT_7_8
    assert Output.MULTIPLE == OutP.MULTIPLE_OUTS  # factory Cali's output


def test_instrument_tag_constants():
    # ProductData.instrument tag, confirmed against the factory library:
    # 1=guitar (block 0-15), 2=bass (16-23, 191-231), 4=vocal (AutoWah, Vocal 58,
    # Vocal Synth). Values are powers of two (3 unused) - likely bit flags.
    assert Instrument.GUITAR == 1
    assert Instrument.BASS == 2
    assert Instrument.VOCAL == 4


def test_input_chain_rows_returns_rows_on_from_port():
    # Grid row == chain index when chains carry no explicit row (CONFIRMED via
    # the 28A read-back: chain[0]=Input 2 on row 1, chain[2]=Input 1 on row 3).
    p = preset.BinaryPreset()
    p.chains.add().in_portid = Input.INPUT_1  # index 0
    p.chains.add().in_portid = 0               # index 1 - internally fed
    p.chains.add().in_portid = Input.INPUT_1  # index 2
    assert client.input_chain_rows(p, Input.INPUT_1) == [0, 2]


def test_input_chain_rows_honors_explicit_row():
    p = preset.BinaryPreset()
    c = p.chains.add()
    c.in_portid = Input.INPUT_1
    c.row = 3
    assert client.input_chain_rows(p, Input.INPUT_1) == [3]


def test_set_chain_input_sends_row_keyed_sparse_grid_update():
    # Confirmed on hardware: only a Grid UPDATE carrying a chain with an
    # explicit `row` re-points that row's input; a full preset whose chains lack
    # `row` is NOT applied. So set_chain_input sends exactly one chain {row,
    # in_portid} - the minimal proven shape.
    qc = client.QuadCortex(FakeTransport())
    qc.set_chain_input(row=2, in_portid=Input.RETURN_1)
    sent = qc._t.sent[-1]
    assert isinstance(sent, pa.GridMessage)
    assert sent.action == pa.MessageAction.UPDATE
    assert len(sent.preset.chains) == 1
    ch = sent.preset.chains[0]
    assert ch.row == 2
    assert ch.in_portid == Input.RETURN_1


def test_set_param_sends_row_column_keyed_grid_update():
    # CONFIRMED capture shape: Grid{UPDATE, preset{chains{row, models{column,
    # params{index, param_values{float_value}}}}}}.
    qc = client.QuadCortex(FakeTransport())
    qc.set_param(Block(0, 1), 1, Encoded(0.4553))
    sent = qc._t.sent[-1]
    assert isinstance(sent, pa.GridMessage)
    assert sent.action == pa.MessageAction.UPDATE
    ch = sent.preset.chains[0]
    assert ch.row == 0
    model = ch.models[0]
    assert model.column == 1
    param = model.params[0]
    assert param.index == 1
    assert abs(param.param_values[0].float_value - 0.4553) < 1e-6


def test_set_param_sends_exactly_one_param_value():
    # This replaces a test that asserted param_values was "extended to index 2"
    # for scene=2. The message was built exactly as intended - but the intent was
    # wrong: the padding entries carried protobuf defaults, the device reads index
    # 0, and so the parameter was zeroed in every scene. A construction test
    # cannot catch that. See test_set_param_refuses_a_nonzero_scene.
    qc = client.QuadCortex(FakeTransport())
    qc.set_param(Block(0, 1), 1, Encoded(0.5))
    param = qc._t.sent[-1].preset.chains[0].models[0].params[0]
    assert len(param.param_values) == 1, "no padding entries may be emitted"
    assert param.param_values[0].HasField("float_value")
    assert abs(param.param_values[0].float_value - 0.5) < 1e-6


def test_set_bypass_sends_row_column_keyed_grid_update():
    # CONFIRMED capture shape: Grid{UPDATE, preset{bypass{row, colBypass{column,
    # sceneBypass{bypass}}}}}.
    qc = client.QuadCortex(FakeTransport())
    qc.set_bypass(Block(0, 4), bypassed=True)
    sent = qc._t.sent[-1]
    assert isinstance(sent, pa.GridMessage)
    assert sent.action == pa.MessageAction.UPDATE
    bp = sent.preset.bypass[0]
    assert bp.row == 0
    cb = bp.colBypass[0]
    assert cb.column == 4
    assert cb.sceneBypass[0].bypass is True


def test_set_bypass_never_pads_scene_bypass():
    # Replaces a test asserting sceneBypass was padded to the scene index. That is
    # not how the device reads it: only index 0 is honoured, applied to the ACTIVE
    # scene, so padding wrote a default False to the wrong scene and did nothing to
    # the intended one. See test_set_bypass_targets_a_scene_by_switching_to_it.
    qc = client.QuadCortex(FakeTransport())
    qc.set_bypass(Block(0, 4), bypassed=True, scene=1)
    cb = qc._t.sent[-1].preset.bypass[0].colBypass[0]
    assert len(cb.sceneBypass) == 1
    assert cb.sceneBypass[0].bypass is True


def test_reroute_grid_input_sends_set_chain_input_per_matching_row():
    # Given a preset (as read from the grid) with input rows on Input 1,
    # reroute_grid_input sends one row-keyed Grid update per matching row.
    p = preset.BinaryPreset()
    p.chains.add().in_portid = Input.INPUT_1   # row 0
    p.chains.add().in_portid = 0                # row 1 internal
    p.chains.add().in_portid = Input.INPUT_1   # row 2
    qc = client.QuadCortex(FakeTransport())
    rows = qc.reroute_grid_input(p, Input.RETURN_1)
    assert rows == [0, 2]
    grids = [m for m in qc._t.sent if isinstance(m, pa.GridMessage)]
    assert len(grids) == 2
    moved = {(g.preset.chains[0].row, g.preset.chains[0].in_portid) for g in grids}
    assert moved == {(0, Input.RETURN_1), (2, Input.RETURN_1)}


def test_reroute_grid_input_raises_when_no_matching_row():
    p = preset.BinaryPreset()
    p.chains.add().in_portid = Input.RETURN_1
    qc = client.QuadCortex(FakeTransport())
    try:
        qc.reroute_grid_input(p, Input.INPUT_2)
        assert False, "expected KeyError"
    except KeyError:
        pass


# -- 5.2 recall_preset + switch_scene ----------------------------------------


def test_recall_preset_sends_setlist_position():
    # CONFIRMED wire shape (Windows capture): recalling "28C" from the user
    # setlist sent {folder_key: "/media/p4/Presets/My Presets", position: 218,
    # is_factory: false} - position is the linear index bank*8 + letter.
    qc = client.QuadCortex(FakeTransport())
    qc.recall_preset("/media/p4/Presets/My Presets", 218)
    sent = qc._t.sent[-1]
    assert isinstance(sent, pa.SetlistPositionMessage)
    assert sent.folder_key == "/media/p4/Presets/My Presets"
    assert sent.position == 218
    assert sent.is_factory is False
    assert sent.action == pa.MessageAction.UPDATE


def test_switch_scene_sends_scene_message():
    qc = client.QuadCortex(FakeTransport())
    qc.switch_scene(3)
    sent = qc._t.sent[-1]
    assert isinstance(sent, pa.SceneMessage)
    assert sent.selected_scene == 3


# -- 5.3 copy_scene + set_param + write_preset -------------------------------


def test_copy_scene_sends_scene_copy():
    qc = client.QuadCortex(FakeTransport())
    qc.copy_scene(from_index=0, to_index=1)
    sent = qc._t.sent[-1]
    assert isinstance(sent, pa.SceneCopyMessage)
    assert (sent.from_index, sent.to_index, sent.is_swap) == (0, 1, False)
    # CONFIRMED shape (session-03): the device broadcast action UPDATE, not COPY.
    assert sent.action == pa.MessageAction.UPDATE


def test_scene_label_and_color():
    qc = client.QuadCortex(FakeTransport())
    qc.set_scene_label(3, "Kick2")
    lbl = qc._t.sent[-1]
    assert isinstance(lbl, pa.SceneLabelMessage)
    assert (lbl.index, lbl.label, lbl.action) == (3, "Kick2", pa.MessageAction.UPDATE)
    qc.set_scene_color(1, 0xFFFF02C2)
    col = qc._t.sent[-1]
    assert isinstance(col, pa.SceneColorMessage)
    assert (col.index, col.color, col.action) == (1, 0xFFFF02C2, pa.MessageAction.UPDATE)


# -- 5.4 save_current_preset + delete_preset (file ops) -----------------------


def test_save_current_preset_sends_file_create_by_reference():
    # CONFIRMED wire shape (Windows capture): "Save As" to slot 28E sent a
    # FileMessage with default action (CREATE=0), type 0, NO preset payload,
    # folder.key = setlist path, and one files entry {index: 220, name,
    # instrument: 2} - the device saves the preset already on its grid.
    qc = client.QuadCortex(FakeTransport())
    qc.save_current_preset(
        "/media/p4/Presets/My Presets", 220, "Test save to user sl", instrument=2
    )
    sent = qc._t.sent[-1]
    assert isinstance(sent, pa.FileMessage)
    assert sent.action == pa.MessageAction.CREATE  # 0, the proto default
    assert sent.type == 0
    assert not sent.HasField("preset_payload")
    assert sent.folder.key == "/media/p4/Presets/My Presets"
    assert sent.folder.is_factory is False
    entry = sent.folder.files[0]
    assert (entry.index, entry.name, entry.instrument) == (
        220,
        "Test save to user sl",
        2,
    )


def test_delete_preset_sends_file_delete_by_path():
    # CONFIRMED wire shape (Windows capture 2): delete addresses the preset by
    # its device file path "<setlist>/<name>.pb", not by slot index.
    qc = client.QuadCortex(FakeTransport())
    qc.delete_preset("/media/p4/Presets/My Presets", "Test save to user sl")
    sent = qc._t.sent[-1]
    assert isinstance(sent, pa.FileMessage)
    assert sent.action == pa.MessageAction.DELETE
    assert sent.type == 0
    assert sent.folder.key == "/media/p4/Presets/My Presets"
    assert sent.folder.is_factory is False
    assert (
        sent.folder.files[0].key
        == "/media/p4/Presets/My Presets/Test save to user sl.pb"
    )


def test_move_preset_sends_file_move():
    # CONFIRMED wire shape (Windows capture 2): source by file path,
    # destination by linear slot index in to_folder.
    qc = client.QuadCortex(FakeTransport())
    qc.move_preset("/media/p4/Presets/My Presets", "Darkglass AO900 2_1", 219)
    sent = qc._t.sent[-1]
    assert isinstance(sent, pa.FileMessage)
    assert sent.action == pa.MessageAction.MOVE
    assert (
        sent.folder.files[0].key
        == "/media/p4/Presets/My Presets/Darkglass AO900 2_1.pb"
    )
    assert sent.to_folder.key == "/media/p4/Presets/My Presets"
    assert sent.to_folder.files[0].index == 219


# -- session hello -------------------------------------------------------------


def test_hello_performs_full_connect_handshake():
    canned = {"ResetCommsBuffersMessage": pa.ResetCommsBuffersMessage(session_id="ab")}
    qc = client.QuadCortex(FakeTransport(canned))
    reply = qc._hello(settle=0)
    assert reply.session_id == "ab"
    sent = qc._t.sent
    # ResetCommsBuffers goes via request() (recorded first), then the burst.
    assert isinstance(sent[0], pa.ResetCommsBuffersMessage)
    assert len(sent[0].session_id) == 32  # fresh 32-hex token
    # Version announce carries a valid CC version (the device gates push on it).
    version_updates = [
        m for m in sent
        if isinstance(m, pa.VersionMessage) and m.action == pa.MessageAction.UPDATE
    ]
    assert version_updates and version_updates[0].cortex_control_version == "4.0.1"
    # Connection{true} is present.
    conns = [m for m in sent if isinstance(m, pa.ConnectionMessage)]
    assert conns and conns[0].connected is True
    # RecallPreset subscription READ is present (what makes preset pushes flow).
    recall_reads = [
        m for m in sent
        if isinstance(m, pa.RecallPresetMessage) and m.action == pa.MessageAction.READ
    ]
    assert recall_reads
    # ModelRepo READ is present (empirically required to open the push gate).
    assert any(
        isinstance(m, pa.ModelRepoMessage) and m.action == pa.MessageAction.READ
        for m in sent
    )
    # hello must NOT issue a standalone Version READ (would race later requests).
    assert not any(
        isinstance(m, pa.VersionMessage) and m.action == pa.MessageAction.READ
        for m in sent
    )


# -- ergonomics: no magic numbers at the call site -----------------------------


def test_switch_scene_accepts_a_scene_enum():
    from pyquadcortex.protocol.enums import Scene

    fake = FakeTransport()
    qc = client.QuadCortex(fake)
    qc.switch_scene(Scene.B)
    assert fake.sent[-1].selected_scene == 1
    # Scene letters map to the device's zero-based numbering.
    assert (Scene.A, Scene.B, Scene.D, Scene.H) == (0, 1, 3, 7)


def test_recall_infers_is_factory_from_the_setlist():
    # A caller should not have to remember to pass is_factory alongside the
    # factory setlist; the two always agree.
    fake = FakeTransport()
    qc = client.QuadCortex(fake)

    qc.recall_preset(Setlist.FACTORY, 212)
    assert fake.sent[-1].is_factory is True

    qc.recall_preset(Setlist.USER, 218)
    assert fake.sent[-1].is_factory is False

    # An explicit value still wins, for a setlist we do not know about.
    qc.recall_preset("/some/other/setlist", 0, is_factory=True)
    assert fake.sent[-1].is_factory is True


def test_recall_accepts_a_slot_name():
    fake = FakeTransport()
    qc = client.QuadCortex(fake)
    qc.recall_preset(Setlist.USER, "28C")
    assert fake.sent[-1].position == 218          # (28-1)*8 + 2
    qc.recall_preset(Setlist.USER, 218)
    assert fake.sent[-1].position == 218


def test_find_preset_looks_a_preset_up_by_name():
    listing = pa.FileMessage()
    listing.folder.key = str(Setlist.FACTORY)
    for index, name in ((7, "D-Cell H4 Ch3"), (212, "Cali Basswalk")):
        pd = listing.folder.files.add()
        pd.index = index
        pd.name = name
    fake = FakeTransport()
    fake.broadcast = listing
    qc = client.QuadCortex(fake)

    found = qc.find_preset("Cali Basswalk", Setlist.FACTORY)
    assert found.index == 212
    # Case and surrounding whitespace should not matter.
    assert qc.find_preset("  cali basswalk ", Setlist.FACTORY).index == 212

    with pytest.raises(KeyError, match="No Such Preset"):
        qc.find_preset("No Such Preset", Setlist.FACTORY)


def test_save_and_move_accept_slot_names():
    fake = FakeTransport()
    qc = client.QuadCortex(fake)

    qc.save_current_preset(Setlist.USER, "30A", "Some Preset")
    assert fake.sent[-1].folder.files[0].index == 232      # (30-1)*8 + 0

    qc.move_preset(Setlist.USER, "Some Preset", "28D")
    assert fake.sent[-1].to_folder.files[0].index == 219   # (28-1)*8 + 3


# -- grid blocks (add / replace / remove) -------------------------------------


def test_set_block_sends_row_column_keyed_grid_update():
    # CONFIRMED on hardware: placing a block is the same keyed sparse Grid
    # UPDATE as set_param, carrying `hash` instead of params. The device's own
    # broadcast when a block is added on the unit has this exact shape.
    qc = client.QuadCortex(FakeTransport())
    qc.set_block(Block(0, 2, 5005))
    sent = qc._t.sent[-1]
    assert isinstance(sent, pa.GridMessage)
    assert sent.action == pa.MessageAction.UPDATE
    ch = sent.preset.chains[0]
    assert ch.row == 0
    assert ch.models[0].column == 2
    assert ch.models[0].hash == 5005


def test_set_block_accepts_a_catalog_model():
    model = catalog.Model(id=4005, name="Graphic-9", category="Equalizer",
                          category_id=4)
    qc = client.QuadCortex(FakeTransport())
    qc.set_block(Block(1, 3, model))
    assert qc._t.sent[-1].preset.chains[0].models[0].hash == 4005


def test_remove_block_sends_grid_delete():
    # CONFIRMED: deleting a block on the unit broadcast
    # Grid{action: DELETE, chains{row, models{column, hash:0}}}. Sending the
    # same shape from the host removes the block; an UPDATE with hash=0 does
    # NOT (the firmware ignores a zero hash on an update).
    qc = client.QuadCortex(FakeTransport())
    qc.remove_block(Block(0, 4))
    sent = qc._t.sent[-1]
    assert isinstance(sent, pa.GridMessage)
    assert sent.action == pa.MessageAction.DELETE
    ch = sent.preset.chains[0]
    assert ch.row == 0
    assert ch.models[0].column == 4
    assert ch.models[0].hash == 0


def test_set_param_accepts_a_parameter_name_when_the_catalog_is_loaded():
    qc = client.QuadCortex(FakeTransport())
    qc._catalog = catalog.parse_model_repo(_sample_repo_payload())
    # row 0 / column 1 holds model 5005 in this grid, whose parameter 0 is
    # THRESHOLD; naming it must resolve to that index.
    qc.set_param(Block(0, 1, 5005), "THRESHOLD", Encoded(0.25))
    param = qc._t.sent[-1].preset.chains[0].models[0].params[0]
    assert param.index == 0
    assert abs(param.param_values[0].float_value - 0.25) < 1e-6


def test_set_param_by_name_needs_a_known_model():
    qc = client.QuadCortex(FakeTransport())
    qc._catalog = catalog.parse_model_repo(_sample_repo_payload())
    with pytest.raises(KeyError):
        qc.set_param(Block(0, 1, 5005), "NOPE", Encoded(0.5))


def _sample_repo_payload():
    from tests.test_catalog import make_payload

    return make_payload()


def test_set_param_accepts_a_value_in_real_units():
    # Confirmed on hardware: the wire is normalized 0..1 (sending 1.0 to a
    # -60..+12 dB THRESHOLD read +12.0 dB on the unit). real= converts through
    # the catalog range, so callers can speak dB instead of fractions.
    qc = client.QuadCortex(FakeTransport())
    qc._catalog = catalog.parse_model_repo(_sample_repo_payload())
    comp = qc._catalog[5005]          # THRESHOLD spans -60..+12 dB
    qc.set_param(Block(0, 1, comp), "THRESHOLD", Real(-24.0))
    param = qc._t.sent[-1].preset.chains[0].models[0].params[0]
    assert param.param_values[0].float_value == pytest.approx(0.5)


def test_a_real_value_on_a_block_with_no_model_is_refused():
    """The conversion depends on WHICH block is in the cell.

    This passed for the wrong reason for a while: it put `Real(-20)` in the
    PARAM position, so it hit "set_param needs a value" and never reached the
    refusal it is named after. With the value in the value position it does,
    and the message is the one a confused caller has to act on.
    """
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(TypeError, match="which model is there"):
        qc.set_param(Block(0, 1), 0, Real(-20))
    assert not qc._t.sent


def test_set_param_with_no_value_at_all_is_refused():
    """The other half of what the test above used to be accidentally testing."""
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(TypeError):
        qc.set_param(Block(0, 1), Real(-20))


# -- the per-scene write ceiling ----------------------------------------------


def test_set_param_writes_one_scene_by_promoting_then_switching():
    """Per-scene parameter writes, confirmed on hardware, take three messages.

    The device honours ``param_values[0]`` against whichever scene is ACTIVE, and
    only on a parameter whose ``scene_mode`` is set. Crucially it accepts EITHER the
    flag OR a value in one message, never both - sending them together silently
    ignores the flag, which is why this looked impossible.
    """
    from pyquadcortex.protocol.enums import Scene

    fake = FakeTransport()
    qc = client.QuadCortex(fake)
    qc.set_param(Block(2, 5), 0, Encoded(0.8), scene=Scene.D)

    assert [type(m).__name__ for m in fake.sent] == [
        "GridMessage", "SceneMessage", "GridMessage"], "promote, switch, write"

    promote = fake.sent[0].preset.chains[0].models[0].params[0]
    assert promote.scene_mode is True
    assert not promote.param_values, "the flag must travel ALONE or it is ignored"

    assert fake.sent[1].selected_scene == 3

    write = fake.sent[2].preset.chains[0].models[0].params[0]
    assert not write.HasField("scene_mode"), "the value must travel alone too"
    assert len(write.param_values) == 1, "never pad; index 0 means the active scene"
    assert abs(write.param_values[0].float_value - 0.8) < 1e-6


def test_set_param_without_a_scene_writes_the_active_scene_only():
    # No scene named: one message, no promotion, no scene switch. On a parameter
    # that is not scene-following this changes its single global value.
    fake = FakeTransport()
    qc = client.QuadCortex(fake)
    qc.set_param(Block(0, 1), 5, Encoded(0.25))
    assert [type(m).__name__ for m in fake.sent] == ["GridMessage"]
    p = fake.sent[0].preset.chains[0].models[0].params[0]
    assert len(p.param_values) == 1
    assert p.param_values[0].HasField("float_value")


def test_set_param_can_skip_promotion():
    from pyquadcortex.protocol.enums import Scene

    fake = FakeTransport()
    qc = client.QuadCortex(fake)
    qc.set_param(Block(0, 1), 5, Encoded(0.5), scene=Scene.B, promote=False)
    assert [type(m).__name__ for m in fake.sent] == ["SceneMessage", "GridMessage"]


def test_set_param_scene_mode_sends_the_flag_alone():
    fake = FakeTransport()
    qc = client.QuadCortex(fake)
    qc.set_param_scene_mode(Block(2, 5), 1, enabled=True)
    prm = fake.sent[-1].preset.chains[0].models[0].params[0]
    assert prm.scene_mode is True
    assert not prm.param_values, "a value alongside the flag makes the device drop it"


def test_set_lane_output_supports_per_scene_values():
    from pyquadcortex.protocol.enums import Scene

    fake = FakeTransport()
    qc = client.QuadCortex(fake)
    qc.set_param(LaneOutput(0), 0, Encoded(0.0), scene=Scene.D)
    assert [type(m).__name__ for m in fake.sent] == [
        "GridMessage", "SceneMessage", "GridMessage"]
    promote = fake.sent[0].preset.chains[0].output_control[0]
    assert promote.hash == 23000
    assert promote.params[0].scene_mode is True
    assert not promote.params[0].param_values
    write = fake.sent[2].preset.chains[0].output_control[0].params[0]
    assert len(write.param_values) == 1
    assert write.param_values[0].float_value == 0.0


def test_set_bypass_targets_a_scene_by_switching_to_it():
    # Confirmed on hardware: the device applies sceneBypass[0] to whichever scene
    # is ACTIVE, and ignores entries beyond index 0. So the old padding was doubly
    # wrong - it wrote a default False to the active scene and did nothing to the
    # scene asked for. Naming a scene therefore means: switch to it, then write
    # index 0. Ordering over the pipe is enough; no settle delay is needed.
    from pyquadcortex.protocol.enums import Scene

    fake = FakeTransport()
    qc = client.QuadCortex(fake)

    qc.set_bypass(Block(0, 2), bypassed=True, scene=Scene.D)
    assert isinstance(fake.sent[-2], pa.SceneMessage)
    assert fake.sent[-2].selected_scene == 3
    cb = fake.sent[-1].preset.bypass[0].colBypass[0]
    assert len(cb.sceneBypass) == 1, "never pad; the device only reads index 0"
    assert cb.sceneBypass[0].bypass is True

    # With no scene named, act on whatever scene is active: no switch is sent.
    fake.sent.clear()
    qc.set_bypass(Block(0, 2), bypassed=False)
    assert [type(m).__name__ for m in fake.sent] == ["GridMessage"]
    assert len(fake.sent[0].preset.bypass[0].colBypass[0].sceneBypass) == 1


# -- review follow-ups: file ops, polling, lane output, ergonomics -------------


class TimingOutTransport(FakeTransport):
    """A transport whose request() never gets a reply, like the real device."""

    def request(self, msg, timeout=5.0):
        self.sent.append(msg)
        raise TimeoutError("no response")


def test_file_operations_do_not_raise_when_the_device_stays_silent():
    # File ops are asynchronous and every host write is STALLed, so a missing reply
    # says nothing about success. Raising made callers wrap each one in
    # try/except and verify by re-reading anyway.
    qc = client.QuadCortex(TimingOutTransport())
    assert qc.delete_preset(Setlist.USER, "Some Preset") is None
    assert qc.move_preset(Setlist.USER, "Some Preset", "28D") is None
    assert qc.save_current_preset(Setlist.USER, "30A", "Some Preset") == "Some Preset"


def test_save_current_preset_reports_the_name_the_device_actually_stored():
    # The device de-duplicates a colliding name, so the requested name can differ
    # from the stored one. confirm=True asks the device rather than assuming.
    listing = pa.FileMessage()
    listing.folder.key = str(Setlist.USER)
    stored = listing.folder.files.add()
    stored.index = 232                                   # 30A
    stored.name = "Cali Basswalk [Ret_1"                 # renamed by the device
    fake = FakeTransport()
    fake.broadcast = listing
    qc = client.QuadCortex(fake)

    got = qc.save_current_preset(Setlist.USER, "30A", "Cali Basswalk [Ret1]",
                                 confirm=True)
    assert got == "Cali Basswalk [Ret_1"


def test_wait_for_listing_polls_until_the_condition_holds():
    # A fixed sleep produces false negatives after a batch of mutations, because
    # settling time scales with the number of them.
    listing = pa.FileMessage()
    listing.folder.key = str(Setlist.USER)
    entry = listing.folder.files.add()
    entry.index = 0
    entry.name = "Eventually"

    class Eventually(FakeTransport):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def await_broadcast(self, expected_class, trigger, timeout=40.0, match=None):
            trigger()
            self.calls += 1
            return listing if self.calls >= 3 else pa.FileMessage(
                folder=pa.FolderInfo(key=str(Setlist.USER), files=[pa.ProductData(index=0)])
            )

    fake = Eventually()
    qc = client.QuadCortex(fake)
    entries = qc.wait_for_listing(
        Setlist.USER, until=lambda es: any(e.name == "Eventually" for e in es),
        timeout=30.0, interval=0.0,
    )
    assert [e.name for e in entries] == ["Eventually"]
    assert fake.calls == 3, "it polled rather than sleeping once"


def test_set_lane_output_writes_into_output_control_not_models():
    # Lane Output Control lives in chain.output_control[], which set_param cannot
    # reach, so VOLUME/PAN/MUTE/SOLO were unreachable through the API.
    fake = FakeTransport()
    qc = client.QuadCortex(fake)
    qc.set_param(LaneOutput(2), 1, Encoded(0.5))         # index, no catalog needed
    sent = fake.sent[-1]
    chain = sent.preset.chains[0]
    assert chain.row == 2
    assert not chain.models, "must not touch models[]"
    oc = chain.output_control[0]
    assert oc.hash == qc.LANE_OUTPUT_CONTROL == 23000
    assert oc.params[0].index == 1
    assert len(oc.params[0].param_values) == 1
    assert abs(oc.params[0].param_values[0].float_value - 0.5) < 1e-6


def test_position_to_slot_inverts_slot_to_position():
    from pyquadcortex.protocol.client import position_to_slot

    assert position_to_slot(218) == "28C"
    assert position_to_slot(0) == "1A"
    for slot in ("1A", "4B", "28C", "30A", "32H"):
        assert position_to_slot(client.slot_to_position(slot)) == slot
    with pytest.raises(ValueError):
        position_to_slot(-1)


def test_instrument_has_a_member_for_the_untagged_default():
    # save_current_preset's default was 0, which was not a member of Instrument.
    assert Instrument.NONE == 0
    assert Instrument(0) is Instrument.NONE


# -- routing, splitter/mixer, default_scene, slot helpers ----------------------


def test_set_chain_output_is_the_sibling_of_set_chain_input():
    fake = FakeTransport()
    qc = client.QuadCortex(fake)
    qc.set_chain_output(row=1, out_portid=Output.XLR_1_2)
    sent = fake.sent[-1]
    assert isinstance(sent, pa.GridMessage)
    assert sent.action == pa.MessageAction.UPDATE
    chain = sent.preset.chains[0]
    assert chain.row == 1
    assert chain.out_portid == int(Output.XLR_1_2)
    assert not chain.HasField("in_portid"), "must not touch the input"
    assert not chain.models, "row-keyed routing only"


def test_set_mixer_param_targets_the_mixer_collection():
    # Factory presets build their scenes out of per-scene Mixer LEVEL A/B, so this
    # collection has to be reachable to reproduce that behaviour.
    fake = FakeTransport()
    qc = client.QuadCortex(fake)
    qc.set_param(Mixer(0), 0, Encoded(0.769))
    chain = fake.sent[-1].preset.chains[0]
    assert chain.row == 0
    assert not chain.models and not chain.splitter
    mixer = chain.mixer[0]
    assert mixer.hash == qc.MIXER == 11000
    assert mixer.params[0].index == 0
    assert len(mixer.params[0].param_values) == 1


def test_set_splitter_param_writes_combined_splitter_not_splitter():
    # Six attempts against chain.splitter[] all read back unchanged. The device's
    # own broadcast, captured while the splitter was dragged on the unit, uses
    # chain.combined_splitter with NO hash and the UNIFIED model's parameter order -
    # which is why this looked impossible rather than merely undiscovered.
    fake = FakeTransport()
    qc = client.QuadCortex(fake)
    qc.set_param(Splitter(0), 3, Encoded(0.25))      # 3 = LEVEL TO A
    chain = fake.sent[-1].preset.chains[0]
    assert chain.row == 0
    assert not chain.splitter, "the legacy field is the device's read-only view"
    assert not chain.models and not chain.mixer
    el = chain.combined_splitter[0]
    assert not el.HasField("hash"), "the broadcast carries no hash, so nor do we"
    assert el.params[0].index == 3
    assert len(el.params[0].param_values) == 1
    assert abs(el.params[0].param_values[0].float_value - 0.25) < 1e-6


def test_splitter_param_per_scene_uses_promote_switch_write():
    from pyquadcortex.protocol.enums import Scene

    fake = FakeTransport()
    qc = client.QuadCortex(fake)
    qc.set_param(Splitter(0), 3, Encoded(0.1), scene=Scene.B)
    assert [type(m).__name__ for m in fake.sent] == [
        "GridMessage", "SceneMessage", "GridMessage"]
    promote = fake.sent[0].preset.chains[0].combined_splitter[0].params[0]
    assert promote.scene_mode is True
    assert not promote.param_values


def test_set_tempo_param_reaches_tempo_program_data():
    # tempoProgramData is not row or column keyed, yet a Grid UPDATE carrying it is
    # applied - confirmed on hardware, which is what makes per-preset tempo reachable.
    fake = FakeTransport()
    qc = client.QuadCortex(fake)
    qc.set_param(Tempo(), 2, Encoded(0.0))
    sent = fake.sent[-1]
    assert isinstance(sent, pa.GridMessage)
    assert not sent.preset.chains, "not a chain edit"
    tp = sent.preset.tempoProgramData[0]
    assert tp.hash == qc.TEMPO_CONTROL == 25000
    assert tp.params[0].index == 2
    assert tp.params[0].param_values[0].float_value == 0.0


def test_mixer_param_per_scene_uses_promote_switch_write():
    from pyquadcortex.protocol.enums import Scene

    fake = FakeTransport()
    qc = client.QuadCortex(fake)
    qc.set_param(Mixer(0), 0, Encoded(0.0), scene=Scene.C)
    assert [type(m).__name__ for m in fake.sent] == [
        "GridMessage", "SceneMessage", "GridMessage"]
    promote = fake.sent[0].preset.chains[0].mixer[0].params[0]
    assert promote.scene_mode is True
    assert not promote.param_values, "flag alone, or the device drops it"


def test_save_current_preset_sets_the_default_scene_by_switching_first():
    from pyquadcortex.protocol.enums import Scene

    fake = FakeTransport()
    qc = client.QuadCortex(fake)
    qc.save_current_preset(Setlist.USER, "30A", "Patch", default_scene=Scene.D)
    kinds = [type(m).__name__ for m in fake.sent]
    assert kinds == ["SceneMessage", "FileMessage"], "switch, then save"
    assert fake.sent[0].selected_scene == 3


def test_slot_names_beyond_the_setlist_are_rejected():
    # A setlist is 256 slots, so bank 33 does not exist. Accepting "33A" silently
    # produced position 256, the device ignored the save, and it surfaced much
    # later as a listing that never showed the preset.
    for bad in ("33A", "0A", "99H"):
        with pytest.raises(ValueError):
            client.slot_to_position(bad)
    with pytest.raises(ValueError):
        client.position_to_slot(256)


def test_position_to_slot_can_match_the_padded_form_it_accepts():
    # slot_to_position takes "01A" and "1A"; position_to_slot returns "1A", so
    # comparing against a padded string silently never matched.
    assert client.position_to_slot(0) == "1A"
    assert client.position_to_slot(0, pad=True) == "01A"
    for slot in ("01A", "04B", "28C", "32H"):
        assert client.position_to_slot(client.slot_to_position(slot), pad=True) == slot


def test_wait_for_listing_rides_out_a_missed_push():
    """A single quiet interval must not abort the wait.

    list_presets raises TimeoutError when the device fails to push a File broadcast.
    Propagating that produced exactly the false negative this method exists to
    prevent: a save that had already succeeded killing a long build mid-run.
    """
    listing = pa.FileMessage()
    listing.folder.key = str(Setlist.USER)
    e = listing.folder.files.add()
    e.index = 0
    e.name = "Arrived"

    class FlakyTransport(FakeTransport):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        def await_broadcast(self, expected_class, trigger, timeout=40.0, match=None):
            trigger()
            self.attempts += 1
            if self.attempts in (1, 2):
                raise TimeoutError("no FileMessage broadcast within 25.0s")
            return listing

    fake = FlakyTransport()
    qc = client.QuadCortex(fake)
    entries = qc.wait_for_listing(
        Setlist.USER, until=lambda es: any(x.name == "Arrived" for x in es),
        timeout=30.0, interval=0.0,
    )
    assert [x.name for x in entries] == ["Arrived"]
    assert fake.attempts == 3, "it kept polling through two missed pushes"


def test_wait_for_listing_distinguishes_its_two_failures():
    # "the condition never became true" and "the device went silent" are different
    # diagnoses: only the first tells you anything about your change.
    quiet = pa.FileMessage()
    quiet.folder.key = str(Setlist.USER)
    quiet.folder.files.add().index = 0          # a listing, but no matching preset

    fake = FakeTransport()
    fake.broadcast = quiet
    qc = client.QuadCortex(fake)
    with pytest.raises(TimeoutError, match="condition never became true"):
        qc.wait_for_listing(Setlist.USER, until=lambda es: False,
                            timeout=0.0, interval=0.0)

    class SilentTransport(FakeTransport):
        def await_broadcast(self, expected_class, trigger, timeout=40.0, match=None):
            trigger()
            raise TimeoutError("no FileMessage broadcast within 25.0s")

    qc2 = client.QuadCortex(SilentTransport())
    with pytest.raises(TimeoutError, match="stopped pushing listings"):
        qc2.wait_for_listing(Setlist.USER, until=lambda es: True,
                             timeout=0.0, interval=0.0)


def test_set_chain_output_docstring_does_not_lump_19_with_the_internal_routes():
    # 19 (MULTIPLE) is a real destination - it is how factory presets reach the
    # Multi-Out - while 16-18 are internal row-to-row routing. Conflating them
    # steered a user away from the correct value.
    doc = client.QuadCortex.set_chain_output.__doc__
    assert "16 to 18" in doc
    assert "19" in doc and "real destination" in doc
    assert "16 to 19" not in doc, "the old wording called 19 internal"


# -- grid topology (verified against factory presets on hardware) --------------
# splitter/mixer/combined_splitter/split_control_points exist ONLY on rows 0 and
# 2: counted across all 68 rows of 17 factory presets, each appears 17 times on
# rows 0 and 2 and zero times on rows 1 and 3, because a branch can only
# originate on an even row with its lane on the row below.


def _preset_with_split(row, split, mix, block_rows=()):
    p = preset.BinaryPreset()
    for r in range(4):
        chain = p.chains.add()
        chain.row = r
        for c in range(8):
            m = chain.models.add()
            m.column = c
            if r in block_rows and c == 0:
                m.hash = 5005
        if r % 2 == 0:
            scp = chain.split_control_points.add()
            scp.split = split if r == row else -1
            scp.mix = mix if r == row else -1
    return p


def test_splits_reports_a_branch_that_never_rejoins():
    # Factory "Strat Ambience" (05B) reports split=2 mix=-1 on row 0: it branches
    # and the lane never recombines. Dropping those hid a row that is spoken for.
    p = _preset_with_split(row=0, split=2, mix=-1)
    found = client.splits(p)
    assert len(found) == 1
    assert found[0].row == 0
    assert found[0].split_column == 2
    assert found[0].mix_column == -1
    assert found[0].rejoins is False
    assert found[0].lane_row == 1


def test_splits_reports_a_branch_that_does_rejoin():
    p = _preset_with_split(row=2, split=4, mix=4)
    found = client.splits(p)
    assert [(s.row, s.split_column, s.mix_column) for s in found] == [(2, 4, 4)]
    assert found[0].rejoins is True
    assert found[0].lane_row == 3


def test_splits_omits_rows_that_do_not_branch():
    p = _preset_with_split(row=0, split=-1, mix=-1)
    assert client.splits(p) == []


def test_free_rows_excludes_the_lane_of_a_branch_even_when_it_is_empty():
    # 05B branches on row 0 and holds nothing on row 1. Row 1 is NOT free:
    # building there puts blocks inside the existing chain's parallel path.
    p = _preset_with_split(row=0, split=2, mix=-1, block_rows=(0,))
    assert client.free_rows(p) == [2, 3]


def test_free_rows_counts_an_empty_row_below_a_serial_row_as_free():
    p = _preset_with_split(row=0, split=-1, mix=-1, block_rows=(0,))
    assert client.free_rows(p) == [1, 2, 3]


def _preset_with_bypass_and_param():
    p = preset.BinaryPreset()
    for r in range(4):
        bp = p.bypass.add()
        for c in range(8):
            cb = bp.colBypass.add()
            for s in range(8):
                cb.sceneBypass.add().bypass = (r == 1 and c == 2 and s in (0, 3))
            if r == 1 and c == 2:
                cb.sceneMode = True
        chain = p.chains.add()
        for c in range(8):
            m = chain.models.add()
            prm = m.params.add()
            if r == 1 and c == 2:
                prm.scene_mode = True
                prm.param_values.add().float_value = 0.25
                prm.param_values.add().string_value = "named"
                prm.param_values.add()                       # neither field set
    return p


def test_bypass_state_reads_positionally_not_by_field_values():
    """Stored entries leave row/column at 0; position IS the address."""
    p = _preset_with_bypass_and_param()
    st = client.bypass_state(p, Block(1, 2))
    assert st.scene_mode is True
    assert st.scenes == (True, False, False, True, False, False, False, False)
    other = client.bypass_state(p, Block(0, 0))
    assert other.scene_mode is False
    assert not any(other.scenes)


def test_param_state_returns_floats_strings_and_none_per_slot():
    p = _preset_with_bypass_and_param()
    st = client.param_state(p, Block(1, 2), 0)
    assert st.scene_mode is True
    assert st.values == (0.25, "named", None)


def test_set_input_port_confirm_polls_until_the_port_agrees():
    """The first read after a write can be stale even when the write landed."""

    class EventuallyConsistent(FakeTransport):
        def __init__(self):
            super().__init__()
            self.reads = 0

        def await_broadcast(self, expected_class, trigger, timeout=40.0, match=None):
            trigger()
            self.reads += 1
            io = pa.IOSettingsMessage(action=pa.MessageAction.UPDATE)
            port = io.settings.in_port.add()
            port.input_port_id = 1
            port.level = 0.2 if self.reads == 1 else 0.5   # stale first read
            return io

    qc = client.QuadCortex(EventuallyConsistent())
    got = qc.set_input_port(1, level=Encoded(0.5), confirm=True, timeout=10.0)
    assert qc._t.reads == 2                    # one stale read absorbed
    assert abs(got.settings.in_port[0].level - 0.5) < 1e-6


def test_set_input_port_confirm_timeout_explains_staleness():
    class AlwaysStale(FakeTransport):
        def await_broadcast(self, expected_class, trigger, timeout=40.0, match=None):
            trigger()
            io = pa.IOSettingsMessage(action=pa.MessageAction.UPDATE)
            port = io.settings.in_port.add()
            port.input_port_id = 1
            port.level = 0.2
            return io

    qc = client.QuadCortex(AlwaysStale())
    with pytest.raises(TimeoutError, match="eventually consistent"):
        qc.set_input_port(1, level=Encoded(0.5), confirm=True, timeout=2.0)


def test_preset_dirty_reads_and_treats_absent_as_false():
    """is_dirty has no field presence: absent IS false, not unknown."""
    qc = client.QuadCortex(FakeTransport(
        canned={"PresetDirtyMessage": pa.PresetDirtyMessage(
            action=pa.MessageAction.UPDATE, is_dirty=True)}))
    assert qc.preset_dirty() is True
    assert qc._t.sent[-1].action == pa.MessageAction.READ
    qc2 = client.QuadCortex(FakeTransport(
        canned={"PresetDirtyMessage": pa.PresetDirtyMessage(
            action=pa.MessageAction.UPDATE)}))       # flag absent on the wire
    assert qc2.preset_dirty() is False


def test_restore_audio_clears_the_mute_preference_only_when_set():
    """The only host-side release from the invisible tuner engagement."""
    muted = pa.TunerMessage(action=pa.MessageAction.UPDATE, input_port_id=1,
                            mute=True)
    qc = client.QuadCortex(StateTransport(muted))
    import warnings as _w
    with _w.catch_warnings():
        _w.simplefilter("error")           # the remedy must not warn
        assert qc.restore_audio() is True
    assert qc._t.sent[-1].mute is False

    clean = pa.TunerMessage(action=pa.MessageAction.UPDATE, input_port_id=1)
    qc2 = client.QuadCortex(StateTransport(clean))
    assert qc2.restore_audio() is False
    assert not any(isinstance(m, pa.TunerMessage)
                   and m.action == pa.MessageAction.UPDATE and m.HasField("mute")
                   for m in qc2._t.sent), "nothing written when already audible"


def test_tuner_writes_warn_when_they_will_silence_the_rig():
    muted = pa.TunerMessage(action=pa.MessageAction.UPDATE, input_port_id=1,
                            mute=True)
    qc = client.QuadCortex(StateTransport(muted))
    with pytest.warns(UserWarning, match="go silent"):
        qc.set_tuner_input(2)
    qc2 = client.QuadCortex(FakeTransport())
    with pytest.warns(UserWarning, match="SILENCES"):
        qc2.set_tuner_mute(True)


def test_power_option_enum_matches_the_schema():
    from pyquadcortex.protocol import PowerOption
    wire = {v.name: v.number for v in
            pa.PowerOptions.DESCRIPTOR.enum_types_by_name["Enum"].values}
    assert {m.name: m.value for m in PowerOption} == wire


def test_recall_reason_enum_matches_the_schema():
    from pyquadcortex.protocol import RecallReason
    wire = {v.name: v.number for v in
            pa.RecallPresetReason.DESCRIPTOR.enum_types_by_name["Enum"].values}
    assert wire == {"OTHER": 0, "UNDO": 1, "SAVE": 2}
    assert {m.name: m.value for m in RecallReason} == wire


def test_read_current_preset_uses_recallpreset_read_and_request_id():
    push = pa.RecallPresetMessage(action=pa.MessageAction.UPDATE, request_id=1)
    push.preset.name = "live state"
    qc = client.QuadCortex(StateTransport(push))
    got = qc.read_current_preset()
    assert got.name == "live state"
    asked = qc._t.sent[-1]
    assert isinstance(asked, pa.RecallPresetMessage)
    assert asked.action == pa.MessageAction.READ
    match = qc._t.matches[-1]
    assert match(push) is True
    assert match(pa.RecallPresetMessage(request_id=999)) is False
    assert match(pa.RecallPresetMessage()) is False


def test_read_current_preset_push_hands_back_the_whole_reply():
    """`read_current_preset` returns the preset inside the reply; the state
    layer needs `reason` beside it, and both come from one answer. Same request
    and same match either way - this is where that method does its work."""
    push = pa.RecallPresetMessage(action=pa.MessageAction.UPDATE, request_id=1,
                                  reason=pa.RecallPresetReason.OTHER)
    push.preset.name = "live state"
    qc = client.QuadCortex(StateTransport(push))
    got = qc.read_current_preset_push()
    assert got.preset.name == "live state"
    assert got.reason == pa.RecallPresetReason.OTHER
    asked = qc._t.sent[-1]
    assert isinstance(asked, pa.RecallPresetMessage)
    assert asked.action == pa.MessageAction.READ
    assert asked.HasField("request_id")


def test_loaded_position_reads_the_slot_without_recalling_it():
    """`SetlistPosition{READ}`. The same message type as a recall, and the
    action is the whole difference between asking and loading - so the wire
    shape is asserted rather than assumed.

    Confirmed on hardware 2026-08-15 (d14e): answered in 3 ms with the request
    id echoed, on the first attempt.
    """
    push = pa.SetlistPositionMessage(action=pa.MessageAction.UPDATE, request_id=1,
                                     folder_key="/media/p4/Presets/My Presets",
                                     position=218, is_factory=False)
    qc = client.QuadCortex(StateTransport(push))
    got = qc.loaded_position()
    assert got.position == 218
    assert got.folder_key == "/media/p4/Presets/My Presets"
    asked = qc._t.sent[-1]
    assert isinstance(asked, pa.SetlistPositionMessage)
    assert asked.action == pa.MessageAction.READ, (
        "an UPDATE here would RECALL the slot rather than ask about it")
    assert not asked.HasField("folder_key"), (
        "a READ names no slot - it asks which one is loaded")
    assert not asked.HasField("position")
    match = qc._t.matches[-1]
    assert match(push) is True
    assert match(pa.SetlistPositionMessage(request_id=999)) is False
    assert match(pa.SetlistPositionMessage()) is False, (
        "the burst pushes one of these with no request_id at all, and taking "
        "that for our answer is how a read returns somebody else's news")


def test_active_scene_reads_and_returns_the_enum():
    from pyquadcortex.protocol.enums import Scene
    push = pa.SceneMessage(action=pa.MessageAction.UPDATE, request_id=1,
                           selected_scene=2)
    qc = client.QuadCortex(StateTransport(push))
    assert qc.active_scene() is Scene.C
    assert qc._t.sent[-1].action == pa.MessageAction.READ


def test_params_equal_compares_lists_by_selected_option():
    from pyquadcortex.protocol import params_equal
    # same option, same count
    assert params_equal(1 / 3, 1 / 3, option_count=4)
    assert not params_equal(1 / 3, 2 / 3, option_count=4)
    # the rescaling case this exists for: a block was added, count 7 -> 8,
    # option 2 moves from 2/6 to 2/7 and must still compare equal
    assert params_equal(2 / 6, 2 / 7, option_count=(7, 8))
    assert not params_equal(2 / 6, 3 / 7, option_count=(7, 8))


def test_params_equal_on_plain_floats_uses_a_tolerance_and_handles_nan():
    from pyquadcortex.protocol import params_equal
    assert params_equal(0.5, 0.50000001)
    assert not params_equal(0.5, 0.56)
    nan = float("nan")
    assert params_equal(nan, nan)          # factory content stores NaN
    assert not params_equal(nan, 0.5)


def test_params_equal_rejects_a_degenerate_option_count():
    from pyquadcortex.protocol import params_equal
    with pytest.raises(ValueError, match="at least 2 options"):
        params_equal(0.0, 0.0, option_count=1)


class _Capture:
    key = "a" * 64
    name = "Test Cap"


def test_set_capture_applies_params_after_the_file_name():
    """Loading a capture resets the block's knobs, so order is data integrity."""
    qc = client.QuadCortex(EchoingTransport())
    # model_id on the cell is what to place, so this both places and points.
    qc.set_capture(Block(0, 2, 14000), capture=_Capture(),
                   params={4: Encoded(0.56)})
    writes = []
    for msg in qc._t.sent:
        if isinstance(msg, pa.GridMessage):
            for chain in msg.preset.chains:
                for m in chain.models:
                    for pr in m.params:
                        for pv in pr.param_values:
                            if pv.HasField("string_value"):
                                writes.append(("text", pr.index))
                            elif pv.HasField("float_value"):
                                writes.append(("float", pr.index, round(pv.float_value, 3)))
    assert ("text", 5) in writes
    assert ("float", 4, 0.56) in writes
    # the parameter write must come AFTER the capture reference
    assert writes.index(("float", 4, 0.56)) > writes.index(("text", 5))


def test_set_capture_does_not_rewrite_a_values_scale():
    """It used to wrap every value in `Encoded`, whatever the caller wrote.

    So `Real(0.5)` on a knob running -40..+12 dB was sent as the DEVICE's 0.5 -
    the exact swap ADR-0016 exists to close, performed by the library and
    reported as success.
    """
    qc = client.QuadCortex(EchoingTransport())
    with pytest.raises(TypeError, match="which model is there"):
        qc.set_capture(Block(0, 2, 14000), capture=_Capture(),
                       params={4: Real(0.5)})
    # Refusing is the honest answer: this path addresses the cell without a
    # model id, so no scale is known. What must not happen is the old
    # behaviour, where Real(0.5) was rewritten as the DEVICE's 0.5 and sent.
    floats = [round(pv.float_value, 5)
              for msg in qc._t.sent if isinstance(msg, pa.GridMessage)
              for chain in msg.preset.chains for m in chain.models
              for pr in m.params for pv in pr.param_values
              if pr.index == 4 and pv.HasField("float_value")]
    assert floats == []


def test_set_capture_refuses_a_bare_number_like_set_param_does():
    """The docstring taught `params={4: 0.56}` while `set_param` refused it."""
    qc = client.QuadCortex(EchoingTransport())
    with pytest.raises(TypeError, match="two number lines"):
        qc.set_capture(Block(0, 2, 14000), capture=_Capture(), params={4: 0.56})


def test_set_capture_refuses_params_that_would_clobber_the_reference():
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(ValueError, match="capture reference itself"):
        qc.set_capture(Block(0, 2), capture=_Capture(),
                       params={5: "junk"})
    assert qc._t.sent == []


def test_row_status_marks_an_empty_lane_reserved_not_free():
    """The 05B shape: a branch on row 0, its lane row 1 empty. Not free."""
    p = _preset_with_split(row=0, split=2, mix=-1, block_rows=(0,))
    statuses = client.row_status(p)
    assert [r.status for r in statuses] == ["occupied", "reserved", "free", "free"]
    lane = statuses[1]
    assert lane.reserved_by == 0
    assert lane.block_count == 0
    # and it agrees with free_rows, which encodes the same answer without the why
    assert [r.row for r in statuses if r.status == "free"] == client.free_rows(p)


def test_row_status_keeps_the_split_visible_on_an_occupied_lane():
    p = _preset_with_split(row=0, split=2, mix=4, block_rows=(0, 1))
    lane = client.row_status(p)[1]
    assert lane.status == "occupied"
    assert lane.reserved_by == 0        # occupied AND a lane - both facts shown


def test_row_status_on_a_serial_preset_is_plain():
    p = _preset_with_split(row=0, split=-1, mix=-1, block_rows=(0,))
    statuses = client.row_status(p)
    assert [r.status for r in statuses] == ["occupied", "free", "free", "free"]
    assert all(r.reserved_by is None for r in statuses)


def test_splitter_and_mixer_writes_refuse_an_odd_row():
    qc = client.QuadCortex(FakeTransport())
    for row in (1, 3):
        with pytest.raises(ValueError, match="row 0 or"):
            qc.set_param(Splitter(row), 3, Encoded(0.5))
        with pytest.raises(ValueError, match="row 0 or"):
            qc.set_param(Mixer(row), 0, Encoded(0.5))
    assert qc._t.sent == [], "nothing should reach the wire for a row without one"


# -- set_block capacity verification ------------------------------------------
# A placement can be refused for want of DSP capacity: accepted on the wire,
# absent afterwards. The device echoes a Grid broadcast naming the cell it
# accepted, and a refused block produces none, so the refusal is detectable
# without saving.


class EchoingTransport(FakeTransport):
    """Echoes the accepted cell the way the device does, unless refusing it."""

    def __init__(self, refuse=()):
        super().__init__()
        self.refuse = set(refuse)

    def await_broadcast(self, expected_class, trigger, timeout=40.0, match=None):
        trigger()
        sent = self.sent[-1]
        chain = sent.preset.chains[0]
        model = chain.models[0]
        if model.hash in self.refuse:
            raise TimeoutError(f"no {expected_class.__name__} broadcast")
        echo = pa.GridMessage(action=pa.MessageAction.UPDATE)
        ch = echo.preset.chains.add()
        ch.row = chain.row
        m = ch.models.add()
        m.column = model.column
        m.hash = model.hash
        assert match is None or match(echo), "the client should accept this echo"
        return echo


def test_set_block_verifies_the_device_accepted_the_cell():
    qc = client.QuadCortex(EchoingTransport())
    qc.set_block(Block(1, 0, 5005))
    chain = qc._t.sent[-1].preset.chains[0]
    assert chain.row == 1
    assert chain.models[0].column == 0
    assert chain.models[0].hash == 5005


def test_set_block_raises_when_the_device_never_echoes_the_cell():
    qc = client.QuadCortex(EchoingTransport(refuse={21005}))
    with pytest.raises(client.BlockRefused, match="no DSP capacity"):
        qc.set_block(Block(1, 4, 21005))


def test_set_block_can_skip_verification_for_fire_and_forget_placement():
    qc = client.QuadCortex(EchoingTransport(refuse={21005}))
    qc.set_block(Block(1, 4, 21005), verify=False)   # must not raise
    assert qc._t.sent[-1].preset.chains[0].models[0].hash == 21005


def test_set_block_echo_match_ignores_an_echo_for_a_different_cell():
    qc = client.QuadCortex(FakeTransport())
    captured = {}

    def await_broadcast(expected_class, trigger, timeout=40.0, match=None):
        trigger()
        captured["match"] = match
        return pa.GridMessage()

    qc._t.await_broadcast = await_broadcast
    qc.set_block(Block(2, 3, 5005))
    match = captured["match"]

    def echo(row, column, hash_):
        m = pa.GridMessage()
        ch = m.preset.chains.add()
        ch.row = row
        mdl = ch.models.add()
        mdl.column = column
        mdl.hash = hash_
        return m

    assert match(echo(2, 3, 5005)) is True
    assert match(echo(2, 3, 4000)) is False, "a different model is not our cell"
    assert match(echo(0, 3, 5005)) is False, "a different row is not our cell"
    assert match(echo(2, 5, 5005)) is False, "a different column is not our cell"


# -- input gate ---------------------------------------------------------------


def test_set_input_gate_writes_a_row_keyed_update_into_input_control():
    qc = client.QuadCortex(FakeTransport())
    qc.set_param(LaneInput(0), 1, Encoded(1.0))          # BYPASS
    msg = qc._t.sent[-1]
    assert msg.action == pa.MessageAction.UPDATE
    chain = msg.preset.chains[0]
    assert chain.row == 0
    assert len(chain.input_control) == 1
    gate = chain.input_control[0]
    assert gate.hash == 28000
    assert gate.params[0].index == 1
    assert gate.params[0].param_values[0].float_value == pytest.approx(1.0)


def test_set_input_gate_promotes_then_switches_then_writes_for_a_scene():
    # Same three-message sequence as set_lane_output: the scene_mode flag must
    # travel alone, or the device treats the message as a plain value write.
    qc = client.QuadCortex(FakeTransport())
    qc.set_param(LaneInput(0), 0, Encoded(0.9), scene=2)
    flag, switch, write = qc._t.sent[-3:]
    assert flag.preset.chains[0].input_control[0].params[0].scene_mode is True
    assert not flag.preset.chains[0].input_control[0].params[0].param_values
    assert isinstance(switch, pa.SceneMessage)
    assert write.preset.chains[0].input_control[0].params[0].param_values


def test_set_input_gate_needs_a_value():
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(TypeError):
        qc.set_param(LaneInput(0), param=1)


# -- blank scene labels -------------------------------------------------------


def test_set_scene_label_none_sends_the_space_the_unit_uses():
    # Factory "Cali Basswalk" (27E) reads back " " for the four scenes it does
    # not use, so `if not label` works and `label == ""` does not.
    qc = client.QuadCortex(FakeTransport())
    qc.set_scene_label(5, None)
    assert qc._t.sent[-1].label == client.SCENE_UNLABELLED == " "


def test_set_scene_label_still_sends_a_given_label_verbatim():
    qc = client.QuadCortex(FakeTransport())
    qc.set_scene_label(0, "Bright Punch")
    assert qc._t.sent[-1].label == "Bright Punch"


def test_copy_scene_documents_that_the_colour_travels_too():
    doc = client.QuadCortex.copy_scene.__doc__
    assert "COLOUR" in doc and "0xff45f862" in doc


def test_set_mixer_param_now_takes_real_because_its_span_was_measured():
    # MIXER LEVEL's bounds are MIN_MIXER_DB / MAX_MIXER_DB, which the catalog
    # names and units.FIRMWARE_CONSTANTS supplies: -40..+12 dB, so 0.0 dB is
    # 10/13. Measured on 2026-08-25 at -24.4 dB at 0.30 and +12.0 at 1.0.
    qc = client.QuadCortex(FakeTransport())
    qc._catalog = catalog.parse_model_repo(_sample_repo_payload())
    qc.set_param(Mixer(0), "MIXER LEVEL", Real(0.0))
    written = qc._t.sent[-1].preset.chains[0].mixer[0].params[0]
    assert written.param_values[0].float_value == pytest.approx(client.UNITY_LEVEL)
    qc.set_param(Mixer(0), "MIXER LEVEL", Encoded(client.UNITY_LEVEL))
    written = qc._t.sent[-1].preset.chains[0].mixer[0].params[0]
    assert written.param_values[0].float_value == pytest.approx(0.76923077)


# -- split/mix mute -----------------------------------------------------------
# One control, not two: muting the splitter on the unit shows the mixer's MUTE
# already engaged. The write goes to splitBypass and the device reports it in
# mixBypass; a write to mixBypass does nothing. Established by a four-trial
# matrix (each field x rows 0 and 2, one write per fresh recall).


def test_set_split_mute_writes_splitbypass_not_mixbypass():
    qc = client.QuadCortex(FakeTransport())
    qc.set_split_mute(row=2, muted=True)
    chain = qc._t.sent[-1].preset.chains[0]
    assert chain.row == 2
    assert [x.bypass for x in chain.splitBypass] == [True]
    assert len(chain.mixBypass) == 0, "mixBypass is the report field, not the write"


def test_set_split_mute_can_unmute():
    qc = client.QuadCortex(FakeTransport())
    qc.set_split_mute(row=0, muted=False)
    assert [x.bypass for x in qc._t.sent[-1].preset.chains[0].splitBypass] == [False]


def test_set_split_mute_refuses_an_odd_row():
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(ValueError, match="row 0 or"):
        qc.set_split_mute(row=1)
    assert qc._t.sent == []


# -- STOMP footswitch assignments ---------------------------------------------


def test_set_stomp_assignment_deletes_the_old_then_writes_the_new():
    # The unit's own sequence. An UPDATE alone leaves the previous assignment.
    qc = client.QuadCortex(FakeTransport())
    qc.set_stomp_assignment(Block(2, 3), footswitch=Footswitch.D)
    delete, update = qc._t.sent[-2:]
    assert delete.action == pa.MessageAction.DELETE
    gone = delete.preset.stomp_mode_assignments[0]
    assert (gone.row, gone.column) == (2, 3)
    assert update.action == pa.MessageAction.UPDATE
    made = update.preset.stomp_mode_assignments[0]
    assert (made.row, made.column, made.stomp_index) == (2, 3, 3)


def test_stomp_assignments_reads_them_back():
    p = preset.BinaryPreset()
    for row, col, idx in ((0, 1, 0), (2, 6, 7)):
        a = p.stomp_mode_assignments.add()
        a.row, a.column, a.stomp_index = row, col, idx
    assert client.stomp_assignments(p) == [
        client.StompAssignment(row=0, column=1, footswitch=0),
        client.StompAssignment(row=2, column=6, footswitch=7),
    ]


def test_set_stomp_momentary_writes_the_map_entry():
    qc = client.QuadCortex(FakeTransport())
    qc.set_stomp_momentary(Footswitch.H, True)
    assert dict(qc._t.sent[-1].preset.stomp_is_momentary) == {7: True}


def test_set_stomp_momentary_is_keyed_by_footswitch_not_column():
    """The map key is the footswitch index, which is NOT the block's column.

    Hardware said so in the one case that can tell them apart: a block at column
    3 assigned to footswitch E broadcast ``stomp_is_momentary{key: 4}``. Every
    earlier sample had index equal to column and so proved nothing.
    """
    qc = client.QuadCortex(FakeTransport())
    qc.set_stomp_momentary(Footswitch.E, True)
    assert dict(qc._t.sent[-1].preset.stomp_is_momentary) == {4: True}


def test_set_stomp_momentary_can_clear_back_to_latching():
    qc = client.QuadCortex(FakeTransport())
    qc.set_stomp_momentary(Footswitch.B, False)
    sent = qc._t.sent[-1]
    assert sent.action == pa.MessageAction.UPDATE
    assert dict(sent.preset.stomp_is_momentary) == {1: False}
    # The entry must travel alone: the device applies preset-level maps only from
    # a sparse update, and a False is a real value here rather than an absence.
    assert not sent.preset.stomp_mode_assignments
    assert not sent.preset.stomp_labels


# -- expression pedal assignment ----------------------------------------------


def test_set_expression_is_row_column_keyed_with_a_range():
    qc = client.QuadCortex(FakeTransport())
    qc.set_expression(Block(0, 2), param=4, pedal=2,
                      minimum=Encoded(0.1), maximum=Encoded(0.9))
    chain = qc._t.sent[-1].preset.chains[0]
    assert chain.row == 0
    prm = chain.models[0].params[0]
    assert chain.models[0].column == 2
    assert prm.index == 4
    assert prm.expression == 2
    assert prm.expression_min == pytest.approx(0.1)
    assert prm.expression_max == pytest.approx(0.9)


def test_clear_expression_is_row_column_keyed_and_resets_the_range():
    qc = client.QuadCortex(FakeTransport())
    qc.clear_expression(Block(1, 3), param=4)
    chain = qc._t.sent[-1].preset.chains[0]
    assert chain.row == 1
    prm = chain.models[0].params[0]
    assert chain.models[0].column == 3
    assert prm.index == 4
    assert prm.expression == 0
    assert prm.expression_min == pytest.approx(0.0)
    assert prm.expression_max == pytest.approx(1.0)


# -- expression pedal on a Lane Output Control --------------------------------
#
# The Lane Output Control has no column, so set_expression cannot reach it -
# the same reason set_param cannot and set_lane_output exists. The parameter
# types below are the live catalog's, read off the device: VOLUME and PAN are
# floats, MUTE and SOLO are switches, and that distinction is load-bearing.

LANE_OUTPUT_CATEGORY = """
<Category id="23" name="Lane Output">
  <Model blob="loc" id="23000" name="LaneOutputControl" internal="true">
    <Parameter defaultValue="0.769" max="MAX_MIXER_DB" min="MIN_MIXER_DB" name="VOLUME" type="float" units="dB" min_string="OFF"/>
    <Parameter defaultValue="0.5" max="1" min="0" name="PAN" type="float" units=""/>
    <Parameter defaultValue="0" max="1" min="0" name="MUTE" type="switch"/>
    <Parameter defaultValue="0" max="1" min="0" name="SOLO" type="switch"/>
  </Model>
</Category>
<Category id="20" name="Neural Capture Internal">
  <Model blob="rec" id="20000" name="NC_Recorder" skip_self_test="true">
    <Parameter defaultValue="0" max="1" min="0" name="A" type="float"/>
    <Parameter defaultValue="0" max="1" min="0" name="B" type="float"/>
    <Parameter defaultValue="MAX_INPUT_TRIM" max="MAX_INPUT_TRIM" min="MIN_INPUT_TRIM" name="OUT LEVEL" type="float" units="dB" steps="41"/>
  </Model>
</Category>
"""


def _lane_client():
    """A client whose catalog carries the Lane Output Control and the Mixer."""
    from tests.test_catalog import SAMPLE_XML, make_payload

    xml = SAMPLE_XML.replace("</Models>", LANE_OUTPUT_CATEGORY + "</Models>")
    qc = client.QuadCortex(FakeTransport())
    qc._catalog = catalog.parse_model_repo(make_payload(xml))
    return qc


def test_set_lane_output_expression_writes_into_output_control_not_models():
    qc = _lane_client()
    qc.set_expression(LaneOutput(0), param="VOLUME", pedal=1,
                      minimum=Encoded(0.0), maximum=Encoded(0.830769))
    chain = qc._t.sent[-1].preset.chains[0]
    assert chain.row == 0
    assert not chain.models, "must not touch models[]"
    oc = chain.output_control[0]
    assert oc.hash == qc.LANE_OUTPUT_CONTROL == 23000
    prm = oc.params[0]
    assert prm.index == 0
    assert prm.expression == 1
    assert prm.expression_min == pytest.approx(0.0)
    assert prm.expression_max == pytest.approx(0.830769)


def test_set_lane_output_expression_does_not_send_scene_mode():
    """The unit does not set it when IT assigns a pedal, so neither do we.

    An early hand-built probe carried scene_mode: true and worked, which made it
    look required. Assigning on the touchscreen and reading back settled it: the
    unit leaves the flag alone. The manual also excludes an expression-assigned
    parameter from scene data, so forcing it is at best inert.
    """
    qc = _lane_client()
    qc.set_expression(LaneOutput(0), param="VOLUME")
    prm = qc._t.sent[-1].preset.chains[0].output_control[0].params[0]
    assert not prm.HasField("scene_mode")


def test_set_lane_output_expression_takes_pedal_two_and_a_reversed_sweep():
    qc = _lane_client()
    qc.set_expression(LaneOutput(2), param="PAN", pedal=2,
                      minimum=Encoded(0.8), maximum=Encoded(0.2))
    prm = qc._t.sent[-1].preset.chains[0].output_control[0].params[0]
    assert prm.index == 1
    assert prm.expression == 2
    assert prm.expression_min == pytest.approx(0.8)
    assert prm.expression_max == pytest.approx(0.2)


def test_clear_lane_output_expression_writes_zero_and_the_unassigned_range():
    qc = _lane_client()
    qc.clear_expression(LaneOutput(3), param="VOLUME")
    oc = qc._t.sent[-1].preset.chains[0].output_control[0]
    assert oc.hash == 23000
    prm = oc.params[0]
    assert prm.index == 0
    assert prm.expression == 0
    assert prm.expression_min == pytest.approx(0.0)
    assert prm.expression_max == pytest.approx(1.0)


@pytest.mark.parametrize("param", ["MUTE", "SOLO", 2, 3])
def test_lane_output_expression_refuses_the_two_the_device_refuses(param):
    """The device silently drops these; the library refuses them out loud.

    Tested on hardware with four message shapes - bare, with scene_mode, with
    expression_min/max, and the byte-identical message VOLUME accepted in the
    same session - plus a Grid DELETE. None landed, in either direction. The
    unit's own touchscreen writes the very same field, so this is ADR-0007's
    case: modelled and refused, not omitted and not silently broken.
    """
    qc = _lane_client()
    with pytest.raises(client.ControlNotDrivable) as refused:
        qc.set_expression(LaneOutput(0), param=param)
    with pytest.raises(client.ControlNotDrivable):
        qc.clear_expression(LaneOutput(0), param=param)
    assert not qc._t.sent, "a refused call must send nothing"

    # ADR-0007 settled: a refusal carries what, why and what to do instead, so a
    # caller can branch on it rather than parse the message.
    assert isinstance(refused.value, ValueError), "except ValueError must keep working"
    assert refused.value.control.endswith(("MUTE", "SOLO"))
    assert "silently refuses" in refused.value.evidence
    assert "touchscreen" in refused.value.workaround


def test_the_refusal_is_a_measured_list_and_not_a_rule_about_switches():
    """Being a `switch` is NOT what makes a parameter unassignable.

    Three rules were tried against hardware and all three are false - the
    reasoning is recorded beside LANE_OUTPUT_UNASSIGNABLE. This pins the one
    that is most tempting to reintroduce: every OTHER collection accepts an
    assignment on a switch-typed parameter, so a `type == "switch"` check would
    refuse writes the device demonstrably takes.
    """
    assert client.LANE_OUTPUT_UNASSIGNABLE == ("MUTE", "SOLO")

    qc = _lane_client()
    switch = qc.catalog[25000].parameter("TYPE")
    assert switch.type == "switch", "the fixture's TYPE must stay a switch"

    # Resolved by NAME through the catalog, so the type is right there to be
    # checked - and must not be. Jewel HIGH CUT, Mixer PHASE and Splitter TYPE
    # are all switches and all took an assignment on hardware.
    qc.set_expression(Block(0, 1, 25000), param="TYPE", pedal=1)
    prm = qc._t.sent[-1].preset.chains[0].models[0].params[0]
    assert prm.index == switch.index
    assert prm.expression == 1, "set_expression must not care about parameter type"


# -- the lane VOLUME speaks dB, over the span the catalog names ---------------


def test_set_lane_output_real_converts_volume_through_the_measured_db_scale():
    qc = _lane_client()
    qc.set_param(LaneOutput(0), "VOLUME", Real(-3.1))
    prm = qc._t.sent[-1].preset.chains[0].output_control[0].params[0]
    assert prm.param_values[0].float_value == pytest.approx(
        client.db_to_lane_level(-3.1))
    assert prm.param_values[0].float_value == pytest.approx(0.7096, abs=1e-4)


def test_set_lane_output_real_puts_unity_at_the_documented_value():
    qc = _lane_client()
    qc.set_param(LaneOutput(0), "VOLUME", Real(0.0))
    prm = qc._t.sent[-1].preset.chains[0].output_control[0].params[0]
    assert prm.param_values[0].float_value == pytest.approx(client.UNITY_LEVEL)


def test_a_bound_nobody_has_measured_still_refuses_real():
    """The recorder's OUT LEVEL, and it is now the ONLY one.

    Reading the whole catalog resolved every other symbolic bound, so this is
    what is left: a parameter whose block crashes the unit when placed, which
    means nobody can read its ends off the screen. It refuses rather than
    converting against a number somebody made up - see units.UNMEASURED_BOUNDS
    and units.DO_NOT_PROBE.
    """
    qc = _lane_client()
    with pytest.raises(ValueError, match="nobody has measured"):
        qc.set_param(Block(0, 1, 20000), "OUT LEVEL", Real(-3.0))


# -- per-preset MIDI out ------------------------------------------------------
# Not a Grid write: the preset stores these, but a Grid update carrying the
# field is ignored. MIDISettings applies them.


def test_set_midi_out_uses_midisettings_not_grid():
    qc = client.QuadCortex(FakeTransport())
    qc.set_midi_out(MidiSource.FOOTSWITCH_A,
                    [client.MidiOut.cc(channel=3, cc=10, value=64)])
    msg = qc._t.sent[-1]
    assert isinstance(msg, pa.MIDISettingsMessage)
    assert msg.action == pa.MessageAction.UPDATE
    group = msg.general_midi_messages.messages[0]
    assert group.source == 0
    one = group.msg[0]
    assert (one.type, one.channel, one.param1, one.param2) == (1, 3, 10, 64)


def test_preset_load_midi_out_goes_to_its_own_field():
    qc = client.QuadCortex(FakeTransport())
    qc.set_preset_load_midi_out([client.MidiOut.pc(channel=5, program=7,
                                                   bank_msb=1, bank_lsb=2)])
    msg = qc._t.sent[-1]
    assert not msg.general_midi_messages.messages
    one = msg.preset_load_messages.messages[0].msg[0]
    assert (one.type, one.channel, one.param1, one.param2, one.param3) == (3, 5, 1, 2, 7)


def test_midi_out_reader_maps_the_120_slots_to_ten_sources():
    # 10 sources x 12 messages: source N starts at slot N*12. Confirmed on
    # hardware by writing to sources 0, 1, 2, 7, 8, 9 and reading slots 0,
    # 12/13, 24, 84, 96, 108.
    p = preset.BinaryPreset()
    for _ in range(120):
        p.midi_messages_general_v2.add()
    for slot, cc in ((0, 11), (12, 21), (13, 22), (108, 111)):
        m = p.midi_messages_general_v2[slot]
        m.type, m.channel, m.param1, m.param2 = 1, 1, cc, 1
    got = client.midi_out(p)
    assert sorted(got) == [0, 1, 9]
    assert [m.param1 for m in got[1]] == [21, 22]
    assert client.midi_out(p, MidiSource.EXPRESSION_2)[0].param1 == 111
    assert client.midi_out(p, MidiSource.FOOTSWITCH_C) == []


# -- string-valued parameters and option lists --------------------------------


def test_set_param_can_write_a_string_value():
    # A cab's microphone selection travels as string_value, not float_value.
    qc = client.QuadCortex(FakeTransport())
    qc.set_param(Block(0, 5), 1, "NG_212 DG Neo_Condenser U47")
    val = qc._t.sent[-1].preset.chains[0].models[0].params[0].param_values[0]
    assert val.string_value == "NG_212 DG Neo_Condenser U47"
    assert not val.HasField("float_value")


def test_set_param_takes_exactly_one_value():
    """There is one value argument now, so "both at once" is arity, not a guard.

    This test used to pass `("x", Real(1.0))` against the old `text=`/`real=`
    pair and assert TypeError. It kept passing after those were removed, on
    "takes from 3 to 4 positional arguments but 5 were given" - green for an
    API that no longer exists. Asserting the message is what makes it mean
    something again.
    """
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(TypeError, match="positional argument"):
        qc.set_param(Block(0, 0), 0, "x", Real(1.0))
    assert not qc._t.sent


def test_param_options_reads_the_list_the_catalog_lacks():
    p = preset.BinaryPreset()
    chain = p.chains.add()
    chain.row = 2
    for c in range(8):
        m = chain.models.add()
        m.column = c
        for i in range(5):
            m.params.add().index = i
    chain.models[0].params[4].dynamic_steps.extend(["Off", "Follow Input", "Input 1"])
    assert client.param_options(p, Block(2, 0), 4) == [
        "Off", "Follow Input", "Input 1"]
    assert client.param_options(p, Block(2, 1), 4) == []


def test_midi_out_builders_match_what_the_unit_stores():
    # Each confirmed by entering the message on the unit and reading the preset:
    # CC -> type 1 with a value; CC Toggle -> type 2 with min/max; PC -> type 3
    # with the two bank bytes then the program.
    assert client.MidiOut.cc(channel=3, cc=10, value=64) == (1, 3, 10, 64, 0)
    assert client.MidiOut.cc_toggle(channel=4, cc=30, minimum=5, maximum=120) \
        == (2, 4, 30, 5, 120)
    assert client.MidiOut.pc(channel=5, program=7, bank_msb=1, bank_lsb=2) \
        == (3, 5, 1, 2, 7)
    # An expression source sweeps, so even a plain CC carries min/max.
    assert client.MidiOut.expression_cc(channel=6, cc=40, minimum=12, maximum=13) \
        == (1, 6, 40, 12, 13)


# -- global device settings ----------------------------------------------------
# These change the unit rather than a preset, and there is nothing to save.
# State pushes can be PARTIAL, so each reader waits for a push carrying the
# field it needs rather than accepting the first one of that type.


class StateTransport(FakeTransport):
    """Serves a canned state push, recording the match predicate used."""

    def __init__(self, push):
        super().__init__()
        self.push = push
        self.matches = []

    def await_broadcast(self, expected_class, trigger, timeout=40.0, match=None):
        trigger()
        self.matches.append(match)
        return self.push


def test_settings_reads_general_settings_and_requires_a_full_push():
    full = pa.GeneralSettingsMessage(action=pa.MessageAction.UPDATE,
                                     screen_brightness=50)
    full.scene_block_bypass = 0
    qc = client.QuadCortex(StateTransport(full))
    got = qc.settings()
    assert got.screen_brightness == 50
    # the READ went out, and a push lacking scene_block_bypass is not accepted
    assert qc._t.sent[-1].action == pa.MessageAction.READ
    match = qc._t.matches[-1]
    assert match(full) is True
    assert match(pa.GeneralSettingsMessage(screen_brightness=1)) is False


def test_update_settings_sends_only_the_named_fields():
    qc = client.QuadCortex(FakeTransport())
    qc.update_settings(screen_brightness=60, swap_tempo_tuner_access=True)
    msg = qc._t.sent[-1]
    assert msg.action == pa.MessageAction.UPDATE
    assert msg.screen_brightness == 60
    assert msg.swap_tempo_tuner_access is True
    assert not msg.HasField("led_brightness")


def test_input_level_db_matches_the_four_measured_points():
    """Owner-set trims read on screen and on the wire at the same moment."""
    from pyquadcortex.protocol import db_to_input_level, input_level_db
    for wire, screen in ((0.4055555462837219, 17.2), (0.40042707324028015, 16.8),
                         (0.5000885725021362, 24.0), (0.1666666716337204, 0.0)):
        assert abs(input_level_db(wire) - screen) < 0.05   # display rounds to 0.1
        assert abs(db_to_input_level(screen) - wire) < 0.0005
    assert input_level_db(0.0) == -12.0
    assert input_level_db(1.0) == 60.0                     # the spec sheet's max


def test_db_to_input_level_refuses_gains_the_unit_does_not_have():
    from pyquadcortex.protocol import db_to_input_level
    with pytest.raises(ValueError, match="does not exist"):
        db_to_input_level(61)
    with pytest.raises(ValueError, match="does not exist"):
        db_to_input_level(-12.1)


def test_lane_level_db_matches_the_three_measured_points():
    """A row's VOLUME read on screen and on the wire at the same moment. Two
    releases said -100..+30 dB: both spans put 0 dB at exactly 10/13, so the
    original unity-only measurement could never tell them apart."""
    from pyquadcortex.protocol import UNITY_LEVEL, db_to_lane_level, lane_level_db
    for wire, screen in ((0.7099999785, -3.1), (1.0, 12.0), (0.0099999998, -39.5)):
        assert abs(lane_level_db(wire) - screen) < 0.05    # display rounds to 0.1
        assert abs(db_to_lane_level(screen) - wire) < 0.0005
    assert lane_level_db(UNITY_LEVEL) == pytest.approx(0.0, abs=1e-6)
    assert db_to_lane_level(0.0) == pytest.approx(10 / 13)


def test_db_to_lane_level_refuses_levels_the_unit_does_not_have():
    from pyquadcortex.protocol import db_to_lane_level
    with pytest.raises(ValueError, match="does not exist"):
        db_to_lane_level(12.1)
    with pytest.raises(ValueError, match="Off position"):
        db_to_lane_level(-41)


def test_hybrid_mode_maps_all_six_ordered_pairs():
    """Read off the unit's own MODE indicator, which names the TOP row first."""
    from pyquadcortex.protocol.enums import FootswitchMode as M
    from pyquadcortex.protocol.enums import HYBRID_MODES, hybrid_mode
    assert hybrid_mode(M.PRESET, M.SCENE) == 3
    assert hybrid_mode(M.PRESET, M.STOMP) == 4
    assert hybrid_mode(M.SCENE, M.PRESET) == 5
    assert hybrid_mode(M.SCENE, M.STOMP) == 6
    assert hybrid_mode(M.STOMP, M.PRESET) == 7
    assert hybrid_mode(M.STOMP, M.SCENE) == 8
    # 4 and 7 are the two arrangements of the same pair - the unit's "swap rows"
    assert HYBRID_MODES[4] == (M.PRESET, M.STOMP)
    assert HYBRID_MODES[7] == (M.STOMP, M.PRESET)
    assert set(HYBRID_MODES) == {3, 4, 5, 6, 7, 8}


def test_hybrid_mode_refuses_the_same_mode_on_both_rows():
    from pyquadcortex.protocol.enums import FootswitchMode as M
    from pyquadcortex.protocol.enums import hybrid_mode
    with pytest.raises(ValueError, match="DIFFERENT modes"):
        hybrid_mode(M.SCENE, M.SCENE)


def test_describe_mode_names_base_hybrid_and_the_broken_value():
    from pyquadcortex.protocol.enums import describe_mode
    assert describe_mode(1) == "SCENE"
    assert describe_mode(7) == "HYBRID STOMP (A-D) + PRESET (E-H)"
    assert "INVALID" in describe_mode(9)
    assert "unknown" in describe_mode(42)


def test_set_mode_cycle_refuses_the_value_that_breaks_the_footswitches():
    """The device ACCEPTS 9 and the footswitches then stop working."""
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(ValueError, match="non-functional"):
        qc.set_mode_cycle([9, 1])
    assert qc._t.sent == []


def test_set_mode_cycle_refuses_what_the_device_would_silently_drop():
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(ValueError, match="rejects mode values above 9"):
        qc.set_mode_cycle([12, 1])
    with pytest.raises(ValueError, match="at most one HYBRID"):
        qc.set_mode_cycle([3, 4, 1])
    with pytest.raises(ValueError, match="cannot be the only slot"):
        qc.set_mode_cycle([7])
    assert qc._t.sent == []


def test_set_mode_cycle_sends_a_valid_hybrid():
    from pyquadcortex.protocol.enums import FootswitchMode as M
    from pyquadcortex.protocol.enums import hybrid_mode
    qc = client.QuadCortex(FakeTransport())
    qc.set_mode_cycle([hybrid_mode(M.PRESET, M.STOMP), M.SCENE])
    assert list(qc._t.sent[-1].available_modes.modes) == [4, 1]


def test_mode_cycle_waits_for_a_push_that_contains_the_cycle():
    """mode() accepts any push carrying `mode`, and the device sends it alone."""
    partial = pa.ModeMessage(action=pa.MessageAction.UPDATE, mode=7)
    full = pa.ModeMessage(action=pa.MessageAction.UPDATE, mode=7)
    full.available_modes.modes.extend([7, 1])
    qc = client.QuadCortex(StateTransport(full))
    assert qc.mode_cycle() == [7, 1]
    match = qc._t.matches[-1]
    assert match(full) is True
    assert match(partial) is False        # the partial push must not satisfy it


class _IR:
    def __init__(self, key, name):
        self.key, self.name = key, name


def test_set_ir_writes_the_library_key_as_ir_path_and_the_name_separately():
    """IR PATH takes the KEY, not a path - read off a working block on hardware."""
    qc = client.QuadCortex(EchoingTransport())
    ir = _IR("CIR_eb6d6d347e75f988010a9746580c31c", "Rex 57 on axis")
    qc.set_ir(Block(1, 0, None), ir=ir)
    strings = {}
    for msg in qc._t.sent:
        if isinstance(msg, pa.GridMessage):
            for chain in msg.preset.chains:
                for m in chain.models:
                    for pr in m.params:
                        for pv in pr.param_values:
                            if pv.HasField("string_value"):
                                strings[pr.index] = pv.string_value
    assert strings[2] == "CIR_eb6d6d347e75f988010a9746580c31c"
    assert strings[22] == "Rex 57 on axis"


def test_set_ir_slot_one_uses_the_second_pair_of_parameters():
    qc = client.QuadCortex(EchoingTransport())
    qc.set_ir(Block(1, 0, None), ir=_IR("CIR_abc", "Second"), slot=1)
    indices = {pr.index for msg in qc._t.sent if isinstance(msg, pa.GridMessage)
               for chain in msg.preset.chains for m in chain.models for pr in m.params}
    assert indices == {10, 23}


def test_set_ir_rejects_anything_without_a_key():
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(TypeError, match="carrying key and name"):
        qc.set_ir(Block(1, 0, None), ir=_IR("", "No key"))
    with pytest.raises(ValueError, match="slot must be 0 or 1"):
        qc.set_ir(Block(1, 0, None), ir=_IR("CIR_x", "x"), slot=2)


def test_list_irs_asks_with_type_1_and_drops_keyless_plugin_assets():
    """The 588 plugin IRs expose a name and no key, and the unit cannot load them."""
    listing = pa.FileMessage(request_id=1)
    listing.folder.key = "local_ir_root"
    loadable = listing.folder.files.add()
    loadable.key, loadable.name = "CIR_abc", "Mine"
    listing.folder.files.add().name = "Plugin Asset With No Key"
    qc = client.QuadCortex(StateTransport(listing))
    got = qc.list_irs()
    assert [e.name for e in got] == ["Mine"]
    asked = qc._t.sent[-1]
    assert asked.type == 1
    assert asked.action == pa.MessageAction.READ
    match = qc._t.matches[-1]
    assert match(listing) is True
    other = pa.FileMessage(request_id=1)
    other.folder.key = "/opt/neuraldsp/impulse_responses"
    assert match(other) is False


class _FavEntry:
    def __init__(self, name, folder_key, folder_name, is_factory=False):
        self.name, self.folder_key = name, folder_key
        self.folder_name, self.is_factory = folder_name, is_factory


def test_add_favorite_sends_one_entry_with_the_flag_and_create():
    """The shape captured from the unit: CREATE, is_favorites, a single item."""
    qc = client.QuadCortex(FakeTransport())
    entry = _FavEntry("Brit 2203", "/opt/neuraldsp/Factory Library",
                      "Factory Library", is_factory=True)
    qc.add_favorite(entry, verify=False)
    sent = qc._t.sent[-1]
    assert sent.action == pa.MessageAction.CREATE
    assert sent.is_favorites is True
    assert len(sent.items) == 1
    assert sent.items[0].name == "Brit 2203"
    assert sent.items[0].folder_key == "/opt/neuraldsp/Factory Library"
    assert sent.items[0].is_factory is True


def test_remove_favorite_uses_delete():
    qc = client.QuadCortex(FakeTransport())
    qc.remove_favorite(_FavEntry("x", "/k", "k"), verify=False)
    assert qc._t.sent[-1].action == pa.MessageAction.DELETE


def test_favorite_requires_a_name_and_folder_key():
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(TypeError, match="name and folder_key"):
        qc.add_favorite(_FavEntry("", "", ""), verify=False)
    assert qc._t.sent == []


def test_add_favorite_verifies_via_the_echo():
    """The device echoes the changed entry back; that is the only confirmation."""
    echo = pa.RecentsFavoritesMessage(is_favorites=True)
    echo.items.add().name = "Brit 2203"
    qc = client.QuadCortex(StateTransport(echo))
    got = qc.add_favorite(_FavEntry("Brit 2203", "/k", "k"))
    assert got is echo
    match = qc._t.matches[-1]
    assert match(echo) is True
    # a Recents push (no flag) must not be mistaken for the acknowledgement
    recents = pa.RecentsFavoritesMessage()
    recents.items.add().name = "Brit 2203"
    assert match(recents) is False


def test_add_favorite_explains_a_silent_mismatch_on_timeout():
    """A wrong folder_key is ignored in silence, so the timeout has to teach."""

    class NoEcho(FakeTransport):
        def await_broadcast(self, expected_class, trigger, timeout=40.0, match=None):
            trigger()
            raise TimeoutError("no RecentsFavoritesMessage broadcast")

    qc = client.QuadCortex(NoEcho())
    with pytest.raises(TimeoutError, match="IGNORED SILENTLY"):
        qc.add_favorite(_FavEntry("Fuzz This", "/media/p4/Presets/My Presets",
                                  "My Presets"), timeout=0.01)
    # the write still went out; it is the acknowledgement that never came
    assert qc._t.sent[-1].items[0].name == "Fuzz This"


def test_set_hold_timing_writes_the_index_not_the_milliseconds():
    """The device stores an index; 800 ms is 3, which is what the unit showed."""
    qc = client.QuadCortex(FakeTransport())
    qc.set_hold_timing(Milliseconds(800))
    assert qc._t.sent[-1].hold_timing == 3
    qc.set_hold_timing(Milliseconds(500))
    assert qc._t.sent[-1].hold_timing == 0
    qc.set_hold_timing(Milliseconds(1000))
    assert qc._t.sent[-1].hold_timing == 5


@pytest.mark.parametrize("bad", [450, 550, 1100, 3, 0])
def test_set_hold_timing_rejects_values_the_unit_does_not_offer(bad):
    """The field takes any integer unvalidated, so the check has to be here."""
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(ValueError, match="hold timing must be one of"):
        qc.set_hold_timing(Milliseconds(bad))
    assert qc._t.sent == []


def test_hold_timing_ms_converts_back():
    push = pa.GeneralSettingsMessage(action=pa.MessageAction.UPDATE, hold_timing=3)
    push.scene_block_bypass = 0
    qc = client.QuadCortex(StateTransport(push))
    assert qc.hold_timing_ms() == 800


def test_hold_timing_ms_refuses_to_invent_a_value_for_an_out_of_range_index():
    push = pa.GeneralSettingsMessage(action=pa.MessageAction.UPDATE, hold_timing=5000)
    push.scene_block_bypass = 0
    qc = client.QuadCortex(StateTransport(push))
    with pytest.raises(ValueError, match="outside the six values"):
        qc.hold_timing_ms()


def test_update_settings_rejects_unknown_fields():
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(TypeError, match="no field"):
        qc.update_settings(nonsense=1)
    assert qc._t.sent == []


def test_update_settings_refuses_the_power_and_wifi_commands():
    # power_option can shut the unit down or reboot it; these are not settings.
    qc = client.QuadCortex(FakeTransport())
    for field in ("power_option", "reset_wifi_networks"):
        with pytest.raises(ValueError, match="device commands"):
            qc.update_settings(**{field: 1})
    assert qc._t.sent == []


def test_set_scene_bypass_behavior_writes_the_enum():
    qc = client.QuadCortex(FakeTransport())
    qc.set_scene_bypass_behavior(SceneBypassBehavior.NEVER_OVERWRITE)
    assert qc._t.sent[-1].scene_block_bypass == 2


def test_input_and_output_level_writes_are_sparse_and_port_keyed():
    # One port per message: writing one input's level left the other three
    # byte-identical on hardware.
    qc = client.QuadCortex(FakeTransport())
    qc.set_input_level(5, Encoded(0.25))
    ports = qc._t.sent[-1].settings.in_port
    assert len(ports) == 1
    assert ports[0].input_port_id == 5
    assert ports[0].level == pytest.approx(0.25)
    assert not qc._t.sent[-1].settings.out_port
    assert len(qc._t.sent) == 1

    qc.set_output_level(9, Encoded(0.5))
    out = qc._t.sent[-1].settings.out_port
    assert (out[0].output_port_id, round(out[0].level, 3)) == (9, 0.5)


def test_global_eq_and_mode_and_gig_view_writes():
    qc = client.QuadCortex(FakeTransport())
    qc.set_global_eq_bypassed(False)
    assert qc._t.sent[-1].bypassed is False
    qc.set_mode(2)
    assert qc._t.sent[-1].mode == 2
    qc.set_gig_view(True)
    assert qc._t.sent[-1].show is True


def test_capture_screen_waits_for_the_uncorrelated_png_update():
    png = b"\x89PNG\r\n\x1a\nframe"
    push = pa.RemoteControlMessage(
        action=pa.MessageAction.UPDATE,
        screenshot=pa.RemoteControlScreenshot(payload=png),
    )
    transport = StateTransport(push)
    qc = client.QuadCortex(transport)

    assert qc.capture_screen(timeout=3.5) == png
    assert qc._remote_control_ready is True

    sent = transport.sent[-1]
    assert isinstance(sent, pa.RemoteControlMessage)
    assert sent.action == pa.MessageAction.READ
    assert sent.HasField("screenshot")
    assert not sent.screenshot.HasField("payload")
    assert not sent.HasField("request_id")
    assert sent.SerializeToString().hex() == "08032200"
    match = transport.matches[-1]
    assert match(push) is True
    assert match(pa.RemoteControlMessage(
        action=pa.MessageAction.UPDATE,
        screenshot=pa.RemoteControlScreenshot(payload=b"not a png"),
    )) is False
    assert match(pa.RemoteControlMessage(
        action=pa.MessageAction.READ,
        screenshot=pa.RemoteControlScreenshot(payload=png),
    )) is False


def test_tap_screen_primes_then_sends_the_verified_inverted_pair():
    transport = StateTransport(pa.RemoteControlMessage(
        action=pa.MessageAction.UPDATE,
        screenshot=pa.RemoteControlScreenshot(
            payload=b"\x89PNG\r\n\x1a\nprime"),
    ))
    qc = client.QuadCortex(transport)

    qc.tap_screen(184, 147)

    prime, begin, end = transport.sent
    assert prime.SerializeToString().hex() == "08032200"
    assert begin.SerializeToString().hex() == "08011a0c0d0000384315000013431801"
    assert end.SerializeToString().hex() == "08011a0a0d000038431500001343"
    assert (begin.mouse.x, begin.mouse.y, begin.mouse.type) == pytest.approx(
        (184, 147, pa.RemoteControlMouse.RELEASE))
    assert (end.mouse.x, end.mouse.y, end.mouse.type) == pytest.approx(
        (184, 147, pa.RemoteControlMouse.PRESS))
    assert transport.sequence_timing == (0.3, 0.02)

    qc.tap_screen(184, 147)
    assert len(transport.sent) == 5
    assert transport.sequence_timing == (0.0, 0.02)


@pytest.mark.parametrize("x,y,exception", [
    (-1, 0, ValueError),
    (800, 0, ValueError),
    (0, 480, ValueError),
    (float("inf"), 0, ValueError),
    (float("nan"), 0, ValueError),
    (True, 0, TypeError),
    ("184", 147, TypeError),
])
def test_tap_screen_rejects_invalid_coordinates(x, y, exception):
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(exception):
        qc.tap_screen(x, y)
    assert qc._t.sent == []


def test_mode_reader_waits_for_a_push_carrying_mode():
    push = pa.ModeMessage(action=pa.MessageAction.UPDATE, mode=1)
    push.available_modes.modes.extend([0, 1, 2])
    qc = client.QuadCortex(StateTransport(push))
    got = qc.mode()
    assert got.mode == 1
    assert list(got.available_modes.modes) == [0, 1, 2]
    assert qc._t.matches[-1](pa.ModeMessage()) is False


# -- moving blocks and creating branches ---------------------------------------


def test_move_block_sends_a_row_and_column_addressed_move():
    qc = client.QuadCortex(FakeTransport())
    qc.move_block(Block(2, 1), Block(2, 7))
    msg = qc._t.sent[-1]
    assert isinstance(msg, pa.GridMoveMessage)
    mv = msg.move[0]
    assert (mv.from_row, mv.from_col, mv.to_row, mv.to_col, mv.is_drop) == (2, 1, 2, 7, True)
    # the advisory grid snapshot is not sent
    assert not msg.HasField("grid")


def test_set_split_activates_a_branch_on_an_even_row():
    qc = client.QuadCortex(FakeTransport())
    qc.set_split(row=0, split_column=3, mix_column=5)
    chain = qc._t.sent[-1].preset.chains[0]
    assert chain.row == 0
    assert (chain.split_control_points[0].split,
            chain.split_control_points[0].mix) == (3, 5)


def test_set_split_allows_a_branch_that_never_rejoins():
    qc = client.QuadCortex(FakeTransport())
    qc.set_split(row=2, split_column=2, mix_column=-1)
    scp = qc._t.sent[-1].preset.chains[0].split_control_points[0]
    assert (scp.split, scp.mix) == (2, -1)


def test_clear_split_writes_the_minus_one_sentinels():
    qc = client.QuadCortex(FakeTransport())
    qc.clear_split(row=0)
    scp = qc._t.sent[-1].preset.chains[0].split_control_points[0]
    assert (scp.split, scp.mix) == (-1, -1)


def test_split_helpers_refuse_an_odd_row():
    qc = client.QuadCortex(FakeTransport())
    for call in (lambda: qc.set_split(1, 2, 3), lambda: qc.clear_split(3)):
        with pytest.raises(ValueError, match="row 0 or"):
            call()
    assert qc._t.sent == []


def test_set_expression_bypass_writes_both_halves():
    qc = client.QuadCortex(FakeTransport())
    qc.set_expression_bypass(Block(0, 2), pedal=1, mode=1, invert=True,
                             delay_ms=Milliseconds(250), latch_emulation=True)
    model = qc._t.sent[-1].preset.chains[0].models[0]
    assert model.column == 2
    be = model.bypass_expression[0]
    assert (be.expression, be.expression_min, be.expression_max) == (1, 0.0, 1.0)
    info = model.expression_bypass_info[0]
    assert (info.type, info.invert, info.delay_ms, info.latch_emulation) \
        == (1, True, 250, True)


# -- I/O ports, tuner, looper ---------------------------------------------------


def test_set_input_port_sends_one_field_per_message():
    # The device drops some fields that share a port entry - impedance and output mute
    # both failed when paired and both worked alone - so each goes in its own message.
    qc = client.QuadCortex(FakeTransport())
    qc.set_input_port(2, input_type=0.5)
    port = qc._t.sent[-1].settings.in_port[0]
    assert port.input_port_id == 2
    assert port.input_type == pytest.approx(0.5)
    assert not port.HasField("level")

    qc = client.QuadCortex(FakeTransport())
    qc.set_input_port(2, level=Encoded(0.4), impedance=0.875, input_type=0.5,
                      ground_lift=0.0)
    assert len(qc._t.sent) == 4, "four fields, four messages"
    for msg in qc._t.sent:
        port = msg.settings.in_port[0]
        assert port.input_port_id == 2
        set_fields = [f.name for f, _ in port.ListFields() if f.name != "input_port_id"]
        assert len(set_fields) == 1, f"{set_fields} shared one message"


def test_set_input_level_still_works_and_delegates():
    qc = client.QuadCortex(FakeTransport())
    qc.set_input_level(5, Encoded(0.25))
    port = qc._t.sent[-1].settings.in_port[0]
    assert (port.input_port_id, round(port.level, 3)) == (5, 0.25)
    assert not port.HasField("input_type")


def test_set_output_port_sends_ground_lift_and_mute_separately():
    qc = client.QuadCortex(FakeTransport())
    qc.set_output_port(1, ground_lift=1.0, mute=True)
    assert len(qc._t.sent) == 2, "mute must not share a message with ground lift"
    fields = []
    for msg in qc._t.sent:
        port = msg.settings.out_port[0]
        assert port.output_port_id == 1
        fields += [f.name for f, _ in port.ListFields() if f.name != "output_port_id"]
    assert sorted(fields) == ["ground_lift", "mute"]


def test_set_output_port_needs_something_to_set():
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(TypeError):
        qc.set_output_port(1)
    assert qc._t.sent == []


def test_usb_midi_and_pairing_writes():
    qc = client.QuadCortex(FakeTransport())
    qc.set_usb_port(dry_wet=1.0)
    assert qc._t.sent[-1].settings.usb_port.dry_wet == pytest.approx(1.0)
    assert not qc._t.sent[-1].settings.usb_port.HasField("level")

    qc.set_midi_thru(True)
    assert qc._t.sent[-1].settings.midi_port.midi_thru == pytest.approx(1.0)
    qc.set_midi_thru(False)
    assert qc._t.sent[-1].settings.midi_port.midi_thru == pytest.approx(0.0)

    qc.set_output_pairing(out3_4=False)
    msg = qc._t.sent[-1]
    assert msg.out3_4_linked is False
    assert not msg.HasField("xlr1_2_linked")


def test_tuner_and_looper_readers_and_the_tuner_input_write():
    qc = client.QuadCortex(FakeTransport())
    qc.set_tuner_input(2)
    assert qc._t.sent[-1].input_port_id == 2
    qc.show_tuner(True)
    assert qc._t.sent[-1].show is True

    tuner_push = pa.TunerMessage(action=pa.MessageAction.UPDATE, input_port_id=1)
    qc2 = client.QuadCortex(StateTransport(tuner_push))
    assert qc2.tuner().input_port_id == 1
    assert qc2._t.matches[-1](pa.TunerMessage()) is False

    looper_push = pa.LooperMessage(action=pa.MessageAction.UPDATE)
    looper_push.status.state = 1
    looper_push.status.free_samples = 27131904
    qc3 = client.QuadCortex(StateTransport(looper_push))
    got = qc3.looper()
    assert got.status.state == 1
    assert got.status.free_samples == 27131904
    assert qc3._t.matches[-1](pa.LooperMessage()) is False


# -- folder discovery ----------------------------------------------------------
# One File READ makes the device enumerate its whole tree - 399 folders on the
# observed unit, not just the two setlists.


class CollectingTransport(FakeTransport):
    def __init__(self, pushes):
        super().__init__()
        self.pushes = pushes
        self.seconds = None

    def collect(self, expected_class, trigger, seconds, match=None):
        trigger()
        self.seconds = seconds
        return [m for m in self.pushes
                if isinstance(m, expected_class) and (match is None or match(m))]


def _folder(key, name, slots, occupied, factory):
    m = pa.FileMessage(action=pa.MessageAction.UPDATE)
    m.folder.key = key
    m.folder.name = name
    m.folder.is_factory = factory
    for i in range(slots):
        f = m.folder.files.add()
        f.index = i
        if i < occupied:
            f.name = f"p{i}"
    return m


def test_list_folders_collects_every_pushed_folder():
    pushes = [
        _folder("/media/p4/Presets/My Presets", "My Presets", 4, 2, False),
        _folder("local_nc_root", "Captures Library", 3, 3, False),
        _folder("", "nameless", 1, 0, False),          # no key: ignored
    ]
    qc = client.QuadCortex(CollectingTransport(pushes))
    got = qc.list_folders(seconds=5)
    assert [f.key for f in got] == ["/media/p4/Presets/My Presets", "local_nc_root"]
    mine = got[0]
    assert (mine.name, mine.slots, mine.occupied, mine.is_factory) \
        == ("My Presets", 4, 2, False)
    assert qc._t.seconds == 5
    assert qc._t.sent[-1].action == pa.MessageAction.READ


def test_list_folders_keeps_the_fullest_push_per_key():
    # The device pushes a key more than once, and an early push can be short.
    pushes = [_folder("k", "K", 1, 1, False), _folder("k", "K", 6, 4, False)]
    qc = client.QuadCortex(CollectingTransport(pushes))
    got = qc.list_folders(seconds=1)
    assert len(got) == 1
    assert (got[0].slots, got[0].occupied) == (6, 4)


def test_favorites_asks_with_the_flag_and_returns_the_entries():
    """READ + is_favorites gets Favorites; the REPLY does not set the flag."""
    push = pa.RecentsFavoritesMessage(action=pa.MessageAction.UPDATE, request_id=1)
    it = push.items.add()
    it.name = "Brit 2203"
    it.folder_key = "/opt/neuraldsp/Factory Library"
    it.folder_name = "Factory Library"
    qc = client.QuadCortex(StateTransport(push))
    got = qc.favorites()
    assert [e.name for e in got] == ["Brit 2203"]
    asked = qc._t.sent[-1]
    assert asked.action == pa.MessageAction.READ
    assert asked.is_favorites is True
    assert asked.HasField("request_id")


def test_favorites_matches_on_request_id_not_the_flag():
    """Matching on the flag rejected every valid reply and hid this feature."""
    push = pa.RecentsFavoritesMessage(request_id=1)
    push.items.add().name = "Brit 2203"
    qc = client.QuadCortex(StateTransport(push))
    qc.favorites()
    match = qc._t.matches[-1]
    assert match(push) is True                                   # right id, no flag
    other = pa.RecentsFavoritesMessage(request_id=999)
    other.items.add().name = "Something Else"
    assert match(other) is False                                 # a different request
    assert match(pa.RecentsFavoritesMessage()) is False           # no id at all


def test_favorites_returns_an_empty_list_when_there_are_none():
    """An empty Favorites list is a real, empty push - not a missing answer."""
    qc = client.QuadCortex(StateTransport(pa.RecentsFavoritesMessage(request_id=1)))
    assert qc.favorites() == []


def test_favorites_retries_before_giving_up():
    """The first read after connecting is often dropped, so one timeout is normal."""

    class FlakyOnce(FakeTransport):
        def __init__(self):
            super().__init__()
            self.tries = 0

        def await_broadcast(self, expected_class, trigger, timeout=40.0, match=None):
            trigger()
            self.tries += 1
            if self.tries == 1:
                raise TimeoutError("dropped, as the device does")
            reply = pa.RecentsFavoritesMessage(request_id=self.sent[-1].request_id)
            reply.items.add().name = "Brit 2203"
            return reply

    qc = client.QuadCortex(FlakyOnce())
    assert [e.name for e in qc.favorites()] == ["Brit 2203"]
    assert qc._t.tries == 2


def test_favorites_timeout_says_reads_are_lazy():
    class NeverAnswers(FakeTransport):
        def await_broadcast(self, expected_class, trigger, timeout=40.0, match=None):
            trigger()
            raise TimeoutError("nothing")

    qc = client.QuadCortex(NeverAnswers())
    with pytest.raises(TimeoutError, match="Reads are lazy"):
        qc.favorites(timeout=0.03, attempts=2)


# -- submessage writes replace the whole submessage -----------------------------
# Sending master_volume_assignment with one flag set left the other three FALSE on
# hardware, quietly stopping the knob controlling those outputs. So these read the
# current value and merge, rather than sending one field.


def _settings_push(**mv):
    m = pa.GeneralSettingsMessage(action=pa.MessageAction.UPDATE)
    m.scene_block_bypass = 0
    for k, v in mv.items():
        setattr(m.master_volume_assignment, k, v)
    return m


def test_set_master_volume_assignment_sends_all_four_flags():
    push = _settings_push(out12=True, out34=True, send12=True, headphones=True)
    qc = client.QuadCortex(StateTransport(push))
    qc.set_master_volume_assignment(send12=False)
    got = qc._t.sent[-1].master_volume_assignment
    assert (got.out12, got.out34, got.send12, got.headphones) \
        == (True, True, False, True), "the untouched flags must be carried through"


def test_set_global_bypass_needs_four_rows_and_carries_the_other_one():
    push = _settings_push(out12=True)
    push.global_bypass_ir.row2 = True
    qc = client.QuadCortex(StateTransport(push))
    qc.set_global_bypass(cab=(True, False, False, False))
    msg = qc._t.sent[-1]
    assert (msg.global_bypass_cab.row1, msg.global_bypass_cab.row2) == (True, False)
    assert msg.global_bypass_ir.row2 is True, "the untouched collection is preserved"

    with pytest.raises(ValueError, match="four booleans"):
        qc.set_global_bypass(cab=(True, False))
    with pytest.raises(TypeError):
        qc.set_global_bypass()


def test_set_global_eq_band_is_sparse_by_parameter_index():
    qc = client.QuadCortex(FakeTransport())
    qc.set_global_eq_band(1, Encoded(0.6))
    params = qc._t.sent[-1].parameters
    assert len(params) == 1
    assert (params[0].parameter_index, round(params[0].value, 3)) == (1, 0.6)


def test_set_mode_cycle_replaces_the_whole_list():
    qc = client.QuadCortex(FakeTransport())
    qc.set_mode_cycle([1, 0, 2])
    assert list(qc._t.sent[-1].available_modes.modes) == [1, 0, 2]


# -- list-valued (comboBox) parameters -----------------------------------------
# A list parameter stores index / (count - 1), and the option NAMES live in the
# preset rather than the catalog. Confirmed both directions on hardware: the unit
# stored 0.2 for "Input 2" out of 16 options, and a host write of 3/17 out of 18
# read back as the same choice.


def test_option_value_maps_a_name_to_the_wire_value():
    opts = ["Off", "Follow Input", "Input 1", "Input 2"]
    assert client.option_value(opts, "Off") == 0.0
    assert client.option_value(opts, "Input 3" if False else "Input 2") == 1.0
    assert client.option_value(opts, "Input 1") == pytest.approx(2 / 3)
    assert client.option_value(opts, 1) == pytest.approx(1 / 3)


def test_option_value_matches_the_captured_side_chain_case():
    # 16 options, "Input 2" at index 3, stored as 0.2 by the unit.
    opts = ["Off", "Follow Input", "Input 1", "Input 2", "Input 1/2", "Return 1",
            "Return 2", "Return 1/2", "USB input 5", "USB input 6", "USB input 7",
            "USB input 8", "USB input 5/6", "USB input 7/8", "Legendary 87 (M)",
            "Microtubes VMT"]
    assert client.option_value(opts, "Input 2") == pytest.approx(0.2)
    assert client.option_at(opts, 0.2) == "Input 2"


def test_option_value_rejects_an_unknown_name_or_index():
    with pytest.raises(ValueError):
        client.option_value([], "anything")
    with pytest.raises(ValueError):
        client.option_value(["a", "b"], 5)
    with pytest.raises(ValueError):
        client.option_value(["a", "b"], "c")


def test_option_at_round_trips_every_index():
    opts = [f"o{i}" for i in range(18)]
    for i, name in enumerate(opts):
        assert client.option_at(opts, client.option_value(opts, name)) == name


def test_set_param_option_resolves_the_name_through_the_preset():
    p = preset.BinaryPreset()
    chain = p.chains.add()
    chain.row = 1
    m = chain.models.add()
    m.column = 0
    m.hash = 5018
    for i in range(7):
        m.params.add().index = i
    m.params[6].dynamic_steps.extend(["Off", "Follow Input", "Input 1", "Input 2"])

    qc = client.QuadCortex(FakeTransport())
    qc.set_param_option(Block(1, 0), 6, option="Input 2", source=p)
    written = qc._t.sent[-1].preset.chains[0].models[0].params[0]
    assert written.index == 6
    assert written.param_values[0].float_value == pytest.approx(1.0)


def test_set_param_option_resolves_a_parameter_NAME_via_the_preset_block():
    # The model comes from the preset, so no model= argument is needed.
    p = preset.BinaryPreset()
    chain = p.chains.add()
    chain.row = 1
    m = chain.models.add()
    m.column = 0
    m.hash = 5005
    for i in range(1):
        m.params.add().index = i
    m.params[0].dynamic_steps.extend(["Off", "On"])

    qc = client.QuadCortex(FakeTransport())
    qc._catalog = catalog.parse_model_repo(_sample_repo_payload())
    qc.set_param_option(Block(1, 0), "THRESHOLD", option="On", source=p)
    written = qc._t.sent[-1].preset.chains[0].models[0].params[0]
    assert written.index == 0
    assert written.param_values[0].float_value == pytest.approx(1.0)


def test_set_param_option_needs_the_block_to_be_in_the_source_preset():
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(ValueError, match="no block at row"):
        qc.set_param_option(Block(3, 7), "SOURCE", option="Off",
                            source=preset.BinaryPreset())


# -- output mute must travel alone ---------------------------------------------


def test_set_output_mute_sends_only_the_port_and_the_flag():
    # A message carrying mute AND ground_lift left the port unmuted on hardware;
    # mute alone worked, matching the unit's own broadcast.
    qc = client.QuadCortex(FakeTransport())
    qc.set_output_mute(1, True)
    port = qc._t.sent[-1].settings.out_port[0]
    assert (port.output_port_id, port.mute) == (1, True)
    assert not port.HasField("ground_lift")
    assert not port.HasField("level")


# -- tuner reference pitch is an offset ---------------------------------------


def test_set_tuner_reference_writes_the_offset_from_440():
    # Changing FREQ 440 -> 442 on the unit broadcast frequency: 1.99999809.
    qc = client.QuadCortex(FakeTransport())
    qc.set_tuner_reference(Hertz(2.0))
    assert qc._t.sent[-1].frequency == pytest.approx(2.0)
    qc.set_tuner_reference(Hertz(0.0))
    assert qc._t.sent[-1].frequency == pytest.approx(0.0)


# -- setlists are siblings, not children --------------------------------------


def test_create_setlist_uses_a_sibling_path_under_the_presets_root():
    qc = client.QuadCortex(FakeTransport())
    path = qc.create_setlist("probe")
    assert path == "/media/p4/Presets/probe"
    folder = qc._t.sent[-1].folder
    assert folder.key == "/media/p4/Presets/probe"
    assert folder.name == "probe"
    assert folder.is_factory is False
    assert "My Presets" not in folder.key, "a setlist is not nested inside My Presets"


# -- master volume, pinning, setlist deletion ----------------------------------


def test_master_volume_reads_as_the_displayed_number():
    """0..1 on the wire, ``round(v * 100)`` on screen.

    This test also used to assert ``not hasattr(qc, "set_master_volume")``, on
    the strength of a hardware measurement that a write was accepted and ignored.
    That measurement was a stale read - ``master_volume()`` called straight after
    a write returns the PREVIOUS value - and the write had been landing all
    along. A test can lock in a wrong fact just as firmly as a docstring can.
    """
    push = pa.MasterVolumeMessage(action=pa.MessageAction.UPDATE, volume=0.471074373)
    qc = client.QuadCortex(StateTransport(push))
    assert round(qc.master_volume().volume * 100) == 47
    assert qc._t.matches[-1](pa.MasterVolumeMessage()) is False


def test_pin_model_sends_no_action_because_update_does_nothing():
    # The unit's own broadcast carries no action field; an UPDATE is ignored.
    qc = client.QuadCortex(FakeTransport())
    qc.pin_model(4006)
    msg = qc._t.sent[-1]
    assert list(msg.models) == [4006]
    assert msg.action == pa.MessageAction.CREATE, "the default action, as the unit sends"


def test_unpin_model_uses_delete():
    qc = client.QuadCortex(FakeTransport())
    qc.unpin_model(4006)
    msg = qc._t.sent[-1]
    assert msg.action == pa.MessageAction.DELETE
    assert list(msg.models) == [4006]


def test_pin_model_accepts_a_catalog_model():
    qc = client.QuadCortex(FakeTransport())
    qc._catalog = catalog.parse_model_repo(_sample_repo_payload())
    qc.pin_model(qc._catalog[5005])
    assert list(qc._t.sent[-1].models) == [5005]


def test_delete_setlist_addresses_the_folder_key():
    qc = client.QuadCortex(FakeTransport())
    qc.delete_setlist("probe")
    msg = qc._t.sent[-1]
    assert msg.action == pa.MessageAction.DELETE
    assert msg.folder.key == "/media/p4/Presets/probe"
    assert msg.folder.name == "probe"


def test_looper_state_enum_omits_the_value_never_observed():
    # Overdub was the obvious guess for 3 and turned out to be 6, so 3 stays out.
    from pyquadcortex.protocol.enums import LooperState
    assert [int(s) for s in LooperState] == [1, 2, 4, 5, 6]
    assert int(LooperState.OVERDUBBING) == 6
    assert 3 not in [int(s) for s in LooperState], "3 was never seen; do not invent it"


def test_expression_bypass_mode_numbering_is_not_the_manual_order():
    # Each set deliberately on the unit with a scene change fencing them apart:
    # Heel-Toe stored 2, Switch 1, Stop 0. An earlier release had this reversed.
    from pyquadcortex.protocol.enums import ExpressionSwitchMode as M
    assert (int(M.STOP), int(M.SWITCH), int(M.HEEL_TOE)) == (0, 1, 2)


def test_set_expression_bypass_accepts_the_enum():
    qc = client.QuadCortex(FakeTransport())
    from pyquadcortex.protocol.enums import ExpressionSwitchMode as M
    qc.set_expression_bypass(Block(0, 1), pedal=1, mode=M.HEEL_TOE)
    assert qc._t.sent[-1].preset.chains[0].models[0].expression_bypass_info[0].type == 2


# -- copying presets and setlists ----------------------------------------------
# Neither is a device operation. The unit's paste broadcasts the same
# File{CREATE, folder{key, files}} shape as a Save As, just aimed at another folder
# key, and its setlist duplicate only NARRATES progress via BulkOperation. So both
# are compositions of recall + save.


class RecallSaveTransport(FakeTransport):
    """Answers a recall with a preset and a listing with canned entries."""

    def __init__(self, preset_name="Brit 2203", entries=()):
        super().__init__()
        self.preset_name = preset_name
        self.entries = entries
        self.calls = []

    def await_broadcast(self, expected_class, trigger, timeout=40.0, match=None):
        trigger()
        self.calls.append(expected_class.__name__)
        if expected_class is pa.RecallPresetMessage:
            m = pa.RecallPresetMessage(action=pa.MessageAction.UPDATE)
            m.preset.name = self.preset_name
            return m
        listing = pa.FileMessage(action=pa.MessageAction.UPDATE)
        listing.folder.key = "/media/p4/Presets/dest"
        for index, name in self.entries:
            f = listing.folder.files.add()
            f.index = index
            if name:
                f.name = name
        # echo back whatever was saved, so the save's confirm step resolves at
        # once instead of polling
        for msg in self.sent:
            if isinstance(msg, pa.FileMessage) and len(msg.folder.files) \
                    and msg.folder.files[0].HasField("name"):
                src = msg.folder.files[0]
                f = listing.folder.files.add()
                f.index = src.index
                f.name = src.name
        return listing

    def last_save(self):
        """The File CREATE this transport was asked to store, ignoring the READs
        the confirm step sends afterwards."""
        for msg in reversed(self.sent):
            if isinstance(msg, pa.FileMessage) and len(msg.folder.files) \
                    and msg.folder.files[0].HasField("name"):
                return msg
        raise AssertionError("no File CREATE carrying a named entry was sent")


def test_copy_preset_recalls_the_source_then_saves_into_the_destination():
    t = RecallSaveTransport(entries=[(0, "already here")])
    qc = client.QuadCortex(t)
    qc.copy_preset("/media/p4/Presets/src", 4, "/media/p4/Presets/dest")
    saved = qc._t.last_save()
    assert saved.folder.key == "/media/p4/Presets/dest"
    entry = saved.folder.files[0]
    assert entry.name == "Brit 2203", "the source preset's own name by default"
    assert entry.index == 1, "the first free slot, 0 being taken"


def test_copy_preset_honours_an_explicit_slot_and_name():
    qc = client.QuadCortex(RecallSaveTransport())
    qc.copy_preset("/media/p4/Presets/src", 0, "/media/p4/Presets/dest",
                   to_position=7, name="renamed")
    entry = qc._t.last_save().folder.files[0]
    assert (entry.index, entry.name) == (7, "renamed")


def test_copy_preset_does_recall_the_source_which_changes_the_grid():
    # Worth asserting: this is not a background copy, it loads the preset.
    t = RecallSaveTransport()
    qc = client.QuadCortex(t)
    qc.copy_preset("/media/p4/Presets/src", 2, "/media/p4/Presets/dest",
                   to_position=0)
    assert "RecallPresetMessage" in t.calls
    assert any(isinstance(m, pa.SetlistPositionMessage) for m in qc._t.sent)


# -- Global EQ by band, not by wire index --------------------------------------
# 5 parameters per band at offsets GAIN 0, FREQUENCY 1, Q 2, TYPE 3. Established by
# changing each of band 1's controls and seeing which index moved, then checked
# against the whole 28-parameter list, whose defaults line up as a five-band
# parametric EQ should: identical gains and Qs, rising frequencies, and
# shelf/peak/peak/peak/shelf types.


def test_set_global_eq_maps_band_and_control_to_the_wire_index():
    qc = client.QuadCortex(FakeTransport())
    qc.set_global_eq(band=1, gain=Encoded(0.75))
    assert qc._t.sent[-1].parameters[0].parameter_index == 0
    qc.set_global_eq(band=3, gain=Encoded(0.75))
    assert qc._t.sent[-1].parameters[0].parameter_index == 10
    qc.set_global_eq(band=5, q=Encoded(0.2))
    assert qc._t.sent[-1].parameters[0].parameter_index == 22
    qc.set_global_eq(band=2, frequency=Encoded(0.4))
    assert qc._t.sent[-1].parameters[0].parameter_index == 6


def test_set_global_eq_sends_the_filter_type_as_an_option_value():
    from pyquadcortex.protocol.enums import GlobalEQFilter
    qc = client.QuadCortex(FakeTransport())
    qc.set_global_eq(band=1, filter_type=GlobalEQFilter.LOW_SHELF)
    p = qc._t.sent[-1].parameters[0]
    assert (p.parameter_index, p.value) == (3, pytest.approx(1.0))
    qc.set_global_eq(band=1, filter_type=GlobalEQFilter.PEAK)
    assert qc._t.sent[-1].parameters[0].value == pytest.approx(0.0)
    qc.set_global_eq(band=5, filter_type=GlobalEQFilter.HIGH_SHELF)
    p = qc._t.sent[-1].parameters[0]
    assert (p.parameter_index, p.value) == (23, pytest.approx(0.75))


def test_set_global_eq_sends_only_the_controls_given():
    qc = client.QuadCortex(FakeTransport())
    qc.set_global_eq(band=2, gain=Encoded(0.6), q=Encoded(0.1))
    indices = [m.parameters[0].parameter_index for m in qc._t.sent]
    assert indices == [5, 7], "gain and Q only, no frequency or type write"


def test_set_global_eq_validates_the_band_and_needs_a_control():
    qc = client.QuadCortex(FakeTransport())
    for bad in (0, 6, -1):
        with pytest.raises(ValueError, match="band must be"):
            qc.set_global_eq(band=bad, gain=Encoded(0.5))
    with pytest.raises(TypeError):
        qc.set_global_eq(band=1)
    assert qc._t.sent == []


def test_set_global_eq_enabled_is_the_band_bypass_at_offset_4():
    # 1.0 means the band is ACTIVE; 0.0 bypasses it. Confirmed by toggling band 1's
    # bypass on the unit, and every band ships at 1.0.
    qc = client.QuadCortex(FakeTransport())
    qc.set_global_eq(band=1, enabled=False)
    p = qc._t.sent[-1].parameters[0]
    assert (p.parameter_index, p.value) == (4, pytest.approx(0.0))
    qc.set_global_eq(band=3, enabled=True)
    p = qc._t.sent[-1].parameters[0]
    assert (p.parameter_index, p.value) == (14, pytest.approx(1.0))


def test_set_global_eq_output_addresses_the_out_tab_indices():
    qc = client.QuadCortex(FakeTransport())
    qc.set_global_eq_output(out12=True)
    p = qc._t.sent[-1].parameters[0]
    assert (p.parameter_index, p.value) == (26, pytest.approx(1.0))
    qc.set_global_eq_output(level=Encoded(0.5), out34=False)
    indices = [m.parameters[0].parameter_index for m in qc._t.sent[-2:]]
    assert indices == [25, 27]
    with pytest.raises(TypeError):
        qc.set_global_eq_output()


# -- tempo parameter names -----------------------------------------------------
# Mapped by using each control in the unit's Tempo menu in a named order. Two names
# disagree with the catalog: index 4 is MUTE on screen and START in the catalog,
# index 7 is Subdivisions on screen and NOTELENGTH in the catalog. Only the NAMES
# ever disagreed - two releases claimed 8 and 9 were absent from the catalog, and
# SOUND and ROUTING are described there at exactly those indices.


def test_set_tempo_param_resolves_the_screen_names():
    qc = client.QuadCortex(FakeTransport())
    for name, index in (("TEMPO", 0), ("LED LIGHT", 2), ("VOLUME", 3), ("START", 4),
                        ("PLAYBACK", 4), ("PAN", 5), ("TIME SIGNATURE", 6),
                        ("SUBDIVISIONS", 7), ("SOUND", 8), ("ROUTING", 9)):
        qc.set_param(Tempo(), name, Encoded(0.5))
        got = qc._t.sent[-1].preset.tempoProgramData[0].params[0]
        assert got.index == index, f"{name} should resolve to {index}"


def test_tempo_param_mute_is_refused_because_it_is_inverted():
    """The unit DOES label parameter 4 MUTE - inverted, so 1.0 unmutes. Accepting
    the name would silently do the opposite of what the caller asked."""
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(ValueError, match="INVERTED"):
        qc.set_param(Tempo(), "MUTE", Encoded(1.0))
    assert qc._t.sent == []


def test_set_metronome_muted_is_the_inverse_of_running():
    """Traced from the unit's MUTE button: mute-on writes 0.0, mute-off 1.0."""
    qc = client.QuadCortex(FakeTransport())
    qc.set_metronome_muted(True)
    got = qc._t.sent[-1].preset.tempoProgramData[0].params[0]
    assert got.index == 4
    assert got.param_values[0].float_value == 0.0      # muted == silent == 0.0
    qc.set_metronome_muted(False)
    assert qc._t.sent[-1].preset.tempoProgramData[0].params[0] \
        .param_values[0].float_value == 1.0
    # the two APIs are the same parameter and cannot disagree
    qc.set_metronome_running(True)
    a = qc._t.sent[-1].preset.tempoProgramData[0].params[0].param_values[0].float_value
    qc.set_metronome_muted(False)
    b = qc._t.sent[-1].preset.tempoProgramData[0].params[0].param_values[0].float_value
    assert a == b == 1.0


def test_set_metronome_running_writes_the_transport_polarity():
    qc = client.QuadCortex(FakeTransport())
    qc.set_metronome_running(True)
    got = qc._t.sent[-1].preset.tempoProgramData[0].params[0]
    assert got.index == 4
    assert got.param_values[0].float_value == 1.0     # 1.0 = RUNNING
    qc.set_metronome_running(False)
    got = qc._t.sent[-1].preset.tempoProgramData[0].params[0]
    assert got.param_values[0].float_value == 0.0


def test_tempo_param_names_are_case_and_space_tolerant():
    qc = client.QuadCortex(FakeTransport())
    qc.set_param(Tempo(), "routing", Encoded(0.75))
    assert qc._t.sent[-1].preset.tempoProgramData[0].params[0].index == 9
    qc.set_param(Tempo(), " Sound ", Encoded(0.2))
    assert qc._t.sent[-1].preset.tempoProgramData[0].params[0].index == 8


def test_real_units_refused_for_a_tempo_param_the_catalog_does_not_describe():
    # The catalog describes 0-22 while the preset carries 24, so index 23 cannot be
    # converted to real units - refuse rather than guess. It is the one tempo
    # parameter still unattributed, and notably NOT a 14th beat: the beats stop at 22.
    qc = client.QuadCortex(FakeTransport())
    qc._catalog = catalog.parse_model_repo(_sample_repo_payload())
    assert len(qc._catalog[25000].parameters) == 23
    with pytest.raises(ValueError, match="does not describe index 23"):
        qc.set_param(Tempo(), 23, Real(3))
    assert qc._t.sent == []


def test_set_tempo_option_range_checks_against_the_catalogs_step_count():
    # The catalog's `steps` is the option count: ROUTING has 5, so 3 maps to 0.75 -
    # which is what the unit stored for OUT 3/4.
    qc = client.QuadCortex(FakeTransport())
    qc._catalog = catalog.parse_model_repo(_sample_repo_payload())
    qc.set_tempo_option("ROUTING", 3)
    assert qc._t.sent[-1].preset.tempoProgramData[0].params[0] \
        .param_values[0].float_value == pytest.approx(0.75)
    with pytest.raises(ValueError, match="5 options"):
        qc.set_tempo_option("ROUTING", 5)


# -- the TEMPO span ------------------------------------------------------------
# The catalog names TEMPO's bounds MIN_TEMPO / MAX_TEMPO rather than giving
# numbers, and units.FIRMWARE_CONSTANTS supplies 40..240 - so
# the span was measured instead: three screen readings taken against simultaneous
# wire reads on 2026-08-12, each landing on the displayed integer exactly.


#: (wire value, bpm on the unit's screen). The 59 is the one that earns the fit:
#: 111 and 120 sit 9 bpm apart, and two close points cannot distinguish spans -
#: the lesson the lane levels taught after two releases of a wrong one.
MEASURED_TEMPO_POINTS = ((0.095, 59.0), (0.355, 111.0), (0.400, 120.0))


@pytest.mark.parametrize("value,bpm", MEASURED_TEMPO_POINTS)
def test_tempo_bpm_matches_every_measured_point(value, bpm):
    assert client.tempo_bpm(value) == pytest.approx(bpm, abs=0.01)
    assert client.bpm_to_tempo(bpm) == pytest.approx(value, abs=1e-6)


def test_tempo_bpm_refuses_a_tempo_the_unit_does_not_have():
    with pytest.raises(ValueError, match="40..240"):
        client.bpm_to_tempo(300.0)
    with pytest.raises(ValueError, match="40..240"):
        client.bpm_to_tempo(39.0)


def test_set_tempo_param_takes_real_as_bpm_for_index_zero():
    """TEMPO converts through the catalog like everything else now.

    It used to be the one index served by a hand-measured span, which let it
    work with no catalog loaded. It is not special: the device publishes
    min="MIN_TEMPO" max="MAX_TEMPO" and steps="201", and the numbers behind
    those names are in units.FIRMWARE_CONSTANTS. Needing a catalog to speak bpm
    is the honest cost - protocol.bpm_to_tempo is the offline route.
    """
    qc = _lane_client()
    qc.set_param(Tempo(), "TEMPO", Real(111.0))
    sent = qc._t.sent[-1].preset.tempoProgramData[0].params[0]
    assert sent.index == 0
    assert sent.param_values[0].float_value == pytest.approx(0.355, abs=1e-6)


# -- the Tempo menu's MODE switch ----------------------------------------------
# Found 2026-08-12 by capturing every field of every message the device answers in
# each switch position and diffing: exactly one moved, the DEVICE tempo block's
# parameter 1. Three earlier investigations watched for a broadcast on commit,
# correctly found none, and concluded the switch was not on the wire - it is, and
# only a READ finds it.


def _global_tempo_with_mode(value, count=25, keyed=True):
    """A ``GlobalTempo`` push in the shape that carries parameters.

    ``keyed`` reflects what the DEVICE actually sends, which was checked against
    the 2026-08-12 captures rather than assumed: every one of the 25 params
    carries an explicit ``index``, and there it equals position. The first version
    of this helper built them with ``index`` absent - a shape the unit has never
    been observed sending - which made the reader look correct for the wrong
    reason. ``keyed=False`` is kept to prove the positional fallback still works.
    """
    message = pa.GlobalTempoMessage(action=pa.MessageAction.UPDATE)
    for index in range(count):
        param = message.params.add()
        if keyed:
            param.index = index
        param.param_values.add(
            float_value=value if index == client.QuadCortex.TEMPO_MODE_PARAM else 0.0)
    return message


def test_tempo_mode_reads_parameter_one_of_the_device_block():
    fake = FakeTransport()
    fake.broadcast = _global_tempo_with_mode(1.0)
    qc = client.QuadCortex(fake)

    assert qc.tempo_mode() is TempoMode.GLOBAL
    assert isinstance(fake.sent[-1], pa.GlobalTempoMessage)
    assert fake.sent[-1].action == pa.MessageAction.READ


def test_tempo_mode_predicate_rejects_everything_that_cannot_answer():
    """The predicate is the whole instrument, so it is pinned here.

    ``GlobalTempo`` alternates two shapes and only one carries parameters. A
    single earlier READ landed on the clock shape and was written up as a dead
    end - "returned only a running clock" - and that stood for eight releases
    (0.33.0 through 0.40.0). A
    waiter that accepts any ``GlobalTempo`` reproduces it exactly.

    Every case below was chosen because it DISTINGUISHES the real predicate from
    a weaker one. An earlier version of this test used only the clock shape, an
    empty push and a full params push - all three separated by "params non-empty"
    alone - so replacing the predicate with ``len(m.params) > 0`` kept it green.
    Mutation-checked: each assertion here fails under that weakening.
    """
    fake = FakeTransport()
    fake.broadcast = _global_tempo_with_mode(0.0)
    qc = client.QuadCortex(fake)
    qc.tempo_mode()
    match = fake.last_match

    clock = pa.GlobalTempoMessage(action=pa.MessageAction.UPDATE)
    clock.metronome_status.current_beat = 2
    assert not match(clock), "the clock shape carries no parameters"
    assert not match(pa.GlobalTempoMessage()), "an empty push is not an answer"

    short = pa.GlobalTempoMessage(action=pa.MessageAction.UPDATE)
    short.params.add().param_values.add(float_value=0.4)      # only index 0
    assert not match(short), "params present, but none of them is the mode"

    no_values = _global_tempo_with_mode(0.0)
    no_values.params[1].ClearField("param_values")
    assert not match(no_values), "the mode param carries no value"

    as_int = _global_tempo_with_mode(0.0)
    as_int.params[1].ClearField("param_values")
    as_int.params[1].param_values.add(int_value=1)
    assert not match(as_int), (
        "ParamValue.value is a REAL oneof - reading .float_value off an "
        "int-valued param yields 0.0 silently, which would report PRESET")

    assert match(_global_tempo_with_mode(0.0)), "the params shape IS the answer"


def test_tempo_mode_reads_by_index_not_by_position():
    """The device keys these params, so the reader must not count them.

    Checked against the 2026-08-12 captures: every param of the pushed shape
    carries an explicit ``index``. It happens to equal position on this firmware,
    so a positional read is right by luck - and a sparse or reordered push would
    silently return a NEIGHBOURING tempo parameter. The neighbours are 0.0/1.0
    floats too (LED LIGHT, START), so the wrong answer would look valid.
    """
    # Built so the two readings DISAGREE, which is the only way this test can
    # fail when the fix is removed. Position 1 holds index 2 (LED LIGHT = 1.0);
    # index 1 - the mode - is last and holds 0.0. A positional read answers
    # GLOBAL, the correct read answers PRESET.
    sparse = pa.GlobalTempoMessage(action=pa.MessageAction.UPDATE)
    for index, value in ((0, 0.4), (2, 1.0), (1, 0.0)):
        param = sparse.params.add()
        param.index = index
        param.param_values.add(float_value=value)

    fake = FakeTransport()
    fake.broadcast = sparse
    assert client.QuadCortex(fake).tempo_mode() is TempoMode.PRESET, (
        "read position 1 (LED LIGHT) instead of the param keyed index 1")

    # And the positional fallback still applies when the device omits index.
    fake = FakeTransport()
    fake.broadcast = _global_tempo_with_mode(1.0, keyed=False)
    assert client.QuadCortex(fake).tempo_mode() is TempoMode.GLOBAL


def test_tempo_mode_refuses_a_value_that_is_not_a_mode():
    """Rounding would turn "the mapping is wrong" into a confident answer.

    Same policy as ``beats()``, which returns an unrecognised quantized value as
    a raw float rather than rounding it into an enum. 0.4 is not PRESET.
    """
    fake = FakeTransport()
    fake.broadcast = _global_tempo_with_mode(0.4)
    with pytest.raises(ValueError, match="0.4"):
        client.QuadCortex(fake).tempo_mode()


def test_tempo_mode_timeout_says_which_silence_it_was():
    """"No push arrived" and "none of them answered" are different facts.

    Conflating them is what cost this project eight releases, so the error must
    not assert device silence when it observed predicate silence.
    """
    class Silent(FakeTransport):
        def await_broadcast(self, expected_class, trigger, timeout=40.0, match=None):
            self.last_match = match
            trigger()
            for _ in range(3):
                match(pa.GlobalTempoMessage(action=pa.MessageAction.UPDATE))
            raise TimeoutError("no GlobalTempoMessage broadcast within 30.0s")

    with pytest.raises(TimeoutError, match="3 GlobalTempo push"):
        client.QuadCortex(Silent()).tempo_mode()


def test_set_tempo_mode_writes_the_device_block_and_not_the_preset():
    """Scope is the point. ADR-0007 rejected letting a tempo write land in
    whichever scope the unit happened to be in, because a guess and a success look
    identical to the caller. Measured on hardware: the preset's own parameter 1 did
    not move across this write."""
    fake = FakeTransport()
    qc = client.QuadCortex(fake)

    qc.set_tempo_mode(TempoMode.GLOBAL)
    sent = fake.sent[-1]
    assert isinstance(sent, pa.GlobalTempoMessage), "not a Grid/preset edit"
    assert sent.action == pa.MessageAction.UPDATE
    assert len(sent.params) == 1
    assert sent.params[0].index == 1
    assert sent.params[0].param_values[0].float_value == 1.0

    qc.set_tempo_mode(TempoMode.PRESET)
    assert fake.sent[-1].params[0].param_values[0].float_value == 0.0


def test_set_tempo_mode_refuses_a_value_that_is_not_a_mode():
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(ValueError):
        qc.set_tempo_mode(2)
    assert qc._t.sent == []


# -- per-beat metronome states (STEPSTATE) -------------------------------------
# Traced on hardware: from a 4/4 preset reading ENNN, one touch on beat 3 wrote
# index 12 = 0.3333, three touches on beat 4 walked index 13 through 0.3333,
# 0.6667, 1.0, and four touches on beat 1 walked index 10 from 0.6667 through 1.0,
# 0.0, 0.3333 and back to 0.6667. That wraparound is what proves the count is
# exactly four and fixes the cycle order.


def test_beats_map_to_the_stepstate_indices():
    assert client.QuadCortex.TEMPO_BEATS[1] == 10     # STEPSTATE0
    assert client.QuadCortex.TEMPO_BEATS[13] == 22    # STEPSTATE12
    assert len(client.QuadCortex.TEMPO_BEATS) == 13


def test_set_beat_writes_the_traced_wire_values():
    from pyquadcortex.protocol.enums import MetronomeBeat

    qc = client.QuadCortex(FakeTransport())
    qc._catalog = catalog.parse_model_repo(_sample_repo_payload())
    # exactly the four values the unit wrote, and the indices it wrote them to
    for beat, state, index, value in (
            (3, MetronomeBeat.MUTE, 12, 1 / 3),
            (4, MetronomeBeat.ON, 13, 1.0),
            (1, MetronomeBeat.DOWN, 10, 2 / 3),
            (2, MetronomeBeat.OFF, 11, 0.0)):
        qc.set_beat(beat, state)
        got = qc._t.sent[-1].preset.tempoProgramData[0].params[0]
        assert got.index == index
        assert got.param_values[0].float_value == pytest.approx(value)


def test_set_beat_rejects_a_beat_the_unit_cannot_store():
    from pyquadcortex.protocol.enums import MetronomeBeat

    qc = client.QuadCortex(FakeTransport())
    qc._catalog = catalog.parse_model_repo(_sample_repo_payload())
    for bad in (0, 14, -1):
        with pytest.raises(ValueError, match="beat must be 1 to 13"):
            qc.set_beat(bad, MetronomeBeat.DOWN)
    with pytest.raises(ValueError):
        qc.set_beat(1, 4)          # not one of the four states
    assert qc._t.sent == []


def test_set_beats_writes_consecutive_beats_and_leaves_the_rest():
    from pyquadcortex.protocol.enums import MetronomeBeat as B

    qc = client.QuadCortex(FakeTransport())
    qc._catalog = catalog.parse_model_repo(_sample_repo_payload())
    qc.set_beats([B.DOWN, B.OFF, B.MUTE, B.ON])
    assert len(qc._t.sent) == 4
    indices = [m.preset.tempoProgramData[0].params[0].index for m in qc._t.sent]
    assert indices == [10, 11, 12, 13]        # only the four given; 14-22 untouched
    with pytest.raises(ValueError, match="stores only 13"):
        qc.set_beats([B.OFF] * 14)


def test_beats_reads_the_states_back_as_the_enum():
    """The end state of the traced session, in the device's own words."""
    from pyquadcortex.protocol.enums import MetronomeBeat as B

    p = preset.BinaryPreset()
    tp = p.tempoProgramData.add()
    tp.hash = 25000
    values = [0.33, 0.0, 0.0, 0.6956, 0.0, 0.5, 0.1, 0.0, 0.0, 0.0]   # 0-9
    values += [0.666666687, 0.0, 0.333333343, 1.0]                    # beats 1-4
    values += [0.0] * 10                                              # beats 5-13, +23
    for value in values:
        tp.params.add().param_values.add().float_value = value
    got = client.beats(p)
    assert got[1] == B.DOWN
    assert got[2] == B.OFF
    assert got[3] == B.MUTE
    assert got[4] == B.ON
    # all 13 are always present whatever the signature - stored, simply not sounded
    assert len(got) == 13
    assert all(got[b] == B.OFF for b in range(5, 14))


def test_beats_returns_a_raw_float_it_cannot_place_rather_than_rounding():
    # Nothing has been seen to write an off-grid value; snapping one into an enum
    # would hide the day something does.
    p = preset.BinaryPreset()
    tp = p.tempoProgramData.add()
    tp.hash = 25000
    for index in range(24):
        tp.params.add().param_values.add().float_value = 0.5 if index == 10 else 0.0
    assert client.beats(p)[1] == pytest.approx(0.5)


def test_a_raw_index_still_works():
    qc = client.QuadCortex(FakeTransport())
    qc.set_param(Tempo(), 11, Encoded(0.3))
    assert qc._t.sent[-1].preset.tempoProgramData[0].params[0].index == 11


def test_tempo_params_reads_positionally_because_the_device_omits_the_index():
    # A stored preset carries 24 tempo params and sets `index` on NONE of them, so
    # position is the index. Values here are from a real read-back.
    p = preset.BinaryPreset()
    tp = p.tempoProgramData.add()
    tp.hash = 25000
    for value in (0.4, 0.0, 1.0, 0.6131, 1.0, 0.5, 0.1, 0.3333, 0.2, 0.75):
        prm = tp.params.add()
        prm.param_values.add().float_value = value
    got = client.tempo_params(p)
    assert got[0] == pytest.approx(0.4)
    assert got[4] == pytest.approx(1.0), "MUTE"
    assert got[7] == pytest.approx(0.3333), "SUBDIVISIONS"
    assert got[8] == pytest.approx(0.2), "SOUND"
    assert got[9] == pytest.approx(0.75), "ROUTING"
    assert all(not prm.HasField("index") for prm in tp.params)


def test_tempo_params_is_empty_when_the_preset_carries_none():
    assert client.tempo_params(preset.BinaryPreset()) == {}


# -- metronome option enums ----------------------------------------------------
# Read off the unit's own dropdowns, top to bottom, with the ordering confirmed by
# selecting the LAST entry of each and seeing the wire store exactly 1.0. Every
# earlier one-off pairing agrees: 1/8 notes = 1, 3/4 = 1, 4/4 = 2, BLOCK = 1,
# OUT 3/4 = 3.


def test_the_option_lists_match_the_counts_the_catalog_publishes():
    from pyquadcortex.protocol.enums import (MetronomeRouting, MetronomeSound,
                                    TempoSubdivision, TimeSignature)
    assert len(TempoSubdivision) == 4
    assert len(MetronomeRouting) == 5
    assert len(MetronomeSound) == 6
    assert len(TimeSignature) == 21


def test_the_earlier_one_off_pairings_agree_with_the_full_lists():
    from pyquadcortex.protocol.enums import (MetronomeRouting, MetronomeSound,
                                    TempoSubdivision, TimeSignature)
    assert int(TempoSubdivision.EIGHTH) == 1        # stored 0.3333 = 1/3
    assert int(TimeSignature.THREE_FOUR) == 1       # stored 0.05 = 1/20
    assert int(TimeSignature.FOUR_FOUR) == 2        # the factory default, 0.1
    assert int(MetronomeSound.BLOCK) == 1           # stored 0.2 = 1/5
    assert int(MetronomeRouting.OUT_3_4) == 3       # stored 0.75 = 3/4
    # and MULTI is first - an earlier guess had the headphones at 0
    assert int(MetronomeRouting.MULTI) == 0
    assert int(MetronomeRouting.HEADPHONES) == 1


def test_the_last_option_of_each_list_is_the_wire_value_1():
    # This is what the ordering was confirmed with on the unit.
    from pyquadcortex.protocol.enums import (MetronomeRouting, MetronomeSound,
                                    TempoSubdivision, TimeSignature)
    for enum_cls, count in ((TempoSubdivision, 4), (MetronomeRouting, 5),
                            (MetronomeSound, 6), (TimeSignature, 21)):
        last = max(int(m) for m in enum_cls)
        assert last == count - 1
        assert last / (count - 1) == pytest.approx(1.0)


def test_typed_metronome_setters_send_the_right_index_and_value():
    from pyquadcortex.protocol.enums import (MetronomeRouting, MetronomeSound,
                                    TempoSubdivision, TimeSignature)
    qc = client.QuadCortex(FakeTransport())
    qc._catalog = catalog.parse_model_repo(_sample_repo_payload())
    # No skip guard here on purpose. This test silently skipped for several releases
    # because the fixture's TempoControl stopped at index 3, so the four typed
    # setters below were never actually exercised. The fixture now carries all 23
    # parameters; if it ever regresses, this should FAIL rather than vanish.
    assert len(qc._catalog[25000].parameters) == 23
    for call, index, value in (
            (lambda: qc.set_tempo_subdivision(TempoSubdivision.EIGHTH), 7, 1 / 3),
            (lambda: qc.set_metronome_sound(MetronomeSound.BLOCK), 8, 0.2),
            (lambda: qc.set_metronome_routing(MetronomeRouting.OUT_3_4), 9, 0.75),
            (lambda: qc.set_time_signature(TimeSignature.THREE_FOUR), 6, 0.05)):
        call()
        prm = qc._t.sent[-1].preset.tempoProgramData[0].params[0]
        assert prm.index == index
        assert prm.param_values[0].float_value == pytest.approx(value)


def test_typed_setters_reject_a_value_outside_the_list():
    # A bare int is accepted but range-checked, so a wrong number cannot be stored
    # as something meaningless.
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(ValueError):
        qc.set_tempo_subdivision(9)
    with pytest.raises(ValueError):
        qc.set_metronome_routing(5)
    with pytest.raises(ValueError):
        qc.set_time_signature(21)
    assert qc._t.sent == []


def test_set_mode_cycle_accepts_a_hybrid_slot_value():
    # A merged HYBRID slot is just another value in the list: the unit reported
    # available_modes{7, 1} for Preset+Stomp merged with Scene standing alone, and
    # sending the same pair back creates it.
    qc = client.QuadCortex(FakeTransport())
    qc.set_mode_cycle([7, 1])
    assert list(qc._t.sent[-1].available_modes.modes) == [7, 1]
    qc.set_mode(7)
    assert qc._t.sent[-1].mode == 7


# -- Neural Captures -----------------------------------------------------------
# A capture BLOCK is an ordinary model (14000); which capture it plays is the string
# parameter `file_name`, holding the library file's 64-char content hash followed
# directly by its display name. Read off factory 28A and confirmed by pointing a
# host-placed block at a freshly created capture.


class _Entry:
    def __init__(self, key, name):
        self.key, self.name = key, name


def test_set_capture_writes_the_hash_and_name_as_one_string():
    qc = client.QuadCortex(EchoingTransport())
    entry = _Entry("0200eff9df18229325d1816aeb8445eca03604f2a9f95fd3732ceaed167c25c1",
                   "Kyle Pb 1")
    qc.set_capture(Block(1, 0, 14000), capture=entry)
    placed, pointed = qc._t.sent[-2:]
    assert placed.preset.chains[0].models[0].hash == 14000
    prm = pointed.preset.chains[0].models[0].params[0]
    assert prm.index == 5
    assert prm.param_values[0].string_value == entry.key + entry.name
    assert "" == entry.key[len(entry.key):], "no separator between hash and name"


def test_set_capture_needs_a_library_entry():
    qc = client.QuadCortex(EchoingTransport())
    with pytest.raises(TypeError, match="captures\\(\\)"):
        qc.set_capture(Block(1, 0), capture="Kyle Pb 1")


def test_captures_browses_the_library_not_the_catalog():
    # The catalog does NOT grow when a capture is saved, so it cannot be the source.
    listing = pa.FileMessage(action=pa.MessageAction.UPDATE)
    listing.folder.key = "local_nc_root"
    for key, name in (("aa" * 32, "Kyle Pb 1"), ("bb" * 32, "Darkglass VMT 1")):
        f = listing.folder.files.add()
        f.key = key
        f.name = name
    qc = client.QuadCortex(StateTransport(listing))
    got = qc.captures()
    assert [e.name for e in got] == ["Kyle Pb 1", "Darkglass VMT 1"]
    assert got[0].key == "aa" * 32


# -- master volume -------------------------------------------------------------

def test_set_master_volume_sends_only_the_level():
    """No companion field, and above all no ``calibrate``.

    ``calibrate`` is an action rather than a flag: it opens the unit's
    full-screen Master Volume Calibration dialog and waits for a human to sweep
    the knob. It got sent once, by accident, while probing which companion field
    a level write needed - the answer being none.
    """
    qc = client.QuadCortex(FakeTransport())
    qc.set_master_volume(Encoded(0.30))
    sent = qc._t.sent[-1]
    assert sent.action == pa.MessageAction.UPDATE
    assert abs(sent.volume - 0.30) < 1e-6
    assert not sent.HasField("calibrate")
    assert not sent.HasField("engaged")


def test_set_master_volume_rejects_the_screen_scale():
    """0..1 on the wire, 0-100 on screen, so 30 is the mistake to expect.

    Nothing is sent - the value never reaches the device. Same policy as
    ``set_hold_timing``: a field the device does not range-check is one this
    library range-checks, and this one feeds an amplifier.
    """
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(ValueError) as caught:
        qc.set_master_volume(Encoded(30))
    assert "0.30" in str(caught.value)
    assert qc._t.sent == []


@pytest.mark.parametrize("bad", [-0.1, 1.01, 100.0, 1e9])
def test_set_master_volume_rejects_anything_outside_zero_to_one(bad):
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(ValueError):
        qc.set_master_volume(Encoded(bad))
    assert qc._t.sent == []


@pytest.mark.parametrize("edge", [0.0, 1.0])
def test_set_master_volume_accepts_both_ends(edge):
    qc = client.QuadCortex(FakeTransport())
    qc.set_master_volume(Encoded(edge))
    assert qc._t.sent[-1].volume == edge


# -- listening to what the device pushes ---------------------------------------


def test_add_and_remove_listener_pass_straight_through_to_the_transport():
    # The client's whole job here is to spare the caller reaching into qc._t. The
    # contract - RX thread, no blocking, no reads - belongs to the transport and is
    # tested there.
    fake = FakeTransport()
    qc = client.QuadCortex(fake)
    seen = []

    drop = qc.add_listener(seen.append)
    assert fake.listeners == [seen.append]

    drop()
    assert fake.listeners == []
    assert qc.remove_listener(seen.append) is False, \
        "removing what is not registered reports so rather than raising"

    qc.add_listener(seen.append)
    assert qc.remove_listener(seen.append) is True
    assert fake.listeners == []


# -- picking an option by enum -------------------------------------------------
# The names live in the catalog's stepNames, which this library read for the
# first time on 2026-08-26. set_param_option used to require a preset to read
# them from; that is true of 12 dynamic lists and of nothing else.


def _option_client():
    """A client whose catalog carries a fixed list and a two-option switch."""
    from tests.test_catalog import SAMPLE_XML, make_payload

    xml = SAMPLE_XML.replace("</Models>", """
<Category id="4" name="Equalizer">
  <Model blob="lhc" id="4003" name="Low-High Cut">
    <Parameter defaultValue="0" max="8" min="0" name="HPF SLOPE" steps="9" type="rotarySwitch" units="dB/oct" stepNames="Flat,   -6, -12, -18, -24, -30, -36, -42, -48"/>
    <Parameter defaultValue="0" max="1" min="0" name="SYNC" steps="2" type="switch" stepNames="Off,On"/>
  </Model>
</Category>
""" + "</Models>")
    qc = client.QuadCortex(FakeTransport())
    qc._catalog = catalog.parse_model_repo(make_payload(xml))
    return qc


def test_an_option_enum_selects_by_its_wire_position():
    """Confirmed on hardware: wire 0.25 on this knob showed '-12 dB/o'."""
    from pyquadcortex.protocol import options

    qc = _option_client()
    qc.set_param_option(Block(0, 2, 4003), "HPF SLOPE", options.HpfSlope.MINUS_12)
    written = qc._t.sent[-1].preset.chains[0].models[0].params[0]
    assert written.index == 0
    assert written.param_values[0].float_value == pytest.approx(0.25)


def test_an_option_name_still_works_and_matches_the_devices_spelling():
    qc = _option_client()
    qc.set_param_option(Block(0, 2, 4003), "HPF SLOPE", "-12")
    written = qc._t.sent[-1].preset.chains[0].models[0].params[0]
    assert written.param_values[0].float_value == pytest.approx(0.25)


def test_a_fixed_list_needs_no_preset():
    """The whole point: 527 of the 539 lists are in the catalog."""
    qc = _option_client()
    qc.set_param_option(Block(0, 2, 4003), "HPF SLOPE", 8)      # no source=
    written = qc._t.sent[-1].preset.chains[0].models[0].params[0]
    assert written.param_values[0].float_value == pytest.approx(1.0)


def test_a_parameter_with_no_options_says_to_pass_the_preset():
    qc = _option_client()
    with pytest.raises(ValueError, match="dynamic list"):
        qc.set_param_option(Block(0, 1, 5005), "THRESHOLD", 0)


def test_a_two_option_switch_takes_a_bool():
    qc = _option_client()
    qc.set_param(Block(0, 2, 4003), "SYNC", True)
    written = qc._t.sent[-1].preset.chains[0].models[0].params[0]
    assert written.param_values[0].float_value == pytest.approx(1.0)
    qc.set_param(Block(0, 2, 4003), "SYNC", False)
    written = qc._t.sent[-1].preset.chains[0].models[0].params[0]
    assert written.param_values[0].float_value == pytest.approx(0.0)


def test_a_bool_on_a_longer_list_is_refused_rather_than_picking_the_last():
    """True is wire 1.0, which on a nine-way switch means '-48', not 'on'."""
    qc = _option_client()
    with pytest.raises(TypeError, match="offers 9 options"):
        qc.set_param(Block(0, 2, 4003), "HPF SLOPE", True)


def test_a_bool_on_a_continuous_knob_is_refused():
    """`set_param(block, "GAIN", True)` meaning "enable" is a plausible slip,
    and it would have written the top of a -60..+12 dB range."""
    qc = _option_client()
    with pytest.raises(TypeError, match="not a list at all"):
        qc.set_param(Block(0, 1, 5005), "THRESHOLD", True)


def test_a_bool_the_catalog_cannot_describe_is_refused_not_guessed():
    """An indexed write fetches no catalog, so there is nothing to check with.

    A bool we cannot check is refused rather than written as 1.0, which is what
    it used to do.
    """
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(TypeError, match="no way to check"):
        qc.set_param(Block(0, 1), 3, True)


def test_a_bool_on_a_named_tempo_parameter_is_refused_too():
    """Tempo maps its screen names straight to indexes, so the spec was never
    in hand and the guard did not fire. `TIME SIGNATURE` has 21 options."""
    qc = _lane_client()
    with pytest.raises(TypeError, match="offers 21 options"):
        qc.set_param(Tempo(), "TIME SIGNATURE", True)


def test_an_enum_from_a_different_list_is_refused():
    """An IntEnum member IS an int, so a member of the wrong list would convert.

    `DynMode3.GATE` and `SplitterType.CROSSOVER` are both 2, and both used to be
    accepted wherever an option index was wanted.
    """
    from pyquadcortex.protocol import options

    qc = _option_client()
    with pytest.raises(TypeError, match="belongs to a different list"):
        qc.set_param_option(Block(0, 2, 4003), "HPF SLOPE", options.DynMode3.GATE)


def test_a_bool_cannot_name_an_option():
    """`set_param` refuses True on a 3-option list; this refused nothing and
    picked index 1."""
    qc = _option_client()
    with pytest.raises(TypeError, match="cannot name an option"):
        qc.set_param_option(Block(0, 2, 4003), "HPF SLOPE", True)


def test_a_dynamic_lists_catalog_snapshot_is_not_used():
    """The catalog's copy is the wrong LENGTH, so it picks the right name at the
    wrong position.

    A Doubler's TRIGGER publishes 45 stepNames while the real list is 19 to 25
    depending on the preset. Option 1 of 45 is wire 0.0227, which against a real
    19-entry list reads back as option 0 - silently the wrong choice.
    """
    from tests.test_catalog import SAMPLE_XML, make_payload

    names = ",".join(f"o{i}" for i in range(45))
    xml = SAMPLE_XML.replace("</Models>", f'''
<Category id="18" name="Pitch">
  <Model blob="dbl" id="18000" name="Doubler">
    <Parameter defaultValue="0" max="44" min="0" name="TRIGGER" steps="45"
     type="comboBox" dynamic="true" stepNames="{names}"/>
  </Model>
</Category>
''' + "</Models>")
    qc = client.QuadCortex(FakeTransport())
    qc._catalog = catalog.parse_model_repo(make_payload(xml))
    with pytest.raises(ValueError, match="builds its options from the PRESET"):
        qc.set_param_option(Block(0, 1, 18000), "TRIGGER", "o1")


def test_the_recorder_cannot_be_placed_on_the_grid():
    """Placing it crashed the unit and needed a reboot, 2026-08-26.

    That was recorded in units.DO_NOT_PROBE and nothing read it, so set_block
    would happily do it again. A note nothing enforces is not a guard.
    """
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(ValueError, match="must not be placed on the grid"):
        qc.set_block(Block(1, 1, 20000))
    assert not qc._t.sent, "nothing should reach the wire"


# -- real units through the public entry point ---------------------------------
# Every real-unit test above uses a plain linear parameter with a value inside
# range. The taper, the floor and the refusals were covered only on `Parameter`
# and on `target.normalize`, one and two layers below where a caller sits.


def test_set_param_real_applies_the_taper():
    """A cab LEVEL, through `qc.set_param`, not through the converter."""
    qc = _scale_client()
    qc.set_param(Block(0, 5, 12000), "MIC 1 LEVEL", Real(0.0))
    written = qc._t.sent[-1].preset.chains[0].models[0].params[0]
    assert written.param_values[0].float_value == pytest.approx(0.5, abs=1e-4)


def test_set_param_real_refuses_below_the_floor():
    """The blocking bug, reached the way a caller reaches it.

    Without the floor this converts to wire 0.0005 and mutes the microphone.
    """
    qc = _scale_client()
    with pytest.raises(ValueError, match="does not exist there"):
        qc.set_param(Block(0, 5, 12000), "MIC 1 LEVEL", Real(-30.0))


def test_set_param_real_refuses_a_value_off_the_top():
    qc = _scale_client()
    with pytest.raises(ValueError, match="does not exist there"):
        qc.set_param(LaneOutput(0), "VOLUME", Real(13.0))


def test_wrapping_a_bool_in_real_is_a_deliberate_one_point_oh():
    """The old `real=True` footgun is gone, because the wrapper closes it.

    `Real(True)` collapses to `Real(1.0)` when it is constructed, so the caller
    has said in as many words that they mean the value 1.0 on this knob's own
    scale. That is not the accident the guard was written for.

    What IS still an accident is a BARE True on something that is not a
    two-option switch, and that is refused a few lines below.
    """
    qc = _scale_client()
    qc.set_param(LaneOutput(0), "VOLUME", Real(True))
    written = qc._t.sent[-1].preset.chains[0].output_control[0].params[0]
    # 1.0 dB on a -40..+12 knob.
    assert written.param_values[0].float_value == pytest.approx(0.788, abs=0.01)


def test_a_bare_bool_on_a_continuous_knob_is_still_refused():
    qc = _scale_client()
    with pytest.raises(TypeError, match="not a list at all"):
        qc.set_param(LaneOutput(0), "VOLUME", True)


def test_the_converter_still_refuses_a_bool_for_its_own_callers():
    """`to_normalized` is public and reachable without set_param."""
    from pyquadcortex.protocol import catalog as catalog_module

    spec = catalog_module.Parameter(index=0, name="X", minimum=0.0, maximum=10.0,
                                    default=0.0, units="", type="float")
    with pytest.raises(TypeError, match="bool IS an int"):
        spec.to_normalized(True)


def test_set_param_real_refuses_the_one_unmeasurable_parameter():
    """ControlNotDrivable, with the evidence, not a bare ValueError."""
    from pyquadcortex.protocol.errors import ControlNotDrivable

    qc = _lane_client()
    with pytest.raises(ControlNotDrivable) as excinfo:
        qc.set_param(Block(0, 1, 20000), "OUT LEVEL", Real(-3.0))
    assert "nobody has measured" in excinfo.value.evidence
    assert "normalized 0..1" in excinfo.value.workaround


def _scale_client():
    """A client whose catalog carries a cab and a lane output, with real bounds."""
    from tests.test_catalog import SAMPLE_XML, make_payload

    xml = SAMPLE_XML.replace("</Models>", """
<Category id="12" name="Cabsim Guitar (M)">
  <Model blob="cab" id="12000" name="Default Cabsim" internal="true">
    <Parameter defaultValue="0" max="1" min="0" name="bypass" type="switch"/>
    <Parameter defaultValue="0" max="999" min="0" name="ir" type="string"/>
    <Parameter defaultValue="0.5" max="MAX_CABSIM_DB" min="MIN_CABSIM_DB" name="MIC 1 LEVEL" type="float" units="dB" skew="4.9594844" min_string="OFF"/>
  </Model>
</Category>
<Category id="23" name="Lane Output">
  <Model blob="loc" id="23000" name="LaneOutputControl" internal="true">
    <Parameter defaultValue="0.769" max="MAX_MIXER_DB" min="MIN_MIXER_DB" name="VOLUME" type="float" units="dB" min_string="OFF"/>
  </Model>
</Category>
""" + "</Models>")
    qc = client.QuadCortex(FakeTransport())
    qc._catalog = catalog.parse_model_repo(make_payload(xml))
    return qc


# -- set_block's verification, and the false negative it used to produce -------


def _preset_holding(row, column, model_id):
    p = preset.BinaryPreset()
    for r in range(4):
        chain = p.chains.add()
        chain.row = r
        for c in range(8):
            m = chain.models.add()
            m.column = c
            if r == row and c == column:
                m.hash = model_id
    return p


def test_a_missing_echo_is_not_a_refusal_if_the_block_actually_landed(monkeypatch):
    """The false negative, pinned.

    `set_block` waited for a Grid echo and raised when none came. It raised
    twice in one session on blocks that had landed perfectly well - so a missing
    echo is not evidence of a refusal, and the unit has to be asked.
    """
    qc = client.QuadCortex(FakeTransport())

    def no_echo(*args, **kwargs):
        raise TimeoutError("no echo")

    monkeypatch.setattr(qc._t, "await_broadcast", no_echo, raising=False)
    monkeypatch.setattr(qc, "read_current_preset",
                        lambda *a, **k: _preset_holding(1, 1, 7040))
    assert qc.set_block(Block(1, 1, 7040)) is None


def test_a_missing_echo_AND_an_empty_cell_names_both_known_causes(monkeypatch):
    """DSP capacity was called "the known cause" for a long time. It is one of
    two: a port conflict puts a modal on the unit that the host never sees."""
    qc = client.QuadCortex(FakeTransport())

    def no_echo(*args, **kwargs):
        raise TimeoutError("no echo")

    monkeypatch.setattr(qc._t, "await_broadcast", no_echo, raising=False)
    monkeypatch.setattr(qc, "read_current_preset",
                        lambda *a, **k: _preset_holding(3, 7, 9999))
    with pytest.raises(client.BlockRefused) as excinfo:
        qc.set_block(Block(1, 1, 7040))
    message = str(excinfo.value)
    assert "DSP capacity" in message
    assert "PORT CONFLICT" in message


def test_a_read_that_fails_does_not_swallow_the_refusal(monkeypatch):
    """If the unit cannot be asked, the refusal stands - it does not become a
    silent success."""
    qc = client.QuadCortex(FakeTransport())

    def no_echo(*args, **kwargs):
        raise TimeoutError("no echo")

    def broken(*args, **kwargs):
        raise RuntimeError("link died")

    monkeypatch.setattr(qc._t, "await_broadcast", no_echo, raising=False)
    monkeypatch.setattr(qc, "read_current_preset", broken)
    with pytest.raises(client.BlockRefused):
        qc.set_block(Block(1, 1, 7040))


# -- the two number lines, through the public entry point ----------------------


def test_real_and_encoded_zero_are_opposite_ends_of_the_knob():
    """The pair that makes the type mandatory rather than a convenience."""
    qc = _scale_client()
    qc.set_param(LaneOutput(0), "VOLUME", Real(0.0))
    unity = qc._t.sent[-1].preset.chains[0].output_control[0].params[0]
    qc.set_param(LaneOutput(0), "VOLUME", Encoded(0.0))
    off = qc._t.sent[-1].preset.chains[0].output_control[0].params[0]
    assert unity.param_values[0].float_value == pytest.approx(0.76923, abs=1e-4)
    assert off.param_values[0].float_value == pytest.approx(0.0)


def test_encoded_needs_no_catalog_at_all():
    """What keeps an index-addressed write free of a round trip."""
    qc = client.QuadCortex(FakeTransport())          # no catalog loaded
    qc.set_param(Block(0, 1), 3, Encoded(0.25))
    written = qc._t.sent[-1].preset.chains[0].models[0].params[0]
    assert written.param_values[0].float_value == pytest.approx(0.25)


def test_the_wrong_unit_is_refused_before_anything_reaches_the_wire():
    qc = _scale_client()
    with pytest.raises(TypeError, match="dB"):
        qc.set_param(LaneOutput(0), "VOLUME", Hertz(217))
    assert not qc._t.sent


def test_a_bare_number_is_refused_and_the_message_rewrites_the_call():
    qc = _scale_client()
    with pytest.raises(TypeError) as excinfo:
        qc.set_param(LaneOutput(0), "VOLUME", -3.1)
    message = str(excinfo.value)
    assert "Real(-3.1)" in message
    assert "Encoded(-3.1)" in message
    assert "two number lines" in message


def test_a_string_on_a_number_parameter_is_refused():
    """Collapsing text= into the positional removed the declaration of intent.

    A value that arrives as a string from argv or JSON would otherwise take the
    string path onto a dB knob and be sent, which the device accepts silently.
    """
    qc = _option_client()
    with pytest.raises(TypeError, match="not a string one"):
        qc.set_param(Block(0, 1, 5005), "THRESHOLD", "0.5")


def test_an_encoded_value_outside_the_devices_scale_is_refused():
    """0..1 is the one bound true of all 3,809 parameters, so it is the only
    one checkable without asking the device anything."""
    qc = client.QuadCortex(FakeTransport())
    for bad in (5.0, -1.0, float("nan")):
        with pytest.raises(ValueError, match="0.0 to 1.0"):
            qc.set_param(Block(0, 1), 3, Encoded(bad))
    assert not qc._t.sent


def test_a_number_on_a_string_parameter_is_refused():
    """The string guard was one-directional.

    A string on a number's parameter was refused; a number on a STRING
    parameter was converted and sent. On a cab's `ir selector`, whose catalog
    range is 0..999, `Real(0.5)` became wire 0.0005 - a write that looks like it
    worked and means nothing.
    """
    from tests.test_targets import _diverging_cab_catalog

    for param in (1, "ir"):                       # by index and by name
        qc = client.QuadCortex(FakeTransport())
        qc._catalog = _diverging_cab_catalog()()
        with pytest.raises(TypeError, match="is a string parameter"):
            qc.set_param(Block(0, 5, 12001), param, Real(0.5))
        assert not qc._t.sent


def test_an_indexed_encoded_write_still_costs_no_catalog():
    """The deliberate gap in the check above, and why it is deliberate.

    `Encoded` addressed by INDEX is not checked, because checking would mean
    fetching a catalog - and an index-addressed write staying free of a round
    trip is that branch's whole reason to exist. Naming the parameter fetches
    one anyway, so that path IS checked.
    """
    from tests.test_targets import _diverging_cab_catalog

    qc = client.QuadCortex(FakeTransport())       # no catalog at all
    qc.set_param(Block(0, 5), 3, Encoded(0.5))
    assert qc._t.sent

    qc = client.QuadCortex(FakeTransport())
    qc._catalog = _diverging_cab_catalog()()
    with pytest.raises(TypeError, match="is a string parameter"):
        qc.set_param(Block(0, 5, 12001), "ir", Encoded(0.5))


def test_a_unit_check_reads_the_spec_the_conversion_uses_on_a_cab(monkeypatch):
    """The blocker from the #37 triage, through the public entry point.

    A cab's own catalog entry and the shared layout describe different
    parameters at the same index. The check read one and the conversion used the
    other, so on 157 of 174 cab models Db, Hertz and Percent all wrote the same
    wire value.
    """
    from tests.test_targets import _diverging_cab_catalog
    from pyquadcortex.protocol.values import Hertz

    qc = client.QuadCortex(FakeTransport())
    qc._catalog = _diverging_cab_catalog()()
    qc.set_param(Block(0, 5, 12001), 2, Db(-3.0))         # the layout is dB
    assert qc._t.sent
    with pytest.raises(TypeError, match="dB"):
        qc.set_param(Block(0, 5, 12001), 2, Hertz(-3.0))


# -- typed values reach the SETTINGS writes too --------------------------------
#
# ADR-0016 made `set_param` refuse a bare number. Everything below writes a
# value to the unit as well, and until now took a bare wire float - so "a value
# says which scale it is on" was true of one method and not of the library.


def test_an_input_gain_takes_db_and_converts_it():
    """-12..+60 dB, measured. 0 dB is exactly 1/6, which is the check."""
    qc = client.QuadCortex(FakeTransport())
    qc.set_input_level(Input.INPUT_1, Db(0.0))
    assert qc._t.sent[-1].settings.in_port[0].level == pytest.approx(1 / 6)
    qc.set_input_level(Input.INPUT_1, Db(24.0))
    assert qc._t.sent[-1].settings.in_port[0].level == pytest.approx(0.5)


def test_an_input_gain_outside_the_span_is_refused_not_clamped():
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(ValueError):
        qc.set_input_level(Input.INPUT_1, Db(90.0))
    assert qc._t.sent == []


def test_a_setting_with_no_measured_scale_refuses_a_real_value():
    """ADR-0007's shape rather than a silent guess.

    Nothing in the catalog describes an output port, and nobody has read one's
    screen against its wire value - so there is no span. Converting dB against
    an invented one is exactly what ADR-0015 exists to stop, so it refuses and
    the refusal says what would settle it.
    """
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(ControlNotDrivable) as caught:
        qc.set_output_level(9, Db(-3.0))
    assert "measure" in caught.value.workaround
    assert qc._t.sent == []
    qc.set_output_level(9, Encoded(0.5))          # the honest way to say it
    assert qc._t.sent[-1].settings.out_port[0].level == pytest.approx(0.5)


@pytest.mark.parametrize("call", [
    lambda qc: qc.set_input_level(Input.INPUT_1, 0.5),
    lambda qc: qc.set_output_level(9, 0.5),
    lambda qc: qc.set_usb_port(level=0.5),
    lambda qc: qc.set_master_volume(0.3),
    lambda qc: qc.set_global_eq_band(1, 0.6),
    lambda qc: qc.set_global_eq(1, gain=0.75),
    lambda qc: qc.set_global_eq_output(level=0.5),
    lambda qc: qc.set_hold_timing(800),
    lambda qc: qc.set_tuner_reference(2.0),
    lambda qc: qc.set_expression(Block(0, 2), 4, minimum=0.1),
])
def test_a_bare_number_is_refused_by_every_settings_write(call):
    """The rule is the library's now, not one method's."""
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(TypeError, match="which scale it is on"):
        call(qc)
    assert qc._t.sent == []


def test_a_global_eq_gain_takes_db_on_the_manuals_span():
    """-12..+12 dB. Both documented points, which is all the evidence there is."""
    qc = client.QuadCortex(FakeTransport())
    qc.set_global_eq(1, gain=Db(0.0))
    assert qc._t.sent[-1].parameters[0].value == pytest.approx(0.5)
    qc.set_global_eq(1, gain=Db(6.0))
    assert qc._t.sent[-1].parameters[0].value == pytest.approx(0.75)


def test_a_global_eq_frequency_has_no_scale_and_says_so():
    """The band's GAIN is known and its FREQUENCY is not, in the same call."""
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(ControlNotDrivable):
        qc.set_global_eq(1, frequency=Hertz(400.0))
    assert qc._t.sent == []


def test_a_setting_with_no_wire_scale_refuses_encoded():
    """The hold threshold has ONE number line and it is the screen's.

    The wire carries an index derived from the milliseconds, so `Encoded(0.5)`
    is not a quieter way of saying anything - it is meaningless, and saying so
    beats accepting it.
    """
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(TypeError, match="no 0..1 device scale"):
        qc.set_hold_timing(Encoded(0.5))
    with pytest.raises(TypeError, match="no 0..1 device scale"):
        qc.set_tuner_reference(Encoded(0.5))
    assert qc._t.sent == []


def test_the_single_scale_settings_take_their_own_unit():
    qc = client.QuadCortex(FakeTransport())
    qc.set_hold_timing(Milliseconds(800))
    assert qc._t.sent[-1].hold_timing == 3
    qc.set_tuner_reference(Hertz(2.0))
    assert qc._t.sent[-1].frequency == pytest.approx(2.0)


def test_the_wrong_unit_on_a_single_scale_setting_is_refused():
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(TypeError, match="ms"):
        qc.set_hold_timing(Hertz(800))
    with pytest.raises(TypeError, match="Hz"):
        qc.set_tuner_reference(Milliseconds(2.0))
    assert qc._t.sent == []


def test_an_expression_sweep_end_converts_through_the_target_parameter():
    """The sweep runs between POSITIONS of the parameter it is assigned to, so
    its scale is that parameter's and `Db` means the same thing it means in a
    `set_param` to the same knob."""
    qc = _scale_client()
    qc.set_expression(LaneOutput(0), "VOLUME", pedal=1,
                      minimum=Encoded(0.0), maximum=Db(3.2))
    prm = qc._t.sent[-1].preset.chains[0].output_control[0].params[0]
    assert prm.expression_min == pytest.approx(0.0)
    assert prm.expression_max == pytest.approx(
        units_module.db_to_lane_level(3.2))


def test_an_expression_sweep_end_checks_its_unit_like_a_write_does():
    qc = _scale_client()
    with pytest.raises(TypeError, match="dB"):
        qc.set_expression(LaneOutput(0), "VOLUME", pedal=1,
                          maximum=Hertz(3.2))
    assert not qc._t.sent


def test_an_unassigned_expression_sweep_still_defaults_to_the_whole_range():
    """The defaults are the library's own, so they did not become a break."""
    qc = _scale_client()
    qc.set_expression(LaneOutput(0), "VOLUME", pedal=1)
    prm = qc._t.sent[-1].preset.chains[0].output_control[0].params[0]
    assert (prm.expression_min, prm.expression_max) == (0.0, 1.0)


def test_the_usb_level_takes_encoded_and_refuses_a_real():
    """The happy path had no test at all - the only USB-level assertion checked
    the field was ABSENT."""
    qc = client.QuadCortex(FakeTransport())
    qc.set_usb_port(level=Encoded(0.4))
    assert qc._t.sent[-1].settings.usb_port.level == pytest.approx(0.4)

    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(ControlNotDrivable):
        qc.set_usb_port(level=Db(-6.0))
    assert qc._t.sent == []


def test_the_global_eq_output_level_refuses_a_real_too():
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(ControlNotDrivable):
        qc.set_global_eq_output(level=Db(-6.0))
    assert qc._t.sent == []


def test_a_global_eq_gain_outside_its_span_is_refused():
    """The input gain had this test and the Global EQ gain did not, which
    matters more here: its span is the weaker of the two."""
    qc = client.QuadCortex(FakeTransport())
    for bad in (Db(-20.0), Db(20.0)):
        with pytest.raises(ValueError):
            qc.set_global_eq(1, gain=bad)
    assert qc._t.sent == []


def test_the_master_volume_screen_number_is_caught_on_the_first_attempt():
    """ADR-0017's motivating bug, and a regression this PR introduced once.

    `set_master_volume(30)` means "30 on screen" and writes full output. The
    typed refusal replaced the helpful message with one offering `Encoded(30)`
    - self-contradictory next to "0.0 to 1.0", and pointing at the dangerous
    number. The caller has to be told the answer, not told twice.
    """
    qc = client.QuadCortex(FakeTransport())
    for given in (30, Encoded(30)):
        with pytest.raises(ValueError) as caught:
            qc.set_master_volume(given)
        assert "0.30" in str(caught.value)
    assert qc._t.sent == []


def test_a_bare_number_never_suggests_an_encoded_the_wire_cannot_hold():
    """The general form of the bug above, wherever a value is out of range."""
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(TypeError) as caught:
        qc.set_global_eq_band(1, 30.0)
    assert "Encoded(30.0)" not in str(caught.value)
    assert "0.0 to 1.0" in str(caught.value)


def test_a_sweep_end_suggests_real_rather_than_a_unit_it_cannot_check():
    """1,780 parameters are unitless, so `Db` would earn a second refusal."""
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(TypeError) as caught:
        qc.set_expression(Block(0, 2), 4, minimum=0.1)
    assert "Real(0.1)" in str(caught.value)


def test_the_bypass_switch_delay_takes_milliseconds():
    qc = client.QuadCortex(FakeTransport())
    qc.set_expression_bypass(Block(0, 2), delay_ms=Milliseconds(250))
    info = qc._t.sent[-1].preset.chains[0].models[0].expression_bypass_info[0]
    assert info.delay_ms == 250

    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(TypeError, match="no 0..1 device scale"):
        qc.set_expression_bypass(Block(0, 2), delay_ms=Encoded(0.5))
