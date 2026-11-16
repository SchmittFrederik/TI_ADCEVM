#!/usr/bin/env python3
"""
Check how reliable LMK04828 register readback is.

Reads every register of a flashed configuration file several times and
compares against the file, in up to three readback modes:

    od   3-wire, SDIO open drain   (0x149 bit 6 = 1, chip default)
    pp   3-wire, SDIO push-pull    (0x149 bit 6 = 0)
    4w   4-wire on the RESET/GPO pin (FT245 D6)

Flash the same file first (lmk_flash.py), with the LMK in 3-wire mode.
Afterwards 0x149, 0x14A and R0 are restored to the file's values.

Usage:

    ./lmk_readback_test.py configs/LMKConfig.txt [--reads 5] [--modes od pp 4w]
"""

import argparse
import sys
from collections import Counter

from ti_adcevm.lmk04828 import LMK04828

SDIO_RDBK_OPEN_DRAIN = 0x40
SKIP = LMK04828.NO_VERIFY | {0x149, 0x14A}


def run_mode(lmk, expected, reads):
    """Read every register `reads` times. Returns (bad_reads, total_reads, details)."""

    bad = 0
    details = []
    for address, value in expected.items():
        seen = Counter(lmk.read_register(address) for _ in range(reads))
        wrong = sum(n for v, n in seen.items() if v != value)
        if wrong:
            bad += wrong
            got = ", ".join(f"0x{v:02X} x{n}" for v, n in seen.most_common())
            details.append(f"    R0x{address:03X} expected 0x{value:02X}, got {got}")
    return bad, reads * len(expected), details


def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("config", help="the configuration file that is currently flashed")
    parser.add_argument("--reads", type=int, default=5, help="reads per register (default 5)")
    parser.add_argument(
        "--modes", nargs="+", choices=("od", "pp", "4w"), default=["od", "pp", "4w"],
        help="readback modes to test (default: all)",
    )
    parser.add_argument(
        "--half-period", type=float, default=LMK04828.HALF_PERIOD,
        help=f"SPI half period in s (default {LMK04828.HALF_PERIOD})",
    )
    parser.add_argument("--max-lines", type=int, default=15, help="mismatch lines shown per mode")
    args = parser.parse_args()

    registers = LMK04828.parse_config(args.config)
    file_values = dict(registers)
    expected = {a: v for a, v in file_values.items() if a not in SKIP}
    r149 = file_values.get(0x149, 0x42)
    r14a = file_values.get(0x14A, 0x02)
    r0 = file_values.get(0x000, 0x00)

    if r0 & LMK04828.SPI_3WIRE_DIS:
        print("This config leaves the LMK in 4-wire mode; test expects 3-wire.", file=sys.stderr)
        return 1

    print(f"{len(expected)} registers x {args.reads} reads per mode, "
          f"half period {args.half_period * 1e6:.0f} us\n")

    results = {}
    try:
        with LMK04828(half_period=args.half_period) as lmk:
            try:
                for mode in args.modes:
                    if mode == "od":
                        lmk.write_register(0x149, r149 | SDIO_RDBK_OPEN_DRAIN)
                        name = "3-wire open drain"
                    elif mode == "pp":
                        lmk.write_register(0x149, r149 & ~SDIO_RDBK_OPEN_DRAIN)
                        name = "3-wire push-pull"
                    else:
                        lmk.enable_4wire_readback()
                        name = "4-wire (RESET pin)"

                    bad, total, details = run_mode(lmk, expected, args.reads)
                    results[name] = (bad, total)

                    print(f"{name:20s}: {bad} of {total} reads wrong")
                    for line in details[:args.max_lines]:
                        print(line)
                    if len(details) > args.max_lines:
                        print(f"    ... {len(details) - args.max_lines} more registers")
                    print()

                    if mode == "4w":
                        lmk.enable_3wire_readback()
            finally:
                if not lmk.spi_3wire:
                    lmk.enable_3wire_readback()
                lmk.write_register(0x14A, r14a)
                lmk.write_register(0x149, r149)
                lmk.write_register(0x000, r0)

    except (ValueError, RuntimeError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print("Summary")
    print("-" * 50)
    for name, (bad, total) in results.items():
        print(f"{name:20s}: {'OK' if bad == 0 else f'{100 * bad / total:.1f} % wrong'}")

    consistent = [n for n, (b, _) in results.items() if b == 0]
    if len(consistent) < len(results) and consistent:
        print("\nAt least one mode reads everything correctly, so the registers are")
        print("written correctly and the failing modes have a readback problem.")
    elif not consistent:
        print("\nNo mode reads back the file. If all modes agree on the same wrong")
        print("values, the writes themselves are the problem.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
