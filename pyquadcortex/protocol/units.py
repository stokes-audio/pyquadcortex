"""The numbers the device's catalog names but does not spell out.

Every parameter's scale comes from the catalog - its ``min``, ``max`` and
``skew`` - and :class:`pyquadcortex.protocol.catalog.Parameter` does the
converting. This module holds the few numbers the catalog leaves symbolic, plus
the handful of scales that belong to things the catalog does not describe at
all, such as an input PORT.

**The history is worth knowing, because it cost several days.** A
``<Parameter>``'s ``min`` and ``max`` are usually numbers, but 55 PARAMETERS name
one instead - ``min="MIN_CABSIM_DB"``. The parser handed those to a float conversion
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
    # four points INCLUDING THE TOP - -39.5 at 0.01, -34.8 at 0.10, -14.0 at
    # 0.50, +12.0 at 1.00. A return CAN boost, which is what separates it from
    # the send side above; citing only the three interior points would have left
    # the +12 looking assumed, which is the mistake recorded under MIN_MIXER_DB.
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

#: Where a knob's numbers actually start, for the families whose bottom is an
#: OFF detent rather than the bottom of the scale.
#:
#: Keyed by the LAW - ``(minimum, maximum, skew)`` after the bounds are resolved
#: - and NOT by the catalog's constant name, which was the first attempt and was
#: wrong. The device spells one knob two ways: most cabs say
#: ``min="MIN_CABSIM_DB"`` while the PCOM variants write ``min="-40" max="6"``
#: for the identical control, same taper and all. Keyed by spelling, the guard
#: protected one and not the other, so asking a PCOM cab for -30 dB returned
#: wire 0.000516 and MUTED the microphone - the exact bug this table exists to
#: prevent, surviving inside the fix for it.
#:
#: The law is the physical control, so the law is the honest key.
#:
#: Each value is ``(floor_wire, displayed)``: the lowest wire position with a
#: NUMERIC display, and what the unit SHOWS there. Both are measured. The second
#: matters because the law does not reproduce it exactly - the lane family's
#: fitted value at wire 0.01 is -39.48 while the screen says -39.5 - and a
#: refusal that quotes a number it would itself reject is a dead end for whoever
#: reads it.
#:
#: ``min_string="OFF"`` says the bottom of a range shows a word rather than a
#: number, and 254 parameters carry it. It does NOT say where the numbers
#: resume, so it cannot key this table; only measurement knows that. The
#: consequence is deliberate and worth stating: parameters on a listed law
#: inherit its floor even where nobody drove that particular knob. For the two
#: linear families that costs at most a loud refusal across a 0.5 dB sliver at
#: the very bottom. For the cab it prevents an 18 dB silent mute. That trade is
#: the right way round.
#:
#: **Every value in here was first read with the unit's own encoder, and that is
#: not the same measurement as the one described above.** A knob the catalog
#: gives no ``steps`` moves in hundredths when it is TURNED, so wire 0.01 is the
#: first position a player can reach - which is why all three original entries
#: said 0.01. A host write goes lower, and driving each family that way on
#: 2026-09-11 found the three families did not agree with each other:
#:
#: | family | at wire 0.000001 | verdict |
#: |---|---|---|
#: | cab LEVEL | `-37.2 dB` | no detent - ENTRY REMOVED |
#: | lane / mixer / splitter | `OFF` | detent real, floor kept |
#: | FX send | `OFF`, but `-39.8 dB` at 0.005 | detent real, floor lowered |
#:
#: So there is no rule here, and one was looked for. "A stepless knob has no
#: detent" fits the cab and the amp OUTPUT family and is FALSE for the lane and
#: the send, which are stepless and stop at a word. The only way to know a
#: family is to drive it, which is what this table has always said.
#:
#: **The cab entry was removed on evidence, not on doubt.** It claimed `OFF`
#: below wire 0.01 and called -21.8 dB the quietest position, costing a caller
#: 16 dB of a real range. Three independent readings say otherwise, all on
#: CorOS 4.0.1 through a `412 CA Stand OS A V30 01 (M)`: the screen prints
#: -37.2 dB at wire 0.000001; with the second microphone fully Off the cab is
#: audibly passing signal at wire 0.009, below the claimed floor; and 0.009
#: against 0.011 - 0.7 dB apart across the claimed boundary - sound the same,
#: where a real cliff would be silence against a tone.
#:
#: Which means the record that built this table misread its own evidence. A
#: caller asking a cab for -30 dB got wire 0.000516, and -30 dB on one
#: microphone is close to inaudible - so "MUTED the microphone" was the level
#: that was asked for, arriving correctly. The library was doing as it was told.
#: What the table legitimately prevents is a value below a REAL detent, and the
#: two families that have one keep their guards.
FLOOR_WIRE = {
    # The cab section's per-mic LEVEL is NOT here, and the gap is deliberate.
    # It read (0.01, -21.8) from 2026-08-26 until 2026-09-11, when the knob was
    # driven below the encoder's reach and turned out to have no detent at all.
    # See the note above; `tests/test_scales.py` holds the readings that removed
    # it, so re-adding it has to argue with them.
    # The lane, mixer, splitter and FX-return LEVEL family. -39.5 dB at wire
    # 0.01 on the lane VOLUME, confirmed on the splitter's LEVEL TO A, which
    # reads OFF at wire 0.0. Measured 2026-08-25.
    (-40.0, 12.0, 1.0): (0.01, -39.5),
    # The FX loop's send side. -39.8 dB at wire 0.005, with OFF at 0.000001, so
    # the detent is real and the floor is LOWER than the 0.01 / -39.6 dB read
    # off the encoder on 2026-08-26. Lowered 2026-09-11. Not bisected: the
    # boundary is somewhere in (0.000001, 0.005], and this is the lowest
    # position anybody has actually seen a number at.
    (-40.0, 0.0, 1.0): (0.005, -39.8),
}

#: The span a LABELLED-END control actually draws, whatever it declares.
#:
#: 36 parameters carry ``min_string``, ``mid_string`` and ``max_string``
#: together - 35 spell them "L"/"C"/"R" and one, `Micro Processor (ST)`'s
#: `A/B PITCH MIX`, spells them "A"/"A/B"/"B". They are pan-style controls, and
#: the unit draws every one of them as a bipolar scale reading 50 on one side
#: through the middle label to 50 on the other. The declared ``min``/``max`` do
#: NOT say that, and they do not even agree with each other: the same drawn
#: control is declared four different ways.
#:
#: | declared span | parameters | measured on |
#: |---|---|---|
#: | -1..1 | 22 | a stereo cab's `BALANCE` |
#: | 0..10 | 10 | a mono cab's `PAN` |
#: | 0..1 | 3 | a Minivoicer's `V1 PAN` and `V2 PAN` |
#: | -50..50 | 1 | declares the drawn span already, with ``steps=101`` |
#:
#: Read off the screen on CorOS 4.0.1, 2026-09-11, by writing wire values and
#: looking: wire 0.0 shows "50 L", wire 0.5 shows "C", wire 0.75 shows "25 R",
#: wire 1.0 shows "50 R". A Minivoicer `V1 PAN` sitting at its untouched
#: default of 0.6 shows "10 R", which is the same line and needed no write at
#: all. So the display is ``(wire - 0.5) * 100``, with the sign shown as the
#: side letter. Every reading is in `tests/test_scales.py`.
#:
#: Corroborated by a note in `protocol.md` that predates this work: the lane
#: output's `1 PAN` is recorded there as "0.5 is centre". That parameter
#: declares 0..1, where wire 0.5 would read "0.50" rather than a middle
#: label, so the bipolar behaviour had been seen before without being named.
#:
#: **Why this is a span and not a refusal.** ADR-0015 makes the catalog the
#: source of a scale, and here the catalog is measurably wrong rather than
#: imprecise - `Real(0.0)` reached hard left on a mono cab and dead center on a
#: stereo one, for the same physical knob, purely because the two entries
#: declare different numbers. Refusing `Real` on all 36 was the other option and
#: would have been safe and useless. Three of the four declared spans are
#: measured and the fourth states the drawn span itself, so nothing here is
#: inherited from a knob nobody drove.
#:
#: **Why the key is all three labels.** 267 parameters carry one or two of them,
#: almost always ``min_string="OFF"`` on a dB scale, and none of those is a pan.
#: Requiring all three selects exactly the 36. The declared span cannot be the
#: key: ``(0.0, 1.0, 1.0)`` is one of the commonest laws in the catalog and
#: almost none of those parameters is in this family.
#:
#: The DEFAULT moves with the span. A declared default is a position on the
#: declared scale, so leaving it behind would make a mono cab's `PAN` report 5
#: against -50..+50 - "5 R" for a knob whose default is dead centre. Converted
#: through the declared law, the three centred families all land on 0.0 and a
#: Minivoicer's `V1 PAN` lands on 10.0, which is the `10 R` its untouched
#: default actually shows.
#:
#: Applied to LINEAR members only. All 36 declare no skew or skew=1, so the
#: measured straight line and the declared taper agree; a member that ever
#: declares a taper keeps what the catalog said rather than being converted
#: through a mapping nobody measured for it.
#:
#: What is NOT claimed: the granularity. ``steps`` reads 360 on 21 of them and
#: nothing on the rest, while the one entry declaring the drawn span says 101.
#: The readings land on whole numbers, and nobody has looked for the smallest
#: move the screen will show.
LABELLED_END_SPAN = (-50.0, 50.0)

#: What a caller wanting silence should write instead of the bottom of a dB
#: scale. Shared by every family in :data:`FLOOR_WIRE`.
OFF_HINT = ("for silence write the wire value 0.0, the Off position - the "
            "bottom of the dB scale is a different thing")

#: Models that must never be placed on the grid, and why.
#:
#: Enforced by :meth:`~pyquadcortex.protocol.QuadCortex.set_block`, which is the
#: only reason this is a separate table from :data:`DO_NOT_PROBE`: a note that
#: nothing reads is not a guard, and the note here is "this reboots the unit".
UNPLACEABLE_MODELS = {
    20000: ("NC_Recorder is the internal recorder the Neural Capture wizard "
            "drives, not a block. Placing it on the grid CRASHED the unit - "
            "\"Something went wrong ... Cancel / Reboot\" - and required a "
            "reboot, 2026-08-26. Its `internal` and `hidden` flags are both "
            "false, which is what made it look placeable; the category name "
            "\"Neural Capture Internal\" was the real signal and was not read "
            "carefully enough."),
}

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
    this scale; the numbers live in ``SETTING_SPANS["INPUT_GAIN_DB"]``, with the
    readings that produced them. Lane and mixer levels run -40..+12 dB instead -
    :func:`lane_level_db` converts those.
    """
    low, high = SETTING_SPANS["INPUT_GAIN_DB"]
    return low + (high - low) * level


