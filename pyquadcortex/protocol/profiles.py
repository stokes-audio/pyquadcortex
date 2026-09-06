"""The device profiles this library knows, and how a connection picks one.

A profile is a client class (ADR-0020). ``QuadCortex`` in ``client.py`` is the
Quad Cortex on CorOS 4.0.1 and the base of every other profile. This module
holds the two profiles we know are coming and have not measured, and the
registry ``connect()`` resolves through. Adding a profile is: subclass
``QuadCortex``, declare the class attributes, and the registry sees it.
"""
from __future__ import annotations

from pyquadcortex.protocol.client import QuadCortex
from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa
from pyquadcortex.protocol.support import Evidence, Hardware, NoSnapshot

_DEVICE_NAMES = {pa.VersionMessage.QC: "Quad Cortex",
                 pa.VersionMessage.ATMA: "Quad Cortex Mini"}


class QuadCortex41(QuadCortex):
    """Quad Cortex on CorOS 4.1 - connects, and verifies nothing yet.

    PR #42's description (2026-09-03) reports ``pytest --hardware``: 2742
    passed, 8 skipped, on a Quad Cortex running CorOS 4.1.0 / app firmware
    d14e, by tony-xmelon. That is a contributor's report and the maintainer has
    not reproduced it, which is what ``Evidence.CONTRIBUTED`` says here. The
    connection is therefore known to work with this handshake and announce
    string. Which operations behave as on 4.0.1 is not known per name, so every
    inherited operation refuses under ``Support.VERIFIED`` and runs with a
    warning under ``Support.EXPERIMENTAL``. The snapshot is deliberately absent:
    binding the 4.0.1 constants would hand a 4.1 user names their unit does not
    use.

    To finish this profile, on a 4.1 unit:

    1. ``scripts/generate_models.py --snapshot coros_4_1_0`` and the params and
       options generators; bind the three modules below.
    2. ``pytest tests/hardware --hardware --profile QuadCortex41``; the report
       at the end lists the operations whose tests passed. Put those names in
       ``VERIFIED``.
    3. Record any operation that behaved differently in ``docs/protocol.md``
       beside the 4.0.1 record, dated and named, and override it here.
    """

    MEASURED_ON = ("4.1.0",)
    EVIDENCE = Evidence.CONTRIBUTED
    VERIFIED = frozenset()
    models = NoSnapshot("coros_4_1_0")
    params = NoSnapshot("coros_4_1_0")
    options = NoSnapshot("coros_4_1_0")


class QuadCortexMini(QuadCortex):
    """Quad Cortex Mini - recognised, not supported, and here to be finished.

    Nothing has been measured: no Mini has connected through this library. The
    schema names it (``DeviceType.ATMA``, ``Mode.atma_page``, ``AtmaPowerOnMode``),
    which is inference from field names, not a measurement. Four footswitches
    is from the product page. Because ``MEASURED_ON`` is empty this class is
    never resolved; a Mini refuses to connect with a message naming this class.

    To start, on a Mini: ``pytest tests/hardware --hardware --profile
    QuadCortexMini``. That connects as this class rather than the one the unit
    resolves to - which is nothing, so a plain run refuses - and the suite
    connects ``Support.EXPERIMENTAL`` itself. If the handshake works, send the
    report at the end of the run. Expect the eight-footswitch assumptions in
    ``QuadCortex`` to need overrides here, and expect this class to want a
    different base than ``QuadCortex`` once its shape is known.
    """

    DEVICE_TYPE = pa.VersionMessage.ATMA
    MEASURED_ON = ()
    EVIDENCE = Evidence.STUB
    HARDWARE = Hardware(footswitches=4, expression_ports=2)
    VERIFIED = frozenset()
    models = NoSnapshot("coros_mini")
    params = NoSnapshot("coros_mini")
    options = NoSnapshot("coros_mini")


class UnsupportedDevice(Exception):
    """The unit is not one this library has a measured profile for.

    Raised at connect, before the handshake. The message names what the unit
    said and what is registered. It never picks a profile for the caller: the
    ``profile=`` argument is how a person chooses to measure an unknown unit.
    """

    def __init__(self, device_type, coros_version, message):
        super().__init__(message)
        self.device_type = device_type
        self.coros_version = coros_version


def _all_profiles() -> list[type[QuadCortex]]:
    return [QuadCortex, *QuadCortex._PROFILES]


def registry() -> dict[tuple[int, str], type[QuadCortex]]:
    """``(device_type, coros_version) -> class``, one entry per measured version."""
    reg: dict[tuple[int, str], type[QuadCortex]] = {}
    for cls in _all_profiles():
        for coros in cls.MEASURED_ON:
            reg[(cls.DEVICE_TYPE, coros)] = cls
    return reg


def stubs() -> list[type[QuadCortex]]:
    """Profiles that cannot be resolved because nothing was measured on them."""
    return [cls for cls in _all_profiles() if not cls.MEASURED_ON]


def _describe(cls: type[QuadCortex]) -> str:
    level = {Evidence.MAINTAINER: "verified on the maintainer's unit",
             Evidence.CONTRIBUTED: "contributed, not verified by the maintainer",
             Evidence.STUB: "stub"}[cls.EVIDENCE]
    return f"{_DEVICE_NAMES.get(cls.DEVICE_TYPE, cls.DEVICE_TYPE)} {', '.join(cls.MEASURED_ON)} ({level})"


def resolve(reply: pa.VersionMessage) -> type[QuadCortex]:
    """The class for the unit that sent ``reply``, or ``UnsupportedDevice``."""
    if not (reply.HasField("device_type") and reply.HasField("zenos_git_hash")):
        raise UnsupportedDevice(None, None,
                                "the unit did not report device_type and zenos_git_hash in "
                                "its Version reply, so no profile can be chosen")
    key = (reply.device_type, reply.zenos_git_hash)
    found = registry().get(key)
    if found is not None:
        return found
    device = _DEVICE_NAMES.get(reply.device_type, pa.VersionMessage.DeviceType.Name(reply.device_type))
    known = "; ".join(_describe(c) for c in _all_profiles() if c.MEASURED_ON)
    same_device = [c for c in _all_profiles() if c.DEVICE_TYPE == reply.device_type]
    if same_device and all(c.EVIDENCE is Evidence.STUB for c in same_device):
        stub = same_device[0]
        hint = (f"The {device} is recognised and not yet supported; {stub.__name__} is the "
                f"stub to finish. To start: connect(profile={stub.__name__}, "
                f"support=Support.EXPERIMENTAL), then `pytest tests/hardware --hardware` "
                f"and send the report. See its docstring.")
    elif same_device:
        nearest = max(same_device, key=lambda c: c.MEASURED_ON)
        hint = (f"Pass profile={nearest.__name__} to treat it as "
                f"{', '.join(nearest.MEASURED_ON)} while you measure it, with "
                f"support=Support.EXPERIMENTAL; then add {reply.zenos_git_hash!r} to "
                f"{nearest.__name__}.MEASURED_ON.")
    else:
        hint = "No profile exists for this device type."
    raise UnsupportedDevice(
        reply.device_type, reply.zenos_git_hash,
        f"the unit reports {pa.VersionMessage.DeviceType.Name(reply.device_type)}, "
        f"CorOS {reply.zenos_git_hash}; this library has profiles for: {known}. {hint}")
