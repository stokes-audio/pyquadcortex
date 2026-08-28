"""Structural checks on the published docs.

Both of these defects shipped in 0.34.0 and were found by an outside user:
an unclosed code fence swallowed an entire section of api.md, and the method
table presented eleven module-level functions as methods.
"""
import ast
import pathlib
import re
import textwrap

import pytest

from pyquadcortex import protocol

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = sorted(ROOT.glob("docs/*.md")) + [ROOT / "README.md"]


@pytest.mark.parametrize("doc", DOCS, ids=lambda d: d.name)
def test_code_fences_are_balanced(doc):
    """An odd fence count means a section is rendering inside a code block."""
    if not doc.exists():
        pytest.skip(f"{doc.name} not present")
    opens = sum(1 for line in doc.read_text().splitlines()
                if line.startswith("```"))
    assert opens % 2 == 0, (
        f"{doc.name} has {opens} fence lines - one is unclosed, and everything "
        f"after it renders as code"
    )


def _api_table_rows() -> str:
    text = (ROOT / "docs" / "api.md").read_text()
    section = text[text.index("## Method groups"):text.index("**Rows and columns")]
    return "\n".join(line for line in section.splitlines()
                     if line.startswith("|"))     # table rows only, not prose


def test_api_method_table_entries_exist_where_the_table_says():
    """`name(` in the table must be a real method; `protocol.name(` a real
    module function. help() is the contract; the table must match it."""
    rows = _api_table_rows()
    problems = []
    for m in re.finditer(r"`(protocol\.)?([a-z_][a-z0-9_]*)\(", rows):
        prefixed, name = bool(m.group(1)), m.group(2)
        if prefixed:
            if not callable(getattr(protocol, name, None)):
                problems.append(f"protocol.{name} listed but absent")
        else:
            if not hasattr(protocol.QuadCortex, name):
                hint = (" (it IS a module function - prefix it)"
                        if hasattr(protocol, name) else "")
                problems.append(f"{name} listed as a method but is not{hint}")
    assert not problems, "; ".join(problems)


def test_the_api_table_carries_no_pre_flip_import_paths():
    """A row still spelled the old way is skipped by the check above, not caught.

    That check matches an optional `protocol.` prefix and then a lowercase name,
    so `` `pyquadcortex.blocks(` `` matches nothing at all - the prefix group
    fails, the name group swallows `pyquadcortex`, and it runs into a `.` where
    it wants a `(`. Before the move that row was the case being checked. A rule
    that silently passes over exactly the rows this change could leave behind is
    worth nothing here, so stale spellings are named instead.

    The two exempt prefixes are the two namespaces that exist: `protocol` and
    `device`. The exemption said `model` until the model package was renamed,
    which had it backwards - waving through the dead path and flagging the live
    one. Nothing failed, because api.md documents the protocol layer and carries
    no model rows yet; the first one would have tripped it.
    """
    stale = re.findall(r"`pyquadcortex\.(?!protocol\b|device\b)[a-z_][a-z0-9_.]*\(",
                       _api_table_rows())
    assert not stale, (
        f"docs/api.md still shows {sorted(set(stale))} - the message-level API "
        f"is imported from pyquadcortex.protocol now (ADR-0006)")


# -- the snippets in the prose are code too -----------------------------------
#
# Nothing compiled them until 2026-08-27. Two rounds of triage on the same PR
# found seven that raise if run - a `Block(0, 2, model=5011)` keyword that has
# never existed, a parameter NAMED on a Block carrying no model id, a bare
# number where a value type is now required. `tests/test_examples.py` parses
# `examples/` only, so none of these were visible to it.
#
# These checks are deliberately narrow: they pin the mistakes that actually
# happened, in the places a reader copies from, without pretending to execute
# a snippet that would open a device.

SNIPPET_SOURCES = DOCS + [ROOT / "changelog.md"] + sorted(
    (ROOT / "pyquadcortex").rglob("*.py"))


