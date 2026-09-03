"""The cache on a real RX thread, over a real ``Transport`` and a fake HID link.

``tests/test_state.py`` drives the cache through a loopback double, which is
where the merge rules belong: they are about message content, and a double keeps
them readable. This file exists for the rules a double cannot test at all,
because they are about a THREAD:

    The RX thread applies pushes and notes what needs re-reading; the caller's
    thread does the re-reading. The RX thread still cannot block or die.

A cache that re-read from its listener would pass every content test and would
stall the whole connection on hardware for the length of one timeout - a failure
that looks like success, which is the expensive kind (ADR-0009).

No hardware and no ``hid``: the fake below is the unit's side of the HID link,
built with the real ``framing`` so the transport's reassembly path runs for real.
"""
import collections
import threading
import time

import pytest

from pyquadcortex.device import state
from pyquadcortex.protocol import client as protocol_client
from pyquadcortex.protocol import framing, registry, transport
from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa

#: High enough that the transport's keepalive never lands mid-test.
QUIET_KEEPALIVE = 3600.0
PATIENCE = 2.0


class FakeUnit:
    """The unit's side of the link: answers a Version READ, pushes on demand.

    Only Version is answered, because it is the only thing the cache reads over
    this link. Anything else the host writes is counted and dropped, which is
    what makes ``asked_for`` a usable assertion about reads the model issued.
    """

    def __init__(self):
        self._inbox = collections.deque()
        self._lock = threading.Lock()
        self.asked_for = collections.Counter()

    def write(self, report):
        report = bytes(report)
        frame = framing.decode_reports([report])
        message_type, payload = frame.message_type, frame.payload
        message_class = registry.class_for(message_type)
        message = message_class()
        message.ParseFromString(payload)
        with self._lock:
            self.asked_for[message_class.__name__] += 1
        if message_class is pa.VersionMessage:
            reply = pa.VersionMessage(action=pa.MessageAction.UPDATE,
                                      app_fw_version="d14e",
                                      device_serial_number="QCS0000001")
            if message.HasField("request_id"):
                reply.request_id = message.request_id
            self.push(reply)
        return len(report)

    def push(self, message):
        """Queue ``message`` for the transport's read loop to pick up."""
        reports = framing.encode_message(registry.type_for(type(message)),
                                         message.SerializeToString())
        with self._lock:
            for report in reports:
                # encode_message stamps the host->device report id; real input
                # reports carry the device->host one. Same restamp as
                # tests/test_transport.py's FakeHid.
                self._inbox.append(bytes([framing.IN_REPORT_ID])
                                   + bytes(report)[1:])

    def read(self, size, timeout=0):
        with self._lock:
            if self._inbox:
                return list(self._inbox.popleft())
        time.sleep(0.005)          # a blocking read that times out, unspun
        return []

    def close(self):
        pass


def _wait_until(predicate, timeout=PATIENCE):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


@pytest.fixture
def live():
    """A cache listening on a started transport, over the real client."""
    unit = FakeUnit()
    link = transport.Transport(unit, keepalive_interval=QUIET_KEEPALIVE)
    link.start()
    cache = state.DeviceState()
    cache.listen_on(link)
    cache.bind(protocol_client.QuadCortex(link))
    try:
        yield unit, link, cache
    finally:
        cache.close()
        link.stop()


def test_the_rx_thread_applies_a_push_and_asks_the_unit_for_nothing(live):
    unit, link, cache = live

    unit.push(pa.PresetDirtyMessage(action=pa.MessageAction.UPDATE,
                                    is_dirty=True))

    assert _wait_until(lambda: cache.cached("dirty").get("is_dirty") is True)
    assert unit.asked_for["PresetDirtyMessage"] == 0
    assert cache.value("dirty", "is_dirty") is True
    assert unit.asked_for["PresetDirtyMessage"] == 0


def test_a_push_that_forces_a_reread_does_not_read_on_the_rx_thread(live):
    """The rule, stated as a measurement: the mark appears, the read does not."""
    unit, link, cache = live
    assert cache.value("identity", "app_fw_version") == "d14e"
    assert unit.asked_for["VersionMessage"] == 1

    unit.push(pa.VersionMessage(action=pa.MessageAction.UPDATE,
                                uboot_version="2019.04"))

    assert _wait_until(lambda: cache.needs_read("identity"))
    time.sleep(0.2)                      # long enough for a listener to have read
    assert unit.asked_for["VersionMessage"] == 1, (
        "the re-read was issued from the RX thread - on hardware that stalls "
        "the read loop for its whole timeout and can never be satisfied")

    assert cache.value("identity", "app_fw_version") == "d14e"
    assert unit.asked_for["VersionMessage"] == 2, "the caller's thread re-reads"


def test_the_read_loop_still_runs_after_a_push_that_forced_a_reread(live):
    """"The RX thread never dies" is absolute, marks included."""
    unit, link, cache = live
    unit.push(pa.VersionMessage(action=pa.MessageAction.UPDATE,
                                uboot_version="2019.04"))
    assert _wait_until(lambda: cache.needs_read("identity"))

    assert cache.value("identity", "device_serial_number") == "QCS0000001"
    assert link.device_lost is None


def test_the_read_loop_survives_a_push_the_cache_chokes_on(live, monkeypatch):
    """A bug in the cache costs one message, not the connection.

    Forced rather than waited for: an exception from the listener is the case
    the RX path's guarantee is written for, and there is no way to provoke it
    from message content without a bug to provoke it with.
    """
    unit, link, cache = live

    def explode(*args, **kwargs):
        raise ValueError("a bug in the cache")

    monkeypatch.setattr(state, "fields_applied", explode)
    unit.push(pa.PresetDirtyMessage(action=pa.MessageAction.UPDATE,
                                    is_dirty=True))
    time.sleep(0.1)

    monkeypatch.undo()
    assert cache.value("identity", "app_fw_version") == "d14e"
    assert link.device_lost is None


def test_reading_the_cache_from_a_listener_is_refused_not_hung(live):
    """The other half of ADR-0009's bargain, on the cache's own read path.

    A future listener that wants a value it did not receive marks it for
    re-reading; it does not fetch it. This is what happens if it tries.
    """
    unit, link, cache = live
    tried = threading.Event()
    refusal = {}

    def reads_from_the_rx_thread(message):
        if tried.is_set():
            return
        tried.set()
        try:
            cache.value("identity", "app_fw_version")
            refusal["error"] = None
        except BaseException as exc:            # noqa: BLE001 - the type is the point
            refusal["error"] = exc

    detach = link.add_listener(reads_from_the_rx_thread)
    try:
        unit.push(pa.PresetDirtyMessage(action=pa.MessageAction.UPDATE,
                                        is_dirty=True))
        assert tried.wait(PATIENCE), "no push arrived, so the listener never ran"
        assert _wait_until(lambda: "error" in refusal)
    finally:
        detach()

    error = refusal["error"]
    assert isinstance(error, RuntimeError), f"not refused: {error!r}"
    assert not isinstance(error, TimeoutError), "it waited instead of refusing"
    assert "RX thread" in str(error)
    # And the link is unharmed: the refusal cost one listener call.
    assert cache.value("identity", "app_fw_version") == "d14e"
