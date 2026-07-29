"""Named values for the device's ports, instrument tags, and setlists.

These mirror the Quad Cortex's own protocol enums, so members can be passed
straight to the client methods (they subclass ``int`` / ``str`` and serialize as
the plain value):

    from pyquadcortex import Input, Instrument, Setlist

    qc.set_chain_input(row=0, in_portid=Input.RETURN_1)
    qc.save_current_preset(Setlist.USER, pos, "My Preset",
                           instrument=Instrument.BASS)

See ``docs/protocol.md`` for how these values were established.
"""

from enum import IntEnum, StrEnum


class Input(IntEnum):
    """Input port for a grid chain (``BinaryPreset.Chain.in_portid``)."""

    EMPTY = 0             # chain is fed internally (splitter/mixer), not a port
    INPUT_1 = 1
    INPUT_2 = 2
    INPUT_1_2 = 3         # stereo pair
    RETURN_1 = 4
    RETURN_2 = 5
    RETURN_1_2 = 6        # stereo pair
    PREV_ROW = 7          # feed from the previous grid row ("Prev. Row")
    USB_5 = 8
    USB_6 = 9
    USB_7 = 10
    USB_8 = 11
    USB_5_6 = 12          # stereo pair
    USB_7_8 = 13          # stereo pair
    SIDECHAIN_BUFFER = 14  # internal sidechain source (blank in the UI)


class Output(IntEnum):
    """Output port for a grid chain (``BinaryPreset.Chain.out_portid``)."""

    EMPTY = 0
    XLR_1_2 = 1           # "Output 1/2"
    OUT_3_4 = 2           # "Output 3/4"
    SEND_1_2 = 3
    XLR_1 = 4             # "Output 1"
    XLR_2 = 5             # "Output 2"
    OUT_3 = 6
    OUT_4 = 7
    SEND_1 = 8
    SEND_2 = 9
    USB_5 = 10
    USB_6 = 11
    USB_7 = 12
    USB_8 = 13
    USB_5_6 = 14
    USB_7_8 = 15
    # 16-18 are internal grid-routing states: they feed another row rather than a
    # jack. 19 (MULTIPLE) is DIFFERENT - it is a real destination, and is what
    # factory presets use to reach the Multi-Out. The device does not validate
    # this field, so a nonsense id is stored rather than rejected.
    NEXT_ROW_3 = 16
    NEXT_ROW_4 = 17
    NEXT_ROW_3_4 = 18
    MULTIPLE = 19
    USB_3 = 20
    USB_4 = 21
    USB_3_4 = 22


class Scene(IntEnum):
    """The eight scenes within a preset, as labelled on the unit.

    The device numbers scenes from zero, so ``Scene.A`` is 0. Use these rather
    than bare integers::

        qc.switch_scene(Scene.B)
    """

    A = 0
    B = 1
    C = 2
    D = 3
    E = 4
    F = 5
    G = 6
    H = 7


class Instrument(IntEnum):
    """Instrument category tag for a saved preset (``ProductData.instrument``).

    Values are bit flags; 3 is unused.
    """

    # An untagged preset. This is the default a save sends when no instrument
    # is given, and the device accepts it; it simply carries no tag.
    NONE = 0
    GUITAR = 1
    BASS = 2
    VOCAL = 4


class Setlist(StrEnum):
    """Device filesystem paths of the setlists presets are addressed within."""

    USER = "/media/p4/Presets/My Presets"
    # Note the trailing slash: Cortex Control sends it verbatim for factory
    # recalls, so a recall needs it. Pair this with ``is_factory=True``.
    #
    # Beware the asymmetry: the device reports this same folder's LISTING key
    # WITHOUT the trailing slash, so anything matching a folder key against this
    # value must normalize it (see QuadCortex.list_presets).
    FACTORY = "/opt/neuraldsp/Factory Library/"


class FootswitchMode(IntEnum):
    """What the footswitches do: the three base modes, as ``Mode.mode`` numbers them.

    A merged HYBRID slot is a different value entirely - see
    :func:`~pyquadcortex.enums.hybrid_mode`.
    """

    PRESET = 0
    SCENE = 1
    STOMP = 2


# Every HYBRID composite, mapped to (top row A-D, bottom row E-H).
#
# Read off the unit one value at a time: the MODE indicator names the pair with the
# TOP row first. The six ordered pairs occupy 3-8 in lexicographic order over
# PRESET, SCENE, STOMP - so 4 and 7 are the two arrangements of Preset/Stomp, which
# is what the manual's "tap the right edge of the HYBRID slot to swap the Modes rows"
# produces.
HYBRID_MODES = {
    3: (FootswitchMode.PRESET, FootswitchMode.SCENE),
    4: (FootswitchMode.PRESET, FootswitchMode.STOMP),
    5: (FootswitchMode.SCENE, FootswitchMode.PRESET),
    6: (FootswitchMode.SCENE, FootswitchMode.STOMP),
    7: (FootswitchMode.STOMP, FootswitchMode.PRESET),
    8: (FootswitchMode.STOMP, FootswitchMode.SCENE),
}

