"""Tests for the framed HID transport (pyquadcortex.protocol.transport).

These use an in-memory fake HID device - NO real hardware. The fake mimics the
subset of the hidapi ``hid.Device`` interface the transport relies on:

  * ``write(report) -> int`` where ``report`` is a full 129-byte output report.
  * ``read(size, timeout) -> list[int]`` returning one input report as a list of
    ints (report-ID byte first), or ``[]`` on timeout.
  * ``close()``.

The fake responds to ``VersionMessage`` requests by echoing back a
``VersionMessage`` carrying the same ``request_id`` (built with ``framing`` so
the transport's reassembly path is exercised for real). Because the transport
registers the pending request_id BEFORE it writes, having ``write`` enqueue the
response is race-free: the waiter is always registered before the reply lands.
"""

import collections
import contextlib
import gzip
import itertools
import json
import logging
import os
import pathlib
import struct
import subprocess
import sys
import textwrap
import threading
import time
import zlib

import pytest

from pyquadcortex.protocol import framing, registry, transport
from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa

# Short timeouts keep the suite fast and prevent hangs if something misbehaves.
REQUEST_TIMEOUT = 2.0
# A keepalive interval large enough that keepalives never interfere with the
# request/response tests.
QUIET_KEEPALIVE = 3600.0


class FakeHid:
    """In-memory stand-in for an hidapi ``hid.Device``.

    On ``write`` of a ``VersionMessage`` it enqueues a framed ``VersionMessage``
    response echoing the request's ``request_id``. ``kernel_version`` lets a test
    make that response large enough to span multiple input reports. ``read``
    hands out one buffered report per call (FIFO), returning ``[]`` when empty.
    """

    def __init__(self, kernel_version=""):
        self._inbox = collections.deque()
        self._lock = threading.Lock()
        self._kernel_version = kernel_version
        self.writes = []  # raw bytes of every report written (for assertions)

    def write(self, report):
        report = bytes(report)
        self.writes.append(report)
        frame = framing.decode_reports([report])
        if registry.class_for(frame.message_type) is pa.VersionMessage:
            req = pa.VersionMessage()
            req.ParseFromString(frame.payload)
            resp = pa.VersionMessage(action=pa.MessageAction.READ)
            if req.HasField("request_id"):
                resp.request_id = req.request_id
            if self._kernel_version:
                resp.linux_kernel_version = self._kernel_version
            reports = framing.encode_message(
                registry.type_for(pa.VersionMessage), resp.SerializeToString()
            )
            self.inject(*reports)
        return len(report)

    def inject(self, *reports):
        """Append raw reports to the read queue (used to simulate device input).

        Real device->host INPUT reports are prefixed with framing.IN_REPORT_ID
        (0x01), but framing.encode_message stamps framing.OUT_REPORT_ID (0x02)
        because it frames host->device OUTPUT reports. We restamp the leading id
        byte to IN_REPORT_ID so the fake mirrors real input reports on the wire.
        This round-trips only because framing.decode_reports ignores the id byte;
        if decode is ever hardened to assert the report id, both this fake and
        the transport's read path would need updating.
        """
        with self._lock:
            for r in reports:
                r = bytes(r)
                self._inbox.append(bytes([framing.IN_REPORT_ID]) + r[1:])

    def pending_reads(self):
        """Number of reports still queued to be read (thread-safe)."""
        with self._lock:
            return len(self._inbox)

    def read(self, size, timeout=0):
        with self._lock:
            if self._inbox:
                return list(self._inbox.popleft())
        # Nothing buffered: emulate a blocking read that times out, without
        # busy-spinning the RX thread.
        time.sleep(0.005)
        return []

    def close(self):
        pass


