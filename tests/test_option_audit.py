"""The screen readings behind `options.OPTION_AUDIT`.

`stepNames` is the catalog's vocabulary, and the catalog is not the unit's
screen. Where the two could be compared offline they disagreed: for the twelve
parameters whose list the device builds from the preset, the catalog writes
``In 1`` and ``Ret 1/2`` and the device writes ``Input 1`` and ``Return 1/2``.
So a list's names are a hypothesis until somebody reads them off the unit.

`tests/fixtures/catalog/option_readings.json` is where those readings live - one
row per POSITION, because "index 2 showed 'Gate'" is a fact that can be checked
and "this list is fine" is not. `scripts/generate_options.py` reads the file and
stamps each enum, so a list nobody has read says so in its own docstring rather
than looking identical to one that was checked.

What these tests protect is the honesty of that stamp: that a reading names a
list that exists, that "audited" means every position was read, and that the
counts move only when somebody changes them on purpose.
"""

import collections
import json
import pathlib

import pytest

from pyquadcortex.protocol import options

READINGS = (pathlib.Path(__file__).parent / "fixtures" / "catalog"
            / "option_readings.json")

#: Where the audit stood when this file was last updated, as
#: ``{status: number of lists}``. Pinned so that landing an audit is a visible
#: diff and losing one cannot happen quietly. ``None`` is "nobody has looked".
#:
#: ``docs/domain-model.md`` quotes the unread number in prose; the two move
#: together or this test fails, which is the only thing keeping the document
#: from drifting away from the code.
EXPECTED = {"audited": 5, "hidden": 6, None: 102}

#: Lists no parameter shows on screen, so no reading of them is possible. Named
#: rather than counted, because "hidden" is the one status a reader might
#: mistake for a job still to do, and because the set changing means the device
#: changed. Five of the six carry labels that look like source identifiers -
#: which is the likeliest reason they do: nobody was meant to read them.
HIDDEN_LISTS = {
    ("Noral", "Inverted"),
    ("Duck", "Gate"),
    ("nolly", "nollySkewed", "nollySkewedPlug"),
    ("0", "1", "2", "3"),
    ("Sine", "Triang", "Sawtooth", "Square", "Pulse", "Pink NS", "White NS"),
    ("Clean", "Crunch", "Lead"),
}


@pytest.fixture(scope="module")
def rows():
    return json.loads(READINGS.read_text(encoding="utf-8"))


def test_the_fixture_is_a_list_of_complete_rows(rows):
    required = {"snapshot", "labels", "index", "screen", "read_on",
                "model", "param", "param_index"}
    for row in rows:
        missing = required - set(row)
        assert not missing, f"reading {row} is missing {sorted(missing)}"
        assert row["kind"] in (None, "text", "symbol") if "kind" in row else True


def test_every_reading_names_a_list_the_snapshot_has(rows):
    """A reading against labels no parameter offers is a typo, not evidence.

    It is the easy mistake when transcribing: one character wrong in a label and
    the row silently audits nothing, while the list it was meant for stays
    unread and looks it.
    """
    for row in rows:
        if row["snapshot"] != "coros_4_0_1":
            continue
        labels = tuple(row["labels"])
        assert labels in options.OPTION_AUDIT, (
            f"{row['model']} {row['param']}: no list in the snapshot offers "
            f"{labels}")


def test_every_reading_is_inside_its_list(rows):
    for row in rows:
        labels = tuple(row["labels"])
        assert 0 <= row["index"] < len(labels), (
            f"{row['param']} index {row['index']} is outside a "
            f"{len(labels)}-entry list")


def test_two_readings_of_one_position_agree(rows):
    """A list is shared by up to 28 parameters and read on whichever is handy.

    Two readings that agree are corroboration. Two that DISAGREE mean the same
    `stepNames` string is drawn two different ways, so the parameters do not
    really share one list - a finding about the device, and one that must not be
    settled by whichever row happens to come first in the file.
    """
    seen = collections.defaultdict(dict)
    for row in rows:
        key = (row["snapshot"], tuple(row["labels"]), row["index"])
        seen[key][row["screen"]] = row
    for (_, labels, index), words in seen.items():
        assert len(words) == 1, (
            f"{labels} index {index} was read as {sorted(words)} on "
            f"{[r['model'] + ' ' + r['param'] for r in words.values()]}")


def test_audited_means_every_position_was_read(rows):
    """The status this file exists to keep honest.

    Rounding a part-read list up to "audited" is the failure mode: a 21-entry
    list read at four positions feels checked, and marking it so makes it
    indistinguishable from one that was.
    """
    read = collections.defaultdict(set)
    for row in rows:
        if row["snapshot"] == "coros_4_0_1":
            read[tuple(row["labels"])].add(row["index"])
    for labels, status in options.OPTION_AUDIT.items():
        if status == "audited":
            assert read[labels] == set(range(len(labels))), (
                f"{labels} is stamped audited but positions "
                f"{sorted(set(range(len(labels))) - read[labels])} were "
                f"never read")
        elif status == "partial":
            assert 0 < len(read[labels]) < len(labels)
        else:
            assert not read[labels], (
                f"{labels} has readings but is stamped {status!r}")


def test_a_hidden_list_is_not_a_list_awaiting_work():
    assert {k for k, v in options.OPTION_AUDIT.items() if v == "hidden"} == \
        HIDDEN_LISTS


def test_the_audit_counts_are_what_we_last_agreed():
    counts = collections.Counter(options.OPTION_AUDIT.values())
    assert dict(counts) == EXPECTED, (
        f"the audit moved: {dict(counts)}. Update EXPECTED here and the number "
        f"quoted in docs/domain-model.md in the same commit.")


def test_the_audit_covers_the_lists_with_no_enum():
    """The Off/On pair alone is 247 parameters and has no enum to stamp.

    Keying the audit by the enum would have left the biggest list in the catalog
    unable to be recorded at all, and would have reported the job as smaller
    than it is.
    """
    with_enum = {tuple(v) for v in options.OPTION_LABELS.values()}
    without = set(options.OPTION_AUDIT) - with_enum
    assert without == {("Off", "On"), ("OFF", "ON"),
                       ("OFF", "MUTE", "DOWN", "ON")}
    for labels in without:
        assert options.OPTION_AUDIT[labels] == "audited"


def test_a_disagreement_would_reach_the_generated_file(rows):
    """If a reading ever contradicts the catalog, the enum has to show it.

    Nothing disagrees today. The test is written against the MECHANISM rather
    than against a current disagreement, so the day one is recorded it is caught
    here instead of being filed and forgotten.
    """
    source = pathlib.Path(options.__file__).read_text(encoding="utf-8")
    for row in rows:
        if row.get("kind") == "symbol":
            continue
        label = row["labels"][row["index"]]
        if row["screen"] != label:
            assert f"screen: {row['screen']!r}" in source, (
                f"{row['param']} index {row['index']} was read as "
                f"{row['screen']!r} but the catalog says {label!r}, and the "
                f"generated module does not mention it. Regenerate.")
