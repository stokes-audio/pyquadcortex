# From the device's HID enumeration (same on macOS and Windows): the Quad Cortex
# exposes exactly one HID interface, with usage_page 0x1 / usage 0x0 (i.e. NOT a
# vendor-defined usage page).
#
# Quad Cortex Mini is the same vendor. Cortex Control's recovered schema already
# names it DeviceType.ATMA (see VersionMessage), and Mini-only settings in that
# schema (Mode.atma_page, AtmaPowerOnMode) match Neural DSP's published Mini
# features. Mini's USB product ID has not been read off hardware in this repo, so
# open_device() enumerates every Neural DSP HID control interface rather than
# requiring PRODUCT_ID. PRODUCT_ID stays as the Quad Cortex value this library
# was verified against, and as the fallback when enumerate is unavailable.
VENDOR_ID = 0x152A   # Neural DSP
PRODUCT_ID = 0x880A  # Quad Cortex
USAGE_PAGE = 0x0001  # note: NOT a vendor-defined page; the QC exposes a single HID interface
USAGE = 0x0000
INTERFACE_NUMBER = 5
