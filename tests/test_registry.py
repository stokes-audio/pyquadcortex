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


def test_coros_4_1_types_are_registered_at_their_recovered_values():
    assert pa.CortexMessageType.ModelPreset == 71
    assert registry.type_for(pa.ModelPresetMessage) == 71
    assert registry.class_for(71) is pa.ModelPresetMessage
    assert pa.CortexMessageType.RemoteControl == 72
    assert registry.type_for(pa.RemoteControlMessage) == 72
    assert registry.class_for(72) is pa.RemoteControlMessage
