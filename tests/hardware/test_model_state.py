"""The model's cache against a real unit: does it hear, and does it stay quiet?

The offline suite proves the rules: what merges, what forces a re-read, what a
write watcher makes of an echo. It cannot prove the two facts the whole design
rests on, because both are claims about a real unit:

* **the connect burst warms the cache**, so a value read straight after
  connecting costs no round trip;
* **a change the model did not make reaches it**, unasked, with no read.

Both are checked here. So is the third claim the design leans on hardest - that
the metronome's tempo stream, which never stops, costs nothing at all.

**State neutrality (ADR-0005).** Two tests edit the unit: each sets one parameter
on the first occupied block of the loaded preset and restores the value it read
first. That is the same edit ``test_write_echo.py`` makes, for the same reason -
it is the smallest change the unit announces. It leaves the loaded preset marked
as having unsaved changes, which is what an edit does and what discarding it
would have to un-do by throwing the owner's work away. Nothing here saves,
recalls, deletes or renames anything.

**One flag transition per run.** ``PresetDirty`` announces a CHANGE of the flag,
not an edit (``docs/protocol.md``), so the first edit of a run gets an
announcement and the rest do not. The two editing tests are written around that
rather than against it: the first takes the transition and skips if the preset
arrived already dirty, and the second expects whichever outcome its own
before-reading predicts.

One consequence worth knowing before reading a green run: in a whole-file run the
write-through test is the second edit, so it exercises the TIMED-OUT branch and
its self-correction. Its CONFIRMED branch needs to be the first edit of a run::

    pytest tests/hardware --hardware -k write_through

Both were run while this file was written (2026-08-14, d14e): CONFIRMED settled in
612 ms with the unit sending a ``PresetDirty``, and the timed-out branch saw the
unit send a ``Grid`` echo and nothing else.
"""
import collections
import threading
import time

import pytest

from pyquadcortex.device import entries
from pyquadcortex.device.watch import WatchOutcome
from pyquadcortex.protocol.targets import Block

#: The metronome clock always runs, so the unit pushes GlobalTempo in pairs, one
#: pair per beat - 1.5 s apart at the slowest tempo the unit offers (40 bpm).
#: Ten seconds is several beats even there.
PATIENCE = 10.0

#: Long enough to see the tempo stream several times over at any tempo.
QUIET_WATCH = 6.0


