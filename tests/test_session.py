"""Tests for the connection entry point (pyquadcortex.protocol.session).

These must stay device-free: ``session.open_device`` is monkeypatched so no
hidapi and no hardware are involved. What matters here is the contract
:func:`pyquadcortex.protocol.connect` promises - the caller gets a client that is already
handshaken, the device is released on exit, and nothing leaks if bring-up fails.
"""

import pytest

from pyquadcortex.protocol import client, errors, profiles, session, support
from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa


class FakeDevice:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeTransport:
    """Stands in for Transport: records lifecycle, swallows the handshake."""

    instances = []

    #: What the unit says it is. Tests set this before connect() runs.
    version_reply = None

    def __init__(self, device, keepalive_interval=5.0):
        self.device = device
        self.started = False
        self.stopped = False
        self.sent = []
        self.happened = []
        FakeTransport.instances.append(self)

    def start(self):
        self.started = True

    def stop(self, join_timeout=1.0):
        self.stopped = True

    def send(self, message):
        self.sent.append(message)
        self.happened.append(f"send {type(message).__name__}")

    def request(self, message, timeout=None):
        self.sent.append(message)
        self.happened.append(f"request {type(message).__name__}")
        return message  # the handshake only needs *a* reply

    def await_broadcast(self, expected_class, trigger, timeout=40.0, match=None):
        self.happened.append(f"await {expected_class.__name__}")
        trigger()
        reply = type(self).version_reply
        if reply is None:
            reply = pa.VersionMessage(action=pa.MessageAction.UPDATE,
                                      device_type=pa.VersionMessage.QC,
                                      zenos_git_hash="4.0.1",
                                      device_serial_number="QCS0000001")
        assert match is None or match(reply), "the canned reply must satisfy version()'s predicate"
        return reply


@pytest.fixture
def fake_stack(monkeypatch):
    """Patch session's device+transport so connect() runs without hardware."""
    FakeTransport.instances = []
    FakeTransport.version_reply = None
    device = FakeDevice()
    monkeypatch.setattr(session, "open_device", lambda: device)
    monkeypatch.setattr(session, "Transport", FakeTransport)
    return device


def test_connect_returns_a_handshaken_client(fake_stack):
    qc = session.connect(settle=0)
    assert isinstance(qc, client.QuadCortex)
    t = FakeTransport.instances[0]
    assert t.started
    # The handshake ran: a ResetCommsBuffers went out, plus the Connection and
    # the subscription burst. The caller never had to ask for any of it.
    kinds = [type(m).__name__ for m in t.sent]
    assert "ResetCommsBuffersMessage" in kinds
    assert "ConnectionMessage" in kinds
    assert "FileMessage" in kinds


def test_connect_can_defer_the_initial_file_listing(fake_stack):
    qc = session.connect(settle=0, initial_file_listing=False)
    t = FakeTransport.instances[0]

    assert not any(type(message).__name__ == "FileMessage" for message in t.sent)
    assert any(
        type(message).__name__ == "RecallPresetMessage" for message in t.sent
    )
    qc.close()


def test_connect_as_context_manager_releases_the_device(fake_stack):
    with session.connect(settle=0) as qc:
        assert isinstance(qc, client.QuadCortex)
        assert not fake_stack.closed
    t = FakeTransport.instances[0]
    assert t.stopped, "transport must be stopped on exit"
    assert fake_stack.closed, "device must be closed on exit"


def test_close_is_idempotent(fake_stack):
    qc = session.connect(settle=0)
    qc.close()
    qc.close()  # must not raise
    assert fake_stack.closed


def test_failed_handshake_does_not_leak_the_device(monkeypatch, fake_stack):
    def boom(self, *a, **kw):
        raise RuntimeError("handshake failed")

    monkeypatch.setattr(client.QuadCortex, "_hello", boom)
    with pytest.raises(RuntimeError, match="handshake failed"):
        session.connect(settle=0)
    t = FakeTransport.instances[0]
    assert t.stopped, "transport must be stopped when bring-up fails"
    assert fake_stack.closed, "device must be closed when bring-up fails"


