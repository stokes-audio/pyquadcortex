"""The model's write-through cache: `docs/domain-model.md` sections 9 and 10.

Everything here runs offline. The unit's side of the link is a
:class:`LoopbackTransport` that mirrors the two ``Transport`` guarantees the
cache is built on (ADR-0009): a listener sees every decoded message, and it sees
a reply BEFORE the thread that asked for it wakes up. Above it sits the REAL
``QuadCortex``, so ``client.version()`` and ``client.preset_dirty()`` are the
methods that run on hardware rather than stubs of them.

``tests/test_state_rx.py`` covers the same cache on a real ``Transport`` and a
real RX thread, which is the only place the "never reads from the RX thread"
rule can actually be exercised.
"""
import ast
import collections
import logging
import pathlib
import threading
import time

import pytest

from pyquadcortex.device import entries, events, state
from waiting import stays_quiet, wait_for
from pyquadcortex.device.watch import WatchOutcome
from pyquadcortex.protocol import client as protocol_client
from pyquadcortex.protocol.proto import Preset_pb2 as preset_pb
from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa


class LoopbackTransport:
    """Canned replies, listeners notified first, every read counted.

    ``request`` answers from :attr:`replies`, keyed by the request's message
    class name, and hands the reply to every listener BEFORE returning it -
    which is the ordering the real transport guarantees and the ordering the
    cache's read path depends on.
    """

    def __init__(self):
        self.replies = {}
        self.broadcasts = {}
        self.sent = []
        self.reads = collections.Counter()
        self.listeners = []
        self._ids = iter(range(1, 1_000_000))

    # -- the Transport surface the client and the model use -------------------

    def add_listener(self, listener):
        self.listeners.append(listener)
        return lambda: self.remove_listener(listener)

    def remove_listener(self, listener):
        try:
            self.listeners.remove(listener)
        except ValueError:
            return False
        return True

    def send(self, message):
        self.sent.append(message)

    def next_request_id(self):
        return next(self._ids)

    def await_broadcast(self, message_class, trigger, timeout=5.0, match=None):
        """The reads that wait for a PUSH rather than for a reply.

        `read_current_preset` and `active_scene` both work this way: they send a
        READ and wait for the broadcast that echoes its request id. Replies live
        in :attr:`broadcasts` rather than :attr:`replies`, keyed the same way,
        and are called with the message that triggered them so a canned answer
        can echo the id the real one would.

        The reply is held to the caller's own ``match`` before it is handed
        back. Without that, a test could set an answer the real code would have
        rejected and never know - which is the failure mode that makes a double
        worse than no test.
        """
        name = message_class.__name__
        self.reads[name] += 1
        trigger()
        if name in self.broadcasts:
            reply = self.broadcasts[name]
            if callable(reply):
                reply = reply(self.sent[-1] if self.sent else None)
        elif name in self.replies:
            # A READ whose answer is a same-type message with no id to echo -
            # `version()` since 2026-09-03 - waits by type and predicate, but
            # the unit's answer is the same canned reply either way.
            reply = self.replies[name]
            if callable(reply):
                reply = reply()
        else:                                     # pragma: no cover - a test bug
            raise AssertionError(
                f"the test asked the unit for a {name} broadcast and set no "
                f"reply for it")
        if match is not None and not match(reply):
            raise AssertionError(
                f"the canned {name} does not satisfy the match the real read "
                f"uses, so this test is proving something the library would "
                f"have rejected")
        self.push(reply)
        return reply

    def request(self, message, timeout=5.0):
        name = type(message).__name__
        self.sent.append(message)
        self.reads[name] += 1
        try:
            reply = self.replies[name]
        except KeyError:                          # pragma: no cover - a test bug
            raise AssertionError(
                f"the test asked the unit for a {name} and set no reply for it")
        if callable(reply):
            reply = reply()
        self.push(reply)          # every listener sees it first...
        return reply              # ...and only then does the caller wake

    # -- the unit's side ------------------------------------------------------

    def push(self, message):
        """Deliver ``message`` to every listener, as the RX thread would."""
        for listener in list(self.listeners):
            listener(message)


def version_reply(**fields):
    """A ``VersionMessage`` the unit could have sent, carrying only ``fields``."""
    return pa.VersionMessage(action=pa.MessageAction.UPDATE, **fields)


def full_version_reply():
    return version_reply(app_fw_version="d14e", device_serial_number="QCS0000001")


def dirty_push(is_dirty):
    return pa.PresetDirtyMessage(action=pa.MessageAction.UPDATE, is_dirty=is_dirty)


def tempo_pair():
    """One beat of the metronome stream: `GlobalTempo` arrives in pairs.

    The metronome clock always runs, so the unit pushes a pair per beat on
    every connection whether or not anybody is listening - measured 1.5 s apart
    at 40 bpm (``docs/domain-model.md`` section 9, smaller decision 7).
    """
    beat = pa.GlobalTempoMessage(action=pa.MessageAction.UPDATE)
    param = beat.params.add()
    param.index = 0
    param.param_values.add().float_value = 0.4         # 120 bpm on the 40-240 scale
    status = pa.GlobalTempoMessage(action=pa.MessageAction.UPDATE)
    status.metronome_status.is_enabled = 1
    status.metronome_status.current_beat = 1
    return beat, status


def _carrying_something(message, field):
    """``message`` with ``field`` actually set, so presence reports it.

    A submessage is only present once something in it is touched, and an
    unset one would make the copy check below pass by applying nothing.
    """
    message.ClearField(field)
    getattr(message, field).SetInParent()
    return message


def with_an_unknown_field(message, number=999, value=7):
    """``message`` re-parsed with a field number the recovered schema lacks.

    Not hypothetical: `protocol/ProductionAutomation.proto` is recovered rather
    than published (ADR-0010 says so in as many words), so a field the unit
    really sends and our bindings have never heard of is the ordinary case, not
    a future-firmware worry.
    """
    tag = number << 3          # wire type 0, a varint
    encoded = bytearray()
    while tag > 0x7F:
        encoded.append((tag & 0x7F) | 0x80)
        tag >>= 7
    encoded.append(tag)
    grown = type(message)()
    grown.ParseFromString(message.SerializeToString() + bytes(encoded) + bytes([value]))
    return grown


PRESET_FIXTURE = (pathlib.Path(__file__).parent / "fixtures" / "presets"
                  / "structural_preset.bin")


def a_preset():
    """The structural fixture, read off a real unit, as a `BinaryPreset`."""
    payload = preset_pb.BinaryPreset()
    payload.ParseFromString(PRESET_FIXTURE.read_bytes())
    return payload


def recall_push(triggering=None, preset=None):
    """A `RecallPreset` carrying a whole preset, echoing a read's request id.

    Every one of these carries `reason` as well - a host recall and a plain READ
    both report OTHER - which is why the preset entry keeps it.
    """
    push = pa.RecallPresetMessage(action=pa.MessageAction.UPDATE,
                                  reason=pa.RecallPresetReason.OTHER)
    push.preset.CopyFrom(a_preset() if preset is None else preset)
    if triggering is not None and triggering.HasField("request_id"):
        push.request_id = triggering.request_id
    return push


def scene_push(triggering=None, scene=0):
    push = pa.SceneMessage(action=pa.MessageAction.UPDATE, selected_scene=scene)
    if triggering is not None and triggering.HasField("request_id"):
        push.request_id = triggering.request_id
    return push


def grid_push(action=pa.MessageAction.UPDATE):
    """What one edit on the touchscreen produces about forty of."""
    return pa.GridMessage(action=action)


def position_push(triggering=None, position=9):
    """The loaded slot as an answer to a READ, echoing the request id."""
    push = recalled_elsewhere(position)
    if triggering is not None and triggering.HasField("request_id"):
        push.request_id = triggering.request_id
    return push


def recalled_elsewhere(position=9):
    """The unit announcing which preset is loaded.

    The exact shape the connect burst delivers, measured 2026-08-15: action,
    folder_key, is_factory and position, with no request_id."""
    return pa.SetlistPositionMessage(action=pa.MessageAction.UPDATE,
                                     folder_key="/media/p4/Presets/My Presets",
                                     position=position, is_factory=False)


@pytest.fixture
def link():
    """A cache listening on a loopback link, over the real protocol client."""
    transport = LoopbackTransport()
    transport.replies["VersionMessage"] = full_version_reply
    transport.replies["PresetDirtyMessage"] = lambda: dirty_push(False)
    transport.broadcasts["RecallPresetMessage"] = recall_push
    transport.broadcasts["SceneMessage"] = scene_push
    transport.broadcasts["SetlistPositionMessage"] = position_push
    qc = protocol_client.QuadCortex(transport)
    cache = state.DeviceState()
    cache.listen_on(transport)
    cache.bind(qc)
    try:
        yield transport, cache
    finally:
        cache.close()


