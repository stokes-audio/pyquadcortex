"""The device profile seam (ADR-0020, spec 2026-09-03)."""
import importlib
import logging
import types

import pytest

from pyquadcortex.protocol import client, errors, models, options, params, profiles, support
from pyquadcortex.protocol.catalogs import coros_4_0_1
from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa
from test_client import FakeTransport  # the offline transport double


def test_the_baseline_shims_re_export_the_4_0_1_snapshot():
    """`protocol.models` keeps meaning CorOS 4.0.1 until an ADR moves it."""
    assert models.ALL is coros_4_0_1.models.ALL
    assert params.BY_MODEL is coros_4_0_1.params.BY_MODEL
    assert options.OPTION_LABELS is coros_4_0_1.options.OPTION_LABELS
    # Only `options.py` defines `__all__` (`models.py` and `params.py` do not);
    # this checks re-export fidelity on the module that actually has one.
    assert set(options.__all__) == set(coros_4_0_1.options.__all__)


def test_the_old_import_paths_still_resolve_as_modules():
    for name in ("models", "params", "options"):
        importlib.import_module(f"pyquadcortex.protocol.{name}")
        importlib.import_module(f"pyquadcortex.protocol.catalogs.coros_4_0_1.{name}")


def test_quadcortex_declares_the_4_0_1_profile():
    qc = client.QuadCortex
    assert qc.DEVICE_TYPE == pa.VersionMessage.QC
    assert qc.MEASURED_ON == ("4.0.1",)
    assert qc.CC_VERSION == "4.0.1"
    assert qc.EVIDENCE is support.Evidence.MAINTAINER
    assert qc.HARDWARE == support.Hardware(footswitches=8, expression_ports=2)
    assert qc.VERIFIED is support.EVERYTHING
    assert qc.models is coros_4_0_1.models
    assert qc.params is coros_4_0_1.params
    assert qc.options is coros_4_0_1.options


def test_always_holds_only_the_lifecycle_and_each_entry_has_a_reason():
    assert set(client.QuadCortex.ALWAYS) == {
        "version", "catalog", "close", "disconnect", "add_listener", "remove_listener",
        "support", "unverified_operations"}
    for name, reason in client.QuadCortex.ALWAYS.items():
        assert isinstance(reason, str) and len(reason) > 20, name


def test_operations_are_every_public_plain_function_not_in_always():
    ops = client.QuadCortex.operations()
    public = {n for n, v in vars(client.QuadCortex).items()
              if not n.startswith("_") and isinstance(v, types.FunctionType)}
    assert ops == frozenset(public - set(client.QuadCortex.ALWAYS))
    assert "set_scene_label" in ops and "version" not in ops
    assert len(ops) > 100


def test_every_public_property_is_in_always():
    """A property is never an operation, so the guard must never see one.

    `operations()` counts plain functions, which means a property is neither
    guarded nor refused - and a property outside `ALWAYS` would therefore be a
    device read a stub profile answers as if it had measured it. There is one
    place that cannot happen: `ALWAYS`, where each entry carries its reason.
    """
    properties = {n for n, v in vars(client.QuadCortex).items()
                  if not n.startswith("_") and isinstance(v, property)}
    assert properties, "the introspection found no properties at all"
    assert not properties - set(client.QuadCortex.ALWAYS), (
        f"public properties outside ALWAYS: {sorted(properties - set(client.QuadCortex.ALWAYS))}. "
        f"A property cannot be guarded, so it must be an ALWAYS entry with a reason.")


def _profile(verified=frozenset(), name="Probe"):
    """A throwaway subclass; deleted from _PROFILES afterwards by the fixture."""
    return type(name, (client.QuadCortex,), {
        "MEASURED_ON": ("9.9.9",), "EVIDENCE": support.Evidence.STUB,
        "VERIFIED": verified})


@pytest.fixture
def forget_probes():
    yield
    client.QuadCortex._PROFILES[:] = [
        c for c in client.QuadCortex._PROFILES if c.__name__ != "Probe"]


def test_a_subclass_registers_itself_and_inherits_what_it_does_not_declare(forget_probes):
    Probe = _profile()
    assert Probe in client.QuadCortex._PROFILES
    assert Probe.CC_VERSION == "4.0.1" and Probe.DEVICE_TYPE == pa.VersionMessage.QC


