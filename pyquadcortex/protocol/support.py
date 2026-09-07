"""The words a device profile is described in (ADR-0020).

A PROFILE is a client class: ``QuadCortex`` for a Quad Cortex on CorOS 4.0.1,
a subclass for each other unit somebody has measured. This module holds the
vocabulary those classes declare themselves with and the one function that
words a refusal, so the text cannot drift between the places that use it.

It imports nothing from the package on purpose: ``client.py`` needs these
names to guard its methods, and the profile classes need ``client.py``, so
this is the module both can import without a cycle.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, Sequence


class Support(Enum):
    """How a connection treats an operation its profile has not verified.

    ``VERIFIED`` (the default) refuses it with ``ControlNotDrivable``.
    ``EXPERIMENTAL`` runs the inherited behaviour and logs one warning per
    operation. The hardware suite always connects ``EXPERIMENTAL``: on a new
    profile it is the thing doing the verifying.
    """

    VERIFIED = "verified"
    EXPERIMENTAL = "experimental"


class Evidence(Enum):
    """How well a profile is known.

    ``MAINTAINER``: measured on the maintainer's own unit. ``CONTRIBUTED``:
    measured by a contributor and not reproduced by the maintainer. ``STUB``:
    nothing measured; the class exists to show where the measurements go.
    """

    MAINTAINER = "maintainer"
    CONTRIBUTED = "contributed"
    STUB = "stub"


class _Everything:
    """The ``VERIFIED`` value meaning "every operation" - ``QuadCortex`` uses it."""

    def __contains__(self, name: object) -> bool:
        return True

    def __repr__(self) -> str:
        return "EVERYTHING"


EVERYTHING = _Everything()


@dataclass(frozen=True)
class Hardware:
    """Facts about the unit's hardware that the model layer needs.

    Declared ahead of its first reader: nothing reads it yet, and the intended
    consumer is the model layer's footswitch and expression translation
    (``pyquadcortex/device/translate/letters.py``), which hard-codes eight
    letters through ``enums.Footswitch`` and will read this instead once a
    profile with a different count has been measured on a unit.

    Extended only when a measurement needs a new field; a fact nobody reads
    is a guess with a name.
    """

    footswitches: int
    expression_ports: int


class NoSnapshot:
    """What a profile binds instead of a constants snapshot it does not have yet.

    Binding a stub to the 4.0.1 snapshot would hand a 4.1 user names their
    unit does not use, so the stub binds this and the first attribute access
    says how to generate the real thing.
    """

    def __init__(self, name: str):
        self._name = name

    def __getattr__(self, attr: str) -> object:
        raise AttributeError(
            f"no catalog snapshot {self._name!r} yet; run "
            f"`scripts/generate_models.py --snapshot {self._name}` (and the "
            f"params and options generators) against a unit on that firmware "
            f"and bind the result on the profile class")

    def __repr__(self) -> str:
        return f"NoSnapshot({self._name!r})"


class _ProfileClass(Protocol):
    """The interface expected of a profile class by unverified_text."""

    __name__: str
    MEASURED_ON: tuple[str, ...]


def measured_firmware(measured_on: Sequence[str]) -> str:
    """``"CorOS 4.0.1"``, or ``"no firmware measured"`` when there is none.

    The word "CorOS" belongs to the version, not to the sentence around it, so
    it is written here and nowhere else: a stub profile has an empty
    ``MEASURED_ON``, and a caller told their model is "not in this unit's
    catalog (CorOS )" learns nothing from the empty parenthesis. Every message
    that names a profile's firmware goes through this.

    Takes the versions rather than the profile class: a class OBJECT cannot be
    matched against a protocol carrying ``__name__``, so a signature naming the
    class would have cost a type-checker suppression at the one call site that
    has a real class rather than an ``Any``.
    """
    firmware = ", ".join(measured_on)
    return f"CorOS {firmware}" if firmware else "no firmware measured"


def unverified_text(cls: type[_ProfileClass], name: str) -> tuple[str, str]:
    """The evidence and the workaround for an operation ``cls`` has not verified.

    One function, used by the refusal, the experimental-mode warning and the
    connect-time hint, so they cannot say three different things.
    """
    class_name = cls.__name__
    evidence = f"not yet verified on {class_name} ({measured_firmware(cls.MEASURED_ON)})"
    workaround = (
        f"connect(support=Support.EXPERIMENTAL) to try it, or run "
        f"`pytest tests/hardware --hardware --verifies {name}` on your unit "
        f"and add the result to {class_name}.VERIFIED")
    return evidence, workaround