def test_before_handshake_runs_after_start_and_before_the_handshake(fake_stack):
    """The hook exists so a listener can catch the handshake's own state burst.

    Registered a moment later - after connect() returns - and the burst is over.
    So what matters is the ORDER: the transport is started (its RX thread is
    reading) and nothing of the handshake has been sent yet.
    """
    calls = []

    def before(t):
        calls.append((t, t.started, list(t.sent)))

    qc = session.connect(settle=0, before_handshake=before)
    t = FakeTransport.instances[0]
    got, started, sent_by_then = calls[0]
    assert got is t, "the hook gets the transport a listener registers on"
    assert started, "the RX thread must already be reading"
    assert sent_by_then == [], "the hook ran after the handshake had begun"
    # ADR-0020: the profile identity read goes out first, before the handshake.
    assert type(t.sent[0]).__name__ == "VersionMessage"
    assert type(t.sent[1]).__name__ == "ResetCommsBuffersMessage"
    qc.close()


def test_before_handshake_runs_once_however_many_handshake_attempts_it_takes(
        monkeypatch, fake_stack):
    """Registering twice would register the listener twice, and it would then be
    called twice per message. The device can be openable but silent for ~9-17s
    after a boot, so a connect taking three handshake attempts is ordinary rather
    than exotic - and a hook called per attempt would still pass a test where the
    first attempt succeeds.
    """
    attempts = {"n": 0}

    def flaky_hello(self, timeout=5.0, settle=2.0,
                    initial_file_listing=True):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise TimeoutError("no response for request_id=1")

    monkeypatch.setattr(client.QuadCortex, "_hello", flaky_hello)
    calls = []
    qc = session.connect(settle=0, handshake_patience=30.0,
                         before_handshake=calls.append)
    assert attempts["n"] == 3, "the handshake was not actually retried"
    assert len(calls) == 1, "the hook runs once, not once per handshake attempt"
    qc.close()


def test_a_failing_before_handshake_hook_does_not_leak_the_device(fake_stack):
    def boom(t):
        raise RuntimeError("the listener could not be registered")

    with pytest.raises(RuntimeError, match="could not be registered"):
        session.connect(settle=0, before_handshake=boom)
    t = FakeTransport.instances[0]
    assert t.stopped, "transport must be stopped when the hook fails"
    assert fake_stack.closed, "device must be closed when the hook fails"


def test_client_with_caller_supplied_transport_does_not_own_it():
    """A hand-wired QuadCortex must not close a transport it did not open."""
    device = FakeDevice()
    t = FakeTransport(device)
    qc = client.QuadCortex(t)
    qc.close()
    assert not t.stopped
    assert not device.closed


def test_open_device_raises_device_not_found_when_hid_cannot_open(monkeypatch):
    """A failure to open surfaces as DeviceNotFoundError with guidance."""
    import sys
    import types

    fake_hid = types.ModuleType("hid")

    def explode(*a, **kw):
        raise OSError("open failed")

    fake_hid.Device = explode
    monkeypatch.setitem(sys.modules, "hid", fake_hid)
    with pytest.raises(session.DeviceNotFoundError, match="Cortex Control"):
        session.open_device()


def test_open_device_raises_device_not_found_on_the_real_hid_exception(monkeypatch):
    """The `hid` package raises HIDException, which is NOT an OSError.

    The sibling test above stubs hid to raise OSError, which is why it passed
    while the real path was broken: `hid.HIDException` inherits straight from
    Exception, so `except OSError` never fired and a raw traceback reached the
    user instead of the guidance written for exactly this case. This is the most
    common first-run failure there is, so it gets a test using the real
    exception's shape.
    """
    import sys
    import types

    class HIDException(Exception):        # mirrors hid.HIDException's MRO
        pass

    assert not issubclass(HIDException, OSError), "premise of this test"

    fake_hid = types.ModuleType("hid")
    fake_hid.HIDException = HIDException

    def explode(*a, **kw):
        raise HIDException(
            "unable to open device: No HID devices with requested VID/PID found"
        )

    fake_hid.Device = explode
    monkeypatch.setitem(sys.modules, "hid", fake_hid)

    with pytest.raises(session.DeviceNotFoundError, match="Cortex Control"):
        session.open_device()


