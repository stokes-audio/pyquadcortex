#!/usr/bin/env python3
"""Generate a snapshot's ``options.py`` - the choices a list offers.

Writes ``pyquadcortex/protocol/catalogs/<snapshot>/options.py``; ``--snapshot``
names the snapshot after the CorOS version it was read from (ADR-0020).

A list-valued parameter stores ``index / (count - 1)`` on the wire, so choosing
"Lo Pass" means knowing it is the fourth entry. The names are in the device's
catalog, in a ``stepNames`` attribute this library read for the first time on
2026-08-26; before that ``set_param_option`` said they were "not in the catalog"
and made every caller pass a preset to read them from.

Source: a device's ModelRepo payload, either live or previously saved.

    python scripts/generate_options.py --snapshot coros_4_1_0
    python scripts/generate_options.py --snapshot coros_4_1_0 \
        --payload tests/fixtures/catalog/model_repo_coros_4_0_1.bin

Four decisions this generator makes:

1. **One enum per distinct LIST, not per parameter.** 527 parameters carry a
   fixed list and they use only 113 distinct ones, of which 110 get an enum,
   because the same list means the same thing everywhere: the note-length list is shared by ``SYNC NOTE``,
   ``SYNC NOTE L``, ``SYNC NOTE R``, ``SYNC NOTE A`` and ``SYNC NOTE B``. One
   enum per list is one enum per concept.
2. **``Off,On`` gets no enum.** 247 of those 527 offer exactly "Off" and "On",
   and ``OffOn.ON`` says nothing that ``True`` does not. Those parameters take a
   bool.
3. **The device's spelling is kept on the wire and corrected in the name.**
   ``OPTION_LABELS`` holds the strings verbatim, typos included, because a
   dynamic list is matched by string against the preset. ``SPELLING_FIXES``
   below corrects the MEMBER name only, one reviewed line at a time.
4. **A list says whether anyone has checked it against the screen.**
   ``stepNames`` is the catalog's vocabulary and is NOT known to be the text the
   unit draws. Where the two can be compared offline they disagree: for the 12
   dynamic parameters the device renders its own list into the preset's
   ``dynamic_steps``, and against the same parameter's ``stepNames`` the catalog
   says ``In 1``/``Ret 1/2``/``USB 5`` where the device says ``Input
   1``/``Return 1/2``/``USB input 5``. So every enum carries an audit line, and
   a list nobody has read off the screen says so rather than looking checked.
   The readings live in ``tests/fixtures/catalog/option_readings.json`` - one
   row per position, holding what a human read on the unit - and
   ``OPTION_AUDIT`` publishes the status of every fixed list, including the two
   that get no enum.
"""
import argparse
import collections
import json
import keyword
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from pyquadcortex.protocol import catalog  # noqa: E402
import _snapshots  # noqa: E402  (the snapshot package, written once for all three)

# Anchored on this script's own location, never on the working directory:
# the docs give this command with no cwd, and a relative path silently built
# a whole new catalogs tree wherever the run started while reporting success.
CATALOGS = (pathlib.Path(__file__).resolve().parents[1]
            / "pyquadcortex" / "protocol" / "catalogs")


#: Lists that are a boolean wearing a costume. Their parameters take ``True`` and
#: ``False``; no enum is emitted.
BOOLEAN_LISTS = {("off", "on")}

#: Lists that are already published as a hand-written enum carrying evidence a
#: generator cannot, so emitting a second identical one would be one too many.
#:
#: One entry: the metronome's per-beat cells. `enums.MetronomeBeat` publishes
#: exactly these four words, with the hardware behind them - driven on the unit
#: 2026-08-27, one bar at 60 bpm with all four states on the four beats,
#: listened to AND looked at, so it records the sound and the on-screen symbol
#: for each.
#:
#: This list briefly existed for the opposite reason - to keep the catalog's
#: words out because they were thought wrong. They were right; the hand-chosen
#: names were wrong, two of them backwards. See docs/domain-model.md.
NOT_THE_SCREENS_WORDS = {
    ("OFF", "MUTE", "DOWN", "ON"): "the metronome beats - see enums.MetronomeBeat",
}

#: The device's own typos, corrected in the MEMBER NAME ONLY. The wire still
#: carries the device's spelling, which ``OPTION_LABELS`` preserves.
#:
#: One line per correction, each with the evidence that it IS a typo rather than
#: a word this generator's author did not recognise. Guitar equipment is full of
#: real words that look like mistakes, so the bar is deliberately high.
SPELLING_FIXES = {
    # 16 INVERT parameters offer "Noral,Inverted". The only sense the pairing
    # can carry is Normal/Inverted, and no other list in the catalog spells
    # "Normal" this way - 3 spell it "Normal" in the same kind of pair.
    "Noral": "NORMAL",
}