def test_every_operation_is_guarded_on_a_subclass_that_verifies_nothing(forget_probes):
    Probe = _profile()
    for name in client.QuadCortex.operations():
        assert getattr(getattr(Probe, name), "_unverified", False), name
    for name in client.QuadCortex.ALWAYS:
        assert not getattr(getattr(Probe, name), "_unverified", False), name


def test_a_guarded_operation_refuses_under_verified_with_all_three_fields(forget_probes):
    Probe = _profile()
    fake = FakeTransport()
    qc = Probe(fake)
    with pytest.raises(errors.ControlNotDrivable) as caught:
        qc.set_scene_label(0, "x")
    err = caught.value
    assert err.control == "set_scene_label"
    assert err.evidence == "not yet verified on Probe (CorOS 9.9.9)"
    assert "--verifies set_scene_label" in err.workaround
    assert fake.sent == []  # nothing reached the wire


def test_a_guarded_operation_runs_and_warns_once_under_experimental(forget_probes, caplog):
    Probe = _profile()
    fake = FakeTransport()
    qc = Probe(fake, support=support.Support.EXPERIMENTAL)
    with caplog.at_level(logging.WARNING, logger="pyquadcortex.protocol.client"):
        qc.switch_scene(1)
        qc.switch_scene(2)
    assert len(fake.sent) == 2, "the inherited method ran both times"
    warnings = [r for r in caplog.records if "switch_scene" in r.getMessage()]
    assert len(warnings) == 1, "one warning per operation name per instance"
    assert "not yet verified on Probe" in warnings[0].getMessage()


def test_the_warning_is_once_per_connection_not_once_per_process(forget_probes, caplog):
    """`_warned` is per instance, so a second connection warns again.

    The warning says an operation is unverified and is running anyway, which is
    something the person at THIS connection needs told. A set on the class
    would say it once for the life of the process and leave every later
    connection running unverified operations silently.
    """
    Probe = _profile()
    first = Probe(FakeTransport(), support=support.Support.EXPERIMENTAL)
    second = Probe(FakeTransport(), support=support.Support.EXPERIMENTAL)
    with caplog.at_level(logging.WARNING, logger="pyquadcortex.protocol.client"):
        first.switch_scene(1)
        second.switch_scene(1)
    warnings = [r for r in caplog.records if "switch_scene" in r.getMessage()]
    assert len(warnings) == 2, "each connection is told once"


def test_a_verified_operation_is_not_guarded_and_an_override_is_left_alone(forget_probes):
    class Probe(client.QuadCortex):
        MEASURED_ON = ("9.9.9",)
        EVIDENCE = support.Evidence.STUB
        VERIFIED = frozenset({"switch_scene", "set_scene_label"})

        def set_scene_label(self, scene, label):
            return "mine"

    assert not getattr(Probe.switch_scene, "_unverified", False)
    assert Probe(FakeTransport()).set_scene_label(0, "x") == "mine"


def test_an_override_must_also_be_listed_in_verified(forget_probes):
    """An override IS the subclass's measured behaviour, so the set must say so:
    VERIFIED stays the single statement of what the class knows."""
    with pytest.raises(TypeError, match="overrides set_scene_label but does not list it"):
        type("Probe", (client.QuadCortex,), {
            "MEASURED_ON": ("9.9.9",), "EVIDENCE": support.Evidence.STUB,
            "VERIFIED": frozenset(),
            "set_scene_label": lambda self, scene, label: None})


def test_a_rejected_subclass_is_never_registered():
    """__init_subclass__ raises before touching _PROFILES; the rejected class
    must not appear in it at all - not under its own name, not partially
    guarded. Compares the list before/after rather than filtering by name,
    so this does not rely on the `forget_probes` fixture's name filter."""
    before = list(client.QuadCortex._PROFILES)
    with pytest.raises(TypeError, match="overrides set_scene_label but does not list it"):
        type("RejectedProbe", (client.QuadCortex,), {
            "MEASURED_ON": ("9.9.9",), "EVIDENCE": support.Evidence.STUB,
            "VERIFIED": frozenset(),
            "set_scene_label": lambda self, scene, label: None})
    assert client.QuadCortex._PROFILES == before


def test_a_profile_must_subclass_quadcortex_directly(forget_probes):
    """A sub-subclass would be guarded against the BASE implementation.

    `__init_subclass__` installs `_guarded(name, getattr(QuadCortex, name))`,
    so a class two levels down that does not list a name in its own VERIFIED
    gets the base's method wrapped - silently discarding the measured override
    its parent profile made. ADR-0020 is one class per measured unit, so the
    case is refused rather than made to work.
    """
    Parent = _profile(verified=frozenset({"switch_scene"}))
    before = list(client.QuadCortex._PROFILES)
    with pytest.raises(TypeError, match="subclass QuadCortex directly"):
        type("DeeperProbe", (Parent,), {
            "MEASURED_ON": ("9.9.9",), "EVIDENCE": support.Evidence.STUB,
            "VERIFIED": frozenset()})
    assert client.QuadCortex._PROFILES == before


