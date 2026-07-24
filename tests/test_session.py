"""Tests for the connection entry point (pyquadcortex.session).

These must stay device-free: ``session.open_device`` is monkeypatched so no
hidapi and no hardware are involved. What matters here is the contract
:func:`pyquadcortex.connect` promises - the caller gets a client that is already
handshaken, the device is released on exit, and nothing leaks if bring-up fails.
"""

import pytest

from pyquadcortex import client, session


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