class CountingClient:
    """The real client, with every read the cache issues counted.

    Wraps rather than replaces, so the reads are real round trips to the unit -
    this only records that they happened. "No round trip" is otherwise a claim
    nothing on hardware can check.
    """

    def __init__(self, real):
        self._real = real
        self.reads = collections.Counter()

    def version(self, *args, **kwargs):
        self.reads["version"] += 1
        return self._real.version(*args, **kwargs)

    def preset_dirty(self, *args, **kwargs):
        self.reads["preset_dirty"] += 1
        return self._real.preset_dirty(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


class Pushes:
    """Records messages from the RX thread, for the test to read.

    ``type_name=None`` records everything, which is what a failing echo test
    needs: "nothing came back" is a claim about the unit, and this file's
    neighbour learned the hard way that such a claim can be wrong while the unit
    is talking perfectly well (see ``test_write_echo.py``'s ``_landed``).
    """

    def __init__(self, type_name=None):
        self._type_name = type_name
        self._lock = threading.Lock()
        self._seen = []

    def __call__(self, message):
        if self._type_name in (None, type(message).__name__):
            with self._lock:
                self._seen.append(message)

    def seen(self):
        with self._lock:
            return list(self._seen)

    def tally(self):
        return dict(collections.Counter(type(m).__name__ for m in self.seen()))


@pytest.fixture
def counted(qc, model_cache):
    """Count the cache's reads for one test, then give it the client back."""
    counting = CountingClient(qc)
    model_cache.bind(counting)
    try:
        yield counting
    finally:
        model_cache.bind(qc)


def _wait_until(predicate, timeout=PATIENCE):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _first_occupied_block(qc):
    """Row 1's first block, as the wire indexes ``set_param`` wants.

    Wire coordinates on purpose: this drives the PROTOCOL client directly, which
    keeps its zero-based indexes, so there is nothing here to convert.
    """
    preset = qc.read_current_preset()
    for column, model in enumerate(preset.chains[0].models):
        if model.hash:
            was = next(p.param_values[0].float_value
                       for p in model.params if p.index == 0)
            return column, was
    pytest.skip("row 1 of the loaded preset is empty, so there is nothing to edit")


# -- the burst -----------------------------------------------------------------


def test_the_connect_burst_warms_the_cache(burst_warmed, handshake_burst,
                                           record_property):
    """The cheapest state the model ever gets: the unit volunteers it.

    Read from a snapshot the connection fixture took the moment the burst
    finished, so this says the BURST filled it rather than some earlier test.
    """
    record_property("burst_warmed", {name: sorted(fields)
                                     for name, fields in burst_warmed.items()})
    counted = collections.Counter(handshake_burst.names())

    assert counted.get("PresetDirtyMessage"), (
        f"the burst carried no PresetDirty, so there was nothing to warm the "
        f"cache with - it recorded {dict(counted)}")
    assert "is_dirty" in burst_warmed["dirty"], (
        f"the unit announced its unsaved-changes state during the burst and the "
        f"model did not keep it - the cache held {burst_warmed}")


def test_nothing_the_burst_delivered_is_read_again_on_first_access(
        model_cache, counted):
    """The non-functional requirement, stated as a measurement."""
    assert model_cache.value("dirty", "is_dirty") in (True, False)
    assert counted.reads["preset_dirty"] == 0, (
        "the unit had already said so during the handshake and the model asked "
        "again, which is the round trip the cache exists to avoid")


def test_the_burst_does_not_warm_what_the_unit_never_announces(burst_warmed):
    """The other half, and the reason the read path exists.

    The unit does send a ``Version`` during the handshake, but it is the answer
    to our version announce - it sets ``cortex_control_version_valid`` and none
    of the unit's own fields. So identity is exactly the case section 9's third
    column is for: where the unit does not tell us, we ask.
    """
    assert burst_warmed["identity"] == {}, (
        f"the burst carried the unit's own identity after all, which is worth "
        f"knowing - it held {burst_warmed['identity']}. If that is now true, "
        f"the entry's docstring in device/entries.py is wrong.")


def test_state_the_unit_never_announces_costs_one_read_and_then_none(
        model_cache, counted):
    """No model property ships with a staleness caveat, so it asks - once.

    Once is harder than it looks, and this test is what caught it. The protocol
    is symmetric, so a ``Version`` READ is answered by the unit's reply and then,
    0.5-0.8 ms later, by a ``Version`` READ of the unit's own asking for Cortex
    Control's version. The cache used to count that question as a push that had
    landed mid-read and kept the entry marked, so the second field below went
    back to the unit - measured at 7 reads in 40 before the fix and 0 in 60
    after, which reached this suite as a failure about one run in three.
    """
    model_cache.mark_for_reread("identity", "this test wants a cold read")

    firmware = model_cache.value("identity", "app_fw_version")
    serial = model_cache.value("identity", "device_serial_number")

    assert firmware and serial
    assert counted.reads["version"] == 1, (
        f"reading two fields of one entry took {counted.reads['version']} reads")


# -- a change the model did not make -------------------------------------------


def test_an_edit_the_model_did_not_make_reaches_its_cache(qc, model_cache,
                                                          counted, restores,
                                                          record_property):
    """The story's whole point, on the unit.

    The edit goes through the PROTOCOL client, so as far as the model is
    concerned somebody else changed the unit - which is what a hand on the
    touchscreen is. Nothing here asks the unit anything; the model finds out
    because the unit says so.

    Needs a preset with no unsaved changes, because `PresetDirty` announces a
    CHANGE of the flag rather than an edit (``protocol.md``). One transition is
    available per run, and this test is the one that gets it - which is why it
    comes before the write-through test in this file.
    """
    if qc.preset_dirty():
        pytest.skip(
            "the loaded preset already has unsaved changes, and the unit only "
            "announces the flag when it CHANGES - so an edit now would tell the "
            "model nothing and this test could not say anything. Save or reload "
            "the preset on the unit and run this again.")
    column, was = _first_occupied_block(qc)
    restores("row 1 first block, parameter 0", lambda: qc.set_param(Block(0, column), 0, was))

    announcements = Pushes("PresetDirtyMessage")
    everything = Pushes()
    qc.add_listener(announcements)
    qc.add_listener(everything)
    try:
        qc.set_param(Block(0, column), 0, 0.75 if abs(was - 0.75) > 0.05 else 0.25)
        assert _wait_until(lambda: announcements.seen()), (
            f"the unit said nothing about unsaved changes for an edit it "
            f"accepted. It sent {everything.tally()} during the window, so read "
            f"that before blaming the unit for saying nothing.")
    finally:
        qc.remove_listener(announcements)
        qc.remove_listener(everything)

    announced = bool(announcements.seen()[-1].is_dirty)
    record_property("unit_announced_is_dirty", announced)
    assert announced is True, "the unit announced an edit as leaving no unsaved changes"
    assert model_cache.cached("dirty")["is_dirty"] is True, (
        f"the unit said so and the model holds {model_cache.cached('dirty')}")
    assert model_cache.value("dirty", "is_dirty") is True
    assert counted.reads["preset_dirty"] == 0, "the model asked instead of listening"


def test_a_write_through_the_cache_is_settled_by_what_the_unit_says(
        qc, model_cache, restores, record_property):
    """Section 10's outcomes, against the unit that produces them.

    Which one is expected is decided by a state read BEFORE the write, not by
    what came back - both branches assert something specific, and neither can
    stand in for the other:

    * on a clean preset the edit changes the flag, the unit announces it, and the
      write is CONFIRMED;
    * on an already-dirty preset the unit announces nothing (``protocol.md``,
      "``PresetDirty`` announces a CHANGE of flag"), so the write TIMES OUT and
      the cache marks that entry - and the re-read that follows gets the truth
      from the unit. That is the self-correction section 10 is for, on the one
      occasion this suite can produce it honestly.

    ``different`` stays an offline test: it is by definition a bug in our code.
    """
    column, was = _first_occupied_block(qc)
    restores("row 1 first block, parameter 0", lambda: qc.set_param(Block(0, column), 0, was))
    target = 0.75 if abs(was - 0.75) > 0.05 else 0.25
    already_dirty = qc.preset_dirty()
    record_property("preset_was_already_dirty", already_dirty)

    everything = Pushes()
    qc.add_listener(everything)
    try:
        started = time.monotonic()
        watch = model_cache.write_through(
            "dirty", {"is_dirty": True},
            send=lambda: qc.set_param(Block(0, column), 0, target))

        assert model_cache.cached("dirty")["is_dirty"] is True, (
            "the cache was not updated until the echo arrived, which is the "
            "round trip section 9's third rule exists to avoid")
        assert watch.settled(timeout=PATIENCE), "the watcher never settled"
    finally:
        qc.remove_listener(everything)
    record_property("watch_settled_in_ms", (time.monotonic() - started) * 1000.0)
    record_property("unit_sent", everything.tally())

    assert everything.tally().get("GridMessage"), (
        f"the unit did not echo the parameter write at all, so this test is "
        f"measuring a write that never landed - it sent {everything.tally()}")

    if already_dirty:
        assert watch.outcome is WatchOutcome.TIMED_OUT, (
            f"the unit had nothing to announce and the watcher claimed "
            f"{watch.outcome} anyway ({watch.disagreement})")
        assert model_cache.needs_read("dirty") is True
        assert model_cache.value("dirty", "is_dirty") is True, (
            "the re-read did not recover the unit's own answer")
    else:
        assert watch.outcome is WatchOutcome.CONFIRMED, (
            f"the unit answered {watch.outcome} ({watch.disagreement}). It sent "
            f"{everything.tally()} during the window, so read that before "
            f"blaming the unit for saying nothing.")
        assert model_cache.needs_read("dirty") is False


# -- the stream that never stops ----------------------------------------------


def test_the_metronome_stream_costs_the_cache_nothing(qc, model_cache, counted):
    """The design's noisiest neighbour, measured rather than assumed.

    The metronome clock runs on every connection whether anybody asked for it or
    not, so a cache that treated inbound messages as "something changed, go
    re-read" would re-read for the length of the session.
    """
    for entry in entries.ENTRIES:
        model_cache.value(entry.name, sorted(entry.fields())[0])
    counted.reads.clear()

    stream = Pushes("GlobalTempoMessage")
    qc.add_listener(stream)
    try:
        time.sleep(QUIET_WATCH)
    finally:
        qc.remove_listener(stream)

    assert len(stream.seen()) >= 2, (
        f"the unit pushed {len(stream.seen())} GlobalTempo message(s) in "
        f"{QUIET_WATCH}s, so this test saw no stream to be quiet about")
    marked = [entry.name for entry in entries.ENTRIES
              if model_cache.needs_read(entry.name)]
    assert not marked, f"the tempo stream marked {marked} for re-reading"
    assert not counted.reads, f"the cache read {dict(counted.reads)} while idle"
