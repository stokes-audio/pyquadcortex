"""The examples are code, so the suite should notice when they stop being code.

Nothing compiled them until 2026-08-27, and a migration of `set_param`'s
signature left two of them with a syntax error that the full suite reported as
green. They are the first thing a new user runs.

These do NOT execute an example - every one of them opens a device. They parse
it, which is what catches the class of break that actually happened.
"""

import ast
import pathlib
import re

import pytest

EXAMPLES = sorted((pathlib.Path(__file__).parent.parent / "examples").glob("*.py"))


def test_there_are_examples_to_check():
    """A glob that silently matches nothing would make every test below pass."""
    assert len(EXAMPLES) >= 8


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_the_example_is_valid_python(path):
    ast.parse(path.read_text(), filename=str(path))


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_the_example_imports_what_it_uses(path):
    """The specific break: a value type used and never imported."""
    source = path.read_text()
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.update(a.asname or a.name for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update((a.asname or a.name).split(".")[0] for a in node.names)
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    # Derived from the module, not a hand-listed subset - the first version
    # named six types, so an example using Milliseconds unimported passed.
    from pyquadcortex.protocol import values

    for name in values.__all__:
        if name in used:
            assert name in imported, f"{path.name} uses {name} without importing it"


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_the_example_uses_what_it_imports(path):
    """The other direction, which the migration itself got wrong.

    Rewriting these left `UNITY_LEVEL` imported and unused in two of them, and
    the test written to catch example breakage did not catch the breakage it
    was written alongside.
    """
    tree = ast.parse(path.read_text())
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    used |= {n.value.id for n in ast.walk(tree)
             if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            name = alias.asname or alias.name
            assert name in used, f"{path.name} imports {name} and never uses it"


# -- Encoded is available, and never advertised -------------------------------


def _mentions_encoded(text):
    return [line for line in text.split("\n") if re.search(r"\bEncoded\(", line)]


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_an_example_reaching_for_encoded_says_why(path):
    """`Encoded` is an escape hatch, not a route - ADR-0016.

    It stays accepted everywhere, because the wire carries more parameters than
    the catalog describes. But an example that writes the device's own 0..1
    where a unit type would serve teaches the wrong habit, so each use has to
    carry its reason in a comment nearby.

    The two legitimate uses today are the Off detent, which sits BELOW the
    bottom of the dB scale and so has no dB value at all, and an index the
    catalog does not describe.
    """
    lines = path.read_text().split("\n")
    for i, line in enumerate(lines):
        if not re.search(r"\bEncoded\(", line) or line.strip().startswith("#"):
            continue
        # A comment must be nearby AND say something. The first version of this
        # asserted `"#" in window`, which any stray hash satisfied - including
        # one inside a string. It tested that a hash existed, not that a reason
        # was written down.
        comments = [l.split("#", 1)[1].strip()
                    for l in lines[max(0, i - 6):i + 1]
                    if l.lstrip().startswith("#")]
        prose = " ".join(comments).lower()
        assert len(prose.split()) >= 8, (
            f"{path.name}:{i + 1} writes the device's own scale with no comment "
            f"saying why a unit type will not do")
        assert any(word in prose for word in
                   ("detent", "off", "catalog", "index", "scale", "wire")), (
            f"{path.name}:{i + 1} has a comment nearby, but it does not explain "
            f"the choice of the device's scale")
