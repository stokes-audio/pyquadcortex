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
EXPECTED = {"audited": 13, "drawn": 1, "absent": 5, None: 94}

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
    """A reading that contradicts the catalog has to reach the enum.

    Nine rows disagree - every position of a Mono Synth's waveform list, five of
    them abbreviations and two of them the swap - so this runs against real data
    rather than waiting for a disagreement to arrive. It was written before any
    existed and its docstring said so for two commits after they did, which is
    the same drift between the record and the measurement this file exists to
    stop.
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
    covers, and those are the numbers a reader cares about: a list count sounds
    small where a parameter count does not. Derived rather than written down,
    because a number typed into prose goes stale the next time a reading lands.

    Derived here from `options.OPTION_USAGE`, which the generator emits from
    the catalog beside the audit. It used to be scraped out of the enums'
    docstrings with a regex, with the three lists that get no enum hand-copied
    into this file, where an error in the split between the two Off/On
    spellings would have cancelled out unseen. Every list is counted the same
    way now, and this fails if the document drifts or if the snapshot changes
    underneath it.
    """
    assert set(options.OPTION_USAGE) == set(options.OPTION_AUDIT), (
        "every fixed list is in both tables, or one of them is not about all "
        "of them")

    per_status = collections.defaultdict(int)
    for labels, status in options.OPTION_AUDIT.items():
        per_status[status] += options.OPTION_USAGE[labels]
    assert sum(per_status.values()) == 527

    text = " ".join(
        (pathlib.Path(__file__).parents[1] / "docs" / "domain-model.md")
        .read_text(encoding="utf-8").split())
    read = per_status["audited"] + per_status["drawn"]
    for phrase in (f"cover {read} of the 527", f"unread cover {per_status[None]}"):
        assert phrase in text, (
            f"docs/domain-model.md does not say {phrase!r}. The parameter "
            f"counts moved and the document did not.")


def test_the_unread_work_is_long_tailed_and_the_document_says_so():
    """What is left is not 94 equal jobs, and planning one needs the shape.

    Ranked by how many parameters each decides, the unread lists fall away
    fast. The document names the biggest few so a session at the unit can be
    planned; without a derived check that ranking rots the first time the
    snapshot moves.
    """
    unread = sorted(
        (labels for labels, status in options.OPTION_AUDIT.items()
         if status is None),
        key=lambda labels: (-options.OPTION_USAGE[labels], len(labels), labels))
    total = sum(options.OPTION_USAGE[labels] for labels in unread)
    top5 = sum(options.OPTION_USAGE[labels] for labels in unread[:5])
    assert (len(unread), total) == (94, 190)

    text = " ".join(
        (pathlib.Path(__file__).parents[1] / "docs" / "domain-model.md")
        .read_text(encoding="utf-8").split())
    phrase = f"biggest five cover {top5} of the {total}"
    assert phrase in text, (
        f"docs/domain-model.md does not say {phrase!r}. The ranking moved and "
        f"the document did not.")


def test_the_worklist_table_is_the_snapshots_own_ranking():
    """The five rows the document names, in the order it names them.

    The point of `OPTION_USAGE` is to stop these being carried in prose from a
    one-off count, so the table is parsed out of the document by its header and
    its first two columns are compared, IN ORDER, against the snapshot's own
    ranking of the unread lists.

    Column three is checked where the row quotes the label tuple literally,
    which three of the five do; the other two describe their list in prose
    (``a 17-entry `SYNC NOTE` ``) because quoting seventeen note values in a
    table cell would be unreadable. For those two only the COUNT in the
    description is held against the snapshot - ``a 17-entry `PRE ROLL` `` would
    pass, because the control name in a prose cell is the same kind of claim as
    column four and needs the same payload to check.

    What this does NOT check, and cannot offline: column four, the model the
    control appears on. Confirming a model name needs the `ModelRepo` payload,
    and no payload is committed here - the snapshot is generated constants.

    This test also holds the sentence describing the tail below the table,
    because that sentence counts the same ranking starting from the row after
    the last one the table shows.
    """
    unread = sorted(
        (labels for labels, status in options.OPTION_AUDIT.items()
         if status is None),
        key=lambda labels: (-options.OPTION_USAGE[labels], len(labels), labels))

    doc = (pathlib.Path(__file__).parents[1] / "docs" / "domain-model.md")
    lines = doc.read_text(encoding="utf-8").splitlines()
    header = "| parameters | positions | list | somewhere it appears |"
    assert lines.count(header) == 1, (
        f"docs/domain-model.md must carry exactly one worklist table with the "
        f"header {header!r}; it has {lines.count(header)}")
    parsed = []
    for line in lines[lines.index(header) + 2:]:
        if not line.startswith("|"):
            break
        parsed.append([cell.strip() for cell in line.strip("|").split("|")])
    rows = [(int(cells[0]), int(cells[1])) for cells in parsed]

    assert rows == [(options.OPTION_USAGE[labels], len(labels))
                    for labels in unread[:len(rows)]], (
        f"the worklist table is {rows}, and the snapshot's five biggest unread "
        f"lists are "
        f"{[(options.OPTION_USAGE[l], len(l)) for l in unread[:len(rows)]]}. "
        f"The ranking moved and the table did not.")
    assert len(rows) == 5, f"the worklist table has {len(rows)} rows, not five"

    quoted = 0
    for cells, labels in zip(parsed, unread):
        if cells[2] == f"`{','.join(labels)}`":
            quoted += 1
        else:
            assert cells[2].startswith(f"a {len(labels)}-entry "), (
                f"worklist row {cells[0]} describes its list as {cells[2]!r}, "
                f"which is neither the snapshot's labels "
                f"`{','.join(labels)}` nor a description naming its "
                f"{len(labels)} entries")
    assert quoted == 3, (
        f"three of the five rows quote their label tuple; {quoted} do")

    text = " ".join(doc.read_text(encoding="utf-8").split())
    rest = unread[len(rows):]
    few = sum(1 for labels in rest if options.OPTION_USAGE[labels] <= 2)
    two = sum(1 for labels in rest if len(labels) == 2)
    phrase = (f"Of the remaining {len(rest)}, {few} decide one or two "
              f"parameters each and {two} have only two positions")
    assert phrase in text, f"docs/domain-model.md does not say {phrase!r}."


