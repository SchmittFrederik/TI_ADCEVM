#!/usr/bin/env python3
"""
Show the LMK04828 lock/reference status.

The overall result checks for the expected EVM setup: both PLLs locked,
CLKin1 selected as reference and present, not in holdover.

Usage:

    ./lmk_status.py [--mode 4wire]
"""

import argparse
import sys

from ti_adcevm.lmk04828 import LMK04828

EXPECTED_REFERENCE = "CLKin1"


def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument(
        "--mode", choices=("3wire", "4wire"), default="3wire",
        help="readback mode the LMK is currently in (default 3wire)",
    )
    args = parser.parse_args()

    try:
        with LMK04828(initial_mode=args.mode) as lmk:
            s = lmk.read_status()
    except (ValueError, RuntimeError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    def locked(flag):
        return "LOCKED ✓" if flag else "UNLOCKED ✗"

    def warn(flag):
        return "YES !" if flag else "NO"

    print()
    print("LMK04828 STATUS")
    print("-" * 42)
    print(f"PLL1    {locked(s['pll1_locked']):<12}Lost lock: {warn(s['pll1_lock_lost'])}")
    print(f"PLL2    {locked(s['pll2_locked']):<12}Lost lock: {warn(s['pll2_lock_lost'])}")
    print(f"REF     {s['reference'] or 'None':<12}CLKin1 LOS: {warn(s['clkin1_los'])}")
    print(f"HOLD    {'ACTIVE !' if s['holdover'] else 'Inactive ✓'}")
    print(f"DAC     {s['dac']:4d}  (0x{s['dac']:03X})")
    print("-" * 42)
    print("  ".join(f"R{a:03X}=0x{v:02X}" for a, v in s["raw"].items()))

    ok = (
        s["pll1_locked"]
        and s["pll2_locked"]
        and s["reference"] == EXPECTED_REFERENCE
        and not s["clkin1_los"]
        and not s["holdover"]
    )
    print(f"RESULT: {'OK ✓' if ok else 'CHECK FAILED ✗'}")
    print()

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
