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
    """The four things the model asks of a protocol client, and no more.

    ``version()`` here decides the shape the model reads. The producer side of
    that agreement is pinned by
    ``test_the_real_client_returns_the_reply_shape_the_model_reads`` below, so
    this fake cannot drift away from what ``QuadCortex.version()`` actually
    hands back - and ``test_the_real_client_offers_what_the_model_subscribes_with``
    does the same job for the subscription.
    """

    def __init__(self, firmware="d14e", serial="QCS0000001", omit=()):
        self._firmware = firmware
        self._serial = serial
        self._omit = set(omit)
        self.version_reads = 0
        self.closed = False
        self.listeners = []

    def version(self, timeout=10.0):
        self.version_reads += 1
        return self.version_message()

    def version_message(self):
        reply = pa.VersionMessage(action=pa.MessageAction.UPDATE)
        if "app_fw_version" not in self._omit:
            reply.app_fw_version = self._firmware
        if "device_serial_number" not in self._omit:
            reply.device_serial_number = self._serial
        return reply

    def add_listener(self, listener):
        self.listeners.append(listener)
        return lambda: self.remove_listener(listener)

    def remove_listener(self, listener):
        try:
            self.listeners.remove(listener)
        except ValueError:
            return False
        return True

    def push(self, message):
        """The unit volunteering something, as the RX thread would deliver it."""
        for listener in list(self.listeners):
            listener(message)

    def close(self):
        self.closed = True


class ReplyingTransport:
    """A transport that answers a request with a canned reply, by message name.

    Used to drive the REAL ``QuadCortex`` rather than a stub of it.
    """

    def __init__(self, canned, trailing=()):
        self.canned = canned
        #: What the unit sends AFTER answering, before the caller wakes. The
        #: real one does: it answers a `Version` READ and then asks one of its
        #: own (``docs/protocol.md`` section 4). A double that only ever
        #: delivers the answer cannot see a cache that miscounts the rest.
        self.trailing = list(trailing)
        self.sent = []
        self.listeners = []

    def send(self, msg):
        self.sent.append(msg)

    def request(self, msg, timeout=5.0, match=None):
        self.sent.append(msg)
        reply = self.canned[type(msg).__name__]
        for message in [reply] + self.trailing:
            for listener in list(self.listeners):
                listener(message)   # listeners see a reply first (ADR-0009)
        if match is not None and not match(reply):
            raise TimeoutError(f"canned {type(reply).__name__} did not match")
        return reply

    def await_broadcast(self, expected_class, trigger, timeout=5.0, match=None):
        """The same delivery as :meth:`request`, for a read that waits by type
        and predicate rather than by request id - which ``version()`` does,
        because the unit's own ``Version{READ}`` arrives right behind the
        answer and a type-correlated wait cannot tell them apart. The reply and
        the trailing messages all reach the listeners, as on the wire; the
        caller gets the first one the predicate accepts."""
        before = len(self.sent)
        trigger()
        assert len(self.sent) == before + 1, (
            "this double keys the canned reply off the ONE message the trigger "
            "sends; a read that sends none or several needs a different double")
        reply = self.canned[type(self.sent[-1]).__name__]
        arrived = [reply] + self.trailing
        for message in arrived:
            for listener in list(self.listeners):
                listener(message)
        for message in arrived:
            if isinstance(message, expected_class) and (match is None or match(message)):
                return message
        raise TimeoutError(f"no {expected_class.__name__} the predicate accepted")

    def add_listener(self, listener):
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)


