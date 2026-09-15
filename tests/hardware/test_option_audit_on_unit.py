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
import xml.etree.ElementTree as ET

import pytest

from pyquadcortex.protocol import catalog, options

REPO = pathlib.Path(__file__).resolve().parents[2]
READINGS = REPO / "tests" / "fixtures" / "catalog" / "option_readings.json"


@pytest.fixture(scope="module")
def live_catalog(qc):
    return catalog.parse_model_repo(qc._fetch_model_repo())


@pytest.fixture(scope="module")
def live_xml(qc):
    """The catalog's raw XML, for attributes the parser turns into a bool."""
    return ET.fromstring(catalog._extract_xml(qc._fetch_model_repo()))


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
        if status in ("audited", "drawn"):
            assert labels in offered, (
                f"{labels} is stamped {status!r} but no parameter on this unit "
                f"offers it any more")


def test_a_list_stamped_absent_still_looks_the_way_it_did_when_looked_at(live_catalog):
    """`absent` means a person looked and the control was not drawn.

    Nothing on the wire can re-check that - it needs eyes - so this test does
    not pretend to. What it CAN do is notice the two things that would make the
    recorded look stale: the parameter no longer existing, and the catalog's
    `hidden` flag no longer being set on it.

    The flag is emphatically not why these lists are stamped `absent` - it was
    tried as a rule and a Mono Synth's `OSC1 WAVE` disproved it, being flagged
    and drawn. It is used here only in the direction it is safe in: a flag that
    has been REMOVED is a change to the parameter, and a reason for a human to
    go and look again at something last checked on 2026-09-14.

    An earlier version of this test gated on `status == "hidden"`, a word
    `audit_status` had already stopped returning, so the body never ran and it
    passed a full hardware run asserting nothing.
    """
    assert "absent" in set(options.OPTION_AUDIT.values()), (
        "no list is stamped 'absent' any more - if the status was renamed, this "
        "test stopped checking anything rather than failing")
    flagged = collections.defaultdict(list)
    for m in live_catalog:
        for p in m.parameters:
            if p.options and not p.dynamic:
                flagged[tuple(p.options)].append(p.hidden)
    for labels, status in options.OPTION_AUDIT.items():
        if status != "absent":
            continue
        assert flagged[labels], (
            f"{labels} is stamped absent but no parameter on this unit offers "
            f"it any more")
        assert all(flagged[labels]), (
            f"{labels} was looked for on 2026-09-14 and was not drawn, and this "
            f"unit no longer marks every parameter using it hidden. The flag "
            f"does not decide the status, but losing it means the parameter "
            f"changed - look at the control again before trusting the record.")


def test_the_hidden_attribute_is_still_shaped_the_way_it_was_counted(live_xml):
    """`Parameter.hidden` documents two numbers that nothing has been checking.

    649 parameters say `"true"` and exactly one says `"atma"` - the Freeze
    block's `MOMENTARY` switch, which is the whole evidence that the catalog
    names the MODEL a parameter is hidden on, and a second independent sign that
    ATMA is the Mini. Both live in a docstring and in `docs/domain-model.md`,
    and the parser turns the attribute into a bool, so nothing offline can see
    either number go stale.

    A failure here is a finding about the device. If `atma` has spread to more
    parameters, `Parameter.hidden` answering only for a Quad Cortex matters more
    than it does today.
    """
    values = collections.Counter(
        p.get("hidden") for p in live_xml.iter("Parameter")
        if p.get("hidden") is not None)
    assert values == {"true": 649, "atma": 1}, (
        f"the hidden attribute now reads {dict(values)}; "
        f"Parameter.hidden and docs/domain-model.md say "
        f"{{'true': 649, 'atma': 1}}")

    atma = [(m.get("name"), p.get("name"))
            for c in live_xml.findall("Category") for m in c.findall("Model")
            for p in m.findall("Parameter") if p.get("hidden") == "atma"]
    assert atma == [("Freeze", "MOMENTARY")], (
        f"the one atma-hidden parameter is now {atma}")
