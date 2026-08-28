"""The value types against the real unit, one write per type.

Every conversion here is exercised offline in `tests/test_values.py`. What this
adds is that the unit AGREES: a value written in the parameter's own units comes
back as the same value, through a fresh connection.

The reconnect is not ceremony. A read straight after a write returns the
PREVIOUS value on this firmware, and that trap has produced two wrong
conclusions in this project - one of which stood as a documented fact for
several releases.

State-neutral per ADR-0005: every parameter touched is snapshotted and restored.
"""

import time

import pytest

from pyquadcortex.protocol import values
from pyquadcortex.protocol.targets import Block, LaneOutput, Tempo

#: Long enough for the unit to settle before a read-back.
SETTLE = 2.0

#: ``(label, target, parameter, value to write)`` - one per unit type the
#: catalog actually has on a control this suite can reach without placing a
#: block. Each is chosen away from the ends, so a clamp would show up.
WRITES = [
    ("dB, the lane volume", LaneOutput(1), "VOLUME", values.Db(-6.0)),
    ("bpm, the preset tempo", Tempo(), "TEMPO", values.Bpm(132)),
]


def _stored(qc, target, index):
    """What the preset currently holds for one parameter, on the wire."""
    preset = qc.read_current_preset()
    if isinstance(target, Tempo):
        entry = preset.tempoProgramData[0]
    else:
        entry = getattr(preset.chains[target.row], target.collection)[0]
    return entry.params[index].param_values[0].float_value


@pytest.mark.parametrize("label,target,param,written", WRITES,
                         ids=[w[0] for w in WRITES])
def test_the_unit_agrees_with_the_value_we_wrote(qc, restores, label, target,
                                                 param, written):
    index = qc.catalog[target.model_id].parameter(param).index
    spec = qc.catalog[target.model_id].parameters[index]
    before = _stored(qc, target, index)
    restores(f"{label} {param}",
             lambda: qc.set_param(target, index, values.Encoded(before)))

    qc.set_param(target, param, written)
    time.sleep(SETTLE)

    wire = _stored(qc, target, index)
    assert wire == pytest.approx(spec.to_normalized(float(written)), abs=1e-4), (
        f"{label}: wrote {written!r}, the unit stored {wire}")

    # And the read direction agrees, in the same units and the same type.
    back = spec.to_real(wire)
    assert type(back) is type(written), (
        f"{label}: wrote {type(written).__name__}, read back "
        f"{type(back).__name__}")
    assert float(back) == pytest.approx(float(written), abs=0.05)


def test_encoded_and_real_zero_land_in_different_places_on_the_unit(qc, restores):
    """The pair the whole design rests on, against the device rather than a fake.

    Real(0.0) is 0 dB - unity. Encoded(0.0) is the Off detent. If these ever
    landed in the same place the types would be decoration.
    """
    target, param = LaneOutput(1), "VOLUME"
    index = qc.catalog[target.model_id].parameter(param).index
    before = _stored(qc, target, index)
    restores("lane VOLUME for the zero comparison",
             lambda: qc.set_param(target, index, values.Encoded(before)))

    qc.set_param(target, param, values.Real(0.0))
    time.sleep(SETTLE)
    unity = _stored(qc, target, index)

    qc.set_param(target, param, values.Encoded(0.0))
    time.sleep(SETTLE)
    off = _stored(qc, target, index)

    assert unity == pytest.approx(0.76923, abs=1e-3), "Real(0.0) should be unity"
    assert off == pytest.approx(0.0, abs=1e-6), "Encoded(0.0) should be the detent"


def test_the_wrong_unit_never_reaches_the_unit(qc):
    """A read-only check: the refusal happens before anything is sent."""
    with pytest.raises(TypeError, match="dB"):
        qc.set_param(LaneOutput(1), "VOLUME", values.Hertz(217))
