"""Display units: dB, Hz, bpm and milliseconds on screen, raw scales on the wire.

Every mapping below was measured on hardware and is written up at the protocol
layer. Three of the five - the two level scales and the tempo - have a protocol
helper that performs the conversion, and this module calls it rather than
restating the arithmetic: two copies of a measured scale drift, and both copies
go on returning a plausible number. The other two have no helper to call. The
tuner has only a documented rule, and hold timing has the protocol layer's
constant tuple, which is the part worth sharing. Both are pinned in
tests/test_translation.py against what the protocol WRITE method expects.

What this module adds either way is the type guard, because the protocol helpers
are arithmetic and will happily multiply a bool.
"""

from pyquadcortex import protocol
from pyquadcortex.device.translate.guards import _a_number, _a_whole_number

#: The tuner's reference pitch when the wire offset is zero. The wire stores an
#: OFFSET from this, not the pitch itself - see :func:`tuner_reference_hz`.
CONCERT_A_HZ = 440.0


def input_level_db(level: float) -> float:
    """An input port's wire level (0..1) as the dB the unit displays.

    Input gain spans -12 to +60 dB. Delegates to
    :func:`pyquadcortex.protocol.input_level_db`, which carries the measurement.

    An input port and a lane are both a 0..1 wire value and they are NOT the
    same scale - see :func:`lane_level_db`.

    A level outside 0..1 is converted rather than refused, unlike
    :func:`hold_timing_ms`, which refuses an index outside its six. The
    difference is that an out-of-span level still has a meaning under a linear
    scale - it is off the end of the knob - while an index outside its list
    names nothing at all. Neither has been seen from a unit.
    """
    return protocol.input_level_db(_a_number(level, "an input level"))


def db_to_input_level(db: float) -> float:
    """Displayed input-gain dB as the wire level an input port takes.

    Refuses anything outside -12..+60 dB rather than clamping, because a clamped
    write lands and reads back as a value the caller never asked for.
    """
    return protocol.db_to_input_level(_a_number(db, "an input gain in dB"))


def lane_level_db(value: float) -> float:
    """A lane, mixer or splitter LEVEL wire value (0..1) as displayed dB.

    These span -40 to +12 dB, with 0 dB at :data:`pyquadcortex.protocol.UNITY_LEVEL`
    (10/13). Delegates to :func:`pyquadcortex.protocol.lane_level_db`.

    The bottom of the knob is a detent, not a dB value: wire 0.0 reads "Off" on
    screen and -39.5 dB (wire 0.01) is the lowest numeric step. This converts the
    scale; it does not model the Off position.
    """
    return protocol.lane_level_db(_a_number(value, "a lane level"))


def db_to_lane_level(db: float) -> float:
    """Displayed dB as the wire value a lane, mixer or splitter LEVEL takes.

    Refuses anything outside -40..+12 dB.

    **-40.0 dB is silence, not the bottom of the knob.** It converts to wire
    0.0, which is the Off detent: the lowest NUMERIC step on the unit is -39.5
    dB, and the screen reads "Off" below it. So asking for -40 dB mutes the
    lane, and anything between -40.0 and -39.5 is a reading the screen has no
    way to show. For silence, write the wire's 0.0 directly and mean it.
    """
    return protocol.db_to_lane_level(_a_number(db, "a lane level in dB"))


def tempo_bpm(value: float) -> float:
    """A ``TEMPO`` wire value (0..1) as the bpm the unit displays.

    Tempo spans 40 to 240 bpm. Delegates to
    :func:`pyquadcortex.protocol.tempo_bpm`, which carries the measurement and its
    limits: three screen-vs-wire points, with the two endpoints coming from the
    fit rather than from a driven extreme.

    A wire value outside 0..1 is REFUSED, where :func:`input_level_db` and
    :func:`lane_level_db` convert one. That difference is the protocol helpers'
    rather than a rule added at this seam - the tempo one refuses because the bpm
    a caller would read back does not exist on the unit - and this wrapper
    neither widens it nor narrows it.
    """
    return protocol.tempo_bpm(_a_number(value, "a tempo wire value"))


