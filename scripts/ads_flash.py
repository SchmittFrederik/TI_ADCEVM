#!/usr/bin/env python3
"""
Initialize the ADS54J40 and configure its JESD204B link for LMFS = 4244.

Runs the datasheet reset sequence (Table 9-1) and then programs both
channels for LMFS = 4244: 4 lanes per device (2 per ADC), M=2, F=4, S=4,
no decimation, 40X SERDES PLL. Every written register is read back.

The write order and the "always write 1" registers follow TI's EVM GUI
file configs/evmgui/Configuration Files/ADS54J40_LMF_4244.cfg.

Active lanes: DA1, DA2, DB1, DB2 (EVM FMC DP4, DP6, DP0, DP2).
--pdn-unused-lanes tri-states DA0/DA3/DB0/DB3 (TI's file leaves them on).

Needs the ADC sample clock running (LMK DCLKout2, LVPECL on this EVM):
the JESD blocks don't accept or return register values without it.

Usage:

    ./ads_flash.py [--k 8] [--sample-msps 800] [--sysref-mhz 3.125] [--pdn-unused-lanes] [-v]
"""

import argparse
import math
import sys

from ti_adcevm.ads54j40 import ADS54J40

# ---------------- link parameters (LMFS 4244, datasheet Table 8-14) ----------------
L, M, F, S = 4, 2, 4, 4
N_PRIME = 16                # bits per sample on the link
SUBCLASS = 1                # 1 = subclass 1 (deterministic latency via SYSREF)

CHANNELS = (0, 1)

# JESD digital page (6900h)
CTRL_K = 0x80                       # 0x000 bit 7: use K from register 0x006
JESD_MODE_FILTER = 0x02             # 0x001: JESD FILTER = 000, JESD MODE = 010 (4244; 4211 is 0x04)
SUBCLASS_REG = SUBCLASS << 3        # 0x007 bit 3
DIG_ALWAYS_1 = 0x80                 # 0x016 bit 7: must write 1

# JESD analog page (6A00h)
ANA_ALWAYS_1 = 0x02                 # 0x012 bit 1: must write 1
JESD_PLL_MODE_40X = 0x02            # 0x016: 40X mode, two lanes per ADC
PLL_RESET = 0x40                    # 0x017 bit 6
LANE_PDN = 0x28                     # 0x017 bits 5 and 3 (LANE PDN 1 + 0)

# Offset read page (6100h / 0000h)
OFFSET_ALWAYS_1 = 0x02              # 0x068 bit 1: must write 1


def banner(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def link_summary(k, sample_msps, sysref_mhz):
    """Print the derived link rates and check K and SYSREF. Returns False if invalid."""

    lane_gbps = M * N_PRIME * sample_msps * 10 / 8 / L / 1000
    frame_mhz = sample_msps / S                 # Table 8-4: LMFC = (fS / 4) / K
    lmfc_mhz = frame_mhz / k
    ratio = lmfc_mhz / sysref_mhz

    print(f"  LMFS            : {L}{M}{F}{S}, N' = {N_PRIME}, subclass {SUBCLASS}")
    print(f"  Lane rate       : {lane_gbps:.3f} Gbps (ADC max 10.0)")
    print(f"  Core clock      : {lane_gbps * 1000 / 40:.3f} MHz (lane rate / 40, FPGA side)")
    print(f"  Frame clock     : {frame_mhz:.3f} MHz")
    print(f"  K               : {k} frames/multiframe (F x K = {F * k} octets)")
    print(f"  LMFC            : {lmfc_mhz:.4f} MHz")
    print(f"  SYSREF          : {sysref_mhz:.4f} MHz = LMFC / {ratio:g}")

    ok = True
    k_min = math.ceil(17 / F)
    if not k_min <= k <= 32:
        print(f"  !! K must be {k_min}..32 for F = {F} (JESD204B: 17 <= F x K <= 1024)")
        ok = False
    if lane_gbps > 10.0:
        print("  !! lane rate above the ADS54J40 maximum of 10 Gbps")
        ok = False
    n = math.log2(ratio) if ratio > 0 else -1
    if abs(n - round(n)) > 1e-9 or round(n) < 0:
        print("  !! SYSREF must be LMFC / 2^n (n = 0, 1, 2, ...), datasheet equation 1")
        ok = False
    return ok


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


def configure(adc, k, pdn_unused_lanes):
    """
    Program the JESD link on both channels, in the order of TI's
    ADS54J40_LMF_4244.cfg. Returns a list of pass/fail results.

    The "always write 1" bits are write-only (type W in the datasheet)
    and are written without readback.
    """

    results = []
    lane_pdn = LANE_PDN if pdn_unused_lanes else 0x00

    for ch in CHANNELS:
        print(f"--- Channel {ch} ---")

        results += [
            write_verify(adc, ch, "jesd_digital", 0x001, JESD_MODE_FILTER, "JESD MODE/FILTER"),
            write_verify(adc, ch, "jesd_digital", 0x007, SUBCLASS_REG, "SUBCLASS"),
            write_verify(adc, ch, "jesd_digital", 0x000, CTRL_K, "CTRL K enable"),
            write_verify(adc, ch, "jesd_digital", 0x006, k - 1, "K - 1"),
        ]
        adc.write_register("jesd_digital", 0x016, DIG_ALWAYS_1, channel=ch)
        adc.write_register("jesd_analog", 0x012, ANA_ALWAYS_1, channel=ch)

        results.append(
            write_verify(adc, ch, "jesd_analog", 0x016, JESD_PLL_MODE_40X, "JESD PLL MODE")
        )

        # Pulse PLL RESET (Table 9-1 step 4), keeping the lane power-down bits.
        adc.write_register("jesd_analog", 0x017, lane_pdn | PLL_RESET, channel=ch)
        results.append(
            write_verify(adc, ch, "jesd_analog", 0x017, lane_pdn, "PLL reset/LANE PDN")
        )

        adc.write_register("offset_read", 0x068, OFFSET_ALWAYS_1, channel=ch)

    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--k", type=int, default=8,
                        help="frames per multiframe, must match the FPGA core (default 8)")
    parser.add_argument("--sample-msps", type=float, default=800.0,
                        help="ADC sample clock from the LMK, for the rate check (default 800)")
    parser.add_argument("--sysref-mhz", type=float, default=3.125,
                        help="SYSREF frequency from the LMK, for the check (default 3.125)")
    parser.add_argument("--pdn-unused-lanes", action="store_true",
                        help="power down the unused lanes DA0/DA3/DB0/DB3")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="log SPI frames")
    args = parser.parse_args()

    banner("JESD204B link parameters")
    if not link_summary(args.k, args.sample_msps, args.sysref_mhz):
        print("\nInvalid link parameters, nothing written.", file=sys.stderr)
        return 1

    try:
        with ADS54J40(verbose=args.verbose) as adc:
            banner("ADS54J40 initialization (datasheet Table 9-1)")
            adc.initialize()

            banner(f"Configuring LMFS {L}{M}{F}{S}, K={args.k}")
            results = configure(adc, args.k, args.pdn_unused_lanes)

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