def _drop_before_blocks(snippet):
    """Remove the OLD-syntax half of a before/after example.

    `docs/migration.md` exists to show the syntax that no longer works, so a
    guard that refused it would refuse the guide for getting past it. The
    exemption is a marker rather than a whole-file skip: everything after
    `# after` is checked like any other snippet, which is the half a reader
    copies.
    """
    kept, skipping = [], False
    for line in snippet.split("\n"):
        marker = line.strip().lower()
        if marker.startswith("# before"):
            skipping = True
            continue
        if marker.startswith("# after"):
            skipping = False
            continue
        kept.append("" if skipping else line)
    return "\n".join(kept)


def _python_snippets(path):
    """Every ```python fence in a .md, or every ``::`` block in a .py docstring."""
    text = path.read_text()
    if path.suffix == ".md":
        return [_drop_before_blocks(f)
                for f in re.findall(r"```python\n(.*?)```", text, re.DOTALL)]
    blocks = []
    for doc in re.findall(r'"""(.*?)"""', text, re.DOTALL):
        for chunk in re.split(r"::\n", doc)[1:]:
            kept, depth = [], None
            for line in chunk.splitlines():
                if not line.strip():
                    kept.append(line)
                    continue
                indent = len(line) - len(line.lstrip())
                # The block ends at the first line shallower than its own first
                # line. Testing "is it indented at all" instead kept the prose
                # that follows, which dragged the common indent down and left
                # the code indented after dedent - so nothing parsed and every
                # check below passed on an empty list.
                if depth is None:
                    depth = indent
                elif indent < depth:
                    break
                kept.append(line)
            if any("qc." in line or "Block(" in line for line in kept):
                # textwrap, not a fixed strip: a docstring block is indented by
                # its method's level, so a hand-rolled `[4:]` left it indented,
                # `ast.parse` raised, and every check below saw nothing. That
                # made the whole suite pass vacuously.
                blocks.append(textwrap.dedent("\n".join(kept)))
    return blocks


def _calls(snippet):
    """Parsed calls in a snippet, or nothing if it is not standalone Python."""
    try:
        tree = ast.parse(snippet)
    except SyntaxError:
        return []
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)]


def _name_of(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


@pytest.mark.parametrize("path", SNIPPET_SOURCES,
                         ids=lambda p: str(p.relative_to(ROOT)))
def test_no_snippet_passes_a_keyword_the_signature_does_not_have(path):
    """`Block(0, 2, model=5011)` was in `set_param`'s own docstring.

    `Block`'s field is `model_id` and it is positional in every real call, so
    this raised `TypeError` for anyone who copied the library's headline example.
    """
    for snippet in _python_snippets(path):
        for call in _calls(snippet):
            if _name_of(call.func) == "Block":
                fields = {"row", "column", "model_id"}
                bad = [k.arg for k in call.keywords if k.arg not in fields]
                assert not bad, (
                    f"{path.name}: Block(..., {bad[0]}=...) - Block takes "
                    f"row, column and model_id, so this raises TypeError"
                )


@pytest.mark.parametrize("path", SNIPPET_SOURCES,
                         ids=lambda p: str(p.relative_to(ROOT)))
def test_no_snippet_names_a_parameter_on_a_block_with_no_model(path):
    """Naming a parameter needs to know which model is in the cell.

    `set_param(Block(0, 5), "MIX", ...)` raises before it looks at the value,
    because a grid cell holds whatever the player put there. Three snippets
    shipped this way, README included.
    """
    for snippet in _python_snippets(path):
        for call in _calls(snippet):
            if _name_of(call.func) != "set_param" or len(call.args) < 2:
                continue
            target, param = call.args[0], call.args[1]
            names_it = isinstance(param, ast.Constant) and isinstance(param.value, str)
            if not names_it or _name_of(target.func if isinstance(target, ast.Call)
                                       else target) != "Block":
                continue
            # Positional OR keyword: `Block(0, 5, 12053)` and
            # `Block(row=0, column=5, model_id=12053)` both say which model.
            says_model = (len(target.args) >= 3
                          or any(k.arg == "model_id" for k in target.keywords))
            assert says_model, (
                f"{path.name}: set_param({ast.unparse(target)}, "
                f"{ast.unparse(param)}, ...) - naming a parameter needs the "
                f"model id as Block's third argument"
            )


#: Every method that refuses a bare number, and WHERE its values sit: the
#: positional slots to check, and the keywords.
#:
#: ADR-0017 widened the rule from `set_param` to ten more methods and this list
#: did not move with it, so the guard went stale in the same commit - and two
#: `README.md` snippets that now raise got through, twenty-five lines below the
#: page's own explanation of typed values. A method added to ADR-0017's scope
#: belongs here in the same change.
REFUSE_A_BARE_NUMBER = {
    "set_param": (2, ()),
    "set_input_level": (1, ()),
    "set_output_level": (1, ()),
    "set_master_volume": (0, ()),
    "set_global_eq_band": (1, ()),
    "set_hold_timing": (0, ()),
    "set_tuner_reference": (0, ()),
    "set_input_port": (None, ("level",)),
    "set_output_port": (None, ("level",)),
    "set_usb_port": (None, ("level",)),
    "set_global_eq": (None, ("gain", "frequency", "q")),
    "set_global_eq_output": (None, ("level",)),
    "set_expression": (None, ("minimum", "maximum")),
    "set_expression_bypass": (None, ("delay_ms",)),
}


def _bare(node):
    return (isinstance(node, ast.Constant)
            and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool))


