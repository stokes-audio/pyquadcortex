"""High-level QuadCortex client for pyquadcortex.protocol.

This is the ergonomic API a caller (CLI, script) uses to control a Quad Cortex.
It builds protobuf messages and hands them to a ``transport``-like object,
which is dependency-injected via the constructor. The transport exposes:

  * ``send(message)``                  - fire-and-forget
  * ``send_sequence(messages, ...)``   - an atomic, timed message sequence
  * ``request(message, timeout=...)``  - send and block for the correlated reply

The client deliberately knows NOTHING about hidapi, HID reports, or the framing
layer: it only speaks protobuf messages. That keeps this layer testable with a
fake transport and keeps all wire concerns in ``framing``/``transport``.

**Rows and columns are ZERO-BASED; the unit displays rows 1 to 4.** So ``row=0``
is the top row on screen, and ``row=2`` is the one labelled 3. Getting this wrong
is quiet rather than loud - an edit lands on a real row, just not the one intended,
and it reads back perfectly. When a change is meant to be audible, check which row
actually reaches a physical output: ``chain.out_portid`` values 16 to 18 are internal
row-to-row routing rather than jacks, though 19 (``MULTIPLE``) is a real destination.

Field semantics were confirmed against real Cortex Control 4.0.1 sessions:
session connect, preset recall (user AND factory setlists), scene switch, grid
bypass/param writes, Save As, delete, and move are all reproduced verbatim from
observed traffic. ``copy_scene`` came from a different source, because Cortex
Control has no scene-copy feature to observe: its shape was read off the device's
own broadcast when a scene was copied on the unit. It too is fully confirmed on
hardware, including its ``from_index`` and ``swap`` behaviour. See
``docs/protocol.md`` for the per-operation coverage table.
"""

import math
import time
import typing
import uuid
import warnings
from typing import NamedTuple

from pyquadcortex.protocol import catalog, enums, registry, targets
from pyquadcortex.protocol import options as options_module
from pyquadcortex.protocol import values as values_module
from pyquadcortex.protocol import units as units_module
from pyquadcortex.protocol.enums import (Footswitch, Input, Instrument, Scene,  # noqa: F401
                                MetronomeBeat, MetronomeRouting,
                                MetronomeSound, MidiOutType,
                                MidiSource, Output, SceneBypassBehavior, Setlist,
                                TempoMode, TempoSubdivision, TimeSignature)
from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa
from pyquadcortex.protocol.proto import Preset_pb2 as preset

from pyquadcortex.protocol.errors import (BlockRefused,  # noqa: F401
                                          ControlNotDrivable)
from pyquadcortex.protocol.targets import (  # noqa: F401
    LANE_OUTPUT_UNASSIGNABLE, Block, LaneInput, LaneOutput, Mixer, ParamTarget,
    Splitter, Tempo, _require_even_row)
from pyquadcortex.protocol.units import (UNITY_LEVEL, bpm_to_tempo,  # noqa: F401
                                         db_to_input_level, db_to_lane_level,
                                         input_level_db, lane_level_db, tempo_bpm)




def _mode_param(message, index: int):
    """The float at tempo parameter ``index`` in a ``GlobalTempo``, or ``None``.

    ``None`` means "this message does not answer the question" - the clock shape,
    a params list too short, a param carrying no value, or a value stored as
    something other than a float. It is deliberately not an exception: this runs
    as a match predicate, where the right response to a message that cannot
    answer is to keep waiting for one that can.

    Two things here are not decoration.

    **The explicit ``index`` wins over position.** ``Param.index`` is
    presence-tracked, the device sets it on every param of a captured push
    (checked: all 25, and there it equals position), and this library's own writes
    set it. Reading positionally while the device keys by index is the
    ``ColBypass.column`` mistake in a new place - it works until the device sends
    a sparse or reordered list, and then it returns a neighbouring tempo
    parameter as the answer. The neighbours are 0.0/1.0 floats too, so the wrong
    answer would round cleanly and look right. Same fallback as
    ``set_block.echoes_cell``: trust the index when present, use position when not.

    **``ParamValue.value`` is a REAL oneof**, not a synthetic one - ``int_value``,
    ``float_value``, ``string_value``. Reading ``.float_value`` off a param that
    holds an int yields 0.0 with no error, which would report PRESET with total
    confidence. ``tempo_params()`` already guards this; so does this.
    """
    by_index = None
    for position, param in enumerate(message.params):
        at = param.index if field_present(param, "index") else position
        if at == index:
            by_index = param
            break
    if by_index is None or not by_index.param_values:
        return None
    first = by_index.param_values[0]
    if not field_present(first, "float_value"):
        return None
    return first.float_value


#: Where user setlists live. They sit SIDE BY SIDE here rather than nested inside
#: "My Presets" - a folder created under My Presets is not a setlist and the device
#: ignores it. :meth:`QuadCortex.create_setlist` builds a key from this.
USER_SETLIST_ROOT = "/media/p4/Presets"

#: How the unit stores "this scene has no label": a single space, not an empty
#: string. So ``label.strip()`` detects a blank scene and ``label == ""`` does not.
#: :meth:`QuadCortex.set_scene_label` sends this when given ``None``.
SCENE_UNLABELLED = " "


# -- typed values for the SETTINGS writes -------------------------------------
#
# `set_param` addresses a knob the catalog describes, so the scale comes from
# there. Nothing below is a catalog model: an input port, the Global EQ, the
# master volume and the USB port are all settings the catalog never mentions.
# ADR-0016's rule still applies to them - a value says which line it is on -
# but the scale has to come from somewhere else, and for most of them it does
# not exist yet. Three cases, and the difference is worth keeping visible:
#
#   * a wire 0..1 whose real scale IS known - `_INPUT_GAIN`, `_GLOBAL_EQ_GAIN`;
#   * a wire 0..1 whose real scale is NOT known - `Encoded` only, and the
#     refusal says what would have to be measured;
#   * a setting with no 0..1 line at all, where the wire carries the real
#     number - the hold threshold in ms, the tuner offset in Hz. `Encoded` is
#     meaningless there and is refused.

def _setting_scale(name: str, span_key: str, unit: str):
    """A `Parameter` for a scale the device has and its catalog does not publish.

    A real `Parameter` rather than a private conversion, so these get the ONE
    law from ADR-0015, the unit check, and the out-of-range refusal, instead of
    a second implementation that could drift from all three.
    """
    low, high = units_module.SETTING_SPANS[span_key]
    return catalog.Parameter(index=-1, name=name, minimum=low, maximum=high,
                             default=0.0, units=unit, type="float")


#: -12..+60 dB, measured. See ``units.SETTING_SPANS``.
_INPUT_GAIN = _setting_scale("an input port's GAIN", "INPUT_GAIN_DB", "dB")

#: -12..+12 dB, the MANUAL's span on two points. See ``units.SETTING_SPANS``.
_GLOBAL_EQ_GAIN = _setting_scale("a Global EQ band's GAIN",
                                 "GLOBAL_EQ_GAIN_DB", "dB")


def _bare_number(value, what, *, encoded=True, unit_example=None):
    """The message for a bare number, rewritten for the setting in hand."""
    lines = [f"{what} needs a value that says which scale it is on; you passed "
             f"{value!r}."]
    if unit_example:
        lines.append(f"  {unit_example}   the screen's line")
    if encoded:
        # NEVER offer `Encoded(value)` for a number the device's line cannot
        # hold. The first version did, and on the master volume - where the
        # screen shows 0-100 and the wire takes 0..1 - it answered
        # `set_master_volume(30)` with "Encoded(30)  the device's line, 0.0 to
        # 1.0", which contradicts itself and points at the dangerous number.
        if 0.0 <= float(value) <= 1.0:
            lines.append(f"  Encoded({value!r})  the device's line, 0.0 to 1.0")
        else:
            lines.append(f"  Encoded(...)  the device's line, 0.0 to 1.0 - "
                         f"which {value!r} is outside")
    lines.append("See docs/api.md, 'the two number lines'.")
    return TypeError("\n".join(lines))


def _setting_wire(value, what, scale=None, unit_example=None):
    """A typed value for a setting -> the wire's 0..1.

    ``scale`` is the real scale where one is known, and ``None`` where nobody
    has measured it - in which case a real value is REFUSED rather than
    converted against a number somebody made up. That is ADR-0007's shape:
    modelled, and refuses out loud.
    """
    if isinstance(value, values_module.Encoded):
        if not 0.0 <= float(value) <= 1.0:
            raise ValueError(
                f"the device's scale is 0.0 to 1.0 and {value!r} is outside it. "
                f"If that number is what the screen shows, it is not Encoded."
            )
        return float(value)
    if isinstance(value, values_module.Real):
        if scale is None:
            raise ControlNotDrivable(
                control=what,
                evidence=(
                    "nothing in the device's catalog describes this setting, "
                    "and nobody has read its screen against its wire value - so "
                    "there is no span to convert against. Inventing one is the "
                    "guess ADR-0015 exists to prevent."
                ),
                workaround=(
                    f"pass Encoded(...) with the device's own 0..1, or measure "
                    f"the scale and add it to units.SETTING_SPANS"
                ),
            )
        value.check_unit(scale)
        return scale.to_normalized(float(value))
    raise _bare_number(value, what, unit_example=unit_example)


def _setting_real(value, what, unit_cls):
    """A setting whose only number line is the screen's.

    The hold threshold is milliseconds and the tuner offset is Hz - the wire
    carries the real number (or an index derived from it), not a 0..1 position.
    `Encoded` has nothing to mean here and says so rather than being quietly
    accepted.
    """
    if isinstance(value, values_module.Encoded):
        raise TypeError(
            f"{what} has no 0..1 device scale, so Encoded({float(value)!r}) "
            f"cannot say anything about it - the wire carries the real number "
            f"itself. Pass {unit_cls.__name__}({float(value)!r})."
        )
    if isinstance(value, values_module.Real):
        value.check_unit(catalog.Parameter(
            index=-1, name=what, minimum=None, maximum=None, default=0.0,
            units=next(iter(sorted(unit_cls.CATALOG_UNITS))), type="float"))
        return float(value)
    raise _bare_number(value, what, encoded=False,
                       unit_example=f"{unit_cls.__name__}({value!r})")


def _sweep_wire(value, target, index, spec, get_catalog, what):
    """A sweep endpoint -> the wire's 0..1.

    An expression sweep runs between two POSITIONS of the parameter it is
    assigned to, so its scale is that parameter's and the catalog already knows
    it. This is `set_param`'s numeric dispatch over again for that reason - the
    same spec, the same unit check, the same one law - rather than a private
    conversion that could disagree with what a write to the same knob does.
    """
    if isinstance(value, values_module.Encoded):
        if not 0.0 <= float(value) <= 1.0:
            raise ValueError(
                f"the device's scale is 0.0 to 1.0 and {value!r} is outside it. "
                f"If that number is in the parameter's own units, it is "
                f"Real({float(value)!r}) rather than Encoded."
            )
        return float(value)
    if isinstance(value, values_module.Real):
        if spec is None:
            spec = target.spec_at(index, get_catalog)
        if spec is not None:
            _reject_number_on_a_string_parameter(spec, value)
            value.check_unit(spec)
        return target.normalize(index, float(value), get_catalog, spec)
    # `Real` first, like `set_param`'s own message: 1,780 catalog parameters
    # are unitless, and suggesting `Db` for one of those sends the caller into
    # a second refusal from `check_unit`.
    raise _bare_number(value, what, unit_example=f"Real({value!r})")


