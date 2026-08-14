"""The write watcher's one sentence: `docs/domain-model.md` section 10.

    Every field we sent must come back with the value we sent.

``tests/test_state.py`` drives the watcher through the cache, which is how it is
really used - but the cache's entries hold one or two fields, and the rule that
matters is about the fields we did NOT send. So the interesting cases are here,
against :class:`WriteWatch` itself.

Why they matter: "the echo equals what we sent" would pass every test the cache
can currently reach, and would cry wolf constantly on hardware, because the unit
legitimately changes things nobody asked about. Section 10 lists four - a
gain-reduction meter, a mirrored parameter, NaN in unused slots, dropdown values
recomputed on rows we never touched - and every one of them is a field we did
not send.
"""
import threading
import time

import pytest

from pyquadcortex.device.watch import WatchOutcome, Watchdog, WriteWatch


def a_watch(sent, patience=30.0):
    return WriteWatch("test entry", sent, time.monotonic() + patience)


# -- what the unit also changed ----------------------------------------------


def test_an_echo_that_also_carries_something_we_did_not_send_confirms():
    """The mirrored parameter, the recomputed dropdown, the live meter."""
    watch = a_watch({"level": 0.5})

    assert watch.absorb({"level": 0.5, "gain_reduction": -3.2}) \
        is WatchOutcome.CONFIRMED


def test_a_field_we_did_not_send_cannot_make_a_write_look_wrong():
    watch = a_watch({"level": 0.5})

    watch.absorb({"level": 0.5, "mirrored": 999.0})

    assert watch.disagreement is None


# -- every field we sent ------------------------------------------------------


def test_an_echo_carrying_only_some_of_what_we_sent_is_not_a_confirmation():
    """Not yet confirmed, and not yet wrong. The echo is a sparse delta, so a
    second one can still carry the rest."""
    watch = a_watch({"level": 0.5, "mix": 0.25})

    assert watch.absorb({"level": 0.5}) is None
    assert watch.outcome is None


def test_the_confirmation_completes_across_two_echoes():
    watch = a_watch({"level": 0.5, "mix": 0.25})
    watch.absorb({"level": 0.5})

    assert watch.absorb({"mix": 0.25}) is WatchOutcome.CONFIRMED


def test_one_wrong_field_is_a_disagreement_even_when_the_others_match():
    watch = a_watch({"level": 0.5, "mix": 0.25})

    assert watch.absorb({"level": 0.5, "mix": 0.9}) is WatchOutcome.DIFFERENT
    assert watch.disagreement == ("mix", 0.25, 0.9)


def test_a_disagreement_names_the_field_the_value_sent_and_the_value_returned():
    """Section 10: a bug in our code, now with a name and a location. A watcher
    that only said "it did not stick" would leave the reader no better off."""
    watch = a_watch({"level": 0.5})
    watch.absorb({"level": 0.0})

    field, sent, returned = watch.disagreement
    assert (field, sent, returned) == ("level", 0.5, 0.0)


def test_an_echo_that_mentions_nothing_we_sent_settles_nothing():
    watch = a_watch({"level": 0.5})

    assert watch.absorb({"something_else": 1}) is None
    assert watch.outcome is None


# -- a settled write stays settled -------------------------------------------


def test_a_later_echo_cannot_overturn_a_confirmation():
    """The unit keeps talking after a write lands. A watcher that kept reading
    would report a disagreement about a value somebody changed afterwards."""
    watch = a_watch({"level": 0.5})
    watch.absorb({"level": 0.5})

    assert watch.absorb({"level": 0.9}) is None
    assert watch.outcome is WatchOutcome.CONFIRMED


def test_a_write_the_unit_confirmed_cannot_then_time_out():
    watch = a_watch({"level": 0.5})
    watch.absorb({"level": 0.5})

    assert watch.time_out() is False
    assert watch.outcome is WatchOutcome.CONFIRMED


def test_a_write_that_timed_out_cannot_then_be_confirmed():
    """The race the watchdog runs into on every write confirmed near its
    deadline; whichever gets there first is the answer."""
    watch = a_watch({"level": 0.5})
    assert watch.time_out() is True

    assert watch.absorb({"level": 0.5}) is None
    assert watch.outcome is WatchOutcome.TIMED_OUT


def test_a_write_with_no_fields_is_refused():
    """It would confirm on the first echo of anything at all, having checked
    nothing."""
    with pytest.raises(ValueError):
        a_watch({})


def test_a_watch_is_not_settled_until_it_is_published():
    """`settled()` returning has to mean the outcome's consequences have already
    happened - the entry marked, the line logged - or a caller waiting on it
    races the thread that decided."""
    watch = a_watch({"level": 0.5})
    watch.absorb({"level": 0.5})
    assert watch.settled(timeout=0) is False

    watch.publish()

    assert watch.settled(timeout=0) is True


# -- the watchdog -------------------------------------------------------------


def test_the_watchdog_gives_up_on_a_write_at_its_deadline():
    given_up_on = []
    dog = Watchdog(given_up_on.append, "pyquadcortex-test-watchdog")
    watch = a_watch({"level": 0.5}, patience=0.05)
    dog.add(watch)
    try:
        assert watch.settled(timeout=5.0)
    finally:
        dog.stop()
    assert watch.outcome is WatchOutcome.TIMED_OUT
    assert given_up_on == [watch]


def test_the_watchdog_leaves_a_confirmed_write_alone():
    given_up_on = []
    dog = Watchdog(given_up_on.append, "pyquadcortex-test-watchdog")
    watch = a_watch({"level": 0.5}, patience=0.05)
    dog.add(watch)
    watch.absorb({"level": 0.5})
    try:
        time.sleep(0.3)
    finally:
        dog.stop()
    assert watch.outcome is WatchOutcome.CONFIRMED
    assert given_up_on == []


def test_a_write_added_while_the_watchdog_is_idle_still_times_out():
    """The lost-notify bug this is written to catch: with nothing outstanding
    the thread waits with no timeout at all, so a write added at that moment
    would wait for a wake-up that had already happened and never time out."""
    dog = Watchdog(lambda watch: None, "pyquadcortex-test-watchdog")
    warm_up = a_watch({"level": 0.5}, patience=0.02)
    dog.add(warm_up)
    assert warm_up.settled(timeout=5.0)
    time.sleep(0.05)                       # the thread is now waiting on nothing

    late = a_watch({"level": 0.5}, patience=0.05)
    dog.add(late)
    try:
        assert late.settled(timeout=5.0), "the watchdog slept through it"
    finally:
        dog.stop()


def test_stopping_the_watchdog_ends_its_thread():
    dog = Watchdog(lambda watch: None, "pyquadcortex-test-watchdog")
    dog.add(a_watch({"level": 0.5}, patience=30.0))
    assert _named_threads("pyquadcortex-test-watchdog")

    dog.stop()

    assert not _named_threads("pyquadcortex-test-watchdog")


def test_a_watchdog_stopped_before_a_deadline_does_not_report_a_timeout():
    """The connection is going away, so "the unit never answered" would be a
    claim about the unit rather than a fact."""
    given_up_on = []
    dog = Watchdog(given_up_on.append, "pyquadcortex-test-watchdog")
    watch = a_watch({"level": 0.5}, patience=0.05)
    dog.add(watch)
    dog.stop()
    time.sleep(0.2)
    assert given_up_on == []


def _named_threads(name):
    return [t for t in threading.enumerate() if t.name == name]
