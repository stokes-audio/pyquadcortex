"""The manual-coverage summary must match the table it summarises.

That summary line was hand-maintained and drifted badly: it claimed 65 yes and 13
no when the table held 54 and 22, overstating coverage by 11 rows. A doc that
audits what works is worth nothing if its own headline is stale, so the count is
checked here instead of trusted.
"""
import pathlib
import re
from collections import Counter

import pytest

DOC = pathlib.Path(__file__).resolve().parent.parent / "docs" / "manual-coverage.md"
STATUSES = ("yes", "partly", "no", "n/a")


def _tally(text):
    counts = Counter()
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        fields = [f.strip() for f in line.strip().strip("|").split("|")]
        if len(fields) < 3 or fields[1] == "Status" or set(fields[1]) <= set("- "):
            continue
        counts[fields[1]] += 1
    return counts


@pytest.mark.skipif(not DOC.exists(), reason="docs/manual-coverage.md not present")
def test_summary_matches_table():
    text = DOC.read_text()
    counts = _tally(text)
    unknown = set(counts) - set(STATUSES)
    assert not unknown, f"unexpected status value(s) in the table: {sorted(unknown)}"

    match = re.search(
        r"Of (\d+) features audited: \*\*(\d+) yes\*\*, \*\*(\d+) partly\*\*, "
        r"\*\*(\d+) no\*\*, \*\*(\d+) n/a\*\*\.", text)
    assert match, "the summary line is missing or has changed shape"
    total, *claimed = (int(g) for g in match.groups())

    actual = [counts[s] for s in STATUSES]
    assert claimed == actual, (
        f"summary says {dict(zip(STATUSES, claimed))} but the table has "
        f"{dict(zip(STATUSES, actual))}")
    assert total == sum(actual), f"summary total {total} != {sum(actual)} rows"