def test_close_says_goodbye_before_tearing_down():
    """The device is told the client is leaving, and told FIRST.

    Cortex Control sends Connection{connected: false} on quit; this library
    announced the connect and then went quiet. The send needs a live transport, so
    ordering matters: it has to precede transport.stop and device.close.
    """
    from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa

    order = []

    class RecordingTransport(FakeTransport):
        def send(self, msg):
            order.append(("send", type(msg).__name__, getattr(msg, "connected", None)))

    device = FakeDevice()
    t = RecordingTransport(device)
    owned = [lambda: order.append(("device.close", None, None)),
             lambda: order.append(("transport.stop", None, None))]
    qc = client.QuadCortex(t, _owned_resources=owned)
    owned.append(qc.disconnect)          # as session.connect() does

    qc.close()

    assert order[0] == ("send", "ConnectionMessage", False), \
        f"goodbye must go out first, got {order}"
    assert [step[0] for step in order] == ["send", "transport.stop", "device.close"]


def test_disconnect_is_best_effort():
    # A dead link must not stop the rest of teardown.
    class DeadTransport(FakeTransport):
        def send(self, msg):
            raise OSError("link gone")

    torn_down = []
    qc = client.QuadCortex(DeadTransport(FakeDevice()),
                           _owned_resources=[lambda: torn_down.append("closed")])
    qc._owned.append(qc.disconnect)
    qc.close()                                  # must not raise
    assert torn_down == ["closed"], "teardown continued despite the failed send"


def test_disconnect_is_public_for_callers_owning_their_own_transport():
    # A caller who supplied their own transport owns teardown and previously had no
    # non-private way to send the goodbye.
    from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa

    t = FakeTransport(FakeDevice())
    sent = []
    t.send = lambda m: sent.append(m)
    qc = client.QuadCortex(t)                   # no owned resources
    qc.disconnect()
    assert isinstance(sent[0], pa.ConnectionMessage)
    assert sent[0].connected is False
    qc.close()                                  # still a no-op for a borrowed transport


def test_connect_retries_the_handshake_within_its_patience(monkeypatch):
    """The device can be openable but silent for ~9-12s after a (re)boot, so a
    successful open proves nothing about readiness - retry the HANDSHAKE."""
    from pyquadcortex.protocol import session

    class QuietDevice:
        def close(self):
            pass

    class FakeTransport:
        def __init__(self, device):
            pass

        def start(self):
            pass

        def stop(self, join_timeout=1.0):
            pass

        def send(self, message):
            pass

        def await_broadcast(self, expected_class, trigger, timeout=40.0, match=None):
            trigger()
            return pa.VersionMessage(action=pa.MessageAction.UPDATE,
                                     device_type=pa.VersionMessage.QC,
                                     zenos_git_hash="4.0.1",
                                     device_serial_number="QCS0000001")

    attempts = {"n": 0}

    def flaky_hello(self, timeout=5.0, settle=2.0,
                    initial_file_listing=True):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise TimeoutError("no response for request_id=1")

    monkeypatch.setattr(session, "open_device", lambda: QuietDevice())
    monkeypatch.setattr(session, "Transport", FakeTransport)
    monkeypatch.setattr(session.QuadCortex, "_hello", flaky_hello)
    monkeypatch.setattr(session.QuadCortex, "disconnect", lambda self: None,
                        raising=False)
    qc = session.connect(timeout=0.01, settle=0, handshake_patience=30.0)
    assert attempts["n"] == 3, "two silent windows were retried through"
    qc.close()


def test_connect_gives_up_after_its_patience_with_the_silent_window_explained(monkeypatch):
    from pyquadcortex.protocol import session

    class QuietDevice:
        def close(self):
            pass

    class FakeTransport:
        def __init__(self, device):
            pass

        def start(self):
            pass

        def stop(self, join_timeout=1.0):
            pass

        def send(self, message):
            pass

        def await_broadcast(self, expected_class, trigger, timeout=40.0, match=None):
            trigger()
            return pa.VersionMessage(action=pa.MessageAction.UPDATE,
                                     device_type=pa.VersionMessage.QC,
                                     zenos_git_hash="4.0.1",
                                     device_serial_number="QCS0000001")

    def never_answers(self, timeout=5.0, settle=2.0,
                      initial_file_listing=True):
        raise TimeoutError("no response for request_id=1")

    monkeypatch.setattr(session, "open_device", lambda: QuietDevice())
    monkeypatch.setattr(session, "Transport", FakeTransport)
    monkeypatch.setattr(session.QuadCortex, "_hello", never_answers)
    with pytest.raises(TimeoutError, match="openable-but-silent"):
        session.connect(timeout=0.01, settle=0, handshake_patience=0.05)


