#!/usr/bin/env python3

from ti_adcevm.lmk04828 import LMK04828

def check_status(lmk):
    # Read status registers
    r182 = lmk.read_register(0x182)
    r183 = lmk.read_register(0x183)
    r184 = lmk.read_register(0x184)
    r185 = lmk.read_register(0x185)
    r188 = lmk.read_register(0x188)

    # PLL status
    pll1_locked = bool(r182 & 0x02)
    pll1_lost = bool(r182 & 0x04)

    pll2_locked = bool(r183 & 0x02)
    pll2_lost = bool(r183 & 0x04)

    # Reference selection: bits 5, 4, 3 of R184
    clkin2 = bool(r184 & 0x20)
    clkin1 = bool(r184 & 0x10)
    clkin0 = bool(r184 & 0x08)

    # Input loss-of-signal: bits 1, 0 of R184
    clkin1_los = bool(r184 & 0x02)
    clkin0_los = bool(r184 & 0x01)

    # Holdover: bit 4 of R188
    holdover = bool(r188 & 0x10)

    # PLL1 DAC: R184[7:6] and R185[7:0]
    dac = ((r184 & 0xC0) << 2) | r185

    # Reference selection display
    if clkin2:
        reference = "CLKin2"
    elif clkin1:
        reference = "CLKin1"
    elif clkin0:
        reference = "CLKin0"
    else:
        reference = "None"

    # Compact terminal output
    print()
    print("LMK04828 STATUS")
    print("-" * 42)

    print(
        f"PLL1    {'LOCKED ✓' if pll1_locked else 'UNLOCKED ✗':<12}"
        f"Lost lock: {'YES !' if pll1_lost else 'NO'}"
    )

    print(
        f"PLL2    {'LOCKED ✓' if pll2_locked else 'UNLOCKED ✗':<12}"
        f"Lost lock: {'YES !' if pll2_lost else 'NO'}"
    )

    print(
        f"REF     {reference:<12}"
        f"CLKin1 LOS: {'YES !' if clkin1_los else 'NO'}"
    )

    print(
        f"HOLD    {'ACTIVE !' if holdover else 'Inactive ✓'}"
    )

    print(f"DAC     {dac:4d}  (0x{dac:03X})")

    print("-" * 42)

    print(
        f"R182=0x{r182:02X}  "
        f"R183=0x{r183:02X}  "
        f"R184=0x{r184:02X}  "
        f"R185=0x{r185:02X}  "
        f"R188=0x{r188:02X}"
    )

    # Overall result for your expected configuration
    checks = [
        pll1_locked,
        pll2_locked,
        clkin1,
        not clkin1_los,
        not holdover,
    ]

    ok = all(checks)

    print(f"RESULT: {'OK ✓' if ok else 'CHECK FAILED ✗'}")
    print()

    return ok


if __name__ == "__main__":
    # Your driver defaults to 3-wire mode unless specified otherwise.
    # Use 4-wire mode if the LMK was configured for 4-wire readback.
    with LMK04828(initial_mode="3wire") as lmk:
        check_status(lmk)