#: Positions where the screen and the catalog disagree about MEANING, not just
#: spelling, and the member name therefore follows the SCREEN.
#:
#: Most screen wording differs harmlessly - the unit draws ``SIN`` where the
#: catalog writes ``Sine``, and nobody is misled. This table is for the other
#: kind, and it is written by hand one reviewed line at a time because no
#: generator can tell an abbreviation from a contradiction.
#:
#: Every entry MUST have a driven reading behind it in ``option_readings.json``;
#: ``check_corrections`` below refuses one that does not, so this cannot become
#: somewhere to put a hunch. The wire is untouched - ``OPTION_LABELS`` still
#: holds the catalog's strings, because that is what the device publishes.
#:
#: One list so far. A SECOND is not a precedent to follow blindly; read it on
#: the unit the same way and write the evidence beside it.
MEANING_DISAGREEMENTS = {
    # Driven on a Mono Synth 2026-09-14, both oscillators set at once and read
    # together: wire position 5 draws WHT and position 6 draws PNK, while the
    # catalog calls them "Pink NS" and "White NS".
    #
    # Confirmed acoustically 2026-09-15, which is what makes this a fact about
    # the DEVICE rather than about one screen. Both positions were captured off
    # the unit's USB audio interface and SUBTRACTED, which cancels the rest of
    # the signal chain: the difference between them climbs monotonically
    # across all seven octave bands, ~3.6 dB per octave, so position 5 is the
    # brighter - the direction that separates white noise from pink, at the
    # right order of magnitude. The catalog has the two swapped, so
    # `PINK_NS = 5` would hand a caller white noise.
    ("Sine", "Triang", "Sawtooth", "Square", "Pulse", "Pink NS", "White NS"): {
        5: ("WHITE_NS", "position 5 drew 'WHT', which is 'White NS' - and "
            "recorded against position 6 the difference between them climbs "
            "monotonically across the seven octave bands, ~3.6 dB per octave, "
            "which makes it the brighter and so the white one; see "
            "docs/domain-model.md"),
        6: ("PINK_NS", "position 6 drew 'PNK', which is 'Pink NS' - and it is "
            "the darker of the pair by that same measurement, so it is the "
            "pink one; see docs/domain-model.md"),
    },
}


