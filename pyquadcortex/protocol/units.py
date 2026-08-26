"""Screen values and wire values, for the scales the catalog cannot convert.

The device publishes some parameters with a real-world unit but the range
``0.0..1.0`` - a placeholder, the wire's own normalized scale rather than the
span the parameter covers. For those the catalog offers nothing to convert
with, so the span was measured against the unit's screen and lives here.

These are pure functions over floats, and they are separate from ``client.py``
for a structural reason: ``targets.py`` needs them, because a target owns the
knowledge that ITS placeholder parameter has a measured span - `LaneOutput`
converts VOLUME in dB, `Tempo` converts TEMPO in bpm - and ``client.py`` imports
``targets.py``. Keeping these here is what stops that being a cycle.

Every span was solved the same way and carries the same warning: take THREE
well-separated screen readings, not two. Two close points cannot distinguish
spans, which is how the lane levels carried a wrong span for two releases.

The parameters whose spans have NOT been measured still refuse ``real=``. There
are 51 of them across 23 models; recovering their spans is tracked separately.
"""

#: The wire value the mixer, splitter and lane-output LEVEL parameters hold when
#: nothing is attenuated - 10/13, which is 0 dB on the -40..+12 dB span those
#: controls cover. The catalog publishes them as 0..1 "dB" (see
#: :attr:`~pyquadcortex.protocol.catalog.Parameter.range_is_placeholder`), so this is the
#: reference point for reading and writing them. Measured on every row carrying
#: one across 17 factory presets.
#:
#: Two releases said -100..+30 dB here, and the mistake is instructive: both spans
#: put 0 dB at exactly 10/13 (100/130 = 40/52), so unity - the only point the
#: original measurement had - cannot tell them apart. The true span comes from
#: reading the screen against the wire at three more points: -3.1 dB at 0.71,
#: +12.0 dB at 1.0, and -39.5 dB at 0.01, the lowest numeric step before the
#: screen reads "Off". :func:`lane_level_db` / :func:`db_to_lane_level` convert.
UNITY_LEVEL = 0.76923077


def input_level_db(level: float) -> float:
    """Convert an input port's wire ``level`` (0..1) to the dB the unit displays.

    An input port's gain spans **-12 to +60 dB**, so ``dB = -12 + 72 * level``.
    Solved from four owner-set trims read simultaneously on screen and on the wire
    (screen +17.2/+16.8/+24.0/0.0 against wire 0.40556/0.40043/0.50009/0.16667 -
    every point lands within display rounding, and 0 dB is exactly 1/6). It also
    matches the hardware spec sheet's "MAX INPUT GAIN: +60dB".

    INPUT ports only. Lane and mixer levels run -40..+12 dB instead -
    :func:`lane_level_db` converts those.
    """
    return -12.0 + 72.0 * level


def db_to_input_level(db: float) -> float:
    """Convert displayed input-gain dB to the wire ``level`` an input port takes.

    Inverse of :func:`input_level_db`; see it for how the scale was measured.
    Values outside -12..+60 dB do not exist on the unit and are refused rather
    than silently clamped.
    """
    if not -12.0 <= db <= 60.0:
        raise ValueError(
            f"input gain runs -12..+60 dB on the unit; {db} dB does not exist"
        )
    return (db + 12.0) / 72.0


def lane_level_db(value: float) -> float:
    """Convert a lane/mixer/splitter LEVEL wire ``value`` (0..1) to displayed dB.

    These controls span **-40 to +12 dB**, so ``dB = -40 + 52 * value``, with 0 dB
    at :data:`UNITY_LEVEL` (10/13 exactly). Solved from three screen readings taken
    against simultaneous wire reads of a row's VOLUME: -3.1 dB at 0.71, +12.0 dB at
    1.0, -39.5 dB at 0.01 (least squares over the three: -40.02..+11.99).

    The MIXER and SPLITTER were long claimed here on the strength of that VOLUME
    measurement alone. They were measured on 2026-08-25 and the claim held:
    splitter ``LEVEL TO B`` read -3.1 dB at 0.71 - the very same point - and
    +12.0 at 1.0, while ``MIXER LEVEL`` read -24.4 at 0.30 and +12.0 at 1.0.
    Splitter ``LEVEL TO A`` agrees at 0.30 and reads **"OFF"** at wire 0.0, so
    the Off detent belongs to the family and not to the lane VOLUME alone.

    The bottom of the knob is special: -39.5 dB (wire 0.01) is the lowest NUMERIC
    step, and below it the screen reads "Off" - so wire 0.0 is an Off position,
    not -40 dB. A caller wanting silence writes 0.0, not the bottom of the dB
    scale. This function still maps 0.0 to -40.0 because it converts the scale;
    it does not model the Off detent.

    The catalog publishes these parameters as 0..1 "dB" - a placeholder, which is
    why ``real=`` raises for them and this helper exists.
    """
    return -40.0 + 52.0 * value


