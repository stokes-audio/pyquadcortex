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