def check_corrections(readings: dict, present: dict) -> None:
    """Refuse a correction the readings do not support.

    **What this cannot do.** No check here proves that `WHT` means `White NS`;
    that is a human's judgement and it lives in the comment beside the entry.
    What the checks establish is that a correction is the SHAPE a swap has and
    that its reason quotes the two strings being paired - so a false entry has
    to be written out as "position 0 drew 'SIN', which is 'Square'", which a
    reviewer reads as nonsense, rather than as two member names that look
    reasonable on their own. The bar is reviewability, not proof.

    Without this the table above is just `SPELLING_FIXES` with a bigger blast
    radius - a place to rename a member on a hunch, which is exactly what the
    audit exists to stop.

    Only lists THIS catalog offers are checked. A correction is per snapshot and
    a different firmware need not carry the list at all, so demanding a reading
    for one that is not here would make a 4.1.0 run fail over a 4.0.1 finding.
    """
    for labels, by_index in MEANING_DISAGREEMENTS.items():
        if labels not in present:
            continue
        seen = readings.get(labels, {})
        # Built with the one-argument `member_name`: the three-argument form
        # consults this very table, which would let an entry justify itself.
        # Two positions of one list can mangle to the SAME member name - the
        # note lists do it, where "A" and "A#" both give A - and `render_enum`
        # resolves that with an `_<index>` suffix. A correction naming one of
        # those is ambiguous, so it is refused rather than resolved by whichever
        # index this dict happened to keep.
        emitted = collections.Counter(member_name(other) for other in labels)
        available = {member_name(other): i for i, other in enumerate(labels)}
        if len(by_index) < 2:
            raise SystemExit(
                f"MEANING_DISAGREEMENTS for {labels} has {len(by_index)} "
                f"position(s). A swap needs at least two: an entry that renames "
                f"one position either deletes a member or, when empty, publishes "
                f"a contested list with nothing contested in it.")
        for index, (member, evidence) in by_index.items():
            rows = [r for r in seen.get(index, [])
                    if r.get("method") == "driven"
                    and r.get("kind") not in ("absent", "symbol")]
            if not rows:
                raise SystemExit(
                    f"MEANING_DISAGREEMENTS renames position {index} of "
                    f"{labels} to {member!r}, but no DRIVEN reading records "
                    f"what the screen shows there. Read it on the unit first. "
                    f"(A row saying the control is not drawn does not count - "
                    f"it says nothing about what this position means.)")
            # The reading must also CONTRADICT the catalog. Merely requiring one
            # to exist let a rename ride on a reading that agreed with the label
            # it was overruling - "rename position 0 to HARD" passed with the
            # screen reading 'Soft' recorded there. Which member name a
            # contradiction deserves is still a human's call, and the reason
            # goes in the comment beside the entry; what is machine-checkable is
            # that there IS one.
            word = rows[0]["screen"]
            if word == labels[index]:
                raise SystemExit(
                    f"MEANING_DISAGREEMENTS renames position {index} of "
                    f"{labels} to {member!r}, but the driven reading there says "
                    f"the screen shows {word!r} - which is what the catalog "
                    f"already calls it. There is nothing to correct.")
            # And the new name must be one this list already contains. THAT is
            # what gives the guard teeth: the contradiction check above is
            # trivially satisfied on any list whose screen text ABBREVIATES, and
            # every position of the one list this table governs does exactly
            # that ("SIN" vs "Sine"). So a rename to anything at all passed -
            # `{0: "HARD_SYNC"}` was accepted on a pure hunch, and went on to
            # stamp the docstring "the catalog is WRONG at 0" and make
            # `set_param_option(..., "Sine")` raise.
            #
            # A correction this table can express is always "this position is
            # the thing the catalog calls a DIFFERENT position of this list" -
            # which is what a swap is. Anything else is a new claim about the
            # device and belongs in a record, not in a rename.
            #
            # Note the raw one-argument `member_name`: the three-argument form
            # consults this very table, which would let an entry justify itself.
            if member not in available:
                raise SystemExit(
                    f"MEANING_DISAGREEMENTS renames position {index} of "
                    f"{labels} to {member!r}, which is not what this list calls "
                    f"any of its positions ({sorted(available)}). This table can "
                    f"only say a position means what the catalog calls ANOTHER "
                    f"position of the same list - a swap. A new claim about the "
                    f"device needs a record, not a rename.")
            if emitted[member] > 1:
                raise SystemExit(
                    f"MEANING_DISAGREEMENTS renames position {index} of "
                    f"{labels} to {member!r}, and more than one position of "
                    f"this list produces that name, so it does not say which. "
                    f"This table cannot express that correction.")
            # The evidence has to QUOTE the reading and the label it is being
            # paired with. Nothing here can prove that WHT means White NS -
            # that is a human's judgement - but the shape checks alone let a
            # false pair through: `{0: "SQUARE", 3: "SINE"}` is a closed
            # permutation, and on a list whose screen text abbreviates the
            # contradiction test is satisfied at every position, so it passed
            # and stamped "the catalog is WRONG at 0, 3". Requiring the reason
            # to name both strings does not make that impossible; it makes it
            # something a reviewer reads as the nonsense it is, rather than two
            # member names that look plausible on their own.
            target = labels[available[member]]
            for quoted in (word, target):
                if quoted not in evidence:
                    raise SystemExit(
                        f"MEANING_DISAGREEMENTS renames position {index} of "
                        f"{labels} to {member!r}, and the reason beside it does "
                        f"not quote {quoted!r}. Say what was read and what it "
                        f"is being paired with, so the claim can be judged: "
                        f"got {evidence!r}.")
            if available[member] == index:
                raise SystemExit(
                    f"MEANING_DISAGREEMENTS renames position {index} of "
                    f"{labels} to {member!r}, which is already that position's "
                    f"name. Nothing changes.")

        # ...and the entry as a WHOLE must be a permutation of its own indexes.
        #
        # Checking each rename in isolation was not enough, and the error
        # message above was already claiming more than the code did. Three
        # entries passed that are not swaps: `{0: "SQUARE"}` renamed position 0
        # to a name belonging to position 3, shadowing the real SQUARE to
        # SQUARE_3 - which is `{0: "HARD_SYNC"}` again, spelled with a name from
        # the list. And a HALF swap, `{5: "WHITE_NS"}` alone, emitted WHITE_NS
        # and WHITE_NS_6 and deleted PINK_NS from a public enum with no error.
        #
        # A swap is a closed cycle: the positions being renamed and the
        # positions whose names are being used are the same set.
        moved = {index: available[member] for index, (member, _) in by_index.items()}
        if set(moved) != set(moved.values()):
            raise SystemExit(
                f"MEANING_DISAGREEMENTS for {labels} renames positions "
                f"{sorted(moved)} using the names of positions "
                f"{sorted(set(moved.values()))}. Those have to be the same set: "
                f"this table says two or more positions of one list are "
                f"exchanged, and anything else either invents a claim or "
                f"deletes a member that nothing then emits.")


#: Characters that must become a word rather than an underscore, because the
#: underscore would lose the distinction. "+" and "-" appear as a whole option
#: name on a Rotary's direction switch.
WORDS = {"+": "PLUS", "-": "MINUS", "%": "PCT", "&": "AND", "/": "_"}

