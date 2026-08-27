"""The device's block catalog: what models exist, and what knobs they have.

A Quad Cortex identifies a grid block by an integer (``BinaryPreset.Model.hash``
on the wire). On its own that integer says nothing - you cannot tell 5005 is a
compressor, and you cannot tell which parameter index is "THRESHOLD". The device
resolves this itself with a **model repository** it sends to a connecting client:
a gzipped tar holding one ``ModelRepo.xml``, listing every model installed on
*that unit*, grouped into categories, each with its parameters in wire-index
order and their ranges.

This module turns that payload into a :class:`ModelCatalog`. Because it comes
from the device, the catalog automatically covers content that is not built in -
purchased plugin models in particular.

It does NOT enumerate Neural Captures. The Neural Capture category holds only a
couple of entries and does not grow when a capture is saved: a capture BLOCK is one
of those models and the capture it plays is a string parameter naming a library
file. Use :meth:`pyquadcortex.protocol.QuadCortex.captures` to browse what is available.

Which models are "factory" matters for the generated constants in
:mod:`pyquadcortex.protocol.models`: only models every unit is guaranteed to have belong
there. :attr:`Model.is_factory` encodes that rule (see the class docstring).
"""

from __future__ import annotations

import gzip
import io
import tarfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace

# The numbers behind the catalog's symbolic bounds. `units` imports nothing from
# this module, so this direction is the only one and there is no cycle.
from pyquadcortex.protocol import units

# Categories holding Neural Captures, kept out of the generated constants.
#
# These ids are capture BLOCK types, not individual captures. A block's model id says
# only "this is a Neural Capture"; WHICH capture it plays is a string parameter,
# `file_name`, holding the library file's 64-character content hash followed by its
# display name. Saving a new capture does not add a model here.
#
# That explains an earlier puzzle honestly. Thirteen of seventeen factory presets
# reference id 14000 from positions no single capture could fill - the amp slot in one,
# a pedal slot ahead of a real amp in another - which had been read as evidence that a
# capture id was a per-unit SLOT. It is not: they all use the same block model with
# different `file_name` strings.
_CAPTURE_CATEGORY_IDS = frozenset({14, 20})

#: A knob linear in its own units. The catalog spells this three ways - the
#: name, ``1`` and ``1.0`` - and 583 parameters use one of them.
LIN_SKEW = 1.0

#: ``LOG_SKEW`` is NOT a logarithmic sweep, despite the name. It is the same
#: power law every other parameter uses, at skew 0.3.
#:
#: Solved on hardware 2026-08-26 from two readings on an Envelope Filter, chosen
#: because its two ``LOG_SKEW`` knobs cover different ranges in different units:
#: ``FREQ`` (100..10000 Hz) read **197 Hz** at wire 0.25 and ``RESO`` (1..10)
#: read **4.45** at wire 0.75. Those give exponents 3.3366 and 3.3330
#: independently, both ``1/0.3``. A true log sweep would have shown 316 Hz and
#: 5.62; a linear one, 2575 and 7.75.
#:
#: The name is the device's, and it is misleading. Do not "fix" this to a log
#: law without driving one of the 16 parameters that carry it.
LOG_SKEW = 0.3


def parse_skew(raw: str | None) -> float:
    """The taper from the catalog's ``skew`` attribute, as a positive float.

    Anything unrecognised falls back to :data:`LIN_SKEW`, which is what an absent
    attribute means. The shipped catalog needs that tolerance: two parameters
    carry ``" 0.4"`` with a leading space and two carry ``""``.
    """
    if raw is None:
        return LIN_SKEW
    text = raw.strip()
    if text == "LIN_SKEW":
        return LIN_SKEW
    if text == "LOG_SKEW":
        return LOG_SKEW
    try:
        value = float(text)
    except ValueError:
        return LIN_SKEW
    return value if value > 0.0 else LIN_SKEW


