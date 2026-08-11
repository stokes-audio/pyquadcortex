"""Tests for the message registry (pyquadcortex.protocol.registry).

The registry bridges the frame codec (which deals in integer message-type tags)
and the generated protobuf message classes, mapping both directions.
"""

from pyquadcortex.protocol import registry
from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa


def test_maps_class_to_enum_and_back():
    assert registry.type_for(pa.RecallPresetMessage) == pa.CortexMessageType.Enum.Value("RecallPreset")
    assert registry.class_for(pa.CortexMessageType.Enum.Value("Scene")) is pa.SceneMessage
    assert registry.class_for(pa.CortexMessageType.Enum.Value("Version")) is pa.VersionMessage
