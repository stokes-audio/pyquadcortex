"""Calls a type checker MUST reject, and calls it must accept.

Not run. `tests/test_typing.py` puts mypy over this file and holds its output
against the `# want:` markers below, so a check that stops catching something
fails the suite rather than going quiet.

Every rejected line is a real mistake somebody could make: the unit types exist
because `real=-3.1` was dB on an EQ band and milliseconds on a delay.
"""

from pyquadcortex.protocol import params
from pyquadcortex.protocol.client import QuadCortex
from pyquadcortex.protocol.targets import Block, LaneInput, LaneOutput, Tempo
from pyquadcortex.protocol.values import (Bpm, Db, Encoded, Hertz,
                                          Milliseconds, Percent, Real)

qc: QuadCortex
block: Block

# -- accepted: the unit the catalog publishes for that parameter --------------
qc.set_param(LaneOutput(0), params.LaneOutputParam.VOLUME, Db(-3.1))
qc.set_param(LaneInput(0), params.LaneInputParam.INPUT_GAIN, Db(12.0))
qc.set_param(Tempo(), params.TempoParam.TEMPO, Bpm(120))
qc.set_param(block, params.Cabsim.HPF, Hertz(80.0))

# -- accepted: the two that claim nothing, so they fit anywhere ---------------
qc.set_param(LaneOutput(0), params.LaneOutputParam.VOLUME, Real(-3.1))
qc.set_param(LaneOutput(0), params.LaneOutputParam.VOLUME, Encoded(0.5))
qc.set_param(block, params.Cabsim.MIC_1_DISTANCE, Real(3.0))
qc.set_param(block, params.Cabsim.MIC_1_DISTANCE, Encoded(0.3))

# -- accepted: a constant is still an int, so nothing else got harder ---------
index: int = params.LaneOutputParam.VOLUME
qc.set_param(LaneOutput(0), 0, Encoded(0.5))
qc.set_param(LaneOutput(0), "VOLUME", Db(-3.1))

# -- rejected: the wrong unit -------------------------------------------------
qc.set_param(LaneOutput(0), params.LaneOutputParam.VOLUME, Hertz(217))  # want: error
qc.set_param(LaneInput(0), params.LaneInputParam.INPUT_GAIN, Percent(50))  # want: error
qc.set_param(Tempo(), params.TempoParam.TEMPO, Milliseconds(120))  # want: error
qc.set_param(block, params.Cabsim.HPF, Db(80.0))  # want: error

# -- rejected: a unit on a parameter the catalog says has none ----------------
qc.set_param(LaneOutput(0), params.LaneOutputParam.PAN, Db(0.5))  # want: error
qc.set_param(block, params.Cabsim.MIC_1_DISTANCE, Db(3.0))  # want: error

# -- rejected: a bare number, which the runtime refuses too -------------------
qc.set_param(LaneOutput(0), params.LaneOutputParam.VOLUME, -3.1)  # want: error