@dataclass(frozen=True)
class Parameter:
    """One knob of a model, at its wire index.

    ``index`` is what :meth:`pyquadcortex.protocol.QuadCortex.set_param` addresses. Note
    that not every index is a visible knob: a cab's parameters are internal
    ``ir selector`` entries, for instance, so writing one changes stored data
    without moving anything on screen.
    """

    index: int
    name: str
    #: ``None`` where the catalog names a bound nobody has measured - see
    #: :data:`~pyquadcortex.protocol.units.UNMEASURED_BOUNDS`. Such a parameter
    #: refuses to convert rather than answering with an invented number.
    minimum: float | None
    maximum: float | None
    default: float
    units: str = ""
    type: str = ""
    steps: int | None = None
    #: The taper, from the XML's ``skew``. ``real = min + (max - min) * wire **
    #: (1 / skew)``. 1.0 is a straight line, which is what an absent attribute
    #: means; 617 parameters carry something else. See :func:`parse_skew`.
    skew: float = LIN_SKEW
    #: The lowest wire position with a NUMERIC display, where the bottom of the
    #: range is an OFF detent instead. 0.0 where every position is a number. See
    #: :data:`~pyquadcortex.protocol.units.FLOOR_WIRE`, which is where the
    #: measurement lives.
    floor_wire: float = 0.0

    @property
    def floor(self) -> float | None:
        """The lowest value this parameter will actually DISPLAY.

        Usually :attr:`minimum`, but not where the bottom of the scale is an Off
        position: a cab LEVEL's law runs to -40 dB and its quietest real setting
        is -21.8 dB.
        """
        if self.minimum is None or self.maximum is None:
            return None
        return self.to_real(self.floor_wire)

    @property
    def option_count(self) -> int | None:
        """How many options a list-valued parameter offers, or None.

        For a ``comboBox`` or ``switch`` the catalog's ``steps`` IS the option
        count, and the wire value of option N is ``N / (count - 1)``. Confirmed
        against the tempo controls: NOTELENGTH has ``steps=4`` and selecting the
        second option stored 0.3333 (1/3), TIME SIGNATURE has ``steps=21`` and the
        second option stored 0.05 (1/20), ROUTING has ``steps=5`` and the fourth
        stored 0.75 (3/4).

        **Not reliable for a parameter whose options enumerate the preset's
        blocks** - a Doubler's TRIGGER publishes ``steps=45`` while the real list is
        19 to 25 entries depending on the preset. For those, read the list from the
        preset with :func:`pyquadcortex.protocol.param_options`, which is authoritative.

        ``empty`` counts too, and only because the catalog is small enough to check
        exhaustively: 16 parameters carry that type, the 13 ``STEPSTATE`` per-beat
        metronome cells with ``steps=4`` and three ``DUMMY`` entries with no steps
        at all. So requiring ``steps`` admits exactly the beats and nothing else.
        Without this, the per-beat cells were unreachable through
        :meth:`QuadCortex.set_tempo_option` despite the count sitting right there
        in the catalog.
        """
        if self.type in ("comboBox", "switch", "rotarySwitch", "empty") and self.steps:
            return self.steps
        return None

    def option_to_value(self, option: int) -> float:
        """The wire value that selects option ``option`` of this parameter."""
        count = self.option_count
        if count is None:
            raise ValueError(
                f"{self.name!r} is a {self.type or 'plain'} parameter, not a list, "
                f"so it has no options - pass a value instead"
            )
        if not 0 <= option < count:
            raise ValueError(
                f"{self.name!r} has {count} options (0 to {count - 1}), "
                f"got {option}"
            )
        return 0.0 if count == 1 else option / (count - 1)

    def value_to_option(self, value: float) -> int:
        """Which option a wire value selects."""
        count = self.option_count
        if count is None:
            raise ValueError(f"{self.name!r} is not a list parameter")
        return 0 if count == 1 else round(value * (count - 1))

    def to_normalized(self, real: float) -> float:
        """Convert a value in this parameter's own units to the wire's 0..1.

        Applies the parameter's :attr:`skew`, so this is a straight line only
        where the catalog says it is. Confirmed on hardware: the wire carries a
        normalized float. Sending 1.0 to a THRESHOLD whose catalog range is
        -60..+12 dB made the unit read +12.0 dB.

        A value the knob has no position for is REFUSED rather than clamped -
        see :meth:`_reject_outside_range`.

        Raises ``ValueError`` for a parameter whose bounds the catalog names
        and nobody has measured, rather than returning a number that would
        quietly mean something else - see :meth:`_reject_unmeasured`.
        """
        if isinstance(real, bool):
            raise TypeError(
                f"a real value is a number, not {real!r}. A bool IS an int in "
                f"Python, so without this True would quietly write the top of "
                f"{self.name!r}'s range and look deliberate."
            )
        self._reject_unmeasured()
        span = self.maximum - self.minimum
        if span == 0:
            return 0.0
        self._reject_outside_range(real)
        fraction = min(1.0, max(0.0, (real - self.minimum) / span))
        return fraction ** self.skew

    def _reject_outside_range(self, real: float):
        """Refuse a value the knob has no position for, rather than clamping.

        A silently clamped write looks like it worked and lands somewhere else,
        so asking for a setting the unit does not have is an error rather than a
        nudge to the nearest one.

        The bottom of the range is :attr:`floor`, not :attr:`minimum`, and the
        difference is the whole reason this is here: a cab LEVEL's law runs to
        -40 dB while its quietest real position is -21.8 dB, so -30 dB converts
        to wire 0.0005 and MUTES the microphone.
        """
        low, high = self.floor, self.maximum
        if low is None or low <= real <= high:
            return
        unit = f" {self.units}" if self.units else ""
        hint = f" ({units.OFF_HINT})" if self.floor_wire > 0.0 else ""
        raise ValueError(
            f"{self.name!r} runs {round(low, 1):g}..{high:g}{unit} on the unit; "
            f"{real:g}{unit} does not exist there.{hint}"
        )

    def to_real(self, normalized: float) -> float:
        """Convert a wire 0..1 value back into this parameter's own units.

        ``real = min + (max - min) * wire ** (1 / skew)``. Confirmed on hardware
        2026-08-26 over three unrelated blocks in two different units: a cab
        LEVEL at skew 4.9594844 (wire 0.01/0.50/1.00 read -21.8/0.0/6.0 dB), a
        Low-High Cut HPF FREQ at skew 0.3 (wire 0.25 read 217 Hz), and the same
        block's OUTPUT with no skew (wire 0.25 read -10.0 dB).

        Raises ``ValueError`` for a parameter whose bounds the catalog names and
        nobody has measured - see :meth:`_reject_unmeasured`.
        """
        self._reject_unmeasured()
        span = self.maximum - self.minimum
        if span == 0:
            return self.minimum
        clamped = min(1.0, max(0.0, normalized))
        return self.minimum + span * clamped ** (1.0 / self.skew)

    def _reject_unmeasured(self):
        """Refuse rather than convert against a bound nobody has measured."""
        if self.minimum is None or self.maximum is None:
            raise ValueError(
                f"{self.name!r} has a bound the catalog names but nobody has "
                f"measured, so there is nothing honest to convert with - see "
                f"units.UNMEASURED_BOUNDS. Pass the normalized 0..1 the wire "
                f"carries instead."
            )


