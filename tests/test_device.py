"""The model's `Device` - the object `pyquadcortex.connect()` hands back.

At this stage the `Device` carries the unit's identity and owns the connection;
the Directory, the cache, and the grid arrive in later stories. Everything here
runs against fakes, so no hidapi and no hardware are involved.
"""
import pytest

import pyquadcortex
from pyquadcortex import Device, protocol
from pyquadcortex.protocol import client, session
from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa


class FakeClient:
    """The two things the model asks of a protocol client, and no more."""

    def __init__(self, firmware="d14e", serial="QCS0000001"):
        self._firmware = firmware
        self._serial = serial
        self.version_reads = 0
        self.closed = False

    def version(self, timeout=10.0):
        self.version_reads += 1
        reply = pa.VersionMessage(action=pa.MessageAction.UPDATE)
        reply.app_fw_version = self._firmware
        reply.device_serial_number = self._serial
        return reply

    def close(self):
        self.closed = True


class FakeDevice:
    """Stands in for the opened HID device."""

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
        FakeTransport.instances.append(self)

    def start(self):
        self.started = True

    def stop(self, join_timeout=1.0):
        self.stopped = True

    def send(self, message):
        pass

    def request(self, message, timeout=None):
        return message  # the handshake only needs *a* reply


@pytest.fixture
def fake_stack(monkeypatch):
    """Patch the protocol session's device+transport so connect() runs dry."""
    FakeTransport.instances = []
    device = FakeDevice()
    monkeypatch.setattr(session, "open_device", lambda: device)
    monkeypatch.setattr(session, "Transport", FakeTransport)
    return device


def test_from_client_reports_the_units_firmware_and_serial():
    device = Device.from_client(FakeClient(firmware="d14e", serial="QCS0000042"))
    assert device.firmware == "d14e"
    assert device.serial == "QCS0000042"


def test_from_client_asks_the_unit_nothing_until_it_is_asked():
    qc = FakeClient()
    Device.from_client(qc)
    assert qc.version_reads == 0, "building the model must not talk to the unit"


def test_identity_is_read_once_however_often_it_is_asked_for():
    qc = FakeClient()
    device = Device.from_client(qc)
    assert (device.firmware, device.serial, device.firmware) == (
        "d14e", "QCS0000001", "d14e")
    assert qc.version_reads == 1


def test_from_client_exposes_the_client_it_was_given():
    """Both layers in one script: the caller keeps their protocol handle."""
    qc = FakeClient()
    assert Device.from_client(qc).client is qc


def test_from_client_does_not_close_a_connection_it_did_not_open():
    qc = FakeClient()
    with Device.from_client(qc) as device:
        assert device.client is qc
    assert not qc.closed, "the caller opened it, so the caller closes it"


def test_connect_returns_a_model_over_a_real_protocol_client(fake_stack):
    device = pyquadcortex.connect(settle=0)
    try:
        assert isinstance(device, Device)
        assert isinstance(device.client, client.QuadCortex)
    finally:
        device.close()


def test_connect_hands_the_device_the_connection_to_close(fake_stack):
    with pyquadcortex.connect(settle=0) as device:
        assert not fake_stack.closed
    assert fake_stack.closed, "the model opened the unit, so the model releases it"


def test_close_is_idempotent(fake_stack):
    device = pyquadcortex.connect(settle=0)
    device.close()
    device.close()  # must not raise
    assert fake_stack.closed


def test_protocol_connect_still_returns_todays_client(fake_stack):
    with protocol.connect(settle=0) as qc:
        assert isinstance(qc, client.QuadCortex)
        assert not isinstance(qc, Device)


def test_repr_names_the_type_without_talking_to_the_unit():
    qc = FakeClient()
    text = repr(Device.from_client(qc))
    assert "Device" in text
    assert qc.version_reads == 0
