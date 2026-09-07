"""Registers the ``--hardware`` flag that gates the online suite.

It lives here rather than in ``tests/hardware/`` because pytest only reads
command-line options from the rootdir's conftest, and the flag has to be
recognised even on a run that never descends into the hardware directory.
``--verifies``, ``--profile`` and the ``verifies`` marker are registered here
for the same reason, although only the hardware suite acts on them (ADR-0020).

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
    parser.addoption(
        "--verifies",
        action="store",
        default=None,
        metavar="OPERATION",
        help="with --hardware: run only the tests marked verifies(OPERATION) "
             "(ADR-0020). pytest's -m cannot match a marker's arguments.",
    )
    parser.addoption(
        "--profile",
        action="store",
        default=None,
        metavar="CLASSNAME",
        help="names a profile class from pyquadcortex.protocol.profiles or "
             "QuadCortex; used with --hardware to measure a unit the registry "
             "would refuse (ADR-0020).",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "verifies(*operations): the QuadCortex operations a hardware test exercises "
        "(ADR-0020). Names are checked against QuadCortex.operations() at collection.")

