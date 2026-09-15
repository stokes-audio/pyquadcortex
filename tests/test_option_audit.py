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
from pyquadcortex.protocol.catalogs.coros_4_0_1 import options as generated

READINGS = (pathlib.Path(__file__).parent / "fixtures" / "catalog"
            / "option_readings.json")

#: Where the audit stood when this file was last updated, as
#: ``{status: number of lists}``. Pinned so that landing an audit is a visible
#: diff and losing one cannot happen quietly. ``None`` is "nobody has looked".
#:
#: ``docs/domain-model.md`` quotes these numbers in prose, and
#: ``test_the_document_quotes_the_same_counts`` below actually opens the file
#: and looks for them. An earlier version of this comment claimed the two moved
#: together while the test only compared the code against the literal here -
#: which would have passed happily with the document saying anything at all.
EXPECTED = {"audited": 12, "drawn": 1, "absent": 5, None: 95}

#: Lists somebody looked for on the unit and did not find, so no reading of them
#: is possible. Named rather than counted, because "absent" is the one status a
#: reader might mistake for a job still to do, and because the set changing
#: means the device changed.
#:
#: **Every entry is an observation.** An earlier version derived this from the
#: catalog's `hidden` flag and listed seven, including the Mono Synth's
#: `OSC1 WAVE` - which is on the screen, on a tab called Oscillator, drawn as
#: waveform icons. The flag is the vendor's intent, not a fact about the glass,
#: and ADR-0010 is the precedent for what happens to rules like that.
ABSENT_LISTS = {
    ("Noral", "Inverted"),
    ("Duck", "Gate"),
    ("nolly", "nollySkewed", "nollySkewedPlug"),
    ("0", "1", "2", "3"),
    ("Clean", "Crunch", "Lead"),
}


@pytest.fixture(scope="module")
def rows():
    return json.loads(READINGS.read_text(encoding="utf-8"))


def test_the_fixture_is_a_list_of_complete_rows(rows):
    required = {"snapshot", "labels", "index", "screen", "read_on", "method",
                "model", "model_id", "param", "param_index"}
    for row in rows:
        missing = required - set(row)
        assert not missing, f"reading {row} is missing {sorted(missing)}"
        # model_id is in that set although it may be null, because the hardware
        # test dereferences it. A row without the KEY passes every offline check
        # and raises KeyError on the unit, which is the worst place to find out.
        if "kind" in row:
            assert row["kind"] in ("text", "symbol", "absent"), row
        # An "absent" row records that a control was looked for and not drawn,
        # so it has no screen text by construction.
        assert (row["screen"] is None) == (row.get("kind") == "absent"), row
        # "looked" is the only honest method for an absent row: nothing was
        # driven and no list was transcribed, somebody looked for a control and
        # it was not there. Forcing those rows to claim "driven" let the
        # generator's correction guard accept a rename backed by one.
        assert row["method"] in ("driven", "list", "looked"), row
        assert (row["method"] == "looked") == (row.get("kind") == "absent"), row


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
        # An "absent" row is an observation about the CONTROL, not a reading of
        # a position - its index is a placeholder and counting it would report
        # position 0 of an undrawn list as read.
        if row["snapshot"] == "coros_4_0_1" and row.get("kind") != "absent":
            read[tuple(row["labels"])].add(row["index"])
    for labels, status in options.OPTION_AUDIT.items():
        if status in ("audited", "drawn"):
            assert read[labels] == set(range(len(labels))), (
                f"{labels} is stamped {status!r} but positions "
                f"{sorted(set(range(len(labels))) - read[labels])} were "
                f"never read")
        elif status == "partial":
            assert 0 < len(read[labels]) < len(labels)
        else:
            assert not read[labels], (
                f"{labels} has readings but is stamped {status!r}")


def test_an_absent_list_is_not_a_list_awaiting_work():
    assert {k for k, v in options.OPTION_AUDIT.items() if v == "absent"} == \
        ABSENT_LISTS


def test_absent_is_recorded_by_observation_and_never_by_the_hidden_flag():
    """The rule that was wrong, kept honest by the list that disproved it.

    A Mono Synth's `OSC1 WAVE` is marked `hidden="true"` in the catalog and is
    nonetheless drawn on screen. If `absent` ever goes back to being derived
    from the flag, this list will be stamped unreadable and a visible control
    will be declared permanently uncheckable.
    """
    osc = ("Sine", "Triang", "Sawtooth", "Square", "Pulse", "Pink NS", "White NS")
    assert options.OPTION_AUDIT[osc] != "absent", (
        "OSC1 WAVE is on screen - it was read on the unit 2026-09-14. Marking "
        "it absent means `absent` is being derived from the hidden flag again.")


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
    assert options.OPTION_AUDIT[("Off", "On")] == "audited"
    assert options.OPTION_AUDIT[("OFF", "ON")] == "audited"
    # The metronome's four are DRAWN, not written, so its words are still
    # unchecked - and it is the one list with no enum whose docstring could have
    # said so, which is why the status has to carry it instead.
    assert options.OPTION_AUDIT[("OFF", "MUTE", "DOWN", "ON")] == "drawn"


