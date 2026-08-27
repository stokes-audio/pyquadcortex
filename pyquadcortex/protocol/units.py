"""The numbers the device's catalog names but does not spell out.

Every parameter's scale comes from the catalog - its ``min``, ``max`` and
``skew`` - and :class:`pyquadcortex.protocol.catalog.Parameter` does the
converting. This module holds the few numbers the catalog leaves symbolic, plus
the handful of scales that belong to things the catalog does not describe at
all, such as an input PORT.

**The history is worth knowing, because it cost several days.** A
``<Parameter>``'s ``min`` and ``max`` are usually numbers, but 55 of them are a
NAME - ``min="MIN_CABSIM_DB"``. The parser handed those to a float conversion
that fell back to ``0.0`` and ``1.0``, and that invented a concept this library
called a "placeholder range": a parameter published as ``0..1`` with a real unit
and therefore, supposedly, unconvertible. There is no such thing. Zero
parameters in the shipped catalog are published that way. There was only a bound
we could not read, and a table of hand-measured spans grew for months to work
around it.

So: a bound the catalog names needs an entry HERE, with the evidence for its
number, in the same commit that meets it. :func:`catalog._as_bound` raises for a
name it has never seen rather than falling back, because falling back is what
created the bug.

See ADR-0015.
"""


#: The numeric bounds the catalog NAMES but does not spell out.
#:
#: Each entry records how its number is known. Adding one without that is the
#: guess the rule against guessing exists to prevent.
FIRMWARE_CONSTANTS = {
    # A PCOM cab spells the very same LEVEL knob out literally: min="-40"
    # max="6". Confirmed on screen through the skew=4.9594844 taper at three
    # points - wire 0.01/0.50/1.00 read -21.8/0.0/6.0 dB, 2026-08-26.
    "MIN_CABSIM_DB": -40.0,
    "MAX_CABSIM_DB": 6.0,

    # steps=241 across the span means 0.1 dB steps, which fixes the width at 24
    # dB on its own. Measured 2026-08-25 on Parametric-8 at four points -
    # 0.0/0.10/0.50/1.00 read -12.0/-9.6/0.0/+12.0 - and separately on
    # Parametric-3 and the Output Equalizer rather than assumed to carry over.
    "MIN_EQ_DB": -12.0,
    "MAX_EQ_DB": 12.0,

    # The lane, mixer and splitter LEVEL family. Measured 2026-08-25: lane
    # VOLUME -3.1 at 0.71 and +12.0 at 1.0; MIXER LEVEL -24.4 at 0.30; splitter
    # LEVEL TO B -3.1 at 0.71. Confirmed a second way from the wire alone on
    # 2026-08-26 - a splitter's LEVEL TO A and LEVEL TO B both sit at
    # 0.76923078, which is 10/13, and 10/13 of -40..12 is exactly 0 dB.
    #
    # Two releases said -100..+30 here, and the mistake is instructive: both
    # spans put 0 dB at exactly 10/13, so unity - the only point the original
    # measurement had - could not tell them apart. Two close points cannot
    # distinguish spans. Take the extremes.
    "MIN_MIXER_DB": -40.0,
    "MAX_MIXER_DB": 12.0,

    # The SEND side of the FX loop: a Send block's LEVEL and THRU, and an FX
    # Loop's SEND LEV. Measured 2026-08-26 at five points including both ends -
    # -39.6 at 0.01, -36.0 at 0.10, -20.0 at 0.50, -10.0 at 0.75, 0.0 at 1.00 -
    # and every one is exact. It tops out at UNITY, not +12: a send cannot
    # boost, which is why this family is separate from the mixer's.
    "MIN_FXLOOP_OUT_GAIN_DB": -40.0,
    "MAX_FXLOOP_OUT_GAIN_DB": 0.0,

    # The RETURN side: an FX Loop's LEVEL and RET LEV. Measured 2026-08-26 at
    # -34.8 at 0.10, -14.0 at 0.50, -1.0 at 0.75. A return CAN boost.
    "MIN_FXLOOP_IN_GAIN_DB": -40.0,
    "MAX_FXLOOP_IN_GAIN_DB": 12.0,

    # steps=201 across the span means whole bpm, fixing the width at 200.
    # Measured 2026-08-25 at 59/111/120 bpm against wire 0.095/0.355/0.400.
    "MIN_TEMPO": 40.0,
    "MAX_TEMPO": 240.0,

    # The Splitter Crossover's FREQUENCY. SOLVED rather than measured, and the
    # derivation is worth keeping because it needed no screen: the catalog
    # states defaultValue="400.0" and skew="0.17722914651016206", and the unit
    # was holding wire 0.49547526240348816 for that knob on 2026-08-26. Solving
    # the law for `max` with min=20 gives 20000.000 to three decimals. Two
    # catalog facts and one wire reading pin both ends.
    "MIN_EQ_FREQ": 20.0,
    "MAX_EQ_FREQ": 20000.0,
}

