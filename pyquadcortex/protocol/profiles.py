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
    """Quad Cortex on CorOS 4.1, with contributed per-operation evidence.

    PR #42's description (2026-09-03) reports ``pytest --hardware``: 2742
    passed, 8 skipped, on a Quad Cortex running CorOS 4.1.0 / app firmware
    d14e, by tony-xmelon. That is a contributor's report and the maintainer has
    not reproduced it, which is what ``Evidence.CONTRIBUTED`` says here. The
    connection is therefore known to work with this handshake and announce
    string. ``capture_screen`` and ``tap_screen`` have dated 4.1.0 evidence and
    are VERIFIED; other inherited operations refuse under ``Support.VERIFIED``
    and run with a warning under ``Support.EXPERIMENTAL``. The snapshot is deliberately absent:
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
    VERIFIED = frozenset({"capture_screen", "tap_screen"})
    models = NoSnapshot("coros_4_1_0")
    params = NoSnapshot("coros_4_1_0")
    options = NoSnapshot("coros_4_1_0")

    def capture_screen(self, timeout: float = 10.0) -> bytes:
        """Return the CorOS 4.1 physical-display PNG."""
        return self._capture_screen(timeout=timeout)

    def tap_screen(self, x: float, y: float) -> None:
        """Tap a CorOS 4.1 physical-screen pixel coordinate."""
        self._tap_screen(x, y)


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


def _version_key(version: str) -> tuple:
    """"4.10.0" sorts above "4.9.0", which it does not as a string.

    Compared per part, as integers where the part is one: a CorOS version is
    dotted numbers today, and the string comparison this replaces called 4.9.0
    the newest of the two - so the hint below offered the older profile as the
    nearest. A part that is not a number (a pre-release suffix such as
    "0-rc1") is kept as a string, tagged behind every numeric part so a
    numeric part never compares against a string part - Python raises
    `TypeError` on that, and a profile carrying one release candidate would
    crash the refusal path that is supposed to explain the version mismatch,
    not add its own. Tagging a numeric part ahead of a string one also keeps
    the plain release sorting above its own pre-release at the same position.
    """
    return tuple((1, int(part)) if part.isdigit() else (0, part)
                 for part in version.split("."))


def _describe(cls: type[QuadCortex]) -> str:
    level = {Evidence.MAINTAINER: "verified on the maintainer's unit",
             Evidence.CONTRIBUTED: "contributed, not verified by the maintainer",
             Evidence.STUB: "stub"}.get(cls.EVIDENCE, cls.EVIDENCE.name.lower())
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
                f"stub to finish. To start: `pytest tests/hardware --hardware "
                f"--profile {stub.__name__}` and send the report (a Python caller can "
                f"also connect(profile={stub.__name__}, support=Support.EXPERIMENTAL)). "
                f"See its docstring.")
    elif same_device:
        # `default=()` for a stub among them: it has measured nothing, and an
        # empty tuple sorts below every version, which is where it belongs.
        nearest = max(same_device,
                      key=lambda c: max((_version_key(v) for v in c.MEASURED_ON),
                                        default=()))
        hint = (f"Pass `--profile {nearest.__name__}` to `pytest tests/hardware "
                f"--hardware` to treat it as {', '.join(nearest.MEASURED_ON)} while you "
                f"measure it (a Python caller can also connect(profile={nearest.__name__}, "
                f"support=Support.EXPERIMENTAL)); then add {reply.zenos_git_hash!r} to "
                f"{nearest.__name__}.MEASURED_ON.")
    else:
        hint = "No profile exists for this device type."
    raise UnsupportedDevice(
        reply.device_type, reply.zenos_git_hash,
        f"the unit reports {pa.VersionMessage.DeviceType.Name(reply.device_type)}, "
        f"CorOS {reply.zenos_git_hash}; this library has profiles for: {known}. {hint}")
