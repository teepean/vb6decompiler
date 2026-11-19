#!/usr/bin/env python3
"""Hex dump of a variable table or record descriptor from an executable.

usage: tabledump.py EXE 0xVA [BYTES]
Sixteen bytes a line, by virtual address, for reading what records.table_entries makes of it.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from vb6dec.pe import Image  # noqa: E402


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip())
        return 2
    im = Image(argv[0])
    va, n = int(argv[1], 0), int(argv[2], 0) if len(argv) > 2 else 256
    data = im.bytes(va, n)
    for i in range(0, len(data), 16):
        row = data[i:i + 16]
        print("%08x  %s" % (va + i, " ".join("%02x%02x" % (row[j], row[j + 1]) for j in range(0, len(row) - 1, 2))))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
