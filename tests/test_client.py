"""Tests for the high-level QuadCortex client (pyquadcortex.client).

The client builds protobuf messages and hands them to a transport-like object
exposing ``send(message)`` and ``request(message, timeout=...)``. It never
touches hidapi or framing directly. These tests inject a FakeTransport so the
client can be exercised without a device.
"""

import itertools

from pyquadcortex import client
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
    # CONFIRMED (device, 2026-07-23): a host recall echoes its request_id on the
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


# -- input rerouting (Phase B) ------------------------------------------------


def test_input_port_constants_match_schema_enum():
    # Chain.in_portid uses GainCalInputPortParameter.InputPortId verbatim -
    # CONFIRMED exhaustively on-device 2026-07-23 (ids 0-14; 15 rejected).
    InP = pa.GainCalInputPortParameter.InputPortId
    assert client.INPUT_1 == InP.INPUT_1
    assert client.INPUT_2 == InP.INPUT_2
    assert client.INPUT_1_2 == InP.INPUT_1_2
    assert client.RETURN_1 == InP.RETURN_1
    assert client.RETURN_2 == InP.RETURN_2
    assert client.RETURN_1_2 == InP.RETURN_1_2
    assert client.PREV_ROW == InP.PREV_ROW
    assert client.USB_IN_5 == InP.USB_IN_5
    assert client.USB_IN_8 == InP.USB_IN_8
    assert client.USB_IN_5_6 == InP.USB_IN_5_6
    assert client.USB_IN_7_8 == InP.USB_IN_7_8
    assert client.SIDECHAIN_BUFFER == InP.SIDECHAIN_BUFFER
    # Owner-confirmed anchors (the rig): Input 1, Input 2, Return 1.
    assert (client.INPUT_1, client.INPUT_2, client.RETURN_1) == (1, 2, 4)


def test_output_port_constants_match_schema_enum():
    # Chain.out_portid uses GainCalOutputPortParameter.OutputPortId verbatim -
    # anchored by owner's 28F (out 4="Output 1", 1="Output 1/2") and spot-
    # confirmed 2026-07-23 (2="Output 3/4", 3="Send 1/2", 10="USB 5").
    OutP = pa.GainCalOutputPortParameter.OutputPortId
    assert client.OUT_XLR_1_2 == OutP.XLR_1_2      # "Output 1/2" (owner)
    assert client.OUT_XLR_1 == OutP.XLR_1          # "Output 1"   (owner)
    assert client.OUT_3_4 == OutP.OUTPUT_3_4       # "Output 3/4" (owner)
    assert client.OUT_SEND_1_2 == OutP.SEND_1_2    # "Send 1/2"   (owner)
    assert client.OUT_USB_5 == OutP.USB_OUT_5      # "USB 5"      (owner)
    assert client.OUT_USB_7_8 == OutP.USB_OUT_7_8
    assert client.OUT_MULTIPLE == OutP.MULTIPLE_OUTS  # factory Cali's output


def test_instrument_tag_constants():
    # ProductData.instrument tag, confirmed against the factory library:
    # 1=guitar (block 0-15), 2=bass (16-23, 191-231), 4=vocal (AutoWah, Vocal 58,
    # Vocal Synth). Values are powers of two (3 unused) - likely bit flags.
    assert client.INSTRUMENT_GUITAR == 1
    assert client.INSTRUMENT_BASS == 2
    assert client.INSTRUMENT_VOCAL == 4


def test_input_chain_rows_returns_rows_on_from_port():
    # Grid row == chain index when chains carry no explicit row (CONFIRMED via
    # the 28A read-back: chain[0]=Input 2 on row 1, chain[2]=Input 1 on row 3).
    p = preset.BinaryPreset()
    p.chains.add().in_portid = client.INPUT_1  # index 0
    p.chains.add().in_portid = 0               # index 1 - internally fed
    p.chains.add().in_portid = client.INPUT_1  # index 2
    assert client.input_chain_rows(p, client.INPUT_1) == [0, 2]


def test_input_chain_rows_honors_explicit_row():
    p = preset.BinaryPreset()
    c = p.chains.add()
    c.in_portid = client.INPUT_1
    c.row = 3
    assert client.input_chain_rows(p, client.INPUT_1) == [3]


def test_set_chain_input_sends_row_keyed_sparse_grid_update():
    # CONFIRMED (device 2026-07-23): only a Grid UPDATE carrying a chain with an
    # explicit `row` re-points that row's input; a full preset whose chains lack
    # `row` is NOT applied. So set_chain_input sends exactly one chain {row,
    # in_portid} - the minimal proven shape.
    qc = client.QuadCortex(FakeTransport())
    qc.set_chain_input(row=2, in_portid=client.RETURN_1)
    sent = qc._t.sent[-1]
    assert isinstance(sent, pa.GridMessage)
    assert sent.action == pa.MessageAction.UPDATE
    assert len(sent.preset.chains) == 1
    ch = sent.preset.chains[0]
    assert ch.row == 2
    assert ch.in_portid == client.RETURN_1


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
    p.chains.add().in_portid = client.INPUT_1   # row 0
    p.chains.add().in_portid = 0                # row 1 internal
    p.chains.add().in_portid = client.INPUT_1   # row 2
    qc = client.QuadCortex(FakeTransport())
    rows = qc.reroute_grid_input(p, client.RETURN_1)
    assert rows == [0, 2]
    grids = [m for m in qc._t.sent if isinstance(m, pa.GridMessage)]
    assert len(grids) == 2
    moved = {(g.preset.chains[0].row, g.preset.chains[0].in_portid) for g in grids}
    assert moved == {(0, client.RETURN_1), (2, client.RETURN_1)}


def test_reroute_grid_input_raises_when_no_matching_row():
    p = preset.BinaryPreset()
    p.chains.add().in_portid = client.RETURN_1
    qc = client.QuadCortex(FakeTransport())
    try:
        qc.reroute_grid_input(p, client.INPUT_2)
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
    reply = qc.hello(settle=0)
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