def test_the_reading_that_settled_the_shortening_is_still_in_the_fixture():
    """The comparison the documentation rests on, held against the readings.

    The catalog says `Sine`. A Mono Synth's oscillator tab draws `SIN`; a
    Flanger Engine's WAVEFORM, offering the same word, draws `Sine`. That pair
    is what shows the shortening belongs to the control rather than to the
    catalog's text, so both halves are asserted by name. Checking only the SET
    of mismatched controls is not enough: that stays green if these readings
    are replaced by some other control's.
    """
    rows = json.loads(READINGS.read_text(encoding="utf-8"))

    def screen_for(model, param, word):
        for r in rows:
            if (r["model"], r["param"]) == (model, param) and \
                    r["labels"][r["index"]] == word:
                return r["screen"]
        raise AssertionError(f"no reading of {model} / {param} at {word!r}")

    assert screen_for("Mono Synth", "OSC1 WAVE", "Sine") == "SIN"
    assert screen_for("Flanger Engine", "WAVEFORM", "Sine") == "Sine"
    assert screen_for("Flanger Engine", "WAVEFORM", "Square") == "Square"


def test_only_the_mono_synth_shortens_a_word_on_screen():
    """No second control has turned up that draws its own words.

    A later reading that shortened a word somewhere else would make
    `docs/domain-model.md` wrong rather than just incomplete, so the set is
    pinned. The metronome's four cells are in it because they draw circles;
    they do not shorten anything.
    """
    rows = json.loads(READINGS.read_text(encoding="utf-8"))
    differ = {(r["model"], r["param"]) for r in rows
              if r["screen"] and r["screen"] != r["labels"][r["index"]]}
    assert differ == {
        ("Mono Synth", "OSC1 WAVE"), ("Mono Synth", "OSC2 WAVE"),
        ("Tempo page", "STEPSTATE0"), ("Tempo page", "STEPSTATE1"),
        ("Tempo page", "STEPSTATE2"), ("Tempo page", "STEPSTATE3"),
    }, (
        "the screen matches the catalog everywhere it has been read except the "
        "Mono Synth's two oscillators, which shorten, and the metronome's four "
        "step cells, which draw circles. A new entry here means "
        "docs/domain-model.md needs rewriting, not this list extending")

    # The document counts controls READ, which is not every control in the
    # fixture: five are records of looking and finding nothing on screen. Held
    # against the document rather than against a literal, because the sentence
    # is the thing that goes stale.
    pairs = {(r["model"], r["param"]) for r in rows}
    looked = {(r["model"], r["param"]) for r in rows if r.get("method") == "looked"}
    read, matching = len(pairs - looked), len(pairs - looked) - len(differ)
    words = {2: "two", 4: "four", 5: "five", 13: "thirteen", 14: "fourteen",
             19: "Nineteen", 20: "Twenty"}
    for n in (read, len(looked), matching):
        assert n in words, (
            f"{n} has no spelling here, so this test cannot say what "
            f"docs/domain-model.md should read. Add it, and update the "
            f"document in the same commit")
    text = " ".join(
        (pathlib.Path(__file__).parents[1] / "docs" / "domain-model.md")
        .read_text(encoding="utf-8").split())
    phrase = (f"{words[read]} controls have been read - the fixture holds "
              f"{len(pairs)}, but {words[len(looked)]} of those are records of "
              f"looking")
    assert phrase in text, (
        f"docs/domain-model.md does not say {phrase!r}. The readings moved and "
        f"the document did not.")
    assert f"The other {words[matching].lower()} match the catalog" in text, (
        f"docs/domain-model.md must say {words[matching].lower()} read controls "
        f"match the catalog exactly")


def test_the_ranking_recipe_the_changelog_publishes_actually_works():
    """`changelog.md` hands users a recipe for ranking the unread lists.

    The snippet is EXECUTED, not hand-copied. A copy in this file only stays
    honest by convention: an earlier version of this test kept its own sort
    key, the changelog's grew a tiebreak, the two orders diverged from index 9,
    and the test passed anyway because it only ever read the first entry.

    `tests/test_docs.py` covers the fences under `docs/`; `changelog.md` sits
    at the repo root and is outside its glob, so the one snippet a reader is
    most likely to copy had nothing running it.
    """
    import re

    text = (pathlib.Path(__file__).parents[1] / "changelog.md").read_text(
        encoding="utf-8")
    fences = [f for f in re.findall(r"```python\n(.*?)```", text, re.DOTALL)
              if "OPTION_USAGE" in f]
    assert len(fences) == 1, (
        f"expected exactly one OPTION_USAGE snippet in changelog.md, found "
        f"{len(fences)}")

    scope: dict = {}
    exec(fences[0], scope)                      # noqa: S102 - the point
    assert scope["unread"], "the recipe produced no unread lists"

    # The comment beside the last line publishes its own answer, which is the
    # part that rots.
    biggest = options.OPTION_USAGE[scope["unread"][0]]
    assert f"# {biggest} parameters - the biggest unread list" in text, (
        f"changelog.md's recipe claims an answer that is no longer {biggest}")
