"""The preset on the grid, and its eight scenes.

Everything here reads through the state layer, so a value is what the unit is
doing now rather than what it was doing when the object was built. Reading a
property never changes what comes out of the outputs; the one thing here that
does - :meth:`Scene.activate` - is a method, deliberately, because principle 4
says nothing audible may be a side effect of a read.
"""

from pyquadcortex import protocol
from pyquadcortex.device import translate
from pyquadcortex.device.grid import BlockGrid, Rows


class Scene:
    """One of a preset's eight scenes, as the unit labels them: A to H."""

    def __init__(self, preset: "Preset", letter: translate.SceneLetter):
        self._preset = preset
        self._letter = letter

    @property
    def letter(self) -> translate.SceneLetter:
        return self._letter

    @property
    def name(self) -> str:
        """This scene's label, as Gig View's EDIT SCENE shows it.

        Empty when the scene has no label. The unit stores a single space for
        that rather than an empty string, and shows the letter on screen
        instead, so ``if scene.name:`` means what a caller expects.
        """
        return translate.scene_name(self._preset.wire, self._letter)

    @property
    def blocks(self) -> BlockGrid:
        """The grid as THIS scene sees it.

        Fixed-bound: it goes on answering about this scene whatever the unit
        switches to. Contrast ``preset.blocks``, which follows the active scene.
        """
        return BlockGrid(self._preset, scene=self._letter)

    @property
    def is_active(self) -> bool:
        """Whether the unit is on this scene right now."""
        return self._preset.active_scene == self._letter

    def activate(self):
        """Switch the unit to this scene.

        **Audible.** This is what comes out of the outputs changing, which is
        why it is a method rather than something a property does on your behalf
        (design principle 4, the same rule that makes recalling explicit).

        The model's copy is updated before the unit's echo arrives, because
        waiting for it would make every write pay for information we almost
        always already have. A matching echo then changes nothing, which is one
        code path rather than two (``docs/domain-model.md`` section 9, rule 3).
        If the write never reaches the unit, the active scene is marked for
        re-reading and the exception is passed on.

        Returns:
            The write's watch. Ignoring it is fine and normal - the outcomes are
            logged either way - but a caller who wants to know can wait on it.
        """
        # The check `is_current` describes. Without it, a Scene reached through
        # a Preset somebody held across a recall would switch the scene of
        # whatever is loaded NOW - audibly, and on a preset the caller never
        # opened.
        self._preset._check_current()
        index = translate.scene_to_wire(self._letter)
        state = self._preset.state
        client = self._preset.client
        return state.write_through(
            "scene", {"selected_scene": index},
            send=lambda: client.switch_scene(index))

    def __eq__(self, other):
        if not isinstance(other, Scene):
            return NotImplemented
        return (self._letter, id(self._preset)) == (other._letter,
                                                    id(other._preset))

    def __hash__(self):
        return hash((self._letter, id(self._preset)))

    def __repr__(self) -> str:
        return f"<Scene {self._letter}>"


class Scenes:
    """A preset's eight scenes. ``preset.scenes["B"]``, ``scenes.active``."""

    def __init__(self, preset: "Preset"):
        self._preset = preset

    @property
    def active(self) -> Scene:
        """The scene the unit is on right now."""
        return Scene(self._preset, self._preset.active_scene)

    def __getitem__(self, letter) -> Scene:
        """One scene, by the letter the unit labels it with.

        A bare number is refused. Scene B is wire index 1, so a number here
        reads as either one, and the model never takes an index where the
        screen shows a letter.
        """
        wire = translate.scene_to_wire(letter)
        return Scene(self._preset, translate.scene_from_wire(wire))

    def __iter__(self):
        return (Scene(self._preset, letter) for letter in translate.SceneLetter)

    def __len__(self) -> int:
        return len(translate.SceneLetter)

    def __repr__(self) -> str:
        return f"<Scenes A to H, {len(self)} of them>"


