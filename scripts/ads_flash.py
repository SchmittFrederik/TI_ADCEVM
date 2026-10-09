#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ads54j40 import ADS54J40

# ---------------- target config (adjust as needed) ----------------
SAMPLE_MSPS = 800          # ADC sampling rate
LMFS        = "8224"       # L=4/dev (2/ADC), M=2, F=1, S=1
K_FRAMES    = 8           # frames per multiframe  ->  written as K-1 = 0x0F
SUBCLASS    = 1            # 1 = subclass 1 (SYSREF sync)
PDN_UNUSED_LANES = True    # tri-state DA0/DA3/DB0/DB3 in LMFS=4211

K_REG     = K_FRAMES - 1
SUBC_REG  = SUBCLASS << 3
# LMFS=4211 (from Table 8-17): JESD_MODE=010, JESD_FILTER=000 --> 0x02
JESD_MAIN_DIG_REG   = 0x02
JESD_PLL_40X_REG    = 0x02

CHANNELS = (0, 1)

def banner(t):
    print("\n" + "=" * 70); print(t); print("=" * 70)

results = []

def set_verify(ch, page, addr, desc, value):
    adc.write_register(page, addr, value, channel=ch)
    rb = adc.read_register(page, addr, channel=ch)
    ok = (rb == value)
    results.append(ok)
    status = "[PASS]" if ok else "[FAIL]"
    print(f"  CH{ch} {page} 0x{addr:03X} ({desc:28s}) write=0x{value:02X} read=0x{rb:02X} {status}")
    return ok


with ADS54J40(verbose=False) as adc:

    banner(f"Configuring JESD204B link: {SAMPLE_MSPS} MSa, LMFS={LMFS}, K={K_FRAMES}, subclass {SUBCLASS}")
    print(f"  (K reg = {K_REG:#04x}, SUBCLASS reg = {SUBC_REG:#04x})\n")

    for ch in CHANNELS:
        print(f"--- Channel {ch} ---")

        # 1. CTRL K = 1 (bit7 of 0x000) so 0x006 K value takes effect
        set_verify(ch, "jesd_digital", 0x000, "CTRL K enable",       0x80)
        # 2. K value (frames per multiframe)
        set_verify(ch, "jesd_digital", 0x006, "frames/multiframe K", K_REG)
        # 3. JESD MODE/FILTER -> 40X, 2 lanes
        set_verify(ch, "jesd_digital", 0x001, "JESD MODE/FILTER",    JESD_MAIN_DIG_REG)
        # 4. Subclass
        set_verify(ch, "jesd_digital", 0x007, "SUBCLASS",            SUBC_REG)
        # 5. JESD PLL MODE 40X
        set_verify(ch, "jesd_analog",  0x016, "JESD PLL MODE",       JESD_PLL_40X_REG)

    # 6. PLL reset pulse (Table 9-1 step 4), per channel
    print("\n--- PLL reset pulse ---")
    for ch in CHANNELS:
        adc.write_register("jesd_analog", 0x017, 0x40, channel=ch)
        adc.write_register("jesd_analog", 0x017, 0x00, channel=ch)
        print(f"  CH{ch} PLL reset pulsed (0x017: 0x40 -> 0x00)")

    # 7. Optional: power down unused lanes (LANE PDN 1+0 = 0x28) for LMFS=4211
    if PDN_UNUSED_LANES:
        print("\n--- Lane power-down (unused lanes DA0/DA3/DB0/DB3) ---")
        for ch in CHANNELS:
            adc.write_register("jesd_analog", 0x017, 0x28, channel=ch)
            rb = adc.read_register("jesd_analog", 0x017, channel=ch)
            ok = (rb == 0x28)
            results.append(ok)
            print(f"  CH{ch} LANE PDN 0x017 = 0x{rb:02X} {'[PASS]' if ok else '[FAIL]'}")

    # ---------------- final full read-back dump ----------------
    banner("Final read-back verification")
    for ch in CHANNELS:
        print(f"--- Channel {ch} ---")
        for page, addr, desc in [
            ("jesd_digital", 0x000, "CTRL K"),
            ("jesd_digital", 0x006, "K"),
            ("jesd_digital", 0x001, "JESD MODE/FILTER"),
            ("jesd_digital", 0x007, "SUBCLASS"),
            ("jesd_analog",  0x016, "PLL MODE"),
            ("jesd_analog",  0x017, "LANE PDN/PLL rst"),
        ]:
            v = adc.read_register(page, addr, channel=ch)
            print(f"  CH{ch} {page} 0x{addr:03X} = 0x{v:02X}")

    banner("RESULT")
    passed = sum(results)
    print(f"  {passed}/{len(results)} checks passed")
    print("   ALL CHECKS PASSED" if passed == len(results) else "   SOME CHECKS FAILED")