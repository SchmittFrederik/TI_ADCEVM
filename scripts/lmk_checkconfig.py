#!/usr/bin/env python3

"""
Decode an LMK04828 register export and print the resulting clock frequencies.

Usage:

    ./lmk_checkconfig.py configs/LMKConfig.txt [--oscin 122.88] [--clkin1 10]
"""

import argparse
from pathlib import Path

from ti_adcevm.lmk04828 import LMK04828


# ============================================================
# Default input frequencies
# ============================================================

OSCIN_MHZ = 122.88
CLKIN1_MHZ = 10.0
CLKIN0_MHZ = None


# ============================================================
# TICS Pro register parser
# ============================================================

def parse_tics_file(filename):
    """Return {address: value} from a register export (last write wins)."""

    return dict(LMK04828.parse_config(filename))


def reg(registers, address):
    if address not in registers:
        raise ValueError(
            f"Missing register 0x{address:03X}"
        )

    return registers[address]


# ============================================================
# PLL2
# ============================================================

def calculate_pll2(registers, oscin_mhz):

    r160 = reg(registers, 0x160)
    r161 = reg(registers, 0x161)
    r162 = reg(registers, 0x162)

    r166 = reg(registers, 0x166)
    r167 = reg(registers, 0x167)
    r168 = reg(registers, 0x168)

    # PLL2 R divider

    pll2_r = (
        ((r160 & 0x0F) << 8)
        | r161
    )

    if pll2_r == 0:
        raise ValueError("Invalid PLL2 R divider (zero).")

    # PLL2 P prescaler

    p_code = (r162 >> 5) & 0x07

    p_values = {
        0: 8,
        1: 2,
        2: 2,
        3: 3,
        4: 4,
        5: 5,
        6: 6,
        7: 7,
    }

    pll2_p = p_values[p_code]

    # PLL2 N divider

    pll2_n = (
        ((r166 & 0x03) << 16)
        | (r167 << 8)
        | r168
    )

    if pll2_n == 0:
        raise ValueError("Invalid PLL2 N divider (zero).")

    # Reference doubler

    doubler = r162 & 0x01

    # OSCin frequency range setting

    oscin_code = (r162 >> 2) & 0x07

    oscin_ranges = {
        0: "0-63 MHz",
        1: ">63-127 MHz",
        2: ">127-255 MHz",
        4: ">255-500 MHz",
    }

    # PLL2 phase detector frequency

    pdf_mhz = (
        oscin_mhz
        * (2 if doubler else 1)
        / pll2_r
    )

    # Internal PLL2 VCO frequency

    vco_mhz = pdf_mhz * pll2_p * pll2_n

    # VCO selection

    r138 = reg(registers, 0x138)

    vco_mux = (r138 >> 5) & 0x03

    vco_names = {
        0: "VCO0",
        1: "VCO1",
        2: "External VCO (CLKin1)",
        3: "Reserved",
    }

    vco_ranges = {
        0: (2370, 2630),
        1: (2920, 3080),
    }

    if vco_mux in vco_ranges:
        vco_min, vco_max = vco_ranges[vco_mux]
        vco_valid = vco_min <= vco_mhz <= vco_max
    else:
        vco_valid = None

    # Distribution clock source

    # Internal VCO drives the distribution path; for an external VCO
    # (CLKin1) main() substitutes the CLKin1 frequency.
    distribution_mhz = vco_mhz if vco_mux in (0, 1) else None

    return {
        "R": pll2_r,
        "P": pll2_p,
        "N": pll2_n,
        "doubler": bool(doubler),
        "pdf_mhz": pdf_mhz,
        "oscin_code": oscin_code,
        "oscin_range": oscin_ranges.get(
            oscin_code, "Reserved"
        ),
        "vco_mux": vco_mux,
        "vco_name": vco_names[vco_mux],
        "vco_mhz": vco_mhz,
        "vco_valid": vco_valid,
        "distribution_mhz": distribution_mhz,
    }


# ============================================================
# SYSREF
# ============================================================

