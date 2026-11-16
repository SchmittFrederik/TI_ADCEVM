#!/usr/bin/env python3
"""
Find out why some ADS54J40 registers do not read back what was written.

For a few registers it writes a test value as the *first* access after a
page change, reads it back at once, then leaves the page, comes back and
reads again. It then writes a second value without a page change. This is
repeated with three driver settings (no wait / 1 ms wait / 1 ms wait plus
a throwaway read after each page change).

The test values overwrite K, the JESD PLL mode and the output swing, so
run ads_flash.py again afterwards.

Usage:

    ./ads_page_probe.py [-c CHANNEL]
"""

import argparse
import sys

from ti_adcevm.ads54j40 import ADS54J40

# (page, address, first test value, second test value)
TESTS = [
    ("jesd_digital", 0x006, 0x0A, 0x0B),    # K - 1
    ("jesd_digital", 0x001, 0x04, 0x02),    # JESD MODE
    ("jesd_analog", 0x016, 0x02, 0x00),     # JESD PLL MODE
    ("jesd_analog", 0x01B, 0x20, 0x40),     # JESD SWING
]

SETTINGS = [
    ("no wait, no dummy read", 0.0, False),
    ("1 ms wait", 1e-3, False),
    ("1 ms wait + dummy read", 1e-3, True),
]


def leave_page(adc, page):
    """Access another page so the next access to `page` is a page change."""

    other = "jesd_main" if page != "jesd_main" else "jesd_digital"
    adc.read_register("analog_master", 0x059)
    adc.read_register(other, ADS54J40.PAGES[other].addresses.start)


def run(adc, channel):
    rows = []
    for page, address, first, second in TESTS:
        leave_page(adc, page)
        adc.write_register(page, address, first, channel=channel)     # first access in page
        immediate = adc.read_register(page, address, channel=channel)
        leave_page(adc, page)
        after_hop = adc.read_register(page, address, channel=channel)
        adc.write_register(page, address, second, channel=channel)    # same page, no change
        same_page = adc.read_register(page, address, channel=channel)
        rows.append((page, address, first, immediate, after_hop, second, same_page))
    return rows


def verdict(first, immediate, after_hop, second, same_page):
    if immediate == first and same_page == second:
        return "OK"
    if immediate != first and after_hop == first:
        # The misdirected write and the misdirected read after the hop hit the
        # same (previous) page, which looks like a stale read.
        return "1st access after page change hits previous page"
    if immediate != first and same_page == second:
        return "1st access after page change lost"
    if same_page != second and immediate != first:
        return "never reads back (write-only or no clock?)"
    return "inconsistent"


def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("-c", "--channel", type=int, choices=(0, 1), default=0)
    args = parser.parse_args()

    try:
        with ADS54J40() as adc:
            for name, settle, dummy in SETTINGS:
                adc.PAGE_SETTLE = settle
                adc.page_dummy_read = dummy
                adc._forget_pages()

                print(f"\n=== {name} (CH{args.channel}) ===")
                print(f"{'register':22s} {'1st write':>9} {'read':>5} {'after hop':>9} "
                      f"{'2nd write':>9} {'read':>5}  verdict")
                for page, address, first, immediate, after_hop, second, same_page in run(adc, args.channel):
                    print(
                        f"{page:12s} 0x{address:03X}       0x{first:02X}  0x{immediate:02X}      "
                        f"0x{after_hop:02X}      0x{second:02X}  0x{same_page:02X}  "
                        f"{verdict(first, immediate, after_hop, second, same_page)}"
                    )
    except (ValueError, RuntimeError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print("\nRegisters were overwritten with test values: run ads_flash.py again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