@pytest.mark.parametrize("path", SNIPPET_SOURCES,
                         ids=lambda p: str(p.relative_to(ROOT)))
def test_no_snippet_passes_a_bare_number_where_one_is_refused(path):
    """ADR-0016 and ADR-0017 made these a TypeError; the docs still taught them."""
    for snippet in _python_snippets(path):
        for call in _calls(snippet):
            where = REFUSE_A_BARE_NUMBER.get(_name_of(call.func))
            if where is None:
                continue
            slot, keywords = where
            found = []
            if slot is not None and len(call.args) > slot and _bare(call.args[slot]):
                found.append(repr(call.args[slot].value))
            found += [f"{k.arg}={k.value.value!r}" for k in call.keywords
                      if k.arg in keywords and _bare(k.value)]
            assert not found, (
                f"{path.name}: {_name_of(call.func)}(..., {found[0]}) is "
                f"refused - every value has two number lines, so it must say "
                f"which one it is on"
            )


@pytest.mark.parametrize("path", sorted((ROOT / "pyquadcortex").rglob("*.py")),
                         ids=lambda p: str(p.relative_to(ROOT)))
def test_a_docstring_reaching_for_encoded_says_why(path):
    """ADR-0016's "never advertised" rule, held where it is checkable.

    `tests/test_examples.py` holds it for `examples/`. This holds it for the
    other place a reader copies from: a code block inside a docstring, which
    `help()` prints. Both ask the same thing - a use of the device's own scale
    where a unit type would serve has to say why it will not.

    What NEITHER covers, and the rule text says so rather than implying
    otherwise: prose in `docs/`, and the error messages that OFFER `Encoded` as
    the way out. Offering the escape hatch to someone who has just been refused
    is the message's whole job.
    """
    if path.name == "values.py":
        # Where the two scales are DEFINED and set against each other. A rule
        # that forbade naming `Encoded` here would forbid documenting it.
        pytest.skip("the module that defines the type")
    for snippet in _python_snippets(path):
        lines = snippet.split("\n")
        for i, line in enumerate(lines):
            if "Encoded(" not in line or line.lstrip().startswith("#"):
                continue
            prose = " ".join(l.split("#", 1)[1].strip()
                             for l in lines[max(0, i - 4):i + 1]
                             if l.lstrip().startswith("#")).lower()
            # Four words, not eight: "an index the catalog omits" is a complete
            # reason and the reason WORD below is what does the real work - a
            # bare "# ok" or "# the same" fails on that, whatever its length.
            assert len(prose.split()) >= 4 and any(
                w in prose for w in ("detent", "off", "catalog", "index",
                                     "scale", "wire")), (
                f"{path.name}: a docstring writes the device's own scale with "
                f"no comment saying why a unit type will not do"
            )