#: Where a human's screen readings live. One row per POSITION of one list, not
#: one row per list, because the pairing is the evidence: "index 2 of this list
#: showed 'Gate'" is a fact someone can check, and "this list is fine" is not.
#:
#: Beside `tests/fixtures/catalog/scales.json` on purpose - screen readings are
#: evidence and this repo keeps them in one place (CLAUDE.md), so the generator
#: reaches into tests/ rather than keeping a second copy that can drift.
#:
#: Each row carries a ``method``: ``"driven"`` when the wire position was known
#: and the screen read at it, ``"list"`` when the control's own choices were
#: transcribed in order, and ``"looked"`` for a row recording that a control is
#: not drawn at all. `tests/test_option_audit.py` enforces what follows from it;
#: nothing in this script reads the field, which is why the helper that used to
#: sit here was deleted rather than left looking load-bearing.
#:
#: **How to take a reading.** A control's display order is NOT its wire order,
#: and assuming it is nearly put three backwards names in here on 2026-09-14.
#: A list of three or more may be read off the control in order, but at least
#: two positions must then be DRIVEN and read back, one of them awkward - the
#: "16 Beats" that breaks QUANTIZE's counting, the "4 Alt" that breaks TAP
#: PRESET's. A TWO-position control is read only by driving each position: read
#: as a list, RECORD MODE, DUPLICATE MODE and CURVE each came back reversed, and
#: driving position 0 showed all three agreed with the catalog after all.
READINGS = (pathlib.Path(__file__).resolve().parents[1]
            / "tests" / "fixtures" / "catalog" / "option_readings.json")


def load_readings(snapshot: str) -> dict:
    """``{labels: {index: row}}`` for one snapshot's readings.

    Keyed by the LABELS rather than by a model or a parameter, because a list is
    audited once and used by up to 28 parameters. A reading still records which
    parameter it was taken on - that is how a disputed row gets re-driven.

    Readings are per snapshot and never inherited. A 4.1.0 catalog that spells a
    list differently has not been read just because 4.0.1 was: the one list we
    can compare across firmwares already differs, by the U+00A4 separator that
    appears in 27 of 4.1.0's labels and none of 4.0.1's.
    """
    if not READINGS.exists():
        # NOT an empty dict. Returning one rewrites options.py with all 113
        # lists stamped "NOT audited", erasing every audit, and prints success -
        # the same silent-wrong-path failure the CATALOGS comment above was
        # written about. The file is committed; if it is not here, the run is
        # wrong, not the record.
        raise SystemExit(
            f"no readings at {READINGS}. That file is committed, so this is a "
            f"wrong path rather than an empty audit - regenerating now would "
            f"erase every recorded reading from options.py.")
    rows = json.loads(READINGS.read_text(encoding="utf-8"))
    out = collections.defaultdict(lambda: collections.defaultdict(list))
    for row in rows:
        if row["snapshot"] != snapshot:
            continue
        out[tuple(row["labels"])][row["index"]].append(row)
    return {labels: dict(by_index) for labels, by_index in out.items()}


def is_symbol(rows: list) -> bool:
    """True when the unit DRAWS this position instead of writing it.

    The metronome's beat cells are the case: the four states appear as a filled
    circle, an empty one, and a filled one with a dot above or below. There is
    no text on screen to agree or disagree with the catalog, so such a reading
    records what was drawn and never counts as a mismatch. Confirmed again
    2026-09-14 - the four drawings came back in the recorded order.
    """
    return any(r.get("kind") == "symbol" for r in rows)


def screen_word(labels: tuple, index: int, rows: list) -> str:
    """What the screen showed at one position, across every reading of it.

    A list is shared by up to 28 parameters and read on whichever block is to
    hand, so the same position can be read twice on two different blocks. Two
    readings that AGREE are corroboration. Two that disagree mean the list is
    not really one list - the same ``stepNames`` string is drawn two ways - and
    that is a finding about the device, so it stops the generator instead of
    letting one row win by file order.
    """
    words = {r["screen"] for r in rows}
    if len(words) > 1:
        where = "; ".join(f"{r['model']} {r['param']} read {r['screen']!r}"
                          for r in rows)
        raise SystemExit(
            f"readings disagree for {labels} at index {index}: {where}. "
            f"Two parameters sharing a stepNames string show different words, "
            f"so they are not one list. Record it in docs/domain-model.md.")
    return rows[0]["screen"]


def audit_status(labels: tuple, readings: dict) -> str | None:
    """What is known about whether this list's words are the screen's.

    ``"audited"`` when every position has been read off the unit, ``"drawn"``
    when every position was read but the unit draws pictures rather than words,
    ``"partial"`` when only some, ``"absent"`` when somebody looked for the
    control and the unit does not draw it, and ``None`` when nobody has looked.

    A half-read list is NOT audited. It is the most tempting place to round up -
    a 21-entry note-length list read at four positions feels checked - and
    rounding up is what makes an unaudited list indistinguishable from a checked
    one, which is the whole thing this stamp exists to prevent.

    **``"absent"`` comes from an observation, never from the catalog's
    ``hidden`` flag.** The first version of this derived it from the flag, and
    hardware disproved the rule on the day it was written: of the six lists used
    only by parameters the catalog marks hidden, five were looked for on the
    unit and were not drawn - and the sixth, a Mono Synth's ``OSC1 WAVE``, is on
    the screen, on a tab called Oscillator, with its own icons. So the flag is a
    hint about the vendor's intent and not a fact about the screen, and a status
    built on it would have declared a visible control permanently uncheckable.
    ADR-0010 is the precedent: a rule about a parameter attribute, plausible,
    and false on the unit. An ``absent`` row records WHERE somebody looked, so
    the claim can be re-checked the way a reading can.
    """
    seen = dict(readings.get(labels) or {})
    absent = [r for rows in seen.values() for r in rows if r.get("kind") == "absent"]
    positions = {i: rows for i, rows in seen.items()
                 if not all(r.get("kind") == "absent" for r in rows)}
    if absent and positions:
        raise SystemExit(
            f"{labels} is recorded both as read and as not drawn: "
            f"{absent[0]['model']} {absent[0]['param']} was looked for and was "
            f"not there, yet positions {sorted(positions)} have readings. One "
            f"of the two observations is wrong; neither should win by file "
            f"order.")
    if absent:
        return "absent"
    seen = positions
    if not seen:
        return None
    if set(seen) != set(range(len(labels))):
        return "partial"
    # Every position read - but a list the unit DRAWS has had no words checked,
    # and calling that "audited" is the overstatement this whole stamp exists
    # to prevent. It gets its own answer.
    if all(is_symbol(rows) for rows in seen.values()):
        return "drawn"
    return "audited"


