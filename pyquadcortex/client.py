"""High-level QuadCortex client for pyquadcortex.

This is the ergonomic API a caller (CLI, script) uses to control a Quad Cortex.
It builds protobuf messages and hands them to a ``transport``-like object,
which is dependency-injected via the constructor. The transport exposes:

  * ``send(message)``                  - fire-and-forget
  * ``request(message, timeout=...)``  - send and block for the correlated reply

The client deliberately knows NOTHING about hidapi, HID reports, or the framing
layer: it only speaks protobuf messages. That keeps this layer testable with a
fake transport and keeps all wire concerns in ``framing``/``transport``.

Field semantics were confirmed against real Cortex Control 4.0.1 sessions:
session connect, preset recall (user AND factory setlists), scene switch, grid
bypass/param writes, Save As, delete, and move are all reproduced verbatim from
observed traffic. ``copy_scene`` came from a different source, because Cortex
Control has no scene-copy feature to observe: its shape was read off the device's
own broadcast when a scene was copied on the unit. It too is fully confirmed on
hardware, including its ``from_index`` and ``swap`` behaviour. See
``docs/protocol.md`` for the per-operation coverage table.
"""

import time
import uuid

from pyquadcortex import catalog, registry
from pyquadcortex.enums import Input, Instrument, Output, Setlist  # noqa: F401
from pyquadcortex.proto import ProductionAutomation_pb2 as pa
from pyquadcortex.proto import Preset_pb2 as preset