class FakeDevice:
    """Stands in for the opened HID device."""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeTransport:
    """Stands in for Transport: records lifecycle, swallows the handshake.

    ``happened`` is the running order of everything the connect path does to it,
    which is what makes "before the handshake" checkable rather than assumed.
    """

    instances = []

    def __init__(self, device, keepalive_interval=5.0):
        self.device = device
        self.started = False
        self.stopped = False
        self.listeners = []
        self.happened = []
        FakeTransport.instances.append(self)

    def start(self):
        self.started = True
        self.happened.append("start")

    def stop(self, join_timeout=1.0):
        self.stopped = True

    def send(self, message):
        self.happened.append(f"send {type(message).__name__}")

    def request(self, message, timeout=None):
        self.happened.append(f"request {type(message).__name__}")
        return message  # the handshake only needs *a* reply

    def add_listener(self, listener):
        self.listeners.append(listener)
        self.happened.append("add_listener")
        return lambda: self.remove_listener(listener)

    def remove_listener(self, listener):
        try:
            self.listeners.remove(listener)
        except ValueError:
            return False
        return True

    def push(self, message):
        for listener in list(self.listeners):
            listener(message)


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
    """Two fields, one round trip - through the real client and the real cache.

    Built on `ReplyingTransport` rather than `FakeClient` on purpose. `FakeClient`
    hands its reply straight back and notifies no listener, so the cache's
    arrival count never moves and this test passed just as happily against the
    version of the cache that took two round trips here (see
    `tests/test_state.py`'s `test_the_unit_asking_a_question_back_is_not_a_push_
    that_landed`). The reply carries a field the entry does not keep and is
    followed by the unit's own `Version` READ, which is what the unit really
    sends.
    """
    reply = pa.VersionMessage(action=pa.MessageAction.UPDATE)
    reply.app_fw_version = "d14e"
    reply.device_serial_number = "QCS0000001"
    reply.uboot_version = "2019.04"
    transport = ReplyingTransport(
        {"VersionMessage": reply},
        trailing=[pa.VersionMessage(action=pa.MessageAction.READ)])
    device = Device.from_client(client.QuadCortex(transport))

    assert (device.firmware, device.serial, device.firmware) == (
        "d14e", "QCS0000001", "d14e")
    assert sum(isinstance(m, pa.VersionMessage) for m in transport.sent) == 1


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


def test_repr_says_open_before_close_and_closed_after(fake_stack):
    """A model that lies about itself is the one thing this library must not do."""
    device = pyquadcortex.connect(settle=0)
    assert repr(device) == "<Device open, owns its connection>"
    device.close()
    assert repr(device) == "<Device closed, owns its connection>"


def test_repr_distinguishes_an_owned_connection_from_a_borrowed_one():
    """Whether close() releases the unit is otherwise invisible from outside."""
    assert repr(Device.from_client(FakeClient())) == (
        "<Device open, borrows its connection>")


# -- what a Version reply has to carry ---------------------------------------


def test_the_real_client_returns_the_reply_shape_the_model_reads():
    """Pins the producer, not just the model's stub of it.

    ``Device.firmware`` and ``Device.serial`` read two named fields off whatever
    ``QuadCortex.version()`` returns. Without this, the real method could return
    something else entirely - or nothing - and every test here would still pass,
    because they all read the reply the fake builds.
    """
    reply = pa.VersionMessage(action=pa.MessageAction.UPDATE)
    reply.app_fw_version = "d14e"
    reply.device_serial_number = "QCS0000001"
    transport = ReplyingTransport({"VersionMessage": reply})
    qc = client.QuadCortex(transport)

    got = qc.version()
    assert isinstance(got, pa.VersionMessage)
    assert got.app_fw_version == "d14e"
    assert got.device_serial_number == "QCS0000001"
    assert Device.from_client(qc).firmware == "d14e"
    assert isinstance(transport.sent[-1], pa.VersionMessage)


def test_a_version_reply_missing_a_field_is_refused_not_reported_as_empty():
    """An absent field decodes as "", which would ship a guess as a fact."""
    device = Device.from_client(FakeClient(omit=["app_fw_version"]))
    with pytest.raises(RuntimeError, match="app_fw_version"):
        device.firmware


def test_an_incomplete_version_reply_is_not_cached():
    """A cached empty string could never be recovered from on this connection."""
    qc = FakeClient(omit=["device_serial_number"])
    device = Device.from_client(qc)
    with pytest.raises(RuntimeError):
        device.serial
    qc._omit = set()                     # the unit answers properly next time
    assert device.serial == "QCS0000001"
    assert qc.version_reads == 2


# -- a closed Device answers nothing -----------------------------------------


def test_a_closed_device_refuses_reads_it_could_have_served_from_cache(fake_stack):
    """The cache must not outlive the connection it was read over."""
    device = pyquadcortex.connect(settle=0)
    FakeTransport.instances[-1].push(_fake_version_reply())   # as the burst does
    assert device.firmware == "d14e"

    device.close()

    for attribute in ("firmware", "serial", "client"):
        with pytest.raises(RuntimeError, match="closed"):
            getattr(device, attribute)


def test_a_closed_borrowed_device_refuses_reads_that_would_still_work():
    """close() means done with this Device, even when the connection lives on."""
    qc = FakeClient()
    device = Device.from_client(qc)
    device.close()
    assert not qc.closed, "the caller opened it, so the caller closes it"
    with pytest.raises(RuntimeError, match="closed"):
        device.firmware


