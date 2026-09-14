#!/usr/bin/env python3
"""
Write an LMK04828 register.

Examples:

    ./writeregister.py 0x002 0x01
    ./writeregister.py 0x002 0x01 --verify

Register writes are identical in 3-wire and 4-wire mode.
"""

import argparse
import sys

from lmk04828 import LMK04828


def main():
    parser = argparse.ArgumentParser(
        description="Write an LMK04828 register."
    )

    parser.add_argument(
        "address",
        help="Register address, e.g. 0x002",
    )

    parser.add_argument(
        "value",
        help="Register value, e.g. 0x01",
    )

    parser.add_argument(
        "--verify",
        action="store_true",
        help="Read the register back after writing.",
    )

    args = parser.parse_args()

    try:
        address = int(args.address, 0)
        value = int(args.value, 0)

        with LMK04828() as lmk:

            lmk.write_register(address, value)

            print(
                f"LMK04828 register "
                f"0x{address:03X} <- 0x{value:02X}"
            )

            if args.verify:

                readback = lmk.read_register(address)

                if readback != value:
                    print(
                        f"VERIFY FAILED: "
                        f"read 0x{readback:02X}, "
                        f"expected 0x{value:02X}",
                        file=sys.stderr,
                    )
                    return 1

                print(
                    f"VERIFY OK: "
                    f"0x{readback:02X} "
                    f"({lmk.spi_mode} readback)"
                )

    except (ValueError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())