@dataclass(frozen=True)
class Model:
    """One block type: an amp, a pedal, a cab, a capture."""

    id: int
    name: str
    category: str
    category_id: int
    based_on: str = ""
    parameters: tuple[Parameter, ...] = ()
    sku: str | None = None
    plugin_id: str | None = None
    hidden: bool = False
    internal: bool = False
    category_hidden: bool = False
    #: Ids of older models this one supersedes (the XML ``replaces`` attribute).
    replaces: tuple[int, ...] = ()
    #: True if a NEWER model replaces this one. Superseded models stay in the
    #: catalog - old presets still reference them - but the replacement is the
    #: one you want when building a new chain, and it is the one that earns the
    #: clean generated constant name (the two "Graphic-9" equalizers, 4005
    #: replaces 4002, are why this matters).
    superseded: bool = False

    @property
    def is_factory(self) -> bool:
        """True if every Quad Cortex is guaranteed to have this model.

        False for anything a given unit might lack or number differently:
        purchasable plugin content (``sku``/``plugin_id`` - the Archetype
        models), models or categories the firmware hides, internal routing
        helpers, and Neural Captures (user content in slot-numbered ids).
        Only factory models get generated constants; everything else must be
        looked up at runtime through the catalog.
        """
        return not (
            self.sku
            or self.plugin_id
            or self.hidden
            or self.internal
            or self.category_hidden
            or self.category_id in _CAPTURE_CATEGORY_IDS
        )

    def parameter(self, name: str) -> Parameter:
        """Return the parameter called ``name`` (case-insensitive)."""
        wanted = name.strip().lower()
        for p in self.parameters:
            if p.name.lower() == wanted:
                return p
        raise KeyError(
            f"model {self.name!r} ({self.id}) has no parameter {name!r}; "
            f"it has {[p.name for p in self.parameters]}"
        )