def audit_lines(labels: tuple, readings: dict, indent: str = "    ") -> list[str]:
    """The docstring paragraph stating what is known about this list's words."""
    status = audit_status(labels, readings)
    seen = readings.get(labels, {})
    if status == "absent":
        where = ", ".join(sorted({f"{r['model']}'s {r['param']}" for rows
                                  in readings.get(labels, {}).values()
                                  for r in rows if r.get("kind") == "absent"}))
        return [indent + "NOT DRAWN by the unit, so these words cannot be held",
                indent + "against a screen. Looked for on " + where + " and not",
                indent + "found on any page of the block (2026-09-14). This is",
                indent + "an observation, not the catalog's ``hidden`` flag - a",
                indent + "list marked hidden IS drawn on a Mono Synth."]
    if status == "drawn":
        return [indent + "Every position was read on the unit, but the screen",
                indent + "DRAWS them rather than naming them, so these WORDS",
                indent + "remain unchecked - what was confirmed is the order and",
                indent + "the behaviour, not the spelling. See",
                indent + "``option_readings.json`` for the pictures."]
    if status is None:
        return [indent + "NOT audited against the screen. These names are the",
                indent + "catalog's ``stepNames``, which is not known to be the",
                indent + "wording the unit draws."]
    dates = sorted({r["read_on"] for rows in seen.values() for r in rows})
    when = dates[0] if len(dates) == 1 else f"{dates[0]}..{dates[-1]}"
    differ = [i for i, rows in sorted(seen.items())
              if not is_symbol(rows) and screen_word(labels, i, rows) != labels[i]]
    drawn = sorted(i for i, rows in seen.items() if is_symbol(rows))
    if status == "partial":
        lines = [indent + f"PARTLY audited against the screen ({when}): "
                 f"{len(seen)} of {len(labels)} positions read.",
                 indent + "The rest are the catalog's ``stepNames``, unchecked."]
    else:
        lines = [indent + f"Audited against the unit's screen {when}: "
                 f"all {len(labels)} positions read."]
    if drawn:
        lines.append(indent + "The unit DRAWS these positions rather than naming "
                     "them, so the")
        lines.append(indent + "reading records the picture; see "
                     "``option_readings.json``.")
    if differ:
        wrong = sorted(MEANING_DISAGREEMENTS.get(tuple(labels), {}))
        spelling = [i for i in differ if i not in wrong]
        if spelling:
            lines.append(indent + "The screen SPELLS "
                         + ", ".join(str(i) for i in spelling)
                         + " differently; its word is beside the member.")
        if wrong:
            lines.append(indent + "The catalog is WRONG at "
                         + ", ".join(str(i) for i in wrong)
                         + " - not a spelling, a different thing. The member "
                         + "name follows")
            lines.append(indent + "the screen, and naming these by the catalog's "
                         + "string is refused.")
    return lines


def member_name(label: str, labels: tuple = (), index: int | None = None) -> str:
    """'Lo Pass' -> 'LO_PASS'; '1/64T' -> 'N1_64T'; '-6' -> 'MINUS_6'.

    Where a driven reading shows the screen means something DIFFERENT here -
    not merely spells it differently - the screen's meaning wins and the name
    comes from `MEANING_DISAGREEMENTS`.
    """
    corrected = MEANING_DISAGREEMENTS.get(tuple(labels), {}).get(index)
    if corrected is not None:
        return corrected[0]
    text = label.strip()
    if text in SPELLING_FIXES:
        return SPELLING_FIXES[text]
    if text in WORDS:
        return WORDS[text]
    if text.startswith("-") and text[1:].strip():
        text = "MINUS " + text[1:]
    if text.startswith("+") and text[1:].strip():
        text = "PLUS " + text[1:]
    text = text.replace("%", " PCT").replace("&", " AND ")
    cleaned = re.sub(r"[^0-9a-zA-Z]+", "_", text).strip("_").upper()
    if not cleaned:
        return "BLANK"
    if cleaned[0].isdigit():
        cleaned = "N" + cleaned
    if keyword.iskeyword(cleaned.lower()):
        cleaned += "_"
    return cleaned


