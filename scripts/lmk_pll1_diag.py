#!/usr/bin/env python3
"""
Watch PLL1 of the LMK04828 to find out why it does not lock.

Prints the live PLL1 settings, then logs PLL1 lock detect and the CPout1
tuning voltage (VCXO control voltage) over time. The tuning voltage is
read through the holdover DAC in tracking mode (TRACK_EN = 1); its update
rate is temporarily raised to ~100 Hz and restored afterwards.

Usage:

    ./lmk_pll1_diag.py [--ref-mhz 10] [--oscin-mhz 122.88] [--seconds 60]
"""

import argparse
import sys
import time

from ti_adcevm.lmk04828 import LMK04828

VCC = 3.3
DAC_MAX = 1023
DAC_CLK_MULT = {0: 4, 1: 64, 2: 1024, 3: 16384}
WINDOW_NS = {0: 4, 1: 9, 2: 19, 3: 43}


def read_pll1_config(lmk):
    r = {a: lmk.read_register(a) for a in
         (0x146, 0x147, 0x14B, 0x14E, 0x14F, 0x155, 0x156, 0x159, 0x15A, 0x15B, 0x15C, 0x15D)}
    return {
        "clkin1_en": bool(r[0x146] & 0x10),
        "clkin1_type": "MOS" if r[0x146] & 0x02 else "bipolar",
        "clkin_sel_mode": (r[0x147] >> 4) & 0x07,
        "los_en": bool(r[0x14B] & 0x20),
        "track_en": bool(r[0x14B] & 0x10),
        "clkin1_r": ((r[0x155] & 0x3F) << 8) | r[0x156],
        "pll1_n": ((r[0x159] & 0x3F) << 8) | r[0x15A],
        "window": (r[0x15B] >> 6) & 0x03,
        "cp_tri": bool(r[0x15B] & 0x20),
        "cp_pol": "positive" if r[0x15B] & 0x10 else "negative",
        "cp_ua": 50 + 100 * (r[0x15B] & 0x0F),
        "dld_cnt": ((r[0x15C] & 0x3F) << 8) | r[0x15D],
        "dac_clk_mult": DAC_CLK_MULT[(r[0x14E] >> 6) & 0x03],
        "dac_clk_cntr": r[0x14F],
        "raw_14e": r[0x14E],
        "raw_14f": r[0x14F],
    }


def print_config(c, ref_mhz, oscin_mhz):
    pdf_ref = ref_mhz * 1e3 / c["clkin1_r"] if c["clkin1_r"] else float("nan")
    pdf_vcxo = oscin_mhz * 1e3 / c["pll1_n"] if c["pll1_n"] else float("nan")
    dac_period = c["dac_clk_mult"] * max(c["dac_clk_cntr"], 1) / (pdf_ref * 1e3)

    print("PLL1 configuration (read from chip)")
    print("-" * 60)
    print(f"CLKin1 enabled      : {c['clkin1_en']}   type: {c['clkin1_type']}")
    print(f"CLKin_SEL_MODE      : {c['clkin_sel_mode']}   (1 = CLKin1 manual)")
    print(f"CLKin1_R / PLL1_N   : {c['clkin1_r']} / {c['pll1_n']}")
    print(f"PDF from reference  : {pdf_ref:.4f} kHz  ({ref_mhz} MHz / {c['clkin1_r']})")
    print(f"PDF from VCXO       : {pdf_vcxo:.4f} kHz  ({oscin_mhz} MHz / {c['pll1_n']})")
    if abs(pdf_ref - pdf_vcxo) > 1e-6 * pdf_ref:
        print("  !! the two PDFs differ: R/N do not match the given frequencies")
    print(f"Charge pump         : {c['cp_ua']} uA, {c['cp_pol']} polarity, "
          f"{'TRI-STATE !!' if c['cp_tri'] else 'active'}")
    print(f"Lock detect         : window {WINDOW_NS[c['window']]} ns, "
          f"{c['dld_cnt']} cycles ({c['dld_cnt'] / pdf_ref:.1f} ms)")
    print(f"LOS detection       : {'enabled' if c['los_en'] else 'DISABLED (CLKin1 LOS flag is meaningless)'}")
    print(f"DAC tracking        : {'on' if c['track_en'] else 'OFF'}, "
          f"configured update every {dac_period:.2f} s")
    print()


