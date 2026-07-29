"""The readme is the PyPI long description, rendered with no base URL.

A relative link there 404s on the project page - which is exactly what happened on
the first real release - so every link must be absolute. Anchors are fine; they
stay on the page.
"""
import pathlib
import re

README = pathlib.Path(__file__).resolve().parent.parent / "readme.md"

# Matches ](target) for links AND nested-image badge links, which a plain
# link regex misses - the one relative link that survived the first fix was
# inside [![badge](img)](LICENSE).
TARGETS = re.compile(r"\]\(([^)]+)\)")


def test_readme_links_are_absolute():
    relative = [
        target for target in TARGETS.findall(README.read_text())
        if not target.startswith(("http://", "https://", "#", "mailto:"))
    ]
    assert not relative, (
        f"relative link(s) in readme.md would 404 on the PyPI project page: "
        f"{relative}"
    )