def _concept(param_name: str) -> str:
    """The name with its trailing index stripped: 'STEPSTATE7' -> 'STEPSTATE'.

    Lists shared by a numbered family - the 13 metronome beats, a multi-band
    EQ's per-band switches - would otherwise have 13 equally common names and
    pick one arbitrarily.
    """
    return re.sub(r"\d+$", "", param_name).strip() or param_name


def class_name(concept: str) -> str:
    """'SYNC NOTE' -> 'SyncNote'; 'DYN MODE' -> 'DynMode'."""
    cleaned = re.sub(r"[^0-9a-zA-Z ]+", " ", concept)
    name = "".join(w[:1].upper() + w[1:].lower() for w in cleaned.split())
    if not name:
        name = "Choice"
    if name[0].isdigit():
        name = "N" + name
    if keyword.iskeyword(name.lower()):
        name += "_"
    return name


def collect_all(cat: catalog.ModelCatalog) -> dict:
    """``{labels: [(model, parameter), ...]}`` for every fixed list in the catalog.

    Every one, including the two that become a bool and the one already
    published by hand. ``OPTION_AUDIT`` covers these rather than only the
    enums: a list with no enum is still a list whose words nobody has checked,
    and the Off/On pair alone is 247 parameters. Counting only the enums would
    report the job as smaller than it is.
    """
    lists = collections.defaultdict(list)
    for model in cat:
        for p in model.parameters:
            if not p.options or p.dynamic:
                continue
            lists[p.options].append((model, p))
    return lists


def collect(cat: catalog.ModelCatalog) -> dict:
    """``{labels: [(model, parameter name), ...]}`` for every list that gets an enum."""
    return {labels: users for labels, users in collect_all(cat).items()
            if tuple(o.lower() for o in labels) not in BOOLEAN_LISTS
            and labels not in NOT_THE_SCREENS_WORDS}


def _best_concept(users: list) -> str:
    """The parameter name that most often carries this list.

    Ties go to the shortest, then alphabetical, so regenerating from the same
    catalog produces the same file rather than following dict order.
    """
    counts = collections.Counter(_concept(p.name) for _, p in users)
    return min(counts.items(), key=lambda kv: (-kv[1], len(kv[0]), kv[0]))[0]