def test_a_public_staticmethod_is_never_guarded(forget_probes, monkeypatch):
    """`operations()` counts plain functions, so a static helper is not one.

    A `staticmethod` object is callable, so the older predicate made a public
    one an operation - and the guard would then wrap it and pass `self` as its
    first argument, breaking a method that touches no device at all.
    """
    monkeypatch.setattr(client.QuadCortex, "describe_wire_format",
                        staticmethod(lambda: "a helper, not an operation"),
                        raising=False)
    assert "describe_wire_format" not in client.QuadCortex.operations()
    Probe = _profile()
    assert not getattr(Probe.describe_wire_format, "_unverified", False)
    assert Probe.describe_wire_format() == "a helper, not an operation"


def test_set_blocks_refusal_says_so_when_the_profile_measured_no_firmware(forget_probes):
    """A stub profile has an empty `MEASURED_ON`, and the refusal used to build
    its own text: `not in this unit's catalog (CorOS )`. One renderer, so the
    parenthesis is never empty."""
    from pyquadcortex.protocol import catalog

    Probe = type("Probe", (client.QuadCortex,), {
        "MEASURED_ON": (), "EVIDENCE": support.Evidence.STUB,
        "VERIFIED": frozenset({"set_block"})})
    qc = Probe(FakeTransport())
    qc._catalog = catalog.parse_model_repo(b'<?xml version="1.0"?><ModelRepo/>')

    with pytest.raises(errors.ControlNotDrivable) as caught:
        qc.set_block(client.Block(0, 3, 6026), verify=False)

    assert "(no firmware measured)" in caught.value.evidence
    assert "CorOS )" not in caught.value.evidence


def test_unverified_operations_is_empty_on_the_base_and_full_on_a_stub(forget_probes):
    assert client.QuadCortex(FakeTransport()).unverified_operations == frozenset()
    Probe = _profile(verified=frozenset({"switch_scene"}))
    got = Probe(FakeTransport()).unverified_operations
    assert got == client.QuadCortex.operations() - {"switch_scene"}


def test_support_defaults_to_verified_and_is_readable():
    assert client.QuadCortex(FakeTransport()).support is support.Support.VERIFIED
    qc = client.QuadCortex(FakeTransport(), support=support.Support.EXPERIMENTAL)
    assert qc.support is support.Support.EXPERIMENTAL


def test_the_4_1_profile_exposes_only_operations_with_contributed_evidence():
    cls = profiles.QuadCortex41
    assert issubclass(cls, client.QuadCortex)
    assert cls.MEASURED_ON == ("4.1.0",)
    assert cls.EVIDENCE is support.Evidence.CONTRIBUTED
    assert cls.VERIFIED == frozenset({"preset_screenshot"})
    assert cls.CC_VERSION == "4.0.1", "inherited: the contributor's runs announced 4.0.1"
    assert isinstance(cls.models, support.NoSnapshot)
    with pytest.raises(AttributeError, match="coros_4_1_0"):
        cls.models.Delay
    assert cls(FakeTransport()).unverified_operations == (
        client.QuadCortex.operations() - {"preset_screenshot"}
    )


def test_the_mini_stub_is_recognised_but_cannot_connect():
    cls = profiles.QuadCortexMini
    assert cls.DEVICE_TYPE == pa.VersionMessage.ATMA
    assert cls.MEASURED_ON == ()
    assert cls.EVIDENCE is support.Evidence.STUB
    assert cls.HARDWARE == support.Hardware(footswitches=4, expression_ports=2)
    assert cls in profiles.stubs()
    assert cls not in profiles.registry().values()


def test_the_registry_has_one_entry_per_measured_version():
    reg = profiles.registry()
    assert reg[(pa.VersionMessage.QC, "4.0.1")] is client.QuadCortex
    assert reg[(pa.VersionMessage.QC, "4.1.0")] is profiles.QuadCortex41
    assert all(len(k) == 2 for k in reg)


def _reply(device_type, coros):
    return pa.VersionMessage(action=pa.MessageAction.UPDATE, device_type=device_type,
                             zenos_git_hash=coros, device_serial_number="QA00EE910")


