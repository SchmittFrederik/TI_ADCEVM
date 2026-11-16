#!/usr/bin/env python3
"""
Initialize the ADS54J40 and configure its JESD204B link.

Runs the datasheet reset sequence (Table 9-1) and then programs both
channels for LMFS = 4211 (two lanes per ADC, 40X SERDES PLL, no
decimation). Every written register is read back.

Usage:

    ./ads_flash.py [-v]
"""

import argparse
import sys

from ti_adcevm.ads54j40 import ADS54J40

# ---------------- target configuration ----------------
SAMPLE_MSPS = 800           # ADC sampling rate (informational only)
LMFS = "4211"               # L=4 lanes/device (2 per ADC), M=2, F=1, S=1
K_FRAMES = 8                # frames per multiframe, register holds K-1
SUBCLASS = 1                # 1 = subclass 1 (deterministic latency via SYSREF)
PDN_UNUSED_LANES = True     # tri-state DA0/DA3/DB0/DB3, unused with LMFS=4211

CHANNELS = (0, 1)

# JESD digital page (6900h)
CTRL_K = 0x80                       # 0x000 bit 7: use K from register 0x006
K_REG = K_FRAMES - 1                # 0x006
JESD_MODE_FILTER = 0x02             # 0x001: JESD FILTER = 000, JESD MODE = 010
SUBCLASS_REG = SUBCLASS << 3        # 0x007 bit 3

# JESD analog page (6A00h)
JESD_PLL_MODE_40X = 0x02            # 0x016: 40X mode, two lanes per ADC
PLL_RESET = 0x40                    # 0x017 bit 6
LANE_PDN = 0x28                     # 0x017 bits 5 and 3 (LANE PDN 1 + 0)


def banner(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def write_verify(adc, channel, page, address, value, description):
    """Write a register, read it back, print the result and return True on match."""

    adc.write_register(page, address, value, channel=channel)
    readback = adc.read_register(page, address, channel=channel)
    ok = readback == value

    print(
        f"  CH{channel} {page:12s} 0x{address:03X} ({description:20s}) "
        f"write=0x{value:02X} read=0x{readback:02X} {'[PASS]' if ok else '[FAIL]'}"
    )
    return ok


def configure(adc):
    """Program the JESD link on both channels. Returns a list of pass/fail results."""

    results = []
    lane_pdn = LANE_PDN if PDN_UNUSED_LANES else 0x00

    for ch in CHANNELS:
        print(f"--- Channel {ch} ---")

        results += [
            write_verify(adc, ch, "jesd_digital", 0x000, CTRL_K, "CTRL K enable"),
            write_verify(adc, ch, "jesd_digital", 0x006, K_REG, "frames/multiframe K"),
            write_verify(adc, ch, "jesd_digital", 0x001, JESD_MODE_FILTER, "JESD MODE/FILTER"),
            write_verify(adc, ch, "jesd_digital", 0x007, SUBCLASS_REG, "SUBCLASS"),
            write_verify(adc, ch, "jesd_analog", 0x016, JESD_PLL_MODE_40X, "JESD PLL MODE"),
        ]

        # Pulse PLL RESET (Table 9-1 step 4), keeping the lane power-down bits.
        adc.write_register("jesd_analog", 0x017, lane_pdn | PLL_RESET, channel=ch)
        results.append(
            write_verify(adc, ch, "jesd_analog", 0x017, lane_pdn, "PLL reset/LANE PDN")
        )

    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("-v", "--verbose", action="count", default=0, help="log SPI frames")
    args = parser.parse_args()

    try:
        with ADS54J40(verbose=args.verbose) as adc:
            banner("ADS54J40 initialization (datasheet Table 9-1)")
            adc.initialize()

            banner(
                f"JESD204B link: {SAMPLE_MSPS} MSPS, LMFS={LMFS}, "
                f"K={K_FRAMES}, subclass {SUBCLASS}"
            )
            results = configure(adc)

    except (ValueError, RuntimeError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    banner("RESULT")
    passed = sum(results)
    print(f"  {passed}/{len(results)} checks passed")

    if passed != len(results):
        print("  SOME CHECKS FAILED")
        return 1

    print("  ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