def _wait_until(predicate, timeout=REQUEST_TIMEOUT):
    """Poll ``predicate`` until true or ``timeout`` elapses; return its result."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def test_request_response_round_trip():
    fake = FakeHid()
    t = transport.Transport(fake, keepalive_interval=QUIET_KEEPALIVE)
    t.start()
    try:
        resp = t.request(
            pa.VersionMessage(action=pa.MessageAction.READ), timeout=REQUEST_TIMEOUT
        )
    finally:
        t.stop()
    assert isinstance(resp, pa.VersionMessage)
    assert resp.HasField("request_id")
    assert resp.request_id == 1  # first id from itertools.count(1)


def test_request_match_ignores_a_same_type_echo_and_waits_for_full_reply():
    class EchoThenFull(FakeHid):
        def write(self, report):
            report = bytes(report)
            self.writes.append(report)
            frame = framing.decode_reports([report])
            request = pa.VersionMessage()
            request.ParseFromString(frame.payload)
            echo = pa.VersionMessage(action=pa.MessageAction.READ)
            full = pa.VersionMessage(
                action=pa.MessageAction.UPDATE,
                device_serial_number="QCS0000001",
            )
            for response in (echo, full):
                self.inject(*framing.encode_message(
                    registry.type_for(pa.VersionMessage),
                    response.SerializeToString(),
                ))
            return len(report)

    fake = EchoThenFull()
    t = transport.Transport(fake, keepalive_interval=QUIET_KEEPALIVE)
    t.start()
    try:
        reply = t.request(
            pa.VersionMessage(action=pa.MessageAction.READ),
            timeout=REQUEST_TIMEOUT,
            match=lambda message: message.HasField("device_serial_number"),
        )
    finally:
        t.stop()

    assert reply.device_serial_number == "QCS0000001"


def test_multi_report_reassembly():
    # A 300-char kernel-version string makes the response payload > 128 bytes so
    # it spans multiple input reports; the transport must reassemble them.
    kernel = "k" * 300
    fake = FakeHid(kernel_version=kernel)

    # Sanity-check the fixture actually forces multiple reports.
    probe = pa.VersionMessage(action=pa.MessageAction.READ)
    probe.linux_kernel_version = kernel
    probe_reports = framing.encode_message(
        registry.type_for(pa.VersionMessage), probe.SerializeToString()
    )
    assert len(probe_reports) > 1

    t = transport.Transport(fake, keepalive_interval=QUIET_KEEPALIVE)
    t.start()
    try:
        resp = t.request(
            pa.VersionMessage(action=pa.MessageAction.READ), timeout=REQUEST_TIMEOUT
        )
    finally:
        t.stop()
    assert isinstance(resp, pa.VersionMessage)
    assert resp.linux_kernel_version == kernel


def test_unknown_message_type_does_not_crash_rx():
    fake = FakeHid()
    t = transport.Transport(fake, keepalive_interval=QUIET_KEEPALIVE)
    t.start()
    try:
        # Inject a well-framed report whose message-type tag is not in the
        # registry. The RX thread must skip it (KeyError from registry) and keep
        # running rather than dying.
        unknown_type = 60000  # not a registered CortexMessageType
        for report in framing.encode_message(unknown_type, b"\x08\x03"):
            fake.inject(report)

        # Wait until the RX loop has consumed and discarded the bogus frame, so
        # the request below genuinely follows a survived decode error.
        assert _wait_until(lambda: fake.pending_reads() == 0)

        # A subsequent valid request must still round-trip, proving the RX
        # thread survived the bad frame.
        resp = t.request(
            pa.VersionMessage(action=pa.MessageAction.READ), timeout=REQUEST_TIMEOUT
        )
    finally:
        t.stop()
    assert isinstance(resp, pa.VersionMessage)
    assert resp.request_id == 1


def test_reassembly_buffer_is_capped_so_a_lost_frame_cannot_wedge_rx(monkeypatch):
    # Shrink the cap so the test triggers it with a handful of reports instead of
    # the ~500 a real uint16 payload_len would require. The RX loop reads the
    # module global each iteration, so patching it takes effect on the fly.
    monkeypatch.setattr(transport, "_MAX_REPORTS_PER_MESSAGE", 3)

    fake = FakeHid()
    t = transport.Transport(fake, keepalive_interval=QUIET_KEEPALIVE)
    t.start()
    try:
        # A first report declares a payload far larger than the reports we feed,
        # so is_complete never returns True - simulating a lost continuation
        # frame that would otherwise grow the buffer without bound. Feeding
        # cap + 1 such reports must trip the cap and reset the buffer (empty).
        big_len = 4000  # in-range uint16, needs far more than cap+1 reports
        header = struct.pack("<HH", registry.type_for(pa.VersionMessage), big_len)
        first_body = header + bytes(framing.REPORT_SIZE - len(header))
        first_report = bytes([framing.IN_REPORT_ID]) + first_body
        cont_report = bytes([framing.IN_REPORT_ID]) + bytes(framing.REPORT_SIZE)

        fake.inject(first_report)
        for _ in range(transport._MAX_REPORTS_PER_MESSAGE):
            fake.inject(cont_report)
        assert _wait_until(lambda: fake.pending_reads() == 0)

        # If the buffer had NOT reset, this response would be appended to the
        # wedged buffer (whose stale header demands 4000 bytes) and never
        # dispatched, so request() would time out. Round-tripping proves the cap
        # reset the buffer and the RX thread survived.
        resp = t.request(
            pa.VersionMessage(action=pa.MessageAction.READ), timeout=REQUEST_TIMEOUT
        )
    finally:
        t.stop()
    assert isinstance(resp, pa.VersionMessage)


def test_request_times_out_when_no_response():
    # A device that never answers must yield a clean TimeoutError, not a hang.
    class SilentHid:
        def write(self, report):
            return len(report)

        def read(self, size, timeout=0):
            time.sleep(0.005)
            return []

        def close(self):
            pass

    t = transport.Transport(SilentHid(), keepalive_interval=QUIET_KEEPALIVE)
    t.start()
    try:
        with pytest.raises(TimeoutError):
            t.request(pa.VersionMessage(action=pa.MessageAction.READ), timeout=0.2)
    finally:
        t.stop()


def test_keepalive_is_sent_periodically():
    # With a tiny interval, the keepalive thread should emit KeepAlive writes.
    fake = FakeHid()
    t = transport.Transport(fake, keepalive_interval=0.02)
    t.start()
    try:
        def saw_keepalive():
            for report in list(fake.writes):
                frame = framing.decode_reports([report])
                if registry.class_for(frame.message_type) is pa.KeepAliveMessage:
                    return True
            return False

        assert _wait_until(saw_keepalive, timeout=REQUEST_TIMEOUT)
    finally:
        t.stop()


def test_stop_is_idempotent_and_does_not_hang():
    fake = FakeHid()
    t = transport.Transport(fake, keepalive_interval=QUIET_KEEPALIVE)
    t.start()
    t.stop()
    t.stop()  # second stop must be harmless


class RecordingHid:
    """Records the calling thread of each write, in write order.

    A small per-write delay widens the window for interleaving so that, WITHOUT
    the transport's write lock, concurrent multi-report sends would interleave
    their reports (making the atomicity assertion below fail). WITH the write
    lock, each send's reports are always contiguous regardless of the delay, so
    the test passes deterministically - the delay only affects speed, never the
    invariant.
    """

    def __init__(self, delay=0.001):
        self._delay = delay
        self.write_threads = []  # thread ident per write, in order
        self._record_lock = threading.Lock()

    def write(self, report):
        if self._delay:
            time.sleep(self._delay)
        with self._record_lock:
            self.write_threads.append(threading.get_ident())
        return len(report)

    def read(self, size, timeout=0):
        time.sleep(0.005)
        return []

    def close(self):
        pass


def test_send_writes_each_message_as_an_atomic_group():
    # A long string forces each message to span multiple reports.
    kernel = "k" * 300
    reports_per_msg = len(
        framing.encode_message(
            registry.type_for(pa.VersionMessage),
            _version_read(kernel).SerializeToString(),
        )
    )
    assert reports_per_msg > 1  # otherwise this test proves nothing

    dev = RecordingHid()
    # No start(): we exercise send() concurrency only, with no RX/keepalive noise.
    t = transport.Transport(dev, keepalive_interval=QUIET_KEEPALIVE)

    num_threads = 6

    def worker():
        t.send(_version_read(kernel))

    threads = [threading.Thread(target=worker) for _ in range(num_threads)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    seq = dev.write_threads
    assert len(seq) == num_threads * reports_per_msg
    # With atomic per-message writes, each thread's reports form one contiguous
    # run, so there are exactly num_threads runs, each reports_per_msg long.
    run_lengths = [len(list(group)) for _, group in itertools.groupby(seq)]
    assert len(run_lengths) == num_threads
    assert all(length == reports_per_msg for length in run_lengths)


def _version_read(kernel_version):
    msg = pa.VersionMessage(action=pa.MessageAction.READ)
    msg.linux_kernel_version = kernel_version
    return msg


def _recall_broadcast(name, rid=None):
    """Build framed reports for a RecallPreset broadcast (optionally with id)."""
    from pyquadcortex.protocol.proto import Preset_pb2 as preset

    rp = pa.RecallPresetMessage(
        action=pa.MessageAction.UPDATE, preset=preset.BinaryPreset(name=name)
    )
    if rid is not None:
        rp.request_id = rid
    return framing.encode_message(
        registry.type_for(pa.RecallPresetMessage), rp.SerializeToString()
    )


def test_await_broadcast_matches_by_predicate_skipping_nonmatching():
    # The device pushes a stale RecallPreset with no request_id (e.g. the
    # hello-subscription grid state) then the one echoing our recall's id. A
    # match predicate must skip the stale push and return only the matching one.
    fake = FakeHid()
    t = transport.Transport(fake, keepalive_interval=QUIET_KEEPALIVE)
    t.start()

    def trigger():
        fake.inject(*_recall_broadcast("stale-seed", rid=None))
        fake.inject(*_recall_broadcast("mine", rid=77))

    try:
        got = t.await_broadcast(
            pa.RecallPresetMessage,
            trigger,
            timeout=REQUEST_TIMEOUT,
            match=lambda m: m.HasField("request_id") and m.request_id == 77,
        )
    finally:
        t.stop()
    assert got.preset.name == "mine"
    assert got.request_id == 77


def test_next_request_id_is_monotonic_and_shared_with_request():
    fake = FakeHid()
    t = transport.Transport(fake, keepalive_interval=QUIET_KEEPALIVE)
    a = t.next_request_id()
    b = t.next_request_id()
    assert b == a + 1


# -- logging hygiene ----------------------------------------------------------


def test_library_does_not_print_to_the_console_by_default(capsys):
    """A library must stay silent unless the application configures logging.

    Without a NullHandler on the package logger, Python's handler of last resort
    prints WARNING and above straight to stderr, so an internal transport message
    would surface uninvited in a caller's output.
    """
    import logging

    import pyquadcortex  # noqa: F401  (import installs the NullHandler)

    pkg_logger = logging.getLogger("pyquadcortex")
    assert any(isinstance(h, logging.NullHandler) for h in pkg_logger.handlers), \
        "the pyquadcortex logger needs a NullHandler"

    capsys.readouterr()  # discard anything captured so far
    logging.getLogger("pyquadcortex.protocol.transport").warning("should not reach the console")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_collect_gathers_every_matching_push_without_consuming_them():
    # One request can provoke hundreds of pushes (a File READ enumerates the
    # device's whole folder tree), so collect() accumulates rather than taking
    # the first, and leaves messages available to waiters.
    #
    # A real Transport, never started: the trigger dispatches on the calling
    # thread, so no RX thread is needed and none of the transport's state has to
    # be faked.
    t = transport.Transport(FakeHid(), keepalive_interval=QUIET_KEEPALIVE)

    def trigger():
        for i in range(3):
            m = pa.FileMessage(action=pa.MessageAction.UPDATE)
            m.folder.key = f"k{i}"
            t._dispatch(m)
        other = pa.SceneMessage(action=pa.MessageAction.UPDATE, selected_scene=1)
        t._dispatch(other)

    got = t.collect(pa.FileMessage, trigger, 0.2,
                    match=lambda m: m.folder.key != "k1")
    assert [m.folder.key for m in got] == ["k0", "k2"]
    assert t._collectors == [], "the collector is removed when done"


# -- persistent listeners ------------------------------------------------------
# add_listener is the only inbound hook that is not scoped to one trigger or one
# reply, so what these tests protect is mostly what it must NOT do: consume a
# message, block the read loop, or take its peers down with it.


def test_a_listener_sees_an_unsolicited_push_no_waiter_wanted():
    # The case the other three hooks cannot serve: a push nobody asked for. With
    # no listener this message reaches _dispatch, matches nothing, and is dropped
    # at debug level.
    fake = FakeHid()
    t = transport.Transport(fake, keepalive_interval=QUIET_KEEPALIVE)
    seen = []
    t.start()
    try:
        t.add_listener(seen.append)
        fake.inject(*_recall_broadcast("unsolicited", rid=None))
        assert _wait_until(lambda: len(seen) == 1), "the push never reached the listener"
    finally:
        t.stop()
    assert isinstance(seen[0], pa.RecallPresetMessage)
    assert seen[0].preset.name == "unsolicited"


def test_a_listener_does_not_steal_a_message_from_its_waiter():
    # A listener is additive: the reply still lands in request()'s hands.
    fake = FakeHid()
    t = transport.Transport(fake, keepalive_interval=QUIET_KEEPALIVE)
    seen = []
    t.start()
    try:
        t.add_listener(seen.append)
        resp = t.request(
            pa.VersionMessage(action=pa.MessageAction.READ), timeout=REQUEST_TIMEOUT
        )
    finally:
        t.stop()
    assert resp.request_id == 1, "the waiter did not get its reply"
    assert [type(m) for m in seen] == [pa.VersionMessage]
    assert seen[0] is resp, "the listener and the waiter get the same message"


def test_a_listener_has_already_run_when_the_blocked_caller_wakes():
    # The ordering a push-fed cache depends on: listeners are notified before the
    # waiter's event is set, so a cache fed by a listener is current by the time
    # the caller that provoked the reply gets it back. No sleeps - if the order
    # were the other way round, the list would still be empty here.
    fake = FakeHid()
    t = transport.Transport(fake, keepalive_interval=QUIET_KEEPALIVE)
    seen = []
    t.start()
    try:
        t.add_listener(seen.append)
        t.request(
            pa.VersionMessage(action=pa.MessageAction.READ), timeout=REQUEST_TIMEOUT
        )
        assert len(seen) == 1, "the caller woke before the listener had the message"
    finally:
        t.stop()


def test_a_raising_listener_costs_nobody_else_the_message(caplog):
    # Wrap and log, like every other decode step in the RX path: the peers still
    # see the message, the waiter still gets its reply, and the read loop is still
    # running afterwards.
    fake = FakeHid()
    t = transport.Transport(fake, keepalive_interval=QUIET_KEEPALIVE)
    before, after = [], []

    def explodes(message):
        raise RuntimeError("a listener with a bug in it")

    t.start()
    try:
        t.add_listener(before.append)
        t.add_listener(explodes)
        t.add_listener(after.append)
        with caplog.at_level("ERROR", logger="pyquadcortex.protocol.transport"):
            first = t.request(
                pa.VersionMessage(action=pa.MessageAction.READ),
                timeout=REQUEST_TIMEOUT,
            )
            # A second round trip through the same RX thread: had the raise killed
            # it, this would time out rather than answer.
            second = t.request(
                pa.VersionMessage(action=pa.MessageAction.READ),
                timeout=REQUEST_TIMEOUT,
            )
    finally:
        t.stop()
    assert first is not None and second is not None
    assert len(before) == 2, "a listener registered before the raiser lost a message"
    assert len(after) == 2, "a listener registered after the raiser lost a message"
    assert "a listener with a bug in it" in caplog.text, \
        "a raising listener must be logged, not silently swallowed"


def test_a_listener_raising_outside_exception_still_cannot_kill_the_rx_thread():
    # The reason this is not covered by the test above: a listener is arbitrary
    # caller code, and two ordinary things it might do - pytest.fail() and
    # sys.exit() - raise BaseException subclasses, which a plain `except
    # Exception` lets through. That kills the read loop, and what the caller then
    # sees is a TimeoutError with device_lost unset: the connection is dead and
    # nothing says why. SystemExit stands in for both here.
    fake = FakeHid()
    t = transport.Transport(fake, keepalive_interval=QUIET_KEEPALIVE)
    after = []

    def bails_out(message):
        raise SystemExit("a listener that called sys.exit()")

    t.start()
    try:
        t.add_listener(bails_out)
        t.add_listener(after.append)
        fake.inject(*_recall_broadcast("push", rid=None))
        assert _wait_until(lambda: after), "the peer listener lost the message"
        assert t._rx.is_alive(), "the RX thread died"
        assert t.request(pa.VersionMessage(action=pa.MessageAction.READ),
                         timeout=REQUEST_TIMEOUT) is not None
    finally:
        t.stop()


def test_a_listener_cannot_read_from_the_device_on_the_rx_thread():
    # The design rule this enforces: the RX thread applies pushes and notes what
    # needs re-reading, and the caller's thread does the re-reading. All three
    # correlated waits are refused, and refused with RuntimeError rather than left
    # to time out - the RX thread is the thread that would have to deliver the
    # answer, so waiting for one from inside it can only stall the whole link.
    fake = FakeHid()
    t = transport.Transport(fake, keepalive_interval=QUIET_KEEPALIVE)
    refusals = {}

    def tries_to_read(message):
        attempts = {
            "request": lambda: t.request(
                pa.VersionMessage(action=pa.MessageAction.READ), timeout=0.1),
            "await_broadcast": lambda: t.await_broadcast(
                pa.SceneMessage, lambda: None, timeout=0.1),
            "collect": lambda: t.collect(pa.SceneMessage, lambda: None, 0.1),
        }
        for name, attempt in attempts.items():
            try:
                attempt()
                refusals[name] = None  # allowed through: the guard is not working
            except Exception as exc:  # noqa: BLE001 - the point is what type it is
                refusals[name] = exc

    t.start()
    try:
        t.add_listener(tries_to_read)
        fake.inject(*_recall_broadcast("push", rid=None))
        assert _wait_until(lambda: len(refusals) == 3)
        # The link still works, which is the whole point of refusing rather than
        # letting a listener sit in a wait.
        assert t.request(pa.VersionMessage(action=pa.MessageAction.READ),
                         timeout=REQUEST_TIMEOUT) is not None
    finally:
        t.stop()
    for name, exc in refusals.items():
        assert isinstance(exc, RuntimeError), f"{name} was not refused: {exc!r}"
        assert not isinstance(exc, TimeoutError), f"{name} waited instead of refusing"
        assert "RX thread" in str(exc)


def test_removing_a_listener_actually_removes_it():
    fake = FakeHid()
    t = transport.Transport(fake, keepalive_interval=QUIET_KEEPALIVE)
    by_callable, by_identity = [], []
    t.start()
    try:
        drop = t.add_listener(by_callable.append)
        t.add_listener(by_identity.append)
        fake.inject(*_recall_broadcast("first", rid=None))
        assert _wait_until(lambda: len(by_callable) == 1 and len(by_identity) == 1)

        drop()                                   # the handle add_listener returned
        assert t.remove_listener(by_identity.append) is True   # ... or by equality
        assert t.remove_listener(by_identity.append) is False, \
            "removing twice must report that there was nothing to remove"

        fake.inject(*_recall_broadcast("second", rid=None))
        assert _wait_until(lambda: fake.pending_reads() == 0)
        assert t.request(pa.VersionMessage(action=pa.MessageAction.READ),
                         timeout=REQUEST_TIMEOUT) is not None  # the push was handled
    finally:
        t.stop()
    assert [m.preset.name for m in by_callable] == ["first"]
    assert [m.preset.name for m in by_identity] == ["first"]


# -- device loss ---------------------------------------------------------------
# The unplug sequence as measured on macOS: the FIRST read exception carries the
# same text as the benign write stall; the SECOND says "Device is disconnected".
# A single blip must not be treated as loss; two in a row must.


class UnpluggableDevice(FakeHid):
    """A FakeHid whose reads start raising after unplug()."""

    def __init__(self, errors=None):
        super().__init__()
        self._errors = list(errors or [])
        self._unplugged = False

    def unplug(self, errors):
        self._errors = list(errors)
        self._unplugged = True

    def read(self, size, timeout=0):
        if self._unplugged:
            if self._errors:
                raise OSError(self._errors.pop(0))
            raise OSError("Device is disconnected")
        return super().read(size, timeout)


def _started(dev):
    t = transport.Transport(dev, keepalive_interval=999)
    t.start()
    return t


def test_one_read_blip_is_transient_and_the_transport_stays_healthy():
    dev = UnpluggableDevice()
    t = _started(dev)
    try:
        # one failing read, then healthy again
        blip = ["IOHIDDeviceSetReport failed: (0xE0005000) unknown error code"]
        original = dev.read
        calls = {"n": 0}

        def flaky(size, timeout=0):
            if calls["n"] == 0:
                calls["n"] += 1
                raise OSError(blip[0])
            return original(size, timeout)

        dev.read = flaky
        deadline = time.monotonic() + 2.0
        while calls["n"] == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.05)
        assert t.device_lost is None
        assert t.request(pa.VersionMessage(action=pa.MessageAction.READ),
                         timeout=2.0) is not None
    finally:
        t.stop()


def test_two_consecutive_read_failures_confirm_loss_with_the_second_message():
    dev = UnpluggableDevice()
    t = _started(dev)
    try:
        dev.unplug(["IOHIDDeviceSetReport failed: (0xE0005000) unknown error code",
                    "Device is disconnected"])
        deadline = time.monotonic() + 2.0
        while t.device_lost is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert t.device_lost is not None
        # the honest SECOND message, not the stall-lookalike first
        assert "Device is disconnected" in str(t.device_lost)
        with pytest.raises(transport.DeviceLostError, match="Device is disconnected"):
            t.send(pa.KeepAliveMessage(action=pa.MessageAction.UPDATE))
        with pytest.raises(transport.DeviceLostError):
            t.request(pa.VersionMessage(action=pa.MessageAction.READ), timeout=1.0)
        with pytest.raises(transport.DeviceLostError):
            t.await_broadcast(pa.SceneMessage, lambda: None, timeout=1.0)
        # the RX thread is still alive, waiting quietly - "never dies" holds
        assert t._rx.is_alive()
    finally:
        t.stop()


def test_loss_wakes_a_blocked_request_fast_with_the_real_error():
    dev = UnpluggableDevice()
    t = _started(dev)
    try:
        result = {}

        def blocked():
            try:
                # ModelRepo gets no reply from the fake, so this would wait the
                # full timeout if loss did not wake it
                t.request(pa.ModelRepoMessage(action=pa.MessageAction.READ),
                          timeout=30.0)
            except Exception as e:
                result["error"] = e

        worker = threading.Thread(target=blocked)
        worker.start()
        time.sleep(0.1)
        started = time.monotonic()
        dev.unplug(["first lie", "Device is disconnected"])
        worker.join(timeout=5.0)
        assert not worker.is_alive(), "the blocked request never woke"
        assert time.monotonic() - started < 5.0, "woke by timeout, not by loss"
        assert isinstance(result["error"], transport.DeviceLostError)
    finally:
        t.stop()


# -- what the RX path says when a frame does not reach a listener --------------
#
# Three different things stop a frame, and until the trailer's ENCRYPTED byte was
# read (docs/protocol.md 2.3) they all produced one line: "skipping undecodable
# inbound message". They mean different things, so they log differently now.


def _captured_frame(name):
    """Load one REAL captured frame from tests/fixtures/frames."""
    fixture = json.loads(
        (pathlib.Path(__file__).parent / "fixtures" / "frames" / name).read_text()
    )
    return fixture, [
        bytes.fromhex(h)
        for h in fixture.get("frames_hex", [fixture.get("frame_hex")])
    ]


@contextlib.contextmanager
def caplog_at(level):
    """Collect pyquadcortex.protocol.transport records at ``level``."""
    records = []

    class _Sink(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("pyquadcortex.protocol.transport")
    sink = _Sink()
    old_level = logger.level
    logger.addHandler(sink)
    logger.setLevel(level)
    try:
        yield records
    finally:
        logger.removeHandler(sink)
        logger.setLevel(old_level)


def _feed(reports, level="DEBUG"):
    """Push reports at a live transport and return (log text, seen messages)."""
    fake = FakeHid()
    t = transport.Transport(fake, keepalive_interval=QUIET_KEEPALIVE)
    seen = []
    t.add_listener(seen.append)
    t.start()
    try:
        with caplog_at(level) as records:
            for report in reports:
                fake.inject(report)
            assert _wait_until(lambda: fake.pending_reads() == 0)
            # The RX thread must have survived: a normal request still works.
            resp = t.request(
                pa.VersionMessage(action=pa.MessageAction.READ),
                timeout=REQUEST_TIMEOUT,
            )
        assert isinstance(resp, pa.VersionMessage)
    finally:
        t.stop()
    return "\n".join(r.getMessage() for r in records), seen


def test_an_encrypted_push_is_reported_as_encrypted_not_as_a_bad_parse():
    """A real encrypted License reply, straight off the wire.

    Before the trailer flag was read this frame reached ParseFromString, failed,
    and was logged as undecodable - indistinguishable from corruption. It is
    recognised now, and it still must not reach a listener, because we cannot
    read it.
    """
    _fixture, frames = _captured_frame("license_reply_encrypted.json")
    text, seen = _feed(frames)

    assert "encrypted License payload" in text
    assert "undecodable" not in text
    assert not [m for m in seen if isinstance(m, pa.LicenseMessage)]


def test_an_unregistered_message_type_says_so_by_number():
    unknown = 60000  # not a CortexMessageType the registry knows
    text, _seen = _feed(framing.encode_message(unknown, b"\x08\x03"))

    assert f"unregistered message type {unknown}" in text
    assert "encrypted" not in text
    assert "undecodable" not in text


def test_a_corrupt_payload_is_still_reported_as_undecodable():
    """The one case that means something is actually wrong keeps its own line.

    A registered type, the ENCRYPTED flag clear, and bytes that are not a valid
    protobuf message for it. Nothing else explains this, so it stays "undecodable"
    and keeps the traceback.
    """
    # Field 1, wire type 2 (length-delimited), length 0x7f, then nothing.
    corrupt = b"\x0a\x7f"
    scene = registry.type_for(pa.SceneMessage)
    text, seen = _feed(framing.encode_message(scene, corrupt))

    assert "undecodable Scene payload" in text
    assert "encrypted" not in text
    assert not [m for m in seen if isinstance(m, pa.SceneMessage)]


def test_a_malformed_frame_is_reported_separately_from_a_bad_payload():
    """A frame the codec itself rejects never gets as far as a message type."""
    body = bytes([4, 0xC0, 0xDE, 0xAD, 0xBE, 0xEF])  # body shorter than the trailer
    report = bytes([framing.OUT_REPORT_ID]) + body + bytes(129 - 1 - len(body))
    text, _seen = _feed([report])

    assert "malformed inbound frame" in text
    assert "undecodable" not in text


# Each way of damaging a gzip stream, with the exception it actually raises on
# CPython. These were MEASURED, not reasoned about: the first version of this
# test used three inputs that all raised EOFError while claiming to cover three
# hierarchies, so two thirds of the catch tuple went unpinned. `gzip.decompress`
# only runs once the magic bytes match, so damage has to survive that check.
GZIP_DAMAGE = [
    # Header method byte set to 9 (only 8, deflate, is valid).
    ("bad header method", gzip.BadGzipFile, lambda b: b[:2] + bytes([9]) + b[3:]),
    # A flipped bit in the deflate stream: the block header stops being valid.
    ("corrupt deflate stream", zlib.error, lambda b: b[:10] + bytes([b[10] ^ 1]) + b[11:]),
    # Cut short, so the stream ends before its end-of-stream marker.
    ("truncated", EOFError, lambda b: b[:20]),
]


def test_the_gzip_damage_cases_raise_the_exceptions_they_claim_to():
    """Guard the guard: prove each case reaches a DIFFERENT exception type.

    Without this, the parametrized test below passes just as happily with three
    inputs that all raise the same thing, which is how it was originally wrong.
    """
    blob = gzip.compress(b"\x08\x03" * 40)
    raised = {}
    for label, expected, damage in GZIP_DAMAGE:
        with pytest.raises(expected) as caught:
            gzip.decompress(damage(blob))
        raised[label] = type(caught.value)

    assert len(set(raised.values())) == len(GZIP_DAMAGE), raised
    # And they really are unrelated hierarchies, which is why one except clause
    # naming only OSError missed two of them.
    assert not issubclass(zlib.error, (OSError, EOFError))
    assert issubclass(gzip.BadGzipFile, OSError)


@pytest.mark.parametrize("label,expected,damage", GZIP_DAMAGE)
def test_a_damaged_compressed_payload_is_undecodable_not_an_unexpected_error(
    label, expected, damage
):
    """Broken gzip raises across three unrelated exception hierarchies.

    BadGzipFile is an OSError, a truncated stream is an EOFError, and a corrupt
    deflate stream is a zlib.error, which is neither. Only the first was caught
    here, so the other two escaped to the RX loop's backstop and were logged at
    ERROR as "unexpected". The RX thread survived either way; what was wrong was
    calling a damaged payload a bug in the library.
    """
    blob = gzip.compress(b"\x08\x03" * 40)
    scene = registry.type_for(pa.SceneMessage)
    text, seen = _feed(framing.encode_message(scene, damage(blob)))

    assert "undecodable Scene payload" in text, label
    assert "unexpected error" not in text, label
    assert not [m for m in seen if isinstance(m, pa.SceneMessage)], label


def test_a_value_error_from_parsing_is_reachable_under_the_pure_python_protobuf():
    """Justify the ValueError in _handle_message's catch tuple.

    Nothing in this suite reaches it, because the installed protobuf uses the
    `upb` implementation, which raises DecodeError for invalid UTF-8 in a string
    field. The PURE-PYTHON implementation, which any user can select with
    PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python, raises UnicodeDecodeError
    instead - and that is a ValueError.

    So the guard is deliberate breadth rather than a leftover, and this test is
    what would notice if protobuf ever stopped behaving that way, at which point
    the ValueError could come out of the tuple.
    """
    source = textwrap.dedent(
        """
        from google.protobuf.internal import api_implementation
        from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa
        assert api_implementation.Type() == "python", api_implementation.Type()
        try:
            pa.SceneLabelMessage().ParseFromString(b"\\x22\\x02\\xff\\xfe")
        except Exception as exc:
            print(type(exc).__name__, isinstance(exc, ValueError))
        else:
            print("NO-RAISE", False)
        """
    )
    env = {**os.environ, "PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION": "python",
           "PYTHONPATH": str(pathlib.Path(__file__).resolve().parents[1])}
    result = subprocess.run([sys.executable, "-c", source], env=env,
                            capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    name, is_value_error = result.stdout.split()
    assert is_value_error == "True", f"{name} is not a ValueError"
    assert name == "UnicodeDecodeError", name


def test_a_compressed_flag_disagreeing_with_the_magic_bytes_is_reported():
    """The one thing that would tell us the flag reading has gone stale.

    Compression is detected by the magic bytes on purpose (ADR-0019), and the
    argument for that rests on the flag and the magic bytes agreeing on all
    15,675 messages measured - on CorOS 4.0.1, and nowhere else. If a firmware
    ever disagrees, the library should say so rather than silently discarding
    half the evidence. The magic bytes still decide what happens to the payload.
    """
    scene = registry.type_for(pa.SceneMessage)
    payload = pa.SceneMessage(action=pa.MessageAction.UPDATE).SerializeToString()
    reports = framing.encode_message(scene, payload)
    assert len(reports) == 1, "this test hand-edits a single-report trailer"

    # Set COMPRESSED on a payload that is plainly not gzip.
    report = bytearray(reports[0])
    trailer_start = 3 + report[1] - framing.TRAILER_SIZE
    report[trailer_start + framing.TRAILER_COMPRESSED] = 1
    assert framing.decode_reports([bytes(report)]).compressed is True

    text, seen = _feed([bytes(report)])

    assert "COMPRESSED flag says True" in text
    assert "lacks the gzip magic bytes" in text
    # ... and the message still arrives, because the magic bytes are the test.
    assert [m for m in seen if isinstance(m, pa.SceneMessage)]


def test_agreement_between_the_flag_and_the_magic_bytes_is_not_reported():
    """The quiet case stays quiet, or the log line is worthless."""
    scene = registry.type_for(pa.SceneMessage)
    payload = pa.SceneMessage(action=pa.MessageAction.UPDATE).SerializeToString()
    text, seen = _feed(framing.encode_message(scene, payload))

    assert "COMPRESSED flag" not in text
    assert [m for m in seen if isinstance(m, pa.SceneMessage)]