# -- pushes are data, not invalidation triggers -------------------------------


def test_a_push_the_handshake_delivered_answers_the_first_read_for_free(link):
    """The connect burst's whole value - section 9, smaller decision 1."""
    transport, cache = link
    transport.push(dirty_push(True))

    assert cache.value("dirty", "is_dirty") is True
    assert transport.reads["PresetDirtyMessage"] == 0, (
        "the unit had already said so; asking again is the round trip the "
        "cache exists to avoid")


def test_a_partial_push_merges_into_the_cache_rather_than_replacing_it(link):
    """Section 9, smaller decision 2: an absent field means "not mentioned"."""
    transport, cache = link
    assert cache.value("identity", "device_serial_number") == "QCS0000001"

    transport.push(version_reply(app_fw_version="d15a"))

    assert cache.value("identity", "app_fw_version") == "d15a"
    assert cache.value("identity", "device_serial_number") == "QCS0000001", (
        "the push did not mention the serial, which is not the same as the "
        "unit reporting it empty")
    assert transport.reads["VersionMessage"] == 1


def test_a_field_the_wire_gives_no_presence_is_carried_by_every_push(link):
    """`is_dirty` cannot be absent: proto3 gives it no presence, so False and
    unset are the same bytes. The protocol layer's recorded evidence is that
    absent IS false (``QuadCortex.preset_dirty``), so this is the one field the
    cache reads without a presence check - declared, not assumed."""
    transport, cache = link
    transport.push(dirty_push(True))
    assert cache.value("dirty", "is_dirty") is True

    transport.push(dirty_push(False))

    assert cache.value("dirty", "is_dirty") is False, (
        "a clean save announces itself with a message that sets no field at "
        "all; reading that as 'not mentioned' leaves the model stuck dirty")
    assert transport.reads["PresetDirtyMessage"] == 0


# -- a field we do not keep, checked per field --------------------------------


def test_a_push_naming_a_field_the_model_does_not_keep_forces_a_reread(link):
    """The failure this rule exists to catch: half a message applied."""
    transport, cache = link
    assert cache.value("identity", "app_fw_version") == "d14e"
    assert transport.reads["VersionMessage"] == 1

    transport.push(version_reply(app_fw_version="d15a",
                                 linux_kernel_version="5.10.0"))

    assert cache.needs_read("identity") is True
    cache.value("identity", "app_fw_version")
    assert transport.reads["VersionMessage"] == 2, (
        "the cache kept answering from a copy it had already been told was "
        "incomplete")


def test_a_push_naming_a_field_the_schema_does_not_know_forces_a_reread(link):
    """A recovered schema's own failure mode. The field is real on the unit and
    absent from our bindings, so it decodes into nothing at all - the quietest
    possible way to drop half a message."""
    transport, cache = link
    assert cache.value("identity", "app_fw_version") == "d14e"

    transport.push(with_an_unknown_field(version_reply(app_fw_version="d15a")))

    assert cache.needs_read("identity") is True


def test_the_half_of_the_push_we_do_understand_is_still_applied(link):
    """Marking for re-read and applying what we read are not alternatives.

    If the kept half were dropped, the answer between the push and the next
    read would be the OLD value - confidently wrong, just for a shorter while.
    """
    transport, cache = link
    assert cache.value("identity", "app_fw_version") == "d14e"

    transport.push(version_reply(app_fw_version="d15a",
                                 linux_kernel_version="5.10.0"))

    assert cache.cached("identity")["app_fw_version"] == "d15a"


def test_it_marks_only_the_part_of_the_cache_the_push_named(link):
    """"Exactly that part" - a Version surprise says nothing about the preset."""
    transport, cache = link
    transport.push(dirty_push(True))
    cache.value("identity", "app_fw_version")

    transport.push(version_reply(linux_kernel_version="5.10.0"))

    assert cache.needs_read("identity") is True
    assert cache.needs_read("dirty") is False
    assert cache.value("dirty", "is_dirty") is True
    assert transport.reads["PresetDirtyMessage"] == 0


def test_the_forced_reread_names_the_field_that_forced_it(caplog, link):
    """Section 10's standard for a log line: a bug with a name and a location.
    The event name and the field are what issue #16's counters read."""
    transport, cache = link
    cache.value("identity", "app_fw_version")

    with caplog.at_level(logging.INFO, logger="pyquadcortex.device.state"):
        transport.push(version_reply(uboot_version="2019.04"))

    assert any("push.forced_reread" in r.message and "uboot_version" in r.message
               for r in caplog.records), caplog.text


def test_one_reread_is_enough_and_the_cache_is_trusted_again(link):
    """Section 9: "we discard our copy and read a fresh one. Slower, but right."

    Once, not on every access. The read's own answer carries the same fields we
    do not keep, so an entry that re-armed the mark from its own reply would
    never cache anything again.
    """
    transport, cache = link
    transport.replies["VersionMessage"] = lambda: version_reply(
        app_fw_version="d14e", device_serial_number="QCS0000001",
        uboot_version="2019.04")
    transport.push(version_reply(uboot_version="2019.04"))

    cache.value("identity", "app_fw_version")
    cache.value("identity", "app_fw_version")
    cache.value("identity", "device_serial_number")

    assert transport.reads["VersionMessage"] == 1
    assert cache.needs_read("identity") is False


def test_a_push_that_lands_during_a_proactive_read_is_not_lost(link):
    """The window the read path has to close.

    A read replaces our copy with an answer the unit composed before the push
    arrived. Clearing the mark unconditionally would drop that push with
    nothing left to recover it from.
    """
    transport, cache = link

    def reply_but_a_push_first():
        transport.push(version_reply(app_fw_version="d15a"))
        return full_version_reply()

    transport.replies["VersionMessage"] = reply_but_a_push_first
    cache.value("identity", "app_fw_version")

    assert cache.needs_read("identity") is True


def test_a_grid_push_that_lands_during_a_preset_read_is_not_lost_either(link):
    """The same window, for the push that says nothing but means everything.

    A `Grid` carries no field the preset entry keeps, so it counts as an arrival
    on the strength of voiding the copy alone. It is the case a guard written as
    "count it if it applied something" would drop, and dropping it loses an edit
    made on the touchscreen while the model was reading.
    """
    transport, cache = link

    def preset_but_a_grid_first(triggering=None):
        transport.push(grid_push())
        return recall_push(triggering)

    transport.broadcasts["RecallPresetMessage"] = preset_but_a_grid_first
    cache.value("preset", "preset")

    assert cache.needs_read("preset") is True


def test_the_unit_asking_a_question_back_is_not_a_push_that_landed(link):
    """A `Version` READ is answered by TWO messages, and still costs one read.

    Measured on the unit 2026-08-27 (d14e), ten host reads out of ten: a
    `Version{READ}` is answered by a `Version{UPDATE}` carrying the unit's
    fifteen fields and then, 0.5-0.8 ms later, by a `Version{READ}` of the
    unit's own - the protocol is symmetric, and that one is the unit asking US
    for Cortex Control's version. It carries `action` and nothing else.

    So it says nothing: not a field the entry keeps, not a field it does not
    keep. It cannot have made our copy stale, and counting it as a message that
    landed while we were reading marks the entry for a re-read the unit never
    asked for. Reported rather than measured: that mark reached the hardware
    suite as a failing run roughly one time in three.

    Two deliberate departures from the wire, and neither changes what is being
    measured. The answer here carries an unkept field, as the real fifteen-field
    reply does, so the mark is set by the answer and cleared by the count rather
    than by the whole-entry `answered` path - the real sequence. And the
    follow-up lands inside the read window EVERY time, where on the unit that is
    a race the question wins about one read in six; the certain case is the one
    worth pinning. Order is the one thing the loopback cannot reproduce - it
    pushes the returned reply last - and order is the one thing the read path
    does not look at, since it compares a count taken before the read with a
    count taken after it.
    """
    transport, cache = link

    def answers_and_then_asks_back():
        transport.push(pa.VersionMessage(action=pa.MessageAction.READ))
        return version_reply(app_fw_version="d14e",
                             device_serial_number="QCS0000001",
                             uboot_version="2019.04")

    transport.replies["VersionMessage"] = answers_and_then_asks_back
    cache.mark_for_reread("identity", "this test wants a cold read")

    assert cache.value("identity", "app_fw_version") == "d14e"
    assert cache.value("identity", "device_serial_number") == "QCS0000001"

    assert transport.reads["VersionMessage"] == 1, (
        "the unit asking a question back was counted as a push that landed "
        "during the read, so the second field went to the unit again")
    assert cache.needs_read("identity") is False


