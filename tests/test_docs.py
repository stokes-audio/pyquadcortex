"""Structural checks on the published docs.

Both of these defects shipped in 0.34.0 and were found by an outside user:
an unclosed code fence swallowed an entire section of api.md, and the method
table presented eleven module-level functions as methods.
"""
import pathlib
import re

import pytest

import pyquadcortex

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


def test_api_method_table_entries_exist_where_the_table_says():
    """`name(` in the table must be a real method; `pyquadcortex.name(` a real
    module function. help() is the contract; the table must match it."""
    text = (ROOT / "docs" / "api.md").read_text()
    section = text[text.index("## Method groups"):text.index("**Rows and columns")]
    rows = "\n".join(line for line in section.splitlines()
                     if line.startswith("|"))     # table rows only, not prose
    problems = []
    for m in re.finditer(r"`(pyquadcortex\.)?([a-z_][a-z0-9_]*)\(", rows):
        prefixed, name = bool(m.group(1)), m.group(2)
        if prefixed:
            if not callable(getattr(pyquadcortex, name, None)):
                problems.append(f"pyquadcortex.{name} listed but absent")
        else:
            if not hasattr(pyquadcortex.QuadCortex, name):
                hint = (" (it IS a module function - prefix it)"
                        if hasattr(pyquadcortex, name) else "")
                problems.append(f"{name} listed as a method but is not{hint}")
    assert not problems, "; ".join(problems)