def bpm_to_tempo(bpm: float) -> float:
    """A displayed tempo in bpm as the wire value ``TEMPO`` takes.

    Refuses anything outside 40..240 bpm rather than clamping, because a clamped
    write lands and reads back as a tempo the caller never asked for.

    This pair is a wrapper and not a home. The protocol layer calls
    :func:`pyquadcortex.protocol.bpm_to_tempo` itself, inside
    :meth:`~pyquadcortex.protocol.QuadCortex.set_tempo_param`, so the helper has
    to stay down there: moving it up here would make the protocol layer import
    the model, which is the one direction the layering forbids.
    """
    return protocol.bpm_to_tempo(_a_number(bpm, "a tempo in bpm"))


def tuner_reference_hz(offset: float) -> float:
    """The tuner's wire ``frequency`` as the absolute reference pitch on screen.

    The wire stores an OFFSET from 440 Hz, not the pitch: setting FREQ to 442 on
    the unit broadcast ``frequency: 1.99999809``. The screen shows 442, so the
    model does too.

    **Evidence:** that single observed pair (442 -> 2.0) is the whole of it. It
    fixes the zero point and the direction; that the unit is one Hz per unit
    rather than something that merely agrees at 2.0 has not been checked against
    a second value on screen, and this function says so rather than implying more
    (see :meth:`pyquadcortex.protocol.QuadCortex.set_tuner_reference`). No range
    is enforced for the same reason: the unit's FREQ limits have not been read,
    and a limit invented here would refuse a setting the unit allows.

    Nothing is rounded either, so the wire's 1.99999809 reads back as
    441.99999809 rather than the 442 on the screen. How many digits the unit's
    FREQ field shows has not been read off it, and rounding to a precision
    nobody has checked would be the same guess in the other direction.
    """
    return CONCERT_A_HZ + _a_number(offset, "a tuner reference offset")


def hz_to_tuner_reference(hz: float) -> float:
    """A reference pitch in Hz as the offset from 440 the wire carries.

    Inverse of :func:`tuner_reference_hz`; see it for the evidence and for why
    no range is enforced.
    """
    return _a_number(hz, "a tuner reference pitch") - CONCERT_A_HZ


def hold_timing_ms(index: int) -> int:
    """The wire's ``hold_timing`` index as the milliseconds the screen shows.

    Six settings, 500 to 1000 ms in 100 ms steps. The device accepts and stores
    any integer in that field without validating it, so an index outside the six
    means something wrote a value no screen can show - reported rather than
    rounded to the nearest real setting.
    """
    choices = protocol.QuadCortex.HOLD_TIMING_MS
    _a_whole_number(index, "a wire hold-timing index")
    if not 0 <= index < len(choices):
        raise ValueError(
            f"hold timing reads {index!r}, which is outside the "
            f"{len(choices)} values the unit offers - something wrote an "
            f"unvalidated value into it"
        )
    return choices[index]


def ms_to_hold_timing(milliseconds: int) -> int:
    """Milliseconds as the ``hold_timing`` index the wire carries.

    Only the six values the unit offers convert. Anything else is refused rather
    than rounded, and that is meant literally: 500.9 ms is not 500 ms, and
    ``"500"`` is not a number. The protocol layer's setter takes
    ``int(milliseconds)`` and so accepts both, which is the behaviour this
    docstring would otherwise be describing wrongly.
    """
    choices = protocol.QuadCortex.HOLD_TIMING_MS
    _a_whole_number(milliseconds, "hold timing in ms")
    if milliseconds not in choices:
        raise ValueError(
            f"hold timing must be one of {list(choices)} ms, "
            f"not {milliseconds!r}"
        )
    return choices.index(milliseconds)