class Preset:
    """The preset on the grid.

    Reached as ``device.preset``, which always hands back the current one. Hold
    one across a recall and it reports :attr:`is_current` False rather than
    quietly describing the preset that used to be loaded.
    """

    def __init__(self, device, loaded):
        """Internal. Use ``device.preset``.

        Args:
            device: the `Device` this reads through.
            loaded: which slot was loaded when this was built, as the ``loaded``
                entry reports it. What :attr:`is_current` compares against.
        """
        self._device = device
        self._loaded = dict(loaded)

    # -- what the grid and the scenes read through ----------------------------

    @property
    def state(self):
        """The state layer. Raises once the `Device` is closed."""
        return self._device.state

    @property
    def client(self):
        """The protocol connection. Raises once the `Device` is closed."""
        return self._device.client

    def _check_current(self) -> None:
        """Refuse to answer once this is not the preset on the grid.

        A `Preset` reads live state, so without this it would go on answering
        after the unit loaded something else - and answering with the NEW
        preset's contents, which is worse than answering with the old ones. That
        is the failure the whole layer exists to avoid, and it is the same one
        `Device._check_open` refuses for a closed connection.

        :attr:`is_current` and ``__repr__`` deliberately do not call this: asking
        whether an object is still good must not raise, and neither must a
        debugger.
        """
        if self.is_current:
            return
        raise RuntimeError(
            "this Preset is no longer the one on the grid - the unit has "
            "loaded another since it was read, so nothing it could report "
            "would be about the preset you opened. Ask the device for the "
            "current one with device.preset, or check preset.is_current first.")

    @property
    def wire(self):
        """The preset payload the unit sent, read through the cache.

        Fetched on every access, so an edit somebody made on the touchscreen is
        picked up rather than remembered wrongly. The cache answers from its
        copy when it has one and asks the unit when it does not.

        **This is the cache's own object, not a copy, and mutating it corrupts
        the model.** It is the seam the grid and the blocks read through rather
        than something a caller is meant to hold - the way down to the wire is
        ``device.client``, which says the same thing about itself. It is not
        copied on the way out because the grid memoizes its handles against this
        object's identity, and a fresh copy per access would defeat that and
        make every block property re-derive the whole grid.
        """
        self._check_current()
        return self.state.value("preset", "preset")

    @property
    def catalog(self):
        """The unit's own catalogue of virtual devices.

        Fetched once per connection and cached by the protocol layer; the first
        access costs a transfer of about 47 KB.
        """
        return self.client.catalog

    @property
    def active_scene(self) -> translate.SceneLetter:
        """Which scene the unit is on, as a letter.

        Refuses once this is no longer the loaded preset, for the same reason
        `has_unsaved_changes` does: the unit's answer would be about a preset
        this object is not.
        """
        self._check_current()
        return translate.scene_from_wire(
            self.state.value("scene", "selected_scene"))

    # -- what the preset reports ----------------------------------------------

    @property
    def name(self) -> str:
        """The preset's name, as the Directory shows it."""
        wire = self.wire
        if not protocol.field_present(wire, "name"):
            raise RuntimeError(
                "the unit's answer for this preset carried no name, so there "
                "is none to report. Nothing was cached for it, so asking again "
                "can still succeed.")
        return wire.name

    @property
    def has_unsaved_changes(self) -> bool:
        """Whether the grid holds edits nobody has saved.

        The italic name on screen. Answered from the model's copy, which the
        unit keeps current by pushing this in the connect burst and whenever the
        flag CHANGES - so it costs no round trip once the connection is warm.
        (Not on every edit: an edit to an already-dirty preset sends nothing,
        measured 2026-08-14. That is why this is a cached fact rather than
        something counted from edit notifications.)

        A recall clears this, and the unit says nothing about it when it does
        (measured), so the model re-reads after a recall rather than waiting to
        be told.

        Refuses once this is no longer the loaded preset: the flag the unit
        holds is about whatever is on the grid now, which by then is something
        else.
        """
        self._check_current()
        return self.state.value("dirty", "is_dirty")

    @property
    def is_current(self) -> bool:
        """Whether this is still the preset on the grid.

        Hold a `Preset` while somebody taps a different slot on the touchscreen
        and this object is about something the unit is no longer showing. Every
        other property here checks this first and refuses, and so does
        `Scene.activate` - so neither a read nor a write can quietly land on a
        preset the caller never opened (``docs/domain-model.md`` section 12).

        This property is the one that does NOT refuse, because asking whether an
        object is still good must not raise.

        Costs no round trip: it compares the loaded slot the unit last reported
        against the one this object was built at, both from the model's copy. An
        EDIT does not make a preset stale - it is the same preset, and only the
        model's copy of its contents is behind.
        """
        return self.state.cached("loaded") == self._loaded

    @property
    def scenes(self) -> Scenes:
        """This preset's eight scenes."""
        return Scenes(self)

    @property
    def rows(self) -> Rows:
        """The grid's four rows, live-bound to the active scene."""
        return Rows(self.blocks)

    @property
    def blocks(self) -> BlockGrid:
        """The grid, live-bound to whichever scene is active.

        Reads and writes through the active scene, like the touchscreen itself.
        Use ``scene.blocks`` to read one particular scene.
        """
        return BlockGrid(self)

    def __repr__(self) -> str:
        # Never triggers a device read: repr() is called by debuggers and
        # logging, and a model that reports itself wrongly is the one thing this
        # library cannot do. So it names the slot it was built at rather than
        # the preset's name, which would have to be fetched.
        where = self._loaded.get("position")
        at = "an unknown slot" if where is None else f"slot {where}"
        return f"<Preset loaded from {at}>"
