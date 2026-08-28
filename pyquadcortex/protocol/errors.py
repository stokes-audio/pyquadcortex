"""What the protocol layer raises.

Small on purpose, and separate from ``client.py`` for one structural reason:
``targets.py`` decides whether a control can be driven, and ``client.py`` uses
``targets.py``. If the exception lived in the client the two would import each
other. It is also where a reader looks for it.

``DeviceLostError`` is NOT here - it belongs to the transport, which raises it,
and the model re-raises that one rather than inventing a second name for the
device going away.
"""


class BlockRefused(RuntimeError):
    """The device did not accept a block placement.

    Raised by :meth:`~pyquadcortex.protocol.client.QuadCortex.set_block` when no echo
    confirms the cell AND reading the preset back shows the cell does not hold
    the model. Both halves matter: a missing echo alone is not a refusal, and
    treating it as one produced FALSE NEGATIVES twice in one session on blocks
    that had landed.

    TWO causes are known, and for a long time this said one.

    * The preset has no DSP capacity left for that model.
    * A PORT CONFLICT. Placing an FX Loop beside a Send that competes for the
      same physical send is refused, and the unit puts a modal on its own screen
      that the host never sees - so from here it looks identical to running out
      of capacity, and it stays refused until somebody dismisses it ON THE UNIT.
      Observed 2026-08-26: "Port Conflict / Send is used as an output by FX Loop
      Send 1 on path 2, please change it first", stacked four deep.
    """


class ControlNotDrivable(ValueError):
    """A request the library will not answer, because answering means guessing.

    ADR-0007 decided that such a control is REPRESENTED and REFUSES rather than
    being omitted or guessed at, and left one question open: how the refusal
    reads in practice, to be settled by the first control that ships one. That
    was the Lane Output Control's MUTE and SOLO, where the DEVICE is what
    refuses - it silently drops a host write.

    ADR-0017 added a second shape, and the class name reads a little narrow for
    it, which is worth saying rather than papering over. There the host CAN
    drive the control - the master volume, an output port's level - and what is
    missing is the SCALE: nobody has read that control's screen against its
    wire value, so a value in dB cannot be converted without inventing a span.
    The remedy differs accordingly, and `workaround` is the field that says
    which case you are in: "do it on the unit" for the first, "pass Encoded(),
    or measure the scale" for the second. Branch on `workaround`, not on the
    class name.

    It subclasses ``ValueError`` because it is raised on an argument the caller
    chose, so ``except ValueError`` around a rig-building script keeps working.
    Catching this instead tells you the specific thing: the request was well
    formed and the DEVICE is what refuses.

    The three attributes exist so a caller can branch rather than parse a
    string - a script assigning pedals across a rig wants to skip the refused
    ones and report them, not die on the first::

        try:
            qc.set_expression(LaneOutput(row), name, pedal=1)
        except ControlNotDrivable as refusal:
            print(f"{refusal.control}: {refusal.workaround}")

    ``control`` is what was addressed, ``evidence`` is why we believe it cannot
    be driven, and ``workaround`` is what to do instead. All three are required:
    a refusal with no evidence is the guess this ADR exists to prevent.
    """

    def __init__(self, control: str, evidence: str, workaround: str):
        super().__init__(f"{control}: {evidence} {workaround}")
        self.control = control
        self.evidence = evidence
        self.workaround = workaround
