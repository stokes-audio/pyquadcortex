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
        --payload model_repo_payload.bin

Three decisions this generator makes:

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
        return {}
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


def audit_status(labels: tuple, readings: dict, users: list = ()) -> str | None:
    """What is known about whether this list's words are the screen's.

    ``"audited"`` when every position has been read off the unit, ``"partial"``
    when only some, ``"hidden"`` when no parameter using it is on the screen at
    all, and ``None`` when nobody has looked.

    A half-read list is NOT audited. It is the most tempting place to round up -
    a 21-entry note-length list read at four positions feels checked - and
    rounding up is what makes an unaudited list indistinguishable from a checked
    one, which is the whole thing this stamp exists to prevent.

    ``"hidden"`` is its own answer rather than a kind of unread, because the two
    call for opposite things. An unread list is work somebody should do; a
    hidden one is work nobody can do, and leaving it in the unread pile makes
    the job look bigger than it is forever. Six lists are hidden, 38 parameters,
    and they include the two that looked most like typos - which is the likely
    explanation for the typos.
    """
    seen = readings.get(labels)
    if seen:
        return "audited" if set(seen) == set(range(len(labels))) else "partial"
    if users and all(p.hidden for _, p in users):
        return "hidden"
    return None


def audit_lines(labels: tuple, readings: dict, users: list = (),
                indent: str = "    ") -> list[str]:
    """The docstring paragraph stating what is known about this list's words."""
    status = audit_status(labels, readings, users)
    seen = readings.get(labels, {})
    if status == "hidden":
        return [indent + "CANNOT be audited: every parameter using this list is",
                indent + "marked hidden, so the unit never draws these words and",
                indent + "there is no screen text to hold them against."]
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
        lines.append(indent + "The screen and the catalog DISAGREE at "
                     + ", ".join(str(i) for i in differ)
                     + "; the screen's word is beside the member.")
    return lines


def member_name(label: str) -> str:
    """'Lo Pass' -> 'LO_PASS'; '1/64T' -> 'N1_64T'; '-6' -> 'MINUS_6'."""
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
             + audit_lines(labels, readings, users) + ['    """', ""])
    seen = readings.get(labels, {})
    used = {}
    for index, label in enumerate(labels):
        member = member_name(label)
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
        elif member_name(label) != label.upper():
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
    status = {labels: audit_status(labels, readings, users)
              for labels, users in every.items()}
    done = sum(1 for v in status.values() if v == "audited")
    part = sum(1 for v in status.values() if v == "partial")
    off = sum(1 for v in status.values() if v == "hidden")

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
        "**A two-option Off/On parameter gets no enum.** 247 parameters offer",
        "exactly those, and ``True`` says everything ``OffOn.ON`` would::",
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
        f"{done} audited, {part} partly, {off} impossible (every parameter",
        f"using them is hidden), {len(every) - done - part - off} unread.",
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
              "#: Whether anyone has held this list against the unit's SCREEN.",
              "#:",
              '#: ``"audited"`` means every position was read on the device and the',
              '#: reading is in ``tests/fixtures/catalog/option_readings.json``.',
              '#: ``"partial"`` means some positions were. ``None`` means the names',
              "#: are the catalog's and nobody has looked.",
              "#:",
              "#: Keyed by the LABELS rather than by the enum, because the two lists",
              "#: that become a bool and the one published by hand have no enum and",
              "#: are still 251 parameters whose words need checking.",
              "OPTION_AUDIT = {"]
    for labels in sorted(every, key=lambda l: (names.get(l, ""), l)):
        tag = f"{status[labels]!r}" if status[labels] else "None"
        note = f"  # {names[labels]}" if labels in names else "  # no enum"
        lines.append(f"    {labels!r}: {tag},{note}")
    lines.append("}")

    lines += ["", "", "__all__ = ["]
    for labels, _ in sorted(lists.items(), key=lambda kv: names[kv[0]]):
        lines.append(f'    "{names[labels]}",')
    lines += ['    "OPTION_LABELS",', '    "OPTION_AUDIT",', "]", ""]
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
    print(f"wrote {out} ({text.count('class ')} enums, "
          f"{text.count(chr(58) + chr(32) + chr(39) + 'audited' + chr(39))} audited lists)")


if __name__ == "__main__":
    main()