#: Bounds the catalog names that NOBODY HAS MEASURED, and why not.
#:
#: A parameter with one of these carries ``None`` for that bound and REFUSES to
#: convert, rather than answering against a number somebody made up. This is the
#: ADR-0007 shape: modelled, and refuses out loud.
UNMEASURED_BOUNDS = {
    # NC_Recorder OUT LEVEL, the one parameter in DO_NOT_PROBE. steps=41 and
    # defaultValue=MAX_INPUT_TRIM are everything the catalog gives, and neither
    # fixes an endpoint. Placing the block to read the screen crashes the unit,
    # so this stays unmeasured on purpose.
    "MIN_INPUT_TRIM": "NC_Recorder OUT LEVEL - see DO_NOT_PROBE",
    "MAX_INPUT_TRIM": "NC_Recorder OUT LEVEL - see DO_NOT_PROBE",
}

#: The lowest wire position with a NUMERIC display, for families whose bottom is
#: an OFF detent rather than the bottom of the scale. Keyed by the catalog's own
#: constant name, which is how the parser finds it.
#:
#: This exists because ``min`` is frequently NOT a place the knob goes. A cab
#: LEVEL's law runs to -40 dB, but its quietest real setting is -21.8 dB at wire
#: 0.01 and the screen reads OFF below that. Without a floor, asking for -30 dB
#: returns wire 0.0005 and MUTES the mic - a silently wrong value, which is the
#: failure this library exists to prevent.
#:
#: ``min_string="OFF"`` in the catalog says the bottom shows a word rather than a
#: number, but not WHERE the numbers resume. Only measurement knows that, which
#: is why this table is separate from :data:`FIRMWARE_CONSTANTS` and why it
#: covers only the families somebody has actually driven.
FLOOR_WIRE = {
    # -21.8 dB at wire 0.01, OFF below it. Measured 2026-08-26.
    "MIN_CABSIM_DB": 0.01,
    # -39.5 dB at wire 0.01 on the lane VOLUME, confirmed on the splitter's
    # LEVEL TO A, which reads OFF at wire 0.0. Measured 2026-08-25.
    "MIN_MIXER_DB": 0.01,
    # -39.6 dB at wire 0.01. Measured 2026-08-26.
    "MIN_FXLOOP_OUT_GAIN_DB": 0.01,
    "MIN_FXLOOP_IN_GAIN_DB": 0.01,
}

#: What a caller wanting silence should write instead of the bottom of a dB
#: scale. Shared by every family in :data:`FLOOR_WIRE`.
OFF_HINT = ("for silence write the wire value 0.0, the Off position - the "
            "bottom of the dB scale is a different thing")

#: Parameters that will NOT be measured, and why. Distinct from an unmeasured
#: bound: nobody is going to look, so a later session should not spend a session
#: rediscovering the reason.
DO_NOT_PROBE = {
    # NC_Recorder is the internal recorder the Neural Capture wizard drives, not
    # a block. Placing it on the grid to measure OUT LEVEL CRASHED the unit -
    # "Something went wrong ... Cancel / Reboot" - and required a reboot,
    # 2026-08-26. Its `internal` and `hidden` flags are both false, which is what
    # made it look placeable; the category name "Neural Capture Internal" was the
    # real signal and was not read carefully enough. The same two blocks placed
    # again without it did not crash.
    #
    # This is the second time probing capture/IR machinery has taken the unit
    # down - CLAUDE.md already records that IR-import probing killed the USB link
    # and needed a power cycle. One unmeasured parameter is the better trade.
    (20000, 2): "NC_Recorder OUT LEVEL - placing this block crashes the unit",
}


#: The wire value a lane, mixer or splitter LEVEL holds at unity, where nothing
#: is attenuated. 10/13 exactly, which is 0 dB on the -40..+12 span.
UNITY_LEVEL = 0.76923077


