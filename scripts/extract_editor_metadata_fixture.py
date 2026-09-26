#!/usr/bin/env python3
"""Distil editor-layout evidence from a device ModelRepo.

    python scripts/extract_editor_metadata_fixture.py --payload model_repo.bin

The full vendor catalog is not redistributed.  The fixture contains only the
raw attributes needed to reproduce the midpoint, display-position, and
conditional-editor facts asserted by the offline suite.
"""

import argparse
import collections
import json
import pathlib
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from pyquadcortex.protocol import catalog  # noqa: E402


EDITOR_ATTRIBUTES = {
    "name", "min_string", "mid_string", "max_string", "skew", "displayPos",
    "isplayPos", "toggleOn", "toggleOff", "toggleStep",
}


def _row(model, index, parameter):
    return {
        "model_id": int(model.get("id")),
        "model": model.get("name"),
        "index": index,
        "raw": {
            key: value for key, value in parameter.attrib.items()
            if key in EDITOR_ATTRIBUTES
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", required=True)
    parser.add_argument(
        "--out", default="tests/fixtures/catalog/editor_metadata.json")
    args = parser.parse_args()

    payload = pathlib.Path(args.payload).read_bytes()
    root = ET.fromstring(catalog._extract_xml(payload))
    models = list(root.iter("Model"))
    all_parameters = [
        (model, index, parameter)
        for model in models
        for index, parameter in enumerate(model.findall("Parameter"))
    ]

    midpoint_rows = [
        _row(model, index, parameter)
        for model, index, parameter in all_parameters
        if parameter.get("mid_string") is not None
    ]

    collision_rows = []
    gap_models = []
    display_carriers = 0
    for model in models:
        positions = collections.defaultdict(list)
        for index, parameter in enumerate(model.findall("Parameter")):
            raw = parameter.get("displayPos")
            if raw is None:
                continue
            display_carriers += 1
            try:
                positions[int(raw)].append(_row(model, index, parameter))
            except ValueError:
                continue
        duplicates = {
            str(position): rows for position, rows in positions.items()
            if len(rows) > 1
        }
        if duplicates:
            collision_rows.append({
                "model_id": int(model.get("id")),
                "model": model.get("name"),
                "positions": duplicates,
            })
        # Use the explicit all-parameter basis: every missing integer in
        # 0..max(displayPos) is a gap. This counts all 533 CorOS 4.0.1 models,
        # including the two hidden models PCOM Tape Delay (ST) and Chief DC2W
        # PCOM; excluding them gives 19 rather than 21 gap models.
        if positions:
            missing = sorted(set(range(max(positions) + 1)) - set(positions))
            if missing:
                gap_models.append({
                    "model_id": int(model.get("id")),
                    "model": model.get("name"),
                    "missing": missing,
                })

    toggle_rows = {
        attribute: [
            _row(model, index, parameter)
            for model, index, parameter in all_parameters
            if parameter.get(attribute) is not None
        ]
        for attribute in ("toggleOn", "toggleOff", "toggleStep")
    }
    self_references = []
    for attribute in ("toggleOn", "toggleOff"):
        for row in toggle_rows[attribute]:
            indexes = {
                int(part.strip()) for part in row["raw"][attribute].split(",")
                if part.strip().lstrip("-").isdigit()
            }
            if row["index"] in indexes:
                self_references.append({
                    **row, "attribute": attribute,
                })

    typo = next(
        _row(model, index, parameter)
        for model, index, parameter in all_parameters
        if parameter.get("isplayPos") is not None
    )

    fixture = {
        "source": "Quad Cortex CorOS 4.0.1 ModelRepo payload",
        "firmware": "CorOS 4.0.1",
        "parameters": len(all_parameters),
        "mid_string": {
            "rows": midpoint_rows,
        },
        "displayPos": {
            "carriers": display_carriers,
            "collision_models": collision_rows,
            "gap_models": gap_models,
            "device_typo": typo,
        },
        "toggle": {
            "counts": {
                attribute: len(rows)
                for attribute, rows in toggle_rows.items()
            },
            "step_rows": toggle_rows["toggleStep"],
            "all_step_carriers_have_on_or_off": all(
                "toggleOn" in row["raw"] or "toggleOff" in row["raw"]
                for row in toggle_rows["toggleStep"]
            ),
            "self_references": self_references,
        },
    }

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
    print(
        f"wrote {len(midpoint_rows)} midpoint rows, "
        f"{len(collision_rows)} collision models, and "
        f"{len(self_references)} self-references to {out}")


if __name__ == "__main__":
    main()
