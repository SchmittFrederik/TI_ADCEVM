#!/usr/bin/env python3
"""
Flash a TICS Pro LMK04828 register configuration.

Usage:

    ./flash_lmk.py LMKConfig800.txt

Optional verification:

    ./flash_lmk.py LMKConfig800.txt --verify

The script keeps one FTDI connection open for the complete
configuration and automatically follows the LMK 3-wire/4-wire
transition caused by R0[4].
"""

import argparse
import sys

from lmk04828 import LMK04828


def main():
    parser = argparse.ArgumentParser(
        description="Flash a TICS Pro LMK04828 configuration."
    )

    parser.add_argument(
        "config",
        help="TICS Pro configuration file",
    )

    parser.add_argument(
        "--verify",
        action="store_true",
        help="Read every register back after writing.",
    )

    args = parser.parse_args()

    try:
        with LMK04828() as lmk:

            print(f"Configuration: {args.config}")
            print(f"Initial SPI mode: {lmk.spi_mode}")
            print()

            lmk.load_config(
                args.config,
                verify=args.verify,
            )

            print()
            print(f"Final SPI mode: {lmk.spi_mode}")

    except (ValueError, RuntimeError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())