def test_a_recall_landing_during_a_read_of_the_dirty_flag_is_not_lost(link):
    """The same window, reached by a path the arrival count cannot see.

    The test above counts messages for the entry being read. A recall marks the
    `dirty` entry through `LOADED.resets`, and a `SetlistPosition` is not a
    message the `dirty` entry is fed by, so it never reaches that count. The
    unit sends NO `PresetDirty` after a recall (measured; see `_A_RECALL_RESETS`),
    so nothing else corrects it: a read that dropped this mark would go on
    reporting edits the recall discarded, which is the one thing `resets` exists
    to prevent. The window is the 2-11 ms `preset_dirty()` read.
    """
    transport, cache = link
    cache.apply_push(recalled_elsewhere(position=9))

    def reply_but_a_recall_first():
        transport.push(recalled_elsewhere(position=17))
        return dirty_push(True)

    transport.replies["PresetDirtyMessage"] = reply_but_a_recall_first
    assert cache.value("dirty", "is_dirty") is True

    assert cache.needs_read("dirty") is True, (
        "the recall cleared the flag on the unit and said nothing about it, so "
        "the model is now reporting edits that no longer exist")


def test_a_watchdog_timeout_during_a_read_of_that_entry_is_not_lost(link):
    """And by a path on another thread entirely.

    The watchdog gives up on a write from its own thread, which is not the RX
    thread and is counted nowhere. Its mark means "a write of ours may or may
    not have landed"; the answer to the read in flight was composed at an
    unknown moment, possibly before the write reached the unit, so it cannot
    stand in for the re-read the timeout asked for.

    The push before the read is only there to make the read happen at all: a
    write leaves the entry warm on its own value, so without a mark there would
    be nothing to read. It names `uboot_version`, which the entry does not keep,
    and carries nothing the watcher is waiting for.

    The patience is the one number here that is a trade rather than a fact. The
    watchdog has to fire while the read is in flight, so the read must start
    first, and what has to happen in between is two adjacent statements against
    a stub - microseconds. 0.5 s buys about five orders of magnitude of margin
    for half a second of suite time; the assert below is what makes the
    remaining risk a loud failure rather than a quiet pass.
    """
    transport, cache = link
    watch = cache.write_through("identity", {"app_fw_version": "MINE"},
                                send=lambda: None, patience=0.5)
    transport.push(version_reply(uboot_version="2019.04"))
    assert watch.outcome is None, "the watchdog fired before the read started"

    def reply_after_the_watchdog_gave_up():
        assert watch.settled(timeout=5.0)
        assert watch.outcome is WatchOutcome.TIMED_OUT
        return full_version_reply()

    transport.replies["VersionMessage"] = reply_after_the_watchdog_gave_up
    cache.value("identity", "app_fw_version")

    assert cache.needs_read("identity") is True, (
        "the write was never confirmed and the read that discarded the mark "
        "may predate it"
    )


#: Qualified function name -> how many times it assigns `needs_read` by hand,
#: and why each of those assignments is safe where it is. Everything else has to
#: go through `mark_for_reread`, which COUNTS the mark, or a read already in
#: flight will discard it - see the two tests above.
#:
#: A COUNT rather than a name, because two of these live in the same function
#: for different reasons, and a third assignment added beside them would
#: otherwise inherit an argument that does not cover it.
MAY_SET_THE_MARK_BY_HAND = {
    # A fresh slot starts trusted. Nothing has been read yet, so there is
    # nothing to discard.
    "_Slot.__init__": 1,
    # The counted setter itself. `mark_for_reread` is its only caller.
    "_Slot.marked": 1,
    # Two, and they are not the same argument.
    #
    # The per-field mark, which is safe uncounted because the message that
    # forced it is itself one of the arrivals a read weighs - together with the
    # read's own answer, which is the SECOND, and both are needed to clear the
    # `extra > 1` bar. `test_a_marking_push_during_any_entrys_read_survives_it`
    # is what holds that second arrival, per entry, because PR #34 made "a
    # message of a tracked type that said nothing" a real category and a read
    # answered by one would leave the bar at one arrival. It must STAY
    # uncounted: counting it would re-arm the mark from the read's own reply and
    # the entry would cache nothing again.
    #
    # And the `answered` clear, whose argument is ADR-0012's - a push carrying
    # every field an entry keeps is what a read returns, so there is nothing
    # left to ask about. That one is not covered by the arrival count at all,
    # and it is the assignment that could wipe a COUNTED mark with the read's
    # own reply. `marked_since` is what puts it back.
    "DeviceState._apply_one": 2,
    # The read path, which is what decides a mark's fate from the two counters.
    "DeviceState.value": 1,
}


def _assigns_the_mark(node) -> bool:
    """Whether ``node`` is an assignment whose target is some `.needs_read`."""
    if isinstance(node, (ast.AugAssign, ast.AnnAssign)):
        targets = [node.target]
    elif isinstance(node, ast.Assign):
        targets = node.targets
    else:
        return False
    return any(isinstance(found, ast.Attribute) and found.attr == "needs_read"
               for target in targets for found in ast.walk(target))


def _mark_setters(tree) -> dict:
    """Qualified function name -> how many times it assigns `.needs_read`.

    Tracks the INNERMOST enclosing function, so a nested `def` is reported
    under its own name rather than hiding inside an allowlisted one, and
    anything outside a function at all is reported under ``<module>``.
    """
    counts = collections.Counter()

    def scan(node, prefix, inside):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                scan(child, f"{prefix}{child.name}.", inside)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scan(child, prefix, f"{prefix}{child.name}")
            else:
                if _assigns_the_mark(child):
                    counts[inside] += 1
                scan(child, prefix, inside)

    scan(tree, "", "<module>")
    return dict(counts)


def test_nothing_else_sets_the_mark_without_counting_it():
    """The two tests above are one window each; this is the rule behind them.

    A new path that set `needs_read` by hand would be dropped by a read in
    flight - silently, and only inside a window of a few milliseconds, which is
    how both cases above went unnoticed. Said out loud here rather than left to
    the next author to infer from a comment.

    Where this stops seeing: it reads `state.py` alone, which is the only module
    that touches `needs_read` at all - `_Slot` and `DeviceState._slots` are both
    private, and nothing outside can reach a slot to assign to one. Within that
    file it sees a plain, augmented or annotated assignment whose target names
    `needs_read`, at any depth, in a function or out of one, `async` or not, a
    tuple target included. It does NOT see `setattr(slot, "needs_read", True)`,
    nor an assignment made through another reference to the same slot.
    """
    counts = _mark_setters(ast.parse(pathlib.Path(state.__file__).read_text()))

    differs = {name: (counts.get(name, 0), MAY_SET_THE_MARK_BY_HAND.get(name, 0))
               for name in set(counts) | set(MAY_SET_THE_MARK_BY_HAND)
               if counts.get(name) != MAY_SET_THE_MARK_BY_HAND.get(name)}
    assert not differs, (
        f"{differs} sets an entry's mark a different number of times than this "
        f"file accounts for (found, accounted for). Call `mark_for_reread` "
        f"instead, or account for it above with the reason a read in flight "
        f"may throw the mark away")


@pytest.mark.parametrize("entry", entries.ENTRIES, ids=lambda entry: entry.name)
def test_a_marking_push_during_any_entrys_read_survives_it(entry, link):
    """Every entry's read answer COUNTS, which the rule above depends on.

    The uncounted mark in `_apply_one` is safe because the marking push and the
    read's own answer are two arrivals, and the read path's bar is more than
    one. That needs the answer to count, and since PR #34 a message of a tracked
    type counts only if it said something - so an entry whose read were answered
    by a message carrying nothing would sit at one arrival, and a push that
    marked it mid-read would be dropped exactly as the two tests above describe.

    True of all five entries today. Pinned per entry rather than argued once,
    because it is a property of each entry's read and the next entry inherits
    nothing.
    """
    transport, cache = link
    marking = with_an_unknown_field(next(iter(entry.feeds))())

    def marks_first(canned):
        def pushing(*args):
            transport.push(marking)
            return canned(*args) if callable(canned) else canned
        return pushing

    for table in (transport.replies, transport.broadcasts):
        for name, canned in list(table.items()):
            table[name] = marks_first(canned)

    cache.value(entry.name, sorted(entry.fields())[0])

    assert cache.needs_read(entry.name) is True, (
        "the answer to this entry's read did not count as an arrival, so the "
        "push that marked it mid-read was thrown away")


