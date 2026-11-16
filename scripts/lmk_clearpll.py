#!/usr/bin/env python3
"""
Clear the LMK04828 PLL lock-lost flags and report whether they stay clear.

Usage:

    ./lmk_clearpll.py [--mode 4wire] [--wait 1.0]
"""

import argparse
import sys
import time

from ti_adcevm.lmk04828 import LMK04828


def print_pll_status(label, s):
    def yes_no(flag):
        return "YES" if flag else "NO"

    print(f"{label}:")
    print(f"  PLL1: Locked = {yes_no(s['pll1_locked']):3s} | Lock lost = {yes_no(s['pll1_lock_lost'])}")
    print(f"  PLL2: Locked = {yes_no(s['pll2_locked']):3s} | Lock lost = {yes_no(s['pll2_lock_lost'])}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument(
        "--mode", choices=("3wire", "4wire"), default="3wire",
        help="readback mode the LMK is currently in (default 3wire)",
    )
    parser.add_argument(
        "--wait", type=float, default=1.0,
        help="seconds to wait after clearing before re-reading (default 1.0)",
    )
    args = parser.parse_args()

    try:
        with LMK04828(initial_mode=args.mode) as lmk:
            print("LMK04828 PLL Status")
            print("=" * 40)

            print_pll_status("Before clearing", lmk.read_status())
            lmk.clear_pll_lock_lost()
            time.sleep(args.wait)
            post = lmk.read_status()
            print()
            print_pll_status("After clearing", post)

    except (ValueError, RuntimeError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print()
    if not (post["pll1_locked"] and post["pll2_locked"]):
        print("ERROR: At least one PLL is not locked.")
        return 1
    if post["pll1_lock_lost"] or post["pll2_lock_lost"]:
        print("WARNING: At least one lock-lost flag remains set.")
        return 1

    print("SUCCESS: Both PLLs are locked and their lock-lost flags are clear.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
