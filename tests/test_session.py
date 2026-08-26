"""Tests for the connection entry point (pyquadcortex.protocol.session).

These must stay device-free: ``session.open_device`` is monkeypatched so no
hidapi and no hardware are involved. What matters here is the contract
:func:`pyquadcortex.protocol.connect` promises - the caller gets a client that is already
handshaken, the device is released on exit, and nothing leaks if bring-up fails.
"""

import pytest

from pyquadcortex.protocol import client, session


class FakeDevice:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeTransport:
    """Stands in for Transport: records lifecycle, swallows the handshake."""

    instances = []

    def __init__(self, device, keepalive_interval=5.0):
        self.device = device
        self.started = False
        self.stopped = False
        self.sent = []
        FakeTransport.instances.append(self)

    def start(self):
        self.started = True

    def stop(self, join_timeout=1.0):
        self.stopped = True

    def send(self, message):
        self.sent.append(message)

    def request(self, message, timeout=None):
        self.sent.append(message)
        return message  # the handshake only needs *a* reply


@pytest.fixture
def fake_stack(monkeypatch):
    """Patch session's device+transport so connect() runs without hardware."""
    FakeTransport.instances = []
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
    assert type(t.sent[0]).__name__ == "ResetCommsBuffersMessage"
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

    def flaky_hello(self, timeout=5.0, settle=2.0):
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


def _hid_info(**overrides):
    """One hid.enumerate() dict, defaulting to a Quad Cortex control interface."""
    info = {
        "vendor_id": 0x152A,
        "product_id": 0x880A,
        "path": b"qc-path",
        "usage_page": 0x0001,
        "usage": 0x0000,
        "interface_number": 5,
    }
    info.update(overrides)
    return info


class RecordingDevice:
    """Stands in for hid.Device: records how it was constructed."""

    opened = []

    def __init__(self, vid=None, pid=None, serial=None, path=None):
        RecordingDevice.opened.append({"vid": vid, "pid": pid, "path": path})
        self.closed = False

    def close(self):
        self.closed = True


def _install_hid(monkeypatch, *, enumerate_result=None, device_cls=RecordingDevice):
    """Put a fake `hid` module in sys.modules for open_device() to import."""
    import sys
    import types

    RecordingDevice.opened = []
    fake_hid = types.ModuleType("hid")
    if enumerate_result is not None:
        fake_hid.enumerate = lambda *a, **kw: list(enumerate_result)
    fake_hid.Device = device_cls
    monkeypatch.setitem(sys.modules, "hid", fake_hid)
    return fake_hid


def test_open_device_opens_a_mini_with_a_different_product_id(monkeypatch):
    """Quad Cortex Mini may not be 0x880A. Enumerate by vendor, open by path."""
    mini = _hid_info(product_id=0xFFFF, path=b"mini-path")
    _install_hid(monkeypatch, enumerate_result=[mini])
    device = session.open_device()
    assert RecordingDevice.opened == [
        {"vid": None, "pid": None, "path": b"mini-path"}
    ]
    assert device is not None


def test_open_device_still_opens_quad_cortex_by_path_when_enumerated(monkeypatch):
    _install_hid(monkeypatch, enumerate_result=[_hid_info()])
    session.open_device()
    assert RecordingDevice.opened == [
        {"vid": None, "pid": None, "path": b"qc-path"}
    ]


def test_open_device_skips_a_non_control_hid_interface_when_a_control_one_exists(
        monkeypatch):
    other = _hid_info(path=b"other", usage_page=0xFF00, product_id=0x0001)
    control = _hid_info(path=b"control")
    _install_hid(monkeypatch, enumerate_result=[other, control])
    session.open_device()
    assert RecordingDevice.opened == [
        {"vid": None, "pid": None, "path": b"control"}
    ]


def test_open_device_accepts_an_unreported_usage_page(monkeypatch):
    """Linux hidraw often leaves usage_page at 0. That is not a mismatch."""
    mini = _hid_info(product_id=0xFFFF, path=b"linux-mini", usage_page=0)
    _install_hid(monkeypatch, enumerate_result=[mini])
    session.open_device()
    assert RecordingDevice.opened == [
        {"vid": None, "pid": None, "path": b"linux-mini"}
    ]


def test_open_device_falls_back_to_the_known_pid_when_enumerate_is_empty(
        monkeypatch):
    _install_hid(monkeypatch, enumerate_result=[])
    session.open_device()
    assert RecordingDevice.opened == [
        {"vid": 0x152A, "pid": 0x880A, "path": None}
    ]


