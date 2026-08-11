"""Tests for the qcctl command-line interface (pyquadcortex.protocol.cli).

These tests exercise ONLY argument parsing. They must never touch hidapi or open
a device: importing ``pyquadcortex.protocol.cli`` and calling ``build_parser()`` has to be
import-safe and device-free (device opening is deferred to
``pyquadcortex.protocol.session``, whose ``import hid`` is itself lazy). If any of these
tests start requiring hidapi, the lazy-import contract has been broken.
"""

import pytest

from pyquadcortex.protocol import cli, client
from pyquadcortex.protocol.enums import Setlist


def test_parse_recall_args():
    ns = cli.build_parser().parse_args(["recall", "--slot", "28C"])
    assert ns.command == "recall" and ns.slot == "28C"
    # Setlist defaults to the confirmed user "My Presets" device path.
    assert ns.setlist == Setlist.USER


def test_parse_recall_custom_setlist():
    ns = cli.build_parser().parse_args(
        ["recall", "--setlist", "/media/p4/Presets/Gig", "--slot", "1A"]
    )
    assert ns.setlist == "/media/p4/Presets/Gig" and ns.slot == "1A"


def test_slot_to_position():
    # CONFIRMED mapping from the Windows capture: 28C -> 218, 28E -> 220.
    assert client.slot_to_position("28C") == 218
    assert client.slot_to_position("28E") == 220
    assert client.slot_to_position("1A") == 0
    assert client.slot_to_position("32H") == 255
    for bad in ("", "C", "28I", "0A", "28"):
        with pytest.raises(ValueError):
            client.slot_to_position(bad)


def test_parse_scene_args():
    ns = cli.build_parser().parse_args(["scene", "--index", "3"])
    assert ns.command == "scene" and ns.index == 3


def test_parse_version_args():
    ns = cli.build_parser().parse_args(["version"])
    assert ns.command == "version"


def test_parse_dump_preset_args():
    ns = cli.build_parser().parse_args(["dump-preset", "--slot", "28C"])
    assert ns.command == "dump-preset" and ns.slot == "28C"
    assert ns.setlist == Setlist.USER


def test_missing_subcommand_exits_nonzero():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([])
