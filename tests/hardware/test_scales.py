"""The scale fixture still matches the unit's own catalog.

`tests/fixtures/catalog/scales.json` holds the bounds, taper and floor of every
parameter the offline scale tests assert on. It is distilled from a device
rather than committed whole, so like the generated constants it is a snapshot
and nothing offline can notice it going stale.

A failure here means the fixture is out of date. Regenerate and READ the diff:

    python scripts/extract_scale_fixture.py --payload <a saved payload>

A changed bound or taper is a real protocol change and belongs in
`docs/protocol.md` alongside the readings it invalidates.
"""
import json
import pathlib
import xml.etree.ElementTree as ET

import pytest

from pyquadcortex.protocol import catalog, units

REPO = pathlib.Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "catalog" / "scales.json"


@pytest.fixture(scope="module")
def live_catalog(qc):
    return catalog.parse_model_repo(qc._fetch_model_repo())


@pytest.fixture(scope="module")
def live_xml(qc):
    return ET.fromstring(catalog._extract_xml(qc._fetch_model_repo()))


def _raw_attrs(qc, model_id: int, index: int) -> dict:
    """One parameter's XML attributes, straight off the unit."""
    root = ET.fromstring(catalog._extract_xml(qc._fetch_model_repo()))
    for category in root.findall("Category"):
        for element in category.findall("Model"):
            if int(element.get("id", -1)) == model_id:
                return dict(element.findall("Parameter")[index].attrib)
    raise AssertionError(f"no model {model_id} on this unit")


@pytest.mark.parametrize("row", json.loads(FIXTURE.read_text()),
                         ids=lambda r: f"{r['model_id']}.{r['index']}")
def test_the_fixture_still_matches_this_unit(qc, live_catalog, row):
    """Read-only: nothing is written to the unit, so no restore is needed."""
    model = live_catalog[row["model_id"]]
    p = model.parameters[row["index"]]
    # The RAW attributes are the device's own words, so this is a genuine
    # comparison against the unit. The resolved columns are not: `minimum` for a
    # symbolic bound, and `floor_wire` always, are read out of `units.py`, so
    # comparing them here would be comparing units.py against a copy of itself.
    # They are checked in the offline suite, where the fixture's raw attributes
    # are re-parsed; here what matters is that the device still says the same
    # thing.
    raw = _raw_attrs(qc, row["model_id"], row["index"])
    assert raw == row["raw"], (
        f"{model.name!r} {p.name!r}: this unit's catalog no longer matches the "
        f"committed fixture.\n  fixture: {row['raw']}\n  this unit: {raw}\n"
        f"A changed bound, taper or step count is a PROTOCOL CHANGE and belongs "
        f"in docs/protocol.md alongside the readings it invalidates. Regenerate "
        f"with scripts/extract_scale_fixture.py only once you know which it is - "
        f"regenerating first would bless the change silently.")
    assert (p.name, p.units, p.type) == (row["name"], row["units"], row["type"])


def test_every_symbolic_bound_this_unit_ships_is_known(live_catalog):
    """The guard that stops the placeholder bug coming back.

    `catalog._as_bound` raises for a constant name this build has never met, so
    parsing the live catalog at all proves the two tables cover it. What this
    adds is the reverse direction: that we are not carrying numbers for
    constants the unit stopped using.
    """
    assert live_catalog, "the unit's catalog parsed, so every bound resolved"
    unmeasured = [(m.id, m.name, p.name) for m in live_catalog
                  for p in m.parameters
                  if p.minimum is None or p.maximum is None]
    assert unmeasured == [(20000, "NC_Recorder", "OUT LEVEL")], (
        f"the set of unmeasured bounds changed: {unmeasured}")
    for name in list(units.FIRMWARE_CONSTANTS) + list(units.UNMEASURED_BOUNDS):
        assert name.startswith(("MIN_", "MAX_")), name