def test_open_device_falls_back_to_the_known_pid_when_enumerate_is_missing(
        monkeypatch):
    """The original Device(vid, pid) path, for backends with no enumerate()."""
    _install_hid(monkeypatch, enumerate_result=None)
    session.open_device()
    assert RecordingDevice.opened == [
        {"vid": 0x152A, "pid": 0x880A, "path": None}
    ]


def test_open_device_tries_the_next_interface_when_the_first_fails_to_open(
        monkeypatch):
    first = _hid_info(path=b"busy")
    second = _hid_info(path=b"free", product_id=0xFFFF)

    class FlakyDevice(RecordingDevice):
        def __init__(self, vid=None, pid=None, serial=None, path=None):
            super().__init__(vid=vid, pid=pid, serial=serial, path=path)
            if path == b"busy":
                raise OSError("exclusive access")

    _install_hid(monkeypatch, enumerate_result=[first, second],
                 device_cls=FlakyDevice)
    session.open_device()
    assert RecordingDevice.opened[-1] == {
        "vid": None, "pid": None, "path": b"free",
    }


def test_open_device_uses_open_path_on_the_older_hidapi_package(monkeypatch):
    import sys
    import types

    opened = []

    class OldDevice:
        def open(self, vid, pid):
            opened.append(("open", vid, pid))

        def open_path(self, path):
            opened.append(("open_path", path))

        def close(self):
            pass

    fake_hid = types.ModuleType("hid")
    fake_hid.enumerate = lambda *a, **kw: [_hid_info(path=b"old-path")]
    fake_hid.device = OldDevice
    monkeypatch.setitem(sys.modules, "hid", fake_hid)
    session.open_device()
    assert opened == [("open_path", b"old-path")]


def test_open_device_opens_the_only_neural_dsp_interface_even_if_usage_page_differs(
        monkeypatch):
    """If Mini reports a vendor-defined page and it is the only Neural DSP HID
    device, skipping it would mean never connecting. Prefer the control page
    when one exists; otherwise take whatever the vendor enumerated."""
    mini = _hid_info(product_id=0xFFFF, path=b"atma", usage_page=0xFF00)
    _install_hid(monkeypatch, enumerate_result=[mini])
    session.open_device()
    assert RecordingDevice.opened == [
        {"vid": None, "pid": None, "path": b"atma"}
    ]


def test_control_interfaces_ignores_other_vendors():
    ours = _hid_info()
    other = _hid_info(vendor_id=0x1234, path=b"not-ours")
    assert session._control_interfaces([other, ours]) == [ours]


def test_open_device_opens_a_mini_named_in_the_product_string_with_vendor_id_zero(
        monkeypatch):
    """macOS hidapi reports vendor_id 0 for some interfaces. A Mini that did
    that would be invisible to a VID-only filter."""
    mini = _hid_info(
        vendor_id=0,
        product_id=0,
        path=b"named-mini",
        product_string="Quad Cortex Mini",
        manufacturer_string="Neural DSP",
    )
    _install_hid(monkeypatch, enumerate_result=[mini])
    session.open_device()
    assert RecordingDevice.opened == [
        {"vid": None, "pid": None, "path": b"named-mini"}
    ]


def test_open_device_says_hidapi_saw_nothing_when_enumerate_is_empty(monkeypatch):
    """The 0x880A fallback's HIDException must not be the whole story: Mini is
    not that PID, so 'VID/PID not found' reads as 'this library only opens QC'."""
    def explode(*a, **kw):
        raise OSError("No HID devices with requested VID/PID found")

    _install_hid(monkeypatch, enumerate_result=[], device_cls=explode)
    with pytest.raises(session.DeviceNotFoundError, match="does not currently see"):
        session.open_device()


def test_open_device_says_which_pid_it_saw_when_open_fails(monkeypatch):
    def explode(*a, **kw):
        raise OSError("exclusive access")

    mini = _hid_info(product_id=0xFFFF, path=b"busy-mini")
    _install_hid(monkeypatch, enumerate_result=[mini], device_cls=explode)
    with pytest.raises(session.DeviceNotFoundError, match="0xffff"):
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

    attempts = {"n": 0}

    def flaky_hello(self, timeout=5.0, settle=2.0):
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

    def never_answers(self, timeout=5.0, settle=2.0):
        raise TimeoutError("no response for request_id=1")

    monkeypatch.setattr(session, "open_device", lambda: QuietDevice())
    monkeypatch.setattr(session, "Transport", FakeTransport)
    monkeypatch.setattr(session.QuadCortex, "_hello", never_answers)
    with pytest.raises(TimeoutError, match="openable-but-silent"):
        session.connect(timeout=0.01, settle=0, handshake_patience=0.05)
