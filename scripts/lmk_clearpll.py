#!/usr/bin/env python3

from ti_adcevm.lmk04828 import LMK04828
from time import sleep

def check_pll(lmk):
    r182 = lmk.read_register(0x182)
    r183 = lmk.read_register(0x183)

    # PLL status
    pll1_locked = bool(r182 & 0x02)
    pll1_lost   = bool(r182 & 0x04)

    pll2_locked = bool(r183 & 0x02)
    pll2_lost   = bool(r183 & 0x04)

    return pll1_locked, pll1_lost, pll2_locked, pll2_lost


def clear_pll(lmk):
    # Clear PLL1 lock-lost flag
    lmk.write_register(0x182, 0x01)
    lmk.write_register(0x182, 0x00)

    # Clear PLL2 lock-lost flag
    lmk.write_register(0x183, 0x01)
    lmk.write_register(0x183, 0x00)


def print_pll_status(label, status):
    pll1_locked, pll1_lost, pll2_locked, pll2_lost = status

    def state(value):
        return "YES" if value else "NO"

    print(f"{label}:")
    print(f"  PLL1: Locked = {state(pll1_locked):3s} | Lock lost = {state(pll1_lost)}")
    print(f"  PLL2: Locked = {state(pll2_locked):3s} | Lock lost = {state(pll2_lost)}")


def main():
    lmk = LMK04828()

    print("LMK04828 PLL Status")
    print("=" * 40)

    # Read initial status
    pre = check_pll(lmk)
    print_pll_status("Before clearing", pre)

    # Clear lock-lost flags
    clear_pll(lmk)

    # pause 1s 
    sleep(1)

    # Read status again
    post = check_pll(lmk)
    print()
    print_pll_status("After clearing", post)

    # Evaluate result
    print()
    if not post[0] or not post[2]:
        print("ERROR: At least one PLL is not locked.")
    elif post[1] or post[3]:
        print("WARNING: At least one lock-lost flag remains set.")
    else:
        print("SUCCESS: Both PLLs are locked and their lock-lost flags are clear.")


if __name__ == "__main__":
    main()