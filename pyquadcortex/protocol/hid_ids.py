# From the device's HID enumeration (same on macOS and Windows): the Quad Cortex
# exposes exactly one HID interface, with usage_page 0x1 / usage 0x0 (i.e. NOT a
# vendor-defined usage page).
VENDOR_ID = 0x152A   # Neural DSP
PRODUCT_ID = 0x880A  # Quad Cortex
USAGE_PAGE = 0x0001  # note: NOT a vendor-defined page; the QC exposes a single HID interface
USAGE = 0x0000
INTERFACE_NUMBER = 5