# -- the tempo stream ---------------------------------------------------------


def test_the_metronome_stream_causes_no_reads_and_no_churn(link):
    """Section 9, smaller decision 7. At 40 bpm the unit pushes a pair every
    1.5 s for the life of every connection. An invalidation-based cache would
    spend its life re-reading."""
    transport, cache = link
    transport.push(dirty_push(True))
    assert cache.value("identity", "app_fw_version") == "d14e"
    reads_before = dict(transport.reads)

    for _ in range(40):                        # a minute of beats at 40 bpm
        for message in tempo_pair():
            transport.push(message)

    assert dict(transport.reads) == reads_before
    assert cache.needs_read("identity") is False
    assert cache.needs_read("dirty") is False
    assert cache.value("dirty", "is_dirty") is True
    assert cache.value("identity", "app_fw_version") == "d14e"
    assert dict(transport.reads) == reads_before


def test_the_stream_above_really_reaches_the_cache(link):
    """Guards the test above, which eighty pushes into a void would also pass.

    Same transport, same listeners, one message the cache does track: if the
    delivery path were broken this fails and the churn test stops meaning
    anything.
    """
    transport, cache = link
    for message in tempo_pair():
        transport.push(message)
    transport.push(dirty_push(True))

    assert cache.value("dirty", "is_dirty") is True
    assert transport.reads["PresetDirtyMessage"] == 0


def test_a_message_type_no_entry_tracks_is_ignored_outright(link):
    """Section 9: "A message of a type we know nothing about is ignored
    outright, which is what the RX thread already does." """
    transport, cache = link
    cache.value("identity", "app_fw_version")
    transport.push(dirty_push(True))

    transport.push(pa.IOMeterMessage(action=pa.MessageAction.UPDATE))
    transport.push(pa.CPULoadMessage(action=pa.MessageAction.UPDATE))

    assert cache.needs_read("identity") is False
    assert cache.needs_read("dirty") is False


# -- state the unit does not volunteer ---------------------------------------


def test_state_the_unit_never_broadcasts_is_read_on_first_access(link):
    """No model property ships with a staleness caveat, so the fallback is a
    read rather than a shrug. Version is that case: the unit answers a READ and
    never announces its own firmware."""
    transport, cache = link
    assert transport.reads["VersionMessage"] == 0

    assert cache.value("identity", "app_fw_version") == "d14e"

    assert transport.reads["VersionMessage"] == 1


def test_a_second_access_of_the_same_entry_costs_no_round_trip(link):
    transport, cache = link
    cache.value("identity", "app_fw_version")
    cache.value("identity", "device_serial_number")
    cache.value("identity", "app_fw_version")
    assert transport.reads["VersionMessage"] == 1


def test_a_read_replaces_the_entry_rather_than_merging_into_it(link):
    """A read is the unit's whole answer, so a field it does not carry is a
    field the unit did not confirm. Leaving the old value in place would report
    something no read has returned."""
    transport, cache = link
    assert cache.value("identity", "device_serial_number") == "QCS0000001"
    cache.mark_for_reread("identity", "this test")
    transport.replies["VersionMessage"] = lambda: version_reply(
        app_fw_version="d15a")

    assert cache.value("identity", "app_fw_version") == "d15a"
    with pytest.raises(RuntimeError, match="device_serial_number"):
        cache.value("identity", "device_serial_number")


def test_a_field_the_unit_did_not_send_is_refused_not_reported_empty(link):
    """An absent string decodes as "", and reporting that is the guess this
    whole layer exists to avoid."""
    transport, cache = link
    transport.replies["VersionMessage"] = lambda: version_reply(
        app_fw_version="d14e")
    with pytest.raises(RuntimeError, match="device_serial_number"):
        cache.value("identity", "device_serial_number")


def test_an_incomplete_answer_leaves_a_retry_able_to_recover(link):
    transport, cache = link
    transport.replies["VersionMessage"] = lambda: version_reply(
        app_fw_version="d14e")
    with pytest.raises(RuntimeError):
        cache.value("identity", "device_serial_number")

    transport.replies["VersionMessage"] = full_version_reply
    assert cache.value("identity", "device_serial_number") == "QCS0000001"
    assert transport.reads["VersionMessage"] == 2


def test_the_field_the_unit_did_send_is_still_answered_from_the_cache(link):
    """Per field, here too: a reply that carried the firmware and not the serial
    told us the firmware, and a retry is only owed for the half that is missing.
    """
    transport, cache = link
    transport.replies["VersionMessage"] = lambda: version_reply(
        app_fw_version="d14e")
    with pytest.raises(RuntimeError):
        cache.value("identity", "device_serial_number")

    assert cache.value("identity", "app_fw_version") == "d14e"
    assert transport.reads["VersionMessage"] == 1


def test_a_read_before_the_cache_is_bound_to_a_connection_is_refused(link):
    transport, _ = link
    unbound = state.DeviceState()
    with pytest.raises(RuntimeError, match="not connected"):
        unbound.value("identity", "app_fw_version")


def test_a_field_no_entry_keeps_is_a_programming_error_not_a_read(link):
    transport, cache = link
    with pytest.raises(KeyError, match="power_option"):
        cache.value("identity", "power_option")
    assert transport.reads["VersionMessage"] == 0


# -- what the entries declare ------------------------------------------------


@pytest.mark.parametrize("entry", entries.ENTRIES, ids=lambda e: e.name)
def test_a_field_declared_presence_free_really_has_none(entry):
    """The one exception to the presence rule, checked against the schema.

    A field listed here is read with no presence check, so if the schema gives
    it presence the declaration downgrades a checkable answer to an unchecked
    one - which is the guess the rule forbids.
    """
    for message_class, plan in entry.feeds.items():
        for name in plan.no_presence:
            field = message_class.DESCRIPTOR.fields_by_name[name]
            assert not field.has_presence, (
                f"{message_class.__name__}.{name} does have presence - keep it "
                f"in `kept` and let the presence check do its job")


@pytest.mark.parametrize("entry", entries.ENTRIES, ids=lambda e: e.name)
def test_a_kept_field_is_one_the_wire_can_report_absent(entry):
    for message_class, plan in entry.feeds.items():
        for name in plan.kept:
            field = message_class.DESCRIPTOR.fields_by_name[name]
            assert field.has_presence, (
                f"{message_class.__name__}.{name} has no presence, so an unset "
                f"message reports its default as an answer - declare it in "
                f"`no_presence` with the evidence for what absent means")


@pytest.mark.parametrize("entry", entries.ENTRIES, ids=lambda e: e.name)
def test_no_field_an_entry_leaves_unkept_is_invisible_on_the_wire(entry):
    """The one blind spot the per-field check genuinely has, held shut.

    A proto3 scalar with no presence is written only when it differs from its
    default, so a message that leaves one at its default carries no bytes for it
    at all - ``PresetDirty{is_dirty: False}`` serialises to two bytes, both of
    them ``action``. Nothing can see a change to such a field, because there is
    nothing to see: not ``ListFields``, not the unknown-field weigh-in, not a
    hand-written parser.

    So a presence-free field has to be KEPT or it can never be noticed, and
    "does the model keep it?" is a question about our code rather than about the
    wire - which makes it checkable, which is this test. It fires the day an
    entry is fed by a type carrying one it does not keep, and the answer then is
    to keep it with the evidence for what its default means, exactly as
    ``is_dirty`` is kept.
    """
    for message_class, plan in entry.feeds.items():
        if plan.voids_the_copy():
            # This plan has no blind spot, because it does not look. Every
            # message of this type makes the entry untrusted whatever it
            # carries, which is a STRONGER answer than keeping the field: it
            # cannot be fooled by a field the wire renders as nothing. That is
            # exactly why `Grid` and `SceneLabel` are declared this way - see
            # `FieldPlan.invalidates`.
            continue
        declared = plan.kept | plan.no_presence | entries.SCAFFOLDING
        invisible = sorted(field.name for field in message_class.DESCRIPTOR.fields
                           if not field.has_presence and field.name not in declared)
        assert not invisible, (
            f"{entry.name} does not keep {message_class.__name__}.{invisible}, "
            f"which the wire cannot report as absent - so a change to it is "
            f"undetectable rather than merely unkept. Either keep it with the "
            f"evidence for what its default means, as `is_dirty` is kept, or "
            f"declare the whole type as voiding the copy")


