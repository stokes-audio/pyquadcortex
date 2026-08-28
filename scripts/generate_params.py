#!/usr/bin/env python3
"""Generate ``pyquadcortex/protocol/params.py`` - parameter constants per model.

A parameter is written by its wire INDEX. Indices are positional and several are
not visible knobs, so writing one by number is how you change stored data and
move nothing on screen. These constants make the index a name.

Source: a device's ModelRepo payload, either live or previously saved.

    # from a connected Quad Cortex (Cortex Control must be quit)
    python scripts/generate_params.py

    # from a saved payload, for reproducible regeneration
    python scripts/generate_params.py --payload model_repo_payload.bin

Three things this generator knows that the catalog does not:

1. **The catalog UNDER-DESCRIBES cabs.** It lists two parameters for a cab
   model - the two mic selectors - while the wire carries 22. Measured on four
   cabs across all four categories (Bass/Guitar, M/ST): every one is the
   ``Default Cabsim`` layout. So the 140 cab models share ONE layout and get one
   enum, rather than 140 two-member enums that would each hide 20 parameters.
2. **A cab's repeated block is a MICROPHONE**, not an IR slot or a channel.
   Confirmed against the unit's own editor: mic 1 read POSITION 2.9 / DIST 3.0
   and mic 2 read POSITION 5.6 / DIST 3.3, matching wire indices 5 and 13 at
   0.29/0.30 and 0.56/0.33. That is what maps a mic to an index; ordering alone
   would have been an assumption.
3. **An IR Loader's repeated block IS two IR slots**, which
   ``QuadCortex.set_ir(cell, ir, slot=)`` already drives at indices 2 and 10.

Only FACTORY content is emitted, for the same reason ``models.py`` does: a unit
may not have a purchased model, and capture ids are user slots. Resolve those at
runtime through ``qc.catalog``.
"""
import argparse
import keyword
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pyquadcortex.protocol import catalog  # noqa: E402

#: The cab layout every cab model actually uses on the wire. Its repeated
#: eight-parameter block is a microphone.
CABSIM_LAYOUT = 12000
CABSIM_CATEGORIES = ("Cabsim Guitar (M)", "Cabsim Guitar (ST)",
                     "Cabsim Bass (M)", "Cabsim Bass (ST)")

#: Models whose duplicated parameter names are a repeated group, and what one
#: repetition of that group IS. Anything else with duplicates is a surprise and
#: stops the generator rather than being numbered blindly.
GROUPED = {
    "IRLoaders": "IR",
}

#: The containers a target addresses. These are `internal` models, so
#: `factory_models()` never yields them, but they are the parameters a caller
#: reaches through `LaneOutput`, `LaneInput`, `Mixer`, `Splitter` and `Tempo` -
#: the ones most worth naming.
CONTAINERS = [
    ("LaneOutputParam", 23000, "A row's Lane Output Control - `LaneOutput(row)`."),
    ("LaneInputParam", 28000, "A row's Input Gate Control - `LaneInput(row)`."),
    ("MixerParam", 11000, "A row's Mixer - `Mixer(row)`."),
    ("SplitterParam", 10004, "A row's splitter - `Splitter(row)`."),
    ("SplitterABParam", 10000, "The older two-parameter splitter view."),
    ("TempoParam", 25000, "The preset's TempoControl - `Tempo()`."),
]


def class_name(model: str) -> str:
    """'Chief DS1' -> 'ChiefDs1'; '810 Amped VT (M)' -> 'N810AmpedVtM'."""
    cleaned = re.sub(r"[^0-9a-zA-Z ]+", " ", model)
    name = "".join(word[:1].upper() + word[1:].lower() for word in cleaned.split())
    if not name:
        name = "Unnamed"
    if name[0].isdigit():
        name = "N" + name
    if keyword.iskeyword(name.lower()):
        name += "_"
    return name


def member_name(param: str) -> str:
    """'NOISE REDUCTION' -> 'NOISE_REDUCTION'; 'ir selector' -> 'IR_SELECTOR'."""
    cleaned = re.sub(r"[^0-9a-zA-Z]+", "_", param).strip("_").upper()
    if not cleaned:
        cleaned = "UNNAMED"
    if cleaned[0].isdigit():
        cleaned = "N" + cleaned
    if keyword.iskeyword(cleaned.lower()):
        cleaned += "_"
    return cleaned