# The device ACCEPTS 9 and it is broken: the indicator reads "<blank> + Scene" and the
# footswitches stop working entirely. It is refused by set_mode_cycle() for that reason.
# 10 and above are rejected by the device itself.
BROKEN_MODE_VALUE = 9


def hybrid_mode(top, bottom) -> int:
    """The composite value that puts ``top`` on footswitches A-D and ``bottom`` on E-H.

    Both are :class:`FootswitchMode` members and must differ - a hybrid splits the two
    rows between two different modes::

        qc.set_mode_cycle([hybrid_mode(FootswitchMode.PRESET, FootswitchMode.STOMP)])

    All six combinations were confirmed on hardware by reading the unit's own MODE
    indicator.
    """
    top, bottom = FootswitchMode(top), FootswitchMode(bottom)
    if top == bottom:
        raise ValueError(
            f"a HYBRID splits the two footswitch rows between DIFFERENT modes; "
            f"{top.name} was given for both"
        )
    for value, pair in HYBRID_MODES.items():
        if pair == (top, bottom):
            return value
    raise ValueError(f"no composite value for {top.name} over {bottom.name}")


def describe_mode(value: int) -> str:
    """Name a ``Mode.mode`` value, base or hybrid, for logs and error messages."""
    if value in HYBRID_MODES:
        top, bottom = HYBRID_MODES[value]
        return f"HYBRID {top.name} (A-D) + {bottom.name} (E-H)"
    if value == BROKEN_MODE_VALUE:
        return f"{value} - INVALID, breaks the footswitches"
    try:
        return FootswitchMode(value).name
    except ValueError:
        return f"unknown mode value {value}"


class Footswitch(IntEnum):
    """The eight footswitches, as ``stomp_index`` numbers them.

    Confirmed against factory content: "Darkglass AO900 2" assigns its row 0
    blocks to A-D and its row 2 blocks to E-H, in this order.
    """

    A = 0
    B = 1
    C = 2
    D = 3
    E = 4
    F = 5
    G = 6
    H = 7


class MidiSource(IntEnum):
    """What sends a per-preset MIDI Out message (``GeneralMIDIMessage.source``).

    Ten sources, which is why the preset's ``midi_messages_general_v2`` holds
    120 slots: 10 sources x 12 messages, source N starting at slot ``N*12``.
    Confirmed by writing distinct messages to sources 0, 1, 2, 7, 8 and 9 and
    reading back slots 0, 12/13, 24, 84, 96 and 108.

    Footswitches A-H are 0-7, and 8 and 9 are the two expression pedals: a
    message assigned to Expression 1 on the unit landed in slot 96, which is
    source 8.
    """

    FOOTSWITCH_A = 0
    FOOTSWITCH_B = 1
    FOOTSWITCH_C = 2
    FOOTSWITCH_D = 3
    FOOTSWITCH_E = 4
    FOOTSWITCH_F = 5
    FOOTSWITCH_G = 6
    FOOTSWITCH_H = 7
    EXPRESSION_1 = 8
    EXPRESSION_2 = 9


class MidiOutType(IntEnum):
    """Type code of a per-preset MIDI Out message (``MidiMessageInfo.type``).

    Confirmed on hardware by entering each on the unit and reading the saved
    preset: CC stored ``type: 1``, CC Toggle ``type: 2``, PC ``type: 3``.
    """

    CC = 1
    CC_TOGGLE = 2
    PC = 3


class SceneBypassBehavior(IntEnum):
    """How block bypass changes are kept when working with scenes.

    This is a GLOBAL device setting, and it changes what
    :meth:`~pyquadcortex.QuadCortex.set_bypass` actually persists - worth reading
    before concluding a bypass write failed. The three values are the manual's
    three choices, in its order.
    """

    #: Every bypass change is saved per scene. The device's default.
    ALWAYS_OVERWRITE = 0
    #: Footswitch presses in STOMP mode are not saved; touchscreen edits are.
    NONSTOMP_OVERWRITE = 1
    #: No bypass change is saved, by any method.
    NEVER_OVERWRITE = 2


class ExpressionBypassMode(IntEnum):
    """How an expression pedal bypasses a block (``expression_bypass_info.type``).

    All three confirmed, by setting each one deliberately on the unit with a scene
    change fencing them apart so the value landed on in each window was
    unambiguous: Heel-Toe stored 2, Switch 1, Stop 0.

    Note this is NOT the manual's listed order, and the unit's SWITCH ON control
    cycles numerically - from Heel-Toe (2) a press gives Stop (0), then Switch (1),
    then Heel-Toe again, which is what an earlier session had reported as its cycle.
    """

    STOP = 0
    SWITCH = 1
    HEEL_TOE = 2