# -- ADR-0020: connect() resolves the profile before the handshake -----------


def test_connect_retries_the_identity_read_within_the_same_patience(monkeypatch, fake_stack):
    """The identity read is the first thing connect() asks of the unit, and it
    lands in the same 9-17s openable-but-silent window the handshake retry
    loop exists for - so it must share handshake_patience rather than fail
    outright at a bare `timeout`."""
    real_await = FakeTransport.await_broadcast
    calls = {"n": 0}

    def flaky_await(self, expected_class, trigger, timeout=40.0, match=None):
        calls["n"] += 1
        if calls["n"] < 3:
            trigger()
            raise TimeoutError("no response for request_id=1")
        return real_await(self, expected_class, trigger, timeout=timeout, match=match)

    monkeypatch.setattr(FakeTransport, "await_broadcast", flaky_await)
    qc = session.connect(handshake_patience=30.0)
    assert calls["n"] == 3, "the identity read was retried, not failed outright"
    assert type(qc) is client.QuadCortex


def test_connect_gives_up_on_a_silent_identity_read_after_its_patience(monkeypatch, fake_stack):
    def never_answers(self, expected_class, trigger, timeout=40.0, match=None):
        trigger()
        raise TimeoutError("no response for request_id=1")

    monkeypatch.setattr(FakeTransport, "await_broadcast", never_answers)
    with pytest.raises(TimeoutError, match="openable-but-silent"):
        session.connect(timeout=0.05, handshake_patience=0.3)
    t = FakeTransport.instances[0]
    assert t.stopped and t.device.closed


def test_connect_waits_for_a_version_carrying_both_fields_it_resolves_on(
        monkeypatch, fake_stack):
    """A partial identity reply is waited THROUGH, not refused.

    The unit answers a `Version` READ twice, and `version()`'s predicate rightly
    accepts a reply carrying only the serial or only the firmware - the cache
    keeps what the unit sent. Resolution needs `device_type` AND
    `zenos_git_hash`, and reading identity through that looser predicate meant a
    partial first answer raised `UnsupportedDevice` on a unit this library has a
    profile for. The wait is now the strict one, inside the same patience.
    """
    partial = pa.VersionMessage(action=pa.MessageAction.UPDATE,
                                device_serial_number="QCS0000001")
    full = pa.VersionMessage(action=pa.MessageAction.UPDATE,
                             device_type=pa.VersionMessage.QC,
                             zenos_git_hash="4.0.1",
                             device_serial_number="QCS0000001")
    reads = {"n": 0}

    def two_answers(self, expected_class, trigger, timeout=40.0, match=None):
        reads["n"] += 1
        trigger()
        assert match is not None, "the identity read must carry a predicate"
        if reads["n"] == 1:
            # What the real transport does with a message the predicate
            # rejects: keep waiting, and time out with nothing to hand back.
            assert not match(partial), "a serial-only reply cannot resolve a profile"
            raise TimeoutError("no response for request_id=1")
        assert match(full)
        return full

    monkeypatch.setattr(FakeTransport, "await_broadcast", two_answers)
    qc = session.connect(settle=0, handshake_patience=30.0)
    assert reads["n"] == 2, "the partial answer was waited through, not taken"
    assert type(qc) is client.QuadCortex


def test_connect_says_what_went_unanswered_when_no_identity_ever_arrives(
        monkeypatch, fake_stack):
    """The guidance is one sentence; the clause naming the question differs."""
    def never_resolves(self, expected_class, trigger, timeout=40.0, match=None):
        trigger()
        raise TimeoutError("no response for request_id=1")

    monkeypatch.setattr(FakeTransport, "await_broadcast", never_resolves)
    with pytest.raises(TimeoutError) as caught:
        session.connect(timeout=0.05, handshake_patience=0.3)
    text = str(caught.value)
    assert "device_type and zenos_git_hash" in text
    assert "openable-but-silent" in text


