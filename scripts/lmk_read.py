#!/usr/bin/env python3
"""
Read one LMK04828 register.

Examples:

    ./lmk_read.py 0x003
    ./lmk_read.py 0x003 --mode 4wire

--mode does not configure the LMK; it tells the driver which readback
path the LMK is already using.
"""

import argparse
import sys

from ti_adcevm.lmk04828 import LMK04828


def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("address", type=lambda x: int(x, 0), help="register address, e.g. 0x003")
    parser.add_argument(
        "--mode", choices=("3wire", "4wire"), default="3wire",
        help="readback mode the LMK is currently in (default 3wire)",
    )
    args = parser.parse_args()

    try:
        with LMK04828(initial_mode=args.mode) as lmk:
            value = lmk.read_register(args.address)
            print(
                f"LMK04828 register 0x{args.address:03X} = 0x{value:02X} "
                f"(0b{value:08b}, {lmk.spi_mode} readback)"
            )

    except (ValueError, RuntimeError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
