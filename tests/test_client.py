"""Tests for the high-level QuadCortex client (pyquadcortex.client).

The client builds protobuf messages and hands them to a transport-like object
exposing ``send(message)`` and ``request(message, timeout=...)``. It never
touches hidapi or framing directly. These tests inject a FakeTransport so the
client can be exercised without a device.
"""

import itertools

import pytest

from pyquadcortex import catalog, client
from pyquadcortex.enums import Input, Instrument, Output, Setlist
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


def test_set_param_targets_the_requested_scene_slot():
    qc = client.QuadCortex(FakeTransport())
    qc.set_param(row=0, column=1, param_index=1, value=0.5, scene=2)
    param = qc._t.sent[-1].preset.chains[0].models[0].params[0]
    assert len(param.param_values) == 3          # extended to index 2
    assert abs(param.param_values[2].float_value - 0.5) < 1e-6


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


def test_set_bypass_targets_the_requested_scene_slot():
    qc = client.QuadCortex(FakeTransport())
    qc.set_bypass(row=0, column=4, bypassed=True, scene=1)
    cb = qc._t.sent[-1].preset.bypass[0].colBypass[0]
    assert len(cb.sceneBypass) == 2
    assert cb.sceneBypass[1].bypass is True


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
