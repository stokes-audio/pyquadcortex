"""The mechanical half of `docs/writing.md`.

The documents in this repository drifted into review transcripts once: capitals
for emphasis, sentences about earlier drafts, paragraphs that grew a qualifier
per review round. These checks catch the part of that a regex can see. The
rest is judgement, and `docs/writing.md` says what to judge by.

Each rule here is short on purpose. A style engine cries wolf and gets turned
off; a short list that names exactly the tells that happened stays on.
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
WRITING_GUIDE = ROOT / "docs" / "writing.md"

#: Files the rules do not govern, and why. Everything else ending `.md` is
#: governed, so a new document has to come through `docs/writing.md`'s table.
NOT_GOVERNED = {
    "code_of_conduct.md": "the Contributor Covenant's own text",
}
#: `.github/` holds issue and pull request forms, which have no prose purpose.
#: The rest are tool directories that can hold third-party READMEs.
NOT_GOVERNED_DIRS = {".github", ".venv", "venv", "env", ".pytest_cache", ".claude",
                     "node_modules", "build", "dist", "htmlcov", ".mypy_cache"}


def governed_documents():
    found = []
    for path in sorted(ROOT.rglob("*.md")):
        rel = path.relative_to(ROOT)
        if rel.parts[0] in NOT_GOVERNED_DIRS or rel.name in NOT_GOVERNED:
            continue
        found.append(path)
    return found


DOCUMENTS = governed_documents()


def _ids(path):
    return path.relative_to(ROOT).as_posix()


def _governed_text(path):
    """The part of a document the rules apply to.

    `changelog.md`'s released entries are a record of what was said at release
    time, so only the text before the first released version is governed.
    """
    text = path.read_text(encoding="utf-8")
    if path.name == "changelog.md":
        cut = re.search(r"^## \d+\.\d+", text, re.MULTILINE)
        if cut:
            text = text[:cut.start()]
    return text


def _without_code(text):
    """Drop fenced code blocks and inline code spans; keep line structure.

    An inline span may wrap across a line break, so spans are removed from the
    whole text with their newlines kept, and line numbers stay right.
    """
    out, fence = [], None
    for line in text.splitlines():
        opened = re.match(r"\s*(`{3,}|~{3,})", line)
        if fence is None and opened:
            fence = opened.group(1)[0] * 3
            out.append("")
            continue
        if fence is not None:
            if line.strip().startswith(fence):
                fence = None
            out.append("")
            continue
        out.append(line)
    joined = "\n".join(out)
    joined = re.sub(r"`[^`]*`",
                    lambda m: "`code`" + "\n" * m.group(0).count("\n"), joined)
    # A link target or a bare URL is an address, not prose.
    joined = re.sub(r"\]\([^)\s]*\)", "](url)", joined)
    return re.sub(r"https?://\S+", "url", joined)


# -- one purpose per document ---------------------------------------------------

PURPOSE_WORDS = 40
NEGATION = re.compile(r"\b(not|never|nor|neither|except|nothing|no|cannot)\b|n't\b",
                      re.IGNORECASE)


@pytest.mark.parametrize("path", DOCUMENTS, ids=_ids)
def test_the_document_opens_with_one_purpose_line(path):
    """The line after the title says what the document is for, in 40 words.

    It may not say what the document is not for. A purpose that grows into
    "X, and not Y, and never Z" has stopped being a purpose, which is the drift
    this guards against.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    title = next((i for i, l in enumerate(lines) if l.startswith("# ")), None)
    assert title is not None, f"{_ids(path)} has no `# ` title"
    after = [l for l in lines[title + 1:] if l.strip()]
    assert after and after[0].startswith("> Purpose:"), (
        f"{_ids(path)}: the first line after the title must be `> Purpose: ...`"
        f" (see docs/writing.md); found {after[0][:60]!r}" if after else
        f"{_ids(path)} has nothing after its title")
    purpose = after[0][len("> Purpose:"):].strip()
    words = purpose.split()
    assert len(words) <= PURPOSE_WORDS, (
        f"{_ids(path)}: the purpose line is {len(words)} words; the limit is "
        f"{PURPOSE_WORDS}")
    hit = NEGATION.search(purpose)
    assert hit is None, (
        f"{_ids(path)}: the purpose line says {hit.group(0)!r}. A purpose says "
        f"what the document is for, never what it is not for; put the exclusion "
        f"where the excluded thing belongs and link to it")