def _level_span() -> tuple[float, float]:
    """The lane/mixer/splitter dB span, from the one place it is written down."""
    return FIRMWARE_CONSTANTS["MIN_MIXER_DB"], FIRMWARE_CONSTANTS["MAX_MIXER_DB"]


def _tempo_span() -> tuple[float, float]:
    return FIRMWARE_CONSTANTS["MIN_TEMPO"], FIRMWARE_CONSTANTS["MAX_TEMPO"]


def input_level_db(level: float) -> float:
    """Convert an input port's wire ``level`` (0..1) to the dB the unit displays.

    An input port's gain spans **-12 to +60 dB**, so ``dB = -12 + 72 * level``.
    Solved from four owner-set trims read simultaneously on screen and on the wire
    (screen +17.2/+16.8/+24.0/0.0 against wire 0.40556/0.40043/0.50009/0.16667 -
    every point lands within display rounding, and 0 dB is exactly 1/6). It also
    matches the hardware spec sheet's "MAX INPUT GAIN: +60dB".

    An input PORT is not a catalog model, so nothing in the catalog describes
    this scale and this function is the only place it lives. Lane and mixer
    levels run -40..+12 dB instead - :func:`lane_level_db` converts those.
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

    A convenience over the ``MIN_MIXER_DB`` family in :data:`FIRMWARE_CONSTANTS`,
    which is where the span and its evidence live. The family is linear, so
    ``dB = -40 + 52 * value``, with 0 dB at :data:`UNITY_LEVEL`.

    The bottom of the knob is special: -39.5 dB (wire 0.01) is the lowest NUMERIC
    step, and below it the screen reads "Off" - so wire 0.0 is an Off position,
    not -40 dB. This function still maps 0.0 to -40.0 because it converts the
    scale; it does not model the Off detent. :data:`FLOOR_WIRE` does, and
    ``Parameter.to_normalized`` enforces it.

    Prefer ``qc.set_param(..., real=)``, which reads the span from the catalog
    and applies any taper. This helper exists for the ``device/translate``
    boundary, which converts without a catalog in hand.
    """
    low, high = _level_span()
    return low + (high - low) * value


def db_to_lane_level(db: float) -> float:
    """Convert displayed dB to the wire value a lane/mixer/splitter LEVEL takes.

    Inverse of :func:`lane_level_db`. Values outside the span do not exist on the
    unit and are refused rather than silently clamped. The knob's numeric floor
    is -39.5 dB; for silence write 0.0 directly, the Off position, instead of
    converting a dB value.
    """
    low, high = _level_span()
    if not low <= db <= high:
        raise ValueError(
            f"lane and mixer levels run {low:g}..{high:+g} dB on the unit; "
            f"{db} dB does not exist ({OFF_HINT})"
        )
    return (db - low) / (high - low)


def tempo_bpm(value: float) -> float:
    """Convert a ``TEMPO`` wire value (0..1) to the bpm the unit displays.

    A convenience over the ``MIN_TEMPO`` family in :data:`FIRMWARE_CONSTANTS`,
    which is where the span and its evidence live. Tempo is linear over
    **40 to 240 bpm**, so ``bpm = 40 + 200 * value``; the catalog's ``steps=201``
    says the same thing, one step per whole bpm.

    A wire value outside 0..1 is refused rather than converted, for the same
    reason :func:`bpm_to_tempo` refuses a bpm outside the span: the tempo the
    caller would read back does not exist on the unit.
    """
    if not 0.0 <= value <= 1.0:
        raise ValueError(
            f"a tempo wire value runs 0..1; {value} is outside it"
        )
    low, high = _tempo_span()
    return low + (high - low) * value


def bpm_to_tempo(bpm: float) -> float:
    """Convert a bpm to the wire value ``TEMPO`` takes.

    Inverse of :func:`tempo_bpm`. A bpm outside the span does not exist on the
    unit and is refused rather than silently clamped.
    """
    low, high = _tempo_span()
    if not low <= bpm <= high:
        raise ValueError(
            f"the unit's tempo runs {low:g}..{high:g} bpm; {bpm} bpm does not exist"
        )
    return (bpm - low) / (high - low)


#: Where user setlists live. They sit SIDE BY SIDE here rather than nested inside
#: "My Presets" - a folder created under My Presets is not a setlist and the device
#: ignores it. :meth:`QuadCortex.create_setlist` builds a key from this.
USER_SETLIST_ROOT = "/media/p4/Presets"