@pytest.mark.parametrize("entry", entries.ENTRIES, ids=lambda e: e.name)
def test_a_plan_that_voids_the_copy_really_does_it_unconditionally(entry):
    """The exemption above has to be load-bearing.

    A plan skipped there is trusted to mark the entry from an EMPTY message,
    because that is what the types it exists for can look like: a `Grid` UPDATE
    with nothing but its action, or a `SceneLabel` renaming scene A to a blank
    label, both of which set nothing in `ListFields()`. If the flag ever stopped
    doing that, the skip above would be forgiving a real blind spot.
    """
    for message_class, plan in entry.feeds.items():
        if not plan.voids_the_copy():
            continue
        # Asserted on the PLAN, not on a message. An earlier version built an
        # empty message and checked `fields_applied` was empty - which it is for
        # any presence-bearing field regardless of what the plan declares, so
        # the assertion held for a plan keeping two fields and proved nothing.
        assert not (plan.kept | plan.no_presence), (
            f"{entry.name} both voids its copy on {message_class.__name__} and "
            f"keeps {sorted(plan.kept | plan.no_presence)} from it, which is "
            f"two answers to one question")
        # And the property the exemption actually rests on: an empty message of
        # this type still marks the entry. That is what the per-field check
        # cannot do, and why these types are declared by type at all.
        assert entries.unkept_fields(message_class(), plan) == []
        assert plan.voids_the_copy(), (
            f"nothing would mark {entry.name} from an empty "
            f"{message_class.__name__}, so the skip above forgives a real "
            f"blind spot")


@pytest.mark.parametrize("entry", entries.ENTRIES, ids=lambda e: e.name)
def test_nothing_an_entry_keeps_is_a_container_the_rx_thread_owns(entry):
    """The cache stores what ``getattr`` hands back, and for a message or a
    repeated field that is a live container INSIDE the message the RX thread
    just decoded - shared with every other listener and read afterwards from
    other threads.

    Scalars are copied by value and need nothing. A SUBMESSAGE is copied on the
    way in (``entries._held``), which is what makes the preset entry safe to
    hold a whole ``BinaryPreset``. This test proves the copy really happens
    rather than trusting that it does, by mutating the source afterwards.

    A repeated field is still refused outright. ``_held`` does not copy one, and
    nothing needs it to yet - the day something does, that is the moment to
    decide what a repeated field means in a cache rather than to discover it.
    """
    for message_class, plan in entry.feeds.items():
        for name in sorted(plan.kept | plan.no_presence):
            field = message_class.DESCRIPTOR.fields_by_name[name]
            assert not field.is_repeated, (
                f"{message_class.__name__}.{name} is repeated, so the cache "
                f"would hold a live container from a message the RX thread owns")
            if field.type not in (field.TYPE_MESSAGE, field.TYPE_GROUP):
                continue
            source = message_class()
            held = entries.fields_applied(_carrying_something(source, name), plan)
            assert name in held, (
                f"{message_class.__name__}.{name} did not survive being applied")
            assert held[name] is not getattr(source, name), (
                f"{entry.name} holds {message_class.__name__}.{name} BY "
                f"REFERENCE. That container lives inside a message the RX "
                f"thread decoded and handed to every other listener, so what "
                f"the model reports would change when any of them touches it")


@pytest.mark.parametrize("entry", entries.ENTRIES, ids=lambda e: e.name)
def test_every_field_an_entry_names_exists_on_the_message_that_feeds_it(entry):
    """A misspelled field name is a field silently never applied."""
    for message_class, plan in entry.feeds.items():
        known = {f.name for f in message_class.DESCRIPTOR.fields}
        missing = sorted((plan.kept | plan.no_presence) - known)
        assert not missing, (
            f"{entry.name} keeps {missing}, which {message_class.__name__} "
            f"does not have")


@pytest.mark.parametrize("entry", entries.ENTRIES, ids=lambda e: e.name)
def test_the_scaffolding_the_check_ignores_is_on_every_feeding_type(entry):
    """`action` and `request_id` are the transport's, not the unit's state, so
    the check skips them. If a feeding type lacked one, the skip would be
    forgiving a field that never arrives - and it would hide the day a message
    type starts carrying state in a field with one of those names."""
    for message_class in entry.feeds:
        known = {f.name for f in message_class.DESCRIPTOR.fields}
        assert entries.SCAFFOLDING <= known, (
            f"{message_class.__name__} lacks "
            f"{sorted(entries.SCAFFOLDING - known)}")


def test_every_entry_answers_a_read_for_every_field_it_keeps(link):
    """An entry the model cannot read is one it can only guess about."""
    transport, cache = link
    for entry in entries.ENTRIES:
        cache.mark_for_reread(entry.name, "this test")
        for field in entry.fields():
            cache.value(entry.name, field)      # raises if the read cannot serve it


# -- a closed connection answers nothing -------------------------------------


def test_a_closed_cache_refuses_a_read_it_could_have_served(link):
    """Anything the model caches is valid only while its connection is."""
    transport, cache = link
    cache.value("identity", "app_fw_version")
    cache.close()
    with pytest.raises(RuntimeError, match="closed"):
        cache.value("identity", "app_fw_version")


def test_a_closed_cache_stops_listening(link):
    transport, cache = link
    cache.close()
    transport.push(dirty_push(True))
    assert transport.listeners == []


def test_a_closed_cache_will_not_say_what_it_remembers(link):
    """`cached()` is a read too, and a closed one answering `{}` is not a
    refusal - it reads as "the unit told us nothing"."""
    transport, cache = link
    transport.push(dirty_push(True))
    assert cache.cached("dirty") == {"is_dirty": True}
    cache.close()
    with pytest.raises(RuntimeError, match="closed"):
        cache.cached("dirty")


def test_a_closed_cache_will_not_say_what_it_would_do_next(link):
    """`needs_read` flipped from True to False across `close()`, which is the
    answer "the next read is free" about a cache whose next read raises."""
    transport, cache = link
    cache.mark_for_reread("dirty", "this test")
    assert cache.needs_read("dirty") is True
    cache.close()
    with pytest.raises(RuntimeError, match="closed"):
        cache.needs_read("dirty")


def test_closing_twice_is_harmless(link):
    transport, cache = link
    cache.close()
    cache.close()


# -- writes ------------------------------------------------------------------


def test_a_write_updates_the_cache_before_any_echo_arrives(link):
    """Section 9, rule 3. Waiting for the echo would make every write pay for
    information we already have."""
    transport, cache = link
    transport.push(dirty_push(False))

    cache.write_through("dirty", {"is_dirty": True}, send=lambda: None)

    assert cache.value("dirty", "is_dirty") is True
    assert transport.reads["PresetDirtyMessage"] == 0


def test_the_write_reaches_the_unit(link):
    transport, cache = link
    cache.write_through("dirty", {"is_dirty": True},
                        send=lambda: transport.send(dirty_push(True)))
    assert [type(m).__name__ for m in transport.sent] == ["PresetDirtyMessage"]


def test_an_echo_carrying_every_field_we_sent_confirms_the_write(link):
    transport, cache = link
    watch = cache.write_through("dirty", {"is_dirty": True}, send=lambda: None)

    transport.push(dirty_push(True))

    assert watch.outcome is WatchOutcome.CONFIRMED


def test_an_echo_carrying_the_units_own_request_id_still_confirms(link):
    """Narrow on purpose, and worth saying what it does NOT pin.

    The scaffolding fields are stripped before the watcher sees the echo, so
    this proves the stripping and nothing about the "every field we sent" rule -
    an implementation demanding the whole echo equal what we sent passes it.
    That rule needs an echo carrying a KEPT field we did not send, which no
    entry here is wide enough to produce; ``tests/test_watch.py`` is where it
    lives, and that is the file's stated reason for existing.
    """
    transport, cache = link
    watch = cache.write_through("dirty", {"is_dirty": True}, send=lambda: None)

    echo = dirty_push(True)
    echo.request_id = 41                       # the unit's own, not ours
    transport.push(echo)

    assert watch.outcome is WatchOutcome.CONFIRMED


def test_an_echo_returning_another_value_for_a_field_we_sent_is_reported(link):
    """A bug in our code, now with a name and a location."""
    transport, cache = link
    watch = cache.write_through("dirty", {"is_dirty": True}, send=lambda: None)

    transport.push(dirty_push(False))

    assert watch.outcome is WatchOutcome.DIFFERENT
    assert watch.disagreement == ("is_dirty", True, False)


