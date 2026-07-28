"""Tests for the high-level QuadCortex client (pyquadcortex.client).

The client builds protobuf messages and hands them to a transport-like object
exposing ``send(message)`` and ``request(message, timeout=...)``. It never
touches hidapi or framing directly. These tests inject a FakeTransport so the
client can be exercised without a device.
"""

import itertools

import pytest

from pyquadcortex import catalog, client
from pyquadcortex.enums import (Footswitch, Input, Instrument, MidiSource,
                                Output, SceneBypassBehavior, Setlist)
from pyquadcortex.proto import ProductionAutomation_pb2 as pa
from pyquadcortex.proto import Preset_pb2 as preset


class FakeTransport:
    """Records outbound messages and returns canned responses by class name."""

    def __init__(self, canned=None):
        self.sent = []
        self.canned = canned or {}
        self.broadcast = None
        self.last_match = None  # the match predicate read_preset passed, if any
        self._ids = itertools.count(1)

    def send(self, msg):
        self.sent.append(msg)

    def request(self, msg, timeout=5.0):
        self.sent.append(msg)
        return self.canned.get(type(msg).__name__)

    def next_request_id(self):
        return next(self._ids)

    def await_broadcast(self, expected_class, trigger, timeout=40.0, match=None):
        self.last_match = match
        trigger()
        return self.broadcast


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
    qc.set_param(row=0, column=1, param_index=1, value=0.4553)
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
    qc.set_param(row=0, column=1, param_index=1, value=0.5)
    param = qc._t.sent[-1].preset.chains[0].models[0].params[0]
    assert len(param.param_values) == 1, "no padding entries may be emitted"
    assert param.param_values[0].HasField("float_value")
    assert abs(param.param_values[0].float_value - 0.5) < 1e-6


def test_set_bypass_sends_row_column_keyed_grid_update():
    # CONFIRMED capture shape: Grid{UPDATE, preset{bypass{row, colBypass{column,
    # sceneBypass{bypass}}}}}.
    qc = client.QuadCortex(FakeTransport())
    qc.set_bypass(row=0, column=4, bypassed=True)
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
    qc.set_bypass(row=0, column=4, bypassed=True, scene=1)
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
    from pyquadcortex.enums import Scene

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
    qc.set_block(row=0, column=2, model=5005)
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
    qc.set_block(row=1, column=3, model=model)
    assert qc._t.sent[-1].preset.chains[0].models[0].hash == 4005


def test_remove_block_sends_grid_delete():
    # CONFIRMED: deleting a block on the unit broadcast
    # Grid{action: DELETE, chains{row, models{column, hash:0}}}. Sending the
    # same shape from the host removes the block; an UPDATE with hash=0 does
    # NOT (the firmware ignores a zero hash on an update).
    qc = client.QuadCortex(FakeTransport())
    qc.remove_block(row=0, column=4)
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
    qc.set_param(row=0, column=1, param="THRESHOLD", value=0.25, model=5005)
    param = qc._t.sent[-1].preset.chains[0].models[0].params[0]
    assert param.index == 0
    assert abs(param.param_values[0].float_value - 0.25) < 1e-6


def test_set_param_by_name_needs_a_known_model():
    qc = client.QuadCortex(FakeTransport())
    qc._catalog = catalog.parse_model_repo(_sample_repo_payload())
    with pytest.raises(KeyError):
        qc.set_param(row=0, column=1, param="NOPE", value=0.5, model=5005)


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
    qc.set_param(row=0, column=1, param="THRESHOLD", real=-24.0, model=comp)
    param = qc._t.sent[-1].preset.chains[0].models[0].params[0]
    assert param.param_values[0].float_value == pytest.approx(0.5)


def test_set_param_real_units_require_param_and_model():
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(TypeError):
        qc.set_param(row=0, column=1, real=-20)


# -- the per-scene write ceiling ----------------------------------------------


def test_set_param_writes_one_scene_by_promoting_then_switching():
    """Per-scene parameter writes, confirmed on hardware, take three messages.

    The device honours ``param_values[0]`` against whichever scene is ACTIVE, and
    only on a parameter whose ``scene_mode`` is set. Crucially it accepts EITHER the
    flag OR a value in one message, never both - sending them together silently
    ignores the flag, which is why this looked impossible.
    """
    from pyquadcortex.enums import Scene

    fake = FakeTransport()
    qc = client.QuadCortex(fake)
    qc.set_param(row=2, column=5, param_index=0, value=0.8, scene=Scene.D)

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
    qc.set_param(row=0, column=1, param_index=5, value=0.25)
    assert [type(m).__name__ for m in fake.sent] == ["GridMessage"]
    p = fake.sent[0].preset.chains[0].models[0].params[0]
    assert len(p.param_values) == 1
    assert p.param_values[0].HasField("float_value")


