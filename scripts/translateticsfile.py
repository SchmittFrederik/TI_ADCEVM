#!/usr/bin/env python3
"""
Annotate a TICS Pro LMK04828 register export with decoded address/value.

    R0 (INIT)   0x000090   ->   R0 (INIT)   0x000090  -> R0x000 => 0x90 = 0b10010000

Usage:

    ./translateticsfile.py configs/LMKConfig.txt annotated.txt
"""

import argparse
import re

# "R<n> [(text)] 0x<24-bit word>", word = address << 8 | value
TICS_LINE = re.compile(r"^\s*R\d+(?:\s*\([^)]*\))?\s+(0x[0-9A-Fa-f]+)")


def annotate_file(input_file, output_file):
    with open(input_file, encoding="utf-8") as infile, \
         open(output_file, "w", encoding="utf-8") as outfile:

        for line in infile:
            match = TICS_LINE.match(line)

            if match is None:
                # Keep headers, blank lines, comments, etc. unchanged.
                outfile.write(line)
                continue

            word = int(match.group(1), 16)
            address, value = word >> 8, word & 0xFF

            outfile.write(
                f"{line.rstrip()}  -> R0x{address:03X} => 0x{value:02X} = 0b{value:08b}\n"
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("input", help="TICS Pro register export")
    parser.add_argument("output", help="annotated output file")
    args = parser.parse_args()

    annotate_file(args.input, args.output)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
