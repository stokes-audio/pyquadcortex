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

import pytest

from pyquadcortex.protocol import catalog, units

REPO = pathlib.Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "catalog" / "scales.json"


@pytest.fixture(scope="module")
def live_catalog(qc):
    return catalog.parse_model_repo(qc._fetch_model_repo())


@pytest.mark.parametrize("row", json.loads(FIXTURE.read_text()),
                         ids=lambda r: f"{r['model_id']}.{r['index']}")
def test_the_fixture_still_matches_this_unit(live_catalog, row):
    """Read-only: nothing is written to the unit, so no restore is needed."""
    model = live_catalog[row["model_id"]]
    p = model.parameters[row["index"]]
    actual = {"name": p.name, "minimum": p.minimum, "maximum": p.maximum,
              "units": p.units, "type": p.type, "steps": p.steps,
              "skew": p.skew, "floor_wire": p.floor_wire}
    expected = {k: row[k] for k in actual}
    assert actual == expected, (
        f"{model.name!r} {p.name!r} no longer matches the committed fixture. "
        f"Regenerate with scripts/extract_scale_fixture.py and read the diff.")


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