def members(model, group: str = None) -> list[tuple[str, int, str]]:
    """``(name, index, comment)`` for each parameter, duplicates disambiguated.

    A name the device publishes more than once is numbered by OCCURRENCE, and
    both occurrences are numbered - a bare first member would read like the real
    one and hide that it is one of a pair. ``group`` supplies the word: MIC for a
    cab, IR for an IR Loader.
    """
    counts: dict[str, int] = {}
    for p in model.parameters:
        counts[p.name] = counts.get(p.name, 0) + 1
    if group is None and any(n > 1 for n in counts.values()):
        raise SystemExit(
            f"{model.id} {model.name!r} publishes a duplicate parameter name and "
            f"is not in GROUPED, so there is no honest way to number it. Find out "
            f"what one repetition IS before regenerating."
        )

    seen: dict[str, int] = {}
    out = []
    for p in model.parameters:
        base = member_name(p.name)
        if counts[p.name] > 1:
            seen[p.name] = seen.get(p.name, 0) + 1
            # Do not stutter: an IR Loader's "IR PATH" is IR_1_PATH, not
            # IR_1_IR_PATH, because the group word is already the prefix.
            stem = base[len(group) + 1:] if base.startswith(f"{group}_") else base
            name = f"{group}_{seen[p.name]}_{stem}"
        else:
            name = base
        units = f" {p.units}" if p.units else ""
        out.append((name, p.index, f"{p.type}{units}"))
    return out


#: Which unit marker a catalog `units` string maps to.
#:
#: The three the value types decline - `x`, `bits`, `dB/oct`, two parameters
#: each - map to `NoUnit` for the same reason `values.of_unit` hands them a
#: plain `Real`: there is no type to name. Note this stretches `NoUnit` past
#: what its own docstring claims, which is that the parameter HAS no unit; for
#: these six it means "has one, and nothing models it". Six parameters is not
#: worth a second marker, but the difference should not be silent.
UNIT_TYPES = {
    "dB": "DbUnit", "%": "PercentUnit", "Hz": "HertzUnit",
    "ms": "MillisecondsUnit", "s": "SecondsUnit",
    "Semitones": "SemitonesUnit", "st": "SemitonesUnit",
    "Cents": "CentsUnit", "cents": "CentsUnit", "BPM": "BpmUnit",
}


def render_enum(cls: str, doc: str, model, group=None) -> list[str]:
    lines = ["", "", f"class {cls}(ParamSet):", f'    """{doc}"""', ""]
    used = set()
    by_index = {p.index: p for p in model.parameters}
    for name, index, comment in members(model, group):
        if name in used:                       # same name AND same spelling twice
            name = f"{name}_{index}"
        used.add(name)
        spec = by_index.get(index)
        unit = UNIT_TYPES.get(spec.units if spec else "", "NoUnit")
        lines.append(f"    {name}: Param[{unit}] = Param({index}, {name!r})"
                     f"    # {comment}")
    return lines