def test_a_disagreement_would_reach_the_generated_file(rows):
    """If a reading ever contradicts the catalog, the enum has to show it.

    Nothing disagrees today. The test is written against the MECHANISM rather
    than against a current disagreement, so the day one is recorded it is caught
    here instead of being filed and forgotten.
    """
    # The GENERATED module, not `options.__file__` - that is a four-line shim
    # re-exporting this one, and reading it made this assertion unreachable:
    # "screen: " can never appear there, so the test passed only because no
    # reading disagrees yet. It would have fired on the first real disagreement,
    # after a correct regeneration, telling the author to regenerate again.
    source = pathlib.Path(generated.__file__).read_text(encoding="utf-8")
    for row in rows:
        if row.get("kind") in ("symbol", "absent"):
            continue
        label = row["labels"][row["index"]]
        if row["screen"] != label:
            assert f"screen: {row['screen']!r}" in source, (
                f"{row['param']} index {row['index']} was read as "
                f"{row['screen']!r} but the catalog says {label!r}, and the "
                f"generated module does not mention it. Regenerate.")


def test_a_list_was_read_the_way_the_rule_says_it_must_be(rows):
    """The safeguard that nearly did not exist.

    Transcribing a control's choices in order is far faster than driving every
    position, and on a dial it works. On a TWO-position control it does not:
    `RECORD MODE`, `DUPLICATE MODE` and `CURVE` were each read as a list on
    2026-09-14 and each came back in the OPPOSITE order from the catalog. All
    three were wrong, and recording them would have put three backwards names
    into the library - the same failure `enums.MetronomeBeat` already had once.

    So the rule: a two-position list is audited only by DRIVING both positions,
    and a longer one only with at least two positions driven. This test is what
    makes it a rule rather than a paragraph - the first version of this work
    wrote the rule into a comment and shipped a list that broke it.
    """
    driven = collections.defaultdict(set)
    for row in rows:
        if row["method"] == "driven" and row.get("kind") != "absent":
            driven[tuple(row["labels"])].add(row["index"])
    for labels, status in options.OPTION_AUDIT.items():
        if status not in ("audited", "drawn"):
            continue
        anchors = driven[labels]
        if len(labels) == 2:
            assert anchors == {0, 1}, (
                f"{labels} is a two-position list stamped {status!r}, but "
                f"positions {sorted(anchors)} were driven. Both must be: read "
                f"as a list, a two-position control does not even give its "
                f"order away.")
        else:
            assert len(anchors) >= 2, (
                f"{labels} is stamped {status!r} off a list reading with "
                f"{len(anchors)} driven anchor(s) ({sorted(anchors)}). Two are "
                f"needed, one of them somewhere an error would show.")
            # "somewhere an error would show" is a human's judgement and this
            # test cannot make it - the reason belongs in the row's note. What
            # CAN be checked is that the anchors are not huddled together, which
            # is the cheap way to satisfy a count while proving almost nothing.
            # The waveform list was exactly that: anchored at 5 and 6 only,
            # adjacent and at one end, leaving positions 0 to 4 on a
            # transcription alone - on the one list that produced a rename.
            #
            # `len(labels) // 2` is a floor, not a standard. It catches huddling
            # and it does NOT prove the middle: REC. LENGTH passes it at 0 and
            # 16 of 33 with nothing above the halfway point, and what actually
            # covers that list is a separate confirmation, recorded in its note,
            # that the entries run 1 to 32 with no gaps. A list whose order is
            # less predictable than counting deserves more anchors than this
            # allows, and the note is where a reader finds out whether it got
            # them.
            #
            # No length guard: two anchors in a 3-entry list already span 1 or
            # more, so the condition is free there rather than skipped - and an
            # `if len(labels) >= 4` around it would read as though short lists
            # were exempt, which they are not.
            assert max(anchors) - min(anchors) >= len(labels) // 2, (
                f"{labels} is stamped {status!r} with its driven anchors at "
                f"{sorted(anchors)} - too close together to say the order holds "
                f"across the list. Drive one nearer the other end.")