def test_the_unit_winning_a_disagreement_leaves_the_units_value_cached(link):
    """Applying the whole echo is what handles section 10's four legitimate
    cases for free, so a write the unit overrode must not be left behind."""
    transport, cache = link
    cache.write_through("dirty", {"is_dirty": True}, send=lambda: None)

    transport.push(dirty_push(False))

    assert cache.cached("dirty")["is_dirty"] is False


def test_a_disagreement_forces_a_reread_of_the_entry(link):
    """A write the unit contradicted is a write we do not understand, so the
    rest of what it claimed is not to be believed either."""
    transport, cache = link
    cache.write_through("dirty", {"is_dirty": True}, send=lambda: None)

    transport.push(dirty_push(False))

    assert cache.needs_read("dirty") is True


def test_a_disagreement_does_not_leave_an_unconfirmed_field_behind(link):
    """The case the mark is really for.

    A write of two fields, an echo carrying only one of them, and that one
    disagreeing. The other is a value we put in the cache ourselves, that the
    unit never confirmed, in a write the unit has just demonstrated it disagreed
    with - the "confidently wrong" state, reached down the one path that used to
    clear up after itself least.

    ``identity`` is used because it is the only entry today wide enough to have
    a second field; the rule is about the mechanism, not about that entry, and
    nothing in the model writes firmware.
    """
    transport, cache = link
    cache.write_through("identity",
                        {"app_fw_version": "MINE", "device_serial_number": "MINE"},
                        send=lambda: None)

    transport.push(version_reply(app_fw_version="THEIRS"))

    assert cache.cached("identity")["device_serial_number"] == "MINE"
    assert cache.needs_read("identity") is True
    assert cache.value("identity", "device_serial_number") == "QCS0000001", (
        "the unconfirmed field was never re-read, so the model kept answering "
        "with a value it made up")


def test_an_echo_that_never_comes_times_out_and_forces_a_reread(link):
    """A silently ignored write self-corrects instead of poisoning the cache."""
    transport, cache = link
    watch = cache.write_through("dirty", {"is_dirty": True}, send=lambda: None,
                                patience=0.05)

    assert watch.settled(timeout=5.0)
    assert watch.outcome is WatchOutcome.TIMED_OUT
    assert cache.needs_read("dirty") is True


def test_the_watcher_does_not_block_the_write(link):
    transport, cache = link
    started = time.monotonic()
    cache.write_through("dirty", {"is_dirty": True}, send=lambda: None,
                        patience=30.0)
    assert time.monotonic() - started < 1.0


def test_a_confirmed_write_does_not_force_a_reread(link):
    transport, cache = link
    transport.push(dirty_push(False))
    cache.write_through("dirty", {"is_dirty": True}, send=lambda: None,
                        patience=0.05)
    transport.push(dirty_push(True))

    time.sleep(0.2)                            # past the patience it was given
    assert cache.needs_read("dirty") is False


def test_a_write_whose_send_fails_is_taken_back_out_of_the_cache(link):
    """The unit never heard it, so our copy would be the only place it exists."""
    transport, cache = link
    transport.push(dirty_push(False))

    def send():
        raise TimeoutError("the unit did not take it")

    with pytest.raises(TimeoutError):
        cache.write_through("dirty", {"is_dirty": True}, send=send)

    assert cache.needs_read("dirty") is True


def test_a_failed_send_leaves_no_watcher_to_time_out_later(link):
    """The entry recovers on the next read and stays recovered.

    A watcher left behind for a write the unit never received would fire at its
    deadline and mark the entry again, so a caller who had already put it right
    would find it wrong once more for no reason.
    """
    transport, cache = link

    def send():
        raise TimeoutError("the unit did not take it")

    with pytest.raises(TimeoutError):
        cache.write_through("dirty", {"is_dirty": True}, send=send, patience=0.05)
    assert cache.value("dirty", "is_dirty") is False    # the read clears the mark

    time.sleep(0.25)                                    # well past that patience
    assert cache.needs_read("dirty") is False


def test_a_write_to_a_field_the_entry_does_not_keep_is_refused(link):
    """A write the cache cannot hold would be applied nowhere and confirmed
    against nothing."""
    transport, cache = link
    with pytest.raises(ValueError, match="power_option"):
        cache.write_through("dirty", {"power_option": 1}, send=lambda: None)


def test_a_write_through_a_closed_cache_is_refused(link):
    transport, cache = link
    cache.close()
    with pytest.raises(RuntimeError, match="closed"):
        cache.write_through("dirty", {"is_dirty": True}, send=lambda: None)


def test_the_watchdog_does_not_outlive_the_connection(link):
    transport, cache = link
    cache.write_through("dirty", {"is_dirty": True}, send=lambda: None,
                        patience=30.0)
    assert _watchdog_threads(), "no watchdog was started, so this proves nothing"

    cache.close()

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not _watchdog_threads():
            return
        time.sleep(0.02)
    pytest.fail("the write watchdog is still running after close()")


def test_a_write_still_in_flight_when_the_connection_closes_does_not_hang(link):
    """Nothing can settle it once the connection is gone, so nobody may wait.

    ``settled()`` with its documented default waits forever, and the watchdog
    deliberately does NOT call a closed connection's outstanding writes timed
    out - that would be a claim about the unit rather than a fact. So the
    waiting has to end without an outcome.
    """
    transport, cache = link
    watch = cache.write_through("dirty", {"is_dirty": True}, send=lambda: None,
                                patience=30.0)

    cache.close()

    started = time.monotonic()
    assert watch.settled(timeout=5.0) is False
    assert time.monotonic() - started < 1.0, (
        "it waited out the timeout it was given, which with settled()'s "
        "documented default of None is forever")
    assert watch.outcome is None


def test_a_write_that_races_the_close_leaves_no_thread_behind(link):
    """The window between `write_through` checking the cache is open and the
    watchdog starting its thread. A thread started there waits forever on a
    connection nobody can reach."""
    transport, cache = link
    cache.close()

    cache._watchdog.add(_a_watch_for_the_race())

    assert not _watchdog_threads()


def _a_watch_for_the_race():
    from pyquadcortex.device.watch import WriteWatch
    return WriteWatch("dirty", {"is_dirty": True}, time.monotonic() + 30.0)


def test_the_watchdog_firing_as_the_connection_closes_does_not_raise(link):
    """The watchdog is inside its callback when `close()` lands. Marking a slot
    that is gone would raise on a thread with nobody to catch it."""
    transport, cache = link
    watch = cache.write_through("dirty", {"is_dirty": True}, send=lambda: None,
                                patience=30.0)
    cache.close()

    cache._gave_up_on(watch)                   # no exception is the assertion


def test_no_watchdog_runs_until_something_is_written(link):
    transport, cache = link
    cache.value("identity", "app_fw_version")
    transport.push(dirty_push(True))
    assert not _watchdog_threads()


def _watchdog_threads():
    return [t for t in threading.enumerate()
            if t.name.startswith(state.WATCHDOG_THREAD_NAME)]


# -- the preset and the active scene ------------------------------------------
#
# These two entries are what the grid, the scenes and `device.preset` read
# through. Both reads are one request and one answer, which is what `StateEntry`
# requires - the Directory's listings are the streaming case and they are not
# here.


def test_the_preset_entry_is_registered():
    assert entries.ENTRY_BY_NAME["preset"].fields() >= {"preset"}


def test_the_scene_entry_is_registered():
    assert entries.ENTRY_BY_NAME["scene"].fields() == {"selected_scene"}


def test_the_preset_entry_keeps_the_reason_every_push_carries():
    """Measured 2026-08-15: the connect burst's seed `RecallPreset` sets action,
    preset AND reason, and so does the push a recall produces. An entry that did
    not keep `reason` would be marked for re-reading by the very burst that
    warmed it."""
    plan = entries.PRESET.feeds[pa.RecallPresetMessage]
    assert plan.kept == {"preset", "reason"}


def test_the_seed_push_the_unit_really_sends_leaves_the_entry_trusted():
    """The shape the burst delivers, field for field, held against the plan."""
    plan = entries.PRESET.feeds[pa.RecallPresetMessage]
    seed = recall_push()
    assert sorted(f.name for f, _ in seed.ListFields()) == \
        ["action", "preset", "reason"], "the fixture no longer matches the unit"
    assert not entries.unkept_fields(seed, plan)