class QuadCortex:
    """Ergonomic control surface over a request/response transport."""

    def __init__(self, transport, _owned_resources=None):
        self._t = transport
        # Set by pyquadcortex.protocol.connect() so close() can tear down the transport
        # and HID device it opened on the caller's behalf. When a caller wires
        # their own transport, they own its lifecycle and this stays empty.
        self._owned = _owned_resources or []
        # Populated on first use of .catalog (a ~47 KB fetch from the device).
        self._catalog = None
        # CorOS ignores mouse messages until the first screenshot READ has
        # initialized the remote-control surface for this connection.
        self._remote_control_ready = False

    # -- catalog -------------------------------------------------------------

    @property
    def catalog(self):
        """This unit's :class:`~pyquadcortex.protocol.catalog.ModelCatalog`, fetched once.

        Every block on the grid is stored as an integer model id; the catalog is
        what turns that into a name, a category, and the parameter list in wire
        index order. It comes FROM the device, so it covers whatever this unit
        actually has - purchased plugin models and the player's own Neural
        Captures included - which no hard-coded table could know.

        Fetched lazily (a ~47 KB transfer) and cached for the session.
        """
        if self._catalog is None:
            self._catalog = catalog.parse_model_repo(self._fetch_model_repo())
        return self._catalog

    def _fetch_model_repo(self, timeout: float = 25.0) -> bytes:
        """Ask the device for its ModelRepo payload and return the raw bytes."""
        message = self._t.await_broadcast(
            pa.ModelRepoMessage,
            lambda: self._t.send(pa.ModelRepoMessage(action=pa.MessageAction.READ)),
            timeout=timeout,
            match=lambda m: bool(m.model_repo_payload),
        )
        return message.model_repo_payload

    # -- lifecycle -----------------------------------------------------------

    def disconnect(self):
        """Tell the device this client is going away.

        Sends ``Connection{connected: false}``, which is what Cortex Control does
        on quit. Without it the device is never told the client left - it simply
        stops receiving keepalives - and this library announced the connect but
        never the disconnect.

        Best effort: a failure here never prevents teardown. On this device that
        matters less than it sounds, because EVERY host write is reported as
        failing thanks to the deliberate status-stage STALL, so swallowing the
        error is the normal path rather than a special case.

        :func:`pyquadcortex.protocol.connect` calls this for you as the first step of
        :meth:`close`. It is public for callers who supplied their own transport
        and therefore own teardown themselves, who otherwise had no
        non-private way to send it.

        Whether an abandoned session leaks anything on the device is an open
        question - see ``docs/protocol.md`` section 4.3 for what was measured.
        """
        try:
            return self._t.send(pa.ConnectionMessage(connected=False))
        except Exception:  # pragma: no cover - the link may already be gone
            return None

    def close(self):
        """Release the device, if this object opened it.

        Safe to call more than once. A :class:`QuadCortex` built around a
        caller-supplied transport does not own it, so this is then a no-op.
        """
        while self._owned:
            closer = self._owned.pop()
            try:
                closer()
            except Exception:  # pragma: no cover - best-effort teardown
                pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    # -- live state pushes -----------------------------------------------------

    def add_listener(self, listener):
        """Call ``listener(message)`` for every message the device sends.

        A pass-through to
        :meth:`~pyquadcortex.protocol.transport.Transport.add_listener`, which is
        where the contract lives and is worth reading before you use this: the
        listener runs on the transport's RX thread, so it must not block, and it
        may not read from the device (the transport refuses that outright). It is
        removed with the returned callable or with :meth:`remove_listener`.

        This is how a long-lived caller sees the state the unit pushes without
        being asked - a touchscreen edit, a preset recall, the metronome's tempo
        stream - rather than the one-shot answer to a call it just made. To catch
        the connect handshake's own burst of state, register before the handshake
        with ``protocol.connect(before_handshake=...)``.
        """
        return self._t.add_listener(listener)

    def remove_listener(self, listener):
        """Stop calling ``listener``; return True if it had been registered.

        Never raises, so teardown can call it unconditionally.
        """
        return self._t.remove_listener(listener)

    # -- session -------------------------------------------------------------

    # State types the device only PUSHES to a client that has subscribed by
    # sending a READ for them during connect. Order/content mirror Cortex
    # Control's connect burst. RecallPreset is the one
    # that matters for read_preset, but the device appears to want the whole
    # set before it treats the client as fully connected.
    _SUBSCRIBE_TYPES = (
        "ModuleStats", "License", "UndoRedo", "IOSettings", "GeneralSettings",
        "ShowGigView", "Mode", "GlobalEQ", "MasterVolume", "File",
        "RecentsFavorites", "CompilerInhibitedModules", "RecallPreset",
        "NewModels", "PinnedModels", "DefaultParameters", "GlobalTempo",
        "SetlistPosition", "PresetDirty", "Scene", "BulkOperation", "Updater",
    )

    # Cortex Control version string the host announces. The device gates state
    # PUSH behaviour on receiving a valid cortex_control_version (see hello);
    # this is the version captured on the wire.
    CC_VERSION = "4.0.1"

    def _hello(self, timeout: float = 5.0, settle: float = 2.0):
        """Perform the full connect handshake Cortex Control performs.

        Internal: :func:`pyquadcortex.protocol.connect` calls this for you, so a caller
        never has to. It is only separate so the handshake can be tested and so
        an advanced caller wiring their own transport can still drive it.

        Confirmed by capture and live probe: the device
        will not push state (no RecallPreset preset dumps, no Grid/Scene sync)
        to a client that has only opened the pipe - a minimal
        ResetCommsBuffers+Connection is NOT enough (proven live: recalls
        produced zero device traffic until the full burst below was sent).
        The working sequence is:

          1. ``ResetCommsBuffers`` with a fresh 32-hex ``session_id`` (echoed).
          2. ``Version`` READ, then a ``Version`` UPDATE announcing
             ``cortex_control_version`` (the device gates push behaviour on a
             valid CC version).
          3. ``Connection{connected: true}``.
          4. A READ for each state type in ``_SUBSCRIBE_TYPES`` - this is the
             subscription that makes the device start pushing that state.

        Returns the echoed ResetCommsBuffers reply. After this, ``read_preset``
        and the device's live-sync pushes work.
        """
        reply = self._t.request(
            pa.ResetCommsBuffersMessage(session_id=uuid.uuid4().hex), timeout=timeout
        )
        # Announce our (Cortex Control) version - the device gates push
        # behaviour on a valid cortex_control_version. We do NOT also issue a
        # Version READ here: a redundant host READ would race with a caller's
        # later version request, since READ replies carry no request_id to
        # disambiguate and _dispatch gives an id-less reply to whichever waiter
        # is first in line.
        #
        # Skipping it costs nothing and quietens the link: the device's own
        # Version READ is the tail of its answer to a host Version READ, so with
        # none sent here it asks nothing back. Measured 2026-08-27 on d14e - one
        # inbound Version through connect and the whole burst, an UPDATE
        # carrying cortex_control_version_valid in answer to this announce, and
        # none in the eight seconds of idling after it.
        self._t.send(
            pa.VersionMessage(
                action=pa.MessageAction.UPDATE, cortex_control_version=self.CC_VERSION
            )
        )
        self._t.send(pa.ModelRepoMessage(action=pa.MessageAction.READ))
        self._t.send(pa.ConnectionMessage(connected=True))
        for name in self._SUBSCRIBE_TYPES:
            self._t.send(registry.class_for(pa.CortexMessageType.Enum.Value(name))(
                action=pa.MessageAction.READ
            ))
        # The device needs a moment after the burst before it treats the client
        # as connected and starts pushing; a command sent too soon gets no push
        # (observed as flaky read_preset timeouts). Settle before returning so
        # callers can issue the first command immediately.
        time.sleep(settle)
        return reply

    # -- read ----------------------------------------------------------------

    def version(self, timeout: float = 10.0):
        """Read the device's version info.

        Returns the device's ``VersionMessage``, whose fields include
        ``app_fw_version`` (the firmware version), ``device_type``,
        ``device_serial_number``, and ``comms_version``.

        Works without the connect handshake, so it is a good first call to
        confirm the device is talking.
        """
        return self._t.request(
            pa.VersionMessage(action=pa.MessageAction.READ), timeout=timeout
        )

    def find_preset(self, name: str, setlist: str = Setlist.USER,
                    timeout: float = 25.0):
        """Look a preset up by the name shown on the unit.

        Returns its listing entry, whose ``index`` is the position the recall and
        read methods take::

            cali = qc.find_preset("Cali Basswalk", Setlist.FACTORY)
            preset = qc.read_preset(Setlist.FACTORY, cali.index)

        Matching is exact but case-insensitive. Raises ``KeyError`` if no preset
        of that name exists in the setlist.
        """
        wanted = name.strip().lower()
        entries = self.list_presets(setlist, timeout=timeout)
        for entry in entries:
            if entry.name.strip().lower() == wanted:
                return entry
        raise KeyError(f"no preset named {name!r} in {str(setlist)!r}")

    def read_preset(
        self, setlist_path: str, position, is_factory: bool | None = None,
        timeout: float = 40.0,
    ) -> preset.BinaryPreset:
        """Recall a preset and return its full ``BinaryPreset``.

        ``position`` is either the linear slot index or the slot name shown on
        the unit (``"28C"``); :meth:`find_preset` turns a preset name into one.
        ``is_factory`` is inferred from ``setlist_path``.

        Confirmed by capture and live probe: there is NO
        host-initiated "read preset" request - a ``GridMessage``/``RecallPreset``
        READ gets no reply. Instead the device BROADCASTS a ``RecallPreset``
        push (its ``preset`` field carrying the full BinaryPreset, often
        gzip-compressed - the transport decompresses it) whenever a preset is
        recalled, by host or by the unit. So this recalls the slot and captures
        that push. NOTE: this DOES load the preset onto the grid (it is not a
        side-effect-free read); the device services the push lazily (10-25s
        observed), hence the generous timeout.

        **The recall INTERRUPTS THE AUDIO - every time, including when it recalls
        the preset already loaded.** Measured by ear across four consecutive
        recalls: three redundant ones of the same preset and one genuine change,
        and all four cut the sound; only the duration differed (the real change
        was longer). Loading a preset reloads the engine, so this is expected
        device behaviour rather than a fault - but it means a verify-by-re-reading
        loop built on this method stutters a rig on EVERY iteration, even when
        nothing changes. It also resets the active scene and discards unsaved
        edits. Use :meth:`read_current_preset` for inspection: it reads the live
        grid with no side effects at all.

        Correlation, confirmed on hardware: the RecallPreset push a host
        recall triggers echoes that recall's ``request_id``, while the
        unsolicited seed push (hello's subscription grid state) carries none.
        Without matching on the id, the waiter returns whatever RecallPreset
        arrives first - which lags by one recall when a prior push is still in
        flight (the seed seeds the lag). So tag the recall with a fresh
        request_id and accept only the push echoing it.
        """
        rid = self._t.next_request_id()

        def trigger():
            self.recall_preset(setlist_path, position, is_factory, request_id=rid)

        push = self._t.await_broadcast(
            pa.RecallPresetMessage,
            trigger,
            timeout=timeout,
            match=lambda m: m.HasField("request_id") and m.request_id == rid,
        )
        return push.preset

    def list_presets(self, setlist: str = Setlist.USER, timeout: float = 25.0,
                     include_empty: bool = False) -> list:
        """List the presets in a setlist, in slot order.

        Each entry is a ``ProductData`` with ``index`` (the linear slot position,
        see :func:`slot_to_position`), ``name``, and ``instrument`` (see
        :class:`~pyquadcortex.protocol.enums.Instrument`).

        The device always reports a setlist as its full complement of 256 slots,
        most of which are typically empty. By default only occupied slots are
        returned; pass ``include_empty=True`` for the complete slot map, e.g. to
        find a free slot to save into.

        ``setlist`` is any folder KEY the device reports, not only the two
        setlists: plugin artist folders and the Captures Library work too, and
        :meth:`list_folders` enumerates all of them. Confirmed by listing
        ``"106_f"`` (three "Darkglass VMT" captures) and a plugin artist folder.

        Unlike :meth:`read_preset`, this does not change what is loaded on the
        grid. There is no host-initiated "list" request: a ``File`` READ makes the
        device push a folder listing per setlist, so this sends that READ and
        waits for the listing whose key matches ``setlist``.

        A listing that arrives is COMPLETE - five READs against an 18-preset setlist
        each produced a full listing, and no short one has been observed. But a READ
        does not reliably produce one promptly: two of those five saw nothing for
        that setlist within 8 s, delivery being lazy. So treat a timeout as "ask
        again", which is what :meth:`wait_for_listing` does, rather than as an
        answer about the setlist's contents.

        Note the trailing-slash asymmetry the match has to absorb: recalls need
        the factory path WITH its trailing slash (Cortex Control sends it that
        way), but the device reports that same folder's listing key WITHOUT one.
        Keys are therefore compared with trailing slashes normalized away.
        """
        wanted = str(setlist).rstrip("/")
        listing = self._t.await_broadcast(
            pa.FileMessage,
            lambda: self._t.send(pa.FileMessage(action=pa.MessageAction.READ)),
            timeout=timeout,
            match=lambda m: (
                m.folder.key.rstrip("/") == wanted and len(m.folder.files) > 0
            ),
        )
        entries = sorted(
            listing.folder.files,
            key=lambda pd: pd.index if pd.HasField("index") else -1,
        )
        if include_empty:
            return entries
        return [pd for pd in entries if pd.HasField("name") and pd.name]

    # -- navigation ----------------------------------------------------------

    def recall_preset(
        self,
        setlist_path: str,
        position,
        is_factory: bool | None = None,
        request_id: int | None = None,
    ):
        """Recall a preset within the setlist at ``setlist_path``.

        ``position`` is either the linear slot index or the slot name shown on
        the unit (``"28C"``). ``is_factory`` is inferred from ``setlist_path``
        and only needs passing for a setlist this library does not know about.

        Confirmed by capture: recall is a ``SetlistPositionMessage`` UPDATE. The
        setlist is addressed by its device filesystem path in ``folder_key`` and
        the preset by its LINEAR index in ``position``: bank*8 + letter,
        zero-based, so preset "28C" is ``(28-1)*8 + 2 == 218``. Cortex Control
        recalling 28C sent exactly ``{folder_key: "/media/p4/Presets/My
        Presets", position: 218, is_factory: false}``.
        """
        msg = pa.SetlistPositionMessage(action=pa.MessageAction.UPDATE)
        msg.folder_key = setlist_path
        msg.position = _as_position(position)
        msg.is_factory = _is_factory_setlist(setlist_path) if is_factory is None \
            else is_factory
        if request_id is not None:
            msg.request_id = request_id
        return self._t.send(msg)

    def switch_scene(self, scene: int):
        """Switch the active scene.

        Takes a :class:`~pyquadcortex.protocol.enums.Scene` (``Scene.B``); scenes are
        numbered from zero, so a bare integer works too.
        """
        msg = pa.SceneMessage(action=pa.MessageAction.UPDATE)
        msg.selected_scene = int(scene)
        return self._t.send(msg)

    def copy_scene(self, from_index: int, to_index: int, swap: bool = False):
        """Copy (or swap, when ``swap=True``) one scene onto another.

        Cortex Control has no scene-copy feature, so this message was not learned
        from its traffic. Instead, copying a scene ON THE UNIT broadcasts
        ``SceneCopy{action: UPDATE, to_index: N}`` (note the action is UPDATE,
        not COPY), and that is the shape sent here.

        Confirmed working host-to-device on hardware, ``from_index`` included:
        ``copy_scene(1, 3)`` on a preset whose scenes A and B differ made scene D
        an exact copy of scene B (not of scene A, which is what a device ignoring
        ``from_index`` would have produced).

        ``swap=True`` is also confirmed: it exchanges the two scenes rather than
        overwriting one, so scene B ends up holding scene D's former state and
        vice versa.

        The scene's LABEL and COLOUR both travel with its bypass and parameter
        state: a copy renames and recolours the destination scene, and a swap
        exchanges both. Confirmed on hardware with nothing else sent - ``copy_scene(
        Scene.E, Scene.B)`` on factory 28A moved 'Clean +VMT' and ``0xff45f862``
        onto scene B - and by performing the same copy on the unit. So reproducing a
        scene map needs no :meth:`set_scene_color` calls for the copied scenes.
        """
        return self._t.send(
            pa.SceneCopyMessage(
                action=pa.MessageAction.UPDATE,
                from_index=from_index,
                to_index=to_index,
                is_swap=swap,
            )
        )

    def set_scene_label(self, scene_index: int, label):
        """Rename a scene, or blank it with ``label=None``.

        Confirmed shape: ``SceneLabel{action: UPDATE, index, label}`` (observed as
        the device's broadcast when a scene was renamed on the unit).

        The unit stores an unlabelled scene as a single SPACE rather than an empty
        string - factory "Cali Basswalk" (27E) reads back ``" "`` for the four
        scenes it does not use - so ``None`` sends :data:`SCENE_UNLABELLED` to match
        what the unit itself writes, and a blank scene is detected with
        ``label.strip()`` rather than ``label == ""``.
        """
        return self._t.send(
            pa.SceneLabelMessage(
                action=pa.MessageAction.UPDATE,
                index=scene_index,
                label=SCENE_UNLABELLED if label is None else label,
            )
        )

    def set_scene_color(self, scene_index: int, color: int):
        """Recolor a scene. Confirmed shape: ``SceneColor{action:
        UPDATE, index, color}`` with ``color`` an ARGB uint32 (recoloring a
        scene pinkish on the unit broadcast 0xFFFF02C2)."""
        return self._t.send(
            pa.SceneColorMessage(
                action=pa.MessageAction.UPDATE, index=scene_index, color=color
            )
        )

    # -- grid write ----------------------------------------------------------

    def write_preset(self, p: preset.BinaryPreset):
        """Send ``p`` as a ``Grid`` UPDATE - the low-level grid-edit primitive.

        The device applies a Grid UPDATE by locating each chain/model by its
        ``row``/``column`` KEY (mirroring the captured param-change updates), so
        ``p`` must carry explicit ``row`` (and ``column`` for model edits) on the
        elements it changes. A sparse, correctly-keyed preset works; the
        convenience wrappers :meth:`set_chain_input`, :meth:`set_param`, and
        :meth:`set_bypass` build exactly such presets and are the usual entry
        points.

        WARNING, confirmed on hardware: a preset freshly read from a
        recall carries NO explicit
        ``row``, so writing it back WHOLESALE does nothing - a full-preset write
        that re-pointed ``in_portid`` read back UNCHANGED. Do not expect a
        recalled-then-mutated preset to persist via this method; use the keyed
        wrappers instead.
        """
        return self._t.send(pa.GridMessage(action=pa.MessageAction.UPDATE, preset=p))

    def set_chain_input(self, row: int, in_portid: int):
        """Re-point one grid ``row``'s input to ``in_portid`` (row-keyed update).

        Confirmed on hardware: a ``Grid`` UPDATE carrying a single chain
        ``{row, in_portid}`` re-points that grid row's input; the device then
        saves it with ``save_current_preset`` (which snapshots the grid). This
        is the ONLY shape that actually moved an input on the wire - a
        full-preset write whose chains lacked ``row`` did nothing. Verified live:
        recall factory D-Cell (row 0 = Input 1) -> ``set_chain_input(0, INPUT_2)``
        -> Save -> recall shows ``in_portid == INPUT_2``.
        """
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        chain = msg.preset.chains.add()
        chain.row = row
        chain.in_portid = in_portid
        return self._t.send(msg)

    def _get_catalog(self):
        """The catalog, fetched lazily - see `ParamTarget.model`."""
        return self.catalog

    # Three overloads, and the order and the value unions both matter.
    #
    # A `Param` IS an `int` - that is what keeps it free of a catalog fetch -
    # so an `int` overload accepting real values would swallow every wrong-unit
    # call before the checker could reject it. Overload 3 therefore takes only
    # what an index-addressed write actually needs, which is the device's own
    # scale, a string, or a switch.
    #
    # The cost, stated rather than discovered: `set_param(target, 21, Real(3))`
    # is a static error although it runs fine. Address by INDEX and say
    # `Encoded`; name the parameter, or use its generated constant, to write
    # real units. Three lines in this repository do the former, all in tests.
    @typing.overload
    def set_param(self, target, param: "values_module.Param[values_module.U]",
                  value: "values_module.Real[values_module.U] | "
                         "values_module.Encoded | str | bool | None" = None,
                  *, scene=None, promote: bool = True): ...

    @typing.overload
    def set_param(self, target, param: str,
                  value: "values_module.Real[typing.Any] | "
                         "values_module.Encoded | str | bool | None" = None,
                  *, scene=None, promote: bool = True): ...

    @typing.overload
    def set_param(self, target, param: int,
                  value: "values_module.Encoded | str | bool | None" = None,
                  *, scene=None, promote: bool = True): ...

    def set_param(self, target, param, value=None, *, scene=None,
                  promote: bool = True):
        """Set one parameter, wherever on the unit it lives.

        ``target`` says WHERE, and is one of the addresses in
        :mod:`pyquadcortex.protocol.targets`::

            qc.set_param(Block(0, 2, 1), "GAIN", Real(5.0))   # a Myth Drive
            qc.set_param(LaneOutput(0), "VOLUME", Db(-3.1))
            qc.set_param(LaneInput(0), "INPUT GAIN", Db(12.0))
            qc.set_param(Mixer(0), "LEVEL A", Db(0.0))
            qc.set_param(Splitter(0), "LEVEL TO B", Db(-27.0))
            qc.set_param(Tempo(), "TEMPO", Bpm(120))

        :func:`blocks` hands back :class:`~pyquadcortex.protocol.targets.Block` values
        with ``model_id`` already filled in, so reading a preset gives you
        addresses you can write to directly.

        Confirmed shape: a knob change streams ``Grid{UPDATE, preset{chains{row,
        <collection>{<key>, params{index, param_values[scene]{float_value}}}}}}``,
        where the collection and the key come from the target - a block keys by
        ``column``, the lane output, input gate and mixer by ``hash``, the
        splitter by neither, and tempo hangs off the preset rather than a chain.
        A keyed sparse update is the ONLY way an edit persists; a full-preset
        write is dropped. Save the grid afterwards to keep it.

        ``param`` is a NAME or a wire index. Naming is the safer route - indices
        are positional and not every one is a visible knob, so a cab's index 0 is
        an internal ``ir selector`` that changes stored data and moves nothing on
        screen. Naming a parameter on a :class:`Block` needs its ``model_id``,
        because what is in a cell is whatever the player put there; every other
        target knows its own model.

        **The value says which SCALE it is on**, because every knob has two:
        the one the screen shows and the device's own 0.0 to 1.0. See
        :mod:`pyquadcortex.protocol.values`.

        * :class:`~pyquadcortex.protocol.values.Real` and the unit types -
          ``Db``, ``Percent``, ``Hertz``, ``Milliseconds``, ``Seconds``,
          ``Semitones``, ``Cents``, ``Bpm`` - are the screen's scale. They
          convert through the catalog's own description of the parameter, so
          they apply any taper and REFUSE a value the knob has no position for
          rather than clamping. A unit type also checks itself against the
          catalog's unit. They need a catalog, which comes from the device.
        * :class:`~pyquadcortex.protocol.values.Encoded` is the device's scale.
          It converts nothing and needs no catalog, which is what keeps an
          index-addressed write free of a round trip.
        * A plain ``str`` writes a string-valued parameter, such as a cab's
          microphone.

        A bare number is refused: on a lane VOLUME ``Real(0.0)`` is unity and
        ``Encoded(0.0)`` is silence, and nothing in a plain ``0.0`` says which
        was meant.

        One parameter refuses a real value outright: ``NC_Recorder``'s ``OUT
        LEVEL``, whose bounds the catalog names and nobody can measure, because
        placing that block crashes the unit.

        **Per-scene values.** Name a ``scene`` to change that scene alone::

            # index 0 with no model id, so the catalog cannot be asked what
            # scale this knob is on - the device's own is all there is
            qc.set_param(Block(2, 5), 0, Encoded(0.8), scene=Scene.D)

        Three things had to line up for this, all confirmed on hardware:

        1. The device honours ``param_values[0]`` against whichever scene is
           **active** - the index is not a scene selector, so nothing is padded.
        2. It only keeps per-scene values for a parameter whose ``scene_mode`` is
           set, so naming a scene promotes the parameter first (pass
           ``promote=False`` to skip that if you know it is already set).
        3. The device accepts **either** the flag **or** a value in one message,
           never both: sent together, the flag is silently dropped. So this
           issues the promotion, the scene switch and the write as three
           messages, and leaves the unit sitting on that scene - a visible side
           effect.

        Without ``scene``, the write lands on the active scene, which for a
        parameter that is not scene-following is its single global value and so
        appears in all eight.
        """
        index, spec = target.index_of(param, self._get_catalog)

        if value is None:
            raise TypeError(
                "set_param needs a value that says which scale it is on. See "
                "docs/api.md, 'the two number lines'."
            )
        if isinstance(value, bool):
            # A bool is the natural way to write a two-option switch, and 247
            # parameters are exactly Off/On. It is NOT safe on anything else:
            # True is the wire's 1.0, which selects the LAST option of a longer
            # list and writes full scale on a continuous knob. So the parameter
            # has to be checked, and a bool we cannot check is refused rather
            # than guessed - `set_param(block, "GAIN", True)` meaning "enable"
            # is a plausible slip and it would have written maximum gain.
            #
            # The spec is often not in hand here: an indexed write never fetches
            # one, and `Tempo` maps its screen names straight to indexes.
            if spec is None:
                # KeyError alone: the catalog has no such model, so there is
                # genuinely nothing to check against and the refusal below is
                # the answer. Anything else - a read timeout, a closed
                # transport, an OSError out of hid, ADR-0009's refusal to read
                # on the RX thread - means we could not LOOK, which is a
                # different thing, and swallowing it wrote the value unchecked.
                try:
                    spec = target.spec_at(index, self._get_catalog)
                except KeyError:
                    spec = None
            if spec is None:
                raise TypeError(
                    f"True and False can only write a two-option switch, and "
                    f"this parameter is not one the catalog describes - so there "
                    f"is no way to check. Pass Encoded(0.0) or Encoded(1.0) if "
                    f"you meant the device's own scale, or connect so the "
                    f"catalog can be read."
                )
            if spec.option_count != 2:
                offers = (f"offers {spec.option_count} options"
                          if spec.option_count else "is not a list at all")
                raise TypeError(
                    f"{spec.name!r} {offers}, so True and False cannot say what "
                    f"you mean. True is the wire's 1.0, which on a list picks the "
                    f"LAST option and on a continuous knob writes the top of the "
                    f"range. Use an enum from pyquadcortex.protocol.options with "
                    f"set_param_option, or pass the number you want."
                )
            value = 1.0 if value else 0.0
        elif isinstance(value, str):
            # A string is itself - but check the parameter actually takes one.
            # Collapsing text= into the positional removed the caller's
            # declaration of intent, so a value that arrives as a string from
            # argv or JSON would otherwise take the string path onto a dB knob
            # and be sent. The catalog publishes type="string" for exactly the
            # 396 parameters that want it.
            if spec is None:
                # See the bool branch: only "the catalog does not describe this
                # model" is swallowed. A failure to CONSULT the catalog used to
                # become `spec = None`, and `spec is None` means "allow" here -
                # so a disconnected device turned a checked write into an
                # unchecked one.
                try:
                    spec = target.spec_at(index, self._get_catalog)
                except KeyError:
                    spec = None
            if spec is not None and spec.type != "string":
                raise TypeError(
                    f"{spec.name!r} is a {spec.type or 'plain'} parameter, not a "
                    f"string one, so {value!r} is not a value it can hold. If "
                    f"that string came from a file or a command line, convert it "
                    f"first and say which scale it is on - Real({value!r}) or "
                    f"Encoded({value!r})."
                )
        elif isinstance(value, values_module.Encoded):
            # The device's own scale, so there is nothing to convert and no
            # catalog to fetch. This is what keeps an index-addressed write free
            # of a round trip.
            #
            # 0..1 is the one invariant true of all 3,809 parameters, so it is
            # the only bound checkable without asking the device anything. NaN
            # fails this comparison too, which is deliberate: four factory
            # presets store NaN, and passing one through would write it.
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(
                    f"the device's scale is 0.0 to 1.0 and {value!r} is outside "
                    f"it. If that number is in the parameter's own units, it is "
                    f"Real({float(value)!r}) rather than Encoded."
                )
            # Only if a spec is already in hand - naming the parameter fetched
            # one. Fetching here on purpose does not happen: an index-addressed
            # `Encoded` write staying free of a round trip is this branch's
            # whole reason to exist.
            if spec is not None:
                _reject_number_on_a_string_parameter(spec, value)
            value = float(value)
        elif isinstance(value, values_module.Real):
            # `spec_at` is the one answer to "what sits at this wire index",
            # and the check and the conversion both read it. They used to
            # resolve it separately and disagree on a cab, where the model's own
            # entry is not wire-indexed at all.
            if spec is None:
                spec = target.spec_at(index, self._get_catalog)
            if spec is not None:
                _reject_number_on_a_string_parameter(spec, value)
                value.check_unit(spec)
            value = target.normalize(index, float(value), self._get_catalog, spec)
        else:
            raise TypeError(
                f"set_param needs a value that says which scale it is on; you "
                f"passed {value!r}. Every knob has two number lines - the "
                f"screen's and the device's - and a bare number cannot say "
                f"which one you mean.\n"
                f"  Real({value!r})     the screen's line, this knob's own units\n"
                f"  Db({value!r})       the same, and checked against the catalog\n"
                f"  Encoded({value!r})  the device's line, 0.0 to 1.0\n"
                f"See docs/api.md, 'the two number lines'."
            )
        if scene is not None:
            if not target.supports_scenes:
                raise TypeError(
                    f"{target.describe()} has no per-scene values - scenes live "
                    f"on the grid, and this is not on it"
                )
            if promote:
                self.set_param_scene_mode(target, index, True)
            self.switch_scene(scene)
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        prm = target.container(msg).params.add()
        prm.index = index
        if isinstance(value, str):
            prm.param_values.add().string_value = value
        else:
            prm.param_values.add().float_value = value
        return self._t.send(msg)

    def set_param_scene_mode(self, target, param, enabled: bool = True):
        """Make a parameter follow scenes, or stop it following them.

        A parameter only keeps per-scene values while ``scene_mode`` is set;
        until then it has one value shared by all eight scenes, and a per-scene
        write is not kept. :meth:`set_param` sequences this for you when you name
        a scene.

        **The flag must travel ALONE.** A message carrying both the flag and a
        value is treated as a plain value write and the flag is dropped -
        confirmed on hardware, and the reason this is a separate message rather
        than a field on the write.
        """
        index, _ = target.index_of(param, self._get_catalog)
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        prm = target.container(msg).params.add()
        prm.index = index
        prm.scene_mode = enabled
        return self._t.send(msg)

    def set_expression(self, target, param, pedal: int = 1,
                       minimum=values_module.Encoded(0.0),
                       maximum=values_module.Encoded(1.0)):
        """Assign an expression pedal to one parameter, wherever it lives.

        ``pedal`` is 1 or 2, matching EXP 1 and EXP 2 on the back panel.
        ``minimum`` and ``maximum`` are the two ends of the sweep, and they are
        POSITIONS OF THE PARAMETER being assigned - so they take the same typed
        values a write to that parameter takes, converted through the same
        catalog spec. Setting minimum above maximum reverses the pedal, which is
        how the manual describes inverting a parameter. The unit DISPLAYS them as
        percentages of travel - wire 0.830769 shows as 83.08% - which is a
        display, not a third scale.

        A volume pedal on a row that reaches a physical output, in dB rather
        than in wire values::

            # the heel is the Off detent, which sits BELOW the dB scale, so
            # the device's own 0.0 is the only thing that names it
            qc.set_expression(LaneOutput(0), "VOLUME", pedal=1,
                              minimum=Encoded(0.0), maximum=Db(3.2))

        ``Encoded(0.0)`` rather than ``Db(-40.0)`` for the heel, because the
        bottom of that knob is an Off detent below the dB scale - the same
        reason :meth:`set_param` refuses -40 dB there.

        Confirmed on hardware against every container the unit has - blocks, the
        input gate, the mixer, the splitter and the lane output - on both float
        and ``switch``-typed parameters. Parameter TYPE does not matter: the
        manual gives every assignable parameter a MIN/MAX sweep, and a block's
        BYPASS is the separate feature :meth:`set_expression_bypass` drives.

        **Two parameters refuse**: a Lane Output Control's MUTE and SOLO raise
        :class:`ControlNotDrivable`, because the device silently drops a host
        write of those in both directions while accepting the byte-identical
        message aimed at VOLUME. See
        :data:`~pyquadcortex.protocol.targets.LANE_OUTPUT_UNASSIGNABLE`, which records
        the three candidate rules that were tried and disproved.

        Note the manual's warning: a parameter assigned to an expression pedal is
        excluded from Scene data and will not change when switching scenes.
        """
        index, spec = target.index_of(param, self._get_catalog)
        if target.unassignable:
            # Only these targets can refuse, so only they pay for a catalog.
            spec = spec or target.spec_at(index, self._get_catalog)
        if spec is not None:
            target.refuse_if_unassignable(spec)
        low = _sweep_wire(minimum, target, index, spec, self._get_catalog,
                          "an expression sweep's minimum")
        high = _sweep_wire(maximum, target, index, spec, self._get_catalog,
                           "an expression sweep's maximum")
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        prm = target.container(msg).params.add()
        prm.index = index
        prm.expression = int(pedal)
        prm.expression_min = low
        prm.expression_max = high
        return self._t.send(msg)

    def clear_expression(self, target, param):
        """Unassign the expression pedal from a parameter.

        Writes ``expression: 0`` with the sweep back to the ``0.0..1.0`` every
        unassigned parameter reads. Refuses the same two as
        :meth:`set_expression`, for the same reason - the device will not take a
        host clear on them either.
        """
        index, spec = target.index_of(param, self._get_catalog)
        if target.unassignable:
            # Only these targets can refuse, so only they pay for a catalog.
            spec = spec or target.spec_at(index, self._get_catalog)
        if spec is not None:
            target.refuse_if_unassignable(spec)
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        prm = target.container(msg).params.add()
        prm.index = index
        prm.expression = 0
        prm.expression_min = 0.0
        prm.expression_max = 1.0
        return self._t.send(msg)



    # -- grid blocks ---------------------------------------------------------

    def set_block(self, cell, verify: bool = True, timeout: float = 5.0):
        """Put a model in a grid cell.

        ``cell`` is a :class:`~pyquadcortex.protocol.targets.Block`, and its
        ``model_id`` is WHAT TO PLACE - "what is, or is to be, in this cell" is
        the one meaning that reads the same for a block you read and a block you
        write, so :func:`blocks` round-trips::

            source = protocol.blocks(preset)[0]
            qc.set_block(Block(row=2, column=0, model_id=source.model_id))


        Creates a block in an empty cell and replaces whatever is in an occupied
        one - the device makes no distinction. ``model_id`` is a model id or a
        :class:`~pyquadcortex.protocol.catalog.Model`; :mod:`pyquadcortex.protocol.models` has
        constants for the factory blocks, and :attr:`catalog` resolves anything
        installed on the unit, including purchased models and Neural Captures.

        Confirmed on hardware, and matching the device's own broadcast when a
        block is added on the unit: ``Grid{UPDATE, preset{chains{row,
        models{column, hash}}}}``. Save the grid afterwards to keep it.

        **A placement can be refused for want of DSP capacity.** The preset as a
        whole has a processing budget, and a block that does not fit is accepted on
        the wire like any other write and simply is not there afterwards. Confirmed
        on hardware: adding a chain ending in a bass cab to factory "OneStar Clean
        Tweed" (02C) placed every block except the cab, deterministically, while the
        cheaper block AFTER it in the same chain landed. Nothing in the reply says
        so - every host write is STALLed, and there is no per-block error message.

        So by default this VERIFIES, which is possible without saving: the device
        echoes a ``Grid`` broadcast naming the cell it accepted (~0.3 s on the
        firmware measured), and a refused block produces no echo at all. When none
        arrives within ``timeout``, this raises :class:`BlockRefused`. Pass
        ``verify=False`` to send and return immediately, in which case a save and
        read-back is the only way to learn whether the block is there.
        """
        if cell.model_id in units_module.UNPLACEABLE_MODELS:
            raise ValueError(
                f"model {cell.model_id} must not be placed on the grid: "
                f"{units_module.UNPLACEABLE_MODELS[cell.model_id]} Recovering "
                f"needs a power cycle, so this is refused rather than tried."
            )
        row, column, model = cell.row, cell.column, cell.model_id
        model_id = int(getattr(model, "id", model))
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        chain = msg.preset.chains.add()
        chain.row = row
        model_msg = chain.models.add()
        model_msg.column = column
        model_msg.hash = model_id
        if not verify:
            return self._t.send(msg)

        def echoes_cell(m):
            # Same row/column conventions as blocks(): both fields may arrive
            # without presence, in which case position in the repeated field is
            # the index.
            for i, ch in enumerate(m.preset.chains):
                if (ch.row if field_present(ch, "row") else i) != row:
                    continue
                for j, mdl in enumerate(ch.models):
                    if (mdl.column if field_present(mdl, "column") else j) != column:
                        continue
                    if field_present(mdl, "hash") and mdl.hash == model_id:
                        return True
            return False

        try:
            self._t.await_broadcast(pa.GridMessage, lambda: self._t.send(msg),
                                    timeout=timeout, match=echoes_cell)
            return None
        except TimeoutError:
            pass

        # No echo is not the same as no placement, and treating it as one was a
        # FALSE NEGATIVE this raised twice in one session on blocks that had
        # landed perfectly well. So ask the unit what is actually in the cell
        # before telling the caller it refused.
        try:
            landed = any(b.row == row and b.column == column and b.model_id == model_id
                         for b in blocks(self.read_current_preset()))
        except Exception:
            landed = False
        if landed:
            return None

        raise BlockRefused(
            f"the device did not accept {model_id} at row {row} column "
            f"{column}: no Grid echo within {timeout}s, and reading the preset "
            f"back shows the cell is not holding it. Two causes are known. The "
            f"preset may have no DSP capacity left for this block - try a "
            f"cheaper one, or free a block. Or the placement may have hit a PORT "
            f"CONFLICT, which puts a modal on the unit's screen that the host "
            f"never sees: an FX Loop next to a Send competing for the same "
            f"physical send does this, and it has to be dismissed on the unit. "
            f"Pass verify=False to send without checking."
        ) from None

    def remove_block(self, cell):
        """Remove the block at ``cell``, leaving it empty.

        Confirmed on hardware, and matching the device's own broadcast when a
        block is deleted on the unit: ``Grid{action: DELETE, preset{chains{row,
        models{column, hash: 0}}}}``. The ACTION is what marks the removal -
        an UPDATE carrying ``hash: 0`` is transmitted but ignored by the
        firmware. Save the grid afterwards to keep it.
        """
        row, column = cell.row, cell.column
        msg = pa.GridMessage(action=pa.MessageAction.DELETE)
        chain = msg.preset.chains.add()
        chain.row = row
        model_msg = chain.models.add()
        model_msg.column = column
        model_msg.hash = 0
        return self._t.send(msg)

    def preset_dirty(self, timeout: float = 5.0) -> bool:
        """Whether the live grid has unsaved changes. Fast and cheap to poll.

        ``PresetDirty{READ}`` answers as an UPDATE echoing ``request_id``, in
        2-11 ms across every measured poll (two independent hardware sessions).
        Reads true after an edit and false after a clean save - confirmed by
        watching it flip across a save. Also pushed unsolicited in the connect
        burst, so state trackers can subscribe rather than poll.

        **The push announces a CHANGE of the flag, not an edit.** Measured
        2026-08-14 as a controlled pair in one connection: the same ``set_param``
        write produced a ``Grid`` echo AND a ``PresetDirty`` when the preset was
        clean, and a ``Grid`` echo and nothing else when it was already dirty.
        So do not wait on this to confirm an edit landed - it will time out on an
        already-dirty preset, correctly, because the unit said nothing. The
        ``Grid`` echo is the per-edit signal. See ``protocol.md``,
        "``PresetDirty`` announces a CHANGE of flag, not an edit".

        ``is_dirty`` has no field presence, so absent simply IS false - do not
        try to distinguish them. And like most reads here, the FIRST request
        after connecting is sometimes dropped; this raises ``TimeoutError`` in
        that case, and asking again is the fix.
        """
        reply = self._t.request(
            pa.PresetDirtyMessage(action=pa.MessageAction.READ), timeout=timeout)
        return bool(reply.is_dirty)

    def read_current_preset(self, timeout: float = 15.0):
        """The LIVE grid - the current editing state, unsaved changes included.

        ``RecallPreset{READ}`` answers with the preset as it exists on the device
        RIGHT NOW: an unsaved ``set_param`` write showed up in the reply, and the
        read has no side effects - the unsaved edit survived it, and the active
        scene is untouched. This kills the old inspect cycle of saving to a
        scratch slot just to see what the device holds, and it distinguishes "my
        write never applied" from "it applied and was later reset", which
        :meth:`read_preset` cannot do.

        The reply also carries ``reason`` (:class:`~pyquadcortex.protocol.RecallReason`):
        why the preset last changed. Measured: a host recall and a plain READ
        both report ``OTHER``; the push emitted by a save reports ``SAVE``;
        ``UNDO`` is defined but not yet observed. State trackers watching
        RecallPreset pushes can use it to tell a save's echo from a real recall.

        Contrast with :meth:`read_preset`, which reads a STORED slot - and which
        RECALLS that slot as a side effect, discarding unsaved edits, resetting
        the active scene to the preset's default, and **interrupting the audio
        every time** - even when it recalls the preset already loaded. Interleaving it with
        scene-targeted writes silently retargets them; use this method for
        inspection during editing.
        """
        return self.read_current_preset_push(timeout=timeout).preset

    def read_current_preset_push(self, timeout: float = 15.0):
        """The whole ``RecallPreset`` reply, not just the preset inside it.

        Same request and same match as :meth:`read_current_preset` - this is
        where that method's work happens and it returns ``.preset`` from here -
        so everything that method's docstring records about the wire applies
        unchanged. Nothing new is sent.

        It exists because the reply carries ``reason`` beside the preset, and a
        caller tracking state needs both from one answer. Confirmed on hardware
        2026-08-15: the connect burst's seed push sets ``action``, ``preset``
        and ``reason``, and so does the push a recall produces.
        """
        request_id = self._t.next_request_id()
        message = pa.RecallPresetMessage(action=pa.MessageAction.READ,
                                         request_id=request_id)
        return self._t.await_broadcast(
            pa.RecallPresetMessage, lambda: self._t.send(message), timeout=timeout,
            match=lambda m: (m.HasField("request_id")
                             and m.request_id == request_id))

    def loaded_position(self, timeout: float = 10.0):
        """Which preset slot is on the grid right now.

        ``SetlistPosition{READ}`` answers with ``folder_key``, ``position`` and
        ``is_factory``, and echoes ``request_id``. Returns the whole message, so
        a caller can compare all three rather than a rendered name - two
        addresses are best compared as positions, since a slot NAME moves with
        the footswitch mode.

        Confirmed on hardware 2026-08-15 (d14e): the READ was answered in 3 ms
        with the id echoed, on the first attempt. The unit also pushes one of
        these unsolicited in the connect burst and on every recall, so a state
        tracker can subscribe rather than poll.

        A READ does not recall anything - contrast :meth:`recall_preset`, which
        is the same message type as an UPDATE and does change what is loaded.
        """
        request_id = self._t.next_request_id()
        message = pa.SetlistPositionMessage(action=pa.MessageAction.READ,
                                            request_id=request_id)
        return self._t.await_broadcast(
            pa.SetlistPositionMessage, lambda: self._t.send(message),
            timeout=timeout,
            match=lambda m: (m.HasField("request_id")
                             and m.request_id == request_id))

    def active_scene(self, timeout: float = 10.0):
        """Which scene the unit is on right now, as a :class:`~pyquadcortex.protocol.Scene`.

        ``Scene{READ}`` answers with ``selected_scene`` and echoes ``request_id``;
        confirmed live by switching scenes between reads. Several writes apply to
        "the active scene" (``set_bypass`` on a scene-mode block, ``set_param``
        scene values), and a recall changes it out from under you - this makes the
        assumption checkable instead of tracked by hand.
        """
        request_id = self._t.next_request_id()
        message = pa.SceneMessage(action=pa.MessageAction.READ,
                                  request_id=request_id)
        reply = self._t.await_broadcast(
            pa.SceneMessage, lambda: self._t.send(message), timeout=timeout,
            match=lambda m: (m.HasField("request_id")
                             and m.request_id == request_id))
        return Scene(reply.selected_scene)

    def set_bypass(self, cell, bypassed: bool, scene=None):
        """Bypass or enable one block on the grid (row/column-keyed sparse update).

        Shape: ``Grid{UPDATE, preset{bypass{row, colBypass{column,
        sceneBypass{bypass}}}}}``. Save the grid afterwards to keep it.

        Unlike parameters, bypass really is per scene - but not by index.
        Confirmed on hardware: the device applies ``sceneBypass[0]`` to whichever
        scene is ACTIVE and ignores any entry beyond it. So:

        * without ``scene``, this changes the block in the currently active scene;
        * with ``scene`` (a :class:`~pyquadcortex.protocol.enums.Scene`), the unit is first
          switched to that scene, which is a visible side effect worth knowing
          about - the unit is left sitting there.

        Ordering over the pipe is enough for that pair; no settle delay is needed.

        Blocks only follow scenes when their ``ColBypass.sceneMode`` is set. For a
        block without it, bypass is one global state: the write lands on ALL EIGHT
        stored scene slots at once (measured - a fresh block took a single write
        across every slot). ``sceneMode`` itself is NOT host-writable: sent alone
        and sent beside a bypass entry, both were ignored. Factory content arrives
        with it set; the unit's own UI is presumably what sets it.

        **A Neural Capture block loses this write at its FIRST save.** A bypass
        written to a capture block in the same session that first PLACES it reads
        back correctly from :meth:`read_current_preset`, survives unrelated edits -
        and is dropped by the save that first materialises the capture, while an
        ordinary block in the same row keeps it. (Same family as the capture load
        resetting parameters, though parameters written after the load DO survive
        the save; bypass does not.) The sequence that persists, field-verified on
        24 presets and reproduced here::

            qc.save_current_preset(...)            # materialises the capture
            qc.recall_preset(...)                  # the stored slot
            qc.set_bypass(cell, True)              # now it sticks
            qc.save_current_preset(...)            # same name, same slot: no rename

        Verify with :func:`bypass_state` on the STORED preset, not just the live
        grid.

        **The trap that looks like a refused write:** :meth:`read_preset` RECALLS
        the slot it reads, which resets the active scene to the preset's default.
        A read interleaved between :meth:`switch_scene` and this write silently
        retargets the write at the default scene - which is how a field session
        concluded capture blocks ignore bypass, and how the first three probes
        here reproduced that conclusion. Inspect with :meth:`read_current_preset`
        instead, and check :meth:`active_scene` when a scene-targeted write seems
        to vanish.
        """
        row, column = cell.row, cell.column
        if scene is not None:
            self.switch_scene(scene)
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        bp = msg.preset.bypass.add()
        bp.row = row
        cb = bp.colBypass.add()
        cb.column = column
        cb.sceneBypass.add().bypass = bypassed
        return self._t.send(msg)

    def reroute_grid_input(self, p: preset.BinaryPreset, to_port: int,
                           from_port: int | None = None) -> list:
        """Re-point every grid input row on ``from_port`` to ``to_port``.

        ``p`` is the preset currently on the grid (from :meth:`read_preset`); the
        rows to move are found with :func:`input_chain_rows` and each is sent as
        a row-keyed :meth:`set_chain_input` update. Returns the rows moved.
        ``from_port`` defaults to :attr:`Input.INPUT_1` (factory presets are all
        on Input 1). Raises ``KeyError`` if no row is on ``from_port``. Save the
        grid to persist.
        """
        if from_port is None:
            from_port = Input.INPUT_1
        rows = input_chain_rows(p, from_port)
        if not rows:
            raise KeyError(f"no grid input row on port {from_port}")
        for row in rows:
            self.set_chain_input(row, to_port)
        return rows

    # -- file ops ------------------------------------------------------------

    LANE_OUTPUT_CONTROL = targets.LANE_OUTPUT_CONTROL

    TEMPO_CONTROL = targets.TEMPO_CONTROL

    INPUT_GATE_CONTROL = targets.INPUT_GATE_CONTROL


    #: The tempo parameter index carrying each BEAT of the bar, 1-based: beat 1 is
    #: index 10, beat 13 is index 22. These are the catalog's ``STEPSTATE0`` to
    #: ``STEPSTATE12``, one per beat, and each is a four-option list whose values
    #: are the :class:`~pyquadcortex.protocol.enums.MetronomeBeat` states.
    #:
    #: Traced on hardware by touching cells on the Tempo page in a known order.
    #: An older capture corroborates the mapping from a direction nobody was
    #: looking: selecting 7/8 (2+2+3) wrote indices 12 and 14 together, which are
    #: beats 3 and 5 - the group starts of 2+2+3 are beats 1, 3 and 5, and beat 1
    #: was already accented.
    #:
    #: How many beats are LIVE follows the time signature. That the count for the
    #: compound signatures matches their numerator (whether 6/8 draws six cells or
    #: two) has not been measured, so writing a beat above the current signature's
    #: count is allowed here rather than guessed at.
    TEMPO_BEATS = {beat: 9 + beat for beat in range(1, 14)}


    def set_tempo_option(self, param, option: int):
        """Set a list-valued tempo parameter by OPTION NUMBER rather than a float.

        Safer than a raw `set_param(Tempo(), ...)` for the controls that are lists, because
        the option is range-checked against the count the catalog publishes and the
        normalized value is worked out for you::

            qc.set_tempo_option("SUBDIVISIONS", 1)   # the second of four

        The counts, from the catalog: TIME SIGNATURE 21, SUBDIVISIONS 4, SOUND 6,
        ROUTING 5. What each option IS has not been established - the device supplies
        no option names for these parameters (unlike block parameters, whose lists
        arrive in the preset) and the manual does not enumerate them. Confirmed
        pairings so far: SUBDIVISIONS option 1 is 1/8 notes, TIME SIGNATURE option 1
        is 3/4, ROUTING option 3 is OUT 3/4, SOUND option 1 is Block.
        """
        index = param
        if isinstance(param, str):
            key = param.strip().upper()
            index = Tempo.NAMES.get(key)
            if index is None:
                index = self.catalog[self.TEMPO_CONTROL].parameter(param).index
        spec = self.catalog[self.TEMPO_CONTROL].parameters[index]
        return self.set_param(Tempo(), index,
                              values_module.Encoded(spec.option_to_value(option)))

    def set_tempo_subdivision(self, subdivision: "TempoSubdivision"):
        """Set the metronome's SUBDIVISIONS, by name rather than by number.

        Takes a :class:`~pyquadcortex.protocol.enums.TempoSubdivision`. A plain int is
        accepted and range-checked - an unknown one raises rather than storing a
        value that means nothing.
        """
        return self.set_tempo_option("SUBDIVISIONS", int(TempoSubdivision(subdivision)))

    def set_metronome_sound(self, sound: "MetronomeSound"):
        """Set the metronome's SOUND. Takes a
        :class:`~pyquadcortex.protocol.enums.MetronomeSound`."""
        return self.set_tempo_option("SOUND", int(MetronomeSound(sound)))

    def set_metronome_routing(self, routing: "MetronomeRouting"):
        """Set where metronome playback goes. Takes a
        :class:`~pyquadcortex.protocol.enums.MetronomeRouting`."""
        return self.set_tempo_option("ROUTING", int(MetronomeRouting(routing)))

    def set_time_signature(self, signature: "TimeSignature"):
        """Set the metronome's time signature. Takes a
        :class:`~pyquadcortex.protocol.enums.TimeSignature`.

        **This rewrites per-beat states**, because the accent pattern is stored per
        beat and the device re-lays it out for the new signature. Selecting 7/8
        (2+2+3) accents beats 3 and 5 to join the beat 1 that was already accented.
        So set the signature FIRST and the beats after - the other order loses
        them. Read them back with :meth:`beats`.
        """
        return self.set_tempo_option("TIME SIGNATURE", int(TimeSignature(signature)))

    def set_beat(self, beat: int, state: "MetronomeBeat"):
        """Set how ONE beat of the bar sounds.

        ``beat`` is 1-based, up to 13. ``state`` is a
        :class:`~pyquadcortex.protocol.enums.MetronomeBeat` - ``NORMAL``, ``OFF``,
        ``ACCENT`` or ``QUIET``. A plain int is accepted and range-checked::

            qc.set_beat(1, MetronomeBeat.ACCENT)   # the downbeat
            qc.set_beat(3, MetronomeBeat.OFF)      # skip beat 3

        These are the cells on the Tempo page, catalog ``STEPSTATE0`` upwards, and
        the mapping was traced by touching them on the unit. Note the enum's order
        IS the order a cell cycles when touched, which is not a loudness ordering.

        Beats beyond the current time signature's count are storable but inaudible,
        and :meth:`set_time_signature` rewrites these, so set the signature first.
        """
        if beat not in self.TEMPO_BEATS:
            raise ValueError(
                f"beat must be 1 to 13, not {beat!r} - the unit stores 13 per-beat "
                f"cells (the catalog's STEPSTATE0 to STEPSTATE12), enough for its "
                f"largest signature, 13/4"
            )
        return self.set_tempo_option(self.TEMPO_BEATS[beat],
                                     int(MetronomeBeat(state)))

    def set_beats(self, states) -> list:
        """Set consecutive beats from the START of the bar, in one call.

        Takes an iterable of :class:`~pyquadcortex.protocol.enums.MetronomeBeat`, beat 1
        first::

            qc.set_beats([ACCENT, NORMAL, OFF, QUIET])   # a 4/4 bar

        Writes only the beats given - a shorter sequence leaves the rest alone,
        which matters because the unit keeps 13 cells regardless of signature.
        Returns what each :meth:`set_beat` returned.
        """
        states = list(states)
        if len(states) > len(self.TEMPO_BEATS):
            raise ValueError(
                f"got {len(states)} beats but the unit stores only "
                f"{len(self.TEMPO_BEATS)}"
            )
        return [self.set_beat(i, s) for i, s in enumerate(states, start=1)]

    def set_tempo_led(self, on: bool):
        """Turn this preset's TEMPO LED on or off."""
        return self.set_param(Tempo(), "LED LIGHT",
                              values_module.Encoded(1.0 if on else 0.0))

    def set_metronome_running(self, running: bool):
        """Start or stop this preset's metronome.

        Tempo parameter 4 - the unit's MUTE, the catalog's START, the manual's
        PLAYBACK, all one control - where **1.0 is audible and 0.0 is silent**.
        The unit offers no start/stop button; its transport always runs and MUTE
        is how a player silences it, so :meth:`set_metronome_muted` speaks in the
        label they see. These two cannot disagree: ``running=True`` is
        ``muted=False``. This is the control to reach for
        when the click must be OFF: :meth:`set_metronome_volume` cannot silence
        it (its floor is -60 dB, still audible), and two releases of this library
        documented parameter 4 as a mute with the polarity inverted, which parked
        36 field-built presets at "running, quietly". **Confirmed by ear**, not
        only by the factory-content argument: ``True`` produced an audible click
        and ``False`` stopped it, with a person listening at the unit. An audible effect - see
        "Settings only your ears can verify" in the API guide.
        """
        return self.set_param(Tempo(), "START",
                              values_module.Encoded(1.0 if running else 0.0))

    def set_metronome_muted(self, muted: bool):
        """Mute or unmute this preset's metronome - the unit's own MUTE control.

        The Tempo page on the unit offers exactly one control here, labelled
        **MUTE**, and it writes tempo parameter 4 INVERTED: mute-on sends 0.0,
        mute-off sends 1.0 (traced from the hardware). This method speaks in the
        label a player sees, so ``set_metronome_muted(True)`` silences the click.

        :meth:`set_metronome_running` is the same parameter in wire terms
        (``running=True`` is ``muted=False``). Use whichever reads better where
        you are; they cannot disagree.

        Note the unit has no start/stop control - the transport always runs, and
        muting is how a player silences it. An audible effect: see "Settings only
        your ears can verify" in the API guide.
        """
        return self.set_metronome_running(not muted)

    def set_metronome_volume(self, value):
        """Set this preset's metronome level. **Wire 0.0 is -60 dB, not silence.**

        The catalog's range for this control is genuine: **-60 to +9 dB**, linear
        in the wire value (``dB = -60 + 69 * value``), and unlike the
        similarly-named lane VOLUME - whose wire 0.0 is an Off detent and really
        is silence - the metronome's quietest setting is still plainly audible on
        headphones. True silence is not reachable with this control;
        stop the transport instead with :meth:`set_metronome_running`.

        Takes a typed value like any other parameter - ``Db`` for decibels,
        ``Encoded`` for the device's own 0..1::

            qc.set_metronome_volume(Db(-20.0))       # -20 dB
        """
        return self.set_param(Tempo(), "VOLUME", value)

    #: Index of MODE inside the DEVICE tempo block. It is the catalog's ``TYPE``,
    #: which no control in the preset's own Tempo page writes - the preset copy of
    #: parameter 1 sat at 0.0 through every flip measured.
    TEMPO_MODE_PARAM = 1

    def tempo_mode(self, timeout: float = 30.0) -> "TempoMode":
        """Whether the unit is running on the PRESET's tempo or the DEVICE's.

        The Tempo and Metronome menu's MODE switch. Returns a
        :class:`~pyquadcortex.protocol.enums.TempoMode`.

        Confirmed on hardware (2026-08-12) by capturing every field of every
        message the device answers in each switch position and diffing the two:
        exactly one field moved, this one. The tempo actually in effect
        corroborates it from a second direction - the unit displayed 111 bpm in
        PRESET with the preset block holding 0.355, and 120 bpm in GLOBAL with the
        device block holding 0.400.

        **The device emits no CHANGE EVENT when the switch moves** - three earlier
        investigations watched for one and correctly found none. The current VALUE
        is a different matter: it rides the ambient ``GlobalTempo`` params push,
        which arrived twice per 14-second window in each of the three captures
        (against 63 clock-shaped pushes in the same window). So a state tracker CAN
        follow this field from pushes; what it cannot do is be told the moment it
        moves.

        That is also why the timeout is generous. ``GlobalTempo`` alternates two
        shapes and only one carries parameters, so this waits for that shape
        specifically rather than taking the first ``GlobalTempo`` to arrive - which
        is how a single earlier READ came back holding only the running clock and
        got written up as a dead end.

        **A read straight after a write returns the PREVIOUS value, and "a moment"
        is not long enough.** This type does not echo ``request_id`` - zero of 64
        captured pushes carried one - so this returns the next AMBIENT params push,
        which may have been generated before your write. That shape arrives only
        about every seven seconds, so **wait longer than that interval** - ten
        seconds is the figure the hardware suite uses. Observed directly: a write
        followed by a 3-second settle read back the old value, while the write had
        landed and every read afterwards agreed.

        A caller who needs certainty rather than a settle should discard the first
        matching push and take the second, which cannot predate the call.
        """
        index = self.TEMPO_MODE_PARAM
        seen = []

        def carries_mode(message):
            seen.append(message)
            return _mode_param(message, index) is not None

        try:
            reply = self._t.await_broadcast(
                pa.GlobalTempoMessage,
                lambda: self._t.send(
                    pa.GlobalTempoMessage(action=pa.MessageAction.READ)),
                timeout=timeout, match=carries_mode)
        except TimeoutError:
            # "No broadcast arrived" and "none of them carried what I asked for"
            # are different facts, and this project has already spent eight
            # releases on the difference. Say which one happened.
            raise TimeoutError(
                f"no GlobalTempo carrying tempo parameter {index} within "
                f"{timeout}s. {len(seen)} GlobalTempo push(es) DID arrive - this "
                f"type alternates a clock shape with a params shape and only the "
                f"params shape answers, so a longer timeout may be all that is "
                f"needed. Do not read this as the device being silent."
            ) from None

        value = _mode_param(reply, index)
        if value not in (float(TempoMode.PRESET), float(TempoMode.GLOBAL)):
            # Not rounded into an enum. The same policy as beats(): a value
            # outside the states we know means the assumption is wrong, and
            # rounding would convert that signal into a confident answer.
            raise ValueError(
                f"tempo parameter {index} holds {value!r}, which is neither "
                f"{float(TempoMode.PRESET)} (PRESET) nor {float(TempoMode.GLOBAL)} "
                f"(GLOBAL). The MODE mapping may not hold on this firmware."
            )
        return TempoMode(int(value))

    def set_tempo_mode(self, mode: "TempoMode"):
        """Move the MODE switch: run on the preset's tempo, or the device's.

        Takes a :class:`~pyquadcortex.protocol.enums.TempoMode`. Confirmed on
        hardware and ON THE UNIT'S OWN SCREEN (2026-08-12): writing GLOBAL moved
        the menu's switch and changed the tempo in effect from 111 to 120 bpm,
        writing PRESET moved both back.

        **Global, not per preset**, despite riding a tempo message: there is nothing to
        save afterwards. Read :meth:`tempo_mode` first if you intend to put it back.

        What was MEASURED is that the write moves the device block and leaves the
        loaded preset's own copy alone. That it therefore affects EVERY preset
        follows from the menu being a device setting, and was not tested with a
        second preset loaded.

        This does NOT move either tempo block. The preset's
        ``tempoProgramData`` parameter 1 was measured before and after the write
        and did not move, which is what makes the scope of this write knowable
        rather than assumed - the device accepts a write it does not understand
        and says nothing, so a write whose target is guessed is indistinguishable
        from one that worked.
        """
        message = pa.GlobalTempoMessage(action=pa.MessageAction.UPDATE)
        param = message.params.add()
        param.index = self.TEMPO_MODE_PARAM
        param.param_values.add().float_value = float(int(TempoMode(mode)))
        return self._t.send(message)

    def set_chain_output(self, row: int, out_portid: int):
        """Point one grid ``row``'s output at ``out_portid`` (row-keyed update).

        The sibling of :meth:`set_chain_input`, and the piece needed to finish a
        chain built on a previously empty row: blocks and an input are not enough,
        because a row whose output is unset does not reach a jack.

        Confirmed on hardware by read-back, not assumed from the symmetry: a
        ``Grid`` UPDATE carrying a single chain ``{row, out_portid}`` re-points that
        row's output, and the value survives a save and recall.

        Pass a :class:`~pyquadcortex.protocol.enums.Output`. Note that not every member is a
        physical destination. Values **16 to 18** are internal grid routing
        (``NEXT_ROW_*``): a row set to one of those feeds another row rather than a
        jack.

        **19 (``MULTIPLE``) IS a real destination** - it is what factory presets use
        to reach the Multi-Out, so it is often the right value when building a chain
        that has to be audible alongside an existing one. Factory "Brit 2203" uses 16
        on row 0 to feed the next row and 19 on row 2 for the actual output.

        Note also that the device does NOT validate this field: an id that means
        nothing is stored rather than rejected, so a typo reads back cleanly. See
        ``docs/protocol.md``.
        """
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        chain = msg.preset.chains.add()
        chain.row = row
        chain.out_portid = out_portid
        return self._t.send(msg)

    SPLITTER_AB = targets.SPLITTER_AB
    MIXER = targets.MIXER

    SPLITTER = targets.SPLITTER












    def set_split_mute(self, row: int, muted: bool = True):
        """Mute or unmute the split/mix path on ``row``.

        The manual lists a MUTE under both SPLITTER PARAMETERS and MIXER
        PARAMETERS. It is **one control**, not two: muting the splitter on the
        unit shows the mixer's MUTE already engaged (confirmed on the unit).
        Neither appears in the catalog's parameter list for either model, which
        is why it is here rather than a ``param`` on
        `set_param(Splitter(row), ...)`.

        The write goes to ``Chain.splitBypass`` and the device reports the result
        in ``Chain.mixBypass`` - the same write-here/read-there split as
        ``combined_splitter`` versus ``splitter[]``. A write to ``mixBypass``
        does nothing.

        Both fields are ``repeated SceneBypass``, one entry per scene, but a
        single write sets **all eight**: it is not per-scene in practice.

        ``row`` must be 0 or 2, as for any splitter or mixer.
        """
        _require_even_row(row, "splitter or mixer")
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        chain = msg.preset.chains.add()
        chain.row = row
        chain.splitBypass.add().bypass = muted
        return self._t.send(msg)

    def set_stomp_assignment(self, cell, footswitch):
        """Assign a block to a STOMP-mode footswitch.

        ``footswitch`` is a :class:`~pyquadcortex.protocol.enums.Footswitch` (or 0-7 for
        A-H). One footswitch may drive several blocks - factory content does
        this - so assigning does not displace anything else.

        Reproducing the unit's own two-message sequence, which is what makes it
        stick: a DELETE of any existing assignment for that cell, then the new
        one. Sending only the UPDATE leaves the old assignment in place.

        Read them back with :func:`stomp_assignments`.
        """
        row, column = cell.row, cell.column
        self.clear_stomp_assignment(cell)
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        a = msg.preset.stomp_mode_assignments.add()
        a.row = row
        a.column = column
        a.stomp_index = int(footswitch)
        return self._t.send(msg)

    def clear_stomp_assignment(self, cell):
        """Unassign a block from its footswitch."""
        row, column = cell.row, cell.column
        msg = pa.GridMessage(action=pa.MessageAction.DELETE)
        a = msg.preset.stomp_mode_assignments.add()
        a.row = row
        a.column = column
        return self._t.send(msg)

    def set_stomp_momentary(self, footswitch, momentary: bool = True):
        """Make a footswitch momentary rather than latching, for this preset.

        ``BinaryPreset.stomp_is_momentary`` is a map keyed by **footswitch index**,
        not by column. Confirmed on hardware with a case where the two differ: a
        block at column 3 assigned to footswitch E broadcast
        ``stomp_is_momentary{key: 4}``. The map is sparse and factory content
        leaves it empty, so a missing entry means latching.

        The control is real on the unit despite the manual's silence. Manual 4.0.0
        documents "momentary" only for the expression toe switch and Looper X, but
        the touchscreen's **Assign footswitch** modal carries a Latching/Momentary
        toggle, and using it broadcasts exactly this map entry.

        **A momentary write only lands on a footswitch driving exactly ONE block.**
        The device enforces that rule on the wire, and enforces it SILENTLY: a write
        aimed at a switch with two or more blocks is accepted, echoes nothing, and
        reads back unchanged. The unit greys its own toggle out in the same case, so
        this is a device rule rather than a transport wart. Verified three ways
        within one preset - two single-block switches took the write and read back
        ``True``, one of them confirmed by eye on the unit having never been touched
        by hand, while a two-block switch stayed ``False`` across repeated attempts.

        Check the target with :meth:`stomp_assignments` first if it matters. There
        is no error to catch.

        **Why this method does not check for you.** It could - the rule is
        readable, and :meth:`set_master_volume_assignment` shows a setter here may
        read before it writes. The choice is deliberate: enforcement lands at the
        model layer at M1, where ``StompAssignment.targets`` already knows the
        count, so the refusal costs no extra round trip and can name the switch.
        Adding a read here would buy the same guarantee at the price of a device
        read on every call, in a layer whose job is to say exactly what goes on
        the wire.
        """
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        msg.preset.stomp_is_momentary[int(footswitch)] = momentary
        return self._t.send(msg)

    def set_stomp_label(self, footswitch, label: str, single: bool = False):
        """Label a footswitch for this preset.

        Two maps exist: ``stomp_labels`` and ``single_stomp_labels``, the latter
        used when the footswitch drives exactly one block. The unit clears both
        when an assignment is removed. ``single=True`` writes the second.
        """
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        target = (msg.preset.single_stomp_labels if single
                  else msg.preset.stomp_labels)
        target[int(footswitch)] = label
        return self._t.send(msg)



    def set_midi_out(self, source, messages):
        """Set the MIDI messages a footswitch or expression pedal sends.

        ``source`` is a :class:`~pyquadcortex.protocol.enums.MidiSource` - footswitches A-H
        (0-7) or the two expression pedals (8, 9). ``messages`` is a list of
        :class:`MidiOut` entries, up to 12; the list REPLACES whatever that source
        had.

        These do NOT travel by ``Grid``. The preset stores them in
        ``midi_messages_general_v2``, but a ``Grid`` update carrying that field is
        accepted and ignored - ``MIDISettings`` is what applies them::

            qc.set_midi_out(MidiSource.FOOTSWITCH_A, [MidiOut.cc(channel=3, cc=10, value=64)])

        Confirmed on hardware: the values survive a save and read-back, and the
        120-slot array is 10 sources x 12 messages, so source N occupies slots
        ``N*12`` onward. The device mirrors the first message of each source into
        the 10-slot legacy ``midi_messages_general``.

        A ``MIDISettings`` READ gets no reply on this firmware, so read the saved
        preset to verify rather than asking the device.
        """
        return self._midi_settings("general_midi_messages", source, messages)

    def set_preset_load_midi_out(self, messages):
        """Set the MIDI messages sent when this preset is loaded (up to 12).

        Same mechanism as :meth:`set_midi_out`; these land in
        ``BinaryPreset.midi_messages``.
        """
        return self._midi_settings("preset_load_messages", 0, messages)

    def _midi_settings(self, field: str, source, messages):
        msg = pa.MIDISettingsMessage(action=pa.MessageAction.UPDATE)
        group = getattr(msg, field).messages.add()
        group.source = int(source)
        for m in messages:
            entry = group.msg.add()
            entry.type = int(m.type)
            entry.channel = int(m.channel)
            entry.param1 = int(m.param1)
            entry.param2 = int(m.param2)
            entry.param3 = int(m.param3)
        return self._t.send(msg)


    def move_block(self, source, destination, drop: bool = True):
        """Move the block at one grid cell to another.

        ``source`` and ``destination`` are
        :class:`~pyquadcortex.protocol.targets.Block` cells; only their coordinates
        are read, so a destination's ``model_id`` is ignored.


        Shape: ``GridMove{move{from_row, from_col, to_row, to_col, is_drop}}``.
        Confirmed driven host-to-device: moving row 2 column 1 to column 7 left
        every other cell on that row where it was, and row 0 untouched.

        **A cross-row move creates a parallel path**, which is what the manual
        means by dragging a block from path A to path B. Moving row 0 column 6 to
        row 1 column 6 on factory "Brit 2203" - a serial preset with no branch -
        left the device reporting ``Split(row=0, split_column=0, mix_column=7)``:
        the branch and rejoin columns are computed by the device, not by the
        caller. Use :meth:`set_split` to place them deliberately instead.

        The message also has an optional ``grid`` snapshot of every row's model
        ids. It is ADVISORY - replaying one with a cell zeroed does not delete a
        block - so this sends only the move.
        """
        from_row, from_col = source.row, source.column
        to_row, to_col = destination.row, destination.column
        msg = pa.GridMoveMessage()
        mv = msg.move.add()
        mv.from_row = from_row
        mv.from_col = from_col
        mv.to_row = to_row
        mv.to_col = to_col
        mv.is_drop = drop
        return self._t.send(msg)

    def set_split(self, row: int, split_column: int, mix_column: int):
        """Branch ``row`` into its parallel lane at ``split_column``, rejoining at
        ``mix_column``.

        This is how a splitter is created without touching the unit. Every even
        row already carries a splitter, mixer and combined splitter - they are
        simply dormant, with ``split_control_points`` reporting ``-1`` - so
        activating a branch means setting the columns::

            Grid{UPDATE, preset{chains{row, split_control_points{split, mix}}}}

        Confirmed on factory "Brit 2203", a serial preset: after this write
        :func:`splits` reported the branch, and `set_param(Splitter(row), ...)`,
        `set_param(Mixer(row), ...)` and :meth:`set_split_mute` all drove it - a
        ``LEVEL TO B`` of 0.25 and a mixer ``LEVEL B`` of 0.5 both read back.

        Pass ``mix_column=-1`` for a branch that never rejoins, which is what
        several factory presets do. ``row`` must be 0 or 2.
        """
        _require_even_row(row, "splitter or mixer")
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        chain = msg.preset.chains.add()
        chain.row = row
        scp = chain.split_control_points.add()
        scp.split = split_column
        scp.mix = mix_column
        return self._t.send(msg)

    def clear_split(self, row: int):
        """Remove ``row``'s branch, making it serial again.

        Sets both columns to the ``-1`` sentinel. Confirmed on factory 28A:
        clearing row 0 left row 2's branch untouched.
        """
        return self.set_split(row, -1, -1)

    def set_expression_bypass(self, block, pedal: int = 1,
                              mode: int = 0, invert: bool = False,
                              delay_ms=values_module.Milliseconds(0),
                              latch_emulation: bool = False):
        """Let an expression pedal bypass a block.

        ``block`` is a :class:`~pyquadcortex.protocol.targets.Block`. This is the only
        expression feature that is block-only: a bypass is not a parameter, so
        the other targets have nothing to point it at.


        Writes both halves of the feature in one message: ``bypass_expression``
        (which pedal, and the range over which it acts) and
        ``expression_bypass_info`` (how it behaves). Confirmed round-tripping
        through a save: pedal 1, mode 1, invert, 250 ms, latch emulation.

        ``mode`` is an
        :class:`~pyquadcortex.protocol.enums.ExpressionSwitchMode`: all three are confirmed
        on the unit, and note the numbering is not the manual's listed order -
        ``STOP`` is 0, ``SWITCH`` 1 and ``HEEL_TOE`` 2. ``invert`` reverses the
        value at which the bypass engages and ``latch_emulation`` lets a
        momentary toe switch behave as latching.

        ``delay_ms`` is the switch delay, up to 5000 ms, and takes
        ``Milliseconds(250)``. Like the HOLD threshold it has no 0..1 line - the
        wire carries the milliseconds themselves - so ``Encoded`` is refused.
        """
        if not isinstance(block, Block):
            raise TypeError(
                f"set_expression_bypass takes a Block: a bypass is not a "
                f"parameter, so {type(block).__name__} has nothing to bypass"
            )
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        model = block.container(msg)
        be = model.bypass_expression.add()
        be.expression = int(pedal)
        be.expression_min = 0.0
        be.expression_max = 1.0
        info = model.expression_bypass_info.add()
        info.type = int(mode)
        info.invert = invert
        # int(): the wire field is an integer count of milliseconds, and the
        # typed value is a float subclass like every other value here.
        info.delay_ms = int(_setting_real(
            delay_ms, "the bypass switch delay", values_module.Milliseconds))
        info.latch_emulation = latch_emulation
        return self._t.send(msg)



    # -- global device settings ----------------------------------------------
    #
    # These are GLOBAL: unlike a preset edit, a write here changes the unit
    # itself and there is nothing to save. Read the current value first if you
    # intend to put it back.
    #
    # Two things about these that have caused wrong conclusions more than once:
    #
    #   1. State pushes can be PARTIAL - a push following an UPDATE may carry only
    #      what changed - so each reader below waits for a push that actually
    #      contains the field it needs rather than taking the first one.
    #   2. A read immediately after a write can return the PREVIOUS value. Three
    #      separate settings looked like they had refused a write when the write had
    #      in fact landed and the read was simply stale. Allow a settle, or re-read,
    #      before concluding anything.

    def _read_state(self, cls, match, timeout: float = 10.0):
        """READ a state type and return the first push satisfying ``match``."""
        return self._t.await_broadcast(
            cls, lambda: self._t.send(cls(action=pa.MessageAction.READ)),
            timeout=timeout, match=match)

    def settings(self, timeout: float = 10.0):
        """The device's global settings, as a ``GeneralSettings`` message.

        One message covers most of the unit's Device Settings and System menus:
        ``screen_brightness``, ``led_brightness``, ``scene_block_bypass``,
        ``global_bypass_cab``/``_ir``, ``stomp_mode_auto_assign``,
        ``swap_tempo_tuner_access``, ``enable_dynamic_delay_compensation``,
        ``hold_timing``, ``lock_screen_and_volume_knob`` (the unit's lock mode -
        note it locks the TOUCHSCREEN AND VOLUME KNOB only: a host parameter
        write landed, read back exactly and restored cleanly while it was
        engaged, so host control is unaffected despite what "locked" invites you
        to assume; engaging it on the unit is observable as a partial push
        carrying only that field), MIDI channel and clock
        settings, ``power_option``,
        ``master_volume_assignment`` (the per-output checkboxes) and the
        ``available_disk_space``/``total_disk_space`` pair, among others.

        Returned raw rather than wrapped: it is a wide, firmware-defined message
        and reshaping it would hide fields. Read fields with
        :func:`field_present` before trusting them.
        """
        return self._read_state(pa.GeneralSettingsMessage,
                                lambda m: m.HasField("scene_block_bypass"), timeout)

    def update_settings(self, **fields):
        """Change global settings, sparsely: only the fields named are sent.

        Any scalar field of ``GeneralSettings`` may be given::

            qc.update_settings(screen_brightness=60, swap_tempo_tuner_access=True)

        Confirmed writable on hardware, each tested on its own and restored:
        ``screen_brightness``, ``led_brightness``, ``dimmed_led_brightness``,
        ``scene_block_bypass``, ``stomp_mode_auto_assign``,
        ``swap_tempo_tuner_access``, ``enable_dynamic_delay_compensation``,
        ``gig_view_stomp_access_enabled``, ``hold_timing``, ``midi_channel``,
        ``midi_over_usb``, ``midi_clock_in_enabled``, ``ignore_duplicate_pc``,
        ``disable_internet_connection_check`` and ``enable_preset_dimmed``.

        **Refused:** ``internal_midi_clock_enabled`` stays true whatever you send,
        with ``midi_clock_in_enabled`` either way, so the internal clock cannot be
        switched off from here.

        Two scales are not what they look like:

        * **Brightness is quantized.** Writing 30 read back as 31 and 60 as 59, so
          the device stores it on a coarser internal scale.
        * **``dimmed_led_brightness`` is capped just under ``led_brightness``** -
          the dimmed value has to be dimmer. Asking for 100 landed on 25 with
          ``led_brightness`` at 28, on 9 with it at 13, and on 56 with it at 59. So
          raise ``led_brightness`` first if you want a high dimmed value.
        * **``hold_timing`` is an INDEX, not milliseconds.** The unit offers six
          values - 500 to 1000 ms in 100 ms steps - and the field is the index into
          them, confirmed by reading 3 over USB while the screen showed 800 ms. So
          ``ms = 500 + 100 * hold_timing``. The device does NOT validate it: 0 and
          5000 both round-tripped, so only 0-5 are meaningful. Use
          :meth:`set_hold_timing` to pass milliseconds and have this checked.

        **Top-level fields are sparse, but a SUBMESSAGE is replaced wholesale.**
        Sending ``master_volume_assignment`` with one flag set leaves the other
        three false. Use :meth:`set_master_volume_assignment` and
        :meth:`set_global_bypass`, which read the current value and merge.

        Values the device treats as commands rather than settings are refused
        here to avoid an accident: ``power_option`` can shut the unit down or
        reboot it, and ``reset_wifi_networks`` discards saved networks. Send
        those yourself through the transport if you really mean to.
        """
        blocked = {"power_option", "reset_wifi_networks"}
        bad = blocked.intersection(fields)
        if bad:
            raise ValueError(
                f"{sorted(bad)} are device commands rather than settings and are "
                f"not sent by this method - power_option can shut the unit down "
                f"or reboot it"
            )
        unknown = [k for k in fields
                   if k not in pa.GeneralSettingsMessage.DESCRIPTOR.fields_by_name]
        if unknown:
            raise TypeError(f"GeneralSettings has no field(s) {sorted(unknown)}")
        msg = pa.GeneralSettingsMessage(action=pa.MessageAction.UPDATE, **fields)
        return self._t.send(msg)

    HOLD_TIMING_MS = (500, 600, 700, 800, 900, 1000)

    def set_hold_timing(self, milliseconds):
        """How long a press must last before it counts as a HOLD gesture.

        Takes ``Milliseconds(800)`` - one of 500, 600, 700, 800, 900 or 1000, the
        six values the unit offers - and writes the index the device actually
        stores. There is no 0..1 line here at all, so ``Encoded`` is refused
        rather than quietly accepted: the wire carries an INDEX derived from the
        milliseconds, not a position along a range.
        ``GeneralSettings.hold_timing`` is that index, confirmed by reading 3 while
        the screen showed 800 ms, so ``ms = 500 + 100 * index``.

        **This is a gesture threshold, not the duration of an assignable
        per-footswitch action**, settled on hardware. Holding a stomp footswitch
        produces exactly one ordinary bypass toggle and nothing on screen; holding
        in SCENE mode selects a scene and in PRESET mode recalls a preset - each
        indistinguishable on the wire from a press. What the threshold governs is
        the unit's FIXED hold gestures: hold TEMPO for the Tuner, BANK DOWN +
        TEMPO for Gig View, and the touchscreen tap-and-holds. Shortening it to
        500 ms makes the Tuner open sooner, which is how it was pinned.

        So the manual's "its assigned HOLD action" is loose wording: nothing binds
        a hold to a footswitch on this firmware, and there is no hold event to
        observe.

        The device accepts any integer in that field without validation, storing 0
        and 5000 as happily as a real index, so this rejects anything outside the
        six rather than letting a meaningless value through.
        """
        milliseconds = _setting_real(milliseconds, "the HOLD threshold",
                                     values_module.Milliseconds)
        try:
            index = self.HOLD_TIMING_MS.index(int(milliseconds))
        except ValueError:
            raise ValueError(
                f"hold timing must be one of {list(self.HOLD_TIMING_MS)} ms, "
                f"not {milliseconds}"
            ) from None
        return self.update_settings(hold_timing=index)

    def hold_timing_ms(self, timeout: float = 10.0) -> int:
        """The current HOLD action timing, in milliseconds."""
        index = self.settings(timeout).hold_timing
        if 0 <= index < len(self.HOLD_TIMING_MS):
            return self.HOLD_TIMING_MS[index]
        raise ValueError(
            f"hold_timing reads {index}, which is outside the six values the unit "
            f"offers - something wrote an unvalidated value into it"
        )

    def set_scene_bypass_behavior(self, behavior):
        """Set whether block bypass changes are saved per scene.

        A :class:`~pyquadcortex.protocol.enums.SceneBypassBehavior`. This is global, and it
        decides what :meth:`set_bypass` persists.

        **A host write counts as a touchscreen edit, not a footswitch press.**
        Measured across all three modes, each write verified as landed before the
        scene was changed:

        =====================  ===========  ==========  =============
        mode                   touchscreen  footswitch  host write
        =====================  ===========  ==========  =============
        ``ALWAYS_OVERWRITE``   persists     persists    **persists**
        ``NONSTOMP_OVERWRITE`` persists     discarded   **persists**
        ``NEVER_OVERWRITE``    discarded    discarded   **discarded**
        =====================  ===========  ==========  =============

        The touchscreen and footswitch columns were driven by hand on the unit;
        the host column is this method plus :meth:`set_bypass`. The manual names
        only the two physical routes, so where a host write falls was not
        inferable from it.

        The consequence for a caller: under ``NEVER_OVERWRITE`` a bypass write is
        applied and then dropped on the next scene change, which looks exactly
        like a failed write and is not one. Confirmed writable and restorable.
        """
        return self.update_settings(scene_block_bypass=int(behavior))

    def io_settings(self, timeout: float = 10.0):
        """The unit's input, output, headphone, USB, MIDI and expression ports.

        An ``IOSettings`` message. ``settings.in_port[]`` carries each input's
        ``level`` (the gain), ``input_zmode`` (impedance), ``input_type``,
        ``ground_lift`` and ``plugged``; ``settings.out_port[]`` the output
        levels and mutes; plus ``hp_port``, ``usb_port``, ``midi_port`` and
        ``exp_port[]``. ``plugged`` is useful ground truth for what is physically
        connected.
        """
        return self._read_state(pa.IOSettingsMessage,
                                lambda m: len(m.settings.in_port) > 0, timeout)

    def set_input_port(self, input_port_id: int, level=None,
                       impedance: float | None = None, input_type: float | None = None,
                       ground_lift: float | None = None, confirm: bool = False,
                       timeout: float = 20.0):
        """Change one input port's settings, sparsely.

        Only the arguments given are sent, and only that port is affected -
        confirmed on hardware, where writing one input left the other three
        byte-identical.

        **``input_port_id`` takes the** :class:`~pyquadcortex.protocol.Input` **enum values,
        NOT 1/2/3/4.** The combined ids are interleaved, so Return 1 is **4** and
        Return 2 is **5** (3 is INPUT_1_2, 6 is RETURN_1_2). Passing 3 for
        "Return 1" writes the combined Input 1/2 entry instead - an easy and
        expensive mistake, so pass ``Input.RETURN_1`` rather than a number.

        ``level`` (the gain) says which scale it is on - ``Db(24.0)`` for the
        screen's **-12..+60 dB**, ``Encoded(0.5)`` for the device's 0..1.
        ``impedance``, ``input_type`` (the manual's Instrument/Mic switch) and
        ``ground_lift`` are SELECTORS rather than values on a scale, so they
        stay plain the way an option list does - ADR-0016 types values, not
        switches. All four are confirmed writable.

        **Each field is sent in its own message**, because the device drops some
        fields that share a port entry with another: `mute` on an output and
        `impedance` on an input both failed when paired and both worked alone. Rather
        than track which combinations are safe, this sends one at a time - a few extra
        writes for a guarantee.

        These are global and survive power cycles, and an input gain is usually
        something a player has set by ear. Read :meth:`io_settings` first if you
        intend to put it back.

        **A single read-back is not verification.** The first :meth:`io_settings`
        after a write can return the OLD value even when the write landed -
        measured in the field: four port writes, one clean read, one stale value,
        and a build run burned on a "refusal" that never happened. With
        ``confirm=True`` this polls :meth:`io_settings` until the port reflects
        every field written (float32-tolerant), raising ``TimeoutError`` -
        explaining the stale-read behaviour - if it never does.
        """
        if level is not None:
            level = _setting_wire(level, "an input port's GAIN", _INPUT_GAIN,
                                  unit_example="Db(24.0)")
        given = (("level", level), ("input_zmode", impedance),
                 ("input_type", input_type), ("ground_lift", ground_lift))
        result = self._io_port_fields("in_port", "input_port_id", input_port_id,
                                      given)
        if not confirm:
            return result
        wanted = [(name, value) for name, value in given if value is not None]
        deadline = time.monotonic() + timeout
        seen = {}
        while time.monotonic() < deadline:
            time.sleep(1.0)
            io = self.io_settings(timeout=min(10.0, timeout))
            port = next((x for x in io.settings.in_port
                         if x.input_port_id == input_port_id), None)
            if port is None:
                continue
            seen = {name: getattr(port, name) for name, _ in wanted}
            if all(abs(seen[name] - float(value)) <= 1e-4
                   for name, value in wanted):
                return io
        raise TimeoutError(
            f"input port {input_port_id} still reads {seen} after {timeout}s where "
            f"{dict(wanted)} was written. Reads here are eventually consistent - "
            f"the first read after a write can return the old value even when the "
            f"write landed - so this may be extreme lag rather than a refusal; "
            f"read io_settings() again before concluding anything."
        )

    def set_output_port(self, output_port_id: int, level=None,
                        ground_lift: float | None = None, mute: bool | None = None):
        """Change one output port's settings, sparsely.

        ``level``, ``ground_lift`` and ``mute`` are all confirmed writable.
        ``level`` takes ``Encoded`` only: nobody has read an output port's
        screen against its wire value, so no dB span is known for it. A `Db`
        raises rather than converting against a guess.

        **Each field is sent in its own message.** The device drops ``mute`` when it
        shares a port entry with another field, which is why an earlier version of
        this library reported mute as unwritable. Sending one at a time avoids having
        to know which combinations are safe.
        """
        if level is not None:
            level = _setting_wire(level, "an output port's LEVEL")
        return self._io_port_fields(
            "out_port", "output_port_id", output_port_id,
            (("level", level), ("ground_lift", ground_lift), ("mute", mute)))

    def _io_port_fields(self, collection, key, port_id, fields):
        """Send each given port field in a message of its own.

        The device silently drops some fields that arrive alongside another in the
        same port entry - output ``mute`` and input ``input_zmode`` both failed when
        paired and both worked alone. Rather than track which combinations are safe,
        every field goes separately.
        """
        given = [(name, value) for name, value in fields if value is not None]
        if not given:
            raise TypeError(f"nothing to set on {collection} {port_id}")
        for name, value in given:
            msg = pa.IOSettingsMessage(action=pa.MessageAction.UPDATE)
            port = getattr(msg.settings, collection).add()
            setattr(port, key, port_id)
            setattr(port, name, value)
            self._t.send(msg)

    def set_usb_port(self, level=None, hp_select: float | None = None,
                     dry_wet: float | None = None):
        """Change the USB audio settings. All three fields confirmed writable.

        ``dry_wet`` chooses whether USB outputs carry clean DI or processed audio,
        ``hp_select`` which USB channels feed the headphones, and ``level`` the USB
        output level. The first two are selectors and stay plain; ``level`` takes
        ``Encoded``, since no dB span has been measured for it.

        **One field per message, like the other I/O ports.** Sending ``level`` and
        ``dry_wet`` in a single message applied the level and silently dropped the
        dry/wet; sent separately both land. So this sends one message per field
        given, in the order listed above.
        """
        if level is not None:
            level = _setting_wire(level, "the USB output LEVEL")
        given = [(name, value) for name, value in
                 (("level", level), ("hp_select", hp_select), ("dry_wet", dry_wet))
                 if value is not None]
        if not given:
            raise TypeError("nothing to set on the USB port")
        for name, value in given:
            msg = pa.IOSettingsMessage(action=pa.MessageAction.UPDATE)
            setattr(msg.settings.usb_port, name, value)
            self._t.send(msg)

    def set_midi_thru(self, enabled: bool):
        """Turn MIDI Thru on or off. Confirmed writable (the field is a float)."""
        msg = pa.IOSettingsMessage(action=pa.MessageAction.UPDATE)
        msg.settings.midi_port.midi_thru = 1.0 if enabled else 0.0
        return self._t.send(msg)

    def set_output_pairing(self, xlr1_2: bool | None = None, out3_4: bool | None = None):
        """Pair or unpair the output couples, which makes them share settings.

        The manual's "hold OUTPUTS 1/2 or 3/4 to pair or unpair them". Paired
        outputs share level, ground lift and mute. Confirmed writable.
        """
        msg = pa.IOSettingsMessage(action=pa.MessageAction.UPDATE)
        if xlr1_2 is not None:
            msg.xlr1_2_linked = xlr1_2
        if out3_4 is not None:
            msg.out3_4_linked = out3_4
        return self._t.send(msg)

    def tuner(self, timeout: float = 10.0):
        """The tuner's state.

        Reports ``input_port_id``, ``frequency`` and ``mute``.

        ``frequency`` is the reference-pitch OFFSET from 440 Hz, not the detected
        pitch: it reads 0 with a standard reference and moving FREQ to 442 on the
        unit broadcasts 1.99999809. Write it with :meth:`set_tuner_reference`.

        **This read is blind to the engaged-tuner state** that host writes create
        (see :meth:`set_tuner_mute`): every field reads back normally while the
        rig is silenced by it.

        ``enable_meter`` and ``meter`` are the needle feed, and ``enable_meter``
        refuses a write from here - it stays false and ``meter`` stays 0.0 - so the
        live needle is not readable over USB. ``mute`` and ``input_port_id`` both
        write; see :meth:`set_tuner_mute` and :meth:`set_tuner_input`.
        """
        return self._read_state(pa.TunerMessage,
                                lambda m: m.HasField("input_port_id"), timeout)

    def show_tuner(self, shown: bool = True):
        """Send ``ShowTuner{show}``. **Measured to do NOTHING on firmware d14e.**

        A field session with a person at the unit checked both directions:
        ``show_tuner(True)`` displayed nothing on screen and engaged nothing, and
        ``show_tuner(False)`` did NOT release the invisible engaged-tuner state
        that :meth:`set_tuner_input`/:meth:`set_tuner_mute` create - it is not the
        escape hatch it looks like, and believing it was cost that session a
        debugging round.

        And there is nothing better to find: a capture session established that a
        physical tuner close broadcasts NOTHING, so no host message opens or
        closes the tuner on this firmware. `Tuner{DELETE}` and
        `ShowTuner{DELETE}` were tried too, and left the rig silent. Use
        :meth:`restore_audio` to get sound back. This method is kept only so the
        no-op is documented rather than rediscovered; do not build on it.
        """
        return self._t.send(pa.ShowTunerMessage(action=pa.MessageAction.UPDATE,
                                                show=shown))

    def set_tuner_input(self, input_port_id: int):
        """Choose which input feeds the Tuner.

        What the device ACCEPTS, from a full sweep of the :class:`Input` enum with
        settled read-backs: the four single inputs (``INPUT_1``, ``INPUT_2``,
        ``RETURN_1``, ``RETURN_2``), the combined ``INPUT_1_2``, and ``USB_5`` /
        ``USB_6``. Everything else is REFUSED and the setting reverts - including
        ``RETURN_1_2``, so there is no combined-returns tuning and no mode covering
        all four inputs; that is a device limit, not a library gap. The whole
        accepted set matches the unit's own picker one for one, USB included.

        **WARNING - writing this ENGAGES the tuner, invisibly**, even with the mute
        preference untouched; if that preference is already true, THE OUTPUTS GO
        SILENT. Only a person opening and closing the tuner on the unit releases
        the state. Full description on :meth:`set_tuner_mute`; list entry under
        "Settings only your ears can verify" in the API guide.
        """
        self._warn_if_tuner_write_silences()
        return self._t.send(pa.TunerMessage(action=pa.MessageAction.UPDATE,
                                            input_port_id=input_port_id))

    def set_tuner_mute(self, muted: bool):
        """Set the Tuner's mute-while-tuning preference. Confirmed writable.

        The manual's MUTE control in the Tuner menu, so silent tuning on stage.
        The flag itself is a persistent preference and mutes nothing on its own.

        **WARNING - writing this ENGAGES the tuner, invisibly.** Any host write to
        the Tuner subsystem puts the unit into an engaged-tuner state that never
        appears on screen; engaged plus ``muted=True`` means THE OUTPUTS GO SILENT
        with no visible cause. Field-measured: the state survived ~100 recalls, 60
        saves and every scene switch of a 33-minute build, read-back is blind to
        it (this flag reads back exactly as written while the rig is silent), and
        the lossless release is a person opening and closing the tuner on the
        unit - :meth:`show_tuner` does not do it, and **no disengage message
        exists**: a capture session established that the physical close
        broadcasts nothing at all, so a host cannot send it.

        From the host, :meth:`restore_audio` is the escape hatch - it clears this
        preference, leaving the unit engaged but AUDIBLE (engagement alone is
        harmless, verified by ear). The cost is the preference itself. Call it at
        the end of anything that touches the tuner. See "Settings only your ears
        can verify" in the API guide.
        """
        if muted:
            warnings.warn(
                "set_tuner_mute(True) engages the tuner invisibly and SILENCES "
                "the outputs, with nothing on screen to explain it. Call "
                "restore_audio() when done, or have someone close the tuner on "
                "the unit (the only release that keeps the preference).",
                stacklevel=2,
            )
        return self._t.send(pa.TunerMessage(action=pa.MessageAction.UPDATE,
                                            mute=muted))

    def _warn_if_tuner_write_silences(self):
        """Warn when a tuner write is about to leave the outputs muted."""
        try:
            muted = self.tuner(timeout=3.0).mute
        except Exception:
            return          # never let the courtesy check break the write
        if muted:
            warnings.warn(
                "this tuner write engages the tuner invisibly, and the mute "
                "preference is currently SET, so the outputs will go silent with "
                "nothing on screen to explain it. Call restore_audio() when done.",
                stacklevel=3,
            )

    def restore_audio(self, timeout: float = 10.0) -> bool:
        """Undo the silence a host tuner write causes. Returns True if it acted.

        Any host write to the Tuner engages an invisible tuner state, and while
        the mute PREFERENCE is set that state silences the outputs with nothing
        on screen to explain it (see :meth:`set_tuner_mute`). This is the only
        host-side release: it clears the preference, which leaves the unit
        engaged-but-audible - measured, engagement alone is harmless.

        **The cost is real:** it discards the player's silent-tuning preference,
        so the next time they open the tuner it will not mute. The lossless
        release is a person closing the tuner on the unit, which restores audio
        AND keeps the preference. There is no message for that close - it
        broadcasts nothing at all (captured twice), so a host cannot send it.

        Call this at the end of anything that touched the tuner, or leave the
        preference off for the whole run. An automated build has no business
        leaving an instrument silent.
        """
        if not self.tuner(timeout=timeout).mute:
            return False
        self.set_tuner_mute(False)
        return True

    def looper(self, timeout: float = 10.0):
        """The Looper X block's state, if one is on the grid.

        A ``Looper`` message whose ``status`` carries ``state``, ``progress``,
        ``loop_length``, ``free_samples``, ``armed``, ``in_reverse``,
        ``half_speed``, ``undo_count``, ``redo_available`` and more, plus the
        top-level ``one_shot_play``, ``sync_start_waiting`` and
        ``quantize_enabled``.

        ``status.state`` values are in :class:`~pyquadcortex.protocol.enums.LooperState`,
        mapped by watching each transport control being pressed in a known order.
        Two things worth knowing from that session: with nothing plugged in the
        Looper sits in ``ARMED`` forever and its other controls stay inert, since
        RECORD waits for a signal to cross the threshold; and REVERSE and HALF
        SPEED do not change ``state`` at all, they set ``in_reverse`` and
        ``half_speed`` while playback continues.

        Readable only - the transport is not driven from here. The manual notes
        MIDI CC#48-61 control the Looper, which is the other available route.
        """
        return self._read_state(pa.LooperMessage, lambda m: m.HasField("status"),
                                timeout)

    def set_input_level(self, input_port_id: int, level):
        """Set an input port's gain.

        ``level`` says which scale it is on, like every other value in this
        library: ``Db(24.0)`` for the screen's, ``Encoded(0.5)`` for the
        device's. The span is **-12..+60 dB** and it is measured - see
        :func:`input_level_db`::

            qc.set_input_level(Input.INPUT_1, Db(24.0))

        Sparse and keyed by ``input_port_id``, so other ports are untouched -
        confirmed on hardware, where writing one input's level left the other
        three byte-identical. Read :meth:`io_settings` first if you mean to
        restore it: these are global, and an input gain is the kind of setting a
        player has dialled in by ear.
        """
        return self.set_input_port(input_port_id, level=level)

    def set_output_level(self, output_port_id: int, level):
        """Set an output port's level.

        ``Encoded`` only, and that is a statement about what is known rather
        than a preference: nobody has read an output port's screen against its
        wire value, so there is no span to convert dB against. A `Db` here
        raises :class:`ControlNotDrivable` naming what would settle it, instead
        of answering with an invented number.
        """
        return self.set_output_port(output_port_id, level=level)

    def global_eq(self, timeout: float = 10.0):
        """The Global EQ state: ``bypassed`` plus its five bands."""
        return self._read_state(pa.GlobalEQMessage,
                                lambda m: m.HasField("bypassed"), timeout)

    def set_global_eq_bypassed(self, bypassed: bool = True):
        """Turn the Global EQ off or on. Confirmed writable on hardware.

        ``bypassed=True`` is the EQ OFF, which is how the observed unit ships. The
        unit's own On/Off control is the inverse of this flag.

        Note that the unit disables the Global EQ by itself when a preset runs out
        of processing headroom, which arrives as
        ``CompilerInhibitedModules{global_eq}``.
        """
        return self._t.send(pa.GlobalEQMessage(action=pa.MessageAction.UPDATE,
                                               bypassed=bypassed))

    def mode(self, timeout: float = 10.0):
        """The footswitch mode state: which slot is active, and which exist.

        ``mode`` identifies the active SLOT, not a fixed mode - the slots are
        user-arranged, so slot 0 is not necessarily PRESET mode.
        ``available_modes.modes`` lists the slots configured, three by default.

        A merged HYBRID slot appears as a single composite value: a unit with Preset
        and Stomp merged reported ``available_modes{7, 1}`` and cycling alternated
        ``mode: 7`` and ``mode: 1``. See :meth:`set_mode_cycle`.

        **This may not carry the cycle.** Mode pushes are often PARTIAL - a switch
        broadcasts ``mode`` alone - so ``available_modes.modes`` can read empty here
        even though slots are configured. Use :meth:`mode_cycle` to read the cycle;
        it waits for a push that actually contains one.
        """
        return self._read_state(pa.ModeMessage, lambda m: m.HasField("mode"),
                                timeout)

    def mode_cycle(self, timeout: float = 10.0) -> list:
        """The configured footswitch mode slots, in cycle order.

        Waits for a push that CONTAINS the cycle, rather than the first one
        mentioning modes at all. :meth:`mode` accepts any push carrying ``mode``,
        and the device frequently sends that field alone - so reading the cycle
        through it can hand back an empty list from a partial push and make a
        perfectly good configuration look empty. That misread produced two
        contradictory answers about which composite values the device accepts.
        """
        state = self._read_state(
            pa.ModeMessage, lambda m: len(m.available_modes.modes) > 0, timeout)
        return list(state.available_modes.modes)

    def set_mode(self, slot: int):
        """Switch to a footswitch mode SLOT by index. Confirmed on hardware.

        See :meth:`mode` on why this is a slot rather than a named mode.
        """
        return self._t.send(pa.ModeMessage(action=pa.MessageAction.UPDATE,
                                           mode=slot))

    def set_gig_view(self, shown: bool = True):
        """Open or close Gig View on the unit. Confirmed on hardware."""
        return self._t.send(pa.ShowGigViewMessage(action=pa.MessageAction.UPDATE,
                                                  show=shown))

    def capture_screen(self, timeout: float = 10.0) -> bytes:
        """Return a PNG of the unit's current physical display.

        Confirmed at 800 x 480 on QC CorOS 4.1.0. CorOS answers
        ``RemoteControl{READ, screenshot:{}}`` with an asynchronous,
        uncorrelated ``UPDATE`` carrying the PNG, so a type waiter is installed
        before the read. Replies that are not screenshot PNGs are ignored.
        """
        reply = self._t.await_broadcast(
            pa.RemoteControlMessage,
            lambda: self._t.send(pa.RemoteControlMessage(
                action=pa.MessageAction.READ,
                screenshot=pa.RemoteControlScreenshot(),
            )),
            timeout=timeout,
            match=lambda message: (
                message.action == pa.MessageAction.UPDATE
                and message.HasField("screenshot")
                and message.screenshot.HasField("payload")
                and message.screenshot.payload.startswith(b"\x89PNG\r\n\x1a\n")
            ),
        )
        self._remote_control_ready = True
        return bytes(reply.screenshot.payload)

    def tap_screen(self, x: float, y: float) -> None:
        """Tap a raw pixel coordinate on the unit's 800 x 480 touchscreen.

        The coordinate space and two-message gesture were confirmed on QC
        CorOS 4.1.0. Runtime semantics are inverted against the recovered enum
        labels: wire value ``RELEASE=1`` begins the touch and ``PRESS=0`` ends
        it. The nominal ``TAP`` type did not honour its coordinates.

        The pair is fire-and-forget. The first call primes CorOS' remote-control
        surface with :meth:`capture_screen`; the transport then sends the pair
        atomically after the measured settle and inter-message intervals.
        Prefer a semantic control where one exists.
        """
        for name, value, upper in (("x", x, 800), ("y", y, 480)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be a real pixel coordinate")
            if not math.isfinite(value) or not 0 <= value < upper:
                raise ValueError(f"{name} must be in the range 0 <= {name} < {upper}")

        prime_delay = 0.0
        if not self._remote_control_ready:
            self.capture_screen()
            prime_delay = 0.3
        self._t.send_sequence(
            (
                pa.RemoteControlMessage(
                    action=pa.MessageAction.UPDATE,
                    mouse=pa.RemoteControlMouse(
                        x=x, y=y, type=pa.RemoteControlMouse.RELEASE)),
                pa.RemoteControlMessage(
                    action=pa.MessageAction.UPDATE,
                    mouse=pa.RemoteControlMouse(
                        x=x, y=y, type=pa.RemoteControlMouse.PRESS)),
            ),
            delay=prime_delay,
            interval=0.02,
        )

    def list_folders(self, seconds: float = 20.0) -> list:
        """Every folder the device knows about, as :class:`Folder` entries.

        A single ``File`` READ makes the device enumerate its whole tree, which is
        far more than the two setlists: on the observed unit 399 folders arrive
        over about fifteen seconds. What is in there:

        * ``My Presets`` and the ``Factory Library``, 256 slots each.
        * The **Captures Library** (``local_nc_root``) - 2062 factory captures,
          also grouped into ~180 per-amp folders keyed ``NNN_f`` and named after
          the amp, e.g. ``106_f`` is "Darkglass VMT" with three variants.
        * ``/opt/neuraldsp/impulse_responses``, 588 factory IRs.
        * One folder per installed plugin, each with an ``Artists`` tree of that
          plugin's artist presets.

        Every one of those keys works with :meth:`list_presets`, so this is how a
        caller discovers what is addressable rather than assuming two setlists.

        Takes ``seconds`` rather than a timeout because the answer is "everything
        that arrived", not "the first match".
        """
        pushes = self._t.collect(
            pa.FileMessage,
            lambda: self._t.send(pa.FileMessage(action=pa.MessageAction.READ)),
            seconds,
            match=lambda m: bool(m.folder.key))
        best: dict[str, Folder] = {}
        for m in pushes:
            f = m.folder
            occupied = sum(1 for x in f.files
                           if x.HasField("name") and x.name)
            prev = best.get(f.key)
            entry = Folder(
                key=f.key,
                name=f.name if f.HasField("name") else "",
                slots=len(f.files),
                occupied=occupied,
                is_factory=f.is_factory if f.HasField("is_factory") else False,
            )
            if prev is None or entry.slots > prev.slots:
                best[f.key] = entry
        return sorted(best.values(), key=lambda e: e.key)

    def recents(self, timeout: float = 10.0):
        """The unit's RECENTS list, as a ``RecentsFavorites`` message.

        Each ``items`` entry carries ``name``, ``folder_key`` and ``folder_name``,
        so an entry can be fed straight back to :meth:`find_preset` or
        :meth:`recall_preset`.

        **This is Recents, not Favorites**, even though one message type carries both.
        A plain ``READ`` returns Recents, headed by the most recently saved preset;
        adding ``is_favorites: true`` to the request returns Favorites instead, which
        is what :meth:`favorites` does. Neither REPLY sets the flag, so the two are
        told apart by what you asked for - correlate on ``request_id``.

        **Writing needs ONE ENTRY, not the whole list.** Sending all 51 entries back
        with an extra item does nothing - the device maintains both lists with a
        single-entry ``DELETE`` then ``CREATE`` pair, which is what it does itself on
        every preset recall. See :meth:`add_favorite` and :meth:`remove_favorite`.

        Note the device sometimes answers with an EMPTY push before the real one,
        and occasionally does not answer the first request at all, so this matches
        on a non-empty list and may need a retry - see the stale-read note above.
        """
        return self._read_state(pa.RecentsFavoritesMessage,
                                lambda m: len(m.items) > 0, timeout)

    def _favorite(self, entry, remove, verify, timeout):
        """Add or remove one Favorites entry, optionally waiting for the echo."""
        name = getattr(entry, "name", None)
        folder_key = getattr(entry, "folder_key", None) or getattr(entry, "key", None)
        if not name or not folder_key:
            raise TypeError(
                "a favorite needs an entry carrying name and folder_key - pass an "
                "item from recents(), or a Folder/preset entry"
            )
        msg = pa.RecentsFavoritesMessage(
            action=pa.MessageAction.DELETE if remove else pa.MessageAction.CREATE,
            is_favorites=True)
        item = msg.items.add()
        item.name = name
        item.folder_key = folder_key
        folder_name = (getattr(entry, "folder_name", None)
                       or folder_key.rsplit("/", 1)[-1])
        item.folder_name = folder_name
        item.is_factory = bool(getattr(entry, "is_factory", False))
        if not verify:
            return self._t.send(msg)
        # The device echoes the changed entry back with is_favorites set. That echo
        # is the only confirmation available: the Favorites LIST cannot be read on
        # demand (see recents()), so there is nothing to read back afterwards.
        try:
            return self._t.await_broadcast(
                pa.RecentsFavoritesMessage, lambda: self._t.send(msg), timeout=timeout,
                match=lambda m: (m.is_favorites
                                 and any(i.name == name for i in m.items)))
        except TimeoutError:
            raise TimeoutError(
                f"the device did not acknowledge {'un-' if remove else ''}favouriting "
                f"{name!r} in {folder_name!r}. The entry has to match the device's own "
                f"record - a wrong folder_key or is_factory is IGNORED SILENTLY, which "
                f"is what this echo exists to catch. Pass an item straight from "
                f"recents() rather than building one: 'Fuzz This' lives in "
                f"/opt/neuraldsp/Factory Library with is_factory=True, and naming it "
                f"under My Presets produced exactly this timeout."
            ) from None

    def add_favorite(self, entry, verify: bool = True, timeout: float = 10.0):
        """Mark a preset as a Favorite.

        ``entry`` is anything carrying ``name`` and ``folder_key`` - an item from
        :meth:`recents`, or a preset entry from :meth:`list_presets` paired with its
        folder. On the unit this is multiselect plus the heart button, and **only
        presets can be favourited**; there is no favouriting of captures or IRs.

        The list is maintained one ENTRY at a time. Sending the whole list back with
        an extra item does nothing at all, which is what made this look read-only at
        first.

        **Pass the device's own entry, not one you built.** The name, ``folder_key``
        and ``is_factory`` must match its record; a mismatch is ignored in silence.
        "Fuzz This" lives in the Factory Library, and naming it under My Presets
        produced no error and no favourite.

        With ``verify`` (the default) this waits for the device to echo the change
        back and raises ``TimeoutError`` if it does not, which is what turns that
        silent mismatch into a visible failure. The echo is the only confirmation
        available - :meth:`recents` cannot read the Favorites list.
        """
        return self._favorite(entry, remove=False, verify=verify, timeout=timeout)

    def remove_favorite(self, entry, verify: bool = True, timeout: float = 10.0):
        """Un-favourite a preset. See :meth:`add_favorite` for the argument."""
        return self._favorite(entry, remove=True, verify=verify, timeout=timeout)

    def favorites(self, timeout: float = 20.0, attempts: int = 3):
        """The unit's FAVORITES, as a list of entries - possibly empty.

        Ask with the flag set and the device answers with the Favorites list::

            RecentsFavorites{READ, is_favorites: true, request_id: N}

        Each entry carries ``name``, ``folder_key``, ``folder_name`` and
        ``is_factory``, so it can be fed straight to :meth:`recall_preset`,
        :meth:`find_preset` or :meth:`remove_favorite`.

        **The reply does NOT set `is_favorites`.** Both lists come back with the flag
        absent, so a predicate like ``m.is_favorites == True`` rejects every valid
        answer - which is exactly how an earlier version of this library concluded the
        Favorites list could not be read at all. Correlate on ``request_id``, which the
        device does echo, and which is what this method matches on.

        An empty Favorites list answers with a real, empty push rather than silence, so
        ``[]`` here means "no favourites", not "no answer". The first request after
        connecting is sometimes dropped, so this retries up to ``attempts`` times before
        raising ``TimeoutError``.
        """
        last = None
        for _ in range(max(1, attempts)):
            request_id = self._t.next_request_id()
            message = pa.RecentsFavoritesMessage(
                action=pa.MessageAction.READ, is_favorites=True,
                request_id=request_id)
            try:
                reply = self._t.await_broadcast(
                    pa.RecentsFavoritesMessage,
                    lambda m=message: self._t.send(m),
                    timeout=timeout / max(1, attempts),
                    match=lambda m: (m.HasField("request_id")
                                     and m.request_id == request_id))
                return list(reply.items)
            except TimeoutError as exc:
                last = exc
        raise TimeoutError(
            f"the device did not answer a Favorites read in {attempts} attempts "
            f"({timeout}s total). Reads are lazy - the first request after connecting "
            f"is often dropped - so this is usually worth retrying rather than a sign "
            f"that Favorites is unreadable."
        ) from last

    def set_master_volume_assignment(self, out12: bool | None = None, out34: bool | None = None,
                                     send12: bool | None = None, headphones: bool | None = None):
        """Choose which outputs the Master Volume knob governs.

        The manual's checkboxes in the Master Volume overlay. Confirmed writable.

        **A submessage write REPLACES the whole submessage.** Unlike the top-level
        fields of ``GeneralSettings``, which are sparse, sending
        ``master_volume_assignment`` with one field set leaves the other three
        FALSE - which quietly stops the knob controlling those outputs. So this
        reads the current assignment first and sends all four, with ``None``
        meaning "leave as it is".
        """
        current = self.settings().master_volume_assignment
        msg = pa.GeneralSettingsMessage(action=pa.MessageAction.UPDATE)
        target = msg.master_volume_assignment
        for name, value in (("out12", out12), ("out34", out34),
                            ("send12", send12), ("headphones", headphones)):
            setattr(target, name,
                    getattr(current, name) if value is None else value)
        return self._t.send(msg)

    def set_global_bypass(self, cab=None, ir=None):
        """Bypass Cab or IR Loader blocks across ALL presets, per grid row.

        ``cab`` and ``ir`` are four booleans, one per row, or ``None`` to leave
        that one alone. The manual's GLOBAL BYPASS. Confirmed writable.

        Subject to the same whole-submessage rule as
        :meth:`set_master_volume_assignment`, so the current value is read and
        merged rather than sent piecemeal.
        """
        if cab is None and ir is None:
            raise TypeError("set_global_bypass needs cab= or ir=")
        current = self.settings()
        msg = pa.GeneralSettingsMessage(action=pa.MessageAction.UPDATE)
        for field, rows in (("global_bypass_cab", cab), ("global_bypass_ir", ir)):
            if rows is None:
                continue
            if len(rows) != 4:
                raise ValueError(f"{field} needs four booleans, one per row")
            target = getattr(msg, field)
            for i, value in enumerate(rows, start=1):
                setattr(target, f"row{i}", bool(value))
        # carry the untouched one through unchanged
        if cab is None:
            msg.global_bypass_cab.CopyFrom(current.global_bypass_cab)
        if ir is None:
            msg.global_bypass_ir.CopyFrom(current.global_bypass_ir)
        return self._t.send(msg)

    def set_global_eq_band(self, parameter_index: int, value):
        """Set one Global EQ parameter, by its wire index.

        ``Encoded`` only, and necessarily so: this addresses a parameter by a
        raw index whose MEANING is not established, so there is nothing to say
        what scale it would be on. :meth:`set_global_eq` knows which offset is
        a band's GAIN and takes ``Db`` there.

        The Global EQ reports 28 ``parameters`` entries, each
        ``{parameter_index, value}``. Confirmed writable and sparse: writing index
        1 left the rest alone. Which index is which band's type, gain, frequency
        or Q is not established, so read :meth:`global_eq` and compare rather than
        guessing.
        """
        msg = pa.GlobalEQMessage(action=pa.MessageAction.UPDATE)
        prm = msg.parameters.add()
        prm.parameter_index = parameter_index
        prm.value = _setting_wire(
            value, f"Global EQ parameter {parameter_index}")
        return self._t.send(msg)

    def set_mode_cycle(self, slots):
        """Set which footswitch mode slots are in the cycle, and their order.

        The manual's Modes Configuration menu, where slots are dragged to reorder.
        Confirmed writable: sending ``[1, 0, 2]`` read back in that order. The whole
        list is replaced, which matches the feature - it IS the cycle.

        **A HYBRID slot is just another value in this list**, and all six are now
        mapped - build them with :func:`~pyquadcortex.protocol.hybrid_mode`::

            from pyquadcortex.protocol import FootswitchMode as Mode, hybrid_mode
            qc.set_mode_cycle([hybrid_mode(Mode.PRESET, Mode.STOMP), Mode.SCENE])

        A hybrid gives footswitches A-D one mode and E-H another, so the composite
        encodes an ORDERED pair - 3 to 8, the six pairs in lexicographic order over
        PRESET, SCENE, STOMP. 4 and 7 are the two arrangements of Preset/Stomp, which
        is the manual's "tap the right edge to swap the Modes rows". See
        :data:`~pyquadcortex.protocol.HYBRID_MODES` for the table and
        :func:`~pyquadcortex.protocol.describe_mode` to name a value.

        Two limits, both measured:

        * **A cycle holds at most ONE composite.** ``[3, 4, 5]`` comes back as ``[3]``,
          and a composite cannot be the only slot either - ``[7]`` alone is refused and
          the device reverts to its default. Pair a hybrid with a base mode.
        * **9 is refused here.** The device ACCEPTS it, and it is broken: the indicator
          reads "<blank> + Scene" and the footswitches stop responding altogether. 10
          and above the device rejects itself. So the range it accepts is wider than the
          range that works, and this method will not send the difference.

        Note the merge only became visible after the menu was CONFIRMED on the unit: an
        earlier session merged without pressing OK and the device broadcast nothing at
        all.
        """
        values = [int(s) for s in slots]
        broken = [v for v in values if v == enums.BROKEN_MODE_VALUE]
        if broken:
            raise ValueError(
                f"mode value {enums.BROKEN_MODE_VALUE} is accepted by the device but "
                f"leaves the footswitches non-functional (the indicator shows "
                f"'<blank> + Scene'), so it is not sent. Valid slots are 0-2 for the "
                f"base modes and 3-8 for the hybrids - see HYBRID_MODES."
            )
        unknown = [v for v in values if v < 0 or v > 8]
        if unknown:
            raise ValueError(
                f"the device rejects mode values above 9; {unknown} would be dropped "
                f"from the cycle. Valid slots are 0-2 and 3-8."
            )
        hybrids = [v for v in values if v in enums.HYBRID_MODES]
        if len(hybrids) > 1:
            raise ValueError(
                f"a cycle holds at most one HYBRID slot; {hybrids} were given and the "
                f"device would keep only the first"
            )
        if hybrids and len(values) == 1:
            raise ValueError(
                f"a HYBRID cannot be the only slot - the device refuses it and reverts "
                f"to its default. Pair {values[0]} "
                f"({enums.describe_mode(values[0])}) with a base mode, e.g. "
                f"[{values[0]}, {int(enums.FootswitchMode.SCENE)}]"
            )
        msg = pa.ModeMessage(action=pa.MessageAction.UPDATE)
        msg.available_modes.modes.extend(values)
        return self._t.send(msg)

    def set_param_option(self, block, param, option,
                         source: preset.BinaryPreset | None = None):
        """Choose a list-valued parameter's option.

        List parameters store ``index / (count - 1)``, so choosing one means
        knowing its position. Pass an enum from
        :mod:`pyquadcortex.protocol.options`, which names them::

            block = protocol.blocks(preset)[0]      # carries its model_id
            qc.set_param_option(block, "DYN MODE", options.DynMode3.GATE)

        An option NAME or a bare index works too. A name is matched against the
        device's own spelling, typos included - see ``options.OPTION_LABELS``.

        **``source=`` is only needed for a DYNAMIC list.** Twelve parameters
        build their options from the preset, because the list can include one
        entry per block earlier in the chain, and the catalog's ``steps``
        overstates it. A side-chain SOURCE is the case to know::

            p = qc.read_preset(Setlist.USER, "30A")
            qc.set_param_option(Block(1, 0), "SOURCE", "Input 2", source=p)

        Everything else reads its options from the catalog. This docstring used
        to say the names were "not in the catalog - they are in the preset, per
        block", and that was wrong for 527 of the 539 lists; ``stepNames`` had
        them all along.

        ``param`` may be a wire index or a parameter NAME. With ``source=``, the
        block's model is taken from the preset, so no ``model=`` is needed.

        Confirmed on hardware both ways: the unit stored 0.2 for "Input 2" out of
        16 options when set on screen, and a host write of 3/17 out of 18 options
        read back as "Input 2". Confirmed for a fixed list on 2026-08-26: wire
        0.25 on a Low-High Cut's HPF SLOPE, option 2 of 9, showed "-12 dB/o".
        """
        model_id = block.model_id
        if model_id is None and source is not None:
            model_id = next((b.model_id for b in blocks(source)
                             if b.row == block.row and b.column == block.column),
                            None)
        index = param
        if isinstance(param, str):
            if model_id is None:
                raise ValueError(
                    f"no block at row {block.row} column {block.column} in "
                    f"the preset given as source=")
            index = self.catalog[model_id].parameter(param).index

        # The preset first when one was given: it is authoritative for a dynamic
        # list, it agrees with the catalog on the rest, and reading it costs
        # nothing. The catalog comes over USB, so a caller who already handed us
        # the answer should not pay for a round trip.
        names = tuple(param_options(source, block, index)) if source else ()
        dynamic = False
        if not names and model_id is not None:
            model = self.catalog.get(model_id)
            if model is not None and index < len(model.parameters):
                spec = model.parameters[index]
                # A DYNAMIC list's stepNames are a snapshot and the catalog's
                # own count overstates it - a Doubler's TRIGGER publishes 45
                # while the real list is 19 to 25 depending on the preset. Using
                # them would pick the right NAME at the wrong POSITION: option 1
                # of 45 is wire 0.0227, which against a real 19-entry list reads
                # back as option 0. Silently the wrong choice, and the device
                # says nothing.
                dynamic = spec.dynamic
                names = () if dynamic else spec.options
        if not names:
            raise ValueError(
                f"index {index} on row {block.row} column {block.column} "
                + ("builds its options from the PRESET - the list includes one "
                   "entry per block ahead of it, so the catalog's copy is a "
                   "snapshot at the wrong length. Pass source=<the preset you "
                   "read>." if dynamic else
                   "offers no options here. A dynamic list - one whose entries "
                   "include the blocks ahead of it - is only in the preset, so "
                   "pass source=<the preset you read>.")
            )
        if isinstance(option, bool):
            raise TypeError(
                f"True and False cannot name an option out of {len(names)}: "
                f"{names[:4]}{'...' if len(names) > 4 else ''}. A bool would be "
                f"read as the index 0 or 1. Name the option, or use an enum from "
                f"pyquadcortex.protocol.options."
            )
        # An IntEnum member is an int, so a member of the WRONG list converts
        # silently: DynMode3.GATE and SplitterType.CROSSOVER are both 2, and
        # both would be accepted here. Check the enum describes THIS list.
        labels = options_module.OPTION_LABELS.get(type(option))
        if labels is not None and tuple(labels) != tuple(names):
            raise TypeError(
                f"{type(option).__name__}.{option.name} belongs to a different "
                f"list. This parameter offers {list(names)[:4]}"
                f"{'...' if len(names) > 4 else ''}, and that enum describes "
                f"{list(labels)[:4]}{'...' if len(labels) > 4 else ''}. An "
                f"IntEnum member is an int, so without this the wrong one would "
                f"convert quietly."
            )
        return self.set_param(block, index,
                              values_module.Encoded(option_value(names, option)))

    def set_output_mute(self, output_port_id: int, muted: bool = True):
        """Mute or unmute an output port.

        **Send it alone.** This is the same field :meth:`set_output_port` exposes,
        but the device drops it when it arrives alongside another field in the same
        port entry: a message carrying ``mute`` and ``ground_lift`` together left
        the port unmuted, while ``mute`` on its own worked. That is why this is a
        separate method - the shape came from watching the unit's own broadcast,
        which sends nothing but ``{output_port_id, mute}``.
        """
        return self.set_output_port(output_port_id, mute=muted)

    def set_tuner_reference(self, offset_hz):
        """Set the tuner's reference pitch, as an OFFSET in Hz from 440.

        Takes ``Hertz(2.0)``. The wire carries the Hz offset itself rather than
        a 0..1 position, so ``Encoded`` has nothing to mean here and is refused.

        ``Tuner.frequency`` is not the absolute reference pitch: changing FREQ from
        440 to 442 on the unit broadcast ``frequency: 1.99999809``. So pass 2.0 for
        442 Hz and 0.0 for 440 Hz. Confirmed writable - 5.0 round-tripped - and
        restored to 0.

        That the scale is Hz rather than cents or steps rests on the single
        observed pair (442 -> 2.0); it has not been checked against a second value
        on screen.
        """
        offset_hz = _setting_real(offset_hz, "the tuner reference offset",
                                  values_module.Hertz)
        return self._t.send(pa.TunerMessage(action=pa.MessageAction.UPDATE,
                                            frequency=offset_hz))

    def create_setlist(self, name: str):
        """Create a new setlist, which the unit's Directory calls a folder.

        Setlists live SIDE BY SIDE under ``/media/p4/Presets``, not nested inside
        "My Presets" - which is what made an earlier attempt fail. Confirmed by
        watching the unit create one and then doing the same from the host:

            File{CREATE, type: 0, folder{key: "/media/p4/Presets/<name>",
                                         name: "<name>", is_factory: false}}

        The new key works everywhere a setlist path does, so presets can be saved
        into it with :meth:`save_current_preset`. This is also what the MIDI
        documentation's 'User folders' are - they are created, not built in.
        """
        msg = pa.FileMessage(type=0)
        msg.folder.key = f"{USER_SETLIST_ROOT}/{name}"
        msg.folder.name = name
        msg.folder.is_factory = False
        self._file_operation(msg)
        return f"{USER_SETLIST_ROOT}/{name}"

    def master_volume(self, timeout: float = 10.0):
        """The Master Volume state.

        ``volume`` is normalized 0..1 and the unit displays ``round(volume * 100)``
        - 0.566115677 shows as 57, not 56. The knob quantizes in steps of 1/121.

        Writable through :meth:`set_master_volume`. This docstring said "read-only"
        for several releases on the strength of a measurement that was really a
        stale read; see that method.
        """
        return self._read_state(pa.MasterVolumeMessage,
                                lambda m: m.HasField("volume"), timeout)

    def set_master_volume(self, volume):
        """Set the Master Volume.

        ``Encoded`` only. The unit displays 0-100 with no unit, and nobody has
        established what that number IS - a percentage of travel, or a dB
        taper - so there is no screen scale to convert. That is also why the
        typed value earns its keep here more than anywhere: the classic mistake
        is passing 30 meaning "30 on screen", and now the argument has to say
        which line it is on before it is looked at.

        The whole write is ``MasterVolume{UPDATE, volume}``. It lands on its own
        with no companion field, and it is a real level change: a host write of
        0.30 took the unit's overlay to 30 and audibly dropped the output.

        **This corrects a recorded finding.** Earlier work measured the write as
        accepted-and-ignored. That was a stale read rather than a refusal -
        :meth:`master_volume` called straight after a write returns the PREVIOUS
        value, so write-then-read reports every result one step late. Reconnect,
        or wait, before believing a read-back.

        After a host write the physical knob **soft-takes-over**: it does nothing
        until turned past the value that was set, then resumes control. That is
        exactly what the manual describes Cortex Control doing when it "adjusts
        output level and temporarily deactivates the hardware wheel", so Cortex
        Control is writing this field rather than using some other route.

        Master volume is a gain stage of its own, applied downstream of the stored
        port levels - writing it changes no ``IOSettings`` level. Use
        :meth:`set_master_volume_assignment` to choose which outputs it governs.

        Out-of-range values are REJECTED rather than sent. The wire is 0..1 while
        the unit displays 0-100, so ``set_master_volume(Encoded(30))`` is the
        obvious mistake, and what the device does with 30.0 is unknown on a
        control that feeds an amplifier. Same reasoning as
        :meth:`set_hold_timing`: a field the device does not range-check itself is
        one this library range-checks for it.

        .. warning::
           Never send ``calibrate`` alongside a level. It is an ACTION, not a
           flag: it opens the full-screen Master Volume Calibration dialog and
           waits for the owner to sweep the knob and tap SAVE. This method never
           sends it.
        """
        # The screen-number mistake gets its own message BEFORE anything else,
        # because a caller who read 30 off the unit needs the number to pass,
        # not a lecture on types. Typed or bare: `set_master_volume(30)` was
        # the motivating bug for ADR-0017 and it should not take two attempts
        # to be told the answer.
        if (isinstance(volume, (int, float))
                and not isinstance(volume, values_module.Real)
                and 1.0 < float(volume) <= 100.0):
            raise ValueError(
                f"master volume is normalized 0..1, not {volume!r}. The unit "
                f"displays round(volume * 100), so pass "
                f"Encoded({float(volume) / 100:.2f}) for {float(volume):.0f} "
                f"on screen."
            )
        level = _setting_wire(volume, "the master volume")
        msg = pa.MasterVolumeMessage(action=pa.MessageAction.UPDATE)
        msg.volume = level
        return self._t.send(msg)

    def pinned_models(self, timeout: float = 8.0):
        """Which models are pinned to the top of their category, as ids."""
        msg = self._read_state(pa.PinnedModelsMessage, lambda m: True, timeout)
        return list(msg.models) if msg is not None else []

    def pin_model(self, model):
        """Pin a model to the top of its category in the device list.

        Note the shape: the unit's own broadcast carries **no action field**, and
        an UPDATE does nothing - which is why an earlier attempt looked refused.

        **Pinning APPENDS.** The list is not replaced and not de-duplicated:
        pinning something already pinned leaves two entries for it. Check
        :meth:`pinned_models` first if that matters.
        """
        msg = pa.PinnedModelsMessage()
        msg.models.append(int(getattr(model, "id", model)))
        return self._t.send(msg)

    def unpin_model(self, model):
        """Unpin a model, removing EVERY entry for it.

        ``PinnedModels`` with action DELETE. Removes all occurrences, which is how
        a duplicated pin gets cleaned up.
        """
        msg = pa.PinnedModelsMessage(action=pa.MessageAction.DELETE)
        msg.models.append(int(getattr(model, "id", model)))
        return self._t.send(msg)

    def delete_setlist(self, name: str):
        """Delete a setlist and whatever it holds.

        ``File{DELETE, folder{key, name}}`` against the setlist's own key.
        Confirmed: the folder disappears from the listing. Like the other file
        operations this is eventually consistent, so re-enumerate rather than
        checking immediately.
        """
        msg = pa.FileMessage(action=pa.MessageAction.DELETE, type=0)
        msg.folder.key = f"{USER_SETLIST_ROOT}/{name}"
        msg.folder.name = name
        return self._file_operation(msg)

    def copy_preset(self, from_setlist: str, position, to_setlist: str,
                    to_position=None, name: str | None = None,
                    instrument: int = Instrument.NONE):
        """Copy a preset into another setlist.

        Not a device operation - the unit has no host-drivable copy - but a
        composition of two that are: this RECALLS the source preset and then saves
        the grid into the destination. Which is exactly what the unit's own
        copy/paste turns out to be: pasting broadcasts the same
        ``File{CREATE, folder{key, files{...}}}`` shape as a Save As, just aimed at
        a different folder key.

        Consequences worth knowing, both from that mechanism:

        * It CHANGES what is loaded on the unit, and leaves the source preset on
          the grid afterwards.
        * It copies the preset's audio state, not its metadata: the destination
          gets whatever ``instrument`` is passed and no tags, because that is all a
          save can carry (see :meth:`save_current_preset`).

        ``to_position`` defaults to the first free slot in the destination.
        Returns the name the device actually stored, which may be de-duplicated.
        """
        p = self.read_preset(from_setlist, position)
        source_name = p.name if field_present(p, "name") else None
        if to_position is None:
            taken = {e.index for e in self.list_presets(to_setlist)}
            to_position = next(i for i in range(256) if i not in taken)
        return self.save_current_preset(to_setlist, to_position,
                                        name or source_name or "copy",
                                        instrument=instrument, confirm=True)

    def duplicate_setlist(self, source_name: str, dest_name: str,
                          limit: int | None = None):
        """Copy a whole setlist into a new one, preset by preset.

        Also a composition rather than a device operation. The unit's own
        duplicate action broadcasts a ``BulkOperation`` narrating its progress -
        ``"Duplicating, please wait."``, then a progress fraction, then
        ``finished`` - but that is the device REPORTING: replaying it copies
        nothing, and no host-drivable duplicate exists. So this creates the
        destination and copies each preset with :meth:`copy_preset`.

        Which means it is SLOW - a recall and a save per preset, several seconds
        each - and it recalls every one of them on the unit as it goes. ``limit``
        caps how many are copied, for trying it out on a large setlist.

        Returns the list of names stored in the destination.
        """
        dest_key = self.create_setlist(dest_name)
        time.sleep(3.0)
        source_key = (source_name if source_name.startswith("/")
                      else f"{USER_SETLIST_ROOT}/{source_name}")
        entries = self.list_presets(source_key)
        if limit is not None:
            entries = entries[:limit]
        stored = []
        for i, entry in enumerate(entries):
            stored.append(self.copy_preset(source_key, entry.index, dest_key,
                                           to_position=i, name=entry.name,
                                           instrument=entry.instrument))
        return stored

    #: How many parameters each Global EQ band occupies, and the offset of each
    #: control within a band. See :meth:`set_global_eq`.
    GLOBAL_EQ_BAND_STRIDE = 5
    GLOBAL_EQ_BANDS = 5

    #: Wire indices of the Global EQ's OUT tab, which sits outside the five bands.
    GLOBAL_EQ_OUT_LEVEL = 25
    GLOBAL_EQ_OUT_12 = 26
    GLOBAL_EQ_OUT_34 = 27

    def set_global_eq_output(self, level=None, out12: bool | None = None,
                             out34: bool | None = None):
        """Set the Global EQ's OUT tab: its overall level and which outputs it feeds.

        The manual's OUT TAB - "assign the GLOBAL EQ to one or both output pairs and
        adjust its overall output level". These are the three indices beyond the five
        bands.

        ``out12`` is index 26, confirmed by assigning OUT 1/2 on the unit. ``out34``
        is index 27, which is the only index left and was never seen written, so it
        is identified by elimination rather than observation.

        ``level`` is index 25 and takes ``Encoded`` only. Its dB mapping is NOT
        established: the knob was watched moving continuously, so no value could be
        tied to a reading on screen.
        """
        if level is not None:
            level = values_module.Encoded(
                _setting_wire(level, "the Global EQ's OUT LEVEL"))
        controls = [(self.GLOBAL_EQ_OUT_LEVEL, level),
                    (self.GLOBAL_EQ_OUT_12,
                     None if out12 is None else values_module.Encoded(float(out12))),
                    (self.GLOBAL_EQ_OUT_34,
                     None if out34 is None else values_module.Encoded(float(out34)))]
        controls = [(i, v) for i, v in controls if v is not None]
        if not controls:
            raise TypeError("set_global_eq_output needs level=, out12= or out34=")
        for index, value in controls:
            self.set_global_eq_band(index, value)

    def set_global_eq(self, band: int, gain=None, frequency=None, q=None,
                      filter_type=None, enabled: bool | None = None):
        """Set one Global EQ band's controls, by band number rather than wire index.

        ``band`` is 1 to 5 as the unit numbers them.

        ``gain`` takes ``Db`` or ``Encoded``. Its span is **-12..+12 dB**, and
        that span is the MANUAL's rather than a measurement - what supports it
        here is two points, wire 0.5 reading 0 dB and 0.75 reading +6 dB, which
        a straight line over -12..+12 reproduces exactly. Recorded as the
        weaker evidence it is in ``units.SETTING_SPANS``, and queued to be
        driven on screen::

            qc.set_global_eq(2, gain=Db(-3.0))

        ``frequency`` and ``q`` take ``Encoded`` only - nothing ties either to a
        reading on screen yet. ``filter_type`` takes a
        :class:`~pyquadcortex.protocol.enums.GlobalEQFilter`, and ``enabled`` a
        bool; both are selectors rather than values on a scale.

        The layout is **5 parameters per band**, at offsets ``0 GAIN``,
        ``1 FREQUENCY``, ``2 Q``, ``3 TYPE``, so band N's controls live at
        ``(N - 1) * 5 + offset``. Established by changing each of band 1's controls
        in turn and reading which index moved, then checked against the whole
        28-parameter list: laid out five per band the defaults line up exactly as a
        five-band parametric EQ should - identical gains, identical Qs,
        monotonically increasing frequencies, and shelf/peak/peak/peak/shelf types.

        ``enabled`` is offset 4 - the manual's EQ BAND BYPASS - where **1.0 means the
        band is active** and 0.0 bypasses it. Confirmed by toggling band 1's bypass on
        the unit, and consistent with every band shipping at 1.0.

        Indices 25 to 27 are the OUT tab, reached through
        :meth:`set_global_eq_output`.

        Writes are sparse by index, so only the controls given are sent.
        """
        if not 1 <= band <= self.GLOBAL_EQ_BANDS:
            raise ValueError(f"band must be 1 to {self.GLOBAL_EQ_BANDS}, got {band}")
        base = (band - 1) * self.GLOBAL_EQ_BAND_STRIDE
        if gain is not None:
            gain = values_module.Encoded(_setting_wire(
                gain, "a Global EQ band's GAIN", _GLOBAL_EQ_GAIN,
                unit_example="Db(-3.0)"))
        if frequency is not None:
            frequency = values_module.Encoded(_setting_wire(
                frequency, "a Global EQ band's FREQUENCY"))
        if q is not None:
            q = values_module.Encoded(_setting_wire(q, "a Global EQ band's Q"))
        # A selector and a switch, so the library builds their wire values
        # itself - there is no caller number here to say a scale for. Separate
        # names rather than reassigning the parameters: `enabled` is a bool and
        # `filter_type` an enum, and writing an `Encoded` over either makes the
        # signature stop describing the variable halfway down the function.
        type_wire = (None if filter_type is None
                     else values_module.Encoded(int(filter_type) / 4))
        enabled_wire = (None if enabled is None
                        else values_module.Encoded(float(enabled)))
        controls = [(offset, value) for offset, value in
                    ((0, gain), (1, frequency), (2, q), (3, type_wire),
                     (4, enabled_wire))
                    if value is not None]
        if not controls:
            raise TypeError("set_global_eq needs at least one control to set")
        for offset, value in controls:
            self.set_global_eq_band(base + offset, value)
        return None

    #: Wire index of the ``file_name`` parameter on a Neural Capture block, and the
    #: root folder key of the Captures Library. See :meth:`set_capture`.
    CAPTURE_FILE_NAME_PARAM = 5
    CAPTURES_LIBRARY = "local_nc_root"

    def captures(self, timeout: float = 30.0) -> list:
        """Every Neural Capture in the library, as listing entries.

        Each has a ``name`` and a ``key`` - the key being a 64-character content hash,
        which is half of what :meth:`set_capture` needs. On the observed unit this is
        over two thousand entries: the factory capture libraries plus the player's own.

        Note this is NOT the same as the models in :attr:`catalog`. The catalog's
        Neural Capture category lists only a couple of entries and does not grow when a
        capture is saved, so it cannot be used to find out what is available.
        """
        return self.list_presets(self.CAPTURES_LIBRARY, timeout=timeout)

    def set_capture(self, cell, capture, params: dict | None = None):
        """Point a Neural Capture block at a capture from the library.

        ``capture`` is an entry from :meth:`captures` (or anything with ``key`` and
        ``name``). A capture BLOCK is an ordinary model - 14000 on the observed unit -
        and which capture it plays is held in a string parameter:

            ``file_name = <64-char content hash><display name>``

        the hash being the library entry's ``key``, concatenated directly with its
        name and no separator. Read off factory 28A, whose capture block holds
        ``"3c06...3a2dDarkglass VMT 1"``, and confirmed by placing a block from the
        host and pointing it at a freshly made capture.

        So the model id identifies "a capture block", not which capture - which is why
        the catalog cannot enumerate what is available and :meth:`captures` is the
        list to browse.

        **Loading a capture RESETS the block's other parameters** to the capture's
        own defaults, silently. A VOLUME of 0.56 written before the load read back
        0.5 afterwards; written after, it survived. The natural calling order -
        walk parameters by index, where ``file_name`` happens to be 5, right after
        VOLUME at 4 - loses every knob before it with no error, and a rig whose
        knobs sit at defaults passes every check while being written wrongly.

        So write parameters AFTER this call, or pass them here and they are
        applied once the capture is in::

            qc.set_capture(row=0, column=2, capture=c,
                           params={4: Db(-5.0)})        # VOLUME, by wire index

        ``params`` maps parameter index to value, and each one goes through
        :meth:`set_param` exactly as if you had written it there - so it says
        which scale it is on, and a bare number is refused here too. Pass
        ``model=None`` to point an existing block at a new capture without
        re-placing it.

        Parameters passed here survive the preset's first save. A BYPASS written
        to this block before that save does not - see :meth:`set_bypass` for the
        sequence that persists. And like :meth:`set_block`, this raises
        :class:`BlockRefused` on a DSP-capacity refusal - captures are expensive
        blocks, so that refusal is one to expect.
        """
        row, column = cell.row, cell.column
        model = cell.model_id
        key = getattr(capture, "key", None)
        name = getattr(capture, "name", None)
        if key is None or name is None:
            raise TypeError(
                "capture must be an entry from captures(), carrying key and name"
            )
        if params and self.CAPTURE_FILE_NAME_PARAM in params:
            raise ValueError(
                f"params must not include index {self.CAPTURE_FILE_NAME_PARAM} - "
                f"that is the capture reference itself, set from `capture`"
            )
        if model is not None:
            self.set_block(Block(row, column, model))
        # Addressed by cell alone, with no model id, which is deliberate: it
        # keeps this path free of a catalog round trip. The cost is that a
        # `Real` in `params` cannot be converted here and says so - `set_param`
        # raises naming the missing model rather than guessing a scale.
        cell = Block(row, column)
        result = self.set_param(cell, self.CAPTURE_FILE_NAME_PARAM, f"{key}{name}")
        for index, value in (params or {}).items():
            # Passed through as they arrive. Wrapping them in `Encoded` here
            # meant a caller who wrote `Real(0.5)` got the DEVICE's 0.5 - the
            # exact swap ADR-0016 exists to close, performed by the library
            # itself and reported as success. `set_param` refuses a bare number
            # for this method's callers too.
            result = self.set_param(cell, index, value)
        return result

    IR_LIBRARY = "local_ir_root"
    USER_IRS = "2_q"
    IR_FILE_TYPE = 1
    IR_PATH_PARAMS = (2, 10)
    IR_NAME_PARAMS = (22, 23)
    IR_LOADER_MODELS = range(29001, 29009)

    def list_irs(self, folder: str | None = None, timeout: float = 20.0) -> list:
        """Every Impulse Response the unit can load, as listing entries.

        Each has a ``key`` and a ``name``, which are exactly what :meth:`set_ir`
        needs. ``folder`` defaults to the whole IRs Library; pass
        :attr:`USER_IRS` (``"2_q"``, shown as "My IRs") for only your own.

        **The 588 entries under ``/opt/neuraldsp/impulse_responses`` are excluded,
        and deliberately.** They are assets belonging to purchased desktop plugins,
        they expose a ``name`` and NO ``key``, and the unit cannot load them - its
        own IR browser does not show them. Listing them here would offer IRs that
        cannot be used.

        IRs are ``FileMessage.type: 1``. That field is a category selector - 0 is
        presets, 2 is captures - and a listing request must set it, unlike
        :meth:`list_presets` which sends a bare READ and filters the flood.
        """
        wanted = folder or self.IR_LIBRARY
        request_id = self._t.next_request_id()
        message = pa.FileMessage(action=pa.MessageAction.READ,
                                 type=self.IR_FILE_TYPE, request_id=request_id)
        listing = self._t.await_broadcast(
            pa.FileMessage, lambda: self._t.send(message), timeout=timeout,
            match=lambda m: (m.HasField("request_id")
                             and m.request_id == request_id
                             and m.folder.key.rstrip("/") == wanted.rstrip("/")))
        return [f for f in listing.folder.files if f.HasField("key") and f.key]

    def set_ir(self, cell, ir, slot: int = 0):
        """Point an IR Loader block at an IR from the library.

        ``ir`` is an entry from :meth:`list_irs` (anything with ``key`` and
        ``name``). ``slot`` is 0 or 1: **every IR Loader has TWO IR slots**,
        whatever its name suggests, and each has its own parameters.

        An IR reference is **two strings, and the first is not a path**::

            IR PATH  (param 2 or 10)  = the library entry's KEY, e.g.
                                        "CIR_eb6d6d347e75f988010a9746580c31c"
            IR NAME  (param 22 or 23) = its display name, e.g. "Rex 57 on axis"

        Read off a working block placed on the unit by hand. The `IR PATH` label
        is misleading: a real filesystem path does not resolve, and neither does a
        bare name - only the key does. This differs from a Neural Capture block,
        which holds ONE string concatenating hash and name.

        Confirmed end to end: a loader pointed at an IR from the host showed that IR
        on the unit with no warning icon.

        A bad reference is not reported to the host - the device stores any string
        unchanged - but the unit shows a warning icon and "<IR NAME> is missing" on
        screen, so check there if a block goes quiet.
        """
        row, column = cell.row, cell.column
        model = cell.model_id
        key = getattr(ir, "key", None)
        name = getattr(ir, "name", None)
        if not key or not name:
            raise TypeError(
                "ir must be an entry from list_irs(), carrying key and name - the "
                "IR PATH parameter takes the library KEY, not a path or a name"
            )
        if slot not in (0, 1):
            raise ValueError(f"slot must be 0 or 1, not {slot!r}")
        if model is not None:
            self.set_block(Block(row, column, model))
        cell = Block(row, column)
        self.set_param(cell, self.IR_PATH_PARAMS[slot], key)
        return self.set_param(cell, self.IR_NAME_PARAMS[slot], name)

    def show_capture_dialog(self, shown: bool = True):
        """Answer the device's request to open the Neural Capture dialog.

        **The capture flow is gated on the host.** Choosing "New Neural Capture" on
        the unit does not open anything by itself: the device broadcasts
        ``NeuralCapture{try_to_show_dialog: true}`` and waits for the connected host
        to reply. With no reply the tap appears to do nothing at all - the unit simply
        returns to the grid - which is what made this feature look inert while a host
        was attached.

        So a client that wants the flow has to answer::

            NeuralCapture{UPDATE, show_dialog: true}

        The message also carries ``show_dialog_fail_reason``, a string, for refusing
        with an explanation.

        **Do not answer this unless you are implementing the capture UI.** The dialog
        is the HOST's to draw - the unit hands its capture flow to a connected editor,
        which is what Cortex Control's Neural Capture does. Replying ``true`` from a
        library that draws nothing puts the device into the flow with no interface
        anywhere: the device reported ``state: 1`` and prepared its A/B model while the
        unit's own screen stayed on the grid.

        Worse, staying SILENT is not neutral either - the unit waits for the answer and
        its own wizard never opens, so simply being connected suppresses on-device
        capture. To use the unit's wizard, disconnect first.

        Creating a capture is not implemented here. The engine is reachable though:
        the catalog's Neural Capture Internal category holds ``NC_Recorder``,
        ``NC_Trainer`` and ``NC_Refiner``, whose parameters are the controls (START
        TRAINING, SET SEED, CANCEL TRAINING, START AUTO REFINE, SET LATENCY, OUTPUT
        GAIN, EXPORT MODEL) and whose meters report progress, loss and a sanity check.
        Using a capture that already exists is :meth:`set_capture`.
        """
        return self._t.send(pa.NeuralCaptureMessage(action=pa.MessageAction.UPDATE,
                                                    show_dialog=shown))

    def wait_for_listing(self, setlist: str = Setlist.USER, until=None,
                         timeout: float = 45.0, interval: float = 2.0):
        """Re-list ``setlist`` until ``until(entries)`` holds, and return them.

        File operations are eventually consistent, and the lag scales with how
        many you performed: a single delete settles in a few seconds, but after
        eleven deletes a listing five seconds later still showed all eleven
        presets - they had in fact all gone. A fixed sleep therefore produces
        false negatives, which in a careful script reads as "the clear failed"
        on work that actually succeeded.

        Poll instead::

            # wait for a save to appear
            qc.wait_for_listing(Setlist.USER,
                                until=lambda e: any(p.name == "My Patch" for p in e))

            # wait for a bulk delete to settle
            qc.wait_for_listing(Setlist.USER, until=lambda e: not e)

        With no ``until``, this waits for two consecutive identical listings,
        which is the general "has it stopped changing?" question.

        **A missed push is ridden out, not raised.** The device sometimes goes quiet
        for a polling interval and the underlying listing request times out; that is
        the transient this method exists to absorb, so it keeps polling until its own
        ``timeout``. You do not need to wrap this in your own retry loop.

        Two different ``TimeoutError`` diagnoses come out of it, which are worth
        telling apart: *the condition never became true* means listings arrived and
        your predicate stayed false, whereas *the device stopped pushing listings*
        means nothing was ever evaluated - so the latter says nothing about whether
        your change landed.
        """
        deadline = time.monotonic() + timeout
        previous = None
        listings_seen = 0
        missed_pushes = 0
        while True:
            try:
                entries = self.list_presets(setlist)
            except TimeoutError:
                # A missed File push is exactly the transient this method exists to
                # ride out. Surfacing it would produce the very false negative the
                # docstring warns about - one quiet polling interval aborting a wait
                # for work that already succeeded. Keep polling until OUR deadline.
                missed_pushes += 1
            else:
                listings_seen += 1
                if until is not None:
                    if until(entries):
                        return entries
                else:
                    signature = [(e.index, e.name) for e in entries]
                    if previous is not None and signature == previous:
                        return entries
                    previous = signature
            if time.monotonic() >= deadline:
                if listings_seen == 0:
                    # Different diagnosis: we never saw a listing at all.
                    raise TimeoutError(
                        f"the device stopped pushing listings for {str(setlist)!r}: "
                        f"{missed_pushes} attempt(s) in {timeout}s each timed out "
                        "waiting for a File broadcast. The condition was never "
                        "evaluated, so this says nothing about whether your change "
                        "landed."
                    )
                raise TimeoutError(
                    f"the condition never became true for {str(setlist)!r} within "
                    f"{timeout}s ({listings_seen} listing(s) checked"
                    + (f", {missed_pushes} missed push(es) ridden out"
                       if missed_pushes else "") + ")"
                )
            time.sleep(interval)

    def _file_operation(self, msg, timeout: float = 5.0):
        """Send a File message, tolerating the device not replying.

        File operations are asynchronous and this protocol STALLs every host
        write, so a missing reply says nothing about whether the operation
        worked - the device may simply not answer. Treating that as an error made
        callers wrap every save and delete in ``try/except TimeoutError`` and
        verify by re-reading anyway, which is the same principle the transport
        already applies to the benign write stall.

        Returns the device's reply if one arrives, else ``None``. Either way,
        DEVICE STATE IS THE ARBITER: re-read to confirm (see
        :meth:`wait_for_listing`).
        """
        try:
            return self._t.request(msg, timeout=timeout)
        except TimeoutError:
            return None

    def save_current_preset(
        self,
        setlist_path: str,
        position,
        name: str,
        instrument: int = Instrument.NONE,
        default_scene=None,
        confirm: bool = False,
        confirm_timeout: float = 20.0,
    ):
        """Save the preset currently on the grid into a setlist slot ("Save As").

        ``position`` is either the linear slot index or the slot name shown on
        the unit (``"30A"``). Saving OVERWRITES whatever occupies that slot.

        ``default_scene`` sets which scene the preset comes up in. There is no
        field for it in the File message: the device records whichever scene is
        ACTIVE at save time, so this switches to that scene first and the saved
        preset's ``default_scene`` reads back accordingly. Note the side effect -
        the unit is left on that scene.

        **The device may not use the name you asked for.** If the setlist already
        contains a preset of that name, it de-duplicates: the base is truncated as
        needed and a ``_N`` suffix appended, to 20 characters total, so saving a
        second ``"Cali Basswalk [Ret1]"`` yields ``"Cali Basswalk [Ret_1"``. A
        unique name is stored verbatim and is not length-limited (36 characters
        was stored intact). If the resulting name matters, read the slot back and
        use what the device reports.

        Confirmed by capture: Cortex Control's "Save As" is a
        ``FileMessage`` with action CREATE (unset, the default), ``type: 0``,
        and NO preset payload - the device saves the preset it already has on
        the grid. The target slot is addressed inside ``folder``: the setlist's
        device path in ``folder.key`` and one ``files`` entry carrying the
        LINEAR slot index (bank*8 + letter, zero-based: "28E" == 220), the
        preset name, and the instrument tag (captured save sent
        ``instrument: 2``). Saving to slot 28E as "Test save to user sl" sent
        exactly ``{folder{key: "/media/p4/Presets/My Presets",
        is_factory: false, files{index: 220, name: ..., instrument: 2}}}``.

        ``instrument`` is the tag the unit filters on, and is the only preset
        metadata this can set. The descriptive ``tags`` a factory preset carries
        ('Clean', 'Crunch') are NOT reproduced: a preset saved this way reads back
        with an EMPTY tag list whatever its source had, and no route to them was
        found on this firmware - not ``ProductData.tags`` on this message, not a
        File UPDATE carrying them, not a ``Grid`` UPDATE carrying
        ``preset.tags``. All three are accepted and leave the list empty. Nothing
        stale is inherited, so a derived preset is simply untagged.
        """
        if default_scene is not None:
            # No field carries this; the device takes the active scene at save time.
            self.switch_scene(default_scene)
        msg = pa.FileMessage(type=0)
        msg.folder.key = setlist_path
        msg.folder.is_factory = False
        entry = msg.folder.files.add()
        entry.index = _as_position(position)
        entry.name = name
        entry.instrument = instrument
        self._file_operation(msg)
        if not confirm:
            return name
        # The device renames on a collision, so the only way to know what it
        # actually stored is to ask it.
        try:
            entries = self.wait_for_listing(
                setlist_path,
                until=lambda es: any(e.index == entry.index and e.name for e in es),
                timeout=confirm_timeout,
            )
        except TimeoutError:
            return None
        for e in entries:
            if e.index == entry.index:
                return e.name
        return None

    def delete_preset(self, setlist_path: str, name: str):
        """Delete the preset named ``name`` from the setlist at ``setlist_path``.

        Confirmed by capture: deleting "Test save to user
        sl" from slot 28E sent ``File{action: DELETE, type: 0, folder{key:
        <setlist path>, is_factory: false, files{key: "<setlist
        path>/<name>.pb"}}}`` - the preset is addressed by its device FILE
        PATH (name-based, ``.pb`` extension), NOT by slot index.
        """
        msg = pa.FileMessage(action=pa.MessageAction.DELETE, type=0)
        msg.folder.key = setlist_path
        msg.folder.is_factory = False
        msg.folder.files.add().key = f"{setlist_path}/{name}.pb"
        return self._file_operation(msg)

    def move_preset(self, setlist_path: str, name: str, to_position):
        """Move the preset named ``name`` to slot ``to_position`` (same setlist).

        ``to_position`` is either the linear slot index or the slot name shown on
        the unit (``"28D"``).

        Confirmed by capture: dragging "Darkglass AO900
        2_1" onto slot 28D sent ``File{action: MOVE, type: 0, folder{key:
        <setlist path>, files{key: "<setlist path>/<name>.pb"}},
        to_folder{key: <setlist path>, files{index: 219}}}`` - source by FILE
        PATH, destination by LINEAR slot index.
        """
        msg = pa.FileMessage(action=pa.MessageAction.MOVE, type=0)
        msg.folder.key = setlist_path
        msg.folder.is_factory = False
        msg.folder.files.add().key = f"{setlist_path}/{name}.pb"
        msg.to_folder.key = setlist_path
        msg.to_folder.files.add().index = _as_position(to_position)
        return self._file_operation(msg)


def field_present(message, field: str) -> bool:
    """Whether ``field`` is set on ``message``, without raising.

    ``HasField`` is the natural way to read this schema, because most fields in a
    preset payload sit in a synthetic ``oneof`` and absent means "not addressed".
    But presence is not universal: ``HasField`` raises ``ValueError`` on a field
    that has none, and the schema has plenty - ``SceneBypass.bypass`` is the one
    that bites, since walking per-scene bypass is a common thing to want::

        # raises ValueError: Field SceneBypass.bypass does not have presence
        entry.HasField("bypass")

        field_present(entry, "bypass")      # False, and no exception

    For a field without presence this returns ``False``, since the wire cannot
    distinguish "absent" from "zero" there anyway. See ``docs/protocol.md`` for
    which fields those are.
    """
    try:
        return message.HasField(field)
    except ValueError:
        return False


def blocks(p: preset.BinaryPreset) -> list:
    """The OCCUPIED grid cells of ``p``, as :class:`Block` entries.

    Use this rather than walking ``chains[].models[]`` yourself. The device
    reports every row as its full complement of **8 column slots**, with empty
    ones present as ``Model`` entries whose ``hash`` is absent or zero, so
    ``len(chain.models)`` is 8 for every row - including entirely empty rows - and
    is not a block count. ``output_control`` and ``input_control`` are padded the
    same way, one entry on every row. ``splitter``, ``mixer``,
    ``combined_splitter`` and ``split_control_points`` are NOT: they exist only on
    rows 0 and 2, and are empty on rows 1 and 3, because a branch can only
    originate on an even row with its lane on the row below.

    Nor is ``in_portid`` a usable occupancy signal: ``Input.EMPTY`` means "not fed
    from a physical jack", which is the normal state of any row that is not an
    input row, occupied or not. Factory "Brit 2203" has six blocks on row 2 with
    ``in_portid`` EMPTY.
    """
    found = []
    for i, chain in enumerate(p.chains):
        row = chain.row if field_present(chain, "row") else i
        for j, model in enumerate(chain.models):
            if not (field_present(model, "hash") and model.hash):
                continue
            column = model.column if field_present(model, "column") else j
            found.append(Block(row=row, column=column, model_id=model.hash))
    return found


class Split(NamedTuple):
    """Where a row branches into a parallel lane, and where it recombines.

    ``mix_column`` is ``-1`` for a branch that never recombines, so prefer
    :attr:`rejoins` over testing the number. :attr:`lane_row` is the row the
    parallel lane occupies.
    """

    row: int
    split_column: int
    mix_column: int

    @property
    def rejoins(self) -> bool:
        """Whether the parallel lane recombines into this row."""
        return self.mix_column >= 0

    @property
    def lane_row(self) -> int:
        """The row carrying this branch's parallel lane."""
        return self.row + 1


def splits(p: preset.BinaryPreset) -> list:
    """Where each row branches into a parallel lane, as :class:`Split` entries.

    This is the readable half of the grid topology. It does NOT live on the
    splitter block - that carries no ``column`` at all - but in
    ``Chain.split_control_points``, whose ``split`` and ``mix`` fields give the
    columns where the lane leaves and rejoins. Those fields have **no presence**,
    so ``HasField`` (and therefore :func:`field_present`) reports them missing
    even when set; read them directly, as this does.

    A branch is present when ``split >= 0``. ``mix`` is independent: a lane may
    never recombine, and reports ``-1`` when it does not, so test
    :attr:`Split.rejoins` rather than the column. Factory "Strat Ambience" (05B)
    branches at column 2 and never rejoins; "Darkglass AO900 1" (27H) branches and
    rejoins at column 4 on rows 0 and 2. A row with no branch reports ``-1`` for
    both and is omitted.

    The parallel lane occupies :attr:`Split.lane_row`, the row BELOW the branch,
    which is spoken for whether or not it holds blocks - see :func:`free_rows`.
    Only rows 0 and 2 can carry a branch at all.
    """
    found = []
    for i, chain in enumerate(p.chains):
        row = chain.row if field_present(chain, "row") else i
        for scp in chain.split_control_points:
            # -1 means "no branch here" - factory "Brit 2203" reports (-1, -1) on
            # its serial rows. `mix` alone being -1 is a branch that never rejoins.
            if scp.split < 0:
                continue
            found.append(Split(row=row, split_column=scp.split, mix_column=scp.mix))
    return found


class Folder(NamedTuple):
    """One folder the device reports, from :meth:`QuadCortex.list_folders`."""

    key: str
    name: str
    slots: int
    occupied: int
    is_factory: bool


class MidiOut(NamedTuple):
    """One per-preset MIDI Out message.

    The wire carries a generic ``{type, channel, param1, param2, param3}``, so
    build these with :meth:`cc` or :meth:`pc` rather than by hand - what the
    three params mean depends on the type. Both mappings are confirmed by
    entering a message on the unit and reading the saved preset.
    """

    type: int
    channel: int
    param1: int = 0
    param2: int = 0
    param3: int = 0

    @classmethod
    def cc(cls, channel: int, cc: int, value: int):
        """A Control Change sending one value, for a footswitch source.

        ``param1`` is the CC number and ``param2`` the value.
        """
        return cls(type=MidiOutType.CC, channel=channel, param1=cc, param2=value)

    @classmethod
    def cc_toggle(cls, channel: int, cc: int, minimum: int, maximum: int):
        """A Control Change that alternates between two values on each press.

        ``param2`` and ``param3`` are the MIN and MAX the manual describes.
        Confirmed: entering CC Toggle on the unit stored ``type: 2`` with
        ``param2: 5, param3: 120`` for a 5/120 range.
        """
        return cls(type=MidiOutType.CC_TOGGLE, channel=channel, param1=cc,
                   param2=minimum, param3=maximum)

    @classmethod
    def expression_cc(cls, channel: int, cc: int, minimum: int, maximum: int):
        """A Control Change swept by an expression pedal.

        An expression source sends a RANGE rather than a single value, so the
        unit asks for min and max even for a plain CC: the stored message is
        ``type: 1`` with ``param2``/``param3`` holding the ends of the sweep.
        Use this for :attr:`~pyquadcortex.protocol.enums.MidiSource.EXPRESSION_1` and
        ``EXPRESSION_2``; use :meth:`cc` for a footswitch.
        """
        return cls(type=MidiOutType.CC, channel=channel, param1=cc,
                   param2=minimum, param3=maximum)

    @classmethod
    def pc(cls, channel: int, program: int, bank_msb: int = 0, bank_lsb: int = 0):
        """A Program Change: ``param1``/``param2`` are the bank select bytes
        (CC#0 and CC#32) and ``param3`` the program number."""
        return cls(type=MidiOutType.PC, channel=channel, param1=bank_msb,
                   param2=bank_lsb, param3=program)


class StompAssignment(NamedTuple):
    """A block bound to a STOMP-mode footswitch."""

    row: int
    column: int
    footswitch: int


class ExpressionAssignment(NamedTuple):
    """One parameter with an expression pedal on it.

    ``minimum`` and ``maximum`` are the two ends of the sweep as the WIRE
    carries them, so they are :class:`~pyquadcortex.protocol.values.Encoded`.
    They are positions of the parameter being swept, so converting them to that
    parameter's own units needs the catalog - which is the model layer's job,
    not this one's. See ``pyquadcortex.device`` for the same thing in dB.

    ``minimum`` above ``maximum`` REVERSES the pedal, which is how the manual
    describes inverting a parameter, so the pair is not ordered and must not be
    sorted.

    The field is ``param_index`` rather than ``index`` for two reasons, and the
    second is the real one: it matches :func:`param_state`'s argument name, and
    a `NamedTuple` field called ``index`` SHADOWS ``tuple.index``, so the
    method would silently disappear from a public type.
    """

    target: "targets.ParamTarget"
    param_index: int
    pedal: int
    #: ``None`` where the unit did not send that end. Never seen in any
    #: committed fixture; carried because a protobuf default reported as the
    #: unit's answer is exactly what `field_present` exists to prevent.
    minimum: "values_module.Encoded | None"
    maximum: "values_module.Encoded | None"

    @property
    def reversed(self) -> bool:
        """Whether the heel is the LOUD end - `minimum` above `maximum`.

        ``False`` where either end is absent: with one end unknown there is no
        ordering to report, and guessing one would be inventing the setting.
        """
        if self.minimum is None or self.maximum is None:
            return False
        return float(self.minimum) > float(self.maximum)


#: Which target addresses each row-keyed collection that can carry an
#: assignment. `models` is NOT here: a block is keyed by column as well as row,
#: so it is walked separately. `combined_splitter` is the splitter's, matching
#: `targets.Splitter` - the device sends that one bare, with no key of its own.
_EXPRESSION_LANE_COLLECTIONS = (
    ("output_control", targets.LaneOutput),
    ("input_control", targets.LaneInput),
    ("mixer", targets.Mixer),
    ("combined_splitter", targets.Splitter),
)


def _sweep_or_none(prm, field: str):
    """One sweep end, or ``None`` where the unit did not send it."""
    if not field_present(prm, field):
        return None
    return values_module.Encoded(getattr(prm, field))


def _assignments_on(entry, target) -> list:
    """The assigned parameters of one container entry."""
    found = []
    for position, prm in enumerate(entry.params):
        # `expression: 0` is UNASSIGNED - it is what `clear_expression`
        # writes - and the device SENDS it, present on all 576 parameters
        # across the committed fixtures. So "never touched" and "pedal
        # removed" are the same answer, which is the right one: both mean no
        # pedal.
        if not (field_present(prm, "expression") and prm.expression):
            continue
        found.append(ExpressionAssignment(
            target=target,
            # The POSITION, not `prm.index`. That field is proto3-optional
            # so it HAS presence - and the device never sends it: absent on all
            # 576 parameters across every committed fixture, measured with
            # `field_present`. Reading it would report every assignment as
            # being on the first knob. `param_state` addresses positionally for
            # the same reason.
            param_index=position,
            pedal=prm.expression,
            # Checked rather than read: both sit in synthetic oneofs, so an
            # absent end is distinguishable from a real 0.0 - and reporting a
            # protobuf default as the unit's answer is the guess CLAUDE.md
            # forbids. Neither is absent anywhere in the fixtures; this is the
            # rule holding, not a bug seen.
            minimum=_sweep_or_none(prm, "expression_min"),
            maximum=_sweep_or_none(prm, "expression_max"),
        ))
    return found


def expression_assignments(p: preset.BinaryPreset) -> list:
    """Every expression pedal assignment in ``p``, as :class:`ExpressionAssignment`.

    The library could WRITE one of these before it could read one back, which
    is the gap this closes: :meth:`QuadCortex.set_expression` is confirmed on
    every container the unit has, and nothing reported what was already
    assigned.

    **The index is the parameter's POSITION in ``params[]``.** The wire's own
    ``index`` field is proto3-optional, so it HAS presence - and the device
    never sends it: absent on all 576 parameters across every committed
    fixture. Reading it would put every assignment on the first knob.
    :func:`param_state` addresses positionally for the same reason.

    Bypass assignments are a different feature on a different field and are NOT
    here - a pedal that BYPASSES a block is not a pedal on one of its knobs.

    **Two places this does not look, neither established either way.**
    ``Chain.splitter`` is NOT a second view of ``combined_splitter``: in every
    committed fixture it is a different model, 10002 with three parameters
    against 10004 with eight, and whether it can carry an assignment has never
    been checked. :class:`~pyquadcortex.protocol.targets.Tempo` is a preset
    target with no row, so it is outside ``chains`` entirely, and
    :meth:`QuadCortex.set_expression` accepts any target. Both are gaps in
    coverage rather than measured absences, and saying so beats calling this
    list complete.
    """
    found = []
    for i, chain in enumerate(p.chains):
        row = chain.row if field_present(chain, "row") else i
        for j, model in enumerate(chain.models):
            # Every row reports its full eight slots, empty ones included, so
            # skip an unoccupied cell the way `blocks()` does rather than
            # reporting an assignment against a block that is not there.
            if not (field_present(model, "hash") and model.hash):
                continue
            column = model.column if field_present(model, "column") else j
            found += _assignments_on(
                model, targets.Block(row=row, column=column,
                                     model_id=model.hash))
        for collection, address in _EXPRESSION_LANE_COLLECTIONS:
            for entry in getattr(chain, collection):
                found += _assignments_on(entry, address(row=row))
    return found


def stomp_assignments(p: preset.BinaryPreset) -> list:
    """Which blocks are bound to which footswitches, as :class:`StompAssignment`.

    Note that ``row``, ``column`` and ``stomp_index`` all lack presence, so a
    zero is indistinguishable from unset - an entry for row 0, column 0,
    footswitch A reads as a bare, apparently empty entry. Factory content
    populates this: "Darkglass AO900 2" binds eight blocks to A-H.
    """
    return [StompAssignment(row=a.row, column=a.column, footswitch=a.stomp_index)
            for a in p.stomp_mode_assignments]


@typing.overload
def midi_out(p: preset.BinaryPreset, source: None = None
             ) -> dict[int, list[MidiOut]]: ...


@typing.overload
def midi_out(p: preset.BinaryPreset, source: int) -> list[MidiOut]: ...


def midi_out(p: preset.BinaryPreset, source=None):
    """The per-preset MIDI Out messages, keyed by :class:`MidiSource`.

    Reads the 120-slot ``midi_messages_general_v2`` as 10 sources x 12 messages.
    Pass ``source`` to get one source's list instead of the whole map. Empty
    slots are dropped, so a source with nothing assigned is absent.

    The return type DEPENDS on that argument - a map without it, one source's
    list with it - which the annotation used to hide by claiming `dict` for
    both. The overloads say it instead, so a caller that passes a source gets
    told it has a list.
    """
    out: dict[int, list[MidiOut]] = {}
    for i, m in enumerate(p.midi_messages_general_v2):
        if not (m.type or m.channel or m.param1 or m.param2 or m.param3):
            continue
        out.setdefault(i // 12, []).append(
            MidiOut(type=m.type, channel=m.channel, param1=m.param1,
                    param2=m.param2, param3=m.param3))
    if source is not None:
        return out.get(int(source), [])
    return out


def preset_load_midi_out(p: preset.BinaryPreset) -> list:
    """The MIDI messages this preset sends when it is loaded."""
    return [MidiOut(type=m.type, channel=m.channel, param1=m.param1,
                    param2=m.param2, param3=m.param3)
            for m in p.midi_messages
            if m.type or m.channel or m.param1 or m.param2 or m.param3]


def tempo_params(p: preset.BinaryPreset) -> dict:
    """A preset's tempo parameters, keyed by index.

    Read POSITIONALLY, because the stored preset carries 24 of them and none has an
    explicit ``index`` - the field is absent on every one, so position is the index.
    (A host WRITE does set ``index``; it is only the device's stored form that is
    positional.) Names for the indices are in
    `targets.Tempo.NAMES`.

    Values are the normalized 0..1 of the ACTIVE scene, as elsewhere.
    """
    if not len(p.tempoProgramData):
        return {}
    out = {}
    for index, prm in enumerate(p.tempoProgramData[0].params):
        values = [x.float_value for x in prm.param_values
                  if field_present(x, "float_value")]
        if values:
            out[index] = values[0]
    return out


def beats(p: preset.BinaryPreset) -> dict:
    """A preset's per-beat metronome states, keyed by 1-based BEAT number.

    Reads tempo parameters 10 to 22 - the catalog's ``STEPSTATE0`` to
    ``STEPSTATE12`` - and returns them as
    :class:`~pyquadcortex.protocol.enums.MetronomeBeat` values.

    All 13 are always present, whatever the time signature, so a 4/4 preset still
    reports beats 5 to 13. They are stored, simply not sounded. This does not
    filter them, because how many a signature actually sounds has not been measured
    for the compound ones.

    A value that is not one of the four quantized states is returned as the raw
    float rather than being rounded into an enum - nothing has been seen to write
    one, and silently snapping it would hide the day something does.
    """
    tp = tempo_params(p)
    out = {}
    for beat, index in QuadCortex.TEMPO_BEATS.items():
        if index not in tp:
            continue
        option = tp[index] * 3.0
        nearest = round(option)
        if abs(option - nearest) < 0.01 and 0 <= nearest <= 3:
            out[beat] = MetronomeBeat(nearest)
        else:
            out[beat] = tp[index]
    return out


def param_options(p: preset.BinaryPreset, block, param_index: int) -> list:
    """The option names of a list-valued parameter, as THIS PRESET renders them.

    For the 12 parameters marked ``dynamic`` in the catalog this is the only
    honest source: their lists include one entry per block earlier in the chain,
    so the length changes with the preset and the catalog's ``steps`` overstates
    it. For the other 527 the catalog has the names too, in ``stepNames``, and
    :meth:`QuadCortex.set_param_option` reads them from there.

    This docstring used to say the names were "NOT in the device catalog". That
    was true of the dynamic twelve and wrong about the rest - see ADR-0015.

    The preset carries the rendered list in ``Param.dynamic_steps``. Reading
    factory "US TWN Vibrato" (01C), the
    Doubler's TRIGGER options are ``Off, Follow Input, Input 1, Input 2, Input
    1/2, Return 1, Return 2, Return 1/2, USB input 5..8, ...``.

    Some of those lists include one entry per block in the preset, which is why
    such a parameter's stored value changes when the block count does.
    """
    for i, chain in enumerate(p.chains):
        if (chain.row if field_present(chain, "row") else i) != block.row:
            continue
        for j, model in enumerate(chain.models):
            if (model.column if field_present(model, "column") else j) != block.column:
                continue
            if param_index < len(model.params):
                return list(model.params[param_index].dynamic_steps)
    return []


def option_value(options, option) -> float:
    """The normalized wire value that selects ``option`` from ``options``.

    A list-valued (comboBox) parameter stores ``index / (count - 1)``. Confirmed
    on two different lists: a side-chain SOURCE of 16 options stored 0.2 for
    index 3 when set on the unit, and one of 18 options round-tripped 3/17 for
    the same choice. ``options`` comes from :func:`param_options`.

    ``option`` may be the name or the index.
    """
    if not options:
        raise ValueError("no options: read them with param_options() first")
    index = options.index(option) if isinstance(option, str) else int(option)
    if not 0 <= index < len(options):
        raise ValueError(f"option index {index} outside 0..{len(options) - 1}")
    if len(options) == 1:
        return 0.0
    return index / (len(options) - 1)


def option_at(options, value: float):
    """Which of ``options`` a normalized wire ``value`` selects."""
    if not options:
        return None
    return options[round(value * (len(options) - 1))]


def free_rows(p: preset.BinaryPreset) -> list:
    """The rows of ``p`` available for an independent chain, lowest first.

    A row is free when it holds no blocks AND is not the parallel lane of a branch
    on the row above. The second half is the part that bites: building on the lane
    row of a branch puts blocks inside the existing chain's parallel path rather
    than beside it, and the lane row is frequently empty, so block count alone says
    a row is free when it is not. Factory "Strat Ambience" (05B) branches on row 0
    and holds nothing on row 1; row 1 is not free.

    :func:`row_status` gives the same answer with the reasoning attached - per
    row, occupied / free / reserved-as-a-lane - worth reading when a row you
    expected to be free is not.
    """
    used = {b.row for b in blocks(p)}
    lanes = {s.lane_row for s in splits(p)}
    return [row for row in range(len(p.chains)) if row not in used | lanes]


class RowStatus(NamedTuple):
    """One row's topology: what it holds, and whether it is truly available."""

    row: int
    #: ``"occupied"``, ``"free"``, or ``"reserved"`` (the parallel lane of a
    #: branch on the row above, spoken for even when empty).
    status: str
    #: How many blocks the row holds.
    block_count: int
    #: The row a branch reserving this one lives on, else ``None``.
    reserved_by: int | None


class BypassState(NamedTuple):
    """One block's stored bypass: the scene-mode flag and the eight scene slots."""

    scene_mode: bool
    scenes: tuple


def bypass_state(p: preset.BinaryPreset, cell) -> BypassState:
    """A preset's stored bypass for one grid cell.

    The read-side counterpart of :meth:`QuadCortex.set_bypass`, because verifying
    a bypass write should not require walking the proto - and the proto's shape is
    a trap. The bypass table is addressed **positionally**:
    ``preset.bypass[row].colBypass[column]``. The ``row`` and ``column`` fields
    INSIDE those entries read 0 on every stored entry; filtering on them returns
    cell (0,0) thirty-two times, which is exactly the wrong answer in a
    plausible-looking shape.

    ``scenes[i]`` is scene *i*'s stored flag. When ``scene_mode`` is false the
    block has ONE global bypass state and the eight slots are kept consistent -
    a global write updates all of them (measured).

    Note the table persists for EMPTY cells, so a freshly placed block inherits
    whatever the cell last held.
    """
    row, column = cell.row, cell.column
    cb = p.bypass[row].colBypass[column]
    return BypassState(scene_mode=bool(cb.sceneMode),
                       scenes=tuple(bool(sb.bypass) for sb in cb.sceneBypass))


def _reject_number_on_a_string_parameter(spec, value):
    """The mirror of the string guard, which was one-directional.

    A string was checked against a number's parameter and refused; a number on
    a STRING parameter was converted and sent. `Real(0.5)` on a cab's
    `ir selector`, whose catalog range is 0..999, became wire 0.0005 - a write
    that looks like it worked and means nothing.
    """
    if spec.type != "string":
        return
    raise TypeError(
        f"{spec.name!r} is a string parameter, so {value!r} is not a value it "
        f"can hold. Pass the string itself - set_param(target, param, \"...\") "
        f"- which is what a string-valued parameter takes."
    )


class ParamState(NamedTuple):
    """One parameter's stored state: its scene-mode flag and per-scene values."""

    scene_mode: bool
    values: tuple


def param_state(p: preset.BinaryPreset, cell, param_index: int) -> ParamState:
    """A preset's stored values for one block parameter, all scenes.

    The read-side counterpart of :meth:`QuadCortex.set_param`. ``values`` holds
    one entry per scene slot - `Encoded` values for ordinary parameters (the
    device's own 0..1, which is what the preset stores), strings for
    string-valued ones (capture ``file_name``, IR references, cab mic), ``None``
    where a slot carries neither.

    While ``scene_mode`` is false the parameter has ONE effective value; slots
    beyond the first are not maintained and should not be trusted. Factory
    content stores NaN in some slots - compare with :func:`params_equal`, which
    treats NaN as equal to NaN.
    """
    row, column = cell.row, cell.column
    prm = p.chains[row].models[column].params[param_index]
    stored = []
    for pv in prm.param_values:
        if pv.HasField("string_value"):
            stored.append(pv.string_value)
        elif pv.HasField("float_value"):
            stored.append(values_module.Encoded(pv.float_value))
        else:
            stored.append(None)
    return ParamState(scene_mode=bool(prm.scene_mode), values=tuple(stored))


def row_status(p: preset.BinaryPreset) -> list:
    """Every row's topology, as :class:`RowStatus` entries - lowest row first.

    The distinction this exists to make visible: **an empty row is not
    necessarily an available row.** A branch on row 0 or 2 claims the row below
    as its parallel lane, and that lane is spoken for whether or not it holds
    blocks. Factory "Strat Ambience" (05B) branches on row 0 and keeps row 1
    empty; writing a chain there puts blocks inside 05B's parallel path, not
    beside it. A naive "no blocks means free" check walks straight into that.

    :func:`free_rows` answers the narrower question "where can I build?"; this
    answers "why?"::

        for r in row_status(p):
            print(r.row, r.status,
                  f"(lane of the branch on row {r.reserved_by})"
                  if r.status == "reserved" else "")

    An occupied lane row reports ``"occupied"`` with ``reserved_by`` still set,
    so the split relationship stays visible either way.
    """
    used: dict[int, int] = {}
    for b in blocks(p):
        used[b.row] = used.get(b.row, 0) + 1
    lanes = {s.lane_row: s.row for s in splits(p)}
    out = []
    for row in range(len(p.chains)):
        count = used.get(row, 0)
        if count:
            status = "occupied"
        elif row in lanes:
            status = "reserved"
        else:
            status = "free"
        out.append(RowStatus(row=row, status=status, block_count=count,
                             reserved_by=lanes.get(row)))
    return out


#: The input-gate parameter that is a LIVE METER, not a setting: ``input_control``
#: index 2, the catalog's GAIN REDUCTION. The device samples it into the preset at
#: save time, so two saves of an identical rig differ there. Anything doing
#: round-trip verification must exclude it.
GAIN_REDUCTION_PARAM = 2


def params_equal(a: float, b: float, option_count=None,
                 tolerance: float = 1e-4) -> bool:
    """Whether two wire parameter values mean the same thing.

    Plain parameters compare as floats within ``tolerance`` (float32 storage
    makes exact equality a trap).

    For a LIST (comboBox) parameter, pass ``option_count`` and the values compare
    by the OPTION they select. That absorbs the rescaling this helper exists for:
    a list value is stored as ``index / (count - 1)``, and adding or removing a
    block changes the count on block-enumerating lists sitting on rows never
    written to. The same selected option then reads back as a different float,
    and a before/after diff reports corruption where nothing changed.

    ``option_count`` is one count (unchanged on both sides) or a ``(before,
    after)`` pair when the count itself moved::

        params_equal(0.5, 0.5)                          # plain float
        params_equal(1/3, 1/3, option_count=4)          # option 1 == option 1
        params_equal(2/6, 2/7, option_count=(7, 8))     # option 2 == option 2

    Counts come from the preset's ``dynamic_steps`` (authoritative for
    block-enumerating lists) or the catalog's
    :attr:`~pyquadcortex.protocol.catalog.Parameter.option_count`; when neither knows the
    parameter, compare as floats and expect false mismatches on rescaled lists -
    there is no honest way around that without the count.

    Two more traps for anyone diffing presets, documented here because this is
    the function they will reach for: ``input_control`` index 2
    (:data:`GAIN_REDUCTION_PARAM`) is a live meter sampled at save time and never
    compares equal across saves, and factory content stores NaN in some
    ``param_values`` - and ``NaN != NaN``, so exclude or special-case both.
    """
    if option_count is not None:
        try:
            count_a, count_b = option_count
        except TypeError:
            count_a = count_b = int(option_count)
        if count_a < 2 or count_b < 2:
            raise ValueError(
                f"a list parameter has at least 2 options; got {option_count!r}"
            )
        return (round(a * (count_a - 1)) == round(b * (count_b - 1)))
    if a != a or b != b:                      # NaN: equal only to another NaN
        return a != a and b != b
    return abs(a - b) <= tolerance


def _is_factory_setlist(setlist_path: str) -> bool:
    """Whether ``setlist_path`` is the factory library.

    Compared with trailing slashes normalized: the factory path carries one for
    recalls but the device omits it elsewhere (see :class:`Setlist`).
    """
    return str(setlist_path).rstrip("/") == str(Setlist.FACTORY).rstrip("/")


def _as_position(position) -> int:
    """Accept either a linear slot index or a slot name like ``"28C"``."""
    if isinstance(position, str):
        return slot_to_position(position)
    return int(position)


def slot_to_position(slot: str) -> int:
    """Convert a QC slot name like ``"28C"`` to its linear wire position.

    Confirmed by capture: the wire ``position`` is zero-based
    ``(bank - 1) * 8 + letter`` with A=0..H=7; recalling "28C" sent 218 and
    saving to "28E" sent 220.

    A zero-padded bank is accepted (``"01A"`` and ``"1A"`` are the same slot).
    Note that :func:`position_to_slot` returns the UNPADDED form by default,
    because that is what the unit displays - so comparing its output against a
    padded string never matches. Compare linear positions instead, or ask for
    ``position_to_slot(pos, pad=True)``.
    """
    slot = slot.strip().upper()
    if len(slot) < 2 or not slot[:-1].isdigit() or slot[-1] not in "ABCDEFGH":
        raise ValueError(f"slot must look like '28C' (bank number + letter A-H): {slot!r}")
    bank = int(slot[:-1])
    if not 1 <= bank <= BANKS:
        # A setlist is exactly 256 slots, so bank 33 does not exist. Accepting it
        # silently produced a position of 256, the device ignored the save, and the
        # failure surfaced much later as a listing that never showed the preset.
        raise ValueError(
            f"bank must be 1 to {BANKS} (a setlist holds {BANKS * 8} slots): {slot!r}")
    return (bank - 1) * 8 + (ord(slot[-1]) - ord("A"))


def input_chain_rows(p: preset.BinaryPreset, from_port: int = Input.INPUT_1) -> list:
    """Return the grid rows whose input chain is on ``from_port``.

    A chain's grid row is its explicit ``row`` when set, else its index in
    ``p.chains``. A recalled preset never carries ``row``, so in practice the
    index is the row. Feed each returned row to
    :meth:`QuadCortex.set_chain_input` to re-point it, then save.

    Worked example, factory "Brit 2203": four chains, none with an explicit
    ``row``; ``chains[0]`` is on ``INPUT_1`` with 8 blocks, ``chains[2]`` holds 6
    blocks but reads ``EMPTY`` because it is fed internally by the splitter, and
    ``chains[1]`` and ``chains[3]`` are empty. So this returns ``[0]``.

    Note what that means: a row being ``EMPTY`` says nothing about whether it
    holds blocks. Use :func:`blocks` for occupancy.
    """
    rows = []
    for i, chain in enumerate(p.chains):
        if chain.HasField("in_portid") and chain.in_portid == from_port:
            rows.append(chain.row if chain.HasField("row") else i)
    return rows

BANKS = 32
SLOTS_PER_BANK = 8
SETLIST_SLOTS = BANKS * SLOTS_PER_BANK


def position_to_slot(position: int, pad: bool = False) -> str:
    """Turn a linear slot index into the slot name shown on the unit.

    The inverse of :func:`slot_to_position`: ``218 -> "28C"``. Anything reporting
    results to a person wants this, because the unit talks in slot names while the
    wire talks in indices.

    The default output is UNPADDED (``0 -> "1A"``), matching what the unit
    displays. `slot_to_position` also accepts the padded ``"01A"``, so the two are
    not symmetric: comparing this output against a padded string silently never
    matches, and the usual symptom is a listing wait that times out on a save that
    actually worked. Either pass ``pad=True`` for ``"01A"``, or - better - compare
    linear positions and avoid the question.
    """
    position = int(position)
    if not 0 <= position < SETLIST_SLOTS:
        raise ValueError(
            f"slot position must be 0 to {SETLIST_SLOTS - 1}: {position}")
    bank, letter = divmod(position, SLOTS_PER_BANK)
    return f"{bank + 1:02d}{'ABCDEFGH'[letter]}" if pad else \
           f"{bank + 1}{'ABCDEFGH'[letter]}"

