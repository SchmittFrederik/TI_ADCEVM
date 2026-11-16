#!/usr/bin/env python3
"""
Write one ADS54J40 register.

Examples:

    ./ads_write.py analog_master 0x59 0x20
    ./ads_write.py jesd_analog 0x16 0x02 --channel 1 --verify
"""

import argparse
import sys

from ti_adcevm.ads54j40 import ADS54J40


def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("page", choices=ADS54J40.PAGES, help="register page")
    parser.add_argument("address", type=lambda x: int(x, 0), help="register address, e.g. 0x20")
    parser.add_argument("value", type=lambda x: int(x, 0), help="8-bit value, e.g. 0x55")
    parser.add_argument(
        "-c", "--channel", type=int, choices=(0, 1), default=0,
        help="JESD channel (0 = A, 1 = B), default 0",
    )
    parser.add_argument("--verify", action="store_true", help="read the register back")
    parser.add_argument(
        "-v", "--verbose", action="count", default=0,
        help="log SPI frames (-vv: also every SDOUT bit)",
    )
    args = parser.parse_args()

    try:
        with ADS54J40(verbose=args.verbose) as adc:
            adc.write_register(args.page, args.address, args.value, channel=args.channel)
            print(
                f"ADS54J40 {args.page} 0x{args.address:03X} (CH{args.channel}) "
                f"<- 0x{args.value:02X}"
            )

            if args.verify:
                readback = adc.read_register(args.page, args.address, channel=args.channel)
                if readback != args.value:
                    print(
                        f"VERIFY FAILED: read 0x{readback:02X}, expected 0x{args.value:02X}",
                        file=sys.stderr,
                    )
                    return 1
                print(f"VERIFY OK: 0x{readback:02X}")

    except (ValueError, RuntimeError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