def db_to_input_level(db: float) -> float:
    """Convert displayed input-gain dB to the wire ``level`` an input port takes.

    Inverse of :func:`input_level_db`; see it for how the scale was measured.
    Values outside the span do not exist on the unit and are refused rather than
    silently clamped.
    """
    low, high = SETTING_SPANS["INPUT_GAIN_DB"]
    if not low <= db <= high:
        raise ValueError(
            f"input gain runs {low:g}..{high:g} dB on the unit; {db} dB does "
            f"not exist"
        )
    return (db - low) / (high - low)


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

    Prefer ``qc.set_param(..., Db(...))``, which reads the span from the catalog
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


#: The spans a SETTING has that no catalog model describes.
#:
#: Separate from :data:`FIRMWARE_CONSTANTS`, which resolves a name the catalog
#: itself writes. Nothing in the catalog mentions an input port or the Global
#: EQ at all, so these numbers have no name to resolve and their only home is
#: here. Each records how it is known, and they are not known the same way -
#: which is the point of writing it down rather than presenting one list. The
#: input port's four points all sit in the bottom half of its travel and lean on
#: the spec sheet for the top; the Global EQ's four span the whole travel and
#: include both ends.
SETTING_SPANS = {
    # An input port's gain. Solved from four owner-set trims read simultaneously
    # on screen and on the wire - screen +17.2/+16.8/+24.0/0.0 against wire
    # 0.40556/0.40043/0.50009/0.16667. Every point lands within display
    # rounding, 0 dB is exactly 1/6, and it agrees with the hardware spec
    # sheet's "MAX INPUT GAIN: +60dB". See :func:`input_level_db`.
    "INPUT_GAIN_DB": (-12.0, 60.0),

    # A Global EQ band's GAIN. Driven on screen 2026-09-11, CorOS 4.0.1: band 1's
    # GAIN written over the wire and the Global EQ page read each time - wire
    # 0.0/0.25/0.75/1.0 displayed -12.0/-6.0/+6.0/+12.0 dB. The ENDS are what
    # settle the span, and they were the measurement's point: before this the
    # span was the MANUAL's on two points 6 dB apart on a range claimed to be
    # 24 dB wide, which is the shape of the mistake that put -100..+30 in
    # MIN_MIXER_DB above for two releases. The two quartiles came along free and
    # rule out a taper: at the display's own 0.1 dB rounding the two together
    # admit only skews 0.994..1.006, so this is linear rather than a power law
    # close to it. `test_the_global_eq_gain_quartiles_rule_out_a_taper` computes
    # that intersection rather than quoting this comment.
    "GLOBAL_EQ_GAIN_DB": (-12.0, 12.0),
}

# There is deliberately NO `global_eq_gain_db()` pair beside `input_level_db()`.
# The input port's pair predates ADR-0017 and has a caller that needs to convert
# with no catalog in hand - `device/translate`. A span added since converts
# through a `catalog.Parameter` built from the entry above, so it gets ADR-0015's
# one law, the unit check and the range refusal at once. A function per span
# would be a second implementation of each, which is what ADR-0017 says it
# avoided; if a reader ever needs one in dB, the answer is a public accessor for
# the scale, not a new pair of functions.


#: Where user setlists live. They sit SIDE BY SIDE here rather than nested inside
#: "My Presets" - a folder created under My Presets is not a setlist and the device
#: ignores it. :meth:`QuadCortex.create_setlist` builds a key from this.
USER_SETLIST_ROOT = "/media/p4/Presets"