def test_the_document_quotes_the_same_counts():
    """`docs/domain-model.md` states the audit's numbers in prose.

    Nothing else connects the two, so without this the document drifts away
    from the code the first time somebody updates one and not the other.
    """
    doc = (pathlib.Path(__file__).parents[1] / "docs" / "domain-model.md")
    # Whitespace-collapsed: the sentence is wrapped at 80 columns in the
    # document, so a literal search would demand the prose keep a line break in
    # one particular place, and reflowing a paragraph would "fail the audit".
    text = " ".join(doc.read_text(encoding="utf-8").split())
    counts = collections.Counter(options.OPTION_AUDIT.values())
    phrase = (f"{counts['audited']} audited, {counts['drawn']} drawn, "
              f"{counts['absent']} not drawn, {counts[None]} unread")
    assert phrase in text, (
        f"docs/domain-model.md does not say {phrase!r}. The audit moved and the "
        f"document did not; they are updated in the same commit.")


def test_a_drawn_list_is_not_counted_as_words_checked():
    """`OFF,MUTE,DOWN,ON` is the case, and it is the easiest one to overclaim.

    All four positions were driven and read, so by position count the list is
    complete. But the unit draws a filled or empty circle with an optional dot;
    it never writes `MUTE`. Those four WORDS are exactly the hypothesis this
    mechanism exists to flag, so calling the list audited would be the
    overstatement in its purest form - a list stamped checked where nothing
    about the spelling was.
    """
    drawn = {k for k, v in options.OPTION_AUDIT.items() if v == "drawn"}
    assert drawn == {("OFF", "MUTE", "DOWN", "ON")}


def test_the_published_record_of_the_swap_says_what_was_measured():
    """What `OPTION_CONTESTED` and the enum publish about the one catalog error.

    The BEHAVIOUR - that `set_param_option` refuses these names and still
    accepts the others - is tested in `tests/test_client.py` against the real
    method. This one guards the record: which positions are contested, and that
    the members follow the screen rather than the catalog.

    An earlier version of this test was named as though it covered the refusal
    and never called the method that refuses.
    """
    names = ("Sine", "Triang", "Sawtooth", "Square", "Pulse",
             "Pink NS", "White NS")
    assert options.OPTION_CONTESTED[names] == {5: "Pink NS", 6: "White NS"}
    # the screen draws WHT at 5 and PNK at 6, so the members read that way
    assert options.Osc1Wave.WHITE_NS == 5
    assert options.Osc1Wave.PINK_NS == 6
    # and the catalog's own strings are untouched, because the device sends them
    assert options.OPTION_LABELS[options.Osc1Wave] == names


def test_a_correction_cannot_be_added_without_a_reading_behind_it(rows):
    """`MEANING_DISAGREEMENTS` renames a public member, so it needs evidence.

    Without the generator's check it is `SPELLING_FIXES` with a bigger blast
    radius. This holds the other half: that the evidence for the one entry
    that exists is actually in the fixture, driven rather than transcribed.
    """
    driven = {(tuple(r["labels"]), r["index"]) for r in rows
              if r["method"] == "driven"}
    for labels, by_index in options.OPTION_CONTESTED.items():
        for index in by_index:
            assert (labels, index) in driven, (
                f"position {index} of {labels} is published as contested, but "
                f"no driven reading in the fixture says what the screen shows "
                f"there")


def test_the_document_quotes_the_same_parameter_counts():
    """The counts in prose that the status counts do not cover.

    `docs/domain-model.md` states how many PARAMETERS each part of the audit
    covers, and those are the numbers a reader cares about - 12 lists sounds
    small and 287 parameters does not. They were wrong twice on this branch,
    once as a silent regression, because nothing derived them.

    Derived here from the generated enums' own docstrings, which state how many
    parameters use each list, plus the three lists that get no enum. So this
    fails if the document drifts OR if the snapshot changes underneath it.
    """
    import re

    source = pathlib.Path(generated.__file__).read_text(encoding="utf-8")
    uses = {}
    for match in re.finditer(
            r"class (\w+)\(IntEnum\):\n    \"\"\"(\d+) parameters? use this list",
            source):
        uses[tuple(options.OPTION_LABELS[getattr(options, match.group(1))])] = \
            int(match.group(2))
    # the three with no enum, whose parameter counts live nowhere else
    uses[("Off", "On")] = 222
    uses[("OFF", "ON")] = 25
    uses[("OFF", "MUTE", "DOWN", "ON")] = 13

    per_status = collections.defaultdict(int)
    for labels, status in options.OPTION_AUDIT.items():
        per_status[status] += uses[labels]
    assert sum(per_status.values()) == 527

    text = " ".join(
        (pathlib.Path(__file__).parents[1] / "docs" / "domain-model.md")
        .read_text(encoding="utf-8").split())
    read = per_status["audited"] + per_status["drawn"]
    for phrase in (f"cover {read} of the 527", f"unread cover {per_status[None]}"):
        assert phrase in text, (
            f"docs/domain-model.md does not say {phrase!r}. The parameter "
            f"counts moved and the document did not.")