def render(cat: catalog.ModelCatalog) -> str:
    lines = [
        '"""Parameter constants for the factory blocks every Quad Cortex has.',
        "",
        "GENERATED by scripts/generate_params.py - do not edit by hand.",
        "",
        "Each class is a model and each member a parameter's wire index, so a",
        "member goes straight to :meth:`pyquadcortex.protocol.QuadCortex.set_param`::",
        "",
        "    qc.set_param(LaneOutput(0), params.LaneOutputParam.VOLUME, Db(-3.1))",
        "",
        "A constant IS its wire index - `Param` subclasses `int` - so passing",
        "one needs no catalog, which makes it the cheapest route as well as the",
        "clearest, since a catalog is fetched from the device.",
        "",
        "It also carries the parameter's UNIT in its type, so a type checker",
        "rejects `set_param(LaneOutputParam.VOLUME, Hertz(217))` before it runs",
        "(ADR-0018). The runtime check is unchanged and still covers every other",
        "caller - a string, a bare index, or anyone not running a checker.",
        "",
        "**Cabs share one layout.** The catalog lists two parameters for a cab -",
        "its two mic selectors - while the wire carries 22. So a cab is CHOSEN by",
        "its `models.*` id and DRIVEN through :class:`Cabsim`::",
        "",
        "    cab = Block(0, 5, models.CabsimBassM.N212_DARKGLASS_NEO_M)",
        "    qc.set_block(cab)",
        "    qc.set_param(cab, params.Cabsim.MIC_1_DISTANCE, Real(3.0))",
        "",
        "A name the device publishes twice is numbered by occurrence, and BOTH",
        "occurrences are numbered. For a cab that repetition is a MICROPHONE and",
        "for an IR Loader it is an IR slot; both are measured, not assumed.",
        "",
        "Only FACTORY content is here, as in `models.py`. Resolve anything else",
        "through ``qc.catalog``.",
        '"""',
        "from pyquadcortex.protocol.values import (BpmUnit, CentsUnit, DbUnit,",
        "                                          HertzUnit, MillisecondsUnit,",
        "                                          NoUnit, Param, PercentUnit,",
        "                                          SecondsUnit, SemitonesUnit)",
        "",
        "",
        "class _ParamSetMeta(type):",
        '    """The `IntEnum` surface worth keeping, on a plain class.',
        "",
        "    These were `IntEnum`s until ADR-0018. An enum member's type is the",
        "    enum class, so it cannot carry a PER-MEMBER unit, and a model's",
        "    parameters have mixed units - which is the whole reason the shape",
        "    changed. What callers actually used of `IntEnum` is iteration,",
        "    lookup by name and `__members__`, so those are here; the rest of",
        "    the enum machinery was never reached.",
        '    """',
        "",
        "    @property",
        "    def __members__(cls):",
        "        return {k: v for k, v in vars(cls).items()",
        "                if isinstance(v, Param)}",
        "",
        "    def __iter__(cls):",
        "        return iter(cls.__members__.values())",
        "",
        "    def __getitem__(cls, name):",
        "        return cls.__members__[name]",
        "",
        "    def __len__(cls):",
        "        return len(cls.__members__)",
        "",
        "    def __contains__(cls, item):",
        "        return item in cls.__members__.values()",
        "",
        "",
        "class ParamSet(metaclass=_ParamSetMeta):",
        '    """One model\'s parameters. A namespace, not a value."""',
        "",
        "    def __init__(self):",
        "        raise TypeError(",
        "            f\"{type(self).__name__} is a namespace of parameter \"",
        "            f\"constants, not something to instantiate - use its \"",
        "            f\"members, such as {type(self).__name__}.<NAME>\")",
    ]

    lines += ["", "", "# -- the containers a target addresses " + "-" * 40]
    for cls, model_id, doc in CONTAINERS:
        lines += render_enum(cls, doc, cat[model_id])

    lines += ["", "", "# -- cabs: one layout, shared by every cab model " + "-" * 30]
    lines += render_enum(
        "Cabsim",
        "Every cab model's parameters. The catalog under-describes these.\n\n"
        "    Measured on four cabs across all four categories: the wire carries\n"
        "    22 parameters in the `Default Cabsim` layout regardless of which cab\n"
        "    is loaded, or whether it is mono or stereo. `MIC_1_PAN` is labelled\n"
        "    BALANCE on a stereo cab and PAN on a mono one - one wire index, two\n"
        "    screen names.\n\n"
        "    The mic-to-index mapping was confirmed against the unit's own\n"
        "    editor. Index 21 exists on the wire, is absent from the catalog and\n"
        "    reads 0.0 everywhere, so it is omitted rather than guessed at.\n    ",
        cat[CABSIM_LAYOUT], group="MIC")

    lines += ["", "", "# -- every other factory model " + "-" * 47]
    described = [m for m in cat.factory_models()
                 if m.parameters and m.category not in CABSIM_CATEGORIES]
    used: dict[str, int] = {}
    index_lines = []
    for model in sorted(described, key=lambda m: (m.superseded, m.id)):
        cls = class_name(model.name)
        if model.superseded:
            cls += "Legacy"
        if cls in used:
            cls = f"{cls}{model.id}"
        used[cls] = model.id
        lines += render_enum(cls, f"{model.name} ({model.category}).", model,
                             group=GROUPED.get(model.category))
        index_lines.append(f"    {model.id}: {cls},")

    lines += ["", "", "#: Every generated parameter set, keyed by its model id.",
              "BY_MODEL = {"] + index_lines + ["}", ""]
    return "\n".join(lines)


def load_payload(path):
    if path:
        return pathlib.Path(path).read_bytes()
    from pyquadcortex.protocol import connect
    with connect() as qc:
        return qc._fetch_model_repo()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--payload", help="a saved ModelRepo payload")
    ap.add_argument("--out", default="pyquadcortex/protocol/params.py")
    args = ap.parse_args()

    cat = catalog.parse_model_repo(load_payload(args.payload))
    text = render(cat)
    pathlib.Path(args.out).write_text(text)
    print(f"wrote {args.out} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