def name_lists(lists: dict) -> dict:
    """Give each list a class name, from the concept that most often uses it.

    Concepts collide, and heavily: 14 different lists are somebody's ``MODE``
    and 6 are somebody's ``SYNC NOTE``. A numeric suffix would name them
    ``Mode2``, ``Mode2_``, ``Mode2__`` and so on, which is unusable.

    A colliding list is qualified by its MODEL instead, because that is what a
    caller has in hand - they are setting a parameter on a block they chose. The
    two-model cases are almost all an (M)/(ST) pair of the same pedal, so the
    shortest name in the group is the pedal. Only where a list is spread across
    more models than that does it fall back to the option count, and then to its
    first option.
    """
    chosen, taken = {}, {}
    ordered = sorted(lists.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    concepts = collections.Counter(class_name(_best_concept(u))
                                   for u in lists.values())
    for labels, users in ordered:
        base = class_name(_best_concept(users))
        name = base
        if concepts[base] > 1:
            models = sorted({m.name for m, _ in users}, key=lambda n: (len(n), n))
            if len(models) <= 3:
                name = class_name(models[0]) + base
            else:
                name = f"{base}{len(labels)}"
            if name in taken:
                name = f"{base}{member_name(labels[0]).title().replace('_', '')}"
        while name in taken:
            name += "_"
        taken[name] = labels
        chosen[labels] = name
    return chosen


def render_enum(name: str, labels: tuple, users: list, readings: dict) -> list[str]:
    models = sorted({m.name for m, _ in users})
    params = sorted({p.name for _, p in users})
    summary = (f"{len(users)} parameter" + ("s" if len(users) != 1 else "")
               + f" use this list: {', '.join(params[:6])}"
               + (", ..." if len(params) > 6 else ""))
    if len(models) <= 3:
        where = f"    On {', '.join(models)}."
    else:
        where = (f"    On {len(models)} models, among them "
                 f"{', '.join(models[:3])}.")
    lines = (["", "", f"class {name}(IntEnum):", f'    """{summary}', "", where, ""]
             + audit_lines(labels, readings) + ['    """', ""])
    seen = readings.get(labels, {})
    used = {}
    for index, label in enumerate(labels):
        member = member_name(label, labels, index)
        if member in used:
            member = f"{member}_{index}"
        used[member] = index
        # Three things the member name can hide, in order of how much they
        # matter: the screen disagreeing with the catalog, the catalog's own
        # spelling being mangled, and nothing.
        rows = seen.get(index)
        if rows and is_symbol(rows):
            note = f"    # drawn as {screen_word(labels, index, rows)!r}"
        elif rows and screen_word(labels, index, rows) != label:
            note = (f"    # screen: {screen_word(labels, index, rows)!r}; "
                    f"catalog: {label!r}")
        elif member_name(label, labels, index) != label.upper():
            note = f"    # {label!r}"
        else:
            note = ""
        lines.append(f"    {member} = {index}{note}")
    return lines


def render(cat: catalog.ModelCatalog, snapshot: str) -> str:
    every = collect_all(cat)
    lists = collect(cat)
    names = name_lists(lists)
    total = sum(len(v) for v in lists.values())
    readings = load_readings(snapshot)
    check_corrections(readings, every)
    # {labels: {index: the catalog string that means the wrong thing here}}
    CONTESTED = {labels: {i: labels[i] for i in by_index}
                 for labels, by_index in MEANING_DISAGREEMENTS.items()
                 if labels in every}
    # Counted, never written down: the literal that used to sit in the header
    # below is exactly the kind that drifts, and one beside it already had.
    bools = sum(len(users) for labels, users in every.items()
                if tuple(o.lower() for o in labels) in BOOLEAN_LISTS)
    status = {labels: audit_status(labels, readings) for labels in every}
    # Of the three lists with no enum, the parameters whose words are still
    # open. This was the literal 260 - every parameter those lists cover - and
    # stayed 260 after 247 of them were read, so the shipped module claimed more
    # was outstanding than actually was.
    unchecked_no_enum = sum(
        len(users) for labels, users in every.items()
        if labels not in names and status[labels] in (None, "partial", "drawn"))
    done = sum(1 for v in status.values() if v == "audited")
    part = sum(1 for v in status.values() if v == "partial")
    # These four words are the statuses `audit_status` actually returns. An
    # earlier version counted "hidden", which this file stopped returning in the
    # same commit that introduced it, so the generated header reported "0
    # impossible" and folded five lists into the unread number - while restating
    # the flag-derived claim the measurement had just disproved, at the top of
    # the shipped module. Anything counting statuses reads them from here.
    off = sum(1 for v in status.values() if v == "absent")
    pics = sum(1 for v in status.values() if v == "drawn")

    lines = [
        '"""The choices a list-valued parameter offers, as enums.',
        "",
        f"GENERATED by ``scripts/generate_options.py --snapshot {snapshot}``. Do not edit by hand.",
        "",
        "A list parameter stores ``index / (count - 1)`` on the wire, so picking",
        "an option means knowing its position. These name the positions::",
        "",
        "    qc.set_param_option(block, 'DYN MODE', options.DynMode.GATE)",
        "",
        f"{len(lists)} enums cover {total} parameters, because the same list means",
        "the same thing wherever it appears - the note-length list is shared by",
        "``SYNC NOTE``, ``SYNC NOTE L``, ``SYNC NOTE R`` and two more.",
        "",
        f"**A two-option Off/On parameter gets no enum.** {bools} parameters",
        "offer exactly those, and ``True`` says everything ``OffOn.ON`` would::",
        "",
        "    qc.set_param(block, 'SYNC', True)",
        "",
        "**A dynamic list gets no enum either.** Twelve parameters build their",
        "list from the preset - it includes one entry per upstream block - so",
        "read those with :func:`~pyquadcortex.protocol.client.param_options`.",
        "",
        "The member names are ours; the wire's strings are the device's, and",
        "``OPTION_LABELS`` keeps them verbatim. Where the two differ the label is",
        "in a comment beside the member.",
        "",
        "**These names come from the catalog, not from the screen.** ``stepNames``",
        "is the device's own vocabulary, and where it can be compared against what",
        "the device RENDERS it does not match - the catalog writes ``In 1`` and",
        "``Ret 1/2`` where the device writes ``Input 1`` and ``Return 1/2``. So",
        "each enum says whether a human has read it off the unit, and",
        f"``OPTION_AUDIT`` publishes that for all {len(every)} fixed lists:",
        f"{done} audited, {pics} drawn (read, but the unit draws pictures",
        f"rather than words), {off} not drawn at all, {part} partly, and",
        f"{len(every) - done - part - off - pics} that nobody has looked at.",
        '"""',
        "from enum import IntEnum",
    ]

    for labels, users in sorted(lists.items(), key=lambda kv: names[kv[0]]):
        lines += render_enum(names[labels], labels, users, readings)

    lines += ["", "", "#: Each enum's options as the DEVICE spells them, in wire order.",
              "#:",
              "#: The member names above are ours - mangled to be valid Python, and",
              "#: corrected where the device has a typo. These are the strings the",
              "#: unit actually uses, which is what a dynamic list matches against.",
              "OPTION_LABELS = {"]
    for labels, _ in sorted(lists.items(), key=lambda kv: names[kv[0]]):
        lines.append(f"    {names[labels]}: {labels!r},")
    lines.append("}")

    lines += ["", "",
              "#: Catalog names a driven reading showed to be WRONG about what the",
              "#: position means, as ``{labels: {index: the catalog's name}}``.",
              "#:",
              "#: One entry: a Mono Synth's oscillator waveforms, where the catalog",
              "#: has pink and white noise swapped. The strings stay in",
              "#: ``OPTION_LABELS`` because the device publishes them, but naming",
              "#: one of these in ``set_param_option`` is refused rather than",
              "#: silently selecting the other noise.",
              "OPTION_CONTESTED = {"]
    for labels in sorted(CONTESTED, key=lambda l: (names.get(l, ""), l)):
        lines.append(f"    {labels!r}: {CONTESTED[labels]!r},")
    lines.append("}")

    lines += ["", "",
              "#: Whether anyone has held this list against the unit's SCREEN.",
              "#:",
              "#: Five answers, and every one of them is a recorded observation in",
              "#: ``tests/fixtures/catalog/option_readings.json``:",
              "#:",
              '#: - ``"audited"`` - every position was read off the screen. It does',
              "#:   NOT mean the words matched: a list can be audited AND",
              "#:   disagree, which is the whole point of reading one. The enum's",
              "#:   own docstring says which positions differ and how.",
              '#: - ``"drawn"`` - every position was read, but the unit DRAWS them',
              "#:   rather than naming them, so these words are still unchecked.",
              '#: - ``"partial"`` - some positions were read and some were not.',
              '#: - ``"absent"`` - somebody looked for the control and the unit',
              "#:   does not draw it. NOT inferred from the catalog's ``hidden``",
              "#:   flag, which marks a Mono Synth's ``OSC1 WAVE`` that is plainly",
              "#:   on screen.",
              "#: - ``None`` - the names are the catalog's and nobody has looked.",
              "#:",
              "#: Keyed by the LABELS rather than by the enum, because the two lists",
              "#: that become a bool and the one published by hand have no enum and",
              f"#: are {unchecked_no_enum} parameters whose words still need",
              "#: checking - the rest of those three lists have been read.",
              "OPTION_AUDIT = {"]
    for labels in sorted(every, key=lambda l: (names.get(l, ""), l)):
        tag = f"{status[labels]!r}" if status[labels] else "None"
        note = f"  # {names[labels]}" if labels in names else "  # no enum"
        lines.append(f"    {labels!r}: {tag},{note}")
    lines.append("}")

    unread = sorted((labels for labels in every if status[labels] is None),
                    key=lambda l: (-len(every[l]), len(l), l))
    unread_params = sum(len(every[l]) for l in unread)
    lines += ["", "",
              "#: How many PARAMETERS each fixed list decides, as",
              "#: ``{labels: count}``.",
              "#:",
              "#: Published beside ``OPTION_AUDIT`` because the two are read",
              "#: together: a list nobody has looked at matters in proportion to",
              "#: how many controls it governs, and the numbers were being quoted",
              "#: in prose from one-off counts before this existed. Keyed by the",
              "#: LABELS, like the audit, so the three lists with no enum are in",
              "#: it too.",
              "#:",
              f"#: Totals on this snapshot: {sum(len(u) for u in every.values())}",
              f"#: parameters across {len(every)} lists, of which"
              f" {unread_params} across",
              f"#: {len(unread)} lists nobody has read. The work is long-tailed -"
              " the",
              f"#: five biggest unread lists cover"
              f" {sum(len(every[l]) for l in unread[:5])} of those"
              f" {unread_params}.",
              "OPTION_USAGE = {"]
    for labels in sorted(every, key=lambda l: (names.get(l, ""), l)):
        note = f"  # {names[labels]}" if labels in names else "  # no enum"
        lines.append(f"    {labels!r}: {len(every[labels])},{note}")
    lines.append("}")

    lines += ["", "", "__all__ = ["]
    for labels, _ in sorted(lists.items(), key=lambda kv: names[kv[0]]):
        lines.append(f'    "{names[labels]}",')
    lines += ['    "OPTION_LABELS",', '    "OPTION_AUDIT",',
              '    "OPTION_USAGE",', '    "OPTION_CONTESTED",', "]", ""]
    return "\n".join(lines)


def load_payload(path: str | None) -> bytes:
    if path:
        return pathlib.Path(path).read_bytes()
    import pyquadcortex.protocol as pq
    qc = pq.connect()
    try:
        return qc._fetch_model_repo()
    finally:
        qc.disconnect()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--payload")
    ap.add_argument("--snapshot", required=True,
                    help="snapshot package to write, named by CorOS version, e.g. coros_4_1_0")
    args = ap.parse_args()

    cat = catalog.parse_model_repo(load_payload(args.payload))
    text = render(cat, snapshot=args.snapshot)
    directory = _snapshots.ensure_snapshot_package(CATALOGS, args.snapshot)
    out = directory / "options.py"
    out.write_text(text, encoding="utf-8")
    # Again, now the module is on disk: the package's __init__ imports
    # exactly what is there, so a snapshot generated one module at a
    # time imports at every step instead of only at the last.
    _snapshots.ensure_snapshot_package(CATALOGS, args.snapshot)
    audited = text.count(": 'audited'")
    print(f"wrote {out} ({text.count('class ')} enums, {audited} audited lists)")


if __name__ == "__main__":
    main()
