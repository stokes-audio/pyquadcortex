"""The device profile seam (ADR-0020, spec 2026-09-03)."""
import importlib
import logging

import pytest

from pyquadcortex.protocol import client, errors, models, options, params, support
from pyquadcortex.protocol.catalogs import coros_4_0_1
from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa
from tests.test_client import FakeTransport  # the offline transport double


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


def test_operations_are_every_public_method_not_in_always():
    ops = client.QuadCortex.operations()
    public = {n for n, v in vars(client.QuadCortex).items()
              if not n.startswith("_") and (callable(v) or isinstance(v, property))}
    assert ops == frozenset(public - set(client.QuadCortex.ALWAYS))
    assert "set_scene_label" in ops and "version" not in ops
    assert len(ops) > 100


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


def test_unverified_operations_is_empty_on_the_base_and_full_on_a_stub(forget_probes):
    assert client.QuadCortex(FakeTransport()).unverified_operations == frozenset()
    Probe = _profile(verified=frozenset({"switch_scene"}))
    got = Probe(FakeTransport()).unverified_operations
    assert got == client.QuadCortex.operations() - {"switch_scene"}


def test_support_defaults_to_verified_and_is_readable():
    assert client.QuadCortex(FakeTransport()).support is support.Support.VERIFIED
    qc = client.QuadCortex(FakeTransport(), support=support.Support.EXPERIMENTAL)
    assert qc.support is support.Support.EXPERIMENTAL
