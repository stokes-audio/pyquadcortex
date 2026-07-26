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
purchased plugin models, and Neural Captures the player made themselves - which
is exactly the content no hard-coded table could know about.

Which models are "factory" matters for the generated constants in
:mod:`pyquadcortex.models`: only models every unit is guaranteed to have belong
there. :attr:`Model.is_factory` encodes that rule (see the class docstring).
"""

from __future__ import annotations

import gzip
import io
import tarfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

# Categories holding Neural Captures. These are user content: the ids are slots,
# so the same id means a different capture on a different unit (or after the
# player overwrites one). They must never become hard-coded constants.
_CAPTURE_CATEGORY_IDS = frozenset({14, 20})


@dataclass(frozen=True)
class Parameter:
    """One knob of a model, at its wire index.

    ``index`` is what :meth:`pyquadcortex.QuadCortex.set_param` addresses. Note
    that not every index is a visible knob: a cab's parameters are internal
    ``ir selector`` entries, for instance, so writing one changes stored data
    without moving anything on screen.
    """

    index: int
    name: str
    minimum: float
    maximum: float
    default: float
    units: str = ""
    type: str = ""
    steps: int | None = None


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
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


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
                    minimum=_as_float(p.get("min")),
                    maximum=_as_float(p.get("max"), 1.0),
                    default=_as_float(p.get("defaultValue")),
                    units=p.get("units", ""),
                    type=p.get("type", ""),
                    steps=_as_int(p.get("steps")),
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
            )
    return catalog
