"""Registers the ``--hardware`` flag that gates the online suite.

It lives here rather than in ``tests/hardware/`` because pytest only reads
command-line options from the rootdir's conftest, and the flag has to be
recognised even on a run that never descends into the hardware directory.

See ADR-0005. The offline suite (ADR-0002) must stay runnable, and stay
meaningful, with no unit attached - so the hardware tests are never merely
skipped without the flag. Reached by recursion they are not collected at all;
named on the command line, where pytest ignores that veto, the run stops with an
error naming the flag. Both hooks live in ``tests/hardware/conftest.py``.
"""


def pytest_addoption(parser):
    parser.addoption(
        "--hardware",
        action="store_true",
        default=False,
        help="run the hardware-in-the-loop suite against a connected Quad Cortex "
             "(ADR-0005). Requires Cortex Control to be quit. Never used in CI.",
    )