def test_set_param_can_skip_promotion():
    from pyquadcortex.enums import Scene

    fake = FakeTransport()
    qc = client.QuadCortex(fake)
    qc.set_param(row=0, column=1, param_index=5, value=0.5, scene=Scene.B, promote=False)
    assert [type(m).__name__ for m in fake.sent] == ["SceneMessage", "GridMessage"]


def test_set_param_scene_mode_sends_the_flag_alone():
    fake = FakeTransport()
    qc = client.QuadCortex(fake)
    qc.set_param_scene_mode(row=2, column=5, param_index=1, enabled=True)
    prm = fake.sent[-1].preset.chains[0].models[0].params[0]
    assert prm.scene_mode is True
    assert not prm.param_values, "a value alongside the flag makes the device drop it"


def test_set_lane_output_supports_per_scene_values():
    from pyquadcortex.enums import Scene

    fake = FakeTransport()
    qc = client.QuadCortex(fake)
    qc.set_lane_output(row=0, param=0, value=0.0, scene=Scene.D)
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
    from pyquadcortex.enums import Scene

    fake = FakeTransport()
    qc = client.QuadCortex(fake)

    qc.set_bypass(row=0, column=2, bypassed=True, scene=Scene.D)
    assert isinstance(fake.sent[-2], pa.SceneMessage)
    assert fake.sent[-2].selected_scene == 3
    cb = fake.sent[-1].preset.bypass[0].colBypass[0]
    assert len(cb.sceneBypass) == 1, "never pad; the device only reads index 0"
    assert cb.sceneBypass[0].bypass is True

    # With no scene named, act on whatever scene is active: no switch is sent.
    fake.sent.clear()
    qc.set_bypass(row=0, column=2, bypassed=False)
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
    qc.set_lane_output(row=2, param=1, value=0.5)         # index, no catalog needed
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
    from pyquadcortex.client import position_to_slot

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
    qc.set_mixer_param(row=0, param=0, value=0.769)
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
    qc.set_splitter_param(row=0, param=3, value=0.25)      # 3 = LEVEL TO A
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
    from pyquadcortex.enums import Scene

    fake = FakeTransport()
    qc = client.QuadCortex(fake)
    qc.set_splitter_param(row=0, param=3, value=0.1, scene=Scene.B)
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
    qc.set_tempo_param(2, value=0.0)
    sent = fake.sent[-1]
    assert isinstance(sent, pa.GridMessage)
    assert not sent.preset.chains, "not a chain edit"
    tp = sent.preset.tempoProgramData[0]
    assert tp.hash == qc.TEMPO_CONTROL == 25000
    assert tp.params[0].index == 2
    assert tp.params[0].param_values[0].float_value == 0.0


def test_mixer_param_per_scene_uses_promote_switch_write():
    from pyquadcortex.enums import Scene

    fake = FakeTransport()
    qc = client.QuadCortex(fake)
    qc.set_mixer_param(row=0, param=0, value=0.0, scene=Scene.C)
    assert [type(m).__name__ for m in fake.sent] == [
        "GridMessage", "SceneMessage", "GridMessage"]
    promote = fake.sent[0].preset.chains[0].mixer[0].params[0]
    assert promote.scene_mode is True
    assert not promote.param_values, "flag alone, or the device drops it"


def test_save_current_preset_sets_the_default_scene_by_switching_first():
    from pyquadcortex.enums import Scene

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


def test_splitter_and_mixer_writes_refuse_an_odd_row():
    qc = client.QuadCortex(FakeTransport())
    for row in (1, 3):
        with pytest.raises(ValueError, match="row 0 or"):
            qc.set_splitter_param(row=row, param=3, value=0.5)
        with pytest.raises(ValueError, match="row 0 or"):
            qc.set_mixer_param(row=row, param=0, value=0.5)
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
    qc.set_block(row=1, column=0, model=5005)
    chain = qc._t.sent[-1].preset.chains[0]
    assert chain.row == 1
    assert chain.models[0].column == 0
    assert chain.models[0].hash == 5005