def db_to_lane_level(db: float) -> float:
    """Convert displayed dB to the wire value a lane/mixer/splitter LEVEL takes.

    Inverse of :func:`lane_level_db`; see it for how the scale was measured.
    Values outside -40..+12 dB do not exist on the unit and are refused rather
    than silently clamped. The knob's numeric floor is -39.5 dB; for silence
    write 0.0 directly (the Off position) instead of converting a dB value.
    """
    if not -40.0 <= db <= 12.0:
        raise ValueError(
            f"lane and mixer levels run -40..+12 dB on the unit; {db} dB "
            f"does not exist (for silence write 0.0, the Off position)"
        )
    return (db + 40.0) / 52.0


def tempo_bpm(value: float) -> float:
    """Convert a ``TEMPO`` wire value (0..1) to the bpm the unit displays.

    Tempo spans **40 to 240 bpm**, so ``bpm = 40 + 200 * value``. Solved from three
    screen readings taken against simultaneous wire reads, each landing on the
    displayed integer exactly: 59 bpm at 0.095, 111 bpm at 0.355, 120 bpm at 0.400.
    The 59 is what makes the fit worth trusting - a span needs a point away from the
    others, which is the lesson the lane levels taught (``protocol.md``, "Some
    catalog ranges are placeholders").

    The ENDPOINTS are the fit's, not separate measurements: neither extreme was
    driven. They land on 40 and 240, which is the tempo range the unit's manual
    documents, so the two agree - but if you need the extremes exactly, drive them.

    The catalog publishes ``TEMPO`` as 0..1 with a real-world unit - a placeholder,
    which is why this helper exists.

    A wire value outside 0..1 is refused rather than converted, for the same reason
    :func:`bpm_to_tempo` refuses a bpm outside the span: the tempo the caller would
    read back does not exist on the unit.
    """
    if not 0.0 <= value <= 1.0:
        raise ValueError(
            f"a tempo wire value runs 0..1; {value} is outside it"
        )
    return 40.0 + 200.0 * value


def bpm_to_tempo(bpm: float) -> float:
    """Convert a bpm to the wire value ``TEMPO`` takes.

    Inverse of :func:`tempo_bpm`; see it for how the scale was measured. A bpm
    outside 40..240 does not exist on the unit and is refused rather than silently
    clamped.
    """
    if not 40.0 <= bpm <= 240.0:
        raise ValueError(
            f"the unit's tempo runs 40..240 bpm; {bpm} bpm does not exist"
        )
    return (bpm - 40.0) / 200.0


#: Where user setlists live. They sit SIDE BY SIDE here rather than nested inside
#: "My Presets" - a folder created under My Presets is not a setlist and the device
#: ignores it. :meth:`QuadCortex.create_setlist` builds a key from this.
USER_SETLIST_ROOT = "/media/p4/Presets"

#: How the unit stores "this scene has no label": a single space, not an empty
#: string. So ``label.strip()`` detects a blank scene and ``label == ""`` does not.
#: :meth:`QuadCortex.set_scene_label` sends this when given ``None``.


