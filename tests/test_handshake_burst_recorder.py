"""The hardware suite's connect-burst recorder, checked offline.

``tests/hardware/conftest.py`` attaches a listener before the handshake, records
what the unit pushes, and stops once the burst is over. Stopping is what makes the
recording mean "the burst" rather than "the traffic so far", and it has three
parts: recording stops, the listener comes off the transport, and a message
arriving after both - the RX thread notifies from a snapshot - does not reopen it.

**Who covers what**, because it is not obvious and getting it backwards leads to
deleting the wrong assertion:

* The hardware burst test covers the WIRING. If the fixture stopped calling
  ``record_until``, its ``assert handshake_burst.closed`` and ``unfinished() is
  None`` fail loudly - neither is a floor, so contamination cannot satisfy them.
  Those two lines are load-bearing, not belt-and-braces.
* This file covers the STOPPING ITSELF, which no hardware test can see: a recorder
  that sets its flag and keeps recording anyway, one that stops recording but
  stays on the transport, or one that stops on the FIRST message of the burst's
  closing group rather than the whole of it, reads exactly like a working one
  from the outside - the last of those about NINE runs in TEN, because losing
  ``Scene`` alone is enough and ``Scene`` is the one lost most often.

The two hardware tests that read ``burst_warmed`` for entries the burst delivers
call ``unfinished()`` before their own assertions. The identity test does not,
deliberately: identity reaches the cache from ``connect()``'s own ``Version``
READ at about 0.7 s, so a tail that never arrived would fail it for something it
does not depend on.

Timing is real here rather than faked, so the code under test is the code that
runs on the unit.
"""
import importlib.util
import threading
import time
from pathlib import Path

import pytest

from pyquadcortex.device import entries
from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa

_HARDWARE_CONFTEST = (
    Path(__file__).resolve().parent / "hardware" / "conftest.py")


