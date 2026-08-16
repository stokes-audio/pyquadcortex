"""The model's own errors.

Small on purpose. The model raises the protocol layer's ``DeviceLostError``
rather than inventing a second name for the device going away, and it prevents
what it can with types instead of exceptions - a factory preset has no
``save()`` at all, a row that cannot branch has no ``splitter``
(``docs/domain-model.md`` section 8). What is left is the handful of refusals
that mirror something the unit itself cannot do.
"""


class InactiveSceneError(RuntimeError):
    """A write was attempted through a grid bound to a scene that is not active.

    The unit has no way to write to a scene it is not in: you switch to it
    first. Doing that silently would change what comes out of the outputs and
    LEAVE it changed, which is far more than the caller asked for - so the model
    refuses and names :meth:`~pyquadcortex.device.preset.Scene.activate` as the
    step to take. Reading through such a grid is fine
    (``docs/domain-model.md`` section 10).
    """
