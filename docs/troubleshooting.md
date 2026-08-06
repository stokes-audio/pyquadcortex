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



## The rig is silent (or clicking) and every read-back looks perfect

See **"Settings only your ears can verify"** in [api.md](api.md). The short version: any
host write to the Tuner subsystem engages an invisible tuner state - combined with the
tuner's mute preference, the outputs go silent with no on-screen cause, and only opening
and closing the tuner on the unit releases it. A faint metronome click means the transport
(tempo parameter 4) is running - 1.0 is RUNNING, and the volume control's floor is -60 dB,
not silence. None of this is visible to a read: the values read back exactly as written.


## How long a reboot or cold boot actually takes

Measured on d14e: a reboot is ~39 s not enumerated, then ~9 s enumerated-but-silent, then
a ~2 s handshake - about 55 s total, and `connect()` rides through the silent window on
its own (`handshake_patience`). A cold boot showed ~11.7 s of silent-but-openable. If a
unit is unreachable for MINUTES, that is not a boot - see the USB-link-death section.