class QuadCortex:
    """Ergonomic control surface over a request/response transport."""

    def __init__(self, transport, _owned_resources=None):
        self._t = transport
        # Set by pyquadcortex.connect() so close() can tear down the transport
        # and HID device it opened on the caller's behalf. When a caller wires
        # their own transport, they own its lifecycle and this stays empty.
        self._owned = _owned_resources or []
        # Populated on first use of .catalog (a ~47 KB fetch from the device).
        self._catalog = None

    # -- catalog -------------------------------------------------------------

    @property
    def catalog(self):
        """This unit's :class:`~pyquadcortex.catalog.ModelCatalog`, fetched once.

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

        Internal: :func:`pyquadcortex.connect` calls this for you, so a caller
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
        # Version READ here: the device sends its own Version READ to us, and a
        # redundant host READ would race with a caller's later version request
        # (READ replies carry no request_id to disambiguate).
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
        self, setlist_path: str, position, is_factory: bool = None,
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
        :class:`~pyquadcortex.enums.Instrument`).

        The device always reports a setlist as its full complement of 256 slots,
        most of which are typically empty. By default only occupied slots are
        returned; pass ``include_empty=True`` for the complete slot map, e.g. to
        find a free slot to save into.

        Unlike :meth:`read_preset`, this does not change what is loaded on the
        grid. There is no host-initiated "list" request: a ``File`` READ makes the
        device push a folder listing per setlist, so this sends that READ and
        waits for the listing whose key matches ``setlist``. (The device sends
        each setlist's listing more than once; the first is complete, so the
        duplicates are ignored.)

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
        is_factory: bool = None,
        request_id: int = None,
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

        Takes a :class:`~pyquadcortex.enums.Scene` (``Scene.B``); scenes are
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

        Either way the scene's LABEL travels with its bypass and parameter state:
        a copy renames the destination scene, and a swap exchanges both labels.
        """
        return self._t.send(
            pa.SceneCopyMessage(
                action=pa.MessageAction.UPDATE,
                from_index=from_index,
                to_index=to_index,
                is_swap=swap,
            )
        )

    def set_scene_label(self, scene_index: int, label: str):
        """Rename a scene. Confirmed shape: ``SceneLabel{action:
        UPDATE, index, label}`` (observed as the device's broadcast when a
        scene was renamed on the unit)."""
        return self._t.send(
            pa.SceneLabelMessage(
                action=pa.MessageAction.UPDATE, index=scene_index, label=label
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

    def set_param(self, row: int, column: int, param_index=None,
                  value: float = 0.0, scene: int = 0, param=None, model=None):
        """Set one block parameter on the grid (row/column-keyed sparse update).

        Confirmed shape: a knob change streams
        ``Grid{UPDATE, preset{chains{row, models{column, params{index,
        param_values[scene]{float_value}}}}}}``. This is the ONLY way an edit
        persists - a full-preset write is dropped (chains carry no row). Save
        the grid afterwards to keep it. ``value`` is the normalized 0..1 float
        the device expects.

        The parameter may be given as a wire ``param_index``, or by NAME via
        ``param`` together with ``model`` (the block's model id, or a
        :class:`~pyquadcortex.catalog.Model`), which resolves the index through
        the device catalog. Naming is the safer route: indices are positional
        and not every one is a visible knob - a cab's parameters are internal
        ``ir selector`` entries, so writing index 0 changes stored data and
        moves nothing on screen.
        """
        if param is not None:
            param_index = self._resolve_param_index(param, model)
        if param_index is None:
            raise TypeError("set_param needs either param_index or param=<name> with model=")
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        chain = msg.preset.chains.add()
        chain.row = row
        model_msg = chain.models.add()
        model_msg.column = column
        p = model_msg.params.add()
        p.index = param_index
        while len(p.param_values) <= scene:
            p.param_values.add()
        p.param_values[scene].float_value = value
        return self._t.send(msg)

    def _resolve_param_index(self, param, model) -> int:
        """Turn a parameter name into its wire index using the catalog."""
        if isinstance(param, int):
            return param
        if model is None:
            raise TypeError(
                "naming a parameter needs model=<model id or catalog Model> so "
                "the index can be resolved from the catalog"
            )
        resolved = model if hasattr(model, "parameter") else self.catalog[int(model)]
        return resolved.parameter(param).index

    # -- grid blocks ---------------------------------------------------------

    def set_block(self, row: int, column: int, model):
        """Put ``model`` in the grid cell at ``row``/``column``.

        Creates a block in an empty cell and replaces whatever is in an occupied
        one - the device makes no distinction. ``model`` is a model id or a
        :class:`~pyquadcortex.catalog.Model`; :mod:`pyquadcortex.models` has
        constants for the factory blocks, and :attr:`catalog` resolves anything
        installed on the unit, including purchased models and Neural Captures.

        Confirmed on hardware, and matching the device's own broadcast when a
        block is added on the unit: ``Grid{UPDATE, preset{chains{row,
        models{column, hash}}}}``. Save the grid afterwards to keep it.
        """
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        chain = msg.preset.chains.add()
        chain.row = row
        model_msg = chain.models.add()
        model_msg.column = column
        model_msg.hash = int(getattr(model, "id", model))
        return self._t.send(msg)

    def remove_block(self, row: int, column: int):
        """Remove the block at ``row``/``column``, leaving the cell empty.

        Confirmed on hardware, and matching the device's own broadcast when a
        block is deleted on the unit: ``Grid{action: DELETE, preset{chains{row,
        models{column, hash: 0}}}}``. The ACTION is what marks the removal -
        an UPDATE carrying ``hash: 0`` is transmitted but ignored by the
        firmware. Save the grid afterwards to keep it.
        """
        msg = pa.GridMessage(action=pa.MessageAction.DELETE)
        chain = msg.preset.chains.add()
        chain.row = row
        model_msg = chain.models.add()
        model_msg.column = column
        model_msg.hash = 0
        return self._t.send(msg)

    def set_bypass(self, row: int, column: int, bypassed: bool, scene: int = 0):
        """Bypass/enable one block on the grid (row/column-keyed sparse update).

        Confirmed shape: ``Grid{UPDATE, preset{bypass{row,
        colBypass{column, sceneBypass[scene]{bypass}}}}}``. Save the grid to
        persist. Per-scene bypass is indexed by ``scene`` in ``sceneBypass``.
        """
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        bp = msg.preset.bypass.add()
        bp.row = row
        cb = bp.colBypass.add()
        cb.column = column
        while len(cb.sceneBypass) <= scene:
            cb.sceneBypass.add()
        cb.sceneBypass[scene].bypass = bypassed
        return self._t.send(msg)

    def reroute_grid_input(self, p: preset.BinaryPreset, to_port: int,
                           from_port: int = None) -> list:
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

    def save_current_preset(
        self,
        setlist_path: str,
        position,
        name: str,
        instrument: int = 0,
    ):
        """Save the preset currently on the grid into a setlist slot ("Save As").

        ``position`` is either the linear slot index or the slot name shown on
        the unit (``"30A"``). Saving OVERWRITES whatever occupies that slot.

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
        """
        msg = pa.FileMessage(type=0)
        msg.folder.key = setlist_path
        msg.folder.is_factory = False
        entry = msg.folder.files.add()
        entry.index = _as_position(position)
        entry.name = name
        entry.instrument = instrument
        return self._t.request(msg)

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
        return self._t.request(msg)

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
        return self._t.request(msg)


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
    """
    slot = slot.strip().upper()
    if len(slot) < 2 or not slot[:-1].isdigit() or slot[-1] not in "ABCDEFGH":
        raise ValueError(f"slot must look like '28C' (bank number + letter A-H): {slot!r}")
    bank = int(slot[:-1])
    if bank < 1:
        raise ValueError(f"bank must be >= 1: {slot!r}")
    return (bank - 1) * 8 + (ord(slot[-1]) - ord("A"))


def input_chain_rows(p: preset.BinaryPreset, from_port: int = Input.INPUT_1) -> list:
    """Return the grid rows whose input chain is on ``from_port``.

    A chain's grid row is its explicit ``row`` when set, else its index in
    ``p.chains`` (confirmed: chains read back from a recall carry no ``row``, and
    chain index == grid row - the 28A read-back had chain[0]=Input 2 on row 1
    and chain[2]=Input 1 on row 3). Feed each returned row to
    :meth:`QuadCortex.set_chain_input` to re-point it, then save.
    """
    rows = []
    for i, chain in enumerate(p.chains):
        if chain.HasField("in_portid") and chain.in_portid == from_port:
            rows.append(chain.row if chain.HasField("row") else i)
    return rows