def test_set_block_raises_when_the_device_never_echoes_the_cell():
    qc = client.QuadCortex(EchoingTransport(refuse={21005}))
    with pytest.raises(client.BlockRefused, match="no DSP capacity"):
        qc.set_block(row=1, column=4, model=21005)


def test_set_block_can_skip_verification_for_fire_and_forget_placement():
    qc = client.QuadCortex(EchoingTransport(refuse={21005}))
    qc.set_block(row=1, column=4, model=21005, verify=False)   # must not raise
    assert qc._t.sent[-1].preset.chains[0].models[0].hash == 21005


def test_set_block_echo_match_ignores_an_echo_for_a_different_cell():
    qc = client.QuadCortex(FakeTransport())
    captured = {}

    def await_broadcast(expected_class, trigger, timeout=40.0, match=None):
        trigger()
        captured["match"] = match
        return pa.GridMessage()

    qc._t.await_broadcast = await_broadcast
    qc.set_block(row=2, column=3, model=5005)
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
    qc.set_input_gate(row=0, param=1, value=1.0)          # BYPASS
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
    qc.set_input_gate(row=0, param=0, value=0.9, scene=2)
    flag, switch, write = qc._t.sent[-3:]
    assert flag.preset.chains[0].input_control[0].params[0].scene_mode is True
    assert not flag.preset.chains[0].input_control[0].params[0].param_values
    assert isinstance(switch, pa.SceneMessage)
    assert write.preset.chains[0].input_control[0].params[0].param_values


def test_set_input_gate_needs_a_value():
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(TypeError):
        qc.set_input_gate(row=0, param=1)


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


def test_set_mixer_param_refuses_real_units_for_a_placeholder_range():
    # MIXER LEVEL is published as 0..1 "dB". real= used to convert against that
    # range and produce a number meaning something else entirely; it now raises.
    qc = client.QuadCortex(FakeTransport())
    qc._catalog = catalog.parse_model_repo(_sample_repo_payload())
    with pytest.raises(ValueError, match="placeholder range"):
        qc.set_mixer_param(row=0, param="MIXER LEVEL", real=0.0)
    assert qc._t.sent == []
    qc.set_mixer_param(row=0, param="MIXER LEVEL", value=client.UNITY_LEVEL)
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
    qc.set_stomp_assignment(row=2, column=3, footswitch=Footswitch.D)
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


# -- expression pedal assignment ----------------------------------------------


def test_set_expression_is_row_column_keyed_with_a_range():
    qc = client.QuadCortex(FakeTransport())
    qc.set_expression(row=0, column=2, param=4, pedal=2, minimum=0.1, maximum=0.9)
    chain = qc._t.sent[-1].preset.chains[0]
    assert chain.row == 0
    prm = chain.models[0].params[0]
    assert chain.models[0].column == 2
    assert prm.index == 4
    assert prm.expression == 2
    assert prm.expression_min == pytest.approx(0.1)
    assert prm.expression_max == pytest.approx(0.9)


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
    qc.set_param(row=0, column=5, param_index=1, text="NG_212 DG Neo_Condenser U47")
    val = qc._t.sent[-1].preset.chains[0].models[0].params[0].param_values[0]
    assert val.string_value == "NG_212 DG Neo_Condenser U47"
    assert not val.HasField("float_value")


def test_set_param_rejects_text_and_real_together():
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(TypeError):
        qc.set_param(row=0, column=0, param_index=0, text="x", real=1.0)


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
    assert client.param_options(p, row=2, column=0, param_index=4) == [
        "Off", "Follow Input", "Input 1"]
    assert client.param_options(p, row=2, column=1, param_index=4) == []


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
    qc.set_input_level(5, 0.25)
    ports = qc._t.sent[-1].settings.in_port
    assert len(ports) == 1
    assert ports[0].input_port_id == 5
    assert ports[0].level == pytest.approx(0.25)
    assert not qc._t.sent[-1].settings.out_port
    assert len(qc._t.sent) == 1

    qc.set_output_level(9, 0.5)
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
    qc.move_block(2, 1, 2, 7)
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
    qc.set_expression_bypass(row=0, column=2, pedal=1, mode=1, invert=True,
                             delay_ms=250, latch_emulation=True)
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
    qc.set_input_port(2, level=0.4, impedance=0.875, input_type=0.5, ground_lift=0.0)
    assert len(qc._t.sent) == 4, "four fields, four messages"
    for msg in qc._t.sent:
        port = msg.settings.in_port[0]
        assert port.input_port_id == 2
        set_fields = [f.name for f, _ in port.ListFields() if f.name != "input_port_id"]
        assert len(set_fields) == 1, f"{set_fields} shared one message"