def calculate_sysref(registers, distribution_mhz, clkin0_mhz):

    r139 = reg(registers, 0x139)
    r13a = reg(registers, 0x13A)
    r13b = reg(registers, 0x13B)
    r140 = reg(registers, 0x140)

    divider = (
        ((r13a & 0x1F) << 8)
        | r13b
    )

    clkin0_direct = bool(r139 & 0x04)
    sysref_mux = r139 & 0x03

    modes = {
        0: "Normal SYNC",
        1: "Re-clocked",
        2: "SYSREF pulser",
        3: "Continuous SYSREF",
    }

    global_pd = bool(r140 & 0x08)
    sysref_pd = bool(r140 & 0x04)
    ddly_pd = bool(r140 & 0x02)
    pulser_pd = bool(r140 & 0x01)

    if clkin0_direct:

        source_name = "Direct CLKin0"
        divider_frequency = clkin0_mhz
        output_frequency = clkin0_mhz
        operation = "Direct CLKin0 (divider bypassed)"

    else:

        source_name = "VCO / SYSREF divider"

        if distribution_mhz is not None and divider >= 8:
            divider_frequency = distribution_mhz / divider
        else:
            divider_frequency = None

        if sysref_mux == 0:
            output_frequency = None
            operation = "Normal SYNC (not periodic)"

        elif sysref_mux == 1:
            output_frequency = divider_frequency
            operation = "Re-clocked SYSREF"

        elif sysref_mux == 2:
            output_frequency = divider_frequency
            operation = "Triggered SYSREF pulses"

        else:
            output_frequency = divider_frequency
            operation = "Continuous SYSREF"

    if global_pd:
        output_frequency = None

    if sysref_pd and not clkin0_direct:
        output_frequency = None

    if sysref_mux == 2 and pulser_pd and not clkin0_direct:
        output_frequency = None

    return {
        "divider": divider,
        "source": source_name,
        "source_mhz": (
            clkin0_mhz if clkin0_direct else distribution_mhz
        ),
        "divider_frequency": divider_frequency,
        "frequency": output_frequency,
        "mux": sysref_mux,
        "mode": modes[sysref_mux],
        "operation": operation,
        "global_pd": global_pd,
        "sysref_pd": sysref_pd,
        "ddly_pd": ddly_pd,
        "pulser_pd": pulser_pd,
    }

# ============================================================
# Clock output decoding
# ============================================================

OUTPUT_FORMATS = {
    0: "Powerdown",
    1: "LVDS",
    2: "HSDS 6mA",
    3: "HSDS 8mA",
    4: "HSDS 10mA",
    5: "LVPECL 1600mV",
    6: "LVPECL 2000mV",
    7: "LCPECL",
}


DCLK_MUXES = {
    0: "Divider",
    1: "Divider + DCC",
    2: "Bypass",
    3: "Analog delay + divider",
}