def _fake_version_reply():
    reply = pa.VersionMessage(action=pa.MessageAction.UPDATE)
    reply.app_fw_version = "d14e"
    reply.device_serial_number = "QCS0000001"
    return reply


# -- connect() passes its arguments through ----------------------------------


def test_connect_hands_every_argument_to_the_protocol_layer(monkeypatch):
    """A dropped handshake_patience has no symptom a fake stack can show.

    The fake transport swallows the handshake, so a `Device` still comes back
    with the argument missing - and on real hardware the unit would get 5
    seconds of patience instead of 30, inside a documented 9 to 17 second
    openable-but-silent window.
    """
    seen = _spy_on_protocol_connect(monkeypatch)
    pyquadcortex.connect(timeout=1.5, settle=0.25, handshake_patience=45.0)
    assert _without_the_hook(seen) == {
        "timeout": 1.5, "settle": 0.25, "handshake_patience": 45.0}


def test_connect_passes_its_defaults_through_unchanged(monkeypatch):
    seen = _spy_on_protocol_connect(monkeypatch)
    pyquadcortex.connect()
    assert _without_the_hook(seen) == {
        "timeout": 5.0, "settle": 2.0, "handshake_patience": 30.0}


def _spy_on_protocol_connect(monkeypatch):
    seen = {}

    def spy(**kwargs):
        seen.update(kwargs)
        return FakeClient()

    monkeypatch.setattr(protocol, "connect", spy)
    return seen


def _without_the_hook(seen):
    """The numeric arguments, with the subscription hook checked and removed.

    ``before_handshake`` gets its own tests below, where what matters is when it
    runs rather than that it was passed.
    """
    assert callable(seen.get("before_handshake")), (
        "the model did not subscribe before the handshake, so the connect "
        "burst - nearly every state type the unit has - reaches nobody")
    return {name: value for name, value in seen.items()
            if name != "before_handshake"}


# -- the model is listening before the unit starts talking -------------------


def test_the_model_subscribes_before_the_handshake_says_a_word(fake_stack):
    """The burst is the only moment the unit volunteers nearly everything it
    knows, and it does not start until seconds after `connect()` returns. A
    model that subscribed on the client it is handed would miss all of it and
    read every value back one at a time."""
    with pyquadcortex.connect(settle=0):
        happened = FakeTransport.instances[-1].happened

    assert "add_listener" in happened, "the model never subscribed"
    talking = [step for step in happened
               if step.startswith("send") or step.startswith("request")]
    assert talking, "the handshake sent nothing, so ordering proves nothing here"
    assert happened.index("add_listener") < happened.index(talking[0])


def test_a_push_the_burst_delivered_answers_the_first_read_for_free(fake_stack):
    """The whole point of subscribing that early."""
    with pyquadcortex.connect(settle=0) as device:
        FakeTransport.instances[-1].push(_fake_version_reply())
        before = len(FakeTransport.instances[-1].happened)

        assert device.firmware == "d14e"
        assert device.serial == "QCS0000001"

        assert len(FakeTransport.instances[-1].happened) == before, (
            "the unit had already said so, and the model asked again")


def test_closing_the_device_stops_the_model_listening(fake_stack):
    with pyquadcortex.connect(settle=0) as device:
        transport = FakeTransport.instances[-1]
        assert transport.listeners
    assert transport.listeners == []


def test_from_client_listens_on_the_connection_it_was_handed(fake_stack):
    """The burst is long over by then, so this cache starts cold - but an edit
    made from here on still reaches it without anybody asking."""
    qc = FakeClient()
    device = Device.from_client(qc)
    assert qc.listeners, "the model never subscribed to the connection"

    qc.push(qc.version_message())

    assert device.firmware == "d14e"
    assert qc.version_reads == 0


def test_closing_a_borrowed_device_stops_it_listening(fake_stack):
    """It does not own the connection, so it has to leave it as it found it."""
    qc = FakeClient()
    Device.from_client(qc).close()
    assert qc.listeners == []


def test_the_real_client_offers_what_the_model_subscribes_with(fake_stack):
    """Pins the producer, not just the fake.

    Every listening test here runs against ``FakeClient.add_listener``. Without
    this, the real client could rename or lose the method and they would all
    still pass.
    """
    with protocol.connect(settle=0) as qc:
        device = Device.from_client(qc)
        transport = FakeTransport.instances[-1]
        assert transport.listeners, "the model did not reach the transport"
        device.close()
        assert transport.listeners == []
