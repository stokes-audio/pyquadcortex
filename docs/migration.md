# Migration

What to change in your own code when a release renames or removes something.

One section per version pair, newest first. Each lists only the things that
BREAK - a new method or a new argument needs no entry here, because nothing you
already wrote stops working. The changelog is the place for what is new; this is
the place for what moved.

While the major number is 0 these sections are expected to exist. The changelog
says why: everything is verified against one unit on one firmware, protocol
facts are still being corrected at a live rate, and the API is deliberately
still moving. A rename that makes the library read correctly is worth doing
while that is true, and this file is the cost of doing it.

---

## 0.40.0 to the next release

### The protocol API moved to `pyquadcortex.protocol`

Change one import line. `pyquadcortex.connect()` now returns the DOMAIN MODEL's
`Device`; the message-level client is one namespace deeper.

| before | after |
|---|---|
| `from pyquadcortex import X` | `from pyquadcortex.protocol import X` |
| `pyquadcortex.connect()` | `pyquadcortex.protocol.connect()` |
| `pyquadcortex.proto` | `pyquadcortex.protocol.proto` |
| `pyquadcortex.client` | `pyquadcortex.protocol.client` |
| `pyquadcortex.enums` | `pyquadcortex.protocol.enums` |
| `pyquadcortex.session` | `pyquadcortex.protocol.session` |

Every name the package exported is reachable under `pyquadcortex.protocol` with
the same behaviour, apart from the one rename below. `tests/test_namespace.py`
enumerates the pre-flip export list and proves it, so this table cannot quietly
fall out of date.

`qcctl` is unchanged. If you installed the package in editable mode before the
move, reinstall it so the console script points at the new module path.

### `ExpressionBypassMode` is now `ExpressionSwitchMode`

| before | after |
|---|---|
| `ExpressionBypassMode` | `ExpressionSwitchMode` |

Same values, same numbering, same meaning - `STOP` 0, `SWITCH` 1, `HEEL_TOE` 2.
No alias: the old name described one of the three things the enum governs. It is
the unit's **SWITCH ON** control, and it applies to a block's bypass *and* to a
Lane Output Control's MUTE and SOLO. Only the bypass is a bypass.
