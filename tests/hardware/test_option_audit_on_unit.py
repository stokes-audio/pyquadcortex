"""The audited option lists still match the catalog this unit ships.

`tests/fixtures/catalog/option_readings.json` holds what a human read off the
screen, and `options.OPTION_AUDIT` stamps each list from it. Both are pinned to
a SNAPSHOT of the catalog, and like the generated constants nothing offline can
notice that snapshot going stale - a firmware that renames an option would leave
every offline test green while the library published a word the unit no longer
uses, now with "audited" beside it, which is worse than never having checked.

So this holds the readings against the catalog the unit is running right now.
It does not re-read the screen: that needs eyes, and the readings record it
once. What it proves is that the thing that was read is still the thing that
ships - the model still exists, the parameter is still at that index, and it
still offers exactly those words in that order.

A failure here is a finding about the device, not a fixture to relax. Re-read the
changed list on the unit before touching the recorded screen text.
"""
import collections
import json
import pathlib

import pytest

from pyquadcortex.protocol import catalog, options

REPO = pathlib.Path(__file__).resolve().parents[2]
READINGS = REPO / "tests" / "fixtures" / "catalog" / "option_readings.json"


@pytest.fixture(scope="module")
def live_catalog(qc):
    return catalog.parse_model_repo(qc._fetch_model_repo())


@pytest.fixture(scope="module")
def rows():
    return [r for r in json.loads(READINGS.read_text(encoding="utf-8"))
            if r["snapshot"] == "coros_4_0_1"]


def _by_parameter(rows):
    """Readings grouped by the parameter they were taken on."""
    out = collections.defaultdict(list)
    for row in rows:
        if row["model_id"] is None:      # the metronome cells are not a block
            continue
        out[(row["model_id"], row["param_index"])].append(row)
    return out


def test_every_reading_was_taken_on_a_parameter_this_unit_still_has(rows, live_catalog):
    for (model_id, index), group in sorted(_by_parameter(rows).items()):
        row = group[0]
        assert model_id in {m.id for m in live_catalog}, (
            f"{row['model']} (id {model_id}) is not in this unit's catalog, but "
            f"{len(group)} reading(s) were taken on it")
        model = live_catalog[model_id]
        assert index < len(model.parameters), (
            f"{model.name} no longer has a parameter {index}; "
            f"{row['param']} was read there")
        assert model.parameters[index].name == row["param"], (
            f"{model.name} parameter {index} is now "
            f"{model.parameters[index].name!r}, not {row['param']!r} - the "
            f"readings are pinned to the index, so this invalidates them")


def test_every_reading_still_describes_the_list_that_parameter_offers(rows, live_catalog):
    """The labels, in order, exactly - not a subset and not a reordering."""
    for (model_id, index), group in sorted(_by_parameter(rows).items()):
        spec = live_catalog[model_id].parameters[index]
        recorded = tuple(group[0]["labels"])
        assert tuple(spec.options) == recorded, (
            f"{live_catalog[model_id].name} {spec.name} now offers "
            f"{list(spec.options)}; the readings were taken against "
            f"{list(recorded)}")
        for row in group:
            assert row["index"] < len(spec.options)


def test_a_list_stamped_audited_is_still_a_list_this_unit_has(live_catalog):
    offered = {tuple(p.options) for m in live_catalog for p in m.parameters
               if p.options and not p.dynamic}
    for labels, status in options.OPTION_AUDIT.items():
        if status == "audited":
            assert labels in offered, (
                f"{labels} is stamped audited but no parameter on this unit "
                f"offers it any more")


def test_a_list_stamped_hidden_is_still_hidden_on_this_unit(live_catalog):
    """`hidden` is why six lists can never be read, so it has to stay true.

    A firmware that reveals one of them turns an impossible list back into work
    somebody should do, and nothing else would notice.
    """
    visible = collections.defaultdict(list)
    for m in live_catalog:
        for p in m.parameters:
            if p.options and not p.dynamic and not p.hidden:
                visible[tuple(p.options)].append(f"{m.name} {p.name}")
    for labels, status in options.OPTION_AUDIT.items():
        if status == "hidden":
            assert not visible[labels], (
                f"{labels} is stamped impossible-to-audit, but this unit now "
                f"shows it on {visible[labels][:3]}. It is auditable; re-stamp "
                f"it and read it.")
