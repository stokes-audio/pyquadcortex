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