def test_resolve_maps_known_pairs_to_their_class():
    assert profiles.resolve(_reply(pa.VersionMessage.QC, "4.0.1")) is client.QuadCortex
    assert profiles.resolve(_reply(pa.VersionMessage.QC, "4.1.0")) is profiles.QuadCortex41


def test_resolve_refuses_an_unknown_firmware_naming_what_exists_without_taking_it():
    with pytest.raises(profiles.UnsupportedDevice) as caught:
        profiles.resolve(_reply(pa.VersionMessage.QC, "4.2.0"))
    err = caught.value
    assert (err.device_type, err.coros_version) == (pa.VersionMessage.QC, "4.2.0")
    text = str(err)
    assert "QC, CorOS 4.2.0" in text
    assert "4.0.1" in text and "4.1.0" in text
    assert "--profile QuadCortex41" in text and "pytest tests/hardware --hardware" in text
    assert "connect(profile=QuadCortex41" in text and "Support.EXPERIMENTAL" in text


def test_resolve_refuses_a_mini_naming_the_stub_and_how_to_start():
    with pytest.raises(profiles.UnsupportedDevice) as caught:
        profiles.resolve(_reply(pa.VersionMessage.ATMA, "1.0.0"))
    text = str(caught.value)
    assert "Quad Cortex Mini" in text and "QuadCortexMini" in text
    assert "not yet supported" in text
    assert "--profile QuadCortexMini" in text and "pytest tests/hardware --hardware" in text
    assert "connect(profile=QuadCortexMini" in text and "Support.EXPERIMENTAL" in text


def test_resolve_refuses_a_reply_with_no_identity():
    with pytest.raises(profiles.UnsupportedDevice, match="did not report"):
        profiles.resolve(pa.VersionMessage(action=pa.MessageAction.UPDATE))


def test_resolve_refuses_a_reply_carrying_only_the_device_type():
    """Half an identity is not an identity. The registry is keyed on the pair,
    and protobuf answers `""` for a `zenos_git_hash` the unit never sent - so
    without the presence check this would look up (QC, "") and refuse with a
    message naming a firmware the unit never claimed."""
    with pytest.raises(profiles.UnsupportedDevice, match="did not report"):
        profiles.resolve(pa.VersionMessage(action=pa.MessageAction.UPDATE,
                                           device_type=pa.VersionMessage.QC))


def test_resolve_refuses_a_reply_carrying_only_the_coros_version():
    """The other half, which protobuf answers as device_type 0 - a real enum
    member, so the lookup would silently mean a device the unit never named."""
    with pytest.raises(profiles.UnsupportedDevice, match="did not report"):
        profiles.resolve(pa.VersionMessage(action=pa.MessageAction.UPDATE,
                                           zenos_git_hash="4.0.1"))


def test_the_nearest_profile_offered_is_the_newest_by_version_not_by_string():
    """"4.10.0" is newer than "4.9.0" and sorts below it as a string.

    The hint tells somebody which profile to measure their unit as, so the
    string comparison this replaces offered them the older one - and did it on
    exactly the version numbers a project reaches after nine patch releases.
    """
    older = type("OlderProbe", (client.QuadCortex,), {
        "MEASURED_ON": ("4.9.0",), "EVIDENCE": support.Evidence.STUB,
        "VERIFIED": frozenset()})
    newer = type("NewerProbe", (client.QuadCortex,), {
        "MEASURED_ON": ("4.10.0",), "EVIDENCE": support.Evidence.STUB,
        "VERIFIED": frozenset()})
    try:
        with pytest.raises(profiles.UnsupportedDevice) as caught:
            profiles.resolve(_reply(pa.VersionMessage.QC, "4.11.0"))
        assert "profile=NewerProbe" in str(caught.value)
    finally:
        client.QuadCortex._PROFILES[:] = [
            c for c in client.QuadCortex._PROFILES if c not in (older, newer)]


def test_version_key_never_compares_a_number_against_a_string():
    """A pre-release suffix ("4.1.0-rc1") keeps its last segment a string.

    Comparing that key against a plain numeric one ("4.1.0") used to raise
    TypeError - int() vs str() - inside the refusal path that is supposed to
    explain a version mismatch, not add its own. The numeric release must
    still sort above the pre-release.
    """
    numeric = profiles._version_key("4.1.0")
    prerelease = profiles._version_key("4.1.0-rc1")
    assert prerelease < numeric
    assert numeric > prerelease
