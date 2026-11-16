#!/usr/bin/env python3
"""
Write one LMK04828 register.

Examples:

    ./lmk_write.py 0x002 0x01
    ./lmk_write.py 0x002 0x01 --verify

Writes are the same in 3-wire and 4-wire mode; --mode only matters for
--verify. Writing R0 changes the readback mode, which the driver follows.
"""

import argparse
import sys

from ti_adcevm.lmk04828 import LMK04828


def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("address", type=lambda x: int(x, 0), help="register address, e.g. 0x002")
    parser.add_argument("value", type=lambda x: int(x, 0), help="register value, e.g. 0x01")
    parser.add_argument(
        "--mode", choices=("3wire", "4wire"), default="3wire",
        help="readback mode the LMK is currently in (default 3wire)",
    )
    parser.add_argument("--verify", action="store_true", help="read the register back")
    args = parser.parse_args()

    try:
        with LMK04828(initial_mode=args.mode) as lmk:
            lmk.write_register(args.address, args.value)
            print(f"LMK04828 register 0x{args.address:03X} <- 0x{args.value:02X}")

            if args.verify:
                readback = lmk.read_register(args.address)
                if readback != args.value:
                    print(
                        f"VERIFY FAILED: read 0x{readback:02X}, expected 0x{args.value:02X}",
                        file=sys.stderr,
                    )
                    return 1
                print(f"VERIFY OK: 0x{readback:02X} ({lmk.spi_mode} readback)")

    except (ValueError, RuntimeError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