def test_every_governed_document_is_named_in_the_writing_guide():
    """A new document registers its purpose in `docs/writing.md`'s table."""
    guide = WRITING_GUIDE.read_text(encoding="utf-8")
    missing = [_ids(p) for p in DOCUMENTS if f"`{_ids(p)}`" not in guide]
    assert not missing, (
        f"docs/writing.md does not list {missing}. Add a row to its document "
        f"table saying what each is for")


# -- no capitals for emphasis ---------------------------------------------------

#: Acronyms and proper nouns that are written in capitals because that is how
#: they are spelled. A label the unit shows, a protobuf action or a constant is
#: NOT on this list: those go in backticks.
ACRONYMS = {
    "USB", "HID", "MIDI", "CPU", "DSP", "ADR", "PR", "CI", "EQ", "IR", "XML",
    "JSON", "API", "OS", "ID", "LED", "RX", "TX", "PCM", "WAV", "CC", "PC", "MSB",
    "LSB", "DIN", "HP", "DI", "ARGB", "UI", "URL", "CRC", "AES", "GCM", "SHA",
    "SHA1", "MIT", "IEEE", "RMS", "DAW", "ESS", "MCP", "SDK", "HTML", "SD", "FX",
    "XLR", "HPF", "LPF", "KB", "GB", "BPM", "OK", "SSH", "IDE", "MAC", "NET",
    "XXTEA",
    "CPython",
    # Proper nouns whose second letter is upper case
    "IOKit", "USBPcap",
    # File names this repository refers to by name
    "README", "CLAUDE", "STEERING",
    # Names of things on the unit, quoted as the unit spells them
    "PCOM", "AO900", "TWN", "US",
}

SHOUT = re.compile(r"(?<![A-Za-z0-9_/`])([A-Z][A-Z0-9_]{1,}[A-Za-z0-9_]*)(?![A-Za-z0-9_`])")


def _prose_lines(text):
    """(line number, line) for the prose the shouting rule covers.

    Tables are data, so a label in a table cell is not emphasis; code is code.
    Headings and paragraphs are what the rule is about.
    """
    for number, line in enumerate(_without_code(text).splitlines(), 1):
        if line.lstrip().startswith("|"):
            continue
        yield number, line


@pytest.mark.parametrize("path", DOCUMENTS, ids=_ids)
def test_no_capitals_for_emphasis(path):
    """A word in capitals outside code is an acronym, or it is shouting.

    A label the unit shows (`VOLUME`), a protobuf action (`READ`) and a
    constant (`BURST_TAIL`) go in backticks. Everything else in capitals had
    better be on the acronym list above, or it is emphasis, and emphasis
    belongs to the sentence's structure rather than its typography.
    """
    if path == WRITING_GUIDE:
        pytest.skip("the guide quotes the rule's own examples")
    shouted = []
    for number, line in _prose_lines(_governed_text(path)):
        for word in SHOUT.findall(line):
            if word in ACRONYMS or (word.endswith("s") and word[:-1] in ACRONYMS):
                continue
            if re.fullmatch(r"[A-Z]\d*", word):
                continue
            shouted.append(f"line {number}: {word}")
    assert not shouted, (
        f"{_ids(path)} shouts: {shouted[:12]}"
        + (" ..." if len(shouted) > 12 else "")
        + ". Put a device label, action or constant in backticks; lower-case "
          "emphasis; or, for an acronym, add it to ACRONYMS in this file")


# -- no sentences written for a reviewer --------------------------------------

#: Each phrase marks a sentence about the document itself, its history or its
#: reviewer, rather than about the unit or the code. From `docs/writing.md`.
TELLS = (
    "an earlier version of this", "an earlier draft", "used to say",
    "this paragraph", "this section used to",
    "worth stating", "worth noting", "worth keeping", "worth recording",
    "worth the sentence",
    "load-bearing", "cuts the other way", "stated rather than",
    "rather than buried", "the honest", "honestly",
)


