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
        description="Write an ADS54J40 register."
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
        "value",
        type=lambda x: int(x, 0),
        help="8-bit register value, e.g. 0x55",
    )

    parser.add_argument(
        "-c",
        "--channel",
        type=int,
        choices=[0, 1],
        default=0,
        help="ADC channel (0=A, 1=B), default: 0",
    )

    parser.add_argument(
        "--verify",
        action="store_true",
        help="Read the register back after writing",
    )

    args = parser.parse_args()

    with ADS54J40() as adc:

        adc.write_register(
            page=args.page,
            address=args.address,
            value=args.value,
            channel=args.channel,
        )

        if args.verify:

            value = adc.read_register(
                page=args.page,
                address=args.address,
                channel=args.channel,
            )

            print()
            print(f"READBACK : 0x{value:02X}")

            if value != args.value:
                print(
                    f"WARNING: expected 0x{args.value:02X}, "
                    f"got 0x{value:02X}"
                )


if __name__ == "__main__":
    main()