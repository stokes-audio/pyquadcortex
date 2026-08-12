"""The hardware suite's connect-burst recorder, checked offline.

``tests/hardware/conftest.py`` attaches a listener before the handshake, records
what the unit pushes, and stops once the burst is over. Stopping is what makes the
recording mean "the burst" rather than "the traffic so far", and it has three
parts: recording stops, the listener comes off the transport, and a message that
arrives after both - the RX thread notifies from a snapshot - does not reopen it.

The hardware suite cannot check any of that. It reads the recording after the
fixture has closed it and has no way to tell "closed correctly" from "closed and
then quietly kept recording"; the assertions are floors, which contamination
satisfies too. So if the close broke, the burst test would go back to asserting on
whatever the rest of the suite provoked and would still pass. That is the trap
``tests/test_scene_echo_predicates.py`` was written for.

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

    Loaded by path under its own module name: the hardware conftest is not
    collected at all without ``--hardware``, so there is no other way to reach it
    from the offline suite, and pytest's own copy is untouched by this.
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


def test_record_until_stops_when_the_sentinel_arrives(recorder_class):
    transport = FakeTransport()
    burst = recorder_class()
    burst.attach(transport)

    def push_the_burst():
        for _ in range(3):
            burst(_file_push())
        burst(pa.RecallPresetMessage(action=pa.MessageAction.UPDATE))

    feeder = threading.Timer(0.1, push_the_burst)
    feeder.start()
    started = time.monotonic()
    burst.record_until("RecallPresetMessage", patience=5.0)
    took = time.monotonic() - started
    feeder.join()

    assert took < 5.0, "it waited out its patience instead of noticing the sentinel"
    assert burst.settled_in is not None
    assert burst.closed
    assert burst.names() == ["FileMessage"] * 3 + ["RecallPresetMessage"]


def test_record_until_gives_up_rather_than_hanging_on_a_silent_unit(recorder_class):
    # A unit that never sends the sentinel must not hold the whole run. The
    # give-up is reported rather than swallowed, so the hardware test can say the
    # burst was cut off instead of asserting on half of it.
    transport = FakeTransport()
    burst = recorder_class()
    burst.attach(transport)
    burst(_file_push())

    burst.record_until("RecallPresetMessage", patience=0.2)

    assert burst.settled_in is None, "it reported settling on a sentinel it never saw"
    assert burst.closed
    assert transport.listeners == []


def test_the_recorder_is_safe_to_call_from_more_than_one_thread(recorder_class):
    # It runs on the RX thread while the test thread reads names(). Nothing here
    # is subtle; the point is that the lock covers both sides.
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