def test_the_preset_entry_can_read_back_every_field_it_keeps(link):
    """The rule that made keeping `reason` cost something: an entry that keeps
    a field its read cannot answer for loses it the first time it is marked.
    So the read goes through `read_current_preset_push`, which hands back the
    whole reply rather than just the preset inside it."""
    transport, cache = link
    cache.mark_for_reread("preset", "this test")
    assert cache.value("preset", "reason") is not None
    assert cache.value("preset", "preset").name == "Structural Fixture"


def test_a_recall_push_does_not_invalidate_the_preset_it_delivers():
    plan = entries.PRESET.feeds[pa.RecallPresetMessage]
    assert not plan.voids_the_copy()


def test_a_grid_push_invalidates_the_preset_however_empty_it_is():
    """`action` has no presence and lives in SCAFFOLDING, so a Grid UPDATE and a
    Grid DELETE with the same payload are indistinguishable to the per-field
    check - and a Grid message carrying nothing else sets no fields at all. The
    entry therefore does not rely on that check: every Grid push means the grid
    moved, and that IS this entry's decision about `action`."""
    plan = entries.PRESET.feeds[pa.GridMessage]
    assert plan.invalidates
    assert not entries.unkept_fields(pa.GridMessage(), plan), (
        "an empty Grid message names nothing, which is exactly why the flag "
        "and not the field check has to be what marks this entry")


def test_a_scene_label_push_invalidates_the_preset():
    """`SceneLabelMessage.index` and `.label` have NO presence, so renaming
    scene A to a blank label sets nothing in `ListFields()` and the per-field
    check sees an empty message. Scene names live in the preset payload, so our
    copy of it is now wrong and nothing in the message says so."""
    plan = entries.PRESET.feeds[pa.SceneLabelMessage]
    assert plan.invalidates
    assert not entries.unkept_fields(pa.SceneLabelMessage(), plan)


def test_a_scene_colour_push_invalidates_the_preset_too():
    """The model does not expose scene colours, but it holds the whole preset
    payload and `scene_colors` is inside it. There is no harmless-field
    category (root CLAUDE.md), so this marks like everything else."""
    assert entries.PRESET.feeds[pa.SceneColorMessage].invalidates


def test_the_preset_entry_never_hears_about_the_loaded_slot():
    """`SetlistPosition` says WHICH preset is loaded, not what is in it, so it
    feeds the `loaded` entry and not this one.

    Measured 2026-08-15: a recall pushes eight to thirteen `Grid` messages, then
    `RecallPreset` carrying the whole new preset, then `Scene`, then
    `SetlistPosition` - which arrives about 90 ms LAST. The preset entry is
    already right by then, twice over. And the connect burst carries no `Grid`
    pushes at all, because nothing changed there: marking the preset on this
    message would throw away exactly what the burst had just delivered.
    """
    assert pa.SetlistPositionMessage not in entries.PRESET.feeds


def test_the_loaded_slot_is_its_own_entry():
    """It used to be a counter on the preset entry, bumped whenever a recall was
    seen. It is the unit's own answer now: `SetlistPosition{READ}` really does
    reply - 3 ms, request id echoed, confirmed 2026-08-15 - so `is_current` can
    compare a fact the unit stated rather than the model's own bookkeeping."""
    assert entries.ENTRY_BY_NAME["loaded"].fields() == {
        "folder_key", "position", "is_factory"}


def test_a_change_of_loaded_slot_resets_the_dirty_flag_and_the_scene():
    """The measurement that mattered most: a recall pushes NO PresetDirty. It
    clears the unsaved-changes flag on the unit and says nothing about it, so
    without this the model would go on reporting edits the recall discarded.

    Declared on the `loaded` entry rather than as a plan on each of the other
    two, because it must fire on a CHANGE of slot and not on every message of
    that type - the model's own read of the loaded slot is one of those.
    """
    assert entries.LOADED.resets == ("dirty", "scene")
    assert pa.SetlistPositionMessage not in entries.DIRTY.feeds
    assert pa.SetlistPositionMessage not in entries.SCENE.feeds


def test_a_recall_really_does_reset_them(link):
    transport, cache = link
    cache.apply_push(dirty_push(True))
    cache.apply_push(scene_push(scene=2))
    cache.apply_push(recalled_elsewhere(position=9))
    assert not cache.needs_read("dirty") and not cache.needs_read("scene"), (
        "the first sighting of a slot is not a change - there was nothing to "
        "reset yet")
    cache.apply_push(recalled_elsewhere(position=17))
    assert cache.needs_read("dirty")
    assert cache.needs_read("scene")


def test_the_models_own_read_of_the_loaded_slot_resets_nothing(link):
    """Asking which preset is loaded is a question, not news. An earlier version
    put `invalidates` on both entries, so one `device.preset` on a cold cache
    marked them and published two Invalidated events - the model reporting its
    own question as something the unit had done."""
    transport, cache = link
    cache.apply_push(dirty_push(True))
    cache.apply_push(scene_push(scene=2))
    seen = []
    cache.events.subscribe(seen.append)
    cache.value("loaded", "position")
    assert not cache.needs_read("dirty")
    assert not cache.needs_read("scene")
    assert [e for e in stays_quiet(seen) if isinstance(e, events.Invalidated)] == []


def test_an_edit_does_not_touch_the_loaded_slot():
    """Someone turning a knob is still the same preset. Only a recall makes a
    Preset object somebody is holding stale."""
    assert pa.GridMessage not in entries.LOADED.feeds


def test_a_scene_push_carries_the_active_scene():
    plan = entries.SCENE.feeds[pa.SceneMessage]
    assert plan.kept == {"selected_scene"}
    message = pa.SceneMessage(selected_scene=3)
    assert entries.fields_applied(message, plan) == {"selected_scene": 3}
    assert not entries.unkept_fields(message, plan)


def test_the_preset_read_asks_for_the_live_grid(link):
    """`read_current_preset` reads what is on the grid RIGHT NOW, unsaved edits
    included, with no side effects. `read_preset` would RECALL a stored slot -
    interrupting the audio every time and resetting the active scene - which is
    the opposite of a read."""
    transport, cache = link
    cache.value("preset", "preset")
    asked = [m for m in transport.sent if isinstance(m, pa.RecallPresetMessage)]
    assert asked, "nothing asked the unit for the preset"
    assert all(m.action == pa.MessageAction.READ for m in asked)
    assert not any(isinstance(m, pa.SetlistPositionMessage) for m in transport.sent), (
        "the read recalled a slot, which changes what the unit is playing")


def test_a_grid_push_marks_the_preset_without_merging_anything(link):
    transport, cache = link
    cache.apply_push(grid_push())
    assert cache.needs_read("preset")
    assert cache.cached("preset") == {}


def test_forty_grid_pushes_still_cost_one_read(link):
    """A flag, not a queue. One edit on the touchscreen produces about forty."""
    transport, cache = link
    for _ in range(40):
        cache.apply_push(grid_push())
    cache.value("preset", "preset")
    assert transport.reads["RecallPresetMessage"] == 1


def test_the_preset_is_kept_as_a_copy_not_as_a_live_container(link):
    """The cache stores what `getattr` hands back, and for a submessage that is
    a container INSIDE the message the receiving thread just decoded - shared
    with every other listener and read afterwards from other threads.

    So it is copied. This is the test that says so, because the structural check
    below can only see that a submessage IS kept, not whether it was copied.
    """
    transport, cache = link
    push = recall_push()
    cache.apply_push(push)
    held = cache.cached("preset")["preset"]
    assert held.name == "Structural Fixture"
    push.preset.name = "mutated by somebody else"
    assert held.name == "Structural Fixture", (
        "the cache is holding a reference into a message it does not own, so "
        "anyone else who decoded or mutated it changes what the model reports")


def test_the_active_scene_is_read_and_then_free(link):
    transport, cache = link
    assert cache.value("scene", "selected_scene") == 0
    assert cache.value("scene", "selected_scene") == 0
    assert transport.reads["SceneMessage"] == 1


def test_a_scene_switch_on_the_unit_reaches_the_cache(link):
    transport, cache = link
    cache.apply_push(scene_push(scene=3))
    assert cache.cached("scene")["selected_scene"] == 3
    assert not cache.needs_read("scene")


def test_a_recall_elsewhere_changes_the_loaded_slot(link):
    """What `preset.is_current` compares."""
    transport, cache = link
    cache.apply_push(recalled_elsewhere(position=9))
    was = cache.cached("loaded")
    cache.apply_push(recalled_elsewhere(position=17))
    assert cache.cached("loaded") != was
    assert cache.cached("loaded")["position"] == 17


