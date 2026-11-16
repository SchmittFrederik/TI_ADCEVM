#!/usr/bin/env python3
"""
Read one ADS54J40 register.

Examples:

    ./ads_read.py analog_master 0x59
    ./ads_read.py jesd_digital 0x01 --channel 1
"""

import argparse
import sys

from ti_adcevm.ads54j40 import ADS54J40


def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("page", choices=ADS54J40.PAGES, help="register page")
    parser.add_argument("address", type=lambda x: int(x, 0), help="register address, e.g. 0x20")
    parser.add_argument(
        "-c", "--channel", type=int, choices=(0, 1), default=0,
        help="JESD channel (0 = A, 1 = B), default 0",
    )
    parser.add_argument(
        "-v", "--verbose", action="count", default=0,
        help="log SPI frames (-vv: also every SDOUT bit)",
    )
    args = parser.parse_args()

    try:
        with ADS54J40(verbose=args.verbose) as adc:
            value = adc.read_register(args.page, args.address, channel=args.channel)
    except (ValueError, RuntimeError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(
        f"ADS54J40 {args.page} 0x{args.address:03X} (CH{args.channel}) = "
        f"0x{value:02X} (0b{value:08b})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