@pytest.fixture(scope="module")
def recorder_class():
    """The real ``HandshakeBurst``, loaded from the hardware suite's conftest.

    Loaded by path under its own module name: without ``--hardware`` nothing in
    the hardware suite is collected from a recursive run and a named path is
    refused, so there is no other way to reach it from the offline suite, and
    pytest's own copy is untouched by this.
    """
    spec = importlib.util.spec_from_file_location(
        "hardware_conftest", _HARDWARE_CONFTEST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.HandshakeBurst


class FakeTransport:
    """The two methods ``HandshakeBurst`` uses, with the same removal contract."""

    def __init__(self):
        self.listeners = []

    def add_listener(self, listener):
        self.listeners.append(listener)
        return lambda: self.remove_listener(listener)

    def remove_listener(self, listener):
        if listener in self.listeners:
            self.listeners.remove(listener)
            return True
        return False


def _file_push():
    return pa.FileMessage(action=pa.MessageAction.UPDATE)


def _tail():
    """The four messages that close the burst, in the order the unit sends them.

    Built here rather than read off ``BURST_TAIL``, so a test that feeds the tail
    and a recorder that waits for it cannot agree with each other by sharing one
    wrong list.
    """
    return [
        pa.RecallPresetMessage(action=pa.MessageAction.UPDATE),
        pa.SetlistPositionMessage(action=pa.MessageAction.UPDATE, position=1),
        pa.PresetDirtyMessage(action=pa.MessageAction.UPDATE, is_dirty=False),
        pa.SceneMessage(action=pa.MessageAction.UPDATE, selected_scene=1),
    ]


def test_closing_stops_the_recording_and_takes_the_listener_off(recorder_class):
    transport = FakeTransport()
    burst = recorder_class()
    burst.attach(transport)
    assert transport.listeners == [burst], "attach did not register the recorder"

    burst(_file_push())
    assert burst.names() == ["FileMessage"]

    burst.close()
    assert transport.listeners == [], "the recorder stayed on the transport"

    # A message can still arrive after the removal, because the RX thread notifies
    # from a snapshot taken before the first listener ran.
    burst(pa.SceneMessage(action=pa.MessageAction.UPDATE, selected_scene=1))
    assert burst.names() == ["FileMessage"], "recorded after being closed"

    burst.close()          # idempotent: teardown must not care how it got here
    assert transport.listeners == []


def test_record_until_stops_when_the_whole_tail_has_arrived(recorder_class):
    transport = FakeTransport()
    burst = recorder_class()
    burst.attach(transport)

    def push_the_burst():
        for _ in range(3):
            burst(_file_push())
        for message in _tail():
            burst(message)

    feeder = threading.Timer(0.1, push_the_burst)
    feeder.start()
    started = time.monotonic()
    burst.record_until(recorder_class.BURST_TAIL, patience=5.0)
    took = time.monotonic() - started
    feeder.join()

    assert took < 5.0, "it waited out its patience instead of noticing the tail"
    assert burst.settled_in is not None
    assert burst.closed
    # The path the fixture actually takes, and the only one that used to go
    # unchecked here: a stop inlined as `self.closed = True; break` leaves the
    # recorder on the RX thread's listener list for the rest of the session and
    # every other assertion in this file stays green.
    assert transport.listeners == [], "the settle path left the recorder attached"
    # Against the order _tail() pushes, not the order BURST_TAIL declares:
    # record_until turns that tuple into a frozenset, so its order is a record
    # of what the unit does and nothing here may fail when it is corrected.
    assert burst.names() == ["FileMessage"] * 3 + [
        type(m).__name__ for m in _tail()]


def test_record_until_gives_up_rather_than_hanging_on_a_silent_unit(recorder_class):
    # A unit that never sends the sentinel must not hold the whole run. The
    # give-up is reported rather than swallowed, so the hardware test can say the
    # burst was cut off instead of asserting on half of it.
    transport = FakeTransport()
    burst = recorder_class()
    burst.attach(transport)
    burst(_file_push())

    burst.record_until(recorder_class.BURST_TAIL, patience=0.2)

    assert burst.settled_in is None, "it reported settling on a tail it never saw"
    assert burst.closed
    assert transport.listeners == []


def test_recording_and_reading_at_the_same_time_loses_nothing(recorder_class):
    # A smoke test, and labelled as one deliberately. The recorder is written to
    # from the RX thread while the test thread reads names(), so the paths do
    # overlap - but list.append and list() are atomic under CPython's GIL, so
    # removing the lock entirely leaves this green. It would NOT catch that, and
    # an earlier version of this comment claimed it would.
    transport = FakeTransport()
    burst = recorder_class()
    burst.attach(transport)

    # Mixed types on purpose: 800 identical messages leave _seen_types at one
    # element and never call missing() from the other thread, so the test that
    # advertises concurrency would not touch the state the poll reads.
    def push():
        for _ in range(50):
            for message in [pa.FileMessage(action=pa.MessageAction.UPDATE)] + _tail():
                burst(message)

    writers = [threading.Thread(target=push) for _ in range(4)]
    for writer in writers:
        writer.start()
    known = {"FileMessage"} | set(recorder_class.BURST_TAIL)
    while any(writer.is_alive() for writer in writers):
        assert all(name in known for name in burst.names())
        assert burst.missing() <= frozenset()   # no sentinels set: nothing outstanding
    for writer in writers:
        writer.join()

    # 4 writers x 50 passes x (one File plus the four-message tail).
    assert len(burst.names()) == 4 * 50 * 5


def test_record_until_waits_for_the_whole_tail_not_just_the_first_of_it(
        recorder_class):
    """The race that failed two hardware tests together, one run in fifteen.

    The burst's four closing messages arrive in the order ``RecallPreset``,
    ``SetlistPosition``, ``PresetDirty``, ``Scene``, inside six milliseconds -
    re-measured 2026-09-14 on d14e over three sessions, spreads of 6.0, 3.6 and
    5.8 ms (``docs/protocol.md``, "Connect burst, measured"). ``RecallPreset`` is
    the FIRST of the four, not the last. A recorder that stops on it stops with
    the poll's granularity of slack after it - so it can close in the gap before
    the other three land, and the fixture takes its ``burst_warmed`` snapshot of
    a cache that has not heard them yet. The two hardware tests that read that
    snapshot then fail together while every test reading the live cache passes,
    because the cache stays on the transport and gets the messages milliseconds
    later.

    The gap staged here is far wider than the unit's, so this does not depend on
    winning a race to say something. What it proves is the thing the old stop
    condition got wrong: arriving AFTER the first of a group is not arriving
    late.
    """
    transport = FakeTransport()
    burst = recorder_class()
    burst.attach(transport)

    def push_the_tail():
        first, *rest = _tail()
        burst(first)
        # Three poll intervals, where the unit takes six milliseconds.
        time.sleep(0.3)
        for message in rest:
            burst(message)

    feeder = threading.Thread(target=push_the_tail)
    feeder.start()
    burst.record_until(recorder_class.BURST_TAIL, patience=5.0)
    feeder.join()

    assert burst.settled_in is not None, (
        f"the recorder gave up rather than settling; it is still waiting for "
        f"{sorted(burst.missing())}")
    assert burst.unfinished() is None
    assert set(burst.names()) == set(recorder_class.BURST_TAIL), (
        f"the recorder stopped before the whole tail arrived: it holds "
        f"{burst.names()}")


def test_an_unfinished_recording_says_what_never_arrived(recorder_class):
    """A burst that times out must be readable as that, not as a bare absence.

    Without this the two possible failures are indistinguishable from the
    recording alone: the unit sent no ``PresetDirty`` at all, which is a finding
    about the unit, and the recorder stopped before it arrived, which is a bug
    here. The hardware tests put this string in front of their own assertions
    so a future failure says which it was.
    """
    transport = FakeTransport()
    burst = recorder_class()
    burst.attach(transport)
    burst(pa.RecallPresetMessage(action=pa.MessageAction.UPDATE))

    burst.record_until(recorder_class.BURST_TAIL, patience=0.2)

    assert burst.settled_in is None
    assert burst.missing() == {"SetlistPositionMessage", "PresetDirtyMessage",
                               "SceneMessage"}
    unfinished = burst.unfinished()
    assert "PresetDirtyMessage" in unfinished, unfinished
    assert "0.2" in unfinished, unfinished


def test_record_until_refuses_a_single_name(recorder_class):
    """One name is the bug this signature replaced, and a string is a collection.

    ``frozenset("RecallPresetMessage")`` is its eleven distinct letters, so
    passing the old argument to the new signature would wait for message types
    called ``R``, ``e``, ``c`` and never settle - a thirty-second timeout per
    run and a recording of nothing, arriving as a puzzle rather than as a
    mistake.
    """
    transport = FakeTransport()
    burst = recorder_class()
    burst.attach(transport)
    with pytest.raises(TypeError, match="collection"):
        burst.record_until("RecallPresetMessage", patience=0.1)
    assert transport.listeners == [], (
        "a refused call left the recorder on the transport, where it would "
        "record the whole session with nothing left to stop it")


#: Cache entries the connect burst does NOT warm, and why. A new ``StateEntry``
#: comes through here or through ``BURST_TAIL``, and there is no third way -
#: which is the point of the test below.
#: Feed types that reach an entry OUTSIDE the connect burst, with when they do.
#: The recorder must not wait for these: nothing sends them on connect, so a
#: wait would cost the full patience on every run.
OUTSIDE_THE_BURST = {
    "VersionMessage": "connect() READs it before the handshake (ADR-0020), and "
                      "the unit never volunteers it",
    "GridMessage": "one per block on a recall, and a burst of them on an edit - "
                   "never on connect, because nothing has changed yet",
    "SceneLabelMessage": "only when somebody renames a scene",
    "SceneColorMessage": "only when somebody recolours a scene",
}

NOT_WARMED_BY_THE_BURST = {
    "identity": "the unit never announces its identity. It reaches the cache "
                "because connect() READs Version before the handshake and the "
                "state layer is already listening (ADR-0020), which is not the "
                "burst and does not arrive with it",
}


def test_burst_tail_names_every_entry_the_burst_is_expected_to_warm(
        recorder_class):
    """The drift that would re-open the race, caught offline.

    ``BURST_TAIL`` decides when the fixture stops recording and snapshots
    ``burst_warmed``; ``entries.ENTRIES`` decides what that snapshot contains.
    They are two hand-written lists about one moment on the wire, and nothing
    held them together - so an entry added with a new burst message would be
    snapshotted before its message had arrived, exactly the way ``dirty`` and
    ``scene`` were. It would fail one run in however-many, with a message
    blaming the unit.

    So: every entry is either fed by something in ``BURST_TAIL``, or named above
    with the reason it is not. Deciding which is a judgement about the wire, and
    this test is where a new entry has to state it.
    """
    tail = set(recorder_class.BURST_TAIL)
    for entry in entries.ENTRIES:
        # PER FEED TYPE, not per entry. An entry with one foot in BURST_TAIL and
        # one in a message that arrives after the group would pass an
        # any-of-its-feeds check while re-opening the race exactly as before:
        # the recorder settles on the tail, the snapshot is taken, and the other
        # message is still in flight.
        for message_type in sorted(entry.feeds, key=lambda t: t.__name__):
            name = message_type.__name__
            if name in tail:
                continue
            excuse = OUTSIDE_THE_BURST.get(name) or NOT_WARMED_BY_THE_BURST.get(
                entry.name)
            assert excuse, (
                f"entry {entry.name!r} is fed by {name}, which is neither in "
                f"BURST_TAIL nor excused. If the burst carries it, it belongs "
                f"in BURST_TAIL so the recorder waits for it; if it arrives at "
                f"some other time, say when in OUTSIDE_THE_BURST; if the burst "
                f"does not warm this entry at all, say so in "
                f"NOT_WARMED_BY_THE_BURST.")
        warmed_by = {t.__name__ for t in entry.feeds} & tail
        excused = NOT_WARMED_BY_THE_BURST.get(entry.name)
        assert bool(warmed_by) != bool(excused), (
            f"entry {entry.name!r} is warmed by {sorted(warmed_by)} and excused "
            f"with {excused!r} - it must be one or the other.")


def test_every_burst_tail_message_actually_feeds_an_entry(recorder_class):
    """The other direction: a name in ``BURST_TAIL`` nothing reads is a wait
    the fixture pays on every run for no reason, and it would hold the whole
    suite for the full patience if the unit ever stopped sending it."""
    fed = {t.__name__ for entry in entries.ENTRIES for t in entry.feeds}
    assert set(recorder_class.BURST_TAIL) <= fed, (
        f"BURST_TAIL waits for {sorted(set(recorder_class.BURST_TAIL) - fed)}, "
        f"which feeds no cache entry")


def test_a_recorder_that_never_recorded_says_so(recorder_class):
    """The branch that tells "never started" apart from "timed out".

    Unreachable from the fixture, which always calls ``record_until`` - so
    without this the one line that would name a fixture that stopped calling it
    is itself unverified.
    """
    burst = recorder_class()
    burst.attach(FakeTransport())
    assert burst.unfinished() == (
        "the burst was never recorded: record_until was not called")


def test_record_until_refuses_an_empty_collection(recorder_class):
    """Nothing to wait for is satisfied immediately, which reads as success.

    The recorder would come off the transport before the burst began and report
    a finished burst, and every guard added for this race would pass while the
    snapshot held nothing.
    """
    transport = FakeTransport()
    burst = recorder_class()
    burst.attach(transport)
    with pytest.raises(ValueError, match="at least one type name"):
        burst.record_until((), patience=5.0)
    assert burst.settled_in is None, "it settled on an empty condition"
    assert transport.listeners == [], "a refused call left the recorder attached"


def test_the_burst_excuse_lists_name_nothing_that_has_gone_away(recorder_class):
    """A stale excuse is an entry nobody is checking any more.

    Both lists above are opt-outs, so a key that no longer matches anything is
    an exemption still being granted to something renamed or deleted - which is
    how a list like this stops being the gate it was written as.
    """
    assert set(NOT_WARMED_BY_THE_BURST) <= {e.name for e in entries.ENTRIES}, (
        f"NOT_WARMED_BY_THE_BURST excuses entries that no longer exist: "
        f"{sorted(set(NOT_WARMED_BY_THE_BURST) - {e.name for e in entries.ENTRIES})}")
    fed = {t.__name__ for entry in entries.ENTRIES for t in entry.feeds}
    assert set(OUTSIDE_THE_BURST) <= fed, (
        f"OUTSIDE_THE_BURST names message types that feed nothing: "
        f"{sorted(set(OUTSIDE_THE_BURST) - fed)}")


def test_a_tail_that_lands_during_the_last_sleep_still_settles(recorder_class):
    """Patience expiring is a claim about the unit, so it must not be a race.

    The loop used to test its deadline before its condition, so a tail arriving
    in the final 100 ms was recorded in full and then reported as a burst that
    never finished - with a blank where the missing names go, because by then
    nothing was missing. The whole point of the sentence is to be read.
    """
    transport = FakeTransport()
    burst = recorder_class()
    burst.attach(transport)

    def push_the_tail():
        time.sleep(0.25)               # after the last poll, before the deadline
        for message in _tail():
            burst(message)

    feeder = threading.Thread(target=push_the_tail)
    feeder.start()
    burst.record_until(recorder_class.BURST_TAIL, patience=0.3)
    feeder.join()

    assert burst.settled_in is not None, (
        f"a complete recording was reported as cut off: {burst.unfinished()}")
    assert burst.unfinished() is None
    assert set(burst.names()) == set(recorder_class.BURST_TAIL)


def test_an_unfinished_recording_never_names_nothing(recorder_class):
    """The blank the check above prevents, asserted directly.

    A sentence that says "-  never arrived" sends the reader looking for a
    recorder bug the recorder's own state has already disproved.
    """
    transport = FakeTransport()
    burst = recorder_class()
    burst.attach(transport)
    first, *rest = _tail()
    burst(first)

    burst.record_until(recorder_class.BURST_TAIL, patience=0.15)
    for message in rest:              # arrives after the give-up, as on a slow unit
        burst(message)

    unfinished = burst.unfinished()
    assert "never arrived" in unfinished
    named = unfinished.split("s: ")[1].split(" never arrived")[0]
    assert named.strip(), f"it named nothing missing: {unfinished!r}"
    assert "SceneMessage" in named, unfinished


def test_record_until_refuses_message_classes(recorder_class):
    """Names, not classes. A class is neither a str nor empty, so it passes both
    other refusals, matches nothing the recorder stores, and comes back as a
    message the unit supposedly never sent."""
    transport = FakeTransport()
    burst = recorder_class()
    burst.attach(transport)
    with pytest.raises(TypeError, match="NAMES"):
        burst.record_until({pa.PresetDirtyMessage}, patience=5.0)
    assert transport.listeners == []


def test_a_refused_call_does_not_claim_it_was_never_made(recorder_class):
    """"record_until was not called" is a different fault from "it was refused",
    and the caller who catches the refusal is the one reading this."""
    burst = recorder_class()
    burst.attach(FakeTransport())
    with pytest.raises(ValueError):
        burst.record_until((), patience=5.0)
    assert "refused its argument" in burst.unfinished()


def test_tail_positions_says_where_the_closing_group_landed(recorder_class):
    """The signal for the thing the stop condition cannot prove: that the four
    are the LAST four. A fifth closing message would push them back from the
    end, and this is what would show it."""
    transport = FakeTransport()
    burst = recorder_class()
    burst.attach(transport)
    for _ in range(5):
        burst(_file_push())
    for message in _tail():
        burst(message)
    burst.record_until(recorder_class.BURST_TAIL, patience=1.0)

    # Counted from the end: Scene arrived last, so it sits at 1.
    assert burst.tail_positions() == {
        "RecallPresetMessage": 4, "SetlistPositionMessage": 3,
        "PresetDirtyMessage": 2, "SceneMessage": 1}