def test_set_input_level_still_works_and_delegates():
    qc = client.QuadCortex(FakeTransport())
    qc.set_input_level(5, 0.25)
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


def test_favorites_waits_for_a_push_with_items():
    push = pa.RecentsFavoritesMessage(action=pa.MessageAction.UPDATE)
    it = push.items.add()
    it.name = "Brit 2203"
    it.folder_key = "/opt/neuraldsp/Factory Library"
    it.folder_name = "Factory Library"
    qc = client.QuadCortex(StateTransport(push))
    got = qc.favorites()
    assert got.items[0].name == "Brit 2203"
    assert qc._t.matches[-1](pa.RecentsFavoritesMessage()) is False


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
    qc.set_global_eq_band(1, 0.6)
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
    qc.set_param_option(row=1, column=0, param=6, option="Input 2", source=p)
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
    qc.set_param_option(row=1, column=0, param="THRESHOLD", option="On", source=p)
    written = qc._t.sent[-1].preset.chains[0].models[0].params[0]
    assert written.index == 0
    assert written.param_values[0].float_value == pytest.approx(1.0)


def test_set_param_option_needs_the_block_to_be_in_the_source_preset():
    qc = client.QuadCortex(FakeTransport())
    with pytest.raises(ValueError, match="no block at row"):
        qc.set_param_option(row=3, column=7, param="SOURCE", option="Off",
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
    qc.set_tuner_reference(2.0)
    assert qc._t.sent[-1].frequency == pytest.approx(2.0)
    qc.set_tuner_reference(0.0)
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


def test_master_volume_is_readable_and_has_no_setter():
    # The wire is 0..1 mapping to the 0-100 on screen: 47 read back 0.471074373.
    # A write is accepted and changes nothing, so no setter is offered.
    push = pa.MasterVolumeMessage(action=pa.MessageAction.UPDATE, volume=0.471074373)
    qc = client.QuadCortex(StateTransport(push))
    assert round(qc.master_volume().volume * 100) == 47
    assert qc._t.matches[-1](pa.MasterVolumeMessage()) is False
    assert not hasattr(qc, "set_master_volume")


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
    from pyquadcortex.enums import LooperState
    assert [int(s) for s in LooperState] == [1, 2, 4, 5, 6]
    assert int(LooperState.OVERDUBBING) == 6
    assert 3 not in [int(s) for s in LooperState], "3 was never seen; do not invent it"


def test_expression_bypass_mode_numbering_is_not_the_manual_order():
    # Each set deliberately on the unit with a scene change fencing them apart:
    # Heel-Toe stored 2, Switch 1, Stop 0. An earlier release had this reversed.
    from pyquadcortex.enums import ExpressionBypassMode as M
    assert (int(M.STOP), int(M.SWITCH), int(M.HEEL_TOE)) == (0, 1, 2)


def test_set_expression_bypass_accepts_the_enum():
    qc = client.QuadCortex(FakeTransport())
    from pyquadcortex.enums import ExpressionBypassMode as M
    qc.set_expression_bypass(row=0, column=1, pedal=1, mode=M.HEEL_TOE)
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
    qc.set_global_eq(band=1, gain=0.75)
    assert qc._t.sent[-1].parameters[0].parameter_index == 0
    qc.set_global_eq(band=3, gain=0.75)
    assert qc._t.sent[-1].parameters[0].parameter_index == 10
    qc.set_global_eq(band=5, q=0.2)
    assert qc._t.sent[-1].parameters[0].parameter_index == 22
    qc.set_global_eq(band=2, frequency=0.4)
    assert qc._t.sent[-1].parameters[0].parameter_index == 6


def test_set_global_eq_sends_the_filter_type_as_an_option_value():
    from pyquadcortex.enums import GlobalEQFilter
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
    qc.set_global_eq(band=2, gain=0.6, q=0.1)
    indices = [m.parameters[0].parameter_index for m in qc._t.sent]
    assert indices == [5, 7], "gain and Q only, no frequency or type write"


def test_set_global_eq_validates_the_band_and_needs_a_control():
    qc = client.QuadCortex(FakeTransport())
    for bad in (0, 6, -1):
        with pytest.raises(ValueError, match="band must be"):
            qc.set_global_eq(band=bad, gain=0.5)
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
    qc.set_global_eq_output(level=0.5, out34=False)
    indices = [m.parameters[0].parameter_index for m in qc._t.sent[-2:]]
    assert indices == [25, 27]
    with pytest.raises(TypeError):
        qc.set_global_eq_output()


# -- tempo parameter names -----------------------------------------------------
# Mapped by using each control in the unit's Tempo menu in a named order. Two names
# disagree with the catalog (index 4 is MUTE on screen, START in the catalog; index 7
# is Subdivisions on screen, NOTELENGTH in the catalog) and 8 and 9 are absent from
# the catalog entirely.


def test_set_tempo_param_resolves_the_screen_names():
    qc = client.QuadCortex(FakeTransport())
    for name, index in (("TEMPO", 0), ("LED LIGHT", 2), ("VOLUME", 3), ("MUTE", 4),
                        ("PAN", 5), ("TIME SIGNATURE", 6), ("SUBDIVISIONS", 7),
                        ("SOUND", 8), ("ROUTING", 9)):
        qc.set_tempo_param(name, value=0.5)
        got = qc._t.sent[-1].preset.tempoProgramData[0].params[0]
        assert got.index == index, f"{name} should resolve to {index}"


def test_tempo_param_names_are_case_and_space_tolerant():
    qc = client.QuadCortex(FakeTransport())
    qc.set_tempo_param("routing", value=0.75)
    assert qc._t.sent[-1].preset.tempoProgramData[0].params[0].index == 9
    qc.set_tempo_param(" Sound ", value=0.2)
    assert qc._t.sent[-1].preset.tempoProgramData[0].params[0].index == 8


def test_real_units_refused_for_a_tempo_param_the_catalog_does_not_describe():
    # The real unit's catalog describes 0-22; a parameter beyond whatever it
    # describes cannot be converted, so real= must refuse rather than guess.
    qc = client.QuadCortex(FakeTransport())
    qc._catalog = catalog.parse_model_repo(_sample_repo_payload())
    with pytest.raises(ValueError, match="does not describe tempo parameter"):
        qc.set_tempo_param("ROUTING", real=3)
    assert qc._t.sent == []


def test_set_tempo_option_range_checks_against_the_catalogs_step_count():
    # The catalog's `steps` is the option count: ROUTING has 5, so 5 is out of range
    # and 3 maps to 0.75 - which is what the unit stored for OUT 3/4.
    qc = client.QuadCortex(FakeTransport())
    qc._catalog = catalog.parse_model_repo(_sample_repo_payload())
    routing = qc._catalog[25000].parameters[9] if len(qc._catalog[25000].parameters) > 9 else None
    if routing is None:
        pytest.skip("the sample catalog does not go that far")
    qc.set_tempo_option("ROUTING", 3)
    assert qc._t.sent[-1].preset.tempoProgramData[0].params[0] \
        .param_values[0].float_value == pytest.approx(0.75)


def test_a_raw_index_still_works():
    qc = client.QuadCortex(FakeTransport())
    qc.set_tempo_param(11, value=0.3)
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
    from pyquadcortex.enums import (MetronomeRouting, MetronomeSound,
                                    TempoSubdivision, TimeSignature)
    assert len(TempoSubdivision) == 4
    assert len(MetronomeRouting) == 5
    assert len(MetronomeSound) == 6
    assert len(TimeSignature) == 21


def test_the_earlier_one_off_pairings_agree_with_the_full_lists():
    from pyquadcortex.enums import (MetronomeRouting, MetronomeSound,
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
    from pyquadcortex.enums import (MetronomeRouting, MetronomeSound,
                                    TempoSubdivision, TimeSignature)
    for enum_cls, count in ((TempoSubdivision, 4), (MetronomeRouting, 5),
                            (MetronomeSound, 6), (TimeSignature, 21)):
        last = max(int(m) for m in enum_cls)
        assert last == count - 1
        assert last / (count - 1) == pytest.approx(1.0)


def test_typed_metronome_setters_send_the_right_index_and_value():
    from pyquadcortex.enums import (MetronomeRouting, MetronomeSound,
                                    TempoSubdivision, TimeSignature)
    qc = client.QuadCortex(FakeTransport())
    qc._catalog = catalog.parse_model_repo(_sample_repo_payload())
    if len(qc._catalog[25000].parameters) <= 9:
        pytest.skip("the sample catalog stops before the metronome lists")
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
