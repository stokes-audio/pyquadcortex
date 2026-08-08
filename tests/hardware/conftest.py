"""The hardware-in-the-loop suite's fixtures, and its restore contract.

ADR-0005: a successful run is **state-neutral** - everything the suite changed is
put back. A failed run restores as best it can and NAMES what it could not, so
the owner knows what to fix by hand. This is not a nicety: the only unit this
project has is one somebody gigs with.

Run it with::

    pytest tests/hardware --hardware

Without the flag nothing here is collected, so the offline suite stays honest
with no unit attached.
"""
import time

import pytest

def pytest_ignore_collect(collection_path, config):
    # Not merely skipped - not collected. A hardware test that silently "passes"
    # as a skip in an offline run is a test nobody notices has stopped running.
    return not config.getoption("--hardware")


@pytest.fixture(scope="session")
def qc():
    """One connection for the whole run; the handshake is expensive."""
    import pyquadcortex
    with pyquadcortex.connect() as client:
        yield client


@pytest.fixture
def restores():
    """Register undo callables; they run in reverse, failure or not.

    Each entry is ``(description, callable)``. Anything that raises while
    restoring is collected and re-raised at the end as one failure naming every
    unrestored item, rather than the first one aborting the rest of the restore.
    """
    undo = []
    yield lambda description, fn: undo.append((description, fn))

    failed = []
    for description, fn in reversed(undo):
        try:
            fn()
            time.sleep(0.3)
        except Exception as exc:                     # noqa: BLE001 - reported, not swallowed
            failed.append(f"{description}: {exc!r}")
    if failed:
        raise AssertionError(
            "COULD NOT RESTORE THE UNIT - fix these by hand:\n  "
            + "\n  ".join(failed))