def get_output_registers(registers, output):

    # Output pair:
    # DCLKout0 / SDCLKout1 -> 0x100
    # DCLKout2 / SDCLKout3 -> 0x108
    # DCLKout4 / SDCLKout5 -> 0x110
    # ...
    #
    # Only even outputs are DCLK outputs.

    even_output = output if output % 2 == 0 else output - 1

    base = 0x100 + (even_output // 2) * 8

    return {
        "base": base,
        "div": reg(registers, base),
        "mux": reg(registers, base + 3),
        "sdclk_mux": reg(registers, base + 4),
        "power": reg(registers, base + 6),
        "format": reg(registers, base + 7),
    }


def calculate_dclk(registers, output, distribution_mhz):

    r = get_output_registers(registers, output)

    divider_code = r["div"] & 0x1F

    # Divider code 0 represents divide-by-32.

    divider = (
        32 if divider_code == 0
        else divider_code
    )

    mux = r["mux"] & 0x03

    # DCLK output format

    fmt = r["format"] & 0x07

    # Group power-down

    group_pd = bool(r["power"] & 0x08)

    # Frequency

    if mux == 2:
        frequency = distribution_mhz

    elif distribution_mhz is None:
        frequency = None

    else:
        frequency = distribution_mhz / divider

    # Divider output, also fed to SDCLKoutY when SDCLKoutY_MUX = 0.
    device_clock = frequency

    powered_down = (
        group_pd
        or fmt == 0
        or frequency is None
    )

    if powered_down:
        frequency = None

    warnings = []

    if mux == 0 and divider == 1:
        warnings.append(
            "DIV=1 invalid with DCLKoutX_MUX=0"
        )

    return {
        "frequency": frequency,
        "device_clock": device_clock,
        "divider": divider,
        "divider_code": divider_code,
        "mux": mux,
        "mux_name": DCLK_MUXES[mux],
        "format": OUTPUT_FORMATS[fmt],
        "group_pd": group_pd,
        "powered_down": powered_down,
        "warnings": warnings,
        "registers": r,
    }


def calculate_sdclk(registers, output, distribution_mhz, sysref):

    r = get_output_registers(registers, output)

    # SDCLKoutY_MUX is bit 5 of base + 4.

    mux = (r["sdclk_mux"] >> 5) & 0x01

    # SDCLKoutY_PD is bit 0 of base + 6.

    sdclk_pd = bool(r["power"] & 0x01)

    # Output format bits [6:4].

    fmt = (r["format"] >> 4) & 0x07

    group_pd = bool(r["power"] & 0x08)

    if mux == 0:
        # Same divided clock as the paired DCLKoutX output.
        source = "Device clock (= DCLKout)"
        frequency = calculate_dclk(registers, output - 1, distribution_mhz)["device_clock"]

    else:
        source = "SYSREF"

        frequency = sysref["frequency"]

    powered_down = (
        group_pd
        or sdclk_pd
        or fmt == 0
        or frequency is None
    )

    if powered_down:
        frequency = None

    return {
        "frequency": frequency,
        "mux": mux,
        "source": source,
        "format": OUTPUT_FORMATS[fmt],
        "group_pd": group_pd,
        "sdclk_pd": sdclk_pd,
        "powered_down": powered_down,
        "registers": r,
    }


# ============================================================
# Formatting
# ============================================================

def format_frequency(frequency):

    if frequency is None:
        return "N/A"

    if frequency >= 1000:
        return f"{frequency / 1000:.6f} GHz"

    if frequency >= 1:
        return f"{frequency:.6f} MHz"

    return f"{frequency * 1000:.6f} kHz"


def print_output(name, result, description=""):

    status = (
        "POWERED DOWN"
        if result["powered_down"]
        else "ACTIVE"
    )

    print(
        f"{name:<16}"
        f"{format_frequency(result['frequency']):<19}"
        f"{result.get('source', result.get('mux_name')):<27}"
        f"{result['format']:<20}"
        f"{status}"
        f"{description}"
    )

    for warning in result.get("warnings", []):
        print(f"    WARNING: {warning}")


# ============================================================
# Main
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description="LMK04828 TICS Pro clock calculator"
    )

    parser.add_argument(
        "config",
        type=Path,
        help="TICS Pro register export"
    )

    parser.add_argument(
        "--oscin",
        type=float,
        default=OSCIN_MHZ,
        help="OSCin frequency in MHz"
    )

    parser.add_argument(
        "--clkin1",
        type=float,
        default=CLKIN1_MHZ,
        help="CLKin1 frequency in MHz"
    )

    parser.add_argument(
        "--clkin0",
        type=float,
        default=CLKIN0_MHZ,
        help="CLKin0 frequency in MHz (optional)"
    )

    args = parser.parse_args()

    registers = parse_tics_file(args.config)

    print("\n" + "=" * 90)
    print("LMK04828 CLOCK CONFIGURATION")
    print("=" * 90)

    print(f"\nConfiguration : {args.config}")
    print(f"OSCin         : {args.oscin:.6f} MHz")
    print(f"CLKin1        : {args.clkin1:.6f} MHz")
    print(
        f"CLKin0        : "
        f"{format_frequency(args.clkin0)}"
    )

    # --------------------------------------------------------
    # PLL2
    # --------------------------------------------------------

    pll = calculate_pll2(registers, args.oscin)

    print("\n--- PLL2 ---")

    print("Reference       : OSCin")
    print(f"PLL2 R          : {pll['R']}")
    print(f"PLL2 P          : {pll['P']}")
    print(f"PLL2 N          : {pll['N']}")
    print(
        f"Reference doubler: "
        f"{'Enabled' if pll['doubler'] else 'Disabled'}"
    )

    print(f"PLL2 PDF        : {pll['pdf_mhz']:.6f} MHz")
    print(f"OSCin range code: {pll['oscin_range']}")
    print(f"Selected source : {pll['vco_name']}")

    print(f"Calculated VCO  : {pll['vco_mhz']:.6f} MHz")

    if pll["vco_valid"] is not None:
        print(
            f"VCO range check : "
            f"{'PASS' if pll['vco_valid'] else 'WARNING - OUT OF RANGE'}"
        )

    # Select actual distribution source.

    if pll["vco_mux"] == 2:
        distribution_mhz = args.clkin1
        print(
            f"Distribution   : External VCO / CLKin1 "
            f"({distribution_mhz:.6f} MHz)"
        )

    elif pll["vco_mux"] in (0, 1):
        distribution_mhz = pll["vco_mhz"]
        print(
            f"Distribution   : Internal {pll['vco_name']} "
            f"({distribution_mhz:.6f} MHz)"
        )

    else:
        distribution_mhz = None
        print("Distribution   : INVALID VCO_MUX")

    # Check OSCin_FREQ configuration.

    oscin_limits = {0: (0, 63), 1: (63, 127), 2: (127, 255), 4: (255, 500)}
    low, high = oscin_limits.get(pll["oscin_code"], (None, None))

    if low is None or not low < args.oscin <= high:
        print(
            f"WARNING: OSCin_FREQ setting ({pll['oscin_range']}) "
            f"does not match OSCin = {args.oscin} MHz."
        )

    # --------------------------------------------------------
    # SYSREF
    # --------------------------------------------------------

    sysref = calculate_sysref(
        registers,
        distribution_mhz,
        args.clkin0
    )

    print("\n--- SYSREF ---")

    print(f"SYSREF divider : {sysref['divider']}")
    print(f"SYSREF source  : {sysref['source']}")
    print(f"SYSREF mode    : {sysref['mode']}")
    print(f"Operation      : {sysref['operation']}")

    print(
        f"Divider rate   : "
        f"{format_frequency(sysref['divider_frequency'])}"
    )

    print(
        f"Output rate    : "
        f"{format_frequency(sysref['frequency'])}"
    )

    print(
        f"SYSREF global PD: "
        f"{sysref['global_pd']}"
    )

    print(
        f"SYSREF block PD: "
        f"{sysref['sysref_pd']}"
    )

    print(
        f"SYSREF pulser PD: "
        f"{sysref['pulser_pd']}"
    )

    # --------------------------------------------------------
    # Clock outputs
    # --------------------------------------------------------

    print("\n--- CLOCK OUTPUTS ---\n")

    print(
        f"{'Output':<16}"
        f"{'Frequency':<19}"
        f"{'Source':<27}"
        f"{'Format':<20}"
        f"Status"
    )

    print("-" * 105)

    # DCLKout0

    result = calculate_dclk(
        registers, 0, distribution_mhz
    )

    print_output("DCLKout0", result, "\tFPGA-JESD")

    # SDCLKout1

    result = calculate_sdclk(
        registers, 1, distribution_mhz, sysref
    )

    print_output("SDCLKout1", result, "\tFPGA-SysRef")

    # DCLKout2

    result = calculate_dclk(
        registers, 2, distribution_mhz
    )

    print_output("DCLKout2", result, "\tSampleClk")

    # SDCLKout3

    result = calculate_sdclk(
        registers, 3, distribution_mhz, sysref
    )

    print_output("SDCLKout3", result, "\tADC-SysRef")

    # DCLKout12

    result = calculate_dclk(
        registers, 12, distribution_mhz
    )

    print_output("DCLKout12", result, "\tFMCLao")

    print("\n" + "=" * 90)


if __name__ == "__main__":
    main()