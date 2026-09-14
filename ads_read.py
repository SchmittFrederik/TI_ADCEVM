#!/usr/bin/env python3

import argparse

from ads54j40 import ADS54J40


PAGES = [
    "analog_general",
    "analog_page_select",
    "analog_master",
    "analog_adc",
    "jesd_general",
    "jesd_page_select",
    "jesd_page_select1",
    "jesd_main",
    "jesd_digital",
    "jesd_analog",
    "offset_page_select",
    "offset_read",
    "offset_load",
]


def main():

    parser = argparse.ArgumentParser(
        description="Read an arbitrary ADS54J40 register."
    )

    parser.add_argument(
        "page",
        choices=PAGES,
        help="Register page to access",
    )

    parser.add_argument(
        "address",
        type=lambda x: int(x, 0),
        help="Register address, e.g. 0x020",
    )

    parser.add_argument(
        "-c",
        "--channel",
        type=int,
        choices=[0, 1],
        default=0,
        help="ADC channel (0=A, 1=B), default: 0",
    )

    args = parser.parse_args()

    with ADS54J40() as adc:

        value = adc.read_register(
            page=args.page,
            address=args.address,
            channel=args.channel,
        )

    print()
    print(f"PAGE    : {args.page}")
    print(f"ADDRESS : 0x{args.address:02X}")
    print(f"CHANNEL : {args.channel}")
    print(f"VALUE   : 0x{value:02X}  ({value})")


if __name__ == "__main__":
    main()