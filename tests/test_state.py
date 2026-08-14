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
import collections
import logging
import threading
import time

import pytest

from pyquadcortex.device import entries, state
from pyquadcortex.device.watch import WatchOutcome
from pyquadcortex.protocol import client as protocol_client
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


@pytest.fixture
def link():
    """A cache listening on a loopback link, over the real protocol client."""
    transport = LoopbackTransport()
    transport.replies["VersionMessage"] = full_version_reply
    transport.replies["PresetDirtyMessage"] = lambda: dirty_push(False)
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


def test_an_echo_the_unit_added_to_does_not_cry_wolf(link):
    """The bar is one sentence: every field we sent came back with the value we
    sent. NOT "the echo equals what we sent" - the unit legitimately changes
    things nobody asked about, and section 10 lists four of them."""
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

    assert cache.value("dirty", "is_dirty") is False


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


def test_no_watchdog_runs_until_something_is_written(link):
    transport, cache = link
    cache.value("identity", "app_fw_version")
    transport.push(dirty_push(True))
    assert not _watchdog_threads()


def _watchdog_threads():
    return [t for t in threading.enumerate()
            if t.name.startswith(state.WATCHDOG_THREAD_NAME)]