class LooperState(IntEnum):
    """What the Looper X is doing (``LooperStatus.state``).

    Mapped by watching the owner press each transport control in a known order:
    RECORD, PLAY/STOP, REVERSE, HALF SPEED, UNDO. The device reported 5 while
    waiting for a signal, 4 once recording actually began, 2 on play, and 1 after
    the undo removed the loop. REVERSE and HALF SPEED did not change the state at
    all - they set ``in_reverse`` and ``half_speed`` while playback continued.

    ``3`` has never been observed and is deliberately absent. Overdub was the
    obvious guess for it and turned out to be 6, so there is no reason to assume
    what 3 might be.
    """

    #: Stopped, with no loop playing.
    IDLE = 1
    #: Playing back.
    PLAYING = 2
    #: Recording.
    RECORDING = 4
    #: Armed, waiting for an input signal to cross the threshold. The Looper sits
    #: here indefinitely with nothing plugged in, and the other controls stay inert.
    ARMED = 5
    #: Overdubbing. Observed by pressing OVERDUB during playback and again to leave
    #: it, which returned to PLAYING.
    OVERDUBBING = 6


class GlobalEQFilter(IntEnum):
    """Filter shape of a Global EQ band, as an option index.

    The control is a five-option list, so its wire value is ``index / 4``:
    :func:`pyquadcortex.option_value` does that, and
    :meth:`~pyquadcortex.QuadCortex.set_global_eq` takes this enum directly.

    Mapped by cycling the control through every shape on the unit with the values
    read off the wire, and confirmed independently by the shipped defaults: a
    factory-fresh Global EQ reads Lo Shelf on band 1, Peak on bands 2 to 4 and Hi
    Shelf on band 5 - the canonical layout for a five-band parametric EQ.
    """

    PEAK = 0
    HIGH_PASS = 1
    LOW_PASS = 2
    HIGH_SHELF = 3
    LOW_SHELF = 4


class TempoSubdivision(IntEnum):
    """Metronome SUBDIVISIONS - the rhythmic pulses per beat.

    Read off the unit's own list, top to bottom, and the ordering confirmed by
    selecting the last entry and seeing the wire store exactly 1.0. Four options,
    so the wire value is ``option / 3``.
    """

    QUARTER = 0            #: "1/4"
    EIGHTH = 1             #: "1/8"
    EIGHTH_TRIPLET = 2     #: "1/8T"
    SIXTEENTH = 3          #: "1/16"


class MetronomeSound(IntEnum):
    """Metronome SOUND. Six options, so the wire value is ``option / 5``."""

    BLIP = 0
    BLOCK = 1
    COWBELL = 2
    DIGITAL = 3
    DRUM_KIT = 4
    SOFT_KIT = 5


class MetronomeRouting(IntEnum):
    """Where metronome playback is sent. Five options, wire value ``option / 4``.

    Note MULTI is FIRST: an earlier guess that option 0 was the headphones came from
    assuming an operator's starting point matched the factory default, and was wrong.
    """

    MULTI = 0          #: "MULTI"
    HEADPHONES = 1     #: "HP"
    OUT_1_2 = 2        #: "OUT 1/2"
    OUT_3_4 = 3        #: "OUT 3/4"
    SEND_1_2 = 4       #: "SEND 1/2"


class TimeSignature(IntEnum):
    """Metronome T/SIGNATURE. Twenty-one options, wire value ``option / 20``.

    The compound signatures carry their grouping, as the unit displays it - 5/8 and
    7/8 each appear more than once with different accent patterns.

    Changing this also rewrites some of the ``STEPSTATE`` parameters, which hold the
    per-beat accents.
    """

    TWO_FOUR = 0
    THREE_FOUR = 1
    FOUR_FOUR = 2
    FIVE_FOUR = 3
    SIX_FOUR = 4
    SEVEN_FOUR = 5
    EIGHT_FOUR = 6
    NINE_FOUR = 7
    TEN_FOUR = 8
    ELEVEN_FOUR = 9
    TWELVE_FOUR = 10
    THIRTEEN_FOUR = 11
    THREE_EIGHT = 12
    SIX_EIGHT = 13
    NINE_EIGHT = 14
    TWELVE_EIGHT = 15
    FIVE_EIGHT_3_2 = 16    #: "5/8 (3+2)"
    FIVE_EIGHT_2_3 = 17    #: "5/8 (2+3)"
    SEVEN_EIGHT_3_2_2 = 18  #: "7/8 (3+2+2)"
    SEVEN_EIGHT_2_3_2 = 19  #: "7/8 (2+3+2)"
    SEVEN_EIGHT_2_2_3 = 20  #: "7/8 (2+2+3)"
