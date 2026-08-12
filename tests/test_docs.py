"""Structural checks on the published docs.

Both of these defects shipped in 0.34.0 and were found by an outside user:
an unclosed code fence swallowed an entire section of api.md, and the method
table presented eleven module-level functions as methods.
"""
import pathlib
import re

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
    """
    stale = re.findall(r"`pyquadcortex\.(?!protocol\b|model\b)[a-z_][a-z0-9_.]*\(",
                       _api_table_rows())
    assert not stale, (
        f"docs/api.md still shows {sorted(set(stale))} - the message-level API "
        f"is imported from pyquadcortex.protocol now (ADR-0006)")
