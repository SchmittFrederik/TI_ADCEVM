#!/usr/bin/env python3
"""
Write a TICS Pro (or EVM GUI) LMK04828 register configuration.

Usage:

    ./lmk_flash.py configs/LMKConfig.txt
    ./lmk_flash.py configs/LMKConfig.txt --verify

One FTDI connection is kept open for the whole file, and the driver
follows the 3-wire/4-wire switch caused by writes to R0. With --verify,
all registers are read back after the last write.
"""

import argparse
import sys

from ti_adcevm.lmk04828 import LMK04828


def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("config", help="register configuration file")
    parser.add_argument(
        "--mode", choices=("3wire", "4wire"), default="3wire",
        help="readback mode the LMK is currently in (default 3wire)",
    )
    parser.add_argument("--verify", action="store_true", help="read every register back")
    args = parser.parse_args()

    try:
        with LMK04828(initial_mode=args.mode) as lmk:
            print(f"Configuration: {args.config}")
            print(f"Initial SPI mode: {lmk.spi_mode}")
            print()

            lmk.load_config(args.config, verify=args.verify)

            print()
            print(f"Final SPI mode: {lmk.spi_mode}")

    except (ValueError, RuntimeError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
