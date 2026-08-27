#!/usr/bin/env python3
"""Generate ``pyquadcortex/protocol/options.py`` - the choices a list offers.

A list-valued parameter stores ``index / (count - 1)`` on the wire, so choosing
"Lo Pass" means knowing it is the fourth entry. The names are in the device's
catalog, in a ``stepNames`` attribute this library read for the first time on
2026-08-26; before that ``set_param_option`` said they were "not in the catalog"
and made every caller pass a preset to read them from.

Source: a device's ModelRepo payload, either live or previously saved.

    python scripts/generate_options.py
    python scripts/generate_options.py --payload model_repo_payload.bin

Three decisions this generator makes:

1. **One enum per distinct LIST, not per parameter.** 527 parameters carry a
   fixed list and they use only 113 distinct ones, because the same list means
   the same thing everywhere: the note-length list is shared by ``SYNC NOTE``,
   ``SYNC NOTE L``, ``SYNC NOTE R``, ``SYNC NOTE A`` and ``SYNC NOTE B``. One
   enum per list is one enum per concept.
2. **``Off,On`` gets no enum.** 247 of those 527 offer exactly "Off" and "On",
   and ``OffOn.ON`` says nothing that ``True`` does not. Those parameters take a
   bool.
3. **The device's spelling is kept on the wire and corrected in the name.**
   ``OPTION_LABELS`` holds the strings verbatim, typos included, because a
   dynamic list is matched by string against the preset. ``SPELLING_FIXES``
   below corrects the MEMBER name only, one reviewed line at a time.
"""
import argparse
import collections
import keyword
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pyquadcortex.protocol import catalog  # noqa: E402

#: Lists that are a boolean wearing a costume. Their parameters take ``True`` and
#: ``False``; no enum is emitted.
BOOLEAN_LISTS = {("off", "on")}

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


def collect(cat: catalog.ModelCatalog) -> dict:
    """``{labels: [(model, parameter name), ...]}`` for every fixed list."""
    lists = collections.defaultdict(list)
    for model in cat:
        for p in model.parameters:
            if not p.options or p.dynamic:
                continue
            if tuple(o.lower() for o in p.options) in BOOLEAN_LISTS:
                continue
            lists[p.options].append((model, p))
    return lists


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


def render_enum(name: str, labels: tuple, users: list) -> list[str]:
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
    lines = ["", "", f"class {name}(IntEnum):",
             f'    """{summary}', "", where, '    """', ""]
    used = {}
    for index, label in enumerate(labels):
        member = member_name(label)
        if member in used:
            member = f"{member}_{index}"
        used[member] = index
        note = f"    # {label!r}" if member_name(label) != label.upper() else ""
        lines.append(f"    {member} = {index}{note}")
    return lines


def render(cat: catalog.ModelCatalog) -> str:
    lists = collect(cat)
    names = name_lists(lists)
    total = sum(len(v) for v in lists.values())

    lines = [
        '"""The choices a list-valued parameter offers, as enums.',
        "",
        "GENERATED by ``scripts/generate_options.py``. Do not edit by hand.",
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
        '"""',
        "from enum import IntEnum",
    ]

    for labels, users in sorted(lists.items(), key=lambda kv: names[kv[0]]):
        lines += render_enum(names[labels], labels, users)

    lines += ["", "", "#: Each enum's options as the DEVICE spells them, in wire order.",
              "#:",
              "#: The member names above are ours - mangled to be valid Python, and",
              "#: corrected where the device has a typo. These are the strings the",
              "#: unit actually uses, which is what a dynamic list matches against.",
              "OPTION_LABELS = {"]
    for labels, _ in sorted(lists.items(), key=lambda kv: names[kv[0]]):
        lines.append(f"    {names[labels]}: {labels!r},")
    lines.append("}")

    lines += ["", "", "__all__ = ["]
    for labels, _ in sorted(lists.items(), key=lambda kv: names[kv[0]]):
        lines.append(f'    "{names[labels]}",')
    lines += ['    "OPTION_LABELS",', "]", ""]
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
    ap.add_argument("--out", default="pyquadcortex/protocol/options.py")
    args = ap.parse_args()

    cat = catalog.parse_model_repo(load_payload(args.payload))
    text = render(cat)
    pathlib.Path(args.out).write_text(text)
    print(f"wrote {args.out} ({text.count('class ')} enums)")


if __name__ == "__main__":
    main()