#: Parameters the catalog publishes as a PLACEHOLDER whose true span has been
#: MEASURED, keyed by ``(model id, wire index)`` and given in the parameter's own
#: units. Everything absent from here still refuses ``real=``, which is the
#: honest answer: an unmeasured span cannot be converted, only guessed.
#:
#: Keyed by INDEX rather than by name on purpose. A target knows its model id and
#: the index without asking the device, so a conversion here costs no catalog
#: fetch - and the catalog comes over USB.
#:
#: Every entry was measured against the unit's screen at THREE or more
#: well-separated points including both ends. Two close points cannot
#: distinguish spans, which is how the lane levels shipped ``-100..+30`` for two
#: releases: both candidate spans put 0 dB at 10/13, so unity could not tell
#: them apart. `docs/protocol.md` records each measurement.
MEASURED_SPANS = {
    # The lane, mixer and splitter LEVEL family: dB = -40 + 52 * wire.
    # The lane VOLUME came first (-3.1 at 0.71, +12.0 at 1.0, -39.5 at 0.01).
    # The mixer and splitter INHERITED that claim for several releases and were
    # measured on 2026-08-25: splitter LEVEL TO B read -3.1 at 0.71 and +12.0 at
    # 1.0, MIXER LEVEL read -24.4 at 0.30 and +12.0 at 1.0. LEVEL TO A agrees at
    # 0.30 and reads "OFF" at 0.0 - the same Off detent the lane VOLUME has, so
    # that is a property of the family and not of one control.
    #
    # The mixer's LEVEL A and LEVEL B nearly went down as NOT DRIVABLE: four host
    # writes appeared to be dropped. They were not. Both are scene-following, so
    # the wire carries EIGHT values, and a write lands on the ACTIVE scene while
    # the reader was taking `param_values[0]` - scene A. The unit was on scene E.
    # Measured properly afterwards: LEVEL A -24.4 at 0.30 and +12.0 at 1.0,
    # LEVEL B -3.1 at 0.71 and +12.0 at 1.0.
    (23000, 0): (-40.0, 12.0),      # LaneOutputControl VOLUME
    (11000, 0): (-40.0, 12.0),      # Mixer LEVEL A
    (11000, 2): (-40.0, 12.0),      # Mixer LEVEL B
    (11000, 5): (-40.0, 12.0),      # Mixer MIXER LEVEL
    (10004, 3): (-40.0, 12.0),      # Splitter LEVEL TO A
    (10004, 4): (-40.0, 12.0),      # Splitter LEVEL TO B
    # The per-preset tempo: bpm = 40 + 200 * wire, from three screen readings.
    (25000, 0): (40.0, 240.0),      # TempoControl TEMPO
}

#: Block EQ band gains: dB = -12 + 24 * wire. Measured 2026-08-25 on the
#: Parametric-8 at four points - 0.0 -> -12.0, 0.10 -> -9.6, 0.50 -> 0.0,
#: 1.00 -> +12.0 - so both ends and an off-half point that a curved mapping
#: would have missed. Parametric-3 and the Output Equalizer are separate catalog
#: entries and were each measured at both ends rather than assumed to inherit it.
#:
#: A band's TYPE decides whether its GAIN means anything: Lo Pass and Hi Pass
#: disable the control, and a gain written to such a band is stored and ignored.
#: Nothing here can detect that, so it stays a caller's problem and a docs note.
EQ_GAIN_SPAN = (-12.0, 12.0)
for _model, _bands in ((4000, 8), (4001, 3), (4004, 5)):
    for _band in range(_bands):
        MEASURED_SPANS[(_model, _band * 5)] = EQ_GAIN_SPAN
del _model, _bands, _band


def measured_to_wire(span, real: float) -> float:
    """Convert ``real`` to the wire's 0..1 across a measured ``span``.

    Refuses a value the unit has no position for, rather than clamping: a
    silently clamped write looks like it worked and lands somewhere else.
    """
    low, high = span
    if not low <= real <= high:
        raise ValueError(
            f"this parameter runs {low:g}..{high:g} on the unit; {real:g} does "
            f"not exist there. (For a level, silence is value=0.0 - the Off "
            f"position - not the bottom of the dB scale.)"
        )
    return (real - low) / (high - low)


def measured_from_wire(span, value: float) -> float:
    """Convert a wire 0..1 to the parameter's own units across ``span``."""
    low, high = span
    return low + (high - low) * value
