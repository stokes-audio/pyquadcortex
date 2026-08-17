"""Waiting on the model's event thread, for the tests that need to.

The model delivers events on a thread of its own, so a test that asserted
straight after publishing would race it and pass or fail by timing.

This is a plain module rather than fixtures in ``conftest.py`` on purpose. There
are two conftest files in this suite - the rootdir's and ``tests/hardware/``'s -
and ``from conftest import ...`` resolves to whichever pytest inserted last,
which is the hardware one on a full-tree run. That failed loudly here, and it
would have failed quietly if the two files had ever held a same-named helper.
"""

import time

#: How long a test waits for an event before giving up. Generous, because it is
#: only ever reached on a failure: a working stream delivers in microseconds.
PATIENCE = 2.0

#: How long a test waits to be sure NOTHING is coming. A ceiling on plausible
#: delivery rather than a guess at it - see :func:`stays_quiet`.
QUIET = 0.1


def wait_for(box, count, seconds=PATIENCE):
    """Block until ``box`` holds ``count`` items, or fail saying how many came.

    Raises ``AssertionError`` rather than returning a flag: every caller wants
    the test to stop here, and one that forgot to check the flag would go on to
    assert against a list that was still filling.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if len(box) >= count:
            return
        time.sleep(0.005)
    raise AssertionError(
        f"waited {seconds}s for {count} event(s) and {len(box)} arrived")


def stays_quiet(box, seconds=QUIET):
    """Give the delivery thread time to publish, then report what it published.

    The counterpart to :func:`wait_for`, for tests asserting that nothing is
    published. Those cannot wait on a condition - there is no condition - so
    they have to wait out a plausible delivery instead. A test that skipped the
    wait would pass against a stream that publishes everything, simply by asking
    before the thread got there.
    """
    time.sleep(seconds)
    return list(box)