@dataclass
class ModelCatalog:
    """Every model installed on the device, keyed by its wire id."""

    models: dict[int, Model] = field(default_factory=dict)

    def __getitem__(self, model_id: int) -> Model:
        try:
            return self.models[int(model_id)]
        except KeyError:
            raise KeyError(f"no model with id {model_id} in this device's catalog") from None

    def __iter__(self):
        return iter(self.models.values())

    def __len__(self) -> int:
        return len(self.models)

    def get(self, model_id: int, default=None):
        """Like ``dict.get``: the model, or ``default`` if the id is unknown."""
        return self.models.get(int(model_id), default)

    def find(self, name: str) -> Model:
        """Return the model called ``name`` (case-insensitive, exact match)."""
        wanted = name.strip().lower()
        for model in self.models.values():
            if model.name.lower() == wanted:
                return model
        raise KeyError(f"no model named {name!r} in this device's catalog")

    def by_category(self, category: str) -> list[Model]:
        """All models in ``category`` (case-insensitive), in catalog order."""
        wanted = category.strip().lower()
        return [m for m in self.models.values() if m.category.lower() == wanted]

    def categories(self) -> list[str]:
        """Category names, in catalog order, without duplicates."""
        seen = {}
        for m in self.models.values():
            seen.setdefault(m.category, None)
        return list(seen)

    def factory_models(self) -> list[Model]:
        """Only the models every unit is guaranteed to have."""
        return [m for m in self.models.values() if m.is_factory]