def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--ref-mhz", type=float, default=10.0, help="CLKin1 frequency (default 10)")
    parser.add_argument("--oscin-mhz", type=float, default=122.88, help="VCXO frequency (default 122.88)")
    parser.add_argument("--seconds", type=float, default=60.0, help="how long to log (default 60)")
    parser.add_argument("--interval", type=float, default=0.5, help="log interval in s (default 0.5)")
    parser.add_argument(
        "--mode", choices=("3wire", "4wire"), default="3wire",
        help="readback mode the LMK is currently in (default 3wire)",
    )
    args = parser.parse_args()

    try:
        with LMK04828(initial_mode=args.mode) as lmk:
            config = read_pll1_config(lmk)
            print_config(config, args.ref_mhz, args.oscin_mhz)

            if not config["track_en"]:
                print("TRACK_EN is off, the DAC does not follow CPout1. Tuning voltage unavailable.")

            # DAC update rate = PDF / (4 * 200) = 100 Hz at 80 kHz PDF (must stay <= 100 kHz).
            lmk.write_register(0x14E, config["raw_14e"] & 0x3F)    # DAC_CLK_MULT = 4
            lmk.write_register(0x14F, 200)                         # DAC_CLK_CNTR = 200
            lmk.clear_pll_lock_lost()
            try:
                samples = log(lmk, args.seconds, args.interval)
            finally:
                lmk.write_register(0x14E, config["raw_14e"])
                lmk.write_register(0x14F, config["raw_14f"])

    except (ValueError, RuntimeError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted, DAC settings restored.")
        return 1

    interpret(samples)
    return 0


def log(lmk, seconds, interval):
    print(f"{'t [s]':>6}  {'PLL1 LD':8} {'LD lost':8} {'DAC':>5}  {'~Vtune':>7}")
    samples = []
    start = time.monotonic()

    while True:
        t = time.monotonic() - start
        s = lmk.read_status()
        vtune = s["dac"] / DAC_MAX * VCC
        samples.append((t, s["pll1_locked"], s["pll1_lock_lost"], s["dac"]))
        print(
            f"{t:6.1f}  {'LOCKED' if s['pll1_locked'] else '-':8} "
            f"{'YES' if s['pll1_lock_lost'] else '-':8} {s['dac']:5d}  {vtune:6.2f}V"
        )
        if t >= seconds:
            return samples
        time.sleep(interval)


def interpret(samples):
    dacs = [d for _, _, _, d in samples]
    locked_now = samples[-1][1]
    ever_locked = any(l for _, l, _, _ in samples)
    lost = any(x for _, _, x, _ in samples)
    last = dacs[-1]
    late = dacs[len(dacs) // 2:]
    span_late = max(late) - min(late)

    print()
    print("Interpretation")
    print("-" * 60)
    print(f"DAC range over run: {min(dacs)}..{max(dacs)}, last {last} "
          f"(~{last / DAC_MAX * VCC:.2f} V of {VCC} V)")

    if len(set(dacs)) == 1 and last in (0, 512):
        print("DAC never moved: tracking does not seem to run, tuning voltage unknown.")
    elif last <= 20 or last >= DAC_MAX - 20:
        print("Tuning voltage is at a rail. The loop pushes the VCXO as far as it can")
        print("and still cannot match the reference. Typical causes:")
        print("  - the reference does not reach the phase detector cleanly")
        print("    (level, slew rate, termination on the CLKin1 SMA input)")
        print("  - reference frequency outside the VCXO pull range")
        print("  - wrong PLL1_CP_POL for this VCXO")
    elif not locked_now and span_late > 50:
        print("Tuning voltage is still moving: the loop has not settled.")
        print("Either it is very slow (try a longer --seconds), or it is cycle")
        print("slipping because the frequency error is large or the loop is unstable.")
    elif not ever_locked:
        print("Tuning voltage is stable mid-range but lock detect never asserted.")
        print("The loop is probably locked; suspect the lock detector settings or")
        print("excessive reference jitter (slow slew rate on CLKin1).")
    elif lost or not locked_now:
        print("PLL1 locked but dropped lock at least once: intermittent phase errors")
        print("larger than the lock detect window, typically reference signal quality.")
    else:
        print("PLL1 locked and stayed locked during the run.")


if __name__ == "__main__":
    sys.exit(main())
