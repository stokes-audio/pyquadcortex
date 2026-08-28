#!/usr/bin/env python3
"""Distil the parameters under test out of a device ModelRepo into a fixture.

    python scripts/extract_scale_fixture.py --payload model_repo_payload.bin

The offline suite has to prove that the device's own catalog reproduces every
reading taken off the unit's screen. That needs real numbers, and the obvious
way to get them - committing ``ModelRepo.xml`` - would republish 556 KB of the
vendor's product catalog: every model name, every impulse-response name, every
knob. ``tests/test_catalog.py`` has said "ships no vendor data" since it was
written, and this keeps that true.

So the fixture holds only the parameters the tests actually assert on: for each,
its bounds, its taper and its floor. That is the data under test and nothing
else. ``tests/hardware/test_scales_on_unit.py`` proves the live catalog still agrees
with it, which is what stops the fixture drifting away from the device.

Regenerate whenever a reading is added to ``tests/test_scales.py`` for a
parameter not already listed in ``WANTED`` below.
"""

import argparse
import json
import pathlib
import xml.etree.ElementTree as ET
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from pyquadcortex.protocol import catalog  # noqa: E402

#: ``(model id, wire index)`` for every parameter the offline scale tests touch.
#: Each entry says why it is here, because an entry nobody can justify is one
#: nobody will dare delete.
WANTED = {
    (11000, 0): "Mixer LEVEL A - scene-following, and briefly mistaken for undrivable",
    (11000, 2): "Mixer LEVEL B - the other half of that pair",
    (3008, 16): "Parallax cab LEVEL - a Bass Overdrive carrying a cab section, so"
                " it cannot borrow the layout and must carry the law itself",
    (3008, 24): "Parallax cab LEVEL, mic 2",
    (12114, 25): "PCOM Core Cabsim LEVEL - the same knob with LITERAL bounds,"
                 " which is what the floor's law-keying exists for",
    (32000, 2): "Default Cabsim (ST) LEVEL - a STEREO cab, because all three"
                " screen-measured blocks are mono and 86 of 174 cabs are not",
    (13001, 0): "Send 2 LEVEL - proves the send family is more than one model",
    (13003, 0): "Return 2 LEVEL - and the return family likewise",
    (12000, 2): "cab MIC 1 LEVEL - the taper, skew 4.9594844",
    (12000, 10): "cab MIC 2 LEVEL - proves both mics share the layout",
    (4000, 0): "Parametric-8 band 1 GAIN - the EQ family, steps=241",
    (4003, 1): "Low-High Cut HPF FREQ - skew 0.3, the below-1 direction",
    (4003, 3): "Low-High Cut LPF FREQ - the same skew at the other end",
    (4003, 4): "Low-High Cut OUTPUT - no skew, the linear control case",
    (24003, 5): "Envelope Filter FREQ - LOG_SKEW, which is not a log sweep",
    (24003, 7): "Envelope Filter RESO - LOG_SKEW in different units",
    (25000, 0): "TEMPO - MIN_TEMPO, steps=201",
    (23000, 0): "lane output VOLUME - the MIN_MIXER_DB family",
    (11000, 5): "MIXER LEVEL - the same family, measured separately",
    (10004, 3): "splitter LEVEL TO A - the same family again",
    (10004, 4): "splitter LEVEL TO B",
    (10004, 5): "Splitter Crossover FREQUENCY - MIN_EQ_FREQ, solved not measured",
    (10000, 0): "legacy Splitter AB view - must share the unified splitter's span",
    (10000, 1): "legacy Splitter AB view, the B side",
    (13000, 0): "Send LEVEL - MIN_FXLOOP_OUT_GAIN_DB, which tops out at unity",
    (13002, 0): "FX Loop LEVEL - MIN_FXLOOP_IN_GAIN_DB, which can boost",
    (20000, 2): "NC_Recorder OUT LEVEL - the one unmeasured bound; refuses",
}


def _raw_attrs(payload: bytes, model_id: int, index: int) -> dict:
    """The XML attributes of one parameter, exactly as the device wrote them."""
    root = ET.fromstring(catalog._extract_xml(payload))
    for category in root.findall("Category"):
        for element in category.findall("Model"):
            if int(element.get("id", -1)) != model_id:
                continue
            params = element.findall("Parameter")
            if index < len(params):
                return dict(params[index].attrib)
    raise SystemExit(f"no parameter {model_id}[{index}] in this catalog")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--payload", required=True,
                    help="a ModelRepo payload or ModelRepo.xml from a device")
    ap.add_argument("--out", default="tests/fixtures/catalog/scales.json")
    args = ap.parse_args()

    payload = pathlib.Path(args.payload).read_bytes()
    cat = catalog.parse_model_repo(payload)

    rows = []
    for (model_id, index), reason in sorted(WANTED.items()):
        model = cat.get(model_id)
        if model is None:
            raise SystemExit(f"this unit's catalog has no model {model_id}")
        if index >= len(model.parameters):
            raise SystemExit(
                f"model {model_id} {model.name!r} has no parameter at index "
                f"{index} (it has {len(model.parameters)})")
        p = model.parameters[index]
        rows.append({
            "model_id": model_id,
            "model": model.name,
            "index": index,
            "name": p.name,
            # The raw XML, so the offline test can re-parse it. Without this the
            # fixture records ALREADY-RESOLVED numbers and the evidence loop is
            # readings -> fixture -> readings, with units.FIRMWARE_CONSTANTS
            # outside it: six of the fourteen constants could be edited to
            # anything and the whole offline suite stayed green.
            "raw": {k: v for k, v in _raw_attrs(payload, model_id, index).items()},
            "minimum": p.minimum,
            "maximum": p.maximum,
            "units": p.units,
            "type": p.type,
            "steps": p.steps,
            "skew": p.skew,
            "floor_wire": p.floor_wire,
            "floor_display": p.floor_display,
            "why": reason,
        })

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2) + "\n")
    print(f"wrote {len(rows)} parameters to {out}")


if __name__ == "__main__":
    main()