def _as_float(value, fallback=0.0) -> float:
    """A lenient number, for attributes where a string is legitimate.

    ``defaultValue`` is the reason this stays lenient: on a cab it is an IR
    name, not a number ("NG_412 Plini Cab_Dynamic 57"), and 58 parameters carry
    an empty one. Nothing converts with a default, so a fallback costs nothing.
    Bounds are different - see :func:`_as_bound`.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    return units.FIRMWARE_CONSTANTS.get(str(value).strip(), fallback)


def _as_bound(value, fallback, where: str):
    """A parameter's ``min`` or ``max``, which must never be guessed.

    Returns ``None`` for a bound the device names and nobody has measured; the
    parameter then refuses to convert rather than converting against a made-up
    number. Raises for a name this build has never heard of, because silently
    falling back to ``0.0`` and ``1.0`` is exactly what invented the
    "placeholder range" bug - see :data:`~pyquadcortex.protocol.units.FIRMWARE_CONSTANTS`.
    """
    if value is None:
        return fallback
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        pass
    if text in units.FIRMWARE_CONSTANTS:
        return units.FIRMWARE_CONSTANTS[text]
    if text in units.UNMEASURED_BOUNDS:
        return None
    raise ValueError(
        f"the catalog names a bound this build has no number for: {text!r} "
        f"on {where}. Measure it and add it to units.FIRMWARE_CONSTANTS with "
        f"the evidence, or record it in units.UNMEASURED_BOUNDS with the "
        f"reason nobody can. Falling back to 0..1 is what created the "
        f"placeholder-range bug this replaced."
    )


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_xml(payload: bytes) -> bytes:
    """Get the ModelRepo XML out of whatever container the device sent.

    The device sends gzip(tar(ModelRepo.xml)). Accept the intermediate forms too
    so a caller holding already-decompressed bytes, or a bare XML file, works.
    """
    if payload[:2] == b"\x1f\x8b":
        payload = gzip.decompress(payload)
    if payload.lstrip()[:1] == b"<":
        return payload
    with tarfile.open(fileobj=io.BytesIO(payload)) as tf:
        name = next((n for n in tf.getnames() if n.endswith(".xml")), None)
        if name is None:
            raise ValueError("ModelRepo payload contains no .xml member")
        extracted = tf.extractfile(name)
        if extracted is None:
            raise ValueError(f"ModelRepo member {name!r} is not a regular file")
        return extracted.read()


def parse_model_repo(payload: bytes) -> ModelCatalog:
    """Parse a device ModelRepo payload into a :class:`ModelCatalog`."""
    root = ET.fromstring(_extract_xml(payload))
    catalog = ModelCatalog()
    for category in root.findall("Category"):
        category_id = _as_int(category.get("id"))
        category_name = category.get("name", "")
        category_hidden = category.get("hidden") is not None
        for element in category.findall("Model"):
            model_id = _as_int(element.get("id"))
            if model_id is None:
                continue
            parameters = tuple(
                Parameter(
                    index=i,
                    name=p.get("name", ""),
                    minimum=_as_bound(p.get("min"), 0.0,
                                      f"{element.get('name')!r} {p.get('name')!r}"),
                    maximum=_as_bound(p.get("max"), 1.0,
                                      f"{element.get('name')!r} {p.get('name')!r}"),
                    default=_as_float(p.get("defaultValue")),
                    units=p.get("units", ""),
                    type=p.get("type", ""),
                    steps=_as_int(p.get("steps")),
                    skew=parse_skew(p.get("skew")),
                    floor_wire=units.FLOOR_WIRE.get(str(p.get("min")).strip(), 0.0),
                )
                for i, p in enumerate(element.findall("Parameter"))
            )
            catalog.models[model_id] = Model(
                id=model_id,
                name=element.get("name", ""),
                category=category_name,
                category_id=category_id if category_id is not None else -1,
                based_on=element.get("tm", ""),
                parameters=parameters,
                sku=element.get("sku"),
                plugin_id=element.get("plugin_id"),
                hidden=element.get("hidden") is not None,
                internal=element.get("internal") is not None,
                category_hidden=category_hidden,
                replaces=_parse_replaces(element.get("replaces")),
            )

    # Second pass: a model is superseded once some other model claims to replace
    # it. Only knowable after everything is parsed.
    replaced = {old for model in catalog.models.values() for old in model.replaces}
    for model_id in replaced & catalog.models.keys():
        catalog.models[model_id] = replace(catalog.models[model_id], superseded=True)
    return catalog


def _parse_replaces(value: str | None) -> tuple[int, ...]:
    """Parse a ``replaces`` attribute: one id, or several comma-separated."""
    if not value:
        return ()
    ids = []
    for part in value.split(","):
        parsed = _as_int(part.strip())
        if parsed is not None:
            ids.append(parsed)
    return tuple(ids)
