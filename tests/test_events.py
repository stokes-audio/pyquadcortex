"""The event stream a caller subscribes to.

Its whole reason for existing is that a subscriber may READ FROM THE UNIT when it
hears something. The unit's messages arrive on the transport's receiving thread,
and that thread is forbidden from reading (ADR-0009), so an event handed over
there could not do the one thing it is for. Hence a thread of the model's own,
and hence the test that proves the subscriber runs on it.
"""
import threading

import pytest

from waiting import stays_quiet, wait_for
from pyquadcortex.device import events


@pytest.fixture
def stream():
    made = events.EventStream()
    yield made
    made.close()


def test_a_subscriber_hears_what_is_published(stream):
    seen = []
    stream.subscribe(seen.append)
    stream.publish(events.Invalidated("preset", "somebody moved a block"))
    wait_for(seen, 1)
    assert seen[0].part == "preset"


def test_events_arrive_in_the_order_they_were_published(stream):
    seen = []
    stream.subscribe(seen.append)
    for n in range(20):
        stream.publish(events.Changed("dirty", (str(n),)))
    wait_for(seen, 20)
    assert [e.fields[0] for e in seen] == [str(n) for n in range(20)]


def test_a_subscriber_runs_off_the_publishing_thread(stream):
    """The point of the whole class. If this fails, a subscriber that reacts the
    obvious way - go and re-read it - raises instead of working."""
    seen = []
    stream.subscribe(lambda e: seen.append(threading.current_thread().name))
    publisher = threading.current_thread().name
    stream.publish(events.Changed("dirty", ("is_dirty",)))
    wait_for(seen, 1)
    assert seen[0] != publisher
    assert seen[0] == events.EVENT_THREAD_NAME


def test_every_subscriber_gets_every_event(stream):
    first, second = [], []
    stream.subscribe(first.append)
    stream.subscribe(second.append)
    stream.publish(events.Changed("dirty", ("is_dirty",)))
    wait_for(first, 1)
    wait_for(second, 1)


def test_one_subscriber_raising_does_not_rob_the_others(stream):
    """One caller's bug must not cost another caller their event, and it must
    not stop the stream: the event AFTER the failure has to arrive too."""
    def explode(event):
        raise ValueError("this subscriber is broken")

    seen = []
    stream.subscribe(explode)
    stream.subscribe(seen.append)
    stream.publish(events.Changed("dirty", ("first",)))
    stream.publish(events.Changed("dirty", ("second",)))
    wait_for(seen, 2)
    assert [e.fields[0] for e in seen] == ["first", "second"]


def test_unsubscribing_stops_delivery(stream):
    seen = []
    off = stream.subscribe(seen.append)
    off()
    stream.publish(events.Changed("dirty", ("is_dirty",)))
    assert stays_quiet(seen) == []
    assert len(stream) == 0


def test_unsubscribing_one_leaves_the_others(stream):
    gone, stays = [], []
    off = stream.subscribe(gone.append)
    stream.subscribe(stays.append)
    off()
    stream.publish(events.Changed("dirty", ("is_dirty",)))
    wait_for(stays, 1)
    assert gone == []


def test_unsubscribing_twice_is_harmless(stream):
    off = stream.subscribe(lambda e: None)
    off()
    off()
    assert len(stream) == 0


def test_nothing_is_published_when_nobody_is_listening(stream):
    """A model with no subscribers must pay nothing for the event surface. The
    unit pushes its tempo on every beat of every connection, so a stream that
    queued regardless would grow for the life of a script that never asked."""
    stream.publish(events.Changed("dirty", ("is_dirty",)))
    seen = []
    stream.subscribe(seen.append)
    assert stays_quiet(seen) == [], (
        "an event published before anyone subscribed was queued and delivered "
        "late, so the stream is holding events nobody wants")


def test_no_thread_is_started_until_somebody_subscribes():
    made = events.EventStream()
    try:
        made.publish(events.Changed("dirty", ("is_dirty",)))
        running = {t.name for t in threading.enumerate()}
        assert events.EVENT_THREAD_NAME not in running
        made.subscribe(lambda e: None)
        running = {t.name for t in threading.enumerate()}
        assert events.EVENT_THREAD_NAME in running
    finally:
        made.close()


def test_closing_stops_the_thread_and_refuses_new_subscribers():
    made = events.EventStream()
    made.subscribe(lambda e: None)
    made.close()
    assert events.EVENT_THREAD_NAME not in {t.name for t in threading.enumerate()}
    with pytest.raises(RuntimeError, match="closed"):
        made.subscribe(lambda e: None)


def test_closing_drops_the_subscribers(stream):
    stream.subscribe(lambda e: None)
    stream.close()
    assert len(stream) == 0


def test_publishing_after_close_is_harmless(stream):
    seen = []
    stream.subscribe(seen.append)
    stream.close()
    stream.publish(events.Changed("dirty", ("is_dirty",)))
    assert seen == []


def test_closing_twice_is_harmless(stream):
    stream.subscribe(lambda e: None)
    stream.close()
    stream.close()


def test_a_listener_that_is_not_callable_is_refused_at_subscribe(stream):
    """Refused when it is handed over, not on the delivery thread later, where
    the traceback would name a thread the caller has never heard of."""
    with pytest.raises(TypeError):
        stream.subscribe("not a function")


def test_the_event_types_carry_what_happened():
    assert "preset" in repr(events.Invalidated("preset", "a Grid push"))
    assert events.Changed("dirty", ("is_dirty",)) == \
        events.Changed("dirty", ("is_dirty",))
    assert events.Changed("dirty", ("is_dirty",)) != \
        events.Changed("preset", ("is_dirty",))


def test_an_event_says_what_it_is_in_its_repr():
    """These reach a caller's log, so they have to read as something rather
    than as an object address."""
    text = repr(events.Invalidated("preset", "a Grid push changed the grid"))
    assert "Invalidated" in text
    assert "a Grid push changed the grid" in text
