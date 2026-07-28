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