def test_connect_with_a_profile_for_another_device_refuses_a_quad_cortex(fake_stack):
    """The device-type check compares unconditionally now that the identity
    read guarantees the field is there. A Mini profile against a QC must not
    reach the handshake."""
    with pytest.raises(profiles.UnsupportedDevice, match="asked for QuadCortexMini"):
        session.connect(profile=profiles.QuadCortexMini,
                        support=support.Support.EXPERIMENTAL)
    t = FakeTransport.instances[0]
    assert t.stopped and t.device.closed
    assert not any(h.startswith("request ResetComms") for h in t.happened)


def test_connect_resolves_the_profile_from_the_units_version_before_the_handshake(fake_stack):
    qc = session.connect()
    t = FakeTransport.instances[0]
    assert type(qc) is client.QuadCortex
    assert qc.support is support.Support.VERIFIED
    order = [h for h in t.happened if h.startswith(("await Version", "request ResetComms", "send Version"))]
    assert order[0] == "await VersionMessage", "identity is read before anything else is sent"
    assert order.index("await VersionMessage") < order.index("request ResetCommsBuffersMessage")


def test_connect_hands_back_the_4_1_class_for_a_4_1_unit(fake_stack):
    FakeTransport.version_reply = pa.VersionMessage(
        action=pa.MessageAction.UPDATE, device_type=pa.VersionMessage.QC,
        zenos_git_hash="4.1.0", device_serial_number="QCS0000001")
    qc = session.connect()
    assert type(qc) is profiles.QuadCortex41
    assert qc.unverified_operations == client.QuadCortex.operations()
    with pytest.raises(errors.ControlNotDrivable):
        qc.switch_scene(1)


def test_connect_refuses_an_unknown_unit_and_releases_the_device(fake_stack):
    FakeTransport.version_reply = pa.VersionMessage(
        action=pa.MessageAction.UPDATE, device_type=pa.VersionMessage.QC,
        zenos_git_hash="4.2.0", device_serial_number="QCS0000001")
    with pytest.raises(profiles.UnsupportedDevice, match="4.2.0"):
        session.connect()
    t = FakeTransport.instances[0]
    assert t.stopped and t.device.closed
    assert not any(h.startswith("request ResetComms") for h in t.happened), "no handshake ran"


def test_connect_with_profile_skips_the_registry_but_checks_the_device_type(fake_stack):
    FakeTransport.version_reply = pa.VersionMessage(
        action=pa.MessageAction.UPDATE, device_type=pa.VersionMessage.QC,
        zenos_git_hash="4.2.0", device_serial_number="QCS0000001")
    qc = session.connect(profile=profiles.QuadCortex41, support=support.Support.EXPERIMENTAL)
    assert type(qc) is profiles.QuadCortex41
    assert qc.support is support.Support.EXPERIMENTAL
    with pytest.raises(profiles.UnsupportedDevice, match="asked for QuadCortexMini"):
        session.connect(profile=profiles.QuadCortexMini)


def test_the_announce_string_is_the_resolved_profiles(fake_stack):
    """The handshake announces cortex_control_version for the RESOLVED class,
    not always the base QuadCortex - "send VersionMessage" is in `happened`
    for the identity read's own trigger too, so that alone can't fail here."""
    FakeTransport.version_reply = pa.VersionMessage(
        action=pa.MessageAction.UPDATE, device_type=pa.VersionMessage.QC,
        zenos_git_hash="4.1.0", device_serial_number="QCS0000001")
    qc = session.connect()
    assert type(qc) is profiles.QuadCortex41
    t = FakeTransport.instances[0]
    announces = [m for m in t.sent
                if isinstance(m, pa.VersionMessage) and m.HasField("cortex_control_version")]
    assert announces, "the handshake sent a Version announce"
    assert announces[0].cortex_control_version == profiles.QuadCortex41.CC_VERSION