@pytest.mark.parametrize("path", DOCUMENTS, ids=_ids)
def test_no_review_history_in_the_prose(path):
    """A document records what is known. How the argument went is not that."""
    if path == WRITING_GUIDE:
        pytest.skip("the guide lists the phrases")
    text = " ".join(_without_code(_governed_text(path)).lower().split())
    found = sorted({tell for tell in TELLS if tell in text})
    assert not found, (
        f"{_ids(path)} contains {found}. Rewrite the sentence to state the fact; "
        f"the history goes in the commit message or the lab repository")


def test_the_guide_and_this_test_agree_on_the_phrases():
    """The list a reader sees and the list the test uses are the same list."""
    guide = WRITING_GUIDE.read_text(encoding="utf-8")
    missing = [t for t in TELLS if f"`{t}`" not in guide]
    assert not missing, f"docs/writing.md does not list {missing}"


# -- sizes ----------------------------------------------------------------------

PARAGRAPH_WORDS = 150
CLAUDE_BULLET_WORDS = 60


def _blocks(text):
    """Paragraphs and list items as (line number, words), outside code and tables.

    A list item runs from its marker to the next marker or blank line; a
    paragraph runs to the next blank line. A blockquote line is a paragraph.
    """
    blocks, current, start = [], [], None
    for number, line in enumerate(_without_code(text).splitlines(), 1):
        stripped = line.strip()
        is_item = bool(re.match(r"^(\s*[-*]|\s*\d+\.)\s+", line))
        if not stripped or stripped.startswith("#") or stripped.startswith("|"):
            if current:
                blocks.append((start, " ".join(current)))
                current, start = [], None
            continue
        if is_item and current:
            blocks.append((start, " ".join(current)))
            current, start = [], None
        if not current:
            start = number
        current.append(re.sub(r"^([-*]|\d+\.)\s+", "", stripped) if is_item else stripped)
    if current:
        blocks.append((start, " ".join(current)))
    return [(n, len(b.split())) for n, b in blocks]


#: Documents whose paragraphs the length rule does not cover, and why.
NOT_CAPPED = {
    "docs/ADR.md": "a Decided record is append-only and is not edited for length; "
                   "new records follow docs/writing.md",
}


@pytest.mark.parametrize("path", DOCUMENTS, ids=_ids)
def test_no_paragraph_runs_past_the_limit(path):
    """A paragraph over 150 words is two ideas, or one idea with its history."""
    if _ids(path) in NOT_CAPPED:
        pytest.skip(NOT_CAPPED[_ids(path)])
    long = [f"line {n}: {w} words" for n, w in _blocks(_governed_text(path))
            if w > PARAGRAPH_WORDS]
    assert not long, (
        f"{_ids(path)} has paragraphs over {PARAGRAPH_WORDS} words: {long}. "
        f"Split them, or move the detail to the document that owns it")


STEERING_ENTRY_WORDS = 80


def test_steering_change_log_entries_stay_short():
    """A change-log entry in STEERING.md is a summary; the story is in the PR.

    `docs/writing.md` gives the shape and the limit. The narrative history that
    grew here before is archived in the lab repository.
    """
    text = (ROOT / "docs" / "STEERING.md").read_text(encoding="utf-8")
    log = text[text.index("## Change Log"):]
    long = []
    for entry in re.split(r"^### ", log, flags=re.MULTILINE)[1:]:
        title, _, body = entry.partition("\n")
        words = len(_without_code(body).split())
        if words > STEERING_ENTRY_WORDS:
            long.append(f"{title.strip()}: {words} words")
    assert not long, (
        f"STEERING.md change-log entries over {STEERING_ENTRY_WORDS} words: {long}. "
        f"Keep what changed, why, and scope; the story goes in the pull request")


def test_claude_md_bullets_stay_short():
    """A rule in CLAUDE.md is one bullet with a pointer, under 60 words.

    The detail lives in the document the bullet points at. A bullet that
    grows past this is carrying evidence, and evidence belongs elsewhere.
    """
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    long = [f"line {n}: {w} words" for n, w in _blocks(text)
            if w > CLAUDE_BULLET_WORDS]
    assert not long, (
        f"CLAUDE.md bullets over {CLAUDE_BULLET_WORDS} words: {long}. Keep the "
        f"rule and point at the document that holds the detail")
