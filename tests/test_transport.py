"""Tests for the framed HID transport (pyquadcortex.transport).

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
import itertools
import struct
import threading
import time

import pytest

from pyquadcortex import framing, registry, transport
from pyquadcortex.proto import ProductionAutomation_pb2 as pa

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
        msg_type, payload = framing.decode_reports([report])
        if registry.class_for(msg_type) is pa.VersionMessage:
            req = pa.VersionMessage()
            req.ParseFromString(payload)
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
                msg_type, _ = framing.decode_reports([report])
                if registry.class_for(msg_type) is pa.KeepAliveMessage:
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
    from pyquadcortex.proto import Preset_pb2 as preset

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