def test_an_edit_leaves_the_loaded_slot_alone(link):
    """Someone turning a knob is still the same preset - our copy of its
    contents is merely behind. Only a recall makes a held Preset stale."""
    transport, cache = link
    cache.apply_push(recalled_elsewhere())
    was = cache.cached("loaded")
    cache.apply_push(grid_push())
    assert cache.cached("loaded") == was


def test_reading_the_loaded_slot_from_the_cache_never_asks_the_unit(link):
    """`preset.is_current` promises no round trip, and it reads through this."""
    transport, cache = link
    cache.apply_push(recalled_elsewhere())
    cache.mark_for_reread("loaded", "this test")
    assert cache.cached("loaded")["position"] == 9
    assert transport.reads["SetlistPositionMessage"] == 0


# -- what the model tells a caller it noticed ---------------------------------


def test_forty_grid_pushes_produce_one_invalidated_event(link):
    """Fired on the change from trusted to untrusted, not on every push."""
    transport, cache = link
    seen = []
    cache.events.subscribe(seen.append)
    for _ in range(40):
        cache.apply_push(grid_push())
    wait_for(seen, 1)
    assert stays_quiet(seen) == seen
    assert len(seen) == 1
    assert isinstance(seen[0], events.Invalidated)
    assert seen[0].part == "preset"


def test_the_next_invalidation_after_a_read_is_announced_again(link):
    """The mark is cleared by a read, so the caller hears about the NEXT edit
    rather than being told once per connection."""
    transport, cache = link
    seen = []
    cache.events.subscribe(seen.append)
    cache.apply_push(grid_push())
    wait_for(seen, 1)
    cache.value("preset", "preset")
    cache.apply_push(grid_push())
    wait_for(seen, 2)


def test_a_push_restating_what_we_already_knew_is_not_a_change(link):
    """The unit pushes `PresetDirty` on every edit whether or not the answer is
    new. Reporting those would make the stream useless for what it is for."""
    transport, cache = link
    cache.apply_push(dirty_push(True))
    seen = []
    cache.events.subscribe(seen.append)
    cache.apply_push(dirty_push(True))
    assert stays_quiet(seen) == []


def test_a_push_that_moves_a_value_is_a_change(link):
    transport, cache = link
    cache.apply_push(dirty_push(True))
    seen = []
    cache.events.subscribe(seen.append)
    cache.apply_push(dirty_push(False))
    wait_for(seen, 1)
    assert seen[0] == events.Changed("dirty", ("is_dirty",))


def test_the_first_time_a_value_arrives_is_a_change(link):
    transport, cache = link
    seen = []
    cache.events.subscribe(seen.append)
    cache.apply_push(dirty_push(True))
    wait_for(seen, 1)
    assert seen[0] == events.Changed("dirty", ("is_dirty",))


def test_an_event_is_never_delivered_on_the_receiving_thread(link):
    """A subscriber may read from the unit, and the transport refuses a read on
    the thread that applies pushes (ADR-0009). So delivery cannot be on it."""
    transport, cache = link
    seen = []
    cache.events.subscribe(
        lambda e: seen.append(threading.current_thread().name))
    here = threading.current_thread().name
    cache.apply_push(grid_push())
    wait_for(seen, 1)
    assert seen[0] != here


def test_closing_the_cache_closes_the_event_stream(link):
    transport, cache = link
    cache.events.subscribe(lambda e: None)
    cache.close()
    with pytest.raises(RuntimeError, match="closed"):
        cache.events.subscribe(lambda e: None)


# -- the connect burst leaves the cache genuinely warm ------------------------
#
# Measured on hardware 2026-08-15. About 3 s of quiet, ~400 File messages, then
# at 10.04 s these four inside ten milliseconds, in this order:
#
#     RecallPreset     ['action', 'preset', 'reason']
#     SetlistPosition  ['action', 'folder_key', 'is_factory', 'position']
#     PresetDirty      ['action']            (is_dirty has no presence)
#     Scene            ['action', 'selected_scene']
#
# Two of them mark an entry and the next one answers it in full. Without the
# rule that a complete push clears the mark, every one of those entries would
# cost a round trip on first access - which is what "warm for free" is supposed
# to mean, and what the acceptance criteria ask for by name.


def the_connect_burst():
    """The four state messages the burst delivers, in the measured order."""
    return [recall_push(), recalled_elsewhere(), dirty_push(False),
            scene_push(scene=4)]


def test_after_the_connect_burst_nothing_needs_re_reading(link):
    transport, cache = link
    for message in the_connect_burst():
        cache.apply_push(message)
    stale = [name for name in ("preset", "scene", "dirty")
             if cache.needs_read(name)]
    assert not stale, (
        f"{stale} would go to the unit on first access, for values the connect "
        f"burst already delivered")


def test_after_the_connect_burst_reading_costs_nothing(link):
    """The assertion that cannot be satisfied by looking at a flag."""
    transport, cache = link
    for message in the_connect_burst():
        cache.apply_push(message)
    before = sum(transport.reads.values())
    assert cache.value("preset", "preset").name == "Structural Fixture"
    assert cache.value("scene", "selected_scene") == 4
    assert cache.value("dirty", "is_dirty") is False
    assert sum(transport.reads.values()) == before, (
        "the model asked the unit for something the burst had already given it")


def test_a_partial_push_does_not_answer_an_entry(link):
    """The condition that keeps the rule honest. `identity` keeps two fields,
    and a Version carrying one of them is not the unit's whole answer - so it
    must not clear a mark."""
    transport, cache = link
    cache.mark_for_reread("identity", "this test")
    cache.apply_push(version_reply(app_fw_version="d14e"))
    assert cache.needs_read("identity")


def test_a_grid_push_never_answers_the_entry_it_marks(link):
    """The trap in the rule, and the reason it compares against the ENTRY's
    field set rather than the plan's. A Grid plan keeps nothing, so "carries
    every field this plan keeps" is vacuously true of it - and a Grid push would
    clear the very mark it had just set."""
    transport, cache = link
    cache.apply_push(grid_push())
    assert cache.needs_read("preset")
    cache.apply_push(grid_push())
    assert cache.needs_read("preset")


def test_a_push_that_names_something_unkept_does_not_answer_the_entry(link):
    """Complete in its known fields and still not the whole story: a field
    number the schema has never heard of means something changed that we cannot
    see, so this cannot be the unit's whole answer."""
    transport, cache = link
    cache.mark_for_reread("dirty", "this test")
    cache.apply_push(with_an_unknown_field(dirty_push(True)))
    assert cache.needs_read("dirty")


def test_a_recall_leaves_the_dirty_flag_needing_a_read(link):
    """The measured case that makes a change of loaded slot reset `dirty`: a
    recall pushes no PresetDirty, so nothing answers this entry and it has to
    ask.

    The burst runs first, because it always does - the recall has to be a change
    of slot, and there is no such thing as a connection where the first
    SetlistPosition anyone sees is a recall.
    """
    transport, cache = link
    for message in the_connect_burst():
        cache.apply_push(message)
    cache.apply_push(dirty_push(True))
    assert not cache.needs_read("dirty")
    # the measured order of a real recall, to a different slot
    for message in [grid_push(), recall_push(), scene_push(scene=0),
                    recalled_elsewhere(position=17)]:
        cache.apply_push(message)
    assert cache.needs_read("dirty"), (
        "a recall discards unsaved edits and says nothing about it, so the "
        "model would go on reporting changes that no longer exist")


def test_a_recall_in_the_order_the_unit_sends_it_leaves_the_preset_trusted(link):
    """The measured order is Grid FIRST, then RecallPreset about 90 ms later.

    Replayed that way round, the RecallPreset carries the whole entry and clears
    the mark the Grid pushes set - so a recall does NOT leave the preset needing
    a read, which is the desirable behaviour and the whole reason `reason` is
    kept. An earlier version of this test applied the two backwards and asserted
    the opposite, which the real ordering contradicts.
    """
    transport, cache = link
    for message in the_connect_burst():
        cache.apply_push(message)
    for message in [grid_push(), grid_push(), recall_push()]:
        cache.apply_push(message)
    assert not cache.needs_read("preset")


def test_an_edit_on_the_unit_does_leave_the_preset_needing_a_read(link):
    """The case the Grid pushes exist for, with no RecallPreset behind them."""
    transport, cache = link
    for message in the_connect_burst():
        cache.apply_push(message)
    assert not cache.needs_read("preset")
    cache.apply_push(grid_push())
    assert cache.needs_read("preset")
