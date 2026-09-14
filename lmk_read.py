#!/usr/bin/env python3
"""
Read an LMK04828 register.

Examples:

    ./readregister.py 0x003
    ./readregister.py 0x003 --mode 3wire
    ./readregister.py 0x003 --mode 4wire

--mode does not configure the LMK.
It tells the driver which readback path the LMK is already using.
"""

import argparse
import sys

from lmk04828 import LMK04828


def main():
    parser = argparse.ArgumentParser(
        description="Read an LMK04828 register."
    )

    parser.add_argument(
        "address",
        help="Register address, e.g. 0x003",
    )

    parser.add_argument(
        "--mode",
        choices=("3wire", "4wire"),
        default="3wire",
        help=(
            "LMK SPI readback mode "
            "(default: 3wire). "
            "Does not configure the LMK."
        ),
    )

    args = parser.parse_args()

    try:
        address = int(args.address, 0)

        with LMK04828(initial_mode=args.mode) as lmk:

            value = lmk.read_register(address)

            print(
                f"LMK04828 register 0x{address:03X} = "
                f"0x{value:02X} "
                f"({lmk.spi_mode} readback)"
            )

    except (ValueError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())