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
  from the outside - the last of those about fourteen runs in fifteen.

Timing is real here rather than faked, so the code under test is the code that
runs on the unit.
"""
import importlib.util
import threading
import time
from pathlib import Path

import pytest

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
    assert burst.names() == ["FileMessage"] * 3 + list(recorder_class.BURST_TAIL)


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

    def push():
        for _ in range(200):
            burst(pa.FileMessage(action=pa.MessageAction.UPDATE))

    writers = [threading.Thread(target=push) for _ in range(4)]
    for writer in writers:
        writer.start()
    while any(writer.is_alive() for writer in writers):
        assert all(name == "FileMessage" for name in burst.names())
    for writer in writers:
        writer.join()

    assert len(burst.names()) == 800


def test_record_until_waits_for_the_whole_tail_not_just_the_first_of_it(
        recorder_class):
    """The race that failed two hardware tests together, one run in fifteen.

    The burst's four closing messages arrive in the order ``RecallPreset``,
    ``SetlistPosition``, ``PresetDirty``, ``Scene``, inside six milliseconds -
    re-measured 2026-09-14 on d14e over three sessions, spreads of 3.6, 5.8 and
    6.0 ms (``docs/protocol.md``, "Connect burst, measured"). ``RecallPreset`` is
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

    ``frozenset("RecallPresetMessage")`` is twelve letters, so passing the old
    argument to the new signature would wait for message types called ``R``,
    ``e``, ``c`` and never settle - a thirty-second timeout per run and a
    recording of nothing, arriving as a puzzle rather than as a mistake.
    """
    burst = recorder_class()
    burst.attach(FakeTransport())
    with pytest.raises(TypeError, match="collection"):
        burst.record_until("RecallPresetMessage", patience=0.1)
