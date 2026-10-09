#!/usr/bin/env python3

import argparse
import re


TICS_PATTERN = re.compile(
    r"^(\s*R\d+(?:\s*\([^)]*\))?\s+)(0x[0-9A-Fa-f]+)"
)


def annotate_file(input_file, output_file):

    with open(input_file, "r", encoding="utf-8") as infile, \
         open(output_file, "w", encoding="utf-8") as outfile:

        for line in infile:

            match = TICS_PATTERN.match(line)

            if match is None:
                # Keep headers, blank lines, comments, etc.
                outfile.write(line)
                continue

            prefix = match.group(1)
            word_string = match.group(2)

            word = int(word_string, 16)

            # TICS Pro encoding:
            # bits 23:8 = register address
            # bits 7:0  = register value
            address = (word >> 8) & 0x1FFF
            value = word & 0xFF

            # Preserve the original spacing/text before the hex word
            original = line.rstrip()

            annotated = (
                f"{original}"
                f"  -> R0x{address:03X} => 0x{value:02X}"
                f" = 0b{value:08b}\n"
            )

            outfile.write(annotated)


def main():

    parser = argparse.ArgumentParser(
        description="Annotate TICS Pro LMK04828 configuration files."
    )

    parser.add_argument("input")
    parser.add_argument("output")

    args = parser.parse_args()

    annotate_file(args.input, args.output)

    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()