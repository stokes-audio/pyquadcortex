# Troubleshooting

## `DeviceNotFoundError` when it was working a moment ago

If a session was running fine and then the device vanishes mid-run, the usual advice
in that error message does not apply - Cortex Control is quit, the cable is in, and
the unit has booted. What can happen instead is that **the unit's USB link dies and
only a full power-down recovers it**.

This is field experience from one unit, not a protocol finding, and the root cause is
unknown. Recorded because the symptoms are misleading and cost a user about an hour.

**What it looks like:** `hid.enumerate()` reports zero Neural DSP interfaces and
`connect()` raises. Reseating the cable at either end changes nothing, and retrying in
software never succeeds (25 attempts over 75 seconds, never visible once).

**How to tell it apart from a plain disconnection:** the port is *flapping* -
asserting and dropping a connection several times a second - rather than idle. On
macOS:

```bash
log show --last 60s --predicate 'eventMessage CONTAINS "cableChangeOccurred"' \
    --style compact | grep -c cableChangeOccurred
```

Hundreds of events a minute with nobody touching the cable means the connection is
being made and lost repeatedly, so enumeration never completes. Roughly 264 events
were seen while attached, against about 1 per minute with the cable out - which is
also a clean way to exonerate the host: if it is quiet with nothing plugged in, the
Mac's port and USB stack are fine.

**What fixed it:** a **full shutdown** of the unit, then power on. A reboot was *not*
enough, and unplugging at the unit end does not reset its USB controller either.

**Then wait about three minutes before re-diagnosing.** After a restart the link flaps
for a while as it settles, and that looks identical to the fault: in one measurement
the unit was still flapping a minute later with zero interfaces, then enumerated on
its own two and a half minutes after the restart with no intervention. Sampling during
that window twice led a user to wrongly conclude the power cycle had failed.

**What is not established:** the cause. One unit, one host, and only ever the cable
that shipped with it, so a marginal cable is not ruled out. Onset followed roughly 20
minutes of continuous heavy write traffic, though whether that is connected is
unknown.

